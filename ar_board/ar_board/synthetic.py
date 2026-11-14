"""Синтетические кадры с известной истинной гомографией (тесты и eval)."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np


def _rotation(ax: float, ay: float, az: float) -> np.ndarray:
    cx, sx = np.cos(ax), np.sin(ax)
    cy, sy = np.cos(ay), np.sin(ay)
    cz, sz = np.cos(az), np.sin(az)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    return rz @ ry @ rx


def _board_corners(board_w: float, board_h: float) -> np.ndarray:
    return np.array([[[0.0, 0.0]], [[board_w, 0.0]],
                     [[board_w, board_h]], [[0.0, board_h]]], dtype=np.float64)


def sample_homography(layout: Dict[str, Any],
                      frame_size: Tuple[int, int],
                      rng: np.random.Generator,
                      *,
                      max_tilt_deg: float = 35.0,
                      scale_range: Tuple[float, float] = (0.5, 1.0),
                      tilt_deg: Optional[float] = None) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Случайная перспектива планшета: H (пиксели планшета -> пиксели кадра).

    Модель: pinhole-камера, поворот плоскости на `tilt_deg` (по умолчанию
    случайно до `max_tilt_deg`), масштаб планшета в кадре из `scale_range`.
    """
    fw, fh = frame_size
    board = layout["board"]
    bw, bh = float(board["width_px"]), float(board["height_px"])
    ppm = bw / float(board["width_mm"])

    focal = 1.15 * max(fw, fh)
    camera = np.array([[focal, 0.0, fw / 2.0],
                       [0.0, focal, fh / 2.0],
                       [0.0, 0.0, 1.0]], dtype=np.float64)

    tilt = float(rng.uniform(8.0, max_tilt_deg)) if tilt_deg is None else float(tilt_deg)
    phi = float(rng.uniform(0.0, 2.0 * np.pi))
    ax = np.deg2rad(tilt * np.cos(phi))
    ay = np.deg2rad(tilt * np.sin(phi))
    az = np.deg2rad(float(rng.uniform(-10.0, 10.0)))
    rotation = _rotation(ax, ay, az)
    r1, r2 = rotation[:, 0], rotation[:, 1]

    target = float(rng.uniform(*scale_range))
    to_unit = np.diag([1.0 / (ppm * 1000.0), 1.0 / (ppm * 1000.0), 1.0])
    center = np.array([[1.0, 0.0, -bw / 2.0],
                       [0.0, 1.0, -bh / 2.0],
                       [0.0, 0.0, 1.0]], dtype=np.float64)
    corners = _board_corners(bw, bh)

    def homography_at(distance: float) -> np.ndarray:
        motion = np.column_stack([r1, r2, np.array([0.0, 0.0, distance])])
        return camera @ motion @ to_unit @ center

    distance = 1.0
    scale = 0.0
    for _ in range(6):
        homography = homography_at(distance)
        pts = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
        bw_p = float(pts[:, 0].max() - pts[:, 0].min())
        bh_p = float(pts[:, 1].max() - pts[:, 1].min())
        scale = max(bw_p / fw, bh_p / fh)
        distance *= scale / target

    homography = homography_at(distance)
    pts = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
    bbox_w = float(pts[:, 0].max() - pts[:, 0].min())
    bbox_h = float(pts[:, 1].max() - pts[:, 1].min())

    slack_x = max(0.0, (fw - bbox_w) / 2.0 - 6.0)
    slack_y = max(0.0, (fh - bbox_h) / 2.0 - 6.0)
    shift_x = fw / 2.0 - float(pts[:, 0].mean()) + float(rng.uniform(-slack_x, slack_x))
    shift_y = fh / 2.0 - float(pts[:, 1].mean()) + float(rng.uniform(-slack_y, slack_y))
    translate = np.array([[1.0, 0.0, shift_x], [0.0, 1.0, shift_y], [0.0, 0.0, 1.0]])
    homography = translate @ homography

    pts = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
    scale = max((float(pts[:, 0].max()) - float(pts[:, 0].min())) / fw,
                (float(pts[:, 1].max()) - float(pts[:, 1].min())) / fh)

    meta = {
        "tilt_deg": round(tilt, 2),
        "scale": round(scale, 4),
        "distance_m": round(distance, 4),
        "rotation_deg": [round(float(np.rad2deg(ax)), 2),
                         round(float(np.rad2deg(ay)), 2),
                         round(float(np.rad2deg(az)), 2)],
    }
    return homography, meta


def render_frame(board_bgr: np.ndarray, homography: np.ndarray,
                 frame_size: Tuple[int, int], rng: np.random.Generator,
                 *, jpeg_quality: Optional[int] = 90,
                 noise_sigma: float = 4.0) -> np.ndarray:
    """Перспективное наложение планшета на фон + яркость/контраст/шум."""
    fw, fh = frame_size
    gradient = np.linspace(-12.0, 12.0, fh, dtype=np.float32)[:, None, None]
    background = np.clip(96.0 + gradient + rng.normal(0.0, 2.5, (fh, fw, 3)),
                         0, 255).astype(np.uint8)

    warped = cv2.warpPerspective(board_bgr, homography, (fw, fh),
                                 flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.ones(board_bgr.shape[:2], np.float32), homography,
                               (fw, fh), flags=cv2.INTER_LINEAR)
    mask3 = np.clip(mask, 0.0, 1.0)[..., None]
    frame = background.astype(np.float32) * (1.0 - mask3) + warped.astype(np.float32) * mask3

    alpha = float(rng.uniform(0.78, 1.18))
    beta = float(rng.uniform(-22.0, 22.0))
    frame = frame * alpha + beta + rng.normal(0.0, noise_sigma, frame.shape)
    frame = np.clip(frame, 0, 255).astype(np.uint8)

    if jpeg_quality is not None:
        ok, buf = cv2.imencode(".jpg", frame,
                               [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
        if ok:
            frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return frame


def make_case(layout: Dict[str, Any], board_bgr: np.ndarray,
              frame_size: Tuple[int, int], rng: np.random.Generator,
              *, max_tilt_deg: float = 35.0,
              scale_range: Tuple[float, float] = (0.5, 1.0),
              tilt_deg: Optional[float] = None) -> Dict[str, Any]:
    """Один синтетический кадр: frame, истинная H и параметры съёмки."""
    homography, meta = sample_homography(layout, frame_size, rng, max_tilt_deg=max_tilt_deg,
                                         scale_range=scale_range, tilt_deg=tilt_deg)
    frame = render_frame(board_bgr, homography, frame_size, rng)
    return {"frame": frame, "homography": homography, "meta": meta}


def corner_errors(layout: Dict[str, Any], homography_gt: np.ndarray,
                  homography_est: np.ndarray) -> Tuple[float, float]:
    """Ошибка проекции углов зон: (средняя, максимальная) в пикселях кадра."""
    src: list[np.ndarray] = []
    for comp in layout.get("components", []):
        poly = comp.get("polygon_px") or []
        if len(poly) >= 3:
            src.append(np.asarray(poly, dtype=np.float64).reshape(-1, 1, 2))
    if not src:
        for marker in layout.get("markers", []):
            src.append(np.asarray(marker["corners_px"], dtype=np.float64).reshape(-1, 1, 2))
    if not src:
        return 0.0, 0.0
    points = np.concatenate(src)
    truth = cv2.perspectiveTransform(points, homography_gt).reshape(-1, 2)
    estimate = cv2.perspectiveTransform(points, homography_est).reshape(-1, 2)
    distances = np.linalg.norm(truth - estimate, axis=1)
    return float(distances.mean()), float(distances.max())
