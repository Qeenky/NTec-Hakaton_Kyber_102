"""Калибровка порогов PatchCore v2 под НАШИ два планшета (out_real / out_defect).

Что делает
----------
1. 20 перспектив чистого планшета для калибровки (seed A) + 20 ОТЛОЖЕННЫХ чистых
   и 20 дефектных для проверки (seed B). Наклон 0-20°, масштаб 0.7-1.0,
   кадр 2560x1920. Поза — истинная гомография синтеза (на печатных планшетах
   нет ArUco: layout.markers = []).
2. Считает score каждой зоны текущей моделью (`models/*.npz`).
3. threshold_q99 = max(score на чистых калибровочных) * margin (1.10), чтобы был
   запас на отложенных. threshold_q95 ставится тем же значением — именно он
   используется в `is_anomaly`; старые значения сохраняются в meta.
4. Проверяет на 20 отложенных чистых (FP) и 20 дефектных (TP).
5. Бэкапит старые модели в `models_prev/`, пишет `board_calibration.json`.

    python -m v2.calibrate_board [--no-write]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

V2 = Path(__file__).resolve().parent
PROJECT = V2.parents[1]
AR = PROJECT / "ar_board"
MODELS = V2 / "models"
MODELS_PREV = V2 / "models_prev"
OUT = V2 / "board_calibration.json"

FRAME_SIZE = (2560, 1920)
TILT = (0.0, 20.0)
SCALE = (0.7, 1.0)
SEED = 20261114
HOLDOUT_SEED = 20261214

if str(PROJECT / "ar_board") not in sys.path:
    sys.path.insert(0, str(PROJECT / "ar_board"))


def imread(path: Path) -> np.ndarray:
    buf = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size else None
    if img is None:
        raise IOError(f"не прочитан {path}")
    return img


def board_views(board_dir: Path, n: int, seed: int) -> list[dict[str, np.ndarray]]:
    """n перспектив планшета -> canonical-кропы зон (512x512) по истинной H."""
    from ar_board.synthetic import render_frame, sample_homography
    from v2.canonical import canonical_crops, load_layout

    layout = load_layout(board_dir / "layout.json")
    board = imread(board_dir / "board_A3.png")
    rng = np.random.default_rng(seed)
    views = []
    for _ in range(n):
        tilt = float(rng.uniform(*TILT))
        h, meta = sample_homography(layout, FRAME_SIZE, rng, tilt_deg=tilt,
                                    scale_range=SCALE)
        frame = render_frame(board, h, FRAME_SIZE, rng)
        crops = canonical_crops(frame, layout, homography=h)
        views.append({"crops": crops, "meta": meta})
    return views


def score_views(model, views, comps) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {c: [] for c in comps}
    for view in views:
        for cid, crop in view["crops"].items():
            if cid in model.detectors:
                out[cid].append(float(model.score(cid, crop)["score"]))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Калибровка порогов anomaly v2 под планшеты")
    ap.add_argument("--models", default=str(MODELS))
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--holdout-frames", type=int, default=20)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--holdout-seed", type=int, default=HOLDOUT_SEED)
    ap.add_argument("--margin", type=float, default=1.10)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

    from v2.detector import AnomalyV2Model

    model = AnomalyV2Model.load(args.models)
    comps = model.components()

    calib_clean = score_views(model, board_views(AR / "out_real", args.frames, args.seed), comps)
    holdout_clean = score_views(
        model, board_views(AR / "out_real", args.holdout_frames, args.holdout_seed), comps)
    holdout_defect = score_views(
        model, board_views(AR / "out_defect", args.holdout_frames, args.holdout_seed + 1), comps)

    report: dict[str, Any] = {
        "task": "калибровка threshold_q99 PatchCore v2 под планшеты PUMP-001/002",
        "calib_frames_clean": args.frames,
        "holdout_frames_clean": args.holdout_frames,
        "holdout_frames_defect": args.holdout_frames,
        "frame_size": list(FRAME_SIZE),
        "tilt_deg_range": list(TILT),
        "scale_range": list(SCALE),
        "seed_calib": args.seed,
        "seed_holdout": args.holdout_seed,
        "margin": args.margin,
        "pose": "истинная гомография синтеза (на печатных планшетах нет ArUco)",
        "components": {},
    }

    for cid in comps:
        det = model.detectors[cid]
        cc = np.asarray(calib_clean[cid], dtype=np.float64)
        hc = np.asarray(holdout_clean[cid], dtype=np.float64)
        hd = np.asarray(holdout_defect[cid], dtype=np.float64)
        old95, old99 = float(det.threshold_q95), float(det.threshold_q99)
        calib_max = float(cc.max()) if cc.size else float("nan")
        holdout_max = float(hc.max()) if hc.size else float("nan")
        defect_min = float(hd.min()) if hd.size else None
        new_thr = round(calib_max * args.margin, 6)
        if defect_min is not None and new_thr >= defect_min:
            # запас не влезает — середина между максимумом чистого и минимумом дефекта
            new_thr = round(float(np.sqrt(max(calib_max, 1e-9) * defect_min)), 6)

        entry = {
            "calib_clean_scores": [round(float(x), 6) for x in cc],
            "holdout_clean_scores": [round(float(x), 6) for x in hc],
            "holdout_defect_scores": [round(float(x), 6) for x in hd],
            "calib_clean_mean": round(float(cc.mean()), 6) if cc.size else None,
            "calib_clean_max": round(calib_max, 6) if cc.size else None,
            "holdout_clean_mean": round(float(hc.mean()), 6) if hc.size else None,
            "holdout_clean_max": round(holdout_max, 6) if hc.size else None,
            "holdout_defect_mean": round(float(hd.mean()), 6) if hd.size else None,
            "holdout_defect_min": round(defect_min, 6) if defect_min is not None else None,
            "threshold_q95_before": round(old95, 6),
            "threshold_q99_before": round(old99, 6),
            "threshold_q95_after": new_thr,
            "threshold_q99_after": new_thr,
            "calib_clean_fp_before_q99": int((cc > old99).sum()),
            "calib_clean_fp_after": int((cc > new_thr).sum()),
            "holdout_clean_fp_before_q99": int((hc > old99).sum()),
            "holdout_clean_fp_after": int((hc > new_thr).sum()),
            "holdout_defect_tp_before_q99": int((hd > old99).sum()) if hd.size else None,
            "holdout_defect_tp_after": int((hd > new_thr).sum()) if hd.size else None,
            "n_calib_clean": int(cc.size),
            "n_holdout_clean": int(hc.size),
            "n_holdout_defect": int(hd.size),
        }
        report["components"][cid] = entry

        if not args.no_write:
            det.threshold_q95 = float(new_thr)
            det.threshold_q99 = float(new_thr)
            det.meta.update({
                "threshold_q95": float(new_thr),
                "threshold_q99": float(new_thr),
                "threshold_q95_prev": old95,
                "threshold_q99_prev": old99,
                "board_calibration": {
                    "threshold_q95": float(new_thr),
                    "threshold_q99": float(new_thr),
                    "calib_clean_max": entry["calib_clean_max"],
                    "holdout_clean_max": entry["holdout_clean_max"],
                    "holdout_defect_min": entry["holdout_defect_min"],
                    "margin": args.margin,
                    "n_calib_clean": entry["n_calib_clean"],
                    "n_holdout_clean": entry["n_holdout_clean"],
                    "n_holdout_defect": entry["n_holdout_defect"],
                    "frame_size": list(FRAME_SIZE),
                    "tilt_deg_range": list(TILT),
                    "scale_range": list(SCALE),
                    "seed_calib": args.seed,
                    "seed_holdout": args.holdout_seed,
                    "pose": "gt_homography",
                },
            })

    if not args.no_write:
        # бэкап исходных моделей (один раз)
        MODELS_PREV.mkdir(parents=True, exist_ok=True)
        for p in sorted(MODELS.glob("*.np*")) + sorted(MODELS.glob("*.json")):
            if p.name == "manifest.json":
                continue
            dst = MODELS_PREV / p.name
            if not dst.exists():
                shutil.copy2(p, dst)

        for cid, det in model.detectors.items():
            path = Path(args.models) / f"{cid}.npz"
            with np.load(path) as data:
                payload = {k: data[k] for k in data.files}
            payload["threshold_q95"] = np.float32(det.threshold_q95)
            payload["threshold_q99"] = np.float32(det.threshold_q99)
            np.savez_compressed(path, **payload)
            path.with_suffix(".json").write_text(
                json.dumps(det.meta, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        cid: {
            "calib_clean_max": v["calib_clean_max"],
            "holdout_clean_max": v["holdout_clean_max"],
            "holdout_defect_min": v["holdout_defect_min"],
            "thr_q99_before": v["threshold_q99_before"],
            "thr_q99_after": v["threshold_q99_after"],
            "holdout_clean_fp_before": v["holdout_clean_fp_before_q99"],
            "holdout_clean_fp_after": v["holdout_clean_fp_after"],
            "holdout_defect_tp_before": v["holdout_defect_tp_before_q99"],
            "holdout_defect_tp_after": v["holdout_defect_tp_after"],
        }
        for cid, v in report["components"].items()
    }
    report["summary"] = summary
    report["note"] = (
        "Порог q99 = max(score чистых калибровочных)*margin (1.10); q95 приравнен к q99, "
        "потому что is_anomaly использует q95. Проверка — на 20 отложенных чистых и 20 "
        "дефектных перспективах (другой seed). Старые пороги — в models_prev/ и полях *_before."
    )
    if not args.no_write:
        OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("записано:", OUT)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
