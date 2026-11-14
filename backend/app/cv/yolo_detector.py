"""Заготовка реального детектора на YOLO.

Файл не импортируется при DETECTOR=fake, поэтому torch/ultralytics не нужны
для демо. Для включения установите ultralytics и задайте DETECTOR=yolo.
"""

from __future__ import annotations

import os

from PIL import Image

from .detector import CLASS_IDS, CLASS_KINDS, Detection, Box


class YoloDetector:
    """Обёртка над ultralytics.YOLO с тем же интерфейсом, что у FakeDetector."""

    def __init__(self, model_path: str | None = None, conf: float = 0.25) -> None:
        from ultralytics import YOLO  # тяжёлая опциональная зависимость

        self.model = YOLO(model_path or os.getenv("YOLO_MODEL", "yolo11n.pt"))
        self.conf = conf

    def detect(self, image: Image.Image) -> list[Detection]:
        """Запускает модель и переводит боксы в нормализованные координаты."""
        result = self.model.predict(image, conf=self.conf, verbose=False)[0]
        width, height = image.size
        names: dict[int, str] = result.names
        detections: list[Detection] = []

        for box in result.boxes:
            class_name = names[int(box.cls)]
            if class_name not in CLASS_IDS:
                continue
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            bbox: Box = (
                max(0.0, min(1.0, x1 / width)),
                max(0.0, min(1.0, y1 / height)),
                max(0.0, min(1.0, x2 / width)),
                max(0.0, min(1.0, y2 / height)),
            )
            class_id = CLASS_IDS[class_name]
            detections.append(
                Detection(
                    class_id=class_id,
                    class_name=class_name,
                    kind=CLASS_KINDS[class_id],
                    confidence=round(float(box.conf), 3),
                    bbox=bbox,
                )
            )
        return detections
