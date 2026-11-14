"""Сборка дообучающего набора из НАШИХ планшетов (ar_board/out_real, out_defect).

Идея
----
Детектор в проде работает не по всему кадру, а по кропам зон (`adapter.CorrosionDetector`,
`crop_nodes=True`): bbox полигона + 6% отступа -> resize. Поэтому обучаем на таких же
кропах, но с синтетическими перспективами (наклон 0-20°, масштаб 0.7-1.3).

Разметка ЧЕСТНАЯ, но грубая:
  * дефектный планшет (`out_defect`): motor/pump/flange/coupling_guard -> бокс = весь кроп
    с отступом 5% (класс corrosion). Это разметка "дефектная доска", НЕ попиксельная;
  * чистый планшет (`out_real`): пустой label (негатив).

Выход: dataset_board/{images,labels}/{train,val} + data.yaml + prepare_board_report.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AR = REPO / "ar_board"
SEED = 20261114
OUT_SIZE = 640
MARGIN = 0.06          # как в adapter.CorrosionDetector.crop_margin
BOX_INSET = 0.05       # бокс = весь кроп минус 5% с каждой стороны
PERSPECTIVES = 20

BOARDS = (
    # (tag, dir, defect?)
    ("clean", AR / "out_real", False),
    ("defect", AR / "out_defect", True),
)


def imread(path: Path) -> np.ndarray:
    buf = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size else None
    if img is None:
        raise IOError(f"не прочитан {path}")
    return img


def imwrite(path: Path, img: np.ndarray, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise IOError(f"не записан {path}")
    buf.tofile(str(path))


def warp_crop(crop: np.ndarray, rng: np.random.Generator, *, out: int = OUT_SIZE,
              max_tilt: float = 20.0, rot: float = 8.0,
              scale: tuple[float, float] = (0.7, 1.3), shift: float = 0.04) -> np.ndarray:
    """Перспектива/поворот/масштаб кропа — та же модель, что canonical.simulate_unaligned."""
    h, w = crop.shape[:2]
    dst = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    s = float(rng.uniform(*scale))
    ang = float(rng.uniform(-rot, rot))
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, s)
    src = (dst @ m[:, :2].T + m[:, 2]).astype(np.float32)

    tilt = float(rng.uniform(0.0, max_tilt))
    k = 0.5 * np.tan(np.deg2rad(tilt)) * s
    if rng.random() < 0.5:
        idx = [0, 3] if rng.random() < 0.5 else [1, 2]
        src[idx, 0] += (src[idx, 0] - w / 2.0) * (-k)
    else:
        idx = [0, 1] if rng.random() < 0.5 else [2, 3]
        src[idx, 1] += (src[idx, 1] - h / 2.0) * (-k)
    src += rng.normal(0.0, shift * min(h, w), src.shape).astype(np.float32)

    hm = cv2.getPerspectiveTransform(src, dst)
    out_img = cv2.warpPerspective(crop, hm, (out, out), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)
    # фотометрия как в ar_board.synthetic.render_frame
    alpha = float(rng.uniform(0.82, 1.14))
    beta = float(rng.uniform(-18.0, 18.0))
    out_img = np.clip(out_img.astype(np.float32) * alpha + beta
                      + rng.normal(0.0, 3.0, out_img.shape), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", out_img,
                           [int(cv2.IMWRITE_JPEG_QUALITY), int(rng.integers(82, 97))])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else out_img


def component_crops(board: np.ndarray, layout: dict) -> dict[str, np.ndarray]:
    h, w = board.shape[:2]
    out: dict[str, np.ndarray] = {}
    for comp in layout.get("components", []):
        cid = str(comp.get("component_id") or "")
        poly = np.asarray(comp.get("polygon_px") or [], dtype=np.float64)
        if not cid or poly.shape[0] < 3:
            continue
        x1, y1 = poly.min(axis=0)
        x2, y2 = poly.max(axis=0)
        mx = int(round((x2 - x1) * MARGIN))
        my = int(round((y2 - y1) * MARGIN))
        x1 = max(0, int(np.floor(x1)) - mx)
        y1 = max(0, int(np.floor(y1)) - my)
        x2 = min(w, int(np.ceil(x2)) + mx)
        y2 = min(h, int(np.ceil(y2)) + my)
        out[cid] = board[y1:y2, x1:x2]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Кропы зон планшетов для дообучения YOLO")
    ap.add_argument("--out", default=str(ROOT / "dataset_board"))
    ap.add_argument("--perspectives", type=int, default=PERSPECTIVES)
    ap.add_argument("--train-perspectives", type=int, default=16)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    rng = np.random.default_rng(args.seed)

    report: dict = {
        "task": "кропы зон планшетов, синтетические перспективы",
        "seed": args.seed,
        "out_size": OUT_SIZE,
        "margin": MARGIN,
        "box_inset": BOX_INSET,
        "perspectives_per_component": args.perspectives,
        "train_perspectives": args.train_perspectives,
        "boards": {},
    }

    stats = {"train": {"images": 0, "boxes": 0, "clean": 0, "defect": 0},
             "val": {"images": 0, "boxes": 0, "clean": 0, "defect": 0}}
    per_component: dict[str, dict] = {}

    for tag, bdir, is_defect in BOARDS:
        layout = json.loads((bdir / "layout.json").read_text(encoding="utf-8"))
        board = imread(bdir / "board_A3.png")
        crops = component_crops(board, layout)
        report["boards"][tag] = {"dir": str(bdir), "components": sorted(crops),
                                 "is_defect": is_defect}
        for cid, crop in crops.items():
            key = f"{tag}_{cid}"
            per_component[key] = {"tag": tag, "component": cid,
                                  "crop_hw": [int(crop.shape[0]), int(crop.shape[1])],
                                  "is_defect": is_defect}
            for i in range(args.perspectives):
                part = "train" if i < args.train_perspectives else "val"
                img = warp_crop(crop, rng)
                name = f"{key}_{i:02d}"
                imwrite(out / "images" / part / f"{name}.jpg", img)
                if is_defect:
                    # бокс = весь кроп с отступом 5% -> центр 0.5, сторона 0.9
                    label = (f"0 0.500000 0.500000 "
                             f"{1.0 - 2 * BOX_INSET:.6f} {1.0 - 2 * BOX_INSET:.6f}\n")
                else:
                    label = ""
                (out / "labels" / part / f"{name}.txt").parent.mkdir(parents=True, exist_ok=True)
                (out / "labels" / part / f"{name}.txt").write_text(label, encoding="utf-8")
                stats[part]["images"] += 1
                stats[part]["boxes"] += int(bool(label))
                stats[part][tag] += 1

    (out / "data.yaml").write_text(
        f"path: {out.as_posix()}\ntrain: images/train\nval: images/val\n"
        f"names:\n  0: corrosion\n", encoding="utf-8")
    report["split"] = stats
    report["per_component"] = per_component
    report["totals"] = {"images": stats["train"]["images"] + stats["val"]["images"],
                        "boxes": stats["train"]["boxes"] + stats["val"]["boxes"]}
    report["note"] = ("Разметка по факту 'дефектная доска': бокс на весь кроп зоны, "
                      "не попиксельная локализация коррозии.")
    (ROOT / "prepare_board_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"split": stats, "totals": report["totals"]}, indent=2,
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
