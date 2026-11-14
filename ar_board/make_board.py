"""Генератор печатного планшета A3: фото, QR и layout.json.

Маркеры ArUco (4 угловых + identity + guard) рисуются по умолчанию;
`--no-markers` собирает «чистый» лист без них — привязка тогда по visual_align.
`--no-qr` убирает QR с листа: метка печатается/показывается отдельно
(`qr/PUMP-001.png`), планшет остаётся только оборудованием.

Пример:
    python make_board.py --photo pump.jpg --out out/ --equipment PUMP-001
    python make_board.py --photo pump.jpg --out out_real --no-markers --no-qr --board-id clean
"""
from __future__ import annotations

import argparse
import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from ar_board import spec
from ar_board.layout import (components_payload, load_layout, point_in_polygon,
                             polygon_centroid, save_layout)

Color = Tuple[int, int, int]

QUIET: Color = (255, 255, 255)
INK: Color = (40, 48, 58)
MUTED: Color = (108, 118, 130)
GRID: Color = (236, 241, 247)


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Arial/Segoe/DejaVu с кириллицей; fallback — встроенный шрифт."""
    names = ("arialbd.ttf", "segoeuib.ttf") if bold else ("arial.ttf", "segoeui.ttf")
    candidates = [Path("C:/Windows/Fonts") / names[0], Path("C:/Windows/Fonts") / names[1]]
    dejavu = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    candidates += [Path("/usr/share/fonts/truetype/dejavu") / dejavu,
                   Path("/Library/Fonts") / ("Arial Bold.ttf" if bold else "Arial.ttf")]
    for candidate in candidates:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _gradient(size: Tuple[int, int], start: Color, end: Color,
              horizontal: bool = False) -> Image.Image:
    """Линейный градиент (для объёма корпуса)."""
    width, height = size
    if horizontal:
        t = np.tile(np.linspace(0.0, 1.0, width, dtype=np.float32), (height, 1))
    else:
        t = np.tile(np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None], (1, width))
    t = t[..., None]
    arr = np.array(start, np.float32) * (1.0 - t) + np.array(end, np.float32) * t
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


class _Painter:
    """Рисует схему в «базовых» координатах, масштабируя в пиксели печати."""

    def __init__(self, image: Image.Image, k: float) -> None:
        self.img = image
        self.k = k
        self.d = ImageDraw.Draw(image)

    def _box(self, box: Sequence[float]) -> Tuple[int, int, int, int]:
        return tuple(int(round(v * self.k)) for v in box)  # type: ignore[return-value]

    def _points(self, points: Sequence[Sequence[float]]) -> list[Tuple[int, int]]:
        return [(int(round(x * self.k)), int(round(y * self.k))) for x, y in points]

    def _w(self, width: float) -> int:
        return max(1, int(round(width * self.k)))

    def line(self, xy: Sequence[Sequence[float]], fill: Color, width: float = 1) -> None:
        self.d.line(self._points(xy), fill=fill, width=self._w(width))

    def rect(self, box: Sequence[float], fill: Optional[Color] = None,
             outline: Optional[Color] = None, width: float = 1) -> None:
        self.d.rectangle(self._box(box), fill=fill, outline=outline, width=self._w(width))

    def rrect(self, box: Sequence[float], radius: float = 0, fill: Optional[Color] = None,
              outline: Optional[Color] = None, width: float = 1) -> None:
        self.d.rounded_rectangle(self._box(box), radius=radius * self.k, fill=fill,
                                 outline=outline, width=self._w(width))

    def ellipse(self, box: Sequence[float], fill: Optional[Color] = None,
                outline: Optional[Color] = None, width: float = 1) -> None:
        self.d.ellipse(self._box(box), fill=fill, outline=outline, width=self._w(width))

    def arc(self, box: Sequence[float], start: float, end: float,
            fill: Color, width: float = 1) -> None:
        self.d.arc(self._box(box), start=start, end=end, fill=fill, width=self._w(width))

    def text(self, xy: Sequence[float], text: str, size: float, fill: Color,
             anchor: str = "la", bold: bool = False) -> None:
        font = _font(max(6, int(round(size * self.k))), bold)
        self.d.text((xy[0] * self.k, xy[1] * self.k), text, font=font, fill=fill, anchor=anchor)

    def gradient(self, box: Sequence[float], start: Color, end: Color,
                 radius: float = 0, horizontal: bool = False) -> None:
        x0, y0, x1, y1 = self._box(box)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        grad = _gradient((x1 - x0, y1 - y0), start, end, horizontal)
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        md = ImageDraw.Draw(mask)
        if radius <= 0 or radius * self.k >= min(x1 - x0, y1 - y0) / 2:
            md.ellipse((0, 0, x1 - x0 - 1, y1 - y0 - 1), fill=255)
        else:
            md.rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1),
                                 radius=radius * self.k, fill=255)
        self.img.paste(grad, (x0, y0), mask)


def draw_placeholder_pump(target_size: Tuple[int, int]) -> Tuple[Image.Image, Dict[str, Tuple[float, float, float, float]]]:
    """Схематичный, но аккуратный макет насосного агрегата.

    Возвращает изображение и зоны узлов в координатах картинки (x0,y0,x1,y1).
    """
    base_w, base_h = 1440.0, 480.0
    img = Image.new("RGB", target_size, (247, 249, 252))
    k = target_size[0] / base_w
    p = _Painter(img, k)

    for x in range(0, int(base_w), 48):
        p.line(((x, 0), (x, base_h)), GRID, 1)
    for y in range(0, int(base_h), 48):
        p.line(((0, y), (base_w, y)), GRID, 1)

    shadow = Image.new("L", target_size, 0)
    ImageDraw.Draw(shadow).ellipse(p._box((80, 372, 1390, 452)), fill=90)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, int(14 * k))))
    img.paste(Image.new("RGB", target_size, (199, 208, 218)), (0, 0), shadow)

    # Рама-основание
    p.rrect((70, 348, 1380, 404), radius=12, fill=(58, 67, 78), outline=(30, 36, 44), width=3)
    p.rrect((80, 352, 1370, 362), radius=5, fill=(84, 95, 108))
    for bx in range(120, 1360, 120):
        p.ellipse((bx - 6, 370, bx + 6, 382), fill=(150, 160, 170), outline=(28, 34, 42))

    # Электродвигатель
    p.gradient((136, 212, 612, 356), (104, 115, 128), (38, 44, 52), radius=22)
    for x in range(166, 590, 18):
        p.line(((x, 226), (x, 344)), (62, 71, 82), 4)
        p.line(((x + 3, 226), (x + 3, 344)), (118, 128, 140), 1)
    p.gradient((136, 212, 612, 238), (122, 134, 148), (92, 102, 114), radius=12)
    p.gradient((136, 338, 612, 356), (52, 60, 70), (32, 37, 44), radius=10)
    p.rrect((136, 212, 612, 356), radius=22, outline=(26, 32, 40), width=3)
    p.ellipse((128, 212, 184, 356), fill=(50, 57, 66), outline=(24, 30, 38), width=3)
    p.ellipse((146, 232, 172, 336), fill=(74, 84, 96), outline=(40, 48, 58), width=2)
    p.ellipse((584, 228, 620, 342), fill=(58, 66, 76), outline=(26, 32, 40), width=3)
    p.rrect((264, 160, 472, 214), radius=10, fill=(59, 68, 79), outline=(28, 34, 42), width=3)
    p.rrect((282, 172, 454, 198), radius=5, fill=(43, 50, 59))
    for cx in (296, 440):
        p.ellipse((cx - 5, 178, cx + 5, 188), fill=(142, 154, 166))

    # Кожух муфты
    p.rect((600, 282, 664, 300), fill=(122, 130, 138), outline=(60, 66, 74))
    guard = (604, 238, 748, 364)
    gx0, gy0, gx1, gy1 = guard
    stripes = Image.new("RGB", (int((gx1 - gx0) * k), int((gy1 - gy0) * k)), (208, 163, 48))
    sd = ImageDraw.Draw(stripes)
    step = int(36 * k)
    for i in range(-2, int((gx1 - gx0) / 36) + 3):
        x0 = int(i * step)
        sd.line([(x0, int((gy1 - gy0) * k)), (x0 + int(70 * k), 0)],
                fill=(44, 46, 50), width=max(2, int(13 * k)))
    gmask = Image.new("L", stripes.size, 0)
    ImageDraw.Draw(gmask).rounded_rectangle((0, 0, stripes.size[0] - 1, stripes.size[1] - 1),
                                            radius=24 * k, fill=255)
    img.paste(stripes, p._box(guard)[:2], gmask)
    p.rrect(guard, radius=24, outline=(62, 60, 56), width=3)

    # Насос (улитка) + напорный патрубок
    cx, cy, radius = 862.0, 300.0, 106.0
    p.gradient((cx - radius, cy - radius, cx + radius, cy + radius),
               (128, 166, 190), (38, 68, 90), radius=-1)
    p.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
              outline=(22, 38, 50), width=3)
    p.arc((cx - radius + 10, cy - radius + 10, cx + radius - 10, cy + radius - 10),
          start=205, end=340, fill=(162, 191, 209), width=4)
    p.gradient((826, 176, 898, 208), (124, 144, 162), (66, 84, 100), radius=6)
    for flange_y in (148, 178):
        p.rrect((812, flange_y, 912, flange_y + 22), radius=3,
                fill=(96, 110, 124), outline=(32, 42, 52), width=2)
        for bx in (824, 898):
            p.ellipse((bx - 4, flange_y + 7, bx + 4, flange_y + 15),
                      fill=(172, 184, 196), outline=(50, 60, 72))
    p.gradient((838, 92, 886, 152), (152, 168, 182), (92, 106, 120), horizontal=True, radius=4)
    p.rect((838, 92, 886, 152), outline=(62, 74, 86), width=2)
    p.rrect((830, 86, 894, 100), radius=4, fill=(98, 112, 126), outline=(52, 62, 74), width=2)

    # Передняя крышка с болтами
    p.gradient((800, 238, 924, 362), (130, 164, 184), (62, 94, 114), radius=-1)
    p.ellipse((800, 238, 924, 362), outline=(30, 50, 64), width=3)
    for angle in range(0, 360, 45):
        a = math.radians(angle)
        bx, by = cx + 48 * math.cos(a), cy + 48 * math.sin(a)
        p.ellipse((bx - 5, by - 5, bx + 5, by + 5), fill=(168, 182, 194), outline=(46, 60, 72))
    p.ellipse((842, 280, 882, 320), fill=(46, 70, 88), outline=(24, 40, 52), width=2)
    p.ellipse((852, 290, 872, 310), fill=(66, 92, 110))

    # Манометр
    p.line(((884, 112), (944, 100)), (110, 124, 138), 7)
    p.ellipse((942, 58, 1026, 142), fill=(58, 68, 80), outline=(28, 36, 46), width=3)
    p.ellipse((950, 66, 1018, 134), fill=(250, 251, 248), outline=(70, 80, 92), width=2)
    gcx, gcy = 984.0, 100.0
    for tick in range(0, 360, 30):
        a = math.radians(tick)
        r1 = 33 if tick % 90 else 36
        p.line(((gcx + 27 * math.cos(a), gcy + 27 * math.sin(a)),
                (gcx + r1 * math.cos(a), gcy + r1 * math.sin(a))),
               (52, 62, 74), 2 if tick % 90 else 3)
    needle = math.radians(-55)
    p.line(((gcx, gcy), (gcx + 24 * math.cos(needle), gcy + 24 * math.sin(needle))),
           (196, 46, 46), 3)
    p.ellipse((gcx - 5, gcy - 5, gcx + 5, gcy + 5), fill=(40, 46, 54))

    # Всасывающий патрубок
    p.gradient((960, 300, 1248, 346), (150, 166, 180), (92, 106, 120), radius=10)
    p.rrect((960, 300, 1248, 346), radius=10, outline=(66, 78, 90), width=2)
    p.rrect((1188, 288, 1208, 358), radius=3, fill=(92, 106, 120), outline=(40, 50, 60), width=2)
    p.rrect((1206, 278, 1246, 368), radius=4, fill=(96, 110, 124), outline=(40, 50, 60), width=2)
    for bx, by in ((1216, 292), (1236, 292), (1216, 354), (1236, 354)):
        p.ellipse((bx - 4, by - 4, bx + 4, by + 4), fill=(172, 184, 196), outline=(50, 60, 72))

    # Шильдик на раме
    p.rrect((1108, 360, 1298, 400), radius=5, fill=(238, 241, 244),
            outline=(120, 132, 144), width=2)
    p.text((1203, 370), "ДЕМО", 22, (40, 48, 58), anchor="mm", bold=True)
    p.text((1203, 390), "PUMP-001", 13, (92, 102, 114), anchor="mm")

    def note(text: str, xy: Sequence[float], target: Sequence[float], anchor: str = "lm") -> None:
        p.line(((xy[0], xy[1]), (target[0], target[1])), (126, 138, 152), 2)
        p.ellipse((target[0] - 4, target[1] - 4, target[0] + 4, target[1] + 4),
                  fill=(96, 110, 124))
        p.text(xy, text, 24, (46, 56, 68), anchor=anchor)

    note("Фланцевое соединение", (700, 120), (800, 160), anchor="rm")
    note("Манометр", (1012, 40), (1000, 58), anchor="mm")
    note("Электродвигатель", (168, 440), (330, 360))
    note("Кожух муфты", (536, 440), (626, 364))
    note("Насос", (742, 440), (804, 404))

    zones: Dict[str, Tuple[float, float, float, float]] = {
        "motor": (132, 156, 618, 360),
        "pump": (748, 210, 978, 404),
        "coupling_guard": (596, 232, 754, 370),
        "flange": (806, 142, 918, 208),
        "manometer": (944, 52, 1030, 146),
    }
    return img, zones


def _fit_rect(src: Tuple[int, int], box: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Вписывает картинку в box с сохранением пропорций и центрированием."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    scale = min(bw / src[0], bh / src[1])
    w, h = max(1, int(round(src[0] * scale))), max(1, int(round(src[1] * scale)))
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    return cx - w // 2, cy - h // 2, cx - w // 2 + w, cy - h // 2 + h


def _qr_image(data: str, size_px: int) -> Optional[Image.Image]:
    """QR без внешних картинок; None, если qrcode не установлен."""
    try:
        import qrcode

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=10, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        return image.resize((size_px, size_px), Image.NEAREST)
    except Exception:
        return None


def _marker_image(marker_id: int, side_px: int) -> Image.Image:
    """Чёрный ArUco-квадрат side_px (без quiet zone — он на белой бумаге)."""
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, spec.ARUCO_DICT))
    if hasattr(cv2.aruco, "generateImageMarker"):
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, side_px)
    else:  # OpenCV < 4.7
        marker = cv2.aruco.drawMarker(dictionary, marker_id, side_px)
    if marker is None:
        raise RuntimeError(f"Не удалось сгенерировать маркер id={marker_id}")
    return Image.fromarray(marker, mode="L").convert("RGB")


def _export_pdf(png: Path, pdf: Path, title: str) -> bool:
    """A3 landscape PDF с растром 300 dpi (если reportlab доступен)."""
    try:
        from reportlab.lib.pagesizes import A3, landscape
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas as pdf_canvas
    except Exception:
        return False
    page = landscape(A3)
    c = pdf_canvas.Canvas(str(pdf), pagesize=page)
    c.setTitle(title)
    c.setAuthor("ar_board")
    c.setSubject("AR board A3 300 dpi")
    c.drawImage(ImageReader(str(png)), 0, 0, width=page[0], height=page[1])
    c.showPage()
    c.save()
    return True


def _read_existing_layout(path: Path) -> Optional[Dict[str, Any]]:
    """Старый layout.json, если он есть: зоны узлов при пересборке не теряем."""
    if not path.is_file():
        return None
    try:
        return load_layout(path)
    except (OSError, ValueError):
        return None


def _component_polygon(layout: Optional[Dict[str, Any]],
                       component_id: str) -> Optional[list[list[float]]]:
    """Полигон узла из layout.json в пикселях планшета."""
    if not layout:
        return None
    for comp in layout.get("components", []):
        if comp.get("component_id") == component_id:
            poly = comp.get("polygon_px") or []
            if len(poly) >= 3:
                return [[float(x), float(y)] for x, y in poly]
    return None


def _guard_target(polygon: Optional[list[list[float]]],
                  photo_rect: Sequence[float],
                  zones: Dict[str, Tuple[float, float, float, float]]
                  ) -> Tuple[Tuple[float, float], Optional[list[list[float]]], str]:
    """Куда ставить маркер id 4: центр coupling_guard или центр фото."""
    poly = polygon
    if poly is None and "coupling_guard" in zones:
        x0, y0, x1, y1 = zones["coupling_guard"]
        poly = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    if poly is not None:
        center = polygon_centroid(poly)
        if not point_in_polygon(center, poly):
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            center = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
        return center, poly, "coupling_guard"
    center = ((photo_rect[0] + photo_rect[2]) / 2.0,
              (photo_rect[1] + photo_rect[3]) / 2.0)
    return center, None, "photo_center"


def _guard_size_mm(polygon: Optional[list[list[float]]]) -> float:
    """Подбирает сторону маркера так, чтобы он с quiet zone влез в зону."""
    if not polygon:
        return spec.GUARD_MARKER_SIZE_MM
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    side_mm = min(max(xs) - min(xs), max(ys) - min(ys)) / spec.PX_PER_MM
    fit = side_mm - 2.0 * spec.GUARD_MARKER_QUIET_MM - 2.0
    return float(max(10.0, min(spec.GUARD_MARKER_SIZE_MM, fit)))


def generate_board(out_dir: str | Path, equipment_id: str = "PUMP-001",
                   photo: Optional[str | Path] = None,
                   make_pdf: bool = True,
                   title_text: Optional[str] = None,
                   identity_id: Optional[int] = None,
                   guard_marker: bool = False,
                   board_id: Optional[str] = None,
                   no_markers: bool = False,
                   no_qr: bool = False) -> Dict[str, Any]:
    """Собирает PNG/PDF планшета и layout.json. Возвращает layout.

    `identity_id` — ArUco id маркера идентификации планшета (в шапке),
    `guard_marker` — маркер id 4 внутри зоны кожуха муфты. При пересборке
    существующие `components` сохраняются, чтобы не потерять разметку.

    `no_markers` — «чистый» лист: ни угловых ArUco (id 0–3), ни identity-,
    ни guard-маркера. Остаются фото, заголовок и QR оборудования. Привязка
    тогда идёт по признакам сцены (`visual_align`), а не по маркерам.
    Флаг сильнее `identity_id`/`guard_marker`: маркеры просто не рисуются.

    `no_qr` — не печатать QR оборудования (и его подпись) в шапке: карточка
    должна открываться только по отдельной QR-метке, а планшет остаётся
    «только оборудованием» (фото + заголовок). Зоны узлов сохраняются.
    """
    if no_markers:
        identity_id = None
        guard_marker = False
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    width, height = spec.BOARD_W_PX, spec.BOARD_H_PX

    existing = _read_existing_layout(out / "layout.json")
    existing_components = list((existing or {}).get("components") or [])
    if title_text is None and existing:
        title_text = (existing.get("board") or {}).get("title")

    if identity_id is not None:
        if not 0 <= int(identity_id) < 50:
            raise ValueError("identity-id должен быть в диапазоне 0..49 (DICT_4X4_50)")
        if int(identity_id) in spec.CORNER_MARKER_IDS or int(identity_id) == spec.GUARD_MARKER_ID:
            raise ValueError("identity-id не должен совпадать с угловыми маркерами 0..3 "
                             "и маркером кожуха 4")
    if board_id is None and (identity_id is not None or guard_marker):
        board_id = "clean" if guard_marker else "defect"

    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # Поля, рамка, шапка (рамка на 4 мм — вне quiet zone маркеров)
    draw.rectangle((spec.mm_to_px(4), spec.mm_to_px(4),
                    width - spec.mm_to_px(4), height - spec.mm_to_px(4)),
                   outline=(214, 221, 230), width=2)
    draw.line((spec.mm_to_px(90), spec.mm_to_px(78), spec.mm_to_px(278), spec.mm_to_px(78)),
              fill=(222, 228, 235), width=2)

    title = title_text or f"Насосный агрегат · {equipment_id}"
    # Справа от подписи оставляем место под маркер идентификации.
    header_right_mm = 228.0 if identity_id is not None else 275.0
    max_title_w = spec.mm_to_px(header_right_mm) - spec.mm_to_px(90)
    title_font = _fit_font(draw, title, max_title_w, int(spec.mm_to_px(8.2)), bold=True)
    draw.text((spec.mm_to_px(90), spec.mm_to_px(33)), title, font=title_font,
              fill=(28, 36, 46), anchor="lm")

    subtitle = ("AR-планшет · A3 · 300 dpi · привязка по признакам сцены · печать 100%"
                if no_markers else
                "AR-планшет · A3 · 300 dpi · ArUco DICT_4X4_50 id 0–3 · печать 100%")
    sub_font = _fit_font(draw, subtitle, max_title_w, int(spec.mm_to_px(3.4)))
    draw.text((spec.mm_to_px(90), spec.mm_to_px(58)), subtitle, font=sub_font,
              fill=MUTED, anchor="lm")

    # QR с идентификатором оборудования (на планшетах боевой сцены — не печатаем:
    # карточка открывается только по отдельной QR-метке, см. qr/PUMP-001.png).
    if not no_qr:
        qr_size = spec.mm_to_px(30)
        qr = _qr_image(equipment_id, qr_size)
        qr_x, qr_y = spec.mm_to_px(285), spec.mm_to_px(14)
        if qr is not None:
            canvas.paste(qr, (qr_x, qr_y))
        else:
            draw.rectangle((qr_x, qr_y, qr_x + qr_size, qr_y + qr_size), outline=MUTED, width=2)
            draw.text((qr_x + qr_size // 2, qr_y + qr_size // 2), "QR", font=_font(40),
                      fill=MUTED, anchor="mm")
        draw.text((qr_x + qr_size // 2, spec.mm_to_px(49)), equipment_id,
                  font=_font(int(spec.mm_to_px(2.6))), fill=MUTED, anchor="mm")

    markers: list[Dict[str, Any]] = []
    if identity_id is not None:
        id_corners = spec.identity_marker_corners_px()
        (ix0, iy0), (ix1, iy1) = id_corners[0], id_corners[2]
        quiet = spec.mm_to_px(spec.IDENTITY_MARKER_QUIET_MM)
        draw.rectangle((ix0 - quiet, iy0 - quiet, ix1 + quiet, iy1 + quiet),
                       fill=(255, 255, 255))
        canvas.paste(_marker_image(int(identity_id), ix1 - ix0), (ix0, iy0))
        markers.append({
            "id": int(identity_id),
            "role": "identity",
            "size_mm": spec.IDENTITY_MARKER_SIZE_MM,
            "quiet_zone_mm": spec.IDENTITY_MARKER_QUIET_MM,
            "corners_px": id_corners,
            "corners_mm": spec.identity_marker_corners_mm(),
        })

    # Фото или сгенерированная схема
    photo_box = (spec.mm_to_px(spec.PHOTO_BOX_MM[0]), spec.mm_to_px(spec.PHOTO_BOX_MM[1]),
                 spec.mm_to_px(spec.PHOTO_BOX_MM[2]), spec.mm_to_px(spec.PHOTO_BOX_MM[3]))
    zones: Dict[str, Tuple[float, float, float, float]] = {}
    if photo is not None:
        photo_path = Path(photo)
        if not photo_path.is_file():
            raise FileNotFoundError(f"Фото не найдено: {photo_path}")
        source = ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB")
        rect = _fit_rect(source.size, photo_box)
        canvas.paste(source.resize((rect[2] - rect[0], rect[3] - rect[1]), Image.LANCZOS),
                     (rect[0], rect[1]))
    else:
        rect = photo_box
        placeholder, zones_base = draw_placeholder_pump((rect[2] - rect[0], rect[3] - rect[1]))
        canvas.paste(placeholder, (rect[0], rect[1]))
        k = (rect[2] - rect[0]) / 1440.0
        zones = {cid: (rect[0] + box[0] * k, rect[1] + box[1] * k,
                       rect[0] + box[2] * k, rect[1] + box[3] * k)
                 for cid, box in zones_base.items()}

    # Маркер id 4 внутри зоны кожуха муфты (или в центре фото, если зоны нет).
    guard_zone: Optional[str] = None
    guard_center: Optional[Tuple[float, float]] = None
    if guard_marker:
        guard_center, guard_poly, guard_zone = _guard_target(
            _component_polygon(existing, "coupling_guard"), rect, zones)
        size_mm = _guard_size_mm(guard_poly)
        side = spec.mm_to_px(size_mm)
        quiet = spec.mm_to_px(spec.GUARD_MARKER_QUIET_MM)
        gx0 = int(round(guard_center[0] - side / 2.0))
        gy0 = int(round(guard_center[1] - side / 2.0))
        draw.rectangle((gx0 - quiet, gy0 - quiet, gx0 + side + quiet, gy0 + side + quiet),
                       fill=(255, 255, 255))
        canvas.paste(_marker_image(spec.GUARD_MARKER_ID, side), (gx0, gy0))
        g_corners = [[gx0, gy0], [gx0 + side, gy0],
                     [gx0 + side, gy0 + side], [gx0, gy0 + side]]
        g_origin_mm = (gx0 / spec.PX_PER_MM, gy0 / spec.PX_PER_MM)
        markers.append({
            "id": spec.GUARD_MARKER_ID,
            "role": "guard",
            "size_mm": round(size_mm, 2),
            "quiet_zone_mm": spec.GUARD_MARKER_QUIET_MM,
            "corners_px": g_corners,
            "corners_mm": spec.box_corners_mm(g_origin_mm, size_mm),
            "zone": guard_zone,
            "center_px": [round(guard_center[0], 2), round(guard_center[1], 2)],
        })

    # 4 ArUco-маркера по углам (id 0..3), без перекрытия quiet zone.
    # В режиме --no-markers лист остаётся чистым: фото, заголовок, QR.
    for marker_id in ([] if no_markers else sorted(spec.MARKER_ORIGIN_MM)):
        corners = spec.marker_corners_px(marker_id)
        x0, y0 = corners[0]
        x1, y1 = corners[2]
        side = x1 - x0
        canvas.paste(_marker_image(marker_id, side), (x0, y0))
        markers.append({
            "id": marker_id,
            "role": "corner",
            "size_mm": spec.MARKER_SIZE_MM,
            "quiet_zone_mm": spec.QUIET_ZONE_MM,
            "corners_px": corners,
            "corners_mm": spec.marker_corners_mm(marker_id),
        })

    # Подвал
    foot_font = _font(int(spec.mm_to_px(2.7)))
    extra_ids = ([int(identity_id)] if identity_id is not None else []) + \
                ([spec.GUARD_MARKER_ID] if guard_marker else [])
    if no_markers:
        footer_line = "Планшет без ArUco-маркеров · привязка по признакам сцены (visual_align)"
    else:
        ids_text = "0–3" + (", " + ", ".join(str(i) for i in sorted(extra_ids))
                            if extra_ids else "")
        sizes = ["60"] + (["40"] if identity_id is not None else []) + \
                (["30"] if guard_marker else [])
        footer_line = (f"Маркеры ArUco DICT_4X4_50 · id {ids_text} · {'/'.join(sizes)} мм · "
                       "quiet zone 5–10 мм")
    draw.text((spec.mm_to_px(225), spec.mm_to_px(232)), footer_line,
              font=foot_font, fill=(74, 84, 96), anchor="mm")
    draw.text((spec.mm_to_px(225), spec.mm_to_px(246)),
              "Разметка зон: python annotate.py --layout out/layout.json",
              font=foot_font, fill=MUTED, anchor="mm")

    png_path = out / "board_A3.png"
    canvas.save(png_path, dpi=(spec.DPI, spec.DPI), optimize=True)
    pdf_path = out / "board_A3.pdf"
    pdf_ok = make_pdf and _export_pdf(png_path, pdf_path,
                                      f"AR board {equipment_id}")

    markers.sort(key=lambda marker: marker["id"])
    layout: Dict[str, Any] = {
        "version": 1,
        "equipment_id": equipment_id,
        "board": {
            "id": board_id,
            "has_markers": not no_markers,
            "has_qr": not no_qr,
            "identity_marker_id": int(identity_id) if identity_id is not None else None,
            "guard_marker_id": spec.GUARD_MARKER_ID if guard_marker else None,
            "guard_marker_zone": guard_zone,
            "guard_marker_center_px": ([round(guard_center[0], 2), round(guard_center[1], 2)]
                                       if guard_center is not None else None),
            "title": title,
            "width_px": width,
            "height_px": height,
            "width_mm": spec.PAGE_W_MM,
            "height_mm": spec.PAGE_H_MM,
            "dpi": spec.DPI,
            "px_per_mm": round(spec.PX_PER_MM, 6),
            "image": png_path.name,
            "pdf": pdf_path.name if pdf_ok else None,
        },
        "aruco": (None if no_markers else
                  {"dictionary": spec.ARUCO_DICT,
                   "ids": sorted(marker["id"] for marker in markers)}),
        "markers": markers,
        "photo_rect_px": list(rect),
        "components": [],
    }
    if no_markers:
        # Привязка теперь по признакам сцены — маркеров на листе нет.
        layout["alignment"] = "visual_align"
    if existing_components:
        # Пересборка планшета не должна сбрасывать уже размеченные зоны.
        layout["components"] = existing_components
    elif zones:
        layout["components"] = components_payload(layout, zones)
    save_layout(layout, out / "layout.json")
    return layout


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int,
              size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Уменьшает шрифт, пока строка не влезет в max_width."""
    while size > 14:
        font = _font(size, bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 2
    return _font(size, bold)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python make_board.py",
        description="Генерация печатного A3-планшета (ArUco — опционально, --no-markers).",
    )
    parser.add_argument("--photo", help="фото агрегата; без него рисуется схема")
    parser.add_argument("--out", default="out", help="папка для board_A3.png/.pdf/layout.json")
    parser.add_argument("--equipment", default="PUMP-001", help="идентификатор оборудования")
    parser.add_argument("--no-pdf", action="store_true", help="не создавать PDF")
    parser.add_argument("--title", default=None,
                        help="заголовок на планшете (по умолчанию «Насосный агрегат · ID»)")
    parser.add_argument("--identity-id", type=int, default=None,
                        help="ArUco id маркера идентификации планшета в шапке (0..49, "
                             "кроме 0..4), ~40 мм")
    parser.add_argument("--guard-marker", action="store_true",
                        help="маркер id 4 внутри зоны coupling_guard (кожух муфты)")
    parser.add_argument("--board-id", default=None,
                        help="id планшета clean|defect (по умолчанию: guard -> clean, "
                             "без guard -> defect)")
    parser.add_argument("--no-markers", action="store_true",
                        help="планшет без ArUco: без угловых id 0–3, identity и guard; "
                             "остаются фото, заголовок и QR (привязка по visual_align)")
    parser.add_argument("--no-qr", action="store_true",
                        help="не печатать QR оборудования и его подпись: метка выводится "
                             "отдельно (qr/PUMP-001.png), на листе остаются фото и заголовок")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        layout = generate_board(args.out, args.equipment, args.photo,
                                make_pdf=not args.no_pdf,
                                title_text=args.title,
                                identity_id=args.identity_id,
                                guard_marker=args.guard_marker,
                                board_id=args.board_id,
                                no_markers=args.no_markers,
                                no_qr=args.no_qr)
    except (OSError, ValueError) as exc:
        print(f"Ошибка генерации: {exc}", file=sys.stderr)
        return 2
    board = layout["board"]
    out = Path(args.out)
    print(f"Планшет: {out / 'board_A3.png'}  ({board['width_px']}x{board['height_px']}, "
          f"{board['dpi']} dpi)")
    if board.get("pdf"):
        print(f"PDF:     {out / board['pdf']}")
    print(f"JSON:    {out / 'layout.json'}  (маркеров: {len(layout['markers'])}, "
          f"зон: {len(layout['components'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
