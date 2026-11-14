"""Общие фикстуры: SQLite in-memory, без фонового симулятора."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
TEST_LAYOUT_PATH = PROJECT_ROOT / "ar_board" / "out" / "layout.json"
TEST_GAUGE_CONFIG = ROOT / "configs" / "manometer_board.json"
sys.path.insert(0, str(ROOT))

# Важно: выставить до импорта app.config/app.db.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["SIM_ENABLED"] = "false"
# Тесты API не должны зависеть от ar_board/opencv; пайплайн тестируется отдельно.
os.environ["DETECTOR"] = "fake"
# Тесты не должны зависеть от пользовательского backend/.env и рабочей директории:
# схематичный планшет и калибровка под него задаются абсолютными путями.
# (Явно заданные env-переменные важнее значений из .env.)
os.environ["LAYOUT_PATH"] = str(TEST_LAYOUT_PATH)
os.environ["GAUGE_CONFIG"] = str(TEST_GAUGE_CONFIG)
# Тесты не должны грузить CUDA-модель anomaly/v2: канал тестируется с моками.
os.environ["ANOMALY_ENABLED"] = "false"
# ONNX-коррозия тоже тестируется с моками, без реальной модели.
os.environ["CORROSION_ENABLED"] = "false"
# Тесты по умолчанию идут по старому маркерному пути; feature-привязка
# тестируется отдельно (там нужны эталоны visual_align).
os.environ["ALIGN_MODE"] = "aruco"

import pytest  # noqa: E402

from app import cv_state, db  # noqa: E402
from app.seed import seed_all  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def schematic_board_env():
    """Фиксирует планшет `ar_board/out` и калибровку для всех тестов.

    Тесты не должны зависеть от `backend/.env` (там может быть `out_real`) и от
    текущей рабочей директории: пути абсолютные, env важнее `.env`.
    """
    from app.config import reload_settings

    os.environ["LAYOUT_PATH"] = str(TEST_LAYOUT_PATH)
    os.environ["GAUGE_CONFIG"] = str(TEST_GAUGE_CONFIG)
    reload_settings()
    yield


@pytest.fixture()
def memory_db():
    """Чистая схема + демо-агрегат в памяти на каждый тест."""
    db.configure("sqlite+pysqlite:///:memory:", force=True)
    db.init_db()
    seed_all()
    cv_state.clear()
    yield
    cv_state.clear()
    db.get_engine().dispose()
