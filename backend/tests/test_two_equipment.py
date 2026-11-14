"""Два объекта с разными QR: PUMP-001 (норма) и PUMP-002 (дефектный участок).

Проверяются: идемпотентный сид двух карточек, поиск по QR, поля
`board.equipment_id` / `equipment_mismatch` / `hint` в `/analyze` и QR-файлы.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.main import app
from app.seed import seed_all

PROJECT_DIR = Path(__file__).resolve().parents[2]
QR_DIR = PROJECT_DIR / "ar_board" / "qr"
DEFECT_COMPONENTS = {"pump", "coupling_guard"}


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (96, 64), (245, 245, 245)).save(buffer, format="PNG")
    return buffer.getvalue()


class _StubPipeline:
    """Пайплайн-заглушка: проверяем контракт ответа, а не CV."""

    def __init__(self, board_id: str) -> None:
        self.board_id = board_id

    def process(self, image: Image.Image, equipment_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            board={
                "detected": True,
                "board_id": self.board_id,
                "method": "features",
                "confidence": 0.95,
            },
            overlays=[],
            detections=[],
            ocr=None,
            signs=[],
        )


def _analyze(client: TestClient, equipment_id: str) -> dict:
    response = client.post(
        "/analyze",
        params={"equipment_id": equipment_id},
        files={"image": ("frame.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()


# Данные: две карточки, повторный сид не дублирует


def test_seed_two_equipment_idempotent(memory_db) -> None:
    before = {item["id"]: item for item in db.list_equipment()}
    assert set(before) == {"PUMP-001", "PUMP-002"}
    assert before["PUMP-001"]["components_count"] == 5
    assert before["PUMP-002"]["components_count"] == 5

    # Повторный сид: ни одной новой карточки/узла.
    result = seed_all()
    assert result["equipment_created"] is False
    assert result["equipment_created_ids"] == []
    after = {item["id"]: item for item in db.list_equipment()}
    assert set(after) == {"PUMP-001", "PUMP-002"}
    assert after["PUMP-002"]["components_count"] == 5
    assert len(db.get_components("PUMP-002")) == 5

    clean = db.get_equipment("PUMP-001")
    defect = db.get_equipment("PUMP-002")
    assert clean is not None and defect is not None
    assert clean["qr_code"] == "PUMP-001"
    assert defect["qr_code"] == "PUMP-002"
    assert defect["name"] == "Насосный агрегат (дефектный участок)"
    assert defect["model"] == clean["model"] == "DEMO-PUMP"
    assert defect["location"] == "Демонстрационный стенд"

    conditions = {item["name"]: item["condition"] for item in db.get_components("PUMP-002")}
    assert conditions["pump"] == "possible_defect"
    assert conditions["coupling_guard"] == "possible_defect"
    assert conditions["motor"] == "ok"


# API: список, карточка и поиск по QR


def test_equipment_list_card_and_qr_lookup(memory_db) -> None:
    with TestClient(app) as client:
        items = client.get("/equipment").json()
        assert {item["id"] for item in items} == {"PUMP-001", "PUMP-002"}

        found = client.get("/equipment", params={"qr": "PUMP-002"}).json()
        assert len(found) == 1
        assert found[0]["id"] == "PUMP-002"
        assert found[0]["qr_code"] == "PUMP-002"

        card = client.get("/equipment/PUMP-002").json()
        assert len(card["components"]) == 5
        assert {item["name"] for item in card["components"]} == {
            "motor",
            "pump",
            "coupling_guard",
            "flange",
            "manometer",
        }
        conditions = {item["name"]: item["condition"] for item in card["components"]}
        assert {name for name in DEFECT_COMPONENTS if conditions[name] == "possible_defect"} == DEFECT_COMPONENTS


# Ответ /analyze: equipment_id и сверка планшета с запросом


def test_analyze_board_equipment_id_matches(memory_db, monkeypatch) -> None:
    with TestClient(app) as client:
        monkeypatch.setattr(app.state, "pipeline", _StubPipeline("PUMP-002"))
        payload = _analyze(client, "PUMP-002")
        board = payload["board"]
        assert board["board_id"] == "PUMP-002"
        assert board["equipment_id"] == "PUMP-002"
        assert board["equipment_mismatch"] is False
        assert board["hint"] is None


def test_analyze_reports_mismatch_but_keeps_requested_equipment(
    memory_db, monkeypatch
) -> None:
    with TestClient(app) as client:
        monkeypatch.setattr(app.state, "pipeline", _StubPipeline("PUMP-002"))
        client.post("/analytics/reset")
        payload = _analyze(client, "PUMP-001")

        # Аналитика ведётся по запрошенному агрегату, а не по планшету в кадре.
        assert payload["equipment_id"] == "PUMP-001"
        assert payload["analytics"]["frames"] == 1
        assert client.get("/equipment/PUMP-001/analytics").json()["frames"] == 1
        assert client.get("/equipment/PUMP-002/analytics").json()["frames"] == 0

        board = payload["board"]
        assert board["board_id"] == "PUMP-002"
        assert board["equipment_id"] == "PUMP-002"
        assert board["equipment_mismatch"] is True
        assert "PUMP-002" in board["hint"]
        assert board["hint"] == "в кадре другой агрегат: PUMP-002"


# QR: два файла и переключатель 1/2


def _decode_qr(path: Path) -> str:
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    data, _points, _straight = cv2.QRCodeDetector().detectAndDecode(image)
    return str(data)


def test_qr_files_for_both_equipment() -> None:
    assert _decode_qr(QR_DIR / "PUMP-001.png") == "PUMP-001"
    assert _decode_qr(QR_DIR / "PUMP-002.png") == "PUMP-002"

    html = (QR_DIR / "qr_card.html").read_text(encoding="utf-8")
    for token in ("PUMP-001.png", "PUMP-002.png", 'event.key === "1"', 'event.key === "2"'):
        assert token in html
