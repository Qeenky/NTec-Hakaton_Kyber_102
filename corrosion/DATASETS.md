# Датасеты коррозии: что скачано, что нет и почему

Все архивы лежат на диске D: (`corrosion/data/`), распакованы в `corrosion/data/raw/`.
Скачивание — `corrosion/download.py` (без регистрации и токенов).

| # | Набор | Ссылка | Лицензия | Изображений | Разметка | Статус |
|---|-------|--------|----------|-------------|----------|--------|
| 1 | **Corroded Pipelines Dataset** (Figshare 30019591) | https://doi.org/10.6084/m9.figshare.30019591 | **CC BY 4.0** | 110 (внутри труб, робот BIKE) | COCO instance segmentation, RLE-маски, 1 класс `corrosão` → `corrosion` | ✅ скачан (10.5 МБ) |
| 2 | **Corrosion Condition State Semantic Segmentation Dataset** (Figshare 16624663) | https://doi.org/10.6084/m9.figshare.16624663 | **CC0 1.0** | 440 (396 train + 44 test) | Labelme-полигоны, 3 уровня тяжести (`2_Fair`, `3_Poor`, `4_Severe`) + PNG-маски | ✅ скачан (318 МБ) |
| 3 | **Image-based Corrosion Detection Dataset** (Mendeley, Purdue) | https://doi.org/10.17632/tbjn6p2gn9.1 | **CC BY 4.0** | 152 (реальные фото: цепи, замки, трубы) | **разметки в публичном архиве нет** — только JPEG | ⚠️ скачан (632 МБ), в обучение не пошёл |
| 4 | **WPI-ARL Corrosion Image Data Set** (BMVC 2021) | https://arl.wpi.edu/corrosion_dataset/ | **non-commercial research/education only** (disclaimer на сайте) | 600 | только экспертный рейтинг снимка 1–9 (`names_ratings.xlsx`), **боксов/масок нет** | ⚠️ скачан (595 МБ), в обучение не пошёл |
| 5 | **Rust Corrosion Detection** (Roboflow Universe) | https://universe.roboflow.com | зависит от автора (обычно CC BY 4.0) | ~8354 | YOLO-боксы | ❌ пропущен: требуется аккаунт + API-ключ (HTTP 403 без токена) |
| 6 | **YOLOv12 Rust Segmentation**, **Pipeline Corrosion** (Kaggle) | https://www.kaggle.com/datasets | разная | — | маски/боксы | ❌ пропущен: требуется Kaggle API-токен (`kaggle.json`); анонимный API отдаёт reCAPTCHA |
| 7 | **Bootgroup Project Pipes w/ Corrosion** (Roboflow) | https://universe.roboflow.com | зависит от автора | — | боксы | ❌ пропущен: та же причина (Roboflow API-ключ) |

## Почему два набора не пошли в обучение

* **Mendeley / Purdue** — в архиве `Corrosion_Data.zip` (662 МБ) лежат только 152 JPEG
  (`Largeimage/000001.jpg …`), ни JSON, ни XML, ни масок. Описание на странице обещает
  «detailed annotation», но в публичной версии v1 её нет. Использован только как
  качественная проверка на реальных фото (`samples/mendeley_real_*.jpg`).
* **WPI-ARL** — задача набора — регрессия экспертной оценки 1–9 по снимку, а не
  локализация. Боксов нет, поэтому обучать детектор на нём нельзя.

## Что осталось после гармонизации

См. `corrosion/prepare_report.json` (генерируется `prepare.py`):

| Источник | Прочитано | Оставлено после дедупа | Боксов | Отброшено |
|----------|-----------|------------------------|--------|-----------|
| pipelines (Figshare) | 110 | 26 | 168 | 84 (83 near-duplicate кадра из видео, dHash ≤ 3; 1 кадр без разметки) |
| condition (Figshare) | 440 | 440 | 4075 | 0 |
| **Итого** | 550 | **466** | **4243** | 84 |

Сплит 80/10/10 по изображениям (seed 20261113): train 372 / val 47 / test 47.

Почему из 110 кадров pipelines осталось 26: это кадры видео, снятые с частотой 1 Гц,
соседние кадры почти идентичны. Оставлять их — значит получить утечку между
train и val и завышенные метрики. Дедупликация по dHash (64 бита, порог Хэмминга 3)
вырезает такие серии.
