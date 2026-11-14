"""Оценка остаточного ресурса комплектующей.

Расчёт всегда приблизительный: is_estimate=true, в basis перечислены
исходные данные и формула.
"""

from __future__ import annotations

from datetime import date
from typing import Any

# Коэффициенты состояния: чем хуже состояние, тем быстрее расходуется ресурс.
CONDITION_FACTORS: dict[str, float] = {
    "ok": 1.0,
    "check_required": 0.7,
    "possible_defect": 0.4,
}

DAYS_IN_YEAR = 365.25


def _insufficient(basis: dict[str, Any], reason: str) -> dict[str, Any]:
    """Единый ответ при нехватке исходных данных."""
    return {
        "value": None,
        "status": "insufficient_data",
        "is_estimate": True,
        "basis": {**basis, "reason": reason},
    }


def compute_remaining_resource(
    normative_life_years: float | None,
    installed_date: str | None,
    condition: str | None,
    today: date | None = None,
) -> dict[str, Any]:
    """Считает остаток ресурса в годах с поправкой на состояние.

    remaining_years = max(0, normative_life_years - age_years) * condition_factor
    """
    today = today or date.today()
    factor = CONDITION_FACTORS.get(condition or "ok", 1.0)
    basis: dict[str, Any] = {
        "normative_life_years": normative_life_years,
        "installed_date": installed_date,
        "condition": condition,
        "condition_factor": factor,
        "as_of": today.isoformat(),
        "formula": "max(0, normative_life_years - age_years) * condition_factor",
    }

    if normative_life_years is None:
        return _insufficient(basis, "не задан нормативный срок службы")
    if not installed_date:
        return _insufficient(basis, "не задана дата установки")
    try:
        installed = date.fromisoformat(installed_date)
    except ValueError:
        return _insufficient(basis, "дата установки не в формате YYYY-MM-DD")

    age_years = max(0.0, (today - installed).days / DAYS_IN_YEAR)
    remaining = max(0.0, float(normative_life_years) - age_years) * factor
    value = round(remaining, 2)

    basis["age_years"] = round(age_years, 2)
    return {
        "value": value,
        "status": "ok" if value > 0 else "exhausted",
        "is_estimate": True,
        "basis": basis,
    }
