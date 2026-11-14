"""Состояние узлов после осмотра (`POST /inspections`).

Осмотр обновляет `condition` каждого узла, который был виден в момент
осмотра: подтверждённый дефект или визуальный признак → `possible_defect`,
слабое наблюдение → `check_required`, чистый видимый узел → `ok`.
Узел вне кадра состояние не меняет — история не затирается.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import update

from app import analytics, db
from app.cv.detector import CLASS_IDS
from app.main import app
from app.models import Component

EQUIPMENT = "PUMP-002"

# Узлы, которые видит камера в «дефектном» кадре. Манометра в кадре нет.
VISIBLE_BOXES = {
    "motor": [0.02, 0.02, 0.30, 0.30],
    "pump": [0.32, 0.02, 0.60, 0.30],
    "coupling_guard": [0.62, 0.02, 0.90, 0.30],
    "flange": [0.02, 0.32, 0.30, 0.60],
}


def _detection(
    class_name: str,
    kind: str,
    confidence: float,
    bbox: list[float],
    component_id: str | None = None,
) -> dict:
    payload = {
        "class_id": CLASS_IDS[class_name],
        "class_name": class_name,
        "kind": kind,
        "confidence": confidence,
        "bbox": bbox,
    }
    if component_id is not None:
        payload["component_id"] = component_id
    return payload


def _feed_analytics(equipment_id: str, moment: datetime) -> None:
    """Три кадра окна: дефекты motor/pump + подтверждённая коррозия flange."""
    statuses = [
        {
            "component_id": name,
            "bbox": box,
            "status": "ok",
            "confidence": 0.9,
            "defects": [],
        }
        for name, box in VISIBLE_BOXES.items()
    ]
    detections = [
        _detection("oil_leak", "defect", 0.85, VISIBLE_BOXES["motor"], "motor"),
        _detection("corrosion", "defect", 0.80, VISIBLE_BOXES["pump"], "pump"),
    ]
    signs = [
        {
            "component_id": "flange",
            "sign": "corrosion",
            "present": True,
            "confidence": 0.9,
            "source": "cv",
            "area_pct": 14.0,
        }
    ]
    # Три кадра в одном окне: дефекты и признаки становятся подтверждёнными.
    for index in range(3):
        analytics.update(
            equipment_id,
            detections=detections,
            components_status=statuses,
            signs=signs,
            now=moment + timedelta(seconds=index * 0.2),
        )


def _set_condition(equipment_id: str, name: str, condition: str) -> None:
    with db.session_scope() as session:
        session.execute(
            update(Component)
            .where(
                Component.equipment_id == equipment_id,
                Component.name == name,
            )
            .values(condition=condition)
        )


def _conditions(equipment_id: str) -> dict[str, str]:
    return {item["name"]: item["condition"] for item in db.get_components(equipment_id)}


def test_inspection_updates_all_visible_nodes(memory_db) -> None:
    analytics.reset()
    try:
        detections = [
            _detection(name, "component", 0.9, box)
            for name, box in VISIBLE_BOXES.items()
        ]
        detections += [
            _detection("oil_leak", "defect", 0.85, VISIBLE_BOXES["motor"]),
            _detection("corrosion", "defect", 0.80, VISIBLE_BOXES["pump"]),
        ]

        with TestClient(app) as client:
            # Старт приложения пересидирует карточки, поэтому историю задаём
            # после старта: узел вне кадра был в «требует проверки».
            _set_condition(EQUIPMENT, "manometer", "check_required")
            _feed_analytics(EQUIPMENT, datetime.now(timezone.utc))
            response = client.post(
                "/inspections",
                json={"equipment_id": EQUIPMENT, "detections": detections},
            )
        assert response.status_code == 201, response.text

        statuses = {
            item["component_id"]: item["status"]
            for item in response.json()["components_status"]
        }
        # Дефекты и подтверждённая коррозия — possible_defect, чистый узел — ok.
        assert statuses["motor"] == "possible_defect"
        assert statuses["pump"] == "possible_defect"
        assert statuses["flange"] == "possible_defect"  # коррозия из signs
        assert statuses["coupling_guard"] == "ok"
        assert statuses["manometer"] == "insufficient_data"

        conditions = _conditions(EQUIPMENT)
        assert conditions["motor"] == "possible_defect"
        assert conditions["pump"] == "possible_defect"
        assert conditions["flange"] == "possible_defect"
        assert conditions["coupling_guard"] == "ok"
        # Невидимый узел: состояние осталось прежним, история не затёрта.
        assert conditions["manometer"] == "check_required"
    finally:
        analytics.reset()
