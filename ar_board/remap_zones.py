"""Пересчёт зон узлов при пересборке планшета.

При генерации нового листа фото может сместиться, а зоны остаются в старых
координатах — тогда рамки «уезжают» и кажутся огромными. Скрипт берёт зоны
в нормализованных координатах фотографии из старого layout и переносит их
на новый photo_rect.

    python ar_board/remap_zones.py --old out_real_markers/layout.json --new out_real/layout.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    args = ap.parse_args()

    old_path = Path(args.old)
    new_path = Path(args.new)
    old = load(old_path)
    new = load(new_path)

    ox0, oy0, ox1, oy1 = old["photo_rect_px"]
    nx0, ny0, nx1, ny1 = new["photo_rect_px"]
    ow, oh = ox1 - ox0, oy1 - oy0
    nw, nh = nx1 - nx0, ny1 - ny0

    old_zones = {c["component_id"]: c for c in old.get("components", [])}
    remapped = []
    for comp in new.get("components", []):
        src = old_zones.get(comp["component_id"])
        if src is None:
            remapped.append(comp)
            continue
        # старые координаты -> нормализованные в фото -> новые координаты
        poly = []
        for x, y in src["polygon_px"]:
            u = (float(x) - ox0) / ow
            v = (float(y) - oy0) / oh
            poly.append([nx0 + u * nw, ny0 + v * nh])
        comp["polygon_px"] = poly
        comp["polygon_norm"] = [
            [(float(x) - nx0) / nw, (float(y) - ny0) / nh] for x, y in poly
        ]
        remapped.append(comp)

    new["components"] = remapped
    new_path.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")

    for comp in remapped:
        xs = [p[0] for p in comp["polygon_px"]]
        ys = [p[1] for p in comp["polygon_px"]]
        print(
            "%-16s x %6.0f-%6.0f  y %6.0f-%6.0f"
            % (comp["component_id"], min(xs), max(xs), min(ys), max(ys))
        )
    print(f"photo_rect: {new['photo_rect_px']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
