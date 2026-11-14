"""Единый реестр визуальных признаков.

Один признак может приходить из разных каналов:

* `heuristics` — `cv_classic.defects` (rust/oil/oxidation);
* `vlm` — локальная vision-модель (`/vision/check`);
* `marker` — маркерная проверка ArUco (кожух муфты).

Реестр — единственный источник русских названий, допустимых источников и
правила, которое использует признак. Аналитика и диагностика ссылаются на
`sign` из этого списка, а не на сырые имена классов.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VisualSign:
    """Описание одного визуального признака."""

    id: str
    label: str
    sources: tuple[str, ...]
    rule: str | None
    description: str


VISUAL_SIGNS: tuple[VisualSign, ...] = (
    VisualSign(
        "corrosion",
        "Коррозия",
        ("heuristics", "vlm", "yolo"),
        "visual_defect",
        "Ржавчина/коррозия поверхности узла.",
    ),
    VisualSign(
        "oil_leak",
        "Подтёк масла",
        ("heuristics", "vlm"),
        "visual_defect",
        "Масляное пятно или подтёк у уплотнения.",
    ),
    VisualSign(
        "oxidation",
        "Окисление контактов",
        ("heuristics",),
        "visual_defect",
        "Потемнение/окисление клемм и контактов.",
    ),
    VisualSign(
        "paint_peeling",
        "Отслоение краски",
        ("vlm",),
        "visual_defect",
        "Отслоение лакокрасочного покрытия.",
    ),
    VisualSign(
        "mechanical_damage",
        "Механическое повреждение",
        ("vlm",),
        "visual_defect",
        "Вмятина, задир или иное механическое повреждение.",
    ),
    VisualSign(
        "crack",
        "Трещина/скол",
        ("vlm", "detector"),
        "visual_defect",
        "Трещина или скол; задел под отдельный детектор.",
    ),
    VisualSign(
        "texture_anomaly",
        "Аномалия текстуры",
        ("anomaly",),
        "visual_defect",
        "Отклонение текстуры узла от эталонной модели anomaly/v2.",
    ),
    VisualSign(
        "guard_missing",
        "Кожух муфты отсутствует",
        ("marker",),
        "guard_removed",
        "В зоне coupling_guard не найден ArUco id 4.",
    ),
)

SIGN_BY_ID: dict[str, VisualSign] = {item.id: item for item in VISUAL_SIGNS}
SIGN_IDS: tuple[str, ...] = tuple(item.id for item in VISUAL_SIGNS)

# Класс детекции/эвристики -> признак реестра.
HEURISTIC_SIGN_MAP: dict[str, str] = {
    "corrosion": "corrosion",
    "oil_leak": "oil_leak",
    "oxidation": "oxidation",
    "crack": "crack",
    "texture_anomaly": "texture_anomaly",
}

# Признаки, которые умеет отдавать VLM.
VLM_SIGN_MAP: dict[str, str] = {
    "corrosion": "corrosion",
    "oil_leak": "oil_leak",
    "paint_peeling": "paint_peeling",
    "mechanical_damage": "mechanical_damage",
    "crack": "crack",
}


def sign_definition(sign: str) -> VisualSign | None:
    return SIGN_BY_ID.get(str(sign))


def sign_label(sign: str) -> str:
    definition = sign_definition(sign)
    return definition.label if definition else str(sign)


def heuristic_sign_for_class(class_name: str) -> str | None:
    """Класс дефекта эвристик -> id признака (или None)."""
    return HEURISTIC_SIGN_MAP.get(str(class_name))


def vlm_sign_for_name(name: str) -> str | None:
    """Имя признака VLM -> id признака (или None)."""
    return VLM_SIGN_MAP.get(str(name))


__all__ = [
    "HEURISTIC_SIGN_MAP",
    "SIGN_BY_ID",
    "SIGN_IDS",
    "VISUAL_SIGNS",
    "VLM_SIGN_MAP",
    "VisualSign",
    "heuristic_sign_for_class",
    "sign_definition",
    "sign_label",
    "vlm_sign_for_name",
]
