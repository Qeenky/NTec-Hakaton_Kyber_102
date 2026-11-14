"""Идемпотентное заполнение БД карточками агрегатов из data/seed_rig.json.

`seed_rig.json` содержит список карточек (`equipment: [...]`): исправный
PUMP-001 и дефектный PUMP-002 (те же узлы и baseline, другие состояния).
Сид догоняет старые БД: описательные поля и baseline — источник истины,
наработка перезаписывается только при однократной миграции старой карточки,
дальше её ведёт симулятор. Повторный запуск не дублирует оборудование,
комплектующие и регламентные интервалы.

Для обратной совместимости поддерживается и прежний формат: `equipment` —
один объект, а `components` / `maintenance_intervals` — на верхнем уровне.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from . import db
from .models import Component, Equipment, MaintenanceInterval

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_rig.json"


def load_seed() -> dict[str, Any]:
    """Читает JSON с данными карточек."""
    with SEED_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _is_legacy_card(row: Equipment, baseline: dict[str, Any]) -> bool:
    """Старая демо-карточка: baseline пуст или расходится с текущим.

    Сравниваем `vibration_rms`; при миграции наработку можно безопасно
    привести к базовой оценке.
    """
    stored = row.baseline_json or {}
    return stored.get("vibration_rms") != baseline.get("vibration_rms")


def iter_cards(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Нормализует seed в список карточек: equipment + components + intervals.

    Поддерживаются оба формата: `equipment` списком (новый) и одним объектом
    (старый, где components/intervals лежат на верхнем уровне).
    """
    raw = data.get("equipment")
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    cards: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        cards.append(
            {
                "equipment": item,
                "components": item.get("components")
                or data.get("components")
                or [],
                "maintenance_intervals": item.get("maintenance_intervals")
                or data.get("maintenance_intervals")
                or [],
            }
        )
    return cards


def _seed_card(session: Any, card: dict[str, Any]) -> bool:
    """Создаёт/догоняет одну карточку. True — оборудование создано сейчас."""
    equipment = card["equipment"]
    baseline = equipment.get("baseline") or {}
    inserted = False

    row = session.get(Equipment, equipment["id"])
    legacy = row is not None and _is_legacy_card(row, baseline)
    if row is None:
        row = Equipment(
            id=equipment["id"],
            name=equipment["name"],
            model=equipment.get("model"),
            serial=equipment.get("serial"),
            year=equipment.get("year"),
            runtime_hours=equipment.get("runtime_hours"),
            normative_life_years=equipment.get("normative_life_years"),
            last_service_date=equipment.get("last_service_date"),
            qr_code=equipment.get("qr_code"),
            location=equipment.get("location"),
            baseline_json=baseline,
        )
        session.add(row)
        session.flush()
        inserted = True
    else:
        # Описательные поля и baseline всегда из карточки — источник истины.
        row.name = equipment["name"]
        row.model = equipment.get("model")
        row.serial = equipment.get("serial")
        row.year = equipment.get("year")
        row.normative_life_years = equipment.get("normative_life_years")
        row.last_service_date = equipment.get("last_service_date")
        row.qr_code = equipment.get("qr_code")
        row.location = equipment.get("location")
        if not row.baseline_json or legacy:
            row.baseline_json = baseline
        if legacy and equipment.get("runtime_hours") is not None:
            # Один раз при переходе со старой демо-карточки; дальше наработку
            # увеличивает симулятор и перезапись при рестарте недопустима.
            row.runtime_hours = equipment.get("runtime_hours")

    existing_components = {
        component.name: component
        for component in session.scalars(
            select(Component).where(Component.equipment_id == equipment["id"])
        ).all()
    }
    for component in card["components"]:
        current = existing_components.get(component["name"])
        if current is None:
            session.add(
                Component(
                    id=f"{equipment['id']}-{component['name']}",
                    equipment_id=equipment["id"],
                    name=component["name"],
                    kind=component.get("kind", "component"),
                    normative_life_years=component.get("normative_life_years"),
                    installed_date=component.get("installed_date"),
                    condition=component.get("condition", "ok"),
                )
            )
            continue
        # id/name — зоны AR, их не меняем; обновляем только нормативы.
        current.kind = component.get("kind", "component")
        current.normative_life_years = component.get("normative_life_years")
        current.installed_date = component.get("installed_date")
        current.condition = component.get("condition", "ok")

    for interval in card["maintenance_intervals"]:
        existing = session.scalar(
            select(MaintenanceInterval).where(
                MaintenanceInterval.equipment_id == equipment["id"],
                MaintenanceInterval.interval_hours == interval["interval_hours"],
            )
        )
        if existing is None:
            session.add(
                MaintenanceInterval(
                    equipment_id=equipment["id"],
                    interval_hours=interval["interval_hours"],
                    name=interval["name"],
                    description=interval.get("description"),
                )
            )
        else:
            existing.name = interval["name"]
            existing.description = interval.get("description")

    return inserted


def seed_equipment() -> list[str]:
    """Идемпотентно создаёт/догоняет все карточки; возвращает созданные id."""
    cards = iter_cards(load_seed())
    created: list[str] = []
    with db.session_scope() as session:
        for card in cards:
            if _seed_card(session, card):
                created.append(str(card["equipment"]["id"]))
    return created


def seed_if_empty() -> bool:
    """Создаёт карточки, если их нет; иначе приводит к данным seed_rig.json.

    Возвращает True, если хотя бы одна карточка была создана в этом вызове.
    """
    return bool(seed_equipment())


def seed_all() -> dict[str, Any]:
    """Полный сид: вызывается после `db.init_db()`."""
    created = seed_equipment()
    return {
        "equipment_created": bool(created),
        "equipment_created_ids": created,
        "tables": db.table_counts(),
    }


__all__ = [
    "iter_cards",
    "load_seed",
    "seed_all",
    "seed_equipment",
    "seed_if_empty",
]
