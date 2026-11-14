"""FastAPI-приложение: карточки, CV, телеметрия, диагностика и симулятор."""

from __future__ import annotations

import asyncio
import io
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError

from . import analytics, cv_state, db
from .config import get_settings
from .cv.detector import (
    CLASS_IDS,
    COMPONENT_CLASS_NAMES,
    Detection,
    get_detector,
)
from .cv.rules import build_components_status, merge_statuses_with_analytics
from .diagnostics.service import DiagnosticsService
from .paths import BACKEND_DIR, PROJECT_ROOT
from .resource import compute_remaining_resource
from .schemas import (
    InspectionCreate,
    LayoutSelectRequest,
    ResetRequest,
    RuntimeRequest,
    ScenarioRequest,
)
from .seed import seed_if_empty
from .simulator import SCENARIOS, Simulator
from .verdict import build_verdict
from .vision import service as vision_service


@asynccontextmanager
def _start_warmup(app: FastAPI) -> None:
    """Прогрев тяжёлых зависимостей в фоне.

    Первый кадр иначе стоит до 2 минут: torch импортируется лениво, модели
    аномалий/коррозии грузятся в обработчике кадра под локом. На защите это
    выглядит как «сервер висит», поэтому грузим заранее, вне запроса.
    """

    import threading

    def _warm() -> None:
        import logging

        # Логгер uvicorn: обычный app-логгер без handler'а не виден в консоли.
        log = logging.getLogger("uvicorn.error")
        try:
            from pathlib import Path

            from PIL import Image as _Image

            # torch + модели каналов: тот же путь, что в реальном кадре.
            pipeline = getattr(app.state, "pipeline", None)
            if pipeline is not None:
                # Пустой кадр не доходит до каналов: привязка не находит планшет
                # и process() выходит раньше. Поэтому греем настоящим планшетом.
                board_path = Path(pipeline.layout_path).with_name("board_A3.png")
                if board_path.is_file():
                    with _Image.open(board_path) as photo:
                        frame = photo.convert("RGB")
                else:  # pragma: no cover - на стенде планшет всегда есть
                    frame = _Image.new("RGB", (720, 480), (0, 0, 0))
                pipeline.process(frame, "PUMP-001")
                log.info("warmup: пайплайн прогрет (%s)", board_path.name)
        except Exception as exc:  # noqa: BLE001 - прогрев не должен мешать старту
            log.warning("warmup: пайплайн не прогрет (%s)", exc)

        try:
            from .vision.client import _generate

            _generate("ok", timeout=180.0)
            log.info("warmup: vision-модель прогрета")
        except Exception as exc:  # noqa: BLE001
            log.warning("warmup: vision не прогрет (%s)", exc)

    threading.Thread(target=_warm, name="warmup", daemon=True).start()


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Готовит БД, демо-данные, детектор/пайплайн, движок правил и симулятор."""
    db.init_db()
    seed_if_empty()
    app.state.detector = get_detector()
    # В режиме DETECTOR=board у детектора есть полный пайплайн с оверлеями и OCR.
    app.state.pipeline = getattr(app.state.detector, "pipeline", None)
    app.state.diagnostics = DiagnosticsService()
    app.state.simulator = Simulator(app.state.diagnostics)
    app.state.simulator.start()
    _start_warmup(app)
    try:
        yield
    finally:
        await app.state.simulator.stop()


app = FastAPI(
    title="NTec Industrial Diagnostics Backend",
    description=(
        "Сервер компьютерного зрения, телеметрии и диагностики "
        "для мобильного помощника инженера"
    ),
    version="0.4.0",
    lifespan=lifespan,
)

# Мобильный клиент обращается с другого origin (Capacitor/WebView/эмулятор).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _component_card(component: dict[str, Any]) -> dict[str, Any]:
    """Комплектующая + оценка остаточного ресурса."""
    return {
        "id": component["id"],
        "equipment_id": component["equipment_id"],
        "name": component["name"],
        "kind": component["kind"],
        "normative_life_years": component["normative_life_years"],
        "installed_date": component["installed_date"],
        "condition": component["condition"],
        "remaining_resource": compute_remaining_resource(
            component["normative_life_years"],
            component["installed_date"],
            component["condition"],
        ),
    }


def _equipment_card(equipment: dict[str, Any]) -> dict[str, Any]:
    """Карточка агрегата со всеми комплектующими в порядке классов."""
    components = sorted(
        db.get_components(equipment["id"]),
        key=lambda c: CLASS_IDS.get(c["name"], len(CLASS_IDS)),
    )
    return {**equipment, "components": [_component_card(c) for c in components]}


def _roster(equipment_id: str) -> list[str]:
    """Состав агрегата из БД; для неизвестного id — стандартные пять узлов."""
    components = db.get_components(equipment_id)
    return [c["name"] for c in components] or list(COMPONENT_CLASS_NAMES)


def _attach_board_identity(
    board: dict[str, Any], equipment_id: str
) -> dict[str, Any]:
    """Добавляет в блок `board` сверку планшета с запрошенным оборудованием.

    `equipment_id` в ответе равен `board_id` (идентификатору оборудования,
    напечатанному на планшете). Если планшет в кадре — другой агрегат, кадр
    всё равно анализируется по **запрошенному** оборудованию, но клиент
    получает явный флаг `equipment_mismatch=true` и подсказку `hint`.
    """
    board_id = board.get("board_id")
    mismatch = bool(board_id) and str(board_id) != str(equipment_id)
    board["equipment_id"] = board_id
    board["equipment_mismatch"] = mismatch
    board["hint"] = (
        f"в кадре другой агрегат: {board_id}" if mismatch else None
    )
    return board


def _require_equipment(equipment_id: str) -> dict[str, Any]:
    equipment = db.get_equipment(equipment_id)
    if equipment is None:
        raise HTTPException(status_code=404, detail="Оборудование не найдено")
    return equipment


@app.get("/health")
def health() -> dict[str, Any]:
    """Проверка живости сервера, БД и сквозного CV-пайплайна."""
    detector_name = (
        type(app.state.detector).__name__ if hasattr(app.state, "detector") else "none"
    )
    pipeline = getattr(app.state, "pipeline", None)
    counts = db.table_counts()
    return {
        "status": "ok",
        "version": app.version,
        "detector": detector_name,
        "detector_mode": get_settings().detector,
        "pipeline": pipeline.info()
        if pipeline is not None
        else {"enabled": False, "reason": "detector не board"},
        "database": db.get_engine().dialect.name,
        "equipment_count": len(db.list_equipment()),
        "rules_count": len(app.state.diagnostics.catalog.rules),
        "simulator": {
            "enabled": get_settings().sim_enabled,
            "states": len(app.state.simulator.state()["states"])
            if hasattr(app.state, "simulator")
            else 0,
        },
        "rows": counts,
    }


@app.get("/")
def root() -> dict[str, Any]:
    """Короткая подсказка по точкам входа."""
    return {
        "service": "NTec Industrial Diagnostics Backend",
        "docs": "/docs",
        "health": "/health",
        "rules": "/rules",
        "telemetry_ws": "/ws/telemetry?equipment_id=PUMP-001",
        "frame_ws": "/ws/frame?equipment_id=PUMP-001",
        "vision_check": "/vision/check?equipment_id=PUMP-001&component_id=pump",
        "vision_explain": "/vision/explain?equipment_id=PUMP-001",
        "vision_status": "/vision/status",
    }


@app.get("/equipment")
def equipment_list(
    qr: str | None = Query(
        default=None,
        description="Фильтр по QR-метке (совпадает с id оборудования), напр. PUMP-002",
    ),
) -> list[dict[str, Any]]:
    """Список оборудования; `?qr=PUMP-002` — поиск по QR-коду/идентификатору."""
    items = db.list_equipment()
    if qr and qr.strip():
        key = qr.strip().upper()
        items = [
            item
            for item in items
            if str(item.get("qr_code") or "").upper() == key
            or str(item.get("id") or "").upper() == key
        ]
    return items


@app.get("/equipment/{equipment_id}")
def equipment_detail(equipment_id: str) -> dict[str, Any]:
    """Карточка агрегата с комплектующими, нормами и остаточным ресурсом."""
    return _equipment_card(_require_equipment(equipment_id))


@app.get("/equipment/{equipment_id}/diagnostics")
def equipment_diagnostics(equipment_id: str) -> dict[str, Any]:
    """Активные события, история, заблокированные CV/OCR-правила."""
    _require_equipment(equipment_id)
    return app.state.diagnostics.refresh(equipment_id)


@app.get("/equipment/{equipment_id}/telemetry")
def equipment_telemetry(
    equipment_id: str,
    minutes: int = Query(default=30, ge=1, le=24 * 60),
    limit: int = Query(default=5000, ge=10, le=20000),
) -> dict[str, Any]:
    """История телеметрии для графиков (последние N минут)."""
    _require_equipment(equipment_id)
    samples = db.get_telemetry(equipment_id, minutes=minutes, limit=limit)
    scenario_row = db.get_scenario_state(equipment_id)
    signal_names = sorted(
        {name for sample in samples for name in sample["signals"].keys()}
    )
    return {
        "equipment_id": equipment_id,
        "minutes": minutes,
        "scenario": (scenario_row or {}).get("scenario", "normal"),
        "count": len(samples),
        "signals": signal_names,
        "latest": samples[-1] if samples else None,
        "samples": samples,
    }


@app.post("/diagnostics/{diagnostic_id}/ack")
def acknowledge_diagnostic(diagnostic_id: int) -> dict[str, Any]:
    """Подтвердить событие: active → acknowledged."""
    row = app.state.diagnostics.ack(diagnostic_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Диагностика не найдена")
    if row["status"] == "resolved":
        raise HTTPException(status_code=409, detail="Событие уже закрыто")
    return row


@app.get("/rules")
def rules_catalog() -> dict[str, Any]:
    """Каталог правил с человекочитаемыми условиями — материал для слайдов."""
    return app.state.diagnostics.rules_payload()


# Видеоаналитика: окно наблюдения


@app.get("/equipment/{equipment_id}/analytics")
def equipment_analytics(equipment_id: str) -> dict[str, Any]:
    """Полная сводка окна: узлы, дефекты, подтверждённые и нет."""
    _require_equipment(equipment_id)
    return analytics.summary(equipment_id)


@app.post("/analytics/reset")
def reset_analytics(
    equipment_id: str | None = Query(
        default=None, description="Сбросить окно одного агрегата (иначе все)"
    ),
) -> dict[str, Any]:
    """Очищает аккумулятор наблюдений."""
    if equipment_id:
        _require_equipment(equipment_id)
    analytics.reset(equipment_id)
    return {
        "reset": True,
        "equipment_id": equipment_id,
        "analytics": analytics.summary(equipment_id) if equipment_id else None,
    }


# Симулятор


@app.post("/sim/scenario")
async def set_scenario(payload: ScenarioRequest) -> dict[str, Any]:
    """Переключает сценарий и сразу делает один тик."""
    _require_equipment(payload.equipment_id)
    try:
        app.state.simulator.set_scenario(payload.equipment_id, payload.scenario)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await asyncio.to_thread(app.state.simulator.tick_once)
    return {"state": app.state.simulator.state_for(payload.equipment_id)}


@app.get("/sim/state")
def simulator_state() -> dict[str, Any]:
    """Текущие сценарии, сигналы и активная диагностика по всем агрегатам."""
    return app.state.simulator.state()


@app.post("/sim/reset")
async def reset_simulator(payload: ResetRequest | None = None) -> dict[str, Any]:
    """Сброс в `normal`: снимает диагностику и очищает CV-канал."""
    body = payload or ResetRequest()
    if body.equipment_id:
        _require_equipment(body.equipment_id)
    app.state.simulator.reset(
        equipment_id=body.equipment_id,
        clear_history=body.clear_history,
    )
    analytics.reset(body.equipment_id)
    await asyncio.to_thread(app.state.simulator.tick_once)
    return app.state.simulator.state()


@app.post("/sim/runtime")
async def set_runtime(payload: RuntimeRequest) -> dict[str, Any]:
    """Задаёт наработку вручную (демонстрация maintenance_due)."""
    _require_equipment(payload.equipment_id)
    app.state.simulator.set_runtime(payload.equipment_id, payload.runtime_hours)
    await asyncio.to_thread(app.state.simulator.tick_once)
    return {"state": app.state.simulator.state_for(payload.equipment_id)}


@app.post("/sim/tick")
async def force_tick() -> dict[str, Any]:
    """Один принудительный тик — удобно для тестов и отладки."""
    payloads = await asyncio.to_thread(app.state.simulator.tick_once)
    return {"payloads": payloads, "state": app.state.simulator.state()}


# Осмотры


@app.post("/inspections", status_code=201)
def create_inspection(payload: InspectionCreate) -> dict[str, Any]:
    """Сохраняет осмотр, детекции, снимок диагностики и обновляет узлы."""
    _require_equipment(payload.equipment_id)

    detections = [
        Detection(
            class_id=item.class_id,
            class_name=item.class_name,
            kind=item.kind,
            confidence=item.confidence,
            bbox=tuple(item.bbox),  # type: ignore[arg-type]
        )
        for item in payload.detections
    ]
    statuses = build_components_status(detections, _roster(payload.equipment_id))
    diagnostics_snapshot = app.state.diagnostics.refresh(payload.equipment_id)
    active_diagnostics = diagnostics_snapshot["active"]
    analytics_snapshot = analytics.summary(payload.equipment_id)
    # Состояние узла — по детекциям кадра и по подтверждённым признакам окна
    # (коррозия/аномалия приходят в signs, а не в detections). Невидимые узлы
    # остаются insufficient_data, чтобы не затирать историю.
    statuses = merge_statuses_with_analytics(statuses, analytics_snapshot)
    verdict = build_verdict(
        analytics_snapshot,
        roster=_roster(payload.equipment_id),
        diagnostics=active_diagnostics,
    )
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    summary = {
        "frame_id": payload.frame_id,
        "image_width": payload.image_width,
        "image_height": payload.image_height,
        "inference_ms": payload.inference_ms,
        "detections": [d.to_dict() for d in detections],
        "components_status": [asdict(item) for item in statuses],
        "diagnostics": active_diagnostics,
        "verdict": verdict,
        # Журнал с телефона: время, кадр, тип события. До 500 записей.
        "events": payload.events[:500],
    }
    inspection_id = db.save_inspection(
        equipment_id=payload.equipment_id,
        created_at=created_at,
        summary=summary,
        detections=detections,
        statuses=statuses,
        diagnostics=active_diagnostics,
        analytics=analytics_snapshot,
    )

    return {
        "id": inspection_id,
        "equipment_id": payload.equipment_id,
        "created_at": created_at,
        "components_status": [asdict(item) for item in statuses],
        "diagnostics": active_diagnostics,
        "analytics": analytics_snapshot,
        "verdict": verdict,
    }


@app.get("/inspections")
def inspection_history(
    equipment_id: str | None = Query(default=None, description="Фильтр по агрегату"),
) -> list[dict[str, Any]]:
    """История осмотров (новые сверху) вместе с диагностикой и вердиктом."""
    result: list[dict[str, Any]] = []
    for row in db.list_inspections(equipment_id):
        summary = row["summary"] or {}
        verdict = summary.get("verdict") or build_verdict(
            row.get("analytics") or {},
            roster=_roster(row["equipment_id"]),
            diagnostics=row["diagnostics"],
        )
        result.append(
            {
                "id": row["id"],
                "equipment_id": row["equipment_id"],
                "created_at": row["created_at"],
                "summary": summary,
                "diagnostics": row["diagnostics"],
                "analytics": row.get("analytics") or {},
                "verdict": verdict,
            }
        )
    return result


# Загрузка фото для анализа

# Лимит тела файла для POST /analyze (файл больше — 413).
MAX_ANALYZE_BYTES = 15 * 1024 * 1024
# Что принимаем: обычные JPEG и PNG (MPO — многокадровый JPEG с телефона).
ANALYZE_FORMATS = frozenset({"JPEG", "MPO", "PNG"})

_ANALYZE_READ_CHUNK = 1024 * 1024


async def _analyze_frame(
    image: Image.Image,
    equipment_id: str,
    roster: list[str] | None = None,
) -> dict[str, Any]:
    """Один кадр через общий пайплайн: `/ws/frame` и `POST /analyze`.

    Побочные эффекты те же, что у кадра из WebSocket: детекции и OCR уходят в
    `cv_state`, затем пересчитываются правила (`active_diagnostics` /
    `blocked_diagnostics`).
    """
    roster = roster if roster is not None else _roster(equipment_id)
    detector = app.state.detector
    pipeline = getattr(app.state, "pipeline", None)

    # Последний кадр доступен `/vision/check` без повторной загрузки файла.
    _remember_vision_frame(image, equipment_id)

    started = time.perf_counter()
    if pipeline is not None:
        result = await asyncio.to_thread(pipeline.process, image, equipment_id)
        detections = result.detections
        board_payload: dict[str, Any] = result.board
        overlays = result.overlays
        ocr = result.ocr
        signs = result.signs
        source = "board"
    else:
        # FakeDetector/YOLO: узлы без планшета, оверлеев и OCR нет.
        detections = await asyncio.to_thread(detector.detect, image)
        board_payload = {
            "detected": False,
            "markers": 0,
            "reprojection_error": None,
            "confidence": 0.0,
            "reason": "detector_fake",
            "detail": "активен DETECTOR=fake, AR-планшет не ищется",
            "frame_size": [image.width, image.height],
            "board_id": None,
            "identity_marker_id": None,
            "identity_detected": False,
            "auto_switched": False,
        }
        overlays = []
        ocr = cv_state.ocr_snapshot(equipment_id)
        signs = []
        source = detections[0].source if detections else "fake"

    # Сверка планшета в кадре с запрошенным оборудованием: аналитика ниже
    # ведётся по `equipment_id`, даже если планшет чужой (mismatch только флаг).
    board_payload = _attach_board_identity(board_payload, equipment_id)

    statuses = build_components_status(detections, roster)
    inference_ms = int(round((time.perf_counter() - started) * 1000))

    now = datetime.now(timezone.utc)
    # Окно наблюдения обновляем до правил: visual_defect учитывает стабильность.
    analytics.update(
        equipment_id,
        overlays=overlays,
        detections=detections,
        components_status=statuses,
        signs=signs,
        now=now,
    )
    cv_state.publish_detections(
        equipment_id, detections, signs=signs, now=now, source=source
    )
    snapshot = await asyncio.to_thread(app.state.diagnostics.refresh, equipment_id, now)

    return {
        "image_width": image.width,
        "image_height": image.height,
        "board": board_payload,
        "overlays": overlays,
        "ocr": ocr,
        "detections": [d.to_dict() for d in detections],
        "components_status": [asdict(item) for item in statuses],
        "active_diagnostics": snapshot["active"],
        "blocked_diagnostics": snapshot["blocked"],
        "normal_diagnostics": snapshot.get("normal", []),
        "vision": vision_service.latest_vision(equipment_id),
        "analytics": analytics.compact(equipment_id, now=now),
        "inference_ms": inference_ms,
    }


@app.post("/analyze")
async def analyze_photo(
    image: UploadFile = File(..., description="Фото кадра: JPEG или PNG"),
    equipment_id: str = Query(default="PUMP-001", description="Идентификатор агрегата"),
) -> dict[str, Any]:
    """Фото из галереи проходит тот же пайплайн, что и кадр `/ws/frame`.

    Файл читается в память (без временных файлов), ограничение — 15 МБ.
    Ответ повторяет структуру кадра из WebSocket.
    """
    _require_equipment(equipment_id)

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await image.read(_ANALYZE_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_ANALYZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Файл больше 15 МБ: уменьшите фото или сожмите его",
            )
        chunks.append(chunk)

    data = b"".join(chunks)
    if not data:
        raise HTTPException(
            status_code=422,
            detail="Пустой файл: ожидается изображение JPEG или PNG в поле image",
        )

    try:
        with Image.open(io.BytesIO(data)) as decoded:
            image_format = (decoded.format or "").upper()
            if image_format not in ANALYZE_FORMATS:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Формат {image_format or 'неизвестен'} не поддерживается: "
                        "нужен JPEG или PNG"
                    ),
                )
            decoded.load()
            pil_image = decoded.convert("RGB")
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Файл не является изображением или повреждён: {exc}",
        ) from exc

    payload = await _analyze_frame(pil_image, equipment_id)
    return {"equipment_id": equipment_id, **payload}


# Локальная vision-модель (Ollama)

_VISION_FRAME_SIDE = 1280


def _jpeg_bytes(image: Image.Image) -> bytes:
    """JPEG для VLM: длинную сторону уменьшаем до 1280 px (быстрее и стабильнее)."""
    frame = image.convert("RGB")
    if max(frame.size) > _VISION_FRAME_SIDE:
        frame = frame.copy()
        frame.thumbnail((_VISION_FRAME_SIDE, _VISION_FRAME_SIDE))
    buffer = io.BytesIO()
    frame.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _remember_vision_frame(image: Image.Image, equipment_id: str) -> None:
    """Последний кадр для `/vision/check` без multipart."""
    try:
        vision_service.remember_frame(equipment_id, _jpeg_bytes(image))
    except (OSError, ValueError):
        # Кадр не должен ломать основной пайплайн из-за vision.
        return


@app.post("/vision/check")
async def vision_check(
    equipment_id: str = Query(default="PUMP-001", description="Идентификатор агрегата"),
    component_id: str = Query(..., description="Узел: pump, motor, flange, ..."),
    image: UploadFile | None = File(
        default=None,
        description="Фото узла (JPEG/PNG); без файла — последний кадр /ws/frame",
    ),
) -> dict[str, Any]:
    """Проверка узла локальной VLM: признаки, вердикт и уверенность.

    Изображение необязательно: если файла нет, анализируется последний кадр,
    присланный в `/ws/frame` (или `/analyze`). Модель не заменяет правила:
    результат дополнительно создаёт диагностику `visual_signs`.
    """
    _require_equipment(equipment_id)
    roster = _roster(equipment_id)
    if component_id not in roster:
        raise HTTPException(
            status_code=422,
            detail=f"неизвестный узел {component_id!r}; допустимо: {', '.join(roster)}",
        )

    image_bytes: bytes | None = None
    if image is not None:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await image.read(_ANALYZE_READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ANALYZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Файл больше 15 МБ: уменьшите фото или сожмите его",
                )
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise HTTPException(
                status_code=422,
                detail="Пустой файл: ожидается изображение JPEG или PNG в поле image",
            )
        try:
            with Image.open(io.BytesIO(data)) as decoded:
                image_format = (decoded.format or "").upper()
                if image_format not in ANALYZE_FORMATS:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Формат {image_format or 'неизвестен'} не поддерживается: "
                            "нужен JPEG или PNG"
                        ),
                    )
                decoded.load()
                image_bytes = _jpeg_bytes(decoded.convert("RGB"))
        except HTTPException:
            raise
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
        ) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Файл не является изображением или повреждён: {exc}",
            ) from exc

    return await asyncio.to_thread(
        vision_service.check_vision,
        equipment_id,
        component_id,
        image_bytes,
    )


@app.get("/vision/explain")
async def vision_explain(
    equipment_id: str = Query(default="PUMP-001", description="Идентификатор агрегата"),
) -> dict[str, Any]:
    """Итоговое заключение по объекту только по фактам (без изображений)."""
    _require_equipment(equipment_id)
    return await asyncio.to_thread(vision_service.explain_equipment, equipment_id)


@app.get("/vision/status")
async def vision_status() -> dict[str, Any]:
    """Доступна ли локальная модель, какая и когда отвечала последний раз."""
    return await asyncio.to_thread(vision_service.vision_status)


# Горячая подмена layout AR-планшета


def _require_pipeline() -> Any:
    """Пайплайн планшета; при `DETECTOR=fake` переключать нечего."""
    pipeline = getattr(app.state, "pipeline", None)
    if pipeline is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "AR-пайплайн планшета недоступен: сервер запущен с DETECTOR=fake. "
                "Запустите с DETECTOR=board и рабочим LAYOUT_PATH"
            ),
        )
    return pipeline


def _resolve_project_path(raw: str) -> Path:
    """Путь из тела запроса: относительный — от корня проекта, не от cwd.

    Основная база — корень проекта (родитель `backend/`). Дополнительно
    пробуем `backend/`: демо-команды вида `../ar_board/out_defect/layout.json`
    написаны из `backend/`, и они должны работать при любом cwd сервера.
    """
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    attempts = [
        (PROJECT_ROOT / candidate).resolve(),
        (BACKEND_DIR / candidate).resolve(),
    ]
    for attempt in attempts:
        if attempt.is_file():
            return attempt
    # Ничего не нашли: для путей с `..` показываем базу backend/ — так ошибка
    # совпадает с тем, что пользователь набрал в демо-команде.
    return attempts[1] if candidate.parts[:1] == ("..",) else attempts[0]


@app.get("/pipeline/layout")
def pipeline_layout() -> dict[str, Any]:
    """Какой layout сейчас загружен: абсолютный путь, зоны, версия и mtime."""
    return _require_pipeline().layout_info()


@app.post("/pipeline/layout")
async def switch_pipeline_layout(payload: LayoutSelectRequest) -> dict[str, Any]:
    """Атомарно подменяет layout в работающем пайплайне (без рестарта).

    Активные WebSocket-сессии не рвутся: `BoardPipeline` берёт снимок ссылок
    под блокировкой, `Smoother` и OCR-кэш сбрасываются. Формат `/ws/frame` и
    `/analyze` не меняется — просто меняется число и состав `overlays`.
    """
    pipeline = _require_pipeline()
    resolved = _resolve_project_path(payload.path)
    if not resolved.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"layout.json не найден: {resolved}",
        )
    previous_layout = str(pipeline.layout_info().get("path") or "")
    try:
        state = await asyncio.to_thread(
            pipeline.apply_layout, resolved, payload.defects
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"layout.json не найден: {resolved}",
        ) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"layout.json невалиден: {exc}",
        ) from exc
    if str(state.get("path") or "") != previous_layout:
        # Смена планшета — новое наблюдение: старое окно не смешиваем.
        analytics.reset()
    return {"reloaded": True, **state}


# WebSocket


@app.websocket("/ws/telemetry")
async def telemetry_stream(websocket: WebSocket, equipment_id: str) -> None:
    """Стрим `{ts, signals, active_diagnostics}` раз в секунду."""
    if db.get_equipment(equipment_id) is None:
        # Сначала accept, иначе браузер/клиент увидит только HTTP 403 без кода.
        await websocket.accept()
        await websocket.close(code=1008, reason="unknown_equipment")
        return
    await websocket.accept()

    simulator = app.state.simulator
    queue = simulator.hub.subscribe(equipment_id)
    try:
        latest = simulator.latest_payload(equipment_id)
        if latest is None:
            await asyncio.to_thread(simulator.tick_once)
            latest = simulator.latest_payload(equipment_id)
        if latest is not None:
            await websocket.send_json(latest)

        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=3.0)
            except asyncio.TimeoutError:
                # Страховка, если фоновый цикл выключен (SIM_ENABLED=false).
                await asyncio.to_thread(simulator.tick_once)
                payload = simulator.latest_payload(equipment_id)
                if payload is None:
                    continue
            await websocket.send_json(payload)
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        simulator.hub.unsubscribe(equipment_id, queue)


@app.websocket("/ws/frame")
async def frame_stream(websocket: WebSocket, equipment_id: str) -> None:
    """Принимает JPEG-кадры, отдаёт оверлеи AR, OCR, детекции и диагностику."""
    if db.get_equipment(equipment_id) is None:
        # Контракт: неизвестное оборудование — закрытие 1008 (после accept,
        # чтобы клиент получил именно код, а не HTTP 403).
        await websocket.accept()
        await websocket.close(code=1008, reason="unknown_equipment")
        return
    await websocket.accept()
    roster = _roster(equipment_id)
    frame_id = 0

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")
            if not data:
                await websocket.send_json(
                    {
                        "frame_id": frame_id,
                        "equipment_id": equipment_id,
                        "error": "binary_frame_required",
                        "detail": "Отправьте JPEG как бинарное WebSocket-сообщение",
                    }
                )
                continue

            frame_id += 1
            try:
                image = Image.open(io.BytesIO(data)).convert("RGB")
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                await websocket.send_json(
                    {
                        "frame_id": frame_id,
                        "equipment_id": equipment_id,
                        "error": "invalid_image",
                        "detail": str(exc),
                        "board": {
                            "detected": False,
                            "markers": 0,
                            "confidence": 0.0,
                            "reason": "invalid_image",
                            "board_id": None,
                            "identity_marker_id": None,
                            "identity_detected": False,
                            "auto_switched": False,
                            "equipment_id": None,
                            "equipment_mismatch": False,
                            "hint": None,
                        },
                        "overlays": [],
                        "ocr": None,
                        "detections": [],
                        "components_status": [],
                        "active_diagnostics": [],
                        "blocked_diagnostics": [],
                        "vision": vision_service.latest_vision(equipment_id),
                        "analytics": analytics.compact(equipment_id),
                        "inference_ms": 0,
                    }
                )
                continue

            payload = await _analyze_frame(image, equipment_id, roster)
            await websocket.send_json(
                {
                    "frame_id": frame_id,
                    "equipment_id": equipment_id,
                    **payload,
                }
            )
    except WebSocketDisconnect:
        return
