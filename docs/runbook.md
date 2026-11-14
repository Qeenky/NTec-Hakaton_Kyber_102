# Runbook: поднять всё с нуля на защите

Пошаговая инструкция для Windows/PowerShell. Все пути — из этого проекта
(`D:\NTec-Хакатон`). Пароли в документе не приводятся: только плейсхолдеры.

---

## 0. Prerequisites

| Что | Требование | Как проверить |
|---|---|---|
| ОС | Windows 10/11, PowerShell 5.1+ | `$PSVersionTable.PSVersion` |
| Python | 3.10.x, venv `backend\.venv` | `backend\.venv\Scripts\python.exe --version` → 3.10.11 |
| PostgreSQL | 18, служба `postgresql-x64-18` | `Get-Service postgresql-x64-18` → `Running` |
| Flutter | 3.50.0 stable, Dart 3.14, `D:\flutter` (не в PATH) | `D:\flutter\bin\flutter.bat --version` |
| Android SDK | `%LOCALAPPDATA%\Android\Sdk` | `...\platform-tools\adb.exe version` |
| Телефон | Android, USB-отладка, камера | `adb devices` → `device` |
| Ollama | установлена, модель `qwen2.5vl:3b` скачана | `ollama list` → `qwen2.5vl:3b` |
| Печать | A3, матовая бумага, принтер или плоттер | сторона ArUco-маркера 60±1 мм |
| Сеть | Wi-Fi hotspot ноутбука **без интернета** или USB | `ipconfig` |

`flutter` и `adb` не в PATH — ниже используются полные пути. VPN (в т.ч.
`VPN-адаптер`) перед демо выключить: он перехватывает локальный трафик.
Ollama без модели — не ошибка: сервер поднимется, VLM-канал просто отключится
(см. §2б).

Все команды выполняются из указанной папки. Если PowerShell блокирует
активацию venv — активировать не нужно, вызывайте `python.exe` напрямую.

---

## 1. База данных

Служба PostgreSQL должна быть запущена (обычно стартует автоматически).

```powershell
Get-Service postgresql-x64-18
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
& $psql -U postgres -c "CREATE USER ntec WITH PASSWORD '<ПАРОЛЬ>';"
& $psql -U postgres -c "CREATE DATABASE ntec OWNER ntec;"
& $psql -U postgres -d ntec -c "GRANT ALL ON SCHEMA public TO ntec;"
```

Если роль/база уже есть — команды пропустить (ошибка `already exists` не
страшна). Пароль суперпользователя нужен только здесь.

Файл `backend\.env` (создать из примера, если нет):

```powershell
cd D:\NTec-Хакатон\backend
Copy-Item .env.example .env
notepad .env
```

Минимум для PostgreSQL (пароль — свой, в тексте не хранить):

```dotenv
DATABASE_URL=postgresql+psycopg://ntec:<ПАРОЛЬ>@127.0.0.1:5432/ntec
DETECTOR=board
LAYOUT_PATH=../ar_board/out_real/layout.json
GAUGE_CONFIG=configs/manometer_board.json
CV_DEFECTS=false
SIM_ENABLED=true
```

`DATABASE_URL` обязателен с драйвером `postgresql+psycopg` (psycopg v3).
Код сам переписывает `postgresql://` и `postgres://`. Если `DATABASE_URL` не
задан, backend молча уйдёт в SQLite `backend/data/app.db` — для защиты это
резерв, но не основной режим.

Схема и демо-данные (идемпотентно):

```powershell
.\.venv\Scripts\python.exe scripts\init_db.py
```

Ожидаемо: `Диалект: postgresql`, `Демо-агрегат создан: да`, 8 таблиц, у
`telemetry_samples.payload` и `diagnostics.evidence` — `jsonb`.

Проверка подключения без Python:

```powershell
& $psql -U ntec -h 127.0.0.1 -d ntec -c "select count(*) from equipment;"
```

---

## 2. Backend и `/health`

```powershell
cd D:\NTec-Хакатон\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

В отдельном окне:

```powershell
curl.exe -s http://127.0.0.1:8000/health
```

Ожидаемо:

* `"status":"ok"`, `"version":"0.4.0"`;
* `"detector_mode":"board"`, `"detector":"BoardDetector"`;
* `"pipeline":{"enabled":true,...,"layout_components":5,"gauge_ocr":true,"defects_enabled":false}`;
* `"database":"postgresql"`, `"equipment_count":1`, `"rules_count":13`;
* `"simulator":{"enabled":true,"states":1}`.

Если `pipeline.enabled=false` — не найден `layout.json` или `DETECTOR=fake`.
Проверить путь из `.env` и что файл существует:

```powershell
Test-Path ..\ar_board\out_real\layout.json
```

Swagger: `http://127.0.0.1:8000/docs`. Логи uvicorn — в этом же окне; не
закрывать.

Тесты (необязательно, но полезно до выезда):

```powershell
$env:LAYOUT_PATH = "..\ar_board\out\layout.json"   # тесты рассчитаны на макет out/
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:LAYOUT_PATH
```

Без override два теста пайплайна (`test_ocr_reads_manometer_and_publishes_state`,
`test_ws_frame_contract`) падают: `.env` указывает `out_real`, а калибровка
`GAUGE_CONFIG` — под `out/`. Это не регрессия кода, а рассогласование планшета и
калибровки; см. §5.

---

## 2б. Локальная модель (Ollama)

VLM нужна только для AI-проверки узла (`POST /vision/check`) и AI-заключения
(`GET /vision/explain`). Без неё осмотр, правила, аналитика и `verdict` работают.

### Запуск и модель

`ollama` может быть не в PATH — полный путь
`$env:LOCALAPPDATA\Programs\Ollama\ollama.exe`. Модель качается **до выезда**
(интернет на площадке не нужен и не должен использоваться).

```powershell
# 1. Сервер Ollama (отдельное окно; окно не закрывать)
ollama serve

# 2. Модель (один раз, заранее)
ollama pull qwen2.5vl:3b
ollama list
```

Ожидаемо в `ollama list`: `qwen2.5vl:3b` (Q4_K_M, ~3.8B). Если `ollama serve`
пишет `address already in use` — Ollama уже запущена (трей/служба), это
нормально: второй экземпляр не нужен. Если команда `ollama` не находится:

```powershell
$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
& $ollama serve
& $ollama pull qwen2.5vl:3b
& $ollama list
```

### Проверка со стороны backend

```powershell
curl.exe -s http://127.0.0.1:8000/vision/status
```

Ожидаемо: `"enabled":true`, `"available":true`, `"model":"qwen2.5vl:3b"`,
`"url":"http://127.0.0.1:11434"`, `"error":null`, в `models` есть
`qwen2.5vl:3b`.

### Прогрев модели

Первый ответ всегда медленный: загрузка весов в VRAM/RAM. На сцене этого ждать
нельзя, поэтому прогревать заранее — коротким запросом (так же делает
`tools\demo_start.ps1`):

```powershell
$warm = '{"model":"qwen2.5vl:3b","prompt":"ok","stream":false,"options":{"num_predict":1}}'
Measure-Command {
  Invoke-RestMethod -Method Post http://127.0.0.1:11434/api/generate `
    -ContentType "application/json" -Body $warm -TimeoutSec 120 | Out-Null
} | Select-Object TotalSeconds
```

Первый прогрев — десятки секунд, повторный — единицы. После прогрева проверка
узла:

```powershell
curl.exe -s -X POST "http://127.0.0.1:8000/vision/check?equipment_id=PUMP-001&component_id=pump" `
  -F "image=@ar_board/out_real/board_A3.png"
```

Ожидаемо: `200`, `verdict` из `defect` / `no_defect` / `insufficient`,
четыре признака, `model":"qwen2.5vl:3b"`, `latency_ms` порядка 1–6 с (p50
4.6 с по бенчмарку). `cached=true`, `latency_ms=0` — ответ из кэша.

### Если модели нет

| Симптом | Что делать |
|---|---|
| `available=false`, `error="model_not_found"` | `ollama pull qwen2.5vl:3b`; проверить имя в `OLLAMA_MODEL` |
| `error="ollama_unavailable"` | запустить `ollama serve`; проверить `http://127.0.0.1:11434/api/tags` |
| `error="timeout"` | модель выгружена/не хватает памяти: прогреть, закрыть лишнее, повторить |
| `error="vision_disabled"` | `VISION_ENABLED=false` в `.env`; поставить `true` и перезапустить backend |
| Модель не нужна на демо | оставить как есть: кнопки AI покажут «недоступно», остальное работает |

Система **не падает** без Ollama: `/health`, `/ws/frame`, `/analyze`, правила,
аналитика и сохранение осмотра продолжают работать. VLM-канал отключается,
`verdict` считается по правилам и окну, а не по модели.

---

## 3. Сеть: hotspot или USB

### Вариант A — Wi-Fi hotspot ноутбука (основной)

1. Параметры Windows → Сеть и Интернет → Мобильный хот-спот → включить.
2. Ноутбук раздаёт `192.168.137.1`. Интернета у клиентов нет — так и надо.
3. Проверить адрес:

```powershell
ipconfig | Select-String -Pattern "192.168.137.1"
```

4. Разрешить входящие на 8000 (один раз, от администратора):

```powershell
New-NetFirewallRule -DisplayName "NTec backend" -Direction Inbound `
  -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private
```

5. С телефона открыть `http://192.168.137.1:8000/health` в браузере. Если
   страница не открывается — firewall/профиль сети/AP isolation, см. §8.

### Вариант B — USB (резерв)

```powershell
$adb = "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
& $adb devices
& $adb reverse tcp:8000 tcp:8000
& $adb reverse --list
```

В приложении адрес `127.0.0.1:8000`. USB-режим не требует Wi-Fi вообще —
удобно для проверки офлайна в режиме полёта.

Адрес задаётся в приложении (экран «Подключение»), хранится в
`shared_preferences`, по умолчанию `192.168.137.1:8000`; кнопка «Подставить
адрес для USB» ставит `127.0.0.1:8000`.

---

## 4. APK: сборка, установка, адрес

Собирать **до выезда**: `flutter build` тянет Gradle-зависимости из сети.
На площадке — только установка.

```powershell
cd D:\NTec-Хакатон\mobile
D:\flutter\bin\flutter.bat pub get
D:\flutter\bin\flutter.bat build apk --release
```

APK: `mobile\build\app\outputs\flutter-apk\app-release.apk` (в текущей поставке
файла ещё нет — собрать заранее).

```powershell
$adb = "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
& $adb install -r D:\NTec-Хакатон\mobile\build\app\outputs\flutter-apk\app-release.apk
```

Если подпись не совпадает со старой установкой:

```powershell
& $adb uninstall ru.ntec.ntec_inspector
& $adb install D:\NTec-Хакатон\mobile\build\app\outputs\flutter-apk\app-release.apk
```

Проверка на телефоне:

1. Разрешить камеру.
2. Экран «Подключение» → адрес → «Сохранить» → «Проверить связь».
3. Ожидаемо: «Связь есть · …» с версией и детектором.
4. Стартовый экран: QR `PUMP-001` (или «Выбрать оборудование вручную»).

В манифесте уже включены `CAMERA`, `INTERNET` и
`android:usesCleartextTraffic="true"` — HTTP до локального сервера работает без
TLS.

---

## 5. AR-планшет

В рантайме «AR-планшет» — это **напечатанный лист A3**, а не программа. Клиент
наводит камеру на лист, сервер ищет 4 ArUco и проецирует зоны.

1. Выбрать планшет, под который настроен `.env`:
   * `ar_board\out_real\board_A3.pdf` — из реального фото (сейчас в `.env`),
     layout `ar_board\out_real\layout.json`;
   * `ar_board\out\board_A3.pdf` — синтетическая схема, layout
     `ar_board\out\layout.json`.
2. Печать: A3, **альбомная**, масштаб **100 % / Actual size**, без «Fit to
   page»; бумага **матовая**, не ламинировать, не сгибать.
3. Проверить сторону маркера линейкой: **60±1 мм**.

### Переключение планшетов на лету

На защите два планшета: чистый `ar_board\out_real` (5 зон, с манометром) и
дефектный `ar_board\out_defect` (4 зоны, без манометра). Переключение не требует
рестарта и не рвёт активные `/ws/frame`.

Текущее состояние:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/pipeline/layout |
  Format-List path,components,component_ids,revision,gauge_ocr,defects_enabled
```

На дефектный планшет (включая эвристики `cv_classic`):

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/pipeline/layout `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"path":"../ar_board/out_defect/layout.json","defects":true}'
```

Ожидаемо: `reloaded=true`, `components=4`, `component_ids` без `manometer`,
`gauge_ocr=false`, `defects_enabled=true`, `revision` увеличился на 1. В кадре:
4 полигона, `ocr=null`, `gauge_drift` — `blocked_by="ocr_unavailable"`.

Обратно на чистый:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/pipeline/layout `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"path":"../ar_board/out_real/layout.json","defects":false}'
```

Важно:

* относительный путь резолвится от корня проекта, а не от cwd;
* смена планшета сбрасывает окно видеоаналитики (новое наблюдение), смена
  только флага `defects` — нет;
* ошибки: `404` — файла нет, `422` — битый/неполный `layout.json`,
  `409` — сервер запущен с `DETECTOR=fake`;
* неудачный `POST` ничего не меняет: `GET` покажет прежний файл и `revision`.

4. Проверить детекцию с веб-камеры (окно OpenCV, `q` — выход):

```powershell
cd D:\NTec-Хакатон\ar_board
.\.venv\Scripts\python.exe -m ar_board.cli --layout out_real\layout.json --video 0
```

В HUD: FPS, число маркеров, ошибка репроекции, coverage. Если маркеров 0 —
см. §8.

5. **Важно про OCR.** `configs/manometer_board.json` откалиброван под
   синтетический макет `out/` (шкала 0–13.39 бар). На `out_real` плоский скан
   даёт `no_needle`, то есть `gauge_drift` по камере не сработает. Для показа
   `gauge_drift` используйте сценарий симулятора (он подаёт `manometer_ocr` в
   телеметрию) либо перекалибруйте конфиг под свой планшет:

```powershell
cd D:\NTec-Хакатон
cv_classic\.venv\Scripts\python.exe -m cv_classic.calibrate `
  --image <кадр_манометра.png> --out backend\configs\manometer_out_real.json `
  --value-min 0 --value-max 13.39 --unit бар
```

(в `--image` подставить кадр манометра с вашего планшета) и укажите
`GAUGE_CONFIG=configs/manometer_out_real.json` в `.env`.

6. **Сценарии и живая камера.** `guard_removed`, `seal_leak`, `looseness`,
   `electrical_contact` подают синтетические CV-наблюдения из симулятора; поток
   кадров с телефона их заменяет, и правило гаснет. Проверяйте такие сценарии
   без активного осмотра (curl / `/sim/scenario`) либо используйте `gauge_drift`
   и сенсорные сценарии — они не зависят от кадров.

---

## 6. Резервное видео (без телефона)

Скрипт изображает камеру: накладывает перспективу на планшет, шлёт JPEG по
`/ws/frame`, рисует ответ и пишет mp4. Сервер должен быть запущен.

```powershell
cd D:\NTec-Хакатон\backend

# норма: 2 минуты, 2 кадра/с
.\.venv\Scripts\python.exe scripts\simulate_phone.py `
  --board ..\ar_board\out_real\board_A3.png --equipment PUMP-001 `
  --fps 2 --duration 120 --scenario normal --save ..\demo_backup_normal.mp4

# дрейф манометра: сценарий симулятора, ~45 с
.\.venv\Scripts\python.exe scripts\simulate_phone.py `
  --board ..\ar_board\out_real\board_A3.png --equipment PUMP-001 `
  --fps 2 --duration 45 --scenario gauge_drift --save ..\demo_backup_gauge.mp4

# дефектный планшет: сначала переключить layout (4 зоны + эвристики),
# затем записать видео с картинкой out_defect
Invoke-RestMethod -Method Post http://127.0.0.1:8000/pipeline/layout `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"path":"../ar_board/out_defect/layout.json","defects":true}'
.\.venv\Scripts\python.exe scripts\simulate_phone.py `
  --board ..\ar_board\out_defect\board_A3.png --equipment PUMP-001 `
  --fps 2 --duration 45 --scenario normal --save ..\demo_backup_defect.mp4
# вернуть чистый планшет
Invoke-RestMethod -Method Post http://127.0.0.1:8000/pipeline/layout `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"path":"../ar_board/out_real/layout.json","defects":false}'
```

В корне уже лежат `demo_ar.mp4`, `demo_clean.mp4` и `demo_gauge_drift.mp4` — их
и показывать, если телефон/камера подведут. `--scenario` переключает сценарий
через `POST /sim/scenario` перед стартом. Проверить запись:

```powershell
Start-Process ..\demo_ar.mp4
```

---

## 7. Проверка офлайн-режима

Требование: интернет не нужен, наружу не уходит ни один байт. Проверять на том
же сценарии, что будет на защите.

### 7.1. USB + режим полёта (самый чистый тест)

1. На телефоне: режим полёта, Wi-Fi выключить.
2. На ноутбуке: отключить VPN и все сетевые адаптеры (Wi-Fi/Ethernet) —
   USB-режиму сеть не нужна. Посмотреть и отключить:

```powershell
Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object Name,InterfaceDescription
Disable-NetAdapter -InterfaceDescription "<описание>" -Confirm:$false
# вернуть после теста:
Enable-NetAdapter -InterfaceDescription "<описание>"
```
3. `adb reverse tcp:8000 tcp:8000` (делается заново после переподключения USB).
4. В приложении адрес `127.0.0.1:8000`.
5. Прогнать: health → карточка → осмотр с камеры → завершение → история →
   сценарий `guard_removed`/`gauge_drift` → диагностика.
6. Проверить локальную VLM: `/vision/status` → `available=true`; нажать
   «AI-проверка узла» в приложении. Ollama слушает только `127.0.0.1:11434`,
   наружу не ходит.
7. Убедиться, что в логах uvicorn нет попыток DNS/внешних соединений.

### 7.2. Wi-Fi hotspot без интернета

1. Включить мобильный хот-спот Windows (интернет-источник не нужен; если
   hotspot поднимается на том же Wi-Fi-адаптере, интернет на ноутбуке
   пропадёт — это ожидаемо).
2. Отключить VPN и, если есть, Ethernet.
3. Телефон подключить к hotspot, адрес `192.168.137.1:8000`.
4. Повторить шаги 5–7 из 7.1.

Быстрая проверка, что снаружи ничего не нужно:

```powershell
# локальный сервер жив
curl.exe -s http://127.0.0.1:8000/health
# VLM доступна локально
curl.exe -s http://127.0.0.1:8000/vision/status
& (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe") list
# на ноутбуке не должно быть внешних Established-соединений у python/ollama
Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue |
  Where-Object { $_.RemoteAddress -notmatch '^(127\.0\.0\.1|::1)$' } |
  Select-Object RemoteAddress,RemotePort,OwningProcess
```

Если в списке есть процессы браузера/IDE — это не backend; смотреть
`OwningProcess` через `Get-Process -Id`. Соединения на `127.0.0.1:11434` —
это backend ↔ Ollama, они локальные и допустимы.

**Что считается успехом:** полный сценарий работает при отключённом интернете,
APK не падает, AR-зоны и правила отвечают, AI-проверка узла отвечает,
история сохраняется в PostgreSQL.

---

## 8. Типовые сбои

### Порт 8000 занят

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen |
  Select-Object LocalAddress,OwningProcess
Get-Process -Id <PID>
Stop-Process -Id <PID> -Force
```

Либо запустить на другом порту (`--port 8010`) и поменять адрес в приложении.

### Телефон не видит сервер (localhost работает)

1. `ipconfig` — тот ли `192.168.137.1`; телефон в той же подсети?
2. Firewall: правило `NTec backend` для `Private`; профиль сети — «Частная».
3. AP isolation на чужом Wi-Fi — не использовать чужую сеть, раздавать свою.
4. `Test-NetConnection 192.168.137.1 -Port 8000` с ноутбука.
5. USB: `adb reverse --list`; после переподключения кабеля — повторить reverse.
6. VPN — выключить, он может заворачивать трафик.
7. В приложении сохранён старый адрес — «Сохранить» и «Проверить связь».

### «Нет связи» в приложении, но health открывается в браузере

Проверить, что в настройках нет схемы/слэша (`server_settings.tryParse` понимает
`http://host:8000/`, но лучше вводить `host:8000`), и что телефон не в
«режиме экономии», который режет WebSocket. Перезапустить приложение.

### Камера не стартует

* Разрешение камеры выдано (Настройки Android → Приложения → NTec Инспектор).
* Закрыть Zoom/Teams/браузер, которые держат камеру.
* Перезапустить приложение; при необходимости — телефон.
* Проверить, что на телефоне есть свободное место (запись видео).
* Логи: `adb logcat | Select-String -Pattern "ntec|camera"`.

### Маркеры не видны (`board.reason=no_markers` / `partial`)

* Масштаб печати не 100 % (маркер не 60 мм) — перепечатать.
* Глянец/ламинат/блик — матовая бумага, рассеянный свет, сместить ракурс.
* В кадре меньше 3 маркеров — отойти/наклонить, чтобы лист попал целиком.
* Дистанция <0.4 м или >2 м; наклон >35°; маркеры закрыты рукой/тенью.
* Размытие/грязная линза/автофокус не успел — протереть, подождать.
* `board.reason=low_quality` — маркеры есть, но точность/покрытие ниже порога:
  приблизиться, выровнять плоскость, добавить свет.

### OCR не читает манометр (`no_needle`, `no_dial`, `low_confidence`)

* Проверить, что `GAUGE_CONFIG` соответствует планшету (§5).
* Убрать блик с циферблата, снять под углом ≤30°, навести резкость.
* Для демо не полагаться на камеру: сценарий `gauge_drift` подаёт OCR в
  телеметрию, правило сработает и без чтения стрелки.
* Не считать `ocr.value=null` ошибкой сервера: это честный отказ алгоритма.

### AI-проверка не отвечает (`/vision/*`)

* `/vision/status` → `error`:
  * `ollama_unavailable` — запустить `ollama serve`, проверить
    `http://127.0.0.1:11434/api/tags`;
  * `model_not_found` — `ollama pull qwen2.5vl:3b` (имя должно совпадать с
    `OLLAMA_MODEL`);
  * `vision_disabled` — `VISION_ENABLED=true` в `.env` и перезапуск backend;
  * `timeout` — модель выгрузилась/мало памяти: прогреть (§2б), закрыть
    браузер/IDE, повторить.
* Первый ответ после простоя медленный (загрузка весов) — это не зависание,
  ждать до 60 с.
* `error="no_frame"` — нечего проверять: нужен кадр `/ws/frame` или файл в
  `POST /vision/check`.
* Система без VLM работоспособна: осмотр, правила, аналитика, `verdict` и
  сохранение работают; кнопки AI покажут «недоступно».

### Переключение планшета не сработало

* `404` — путь неверный: относительный резолвится от корня проекта
  (`D:\NTec-Хакатон`), проверьте `Test-Path ..\ar_board\out_defect\layout.json`
  из `backend\`;
* `422` — битый `layout.json`, нет `board`/`markers` или у зоны меньше 3 точек;
* `409` — сервер запущен с `DETECTOR=fake`, пайплайна планшета нет;
* после смены планшета окно аналитики сбрасывается — это ожидаемо, а не
  потеря данных;
* если `overlays` не изменились, сравните `GET /pipeline/layout` →
  `component_ids` и `revision`.

### Backend падает на старте: БД

* `Get-Service postgresql-x64-18`; пароль/порт в `DATABASE_URL`;
* `psql -U ntec -h 127.0.0.1 -d ntec -c "select 1"`;
* если Postgres недоступен, временно: `$env:DATABASE_URL=""` и запуск — уйдёт в
  SQLite (`backend/data/app.db`), но история будет локальной.

### POST возвращает 422 `json_invalid`

PowerShell 5.1 режет кавычки. Использовать `curl.exe --%` (см. `docs/api.md`)
или файл тела `--data-binary "@file.json"`. Проверить `bbox` (0..1,
`x1<x2`, `y1<y2`) и соответствие `class_id`/`class_name`/`kind`.

### WebSocket закрывается кодом 1008

`equipment_id` не найден в БД — проверка есть и в `/ws/telemetry`, и в
`/ws/frame`: соединение принимается и закрывается кодом `1008`,
`reason="unknown_equipment"`. Проверить `GET /equipment` и использовать
`PUMP-001`.

### APK не устанавливается

`adb install -r`; при конфликте подписи — `adb uninstall ru.ntec.ntec_inspector`;
разрешить установку из неизвестных источников, если ставится вручную.

---

## 9. Чек-лист за 30 минут до защиты

1. Ноутбук в сеть, VPN выключен; питание подключено.
2. `Get-Service postgresql-x64-18` → `Running`; `psql ... -c "select 1"` работает.
3. `backend\.env` — правильные `DATABASE_URL`, `LAYOUT_PATH`, `DETECTOR=board`,
   `GAUGE_CONFIG`; `VISION_ENABLED=true`, если нужна AI-проверка.
4. Backend запущен на `0.0.0.0:8000`; `/health` → `status ok`,
   `pipeline.enabled=true`, `rules_count=13`.
5. **Ollama:** `ollama serve` запущен; `ollama list` содержит `qwen2.5vl:3b`;
   `/vision/status` → `available=true`, `error=null`. Модель прогрета коротким
   запросом (§2б); повторная проверка узла укладывается в ~5 с.
6. Если Ollama/модели нет — это допустимо: проверено, что осмотр, правила,
   аналитика и `verdict` работают; AI-кнопки покажут «недоступно». Решение
   зафиксировано заранее, а не в момент показа.
7. Правило `guard_removed` проверено (телефон в этот момент не стримит):
   `POST /sim/scenario` → через 3 с в `diagnostics.active` есть critical; затем
   `POST /sim/reset`.
8. `guard_removed`/`gauge_drift` сброшены, сценарий `normal`, очередь событий
   пуста; окно аналитики очищено (`POST /analytics/reset`).
9. Телефон заряжен ≥80 %, камера разрешена, APK установлен, адрес сохранён,
   «Проверить связь» — зелёная.
10. `adb reverse --list` (для USB) или телефон в hotspot (для Wi-Fi) — проверено.
11. **Планшеты:** чистый `out_real` (5 зон) и дефектный `out_defect` (4 зоны)
    распечатаны, матовые, маркер 60 мм, лежат ровно, свет без бликов.
    Переключение проверено: `GET /pipeline/layout` → `components=5`,
    `POST` с `defects=true` → `components=4`, `revision+1`; затем возврат на
    `out_real` с `defects=false`.
12. Сценарий показа отрепетирован: карточка → осмотр → (опция) AI-проверка →
    завершение → `verdict` и история; видно, что «недостаточно данных» — это
    нормальный исход, а не сбой.
13. Резервное видео `demo_ar.mp4` / `demo_clean.mp4` открывается; плеер и звук
    проверены. Для дефектного планшета — запись из §6 (или живой показ).
14. Офлайн-тест пройден: интернет отключён, полный сценарий отработал,
    AI-проверка ответила, внешних соединений у backend/ollama нет.
15. Сброс демо-состояния: `POST /sim/reset {"clear_history": true}`; телефон
    переподключён; окна uvicorn/Ollama/Swagger открыты, лишние приложения
    закрыты.

После сброса ещё раз открыть карточку `PUMP-001`: `scenario=normal`, активных
событий нет, графики идут, `GET /pipeline/layout` → `out_real`, `defects=false`.
