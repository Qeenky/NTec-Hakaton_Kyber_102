"""Правила связи дефектов с узлами и расчёт статусов комплектующих."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .detector import CLASS_IDS, Detection

# Пороги уверенности вынесены отдельно: их удобно подбирать под модель.
DEFECT_POSSIBLE_CONFIDENCE = 0.60  # дефект достаточно уверен -> possible_defect
DEFECT_CHECK_CONFIDENCE = 0.35  # слабее, но заметен -> check_required
COMPONENT_OK_CONFIDENCE = 0.50  # узел уверенно найден и дефектов нет -> ok

Box = Sequence[float]


@dataclass
class ComponentStatus:
    """Итоговый статус комплектующей для ответа клиенту."""

    component_id: str
    status: str
    confidence: float
    defects: list[str]
    bbox: list[float] | None


def _intersection_area(a: Box, b: Box) -> float:
    """Площадь пересечения двух нормализованных боксов."""
    x1 = max(float(a[0]), float(b[0]))
    y1 = max(float(a[1]), float(b[1]))
    x2 = min(float(a[2]), float(b[2]))
    y2 = min(float(a[3]), float(b[3]))
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def iou(a: Box, b: Box) -> float:
    """Intersection over Union для двух боксов."""
    inter = _intersection_area(a, b)
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, float(a[2]) - float(a[0])) * max(0.0, float(a[3]) - float(a[1]))
    area_b = max(0.0, float(b[2]) - float(b[0])) * max(0.0, float(b[3]) - float(b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def center_inside(inner: Box, outer: Box) -> bool:
    """Проверяет, находится ли центр inner внутри outer."""
    cx = (float(inner[0]) + float(inner[2])) / 2.0
    cy = (float(inner[1]) + float(inner[3])) / 2.0
    return (
        float(outer[0]) <= cx <= float(outer[2])
        and float(outer[1]) <= cy <= float(outer[3])
    )


def defect_belongs_to_component(defect_box: Box, component_box: Box) -> bool:
    """Дефект относится к узлу, если боксы пересекаются или центр дефекта внутри."""
    return iou(defect_box, component_box) > 0.0 or center_inside(defect_box, component_box)


def _sort_key(name: str) -> tuple[int, str]:
    """Сортирует комплектующие в фиксированном порядке классов."""
    return (CLASS_IDS.get(name, len(CLASS_IDS)), name)


def _defect_owner(
    defect: Detection,
    components: Sequence[Detection],
) -> str | None:
    """Узел дефекта: явная привязка или лучший геометрический кандидат.

    Для дефекта, построенного по полигону узла, bbox совпадает с bbox этого
    узла, поэтому побеждает максимальный IoU. Прежний выбор «самый уверенный
    узел» ошибался, когда bbox фланца целиком лежит внутри bbox насоса.
    """
    if defect.component_id:
        return defect.component_id
    best_name: str | None = None
    best_key = 0.0
    for component in components:
        overlap = iou(defect.bbox, component.bbox)
        inside = center_inside(defect.bbox, component.bbox)
        if overlap <= 0.0 and not inside:
            continue
        key = overlap + (1.0 if inside else 0.0)
        if best_name is None or key > best_key:
            best_name = component.class_name
            best_key = key
    return best_name


def build_components_status(
    detections: Sequence[Detection],
    component_ids: Sequence[str],
) -> list[ComponentStatus]:
    """Считает статусы для всех комплектующих оборудования.

    `component_ids` — состав агрегата (например, из БД). Если узел не найден
    в кадре, он всё равно попадает в ответ со статусом insufficient_data.
    Каждый дефект относится ровно к одному узлу (`Detection.component_id` или
    лучший геометрический кандидат), поэтому статус не «размазывается» по
    вложенным узлам.
    """
    components = [d for d in detections if d.kind == "component"]
    defects = [d for d in detections if d.kind == "defect"]
    owners = [_defect_owner(defect, components) for defect in defects]
    result: list[ComponentStatus] = []

    for component_id in sorted(dict.fromkeys(component_ids), key=_sort_key):
        component = max(
            (d for d in components if d.class_name == component_id),
            key=lambda d: d.confidence,
            default=None,
        )
        if component is None:
            result.append(
                ComponentStatus(
                    component_id=component_id,
                    status="insufficient_data",
                    confidence=0.0,
                    defects=[],
                    bbox=None,
                )
            )
            continue

        matched_defects = [
            defect
            for defect, owner in zip(defects, owners)
            if owner == component_id
        ]
        matched_defects.sort(key=lambda d: (-d.confidence, _sort_key(d.class_name)))
        defect_names = sorted({d.class_name for d in matched_defects}, key=_sort_key)

        status = "insufficient_data"
        if matched_defects:
            best_confidence = matched_defects[0].confidence
            if best_confidence >= DEFECT_POSSIBLE_CONFIDENCE:
                status = "possible_defect"
            elif best_confidence >= DEFECT_CHECK_CONFIDENCE:
                status = "check_required"
            elif component.confidence >= COMPONENT_OK_CONFIDENCE:
                status = "ok"
        elif component.confidence >= COMPONENT_OK_CONFIDENCE:
            status = "ok"

        result.append(
            ComponentStatus(
                component_id=component_id,
                status=status,
                confidence=round(component.confidence, 3),
                defects=defect_names,
                bbox=[round(float(v), 4) for v in component.bbox],
            )
        )

    return result


# Приоритет состояний: подтверждённый дефект важнее «требует проверки», а
# «требует проверки» важнее «в норме». `insufficient_data` — не состояние
# узла, а «не видели»: его нельзя записывать в историю.
STATUS_RANK = {"ok": 0, "check_required": 1, "possible_defect": 2}


def _analytics_node_status(node: Mapping[str, Any] | None) -> str | None:
    """Статус узла по окну `analytics.summary`; None — узел не виден.

    Учитываются и подтверждённые дефекты (`status_window`), и визуальные
    признаки (коррозия/аномалия идут в `signs`, а не в `detections`).
    """
    if not node:
        return None
    if int(node.get("frames_seen") or 0) <= 0:
        return None
    window = str(node.get("status_window") or "")
    if window == "insufficient_data":
        # Узел ушёл из кадра: историю состояния не затираем.
        return None
    signs = node.get("signs") or []
    if any(item.get("status") == "detected" for item in signs):
        return "possible_defect"
    if window == "possible_defect":
        return "possible_defect"
    if any(item.get("status") == "single_observation" for item in signs):
        return "check_required"
    if window in {"check_required", "ok"}:
        return window
    return None


def merge_statuses_with_analytics(
    statuses: Sequence[ComponentStatus],
    analytics_summary: Mapping[str, Any] | None,
) -> list[ComponentStatus]:
    """Сводит статусы по детекциям с подтверждёнными признаками окна.

    Возвращает новый список: у видимых узлов состояние берётся по худшему из
    двух источников (детекции кадра и окно `analytics`), у невидимых остаётся
    `insufficient_data` — `save_inspection` такие записи пропускает и не
    затирает ранее сохранённое состояние.
    """
    nodes = {
        str(item.get("component_id")): item
        for item in (analytics_summary or {}).get("components") or []
        if item.get("component_id")
    }
    merged: list[ComponentStatus] = []
    for item in statuses:
        candidates: list[str] = []
        if item.status in STATUS_RANK:
            candidates.append(item.status)
        analytics_status = _analytics_node_status(nodes.get(item.component_id))
        if analytics_status is not None:
            candidates.append(analytics_status)
        if not candidates:
            merged.append(item)
            continue
        status = max(candidates, key=lambda value: STATUS_RANK[value])
        merged.append(item if status == item.status else replace(item, status=status))
    return merged
