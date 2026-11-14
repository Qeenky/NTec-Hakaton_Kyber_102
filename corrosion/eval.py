"""Честная оценка детектора коррозии.

1. Метрики на отложенном тесте (split=test), отдельно по каждому источнику.
2. Тест на нашем планшете: 10 синтетических перспектив чистого и дефектного
   планшета; ложные срабатывания на чистом, площадь поражения по узлам.
3. Визуализации в samples/.

    python eval.py [--weights runs/train/weights/best.pt] [--conf 0.25]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "ar_board"))

FRAMES = 10
SEED = 20261113
CONF = 0.25


def imread(path: Path) -> np.ndarray | None:
    try:
        buf = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size else None
    except Exception:  # noqa: BLE001
        return None


def imwrite(path: Path, img: np.ndarray, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if ok:
        buf.tofile(str(path))


# 1. метрики на отложенном тесте
def dataset_metrics(model, imgsz: int, device: str) -> dict:
    """Общие метрики + матрица по источникам (папки test разложены по префиксу)."""
    data = ROOT / "dataset"
    yaml = data / "data.yaml"
    out: dict = {"overall": {}, "by_source": {}}

    m = model.val(data=str(yaml), split="test", imgsz=imgsz, device=device,
                  plots=False, verbose=False)
    out["overall"] = {
        "mAP50": round(float(m.box.map50), 4),
        "mAP50_95": round(float(m.box.map), 4),
        "precision": round(float(m.box.mp), 4),
        "recall": round(float(m.box.mr), 4),
        "n_images": len(list((data / "images" / "test").glob("*"))),
        "n_boxes": int(sum(1 for f in (data / "labels" / "test").glob("*.txt")
                           for _ in f.read_text(encoding="utf-8").splitlines())),
    }

    # по источникам: пересобираем подпапки через жёсткие ссылки
    tmp = data / "_persource"
    if tmp.exists():
        shutil.rmtree(tmp)
    for part in ("test",):
        for img in (data / "images" / part).glob("*.jpg"):
            src = img.name.split("__")[0]
            d_img = tmp / src / "images"
            d_lbl = tmp / src / "labels"
            d_img.mkdir(parents=True, exist_ok=True)
            d_lbl.mkdir(parents=True, exist_ok=True)
            try:
                os.link(img, d_img / img.name)
            except OSError:
                shutil.copy2(img, d_img / img.name)
            lbl = data / "labels" / part / (img.stem + ".txt")
            if lbl.is_file():
                shutil.copy2(lbl, d_lbl / lbl.name)
    for src_dir in sorted(tmp.iterdir()):
        y = tmp / f"{src_dir.name}.yaml"
        y.write_text(f"path: {tmp.as_posix()}\ntrain: {src_dir.name}/images\n"
                     f"val: {src_dir.name}/images\nnames:\n  0: corrosion\n",
                     encoding="utf-8")
        n_img = len(list((src_dir / "images").glob("*.jpg")))
        n_box = sum(1 for f in (src_dir / "labels").glob("*.txt")
                    for _ in f.read_text(encoding="utf-8").splitlines())
        try:
            mm = model.val(data=str(y), split="val", imgsz=imgsz, device=device,
                           plots=False, verbose=False)
            out["by_source"][src_dir.name] = {
                "images": n_img, "boxes": n_box,
                "mAP50": round(float(mm.box.map50), 4),
                "mAP50_95": round(float(mm.box.map), 4),
                "precision": round(float(mm.box.mp), 4),
                "recall": round(float(mm.box.mr), 4),
            }
        except Exception as exc:  # noqa: BLE001
            out["by_source"][src_dir.name] = {"images": n_img, "boxes": n_box,
                                              "error": repr(exc)[:200]}
    shutil.rmtree(tmp, ignore_errors=True)
    return out


# 2. планшет
def board_eval(detector, board_dir: str, frames: int = FRAMES, seed: int = SEED) -> dict:
    """10 перспектив планшета: площади по узлам + ложные срабатывания."""
    from ar_board import detect_board, load_layout, project_components
    from ar_board.synthetic import make_case

    base = ROOT.parent / "ar_board" / board_dir
    layout = load_layout(base / "layout.json")
    board = imread(base / "board_A3.png")
    if board is None:
        return {"error": f"не читается {base / 'board_A3.png'}"}

    rng = np.random.default_rng(seed)
    per_node: dict[str, list[float]] = defaultdict(list)
    per_node_frames: dict[str, int] = defaultdict(int)
    rows = []
    for i in range(1, frames + 1):
        case = make_case(layout, board, (1280, 960), rng, max_tilt_deg=35.0,
                         scale_range=(0.5, 1.0))
        frame = case["frame"]
        pose = detect_board(frame, layout)
        projected = project_components(pose, layout) if pose is not None else []
        polys = {p["component_id"]: p["polygon"] for p in projected if p.get("polygon")}
        res = detector.detect(frame, polys) if polys else {}
        boxes_all = detector.predict_boxes(frame)
        row = {"frame": i, "tilt": case["meta"]["tilt_deg"], "scale": case["meta"]["scale"],
               "pose_ok": bool(pose is not None and pose.is_valid),
               "markers": len(pose.marker_ids) if pose else 0,
               "n_boxes_frame": int(len(boxes_all)),
               "max_conf_frame": round(float(boxes_all[:, 4].max()), 3) if len(boxes_all) else 0.0,
               "nodes": {}}
        for cid, r in res.items():
            per_node[cid].append(r["area_pct"])
            if r["detected"]:
                per_node_frames[cid] += 1
            row["nodes"][cid] = {"area_pct": r["area_pct"], "detected": r["detected"],
                                 "conf": r["confidence"], "n_boxes": len(r["boxes"])}
        row["any_node_detected"] = any(v["detected"] for v in row["nodes"].values())
        rows.append(row)

    summary = {
        "board": board_dir,
        "frames": frames,
        "pose_ok": sum(1 for r in rows if r["pose_ok"]),
        "frames_any_node_detected": sum(1 for r in rows if r["any_node_detected"]),
        "frames_any_box": sum(1 for r in rows if r["n_boxes_frame"] > 0),
        "by_node": {
            cid: {
                "mean_area_pct": round(float(np.mean(v)), 2),
                "max_area_pct": round(float(np.max(v)), 2),
                "frames_detected": per_node_frames.get(cid, 0),
                "frames": len(v),
            } for cid, v in per_node.items()
        },
        "rows": rows,
    }
    return {"summary": summary, "layout": layout}


# 3. визуализации
def draw_boxes(img: np.ndarray, boxes: np.ndarray, color=(0, 90, 255)) -> np.ndarray:
    out = img.copy()
    for x1, y1, x2, y2, c in boxes:
        p1 = (int(x1), int(y1))
        p2 = (int(x2), int(y2))
        cv2.rectangle(out, p1, p2, (10, 12, 16), 4, cv2.LINE_AA)
        cv2.rectangle(out, p1, p2, color, 2, cv2.LINE_AA)
        cv2.putText(out, f"{c:.2f}", (p1[0] + 2, max(12, p1[1] - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    return out


def draw_nodes(img: np.ndarray, res: dict, polys: dict) -> np.ndarray:
    out = img.copy()
    for cid, poly in polys.items():
        pts = np.asarray(poly, dtype=np.int32).reshape(-1, 1, 2)
        r = res.get(cid, {})
        hot = bool(r.get("detected"))
        color = (40, 60, 240) if hot else (90, 200, 120)
        for b in r.get("boxes", []):
            x1, y1, x2, y2 = b["box"]
            cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), (0, 140, 255), 2,
                          cv2.LINE_AA)
        cv2.polylines(out, [pts], True, (10, 12, 16), 4, cv2.LINE_AA)
        cv2.polylines(out, [pts], True, color, 2, cv2.LINE_AA)
        x, y = pts[0][0]
        label = f"{cid} {r.get('area_pct', 0.0):.1f}%"
        cv2.putText(out, label, (int(x) + 4, int(y) + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (10, 12, 16), 3, cv2.LINE_AA)
        cv2.putText(out, label, (int(x) + 4, int(y) + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Оценка детектора коррозии")
    ap.add_argument("--weights", default=str(ROOT / "runs" / "train" / "weights" / "best.pt"))
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default="0")
    ap.add_argument("--conf", type=float, default=CONF)
    ap.add_argument("--frames", type=int, default=FRAMES)
    ap.add_argument("--skip-dataset", action="store_true")
    args = ap.parse_args(argv)

    from ultralytics import YOLO
    from adapter import CorrosionDetector

    model = YOLO(args.weights)
    detector = CorrosionDetector(args.weights, conf=args.conf, imgsz=args.imgsz,
                                 device=args.device)
    report: dict = {"weights": args.weights, "conf": args.conf, "imgsz": args.imgsz,
                    "seed": SEED, "frames_per_board": args.frames}

    if not args.skip_dataset:
        print("== метрики на отложенном тесте ==")
        report["dataset"] = dataset_metrics(model, args.imgsz, args.device)
        print(json.dumps(report["dataset"], indent=2, ensure_ascii=False))

    print("== планшет: чистый и дефектный ==")
    samples = ROOT / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    boards = {}
    for tag, d in (("clean", "out_real"), ("defect", "out_defect")):
        res = board_eval(detector, d, args.frames)
        if "error" in res:
            boards[tag] = res
            continue
        boards[tag] = res["summary"]
        print(f"  {tag}: узлы детектированы в {res['summary']['frames_any_node_detected']}"
              f"/{res['summary']['frames']} кадрах, боксы в {res['summary']['frames_any_box']}"
              f"/{res['summary']['frames']}")

        # визуализация двух кадров на доску
        layout = res["layout"]
        board = imread(ROOT.parent / "ar_board" / d / "board_A3.png")
        rng = np.random.default_rng(SEED)
        from ar_board import detect_board, project_components
        from ar_board.synthetic import make_case
        for i in range(1, args.frames + 1):
            case = make_case(layout, board, (1280, 960), rng, max_tilt_deg=35.0,
                             scale_range=(0.5, 1.0))
            if i not in (1, 4):
                continue
            frame = case["frame"]
            pose = detect_board(frame, layout)
            projected = project_components(pose, layout) if pose else []
            polys = {p["component_id"]: p["polygon"] for p in projected if p.get("polygon")}
            r = detector.detect(frame, polys)
            boxes = detector.predict_boxes(frame)
            vis = draw_nodes(draw_boxes(frame, boxes), r, polys)
            imwrite(samples / f"board_{tag}_frame{i:02d}.jpg", vis)

    report["boards"] = boards

    # сводная таблица «узел → площадь»
    table = {}
    clean = boards.get("clean", {}).get("by_node", {})
    defect = boards.get("defect", {}).get("by_node", {})
    for cid in sorted(set(clean) | set(defect)):
        table[cid] = {
            "clean_mean_pct": clean.get(cid, {}).get("mean_area_pct", 0.0),
            "clean_max_pct": clean.get(cid, {}).get("max_area_pct", 0.0),
            "clean_frames_detected": clean.get(cid, {}).get("frames_detected", 0),
            "defect_mean_pct": defect.get(cid, {}).get("mean_area_pct", 0.0),
            "defect_max_pct": defect.get(cid, {}).get("max_area_pct", 0.0),
            "defect_frames_detected": defect.get(cid, {}).get("frames_detected", 0),
        }
    report["node_table"] = table
    print("\nузел → средняя площадь поражения, %")
    print(f"{'узел':<16}{'чистый':>9}{'дефект':>9}")
    for cid, row in table.items():
        print(f"{cid:<16}{row['clean_mean_pct']:>9.2f}{row['defect_mean_pct']:>9.2f}")

    # примеры с публичных данных (предсказание vs GT)
    test_dir = ROOT / "dataset" / "images" / "test"
    lbl_dir = ROOT / "dataset" / "labels" / "test"
    shown = defaultdict(int)
    for img_path in sorted(test_dir.glob("*.jpg")):
        src = img_path.name.split("__")[0]
        if shown[src] >= 2:
            continue
        img = imread(img_path)
        if img is None:
            continue
        boxes = detector.predict_boxes(img)
        vis = draw_boxes(img, boxes, color=(0, 200, 255))
        gt = []
        lbl = lbl_dir / (img_path.stem + ".txt")
        h, w = img.shape[:2]
        if lbl.is_file():
            for line in lbl.read_text(encoding="utf-8").splitlines():
                p = line.split()
                if len(p) == 5:
                    cx, cy, bw, bh = map(float, p[1:])
                    gt.append([(cx - bw / 2) * w, (cy - bh / 2) * h,
                               (cx + bw / 2) * w, (cy + bh / 2) * h, 1.0])
        for x1, y1, x2, y2, _ in gt:
            cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), (60, 220, 60), 2)
        shown[src] += 1
        imwrite(samples / f"public_{src}_{shown[src]}.jpg", vis)

    # качественная проверка на реальных фото Mendeley (без разметки)
    men = sorted((ROOT / "data" / "raw" / "mendeley").rglob("*.jpg"))[:2]
    for i, p in enumerate(men, 1):
        img = imread(p)
        if img is None:
            continue
        h, w = img.shape[:2]
        scale = 1280.0 / max(h, w)
        small = cv2.resize(img, (int(w * scale), int(h * scale)))
        boxes = detector.predict_boxes(small)
        imwrite(samples / f"mendeley_real_{i}.jpg", draw_boxes(small, boxes))

    (ROOT / "eval_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nотчёт:", ROOT / "eval_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
