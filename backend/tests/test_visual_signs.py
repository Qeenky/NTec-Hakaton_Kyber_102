"""Реестр визуальных признаков, агрегация по окну и trend."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import analytics
from app.analytics import AnalyticsStore
from app.cv.pipeline import BoardPipeline
from app.verdict import build_verdict
from app.visual_signs import SIGN_BY_ID, SIGN_IDS, VISUAL_SIGNS

PROJECT_DIR = Path(__file__).resolve().parents[2]
BOARD_PATH = PROJECT_DIR / "ar_board" / "out_real_markers" / "board_A3.png"
LAYOUT_PATH = PROJECT_DIR / "ar_board" / "out_real_markers" / "layout.json"
DEFECT_BOARD_PATH = PROJECT_DIR / "ar_board" / "out_defect_markers" / "board_A3.png"
DEFECT_LAYOUT_PATH = PROJECT_DIR / "ar_board" / "out_defect_markers" / "layout.json"
GAUGE_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "manometer_board.json"

EQUIPMENT = "PUMP-001"
NOW = datetime.now(timezone.utc)


def _frame(
    store: AnalyticsStore,
    moment: datetime,
    *,
    components: dict[str, bool],
    defects: list[tuple[str, str, float]] | None = None,
    signs: list[dict] | None = None,
) -> dict:
    overlays = [
        {"component_id": component_id, "visible": visible}
        for component_id, visible in components.items()
    ]
    detections = [
        {
            "kind": "defect",
            "class_name": class_name,
            "component_id": component_id,
            "confidence": confidence,
        }
        for component_id, class_name, confidence in (defects or [])
    ]
    statuses = [
        {"component_id": component_id, "bbox": [0, 0, 1, 1]}
        for component_id, visible in components.items()
        if visible
    ]
    return store.update(
        EQUIPMENT,
        overlays=overlays,
        detections=detections,
        components_status=statuses,
        signs=signs or [],
        now=moment,
    )


def _component(summary: dict, component_id: str) -> dict:
    return next(
        item for item in summary["components"] if item["component_id"] == component_id
    )


def _sign(summary: dict, component_id: str, sign_id: str) -> dict:
    return next(
        item
        for item in _component(summary, component_id)["signs"]
        if item["sign"] == sign_id
    )


def test_registry_contains_all_required_signs() -> None:
    required = {
        "corrosion",
        "oil_leak",
        "oxidation",
        "paint_peeling",
        "mechanical_damage",
        "crack",
        "texture_anomaly",
        "guard_missing",
    }
    assert set(SIGN_IDS) == required
    assert SIGN_BY_ID["corrosion"].label == "Коррозия"
    assert "heuristics" in SIGN_BY_ID["corrosion"].sources
    assert "vlm" in SIGN_BY_ID["corrosion"].sources
    assert SIGN_BY_ID["paint_peeling"].sources == ("vlm",)
    assert SIGN_BY_ID["guard_missing"].sources == ("marker",)
    assert SIGN_BY_ID["guard_missing"].rule == "guard_removed"
    assert len(VISUAL_SIGNS) == len(SIGN_IDS)


def test_signs_statuses_and_source_merge() -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=3)
    for index in range(3):
        _frame(
            store,
            NOW + timedelta(seconds=index),
            components={"motor": True},
            defects=[("motor", "corrosion", 0.55)],
        )
    # VLM даёт тот же признак — sources должны слиться, а не задвоиться.
    analytics_store = store
    for index in range(3):
        analytics_store.record_signs(
            EQUIPMENT,
            "motor",
            ["corrosion"],
            confidence=0.7,
            source="vlm",
            now=NOW + timedelta(seconds=3 + index),
        )
    summary = store.summary(EQUIPMENT, now=NOW + timedelta(seconds=6))
    corrosion = _sign(summary, "motor", "corrosion")
    assert corrosion["status"] == "detected"
    assert corrosion["sources"] == ["heuristics", "vlm"]
    assert corrosion["presence_ratio"] >= 0.3
    assert corrosion["confidence"] == pytest.approx(0.7)
    assert _sign(summary, "motor", "oil_leak")["status"] == "not_detected"


def test_signs_not_visible_vs_not_detected() -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=2)
    _frame(
        store,
        NOW,
        components={"pump": True, "motor": False},
    )
    summary = store.summary(EQUIPMENT, now=NOW)
    assert _sign(summary, "pump", "corrosion")["status"] == "not_detected"
    assert _sign(summary, "motor", "corrosion")["status"] == "not_visible"
    assert _sign(summary, "motor", "guard_missing")["status"] == "not_visible"


def test_signs_trend_fading_and_growing() -> None:
    store = AnalyticsStore(window_sec=20, confirm_ratio=0.3, confirm_frames=2)
    # Признак в первой половине окна, затем узел виден без признака.
    for index in (0, 1):
        _frame(
            store,
            NOW + timedelta(seconds=index),
            components={"pump": True},
            defects=[("pump", "oil_leak", 0.7)],
        )
    for index in (12, 13):
        _frame(
            store,
            NOW + timedelta(seconds=index),
            components={"pump": True},
        )
    summary = store.summary(EQUIPMENT, now=NOW + timedelta(seconds=15))
    assert _sign(summary, "pump", "oil_leak")["trend"] == "fading"

    store2 = AnalyticsStore(window_sec=20, confirm_ratio=0.3, confirm_frames=2)
    for index in (0, 1):
        _frame(store2, NOW + timedelta(seconds=index), components={"pump": True})
    for index in (12, 13):
        _frame(
            store2,
            NOW + timedelta(seconds=index),
            components={"pump": True},
            defects=[("pump", "oil_leak", 0.7)],
        )
    summary = store2.summary(EQUIPMENT, now=NOW + timedelta(seconds=15))
    assert _sign(summary, "pump", "oil_leak")["trend"] == "growing"


def _guard_signs(board_path: Path, layout_path: Path, *, defects: bool = False) -> list[dict]:
    """Признаки guard_missing для планшета (проверка от маркера, не от флага)."""
    raw = np.frombuffer(board_path.read_bytes(), dtype=np.uint8)
    board = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    h, w = board.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[80, 80], [1200, 90], [1190, 640], [90, 630]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    frame = np.full((720, 1280, 3), (28, 30, 34), np.uint8)
    cv2.warpPerspective(board, matrix, (1280, 720), dst=frame,
                        borderMode=cv2.BORDER_TRANSPARENT)
    pipeline = BoardPipeline(
        layout_path=str(layout_path),
        gauge_config_path=str(GAUGE_CONFIG),
        ocr_every=1,
        defects_enabled=defects,
    )
    from PIL import Image

    result = pipeline.process(
        Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), EQUIPMENT
    )
    return [
        item for item in result.signs
        if item["sign"] == "guard_missing"
        and item["component_id"] == "coupling_guard"
    ]


def test_guard_missing_depends_on_marker_not_defects_flag() -> None:
    pytest.importorskip("cv_classic")
    if not DEFECT_BOARD_PATH.is_file() or not DEFECT_LAYOUT_PATH.is_file():
        pytest.skip("нет ar_board/out_defect")

    # Чистый планшет: маркер id 4 в зоне кожуха найден → признака нет,
    # даже если ручной флаг эвристик выключен.
    assert _guard_signs(BOARD_PATH, LAYOUT_PATH, defects=False) == []

    # Дефектный планшет: маркера id 4 нет → guard_missing есть, хотя
    # эвристики отключены флагом. Флаг больше не управляет этим каналом.
    guard = _guard_signs(DEFECT_BOARD_PATH, DEFECT_LAYOUT_PATH, defects=False)
    assert guard, "на дефектном планшете маркера кожуха нет"
    assert guard[0]["confidence"] == pytest.approx(0.9)
    assert guard[0]["source"] == "marker"


def test_verdict_lists_sign_labels() -> None:
    summary = {
        "frames": 10,
        "components": [
            {
                "component_id": "pump",
                "label": "Насос",
                "frames_seen": 10,
                "visibility_ratio": 1.0,
                "status_window": "possible_defect",
                "defects": [],
                "signs": [
                    {
                        "sign": "corrosion",
                        "label": "Коррозия",
                        "status": "detected",
                    },
                    {
                        "sign": "oil_leak",
                        "label": "Подтёк масла",
                        "status": "detected",
                    },
                ],
            }
        ],
    }
    verdict = build_verdict(summary)
    assert verdict["status"] == "defects_found"
    assert verdict["components_with_issues"] == ["pump"]
    assert "Коррозия" in verdict["reason"]
    assert "Подтёк масла" in verdict["reason"]
