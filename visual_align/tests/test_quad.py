"""pytest: fallback-привязка по контуру печатного листа (quad)."""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from visual_align.aligner import FeatureAligner, load_image_unicode, project_points
from visual_align.dataset import (FRAME_SIZE, corner_errors, make_case,
                                  resize_cover)
from visual_align.quad import (detect_sheet_quad, order_quad, quad_confidence)

ROOT = Path(__file__).resolve().parents[1]
BOARD_PATH = ROOT.parent / "ar_board" / "out_real" / "board_A3.png"
LAYOUT_PATH = ROOT.parent / "ar_board" / "out_real" / "layout.json"
REF_PHOTO = ROOT.parent / "ar_board" / "ref" / "PUMP-001_photo.png"
REF_LAYOUT = ROOT.parent / "ar_board" / "ref" / "PUMP-001_layout.json"
SCALE_35 = ROOT.parent / "test_photos" / "_scale_35.jpg"
NEGATIVE = ROOT / "data" / "negatives" / "01_corroded_pipe.jpg"


def _small_case(layout, board, seed: int, scale: float):
    rng = np.random.default_rng(seed)
    return make_case(layout, board, FRAME_SIZE, rng,
                     tilt_range=(0.0, 8.0), scale_range=(scale, scale),
                     rotation_range=(-6.0, 6.0))


def test_quad_registers_small_sheet(aligner, layout, board):
    """Лист на ~22% кадра: признаки бессильны, quad даёт точную позу."""
    case = _small_case(layout, board, 71, 0.22)
    aligner.reset()  # иначе сработает hold_last_pose от предыдущих тестов
    assert not aligner.register(case["frame"]).ok, "ожидали отказ признаков"
    result = aligner.register_by_quad(case["frame"])
    assert result.ok, result.reason
    assert result.method == "quad"
    assert 0.0 < result.confidence <= 1.0
    assert result.inliers == 0
    errors = corner_errors(layout, case["homography"], result.homography)
    assert errors["mean"] < 8.0, errors
    assert errors["p95"] < 16.0, errors


def test_quad_maps_cropped_reference(layout, board):
    """Эталон-обрезок: лист выходит за границы картинки, координаты сдвинуты."""
    if not (REF_PHOTO.is_file() and REF_LAYOUT.is_file()):
        pytest.skip("нет эталона-обрезка PUMP-001")
    # photo_rect_px из полного планшета: обрезок 1500x1500 в точке (1730, 1004).
    aligner = FeatureAligner.from_files(
        REF_PHOTO, REF_LAYOUT, name="crop",
        sheet_rect=(-1730.0, -1004.0, -1730.0 + 4961.0, -1004.0 + 3508.0))
    assert aligner.reference.sheet_aspect == pytest.approx(1.414, abs=0.01)

    frame = load_image_unicode(SCALE_35) if SCALE_35.is_file() else None
    if frame is None:
        pytest.skip("нет test_photos/_scale_35.jpg")
    result = aligner.register_by_quad(frame)
    assert result.ok, result.reason
    # Истина: лист — прямоугольник по яркой маске кадра.
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    ys, xs = np.where(gray > 150)
    sheet = np.float32([[xs.min(), ys.min()], [xs.max(), ys.min()],
                        [xs.max(), ys.max()], [xs.min(), ys.max()]])
    board = np.float32([[0, 0], [4961, 0], [4961, 3508], [0, 3508]])
    truth = cv2.getPerspectiveTransform(board, sheet) @ np.array(
        [[1.0, 0.0, 1730.0], [0.0, 1.0, 1004.0], [0.0, 0.0, 1.0]])
    corners = np.array([[0, 0], [1500, 0], [1500, 1500], [0, 1500]], np.float64)
    error = np.linalg.norm(project_points(result.homography, corners)
                           - project_points(truth, corners), axis=1)
    assert float(error.max()) < 8.0, error


def test_quad_rejects_non_sheet(aligner):
    """Полоса 5:1 и случайное фото — не лист: quad честно отказывает."""
    bar = np.full((720, 1280, 3), 30, np.uint8)
    cv2.rectangle(bar, (300, 300), (900, 420), (242, 242, 242), -1)
    assert detect_sheet_quad(bar) is None
    assert not aligner.register_by_quad(bar).ok

    negative = resize_cover(load_image_unicode(NEGATIVE), FRAME_SIZE)
    result = aligner.register_by_quad(negative)
    assert not result.ok
    assert result.homography is None

    white = np.full((720, 1280, 3), 245, np.uint8)
    assert not aligner.register_by_quad(white).ok

    # Белый прямоугольник A3 с посторонним содержимым: геометрия есть,
    # но NCC эталона и кадра низкий — quad обязан отказать.
    rng = np.random.default_rng(5)
    fake = np.full((720, 1280, 3), 30, np.uint8)
    patch = rng.integers(170, 255, (283, 400, 3), dtype=np.uint8)
    fake[200:483, 400:800] = patch
    detected = detect_sheet_quad(fake)
    assert detected is not None
    rejected = aligner.register_by_quad(fake)
    assert not rejected.ok
    assert rejected.reason == "quad_low_ncc"


def test_quad_order_and_confidence():
    """Порядок углов стабилен, уверенность — в [0, 1] и растёт с качеством."""
    shuffled = np.array([[300.0, 200.0], [100.0, 100.0],
                         [100.0, 200.0], [300.0, 100.0]])
    ordered = order_quad(shuffled)
    assert ordered.tolist() == [[100.0, 100.0], [300.0, 100.0],
                                [300.0, 200.0], [100.0, 200.0]]
    good = quad_confidence(0.5, 0.98, 0.99, 1.41)
    poor = quad_confidence(0.01, 0.80, 0.80, 2.5)
    assert 0.0 <= poor < good <= 1.0


def test_quad_latency(aligner, layout, board):
    """quad укладывается в бюджет 15 мс на 480x720 и 1280x720 (медиана)."""
    timings = {}
    for size in ((480, 720), (1280, 720)):
        rng = np.random.default_rng(81)
        case = make_case(layout, board, size, rng, tilt_range=(0.0, 5.0),
                         scale_range=(0.5, 0.5), rotation_range=(-4.0, 4.0))
        frame = case["frame"]
        aligner.register_by_quad(frame)  # прогрев
        samples = []
        for _ in range(7):
            started = time.perf_counter()
            result = aligner.register_by_quad(frame)
            samples.append((time.perf_counter() - started) * 1000.0)
        assert result.ok, result.reason
        timings[size] = float(np.median(samples))
    assert timings[(480, 720)] <= 15.0, timings
    assert timings[(1280, 720)] <= 15.0, timings
