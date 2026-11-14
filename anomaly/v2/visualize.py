"""Визуализации v2/samples: кроп | heatmap | маска | наложение.

Запуск:
    python -m v2.visualize
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

from .canonical import CROP_SIZE, imread, imwrite
from .detector import AnomalyV2Model, overlay_heatmap
from .synth_defects import load_dataset

V2 = Path(__file__).resolve().parent
DATA = V2 / "data"
MODELS = V2 / "models"
SAMPLES = V2 / "samples"

# 8 показательных примеров: узел, тип дефекта
CASES: Sequence[tuple[str, str]] = (
    ("motor", "corrosion"),
    ("motor", "crack"),
    ("pump", "corrosion"),
    ("pump", "oil_leak"),
    ("coupling_guard", "missing_guard"),
    ("coupling_guard", "crack"),
    ("flange", "erosion"),
    ("pump", "erosion"),
)


def _label(img: np.ndarray, text: str, color=(255, 255, 255)) -> np.ndarray:
    out = img.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 26), (24, 24, 24), -1)
    cv2.putText(out, text, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


def _heat_to_bgr(heat: np.ndarray, size: int = CROP_SIZE) -> np.ndarray:
    hm = cv2.resize(heat, (size, size), interpolation=cv2.INTER_LINEAR)
    lo, hi = float(np.percentile(hm, 50)), float(np.percentile(hm, 99.5))
    hm = np.clip((hm - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    return cv2.applyColorMap((hm * 255).astype(np.uint8), cv2.COLORMAP_JET)


def _mask_to_bgr(mask: np.ndarray) -> np.ndarray:
    m = (mask > 127).astype(np.uint8) * 255
    return cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)


def make_samples(force: bool = False) -> List[str]:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    model = AnomalyV2Model.load(MODELS)
    data = load_dataset(DATA)
    written: List[str] = []

    for i, (comp, dtype) in enumerate(CASES):
        det = model.detectors.get(comp)
        if det is None:
            continue
        candidates = [
            r
            for r in data["records"]
            if r["component"] == comp
            and r["kind"] == "defect"
            and r.get("defect_type") == dtype
        ]
        if not candidates:
            continue
        # для витрины берём дефект ближе к центру кропа (не у самой кромки)
        def centrality(rec: Dict[str, Any]) -> float:
            m = imread(DATA / "mask" / comp / f"{rec['id']}.png", cv2.IMREAD_GRAYSCALE)
            ys, xs = np.where(m > 127)
            if len(xs) == 0:
                return -1.0
            cx, cy = float(xs.mean()), float(ys.mean())
            return min(cx, cy, CROP_SIZE - cx, CROP_SIZE - cy)

        rec = max(candidates, key=centrality)
        sid = rec["id"]
        crop = imread(DATA / "defect" / comp / f"{sid}.png")
        mask = imread(DATA / "mask" / comp / f"{sid}.png", cv2.IMREAD_GRAYSCALE)
        clean = imread(DATA / "clean" / comp / f"{rec['base_id']}.png")

        r = det.score(crop)
        r_clean = det.score(clean)
        heat_bgr = _heat_to_bgr(r["heatmap"])
        over = overlay_heatmap(crop, r["heatmap"], alpha=0.55)
        mask_bgr = _mask_to_bgr(mask)
        mask_on = crop.copy()
        mask_on[mask > 127] = (
            0.45 * mask_on[mask > 127] + 0.55 * np.array([0, 255, 0])
        ).astype(np.uint8)

        panels = [
            _label(clean, f"clean crop  score={r_clean['score']:.2f}"),
            _label(crop, f"defect: {dtype}  score={r['score']:.2f}"),
            _label(heat_bgr, "heatmap (JET)"),
            _label(mask_bgr, "defect mask (GT)"),
            _label(over, "overlay"),
            _label(mask_on, "mask on crop"),
        ]
        grid = np.vstack(
            [np.hstack(panels[:3]), np.hstack(panels[3:])]
        )
        path = SAMPLES / f"sample_{i+1:02d}_{comp}_{dtype}.png"
        imwrite(path, grid)
        written.append(str(path))

    # сводная таблица порогов
    lines = ["узел | порог q95 | порог q99 | top-k | патчей в банке"]
    for c, d in model.detectors.items():
        lines.append(
            f"{c} | {d.threshold_q95:.3f} | {d.threshold_q99:.3f} | {d.topk} | {len(d.bank)}"
        )
    (SAMPLES / "thresholds.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


def main() -> int:
    written = make_samples()
    for p in written:
        print(f"  {p}")
    print(f"готово: {len(written)} визуализаций в {SAMPLES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
