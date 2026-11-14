"""Канал детектора аномалий anomaly/v2 (ленивая загрузка, безопасная деградация).

Модель грузится при первом кадре, не при старте сервера. Ошибка (нет torch,
модели, битый файл) выключает канал, но не роняет пайплайн. Считаем не каждый
кадр: раз в `ANOMALY_EVERY`, между вызовами отдаём кэш.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Sequence

from ..config import get_settings

_logger = logging.getLogger(__name__)

SIGN_ID = "texture_anomaly"
SIGN_LABEL = "Аномалия текстуры"


class AnomalyChannel:
    """Обёртка над `anomaly.v2.AnomalyV2Model` с кэшем и деградацией."""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        enabled: bool | None = None,
        every: int | None = None,
        threshold_quantile: float | None = None,
        threshold_factor: float | None = None,
    ) -> None:
        settings = get_settings()
        self.model_dir = Path(model_dir or settings.anomaly_model_dir)
        self.enabled = (
            bool(settings.anomaly_enabled) if enabled is None else bool(enabled)
        )
        self.every = max(1, int(every if every is not None else settings.anomaly_every))
        self.threshold_quantile = float(
            threshold_quantile
            if threshold_quantile is not None
            else settings.anomaly_threshold_quantile
        )
        self.threshold_factor = max(
            1.0,
            float(
                threshold_factor
                if threshold_factor is not None
                else settings.anomaly_threshold_factor
            ),
        )
        self._lock = threading.RLock()
        self._model: Any | None = None
        self._loaded = False
        self._load_attempted = False
        self._disabled = not self.enabled
        self._reason = "disabled" if not self.enabled else "not_loaded"
        self._device: str | None = None
        self._counters: dict[str, int] = {}
        self._cache: dict[str, list[dict[str, Any]]] = {}

    # Статус

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": bool(self.enabled and not self._disabled),
                "loaded": self._loaded,
                "reason": self._reason,
                "device": self._device,
                "every": self.every,
                "threshold_quantile": self.threshold_quantile,
                "threshold_factor": self.threshold_factor,
                "model_dir": str(self.model_dir),
            }

    # Загрузка/скоринг

    def _ensure_model(self) -> bool:
        with self._lock:
            if self._loaded:
                return True
            if self._disabled or self._load_attempted:
                return False
            self._load_attempted = True
            try:
                import torch  # локальный импорт: сервер живёт и без torch

                from anomaly.v2.detector import AnomalyV2Model

                # На 6 ГБ видеопамяти VLM и torch-модели не уживаются:
                # по умолчанию канал аномалий считает на CPU (30-40 мс),
                # а GPU оставляем локальной vision-модели.
                import os as _os

                device = (_os.getenv("ANOMALY_DEVICE") or "").strip().lower()
                if device not in {"cuda", "cpu"}:
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                self._model = AnomalyV2Model.load(self.model_dir, device=device)
                self._loaded = True
                self._device = device
                self._reason = None
                _logger.info(
                    "anomaly/v2 загружен: %s (device=%s)",
                    self.model_dir,
                    device,
                )
                return True
            except Exception as exc:  # noqa: BLE001 - канал не должен ронять сервер
                self._disabled = True
                self._loaded = False
                self._reason = f"{type(exc).__name__}: {exc}"
                _logger.warning("anomaly/v2 отключён: %s", self._reason)
                return False

    def score_frame(
        self,
        frame_bgr: Any,
        layout: dict[str, Any],
        homography: Any,
        visible_components: Sequence[str],
        equipment_id: str,
    ) -> list[dict[str, Any]]:
        """Возвращает признаки `texture_anomaly` для аномальных узлов."""
        if not self.enabled:
            return []
        with self._lock:
            count = self._counters.get(equipment_id, 0) + 1
            self._counters[equipment_id] = count
            should_score = count == 1 or count % self.every == 0
            if not should_score:
                return [dict(item) for item in self._cache.get(equipment_id, [])]
            if not self._ensure_model():
                return []
            model = self._model
            components = set(model.components()) if model is not None else set()
            results: list[dict[str, Any]] = []
            try:
                from anomaly.v2.canonical import canonical_crop

                # Кожух муфты — ровная окрашенная пластина без текстуры:
                # аномалиям там нечего сравнивать, красная краска даёт ложные.
                skip = {"coupling_guard"}
                for component_id in visible_components:
                    if component_id in skip or component_id not in components:
                        continue
                    crop = canonical_crop(
                        frame_bgr,
                        layout,
                        component_id,
                        homography=homography,
                    )
                    if crop is None:
                        continue
                    detector = getattr(model, "detectors", {}).get(component_id)
                    if detector is not None:
                        # Без TTA/heatmap: для backend важны score/area, а не карта.
                        payload = detector.score(crop, tta=False, heatmap=False)
                    else:
                        payload = model.score(component_id, crop)
                    score = float(payload.get("score") or 0.0)
                    area = float(payload.get("area") or 0.0)
                    threshold = self._threshold(payload)
                    if not (score > threshold):
                        continue
                    results.append(
                        {
                            "sign": SIGN_ID,
                            "component_id": component_id,
                            "confidence": round(
                                min(1.0, score / max(threshold, 1e-9)), 4
                            ),
                            "source": "anomaly",
                            "present": True,
                            "score": round(score, 4),
                            "area": round(area, 4),
                            "threshold": round(threshold, 4),
                            "is_anomaly": True,
                            "latency_ms": payload.get("latency_ms"),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                self._disabled = True
                self._reason = f"{type(exc).__name__}: {exc}"
                _logger.warning("anomaly/v2 отключён на кадре: %s", self._reason)
                results = []
            self._cache[equipment_id] = [dict(item) for item in results]
            return results

    def reset(self) -> None:
        """Сбрасывает кэш/счётчики канала (смена layout или оборудования)."""
        with self._lock:
            self._counters.clear()
            self._cache.clear()

    def _threshold(self, payload: dict[str, Any]) -> float:
        if self.threshold_quantile >= 0.98:
            base = float(payload.get("threshold_q99") or 0.0)
        else:
            base = float(payload.get("threshold") or 0.0)
        return base * self.threshold_factor


__all__ = ["AnomalyChannel", "SIGN_ID", "SIGN_LABEL"]
