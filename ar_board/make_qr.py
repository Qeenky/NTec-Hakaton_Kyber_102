"""Отдельная QR-метка оборудования: крупный PNG для печати/показа с экрана.

QR печатается НЕ на планшете (там теперь `--no-qr`), а отдельно: пользователь
наводит камеру на метку — открывается карточка оборудования. Планшет остаётся
«только оборудованием».

Пример:
    python make_qr.py --data PUMP-001 --out qr/PUMP-001.png
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageDraw

from make_board import _font

WHITE = (255, 255, 255)
INK = (28, 36, 46)
MUTED = (108, 118, 130)


def make_qr_png(data: str, out: str | Path, caption: Optional[str] = None,
                min_px: int = 1200, pad_px: int = 120) -> Path:
    """Крупный QR (>= min_px по стороне) на белом поле с подписью снизу."""
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    modules = qr.modules_count + 2 * 4  # + quiet zone (border)
    box = max(4, -(-min_px // modules))  # ceil: гарантируем >= min_px
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_img = qr_img.resize((modules * box, modules * box), Image.NEAREST)

    caption = caption if caption is not None else f"{data} · QR оборудования"
    font = _font(max(28, qr_img.width // 16), bold=True)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    cap_h = int(probe.textbbox((0, 0), caption, font=font)[3]) + 40

    width = qr_img.width + 2 * pad_px
    height = qr_img.height + pad_px + cap_h
    canvas = Image.new("RGB", (width, height), WHITE)
    canvas.paste(qr_img, (pad_px, pad_px))
    draw = ImageDraw.Draw(canvas)
    draw.text((width // 2, qr_img.height + pad_px + cap_h // 2), caption,
              font=font, fill=INK, anchor="mm")

    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, dpi=(300, 300), optimize=True)
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python make_qr.py",
        description="Отдельная QR-метка оборудования (PNG, крупная, с подписью).")
    parser.add_argument("--data", default="PUMP-001", help="содержимое QR")
    parser.add_argument("--out", default="qr/PUMP-001.png", help="куда сохранить PNG")
    parser.add_argument("--caption", default=None,
                        help="подпись под QR (по умолчанию «<data> · QR оборудования»)")
    parser.add_argument("--min-px", type=int, default=1200,
                        help="минимальная сторона QR в пикселях (по умолчанию 1200)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    path = make_qr_png(args.data, args.out, args.caption, args.min_px)
    with Image.open(path) as image:
        print(f"QR: {path}  ({image.width}x{image.height}, данные: {args.data})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
