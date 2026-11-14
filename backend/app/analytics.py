"""Аккумулятор наблюдений: решение по окну, а не по одному кадру.

Хранит только агрегаты (по секундным бакетам), а не сами кадры. Каждый кадр
добавляет:

* счётчик кадров окна;
* какие узлы были видны;
* какие визуальные дефекты присутствовали, с confidence.

При чтении агрегаты суммируются по бакетам, бакеты старше `window_sec`
удаляются. Дефект считается подтверждённым, если он присутствовал в
`confirm_frames` кадрах и его `presence_ratio` не ниже `confirm_ratio`
(по умолчанию 3 кадра и 0.3; env `ANALYTICS_CONFIRM_FRAMES`,
`ANALYTICS_CONFIRM_RATIO`).

Формулы:

    visibility_ratio  = frames_seen / frames_total
    presence_ratio    = frames_present / frames_seen_component
    mean_confidence   = Σ confidence / frames_present

Потокобезопасность: один `RLock` на хранилище, обновления и чтение
сериализованы. Ключ состояния — `equipment_id`.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .config import get_settings
from .visual_signs import (
    VISUAL_SIGNS,
    heuristic_sign_for_class,
    sign_definition,
)

# Подписи узлов для отчёта/мобильного клиента.
COMPONENT_LABELS_RU: dict[str, str] = {
    "motor": "Электродвигатель",
    "pump": "Насос",
    "coupling_guard": "Кожух муфты",
    "flange": "Фланцевое соединение",
    "manometer": "Манометр",
}

# Узел считается ушедшим из кадра, если его не видно дольше этого времени.
COMPONENT_ABSENT_SEC = 5.0

DEFECT_LABELS_RU: dict[str, str] = {
    "oil_leak": "масляный подтёк",
    "corrosion": "коррозия",
    "oxidation": "окисление контактов",
    "crack": "трещина",
    "guard_damage": "повреждение кожуха",
    "texture_anomaly": "аномалия текстуры",
}


def _ensure_utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _field(item: Any, name: str, default: Any = None) -> Any:
    """Поле из dataclass или словаря (ComponentStatus / Detection / dict)."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


@dataclass
class _DefectBucket:
    """Агрегат одного класса дефекта на узле внутри секундного бакета."""

    present: int = 0
    confidence_sum: float = 0.0
    max_confidence: float = 0.0
    first_seen: datetime | None = None
    last_seen: datetime | None = None


@dataclass
class _SignBucket:
    """Агрегат одного визуального признака на узле внутри бакета."""

    present: int = 0
    confidence_sum: float = 0.0
    max_confidence: float = 0.0
    sources: set[str] = field(default_factory=set)
    details: dict[str, Any] = field(default_factory=dict)
    first_seen: datetime | None = None
    last_seen: datetime | None = None


@dataclass
class _Bucket:
    """Одна секунда окна: сколько кадров и что в них видно."""

    second: int
    frames: int = 0
    components: dict[str, int] = field(default_factory=dict)
    component_last_seen: dict[str, datetime] = field(default_factory=dict)
    defects: dict[tuple[str, str], _DefectBucket] = field(default_factory=dict)
    signs: dict[tuple[str, str], _SignBucket] = field(default_factory=dict)


@dataclass
class _State:
    """Состояние наблюдения по одному агрегату."""

    buckets: deque[_Bucket] = field(default_factory=deque)
    updated_at: datetime | None = None
    known_components: set[str] = field(default_factory=set)
    unavailable_signs: set[tuple[str, str]] = field(default_factory=set)


class AnalyticsStore:
    """Потокобезопасный аккумулятор по `equipment_id`."""

    def __init__(
        self,
        window_sec: float | None = None,
        confirm_ratio: float | None = None,
        confirm_frames: int | None = None,
    ) -> None:
        self._window_sec = window_sec
        self._confirm_ratio = confirm_ratio
        self._confirm_frames = confirm_frames
        self._lock = threading.RLock()
        self._states: dict[str, _State] = {}

    # Конфигурация

    @property
    def window_sec(self) -> float:
        value = (
            self._window_sec
            if self._window_sec is not None
            else get_settings().analytics_window_sec
        )
        return max(1.0, float(value))

    @property
    def confirm_ratio(self) -> float:
        value = (
            self._confirm_ratio
            if self._confirm_ratio is not None
            else get_settings().analytics_confirm_ratio
        )
        return max(0.0, min(1.0, float(value)))

    @property
    def confirm_frames(self) -> int:
        value = (
            self._confirm_frames
            if self._confirm_frames is not None
            else get_settings().analytics_confirm_frames
        )
        return max(1, int(value))

    def config(self) -> dict[str, Any]:
        return {
            "window_sec": self.window_sec,
            "confirm_ratio": self.confirm_ratio,
            "confirm_frames": self.confirm_frames,
        }

    # Запись

    def update(
        self,
        equipment_id: str,
        *,
        overlays: Sequence[Any] = (),
        detections: Sequence[Any] = (),
        components_status: Sequence[Any] = (),
        signs: Sequence[Any] = (),
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Добавляет один кадр (и явные признаки) и возвращает сводку окна."""
        moment = _ensure_utc(now)
        second = int(moment.timestamp())

        seen: set[str] = set()
        known: set[str] = set()
        for item in overlays:
            component_id = _field(item, "component_id")
            if component_id:
                known.add(str(component_id))
                if _field(item, "visible", False):
                    seen.add(str(component_id))
        for item in components_status:
            component_id = _field(item, "component_id")
            # bbox=None означает «узел не найден в кадре». Узел, которого нет
            # в layout, в окно не попадает; узлы layout приходят из overlays.
            if component_id and _field(item, "bbox") is not None:
                known.add(str(component_id))
                seen.add(str(component_id))

        frame_defects: dict[tuple[str, str], float] = {}
        frame_signs: dict[tuple[str, str], dict[str, Any]] = {}
        status_boxes = {
            str(_field(item, "component_id")): _field(item, "bbox")
            for item in components_status
            if _field(item, "component_id") and _field(item, "bbox") is not None
        }
        for item in detections:
            if str(_field(item, "kind", "")) != "defect":
                continue
            class_name = _field(item, "class_name")
            if not class_name:
                continue
            component_id = _field(item, "component_id")
            if not component_id:
                component_id = _owner_from_bbox(
                    _field(item, "bbox"), status_boxes
                )
            if not component_id:
                continue
            component_id = str(component_id)
            seen.add(component_id)
            known.add(component_id)
            confidence = max(0.0, float(_field(item, "confidence", 0.0) or 0.0))
            key = (component_id, str(class_name))
            frame_defects[key] = max(frame_defects.get(key, 0.0), confidence)
            sign_id = heuristic_sign_for_class(str(class_name))
            if sign_id:
                entry = frame_signs.setdefault(
                    (component_id, sign_id),
                    {"confidence": 0.0, "sources": set(), "details": {}},
                )
                entry["confidence"] = max(entry["confidence"], confidence)
                entry["sources"].add("heuristics")

        unavailable: set[tuple[str, str]] = set()
        for item in signs:
            present = bool(_field(item, "present", True))
            sign_id = str(_field(item, "sign", ""))
            if sign_definition(sign_id) is None:
                continue
            component_id = _field(item, "component_id")
            if not component_id:
                component_id = _owner_from_bbox(
                    _field(item, "bbox"), status_boxes
                )
            if not component_id:
                continue
            component_id = str(component_id)
            if not present:
                if bool(_field(item, "unavailable", False)):
                    unavailable.add((component_id, sign_id))
                continue
            seen.add(component_id)
            known.add(component_id)
            confidence = max(0.0, float(_field(item, "confidence", 0.0) or 0.0))
            source = str(_field(item, "source", "marker") or "marker")
            entry = frame_signs.setdefault(
                (component_id, sign_id),
                {"confidence": 0.0, "sources": set(), "details": {}},
            )
            entry["confidence"] = max(entry["confidence"], confidence)
            entry["sources"].add(source)
            for key in (
                "score",
                "area",
                "threshold",
                "is_anomaly",
                "latency_ms",
                "area_pct",
                "weak",
                "boxes",
            ):
                value = _field(item, key)
                if value is not None:
                    entry["details"][key] = value

        with self._lock:
            state = self._states.setdefault(equipment_id, _State())
            state.updated_at = moment
            state.known_components.update(known)
            state.unavailable_signs.update(unavailable)
            bucket = state.buckets[-1] if state.buckets and state.buckets[-1].second == second else None
            if bucket is None:
                bucket = _Bucket(second=second)
                state.buckets.append(bucket)
            bucket.frames += 1
            for component_id in seen:
                bucket.components[component_id] = bucket.components.get(component_id, 0) + 1
                bucket.component_last_seen[component_id] = moment
            for (component_id, class_name), confidence in frame_defects.items():
                aggregate = bucket.defects.get((component_id, class_name))
                if aggregate is None:
                    aggregate = _DefectBucket()
                    bucket.defects[(component_id, class_name)] = aggregate
                aggregate.present += 1
                aggregate.confidence_sum += confidence
                aggregate.max_confidence = max(aggregate.max_confidence, confidence)
                if aggregate.first_seen is None:
                    aggregate.first_seen = moment
                aggregate.last_seen = moment
            for (component_id, sign_id), payload in frame_signs.items():
                aggregate = bucket.signs.get((component_id, sign_id))
                if aggregate is None:
                    aggregate = _SignBucket()
                    bucket.signs[(component_id, sign_id)] = aggregate
                aggregate.present += 1
                aggregate.confidence_sum += float(payload["confidence"])
                aggregate.max_confidence = max(
                    aggregate.max_confidence, float(payload["confidence"])
                )
                aggregate.sources.update(payload["sources"])
                aggregate.details.update(payload.get("details") or {})
                if aggregate.first_seen is None:
                    aggregate.first_seen = moment
                aggregate.last_seen = moment
            self._evict(state, moment)
            return self._summary_locked(equipment_id, state, moment)

    # Чтение

    def summary(
        self,
        equipment_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Полная сводка по узлам, дефектам и подтверждению."""
        moment = _ensure_utc(now)
        with self._lock:
            state = self._states.get(equipment_id)
            if state is None:
                return self._empty(equipment_id)
            self._evict(state, moment)
            return self._summary_locked(equipment_id, state, moment)

    def compact(
        self,
        equipment_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Компактный поузловый блок для `/ws/frame` и `/analyze`."""
        full = self.summary(equipment_id, now=now)
        components = [
            {
                "component_id": item["component_id"],
                "label": item["label"],
                "visibility_ratio": item["visibility_ratio"],
                "frames_seen": item["frames_seen"],
                "frames_total": item["frames_total"],
                "last_seen_ago_sec": item["last_seen_ago_sec"],
                "status_window": item["status_window"],
                "confidence": item["confidence"],
                "defects": [
                    {
                        "class_name": defect["class_name"],
                        "presence_ratio": defect["presence_ratio"],
                        "mean_confidence": defect["mean_confidence"],
                        "confirmed": defect["confirmed"],
                        "first_seen_at": defect["first_seen_at"],
                        "last_seen_at": defect["last_seen_at"],
                    }
                    for defect in item["defects"]
                ],
                "signs": item["signs"],
                "note": item["note"],
            }
            for item in full["components"]
        ]
        return {
            "window_sec": full["window_sec"],
            "frames": full["frames"],
            "duration_sec": full["duration_sec"],
            "components": components,
        }

    def defect_stability(
        self,
        equipment_id: str,
        component_id: str,
        class_name: str,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Стабильность одного класса на узле (None — наблюдений не было)."""
        full = self.summary(equipment_id, now=now)
        for component in full["components"]:
            if component["component_id"] != component_id:
                continue
            for defect in component["defects"]:
                if defect["class_name"] == class_name:
                    return defect
        return None

    def sign_stability(
        self,
        equipment_id: str,
        component_id: str,
        sign: str,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Статус одного признака на узле (None — узла/признака нет)."""
        full = self.summary(equipment_id, now=now)
        for component in full["components"]:
            if component["component_id"] != component_id:
                continue
            for item in component["signs"]:
                if item["sign"] == sign:
                    return item
        return None

    def record_signs(
        self,
        equipment_id: str,
        component_id: str,
        signs: Sequence[str] | dict[str, bool],
        confidence: float = 0.0,
        source: str = "vlm",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Фиксирует VLM-наблюдение как кадр с признаками.

        VLM проверяет один узел по запросу; считаем это отдельным наблюдением,
        чтобы признаки из VLM попадали в то же окно, что эвристики/маркер.
        """
        present = (
            [name for name, value in signs.items() if value]
            if isinstance(signs, dict)
            else [str(name) for name in signs]
        )
        entries = [
            {
                "sign": name,
                "component_id": component_id,
                "confidence": float(confidence),
                "source": source,
                "present": True,
            }
            for name in present
        ]
        return self.update(
            equipment_id,
            overlays=[{"component_id": component_id, "visible": True}],
            signs=entries,
            now=now,
        )

    def reset(self, equipment_id: str | None = None) -> None:
        """Сбрасывает окно одного агрегата или все окна."""
        with self._lock:
            if equipment_id is None:
                self._states.clear()
            else:
                self._states.pop(equipment_id, None)

    # Внутреннее

    def _evict(self, state: _State, moment: datetime) -> None:
        cutoff = moment.timestamp() - self.window_sec
        while state.buckets and state.buckets[0].second <= cutoff:
            state.buckets.popleft()

    def _empty(self, equipment_id: str) -> dict[str, Any]:
        config = self.config()
        return {
            "equipment_id": equipment_id,
            **config,
            "frames": 0,
            "duration_sec": 0.0,
            "observation_started_at": None,
            "updated_at": None,
            "components": [],
            "confirmed_defects": 0,
            "unconfirmed_defects": 0,
        }

    def _summary_locked(
        self,
        equipment_id: str,
        state: _State,
        moment: datetime,
    ) -> dict[str, Any]:
        config = self.config()
        if not state.buckets:
            return self._empty(equipment_id)

        frames = sum(bucket.frames for bucket in state.buckets)
        seconds: dict[str, int] = {}
        last_seen: dict[str, datetime | None] = {}
        defects: dict[tuple[str, str], dict[str, Any]] = {}
        signs: dict[tuple[str, str], dict[str, Any]] = {}
        midpoint_ts = moment.timestamp() - self.window_sec / 2.0

        for bucket in state.buckets:
            for component_id, count in bucket.components.items():
                seconds[component_id] = seconds.get(component_id, 0) + count
            for component_id, seen_at in bucket.component_last_seen.items():
                last_seen[component_id] = _max_dt(
                    last_seen.get(component_id), seen_at
                )
            for (component_id, class_name), aggregate in bucket.defects.items():
                record = defects.setdefault(
                    (component_id, class_name),
                    {
                        "frames_present": 0,
                        "confidence_sum": 0.0,
                        "max_confidence": 0.0,
                        "first_seen_at": aggregate.first_seen,
                        "last_seen_at": aggregate.last_seen,
                    },
                )
                record["frames_present"] += aggregate.present
                record["confidence_sum"] += aggregate.confidence_sum
                record["max_confidence"] = max(
                    record["max_confidence"], aggregate.max_confidence
                )
                record["first_seen_at"] = _min_dt(
                    record["first_seen_at"], aggregate.first_seen
                )
                record["last_seen_at"] = _max_dt(
                    record["last_seen_at"], aggregate.last_seen
                )
            for (component_id, sign_id), aggregate in bucket.signs.items():
                record = signs.setdefault(
                    (component_id, sign_id),
                    {
                        "frames_present": 0,
                        "confidence_sum": 0.0,
                        "max_confidence": 0.0,
                        "sources": set(),
                        "details": {},
                        "first_seen_at": aggregate.first_seen,
                        "last_seen_at": aggregate.last_seen,
                        "first_half": 0,
                        "second_half": 0,
                    },
                )
                record["frames_present"] += aggregate.present
                record["confidence_sum"] += aggregate.confidence_sum
                record["max_confidence"] = max(
                    record["max_confidence"], aggregate.max_confidence
                )
                record["sources"].update(aggregate.sources)
                record["details"].update(aggregate.details)
                record["first_seen_at"] = _min_dt(
                    record["first_seen_at"], aggregate.first_seen
                )
                record["last_seen_at"] = _max_dt(
                    record["last_seen_at"], aggregate.last_seen
                )
                if bucket.second <= midpoint_ts:
                    record["first_half"] += aggregate.present
                else:
                    record["second_half"] += aggregate.present

        started_at = _bucket_time(state.buckets[0])
        updated_at = state.updated_at or _bucket_time(state.buckets[-1])
        components: list[dict[str, Any]] = []
        confirmed_total = 0
        unconfirmed_total = 0

        component_ids = sorted(set(state.known_components) | set(seconds))
        for component_id in component_ids:
            frames_seen = seconds.get(component_id, 0)
            visibility_ratio = round(frames_seen / frames, 4) if frames else 0.0
            last_seen_at = last_seen.get(component_id)
            last_seen_ago_sec = (
                round(max(0.0, (moment - last_seen_at).total_seconds()), 1)
                if last_seen_at is not None
                else None
            )
            items: list[dict[str, Any]] = []
            for (defect_component, class_name), record in sorted(defects.items()):
                if defect_component != component_id:
                    continue
                present = int(record["frames_present"])
                presence_ratio = (
                    round(present / frames_seen, 4) if frames_seen else 0.0
                )
                mean_confidence = (
                    round(record["confidence_sum"] / present, 4) if present else 0.0
                )
                confirmed = (
                    presence_ratio >= self.confirm_ratio
                    and present >= self.confirm_frames
                )
                item = {
                    "class_name": class_name,
                    "frames_present": present,
                    "frames_seen_component": frames_seen,
                    "presence_ratio": presence_ratio,
                    "mean_confidence": mean_confidence,
                    "max_confidence": round(float(record["max_confidence"]), 4),
                    "first_seen_at": _iso(record["first_seen_at"]),
                    "last_seen_at": _iso(record["last_seen_at"]),
                    "confirmed": confirmed,
                }
                items.append(item)
                confirmed_total += int(confirmed)
                unconfirmed_total += int(not confirmed)

            sign_items: list[dict[str, Any]] = []
            for definition in VISUAL_SIGNS:
                record = signs.get((component_id, definition.id))
                present = int(record["frames_present"]) if record else 0
                if frames_seen == 0:
                    sign_status = "not_visible"
                elif present == 0 and (
                    component_id, definition.id
                ) in state.unavailable_signs:
                    sign_status = "not_visible"
                elif present == 0:
                    sign_status = "not_detected"
                else:
                    sign_confirmed = (
                        present / frames_seen >= self.confirm_ratio
                        and present >= self.confirm_frames
                    )
                    sign_status = (
                        "detected" if sign_confirmed else "single_observation"
                    )
                area_pct = (
                    record["details"].get("area_pct") if record else None
                )
                if (
                    sign_status == "detected"
                    and definition.id == "corrosion"
                    and area_pct is not None
                    and float(area_pct) < 5.0
                ):
                    # 0.5–5 % площади — единичное наблюдение, не подтверждённый дефект.
                    sign_status = "single_observation"
                first_seen = record["first_seen_at"] if record else None
                sign_last_seen = record["last_seen_at"] if record else None
                sign_items.append(
                    {
                        "sign": definition.id,
                        "label": definition.label,
                        "status": sign_status,
                        "sources": sorted(record["sources"]) if record else [],
                        "score": (
                            record["details"].get("score") if record else None
                        ),
                        "area": (
                            record["details"].get("area") if record else None
                        ),
                        "threshold": (
                            record["details"].get("threshold") if record else None
                        ),
                        "area_pct": (
                            record["details"].get("area_pct") if record else None
                        ),
                        "confidence": (
                            round(float(record["max_confidence"]), 4)
                            if record
                            else 0.0
                        ),
                        "presence_ratio": (
                            round(present / frames_seen, 4)
                            if frames_seen
                            else 0.0
                        ),
                        "frames_present": present,
                        "frames_seen_component": frames_seen,
                        "first_seen_ago_sec": (
                            round(
                                max(0.0, (moment - first_seen).total_seconds()), 1
                            )
                            if first_seen is not None
                            else None
                        ),
                        "last_seen_ago_sec": (
                            round(
                                max(
                                    0.0,
                                    (moment - sign_last_seen).total_seconds(),
                                ),
                                1,
                            )
                            if sign_last_seen is not None
                            else None
                        ),
                        "trend": (
                            _sign_trend(
                                int(record["first_half"]),
                                int(record["second_half"]),
                            )
                            if record
                            else "stable"
                        ),
                    }
                )

            status_window, note = _component_state(
                frames_seen=frames_seen,
                frames_total=frames,
                visibility_ratio=visibility_ratio,
                last_seen_ago_sec=last_seen_ago_sec,
                defects=items,
            )
            confidence = (
                round(max(item["mean_confidence"] for item in items), 3)
                if items
                else round(visibility_ratio, 3)
            )
            components.append(
                {
                    "component_id": component_id,
                    "label": COMPONENT_LABELS_RU.get(component_id, component_id),
                    "frames_total": frames,
                    "frames_seen": frames_seen,
                    "visibility_ratio": visibility_ratio,
                    "last_seen_at": _iso(last_seen_at),
                    "last_seen_ago_sec": last_seen_ago_sec,
                    "status_window": status_window,
                    "confidence": confidence,
                    "defects": items,
                    "signs": sign_items,
                    "confirmed_defects": [item for item in items if item["confirmed"]],
                    "unconfirmed_defects": [
                        item for item in items if not item["confirmed"]
                    ],
                    "note": note,
                }
            )

        return {
            "equipment_id": equipment_id,
            **config,
            "frames": frames,
            "duration_sec": round(
                max(0.0, (updated_at - started_at).total_seconds()), 1
            ),
            "observation_started_at": _iso(started_at),
            "updated_at": _iso(updated_at),
            "components": components,
            "confirmed_defects": confirmed_total,
            "unconfirmed_defects": unconfirmed_total,
        }


def _bucket_time(bucket: _Bucket) -> datetime:
    return datetime.fromtimestamp(bucket.second, tz=timezone.utc)


def _format_sec(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _sign_trend(first_half: int, second_half: int) -> str:
    """Тренд по половинам окна: growing / stable / fading."""
    if first_half == 0 and second_half == 0:
        return "stable"
    if first_half == 0:
        return "growing"
    if second_half == 0:
        return "fading"
    if second_half > first_half * 1.2:
        return "growing"
    if second_half < first_half * 0.8:
        return "fading"
    return "stable"


def _component_state(
    *,
    frames_seen: int,
    frames_total: int,
    visibility_ratio: float,
    last_seen_ago_sec: float | None,
    defects: Sequence[dict[str, Any]],
) -> tuple[str, str]:
    """Статус узла по окну наблюдения и человекочитаемая заметка.

    Каждый узел оценивается независимо: `insufficient_data` одного не влияет
    на остальные. Порог «узел ушёл из кадра» — `COMPONENT_ABSENT_SEC` (5 с).
    """
    if frames_seen == 0:
        return "insufficient_data", "узел не встречался в окне"
    if last_seen_ago_sec is not None and last_seen_ago_sec > COMPONENT_ABSENT_SEC:
        return (
            "insufficient_data",
            f"узел вне кадра {_format_sec(last_seen_ago_sec)} с",
        )

    confirmed = [item for item in defects if item["confirmed"]]
    unconfirmed = [item for item in defects if not item["confirmed"]]
    if confirmed:
        classes = ", ".join(
            DEFECT_LABELS_RU.get(item["class_name"], item["class_name"])
            for item in confirmed
        )
        return (
            "possible_defect",
            f"виден {frames_seen} из {frames_total} кадров; подтверждено: {classes}",
        )
    if unconfirmed:
        classes = ", ".join(
            DEFECT_LABELS_RU.get(item["class_name"], item["class_name"])
            for item in unconfirmed
        )
        return (
            "check_required",
            f"виден {frames_seen} из {frames_total} кадров; "
            f"не подтверждено: {classes}",
        )
    if visibility_ratio < 0.5:
        return (
            "check_required",
            f"виден {frames_seen} из {frames_total} кадров (низкая видимость)",
        )
    return "ok", f"виден {frames_seen} из {frames_total} кадров"


def _min_dt(left: datetime | None, right: datetime | None) -> datetime | None:
    values = [value for value in (left, right) if value is not None]
    return min(values) if values else None


def _max_dt(left: datetime | None, right: datetime | None) -> datetime | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def _owner_from_bbox(
    bbox: Any,
    status_boxes: dict[str, Any],
) -> str | None:
    """Узел для дефекта без component_id — по максимальному IoU."""
    if not bbox or len(bbox) < 4:
        return None
    from .cv.rules import iou

    best: str | None = None
    best_iou = 0.0
    for component_id, box in status_boxes.items():
        if not box or len(box) < 4:
            continue
        overlap = iou(bbox, box)
        if overlap > best_iou:
            best = component_id
            best_iou = overlap
    return best


# Синглтон для приложения

_STORE_LOCK = threading.Lock()
_STORE: AnalyticsStore | None = None


def get_store() -> AnalyticsStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = AnalyticsStore()
        return _STORE


def update(
    equipment_id: str,
    *,
    overlays: Sequence[Any] = (),
    detections: Sequence[Any] = (),
    components_status: Sequence[Any] = (),
    signs: Sequence[Any] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    return get_store().update(
        equipment_id,
        overlays=overlays,
        detections=detections,
        components_status=components_status,
        signs=signs,
        now=now,
    )


def summary(equipment_id: str, now: datetime | None = None) -> dict[str, Any]:
    return get_store().summary(equipment_id, now=now)


def compact(equipment_id: str, now: datetime | None = None) -> dict[str, Any]:
    return get_store().compact(equipment_id, now=now)


def reset(equipment_id: str | None = None) -> None:
    get_store().reset(equipment_id)


def defect_stability(
    equipment_id: str,
    component_id: str,
    class_name: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    return get_store().defect_stability(
        equipment_id, component_id, class_name, now=now
    )


def sign_stability(
    equipment_id: str,
    component_id: str,
    sign: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    return get_store().sign_stability(
        equipment_id, component_id, sign, now=now
    )


def record_signs(
    equipment_id: str,
    component_id: str,
    signs: Sequence[str] | dict[str, bool],
    confidence: float = 0.0,
    source: str = "vlm",
    now: datetime | None = None,
) -> dict[str, Any]:
    return get_store().record_signs(
        equipment_id,
        component_id,
        signs,
        confidence=confidence,
        source=source,
        now=now,
    )


__all__ = [
    "AnalyticsStore",
    "COMPONENT_LABELS_RU",
    "compact",
    "defect_stability",
    "get_store",
    "record_signs",
    "reset",
    "sign_stability",
    "summary",
    "update",
]
