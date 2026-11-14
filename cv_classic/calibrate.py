"""Интерактивная калибровка манометра: клик по центру и двум концам шкалы.

    python -m cv_classic.calibrate --image gauge.jpg --out configs/gauge_01.json

Клики мышью (в таком порядке):
    1) центр циферблата (ось стрелки);
    2) точка начала шкалы (значение value_min, обычно "0");
    3) точка конца шкалы (значение value_max).

Клавиши: R — сброс кликов, S — сохранить (если 3 точки), Q/ESC — выход.

Есть безоконный режим (для скриптов/CI):
    python -m cv_classic.calibrate --image g.png --out c.json \\
        --points "340,340 120,560 560,560"
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from .gauge import GaugeConfig, _find_dial, _to_bgr

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

WINDOW = "cv_classic.calibrate"
STEP_NAMES = ("центр", "минимум шкалы (0)", "максимум шкалы")


def point_angle(center: tuple[float, float], pt: tuple[float, float]) -> float:
    """Угол из центра на точку: 0° вправо, против часовой стрелки положительный."""
    ang = math.degrees(math.atan2(center[1] - pt[1], pt[0] - center[0]))
    return float(ang % 360.0)


def estimate_radius(image: np.ndarray, center: tuple[float, float],
                    p_min: tuple[float, float], p_max: tuple[float, float]
                    ) -> tuple[float, str]:
    """Радиус: из Hough (если центр рядом) иначе по кликам (они ~0.8 R)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dial, _ = _find_dial(gray, image, GaugeConfig())
    if dial is not None:
        d = math.hypot(dial.cx - center[0], dial.cy - center[1])
        if d < 0.12 * max(image.shape[:2]):
            return float(dial.r), f"hough(r={dial.r:.1f})"
    far = max(math.hypot(p[0] - center[0], p[1] - center[1])
              for p in (p_min, p_max))
    return float(far * 1.25), "clicks*1.25"


def build_config(image_path: str | Path, points: list[tuple[float, float]],
                 value_min: float = 0.0, value_max: float = 60.0,
                 unit: str = "bar", clockwise: bool = True,
                 radius: float | None = None) -> GaugeConfig:
    """Три клика -> GaugeConfig (углы в абсолютной системе изображения)."""
    if len(points) != 3:
        raise ValueError("нужны ровно 3 точки: центр, min, max")
    center, p_min, p_max = points
    img = _to_bgr(image_path)
    if radius is None:
        radius, _how = estimate_radius(img, center, p_min, p_max)
    a_min = point_angle(center, p_min)
    a_max = point_angle(center, p_max)
    return GaugeConfig(
        angle_min=a_min, angle_max=a_max,
        value_min=float(value_min), value_max=float(value_max), unit=unit,
        clockwise=bool(clockwise), center=(float(center[0]), float(center[1])),
        radius=float(radius), image_path=str(image_path),
    )


def save_config(cfg: GaugeConfig, out: str | Path,
                extra: dict | None = None) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.to_json()
    data["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["schema"] = "cv_classic.gauge_config/v1"
    if extra:
        data.update(extra)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


# Отрисовка / GUI
def draw_overlay(image: np.ndarray, points: list[tuple[float, float]],
                 cfg: GaugeConfig | None = None) -> np.ndarray:
    vis = image.copy()
    colors = [(0, 255, 255), (0, 200, 0), (0, 0, 255)]
    for i, p in enumerate(points):
        c = (int(p[0]), int(p[1]))
        cv2.drawMarker(vis, c, colors[i % 3], cv2.MARKER_CROSS, 24, 2)
        cv2.putText(vis, str(i + 1), (c[0] + 8, c[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, colors[i % 3], 2, cv2.LINE_AA)
    if len(points) >= 1:
        cx, cy = int(points[0][0]), int(points[0][1])
        for p in points[1:]:
            cv2.line(vis, (cx, cy), (int(p[0]), int(p[1])), (255, 0, 255), 1,
                     cv2.LINE_AA)
        if cfg is not None and cfg.radius:
            cv2.circle(vis, (cx, cy), int(cfg.radius), (255, 255, 0), 1,
                       cv2.LINE_AA)
        if cfg is not None:
            cv2.putText(vis, f"min {cfg.angle_min:.1f}deg  max "
                             f"{cfg.angle_max:.1f}deg  cw={cfg.clockwise}",
                        (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                        (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(vis, f"click {len(points) + 1}/3: "
                     f"{STEP_NAMES[min(len(points), 2)]}",
                (12, vis.shape[0] - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 0, 0), 2, cv2.LINE_AA)
    return vis


def run_gui(image_path: str | Path, out: str | Path, value_min: float,
            value_max: float, unit: str, clockwise: bool,
            radius: float | None) -> int:
    img = _to_bgr(image_path)
    h, w = img.shape[:2]
    scale = min(1.0, 1100.0 / max(h, w))
    disp = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1.0 \
        else img.copy()
    state: dict[str, list[tuple[float, float]]] = {"pts": []}
    saved = {"path": None}
    print("Клики: 1) центр  2) начало шкалы  3) конец шкалы. "
          "R — сброс, Q — выход.")

    def redraw() -> np.ndarray:
        cfg = None
        if len(state["pts"]) == 3:
            cfg = build_config(image_path, state["pts"], value_min, value_max,
                               unit, clockwise, radius)
        v = draw_overlay(img, state["pts"], cfg)
        return cv2.resize(v, (disp.shape[1], disp.shape[0])) if scale < 1.0 else v

    def on_mouse(event: int, x: int, y: int, flags: int, param: object) -> None:
        if event != cv2.EVENT_LBUTTONDOWN or len(state["pts"]) >= 3:
            return
        state["pts"].append((x / scale, y / scale))
        if len(state["pts"]) == 3:
            cfg = build_config(image_path, state["pts"], value_min, value_max,
                               unit, clockwise, radius)
            p = save_config(cfg, out, {"calibration_mode": "gui"})
            saved["path"] = p
            print(f"сохранено: {p}  (angle_min={cfg.angle_min:.2f}, "
                  f"angle_max={cfg.angle_max:.2f}, r={cfg.radius:.1f})")

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW, on_mouse)
    try:
        while True:
            cv2.imshow(WINDOW, redraw())
            key = cv2.waitKey(20) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                break
            if key in (ord("r"), ord("R")):
                state["pts"].clear()
                saved["path"] = None
                print("сброшено")
            if key in (13, 10, ord("s"), ord("S")) and len(state["pts"]) == 3 \
                    and saved["path"] is None:
                cfg = build_config(image_path, state["pts"], value_min,
                                   value_max, unit, clockwise, radius)
                saved["path"] = save_config(cfg, out,
                                            {"calibration_mode": "gui"})
                print(f"сохранено: {saved['path']}")
    finally:
        cv2.destroyAllWindows()
    return 0 if saved["path"] is not None else 2


def _parse_points(text: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for token in text.replace(";", " ").split():
        x, y = token.split(",")
        pts.append((float(x), float(y)))
    if len(pts) != 3:
        raise SystemExit("--points: нужны 3 точки вида 'x,y x,y x,y'")
    return pts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Калибровка шкалы манометра по трём кликам")
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--value-min", type=float, default=0.0)
    ap.add_argument("--value-max", type=float, default=60.0)
    ap.add_argument("--unit", default="bar")
    ap.add_argument("--counter-clockwise", action="store_true",
                    help="шкала растёт против часовой стрелки")
    ap.add_argument("--radius", type=float, default=None,
                    help="радиус циферблата в пикселях (по умолчанию авто)")
    ap.add_argument("--points", default="",
                    help="безоконный режим: 'cx,cy minx,miny maxx,maxy'")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    clockwise = not args.counter_clockwise
    if args.points:
        pts = _parse_points(args.points)
        img = _to_bgr(args.image)
        radius, how = (args.radius, "cli") if args.radius else \
            estimate_radius(img, pts[0], pts[1], pts[2])
        cfg = build_config(args.image, pts, args.value_min, args.value_max,
                           args.unit, clockwise, radius)
        path = save_config(cfg, args.out,
                           {"calibration_mode": "points",
                            "radius_source": how})
        dt = time.perf_counter() - t0
        print(f"сохранено: {path}")
        print(f"  center={tuple(round(v, 1) for v in cfg.center)} "
              f"r={cfg.radius:.1f} ({how})")
        print(f"  angle_min={cfg.angle_min:.2f} angle_max={cfg.angle_max:.2f} "
              f"cw={cfg.clockwise} {cfg.value_min:g}..{cfg.value_max:g} "
              f"{cfg.unit}")
        print(f"  время калибровки: {dt:.2f} c")
        return 0
    try:
        return run_gui(args.image, args.out, args.value_min, args.value_max,
                       args.unit, clockwise, args.radius)
    except cv2.error as exc:  # нет дисплея / нет GUI-бэкенда OpenCV
        print(f"не удалось открыть окно OpenCV: {exc}")
        print("используйте безоконный режим --points \"cx,cy minx,miny maxx,maxy\"")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
