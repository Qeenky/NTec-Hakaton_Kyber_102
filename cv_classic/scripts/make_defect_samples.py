"""Генерация синтетических ROI для проверки детекторов дефектов.

    python cv_classic/scripts/make_defect_samples.py
    python cv_classic/scripts/make_defect_samples.py --out cv_classic/data/defects --seed 2026

Датасет (по заданию): 40 чистых негативов, 30 ржавчины, 30 масла,
20 окисления, 20 смешанных/пограничных. Все изображения синтетические,
детерминированные по seed, без скачивания из интернета.

Типы ROI:
    clean        ровная краска/металл + градиент, шум, царапины, тени, блики
    rust         коричнево-оранжевые пятна с неровными краями (5-40 % площади)
    oil          тёмные глянцевые blob'ы, вытянутые вдоль вертикали
    oxidation    локальное потемнение с потерей насыщенности и зернистостью
    rust_oil     ржавчина + масло (метки rust=1, oil=1)
    light_rust   мелкая ржавчина 2-4 % (сложные позитивы)
    rusty_paint  ровная рыжая краска, прямые края (сложный негатив)
    dirty        сухой грязно-коричневый развод (сложный негатив)
    shadow_glint сильная тень + блик на чистом металле (сложный негатив)

truth.json: {"schema", "seed", "items": [{file, group, labels{rust,oil,oxidation},
area_frac, hard_negative}]}.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# BGR-палитры «чистой» поверхности: сталь, краска, оцинковка, графит.
PALETTE = [
    (132, 134, 136), (84, 86, 90), (168, 170, 173), (198, 200, 202),
    (140, 110, 84), (92, 120, 96), (150, 163, 172), (60, 62, 66),
    (110, 118, 126), (176, 166, 150),
]


# Вспомогательные генераторы
def _lowfreq(rng: np.random.Generator, h: int, w: int,
             scale: float = 0.08) -> np.ndarray:
    """Низкочастотное поле ~[-1, 1] (освещённость/неоднородность)."""
    sh = max(2, int(round(h * scale)))
    sw = max(2, int(round(w * scale)))
    small = rng.normal(0.0, 1.0, (sh, sw)).astype(np.float32)
    field = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    field = cv2.GaussianBlur(field, (0, 0), max(1.0, 0.04 * min(h, w)))
    return (field - float(field.mean())) / max(float(field.std()), 1e-6) * 0.5


def _base_surface(rng: np.random.Generator, h: int, w: int) -> np.ndarray:
    """Ровная окрашенная/металлическая поверхность с шумом и царапинами."""
    base = np.array(PALETTE[int(rng.integers(len(PALETTE)))], np.float32)
    img = np.empty((h, w, 3), np.float32)
    img[:] = base
    # освещённость и лёгкий градиент
    img *= (1.0 + 0.22 * _lowfreq(rng, h, w, 0.05))[..., None]
    gy = rng.uniform(-0.10, 0.10)
    gx = rng.uniform(-0.10, 0.10)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img *= (1.0 + gx * (xx / w - 0.5) + gy * (yy / h - 0.5))[..., None]
    # царапины
    for _ in range(int(rng.integers(0, 9))):
        x1, y1 = rng.uniform(0, w), rng.uniform(0, h)
        ang = rng.uniform(0, np.pi)
        ln = rng.uniform(0.15, 0.6) * min(h, w)
        x2 = x1 + ln * np.cos(ang)
        y2 = y1 + ln * np.sin(ang)
        delta = rng.uniform(18, 38) * (1 if rng.random() < 0.6 else -1)
        layer = img.copy()
        cv2.line(layer, (int(x1), int(y1)), (int(x2), int(y2)),
                 (float(base[0] + delta), float(base[1] + delta),
                  float(base[2] + delta)), int(rng.integers(1, 3)), cv2.LINE_AA)
        a = rng.uniform(0.25, 0.6)
        img = img * (1 - a) + layer * a
    # шум сенсора
    img += rng.normal(0.0, rng.uniform(2.5, 8.0), img.shape)
    # виньетирование
    r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
    img *= (1.0 - rng.uniform(0.05, 0.22) * r ** 2)[..., None]
    return np.clip(img, 0, 255)


def _add_shadow(img: np.ndarray, rng: np.random.Generator,
                feather: tuple[float, float] = (4.0, 10.0),
                drop: tuple[float, float] = (0.25, 0.45)) -> np.ndarray:
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.float32)
    style = int(rng.integers(0, 3))
    if style == 0:  # полоса
        ang = rng.uniform(-0.5, 0.5) + (np.pi / 2 if rng.random() < 0.5 else 0.0)
        p1 = (int(-w * 0.2), int(h * rng.uniform(0.2, 0.8)))
        p2 = (int(w * 1.2), int(p1[1] + np.tan(ang) * w * 1.4))
        width = int(rng.uniform(0.35, 0.8) * min(h, w))
        cv2.line(mask, p1, p2, 1.0, width)
    elif style == 1:  # полигон
        pts = rng.uniform([0, 0], [w, h], size=(3, 2)).astype(np.int32)
        cv2.fillPoly(mask, [pts], 1.0)
    else:  # широкая мягкая клякса
        cv2.ellipse(mask, (int(rng.uniform(0, w)), int(rng.uniform(0, h))),
                    (int(rng.uniform(0.25, 0.6) * w),
                     int(rng.uniform(0.25, 0.6) * h)),
                    float(rng.uniform(0, 180)), 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), rng.uniform(*feather))
    factor = 1.0 - rng.uniform(*drop) * mask
    return img * factor[..., None]


def _add_glint(img: np.ndarray, rng: np.random.Generator,
               strength: tuple[float, float] = (30.0, 75.0),
               feather: tuple[float, float] = (3.0, 9.0)) -> np.ndarray:
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.float32)
    cx, cy = rng.uniform(0.15, 0.85) * w, rng.uniform(0.15, 0.85) * h
    cv2.ellipse(mask, (int(cx), int(cy)),
                (int(rng.uniform(0.10, 0.35) * w),
                 int(rng.uniform(0.02, 0.10) * h)),
                float(rng.uniform(-40, 40)), 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), rng.uniform(*feather))
    add = rng.uniform(*strength) * mask
    return np.clip(img + add[..., None], 0, 255)


def _add_specular_streak(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Резкий узкий блик (как на мокрой/полированной поверхности)."""
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.float32)
    x1, y1 = rng.uniform(0.1, 0.9) * w, rng.uniform(0.1, 0.9) * h
    ang = rng.uniform(0, np.pi)
    ln = rng.uniform(0.2, 0.55) * min(h, w)
    x2, y2 = x1 + ln * np.cos(ang), y1 + ln * np.sin(ang)
    cv2.line(mask, (int(x1), int(y1)), (int(x2), int(y2)), 1.0,
             int(rng.integers(2, 6)), cv2.LINE_AA)
    mask = cv2.GaussianBlur(mask, (0, 0), rng.uniform(0.8, 2.2))
    return np.clip(img + rng.uniform(45, 95) * mask[..., None], 0, 255)


def _jpeg(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    q = int(rng.integers(55, 93))
    ok, buf = cv2.imencode(".jpg", np.clip(img, 0, 255).astype(np.uint8),
                           [int(cv2.IMWRITE_JPEG_QUALITY), q])
    if not ok:
        return img
    return cv2.imdecode(buf, cv2.IMREAD_COLOR).astype(np.float32)


def _finish(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    img = _jpeg(img, rng) if rng.random() < 0.5 else img
    if rng.random() < 0.45:
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.35, 0.9))
    return np.clip(img, 0, 255).astype(np.uint8)


# Маски и материалы
def _blob_mask(rng: np.random.Generator, h: int, w: int, area_frac: float,
               n_lo: int = 1, n_hi: int = 3,
               feather: tuple[float, float] = (1.2, 3.0),
               roughness: float = 0.55) -> np.ndarray:
    """Пятно(а) заданной суммарной площади с неровным краем. Маска 0..1."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    field = np.zeros((h, w), np.float32)
    k = int(rng.integers(n_lo, n_hi + 1))
    shares = rng.dirichlet(np.ones(k))
    for share in shares:
        cx = rng.uniform(0.18, 0.82) * w
        cy = rng.uniform(0.18, 0.82) * h
        rx = rng.uniform(0.10, 0.30) * min(h, w)
        ry = rx * rng.uniform(0.55, 1.65)
        noise = _lowfreq(rng, h, w, 0.10) * 2.0
        blob = -(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) + roughness * noise
        target = share * area_frac
        thr = float(np.quantile(blob, 1.0 - min(target, 0.95)))
        field = np.maximum(field, (blob >= thr).astype(np.float32))
    field = cv2.GaussianBlur(field, (0, 0), rng.uniform(*feather))
    return np.clip(field, 0, 1)


def _rust_color(rng: np.random.Generator, h: int, w: int,
                hue: float | None = None) -> np.ndarray:
    """Поле цвета ржавчины в BGR (H 8-22 OpenCV = 16-44 градуса)."""
    hue = rng.uniform(8, 21) if hue is None else hue
    sat = rng.uniform(0.55, 0.90)
    val = rng.uniform(0.30, 0.62)
    tex = _lowfreq(rng, h, w, 0.06)
    fine = rng.normal(0.0, 0.05, (h, w)).astype(np.float32)
    hc = np.clip(hue + 6.0 * tex + 1.5 * fine, 4.0, 26.0)
    sc = np.clip(sat * (1.0 + 0.12 * tex), 0.35, 1.0)
    vc = np.clip(val * (1.0 + 0.30 * tex) + 0.03 * fine, 0.12, 0.80)
    hsv = np.stack([hc, sc * 255.0, vc * 255.0], axis=-1).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR).astype(np.float32)


def _add_rust(img: np.ndarray, rng: np.random.Generator,
              area_frac: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = _blob_mask(rng, h, w, area_frac, 1, 3, (1.2, 2.6), 0.6)
    rust = _rust_color(rng, h, w)
    out = img * (1.0 - m[..., None]) + rust * m[..., None]
    # мелкая сыпь вокруг пятна: ржавчина не имеет гладкой кромки
    speck = (rng.random((h, w)) < rng.uniform(0.01, 0.05)).astype(np.float32)
    speck = cv2.GaussianBlur(speck, (0, 0), 0.6)
    halo = np.clip(m * 1.8, 0, 1) * speck
    out = out * (1.0 - 0.75 * halo[..., None]) + rust * 0.75 * halo[..., None]
    # тёмные «язвы» внутри
    pits = (rng.random((h, w)) < 0.02).astype(np.float32)
    pits = cv2.GaussianBlur(pits, (0, 0), 0.8) * (m > 0.6)
    out -= 55.0 * pits[..., None]
    # ржавчина шершавая: высокочастотный шум только внутри маски
    rough = rng.normal(0.0, rng.uniform(5.0, 11.0), (h, w)).astype(np.float32)
    out += (rough * m)[..., None]
    return out


def _oil_mask(rng: np.random.Generator, h: int, w: int,
              area_frac: float) -> tuple[np.ndarray, float, float, float, float]:
    """Вытянутое вниз пятно (эллипс + хвост подтёка)."""
    elong = float(rng.uniform(1.9, 3.4))
    major = np.sqrt(area_frac * h * w * elong / np.pi)
    minor = major / elong
    cx = rng.uniform(0.25, 0.75) * w
    cy = rng.uniform(0.22, 0.55) * h
    angle = 90.0 + float(rng.normal(0, 14))
    mask = np.zeros((h, w), np.float32)
    cv2.ellipse(mask, (int(cx), int(cy)), (int(major), int(minor)), angle,
                0, 360, 1.0, -1)
    # хвост вниз (гравитация)
    tail_len = rng.uniform(0.25, 0.6) * major
    steps = int(max(4, tail_len / 4))
    for i in range(steps):
        t = i / max(steps - 1, 1)
        y = cy + minor * 0.6 + t * tail_len
        r = max(1, int(minor * 0.35 * (1.0 - t)))
        cv2.circle(mask, (int(cx + rng.normal(0, 1.2)), int(y)), r, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), rng.uniform(1.0, 2.2))
    return np.clip(mask, 0, 1), elong, angle, major, minor


def _add_oil(img: np.ndarray, rng: np.random.Generator,
             area_frac: float) -> tuple[np.ndarray, dict]:
    h, w = img.shape[:2]
    m, elong, angle, major, minor = _oil_mask(rng, h, w, area_frac)
    smooth = cv2.GaussianBlur(img, (0, 0), 1.1)
    out = img * (1.0 - m[..., None]) + smooth * m[..., None]
    # мокрое пятно темнее и чуть насыщеннее/желтее
    hsv = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8),
                       cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 2] *= 1.0 - rng.uniform(0.35, 0.55) * m
    hsv[..., 1] = np.clip(hsv[..., 1] * (1.0 + 0.12 * m), 0, 255)
    hsv[..., 0] = np.clip(hsv[..., 0] + 2.5 * m, 0, 179)
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    # глянцевые блики внутри пятна (мокрый блеск)
    gl = np.zeros((h, w), np.float32)
    ys, xs = np.nonzero(m > 0.75)
    if ys.size:
        for _ in range(int(rng.integers(2, 5))):
            i = int(rng.integers(ys.size))
            gx, gy = float(xs[i]), float(ys[i])
            ax = max(2.0, major * rng.uniform(0.35, 0.62))
            ay = max(1.0, minor * rng.uniform(0.12, 0.28))
            cv2.ellipse(gl, (int(gx), int(gy)), (int(ax), int(ay)),
                        angle + float(rng.normal(0, 6)), 0, 360, 1.0, -1)
    gl = cv2.GaussianBlur(gl, (0, 0), rng.uniform(1.0, 2.0)) * m
    out = np.clip(out + rng.uniform(120, 190) * gl[..., None], 0, 255)
    meta = {"elongation": round(elong, 2), "angle": round(angle, 1),
            "glint_frac": round(float(gl.mean() / max(m.mean(), 1e-6)), 3)}
    return out, meta


def _add_oxidation(img: np.ndarray, rng: np.random.Generator,
                   area_frac: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = _blob_mask(rng, h, w, area_frac, 1, 2, (4.0, 9.0), 0.7)
    hsv = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8),
                       cv2.COLOR_BGR2HSV).astype(np.float32)
    v_factor = 1.0 - rng.uniform(0.30, 0.50) * m
    s_factor = 1.0 - rng.uniform(0.35, 0.65) * m
    hsv[..., 2] *= v_factor
    hsv[..., 1] = np.clip(hsv[..., 1] * s_factor, 0, 255)
    hsv[..., 0] = np.clip(hsv[..., 0] + rng.uniform(4, 14) * m, 0, 179)
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    # зернистость/патина
    grain = rng.normal(0.0, rng.uniform(8.0, 15.0), (h, w)).astype(np.float32)
    out += (grain * m)[..., None]
    speck = (rng.random((h, w)) < 0.03).astype(np.float32)
    speck = cv2.GaussianBlur(speck, (0, 0), 0.7) * (m > 0.5)
    out -= 40.0 * speck[..., None]
    return out


# Сэмплы по группам
def _size(rng: np.random.Generator) -> tuple[int, int]:
    side = int(rng.integers(176, 257))
    return side, side


def make_clean(seed: int) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(seed)
    h, w = _size(rng)
    img = _base_surface(rng, h, w)
    kind = int(rng.integers(0, 5))
    if kind in (0, 1, 2):
        img = _add_shadow(img, rng,
                          feather=(3.5, 12.0) if kind != 2 else (2.0, 4.5),
                          drop=(0.18, 0.45))
    if kind in (1, 3, 4):
        img = _add_glint(img, rng)
    if kind == 4:
        img = _add_specular_streak(img, rng)
    return _finish(img, rng), {"group": "clean", "labels": _labels(),
                               "area_frac": 0.0, "hard": False,
                               "variant": kind}


def make_rust(seed: int) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(seed)
    h, w = _size(rng)
    img = _base_surface(rng, h, w)
    if rng.random() < 0.4:
        img = _add_shadow(img, rng, (5.0, 12.0), (0.15, 0.35))
    area = float(rng.uniform(0.05, 0.40))
    img = _add_rust(img, rng, area)
    return _finish(img, rng), {"group": "rust",
                               "labels": _labels(rust=1),
                               "area_frac": round(area, 3), "hard": False,
                               "variant": 0}


def make_oil(seed: int) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(seed)
    h, w = _size(rng)
    img = _base_surface(rng, h, w)
    area = float(rng.uniform(0.06, 0.25))
    img, meta = _add_oil(img, rng, area)
    return _finish(img, rng), {"group": "oil", "labels": _labels(oil=1),
                               "area_frac": round(area, 3), "hard": False,
                               "variant": 0, **meta}


def make_oxidation(seed: int) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(seed)
    h, w = _size(rng)
    img = _base_surface(rng, h, w)
    area = float(rng.uniform(0.08, 0.35))
    img = _add_oxidation(img, rng, area)
    return _finish(img, rng), {"group": "oxidation",
                               "labels": _labels(oxidation=1),
                               "area_frac": round(area, 3), "hard": False,
                               "variant": 0}


def make_rust_oil(seed: int) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(seed)
    h, w = _size(rng)
    img = _base_surface(rng, h, w)
    area = float(rng.uniform(0.08, 0.28))
    img = _add_rust(img, rng, area)
    oil_area = float(rng.uniform(0.05, 0.16))
    img, _ = _add_oil(img, rng, oil_area)
    return _finish(img, rng), {"group": "rust_oil",
                               "labels": _labels(rust=1, oil=1),
                               "area_frac": round(area + oil_area, 3),
                               "hard": False, "variant": 0}


def make_light_rust(seed: int) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(seed)
    h, w = _size(rng)
    img = _base_surface(rng, h, w)
    area = float(rng.uniform(0.02, 0.04))
    img = _add_rust(img, rng, area)
    return _finish(img, rng), {"group": "light_rust",
                               "labels": _labels(rust=1),
                               "area_frac": round(area, 3), "hard": False,
                               "variant": 0}


def make_rusty_paint(seed: int) -> tuple[np.ndarray, dict]:
    """Сложный негатив: ровная рыжая краска с прямыми краями, без текстуры."""
    rng = np.random.default_rng(seed)
    h, w = _size(rng)
    img = _base_surface(rng, h, w)
    mask = np.zeros((h, w), np.float32)
    if rng.random() < 0.5:
        x1, y1 = int(rng.uniform(0.1, 0.4) * w), int(rng.uniform(0.1, 0.4) * h)
        x2, y2 = int(rng.uniform(0.6, 0.95) * w), int(rng.uniform(0.6, 0.95) * h)
        cv2.rectangle(mask, (x1, y1), (x2, y2), 1.0, -1)
    else:
        pts = np.array([[rng.uniform(0.05, 0.3) * w, rng.uniform(0.2, 0.5) * h],
                        [rng.uniform(0.7, 0.95) * w, rng.uniform(0.15, 0.45) * h],
                        [rng.uniform(0.65, 0.95) * w, rng.uniform(0.6, 0.9) * h],
                        [rng.uniform(0.05, 0.35) * w, rng.uniform(0.65, 0.9) * h]],
                       np.int32)
        cv2.fillPoly(mask, [pts], 1.0)
    mask = cv2.GaussianBlur(mask, (0, 0), 0.8)
    hue = float(rng.uniform(9, 18))
    color = _rust_color(rng, h, w, hue=hue)
    # краска ровная: сглаживаем цветовое поле, убираем мелкую текстуру
    color = cv2.GaussianBlur(color, (0, 0), 1.6)
    img = img * (1.0 - mask[..., None]) + color * mask[..., None]
    return _finish(img, rng), {"group": "rusty_paint",
                               "labels": _labels(),
                               "area_frac": round(float(mask.mean()), 3),
                               "hard": True, "variant": 0}


def make_dirty(seed: int) -> tuple[np.ndarray, dict]:
    """Сложный негатив: сухой коричневый развод, без блеска и резкой кромки."""
    rng = np.random.default_rng(seed)
    h, w = _size(rng)
    img = _base_surface(rng, h, w)
    area = float(rng.uniform(0.10, 0.25))
    m = _blob_mask(rng, h, w, area, 1, 2, (6.0, 12.0), 0.8)
    smudge = np.empty((h, w, 3), np.float32)
    smudge[:] = np.array([rng.uniform(55, 80), rng.uniform(65, 90),
                          rng.uniform(80, 105)], np.float32)
    alpha = rng.uniform(0.45, 0.65) * m
    img = img * (1.0 - alpha[..., None]) + smudge * alpha[..., None]
    return _finish(img, rng), {"group": "dirty", "labels": _labels(),
                               "area_frac": round(area, 3), "hard": True,
                               "variant": 0}


def make_shadow_glint(seed: int) -> tuple[np.ndarray, dict]:
    """Сложный негатив: сильная тень + резкий блик на чистом металле."""
    rng = np.random.default_rng(seed)
    h, w = _size(rng)
    img = _base_surface(rng, h, w)
    img = _add_shadow(img, rng, (3.5, 9.0), (0.35, 0.55))
    img = _add_specular_streak(img, rng)
    if rng.random() < 0.5:
        img = _add_glint(img, rng, (40, 80), (2.0, 5.0))
    return _finish(img, rng), {"group": "shadow_glint", "labels": _labels(),
                               "area_frac": 0.0, "hard": True, "variant": 0}


def _labels(rust: int = 0, oil: int = 0, oxidation: int = 0) -> dict[str, int]:
    return {"rust": int(rust), "oil": int(oil), "oxidation": int(oxidation)}


# Сборка датасета
GROUPS = [
    ("clean", make_clean, 40),
    ("rust", make_rust, 30),
    ("oil", make_oil, 30),
    ("oxidation", make_oxidation, 20),
    ("mixed", None, 20),  # смешанные/пограничные, раскладываются ниже
]

MIXED = [
    (make_rust_oil, 6),
    (make_light_rust, 4),
    (make_rusty_paint, 4),
    (make_dirty, 3),
    (make_shadow_glint, 3),
]


def build_dataset(out_dir: str | Path, seed: int = 2026,
                  counts: dict[str, int] | None = None,
                  ) -> list[dict]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts = counts or {}
    items: list[dict] = []
    idx = 0

    def add(fn, name: str, rng_seed: int) -> None:
        nonlocal idx
        img, meta = fn(rng_seed)
        fn_name = f"{name}_{idx:03d}.png"
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            raise RuntimeError(f"cannot encode {fn_name}")
        (out / fn_name).write_bytes(buf.tobytes())
        items.append({
            "file": fn_name, "group": meta["group"],
            "labels": meta["labels"], "area_frac": meta["area_frac"],
            "hard_negative": bool(meta.get("hard", False)),
            "variant": meta.get("variant"),
        })
        idx += 1

    for group, fn, default_n in GROUPS:
        if group == "mixed":
            continue
        n = int(counts.get(group, default_n))
        for k in range(n):
            add(fn, group, seed + 1000 * (len(items) + 1) + k)

    n_mixed = int(counts.get("mixed", 20))
    plan = [n for _, n in MIXED]
    if n_mixed == sum(plan):
        alloc = plan
    else:  # пропорционально, если задано другое количество
        alloc = [max(0, int(round(n * n_mixed / sum(plan)))) for n in plan]
        while sum(alloc) < n_mixed:
            alloc[int(np.argmax(plan))] += 1
        while sum(alloc) > n_mixed:
            alloc[int(np.argmax(alloc))] -= 1
    for j, (fn, _) in enumerate(MIXED):
        for k in range(alloc[j]):
            add(fn, "mixed", seed + 7919 * (len(items) + 3) + k)

    truth = {"schema": "cv_classic.defect_samples/v1", "seed": seed,
             "size": "random 176..256 px (square ROI)", "items": items}
    (out / "truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    return items


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(
        Path(__file__).resolve().parents[1] / "data" / "defects"))
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--clean", type=int, default=40)
    ap.add_argument("--rust", type=int, default=30)
    ap.add_argument("--oil", type=int, default=30)
    ap.add_argument("--oxidation", type=int, default=20)
    ap.add_argument("--mixed", type=int, default=20)
    args = ap.parse_args(argv)
    counts = {"clean": args.clean, "rust": args.rust, "oil": args.oil,
              "oxidation": args.oxidation, "mixed": args.mixed}
    items = build_dataset(args.out, args.seed, counts)
    groups: dict[str, int] = {}
    for it in items:
        groups[it["group"]] = groups.get(it["group"], 0) + 1
    print(f"сгенерировано {len(items)} ROI -> {args.out}")
    for g, n in sorted(groups.items()):
        print(f"  {g:<12}{n:>4}")
    print(f"truth.json обновлён (seed={args.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
