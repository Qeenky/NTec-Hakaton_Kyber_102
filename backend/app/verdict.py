"""Итог осмотра по окну видеоаналитики.

Вердикт никогда не превращает нехватку данных в «норму»: если часть узлов
не наблюдалась, итог — `insufficient_data`, а не `no_visible_defects`.
Признаки берутся из единого реестра (`visual_signs`) и окна наблюдения.
"""

from __future__ import annotations

from typing import Any, Sequence

from .analytics import COMPONENT_LABELS_RU
from .visual_signs import sign_label

DISCLAIMER = (
    "Отсутствие видимых дефектов не подтверждает исправность оборудования."
)

ATTENTION_STATUSES = {"check_required", "possible_defect"}
MIN_VISIBILITY = 0.6


def _russian_list(labels: Sequence[str]) -> str:
    items = [str(item) for item in labels if item]
    if not items:
        return "—"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " и " + items[-1]


def _unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def build_verdict(
    summary: dict[str, Any] | None,
    *,
    roster: Sequence[str] | None = None,
    diagnostics: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Собирает `verdict` из полной сводки `analytics`.

    Приоритет:
    1. подтверждённый признак/дефект → `defects_found`;
    2. `single_observation`/`check_required`/неподтверждённые наблюдения →
       `attention_required`;
    3. не все узлы наблюдались достаточно → `insufficient_data`;
    4. все `ok` и видимость ≥ 0.6 → `no_visible_defects`.
    """
    summary = summary or {}
    components = [item for item in summary.get("components") or [] if item]
    frames = int(summary.get("frames") or 0)

    if not components:
        not_seen = list(roster or [])
        labels = [
            COMPONENT_LABELS_RU.get(component, component) for component in not_seen
        ]
        return {
            "status": "insufficient_data",
            "reason": (
                "Видеонаблюдение не проводилось: данных по узлам нет."
                if not not_seen
                else "Видеонаблюдение не проводилось, узлы не проверены: "
                f"{_russian_list(labels)}."
            ),
            "components_with_issues": [],
            "components_not_seen": not_seen,
            "disclaimer": DISCLAIMER,
        }

    by_id = {
        str(item["component_id"]): item
        for item in components
        if item.get("component_id")
    }
    labels = {
        component_id: str(item.get("label") or component_id)
        for component_id, item in by_id.items()
    }

    detected_signs: list[tuple[str, str]] = []
    single_signs: list[tuple[str, str]] = []
    for component_id, item in by_id.items():
        for sign in item.get("signs") or []:
            status = sign.get("status")
            label = str(sign.get("label") or sign_label(str(sign.get("sign"))))
            area_pct = sign.get("area_pct")
            if sign.get("sign") == "corrosion" and area_pct is not None:
                label = f"{label} ({float(area_pct):.0f}% площади)"
            if status == "detected":
                detected_signs.append((component_id, label))
            elif status == "single_observation":
                single_signs.append((component_id, label))

    # Обратная совместимость со старыми снимками без `signs`.
    confirmed_nodes = [
        component_id
        for component_id, item in by_id.items()
        if any(defect.get("confirmed") for defect in item.get("defects") or [])
    ]
    attention_nodes = [
        component_id
        for component_id, item in by_id.items()
        if component_id not in confirmed_nodes
        and (
            item.get("status_window") in ATTENTION_STATUSES
            or any(
                not defect.get("confirmed")
                for defect in item.get("defects") or []
            )
        )
    ]

    issue_nodes = sorted(
        {component_id for component_id, _ in detected_signs}
        | {component_id for component_id, _ in single_signs}
        | set(confirmed_nodes)
        | set(attention_nodes)
    )
    not_seen = [
        component_id
        for component_id, item in by_id.items()
        if int(item.get("frames_seen") or 0) == 0
    ]
    low_visibility = [
        component_id
        for component_id, item in by_id.items()
        if float(item.get("visibility_ratio") or 0.0) < MIN_VISIBILITY
    ]
    all_ok = all(item.get("status_window") == "ok" for item in by_id.values())

    if detected_signs or confirmed_nodes:
        labels_ru = _unique([label for _, label in detected_signs]) or [
            labels[component_id] for component_id in confirmed_nodes
        ]
        return {
            "status": "defects_found",
            "reason": (
                "По окну наблюдения подтверждены визуальные признаки: "
                f"{_russian_list(labels_ru)}."
            ),
            "components_with_issues": issue_nodes,
            "components_not_seen": sorted(not_seen),
            "disclaimer": DISCLAIMER,
        }

    if single_signs or attention_nodes:
        labels_ru = _unique([label for _, label in single_signs]) or [
            labels[component_id] for component_id in attention_nodes
        ]
        return {
            "status": "attention_required",
            "reason": (
                "Есть визуальные признаки, требующие повторной проверки: "
                f"{_russian_list(labels_ru)}."
            ),
            "components_with_issues": issue_nodes,
            "components_not_seen": sorted(not_seen),
            "disclaimer": DISCLAIMER,
        }

    if not all_ok or low_visibility or not_seen or frames == 0:
        missing = sorted(set(not_seen) | set(low_visibility))
        if missing:
            reason = (
                "Недостаточно данных: узлы не наблюдались достаточно долго — "
                f"{_russian_list([labels[c] for c in missing])}."
            )
        else:
            reason = (
                "Недостаточно данных для итога: часть узлов не наблюдалась "
                "в окне."
            )
        return {
            "status": "insufficient_data",
            "reason": reason,
            "components_with_issues": [],
            "components_not_seen": sorted(not_seen),
            "disclaimer": DISCLAIMER,
        }

    return {
        "status": "no_visible_defects",
        "reason": (
            "Все узлы наблюдались достаточно (видимость не ниже 60 %), "
            "видимых дефектов не обнаружено."
        ),
        "components_with_issues": [],
        "components_not_seen": [],
        "disclaimer": DISCLAIMER,
    }


__all__ = ["DISCLAIMER", "build_verdict"]
