"""Синтетические кадры с известной истинной гомографией (для eval и тестов).

Генератор детерминирован: всё случайное берётся из `np.random.default_rng(seed)`.
Модель — pinhole-камера, плоскость планшета поворачивается на заданный угол,
затем масштабируется и сдвигается в кадр. Истинная гомография `H` известна,
поэтому ошибку проекции углов зон можно посчитать точно.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np

from .aligner import load_image_unicode, save_image_unicode

FRAME_SIZE = (1280, 720)
SEED = 20261115


def load_layout(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rotation(ax: float, ay: float, az: float) -> np.ndarray:
    cx, sx = np.cos(ax), np.sin(ax)
    cy, sy = np.cos(ay), np.sin(ay)
    cz, sz = np.cos(az), np.sin(az)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    return rz @ ry @ rx


def _board_corners(board_w: float, board_h: float) -> np.ndarray:
    return np.array([[[0.0, 0.0]], [[board_w, 0.0]],
                     [[board_w, board_h]], [[0.0, board_h]]], dtype=np.float64)


def sample_homography(layout: Mapping[str, Any],
                      frame_size: Tuple[int, int],
                      rng: np.random.Generator,
                      *,
                      tilt_range: Tuple[float, float] = (0.0, 25.0),
                      scale_range: Tuple[float, float] = (0.6, 1.4),
                      rotation_range: Tuple[float, float] = (-15.0, 15.0),
                      focal_scale: float = 1.15,
                      ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Случайная перспектива планшета: H (пиксели планшета -> пиксели кадра)."""
    fw, fh = frame_size
    board = layout["board"]
    bw, bh = float(board["width_px"]), float(board["height_px"])
    ppm = bw / float(board["width_mm"])

    focal = focal_scale * max(fw, fh)
    camera = np.array([[focal, 0.0, fw / 2.0],
                       [0.0, focal, fh / 2.0],
                       [0.0, 0.0, 1.0]], dtype=np.float64)

    tilt = float(rng.uniform(*tilt_range))
    phi = float(rng.uniform(0.0, 2.0 * np.pi))
    ax = np.deg2rad(tilt * np.cos(phi))
    ay = np.deg2rad(tilt * np.sin(phi))
    az = np.deg2rad(float(rng.uniform(*rotation_range)))
    rotation = _rotation(ax, ay, az)
    r1, r2 = rotation[:, 0], rotation[:, 1]

    target = float(rng.uniform(*scale_range))
    to_unit = np.diag([1.0 / (ppm * 1000.0), 1.0 / (ppm * 1000.0), 1.0])
    center = np.array([[1.0, 0.0, -bw / 2.0],
                       [0.0, 1.0, -bh / 2.0],
                       [0.0, 0.0, 1.0]], dtype=np.float64)
    corners = _board_corners(bw, bh)

    def homography_at(distance: float) -> np.ndarray:
        motion = np.column_stack([r1, r2, np.array([0.0, 0.0, distance])])
        return camera @ motion @ to_unit @ center

    distance = 1.0
    for _ in range(6):
        homography = homography_at(distance)
        pts = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
        current = max((float(pts[:, 0].max()) - float(pts[:, 0].min())) / fw,
                      (float(pts[:, 1].max()) - float(pts[:, 1].min())) / fh)
        distance *= current / target

    homography = homography_at(distance)
    pts = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
    bbox_w = float(pts[:, 0].max() - pts[:, 0].min())
    bbox_h = float(pts[:, 1].max() - pts[:, 1].min())
    slack_x = max(0.0, (fw - bbox_w) / 2.0 - 4.0)
    slack_y = max(0.0, (fh - bbox_h) / 2.0 - 4.0)
    shift_x = fw / 2.0 - float(pts[:, 0].mean()) + float(rng.uniform(-slack_x, slack_x))
    shift_y = fh / 2.0 - float(pts[:, 1].mean()) + float(rng.uniform(-slack_y, slack_y))
    translate = np.array([[1.0, 0.0, shift_x], [0.0, 1.0, shift_y], [0.0, 0.0, 1.0]])
    homography = translate @ homography

    pts = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
    scale = max((float(pts[:, 0].max()) - float(pts[:, 0].min())) / fw,
                (float(pts[:, 1].max()) - float(pts[:, 1].min())) / fh)
    meta = {
        "tilt_deg": round(tilt, 2),
        "scale": round(scale, 4),
        "rotation_deg": round(float(np.rad2deg(az)), 2),
        "distance_m": round(distance, 4),
    }
    return homography, meta


def render_frame(board_bgr: np.ndarray, homography: np.ndarray,
                 frame_size: Tuple[int, int], rng: np.random.Generator,
                 *, blur_sigma: float = 0.0,
                 jpeg_quality: Optional[int] = 90,
                 brightness: float = 1.0, contrast: float = 0.0,
                 noise_sigma: float = 3.0, shading: float = 0.0) -> np.ndarray:
    """Перспективное наложение планшета на фон + освещение + размытие + JPEG."""
    fw, fh = frame_size
    gradient = np.linspace(-14.0, 14.0, fh, dtype=np.float32)[:, None, None]
    background = np.clip(92.0 + gradient + rng.normal(0.0, 2.5, (fh, fw, 3)),
                         0, 255).astype(np.uint8)

    warped = cv2.warpPerspective(board_bgr, homography, (fw, fh), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.ones(board_bgr.shape[:2], np.float32), homography,
                               (fw, fh), flags=cv2.INTER_LINEAR)
    mask3 = np.clip(mask, 0.0, 1.0)[..., None]
    frame = background.astype(np.float32) * (1.0 - mask3) + warped.astype(np.float32) * mask3

    if shading > 0.0:
        # Неравномерная засветка: медленное мультипликативное поле по кадру.
        yy, xx = np.mgrid[0:fh, 0:fw].astype(np.float32)
        fx, fy = rng.uniform(0.4, 1.2), rng.uniform(0.4, 1.2)
        px, py = rng.uniform(0.0, 2.0 * np.pi), rng.uniform(0.0, 2.0 * np.pi)
        field = 1.0 + float(shading) * 0.5 * (np.sin(2.0 * np.pi * fx * xx / fw + px)
                                              + np.cos(2.0 * np.pi * fy * yy / fh + py))
        frame = frame * field[..., None]

    frame = frame * float(brightness) + float(contrast) + rng.normal(0.0, noise_sigma, frame.shape)
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    if blur_sigma > 0.0:
        frame = cv2.GaussianBlur(frame, (0, 0), float(blur_sigma))
    if jpeg_quality is not None:
        ok, buffer = cv2.imencode(".jpg", frame,
                                  [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
        if ok:
            frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    return frame


def make_case(layout: Mapping[str, Any], board_bgr: np.ndarray,
              frame_size: Tuple[int, int], rng: np.random.Generator,
              *, tilt_range: Tuple[float, float] = (0.0, 25.0),
              scale_range: Tuple[float, float] = (0.6, 1.4),
              rotation_range: Tuple[float, float] = (-15.0, 15.0),
              blur_sigma: float = 0.0,
              jpeg_quality: Optional[int] = 90,
              shading: float = 0.0) -> Dict[str, Any]:
    """Один синтетический кадр: frame, истинная H и параметры съёмки."""
    homography, meta = sample_homography(
        layout, frame_size, rng, tilt_range=tilt_range, scale_range=scale_range,
        rotation_range=rotation_range)
    brightness = float(rng.uniform(0.78, 1.18))
    contrast = float(rng.uniform(-22.0, 22.0))
    frame = render_frame(board_bgr, homography, frame_size, rng,
                         blur_sigma=blur_sigma, jpeg_quality=jpeg_quality,
                         brightness=brightness, contrast=contrast, shading=shading)
    meta["brightness"] = round(brightness, 3)
    meta["contrast"] = round(contrast, 2)
    meta["blur_sigma"] = round(float(blur_sigma), 2)
    meta["jpeg_quality"] = jpeg_quality
    meta["shading"] = round(float(shading), 3)
    return {"frame": frame, "homography": homography, "meta": meta}


def project_points(homography: np.ndarray, points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, np.asarray(homography, np.float64)).reshape(-1, 2)


def corner_errors(layout: Mapping[str, Any], homography_gt: np.ndarray,
                  homography_est: np.ndarray) -> Dict[str, float]:
    """Ошибка проекции углов всех зон: средняя, p95, максимум (px кадра)."""
    source: list[np.ndarray] = []
    for comp in layout.get("components", []):
        poly = comp.get("polygon_px") or []
        if len(poly) >= 3:
            source.append(np.asarray(poly, dtype=np.float64).reshape(-1, 2))
    if not source:
        return {"mean": float("nan"), "p95": float("nan"), "max": float("nan"),
                "n": 0}
    points = np.concatenate(source)
    truth = project_points(homography_gt, points)
    estimate = project_points(homography_est, points)
    distances = np.linalg.norm(truth - estimate, axis=1)
    return {
        "mean": float(distances.mean()),
        "p95": float(np.percentile(distances, 95)),
        "max": float(distances.max()),
        "n": int(len(distances)),
    }


def board_size(layout: Mapping[str, Any]) -> Tuple[float, float]:
    board = layout["board"]
    return float(board["width_px"]), float(board["height_px"])


def remove_markers(board_bgr: np.ndarray, layout: Mapping[str, Any]) -> np.ndarray:
    """Замазывает ArUco-маркеры бумажным фоном — эталон/кадры без маркеров."""
    out = board_bgr.copy()
    ppm = float(layout["board"]["width_px"]) / float(layout["board"]["width_mm"])
    for marker in layout.get("markers", []):
        corners = np.asarray(marker["corners_px"], dtype=np.float64)
        if len(corners) < 3:
            continue
        quiet = float(marker.get("quiet_zone_mm", 10.0)) * ppm
        center = corners.mean(axis=0)
        scaled = center + (corners - center) * ((60.0 * ppm + 2.0 * quiet)
                                               / max(1.0, 60.0 * ppm))
        cv2.fillPoly(out, [np.round(scaled).astype(np.int32)], (247, 247, 247))
    return out


def occlude_third(frame: np.ndarray, homography: np.ndarray,
                  layout: Mapping[str, Any], rng: np.random.Generator,
                  *, fraction: float = 1.0 / 3.0, side: str = "right") -> np.ndarray:
    """Закрывает треть проекции планшета имитацией серого листа (перекрытие)."""
    bw, bh = board_size(layout)
    x0 = bw * (1.0 - fraction) if side == "right" else 0.0
    x1 = bw if side == "right" else bw * fraction
    pad = 0.04 * bh
    strip = np.array([[x0, -pad], [x1, -pad], [x1, bh + pad], [x0, bh + pad]])
    pts = project_points(homography, strip)
    out = frame.copy()
    height, width = out.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [np.round(pts).astype(np.int32)], 255)
    # Мягкая тень по краю перекрытия.
    shadow = cv2.GaussianBlur(mask, (0, 0), 9)
    out = np.clip(out.astype(np.float32) * (1.0 - 0.35 * shadow[..., None] / 255.0),
                  0, 255).astype(np.uint8)
    grain = rng.normal(0.0, 6.0, (height, width, 3))
    ramp = np.linspace(88.0, 128.0, width, dtype=np.float32)[None, :, None]
    cover = np.clip(ramp + grain, 0, 255).astype(np.uint8)
    out[mask > 0] = cover[mask > 0]
    # Пара "заклёпок", чтобы перекрытие не было идеально однородным.
    for _ in range(12):
        cx = int(rng.uniform(pts[:, 0].min(), pts[:, 0].max()))
        cy = int(rng.uniform(pts[:, 1].min(), pts[:, 1].max()))
        cv2.circle(out, (cx, cy), int(rng.integers(3, 7)), (70, 72, 74), -1,
                   cv2.LINE_AA)
    return out


def resize_cover(image: np.ndarray, frame_size: Tuple[int, int]) -> np.ndarray:
    """Вписывает изображение в кадр с обрезкой (для негативных примеров)."""
    fw, fh = frame_size
    height, width = image.shape[:2]
    scale = max(fw / width, fh / height)
    resized = cv2.resize(image, (max(1, int(round(width * scale))),
                                 max(1, int(round(height * scale)))),
                         interpolation=cv2.INTER_AREA)
    x0 = max(0, (resized.shape[1] - fw) // 2)
    y0 = max(0, (resized.shape[0] - fh) // 2)
    return resized[y0:y0 + fh, x0:x0 + fw].copy()


def make_negative_images(seed: int = SEED) -> Dict[str, np.ndarray]:
    """Детерминированные "случайные фото": шум, текстура, градиент."""
    rng = np.random.default_rng(seed)
    fw, fh = FRAME_SIZE
    result: Dict[str, np.ndarray] = {}

    # Много­масштабный шум: гладкие пятна + мелкое зерно (не однородная заливка).
    coarse = rng.normal(120.0, 50.0, (max(2, fh // 8), max(2, fw // 8), 3))
    coarse = cv2.resize(coarse, (fw, fh), interpolation=cv2.INTER_CUBIC)
    noise = coarse + rng.normal(0.0, 12.0, (fh, fw, 3))
    result["noise"] = np.clip(noise, 0, 255).astype(np.uint8)

    texture = rng.integers(0, 255, (fh, fw, 3), dtype=np.uint8)
    for _ in range(40):
        color = tuple(int(v) for v in rng.integers(0, 255, 3))
        p1 = (int(rng.integers(0, fw)), int(rng.integers(0, fh)))
        p2 = (int(rng.integers(0, fw)), int(rng.integers(0, fh)))
        cv2.line(texture, p1, p2, color, int(rng.integers(1, 9)), cv2.LINE_AA)
    result["texture"] = cv2.GaussianBlur(texture, (0, 0), 1.2)

    gradient = np.zeros((fh, fw, 3), np.uint8)
    for x in range(fw):
        gradient[:, x] = (int(255 * x / fw), 60, int(255 * (1 - x / fw)))
    result["gradient"] = cv2.GaussianBlur(gradient, (0, 0), 2.0)
    return result


def save_frame(path: str | Path, frame: np.ndarray) -> None:
    save_image_unicode(path, frame)


__all__ = [
    "FRAME_SIZE", "SEED", "load_layout", "load_image_unicode", "sample_homography",
    "render_frame", "make_case", "project_points", "corner_errors", "board_size",
    "remove_markers", "occlude_third", "resize_cover", "make_negative_images",
    "save_frame",
]
