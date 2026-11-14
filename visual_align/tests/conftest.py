"""Общие фикстуры: эталон собирается один раз на сессию."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from visual_align.aligner import FeatureAligner  # noqa: E402
from visual_align.dataset import load_image_unicode, load_layout  # noqa: E402

LAYOUT_PATH = WORKSPACE / "ar_board" / "out_real" / "layout.json"


@pytest.fixture(scope="session")
def layout_path() -> Path:
    return LAYOUT_PATH


@pytest.fixture(scope="session")
def layout() -> dict:
    return load_layout(LAYOUT_PATH)


@pytest.fixture(scope="session")
def board():
    return load_image_unicode(LAYOUT_PATH.parent / "board_A3.png")


@pytest.fixture(scope="session")
def aligner(layout_path: Path) -> FeatureAligner:
    return FeatureAligner.from_files(layout_path.parent / "board_A3.png", layout_path,
                                     name="test")
