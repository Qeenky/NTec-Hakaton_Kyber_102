"""AR-привязка к печатному планшету с ArUco-маркерами."""
from __future__ import annotations

from .detect import BoardPose, detect_board, detect_marker_ids
from .layout import COMPONENT_ORDER, load_layout, save_layout
from .project import Smoother, project_components, render_overlay
from .registry import (BoardEntry, BoardIdentification, identify_board,
                       load_boards)

__all__ = [
    "BoardEntry",
    "BoardIdentification",
    "BoardPose",
    "COMPONENT_ORDER",
    "Smoother",
    "detect_board",
    "detect_marker_ids",
    "identify_board",
    "load_boards",
    "load_layout",
    "project_components",
    "render_overlay",
    "save_layout",
]
__version__ = "1.0.0"
