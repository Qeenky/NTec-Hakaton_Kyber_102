"""Единый CV-пайплайн кадра: AR-планшет → зоны → OCR манометра → детекции.

Поток одного кадра:

    PIL.Image
        └─ ar_board.detect_board (ArUco, гомография + PnP)
             ├─ нет позы ──► board.detected=false + reason
             └─ ar_board.project_components (5 полигонов узлов, visible)
                  ├─ Smoother (EMA по углам, чтобы рамки не дрожали)
                  ├─ Detection(kind="component", source="board")
                  ├─ OCR манометра раз в N кадров (cv_classic.read_gauge)
                  │    └─ cv_state.publish_ocr → правило gauge_drift
                  └─ эвристики дефектов (cv_classic.defects, если появится)

Confidence узла (документируется в README, раздел «Сквозной пайплайн»):

    q_reproj   = clamp(1 - reprojection_error / 6.0)     # 6 px — порог detect_board
    q_coverage = clamp(coverage / 0.30)                  # 30 % кадра — «уверенный» кадр
    confidence = clamp(0.90 + 0.05*q_reproj + 0.03*q_coverage, 0.0, 0.98)

То есть уверенность «планшет найден» всегда 0.90..0.98 и растёт с точностью
репоецирования и площадью маркеров в кадре.

Углы поворота планшета и «дрожание» камеры гасятся `Smoother` (EMA, alpha=0.35):
полигоны в ответе сглажены, а решение о видимости принимается по сырым данным.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
from PIL import Image

from .. import cv_state
from ..config import get_settings
from ..paths import ensure_sys_path

ensure_sys_path()  # ar_board / cv_classic лежат в корне проекта

import ar_board  # noqa: E402  (после ensure_sys_path)
from cv_classic import GaugeConfig, read_gauge  # noqa: E402

try:  # маркерная проверка есть не во всех версиях cv_classic
    from cv_classic import check_marker_in_roi as _check_marker_in_roi  # noqa: E402
except (ImportError, AttributeError):  # pragma: no cover - защита от старой версии
    _check_marker_in_roi = None

from .detector import CLASS_IDS, Detection  # noqa: E402
from .rules import build_components_status  # noqa: E402
from .anomaly_channel import AnomalyChannel  # noqa: E402
from .board_registry import (  # noqa: E402
    DEFAULT_STABLE_FRAMES,
    VisualBoardRegistry,
    detect_identity_markers,
    layout_board_info,
    load_board_registry,
)
from .corrosion_channel import CorrosionChannel  # noqa: E402

_logger = logging.getLogger(__name__)

COMPONENT_LABELS: dict[str, str] = dict(ar_board.COMPONENT_ORDER)

# Статус узла -> цвет оверлея для мобильного клиента.
STATUS_COLORS: dict[str, str] = {
    "ok": "green",
    "check_required": "yellow",
    "possible_defect": "red",
    "insufficient_data": "grey",
}

# Единицы OCR -> бар (в телеметрии давление в барах).
_UNIT_TO_BAR: dict[str, float] = {
    "бар": 1.0,
    "bar": 1.0,
    "мпа": 10.0,
    "mpa": 10.0,
    "кпа": 0.01,
    "kpa": 0.01,
    "psi": 0.0689476,
}

_REASON_TEXT: dict[str, str] = {
    "no_markers": "маркеры ArUco не найдены в кадре",
    "partial": "видно меньше 3 маркеров из 4",
    "low_quality": "маркеры найдены, но точность/покрытие ниже порога",
    "no_layout": "layout.json не найден — пайплайн планшета недоступен",
    "no_match": "планшет не опознан по сцене (feature-привязка)",
    "identity_missing": "маркер идентификации планшета не найден — layout не меняю",
    "error": "ошибка обработки кадра",
}


@dataclass
class PipelineResult:
    """Всё, что нужно отдать мобильному клиенту по одному кадру."""

    board: dict[str, Any]
    overlays: list[dict[str, Any]] = field(default_factory=list)
    detections: list[Detection] = field(default_factory=list)
    defects: list[Detection] = field(default_factory=list)
    signs: list[dict[str, Any]] = field(default_factory=list)
    components_status: list[dict[str, Any]] = field(default_factory=list)
    ocr: dict[str, Any] | None = None
    timings: dict[str, float] = field(default_factory=dict)
    image_size: tuple[int, int] = (0, 0)

    @property
    def detected(self) -> bool:
        return bool(self.board.get("detected"))


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def board_confidence(reprojection_error: float, coverage: float) -> float:
    """Confidence «планшет найден»: 0.90..0.98 (формула — в docstring модуля)."""
    q_reproj = _clamp(1.0 - float(reprojection_error) / 6.0)
    q_coverage = _clamp(float(coverage) / 0.30)
    return round(_clamp(0.90 + 0.05 * q_reproj + 0.03 * q_coverage, 0.0, 0.98), 3)


def _polygon_bbox(polygon: Sequence[Sequence[float]], width: int, height: int) -> tuple[float, float, float, float]:
    """Полигон в пикселях кадра -> нормализованный bbox 0..1 (для Detection)."""
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    x1 = _clamp(min(xs) / max(1, width))
    y1 = _clamp(min(ys) / max(1, height))
    x2 = _clamp(max(xs) / max(1, width))
    y2 = _clamp(max(ys) / max(1, height))
    if x2 - x1 < 0.005:
        x2 = min(1.0, x1 + 0.005)
    if y2 - y1 < 0.005:
        y2 = min(1.0, y1 + 0.005)
    return (round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4))


def _board_defects_flag(layout: Mapping[str, Any] | None) -> bool | None:
    """Флаг «на этом планшете есть дефекты» из `board.defects`.

    Чистый планшет (`defects: false`) не должен показывать коррозию и
    текстурные аномалии, даже если `CV_DEFECTS=true` в окружении. Флаг
    задаётся генератором планшета и переезжает вместе с layout при
    авто-переключении. `None` — планшет флаг не задаёт, действует настройка.
    """
    data: Mapping[str, Any] = layout or {}
    board = data.get("board") if isinstance(data.get("board"), Mapping) else {}
    for source in (board, data):
        value = source.get("defects") if isinstance(source, Mapping) else None
        if isinstance(value, bool):
            return value
    return None


def _dedupe_defects(detections: Sequence[Detection]) -> list[Detection]:
    """Оставляет по одному дефекту каждого класса на узел (самый уверенный).

    Дедупликация нужна, если детектор вернул один и тот же класс дважды;
    узел берётся из явной привязки (`component_id`).
    """
    best: dict[tuple[str, str], Detection] = {}
    for item in detections:
        key = (
            item.class_name,
            item.component_id or f"{item.bbox[0]:.3f}:{item.bbox[1]:.3f}",
        )
        current = best.get(key)
        if current is None or item.confidence > current.confidence:
            best[key] = item
    return list(best.values())


def _load_defects_module() -> Any | None:
    """Модуль `cv_classic.defects`, если он появился (иначе None).

    Контракт необязательный: пайплайн продолжает работать без эвристик.
    """
    try:
        from cv_classic import defects  # type: ignore[attr-defined]

        return defects
    except ImportError:
        return None
    except Exception:  # noqa: BLE001 - сломанный модуль не должен ронять сервер
        _logger.exception("cv_classic.defects не импортировался")
        return None


# Имя эвристики cv_classic.defects -> класс детекции backend.
_DEFECT_CLASS_MAP: dict[str, str] = {
    "rust": "corrosion",
    "oxidation": "oxidation",
    "oil": "oil_leak",
}


class BoardPipeline:
    """Сквозной проход кадра: планшет → зоны → OCR → детекции."""

    def __init__(
        self,
        layout_path: str | None = None,
        gauge_config_path: str | None = None,
        ocr_every: int | None = None,
        smooth_alpha: float = 0.35,
        roi_size: int = 512,
        roi_margin: float = 1.30,
        unit_scale: float | None = None,
        defects_enabled: bool | None = None,
        board_registry_path: str | None = None,
        identity_stable_frames: int | None = None,
        align_mode: str | None = None,
        align_every: int | None = None,
    ) -> None:
        settings = get_settings()
        self._lock = threading.RLock()
        self._layout_revision = 0
        self.layout_path = str(Path(layout_path or settings.layout_path).expanduser().resolve())
        self.layout: dict[str, Any] = ar_board.load_layout(self.layout_path)

        configured = gauge_config_path if gauge_config_path is not None else settings.gauge_config
        self.gauge_config_path: str | None = str(configured) if configured else None
        self.gauge_config = self._load_gauge_config(self.gauge_config_path)
        self.unit_scale = settings.gauge_unit_scale if unit_scale is None else unit_scale
        self.ocr_every = max(1, int(ocr_every if ocr_every is not None else settings.gauge_ocr_every))

        self.roi_size = int(roi_size)
        self.roi_size = self.roi_size + (self.roi_size % 2)
        self._roi_margin = float(roi_margin)
        self.roi_matrix: np.ndarray | None = self._build_roi_matrix(self.layout, self._roi_margin)

        self._smoothers: dict[str, ar_board.Smoother] = {}
        self._smooth_alpha = float(smooth_alpha)
        self._counters: dict[str, int] = {}
        self._last_ocr: dict[str, dict[str, Any]] = {}
        self._defects_module = _load_defects_module()
        self.defects_enabled = (
            settings.cv_defects if defects_enabled is None else bool(defects_enabled)
        )
        # Флаг планшета важнее настройки окружения: чистый планшет не должен
        # показывать дефекты из-за CV_DEFECTS=true. Явный аргумент сильнее.
        board_defects = _board_defects_flag(self.layout)
        if defects_enabled is None and board_defects is not None:
            self.defects_enabled = board_defects
        self._labels = {
            str(item.get("component_id")): str(item.get("label") or "")
            for item in self.layout.get("components", [])
        }
        # Реестр планшетов: identity-маркер -> layout. Нет файла — пустой реестр,
        # пайплайн работает как раньше (только ручное переключение).
        registry_raw = board_registry_path or str(settings.board_registry_path)
        self.board_registry_path = str(Path(registry_raw).expanduser().resolve())
        self.registry = load_board_registry(self.board_registry_path)
        self.identity_stable_frames = max(
            1,
            int(
                identity_stable_frames
                if identity_stable_frames is not None
                else DEFAULT_STABLE_FRAMES
            ),
        )
        self._identity_window = self.identity_stable_frames + 2
        self._identity_history: deque[int | None] = deque(maxlen=self._identity_window)
        self._identity_cooldown = 0
        # Ленивый канал аномалий: модель загрузится при первом кадре.
        self.anomaly = AnomalyChannel()
        self.corrosion = CorrosionChannel()
        # Привязка без маркеров: эталоны и ORB/AKAZE-дескрипторы.
        self.align_mode = (align_mode or settings.align_mode or "features").strip().lower()
        if self.align_mode not in {"features", "aruco", "auto"}:
            self.align_mode = "features"
        self.visual_registry = VisualBoardRegistry(
            settings.align_references,
            settings.align_cache_dir,
            mode=self.align_mode,
            every=max(1, int(align_every if align_every is not None else settings.align_every)),
            max_ref_width=settings.align_max_ref_width,
        )
        self.board_id = layout_board_info(self.layout)["board_id"]

    # Конфигурация

    @staticmethod
    def _load_gauge_config(path: str | None) -> GaugeConfig | None:
        """Конфиг калибровки манометра; нет файла — read_gauge без конфига."""
        if not path:
            return None
        try:
            return GaugeConfig.from_json(path)
        except (OSError, ValueError, KeyError) as exc:
            _logger.warning("GAUGE_CONFIG=%s не загружен (%s); читаю без калибровки", path, exc)
            return None

    def _build_roi_matrix(
        self,
        layout: dict[str, Any],
        margin: float,
    ) -> np.ndarray | None:
        """Матрица «пиксели планшета → квадратный ROI манометра».

        ROI строится в системе координат планшета, поэтому позиция прибора в
        кропе не зависит от ракурса камеры — отсюда фиксированные center/radius
        в конфиге калибровки и чтение за ~100 мс (без перебора HoughCircles).
        """
        component = next(
            (
                item
                for item in layout.get("components", [])
                if item.get("component_id") == "manometer"
            ),
            None,
        )
        if component is None:
            # На планшете может не быть манометра — это не ошибка конфигурации,
            # просто канал OCR недоступен. Правило gauge_drift честно уйдёт
            # в blocked_by=ocr_unavailable, остальной пайплайн работает.
            _logger.info("в layout.json нет зоны manometer — OCR отключён")
            return None
        polygon = np.asarray(component["polygon_px"], dtype=np.float64).reshape(-1, 2)
        x1, y1 = polygon.min(axis=0)
        x2, y2 = polygon.max(axis=0)
        center_x, center_y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        side = float(max(x2 - x1, y2 - y1)) * float(margin)
        half = side / 2.0
        source = np.array(
            [
                [center_x - half, center_y - half],
                [center_x + half, center_y - half],
                [center_x + half, center_y + half],
                [center_x - half, center_y + half],
            ],
            dtype=np.float32,
        )
        edge = float(self.roi_size - 1)
        target = np.array([[0.0, 0.0], [edge, 0.0], [edge, edge], [0.0, edge]], dtype=np.float32)
        return cv2.getPerspectiveTransform(source, target).astype(np.float64)

    def _smoother(self, equipment_id: str) -> ar_board.Smoother:
        smoother = self._smoothers.get(equipment_id)
        if smoother is None:
            smoother = ar_board.Smoother(alpha=self._smooth_alpha)
            self._smoothers[equipment_id] = smoother
        return smoother

    @staticmethod
    def load_layout_file(path: str | Path) -> dict[str, Any]:
        """Читает и строго валидирует layout.json, не меняя состояние пайплайна.

        Ошибки намеренно не глотаются: HTTP-слой превращает
        `FileNotFoundError` в 404, а `ValueError` (включая битый JSON) — в 422.
        """
        resolved = Path(path).expanduser().resolve()
        layout = ar_board.load_layout(resolved)
        components = layout.get("components")
        if not isinstance(components, list) or not components:
            raise ValueError("в layout.json нет зон: поле components пустое")
        seen: set[str] = set()
        for item in components:
            if not isinstance(item, dict):
                raise ValueError("элемент components должен быть объектом")
            component_id = item.get("component_id")
            if not isinstance(component_id, str) or not component_id.strip():
                raise ValueError("у зоны отсутствует component_id")
            if component_id in seen:
                raise ValueError(f"зона {component_id} описана дважды")
            seen.add(component_id)
            polygon = item.get("polygon_px")
            if not isinstance(polygon, list) or len(polygon) < 3:
                raise ValueError(f"у зоны {component_id} меньше 3 точек polygon_px")
            for point in polygon:
                if (
                    not isinstance(point, (list, tuple))
                    or len(point) != 2
                    or not all(isinstance(value, (int, float)) for value in point)
                ):
                    raise ValueError(
                        f"у зоны {component_id} некорректная точка polygon_px"
                    )
        return layout

    def apply_layout(
        self,
        layout_path: str | Path,
        defects: bool | None = None,
    ) -> dict[str, Any]:
        """Атомарно подменяет layout и флаг эвристик (hot-reload).

        Новый layout, метка и матрица ROI готовятся до захвата блокировки:
        если файл битый, работающий пайплайн не меняется вовсе. Активные
        WebSocket-сессии продолжают работать — `process()` берёт снимок
        ссылок под той же блокировкой, поэтому кадр всегда видит цельный
        layout (старый или новый), а `Smoother`/OCR-кэш сбрасываются.

        `defects=None` сохраняет текущее значение при перезагрузке того же
        планшета; при смене агрегата берётся флаг `board.defects` из layout
        (чистый планшет — `false`, дефектный — `true`). `True/False` включает
        или выключает эвристики на лету.
        """
        resolved = Path(layout_path).expanduser().resolve()
        layout = self.load_layout_file(resolved)
        roi_matrix = self._build_roi_matrix(layout, self._roi_margin)
        labels = {
            str(item.get("component_id")): str(item.get("label") or "")
            for item in layout.get("components", [])
        }
        with self._lock:
            previous_board_id = self.board_id
            new_board_id = layout_board_info(layout)["board_id"]
            self.layout = layout
            self.layout_path = str(resolved)
            self.roi_matrix = roi_matrix
            self._labels = labels
            self.board_id = new_board_id
            self._layout_revision += 1
            if defects is not None:
                self.defects_enabled = bool(defects)
            elif new_board_id != previous_board_id:
                # Смена агрегата несёт свой флаг: чистый планшет выключает
                # эвристики, дефектный включает. Перезагрузка того же
                # планшета (out_* → ref) флаг не трогает — иначе ручной
                # `defects=false` отменялся бы следующим кадром.
                board_defects = _board_defects_flag(layout)
                if board_defects is not None:
                    self.defects_enabled = board_defects
            self.reset()
            self.anomaly.reset()
            self.corrosion.reset()
            self.visual_registry.reset()
            # Новая геометрия — новая идентификация: история и пауза
            # начинаются заново и после ручного override, и после авто-смены.
            self._identity_history.clear()
            self._identity_cooldown = 0
        _logger.info(
            "layout подменён: %s (%d зон, revision=%d, defects=%s)",
            resolved,
            len(layout.get("components", [])),
            self._layout_revision,
            self.defects_enabled,
        )
        return self.layout_info()

    def layout_info(self) -> dict[str, Any]:
        """Состояние загруженного layout для `GET/POST /pipeline/layout`."""
        with self._lock:
            path = Path(self.layout_path).expanduser().resolve()
            layout = self.layout
            revision = self._layout_revision
            gauge_ocr = self.roi_matrix is not None
            defects_enabled = bool(self.defects_enabled)
            registry = self.registry
        components = [
            str(item.get("component_id"))
            for item in layout.get("components", [])
            if isinstance(item, dict)
        ]
        board = layout.get("board") if isinstance(layout.get("board"), dict) else {}
        meta = layout_board_info(layout)
        entry = registry.entry_for_layout(path)
        board_id = meta["board_id"] or (entry.board_id if entry else None)
        identity_marker_id = (
            meta["identity_marker_id"]
            if meta["identity_marker_id"] is not None
            else (entry.identity_marker_id if entry else None)
        )
        try:
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
            mtime_epoch: float | None = round(stat.st_mtime, 3)
            file_size: int | None = stat.st_size
            exists = True
        except OSError:
            mtime = None
            mtime_epoch = None
            file_size = None
            exists = False
        return {
            "path": str(path),
            "exists": exists,
            "layout_version": layout.get("version"),
            "revision": revision,
            "components": len(components),
            "component_ids": components,
            "mtime": mtime,
            "mtime_epoch": mtime_epoch,
            "file_size": file_size,
            "equipment_id": layout.get("equipment_id"),
            "board": {
                "image": board.get("image"),
                "width_px": board.get("width_px"),
                "height_px": board.get("height_px"),
                "board_id": board_id,
                "identity_marker_id": identity_marker_id,
                "guard_marker_id": meta["guard_marker_id"],
            },
            "board_id": board_id,
            "identity_marker_id": identity_marker_id,
            "guard_marker_id": meta["guard_marker_id"],
            "board_registry": registry.info(),
            "alignment": self.align_mode,
            "align": self.visual_registry.info(),
            "gauge_ocr": gauge_ocr,
            "defects_enabled": defects_enabled,
        }

    # Публичный вход

    def process(self, image: Image.Image, equipment_id: str) -> PipelineResult:
        """Полный проход кадра. Исключений наружу не бросает."""
        started = time.perf_counter()
        frame = np.asarray(image.convert("RGB"), dtype=np.uint8)[:, :, ::-1]
        frame = np.ascontiguousarray(frame)
        height, width = frame.shape[:2]
        timings: dict[str, float] = {}

        # Снимок ссылок: hot-reload меняет их под этой же блокировкой, поэтому
        # кадр целиком считается по одному layout (старый или новый).
        with self._lock:
            layout = self.layout
            roi_matrix = self.roi_matrix
            labels = self._labels
            smoother = self._smoother(equipment_id)

        feature_mode = False
        feature_extra: dict[str, Any] = {}
        auto_switched = False
        pose = None
        confidence = 0.0
        if self.align_mode in {"features", "auto"}:
            try:
                match = self.visual_registry.register(frame, equipment_id)
            except Exception as exc:  # noqa: BLE001 - привязка не роняет кадр
                _logger.exception("feature-привязка упала на кадре")
                match = None
            if match is not None:
                target = Path(match.layout_path).expanduser().resolve()
                if (
                    match.board_id != self.board_id
                    or target != Path(self.layout_path).expanduser().resolve()
                ):
                    try:
                        self.apply_layout(target)
                        auto_switched = True
                        try:
                            from .. import analytics

                            analytics.reset(equipment_id)
                        except Exception:  # noqa: BLE001
                            _logger.exception("сброс аналитики после смены layout упал")
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        _logger.warning("feature-смена layout не удалась: %s", exc)
                        match = None
                if match is not None:
                    with self._lock:
                        layout = self.layout
                        roi_matrix = self.roi_matrix
                        labels = self._labels
                        smoother = self._smoother(equipment_id)
                    pose = self._feature_pose(match, layout, width, height)
                    confidence = float(match.result.confidence)
                    feature_mode = True
                    feature_extra = {
                        "board_id": match.board_id,
                        "method": "features",
                        "inliers": int(match.result.inliers),
                        "inlier_ratio": round(float(match.result.inlier_ratio), 4),
                        "confidence": round(confidence, 4),
                        "stale": bool(match.result.stale),
                        "age_sec": round(float(match.result.age_sec), 3),
                        "auto_switched": bool(auto_switched),
                    }
            if not feature_mode and self.align_mode == "features":
                timings["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
                board = {
                    "detected": False,
                    "markers": 0,
                    "reprojection_error": None,
                    "coverage": 0.0,
                    "confidence": 0.0,
                    "reason": "no_match",
                    "detail": _REASON_TEXT["no_match"],
                    "method": "features",
                    "board_id": None,
                    "inliers": 0,
                    "inlier_ratio": 0.0,
                    "stale": False,
                    "age_sec": 0.0,
                    "auto_switched": False,
                    "frame_size": [width, height],
                }
                board.update(self._identity_block(layout, None, auto_switched=False))
                return PipelineResult(
                    board=board,
                    ocr=self._cached_ocr(equipment_id, stale=True),
                    timings=timings,
                    image_size=(width, height),
                )

        if not feature_mode:
            try:
                detect_started = time.perf_counter()
                pose = ar_board.detect_board(frame, layout)
                timings["detect_ms"] = round((time.perf_counter() - detect_started) * 1000, 2)
            except Exception as exc:  # noqa: BLE001 - кадр не должен ронять стрим
                _logger.exception("detect_board упал на кадре")
                board = {
                    "detected": False,
                    "markers": 0,
                    "reprojection_error": None,
                    "coverage": 0.0,
                    "confidence": 0.0,
                    "reason": "error",
                    "detail": str(exc),
                    "frame_size": [width, height],
                }
                board.update(self._identity_block(layout, None, auto_switched=False))
                return PipelineResult(
                    board=board,
                    ocr=self._cached_ocr(equipment_id, stale=True),
                    timings={"total_ms": round((time.perf_counter() - started) * 1000, 2)},
                    image_size=(width, height),
                )

            if pose is None:
                reason = self._diagnose(frame, layout)
                timings["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
                board = {
                    "detected": False,
                    "markers": 0,
                    "reprojection_error": None,
                    "coverage": 0.0,
                    "confidence": 0.0,
                    "reason": reason,
                    "detail": _REASON_TEXT.get(reason, reason),
                    "frame_size": [width, height],
                }
                board.update(self._identity_block(layout, None, auto_switched=False))
                return PipelineResult(
                    board=board,
                    ocr=self._cached_ocr(equipment_id, stale=True),
                    timings=timings,
                    image_size=(width, height),
                )

        # --- Идентификация планшета и авто-переключение layout -------------
        if feature_mode:
            meta = layout_board_info(layout)
            identity_marker_id = None
            auto_switched = bool(feature_extra.get("auto_switched", False))
        else:
            identity_started = time.perf_counter()
            identity_marker_id, _ = self._identity_from_frame(frame, layout)
            auto_switched = False
            if identity_marker_id is not None:
                auto_switched = self._maybe_auto_switch(identity_marker_id, equipment_id)
            else:
                self._observe_missing_identity()
            timings["identity_ms"] = round(
                (time.perf_counter() - identity_started) * 1000, 2
            )

        if auto_switched:
            # Новая геометрия могла отличаться: позу пересчитываем по новому
            # layout. Это происходит только на кадре переключения.
            with self._lock:
                layout = self.layout
                roi_matrix = self.roi_matrix
                labels = self._labels
                smoother = self._smoother(equipment_id)
            try:
                refreshed = ar_board.detect_board(frame, layout)
                if refreshed is not None:
                    pose = refreshed
            except Exception:  # noqa: BLE001
                _logger.exception("повторная детекция после авто-переключения упала")

        meta = layout_board_info(layout)
        if not feature_mode:
            confidence = board_confidence(pose.reprojection_error, pose.coverage)
        # Identity/guard-маркеры участвуют в позе, но в историческом поле
        # `markers` считаем только угловые маркеры планшета (0..3), чтобы
        # контракт клиента не менялся из-за маркеров идентификации.
        roles = {
            int(item["id"]): str(item.get("role") or "")
            for item in layout.get("markers", [])
            if isinstance(item, dict) and "id" in item
        }
        corner_ids = [
            marker_id
            for marker_id in pose.marker_ids
            if roles.get(int(marker_id), "") not in {"identity", "guard"}
        ]
        board = {
            "detected": True,
            "markers": len(corner_ids),
            "marker_ids": corner_ids,
            "all_marker_ids": list(pose.marker_ids),
            "reprojection_error": round(float(pose.reprojection_error), 3),
            "coverage": round(float(pose.coverage), 4),
            "confidence": confidence,
            "valid": bool(pose.is_valid),
            "method": pose.method,
            "reason": None,
            "frame_size": [width, height],
        }
        board.update(
            self._identity_block(
                layout, identity_marker_id, auto_switched=auto_switched
            )
        )
        if feature_mode:
            board.update(feature_extra)
        else:
            board.update(
                {
                    "method": pose.method,
                    "inliers": 0,
                    "inlier_ratio": 0.0,
                    "stale": False,
                    "age_sec": 0.0,
                    "auto_switched": bool(auto_switched),
                }
            )
        if meta["identity_marker_id"] is not None and self.registry.available:
            # Планшет нового поколения обязан предъявить identity-маркер.
            # Без него layout не меняем, но кадр честно помечаем «не опознан».
            if identity_marker_id is None:
                board["detected"] = False
                board["reason"] = "identity_missing"
                board["detail"] = _REASON_TEXT["identity_missing"]
        if pose.board_corners is not None:
            board["corners"] = [
                [round(float(x), 1), round(float(y), 1)] for x, y in pose.board_corners
            ]

        project_started = time.perf_counter()
        projected = ar_board.project_components(pose, layout)
        smoothed = smoother.update(projected)
        timings["project_ms"] = round((time.perf_counter() - project_started) * 1000, 2)

        # Маркеры — это fiducial-метки, а не дефект поверхности. Если оставить
        # чёрный квадрат в ROI узла, эвристики видят «окисление», а аномалия —
        # выброс текстуры. Для визуальных каналов маркеры затираем.
        visual_frame = self._mask_markers(frame, pose)
        # Эвристики/аномалии/corrosion имеют смысл только там, где в зоне есть
        # фотография поверхности. На схематичном планшете (белый фон, рисунок)
        # они дают ложные «масло/окисление», поэтому такие зоны пропускаем.
        photo_ids = self._photo_component_ids(visual_frame, smoothed)

        detections = self._component_detections(smoothed, confidence, width, height)
        defects = self._detect_defects(
            visual_frame, smoothed, width, height, labels, component_ids=photo_ids
        )
        if defects:
            detections.extend(defects)
        signs = (
            [
                {
                    "sign": "guard_missing",
                    "component_id": "coupling_guard",
                    "present": False,
                    "unavailable": True,
                }
            ]
            if feature_mode
            else self._check_guard_marker(
                frame, smoothed, meta["guard_marker_id"]
            )
        )
        signs.extend(
            self.anomaly.score_frame(
                visual_frame,
                layout,
                pose.homography,
                [cid for cid in photo_ids],
                equipment_id,
            )
        )
        signs.extend(
            self.corrosion.score_frame(
                visual_frame,
                [
                    item
                    for item in smoothed
                    if str(item.get("component_id")) in photo_ids
                ],
                equipment_id,
            )
        )

        statuses = build_components_status(detections, list(COMPONENT_LABELS))
        overlays = self._overlays(smoothed, statuses, confidence, labels)

        ocr = self._maybe_read_ocr(frame, pose, equipment_id, timings, roi_matrix)

        timings["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return PipelineResult(
            board=board,
            overlays=overlays,
            detections=detections,
            defects=defects,
            signs=signs,
            components_status=[_status_dict(item) for item in statuses],
            ocr=ocr,
            timings=timings,
            image_size=(width, height),
        )

    # Шаги

    @staticmethod
    def _feature_pose(
        match: Any,
        layout: dict[str, Any],
        width: int,
        height: int,
    ) -> ar_board.BoardPose:
        """Синтетическая поза из feature-гомографии (без ArUco)."""
        homography = np.asarray(match.result.homography, dtype=np.float64)
        board = layout.get("board") if isinstance(layout.get("board"), dict) else {}
        board_w = float(board.get("width_px") or 1.0)
        board_h = float(board.get("height_px") or 1.0)
        corners = np.array(
            [[0.0, 0.0], [board_w, 0.0], [board_w, board_h], [0.0, board_h]],
            dtype=np.float64,
        )
        quad = cv2.perspectiveTransform(
            corners.reshape(-1, 1, 2), homography
        ).reshape(-1, 2)
        coverage = 0.0
        if width > 0 and height > 0:
            coverage = abs(float(cv2.contourArea(quad.astype(np.float32)))) / (
                width * height
            )
        return ar_board.BoardPose(
            homography=homography,
            marker_ids=[],
            reprojection_error=float(match.result.reproj_px or 0.0),
            coverage=min(1.0, coverage),
            is_valid=True,
            frame_size=(width, height),
            board_corners=quad,
            method="features",
        )

    def _identity_from_frame(
        self,
        frame: np.ndarray,
        layout: dict[str, Any],
    ) -> tuple[int | None, dict[int, np.ndarray]]:
        """Ищет identity-маркеры реестра и текущего layout в кадре.

        Приоритет — маркер текущего layout (чтобы кадр с двумя планшетами не
        «дёргал» переключение); иначе берётся самый крупный найденный.
        """
        meta = layout_board_info(layout)
        ids = set(self.registry.identity_ids())
        if meta["identity_marker_id"] is not None:
            ids.add(int(meta["identity_marker_id"]))
        if not ids:
            return None, {}
        aruco = layout.get("aruco") if isinstance(layout.get("aruco"), dict) else {}
        found = detect_identity_markers(frame, ids, aruco.get("dictionary"))
        if not found:
            return None, {}
        preferred = meta["identity_marker_id"]
        if preferred is not None and int(preferred) in found:
            return int(preferred), found
        chosen = max(
            found,
            key=lambda marker_id: float(
                cv2.contourArea(np.asarray(found[marker_id], dtype=np.float32))
            ),
        )
        return int(chosen), found

    def _reset_identity_tracking(self) -> None:
        with self._lock:
            self._identity_history.clear()
            self._identity_cooldown = 0

    def _observe_missing_identity(self) -> None:
        """Кадр без identity-маркера: layout не меняем, историю не сбрасываем."""
        with self._lock:
            if self._identity_cooldown > 0:
                self._identity_cooldown -= 1
            self._identity_history.append(None)

    def _maybe_auto_switch(self, identity_marker_id: int, equipment_id: str) -> bool:
        """Переключает layout по identity-маркеру с гистерезисом.

        Правило: планшет считается «стабильно видимым», если его identity-маркер
        встретился `identity_stable_frames` раз в последних `N+2` кадрах
        (для стабильного планшета это ровно 3 кадра подряд; одиночный
        пропуск/блик не сбрасывает счётчик). После переключения действует
        пауза `N` кадров, поэтому при чередовании планшетов layout меняется не
        чаще, чем раз в `N` кадров.

        `auto_switched=true` возвращается только на кадре фактической подмены.
        Ручной `POST /pipeline/layout` очищает историю: после override
        автопереключение снова требует `identity_stable_frames` подтверждений.
        """
        with self._lock:
            if self._identity_cooldown > 0:
                self._identity_cooldown -= 1
                self._identity_history.append(identity_marker_id)
                return False
            self._identity_history.append(identity_marker_id)

            entry = self.registry.entry_for_marker(identity_marker_id)
            if entry is None or entry.layout_path is None:
                return False
            try:
                target = Path(entry.layout_path).expanduser().resolve()
                current = Path(self.layout_path).expanduser().resolve()
            except OSError:  # pragma: no cover - защита от странных путей Windows
                return False
            if target == current:
                return False
            sightings = sum(
                1 for item in self._identity_history if item == identity_marker_id
            )
            if sightings < self.identity_stable_frames:
                return False

        try:
            self.apply_layout(target)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _logger.warning("авто-переключение на %s не удалось: %s", target, exc)
            self._reset_identity_tracking()
            return False

        with self._lock:
            self._identity_history.clear()
            self._identity_history.append(identity_marker_id)
            self._identity_cooldown = self.identity_stable_frames
        _logger.info(
            "авто-переключение layout по identity-маркеру %s: %s",
            identity_marker_id,
            target,
        )
        # Новый планшет — новое наблюдение: старое окно не смешиваем.
        try:
            from .. import analytics

            analytics.reset(equipment_id)
        except Exception:  # noqa: BLE001 - сброс окна не должен ронять кадр
            _logger.exception("сброс окна аналитики после авто-переключения упал")
        return True

    def _identity_block(
        self,
        layout: dict[str, Any],
        identity_marker_id: int | None,
        *,
        auto_switched: bool,
    ) -> dict[str, Any]:
        """Поля `board` про идентификацию планшета (общие для всех веток)."""
        meta = layout_board_info(layout)
        entry = self.registry.entry_for_marker(identity_marker_id)
        if entry is None:
            entry = self.registry.entry_for_layout(self.layout_path)
        configured_id = meta["identity_marker_id"]
        if configured_id is None and entry is not None:
            configured_id = entry.identity_marker_id
        return {
            "board_id": entry.board_id if entry is not None else meta["board_id"],
            "identity_marker_id": (
                identity_marker_id
                if identity_marker_id is not None
                else configured_id
            ),
            "identity_detected": identity_marker_id is not None,
            "auto_switched": bool(auto_switched),
            "board_registry": self.registry.available,
        }

    def _diagnose(self, frame: np.ndarray, layout: dict[str, Any]) -> str:
        """Причина, по которой `detect_board` вернул None."""
        try:
            relaxed = ar_board.detect_board(
                frame,
                layout,
                min_markers=1,
                max_reprojection_error=1e9,
                min_coverage=0.0,
            )
        except Exception:  # noqa: BLE001
            return "error"
        if relaxed is None:
            return "no_markers"
        if len(relaxed.marker_ids) < 3:
            return "partial"
        return "low_quality"

    def _component_detections(
        self,
        projected: Iterable[dict[str, Any]],
        confidence: float,
        width: int,
        height: int,
    ) -> list[Detection]:
        detections: list[Detection] = []
        for item in projected:
            if not item.get("visible"):
                continue
            polygon = item.get("polygon") or []
            component_id = str(item.get("component_id"))
            if len(polygon) < 3 or component_id not in CLASS_IDS:
                continue
            detections.append(
                Detection(
                    class_id=CLASS_IDS[component_id],
                    class_name=component_id,
                    kind="component",
                    confidence=confidence,
                    bbox=_polygon_bbox(polygon, width, height),
                    source="board",
                )
            )
        return detections

    def _check_guard_marker(
        self,
        frame: np.ndarray,
        projected: Sequence[dict[str, Any]],
        guard_marker_id: int | None = 4,
    ) -> list[dict[str, Any]]:
        """Маркерная проверка кожуха: ArUco в зоне `coupling_guard`.

        Id маркера берётся из `layout.board.guard_marker_id` (по умолчанию 4).
        Если маркер не найден в зоне — признак `guard_missing` (confidence 0.9).
        Если маркер найден — признак не публикуется (`not_detected` в окне).
        Проверка не зависит от флага `defects`: это отдельный визуальный канал,
        и на чистом планшете он обязан подтверждать наличие кожуха.
        """
        if _check_marker_in_roi is None or guard_marker_id is None:
            return []
        component = next(
            (
                item
                for item in projected
                if str(item.get("component_id")) == "coupling_guard"
                and item.get("visible")
                and len(item.get("polygon") or []) >= 3
            ),
            None,
        )
        if component is None:
            return []
        try:
            result = _check_marker_in_roi(frame, int(guard_marker_id), component["polygon"])
        except Exception:  # noqa: BLE001 - маркерная проверка не роняет кадр
            _logger.exception("check_marker_in_roi упал на кадре")
            return []
        if getattr(result, "error", None):
            return []
        if getattr(result, "present", False):
            return []
        return [
            {
                "sign": "guard_missing",
                "component_id": "coupling_guard",
                "confidence": 0.9,
                "source": "marker",
                "present": True,
                "reason": getattr(result, "reason", None),
            }
        ]

    def _photo_component_ids(
        self,
        frame: np.ndarray,
        projected: Sequence[dict[str, Any]],
        *,
        max_white_frac: float = 0.60,
        min_std: float = 8.0,
    ) -> set[str]:
        """Узлы, где в зоне действительно фотография поверхности.

        На печатном схематичном планшете зона может быть почти белой с
        рисунком: эвристики «масло/окисление» и модель аномалий принимают
        отдельные тёмные штрихи за дефект. Гейт по доле белого фона не
        зависит от layout: на дефектном планшете фото занимает зону целиком,
        на чистом — нет.
        """
        photo: set[str] = set()
        for item in projected:
            if not item.get("visible"):
                continue
            polygon = item.get("polygon") or []
            if len(polygon) < 4:
                continue
            roi = self._component_roi(frame, polygon)
            if roi is None or roi.size == 0:
                continue
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            white_frac = float(
                np.mean((hsv[:, :, 2] >= 235) & (hsv[:, :, 1] <= 30))
            )
            if white_frac > max_white_frac:
                continue
            if float(roi.std()) < min_std:
                continue
            photo.add(str(item.get("component_id")))
        return photo

    def _mask_markers(
        self,
        frame: np.ndarray,
        pose: ar_board.BoardPose,
    ) -> np.ndarray:
        """Затирает ArUco-маркеры в кадре (для эвристик/аномалий/коррозии).

        Маркеры идентификации и кожуха могут попадать внутрь ROI узла: чёрный
        квадрат даёт ложное «окисление»/«масло», а модель аномалий видит
        выброс текстуры. Для визуальной проверки поверхности маркер
        закрашивается медианным цветом узкого кольца вокруг него;
        guard-проверка по-прежнему работает по исходному кадру.
        """
        corners = getattr(pose, "marker_corners", None) or {}
        if not corners:
            return frame
        masked = frame.copy()
        height, width = frame.shape[:2]
        for quad in corners.values():
            points = np.asarray(quad, dtype=np.float32).reshape(-1, 2)
            hull = cv2.convexHull(points).astype(np.int32)
            x, y, w, h = cv2.boundingRect(hull)
            if w < 2 or h < 2:
                continue
            margin = 12
            x1, y1 = max(0, x - margin), max(0, y - margin)
            x2 = min(width, x + w + margin)
            y2 = min(height, y + h + margin)
            patch = frame[y1:y2, x1:x2]
            if patch.size == 0:
                continue
            local_mask = np.zeros(patch.shape[:2], dtype=np.uint8)
            cv2.fillConvexPoly(
                local_mask, hull - np.array([x1, y1], dtype=np.int32), 255
            )
            near = cv2.dilate(
                local_mask, np.ones((9, 9), dtype=np.uint8), iterations=1
            )
            ring = patch[(near > 0) & (local_mask == 0)]
            if ring.size:
                color = tuple(int(value) for value in np.median(ring, axis=0))
            else:
                color = (240, 240, 240)
            cv2.fillConvexPoly(masked, hull, color)
        return masked

    def _detect_defects(
        self,
        frame: np.ndarray,
        projected: Sequence[dict[str, Any]],
        width: int,
        height: int,
        labels: dict[str, str] | None = None,
        component_ids: set[str] | None = None,
    ) -> list[Detection]:
        """Визуальные дефекты `cv_classic.defects` по ROI каждого узла.

        Контракт модуля: `detect_defects(roi_bgr) -> {имя: {score, present,
        confidence, evidence, error}}`. Порог срабатывания (`present`) задан
        в `cv_classic/configs/defects.json`, здесь он не дублируется.

        Публикуемая уверенность — `score` детектора (калиброванный вклад
        признаков). Класс и узел проставляются явно, `evidence` переносит
        score/порог/площадь/признаки в `cv_state` и ответ API.
        """
        module = self._defects_module
        if module is None or not self.defects_enabled:
            return []
        fn = getattr(module, "detect_defects", None)
        if fn is None:
            return []

        detections: list[Detection] = []
        for item in projected:
            polygon = item.get("polygon") or []
            if not item.get("visible") or len(polygon) < 3:
                continue
            component_id = str(item.get("component_id"))
            if component_ids is not None and component_id not in component_ids:
                continue
            roi = self._component_roi(frame, polygon)
            if roi is None:
                continue
            try:
                raw = fn(roi)
            except Exception:  # noqa: BLE001 - эвристики не роняют кадр
                _logger.exception("cv_classic.defects упал; отключаю эвристики")
                self._defects_module = None
                return detections
            if not isinstance(raw, dict):
                continue

            bbox = _polygon_bbox(polygon, width, height)
            label = (labels or {}).get(component_id) or COMPONENT_LABELS.get(
                component_id, component_id
            )
            for name, payload in raw.items():
                class_name = _DEFECT_CLASS_MAP.get(str(name))
                if class_name is None or not isinstance(payload, dict):
                    continue
                if not payload.get("present"):
                    continue
                score = _clamp(float(payload.get("score") or 0.0))
                if score <= 0.0:
                    continue
                features = payload.get("evidence")
                if not isinstance(features, dict):
                    features = {}
                evidence = {
                    "detector": str(name),
                    "class": class_name,
                    "score": round(score, 4),
                    "threshold": features.get("threshold"),
                    "area_px": item.get("area_px"),
                    "component_label": label,
                    "features": features,
                }
                detections.append(
                    Detection(
                        class_id=CLASS_IDS[class_name],
                        class_name=class_name,
                        kind="defect",
                        confidence=round(score, 3),
                        bbox=bbox,
                        source="board",
                        component_id=component_id,
                        evidence=evidence,
                    )
                )
        return _dedupe_defects(detections)

    @staticmethod
    def _component_roi(
        frame: np.ndarray,
        polygon: Sequence[Sequence[float]],
        max_side: int = 320,
    ) -> np.ndarray | None:
        """Вырезает зону узла в квадратную ROI (без перспективы)."""
        points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
        if len(points) < 4:
            return None
        x1, y1 = points.min(axis=0)
        x2, y2 = points.max(axis=0)
        if min(x2 - x1, y2 - y1) < 24:
            return None
        side = int(min(max_side, max(64, max(x2 - x1, y2 - y1))))
        target = np.array(
            [[0.0, 0.0], [side - 1, 0.0], [side - 1, side - 1], [0.0, side - 1]],
            dtype=np.float32,
        )
        try:
            matrix = cv2.getPerspectiveTransform(points[:4], target)
            return cv2.warpPerspective(
                frame,
                matrix,
                (side, side),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
        except cv2.error:
            return None

    def _overlays(
        self,
        projected: Sequence[dict[str, Any]],
        statuses: Sequence[Any],
        confidence: float,
        labels: dict[str, str],
    ) -> list[dict[str, Any]]:
        by_id = {item.component_id: item for item in statuses}
        overlays: list[dict[str, Any]] = []
        for item in projected:
            component_id = str(item.get("component_id"))
            status = by_id.get(component_id)
            state = status.status if status is not None else "insufficient_data"
            overlays.append(
                {
                    "component_id": component_id,
                    "label": labels.get(component_id) or COMPONENT_LABELS.get(component_id, component_id),
                    "polygon": item.get("polygon") or [],
                    "visible": bool(item.get("visible")),
                    "status": state,
                    "color": STATUS_COLORS.get(state, "grey"),
                    "confidence": round(float(status.confidence), 3) if status else 0.0,
                    "board_confidence": confidence,
                    "defects": list(status.defects) if status else [],
                    "area_px": item.get("area_px"),
                }
            )
        return overlays

    # OCR

    def _maybe_read_ocr(
        self,
        frame: np.ndarray,
        pose: ar_board.BoardPose,
        equipment_id: str,
        timings: dict[str, float],
        roi_matrix: np.ndarray | None,
    ) -> dict[str, Any] | None:
        """Читает манометр раз в `ocr_every` кадров, между чтениями — кэш.

        Если в layout нет зоны манометра, OCR недоступен: возвращаем `None`.
        """
        if roi_matrix is None:
            return None
        counter = self._counters.get(equipment_id, 0) + 1
        self._counters[equipment_id] = counter

        if (counter - 1) % self.ocr_every != 0:
            return self._cached_ocr(equipment_id)

        ocr_started = time.perf_counter()
        reading = self._read_gauge(frame, pose, roi_matrix)
        timings["ocr_ms"] = round((time.perf_counter() - ocr_started) * 1000, 2)
        reading["refreshed"] = True
        reading["age_frames"] = 0
        reading["_frame"] = counter
        self._last_ocr[equipment_id] = reading

        cv_state.publish_ocr(
            equipment_id,
            value=reading.get("value"),
            unit=str(reading.get("unit") or "бар"),
            confidence=float(reading.get("confidence") or 0.0),
            error=reading.get("error"),
            angle_deg=reading.get("angle_deg"),
            source="board",
        )
        public = dict(reading)
        public.pop("_frame", None)
        return public

    def _read_gauge(
        self,
        frame: np.ndarray,
        pose: ar_board.BoardPose,
        roi_matrix: np.ndarray | None,
    ) -> dict[str, Any]:
        """Вырезает зону манометра по гомографии и читает стрелку."""
        if roi_matrix is None:
            return {
                "value": None,
                "unit": "бар",
                "confidence": 0.0,
                "error": "no_gauge_zone",
                "detail": "на планшете нет зоны манометра",
                "angle_deg": None,
            }
        try:
            matrix = roi_matrix @ np.linalg.inv(pose.homography)
            crop = cv2.warpPerspective(
                frame,
                matrix,
                (self.roi_size, self.roi_size),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
        except (np.linalg.LinAlgError, cv2.error) as exc:
            return {
                "value": None,
                "unit": "бар",
                "confidence": 0.0,
                "error": "roi_failed",
                "detail": str(exc),
                "angle_deg": None,
            }

        try:
            reading = read_gauge(crop, self.gauge_config)
        except Exception as exc:  # noqa: BLE001 - OCR не должен ронять стрим
            _logger.exception("read_gauge упал")
            return {
                "value": None,
                "unit": "бар",
                "confidence": 0.0,
                "error": "ocr_failed",
                "detail": str(exc),
                "angle_deg": None,
            }

        unit = reading.unit or "бар"
        scale = self.unit_scale if self.unit_scale is not None else _UNIT_TO_BAR.get(unit.lower(), 1.0)
        value = None if reading.value is None else round(float(reading.value) * float(scale), 3)
        return {
            "value": value,
            "raw_value": None if reading.value is None else round(float(reading.value), 3),
            "unit": "бар" if scale != 1.0 else unit,
            "confidence": round(float(reading.confidence), 3),
            "error": reading.error,
            "angle_deg": None if reading.angle_deg is None else round(float(reading.angle_deg), 2),
            "method": reading.method,
            "image_size": [self.roi_size, self.roi_size],
        }

    # Сервисные методы

    def _cached_ocr(self, equipment_id: str, stale: bool = False) -> dict[str, Any] | None:
        """Последнее OCR-значение без служебных ключей (кэш между чтениями)."""
        previous = self._last_ocr.get(equipment_id)
        if previous is None:
            return None
        cached = dict(previous)
        cached.pop("_frame", None)
        cached["refreshed"] = False
        cached["age_frames"] = self._counters.get(equipment_id, 0) - int(
            previous.get("_frame", 0)
        )
        if stale:
            cached["stale"] = True
            cached["detail"] = "планшет не найден в текущем кадре"
        return cached

    def reset(self, equipment_id: str | None = None) -> None:
        """Сбрасывает сглаживание и кэш OCR (смена сценария/камеры/layout)."""
        with self._lock:
            if equipment_id is None:
                self._smoothers.clear()
                self._counters.clear()
                self._last_ocr.clear()
                return
            self._smoothers.pop(equipment_id, None)
            self._counters.pop(equipment_id, None)
            self._last_ocr.pop(equipment_id, None)

    def info(self) -> dict[str, Any]:
        """Краткая конфигурация пайплайна для `/health`."""
        with self._lock:
            layout = self.layout
            revision = self._layout_revision
            gauge_ocr = self.roi_matrix is not None
            registry = self.registry
        meta = layout_board_info(layout)
        entry = registry.entry_for_layout(self.layout_path)
        return {
            "enabled": True,
            "layout": self.layout_path,
            "layout_revision": revision,
            "layout_components": len(layout.get("components", [])),
            "layout_component_ids": [
                str(item.get("component_id"))
                for item in layout.get("components", [])
                if isinstance(item, dict)
            ],
            "board_id": meta["board_id"] or (entry.board_id if entry else None),
            "identity_marker_id": (
                meta["identity_marker_id"]
                if meta["identity_marker_id"] is not None
                else (entry.identity_marker_id if entry else None)
            ),
            "guard_marker_id": meta["guard_marker_id"],
            "board_registry": registry.info(),
            "identity_stable_frames": self.identity_stable_frames,
            "gauge_ocr": gauge_ocr,
            "gauge_config": self.gauge_config_path,
            "gauge_config_loaded": self.gauge_config is not None,
            "ocr_every": self.ocr_every,
            "roi_size": self.roi_size,
            "defects_module": self._defects_module is not None,
            "defects_enabled": bool(self.defects_enabled),
            "anomaly": self.anomaly.status(),
            "corrosion": self.corrosion.status(),
            "align": {
                "mode": self.align_mode,
                "references": len(self.visual_registry.entries),
                "every": self.visual_registry.every,
            },
        }


def _status_dict(status: Any) -> dict[str, Any]:
    return {
        "component_id": status.component_id,
        "status": status.status,
        "confidence": status.confidence,
        "defects": list(status.defects),
        "bbox": status.bbox,
    }


class BoardDetector:
    """`Detector`-совместимая обёртка над пайплайном (для фабрики детекторов).

    Основной путь `/ws/frame` вызывает `pipeline.process(...)` напрямую — там
    нужны оверлеи и OCR. Этот класс нужен, чтобы `get_detector()` возвращал
    объект с прежним интерфейсом `detect(image) -> list[Detection]`.
    """

    source = "board"

    def __init__(self, pipeline: BoardPipeline | None = None) -> None:
        self.pipeline = pipeline or BoardPipeline()

    def detect(self, image: Image.Image) -> list[Detection]:
        return self.pipeline.process(image, "default").detections


__all__ = [
    "BoardDetector",
    "BoardPipeline",
    "COMPONENT_LABELS",
    "PipelineResult",
    "STATUS_COLORS",
    "board_confidence",
]
