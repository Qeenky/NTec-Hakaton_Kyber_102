"""Итог осмотра (`verdict`) и активация visual_defect по окну."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import analytics, cv_state
from app.diagnostics.service import DiagnosticsService
from app.verdict import DISCLAIMER, build_verdict

EQUIPMENT = "PUMP-001"
ROSTER = ("motor", "pump", "coupling_guard", "flange", "manometer")


def _component(
    component_id: str,
    *,
    status: str = "ok",
    frames_seen: int = 10,
    frames_total: int = 10,
    defects: list[dict] | None = None,
) -> dict:
    return {
        "component_id": component_id,
        "label": component_id,
        "frames_total": frames_total,
        "frames_seen": frames_seen,
        "visibility_ratio": round(frames_seen / frames_total, 4),
        "status_window": status,
        "defects": defects or [],
        "note": "",
    }


def _defect(confirmed: bool = False, confidence: float = 0.5) -> dict:
    return {
        "class_name": "corrosion",
        "frames_present": 5,
        "frames_seen_component": 10,
        "presence_ratio": 0.5,
        "mean_confidence": confidence,
        "max_confidence": confidence,
        "confirmed": confirmed,
    }


def test_verdict_defects_found() -> None:
    summary = {
        "frames": 10,
        "components": [
            _component("coupling_guard", status="possible_defect",
                       defects=[_defect(confirmed=True, confidence=0.51)]),
            _component("pump"),
        ],
    }
    verdict = build_verdict(summary, roster=ROSTER)
    assert verdict["status"] == "defects_found"
    assert verdict["components_with_issues"] == ["coupling_guard"]
    assert "подтверждены" in verdict["reason"]
    assert verdict["disclaimer"] == DISCLAIMER


def test_verdict_no_visible_defects() -> None:
    summary = {
        "frames": 10,
        "components": [
            _component(component_id, frames_seen=10) for component_id in ROSTER
        ],
    }
    verdict = build_verdict(summary, roster=ROSTER)
    assert verdict["status"] == "no_visible_defects"
    assert verdict["components_with_issues"] == []
    assert verdict["components_not_seen"] == []
    assert "видимых дефектов не обнаружено" in verdict["reason"]


def test_verdict_insufficient_when_part_not_seen() -> None:
    summary = {
        "frames": 10,
        "components": [
            _component("motor", frames_seen=10),
            _component("pump", frames_seen=10),
            _component("coupling_guard", status="insufficient_data", frames_seen=0),
            _component("flange", status="insufficient_data", frames_seen=0),
            _component("manometer", status="insufficient_data", frames_seen=0),
        ],
    }
    verdict = build_verdict(summary, roster=ROSTER)
    assert verdict["status"] == "insufficient_data"
    assert verdict["components_not_seen"] == [
        "coupling_guard",
        "flange",
        "manometer",
    ]
    assert "Недостаточно данных" in verdict["reason"]


def test_verdict_attention_for_single_observation() -> None:
    summary = {
        "frames": 10,
        "components": [
            _component("motor", status="check_required",
                       defects=[_defect(confirmed=False, confidence=0.47)]),
            _component("pump"),
        ],
    }
    verdict = build_verdict(summary, roster=ROSTER)
    assert verdict["status"] == "attention_required"
    assert verdict["components_with_issues"] == ["motor"]


def test_verdict_never_normal_without_video() -> None:
    verdict = build_verdict({}, roster=ROSTER)
    assert verdict["status"] == "insufficient_data"
    assert verdict["status"] != "no_visible_defects"
    assert verdict["components_not_seen"] == list(ROSTER)
    assert "не проводилось" in verdict["reason"]


def test_window_confirmed_visual_defect_activates_below_frame_threshold(
    memory_db,
) -> None:
    """Окно подтвердило дефект (mean 0.48) — диагностика всё равно активна."""
    analytics.reset()
    cv_state.clear()
    now = datetime.now(timezone.utc)
    store = analytics.get_store()
    for index in range(3):
        store.update(
            EQUIPMENT,
            overlays=[{"component_id": "motor", "visible": True}],
            detections=[
                {
                    "kind": "defect",
                    "class_name": "corrosion",
                    "component_id": "motor",
                    "confidence": 0.48,
                }
            ],
            components_status=[{"component_id": "motor", "bbox": [0, 0, 1, 1]}],
            now=now + timedelta(seconds=index),
        )

    snapshot = DiagnosticsService().refresh(
        EQUIPMENT, now=now + timedelta(seconds=2), points=[]
    )
    visual = [
        item
        for item in snapshot["active"]
        if str(item["rule_id"]).startswith("visual_defect")
    ]
    assert len(visual) == 1, snapshot["active"]
    item = visual[0]
    assert item["severity"] == "warning"
    assert item["confidence"] == 0.48
    assert "Подтверждено по 3 из 3 кадров" in item["message"]
    assert item["evidence"][0]["analytics"]["confirmed"] is True
    analytics.reset()
    cv_state.clear()
