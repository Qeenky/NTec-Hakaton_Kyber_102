"""Горячая подмена layout: `GET/POST /pipeline/layout` без рестарта сервера.

Проверяется демо-сценарий защиты: чистый планшет (5 зон) → `POST` на дефектный
макет (4 зоны) → следующий кадр приходит с новым набором `overlays`, при этом
активные WebSocket-сессии и формат ответа не меняются.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.cv.pipeline import BoardDetector, BoardPipeline
from app.main import app

PROJECT_DIR = Path(__file__).resolve().parents[2]
REAL_LAYOUT = PROJECT_DIR / "ar_board" / "out_real" / "layout.json"
DEFECT_LAYOUT = PROJECT_DIR / "ar_board" / "out_defect" / "layout.json"
REAL_BOARD = PROJECT_DIR / "ar_board" / "out_real" / "board_A3.png"
DEFECT_BOARD = PROJECT_DIR / "ar_board" / "out_defect" / "board_A3.png"
GAUGE_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "manometer_board.json"

pytestmark = pytest.mark.skipif(
    not all(
        path.is_file()
        for path in (REAL_LAYOUT, DEFECT_LAYOUT, REAL_BOARD, DEFECT_BOARD)
    ),
    reason="нет ar_board/out_real или ar_board/out_defect",
)

EQUIPMENT = "PUMP-001"
REAL_IDS = {"motor", "pump", "coupling_guard", "flange", "manometer"}
DEFECT_IDS = {"motor", "pump", "coupling_guard", "flange"}


@lru_cache(maxsize=4)
def _jpeg_frame(board_path: str, width: int = 1280, height: int = 720) -> bytes:
    """Планшет в кадре камеры (перспектива + поля), как на демо."""
    raw = np.frombuffer(Path(board_path).read_bytes(), dtype=np.uint8)
    board = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    assert board is not None
    board_h, board_w = board.shape[:2]
    inset, tilt = 0.06, 0.01
    source = np.float32([[0, 0], [board_w, 0], [board_w, board_h], [0, board_h]])
    target = np.float32(
        [
            [width * inset, height * inset],
            [width * (1 - inset), height * (inset + tilt)],
            [width * (1 - inset - tilt), height * (1 - inset)],
            [width * (inset + tilt), height * (1 - inset)],
        ]
    )
    matrix = cv2.getPerspectiveTransform(source, target)
    frame = np.full((height, width, 3), (28, 30, 34), dtype=np.uint8)
    cv2.warpPerspective(
        board,
        matrix,
        (width, height),
        dst=frame,
        borderMode=cv2.BORDER_TRANSPARENT,
    )
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    assert ok
    return buffer.tobytes()


@pytest.fixture()
def board_client(memory_db):
    """TestClient с настоящим пайплайном на чистом планшете `out_real`."""
    with TestClient(app) as client:
        pipeline = BoardPipeline(
            layout_path=str(REAL_LAYOUT),
            gauge_config_path=str(GAUGE_CONFIG),
            ocr_every=1,
        align_mode="features",
        )
        app.state.detector = BoardDetector(pipeline)
        app.state.pipeline = pipeline
        yield client


def _analyze(client: TestClient, board_path: Path) -> dict:
    response = client.post(
        "/analyze",
        params={"equipment_id": EQUIPMENT},
        files={"image": (board_path.name, _jpeg_frame(str(board_path)), "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_get_layout_reports_current_state(board_client) -> None:
    response = board_client.get("/pipeline/layout")
    assert response.status_code == 200, response.text
    state = response.json()

    assert Path(state["path"]) == REAL_LAYOUT.resolve()
    assert state["exists"] is True
    assert state["components"] == 5
    assert set(state["component_ids"]) == REAL_IDS
    assert state["layout_version"] == 1
    assert state["mtime"] and state["mtime_epoch"] > 0
    assert state["gauge_ocr"] is True


def test_post_layout_switches_pipeline_without_restart(board_client) -> None:
    pipeline = app.state.pipeline
    # Первый кадр прогревает Smoother и OCR-кэш.
    first = _analyze(board_client, REAL_BOARD)
    assert len(first["overlays"]) == 5
    assert pipeline._smoothers, "Smoother не заполнился"
    assert pipeline.roi_matrix is not None

    revision_before = board_client.get("/pipeline/layout").json()["revision"]
    response = board_client.post(
        "/pipeline/layout",
        json={"path": "../ar_board/out_defect/layout.json"},
    )
    assert response.status_code == 200, response.text
    state = response.json()

    assert state["reloaded"] is True
    assert Path(state["path"]).name == "layout.json"
    assert "out_defect" in Path(state["path"]).parts
    assert state["components"] == 4
    assert set(state["component_ids"]) == DEFECT_IDS
    # Ручная подмена — ровно одна ревизия поверх текущей (первый кадр мог
    # сам переключить layout на эталон visual_align).
    assert state["revision"] == revision_before + 1
    assert state["gauge_ocr"] is False
    # Сглаживание и OCR-кэш сброшены, сам объект пайплайна тот же.
    assert app.state.pipeline is pipeline
    assert pipeline._smoothers == {}
    assert pipeline._last_ocr == {}
    assert pipeline.roi_matrix is None


def test_analyze_follows_switched_layout(board_client) -> None:
    # 1) Чистый планшет по layout `out_real` — 5 зон.
    clean = _analyze(board_client, REAL_BOARD)
    assert clean["board"]["detected"] is True
    assert len(clean["overlays"]) == 5
    assert {item["component_id"] for item in clean["overlays"]} == REAL_IDS

    # 2) Переключаем планшет на дефектный макет.
    switched = board_client.post(
        "/pipeline/layout",
        json={"path": "../ar_board/out_defect/layout.json"},
    )
    assert switched.status_code == 200, switched.text

    # 3) Дефектный планшет: 4 зоны, ровно как в layout, OCR отключён.
    defect = _analyze(board_client, DEFECT_BOARD)
    assert defect["board"]["detected"] is True
    assert len(defect["overlays"]) == 4
    assert {item["component_id"] for item in defect["overlays"]} == DEFECT_IDS
    assert defect["ocr"] is None

    # 4) Снова чистый планшет: feature-привязка узнаёт эталон clean и
    #    автоматически возвращает layout clean (5 зон), окно сбрасывается.
    clean_again = _analyze(board_client, REAL_BOARD)
    assert clean_again["board"]["detected"] is True
    assert clean_again["board"]["board_id"] == "PUMP-001"
    assert clean_again["board"]["auto_switched"] is True
    assert len(clean_again["overlays"]) == 5
    assert {item["component_id"] for item in clean_again["overlays"]} == REAL_IDS


def test_layout_switch_errors_keep_current_layout(board_client, tmp_path) -> None:
    missing = board_client.post(
        "/pipeline/layout",
        json={"path": "../ar_board/out_defect/no_such_layout.json"},
    )
    assert missing.status_code == 404
    assert "не найден" in missing.json()["detail"]

    broken = tmp_path / "broken_layout.json"
    broken.write_text('{"board": {', encoding="utf-8")
    invalid_json = board_client.post("/pipeline/layout", json={"path": str(broken)})
    assert invalid_json.status_code == 422

    layout = json.loads(REAL_LAYOUT.read_text(encoding="utf-8"))
    layout["components"] = []
    empty = tmp_path / "empty_components.json"
    empty.write_text(json.dumps(layout), encoding="utf-8")
    empty_response = board_client.post("/pipeline/layout", json={"path": str(empty)})
    assert empty_response.status_code == 422

    # Ни одна неудачная попытка не меняет работающий пайплайн.
    state = board_client.get("/pipeline/layout").json()
    assert Path(state["path"]) == REAL_LAYOUT.resolve()
    assert state["components"] == 5
    assert state["revision"] == 0
