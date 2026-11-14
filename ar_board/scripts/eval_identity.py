"""Проверка самоидентификации планшетов по identity-маркеру.

    python scripts/eval_identity.py

Для каждого планшета из boards.json строится 10 синтетических перспектив,
проверяется: identity-маркер найден, id определён верно, ошибка репроекции
< 1 px, реестр identify_board() вернул правильный board_id. Для чистого
планшета дополнительно проверяется, что маркер id 4 лежит внутри полигона
coupling_guard.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ar_board import (detect_board, identify_board, load_boards,  # noqa: E402
                      load_layout)
from ar_board.io_utils import imread, imwrite  # noqa: E402
from ar_board.layout import point_in_polygon, polygon_centroid  # noqa: E402
from ar_board.synthetic import corner_errors, make_case  # noqa: E402

TARGET_REPROJ = 1.0


def _component_polygon(layout: Dict[str, Any], component_id: str) -> Optional[list]:
    for comp in layout.get("components", []):
        if comp.get("component_id") == component_id:
            return [[float(x), float(y)] for x, y in comp.get("polygon_px", [])]
    return None


def _guard_check(layout: Dict[str, Any]) -> Dict[str, Any]:
    """Геометрическая проверка маркера id 4 внутри coupling_guard."""
    guard = next((m for m in layout.get("markers", []) if m.get("role") == "guard"), None)
    polygon = _component_polygon(layout, "coupling_guard")
    if guard is None or polygon is None:
        return {"available": False}
    corners = [[float(x), float(y)] for x, y in guard["corners_px"]]
    center = polygon_centroid(corners)
    inside = [point_in_polygon(point, polygon) for point in corners + [center]]
    return {
        "available": True,
        "marker_id": int(guard["id"]),
        "inside": all(inside),
        "corners_inside": sum(inside[:4]),
        "center": center,
        "polygon": polygon,
    }


def run(frames: int, seed: int, max_tilt: float, out: Path, save: bool) -> int:
    boards = load_boards(ROOT / "boards.json")
    frame_size = (1280, 960)
    rows: List[Dict[str, Any]] = []
    guard_results: Dict[str, Dict[str, Any]] = {}

    for board_index, entry in enumerate(boards):
        layout = load_layout(entry.layout_path)
        board_image = imread(entry.layout_path.parent / layout["board"]["image"])
        if board_image is None:
            print(f"Не читается планшет {entry.board_id}", file=sys.stderr)
            return 2

        guard_results[entry.board_id] = _guard_check(layout)
        rng = np.random.default_rng(seed + board_index)
        save_dir = out / entry.board_id
        if save:
            save_dir.mkdir(parents=True, exist_ok=True)

        print(f"\nПланшет {entry.board_id}: identity id {entry.identity_marker_id}, "
              f"layout {entry.layout_path.relative_to(ROOT)}")
        header = (f"{'#':>2}  {'наклон':>7}  {'масштаб':>7}  {'identity':>8}  "
                  f"{'реестр':>7}  {'репроекц':>9}  {'углы ср.':>8}  статус")
        print(header)
        print("-" * len(header))

        for index in range(1, frames + 1):
            case = make_case(layout, board_image, frame_size, rng,
                             max_tilt_deg=max_tilt, scale_range=(0.5, 1.0))
            pose = detect_board(case["frame"], layout)
            ident = identify_board(case["frame"], boards)
            identity_ok = (pose is not None
                           and pose.identity_marker_id == entry.identity_marker_id)
            registry_ok = ident is not None and ident.board_id == entry.board_id
            reproj = pose.reprojection_error if pose is not None else float("nan")
            corner_mean, corner_max = (corner_errors(layout, case["homography"],
                                                     pose.homography)
                                       if pose is not None else (float("nan"),) * 2)
            row = {
                "board_id": entry.board_id,
                "identity_marker_id": entry.identity_marker_id,
                "tilt": case["meta"]["tilt_deg"],
                "scale": case["meta"]["scale"],
                "identity_ok": identity_ok,
                "registry_ok": registry_ok,
                "reproj": reproj,
                "corner_mean": corner_mean,
                "corner_max": corner_max,
            }
            rows.append(row)

            if save:
                imwrite(save_dir / f"frame_{index:02d}.jpg", case["frame"],
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
                if pose is not None:
                    from ar_board import project_components, render_overlay
                    overlay = render_overlay(case["frame"],
                                            project_components(pose, layout), pose)
                    imwrite(save_dir / f"overlay_{index:02d}.jpg", overlay,
                            [cv2.IMWRITE_JPEG_QUALITY, 92])

            status = "OK" if identity_ok and registry_ok and reproj < TARGET_REPROJ else "FAIL"
            print(f"{index:>2}  {row['tilt']:>6.1f}°  {row['scale']:>7.3f}  "
                  f"{'id ' + str(pose.identity_marker_id) if pose and pose.identity_marker_id is not None else '—':>8}  "
                  f"{ident.board_id if ident else '—':>7}  {reproj:>8.2f}px  "
                  f"{corner_mean:>7.2f}px  {status}")

    print("\nИТОГИ")
    total = len(rows)
    identity_hits = sum(1 for row in rows if row["identity_ok"])
    registry_hits = sum(1 for row in rows if row["registry_ok"])
    errors = [row["reproj"] for row in rows if row["identity_ok"]]
    mean_error = float(np.mean(errors)) if errors else float("inf")
    max_error = float(np.max(errors)) if errors else float("inf")

    identity_pass = identity_hits == total
    registry_pass = registry_hits == total
    error_pass = mean_error < TARGET_REPROJ and max_error < TARGET_REPROJ

    for entry in boards:
        subset = [row for row in rows if row["board_id"] == entry.board_id]
        hits = sum(1 for row in subset if row["identity_ok"])
        correct = sum(1 for row in subset if row["registry_ok"])
        print(f"  {entry.board_id}: identity {hits}/{len(subset)}, "
              f"id {correct}/{len(subset)}")
    print(f"  identity найден:        {identity_hits}/{total}              "
          f"{'PASS' if identity_pass else 'FAIL'}")
    print(f"  id определён верно:    {registry_hits}/{total}              "
          f"{'PASS' if registry_pass else 'FAIL'}")
    print(f"  ошибка репроекции:     {mean_error:.2f} px (макс {max_error:.2f})  "
          f"цель < {TARGET_REPROJ:.0f} px  {'PASS' if error_pass else 'FAIL'}")

    guard_pass = True
    for board_id, result in guard_results.items():
        if not result.get("available"):
            continue
        inside_text = "да" if result["inside"] else "НЕТ"
        print(f"  guard id {result['marker_id']} в coupling_guard ({board_id}): "
              f"{inside_text}, вершин внутри {result['corners_inside']}/4")
        guard_pass = guard_pass and bool(result["inside"])
    if not any(r.get("available") for r in guard_results.values()):
        print("  guard-маркер:          нет в реестре (проверка пропущена)")

    if save:
        print(f"  кадры:                 {out}")
    return 0 if (identity_pass and registry_pass and error_pass and guard_pass) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка идентификации планшетов.")
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261114)
    parser.add_argument("--max-tilt", type=float, default=35.0)
    parser.add_argument("--out", default=str(ROOT / "out_identity"))
    parser.add_argument("--no-save", action="store_true", help="не сохранять кадры")
    args = parser.parse_args(argv)
    return run(max(1, args.frames), args.seed, args.max_tilt,
               Path(args.out), not args.no_save)


if __name__ == "__main__":
    raise SystemExit(main())
