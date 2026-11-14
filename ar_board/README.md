# ar_board — AR-привязка по печатному планшету A3

Мобильное AR-приложение наводится на планшет A3 с изображением насосного агрегата
и подсвечивает узлы и дефекты. Вся математика — на Python-сервере: клиент получает
готовые полигоны в координатах кадра и только рисует их.

Планшет выглядит как обычный лист: только фото оборудования и заголовок —
QR-кода на нём нет. QR оборудования вынесен на отдельную метку (`qr/PUMP-001.png`):
карточка открывается, только когда камера наведена на неё, а не на планшет.
Привязка — **по признакам сцены** (`visual_align`): сервер сопоставляет кадр с
эталонным рендером листа (`references.json`) и находит гомографию. ArUco-маркеры
на боевых планшетах **не печатаются**, но режим с маркерами сохранён как fallback
(папки `out_real_markers/`, `out_defect_markers/`, `boards.json`).

## Состав

```
ar_board/
  make_board.py          генератор планшета A3 300 dpi + layout.json (--no-markers, --no-qr)
  make_qr.py             отдельная QR-метка оборудования (qr/PUMP-001.png)
  qr/                    QR-метка и qr_card.html для показа с экрана
  references.json        эталоны для visual_align: board_id -> PNG + layout.json
  boards.json            legacy-реестр маркеров: board_id -> identity-маркер -> layout
  annotate.py            разметка 5 зон узлов мышью (OpenCV)
  ar_board/
    spec.py              единая геометрия листа и маркеров (мм/px)
    layout.py            контракт layout.json, порядок узлов
    detect.py            detect_board(): ArUco + гомография + solvePnP
    registry.py          load_boards(), identify_board() по identity-маркеру
    project.py           project_components(), Smoother, render_overlay()
    synthetic.py         синтетические перспективы с истинной H
    io_utils.py          Unicode-safe imread/imwrite для Windows
    cli.py               камера/видео -> оверлей, FPS
  scripts/eval_synthetic.py   проверка на 10 перспективах
  scripts/eval_identity.py    идентификация: 10 перспектив каждого планшета
  tests/test_ar_board.py      pytest
  tests/test_board_identity.py  реестр, identity, guard, планшет без маркеров
  out/                        готовый планшет, layout.json, synthetic/
  out_real/, out_defect/      боевые планшеты БЕЗ маркеров и БЕЗ QR (фото + заголовок)
  out_real_markers/, out_defect_markers/  те же листы с ArUco (fallback)
```

## Установка

```bat
cd D:\NTec-Хакатон\ar_board
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 1. Планшет

```bat
.venv\Scripts\python.exe make_board.py --photo pump.jpg --out out\ --equipment PUMP-001
```

Без `--photo` рисуется схема агрегата средствами PIL (корпус, двигатель, муфта,
фланец, труба, манометр, подписи) — комплект работает сразу:

```bat
.venv\Scripts\python.exe make_board.py --out out\ --equipment PUMP-001
```

Результат: `out/board_A3.png` (4961×3508, 300 dpi), `out/board_A3.pdf`,
`out/layout.json`. Маркеры — чёрные квадраты 60 мм с белой quiet zone 10 мм
по углам; фото/схема — по центру, QR с `PUMP-001` — в шапке (для боевых
планшетов QR убирается флагом `--no-qr`, см. ниже).

Дополнительные параметры идентификации (legacy, для режима с маркерами):

- `--identity-id N` — ArUco-маркер `DICT_4X4_50` id N (~40 мм) в шапке справа
  от подписи, не перекрывает QR и угловые маркеры;
- `--guard-marker` — маркер id 4 (~30 мм) внутри полигона `coupling_guard`;
- `--board-id clean|defect` — id планшета для `boards.json` (по умолчанию:
  с guard — `clean`, без guard — `defect`);
- `--no-markers` — планшет без единого ArUco (см. раздел «Планшет без маркеров»).

Пересборка планшетов с маркерами (зоны узлов сохраняются из старого `layout.json`):

```bat
.venv\Scripts\python.exe make_board.py --photo ..\photos\1.jpg ^
  --identity-id 7 --guard-marker --board-id clean --out out_real_markers
.venv\Scripts\python.exe make_board.py ^
  --photo ..\organizer_data\defects\_curated\14_pump_насос.jpg ^
  --identity-id 8 --board-id defect --out out_defect_markers
```

### Печать

- A3, **альбомная**, масштаб **100% / «Actual size»**, без «Fit to page».
- Матовая бумага (не глянец): блики — главный враг детекции.
- Не сгибать и не ламинировать глянцем; маркеры не обрезать.
- Альтернатива: вывести PNG на монитор/ТВ 16:9 в полный экран, выключить
  агрессивное шумоподавление и «улучшение картинки».
- Проверка масштаба (только для листов с маркерами): сторона маркера на бумаге
  должна быть 60±1 мм, identity-маркер — 40±1 мм.

## Планшет без маркеров

Зачем: на защите планшет должен выглядеть как обычный лист с фото оборудования
и заголовком, а не как «полигон с чёрными квадратами». Маркеры на фото
агрегата портят вид, бликуют и требуют точной печати 100%, а привязку теперь даёт
`visual_align` — сопоставление кадра с эталонным рендером листа по признакам
сцены. Поэтому на боевых планшетах ArUco **нет вообще**: ни угловых (id 0–3),
ни identity, ни guard-маркера на кожухе. QR тоже вынесен на отдельную метку
(`--no-qr`): планшет — это только оборудование.

Как собрать:

```bat
.venv\Scripts\python.exe make_board.py --photo ..\photos\1.jpg ^
  --title "Насосный агрегат · PUMP-001" ^
  --no-markers --no-qr --board-id PUMP-001 --out out_real

.venv\Scripts\python.exe make_board.py ^
  --photo ..\organizer_data\defects\_curated\14_pump_насос.jpg ^
  --title "Дефектный участок · PUMP-002" ^
  --no-markers --no-qr --board-id PUMP-002 --out out_defect
```

Что делает `--no-markers`:

- не рисует угловые ArUco (id 0–3), identity-маркер в шапке и guard-маркер id 4;
- оставляет фото, заголовок и подзаголовок;
- в `layout.json` пишет `"markers": []`, `"aruco": null`,
  `"board": {"id": "clean|defect", "title": "...", "has_markers": false}`,
  `"alignment": "visual_align"`;
- **зоны `components` не трогает** — они уже размечены в координатах планшета
  и переносятся из предыдущего `layout.json` при пересборке (проверяется тестом
  `test_no_markers_keeps_components`).

Что делает `--no-qr`:

- не рисует QR оборудования и подпись под ним в правом верхнем углу — там
  остаётся белая бумага (шапка и фото не меняются);
- в `layout.json` пишет `"board": {"has_qr": false}`; зоны `components`
  и `photo_rect_px` остаются прежними;
- сама метка живёт отдельно: `qr/PUMP-001.png` и `qr/qr_card.html`.

Проверка чистоты листа (делается один раз после сборки):

- `cv2.aruco.ArucoDetector` на рендере находит 0 маркеров;
- углы листа и место identity-маркера — белая бумага (min ≥ 240);
- чёрных квадратов ≥ 25 мм нет;
- `cv2.QRCodeDetector` на планшете находит **0 QR** (ни одного совпадения);
- `cv2.QRCodeDetector` на `qr/PUMP-001.png` читает ровно `PUMP-001`.

Версии с маркерами сохранены как fallback: `out_real_markers/`,
`out_defect_markers/` (PNG + PDF + layout.json с маркерами). Реестр `boards.json`
теперь указывает именно на них — маркерная детекция и `eval_identity.py`
продолжают работать без изменений кода.

Файлы боевых планшетов:

- `out_real/board_A3.png`, `out_real/board_A3.pdf`, `out_real/layout.json`
  (фото `..\photos\1.jpg`, «Насосный агрегат · PUMP-001», 5 зон);
- `out_defect/board_A3.png`, `out_defect/board_A3.pdf`, `out_defect/layout.json`
  (фото `14_pump_насос.jpg`, «Дефектный участок · PUMP-002», 4 зоны).

## QR-метка отдельно

QR напечатан **не на планшете**, а на отдельной табличке/листе: пользователь
наводит камеру на QR — приложение открывает карточку оборудования; наводит на
планшет — видит только оборудование и зоны узлов.

```bat
.venv\Scripts\python.exe make_qr.py --data PUMP-001 --out qr/PUMP-001.png
.venv\Scripts\python.exe make_qr.py --data PUMP-002 --out qr/PUMP-002.png
```

- `qr/PUMP-001.png` и `qr/PUMP-002.png` — крупные QR (≈1220–1460 px, с белым
  полем и подписью), читаются `cv2.QRCodeDetector` как `PUMP-001` / `PUMP-002`;
- `qr/qr_card.html` — страница для показа с экрана: QR на белом фоне,
  клавиши **1** / **2** переключают исправный и дефектный объекты, клик
  (или Enter/пробел) — полный экран, Esc — выход. Оба PNG должны лежать рядом
  с HTML.

Сценарий целиком: **QR отдельно → карточка оборудования → планшет → осмотр**.

Эталоны для безмаркерной привязки — `references.json`:

```json
{"references": [
  {"board_id": "PUMP-001", "image": "ref/PUMP-001_photo.png", "layout": "ref/PUMP-001_layout.json"},
  {"board_id": "PUMP-002", "image": "ref/PUMP-002_photo.png", "layout": "ref/PUMP-002_layout.json"}
]}
```

Как привязка работает теперь (модуль `visual_align`):

1. эталон (`image` из `references.json`) один раз описывается локальными
   признаками ORB и AKAZE;
2. на кадре ищутся те же признаки, сопоставляются BFMatcher + Lowe ratio,
   RANSAC оценивает гомографию «эталон → кадр»;
3. проверки правдоподобия (доля инлайеров, выпуклость и площадь проекции,
   пространственный разброс, NCC эталона с кадром) отбраковывают ложные позы;
4. `project_components(pose, layout)` тем же механизмом, что и раньше, переносит
   `components` из `layout.json` в координаты кадра — формат layout не изменился.

Идентификация планшета (`PUMP-001` / `PUMP-002`) в этом режиме — по совпадению
с эталоном: у какого `board_id` выше уверенность и NCC, тот layout и берётся.

## 2. Идентификация планшета (legacy, по маркерам)

Зачем: раньше сервер брал геометрию из конфига (`LAYOUT_PATH`). Если в кадре
оказывался другой планшет, зоны ложились неверно и появлялись ложные дефекты.
Теперь планшет сам сообщает, кто он. На боевых листах маркеров больше нет —
этот режим оставлен как fallback и как референс для тестов.

Как работает:

1. На каждом планшете с маркерами напечатан свой identity-маркер
   (`DICT_4X4_50`): id 7 — чистый, id 8 — дефектный. Он стоит в шапке, поэтому
   виден даже при сильном наклоне и не закрывает фото.
2. `detect_marker_ids(frame)` находит все ArUco-маркеры кадра.
3. `identify_board(frame, boards)` сверяет найденные identity-id с `boards.json`,
   для каждого кандидата строит позу по его `layout.json` и возвращает лучший
   результат (`BoardIdentification`: `board_id`, `identity_marker_id`, `pose`).
4. Дальше `project_components(pose, identified.layout)` проецирует зоны именно
   того планшета, который видит камера.

Реестр `boards.json` (указывает на копии с маркерами):

```json
{"boards": [
  {"board_id": "clean",  "identity_marker_id": 7, "layout": "out_real_markers/layout.json"},
  {"board_id": "defect", "identity_marker_id": 8, "layout": "out_defect_markers/layout.json"}
]}
```

Маркер id 4 (`--guard-marker`) — «сторож» внутри зоны кожуха муфты: его
положение берётся из `coupling_guard` в `layout.json`. Если зоны ещё нет,
маркер ставится в геометрический центр фото, а в layout пишется
`"guard_marker_zone": "photo_center"` — после разметки планшет пересобирается,
и маркер автоматически встаёт в зону.

Добавить третий планшет (безмаркерный):

1. Собрать лист: `make_board.py --photo ... --title "..." --no-markers --board-id third --out out_third`.
2. Разметить зоны: `python annotate.py --layout out_third\layout.json`.
3. Добавить запись в `references.json`:
   `{"board_id": "third", "image": "out_third/board_A3.png", "layout": "out_third/layout.json"}`.
4. Перезапустить `visual_align` — новый эталон подхватится без правок кода.

То же для маркерного fallback: собрать с `--identity-id 9 --board-id third` в
`out_third_markers/` и добавить запись в `boards.json`.

## 3. Разметка зон

```bat
.venv\Scripts\python.exe annotate.py --layout out\layout.json
```

- `1..5` — выбрать узел: motor, pump, coupling_guard, flange, manometer;
- ЛКМ-протяжка — прямоугольник; после отпускания выбирается следующий пустой узел;
- `s` — сохранить, `r` — сбросить текущий, `q` — выход с сохранением, `Esc` — без.

Координаты пишутся в `layout.json` в пикселях планшета (`polygon_px`) и дублируются
в `polygon_norm` (0..1). Если OpenCV-окно недоступно (headless), скрипт печатает
понятную ошибку и завершается с кодом 3, а не падает.

## 4. Проверка с веб-камеры

```bat
.venv\Scripts\python.exe -m ar_board.cli --layout out\layout.json --video 0
```

- `--video 1` — вторая камера, `--video clip.mp4` — видеофайл;
- `--alpha 0.35` — сглаживание (EMA), `--no-smooth` — выключить;
- `--record out\demo.mp4` — записать размеченное видео;
- `--no-display` — без окна, только статистика (для проверки без GUI);
- `q`/`Esc` — выход. В HUD: FPS, число маркеров, ошибка репроекции, coverage.

CLI пока маркерный (через `detect_board`), поэтому запускайте его на листах с
маркерами: `--layout out_real_markers\layout.json`. Безмаркерный режим — это
`visual_align` (`references.json`).

## 5. Автопроверка

```bat
.venv\Scripts\python.exe scripts\eval_synthetic.py
.venv\Scripts\python.exe scripts\eval_identity.py
.venv\Scripts\python.exe -m pytest
```

`eval_synthetic.py` строит 10 случайных перспектив (`warpPerspective` с наклоном
до 35°, масштабом 0.5–1.0), добавляет яркость/контраст/шум/JPEG и сравнивает
проекцию с истинными углами из `layout.json`. Кадры и оверлеи складываются в
`out/synthetic/`.

Фактический результат (seed 20261113):

```
детекция:            10/10 (100%)   цель >= 90%   PASS
ошибка репроекции:   0.40 px        цель < 5 px   PASS
углы зон (<=25°):    0.19 px        цель < 8 px   PASS
кадр без маркеров:   None                          PASS
```

`eval_identity.py` берёт оба планшета из `boards.json` (это копии с маркерами),
строит по 10 перспектив каждого и проверяет identity-маркер, `identify_board()`
и попадание guard-маркера в `coupling_guard`. Фактический результат (seed 20261114):

```
identity найден:        20/20        PASS
id определён верно:     20/20        PASS
ошибка репроекции:      0.41 px (макс 0.51)  цель < 1 px  PASS
guard id 4 в coupling_guard (clean): да, вершин внутри 4/4
```

`pytest`: 31 тест — контракт layout, quiet zone, самодетекция планшета,
3/4 маркера, пустые кадры, EMA и сброс через 1 с, видимость полигонов,
headless-разметка, Unicode-пути, CLI на видеофайле, реестр `boards.json`,
печать identity-маркеров, guard внутри `coupling_guard`, fallback в центр фото,
сохранение `components`, плюс планшет без маркеров: пустой `markers`, ноль ArUco
на рендере, белые углы, читаемый QR, сохранённые зоны и валидный
`references.json`; плюс `--no-qr`: ноль QR на листе при неизменных зонах.

## Контракт layout.json

С маркерами (fallback, `out_real_markers/`, `out_defect_markers/`):

```json
{
  "version": 1,
  "equipment_id": "PUMP-001",
  "board": {"id": "clean", "has_markers": true,
            "identity_marker_id": 7, "guard_marker_id": 4,
            "guard_marker_zone": "coupling_guard",
            "width_px": 4961, "height_px": 3508,
            "width_mm": 420, "height_mm": 297, "dpi": 300},
  "aruco": {"dictionary": "DICT_4X4_50", "ids": [0, 1, 2, 3, 4, 7]},
  "markers": [
    {"id": 0, "role": "corner", "size_mm": 60,
     "corners_px": [[177,177],[886,177],[886,886],[177,886]]},
    {"id": 4, "role": "guard", "size_mm": 30, "zone": "coupling_guard",
     "corners_px": [[x,y], ...]},
    {"id": 7, "role": "identity", "size_mm": 40,
     "corners_px": [[x,y], ...]}
  ],
  "components": [{"component_id": "motor", "label": "Электродвигатель",
                  "polygon_px": [[x,y], ...], "polygon_norm": [[u,v], ...]}]
}
```

Без маркеров (боевые `out_real/`, `out_defect/`) — тот же файл, но:

```json
{
  "board": {"id": "clean", "has_markers": false, "title": "Насосный агрегат · PUMP-001",
            "identity_marker_id": null, "guard_marker_id": null, "...": "..."},
  "aruco": null,
  "markers": [],
  "alignment": "visual_align",
  "components": ["...те же зоны, что и раньше..."]
}
```

`components` в обоих режимах одинаковые: координаты зон не зависят от маркеров.

Порядок углов маркера — TL, TR, BR, BL (как у `cv2.aruco`). Клиент получает от
`project_components()` список `{component_id, label, polygon, visible}`.
`board.id` — `clean` или `defect`; `board.has_markers` — есть ли на листе ArUco;
`identity_marker_id` — id маркера идентификации; `guard_marker_id`/
`guard_marker_zone` — маркер кожуха и зона, в которую он поставлен
(`coupling_guard` или `photo_center`); `alignment` — способ привязки
(`visual_align` у безмаркерных листов).

## API рантайма

```python
from ar_board import (detect_board, identify_board, load_boards,
                      project_components, Smoother, render_overlay)

boards = load_boards("boards.json")        # реестр планшетов
found = identify_board(frame_bgr, boards)  # BoardIdentification | None
if found:
    pose = found.pose                     # BoardPose
    layout = found.layout                 # layout.json этого планшета
    zones = project_components(pose, layout)
    zones = Smoother(alpha=0.35).update(zones)
    frame_out = render_overlay(frame_bgr, zones, pose)
```

`BoardPose`: `homography` 3×3 (планшет → кадр), `marker_ids`, `reprojection_error`
(px), `coverage` (доля кадра), `is_valid`, `frame_size`, `marker_corners`,
`board_corners`, `rvec/tvec/pnp_error` (solvePnP, если доступен), а также
`identity_marker_id`, `board_id`, `guard_marker_id`, `guard_marker_visible`.
Проекция идёт по гомографии (устойчивее при неизвестной камере), solvePnP
считается для rvec/tvec и валидации перспективы.

## Ограничения

Безмаркерный режим (`visual_align`):

- нужен **текстурный** кадр: фото оборудования, заголовок, подписи, рамка листа
  дают признаки. Однородный белый лист без фото не привяжется;
- дистанция **0.4–2 м**, наклон до **~35°**; ближе 0.4 м лист не влезает в кадр;
- блики и пересвет убивают признаки сильнее, чем маркеры — бумага матовая,
  свет рассеянный;
- если кадр «провалился», отдаётся последняя валидная поза (`stale`, до 1.5 с),
  а не мусорная гомография.

Legacy-режим с маркерами (`out_real_markers/`, `out_defect_markers/`):

- Дистанция **0.4–2 м**; ближе 0.4 м маркеры выходят из кадра, дальше 2 м — мелкие.
- Наклон плоскости до **~35°**; комфортно до 25–30°.
- Держите планшет в центральных ~80% кадра: меньше сказывается дисторсия объектива.
- Нужно **минимум 3 маркера** (4 надёжнее); маркеры нельзя закрывать рукой,
  телефоном или тенью.
- Identity-маркер (40 мм в шапке) должен быть виден: по нему определяется
  планшет. Угловые маркеры дают позу, identity — какой это лист.
- Блики на глянце/ламинированной бумаге ломают порог бинаризации — только матовая.
- Плохое освещение и сильное размытие уменьшают дальность; автофокус полезен.
- Камера ноутбука — 720p; для телефона лучше 1080p и фиксированная экспозиция.
- Одна плоскость: планшет должен быть плоским, без изгибов.

## Что говорить жюри

- Планшет выглядит как **обычный лист**: только фото агрегата и заголовок.
  Никаких чёрных квадратов — привязка идёт по признакам сцены (`visual_align`:
  ORB/AKAZE + RANSAC-гомография по эталону листа), маркеры не нужны.
- QR оборудования — **отдельная метка** (`qr/PUMP-001.png`): карточка
  открывается только при наведении на неё, а планшет остаётся оборудованием.
  Сценарий: QR отдельно → карточка → планшет → осмотр.
- Почему это сильнее маркеров: лист не портится, не требует точной печати 100%
  и не бликует чёрными квадратами; работает на том же CPU, офлайн, без обучения
  нейросети и без GPU.
- Маркерный режим не выброшен: `out_real_markers/`, `out_defect_markers/` и
  `boards.json` — воспроизводимый fallback, если на площадке плохой свет и
  признаки «плывут». Это честный план Б, а не костыль.
- ТЗ допускает QR как идентификатор оборудования — метка `PUMP-001` однозначно
  связывает оборудование с карточкой агрегата; на планшете QR нет, поэтому
  карточка не открывается случайно при осмотре листа.
- Вся математика на сервере: клиент — тонкий, получает полигоны в пикселях кадра.
  Это позволяет менять модель/разметку без обновления приложения.
- Точность на синтетике (маркерный референс) — **0.19 px** по углам зон при
  наклоне ≤25°, детекция 100%; на реальной камере точность ограничена
  разрешением и качеством печати, а не алгоритмом.
- Ограничения честно оцифрованы (0.4–2 м, ≤35°), есть CLI-демо без телефона и
  автоматические тесты — воспроизводимость важнее «красивой картинки».

## Мелочи

- Пути с кириллицей поддерживаются (`io_utils.py`): `cv2.imread`/`VideoCapture`
  на Windows их не читают, поэтому используются `np.fromfile`/`imdecode` и
  временная ASCII-копия для видео.
- Камера занята другим приложением → закройте Zoom/Teams/браузер и повторите.
- Если маркеры (legacy-лист) не находятся: проверьте масштаб печати, матовость,
  свет и то, что в кадре целиком хотя бы 3 маркера.
- Если безмаркерная привязка «прыгает»: убедитесь, что в кадре целиком фото
  агрегата и заголовок, и что лист не пересвечен.
