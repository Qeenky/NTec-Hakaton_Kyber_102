"""Константы связи VLM-проверки с движком диагностики."""

from __future__ import annotations

# Диагностика, которую создаёт vision-проверка (не правило YAML).
VISUAL_SIGNS_RULE_ID = "visual_signs"
VISUAL_SIGNS_TITLE = "Визуальные признаки дефекта (VLM)"

SIGN_TITLES_RU: dict[str, str] = {
    "corrosion": "коррозия",
    "paint_peeling": "отслоение краски",
    "oil_leak": "масляный подтёк",
    "mechanical_damage": "механическое повреждение",
}

SIGN_RECOMMENDATIONS: dict[str, str] = {
    "corrosion": "Зачистить и окрасить поверхность, проверить толщину стенки.",
    "paint_peeling": "Очистить отслаивающееся покрытие и восстановить окраску.",
    "oil_leak": "Проверить уплотнения и уровень масла, устранить подтёк.",
    "mechanical_damage": "Осмотреть повреждённый узел, оценить допустимость эксплуатации.",
}

__all__ = [
    "SIGN_RECOMMENDATIONS",
    "SIGN_TITLES_RU",
    "VISUAL_SIGNS_RULE_ID",
    "VISUAL_SIGNS_TITLE",
]
