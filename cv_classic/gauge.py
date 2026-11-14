"""Чтение показания аналогового манометра классическим CV.

Схема углов (везде, включая конфиг):
    0°  — вправо (3 часа);
    +90° — вверх; 180° — влево; -90°/270° — вниз;
    положительное направление — против часовой стрелки.
Типовой манометр: 0 бар при 225° (низ-лево), конец шкалы при -45° (низ-право),
развёртка 270° по часовой стрелке -> value растёт при уменьшении угла.

Пайплайн (каждый шаг деградирует мягко, исключений наружу нет):
    1) препроцессинг (gray + blur + CLAHE при необходимости);
    2) поиск циферблата: HoughCircles (сетка параметров) + скоринг,
       fallback — самый круглый/контрастный контур;
    3) нормализация: вырезать круг, масштаб к 512x512, оценка поворота по
       "пустому" сектору шкалы (при auto_orient);
    4) поиск стрелки: маска "тёмное + красное", HoughLinesP через центр и
       профиль длины радиальных пробегов по углам, два порога -> стабильность;
    5) угол -> значение линейной интерполяцией по конфигу шкалы;
    6) confidence из качества круга, длины стрелки и стабильности угла.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

NORM_SIZE = 512
R_OUT = NORM_SIZE * 0.47  # радиус циферблата в нормализованном изображении

ERROR_BAD_IMAGE = "bad_image"
ERROR_NO_DIAL = "no_dial"
ERROR_NO_NEEDLE = "no_needle"
ERROR_NEEDLE_SHORT = "needle_too_short"
ERROR_BAD_CONFIG = "bad_config"
ERROR_LOW_CONFIDENCE = "low_confidence"
ERROR_INTERNAL = "internal_error"


# Конфиг
@dataclass(frozen=True)
class GaugeConfig:
    """Шкала конкретного прибора. Углы в градусах, 0° — вправо, CCW+."""

    angle_min: float = 225.0  # угол значения value_min
    angle_max: float = -45.0  # угол значения value_max
    value_min: float = 0.0
    value_max: float = 60.0
    unit: str = "bar"
    clockwise: bool = True
    center: tuple[float, float] | None = None
    radius: float | None = None
    auto_orient: bool = False  # компенсировать поворот прибора по шкале
    target_size: int = NORM_SIZE
    min_needle_frac: float = 0.30
    min_confidence: float = 0.15
    image_path: str | None = None
    keep_debug_image: bool = False

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        if d["center"] is not None:
            d["center"] = list(d["center"])
        return d

    @classmethod
    def from_json(cls, path: str | Path) -> "GaugeConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GaugeConfig":
        known = {f for f in cls.__dataclass_fields__}
        kw = {k: v for k, v in raw.items() if k in known}
        if kw.get("center") is not None:
            kw["center"] = (float(kw["center"][0]), float(kw["center"][1]))
        for k in ("angle_min", "angle_max", "value_min", "value_max"):
            if k in kw:
                kw[k] = float(kw[k])
        if kw.get("radius") is not None:
            kw["radius"] = float(kw["radius"])
        return cls(**kw)


@dataclass
class GaugeReading:
    """Результат чтения."""

    value: float | None
    unit: str
    angle_deg: float | None
    confidence: float
    method: str
    debug: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Утилиты
def _to_bgr(image: Any) -> np.ndarray:
    """PIL / np / путь -> BGR uint8. Бросает ValueError на мусоре."""
    if isinstance(image, (str, Path)):
        raw = np.frombuffer(Path(image).read_bytes(), np.uint8)
        decoded = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError(f"cannot decode image: {image}")
        image = decoded
    if hasattr(image, "convert"):  # PIL.Image
        image = np.array(image.convert("RGB"))[:, :, ::-1]
    arr = np.asarray(image)
    if arr.size == 0 or arr.ndim < 2:
        raise ValueError("empty image")
    if arr.dtype != np.uint8:
        if np.issubdtype(arr.dtype, np.floating) and arr.max() <= 1.0:
            arr = arr * 255.0  # изображение в диапазоне 0..1
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    elif arr.shape[2] == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    return np.ascontiguousarray(arr)


def _norm360(a: float) -> float:
    return float(a) % 360.0


def _ang_diff(a: float, b: float) -> float:
    """Минимальная разница углов в градусах, 0..180."""
    d = abs(_norm360(a) - _norm360(b))
    return min(d, 360.0 - d)


def _edge_map(gray: np.ndarray) -> np.ndarray:
    """Canny с порогами, адаптированными к контрасту кадра."""
    blur = cv2.GaussianBlur(gray, (5, 5), 1.2)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    med = float(np.median(np.hypot(gx, gy)))
    lo = max(8.0, 0.55 * med)
    hi = max(26.0, 1.40 * med)
    return cv2.Canny(blur, lo, hi)


# Шаг 2: поиск циферблата
@dataclass
class _Dial:
    cx: float
    cy: float
    r: float
    score: float
    method: str


def _disk(gray: np.ndarray, cx: float, cy: float, r: float) -> np.ndarray:
    m = np.zeros(gray.shape, np.uint8)
    cv2.circle(m, (int(round(cx)), int(round(cy))), max(1, int(round(r))), 255, -1)
    return m


def _circle_score(gray: np.ndarray, edges_d: np.ndarray, cx: float, cy: float,
                  r: float, min_dim: int) -> float:
    """Правдоподобие того, что окружность — циферблат (не зависит от яркости).

    Слагаемые: поддержка рёбрами при реальном перепаде яркости, относительный
    контраст лицо/корпус, однородность лица, тёмная ступица, наличие рисок.
    """
    if r < 0.10 * min_dim or r > 0.60 * min_dim:
        return 0.0
    h, w = gray.shape
    th = np.linspace(0, 2 * math.pi, 240, endpoint=False)
    xs = np.round(cx + r * np.cos(th)).astype(int)
    ys = np.round(cy - r * np.sin(th)).astype(int)
    inside = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    inside_frac = float(inside.mean())
    if inside_frac < 0.85:  # круг должен целиком быть в кадре
        return 0.0
    ok = 0
    for rad in (r - 1.5, r, r + 1.5):
        xs = np.round(cx + rad * np.cos(th)).astype(int)
        ys = np.round(cy - rad * np.sin(th)).astype(int)
        ins = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        ok += int((edges_d[ys[ins], xs[ins]] > 0).sum())
    cov = ok / 720.0

    m_face = _disk(gray, cx, cy, r * 0.62)
    mean_face, std_face = cv2.meanStdDev(gray, mask=m_face)
    face = float(mean_face[0][0])
    std_face_f = float(std_face[0][0])
    outer_sum = float(cv2.mean(gray, _disk(gray, cx, cy, r * 1.10))[0])
    outer = (outer_sum * 1.10 ** 2 - face * 1.02 ** 2) / (1.10 ** 2 - 1.02 ** 2)
    dif = abs(face - outer)
    rel = dif / max(1.0, face)

    hub = float(cv2.mean(gray, _disk(gray, cx, cy, max(2.0, r * 0.06)))[0])
    ann = cv2.bitwise_and(_disk(gray, cx, cy, r * 0.93),
                          cv2.bitwise_not(_disk(gray, cx, cy, r * 0.78)))
    vals = gray[ann > 0]
    tick_frac = float((vals < 0.5 * (face + outer)).mean()) if vals.size else 0.0

    contrast_q = min(1.0, rel / 0.22)
    lift_q = float(np.clip((face - outer) / max(20.0, 0.25 * face), 0.0, 1.0))
    uniform_q = float(np.clip(1.0 - std_face_f / max(12.0, 0.9 * max(dif, 12.0)),
                              0.0, 1.0))
    hub_q = float(np.clip((face - hub) / max(25.0, 0.4 * face), 0.0, 1.0))
    ticks_q = float(np.clip(tick_frac / 0.06, 0.0, 1.0)) * (
        1.0 if tick_frac < 0.65 else 0.4)
    gate = min(1.0, rel / 0.15)  # рёбра без перепада яркости ничего не значат
    return (0.44 * cov * gate + 0.16 * contrast_q + 0.12 * lift_q +
            0.14 * uniform_q + 0.08 * hub_q + 0.06 * ticks_q)


def _hough_candidates(gray: np.ndarray) -> list[_Dial]:
    """Перебор параметров HoughCircles; изображение предварительно уменьшено."""
    h, w = gray.shape
    min_dim = min(h, w)
    scale = 1.0
    work = gray
    if min_dim > 720:
        scale = 720.0 / min_dim
        work = cv2.resize(gray, (int(w * scale), int(h * scale)),
                          interpolation=cv2.INTER_AREA)
    work = cv2.GaussianBlur(work, (5, 5), 1.3)
    wd = min(work.shape)
    rmin, rmax = int(wd * 0.12), int(wd * 0.54)
    out: list[_Dial] = []
    for dp in (1.0, 1.5):
        for p1 in (80, 130):
            for p2 in (28, 42, 60):
                circles = cv2.HoughCircles(
                    work, cv2.HOUGH_GRADIENT, dp, wd * 0.10,
                    param1=p1, param2=p2, minRadius=rmin, maxRadius=rmax)
                if circles is None:
                    continue
                for c in circles[0][:12]:
                    x, y, r = float(c[0]), float(c[1]), float(c[2])
                    out.append(_Dial(x / scale, y / scale, r / scale, 0.0,
                                     f"hough(dp={dp},p1={p1},p2={p2})"))
    return out


def _contour_candidate(gray: np.ndarray) -> _Dial | None:
    """Fallback: самый контрастный круглый контур (циферблат — светлое лицо)."""
    blur = cv2.GaussianBlur(gray, (7, 7), 1.5)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    img_area = float(gray.size)
    best: _Dial | None = None
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 0.03 * img_area:
            continue
        per = cv2.arcLength(c, True) + 1e-6
        circ = 4 * math.pi * area / (per * per)
        if circ < 0.55:
            continue
        (x, y), r = cv2.minEnclosingCircle(c)
        if r < 0.08 * min(gray.shape):
            continue
        score = 0.5 * min(1.0, circ) + 0.5 * min(1.0, area / (math.pi * r * r))
        if best is None or score > best.score:
            best = _Dial(x, y, r, score, "contour")
    return best


def _bright_blob_candidate(gray: np.ndarray) -> _Dial | None:
    """Кандидат по самому большому светлому пятну (лицо циферблата)."""
    blur = cv2.GaussianBlur(gray, (9, 9), 2.0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best: _Dial | None = None
    best_area = 0.0
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 0.05 * gray.size:
            continue
        (x, y), r = cv2.minEnclosingCircle(c)
        if r < 0.08 * min(gray.shape):
            continue
        fill = area / max(1.0, math.pi * r * r)
        if fill < 0.72:
            continue
        if area > best_area:
            best_area = area
            best = _Dial(float(x), float(y), float(r), 0.0, "bright_blob")
    return best


def _circle_fit(pts: np.ndarray) -> tuple[float, float, float]:
    """МНК-подгонка окружности (Kasa): x^2+y^2+Dx+Ey+F=0."""
    x, y = pts[:, 0], pts[:, 1]
    a = np.stack([x, y, np.ones_like(x)], axis=1)
    b = -(x * x + y * y)
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    d, e, f = float(sol[0]), float(sol[1]), float(sol[2])
    cx, cy = -d / 2.0, -e / 2.0
    r = math.sqrt(max(1e-6, cx * cx + cy * cy - f))
    return cx, cy, r


def _refine_dial_center(gray: np.ndarray, dial: _Dial) -> _Dial:
    """Уточнить центр/радиус по градиенту лицо->корпус вдоль 180 лучей."""
    n_rays = 180
    rs = np.arange(0.86, 1.10, 0.004) * dial.r
    th = np.linspace(0, 2 * math.pi, n_rays, endpoint=False)
    cos_t, sin_t = np.cos(th), np.sin(th)
    mx = (dial.cx + cos_t[:, None] * rs[None, :]).astype(np.float32)
    my = (dial.cy - sin_t[:, None] * rs[None, :]).astype(np.float32)
    prof = cv2.remap(gray, mx, my, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)
    grad = np.diff(prof.astype(np.float32), axis=1)
    idx = np.argmin(grad, axis=1)
    strength = grad[np.arange(n_rays), idx]
    ok = strength < -6.0
    if ok.sum() < 40:
        return dial
    r_edge = rs[idx] + 0.002 * dial.r
    r_edge = r_edge[ok]
    pts = np.stack([dial.cx + cos_t[ok] * r_edge,
                    dial.cy - sin_t[ok] * r_edge], axis=1)
    cx, cy, r = _circle_fit(pts)
    if abs(cx - dial.cx) > 0.15 * dial.r or abs(cy - dial.cy) > 0.15 * dial.r:
        return dial
    if not (0.90 * dial.r <= r <= 1.10 * dial.r):
        return dial
    resid = np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r)
    keep = resid < max(2.0, 0.03 * dial.r)
    if keep.sum() >= 0.6 * pts.shape[0]:
        cx2, cy2, r2 = _circle_fit(pts[keep])
        if (abs(cx2 - cx) < 0.03 * dial.r and abs(r2 - r) < 0.03 * dial.r):
            cx, cy, r = cx2, cy2, r2
    return _Dial(float(cx), float(cy), float(r), dial.score, dial.method)


def _needle_probe(bgr: np.ndarray, dial: _Dial, cfg: GaugeConfig) -> float:
    """Правдоподобие 'это циферблат со стрелкой': макс. длина тёмного луча."""
    try:
        norm, _ = _normalize(bgr, dial, cfg.target_size)
        R = cfg.target_size * 0.47
        gray = cv2.cvtColor(norm, cv2.COLOR_BGR2GRAY)
        inner = np.zeros(gray.shape, np.uint8)
        cv2.circle(inner, (gray.shape[1] // 2, gray.shape[0] // 2),
                   int(0.75 * R), 255, -1)
        otsu, _ = cv2.threshold(gray.reshape(-1, 1), 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        thr = max(25.0, min(200.0, float(otsu)))
        mask = _needle_mask(norm, thr)
        mask[inner == 0] = 0
        prof = _run_profile(mask, R, nbins=360)
        return float(prof.max())
    except Exception:  # noqa: BLE001
        return 0.0


def _find_dial(gray: np.ndarray, bgr: np.ndarray,
               cfg: GaugeConfig) -> tuple[_Dial | None, list[dict]]:
    if cfg.center is not None and cfg.radius:
        d = _Dial(float(cfg.center[0]), float(cfg.center[1]), float(cfg.radius),
                  1.0, "config")
        d = _refine_dial_center(gray, d)
        return d, [{"method": "config", "score": 1.0,
                    "r": round(d.r, 1)}]
    edges = _edge_map(gray)
    edges_d = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    min_dim = min(gray.shape)
    pool = _hough_candidates(gray)
    for extra in (_bright_blob_candidate(gray), _contour_candidate(gray)):
        if extra is not None:
            pool.append(extra)
    scored: list[_Dial] = []
    seen: list[tuple[float, float, float]] = []
    for cand in pool:
        if any(abs(cand.cx - x) < 0.04 * min_dim and
               abs(cand.cy - y) < 0.04 * min_dim and
               abs(cand.r - r) < 0.05 * min_dim for x, y, r in seen):
            continue
        seen.append((cand.cx, cand.cy, cand.r))
        cand.score = _circle_score(gray, edges_d, cand.cx, cand.cy, cand.r,
                                   min_dim)
        if cand.score > 0:
            scored.append(cand)
    scored.sort(key=lambda d: d.score, reverse=True)

    top: list[dict] = []
    if not scored:
        return None, top
    best: _Dial | None = None
    best_comb = -1.0
    for cand in scored[:6]:
        refined = _refine_dial_center(gray, cand)
        if refined is not cand:
            refined.score = cand.score
        probe = _needle_probe(bgr, refined, cfg)
        comb = 0.40 * min(1.0, cand.score) + 0.60 * probe
        if refined.method in ("contour", "bright_blob"):
            comb *= 0.95
        top.append({"cx": round(refined.cx, 1), "cy": round(refined.cy, 1),
                    "r": round(refined.r, 1), "score": round(cand.score, 3),
                    "probe": round(probe, 3), "combined": round(comb, 3),
                    "method": cand.method})
        if comb > best_comb:
            best_comb, best = comb, refined
    top.sort(key=lambda d: d["combined"], reverse=True)
    if best is None or best.score < 0.15:
        # ни один кандидат не похож на циферблат (шум, пустой кадр)
        return None, top
    return best, top


# Шаг 3: нормализация и оценка поворота
def _normalize(bgr: np.ndarray, dial: _Dial, size: int) -> tuple[np.ndarray, float]:
    r_out = size * 0.47
    scale = r_out / max(1.0, dial.r)
    m = np.array([[scale, 0.0, size / 2.0 - scale * dial.cx],
                  [0.0, scale, size / 2.0 - scale * dial.cy]], np.float32)
    norm = cv2.warpAffine(bgr, m, (size, size), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)
    return norm, scale


def _estimate_rotation(norm_gray: np.ndarray) -> tuple[float, float, dict]:
    """Поворот прибора по 'пустому' сектору шкалы (обычно 90°, внизу).

    Возвращает (rotation_deg, quality 0..1, debug). Угол — на сколько прибор
    повёрнут против часовой стрелки относительно канонического положения.
    """
    size = norm_gray.shape[0]
    cx = cy = size / 2.0
    r_out = size * 0.47
    yy, xx = np.mgrid[0:size, 0:size]
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    ring = (rr >= 0.74 * r_out) & (rr <= 0.97 * r_out)
    vals = norm_gray[ring]
    if vals.size < 50:
        return 0.0, 0.0, {"reason": "tiny_ring"}
    thr, _ = cv2.threshold(vals.reshape(-1, 1), 0, 255,
                           cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = min(float(thr), 0.9 * float(np.median(vals)))
    dark = ring & (norm_gray <= thr)
    ys, xs = np.nonzero(dark)
    if ys.size < 200:
        return 0.0, 0.0, {"reason": "no_marks", "pixels": int(ys.size)}
    ang = np.degrees(np.arctan2(cy - ys, xs - cx)) % 360.0
    hist, _ = np.histogram(ang, bins=360, range=(0.0, 360.0))
    hist = hist.astype(np.float32)
    k = np.ones(3, np.float32) / 3.0
    hist = np.convolve(np.r_[hist[-1:], hist, hist[:1]], k, "same")[1:-1]
    med = float(np.median(hist))
    low = hist < max(1.5, 0.30 * med)
    if not low.any():
        return 0.0, 0.0, {"reason": "no_gap", "median": round(med, 1)}
    # самый длинный непрерывный участок "пустоты" (по кругу)
    ext = np.r_[low, low]
    best_len, best_start, cur, start = 0, 0, 0, 0
    for i in range(ext.size):
        if ext[i]:
            if cur == 0:
                start = i
            cur += 1
            if cur > best_len:
                best_len, best_start = cur, start
        else:
            cur = 0
    best_len = min(best_len, 360)
    gap_center = (best_start + best_len / 2.0) % 360.0
    if 55 <= best_len <= 130:
        quality = float(np.clip(1.0 - abs(best_len - 90) / 45.0, 0.5, 1.0))
    elif 35 <= best_len <= 150:
        quality = 0.30
    else:
        quality = 0.0
    rot = gap_center - 270.0
    return rot, quality, {
        "gap_deg": int(best_len), "gap_center": round(float(gap_center), 1),
        "density_median": round(med, 1), "threshold": round(thr, 1),
    }


# Шаг 4: поиск стрелки
def _needle_mask(norm_bgr: np.ndarray, thr: float) -> np.ndarray:
    gray = cv2.cvtColor(norm_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(norm_bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    dark = gray < thr
    red = (sat > 85) & ((hue < 12) | (hue > 168)) & (val < 235)
    return (dark | red).astype(np.uint8)


def _polar_coords(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    size = mask.shape[0]
    cx = cy = size / 2.0
    ys, xs = np.nonzero(mask)
    r = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    ang = np.degrees(np.arctan2(cy - ys, xs - cx))
    return r, ang


def _refine_angle(r: np.ndarray, ang: np.ndarray, a0: float, R: float) -> float:
    """Уточнить угол: среднее по тёмным пикселям в окне +-6°, вес = r."""
    win = np.abs((ang - a0 + 180.0) % 360.0 - 180.0) < 6.0
    sel = win & (r > 0.25 * R) & (r < 0.82 * R)
    if sel.sum() < 20:
        return a0
    a = ang[sel]
    w = r[sel]
    # переводим в локальную систему около a0, чтобы не было скачка 0/360
    loc = (a - a0 + 180.0) % 360.0 - 180.0
    return float((a0 + float(np.average(loc, weights=w)) + 360.0) % 360.0)


def _run_profile(mask: np.ndarray, R: float, nbins: int = 720,
                 rmax: float = 0.88) -> np.ndarray:
    """Для каждого угла — длина непрерывного тёмного пробега от центра (0..1)."""
    size = mask.shape[0]
    cx = cy = size / 2.0
    n_r = max(8, int(rmax * R))
    rs = np.arange(1, n_r, dtype=np.float32)
    angles = np.arange(nbins) * (360.0 / nbins)
    a = np.radians(angles)[:, None]
    xs = np.clip(np.rint(cx + rs[None, :] * np.cos(a)).astype(np.int32), 0, size - 1)
    ys = np.clip(np.rint(cy - rs[None, :] * np.sin(a)).astype(np.int32), 0, size - 1)
    dark = mask[ys, xs] > 0
    prof = np.zeros(nbins, np.float32)
    start_min = int(0.18 * n_r)
    max_gap = max(3, int(0.10 * n_r))  # терпим блики/шум до 5% радиуса
    for i in range(nbins):
        row = dark[i]
        if not row.any():
            continue
        s = int(row.argmax())
        if s > start_min:
            continue
        e, gap, n = s, 0, row.size
        while e < n:
            if row[e]:
                gap = 0
                e += 1
            else:
                gap += 1
                if gap > max_gap:
                    break
                e += 1
        prof[i] = (e - s - gap) / float(n_r)
    # сглаживание по кругу
    k = np.ones(5, np.float32) / 5.0
    prof = np.convolve(np.r_[prof[-4:], prof, prof[:4]], k, "same")[4:-4]
    return prof


def _histogram_needle(mask: np.ndarray, R: float, nbins: int = 720
                      ) -> tuple[float | None, float, dict]:
    prof = _run_profile(mask, R, nbins)
    if float(prof.max()) <= 0.0:
        return None, 0.0, {"profile_max": 0.0}
    thick = prof.copy()
    # центр пика: взвешенное среднее вокруг максимума (устойчивее одного бина)
    i0 = int(np.argmax(thick))
    a0 = (i0 * 360.0 / nbins) % 360.0
    arc = 6.0
    idx = ((np.arange(nbins) * 360.0 / nbins - a0 + 180.0) % 360.0 - 180.0)
    sel = np.abs(idx) < arc
    w = np.clip(prof[sel] - 0.5 * float(prof.max()), 0, None)
    if w.sum() > 0:
        a0 = float((a0 + np.average(idx[sel], weights=w) + 360.0) % 360.0)
    r, ang = _polar_coords(mask)
    a0 = _refine_angle(r, ang, a0, R)
    return a0, float(prof.max()), {"profile_max": round(float(prof.max()), 3)}


def _needle_straightness(mask: np.ndarray, angle: float, R: float) -> tuple[float, float]:
    """Разброс тёмных пикселей поперёк луча (px) и доля покрытия.

    Настоящая стрелка — узкая линия: поперечный разброс ~1-3 px.
    Шум/фон даёт большой разброс -> кандидат отбраковывается.
    """
    r, ang = _polar_coords(mask)
    loc = (ang - angle + 180.0) % 360.0 - 180.0
    sel = (np.abs(loc) < 5.0) & (r > 0.20 * R) & (r < 0.58 * R)
    if sel.sum() < 30:
        return 99.0, 0.0
    perp = r[sel] * np.sin(np.radians(loc[sel]))
    spread = float(np.percentile(np.abs(perp - np.median(perp)), 80))
    bins = np.linspace(0.20 * R, 0.58 * R, 12)
    idx = np.digitize(r[sel], bins) - 1
    cover = float(np.mean([np.any(idx == i) for i in range(len(bins) - 1)]))
    return spread, cover


def _hough_needle(mask: np.ndarray, R: float) -> tuple[float | None, float, dict]:
    size = mask.shape[0]
    cx = cy = size / 2.0
    yy, xx = np.mgrid[0:size, 0:size]
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    m = (mask > 0) & (rr <= 0.74 * R)
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE,
                         np.ones((3, 3), np.uint8))
    lines = cv2.HoughLinesP(m, 1, np.pi / 360, threshold=max(18, int(0.22 * R)),
                            minLineLength=int(0.33 * R),
                            maxLineGap=max(4, int(0.035 * R)))
    if lines is None:
        return None, 0.0, {"lines": 0}
    best: tuple[float, float] | None = None
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        dx, dy = float(x2 - x1), float(y2 - y1)
        length = math.hypot(dx, dy)
        if length < 1e-3:
            continue
        dist = abs(dx * (cy - y1) - dy * (cx - x1)) / length
        if dist > 0.10 * R:
            continue
        # направление "от центра"
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        if (mx - cx) ** 2 + (my - cy) ** 2 < (0.05 * R) ** 2:
            continue
        if dx * (mx - cx) + dy * (my - cy) < 0:
            dx, dy = -dx, -dy
        ang = math.degrees(math.atan2(-dy, dx)) % 360.0
        score = length * (1.0 - dist / (0.10 * R))
        if best is None or score > best[0]:
            best = (score, ang)
    if best is None:
        return None, 0.0, {"lines": int(lines.shape[0]), "through_center": 0}
    r, ang_all = _polar_coords(mask)
    ang = _refine_angle(r, ang_all, best[1], R)
    # длина вдоль найденного угла (в пределах циферблата)
    win = (np.abs((ang_all - ang + 180.0) % 360.0 - 180.0) < 2.0) & (r < 0.88 * R)
    length_rel = float(r[win].max() / R) if win.any() else 0.0
    return ang, min(length_rel, 0.9), {"lines": int(np.asarray(lines).size // 4),
                                       "through_center": 1}


def _detect_needle(norm_bgr: np.ndarray, cfg: GaugeConfig
                   ) -> dict[str, Any]:
    """Стрелка: HoughLinesP через центр + профиль радиальных пробегов.

    Три порога (тугой / Otsu / мягкий) — чтобы пережить блики и размытие,
    которые разрывают тонкую стрелку на части.
    """
    size = norm_bgr.shape[0]
    R = size * 0.47
    gray = cv2.cvtColor(norm_bgr, cv2.COLOR_BGR2GRAY)
    cx = cy = size // 2
    yy, xx = np.mgrid[0:size, 0:size]
    inner = (xx - cx) ** 2 + (yy - cy) ** 2 < (0.75 * R) ** 2
    vals = gray[inner]
    otsu, _ = cv2.threshold(vals.reshape(-1, 1), 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr_a = max(25.0, min(200.0, float(otsu)))
    thr_b = max(20.0, thr_a * 0.72)
    thr_c = float(np.clip(thr_a * 1.12, 40.0, 232.0))

    res: dict[str, Any] = {"thresholds": [round(thr_a, 1), round(thr_b, 1),
                                          round(thr_c, 1)]}
    cand: dict[str, list[tuple[str, float, float]]] = {"hough": [],
                                                       "histogram": []}
    masks: dict[str, np.ndarray] = {}
    peaks: dict[str, float | None] = {}
    for tag, thr in (("a", thr_a), ("b", thr_b), ("c", thr_c)):
        mask = _needle_mask(norm_bgr, thr)
        masks[tag] = mask
        ang_p, len_p, dbg_p = _histogram_needle(mask, R)
        res[f"hist_{tag}"] = dbg_p
        if ang_p is not None and len_p >= cfg.min_needle_frac:
            cand["histogram"].append((tag, ang_p, len_p))
        peaks[tag] = ang_p
        if tag != "c":  # Hough на мягком пороге шумит, не нужен
            ang_h, len_h, dbg_h = _hough_needle(mask, R)
            res[f"hough_{tag}"] = dbg_h
            if ang_h is not None and len_h >= cfg.min_needle_frac:
                cand["hough"].append((tag, ang_h, len_h))
                peaks[tag] = ang_h
        if tag == "a":
            res["needle_len_hist"] = round(float(len_p), 3)
            res["angle_hist"] = None if ang_p is None else round(ang_p, 2)
            res["needle_len_hough"] = round(float(
                cand["hough"][0][2] if cand["hough"] else 0.0), 3)

    if not cand["hough"] and not cand["histogram"]:
        res["reason"] = "no candidate above min length"
        return {"angle": None, "length_rel": 0.0, "method": "none",
                "agreement": 0.0, "stability": 0.0, "straight": False,
                "spread": 99.0, "debug": res}

    # стабильность угла при двух разных порогах
    pa, pb = peaks.get("a"), peaks.get("b")
    stability = 0.0
    if pa is not None and pb is not None:
        stability = max(0.0, 1.0 - _ang_diff(pa, pb) / 8.0)

    hough = max(cand["hough"], key=lambda c: c[2]) if cand["hough"] else None
    hist = max(cand["histogram"], key=lambda c: c[2]) \
        if cand["histogram"] else None
    mask_used = masks["c"]  # самый мягкий порог: стрелка видна целиком
    if hough and hist:
        diff = _ang_diff(hough[1], hist[1])
        if diff <= 6.0:
            w1, w2 = hough[2], hist[2]
            ang = (hough[1] * w1 + hist[1] * w2) / (w1 + w2)
            method, length = "hough+histogram", max(w1, w2)
            agreement = max(0.0, 1.0 - diff / 6.0)
        else:
            pick = hough if hough[2] >= hist[2] else hist
            ang, method, length = pick[1], pick[0], pick[2]
            agreement = max(0.0, 1.0 - diff / 45.0)
            res["disagreement_deg"] = round(diff, 2)
    else:
        pick = hough if hough else hist
        assert pick is not None
        ang, method, length = pick[1], pick[0], pick[2]
        agreement = 0.45

    res["stability"] = round(stability, 3)
    spread, cover = _needle_straightness(mask_used, ang, R)
    res["perp_spread_px"] = round(float(spread), 2)
    res["radial_cover"] = round(float(cover), 2)
    straight = bool(spread < 0.030 * R and cover >= 0.6)
    res["straight"] = straight
    return {"angle": float(_norm360(ang)), "length_rel": float(length),
            "method": method, "agreement": float(agreement),
            "stability": float(stability), "straight": straight,
            "spread": float(spread), "debug": res}


# Шаг 5: угол -> значение
def angle_to_value(angle_deg: float, cfg: GaugeConfig) -> tuple[float, float]:
    """Линейная интерполяция по шкале. Возвращает (value, frac 0..1 до клипа)."""
    a = _norm360(angle_deg)
    amin = _norm360(cfg.angle_min)
    amax = _norm360(cfg.angle_max)
    if cfg.clockwise:
        sweep = (amin - amax) % 360.0
        delta = (amin - a) % 360.0
    else:
        sweep = (amax - amin) % 360.0
        delta = (a - amin) % 360.0
    if sweep < 1e-6:
        raise ValueError("degenerate scale: angle_min == angle_max")
    frac = delta / sweep
    value = cfg.value_min + frac * (cfg.value_max - cfg.value_min)
    return float(value), float(frac)


def value_to_angle(value: float, cfg: GaugeConfig) -> float:
    """Обратное преобразование (для тестов/отрисовки)."""
    span = cfg.value_max - cfg.value_min
    frac = 0.0 if span == 0 else (value - cfg.value_min) / span
    if cfg.clockwise:
        return _norm360(cfg.angle_min - ((cfg.angle_min - cfg.angle_max) % 360.0) * frac)
    return _norm360(cfg.angle_min + ((cfg.angle_max - cfg.angle_min) % 360.0) * frac)


# Публичная точка входа
def read_gauge(image_bgr: Any, config: GaugeConfig | None = None) -> GaugeReading:
    """Прочитать показание манометра.

    Args:
        image_bgr: np.ndarray (BGR или gray), PIL.Image или путь к файлу.
        config: шкала прибора; по умолчанию 0..60 bar, 225°..-45°.

    Returns:
        GaugeReading; при неудаче value=None и заполнен error.
    """
    cfg = config or GaugeConfig()
    t0 = time.perf_counter()
    debug: dict[str, Any] = {"angle_convention": "0deg=right, CCW positive"}
    try:
        bgr = _to_bgr(image_bgr)
    except Exception as exc:  # noqa: BLE001
        return GaugeReading(None, cfg.unit, None, 0.0, "none",
                            {"error_detail": str(exc)}, ERROR_BAD_IMAGE)
    h, w = bgr.shape[:2]
    debug["image_size"] = [w, h]
    if min(h, w) < 40:
        return GaugeReading(None, cfg.unit, None, 0.0, "none", debug,
                            ERROR_BAD_IMAGE)

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if gray.std() > 5:  # CLAHE только если есть хоть какой-то контраст
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        std = float(gray.std())
        w = 0.3 if std > 45 else 0.75  # на «плоских» кадрах вытягиваем контраст
        debug["contrast_std"] = round(std, 2)
        debug["clahe_weight"] = w
        gray_for_det = cv2.addWeighted(gray, 1.0 - w, clahe.apply(gray), w, 0)
    else:
        gray_for_det = gray

    dial, cands = _find_dial(gray_for_det, bgr, cfg)
    debug["dial_candidates"] = cands
    if dial is None:
        debug["timing_ms"] = round((time.perf_counter() - t0) * 1e3, 1)
        return GaugeReading(None, cfg.unit, None, 0.0, "none", debug,
                            ERROR_NO_DIAL)
    debug["dial"] = {"cx": round(dial.cx, 1), "cy": round(dial.cy, 1),
                     "r": round(dial.r, 1), "score": round(dial.score, 3),
                     "method": dial.method}

    norm, scale = _normalize(bgr, dial, cfg.target_size)
    debug["norm_scale"] = round(scale, 3)
    rot, rot_q, rot_dbg = 0.0, 0.0, {"reason": "disabled"}
    if cfg.auto_orient:
        rot, rot_q, rot_dbg = _estimate_rotation(
            cv2.cvtColor(norm, cv2.COLOR_BGR2GRAY))
        debug["rotation_est_deg"] = round(rot, 2)
        debug["rotation_quality"] = round(rot_q, 2)
        debug["rotation_debug"] = rot_dbg

    needle = _detect_needle(norm, cfg)
    debug["needle"] = needle["debug"]
    if cfg.keep_debug_image:
        debug["normalized_bgr"] = norm

    if needle["angle"] is None:
        debug["timing_ms"] = round((time.perf_counter() - t0) * 1e3, 1)
        code = ERROR_NO_NEEDLE if needle["length_rel"] <= 1e-6 \
            else ERROR_NEEDLE_SHORT
        return GaugeReading(None, cfg.unit, None, 0.0, "none", debug, code)
    if not needle.get("straight", True):
        debug["timing_ms"] = round((time.perf_counter() - t0) * 1e3, 1)
        return GaugeReading(None, cfg.unit, None, 0.0, "none", debug,
                            ERROR_NO_NEEDLE)

    angle_img = float(needle["angle"])
    angle_dial = _norm360(angle_img - rot) if cfg.auto_orient else angle_img
    debug["angle_image_frame"] = round(angle_img, 2)
    debug["angle_dial_frame"] = round(angle_dial, 2)

    try:
        value_raw, frac = angle_to_value(angle_dial, cfg)
    except ValueError as exc:
        debug["error_detail"] = str(exc)
        return GaugeReading(None, cfg.unit, angle_dial, 0.0, "none", debug,
                            ERROR_BAD_CONFIG)

    circle_q = min(1.0, dial.score / 0.75)
    len_q = float(np.clip((needle["length_rel"] - cfg.min_needle_frac) /
                          (0.60 - cfg.min_needle_frac), 0.0, 1.0))
    straight_q = float(np.clip(1.0 - needle.get("spread", 0.0) /
                               (0.030 * cfg.target_size * 0.47), 0.0, 1.0))
    conf = (0.35 * circle_q + 0.25 * len_q + 0.15 * needle["stability"] +
            0.10 * needle["agreement"] + 0.15 * straight_q)
    clamped = bool(frac < -0.03 or frac > 1.03)
    if clamped:
        conf *= 0.7
    if cfg.auto_orient:
        conf *= 0.85 + 0.15 * rot_q
    conf = float(np.clip(conf, 0.0, 1.0))
    value = cfg.value_min + min(max(frac, 0.0), 1.0) * (cfg.value_max -
                                                       cfg.value_min)
    debug["value_raw"] = round(value_raw, 3)
    debug["frac"] = round(frac, 4)
    debug["clamped"] = clamped
    debug["confidence_parts"] = {
        "circle": round(circle_q, 3), "needle_len": round(len_q, 3),
        "stability": round(needle["stability"], 3),
        "agreement": round(needle["agreement"], 3),
    }
    debug["needle_length_rel"] = round(needle["length_rel"], 3)
    debug["timing_ms"] = round((time.perf_counter() - t0) * 1e3, 1)

    if conf < cfg.min_confidence:
        return GaugeReading(None, cfg.unit, angle_dial, conf, needle["method"],
                            debug, ERROR_LOW_CONFIDENCE)
    return GaugeReading(float(value), cfg.unit, angle_dial, conf,
                        needle["method"], debug, None)


def read_gauge_dict(image: Any, config: GaugeConfig | None = None) -> dict[str, Any]:
    """Обёртка для backend: PIL/np -> плоский dict."""
    return read_gauge(image, config).as_dict()
