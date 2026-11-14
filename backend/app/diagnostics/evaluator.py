"""Движок вычисления правил: условия → evidence → confidence.

Формула confidence (документируется в README и на слайдах):

    confidence = clamp(
        (Σ w_i · m_i / Σ w_i) × (0.5 + 0.5 · mean(e_i)),
        0, 1
    )

* `w_i` — вес условия из YAML; `m_i = 1`, если условие совпало;
* `e_i ∈ [0, 1]` — относительный запас по порогу:
  `min(1, |actual − threshold| / (0.2 · |threshold|))`;
  для CV-условия `e = confidence` детекции, для тренда — превышение
  минимального наклона, для дискретных событий (`crosses`, `in`) `e = 1`;
* множитель `0.5 + 0.5·mean(e)` не даёт правилу сработать «на самом пороге»:
  при точном равенстве confidence примерно вдвое ниже, чем при явном выходе
  за порог. Это и обеспечивает плавное срабатывание на разгоне сценария.

Для правила `visual_defect` формула отдельная (визуальные эвристики слабее
датчиков, поэтому у правила свой `activate_confidence`):

    confidence = clamp(mean(score_i) × (1 + 0.10 · (k − 1)), 0, 1)

где `score_i` — score детектора `cv_classic.defects`, `k` — число различных
классов дефекта на одном узле. Второй класс добавляет 10 % к уверенности.
Severity: один класс — `warning`, два и более — `critical`.

Тренд считается линейной регрессией по окну; наклон нормализован на секунду:
`slope = Σ((t−t̄)(x−x̄)) / Σ(t−t̄)²`, требуется ≥5 точек и ≥60% окна.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from ..cv_state import CvObservation, CvSnapshot
from .catalog import (
    SEVERITY_RANK,
    Condition,
    Rule,
    condition_text,
)

TelemetryPoint = tuple[datetime, dict[str, Any]]

_GROUPS: dict[str, tuple[str, ...]] = {
    "current": ("current_a", "current_b", "current_c"),
    "vibration": (
        "vibration_rms",
        "vibration_axial",
        "vibration_hf",
        "vibration_lf_1x",
    ),
}

# Визуальные классы, которые считаются дефектами для правила visual_defect.
VISUAL_DEFECT_CLASSES: tuple[str, ...] = (
    "oil_leak",
    "corrosion",
    "oxidation",
    "crack",
    "guard_damage",
    "texture_anomaly",
)

VISUAL_CLASS_TITLES_RU: dict[str, str] = {
    "oil_leak": "масляный подтёк",
    "corrosion": "коррозия",
    "oxidation": "окисление контактов",
    "crack": "трещина",
    "guard_damage": "повреждение кожуха",
    "texture_anomaly": "аномалия текстуры",
}

COMPONENT_TITLES_RU: dict[str, str] = {
    "motor": "электродвигатель",
    "pump": "насос",
    "coupling_guard": "кожух муфты",
    "flange": "фланцевое соединение",
    "manometer": "манометр",
}

_BLOCK_PRIORITY: dict[str, int] = {
    "insufficient_window": 1,
    "signal_missing": 2,
    "ocr_unavailable": 3,
    "camera_unavailable": 4,
}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@dataclass
class ConditionResult:
    """Результат одного условия + готовый элемент evidence."""

    condition: str
    signal: str | None
    op: str
    actual: Any
    baseline: Any
    threshold: Any
    matched: bool | None
    weight: float
    score: float = 0.0
    note: str | None = None
    applicable: bool = True
    cv_class: str | None = None

    def to_evidence(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "signal": self.signal,
            "op": self.op,
            "actual": _round(self.actual),
            "baseline": _round(self.baseline),
            "threshold": _round(self.threshold),
            "matched": self.matched,
            "weight": self.weight,
            "score": round(self.score, 4),
            **({"note": self.note} if self.note else {}),
        }


@dataclass
class RuleEvaluation:
    """Полная оценка правила.

    `severity`, `message`, `recommendation`, `overlay` — динамические
    переопределения (например, visual_defect меняет цвет и текст по классам
    дефектов). Если поле не задано, берётся значение правила из YAML.
    """

    rule: Rule
    confidence: float
    matched: bool
    evidence: list[dict[str, Any]]
    blocked_by: str | None = None
    blocked_reason: str | None = None
    conditions_matched: int = 0
    conditions_total: int = 0
    matched_weight: float = 0.0
    total_weight: float = 0.0
    suppressed_by: str | None = None
    severity: str | None = None
    message: str | None = None
    recommendation: str | None = None
    overlay: dict[str, Any] | None = None
    # Дефект подтверждён окном видеоаналитики: активируем правило, даже если
    # confidence отдельного кадра/mean ниже порога активации.
    window_confirmed: bool = False

    @property
    def effective_severity(self) -> str:
        return self.severity or self.rule.severity

    @property
    def effective_message(self) -> str:
        return self.message or self.rule.message

    @property
    def effective_recommendation(self) -> str:
        return self.recommendation or self.rule.recommendation

    @property
    def effective_overlay(self) -> dict[str, Any] | None:
        if self.overlay is not None:
            return self.overlay
        return self.rule.overlay.model_dump() if self.rule.overlay else None

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK.get(self.effective_severity, 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule.id,
            "title": self.rule.title,
            "severity": self.effective_severity,
            "confidence": self.confidence,
            "matched": self.matched,
            "evidence": self.evidence,
            "message": self.effective_message,
            "recommendation": self.effective_recommendation,
            "overlay": self.effective_overlay,
            "blocked_by": self.blocked_by,
            "blocked_reason": self.blocked_reason,
            "conditions_matched": self.conditions_matched,
            "conditions_total": self.conditions_total,
            "suppressed_by": self.suppressed_by,
            "window_confirmed": self.window_confirmed,
        }


@dataclass
class _Context:
    points: list[TelemetryPoint]
    latest: dict[str, Any]
    latest_ts: datetime
    baseline: dict[str, Any]
    cv: CvSnapshot


def linear_slope(values: Sequence[tuple[datetime, float]]) -> float:
    """МНК-наклон в единицах сигнала за секунду."""
    if len(values) < 2:
        return 0.0
    origin = values[0][0]
    xs = [(moment - origin).total_seconds() for moment, _ in values]
    ys = [value for _, value in values]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator


def _window(points: list[TelemetryPoint], window_sec: float) -> list[TelemetryPoint]:
    if not points:
        return []
    if window_sec <= 0:
        return points[-1:]
    cutoff = points[-1][0] - timedelta(seconds=window_sec)
    return [point for point in points if point[0] >= cutoff]


def _resolve_ref(ref: str, baseline: dict[str, Any], latest: dict[str, Any]) -> Any:
    """`baseline.x` → норма из карточки, иначе — текущее значение сигнала."""
    if ref.startswith("baseline."):
        return baseline.get(ref[len("baseline.") :])
    if ref == "baseline":
        return None
    return latest.get(ref)


def _condition_base(condition: Condition) -> dict[str, Any]:
    return {
        "condition": condition_text(condition),
        "signal": condition.signal,
        "op": condition.op,
    }


def _missing(condition: Condition, signal: str | None, note: str) -> ConditionResult:
    return ConditionResult(
        **_condition_base(condition),
        actual=None,
        baseline=None,
        threshold=None,
        matched=None,
        weight=condition.weight,
        score=0.0,
        note=note,
        applicable=not condition.optional,
        cv_class=condition.cv_class,
    )


def _eval_comparison(condition: Condition, context: _Context) -> ConditionResult:
    assert condition.signal is not None
    actual = _number(context.latest.get(condition.signal))
    if actual is None:
        return _missing(condition, condition.signal, "signal_missing")

    baseline_value: Any = None
    threshold: Any = None
    if condition.ref:
        reference = _resolve_ref(condition.ref, context.baseline, context.latest)
        reference_number = _number(reference)
        if reference_number is None:
            return _missing(condition, condition.signal, "ref_missing")
        threshold = reference_number * condition.factor
        if condition.ref.startswith("baseline."):
            baseline_value = reference_number
    else:
        threshold = float(condition.value)  # type: ignore[arg-type]

    if threshold is None:
        return _missing(condition, condition.signal, "threshold_missing")

    if condition.op == "gt":
        matched = actual > threshold
    elif condition.op == "lt":
        matched = actual < threshold
    elif condition.op == "gte":
        matched = actual >= threshold
    else:
        matched = actual <= threshold

    distance = abs(actual - float(threshold)) / max(abs(float(threshold)), 1e-9)
    score = clamp(distance / 0.2) if matched else 0.0
    return ConditionResult(
        **_condition_base(condition),
        actual=actual,
        baseline=baseline_value,
        threshold=threshold,
        matched=matched,
        weight=condition.weight,
        score=score,
        cv_class=condition.cv_class,
    )


def _eval_trend(condition: Condition, context: _Context) -> ConditionResult:
    assert condition.signal is not None
    window = _window(context.points, condition.window_sec)
    if len(window) < 5:
        return _missing(condition, condition.signal, "insufficient_window")
    span = (window[-1][0] - window[0][0]).total_seconds()
    if span < 0.6 * condition.window_sec:
        return _missing(condition, condition.signal, "insufficient_window")

    samples: list[tuple[datetime, float]] = []
    for moment, signals in window:
        value = _number(signals.get(condition.signal))
        if value is not None:
            samples.append((moment, value))
    if len(samples) < 5:
        return _missing(condition, condition.signal, "insufficient_window")

    slope = linear_slope(samples)
    min_slope = abs(float(condition.value or 0.0))
    if condition.op == "trend_up":
        matched = slope >= min_slope and slope > 0
        threshold = min_slope
    else:
        matched = slope <= -min_slope and slope < 0
        threshold = -min_slope

    if min_slope <= 0:
        score = 1.0 if matched else 0.0
    else:
        score = clamp((abs(slope) / min_slope - 1.0) / 2.0) if matched else 0.0

    return ConditionResult(
        **_condition_base(condition),
        actual=slope,
        baseline=None,
        threshold=threshold,
        matched=matched,
        weight=condition.weight,
        score=score,
        cv_class=condition.cv_class,
    )


def _eval_crosses(condition: Condition, context: _Context) -> ConditionResult:
    assert condition.signal is not None
    window = _window(context.points, condition.window_sec)
    values = [
        value
        for _, signals in window
        if (value := _number(signals.get(condition.signal))) is not None
    ]
    if len(values) < 2:
        return _missing(condition, condition.signal, "insufficient_window")

    thresholds = condition.values or [float(condition.value)]  # type: ignore[arg-type]
    latest = values[-1]
    crossed: float | None = None
    for threshold in thresholds:
        if any(value < threshold for value in values[:-1]) and latest >= threshold:
            crossed = threshold
            break

    return ConditionResult(
        **_condition_base(condition),
        actual=latest,
        baseline=None,
        threshold=crossed if crossed is not None else min(thresholds),
        matched=crossed is not None,
        weight=condition.weight,
        score=1.0 if crossed is not None else 0.0,
        note=None if crossed is not None else "no_crossing",
    )


def _group_values(signal: str, latest: dict[str, Any]) -> list[float]:
    names = _GROUPS.get(signal, (signal,))
    values = [_number(latest.get(name)) for name in names]
    return [value for value in values if value is not None]


def _eval_unbalance(condition: Condition, context: _Context) -> ConditionResult:
    assert condition.signal is not None
    values = _group_values(condition.signal, context.latest)
    if len(values) < 2:
        return _missing(condition, condition.signal, "signal_missing")
    mean = sum(values) / len(values)
    if abs(mean) < 1e-9:
        return _missing(condition, condition.signal, "zero_mean")
    unbalance = (max(values) - min(values)) / abs(mean) * 100.0
    threshold = float(condition.value)  # type: ignore[arg-type]
    matched = unbalance > threshold
    score = clamp(((unbalance - threshold) / threshold) / 0.2) if matched else 0.0
    return ConditionResult(
        **_condition_base(condition),
        actual=unbalance,
        baseline=mean,
        threshold=threshold,
        matched=matched,
        weight=condition.weight,
        score=score,
        cv_class=condition.cv_class,
    )


def _eval_deviation(condition: Condition, context: _Context) -> ConditionResult:
    assert condition.signal is not None and condition.ref is not None
    actual_value = _number(context.latest.get(condition.signal))
    if actual_value is None:
        return _missing(condition, condition.signal, "signal_missing")
    reference = _number(_resolve_ref(condition.ref, context.baseline, context.latest))
    if reference is None or abs(reference) < 1e-9:
        return _missing(condition, condition.signal, "ref_missing")

    deviation = abs(actual_value - reference) / abs(reference) * 100.0
    threshold = float(condition.value)  # type: ignore[arg-type]
    matched = deviation > threshold
    score = clamp(((deviation - threshold) / threshold) / 0.2) if matched else 0.0
    return ConditionResult(
        **_condition_base(condition),
        actual=deviation,
        baseline=reference,
        threshold=threshold,
        matched=matched,
        weight=condition.weight,
        score=score,
        note=f"{condition.signal}={actual_value:g}, {condition.ref}={reference:g}",
        cv_class=condition.cv_class,
    )


def _eval_in(condition: Condition, context: _Context) -> ConditionResult:
    assert condition.signal is not None
    actual = context.latest.get(condition.signal)
    if actual is None:
        return _missing(condition, condition.signal, "signal_missing")
    allowed = condition.value if isinstance(condition.value, list) else [condition.value]
    matched = actual in allowed
    return ConditionResult(
        **_condition_base(condition),
        actual=actual,
        baseline=None,
        threshold=allowed,
        matched=matched,
        weight=condition.weight,
        score=1.0 if matched else 0.0,
        cv_class=condition.cv_class,
    )


def _eval_has_signal(condition: Condition, context: _Context) -> ConditionResult:
    assert condition.signal is not None
    actual = context.latest.get(condition.signal)
    matched = actual is not None
    return ConditionResult(
        **_condition_base(condition),
        actual=actual,
        baseline=None,
        threshold=None,
        matched=matched,
        weight=condition.weight,
        score=1.0 if matched else 0.0,
        cv_class=condition.cv_class,
    )


def _eval_cv(condition: Condition, context: _Context) -> ConditionResult:
    assert condition.cv_class is not None
    if not context.cv.available:
        return ConditionResult(
            **_condition_base(condition),
            actual=None,
            baseline=None,
            threshold=condition.min_confidence,
            matched=False,
            weight=condition.weight,
            score=0.0,
            note="camera_unavailable",
            cv_class=condition.cv_class,
        )

    observation: CvObservation | None = context.cv.best(
        condition.cv_class,
        min_confidence=condition.min_confidence,
        component_id=condition.component_id,
    )
    matched = observation is not None
    return ConditionResult(
        **_condition_base(condition),
        actual=observation.confidence if observation else None,
        baseline=None,
        threshold=condition.min_confidence,
        matched=matched,
        weight=condition.weight,
        score=observation.confidence if observation else 0.0,
        note=(
            f"bbox={observation.bbox}, component={observation.component_id}"
            if observation
            else "no_fresh_detection"
        ),
        cv_class=condition.cv_class,
    )


def _visual_thresholds(rule: Rule) -> dict[str, float]:
    """Порог min_confidence по каждому визуальному классу из условий YAML."""
    thresholds: dict[str, float] = {}

    def walk(conditions: Sequence[Condition]) -> None:
        for condition in conditions:
            if condition.op == "cv" and condition.cv_class:
                thresholds.setdefault(condition.cv_class, condition.min_confidence)
            if condition.alternatives:
                walk(condition.alternatives)

    walk(rule.conditions)
    return thresholds


def _visual_confidence(scores: Sequence[float], classes: int) -> float:
    """Взвешенная уверенность визуального дефекта.

    `confidence = clamp(mean(score_i) × (1 + 0.10 × (k − 1)))`, где `k` —
    число различных классов на одном узле. Каждый детектор даёт равный вес;
    второй и последующие классы добавляют по 10 % к уверенности. Активация
    правила — по собственному порогу `activate_confidence` (0.5), потому что
    калиброванные score эвристик ниже, чем у датчиков.
    """
    if not scores:
        return 0.0
    mean_score = sum(scores) / len(scores)
    return round(clamp(mean_score * (1.0 + 0.10 * max(0, classes - 1))), 4)


def _visual_evidence(
    observation: CvObservation,
    threshold: float,
) -> dict[str, Any]:
    """Один элемент evidence: класс, score, порог, площадь и признаки."""
    meta = observation.meta if isinstance(observation.meta, dict) else {}
    raw = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
    original_threshold = raw.get("threshold")
    features = raw.get("features")
    if not isinstance(features, dict):
        features = {}
    component_id = observation.component_id or "unknown"
    class_ru = VISUAL_CLASS_TITLES_RU.get(observation.class_name, observation.class_name)
    component_ru = raw.get("component_label") or COMPONENT_TITLES_RU.get(
        component_id, component_id
    )
    return {
        "condition": (
            f"CV: {observation.class_name} (conf ≥ {threshold:.2f}, "
            f"узел {component_id})"
        ),
        "signal": observation.class_name,
        "op": "cv",
        "actual": round(float(observation.confidence), 4),
        "baseline": None,
        "threshold": (
            original_threshold if original_threshold is not None else round(threshold, 4)
        ),
        "matched": True,
        "weight": 1.0,
        "score": round(float(observation.confidence), 4),
        "class": observation.class_name,
        "class_ru": class_ru,
        "component": component_id,
        "component_ru": component_ru,
        "area_px": raw.get("area_px"),
        "score": raw.get("score", round(float(observation.confidence), 4)),
        "area": raw.get("area"),
        "area_pct": raw.get("area_pct"),
        "detector": raw.get("detector"),
        "features": features,
    }


def _visual_nodes(
    cv: CvSnapshot,
    thresholds: dict[str, float],
) -> dict[str, list[CvObservation]]:
    """Свежие визуальные наблюдения, сгруппированные по узлу."""
    if not cv.available:
        return {}
    by_component: dict[str, list[CvObservation]] = {}
    for observation in cv.observations:
        if observation.class_name not in thresholds:
            continue
        if not observation.component_id:
            continue
        if observation.confidence + 1e-9 < thresholds[observation.class_name]:
            continue
        by_component.setdefault(str(observation.component_id), []).append(observation)
    return by_component


def _visual_node_evaluation(
    rule: Rule,
    component_id: str,
    items: Sequence[CvObservation],
    thresholds: dict[str, float],
    *,
    primary: bool,
) -> RuleEvaluation:
    """Оценка `visual_defect` для одного узла.

    Главный (самый уверенный) узел сохраняет id правила `visual_defect` —
    обратная совместимость. Для остальных узлов id получает суффикс
    `_<component_id>`, чтобы в БД жили отдельные открытые события с разными
    `component_id`, а не одно общее.
    """
    items = sorted(items, key=lambda item: (-item.confidence, item.class_name))
    classes = sorted({item.class_name for item in items})
    confidence = _visual_confidence([item.confidence for item in items], len(classes))
    severity = "critical" if len(classes) >= 2 else "warning"
    defects_text = ", ".join(
        VISUAL_CLASS_TITLES_RU.get(name, name) for name in classes
    )
    evidence = [
        _visual_evidence(item, thresholds[item.class_name]) for item in items
    ]
    component_ru = evidence[0]["component_ru"] if evidence else component_id
    try:
        message = rule.message.format(
            defects=defects_text, component=component_ru
        )
    except (KeyError, IndexError, ValueError):
        message = (
            f"Визуальные признаки дефекта: {defects_text} на узле {component_ru}."
        )
    recommendations: list[str] = []
    for name in classes:
        text = rule.recommendations.get(name)
        if text and text not in recommendations:
            recommendations.append(text)
    recommendation = " ".join(recommendations) or rule.recommendation
    marker = rule.overlay.marker if rule.overlay else "highlight"
    overlay = {
        "component_id": component_id,
        "marker": marker,
        "color": "red" if severity == "critical" else "orange",
        "label": f"Визуальный дефект: {defects_text}",
    }
    node_rule = (
        rule
        if primary
        else rule.model_copy(
            update={
                "id": f"{rule.id}_{component_id}",
                # Поузловые оценки не должны схлопываться в один dedupe-group.
                "dedupe_group": None,
            }
        )
    )
    return RuleEvaluation(
        rule=node_rule,
        confidence=confidence,
        matched=True,
        evidence=evidence,
        conditions_matched=1,
        conditions_total=1,
        matched_weight=1.0,
        total_weight=1.0,
        severity=severity,
        message=message,
        recommendation=recommendation,
        overlay=overlay,
    )


def evaluate_visual_defect_nodes(
    rule: Rule,
    cv: CvSnapshot,
    now: datetime | None = None,
) -> list[RuleEvaluation]:
    """Все узлы с визуальными дефектами — отдельная оценка на каждый узел."""
    thresholds = _visual_thresholds(rule)
    if not thresholds:
        thresholds = {name: 0.35 for name in VISUAL_DEFECT_CLASSES}
    nodes = _visual_nodes(cv, thresholds)
    ordered = sorted(
        nodes.items(),
        key=lambda pair: (
            -_visual_confidence(
                [item.confidence for item in pair[1]],
                len({item.class_name for item in pair[1]}),
            ),
            pair[0],
        ),
    )
    return [
        _visual_node_evaluation(
            rule,
            component_id,
            items,
            thresholds,
            primary=index == 0,
        )
        for index, (component_id, items) in enumerate(ordered)
    ]


def _evaluate_visual_defect(
    rule: Rule,
    cv: CvSnapshot,
    now: datetime | None = None,
) -> RuleEvaluation:
    """Специальная оценка `visual_defect`: худший узел (для `evaluate_rule`).

    Полный список поузловых оценок даёт `evaluate_visual_defect_nodes`; эта
    функция оставлена для обратной совместимости и прямых тестов правила.
    """
    thresholds = _visual_thresholds(rule)
    if not thresholds:
        thresholds = {name: 0.35 for name in VISUAL_DEFECT_CLASSES}

    if not cv.available:
        return RuleEvaluation(
            rule=rule,
            confidence=0.0,
            matched=False,
            evidence=[],
            blocked_by="camera_unavailable",
            blocked_reason="нет свежих кадров с камеры (TTL истёк)",
            conditions_total=len(rule.conditions),
        )

    evaluations = evaluate_visual_defect_nodes(rule, cv, now)
    if not evaluations:
        return RuleEvaluation(
            rule=rule,
            confidence=0.0,
            matched=False,
            evidence=[],
            conditions_total=len(rule.conditions),
        )
    return evaluations[0]


def _eval_any(condition: Condition, context: _Context) -> ConditionResult:
    results = [_eval_condition(item, context) for item in condition.alternatives]
    matched_results = [item for item in results if item.matched]
    chosen = (
        max(matched_results, key=lambda item: item.score)
        if matched_results
        else max(results, key=lambda item: item.score, default=None)
    )
    matched = bool(matched_results)
    note: str | None = None
    if not matched:
        if any(item.note == "camera_unavailable" for item in results):
            note = "camera_unavailable"
        elif any(item.note == "ocr_unavailable" for item in results):
            note = "ocr_unavailable"
        elif results and all(item.note in {"signal_missing", "ref_missing"} for item in results):
            note = "signal_missing"
        else:
            note = "no_alternative_matched"

    return ConditionResult(
        condition=condition_text(condition),
        signal=condition.signal,
        op=condition.op,
        actual=chosen.actual if chosen else None,
        baseline=chosen.baseline if chosen else None,
        threshold=chosen.threshold if chosen else None,
        matched=matched,
        weight=condition.weight,
        score=chosen.score if chosen else 0.0,
        note=note,
        applicable=not condition.optional,
        cv_class=condition.cv_class,
    )


def _eval_condition(condition: Condition, context: _Context) -> ConditionResult:
    if condition.op == "any":
        return _eval_any(condition, context)
    if condition.op == "cv":
        return _eval_cv(condition, context)
    if condition.op in {"gt", "lt", "gte", "lte"}:
        return _eval_comparison(condition, context)
    if condition.op in {"trend_up", "trend_down"}:
        return _eval_trend(condition, context)
    if condition.op == "crosses":
        return _eval_crosses(condition, context)
    if condition.op == "unbalance_pct":
        return _eval_unbalance(condition, context)
    if condition.op == "deviation_pct":
        return _eval_deviation(condition, context)
    if condition.op == "in":
        return _eval_in(condition, context)
    if condition.op == "has_signal":
        return _eval_has_signal(condition, context)
    raise ValueError(f"неизвестный оператор: {condition.op}")


def combine_confidence(
    matched_weight: float,
    total_weight: float,
    matched_scores: Sequence[float],
) -> float:
    """Взвешенная доля совпадений × коэффициент запаса по порогу."""
    if total_weight <= 0 or not matched_scores:
        return 0.0
    fraction = clamp(matched_weight / total_weight)
    margin = 0.5 + 0.5 * (sum(matched_scores) / len(matched_scores))
    return round(clamp(fraction * margin), 4)


def evaluate_rule(
    rule: Rule,
    points: Sequence[TelemetryPoint],
    baseline: dict[str, Any],
    cv: CvSnapshot,
    now: datetime | None = None,
) -> RuleEvaluation:
    """Оценивает одно правило по окну телеметрии и CV-снимку."""
    if rule.dynamic == "visual_defect":
        # Визуальному правилу телеметрия не нужна: оно живёт только по кадрам.
        return _evaluate_visual_defect(rule, cv, now)

    ordered = sorted(points, key=lambda item: item[0])
    if not ordered:
        if rule.requires_cv and not cv.available:
            return RuleEvaluation(
                rule=rule,
                confidence=0.0,
                matched=False,
                evidence=[],
                blocked_by="camera_unavailable",
                blocked_reason="нет свежих кадров с камеры (TTL истёк)",
                conditions_total=len(rule.conditions),
            )
        return RuleEvaluation(
            rule=rule,
            confidence=0.0,
            matched=False,
            evidence=[],
            blocked_by="no_telemetry",
            blocked_reason="нет сэмплов телеметрии",
            conditions_total=len(rule.conditions),
        )

    latest_ts = ordered[-1][0]
    if now is not None and now < latest_ts:
        latest_ts = now
    context = _Context(
        points=ordered,
        latest=ordered[-1][1],
        latest_ts=latest_ts,
        baseline=baseline,
        cv=cv,
    )

    results = [_eval_condition(condition, context) for condition in rule.conditions]
    applicable = [result for result in results if result.applicable]
    total_weight = sum(result.weight for result in applicable)
    matched_results = [result for result in applicable if result.matched]
    matched_weight = sum(result.weight for result in matched_results)
    scores = [result.score for result in matched_results]

    blocked_by: str | None = None
    blocked_reason: str | None = None
    block_priority = 0
    for result in applicable:
        candidate: str | None = None
        reason: str | None = None
        if result.note == "camera_unavailable":
            candidate = "camera_unavailable"
            reason = "нет свежих кадров с камеры (TTL истёк)"
        elif result.note in {"signal_missing", "ref_missing"} and result.matched is None:
            candidate = f"signal_missing:{result.signal}"
            reason = f"в телеметрии нет сигнала {result.signal}"
        elif result.note == "insufficient_window" and result.matched is None:
            candidate = "insufficient_window"
            reason = "недостаточно данных для расчёта тренда"

        priority = _BLOCK_PRIORITY.get(candidate or "", 0)
        if candidate and candidate.startswith("signal_missing"):
            priority = _BLOCK_PRIORITY["signal_missing"]
        if candidate and priority > block_priority:
            blocked_by = candidate
            blocked_reason = reason
            block_priority = priority

    if rule.requires_ocr and _number(context.latest.get("manometer_ocr")) is None:
        blocked_by = "ocr_unavailable"
        blocked_reason = "нет OCR-значения аналогового манометра"

    if blocked_by is not None:
        # CV/OCR-правило не должно срабатывать по одним датчикам.
        confidence = 0.0
    else:
        confidence = combine_confidence(matched_weight, total_weight, scores)

    return RuleEvaluation(
        rule=rule,
        confidence=confidence,
        matched=blocked_by is None and matched_weight == total_weight and total_weight > 0,
        evidence=[result.to_evidence() for result in results],
        blocked_by=blocked_by,
        blocked_reason=blocked_reason,
        conditions_matched=len(matched_results),
        conditions_total=len(applicable),
        matched_weight=matched_weight,
        total_weight=total_weight,
    )


def evaluate_all(
    rules: Iterable[Rule],
    points: Sequence[TelemetryPoint],
    baseline: dict[str, Any],
    cv: CvSnapshot,
    now: datetime | None = None,
) -> list[RuleEvaluation]:
    """Оценивает каталог правил.

    `visual_defect` раскрывается в отдельную оценку на каждый узел с
    дефектом: два узла → два открытых события с разными `component_id`.
    """
    results: list[RuleEvaluation] = []
    for rule in rules:
        if not rule.enabled:
            continue
        if rule.dynamic == "visual_defect":
            nodes = evaluate_visual_defect_nodes(rule, cv, now)
            if nodes:
                results.extend(nodes)
            else:
                # Нет дефектов/камера недоступна: базовая оценка нужна, чтобы
                # закрыть открытое событие и показать blocked_by.
                results.append(evaluate_rule(rule, points, baseline, cv, now=now))
        else:
            results.append(evaluate_rule(rule, points, baseline, cv, now=now))
    return results


def dedupe(
    evaluations: Sequence[RuleEvaluation],
    relevance_threshold: float = 0.45,
    activate_confidence: float | None = None,
) -> list[RuleEvaluation]:
    """Схлопывает перекрывающиеся правила, оставляя более специфичное.

    Дополнительно подавляет правила из `suppressed_by_rules`, если более
    специфичный диагноз уже активен (confidence ≥ `activate_confidence`).
    Так `visual_defect` не дублирует `seal_leak`, `mechanical_looseness`,
    `electrical_contact` и `guard_removed`.
    """
    winners: dict[str, RuleEvaluation] = {}
    singles: list[RuleEvaluation] = []

    def key(item: RuleEvaluation) -> tuple[int, int, float]:
        return (item.rule.specificity, item.severity_rank, item.confidence)

    for evaluation in evaluations:
        group = evaluation.rule.dedupe_group
        if not group or evaluation.confidence < relevance_threshold:
            singles.append(evaluation)
            continue
        current = winners.get(group)
        if current is None:
            winners[group] = evaluation
            continue
        if key(evaluation) > key(current):
            current.suppressed_by = evaluation.rule.id
            winners[group] = evaluation
        else:
            evaluation.suppressed_by = current.rule.id

    result = singles + list(winners.values())

    active_threshold = (
        relevance_threshold
        if activate_confidence is None
        else activate_confidence
    )
    active = {
        evaluation.rule.id: evaluation
        for evaluation in result
        if evaluation.matched
        and not evaluation.suppressed_by
        and evaluation.confidence >= active_threshold
    }
    for evaluation in result:
        if evaluation.suppressed_by:
            continue
        for suppressor_id in evaluation.rule.suppressed_by_rules:
            suppressor = active.get(suppressor_id)
            if suppressor is not None and suppressor is not evaluation:
                evaluation.suppressed_by = suppressor_id
                break
    return result


def sort_evaluations(evaluations: Sequence[RuleEvaluation]) -> list[RuleEvaluation]:
    """Приоритет: critical > warning > info, затем confidence."""
    return sorted(
        evaluations,
        key=lambda item: (-item.severity_rank, -item.confidence, item.rule.id),
    )


__all__ = [
    "ConditionResult",
    "RuleEvaluation",
    "TelemetryPoint",
    "clamp",
    "combine_confidence",
    "dedupe",
    "evaluate_all",
    "evaluate_rule",
    "evaluate_visual_defect_nodes",
    "linear_slope",
    "sort_evaluations",
]
