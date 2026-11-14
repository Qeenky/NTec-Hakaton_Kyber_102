"""quad-fallback в реестре: маленький лист, приоритет признаков, счётчики.

Кадры синтетические: настоящий печатный планшет A3 (`out_real/board_A3.png`)
накладывается на тёмный фон в масштабе 22–90% кадра. На 22% признаков для
ORB/AKAZE не хватает, и реестр обязан спасаться контуром листа.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.cv.board_registry import QUAD_FALLBACK_INLIERS, VisualBoardRegistry

PROJECT_DIR = Path(__file__).resolve().parents[2]
AR_BOARD = PROJECT_DIR / "ar_board"
REFERENCES = AR_BOARD / "references.json"
REAL_BOARD = AR_BOARD / "out_real" / "board_A3.png"

pytestmark = pytest.mark.skipif(
    not all(path.is_file() for path in (REFERENCES, REAL_BOARD)),
    reason="нет эталонов visual_align",
)

EQUIPMENT = "PUMP-001"
# photo_rect_px обрезка PUMP-001 внутри A3 (см. ar_board/out_real/layout.json).
CROP_OFFSET = np.array([[1.0, 0.0, 1730.0], [0.0, 1.0, 1004.0], [0.0, 0.0, 1.0]])
CROP_SIZE = (1500.0, 1500.0)


def _load(path: Path) -> np.ndarray:
    raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    board = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    assert board is not None
    return board


def _sheet_frame(board: np.ndarray, coverage: float,
                 width: int = 1280, height: int = 720) -> tuple[np.ndarray, np.ndarray]:
    """Планшет на тёмном фоне; coverage — доля кадра по длинной стороне."""
    board_h, board_w = board.shape[:2]
    scale = min(width * coverage / board_w, height * coverage / board_h)
    target_w, target_h = board_w * scale, board_h * scale
    x0, y0 = (width - target_w) / 2.0, (height - target_h) / 2.0
    source = np.float32([[0, 0], [board_w, 0], [board_w, board_h], [0, board_h]])
    target = np.float32([[x0, y0], [x0 + target_w, y0],
                         [x0 + target_w, y0 + target_h], [x0, y0 + target_h]])
    matrix = cv2.getPerspectiveTransform(source, target)
    frame = np.full((height, width, 3), (28, 30, 34), np.uint8)
    cv2.warpPerspective(board, matrix, (width, height), dst=frame,
                        borderMode=cv2.BORDER_TRANSPARENT)
    return frame, matrix


def _corner_error(homography: np.ndarray, truth: np.ndarray) -> float:
    w, h = CROP_SIZE
    corners = np.array([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]], np.float64)
    estimated = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), homography)
    expected = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), truth)
    return float(np.linalg.norm(estimated - expected, axis=2).max())


CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "align_cache"


@pytest.fixture(scope="module")
def registry() -> VisualBoardRegistry:
    return VisualBoardRegistry(REFERENCES, CACHE_DIR, every=1)


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in after}


def test_quad_fallback_on_small_sheet(registry) -> None:
    """22% кадра: признаки не тянут, quad возвращает точную позу."""
    before = dict(registry.info()["stats"])
    registry.reset()
    board = _load(REAL_BOARD)
    frame, sheet_homography = _sheet_frame(board, 0.22)

    match = registry.register(frame, EQUIPMENT, now=1.0)
    assert match is not None
    assert match.board_id == EQUIPMENT
    assert match.result.method == "quad"
    assert match.result.ok and not match.result.stale
    assert 0.0 < match.result.confidence <= 1.0
    assert match.result.ncc > 0.3

    truth = sheet_homography @ CROP_OFFSET
    assert _corner_error(match.result.homography, truth) < 10.0

    stats = _delta(before, registry.info()["stats"])
    assert stats["frames"] == 1
    assert stats["quad"] == 1
    assert stats["features"] == 0
    assert stats["quad_attempts"] >= 1


def test_features_have_priority_on_large_sheet(registry) -> None:
    """90% кадра: признаки уверенно выигрывают, quad не используется."""
    before = dict(registry.info()["stats"])
    registry.reset()
    frame, _ = _sheet_frame(_load(REAL_BOARD), 0.90)

    match = registry.register(frame, EQUIPMENT, now=10.0)
    assert match is not None
    assert match.result.method in {"orb", "akaze"}
    assert match.result.inliers >= QUAD_FALLBACK_INLIERS
    stats = _delta(before, registry.info()["stats"])
    assert stats["features"] == 1
    assert stats["quad"] == 0


def test_quad_fallback_can_be_disabled() -> None:
    """С выключенным fallback маленький лист честно не привязывается."""
    registry = VisualBoardRegistry(REFERENCES, CACHE_DIR, every=1, quad_fallback=False)
    frame, _ = _sheet_frame(_load(REAL_BOARD), 0.22)
    assert registry.register(frame, EQUIPMENT, now=20.0) is None
    stats = registry.info()["stats"]
    assert stats["quad"] == 0
    assert stats["no_match"] == 1


def test_no_quad_false_positive_on_noise(registry) -> None:
    """Шум и постороннее фото не должны давать quad-привязку."""
    before = dict(registry.info()["stats"])
    registry.reset()
    noise = np.random.default_rng(3).integers(0, 255, (720, 1280, 3), np.uint8)
    assert registry.register(noise, EQUIPMENT, now=30.0) is None
    stats = _delta(before, registry.info()["stats"])
    assert stats["quad"] == 0
    assert stats["no_match"] == 1


def test_info_exposes_quad_stats(registry) -> None:
    info = registry.info()
    assert info["quad_fallback"] is True
    assert info["quad_fallback_inliers"] == QUAD_FALLBACK_INLIERS
    assert set(info["stats"]) >= {"frames", "features", "quad", "no_match"}
    assert any(board["sheet_rect"] for board in info["boards"])
