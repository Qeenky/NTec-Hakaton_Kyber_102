"""Гармонизация публичных датасетов коррозии в YOLO-формат (один класс `corrosion`).

Источники:
  * pipelines  — Figshare 30019591, COCO instance segmentation (bbox готовые);
  * condition  — Figshare 16624663, Labelme-полигоны (3 уровня тяжести → 1 класс);
  * mendeley   — Mendeley tbjn6p2gn9: в публичном архиве только 152 JPEG без
                 разметки → в обучение не идёт (см. DATASETS.md);
  * wpi        — WPI-ARL: только экспертные оценки снимка, без боксов → не идёт.

Что делает:
  1) маски/полигоны → минимальный описанный прямоугольник;
  2) отбрасывает битые файлы, кадры без разметки и микро-боксы;
  3) дедупликация перцептивным хешем (dHash 8x8, Хэмминг <= threshold),
     чтобы один и тот же кадр не попал в train и val;
  4) сплит 80/10/10 по изображениям (стратифицированно по источникам);
  5) пишет dataset/{images,labels}/{train,val,test} + data.yaml + манифест.

    python prepare.py [--out dataset] [--dedup-dist 3] [--min-box 6]
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"

CLASS_NAMES = ["corrosion"]
SEED = 20261113


@dataclass
class Item:
    """Одно изображение-кандидат: путь + боксы в пикселях исходного кадра."""
    source: str
    path: Path
    width: int
    height: int
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    dropped: str | None = None


# чтение источников
def read_image(path: Path) -> np.ndarray | None:
    """cv2.imread не работает с кириллицей в пути — читаем через imdecode."""
    try:
        buf = np.fromfile(str(path), dtype=np.uint8)
        if buf.size == 0:
            return None
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None
    return img


def load_pipelines() -> list[Item]:
    """COCO: берём готовый bbox (axis-aligned, уже минимальный прямоугольник)."""
    base = RAW / "pipelines"
    items: list[Item] = []
    coco_paths = sorted(base.rglob("instances_default.json"))
    if not coco_paths:
        print("  [pipelines] нет instances_default.json — пропуск")
        return items
    coco = json.loads(coco_paths[0].read_text(encoding="utf-8"))
    img_root = coco_paths[0].parents[1] / "images"
    by_id = {im["id"]: im for im in coco["images"]}
    anns = defaultdict(list)
    for a in coco["annotations"]:
        # здесь все объекты размечены RLE с iscrowd=1 — это не «толпа», а формат
        # маски; bbox в COCO уже минимальный описанный прямоугольник.
        anns[a["image_id"]].append(a)

    for iid, im in by_id.items():
        name = Path(im["file_name"]).name
        cand = list(img_root.rglob(name))
        if not cand:
            items.append(Item("pipelines", Path(name), im["width"], im["height"],
                              dropped="файл изображения не найден"))
            continue
        boxes = []
        for a in anns.get(iid, []):
            x, y, w, h = a["bbox"]
            boxes.append((x, y, x + w, y + h))
        items.append(Item("pipelines", cand[0], im["width"], im["height"], boxes))
    return items


def load_condition() -> list[Item]:
    """Labelme-полигоны (severity 2/3/4) → bbox, все уровни = класс corrosion."""
    base = RAW / "condition"
    items: list[Item] = []
    for js in sorted(base.rglob("json/*.json")):
        d = json.loads(js.read_text(encoding="utf-8"))
        img_dir = js.parent.parent / "images"
        cand = list(img_dir.glob(js.stem + ".*"))
        if not cand:
            items.append(Item("condition", js.with_suffix(".jpg"), d.get("imageWidth", 0),
                              d.get("imageHeight", 0), dropped="файл изображения не найден"))
            continue
        boxes = []
        for shape in d.get("shapes", []):
            pts = shape.get("points") or []
            if len(pts) < 3:
                continue
            arr = np.asarray(pts, dtype=np.float64)
            x1, y1 = arr.min(axis=0)
            x2, y2 = arr.max(axis=0)
            boxes.append((float(x1), float(y1), float(x2), float(y2)))
        items.append(Item("condition", cand[0], int(d["imageWidth"]),
                          int(d["imageHeight"]), boxes))
    return items


def load_unlabeled() -> dict:
    """Датасеты без боксов: только фиксируем наличие в отчёте."""
    out = {}
    men = sorted((RAW / "mendeley").rglob("*.jpg"))
    out["mendeley"] = {
        "images": len(men),
        "boxes": 0,
        "used_for_training": False,
        "reason": "в публичном архиве Mendeley только изображения, файлов разметки нет",
    }
    wpi = [p for p in (RAW / "wpi").rglob("*.jpg") if "orig_new_name_ALL" in p.parts]
    out["wpi"] = {
        "images": len(wpi),
        "boxes": 0,
        "used_for_training": False,
        "reason": "только экспертный рейтинг снимка (xlsx), боксов/масок нет",
    }
    return out


# дедупликация
def dhash(img: np.ndarray, size: int = 8) -> int:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    diff = small[:, 1:] > small[:, :-1]
    bits = 0
    for bit in diff.flatten():
        bits = (bits << 1) | int(bit)
    return bits


def dedup(items: list[Item], max_dist: int) -> tuple[list[Item], list[Item]]:
    """Жадная дедупликация: первый встреченный кадр побеждает.

    Сравнение — dHash 64 бита. Приоритет источника: pipelines → condition
    (у pipelines меньше кадров и они «дороже»).
    """
    order = {"pipelines": 0, "condition": 1}
    items = sorted(items, key=lambda it: (order.get(it.source, 9), str(it.path)))
    kept: list[Item] = []
    dupes: list[Item] = []
    hashes: list[int] = []
    for it in items:
        img = read_image(it.path)
        if img is None:
            it.dropped = "битый файл (не читается)"
            dupes.append(it)
            continue
        h = dhash(img)
        hb = np.frombuffer(h.to_bytes(8, "big"), dtype=np.uint8)
        near = False
        for prev in hashes:
            pb = np.frombuffer(prev.to_bytes(8, "big"), dtype=np.uint8)
            if int(np.unpackbits(hb ^ pb).sum()) <= max_dist:
                near = True
                break
        if near:
            it.dropped = f"дубликат (dHash<= {max_dist})"
            dupes.append(it)
            continue
        hashes.append(h)
        it.width, it.height = img.shape[1], img.shape[0]
        kept.append(it)
    return kept, dupes


# фильтрация боксов и сплит
def filter_boxes(it: Item, min_box: float, min_area: float) -> int:
    """Обрезает боксы по кадру и выбрасывает микро-объекты. Возвращает число боксов."""
    out = []
    for x1, y1, x2, y2 in it.boxes:
        x1 = max(0.0, min(x1, it.width))
        x2 = max(0.0, min(x2, it.width))
        y1 = max(0.0, min(y1, it.height))
        y2 = max(0.0, min(y2, it.height))
        w, h = x2 - x1, y2 - y1
        if w < min_box or h < min_box or w * h < min_area:
            continue
        out.append((x1, y1, x2, y2))
    it.boxes = out
    return len(out)


def split_items(items: list[Item], ratios=(0.8, 0.1, 0.1), seed: int = SEED) -> dict[str, list[Item]]:
    """Сплит по изображениям, стратифицированно по источникам."""
    rng = random.Random(seed)
    by_src = defaultdict(list)
    for it in items:
        by_src[it.source].append(it)
    split: dict[str, list[Item]] = {"train": [], "val": [], "test": []}
    for src, lst in sorted(by_src.items()):
        lst = sorted(lst, key=lambda it: str(it.path))
        rng.shuffle(lst)
        n = len(lst)
        n_val = max(1, round(n * ratios[1]))
        n_test = max(1, round(n * ratios[2]))
        if n < 5:  # слишком маленький источник — только в train
            split["train"].extend(lst)
            continue
        split["test"].extend(lst[:n_test])
        split["val"].extend(lst[n_test:n_test + n_val])
        split["train"].extend(lst[n_test + n_val:])
    return split


def write_yolo(split: dict[str, list[Item]], out: Path) -> dict:
    stats = {}
    for part, items in split.items():
        img_dir = out / "images" / part
        lbl_dir = out / "labels" / part
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)
        n_boxes = 0
        per_src = Counter()
        used: set[str] = set()
        for it in items:
            # имена вида 0.jpg повторяются между Train/Test исходного набора —
            # добавляем папку-родителя и счётчик, чтобы ничего не перезаписать
            tag = it.path.parent.parent.name
            stem = f"{it.source}__{tag}__{it.path.stem}"
            base_stem, k = stem, 1
            while stem in used:
                stem = f"{base_stem}_{k}"
                k += 1
            used.add(stem)
            dst = img_dir / (stem + ".jpg")
            img = read_image(it.path)
            if img is None:
                continue
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            if not ok:
                continue
            buf.tofile(str(dst))
            lines = []
            for x1, y1, x2, y2 in it.boxes:
                cx = (x1 + x2) / 2.0 / it.width
                cy = (y1 + y2) / 2.0 / it.height
                bw = (x2 - x1) / it.width
                bh = (y2 - y1) / it.height
                lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            (lbl_dir / (stem + ".txt")).write_text("\n".join(lines) + ("\n" if lines else ""),
                                                   encoding="utf-8")
            n_boxes += len(lines)
            per_src[it.source] += 1
        stats[part] = {"images": len(items), "boxes": n_boxes,
                       "by_source": dict(per_src)}
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Гармонизация датасетов коррозии в YOLO")
    ap.add_argument("--out", default=str(ROOT / "dataset"))
    ap.add_argument("--dedup-dist", type=int, default=3, help="порог Хэмминга для dHash")
    ap.add_argument("--min-box", type=float, default=8.0, help="мин. сторона бокса, px")
    ap.add_argument("--min-area", type=float, default=400.0, help="мин. площадь бокса, px^2")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)

    report: dict = {"class_names": CLASS_NAMES, "params": vars(args), "sources": {}}
    raw_items = load_pipelines() + load_condition()
    print(f"прочитано кандидатов: {len(raw_items)}")
    for src in ("pipelines", "condition"):
        n = sum(1 for it in raw_items if it.source == src)
        report["sources"][src] = {"images_read": n}

    # 1) отбраковка до дедупликации: битые файлы и кадры без разметки
    alive: list[Item] = []
    for it in raw_items:
        if it.dropped:
            continue
        if not it.boxes:
            it.dropped = "нет разметки"
            continue
        if filter_boxes(it, args.min_box, args.min_area) == 0:
            it.dropped = "все боксы меньше порога"
            continue
        alive.append(it)

    # 2) дедупликация
    kept, dupes = dedup(alive, args.dedup_dist)
    # после dedup известны фактические размеры кадра — обрезаем боксы по ним
    kept = [it for it in kept if filter_boxes(it, args.min_box, args.min_area) > 0]
    dropped = [it for it in raw_items if it.dropped]
    print(f"после фильтра/дедупа: {len(kept)} (отброшено {len(dropped)})")

    # 3) сплит и запись
    split = split_items(kept)
    stats = write_yolo(split, out)

    reasons = Counter(it.dropped for it in dropped if it.dropped)
    for src in ("pipelines", "condition"):
        src_items = [it for it in kept if it.source == src]
        report["sources"][src].update({
            "images_kept": len(src_items),
            "boxes": int(sum(len(it.boxes) for it in src_items)),
            "images_dropped": sum(1 for it in dropped if it.source == src),
        })
    report["sources"].update(load_unlabeled())
    report["dropped_reasons"] = dict(reasons)
    report["split"] = stats
    report["totals"] = {
        "images": sum(s["images"] for s in stats.values()),
        "boxes": sum(s["boxes"] for s in stats.values()),
    }
    (out / "data.yaml").write_text(
        f"path: {out.as_posix()}\ntrain: images/train\nval: images/val\ntest: images/test\n"
        f"names:\n  0: {CLASS_NAMES[0]}\n", encoding="utf-8")
    (ROOT / "prepare_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
