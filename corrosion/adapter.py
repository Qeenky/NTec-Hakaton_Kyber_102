"""Модуль детекции коррозии по узлам для бэкенда.

Чистый интерфейс, без зависимостей от backend/:
    from corrosion.adapter import detect_corrosion
    result = detect_corrosion(frame_bgr, component_polygons)
    # {"pump": {"area_pct": 12.3, "boxes": [...], "confidence": 0.71, "detected": True}, ...}

`component_polygons` — зоны узлов в пикселях кадра, как их отдаёт ar_board
(`project_components` → список с "polygon"), но допускается и просто dict
{component_id: [[x, y], ...]}.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULT_WEIGHTS = ROOT / "runs" / "train" / "weights" / "best.pt"
ONNX_PATH = ROOT / "models" / "corrosion_yolo11n.onnx"


def _as_polygons(component_polygons: Any) -> Dict[str, np.ndarray]:
    """Приводит вход к {id: Nx2 float32}. Понимает и dict, и список из ar_board."""
    out: Dict[str, np.ndarray] = {}
    if component_polygons is None:
        return out
    if isinstance(component_polygons, Mapping):
        for key, value in component_polygons.items():
            poly = value.get("polygon", value) if isinstance(value, Mapping) else value
            arr = np.asarray(poly, dtype=np.float32).reshape(-1, 2)
            if len(arr) >= 3:
                out[str(key)] = arr
        return out
    for item in component_polygons:  # список словарей из ar_board.project_components
        poly = item.get("polygon") or []
        arr = np.asarray(poly, dtype=np.float32).reshape(-1, 2)
        if len(arr) >= 3:
            out[str(item.get("component_id", len(out)))] = arr
    return out


def polygon_coverage(polygon: np.ndarray, boxes: np.ndarray, shape: tuple[int, int],
                     scale: float = 1.0) -> tuple[float, float]:
    """Доля площади полигона, перекрытая объединением боксов.

    Возвращает (covered_fraction, covered_area_px). Боксы — в пикселях кадра.
    """
    h, w = shape
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [np.round(polygon * scale).astype(np.int32)], 1)
    total = int(mask.sum())
    if total == 0:
        return 0.0, 0.0
    cover = np.zeros_like(mask)
    for box in boxes:
        x1, y1, x2, y2 = [float(v) * scale for v in box[:4]]
        x1, y1 = max(0, int(np.floor(x1))), max(0, int(np.floor(y1)))
        x2, y2 = min(w - 1, int(np.ceil(x2))), min(h - 1, int(np.ceil(y2)))
        if x2 <= x1 or y2 <= y1:
            continue
        cover[y1:y2 + 1, x1:x2 + 1] = 1
    inter = int(np.logical_and(mask, cover).sum())
    return inter / float(total), float(inter / (scale * scale))


class CorrosionDetector:
    """Обёртка над YOLO11n: кадр → боксы → площадь по узлам.

    Детекция по узлам идёт **по кропу зоны узла** (`crop_nodes=True`): на кадре
    1280x960 узел занимает 3-8 % площади, после ресайза до 640 px мелкие
    очаги коррозии деградируют. Кроп узла масштабируется до `imgsz` — это
    стандартный приём для мелких объектов и не меняет саму модель.
    """

    def __init__(self, weights: str | Path | None = None, *, conf: float = 0.25,
                 iou: float = 0.45, imgsz: int = 640, device: str | None = None,
                 max_side: int = 1280, min_area_pct: float = 0.3,
                 crop_nodes: bool = True, crop_margin: float = 0.06) -> None:
        from ultralytics import YOLO

        path = Path(weights or os.environ.get("CORROSION_WEIGHTS") or DEFAULT_WEIGHTS)
        if not path.is_file():
            raise FileNotFoundError(f"нет весов модели: {path}")
        self.model = YOLO(str(path))
        self.conf = float(conf)
        self.iou = float(iou)
        self.imgsz = int(imgsz)
        self.device = device
        self.max_side = int(max_side)
        self.min_area_pct = float(min_area_pct)
        self.crop_nodes = bool(crop_nodes)
        self.crop_margin = float(crop_margin)

    def predict_boxes(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Боксы коррозии в пикселях кадра: Nx5 (x1, y1, x2, y2, conf)."""
        if frame_bgr is None or frame_bgr.size == 0:
            return np.zeros((0, 5), dtype=np.float32)
        h, w = frame_bgr.shape[:2]
        scale = min(1.0, self.max_side / float(max(h, w)))
        img = frame_bgr
        if scale < 1.0:
            img = cv2.resize(frame_bgr, (int(round(w * scale)), int(round(h * scale))),
                             interpolation=cv2.INTER_AREA)
        return self._predict(img) / np.array([scale, scale, scale, scale, 1.0], dtype=np.float32)

    def _predict(self, img: np.ndarray) -> np.ndarray:
        """Инференс одного изображения в его пикселях."""
        res = self.model.predict(img, conf=self.conf, iou=self.iou, imgsz=self.imgsz,
                                 device=self.device, verbose=False)[0]
        boxes = res.boxes
        if boxes is None or len(boxes) == 0:
            return np.zeros((0, 5), dtype=np.float32)
        xyxy = boxes.xyxy.cpu().numpy()
        conf = boxes.conf.cpu().numpy().reshape(-1, 1)
        return np.hstack([xyxy, conf]).astype(np.float32)

    def _predict_in_polygon(self, frame: np.ndarray, poly: np.ndarray) -> np.ndarray:
        """Боксы внутри зоны узла: кроп по bbox зоны, масштаб до imgsz."""
        h, w = frame.shape[:2]
        x1, y1 = np.floor(poly.min(axis=0)).astype(int)
        x2, y2 = np.ceil(poly.max(axis=0)).astype(int)
        mx = int(round((x2 - x1) * self.crop_margin))
        my = int(round((y2 - y1) * self.crop_margin))
        x1, y1 = max(0, x1 - mx), max(0, y1 - my)
        x2, y2 = min(w, x2 + mx), min(h, y2 + my)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return np.zeros((0, 5), dtype=np.float32)
        ch, cw = crop.shape[:2]
        scale = self.imgsz / float(max(ch, cw))
        if abs(scale - 1.0) > 0.02:
            interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
            crop = cv2.resize(crop, (max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))),
                              interpolation=interp)
        boxes = self._predict(crop)
        if len(boxes) == 0:
            return boxes
        boxes[:, [0, 2]] = boxes[:, [0, 2]] / scale + x1
        boxes[:, [1, 3]] = boxes[:, [1, 3]] / scale + y1
        return boxes

    def detect(self, frame_bgr: np.ndarray,
               component_polygons: Any) -> Dict[str, Dict[str, Any]]:
        """Площадь поражения по каждому узлу кадра."""
        polys = _as_polygons(component_polygons)
        h, w = frame_bgr.shape[:2]
        out: Dict[str, Dict[str, Any]] = {}
        if not polys:
            return out
        dets_all = None if self.crop_nodes else self.predict_boxes(frame_bgr)
        for cid, poly in polys.items():
            dets = self._predict_in_polygon(frame_bgr, poly) if self.crop_nodes else dets_all
            inside: List[Dict[str, Any]] = []
            for x1, y1, x2, y2, c in dets:
                cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                if cv2.pointPolygonTest(poly.reshape(-1, 1, 2), (float(cx), float(cy)), False) >= 0:
                    inside.append({"box": [round(float(x1), 1), round(float(y1), 1),
                                           round(float(x2), 1), round(float(y2), 1)],
                                   "confidence": round(float(c), 4)})
            arr = np.asarray([d["box"] for d in inside], dtype=np.float32) if inside else np.zeros((0, 4))
            frac, _ = polygon_coverage(poly, arr, (h, w))
            area_pct = round(100.0 * float(frac), 2)
            conf = round(float(np.max([d["confidence"] for d in inside])), 4) if inside else 0.0
            out[cid] = {
                "area_pct": area_pct,
                "boxes": inside,
                "confidence": conf,
                "detected": bool(area_pct >= self.min_area_pct),
            }
        return out


_DEFAULT: CorrosionDetector | None = None


def get_detector(**kwargs: Any) -> CorrosionDetector:
    """Ленивый синглтон — модель грузится один раз на процесс."""
    global _DEFAULT
    if _DEFAULT is None or kwargs:
        _DEFAULT = CorrosionDetector(**kwargs)
    return _DEFAULT


def detect_corrosion(frame_bgr: np.ndarray, component_polygons: Any,
                     **kwargs: Any) -> Dict[str, Dict[str, Any]]:
    """Главная точка входа для бэкенда.

    frame_bgr           — кадр BGR (numpy, HxWx3);
    component_polygons  — {component_id: [[x, y], ...]} в пикселях кадра.

    Возвращает {component_id: {"area_pct", "boxes", "confidence", "detected"}}.
    """
    return get_detector(**kwargs).detect(frame_bgr, component_polygons)


def summarize(result: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Короткая сводка для API/UI."""
    worst = max(result.items(), key=lambda kv: kv[1]["area_pct"], default=(None, None))
    return {
        "components": dict(result),
        "detected_count": sum(1 for v in result.values() if v.get("detected")),
        "worst_component": worst[0],
        "worst_area_pct": worst[1]["area_pct"] if worst[1] else 0.0,
        "max_area_pct": max((v["area_pct"] for v in result.values()), default=0.0),
    }
