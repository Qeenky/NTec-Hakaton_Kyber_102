"""Классическое CV для чтения аналогового манометра и визуальных дефектов.

Публичный API:
    read_gauge(image_bgr, config) -> GaugeReading
    read_gauge_dict(image, config) -> dict   (для встраивания в backend)

    detect_rust(roi_bgr)       -> DefectScore   # коррозия
    detect_oil(roi_bgr)        -> DefectScore   # масляный подтёк
    detect_oxidation(roi_bgr)  -> DefectScore   # окисление клемм
    detect_defects(roi_bgr)    -> dict[str, dict]

    check_marker(frame_bgr, marker_id, ...)          -> PresenceResult
    check_marker_in_roi(frame_bgr, marker_id, poly)  -> PresenceResult
"""
from .defects import (
    DefectScore,
    detect_defects,
    detect_oil,
    detect_oxidation,
    detect_rust,
    load_config as load_defect_config,
)
from .gauge import GaugeConfig, GaugeReading, read_gauge, read_gauge_dict
from .presence import (
    PresenceResult,
    check_marker,
    check_marker_in_roi,
    load_config as load_presence_config,
)

__all__ = [
    "GaugeConfig", "GaugeReading", "read_gauge", "read_gauge_dict",
    "DefectScore", "detect_rust", "detect_oil", "detect_oxidation",
    "detect_defects", "load_defect_config",
    "PresenceResult", "check_marker", "check_marker_in_roi",
    "load_presence_config",
]
__version__ = "1.1.0"
