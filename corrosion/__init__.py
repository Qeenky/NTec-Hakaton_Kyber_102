"""Детектор коррозии по узлам оборудования (YOLO11n + площади по зонам).

Публичный интерфейс:
    from corrosion.adapter import detect_corrosion, summarize
"""
from .adapter import CorrosionDetector, detect_corrosion, get_detector, summarize

__all__ = ["CorrosionDetector", "detect_corrosion", "get_detector", "summarize"]
