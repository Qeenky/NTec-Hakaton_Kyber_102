"""anomaly/v2 — детектор дефектов на канонических кропах (AR-выравнивание).

Модули:
    canonical      — выравнивание зон планшета по ArUco-гомографии
    synth_defects  — синтез дефектов на чистых кропах + сборка парного датасета
    detector       — PatchCore с раздельными банками, top-k/area, TTA
    experiments    — аблации, проверка на реальных кадрах, report.json
    visualize      — визуализации для отчёта
"""

from __future__ import annotations

__version__ = "2.0"
