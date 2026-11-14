"""Проекция зон узлов на кадр, сглаживание и отладочная отрисовка."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

from .detect import BoardPose

# OpenCV putText не умеет кириллицу — для оверлея транслитерируем.
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Sch",
    "Ы": "Y", "Э": "E", "Ю": "Yu", "Я": "Ya",
})

_PALETTE = [
    (54, 196, 255),   # motor
    (105, 220, 120),  # pump
    (255, 196, 92),   # coupling_guard
    (110, 160, 255),  # flange
    (235, 120, 240),  # manometer
]


def _color(index: int, component_id: str) -> tuple[int, int, int]:
    if component_id == "motor":
        return _PALETTE[0]
    if component_id == "pump":
        return _PALETTE[1]
    if component_id == "coupling_guard":
        return _PALETTE[2]
    if component_id == "flange":
        return _PALETTE[3]
    if component_id == "manometer":
        return _PALETTE[4]
    return _PALETTE[index % len(_PALETTE)]


def translit(text: str) -> str:
    """Кириллица -> латиница для cv2.putText."""
    return str(text).translate(_TRANSLIT)


def project_components(pose: Optional[BoardPose],
                       layout: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Переносит полигоны зон из пикселей планшета в пиксели кадра.

    `visible=False`, если полигон вышел за кадр или ушёл за плоскость камеры.
    """
    if pose is None:
        return []
    width, height = pose.frame_size
    homography = pose.homography.astype(np.float64)
    result: List[Dict[str, Any]] = []

    for comp in layout.get("components", []):
        raw = comp.get("polygon_px") or []
        if len(raw) < 3:
            continue
        poly = np.asarray(raw, dtype=np.float64).reshape(-1, 2)
        ones = np.ones((len(poly), 1))
        homogeneous = np.hstack([poly, ones]) @ homography.T
        w = homogeneous[:, 2]
        if np.any(w <= 1e-6):
            result.append({
                "component_id": comp.get("component_id", "unknown"),
                "label": comp.get("label", ""),
                "polygon": [],
                "polygon_px": poly.round(3).tolist(),
                "visible": False,
                "in_frame_ratio": 0.0,
            })
            continue
        pts = homogeneous[:, :2] / w[:, None]
        inside = ((pts[:, 0] >= -0.5) & (pts[:, 0] <= width + 0.5)
                  & (pts[:, 1] >= -0.5) & (pts[:, 1] <= height + 0.5))
        ratio = float(np.mean(inside))
        area = abs(float(cv2.contourArea(pts.astype(np.float32))))
        result.append({
            "component_id": comp.get("component_id", "unknown"),
            "label": comp.get("label", ""),
            "polygon": [[round(float(x), 3), round(float(y), 3)] for x, y in pts],
            "polygon_px": poly.round(3).tolist(),
            "visible": bool(ratio >= 1.0 and area > 4.0),
            "in_frame_ratio": round(ratio, 4),
            "area_px": round(area, 2),
        })
    return result


class Smoother:
    """EMA по углам полигонов: гасит дрожание рамок.

    Сброс, если маркеры не видны дольше `reset_after` секунд.
    """

    def __init__(self, alpha: float = 0.35, reset_after: float = 1.0) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha должен быть в диапазоне (0, 1]")
        self.alpha = float(alpha)
        self.reset_after = float(reset_after)
        self._state: Dict[str, np.ndarray] = {}
        self._last_seen: Optional[float] = None

    def reset(self) -> None:
        """Забыть накопленное состояние."""
        self._state.clear()
        self._last_seen = None

    def update(self, projected: Sequence[Dict[str, Any]],
               timestamp: Optional[float] = None) -> List[Dict[str, Any]]:
        """Возвращает копию projected со сглаженными полигонами."""
        now = time.monotonic() if timestamp is None else float(timestamp)
        if self._last_seen is not None and now - self._last_seen > self.reset_after:
            self._state.clear()
        if not projected:
            return []

        smoothed: List[Dict[str, Any]] = []
        alive: set[str] = set()
        for item in projected:
            cid = str(item.get("component_id", "unknown"))
            poly = item.get("polygon") or []
            if len(poly) < 3:
                smoothed.append(dict(item))
                continue
            current = np.asarray(poly, dtype=np.float64).reshape(-1, 2)
            previous = self._state.get(cid)
            if previous is None or previous.shape != current.shape:
                value = current
            else:
                value = self.alpha * current + (1.0 - self.alpha) * previous
            self._state[cid] = value
            alive.add(cid)
            out = dict(item)
            out["polygon"] = [[round(float(x), 3), round(float(y), 3)] for x, y in value]
            smoothed.append(out)

        for cid in list(self._state):
            if cid not in alive:
                del self._state[cid]
        if any(item.get("polygon") for item in projected):
            self._last_seen = now
        return smoothed


def _put_label(frame: np.ndarray, text: str, org: tuple[int, int],
               color: tuple[int, int, int], scale: float = 0.55) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    thickness = 1
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    x, y = org
    x = int(max(2, min(x, frame.shape[1] - tw - 6)))
    y = int(max(th + 6, min(y, frame.shape[0] - 6)))
    cv2.rectangle(frame, (x - 3, y - th - 5), (x + tw + 4, y + baseline + 2),
                  (18, 22, 28), -1)
    cv2.putText(frame, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)


def render_overlay(frame_bgr: np.ndarray,
                   projected: Sequence[Dict[str, Any]],
                   pose: Optional[BoardPose] = None,
                   *,
                   fps: Optional[float] = None,
                   show_hud: bool = True) -> np.ndarray:
    """Отладочная отрисовка: полигоны зон, маркеры и HUD. Возвращает копию."""
    if frame_bgr is None:
        raise ValueError("render_overlay: пустой кадр")
    out = frame_bgr.copy()
    if out.ndim == 2:
        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)

    tint = out.copy()
    for index, item in enumerate(projected):
        poly = item.get("polygon") or []
        if len(poly) < 3:
            continue
        pts = np.asarray(poly, dtype=np.int32).reshape(-1, 1, 2)
        color = _color(index, str(item.get("component_id", "")))
        if not item.get("visible", True):
            color = (140, 140, 140)
        cv2.fillPoly(tint, [pts], color)
    out = cv2.addWeighted(tint, 0.22, out, 0.78, 0.0)

    for index, item in enumerate(projected):
        poly = item.get("polygon") or []
        if len(poly) < 3:
            continue
        pts = np.asarray(poly, dtype=np.int32).reshape(-1, 1, 2)
        color = _color(index, str(item.get("component_id", "")))
        visible = bool(item.get("visible", True))
        if not visible:
            color = (150, 150, 150)
        thickness = 3 if visible else 1
        cv2.polylines(out, [pts], True, (10, 12, 16), thickness + 2, cv2.LINE_AA)
        cv2.polylines(out, [pts], True, color, thickness, cv2.LINE_AA)
        label = translit(item.get("label") or item.get("component_id", ""))
        box_width = float(pts[:, 0, 0].max() - pts[:, 0, 0].min())
        scale = 0.55
        text_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0]
        if text_width > max(40.0, box_width * 1.1):
            label = str(item.get("component_id", ""))
            text_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0]
            if text_width > max(40.0, box_width * 1.1):
                scale = max(0.35, scale * box_width / max(1.0, text_width))
        _put_label(out, label, (int(pts[0][0][0]), int(pts[0][0][1]) - 6), color, scale)

    if pose is not None:
        if pose.board_corners is not None:
            quad = pose.board_corners.astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(out, [quad], True, (80, 200, 255), 1, cv2.LINE_AA)
        for mid, corners in pose.marker_corners.items():
            pts = corners.astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(out, [pts], True, (255, 255, 255), 1, cv2.LINE_AA)
            center = corners.mean(axis=0).astype(int)
            _put_label(out, f"id={mid}", (int(center[0]) - 14, int(center[1]) + 5),
                       (255, 255, 255), 0.45)

    if show_hud:
        if pose is None:
            hud = "BOARD: not found"
        else:
            identity = (f"id {pose.identity_marker_id}"
                        if pose.identity_marker_id is not None else "no identity")
            hud = (f"board {pose.board_id or '?'}  identity {identity}  "
                   f"markers {len(pose.marker_ids)}  reproj {pose.reprojection_error:.2f}px  "
                   f"coverage {pose.coverage * 100:.0f}%  {'OK' if pose.is_valid else 'WEAK'}")
        if fps is not None:
            hud = f"FPS {fps:5.1f}  |  {hud}"
        _put_label(out, hud, (10, 22), (235, 235, 235), 0.55)
    return out
