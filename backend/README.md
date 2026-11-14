# Backend — NTec Inspector

FastAPI-сервер мобильного помощника инженера: приём кадров, маркерless-привязка
планшета, OCR манометра, видеоаналитика по узлам, движок правил, локальная VLM
и хранение осмотров.

## Стек

Python 3.10+, FastAPI, SQLAlchemy 2.0, Pydantic 2, PyYAML, Pillow, NumPy,
OpenCV (contrib), PyTorch, ONNX Runtime, psycopg v3. Тесты — pytest.

## Быстрый старт

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

По умолчанию `.env.example` использует SQLite — PostgreSQL не обязателен.
Для PostgreSQL раскомментируйте строку `DATABASE_URL` в `.env` и создайте БД.

Проверка: `http://127.0.0.1:8000/health`.

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | SQLite или PostgreSQL (`postgresql+psycopg://...`) |
| `LAYOUT_PATH` | layout планшета, по умолчанию `../ar_board/out_real/layout.json` |
| `ALIGN_MODE` | `features` — без маркеров, `aruco` — резервный режим |
| `ALIGN_REFERENCES` | `../ar_board/references.json` |
| `CV_DEFECTS` | эвристики `cv_classic.defects` |
| `ANOMALY_*` | PatchCore v2: включение, устройство, порог |
| `CORROSION_*` | YOLO11n ONNX: включение, провайдеры, порог |
| `SIM_ENABLED` | симулятор телеметрии |
| `VISION_ENABLED`, `OLLAMA_*` | локальная VLM через Ollama |

Полный список — `app/config.py` и `docs/runbook.md`.

## Что внутри

- `app/cv/` — пайплайн кадра, привязка, зоны, OCR, каналы аномалий/коррозии.
- `app/diagnostics/` — каталог правил, evaluator, сервис диагностик.
- `app/vision/` — клиент Ollama, проверка кадра и заключение по фактам.
- `app/simulator.py` — симулятор телеметрии (11 сценариев).
- `app/db.py`, `app/models.py` — SQLAlchemy-модели и миграции.
- `rules/rules.yaml` — 13 правил с evidence.
- `configs/manometer_board.json` — калибровка манометра.
- `data/seed_rig.json` — обезличенные демо-карточки PUMP-001/PUMP-002.
- `tests/` — pytest.

## Данные

Реальные данные заказчика в репозиторий не входят и на сервере хранятся в
PostgreSQL. В `data/seed_rig.json` лежит обезличенный демонстрационный набор:
два агрегата, пять узлов, базовые значения для симулятора. Паспортные номера,
модели, адреса и точные нормативы заказчика удалены.

## API

Основные группы:

- `GET /health`, `GET /equipment`, `GET /equipment/{id}`;
- `GET /equipment/{id}/diagnostics`, `GET /equipment/{id}/telemetry`;
- `POST /analyze`, `WS /ws/frame`, `WS /ws/telemetry`;
- `GET /vision/status`, `POST /vision/check`, `GET /vision/explain`;
- `POST /inspections`, `GET /inspections`;
- `POST /sim/scenario`, `GET /rules`.

Подробно — `docs/api.md`, контракт — `docs/contract.md`.

## Тесты

```powershell
python -m pytest -q
```

Покрыты пайплайн, правила, аналитика, API, vision-клиент, два агрегата,
горячая подмена layout и маркерless-привязка.

## Ограничения

- Привязка без маркеров требует, чтобы планшет занимал заметную часть кадра.
- Детектор аномалий на демо-стенде обучен на малом числе кадров нормы.
- VLM — подтверждающий канал, не замена правилам и инженеру.
- Телеметрия в демо — симулятор.

Подробнее — `docs/limitations.md`, `docs/architecture.md`, `docs/runbook.md`.
