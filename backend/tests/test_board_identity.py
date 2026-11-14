"""Автоопределение планшета по identity-маркеру и автопереключение layout.

Проверяются: реестр `boards.json`, выбор layout по маркеру 7/8, гистерезис
(3 подтверждения + пауза), graceful fallback без реестра и кадр без
identity-маркера. Кадры — настоящие печатные планшеты `out_real`/`out_defect`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import analytics
from app.cv.board_registry import layout_board_info, load_board_registry
from app.cv.pipeline import BoardDetector, BoardPipeline
from app.main import app

PROJECT_DIR = Path(__file__).resolve().parents[2]
AR_BOARD = PROJECT_DIR / "ar_board"
REAL_LAYOUT = AR_BOARD / "out_real_markers" / "layout.json"
DEFECT_LAYOUT = AR_BOARD / "out_defect_markers" / "layout.json"
REAL_BOARD = AR_BOARD / "out_real_markers" / "board_A3.png"
DEFECT_BOARD = AR_BOARD / "out_defect_markers" / "board_A3.png"
SCHEMATIC_BOARD = AR_BOARD / "out" / "board_A3.png"
SCHEMATIC_LAYOUT = AR_BOARD / "out" / "layout.json"
REGISTRY = AR_BOARD / "boards.json"
GAUGE_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "manometer_board.json"

pytestmark = pytest.mark.skipif(
    not all(
        path.is_file()
        for path in (
            REAL_LAYOUT,
            DEFECT_LAYOUT,
            REAL_BOARD,
            DEFECT_BOARD,
            SCHEMATIC_BOARD,
            REGISTRY,
        )
    ),
    reason="нет пересобранных планшетов/реестра ar_board",
)

EQUIPMENT = "PUMP-001"


def _warp(board: np.ndarray) -> np.ndarray:
    height, width = board.shape[:2]
    source = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    target = np.float32([[77, 43], [1203, 50], [1190, 677], [90, 662]])
    matrix = cv2.getPerspectiveTransform(source, target)
    frame = np.full((720, 1280, 3), (28, 30, 34), dtype=np.uint8)
    cv2.warpPerspective(
        board, matrix, (1280, 720), dst=frame, borderMode=cv2.BORDER_TRANSPARENT
    )
    return frame


def _load(path: Path) -> np.ndarray:
    raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    board = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    assert board is not None
    return board


@lru_cache(maxsize=8)
def _frame(path: str) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(_warp(_load(Path(path))), cv2.COLOR_BGR2RGB))


def _jpeg(path: Path) -> bytes:
    ok, buffer = cv2.imencode(
        ".jpg", _warp(_load(path)), [int(cv2.IMWRITE_JPEG_QUALITY), 88]
    )
    assert ok
    return buffer.tobytes()


def _pipeline(layout: Path, **kwargs) -> BoardPipeline:
    return BoardPipeline(
        layout_path=str(layout),
        gauge_config_path=str(GAUGE_CONFIG),
        ocr_every=1,
        **kwargs,
    )


# Реестр


def test_registry_maps_identity_to_layout(tmp_path) -> None:
    for name in ("out_real", "out_defect"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "layout.json").write_text('{"board": {}, "markers": []}', encoding="utf-8")
    registry_path = tmp_path / "boards.json"
    registry_path.write_text(
        json.dumps(
            {
                "boards": [
                    {"board_id": "clean", "identity_marker_id": 7,
                     "layout": "out_real/layout.json"},
                    {"board_id": "defect", "identity_marker_id": 8,
                     "layout": "out_defect/layout.json"},
                ]
            }
        ),
        encoding="utf-8",
    )

    registry = load_board_registry(registry_path)
    assert registry.available is True
    assert registry.identity_ids() == {7, 8}
    clean = registry.entry_for_marker(7)
    assert clean is not None and clean.board_id == "clean"
    assert clean.layout_path == (tmp_path / "out_real" / "layout.json").resolve()
    assert registry.entry_for_layout(clean.layout_path) == clean
    assert registry.entry_for_marker(99) is None

    # Нет файла — пустой реестр, без исключения.
    missing = load_board_registry(tmp_path / "no_such_registry.json")
    assert missing.available is False
    assert missing.info()["available"] is False


def test_layout_board_info_reads_new_block() -> None:
    layout = json.loads(REAL_LAYOUT.read_text(encoding="utf-8"))
    info = layout_board_info(layout)
    assert info == {
        "board_id": "clean",
        "identity_marker_id": 7,
        "guard_marker_id": 4,
    }
    defect = json.loads(DEFECT_LAYOUT.read_text(encoding="utf-8"))
    defect_info = layout_board_info(defect)
    assert defect_info["board_id"] == "defect"
    assert defect_info["identity_marker_id"] == 8
    # null в layout = «маркера кожуха нет» → проверяем стандартный id 4.
    assert defect_info["guard_marker_id"] == 4
    # Старый layout без блока идентификации — безопасные значения.
    assert layout_board_info({"board": {}})["identity_marker_id"] is None


# Автопереключение и гистерезис


def test_auto_switch_clean_by_identity_marker(monkeypatch) -> None:
    resets: list[str | None] = []
    monkeypatch.setattr(
        analytics, "reset", lambda equipment_id=None: resets.append(equipment_id)
    )
    pipeline = _pipeline(DEFECT_LAYOUT)
    clean = _frame(str(REAL_BOARD))

    first = pipeline.process(clean, EQUIPMENT)
    assert first.board["board_id"] == "clean"
    assert first.board["identity_marker_id"] == 7
    assert first.board["auto_switched"] is False
    assert pipeline.layout_info()["board_id"] == "defect"

    second = pipeline.process(clean, EQUIPMENT)
    assert second.board["auto_switched"] is False
    assert pipeline.layout_info()["board_id"] == "defect"

    third = pipeline.process(clean, EQUIPMENT)
    assert third.board["auto_switched"] is True
    assert third.board["board_id"] == "clean"
    assert third.board["detected"] is True
    assert Path(pipeline.layout_info()["path"]) == REAL_LAYOUT.resolve()
    assert pipeline.layout_info()["revision"] == 1
    # Окно аналитики сброшено ровно на кадре переключения.
    assert resets == [EQUIPMENT]
    # Счётчик OCR начат заново: 1, а не 4-й кадр.
    assert pipeline._counters[EQUIPMENT] == 1


def test_alternating_boards_switch_throttled(monkeypatch) -> None:
    resets: list[str | None] = []
    monkeypatch.setattr(
        analytics, "reset", lambda equipment_id=None: resets.append(equipment_id)
    )
    pipeline = _pipeline(REAL_LAYOUT)
    clean = _frame(str(REAL_BOARD))
    defect = _frame(str(DEFECT_BOARD))

    switches: list[int] = []
    for index in range(12):
        frame = clean if index % 2 == 0 else defect
        result = pipeline.process(frame, EQUIPMENT)
        if result.board["auto_switched"]:
            switches.append(index + 1)

    # 3 подтверждения в окне 5 кадров: первое переключение на 6-м кадре,
    # затем пауза 3 кадра → следующее не раньше 9-го (фактически 11-е).
    assert switches == [6, 11]
    assert all(right - left >= 3 for left, right in zip(switches, switches[1:]))
    assert resets == [EQUIPMENT, EQUIPMENT]


def test_missing_registry_keeps_current_layout() -> None:
    pipeline = _pipeline(
        DEFECT_LAYOUT,
        board_registry_path=str(AR_BOARD / "no_such_boards.json"),
    )
    clean = _frame(str(REAL_BOARD))
    result = pipeline.process(clean, EQUIPMENT)

    # Реестра нет: маркер 7 не ищем (в текущем layout ожидается 8), layout
    # остаётся прежним, кадр обрабатывается без ошибок.
    assert result.board["board_registry"] is False
    assert result.board["auto_switched"] is False
    assert result.board["detected"] is True
    assert Path(pipeline.layout_info()["path"]) == DEFECT_LAYOUT.resolve()
    assert pipeline.layout_info()["revision"] == 0


def test_frame_without_identity_marker_keeps_layout(tmp_path) -> None:
    layout = json.loads(REAL_LAYOUT.read_text(encoding="utf-8"))
    layout["board"]["identity_marker_id"] = 7
    layout_path = tmp_path / "layout.json"
    layout_path.write_text(json.dumps(layout), encoding="utf-8")

    pipeline = _pipeline(layout_path)
    before = pipeline.layout_info()["path"]
    # Схематичный планшет: угловые маркеры 0..3 есть, identity 7 — нет.
    result = pipeline.process(_frame(str(SCHEMATIC_BOARD)), EQUIPMENT)

    assert result.board["detected"] is False
    assert result.board["reason"] == "identity_missing"
    assert result.board["identity_detected"] is False
    assert result.board["auto_switched"] is False
    assert result.overlays, "геометрия должна считаться по текущему layout"
    assert pipeline.layout_info()["path"] == before


# API: те же поля в /analyze и /pipeline/layout


def test_analyze_and_layout_endpoint_expose_board(memory_db) -> None:
    with TestClient(app) as client:
        pipeline = _pipeline(DEFECT_LAYOUT)
        app.state.detector = BoardDetector(pipeline)
        app.state.pipeline = pipeline

        payload: dict = {}
        for index in range(3):
            response = client.post(
                "/analyze",
                params={"equipment_id": EQUIPMENT},
                files={"image": ("clean.jpg", _jpeg(REAL_BOARD), "image/jpeg")},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["board"]["board_id"] == "clean"
            assert payload["board"]["identity_marker_id"] == 7
            assert payload["board"]["identity_detected"] is True
            assert payload["board"]["auto_switched"] is (index == 2)

        state = client.get("/pipeline/layout").json()
        assert state["board_id"] == "clean"
        assert state["identity_marker_id"] == 7
        assert state["guard_marker_id"] == 4
        assert state["board_registry"]["available"] is True
        assert Path(state["path"]) == REAL_LAYOUT.resolve()
        assert state["revision"] == 1
