"""Стресс-тест: на каких условиях ломается read_gauge.

    python cv_classic/scripts/stress_test.py

Генерирует по 20 кадров на каждый вариант искажения и печатает
успех / MAE / грубые промахи — эти цифры попадают в README.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cv_classic.gauge import GaugeConfig, read_gauge  # noqa: E402
from cv_classic.synthetic import random_specs, render  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def _dir(ang: float) -> tuple[float, float]:
    return math.cos(math.radians(ang)), -math.sin(math.radians(ang))


def _angle_of(v: tuple[float, float]) -> float:
    return math.degrees(math.atan2(-v[1], v[0])) % 360.0


def _tilt(img: np.ndarray, deg: float, meta: dict) -> tuple[np.ndarray, dict]:
    """Аффинный наклон: сжатие по одной оси на cos(deg) + сдвиг."""
    h, w = img.shape[:2]
    a = np.array([[1.0, 0.0, 0.0],
                  [0.35 * math.sin(math.radians(deg)), math.cos(math.radians(deg)), 0.0]],
                 np.float32)
    m = a[:2, :]
    m[0, 2] = 0.0
    m[1, 2] = (h - (m[1, 0] * w + m[1, 1] * h)) / 2.0
    warped = cv2.warpAffine(img, m, (w, h), borderValue=(180, 180, 180))
    lin = m[:, :2]
    a_min = _angle_of(tuple(lin @ np.array(_dir(meta["angle_min"]))))
    a_max = _angle_of(tuple(lin @ np.array(_dir(meta["angle_max"]))))
    ang = _angle_of(tuple(lin @ np.array(_dir(meta["angle_deg"]))))
    new = dict(meta)
    new.update(angle_min=a_min, angle_max=a_max, angle_deg=ang)
    return warped, new


def _glare(img: np.ndarray, meta: dict, power: float = 235.0) -> np.ndarray:
    """Блик прямо на стрелке (стекло бликует)."""
    h, w = img.shape[:2]
    cx, cy = meta["center"]
    r = meta["radius"]
    a = math.radians(meta["angle_deg"])
    gx = cx + 0.45 * r * math.cos(a)
    gy = cy - 0.45 * r * math.sin(a)
    glow = np.zeros((h, w), np.float32)
    cv2.circle(glow, (int(gx), int(gy)), int(0.22 * r), 1.0, -1, cv2.LINE_AA)
    glow = cv2.GaussianBlur(glow, (0, 0), max(4.0, 0.10 * r))
    out = np.clip(img.astype(np.float32) + power * glow[..., None], 0, 255)
    return out.astype(np.uint8)


def _variant(img: np.ndarray, meta: dict, kind: str) -> tuple[np.ndarray, dict]:
    if kind == "clean":
        return img, dict(meta)
    if kind.startswith("tilt"):
        return _tilt(img, float(kind[4:]), meta)
    if kind == "blur":
        return cv2.GaussianBlur(img, (11, 11), 0), dict(meta)
    if kind == "lowcontrast":
        out = 128.0 + (img.astype(np.float32) - 128.0) * 0.28
        return np.clip(out, 0, 255).astype(np.uint8), dict(meta)
    if kind == "dark":
        return np.clip(img.astype(np.float32) * 0.45, 0, 255).astype(np.uint8), \
            dict(meta)
    if kind == "glare":
        return _glare(img, meta), dict(meta)
    if kind == "glare_mild":
        return _glare(img, meta, power=110.0), dict(meta)
    raise ValueError(kind)


def _value_from_angle(ang: float, meta: dict) -> float:
    """Значение по углу и (возможно искажённым) углам шкалы."""
    cfg = GaugeConfig(angle_min=meta["angle_min"], angle_max=meta["angle_max"],
                      value_min=meta["value_min"], value_max=meta["value_max"],
                      clockwise=True)
    from cv_classic.gauge import angle_to_value
    return angle_to_value(ang, cfg)[0]


def main() -> int:
    kinds = ["clean", "tilt15", "tilt30", "tilt45", "blur", "lowcontrast",
             "dark", "glare_mild", "glare"]
    specs = random_specs(20, seed=777)
    print(f"{'условие':<13}{'успех':>8}{'MAE %':>8}{'промахи':>9}{'conf':>7}")
    print("-" * 45)
    for kind in kinds:
        errs, oks, confs = [], 0, []
        for spec in specs:
            img, meta = render(spec)
            img2, meta2 = _variant(img, meta, kind)
            truth = _value_from_angle(meta2["angle_deg"], meta2)
            cfg = GaugeConfig(angle_min=meta2["angle_min"],
                              angle_max=meta2["angle_max"],
                              value_min=meta2["value_min"],
                              value_max=meta2["value_max"],
                              clockwise=True, unit=meta2["unit"])
            res = read_gauge(img2, cfg)
            rng = meta2["value_max"] - meta2["value_min"]
            err = 100.0 if res.value is None else \
                abs(res.value - truth) / rng * 100.0
            errs.append(err)
            oks += err <= 5.0
            confs.append(res.confidence)
        n = len(errs)
        gross = sum(1 for e in errs if e > 10.0)
        print(f"{kind:<13}{100.0 * oks / n:>7.0f}%{np.mean(errs):>8.2f}"
              f"{100.0 * gross / n:>8.0f}%{np.mean(confs):>7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
