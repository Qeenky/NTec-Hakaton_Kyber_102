"""Быстрые проверки модулей v2 (не требуют обученных моделей).

Запуск:  python -m pytest anomaly/v2/test_v2.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

V2_DIR = Path(__file__).resolve().parent
if str(V2_DIR.parent) not in sys.path:
    sys.path.insert(0, str(V2_DIR.parent))

from v2.canonical import (  # noqa: E402
    canonical_crops,
    component_rects,
    identity_homography,
    imread,
    load_layout,
    simulate_unaligned,
    ssim,
)
from v2.detector import AggregationConfig, ComponentDetector, FeatureExtractor  # noqa: E402
from v2.experiments import auroc as exp_auroc  # noqa: E402
from v2.synth_defects import apply_defect, augment_crop, load_texture_library  # noqa: E402

PROJECT = V2_DIR.parents[1]
BOARD = PROJECT / "ar_board" / "out_real" / "board_A3.png"
LAYOUT = PROJECT / "ar_board" / "out_real" / "layout.json"
CURATED = PROJECT / "organizer_data" / "defects" / "_curated"

pytestmark = pytest.mark.skipif(
    not (BOARD.is_file() and LAYOUT.is_file()), reason="нет планшета/лейаута"
)


# метрики
def test_auroc_perfect_and_chance():
    assert exp_auroc([0.1, 0.2, 0.3], [0.5, 0.6]) == 1.0
    assert exp_auroc([0.5, 0.6], [0.1, 0.2, 0.3]) == 0.0
    assert abs(exp_auroc([0.1, 0.2], [0.3, 0.4]) - 1.0) < 1e-9
    # все значения равны -> 0.5
    assert abs(exp_auroc([1.0, 1.0], [1.0, 1.0]) - 0.5) < 1e-9


def test_auroc_ties_are_averaged():
    # один дефект равен одному чистому: вклад 0.5
    assert abs(exp_auroc([0.0, 1.0], [1.0]) - 0.75) < 1e-9


# канонические кропы
@pytest.fixture(scope="module")
def layout():
    return load_layout(LAYOUT)


@pytest.fixture(scope="module")
def board():
    return imread(BOARD)


def test_canonical_crops_shape(layout, board):
    crops = canonical_crops(board, layout, homography=identity_homography())
    assert set(crops) == set(component_rects(layout))
    for cid, im in crops.items():
        assert im.shape == (512, 512, 3), cid
        assert im.dtype == np.uint8


def test_canonical_crop_survives_perspective(layout, board):
    """Кроп из перспективы похож на эталон (после фотометрической нормировки)."""
    ref = canonical_crops(board, layout, homography=identity_homography())
    # перспектива: истинная H известна -> не зависим от детекции ArUco.
    # Масштаб 0.35: планшет целиком влезает в кадр (иначе кроп обрезан).
    h = np.array(
        [[0.35, 0.02, 10.0], [0.01, 0.35, 10.0], [2.0e-6, 1.0e-6, 1.0]],
        dtype=np.float64,
    )
    frame = cv2.warpPerspective(board, h, (1800, 1300))
    crops = canonical_crops(frame, layout, homography=h)

    def norm(z):
        z = z.astype(np.float32)
        return np.clip((z - z.mean()) / (z.std() + 1e-6) * 40 + 128, 0, 255).astype(np.uint8)

    for cid in ("motor", "pump", "flange", "coupling_guard", "manometer"):
        s = ssim(norm(crops[cid]), norm(ref[cid]))
        assert s >= 0.90, f"{cid}: SSIM={s:.3f}"


def test_simulate_unaligned_changes_geometry(board, layout):
    ref = canonical_crops(board, layout, homography=identity_homography())["motor"]
    out = simulate_unaligned(ref, np.random.default_rng(0))
    assert out.shape == (320, 320, 3)
    assert ssim(ref, cv2.resize(out, (512, 512))) < 0.99


# синтез дефектов
@pytest.fixture(scope="module")
def textures():
    if not CURATED.is_dir():
        pytest.skip("нет organizer_data/defects/_curated")
    return load_texture_library(CURATED, per_image=1, seed=0)


@pytest.mark.parametrize("dtype", ["corrosion", "oil_leak", "crack", "erosion"])
def test_defect_mask_localizes_change(board, layout, textures, dtype):
    crop = augment_crop(
        canonical_crops(board, layout, homography=identity_homography())["motor"],
        np.random.default_rng(1),
    )
    img, mask = apply_defect(crop, dtype, np.random.default_rng(2), textures)
    assert img.shape == crop.shape and mask.shape == crop.shape[:2]
    m = mask > 127
    assert m.mean() > 0.002, f"{dtype}: маска пустая"
    diff = np.abs(img.astype(np.float32) - crop.astype(np.float32)).mean(axis=2)
    # изменения должны концентрироваться в маске
    assert diff[m].mean() > 2.0 * max(diff[~m].mean(), 1e-6)


def test_missing_guard_uses_plate_background(board, layout, textures):
    crop = canonical_crops(board, layout, homography=identity_homography())["coupling_guard"]
    img, mask = apply_defect(crop, "missing_guard", np.random.default_rng(4), textures)
    m = mask > 127
    assert m.mean() > 0.03
    # заливка светлая (бумага планшета), а не красная
    fill = img[m].reshape(-1, 3).mean(axis=0)
    assert fill.min() > 140, fill


# детектор
def test_detector_fit_score_roundtrip(tmp_path, board, layout):
    crops = canonical_crops(board, layout, homography=identity_homography(), size=256)
    ex = FeatureExtractor(input_size=128, device="cpu")
    det = ComponentDetector("motor", ex, aggregation=AggregationConfig("topk_area"), tta=False)
    train = [
        augment_crop(crops["motor"], np.random.default_rng(i), jpeg_quality=(90, 95))
        for i in range(3)
    ]
    det.fit(train)
    assert det.bank is not None and len(det.bank) > 0
    calib = [ex.extract(t) for t in train]
    det.calibrate(calib)
    r = det.score(crops["motor"])
    for key in ("score", "topk_mean", "area", "heatmap", "threshold", "is_anomaly"):
        assert key in r
    assert np.isfinite(r["score"]) and r["heatmap"].shape == (128, 128)

    path = det.save(tmp_path / "motor")
    loaded = ComponentDetector.load(path["npz"], device="cpu")
    r2 = loaded.score(crops["motor"])
    assert abs(r2["score"] - r["score"]) < 1e-3
    assert loaded.threshold_q95 == pytest.approx(det.threshold_q95, rel=1e-5)
