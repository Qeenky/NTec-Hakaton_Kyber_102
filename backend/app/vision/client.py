"""Клиент локальной VLM через Ollama (офлайн, без облачных API).

Модель не вызывается из event loop: все функции синхронные, API-слой оборачивает
их в `asyncio.to_thread`. Любая сетевая проблема превращается в `error`, а не в
исключение: сервер должен оставаться живым и без Ollama.

Промпт для изображения взят из `tools/llm_probe.py` и дополнен гипотезами
датчиков. Ответ валидируется pydantic-схемой; при битом JSON делается один
повтор с более строгим указанием.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..config import get_settings

# Длинная сторона кадра для модели: больше — риск пустого ответа из-за VRAM.
VISION_MAX_SIDE = 768
CACHE_TTL_SEC = 600.0
REQUEST_TIMEOUT_SEC = 60.0
STATUS_TIMEOUT_SEC = 5.0
# Заключение по фактам длиннее ответа проверки кадра: при 300 токенах
# JSON обрывался на середине и парсинг падал (invalid_json).
EXPLAIN_NUM_PREDICT = 1200

PROMPT_TEMPLATE = """Ты — помощник инженера по визуальному осмотру промышленного оборудования.
Смотри только на изображение. Не додумывай то, чего не видно.
Показания датчиков и гипотезы тебе НЕ передаются: ты оцениваешь только то, что видно на изображении.
Не упоминай вибрацию, температуру, подшипники, датчики и режимы работы — их на фото не видно.
Если поверхность узла видна целиком и в фокусе — для каждого признака дай true или false.
null допустим только тогда, когда оценить признак физически невозможно: узел перекрыт, вне кадра, кадр слишком тёмный или размытый.

Оцени, видны ли на поверхности следующие признаки, и верни СТРОГО JSON:
{
  "surface": "краткое описание поверхности (1 предложение)",
  "corrosion": true | false | null,
  "paint_peeling": true | false | null,
  "oil_leak": true | false | null,
  "mechanical_damage": true | false | null,
  "comment": "1-2 предложения, что именно видно",
  "confidence": 0.0
}

Правила:
- true — признак явно виден;
- false — признак отсутствует И соответствующая часть поверхности видна на кадре;
- null — только если эту часть узла физически невозможно оценить (перекрыта, обрезана, темно);
- если поверхность чистая, ровная и окрашена равномерно — это false, а не null;
- запрещено писать «деталей не видно», если поверхность видна: ставь false;
- зернистость, шум матрицы, артефакты сжатия и цифровой зум — НЕ признак дефекта;
- ровная окрашенная поверхность с зерном — это false, а не коррозия;
- confidence — твоя уверенность 0..1;
- никаких чисел, размеров и диагнозов.

Пример правильного ответа для чистого окрашенного корпуса:
{"surface":"ровная синяя окрашенная поверхность","corrosion":false,"paint_peeling":false,"oil_leak":false,"mechanical_damage":false,"comment":"Поверхность чистая, следов коррозии и повреждений нет.","confidence":0.9}
"""

STRICT_SUFFIX = """
Верни ТОЛЬКО JSON-объект с ключами surface, corrosion, paint_peeling, oil_leak,
mechanical_damage, comment, confidence. Без markdown, без пояснений и текста вокруг.
"""

EXPLAIN_PROMPT = """Ты — инженер-диагност промышленного насосного агрегата.
Ниже — единственные факты, которые тебе разрешено использовать (JSON):

{facts}

Правила:
- не выдумывай числа: используй только числа из фактов;
- ссылайся только на переданные факты, не добавляй диагнозов, которых в них нет;
- если в «активные_диагностики» есть события severity warning или critical,
  прямо назови выявленные признаки и НЕ называй объект исправным или «в норме»;
- не перечисляй данные карточки (модель, номер, год), если они не
  нужны для вывода; начни с главного: есть отклонения или нет;
- пиши по-русски, ровно 3–4 предложения в summary;
- severity: info (норма/наблюдение), warning (есть предупреждение), critical (авария);
- actions — короткие практические действия, только по фактам;
- facts_used — список ключей фактов, на которые ты опирался.

Верни СТРОГО JSON:
{{"summary": "...", "severity": "info|warning|critical", "actions": ["..."], "facts_used": ["..."]}}
"""

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)")


# Схемы ответа


class VisionPayload(BaseModel):
    """Строгая схема ответа модели по изображению."""

    model_config = ConfigDict(extra="ignore")

    surface: str
    corrosion: bool | None
    paint_peeling: bool | None
    oil_leak: bool | None
    mechanical_damage: bool | None
    comment: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: Any) -> Any:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, min(1.0, float(value)))
        return value


class ExplainPayload(BaseModel):
    """Схема ответа объяснятора."""

    model_config = ConfigDict(extra="ignore")

    summary: str
    severity: str = "info"
    actions: list[str] = Field(default_factory=list)
    facts_used: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class VisionResult:
    """Результат проверки узла: признаки, вердикт и метаданные."""

    component_id: str
    verdict: str
    signs: dict[str, bool | None]
    confidence: float
    comment: str
    surface: str = ""
    model: str = ""
    latency_ms: int = 0
    grounded: bool = True
    error: str | None = None
    cached: bool = False
    suppressed_by: str | None = None
    checked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "verdict": self.verdict,
            "signs": dict(self.signs),
            "confidence": round(float(self.confidence), 4),
            "comment": self.comment,
            "surface": self.surface,
            "model": self.model,
            "latency_ms": int(self.latency_ms),
            "grounded": bool(self.grounded),
            "error": self.error,
            "cached": bool(self.cached),
            "suppressed_by": self.suppressed_by,
            "checked_at": self.checked_at,
        }


class ModelError(Exception):
    """Ошибка вызова модели с машинным кодом для поля `error`."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# HTTP (единственное место, которое мокают тесты)


def _http_post(path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST JSON в Ollama и разбор ответа."""
    settings = get_settings()
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.ollama_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_get(path: str, timeout: float) -> dict[str, Any]:
    """GET JSON из Ollama (список моделей)."""
    settings = get_settings()
    request = urllib.request.Request(f"{settings.ollama_url}{path}", method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _error_code(exc: BaseException) -> str:
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text:
        return "timeout"
    if isinstance(exc, (urllib.error.URLError, ConnectionError, OSError)):
        return "ollama_unavailable"
    return "http_error"


def _post_with_retry(
    path: str,
    payload: dict[str, Any],
    timeout: float = REQUEST_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Один повтор при сетевой ошибке/таймауте."""
    last: BaseException | None = None
    for _ in range(2):
        try:
            return _http_post(path, payload, timeout)
        except Exception as exc:  # noqa: BLE001 - сеть, таймауты, сокеты
            last = exc
    assert last is not None
    raise ModelError(_error_code(last), str(last))


def _get_with_retry(path: str, timeout: float = STATUS_TIMEOUT_SEC) -> dict[str, Any]:
    last: BaseException | None = None
    for _ in range(2):
        try:
            return _http_get(path, timeout)
        except Exception as exc:  # noqa: BLE001
            last = exc
    assert last is not None
    raise ModelError(_error_code(last), str(last))


# Метрики последнего ответа (для /vision/status)

_status_lock = threading.Lock()
_last_response_ms: int | None = None
_last_ok_at: str | None = None
_last_error: str | None = None


def _mark_ok(latency_ms: int) -> None:
    global _last_response_ms, _last_ok_at, _last_error
    with _status_lock:
        _last_response_ms = int(latency_ms)
        _last_ok_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _last_error = None


def _mark_error(code: str) -> None:
    global _last_error
    with _status_lock:
        _last_error = code


def reset_client_state() -> None:
    """Сброс кэша и метрик (тесты, смена модели)."""
    global _last_response_ms, _last_ok_at, _last_error
    with _cache_lock:
        _cache.clear()
    with _status_lock:
        _last_response_ms = None
        _last_ok_at = None
        _last_error = None


# Кэш по хэшу изображения

_cache: dict[str, tuple[float, VisionResult]] = {}
_cache_lock = threading.Lock()


def _cache_key(image_bytes: bytes, component_id: str, hypotheses: list[str]) -> str:
    digest = hashlib.sha256(image_bytes).hexdigest()
    # Компонент и гипотезы входят в промпт, поэтому участвуют в ключе: иначе
    # один и тот же кадр вернул бы ответ, посчитанный по другим подсказкам.
    extra = hashlib.sha256(
        (component_id + "\x1f" + "\x1f".join(hypotheses)).encode("utf-8")
    ).hexdigest()[:16]
    return f"{digest}:{extra}"


def _cache_get(key: str, now: float) -> VisionResult | None:
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            return None
        created, result = item
        if now - created > CACHE_TTL_SEC:
            _cache.pop(key, None)
            return None
        return result


def _cache_put(key: str, result: VisionResult) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), result)
        if len(_cache) > 128:
            oldest = sorted(_cache.items(), key=lambda pair: pair[1][0])[:32]
            for stale_key, _ in oldest:
                _cache.pop(stale_key, None)


# Изображение и разбор ответа


def estimate_noise_sigma(gray: Any, cv2: Any) -> float:
    """Грубая оценка шума матрицы (метод Иммеркэра).

    Возвращает оценку стандартного отклонения шума в уровнях яркости.
    Нужна, чтобы не путать зерно камеры с шероховатостью поверхности:
    на близком расстоянии в помещении ISO растёт, и VLM принимает шум
    за коррозию (проверено: σ≈10–25 даёт ложный «дефект»).
    """
    import numpy as np

    lap = cv2.Laplacian(gray, cv2.CV_64F)
    h, w = gray.shape[:2]
    if h < 3 or w < 3:
        return 0.0
    return float(np.sqrt(np.pi / 2.0) / (6.0 * (w - 2) * (h - 2)) * np.abs(lap).sum())


def _denoise(image: Any, cv2: Any, threshold: float = 8.0) -> Any:
    """Подавляет шум только если он заметный; иначе кадр не трогаем."""
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        sigma = estimate_noise_sigma(gray, cv2)
        if sigma < threshold:
            return image
        return cv2.fastNlMeansDenoisingColored(image, None, 7, 7, 7, 21)
    except Exception:  # noqa: BLE001 - шумодав не должен ронять запрос
        return image


def encode_image(image: Any) -> bytes:
    """Байты JPEG/PNG: принимает bytes или numpy BGR (как отдаёт OpenCV)."""
    if isinstance(image, (bytes, bytearray)):
        return bytes(image)
    try:
        import cv2  # локальный импорт: тесты без OpenCV не падают
    except ImportError as exc:  # pragma: no cover - окружение без OpenCV
        raise ModelError("bad_image", "нужен numpy-кадр или bytes") from exc
    image = _denoise(image, cv2)
    # Ограничиваем длинную сторону: vision-энкодер на 4961 px съедает всю
    # видеопамять, и модель возвращает пустой ответ (проверено).
    try:
        height, width = image.shape[:2]
        longest = max(height, width)
        if longest > VISION_MAX_SIDE:
            factor = VISION_MAX_SIDE / float(longest)
            image = cv2.resize(
                image,
                (max(1, int(round(width * factor))), max(1, int(round(height * factor)))),
                interpolation=cv2.INTER_AREA,
            )
    except Exception:  # noqa: BLE001 - не повод ронять запрос
        pass
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise ModelError("bad_image", "не удалось закодировать кадр в JPEG")
    return buffer.tobytes()


def _strip_json(raw: str) -> str:
    return _JSON_FENCE.sub("", raw.strip()).strip()


_SUMMARY_FIELD = re.compile(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)')


def _salvage_summary(raw: str) -> str | None:
    """Достаёт заключение из обрезанного/невалидного ответа модели.

    Модель на 6 ГБ VRAM иногда не закрывает JSON: `num_predict` кончается
    на середине строки `summary`. Пользователю нужен текст заключения, а не
    сырой JSON, поэтому вытаскиваем поле регуляркой. Связный текст без JSON
    возвращаем как есть; JSON без `summary` — не заключение.
    """
    text = " ".join((raw or "").split())
    if len(text) < 40 or not any(ch.isalnum() for ch in text):
        return None
    match = _SUMMARY_FIELD.search(text)
    if match:
        fragment = match.group(1)
        try:
            value = json.loads('"' + fragment + '"')
        except (json.JSONDecodeError, ValueError):
            value = fragment.replace('\\"', '"').replace("\\n", " ").strip()
        value = " ".join(str(value).split())
        return value[:700] or None
    if text.lstrip().startswith("{"):
        return None
    return text[:700]


def parse_vision_payload(raw: str) -> VisionPayload:
    """JSON → pydantic; любое несоответствие схеме — ошибка."""
    cleaned = _strip_json(raw)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("ответ модели — не JSON-объект")
    return VisionPayload.model_validate(data)


def _numbers(text: str) -> set[float]:
    values: set[float] = set()
    for token in _NUMBER.findall(text or ""):
        try:
            values.add(round(float(token.replace(",", ".")), 4))
        except ValueError:
            continue
    return values


def is_grounded(comment: str, allowed_text: str) -> bool:
    """Нет ли в комментарии чисел, которых не было во входных данных.

    «Входные данные» — промпт с гипотезами датчиков и служебные числа шаблона.
    Флаг не отбрасывает ответ: он лишь помечает его `grounded=false`.
    """
    return _numbers(comment) <= _numbers(allowed_text)


def verdict_from_signs(signs: dict[str, bool | None]) -> str:
    """Любой true → defect; всё false → no_defect; иначе insufficient."""
    flags = [
        signs.get("corrosion"),
        signs.get("paint_peeling"),
        signs.get("oil_leak"),
        signs.get("mechanical_damage"),
    ]
    if any(flag is True for flag in flags):
        return "defect"
    if all(flag is False for flag in flags):
        return "no_defect"
    return "insufficient"


def _verdict_with_text_fallback(
    signs: dict[str, bool | None],
    comment: str,
    confidence: float,
) -> str:
    """Вердикт по признакам с осторожным текстовым фолбэком.

    Модель часто ставит null вместо false на чистой поверхности, но в
    комментарии прямо пишет «следов не видно». Такой ответ не должен
    превращаться в «недостаточно данных»: иначе чистый узел на демо
    выглядит как отказ. Фолбэк срабатывает только при уверенности >= 0.7.
    """
    verdict = verdict_from_signs(signs)
    if verdict != "insufficient" or confidence < 0.7:
        return verdict
    if any(value is True for value in signs.values()):
        return verdict
    text = (comment or "").lower()
    negative = ("не видно", "нет следов", "отсутств", "не обнаруж", "без следов")
    if any(marker in text for marker in negative):
        return "no_defect"
    return verdict


# Публичный вызов модели


def _generate(
    prompt: str,
    image_b64: str | None = None,
    timeout: float = REQUEST_TIMEOUT_SEC,
    num_predict: int = 300,
) -> tuple[str, int]:
    settings = get_settings()
    payload: dict[str, Any] = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        # 4096 обязателен: кадр 1280 px + промпт ≈ 2800 токенов, при 2048
        # Ollama отвечает 400. Кадр перед отправкой ужимается до 768 px.
        # num_predict: 300 хватает на короткий JSON проверки кадра, но
        # заключение по фактам обрезалось на середине (invalid_json).
        "options": {
            "temperature": 0,
            "num_predict": int(num_predict),
            "num_ctx": 4096,
        },
    }
    if image_b64 is not None:
        payload["images"] = [image_b64]
    started = time.perf_counter()
    body = _post_with_retry("/api/generate", payload, timeout=timeout)
    latency_ms = int(round((time.perf_counter() - started) * 1000))
    text = body.get("response")
    if not _looks_valid(text):
        # Раннер на 6 ГБ иногда деградирует и отдаёт пустоту или мусор.
        # Выгружаем модель и пробуем ещё раз — это лечит состояние.
        import logging

        logging.getLogger(__name__).warning(
            "vision: пустой/мусорный ответ, перезапускаю модель"
        )
        _unload_model(settings.ollama_model)
        body = _post_with_retry("/api/generate", payload, timeout=timeout)
        latency_ms = int(round((time.perf_counter() - started) * 1000))
        text = body.get("response")
    if not _looks_valid(text):
        _mark_error("empty_response")
        raise ModelError("empty_response", "Ollama вернула пустой или мусорный ответ")
    _mark_ok(latency_ms)
    return text, latency_ms


def _looks_valid(text: Any) -> bool:
    """Ответ непустой и не состоит из мусорных символов вроде «@@@@»."""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    if not any(ch.isalnum() for ch in stripped):
        return False
    return True


def _unload_model(model: str) -> None:
    """Выгружает модель из памяти (keep_alive=0), чтобы перезагрузить её чисто."""
    try:
        _http_post(
            "/api/generate",
            {"model": model, "prompt": "", "keep_alive": 0, "stream": False},
            timeout=15.0,
        )
    except Exception:  # noqa: BLE001 - выгрузка не критична
        pass


def check_component(
    image_bgr: Any,
    component_id: str,
    hypotheses: list[str],
) -> VisionResult:
    """Проверяет узел по изображению; никогда не бросает исключение наружу."""
    settings = get_settings()
    signs: dict[str, bool | None] = {
        "corrosion": None,
        "paint_peeling": None,
        "oil_leak": None,
        "mechanical_damage": None,
    }
    started = time.perf_counter()

    def failure(code: str, comment: str = "") -> VisionResult:
        _mark_error(code)
        return VisionResult(
            component_id=component_id,
            verdict="insufficient",
            signs=signs,
            confidence=0.0,
            comment=comment,
            model=settings.ollama_model,
            latency_ms=int(round((time.perf_counter() - started) * 1000)),
            grounded=True,
            error=code,
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    if not settings.vision_enabled:
        return failure("vision_disabled")

    try:
        image_bytes = encode_image(image_bgr)
    except ModelError as exc:
        return failure(exc.code, exc.detail)

    # Гипотезы в промпт больше не попадают (модель начинала их «подтверждать»),
    # поэтому и в ключе кэша они не участвуют.
    key = _cache_key(image_bytes, component_id, [])
    cached = _cache_get(key, time.time())
    if cached is not None:
        from dataclasses import replace

        return replace(cached, cached=True, latency_ms=0)

    hints = "; ".join(item.strip() for item in hypotheses if item.strip())
    prompt = PROMPT_TEMPLATE
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    try:
        raw, _ = _generate(prompt, image_b64)
        try:
            payload = parse_vision_payload(raw)
        except (json.JSONDecodeError, ValidationError, ValueError):
            # Один повтор с более строгим указанием формата.
            raw, _ = _generate(prompt + STRICT_SUFFIX, image_b64)
            try:
                payload = parse_vision_payload(raw)
            except (json.JSONDecodeError, ValidationError, ValueError):
                return failure("invalid_json")
    except ModelError as exc:
        return failure(exc.code, exc.detail)

    parsed_signs = {
        "corrosion": payload.corrosion,
        "paint_peeling": payload.paint_peeling,
        "oil_leak": payload.oil_leak,
        "mechanical_damage": payload.mechanical_damage,
    }
    result = VisionResult(
        component_id=component_id,
        verdict=_verdict_with_text_fallback(parsed_signs, payload.comment, float(payload.confidence)),
        signs=parsed_signs,
        confidence=round(float(payload.confidence), 4),
        comment=payload.comment,
        surface=payload.surface,
        model=settings.ollama_model,
        latency_ms=int(round((time.perf_counter() - started) * 1000)),
        grounded=is_grounded(payload.comment, prompt + hints),
        error=None,
        checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    _cache_put(key, result)
    return result


def explain_facts(facts: dict[str, Any]) -> tuple[dict[str, Any], int, str]:
    """Просит модель объяснить факты. Возвращает (payload, latency_ms, error)."""
    settings = get_settings()
    if not settings.vision_enabled:
        return {}, 0, "vision_disabled"

    facts_json = json.dumps(facts, ensure_ascii=False, sort_keys=True)
    prompt = EXPLAIN_PROMPT.format(facts=facts_json)

    try:
        raw, latency_ms = _generate(prompt, num_predict=EXPLAIN_NUM_PREDICT)
        try:
            payload = ExplainPayload.model_validate_json(_strip_json(raw))
        except (json.JSONDecodeError, ValidationError, ValueError):
            # Обрезанный JSON — частая история: сначала пробуем вытащить
            # summary без второго вызова модели (экономит 10–15 секунд).
            salvaged = _salvage_summary(raw)
            if salvaged:
                return (
                    {
                        "summary": salvaged,
                        "severity": "warning",
                        "actions": [],
                        "facts_used": [],
                        "parsed_as_text": True,
                    },
                    latency_ms,
                    "",
                )
            raw, extra_ms = _generate(
                prompt + STRICT_SUFFIX, num_predict=EXPLAIN_NUM_PREDICT
            )
            latency_ms += extra_ms
            try:
                payload = ExplainPayload.model_validate_json(_strip_json(raw))
            except (json.JSONDecodeError, ValidationError, ValueError):
                salvaged = _salvage_summary(raw)
                if salvaged:
                    return (
                        {
                            "summary": salvaged,
                            "severity": "warning",
                            "actions": [],
                            "facts_used": [],
                            "parsed_as_text": True,
                        },
                        latency_ms,
                        "",
                    )
                return {}, latency_ms, "invalid_json"
    except ModelError as exc:
        return {}, 0, exc.code

    if not payload.summary.strip():
        return {}, latency_ms, "empty_summary"
    # Числа в заключении должны быть из фактов — иначе честно отказываемся.
    if not is_grounded(payload.summary, facts_json + prompt):
        return {}, latency_ms, "ungrounded"

    severity = payload.severity.strip().lower()
    if severity not in {"info", "warning", "critical"}:
        severity = "warning"
    return (
        {
            "summary": payload.summary.strip(),
            "severity": severity,
            "actions": [item.strip() for item in payload.actions if item.strip()],
            "facts_used": [item.strip() for item in payload.facts_used if item.strip()],
        },
        latency_ms,
        "",
    )


def ollama_status() -> dict[str, Any]:
    """Доступность модели и метрики последнего ответа."""
    settings = get_settings()
    status: dict[str, Any] = {
        "enabled": bool(settings.vision_enabled),
        "available": False,
        "model": settings.ollama_model,
        "url": settings.ollama_url,
        "last_response_ms": _last_response_ms,
        "last_ok_at": _last_ok_at,
        "last_error": _last_error,
        "error": None,
        "models": [],
    }
    if not settings.vision_enabled:
        status["error"] = "vision_disabled"
        return status
    try:
        body = _get_with_retry("/api/tags")
    except ModelError as exc:
        status["error"] = exc.code
        status["last_error"] = exc.code
        _mark_error(exc.code)
        return status

    names = [
        str(item.get("name") or item.get("model") or "")
        for item in body.get("models", [])
        if isinstance(item, dict)
    ]
    status["models"] = [name for name in names if name]
    wanted = settings.ollama_model
    status["available"] = any(
        name == wanted or name == f"{wanted}:latest" or name.split(":")[0] == wanted
        for name in names
    )
    if not status["available"]:
        status["error"] = "model_not_found"
    status["last_response_ms"] = _last_response_ms
    status["last_ok_at"] = _last_ok_at
    status["last_error"] = _last_error
    return status


__all__ = [
    "CACHE_TTL_SEC",
    "EXPLAIN_PROMPT",
    "ExplainPayload",
    "ModelError",
    "PROMPT_TEMPLATE",
    "STRICT_SUFFIX",
    "VisionPayload",
    "VisionResult",
    "check_component",
    "encode_image",
    "explain_facts",
    "is_grounded",
    "ollama_status",
    "parse_vision_payload",
    "reset_client_state",
    "verdict_from_signs",
]
