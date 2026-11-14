"""Синтез дефектов на канонических кропах + сборка парного датасета.

Идея
----
Чистых кадров нашего объекта почти нет (один планшет = одна сцена). Чтобы
проверить *локализацию*, а не «новизну ракурса», дефекты переносятся на
канонические кропы чистого планшета:

    кроп нормы --(аугментация)--> чистый сэмпл
                                \\--(дефект + маска)--> дефектный сэмпл

Дефектный сэмпл получается из того же базового кропа, что и чистый, поэтому
единственное отличие в паре — сам дефект (фотометрия и геометрия совпадают).

Текстуры берутся с реальных фото организатора (`organizer_data/defects/_curated`):
коррозия/оксидация — ржавая текстура, эрозия/кавитация — солевые отложения,
масло — тёмные глянцевые пятна. Это перенос текстуры, а не реальный дефект
нашего насоса: см. ограничения в README.

Типы дефектов
-------------
    corrosion      — ржавая текстура по маске, multiply-blend (сохраняет свет)
    oil_leak       — тёмное глянцевое пятно с градиентом и подтёками
    crack          — тёмная ломаная линия с размытием и светлым краем
    erosion        — светлая кавернозная зона (соль/кавитация)
    missing_guard  — вырез зоны кожуха, заливка фоном планшета + шум (только coupling_guard)

API
---
    load_texture_library(curated_dir) -> dict[str, list[np.ndarray]]
    augment_crop(crop, rng, ...) -> np.ndarray
    apply_defect(crop, defect_type, rng, textures) -> (img, mask)
    build_dataset(root, board, layout, ...) -> dict   # пишет v2/data/
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .canonical import CROP_SIZE, imread, imwrite, load_layout, canonical_crops, identity_homography

# Конфигурация датасета
N_CLEAN = 60          # чистых кропов на узел
N_DEFECT = 40         # дефектных на узел
N_PER_TYPE = 10
N_TRAIN = 36          # из чистых: банк
N_CALIB = 12          # из чистых: калибровка порога
N_TEST = 12           # из чистых: тест (36+12+12 = 60)

DEFECT_TYPES: Dict[str, Tuple[str, ...]] = {
    "motor": ("corrosion", "oil_leak", "crack", "erosion"),
    "pump": ("corrosion", "oil_leak", "crack", "erosion"),
    "coupling_guard": ("corrosion", "oil_leak", "missing_guard", "crack"),
    "flange": ("corrosion", "oil_leak", "crack", "erosion"),
    "manometer": ("corrosion", "oil_leak", "crack", "erosion"),
}

SEED_CLEAN = 0
SEED_DEFECT = 1000


# Библиотека текстур
_RUST_TYPES = {"corrosion", "oxidation", "mineral_salt"}
_EROSION_TYPES = {"cavitation", "mineral_salt", "grease"}


def _read_labels(curated_dir: Path) -> List[Dict[str, str]]:
    path = curated_dir / "labels.csv"
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = []
        for row in csv.DictReader(fh):
            row = {(k or "").lstrip("\ufeff"): v for k, v in row.items()}
            if str(row.get("is_defect", "1")).strip() == "1":
                rows.append(row)
        return rows


def _brown_fraction(bgr: np.ndarray) -> float:
    """Доля «ржавых» пикселей (коричнево-оранжевый тон, средняя насыщенность)."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    rust = ((h < 30) | (h > 165)) & (s > 60) & (v > 35) & (v < 235)
    return float(rust.mean())


def _texture_patches(
    bgr: np.ndarray, rng: np.random.Generator, n: int, size: int = 192, tries: int = 24
) -> List[Tuple[float, np.ndarray]]:
    """Лучшие по «ржавости» патчи изображения: [(score, patch), ...].

    score = доля ржавых пикселей * контраст патча: ровные бурые заливки
    (низкий std) не считаются хорошей текстурой.
    """
    h, w = bgr.shape[:2]
    if h < size + 4 or w < size + 4:
        size = max(64, min(size, h - 2, w - 2))
        if size < 32:
            return []
    out: List[Tuple[float, np.ndarray]] = []
    for _ in range(tries):
        y = int(rng.integers(0, h - size))
        x = int(rng.integers(0, w - size))
        patch = bgr[y : y + size, x : x + size]
        std = float(patch.std())
        if std < 6.0:
            continue
        score = _brown_fraction(patch) * min(1.0, std / 45.0)
        out.append((score, patch.copy()))
    out.sort(key=lambda t: t[0], reverse=True)
    return out[:n]


def load_texture_library(
    curated_dir: str | Path, *, per_image: int = 3, seed: int = 0, verbose: bool = False
) -> Dict[str, List[np.ndarray]]:
    """Ржавые и «эрозионные» текстуры с реальных фото дефектов организатора.

    Возвращает {"corrosion": [...], "erosion": [...]} — BGR-патчи 192x192.
    """
    curated = Path(curated_dir)
    rng = np.random.default_rng(seed)
    labels = _read_labels(curated)
    rust: List[np.ndarray] = []
    erosion: List[np.ndarray] = []
    used = 0
    for row in labels:
        fname = row.get("file") or ""
        dtype = (row.get("defect_type") or "").strip()
        path = curated / fname
        if not fname or not path.is_file():
            continue
        if dtype not in _RUST_TYPES and dtype not in _EROSION_TYPES:
            continue
        img = imread(path)
        if img is None:
            continue
        # ограничим размер, чтобы не тратить RAM на 4000px фото
        h, w = img.shape[:2]
        if max(h, w) > 1400:
            k = 1400.0 / max(h, w)
            img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
        pats = _texture_patches(img, rng, per_image)
        used += 1
        for score, patch in pats:
            if score < 0.12:
                continue
            if dtype in _RUST_TYPES:
                rust.append(patch)
            if dtype in _EROSION_TYPES:
                erosion.append(patch)
        if verbose:
            print(f"  текстуры: {fname} ({dtype}) -> {len(pats)}")
    if not rust:
        raise RuntimeError(f"не найдено ржавых текстур в {curated}")
    if not erosion:
        erosion = [p.copy() for p in rust[: max(1, len(rust) // 2)]]
    if verbose:
        print(f"  библиотека текстур: {len(rust)} rust, {len(erosion)} erosion, файлов={used}")
    return {"corrosion": rust, "erosion": erosion}


# Аугментации чистого кропа
def augment_crop(
    crop: np.ndarray,
    rng: np.random.Generator,
    *,
    rot_deg: float = 8.0,
    scale: Tuple[float, float] = (0.9, 1.1),
    brightness: Tuple[float, float] = (-18.0, 18.0),
    contrast: Tuple[float, float] = (0.85, 1.15),
    gamma: Tuple[float, float] = (0.85, 1.18),
    noise_sigma: float = 2.5,
    jpeg_quality: Tuple[int, int] = (70, 95),
) -> np.ndarray:
    """Геометрическая + фотометрическая аугментация кропа (как в ТЗ)."""
    h, w = crop.shape[:2]
    angle = float(rng.uniform(-rot_deg, rot_deg))
    s = float(rng.uniform(*scale))
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, s)
    img = cv2.warpAffine(
        crop, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101
    )

    x = img.astype(np.float32) / 255.0
    g = float(rng.uniform(*gamma))
    x = np.power(np.clip(x, 0.0, 1.0), g)
    c = float(rng.uniform(*contrast))
    b = float(rng.uniform(*brightness)) / 255.0
    x = np.clip((x - 0.5) * c + 0.5 + b, 0.0, 1.0) * 255.0
    if noise_sigma > 0:
        x = x + rng.normal(0.0, noise_sigma, x.shape)
    img = np.clip(x, 0, 255).astype(np.uint8)

    q = int(rng.integers(jpeg_quality[0], jpeg_quality[1] + 1))
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), q])
    if ok:
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return img


# Вспомогательная геометрия дефектов
def _plate_color(crop: np.ndarray) -> np.ndarray:
    """Цвет фона планшета: медиана пикселей по периметру кропа."""
    b = 10
    border = np.concatenate(
        [
            crop[:b].reshape(-1, 3),
            crop[-b:].reshape(-1, 3),
            crop[:, :b].reshape(-1, 3),
            crop[:, -b:].reshape(-1, 3),
        ],
        axis=0,
    )
    return np.median(border, axis=0).astype(np.float32)


def content_mask(crop: np.ndarray) -> np.ndarray:
    """Маска «железа»: пиксели, отличные от фона планшета."""
    bg = _plate_color(crop)
    d = np.linalg.norm(crop.astype(np.float32) - bg[None, None, :], axis=2)
    m = (d > 30).astype(np.uint8)
    k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    k9 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k5)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k9)
    return m


def _content_center(
    crop: np.ndarray, rng: np.random.Generator, min_rel: float = 0.55
) -> Tuple[int, int, np.ndarray, float]:
    """Точка внутри «железа» + маска контента + расстояние до края в этой точке."""
    h, w = crop.shape[:2]
    m = content_mask(crop)
    if m.sum() < 0.05 * h * w:
        return w // 2, h // 2, m, float(min(h, w) // 4)
    dist = cv2.distanceTransform(m, cv2.DIST_L2, 5)
    peak = float(dist.max())
    if peak <= 0:
        return w // 2, h // 2, m, float(min(h, w) // 4)
    cand = np.argwhere(dist > min_rel * peak)
    y, x = cand[int(rng.integers(0, len(cand)))]
    return int(x), int(y), m, float(dist[y, x])


def _blob_mask(
    shape: Tuple[int, int],
    center: Tuple[int, int],
    radius: float,
    rng: np.random.Generator,
    *,
    soft: float = 0.12,
    satellites: int = 2,
) -> np.ndarray:
    """Неровное пятно (радиальная пертурбация) -> мягкая маска float32 0..1."""
    h, w = shape
    m = np.zeros((h, w), np.uint8)
    n = 28
    ang = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    rr = radius * (0.62 + 0.38 * rng.random(n))
    cx, cy = center
    pts = np.stack([cx + rr * np.cos(ang), cy + rr * np.sin(ang)], axis=1)
    cv2.fillPoly(m, [np.round(pts).astype(np.int32)], 255)
    for _ in range(satellites):
        a = rng.uniform(0, 2 * np.pi)
        d = radius * rng.uniform(0.55, 0.95)
        c2 = (int(cx + d * np.cos(a)), int(cy + d * np.sin(a)))
        cv2.circle(m, c2, int(radius * rng.uniform(0.2, 0.4)), 255, -1)
    sigma = max(1.0, radius * soft)
    soft_m = cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), sigma)
    return np.clip(soft_m, 0.0, 1.0)


def _fit_texture(tex: np.ndarray, h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    """Патч текстуры нужного размера: кроп 1:1, если хватает, иначе resize.

    Кроп 1:1 важен: resize 192->512 размывает ржавчину до «коричневой
    заливки», и дефект перестаёт быть текстурным.
    """
    t = tex
    if rng.random() < 0.5:
        t = cv2.flip(t, 1)
    if rng.random() < 0.5:
        t = cv2.flip(t, 0)
    k = int(rng.integers(0, 4))
    if k:
        t = np.rot90(t, k).copy()
    th, tw = t.shape[:2]
    if h <= th and w <= tw:
        y = int(rng.integers(0, th - h + 1))
        x = int(rng.integers(0, tw - w + 1))
        return t[y : y + h, x : x + w]
    scale = max(h / th, w / tw)
    interp = cv2.INTER_LINEAR if scale < 2.0 else cv2.INTER_CUBIC
    t = cv2.resize(t, (max(w, int(tw * scale)), max(h, int(th * scale))), interpolation=interp)
    th, tw = t.shape[:2]
    y = int(rng.integers(0, th - h + 1))
    x = int(rng.integers(0, tw - w + 1))
    return t[y : y + h, x : x + w]


def _texture_layer(
    tex: np.ndarray,
    h: int,
    w: int,
    rng: np.random.Generator,
    *,
    brightness: float = 1.0,
    contrast: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Текстура размера (h,w) и её яркостная модуляция (для multiply-blend)."""
    t = _fit_texture(tex, h, w, rng).astype(np.float32)
    gray = cv2.cvtColor(t.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    shade = gray / max(float(gray.mean()), 1e-3)
    shade = np.clip((shade - 1.0) * contrast + 1.0, 0.25, 2.2) * brightness
    return t, shade


def _blob_bbox(mask: np.ndarray, pad: int = 4) -> Tuple[int, int, int, int]:
    ys, xs = np.where(mask > 0.05)
    h, w = mask.shape
    if len(ys) == 0:
        return 0, 0, w, h
    return (
        max(0, int(ys.min()) - pad),
        min(h, int(ys.max()) + pad + 1),
        max(0, int(xs.min()) - pad),
        min(w, int(xs.max()) + pad + 1),
    )


def _speckle(shape: Tuple[int, int], rng: np.random.Generator, amount: float = 1.0) -> np.ndarray:
    h, w = shape
    n = int(amount * h * w * 0.002)
    s = np.zeros((h, w), np.float32)
    ys = rng.integers(0, h, n)
    xs = rng.integers(0, w, n)
    s[ys, xs] = rng.uniform(-40, 40, n)
    return cv2.GaussianBlur(s, (0, 0), 1.0)


# Дефекты
def apply_corrosion(
    crop: np.ndarray, rng: np.random.Generator, tex: np.ndarray, *, severe: bool = False
) -> Tuple[np.ndarray, np.ndarray]:
    """Коррозия: текстура ржавчины по маске с сохранением освещения (multiply)."""
    h, w = crop.shape[:2]
    cx, cy, _, dist = _content_center(crop, rng)
    radius = float(rng.uniform(0.10, 0.20) * min(h, w)) * (1.35 if severe else 1.0)
    radius = max(8.0, min(radius, 0.85 * max(dist, 12.0)))
    mask = _blob_mask((h, w), (cx, cy), radius, rng, satellites=3 if severe else 2)
    # рваный край: маску режем шумом
    noise = cv2.GaussianBlur(
        rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), max(1.0, radius * 0.25)
    )
    mask = np.clip(mask + 0.35 * noise, 0.0, 1.0)
    mask = np.clip((mask - 0.35) / 0.35, 0.0, 1.0)
    # неоднородная «пропитка»: внутри пятна альфа тоже гуляет
    grain = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), radius * 0.12)
    mask = np.clip(mask * (0.85 + 0.3 * grain), 0.0, 1.0)

    y0, y1, x0, x1 = _blob_bbox(mask)
    bh, bw = y1 - y0, x1 - x0
    layer, shade = _texture_layer(tex, bh, bw, rng, contrast=1.25)
    rust_col = np.median(layer.reshape(-1, 3), axis=0)
    rust_col = np.clip(rust_col * rng.uniform(0.8, 1.2), 0, 255)
    tinted = rust_col[None, None, :] * shade[..., None] * rng.uniform(0.85, 1.05)

    base = crop[y0:y1, x0:x1].astype(np.float32)
    m = mask[y0:y1, x0:x1]
    m3 = m[..., None]
    strength = 0.94 if severe else 0.88
    out = base * (1.0 - m3 * strength) + (base * 0.30 + tinted * 0.70) * m3 * strength
    # затемнение по кромке пятна (грязь скапливается по краю)
    edge = np.clip(m * (1.0 - m) * 4.0, 0.0, 1.0)
    out *= 1.0 - 0.25 * edge[..., None]
    out += _speckle((bh, bw), rng, 1.2 if severe else 0.8)[..., None]

    result = crop.astype(np.float32)
    result[y0:y1, x0:x1] = out
    result = np.clip(result, 0, 255).astype(np.uint8)
    return result, (mask > 0.5).astype(np.uint8) * 255


def apply_oil_leak(
    crop: np.ndarray, rng: np.random.Generator, tex: Optional[np.ndarray] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """Подтёк масла: тёмное глянцевое пятно с градиентом и каплями вниз."""
    h, w = crop.shape[:2]
    cx, cy, _, dist = _content_center(crop, rng)
    radius = float(rng.uniform(0.09, 0.17) * min(h, w))
    radius = max(8.0, min(radius, 0.8 * max(dist, 12.0)))
    mask = _blob_mask((h, w), (cx, cy), radius, rng, soft=0.18, satellites=1)

    # подтёки вниз
    drips = np.zeros((h, w), np.uint8)
    for _ in range(int(rng.integers(2, 5))):
        x0 = int(np.clip(cx + rng.normal(0, radius * 0.5), 2, w - 3))
        y0 = int(np.clip(cy + rng.uniform(0, radius * 0.4), 2, h - 3))
        y1 = int(np.clip(y0 + rng.uniform(0.8, 2.2) * radius, y0 + 2, h - 2))
        thick = int(max(1, rng.uniform(1.5, 4.0)))
        cv2.line(drips, (x0, y0), (int(x0 + rng.normal(0, 3)), y1), 255, thick)
        cv2.circle(drips, (int(x0 + rng.normal(0, 3)), y1), thick, 255, -1)
    drips = cv2.GaussianBlur(drips.astype(np.float32) / 255.0, (0, 0), 1.6)
    mask = np.clip(mask + 0.85 * drips, 0.0, 1.0)

    base = crop.astype(np.float32)
    m3 = mask[..., None]
    # тёмная масляная плёнка (масло почти чёрное, но с цветным отливом)
    out = base * (1.0 - 0.72 * m3)
    # глянец: блик сверху-слева пятна
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sx, sy = cx - radius * 0.35, cy - radius * 0.35
    gloss = np.exp(-(((xx - sx) ** 2 + (yy - sy) ** 2) / (2.0 * (radius * 0.40) ** 2)))
    out += (gloss * mask * rng.uniform(50, 95))[..., None]
    # тонкий яркий контур по кромке плёнки
    edge = np.clip(mask * (1.0 - mask) * 4.0, 0.0, 1.0)
    out += (edge * rng.uniform(8, 20))[..., None]
    # тёплый/зеленоватый отлив масла
    tint = np.array([10.0, 18.0, 6.0], dtype=np.float32) * rng.uniform(0.4, 1.0)
    out += tint[None, None, :] * m3 * 0.6
    out += _speckle((h, w), rng, 0.4)[..., None]
    out = np.clip(out, 0, 255).astype(np.uint8)
    return out, (mask > 0.5).astype(np.uint8) * 255


def apply_crack(
    crop: np.ndarray, rng: np.random.Generator, tex: Optional[np.ndarray] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """Трещина: тёмная ломаная с размытием и светлым краем (отражение)."""
    h, w = crop.shape[:2]
    cx, cy, _, dist = _content_center(crop, rng)
    length = float(rng.uniform(0.22, 0.45) * min(h, w))
    ang = float(rng.uniform(0, 2 * np.pi))
    n_seg = int(rng.integers(4, 8))
    pts = [(float(cx), float(cy))]
    step = length / n_seg
    for i in range(n_seg):
        ang += float(rng.normal(0, 0.45))
        px, py = pts[-1]
        pts.append(
            (
                float(np.clip(px + step * np.cos(ang), 4, w - 5)),
                float(np.clip(py + step * np.sin(ang), 4, h - 5)),
            )
        )
    line = np.zeros((h, w), np.uint8)
    thick = int(max(1, round(rng.uniform(1.5, 3.2))))
    cv2.polylines(
        line, [np.round(np.array(pts)).astype(np.int32)], False, 255, thick, lineType=cv2.LINE_AA
    )
    # ответвление
    if rng.random() < 0.6:
        i0 = int(rng.integers(1, max(2, len(pts) - 1)))
        a2 = ang + float(rng.normal(0, 1.0))
        p0 = np.array(pts[i0])
        p1 = p0 + np.array([np.cos(a2), np.sin(a2)]) * length * rng.uniform(0.2, 0.45)
        cv2.line(
            line,
            tuple(np.round(p0).astype(int)),
            tuple(np.round(p1).astype(int)),
            255,
            max(1, thick - 1),
            cv2.LINE_AA,
        )

    core = cv2.GaussianBlur(line.astype(np.float32) / 255.0, (0, 0), 0.8)
    halo = cv2.GaussianBlur(line.astype(np.float32) / 255.0, (0, 0), 2.6)

    base = crop.astype(np.float32)
    out = base * (1.0 - 0.85 * core[..., None])
    # светлый край с одной стороны (блик на кромке)
    shifted = np.roll(core, -2, axis=1)
    rim = np.clip(shifted - core, 0.0, 1.0)
    out += (rim * 50.0)[..., None]
    out += (halo * 14.0)[..., None]
    out = np.clip(out, 0, 255).astype(np.uint8)
    mask = (halo > 0.12).astype(np.uint8) * 255
    return out, mask


def apply_erosion(
    crop: np.ndarray, rng: np.random.Generator, tex: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """Эрозия/кавитация: светлая матовая кавернозная зона с питтингом."""
    h, w = crop.shape[:2]
    cx, cy, _, dist = _content_center(crop, rng)
    radius = float(rng.uniform(0.08, 0.15) * min(h, w))
    radius = max(8.0, min(radius, 0.8 * max(dist, 12.0)))
    mask = _blob_mask((h, w), (cx, cy), radius, rng, soft=0.10, satellites=3)

    y0, y1, x0, x1 = _blob_bbox(mask)
    bh, bw = y1 - y0, x1 - x0
    layer, shade = _texture_layer(tex, bh, bw, rng, brightness=1.1, contrast=1.2)
    bright = np.clip(layer * 0.8 + 60.0, 0, 255)
    base = crop[y0:y1, x0:x1].astype(np.float32)
    m = mask[y0:y1, x0:x1]
    m3 = m[..., None]
    out = base * (1.0 - 0.82 * m3) + (base * 0.22 + bright * 0.78) * m3 * 0.82
    # питтинг: мелкие тёмные кратеры
    pits = np.zeros((bh, bw), np.float32)
    for _ in range(int(rng.integers(8, 20))):
        a = rng.uniform(0, 2 * np.pi)
        d = radius * np.sqrt(rng.random())
        px = int(np.clip(cx - x0 + d * np.cos(a), 0, bw - 1))
        py = int(np.clip(cy - y0 + d * np.sin(a), 0, bh - 1))
        cv2.circle(pits, (px, py), int(rng.uniform(1, 3)), float(rng.uniform(0.4, 1.0)), -1)
    pits = cv2.GaussianBlur(pits, (0, 0), 0.9) * m
    out *= (1.0 - 0.5 * pits)[..., None]

    result = crop.astype(np.float32)
    result[y0:y1, x0:x1] = out
    result = np.clip(result, 0, 255).astype(np.uint8)
    return result, (mask > 0.5).astype(np.uint8) * 255


def apply_missing_guard(
    crop: np.ndarray, rng: np.random.Generator, tex: Optional[np.ndarray] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """Отсутствие кожуха: вырезаем зону, заливаем фоном планшета + шум/тень."""
    h, w = crop.shape[:2]
    cx, cy, cmask, dist = _content_center(crop, rng)
    side = int(rng.uniform(0.30, 0.48) * min(h, w))
    x0 = int(np.clip(cx - side // 2, 2, w - side - 2))
    y0 = int(np.clip(cy - side // 2, 2, h - side - 2))
    mask = np.zeros((h, w), np.float32)
    mask[y0 : y0 + side, x0 : x0 + side] = 1.0
    mask = cv2.GaussianBlur(mask, (0, 0), 1.2)

    # цвет самой платы планшета (белая бумага), а не локальный фон кропа:
    # иначе на красном кожухе «дыра» заливается красным
    plate = (tex or {}).get("_plate_bgr") if isinstance(tex, dict) else None
    bg = np.asarray(plate, dtype=np.float32) if plate is not None else _plate_color(crop)
    bg = np.clip(bg * rng.uniform(0.96, 1.04), 0, 255)
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    fill = np.broadcast_to(bg[None, None, :], (h, w, 3)).astype(np.float32)
    fill = fill * (0.97 + 0.06 * yy)  # лёгкий градиент освещения
    fill += rng.normal(0, 3.0, (h, w, 3))
    # тень от оставшейся части кожуха сверху
    shadow = np.zeros((h, w), np.float32)
    shadow[max(0, y0 - 8) : y0 + 8, x0 : x0 + side] = 1.0
    shadow = cv2.GaussianBlur(shadow, (0, 0), 4.0)
    fill *= 1.0 - 0.18 * shadow[..., None]

    base = crop.astype(np.float32)
    m3 = mask[..., None]
    out = base * (1.0 - m3) + fill * m3
    out = np.clip(out, 0, 255).astype(np.uint8)
    return out, (mask > 0.5).astype(np.uint8) * 255


_APPLIERS = {
    "corrosion": apply_corrosion,
    "oil_leak": apply_oil_leak,
    "crack": apply_crack,
    "erosion": apply_erosion,
    "missing_guard": apply_missing_guard,
}


def apply_defect(
    crop: np.ndarray,
    defect_type: str,
    rng: np.random.Generator,
    textures: Dict[str, List[np.ndarray]],
) -> Tuple[np.ndarray, np.ndarray]:
    """Накладывает дефект на кроп. Возвращает (изображение, маска 0/255)."""
    if defect_type not in _APPLIERS:
        raise ValueError(f"неизвестный тип дефекта: {defect_type}")
    if defect_type in ("corrosion", "erosion"):
        key = "corrosion" if defect_type == "corrosion" else "erosion"
        lib = textures[key]
        tex = lib[int(rng.integers(0, len(lib)))]
        if defect_type == "corrosion":
            return apply_corrosion(crop, rng, tex, severe=bool(rng.random() < 0.25))
        return apply_erosion(crop, rng, tex)
    return _APPLIERS[defect_type](crop, rng, textures)


# Сборка датасета
def _split_ids(component: str, n_clean: int, n_defect: int) -> Dict[str, List[str]]:
    clean = [f"{component}_clean_{i:03d}" for i in range(n_clean)]
    defect = [f"{component}_defect_{i:03d}" for i in range(n_defect)]
    return {
        "train": clean[:N_TRAIN],
        "calib": clean[N_TRAIN : N_TRAIN + N_CALIB],
        "test_clean": clean[N_TRAIN + N_CALIB :],
        "test_defect": defect,
    }


def build_dataset(
    root: str | Path,
    board_image: np.ndarray,
    layout: Dict[str, Any],
    *,
    textures: Optional[Dict[str, List[np.ndarray]]] = None,
    curated_dir: str | Path | None = None,
    size: int = CROP_SIZE,
    n_clean: int = N_CLEAN,
    n_defect: int = N_DEFECT,
    force: bool = False,
    seed: int = 0,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Собирает v2/data: чистые кропы, дефектные кропы, маски, splits.

    Дефектные сэмплы строятся из ТЕСТОВЫХ чистых кропов (пары «тот же кадр
    с дефектом и без»), поэтому дефекты не утекают в банк/калибровку.
    """
    root = Path(root)
    meta_path = root / "meta.json"
    if meta_path.is_file() and not force:
        with meta_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("size") == size and data.get("n_clean") == n_clean:
            if verbose:
                print(f"  датасет уже собран: {root}")
            return data

    if textures is None:
        if curated_dir is None:
            raise ValueError("нужны textures или curated_dir")
        textures = load_texture_library(curated_dir, verbose=verbose)
    # цвет платы планшета — для «отсутствия кожуха» (заливка фоном, а не локальным)
    textures.setdefault("_plate_bgr", _plate_color(board_image))  # type: ignore[arg-type]

    t0 = time.perf_counter()
    base_crops = canonical_crops(board_image, layout, size=size, homography=identity_homography())
    if not base_crops:
        raise RuntimeError("не удалось получить канонические кропы планшета")

    for sub in ("clean", "defect", "mask"):
        for cid in base_crops:
            (root / sub / cid).mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []
    splits: Dict[str, Dict[str, List[str]]] = {}

    for cid, base in base_crops.items():
        rng_c = np.random.default_rng(seed + 17 * list(base_crops).index(cid) + 1)
        types = DEFECT_TYPES.get(cid, ("corrosion", "oil_leak", "crack"))
        per_type = max(1, n_defect // len(types))

        clean_imgs: Dict[str, np.ndarray] = {}
        for i in range(n_clean):
            img = augment_crop(base, rng_c)
            name = f"{cid}_clean_{i:03d}"
            imwrite(root / "clean" / cid / f"{name}.png", img)
            clean_imgs[name] = img
            records.append({"id": name, "component": cid, "kind": "clean", "index": i})

        rng_d = np.random.default_rng(seed + SEED_DEFECT + 17 * list(base_crops).index(cid))
        split = _split_ids(cid, n_clean, n_defect)
        test_clean_ids = split["test_clean"]
        k = 0
        for t_i, dtype in enumerate(types):
            for j in range(per_type):
                if k >= n_defect:
                    break
                base_id = test_clean_ids[k % len(test_clean_ids)]
                src = clean_imgs[base_id].copy()
                img, mask = apply_defect(src, dtype, rng_d, textures)
                name = f"{cid}_defect_{k:03d}"
                imwrite(root / "defect" / cid / f"{name}.png", img)
                imwrite(root / "mask" / cid / f"{name}.png", mask)
                records.append(
                    {
                        "id": name,
                        "component": cid,
                        "kind": "defect",
                        "defect_type": dtype,
                        "base_id": base_id,
                        "index": k,
                        "mask_area_frac": round(float((mask > 0).mean()), 5),
                    }
                )
                k += 1
        split["test_defect"] = split["test_defect"][:k]
        splits[cid] = split
        if verbose:
            print(f"  {cid:16s} clean={n_clean} defect={k} types={types}")

    data = {
        "size": size,
        "n_clean": n_clean,
        "n_defect": n_defect,
        "components": list(base_crops),
        "defect_types": {k: list(v) for k, v in DEFECT_TYPES.items()},
        "records": records,
        "splits": splits,
        "n_train": N_TRAIN,
        "n_calib": N_CALIB,
        "n_test_clean": N_TEST,
        "build_seconds": round(time.perf_counter() - t0, 2),
        "note": (
            "Дефекты синтетические: текстуры перенесены с фото других объектов "
            "(organizer_data/defects/_curated). Маски сохранены для pixel-level метрик."
        ),
    }
    root.mkdir(parents=True, exist_ok=True)
    with meta_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    if verbose:
        print(f"  датасет: {len(records)} сэмплов за {data['build_seconds']} c -> {root}")
    return data


def load_dataset(root: str | Path) -> Dict[str, Any]:
    root = Path(root)
    with (root / "meta.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def iter_split(root: str | Path, data: Dict[str, Any], component: str, split: str):
    """Итератор (id, bgr) по сплиту компонента."""
    root = Path(root)
    ids = data["splits"][component][split]
    kind = "defect" if split == "test_defect" else "clean"
    for sid in ids:
        yield sid, imread(root / kind / component / f"{sid}.png")


def load_mask(root: str | Path, component: str, defect_id: str) -> np.ndarray:
    return imread(Path(root) / "mask" / component / f"{defect_id}.png", cv2.IMREAD_GRAYSCALE)
