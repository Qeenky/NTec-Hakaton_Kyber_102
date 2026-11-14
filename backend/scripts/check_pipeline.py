"""Проверка сквозного пайплайна на живом сервере: кадры планшета → ответ.

Что делает:

1. Генерирует N перспектив планшета (`cv2.warpPerspective`, как камера телефона)
   и отправляет их в `/ws/frame`; печатает таблицу по кадрам.
2. Считает p50/p95 задержки ответа, p50/p95 `inference_ms` и фактический FPS.
3. Проверяет кадр без маркеров (`board.detected=false`).
4. Проверяет правило `gauge_drift`: сначала сценарий `normal` (OCR совпадает с
   датчиком → правила нет), затем `gauge_drift` (OCR отходит на 22 % → правило
   появляется). Печатает evidence.

Запуск (сервер уже поднят):

    python scripts/check_pipeline.py --frames 10 --check-rules
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

try:
    import websockets
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Нужен пакет websockets") from exc

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_BOARD = PROJECT_DIR / "ar_board" / "out" / "board_A3.png"


def imread_unicode(path: Path) -> np.ndarray:
    raw = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"Не читается планшет: {path}")
    return image


def perspective(board: np.ndarray, index: int, width: int = 1280, height: int = 720) -> np.ndarray:
    """Разные ракурсы: сдвиг, наклон, масштаб — детерминированно по индексу."""
    board_h, board_w = board.shape[:2]
    source = np.float32([[0, 0], [board_w, 0], [board_w, board_h], [0, board_h]])
    phase = index * 0.7
    inset = 0.04 + 0.015 * np.sin(phase)
    tilt = 0.02 * np.cos(phase * 1.3)
    shift = 0.02 * np.sin(phase * 0.8)
    target = np.float32([
        [width * (inset + shift), height * inset],
        [width * (1 - inset + shift), height * (inset + tilt)],
        [width * (1 - inset - tilt), height * (1 - inset)],
        [width * (inset + tilt), height * (1 - inset)],
    ])
    matrix = cv2.getPerspectiveTransform(source, target)
    frame = np.full((height, width, 3), (26, 28, 32), dtype=np.uint8)
    return cv2.warpPerspective(board, matrix, (width, height), dst=frame,
                               borderMode=cv2.BORDER_TRANSPARENT)


def jpeg(frame: np.ndarray, quality: int = 85) -> bytes:
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buffer.tobytes()


def percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def http_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="GET" if payload is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"HTTP {url}: {exc}") from exc


async def send_frames(args: argparse.Namespace, board: np.ndarray) -> list[dict]:
    target = f"{args.url}?equipment_id={args.equipment}"
    rows: list[dict] = []
    latencies: list[float] = []
    async with websockets.connect(target, max_size=16 * 1024 * 1024) as socket:
        # Прогрев: первый кадр на холодном процессе занимает ~1 с (JIT-эффекты
        # импорта/OpenCV), в статистику он не попадает.
        await socket.send(jpeg(perspective(board, 0)))
        await socket.recv()
        for index in range(args.frames):
            frame = perspective(board, index)
            payload = jpeg(frame)
            started = time.perf_counter()
            await socket.send(payload)
            response = json.loads(await socket.recv())
            latency = (time.perf_counter() - started) * 1000.0
            latencies.append(latency)
            rows.append(
                {
                    "frame": index + 1,
                    "detected": response.get("board", {}).get("detected"),
                    "markers": response.get("board", {}).get("markers"),
                    "reproj": response.get("board", {}).get("reprojection_error"),
                    "board_conf": response.get("board", {}).get("confidence"),
                    "overlays": len(response.get("overlays") or []),
                    "ocr": (response.get("ocr") or {}).get("value"),
                    "ocr_conf": (response.get("ocr") or {}).get("confidence"),
                    "inference_ms": response.get("inference_ms"),
                    "latency_ms": round(latency, 1),
                    "active": [item["rule_id"] for item in response.get("active_diagnostics") or []],
                }
            )

    print("\nкадр | планшет | марк. | reproj | conf | overlays | OCR, бар | inference | latency")
    for row in rows:
        print(
            f"{row['frame']:>4} | {str(row['detected']):<7} | {row['markers']:>5} |"
            f" {row['reproj'] if row['reproj'] is not None else '-':>6} |"
            f" {row['board_conf'] if row['board_conf'] is not None else '-':>4} |"
            f" {row['overlays']:>8} | {row['ocr'] if row['ocr'] is not None else '-':>8} |"
            f" {row['inference_ms']:>7} мс | {row['latency_ms']:>6} мс"
        )

    inferences = [float(row["inference_ms"] or 0) for row in rows]
    detected = sum(1 for row in rows if row["detected"])
    ocr_values = [float(row["ocr"]) for row in rows if row["ocr"] is not None]
    print("\n=== Итоги ===")
    print(f"Кадров: {len(rows)}, планшет найден: {detected}/{len(rows)}")
    print(
        f"Задержка ответа: p50 {percentile(latencies, 0.5):.0f} мс, "
        f"p95 {percentile(latencies, 0.95):.0f} мс"
    )
    print(
        f"inference_ms: p50 {percentile(inferences, 0.5):.0f} мс, "
        f"p95 {percentile(inferences, 0.95):.0f} мс, max {max(inferences):.0f} мс"
    )
    print(f"Средний размер overlays: {statistics.mean([row['overlays'] for row in rows]):.1f}")
    if ocr_values:
        print(f"OCR, бар: {min(ocr_values):.2f}..{max(ocr_values):.2f} (n={len(ocr_values)})")
    return rows


async def check_no_markers(args: argparse.Namespace) -> None:
    blank = np.full((720, 1280, 3), 38, dtype=np.uint8)
    target = f"{args.url}?equipment_id={args.equipment}"
    async with websockets.connect(target, max_size=16 * 1024 * 1024) as socket:
        await socket.send(jpeg(blank))
        response = json.loads(await socket.recv())
    board = response.get("board") or {}
    print("\n=== Кадр без маркеров ===")
    print(f"board.detected={board.get('detected')}, reason={board.get('reason')}, "
          f"overlays={len(response.get('overlays') or [])}, error={response.get('error')}")
    assert board.get("detected") is False, "сервер должен вернуть detected=false"


async def check_gauge_rule(args: argparse.Namespace, board: np.ndarray) -> None:
    """normal: OCR совпадает с датчиком; gauge_drift: расходится."""
    print("\n=== Проверка правила gauge_drift ===")
    http_json(f"{args.api}/sim/reset", {})
    http_json(f"{args.api}/sim/scenario", {"equipment_id": args.equipment, "scenario": "normal"})
    await asyncio.sleep(2.0)

    diagnostics, ocr = await warm_ocr(args, board)
    active = {item["rule_id"] for item in diagnostics["active"]}
    blocked = {item["rule_id"]: item["blocked_by"] for item in diagnostics["blocked"]}
    print(
        f"normal: OCR={ocr.get('value')} {ocr.get('unit')} (conf {ocr.get('confidence')}), "
        f"активные правила={sorted(active)}, gauge_drift blocked_by={blocked.get('gauge_drift')}"
    )
    assert ocr.get("value") is not None, "OCR должен прочитать манометр с планшета"
    assert "gauge_drift" not in active, "в норме правило gauge_drift не должно срабатывать"

    http_json(f"{args.api}/sim/scenario",
              {"equipment_id": args.equipment, "scenario": "gauge_drift"})
    deadline = time.perf_counter() + args.rule_wait
    found = False
    while time.perf_counter() < deadline:
        await send_keepalive(args, board, frames=3, delay=0.6)
        diagnostics = http_json(f"{args.api}/equipment/{args.equipment}/diagnostics")
        active = {item["rule_id"] for item in diagnostics["active"]}
        if "gauge_drift" in active:
            found = True
            break
        await asyncio.sleep(1.0)

    diagnostics = http_json(f"{args.api}/equipment/{args.equipment}/diagnostics")
    gauge = next((item for item in diagnostics["active"] if item["rule_id"] == "gauge_drift"), None)
    if found and gauge is not None:
        evidence = gauge["evidence"][0]
        print(
            "gauge_drift: active, confidence "
            f"{gauge['confidence']}, расхождение OCR={evidence.get('actual')}% "
            f"(порог {evidence.get('threshold')}%), датчик={evidence.get('baseline')} бар"
        )
    else:
        print("gauge_drift: НЕ сработало")
    assert found, "gauge_drift должен сработать при расхождении > 15 %"

    # Возврат в норму: правило должно сняться (гистерезис 0.45).
    http_json(f"{args.api}/sim/reset", {})
    await asyncio.sleep(3.0)
    diagnostics = http_json(f"{args.api}/equipment/{args.equipment}/diagnostics")
    resolved = {item["rule_id"] for item in diagnostics["active"]}
    print(f"после reset: активные правила={sorted(resolved)}")


async def warm_ocr(args: argparse.Namespace, board: np.ndarray,
                   timeout: float = 15.0) -> tuple[dict, dict]:
    """Гоняет кадры, пока сервер не опубликует свежее OCR-значение."""
    deadline = time.perf_counter() + timeout
    diagnostics: dict = {}
    ocr: dict = {}
    while time.perf_counter() < deadline:
        await send_keepalive(args, board, frames=4, delay=0.3)
        diagnostics = http_json(f"{args.api}/equipment/{args.equipment}/diagnostics")
        ocr = (diagnostics.get("camera") or {}).get("ocr") or {}
        if ocr.get("value") is not None:
            return diagnostics, ocr
    return diagnostics, ocr


async def send_keepalive(args: argparse.Namespace, board: np.ndarray,
                         frames: int, delay: float) -> None:
    target = f"{args.url}?equipment_id={args.equipment}"
    async with websockets.connect(target, max_size=16 * 1024 * 1024) as socket:
        for index in range(frames):
            await socket.send(jpeg(perspective(board, index)))
            await socket.recv()
            await asyncio.sleep(delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сквозная проверка пайплайна")
    parser.add_argument("--board", type=Path, default=DEFAULT_BOARD)
    parser.add_argument("--equipment", default="PUMP-001")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/frame")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--check-rules", action="store_true", help="проверить gauge_drift")
    parser.add_argument("--rule-wait", type=float, default=30.0,
                        help="сколько ждать срабатывания правила, с")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    board = imread_unicode(args.board)
    asyncio.run(send_frames(args, board))
    asyncio.run(check_no_markers(args))
    if args.check_rules:
        asyncio.run(check_gauge_rule(args, board))


if __name__ == "__main__":
    main()
