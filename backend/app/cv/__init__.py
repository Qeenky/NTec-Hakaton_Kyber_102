"""Модули компьютерного зрения: детекторы, пайплайн планшета и правила статусов.

Публичные точки входа:

* `app.cv.detector.get_detector()` — фабрика детекторов (`board`/`fake`/`yolo`);
* `app.cv.pipeline.BoardPipeline.process(image, equipment_id)` — сквозной
  пайплайн кадра (ArUco → зоны → OCR → детекции);
* `app.cv.rules.build_components_status()` — статусы узлов по детекциям.
"""
