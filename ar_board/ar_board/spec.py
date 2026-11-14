"""Геометрия печатного планшета A3 — единый источник правды.

Используется и генератором (`make_board.py`), и рантайм-детектором,
поэтому координаты маркеров совпадают до пикселя.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

DPI: int = 300
PAGE_W_MM: float = 420.0
PAGE_H_MM: float = 297.0
PX_PER_MM: float = DPI / 25.4

BOARD_W_PX: int = round(PAGE_W_MM * PX_PER_MM)  # 4961
BOARD_H_PX: int = round(PAGE_H_MM * PX_PER_MM)  # 3508

MARGIN_MM: float = 15.0
MARKER_SIZE_MM: float = 60.0
QUIET_ZONE_MM: float = 10.0
ARUCO_DICT: str = "DICT_4X4_50"

# Левый верхний угол чёрного квадрата маркера, мм. Порядок id = TL, TR, BR, BL.
MARKER_ORIGIN_MM: Dict[int, Tuple[float, float]] = {
    0: (15.0, 15.0),
    1: (345.0, 15.0),
    2: (345.0, 222.0),
    3: (15.0, 222.0),
}

# Свободное поле под фотографию: x0, y0, x1, y1 в мм.
PHOTO_BOX_MM: Tuple[float, float, float, float] = (15.0, 85.0, 405.0, 212.0)

# Идентификация планшета: отдельный маркер в шапке справа от подписи.
# 40 мм, чтобы читался с 1.5–2 м; координата — левый верхний угол, мм.
IDENTITY_MARKER_SIZE_MM: float = 40.0
IDENTITY_MARKER_ORIGIN_MM: Tuple[float, float] = (236.0, 30.0)
IDENTITY_MARKER_QUIET_MM: float = 5.0

# Маркер-«сторож» внутри зоны кожуха муфты (id 4 в DICT_4X4_50).
GUARD_MARKER_ID: int = 4
GUARD_MARKER_SIZE_MM: float = 30.0
GUARD_MARKER_QUIET_MM: float = 3.0

# Угловые маркеры листа: id 0..3 (TL, TR, BR, BL).
CORNER_MARKER_IDS: Tuple[int, ...] = (0, 1, 2, 3)


def mm_to_px(value: float) -> int:
    """Миллиметры -> пиксели планшета (округление как в make_board)."""
    return int(round(value * PX_PER_MM))


def marker_corners_px(marker_id: int) -> List[List[int]]:
    """Углы чёрного квадрата маркера на планшете: TL, TR, BR, BL."""
    x0, y0 = MARKER_ORIGIN_MM[marker_id]
    x1, y1 = x0 + MARKER_SIZE_MM, y0 + MARKER_SIZE_MM
    return [
        [mm_to_px(x0), mm_to_px(y0)],
        [mm_to_px(x1), mm_to_px(y0)],
        [mm_to_px(x1), mm_to_px(y1)],
        [mm_to_px(x0), mm_to_px(y1)],
    ]


def marker_corners_mm(marker_id: int) -> List[List[float]]:
    """Углы маркера в миллиметрах от левого верхнего угла листа."""
    x0, y0 = MARKER_ORIGIN_MM[marker_id]
    return [[x0, y0], [x0 + MARKER_SIZE_MM, y0],
            [x0 + MARKER_SIZE_MM, y0 + MARKER_SIZE_MM], [x0, y0 + MARKER_SIZE_MM]]


def default_marker_corners_px() -> Dict[int, List[List[int]]]:
    """Координаты всех маркеров стандартного планшета."""
    return {mid: marker_corners_px(mid) for mid in sorted(MARKER_ORIGIN_MM)}


def box_corners_px(origin_mm: Tuple[float, float],
                   size_mm: float) -> List[List[int]]:
    """Углы квадрата маркера по левому верхнему углу и стороне, TL, TR, BR, BL."""
    x0, y0 = origin_mm
    x1, y1 = x0 + size_mm, y0 + size_mm
    return [
        [mm_to_px(x0), mm_to_px(y0)],
        [mm_to_px(x1), mm_to_px(y0)],
        [mm_to_px(x1), mm_to_px(y1)],
        [mm_to_px(x0), mm_to_px(y1)],
    ]


def box_corners_mm(origin_mm: Tuple[float, float],
                   size_mm: float) -> List[List[float]]:
    """Углы квадрата маркера в мм: TL, TR, BR, BL."""
    x0, y0 = origin_mm
    x1, y1 = x0 + size_mm, y0 + size_mm
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def identity_marker_corners_px() -> List[List[int]]:
    """Координаты маркера идентификации в шапке планшета."""
    return box_corners_px(IDENTITY_MARKER_ORIGIN_MM, IDENTITY_MARKER_SIZE_MM)


def identity_marker_corners_mm() -> List[List[float]]:
    """Координаты маркера идентификации в миллиметрах."""
    return box_corners_mm(IDENTITY_MARKER_ORIGIN_MM, IDENTITY_MARKER_SIZE_MM)
