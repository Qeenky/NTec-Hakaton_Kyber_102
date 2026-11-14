"""Сквозной CV-пайплайн: планшет → зоны → OCR → правило `gauge_drift`.

Тесты используют настоящий печатный планшет `ar_board/out/board_A3.png` и
настоящую перспективу (`cv2.warpPerspective`), поэтому проверяют не моки, а
рабочий путь: ArUco → гомография → полигоны → чтение манометра → правила.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import cv_state, db
from app.cv.detector import FakeDetector
from app.cv.pipeline import STATUS_COLORS, BoardDetector, BoardPipeline, board_confidence
from app.cv_state import CvObservation
from app.diagnostics.service import DiagnosticsService
from app.main import app
from app.simulator import DEFAULT_BASELINE, build_signals
from starlette.websockets import WebSocketDisconnect

PROJECT_DIR = Path(__file__).resolve().parents[2]
LAYOUT_PATH = PROJECT_DIR / "ar_board" / "out" / "layout.json"
BOARD_PATH = PROJECT_DIR / "ar_board" / "out" / "board_A3.png"
GAUGE_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "manometer_board.json"

pytestmark = pytest.mark.skipif(
    not (LAYOUT_PATH.is_file() and BOARD_PATH.is_file()),
    reason="нет ar_board/out/layout.json или board_A3.png",
)

EQUIPMENT = "PUMP-001"


# Кадры


@pytest.fixture(scope="module")
def board_bgr() -> np.ndarray:
    # cv2.imread не читает пути с кириллицей на Windows -> читаем байты вручную.
    raw = np.frombuffer(BOARD_PATH.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    assert image is not None
    return image


def _warp(board: np.ndarray, width: int = 1280, height: int = 720,
          inset: float = 0.05, tilt: float = 0.02) -> np.ndarray:
    """Перспектива планшета в кадре камеры (как в demo-скрипте)."""
    board_h, board_w = board.shape[:2]
    source = np.float32([[0, 0], [board_w, 0], [board_w, board_h], [0, board_h]])
    target = np.float32([
        [width * inset, height * inset],
        [width * (1 - inset), height * (inset + tilt)],
        [width * (1 - inset - tilt), height * (1 - inset)],
        [width * (inset + tilt), height * (1 - inset)],
    ])
    matrix = cv2.getPerspectiveTransform(source, target)
    frame = np.full((height, width, 3), (28, 30, 34), dtype=np.uint8)
    cv2.warpPerspective(board, matrix, (width, height), dst=frame,
                        borderMode=cv2.BORDER_TRANSPARENT)
    return frame


def _to_pil(frame_bgr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))


def _jpeg_bytes(frame_bgr: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    assert ok
    return buffer.tobytes()


@pytest.fixture(scope="module")
def board_frame(board_bgr: np.ndarray) -> np.ndarray:
    return _warp(board_bgr)


@pytest.fixture()
def pipeline() -> BoardPipeline:
    # Явные пути: тест не зависит от LAYOUT_PATH/GAUGE_CONFIG пользователя.
    return BoardPipeline(
        layout_path=str(LAYOUT_PATH),
        gauge_config_path=str(GAUGE_CONFIG),
        ocr_every=1,
    )


# Пайплайн


def test_board_detected_overlays_and_source(board_frame, pipeline) -> None:
    result = pipeline.process(_to_pil(board_frame), EQUIPMENT)

    assert result.detected is True
    assert result.board["markers"] == 4
    assert result.board["reprojection_error"] < 2.0
    assert 0.90 <= result.board["confidence"] <= 0.98

    assert len(result.overlays) == 5
    ids = {item["component_id"] for item in result.overlays}
    assert ids == {"motor", "pump", "coupling_guard", "flange", "manometer"}
    for overlay in result.overlays:
        assert overlay["visible"] is True
        assert len(overlay["polygon"]) == 4
        assert overlay["status"] in STATUS_COLORS
        assert overlay["color"] == STATUS_COLORS[overlay["status"]]
        assert overlay["label"]
        assert 0.90 <= overlay["board_confidence"] <= 0.98

    components = [d for d in result.detections if d.kind == "component"]
    assert len(components) == 5
    assert all(d.source == "board" for d in components)
    assert all(0.0 <= value <= 1.0 for d in components for value in d.bbox)
    assert all(d.confidence >= 0.90 for d in components)


def test_board_confidence_formula() -> None:
    assert board_confidence(0.0, 1.0) == 0.98
    assert board_confidence(6.0, 0.0) == 0.90
    assert board_confidence(6.0, 1.0) == 0.93


def test_frame_without_markers(pipeline) -> None:
    blank = Image.new("RGB", (1280, 720), (40, 42, 48))
    result = pipeline.process(blank, EQUIPMENT)

    assert result.detected is False
    assert result.board["reason"] == "no_markers"
    assert result.overlays == []
    assert result.detections == []


def test_partial_markers(board_bgr, pipeline) -> None:
    layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    spoiled = board_bgr.copy()
    for marker in layout["markers"]:
        if int(marker["id"]) not in {1, 2}:
            continue
        points = np.asarray(marker["corners_px"], dtype=np.float32)
        x1, y1 = points.min(axis=0).astype(int) - 30
        x2, y2 = points.max(axis=0).astype(int) + 30
        cv2.rectangle(spoiled, (x1, y1), (x2, y2), (248, 248, 250), -1)

    result = pipeline.process(_to_pil(_warp(spoiled)), EQUIPMENT)
    assert result.detected is False
    assert result.board["reason"] == "partial"
    assert result.overlays == []


def test_ocr_reads_manometer_and_publishes_state(board_frame, pipeline) -> None:
    cv_state.clear()
    result = pipeline.process(_to_pil(board_frame), EQUIPMENT)

    assert result.ocr is not None
    assert result.ocr["error"] is None
    assert result.ocr["unit"] == "бар"
    assert 7.0 <= result.ocr["value"] <= 10.0, result.ocr
    assert result.ocr["confidence"] >= 0.5

    published = cv_state.ocr_snapshot(EQUIPMENT)
    assert published is not None
    assert published["value"] == pytest.approx(result.ocr["value"])
    assert cv_state.ocr_value(EQUIPMENT) == pytest.approx(result.ocr["value"])


def test_ocr_runs_every_n_frames(board_frame) -> None:
    pipeline = BoardPipeline(
        layout_path=str(LAYOUT_PATH),
        gauge_config_path=str(GAUGE_CONFIG),
        ocr_every=3,
    )
    first = pipeline.process(_to_pil(board_frame), EQUIPMENT)
    second = pipeline.process(_to_pil(board_frame), EQUIPMENT)
    fourth = pipeline.process(_to_pil(board_frame), EQUIPMENT)
    assert first.ocr is not None and first.ocr["refreshed"] is True
    assert second.ocr is not None and second.ocr["refreshed"] is False
    assert fourth.ocr is not None


# Правило gauge_drift по OCR


def _normal_points(now: datetime, seconds: int = 60):
    points = []
    for second in range(seconds, 0, -1):
        moment = now - timedelta(seconds=second)
        signals = build_signals(
            "normal",
            DEFAULT_BASELINE,
            elapsed_sec=float(seconds - second),
            runtime_hours=7400.0,
            noise=False,
        )
        points.append((moment, signals))
    return points


def _active_ids(service: DiagnosticsService, now: datetime, points) -> set[str]:
    snapshot = service.refresh(EQUIPMENT, now=now, points=points)
    return {item["rule_id"] for item in snapshot["active"]}


def test_gauge_drift_follows_ocr(memory_db) -> None:
    now = datetime.now(timezone.utc)
    points = _normal_points(now)
    service = DiagnosticsService()
    db.set_scenario_state(EQUIPMENT, "normal", now)

    # 1) OCR совпадает с датчиком (10 бар) — правило не срабатывает.
    cv_state.publish_ocr(EQUIPMENT, 10.0, unit="бар", confidence=0.9, now=now)
    assert "gauge_drift" not in _active_ids(service, now, points)

    # 2) OCR расходится на 25 % — правило срабатывает.
    cv_state.publish_ocr(EQUIPMENT, 7.5, unit="бар", confidence=0.9, now=now)
    assert "gauge_drift" in _active_ids(service, now, points)

    row = db.find_open_diagnostic(EQUIPMENT, "gauge_drift")
    assert row is not None
    evidence = row["evidence"][0]
    assert evidence["op"] == "deviation_pct"
    assert evidence["actual"] > 20.0
    assert evidence["baseline"] == pytest.approx(10.0, rel=0.02)


def test_gauge_drift_ignores_board_scale_mismatch(memory_db) -> None:
    """Печатный планшет 8.4 бар против паспортных 10 — это не дрейф датчика."""
    now = datetime.now(timezone.utc)
    service = DiagnosticsService()
    db.set_scenario_state(EQUIPMENT, "normal", now)

    cv_state.publish_ocr(EQUIPMENT, 8.376, unit="бар", confidence=0.84, now=now)
    assert "gauge_drift" not in _active_ids(service, now, _normal_points(now))


def test_gauge_drift_blocked_without_ocr(memory_db) -> None:
    now = datetime.now(timezone.utc)
    service = DiagnosticsService()
    db.set_scenario_state(EQUIPMENT, "normal", now)
    cv_state.clear()

    snapshot = service.refresh(EQUIPMENT, now=now, points=_normal_points(now))
    assert snapshot["active"] == []
    blocked = {item["rule_id"]: item["blocked_by"] for item in snapshot["blocked"]}
    assert blocked.get("gauge_drift") == "ocr_unavailable"


def test_simulated_ocr_has_priority(memory_db) -> None:
    """Сценарий симулятора задаёт manometer_ocr — камера его не перебивает."""
    now = datetime.now(timezone.utc)
    points = _normal_points(now)
    points[-1][1]["manometer_ocr"] = 7.5  # 25 % ниже датчика (10 бар)
    cv_state.publish_ocr(EQUIPMENT, 10.0, unit="бар", confidence=0.9, now=now)
    db.set_scenario_state(EQUIPMENT, "gauge_drift", now)

    snapshot = DiagnosticsService().refresh(EQUIPMENT, now=now, points=points)
    assert "gauge_drift" in {item["rule_id"] for item in snapshot["active"]}


def test_ocr_signal_may_arrive_less_often_than_telemetry(memory_db) -> None:
    """OCR раз в N кадров/сэмплов: значение ищется в пределах CV_TTL_SEC."""
    now = datetime.now(timezone.utc)
    points = _normal_points(now)
    timestamp, signals = points[-3]  # 2 секунды назад, TTL = 5 с
    points[-3] = (timestamp, {**signals, "manometer_ocr": 7.5})
    db.set_scenario_state(EQUIPMENT, "normal", now - timedelta(seconds=30))

    snapshot = DiagnosticsService().refresh(EQUIPMENT, now=now, points=points)
    assert "gauge_drift" in {item["rule_id"] for item in snapshot["active"]}


def test_previous_scenario_ocr_does_not_leak(memory_db) -> None:
    """После смены сценария старое OCR не держит правило активным."""
    now = datetime.now(timezone.utc)
    points = _normal_points(now)
    timestamp, signals = points[-6]  # 5 секунд назад — ещё до reset
    points[-6] = (timestamp, {**signals, "manometer_ocr": 7.5})
    db.set_scenario_state(EQUIPMENT, "normal", now - timedelta(seconds=2))

    snapshot = DiagnosticsService().refresh(EQUIPMENT, now=now, points=points)
    assert "gauge_drift" not in {item["rule_id"] for item in snapshot["active"]}
    blocked = {item["rule_id"]: item["blocked_by"] for item in snapshot["blocked"]}
    assert blocked.get("gauge_drift") == "ocr_unavailable"


# FakeDetector и контракт /ws/frame


def test_fake_detector_never_emits_defects() -> None:
    detector = FakeDetector()
    frames = [
        Image.new("RGB", (320, 240), (30, 30, 30)),
        Image.new("RGB", (320, 240), (200, 200, 200)),
        Image.fromarray(np.random.default_rng(1).integers(0, 255, (240, 320, 3), np.uint8)),
    ]
    for image in frames:
        detections = detector.detect(image)
        assert detections
        assert all(item.kind == "component" for item in detections)
        assert all(item.source == "fake" for item in detections)


def test_ws_frame_contract(memory_db, board_frame) -> None:
    """`/ws/frame` отдаёт board, overlays, ocr и не ломает прежние поля."""
    with TestClient(app) as client:
        # Подменяем fake на настоящий пайплайн в уже запущенном приложении.
        app.state.detector = BoardDetector(
            BoardPipeline(
                layout_path=str(LAYOUT_PATH),
                gauge_config_path=str(GAUGE_CONFIG),
                ocr_every=1,
            )
        )
        app.state.pipeline = app.state.detector.pipeline

        with client.websocket_connect(f"/ws/frame?equipment_id={EQUIPMENT}") as socket:
            socket.send_bytes(_jpeg_bytes(board_frame))
            payload = socket.receive_json()

        assert payload["frame_id"] == 1
        assert payload["board"]["detected"] is True
        assert payload["board"]["markers"] == 4
        assert len(payload["overlays"]) == 5
        assert payload["ocr"]["value"] is not None
        assert payload["ocr"]["unit"] == "бар"
        # Прежний контракт на месте.
        assert "detections" in payload and "components_status" in payload
        assert "active_diagnostics" in payload and "blocked_diagnostics" in payload
        assert payload["detections"][0]["source"] == "board"
        assert payload["inference_ms"] >= 0

        # Кадр без маркеров: сервер отвечает board.detected=false без падения.
        blank = Image.new("RGB", (640, 480), (35, 35, 35))
        with client.websocket_connect(f"/ws/frame?equipment_id={EQUIPMENT}") as socket:
            socket.send_bytes(_jpeg_bytes(cv2.cvtColor(np.asarray(blank), cv2.COLOR_RGB2BGR)))
            payload = socket.receive_json()
        assert payload["board"]["detected"] is False
        assert payload["board"]["reason"] == "no_markers"
        assert payload["overlays"] == []


def test_fake_detector_ws_payload(memory_db) -> None:
    """DETECTOR=fake: оверлеев нет, дефектов нет, источник помечен."""
    from PIL import ImageDraw

    image = Image.new("RGB", (640, 480), (60, 64, 72))
    ImageDraw.Draw(image).rectangle((80, 80, 300, 300), fill=(90, 100, 120))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/frame?equipment_id={EQUIPMENT}") as socket:
            socket.send_bytes(buffer.getvalue())
            payload = socket.receive_json()

    assert payload["board"]["detected"] is False
    assert payload["board"]["reason"] == "detector_fake"
    assert payload["overlays"] == []
    assert all(item["kind"] == "component" for item in payload["detections"])
    assert all(item["source"] == "fake" for item in payload["detections"])


# Приоритеты камеры и сценария


def test_camera_has_priority_per_class(memory_db) -> None:
    """Камера перекрывает сценарий только по своему классу и с большей уверенностью."""
    now = datetime.now(timezone.utc)
    cv_state.clear()
    cv_state.publish(
        EQUIPMENT,
        [CvObservation(class_name="guard_damage", confidence=0.86,
                       component_id="coupling_guard")],
        now=now,
        source="simulator",
    )
    cv_state.publish(
        EQUIPMENT,
        [
            CvObservation(class_name="oil_leak", confidence=0.91,
                          component_id="flange"),
            CvObservation(class_name="guard_damage", confidence=0.50,
                          component_id="coupling_guard"),
        ],
        now=now,
        source="board",
    )

    snap = cv_state.snapshot(EQUIPMENT, now=now)
    assert snap.available is True
    # Класса нет в кадре — наблюдение сценария сохраняется.
    assert snap.best("guard_damage") is not None
    assert snap.best("guard_damage").confidence == 0.86
    # Класс есть в кадре — камера главнее.
    assert snap.best("oil_leak").confidence == 0.91
    cv_state.clear()


def test_guard_removed_survives_blank_camera_frames(memory_db) -> None:
    """5 кадров без маркеров не гасят активное правило scenario=guard_removed."""
    blank = Image.new("RGB", (640, 480), (35, 35, 35))
    frame_bytes = _jpeg_bytes(cv2.cvtColor(np.asarray(blank), cv2.COLOR_RGB2BGR))

    with TestClient(app) as client:
        app.state.detector = BoardDetector(
            BoardPipeline(
                layout_path=str(LAYOUT_PATH),
                gauge_config_path=str(GAUGE_CONFIG),
                ocr_every=1,
            )
        )
        app.state.pipeline = app.state.detector.pipeline

        response = client.post(
            "/sim/scenario",
            json={"equipment_id": EQUIPMENT, "scenario": "guard_removed"},
        )
        assert response.status_code == 200

        with client.websocket_connect(f"/ws/frame?equipment_id={EQUIPMENT}") as socket:
            for _ in range(5):
                socket.send_bytes(frame_bytes)
                payload = socket.receive_json()
                assert payload["board"]["detected"] is False
                active = {item["rule_id"] for item in payload["active_diagnostics"]}
                assert "guard_removed" in active, payload["blocked_diagnostics"]
                blocked = {
                    item["rule_id"]: item["blocked_by"]
                    for item in payload["blocked_diagnostics"]
                }
                assert blocked.get("guard_removed") is None


def test_ws_unknown_equipment_closes_1008(memory_db) -> None:
    """Неизвестный equipment_id: закрытие 1008 и для кадров, и для телеметрии."""
    with TestClient(app) as client:
        for path in (
            "/ws/frame?equipment_id=UNKNOWN-1",
            "/ws/telemetry?equipment_id=UNKNOWN-1",
        ):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect(path) as socket:
                    socket.receive_json()
            assert exc.value.code == 1008


# OCR: no_needle не роняет пайплайн


def test_no_needle_goes_to_ocr_error(board_frame, pipeline, monkeypatch) -> None:
    """Если стрелки нет, пайплайн жив, значение null, код ошибки — в ocr.error."""
    import app.cv.pipeline as pipeline_module
    from cv_classic import GaugeReading

    cv_state.clear()

    def fake_read_gauge(crop, config):  # noqa: ANN001, ARG001
        return GaugeReading(
            value=None,
            unit="бар",
            angle_deg=None,
            confidence=0.0,
            method="no_needle",
            error="no_needle",
        )

    monkeypatch.setattr(pipeline_module, "read_gauge", fake_read_gauge)
    result = pipeline.process(_to_pil(board_frame), EQUIPMENT)

    assert result.detected is True
    assert result.ocr is not None
    assert result.ocr["value"] is None
    assert result.ocr["error"] == "no_needle"
    published = cv_state.ocr_snapshot(EQUIPMENT)
    assert published is not None
    assert published["error"] == "no_needle"
    assert published["value"] is None
