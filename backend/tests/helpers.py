"""Генерация окон телеметрии и CV-снимков для тестов движка."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from app.cv_state import CvObservation, CvSnapshot
from app.diagnostics.catalog import Rule, load_catalog
from app.diagnostics.evaluator import RuleEvaluation, evaluate_rule
from app.simulator import (
    DEFAULT_BASELINE,
    build_signals,
    scenario_cues,
    scenario_progress,
)

START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_cv(
    scenario: str,
    progress: float,
    now: datetime,
    available: bool = True,
) -> CvSnapshot:
    """CV-снимок из синтетических подсказок сценария."""
    cues = scenario_cues(scenario, progress)
    observations = tuple(
        CvObservation(
            class_name=cue.class_name,
            confidence=cue.confidence,
            bbox=cue.bbox,
            component_id=cue.component_id,
            label=cue.label,
        )
        for cue in cues
    )
    if not available:
        return CvSnapshot(
            available=False,
            observed_at=None,
            observations=observations,
            source="test-disabled",
        )
    return CvSnapshot(
        available=True,
        observed_at=now,
        observations=observations,
        source="test",
    )


def make_points(
    scenario: str,
    seconds: int = 90,
    runtime_hours: float = 7400.0,
    seed: int = 7,
    baseline: dict[str, Any] | None = None,
) -> list[tuple[datetime, dict[str, Any]]]:
    """Окно 1 Гц с шумом и плавным разгоном сценария."""
    generator = random.Random(seed)
    points: list[tuple[datetime, dict[str, Any]]] = []
    for second in range(seconds + 1):
        moment = START + timedelta(seconds=second)
        signals = build_signals(
            scenario,
            baseline or DEFAULT_BASELINE,
            elapsed_sec=float(second),
            runtime_hours=runtime_hours,
            rng=generator,
            noise=True,
        )
        points.append((moment, signals))
    return points


def best_evaluation(
    scenario: str,
    rule_id: str,
    seconds: int = 90,
    cv_available: bool = True,
    seed: int = 7,
) -> RuleEvaluation:
    """Пик confidence правила на всём разгоне сценария."""
    catalog = load_catalog()
    rule = catalog.get(rule_id)
    assert rule is not None, f"нет правила {rule_id}"

    generator = random.Random(seed)
    points: list[tuple[datetime, dict[str, Any]]] = []
    best: RuleEvaluation | None = None
    for second in range(seconds + 1):
        moment = START + timedelta(seconds=second)
        signals = build_signals(
            scenario,
            DEFAULT_BASELINE,
            elapsed_sec=float(second),
            runtime_hours=7400.0,
            rng=generator,
            noise=True,
        )
        points.append((moment, signals))
        cv = make_cv(
            scenario,
            scenario_progress(scenario, second),
            moment,
            available=cv_available,
        )
        evaluation = evaluate_rule(
            rule,
            points,
            DEFAULT_BASELINE,
            cv,
            now=moment,
        )
        if best is None or evaluation.confidence > best.confidence:
            best = evaluation
    assert best is not None
    return best


def evaluation_at(
    scenario: str,
    rule_id: str,
    second: int,
) -> RuleEvaluation:
    """Оценка правила в конкретную секунду сценария."""
    catalog = load_catalog()
    rule = catalog.get(rule_id)
    assert rule is not None
    points = make_points(scenario, seconds=second)
    moment = points[-1][0]
    cv = make_cv(
        scenario,
        scenario_progress(scenario, second),
        moment,
        available=True,
    )
    return evaluate_rule(rule, points, DEFAULT_BASELINE, cv, now=moment)


def runtime_points(start: float, end: float, step: float = 1.0):
    """Окно с линейно растущей наработкой."""
    points: list[tuple[datetime, dict[str, Any]]] = []
    value = float(start)
    index = 0
    while value <= end + 1e-9:
        signals = dict(DEFAULT_BASELINE)
        signals["runtime_hours"] = round(value, 3)
        points.append((START + timedelta(seconds=index), signals))
        value += step
        index += 1
    return points


def evaluate_rule_on_points(
    rule: Rule,
    points: Sequence[tuple[datetime, dict[str, Any]]],
) -> RuleEvaluation:
    return evaluate_rule(
        rule,
        points,
        DEFAULT_BASELINE,
        CvSnapshot(available=False, observed_at=None),
        now=points[-1][0],
    )
