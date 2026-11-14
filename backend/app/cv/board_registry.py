"""Реестр планшетов и автоопределение по identity-маркеру.

Контракт (готовит `ar_board/`):

* `ar_board/boards.json` — реестр печатных планшетов::

      {"boards": [
        {"board_id": "clean",  "identity_marker_id": 7,
         "layout": "out_real/layout.json"},
        {"board_id": "defect", "identity_marker_id": 8,
         "layout": "out_defect/layout.json"}
      ]}

* в `layout.json` появился блок `board: {id, identity_marker_id,
  guard_marker_id}`.

Модуль намеренно терпим к отсутствию файлов: старый стенд без реестра должен
работать как раньше (`available=False`, автопереключение выключено). Ошибки
чтения реестра не роняют кадр — только логируются.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

import cv2
import numpy as np

from ..paths import ensure_sys_path

# Корень проекта в sys.path: без этого `import visual_align` не находится
# (пакет лежит рядом с backend/, а не установлен).
ensure_sys_path()

_logger = logging.getLogger(__name__)

DEFAULT_IDENTITY_DICTIONARY = "DICT_4X4_50"
DEFAULT_GUARD_MARKER_ID = 4
# Сколько кадров подряд identity-маркер должен указывать на другой планшет,
# прежде чем пайплайн атомарно подменит layout.
DEFAULT_STABLE_FRAMES = 3
# Длинная сторона кадра для привязки по признакам: больше — медленно и нестабильно.
MAX_ALIGN_SIDE = 1600
# Ширины пирамиды эталонов (px): ORB не тянет разницу масштабов больше ~2x,
# поэтому держим несколько уменьшенных копий и берём лучшую.
ALIGN_PYRAMID = (1600, 900, 500, 280, 160)
# Feature-привязка считается надёжной, только если инлайеров хотя бы столько.
# Иначе пробуем fallback по контуру листа (quad): он грубее, но работает,
# когда планшет занимает меньше ~50% кадра и признаков мало.
QUAD_FALLBACK_INLIERS = 40


@dataclass(frozen=True)
class BoardEntry:
    """Одна запись реестра: планшет ↔ identity-маркер ↔ layout."""

    board_id: str
    identity_marker_id: int
    layout_path: Path | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "board_id": self.board_id,
            "identity_marker_id": self.identity_marker_id,
            "layout": str(self.layout_path) if self.layout_path else None,
        }


class BoardRegistry:
    """Неизменяемый снимок `boards.json` (пустой, если файла нет)."""

    def __init__(
        self,
        path: Path | None = None,
        entries: Iterable[BoardEntry] = (),
    ) -> None:
        self.path = path
        self._entries: tuple[BoardEntry, ...] = tuple(entries)
        self._by_marker: dict[int, BoardEntry] = {
            entry.identity_marker_id: entry for entry in self._entries
        }

    @property
    def available(self) -> bool:
        return bool(self._entries)

    @property
    def entries(self) -> tuple[BoardEntry, ...]:
        return self._entries

    def identity_ids(self) -> set[int]:
        return set(self._by_marker)

    def entry_for_marker(self, marker_id: int | None) -> BoardEntry | None:
        if marker_id is None:
            return None
        return self._by_marker.get(int(marker_id))

    def entry_for_layout(self, layout_path: str | Path | None) -> BoardEntry | None:
        if layout_path is None:
            return None
        target = _resolved(layout_path)
        for entry in self._entries:
            if entry.layout_path is not None and _resolved(entry.layout_path) == target:
                return entry
        return None

    def info(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "available": self.available,
            "boards": [entry.as_dict() for entry in self._entries],
        }


def _resolved(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except OSError:  # pragma: no cover - защита от странных путей Windows
        return Path(path)


def _resolve_layout(raw: Any, base: Path) -> Path | None:
    """Путь из реестра: относительный — от каталога реестра, затем от корня."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw.strip()).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    attempts = [(base / candidate).resolve()]
    # `ar_board/` — корень проекта; реестр может лежать и в backend/.
    project_root = base.parent
    attempts.append((project_root / candidate).resolve())
    for attempt in attempts:
        if attempt.is_file():
            return attempt
    return attempts[0]


def load_board_registry(path: str | Path | None = None) -> BoardRegistry:
    """Читает реестр; нет файла/битый JSON — пустой реестр (fallback)."""
    if path is None:
        return BoardRegistry()
    registry_path = _resolved(path)
    if not registry_path.is_file():
        _logger.info("реестр планшетов не найден: %s (работаю без автоопределения)", registry_path)
        return BoardRegistry(path=registry_path)

    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        _logger.warning("реестр планшетов не прочитан (%s): %s", registry_path, exc)
        return BoardRegistry(path=registry_path)

    items = raw.get("boards") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        _logger.warning("реестр планшетов без списка boards: %s", registry_path)
        return BoardRegistry(path=registry_path)

    entries: list[BoardEntry] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        board_id = item.get("board_id") or item.get("id")
        marker_id = item.get("identity_marker_id")
        if not isinstance(board_id, str) or not board_id.strip():
            continue
        try:
            marker = int(marker_id)
        except (TypeError, ValueError):
            continue
        if marker in seen:
            _logger.warning("реестр: identity-маркер %s описан дважды", marker)
            continue
        seen.add(marker)
        entries.append(
            BoardEntry(
                board_id=board_id.strip(),
                identity_marker_id=marker,
                layout_path=_resolve_layout(item.get("layout"), registry_path.parent),
            )
        )
    _logger.info(
        "реестр планшетов: %s (%d записей)", registry_path, len(entries)
    )
    return BoardRegistry(path=registry_path, entries=entries)


def layout_board_info(layout: Mapping[str, Any] | None) -> dict[str, Any]:
    """Идентификация планшета из блока `board` layout.json.

    Поддерживаются оба варианта именования: `board.id` и `board.board_id`,
    а также дубли на верхнем уровне (на случай промежуточной версии
    генератора). `guard_marker_id` по умолчанию 4 — старый контракт кожуха.
    """
    data: Mapping[str, Any] = layout or {}
    board = data.get("board") if isinstance(data.get("board"), Mapping) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            value = board.get(key)
            if value is not None:
                return value
        for key in keys:
            value = data.get(key)
            if value is not None:
                return value
        return None

    board_id_raw = pick("id", "board_id")
    board_id = str(board_id_raw).strip() if board_id_raw not in (None, "") else None

    identity_raw = pick("identity_marker_id")
    try:
        identity_marker_id = int(identity_raw) if identity_raw is not None else None
    except (TypeError, ValueError):
        identity_marker_id = None

    # Явный `guard_marker_id: null` означает «генератор не наклеил маркер
    # кожуха» (дефектный макет): проверяем стандартный id 4 — его отсутствие и
    # есть признак `guard_missing`. Отсутствие ключа — тот же старый контракт.
    if "guard_marker_id" in board:
        guard_raw = board.get("guard_marker_id")
    elif "guard_marker_id" in data:
        guard_raw = data.get("guard_marker_id")
    else:
        guard_raw = DEFAULT_GUARD_MARKER_ID
    try:
        guard_marker_id = (
            int(guard_raw) if guard_raw is not None else DEFAULT_GUARD_MARKER_ID
        )
    except (TypeError, ValueError):
        guard_marker_id = DEFAULT_GUARD_MARKER_ID

    return {
        "board_id": board_id,
        "identity_marker_id": identity_marker_id,
        "guard_marker_id": guard_marker_id,
    }


@lru_cache(maxsize=8)
def _detector(dictionary_name: str):
    dict_id = getattr(cv2.aruco, dictionary_name, None)
    if dict_id is None:
        raise ValueError(f"unknown aruco dictionary: {dictionary_name}")
    dictionary = cv2.aruco.getPredefinedDictionary(dict_id)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementWinSize = 5
    return cv2.aruco.ArucoDetector(dictionary, params)


def detect_identity_markers(
    frame_bgr: np.ndarray,
    marker_ids: Iterable[int],
    dictionary: str | None = None,
) -> dict[int, np.ndarray]:
    """Находит identity-маркеры в кадре: id -> углы (4, 2).

    Детектор один на словарь (кэш), поэтому повторный проход по кадру дешёвый.
    Если маркер одного id виден несколько раз, берётся самый крупный.
    """
    wanted = {int(item) for item in marker_ids}
    if not wanted or frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return {}
    frame = frame_bgr
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim != 3 or min(frame.shape[:2]) < 48:
        return {}
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    name = dictionary or DEFAULT_IDENTITY_DICTIONARY
    try:
        detector = _detector(str(name))
        corners, ids, _ = detector.detectMarkers(gray)
    except (cv2.error, ValueError):
        return {}
    if corners is None or ids is None:
        return {}
    found: dict[int, np.ndarray] = {}
    for index, raw_id in enumerate(np.asarray(ids).reshape(-1)):
        marker_id = int(raw_id)
        if marker_id not in wanted:
            continue
        quad = np.asarray(corners[index], dtype=np.float64).reshape(4, 2)
        current = found.get(marker_id)
        if current is None or _quad_side(quad) > _quad_side(current):
            found[marker_id] = quad
    return found


def _quad_side(quad: np.ndarray) -> float:
    points = np.asarray(quad, dtype=np.float64).reshape(4, 2)
    return float(
        min(np.linalg.norm(points[(i + 1) % 4] - points[i]) for i in range(4))
    )


# Привязка без маркеров: эталоны + ORB/AKAZE


@dataclass(frozen=True)
class VisualBoardEntry:
    """Эталон для feature-привязки: board_id, картинка, layout."""

    board_id: str
    image_path: Path
    layout_path: Path


@dataclass
class VisualMatch:
    """Лучший эталон и его гомография на текущем кадре."""

    board_id: str
    layout_path: Path
    result: Any
    auto_switched: bool = False


class VisualBoardRegistry:
    """Эталоны `ar_board/references.json` и ленивый feature-матчинг.

    Дескрипторы предвычисляются один раз при старте и кэшируются в памяти
    (и в `align_cache/*.npz`). `register()` вызывается раз в `every` кадров;
    между вызовами возвращается последняя поза с `stale=True`, пока она не
    старше `hold_seconds` (aligner сам держит 1.5 с).

    Приоритет методов: признаки (ORB по пирамиде эталонов) → если их меньше
    `QUAD_FALLBACK_INLIERS` или они не сработали, пробуется контур печатного
    листа (`register_by_quad`). Счётчики по методам — в `info()["stats"]`.
    """

    def __init__(
        self,
        references_path: str | Path,
        cache_dir: str | Path | None = None,
        *,
        mode: str = "features",
        every: int = 2,
        max_ref_width: int = 1600,
        hold_seconds: float = 1.5,
        quad_fallback: bool = True,
    ) -> None:
        self.references_path = Path(references_path)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.mode = str(mode)
        self.every = max(1, int(every))
        self.max_ref_width = int(max_ref_width)
        self.hold_seconds = float(hold_seconds)
        self.quad_fallback = bool(quad_fallback)
        self._lock = threading.RLock()
        self._entries: tuple[VisualBoardEntry, ...] = ()
        self._aligners: dict[str, Any] = {}
        self._errors: list[str] = []
        self._sheet_rects: dict[str, tuple[float, float, float, float] | None] = {}
        self._counters: dict[str, int] = {}
        self._last: dict[str, VisualMatch] = {}
        self._last_ts: dict[str, float] = {}
        # Диагностика привязки: сколько кадров закрыто каким методом.
        self._stats: dict[str, int] = {
            "frames": 0,
            "features": 0,
            "quad": 0,
            "quad_attempts": 0,
            "no_match": 0,
        }
        if self.mode in {"features", "auto"}:
            self._load()

    # Загрузка эталонов

    def _load(self) -> None:
        try:
            raw = json.loads(self.references_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            self._errors.append(f"references: {type(exc).__name__}: {exc}")
            _logger.warning("references.json не прочитан: %s", exc)
            return
        items = raw.get("references") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            self._errors.append("references: нет списка references")
            return
        base = self.references_path.parent
        entries: list[VisualBoardEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            board_id = str(item.get("board_id") or item.get("id") or "").strip()
            if not board_id:
                continue
            image = _resolve_relative(item.get("image"), base)
            layout = _resolve_relative(item.get("layout"), base)
            if image is None or layout is None or not image.is_file() or not layout.is_file():
                self._errors.append(f"{board_id}: нет image/layout")
                continue
            entry = VisualBoardEntry(board_id, image, layout)
            try:
                aligner = self._load_aligner(entry)
            except Exception as exc:  # noqa: BLE001
                self._errors.append(f"{board_id}: {type(exc).__name__}: {exc}")
                _logger.warning("эталон %s не загружен: %s", board_id, exc)
                continue
            entries.append(entry)
            self._aligners[board_id] = aligner
        self._entries = tuple(entries)
        _logger.info(
            "feature-эталоны: %d из %s (mode=%s, every=%d)",
            len(entries),
            self.references_path,
            self.mode,
            self.every,
        )

    def _load_aligner(self, entry: VisualBoardEntry) -> list[Any]:
        """Пирамида эталонов: ORB не тянет разницу масштабов больше ~2x.

        В кадре фотография оборудования может занимать 200–300 px, а эталон
        хранится в 1500 px. Поэтому держим несколько уменьшенных копий
        эталона и выбираем лучшую по числу инлайеров.
        """
        sheet_rect = self._sheet_rect_for(entry)
        return [self._load_aligner_scale(entry, width, sheet_rect)
                for width in ALIGN_PYRAMID]

    def _sheet_rect_for(self, entry: VisualBoardEntry
                        ) -> tuple[float, float, float, float] | None:
        """Геометрия печатного листа для quad-fallback (кэш на эталон)."""
        if entry.board_id in self._sheet_rects:
            return self._sheet_rects[entry.board_id]
        try:
            rect = _infer_sheet_rect(str(entry.image_path),
                                     str(self.references_path.parent))
        except Exception as exc:  # noqa: BLE001 - геометрия не должна ломать старт
            _logger.warning("не удалось определить лист для %s: %s", entry.board_id, exc)
            rect = None
        self._sheet_rects[entry.board_id] = rect
        if rect is not None:
            _logger.info(
                "quad-fallback: лист %s в координатах эталона %s",
                entry.board_id,
                tuple(round(float(v), 1) for v in rect),
            )
        return rect

    def _load_aligner_scale(self, entry: VisualBoardEntry, width: int,
                            sheet_rect: tuple[float, float, float, float] | None = None
                            ) -> Any:
        from visual_align.aligner import FeatureAligner

        cache = (
            self.cache_dir / f"{entry.board_id}_{width}.npz"
            if self.cache_dir is not None
            else None
        )
        if cache is not None and cache.is_file():
            try:
                newest = max(
                    entry.image_path.stat().st_mtime,
                    entry.layout_path.stat().st_mtime,
                )
                if cache.stat().st_mtime >= newest:
                    return FeatureAligner.load(
                        cache,
                        sheet_rect=sheet_rect,
                        hold_seconds=self.hold_seconds,
                        methods=("orb",),
                    )
            except (OSError, ValueError, KeyError):
                pass
        aligner = FeatureAligner.from_files(
            entry.image_path,
            entry.layout_path,
            name=entry.board_id,
            max_ref_width=min(width, self.max_ref_width),
            sheet_rect=sheet_rect,
            hold_seconds=self.hold_seconds,
            methods=("orb",),
        )
        if cache is not None:
            try:
                aligner.save(cache)
            except OSError:
                _logger.warning("не удалось сохранить кэш эталона %s", cache)
        return aligner

    # Публичный API

    @property
    def available(self) -> bool:
        return bool(self._entries)

    @property
    def entries(self) -> tuple[VisualBoardEntry, ...]:
        return self._entries

    def info(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
        return {
            "mode": self.mode,
            "references": len(self._entries),
            "every": self.every,
            "available": self.available,
            "quad_fallback": self.quad_fallback,
            "quad_fallback_inliers": QUAD_FALLBACK_INLIERS,
            "stats": stats,
            "boards": [
                {
                    "board_id": entry.board_id,
                    "image": str(entry.image_path),
                    "layout": str(entry.layout_path),
                    "sheet_rect": (
                        None
                        if self._sheet_rects.get(entry.board_id) is None
                        else [round(float(v), 1)
                              for v in self._sheet_rects[entry.board_id]]
                    ),
                }
                for entry in self._entries
            ],
            "errors": list(self._errors),
        }

    def reset(self, equipment_id: str | None = None) -> None:
        """Сбрасывает счётчики/кэш позы (смена layout или оборудования)."""
        with self._lock:
            if equipment_id is None:
                self._counters.clear()
                self._last.clear()
                self._last_ts.clear()
            else:
                self._counters.pop(equipment_id, None)
                self._last.pop(equipment_id, None)
                self._last_ts.pop(equipment_id, None)
            for pyramid in self._aligners.values():
                for aligner in pyramid:
                    aligner.reset()

    def register(
        self,
        frame_bgr: Any,
        equipment_id: str,
        now: float | None = None,
    ) -> VisualMatch | None:
        """Регистрирует кадр относительно эталонов (раз в `every` кадров)."""
        if not self._entries:
            return None
        moment = time.monotonic() if now is None else float(now)
        with self._lock:
            count = self._counters.get(equipment_id, 0) + 1
            self._counters[equipment_id] = count
            last = self._last.get(equipment_id)
            last_ts = self._last_ts.get(equipment_id)
            should = count == 1 or count % self.every == 0
            if not should and last is not None and last_ts is not None:
                age = moment - last_ts
                if age <= self.hold_seconds:
                    held = copy.copy(last.result)
                    if held.homography is not None:
                        held.homography = held.homography.copy()
                    held.stale = True
                    held.age_sec = float(age)
                    return VisualMatch(
                        board_id=last.board_id,
                        layout_path=last.layout_path,
                        result=held,
                        auto_switched=False,
                    )

        best: VisualMatch | None = None
        entries = [entry for entry in self._entries if entry.board_id in self._aligners]
        with self._lock:
            previous = self._last.get(equipment_id)
        if previous is not None:
            entries.sort(key=lambda entry: entry.board_id != previous.board_id)

        # Крупные кадры (фото с телефона 12 МП) приводим к рабочему размеру:
        # ORB на 4961 px против эталона 1600 px не находит соответствий.
        work_frame = frame_bgr
        frame_scale = 1.0
        try:
            import cv2  # локально, как и в остальном CV-коде
            import numpy as _np

            height, width = frame_bgr.shape[:2]
            longest = max(height, width)
            if longest > MAX_ALIGN_SIDE:
                frame_scale = MAX_ALIGN_SIDE / float(longest)
                work_frame = cv2.resize(
                    frame_bgr,
                    (max(1, int(round(width * frame_scale))), max(1, int(round(height * frame_scale)))),
                    interpolation=cv2.INTER_AREA,
                )
        except Exception:  # noqa: BLE001 - масштабирование не должно ронять кадр
            work_frame = frame_bgr
            frame_scale = 1.0

        for entry in entries:
            pyramid = self._aligners[entry.board_id]
            # Пирамида эталонов: берём масштаб с наибольшим числом инлайеров.
            result = None
            for aligner in pyramid:
                attempt = aligner.register(work_frame, moment)
                if attempt is None or not attempt.ok:
                    continue
                if result is None or int(attempt.inliers) > int(result.inliers):
                    result = attempt
                if int(attempt.inliers) >= 120:
                    break

            # Fallback по контуру листа: признаки либо не сработали, либо их
            # слишком мало для доверия. Приоритет всегда у признаков.
            need_quad = self.quad_fallback and (
                result is None or int(result.inliers) < QUAD_FALLBACK_INLIERS
            )
            if need_quad and pyramid:
                self._bump("quad_attempts")
                try:
                    quad_result = pyramid[0].register_by_quad(work_frame, moment)
                except Exception as exc:  # noqa: BLE001 - fallback не роняет кадр
                    _logger.warning("quad-fallback упал на кадре: %s", exc)
                    quad_result = None
                if quad_result is not None and quad_result.ok:
                    result = quad_result
            # Слабые совпадения не считаем привязкой: иначе зоны одного
            # планшета ложатся на другой с уверенностью 0.1–0.3.
            if result is None or float(getattr(result, "confidence", 0.0) or 0.0) < 0.5:
                continue

            # Гомография посчитана в уменьшенном кадре — возвращаем её в исходный.
            if frame_scale != 1.0 and result.homography is not None:
                shrink = np.array(
                    [[frame_scale, 0.0, 0.0], [0.0, frame_scale, 0.0], [0.0, 0.0, 1.0]]
                )
                result.homography = np.linalg.inv(shrink) @ result.homography
            candidate = VisualMatch(entry.board_id, entry.layout_path, result)
            if best is None or self._rank(candidate) > self._rank(best):
                best = candidate
            if (
                result.method != "quad"
                and not result.stale
                and float(result.confidence) >= 0.9
                and int(result.inliers) >= 50
            ):
                # Текущий эталон уверенно совпал — остальные не проверяем.
                break
        if best is None:
            self._bump("frames")
            self._bump("no_match")
            return None
        self._bump("frames")
        self._bump("quad" if best.result.method == "quad" else "features")
        with self._lock:
            previous = self._last.get(equipment_id)
            best.auto_switched = bool(
                previous is not None and previous.board_id != best.board_id
            )
            self._last[equipment_id] = best
            self._last_ts[equipment_id] = moment
        return best

    def _bump(self, key: str, amount: int = 1) -> None:
        """Счётчик диагностики (потокобезопасно, живёт до перезапуска)."""
        with self._lock:
            self._stats[key] = int(self._stats.get(key, 0)) + int(amount)

    @staticmethod
    def _rank(match: VisualMatch) -> tuple[int, int, int, int, float]:
        """Приоритет: свежая поза > сильные признаки > quad > слабые признаки."""
        result = match.result
        is_quad = 1 if result.method == "quad" else 0
        strong_features = 1 if (
            not is_quad and int(result.inliers) >= QUAD_FALLBACK_INLIERS
        ) else 0
        return (
            1 if not result.stale else 0,
            strong_features,
            is_quad,
            int(result.inliers),
            float(result.confidence),
        )


def _resolve_relative(raw: Any, base: Path) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw.strip()).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base / candidate).resolve()


# Геометрия печатного листа для quad-fallback


def _decode_reduced(path: Path, factor: int = 4) -> np.ndarray | None:
    """Быстрое чтение большого PNG в 1/factor (для сравнения содержимого)."""
    flag = {2: cv2.IMREAD_REDUCED_COLOR_2, 4: cv2.IMREAD_REDUCED_COLOR_4,
            8: cv2.IMREAD_REDUCED_COLOR_8}.get(int(factor), cv2.IMREAD_COLOR)
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        image = cv2.imdecode(data, flag)
    except (OSError, ValueError, cv2.error):
        return None
    if image is None or getattr(image, "size", 0) == 0:
        return None
    return image


def _small_ncc(first: np.ndarray, second: np.ndarray, size: int = 32) -> float:
    """NCC двух картинок после приведения к size x size (грубая проверка)."""
    try:
        a = cv2.resize(first, (size, size), interpolation=cv2.INTER_AREA)
        b = cv2.resize(second, (size, size), interpolation=cv2.INTER_AREA)
    except cv2.error:
        return -1.0
    if a.ndim == 3:
        a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    if b.ndim == 3:
        b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    a = a.astype(np.float32).ravel()
    b = b.astype(np.float32).ravel()
    a -= a.mean()
    b -= b.mean()
    denom = float(np.sqrt(float((a * a).sum()) * float((b * b).sum())))
    if denom < 1e-6:
        return -1.0
    return float((a * b).sum() / denom)


@lru_cache(maxsize=8)
def _infer_sheet_rect(image_path: str, base_dir: str
                      ) -> tuple[float, float, float, float] | None:
    """Где в координатах эталона лежит физический печатный лист.

    `references.json` ссылается на обрезки листа (`photo_rect_px`): эталон —
    это фото узла, вырезанное из A3. Сам layout обрезка не знает, где он был
    на листе, поэтому ищем рядом (`*/layout.json`) полный планшет, у которого
    `photo_rect_px` совпадает по размеру с эталоном и есть файл `board.image`.
    Содержимое сверяем по NCC, чтобы не привязаться к чужому листу.

    Возвращает (x0, y0, x1, y1) листа в пикселях эталона или None, если
    эталон сам является целым листом (тогда quad работает по умолчанию).
    """
    image_path_p = Path(image_path)
    reference = _decode_reduced(image_path_p, factor=4)
    if reference is None:
        return None
    ref_h, ref_w = reference.shape[:2]
    # Размер эталона в исходных пикселях (мы прочитали в 4 раза меньше).
    ref_w *= 4
    ref_h *= 4

    best: tuple[float, tuple[float, float, float, float], float, float] | None = None
    for layout_path in sorted(Path(base_dir).glob("*/layout.json")):
        try:
            layout = json.loads(layout_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(layout, dict):
            continue
        board = layout.get("board") if isinstance(layout.get("board"), dict) else {}
        rect = layout.get("photo_rect_px")
        board_w = board.get("width_px")
        board_h = board.get("height_px")
        image_name = board.get("image")
        if not (rect and board_w and board_h and isinstance(image_name, str)):
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in rect)
            board_w, board_h = float(board_w), float(board_h)
        except (TypeError, ValueError):
            continue
        rect_w, rect_h = x1 - x0, y1 - y0
        if rect_w <= 0 or rect_h <= 0:
            continue
        # Размер обрезка должен совпадать с эталоном (допуск 2%).
        if abs(rect_w - ref_w) > 0.02 * ref_w or abs(rect_h - ref_h) > 0.02 * ref_h:
            continue
        # photo_rect на весь планшет — это не обрезок, лист уже совпадает.
        if rect_w >= 0.98 * board_w and rect_h >= 0.98 * board_h:
            continue
        board_path = layout_path.parent / image_name
        if not board_path.is_file():
            continue
        board_image = _decode_reduced(board_path, factor=4)
        if board_image is None:
            continue
        board_h_small, board_w_small = board_image.shape[:2]
        sx = board_w_small / board_w
        sy = board_h_small / board_h
        crop = board_image[int(round(y0 * sy)):int(round(y1 * sy)),
                           int(round(x0 * sx)):int(round(x1 * sx))]
        if crop.size == 0:
            continue
        ncc = _small_ncc(reference, crop)
        if ncc < 0.90:
            continue
        candidate = (ncc, (x0, y0, x1, y1), board_w, board_h)
        if best is None or candidate[0] > best[0]:
            best = candidate
        if ncc >= 0.995:
            break
    if best is None:
        return None
    _, (x0, y0, x1, y1), board_w, board_h = best
    # Эталон -> пиксели планшета: bx = x0 + rx * sx. Лист = [0, board_w] x [0, board_h].
    sx = (x1 - x0) / float(ref_w)
    sy = (y1 - y0) / float(ref_h)
    return ((0.0 - x0) / sx, (0.0 - y0) / sy,
            (board_w - x0) / sx, (board_h - y0) / sy)


__all__ = [
    "BoardEntry",
    "BoardRegistry",
    "DEFAULT_GUARD_MARKER_ID",
    "DEFAULT_IDENTITY_DICTIONARY",
    "DEFAULT_STABLE_FRAMES",
    "QUAD_FALLBACK_INLIERS",
    "VisualBoardEntry",
    "VisualBoardRegistry",
    "VisualMatch",
    "detect_identity_markers",
    "layout_board_info",
    "load_board_registry",
]
