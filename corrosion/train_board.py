"""Дообучение детектора коррозии на кропах наших планшетов (dataset_board).

Старт: corrosion/runs/train/weights/best.pt (публичные макро-снимки стали).
Выход: corrosion/runs/finetune/weights/best.pt + models/corrosion_board.onnx.

    python train_board.py [--epochs 50] [--time-budget-min 60]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
os.environ.setdefault("MPLBACKEND", "Agg")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fine-tune YOLO11n на кропах планшетов")
    ap.add_argument("--data", default=str(ROOT / "dataset_board" / "data.yaml"))
    ap.add_argument("--model", default=str(ROOT / "runs" / "train" / "weights" / "best.pt"))
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="0")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--patience", type=int, default=20)
    ap.add_argument("--time-budget-min", type=float, default=60.0)
    ap.add_argument("--name", default="finetune")
    ap.add_argument("--seed", type=int, default=20261114)
    ap.add_argument("--export", action="store_true", default=True)
    ap.add_argument("--no-export", dest="export", action="store_false")
    args = ap.parse_args(argv)

    from ultralytics import YOLO

    model_path = Path(args.model)
    if not model_path.is_file():
        raise FileNotFoundError(f"стартовые веса не найдены: {model_path}")
    model = YOLO(str(model_path))

    t0 = time.time()
    stop_at = t0 + args.time_budget_min * 60

    def on_epoch_end(trainer):  # noqa: ANN001
        if time.time() > stop_at:
            print(f"[time-budget] {args.time_budget_min} мин — останавливаемся")
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
        # кропы уже с перспективой, но добавим немного вариативности
        hsv_h=0.010,
        hsv_s=0.4,
        hsv_v=0.30,
        degrees=10.0,
        perspective=0.0008,
        translate=0.08,
        scale=0.30,
        fliplr=0.5,
        flipud=0.2,
        mosaic=0.0,       # бокс = весь кадр, mosaic его ломает
        mixup=0.0,
        close_mosaic=0,
        plots=True,
        verbose=True,
    )
    elapsed = time.time() - t0
    best = ROOT / "runs" / args.name / "weights" / "best.pt"

    metrics: dict = {}
    try:
        m = YOLO(str(best)).val(data=args.data, split="val", imgsz=args.imgsz,
                                device=args.device, plots=False, verbose=False)
        box = m.box
        metrics = {
            "mAP50": float(box.map50),
            "mAP50_95": float(box.map),
            "precision": float(box.mp),
            "recall": float(box.mr),
        }
    except Exception as exc:  # noqa: BLE001
        metrics = {"error": repr(exc)[:300]}

    onnx_path = None
    if args.export:
        try:
            src = Path(YOLO(str(best)).export(format="onnx", imgsz=args.imgsz,
                                             opset=17, simplify=True))
            dst = ROOT / "models" / "corrosion_board.onnx"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            onnx_path = str(dst)
        except Exception as exc:  # noqa: BLE001
            onnx_path = f"export error: {repr(exc)[:200]}"

    report = {
        "task": "fine-tune YOLO11n на кропах планшетов (разметка по доске)",
        "start_weights": str(model_path),
        "weights": str(best),
        "onnx": onnx_path,
        "data": args.data,
        "params": {k: v for k, v in vars(args).items()},
        "train_seconds": round(elapsed, 1),
        "train_minutes": round(elapsed / 60.0, 2),
        "epochs_done": int(getattr(results, "epoch", args.epochs - 1) + 1),
        "val_metrics": metrics,
        "caveat": ("Разметка дообучения — 'дефектная доска' (бокс на весь кроп зоны), "
                   "не попиксельная локализация коррозии."),
    }
    (ROOT / "train_board_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
