"""Оценка детектора коррозии на отложенных перспективах двух планшетов.

20 кадров чистого (out_real) и 20 дефектного (out_defect), наклон 0-20°, масштаб 0.7-1.3.
Кадры генерируются один раз и подаются ОБОИМ моделям (старой и новой) — сравнение честное.

    python eval_board.py [--weights runs/finetune/weights/best.pt]
                         [--baseline runs/train/weights/best.pt] [--frames 20]

Метрики:
  clean_false_positive_rate  — доля кадров, где сработал хотя бы один узел (ложные);
  defect_detection_rate      — доля кадров, где сработал хотя бы один узел (верные).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "ar_board"))

SEED = 20261114
FRAME_SIZE = (1280, 960)
TILT = (0.0, 20.0)
SCALE = (0.7, 1.3)


def imread(path: Path) -> np.ndarray:
    buf = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size else None
    if img is None:
        raise IOError(f"не прочитан {path}")
    return img


def make_frames(board_dir: Path, n: int, seed: int) -> list[dict]:
    """Кадры с ИЗВЕСТНОЙ гомографией.

    На печатных планшетах out_real/out_defect нет ArUco (layout.markers = []),
    поэтому поза берётся из синтеза (она же — GT). Это изолирует качество
    детектора от ошибки AR-выравнивания (~0.2 px в проде).
    """
    from ar_board import load_layout
    from ar_board.synthetic import render_frame, sample_homography

    layout = load_layout(board_dir / "layout.json")
    board = imread(board_dir / "board_A3.png")
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(n):
        tilt = float(rng.uniform(*TILT))
        h, meta = sample_homography(layout, FRAME_SIZE, rng, tilt_deg=tilt,
                                    scale_range=SCALE)
        frame = render_frame(board, h, FRAME_SIZE, rng)
        polys = {}
        for comp in layout.get("components", []):
            poly = comp.get("polygon_px") or []
            if len(poly) >= 3:
                arr = cv2.perspectiveTransform(
                    np.asarray(poly, dtype=np.float64).reshape(-1, 1, 2), h)
                polys[str(comp["component_id"])] = arr.reshape(-1, 2).astype(np.float32)
        frames.append({"frame": frame, "polys": polys, "meta": meta, "pose_ok": True})
    return frames


def run_model(detector, frames: list[dict]) -> dict:
    per_node: dict[str, list[float]] = defaultdict(list)
    per_node_hits: dict[str, int] = defaultdict(int)
    rows = []
    for i, item in enumerate(frames, 1):
        res = detector.detect(item["frame"], item["polys"]) if item["polys"] else {}
        row = {"frame": i, "tilt": item["meta"]["tilt_deg"],
               "scale": item["meta"]["scale"], "pose_ok": item["pose_ok"],
               "nodes": {}}
        for cid, r in res.items():
            per_node[cid].append(r["area_pct"])
            per_node_hits[cid] += int(r["detected"])
            row["nodes"][cid] = {"area_pct": r["area_pct"], "detected": r["detected"],
                                 "conf": r["confidence"], "n_boxes": len(r["boxes"])}
        row["any_detected"] = any(v["detected"] for v in row["nodes"].values())
        rows.append(row)

    n = len(frames)
    return {
        "frames": n,
        "pose_ok": sum(1 for r in rows if r["pose_ok"]),
        "frames_any_detected": sum(1 for r in rows if r["any_detected"]),
        "frames_any_detected_frac": round(sum(1 for r in rows if r["any_detected"]) / n, 4),
        "by_node": {
            cid: {
                "frames_detected": per_node_hits[cid],
                "frames": len(v),
                "detected_frac": round(per_node_hits[cid] / max(len(v), 1), 4),
                "mean_area_pct": round(float(np.mean(v)), 2),
                "max_area_pct": round(float(np.max(v)), 2),
            } for cid, v in per_node.items()
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Board eval: старая vs новая модель коррозии")
    ap.add_argument("--weights", default=str(ROOT / "runs" / "finetune" / "weights" / "best.pt"))
    ap.add_argument("--baseline", default=str(ROOT / "runs" / "train" / "weights" / "best.pt"))
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default="0")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", default=str(ROOT / "board_eval_report.json"))
    args = ap.parse_args(argv)

    from adapter import CorrosionDetector

    models = {}
    for name, path in (("baseline", args.baseline), ("finetuned", args.weights)):
        if not Path(path).is_file():
            models[name] = {"error": f"нет весов: {path}"}
            continue
        models[name] = CorrosionDetector(path, conf=args.conf, imgsz=args.imgsz,
                                         device=args.device)

    report: dict = {
        "task": "детектор коррозии на отложенных перспективах планшетов",
        "seed": args.seed,
        "frames_per_board": args.frames,
        "frame_size": list(FRAME_SIZE),
        "tilt_deg_range": list(TILT),
        "scale_range": list(SCALE),
        "conf": args.conf,
        "weights": {"baseline": args.baseline, "finetuned": args.weights},
        "boards": {},
    }

    for tag, bdir in (("clean", REPO / "ar_board" / "out_real"),
                      ("defect", REPO / "ar_board" / "out_defect")):
        # один и тот же сид для обоих планшетов, кадры создаются один раз
        frames = make_frames(bdir, args.frames, args.seed + (0 if tag == "clean" else 1))
        report["boards"][tag] = {"dir": str(bdir), "models": {}}
        for name, det in models.items():
            if isinstance(det, dict):
                report["boards"][tag]["models"][name] = det
                continue
            report["boards"][tag]["models"][name] = run_model(det, frames)
        b = report["boards"][tag]["models"]
        print(f"{tag}: baseline any={b.get('baseline', {}).get('frames_any_detected')}"
              f"/{args.frames}, finetuned any={b.get('finetuned', {}).get('frames_any_detected')}"
              f"/{args.frames}")

    # сводка "до/после"
    summary = {}
    for name in ("baseline", "finetuned"):
        clean = report["boards"]["clean"]["models"].get(name, {})
        defect = report["boards"]["defect"]["models"].get(name, {})
        summary[name] = {
            "clean_false_positive_rate": clean.get("frames_any_detected_frac"),
            "defect_detection_rate": defect.get("frames_any_detected_frac"),
            "clean_frames_any": clean.get("frames_any_detected"),
            "defect_frames_any": defect.get("frames_any_detected"),
            "frames": args.frames,
        }
    report["summary"] = summary
    report["caveat"] = ("Перспективы синтетические (ar_board.synthetic), планшеты — "
                        "наши отпечатки. Метрика кадровая, не попиксельная.")
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("отчёт:", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
