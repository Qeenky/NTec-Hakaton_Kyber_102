"""Визуальные признаки дефектов узла по вырезанной ROI — классическое CV.

Без обучения: HSV-сегментация, анализ компонент/моментов контура, Canny,
локальные статистики. Каждый детектор возвращает `DefectScore` с числовым
вкладом каждого признака в `evidence`, поэтому решение можно объяснить.

    detect_rust(roi_bgr)      -> DefectScore  # коррозия
    detect_oil(roi_bgr)       -> DefectScore  # масляный подтёк/пятно
    detect_oxidation(roi_bgr) -> DefectScore  # потемнение/окисление клемм

Все пороги и веса — в `configs/defects.json` (там же комментарии). В функциях
порогов нет: они берутся из конфига, значения по умолчанию — `DEFAULT_CONFIG`
на случай, если JSON потерялся.

Ограничения (см. README): это эвристики для предварительного осмотра.
Рыжая краска и грязная поверхность дают ложные срабатывания на `rust`,
резкая тень — на `oil`; на реальном объекте нужна калибровка под освещение.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "defects.json"

ERROR_BAD_IMAGE = "bad_image"
ERROR_ROI_TOO_SMALL = "roi_too_small"
ERROR_BAD_CONFIG = "bad_config"

# Резервная копия configs/defects.json. Держится в синхроне с файлом;
# нужна, чтобы детекторы не падали, если конфиг не найден.
DEFAULT_CONFIG: dict[str, Any] = {
    "common": {
        "max_side": 384,
        "min_side_px": 24,
        "flat_std": 1.5,
        "edge_low": 40,
        "edge_high": 110,
        "flat_confidence": 0.30,
        "confidence_base": 0.45,
        "confidence_sep_scale": 0.25,
        "confidence_support_floor": 0.85,
        "support_min_frac": 0.01,
    },
    "rust": {
        "threshold": 0.50,
        "hue": [3, 26],
        "sat": [0.30, 1.0],
        "val": [0.08, 0.85],
        "warm_min": 18,
        "min_component_frac": 0.002,
        "frac_full": 0.06,
        "frac_max": 0.80,
        "frac_taper": 0.20,
        "texture_scale": 0.10,
        "grad_start": 1.35,
        "grad_scale": 1.20,
        "hf_start": 1.35,
        "hf_scale": 0.90,
        "shape_scale": 0.30,
        "chroma_scale": 0.25,
        "weights": {"frac": 0.22, "edge": 0.12, "grad": 0.20,
                    "hf": 0.18, "shape": 0.14, "chroma": 0.14},
    },
    "oil": {
        "threshold": 0.60,
        "blur_sigma": 2.0,
        "mad_k": 2.0,
        "min_drop": 0.10,
        "min_component_frac": 0.002,
        "fill_frac": 0.05,
        "area_full": 0.05,
        "area_max": 0.55,
        "area_decay": 0.25,
        "elong_start": 1.25,
        "elong_scale": 1.50,
        "vert_floor": 0.55,
        "glint_min_rise": 0.16,
        "glint_min_ratio": 0.004,
        "glint_max_ratio": 0.60,
        "glint_start": 0.01,
        "glint_scale": 0.06,
        "spread_start": 0.10,
        "spread_scale": 0.25,
        "boundary_start": 1.00,
        "boundary_scale": 1.20,
        "weights": {"area": 0.18, "shape": 0.22, "glint": 0.24,
                    "spread": 0.20, "boundary": 0.16},
    },
    "oxidation": {
        "threshold": 0.52,
        "min_drop": 0.14,
        "min_component_frac": 0.002,
        "area_full": 0.06,
        "area_max": 0.65,
        "area_decay": 0.25,
        "darkening_start": 0.08,
        "darkening_scale": 0.35,
        "desat_scale": 0.35,
        "min_sat_out": 0.06,
        "grain_start": 1.10,
        "grain_scale": 0.90,
        "border_start": 0.15,
        "border_scale": 0.35,
        "border_penalty": 0.35,
        "rust_like_hue": [3, 26],
        "rust_like_sat": 0.45,
        "rust_like_start": 0.10,
        "rust_like_scale": 0.30,
        "rust_like_penalty": 0.60,
        "boundary_start": 2.0,
        "boundary_scale": 1.2,
        "boundary_penalty": 0.50,
        "weights": {"darkening": 0.30, "desat": 0.20, "grain": 0.28,
                    "area": 0.22},
    },
}


@dataclass
class DefectScore:
    """Оценка одного визуального дефекта по ROI."""

    score: float
    present: bool
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Конфиг и утилиты
@lru_cache(maxsize=8)
def _read_config(path: str) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return _merge(DEFAULT_CONFIG, raw)


def _merge(base: dict, extra: dict) -> dict:
    out = {k: (v.copy() if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in extra.items():
        if k.startswith("_") or k == "schema":
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Прочитать и закэшировать конфиг детекторов."""
    p = Path(path) if path is not None else CONFIG_PATH
    try:
        return _read_config(str(p))
    except (OSError, ValueError):
        return _merge(DEFAULT_CONFIG, {})


def to_bgr(image: Any) -> np.ndarray:
    """np/PIL/путь -> BGR uint8. Бросает ValueError на мусоре."""
    if isinstance(image, (str, Path)):
        raw = np.frombuffer(Path(image).read_bytes(), np.uint8)
        decoded = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError(f"cannot decode image: {image}")
        image = decoded
    if hasattr(image, "convert"):  # PIL.Image
        image = np.array(image.convert("RGB"))[:, :, ::-1]
    arr = np.asarray(image)
    if arr.size == 0 or arr.ndim < 2 or arr.shape[0] < 1 or arr.shape[1] < 1:
        raise ValueError("empty image")
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    elif arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    elif arr.shape[2] != 3:
        raise ValueError(f"unsupported channels: {arr.shape[2]}")
    return np.ascontiguousarray(arr)


def _clip01(x: float) -> float:
    return float(np.clip(x, 0.0, 1.0))


def _r(x: float, nd: int = 4) -> float:
    return round(float(x), nd)


def _prepare(roi: Any, cfg: dict) -> tuple[np.ndarray | None, Any, np.ndarray | None,
                                            str | None, bool]:
    """BGR -> (bgr, hsv, gray, error, flat). Ошибки не бросаются наружу."""
    try:
        bgr = to_bgr(roi)
    except Exception:  # noqa: BLE001
        return None, None, None, ERROR_BAD_IMAGE, False
    h, w = bgr.shape[:2]
    com = cfg["common"]
    if min(h, w) < int(com["min_side_px"]):
        return None, None, None, ERROR_ROI_TOO_SMALL, False
    max_side = int(com["max_side"])
    if max(h, w) > max_side:
        sc = max_side / float(max(h, w))
        bgr = cv2.resize(bgr, (max(1, int(round(w * sc))), max(1, int(round(h * sc)))),
                         interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    flat = float(gray.std()) < float(com["flat_std"])
    return bgr, hsv, gray, None, flat


def _frac(mask: np.ndarray) -> float:
    """Доля ненулевых пикселей маски (маска может быть 0/1 или 0/255)."""
    return float(np.count_nonzero(mask)) / float(mask.size)


def _components(mask: np.ndarray, min_px: int) -> tuple[np.ndarray, list[int], np.ndarray]:
    """Бинарная маска -> маска без мелких компонент + номера оставшихся."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros(mask.shape, np.uint8)
    labels: list[int] = []
    for i in range(1, n):
        if int(stats[i, cv2.CC_STAT_AREA]) >= min_px:
            keep[lab == i] = 255
            labels.append(i)
    return keep, labels, lab


def _edges(gray: np.ndarray, cfg: dict) -> np.ndarray:
    com = cfg["common"]
    g8 = np.clip(gray, 0, 255).astype(np.uint8)
    blur = cv2.GaussianBlur(g8, (3, 3), 0.8)
    return cv2.Canny(blur, float(com["edge_low"]), float(com["edge_high"])) > 0


def _empty(error: str | None, cfg: dict, evidence: dict) -> DefectScore:
    flat_conf = float(cfg["common"]["flat_confidence"])
    return DefectScore(0.0, False, flat_conf if error is None else 0.0,
                       evidence, error)


def _confidence(score: float, thr: float, support_q: float, cfg: dict) -> float:
    com = cfg["common"]
    base = float(com["confidence_base"])
    sep = _clip01(abs(score - thr) / max(1e-6, float(com["confidence_sep_scale"])))
    floor = float(com["confidence_support_floor"])
    return _clip01((base + (1.0 - base) * sep) * (floor + (1.0 - floor) * support_q))


def _weighted(parts: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(float(weights.get(k, 0.0)) for k in parts)
    if total <= 0:
        return 0.0
    return _clip01(sum(float(weights.get(k, 0.0)) * v for k, v in parts.items()) / total)


# 1. Ржавчина
def _rust_evidence(bgr: np.ndarray, hsv: np.ndarray, gray: np.ndarray,
                   cfg: dict) -> tuple[float, dict]:
    rc = cfg["rust"]
    h = hsv[..., 0].astype(np.int16)
    s = hsv[..., 1].astype(np.float32) / 255.0
    v = hsv[..., 2].astype(np.float32) / 255.0
    blue = bgr[..., 0].astype(np.int16)
    red = bgr[..., 2].astype(np.int16)
    hlo, hhi = (int(x) for x in rc["hue"])
    slo, shi = (float(x) for x in rc["sat"])
    vlo, vhi = (float(x) for x in rc["val"])
    raw = ((h >= hlo) & (h <= hhi) & (s >= slo) & (s <= shi) &
           (v >= vlo) & (v <= vhi) & ((red - blue) >= int(rc["warm_min"])))
    min_px = max(8, int(round(float(rc["min_component_frac"]) * raw.size)))
    mask, labels, lab = _components(raw.astype(np.uint8), min_px)
    area = float(mask.size)
    frac = _frac(mask)
    ev: dict[str, Any] = {"mask_frac": _r(frac), "components": len(labels),
                          "min_component_px": min_px}
    if not labels or frac <= 0:
        ev.update({"reason": "no_rust_colored_pixels"})
        return 0.0, ev

    # Текстура: ржавчина «пятнистая» — внутри маски рёбер и градиента
    # заметно больше фона (Canny + относительная энергия градиента).
    edges = _edges(gray, cfg)
    in_mask = mask > 0
    dens_in = float(edges[in_mask].mean())
    dens_out = float(edges[~in_mask].mean()) if (~in_mask).any() else 0.0
    q_edge = _clip01((dens_in - dens_out) / float(rc["texture_scale"]))

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gmag = np.hypot(gx, gy)
    g_in = float(gmag[in_mask].mean())
    g_out = float(gmag[~in_mask].mean()) if (~in_mask).any() else g_in
    grad_ratio = g_in / max(g_out, 1e-3)
    q_grad = _clip01((grad_ratio - float(rc["grad_start"])) /
                     float(rc["grad_scale"]))

    hp = gray - cv2.GaussianBlur(gray, (0, 0), 2.0)
    hf_in = float(hp[in_mask].std())
    hf_out = float(hp[~in_mask].std()) if (~in_mask).any() else hf_in
    hf_ratio = hf_in / max(hf_out, 1e-3)
    q_hf = _clip01((hf_ratio - float(rc["hf_start"])) /
                   float(rc["hf_scale"]))

    # Форма: неровные края (отношение площади выпуклой оболочки к площади).
    ratios, weights = [], []
    for i in labels:
        sub = (lab == i).astype(np.uint8)
        cnts, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        a = float(cv2.contourArea(c))
        if a < 20:
            continue
        ha = float(cv2.contourArea(cv2.convexHull(c)))
        ratios.append(ha / max(a, 1.0))
        weights.append(a)
    hull_ratio = float(np.average(ratios, weights=weights)) if ratios else 1.0
    q_shape = _clip01((hull_ratio - 1.0) / float(rc["shape_scale"]))

    # Насыщенность: ржавчина теплее и насыщеннее ровной краски.
    s_in = float(s[in_mask].mean())
    s_out = float(s[~in_mask].mean()) if (~in_mask).any() else 0.0
    q_chroma = _clip01((s_in - s_out) / float(rc["chroma_scale"]))

    # Локальная неоднородность яркости внутри пятна (диагностический признак,
    # в итоговый score не входит: на синтетике не дал прироста).
    v_in = v[in_mask]
    v_out = v[~in_mask] if (~in_mask).any() else v_in
    std_in = float(v_in.std())
    std_out = float(v_out.std())

    # Площадь: 5-10 % ROI уже достаточно; почти весь ROI одного цвета — подозрение
    # на равномерную рыжую краску, а не на коррозию.
    frac_q = _clip01(frac / float(rc["frac_full"]))
    frac_q *= _clip01((float(rc["frac_max"]) - frac) /
                      float(rc["frac_taper"]))

    parts = {"frac": frac_q, "edge": q_edge, "grad": q_grad, "hf": q_hf,
             "shape": q_shape, "chroma": q_chroma}
    score = _weighted(parts, rc["weights"])
    ev.update({
        "edge_density_in": _r(dens_in), "edge_density_out": _r(dens_out),
        "grad_ratio": _r(grad_ratio), "hf_ratio": _r(hf_ratio),
        "hull_ratio": _r(hull_ratio), "sat_in": _r(s_in), "sat_out": _r(s_out),
        "std_v_in": _r(std_in), "std_v_out": _r(std_out),
        "largest_comp_frac": _r(max(
            [float((lab == i).sum()) / area for i in labels] or [0.0])),
        "parts": {k: _r(x) for k, x in parts.items()},
        "score": _r(score), "threshold": rc["threshold"],
    })
    return score, ev


def detect_rust(roi_bgr: Any, config: dict | None = None) -> DefectScore:
    """Коррозия: HSV-диапазон + пятнистость + неровная форма + хрома.

    Возвращает DefectScore; на битом ROI — error, без исключений.
    """
    cfg = config or load_config()
    try:
        rc = cfg["rust"]
        bgr, hsv, gray, err, flat = _prepare(roi_bgr, cfg)
        if bgr is None:
            reason = "empty_or_unreadable" if err == ERROR_BAD_IMAGE else "too_small"
            return _empty(err, cfg, {"reason": reason})
        if flat:
            return _empty(None, cfg, {"reason": "flat_roi",
                                      "std": _r(float(gray.std()), 3)})
        score, ev = _rust_evidence(bgr, hsv, gray, cfg)
        mask_frac = float(ev.get("mask_frac", 0.0))
        support_q = _clip01(mask_frac / float(cfg["common"]["support_min_frac"]))
        thr = float(rc["threshold"])
        return DefectScore(score, bool(score >= thr),
                           _confidence(score, thr, support_q, cfg), ev, None)
    except Exception as exc:  # noqa: BLE001
        return DefectScore(0.0, False, 0.0, {"reason": "internal_error",
                                            "detail": str(exc)}, ERROR_BAD_CONFIG)


# 2. Масло / подтёк
def _shape_moments(binary: np.ndarray) -> tuple[float, float, float]:
    """(удлинение, |sin(угла к горизонту)|, доля площади) бинарного пятна."""
    m = cv2.moments(binary, binaryImage=True)
    if m["m00"] <= 0:
        return 1.0, 0.0, 0.0
    mu20 = m["mu20"] / m["m00"]
    mu02 = m["mu02"] / m["m00"]
    mu11 = m["mu11"] / m["m00"]
    common = 0.5 * (mu20 + mu02)
    diff = math.sqrt(max(0.0, 0.25 * (mu20 - mu02) ** 2 + mu11 ** 2))
    l1, l2 = common + diff, max(common - diff, 1e-6)
    elong = math.sqrt(max(l1 / l2, 1.0))
    theta = 0.5 * math.atan2(2.0 * mu11, mu20 - mu02)
    vert = abs(math.sin(theta))
    return float(elong), float(vert), float(m["m00"]) / float(binary.size)


def _blob_region(mask: np.ndarray, fill_frac: float) -> np.ndarray:
    """Пятно целиком: дилатация -> заливка контура -> эрозия.

    Нужно, чтобы яркие глянцевые блики (они не «тёмные») не выпадали из пятна:
    иначе блеск ищется только по тёмным пикселям и всегда равен нулю.
    """
    h, w = mask.shape[:2]
    k = max(3, int(round(fill_frac * min(h, w))))
    if k % 2 == 0:
        k += 1
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    d = cv2.dilate(mask, ker)
    cnts, _ = cv2.findContours(d, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return mask
    filled = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(filled, [max(cnts, key=cv2.contourArea)], -1, 255, -1)
    # Возвращаем радиус не целиком: небольшой запас нужен, чтобы захватить
    # блики на кромке пятна.
    k2 = max(3, (k // 2) | 1)
    ker2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k2, k2))
    return cv2.erode(filled, ker2)


def _oil_evidence(hsv: np.ndarray, gray: np.ndarray, cfg: dict) -> tuple[float, dict]:
    oc = cfg["oil"]
    v = hsv[..., 2].astype(np.float32)
    sigma = float(oc["blur_sigma"])
    v_blur = cv2.GaussianBlur(v, (0, 0), sigma)
    med = float(np.median(v_blur))
    mad = float(np.median(np.abs(v_blur - med)))
    thr = min(med - float(oc["mad_k"]) * max(mad, 1.0),
              med * (1.0 - float(oc["min_drop"])))
    dark = (v_blur < thr).astype(np.uint8)
    min_px = max(8, int(round(float(oc["min_component_frac"]) * v.size)))
    mask, labels, lab = _components(dark, min_px)
    frac = _frac(mask)
    ev: dict[str, Any] = {"mask_frac": _r(frac), "components": len(labels),
                          "median_v": _r(med, 1), "dark_threshold": _r(thr, 1)}
    if not labels or frac <= 0:
        ev["reason"] = "no_dark_blob"
        return 0.0, ev

    # Пятно целиком: заливаем контур (с дилатацией), чтобы глянцевые блики
    # не выпадали из маски и участвовали в оценке блеска.
    spot = _blob_region(mask, float(oc["fill_frac"]))
    spot_frac = _frac(spot)
    elong, vert, _ = _shape_moments(spot)
    shape_q = _clip01((elong - float(oc["elong_start"])) /
                      float(oc["elong_scale"]))
    shape_q *= float(oc["vert_floor"]) + (1.0 - float(oc["vert_floor"])) * vert

    area_q = _clip01(spot_frac / float(oc["area_full"]))
    area_q *= _clip01((float(oc["area_max"]) - spot_frac) /
                      float(oc["area_decay"]))

    # Глянец: внутри/на кромке пятна есть компактные яркие блики, заметно
    # светлее фона. Соседняя освещённая поверхность (у тени) — большая
    # компонента и отбрасывается по относительной площади.
    vals = v[spot > 0]
    out = v[spot == 0] if (spot == 0).any() else vals
    p05, p50, p95 = (float(np.percentile(vals, q)) for q in (5, 50, 95))
    base = float(np.median(out))
    rise = float(oc["glint_min_rise"]) * 255.0
    bright = ((v > base + rise) & (spot == 0)).astype(np.uint8)
    nb, lb, stats, _ = cv2.connectedComponentsWithStats(bright, 8)
    near = cv2.dilate(spot, np.ones((5, 5), np.uint8)) > 0
    dark_px = max(1, int(np.count_nonzero(spot)))
    glint_mask = np.zeros(spot.shape, bool)
    glint_px = 0
    for i in range(1, nb):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < dark_px * float(oc["glint_min_ratio"]):
            continue
        if a > dark_px * float(oc["glint_max_ratio"]):
            continue
        comp = lb == i
        if not bool(np.any(comp & near)):
            continue
        glint_mask |= comp
        glint_px += a
    glint_frac = glint_px / float(dark_px)
    q_glint = _clip01((glint_frac - float(oc["glint_start"])) /
                      float(oc["glint_scale"]))
    if glint_px:
        vals = v[(spot > 0) | glint_mask]
        p05, p95 = (float(np.percentile(vals, q)) for q in (5, 95))
    spread = (p95 - p05) / 255.0
    q_spread = _clip01((spread - float(oc["spread_start"])) /
                       float(oc["spread_scale"]))

    # Граница пятна: у масла кромка резкая, у размытой тени — плавная.
    gb = cv2.GaussianBlur(v, (0, 0), 2.0)
    gx = cv2.Sobel(gb, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gb, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.hypot(gx, gy)
    ring = cv2.dilate(spot, np.ones((5, 5), np.uint8)) > 0
    ring &= spot == 0
    inner = cv2.erode(spot, np.ones((5, 5), np.uint8)) > 0
    b_edge = float(grad[ring].mean()) if ring.any() else 0.0
    i_edge = float(grad[inner].mean()) if inner.any() else 0.0
    g_roi = float(grad.mean()) + 1e-3
    ratio = b_edge / max(g_roi, 1.0)
    q_boundary = _clip01((ratio - float(oc["boundary_start"])) /
                         float(oc["boundary_scale"]))

    parts = {"area": area_q, "shape": shape_q, "glint": q_glint,
             "spread": q_spread, "boundary": q_boundary}
    score = _weighted(parts, oc["weights"])
    ev.update({
        "spot_frac": _r(spot_frac), "elongation": _r(elong),
        "verticalness": _r(vert),
        "v_p05": _r(p05, 1), "v_p95": _r(p95, 1), "v_base": _r(base, 1),
        "glint_frac": _r(glint_frac), "spread": _r(spread),
        "boundary_grad": _r(b_edge, 2), "inner_grad": _r(i_edge, 2),
        "boundary_ratio": _r(ratio, 3),
        "parts": {k: _r(x) for k, x in parts.items()},
        "score": _r(score), "threshold": oc["threshold"],
    })
    return score, ev


def detect_oil(roi_bgr: Any, config: dict | None = None) -> DefectScore:
    """Масляный подтёк: тёмное пятно + глянец + вытянутость + резкая кромка."""
    cfg = config or load_config()
    try:
        oc = cfg["oil"]
        bgr, hsv, gray, err, flat = _prepare(roi_bgr, cfg)
        if bgr is None:
            reason = "empty_or_unreadable" if err == ERROR_BAD_IMAGE else "too_small"
            return _empty(err, cfg, {"reason": reason})
        if flat:
            return _empty(None, cfg, {"reason": "flat_roi",
                                      "std": _r(float(gray.std()), 3)})
        score, ev = _oil_evidence(hsv, gray, cfg)
        support_q = _clip01(float(ev.get("mask_frac", 0.0)) /
                            float(cfg["common"]["support_min_frac"]))
        thr = float(oc["threshold"])
        return DefectScore(score, bool(score >= thr),
                           _confidence(score, thr, support_q, cfg), ev, None)
    except Exception as exc:  # noqa: BLE001
        return DefectScore(0.0, False, 0.0, {"reason": "internal_error",
                                            "detail": str(exc)}, ERROR_BAD_CONFIG)


# 3. Окисление / потемнение клемм
def _oxidation_evidence(hsv: np.ndarray, gray: np.ndarray,
                        cfg: dict) -> tuple[float, dict]:
    xc = cfg["oxidation"]
    v = hsv[..., 2].astype(np.float32)
    s = hsv[..., 1].astype(np.float32) / 255.0
    med = float(np.median(v))
    thr = med * (1.0 - float(xc["min_drop"]))
    dark = (v < thr).astype(np.uint8)
    min_px = max(8, int(round(float(xc["min_component_frac"]) * v.size)))
    mask, labels, lab = _components(dark, min_px)
    frac = _frac(mask)
    ev: dict[str, Any] = {"mask_frac": _r(frac), "components": len(labels),
                          "median_v": _r(med, 1), "dark_threshold": _r(thr, 1)}
    if not labels or frac <= 0:
        ev["reason"] = "no_local_darkening"
        return 0.0, ev

    in_mask = mask > 0
    out_mask = ~in_mask
    v_in = v[in_mask]
    v_out = v[out_mask] if out_mask.any() else v_in
    darkening = 1.0 - float(v_in.mean()) / max(float(np.median(v_out)), 1.0)
    q_dark = _clip01((darkening - float(xc["darkening_start"])) /
                     float(xc["darkening_scale"]))

    s_in = float(s[in_mask].mean())
    s_out = float(s[out_mask].mean()) if out_mask.any() else s_in
    # На почти сером металле относительная потеря насыщенности ничего не значит.
    if s_out < float(xc["min_sat_out"]):
        desat, q_desat = 0.0, 0.0
        desat_note = "low_base_saturation"
    else:
        desat = 1.0 - s_in / max(s_out, 1e-3)
        q_desat = _clip01(desat / float(xc["desat_scale"]))
        desat_note = None

    # Зернистость: высокочастотная энергия, нормированная на яркость.
    lap = np.abs(cv2.Laplacian(v, cv2.CV_32F, ksize=3))
    r_in = float(lap[in_mask].mean()) / max(float(v_in.mean()), 1.0)
    r_out = float(lap[out_mask].mean()) / max(float(v_out.mean()), 1.0) \
        if out_mask.any() else r_in
    grain = r_in / max(r_out, 1e-4)
    q_grain = _clip01((grain - float(xc["grain_start"])) /
                      float(xc["grain_scale"]))

    area_q = _clip01(frac / float(xc["area_full"]))
    area_q *= _clip01((float(xc["area_max"]) - frac) /
                      float(xc["area_decay"]))

    # Пятно окисления локальное: если тёмная маска упирается в границы ROI,
    # это скорее общая тень/недосвет -> мягкий штраф.
    border = max(2, int(round(0.02 * min(v.shape))))
    edge_mask = np.zeros(mask.shape, bool)
    edge_mask[:border, :] = True
    edge_mask[-border:, :] = True
    edge_mask[:, :border] = True
    edge_mask[:, -border:] = True
    border_frac = _frac(mask[edge_mask] > 0) if edge_mask.any() else 0.0
    border_q = _clip01((border_frac - float(xc["border_start"])) /
                       float(xc["border_scale"]))
    locality = 1.0 - float(xc["border_penalty"]) * border_q

    # Резкая кромка тёмного пятна — это скорее масло/краска (у окисления
    # граница диффузная). Считаем по сглаженному V, чтобы не ловить шум.
    gb = cv2.GaussianBlur(v, (0, 0), 2.0)
    grad = np.hypot(cv2.Sobel(gb, cv2.CV_32F, 1, 0, ksize=3),
                    cv2.Sobel(gb, cv2.CV_32F, 0, 1, ksize=3))
    ring = (cv2.dilate(mask, np.ones((5, 5), np.uint8)) > 0) & (mask == 0)
    b_edge = float(grad[ring].mean()) if ring.any() else 0.0
    g_roi = float(grad.mean()) + 1e-3
    boundary_ratio = b_edge / max(g_roi, 1.0)
    sharp_q = _clip01((boundary_ratio - float(xc["boundary_start"])) /
                      float(xc["boundary_scale"]))
    edge_gate = 1.0 - float(xc["boundary_penalty"]) * sharp_q

    # Рыже-коричневое потемнение — это коррозия (rust), а не окисление клемм:
    # оксидная плёнка на контактах серая/зеленоватая и теряет насыщенность.
    rhlo, rhhi = (int(x) for x in xc["rust_like_hue"])
    rust_like = _frac((hsv[..., 0][in_mask] >= rhlo) &
                      (hsv[..., 0][in_mask] <= rhhi) &
                      (s[in_mask] > float(xc["rust_like_sat"])))
    rust_q = _clip01((rust_like - float(xc["rust_like_start"])) /
                     float(xc["rust_like_scale"]))
    rust_gate = 1.0 - float(xc["rust_like_penalty"]) * rust_q

    parts = {"darkening": q_dark, "desat": q_desat, "grain": q_grain,
             "area": area_q}
    score = _weighted(parts, xc["weights"]) * locality * rust_gate * edge_gate
    ev.update({
        "darkening": _r(darkening), "desat": _r(desat),
        "desat_note": desat_note,
        "grain_ratio": _r(grain), "grain_in": _r(r_in), "grain_out": _r(r_out),
        "border_frac": _r(border_frac), "locality": _r(locality),
        "boundary_ratio": _r(boundary_ratio), "edge_gate": _r(edge_gate),
        "rust_like_frac": _r(rust_like), "rust_gate": _r(rust_gate),
        "parts": {k: _r(x) for k, x in parts.items()},
        "score": _r(score), "threshold": xc["threshold"],
    })
    return score, ev


def detect_oxidation(roi_bgr: Any, config: dict | None = None) -> DefectScore:
    """Окисление: локальное потемнение + потеря насыщенности + зернистость."""
    cfg = config or load_config()
    try:
        xc = cfg["oxidation"]
        bgr, hsv, gray, err, flat = _prepare(roi_bgr, cfg)
        if bgr is None:
            reason = "empty_or_unreadable" if err == ERROR_BAD_IMAGE else "too_small"
            return _empty(err, cfg, {"reason": reason})
        if flat:
            return _empty(None, cfg, {"reason": "flat_roi",
                                      "std": _r(float(gray.std()), 3)})
        score, ev = _oxidation_evidence(hsv, gray, cfg)
        support_q = _clip01(float(ev.get("mask_frac", 0.0)) /
                            float(cfg["common"]["support_min_frac"]))
        thr = float(xc["threshold"])
        return DefectScore(score, bool(score >= thr),
                           _confidence(score, thr, support_q, cfg), ev, None)
    except Exception as exc:  # noqa: BLE001
        return DefectScore(0.0, False, 0.0, {"reason": "internal_error",
                                            "detail": str(exc)}, ERROR_BAD_CONFIG)


# Пакетный вызов для backend
DETECTORS = {"rust": detect_rust, "oil": detect_oil,
             "oxidation": detect_oxidation}


def detect_defects(roi_bgr: Any, config: dict | None = None) -> dict[str, dict]:
    """Все три детектора по одной ROI -> {имя: dict} (готово для JSON)."""
    cfg = config or load_config()
    return {name: fn(roi_bgr, cfg).as_dict() for name, fn in DETECTORS.items()}
