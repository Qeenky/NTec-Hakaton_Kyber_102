"""Unicode-safe чтение/запись файлов для OpenCV (Windows + кириллица)."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np


def imread(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    """cv2.imread, который работает с не-ASCII путями (через np.fromfile)."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite(path: str | Path, image: np.ndarray,
            params: Optional[Sequence[int]] = None) -> bool:
    """cv2.imwrite для не-ASCII путей."""
    suffix = Path(path).suffix or ".png"
    ok, buffer = cv2.imencode(suffix, image, list(params or []))
    if not ok:
        return False
    buffer.tofile(str(path))
    return True


def ascii_temp_copy(path: str | Path, suffix: str = "") -> tuple[str, Optional[Path]]:
    """Копирует файл во временный ASCII-путь, если в пути есть не-ASCII.

    Возвращает (путь_для_OpenCV, временный_файл_или_None).
    """
    source = Path(path)
    if all(ord(char) < 128 for char in str(source.resolve())):
        return str(source), None
    handle = tempfile.NamedTemporaryFile(prefix="ar_board_", suffix=suffix or source.suffix,
                                         delete=False)
    handle.close()
    temp = Path(handle.name)
    shutil.copyfile(source, temp)
    return str(temp), temp
