"""Гистерезис, тренды и формула confidence."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app import db
from app.cv_state import CvSnapshot
from app.diagnostics.catalog import Condition, Rule, load_catalog
from app.diagnostics.evaluator import (
    RuleEvaluation,
    combine_confidence,
    evaluate_rule,
    linear_slope,
)
from app.diagnostics.service import DiagnosticsService

from .helpers import START


def _evaluation(confidence: float, rule: Rule) -> RuleEvaluation:
    return RuleEvaluation(
        rule=rule,
        confidence=confidence,
        matched=confidence >= 0.65,
        evidence=[
            {
                "condition": "vibration_hf > 22.7 × baseline.vibration_hf",
                "actual": 4.4,
                "baseline": 0.11,
                "threshold": 2.497,
                "matched": True,
                "weight": 3,
            }
        ],
    )


def test_hysteresis_activate_keep_resolve(memory_db) -> None:
    """0.65 активирует, 0.50 удерживает, 0.44 снимает."""
    service = DiagnosticsService()
    rule = service.catalog.get("bearing_wear")
    assert rule is not None
    equipment_id = "PUMP-001"

    assert service.apply_evaluation(equipment_id, _evaluation(0.64, rule)) == "none"
    assert db.get_open_diagnostics(equipment_id) == []

    assert service.apply_evaluation(equipment_id, _evaluation(0.65, rule)) == "activate"
    active = db.get_open_diagnostics(equipment_id)
    assert len(active) == 1
    assert active[0]["status"] == "active"

    assert service.apply_evaluation(equipment_id, _evaluation(0.50, rule)) == "keep"
    assert len(db.get_open_diagnostics(equipment_id)) == 1

    assert service.apply_evaluation(equipment_id, _evaluation(0.44, rule)) == "resolve"
    assert db.get_open_diagnostics(equipment_id) == []
    history = db.get_resolved_diagnostics(equipment_id)
    assert len(history) == 1
    assert history[0]["resolved_at"] is not None


def test_blocked_rule_keeps_existing_warning(memory_db) -> None:
    """Потеря камеры не снимает уже выданное предупреждение (fail-safe)."""
    service = DiagnosticsService()
    rule = service.catalog.get("guard_removed")
    assert rule is not None
    assert service.apply_evaluation("PUMP-001", _evaluation(0.9, rule)) == "activate"

    blocked = _evaluation(0.0, rule)
    blocked.blocked_by = "camera_unavailable"
    assert service.apply_evaluation("PUMP-001", blocked) == "keep"
    assert len(db.get_open_diagnostics("PUMP-001")) == 1


def test_linear_slope_is_normalized_per_second() -> None:
    rising = [(START + timedelta(seconds=i), float(i)) for i in range(11)]
    falling = [(START + timedelta(seconds=i), float(10 - i)) for i in range(11)]
    flat = [(START + timedelta(seconds=i), 5.0) for i in range(11)]

    assert linear_slope(rising) == pytest.approx(1.0)
    assert linear_slope(falling) == pytest.approx(-1.0)
    assert linear_slope(flat) == pytest.approx(0.0)


def _trend_rule() -> Rule:
    return Rule(
        id="trend_probe",
        title="Проверка тренда",
        severity="info",
        description="Тестовое правило с одним трендом.",
        message="Рост сигнала.",
        recommendation="Проверить сигнал.",
        conditions=[
            Condition(
                signal="x",
                op="trend_up",
                value=0.5,
                window_sec=10,
            )
        ],
    )


def test_trend_up_matches_rising_signal() -> None:
    rule = _trend_rule()
    points = [(START + timedelta(seconds=i), {"x": float(i)}) for i in range(11)]
    evaluation = evaluate_rule(
        rule,
        points,
        baseline={},
        cv=CvSnapshot(available=False, observed_at=None),
        now=points[-1][0],
    )
    assert evaluation.confidence >= 0.65
    assert evaluation.evidence[0]["op"] == "trend_up"
    assert evaluation.evidence[0]["actual"] == pytest.approx(1.0)

    flat_points = [(START + timedelta(seconds=i), {"x": 5.0}) for i in range(11)]
    flat = evaluate_rule(
        rule,
        flat_points,
        baseline={},
        cv=CvSnapshot(available=False, observed_at=None),
        now=flat_points[-1][0],
    )
    assert flat.confidence == 0.0
    assert flat.evidence[0]["matched"] is False


def test_confidence_formula() -> None:
    """Взвешенная доля × запас по порогу, с ограничением 0..1."""
    assert combine_confidence(5, 5, [1, 1, 1, 1, 1]) == 1.0
    assert combine_confidence(5, 5, [0, 0, 0, 0, 0]) == 0.5
    assert combine_confidence(2, 4, [1, 1]) == 0.5
    assert combine_confidence(0, 4, []) == 0.0
    assert combine_confidence(10, 10, [1, 1]) == 1.0
    assert 0.0 <= combine_confidence(3, 4, [0.2, 0.4, 0.6]) <= 1.0


def test_evidence_contains_threshold_and_baseline() -> None:
    """Evidence — не просто флаг, а числа для объяснения на защите."""
    catalog = load_catalog()
    rule = catalog.get("bearing_wear")
    assert rule is not None
    evaluation = _evaluation(0.8, rule)
    item = evaluation.evidence[0]
    assert item["baseline"] == 0.11
    assert item["threshold"] == 2.497
    assert item["actual"] == 4.4
    assert item["condition"].startswith("vibration_hf")
