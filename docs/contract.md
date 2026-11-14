# Контракт `/ws/frame`: что рисует мобильный клиент

Версия: 2 (совместима с версией 1 — все прежние поля сохранены).
Сервер: `backend/app/main.py`, пайплайн: `backend/app/cv/pipeline.py`.

Клиент отправляет **бинарное** WebSocket-сообщение с JPEG и получает один JSON
на кадр. Никакой геометрии и OCR на клиенте нет: он рисует `overlays` как есть.

```
ws://<host>:8000/ws/frame?equipment_id=PUMP-001
```

## Ответ (кадр 1280×720, планшет найден)

```json
{
  "frame_id": 12,
  "equipment_id": "PUMP-001",
  "image_width": 1280,
  "image_height": 720,

  "board": {
    "detected": true,
    "markers": 0,
    "marker_ids": [],
    "all_marker_ids": [],
    "board_id": "PUMP-001",
    "equipment_id": "PUMP-001",
    "equipment_mismatch": false,
    "hint": null,
    "identity_marker_id": null,
    "identity_detected": false,
    "auto_switched": false,
    "board_registry": true,
    "method": "features",
    "inliers": 482,
    "inlier_ratio": 0.7555,
    "stale": false,
    "age_sec": 0.0,
    "reprojection_error": 0.98,
    "coverage": 0.76,
    "confidence": 0.959,
    "valid": true,
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
    {
      "class_id": 4, "class_name": "manometer", "kind": "component",
      "confidence": 0.977, "bbox": [0.654, 0.332, 0.713, 0.460],
      "source": "board"
    }
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

  "inference_ms": 24
}
```

## Поля, которые клиент обязан отрисовать

| Поле | Как использовать |
|---|---|
| `board.detected` | `false` → показать подсказку «наведите камеру на планшет целиком» |
| `board.reason` | причина: `no_match` (feature-привязка не нашла эталон), `no_markers`, `partial` (видно <3 маркеров), `low_quality`, `identity_missing`, `detector_fake`, `invalid_image`, `error` |
| `board.board_id` | идентификатор планшета = id оборудования: `PUMP-001` (чистый) / `PUMP-002` (дефектный) |
| `board.equipment_id` | всегда равен `board.board_id`: какое оборудование напечатано на планшете в кадре |
| `board.equipment_mismatch` | `true` — планшет в кадре не совпадает с `equipment_id` запроса (`/ws/frame`, `/analyze`) |
| `board.hint` | при несовпадении — строка «в кадре другой агрегат: PUMP-002», иначе `null` |
| `board.method` | `features` (ORB-привязка без маркеров) или `homography+pnp` (маркерный fallback) |
| `board.inliers` / `inlier_ratio` | качество feature-совпадения (для `features`) |
| `board.stale` / `age_sec` | поза удержана (`hold_last_pose` до 1.5 с) или свежая |
| `board.auto_switched` | `true` только на кадре, где пайплайн сам подменил layout |
| `overlays[].polygon` | массив `[x, y]` **в пикселях кадра** (`image_width`×`image_height`), 4 точки, порядок TL→TR→BR→BL. Рисовать замкнутый полигон |
| `overlays[].visible` | если `false` — полигон может выходить за кадр, рисовать серым/не рисовать |
| `overlays[].status` | `ok` \| `check_required` \| `possible_defect` \| `insufficient_data` — подпись узла |
| `overlays[].color` | готовый цвет: `green` \| `yellow` \| `red` \| `grey` |
| `overlays[].label` | русская подпись зоны (`Манометр`, `Кожух муфты`, …) |
| `ocr.value` + `ocr.unit` | крупная плашка «8.4 бар»; `null` → «нет показания» + `ocr.error` |
| `ocr.confidence` | индикатор достоверности OCR (0..1) |
| `ocr.refreshed` / `age_frames` | значение обновляется раз в N кадров; показать «обновлено N кадров назад» |
| `ocr.stale` | `true` — планшет в текущем кадре не найден, показано последнее известное значение (можно приглушить) |
| `active_diagnostics[]` | список событий: `severity` (`critical`/`warning`/`info`), `title`, `message`, `recommendation`, `evidence`, `overlay` |
| `active_diagnostics[].overlay` | дополнительный маркер на узле: `component_id`, `marker` (`highlight`/`outline`/`badge`), `color`, `label` |
| `blocked_diagnostics[]` | почему правило молчит: `blocked_by` — см. таблицу ниже |
| `components_status[]` | статусы узлов из БД-состава агрегата (может отличаться от 5 зон планшета) |
| `inference_ms` | время обработки кадра на сервере |

## Два объекта: PUMP-001 и PUMP-002

На защите два разных агрегата и две разные QR-метки:

| Объект | Карточка | Планшет | QR |
|---|---|---|---|
| `PUMP-001` | «Насосный агрегат», все узлы `ok` | `ar_board/out_real` (5 зон) | `ar_board/qr/PUMP-001.png` |
| `PUMP-002` | «Насосный агрегат (дефектный участок)», `pump` и `coupling_guard` — `possible_defect` | `ar_board/out_defect` (4 зоны, без манометра) | `ar_board/qr/PUMP-002.png` |

* `GET /equipment` — обе карточки; `GET /equipment/PUMP-002` — 5 узлов с
  дефектными состояниями; `GET /equipment?qr=PUMP-002` — фильтр по QR-коду
  (совпадает с `id`).
* `ar_board/references.json` — эталоны с `board_id` `PUMP-001` и `PUMP-002`;
  `board_id` в ответе API всегда равен идентификатору оборудования.
* `ar_board/qr/qr_card.html` — показ QR с экрана: клавиши **1** / **2**
  переключают исправный и дефектный объекты, клик — полный экран.
* Сид (`backend/data/seed_rig.json`) идемпотентен: повторный запуск не
  дублирует ни оборудование, ни узлы, ни регламентные интервалы.

Сверка планшета с запросом: если в кадре `PUMP-002`, а запрошен `PUMP-001`,
ответ содержит `board.equipment_id="PUMP-002"`,
`board.equipment_mismatch=true`, `board.hint="в кадре другой агрегат: PUMP-002"`.
Аналитика и диагностика при этом ведутся по **запрошенному**
`equipment_id` — планшет не подменяет объект, клиент лишь показывает
предупреждение.

## Привязка без маркеров (feature-режим)

По умолчанию `ALIGN_MODE=features`: планшеты без ArUco. Эталоны берутся из
`ar_board/references.json` (PUMP-001/PUMP-002), уменьшаются до ширины 1600 px,
ORB-дескрипторы считаются один раз при старте и кэшируются в памяти (и в
`backend/data/align_cache/`). На каждом кадре:

1. кадр регистрируется относительно эталонов; выбирается лучший по
   `(inliers, confidence)`;
2. `layout` планшета подменяется атомарно, окно аналитики/`Smoother`/кэши
   сбрасываются;
3. регистрация выполняется раз в `ALIGN_EVERY=2` кадра; между вызовами
   используется последняя гомография (`stale=true`, `age_sec`) до 1.5 с;
4. если совпадения нет и поза старше 1.5 с — `board.detected=false`,
   `reason=no_match`, `overlays=[]`, узлы `insufficient_data`.

`ALIGN_MODE=aruco` — старый маркерный режим (планшеты из
`ar_board/out_real_markers`, `out_defect_markers`); `auto` — сначала features,
затем маркерный fallback. `/health → pipeline.align {mode, references, every}`.
`GET /pipeline/layout` → `alignment` и `align`.

## Автоопределение планшета по маркерам (fallback)

Результат не зависит от того, какой `layout.json` прописан в конфиге: на
каждом планшете наклеен **identity-маркер** (`DICT_4X4_50`: id `7` — чистый,
id `8` — дефектный). Пайплайн на каждом кадре:

1. находит ArUco-маркеры и позу планшета;
2. отдельным проходом ищет identity-маркеры из реестра
   `ar_board/boards.json`;
3. по реестру определяет `board_id` и целевой `layout.json`;
4. если целевой layout отличается от текущего и identity-маркер стабилен
   (`identity_stable_frames=3` подтверждения в окне 5 кадров), **атомарно**
   подменяет layout — как `POST /pipeline/layout`: сбрасываются `Smoother`,
   кэш OCR, кэш аномалий/коррозии и окно видеоаналитики;
5. если identity-маркера нет (старый планшет, обрезан кадр), текущий layout
   сохраняется, `board.identity_detected=false`; для планшета нового
   поколения (layout задаёт `board.identity_marker_id`, реестр загружен)
   `board.detected=false`, `board.reason="identity_missing"`, но кадр
   обрабатывается без ошибок.

Гистерезис защищает от «дёрганья» на каждом кадре:

* для стабильного планшета переключение происходит на **3-м** кадре подряд;
* при чередовании чистый/дефектный layout меняется не чаще, чем раз в 3
  кадра (после переключения пауза 3 кадра);
* ручной `POST /pipeline/layout` — override: он сбрасывает историю, после
  него автопереключение снова требует 3 подтверждений;
* `auto_switched=true` приходит только на кадре фактической подмены.

Реестр отсутствует/битый — graceful fallback: `board.board_registry=false`,
автопереключение выключено, работает только ручной `POST` (как раньше).

### Возможные значения `blocked_by`

| Значение | Когда выставляется | Что это значит для клиента |
|---|---|---|
| `camera_unavailable` | CV-правило ждёт свежий кадр, но кадров нет или последнему больше `CV_TTL_SEC` (5 с) | «Наведите камеру» / камера не подключена; правило не может ни сработать, ни подтвердить норму |
| `ocr_unavailable` | Правило ждёт OCR аналогового прибора (`gauge_drift`), а значения нет: манометр не прочитан (например, `ocr.error=no_needle`) или OCR устарел | Показать «нет показания манометра»; сенсорное значение при этом может быть в норме |
| `no_telemetry` | У агрегата вообще нет сэмплов телеметрии в окне правила | Нет данных с датчиков; ждать первый тик симулятора/шлюза |
| `signal_missing:<signal>` | В сэмплах нет конкретного сигнала (например, `signal_missing:noise_db`) | Датчик не отдаёт сигнал; проверьте состав телеметрии |
| `insufficient_window` | Для тренда/пересечения порога мало точек (нужно ≥5 и ≥60 % окна) | Подождать накопления данных |

`reason` — человекочитаемое пояснение к `blocked_by` (можно показывать как есть).

`detections[].source` — происхождение данных на защите: `board` (AR-планшет),
`model` (YOLO), `fake` (заглушка). `detections[].bbox` — нормализованные
координаты 0..1 (`[x1, y1, x2, y2]`), клиенту обычно не нужны, если есть `overlays`.

### Приоритет наблюдений: камера и сценарий

Движок правил видит единый CV-снимок, в который сливаются два источника:

1. **Кадры камеры** (`/ws/frame`, `source=board`/`model`/`fake`).
2. **Синтетические подсказки сценария** (`/sim/scenario`, `source=simulator`).

Правила слияния (реализация — `backend/app/cv_state.py`):

* для одной пары «класс + узел» побеждает наблюдение с **большей уверенностью**,
  поэтому реальная детекция камеры имеет приоритет, но не гасит сценарную
  подсказку, если та увереннее;
* классы, которые камера не наблюдает, сохраняются из сценария и живут по
  общему TTL (`CV_TTL_SEC`), а не обновляются каждым кадром камеры;
* поток кадров без маркеров (пустые `detections`) не стирает активные сценарные
  наблюдения — например, `guard_removed` продолжает гореть на демо с камерой;
* смена сценария (`/sim/scenario`) и `/sim/reset` очищают оба слоя.

## Ошибки

| Ситуация | Ответ |
|---|---|
| Отправлен текст вместо JPEG | `{"error": "binary_frame_required", ...}` без `detections` |
| Битый JPEG | `{"error": "invalid_image", "board": {"detected": false, "reason": "invalid_image"}, "overlays": [], "ocr": null, ...}` |
| Планшет не найден | HTTP-код не меняется; `board.detected=false`, `overlays=[]`, остальные поля на месте |
| `DETECTOR=fake` | `board.reason="detector_fake"`, `overlays=[]`, `detections[].source="fake"`, дефектов не бывает |
| Нет оборудования | соединение закрывается с кодом `1008`, `reason="unknown_equipment"` (и для `/ws/frame`, и для `/ws/telemetry`) |

## `POST /analyze`: фото из галереи

Мобильное приложение может выбрать фото из галереи и прогнать его через тот же
`BoardPipeline`, что и кадр `/ws/frame`. Запрос — `multipart/form-data`, поле
`image` (JPEG или PNG), `equipment_id` — query-параметр (по умолчанию
`PUMP-001`). Ответ повторяет структуру кадра WebSocket (`board`, `overlays`,
`ocr`, `detections`, `components_status`, `active_diagnostics`,
`blocked_diagnostics`, `inference_ms`) плюс `equipment_id`, `image_width`,
`image_height`.

Побочные эффекты те же, что у кадра из WS: детекции и OCR попадают в
`cv_state`, поэтому `GET /equipment/{id}/diagnostics` сразу видит результат
(правила `gauge_drift`, `guard_removed`, `corrosion` и т.д.).

```bash
curl.exe -X POST "http://127.0.0.1:8000/analyze?equipment_id=PUMP-001" \
  -F "image=@ar_board/out_real/board_A3.png"
```

Ответ (сокращён, кадр `4961×3508`, планшет найден):

```json
{
  "equipment_id": "PUMP-001",
  "image_width": 4961,
  "image_height": 3508,
  "board": {
    "detected": true,
    "markers": 4,
    "marker_ids": [0, 1, 2, 3],
    "board_id": "PUMP-001",
    "equipment_id": "PUMP-001",
    "equipment_mismatch": false,
    "hint": null,
    "reprojection_error": 0.31,
    "coverage": 0.74,
    "confidence": 0.977,
    "valid": true,
    "method": "homography+pnp",
    "reason": null,
    "frame_size": [4961, 3508]
  },
  "overlays": [
    {"component_id": "manometer", "label": "Манометр",
     "polygon": [[3580.4, 980.1], [3910.2, 980.1], [3910.2, 1310.7], [3580.4, 1310.7]],
     "visible": true, "status": "ok", "color": "green",
     "confidence": 0.977, "board_confidence": 0.977,
     "defects": [], "area_px": 5321.4}
  ],
  "ocr": {
    "value": 8.383, "unit": "бар", "confidence": 0.837, "error": null,
    "refreshed": true, "age_frames": 0
  },
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
  "inference_ms": 262
}
```

Кадр без маркеров (любое фото) возвращает `200`, `board.detected=false`,
`board.reason` (`no_markers`, `partial`, `low_quality`) и `overlays=[]` — как и
`/ws/frame`.

| Ситуация | Ответ |
|---|---|
| Файл больше 15 МБ | `413`, `detail`: «Файл больше 15 МБ…» |
| Не изображение / битый файл / не JPEG-PNG | `422`, `detail`: «Файл не является изображением или повреждён…» |
| Неизвестный `equipment_id` | `404`, `detail`: «Оборудование не найдено» |
| `DETECTOR=fake` | `200`, `board.reason="detector_fake"`, `overlays=[]` |

Файл обрабатывается в памяти (`io.BytesIO`), временные файлы на диск не
пишутся.

## `GET/POST /pipeline/layout`: переключение планшета на лету

На защите два распечатанных планшета: чистый (`ar_board/out_real`, 5 зон) и
дефектный (`ar_board/out_defect`, 4 зоны, без манометра). Переключение не
требует рестарта сервера и не рвёт активные `/ws/frame`-сессии.

### `GET /pipeline/layout`

Возвращает состояние **работающего** `BoardPipeline`:

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
  "board": {"image": "board_A3.png", "width_px": 4961, "height_px": 3508,
            "board_id": "PUMP-001", "identity_marker_id": null, "guard_marker_id": 4},
  "board_id": "PUMP-001",
  "identity_marker_id": null,
  "guard_marker_id": 4,
  "board_registry": {"path": "D:\\NTec-Хакатон\\ar_board\\boards.json",
                     "available": true, "boards": []},
  "gauge_ocr": true,
  "defects_enabled": true
}
```

* `path` — абсолютный путь к загруженному `layout.json`;
* `components` / `component_ids` — число и id зон (именно они попадут в
  `overlays` следующего кадра);
* `layout_version` — поле `version` из самого файла, `revision` — счётчик
  подмен в текущем процессе (стартует с 0, каждый успешный `POST` +1);
* `mtime` / `mtime_epoch` / `file_size` — метаданные файла на диске;
* `gauge_ocr` — есть ли зона `manometer` (при `false` поле `ocr` в кадре будет
  `null`, а правило `gauge_drift` — `blocked_by="ocr_unavailable"`);
* `defects_enabled` — включены ли сейчас эвристики `cv_classic.defects`
  (ручной флаг `CV_DEFECTS`, меняется полем `defects` в `POST`; по умолчанию
  `true` — визуальные каналы работают всегда, флаг нужен только для отладки);
* `board_id` / `identity_marker_id` — какой планшет сейчас загружен и какой
  маркер его идентифицирует (из `layout.board` или реестра `boards.json`);
* `board_registry` — путь, доступность и содержимое реестра планшетов
  (если `available=false`, автопереключение выключено).

### `POST /pipeline/layout`

Тело — JSON с путём к другому `layout.json` и необязательным флагом
визуальных эвристик:

```json
{"path": "../ar_board/out_defect/layout.json", "defects": true}
```

`defects`:
* `true` — включить эвристики (по умолчанию и так `true`);
* `false` — выключить **только для отладки**; визуальные каналы
  (YOLO-коррозия, аномалии, проверка кожуха) и корректность чистого планшета
  от этого флага не зависят;
* поле отсутствует — сохранить текущее значение (`CV_DEFECTS` или прошлый
  вызов).

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/pipeline/layout `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"path":"../ar_board/out_defect/layout.json","defects":true}'
```

Относительный путь резолвится **не от cwd**, а от корня проекта (родитель
`backend/`); дополнительно поддерживается база `backend/`, поэтому команды
вида `../ar_board/...` работают при любом рабочем каталоге. Абсолютный путь
принимается как есть.

Успешный ответ — `200` и то же состояние, что у `GET`, плюс `"reloaded": true`.
Подмена атомарна: файл читается и валидируется до захвата блокировки, кадр
всегда считается по цельному layout (старому или новому), `Smoother`, кэш
OCR и флаг `defects` применяются вместе. Формат `/ws/frame` и `/analyze` не
меняется — меняется число и состав `overlays`, а также наличие дефектов.

Ручной `POST` — **override**, а не единственный способ смены планшета: после
него автопереключение по identity-маркеру продолжает работать. История
гистерезиса очищается, поэтому если в кадре другой планшет, layout сменится
только после 3 подтверждений (см. «Автоопределение планшета»).

После переключения на `out_defect`:

* `overlays` — 4 зоны (`motor`, `coupling_guard`, `pump`, `flange`), `manometer`
  отсутствует;
* `ocr` — `null` (зоны манометра в layout нет);
* если после этого прислать фото **чистого** планшета, `board.detected=true`,
  но `overlays` по-прежнему 4 — сервер рисует то, что задано в layout.

| Ситуация | Ответ |
|---|---|
| Файл не найден / это каталог | `404`, `detail`: «layout.json не найден: <абсолютный путь>» |
| Битый JSON | `422`, `detail`: «layout.json невалиден: …» |
| Нет `board`/`markers` или `components` пуст | `422`, `detail`: «layout.json невалиден: …» |
| Зона без `component_id` / меньше 3 точек `polygon_px` | `422` |
| `DETECTOR=fake` (пайплайна планшета нет) | `409`, `detail`: «AR-пайплайн планшета недоступен…» |

Неудачный `POST` ничего не меняет: `GET /pipeline/layout` продолжает отдавать
прежний файл и `revision`.

## Визуальные дефекты и правило `visual_defect`

Визуальные каналы (эвристики `cv_classic.defects`, YOLO-коррозия, аномалии
anomaly/v2, маркерная проверка кожуха) работают **всегда** и не выключаются
флагом `defects`: флаг — только ручной выключатель эвристик для отладки
(по умолчанию `CV_DEFECTS=true`). Поэтому чистый планшет действительно
проверяется, а не пропускается.

Пайплайн прогоняет `cv_classic.defects` по ROI каждого видимого узла, где в
зоне есть фотография поверхности (на схематичном белом фоне эвристики
пропускаются — иначе отдельные штрихи рисунка дают ложное «масло»). ArUco-
маркеры перед визуальными каналами затираются: чёрный квадрат маркера — не
дефект поверхности. Если детектор вернул `present=true` (порог задан в
`cv_classic/configs/defects.json`), в ответе появляется детекция дефекта:

```json
{
  "class_id": 9, "class_name": "oxidation", "kind": "defect",
  "confidence": 0.546, "bbox": [0.02, 0.17, 0.42, 0.7], "source": "board",
  "component_id": "motor",
  "evidence": {
    "detector": "oxidation", "class": "oxidation", "score": 0.546,
    "threshold": 0.52, "area_px": 866214.14,
    "component_label": "Электродвигатель",
    "features": {"darkening": 0.81, "parts": {"area": 0.4}, "...": "..."}
  }
}
```

Соответствие классов: `oil` → `oil_leak`, `rust` → `corrosion`,
`oxidation` → `oxidation` (добавлен в конец `CLASS_NAMES`, индексы прежних
классов не изменились). `confidence` = score детектора; `component_id` —
узел, к которому отнесён дефект (bbox узла, а не «самый уверенный сосед»).

Статус узла в `components_status` / `overlays[].status` считается как раньше
(`possible_defect` ≥ 0.60, `check_required` ≥ 0.35): на печатном дефектном
планшете окисление мотора даёт `check_required` и жёлтую рамку.

Правило `visual_defect` (`rules/rules.yaml`, `dynamic: visual_defect`)
замыкает цепочку «жёлтая рамка → диагноз с текстом»:

* срабатывает по любой свежей детекции `oil_leak` / `corrosion` / `oxidation` /
  `crack` / `guard_damage` на узле с `confidence ≥ 0.5` (порог правила;
  фактический порог детектора — `present` из конфига);
* `confidence = clamp(mean(score) × (1 + 0.10 × (k − 1)), 0, 1)`, где `k` —
  число разных классов на узле; у правила собственный
  `activate_confidence: 0.5` (эвристики слабее датчиков);
* severity: один класс — `warning`, два и более классов на одном узле —
  `critical` (цвет overlay `orange` / `red`); если окно видеоаналитики ещё не
  подтвердило дефект — `info` и текст «единичное наблюдение»;
* `message`: «Визуальные признаки дефекта: <классы по-русски> на узле
  <название>»; `recommendation` собирается по классам (коррозия, подтёк,
  окисление, трещина, кожух);
* `evidence[]` — по каждому детектору узла: `class`, `score`, `threshold`,
  `area_px`, `features` (вклад признаков из `DefectScore`), `component`;
* `visual_defect` подавляется более специфичными диагнозами, если те активны:
  `seal_leak`, `mechanical_looseness`, `electrical_contact`, `guard_removed`
  (`suppressed_by_rules`). Подавление не срабатывает, пока специфичное правило
  ниже своего порога активации.

Если окно видеоаналитики **подтвердило** дефект (`confirmed=true`,
`presence_ratio ≥ 0.3` и `frames_present ≥ 3`), диагностика активируется
независимо от покадрового `confidence` (активация — по окну, а не по кадру).
Confidence события — `mean_confidence` окна; severity `warning`, а при ≥2
подтверждённых классах на узле — `critical`. Текст содержит наблюдение:
«…Подтверждено по 12 из 18 кадров.».

В ответе `/analyze` и `/ws/frame` поля только добавляются: у дефектных
детекций появляются `component_id` и `evidence`, у обычных узлов формат
прежний.

## Видеоаналитика: окно наблюдения

Помимо решения по текущему кадру сервер ведёт скользящее окно (по умолчанию
60 с, `ANALYTICS_WINDOW_SEC`). В окне хранятся только агрегаты: число кадров,
видимость узлов и статистика дефектов. В `/ws/frame` и `/analyze` добавлен
компактный блок, существующие поля не меняются:

```json
"analytics": {
  "window_sec": 60,
  "frames": 42,
  "duration_sec": 18.5,
  "components": [
    {
      "component_id": "pump",
      "label": "Насос",
      "visibility_ratio": 0.86,
      "frames_seen": 36,
      "frames_total": 42,
      "last_seen_ago_sec": 0.4,
      "status_window": "ok",
      "confidence": 0.61,
      "defects": [
        {"class_name": "corrosion", "presence_ratio": 0.52,
         "mean_confidence": 0.61, "confirmed": true,
         "first_seen_at": "2026-11-13T14:05:54+00:00",
         "last_seen_at": "2026-11-13T14:06:05+00:00"}
      ],
      "signs": [
        {"sign": "corrosion", "label": "Коррозия",
         "status": "detected", "sources": ["heuristics"],
         "confidence": 0.61, "presence_ratio": 0.52,
         "frames_present": 18, "first_seen_ago_sec": 22.4,
         "last_seen_ago_sec": 0.3, "trend": "stable",
         "area_pct": 12.0}
      ],
      "note": "виден 36 из 42 кадров"
    }
  ]
}
```

Блок `/ws/frame` и `/analyze` теперь **поузловый**: у каждого узла свой
`status_window`, посчитанный по окну, а не по одному кадру. `defects[]`
содержит все классы (и подтверждённые, и нет) с флагом `confirmed`.
Поле `confirmed_defects` из компактного блока удалено — это единственное
изменение формата для мобильного клиента.

Формулы:

```
visibility_ratio = frames_seen / frames_total
presence_ratio   = frames_present / frames_seen_component
mean_confidence  = Σ confidence / frames_present
```

Дефект подтверждён, если `presence_ratio >= ANALYTICS_CONFIRM_RATIO` (0.3) и
`frames_present >= ANALYTICS_CONFIRM_FRAMES` (3). Пороги и окно меняются без
правки кода.

`status_window` (каждый узел оценивается независимо):

| Условие | Статус |
|---|---|
| `frames_seen == 0` | `insufficient_data`, note «узел не встречался в окне» |
| `last_seen_ago_sec > 5` | `insufficient_data`, note «узел вне кадра N с» |
| есть `confirmed=true` | `possible_defect` |
| есть неподтверждённый дефект или `visibility_ratio < 0.5` | `check_required` |
| иначе | `ok` |

`insufficient_data` одного узла не влияет на остальные: узлы, которые видны,
получают свой `ok`/`check_required`/`possible_defect`.

### Визуальные признаки (`signs[]`)

В каждом узле есть `signs[]` — единый реестр признаков из
`app/visual_signs.py`. Один признак может приходить из нескольких источников,
но в ответе это один элемент:

| `sign` | Название | Источники | Правило |
|---|---|---|---|
| `corrosion` | Коррозия | heuristics (rust), vlm | `visual_defect` |
| `oil_leak` | Подтёк масла | heuristics (oil), vlm | `visual_defect` |
| `oxidation` | Окисление контактов | heuristics | `visual_defect` |
| `paint_peeling` | Отслоение краски | vlm | `visual_defect` |
| `mechanical_damage` | Механическое повреждение | vlm | `visual_defect` |
| `crack` | Трещина/скол | vlm, detector (задел) | `visual_defect` |
| `texture_anomaly` | Аномалия текстуры | anomaly (anomaly/v2) | `visual_defect` |
| `guard_missing` | Кожух муфты отсутствует | marker (ArUco id 4 в зоне `coupling_guard`) | `guard_removed` |

Статусы:

| Условие | `status` |
|---|---|
| узел не виден в окне (`frames_seen == 0`) | `not_visible` |
| `presence_ratio >= 0.3` и `frames_present >= 3` | `detected` |
| были срабатывания, но подтверждение не набрано | `single_observation` |
| срабатываний не было, узел виден | `not_detected` |

`sources` — кто реально дал признак в окне (`heuristics`, `vlm`, `marker`);
`confidence` — максимум по источникам; `presence_ratio` считается по кадрам,
где признак дал хотя бы один источник. `trend` сравнивает число срабатываний
в первой и второй половине окна: `growing`, `stable`, `fading`.

`not_visible` и `not_detected` — разные вещи: первое означает «узел не
попал в кадр, проверить нельзя», второе — «узел виден, признак не найден».
Если источник недоступен (VLM выключена, маркера нет в кадре), это не
`not_detected`, а `not_visible`.

Маркерная проверка: `cv_classic.presence.check_marker_in_roi(frame,
guard_marker_id, полигон coupling_guard)`, где `guard_marker_id` берётся из
`layout.board.guard_marker_id` (по умолчанию 4). Если маркер не найден в зоне
— `guard_missing` с confidence 0.9; если найден — признак не публикуется
(`not_detected`). Проверка **не зависит от флага `defects`**: на чистом
планшете маркер id 4 наклеен → `guard_missing = not_detected`, на дефектном
маркера нет → `detected`. Если `guard_marker_id: null`, используется id 4 —
отсутствие маркера и есть признак снятого кожуха.

### Детектор аномалий `anomaly/v2`

Дополнительный канал считает отклонение текстуры узла от эталонной модели:
`app/cv/anomaly_channel.py` + `anomaly/v2` (канонический кроп 512×512 по той
же гомографии ArUco). Модель грузится **лениво** при первом кадре; ошибка
(нет torch/модели/битый файл) выключает канал, сервер продолжает работать.

* `texture_anomaly` («Аномалия текстуры») — признак реестра, источник
  `anomaly`, `confidence = min(1, score / threshold)`;
* считается раз в `ANOMALY_EVERY` (5) кадров только для видимых узлов,
  между вызовами отдаётся кэш; смена layout сбрасывает кэш;
* в `signs[]` и `evidence[]` попадают `score`, `area`, `threshold`;
* env: `ANOMALY_ENABLED` (true), `ANOMALY_MODEL_DIR`
  (`../anomaly/v2/models`), `ANOMALY_THRESHOLD_QUANTILE` (0.99 → q99-порог),
  `ANOMALY_THRESHOLD_FACTOR` (1.3 — запас против доменного сдвига),
  `ANOMALY_EVERY` (5);
* `/health` → `pipeline.anomaly: {enabled, loaded, reason, device, every,
  threshold_quantile, threshold_factor, model_dir}`. До первого кадра
  `loaded=false`, `reason=not_loaded`; при `ANOMALY_ENABLED=false` —
  `enabled=false`, `reason=disabled`.

### Детектор коррозии `corrosion/` (ONNX)

Второй ONNX-канал: `app/cv/corrosion_channel.py`. Для каждого видимого узла
полигон переводится перспективным преобразованием в квадрат 640×640, модель
`corrosion/models/corrosion_yolo11n_640.onnx` ищет боксы, они возвращаются в
координаты кадра. `area_pct` — доля площади узла, покрытая объединением
боксов (верхняя оценка: боксы, не маски).

* признак реестра — `corrosion`, источник `yolo`; при слиянии с эвристиками и
  VLM остаётся один элемент, `sources` перечисляет всех;
* `confidence` — максимальная уверенность боксов; `area_pct` передаётся в
  `signs[]` и `evidence[]`;
* `area_pct >= 5%` → `detected` (при подтверждении окном), `0.5–5%` →
  `single_observation`, `< 0.5%` не публикуется;
* `verdict.reason` содержит площадь: «Коррозия (12% площади)»;
* env: `CORROSION_ENABLED` (true), `CORROSION_MODEL_PATH`
  (`../corrosion/models/corrosion_yolo11n_640.onnx`), `CORROSION_CONF` (0.25),
  `CORROSION_EVERY` (5), `CORROSION_MIN_AREA_PCT` (0.5);
* считается раз в N кадров, кэш между вызовами, сброс при смене layout;
* ошибка/нет onnxruntime/модели → канал выключается, сервер работает;
* `/health` → `pipeline.corrosion: {enabled, loaded, reason, every, conf,
  min_area_pct, model_path, providers}`.

### `GET /equipment/{equipment_id}/analytics`

Полная сводка: та же поузловая структура + агрегаты окна и разбивка дефектов:

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
      "confirmed_defects": ["..."],
      "unconfirmed_defects": [],
      "note": "виден 40 из 40 кадров; подтверждено: коррозия"
    }
  ],
  "confirmed_defects": 1,
  "unconfirmed_defects": 0
}
```

### `POST /analytics/reset`

`POST /analytics/reset?equipment_id=PUMP-001` очищает окно одного агрегата,
без параметра — все окна. Ответ: `{"reset": true, "equipment_id": ...,
"analytics": <пустая сводка>}` (для сброса всех `analytics: null`).

Окно также сбрасывается при `POST /sim/reset` и при успешной смене layout
через `POST /pipeline/layout` (новый планшет — новое наблюдение). Смена только
флага `defects` окно не сбрасывает.

### Стабильность в диагностике

`visual_defect` и `visual_signs` учитывают окно: если дефект был, но не
подтверждён (`presence_ratio`/`frames_present` ниже порогов), severity
понижается до `info`, overlay становится `grey`, а к тексту добавляется
«Единичное наблюдение, требуется повторная проверка.». В `evidence[]`
появляется блок `analytics` (`frames_present`, `frames_seen_component`,
`presence_ratio`, `mean_confidence`, `confirmed`). Когда окно набирает
достаточно кадров, severity возвращается к `warning`/`critical`.

Диагностика поузловая: если подтверждённые дефекты есть на двух узлах,
создаются два отдельных события с разными `component_id` и `rule_id`
(`visual_defect` для самого уверенного узла и `visual_defect_<узел>` для
остальных). У активного события есть top-level `component_id` (дублирует
`overlay.component_id` и `evidence[].component`). Когда узел уходит из кадра
или дефект больше не наблюдается, его событие закрывается, остальные
продолжают жить.

### Осмотры

`POST /inspections` сохраняет в `inspections.analytics_json` полную сводку на
момент осмотра и возвращает её в поле `analytics`; `GET /inspections` отдаёт
то же поле в истории. Это даёт в отчёте формулировку «за N секунд
наблюдения» (`duration_sec`).

`POST /inspections` и `GET /inspections` также возвращают вычисляемый
`verdict` — итог осмотра по окну:

```json
"verdict": {
  "status": "defects_found | attention_required | no_visible_defects | insufficient_data",
  "reason": "1 предложение по-русски",
  "components_with_issues": ["pump"],
  "components_not_seen": ["manometer"],
  "disclaimer": "Отсутствие видимых дефектов не подтверждает исправность оборудования."
}
```

Приоритет статусов:

1. есть подтверждённый по окну дефект → `defects_found`;
2. есть `check_required`/`possible_defect` или неподтверждённые наблюдения →
   `attention_required`;
3. все узлы `ok`, но часть не наблюдалась или `visibility_ratio < 0.6`
   (включая случай «видео не было») → `insufficient_data`;
4. все `ok` и видимость всех узлов ≥ 0.6 → `no_visible_defects`.

`insufficient_data` никогда не превращается в «норму»: если данных нет,
клиент видит именно нехватку данных, а не отсутствие дефектов.
`components_with_issues` — узлы, где есть признаки в статусе `detected` или
`single_observation` (а также подтверждённые дефекты); `components_not_seen` —
узлы с `frames_seen == 0`. В `reason` перечислены русские названия признаков
из реестра, например «Коррозия и Подтёк масла».

## REST, относящийся к пайплайну

* `GET /health` → `detector`, `detector_mode`, `pipeline` (`enabled`, `layout`,
  `layout_revision`, `layout_components`, `gauge_ocr`, `gauge_config`,
  `ocr_every`, `defects_enabled`, `anomaly`, `corrosion`).
* `GET /pipeline/layout`, `POST /pipeline/layout` → текущий layout и горячая
  подмена планшета (см. выше).
* `GET /equipment/{id}/diagnostics` → `camera.ocr` (последнее OCR-значение с
  камеры, `stale`, `age_sec`), `camera.available`, `blocked`, `history`.
* `GET /equipment/{id}/analytics` → полная сводка окна наблюдения;
  `POST /analytics/reset?equipment_id=...` → очистка окна.
* `POST /inspections` → `analytics`, `verdict` + колонка `analytics_json`;
  в `GET /inspections` поля `analytics` и `verdict` есть в каждом осмотре.
* `POST /sim/reset` → сбрасывает и окно видеоаналитики.
* `POST /sim/scenario` `{"equipment_id","scenario"}` → демо `gauge_drift`:
  `manometer_ocr = 0.78 × pressure_outlet`, правило срабатывает через ~10–15 с.

## Семантика OCR и правила `gauge_drift`

1. `manometer_ocr` в свежем сэмпле телеметрии (сценарий симулятора) — приоритет 1.
2. Иначе — `manometer_ocr` не старше `CV_TTL_SEC` (сигнал может приходить реже
   телеметрии; так же переживаются несколько писателей).
3. Иначе — свежее OCR-значение с камеры (`cv_state.ocr`).
4. Если ничего нет → правило `blocked_by: ocr_unavailable`.

Порог правила — 15 % (`rules/rules.yaml`, `deviation_pct`).

## Локальная vision-модель: `/vision/*` (офлайн, Ollama)

Модель `qwen2.5vl:3b` работает локально через Ollama (`http://127.0.0.1:11434`),
облачных API нет. Если Ollama недоступна, сервер отвечает `error`, но остаётся
живым; правила и `/analyze` продолжают работать. В ответах `/analyze` и
`/ws/frame` появилось **только новое поле** `vision` — формат прежних полей не
изменился.

### `POST /vision/check`

Query: `equipment_id` (по умолчанию `PUMP-001`), `component_id` (узел из
карточки: `pump`, `motor`, `flange`, …).
Тело: `multipart/form-data`, поле `image` (JPEG/PNG) — **необязательно**.
Без файла анализируется последний кадр `/ws/frame` (или `/analyze`).

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

* `verdict`: любой из `corrosion` / `paint_peeling` / `mechanical_damage` /
  `oil_leak` = `true` → `defect`; все `false` → `no_defect`; есть `null` и нет
  `true` → `insufficient`.
* `signs` — строго четыре признака; `null` = «не видно / мало качества».
* `grounded=false` — в `comment` есть числа, которых не было во входных данных
  (гипотезах датчиков). Ответ не отбрасывается, только помечается.
* `error`: `null` | `vision_disabled` | `timeout` | `ollama_unavailable` |
  `model_not_found` | `invalid_json` | `no_frame` | `bad_image`.
* `cached=true`, `latency_ms=0` — ответ из кэша по хэшу изображения (TTL 10 мин).
* В промпт добавляется строка «Датчики показывают: <гипотезы>; проверь, видны
  ли соответствующие визуальные признаки». Гипотезы собираются из последней
  телеметрии и активных диагностик; они не заменяют визуальный вывод.

Побочный эффект: при `verdict=defect` создаётся диагностика `visual_signs`
(severity `warning`) с `evidence = признаки + confidence`. Это дополнение к
`visual_defect` (эвристики), а не дубль: если оба сработали по одному узлу,
остаётся более уверенное, второе помечается полем `suppressed_by`
(`visual_defect` ↔ `visual_signs`) и уходит в историю.

### `GET /vision/explain?equipment_id=PUMP-001`

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

* `severity`: `info` | `warning` | `critical`; если факты содержат warning/
  critical-диагностику, модель не может занизить severity.
* Модель обязана не выдумывать числа: если в `summary` появилось число,
  которого нет в фактах, сервер подставляет детерминированное заключение и
  отдаёт `error="ungrounded"`.
* При недоступной модели возвращается детерминированное заключение по фактам
  (правила + ресурс) и `error` (`ollama_unavailable` / `timeout` / …).

### `GET /vision/status`

```json
{"enabled": true, "available": true, "model": "qwen2.5vl:3b",
 "url": "http://127.0.0.1:11434", "last_response_ms": 1405,
 "last_ok_at": "2026-11-13T13:23:40+00:00", "last_error": null,
 "error": null, "models": ["qwen2.5vl:3b"]}
```

`available=false` + `error="model_not_found"` — Ollama жива, но модели нет;
`error="ollama_unavailable"` — не отвечает сам сервер Ollama.

### Поле `vision` в `/analyze` и `/ws/frame`

Добавлено в конец ответа и равно последней успешной проверке узла по этому
агрегату (или `null`, если проверок ещё не было). Остальные поля не менялись:

```json
"vision": {"component_id": "pump", "verdict": "defect", "signs": {...},
           "confidence": 0.9, "error": null, "...": "..."}
```

### Конфигурация и деградация

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `VISION_ENABLED` | `true` | выключает вызовы модели, не ломая API |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | адрес локального Ollama |
| `OLLAMA_MODEL` | `qwen2.5vl:3b` | имя модели в Ollama |

Таймаут одного запроса — 60 с, при сетевой ошибке один повтор. Вызовы идут в
`asyncio.to_thread`, event loop не блокируется. Тесты используют замоканный
HTTP-клиент и модель не запускают.
