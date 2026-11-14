"""Демо без телефона: шлёт кадры планшета в `/ws/frame` и пишет видео.

Скрипт изображает камеру: берёт PNG-планшет, накладывает случайную перспективу
(медленный дрейф + лёгкое дрожание), кодирует JPEG, отправляет по WebSocket и
рисует на кадре то, что вернул сервер: `overlays`, `board`, `ocr`,
`active_diagnostics`. Результат — `demo_ar.mp4` (резервный ролик для защиты).

Пример:

    python scripts/simulate_phone.py \
        --board ../ar_board/out/board_A3.png \
        --equipment PUMP-001 --fps 2 --duration 120 --save demo_ar.mp4

Скрипт не требует камеры, телефона и интернета: только локальный сервер.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:  # websockets 13.x
    import websockets
except ImportError as exc:  # pragma: no cover - зависимость backend
    raise SystemExit("Нужен пакет websockets: pip install websockets") from exc

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_BOARD = PROJECT_DIR / "ar_board" / "out" / "board_A3.png"

# Цвета оверлеев (BGR) по статусу узла из ответа сервера.
STATUS_COLORS: dict[str, tuple[int, int, int]] = {
    "ok": (120, 210, 120),
    "check_required": (60, 210, 250),
    "possible_defect": (70, 70, 235),
    "insufficient_data": (170, 170, 170),
}

# Цвета маркеров диагностики из `active_diagnostics[].overlay.color`.
DIAG_COLORS: dict[str, tuple[int, int, int]] = {
    "red": (60, 60, 235),
    "orange": (40, 165, 255),
    "yellow": (60, 210, 250),
    "blue": (235, 170, 90),
    "green": (120, 210, 120),
}

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

# OpenCV putText не умеет кириллицу — резервная транслитерация.
_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
        "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M",
        "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
        "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Sch",
        "Ы": "Y", "Э": "E", "Ю": "Yu", "Я": "Ya",
    }
)


def translit(text: str) -> str:
    return str(text).translate(_TRANSLIT)


def imread_unicode(path: Path) -> np.ndarray | None:
    """cv2.imread, устойчивый к кириллице в пути (Windows/OpenCV ANSI)."""
    try:
        raw = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(raw, cv2.IMREAD_COLOR)


def _font(size: int):
    """PIL-шрифт с кириллицей (или None — тогда рисуем через cv2)."""
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return None


def draw_label(
    frame: np.ndarray,
    text: str,
    origin: tuple[int, int],
    color: tuple[int, int, int] = (240, 240, 240),
    font_size: int = 20,
    pad: int = 4,
) -> None:
    """Подпись с кириллицей: PIL, при отсутствии шрифта — cv2 + транслит."""
    from PIL import Image, ImageDraw

    font = _font(font_size)
    if font is None:
        cv2.putText(
            frame,
            translit(text),
            origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_size / 32.0,
            color,
            1,
            cv2.LINE_AA,
        )
        return

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image, "RGBA")
    x, y = origin
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    width, height = right - left, bottom - top
    draw.rectangle(
        (x - pad, y - pad, x + width + pad, y + height + pad + 2),
        fill=(16, 18, 22, 190),
    )
    draw.text((x, y - top), text, font=font, fill=(color[2], color[1], color[0], 255))
    frame[:] = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


@dataclass
class PhoneSimulator:
    """Генератор «камеры»: перспектива планшета + отправка кадров + отрисовка."""

    board_path: Path
    url: str
    equipment_id: str
    width: int = 1280
    height: int = 720
    fps: float = 2.0
    jpeg_quality: int = 80
    seed: int = 7
    drift: float = 0.015
    jitter: float = 0.004
    show_hud: bool = True

    board: np.ndarray = field(init=False)
    latencies: list[float] = field(default_factory=list)
    board_detected: int = 0
    frames_sent: int = 0
    ocr_values: list[float] = field(default_factory=list)
    diagnostics_seen: dict[str, int] = field(default_factory=dict)
    last_response: dict[str, Any] | None = None
    started_at: float = field(default_factory=time.perf_counter)

    def __post_init__(self) -> None:
        image = imread_unicode(self.board_path)
        if image is None:
            raise SystemExit(f"Не читается планшет: {self.board_path}")
        # Планшет A3 300 dpi (4961x3508) — для видео достаточно 1920 по ширине.
        if image.shape[1] > 1920:
            scale = 1920.0 / image.shape[1]
            image = cv2.resize(
                image,
                (int(image.shape[1] * scale), int(image.shape[0] * scale)),
                interpolation=cv2.INTER_AREA,
            )
        self.board = image

    # Кадр

    def _quad(self, t: float, rng: np.random.Generator) -> np.ndarray:
        """Положение планшета в кадре: медленный дрейф + дрожание камеры."""
        w, h = float(self.width), float(self.height)
        phase = 2.0 * math.pi * t / 9.0
        dx = self.drift * math.sin(phase) + float(rng.normal(0.0, self.jitter))
        dy = self.drift * 0.6 * math.cos(phase * 0.7) + float(rng.normal(0.0, self.jitter))
        rot = 0.35 * math.sin(phase * 0.5) + float(rng.normal(0.0, 0.10))
        scale = 1.0 + 0.02 * math.sin(phase * 1.3)

        # Базовый прямоугольник с полями, затем поворот вокруг центра.
        margin_x, margin_y = 0.045, 0.055
        left, right = w * (margin_x + dx), w * (1.0 - margin_x + dx)
        top, bottom = h * (margin_y + dy), h * (1.0 - margin_y + dy)
        base = np.array(
            [[left, top], [right, top], [right, bottom], [left, bottom]], dtype=np.float64
        )
        center = base.mean(axis=0)
        base = center + (base - center) * scale
        angle = math.radians(rot)
        rotation = np.array(
            [[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]]
        )
        quad = (base - center) @ rotation.T + center
        # Небольшая перспектива: верх/низ слегка расходятся.
        quad[0] += (quad[0] - center) * 0.004 * math.sin(phase)
        quad[2] += (quad[2] - center) * 0.004 * math.cos(phase)
        return quad.astype(np.float32)

    def render(self, t: float, rng: np.random.Generator) -> np.ndarray:
        """Кадр «камеры»: планшет в перспективе на тёмном фоне."""
        height, width = self.board.shape[:2]
        source = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
        matrix = cv2.getPerspectiveTransform(source, self._quad(t, rng))
        frame = np.full((self.height, self.width, 3), (26, 28, 32), dtype=np.uint8)
        cv2.warpPerspective(
            self.board,
            matrix,
            (self.width, self.height),
            dst=frame,
            borderMode=cv2.BORDER_TRANSPARENT,
        )
        # Лёгкий шум сенсора — проверка устойчивости детектора.
        noise = rng.normal(0.0, 1.2, frame.shape)
        noisy = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        return noisy

    # Отрисовка ответа

    def draw_response(self, frame: np.ndarray, response: dict[str, Any]) -> np.ndarray:
        out = frame.copy()
        polygons: dict[str, np.ndarray] = {}
        for overlay in response.get("overlays") or []:
            polygon = overlay.get("polygon") or []
            if len(polygon) < 3:
                continue
            points = np.asarray(polygon, dtype=np.int32).reshape(-1, 1, 2)
            polygons[str(overlay.get("component_id"))] = points
            color = STATUS_COLORS.get(str(overlay.get("color")), (200, 200, 200))
            if not overlay.get("visible", True):
                color = (120, 120, 120)
            tint = out.copy()
            cv2.fillPoly(tint, [points], color)
            out = cv2.addWeighted(tint, 0.18, out, 0.82, 0.0)
            thickness = 3 if overlay.get("visible", True) else 1
            cv2.polylines(out, [points], True, (12, 14, 18), thickness + 2, cv2.LINE_AA)
            cv2.polylines(out, [points], True, color, thickness, cv2.LINE_AA)

            label = str(overlay.get("label") or overlay.get("component_id"))
            status = str(overlay.get("status") or "")
            text = f"{label}: {status}"
            x = int(points[:, 0, 0].min())
            y = int(points[:, 0, 1].min()) - 26
            draw_label(out, text, (max(4, x), max(24, y)), color, font_size=18)

        # Маркеры активной диагностики (active_diagnostics[].overlay) — их рисует
        # и мобильный клиент: подпись правила на узле цветом severity.
        for diagnostic in response.get("active_diagnostics") or []:
            marker = diagnostic.get("overlay") or {}
            points = polygons.get(str(marker.get("component_id")))
            if points is None or len(points) < 3:
                continue
            color = DIAG_COLORS.get(str(marker.get("color")), (40, 165, 255))
            kind = str(marker.get("marker") or "badge")
            if kind in {"outline", "highlight"}:
                tint = out.copy()
                cv2.fillPoly(tint, [points], color)
                out = cv2.addWeighted(tint, 0.25, out, 0.75, 0.0)
                cv2.polylines(out, [points], True, color, 5, cv2.LINE_AA)
            text = str(marker.get("label") or diagnostic.get("title") or "")
            x = int(points[:, 0, 0].min())
            y = int(points[:, 0, 1].max()) + 8
            draw_label(out, f"! {text}", (max(4, x), min(out.shape[0] - 30, y)), color,
                       font_size=20)

        board = response.get("board") or {}
        ocr = response.get("ocr") or {}
        if self.show_hud:
            self._draw_hud(out, response, board, ocr)
        return out

    def _draw_hud(
        self,
        frame: np.ndarray,
        response: dict[str, Any],
        board: dict[str, Any],
        ocr: dict[str, Any],
    ) -> None:
        lines: list[str] = []
        if board.get("detected"):
            lines.append(
                "Планшет: найден, маркеров "
                f"{board.get('markers')}, reproj {board.get('reprojection_error')} px, "
                f"conf {board.get('confidence')}"
            )
        else:
            lines.append(f"Планшет: НЕ найден ({board.get('reason')})")
        if ocr and ocr.get("value") is not None:
            age = ocr.get("age_frames", 0)
            lines.append(
                f"OCR манометра: {ocr.get('value')} {ocr.get('unit', '')} "
                f"(conf {ocr.get('confidence')}, {age} кадр. назад)"
            )
        elif ocr and ocr.get("error"):
            lines.append(f"OCR манометра: нет значения ({ocr.get('error')})")
        else:
            lines.append("OCR манометра: ещё не читался")

        elapsed = max(1e-6, time.perf_counter() - self.started_at)
        lines.append(
            f"FPS {self.frames_sent / elapsed:4.1f}  кадр {response.get('frame_id')}  "
            f"inference {response.get('inference_ms')} мс"
        )

        active = response.get("active_diagnostics") or []
        if active:
            lines.append(f"Активная диагностика ({len(active)}):")
            for item in active[:4]:
                extra = ""
                for evidence in item.get("evidence") or []:
                    if evidence.get("op") == "deviation_pct" and evidence.get("actual") is not None:
                        extra = (
                            f" — расхождение {float(evidence['actual']):.1f}%"
                            f" (порог {evidence.get('threshold')}%)"
                        )
                lines.append(f"  [{item.get('severity')}] {item.get('title')}{extra}")
        else:
            lines.append("Активной диагностики нет")

        height = frame.shape[0]
        y = 14
        for index, line in enumerate(lines):
            size = 20 if index == 0 else 17
            color = (235, 235, 235)
            if line.startswith("  ["):
                color = (90, 160, 255) if "critical" in line else (90, 210, 250)
            elif "НЕ найден" in line:
                color = (90, 90, 240)
            draw_label(frame, line, (14, y), color, font_size=size)
            _, top, _, bottom = _text_bbox(line, size)
            y += (bottom - top) + 14


def _text_bbox(text: str, size: int) -> tuple[int, int, int, int]:
    font = _font(size)
    if font is None:
        return 0, 0, len(text) * int(size * 0.55), size
    from PIL import ImageDraw, Image

    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    return draw.textbbox((0, 0), text, font=font)


async def set_scenario(base_url: str, equipment_id: str, scenario: str) -> None:
    """Переключает сценарий симулятора телеметрии (локальный HTTP)."""
    body = json.dumps({"equipment_id": equipment_id, "scenario": scenario}).encode()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/sim/scenario",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
        print(f"Сценарий: {scenario}")
    except (urllib.error.URLError, OSError) as exc:
        print(f"Не удалось переключить сценарий: {exc}")


async def run(args: argparse.Namespace) -> int:
    simulator = PhoneSimulator(
        board_path=args.board,
        url=args.url,
        equipment_id=args.equipment,
        width=args.width,
        height=args.height,
        fps=args.fps,
        jpeg_quality=args.quality,
        seed=args.seed,
    )
    if args.scenario:
        await set_scenario(args.api, args.equipment, args.scenario)

    rng = np.random.default_rng(args.seed)
    target = f"{args.url}?equipment_id={args.equipment}"
    writer = None
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(args.save), fourcc, float(args.fps), (args.width, args.height))
        if not writer.isOpened():
            print(f"Не удалось открыть VideoWriter для {args.save}", file=sys.stderr)
            writer = None

    period = 1.0 / max(0.1, float(args.fps))
    deadline = time.perf_counter() + float(args.duration)
    try:
        async with websockets.connect(target, max_size=16 * 1024 * 1024) as socket:
            while time.perf_counter() < deadline:
                frame_started = time.perf_counter()
                elapsed = frame_started - simulator.started_at
                frame = simulator.render(elapsed, rng)
                ok, encoded = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(args.quality)]
                )
                if not ok:
                    continue

                sent_at = time.perf_counter()
                await socket.send(encoded.tobytes())
                raw = await socket.recv()
                latency = (time.perf_counter() - sent_at) * 1000.0
                response = json.loads(raw)
                simulator.latencies.append(latency)
                simulator.frames_sent += 1

                if response.get("board", {}).get("detected"):
                    simulator.board_detected += 1
                ocr = response.get("ocr") or {}
                if ocr.get("refreshed") and ocr.get("value") is not None:
                    simulator.ocr_values.append(float(ocr["value"]))
                for item in response.get("active_diagnostics") or []:
                    rule_id = str(item.get("rule_id"))
                    simulator.diagnostics_seen[rule_id] = (
                        simulator.diagnostics_seen.get(rule_id, 0) + 1
                    )
                simulator.last_response = response

                if writer is not None:
                    writer.write(simulator.draw_response(frame, response))
                if args.show and args.show_every and simulator.frames_sent % args.show_every == 0:
                    print(
                        f"кадр {simulator.frames_sent}: latency {latency:.0f} мс, "
                        f"board={response.get('board', {}).get('detected')}, "
                        f"ocr={(response.get('ocr') or {}).get('value')}"
                    )

                rest = period - (time.perf_counter() - frame_started)
                if rest > 0:
                    await asyncio.sleep(rest)
    except (OSError, websockets.WebSocketException) as exc:
        print(
            f"Не удалось подключиться к {target}: {exc}\n"
            "Убедитесь, что uvicorn запущен: uvicorn app.main:app --port 8000",
            file=sys.stderr,
        )
        if writer is not None:
            writer.release()
        return 1
    finally:
        if writer is not None:
            writer.release()

    report(simulator, args)
    return 0


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))
    return ordered[index]


def report(simulator: PhoneSimulator, args: argparse.Namespace) -> None:
    """Итоговая статистика демо-прогона."""
    elapsed = time.perf_counter() - simulator.started_at
    send_fps = simulator.frames_sent / max(1e-6, elapsed)
    share = 100.0 * simulator.board_detected / max(1, simulator.frames_sent)
    p50 = _percentile(simulator.latencies, 0.50)
    p95 = _percentile(simulator.latencies, 0.95)
    ocr_text = "нет"
    if simulator.ocr_values:
        ocr_text = (
            f"{min(simulator.ocr_values):.2f}..{max(simulator.ocr_values):.2f} "
            f"(median {statistics.median(simulator.ocr_values):.2f}, "
            f"замеров {len(simulator.ocr_values)})"
        )

    print("\n=== Итоги демо ===")
    print(f"Кадров отправлено: {simulator.frames_sent} за {elapsed:.1f} с")
    print(f"FPS отправки: {send_fps:.2f} (цель {args.fps})")
    print(f"Задержка ответа: p50 {p50:.0f} мс, p95 {p95:.0f} мс")
    print(f"Кадров с детекцией планшета: {share:.1f} %")
    print(f"OCR манометра, бар: {ocr_text}")
    print(
        "Активная диагностика по кадрам: "
        + (
            json.dumps(simulator.diagnostics_seen, ensure_ascii=False)
            if simulator.diagnostics_seen
            else "нет"
        )
    )
    last = simulator.last_response or {}
    print(f"Последний inference_ms: {last.get('inference_ms')}")
    if args.save and args.save.exists():
        size_mb = args.save.stat().st_size / (1024 * 1024)
        print(f"Видео: {args.save} ({size_mb:.2f} МБ, mp4v)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Имитация телефона: кадры AR-планшета в /ws/frame + видео"
    )
    parser.add_argument("--board", type=Path, default=DEFAULT_BOARD, help="PNG/JPEG планшета")
    parser.add_argument("--equipment", default="PUMP-001", help="id агрегата")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/frame", help="WebSocket /ws/frame")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="HTTP API (сценарий)")
    parser.add_argument("--fps", type=float, default=2.0, help="кадров в секунду")
    parser.add_argument("--duration", type=float, default=120.0, help="длительность, с")
    parser.add_argument("--save", type=Path, default=None, help="куда писать mp4")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--quality", type=int, default=80, help="JPEG quality 1..100")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--scenario",
        default=None,
        help="переключить сценарий симулятора перед стартом (например gauge_drift)",
    )
    parser.add_argument("--show", action="store_true", help="печатать прогресс")
    parser.add_argument("--show-every", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
