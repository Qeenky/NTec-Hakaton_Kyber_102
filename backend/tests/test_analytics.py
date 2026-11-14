"""Аккумулятор наблюдений: окно, вытеснение, пороги, сброс, слияние."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import analytics, cv_state
from app.analytics import AnalyticsStore
from app.cv_state import CvObservation
from app.diagnostics.service import DiagnosticsService
from app.main import app

EQUIPMENT = "PUMP-001"
START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _frame(
    store: AnalyticsStore,
    moment: datetime,
    *,
    components: dict[str, bool],
    defects: list[tuple[str, str, float]] | None = None,
    equipment_id: str = EQUIPMENT,
) -> dict:
    """Один кадр: видимость узлов и список (узел, класс, confidence)."""
    overlays = [
        {"component_id": component_id, "visible": visible}
        for component_id, visible in components.items()
    ]
    detections = [
        {
            "kind": "defect",
            "class_name": class_name,
            "component_id": component_id,
            "confidence": confidence,
        }
        for component_id, class_name, confidence in (defects or [])
    ]
    statuses = [
        {"component_id": component_id, "bbox": [0.0, 0.0, 1.0, 1.0]}
        for component_id, visible in components.items()
        if visible
    ]
    return store.update(
        equipment_id,
        overlays=overlays,
        detections=detections,
        components_status=statuses,
        now=moment,
    )


def _component(summary: dict, component_id: str) -> dict:
    return next(
        item for item in summary["components"] if item["component_id"] == component_id
    )


def _defect(summary: dict, component_id: str, class_name: str) -> dict:
    return next(
        item
        for item in _component(summary, component_id)["defects"]
        if item["class_name"] == class_name
    )


def test_window_aggregates_visibility_and_defects() -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=3)
    for index in range(4):
        _frame(
            store,
            START + timedelta(seconds=index),
            components={"pump": True, "motor": index % 2 == 0},
            defects=[("pump", "corrosion", 0.60 + 0.01 * index)],
        )

    summary = store.summary(EQUIPMENT, now=START + timedelta(seconds=4))
    assert summary["frames"] == 4
    assert summary["duration_sec"] == 3.0
    assert summary["observation_started_at"] == "2026-01-01T12:00:00+00:00"
    assert summary["updated_at"] == "2026-01-01T12:00:03+00:00"

    pump = _component(summary, "pump")
    motor = _component(summary, "motor")
    assert pump["visibility_ratio"] == 1.0
    assert pump["frames_seen"] == 4
    assert motor["visibility_ratio"] == 0.5

    defect = _defect(summary, "pump", "corrosion")
    assert defect["frames_present"] == 4
    assert defect["frames_seen_component"] == 4
    assert defect["presence_ratio"] == 1.0
    assert defect["mean_confidence"] == pytest.approx(0.615)
    assert defect["max_confidence"] == pytest.approx(0.63)
    assert defect["confirmed"] is True
    assert pump["confirmed_defects"][0]["class_name"] == "corrosion"
    assert summary["confirmed_defects"] == 1


def test_confirmation_thresholds_ratio_and_frames() -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.6, confirm_frames=2)
    for index in range(4):
        confirmed = index < 2
        _frame(
            store,
            START + timedelta(seconds=index),
            components={"pump": True},
            defects=[("pump", "corrosion", 0.7)] if confirmed else [],
        )
    summary = store.summary(EQUIPMENT, now=START + timedelta(seconds=4))
    defect = _defect(summary, "pump", "corrosion")
    # 2 присутствия из 4 кадров = 0.5 < 0.6 — не подтверждён, хотя кадров хватает.
    assert defect["frames_present"] == 2
    assert defect["presence_ratio"] == 0.5
    assert defect["confirmed"] is False
    assert _component(summary, "pump")["unconfirmed_defects"]

    # Порог по кадрам: 3 присутствия при ratio 1.0 — тоже мало (confirm_frames=4).
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=4)
    for index in range(3):
        _frame(
            store,
            START + timedelta(seconds=index),
            components={"pump": True},
            defects=[("pump", "corrosion", 0.7)],
        )
    summary = store.summary(EQUIPMENT, now=START + timedelta(seconds=3))
    defect = _defect(summary, "pump", "corrosion")
    assert defect["presence_ratio"] == 1.0
    assert defect["frames_present"] == 3
    assert defect["confirmed"] is False


def test_window_evicts_old_frames() -> None:
    store = AnalyticsStore(window_sec=10, confirm_ratio=0.3, confirm_frames=2)
    _frame(store, START, components={"pump": True}, defects=[("pump", "corrosion", 0.7)])
    _frame(
        store,
        START + timedelta(seconds=5),
        components={"pump": True},
        defects=[("pump", "corrosion", 0.7)],
    )
    _frame(store, START + timedelta(seconds=12), components={"pump": True})

    summary = store.summary(EQUIPMENT, now=START + timedelta(seconds=12))
    # Кадр на 0-й секунде старше окна (12 > 10) и удалён.
    assert summary["frames"] == 2
    assert summary["duration_sec"] == 7.0
    defect = _defect(summary, "pump", "corrosion")
    assert defect["frames_present"] == 1

    summary = store.summary(EQUIPMENT, now=START + timedelta(seconds=30))
    assert summary["frames"] == 0
    assert summary["components"] == []


def test_reset_single_equipment_and_all() -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=1)
    for equipment_id in ("PUMP-001", "MOTOR-002"):
        _frame(
            store,
            START,
            components={"pump": True},
            defects=[("pump", "corrosion", 0.7)],
            equipment_id=equipment_id,
        )
    store.reset("PUMP-001")
    assert store.summary("PUMP-001", now=START)["frames"] == 0
    assert store.summary("MOTOR-002", now=START)["frames"] == 1
    store.reset()
    assert store.summary("MOTOR-002", now=START)["frames"] == 0


def test_compact_and_api_summary(memory_db) -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=2)
    for index in range(3):
        _frame(
            store,
            START + timedelta(seconds=index),
            components={"pump": True, "motor": True},
            defects=[("pump", "corrosion", 0.7)]
            + ([("motor", "oil_leak", 0.8)] if index == 0 else []),
        )
    compact = store.compact(EQUIPMENT, now=START + timedelta(seconds=3))
    pump = _component(compact, "pump")
    motor = _component(compact, "motor")
    assert compact["frames"] == 3
    assert pump["status_window"] == "possible_defect"
    assert [item["class_name"] for item in pump["defects"] if item["confirmed"]] == [
        "corrosion"
    ]
    assert pump["visibility_ratio"] == 1.0
    assert motor["status_window"] == "check_required"  # единичный кадр
    assert motor["defects"][0]["confirmed"] is False
    assert pump["label"] and pump["frames_total"] == 3

    # API: глобальный аккумулятор приложения.
    analytics.reset()
    api_now = datetime.now(timezone.utc)
    for index in range(3):
        _frame(
            analytics.get_store(),
            api_now + timedelta(seconds=index),
            components={"pump": True},
            defects=[("pump", "corrosion", 0.7)],
        )
    with TestClient(app) as client:
        summary = client.get(f"/equipment/{EQUIPMENT}/analytics")
        assert summary.status_code == 200
        assert summary.json()["frames"] == 3
        assert summary.json()["components"][0]["visibility_ratio"] == 1.0

        reset = client.post(f"/analytics/reset?equipment_id={EQUIPMENT}")
        assert reset.status_code == 200
        assert reset.json()["reset"] is True
        assert reset.json()["analytics"]["frames"] == 0
        assert client.get(f"/equipment/{EQUIPMENT}/analytics").json()["frames"] == 0


def test_inspection_saves_analytics_snapshot(memory_db) -> None:
    analytics.reset()
    api_now = datetime.now(timezone.utc)
    for index in range(3):
        _frame(
            analytics.get_store(),
            api_now + timedelta(seconds=index),
            components={"pump": True},
            defects=[("pump", "corrosion", 0.7)],
        )
    with TestClient(app) as client:
        response = client.post(
            "/inspections",
            json={"equipment_id": EQUIPMENT, "frame_id": 1, "detections": []},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["analytics"]["frames"] == 3
        assert body["analytics"]["components"][0]["visibility_ratio"] == 1.0
        assert body["verdict"]["status"] == "defects_found"
        assert body["verdict"]["components_with_issues"] == ["pump"]

        history = client.get(f"/inspections?equipment_id={EQUIPMENT}").json()
        assert history[0]["analytics"]["frames"] == 3
        assert "observation_started_at" in history[0]["analytics"]
        assert history[0]["verdict"]["status"] == "defects_found"
    analytics.reset()


def test_component_status_window_per_node() -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=3)
    for index in range(4):
        _frame(
            store,
            START + timedelta(seconds=index),
            components={"pump": True, "motor": True, "flange": False},
            defects=(
                [("pump", "corrosion", 0.7)]
                + ([("motor", "oil_leak", 0.8)] if index == 0 else [])
            ),
        )
    summary = store.summary(EQUIPMENT, now=START + timedelta(seconds=4))
    pump = _component(summary, "pump")
    motor = _component(summary, "motor")
    flange = _component(summary, "flange")

    assert pump["status_window"] == "possible_defect"
    assert pump["label"] == "Насос"
    assert pump["frames_seen"] == 4 and pump["frames_total"] == 4
    assert pump["last_seen_ago_sec"] == 1.0
    assert "подтверждено" in pump["note"]

    # Один кадр с дефектом — check_required, не possible_defect.
    assert motor["status_window"] == "check_required"
    assert motor["defects"][0]["confirmed"] is False
    assert "не подтверждено" in motor["note"]

    assert flange["status_window"] == "insufficient_data"
    assert flange["frames_seen"] == 0
    assert "не встречался" in flange["note"]


def test_component_absent_after_five_seconds() -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=1)
    _frame(
        store,
        START,
        components={"pump": True, "motor": True},
        defects=[("pump", "corrosion", 0.7)],
    )
    fresh = store.summary(EQUIPMENT, now=START)
    assert _component(fresh, "pump")["status_window"] == "possible_defect"

    paused = store.summary(EQUIPMENT, now=START + timedelta(seconds=6))
    for component_id in ("pump", "motor"):
        item = _component(paused, component_id)
        assert item["status_window"] == "insufficient_data"
        assert item["last_seen_ago_sec"] == 6.0
        assert "вне кадра 6 с" in item["note"]

    # Возврат узла в кадр снова даёт ok/possible_defect.
    _frame(
        store,
        START + timedelta(seconds=7),
        components={"pump": True, "motor": True},
    )
    back = store.summary(EQUIPMENT, now=START + timedelta(seconds=7))
    # Дефект ещё в окне, поэтому pump снова possible_defect; motor — ok.
    assert _component(back, "pump")["status_window"] == "possible_defect"
    assert _component(back, "motor")["status_window"] == "ok"


def test_never_seen_components_are_independent() -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=2)
    all_ids = ("pump", "motor", "flange", "coupling_guard", "manometer")
    for index in range(3):
        store.update(
            EQUIPMENT,
            overlays=[
                {"component_id": component_id, "visible": component_id in {"pump", "motor"}}
                for component_id in all_ids
            ],
            detections=[],
            components_status=[
                {
                    "component_id": component_id,
                    "bbox": [0, 0, 1, 1] if component_id in {"pump", "motor"} else None,
                }
                for component_id in all_ids
            ],
            now=START + timedelta(seconds=index),
        )
    summary = store.summary(EQUIPMENT, now=START + timedelta(seconds=3))
    assert {item["component_id"] for item in summary["components"]} == set(all_ids)
    assert _component(summary, "pump")["status_window"] == "ok"
    assert _component(summary, "motor")["status_window"] == "ok"
    for component_id in ("flange", "coupling_guard", "manometer"):
        item = _component(summary, component_id)
        assert item["status_window"] == "insufficient_data"
        assert item["frames_seen"] == 0
    assert summary["confirmed_defects"] == 0


def test_two_nodes_two_visual_diagnostics(memory_db) -> None:
    analytics.reset()
    cv_state.clear()
    now = datetime.now(timezone.utc)
    store = analytics.get_store()
    for index in range(3):
        _frame(
            store,
            now + timedelta(seconds=index),
            components={"motor": True, "coupling_guard": True},
            defects=[
                ("motor", "oxidation", 0.6),
                ("coupling_guard", "corrosion", 0.7),
            ],
        )
    cv_state.publish(
        EQUIPMENT,
        [
            CvObservation(
                class_name="oxidation",
                confidence=0.6,
                bbox=[0.1, 0.1, 0.4, 0.4],
                component_id="motor",
                label="oxidation",
            ),
            CvObservation(
                class_name="corrosion",
                confidence=0.7,
                bbox=[0.3, 0.5, 0.6, 0.8],
                component_id="coupling_guard",
                label="corrosion",
            ),
        ],
        now=now,
        source="board",
    )
    service = DiagnosticsService()
    snapshot = service.refresh(EQUIPMENT, now=now + timedelta(seconds=2), points=[])
    visual = [
        item
        for item in snapshot["active"]
        if str(item["rule_id"]).startswith("visual_defect")
    ]
    assert len(visual) == 2, snapshot["active"]
    nodes = {item["overlay"]["component_id"] for item in visual}
    assert nodes == {"motor", "coupling_guard"}
    assert {item["component_id"] for item in visual} == nodes
    assert len({item["rule_id"] for item in visual}) == 2
    for item in visual:
        assert item["severity"] in {"warning", "critical"}
        assert item["evidence"][0]["analytics"]["confirmed"] is True

    # Узел ушёл из текущего кадра, но окно ещё подтверждает дефект —
    # диагностика остаётся (решение по окну, а не по кадру).
    cv_state.publish(
        EQUIPMENT,
        [
            CvObservation(
                class_name="oxidation",
                confidence=0.6,
                bbox=[0.1, 0.1, 0.4, 0.4],
                component_id="motor",
                label="oxidation",
            )
        ],
        now=now + timedelta(seconds=3),
        source="board",
    )
    snapshot = service.refresh(EQUIPMENT, now=now + timedelta(seconds=3), points=[])
    visual = [
        item
        for item in snapshot["active"]
        if str(item["rule_id"]).startswith("visual_defect")
    ]
    assert {item["component_id"] for item in visual} == {"motor", "coupling_guard"}

    # Окно сброшено — узел больше не подтверждён, его событие закрывается.
    analytics.reset()
    snapshot = service.refresh(EQUIPMENT, now=now + timedelta(seconds=3), points=[])
    visual = [
        item
        for item in snapshot["active"]
        if str(item["rule_id"]).startswith("visual_defect")
    ]
    assert len(visual) == 1
    assert visual[0]["overlay"]["component_id"] == "motor"
    analytics.reset()
    cv_state.clear()


def test_single_frame_visual_defect_is_info(memory_db) -> None:
    analytics.reset()
    cv_state.clear()
    service = DiagnosticsService()
    now = datetime.now(timezone.utc)
    _frame(
        analytics.get_store(),
        now,
        components={"motor": True},
        defects=[("motor", "oxidation", 0.6)],
    )
    cv_state.publish(
        EQUIPMENT,
        [
            CvObservation(
                class_name="oxidation",
                confidence=0.6,
                bbox=[0.1, 0.1, 0.4, 0.4],
                component_id="motor",
                label="oxidation",
            )
        ],
        now=now,
        source="board",
    )

    snapshot = service.refresh(EQUIPMENT, now=now, points=[])
    active = {item["rule_id"]: item for item in snapshot["active"]}
    assert "visual_defect" in active
    visual = active["visual_defect"]
    assert visual["severity"] == "info"
    assert "Единичное наблюдение" in visual["message"]
    assert visual["evidence"][0]["analytics"]["confirmed"] is False

    # Ещё два кадра подтверждают дефект — severity возвращается к warning.
    for index in range(1, 3):
        _frame(
            analytics.get_store(),
            now + timedelta(seconds=index),
            components={"motor": True},
            defects=[("motor", "oxidation", 0.6)],
        )
    snapshot = service.refresh(EQUIPMENT, now=now + timedelta(seconds=3), points=[])
    active = {item["rule_id"]: item for item in snapshot["active"]}
    visual = active["visual_defect"]
    assert visual["severity"] == "warning"
    assert "Единичное наблюдение" not in visual["message"]
    assert visual["evidence"][0]["analytics"]["confirmed"] is True

    analytics.reset()
    cv_state.clear()
