"""Свежие наблюдения компьютерного зрения (CV) для движка правил.

Правила с CV-условием срабатывают только по «свежему» кадру: если последнее
наблюдение старше `CV_TTL_SEC` (по умолчанию 5 с), канал считается недоступным
и правило возвращает `blocked_by: camera_unavailable`.

Хранилище сознательно in-memory: поток кадров не пишется целиком в БД, в
`inspection_detections` сохраняются только осмотры.

Кадры камеры (`source != "simulator"`) и синтетические подсказки сценариев
(`source == "simulator"`) лежат в отдельных слотах и сливаются только при
чтении (`snapshot`):

* для одного класса и узла побеждает наблюдение с большей уверенностью, поэтому
  реальная детекция камеры имеет приоритет, но не гасит сценарную подсказку,
  если уверенность у той выше;
* классы, которые камера не наблюдает, остаются из сценария и живут по общему
  TTL (`CV_TTL_SEC`), а не обновляются каждым кадром.

Здесь же лежит последнее OCR-значение манометра (`publish_ocr` / `ocr_snapshot`):
правило `gauge_drift` берёт его, если сценарий симулятора не задаёт
`manometer_ocr` сам. TTL тот же (`CV_TTL_SEC`).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from .config import get_settings
from .cv.detector import Detection

_BBox = list[float]


@dataclass(frozen=True)
class OcrReading:
    """Свежее OCR-значение аналогового прибора (манометра) с камеры."""

    value: float | None
    unit: str = "бар"
    confidence: float = 0.0
    error: str | None = None
    angle_deg: float | None = None
    source: str = "board"
    observed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": None if self.value is None else round(float(self.value), 3),
            "unit": self.unit,
            "confidence": round(float(self.confidence), 4),
            "error": self.error,
            "angle_deg": None if self.angle_deg is None else round(float(self.angle_deg), 2),
            "source": self.source,
            "observed_at": (
                self.observed_at.isoformat(timespec="seconds")
                if self.observed_at
                else None
            ),
        }


@dataclass(frozen=True)
class CvObservation:
    """Один визуальный признак: класс, уверенность, bbox и привязка к узлу."""

    class_name: str
    confidence: float
    bbox: _BBox | None = None
    component_id: str | None = None
    label: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_name": self.class_name,
            "confidence": round(float(self.confidence), 4),
            "bbox": self.bbox,
            "component_id": self.component_id,
            "label": self.label,
            "meta": self.meta,
        }


@dataclass(frozen=True)
class CvSnapshot:
    """Состояние CV-канала на момент оценки правил."""

    available: bool
    observed_at: datetime | None
    observations: tuple[CvObservation, ...] = ()
    source: str | None = None
    ocr: dict[str, Any] | None = None

    def best(
        self,
        class_name: str,
        min_confidence: float = 0.0,
        component_id: str | None = None,
    ) -> CvObservation | None:
        """Самое уверенное наблюдение класса (опционально — на узле)."""
        candidates = [
            item
            for item in self.observations
            if item.class_name == class_name
            and item.confidence >= min_confidence
            and (component_id is None or item.component_id == component_id)
        ]
        return max(candidates, key=lambda item: item.confidence, default=None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "observed_at": (
                self.observed_at.isoformat(timespec="seconds")
                if self.observed_at
                else None
            ),
            "source": self.source,
            "observations": [item.to_dict() for item in self.observations],
            "ocr": self.ocr,
        }


@dataclass
class _Frame:
    observed_at: datetime
    observations: tuple[CvObservation, ...]
    source: str


_lock = threading.Lock()
_frames: dict[str, _Frame] = {}
_scenario_frames: dict[str, _Frame] = {}
_ocr: dict[str, OcrReading] = {}


def _ensure_utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def publish(
    equipment_id: str,
    observations: Sequence[CvObservation],
    now: datetime | None = None,
    source: str = "camera",
) -> None:
    """Публикует кадр/наблюдение, заменяя предыдущее в своём слоте.

    Камера и симулятор пишут в разные слоты: поток кадров не должен затирать
    сценарные подсказки, которые сливаются при чтении (`snapshot`).
    """
    moment = _ensure_utc(now)
    frame = _Frame(
        observed_at=moment,
        observations=tuple(observations),
        source=source,
    )
    with _lock:
        if source == "simulator":
            _scenario_frames[equipment_id] = frame
        else:
            _frames[equipment_id] = frame


def publish_detections(
    equipment_id: str,
    detections: Sequence[Detection],
    now: datetime | None = None,
    source: str = "camera",
    signs: Sequence[dict[str, Any]] = (),
) -> None:
    """Преобразует детекции `/ws/frame` в наблюдения CV-канала.

    Для дефектов определяется ближайший узел: дефект считается относящимся к
    компоненту, если его bbox пересекается с bbox узла или центр внутри.
    `signs` — явные визуальные признаки (маркер/VLM) из реестра.
    """
    components = [item for item in detections if item.kind == "component"]
    component_boxes = {item.class_name: item.bbox for item in components}
    observations: list[CvObservation] = []
    for item in detections:
        component_id = item.class_name if item.kind == "component" else None
        if item.kind == "defect":
            if item.component_id:
                component_id = item.component_id
            else:
                parent = _find_parent(item, components)
                component_id = parent.class_name if parent else None
        meta: dict[str, Any] = {"kind": item.kind}
        if item.evidence:
            # Объяснение эвристики (score/порог/площадь/признаки) — для правила
            # visual_defect и для evidence в диагностике.
            meta["evidence"] = dict(item.evidence)
        observations.append(
            CvObservation(
                class_name=item.class_name,
                confidence=item.confidence,
                bbox=[round(float(value), 4) for value in item.bbox],
                component_id=component_id,
                label=item.class_name,
                meta=meta,
            )
        )
    for sign in signs:
        if not bool(sign.get("present", True)):
            continue
        sign_id = str(sign.get("sign") or "")
        component_id = sign.get("component_id")
        if not sign_id or not component_id:
            continue
        observations.append(
            CvObservation(
                class_name=sign_id,
                confidence=float(sign.get("confidence") or 0.0),
                bbox=[round(float(value), 4) for value in component_boxes.get(
                    str(component_id), [0.0, 0.0, 1.0, 1.0]
                )],
                component_id=str(component_id),
                label=sign_id,
                meta={
                    "kind": "sign",
                    "source": str(sign.get("source") or "marker"),
                    "sign": sign_id,
                    "evidence": {
                        key: sign.get(key)
                        for key in (
                            "score",
                            "area",
                            "area_pct",
                            "threshold",
                            "is_anomaly",
                            "boxes",
                        )
                        if sign.get(key) is not None
                    },
                },
            )
        )
    publish(equipment_id, observations, now=now, source=source)


def _find_parent(item: Detection, components: Sequence[Detection]) -> Detection | None:
    """Узел для дефекта без явной привязки — по максимальному IoU.

    Только пересечение bbox недостаточно: bbox фланца может целиком лежать
    внутри bbox насоса, и «самый уверенный» кандидат оказывался не тем.
    """
    from .cv.rules import center_inside, iou

    best: Detection | None = None
    best_key = 0.0
    for component in components:
        overlap = iou(item.bbox, component.bbox)
        inside = center_inside(item.bbox, component.bbox)
        if overlap <= 0.0 and not inside:
            continue
        key = overlap + (1.0 if inside else 0.0)
        if best is None or key > best_key:
            best = component
            best_key = key
    return best


def _fresh(frame: _Frame | None, moment: datetime, ttl: float) -> _Frame | None:
    """Кадр, если он не старше TTL."""
    if frame is None:
        return None
    if (moment - frame.observed_at).total_seconds() > ttl:
        return None
    return frame


def _merge(
    camera: _Frame | None,
    scenario: _Frame | None,
) -> list[CvObservation]:
    """Слияние слоёв: ключ (класс, узел), побеждает большая уверенность.

    Камера идёт первой, поэтому при равной уверенности остаётся её детекция.
    Классы, которых нет в кадре камеры, приходят из сценария как есть.
    """
    best: dict[tuple[str, str], CvObservation] = {}
    for frame in (camera, scenario):
        if frame is None:
            continue
        for observation in frame.observations:
            key = (observation.class_name, observation.component_id or "")
            current = best.get(key)
            if current is None or observation.confidence > current.confidence:
                best[key] = observation
    return list(best.values())


def snapshot(
    equipment_id: str,
    now: datetime | None = None,
    ttl_sec: float | None = None,
) -> CvSnapshot:
    """Возвращает CV-снимок; `available=False`, если данных нет или они стары.

    Свежие кадры камеры и свежие подсказки сценария сливаются (см. `_merge`):
    сценарное наблюдение остаётся, пока его не перекрыл тот же класс с более
    высокой уверенностью.
    """
    moment = _ensure_utc(now)
    ttl = get_settings().cv_ttl_sec if ttl_sec is None else ttl_sec
    with _lock:
        camera = _frames.get(equipment_id)
        scenario = _scenario_frames.get(equipment_id)
    ocr = ocr_snapshot(equipment_id, now=moment, ttl_sec=ttl)

    fresh_camera = _fresh(camera, moment, ttl)
    fresh_scenario = _fresh(scenario, moment, ttl)
    if fresh_camera is None and fresh_scenario is None:
        stale = camera or scenario
        if stale is None:
            return CvSnapshot(available=False, observed_at=None, ocr=ocr)
        return CvSnapshot(
            available=False,
            observed_at=stale.observed_at,
            observations=stale.observations,
            source=stale.source,
            ocr=ocr,
        )

    fresh = [frame for frame in (fresh_camera, fresh_scenario) if frame is not None]
    return CvSnapshot(
        available=True,
        observed_at=max(frame.observed_at for frame in fresh),
        observations=tuple(_merge(fresh_camera, fresh_scenario)),
        source=fresh_camera.source if fresh_camera is not None else fresh_scenario.source,
        ocr=ocr,
    )


def latest_observations(equipment_id: str) -> list[dict[str, Any]]:
    """Последние наблюдения без проверки TTL (для отладки/ответа API)."""
    with _lock:
        camera = _frames.get(equipment_id)
        scenario = _scenario_frames.get(equipment_id)
    return [item.to_dict() for item in _merge(camera, scenario)]


# OCR аналогового прибора (манометра)


def publish_ocr(
    equipment_id: str,
    value: float | None,
    unit: str = "бар",
    confidence: float = 0.0,
    error: str | None = None,
    angle_deg: float | None = None,
    source: str = "board",
    now: datetime | None = None,
) -> OcrReading:
    """Публикует OCR-значение манометра (перезаписывает предыдущее).

    Правило `gauge_drift` берёт значение отсюда, если в телеметрии нет
    собственного `manometer_ocr` (то есть сценарий симулятора его не задаёт).
    """
    reading = OcrReading(
        value=None if value is None else float(value),
        unit=unit,
        confidence=float(confidence),
        error=error,
        angle_deg=angle_deg,
        source=source,
        observed_at=_ensure_utc(now),
    )
    with _lock:
        _ocr[equipment_id] = reading
    return reading


def ocr_snapshot(
    equipment_id: str,
    now: datetime | None = None,
    ttl_sec: float | None = None,
) -> dict[str, Any] | None:
    """OCR-значение, если оно свежее (тот же TTL, что и у кадров)."""
    moment = _ensure_utc(now)
    ttl = get_settings().cv_ttl_sec if ttl_sec is None else ttl_sec
    with _lock:
        reading = _ocr.get(equipment_id)
    if reading is None or reading.observed_at is None:
        return None
    age = (moment - reading.observed_at).total_seconds()
    if age > ttl:
        payload = reading.to_dict()
        payload["stale"] = True
        payload["age_sec"] = round(age, 2)
        return payload
    payload = reading.to_dict()
    payload["age_sec"] = round(max(0.0, age), 2)
    return payload


def ocr_value(
    equipment_id: str,
    now: datetime | None = None,
    ttl_sec: float | None = None,
) -> float | None:
    """Только число (для движка правил); None, если значения нет/оно устарело."""
    payload = ocr_snapshot(equipment_id, now=now, ttl_sec=ttl_sec)
    if not payload or payload.get("stale") or payload.get("value") is None:
        return None
    return float(payload["value"])


def clear(equipment_id: str | None = None) -> None:
    """Сбрасывает CV-канал (reset сценария, остановка камеры)."""
    with _lock:
        if equipment_id is None:
            _frames.clear()
            _scenario_frames.clear()
            _ocr.clear()
        else:
            _frames.pop(equipment_id, None)
            _scenario_frames.pop(equipment_id, None)
            _ocr.pop(equipment_id, None)


def age_seconds(equipment_id: str, now: datetime | None = None) -> float | None:
    """Возраст самого свежего наблюдения — для диагностических ответов."""
    moment = _ensure_utc(now)
    with _lock:
        frames = [
            frame
            for frame in (_frames.get(equipment_id), _scenario_frames.get(equipment_id))
            if frame is not None
        ]
    if not frames:
        return None
    return max(0.0, min((moment - frame.observed_at).total_seconds() for frame in frames))


__all__ = [
    "CvObservation",
    "CvSnapshot",
    "OcrReading",
    "age_seconds",
    "clear",
    "latest_observations",
    "ocr_snapshot",
    "ocr_value",
    "publish",
    "publish_detections",
    "publish_ocr",
    "snapshot",
]
