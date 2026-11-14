"""Каталог правил: загрузка YAML и валидация pydantic-схемой.

Правила — данные: чтобы поменять порог, не нужно править Python. Формат условия:
`{signal, op, value | ref, window_sec?}`; `ref` ссылается на `baseline.*` из
карточки оборудования или на другой сигнал.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import get_settings

ComparisonOp = Literal["gt", "lt", "gte", "lte"]
ConditionOp = Literal[
    "gt",
    "lt",
    "gte",
    "lte",
    "trend_up",
    "trend_down",
    "crosses",
    "unbalance_pct",
    "deviation_pct",
    "in",
    "has_signal",
    "cv",
    "any",
]
Severity = Literal["critical", "warning", "info"]

SEVERITY_RANK: dict[str, int] = {"critical": 3, "warning": 2, "info": 1}


class Condition(BaseModel):
    """Одно условие правила."""

    model_config = ConfigDict(extra="forbid")

    signal: str | None = None
    op: ConditionOp
    value: float | int | str | list[float | str] | None = None
    values: list[float] | None = None
    ref: str | None = None
    factor: float = 1.0
    window_sec: float = 0.0
    weight: float = Field(default=1.0, gt=0)
    optional: bool = False
    cv_class: str | None = None
    component_id: str | None = None
    min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    alternatives: list["Condition"] = Field(default_factory=list)
    description: str | None = None

    @model_validator(mode="after")
    def validate_condition(self) -> "Condition":
        """Проверяет обязательные поля для каждого оператора."""
        if self.op == "any":
            if not self.alternatives:
                raise ValueError("op=any требует непустой список alternatives")
            if self.signal is not None:
                raise ValueError("op=any не использует signal, условия задаются в alternatives")
            return self

        if self.op == "cv":
            if not self.cv_class:
                raise ValueError("op=cv требует cv_class")
            if self.optional:
                raise ValueError("CV-условие не может быть optional: камера — обязательный канал")
            return self

        if self.signal is None:
            raise ValueError(f"op={self.op} требует signal")

        if self.op in {"gt", "lt", "gte", "lte"}:
            if self.ref is None and self.value is None:
                raise ValueError(f"op={self.op} требует value или ref")
            if self.ref is not None and self.value is not None:
                raise ValueError("укажите что-то одно: value или ref")
            if self.value is not None and not isinstance(self.value, (int, float)):
                raise ValueError(f"op={self.op} требует числовое value")

        if self.op in {"trend_up", "trend_down"}:
            if self.window_sec <= 0:
                raise ValueError("trend_up/trend_down требует window_sec > 0")
            if self.value is not None and not isinstance(self.value, (int, float)):
                raise ValueError("value для тренда — минимальный наклон, число")

        if self.op == "crosses":
            if not self.values and not isinstance(self.value, (int, float)):
                raise ValueError("op=crosses требует values или числовое value")
            if self.window_sec <= 0:
                raise ValueError("op=crosses требует window_sec > 0")

        if self.op == "unbalance_pct":
            if not isinstance(self.value, (int, float)):
                raise ValueError("op=unbalance_pct требует числовой value (порог, %)")

        if self.op == "deviation_pct":
            if self.ref is None:
                raise ValueError("op=deviation_pct требует ref (с чем сравниваем)")
            if not isinstance(self.value, (int, float)):
                raise ValueError("op=deviation_pct требует числовой value (порог, %)")

        if self.op == "in":
            if not isinstance(self.value, list):
                raise ValueError("op=in требует список допустимых значений")

        return self


Condition.model_rebuild()


class Overlay(BaseModel):
    """Что подсветить в AR-интерфейсе."""

    model_config = ConfigDict(extra="forbid")

    component_id: str
    marker: str = "outline"
    color: str = "orange"
    label: str | None = None


class Rule(BaseModel):
    """Правило диагностики."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    title: str
    severity: Severity
    description: str
    message: str
    recommendation: str
    conditions: list[Condition] = Field(min_length=1)
    overlay: Overlay | None = None
    dedupe_group: str | None = None
    specificity: int = 0
    latch_sec: float = Field(default=0.0, ge=0.0)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    # Специальный режим оценки (сейчас только визуальные дефекты).
    dynamic: Literal["visual_defect"] | None = None
    # Рекомендации по классу дефекта: {class_name: текст}.
    recommendations: dict[str, str] = Field(default_factory=dict)
    # Если эти правила активны, текущее правило подавляется как менее специфичное.
    suppressed_by_rules: list[str] = Field(default_factory=list)
    # Собственный порог активации (эвристики слабее датчиков).
    activate_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @property
    def requires_cv(self) -> bool:
        """Есть ли в правиле обязательное CV-условие."""
        return any(_has_cv(condition) for condition in self.conditions)

    @property
    def requires_ocr(self) -> bool:
        """Нужно ли OCR-значение манометра."""
        return any(
            condition.signal == "manometer_ocr" for condition in _flatten(self.conditions)
        )


def _flatten(conditions: list[Condition]) -> list[Condition]:
    result: list[Condition] = []
    for condition in conditions:
        result.append(condition)
        if condition.alternatives:
            result.extend(_flatten(condition.alternatives))
    return result


def _has_cv(condition: Condition) -> bool:
    if condition.op == "cv":
        return True
    return any(_has_cv(item) for item in condition.alternatives)


class RuleCatalog(BaseModel):
    """Корневой документ rules.yaml."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    activate_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    release_confidence: float = Field(default=0.45, ge=0.0, le=1.0)
    rules: list[Rule] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> "RuleCatalog":
        if self.release_confidence >= self.activate_confidence:
            raise ValueError("release_confidence должен быть меньше activate_confidence")
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
            raise ValueError(f"дубли id правил: {duplicates}")
        return self

    def get(self, rule_id: str) -> Rule | None:
        return next((rule for rule in self.rules if rule.id == rule_id), None)


def _describe_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    return str(value)


def condition_text(condition: Condition) -> str:
    """Человекочитаемое условие — для /rules, evidence и слайдов."""
    if condition.description:
        return condition.description

    if condition.op == "any":
        inner = " ИЛИ ".join(condition_text(item) for item in condition.alternatives)
        return f"({inner})"

    if condition.op == "cv":
        component = f", узел {condition.component_id}" if condition.component_id else ""
        return (
            f"CV: {condition.cv_class} (conf ≥ {condition.min_confidence:.2f}"
            f"{component}, TTL 5 с)"
        )

    signal = condition.signal or "?"
    if condition.op in {"gt", "lt", "gte", "lte"}:
        sign = {"gt": ">", "lt": "<", "gte": "≥", "lte": "≤"}[condition.op]
        if condition.ref:
            factor = (
                f"{condition.factor:g} × " if condition.factor != 1.0 else ""
            )
            return f"{signal} {sign} {factor}{condition.ref}"
        return f"{signal} {sign} {_describe_value(condition.value)}"

    if condition.op in {"trend_up", "trend_down"}:
        direction = "растёт" if condition.op == "trend_up" else "падает"
        threshold = float(condition.value or 0.0)
        return (
            f"{signal} {direction}: |наклон| ≥ {threshold:g}/с "
            f"(окно {condition.window_sec:g} с)"
        )

    if condition.op == "crosses":
        thresholds = condition.values or [condition.value]
        return (
            f"{signal} пересекает {_describe_value(thresholds)} "
            f"(окно {condition.window_sec:g} с)"
        )

    if condition.op == "unbalance_pct":
        return f"дисбаланс фаз {signal} > {condition.value} %"

    if condition.op == "deviation_pct":
        return (
            f"|{signal} − {condition.ref}| / |{condition.ref}| × 100 > "
            f"{condition.value} %"
        )

    if condition.op == "in":
        return f"{signal} ∈ {_describe_value(condition.value)}"

    if condition.op == "has_signal":
        return f"{signal} присутствует"

    return f"{signal} {condition.op} {_describe_value(condition.value)}"


def rule_as_dict(rule: Rule) -> dict[str, Any]:
    """Представление правила для `GET /rules`."""
    return {
        "id": rule.id,
        "title": rule.title,
        "severity": rule.severity,
        "description": rule.description,
        "message": rule.message,
        "recommendation": rule.recommendation,
        "conditions": [
            {
                "text": condition_text(condition),
                "op": condition.op,
                "weight": condition.weight,
            }
            for condition in rule.conditions
        ],
        "requires_cv": rule.requires_cv,
        "requires_ocr": rule.requires_ocr,
        "dedupe_group": rule.dedupe_group,
        "specificity": rule.specificity,
        "latch_sec": rule.latch_sec,
        "overlay": rule.overlay.model_dump() if rule.overlay else None,
        "tags": rule.tags,
        "dynamic": rule.dynamic,
        "recommendations": rule.recommendations,
        "suppressed_by_rules": rule.suppressed_by_rules,
        "activate_confidence": rule.activate_confidence,
    }


@lru_cache(maxsize=4)
def _load_cached(path: str, mtime_ns: int) -> RuleCatalog:
    """Кэш по mtime: правка YAML подхватывается без перезапуска процесса."""
    del mtime_ns  # участвует только в ключе кэша
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    return RuleCatalog.model_validate(data)


def load_catalog(path: str | Path | None = None) -> RuleCatalog:
    """Загружает и валидирует каталог правил."""
    catalog_path = Path(path or get_settings().rules_path)
    if not catalog_path.is_file():
        raise FileNotFoundError(f"каталог правил не найден: {catalog_path}")
    return _load_cached(str(catalog_path), catalog_path.stat().st_mtime_ns)


def catalog_as_dict(catalog: RuleCatalog) -> dict[str, Any]:
    """Каталог для API со всеми человекочитаемыми описаниями."""
    return {
        "version": catalog.version,
        "activate_confidence": catalog.activate_confidence,
        "release_confidence": catalog.release_confidence,
        "rules_count": len(catalog.rules),
        "rules": [rule_as_dict(rule) for rule in catalog.rules],
    }


__all__ = [
    "Condition",
    "Overlay",
    "Rule",
    "RuleCatalog",
    "SEVERITY_RANK",
    "catalog_as_dict",
    "condition_text",
    "load_catalog",
    "rule_as_dict",
]
