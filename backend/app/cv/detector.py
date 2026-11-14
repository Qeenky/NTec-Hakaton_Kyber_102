"""Детекторы объектов.

Три режима (`DETECTOR`):

* `board` — настоящий AR-пайплайн: `ar_board.detect_board` + `project_components`
  (см. `app/cv/pipeline.py`). Включается по умолчанию, если найден `layout.json`.
* `fake` — заглушка для отладки клиента: **только узлы, без дефектов**, чтобы
  демо не срабатывало случайно (`source: "fake"`).
* `yolo` — обёртка над ultralytics, если появятся веса.

Порядок классов фиксирован — его знает мобильный клиент.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import numpy as np
from PIL import Image

from ..paths import DEFAULT_LAYOUT_PATH  # noqa: F401  (sys.path для ar_board)

_logger = logging.getLogger(__name__)

# Фиксированный порядок классов. Менять только вместе с моделью и клиентом!
# Новые классы добавляются в конец: индексы существующих не сдвигаются.
CLASS_NAMES: tuple[str, ...] = (
    "motor",
    "pump",
    "coupling_guard",
    "flange",
    "manometer",
    "oil_leak",
    "corrosion",
    "crack",
    "guard_damage",
    "oxidation",
)
CLASS_KINDS: tuple[str, ...] = ("component",) * 5 + ("defect",) * 5
CLASS_IDS: dict[str, int] = {name: idx for idx, name in enumerate(CLASS_NAMES)}
COMPONENT_CLASS_NAMES: tuple[str, ...] = CLASS_NAMES[:5]
DEFECT_CLASS_NAMES: tuple[str, ...] = CLASS_NAMES[5:]

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    """Одна детекция в нормализованных координатах 0..1.

    `source` показывает происхождение данных на защите: `board` (AR-планшет),
    `model` (YOLO) или `fake` (заглушка).

    `component_id` заполняется для визуальных дефектов (к какому узлу
    относится), `evidence` — объяснение эвристики: score, порог, площадь зоны
    и вклад признаков из `cv_classic.defects`. Для узлов оба поля пустые.
    """

    class_id: int
    class_name: str
    kind: str
    confidence: float
    bbox: Box
    source: str = "model"
    component_id: str | None = None
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        """Сериализует детекцию в JSON-совместимый словарь.

        Новые поля (`component_id`, `evidence`) добавляются только когда они
        есть, чтобы контракт детекций узлов остался прежним.
        """
        data = asdict(self)
        data["bbox"] = list(self.bbox)
        if self.component_id is None:
            data.pop("component_id", None)
        if self.evidence is None:
            data.pop("evidence", None)
        return data


class Detector(Protocol):
    """Интерфейс детектора: PIL-кадр -> список детекций."""

    def detect(self, image: Image.Image) -> list[Detection]:
        """Возвращает детекции для одного кадра."""
        ...


# Правдоподобные положения узлов на кадре насосного агрегата.
_COMPONENT_BOXES: dict[str, Box] = {
    "motor": (0.08, 0.12, 0.52, 0.56),
    "pump": (0.50, 0.14, 0.93, 0.60),
    "coupling_guard": (0.30, 0.56, 0.62, 0.87),
    "flange": (0.58, 0.48, 0.88, 0.80),
    "manometer": (0.06, 0.58, 0.28, 0.90),
}


def _clamp_box(box: Box) -> Box:
    """Приводит координаты к [0, 1] и гарантирует x1 < x2, y1 < y2."""
    x1, y1, x2, y2 = box
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if x2 - x1 < 0.01:
        x2 = min(1.0, x1 + 0.01)
    if y2 - y1 < 0.01:
        y2 = min(1.0, y1 + 0.01)
    return (round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4))


def _jitter(box: Box, rng: random.Random, amplitude: float = 0.02) -> Box:
    """Слегка сдвигает бокс, чтобы кадры отличались, но оставались стабильными."""
    return _clamp_box(tuple(coord + rng.uniform(-amplitude, amplitude) for coord in box))


class FakeDetector:
    """Правдоподобная заглушка детектора — только узлы, без дефектов.

    Один и тот же кадр всегда даёт одинаковый результат (хеш пикселей),
    а разные кадры — немного разные детекции. Дефекты намеренно не
    генерируются: иначе на демо случайно сработает `guard_removed` или
    `seal_leak`. Все детекции помечены `source: "fake"`.
    """

    def __init__(self, min_components: int = 2, max_components: int = 4) -> None:
        self.min_components = min_components
        self.max_components = max_components

    def detect(self, image: Image.Image) -> list[Detection]:
        """Генерирует 2–4 узла по хешу кадра. Дефектов не бывает."""
        rgb = image.convert("RGB")
        frame_hash = hashlib.sha256(rgb.tobytes()).hexdigest()

        # Средняя яркость добавляет устойчивый «отпечаток» кадра.
        gray = np.asarray(rgb.convert("L"), dtype=np.uint8)
        brightness = float(gray.mean()) if gray.size else 0.0
        seed = hashlib.sha256(f"{frame_hash}:{brightness:.2f}".encode()).digest()
        rng = random.Random(int.from_bytes(seed[:8], "big"))

        upper = min(self.max_components, len(COMPONENT_CLASS_NAMES))
        lower = min(self.min_components, upper)
        count = rng.randint(lower, upper)
        present = sorted(rng.sample(range(len(COMPONENT_CLASS_NAMES)), count))

        detections: list[Detection] = []
        for index in present:
            name = COMPONENT_CLASS_NAMES[index]
            confidence = round(rng.uniform(0.55, 0.97), 3)
            detections.append(
                Detection(
                    class_id=CLASS_IDS[name],
                    class_name=name,
                    kind="component",
                    confidence=confidence,
                    bbox=_jitter(_COMPONENT_BOXES[name], rng),
                    source="fake",
                )
            )
        return detections


def _default_detector_name() -> str:
    """DETECTOR из env, иначе board при наличии layout.json, иначе fake."""
    configured = os.getenv("DETECTOR", "").strip().lower()
    if configured:
        return configured
    return "board" if DEFAULT_LAYOUT_PATH.is_file() else "fake"


def get_detector(name: str | None = None) -> Detector:
    """Фабрика детектора. DETECTOR=board|fake|yolo (по умолчанию board)."""
    selected = (name or _default_detector_name()).strip().lower()
    if selected == "fake":
        return FakeDetector()
    if selected in {"board", "ar", "arboard"}:
        # Импорт ленивый: cv2/ar_board нужны только в этом режиме.
        try:
            from .pipeline import BoardDetector

            return BoardDetector()
        except Exception as exc:  # noqa: BLE001 - деградируем до заглушки
            _logger.warning(
                "DETECTOR=board недоступен (%s); включаю FakeDetector. "
                "Проверьте opencv-contrib-python и ar_board/out/layout.json",
                exc,
            )
            return FakeDetector()
    if selected == "yolo":
        # Импорт ленивый: torch/ultralytics не нужны для запуска с fake/board.
        from .yolo_detector import YoloDetector

        return YoloDetector()
    raise ValueError(
        f"Неизвестный DETECTOR={selected!r}; ожидалось 'board', 'fake' или 'yolo'"
    )
