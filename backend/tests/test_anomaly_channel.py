"""Канал anomaly/v2: порог, кэш, деградация и интеграция с реестром."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.analytics import AnalyticsStore
from app.cv.anomaly_channel import AnomalyChannel
from app.verdict import build_verdict

EQUIPMENT = "PUMP-001"
NOW = datetime.now(timezone.utc)
LAYOUT = {
    "components": [
        {
            "component_id": "pump",
            "polygon_px": [[10, 10], [50, 10], [50, 50], [10, 50]],
        }
    ]
}


class FakeModel:
    """Минимальная замена AnomalyV2Model для тестов."""

    def __init__(self, score: float = 1.3, threshold_q99: float = 1.2) -> None:
        self.score_value = score
        self.threshold_q99 = threshold_q99
        self.calls = 0

    def components(self) -> list[str]:
        return ["pump"]

    def score(self, component_id: str, crop: np.ndarray) -> dict:
        self.calls += 1
        return {
            "score": self.score_value,
            "area": 0.25,
            "threshold": 1.0,
            "threshold_q99": self.threshold_q99,
            "is_anomaly": True,
            "latency_ms": 1.0,
        }


def _channel(every: int = 5, quantile: float = 0.99) -> tuple[AnomalyChannel, FakeModel]:
    channel = AnomalyChannel(
        model_dir="unused",
        enabled=True,
        every=every,
        threshold_quantile=quantile,
        threshold_factor=1.0,
    )
    model = FakeModel()
    channel._model = model
    channel._loaded = True
    channel._device = "cpu"
    return channel, model


def test_threshold_and_confidence() -> None:
    channel, model = _channel(quantile=0.99)
    frame = np.zeros((64, 64, 3), np.uint8)
    signs = channel.score_frame(frame, LAYOUT, np.eye(3), ["pump"], EQUIPMENT)
    assert model.calls == 1
    assert signs and signs[0]["sign"] == "texture_anomaly"
    assert signs[0]["is_anomaly"] is True
    assert signs[0]["threshold"] == pytest.approx(1.2)
    assert signs[0]["confidence"] == 1.0

    # score ниже q99-порога — признака нет, хотя модель пометила is_anomaly.
    model.score_value = 1.1
    channel._cache.clear()
    channel._counters.clear()
    assert channel.score_frame(frame, LAYOUT, np.eye(3), ["pump"], EQUIPMENT) == []


def test_every_n_and_cache() -> None:
    channel, model = _channel(every=5)
    frame = np.zeros((64, 64, 3), np.uint8)
    for _ in range(4):
        channel.score_frame(frame, LAYOUT, np.eye(3), ["pump"], EQUIPMENT)
    assert model.calls == 1  # первый кадр
    assert channel.score_frame(frame, LAYOUT, np.eye(3), ["pump"], EQUIPMENT)
    assert model.calls == 2  # пятый кадр


def test_error_disables_channel_without_raising() -> None:
    channel, model = _channel()

    def boom(component_id: str, crop: np.ndarray) -> dict:
        raise RuntimeError("cuda out of memory")

    model.score = boom  # type: ignore[method-assign]
    signs = channel.score_frame(
        np.zeros((64, 64, 3), np.uint8), LAYOUT, np.eye(3), ["pump"], EQUIPMENT
    )
    assert signs == []
    status = channel.status()
    assert status["enabled"] is False
    assert "cuda out of memory" in status["reason"]


def test_disabled_channel_does_not_load_model() -> None:
    channel = AnomalyChannel(model_dir="unused", enabled=False)
    signs = channel.score_frame(
        np.zeros((64, 64, 3), np.uint8), LAYOUT, np.eye(3), ["pump"], EQUIPMENT
    )
    assert signs == []
    assert channel.status()["enabled"] is False
    assert channel.status()["loaded"] is False
    assert channel.status()["reason"] == "disabled"


def test_texture_anomaly_in_registry_and_verdict() -> None:
    store = AnalyticsStore(window_sec=60, confirm_ratio=0.3, confirm_frames=3)
    for index in range(3):
        store.update(
            EQUIPMENT,
            overlays=[{"component_id": "pump", "visible": True}],
            signs=[
                {
                    "sign": "texture_anomaly",
                    "component_id": "pump",
                    "confidence": 1.0,
                    "source": "anomaly",
                    "present": True,
                    "score": 1.4,
                    "area": 0.25,
                    "threshold": 1.2,
                }
            ],
            now=NOW + timedelta(seconds=index),
        )
    summary = store.summary(EQUIPMENT, now=NOW + timedelta(seconds=3))
    component = summary["components"][0]
    sign = next(item for item in component["signs"] if item["sign"] == "texture_anomaly")
    assert sign["status"] == "detected"
    assert sign["sources"] == ["anomaly"]
    assert sign["score"] == pytest.approx(1.4)
    assert sign["area"] == pytest.approx(0.25)
    assert sign["threshold"] == pytest.approx(1.2)

    verdict = build_verdict(summary)
    assert verdict["status"] == "defects_found"
    assert verdict["components_with_issues"] == ["pump"]
    assert "Аномалия текстуры" in verdict["reason"]
