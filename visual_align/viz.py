"""Отрисовка для samples/ и отладки: зоны, соответствия, HUD."""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np

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
_BY_NAME = {
    "motor": _PALETTE[0],
    "pump": _PALETTE[1],
    "coupling_guard": _PALETTE[2],
    "flange": _PALETTE[3],
    "manometer": _PALETTE[4],
}


def translit(text: str) -> str:
    return str(text).translate(_TRANSLIT)


def color_for(index: int, name: str) -> Tuple[int, int, int]:
    return _BY_NAME.get(name, _PALETTE[index % len(_PALETTE)])


def put_label(frame: np.ndarray, text: str, org: Tuple[int, int],
              color: Tuple[int, int, int], scale: float = 0.55) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), baseline = cv2.getTextSize(text, font, scale, 1)
    x = int(np.clip(org[0], 2, max(2, frame.shape[1] - tw - 6)))
    y = int(np.clip(org[1], th + 6, frame.shape[0] - 6))
    cv2.rectangle(frame, (x - 3, y - th - 5), (x + tw + 4, y + baseline + 2),
                  (18, 22, 28), -1)
    cv2.putText(frame, text, (x, y), font, scale, color, 1, cv2.LINE_AA)


def draw_polygons(frame: np.ndarray,
                  zones: Mapping[str, Sequence[Sequence[float]]],
                  labels: Optional[Mapping[str, str]] = None,
                  *, dashed: bool = False,
                  dashed_color: Optional[Tuple[int, int, int]] = None,
                  thickness: int = 3,
                  tint: bool = True) -> np.ndarray:
    """Рисует полигоны зон (в координатах кадра). Возвращает копию."""
    out = frame.copy()
    labels = labels or {}
    if tint:
        overlay = out.copy()
        for index, (name, poly) in enumerate(zones.items()):
            if len(poly) < 3:
                continue
            pts = np.asarray(poly, dtype=np.int32).reshape(-1, 1, 2)
            cv2.fillPoly(overlay, [pts], color_for(index, name))
        out = cv2.addWeighted(overlay, 0.18, out, 0.82, 0.0)

    for index, (name, poly) in enumerate(zones.items()):
        if len(poly) < 3:
            continue
        points = np.asarray(poly, dtype=np.float64)
        color = dashed_color if (dashed and dashed_color is not None) \
            else color_for(index, name)
        if dashed:
            for start in range(0, len(points), 2):
                p1 = points[start]
                p2 = points[(start + 1) % len(points)]
                cv2.line(out, tuple(np.round(p1).astype(int)),
                         tuple(np.round(p2).astype(int)), color, 2, cv2.LINE_AA)
            continue
        pts = points.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(out, [pts], True, (10, 12, 16), thickness + 2, cv2.LINE_AA)
        cv2.polylines(out, [pts], True, color, thickness, cv2.LINE_AA)
        label = translit(labels.get(name, name))
        x, y = int(points[:, 0].min()), int(points[:, 1].min()) - 6
        put_label(out, label, (x, y), color, 0.5)
    return out


def draw_hud(frame: np.ndarray, lines: Sequence[str],
             origin: Tuple[int, int] = (10, 24)) -> np.ndarray:
    out = frame.copy()
    x, y = origin
    for index, line in enumerate(lines):
        put_label(out, translit(line), (x, y + index * 22), (235, 235, 235), 0.55)
    return out


def draw_matches(reference_bgr: np.ndarray, ref_keypoints: List[cv2.KeyPoint],
                 frame_bgr: np.ndarray, frame_keypoints: List[cv2.KeyPoint],
                 matches: List[cv2.DMatch], inlier_mask: np.ndarray,
                 *, height: int = 540) -> np.ndarray:
    """Картинка соответствий: зелёные — инлайеры, красные — отброшенные."""
    ref_scale = float(height) / float(reference_bgr.shape[0])
    frame_scale = float(height) / float(frame_bgr.shape[0])
    ref = cv2.resize(reference_bgr, None, fx=ref_scale, fy=ref_scale,
                     interpolation=cv2.INTER_AREA)
    frame = cv2.resize(frame_bgr, None, fx=frame_scale, fy=frame_scale,
                       interpolation=cv2.INTER_AREA)
    ref_keypoints = [cv2.KeyPoint(k.pt[0] * ref_scale, k.pt[1] * ref_scale,
                                  max(1.0, k.size * ref_scale), k.angle, k.response,
                                  k.octave, k.class_id) for k in ref_keypoints]
    frame_keypoints = [cv2.KeyPoint(k.pt[0] * frame_scale, k.pt[1] * frame_scale,
                                    max(1.0, k.size * frame_scale), k.angle, k.response,
                                    k.octave, k.class_id) for k in frame_keypoints]

    inliers = [m for m, ok in zip(matches, inlier_mask) if ok]
    outliers = [m for m, ok in zip(matches, inlier_mask) if not ok]
    canvas = cv2.drawMatches(frame, frame_keypoints, ref, ref_keypoints, outliers, None,
                             matchColor=(60, 60, 220), singlePointColor=(90, 90, 90),
                             flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
    canvas = cv2.drawMatches(frame, frame_keypoints, ref, ref_keypoints, inliers, canvas,
                             matchColor=(80, 230, 90), singlePointColor=(90, 90, 90),
                             flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
    return canvas


def annotate_reference(reference_bgr: np.ndarray,
                       zones_small: Mapping[str, Sequence[Sequence[float]]],
                       labels: Optional[Mapping[str, str]] = None) -> np.ndarray:
    """Эталон с размеченными зонами (координаты уже в масштабе картинки)."""
    out = draw_polygons(reference_bgr, zones_small, labels, thickness=4)
    return draw_hud(out, ["REFERENCE: zones"], (10, 26))
