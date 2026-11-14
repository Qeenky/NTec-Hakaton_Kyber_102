"""Оценка read_gauge на синтетическом датасете.

    python cv_classic/scripts/eval_synthetic.py
    python cv_classic/scripts/eval_synthetic.py --auto-orient   # проверка поворота

Цели: успех >= 85%, MAE <= 3% диапазона, грубые промахи (>= 10% диапазона) <= 10%.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cv_classic.gauge import GaugeConfig, read_gauge  # noqa: E402

try:  # консоль Windows может быть в cp866
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

SUCCESS_PCT = 5.0   # "успешно", если ошибка <= 5% диапазона
GROSS_PCT = 10.0    # грубый промах
TARGET_SUCCESS = 85.0
TARGET_MAE = 3.0
TARGET_GROSS = 10.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "cv_classic" / "data" / "synthetic"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--auto-orient", action="store_true",
                    help="один канонический конфиг + компенсация поворота")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args(argv)

    data = Path(args.data)
    truth = json.loads((data / "truth.json").read_text(encoding="utf-8"))["items"]
    if args.limit:
        truth = truth[:args.limit]

    rows = []
    t_start = time.perf_counter()
    for item in truth:
        if args.auto_orient:
            cfg = GaugeConfig(angle_min=225.0, angle_max=-45.0,
                              value_min=item["value_min"],
                              value_max=item["value_max"], unit=item["unit"],
                              auto_orient=True)
        else:
            cfg = GaugeConfig(angle_min=item["angle_min"],
                              angle_max=item["angle_max"],
                              value_min=item["value_min"],
                              value_max=item["value_max"], unit=item["unit"])
        res = read_gauge(data / item["file"], cfg)
        rng = item["value_max"] - item["value_min"]
        if res.value is None:
            err_pct = 100.0
            err_abs = rng
        else:
            err_abs = abs(res.value - item["value"])
            err_pct = err_abs / rng * 100.0
        rows.append({
            "file": item["file"], "truth": item["value"],
            "read": res.value, "unit": item["unit"], "err_pct": err_pct,
            "error": res.error, "conf": res.confidence,
            "method": res.method, "angle": res.angle_deg,
            "truth_angle": item["angle_deg"], "ms": res.debug.get("timing_ms"),
        })
    elapsed = time.perf_counter() - t_start

    hdr = f"{'файл':<15}{'истина':>9}{'распознано':>12}{'ошибка%':>9}{'conf':>7}{'метод':<18}"
    print(hdr)
    print("-" * 72)
    for r in rows:
        val = "—" if r["read"] is None else f"{r['read']:.2f}"
        flag = ""
        if r["error"]:
            flag = f" [{r['error']}]"
        elif r["err_pct"] > GROSS_PCT:
            flag = " [промах]"
        print(f"{r['file']:<15}{r['truth']:>9.2f}{val:>12}{r['err_pct']:>9.2f}"
              f"{r['conf']:>7.2f}{r['method']:<18}{flag}")

    n = len(rows)
    success = sum(1 for r in rows if r["error"] is None and r["err_pct"] <= SUCCESS_PCT)
    gross = sum(1 for r in rows if r["error"] is not None or r["err_pct"] > GROSS_PCT)
    mae = statistics.fmean(r["err_pct"] for r in rows)
    ok_errs = [r["err_pct"] for r in rows if r["error"] is None]
    mae_ok = statistics.fmean(ok_errs) if ok_errs else 0.0
    succ_pct = 100.0 * success / n
    gross_pct = 100.0 * gross / n
    conf = statistics.fmean(r["conf"] for r in rows)
    ms_vals = [r["ms"] for r in rows if r["ms"]]
    ms = statistics.fmean(ms_vals) if ms_vals else 0.0

    print("-" * 72)
    print(f"изображений: {n}   время: {elapsed:.1f} c ({ms:.0f} мс/шт)")
    print(f"успех (ошибка <= {SUCCESS_PCT:.0f}% диапазона): {success}/{n} = {succ_pct:.1f}%"
          f"   цель >= {TARGET_SUCCESS:.0f}%  -> "
          f"{'OK' if succ_pct >= TARGET_SUCCESS else 'FAIL'}")
    print(f"MAE (неудачи = 100% диапазона): {mae:.2f}%   "
          f"MAE только по успешным: {mae_ok:.2f}%   цель <= {TARGET_MAE:.0f}%  -> "
          f"{'OK' if mae <= TARGET_MAE else 'FAIL'}")
    print(f"грубые промахи (>{GROSS_PCT:.0f}% диапазона): {gross}/{n} = {gross_pct:.1f}%"
          f"   цель <= {TARGET_GROSS:.0f}%  -> "
          f"{'OK' if gross_pct <= TARGET_GROSS else 'FAIL'}")
    print(f"средняя confidence: {conf:.2f}")
    mode = "auto-orient (один конфиг)" if args.auto_orient else "по-приборный конфиг"
    print(f"режим: {mode}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"rows": rows, "success_pct": succ_pct, "mae_pct": mae,
             "mae_ok_pct": mae_ok, "gross_pct": gross_pct, "mode": mode},
            ensure_ascii=False, indent=1), encoding="utf-8")
    ok = (succ_pct >= TARGET_SUCCESS and mae <= TARGET_MAE and
          gross_pct <= TARGET_GROSS)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
