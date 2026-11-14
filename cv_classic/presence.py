"""Проверка наличия объекта по ArUco-маркеру (маркерная проверка, не детекция).

На защитном кожухе муфты наклеен маркер `DICT_4X4_50`, id 4 (см.
`configs/presence.json`). Если маркер не виден в кадре — кожух отсутствует или
смещён. Если маркер виден, но не в зоне кожуха — это чужой/смещённый маркер,
и «присутствует» засчитывать нельзя.

    check_marker(frame_bgr, marker_id=4)                 -> PresenceResult
    check_marker_in_roi(frame_bgr, 4, roi_polygon)       -> PresenceResult

`roi_polygon` — список/массив точек [(x, y), ...] в координатах кадра.

Почему это честно: мы не подменяем детекцию объекта распознаванием маркера —
мы проверяем наличие конкретной метки на конкретном кожухе. Для правила
`guard_removed` этого достаточно; ограничения (закрыт/стёрт/блик) — в README.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .defects import to_bgr

CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "presence.json"

ERROR_BAD_IMAGE = "bad_image"
ERROR_BAD_DICTIONARY = "bad_dictionary"
ERROR_MARKER_TOO_SMALL = "marker_too_small"
ERROR_BAD_ROI = "bad_roi"
ERROR_INTERNAL = "internal_error"

DEFAULT_CONFIG: dict[str, Any] = {
    "dictionary": "DICT_4X4_50",
    "min_side_px": 12.0,
    "good_side_px": 45.0,
    "min_inside_frac": 0.5,
    "absent_confidence": 0.55,
    "absent_textured_confidence": 0.70,
    "present_confidence_base": 0.55,
    "present_confidence_span": 0.45,
    "too_small_confidence": 0.20,
    "min_frame_side_px": 32,
    "min_texture_std": 4.0,
}


@dataclass
class PresenceResult:
    present: bool
    confidence: float
    corners: list[list[float]] | None = None
    error: str | None = None
    marker_id: int | None = None
    reason: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=8)
def _read_config(path: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)
    cfg = dict(DEFAULT_CONFIG)
    for k, v in raw.items():
        if k.startswith("_") or k == "schema":
            continue
        cfg[k] = v
    return cfg


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path is not None else CONFIG_PATH
    return _read_config(str(p))


@lru_cache(maxsize=8)
def _detector(dictionary: str):
    """Кэш ArucoDetector по имени словаря."""
    dict_id = getattr(cv2.aruco, dictionary, None)
    if dict_id is None:
        raise ValueError(f"unknown aruco dictionary: {dictionary}")
    d = cv2.aruco.getPredefinedDictionary(dict_id)
    params = cv2.aruco.DetectorParameters()
    # Маркер маленький и снят под углом: разрешаем более широкий поиск углов.
    try:
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        params.adaptiveThreshWinSizeMin = 5
        params.adaptiveThreshWinSizeMax = 35
    except AttributeError:  # разные сборки OpenCV
        pass
    return cv2.aruco.ArucoDetector(d, params), d


def _quad_side(quad: np.ndarray) -> float:
    q = np.asarray(quad, np.float32).reshape(4, 2)
    sides = [float(np.linalg.norm(q[(i + 1) % 4] - q[i])) for i in range(4)]
    return min(sides)


def _detect(frame_bgr: Any, marker_id: int, cfg: dict
            ) -> tuple[list[np.ndarray], np.ndarray | None, str | None, float]:
    """Общая часть: детекция всех маркеров. -> (corners, ids, error, texture_std)."""
    try:
        bgr = to_bgr(frame_bgr)
    except Exception:  # noqa: BLE001
        return [], None, ERROR_BAD_IMAGE, 0.0
    h, w = bgr.shape[:2]
    if min(h, w) < int(cfg["min_frame_side_px"]):
        return [], None, ERROR_BAD_IMAGE, 0.0
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    try:
        det, _ = _detector(str(cfg["dictionary"]))
        corners, ids, _ = det.detectMarkers(gray)
    except Exception:  # noqa: BLE001
        return [], None, ERROR_BAD_DICTIONARY, float(gray.std())
    if ids is None:
        return [], np.empty(0, np.int32), None, float(gray.std())
    ids_arr = np.asarray(ids).reshape(-1)
    return [np.asarray(c, np.float32).reshape(4, 2) for c in corners], ids_arr, \
        None, float(gray.std())


def check_marker(frame_bgr: Any, marker_id: int,
                 dictionary: str | None = None,
                 config: dict | None = None) -> PresenceResult:
    """Есть ли маркер `marker_id` в кадре.

    present=True только если маркер найден и его сторона >= min_side_px
    (слишком мелкий маркер ненадёжен: возвращается error=marker_too_small).
    """
    cfg = dict(config) if config else load_config()
    if dictionary:
        cfg = dict(cfg, dictionary=dictionary)
    try:
        found, ids, err, texture = _detect(frame_bgr, marker_id, cfg)
        if err is not None:
            return PresenceResult(False, 0.0, None, err, marker_id,
                                  "bad_input", {"texture_std": round(texture, 2)})
        hits = [c for c, i in zip(found, ids) if int(i) == int(marker_id)]
        if not hits:
            base = float(cfg["absent_confidence"])
            textured = float(cfg["absent_textured_confidence"])
            conf = textured if texture >= float(cfg["min_texture_std"]) else base
            return PresenceResult(False, round(conf, 3), None, None, marker_id,
                                  "marker_not_found",
                                  {"texture_std": round(texture, 2),
                                   "markers_in_frame": int(len(found))})
        quad = max(hits, key=_quad_side)
        side = _quad_side(quad)
        min_side = float(cfg["min_side_px"])
        good = float(cfg["good_side_px"])
        size_q = float(np.clip((side - min_side) / max(good - min_side, 1.0), 0, 1))
        det_conf = round(float(cfg["present_confidence_base"]) +
                         float(cfg["present_confidence_span"]) * size_q, 3)
        pts = [[round(float(x), 2), round(float(y), 2)] for x, y in quad]
        debug = {"side_px": round(side, 2), "markers_in_frame": int(len(found))}
        if side < min_side:
            return PresenceResult(False, float(cfg["too_small_confidence"]), pts,
                                  ERROR_MARKER_TOO_SMALL,
                                  marker_id, "marker_too_small", debug)
        return PresenceResult(True, det_conf, pts, None, marker_id, "marker_found",
                              debug)
    except Exception as exc:  # noqa: BLE001
        return PresenceResult(False, 0.0, None, ERROR_INTERNAL, marker_id,
                              "internal_error", {"detail": str(exc)})


def _polygon_array(roi_polygon: Any) -> np.ndarray | None:
    try:
        poly = np.asarray(roi_polygon, np.float32).reshape(-1, 2)
    except Exception:  # noqa: BLE001
        return None
    if poly.shape[0] < 3:
        return None
    if not np.isfinite(poly).all():
        return None
    return poly


def check_marker_in_roi(frame_bgr: Any, marker_id: int, roi_polygon: Any,
                        dictionary: str | None = None,
                        config: dict | None = None) -> PresenceResult:
    """Маркер есть и находится в зоне (полигоне) кожуха.

    present=True, если маркер найден, его центр внутри полигона и в полигоне
    лежит не меньше `min_inside_frac` его углов. Маркер, найденный в кадре, но
    вне зоны, даёт present=False, reason='outside_roi' — координаты маркера
    всё равно возвращаются для отладки.
    """
    poly = _polygon_array(roi_polygon)
    if poly is None:
        return PresenceResult(False, 0.0, None, ERROR_BAD_ROI, marker_id,
                              "bad_roi", {"points": 0})
    res = check_marker(frame_bgr, marker_id, dictionary, config)
    if res.error is not None or not res.present or res.corners is None:
        return res
    quad = np.asarray(res.corners, np.float32).reshape(4, 2)
    center = quad.mean(axis=0)
    d_center = cv2.pointPolygonTest(poly, (float(center[0]), float(center[1])), False)
    inside_frac = float(np.mean([
        cv2.pointPolygonTest(poly, (float(x), float(y)), False) >= 0
        for x, y in quad]))
    cfg = dict(config) if config else load_config()
    if dictionary:
        cfg = dict(cfg, dictionary=dictionary)
    need = float(cfg["min_inside_frac"])
    ok = d_center >= 0 and inside_frac >= need
    debug = dict(res.debug)
    debug.update({"center_inside": bool(d_center >= 0),
                  "corners_inside_frac": round(inside_frac, 3),
                  "min_inside_frac": need, "roi_points": int(poly.shape[0])})
    return PresenceResult(ok, res.confidence, res.corners, None, marker_id,
                          "marker_in_roi" if ok else "outside_roi", debug)
