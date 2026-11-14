"""Синтетическая проверка точности AR-привязки (без камеры).

    python scripts/eval_synthetic.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import make_board  # noqa: E402
from ar_board import detect_board, load_layout, project_components, render_overlay  # noqa: E402
from ar_board.io_utils import imread, imwrite  # noqa: E402
from ar_board.synthetic import corner_errors, make_case  # noqa: E402

TARGET_DETECTION = 0.90
TARGET_REPROJ = 5.0
TARGET_CORNERS = 8.0


def ensure_board(out: Path) -> Path:
    """Генерирует планшет-плейсхолдер, если его ещё нет."""
    layout_path = out / "layout.json"
    if not (out / "board_A3.png").is_file() or not layout_path.is_file():
        make_board.generate_board(out, "PUMP-001")
        return layout_path
    layout = load_layout(layout_path)
    if not layout.get("components"):  # планшет с фото без разметки — пересоберём
        make_board.generate_board(out, layout.get("equipment_id", "PUMP-001"))
    return layout_path


def run(frames: int, seed: int, max_tilt: float, out: Path, save: bool) -> int:
    layout_path = ensure_board(out)
    layout = load_layout(layout_path)
    board = imread(out / "board_A3.png")
    if board is None:
        print(f"Не читается {out / 'board_A3.png'}", file=sys.stderr)
        return 2

    frame_size = (1280, 960)
    rng = np.random.default_rng(seed)
    rows: List[Dict[str, Any]] = []
    save_dir = out / "synthetic"
    if save:
        save_dir.mkdir(parents=True, exist_ok=True)

    print("Синтетика: 10 перспектив, наклон до 35°, масштаб 0.5–1.0, "
          "яркость/контраст/шум/JPEG\n")
    header = (f"{'#':>2}  {'наклон':>7}  {'масштаб':>7}  {'марк.':>5}  "
              f"{'репроекц':>9}  {'углы ср.':>8}  {'углы макс':>9}  статус")
    print(header)
    print("-" * len(header))

    for index in range(1, frames + 1):
        case = make_case(layout, board, frame_size, rng, max_tilt_deg=max_tilt,
                         scale_range=(0.5, 1.0))
        pose = detect_board(case["frame"], layout)
        detected = pose is not None and pose.is_valid
        row: Dict[str, Any] = {
            "index": index,
            "tilt": case["meta"]["tilt_deg"],
            "scale": case["meta"]["scale"],
            "detected": detected,
            "markers": len(pose.marker_ids) if pose else 0,
            "reproj": pose.reprojection_error if pose else float("nan"),
            "corner_mean": float("nan"),
            "corner_max": float("nan"),
        }
        if pose is not None:
            mean_err, max_err = corner_errors(layout, case["homography"], pose.homography)
            row["corner_mean"] = mean_err
            row["corner_max"] = max_err
        rows.append(row)

        if save:
            imwrite(save_dir / f"frame_{index:02d}.jpg", case["frame"],
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
            if pose is not None:
                overlay = render_overlay(case["frame"], project_components(pose, layout), pose)
                imwrite(save_dir / f"overlay_{index:02d}.jpg", overlay,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])

        status = "OK" if detected else "FAIL"
        print(f"{index:>2}  {row['tilt']:>6.1f}°  {row['scale']:>7.3f}  {row['markers']:>5}  "
              f"{row['reproj']:>8.2f}px  {row['corner_mean']:>7.2f}px  "
              f"{row['corner_max']:>8.2f}px  {status}")

    detected_rows = [r for r in rows if r["detected"]]
    detection_rate = len(detected_rows) / max(1, len(rows))
    mean_reproj = float(np.mean([r["reproj"] for r in detected_rows])) if detected_rows else float("inf")
    shallow = [r for r in detected_rows if r["tilt"] <= 25.0]
    mean_corners = float(np.mean([r["corner_mean"] for r in shallow])) if shallow else float("inf")

    blank = np.clip(rng.normal(128, 12, (frame_size[1], frame_size[0], 3)), 0, 255).astype(np.uint8)
    blank_ok = detect_board(blank, layout) is None

    print("\nИТОГИ")
    print(f"  детекция:              {len(detected_rows)}/{len(rows)} "
          f"({detection_rate * 100:.0f}%)   цель >= {TARGET_DETECTION * 100:.0f}%   "
          f"{'PASS' if detection_rate >= TARGET_DETECTION else 'FAIL'}")
    print(f"  ошибка репроекции:     {mean_reproj:.2f} px          "
          f"цель < {TARGET_REPROJ:.0f} px       "
          f"{'PASS' if mean_reproj < TARGET_REPROJ else 'FAIL'}")
    print(f"  углы зон (<=25°):      {mean_corners:.2f} px          "
          f"цель < {TARGET_CORNERS:.0f} px       "
          f"{'PASS' if mean_corners < TARGET_CORNERS else 'FAIL'}")
    print(f"  кадр без маркеров:     {'None' if blank_ok else 'ОШИБКА'}            "
          f"{'PASS' if blank_ok else 'FAIL'}")
    if shallow:
        print(f"  кадров с наклоном <=25°: {len(shallow)} из {len(rows)}")
    if save:
        print(f"  кадры для показа:      {save_dir}")

    ok = (detection_rate >= TARGET_DETECTION and mean_reproj < TARGET_REPROJ
          and mean_corners < TARGET_CORNERS and blank_ok)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синтетическая проверка ar_board.")
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--max-tilt", type=float, default=35.0)
    parser.add_argument("--out", default=str(ROOT / "out"))
    parser.add_argument("--no-save", action="store_true", help="не сохранять кадры")
    args = parser.parse_args(argv)
    return run(max(1, args.frames), args.seed, args.max_tilt,
               Path(args.out), not args.no_save)


if __name__ == "__main__":
    raise SystemExit(main())
