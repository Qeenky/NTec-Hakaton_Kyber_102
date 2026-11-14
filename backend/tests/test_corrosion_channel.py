"""Канал corrosion/ (ONNX): area_pct, пороги, кэш и деградация."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.analytics import AnalyticsStore
from app.cv.corrosion_channel import CorrosionChannel
from app.verdict import build_verdict

EQUIPMENT = "PUMP-001"
NOW = datetime.now(timezone.utc)
POLYGON = [[10, 10], [110, 10], [110, 110], [10, 110]]


class FakeSession:
    def __init__(self, boxes: list[tuple[float, float, float, float, float]]) -> None:
        self.boxes = boxes
        self.calls = 0
        self.raise_error = False

    def get_inputs(self) -> list:
        return [type("Input", (), {"name": "images"})()]

    def run(self, output_names, feed_dict):  # noqa: ANN001
        self.calls += 1
        if self.raise_error:
            raise RuntimeError("onnx session failed")
        out = np.zeros((1, 5, 8400), dtype=np.float32)
        for index, (cx, cy, w, h, conf) in enumerate(self.boxes):
            out[0, 0, index] = cx
            out[0, 1, index] = cy
            out[0, 2, index] = w
            out[0, 3, index] = h
            out[0, 4, index] = conf
        return [out]


def _channel(
    boxes: list[tuple[float, float, float, float, float]],
    *,
    every: int = 5,
    min_area_pct: float = 0.5,
) -> tuple[CorrosionChannel, FakeSession]:
    channel = CorrosionChannel(
        model_path="unused",
        enabled=True,
        conf=0.25,
        every=every,
        min_area_pct=min_area_pct,
    )
    session = FakeSession(boxes)
    channel._session = session
    channel._input_name = "images"
    channel._loaded = True
    return channel, session


def _frame() -> np.ndarray:
    return np.full((200, 200, 3), 128, np.uint8)


def test_area_pct_and_boxes_returned() -> None:
    channel, _ = _channel([(320, 320, 640, 640, 0.9)])
    sign = channel._detect_component(_frame(), "pump", POLYGON)
    assert sign is not None
    assert sign["sign"] == "corrosion"
    assert sign["area_pct"] == pytest.approx(100.0, abs=0.1)
    assert sign["confidence"] == pytest.approx(0.9)
    assert sign["source"] == "yolo"
    assert len(sign["boxes"]) == 1


def test_area_thresholds() -> None:
    # ~4 % площади — публикуется, но weak (single_observation).
    channel, _ = _channel([(320, 320, 128, 128, 0.8)])
    sign = channel._detect_component(_frame(), "pump", POLYGON)
    assert sign is not None
    assert 3.5 <= sign["area_pct"] <= 4.5
    assert sign["weak"] is True

    # 0.1 % — ниже min_area_pct, признак не публикуется.
    channel, _ = _channel([(320, 320, 20, 20, 0.8)])
    assert channel._detect_component(_frame(), "pump", POLYGON) is None


def test_every_n_and_cache() -> None:
    channel, _ = _channel([(320, 320, 640, 640, 0.9)], every=5)
    calls = {"n": 0}
    original = channel._detect_component

    def counted(frame, component_id, polygon):  # noqa: ANN001
        calls["n"] += 1
        return original(frame, component_id, polygon)

    channel._detect_component = counted  # type: ignore[method-assign]
    projected = [{"component_id": "pump", "polygon": POLYGON, "visible": True}]
    for _ in range(4):
        channel.score_frame(_frame(), projected, EQUIPMENT)
    assert calls["n"] == 1  # первый кадр
    channel.score_frame(_frame(), projected, EQUIPMENT)
    assert calls["n"] == 2  # пятый кадр


def test_error_disables_channel_without_raising() -> None:
    channel, session = _channel([(320, 320, 640, 640, 0.9)])
    session.raise_error = True
    signs = channel.score_frame(
        _frame(),
        [{"component_id": "pump", "polygon": POLYGON, "visible": True}],
        EQUIPMENT,
    )
    assert signs == []
    status = channel.status()
    assert status["enabled"] is False
    assert "onnx session failed" in status["reason"]


def test_disabled_channel_and_area_in_verdict() -> None:
    disabled = CorrosionChannel(model_path="unused", enabled=False)
    signs = disabled.score_frame(
        _frame(),
        [{"component_id": "pump", "polygon": POLYGON, "visible": True}],
        EQUIPMENT,
    )
    assert signs == []
    assert disabled.status()["enabled"] is False
    assert disabled.status()["reason"] == "disabled"

    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=3)
    for index in range(3):
        store.update(
            EQUIPMENT,
            overlays=[{"component_id": "pump", "visible": True}],
            signs=[
                {
                    "sign": "corrosion",
                    "component_id": "pump",
                    "confidence": 0.7,
                    "source": "yolo",
                    "present": True,
                    "area_pct": 12.0,
                }
            ],
            now=NOW + timedelta(seconds=index),
        )
    summary = store.summary(EQUIPMENT, now=NOW + timedelta(seconds=3))
    sign = next(
        item
        for item in summary["components"][0]["signs"]
        if item["sign"] == "corrosion"
    )
    assert sign["status"] == "detected"
    assert sign["sources"] == ["yolo"]
    assert sign["area_pct"] == pytest.approx(12.0)
    verdict = build_verdict(summary)
    assert verdict["status"] == "defects_found"
    assert "Коррозия (12% площади)" in verdict["reason"]
