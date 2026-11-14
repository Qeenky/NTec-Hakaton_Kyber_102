"""Визуальные дефекты: классы, правило `visual_defect` и флаг `defects`.

Цепочка демо: эвристики `cv_classic.defects` → `kind="defect"` в ответе →
`cv_state` → правило `visual_defect` с текстом и рекомендацией.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.cv.detector import COMPONENT_CLASS_NAMES, DEFECT_CLASS_NAMES, CLASS_KINDS
from app.cv.pipeline import BoardDetector, BoardPipeline
from app.cv_state import CvObservation, CvSnapshot
from app.diagnostics.catalog import load_catalog
from app.diagnostics.evaluator import RuleEvaluation, dedupe, evaluate_rule
from app.main import app

PROJECT_DIR = Path(__file__).resolve().parents[2]
REAL_LAYOUT = PROJECT_DIR / "ar_board" / "out_real" / "layout.json"
DEFECT_LAYOUT = PROJECT_DIR / "ar_board" / "out_defect" / "layout.json"
REAL_BOARD = PROJECT_DIR / "ar_board" / "out_real" / "board_A3.png"
DEFECT_BOARD = PROJECT_DIR / "ar_board" / "out_defect" / "board_A3.png"
GAUGE_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "manometer_board.json"

pytestmark = pytest.mark.skipif(
    not all(
        path.is_file()
        for path in (REAL_LAYOUT, DEFECT_LAYOUT, REAL_BOARD, DEFECT_BOARD)
    ),
    reason="нет ar_board/out_real или ar_board/out_defect",
)

EQUIPMENT = "PUMP-001"
VISUAL_CLASSES = {"oil_leak", "corrosion", "oxidation", "crack", "guard_damage"}


# Кадры


@lru_cache(maxsize=4)
def _warped_board(board_path: str, width: int = 1280, height: int = 720) -> bytes:
    """Планшет в кадре камеры (перспектива) как JPEG — быстрее полного PNG."""
    raw = np.frombuffer(Path(board_path).read_bytes(), dtype=np.uint8)
    board = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    assert board is not None
    board_h, board_w = board.shape[:2]
    inset, tilt = 0.06, 0.01
    source = np.float32([[0, 0], [board_w, 0], [board_w, board_h], [0, board_h]])
    target = np.float32(
        [
            [width * inset, height * inset],
            [width * (1 - inset), height * (inset + tilt)],
            [width * (1 - inset - tilt), height * (1 - inset)],
            [width * (inset + tilt), height * (1 - inset)],
        ]
    )
    matrix = cv2.getPerspectiveTransform(source, target)
    frame = np.full((height, width, 3), (28, 30, 34), dtype=np.uint8)
    cv2.warpPerspective(
        board,
        matrix,
        (width, height),
        dst=frame,
        borderMode=cv2.BORDER_TRANSPARENT,
    )
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    assert ok
    return buffer.tobytes()


def _pil_frame(board_path: Path) -> Image.Image:
    from io import BytesIO

    return Image.open(BytesIO(_warped_board(str(board_path)))).convert("RGB")


def _snapshot(*observations: CvObservation) -> CvSnapshot:
    return CvSnapshot(
        available=True,
        observed_at=datetime.now(timezone.utc),
        observations=tuple(observations),
        source="board",
    )


def _obs(class_name: str, confidence: float, component_id: str) -> CvObservation:
    return CvObservation(
        class_name=class_name,
        confidence=confidence,
        bbox=[0.1, 0.1, 0.4, 0.4],
        component_id=component_id,
        label=class_name,
        meta={
            "kind": "defect",
            "evidence": {
                "detector": class_name,
                "class": class_name,
                "score": confidence,
                "threshold": 0.52,
                "area_px": 1234.5,
                "component_label": "электродвигатель",
                "features": {"darkening": 0.81, "parts": {"area": 0.4}},
            },
        },
    )


@pytest.fixture()
def board_client(memory_db):
    """TestClient с реальным пайплайном; эвристики стартуют выключенными."""
    with TestClient(app) as client:
        pipeline = BoardPipeline(
            layout_path=str(REAL_LAYOUT),
            gauge_config_path=str(GAUGE_CONFIG),
            ocr_every=1,
        align_mode="features",
            defects_enabled=False,
        )
        app.state.detector = BoardDetector(pipeline)
        app.state.pipeline = pipeline
        yield client


def _analyze(client: TestClient, board_path: Path) -> dict:
    response = client.post(
        "/analyze",
        params={"equipment_id": EQUIPMENT},
        files={"image": (board_path.name, _warped_board(str(board_path)), "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    return response.json()


# Классификатор и правило


def test_oxidation_is_its_own_defect_class() -> None:
    assert "oxidation" in DEFECT_CLASS_NAMES
    assert CLASS_KINDS[list(DEFECT_CLASS_NAMES).index("oxidation") + 5] == "defect"
    # Индексы существующих классов не сдвинулись — мобильный клиент не ломается.
    assert list(COMPONENT_CLASS_NAMES) == [
        "motor",
        "pump",
        "coupling_guard",
        "flange",
        "manometer",
    ]


def test_visual_defect_rule_warning_and_critical() -> None:
    catalog = load_catalog()
    rule = catalog.get("visual_defect")
    assert rule is not None and rule.dynamic == "visual_defect"
    now = datetime.now(timezone.utc)

    one = evaluate_rule(
        rule,
        [],
        {},
        _snapshot(_obs("oxidation", 0.6, "motor")),
        now=now,
    )
    assert one.matched is True
    assert one.confidence == pytest.approx(0.6)
    assert one.effective_severity == "warning"
    assert "окисление контактов" in one.effective_message
    assert "электродвигатель" in one.effective_message
    assert "Протянуть клеммы" in one.effective_recommendation
    assert one.effective_overlay["component_id"] == "motor"
    assert one.effective_overlay["color"] == "orange"

    entry = one.evidence[0]
    assert entry["class"] == "oxidation"
    assert entry["score"] == pytest.approx(0.6)
    assert entry["threshold"] == pytest.approx(0.52)
    assert entry["area_px"] == pytest.approx(1234.5)
    assert entry["features"]["darkening"] == pytest.approx(0.81)

    two = evaluate_rule(
        rule,
        [],
        {},
        _snapshot(
            _obs("corrosion", 0.7, "flange"),
            _obs("oil_leak", 0.65, "flange"),
        ),
        now=now,
    )
    assert two.matched is True
    assert two.effective_severity == "critical"
    assert two.effective_overlay["color"] == "red"
    assert len(two.evidence) == 2
    # mean(0.7, 0.65) × (1 + 0.10 × (2 − 1)) = 0.7425.
    assert two.confidence == pytest.approx(0.7425, abs=1e-4)


def test_visual_defect_suppressed_by_specific_diagnosis() -> None:
    catalog = load_catalog()
    visual_rule = catalog.get("visual_defect")
    seal_rule = catalog.get("seal_leak")
    assert visual_rule is not None and seal_rule is not None

    visual = RuleEvaluation(
        rule=visual_rule, confidence=0.8, matched=True, evidence=[]
    )
    seal = RuleEvaluation(rule=seal_rule, confidence=0.7, matched=True, evidence=[])
    dedupe([visual, seal], relevance_threshold=0.45, activate_confidence=0.65)
    assert visual.suppressed_by == "seal_leak"

    # Специфичный диагноз ниже порога активации visual_defect не подавляет.
    visual2 = RuleEvaluation(
        rule=visual_rule, confidence=0.8, matched=True, evidence=[]
    )
    weak_seal = RuleEvaluation(
        rule=seal_rule, confidence=0.55, matched=True, evidence=[]
    )
    dedupe([visual2, weak_seal], relevance_threshold=0.45, activate_confidence=0.65)
    assert visual2.suppressed_by is None


def test_defects_flag_switches_without_restart() -> None:
    pipeline = BoardPipeline(
        layout_path=str(REAL_LAYOUT),
        gauge_config_path=str(GAUGE_CONFIG),
        ocr_every=1,
        align_mode="features",
        defects_enabled=False,
    )
    frame = _pil_frame(DEFECT_BOARD)

    pipeline.apply_layout(str(DEFECT_LAYOUT), defects=False)
    assert pipeline.layout_info()["defects_enabled"] is False
    disabled = pipeline.process(frame, EQUIPMENT)
    assert not [d for d in disabled.detections if d.kind == "defect"]
    assert disabled.detected is True

    pipeline.apply_layout(str(DEFECT_LAYOUT), defects=True)
    assert pipeline.layout_info()["defects_enabled"] is True
    enabled = pipeline.process(frame, EQUIPMENT)
    defects = [d for d in enabled.detections if d.kind == "defect"]
    assert defects, "на дефектном планшете эвристики должны сработать"
    defect = defects[0]
    assert defect.class_name in VISUAL_CLASSES
    assert defect.source == "board"
    assert defect.component_id in {"motor", "coupling_guard", "pump", "flange"}
    assert defect.evidence["threshold"] > 0
    assert defect.evidence["area_px"] > 0
    assert isinstance(defect.evidence["features"], dict)

    # Без явного defects флаг сохраняется.
    pipeline.apply_layout(str(DEFECT_LAYOUT))
    assert pipeline.layout_info()["defects_enabled"] is True


def test_analyze_publishes_and_clears_visual_defect(board_client) -> None:
    switched = board_client.post(
        "/pipeline/layout",
        json={"path": "../ar_board/out_defect/layout.json", "defects": True},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["defects_enabled"] is True

    defect_payload = _analyze(board_client, DEFECT_BOARD)
    defects = [d for d in defect_payload["detections"] if d["kind"] == "defect"]
    assert defects, defect_payload["detections"]
    defect = defects[0]
    assert defect["class_name"] in VISUAL_CLASSES
    assert defect["source"] == "board"
    assert defect["component_id"]
    assert defect["evidence"]["threshold"] > 0
    assert defect["evidence"]["features"]

    status_by_id = {
        item["component_id"]: item["status"]
        for item in defect_payload["components_status"]
    }
    assert status_by_id[defect["component_id"]] in {
        "check_required",
        "possible_defect",
    }
    overlay = next(
        item
        for item in defect_payload["overlays"]
        if item["component_id"] == defect["component_id"]
    )
    assert overlay["color"] in {"yellow", "red"}

    active = {item["rule_id"]: item for item in defect_payload["active_diagnostics"]}
    assert "visual_defect" in active, defect_payload["active_diagnostics"]
    visual = active["visual_defect"]
    assert "Визуальные признаки дефекта" in visual["message"]
    assert "узле" in visual["message"]
    assert visual["recommendation"]
    assert visual["evidence"] and visual["evidence"][0]["features"]
    # Один кадр — окно ещё не подтвердило дефект: info + просьба перепроверить.
    assert visual["severity"] == "info"
    assert "Единичное наблюдение" in visual["message"]
    assert visual["evidence"][0]["analytics"]["confirmed"] is False

    # Чистый планшет + эвристики выключены: дефектов и диагноза нет.
    clean_switch = board_client.post(
        "/pipeline/layout",
        json={"path": "../ar_board/out_real/layout.json", "defects": False},
    )
    assert clean_switch.status_code == 200, clean_switch.text
    assert clean_switch.json()["defects_enabled"] is False
    clean_payload = _analyze(board_client, REAL_BOARD)
    assert not [d for d in clean_payload["detections"] if d["kind"] == "defect"]
    assert "visual_defect" not in {
        item["rule_id"] for item in clean_payload["active_diagnostics"]
    }

    # Дефектный планшет, но defects=false: правило не срабатывает.
    board_client.post(
        "/pipeline/layout",
        json={"path": "../ar_board/out_defect/layout.json", "defects": False},
    )
    off_payload = _analyze(board_client, DEFECT_BOARD)
    assert not [d for d in off_payload["detections"] if d["kind"] == "defect"]
    assert "visual_defect" not in {
        item["rule_id"] for item in off_payload["active_diagnostics"]
    }
