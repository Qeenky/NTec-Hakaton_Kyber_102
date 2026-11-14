"""Локальная vision-модель (Ollama): визуальные признаки и объяснение.

Пакет намеренно не тянет тяжёлых зависимостей: HTTP — стандартный `urllib`,
изображения кодируются через уже установленный OpenCV/Pillow.
"""

from .client import VisionResult, check_component, ollama_status, verdict_from_signs

__all__ = [
    "VisionResult",
    "check_component",
    "ollama_status",
    "verdict_from_signs",
]
