"""Создаёт схему и сид-данные. Идемпотентно, можно запускать многократно.

Пример:
    python scripts/init_db.py
    $env:DATABASE_URL="postgresql+psycopg://ntec:<ПАРОЛЬ>@127.0.0.1:5432/ntec"
    python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.seed import seed_all  # noqa: E402


def mask_url(url: str) -> str:
    """Прячет пароль в строке подключения для логов."""
    if "@" not in url:
        return url
    prefix, suffix = url.split("@", 1)
    if "//" not in prefix:
        return url
    scheme, credentials = prefix.split("//", 1)
    if ":" not in credentials:
        return url
    user = credentials.split(":", 1)[0]
    return f"{scheme}//{user}:***@{suffix}"


def main() -> int:
    url = db.configure()
    db.init_db()
    result = seed_all()

    print(f"База данных: {mask_url(url)}")
    print(f"Диалект: {db.get_engine().dialect.name}")
    print(f"Демо-агрегат создан: {'да' if result['equipment_created'] else 'нет (уже был)'}")
    print("Таблицы:")
    for table, count in result["tables"].items():
        print(f"  {table:24s} {count:6d} строк")

    if db.get_engine().dialect.name == "postgresql":
        print(
            "PostgreSQL: JSONB-колонки и GIN-индексы созданы "
            "(telemetry_samples.payload, diagnostics.evidence)."
        )
    else:
        print(
            "SQLite fallback: схема совместима с Postgres, "
            "но JSONB/GIN работают только на PostgreSQL."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
