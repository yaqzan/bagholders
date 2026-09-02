"""Phase 3d-A: faithfully re-score the universe under a tail config and dump (symbol,date,overall).

The score-stage tail flags (CONT_BOOST_ENABLED / EARN_BOOST_ENABLED / WVD_WAVE_ENABLED /
DAILY_VOLUME_AUTHORITY_WAVE_ENABLED) are read from env by scoring.py at import, so each
config (lean / +cont / +earn / +wvd / +dailyvol / full) is just a different env set passed
by the queue. ScoreSimulator re-scores in memory (no DB writes).

Output parquet has column `overall` (the re-scored value) -> feed to the MC harness with
MOM_SCORE_COL=overall, and to verify_scorecard for the cheap apex-EV read.

Env: DUMP_OUT (required), DUMP_LOOKBACK (default 3650), DUMP_SYMS (optional comma list for smoke).
"""
import os
import sys
from datetime import date

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from simulator import ScoreSimulator

# Echo the tail-flag config so the run.log records what was scored.
for f in ("CONT_BOOST_ENABLED", "EARN_BOOST_ENABLED", "WVD_WAVE_ENABLED",
          "DAILY_VOLUME_AUTHORITY_WAVE_ENABLED"):
    print(f"  [config] {f}={os.environ.get(f, '(default)')}", flush=True)


def main():
    out = os.environ["DUMP_OUT"]
    lookback = int(os.environ.get("DUMP_LOOKBACK", "3650"))
    syms = os.environ.get("DUMP_SYMS", "").strip()
    symbols = [s.strip() for s in syms.split(",")] if syms else None
    stride = int(os.environ.get("DUMP_SYM_STRIDE", "0"))
    if stride > 1 and not symbols:
        from database.models.core import Stock
        alls = [s.symbol for s in Stock.select().order_by(Stock.symbol)]
        symbols = alls[::stride]
        print(f"  [sample] stride {stride} -> {len(symbols)} of {len(alls)} symbols", flush=True)

    sim = ScoreSimulator(symbols=symbols, lookback_days=lookback)
    scores = sim.simulate(since=date(2016, 1, 1))
    syms_o, dts_o, ov_o = [], [], []
    for k, o in scores.items():
        if o is None:
            continue
        oi = int(round(float(o)))
        if oi < 60:
            continue  # only the tradable-relevant band matters for the override/apex-EV
        s, d = k
        syms_o.append(s)
        dts_o.append(d.isoformat() if hasattr(d, "isoformat") else str(d))
        ov_o.append(oi)
    df = pl.DataFrame({"symbol": syms_o, "date": dts_o, "overall": ov_o})
    df.write_parquet(out)
    print(f"\ndumped {df.height:,} rows (overall>=60) -> {out}", flush=True)
    print(f"  >=75: {(df['overall']>=75).sum():,}", flush=True)


if __name__ == "__main__":
    main()
