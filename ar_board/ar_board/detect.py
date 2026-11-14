"""Детекция ArUco-маркеров планшета и оценка позы (гомография + PnP)."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from . import spec
from .layout import marker_corners_px, px_per_mm


@dataclass
class BoardPose:
    """Результат оценки положения планшета в кадре."""

    homography: np.ndarray                 # 3x3: пиксели планшета -> пиксели кадра
    marker_ids: List[int]
    reprojection_error: float              # средняя ошибка углов маркеров, px
    coverage: float                        # доля площади кадра, занятая маркерами
    is_valid: bool
    frame_size: Tuple[int, int]            # (width, height)
    marker_corners: Dict[int, np.ndarray] = field(default_factory=dict)
    board_corners: Optional[np.ndarray] = None
    rvec: Optional[np.ndarray] = None
    tvec: Optional[np.ndarray] = None
    pnp_error: Optional[float] = None
    method: str = "homography"
    identity_marker_id: Optional[int] = None
    board_id: Optional[str] = None
    guard_marker_id: Optional[int] = None
    guard_marker_visible: Optional[bool] = None

    @property
    def ids(self) -> List[int]:
        """Псевдоним marker_ids для удобства вызовов."""
        return list(self.marker_ids)


@lru_cache(maxsize=8)
def _detector(dict_id: int):
    """Кэшированный ArUco-детектор с субпиксельным уточнением углов."""
    dictionary = cv2.aruco.getPredefinedDictionary(dict_id)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementWinSize = 5
    params.cornerRefinementMaxIterations = 30
    params.cornerRefinementMinAccuracy = 0.01
    params.minMarkerPerimeterRate = 0.01
    params.maxMarkerPerimeterRate = 4.0
    params.adaptiveThreshWinSizeMin = 5
    params.adaptiveThreshWinSizeMax = 41
    params.adaptiveThreshWinSizeStep = 8
    params.polygonalApproxAccuracyRate = 0.04
    return cv2.aruco.ArucoDetector(dictionary, params)


def _known_markers(layout: Optional[Dict[str, Any]]) -> Dict[int, np.ndarray]:
    """Ожидаемые координаты маркеров: из layout или стандартный планшет."""
    if layout is not None:
        found = marker_corners_px(layout)
        if found:
            return found
    return {mid: np.asarray(pts, dtype=np.float64)
            for mid, pts in spec.default_marker_corners_px().items()}


def _detect_markers(gray: np.ndarray):
    """Возвращает (corners, ids) или (None, None)."""
    try:
        detector = _detector(int(getattr(cv2.aruco, spec.ARUCO_DICT)))
        corners, ids, _ = detector.detectMarkers(gray)
    except cv2.error:
        return None, None
    return corners, ids


def detect_marker_ids(frame_bgr: np.ndarray) -> Dict[int, np.ndarray]:
    """Все ArUco-маркеры кадра: id -> углы (4,2). Нужно для идентификации планшета."""
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return {}
    frame = frame_bgr
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim != 3 or frame.shape[0] < 48 or frame.shape[1] < 48:
        return {}
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids = _detect_markers(gray)
    if corners is None or ids is None:
        return {}
    return {int(raw_id): corners[idx].reshape(4, 2).astype(np.float64)
            for idx, raw_id in enumerate(ids.ravel())}


def _coverage(points: np.ndarray, width: int, height: int) -> float:
    """Доля кадра, покрытая выпуклой оболочкой найденных маркеров."""
    if len(points) < 3:
        return 0.0
    hull = cv2.convexHull(points.astype(np.float32))
    area = abs(float(cv2.contourArea(hull)))
    return float(max(0.0, min(1.0, area / float(width * height))))


def _board_quad(homography: np.ndarray, board_w: float, board_h: float) -> Optional[np.ndarray]:
    """Проецирует углы планшета, проверяет конечность и выпуклость."""
    src = np.array([[[0.0, 0.0]], [[board_w, 0.0]],
                    [[board_w, board_h]], [[0.0, board_h]]], dtype=np.float64)
    quad = cv2.perspectiveTransform(src, homography).reshape(-1, 2)
    if not np.all(np.isfinite(quad)):
        return None
    if cv2.isContourConvex(quad.astype(np.float32)) and abs(cv2.contourArea(quad.astype(np.float32))) > 1.0:
        return quad
    return None


def _estimate_pnp(obj_mm: np.ndarray, img_px: np.ndarray,
                  frame_size: Tuple[int, int]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[float]]:
    """solvePnP по плоским точкам; камера неизвестна, K оценочная."""
    width, height = frame_size
    focal = 1.2 * max(width, height)
    camera = np.array([[focal, 0.0, width / 2.0],
                       [0.0, focal, height / 2.0],
                       [0.0, 0.0, 1.0]], dtype=np.float64)
    obj = obj_mm.reshape(-1, 1, 3).astype(np.float64)
    img = img_px.reshape(-1, 1, 2).astype(np.float64)
    for flag in (cv2.SOLVEPNP_IPPE, cv2.SOLVEPNP_ITERATIVE):
        try:
            ok, rvec, tvec = cv2.solvePnP(obj, img, camera, None, flags=flag)
        except cv2.error:
            continue
        if not ok:
            continue
        try:
            proj, _ = cv2.projectPoints(obj, rvec, tvec, camera, None)
        except cv2.error:
            continue
        err = float(np.mean(np.linalg.norm(proj.reshape(-1, 2) - img_px, axis=1)))
        return rvec, tvec, err
    return None, None, None


def detect_board(frame_bgr: np.ndarray,
                 layout: Optional[Dict[str, Any]] = None,
                 *,
                 min_markers: int = 3,
                 max_reprojection_error: float = 6.0,
                 min_coverage: float = 0.02) -> Optional[BoardPose]:
    """Находит планшет в кадре.

    Нужно минимум `min_markers` маркеров из layout. Гомография строится
    по всем видимым углам (`cv2.findHomography`, RANSAC + уточнение),
    дополнительно считается поза через `solvePnP`, если доступна.
    """
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return None
    frame = frame_bgr
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim != 3 or frame.shape[0] < 48 or frame.shape[1] < 48:
        return None
    height, width = frame.shape[:2]

    known = _known_markers(layout)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids = _detect_markers(gray)
    if corners is None or ids is None:
        return None

    detected: Dict[int, np.ndarray] = {}
    for idx, raw_id in enumerate(ids.ravel()):
        mid = int(raw_id)
        if mid in known:
            detected[mid] = corners[idx].reshape(4, 2).astype(np.float64)
    if len(detected) < min_markers:
        return None

    used = sorted(detected)
    src = np.concatenate([known[mid] for mid in used])
    dst = np.concatenate([detected[mid] for mid in used])

    homography, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if homography is None:
        return None
    if inliers is not None and int(inliers.sum()) >= 8:
        mask = inliers.ravel().astype(bool)
        refined, _ = cv2.findHomography(src[mask], dst[mask], 0)
        if refined is not None:
            homography = refined
    if abs(homography[2, 2]) > 1e-12:
        homography = homography / homography[2, 2]
    if not np.all(np.isfinite(homography)):
        return None
    if float(np.linalg.det(homography[:2, :2])) <= 0.0:
        return None

    projected = cv2.perspectiveTransform(src.reshape(-1, 1, 2), homography).reshape(-1, 2)
    reproj = float(np.mean(np.linalg.norm(projected - dst, axis=1)))

    board = (layout or {}).get("board", {})
    board_w = float(board.get("width_px", spec.BOARD_W_PX))
    board_h = float(board.get("height_px", spec.BOARD_H_PX))
    identity_id = board.get("identity_marker_id")
    guard_id = board.get("guard_marker_id")
    if identity_id is None or guard_id is None:
        # Совместимость с layout без явных полей: ищем маркеры по роли.
        for marker in (layout or {}).get("markers", []):
            role = marker.get("role")
            if role == "identity" and identity_id is None:
                identity_id = marker.get("id")
            elif role == "guard" and guard_id is None:
                guard_id = marker.get("id")
    identity_id = int(identity_id) if identity_id is not None else None
    guard_id = int(guard_id) if guard_id is not None else None
    quad = _board_quad(homography, board_w, board_h)
    if quad is None:
        return None

    coverage = _coverage(dst, width, height)
    is_valid = bool(reproj <= max_reprojection_error and coverage >= min_coverage
                    and len(used) >= min_markers)

    ppm_x, ppm_y = px_per_mm(layout) if layout else (spec.PX_PER_MM, spec.PX_PER_MM)
    obj_mm = np.concatenate([
        np.column_stack([known[mid][:, 0] / ppm_x, known[mid][:, 1] / ppm_y,
                         np.zeros(4)])
        for mid in used
    ])
    img_px = np.concatenate([detected[mid] for mid in used])
    rvec, tvec, pnp_error = _estimate_pnp(obj_mm, img_px, (width, height))

    return BoardPose(
        homography=homography.astype(np.float64),
        marker_ids=used,
        reprojection_error=reproj,
        coverage=coverage,
        is_valid=is_valid,
        frame_size=(width, height),
        marker_corners={mid: detected[mid].copy() for mid in used},
        board_corners=quad,
        rvec=rvec,
        tvec=tvec,
        pnp_error=pnp_error,
        method="homography+pnp" if pnp_error is not None else "homography",
        identity_marker_id=identity_id if identity_id in detected else None,
        board_id=board.get("id"),
        guard_marker_id=guard_id,
        guard_marker_visible=(guard_id in detected) if guard_id is not None else None,
    )
