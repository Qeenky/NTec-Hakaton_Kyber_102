"""pytest для маркерной проверки наличия (ArUco DICT_4X4_50, id=4)."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from cv_classic.presence import (
    ERROR_BAD_IMAGE, ERROR_BAD_ROI, ERROR_MARKER_TOO_SMALL, PresenceResult,
    check_marker, check_marker_in_roi, load_config,
)

DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)


def _marker(size: int = 160, marker_id: int = 4) -> np.ndarray:
    return cv2.aruco.generateImageMarker(DICT, marker_id, size)


def _background(w: int = 640, h: int = 480) -> np.ndarray:
    """Светлый «планшет» с лёгкой неоднородностью, чтобы кадр был не пустым."""
    rng = np.random.default_rng(3)
    base = np.full((h, w, 3), 205, np.float32)
    low = cv2.GaussianBlur(rng.normal(0, 12, (h // 8, w // 8)).astype(np.float32),
                           (0, 0), 3)
    low = cv2.resize(low, (w, h), interpolation=cv2.INTER_CUBIC)
    base += low[..., None]
    return np.clip(base, 0, 255).astype(np.uint8)


def _paste_quad(frame: np.ndarray, marker: np.ndarray,
                center: tuple[float, float], side: float,
                angle_deg: float = 0.0) -> np.ndarray:
    """Наклеить маркер на кадр с поворотом (перспективное наложение)."""
    h, w = frame.shape[:2]
    s = marker.shape[0]
    src = np.float32([[0, 0], [s, 0], [s, s], [0, s]])
    th = np.radians(angle_deg)
    c, sn = np.cos(th), np.sin(th)
    half = side / 2.0
    local = np.float32([[-half, -half], [half, -half], [half, half],
                        [-half, half]])
    rot = np.float32([[c, -sn], [sn, c]])
    dst = (local @ rot.T) + np.float32(center)
    m = cv2.getPerspectiveTransform(src, dst)
    marker_bgr = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    warped = cv2.warpPerspective(marker_bgr, m, (w, h))
    mask = cv2.warpPerspective(np.full((s, s), 255, np.uint8), m, (w, h))
    out = frame.copy()
    out[mask > 0] = warped[mask > 0]
    return out


@pytest.fixture(scope="module")
def cfg() -> dict:
    return load_config()


def test_marker_present() -> None:
    frame = _paste_quad(_background(), _marker(), (320, 240), 150, 12)
    res = check_marker(frame, 4)
    assert isinstance(res, PresenceResult)
    assert res.error is None
    assert res.present is True
    assert res.confidence >= 0.55
    assert res.corners is not None and len(res.corners) == 4
    cx = float(np.mean([p[0] for p in res.corners]))
    cy = float(np.mean([p[1] for p in res.corners]))
    assert abs(cx - 320) < 20 and abs(cy - 240) < 20


def test_marker_absent() -> None:
    frame = _background()
    res = check_marker(frame, 4)
    assert res.error is None
    assert res.present is False
    assert res.corners is None
    assert 0.4 <= res.confidence <= 0.8


def test_wrong_marker_id() -> None:
    frame = _paste_quad(_background(), _marker(marker_id=7), (320, 240), 150)
    assert check_marker(frame, 4).present is False
    assert check_marker(frame, 7).present is True


def test_marker_outside_roi() -> None:
    frame = _paste_quad(_background(), _marker(), (150, 120), 120, -8)
    roi = [(350, 260), (600, 260), (600, 450), (350, 450)]
    res = check_marker_in_roi(frame, 4, roi)
    assert res.error is None
    assert res.present is False
    assert res.reason == "outside_roi"
    assert res.corners is not None  # маркер найден, но не там
    assert res.debug["center_inside"] is False


def test_marker_inside_roi() -> None:
    frame = _paste_quad(_background(), _marker(), (320, 240), 140, 20)
    roi = [(180, 110), (470, 100), (500, 360), (170, 390)]
    res = check_marker_in_roi(frame, 4, roi)
    assert res.error is None
    assert res.present is True
    assert res.reason == "marker_in_roi"
    assert res.debug["corners_inside_frac"] >= 0.5


def test_marker_too_small(cfg: dict) -> None:
    frame = _paste_quad(_background(), _marker(), (320, 240), 120)
    strict = dict(cfg, min_side_px=500.0, good_side_px=800.0)
    res = check_marker(frame, 4, config=strict)
    assert res.present is False
    assert res.error == ERROR_MARKER_TOO_SMALL
    assert res.corners is not None


def test_bad_roi(cfg: dict) -> None:
    frame = _paste_quad(_background(), _marker(), (320, 240), 140)
    res = check_marker_in_roi(frame, 4, [(0, 0), (10, 10)])
    assert res.error == ERROR_BAD_ROI
    assert res.present is False


def test_bad_frame_no_exception() -> None:
    for bad in (None, np.zeros((0, 0, 3), np.uint8), "missing.png"):
        res = check_marker(bad, 4)
        assert res.present is False
        assert res.error == ERROR_BAD_IMAGE
        assert res.corners is None
