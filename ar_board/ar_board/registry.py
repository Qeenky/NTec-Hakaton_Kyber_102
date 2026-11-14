"""Реестр планшетов `boards.json` и самоидентификация по identity-маркеру.

Сервер больше не выбирает геометрию по конфигу: он детектирует маркер
идентификации (id 7, 8, ...), находит планшет в реестре и берёт его layout.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .detect import BoardPose, detect_board, detect_marker_ids
from .layout import load_layout

DEFAULT_BOARDS_PATH = Path(__file__).resolve().parents[1] / "boards.json"


@dataclass(frozen=True)
class BoardEntry:
    """Одна запись реестра: id планшета, маркер идентификации и layout."""

    board_id: str
    identity_marker_id: int
    layout_path: Path
    layout: Dict[str, Any]


@dataclass
class BoardIdentification:
    """Результат самоидентификации: какой планшет и его поза в кадре."""

    board: BoardEntry
    pose: BoardPose
    identity_marker_id: int

    @property
    def board_id(self) -> str:
        return self.board.board_id

    @property
    def layout(self) -> Dict[str, Any]:
        return self.board.layout


def load_boards(path: str | Path | None = None) -> List[BoardEntry]:
    """Читает реестр планшетов и загружает layout каждого.

    Пути `layout` в boards.json трактуются относительно самого файла реестра.
    """
    registry_path = Path(path) if path is not None else DEFAULT_BOARDS_PATH
    if not registry_path.is_file():
        raise FileNotFoundError(f"Реестр планшетов не найден: {registry_path}")
    with registry_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    raw_boards = data.get("boards") if isinstance(data, dict) else data
    if not isinstance(raw_boards, list) or not raw_boards:
        raise ValueError(f"boards.json пуст или повреждён: {registry_path}")

    base = registry_path.resolve().parent
    entries: List[BoardEntry] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for raw in raw_boards:
        try:
            board_id = str(raw["board_id"])
            identity_id = int(raw["identity_marker_id"])
            layout_ref = str(raw["layout"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Некорректная запись boards.json: {raw!r}") from exc
        if board_id in seen_names:
            raise ValueError(f"Дублируется board_id={board_id!r} в {registry_path}")
        if identity_id in seen_ids:
            raise ValueError(f"Дублируется identity_marker_id={identity_id} в {registry_path}")
        layout_path = Path(layout_ref)
        if not layout_path.is_absolute():
            layout_path = base / layout_path
        layout = load_layout(layout_path)
        marker_ids = {int(m["id"]) for m in layout.get("markers", [])}
        if identity_id not in marker_ids:
            raise ValueError(
                f"В {layout_path} нет маркера identity_marker_id={identity_id}")
        entries.append(BoardEntry(board_id, identity_id, layout_path, layout))
        seen_ids.add(identity_id)
        seen_names.add(board_id)
    return entries


def identify_board(frame_bgr: np.ndarray,
                   boards: Optional[Sequence[BoardEntry]] = None,
                   boards_path: str | Path | None = None) -> Optional[BoardIdentification]:
    """Определяет планшет по маркеру идентификации.

    Детектирует все ArUco-маркеры кадра, оставляет реестровые identity-id,
    затем для каждого кандидата строит позу по его layout. Возвращает лучший
    результат (минимальная ошибка репроекции) или None.
    """
    entries = list(boards) if boards is not None else load_boards(boards_path)
    if not entries:
        return None
    visible = detect_marker_ids(frame_bgr)
    if not visible:
        return None
    by_identity = {entry.identity_marker_id: entry for entry in entries}
    candidates = [by_identity[mid] for mid in visible if mid in by_identity]
    if not candidates:
        return None

    best: Optional[BoardIdentification] = None
    best_error = float("inf")
    for entry in candidates:
        pose = detect_board(frame_bgr, entry.layout)
        if pose is None or not pose.is_valid:
            continue
        if pose.identity_marker_id != entry.identity_marker_id:
            continue
        error = float(pose.reprojection_error)
        if best is None or error < best_error:
            best = BoardIdentification(entry, pose, entry.identity_marker_id)
            best_error = error
    return best
