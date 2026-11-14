"""visual_align — безмаркерная привязка зон к кадру по эталонному изображению."""

from .aligner import (AlignmentResult, FeatureAligner, Reference, Smoother,
                      confidence_from, load_image_unicode, project_points,
                      save_image_unicode, sheet_quad_from_rect, zones_from_layout)
from .quad import (QuadDetection, detect_sheet_quad, order_quad, quad_aspect,
                   quad_confidence)

__all__ = [
    "AlignmentResult", "FeatureAligner", "QuadDetection", "Reference", "Smoother",
    "confidence_from", "detect_sheet_quad", "load_image_unicode", "order_quad",
    "project_points", "quad_aspect", "quad_confidence", "save_image_unicode",
    "sheet_quad_from_rect", "zones_from_layout",
]
__version__ = "1.1.0"
