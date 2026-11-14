# API

Базовый URL: `http://<host>:8000`. Все ответы — JSON UTF-8; время — ISO-8601 UTC
с точностью до секунды. Аутентификации нет, CORS открыт (`*`): сервер рассчитан
на изолированную локальную сеть. Swagger — `/docs`, схема — `/openapi.json`.

Поля кадра (`/ws/frame`) — источник истины `docs/contract.md`; здесь — все
эндпоинты, коды ошибок, лимиты и сквозной пример.

## Сводка

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/` | подсказка по точкам входа |
| GET | `/health` | живость, диалект БД, детектор, пайплайн, число правил и строк |
| GET | `/equipment` | список оборудования |
| GET | `/equipment/{id}` | карточка, узлы, `baseline`, остаточный ресурс |
| GET | `/equipment/{id}/diagnostics` | активные, история, `blocked`, состояние камеры |
| GET | `/equipment/{id}/telemetry?minutes=&limit=` | история сигналов для графиков |
| GET | `/equipment/{id}/analytics` | полная сводка окна наблюдения: узлы, дефекты, подтверждение |
| POST | `/analytics/reset?equipment_id=` | очистить окно одного агрегата или все |
| GET | `/rules` | каталог 13 правил с человекочитаемыми условиями |
| POST | `/diagnostics/{id}/ack` | подтвердить событие |
| POST | `/inspections` | сохранить осмотр, снимок диагностики, `analytics` и `verdict` |
| GET | `/inspections?equipment_id=` | история осмотров с `analytics` и `verdict` |
| POST | `/analyze?equipment_id=` | фото из галереи через тот же пайплайн, что `/ws/frame` |
| GET | `/pipeline/layout` | текущий загруженный `layout.json` и его состояние |
| POST | `/pipeline/layout` | горячая подмена планшета и флага `defects` |
| POST | `/vision/check?equipment_id=&component_id=` | проверка узла локальной VLM |
| GET | `/vision/explain?equipment_id=` | заключение по фактам без изображений |
| GET | `/vision/status` | доступность Ollama и модели |
| POST | `/sim/scenario` | переключить сценарий симулятора |
| GET | `/sim/state` | сценарии, сигналы, диагностика по всем агрегатам |
| POST | `/sim/reset` | вернуть `normal`, снять диагностику, очистить окно |
| POST | `/sim/runtime` | задать наработку вручную |
| POST | `/sim/tick` | один принудительный тик |
| WS | `/ws/telemetry?equipment_id=` | телеметрия ~1 Гц |
| WS | `/ws/frame?equipment_id=` | JPEG → оверлеи, OCR, диагностика |

---

## GET `/`

```json
{
  "service": "NTec Industrial Diagnostics Backend",
  "docs": "/docs",
  "health": "/health",
  "rules": "/rules",
  "telemetry_ws": "/ws/telemetry?equipment_id=PUMP-001",
  "frame_ws": "/ws/frame?equipment_id=PUMP-001",
  "vision_check": "/vision/check?equipment_id=PUMP-001&component_id=pump",
  "vision_explain": "/vision/explain?equipment_id=PUMP-001",
  "vision_status": "/vision/status"
}
```

## GET `/health`

```json
{
  "status": "ok",
  "version": "0.4.0",
  "detector": "BoardDetector",
  "detector_mode": "board",
  "pipeline": {
    "enabled": true,
    "layout": "D:\\NTec-Хакатон\\ar_board\\out_real\\layout.json",
    "layout_revision": 0,
    "layout_components": 5,
    "layout_component_ids": ["motor", "coupling_guard", "pump", "flange", "manometer"],
    "gauge_ocr": true,
    "gauge_config": "D:\\NTec-Хакатон\\backend\\configs\\manometer_board.json",
    "gauge_config_loaded": true,
    "ocr_every": 10,
    "roi_size": 512,
    "defects_module": true,
    "defects_enabled": false
  },
  "database": "postgresql",
  "equipment_count": 1,
  "rules_count": 13,
  "simulator": {"enabled": true, "states": 1},
  "rows": {"equipment": 1, "components": 5, "inspections": 0,
           "inspection_detections": 0, "telemetry_samples": 1834,
           "diagnostics": 0, "maintenance_intervals": 3, "scenario_state": 1}
}
```

`detector`: `BoardDetector` | `FakeDetector` | `YoloDetector`. При
`DETECTOR=fake` вместо `pipeline` будет
`{"enabled": false, "reason": "detector не board"}`. `rows` — фактическое число
строк по всем таблицам.

## GET `/equipment`

```json
[
  {
    "id": "PUMP-001",
    "name": "Насосный агрегат",
    "model": "DEMO-PUMP",
    "serial": "DEMO-001",
    "year": 2021,
    "runtime_hours": 12450.5,
    "normative_life_years": 10.0,
    "last_service_date": "2026-04-18",
    "qr_code": "PUMP-001",
    "location": "Насосная станция №1, цех 3",
    "baseline": {"vibration_rms": 2.8, "temperature_bearing": 48.0, "pressure_inlet": 3.2},
    "components_count": 5
  }
]
```

`baseline` в примере сокращён; в реальном ответе 17 сигналов.

## GET `/equipment/{id}`

```json
{
  "id": "PUMP-001",
  "name": "Насосный агрегат",
  "model": "DEMO-PUMP",
  "serial": "DEMO-001",
  "year": 2023,
  "runtime_hours": 7400.0,
  "normative_life_years": 20,
  "last_service_date": null,
  "qr_code": "PUMP-001",
  "location": "Демонстрационный стенд",
  "baseline": {"vibration_rms": 1.0, "temperature_bearing": 50.0, "pressure_inlet": 0.3},
  "components": [
    {
      "id": "PUMP-001-coupling_guard",
      "equipment_id": "PUMP-001",
      "name": "coupling_guard",
      "kind": "component",
      "normative_life_years": 6.0,
      "installed_date": "2023-08-20",
      "condition": "check_required",
      "remaining_resource": {
        "value": 1.94,
        "status": "ok",
        "is_estimate": true,
        "basis": {
          "normative_life_years": 6.0,
          "installed_date": "2023-08-20",
          "condition": "check_required",
          "condition_factor": 0.7,
          "as_of": "2026-11-13",
          "formula": "max(0, normative_life_years - age_years) * condition_factor",
          "age_years": 3.23
        }
      }
    }
  ]
}
```

Узлы отсортированы в порядке классов: motor, pump, coupling_guard, flange,
manometer. `condition`: `ok` | `check_required` | `possible_defect`.
`remaining_resource.status`: `ok` | `exhausted` | `insufficient_data`,
`is_estimate` всегда `true`. Нет оборудования — 404.

## GET `/equipment/{id}/diagnostics`

Вызов пересчитывает правила и (для открытых событий) обновляет БД.

```json
{
  "equipment_id": "PUMP-001",
  "scenario": "gauge_drift",
  "evaluated_at": "2026-11-13T10:30:06+00:00",
  "activate_confidence": 0.65,
  "release_confidence": 0.45,
  "camera": {
    "available": false,
    "source": null,
    "age_sec": null,
    "ttl_sec": 5.0,
    "observations": 0,
    "ocr": null
  },
  "active": [
    {
      "id": 1,
      "equipment_id": "PUMP-001",
      "rule_id": "gauge_drift",
      "severity": "warning",
      "status": "active",
      "confidence": 1.0,
      "evidence": [
        {
          "condition": "|manometer_ocr − pressure_outlet| / |pressure_outlet| × 100 > 15 %",
          "signal": "manometer_ocr",
          "op": "deviation_pct",
          "actual": 21.9216,
          "baseline": 8.389,
          "threshold": 15.0,
          "matched": true,
          "weight": 1.0,
          "score": 1.0,
          "note": "manometer_ocr=6.55, pressure_outlet=8.389"
        }
      ],
      "message": "Показания аналогового манометра расходятся с давлением датчика более чем на 15 %.",
      "recommendation": "Расхождение показаний давления. Датчик требует поверки или продувки импульсной линии.",
      "overlay": {"component_id": "manometer", "marker": "badge",
                  "color": "orange", "label": "Поверка манометра"},
      "created_at": "2026-11-13T10:29:52+00:00",
      "updated_at": "2026-11-13T10:30:06+00:00",
      "resolved_at": null,
      "title": "Расхождение показаний манометра",
      "blocked_by": null,
      "blocked_reason": null
    }
  ],
  "history": [],
  "blocked": [
    {
      "rule_id": "guard_removed",
      "title": "Кожух муфты отсутствует",
      "severity": "critical",
      "blocked_by": "camera_unavailable",
      "reason": "нет свежих кадров с камеры (TTL истёк)"
    }
  ]
}
```

* `active` — открытые события (`status` = `active` | `acknowledged`),
  отсортированы по severity и confidence; в `evidence` — все условия, включая
  несовпавшие.
* `history` — до 50 закрытых (`resolved`) событий.
* `blocked` — только те правила, которые молчат из-за отсутствия данных;
  `blocked_by`: `camera_unavailable` | `ocr_unavailable` |
  `signal_missing:<signal>` | `insufficient_window` | `no_telemetry`.
* `camera.ocr` — последнее OCR с камеры; форма отличается от `ocr` в
  `/ws/frame`: `{value, unit, confidence, error, angle_deg, source,
  observed_at, age_sec, stale?}`.

## GET `/equipment/{id}/telemetry`

Параметры: `minutes` 1..1440 (по умолчанию 30), `limit` 10..20000 (по умолчанию
5000). Возвращаются последние `minutes` минут по возрастанию `ts`, не более
`limit` точек.

```json
{
  "equipment_id": "PUMP-001",
  "minutes": 1,
  "scenario": "gauge_drift",
  "count": 10,
  "signals": ["current_a", "current_b", "current_c", "efficiency", "flow",
              "manometer_ocr", "noise_db", "oil_color", "oil_level",
              "pressure_inlet", "pressure_outlet", "runtime_hours",
              "temperature_bearing", "temperature_motor", "vibration_axial",
              "vibration_hf", "vibration_lf_1x", "vibration_rms"],
  "latest": {
    "ts": "2026-11-13T10:30:33+00:00",
    "scenario": "gauge_drift",
    "signals": {
      "vibration_rms": 2.728, "temperature_bearing": 47.94,
      "pressure_inlet": 3.186, "pressure_outlet": 8.294,
      "efficiency": 77.7, "oil_color": "light",
      "manometer_ocr": 6.55
    }
  },
  "samples": [
    {
      "ts": "2026-11-13T10:30:23+00:00",
      "scenario": "gauge_drift",
      "signals": {"vibration_rms": 2.801, "pressure_outlet": 8.435,
                  "manometer_ocr": 6.55}
    }
  ]
}
```

`samples` в примере сокращены; `latest` — последний элемент `samples`.
`manometer_ocr` появляется только когда его задаёт сценарий или камера.

## GET `/rules`

```json
{
  "version": "1.1",
  "activate_confidence": 0.65,
  "release_confidence": 0.45,
  "rules_count": 13,
  "rules": [
    {
      "id": "gauge_drift",
      "title": "Расхождение показаний манометра",
      "severity": "warning",
      "description": "OCR показаний аналогового манометра расходится с давлением датчика более чем на 15 %.",
      "message": "Показания аналогового манометра расходятся с давлением датчика более чем на 15 %.",
      "recommendation": "Расхождение показаний давления. Датчик требует поверки или продувки импульсной линии.",
      "conditions": [
        {
          "text": "|manometer_ocr − pressure_outlet| / |pressure_outlet| × 100 > 15 %",
          "op": "deviation_pct",
          "weight": 1.0
        }
      ],
      "requires_cv": false,
      "requires_ocr": true,
      "dedupe_group": null,
      "specificity": 0,
      "latch_sec": 0.0,
      "overlay": {"component_id": "manometer", "marker": "badge",
                  "color": "orange", "label": "Поверка манометра"},
      "tags": ["ocr", "pressure", "metrology"]
    }
  ]
}
```

`conditions[].text` — человекочитаемая форма; полные правила — в
`backend/rules/rules.yaml`.

## POST `/inspections`

Тело:

```json
{
  "equipment_id": "PUMP-001",
  "detections": [
    {"class_id": 0, "class_name": "motor", "kind": "component",
     "confidence": 0.93, "bbox": [0.11, 0.22, 0.44, 0.61]},
    {"class_id": 6, "class_name": "corrosion", "kind": "defect",
     "confidence": 0.71, "bbox": [0.2, 0.3, 0.28, 0.38]}
  ],
  "frame_id": 17,
  "image_width": 1280,
  "image_height": 720,
  "inference_ms": 24
}
```

Правила валидации: `class_id` 0..9, `class_name` из фиксированного списка,
`kind` совпадает с классом (`0–4` — `component`, `5–9` — `defect`; `9` —
`oxidation`), `bbox` нормализован 0..1, `x1 < x2`, `y1 < y2`. `source` клиент не
присылает; в сохранённом `summary` он будет `model`. Ответ `201`:

```json
{
  "id": 1,
  "equipment_id": "PUMP-001",
  "created_at": "2026-11-13T10:30:26+00:00",
  "components_status": [
    {"component_id": "motor", "status": "possible_defect", "confidence": 0.93,
     "defects": ["corrosion"], "bbox": [0.11, 0.22, 0.44, 0.61]},
    {"component_id": "pump", "status": "insufficient_data", "confidence": 0.0,
     "defects": [], "bbox": null}
  ],
  "diagnostics": [
    {"id": 1, "rule_id": "gauge_drift", "severity": "warning",
     "status": "acknowledged", "confidence": 1.0}
  ],
  "analytics": {
    "equipment_id": "PUMP-001",
    "window_sec": 60.0,
    "confirm_ratio": 0.3,
    "confirm_frames": 3,
    "frames": 40,
    "duration_sec": 11.4,
    "components": [
      {"component_id": "pump", "label": "Насос", "frames_total": 40,
       "frames_seen": 36, "visibility_ratio": 0.9,
       "last_seen_ago_sec": 0.4, "status_window": "ok",
       "confidence": 0.0, "defects": []},
      {"component_id": "manometer", "label": "Манометр", "frames_total": 40,
       "frames_seen": 0, "visibility_ratio": 0.0,
       "last_seen_ago_sec": null, "status_window": "insufficient_data",
       "confidence": 0.0, "defects": []}
    ],
    "confirmed_defects": 0,
    "unconfirmed_defects": 0
  },
  "verdict": {
    "status": "insufficient_data",
    "reason": "Недостаточно данных: узлы не наблюдались достаточно долго — Манометр.",
    "components_with_issues": [],
    "components_not_seen": ["manometer"],
    "disclaimer": "Отсутствие видимых дефектов не подтверждает исправность оборудования."
  }
}
```

`components_status` и `analytics` в примере сокращены. Побочный эффект:
`components.condition` обновляется статусом узла, кроме `insufficient_data`;
полная сводка окна пишется в `inspections.analytics_json`.

## GET `/inspections`

`equipment_id` — необязательный фильтр. Без пагинации, новые сверху. Элемент:

```json
{
  "id": 1,
  "equipment_id": "PUMP-001",
  "created_at": "2026-11-13T10:30:26+00:00",
  "summary": {
    "frame_id": 17,
    "image_width": 1280,
    "image_height": 720,
    "inference_ms": 24,
    "detections": [
      {"class_id": 0, "class_name": "motor", "kind": "component",
       "confidence": 0.93, "bbox": [0.11, 0.22, 0.44, 0.61], "source": "model"}
    ],
    "components_status": [{"component_id": "motor", "status": "possible_defect",
                           "confidence": 0.93, "defects": ["corrosion"],
                           "bbox": [0.11, 0.22, 0.44, 0.61]}],
    "diagnostics": [],
    "verdict": {"status": "insufficient_data", "reason": "...",
                "components_with_issues": [],
                "components_not_seen": ["manometer"],
                "disclaimer": "..."}
  },
  "diagnostics": [],
  "analytics": {"equipment_id": "PUMP-001", "window_sec": 60.0,
                "frames": 0, "duration_sec": 0.0, "components": [],
                "confirmed_defects": 0, "unconfirmed_defects": 0},
  "verdict": {"status": "insufficient_data", "reason": "...",
              "components_with_issues": [],
              "components_not_seen": ["manometer"],
              "disclaimer": "..."}
}
```

`summary` и верхний `diagnostics` — снимок на момент осмотра. `analytics` —
сохранённая сводка окна (для старых записей — `{}`), `verdict` — сохранённый в
`summary.verdict` либо пересчитанный на лету по `analytics` и составу узлов.

## GET `/equipment/{id}/analytics`

Полная сводка окна наблюдения (`app/analytics.py`). Окно — 60 с по умолчанию,
агрегаты по секундным бакетам; кадры не хранятся. Каждый узел оценивается
независимо.

```json
{
  "equipment_id": "PUMP-001",
  "window_sec": 60.0,
  "confirm_ratio": 0.3,
  "confirm_frames": 3,
  "frames": 40,
  "duration_sec": 11.4,
  "observation_started_at": "2026-11-13T14:05:54+00:00",
  "updated_at": "2026-11-13T14:06:05+00:00",
  "components": [
    {
      "component_id": "coupling_guard",
      "label": "Кожух муфты",
      "frames_total": 40,
      "frames_seen": 40,
      "visibility_ratio": 1.0,
      "last_seen_at": "2026-11-13T14:06:05+00:00",
      "last_seen_ago_sec": 0.4,
      "status_window": "possible_defect",
      "confidence": 0.504,
      "defects": [
        {
          "class_name": "corrosion",
          "frames_present": 40,
          "frames_seen_component": 40,
          "presence_ratio": 1.0,
          "mean_confidence": 0.504,
          "max_confidence": 0.504,
          "first_seen_at": "2026-11-13T14:05:54+00:00",
          "last_seen_at": "2026-11-13T14:06:05+00:00",
          "confirmed": true
        }
      ],
      "confirmed_defects": ["corrosion"],
      "unconfirmed_defects": [],
      "note": "виден 40 из 40 кадров; подтверждено: коррозия"
    }
  ],
  "confirmed_defects": 1,
  "unconfirmed_defects": 0
}
```

* `status_window`: `ok` | `check_required` | `possible_defect` |
  `insufficient_data` — см. `docs/architecture.md`, §8;
* дефект подтверждён при `presence_ratio ≥ 0.3` и `frames_present ≥ 3`;
* пустое окно: `frames=0`, `duration_sec=0.0`, `components=[]`,
  `confirmed_defects=0`, `unconfirmed_defects=0`.

## POST `/analytics/reset`

`POST /analytics/reset?equipment_id=PUMP-001` очищает окно одного агрегата;
без параметра — все окна. Ответ:

```json
{"reset": true, "equipment_id": "PUMP-001",
 "analytics": {"equipment_id": "PUMP-001", "window_sec": 60.0,
               "confirm_ratio": 0.3, "confirm_frames": 3, "frames": 0,
               "duration_sec": 0.0, "components": [],
               "confirmed_defects": 0, "unconfirmed_defects": 0}}
```

Для сброса всех `analytics: null`. Неизвестный `equipment_id` — 404. Окно также
сбрасывается при `POST /sim/reset` и при успешной смене layout.

## POST `/analyze?equipment_id=PUMP-001`

Фото из галереи проходит тот же `BoardPipeline`, что и кадр `/ws/frame`.
Запрос — `multipart/form-data`, поле `image` (JPEG или PNG), `equipment_id` —
query-параметр (по умолчанию `PUMP-001`). Ответ повторяет структуру кадра WS
(`board`, `overlays`, `ocr`, `detections`, `components_status`,
`active_diagnostics`, `blocked_diagnostics`, `analytics`, `vision`,
`inference_ms`) плюс `equipment_id`, `image_width`, `image_height`. Поля
`frame_id` нет.

```powershell
curl.exe -X POST "http://127.0.0.1:8000/analyze?equipment_id=PUMP-001" `
  -F "image=@ar_board/out_real/board_A3.png"
```

```json
{
  "equipment_id": "PUMP-001",
  "image_width": 4961,
  "image_height": 3508,
  "board": {"detected": true, "markers": 4, "marker_ids": [0, 1, 2, 3],
            "reprojection_error": 0.31, "coverage": 0.74,
            "confidence": 0.977, "valid": true,
            "method": "homography+pnp", "reason": null,
            "frame_size": [4961, 3508]},
  "overlays": [
    {"component_id": "manometer", "label": "Манометр",
     "polygon": [[3580.4, 980.1], [3910.2, 980.1], [3910.2, 1310.7], [3580.4, 1310.7]],
     "visible": true, "status": "ok", "color": "green",
     "confidence": 0.977, "board_confidence": 0.977,
     "defects": [], "area_px": 5321.4}
  ],
  "ocr": {"value": 8.383, "unit": "бар", "confidence": 0.837, "error": null,
          "refreshed": true, "age_frames": 0},
  "detections": [
    {"class_id": 4, "class_name": "manometer", "kind": "component",
     "confidence": 0.977, "bbox": [0.654, 0.332, 0.713, 0.460], "source": "board"}
  ],
  "components_status": [
    {"component_id": "manometer", "status": "ok", "confidence": 0.977,
     "defects": [], "bbox": [0.654, 0.332, 0.713, 0.460]}
  ],
  "active_diagnostics": [],
  "blocked_diagnostics": [
    {"rule_id": "guard_removed", "severity": "critical",
     "blocked_by": "camera_unavailable"}
  ],
  "analytics": {"window_sec": 60.0, "frames": 1, "duration_sec": 0.0,
                "components": []},
  "vision": null,
  "inference_ms": 262
}
```

Побочные эффекты те же, что у кадра WS: детекции и OCR попадают в `cv_state`,
окно аналитики обновляется, `GET /equipment/{id}/diagnostics` сразу видит
результат. Файл читается в память, временные файлы не пишутся.

| Ситуация | Ответ |
|---|---|
| Файл больше 15 МБ | `413`, `detail`: «Файл больше 15 МБ…» |
| Пустой файл | `422`, «Пустой файл: ожидается изображение JPEG или PNG…» |
| Не изображение / битый файл / не JPEG-PNG | `422`, «Файл не является изображением или повреждён…» |
| Неизвестный `equipment_id` | `404`, «Оборудование не найдено» |
| `DETECTOR=fake` | `200`, `board.reason="detector_fake"`, `overlays=[]` |

## GET `/pipeline/layout`

Состояние **работающего** `BoardPipeline`: какой `layout.json` загружен, сколько
зон, когда файл менялся, есть ли OCR манометра и включены ли эвристики.

```json
{
  "path": "D:\\NTec-Хакатон\\ar_board\\out_real\\layout.json",
  "exists": true,
  "layout_version": 1,
  "revision": 0,
  "components": 5,
  "component_ids": ["motor", "coupling_guard", "pump", "flange", "manometer"],
  "mtime": "2026-11-13T09:52:36+00:00",
  "mtime_epoch": 1794563556.873,
  "file_size": 5409,
  "equipment_id": "PUMP-001",
  "board": {"image": "board_A3.png", "width_px": 4961, "height_px": 3508},
  "gauge_ocr": true,
  "defects_enabled": false
}
```

`revision` — счётчик подмен в текущем процессе (стартует с 0, каждый успешный
`POST` +1). `gauge_ocr=false` — зоны `manometer` нет: `ocr` в кадре будет
`null`, а `gauge_drift` — `blocked_by="ocr_unavailable"`.

## POST `/pipeline/layout`

Горячая подмена планшета без рестарта и без разрыва активных `/ws/frame`.
Тело:

```json
{"path": "../ar_board/out_defect/layout.json", "defects": true}
```

`defects`: `true` — включить эвристики `cv_classic.defects` (дефектный
планшет), `false` — выключить (чистый), поле отсутствует — сохранить текущее
значение (`CV_DEFECTS` или прошлый вызов).

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/pipeline/layout `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"path":"../ar_board/out_defect/layout.json","defects":true}'
```

Относительный путь резолвится **от корня проекта** (родитель `backend/`), а не
от cwd; дополнительно поддерживается база `backend/`. Абсолютный путь
принимается как есть. Успешный ответ — `200` и то же состояние, что у `GET`,
плюс `"reloaded": true`. Подмена атомарна: файл читается и валидируется до
захвата блокировки, кадр всегда считается по цельному layout.

| Ситуация | Ответ |
|---|---|
| Файл не найден / это каталог | `404`, «layout.json не найден: <абсолютный путь>» |
| Битый JSON | `422`, «layout.json невалиден: …» |
| Нет `board`/`markers` или `components` пуст | `422` |
| Зона без `component_id` / меньше 3 точек `polygon_px` | `422` |
| `DETECTOR=fake` (пайплайна планшета нет) | `409`, «AR-пайплайн планшета недоступен…» |

Неудачный `POST` ничего не меняет. После переключения на `out_defect`:
`overlays` — 4 зоны (`motor`, `coupling_guard`, `pump`, `flange`), `ocr=null`.
Смена планшета сбрасывает окно аналитики; смена только `defects` — нет.

## POST `/vision/check?equipment_id=PUMP-001&component_id=pump`

Проверка узла локальной VLM через Ollama. `component_id` обязателен и должен
быть узлом из карточки (`pump`, `motor`, `flange`, …), иначе `422`. Тело —
`multipart/form-data`, поле `image` (JPEG/PNG) **необязательно**: без файла
анализируется последний кадр `/ws/frame` или `/analyze`.

```json
{
  "component_id": "pump",
  "verdict": "defect",
  "signs": {"corrosion": true, "paint_peeling": true,
            "oil_leak": null, "mechanical_damage": null},
  "confidence": 0.9,
  "comment": "видны следы коррозии и отслоение краски",
  "surface": "поверхность оборудования покрыта ржавчиной",
  "model": "qwen2.5vl:3b",
  "latency_ms": 1405,
  "grounded": true,
  "error": null,
  "cached": false,
  "suppressed_by": null,
  "checked_at": "2026-11-13T13:23:40+00:00",
  "hypotheses": ["тревожных признаков нет, датчики в норме"]
}
```

* `verdict`: любой `true` → `defect`; все `false` → `no_defect`; есть `null`
  и нет `true` → `insufficient`;
* `signs` — строго четыре признака; `null` = «не видно / мало качества»;
* `grounded=false` — в `comment` есть числа, которых не было во входных данных;
  ответ не отбрасывается, только помечается;
* `error`: `null` | `vision_disabled` | `timeout` | `ollama_unavailable` |
  `model_not_found` | `invalid_json` | `empty_response` | `http_error` |
  `no_frame` | `bad_image`; `model_not_found` обычно приходит из
  `/vision/status`, а вызов без модели даёт `ollama_unavailable`/`http_error`;
* `cached=true`, `latency_ms=0` — ответ из кэша по хэшу изображения (TTL 10 мин);
* при `verdict=defect` создаётся диагностика `visual_signs` (severity
  `warning`); если по узлу уже активно `visual_defect`, более уверенное
  остаётся, второе получает `suppressed_by`.

## GET `/vision/explain?equipment_id=PUMP-001`

Итоговое заключение **только по фактам** (изображения не передаются):
телеметрия, активные диагностики с evidence, последняя VLM-проверка,
паспорт/остаточный ресурс.

```json
{
  "summary": "Насосный агрегат ... 3–4 предложения по-русски ...",
  "severity": "warning",
  "actions": ["Проверить систему смазки ..."],
  "facts_used": ["vibration_hf > 22.7 × baseline.vibration_hf", "..."],
  "model": "qwen2.5vl:3b",
  "latency_ms": 3073,
  "error": null
}
```

`severity`: `info` | `warning` | `critical`; если факты содержат warning/
critical-диагностику, модель не может занизить severity. Если в `summary`
появилось число, которого нет в фактах, сервер подставляет детерминированное
заключение и отдаёт `error="ungrounded"`. При недоступной модели возвращается
детерминированное заключение по фактам (правила + ресурс) и `error`.

## GET `/vision/status`

```json
{"enabled": true, "available": true, "model": "qwen2.5vl:3b",
 "url": "http://127.0.0.1:11434", "last_response_ms": 1405,
 "last_ok_at": "2026-11-13T13:23:40+00:00", "last_error": null,
 "error": null, "models": ["qwen2.5vl:3b"]}
```

`available=false` + `error="model_not_found"` — Ollama жива, но модели нет;
`error="ollama_unavailable"` — не отвечает сам сервер Ollama;
`error="vision_disabled"` — `VISION_ENABLED=false`.

## POST `/diagnostics/{id}/ack`

Без тела. Переводит `active` в `acknowledged` и возвращает обогащённую строку
события (`title`, `blocked_by`, `blocked_reason`). `acknowledged` повторно
подтверждать можно; `resolved` — 409.

```json
{
  "id": 1,
  "equipment_id": "PUMP-001",
  "rule_id": "gauge_drift",
  "severity": "warning",
  "status": "acknowledged",
  "confidence": 1.0,
  "evidence": [{"op": "deviation_pct", "actual": 21.5193, "baseline": 8.346,
                "threshold": 15.0, "matched": true, "weight": 1.0, "score": 1.0}],
  "message": "Показания аналогового манометра расходятся с давлением датчика более чем на 15 %.",
  "recommendation": "Расхождение показаний давления. Датчик требует поверки или продувки импульсной линии.",
  "overlay": {"component_id": "manometer", "marker": "badge",
              "color": "orange", "label": "Поверка манометра"},
  "created_at": "2026-11-13T10:29:52+00:00",
  "updated_at": "2026-11-13T10:30:17+00:00",
  "resolved_at": null,
  "title": "Расхождение показаний манометра",
  "blocked_by": null,
  "blocked_reason": null
}
```

`evidence` в примере сокращён (в реальном ответе — все условия).

## POST `/sim/scenario`

```json
{"equipment_id": "PUMP-001", "scenario": "gauge_drift"}
```

Допустимо: `normal`, `bearing_wear`, `cavitation`, `misalignment`,
`impeller_wear`, `seal_leak`, `looseness`, `lubrication_degradation`,
`electrical_contact`, `gauge_drift`, `guard_removed`. Сразу делает один тик.
Ответ — `{"state": {...}}`:

```json
{
  "state": {
    "equipment_id": "PUMP-001",
    "scenario": "gauge_drift",
    "scenario_label": "Дрейф манометра",
    "started_at": "2026-11-13T10:29:41+00:00",
    "elapsed_sec": 0.02,
    "runtime_hours": 12451.5,
    "signals": {
      "vibration_rms": 2.819, "temperature_bearing": 48.31,
      "pressure_inlet": 3.224, "pressure_outlet": 8.315,
      "efficiency": 77.93, "oil_color": "light"
    },
    "active_diagnostics": [],
    "camera": {"available": false, "source": null, "age_sec": null,
               "ttl_sec": 5.0, "observations": 0, "ocr": null}
  }
}
```

`signals` сокращены (17 сигналов). Неизвестный сценарий — 422 со списком
допустимых; неизвестный агрегат — 404.

## GET `/sim/state`

```json
{
  "tick_sec": 1.0,
  "runtime_hours_per_sec": 0.02,
  "scenarios": [{"id": "normal", "label": "Норма"},
                {"id": "gauge_drift", "label": "Дрейф манометра"}],
  "states": [
    {"equipment_id": "PUMP-001", "scenario": "normal", "scenario_label": "Норма",
     "started_at": "2026-11-13T10:30:33+00:00", "elapsed_sec": 0.02,
     "runtime_hours": 12452.54, "signals": {"vibration_rms": 2.652},
     "active_diagnostics": [], "camera": {"available": false, "ttl_sec": 5.0}}
  ],
  "ws_subscribers": 0
}
```

`scenarios` сокращён (11 сценариев), `signals` — один сигнал для примера.

## POST `/sim/reset`

Тело необязательно: `{}` — сбросить все агрегаты, `{"equipment_id":
"PUMP-001"}` — один, `{"clear_history": true}` — дополнительно удалить
телеметрию. Возвращает полный `/sim/state`, снимает открытую диагностику и
чистит CV-канал.

## POST `/sim/runtime`

```json
{"equipment_id": "PUMP-001", "runtime_hours": 3999.9}
```

Задаёт наработку, делает тик и возвращает `{"state": {...}}`. Через ~5 с
симулятор (0.02 ч/с) пересечёт 4000 — сработает `maintenance_due`.

## POST `/sim/tick`

Один тик для всех агрегатов. Ответ:

```json
{
  "payloads": [
    {
      "ts": "2026-11-13T10:31:00+00:00",
      "equipment_id": "PUMP-001",
      "scenario": "normal",
      "signals": {"vibration_rms": 2.7, "pressure_outlet": 8.4},
      "camera": {"available": false, "ttl_sec": 5.0},
      "active_diagnostics": [],
      "blocked_diagnostics": []
    }
  ],
  "state": {"tick_sec": 1.0, "runtime_hours_per_sec": 0.02,
            "scenarios": [], "states": [], "ws_subscribers": 0}
}
```

`payloads` — то же, что уходит в `/ws/telemetry`; `state` — полный `/sim/state`
(в примере сокращён).

---

## WebSocket `/ws/telemetry?equipment_id=PUMP-001`

Сервер принимает соединение, сразу отправляет последний снимок (при
необходимости делает тик), затем шлёт по одному сообщению на тик симулятора
(~1 Гц). Если `SIM_ENABLED=false`, при отсутствии сообщений 3 с сервер делает
тик вручную — частота остаётся ~1 кадр/3 с.

```json
{
  "ts": "2026-11-13T07:48:01+00:00",
  "equipment_id": "PUMP-001",
  "scenario": "bearing_wear",
  "signals": {
    "vibration_rms": 3.42, "vibration_axial": 1.2, "vibration_hf": 1.51,
    "vibration_lf_1x": 0.93, "temperature_bearing": 53.1,
    "temperature_motor": 52.4, "current_a": 42.0, "current_b": 41.5,
    "current_c": 42.5, "pressure_inlet": 3.18, "pressure_outlet": 8.39,
    "flow": 63.2, "efficiency": 77.6, "runtime_hours": 12451.0,
    "oil_level": 71.8, "oil_color": "light", "noise_db": 73.8
  },
  "camera": {"available": false, "source": null, "age_sec": null,
             "ttl_sec": 5.0, "observations": 0, "ocr": null},
  "active_diagnostics": [],
  "blocked_diagnostics": []
}
```

`active_diagnostics[]` — те же объекты, что в `GET /diagnostics` →
`active`; `blocked_diagnostics[]` — `{rule_id, title, severity, blocked_by,
reason}`. Очередь подписчика — 10 сообщений: при отставании клиента старые
вытесняются. Неизвестный `equipment_id` — закрытие с кодом `1008`,
`reason="unknown_equipment"`.

## WebSocket `/ws/frame?equipment_id=PUMP-001`

Клиент отправляет **бинарный** JPEG (или PNG — сервер декодирует Pillow), на
каждый кадр получает один JSON. Частота задаётся клиентом: приложение шлёт 4
кадра/с (`250 мс`, `ResolutionPreset.medium`). Сервер не ограничивает частоту;
практический предел — ~16.6 кадр/с на одно соединение с JPEG-декодом и
правилами. Лимит размера сообщения — uvicorn (по умолчанию 16 МиБ), в
приложении своего лимита нет.

Ответ (пример из `docs/contract.md`, кадр 1280×720):

```json
{
  "frame_id": 12,
  "equipment_id": "PUMP-001",
  "image_width": 1280,
  "image_height": 720,
  "board": {
    "detected": true,
    "markers": 4,
    "marker_ids": [0, 1, 2, 3],
    "reprojection_error": 0.30,
    "coverage": 0.68,
    "confidence": 0.977,
    "valid": true,
    "method": "homography+pnp",
    "reason": null,
    "frame_size": [1280, 720],
    "corners": [[63.8, 35.9], [1228.4, 28.6], [1215.9, 690.8], [76.9, 676.8]]
  },
  "overlays": [
    {
      "component_id": "manometer",
      "label": "Манометр",
      "polygon": [[838.2, 239.1], [912.6, 239.1], [912.6, 331.5], [838.2, 331.5]],
      "visible": true,
      "status": "ok",
      "color": "green",
      "confidence": 0.977,
      "board_confidence": 0.977,
      "defects": [],
      "area_px": 5321.4
    }
  ],
  "ocr": {
    "value": 8.383,
    "raw_value": 8.383,
    "unit": "бар",
    "confidence": 0.837,
    "error": null,
    "angle_deg": 56.2,
    "method": "hough+histogram",
    "image_size": [512, 512],
    "refreshed": true,
    "age_frames": 0
  },
  "detections": [
    {"class_id": 4, "class_name": "manometer", "kind": "component",
     "confidence": 0.977, "bbox": [0.654, 0.332, 0.713, 0.460],
     "source": "board"}
  ],
  "components_status": [
    {"component_id": "manometer", "status": "ok", "confidence": 0.977,
     "defects": [], "bbox": [0.654, 0.332, 0.713, 0.460]}
  ],
  "active_diagnostics": [
    {"id": 41, "rule_id": "gauge_drift", "title": "Расхождение показаний манометра",
     "severity": "warning", "status": "active", "confidence": 1.0,
     "message": "...", "recommendation": "...",
     "evidence": [{"op": "deviation_pct", "actual": 22.64, "baseline": 8.467,
                   "threshold": 15.0, "matched": true, "weight": 1.0, "score": 1.0}],
     "overlay": {"component_id": "manometer", "marker": "badge",
                 "color": "orange", "label": "Поверка манометра"},
     "created_at": "...", "updated_at": "...", "resolved_at": null}
  ],
  "blocked_diagnostics": [
    {"rule_id": "guard_removed", "title": "Кожух муфты отсутствует",
     "severity": "critical", "blocked_by": "camera_unavailable",
     "reason": "нет свежих кадров с камеры (TTL истёк)"}
  ],
  "analytics": {
    "window_sec": 60,
    "frames": 42,
    "duration_sec": 18.5,
    "components": [
      {"component_id": "pump", "label": "Насос",
       "visibility_ratio": 0.86, "frames_seen": 36, "frames_total": 42,
       "last_seen_ago_sec": 0.4, "status_window": "ok", "confidence": 0.61,
       "defects": [{"class_name": "corrosion", "presence_ratio": 0.52,
                    "mean_confidence": 0.61, "confirmed": true,
                    "first_seen_at": "2026-11-13T14:05:54+00:00",
                    "last_seen_at": "2026-11-13T14:06:05+00:00"}],
       "note": "виден 36 из 42 кадров"}
    ]
  },
  "vision": {"component_id": "pump", "verdict": "defect", "signs": {},
             "confidence": 0.9, "error": null},
  "inference_ms": 24
}
```

`analytics` — компактный поузловый блок (полная сводка —
`GET /equipment/{id}/analytics`); `vision` — последняя успешная VLM-проверка по
этому агрегату или `null`. Оба поля добавлены в конец ответа, прежние поля не
менялись.

Семантика полей — в `docs/contract.md`. Важное:

* `polygon` — 4 точки в пикселях кадра, порядок TL→TR→BR→BL; `overlays[].color`
  уже готов (`green`/`yellow`/`red`/`grey`).
* `ocr` — результат пайплайна: `refreshed=false` и `age_frames>0` между
  чтениями (каждый 10-й кадр), `stale=true`, если планшет в кадре не найден и
  показано последнее значение. Ошибки OCR: `no_dial`, `no_needle`,
  `needle_too_short`, `low_confidence`, `bad_image`, `bad_config`,
  `roi_failed`, `ocr_failed`.
* `source` детекций: `board` | `model` | `fake`.
* `inference_ms` — время сервера на кадр без учёта пересчёта правил.

**Ошибки кадра.** Текст вместо JPEG:

```json
{"frame_id": 0, "equipment_id": "PUMP-001", "error": "binary_frame_required",
 "detail": "Отправьте JPEG как бинарное WebSocket-сообщение"}
```

Битый JPEG — `error="invalid_image"`, `board.detected=false`,
`board.reason="invalid_image"`, `overlays=[]`, `ocr=null`, `inference_ms=0`.
Планшет не найден — HTTP-код не меняется: `board.detected=false`,
`overlays=[]`, `reason` = `no_markers` | `partial` | `low_quality` | `error`,
`ocr.stale=true`. При `DETECTOR=fake` — `reason="detector_fake"`, оверлеев нет.

**`equipment_id` во `/ws/frame` проверяется** (как и в `/ws/telemetry`):
для несуществующего id соединение принимается и закрывается кодом `1008`,
`reason="unknown_equipment"`. Используйте id из `GET /equipment`.

---

## Ошибки REST

| Код | Когда | Тело |
|---|---|---|
| 404 | нет оборудования | `{"detail":"Оборудование не найдено"}` |
| 404 | нет диагностики при ack | `{"detail":"Диагностика не найдена"}` |
| 409 | ack уже закрытого события | `{"detail":"Событие уже закрыто"}` |
| 422 | не прошла pydantic-валидация | `{"detail":[{"type":"...","loc":["body",0,"bbox"],"msg":"..."}]}` |
| 422 | неизвестный сценарий | `{"detail":"неизвестный сценарий 'x'; допустимо: normal, bearing_wear, ..."}` |
| 422 | `minutes`/`limit` вне диапазона | массив `detail`, как выше |
| 500 | непойманное исключение | стандартный ответ FastAPI |

Аутентификации и кодов 401/403 нет. Ошибки WS: `1008 unknown_equipment` — для
`/ws/telemetry` и `/ws/frame` при неизвестном `equipment_id`.

## Лимиты и TTL

| Что | Значение |
|---|---|
| `GET /telemetry` `minutes` | 1..1440, по умолчанию 30 |
| `GET /telemetry` `limit` | 10..20000, по умолчанию 5000 |
| Окно телеметрии для правил | 90 с, до 600 точек |
| Окно видеоаналитики (`ANALYTICS_WINDOW_SEC`) | 60 с |
| Подтверждение дефекта | `presence_ratio ≥ 0.3`, `frames_present ≥ 3` |
| Узел «вне кадра» | 5 с без наблюдения (`COMPONENT_ABSENT_SEC`) |
| Минимальная видимость для «нормы» | 0.6 (иначе «норма» невозможна) |
| `POST /analyze`, `POST /vision/check` | файл ≤ 15 МБ, JPEG/PNG |
| VLM: таймаут запроса | 60 с, один повтор при сетевой ошибке |
| VLM: кэш ответа | 10 мин по хэшу изображения, до 128 записей |
| VLM: вход | длинная сторона ≤ 1280 px, JPEG q90 |
| История закрытых диагнозов | 50 |
| `/inspections` | без пагинации, все записи |
| TTL камеры и OCR (`CV_TTL_SEC`) | 5 с |
| OCR манометра | раз в `GAUGE_OCR_EVERY` = 10 кадров, между чтениями кэш |
| Хранение телеметрии | `TELEMETRY_RETENTION_MIN` = 180 мин (0 — не чистить) |
| Тик симулятора | `SIM_TICK_SEC` = 1.0 с |
| Ускорение наработки | `SIM_RUNTIME_HOURS_PER_SEC` = 0.02 ч/с (1 моточас ≈ 50 с) |
| WS-очередь телеметрии | 10 сообщений, старые вытесняются |

---

## Сквозной сценарий curl (PowerShell 5.1)

`curl.exe` в Windows — настоящий curl. В PowerShell 5.1 кавычки в JSON режутся,
поэтому для POST используется `--%` (stop-parsing) либо файл тела. Команды
проверены на этом проекте.

```powershell
# 1. Живость и карточка
curl.exe -s http://127.0.0.1:8000/health
curl.exe -s http://127.0.0.1:8000/equipment
curl.exe -s http://127.0.0.1:8000/equipment/PUMP-001

# 2. Сценарий дрейфа манометра и ожидание срабатывания (~15 с)
curl.exe --% -s -X POST http://127.0.0.1:8000/sim/scenario -H "Content-Type: application/json" -d "{\"equipment_id\":\"PUMP-001\",\"scenario\":\"gauge_drift\"}"
Start-Sleep -Seconds 20

# 3. Диагностика: active[] с evidence и blocked[]
curl.exe -s http://127.0.0.1:8000/equipment/PUMP-001/diagnostics

# 4. Сохранение осмотра: тело через файл (кириллица/кавычки не страдают)
$body = @'
{"equipment_id":"PUMP-001","detections":[{"class_id":0,"class_name":"motor","kind":"component","confidence":0.93,"bbox":[0.11,0.22,0.44,0.61]}],"frame_id":17,"image_width":1280,"image_height":720,"inference_ms":24}
'@
Set-Content -Path "$env:TEMP\inspection.json" -Value $body -Encoding utf8
curl.exe -s -X POST http://127.0.0.1:8000/inspections -H "Content-Type: application/json" --data-binary "@$env:TEMP\inspection.json"

# 5. История осмотров
curl.exe -s "http://127.0.0.1:8000/inspections?equipment_id=PUMP-001"

# 6. Подтвердить событие (id из ответа diagnostics, здесь 1)
curl.exe -s -X POST http://127.0.0.1:8000/diagnostics/1/ack

# 7. Окно аналитики и AI-проверка узла (Ollama должна быть запущена)
curl.exe -s http://127.0.0.1:8000/equipment/PUMP-001/analytics
curl.exe -s http://127.0.0.1:8000/vision/status
curl.exe -s -X POST "http://127.0.0.1:8000/vision/check?equipment_id=PUMP-001&component_id=pump" `
  -F "image=@ar_board/out_real/board_A3.png"
curl.exe -s http://127.0.0.1:8000/vision/explain?equipment_id=PUMP-001

# 8. Переключение планшета на дефектный и обратно
curl.exe --% -s -X POST http://127.0.0.1:8000/pipeline/layout -H "Content-Type: application/json" -d "{\"path\":\"../ar_board/out_defect/layout.json\",\"defects\":true}"
curl.exe --% -s -X POST http://127.0.0.1:8000/pipeline/layout -H "Content-Type: application/json" -d "{\"path\":\"../ar_board/out_real/layout.json\",\"defects\":false}"

# 9. Сброс демо (окно аналитики чистится вместе с симулятором)
curl.exe --% -s -X POST http://127.0.0.1:8000/sim/reset -H "Content-Type: application/json" -d "{}"
```

Быстрая проверка «правило сработало» без телефона:

```powershell
curl.exe --% -s -X POST http://127.0.0.1:8000/sim/scenario -H "Content-Type: application/json" -d "{\"equipment_id\":\"PUMP-001\",\"scenario\":\"guard_removed\"}"
Start-Sleep -Seconds 3
curl.exe -s http://127.0.0.1:8000/equipment/PUMP-001/diagnostics
```

`guard_removed` не зависит от датчиков и появляется сразу после тика — но
только если в этот момент **не идёт поток кадров с телефона**: кадр заменяет
синтетическое CV-наблюдение симулятора, и правило гаснет. Для показа с живой
камерой используйте `gauge_drift` (его OCR приходит в телеметрии) или
сенсорные сценарии (`bearing_wear`, `cavitation`, …).
`gauge_drift` требует OCR: в сценарии симулятора — ~10–15 с; с камеры —
только если OCR реально прочитал манометр (см. `docs/runbook.md`, раздел про
калибровку).
