"""Сервис vision: гипотезы датчиков, интеграция с диагностикой, объяснение.

Здесь нет вызовов HTTP напрямую — только `client.check_component` /
`client.explain_facts`. Все функции синхронные, API оборачивает их в
`asyncio.to_thread`, чтобы не блокировать event loop.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from .. import analytics, db
from ..config import get_settings
from ..diagnostics.evaluator import COMPONENT_TITLES_RU
from ..resource import compute_remaining_resource
from . import client
from .constants import (
    SIGN_RECOMMENDATIONS,
    SIGN_TITLES_RU,
    VISUAL_SIGNS_RULE_ID,
    VISUAL_SIGNS_TITLE,
)

_last_results: dict[str, dict[str, Any]] = {}
_last_frames: dict[str, bytes] = {}
_state_lock = threading.Lock()

# Правила → короткая гипотеза для промпта (без чисел: числа берём из датчиков).
RULE_HINTS: dict[str, str] = {
    "bearing_wear": "износ подшипника",
    "cavitation": "кавитация",
    "cavitation_confirmed": "подтверждённая кавитация",
    "misalignment": "расцентровка",
    "impeller_wear": "износ рабочего колеса",
    "seal_leak": "утечка через уплотнение",
    "mechanical_looseness": "ослабление крепления",
    "lubrication_degradation": "деградация смазки",
    "electrical_contact": "нагрев электрических контактов",
    "gauge_drift": "дрейф манометра",
    "guard_removed": "повреждён или снят кожух",
    "maintenance_due": "подходит срок обслуживания",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Последний кадр (/ws/frame, /analyze) и последний результат проверки


def remember_frame(equipment_id: str, image_bytes: bytes) -> None:
    """Запоминает последний кадр узла для `/vision/check` без файла."""
    if not image_bytes:
        return
    with _state_lock:
        _last_frames[equipment_id] = bytes(image_bytes)


def last_frame(equipment_id: str) -> bytes | None:
    with _state_lock:
        return _last_frames.get(equipment_id)


def latest_vision(equipment_id: str) -> dict[str, Any] | None:
    """Последняя успешная VLM-проверка узла (для поля `vision` в /analyze)."""
    with _state_lock:
        result = _last_results.get(equipment_id)
        return dict(result) if result is not None else None


def clear_vision_state(equipment_id: str | None = None) -> None:
    with _state_lock:
        if equipment_id is None:
            _last_results.clear()
            _last_frames.clear()
        else:
            _last_results.pop(equipment_id, None)
            _last_frames.pop(equipment_id, None)


# Гипотезы из телеметрии и активных диагностик


def _number(signals: dict[str, Any], name: str) -> float | None:
    value = signals.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_hypotheses(equipment_id: str, component_id: str) -> list[str]:
    """Подсказки модели: что датчики подозревают на этом узле.

    Промпт получает список гипотез, но модель обязана подтверждать признак
    только по изображению. Числа гипотез позже разрешены в `comment`
    (проверка `grounded`) — это входные данные.
    """
    latest = db.get_latest_telemetry(equipment_id)
    signals = (latest or {}).get("signals") or {}
    hints: list[str] = []

    hf = _number(signals, "vibration_hf")
    if hf is not None and hf > 2.5:
        hints.append("повышенная высокочастотная вибрация (возможен износ подшипника)")
    rms = _number(signals, "vibration_rms")
    if rms is not None and rms > 2.8:
        hints.append("повышенная общая вибрация")
    bearing = _number(signals, "temperature_bearing")
    if bearing is not None and bearing > 75.0:
        hints.append("перегрев подшипникового узла")
    motor_temp = _number(signals, "temperature_motor")
    if motor_temp is not None and motor_temp > 90.0:
        hints.append("перегрев обмоток электродвигателя")
    inlet = _number(signals, "pressure_inlet")
    if inlet is not None and inlet < 0.24:
        hints.append("падение давления на входе")
    currents = [
        value
        for name in ("current_a", "current_b", "current_c")
        if (value := _number(signals, name)) is not None
    ]
    if len(currents) >= 2:
        mean = sum(currents) / len(currents)
        if abs(mean) > 1e-9 and (max(currents) - min(currents)) / abs(mean) * 100.0 > 5.0:
            hints.append("неравномерная нагрузка по фазам")
    oil_level = _number(signals, "oil_level")
    if oil_level is not None and oil_level < 30.0:
        hints.append("низкий уровень масла")
    if signals.get("oil_color") == "dark":
        hints.append("загрязнение масла")
    efficiency = _number(signals, "efficiency")
    if efficiency is not None and efficiency < 53.6:
        hints.append("снижение КПД насоса")

    for row in db.get_open_diagnostics(equipment_id):
        rule_id = str(row.get("rule_id") or "")
        hint = RULE_HINTS.get(rule_id)
        if hint and hint not in hints:
            hints.append(hint)

    if not hints:
        hints.append("тревожных признаков нет, датчики в норме")
    return hints


# Проверка узла


def _component_title(component_id: str) -> str:
    return COMPONENT_TITLES_RU.get(component_id, component_id)


def _true_signs(signs: dict[str, Any]) -> list[str]:
    return [
        name
        for name in SIGN_TITLES_RU
        if signs.get(name) is True
    ]


def apply_vision_diagnostic(
    equipment_id: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> str | None:
    """Создаёт/обновляет `visual_signs` и разрешает конфликт с `visual_defect`.

    Возвращает id правила-победителя, если VLM-диагностика подавлена.
    """
    moment = now or _now()
    verdict = result.get("verdict")
    existing = db.find_open_diagnostic(equipment_id, VISUAL_SIGNS_RULE_ID)

    if verdict != "defect":
        if existing is not None and verdict == "no_defect":
            # Новый чистый кадр снимает визуальную диагностику VLM.
            db.resolve_diagnostic(int(existing["id"]), now=moment)
        return None

    signs = result.get("signs") or {}
    true_signs = _true_signs(signs)
    if not true_signs:
        return None

    component_id = str(result.get("component_id") or "")
    confidence = float(result.get("confidence") or 0.0)
    model = str(result.get("model") or "")
    # VLM-проверка — такое же наблюдение окна, как кадр: признаки попадают
    # в единый реестр и сливаются с эвристиками/маркером.
    analytics.record_signs(
        equipment_id,
        component_id,
        true_signs,
        confidence=confidence,
        source="vlm",
        now=moment,
    )
    sign_stats = {
        name: analytics.sign_stability(
            equipment_id, component_id, name, now=moment
        )
        for name in true_signs
    }
    component_ru = _component_title(component_id)
    signs_ru = ", ".join(SIGN_TITLES_RU[name] for name in true_signs)
    message = (
        f"Визуальные признаки дефекта (VLM): {signs_ru} на узле {component_ru}."
    )
    recommendations: list[str] = []
    for name in true_signs:
        text = SIGN_RECOMMENDATIONS.get(name)
        if text and text not in recommendations:
            recommendations.append(text)
    recommendation = " ".join(recommendations)
    evidence: list[dict[str, Any]] = [
        {
            "condition": f"VLM: {name}",
            "signal": name,
            "op": "vision",
            "actual": True,
            "matched": True,
            "score": confidence,
            "component": component_id,
            "model": model,
        }
        for name in true_signs
    ]
    evidence.append(
        {
            "condition": "VLM confidence",
            "signal": "confidence",
            "op": "gte",
            "actual": confidence,
            "threshold": 0.5,
            "matched": confidence >= 0.5,
            "score": confidence,
            "component": component_id,
            "model": model,
        }
    )
    overlay = {
        "component_id": component_id,
        "marker": "highlight",
        "color": "orange",
        "label": "Визуальные признаки (VLM)",
    }
    severity = "warning"
    # Стабильность по окну: VLM-признак без подтверждения — info.
    stats_list = [item for item in sign_stats.values() if item is not None]
    for entry in evidence:
        stats = sign_stats.get(str(entry.get("signal")))
        if stats is not None:
            entry["sign"] = stats["sign"]
            entry["status"] = stats["status"]
            entry["presence_ratio"] = stats["presence_ratio"]
            entry["sources"] = stats["sources"]
    if stats_list and not any(item["status"] == "detected" for item in stats_list):
        severity = "info"
        overlay["color"] = "grey"
        message = (
            f"{message.rstrip()} "
            "Единичное наблюдение, требуется повторная проверка."
        )

    visual_defect = next(
        (
            row
            for row in db.get_open_diagnostics(equipment_id)
            if str(row.get("rule_id") or "").startswith("visual_defect")
            and (row.get("overlay") or {}).get("component_id")
            in {None, component_id}
        ),
        None,
    )
    if visual_defect is not None:
        defect_overlay = visual_defect.get("overlay") or {}
        same_node = defect_overlay.get("component_id") in {None, component_id}
        if same_node and float(visual_defect.get("confidence") or 0.0) >= confidence:
            # Эвристики увереннее — VLM-диагностику помечаем и закрываем.
            if existing is not None:
                db.update_diagnostic(
                    int(existing["id"]),
                    suppressed_by="visual_defect",
                    now=moment,
                )
                db.resolve_diagnostic(int(existing["id"]), now=moment)
            return "visual_defect"
        if same_node:
            # VLM увереннее — эвристическое правило помечаем и закрываем;
            # `DiagnosticsService.refresh` не даст ему воскреснуть, пока
            # открыта visual_signs.
            db.update_diagnostic(
                int(visual_defect["id"]),
                suppressed_by=VISUAL_SIGNS_RULE_ID,
                now=moment,
            )
            db.resolve_diagnostic(int(visual_defect["id"]), now=moment)

    if existing is not None:
        db.update_diagnostic(
            int(existing["id"]),
            severity=severity,
            confidence=confidence,
            evidence=evidence,
            message=message,
            recommendation=recommendation,
            overlay=overlay,
            suppressed_by=None,
            now=moment,
        )
    else:
        db.create_diagnostic(
            equipment_id=equipment_id,
            rule_id=VISUAL_SIGNS_RULE_ID,
            severity=severity,
            confidence=confidence,
            evidence=evidence,
            message=message,
            recommendation=recommendation,
            overlay=overlay,
            now=moment,
        )
    return None


def check_vision(
    equipment_id: str,
    component_id: str,
    image_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Полный цикл проверки узла: кадр → модель → диагностика → ответ API."""
    payload = image_bytes if image_bytes is not None else last_frame(equipment_id)
    if payload is None:
        settings = get_settings()
        return {
            "component_id": component_id,
            "verdict": "insufficient",
            "signs": {
                "corrosion": None,
                "paint_peeling": None,
                "oil_leak": None,
                "mechanical_damage": None,
            },
            "confidence": 0.0,
            "comment": "",
            "surface": "",
            "model": settings.ollama_model,
            "latency_ms": 0,
            "grounded": True,
            "error": "no_frame",
            "cached": False,
            "suppressed_by": None,
            "checked_at": _iso(),
        }

    hypotheses = build_hypotheses(equipment_id, component_id)
    result = client.check_component(payload, component_id, hypotheses)
    data = result.to_dict()

    if data["error"] is None:
        data["suppressed_by"] = apply_vision_diagnostic(equipment_id, data)
        data["hypotheses"] = hypotheses
        with _state_lock:
            _last_results[equipment_id] = dict(data)

    return data


# Объяснение по фактам


def _component_facts(equipment_id: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for component in db.get_components(equipment_id):
        resource = compute_remaining_resource(
            component.get("normative_life_years"),
            component.get("installed_date"),
            component.get("condition"),
        )
        facts.append(
            {
                "id": component.get("name"),
                "название": _component_title(str(component.get("name"))),
                "состояние": component.get("condition"),
                "нормативный_срок_лет": component.get("normative_life_years"),
                "установлен": component.get("installed_date"),
                "остаточный_ресурс_лет": resource.get("value"),
                "ресурс_статус": resource.get("status"),
            }
        )
    return facts


def _diagnostic_facts(equipment_id: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for row in db.get_open_diagnostics(equipment_id):
        evidence = row.get("evidence") or []
        short_evidence = [
            {
                "условие": item.get("condition"),
                "факт": item.get("actual"),
                "норма": item.get("baseline"),
                "порог": item.get("threshold"),
            }
            for item in evidence[:4]
            if isinstance(item, dict)
        ]
        facts.append(
            {
                "rule_id": row.get("rule_id"),
                "severity": row.get("severity"),
                "confidence": row.get("confidence"),
                "message": row.get("message"),
                "recommendation": row.get("recommendation"),
                "suppressed_by": row.get("suppressed_by"),
                "evidence": short_evidence,
            }
        )
    return facts


TELEMETRY_FACTS: tuple[str, ...] = (
    "vibration_rms",
    "vibration_hf",
    "vibration_lf_1x",
    "vibration_lf_2x",
    "temperature_bearing",
    "temperature_motor",
    "current_a",
    "current_b",
    "current_c",
    "pressure_inlet",
    "pressure_outlet",
    "flow",
    "efficiency",
    "oil_level",
    "oil_color",
    "noise_db",
)


def collect_facts(equipment_id: str) -> dict[str, Any]:
    """Только факты: телеметрия, диагностики с evidence, карточка, ресурс, VLM."""
    equipment = db.get_equipment(equipment_id) or {}
    latest = db.get_latest_telemetry(equipment_id) or {}
    signals = latest.get("signals") or {}
    telemetry = {
        name: signals.get(name)
        for name in TELEMETRY_FACTS
        if signals.get(name) is not None
    }
    scenario = db.get_scenario_state(equipment_id) or {}
    vision = latest_vision(equipment_id)
    return {
        "объект": {
            "id": equipment.get("id"),
            "название": equipment.get("name"),
            "модель": equipment.get("model"),
            "заводской_номер": equipment.get("serial"),
            "год": equipment.get("year"),
            "наработка_ч": equipment.get("runtime_hours"),
            "срок_службы_лет": equipment.get("normative_life_years"),
            "последнее_ТО": equipment.get("last_service_date"),
            "сценарий": scenario.get("scenario"),
            "телеметрия": telemetry,
            "телеметрия_время": latest.get("ts"),
        },
        "узлы": _component_facts(equipment_id),
        "активные_диагностики": _diagnostic_facts(equipment_id),
        "визуальная_проверка": (
            {
                "узел": vision.get("component_id"),
                "вердикт": vision.get("verdict"),
                "признаки": vision.get("signs"),
                "уверенность": vision.get("confidence"),
                "комментарий": vision.get("comment"),
                "время": vision.get("checked_at"),
            }
            if vision
            else None
        ),
    }


def _fallback_explanation(
    facts: dict[str, Any],
) -> tuple[str, str, list[str], list[str]]:
    """Детерминированное заключение, если модель недоступна/ответила невалидно."""
    equipment = facts.get("объект") or {}
    name = equipment.get("название") or equipment.get("id") or "объект"
    equipment_id = equipment.get("id") or ""
    diagnostics = facts.get("активные_диагностики") or []
    vision = facts.get("визуальная_проверка") or {}

    worst_node = None
    worst_value = None
    for node in facts.get("узлы") or []:
        value = node.get("остаточный_ресурс_лет")
        if isinstance(value, (int, float)) and (worst_value is None or value < worst_value):
            worst_value = float(value)
            worst_node = node

    first = f"Объект {name} ({equipment_id})"
    if equipment.get("наработка_ч") is not None:
        first += f", наработка {equipment['наработка_ч']} ч"
    if worst_node is not None and worst_value is not None:
        first += (
            f"; минимальный остаточный ресурс — у узла "
            f"{worst_node.get('название')} ({worst_value:g} лет)"
        )
    first += "."

    if diagnostics:
        titles = "; ".join(
            f"{item.get('rule_id')} (confidence {item.get('confidence')})"
            for item in diagnostics[:4]
        )
        second = f"Активные диагностики: {titles}."
    else:
        second = "Активных диагностик нет, правила молчат."

    if vision:
        third = (
            f"Последняя визуальная проверка узла {vision.get('узел')}: "
            f"{vision.get('вердикт')}, уверенность {vision.get('уверенность')}."
        )
    else:
        third = "Визуальная проверка узла ещё не выполнялась."

    recommendations: list[str] = []
    for item in diagnostics:
        text = item.get("recommendation")
        if text and text not in recommendations:
            recommendations.append(str(text))
    actions = recommendations[:4] or ["Продолжать плановый контроль и наблюдение."]
    fourth = f"Рекомендация: {actions[0]}"

    severity = "info"
    if any(item.get("severity") == "critical" for item in diagnostics):
        severity = "critical"
    elif diagnostics or vision.get("вердикт") == "defect":
        severity = "warning"

    return " ".join([first, second, third, fourth]), severity, actions, [
        "объект",
        "узлы",
        "активные_диагностики",
        "визуальная_проверка",
    ]


def explain_equipment(equipment_id: str) -> dict[str, Any]:
    """Итоговое заключение по объекту: модель или детерминированный fallback."""
    settings = get_settings()
    facts = collect_facts(equipment_id)
    payload, latency_ms, error = client.explain_facts(facts)

    parsed_as_text = False
    if error:
        summary, severity, actions, facts_used = _fallback_explanation(facts)
    else:
        # Модель иногда отвечает связным текстом вместо JSON: клиент отдаёт
        # такой ответ как summary и помечает его. Флаг нужен UI, чтобы не
        # выдавать свободный текст за структурированное заключение.
        parsed_as_text = bool(payload.get("parsed_as_text"))
        summary = payload["summary"]
        severity = payload["severity"]
        actions = payload["actions"]
        facts_used = payload["facts_used"]
        # Модель не может «понизить» severity относительно фактов.
        facts_severity = "info"
        if any(
            item.get("severity") == "critical"
            for item in facts.get("активные_диагностики") or []
        ):
            facts_severity = "critical"
        elif facts.get("активные_диагностики") or (
            (facts.get("визуальная_проверка") or {}).get("вердикт") == "defect"
        ):
            facts_severity = "warning"
        rank = {"info": 0, "warning": 1, "critical": 2}
        if rank[severity] < rank[facts_severity]:
            severity = facts_severity

    result = {
        "summary": summary,
        "severity": severity,
        "actions": actions,
        "facts_used": facts_used,
        "model": settings.ollama_model,
        "latency_ms": int(latency_ms),
        "error": error or None,
    }
    if parsed_as_text:
        result["parsed_as_text"] = True
    return result


def vision_status() -> dict[str, Any]:
    return client.ollama_status()


__all__ = [
    "RULE_HINTS",
    "SIGN_TITLES_RU",
    "TELEMETRY_FACTS",
    "VISUAL_SIGNS_RULE_ID",
    "VISUAL_SIGNS_TITLE",
    "apply_vision_diagnostic",
    "build_hypotheses",
    "check_vision",
    "clear_vision_state",
    "collect_facts",
    "explain_equipment",
    "last_frame",
    "latest_vision",
    "remember_frame",
    "vision_status",
]
