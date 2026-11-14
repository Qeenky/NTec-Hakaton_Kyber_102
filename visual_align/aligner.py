"""Безмаркерная привязка зон узлов к кадру по эталонному изображению.

Идея: эталон (кадр оборудования + размеченные полигоны узлов) один раз
описывается локальными признаками (ORB и AKAZE). На каждом кадре ищутся те же
признаки, сопоставляются по дескрипторам, и по инлайерам RANSAC-ом оценивается
гомография "эталон -> кадр". Дальше полигоны зон просто переносятся этой
гомографией. ArUco/маркеры не используются вообще.

Конвейер `register()`:

1. кадр -> grayscale -> CLAHE (выравнивание освещения);
2. быстрый отказ на почти однородных кадрах (мало текстуры);
3. ORB и AKAZE независимо: BFMatcher + Lowe ratio 0.75 + `findHomography`
   (RANSAC, порог 3.0 px);
4. выбирается вариант с наибольшим числом инлайеров;
5. проверки правдоподобия: доля инлайеров, выпуклость/площадь проекции
   планшета, пространственный разброс инлайеров и фотометрическая
   согласованность (NCC) эталона с кадром;
6. если кадр не прошёл — `hold_last_pose`: отдаётся последняя валидная
   гомография с `stale=True` и `age_sec`.

Рядом живёт fallback `register_by_quad()` (см. `visual_align/quad.py`):
контур светлого печатного листа -> гомография по четырём углам. Он выручает,
когда планшет занимает меньше ~50% кадра и признаков для ORB/AKAZE мало.
Приоритет всегда у признаков; quad — только спасение.

Формула уверенности (0..1), задокументирована и используется как есть::

    term_inliers = min(1, inliers / 60)
    term_ratio   = min(1, inlier_ratio / 0.60)
    term_reproj  = exp(-median_reproj_px / 5.0)
    confidence   = 0.50*term_inliers + 0.30*term_ratio + 0.20*term_reproj

То есть 60 инлайеров и доля 0.60 считаются "полным" качеством, а ошибка
репроекции 5 px уменьшает последний член в e раз. Диапазон строго [0, 1].
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np

from . import quad as quad_fallback

# --- значения по умолчанию (все настраиваются через конструктор) -------------
DEFAULT_MIN_INLIERS = 15
DEFAULT_MIN_INLIER_RATIO = 0.25
DEFAULT_HOLD_SEC = 1.5
DEFAULT_ALPHA = 0.35
DEFAULT_MAX_REF_WIDTH = 1600
DEFAULT_MIN_NCC = 0.30
DEFAULT_RANSAC_PX = 3.0
DEFAULT_RATIO = 0.75
# NCC для quad-привязки: контур листа даёт геометрию, а содержимое проверяем
# мягче, чем для признаков (на 18% кадра обрезок эталона — всего ~55 px).
DEFAULT_MIN_QUAD_NCC = 0.30
METHODS = ("orb", "akaze")

_HAMMING_METHODS = {"orb", "akaze"}  # AKAZE по умолчанию DESCRIPTOR_MLDB -> бинарный


# результат
@dataclass
class AlignmentResult:
    """Результат одного вызова `FeatureAligner.register`."""

    homography: Optional[np.ndarray]          # 3x3: пиксели эталона -> пиксели кадра
    inliers: int = 0
    inlier_ratio: float = 0.0
    method: str = "none"                      # "orb" | "akaze" | "none"
    confidence: float = 0.0
    stale: bool = False                       # поза взята из hold_last_pose
    age_sec: float = 0.0                      # возраст удержанной позы
    matches: int = 0                          # хороших матчей после ratio-теста
    reproj_px: float = 0.0                    # медианная ошибка инлайеров, px
    ncc: float = 0.0                          # фотометрическая согласованность
    frame_size: Tuple[int, int] = (0, 0)
    reason: str = ""                          # почему отказ (для отладки)
    timings_ms: Dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Есть ли валидная (в т.ч. удержанная) гомография."""
        return self.homography is not None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-совместимое представление (для backend/отчётов)."""
        return {
            "ok": self.ok,
            "homography": None if self.homography is None
            else [[round(float(v), 8) for v in row] for row in self.homography],
            "inliers": int(self.inliers),
            "inlier_ratio": round(float(self.inlier_ratio), 4),
            "method": self.method,
            "confidence": round(float(self.confidence), 4),
            "stale": bool(self.stale),
            "age_sec": round(float(self.age_sec), 3),
            "matches": int(self.matches),
            "reproj_px": round(float(self.reproj_px), 3),
            "ncc": round(float(self.ncc), 4),
            "frame_size": [int(self.frame_size[0]), int(self.frame_size[1])],
            "reason": self.reason,
        }


# утилиты
def confidence_from(inliers: int, inlier_ratio: float, reproj_px: float) -> float:
    """Уверенность 0..1 по формуле из docstring модуля."""
    term_inliers = min(1.0, max(0.0, float(inliers)) / 60.0)
    term_ratio = min(1.0, max(0.0, float(inlier_ratio)) / 0.60)
    term_reproj = math.exp(-max(0.0, float(reproj_px)) / 5.0)
    value = 0.50 * term_inliers + 0.30 * term_ratio + 0.20 * term_reproj
    return float(min(1.0, max(0.0, value)))


def as_bgr(image: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """Приводит вход к 3-канальному BGR uint8; None для мусора."""
    if image is None or getattr(image, "size", 0) == 0:
        return None
    array = image
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    if array.ndim == 2:
        array = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
    if array.ndim != 3 or array.shape[2] == 4:
        if array.ndim == 3 and array.shape[2] == 4:
            array = cv2.cvtColor(array, cv2.COLOR_BGRA2BGR)
        else:
            return None
    if array.shape[0] < 8 or array.shape[1] < 8:
        return None
    return array


def _clahe(gray: np.ndarray, clip_limit: float = 2.0) -> np.ndarray:
    """CLAHE — выравнивание локального контраста перед детекцией."""
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _to_gray(image_bgr: np.ndarray) -> np.ndarray:
    if image_bgr.ndim == 2:
        return image_bgr
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)


def project_points(homography: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Перспективное преобразование Nx2 точек, безопасное к w<=0."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    out = cv2.perspectiveTransform(pts, np.asarray(homography, dtype=np.float64))
    return out.reshape(-1, 2)


def _poly_area(points: np.ndarray) -> float:
    return abs(float(cv2.contourArea(np.asarray(points, dtype=np.float32))))


def _kp_array(keypoints: Sequence[cv2.KeyPoint]) -> np.ndarray:
    rows = [[k.pt[0], k.pt[1], k.size, k.angle, k.response, k.octave, k.class_id]
            for k in keypoints]
    return np.asarray(rows, dtype=np.float32).reshape(-1, 7)


def _kp_from_array(array: np.ndarray) -> List[cv2.KeyPoint]:
    result: List[cv2.KeyPoint] = []
    for x, y, size, angle, response, octave, class_id in np.asarray(array).reshape(-1, 7):
        result.append(cv2.KeyPoint(float(x), float(y), float(size), float(angle),
                                   float(response), int(octave), int(class_id)))
    return result


def _detector(method: str):
    """Создаёт детектор признаков. ORB — быстрый, AKAZE — устойчивее."""
    if method == "orb":
        return cv2.ORB_create(nfeatures=4000, scaleFactor=1.2, nlevels=8,
                              edgeThreshold=19, patchSize=31,
                              fastThreshold=12, scoreType=cv2.ORB_HARRIS_SCORE)
    if method == "akaze":
        return cv2.AKAZE_create(descriptor_type=cv2.AKAZE_DESCRIPTOR_MLDB,
                                descriptor_size=0, descriptor_channels=3,
                                threshold=0.001, nOctaves=4, nOctaveLayers=4,
                                diffusivity=cv2.KAZE_DIFF_PM_G2)
    raise ValueError(f"неизвестный метод: {method}")


def _matcher(method: str) -> Tuple[cv2.BFMatcher, Optional[int]]:
    """BFMatcher с нормой под тип дескриптора + абсолютный порог расстояния."""
    if method == "orb":
        return cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False), 80
    # AKAZE MLDB — бинарный дескриптор, норма Хэмминга.
    return cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False), None


# сглаживание
class Smoother:
    """EMA по углам полигонов (alpha=0.35), как в ar_board.

    Состояние хранится по имени зоны. Если зона не приходила дольше
    `reset_after` секунд — накопленное значение сбрасывается.
    """

    def __init__(self, alpha: float = DEFAULT_ALPHA, reset_after: float = 1.0) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha должен быть в диапазоне (0, 1]")
        self.alpha = float(alpha)
        self.reset_after = float(reset_after)
        self._state: Dict[str, np.ndarray] = {}
        self._last_seen: Optional[float] = None

    def reset(self) -> None:
        """Забыть накопленное состояние."""
        self._state.clear()
        self._last_seen = None

    def update(self, zones: Mapping[str, Sequence[Sequence[float]]],
               timestamp: Optional[float] = None) -> Dict[str, List[List[float]]]:
        """Возвращает сглаженные полигоны (копию, исходные не меняются)."""
        now = time.monotonic() if timestamp is None else float(timestamp)
        if self._last_seen is not None and now - self._last_seen > self.reset_after:
            self._state.clear()
        result: Dict[str, List[List[float]]] = {}
        for name, poly in zones.items():
            current = np.asarray(poly, dtype=np.float64).reshape(-1, 2)
            if len(current) < 3:
                result[name] = [[float(x), float(y)] for x, y in current]
                continue
            previous = self._state.get(str(name))
            if previous is None or previous.shape != current.shape:
                value = current
            else:
                value = self.alpha * current + (1.0 - self.alpha) * previous
            self._state[str(name)] = value
            result[name] = [[round(float(x), 3), round(float(y), 3)] for x, y in value]
        if zones:
            self._last_seen = now
        return result


# эталон
def sheet_quad_from_rect(board_size: Tuple[float, float],
                         sheet_rect: Optional[Sequence[float]] = None) -> np.ndarray:
    """Четырёхугольник листа в пикселях эталона: tl, tr, br, bl.

    `sheet_rect` = (x0, y0, x1, y1) — где в координатах эталона лежит
    физический печатный лист. По умолчанию это сам эталон (0..board_size).
    Для эталона-обрезка (например, фото узла, вырезанное из A3) лист шире
    картинки, и координаты могут быть отрицательными — это нормально.
    """
    width, height = float(board_size[0]), float(board_size[1])
    if sheet_rect is None:
        x0, y0, x1, y1 = 0.0, 0.0, width, height
    else:
        x0, y0, x1, y1 = (float(v) for v in sheet_rect)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"некорректный sheet_rect: {sheet_rect!r}")
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)


@dataclass
class Reference:
    """Эталон: изображение, зоны в его пикселях и предвычисленные признаки."""

    name: str
    image: np.ndarray                       # BGR, уменьшенная копия
    ref_scale: float                        # image_px = original_px * ref_scale
    zones: Dict[str, np.ndarray]            # полигоны в пикселях ОРИГИНАЛА
    board_size: Tuple[float, float]         # (w, h) оригинала
    labels: Dict[str, str] = field(default_factory=dict)
    features: Dict[str, Tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
    # Лист в пикселях оригинала (tl, tr, br, bl). None -> лист = board_size.
    sheet_quad: Optional[np.ndarray] = None

    @property
    def scale_matrix(self) -> np.ndarray:
        """Оригинал -> уменьшенная копия."""
        return np.array([[self.ref_scale, 0.0, 0.0],
                         [0.0, self.ref_scale, 0.0],
                         [0.0, 0.0, 1.0]], dtype=np.float64)

    @property
    def sheet_corners(self) -> np.ndarray:
        """Углы листа в пикселях оригинала (всегда 4x2)."""
        if self.sheet_quad is None:
            return sheet_quad_from_rect(self.board_size)
        return np.asarray(self.sheet_quad, dtype=np.float64).reshape(4, 2)

    @property
    def sheet_aspect(self) -> float:
        """Соотношение сторон листа (A3 ~ 1.414); для quad-проверки."""
        return quad_fallback.quad_aspect(self.sheet_corners)


def zones_from_layout(layout: Mapping[str, Any]) -> Tuple[Dict[str, np.ndarray], Dict[str, str]]:
    """Достаёт полигоны узлов из layout.json (формат ar_board)."""
    zones: Dict[str, np.ndarray] = {}
    labels: Dict[str, str] = {}
    for index, comp in enumerate(layout.get("components", [])):
        raw = comp.get("polygon_px") or []
        if len(raw) < 3:
            continue
        name = str(comp.get("component_id") or f"zone_{index}")
        zones[name] = np.asarray(raw, dtype=np.float64).reshape(-1, 2)
        labels[name] = str(comp.get("label", name))
    return zones, labels


def load_image_unicode(path: str | Path) -> np.ndarray:
    """cv2.imread не понимает кириллицу в пути на Windows."""
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"не удалось прочитать изображение: {path}")
    return image


def save_image_unicode(path: str | Path, image: np.ndarray) -> None:
    """cv2.imwrite не понимает кириллицу в пути на Windows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix or ".png"
    ok, buffer = cv2.imencode(ext, image)
    if not ok:
        raise IOError(f"не удалось закодировать изображение: {path}")
    buffer.tofile(str(path))


# основной класс
class FeatureAligner:
    """Привязка зон к кадру по эталону без маркеров."""

    def __init__(self,
                 reference: Reference,
                 *,
                 min_inliers: int = DEFAULT_MIN_INLIERS,
                 min_inlier_ratio: float = DEFAULT_MIN_INLIER_RATIO,
                 hold_seconds: float = DEFAULT_HOLD_SEC,
                 alpha: float = DEFAULT_ALPHA,
                 ransac_px: float = DEFAULT_RANSAC_PX,
                 ratio: float = DEFAULT_RATIO,
                 min_ncc: float = DEFAULT_MIN_NCC,
                 min_quad_ncc: float = DEFAULT_MIN_QUAD_NCC,
                 quad_max_side: int = quad_fallback.DEFAULT_QUAD_MAX_SIDE,
                 min_frame_features: int = 30,
                 min_texture_std: float = 4.0,
                 min_inlier_spread: float = 0.01,
                 methods: Sequence[str] = METHODS,
                 verify_photometric: bool = True,
                 parallel: bool = True,
                 akaze_frame_scale: float = 0.75,
                 compute_missing: bool = True) -> None:
        self.reference = reference
        self.min_inliers = int(min_inliers)
        self.min_inlier_ratio = float(min_inlier_ratio)
        self.hold_seconds = float(hold_seconds)
        self.ransac_px = float(ransac_px)
        self.ratio = float(ratio)
        self.min_ncc = float(min_ncc)
        self.min_quad_ncc = float(min_quad_ncc)
        self.quad_max_side = int(max(160, quad_max_side))
        self.min_frame_features = int(min_frame_features)
        self.min_texture_std = float(min_texture_std)
        self.min_inlier_spread = float(min_inlier_spread)
        self.verify_photometric = bool(verify_photometric)
        self.parallel = bool(parallel)
        self.akaze_frame_scale = float(np.clip(akaze_frame_scale, 0.4, 1.0))
        self.methods = tuple(m for m in methods if m in METHODS)

        self.smoother = Smoother(alpha=alpha)
        self._ref_raw_gray = _to_gray(reference.image)
        self._ref_gray = _clahe(self._ref_raw_gray)
        self._last_valid: Optional[AlignmentResult] = None
        self._last_valid_ts: Optional[float] = None
        self.last_result: Optional[AlignmentResult] = None

        if compute_missing:
            for method in self.methods:
                if method not in reference.features:
                    self.reference.features[method] = self._compute_features(method)

    # -- создание ---------------------------------------------------------- #
    @classmethod
    def from_files(cls, image_path: str | Path, layout_path: str | Path,
                   *, name: Optional[str] = None,
                   max_ref_width: int = DEFAULT_MAX_REF_WIDTH,
                   **kwargs: Any) -> "FeatureAligner":
        """Собирает эталон из изображения и layout.json (полигоны в px)."""
        layout_path = Path(layout_path)
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        zones, labels = zones_from_layout(layout)
        if not zones:
            raise ValueError(f"в {layout_path} нет components[].polygon_px")
        return cls.from_image(image_path, zones,
                              name=name or layout_path.parent.name,
                              board_size=cls._layout_board_size(layout),
                              labels=labels,
                              max_ref_width=max_ref_width, **kwargs)

    @classmethod
    def from_layout(cls, layout_path: str | Path, image_path: Optional[str | Path] = None,
                    *, name: Optional[str] = None, **kwargs: Any) -> "FeatureAligner":
        """layout.json + board.image (путь относительно layout) -> эталон."""
        layout_path = Path(layout_path)
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        if image_path is None:
            image_name = (layout.get("board") or {}).get("image")
            if not image_name:
                raise ValueError("в layout нет board.image, укажите image_path")
            image_path = layout_path.parent / image_name
        return cls.from_files(image_path, layout_path, name=name, **kwargs)

    @classmethod
    def from_image(cls, image_bgr: str | Path | np.ndarray,
                   zones: Mapping[str, Sequence[Sequence[float]]],
                   *, name: str = "reference",
                   board_size: Optional[Tuple[float, float]] = None,
                   labels: Optional[Mapping[str, str]] = None,
                   max_ref_width: int = DEFAULT_MAX_REF_WIDTH,
                   sheet_rect: Optional[Sequence[float]] = None,
                   **kwargs: Any) -> "FeatureAligner":
        """Собирает эталон из произвольного изображения и полигонов.

        `zones` заданы в пикселях ОРИГИНАЛЬНОГО изображения. Для привязки
        картинка уменьшается до `max_ref_width`, а гомография потом
        масштабируется обратно, поэтому зоны остаются в исходных координатах.

        `sheet_rect` = (x0, y0, x1, y1) — где в этих же координатах лежит
        физический печатный лист. Нужен для quad-fallback, когда эталон —
        обрезок листа: тогда лист выходит за границы картинки.
        """
        image = load_image_unicode(image_bgr) if isinstance(image_bgr, (str, Path)) \
            else as_bgr(image_bgr)
        if image is None:
            raise ValueError("пустое эталонное изображение")
        height, width = image.shape[:2]
        if board_size is None:
            board_size = (float(width), float(height))
        ref_scale = 1.0
        if width > max_ref_width:
            ref_scale = float(max_ref_width) / float(width)
            size = (int(max_ref_width), int(round(height * ref_scale)))
            image_small = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        else:
            image_small = image
        zone_arrays = {str(k): np.asarray(v, dtype=np.float64).reshape(-1, 2)
                       for k, v in zones.items()}
        reference = Reference(name=str(name), image=image_small, ref_scale=ref_scale,
                              zones=zone_arrays,
                              board_size=(float(board_size[0]), float(board_size[1])),
                              labels=dict(labels or {}),
                              sheet_quad=sheet_quad_from_rect(
                                  (float(board_size[0]), float(board_size[1])),
                                  sheet_rect))
        return cls(reference, **kwargs)

    @staticmethod
    def _layout_board_size(layout: Mapping[str, Any]) -> Optional[Tuple[float, float]]:
        board = layout.get("board") or {}
        if "width_px" in board and "height_px" in board:
            return float(board["width_px"]), float(board["height_px"])
        return None

    # -- признаки ---------------------------------------------------------- #
    def _compute_features(self, method: str) -> Tuple[np.ndarray, np.ndarray]:
        detector = _detector(method)
        keypoints, descriptors = detector.detectAndCompute(self._ref_gray, None)
        if descriptors is None or len(keypoints) == 0:
            return np.zeros((0, 7), np.float32), np.zeros((0, 32), np.uint8)
        return _kp_array(keypoints), np.asarray(descriptors)

    # -- сохранение / загрузка --------------------------------------------- #
    def save(self, path: str | Path) -> Path:
        """Сохраняет эталон + дескрипторы в .npz (без pickle)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, np.ndarray] = {
            "format": np.array("visual_align/1"),
            "name": np.array(self.reference.name),
            "ref_scale": np.array(self.reference.ref_scale, dtype=np.float64),
            "board_size": np.asarray(self.reference.board_size, dtype=np.float64),
            "sheet_quad": np.asarray(self.reference.sheet_corners, dtype=np.float64),
            "image": self.reference.image,
            "zone_names": np.array(list(self.reference.zones.keys())),
            "label_names": np.array(list(self.reference.labels.keys())),
            "label_values": np.array([self.reference.labels[k]
                                      for k in self.reference.labels]),
        }
        for zone_name, poly in self.reference.zones.items():
            payload[f"zone__{zone_name}"] = np.asarray(poly, dtype=np.float64)
        for method, (keypoints, descriptors) in self.reference.features.items():
            payload[f"kp__{method}"] = np.asarray(keypoints, dtype=np.float32)
            payload[f"desc__{method}"] = np.asarray(descriptors)
        np.savez_compressed(path, **payload)
        return path

    @classmethod
    def load(cls, path: str | Path, *, sheet_rect: Optional[Sequence[float]] = None,
             **kwargs: Any) -> "FeatureAligner":
        """Загружает эталон; дескрипторы берутся из файла, не пересчитываются.

        `sheet_rect` переопределяет геометрию листа из кэша: старые `.npz`
        (до quad-fallback) её не содержат, а для эталона-обрезка она нужна.
        """
        path = Path(path)
        with np.load(path, allow_pickle=False) as data:
            image = np.array(data["image"])
            ref_scale = float(data["ref_scale"])
            board_size = tuple(float(v) for v in np.asarray(data["board_size"]).ravel())
            name = str(data["name"])
            stored_sheet = (np.array(data["sheet_quad"], dtype=np.float64)
                            if "sheet_quad" in data.files else None)
            zones = {str(key): np.array(data[f"zone__{key}"], dtype=np.float64)
                     for key in data["zone_names"].tolist()}
            labels = {str(k): str(v) for k, v in
                      zip(data["label_names"].tolist(), data["label_values"].tolist())}
            features: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
            for key in data.files:
                if key.startswith("kp__"):
                    method = key[4:]
                    features[method] = (np.array(data[key], dtype=np.float32),
                                        np.array(data[f"desc__{method}"]))
        if sheet_rect is not None or stored_sheet is None:
            sheet_quad = sheet_quad_from_rect(board_size, sheet_rect)
        else:
            sheet_quad = np.asarray(stored_sheet, dtype=np.float64).reshape(4, 2)
        reference = Reference(name=name, image=image, ref_scale=ref_scale,
                              zones=zones, board_size=board_size, labels=labels,
                              features=features, sheet_quad=sheet_quad)
        return cls(reference, compute_missing=True, **kwargs)

    # -- основной API ------------------------------------------------------ #
    def reset(self) -> None:
        """Сбрасывает hold_last_pose и сглаживание (новый ролик)."""
        self._last_valid = None
        self._last_valid_ts = None
        self.last_result = None
        self.smoother.reset()

    def register(self, frame_bgr: Optional[np.ndarray],
                 timestamp: Optional[float] = None) -> AlignmentResult:
        """Оценивает гомографию эталон -> кадр. Никогда не бросает на плохом кадре."""
        now = time.monotonic() if timestamp is None else float(timestamp)
        started = time.perf_counter()
        frame = as_bgr(frame_bgr)
        if frame is None:
            return self._reject(now, "bad_frame", started)

        height, width = frame.shape[:2]
        if min(height, width) < 64:
            return self._reject(now, "frame_too_small", started,
                                frame_size=(width, height))

        raw_gray = _to_gray(frame)
        # Честный отказ на однородной поверхности: текстуры нет — привязки нет.
        if float(raw_gray.std()) < self.min_texture_std:
            return self._reject(now, "low_texture", started, frame_size=(width, height))
        gray = _clahe(raw_gray)
        frame_size = (width, height)

        candidates: List[AlignmentResult] = []
        timings: Dict[str, float] = {}
        if len(self.methods) > 1 and self.parallel:
            with ThreadPoolExecutor(max_workers=len(self.methods)) as pool:
                futures = {pool.submit(self._register_with, method, gray, frame_size): method
                           for method in self.methods}
                for future in as_completed(futures):
                    method = futures[future]
                    try:
                        candidate, elapsed = future.result()
                    except Exception:  # noqa: BLE001 - один детектор не должен ломать кадр
                        candidate, elapsed = None, 0.0
                    timings[method] = round(elapsed, 2)
                    if candidate is not None:
                        candidates.append(candidate)
        else:
            for method in self.methods:
                candidate, elapsed = self._register_with(method, gray, frame_size)
                timings[method] = round(elapsed, 2)
                if candidate is not None:
                    candidates.append(candidate)

        timings["total"] = round((time.perf_counter() - started) * 1000.0, 2)
        if not candidates:
            return self._reject(now, "no_homography", started, timings=timings,
                                frame_size=frame_size)

        candidates.sort(key=lambda r: (r.inliers, r.inlier_ratio, -r.reproj_px, r.method),
                        reverse=True)
        inverse_scale = np.linalg.inv(self.reference.scale_matrix)
        for best in candidates:
            if self.verify_photometric:
                ncc = self._photometric_ncc(best.homography @ inverse_scale,
                                            raw_gray, frame_size)
                best.ncc = ncc
                if ncc < self.min_ncc:
                    continue
            best.timings_ms = dict(timings)
            best.timings_ms["total"] = round((time.perf_counter() - started) * 1000.0, 2)
            self.last_result = best
            self._last_valid = best
            self._last_valid_ts = now
            return best

        return self._reject(now, "low_ncc", started, timings=timings,
                            frame_size=frame_size)

    def register_by_quad(self, frame_bgr: Optional[np.ndarray],
                         timestamp: Optional[float] = None) -> AlignmentResult:
        """Fallback-привязка по контуру печатного листа (метод ``quad``).

        Работает, когда планшет маленький и признаков мало: находит светлый
        четырёхугольник листа и строит гомографию по его углам. Поза не
        удерживается (`hold_last_pose` остаётся за `register`): вызывающий
        сам решает, можно ли использовать этот результат.
        """
        now = time.monotonic() if timestamp is None else float(timestamp)
        started = time.perf_counter()
        frame = as_bgr(frame_bgr)
        if frame is None:
            return self._quad_reject("bad_frame", started)
        height, width = frame.shape[:2]
        frame_size = (width, height)
        if min(height, width) < 64:
            return self._quad_reject("frame_too_small", started, frame_size)

        sheet = self.reference.sheet_corners
        detection = quad_fallback.detect_sheet_quad(
            frame,
            expected_aspect=self.reference.sheet_aspect,
            max_side=self.quad_max_side,
        )
        if detection is None:
            return self._quad_reject("quad_no_sheet", started, frame_size)

        # Если лист повёрнут на 90° (портрет вместо альбома), длинная сторона
        # эталона должна лечь на длинную сторону найденного квада.
        target = detection.quad
        ref_wide = (np.linalg.norm(sheet[1] - sheet[0])
                    >= np.linalg.norm(sheet[3] - sheet[0]))
        det_wide = (np.linalg.norm(target[1] - target[0])
                    >= np.linalg.norm(target[3] - target[0]))
        if ref_wide != det_wide:
            target = np.roll(target, -1, axis=0)
        try:
            homography = cv2.getPerspectiveTransform(
                sheet.astype(np.float32), target.astype(np.float32)
            ).astype(np.float64)
        except cv2.error:
            return self._quad_reject("quad_degenerate", started, frame_size)
        if abs(homography[2, 2]) > 1e-12:
            homography = homography / homography[2, 2]
        if not np.all(np.isfinite(homography)):
            return self._quad_reject("quad_nan", started, frame_size)
        ok, reason = self._quad_sanity(homography, detection.quad, frame_size)
        if not ok:
            return self._quad_reject(f"quad_{reason}", started, frame_size)

        ncc = 0.0
        if self.verify_photometric:
            value = self._quad_ncc(homography, _to_gray(frame))
            if value is None:
                return self._quad_reject("quad_ncc_unavailable", started, frame_size)
            ncc = value
            if ncc < self.min_quad_ncc:
                return self._quad_reject("quad_low_ncc", started, frame_size)

        confidence = float(np.clip(
            detection.confidence * (0.65 + 0.35 * max(0.0, ncc)), 0.0, 1.0))
        elapsed = round((time.perf_counter() - started) * 1000.0, 2)
        result = AlignmentResult(
            homography=homography,
            inliers=0,
            inlier_ratio=0.0,
            method="quad",
            confidence=confidence,
            stale=False,
            age_sec=0.0,
            matches=0,
            reproj_px=0.0,
            ncc=float(ncc),
            frame_size=frame_size,
            reason="",
            timings_ms={"quad": elapsed, "total": elapsed},
        )
        self.last_result = result
        return result

    # -- внутренности ------------------------------------------------------ #
    def _register_with(self, method: str, gray: np.ndarray,
                       frame_size: Tuple[int, int]
                       ) -> Tuple[Optional[AlignmentResult], float]:
        stage = time.perf_counter()
        result = self._register_one(method, gray, frame_size)
        return result, (time.perf_counter() - stage) * 1000.0

    def _register_one(self, method: str, gray: np.ndarray,
                      frame_size: Tuple[int, int]) -> Optional[AlignmentResult]:
        features = self.reference.features.get(method)
        if features is None or len(features[0]) == 0 or len(features[1]) == 0:
            return None
        ref_kps, ref_desc = features
        # AKAZE дороже ORB: детектируем на слегка уменьшенном кадре и возвращаем
        # координаты в исходный масштаб. Для гомографии это доли пикселя.
        detect_scale = self.akaze_frame_scale if method == "akaze" else 1.0
        detect_gray = gray
        if detect_scale < 1.0:
            detect_gray = cv2.resize(gray, None, fx=detect_scale, fy=detect_scale,
                                     interpolation=cv2.INTER_AREA)
        try:
            keypoints, descriptors = _detector(method).detectAndCompute(detect_gray, None)
        except cv2.error:
            return None
        if descriptors is None or len(keypoints) < self.min_frame_features:
            return None

        matcher, max_distance = _matcher(method)
        try:
            knn = matcher.knnMatch(np.asarray(descriptors), ref_desc, k=2)
        except cv2.error:
            return None

        ref_points = np.asarray(ref_kps[:, :2], dtype=np.float64)
        frame_points_all = np.asarray([kp.pt for kp in keypoints], dtype=np.float64)
        if detect_scale != 1.0:
            frame_points_all = frame_points_all / detect_scale
        src: List[np.ndarray] = []
        dst: List[np.ndarray] = []
        for pair in knn:
            if len(pair) < 2:
                continue
            first, second = pair[0], pair[1]
            if second.distance <= 1e-9:
                continue
            if first.distance >= self.ratio * second.distance:
                continue
            if max_distance is not None and first.distance > max_distance:
                continue
            src.append(ref_points[first.trainIdx])
            dst.append(frame_points_all[first.queryIdx])
        matches = len(src)
        if matches < max(8, self.min_inliers):
            return None

        src_array = np.asarray(src, dtype=np.float64)
        dst_array = np.asarray(dst, dtype=np.float64)
        try:
            homography, mask = cv2.findHomography(src_array, dst_array,
                                                  cv2.RANSAC, self.ransac_px,
                                                  maxIters=3000, confidence=0.995)
        except cv2.error:
            return None
        if homography is None or mask is None:
            return None
        if abs(homography[2, 2]) > 1e-12:
            homography = homography / homography[2, 2]
        if not np.all(np.isfinite(homography)):
            return None

        inlier_mask = mask.ravel().astype(bool)
        if int(inlier_mask.sum()) < 4:
            return None
        # Уточняем по инлайерам и пересчитываем их уже относительно финальной H.
        try:
            refined, _ = cv2.findHomography(src_array[inlier_mask], dst_array[inlier_mask], 0)
        except cv2.error:
            refined = None
        if refined is not None and abs(refined[2, 2]) > 1e-12:
            refined = refined / refined[2, 2]
            if np.all(np.isfinite(refined)):
                homography = refined

        projected = project_points(homography, src_array)
        errors = np.linalg.norm(projected - dst_array, axis=1)
        inlier_mask = errors <= self.ransac_px
        inliers = int(inlier_mask.sum())
        if inliers < self.min_inliers:
            return None
        inlier_ratio = float(inliers) / float(matches)
        if inlier_ratio < self.min_inlier_ratio:
            return None
        reproj = float(np.median(errors[inlier_mask])) if inliers else float("inf")

        # Гомография в координатах ОРИГИНАЛА эталона (а не уменьшенной копии).
        homography_full = homography @ self.reference.scale_matrix

        ok, reason = self._sanity(homography_full, frame_size)
        if not ok:
            return None
        ok, reason = self._spread(dst_array[inlier_mask], frame_size)
        if not ok:
            return None

        ncc = 0.0
        confidence = confidence_from(inliers, inlier_ratio, reproj)
        return AlignmentResult(
            homography=homography_full.astype(np.float64),
            inliers=inliers,
            inlier_ratio=inlier_ratio,
            method=method,
            confidence=confidence,
            stale=False,
            age_sec=0.0,
            matches=matches,
            reproj_px=reproj,
            ncc=ncc,
            frame_size=frame_size,
            reason="",
        )

    def _sanity(self, homography: np.ndarray,
                frame_size: Tuple[int, int]) -> Tuple[bool, str]:
        """Правдоподобие гомографии: без зеркал, вырождений и абсурдных площадей."""
        if not np.all(np.isfinite(homography)):
            return False, "nan"
        if np.linalg.det(homography[:2, :2]) <= 0.0:
            return False, "mirror"
        width, height = frame_size
        board_w, board_h = self.reference.board_size
        corners = np.array([[0.0, 0.0], [board_w, 0.0],
                            [board_w, board_h], [0.0, board_h]], dtype=np.float64)
        quad = project_points(homography, corners)
        if not np.all(np.isfinite(quad)):
            return False, "quad_nan"
        if not cv2.isContourConvex(quad.astype(np.float32)):
            return False, "not_convex"
        area = _poly_area(quad)
        frame_area = float(width * height)
        if area < 0.05 * frame_area or area > 6.0 * frame_area:
            return False, "area"
        sides = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        if float(sides.min()) < 8.0:
            return False, "degenerate_side"
        # Соотношение сторон проекции не должно быть абсурдным (планшет ~1.41).
        aspect = float(sides.max() / max(1e-6, sides.min()))
        if aspect > 6.0:
            return False, "aspect"
        # Центр планшета обязан попадать внутрь собственной проекции.
        center = project_points(homography, np.array([[board_w / 2.0, board_h / 2.0]]))[0]
        if cv2.pointPolygonTest(quad.astype(np.float32).reshape(-1, 1, 2),
                                (float(center[0]), float(center[1])), False) < 0:
            return False, "center_outside"
        return True, ""

    def _spread(self, inlier_points: np.ndarray,
                frame_size: Tuple[int, int]) -> Tuple[bool, str]:
        """Инлайеры должны покрывать заметную часть кадра, а не одну точку."""
        if len(inlier_points) < 4:
            return False, "few_inliers"
        width, height = frame_size
        hull = cv2.convexHull(inlier_points.astype(np.float32))
        area_ratio = _poly_area(hull) / float(width * height)
        if area_ratio < self.min_inlier_spread:
            return False, "clustered"
        # Порог 10%: на маленьком планшете (18–50% кадра) текстура часто есть
        # только в области фото узла, и корректная поза опирается на компактный
        # кластер. От совсем мелких кластеров по-прежнему защищает NCC.
        span = inlier_points.max(axis=0) - inlier_points.min(axis=0)
        if span[0] < 0.10 * width or span[1] < 0.10 * height:
            return False, "clustered"
        return True, ""

    def _quad_sanity(self, homography: np.ndarray, quad: np.ndarray,
                     frame_size: Tuple[int, int]) -> Tuple[bool, str]:
        """Проверка гомографии quad: без зеркал, вырождений и листа-гиганта."""
        if not np.all(np.isfinite(homography)):
            return False, "nan"
        if np.linalg.det(homography[:2, :2]) <= 0.0:
            return False, "mirror"
        width, height = frame_size
        if not cv2.isContourConvex(np.asarray(quad, np.float32).reshape(-1, 1, 2)):
            return False, "not_convex"
        area = _poly_area(quad)
        frame_area = float(width * height)
        if area < 0.002 * frame_area or area > 0.99 * frame_area:
            return False, "area"
        sides = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        if float(sides.min()) < 8.0:
            return False, "degenerate_side"
        # Лист должен хотя бы частично быть в кадре.
        center = np.asarray(quad, np.float64).mean(axis=0)
        if not (0.0 <= center[0] <= width and 0.0 <= center[1] <= height):
            return False, "outside"
        return True, ""

    def _quad_ncc(self, homography_full: np.ndarray,
                  frame_gray: np.ndarray) -> Optional[float]:
        """NCC эталона и кадра для quad-позы (в координатах исходного кадра).

        В отличие от `_photometric_ncc` не требует, чтобы эталон занимал 5%
        кадра: на 18% планшета обрезок эталона — это ~55x55 px, и это норма.
        """
        try:
            matrix = homography_full @ np.linalg.inv(self.reference.scale_matrix)
            height, width = frame_gray.shape[:2]
            # Считаем на уменьшенной копии кадра (<= 640 px): проверка грубая,
            # а полный кадр 1280x720 стоит в 4 раза дороже.
            scale = min(1.0, 640.0 / float(max(width, height)))
            if scale < 1.0:
                out_w = max(16, int(round(width * scale)))
                out_h = max(16, int(round(height * scale)))
                frame_small = cv2.resize(frame_gray, (out_w, out_h),
                                         interpolation=cv2.INTER_AREA)
                shrink = np.array([[scale, 0.0, 0.0],
                                   [0.0, scale, 0.0],
                                   [0.0, 0.0, 1.0]], dtype=np.float64)
                matrix = shrink @ matrix
            else:
                out_w, out_h = width, height
                frame_small = frame_gray
            warped = cv2.warpPerspective(self._ref_raw_gray, matrix, (out_w, out_h),
                                         flags=cv2.INTER_LINEAR)
            mask = cv2.warpPerspective(
                np.ones(self._ref_raw_gray.shape[:2], np.uint8), matrix,
                (out_w, out_h), flags=cv2.INTER_NEAREST)
        except (cv2.error, np.linalg.LinAlgError):
            return None
        valid = mask > 0
        count = int(valid.sum())
        if count < 120:
            return None
        frame_gray = frame_small
        a = warped[valid].astype(np.float32)
        b = frame_gray[valid].astype(np.float32)
        a -= a.mean()
        b -= b.mean()
        denom = float(np.sqrt(float((a * a).sum()) * float((b * b).sum())))
        if denom < 1e-6:
            return None
        return float(np.clip(float((a * b).sum()) / denom, -1.0, 1.0))

    def _quad_reject(self, reason: str, started: float,
                     frame_size: Optional[Tuple[int, int]] = None) -> AlignmentResult:
        """Отказ quad без hold_last_pose (решение об удержании — за вызывающим)."""
        elapsed = round((time.perf_counter() - started) * 1000.0, 2)
        return AlignmentResult(
            homography=None,
            method="none",
            frame_size=tuple(frame_size or (0, 0)),
            reason=reason,
            timings_ms={"quad": elapsed, "total": elapsed},
        )

    def _photometric_ncc(self, homography_small: np.ndarray, frame_gray: np.ndarray,
                         frame_size: Tuple[int, int]) -> float:
        """NCC эталона и кадра в области перекрытия (защита от ложных гомографий).

        Считается на уменьшенной копии кадра (<= 640 px по длинной стороне):
        это в ~4 раза дешевле и на решение не влияет — проверка грубая.
        """
        width, height = frame_size
        scale = min(1.0, 640.0 / float(max(width, height)))
        out_w, out_h = max(16, int(round(width * scale))), max(16, int(round(height * scale)))
        downscale = np.array([[scale, 0.0, 0.0],
                              [0.0, scale, 0.0],
                              [0.0, 0.0, 1.0]], dtype=np.float64)
        matrix = downscale @ homography_small
        try:
            warped = cv2.warpPerspective(self._ref_gray, matrix, (out_w, out_h),
                                         flags=cv2.INTER_LINEAR)
            mask = cv2.warpPerspective(np.ones(self._ref_gray.shape[:2], np.float32),
                                       matrix, (out_w, out_h),
                                       flags=cv2.INTER_NEAREST)
        except cv2.error:
            return 0.0
        if scale < 1.0:
            frame_small = cv2.resize(frame_gray, (out_w, out_h),
                                     interpolation=cv2.INTER_AREA)
        else:
            frame_small = frame_gray
        mask_bool = mask > 0.5
        if int(mask_bool.sum()) < 0.05 * out_w * out_h:
            return 0.0
        a = warped[mask_bool].astype(np.float32)
        b = frame_small[mask_bool].astype(np.float32)
        a -= a.mean()
        b -= b.mean()
        denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
        if denom < 1e-6:
            return 0.0
        return float(np.clip((a * b).sum() / denom, -1.0, 1.0))

    def _reject(self, now: float, reason: str, started: float,
                timings: Optional[Dict[str, float]] = None,
                frame_size: Optional[Tuple[int, int]] = None) -> AlignmentResult:
        """Отказ текущего кадра с попыткой удержать последнюю позу."""
        size_matches = (frame_size is None
                        or (self._last_valid is not None
                            and tuple(self._last_valid.frame_size) == tuple(frame_size)))
        if (size_matches and self._last_valid is not None
                and self._last_valid_ts is not None):
            age = now - self._last_valid_ts
            if 0.0 <= age <= self.hold_seconds:
                held = AlignmentResult(**{**self._last_valid.__dict__})
                held.homography = self._last_valid.homography.copy()
                held.stale = True
                held.age_sec = float(age)
                held.reason = reason
                held.timings_ms = dict(timings or {})
                held.timings_ms.setdefault("total",
                                           round((time.perf_counter() - started) * 1000.0, 2))
                # Уверенность удержанной позы линейно падает к концу hold-окна.
                held.confidence = float(self._last_valid.confidence
                                        * max(0.0, 1.0 - age / max(1e-6, self.hold_seconds)))
                self.last_result = held
                return held
        if self._last_valid is not None and self._last_valid_ts is not None:
            age = now - self._last_valid_ts
            if age > self.hold_seconds:
                self._last_valid = None
                self._last_valid_ts = None
        result = AlignmentResult(homography=None, method="none",
                                 frame_size=tuple(frame_size or (0, 0)),
                                 reason=reason,
                                 timings_ms=dict(timings or {}))
        result.timings_ms.setdefault("total",
                                     round((time.perf_counter() - started) * 1000.0, 2))
        self.last_result = result
        return result

    # -- проекция ---------------------------------------------------------- #
    def project(self, zones: Optional[Mapping[str, Sequence[Sequence[float]]]] = None,
                *, smooth: bool = True,
                homography: Optional[np.ndarray] = None,
                timestamp: Optional[float] = None) -> Dict[str, List[List[float]]]:
        """Полигоны зон в координатах кадра.

        По умолчанию берёт последнюю валидную (в т.ч. удержанную) гомографию.
        `zones` — в пикселях эталона; если не переданы, используются зоны эталона.
        """
        matrix = homography if homography is not None else (
            self.last_result.homography if self.last_result is not None else None)
        if matrix is None:
            return {}
        source = zones if zones is not None else self.reference.zones
        projected: Dict[str, List[List[float]]] = {}
        for name, poly in source.items():
            points = np.asarray(poly, dtype=np.float64).reshape(-1, 2)
            if len(points) < 3:
                continue
            transformed = project_points(matrix, points)
            if not np.all(np.isfinite(transformed)):
                continue
            projected[str(name)] = [[round(float(x), 3), round(float(y), 3)]
                                    for x, y in transformed]
        if smooth and projected:
            return self.smoother.update(projected, timestamp=timestamp)
        return projected

    def project_result(self, result: AlignmentResult,
                       zones: Optional[Mapping[str, Sequence[Sequence[float]]]] = None
                       ) -> Dict[str, List[List[float]]]:
        """Проекция по конкретному результату (без сглаживания)."""
        if result.homography is None:
            return {}
        return self.project(zones, smooth=False, homography=result.homography)

    # -- отладка / визуализация -------------------------------------------- #
    def debug_matches(self, frame_bgr: np.ndarray, method: str = "orb"
                      ) -> Optional[Tuple[List[cv2.KeyPoint], List[cv2.KeyPoint],
                                          List[cv2.DMatch], np.ndarray]]:
        """Признаки и матчи для картинки: (ref_kps, frame_kps, matches, inlier_mask).

        Считает заново и нужен только для офлайн-визуализаций, не для продакшена.
        """
        frame = as_bgr(frame_bgr)
        if frame is None or method not in self.reference.features:
            return None
        gray = _clahe(_to_gray(frame))
        ref_kps_array, ref_desc = self.reference.features[method]
        ref_kps = _kp_from_array(ref_kps_array)
        try:
            frame_kps, frame_desc = _detector(method).detectAndCompute(gray, None)
        except cv2.error:
            return None
        if frame_desc is None:
            return None
        matcher, max_distance = _matcher(method)
        knn = matcher.knnMatch(np.asarray(frame_desc), ref_desc, k=2)
        matches: List[cv2.DMatch] = []
        for pair in knn:
            if len(pair) < 2:
                continue
            first, second = pair
            if second.distance <= 1e-9:
                continue
            if first.distance >= self.ratio * second.distance:
                continue
            if max_distance is not None and first.distance > max_distance:
                continue
            matches.append(first)
        if len(matches) < 4:
            return None
        src = np.asarray([ref_kps[m.trainIdx].pt for m in matches], dtype=np.float64)
        dst = np.asarray([frame_kps[m.queryIdx].pt for m in matches], dtype=np.float64)
        homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, self.ransac_px)
        if homography is None or mask is None:
            return None
        inliers = mask.ravel().astype(bool)
        order = np.argsort([m.distance for m in matches])
        selected = [matches[i] for i in order[:120]]
        selected_mask = inliers[order[:120]]
        return ref_kps, frame_kps, selected, selected_mask
