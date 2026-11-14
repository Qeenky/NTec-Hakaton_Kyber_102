"""Экспорт обученного детектора в ONNX (CPU/GPU на сервере).

    python export.py [--weights runs/train/weights/best.pt] [--imgsz 640] [--dynamic]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
os.environ.setdefault("MPLBACKEND", "Agg")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Экспорт YOLO11n-коррозии в ONNX")
    ap.add_argument("--weights", default=str(ROOT / "runs" / "train" / "weights" / "best.pt"))
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--dynamic", action="store_true", help="динамические оси (batch/H/W)")
    ap.add_argument("--half", action="store_true", help="FP16 (только для GPU)")
    args = ap.parse_args(argv)

    from ultralytics import YOLO

    model = YOLO(args.weights)
    path = model.export(format="onnx", imgsz=args.imgsz, opset=args.opset,
                        dynamic=args.dynamic, half=args.half, simplify=True)
    src = Path(path)
    dst_dir = ROOT / "models"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"corrosion_yolo11n_{args.imgsz}.onnx"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)

    report = {"onnx": str(dst), "imgsz": args.imgsz, "opset": args.opset,
              "dynamic": args.dynamic, "half": args.half,
              "size_mb": round(dst.stat().st_size / 1e6, 2)}

    # быстрая проверка, что граф читается и считает
    try:
        import numpy as np
        import onnxruntime as ort

        providers = ort.get_available_providers()
        sess = ort.InferenceSession(str(dst), providers=["CPUExecutionProvider"])
        inp = sess.get_inputs()[0]
        dummy = np.zeros((1, 3, args.imgsz, args.imgsz), dtype=np.float32)
        out = sess.run(None, {inp.name: dummy})[0]
        report.update({"onnxruntime": True, "providers": providers,
                       "input": list(inp.shape),
                       "output_shape": list(out.shape)})
    except Exception as exc:  # noqa: BLE001
        report.update({"onnxruntime": False, "error": repr(exc)[:300]})

    (ROOT / "export_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
