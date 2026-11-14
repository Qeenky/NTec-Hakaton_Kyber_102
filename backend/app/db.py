"""Слой доступа к данным: SQLAlchemy 2.0, PostgreSQL (psycopg v3) / SQLite.

Публичные функции сохраняют прежние имена (`get_equipment`, `save_inspection`,
`list_inspections`), поэтому существующий FastAPI-код продолжает работать.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Sequence

from sqlalchemy import Engine, create_engine, event, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import BASE_DIR, get_settings
from .cv.detector import Detection
from .cv.rules import ComponentStatus
from .models import (
    Base,
    Component,
    Diagnostic,
    Equipment,
    Inspection,
    InspectionDetection,
    MaintenanceInterval,
    ScenarioState,
    TelemetrySample,
)

DB_PATH = BASE_DIR / "data" / "app.db"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


# Подключение и схема


def _build_engine(url: str) -> Engine:
    """Создаёт движок с настройками, безопасными для демо и тестов."""
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 20}
        if ":memory:" in url:
            # Одно соединение на процесс: иначе каждый коннект видит пустую БД.
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 10000")
            if ":memory:" not in url:
                cursor.execute("PRAGMA journal_mode = WAL")
            cursor.close()

    return engine


def configure(url: str | None = None, force: bool = False) -> str:
    """(Пере)настраивает подключение. Возвращает фактический URL."""
    global _engine, _session_factory
    target = url or get_settings().database_url
    if target.startswith("sqlite"):
        (BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
    current = (
        _engine.url.render_as_string(hide_password=False) if _engine is not None else None
    )
    if not force and current == target:
        return target
    if _engine is not None:
        _engine.dispose()
    _engine = _build_engine(target)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return target


def get_engine() -> Engine:
    """Движок по умолчанию (создаётся лениво из настроек)."""
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Фабрика сессий."""
    if _session_factory is None:
        configure()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Сессия на операцию: commit при успехе, rollback при ошибке."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _add_legacy_columns(engine: Engine) -> None:
    """Догоняет схему старых SQLite-баз, созданных до появления JSONB-полей."""
    inspector = inspect(engine)
    json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
    additions = {
        "equipment": {"baseline_json": json_type},
        "inspections": {
            "diagnostics_json": json_type,
            "analytics_json": json_type,
        },
        # Vision-диагностика помечает, каким правилом подавлена (VLM ↔ эвристики).
        "diagnostics": {"suppressed_by": "VARCHAR(64)"},
    }
    with engine.begin() as conn:
        for table, columns in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name in existing:
                    continue
                try:
                    conn.exec_driver_sql(
                        f'ALTER TABLE "{table}" ADD COLUMN "{name}" {sql_type}'
                    )
                except Exception as exc:  # noqa: BLE001
                    # Гонка двух стартующих процессов: колонку мог добавить
                    # соседний экземпляр. Это не ошибка схемы.
                    if "already exists" not in str(exc).lower() and "дубликат" not in str(exc).lower():
                        raise


def init_db() -> None:
    """Создаёт таблицы и индексы (идемпотентно)."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_legacy_columns(engine)


def table_counts() -> dict[str, int]:
    """Количество строк по всем таблицам — для health/скрипта инициализации."""
    counts: dict[str, int] = {}
    with get_engine().connect() as conn:
        for table in Base.metadata.tables:
            counts[table] = int(
                conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
            )
    return counts


# Сериализация


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite возвращает naive datetime; считаем его UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    """ISO-8601 в UTC для JSON-ответов."""
    normalized = as_utc(value)
    return normalized.isoformat(timespec="seconds") if normalized else None


def json_value(value: Any) -> Any:
    """Достаёт JSON из JSONB/JSON или из старого TEXT-поля."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _equipment_dict(equipment: Equipment, components_count: int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": equipment.id,
        "name": equipment.name,
        "model": equipment.model,
        "serial": equipment.serial,
        "year": equipment.year,
        "runtime_hours": equipment.runtime_hours,
        "normative_life_years": equipment.normative_life_years,
        "last_service_date": equipment.last_service_date,
        "qr_code": equipment.qr_code,
        "location": equipment.location,
        "baseline": json_value(equipment.baseline_json) or {},
    }
    if components_count is not None:
        data["components_count"] = components_count
    return data


def _component_dict(component: Component) -> dict[str, Any]:
    return {
        "id": component.id,
        "equipment_id": component.equipment_id,
        "name": component.name,
        "kind": component.kind,
        "normative_life_years": component.normative_life_years,
        "installed_date": component.installed_date,
        "condition": component.condition,
    }


def _diagnostic_dict(row: Diagnostic) -> dict[str, Any]:
    return {
        "id": row.id,
        "equipment_id": row.equipment_id,
        "rule_id": row.rule_id,
        "severity": row.severity,
        "status": row.status,
        "confidence": row.confidence,
        "evidence": json_value(row.evidence) or [],
        "message": row.message,
        "recommendation": row.recommendation,
        "overlay": json_value(row.overlay),
        "suppressed_by": row.suppressed_by,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        "resolved_at": iso(row.resolved_at),
    }


# Оборудование и комплектующие


def get_equipment(equipment_id: str) -> dict[str, Any] | None:
    """Карточка оборудования или None."""
    with session_scope() as session:
        row = session.get(Equipment, equipment_id)
        return _equipment_dict(row) if row else None


def list_equipment() -> list[dict[str, Any]]:
    """Список оборудования с количеством комплектующих."""
    with session_scope() as session:
        rows = session.execute(
            select(Equipment, func.count(Component.id))
            .outerjoin(Component, Component.equipment_id == Equipment.id)
            .group_by(Equipment.id)
            .order_by(Equipment.id)
        ).all()
        return [_equipment_dict(equipment, int(count)) for equipment, count in rows]


def get_components(equipment_id: str) -> list[dict[str, Any]]:
    """Комплектующие оборудования."""
    with session_scope() as session:
        rows = session.scalars(
            select(Component)
            .where(Component.equipment_id == equipment_id)
            .order_by(Component.id)
        ).all()
        return [_component_dict(row) for row in rows]


def update_equipment_runtime(equipment_id: str, runtime_hours: float) -> None:
    """Обновляет наработку в карточке (симулятор делает это периодически)."""
    with session_scope() as session:
        session.execute(
            update(Equipment)
            .where(Equipment.id == equipment_id)
            .values(runtime_hours=runtime_hours)
        )


# Осмотры


def save_inspection(
    equipment_id: str,
    created_at: str,
    summary: dict[str, Any],
    detections: Sequence[Detection],
    statuses: Sequence[ComponentStatus],
    diagnostics: Sequence[dict[str, Any]] | None = None,
    analytics: dict[str, Any] | None = None,
) -> int:
    """Атомарно сохраняет осмотр, детекции, снимок диагностики и статусы узлов."""
    with session_scope() as session:
        inspection = Inspection(
            equipment_id=equipment_id,
            created_at=created_at,
            summary_json=summary,
            diagnostics_json=list(diagnostics or []),
            analytics_json=analytics,
        )
        session.add(inspection)
        session.flush()
        inspection_id = inspection.id

        session.add_all(
            [
                InspectionDetection(
                    inspection_id=inspection_id,
                    class_name=detection.class_name,
                    kind=detection.kind,
                    confidence=detection.confidence,
                    component_id=(
                        detection.class_name if detection.kind == "component" else None
                    ),
                    bbox_json=[float(value) for value in detection.bbox],
                )
                for detection in detections
            ]
        )

        for item in statuses:
            if item.status == "insufficient_data":
                continue
            session.execute(
                update(Component)
                .where(
                    Component.equipment_id == equipment_id,
                    Component.name == item.component_id,
                )
                .values(condition=item.status)
            )

        return inspection_id


def list_inspections(equipment_id: str | None = None) -> list[dict[str, Any]]:
    """История осмотров, новые сверху. JSON уже разобран."""
    statement = select(Inspection)
    if equipment_id:
        statement = statement.where(Inspection.equipment_id == equipment_id)
    statement = statement.order_by(Inspection.created_at.desc(), Inspection.id.desc())

    with session_scope() as session:
        rows = session.scalars(statement).all()
        return [
            {
                "id": row.id,
                "equipment_id": row.equipment_id,
                "created_at": row.created_at,
                "summary": json_value(row.summary_json) or {},
                "diagnostics": json_value(row.diagnostics_json) or [],
                "analytics": json_value(row.analytics_json) or {},
            }
            for row in rows
        ]


# Телеметрия


def add_telemetry(
    equipment_id: str,
    ts: datetime,
    payload: dict[str, Any],
    scenario: str = "normal",
) -> None:
    """Сохраняет один сэмпл (payload — все сигналы одним объектом)."""
    with session_scope() as session:
        session.add(
            TelemetrySample(
                equipment_id=equipment_id,
                ts=as_utc(ts),
                payload=payload,
                scenario=scenario,
            )
        )


def get_telemetry(
    equipment_id: str,
    minutes: int = 30,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """История для графиков: последние `minutes` минут по возрастанию ts."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    statement = (
        select(TelemetrySample)
        .where(
            TelemetrySample.equipment_id == equipment_id,
            TelemetrySample.ts >= cutoff,
        )
        .order_by(TelemetrySample.ts.desc())
        .limit(limit)
    )
    with session_scope() as session:
        rows = list(reversed(session.scalars(statement).all()))
        return [
            {
                "ts": iso(row.ts),
                "scenario": row.scenario,
                "signals": json_value(row.payload) or {},
            }
            for row in rows
        ]


def get_recent_telemetry(
    equipment_id: str,
    seconds: int = 90,
    limit: int = 600,
) -> list[tuple[datetime, dict[str, Any]]]:
    """Точки для движка правил: (ts, signals), старые → новые."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    statement = (
        select(TelemetrySample.ts, TelemetrySample.payload)
        .where(
            TelemetrySample.equipment_id == equipment_id,
            TelemetrySample.ts >= cutoff,
        )
        .order_by(TelemetrySample.ts)
        .limit(limit)
    )
    with session_scope() as session:
        return [
            (as_utc(ts), json_value(payload) or {})
            for ts, payload in session.execute(statement).all()
        ]


def get_latest_telemetry(equipment_id: str) -> dict[str, Any] | None:
    """Последний сэмпл или None."""
    statement = (
        select(TelemetrySample)
        .where(TelemetrySample.equipment_id == equipment_id)
        .order_by(TelemetrySample.ts.desc())
        .limit(1)
    )
    with session_scope() as session:
        row = session.scalar(statement)
        if row is None:
            return None
        return {
            "ts": iso(row.ts),
            "scenario": row.scenario,
            "signals": json_value(row.payload) or {},
        }


def cleanup_telemetry(equipment_id: str, older_than_minutes: int) -> int:
    """Удаляет устаревшие сэмплы, чтобы демо-БД не росла бесконечно."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    with session_scope() as session:
        result = session.execute(
            TelemetrySample.__table__.delete().where(
                TelemetrySample.equipment_id == equipment_id,
                TelemetrySample.ts < cutoff,
            )
        )
        return int(result.rowcount or 0)


def clear_telemetry(equipment_id: str | None = None) -> int:
    """Полная очистка истории (используется reset-сценарием)."""
    with session_scope() as session:
        statement = TelemetrySample.__table__.delete()
        if equipment_id:
            statement = statement.where(TelemetrySample.equipment_id == equipment_id)
        result = session.execute(statement)
        return int(result.rowcount or 0)


# Диагностика


def get_open_diagnostics(equipment_id: str) -> list[dict[str, Any]]:
    """Активные и подтверждённые события, новые сверху."""
    statement = (
        select(Diagnostic)
        .where(
            Diagnostic.equipment_id == equipment_id,
            Diagnostic.status != "resolved",
        )
        .order_by(Diagnostic.created_at.desc(), Diagnostic.id.desc())
    )
    with session_scope() as session:
        return [_diagnostic_dict(row) for row in session.scalars(statement).all()]


def get_resolved_diagnostics(equipment_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """История закрытых событий."""
    statement = (
        select(Diagnostic)
        .where(
            Diagnostic.equipment_id == equipment_id,
            Diagnostic.status == "resolved",
        )
        .order_by(Diagnostic.resolved_at.desc(), Diagnostic.id.desc())
        .limit(limit)
    )
    with session_scope() as session:
        return [_diagnostic_dict(row) for row in session.scalars(statement).all()]


def get_diagnostic(diagnostic_id: int) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(Diagnostic, diagnostic_id)
        return _diagnostic_dict(row) if row else None


def find_open_diagnostic(equipment_id: str, rule_id: str) -> dict[str, Any] | None:
    """Открытое событие правила (active или acknowledged)."""
    statement = (
        select(Diagnostic)
        .where(
            Diagnostic.equipment_id == equipment_id,
            Diagnostic.rule_id == rule_id,
            Diagnostic.status != "resolved",
        )
        .order_by(Diagnostic.id.desc())
        .limit(1)
    )
    with session_scope() as session:
        row = session.scalar(statement)
        return _diagnostic_dict(row) if row else None


def create_diagnostic(
    equipment_id: str,
    rule_id: str,
    severity: str,
    confidence: float,
    evidence: list[dict[str, Any]],
    message: str,
    recommendation: str,
    overlay: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """Создаёт событие; при гонке возвращает уже открытое правило."""
    try:
        with session_scope() as session:
            row = Diagnostic(
                equipment_id=equipment_id,
                rule_id=rule_id,
                severity=severity,
                status="active",
                confidence=confidence,
                evidence=evidence,
                message=message,
                recommendation=recommendation,
                overlay=overlay,
                created_at=as_utc(now),
                updated_at=as_utc(now),
            )
            session.add(row)
            session.flush()
            return _diagnostic_dict(row)
    except IntegrityError:
        existing = find_open_diagnostic(equipment_id, rule_id)
        if existing is None:
            raise
        return existing


def update_diagnostic(
    diagnostic_id: int,
    *,
    severity: str | None = None,
    confidence: float | None = None,
    evidence: list[dict[str, Any]] | None = None,
    message: str | None = None,
    recommendation: str | None = None,
    overlay: dict[str, Any] | None = None,
    suppressed_by: str | None = None,
    status: str | None = None,
    resolved_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Обновляет изменяемые поля события."""
    with session_scope() as session:
        row = session.get(Diagnostic, diagnostic_id)
        if row is None:
            return None
        if severity is not None:
            row.severity = severity
        if confidence is not None:
            row.confidence = confidence
        if evidence is not None:
            row.evidence = evidence
        if message is not None:
            row.message = message
        if recommendation is not None:
            row.recommendation = recommendation
        if overlay is not None:
            row.overlay = overlay
        if suppressed_by is not None:
            row.suppressed_by = suppressed_by
        if status is not None:
            row.status = status
        if resolved_at is not None:
            row.resolved_at = as_utc(resolved_at)
        row.updated_at = as_utc(now or datetime.now(timezone.utc))
        session.flush()
        return _diagnostic_dict(row)


def ack_diagnostic(diagnostic_id: int) -> dict[str, Any] | None:
    """Подтверждает событие: active → acknowledged."""
    with session_scope() as session:
        row = session.get(Diagnostic, diagnostic_id)
        if row is None:
            return None
        if row.status == "active":
            row.status = "acknowledged"
            row.updated_at = datetime.now(timezone.utc)
        return _diagnostic_dict(row)


def resolve_diagnostic(
    diagnostic_id: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Закрывает событие вручную или движком."""
    return update_diagnostic(
        diagnostic_id,
        status="resolved",
        resolved_at=now or datetime.now(timezone.utc),
        now=now or datetime.now(timezone.utc),
    )


def resolve_open_diagnostics(
    equipment_id: str,
    rule_ids: Sequence[str] | None = None,
    now: datetime | None = None,
) -> int:
    """Закрывает открытые события (все или только указанные правила)."""
    moment = now or datetime.now(timezone.utc)
    statement = select(Diagnostic).where(
        Diagnostic.equipment_id == equipment_id,
        Diagnostic.status != "resolved",
    )
    if rule_ids:
        statement = statement.where(Diagnostic.rule_id.in_(list(rule_ids)))
    with session_scope() as session:
        rows = session.scalars(statement).all()
        for row in rows:
            row.status = "resolved"
            row.resolved_at = moment
            row.updated_at = moment
        return len(rows)


# Регламентные интервалы


def get_maintenance_intervals(equipment_id: str) -> list[dict[str, Any]]:
    statement = (
        select(MaintenanceInterval)
        .where(MaintenanceInterval.equipment_id == equipment_id)
        .order_by(MaintenanceInterval.interval_hours)
    )
    with session_scope() as session:
        rows = session.scalars(statement).all()
        return [
            {
                "id": row.id,
                "equipment_id": row.equipment_id,
                "interval_hours": row.interval_hours,
                "name": row.name,
                "description": row.description,
            }
            for row in rows
        ]


def upsert_maintenance_interval(
    equipment_id: str,
    interval_hours: float,
    name: str,
    description: str | None = None,
) -> None:
    """Идемпотентно создаёт/обновляет интервал."""
    statement = select(MaintenanceInterval).where(
        MaintenanceInterval.equipment_id == equipment_id,
        MaintenanceInterval.interval_hours == interval_hours,
    )
    with session_scope() as session:
        row = session.scalar(statement)
        if row is None:
            session.add(
                MaintenanceInterval(
                    equipment_id=equipment_id,
                    interval_hours=interval_hours,
                    name=name,
                    description=description,
                )
            )
        else:
            row.name = name
            row.description = description


# Сценарии симулятора


def get_scenario_state(equipment_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(ScenarioState, equipment_id)
        if row is None:
            return None
        return {
            "equipment_id": row.equipment_id,
            "scenario": row.scenario,
            "started_at": iso(row.started_at),
        }


def list_scenario_states() -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(select(ScenarioState).order_by(ScenarioState.equipment_id)).all()
        return [
            {
                "equipment_id": row.equipment_id,
                "scenario": row.scenario,
                "started_at": iso(row.started_at),
            }
            for row in rows
        ]


def set_scenario_state(equipment_id: str, scenario: str, started_at: datetime) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(ScenarioState, equipment_id)
        if row is None:
            row = ScenarioState(
                equipment_id=equipment_id,
                scenario=scenario,
                started_at=as_utc(started_at),
            )
            session.add(row)
        else:
            row.scenario = scenario
            row.started_at = as_utc(started_at)
        session.flush()
        return {
            "equipment_id": row.equipment_id,
            "scenario": row.scenario,
            "started_at": iso(row.started_at),
        }


__all__ = [
    "DB_PATH",
    "ack_diagnostic",
    "add_telemetry",
    "as_utc",
    "clear_telemetry",
    "cleanup_telemetry",
    "configure",
    "create_diagnostic",
    "find_open_diagnostic",
    "get_diagnostic",
    "get_engine",
    "get_equipment",
    "get_components",
    "get_latest_telemetry",
    "get_maintenance_intervals",
    "get_open_diagnostics",
    "get_recent_telemetry",
    "get_resolved_diagnostics",
    "get_scenario_state",
    "get_telemetry",
    "init_db",
    "list_equipment",
    "list_inspections",
    "list_scenario_states",
    "resolve_diagnostic",
    "resolve_open_diagnostics",
    "save_inspection",
    "session_scope",
    "set_scenario_state",
    "table_counts",
    "update_diagnostic",
    "update_equipment_runtime",
    "upsert_maintenance_interval",
]
