"""Pydantic-схемы запросов API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .cv.detector import CLASS_IDS, CLASS_KINDS, CLASS_NAMES


class DetectionIn(BaseModel):
    """Детекция, которую клиент присылает при сохранении осмотра."""

    class_id: int = Field(ge=0, lt=len(CLASS_NAMES))
    class_name: str
    kind: Literal["component", "defect"]
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: list[float] = Field(min_length=4, max_length=4)

    @field_validator("class_name")
    @classmethod
    def class_name_is_known(cls, value: str) -> str:
        """Не принимаем классы вне фиксированного списка."""
        if value not in CLASS_NAMES:
            raise ValueError(f"неизвестный класс: {value}")
        return value

    @field_validator("bbox")
    @classmethod
    def bbox_is_normalized(cls, value: list[float]) -> list[float]:
        """Проверяет координаты 0..1 и корректный порядок углов."""
        if any(coord < 0.0 or coord > 1.0 for coord in value):
            raise ValueError("координаты bbox должны быть в диапазоне 0..1")
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("ожидается bbox [x1, y1, x2, y2] с x1 < x2 и y1 < y2")
        return value

    @model_validator(mode="after")
    def class_fields_match(self) -> "DetectionIn":
        """Не даёт разойтись class_id, class_name и kind."""
        if CLASS_IDS[self.class_name] != self.class_id:
            raise ValueError(
                f"class_id={self.class_id} не соответствует class_name={self.class_name}"
            )
        if CLASS_KINDS[self.class_id] != self.kind:
            raise ValueError(
                f"kind={self.kind} не соответствует классу {self.class_name}"
            )
        return self


class InspectionCreate(BaseModel):
    """Тело POST /inspections."""

    equipment_id: str
    detections: list[DetectionIn] = Field(default_factory=list)
    frame_id: int | None = None
    image_width: int | None = Field(default=None, gt=0)
    image_height: int | None = Field(default=None, gt=0)
    inference_ms: int | None = Field(default=None, ge=0)
    # Журнал событий осмотра с телефона (время, кадр, тип, сообщение).
    events: list[dict] = Field(default_factory=list)


class ScenarioRequest(BaseModel):
    """Тело POST /sim/scenario."""

    equipment_id: str
    scenario: str = Field(min_length=3, max_length=64)


class RuntimeRequest(BaseModel):
    """Тело POST /sim/runtime: ручная наработка для демонстрации ТО."""

    equipment_id: str
    runtime_hours: float = Field(ge=0, le=1_000_000)


class ResetRequest(BaseModel):
    """Тело POST /sim/reset; без equipment_id сбрасываются все агрегаты."""

    equipment_id: str | None = None
    clear_history: bool = False


class LayoutSelectRequest(BaseModel):
    """Тело POST /pipeline/layout: путь к layout.json для горячей подмены.

    Относительный путь резолвится от корня проекта (там, где лежит `backend/`),
    а не от текущей рабочей директории процесса.

    `defects` необязателен: `true/false` включает или выключает визуальные
    эвристики `cv_classic.defects` на лету; без поля сохраняется текущее
    значение (из `CV_DEFECTS` или прошлого переключения).
    """

    path: str = Field(min_length=1, max_length=1024)
    defects: bool | None = None

