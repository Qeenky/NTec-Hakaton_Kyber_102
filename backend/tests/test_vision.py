"""Vision-модель: разбор ответа, повторы, деградация и связь с диагностикой.

HTTP-клиент всегда замокан: тесты не запускают Ollama и не требуют GPU.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.main import app
from app.vision import client
from app.vision.client import VisionResult
from app.vision.constants import VISUAL_SIGNS_RULE_ID

IMAGE = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
HYPO = ["повышенная высокочастотная вибрация (возможен износ подшипника)"]


def _payload(**overrides) -> dict:
    data = {
        "surface": "окрашенная металлическая поверхность",
        "corrosion": True,
        "paint_peeling": True,
        "oil_leak": False,
        "mechanical_damage": False,
        "comment": "Видны следы коррозии и отслоение краски.",
        "confidence": 0.9,
    }
    data.update(overrides)
    return data


def install(monkeypatch, handler):
    """Подменяет HTTP-клиент; возвращает список отправленных payload'ов."""
    calls: list[dict] = []

    def fake_post(path: str, payload: dict, timeout: float) -> dict:
        calls.append(payload)
        return handler(len(calls) - 1, payload)

    monkeypatch.setattr(client, "_http_post", fake_post)
    return calls


@pytest.fixture(autouse=True)
def clean_vision_state():
    client.reset_client_state()
    yield
    client.reset_client_state()


# Клиент: валидный/битый JSON, таймаут, недоступная модель


def test_valid_json_returns_defect(monkeypatch) -> None:
    install(monkeypatch, lambda index, payload: {"response": json.dumps(_payload())})

    result = client.check_component(IMAGE, "pump", HYPO)

    assert isinstance(result, VisionResult)
    assert result.error is None
    assert result.verdict == "defect"
    assert result.signs["corrosion"] is True
    assert result.signs["oil_leak"] is False
    assert result.confidence == pytest.approx(0.9)
    assert result.model == "qwen2.5vl:3b"
    assert result.grounded is True
    assert result.latency_ms >= 0


def test_invalid_json_retries_with_strict_prompt_then_error(monkeypatch) -> None:
    calls = install(monkeypatch, lambda index, payload: {"response": "не JSON"})

    result = client.check_component(IMAGE, "pump", HYPO)

    assert result.error == "invalid_json"
    assert result.verdict == "insufficient"
    assert len(calls) == 2
    assert "Верни ТОЛЬКО JSON" in calls[1]["prompt"]
    assert calls[0]["format"] == "json"
    assert calls[0]["options"]["temperature"] == 0
    assert calls[0]["options"]["num_predict"] <= 300


def test_network_timeout_retries_once_then_reports_timeout(monkeypatch) -> None:
    calls = install(monkeypatch, lambda index, payload: (_ for _ in ()).throw(TimeoutError("timed out")))

    result = client.check_component(IMAGE, "pump", HYPO)

    assert result.error == "timeout"
    assert len(calls) == 2


def test_retry_recovers_after_single_timeout(monkeypatch) -> None:
    def handler(index: int, payload: dict) -> dict:
        if index == 0:
            raise TimeoutError("timed out")
        return {"response": json.dumps(_payload())}

    calls = install(monkeypatch, handler)
    result = client.check_component(IMAGE, "pump", HYPO)

    assert result.error is None
    assert result.verdict == "defect"
    assert len(calls) == 2


def test_unavailable_model_degrades_without_exception(monkeypatch) -> None:
    install(
        monkeypatch,
        lambda index, payload: (_ for _ in ()).throw(urllib.error.URLError("connection refused")),
    )

    result = client.check_component(IMAGE, "pump", HYPO)

    assert result.error == "ollama_unavailable"
    assert result.verdict == "insufficient"
    assert result.confidence == 0.0


def test_verdict_matrix() -> None:
    signs = {
        "corrosion": True,
        "paint_peeling": False,
        "oil_leak": False,
        "mechanical_damage": None,
    }
    assert client.verdict_from_signs(signs) == "defect"
    assert (
        client.verdict_from_signs(
            {
                "corrosion": False,
                "paint_peeling": False,
                "oil_leak": False,
                "mechanical_damage": False,
            }
        )
        == "no_defect"
    )
    assert (
        client.verdict_from_signs(
            {
                "corrosion": False,
                "paint_peeling": None,
                "oil_leak": False,
                "mechanical_damage": None,
            }
        )
        == "insufficient"
    )


def test_number_not_from_input_marks_ungrounded(monkeypatch) -> None:
    install(
        monkeypatch,
        lambda index, payload: {
            "response": json.dumps(_payload(comment="Найдено 42 болта и коррозия."))
        },
    )

    result = client.check_component(IMAGE, "pump", HYPO)

    assert result.verdict == "defect"
    assert result.grounded is False


def test_cache_stops_repeat_model_calls(monkeypatch) -> None:
    calls = install(monkeypatch, lambda index, payload: {"response": json.dumps(_payload())})

    first = client.check_component(IMAGE, "pump", HYPO)
    second = client.check_component(IMAGE, "pump", HYPO)

    assert len(calls) == 1
    assert first.cached is False
    assert second.cached is True
    assert second.component_id == first.component_id


# API и связь с диагностикой


def _fake_model(monkeypatch, result: VisionResult):
    def fake_check_component(image, component_id, hypotheses):
        return result

    monkeypatch.setattr(client, "check_component", fake_check_component)


def _jpeg(width: int = 64, height: int = 48) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 110, 100)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_vision_check_api_creates_visual_signs(memory_db, monkeypatch) -> None:
    _fake_model(
        monkeypatch,
        VisionResult(
            component_id="pump",
            verdict="defect",
            signs={
                "corrosion": True,
                "paint_peeling": True,
                "oil_leak": False,
                "mechanical_damage": False,
            },
            confidence=0.9,
            comment="Коррозия и отслоение краски.",
            surface="металл",
            model="qwen2.5vl:3b",
            latency_ms=2100,
            grounded=True,
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/vision/check",
            params={"equipment_id": "PUMP-001", "component_id": "pump"},
            files={"image": ("pump.jpg", _jpeg(), "image/jpeg")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verdict"] == "defect"
        assert body["signs"]["corrosion"] is True
        assert body["model"] == "qwen2.5vl:3b"
        assert body["latency_ms"] == 2100
        assert body["error"] is None

        diagnostics = test_client.get("/equipment/PUMP-001/diagnostics").json()
        active = {item["rule_id"]: item for item in diagnostics["active"]}
        assert VISUAL_SIGNS_RULE_ID in active
        visual = active[VISUAL_SIGNS_RULE_ID]
        assert visual["title"] == "Визуальные признаки дефекта (VLM)"
        assert visual["severity"] == "info"
        assert "Единичное наблюдение" in visual["message"]
        assert visual["evidence"][0]["component"] == "pump"
        assert visual["evidence"][0]["sign"] == "corrosion"
        assert visual["evidence"][0]["status"] == "single_observation"

        analyzed = test_client.post(
            "/analyze",
            params={"equipment_id": "PUMP-001"},
            files={"image": ("pump.jpg", _jpeg(), "image/jpeg")},
        )
        assert analyzed.status_code == 200, analyzed.text
        assert analyzed.json()["vision"]["verdict"] == "defect"


def test_vision_check_without_image_or_frame_reports_error(memory_db, monkeypatch) -> None:
    from app.vision import service

    service.clear_vision_state()

    with TestClient(app) as test_client:
        response = test_client.post(
            "/vision/check",
            params={"equipment_id": "PUMP-001", "component_id": "pump"},
        )
        assert response.status_code == 200
        assert response.json()["error"] == "no_frame"
        assert response.json()["verdict"] == "insufficient"


def test_visual_defect_wins_when_more_confident(memory_db) -> None:
    from app.vision import service

    service.clear_vision_state()
    row = db.create_diagnostic(
        equipment_id="PUMP-001",
        rule_id="visual_defect",
        severity="warning",
        confidence=0.95,
        evidence=[],
        message="Эвристики",
        recommendation="Осмотреть",
        overlay={"component_id": "pump"},
        now=datetime.now(timezone.utc),
    )
    result = {
        "component_id": "pump",
        "verdict": "defect",
        "signs": {
            "corrosion": True,
            "paint_peeling": False,
            "oil_leak": False,
            "mechanical_damage": False,
        },
        "confidence": 0.7,
        "model": "qwen2.5vl:3b",
    }

    suppressed = service.apply_vision_diagnostic("PUMP-001", result)

    assert suppressed == "visual_defect"
    assert db.find_open_diagnostic("PUMP-001", "visual_defect")["id"] == row["id"]
    assert db.find_open_diagnostic("PUMP-001", VISUAL_SIGNS_RULE_ID) is None


def test_visual_signs_wins_and_marks_visual_defect(memory_db) -> None:
    from app.vision import service

    service.clear_vision_state()
    row = db.create_diagnostic(
        equipment_id="PUMP-001",
        rule_id="visual_defect",
        severity="warning",
        confidence=0.6,
        evidence=[],
        message="Эвристики",
        recommendation="Осмотреть",
        overlay={"component_id": "pump"},
        now=datetime.now(timezone.utc),
    )
    result = {
        "component_id": "pump",
        "verdict": "defect",
        "signs": {
            "corrosion": True,
            "paint_peeling": False,
            "oil_leak": False,
            "mechanical_damage": False,
        },
        "confidence": 0.9,
        "model": "qwen2.5vl:3b",
    }

    suppressed = service.apply_vision_diagnostic("PUMP-001", result)

    assert suppressed is None
    signs = db.find_open_diagnostic("PUMP-001", VISUAL_SIGNS_RULE_ID)
    assert signs is not None and signs["confidence"] == pytest.approx(0.9)
    resolved = db.get_diagnostic(row["id"])
    assert resolved["status"] == "resolved"
    assert resolved["suppressed_by"] == VISUAL_SIGNS_RULE_ID


# /vision/explain: плавающий invalid_json и текстовый фолбэк

COHERENT_TEXT = (
    "Насос работает с повышенной вибрацией, на корпусе видны следы коррозии. "
    "Рекомендуется осмотреть подшипниковый узел и повторить замеры."
)


def test_explain_coherent_text_is_returned_as_summary(memory_db, monkeypatch) -> None:
    """Модель ответила текстом вместо JSON: текст уходит в summary, а не 500."""
    calls = install(monkeypatch, lambda index, payload: {"response": COHERENT_TEXT})

    with TestClient(app) as test_client:
        calls.clear()  # отбрасываем warmup-вызов из lifespan
        response = test_client.get(
            "/vision/explain", params={"equipment_id": "PUMP-001"}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"].strip()
    assert body["summary"].startswith("Насос работает")
    assert body["parsed_as_text"] is True
    assert not body["error"]
    # Связный текст распознаётся сразу: повтор со строгим промптом не нужен.
    assert len(calls) == 1


def test_explain_truncated_json_returns_summary(memory_db, monkeypatch) -> None:
    """Обрезанный JSON (num_predict кончился) — summary достаём без второго вызова."""
    truncated = '{"summary": "На корпусе видны следы коррозии, нужен осмотр узла'
    calls = install(monkeypatch, lambda index, payload: {"response": truncated})

    with TestClient(app) as test_client:
        calls.clear()
        response = test_client.get(
            "/vision/explain", params={"equipment_id": "PUMP-001"}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == "На корпусе видны следы коррозии, нужен осмотр узла"
    assert body["parsed_as_text"] is True
    assert not body["error"]
    assert len(calls) == 1


def test_explain_garbage_answer_degrades_without_500(memory_db, monkeypatch) -> None:
    """Мусорный ответ («@@@@») — машинный error, но не исключение и не 500."""
    install(monkeypatch, lambda index, payload: {"response": "@@@@"})

    with TestClient(app) as test_client:
        response = test_client.get(
            "/vision/explain", params={"equipment_id": "PUMP-001"}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error"] in {"invalid_json", "empty_response"}
    # Детерминированный fallback всегда даёт непустое заключение.
    assert body["summary"].strip()
    assert body.get("parsed_as_text") is not True


def test_explain_valid_json_has_no_parsed_as_text(memory_db, monkeypatch) -> None:
    answer = json.dumps(
        {
            "summary": "Отклонений не выявлено, объект работает в штатном режиме.",
            "severity": "info",
            "actions": ["Продолжать плановый контроль."],
            "facts_used": ["объект"],
        },
        ensure_ascii=False,
    )
    calls = install(monkeypatch, lambda index, payload: {"response": answer})

    with TestClient(app) as test_client:
        calls.clear()  # отбрасываем warmup-вызов из lifespan
        response = test_client.get(
            "/vision/explain", params={"equipment_id": "PUMP-001"}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error"] is None
    assert body["summary"] == "Отклонений не выявлено, объект работает в штатном режиме."
    assert "parsed_as_text" not in body
    assert len(calls) == 1


def test_vision_check_ollama_400_degrades_to_unavailable(memory_db, monkeypatch) -> None:
    """HTTP 400 Ollama (в т.ч. переполнение num_ctx) — error, но не 500."""
    calls: list[dict] = []

    def fake_post(path: str, payload: dict, timeout: float) -> dict:
        calls.append(payload)
        raise urllib.error.HTTPError(
            "http://127.0.0.1:11434/api/generate",
            400,
            "Bad Request",
            {},
            io.BytesIO(
                b'{"error":"the request exceeds the available context size, '
                b'try increasing num_ctx"}'
            ),
        )

    monkeypatch.setattr(client, "_http_post", fake_post)

    with TestClient(app) as test_client:
        calls.clear()  # отбрасываем warmup-вызов из lifespan
        response = test_client.post(
            "/vision/check",
            params={"equipment_id": "PUMP-001", "component_id": "pump"},
            files={"image": ("pump.jpg", _jpeg(), "image/jpeg")},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error"] == "ollama_unavailable"
    assert body["verdict"] == "insufficient"
    assert body["confidence"] == 0.0
    # Один повтор, затем штатная деградация: 500 наружу не уходит.
    assert len(calls) == 2
    assert all(call["options"]["num_ctx"] == 4096 for call in calls)
