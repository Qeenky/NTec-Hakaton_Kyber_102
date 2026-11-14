"""Канал детектора коррозии `corrosion/` (ONNX, ленивая загрузка).

Модель работает только по кропу зоны узла: полигон узла переводится
перспективным преобразованием в квадрат 640×640, инференс ищет боксы коррозии,
боксы возвращаются в координаты кадра. `area_pct` — доля площади узла,
покрытая объединением боксов (верхняя оценка: боксы, не маски).
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from ..config import get_settings

_logger = logging.getLogger(__name__)

SIGN_ID = "corrosion"
SIGN_LABEL = "Коррозия"
INPUT_SIZE = 640


class CorrosionChannel:
    """Обёртка над ONNX-детектором коррозии с кэшем и деградацией."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        enabled: bool | None = None,
        conf: float | None = None,
        every: int | None = None,
        min_area_pct: float | None = None,
    ) -> None:
        settings = get_settings()
        self.model_path = Path(model_path or settings.corrosion_model_path)
        self.enabled = (
            bool(settings.corrosion_enabled) if enabled is None else bool(enabled)
        )
        self.conf = float(conf if conf is not None else settings.corrosion_conf)
        self.every = max(1, int(every if every is not None else settings.corrosion_every))
        self.min_area_pct = float(
            min_area_pct
            if min_area_pct is not None
            else settings.corrosion_min_area_pct
        )
        self._lock = threading.RLock()
        self._session: Any | None = None
        self._input_name: str | None = None
        self._loaded = False
        self._load_attempted = False
        self._disabled = not self.enabled
        self._reason = "disabled" if not self.enabled else "not_loaded"
        self._providers: list[str] = []
        self._counters: dict[str, int] = {}
        self._cache: dict[str, list[dict[str, Any]]] = {}

    # Статус

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": bool(self.enabled and not self._disabled),
                "loaded": self._loaded,
                "reason": self._reason,
                "every": self.every,
                "conf": self.conf,
                "min_area_pct": self.min_area_pct,
                "model_path": str(self.model_path),
                "providers": list(self._providers),
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._cache.clear()

    # Загрузка/инференс

    def _ensure_model(self) -> bool:
        with self._lock:
            if self._loaded:
                return True
            if self._disabled or self._load_attempted:
                return False
            self._load_attempted = True
            try:
                import onnxruntime as ort

                # CUDA-провайдеру нужны cuBLAS/cuDNN; они уже лежат в torch/lib.
                try:
                    import torch

                    torch_lib = Path(torch.__file__).resolve().parent / "lib"
                    if torch_lib.is_dir():
                        os.add_dll_directory(str(torch_lib))
                        os.environ["PATH"] = (
                            str(torch_lib)
                            + os.pathsep
                            + os.environ.get("PATH", "")
                        )
                except Exception:  # noqa: BLE001 - CPU-провайдер тоже подойдёт
                    pass

                available = ort.get_available_providers()
                # Видеопамять делим: VLM нужнее на GPU, детектор коррозии
                # спокойно работает на CPU. Управляется CORROSION_PROVIDERS.
                import os as _os

                allowed = (_os.getenv("CORROSION_PROVIDERS") or "cuda,cpu").lower()
                providers = [
                    name
                    for name in ("CUDAExecutionProvider", "CPUExecutionProvider")
                    if name in available
                    and ("cuda" if name.startswith("CUDA") else "cpu") in allowed
                ] or ["CPUExecutionProvider"]
                self._session = ort.InferenceSession(
                    str(self.model_path), providers=providers
                )
                self._input_name = self._session.get_inputs()[0].name
                self._providers = providers
                self._loaded = True
                self._reason = None
                _logger.info(
                    "corrosion ONNX загружен: %s (%s)",
                    self.model_path,
                    ",".join(providers),
                )
                return True
            except Exception as exc:  # noqa: BLE001 - сервер не должен падать
                self._disabled = True
                self._loaded = False
                self._reason = f"{type(exc).__name__}: {exc}"
                _logger.warning("corrosion ONNX отключён: %s", self._reason)
                return False

    def score_frame(
        self,
        frame_bgr: np.ndarray,
        projected: Sequence[dict[str, Any]],
        equipment_id: str,
    ) -> list[dict[str, Any]]:
        """Возвращает признаки коррозии по видимым узлам."""
        if not self.enabled:
            return []
        with self._lock:
            count = self._counters.get(equipment_id, 0) + 1
            self._counters[equipment_id] = count
            if not (count == 1 or count % self.every == 0):
                return [dict(item) for item in self._cache.get(equipment_id, [])]
            if not self._ensure_model():
                return []
            results: list[dict[str, Any]] = []
            try:
                for item in projected:
                    if not item.get("visible"):
                        continue
                    component_id = str(item.get("component_id"))
                    polygon = item.get("polygon") or []
                    if len(polygon) < 4:
                        continue
                    sign = self._detect_component(
                        frame_bgr, component_id, polygon
                    )
                    if sign is not None:
                        results.append(sign)
            except Exception as exc:  # noqa: BLE001
                self._disabled = True
                self._reason = f"{type(exc).__name__}: {exc}"
                _logger.warning("corrosion ONNX отключён на кадре: %s", self._reason)
                results = []
            self._cache[equipment_id] = [dict(item) for item in results]
            return results

    def _detect_component(
        self,
        frame: np.ndarray,
        component_id: str,
        polygon: Sequence[Sequence[float]],
    ) -> dict[str, Any] | None:
        src = np.asarray(polygon[:4], dtype=np.float32).reshape(4, 2)
        edge = float(INPUT_SIZE - 1)
        dst = np.array(
            [[0.0, 0.0], [edge, 0.0], [edge, edge], [0.0, edge]],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(src, dst)
        crop = cv2.warpPerspective(
            frame,
            matrix,
            (INPUT_SIZE, INPUT_SIZE),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        blob = np.ascontiguousarray(
            rgb.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        )
        assert self._session is not None and self._input_name is not None
        output = self._session.run(None, {self._input_name: blob})[0]
        pred = np.asarray(output)[0].T  # (8400, 4+nc)
        if pred.shape[1] < 5:
            return None
        scores = pred[:, 4]
        keep = scores >= self.conf
        if not np.any(keep):
            return None
        boxes = pred[keep, :4]
        scores = scores[keep]
        xyxy = np.empty_like(boxes)
        xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
        xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
        xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2.0
        xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2.0
        np.clip(xyxy, 0.0, edge, out=xyxy)

        # area_pct: доля площади узла (rectified 640×640), покрытая боксами.
        mask = np.zeros((INPUT_SIZE, INPUT_SIZE), np.uint8)
        for x1, y1, x2, y2 in xyxy:
            cv2.rectangle(
                mask,
                (int(np.floor(x1)), int(np.floor(y1))),
                (int(np.ceil(x2)), int(np.ceil(y2))),
                1,
                -1,
            )
        area_pct = round(100.0 * float(mask.sum()) / (INPUT_SIZE * INPUT_SIZE), 2)
        if area_pct < self.min_area_pct:
            return None

        inverse = np.linalg.inv(matrix)
        frame_boxes: list[list[float]] = []
        for x1, y1, x2, y2 in xyxy:
            corners = np.array(
                [[x1, y1, 1.0], [x2, y1, 1.0], [x2, y2, 1.0], [x1, y2, 1.0]],
                dtype=np.float64,
            ) @ inverse.T
            corners = corners[:, :2] / corners[:, 2:3]
            frame_boxes.append(
                [
                    round(float(corners[:, 0].min()), 1),
                    round(float(corners[:, 1].min()), 1),
                    round(float(corners[:, 0].max()), 1),
                    round(float(corners[:, 1].max()), 1),
                ]
            )
        confidence = round(float(np.max(scores)), 4)
        return {
            "sign": SIGN_ID,
            "component_id": component_id,
            "confidence": confidence,
            "source": "yolo",
            "present": True,
            "area_pct": area_pct,
            "score": confidence,
            "boxes": frame_boxes,
            "weak": bool(area_pct < 5.0),
        }


__all__ = ["CorrosionChannel", "SIGN_ID", "SIGN_LABEL"]
