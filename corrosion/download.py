"""Скачивание публичных датасетов коррозии без регистрации/токенов.

Источники, требующие авторизации (Roboflow Universe, Kaggle), здесь отсутствуют
намеренно — см. DATASETS.md.

    python download.py [--only NAME]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

SOURCES = {
    "figshare_pipelines": {
        "title": "Corroded Pipelines Dataset (Figshare 30019591)",
        "url": "https://ndownloader.figshare.com/files/57550888",
        "file": "Corrosion_Pipelines.zip",
        "size": 11_009_354,
    },
    "mendeley_purdue": {
        "title": "Image-based Corrosion Detection Dataset (Mendeley, Purdue)",
        "url": ("https://data.mendeley.com/public-files/datasets/tbjn6p2gn9/files/"
                "c311d38e-f04d-41ff-a508-dba6b60cc07b/file_downloaded"),
        "file": "Mendeley_Corrosion_Data.zip",
        "size": 662_667_730,
    },
    "wpi_arl": {
        "title": "WPI-ARL Corrosion Image Data Set",
        "url": "https://arl.wpi.edu/data/DATA_SET_FOR_RELEASE_CORROSION.zip",
        "file": "WPI_corrosion.zip",
        "size": None,
    },
    "figshare_condition": {
        "title": "Corrosion Condition State Semantic Segmentation Dataset (Figshare 16624663)",
        "url": "https://ndownloader.figshare.com/files/31729733",
        "file": "Corrosion_Condition_State.zip",
        "size": 333_388_620,
    },
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


def download(url: str, dest: Path, expected: int | None) -> dict:
    """Качает файл с докачкой по Range, возвращает запись для отчёта."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        if expected is None or dest.stat().st_size >= expected * 0.99:
            return {"file": dest.name, "bytes": dest.stat().st_size, "status": "cached"}
    start = dest.stat().st_size if dest.is_file() else 0
    headers = {"User-Agent": UA}
    if start:
        headers["Range"] = f"bytes={start}-"
    req = urllib.request.Request(url, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            mode = "ab" if start and resp.status == 206 else "wb"
            if mode == "wb":
                start = 0
            total = int(resp.headers.get("Content-Length") or 0) + start
            with open(dest, mode) as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
    except Exception as exc:  # noqa: BLE001
        return {"file": dest.name, "status": "error", "error": repr(exc)[:300]}
    size = dest.stat().st_size
    return {"file": dest.name, "bytes": size, "total": total if "total" in dir() else None,
            "status": "ok", "seconds": round(time.time() - t0, 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args(argv)
    names = args.only or list(SOURCES)
    report = {}
    for name in names:
        src = SOURCES[name]
        print(f"[{name}] {src['title']}\n  -> {src['file']}", flush=True)
        rec = download(src["url"], DATA / src["file"], src["size"])
        rec.update({"title": src["title"], "url": src["url"]})
        report[name] = rec
        print("  ", rec, flush=True)
    path = DATA / "download_report.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    old.update(report)
    path.write_text(json.dumps(old, indent=2, ensure_ascii=False), encoding="utf-8")
    print("report ->", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
