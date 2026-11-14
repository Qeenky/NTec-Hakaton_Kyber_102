"""`POST /analyze`: фото из галереи проходит тот же пайплайн, что `/ws/frame`."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import cv_state
from app.cv.pipeline import BoardDetector, BoardPipeline
from app.main import MAX_ANALYZE_BYTES, app

PROJECT_DIR = Path(__file__).resolve().parents[2]
LAYOUT_PATH = PROJECT_DIR / "ar_board" / "out" / "layout.json"
BOARD_PATH = PROJECT_DIR / "ar_board" / "out" / "board_A3.png"
GAUGE_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "manometer_board.json"

pytestmark = pytest.mark.skipif(
    not (LAYOUT_PATH.is_file() and BOARD_PATH.is_file()),
    reason="нет ar_board/out/layout.json или board_A3.png",
)

EQUIPMENT = "PUMP-001"
COMPONENT_IDS = {"motor", "pump", "coupling_guard", "flange", "manometer"}


@pytest.fixture()
def board_client(memory_db):
    """TestClient с настоящим BoardPipeline (как в тестах /ws/frame)."""
    with TestClient(app) as client:
        app.state.detector = BoardDetector(
            BoardPipeline(
                layout_path=str(LAYOUT_PATH),
                gauge_config_path=str(GAUGE_CONFIG),
                ocr_every=1,
            )
        )
        app.state.pipeline = app.state.detector.pipeline
        yield client


@pytest.fixture(scope="module")
def board_bytes() -> bytes:
    return BOARD_PATH.read_bytes()


def _blank_jpeg() -> bytes:
    image = Image.new("RGB", (640, 480), (38, 40, 46))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


def test_analyze_board_returns_ws_frame_contract(board_client, board_bytes) -> None:
    response = board_client.post(
        "/analyze",
        params={"equipment_id": EQUIPMENT},
        files={"image": ("board_A3.png", board_bytes, "image/png")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["equipment_id"] == EQUIPMENT
    assert payload["image_width"] == 4961
    assert payload["image_height"] == 3508
    assert payload["board"]["detected"] is True
    assert payload["board"]["markers"] == 4

    assert len(payload["overlays"]) == 5
    assert {item["component_id"] for item in payload["overlays"]} == COMPONENT_IDS
    assert all(item["polygon"] for item in payload["overlays"])

    assert payload["ocr"]["value"] is not None
    assert payload["ocr"]["unit"] == "бар"
    assert len(payload["components_status"]) >= 5
    assert {item["component_id"] for item in payload["components_status"]} >= COMPONENT_IDS
    assert all(item["source"] == "board" for item in payload["detections"])
    assert payload["inference_ms"] >= 0
    # Прежний контракт кадра на месте.
    for key in ("detections", "active_diagnostics", "blocked_diagnostics"):
        assert key in payload


def test_analyze_photo_without_markers(board_client) -> None:
    response = board_client.post(
        "/analyze",
        files={"image": ("photo.jpg", _blank_jpeg(), "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["board"]["detected"] is False
    assert payload["board"]["reason"] == "no_markers"
    assert payload["overlays"] == []
    assert payload["detections"] == []


def test_analyze_updates_cv_state_and_diagnostics(board_client, board_bytes) -> None:
    response = board_client.post(
        "/analyze", files={"image": ("board_A3.png", board_bytes, "image/png")}
    )
    assert response.status_code == 200, response.text
    ocr_value = response.json()["ocr"]["value"]

    snapshot = cv_state.ocr_snapshot(EQUIPMENT)
    assert snapshot is not None
    assert snapshot["value"] == pytest.approx(ocr_value)
    assert cv_state.snapshot(EQUIPMENT).observations, "детекции не попали в cv_state"

    diagnostics = board_client.get(f"/equipment/{EQUIPMENT}/diagnostics").json()
    assert diagnostics["camera"]["ocr"]["value"] == pytest.approx(ocr_value)
    blocked = {item["rule_id"]: item["blocked_by"] for item in diagnostics["blocked"]}
    active = {item["rule_id"] for item in diagnostics["active"]}
    assert "gauge_drift" in active | set(blocked)
    assert blocked.get("gauge_drift") != "ocr_unavailable"


def test_analyze_unknown_equipment_404(board_client) -> None:
    response = board_client.post(
        "/analyze",
        params={"equipment_id": "UNKNOWN-1"},
        files={"image": ("photo.jpg", _blank_jpeg(), "image/jpeg")},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Оборудование не найдено"


def test_analyze_not_an_image_422(board_client) -> None:
    response = board_client.post(
        "/analyze",
        files={"image": ("notes.txt", b"not an image at all", "text/plain")},
    )
    assert response.status_code == 422
    assert "изображением" in response.json()["detail"]


def test_analyze_too_large_413(board_client) -> None:
    oversized = b"\x00" * (MAX_ANALYZE_BYTES + 1)
    response = board_client.post(
        "/analyze",
        files={"image": ("huge.jpg", oversized, "image/jpeg")},
    )
    assert response.status_code == 413
    assert "15 МБ" in response.json()["detail"]
