"""Обучение детектора коррозии YOLO11n на объединённом датасете.

    python train.py [--epochs 80] [--batch 12] [--device 0] [--time-budget-min 55]

Пишет:
  runs/train/            — чекпойнты и кривые ultralytics
  train_report.json      — итоговые метрики на val + время обучения
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# конфиг/веса ultralytics — на D:, чтобы не засорять C:
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
os.environ.setdefault("MPLBACKEND", "Agg")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="YOLO11n для детекции коррозии")
    ap.add_argument("--data", default=str(ROOT / "dataset" / "data.yaml"))
    ap.add_argument("--model", default=str(ROOT / "weights" / "yolo11n.pt"))
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--device", default="0")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--patience", type=int, default=15)
    ap.add_argument("--time-budget-min", type=float, default=55.0)
    ap.add_argument("--name", default="train")
    ap.add_argument("--seed", type=int, default=20261113)
    args = ap.parse_args(argv)

    from ultralytics import YOLO

    model_path = Path(args.model)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(model_path) if model_path.is_file() else "yolo11n.pt")
    if not model_path.is_file():
        # ultralytics скачал веса в cwd — перенесём в weights/
        import shutil
        for cand in (Path.cwd() / "yolo11n.pt", ROOT / "yolo11n.pt"):
            if cand.is_file():
                shutil.move(str(cand), str(model_path))
                break

    t0 = time.time()
    stop_at = t0 + args.time_budget_min * 60

    def on_epoch_end(trainer):  # noqa: ANN001
        if time.time() > stop_at:
            print(f"[time-budget] {args.time_budget_min} мин исчерпано — останавливаемся")
            trainer.epochs = trainer.epoch + 1

    model.add_callback("on_train_epoch_end", on_epoch_end)

    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        patience=args.patience,
        seed=args.seed,
        project=str(ROOT / "runs"),
        name=args.name,
        exist_ok=True,
        pretrained=True,
        optimizer="auto",
        cos_lr=True,
        close_mosaic=10,
        # коррозия — это в первую очередь цвет/текстура: не ломаем оттенки
        hsv_h=0.010,
        hsv_s=0.5,
        hsv_v=0.35,
        degrees=8.0,
        translate=0.10,
        scale=0.45,
        fliplr=0.5,
        flipud=0.2,
        mosaic=0.8,
        mixup=0.0,
        plots=True,
        verbose=True,
    )
    elapsed = time.time() - t0

    best = ROOT / "runs" / args.name / "weights" / "best.pt"
    metrics = {}
    try:
        m = YOLO(str(best)).val(data=args.data, split="val", imgsz=args.imgsz,
                                device=args.device, plots=False, verbose=False)
        names = m.names
        box = m.box
        metrics = {
            "mAP50": float(box.map50),
            "mAP50_95": float(box.map),
            "precision": float(box.mp),
            "recall": float(box.mr),
            "per_class": {
                names.get(i, str(i)): {
                    "mAP50_95": float(box.maps[i]) if box.maps is not None else None,
                    "precision": float(box.p[i]) if box.p is not None else None,
                    "recall": float(box.r[i]) if box.r is not None else None,
                } for i in range(len(box.ap_class_index))
            },
        }
    except Exception as exc:  # noqa: BLE001
        metrics = {"error": repr(exc)[:300]}

    report = {
        "model": "yolo11n",
        "weights": str(best),
        "data": args.data,
        "params": {k: v for k, v in vars(args).items()},
        "train_seconds": round(elapsed, 1),
        "train_minutes": round(elapsed / 60.0, 2),
        "epochs_done": int(getattr(results, "epoch", args.epochs - 1) + 1),
        "val_metrics": metrics,
        "hardware": os.environ.get("COMPUTERNAME", ""),
    }
    (ROOT / "train_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
