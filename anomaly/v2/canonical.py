"""Канонические кропы узлов планшета: выравнивание по ArUco-гомографии.

Зачем
-----
PatchCore из `anomaly/` сравнивает *разные сцены*: кадр с другого ракурса
даёт другую картинку целиком, и расстояние до банка нормы растёт от новизны
ракурса, а не от дефекта. Здесь зона каждого узла приводится к фиксированному
прямоугольнику в координатах планшета (board px), поэтому один и тот же узел
с любого ракурса попадает в одни и те же пиксели кропа.

Схема
-----
    кадр --(detect_board)--> H: board_px -> frame_px
    rect узла в board_px (из layout.components[i].polygon_px)
    M = S(512/w) @ T(-x0,-y0) @ H^-1      # frame_px -> crop_px
    crop = cv2.warpPerspective(frame, M, (512, 512))

Ректификация делается одним ресемплингом (без промежуточного «выпрямления»
всего планшета), поэтому кроп не теряет резкость.

Публичное API
-------------
    canonical_crops(board_image, layout) -> dict[str, np.ndarray]
    canonical_crop(board_image, layout, component_id) -> np.ndarray | None
    naive_crops(board_image, layout)     -> dict[str, np.ndarray]  # без выравнивания
    board_homography(board_image, layout) -> np.ndarray | None
    ssim(a, b) -> float
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import cv2
import numpy as np

# --- доступ к ar_board (гомография по ArUco) ------------------------------- #
_AR_BOARD_ROOT = Path(__file__).resolve().parents[2] / "ar_board"
if str(_AR_BOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_AR_BOARD_ROOT))

try:  # pragma: no cover - зависит от окружения
    from ar_board.detect import detect_board  # type: ignore
    from ar_board.io_utils import imread as _imread  # type: ignore
    from ar_board.io_utils import imwrite as _imwrite  # type: ignore
    from ar_board.layout import load_layout as _load_layout  # type: ignore
except Exception as exc:  # pragma: no cover
    detect_board = None  # type: ignore
    _imread = _imwrite = _load_layout = None  # type: ignore
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

CROP_SIZE = 512
COMPONENTS: Tuple[str, ...] = ("motor", "pump", "coupling_guard", "flange", "manometer")


# I/O
def imread(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """cv2.imread, устойчивый к кириллическим путям (Windows)."""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        raise IOError(f"не удалось прочитать файл: {path}")
    img = cv2.imdecode(data, flags)
    if img is None:
        raise IOError(f"не удалось декодировать изображение: {path}")
    return img


def imwrite(path: str | Path, image: np.ndarray, params: Optional[Iterable[int]] = None) -> None:
    """cv2.imwrite, устойчивый к кириллическим путям."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = p.suffix or ".png"
    ok, buf = cv2.imencode(ext, image, list(params or []))
    if not ok:
        raise IOError(f"не удалось закодировать изображение: {path}")
    buf.tofile(str(p))


def load_layout(path: str | Path) -> Dict[str, Any]:
    if _load_layout is None:
        raise RuntimeError(f"ar_board.layout недоступен: {_IMPORT_ERROR}")
    return _load_layout(path)


# Геометрия
def _board_size(layout: Dict[str, Any]) -> Tuple[float, float]:
    board = layout.get("board", {})
    return float(board.get("width_px", 4961)), float(board.get("height_px", 3508))


def component_rects(layout: Dict[str, Any], context: float = 1.0) -> Dict[str, Tuple[float, float, float, float]]:
    """Прямоугольники узлов в координатах планшета (детерминированы layout-ом).

    `context` > 1 расширяет окно вокруг узла (контекст вокруг дефекта),
    при этом окно остаётся квадратным и не выходит за пределы планшета.
    """
    bw, bh = _board_size(layout)
    out: Dict[str, Tuple[float, float, float, float]] = {}
    for comp in layout.get("components", []):
        cid = str(comp.get("component_id") or "")
        poly = np.asarray(comp.get("polygon_px") or [], dtype=np.float64)
        if not cid or poly.shape[0] < 3:
            continue
        x0, y0 = poly.min(axis=0)
        x1, y1 = poly.max(axis=0)
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        side = max(x1 - x0, y1 - y0) * float(context)
        side = min(side, bw, bh)
        half = side / 2.0
        cx = float(np.clip(cx, half, bw - half))
        cy = float(np.clip(cy, half, bh - half))
        out[cid] = (cx - half, cy - half, cx + half, cy + half)
    return out


def board_homography(
    board_image: np.ndarray,
    layout: Dict[str, Any],
    *,
    min_markers: int = 3,
) -> Optional[np.ndarray]:
    """H: пиксели планшета -> пиксели кадра. None, если ArUco не найдены."""
    if detect_board is None:
        raise RuntimeError(f"ar_board.detect недоступен: {_IMPORT_ERROR}")
    if board_image is None or board_image.size == 0:
        return None
    pose = detect_board(board_image, layout, min_markers=min_markers)
    if pose is None:
        return None
    return np.asarray(pose.homography, dtype=np.float64)


def crop_matrix(
    homography: np.ndarray,
    rect: Tuple[float, float, float, float],
    size: int = CROP_SIZE,
) -> np.ndarray:
    """Матрица frame_px -> crop_px для прямоугольника в координатах планшета."""
    x0, y0, x1, y1 = rect
    sx = float(size) / max(x1 - x0, 1e-6)
    sy = float(size) / max(y1 - y0, 1e-6)
    translate = np.array([[1.0, 0.0, -x0], [0.0, 1.0, -y0], [0.0, 0.0, 1.0]])
    scale = np.array([[sx, 0.0, 0.0], [0.0, sy, 0.0], [0.0, 0.0, 1.0]])
    return scale @ translate @ np.linalg.inv(homography)


def _warp_crop(
    frame: np.ndarray, homography: np.ndarray, rect: Tuple[float, float, float, float], size: int
) -> np.ndarray:
    m = crop_matrix(homography, rect, size)
    return cv2.warpPerspective(
        frame, m, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


def canonical_crops(
    board_image: np.ndarray,
    layout: Dict[str, Any],
    *,
    size: int = CROP_SIZE,
    context: float = 1.0,
    homography: Optional[np.ndarray] = None,
    min_markers: int = 3,
) -> Dict[str, np.ndarray]:
    """Канонические кропы всех узлов layout-а.

    Параметры
    ---------
    board_image : BGR-кадр с планшетом (любой ракурс).
    layout : layout.json планшета.
    size : сторона квадратного кропа.
    context : >1 — добавить контекст вокруг узла.
    homography : если уже посчитана (или известна истинная) — не детектировать ArUco.

    Возвращает dict[component_id -> (size,size,3) uint8]. Пустой dict, если
    планшет не найден (нет ≥ min_markers маркеров).
    """
    h = homography if homography is not None else board_homography(
        board_image, layout, min_markers=min_markers
    )
    if h is None:
        return {}
    rects = component_rects(layout, context=context)
    out: Dict[str, np.ndarray] = {}
    for cid, rect in rects.items():
        out[cid] = _warp_crop(board_image, h, rect, size)
    return out


def canonical_crop(
    board_image: np.ndarray,
    layout: Dict[str, Any],
    component_id: str,
    *,
    size: int = CROP_SIZE,
    context: float = 1.0,
    homography: Optional[np.ndarray] = None,
) -> Optional[np.ndarray]:
    """Канонический кроп одного узла (None, если планшет не найден)."""
    h = homography if homography is not None else board_homography(board_image, layout)
    if h is None:
        return None
    rect = component_rects(layout, context=context).get(component_id)
    if rect is None:
        return None
    return _warp_crop(board_image, h, rect, size)


def naive_crops(
    board_image: np.ndarray,
    layout: Dict[str, Any],
    *,
    size: int = 320,
    homography: Optional[np.ndarray] = None,
) -> Dict[str, np.ndarray]:
    """Кропы БЕЗ выравнивания (baseline): bbox зоны в кадре, resize до size.

    Именно так зона вырезается «на глаз» по найденной позе: перспектива
    внутри кропа остаётся, масштаб и ракурс плавают от кадра к кадру.
    """
    h = homography if homography is not None else board_homography(board_image, layout)
    if h is None:
        return {}
    bw, bh = _board_size(layout)
    out: Dict[str, np.ndarray] = {}
    fh, fw = board_image.shape[:2]
    for cid, rect in component_rects(layout, context=1.0).items():
        x0, y0, x1, y1 = rect
        quad = np.array(
            [[[x0, y0]], [[x1, y0]], [[x1, y1]], [[x0, y1]]], dtype=np.float64
        )
        pts = cv2.perspectiveTransform(quad, h).reshape(-1, 2)
        ax0 = int(np.clip(np.floor(pts[:, 0].min()), 0, fw - 2))
        ax1 = int(np.clip(np.ceil(pts[:, 0].max()), ax0 + 2, fw))
        ay0 = int(np.clip(np.floor(pts[:, 1].min()), 0, fh - 2))
        ay1 = int(np.clip(np.ceil(pts[:, 1].max()), ay0 + 2, fh))
        crop = board_image[ay0:ay1, ax0:ax1]
        out[cid] = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    return out


# SSIM (без scikit-image: в окружении его нет)
_SSIM_KSIZE = (11, 11)
_SSIM_SIGMA = 1.5


def _ssim_gray(x: np.ndarray, y: np.ndarray) -> float:
    x = x.astype(np.float32)
    y = y.astype(np.float32)
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    blur = lambda z: cv2.GaussianBlur(z, _SSIM_KSIZE, _SSIM_SIGMA)
    mu_x, mu_y = blur(x), blur(y)
    mu_xx, mu_yy, mu_xy = mu_x * mu_x, mu_y * mu_y, mu_x * mu_y
    sx = blur(x * x) - mu_xx
    sy = blur(y * y) - mu_yy
    sxy = blur(x * y) - mu_xy
    num = (2 * mu_xy + c1) * (2 * sxy + c2)
    den = (mu_xx + mu_yy + c1) * (sx + sy + c2)
    return float(np.mean(num / den))


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """SSIM двух BGR/gray uint8 изображений одного размера (mean по каналам)."""
    if a.shape != b.shape:
        raise ValueError(f"ssim: разная форма {a.shape} vs {b.shape}")
    if a.ndim == 3:
        return float(np.mean([_ssim_gray(a[..., c], b[..., c]) for c in range(a.shape[2])]))
    return _ssim_gray(a, b)


def identity_homography() -> np.ndarray:
    """H для случая «на входе уже сам файл планшета»."""
    return np.eye(3, dtype=np.float64)


def simulate_unaligned(
    crop: np.ndarray,
    rng: np.random.Generator,
    *,
    max_tilt_deg: float = 25.0,
    rot_deg: float = 12.0,
    scale: Tuple[float, float] = (0.70, 1.0),
    shift: float = 0.05,
    out_size: int = 320,
) -> np.ndarray:
    """Имитация кропа БЕЗ выравнивания (для baseline-аблации).

    Берём канонический кроп и «снимаем» его как обычный bbox: случайный
    масштаб/поворот/сдвиг + перспективное сжатие одной стороны (наклон до
    `max_tilt_deg`). Именно так выглядит зона, вырезанная по bbox без
    ректификации: контент плавает по масштабу и геометрии от кадра к кадру.
    """
    h, w = crop.shape[:2]
    dst = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    s = float(rng.uniform(*scale))
    ang = float(rng.uniform(-rot_deg, rot_deg))
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, s)
    src = (dst @ m[:, :2].T + m[:, 2]).astype(np.float32)

    k = 0.5 * np.tan(np.deg2rad(float(rng.uniform(0.0, max_tilt_deg)))) * s
    if rng.random() < 0.5:  # сжимаем левую или правую пару углов
        idx = [0, 3] if rng.random() < 0.5 else [1, 2]
        src[idx, 0] += (src[idx, 0] - w / 2.0) * (-k)
    else:  # верхнюю или нижнюю
        idx = [0, 1] if rng.random() < 0.5 else [2, 3]
        src[idx, 1] += (src[idx, 1] - h / 2.0) * (-k)
    src += rng.normal(0.0, shift * min(h, w), src.shape).astype(np.float32)

    hm = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        crop,
        hm,
        (out_size, out_size),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
