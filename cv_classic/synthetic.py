"""Синтетические изображения аналоговых манометров (OpenCV, без интернета).

Схема углов: 0° — вправо (3 часа), положительное направление — против часовой
стрелки. Шкала рисуется в "приборной" системе: 0 бар — 225° (низ-лево),
конец шкалы — -45° (низ-право), развёртка 270° по часовой стрелке.
Всё изображение прибора поворачивается на случайный угол `rotation_deg`.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

# Приборная система координат (до поворота прибора).
DIAL_MIN_DEG = 225.0  # значение value_min
DIAL_MAX_DEG = -45.0  # значение value_max
DIAL_SWEEP_DEG = 270.0  # по часовой стрелке


def polar(cx: float, cy: float, r: float, ang_deg: float) -> tuple[int, int]:
    """Полярные координаты (0° вправо, CCW+) -> пиксели (ось Y вниз)."""
    a = math.radians(ang_deg)
    return int(round(cx + r * math.cos(a))), int(round(cy - r * math.sin(a)))


def value_to_dial_angle(value: float, value_min: float, value_max: float) -> float:
    """Значение -> угол в приборной системе (по часовой стрелке)."""
    frac = (value - value_min) / float(value_max - value_min)
    return DIAL_MIN_DEG - DIAL_SWEEP_DEG * frac


@dataclass
class GaugeSpec:
    """Параметры одного синтетического прибора."""

    seed: int = 0
    radius: int = 260
    rotation_deg: float = 0.0
    value: float = 30.0
    value_min: float = 0.0
    value_max: float = 60.0
    unit: str = "bar"
    red_needle: bool = False
    with_arc: bool = False
    glare: float = 0.7
    blur: float = 0.5
    noise: float = 4.0
    brightness: int = 0
    contrast: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)


def _draw_rotated_text(img: np.ndarray, text: str, center: tuple[float, float],
                       ang_deg: float, scale: float, thickness: int,
                       color: tuple[int, int, int]) -> None:
    """Цифры шкалы: маленький патч с текстом, повёрнутый вдоль радиуса."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), base = cv2.getTextSize(text, font, scale, thickness)
    pad = 6
    patch = np.zeros((th + base + 2 * pad, tw + 2 * pad), np.uint8)
    cv2.putText(patch, text, (pad, pad + th), font, scale, 255, thickness, cv2.LINE_AA)
    rot = ang_deg - 90.0  # текст "стоит" радиально
    h, w = patch.shape
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), rot, 1.0)
    cos_a, sin_a = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin_a + w * cos_a), int(h * cos_a + w * sin_a)
    m[0, 2] += nw / 2.0 - w / 2.0
    m[1, 2] += nh / 2.0 - h / 2.0
    patch = cv2.warpAffine(patch, m, (nw, nh), flags=cv2.INTER_LINEAR,
                           borderValue=0)
    alpha = cv2.GaussianBlur(patch, (3, 3), 0).astype(np.float32) / 255.0
    x0 = int(round(center[0] - nw / 2.0))
    y0 = int(round(center[1] - nh / 2.0))
    x1, y1 = x0 + nw, y0 + nh
    H, W = img.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x1), min(H, y1)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    a = alpha[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)][..., None]
    roi = img[dy0:dy1, dx0:dx1].astype(np.float32)
    col = np.array(color, np.float32).reshape(1, 1, 3)
    img[dy0:dy1, dx0:dx1] = (roi * (1 - a) + col * a).astype(np.uint8)


def render(spec: GaugeSpec) -> tuple[np.ndarray, dict[str, Any]]:
    """Отрисовать манометр. Возвращает (BGR-изображение, метаданные/истина)."""
    rng = random.Random(spec.seed)
    R = float(spec.radius)
    size = int(round(2.35 * R))
    H = W = size
    cx = W / 2.0 + rng.uniform(-0.03, 0.03) * R
    cy = H / 2.0 + rng.uniform(-0.03, 0.03) * R

    # --- фон (стол / приборная панель) ---
    base = rng.randint(150, 215)
    xs = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    ys = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    grad = (xs * rng.uniform(-1, 1) + ys * rng.uniform(-1, 1)) * 22.0
    bg = np.clip(base + grad, 0, 255).astype(np.uint8)
    img = cv2.merge([bg, bg, bg])

    # --- корпус и циферблат ---
    bezel = rng.randint(45, 95)
    cv2.circle(img, (int(cx), int(cy)), int(R * 1.06), (bezel, bezel, bezel), -1,
               cv2.LINE_AA)
    hi = rng.randint(70, 130)
    cv2.circle(img, (int(cx), int(cy)), int(R * 1.02), (hi, hi, hi), 2, cv2.LINE_AA)
    face = rng.randint(232, 252)
    cv2.circle(img, (int(cx), int(cy)), int(R * 1.0), (face, face, face), -1,
               cv2.LINE_AA)

    rot = spec.rotation_deg
    ink = int(rng.randint(15, 55))

    # --- цветная зона (иногда) ---
    if spec.with_arc:
        a0 = DIAL_MIN_DEG - DIAL_SWEEP_DEG * 0.78
        a1 = DIAL_MAX_DEG
        cv2.ellipse(img, (int(cx), int(cy)), (int(R * 0.97), int(R * 0.97)),
                    0, -a0, -a1, (30, 30, 220), max(2, int(R * 0.015)),
                    cv2.LINE_AA)

    # --- шкала: 10 больших делений, 4 малых между ними ---
    n_major = 10
    for i in range(n_major + 1):
        frac = i / n_major
        ang = DIAL_MIN_DEG - DIAL_SWEEP_DEG * frac + rot
        r0, r1 = R * 0.80, R * 0.925
        cv2.line(img, polar(cx, cy, r0, ang), polar(cx, cy, r1, ang),
                 (ink, ink, ink), max(2, int(R * 0.014)), cv2.LINE_AA)
        if i < n_major:
            for k in range(1, 4):
                f2 = frac + k / (4.0 * n_major)
                a2 = DIAL_MIN_DEG - DIAL_SWEEP_DEG * f2 + rot
                cv2.line(img, polar(cx, cy, R * 0.845, a2),
                         polar(cx, cy, R * 0.925, a2), (ink, ink, ink),
                         max(1, int(R * 0.007)), cv2.LINE_AA)
        val = spec.value_min + (spec.value_max - spec.value_min) * frac
        label = f"{val:g}"
        scale = max(0.35, R * 0.0016)
        _draw_rotated_text(img, label,
                           polar(cx, cy, R * 0.685, ang), ang, scale,
                           max(1, int(R * 0.006)), (ink, ink, ink))

    # --- стрелка ---
    true_dial_angle = value_to_dial_angle(spec.value, spec.value_min,
                                          spec.value_max)
    ang = true_dial_angle + rot
    color = (25, 25, 215) if spec.red_needle else (25, 25, 25)
    tip = polar(cx, cy, R * 0.87, ang)
    tail = polar(cx, cy, R * 0.16, ang + 180.0)
    cv2.line(img, tail, tip, color, max(3, int(R * 0.026)), cv2.LINE_AA)
    # остриё-указатель
    cv2.fillConvexPoly(img, np.array([
        tip,
        polar(cx, cy, R * 0.74, ang + 2.0),
        polar(cx, cy, R * 0.74, ang - 2.0),
    ], np.int32), color, cv2.LINE_AA)
    hub = max(3, int(R * 0.055))
    cv2.circle(img, (int(cx), int(cy)), hub, (35, 35, 35), -1, cv2.LINE_AA)

    # --- блик на стекле ---
    if rng.random() < spec.glare:
        ga = rng.uniform(0, 2 * math.pi)
        gr = rng.uniform(0, 0.55) * R
        gx = cx + gr * math.cos(ga)
        gy = cy - gr * math.sin(ga)
        axes = (int(rng.uniform(0.12, 0.34) * R), int(rng.uniform(0.08, 0.22) * R))
        glow = np.zeros((H, W), np.float32)
        cv2.ellipse(glow, (int(gx), int(gy)), axes,
                    rng.uniform(0, 180), 0, 360, 1.0, -1, cv2.LINE_AA)
        glow = cv2.GaussianBlur(glow, (0, 0), max(3.0, R * 0.05))
        img = np.clip(img.astype(np.float32) +
                      180.0 * glow[..., None] * rng.uniform(0.5, 1.0), 0, 255)

    # --- виньетка, размытие, яркость/контраст, шум ---
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (R * 1.25)
    vign = 1.0 - 0.25 * np.clip(rr, 0, 1) ** 2
    img = img * vign[..., None]

    if rng.random() < spec.blur:
        k = rng.choice([3, 5])
        img = cv2.GaussianBlur(img, (k, k), 0)

    img = img * spec.contrast + spec.brightness
    img = np.clip(img, 0, 255)
    if spec.noise > 0:
        img = img + np.random.default_rng(spec.seed).normal(0, spec.noise,
                                                            img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)

    meta: dict[str, Any] = {
        "value": float(spec.value),
        "unit": spec.unit,
        "value_min": float(spec.value_min),
        "value_max": float(spec.value_max),
        "angle_deg": round((ang + 360.0) % 360.0, 4),
        "rotation_deg": round(rot, 4),
        "radius": float(R),
        "angle_min": round(DIAL_MIN_DEG + rot, 4),
        "angle_max": round(DIAL_MAX_DEG + rot, 4),
        "center": [round(cx, 2), round(cy, 2)],
        "clockwise": True,
        "red_needle": spec.red_needle,
        "seed": spec.seed,
        "size": size,
    }
    return img, meta


_SCALES = [
    (0.0, 25.0, "bar"),
    (0.0, 40.0, "bar"),
    (0.0, 60.0, "bar"),
    (0.0, 100.0, "bar"),
    (0.0, 1.6, "MPa"),
    (0.0, 300.0, "psi"),
]


def random_specs(n: int, seed: int = 2026,
                 radius_range: tuple[int, int] = (150, 400)) -> list[GaugeSpec]:
    """Сгенерировать n спецификаций, равномерно покрывающих весь диапазон."""
    rng = random.Random(seed)
    specs: list[GaugeSpec] = []
    for i in range(n):
        vmin, vmax, unit = _SCALES[i % len(_SCALES)]
        frac = i / (n - 1) if n > 1 else 0.5
        frac = min(max(frac + rng.uniform(-0.004, 0.004), 0.003), 0.997)
        specs.append(GaugeSpec(
            seed=seed * 1000 + i,
            radius=rng.randint(*radius_range),
            rotation_deg=rng.uniform(-25.0, 25.0),
            value=round(vmin + (vmax - vmin) * frac, 4),
            value_min=vmin,
            value_max=vmax,
            unit=unit,
            red_needle=(i % 3 == 1),
            with_arc=(i % 5 == 2),
            glare=rng.uniform(0.2, 0.9),
            blur=rng.uniform(0.15, 0.75),
            noise=rng.uniform(1.5, 7.0),
            brightness=rng.uniform(-18, 18),
            contrast=rng.uniform(0.85, 1.12),
        ))
    return specs
