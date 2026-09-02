"""DD-primary Bayesian optimization using an experiment smooth Market Wave CSV.

Experiment-only runner. It reuses the existing dual-wave score transform and
portfolio replay engine, but sources the daily wave oscillator from a
`best_wave_history.csv` file produced by `experiments/sector_wave`.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.calendar_drawdown_2020 import bayes_breadth_dual_wave as dual
from experiments.calendar_drawdown_2020 import bayes_breadth_dual_wave_dd as dd


WAVE_HISTORY_CSV: Path | None = None
WAVE_SCORE_COL = "score"


class SmoothMarketWaveSeries(dual.DualWaveSeries):
    @classmethod
    def load(cls, v: dual.DualVariant) -> "SmoothMarketWaveSeries":
        if WAVE_HISTORY_CSV is None:
            raise ValueError("--wave-history-csv is required")
        rows = []
        with WAVE_HISTORY_CSV.open("r", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                raw = r.get(WAVE_SCORE_COL)
                if raw in (None, ""):
                    continue
                wave_score = float(raw)
                rows.append(
                    {
                        "date": date.fromisoformat(str(r["date"])[:10]),
                        "brd": wave_score,
                        # The dual-wave engine uses RSI as a stabilizer. For a
                        # fused Market Wave oscillator, the score is the
                        # consistent 0-100 stabilizer.
                        "rsi": wave_score,
                    }
                )

        rows.sort(key=lambda r: r["date"])
        brds = [r["brd"] for r in rows]
        for i, r in enumerate(rows):
            b = r["brd"]
            win5 = [x for x in brds[max(0, i - 4): i + 1] if x is not None]
            win20 = [x for x in brds[max(0, i - 19): i + 1] if x is not None]
            r["d5"] = b - brds[i - 5] if i >= 5 and b is not None and brds[i - 5] is not None else 0.0
            r["d10"] = b - brds[i - 10] if i >= 10 and b is not None and brds[i - 10] is not None else 0.0
            r["avg5"] = sum(win5) / len(win5) if win5 else 50.0
            r["min20"] = min(win20) if win20 else (b if b is not None else 50.0)

        crash = {}
        bull = {}
        crash_echo = 0.0
        bull_wave = 0.0
        for r in rows:
            crash_seed = dual.crash_seed(r, v)
            relief = dual.repair_relief(r, v)
            crash_echo = dual.clamp(max(crash_seed, crash_echo * v.crash_decay * (1.0 - v.repair_k * relief)))
            bull_seed = dual.bull_seed(r, v)
            bull_wave = dual.clamp(max(bull_seed, bull_wave * v.bull_decay))
            crash[r["date"]] = crash_echo
            bull[r["date"]] = bull_wave
        return cls(rows, crash, bull)


def strip_wrapper_args(argv: list[str]) -> list[str]:
    global WAVE_HISTORY_CSV, WAVE_SCORE_COL

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--wave-history-csv", required=True)
    parser.add_argument("--wave-score-col", default="score")
    known, remaining = parser.parse_known_args(argv[1:])
    WAVE_HISTORY_CSV = Path(known.wave_history_csv).resolve()
    WAVE_SCORE_COL = known.wave_score_col
    return [argv[0], *remaining]


def main() -> int:
    sys.argv = strip_wrapper_args(sys.argv)
    dual.DualWaveSeries = SmoothMarketWaveSeries
    return dd.main()


if __name__ == "__main__":
    raise SystemExit(main())
