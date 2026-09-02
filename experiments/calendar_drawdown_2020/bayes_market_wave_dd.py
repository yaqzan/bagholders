"""DD-primary Bayesian optimization using persisted sector ETF Market Wave.

This is an experiment-only runner. It reuses the existing dual-wave score
transform/replay engine, but sources the wave series from
MarketBreadth.sector_etf_market_wave_score instead of raw sector ETF breadth.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.calendar_drawdown_2020 import bayes_breadth_dual_wave as dual
from experiments.calendar_drawdown_2020 import bayes_breadth_dual_wave_dd as dd
from market_breadth import _load_sector_etf_breadth_rows


class MarketWaveSeries(dual.DualWaveSeries):
    @classmethod
    def load(cls, v: dual.DualVariant) -> "MarketWaveSeries":
        rows = []
        for row in _load_sector_etf_breadth_rows(date.today()):
            if row.get("market_wave_score") is None:
                continue
            wave_score = float(row["market_wave_score"])
            # Treat Market Wave as the breadth oscillator. Keep avg sector RSI
            # as the oversold/overbought stabilizer where available.
            rows.append({
                "date": row["date"],
                "brd": wave_score,
                "rsi": float(row["avg_rsi"]) if row.get("avg_rsi") is not None else wave_score,
            })

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


def main() -> int:
    dual.DualWaveSeries = MarketWaveSeries
    return dd.main()


if __name__ == "__main__":
    raise SystemExit(main())
