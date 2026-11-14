"""Оценка детекторов дефектов на синтетике: precision/recall/AUC/FPR.

    python cv_classic/scripts/make_defect_samples.py      # сначала датасет
    python cv_classic/scripts/eval_defects.py
    python cv_classic/scripts/eval_defects.py --json-out cv_classic/data/eval_defects.json

Методика (честная, без подгонки на тесте):
  1) датасет детерминированно делится 50/50 со стратификацией по подтипу;
  2) рабочий порог каждого детектора выбирается ТОЛЬКО на train-половине:
     максимум recall при FPR на чистых негативах <= 10 %;
  3) все метрики в таблице считаются на test-половине (порог не видел её);
  4) дополнительно печатаются метрики на порогах из configs/defects.json
     (они и уходят в прод) и ROC-AUC (не зависит от порога);
  5) отдельно считается FPR на чистых негативах и на сложных (рыжая краска,
     грязь, тень+блик) — это главный источник ложных срабатываний.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cv_classic.defects import DETECTORS, load_config  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

CLASSES = ("rust", "oil", "oxidation")
TARGET_PRECISION = 0.85
TARGET_RECALL = 0.80
TARGET_FPR = 0.10
DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data" / "defects"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "eval_defects.json"


def _load(data: Path) -> list[dict]:
    truth = json.loads((data / "truth.json").read_text(encoding="utf-8"))
    items = truth["items"]
    for it in items:
        it["_path"] = str(data / it["file"])
    return items


def _key(it: dict) -> str:
    return str(it["group"])


def _split(items: list[dict], seed: int = 7, test_frac: float = 0.5
           ) -> tuple[list[dict], list[dict]]:
    """Детерминированное стратифицированное деление."""
    rng = np.random.default_rng(seed)
    buckets: dict[str, list[dict]] = {}
    for it in items:
        buckets.setdefault(_key(it), []).append(it)
    train: list[dict] = []
    test: list[dict] = []
    for key in sorted(buckets):
        arr = buckets[key]
        idx = rng.permutation(len(arr))
        n_test = int(round(len(arr) * test_frac))
        n_test = max(1, n_test) if len(arr) > 1 else 0
        n_test = min(n_test, max(0, len(arr) - 1))
        for j, i in enumerate(idx):
            (test if j < n_test else train).append(arr[int(i)])
    return train, test


def _ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def _auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    x = np.asarray(pos + neg, float)
    r = _ranks(x)
    n1, n0 = len(pos), len(neg)
    return float((r[:n1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def _is_clean(r: dict) -> bool:
    """Чистый негатив: все три метки 0 и это не сложный пограничный случай."""
    return not any(r["labels"].values()) and not r["hard_negative"]


def _metrics(rows: list[dict], cls: str, thr: float) -> dict:
    tp = fp = fn = 0
    hard_fp = hard_n = 0
    for r in rows:
        pred = r["scores"][cls] >= thr
        truth = bool(r["labels"][cls])
        if truth and pred:
            tp += 1
        elif truth and not pred:
            fn += 1
        elif not truth and pred:
            fp += 1
        if r["hard_negative"]:
            hard_n += 1
            if pred:
                hard_fp += 1
    clean_neg = [r for r in rows if _is_clean(r)]
    clean_fp = sum(1 for r in clean_neg if r["scores"][cls] >= thr)
    other_neg = [r for r in rows if not r["labels"][cls]
                 and not r["hard_negative"] and not _is_clean(r)]
    other_fp = sum(1 for r in other_neg if r["scores"][cls] >= thr)
    pos = [r["scores"][cls] for r in rows if r["labels"][cls]]
    neg = [r["scores"][cls] for r in clean_neg]
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)
    return {
        "threshold": round(float(thr), 3),
        "positives": tp + fn, "tp": tp, "fn": fn, "fp": fp,
        "clean_negatives": len(clean_neg), "clean_fp": clean_fp,
        "fpr_clean": clean_fp / len(clean_neg) if clean_neg else 0.0,
        "other_class_negatives": len(other_neg), "other_class_fp": other_fp,
        "hard_negatives": hard_n, "hard_fp": hard_fp,
        "fpr_hard": hard_fp / hard_n if hard_n else 0.0,
        "precision": precision, "recall": recall, "f1": f1,
        "auc_clean": _auc(pos, neg),
        "auc_all_neg": _auc(pos, [r["scores"][cls] for r in rows
                                  if not r["labels"][cls]]),
    }


def _tune(rows: list[dict], cls: str) -> tuple[float, dict]:
    """Порог по train: max recall при FPR на чистых негативах <= TARGET_FPR."""
    clean = [r for r in rows if _is_clean(r)]
    pos = [r for r in rows if r["labels"][cls]]
    grid = np.round(np.arange(0.05, 0.951, 0.01), 2)
    best: tuple[float, dict] | None = None
    fallback: tuple[float, dict] | None = None
    for t in grid:
        rec = (sum(1 for r in pos if r["scores"][cls] >= t) / len(pos)
               if pos else 0.0)
        fpr = (sum(1 for r in clean if r["scores"][cls] >= t) / len(clean)
               if clean else 0.0)
        all_neg = [r for r in rows if not r["labels"][cls]]
        prec = 0.0
        if pos:
            tp = sum(1 for r in pos if r["scores"][cls] >= t)
            fp = sum(1 for r in all_neg if r["scores"][cls] >= t)
            prec = tp / (tp + fp) if (tp + fp) else 0.0
        info = {"recall": rec, "fpr": fpr, "precision": prec}
        if fpr <= TARGET_FPR:
            if best is None or rec > best[1]["recall"] + 1e-9:
                best = (float(t), info)
            elif abs(rec - best[1]["recall"]) <= 1e-9 and \
                    prec > best[1]["precision"]:
                best = (float(t), info)
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        if fallback is None or f1 > fallback[1].get("f1", -1):
            fallback = (float(t), {**info, "f1": f1})
    if best is not None:
        return best[0], {**best[1], "rule": "max_recall_fpr<=0.10"}
    assert fallback is not None
    return fallback[0], {**fallback[1], "rule": "max_f1_fallback"}


def _feature_auc(rows: list[dict], cls: str) -> list[dict]:
    """Унивариативная информативность признаков (|AUC-0.5| на test)."""
    pos = [r for r in rows if r["labels"][cls]]
    neg = [r for r in rows if _is_clean(r)]
    keys: set[str] = set()
    for r in rows:
        keys.update(k for k, v in r["evidence"][cls].items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool))
        keys.update(f"parts.{k}" for k in r["evidence"][cls].get("parts", {}))
    out = []
    for key in sorted(keys):
        if key in ("threshold", "score", "components", "min_component_px"):
            continue

        def val(ev: dict) -> float | None:
            if key.startswith("parts."):
                x = ev.get("parts", {}).get(key.split(".", 1)[1])
                # нет маски -> вклад признака 0, а не «нет данных»
                return 0.0 if x is None else float(x)
            x = ev.get(key)
            return None if x is None else float(x)

        p = [val(r["evidence"][cls]) for r in pos]
        n = [val(r["evidence"][cls]) for r in neg]
        p = [x for x in p if x is not None]
        n = [x for x in n if x is not None]
        a = _auc(p, n)
        if a is None:
            continue
        out.append({"feature": key, "auc": round(a, 3),
                    "direction": "higher_is_defect" if a >= 0.5
                    else "lower_is_defect",
                    "strength": round(abs(a - 0.5) * 2, 3)})
    out.sort(key=lambda d: d["strength"], reverse=True)
    return out


def _score_all(items: list[dict], cfg: dict) -> list[dict]:
    import cv2
    rows = []
    for it in items:
        raw = np.frombuffer(Path(it["_path"]).read_bytes(), np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        scores, evidence, errors = {}, {}, {}
        for cls in CLASSES:
            res = DETECTORS[cls](img, cfg)
            scores[cls] = res.score
            evidence[cls] = res.evidence
            errors[cls] = res.error
        rows.append({"file": it["file"], "group": it["group"],
                     "labels": it["labels"], "hard_negative": it["hard_negative"],
                     "area_frac": it["area_frac"], "scores": scores,
                     "evidence": evidence, "errors": errors})
    return rows


def _fmt(ok: bool) -> str:
    return "OK" if ok else "FAIL"


def _run_holdout(items: list[dict], cfg: dict, data: Path, outp: Path) -> int:
    """Метрики на всём датасете при фиксированных порогах из конфига."""
    rows = _score_all(items, cfg)
    print(f"HOLDOUT: {len(items)} ROI, пороги фиксированы в configs/defects.json")
    print(f"{'класс':<11}{'порог':>6}{'pos':>5}{'TP':>4}{'FN':>4}{'FP':>4}"
          f"{'precision':>11}{'recall':>8}{'FPR_clean':>11}"
          f"{'FPR_hard':>10}{'AUC':>7}")
    results = {}
    all_ok = True
    for cls in CLASSES:
        m = _metrics(rows, cls, float(cfg[cls]["threshold"]))
        results[cls] = m
        auc = m["auc_clean"] or 0.0
        print(f"{cls:<11}{m['threshold']:>6.2f}{m['positives']:>5}{m['tp']:>4}"
              f"{m['fn']:>4}{m['fp']:>4}{m['precision']:>11.2f}"
              f"{m['recall']:>8.2f}{m['fpr_clean']:>11.2f}"
              f"{m['fpr_hard']:>10.2f}{auc:>7.3f}")
        ok = (m["precision"] >= TARGET_PRECISION and
              m["recall"] >= TARGET_RECALL and m["fpr_clean"] <= TARGET_FPR)
        all_ok &= ok
        print(f"  -> {'OK' if ok else 'FAIL'}: precision>={TARGET_PRECISION}, "
              f"recall>={TARGET_RECALL}, FPR<={TARGET_FPR}")
    print("\nпровалы:")
    for cls in CLASSES:
        thr = float(cfg[cls]["threshold"])
        for r in rows:
            truth = bool(r["labels"][cls])
            pred = r["scores"][cls] >= thr
            if truth and not pred:
                print(f"  FN {cls:<10}{r['scores'][cls]:.3f}  {r['file']:<16}"
                      f"{r['group']}")
            elif not truth and pred:
                kind = "сложный" if r["hard_negative"] else "негатив"
                print(f"  FP {cls:<10}{r['scores'][cls]:.3f}  {r['file']:<16}"
                      f"{r['group']} ({kind})")
    fimp = {cls: _feature_auc(rows, cls)[:5] for cls in CLASSES}
    print("\nинформативные признаки (AUC, весь holdout):")
    for cls in CLASSES:
        print(f"  {cls}: " + ", ".join(
            f"{t['feature']}={t['auc']:.2f}" for t in fimp[cls]))
    out = {
        "schema": "cv_classic.eval_defects/v1",
        "mode": "holdout_fixed_thresholds",
        "dataset": {"path": str(data), "items": len(items)},
        "thresholds": {c: float(cfg[c]["threshold"]) for c in CLASSES},
        "targets": {"precision": TARGET_PRECISION, "recall": TARGET_RECALL,
                    "fpr_clean": TARGET_FPR},
        "metrics": results,
        "feature_importance": fimp,
        "rows": [{k: v for k, v in r.items() if k != "evidence"} for r in rows],
    }
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"\nсохранено: {outp}")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--json-out", default=str(DEFAULT_OUT))
    ap.add_argument("--split-seed", type=int, default=7)
    ap.add_argument("--holdout", action="store_true",
                    help="без подбора порогов: метрики на всём датасете "
                         "с порогами из configs/defects.json (честный holdout)")
    args = ap.parse_args(argv)

    data = Path(args.data)
    if not (data / "truth.json").exists():
        print(f"нет {data / 'truth.json'} — сначала запустите "
              f"scripts/make_defect_samples.py")
        return 2

    cfg = load_config()
    items = _load(data)
    if args.holdout:
        return _run_holdout(items, cfg, data, Path(args.json_out))

    train, test = _split(items, args.split_seed)
    print(f"датасет: {len(items)} ROI   train={len(train)}   test={len(test)} "
          f"(стратифицированно, seed={args.split_seed})")
    groups: dict[str, int] = {}
    for it in items:
        groups[it["group"]] = groups.get(it["group"], 0) + 1
    print("подтипы: " + ", ".join(f"{k}={v}" for k, v in sorted(groups.items())))

    rows_train = _score_all(train, cfg)
    rows_test = _score_all(test, cfg)

    tuned = {}
    for cls in CLASSES:
        thr, info = _tune(rows_train, cls)
        tuned[cls] = {"threshold": round(thr, 2), **info}

    print("\nпороги (выбраны только на train):")
    for cls in CLASSES:
        t = tuned[cls]
        print(f"  {cls:<10} thr={t['threshold']:.2f}  "
              f"train recall={t['recall']:.2f}  train FPR={t['fpr']:.2f}  "
              f"[{t['rule']}]   в конфиге: {cfg[cls]['threshold']}")

    print("\n" + "=" * 104)
    print("TEST (порог выбран на train — тест не участвовал в подборе)")
    print("=" * 104)
    print(f"{'класс':<11}{'порог':>6}{'pos':>5}{'TP':>4}{'FN':>4}{'FP':>4}"
          f"{'precision':>11}{'recall':>8}{'F1':>7}{'FPR_clean':>11}"
          f"{'FPR_hard':>10}{'AUC':>7}")
    results: dict[str, dict] = {}
    for cls in CLASSES:
        thr = tuned[cls]["threshold"]
        m = _metrics(rows_test, cls, thr)
        results[cls] = m
        auc = m["auc_clean"]
        print(f"{cls:<11}{thr:>6.2f}{m['positives']:>5}{m['tp']:>4}{m['fn']:>4}"
              f"{m['fp']:>4}{m['precision']:>11.2f}{m['recall']:>8.2f}"
              f"{m['f1']:>7.2f}{m['fpr_clean']:>11.2f}{m['fpr_hard']:>10.2f}"
              f"{(auc if auc is not None else 0):>7.3f}")

    print("\nтот же test на порогах из configs/defects.json (прод):")
    print(f"{'класс':<11}{'порог':>6}{'TP':>4}{'FN':>4}{'FP':>4}"
          f"{'precision':>11}{'recall':>8}{'FPR_clean':>11}{'FPR_hard':>10}")
    prod: dict[str, dict] = {}
    for cls in CLASSES:
        thr = float(cfg[cls]["threshold"])
        m = _metrics(rows_test, cls, thr)
        prod[cls] = m
        print(f"{cls:<11}{thr:>6.2f}{m['tp']:>4}{m['fn']:>4}{m['fp']:>4}"
              f"{m['precision']:>11.2f}{m['recall']:>8.2f}"
              f"{m['fpr_clean']:>11.2f}{m['fpr_hard']:>10.2f}")

    print("\nцели (test, train-порог):")
    all_ok = True
    for cls in CLASSES:
        m = results[cls]
        ok = (m["precision"] >= TARGET_PRECISION and
              m["recall"] >= TARGET_RECALL and m["fpr_clean"] <= TARGET_FPR)
        all_ok &= ok
        print(f"  {cls:<10} precision {m['precision']:.2f} "
              f"(>={TARGET_PRECISION:.2f})  recall {m['recall']:.2f} "
              f"(>={TARGET_RECALL:.2f})  FPR_clean {m['fpr_clean']:.2f} "
              f"(<={TARGET_FPR:.2f})  -> {_fmt(ok)}")

    print("\nпровалы на test (порог из train):")
    for cls in CLASSES:
        thr = tuned[cls]["threshold"]
        for r in rows_test:
            truth = bool(r["labels"][cls])
            pred = r["scores"][cls] >= thr
            if truth and not pred:
                print(f"  FN {cls:<10}{r['scores'][cls]:.3f}  {r['file']:<16}"
                      f"{r['group']} (площадь {r['area_frac']:.2f})")
            elif not truth and pred:
                kind = "сложный негатив" if r["hard_negative"] else "негатив"
                print(f"  FP {cls:<10}{r['scores'][cls]:.3f}  {r['file']:<16}"
                      f"{r['group']} ({kind})")

    print("\nсамые информативные признаки (AUC на test, 1.0 = идеальное "
          "разделение):")
    fimp = {}
    for cls in CLASSES:
        top = _feature_auc(rows_test, cls)[:5]
        fimp[cls] = top
        print(f"  {cls}: " + ", ".join(
            f"{t['feature']}={t['auc']:.2f}" for t in top))

    out = {
        "schema": "cv_classic.eval_defects/v1",
        "dataset": {"path": str(data), "items": len(items),
                    "train": len(train), "test": len(test),
                    "groups": groups, "split_seed": args.split_seed},
        "targets": {"precision": TARGET_PRECISION, "recall": TARGET_RECALL,
                    "fpr_clean": TARGET_FPR},
        "tuned_on_train": tuned,
        "test_metrics": results,
        "test_metrics_config_threshold": prod,
        "feature_importance_test": fimp,
        "rows_test": [{k: v for k, v in r.items() if k != "evidence"}
                      for r in rows_test],
        "notes": [
            "Пороги выбраны на train-половине при FPR_clean <= 10%; "
            "метрики посчитаны на test-половине.",
            "FPR_hard — доля ложных срабатываний на сложных негативах "
            "(рыжая краска, сухая грязь, сильная тень + блик); они не входят "
            "в constraint при подборе порога.",
            "Данные синтетические: реальные камеры требуют калибровки под "
            "освещение (см. README).",
        ],
    }
    outp = Path(args.json_out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"\nсохранено: {outp}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
