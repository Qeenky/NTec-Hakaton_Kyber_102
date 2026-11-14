"""PatchCore v2: раздельные банки на узел, устойчивая агрегация, TTA.

Отличия от `anomaly/patchcore.py`
---------------------------------
1. **Банк на каждый узел.** Норма мотора и норма фланца — разные распределения;
   общий банк размывает границу «норма/дефект» и даёт ложные срабатывания на
   соседнем узле.
2. **Агрегация top-k mean + площадь.** Максимум по карте — это один шумный
   патч; берём среднее по k = 1% самых аномальных патчей И долю патчей выше
   порога (площадь аномалии). Оба числа возвращаются, итоговый score —
   калиброванная смесь (масштабы приводятся по чистым кропам).
3. **TTA.** Усреднение карт по кадру и горизонтальному отражению.
4. **Калибровка на чистых кропах, отдельно на узел:** порог патча (q99
   распределения расстояний), опорные значения top-k/площади (q95), рабочий
   порог итогового score (q95 и q99).

Математика банка — как в PatchCore (Roth et al., 2022): признаки ResNet
(layer2+layer3, усреднение 3x3), жадный k-center coreset, расстояние до
ближайшего патча нормы.

Публичное API
-------------
    FeatureExtractor(device=...).extract(bgr) -> (N, C) float32
    ComponentDetector(component=...).fit(images).calibrate(clean).score(crop)
    AnomalyV2Model.load("v2/models").score(component_id, canonical_crop)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import torch
import torch.nn.functional as F
import torchvision

_MODULE_NAMES = ("conv1", "bn1", "relu", "maxpool", "layer1", "layer2", "layer3", "layer4")
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


# Признаки
class FeatureExtractor:
    """Усечённый ResNet: карты layer2+layer3, усреднение 3x3 -> патч-дескрипторы."""

    def __init__(
        self,
        backbone: str = "resnet18",
        input_size: int = 320,
        layers: Tuple[str, ...] = ("layer2", "layer3"),
        patch_size: int = 3,
        device: str | None = None,
        weights: str | None = "IMAGENET1K_V1",
    ) -> None:
        self.backbone_name = backbone
        self.input_size = int(input_size)
        self.layers = tuple(layers)
        self.patch_size = int(patch_size)
        self.weights = weights
        self.device = torch.device(
            device or ("cuda" if torch.cuda.is_available() else "cpu")
        )
        order = [_MODULE_NAMES.index(n) for n in self.layers]
        if order != sorted(order):
            raise ValueError("layers должны идти в порядке сети")
        net = getattr(torchvision.models, self.backbone_name)(weights=self.weights)
        mods = [
            net.conv1,
            net.bn1,
            net.relu,
            net.maxpool,
            net.layer1,
            net.layer2,
            net.layer3,
            net.layer4,
        ]
        self._mods = torch.nn.ModuleList(mods[: max(order) + 1]).to(self.device).eval()
        for p in self._mods.parameters():
            p.requires_grad_(False)
        self.grid = self.input_size // 8
        self.feature_dim: Optional[int] = None

    def _tensor(self, bgr: np.ndarray) -> torch.Tensor:
        img = cv2.resize(
            bgr, (self.input_size, self.input_size), interpolation=cv2.INTER_AREA
        )
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        t = torch.from_numpy(img).permute(2, 0, 1)
        mean = torch.tensor(IMAGENET_MEAN, dtype=torch.float32)[:, None, None]
        std = torch.tensor(IMAGENET_STD, dtype=torch.float32)[:, None, None]
        return ((t - mean) / std).unsqueeze(0).to(self.device)

    def extract(self, bgr: np.ndarray) -> np.ndarray:
        """BGR (H,W,3) -> (N, C) патч-дескрипторы (N = grid*grid)."""
        return self.extract_batch([bgr])[0]

    def extract_batch(self, images: Sequence[np.ndarray]) -> List[np.ndarray]:
        if not images:
            return []
        tens = torch.cat([self._tensor(im) for im in images], dim=0).to(self.device)
        out: Dict[str, torch.Tensor] = {}
        with torch.no_grad():
            x = tens
            for name, mod in zip(_MODULE_NAMES, self._mods):
                x = mod(x)
                out[name] = x
        base = out[self.layers[0]]
        multi = [base]
        for name in self.layers[1:]:
            f = out[name]
            if f.shape[-2:] != base.shape[-2:]:
                f = F.interpolate(
                    f, size=base.shape[-2:], mode="bilinear", align_corners=False
                )
            multi.append(f)
        f = torch.cat(multi, dim=1)
        pad = self.patch_size // 2
        f = F.avg_pool2d(f, self.patch_size, stride=1, padding=pad)
        feats = f.flatten(2).transpose(1, 2).contiguous().cpu().numpy().astype(np.float32)
        self.feature_dim = int(feats.shape[-1])
        return [feats[i] for i in range(len(images))]

    def describe(self) -> Dict[str, Any]:
        return {
            "backbone": self.backbone_name,
            "input_size": self.input_size,
            "layers": list(self.layers),
            "patch_size": self.patch_size,
            "feature_dim": self.feature_dim,
            "backbone_weights": self.weights,
            "device": str(self.device),
        }


# Банк нормы
def greedy_coreset(
    feats: np.ndarray, n_centers: int, rng: np.random.Generator
) -> np.ndarray:
    """Жадный k-center (как в PatchCore): компактный, но покрывающий банк."""
    n = len(feats)
    if n_centers >= n:
        return feats.copy()
    sq = np.einsum("ij,ij->i", feats, feats)
    first = int(rng.integers(n))
    centers = [first]
    c = feats[first]
    d = np.maximum(sq - 2.0 * feats @ c + float(c @ c), 0.0).astype(np.float32)
    chunk = 8192
    for _ in range(n_centers - 1):
        idx = int(np.argmax(d))
        centers.append(idx)
        c = feats[idx]
        cn = float(c @ c)
        for s in range(0, n, chunk):
            e = min(s + chunk, n)
            dd = sq[s:e] - 2.0 * feats[s:e] @ c + cn
            seg = d[s:e]
            np.minimum(seg, np.maximum(dd, 0.0), out=seg)
    return feats[np.asarray(centers, dtype=np.int64)]


def make_bank(
    pool: np.ndarray,
    rng: np.random.Generator,
    *,
    ratio: float = 0.02,
    max_centers: int = 1200,
    min_centers: int = 64,
) -> np.ndarray:
    n = len(pool)
    n_centers = int(round(n * ratio))
    n_centers = max(min_centers, min(n_centers, max_centers, n))
    return greedy_coreset(pool, n_centers, rng)


def nearest_distances(patches: np.ndarray, bank: np.ndarray) -> np.ndarray:
    """(N,C) -> (N,) расстояние до ближайшего патча банка (float32)."""
    bn = np.einsum("ij,ij->i", bank, bank)
    chunk = max(128, min(4096, int(4_000_000 / max(len(bank), 1))))
    out = np.empty(len(patches), dtype=np.float32)
    for s in range(0, len(patches), chunk):
        e = min(s + chunk, len(patches))
        x = patches[s:e]
        xn = np.einsum("ij,ij->i", x, x)[:, None]
        d = bn[None, :] - 2.0 * (x @ bank.T) + xn
        out[s:e] = np.sqrt(np.maximum(d.min(axis=1), 0.0))
    return out


# Детектор на узел
@dataclass
class AggregationConfig:
    mode: str = "topk_area"          # "max_rel" | "topk_area"
    topk_frac: float = 0.01          # k = 1% патчей
    area_patch_quantile: float = 0.99


class ComponentDetector:
    """Детектор одного узла: банк нормы + агрегация + калиброванные пороги."""

    def __init__(
        self,
        component: str,
        extractor: FeatureExtractor,
        *,
        aggregation: AggregationConfig | None = None,
        tta: bool = True,
        bank_ratio: float = 0.02,
        bank_max: int = 1200,
        max_patches_per_image: int = 250,
        seed: int = 0,
    ) -> None:
        self.component = component
        self.extractor = extractor
        self.agg = aggregation or AggregationConfig()
        self.tta = bool(tta)
        self.bank_ratio = float(bank_ratio)
        self.bank_max = int(bank_max)
        self.max_patches_per_image = int(max_patches_per_image)
        self.seed = int(seed)
        self.bank: Optional[np.ndarray] = None
        self.topk: int = 1
        # калибровка
        self.patch_thr: float = float("nan")
        self.topk_ref: float = 1.0
        self.area_ref: float = 1.0
        self.threshold_q95: float = float("nan")
        self.threshold_q99: float = float("nan")
        self.meta: Dict[str, Any] = {}

    # ------------------------------ обучение ------------------------------ #
    def fit_features(self, per_image: Sequence[np.ndarray], verbose: bool = False) -> "ComponentDetector":
        rng = np.random.default_rng(self.seed)
        per_image = list(per_image)
        if not per_image:
            raise ValueError("fit_features(): пустой набор")
        pool = []
        for p in per_image:
            if self.max_patches_per_image and len(p) > self.max_patches_per_image:
                sel = rng.choice(len(p), size=self.max_patches_per_image, replace=False)
                p = p[np.sort(sel)]
            pool.append(p)
        pool_arr = np.concatenate(pool, axis=0)
        self.bank = make_bank(
            pool_arr, rng, ratio=self.bank_ratio, max_centers=self.bank_max
        )
        self.topk = max(1, int(round(self.agg.topk_frac * len(per_image[0]))))
        self.meta.update(
            {
                "component": self.component,
                "n_train_images": len(per_image),
                "n_pool_patches": int(len(pool_arr)),
                "n_bank_patches": int(len(self.bank)),
                "aggregation": self.agg.mode,
                "topk": self.topk,
                "topk_frac": self.agg.topk_frac,
                "tta": self.tta,
                **self.extractor.describe(),
            }
        )
        if verbose:
            print(
                f"    банк {self.component}: {len(self.bank)} патчей "
                f"из {len(pool_arr)} (top-k={self.topk})"
            )
        return self

    def fit(self, images: Iterable[np.ndarray], verbose: bool = False) -> "ComponentDetector":
        return self.fit_features(self.extract_all(images), verbose=verbose)

    def extract_all(self, images: Iterable[np.ndarray]) -> List[np.ndarray]:
        return [self.extractor.extract(im) for im in images]

    # ------------------------------ скоринг ------------------------------- #
    def _aggregate(self, d: np.ndarray) -> Dict[str, float]:
        flat = d.ravel()
        k = max(1, min(self.topk, len(flat)))
        part = np.partition(flat, -k)[-k:]
        topk_mean = float(part.mean())
        peak = float(flat.max())
        median = float(np.median(flat))
        area = float(np.mean(flat > self.patch_thr)) if np.isfinite(self.patch_thr) else float("nan")
        if self.agg.mode == "max_rel":
            score = peak - median
        else:
            topk_term = topk_mean / max(self.topk_ref, 1e-6)
            area_term = area / max(self.area_ref, 1e-6)
            score = 0.5 * topk_term + 0.5 * area_term
        return {
            "score": float(score),
            "topk_mean": topk_mean,
            "area": area,
            "score_max": peak,
            "score_rel": peak - median,
            "map_median": median,
        }

    def patch_map_from(self, feats: np.ndarray, feats_flip: Optional[np.ndarray] = None) -> np.ndarray:
        """Карта расстояний (grid, grid) из готовых признаков; flip усредняется."""
        g = self.extractor.grid
        d = nearest_distances(feats, self.bank).reshape(g, g)
        if feats_flip is not None:
            d_f = nearest_distances(feats_flip, self.bank).reshape(g, g)[:, ::-1]
            d = 0.5 * (d + d_f)
        return d.astype(np.float32)

    def score_patches(
        self, feats: np.ndarray, feats_flip: Optional[np.ndarray] = None
    ) -> Dict[str, Any]:
        """Агрегация по готовым признакам (+ опционально отражению)."""
        return self._aggregate(self.patch_map_from(feats, feats_flip))

    def score(self, bgr: np.ndarray, *, tta: Optional[bool] = None, heatmap: bool = True) -> Dict[str, Any]:
        t0 = time.perf_counter()
        if self.bank is None:
            raise RuntimeError(f"{self.component}: модель не обучена")
        use_tta = self.tta if tta is None else bool(tta)
        feats = self.extractor.extract(bgr)
        feats_flip = self.extractor.extract(cv2.flip(bgr, 1)) if use_tta else None
        d = self.patch_map_from(feats, feats_flip)
        agg = self._aggregate(d)
        size = self.extractor.input_size
        heat = (
            cv2.resize(d, (size, size), interpolation=cv2.INTER_LINEAR)
            if heatmap
            else None
        )
        out: Dict[str, Any] = {
            "component": self.component,
            "threshold": float(self.threshold_q95),
            "threshold_q99": float(self.threshold_q99),
            "is_anomaly": bool(
                np.isfinite(self.threshold_q95) and agg["score"] > self.threshold_q95
            ),
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            **agg,
        }
        if heat is not None:
            out["heatmap"] = heat
        return out

    def score_features(self, feats: np.ndarray, *, tta: bool = False) -> Dict[str, Any]:
        """Скоринг уже извлечённых признаков (для аблаций: экономит forward)."""
        return self.score_patches(feats, None)

    # ----------------------------- калибровка ----------------------------- #
    def calibrate(
        self,
        features: Sequence[np.ndarray],
        *,
        feats_flip: Optional[Sequence[np.ndarray]] = None,
        q_work: float = 0.95,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Пороги по ЧИСТЫМ кропам, не входившим в банк.

        `feats_flip` — признаки отражённых кропов: при включённом TTA пороги
        считаются на той же (усреднённой) карте, что и на инференсе.
        """
        if not features:
            raise ValueError("calibrate(): пустой набор")
        flips = list(feats_flip) if feats_flip is not None else None
        all_d = []
        for i, f in enumerate(features):
            if flips is not None:
                g = self.extractor.grid
                d = self.patch_map_from(f, flips[i])
                all_d.append(d.ravel())
            else:
                all_d.append(nearest_distances(f, self.bank))
        self.patch_thr = float(
            np.quantile(np.concatenate(all_d), self.agg.area_patch_quantile)
        )

        raw = [
            self.score_patches(f, flips[i] if flips is not None else None)
            for i, f in enumerate(features)
        ]
        topk = np.asarray([r["topk_mean"] for r in raw], dtype=np.float64)
        area = np.asarray([r["area"] for r in raw], dtype=np.float64)
        self.topk_ref = float(max(np.quantile(topk, 0.95), 1e-6))
        self.area_ref = float(max(np.quantile(area, 0.95), 1e-4))

        scores = np.asarray(
            [
                self.score_patches(f, flips[i] if flips is not None else None)["score"]
                for i, f in enumerate(features)
            ],
            dtype=np.float64,
        )
        self.threshold_q95 = float(np.quantile(scores, q_work))
        self.threshold_q99 = float(np.quantile(scores, 0.99))
        stats = {
            "calib_n": int(len(features)),
            "patch_thr_q": float(self.agg.area_patch_quantile),
            "patch_thr": self.patch_thr,
            "topk_ref_q95": self.topk_ref,
            "area_ref_q95": self.area_ref,
            "calib_score_mean": float(scores.mean()),
            "calib_score_std": float(scores.std()),
            "calib_score_p50": float(np.quantile(scores, 0.5)),
            "calib_score_p95": self.threshold_q95,
            "calib_score_p99": self.threshold_q99,
            "calib_topk_mean": float(topk.mean()),
            "calib_area_mean": float(area.mean()),
            "threshold_q95": self.threshold_q95,
            "threshold_q99": self.threshold_q99,
        }
        self.meta.update(stats)
        if verbose:
            print(
                f"    калибровка {self.component}: n={len(features)} "
                f"порог q{int(q_work*100)}={self.threshold_q95:.3f} "
                f"patch_thr={self.patch_thr:.3f} area_ref={self.area_ref:.4f}"
            )
        return stats

    # --------------------------- сохранение ------------------------------ #
    def save(self, path: str | Path) -> Dict[str, str]:
        if self.bank is None:
            raise RuntimeError("нечего сохранять: банк не построен")
        p = Path(path)
        if p.suffix != ".npz":
            p = p.with_suffix(".npz")
        p.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            p,
            memory_bank=self.bank.astype(np.float32),
            patch_thr=np.float32(self.patch_thr),
            topk_ref=np.float32(self.topk_ref),
            area_ref=np.float32(self.area_ref),
            threshold_q95=np.float32(self.threshold_q95),
            threshold_q99=np.float32(self.threshold_q99),
        )
        meta = dict(self.meta)
        meta.update(
            {
                "component": self.component,
                "aggregation": self.agg.mode,
                "topk_frac": self.agg.topk_frac,
                "area_patch_quantile": self.agg.area_patch_quantile,
                "tta": self.tta,
                "bank_ratio": self.bank_ratio,
                "bank_max": self.bank_max,
                "max_patches_per_image": self.max_patches_per_image,
                "seed": self.seed,
                "version": "2.0",
                **self.extractor.describe(),
            }
        )
        meta_path = p.with_suffix(".json")
        with meta_path.open("w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        return {"npz": str(p), "json": str(meta_path)}

    @classmethod
    def load(cls, path: str | Path, *, device: str | None = None) -> "ComponentDetector":
        p = Path(path)
        if p.suffix != ".npz":
            p = p.with_suffix(".npz")
        with np.load(p) as data:
            bank = data["memory_bank"].astype(np.float32)
            patch_thr = float(data["patch_thr"])
            topk_ref = float(data["topk_ref"])
            area_ref = float(data["area_ref"])
            thr95 = float(data["threshold_q95"])
            thr99 = float(data["threshold_q99"])
        meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
        extractor = FeatureExtractor(
            backbone=meta.get("backbone", "resnet18"),
            input_size=meta.get("input_size", 320),
            layers=tuple(meta.get("layers", ("layer2", "layer3"))),
            patch_size=meta.get("patch_size", 3),
            device=device,
            weights=meta.get("backbone_weights", "IMAGENET1K_V1"),
        )
        det = cls(
            component=meta.get("component", p.stem),
            extractor=extractor,
            aggregation=AggregationConfig(
                mode=meta.get("aggregation", "topk_area"),
                topk_frac=meta.get("topk_frac", 0.01),
                area_patch_quantile=meta.get("area_patch_quantile", 0.99),
            ),
            tta=bool(meta.get("tta", True)),
            bank_ratio=meta.get("bank_ratio", 0.02),
            bank_max=meta.get("bank_max", 1200),
            max_patches_per_image=meta.get("max_patches_per_image", 250),
            seed=meta.get("seed", 0),
        )
        det.bank = bank
        det.patch_thr = patch_thr
        det.topk_ref = topk_ref
        det.area_ref = area_ref
        det.threshold_q95 = thr95
        det.threshold_q99 = thr99
        det.topk = int(meta.get("topk", max(1, int(round(det.agg.topk_frac * extractor.grid**2)))))
        det.meta = meta
        return det


# Набор моделей (интеграция с backend)
class AnomalyV2Model:
    """Модели всех узлов + удобный score(component_id, canonical_crop)."""

    def __init__(self, detectors: Dict[str, ComponentDetector]) -> None:
        self.detectors = detectors

    def components(self) -> List[str]:
        return sorted(self.detectors)

    def save(self, directory: str | Path) -> List[str]:
        directory = Path(directory)
        paths = []
        for cid, det in self.detectors.items():
            paths.append(det.save(directory / cid)["npz"])
        manifest = {
            "components": self.components(),
            "version": "2.0",
            "files": {cid: f"{cid}.npz" for cid in self.detectors},
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return paths

    @classmethod
    def load(
        cls, directory: str | Path, *, device: str | None = None, components: Optional[Sequence[str]] = None
    ) -> "AnomalyV2Model":
        directory = Path(directory)
        names = list(components) if components else [
            p.stem for p in sorted(directory.glob("*.npz"))
        ]
        dets = {name: ComponentDetector.load(directory / name, device=device) for name in names}
        if not dets:
            raise FileNotFoundError(f"в {directory} нет моделей *.npz")
        return cls(dets)

    def score(self, component_id: str, canonical_crop: np.ndarray) -> Dict[str, Any]:
        """Оценивает канонический кроп узла.

        Возвращает: score, topk_mean, area, heatmap, threshold, is_anomaly,
        component, latency_ms. Бросает KeyError, если узла нет в модели.
        """
        det = self.detectors.get(component_id)
        if det is None:
            raise KeyError(
                f"нет модели для узла '{component_id}'; есть: {self.components()}"
            )
        return det.score(canonical_crop)


_DEFAULT_DIR = Path(__file__).resolve().parent / "models"
_SINGLETON: Dict[str, AnomalyV2Model] = {}


def score(
    component_id: str,
    canonical_crop: np.ndarray,
    models_dir: str | Path = _DEFAULT_DIR,
) -> Dict[str, Any]:
    """Точка входа для backend: score(component_id, canonical_crop) -> dict.

    Модели грузятся один раз (ленивый синглтон) из `models_dir`.
    """
    key = str(models_dir)
    if key not in _SINGLETON:
        _SINGLETON[key] = AnomalyV2Model.load(models_dir)
    return _SINGLETON[key].score(component_id, canonical_crop)


def overlay_heatmap(bgr: np.ndarray, heatmap: np.ndarray, alpha: float = 0.5) -> np.ndarray:
    """Накладывает карту аномальности на кроп (для отчёта и UI)."""
    h, w = bgr.shape[:2]
    hm = cv2.resize(heatmap, (w, h), interpolation=cv2.INTER_LINEAR)
    lo, hi = float(np.percentile(hm, 50)), float(np.percentile(hm, 99.5))
    hm = np.clip((hm - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    color = cv2.applyColorMap((hm * 255).astype(np.uint8), cv2.COLORMAP_JET)
    return cv2.addWeighted(bgr, 1.0 - alpha, color, alpha, 0)
