"""Генерация синтетического датасета манометров.

    python cv_classic/scripts/make_synthetic.py --count 48 --out cv_classic/data/synthetic
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402

from cv_classic.synthetic import random_specs, render  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Синтетические манометры")
    ap.add_argument("--count", type=int, default=48, help="сколько изображений")
    ap.add_argument("--out", default=str(ROOT / "cv_classic" / "data" / "synthetic"))
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    specs = random_specs(args.count, seed=args.seed)
    truth = []
    for i, spec in enumerate(specs):
        img, meta = render(spec)
        name = f"gauge_{i:03d}.png"
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            raise RuntimeError(f"encode failed: {name}")
        (out / name).write_bytes(buf.tobytes())
        meta["file"] = name
        truth.append(meta)
    (out / "truth.json").write_text(
        json.dumps({"count": len(truth), "items": truth}, ensure_ascii=False,
                   indent=1), encoding="utf-8")
    print(f"готово: {len(truth)} изображений -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
