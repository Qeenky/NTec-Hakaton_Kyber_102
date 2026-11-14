"""Контракт `layout.json`: чтение, запись, геометрия зон."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

# Фиксированный порядок узлов: клавиши 1..5 в annotate.py.
COMPONENT_ORDER: Tuple[Tuple[str, str], ...] = (
    ("motor", "Электродвигатель"),
    ("pump", "Насос"),
    ("coupling_guard", "Кожух муфты"),
    ("flange", "Фланцевое соединение"),
    ("manometer", "Манометр"),
)


def load_layout(path: str | Path) -> Dict[str, Any]:
    """Читает и минимально валидирует layout.json."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"layout.json не найден: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if "board" not in data or "markers" not in data:
        raise ValueError(f"layout.json повреждён (нет board/markers): {p}")
    return data


def save_layout(layout: Dict[str, Any], path: str | Path) -> Path:
    """Атомарно пишет layout.json (UTF-8, без ASCII-escape)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".layout_", suffix=".json", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(layout, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return p


def layout_paths(layout: Dict[str, Any], layout_file: str | Path) -> Path:
    """Путь к PNG планшета (поле board.image относительно layout.json)."""
    name = str(layout.get("board", {}).get("image") or "board_A3.png")
    p = Path(name)
    return p if p.is_absolute() else Path(layout_file).resolve().parent / p


def marker_corners_px(layout: Dict[str, Any]) -> Dict[int, np.ndarray]:
    """Маркеры из layout: id -> (4,2) float64, порядок TL, TR, BR, BL."""
    out: Dict[int, np.ndarray] = {}
    for marker in layout.get("markers", []):
        pts = np.asarray(marker.get("corners_px"), dtype=np.float64)
        if pts.shape == (4, 2):
            out[int(marker["id"])] = pts
    return out


def px_per_mm(layout: Dict[str, Any]) -> Tuple[float, float]:
    """Масштаб планшета по осям (px на мм)."""
    board = layout["board"]
    return (float(board["width_px"]) / float(board["width_mm"]),
            float(board["height_px"]) / float(board["height_mm"]))


def polygon_norm(polygon_px: Sequence[Sequence[float]],
                 layout: Dict[str, Any]) -> List[List[float]]:
    """Пиксели планшета -> нормализованные координаты 0..1."""
    w = float(layout["board"]["width_px"])
    h = float(layout["board"]["height_px"])
    return [[round(float(x) / w, 6), round(float(y) / h, 6)] for x, y in polygon_px]


def polygon_centroid(polygon: Sequence[Sequence[float]]) -> Tuple[float, float]:
    """Центр масс полигона (shoelace); при вырождении — среднее вершин."""
    pts = [(float(x), float(y)) for x, y in polygon]
    if not pts:
        raise ValueError("пустой полигон")
    if len(pts) < 3:
        return (sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts))
    twice_area = 0.0
    cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        cross = x0 * y1 - x1 * y0
        twice_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(twice_area) < 1e-9:
        return (sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts))
    return (cx / (3.0 * twice_area), cy / (3.0 * twice_area))


def point_in_polygon(point: Sequence[float],
                     polygon: Sequence[Sequence[float]]) -> bool:
    """Точка внутри (или на границе) полигона — ray casting без OpenCV."""
    x, y = float(point[0]), float(point[1])
    pts = [(float(px), float(py)) for px, py in polygon]
    if len(pts) < 3:
        return False
    inside = False
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        # на границе — считаем внутри
        cross = (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)
        if abs(cross) < 1e-9 and min(x0, x1) - 1e-9 <= x <= max(x0, x1) + 1e-9 \
                and min(y0, y1) - 1e-9 <= y <= max(y0, y1) + 1e-9:
            return True
        if (y0 > y) != (y1 > y):
            x_cross = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < x_cross:
                inside = not inside
    return inside


def components_payload(layout: Dict[str, Any],
                       boxes: Dict[str, Sequence[float]]) -> List[Dict[str, Any]]:
    """Собирает список components в фиксированном порядке по словарю боксов."""
    out: List[Dict[str, Any]] = []
    for cid, label in COMPONENT_ORDER:
        box = boxes.get(cid)
        if box is None:
            continue
        x0, y0, x1, y1 = (float(v) for v in box)
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        poly = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        out.append({
            "component_id": cid,
            "label": label,
            "polygon_px": [[round(x, 2), round(y, 2)] for x, y in poly],
            "polygon_norm": polygon_norm(poly, layout),
        })
    return out
