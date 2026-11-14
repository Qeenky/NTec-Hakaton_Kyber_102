"""По одному сценарию на каждое правило + негативные проверки."""

from __future__ import annotations

import pytest

from app.diagnostics.catalog import load_catalog
from app.cv_state import CvSnapshot
from app.diagnostics.evaluator import evaluate_all
from app.simulator import DEFAULT_BASELINE

from .helpers import (
    START,
    best_evaluation,
    evaluation_at,
    evaluate_rule_on_points,
    make_points,
    runtime_points,
)

# (сценарий симулятора, id правила)
SCENARIO_CASES = [
    ("bearing_wear", "bearing_wear"),
    ("cavitation", "cavitation"),
    ("misalignment", "misalignment"),
    ("impeller_wear", "impeller_wear"),
    ("seal_leak", "seal_leak"),
    ("looseness", "mechanical_looseness"),
    ("lubrication_degradation", "lubrication_degradation"),
    ("electrical_contact", "electrical_contact"),
    ("gauge_drift", "gauge_drift"),
    ("guard_removed", "guard_removed"),
    ("cavitation", "cavitation_confirmed"),
]

EVIDENCE_KEYS = {"condition", "actual", "baseline", "threshold", "matched", "weight"}


@pytest.mark.parametrize("scenario,rule_id", SCENARIO_CASES)
def test_scenario_triggers_expected_rule(scenario: str, rule_id: str) -> None:
    """Сигналы сценария дают ожидаемое правило с заполненным evidence."""
    evaluation = best_evaluation(scenario, rule_id)

    assert evaluation.blocked_by is None, evaluation.blocked_reason
    assert evaluation.confidence >= 0.65, (evaluation.confidence, evaluation.evidence)
    assert evaluation.evidence, "evidence не должен быть пустым"
    assert any(item["matched"] is True for item in evaluation.evidence)
    for item in evaluation.evidence:
        assert EVIDENCE_KEYS.issubset(item), item


def test_bearing_wear_ramps_over_30_50_seconds() -> None:
    """Сценарий не «прыгает»: в начале тихо, к 45-й секунде правило активно."""
    early = evaluation_at("bearing_wear", "bearing_wear", second=5)
    assert early.confidence < 0.65

    middle = evaluation_at("bearing_wear", "bearing_wear", second=45)
    assert middle.confidence >= 0.65, middle.evidence
    # Ведущие условия — пороги правила: HF > 2.27 g и T > 75 °C.
    by_signal = {item["signal"]: item for item in middle.evidence}
    assert by_signal["vibration_hf"]["threshold"] == pytest.approx(2.27, abs=0.01)
    assert by_signal["vibration_hf"]["actual"] > 2.27
    assert by_signal["temperature_bearing"]["threshold"] == pytest.approx(75.0, abs=0.01)
    assert by_signal["temperature_bearing"]["actual"] > 75.0


def test_cv_snapshot_expires_by_ttl() -> None:
    """Свежесть CV: старше 5 секунд — канал считается недоступным."""
    from datetime import timedelta

    from app import cv_state
    from app.cv_state import CvObservation

    cv_state.publish(
        "TTL-TEST",
        [CvObservation(class_name="guard_damage", confidence=0.9)],
        now=START,
    )
    fresh = cv_state.snapshot(
        "TTL-TEST", now=START + timedelta(seconds=4), ttl_sec=5
    )
    assert fresh.available is True

    stale = cv_state.snapshot(
        "TTL-TEST", now=START + timedelta(seconds=6), ttl_sec=5
    )
    assert stale.available is False
    cv_state.clear("TTL-TEST")


def test_normal_scenario_has_no_alarms() -> None:
    """При норме ни одно правило не достигает порога активации."""
    catalog = load_catalog()
    points = make_points("normal", seconds=60)

    for camera in (True, False):
        cv = CvSnapshot(
            available=camera,
            observed_at=START if camera else None,
            observations=(),
            source="test",
        )
        evaluations = evaluate_all(catalog.rules, points, DEFAULT_BASELINE, cv)
        triggered = [
            evaluation.rule.id
            for evaluation in evaluations
            if evaluation.confidence >= 0.65
        ]
        assert triggered == [], f"камера={camera}: ложные срабатывания {triggered}"


def test_cv_rule_is_blocked_without_camera() -> None:
    """Без свежих кадров CV-правило не срабатывает и сообщает причину."""
    guarded = best_evaluation("guard_removed", "guard_removed", cv_available=False)
    assert guarded.confidence == 0.0
    assert guarded.blocked_by == "camera_unavailable"
    assert guarded.evidence[0]["matched"] is False

    leak = best_evaluation("seal_leak", "seal_leak", cv_available=False)
    assert leak.confidence == 0.0
    assert leak.blocked_by == "camera_unavailable"


def test_gauge_drift_requires_ocr_value() -> None:
    """Без OCR-значения манометра правило заблокировано, с ним — срабатывает."""
    without_ocr = evaluation_at("gauge_drift", "gauge_drift", second=0)
    assert without_ocr.confidence == 0.0
    assert without_ocr.blocked_by == "ocr_unavailable"

    with_ocr = best_evaluation("gauge_drift", "gauge_drift")
    assert with_ocr.blocked_by is None
    assert with_ocr.confidence >= 0.65
    assert "manometer_ocr" in with_ocr.evidence[0]["note"]


def test_maintenance_due_crosses_4000() -> None:
    """Пересечение 4000 м/ч поднимает maintenance_due, повтор — нет."""
    catalog = load_catalog()
    rule = catalog.get("maintenance_due")
    assert rule is not None

    crossing = evaluate_rule_on_points(rule, runtime_points(3990, 4010))
    assert crossing.confidence >= 0.65
    assert crossing.blocked_by is None
    assert crossing.evidence[0]["matched"] is True
    assert crossing.evidence[0]["threshold"] == 4000

    already_above = evaluate_rule_on_points(rule, runtime_points(4010, 4030))
    assert already_above.confidence == 0.0

    crossing_8000 = evaluate_rule_on_points(rule, runtime_points(7990, 8010))
    assert crossing_8000.confidence >= 0.65
    assert crossing_8000.evidence[0]["threshold"] == 8000


def test_cavitation_confirmed_is_more_specific() -> None:
    """На позднем этапе сценария остаётся только подтверждённая кавитация."""
    from app.diagnostics.evaluator import dedupe

    simple = best_evaluation("cavitation", "cavitation")
    confirmed = best_evaluation("cavitation", "cavitation_confirmed")
    assert confirmed.rule.specificity > simple.rule.specificity

    catalog = load_catalog()
    points = make_points("cavitation", seconds=35)
    moment = points[-1][0]
    from .helpers import make_cv

    cv = make_cv("cavitation", 1.0, moment, available=True)
    evaluations = evaluate_all(catalog.rules, points, DEFAULT_BASELINE, cv, now=moment)
    winners = {item.rule.id: item for item in dedupe(evaluations, 0.45)}
    assert "cavitation_confirmed" in winners
    if "cavitation" in winners:
        assert winners["cavitation"].suppressed_by == "cavitation_confirmed"
