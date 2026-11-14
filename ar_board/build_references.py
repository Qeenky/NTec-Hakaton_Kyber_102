"""Эталоны для безмаркерной привязки: только область фотографии.

Планшеты имеют большой белый фон, заголовок и QR — ORB цепляется за них,
и дефектный планшет «совпадает» с чистым. Поэтому эталоном делаем сам снимок
оборудования, а зоны пересчитываем в его координаты.

    python ar_board/build_references.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
AR = ROOT / "ar_board"
OUT = AR / "ref"

BOARDS = {"PUMP-001": AR / "out_real", "PUMP-002": AR / "out_defect"}


def build(board_id: str, src: Path) -> dict:
    layout = json.loads((src / "layout.json").read_text(encoding="utf-8"))
    rect = layout["photo_rect_px"]
    x0, y0, x1, y1 = (int(round(v)) for v in rect)

    image = Image.open(src / "board_A3.png").convert("RGB")
    crop = image.crop((x0, y0, x1, y1))
    crop_path = OUT / f"{board_id}_photo.png"
    crop.save(crop_path)

    components = []
    for comp in layout.get("components", []):
        poly = [[float(x) - x0, float(y) - y0] for x, y in comp["polygon_px"]]
        components.append({**comp, "polygon_px": poly})

    ref_layout = {
        "version": 1,
        "equipment_id": layout.get("equipment_id", "PUMP-001"),
        # Валидатор бэкенда требует board и markers — оставляем их пустыми,
        # маркеров на эталоне нет.
        "board": {
            "id": board_id,
            "has_markers": False,
            "identity_marker_id": None,
            "guard_marker_id": None,
            "title": layout.get("board", {}).get("title", ""),
            "width_px": crop.width,
            "height_px": crop.height,
        },
        "markers": [],
        "aruco": None,
        "photo_rect_px": [0, 0, crop.width, crop.height],
        "components": components,
    }
    layout_path = OUT / f"{board_id}_layout.json"
    layout_path.write_text(json.dumps(ref_layout, ensure_ascii=False, indent=1), encoding="utf-8")

    # Пути — относительно ar_board/: бэкенд резолвит их от каталога references.json.
    return {
        "board_id": board_id,
        "image": str(crop_path.relative_to(AR)).replace("\\", "/"),
        "layout": str(layout_path.relative_to(AR)).replace("\\", "/"),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    refs = [build(board_id, src) for board_id, src in BOARDS.items()]
    (AR / "references.json").write_text(
        json.dumps({"references": refs}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for ref in refs:
        print(f"{ref['board_id']}: {ref['image']} + {ref['layout']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
