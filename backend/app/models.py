"""ORM-модели SQLAlchemy 2.0.

Схема переносима между PostgreSQL и SQLite:
* JSON/JSONB выбирается диалектом (`JSONB` в Postgres, `JSON` в SQLite);
* нет `AUTOINCREMENT` и SQLite-специфичных типов — только Integer/Float/
  String/Text/DateTime/JSON;
* частичный уникальный индекс (`WHERE status <> 'resolved'`) поддерживают
  и Postgres, и SQLite.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB в PostgreSQL, обычный JSON (TEXT) в SQLite для тестов.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Базовый класс моделей."""


class Equipment(Base):
    """Цифровая карточка агрегата."""

    __tablename__ = "equipment"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    serial: Mapped[str | None] = mapped_column(String(128))
    year: Mapped[int | None] = mapped_column(Integer)
    runtime_hours: Mapped[float | None] = mapped_column(Float)
    normative_life_years: Mapped[float | None] = mapped_column(Float)
    last_service_date: Mapped[str | None] = mapped_column(String(32))
    qr_code: Mapped[str | None] = mapped_column(String(128))
    location: Mapped[str | None] = mapped_column(String(255))
    # Базовая норма сигналов: к ней привязаны правила (`ref: baseline.*`).
    baseline_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)


class Component(Base):
    """Комплектующая / узел агрегата."""

    __tablename__ = "components"
    __table_args__ = (
        UniqueConstraint("equipment_id", "name", name="ux_components_equipment_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="component")
    normative_life_years: Mapped[float | None] = mapped_column(Float)
    installed_date: Mapped[str | None] = mapped_column(String(32))
    condition: Mapped[str | None] = mapped_column(String(32))


class Inspection(Base):
    """Сохранённый осмотр."""

    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    # Снимок активной диагностики на момент осмотра.
    diagnostics_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonType)
    # Сводка видеоаналитики («за N секунд наблюдения»).
    analytics_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)


class InspectionDetection(Base):
    """Детекция, приложенная к осмотру (bbox хранится в JSONB)."""

    __tablename__ = "inspection_detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inspection_id: Mapped[int] = mapped_column(
        ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False
    )
    class_name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    component_id: Mapped[str | None] = mapped_column(String(64))
    bbox_json: Mapped[list[float]] = mapped_column(JsonType, nullable=False)


class TelemetrySample(Base):
    """Один сэмпл телеметрии: все сигналы одним JSONB-объектом."""

    __tablename__ = "telemetry_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    scenario: Mapped[str] = mapped_column(String(64), nullable=False, default="normal")


class Diagnostic(Base):
    """Диагностическое событие, объединяющее датчики и CV."""

    __tablename__ = "diagnostics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    overlay: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    # Каким правилом подавлено событие (VLM ↔ эвристики), если это было.
    suppressed_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaintenanceInterval(Base):
    """Регламентный межремонтный интервал."""

    __tablename__ = "maintenance_intervals"
    __table_args__ = (
        UniqueConstraint(
            "equipment_id", "interval_hours", name="ux_maintenance_equipment_interval"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    interval_hours: Mapped[float] = mapped_column(Float, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class ScenarioState(Base):
    """Текущий сценарий симулятора для агрегата (переживает рестарт)."""

    __tablename__ = "scenario_state"

    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), primary_key=True
    )
    scenario: Mapped[str] = mapped_column(String(64), nullable=False, default="normal")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# Индексы создаются после объявления классов, чтобы ссылаться на колонки.

Index("ix_components_equipment", Component.equipment_id)
Index("ix_inspections_equipment", Inspection.equipment_id, Inspection.created_at)
Index("ix_detections_inspection", InspectionDetection.inspection_id)

# История графиков: выборка «последние N минут по агрегату» + сортировка по ts.
Index(
    "ix_telemetry_equipment_ts",
    TelemetrySample.equipment_id,
    TelemetrySample.ts.desc(),
)
# GIN по JSONB: поиск сэмплов по содержимому payload (@>).
Index(
    "ix_telemetry_payload_gin",
    TelemetrySample.payload,
    postgresql_using="gin",
    postgresql_ops={"payload": "jsonb_path_ops"},
)
Index("ix_telemetry_equipment_scenario", TelemetrySample.equipment_id, TelemetrySample.scenario)

Index("ix_diagnostics_equipment_status", Diagnostic.equipment_id, Diagnostic.status)
Index("ix_diagnostics_equipment_created", Diagnostic.equipment_id, Diagnostic.created_at.desc())
# GIN по evidence: можно искать «все события, где встречалось условие vibration_rms».
Index(
    "ix_diagnostics_evidence_gin",
    Diagnostic.evidence,
    postgresql_using="gin",
    postgresql_ops={"evidence": "jsonb_path_ops"},
)
# Одно открытое событие на правило; resolved-история не ограничивается.
Index(
    "ux_diagnostics_open_rule",
    Diagnostic.equipment_id,
    Diagnostic.rule_id,
    unique=True,
    postgresql_where=text("status <> 'resolved'"),
    sqlite_where=text("status <> 'resolved'"),
)
