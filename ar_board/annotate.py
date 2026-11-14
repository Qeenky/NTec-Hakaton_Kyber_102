"""Разметка зон узлов на планшете: 5 прямоугольников мышью.

Клавиши: 1..5 — узел, ЛКМ-протяжка — бокс, s — сохранить,
r — сбросить текущий, q — выход с сохранением, Esc — выход без сохранения.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ar_board.io_utils import imread
from ar_board.layout import (COMPONENT_ORDER, components_payload, layout_paths,
                             load_layout, save_layout)

WINDOW = "ar_board annotate"
COLORS = [(60, 180, 255), (90, 210, 120), (255, 190, 80), (120, 150, 255), (230, 120, 240)]

Box = Tuple[float, float, float, float]


def _font(size: int = 20) -> ImageFont.FreeTypeFont:
    """Arial/Segoe/DejaVu для кириллических подписей; иначе встроенный."""
    for name in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/Library/Fonts/Arial.ttf"):
        if Path(name).is_file():
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_cyrillic(frame: np.ndarray,
                   items: Sequence[Tuple[str, Tuple[int, int], Tuple[int, int, int]]]) -> np.ndarray:
    """Подписи с кириллицей поверх кадра (cv2.putText её не умеет)."""
    if not items:
        return frame
    try:
        pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil)
        font = _font(20)
        for text, (x, y), color in items:
            bbox = draw.textbbox((x, y), text, font=font)
            draw.rectangle((bbox[0] - 4, bbox[1] - 3, bbox[2] + 4, bbox[3] + 3),
                           fill=(16, 20, 26))
            draw.text((x, y), text, font=font, fill=(color[2], color[1], color[0]))
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    except (OSError, ValueError):
        return frame


class _State:
    """Состояние разметки: готовые боксы, текущий узел, активная протяжка."""

    def __init__(self, layout: Dict[str, Any]) -> None:
        self.boxes: Dict[str, Box] = {}
        known = {cid for cid, _ in COMPONENT_ORDER}
        for comp in layout.get("components", []):
            cid = comp.get("component_id")
            poly = comp.get("polygon_px") or []
            if cid in known and len(poly) >= 4:
                xs = [float(p[0]) for p in poly]
                ys = [float(p[1]) for p in poly]
                self.boxes[cid] = (min(xs), min(ys), max(xs), max(ys))
        self.current = 0
        self.drag: Optional[list[float]] = None   # координаты окна
        self.dirty = False

    def advance(self) -> None:
        """Переключает на следующий незаполненный узел."""
        for step in range(1, len(COMPONENT_ORDER) + 1):
            index = (self.current + step) % len(COMPONENT_ORDER)
            if COMPONENT_ORDER[index][0] not in self.boxes:
                self.current = index
                return


def _to_board(x: float, y: float, rect: Sequence[int],
              board_w: int, board_h: int) -> Tuple[float, float]:
    """Координаты окна -> координаты планшета."""
    rx, ry, rw, rh = rect
    if rw <= 0 or rh <= 0:
        return 0.0, 0.0
    return ((x - rx) * board_w / rw, (y - ry) * board_h / rh)


def _finish_drag(ctx: Dict[str, Any]) -> None:
    """Фиксирует отпущенную протяжку как бокс текущего узла."""
    state: _State = ctx["state"]
    if state.drag is None:
        return
    rect = list(cv2.getWindowImageRect(WINDOW))
    x0, y0 = _to_board(state.drag[0], state.drag[1], rect, ctx["board_w"], ctx["board_h"])
    x1, y1 = _to_board(state.drag[2], state.drag[3], rect, ctx["board_w"], ctx["board_h"])
    state.drag = None
    if abs(x1 - x0) < 6 or abs(y1 - y0) < 6:
        return
    cid = COMPONENT_ORDER[state.current][0]
    state.boxes[cid] = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    state.dirty = True
    print(f"[{state.current + 1}/5] {cid}: "
          f"{int(state.boxes[cid][0])},{int(state.boxes[cid][1])} - "
          f"{int(state.boxes[cid][2])},{int(state.boxes[cid][3])}")
    state.advance()


def _mouse(event: int, x: int, y: int, _flags: int, ctx: Dict[str, Any]) -> None:
    """ЛКМ-протяжка: старт, обновление и фиксация бокса."""
    state: _State = ctx["state"]
    if event == cv2.EVENT_LBUTTONDOWN:
        state.drag = [float(x), float(y), float(x), float(y)]
    elif event == cv2.EVENT_MOUSEMOVE and state.drag is not None:
        state.drag[2], state.drag[3] = float(x), float(y)
    elif event == cv2.EVENT_LBUTTONUP and state.drag is not None:
        state.drag[2], state.drag[3] = float(x), float(y)
        _finish_drag(ctx)


def _draw(frame: np.ndarray, state: _State, scale_x: float, scale_y: float) -> np.ndarray:
    """Рисует зоны, протяжку и строку подсказок."""
    labels: list[Tuple[str, Tuple[int, int], Tuple[int, int, int]]] = []
    for index, (cid, label) in enumerate(COMPONENT_ORDER):
        box = state.boxes.get(cid)
        if box is None:
            continue
        color = COLORS[index % len(COLORS)]
        p0 = (int(box[0] * scale_x), int(box[1] * scale_y))
        p1 = (int(box[2] * scale_x), int(box[3] * scale_y))
        active = index == state.current
        thickness = 3 if active else 2
        cv2.rectangle(frame, p0, p1, (12, 14, 18), thickness + 2)
        cv2.rectangle(frame, p0, p1, color, thickness)
        text_y = p0[1] - 26 if p0[1] > 64 else p0[1] + 6
        labels.append((f"{index + 1} · {label}", (p0[0] + 4, text_y), color))

    if state.drag is not None:
        p0 = (int(state.drag[0]), int(state.drag[1]))
        p1 = (int(state.drag[2]), int(state.drag[3]))
        cv2.rectangle(frame, p0, p1, (255, 255, 255), 1, cv2.LINE_AA)

    frame = _draw_cyrillic(frame, labels)

    cid = COMPONENT_ORDER[state.current][0]
    status = (f"[{state.current + 1}/5] {cid}  |  zones {len(state.boxes)}/5  |  "
              f"LMB drag, 1-5 select, s save, r reset, q quit")
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 34), (16, 20, 26), -1)
    cv2.putText(frame, status, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.52,
                (235, 235, 235), 1, cv2.LINE_AA)
    return frame


def save_annotations(layout_path: str | Path, layout: Dict[str, Any],
                     boxes: Dict[str, Box]) -> Path:
    """Пишет components (px + norm) в layout.json."""
    payload = dict(layout)
    payload["components"] = components_payload(layout, boxes)
    return save_layout(payload, layout_path)


def _gui_error(details: str) -> str:
    return ("OpenCV-окно недоступно (headless-среда или сборка без GUI). "
            "Разметку нужно выполнять на машине с дисплеем.\n"
            "Альтернатива: вписать polygon_px/polygon_norm в layout.json вручную.\n"
            f"Детали: {details}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python annotate.py",
        description="Разметка 5 зон узлов на планшете (OpenCV).",
    )
    parser.add_argument("--layout", required=True, help="путь к out/layout.json")
    parser.add_argument("--width", type=int, default=1280, help="ширина окна")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    layout_path = Path(args.layout)
    try:
        layout = load_layout(layout_path)
    except (OSError, ValueError) as exc:
        print(f"Ошибка layout: {exc}", file=sys.stderr)
        return 2

    image_path = layout_paths(layout, layout_path)
    image = imread(image_path)
    if image is None:
        print(f"Не найдено изображение планшета: {image_path}", file=sys.stderr)
        return 2
    board_h, board_w = image.shape[:2]

    state = _State(layout)
    ctx = {"state": state, "board_w": board_w, "board_h": board_h}
    view_size = (args.width, max(320, int(round(args.width * board_h / board_w))))
    try:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW, *view_size)
        cv2.setMouseCallback(WINDOW, _mouse, ctx)
        cv2.waitKey(30)
    except cv2.error as exc:
        print(_gui_error(str(exc).splitlines()[0]), file=sys.stderr)
        return 3

    preview: Optional[np.ndarray] = None
    cached: Optional[Tuple[int, int]] = None
    print("Разметка: 1..5 — узел, ЛКМ-протяжка — бокс, s — сохранить, "
          "r — сбросить, q — выход с сохранением.")
    try:
        while True:
            rect = list(cv2.getWindowImageRect(WINDOW))
            if rect[2] <= 0 or rect[3] <= 0:
                rect = [0, 0, view_size[0], view_size[1]]
            view_w, view_h = rect[2], rect[3]
            if cached != (view_w, view_h) or preview is None:
                preview = cv2.resize(image, (view_w, view_h), interpolation=cv2.INTER_AREA)
                cached = (view_w, view_h)

            frame = _draw(preview.copy(), state,
                          view_w / float(board_w), view_h / float(board_h))
            cv2.imshow(WINDOW, frame)

            key = cv2.waitKey(15) & 0xFF
            if key in (ord("q"), ord("Q")):
                if state.dirty:
                    save_annotations(layout_path, layout, state.boxes)
                break
            if key == 27:
                return 0
            if ord("1") <= key <= ord("5"):
                state.current = key - ord("1")
            elif key in (ord("r"), ord("R")):
                cid = COMPONENT_ORDER[state.current][0]
                if state.boxes.pop(cid, None) is not None:
                    state.dirty = True
                    print(f"Сброшено: {cid}")
            elif key in (ord("s"), ord("S")):
                save_annotations(layout_path, layout, state.boxes)
                state.dirty = False
                print(f"Сохранено: {layout_path} (зон: {len(state.boxes)})")

        print(f"Выход. Зон в layout: {len(state.boxes)} -> {layout_path}")
        return 0
    except cv2.error as exc:
        print(_gui_error(str(exc).splitlines()[0]), file=sys.stderr)
        return 3
    finally:
        try:
            cv2.destroyWindow(WINDOW)
        except cv2.error:
            pass
