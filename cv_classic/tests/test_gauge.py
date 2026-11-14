"""Тесты чтения манометра: 5 синтетических кадров + «пустое» изображение."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cv_classic.calibrate import build_config  # noqa: E402
from cv_classic.gauge import (GaugeConfig, angle_to_value, read_gauge,  # noqa: E402
                              value_to_angle)
from cv_classic.synthetic import GaugeSpec, render  # noqa: E402

CACHE = Path(__file__).parent / "_cache"


def _five_cases() -> list[tuple[Path, dict]]:
    """5 кадров, равномерно по шкале, детерминированно."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cases: list[tuple[Path, dict]] = []
    for i, f in enumerate((0.02, 0.25, 0.5, 0.75, 0.98)):
        spec = GaugeSpec(seed=500 + i, radius=180 + 45 * i,
                         rotation_deg=-20.0 + 10.0 * i, value=40.0 * f,
                         value_min=0.0, value_max=40.0, unit="bar",
                         red_needle=bool(i % 2), with_arc=(i == 2),
                         glare=0.5, blur=0.4, noise=4.0)
        img, meta = render(spec)
        path = CACHE / f"case_{i}.png"
        ok, buf = cv2.imencode(".png", img)
        assert ok
        path.write_bytes(buf.tobytes())
        cases.append((path, meta))
    return cases


CASES = _five_cases()


def _cfg(meta: dict) -> GaugeConfig:
    return GaugeConfig(angle_min=meta["angle_min"], angle_max=meta["angle_max"],
                       value_min=meta["value_min"],
                       value_max=meta["value_max"], unit=meta["unit"])


@pytest.mark.parametrize("path,meta", CASES, ids=[c[0].stem for c in CASES])
def test_synthetic_reads(path: Path, meta: dict) -> None:
    res = read_gauge(path, _cfg(meta))
    assert res.error is None, f"{path.name}: {res.error} {res.debug.get('needle')}"
    assert res.value is not None
    rng = meta["value_max"] - meta["value_min"]
    assert abs(res.value - meta["value"]) / rng < 0.03
    assert 0.0 <= res.confidence <= 1.0
    assert res.confidence > 0.3
    assert res.method in ("hough", "histogram", "hough+histogram")


def test_blank_image_returns_error() -> None:
    rng = np.random.default_rng(0)
    blank = np.clip(200 + rng.normal(0, 4, (600, 600, 3)), 0, 255).astype(np.uint8)
    res = read_gauge(blank, GaugeConfig())
    assert res.value is None
    assert res.error in ("no_dial", "no_needle", "needle_too_short",
                         "low_confidence")
    assert res.angle_deg is None


def test_noise_image_does_not_raise() -> None:
    noise = np.random.default_rng(1).integers(0, 255, (320, 320, 3), np.uint8)
    res = read_gauge(noise, GaugeConfig())
    assert res.value is None and res.error is not None


def test_tiny_image_returns_bad_image() -> None:
    res = read_gauge(np.zeros((20, 20, 3), np.uint8))
    assert res.value is None and res.error == "bad_image"


def test_angle_value_roundtrip() -> None:
    cfg = GaugeConfig(angle_min=225.0, angle_max=-45.0, value_min=0.0,
                      value_max=60.0)
    for v in (0.0, 15.0, 30.0, 45.0, 60.0):
        got, _ = angle_to_value(value_to_angle(v, cfg), cfg)
        assert got == pytest.approx(v, abs=1e-6)
    # 90° (12 часов) — середина шкалы 0..60
    assert angle_to_value(90.0, cfg)[0] == pytest.approx(30.0, abs=1e-6)


def test_counter_clockwise_config() -> None:
    cfg = GaugeConfig(angle_min=-45.0, angle_max=225.0, value_min=0.0,
                      value_max=100.0, clockwise=False)
    angle = value_to_angle(25.0, cfg)
    got, _ = angle_to_value(angle, cfg)
    assert got == pytest.approx(25.0, abs=1e-6)


def test_config_json_roundtrip(tmp_path: Path) -> None:
    cfg = GaugeConfig(angle_min=200.0, angle_max=340.0, value_min=0.0,
                      value_max=16.0, unit="MPa", center=(100.0, 120.0),
                      radius=88.0)
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg.to_json()), encoding="utf-8")
    cfg2 = GaugeConfig.from_json(p)
    assert cfg2.unit == "MPa"
    assert cfg2.center == (100.0, 120.0)
    assert cfg2.angle_max == 340.0


def test_calibration_points_end_to_end(tmp_path: Path) -> None:
    """3 клика -> конфиг -> чтение того же кадра."""
    path, meta = CASES[2]
    cx, cy = meta["center"]
    r = 0.8 * meta["radius"]
    pts = [(cx, cy)]
    for a in (meta["angle_min"], meta["angle_max"]):
        pts.append((cx + r * math.cos(math.radians(a)),
                    cy - r * math.sin(math.radians(a))))
    cfg = build_config(path, pts, meta["value_min"], meta["value_max"],
                       meta["unit"], True)
    out = tmp_path / "cal.json"
    out.write_text(json.dumps(cfg.to_json()), encoding="utf-8")
    res = read_gauge(path, GaugeConfig.from_json(out))
    assert res.error is None
    assert res.value is not None
    assert abs(res.value - meta["value"]) / 40.0 < 0.03
