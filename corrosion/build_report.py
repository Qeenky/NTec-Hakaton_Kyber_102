"""Собирает единый corrosion/report.json из отчётов этапов.

    python build_report.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARTS = {
    "download": "data/download_report.json",
    "prepare": "prepare_report.json",
    "train": "train_report.json",
    "eval": "eval_report.json",
    "export": "export_report.json",
}


def load(name: str) -> dict | None:
    p = ROOT / PARTS[name]
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task": "детекция коррозии по узлам планшета (YOLO11n, 1 класс corrosion)",
    }
    for name in PARTS:
        report[name] = load(name)

    ev = report.get("eval") or {}
    tr = report.get("train") or {}
    summary = {
        "model": "yolo11n",
        "weights": tr.get("weights"),
        "train_minutes": tr.get("train_minutes"),
        "val_mAP50": (tr.get("val_metrics") or {}).get("mAP50"),
        "val_mAP50_95": (tr.get("val_metrics") or {}).get("mAP50_95"),
        "val_precision": (tr.get("val_metrics") or {}).get("precision"),
        "val_recall": (tr.get("val_metrics") or {}).get("recall"),
        "test_mAP50": ((ev.get("dataset") or {}).get("overall") or {}).get("mAP50"),
        "board_clean_false_positive_frames": (ev.get("boards") or {}).get("clean", {}).get(
            "frames_any_node_detected"),
        "board_clean_frames": (ev.get("boards") or {}).get("clean", {}).get("frames"),
        "board_defect_frames_with_detection": (ev.get("boards") or {}).get("defect", {}).get(
            "frames_any_node_detected"),
        "node_table": ev.get("node_table"),
    }
    report["summary"] = summary
    (ROOT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
