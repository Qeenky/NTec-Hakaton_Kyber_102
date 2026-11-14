"""Привязка без маркеров: выбор эталона, hold, отказ, смена layout."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app.cv.board_registry import VisualBoardRegistry
from app.cv.pipeline import BoardPipeline

PROJECT_DIR = Path(__file__).resolve().parents[2]
AR_BOARD = PROJECT_DIR / "ar_board"
REAL_LAYOUT = AR_BOARD / "out_real" / "layout.json"
DEFECT_LAYOUT = AR_BOARD / "out_defect" / "layout.json"
REAL_BOARD = AR_BOARD / "out_real" / "board_A3.png"
DEFECT_BOARD = AR_BOARD / "out_defect" / "board_A3.png"
REFERENCES = AR_BOARD / "references.json"
MARKER_LAYOUT = AR_BOARD / "out_real_markers" / "layout.json"
MARKER_BOARD = AR_BOARD / "out_real_markers" / "board_A3.png"

pytestmark = pytest.mark.skipif(
    not all(
        path.is_file()
        for path in (
            REAL_LAYOUT,
            DEFECT_LAYOUT,
            REAL_BOARD,
            DEFECT_BOARD,
            REFERENCES,
        )
    ),
    reason="нет эталонов visual_align",
)


def _frame(path: Path, width: int = 1280, height: int = 720) -> np.ndarray:
    raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    board = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    h, w = board.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    inset, tilt = 0.06, 0.01
    dst = np.float32([
        [width * inset, height * inset],
        [width * (1 - inset), height * (inset + tilt)],
        [width * (1 - inset - tilt), height * (1 - inset)],
        [width * (inset + tilt), height * (1 - inset)],
    ])
    matrix = cv2.getPerspectiveTransform(src, dst)
    frame = np.full((height, width, 3), (28, 30, 34), np.uint8)
    cv2.warpPerspective(board, matrix, (width, height), dst=frame,
                        borderMode=cv2.BORDER_TRANSPARENT)
    return frame


@pytest.fixture(scope="module")
def feature_pipeline() -> BoardPipeline:
    return BoardPipeline(
        layout_path=str(REAL_LAYOUT),
        defects_enabled=False,
        align_mode="features",
        align_every=2,
    )


@pytest.fixture(scope="module")
def registry() -> VisualBoardRegistry:
    return VisualBoardRegistry(REFERENCES, every=2)


def test_registry_chooses_clean_and_defect(registry) -> None:
    clean = registry.register(_frame(REAL_BOARD), "PUMP-001", now=100.0)
    assert clean is not None
    assert clean.board_id == "PUMP-001"
    assert clean.result.inliers > 50
    assert clean.result.confidence > 0.8

    defect = registry.register(_frame(DEFECT_BOARD), "PUMP-001", now=101.0)
    assert defect is not None
    assert defect.board_id == "PUMP-002"
    assert defect.auto_switched is True


def test_hold_last_pose_then_reject(registry) -> None:
    registry.reset()
    good = registry.register(_frame(REAL_BOARD), "PUMP-001", now=200.0)
    assert good is not None and not good.result.stale

    noise = np.random.default_rng(1).integers(0, 255, (720, 1280, 3), np.uint8)
    held = registry.register(noise, "PUMP-001", now=200.2)
    assert held is not None
    assert held.result.stale is True
    assert 0.0 <= held.result.age_sec <= 1.5

    expired = registry.register(noise, "PUMP-001", now=203.0)
    assert expired is None


def test_feature_pipeline_returns_board_fields(feature_pipeline) -> None:
    feature_pipeline.apply_layout(str(REAL_LAYOUT))
    result = feature_pipeline.process(
        Image.fromarray(cv2.cvtColor(_frame(REAL_BOARD), cv2.COLOR_BGR2RGB)),
        "PUMP-001",
    )
    board = result.board
    assert board["detected"] is True
    assert board["board_id"] == "PUMP-001"
    assert board["method"] == "features"
    assert board["inliers"] > 50
    assert board["inlier_ratio"] > 0.3
    assert board["stale"] is False
    assert len(result.overlays) == 5
    assert board["confidence"] > 0.8


def test_switch_board_resets_layout(feature_pipeline) -> None:
    feature_pipeline.apply_layout(str(REAL_LAYOUT))
    feature_pipeline.process(
        Image.fromarray(cv2.cvtColor(_frame(REAL_BOARD), cv2.COLOR_BGR2RGB)),
        "PUMP-001",
    )
    result = feature_pipeline.process(
        Image.fromarray(cv2.cvtColor(_frame(DEFECT_BOARD), cv2.COLOR_BGR2RGB)),
        "PUMP-001",
    )
    assert result.board["board_id"] == "PUMP-002"
    assert result.board["auto_switched"] is True
    assert len(result.overlays) == 4
    assert feature_pipeline.board_id == "PUMP-002"


def test_random_frame_no_match(feature_pipeline) -> None:
    feature_pipeline.apply_layout(str(REAL_LAYOUT))
    noise = np.random.default_rng(2).integers(0, 255, (720, 1280, 3), np.uint8)
    result = feature_pipeline.process(
        Image.fromarray(cv2.cvtColor(noise, cv2.COLOR_BGR2RGB)), "PUMP-001"
    )
    assert result.board["detected"] is False
    assert result.board["reason"] == "no_match"
    assert result.overlays == []
    assert result.detections == []


@pytest.mark.skipif(
    not (MARKER_LAYOUT.is_file() and MARKER_BOARD.is_file()),
    reason="нет маркерных планшетов",
)
def test_align_mode_aruco_uses_markers() -> None:
    pipeline = BoardPipeline(
        layout_path=str(MARKER_LAYOUT),
        defects_enabled=False,
        align_mode="aruco",
    )
    result = pipeline.process(
        Image.fromarray(cv2.cvtColor(_frame(MARKER_BOARD), cv2.COLOR_BGR2RGB)),
        "PUMP-001",
    )
    assert result.board["detected"] is True
    assert result.board["method"] != "features"
    assert result.board["markers"] >= 3
    assert result.board["inliers"] == 0
