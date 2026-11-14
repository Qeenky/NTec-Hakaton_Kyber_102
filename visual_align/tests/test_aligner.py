"""pytest: регистрация синтетики, отказ на мусоре, hold_last_pose, save/load."""
from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from visual_align.aligner import FeatureAligner, Smoother, confidence_from
from visual_align.dataset import (FRAME_SIZE, corner_errors, load_image_unicode,
                                  make_case, resize_cover)

ROOT = Path(__file__).resolve().parents[1]
BOARD_PATH = ROOT.parent / "ar_board" / "out_real" / "board_A3.png"
LAYOUT_PATH = ROOT.parent / "ar_board" / "out_real" / "layout.json"
NEGATIVE = ROOT / "data" / "negatives" / "01_corroded_pipe.jpg"


def _hash(array: np.ndarray) -> str:
    return hashlib.sha1(np.ascontiguousarray(array).tobytes()).hexdigest()


def _case(layout, board, seed: int, **kwargs):
    rng = np.random.default_rng(seed)
    return make_case(layout, board, FRAME_SIZE, rng, **kwargs)


def test_registers_synthetic_frames(aligner, layout, board):
    """Три разные перспективы регистрируются с ошибкой углов < 8 px."""
    for seed in (11, 12, 13):
        case = _case(layout, board, seed, tilt_range=(5.0, 25.0),
                     scale_range=(0.6, 1.4), rotation_range=(-15.0, 15.0))
        result = aligner.register(case["frame"])
        assert result.ok, f"seed={seed} reason={result.reason}"
        assert result.method in {"orb", "akaze"}
        assert result.inliers >= aligner.min_inliers
        assert result.inlier_ratio >= aligner.min_inlier_ratio
        assert 0.0 <= result.confidence <= 1.0
        errors = corner_errors(layout, case["homography"], result.homography)
        assert errors["mean"] < 8.0, errors
        assert errors["p95"] < 20.0, errors


def test_projection_returns_zones_in_frame(aligner, layout, board):
    """project() отдаёт по полигону на каждую зону в координатах кадра."""
    case = _case(layout, board, 21)
    result = aligner.register(case["frame"])
    assert result.ok
    zones = aligner.project_result(result)
    assert set(zones) == set(aligner.reference.zones)
    for name, polygon in zones.items():
        assert len(polygon) == len(aligner.reference.zones[name])
        points = np.asarray(polygon)
        assert np.all(np.isfinite(points))
        # Сравнение с истинной проекцией по углам зоны.
        truth = cv2.perspectiveTransform(
            aligner.reference.zones[name].reshape(-1, 1, 2),
            case["homography"]).reshape(-1, 2)
        assert float(np.linalg.norm(points - truth, axis=1).max()) < 12.0


def test_rejects_random_photo(aligner):
    """Кадр без эталона не должен давать ложную гомографию."""
    frame = resize_cover(load_image_unicode(NEGATIVE), FRAME_SIZE)
    aligner.reset()
    result = aligner.register(frame)
    assert not result.ok
    assert result.homography is None
    assert result.stale is False


def test_rejects_homogeneous_frame(aligner):
    """Почти однородная поверхность — честный None, а не случайная H."""
    frame = np.full((720, 1280, 3), 118, dtype=np.uint8)
    aligner.reset()
    result = aligner.register(frame)
    assert not result.ok
    assert result.reason == "low_texture"


def test_rejects_garbage_input(aligner):
    assert not aligner.register(None).ok
    assert not aligner.register(np.zeros((4, 4, 3), np.uint8)).ok


def test_hold_last_pose(aligner, layout, board):
    """После валидного кадра отказ удерживает позу, затем честно истекает."""
    case = _case(layout, board, 31)
    aligner.reset()
    good = aligner.register(case["frame"], timestamp=100.0)
    assert good.ok and not good.stale

    blank = np.full((720, 1280, 3), 120, np.uint8)
    held = aligner.register(blank, timestamp=100.5)
    assert held.ok
    assert held.stale is True
    assert held.age_sec == pytest.approx(0.5, abs=1e-6)
    assert np.allclose(held.homography, good.homography)
    assert held.confidence < good.confidence

    expired = aligner.register(blank, timestamp=100.0 + aligner.hold_seconds + 0.01)
    assert not expired.ok
    assert expired.homography is None


def test_hold_last_pose_disabled(layout, board):
    aligner = FeatureAligner.from_files(BOARD_PATH, LAYOUT_PATH, hold_seconds=0.0)
    case = _case(layout, board, 41)
    assert aligner.register(case["frame"], timestamp=0.0).ok
    blank = np.full((720, 1280, 3), 120, np.uint8)
    assert not aligner.register(blank, timestamp=0.01).ok


def test_save_load_roundtrip(aligner, tmp_path, layout, board):
    """save/load сохраняет дескрипторы, зоны и результат регистрации."""
    path = tmp_path / "ref.npz"
    aligner.save(path)
    loaded = FeatureAligner.load(path)

    assert loaded.reference.name == aligner.reference.name
    assert loaded.reference.ref_scale == pytest.approx(aligner.reference.ref_scale)
    assert set(loaded.reference.zones) == set(aligner.reference.zones)
    for name, polygon in aligner.reference.zones.items():
        assert np.allclose(loaded.reference.zones[name], polygon)
    for method in ("orb", "akaze"):
        original = aligner.reference.features[method]
        cached = loaded.reference.features[method]
        assert _hash(original[1]) == _hash(cached[1])
        assert _hash(original[0]) == _hash(cached[0])

    case = _case(layout, board, 51)
    aligner.reset()
    loaded.reset()
    first = aligner.register(case["frame"])
    second = loaded.register(case["frame"])
    assert first.ok and second.ok
    assert first.method == second.method
    assert first.inliers == second.inliers
    assert np.allclose(first.homography, second.homography, atol=1e-6)


def test_smoother_ema():
    smoother = Smoother(alpha=0.35, reset_after=1.0)
    first = smoother.update({"motor": [[0, 0], [10, 0], [10, 10], [0, 10]]},
                            timestamp=0.0)
    second = smoother.update({"motor": [[10, 10], [20, 10], [20, 20], [10, 20]]},
                             timestamp=0.1)
    expected = 0.35 * 10 + 0.65 * 0
    assert first["motor"][0] == [0.0, 0.0]
    assert second["motor"][0] == pytest.approx([expected, expected], abs=1e-6)
    # Пауза больше reset_after — состояние сбрасывается.
    third = smoother.update({"motor": [[100, 100], [110, 100], [110, 110], [100, 110]]},
                            timestamp=5.0)
    assert third["motor"][0] == [100.0, 100.0]


def test_confidence_formula_bounds():
    assert confidence_from(0, 0.0, 100.0) >= 0.0
    assert confidence_from(1000, 1.0, 0.0) == pytest.approx(1.0)
    assert confidence_from(60, 0.6, 0.0) == pytest.approx(1.0)
    mid = confidence_from(30, 0.3, 5.0)
    assert 0.0 < mid < 1.0
    assert confidence_from(15, 0.25, 3.0) > confidence_from(15, 0.25, 10.0)
