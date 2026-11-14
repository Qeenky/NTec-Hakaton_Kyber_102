"""Симулятор телеметрии: 1 Гц, плавный разгон сценариев, шум.

Ключевая идея демо: сигналы не «прыгают» к аварийным значениям, а ползут по
smoothstep-кривой за 15–60 секунд. Поэтому правило срабатывает не мгновенно,
а когда confidence превысит порог активации.

`build_signals` — чистая функция (без БД), её же используют тесты движка.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import cv_state, db
from .config import get_settings
from .cv_state import CvObservation
from .diagnostics.service import DiagnosticsService

_logger = logging.getLogger(__name__)

SCENARIOS: tuple[str, ...] = (
    "normal",
    "bearing_wear",
    "cavitation",
    "misalignment",
    "impeller_wear",
    "seal_leak",
    "looseness",
    "lubrication_degradation",
    "electrical_contact",
    "gauge_drift",
    "guard_removed",
)

SCENARIO_LABELS: dict[str, str] = {
    "normal": "Норма",
    "bearing_wear": "Износ подшипника",
    "cavitation": "Кавитация",
    "misalignment": "Расцентровка",
    "impeller_wear": "Износ рабочего колеса",
    "seal_leak": "Утечка через уплотнение",
    "looseness": "Ослабление крепления",
    "lubrication_degradation": "Деградация смазки",
    "electrical_contact": "Нагрев клемм",
    "gauge_drift": "Дрейф манометра",
    "guard_removed": "Кожух муфты снят",
}

# Базовые нормы для PUMP-001; карточка оборудования может их переопределить.
# Значения — демонстрационные, подобраны под пороги правил.
DEFAULT_BASELINE: dict[str, float | str] = {
    "vibration_rms": 1.0,
    "vibration_axial": 1.0,
    "vibration_hf": 0.1,
    "vibration_lf_1x": 0.8,
    "vibration_lf_2x": 0.3,
    "temperature_bearing": 50.0,
    "temperature_motor": 65.0,
    "current_a": 23.0,
    "current_b": 23.0,
    "current_c": 23.0,
    "pressure_inlet": 0.3,
    "pressure_outlet": 10.0,
    "flow": 18.0,
    "efficiency": 63.0,
    "runtime_hours": 7400.0,
    "oil_level": 72.0,
    "oil_color": "light",
    "noise_db": 74.0,
    "rpm_pump": 260.0,
    "rpm_motor": 1460.0,
}

SIGNAL_NAMES: tuple[str, ...] = (
    "vibration_rms",
    "vibration_axial",
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
    "runtime_hours",
    "oil_level",
    "oil_color",
    "noise_db",
    "rpm_pump",
    "rpm_motor",
)


@dataclass(frozen=True)
class SignalEffect:
    """Во что превращается сигнал при полном разгоне сценария."""

    mult: float = 1.0
    add: float = 0.0
    set_value: str | None = None
    set_at: float = 0.5


SCENARIO_RAMP_SEC: dict[str, float] = {
    "normal": 0.0,
    "bearing_wear": 60.0,
    "cavitation": 40.0,
    "misalignment": 40.0,
    "impeller_wear": 45.0,
    "seal_leak": 42.0,
    "looseness": 45.0,
    "lubrication_degradation": 40.0,
    "electrical_contact": 42.0,
    "gauge_drift": 35.0,
    "guard_removed": 0.0,
}

# Множители подобраны так, чтобы сигналы доходили до порогов правил
# и правило активировалось примерно на 25–50-й секунде разгона.
SCENARIO_EFFECTS: dict[str, dict[str, SignalEffect]] = {
    "normal": {},
    "bearing_wear": {
        # Высокочастотная вибрация и температура подшипника растут к порогам.
        "vibration_rms": SignalEffect(mult=2.6),
        "vibration_axial": SignalEffect(mult=1.4),
        "vibration_hf": SignalEffect(mult=40.0),
        "temperature_bearing": SignalEffect(add=40.0),
        "current_a": SignalEffect(mult=1.01),
        "current_b": SignalEffect(mult=1.01),
        "current_c": SignalEffect(mult=1.01),
    },
    "cavitation": {
        # Высокочастотная вибрация и падение давления на входе.
        "vibration_hf": SignalEffect(mult=60.0),
        "vibration_rms": SignalEffect(mult=1.9),
        "pressure_inlet": SignalEffect(mult=0.55),
        "pressure_outlet": SignalEffect(mult=0.97),
        "flow": SignalEffect(mult=0.85),
        "efficiency": SignalEffect(mult=0.9),
        "temperature_bearing": SignalEffect(add=4.0),
        "noise_db": SignalEffect(add=10.0),
    },
    "misalignment": {
        # Рост 2x-гармоники и осевой вибрации.
        "vibration_lf_2x": SignalEffect(mult=8.0),
        "vibration_axial": SignalEffect(mult=2.8),
        "vibration_lf_1x": SignalEffect(mult=1.8),
        "temperature_bearing": SignalEffect(add=4.0),
        "current_a": SignalEffect(mult=1.06),
        "current_b": SignalEffect(mult=1.06),
        "current_c": SignalEffect(mult=1.04),
    },
    "impeller_wear": {
        # Падение КПД и подачи.
        "efficiency": SignalEffect(mult=0.80),
        "flow": SignalEffect(mult=0.90),
        "pressure_outlet": SignalEffect(mult=0.92),
        "vibration_rms": SignalEffect(mult=1.05),
        "temperature_bearing": SignalEffect(add=1.5),
    },
    "seal_leak": {
        # Рост СКЗ и температуры подшипника.
        "vibration_rms": SignalEffect(mult=3.2),
        "temperature_bearing": SignalEffect(add=18.0),
        "oil_level": SignalEffect(mult=0.92),
        "flow": SignalEffect(mult=0.96),
        "efficiency": SignalEffect(mult=0.98),
    },
    "looseness": {
        # Рост 1x-гармоники.
        "vibration_lf_1x": SignalEffect(mult=4.5),
        "vibration_rms": SignalEffect(mult=1.8),
        "vibration_axial": SignalEffect(mult=1.6),
        "temperature_bearing": SignalEffect(add=3.0),
    },
    "lubrication_degradation": {
        # Рост температуры подшипника и ухудшение масла.
        "oil_color": SignalEffect(set_value="dark", set_at=0.5),
        "oil_level": SignalEffect(mult=0.35),
        "temperature_bearing": SignalEffect(add=16.0),
        "vibration_rms": SignalEffect(mult=1.2),
    },
    "electrical_contact": {
        # Рост температуры обмоток и дисбаланс токов.
        "current_a": SignalEffect(mult=1.02),
        "current_b": SignalEffect(mult=1.12),
        "current_c": SignalEffect(mult=0.97),
        "temperature_motor": SignalEffect(add=27.0),
        "temperature_bearing": SignalEffect(add=3.0),
    },
    "gauge_drift": {},
    "guard_removed": {},
}

# Относительный и абсолютный шум на 1 Гц.
NOISE_REL: dict[str, float] = {
    "vibration_rms": 0.02,
    "vibration_axial": 0.02,
    "vibration_hf": 0.02,
    "vibration_lf_1x": 0.02,
    "vibration_lf_2x": 0.02,
    "current_a": 0.008,
    "current_b": 0.008,
    "current_c": 0.008,
    "pressure_inlet": 0.01,
    "pressure_outlet": 0.01,
    "flow": 0.008,
}
NOISE_ABS: dict[str, float] = {
    "temperature_bearing": 0.25,
    "temperature_motor": 0.25,
    "efficiency": 0.3,
    "noise_db": 0.6,
    "oil_level": 0.5,
}
DIGITS: dict[str, int] = {
    "vibration_rms": 3,
    "vibration_axial": 3,
    "vibration_hf": 3,
    "vibration_lf_1x": 3,
    "vibration_lf_2x": 3,
    "temperature_bearing": 2,
    "temperature_motor": 2,
    "current_a": 3,
    "current_b": 3,
    "current_c": 3,
    "pressure_inlet": 3,
    "pressure_outlet": 3,
    "flow": 2,
    "efficiency": 2,
    "runtime_hours": 2,
    "oil_level": 1,
    "noise_db": 2,
    "rpm_pump": 0,
    "rpm_motor": 0,
    "manometer_ocr": 2,
}


def scenario_progress(scenario: str, elapsed_sec: float) -> float:
    """Smoothstep-разгон 0→1 за `SCENARIO_RAMP_SEC`."""
    ramp = SCENARIO_RAMP_SEC.get(scenario, 45.0)
    if ramp <= 0:
        return 1.0
    position = max(0.0, min(1.0, elapsed_sec / ramp))
    return position * position * (3.0 - 2.0 * position)


def build_signals(
    scenario: str,
    baseline: dict[str, Any],
    elapsed_sec: float,
    runtime_hours: float | None = None,
    rng: random.Random | None = None,
    noise: bool = True,
) -> dict[str, Any]:
    """Считает набор сигналов на момент `elapsed_sec` от старта сценария."""
    generator = rng or random.Random(0)
    merged = dict(DEFAULT_BASELINE)
    for name, value in (baseline or {}).items():
        if name in DEFAULT_BASELINE:
            merged[name] = value

    progress = scenario_progress(scenario, elapsed_sec)
    signals: dict[str, Any] = dict(merged)

    for name, effect in SCENARIO_EFFECTS.get(scenario, {}).items():
        if effect.set_value is not None:
            if progress >= effect.set_at:
                signals[name] = effect.set_value
            continue
        start = float(merged.get(name, 0.0))
        target = start * effect.mult + effect.add
        signals[name] = start + (target - start) * progress

    signals["runtime_hours"] = (
        float(runtime_hours) if runtime_hours is not None else float(merged["runtime_hours"])
    )

    # OCR манометра появляется только после начала сценария дрейфа.
    if scenario == "gauge_drift" and progress >= 0.25:
        signals["manometer_ocr"] = round(float(signals["pressure_outlet"]) * 0.78, 2)
    else:
        signals.pop("manometer_ocr", None)

    if noise:
        for name, value in list(signals.items()):
            if not isinstance(value, (int, float)) or name == "runtime_hours":
                continue
            sigma = abs(float(value)) * NOISE_REL.get(name, 0.0) + NOISE_ABS.get(name, 0.0)
            if sigma <= 0:
                continue
            signals[name] = float(value) + generator.gauss(0.0, sigma)

    for name, value in list(signals.items()):
        if isinstance(value, (int, float)) and name in DIGITS:
            signals[name] = round(float(value), DIGITS[name])

    return signals


# Синтетические визуальные признаки для сценариев


@dataclass(frozen=True)
class SimCue:
    """Что «увидела» камера в сценарии."""

    class_name: str
    confidence: float
    bbox: list[float]
    component_id: str | None = None
    label: str | None = None


def scenario_cues(scenario: str, progress: float) -> list[SimCue]:
    """Визуальные подтверждения сценария (эмуляция камеры)."""
    cues: list[SimCue] = []
    if scenario == "guard_removed":
        cues.append(
            SimCue(
                class_name="guard_damage",
                confidence=0.86,
                bbox=[0.30, 0.56, 0.62, 0.87],
                component_id="coupling_guard",
                label="кожух отсутствует",
            )
        )
    if progress < 0.12:
        return cues

    ramp = max(0.0, min(1.0, progress))
    if scenario == "seal_leak":
        cues.append(
            SimCue(
                class_name="oil_leak",
                confidence=round(0.55 + 0.30 * ramp, 3),
                bbox=[0.58, 0.50, 0.73, 0.69],
                component_id="flange",
                label="влажное пятно у вала",
            )
        )
    if scenario == "looseness":
        cues.append(
            SimCue(
                class_name="corrosion",
                confidence=round(0.55 + 0.25 * ramp, 3),
                bbox=[0.60, 0.52, 0.78, 0.72],
                component_id="flange",
                label="коррозия рамы",
            )
        )
    if scenario == "electrical_contact":
        cues.append(
            SimCue(
                class_name="corrosion",
                confidence=round(0.60 + 0.20 * ramp, 3),
                bbox=[0.14, 0.20, 0.30, 0.36],
                component_id="motor",
                label="потемнение изоляции",
            )
        )
    if scenario == "cavitation" and progress >= 0.5:
        cues.append(
            SimCue(
                class_name="cavitation",
                confidence=round(0.50 + 0.30 * ramp, 3),
                bbox=[0.55, 0.20, 0.85, 0.55],
                component_id="pump",
                label="эрозия рабочего колеса",
            )
        )
    return cues


# Хранилище подписчиков WebSocket


class TelemetryHub:
    """Рассылка снимков телеметрии подписчикам `/ws/telemetry`."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, equipment_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10)
        self._subscribers.setdefault(equipment_id, set()).add(queue)
        return queue

    def unsubscribe(self, equipment_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        group = self._subscribers.get(equipment_id)
        if group is None:
            return
        group.discard(queue)
        if not group:
            self._subscribers.pop(equipment_id, None)

    def publish(self, equipment_id: str, payload: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(equipment_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    @property
    def subscriber_count(self) -> int:
        return sum(len(group) for group in self._subscribers.values())


# Сам симулятор


@dataclass
class _EquipmentState:
    equipment_id: str
    scenario: str
    started_at: datetime
    runtime_hours: float
    rng: random.Random
    last_tick_at: datetime | None = None
    last_payload: dict[str, Any] | None = None
    ticks: int = 0


def _utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Simulator:
    """Фоновый генератор телеметрии 1 Гц для всех агрегатов."""

    def __init__(
        self,
        service: DiagnosticsService,
        tick_sec: float | None = None,
        runtime_hours_per_sec: float | None = None,
    ) -> None:
        settings = get_settings()
        self.service = service
        self.tick_sec = settings.sim_tick_sec if tick_sec is None else tick_sec
        self.runtime_hours_per_sec = (
            settings.sim_runtime_hours_per_sec
            if runtime_hours_per_sec is None
            else runtime_hours_per_sec
        )
        self.hub = TelemetryHub()
        self._states: dict[str, _EquipmentState] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._tick_lock = threading.Lock()

    # Жизненный цикл

    def start(self) -> None:
        """Загружает сохранённые сценарии и запускает фоновую задачу."""
        self._load_states()
        if self._task is None and get_settings().sim_enabled:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    def _load_states(self) -> None:
        now = _utc()
        for equipment in db.list_equipment():
            equipment_id = equipment["id"]
            stored = db.get_scenario_state(equipment_id)
            scenario = (stored or {}).get("scenario", "normal")
            if scenario not in SCENARIOS:
                scenario = "normal"
            started_at = now
            if stored and stored.get("started_at"):
                try:
                    started_at = _utc(datetime.fromisoformat(stored["started_at"]))
                except ValueError:
                    started_at = now
            runtime = equipment.get("runtime_hours")
            if runtime is None:
                runtime = float(
                    (equipment.get("baseline") or {}).get(
                        "runtime_hours", DEFAULT_BASELINE["runtime_hours"]
                    )
                )
            self._states[equipment_id] = _EquipmentState(
                equipment_id=equipment_id,
                scenario=scenario,
                started_at=started_at,
                runtime_hours=float(runtime),
                rng=random.Random(f"{equipment_id}:{now.timestamp()}"),
            )

    async def _run(self) -> None:
        """1 Гц: тик → запись → диагностика → рассылка."""
        while not self._stop_event.is_set():
            started = asyncio.get_running_loop().time()
            try:
                self.tick_once()
            except Exception:  # noqa: BLE001 - фоновый цикл не должен умирать
                _logger.exception("ошибка тика симулятора")
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.05, self.tick_sec - elapsed))

    # Сценарии

    def _state(self, equipment_id: str) -> _EquipmentState:
        state = self._states.get(equipment_id)
        if state is None:
            equipment = db.get_equipment(equipment_id)
            if equipment is None:
                raise KeyError(equipment_id)
            baseline = equipment.get("baseline") or {}
            self._states[equipment_id] = _EquipmentState(
                equipment_id=equipment_id,
                scenario="normal",
                started_at=_utc(),
                runtime_hours=float(
                    equipment.get("runtime_hours")
                    or baseline.get("runtime_hours")
                    or DEFAULT_BASELINE["runtime_hours"]
                ),
                rng=random.Random(equipment_id),
            )
            state = self._states[equipment_id]
        return state

    def set_scenario(
        self,
        equipment_id: str,
        scenario: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Переключает сценарий; CV-события прошлого сценария закрываются."""
        if scenario not in SCENARIOS:
            raise ValueError(
                f"неизвестный сценарий {scenario!r}; допустимо: {', '.join(SCENARIOS)}"
            )
        moment = _utc(now)
        state = self._state(equipment_id)
        state.scenario = scenario
        state.started_at = moment
        db.set_scenario_state(equipment_id, scenario, moment)
        cv_state.clear(equipment_id)
        # Правила, завязанные на камеру/OCR, относятся к прошлому сценарию.
        context_rules = [
            rule.id
            for rule in self.service.catalog.rules
            if rule.requires_cv or rule.requires_ocr
        ]
        db.resolve_open_diagnostics(equipment_id, rule_ids=context_rules, now=moment)
        return self.state_for(equipment_id, now=moment)

    def set_runtime(
        self,
        equipment_id: str,
        runtime_hours: float,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Задаёт наработку вручную — удобно показать maintenance_due."""
        state = self._state(equipment_id)
        state.runtime_hours = float(runtime_hours)
        db.update_equipment_runtime(equipment_id, float(runtime_hours))
        return self.state_for(equipment_id, now=_utc(now))

    def reset(
        self,
        equipment_id: str | None = None,
        clear_history: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Возвращает `normal`, закрывает диагностику, чистит CV-канал."""
        moment = _utc(now)
        targets = (
            [equipment_id]
            if equipment_id
            else [equipment["id"] for equipment in db.list_equipment()]
        )
        for target in targets:
            state = self._state(target)
            state.scenario = "normal"
            state.started_at = moment
            db.set_scenario_state(target, "normal", moment)
            cv_state.clear(target)
            db.resolve_open_diagnostics(target, now=moment)
            if clear_history:
                db.clear_telemetry(target)
        return self.state()

    # Тик

    def tick_once(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Один шаг симуляции для всех агрегатов. Возвращает payload'ы."""
        moment = _utc(now)
        payloads: list[dict[str, Any]] = []
        with self._tick_lock:
            for state in list(self._states.values()):
                payloads.append(self._tick_equipment(state, moment))
        return payloads

    def _tick_equipment(self, state: _EquipmentState, moment: datetime) -> dict[str, Any]:
        elapsed = max(0.0, (moment - state.started_at).total_seconds())
        if state.last_tick_at is not None:
            delta = max(0.0, (moment - state.last_tick_at).total_seconds())
            state.runtime_hours += delta * self.runtime_hours_per_sec
        state.last_tick_at = moment

        signals = build_signals(
            state.scenario,
            db_baseline(state.equipment_id),
            elapsed_sec=elapsed,
            runtime_hours=state.runtime_hours,
            rng=state.rng,
            noise=True,
        )

        db.add_telemetry(state.equipment_id, moment, signals, scenario=state.scenario)

        progress = scenario_progress(state.scenario, elapsed)
        cues = scenario_cues(state.scenario, progress)
        if cues:
            cv_state.publish(
                state.equipment_id,
                [
                    CvObservation(
                        class_name=cue.class_name,
                        confidence=cue.confidence,
                        bbox=cue.bbox,
                        component_id=cue.component_id,
                        label=cue.label,
                        meta={"source": "simulator"},
                    )
                    for cue in cues
                ],
                now=moment,
                source="simulator",
            )

        snapshot = self.service.refresh(state.equipment_id, now=moment)
        payload = {
            "ts": db.iso(moment),
            "equipment_id": state.equipment_id,
            "scenario": state.scenario,
            "signals": signals,
            "camera": snapshot["camera"],
            "active_diagnostics": snapshot["active"],
            "blocked_diagnostics": snapshot["blocked"],
        }
        state.last_payload = payload
        state.ticks += 1
        self.hub.publish(state.equipment_id, payload)

        if state.ticks % 60 == 0:
            db.update_equipment_runtime(state.equipment_id, state.runtime_hours)
            retention = get_settings().telemetry_retention_min
            if retention > 0:
                db.cleanup_telemetry(state.equipment_id, retention)
        return payload

    # Состояние

    def state_for(self, equipment_id: str, now: datetime | None = None) -> dict[str, Any]:
        moment = _utc(now)
        state = self._state(equipment_id)
        elapsed = max(0.0, (moment - state.started_at).total_seconds())
        snapshot = state.last_payload
        if snapshot is None:
            signals = build_signals(
                state.scenario,
                db_baseline(equipment_id),
                elapsed_sec=elapsed,
                runtime_hours=state.runtime_hours,
                rng=state.rng,
                noise=False,
            )
            snapshot = {"signals": signals, "active_diagnostics": []}
        return {
            "equipment_id": equipment_id,
            "scenario": state.scenario,
            "scenario_label": SCENARIO_LABELS.get(state.scenario, state.scenario),
            "started_at": db.iso(state.started_at),
            "elapsed_sec": round(elapsed, 2),
            "runtime_hours": round(state.runtime_hours, 2),
            "signals": snapshot.get("signals", {}),
            "active_diagnostics": snapshot.get("active_diagnostics", []),
            "camera": snapshot.get("camera"),
        }

    def state(self, now: datetime | None = None) -> dict[str, Any]:
        moment = _utc(now)
        return {
            "tick_sec": self.tick_sec,
            "runtime_hours_per_sec": self.runtime_hours_per_sec,
            "scenarios": [
                {"id": scenario, "label": SCENARIO_LABELS[scenario]}
                for scenario in SCENARIOS
            ],
            "states": [
                self.state_for(equipment_id, now=moment)
                for equipment_id in sorted(self._states)
            ],
            "ws_subscribers": self.hub.subscriber_count,
        }

    def latest_payload(self, equipment_id: str) -> dict[str, Any] | None:
        state = self._states.get(equipment_id)
        return state.last_payload if state else None


_BASELINE_CACHE: dict[str, dict[str, Any]] = {}


def db_baseline(equipment_id: str) -> dict[str, Any]:
    """Норма сигналов из карточки (с кэшем на процесс)."""
    if equipment_id not in _BASELINE_CACHE:
        equipment = db.get_equipment(equipment_id) or {}
        _BASELINE_CACHE[equipment_id] = dict(equipment.get("baseline") or {})
    return _BASELINE_CACHE[equipment_id]


def invalidate_baseline_cache() -> None:
    _BASELINE_CACHE.clear()


__all__ = [
    "DEFAULT_BASELINE",
    "SCENARIO_LABELS",
    "SCENARIO_RAMP_SEC",
    "SCENARIOS",
    "SIGNAL_NAMES",
    "SimCue",
    "Simulator",
    "TelemetryHub",
    "build_signals",
    "invalidate_baseline_cache",
    "scenario_cues",
    "scenario_progress",
]
