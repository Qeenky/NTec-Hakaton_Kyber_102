"""Эксперименты v2: аблации, проверка на реальных кадрах, report.json.

Запуск:
    python -m v2.experiments            # всё (датасет соберётся при отсутствии)
    python -m v2.experiments --quick    # меньше кадров на реальную проверку

Конфигурации аблаций (отложенный тест, дефекты в обучении не участвуют):
    1. base_no_align_shared_max        — как anomaly/: без выравнивания, общий банк, max
    2. align_shared_max                — + выравнивание
    3. align_percomp_max               — + раздельные банки
    4. align_percomp_topk_area         — + top-k mean и площадь (без TTA)
    5. align_percomp_topk_area_tta     — + TTA (итоговая модель)
"""

from __future__ import annotations

import argparse
import json
import sys
import zlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .canonical import (
    COMPONENTS,
    CROP_SIZE,
    board_homography,
    canonical_crops,
    imread,
    imwrite,
    load_layout,
    simulate_unaligned,
    ssim,
)
from .detector import (
    AggregationConfig,
    AnomalyV2Model,
    ComponentDetector,
    FeatureExtractor,
    make_bank,
    nearest_distances,
)
from .synth_defects import build_dataset, load_dataset, load_texture_library

V2 = Path(__file__).resolve().parent
PROJECT = V2.parents[1]
DATA = V2 / "data"
MODELS = V2 / "models"
SAMPLES = V2 / "samples"
REPORT = V2 / "report.json"

BOARD_REAL = PROJECT / "ar_board" / "out_real" / "board_A3.png"
LAYOUT_REAL = PROJECT / "ar_board" / "out_real" / "layout.json"
BOARD_DEFECT = PROJECT / "ar_board" / "out_defect" / "board_A3.png"
LAYOUT_DEFECT = PROJECT / "ar_board" / "out_defect" / "layout.json"
CURATED = PROJECT / "organizer_data" / "defects" / "_curated"


# Метрики
def auroc(neg: Sequence[float], pos: Sequence[float]) -> float:
    """AUROC (score больше = аномальнее), с корректной обработкой совпадений."""
    neg = np.asarray(neg, dtype=np.float64)
    pos = np.asarray(pos, dtype=np.float64)
    if len(neg) == 0 or len(pos) == 0:
        return float("nan")
    values = np.concatenate([neg, pos])
    order = np.argsort(values, kind="mergesort")
    sorted_vals = values[order]
    ranks = np.empty(len(values), dtype=np.float64)
    i = 0
    while i < len(sorted_vals):
        j = i
        while j + 1 < len(sorted_vals) and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    n_pos = len(pos)
    n_neg = len(neg)
    return float((ranks[len(neg) :].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def pr_at(records: Sequence[Dict[str, Any]], key: str = "score") -> Dict[str, float]:
    """Precision/recall/F1 при пороге из калибровки (threshold в записи)."""
    tp = fp = tn = fn = 0
    for r in records:
        pred = r[key] > r["threshold"]
        if r["kind"] == "defect":
            tp += int(pred)
            fn += int(not pred)
        else:
            fp += int(pred)
            tn += int(not pred)
    prec = tp / (tp + fp) if (tp + fp) else float("nan")
    rec = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) else float("nan")
    return {
        "precision": float(prec),
        "recall": float(rec),
        "f1": float(f1),
        "fpr": float(fpr),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
    }


def bootstrap_macro_ci(
    records: Sequence[Dict[str, Any]], key: str = "score", n_boot: int = 1000, seed: int = 0
) -> List[float]:
    """95% bootstrap-CI для macro AUROC (12 чистых кропов на узел — мало)."""
    comps = sorted({r["component"] for r in records})
    data = {}
    for c in comps:
        rs = [r for r in records if r["component"] == c]
        data[c] = (
            np.asarray([r[key] for r in rs if r["kind"] == "clean"], dtype=np.float64),
            np.asarray([r[key] for r in rs if r["kind"] == "defect"], dtype=np.float64),
        )
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        per_comp = []
        for c in comps:
            neg, pos = data[c]
            nb = neg[rng.integers(0, len(neg), len(neg))]
            pb = pos[rng.integers(0, len(pos), len(pos))]
            per_comp.append(auroc(nb, pb))
        vals.append(float(np.nanmean(per_comp)))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def summarize(records: Sequence[Dict[str, Any]], key: str = "score") -> Dict[str, Any]:
    """Метрики по всем компонентам: macro AUROC + pooled + P/R."""
    per_comp: Dict[str, Any] = {}
    comps = sorted({r["component"] for r in records})
    macro = []
    for c in comps:
        rs = [r for r in records if r["component"] == c]
        neg = [r[key] for r in rs if r["kind"] == "clean"]
        pos = [r[key] for r in rs if r["kind"] == "defect"]
        a = auroc(neg, pos)
        pr = pr_at(rs, key)
        macro.append(a)
        per_comp[c] = {
            "auroc": round(float(a), 4),
            "n_clean": len(neg),
            "n_defect": len(pos),
            "score_clean_mean": round(float(np.mean(neg)), 4) if neg else None,
            "score_defect_mean": round(float(np.mean(pos)), 4) if pos else None,
            "threshold": round(float(rs[0]["threshold"]), 4),
            **{k: round(v, 4) for k, v in pr.items()},
        }
    pooled = auroc(
        [r[key] for r in records if r["kind"] == "clean"],
        [r[key] for r in records if r["kind"] == "defect"],
    )
    pr_all = pr_at(records, key)
    return {
        "auroc_macro": round(float(np.nanmean(macro)), 4),
        "auroc_macro_ci95": bootstrap_macro_ci(records, key),
        "auroc_pooled": round(float(pooled), 4),
        "precision": round(pr_all["precision"], 4),
        "recall": round(pr_all["recall"], 4),
        "f1": round(pr_all["f1"], 4),
        "fpr": round(pr_all["fpr"], 4),
        "per_component": per_comp,
    }


def paired_auroc(records: Sequence[Dict[str, Any]], key: str = "score") -> Dict[str, float]:
    """AUROC «дефект vs его собственный чистый базовый кроп» (без разницы фотометрии)."""
    clean = {r["id"]: r for r in records if r["kind"] == "clean"}
    pairs_neg, pairs_pos = [], []
    for r in records:
        if r["kind"] != "defect":
            continue
        base = clean.get(r.get("base_id", ""))
        if base is None:
            continue
        pairs_neg.append(base[key])
        pairs_pos.append(r[key])
    return {
        "auroc_paired": round(auroc(pairs_neg, pairs_pos), 4),
        "n_pairs": len(pairs_pos),
    }


# Данные
@dataclass
class Sample:
    id: str
    component: str
    kind: str
    image: np.ndarray
    defect_type: Optional[str] = None
    base_id: Optional[str] = None


def load_samples(data: Dict[str, Any], aligned: bool, *, seed: int = 0) -> Dict[str, Dict[str, List[Sample]]]:
    """Читает кропы по сплитам. aligned=False -> имитация кропа без выравнивания."""
    def jitter_rng(sid: str) -> np.random.Generator:
        # crc32 вместо hash(): hash() рандомизирован между запусками Python
        return np.random.default_rng(zlib.crc32(sid.encode("utf-8")))

    out: Dict[str, Dict[str, List[Sample]]] = {}
    for comp in data["components"]:
        out[comp] = {"train": [], "calib": [], "test_clean": [], "test_defect": []}
        split = data["splits"][comp]
        by_id = {r["id"]: r for r in data["records"]}
        for name in ("train", "calib", "test_clean"):
            for sid in split[name]:
                img = imread(DATA / "clean" / comp / f"{sid}.png")
                if not aligned:
                    img = simulate_unaligned(img, jitter_rng(sid))
                out[comp][name].append(
                    Sample(sid, comp, "clean", img, base_id=sid)
                )
        for sid in split["test_defect"]:
            rec = by_id.get(sid, {})
            img = imread(DATA / "defect" / comp / f"{sid}.png")
            if not aligned:
                img = simulate_unaligned(img, jitter_rng(sid))
            out[comp]["test_defect"].append(
                Sample(
                    sid,
                    comp,
                    "defect",
                    img,
                    defect_type=rec.get("defect_type"),
                    base_id=rec.get("base_id"),
                )
            )
    return out


def _subsample(feats: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    if k and len(feats) > k:
        sel = rng.choice(len(feats), size=k, replace=False)
        return feats[np.sort(sel)]
    return feats


# Аблации
@dataclass
class Config:
    name: str
    aligned: bool
    shared_bank: bool
    mode: str
    tta: bool


CONFIGS: Tuple[Config, ...] = (
    Config("base_no_align_shared_max", False, True, "max_rel", False),
    Config("align_shared_max", True, True, "max_rel", False),
    Config("align_percomp_max", True, False, "max_rel", False),
    Config("align_percomp_topk_area", True, False, "topk_area", False),
    Config("align_percomp_topk_area_tta", True, False, "topk_area", True),
)


def evaluate_config(
    cfg: Config,
    data: Dict[str, Any],
    extractor: FeatureExtractor,
    *,
    max_patches_per_image: int = 250,
    bank_ratio: float = 0.02,
    bank_max: int = 1200,
    seed: int = 0,
    verbose: bool = True,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    samples = load_samples(data, cfg.aligned, seed=seed)
    comps = [c for c in data["components"] if c in samples]

    def make_det(component: str) -> ComponentDetector:
        return ComponentDetector(
            component,
            extractor,
            aggregation=AggregationConfig(mode=cfg.mode),
            tta=cfg.tta,
            bank_ratio=bank_ratio,
            bank_max=bank_max,
            max_patches_per_image=max_patches_per_image,
            seed=seed,
        )

    # ---- 1) банки: раздельные (на узел) и/или общий -------------------- #
    dets: Dict[str, ComponentDetector] = {}
    rng = np.random.default_rng(seed)
    shared_pool: List[np.ndarray] = []
    train_feats: Dict[str, List[np.ndarray]] = {}
    for comp in comps:
        feats = [extractor.extract(s.image) for s in samples[comp]["train"]]
        train_feats[comp] = feats
        if cfg.shared_bank:
            shared_pool.extend(_subsample(f, max_patches_per_image, rng) for f in feats)
        else:
            det = make_det(comp)
            det.fit_features(feats)
            dets[comp] = det

    if cfg.shared_bank:
        shared_arr = np.concatenate(shared_pool, axis=0)
        shared_bank = make_bank(shared_arr, rng, ratio=bank_ratio, max_centers=bank_max)
        if verbose:
            print(f"    общий банк: {len(shared_bank)} патчей из {len(shared_arr)}")
        for comp in comps:
            # один и тот же банк, но калибровка — своя на каждый узел
            det = make_det(comp)
            det.bank = shared_bank
            det.topk = max(1, int(round(det.agg.topk_frac * extractor.grid**2)))
            det.meta.update(
                {
                    "component": comp,
                    "bank_scope": "shared",
                    "n_train_images": int(sum(len(samples[c]["train"]) for c in comps)),
                    "n_pool_patches": int(len(shared_arr)),
                    "n_bank_patches": int(len(shared_bank)),
                    "aggregation": cfg.mode,
                    "topk": det.topk,
                    "tta": cfg.tta,
                    **extractor.describe(),
                }
            )
            dets[comp] = det

    # ---- 2) калибровка на чистых кропах, не входивших в банк ---------- #
    for comp in comps:
        det = dets[comp]
        calib = [extractor.extract(s.image) for s in samples[comp]["calib"]]
        calib_flip = (
            [extractor.extract(cv2.flip(s.image, 1)) for s in samples[comp]["calib"]]
            if cfg.tta
            else None
        )
        det.calibrate(calib, feats_flip=calib_flip, verbose=False)
        if verbose:
            print(
                f"    {comp:16s} калибровка: порог={det.threshold_q95:.3f} "
                f"patch_thr={det.patch_thr:.3f}"
            )

    # ---- 3) тест ------------------------------------------------------ #
    records: List[Dict[str, Any]] = []
    for comp in comps:
        det = dets[comp]
        for split, kind in (("test_clean", "clean"), ("test_defect", "defect")):
            for s in samples[comp][split]:
                f = extractor.extract(s.image)
                ff = extractor.extract(cv2.flip(s.image, 1)) if cfg.tta else None
                r = det.score_patches(f, ff)
                records.append(
                    {
                        "component": comp,
                        "id": s.id,
                        "kind": kind,
                        "defect_type": s.defect_type,
                        "base_id": s.base_id,
                        "score": float(r["score"]),
                        "topk_mean": float(r["topk_mean"]),
                        "area": float(r["area"]),
                        "score_max": float(r["score_max"]),
                        "score_rel": float(r["score_rel"]),
                        "threshold": float(det.threshold_q95),
                        "threshold_q99": float(det.threshold_q99),
                    }
                )

    metrics = summarize(records)
    metrics.update(paired_auroc(records))
    metrics["config"] = cfg.name
    metrics["aligned"] = cfg.aligned
    metrics["shared_bank"] = cfg.shared_bank
    metrics["aggregation"] = cfg.mode
    metrics["tta"] = cfg.tta
    metrics["seconds"] = round(time.perf_counter() - t0, 2)
    metrics["records"] = records
    return metrics


# Проверка выравнивания (этап 1)
def check_alignment(
    n_views: int = 20, *, frame_size: Tuple[int, int] = (1600, 1200), tilt: float = 30.0, seed: int = 7
) -> Dict[str, Any]:
    sys.path.insert(0, str(PROJECT / "ar_board"))
    from ar_board.synthetic import make_case  # type: ignore

    layout = load_layout(LAYOUT_REAL)
    board = imread(BOARD_REAL)
    ref = canonical_crops(board, layout, homography=np.eye(3))
    rng = np.random.default_rng(seed)
    crops: Dict[str, List[np.ndarray]] = {c: [] for c in ref}
    corner_err: List[float] = []
    failed = 0

    def norm_img(z: np.ndarray) -> np.ndarray:
        z = z.astype(np.float32)
        m, s = float(z.mean()), float(z.std()) + 1e-6
        return np.clip((z - m) / s * 40 + 128, 0, 255).astype(np.uint8)

    for _ in range(n_views):
        case = make_case(layout, board, frame_size, rng, max_tilt_deg=tilt, scale_range=(0.5, 0.85))
        crops_v = canonical_crops(case["frame"], layout)
        if not crops_v:
            failed += 1
            continue
        # ошибка проекции углов зон: оценка ArUco vs истинная H
        src = np.concatenate(
            [
                np.asarray(comp["polygon_px"], dtype=np.float64).reshape(-1, 1, 2)
                for comp in layout["components"]
            ]
        )
        gt = cv2.perspectiveTransform(src, case["homography"]).reshape(-1, 2)
        est_h = board_homography(case["frame"], layout)
        if est_h is not None:
            est = cv2.perspectiveTransform(src, est_h).reshape(-1, 2)
            corner_err.append(float(np.mean(np.linalg.norm(gt - est, axis=1))))
        for c, im in crops_v.items():
            crops[c].append(im)

    out: Dict[str, Any] = {
        "n_views": n_views,
        "tilt_deg": tilt,
        "frame_size": list(frame_size),
        "failed_detections": failed,
        "corner_error_px_mean": round(float(np.mean(corner_err)), 3) if corner_err else None,
        "corner_error_px_max": round(float(np.max(corner_err)), 3) if corner_err else None,
        "per_component": {},
    }
    for c, ims in crops.items():
        if len(ims) < 2:
            continue
        raw, norm = [], []
        for i in range(len(ims)):
            for j in range(i + 1, len(ims)):
                raw.append(ssim(ims[i], ims[j]))
                norm.append(ssim(norm_img(ims[i]), norm_img(ims[j])))
        vs_ref = [ssim(norm_img(im), norm_img(ref[c])) for im in ims]
        out["per_component"][c] = {
            "ssim_raw_mean": round(float(np.mean(raw)), 4),
            "ssim_raw_min": round(float(np.min(raw)), 4),
            "ssim_norm_mean": round(float(np.mean(norm)), 4),
            "ssim_norm_min": round(float(np.min(norm)), 4),
            "ssim_vs_ref_norm_min": round(float(np.min(vs_ref)), 4),
            "n_crops": len(ims),
        }
    all_norm = [v["ssim_norm_min"] for v in out["per_component"].values()]
    out["ssim_norm_min_overall"] = round(float(min(all_norm)), 4) if all_norm else None
    out["passed_0_85"] = bool(all(v["ssim_raw_mean"] >= 0.85 for v in out["per_component"].values()))
    out["passed_0_85_norm"] = bool(out["ssim_norm_min_overall"] is not None and out["ssim_norm_min_overall"] >= 0.85)
    return out


# Реальные кадры: чистый vs дефектный планшет (этап 5)
def render_clean_perspectives(
    n: int,
    *,
    frame_size: Tuple[int, int] = (2560, 1920),
    tilt: float = 30.0,
    seed: int = 101,
) -> Dict[str, List[np.ndarray]]:
    """Канонические кропы чистого планшета из N синтетических перспектив.

    Это «реальный пайплайн»: те же искажения/ресемплинг, что и на кадрах, но
    только норма. Нужно для калибровки порога в том домене, где модель работает.
    """
    sys.path.insert(0, str(PROJECT / "ar_board"))
    from ar_board.synthetic import make_case  # type: ignore

    layout = load_layout(LAYOUT_REAL)
    board = imread(BOARD_REAL)
    rng = np.random.default_rng(seed)
    out: Dict[str, List[np.ndarray]] = {}
    for _ in range(n):
        case = make_case(layout, board, frame_size, rng, max_tilt_deg=tilt, scale_range=(0.5, 0.85))
        for comp, crop in canonical_crops(case["frame"], layout).items():
            out.setdefault(comp, []).append(crop)
    return out


def recalibrate_on_pipeline(
    model: AnomalyV2Model,
    *,
    n: int = 20,
    frame_size: Tuple[int, int] = (2560, 1920),
    seed: int = 101,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Калибровка порогов на кропах из реального пайплайна (чистый планшет)."""
    crops = render_clean_perspectives(n, frame_size=frame_size, seed=seed)
    stats: Dict[str, Any] = {}
    for comp, det in model.detectors.items():
        ims = crops.get(comp, [])
        if len(ims) < 5:
            continue
        feats = [det.extractor.extract(im) for im in ims]
        flips = [det.extractor.extract(cv2.flip(im, 1)) for im in ims] if det.tta else None
        s = det.calibrate(feats, feats_flip=flips, verbose=False)
        stats[comp] = {
            "threshold_q95": round(det.threshold_q95, 4),
            "threshold_q99": round(det.threshold_q99, 4),
            "calib_n": s["calib_n"],
            "calib_score_mean": round(s["calib_score_mean"], 4),
            "calib_score_p95": round(s["calib_score_p95"], 4),
        }
        if verbose:
            print(
                f"  {comp:16s} порог на реальном пайплайне: "
                f"q95={det.threshold_q95:.3f} q99={det.threshold_q99:.3f}"
            )
    return {"n_calib_views": n, "frame_size": list(frame_size), "per_component": stats}


def eval_real_frames(
    model: AnomalyV2Model,
    n_views: int = 20,
    *,
    frame_size: Tuple[int, int] = (2560, 1920),
    tilt: float = 30.0,
    seed: int = 11,
    verbose: bool = True,
) -> Dict[str, Any]:
    sys.path.insert(0, str(PROJECT / "ar_board"))
    from ar_board.synthetic import make_case  # type: ignore

    boards = {
        "clean": (BOARD_REAL, LAYOUT_REAL),
        "defect": (BOARD_DEFECT, LAYOUT_DEFECT),
    }
    records: List[Dict[str, Any]] = []
    detect_fail = {"clean": 0, "defect": 0}
    latencies: List[float] = []
    per_view: Dict[str, List[Dict[str, Any]]] = {"clean": [], "defect": []}

    for kind, (board_path, layout_path) in boards.items():
        layout = load_layout(layout_path)
        board = imread(board_path)
        rng = np.random.default_rng(seed)
        for i in range(n_views):
            case = make_case(layout, board, frame_size, rng, max_tilt_deg=tilt, scale_range=(0.5, 0.85))
            crops = canonical_crops(case["frame"], layout)
            if not crops:
                detect_fail[kind] += 1
                continue
            view_scores = {}
            for comp, crop in crops.items():
                if comp not in model.detectors:
                    continue
                r = model.score(comp, crop)
                latencies.append(float(r["latency_ms"]))
                view_scores[comp] = r["score"]
                records.append(
                    {
                        "component": comp,
                        "id": f"{kind}_{i:02d}",
                        "kind": kind,
                        "score": float(r["score"]),
                        "topk_mean": float(r["topk_mean"]),
                        "area": float(r["area"]),
                        "threshold": float(r["threshold"]),
                        "threshold_q99": float(r["threshold_q99"]),
                        "tilt_deg": case["meta"]["tilt_deg"],
                    }
                )
            per_view[kind].append(view_scores)

    comps = sorted({r["component"] for r in records})
    per_comp: Dict[str, Any] = {}
    macro = []
    not_evaluated: List[str] = []
    for c in comps:
        rs = [r for r in records if r["component"] == c]
        neg = [r["score"] for r in rs if r["kind"] == "clean"]
        pos = [r["score"] for r in rs if r["kind"] == "defect"]
        if not neg or not pos:
            # узел есть только на одном из планшетов (у дефектного нет манометра)
            not_evaluated.append(c)
            per_comp[c] = {
                "auroc": None,
                "n_clean": len(neg),
                "n_defect": len(pos),
                "score_clean_mean": round(float(np.mean(neg)), 4) if neg else None,
                "score_defect_mean": round(float(np.mean(pos)), 4) if pos else None,
                "note": "нет узла на одном из планшетов — AUROC не определён",
            }
            continue
        a = auroc(neg, pos)
        macro.append(a)
        rs_clean = [r for r in rs if r["kind"] == "clean"]
        rs_def = [r for r in rs if r["kind"] == "defect"]
        per_comp[c] = {
            "auroc": round(float(a), 4),
            "n_clean": len(neg),
            "n_defect": len(pos),
            "score_clean_mean": round(float(np.mean(neg)), 4),
            "score_defect_mean": round(float(np.mean(pos)), 4),
            "score_clean_max": round(float(np.max(neg)), 4),
            "detected_clean_frac_q95": round(
                float(np.mean([r["score"] > r["threshold"] for r in rs_clean])), 3
            ),
            "detected_defect_frac_q95": round(
                float(np.mean([r["score"] > r["threshold"] for r in rs_def])), 3
            ),
            "detected_clean_frac_q99": round(
                float(np.mean([r["score"] > r["threshold_q99"] for r in rs_clean])), 3
            ),
            "detected_defect_frac_q99": round(
                float(np.mean([r["score"] > r["threshold_q99"] for r in rs_def])), 3
            ),
        }
    result = {
        "n_views_per_class": n_views,
        "tilt_deg": tilt,
        "frame_size": list(frame_size),
        "detection_failures": detect_fail,
        "auroc_macro": round(float(np.nanmean(macro)), 4) if macro else None,
        "auroc_pooled": round(
            float(
                auroc(
                    [r["score"] for r in records if r["kind"] == "clean"],
                    [r["score"] for r in records if r["kind"] == "defect"],
                )
            ),
            4,
        ),
        "not_evaluated": not_evaluated,
        "latency_ms_mean": round(float(np.mean(latencies)), 1) if latencies else None,
        "latency_ms_p95": round(float(np.percentile(latencies, 95)), 1) if latencies else None,
        "per_component": per_comp,
        "caveat": (
            "Дефектный планшет — ДРУГОЕ фото (реальный ржавый насос в цеху), "
            "а не тот же кадр с дефектом. Высокий AUROC здесь подтверждает "
            "перенос на реальный дефект, но частично объясняется сменой сцены."
        ),
        "records": records,
    }
    if verbose:
        print(
            f"  реальные кадры: macro AUROC={result['auroc_macro']:.3f}, "
            f"pooled={result['auroc_pooled']:.3f}, провалов детекции={detect_fail}"
        )
    return result


# Pixel-level локализация (этап 2/3)
def eval_pixel_localization(
    model: AnomalyV2Model, data: Dict[str, Any], *, stride: int = 4, verbose: bool = True
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for comp in data["components"]:
        det = model.detectors.get(comp)
        if det is None:
            continue
        for sid in data["splits"][comp]["test_defect"]:
            rec = next(r for r in data["records"] if r["id"] == sid)
            crop = imread(DATA / "defect" / comp / f"{sid}.png")
            mask = imread(DATA / "mask" / comp / f"{sid}.png", cv2.IMREAD_GRAYSCALE)
            r = det.score(crop)
            heat = cv2.resize(r["heatmap"], (CROP_SIZE, CROP_SIZE), interpolation=cv2.INTER_LINEAR)
            m = (mask > 127)
            if m.sum() < 8:
                continue
            hh = heat[::stride, ::stride].ravel()
            mm = m[::stride, ::stride].ravel()
            rows.append(
                {
                    "component": comp,
                    "id": sid,
                    "defect_type": rec.get("defect_type"),
                    "pixel_auroc": auroc(hh[~mm], hh[mm]),
                    "energy_in_mask": float(heat[m].mean() / max(heat[~m].mean(), 1e-6)),
                    "mask_frac": float(m.mean()),
                }
            )
    by_type: Dict[str, List[float]] = {}
    by_comp: Dict[str, List[float]] = {}
    for r in rows:
        by_type.setdefault(str(r["defect_type"]), []).append(r["pixel_auroc"])
        by_comp.setdefault(r["component"], []).append(r["pixel_auroc"])
    out = {
        "n_samples": len(rows),
        "pixel_auroc_macro": round(float(np.mean([r["pixel_auroc"] for r in rows])), 4) if rows else None,
        "energy_ratio_median": round(float(np.median([r["energy_in_mask"] for r in rows])), 3) if rows else None,
        "by_defect_type": {
            k: {"pixel_auroc": round(float(np.mean(v)), 4), "n": len(v)} for k, v in by_type.items()
        },
        "by_component": {
            k: {"pixel_auroc": round(float(np.mean(v)), 4), "n": len(v)} for k, v in by_comp.items()
        },
        "per_sample": rows,
    }
    if verbose:
        print(
            f"  локализация: pixel AUROC={out['pixel_auroc_macro']}, "
            f"энергия в маске x{out['energy_ratio_median']}"
        )
    return out


# main
def ensure_dataset(verbose: bool = True) -> Dict[str, Any]:
    if (DATA / "meta.json").is_file():
        return load_dataset(DATA)
    layout = load_layout(LAYOUT_REAL)
    board = imread(BOARD_REAL)
    textures = load_texture_library(CURATED, verbose=verbose)
    return build_dataset(DATA, board, layout, textures=textures, verbose=verbose)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="v2: аблации и проверка на реальных кадрах")
    ap.add_argument("--quick", action="store_true", help="меньше кадров на реальную проверку")
    ap.add_argument("--device", default=None)
    ap.add_argument("--no-ablation", action="store_true")
    args = ap.parse_args(argv)

    t_start = time.perf_counter()
    print("[1/5] датасет")
    data = ensure_dataset()
    print(f"  сэмплов: {len(data['records'])}, узлы: {data['components']}")

    extractor = FeatureExtractor(device=args.device)
    print(f"[2/5] выравнивание: проверка SSIM ({extractor.device})")
    alignment = check_alignment(n_views=20 if not args.quick else 8)
    for c, v in alignment["per_component"].items():
        print(
            f"  {c:16s} SSIM raw={v['ssim_raw_mean']:.3f} (min {v['ssim_raw_min']:.3f}) "
            f"| norm={v['ssim_norm_mean']:.3f} (min {v['ssim_norm_min']:.3f})"
        )
    print(f"  ошибка позы: {alignment['corner_error_px_mean']} px")

    ablations: List[Dict[str, Any]] = []
    final_model: Optional[AnomalyV2Model] = None
    if not args.no_ablation:
        print("[3/5] аблации")
        for cfg in CONFIGS:
            print(f"  -- {cfg.name}")
            m = evaluate_config(cfg, data, extractor)
            print(
                f"     AUROC macro={m['auroc_macro']:.3f} pooled={m['auroc_pooled']:.3f} "
                f"P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f} "
                f"FPR={m['fpr']:.3f} ({m['seconds']} c)"
            )
            ablations.append(m)
    else:
        print("[3/5] аблации пропущены")

    print("[4/6] итоговая модель (конфиг 5) -> models/")
    samples = load_samples(data, aligned=True)
    model = AnomalyV2Model({})
    for comp in data["components"]:
        det = ComponentDetector(comp, extractor, tta=True)
        det.fit([s.image for s in samples[comp]["train"]])
        calib = [extractor.extract(s.image) for s in samples[comp]["calib"]]
        calib_flip = [extractor.extract(cv2.flip(s.image, 1)) for s in samples[comp]["calib"]]
        det.calibrate(calib, feats_flip=calib_flip)
        model.detectors[comp] = det
        print(
            f"  {comp:16s} банк={len(det.bank)} порог q95={det.threshold_q95:.3f} "
            f"q99={det.threshold_q99:.3f}"
        )
    final_model = model

    print("[5/6] реальные кадры: чистый vs дефектный планшет")
    real_before = eval_real_frames(final_model, n_views=20 if not args.quick else 6)

    # Порог, откалиброванный на эталонных кропах, слишком тесный для реального
    # пайплайна (ресемплинг/JPEG на контрастных кромках). Калибруем на кропах
    # из перспективных кадров ЧИСТОГО планшета (другие seed-ы, не тест).
    print("  калибровка на реальном пайплайне (30 чистых перспектив, seed=101)")
    calib_pipeline = recalibrate_on_pipeline(
        final_model, n=30 if not args.quick else 8
    )
    final_model.save(MODELS)
    real_after = eval_real_frames(final_model, n_views=20 if not args.quick else 6)

    print("[6/6] pixel-level локализация")
    pixel = eval_pixel_localization(final_model, data)

    # ---- отчёт --------------------------------------------------------- #
    ab_clean = [{k: v for k, v in m.items() if k != "records"} for m in ablations]
    report = {
        "version": "2.0",
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "environment": {
            "python": sys.version.split()[0],
            "torch": __import__("torch").__version__,
            "cuda": bool(__import__("torch").cuda.is_available()),
            "device": str(extractor.device),
            "gpu": __import__("torch").cuda.get_device_name(0)
            if __import__("torch").cuda.is_available()
            else None,
            "opencv": cv2.__version__,
        },
        "dataset": {
            "components": data["components"],
            "n_clean": data["n_clean"],
            "n_defect": data["n_defect"],
            "n_train": data["n_train"],
            "n_calib": data["n_calib"],
            "n_test_clean": data["n_test_clean"],
            "defect_types": data["defect_types"],
            "size": data["size"],
            "note": data["note"],
        },
        "alignment": alignment,
        "ablations": ab_clean,
        "final_model": {
            "config": CONFIGS[-1].name,
            "models_dir": str(MODELS.relative_to(V2)),
            "calibration": calib_pipeline,
            "per_component": {
                c: {
                    "threshold_q95": round(d.threshold_q95, 4),
                    "threshold_q99": round(d.threshold_q99, 4),
                    "patch_thr": round(d.patch_thr, 4),
                    "topk": d.topk,
                    "n_bank": int(len(d.bank)),
                }
                for c, d in final_model.detectors.items()
            },
        },
        "pixel_localization": pixel,
        "real_frames": {
            "thresholds_from_synthetic_calib": {
                k: v for k, v in real_before.items() if k != "records"
            },
            "thresholds_from_pipeline_calib": {
                k: v for k, v in real_after.items() if k != "records"
            },
        },
        "elapsed_seconds": round(time.perf_counter() - t_start, 1),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport.json: {REPORT}  ({report['elapsed_seconds']} c)")

    if ablations:
        print("\nконфигурация                      AUROC macro  pooled   P      R      F1")
        for m in ablations:
            print(
                f"{m['config']:34s}{m['auroc_macro']:>10.3f}{m['auroc_pooled']:>9.3f}"
                f"{m['precision']:>7.3f}{m['recall']:>7.3f}{m['f1']:>7.3f}"
            )
    print(
        f"\nреальные кадры: AUROC macro={real_after['auroc_macro']} "
        f"pooled={real_after['auroc_pooled']}"
    )
    for c, v in real_after["per_component"].items():
        print(
            f"  {c:16s} AUROC={v['auroc']} FP(clean)={v.get('detected_clean_frac')} "
            f"TP(defect)={v.get('detected_defect_frac')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
