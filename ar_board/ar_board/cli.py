"""Командный рантайм: камера ноутбука или видеофайл -> оверлей зон."""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence

import cv2

from .detect import detect_board
from .io_utils import ascii_temp_copy
from .layout import load_layout
from .project import Smoother, project_components, render_overlay


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ar_board.cli",
        description="Детекция ArUco-планшета и проекция зон узлов на кадр.",
    )
    parser.add_argument("--layout", required=True, help="путь к out/layout.json")
    parser.add_argument("--video", default="0",
                        help="индекс камеры (0, 1, ...) или путь к видеофайлу")
    parser.add_argument("--alpha", type=float, default=0.35,
                        help="коэффициент EMA-сглаживания (0..1], по умолчанию 0.35")
    parser.add_argument("--no-smooth", action="store_true", help="отключить сглаживание")
    parser.add_argument("--record", help="сохранить размеченное видео в файл (.mp4)")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="остановиться после N кадров (0 = без ограничения)")
    parser.add_argument("--no-display", action="store_true",
                        help="без окна: только статистика в консоль")
    parser.add_argument("--width", type=int, default=1280, help="ширина камеры")
    parser.add_argument("--height", type=int, default=720, help="высота камеры")
    return parser


def _open_capture(source: str, width: int, height: int
                  ) -> tuple[cv2.VideoCapture, Optional[Path]]:
    """Открывает камеру (с фолбэком на DSHOW) или видеофайл.

    Для не-ASCII путей (Windows + кириллица) файл копируется во временный.
    """
    is_camera = source.isdigit()
    if is_camera:
        cap = cv2.VideoCapture(int(source))
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        return cap, None

    path = Path(source)
    if not path.is_file():
        return cv2.VideoCapture(), None
    open_path, temp = ascii_temp_copy(path)
    return cv2.VideoCapture(open_path), temp


def _record_targets(target: str) -> tuple[Path, Optional[Path]]:
    """Путь для VideoWriter и финальный путь (обход не-ASCII в Windows)."""
    final = Path(target)
    if all(ord(char) < 128 for char in str(final.resolve())):
        return final, None
    temp = Path(tempfile.gettempdir()) / f"ar_board_record_{int(time.time() * 1000)}.mp4"
    return temp, final


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    layout_path = Path(args.layout)
    try:
        layout = load_layout(layout_path)
    except (OSError, ValueError) as exc:
        print(f"Ошибка загрузки layout: {exc}", file=sys.stderr)
        return 2
    if not layout.get("components"):
        print("В layout.json нет components — рисовать нечего. "
              "Запустите annotate.py, чтобы разметить зоны.", file=sys.stderr)

    source = str(args.video)
    cap, temp_input = _open_capture(source, args.width, args.height)
    if not cap.isOpened():
        print(f"Не удалось открыть источник видео '{source}'.\n"
              "Проверьте, что камера не занята другим приложением, и разрешите "
              "доступ к ней в настройках Windows.\n"
              "Можно проверить без камеры: python -m ar_board.cli --help",
              file=sys.stderr)
        return 2

    smoother: Optional[Smoother] = None if args.no_smooth else Smoother(alpha=args.alpha)
    writer: Optional[cv2.VideoWriter] = None
    record_write: Optional[Path] = None
    record_final: Optional[Path] = None
    if args.record:
        record_write, record_final = _record_targets(args.record)
    fps = 0.0
    last = time.perf_counter()
    frame_index = 0
    window = "ar_board"

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frame_index += 1

            pose = detect_board(frame, layout)
            projected = project_components(pose, layout)
            if smoother is not None:
                projected = smoother.update(projected)

            now = time.perf_counter()
            dt = max(1e-6, now - last)
            last = now
            instant_fps = 1.0 / dt
            fps = instant_fps if fps == 0.0 else 0.9 * fps + 0.1 * instant_fps

            canvas = render_overlay(frame, projected, pose, fps=fps)

            if record_write is not None:
                if writer is None:
                    h, w = canvas.shape[:2]
                    writer = cv2.VideoWriter(str(record_write),
                                             cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (w, h))
                    if not writer.isOpened():
                        print(f"Не удалось открыть VideoWriter: {record_write}", file=sys.stderr)
                        writer = None
                        record_write = None
                if writer is not None:
                    writer.write(canvas)

            if args.no_display:
                if frame_index % 25 == 0 or args.max_frames:
                    markers = len(pose.marker_ids) if pose else 0
                    err = pose.reprojection_error if pose else float("nan")
                    print(f"frame {frame_index:4d}  fps {fps:5.1f}  markers {markers}  "
                          f"reproj {err:.2f}px  zones {sum(z['visible'] for z in projected)}")
            else:
                try:
                    cv2.imshow(window, canvas)
                    key = cv2.waitKey(1) & 0xFF
                except cv2.error as exc:
                    print("OpenCV-окно недоступно (headless-сборка или нет дисплея). "
                          "Используйте --no-display.\n"
                          f"Детали: {str(exc).splitlines()[0]}", file=sys.stderr)
                    return 3
                if key in (27, ord("q")):
                    break

            if args.max_frames and frame_index >= args.max_frames:
                break
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if record_write is not None and record_final is not None and record_write.exists():
            shutil.move(str(record_write), str(record_final))
            print(f"Видео сохранено: {record_final}")
        if temp_input is not None and temp_input.exists():
            temp_input.unlink(missing_ok=True)
        if not args.no_display:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
