"""Fallback-привязка по контуру печатного листа.

Когда планшет занимает меньше ~50% кадра, ORB/AKAZE находят слишком мало
признаков и feature-привязка честно отказывает. Но сам печатный лист — это
светлый прямоугольник (A3, соотношение сторон 1.414) на тёмном фоне стенда,
и его контур видно даже на 18% кадра.

Алгоритм (`detect_sheet_quad`):

1. кадр -> grayscale -> уменьшение до `max_side` (по умолчанию 640 px);
2. Otsu-бинаризация по яркости + проверка, что светлый класс — это лист
   (средняя яркость «белого» больше средней яркости «чёрного»);
3. морфология (close заполняет дырки от текста/фото, open убирает мусор);
4. `findContours(RETR_EXTERNAL)` -> `approxPolyDP` (eps = 2% периметра);
5. из четырёхугольников выбирается лучший по площади, прямоугольности,
   заполнению, соотношению сторон и контрасту «лист/фон».

Гомография строится по четырём углам: координаты листа в системе эталона
(`sheet_quad`, см. `aligner.Reference`) -> найденный четырёхугольник в кадре.
Это позволяет работать и с эталоном-обрезком: в layout-обрезке лист занимает
прямоугольник с отрицательными координатами, и всё сходится.

Модуль намеренно не зависит от `aligner`, чтобы не было циклического импорта:
он возвращает геометрию и метрики, а `AlignmentResult` собирает вызывающий.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

import cv2
import numpy as np

# Рабочая длинная сторона кадра для детекции: 512 px хватает, чтобы найти
# контур листа даже на 18% кадра (~72 px), и это дешевле, чем 640/1280.
DEFAULT_QUAD_MAX_SIDE = 512
# A3: 420/297 = 1.414. Для квадратных/других листов ожидание можно переопределить.
DEFAULT_SHEET_ASPECT = 420.0 / 297.0
# Допуск по соотношению сторон (относительный): перспектива до ~25° сжимает
# видимую сторону примерно на 10–15%.
DEFAULT_ASPECT_TOLERANCE = 0.25
DEFAULT_MIN_AREA_RATIO = 0.002   # 0.2% кадра — планшет на 18% даёт ~2.5%
DEFAULT_MAX_AREA_RATIO = 0.96    # весь кадр — это не лист
DEFAULT_MIN_RECTANGULARITY = 0.72
DEFAULT_MIN_FILL = 0.72
DEFAULT_MIN_CONTRAST = 15.0      # средняя яркость внутри минус фон, 0..255
# Сколько крупнейших контуров проверять: лист обычно первый-второй.
DEFAULT_MAX_CANDIDATES = 6


@dataclass
class QuadDetection:
    """Найденный контур листа и его метрики (координаты исходного кадра)."""

    quad: np.ndarray                 # (4, 2) float64: tl, tr, br, bl
    confidence: float                # 0..1, только геометрия (без NCC)
    area_ratio: float                # площадь квада / площадь кадра
    rectangularity: float            # contourArea / площадь minAreaRect
    fill: float                      # доля «листа» (bright mask) внутри квада
    aspect: float                    # длинная/короткая сторона minAreaRect
    contrast: float                  # средняя яркость внутри минус фон
    work_scale: float = 1.0          # во сколько раз кадр уменьшали
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quad": [[round(float(x), 2), round(float(y), 2)] for x, y in self.quad],
            "confidence": round(float(self.confidence), 4),
            "area_ratio": round(float(self.area_ratio), 5),
            "rectangularity": round(float(self.rectangularity), 4),
            "fill": round(float(self.fill), 4),
            "aspect": round(float(self.aspect), 4),
            "contrast": round(float(self.contrast), 2),
            "work_scale": round(float(self.work_scale), 4),
            "reason": self.reason,
        }


# геометрия
def quad_aspect(quad: Sequence[Sequence[float]]) -> float:
    """Отношение длинной стороны четырёхугольника к короткой."""
    points = np.asarray(quad, dtype=np.float64).reshape(-1, 2)
    if len(points) < 3:
        return 0.0
    sides = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    short = float(sides.min())
    return float(sides.max() / max(1e-6, short))


def order_quad(points: Sequence[Sequence[float]]) -> np.ndarray:
    """Упорядочивает 4 точки по кругу: tl, tr, br, bl (стабильно)."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(pts) != 4:
        raise ValueError("нужно ровно 4 точки")
    center = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    ordered = pts[np.argsort(angles)]
    # Поворот так, чтобы первой была самая «левая-верхняя» вершина. Сумма
    # координат устойчивее, чем «самый верхний»: у слегка повёрнутого листа
    # верхняя правая точка может быть на пиксель выше левой.
    start = int(np.argmin(ordered[:, 0] + ordered[:, 1]))
    return np.roll(ordered, -start, axis=0)


def quad_confidence(area_ratio: float, rectangularity: float, fill: float,
                    aspect: float, expected_aspect: float = DEFAULT_SHEET_ASPECT
                    ) -> float:
    """Уверенность 0..1 по площади/прямоугольности/заполнению/пропорциям."""
    rect_term = _clip01((float(rectangularity) - 0.72) / 0.28)
    area_term = _clip01(float(area_ratio) / 0.20)
    fill_term = _clip01((float(fill) - 0.72) / 0.28)
    if aspect <= 0.0 or expected_aspect <= 0.0:
        aspect_term = 0.0
    else:
        deviation = abs(math.log(float(aspect) / float(expected_aspect)))
        aspect_term = float(math.exp(-deviation / 0.22))
    value = (0.30 * rect_term + 0.25 * area_term
             + 0.20 * fill_term + 0.25 * aspect_term)
    return float(_clip01(value))


def _clip01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


# детекция
def _as_bgr(image: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """Минимальная нормализация входа (без импорта aligner: циклический импорт)."""
    if image is None or getattr(image, "size", 0) == 0:
        return None
    array = image
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    if array.ndim == 2:
        return cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
    if array.ndim != 3:
        return None
    if array.shape[2] == 4:
        array = cv2.cvtColor(array, cv2.COLOR_BGRA2BGR)
    elif array.shape[2] != 3:
        return None
    if min(array.shape[:2]) < 16:
        return None
    return array


def _bright_mask(gray: np.ndarray) -> np.ndarray:
    """Otsu-маска светлого листа; при инвертированном контрасте — инверсия."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = float(gray[mask > 0].mean()) if np.any(mask > 0) else 0.0
    dark = float(gray[mask == 0].mean()) if np.any(mask == 0) else 0.0
    if bright < dark:
        mask = cv2.bitwise_not(mask)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    return mask


def detect_sheet_quad(
    frame_bgr: Optional[np.ndarray],
    *,
    expected_aspect: float = DEFAULT_SHEET_ASPECT,
    aspect_tolerance: float = DEFAULT_ASPECT_TOLERANCE,
    max_side: int = DEFAULT_QUAD_MAX_SIDE,
    min_area_ratio: float = DEFAULT_MIN_AREA_RATIO,
    max_area_ratio: float = DEFAULT_MAX_AREA_RATIO,
    min_rectangularity: float = DEFAULT_MIN_RECTANGULARITY,
    min_fill: float = DEFAULT_MIN_FILL,
    min_contrast: float = DEFAULT_MIN_CONTRAST,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> Optional[QuadDetection]:
    """Ищет четырёхугольник светлого листа. `None`, если листа нет."""
    frame = _as_bgr(frame_bgr)
    if frame is None:
        return None
    height, width = frame.shape[:2]
    scale = min(1.0, float(max_side) / float(max(height, width)))
    if scale < 1.0:
        work = cv2.resize(frame, (max(1, int(round(width * scale))),
                                  max(1, int(round(height * scale)))),
                          interpolation=cv2.INTER_AREA)
    else:
        work = frame
    work_h, work_w = work.shape[:2]
    frame_area = float(work_w * work_h)
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    mask = _bright_mask(gray)

    try:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
    except cv2.error:
        return None
    if not contours:
        return None
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:max_candidates]

    min_aspect = expected_aspect * (1.0 - float(aspect_tolerance))
    max_aspect = expected_aspect * (1.0 + float(aspect_tolerance))

    best: Optional[QuadDetection] = None
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area <= 0.0:
            continue
        area_ratio = area / frame_area
        if area_ratio < min_area_ratio or area_ratio > max_area_ratio:
            continue
        perimeter = float(cv2.arcLength(contour, True))
        if perimeter < 4.0:
            continue
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) != 4:
            continue
        quad = order_quad(approx.reshape(4, 2))
        if not cv2.isContourConvex(quad.astype(np.float32)):
            continue
        sides = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        if float(sides.min()) < 6.0:
            continue
        rect = cv2.minAreaRect(quad.astype(np.float32))
        rect_w, rect_h = float(rect[1][0]), float(rect[1][1])
        if min(rect_w, rect_h) < 6.0:
            continue
        rectangularity = area / max(1e-6, rect_w * rect_h)
        if rectangularity < min_rectangularity:
            continue
        aspect = max(rect_w, rect_h) / min(rect_w, rect_h)
        if aspect < min_aspect or aspect > max_aspect:
            continue

        fill, contrast = _region_stats(gray, mask, quad)
        if fill < min_fill or contrast < min_contrast:
            continue

        confidence = quad_confidence(area_ratio, rectangularity, fill, aspect,
                                     expected_aspect)
        candidate = QuadDetection(
            quad=quad / scale,
            confidence=confidence,
            area_ratio=area_ratio,
            rectangularity=rectangularity,
            fill=fill,
            aspect=aspect,
            contrast=contrast,
            work_scale=scale,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    if best is None:
        return None
    return best


def _region_stats(gray: np.ndarray, mask: np.ndarray,
                  quad: np.ndarray) -> tuple[float, float]:
    """Доля ярких пикселей внутри квада и контраст «внутри минус фон»."""
    filled = np.zeros(gray.shape, np.uint8)
    cv2.fillPoly(filled, [np.round(quad).astype(np.int32)], 255)
    inside = filled > 0
    count = int(inside.sum())
    if count == 0:
        return 0.0, 0.0
    fill = float(np.count_nonzero(mask[inside])) / float(count)
    ring = cv2.dilate(filled, np.ones((9, 9), np.uint8)) > 0
    ring &= ~inside
    contrast = 0.0
    if np.any(ring):
        contrast = float(gray[inside].mean()) - float(gray[ring].mean())
    return fill, contrast


__all__ = [
    "DEFAULT_QUAD_MAX_SIDE",
    "DEFAULT_SHEET_ASPECT",
    "QuadDetection",
    "detect_sheet_quad",
    "order_quad",
    "quad_aspect",
    "quad_confidence",
]
