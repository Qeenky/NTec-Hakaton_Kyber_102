"""Конфигурация backend: DATABASE_URL, пороги правил, симулятор.

Значения читаются из переменных окружения и (если их нет) из `backend/.env`.
Основной режим — PostgreSQL (`postgresql+psycopg://`), резервный — SQLite-файл,
чтобы тесты и локальный запуск работали без установленного Postgres.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env без внешней зависимости.

    Уже заданные переменные окружения имеют приоритет: удобно переопределять
    DATABASE_URL одной командой, не редактируя файл.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _normalize_database_url(url: str) -> str:
    """Приводит URL к драйверу psycopg v3 (SQLAlchemy 2.0)."""
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg://" + url[len("postgresql+psycopg2://") :]
    return url


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, default))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_path(name: str) -> Path | None:
    """Путь из env. Относительный путь трактуется от `backend/`."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return path


@dataclass(frozen=True)
class Settings:
    """Настройки приложения, зафиксированные на момент запуска."""

    database_url: str
    database_is_postgres: bool
    rules_path: Path
    rule_activate_confidence: float | None
    rule_release_confidence: float | None
    cv_ttl_sec: float
    telemetry_retention_min: int
    sim_enabled: bool
    sim_tick_sec: float
    sim_runtime_hours_per_sec: float
    # Сквозной CV-пайплайн (AR-планшет + OCR манометра)
    detector: str
    layout_path: Path
    # Реестр планшетов (identity-маркер -> layout); нет файла — fallback.
    board_registry_path: Path
    gauge_config: Path | None
    gauge_ocr_every: int
    gauge_unit_scale: float | None
    cv_defects: bool
    # Видеоаналитика: скользящее окно наблюдения и подтверждение дефектов.
    analytics_window_sec: float
    analytics_confirm_ratio: float
    analytics_confirm_frames: int
    # Детектор аномалий anomaly/v2 (ленивая загрузка)
    anomaly_enabled: bool
    anomaly_model_dir: Path
    anomaly_threshold_quantile: float
    anomaly_threshold_factor: float
    anomaly_every: int
    # Детектор коррозии corrosion/ (ONNX)
    corrosion_enabled: bool
    corrosion_model_path: Path
    corrosion_conf: float
    corrosion_every: int
    corrosion_min_area_pct: float
    # Привязка без маркеров (visual_align)
    align_mode: str
    align_every: int
    align_references: Path
    align_cache_dir: Path
    align_max_ref_width: int
    # Локальная vision-модель (Ollama, офлайн)
    vision_enabled: bool
    ollama_url: str
    ollama_model: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Читает настройки один раз за процесс."""
    _load_dotenv(BASE_DIR / ".env")

    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        database_url = _normalize_database_url(configured)
    else:
        # Резервный режим: файл рядом с backend, без внешнего сервера.
        database_url = f"sqlite+pysqlite:///{(BASE_DIR / 'data' / 'app.db').as_posix()}"

    activate_raw = os.getenv("RULE_ACTIVATE_CONFIDENCE", "").strip()
    release_raw = os.getenv("RULE_RELEASE_CONFIDENCE", "").strip()

    layout_path = _env_path("LAYOUT_PATH") or (
        PROJECT_DIR / "ar_board" / "out" / "layout.json"
    )
    gauge_config = _env_path("GAUGE_CONFIG") or (BASE_DIR / "configs" / "manometer_board.json")
    detector = os.getenv("DETECTOR", "").strip().lower()
    if not detector:
        # По умолчанию — настоящий AR-планшет, если layout.json на месте.
        detector = "board" if layout_path.is_file() else "fake"
    unit_scale_raw = os.getenv("GAUGE_UNIT_SCALE", "").strip()

    return Settings(
        database_url=database_url,
        database_is_postgres=database_url.startswith(("postgresql", "postgres")),
        rules_path=Path(os.getenv("RULES_PATH", str(BASE_DIR / "rules" / "rules.yaml"))),
        rule_activate_confidence=float(activate_raw) if activate_raw else None,
        rule_release_confidence=float(release_raw) if release_raw else None,
        cv_ttl_sec=_env_float("CV_TTL_SEC", 5.0),
        telemetry_retention_min=_env_int("TELEMETRY_RETENTION_MIN", 180),
        sim_enabled=_env_bool("SIM_ENABLED", True),
        sim_tick_sec=_env_float("SIM_TICK_SEC", 1.0),
        sim_runtime_hours_per_sec=_env_float("SIM_RUNTIME_HOURS_PER_SEC", 0.02),
        detector=detector,
        layout_path=layout_path,
        board_registry_path=_env_path("BOARD_REGISTRY_PATH")
        or (PROJECT_DIR / "ar_board" / "boards.json"),
        gauge_config=gauge_config,
        gauge_ocr_every=max(1, _env_int("GAUGE_OCR_EVERY", 10)),
        gauge_unit_scale=float(unit_scale_raw) if unit_scale_raw else None,
        cv_defects=_env_bool("CV_DEFECTS", True),
        analytics_window_sec=max(1.0, _env_float("ANALYTICS_WINDOW_SEC", 60.0)),
        analytics_confirm_ratio=max(
            0.0, min(1.0, _env_float("ANALYTICS_CONFIRM_RATIO", 0.3))
        ),
        analytics_confirm_frames=max(1, _env_int("ANALYTICS_CONFIRM_FRAMES", 3)),
        anomaly_enabled=_env_bool("ANOMALY_ENABLED", True),
        anomaly_model_dir=_env_path("ANOMALY_MODEL_DIR")
        or (PROJECT_DIR / "anomaly" / "v2" / "models"),
        anomaly_threshold_quantile=min(
            1.0, max(0.0, _env_float("ANOMALY_THRESHOLD_QUANTILE", 0.99))
        ),
        anomaly_threshold_factor=max(1.0, _env_float("ANOMALY_THRESHOLD_FACTOR", 1.3)),
        anomaly_every=max(1, _env_int("ANOMALY_EVERY", 5)),
        corrosion_enabled=_env_bool("CORROSION_ENABLED", True),
        corrosion_model_path=_env_path("CORROSION_MODEL_PATH")
        or (PROJECT_DIR / "corrosion" / "models" / "corrosion_yolo11n_640.onnx"),
        corrosion_conf=max(0.0, min(1.0, _env_float("CORROSION_CONF", 0.25))),
        corrosion_every=max(1, _env_int("CORROSION_EVERY", 5)),
        corrosion_min_area_pct=max(
            0.0, _env_float("CORROSION_MIN_AREA_PCT", 0.5)
        ),
        align_mode=os.getenv("ALIGN_MODE", "features").strip().lower()
        or "features",
        align_every=max(1, _env_int("ALIGN_EVERY", 2)),
        align_references=_env_path("ALIGN_REFERENCES")
        or (PROJECT_DIR / "ar_board" / "references.json"),
        align_cache_dir=_env_path("ALIGN_CACHE_DIR")
        or (BASE_DIR / "data" / "align_cache"),
        align_max_ref_width=max(320, _env_int("ALIGN_MAX_REF_WIDTH", 1600)),
        vision_enabled=_env_bool("VISION_ENABLED", True),
        ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b").strip(),
    )


def reload_settings() -> None:
    """Сбрасывает кэш (используется тестами и скриптами)."""
    get_settings.cache_clear()
