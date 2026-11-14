"""Мост между backend и соседними пакетами проекта.

`ar_board` (AR-планшет) и `cv_classic` (чтение манометра) лежат рядом с
`backend/` в корне проекта и не являются устанавливаемыми пакетами (у них нет
`pyproject.toml`/`setup.py`, но есть `__init__.py`). Поэтому корень проекта
добавляется в `sys.path` ровно один раз — при первом импорте этого модуля.

Импортировать этот модуль нужно **до** `import ar_board` / `import cv_classic`:

    from ..paths import PROJECT_ROOT, AR_BOARD_DIR  # noqa: F401  (побочный эффект)
    import ar_board

Альтернатива (если появятся wheel-пакеты): `pip install -e ../ar_board -e ../cv_classic`
и этот файл станет не нужен.
"""

from __future__ import annotations

import sys
from pathlib import Path

# backend/app/paths.py -> parents[0]=app, [1]=backend, [2]=корень проекта
BACKEND_DIR: Path = Path(__file__).resolve().parents[1]
PROJECT_ROOT: Path = BACKEND_DIR.parent

AR_BOARD_DIR: Path = PROJECT_ROOT / "ar_board"
CV_CLASSIC_DIR: Path = PROJECT_ROOT / "cv_classic"

DEFAULT_LAYOUT_PATH: Path = AR_BOARD_DIR / "out" / "layout.json"
DEFAULT_BOARD_IMAGE: Path = AR_BOARD_DIR / "out" / "board_A3.png"


def ensure_sys_path() -> Path:
    """Добавляет корень проекта и `ar_board/` в `sys.path` (идемпотентно).

    `ar_board/` нужен отдельной записью: на верхнем уровне лежит каталог
    `ar_board/` без `__init__.py` (namespace-пакет), и без этой записи
    `import ar_board` дал бы namespace-пакет, а не настоящий пакет
    `ar_board/ar_board/__init__.py`.
    """
    for path in (AR_BOARD_DIR, PROJECT_ROOT):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return PROJECT_ROOT


ensure_sys_path()

__all__ = [
    "AR_BOARD_DIR",
    "BACKEND_DIR",
    "CV_CLASSIC_DIR",
    "DEFAULT_BOARD_IMAGE",
    "DEFAULT_LAYOUT_PATH",
    "PROJECT_ROOT",
    "ensure_sys_path",
]
