"""Отправляет тестовый JPEG по WebSocket и печатает ответ сервера.

Пример:
    py scripts/send_test_frame.py --equipment-id PUMP-001
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import websockets

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = BASE_DIR / "data" / "test_frame.jpg"


def ensure_test_image(path: Path) -> Path:
    """Создаёт простой JPEG через Pillow, если файла ещё нет."""
    if path.exists():
        return path

    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (720, 960), (38, 42, 50))
    draw = ImageDraw.Draw(image)

    # Схематичный насосный агрегат: корпус, двигатель, КИП, трубопровод.
    draw.rectangle((60, 110, 380, 520), fill=(82, 96, 112), outline=(150, 170, 190), width=4)
    draw.rectangle((390, 130, 665, 550), fill=(112, 122, 134), outline=(175, 190, 205), width=4)
    draw.rectangle((215, 540, 445, 810), fill=(96, 108, 124), outline=(160, 175, 190), width=4)
    draw.rectangle((420, 470, 635, 750), fill=(120, 132, 146), outline=(180, 195, 210), width=4)
    draw.ellipse((52, 560, 195, 855), fill=(110, 120, 132), outline=(190, 200, 215), width=5)
    draw.ellipse((92, 630, 155, 785), fill=(220, 225, 230), outline=(80, 90, 100), width=3)
    draw.line((580, 100, 580, 900), fill=(70, 80, 92), width=18)
    draw.rectangle((470, 820, 690, 900), fill=(60, 68, 80))

    image.save(path, "JPEG", quality=88)
    print(f"Создан тестовый кадр: {path}")
    return path


async def send_frames(url: str, equipment_id: str, image_path: Path, frames: int) -> None:
    """Отправляет кадры и печатает JSON-ответы."""
    payload = image_path.read_bytes()
    target = f"{url}?equipment_id={equipment_id}"

    async with websockets.connect(target, max_size=16 * 1024 * 1024) as socket:
        for index in range(frames):
            await socket.send(payload)
            raw = await socket.recv()
            response = json.loads(raw)
            print(f"\n--- кадр {index + 1}/{frames} ---")
            print(json.dumps(response, ensure_ascii=False, indent=2))
            if "error" in response:
                raise SystemExit(f"Сервер вернул ошибку: {response['error']}")
            if index < frames - 1:
                await asyncio.sleep(0.3)


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы командной строки."""
    parser = argparse.ArgumentParser(description="Тест WebSocket /ws/frame")
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8000/ws/frame",
        help="WebSocket URL без query-параметров",
    )
    parser.add_argument("--equipment-id", default="PUMP-001")
    parser.add_argument(
        "--image",
        type=Path,
        default=DEFAULT_IMAGE,
        help="JPEG-файл; если отсутствует — будет сгенерирован",
    )
    parser.add_argument("--frames", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    """Точка входа скрипта."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    image_path = ensure_test_image(args.image)
    try:
        asyncio.run(send_frames(args.url, args.equipment_id, image_path, args.frames))
    except (OSError, websockets.WebSocketException) as exc:
        raise SystemExit(
            f"Не удалось подключиться к {args.url}: {exc}\n"
            "Убедитесь, что uvicorn запущен."
        ) from exc


if __name__ == "__main__":
    main()
