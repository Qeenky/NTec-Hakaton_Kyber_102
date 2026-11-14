"""API-смоук: диагностика, сценарии, осмотры и сохранность после рестарта."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.seed import seed_all


def test_health_rules_and_empty_diagnostics(memory_db) -> None:
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["database"] == "sqlite"
        assert health["rules_count"] == 13

        rules = client.get("/rules").json()
        assert rules["rules_count"] == 13
        assert rules["activate_confidence"] == 0.65
        assert rules["release_confidence"] == 0.45
        for rule in rules["rules"]:
            assert rule["conditions"]
            assert all(item["text"] for item in rule["conditions"])

        diagnostics = client.get("/equipment/PUMP-001/diagnostics").json()
        assert diagnostics["active"] == []
        assert diagnostics["scenario"] == "normal"
        # Без камеры CV-правила должны быть видны как заблокированные.
        blocked = {item["rule_id"]: item["blocked_by"] for item in diagnostics["blocked"]}
        assert blocked.get("guard_removed") == "camera_unavailable"


def test_guard_removed_critical_and_ack(memory_db) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/sim/scenario",
            json={"equipment_id": "PUMP-001", "scenario": "guard_removed"},
        )
        assert response.status_code == 200

        diagnostics = client.get("/equipment/PUMP-001/diagnostics").json()
        guard = next(
            item for item in diagnostics["active"] if item["rule_id"] == "guard_removed"
        )
        assert guard["severity"] == "critical"
        assert guard["confidence"] >= 0.65
        assert guard["evidence"]
        assert guard["overlay"]["component_id"] == "coupling_guard"

        ack = client.post(f"/diagnostics/{guard['id']}/ack")
        assert ack.status_code == 200
        assert ack.json()["status"] == "acknowledged"

        inspection = client.post(
            "/inspections",
            json={
                "equipment_id": "PUMP-001",
                "frame_id": 1,
                "detections": [
                    {
                        "class_id": 2,
                        "class_name": "coupling_guard",
                        "kind": "component",
                        "confidence": 0.91,
                        "bbox": [0.3, 0.56, 0.62, 0.87],
                    }
                ],
            },
        )
        assert inspection.status_code == 201
        assert inspection.json()["diagnostics"]
        assert inspection.json()["diagnostics"][0]["rule_id"] == "guard_removed"

        history = client.get("/inspections?equipment_id=PUMP-001").json()
        assert history[0]["diagnostics"][0]["rule_id"] == "guard_removed"

        reset = client.post("/sim/reset", json={})
        assert reset.status_code == 200
        after = client.get("/equipment/PUMP-001/diagnostics").json()
        assert after["active"] == []
        assert any(
            item["rule_id"] == "guard_removed" for item in after["history"]
        )


def test_telemetry_history_endpoint(memory_db) -> None:
    with TestClient(app) as client:
        client.post(
            "/sim/scenario",
            json={"equipment_id": "PUMP-001", "scenario": "bearing_wear"},
        )
        for _ in range(3):
            client.post("/sim/tick")

        payload = client.get(
            "/equipment/PUMP-001/telemetry?minutes=30"
        ).json()
        assert payload["equipment_id"] == "PUMP-001"
        assert payload["count"] >= 4
        assert "vibration_rms" in payload["signals"]
        assert payload["latest"]["signals"]["vibration_rms"] > 0
        assert payload["scenario"] == "bearing_wear"


def test_equipment_card_uses_demo_data(memory_db) -> None:
    """Карточка PUMP-001 — демонстрационный агрегат."""
    with TestClient(app) as client:
        card = client.get("/equipment/PUMP-001").json()

    assert card["name"] == "Насосный агрегат"
    assert card["model"] == "DEMO-PUMP"
    assert card["serial"] == "DEMO-001"
    assert card["year"] == 2023
    assert card["runtime_hours"] == 7400.0
    assert card["normative_life_years"] == 20

    baseline = card["baseline"]
    assert baseline["vibration_rms"] == 1.0
    assert baseline["vibration_lf_1x"] == 0.8
    assert baseline["vibration_lf_2x"] == 0.3
    assert baseline["vibration_hf"] == 0.1
    assert baseline["temperature_bearing"] == 50.0
    assert baseline["temperature_motor"] == 65.0
    assert baseline["current_a"] == 23.0
    assert baseline["flow"] == 18.0
    assert baseline["pressure_outlet"] == 10.0
    assert baseline["rpm_pump"] == 260.0
    assert baseline["rpm_motor"] == 1460.0

    components = {item["name"]: item for item in card["components"]}
    assert set(components) == {"motor", "pump", "coupling_guard", "flange", "manometer"}
    for item in components.values():
        assert item["remaining_resource"]["is_estimate"] is True
    assert components["pump"]["remaining_resource"]["status"] == "ok"
    assert components["pump"]["remaining_resource"]["value"] is not None
    # По кожуху и фланцу нормативов в паспортах нет — честный null.
    assert components["flange"]["remaining_resource"]["status"] == "insufficient_data"
    assert components["coupling_guard"]["remaining_resource"]["value"] is None


def test_reseed_is_idempotent_and_keeps_runtime(memory_db) -> None:
    """Повторный сид обновляет карточку, но не сбрасывает накопленную наработку."""
    db.update_equipment_runtime("PUMP-001", 7450.0)
    seed_all()

    equipment = db.get_equipment("PUMP-001")
    assert equipment is not None
    assert equipment["runtime_hours"] == 7450.0
    assert equipment["baseline"]["vibration_rms"] == 1.0
    assert len(db.get_components("PUMP-001")) == 5


def test_diagnostics_survive_engine_restart(tmp_path) -> None:
    """История и активные события переживают пересоздание подключения."""
    url = f"sqlite+pysqlite:///{(tmp_path / 'persist.db').as_posix()}"
    db.configure(url, force=True)
    db.init_db()
    seed_all()

    now = datetime.now(timezone.utc)
    created = db.create_diagnostic(
        equipment_id="PUMP-001",
        rule_id="bearing_wear",
        severity="warning",
        confidence=0.81,
        evidence=[
            {
                "condition": "vibration_hf > 22.7 × baseline.vibration_hf",
                "actual": 4.1,
                "baseline": 0.11,
                "threshold": 2.497,
                "matched": True,
                "weight": 3,
            }
        ],
        message="Проверка",
        recommendation="Проверить смазку",
        overlay={"component_id": "pump"},
        now=now,
    )
    assert created["id"] > 0

    # Имитация рестарта: старый пул закрыт, подключение создано заново.
    db.configure(url, force=True)
    rows = db.get_open_diagnostics("PUMP-001")
    assert len(rows) == 1
    assert rows[0]["rule_id"] == "bearing_wear"
    assert rows[0]["evidence"][0]["actual"] == 4.1

    db.configure("sqlite+pysqlite:///:memory:", force=True)
