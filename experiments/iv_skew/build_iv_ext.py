"""Extend the IV/skew panel PAST the stale 2026-05-15 ledger cap, through today.

WHY: build_iv.py hardcodes CUTOFF="2026-05-15", but the live holdout line
(strategy_config.CALIBRATION_CUTOFF_DATE) is 2026-06-15 and option_prices/price_history
run to 2026-07-06. So every cached ledger LOOKS like data stops in May — the illusion
that tripped us up. This builds a fresh panel for the new window, driven from the LIVE
`scores` table (production version_id=74 = hash f9fb7b934), reusing build_iv's exact
option functions so the schema matches iv_ledger.

TWO regimes in the output (flagged, don't conflate):
  - IN-SAMPLE GAP  (2026-05-16 .. 2026-06-15): below the live cutoff, 15d-fwd RESOLVED
    (today−15td ≈ 6/15). Immediately usable → adds ~1 month of N to every arm's test.
  - TRUE OOS       (> 2026-06-15): genuine holdout, but 15d-fwd P&L is NOT ripe yet
    (won't resolve for ~2-3 weeks). Features present, pnl15 mostly null → re-run later.

Writes .cache/iv_skew/iv_ledger_ext.parquet (NEW file — does NOT touch the shared
iv_ledger). Columns match iv_ledger + {is_oos, pnl15_resolved, version_id}.

    set PYTHONUTF8=1 && python experiments/iv_skew/build_iv_ext.py
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "experiments" / "iv_skew"))

from strategy_config import CALIBRATION_CUTOFF_DATE  # live holdout line = 2026-06-15
from database.trader_database import DB
from build_iv import atm_call, otm_iv, fwd_pnl  # reuse the EXACT option functions

VERSION_ID = 74                      # production (ALGORITHM_VERSION f9fb7b934)
GAP_START = "2026-05-16"             # day after the stale ledger cap
OUT = _ROOT / ".cache" / "iv_skew" / "iv_ledger_ext.parquet"


def main() -> int:
    DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=180000")
    end = DB.execute_sql(
        "SELECT MAX(date) FROM scores WHERE version_id=%s", (VERSION_ID,)).fetchone()[0]
    end = end.isoformat() if hasattr(end, "isoformat") else str(end)[:10]
    # A true 15-trading-day label needs ~15 bars (~23 calendar days) elapsed. Signals after
    # this date have only a PARTIAL forward window -> their pnl15 would be a truncated,
    # mislabeled horizon, so we null it (features stay valid; re-run later to fill it in).
    resolve_asof = (date.fromisoformat(end) - timedelta(days=23)).isoformat()
    print(f"window {GAP_START} .. {end}  (live cutoff={CALIBRATION_CUTOFF_DATE}, version_id={VERSION_ID})", flush=True)
    print(f"pnl15 fully-resolved only for signals <= {resolve_asof} (need ~15 fwd bars)", flush=True)

    cur = DB.execute_sql(
        "SELECT symbol, date, overall FROM scores "
        "WHERE version_id=%s AND overall>=70 AND date BETWEEN %s AND %s ORDER BY date, symbol",
        (VERSION_ID, GAP_START, end))
    sigs = [(s, d.isoformat() if hasattr(d, "isoformat") else str(d)[:10], int(o)) for s, d, o in cur.fetchall()]
    print(f"{len(sigs)} candidate 70+ signals in window", flush=True)

    recs = []
    miss = 0
    t0 = time.time()
    for i, (sym, d, ov) in enumerate(sigs):
        try:
            ac = atm_call(sym, d)
            if not ac or ac[1] is None or ac[2] is None or float(ac[1]) <= 0:
                miss += 1
                continue
            oid, eprice, eiv, close = int(ac[0]), float(ac[1]), float(ac[2]), float(ac[3])
            put_iv = otm_iv(sym, d, "put", close, -0.10)
            call_iv = otm_iv(sym, d, "call", close, 0.10)
            skew = (put_iv - call_iv) if (put_iv is not None and call_iv is not None) else None
            resolved = d <= resolve_asof
            pnl15, pmax, pmin = fwd_pnl(oid, eprice, d) if resolved else (None, None, None)
            recs.append({
                "symbol": sym, "date": d, "overall": ov,
                "atm_iv": round(eiv, 4), "entry_premium": eprice,
                "skew": (round(skew, 4) if skew is not None else None),
                "pnl15": pnl15, "pnl_max": pmax, "pnl_min": pmin,
                "is_oos": d > CALIBRATION_CUTOFF_DATE,
                "pnl15_resolved": resolved,
                "version_id": VERSION_ID,
            })
        except Exception as e:
            miss += 1
            if miss <= 3:
                print("  err", sym, d, repr(e)[:70], flush=True)
        if (i + 1) % 400 == 0:
            print(f"  {i+1}/{len(sigs)} kept={len(recs)} miss={miss} ({time.time()-t0:.0f}s)", flush=True)

    df = pl.DataFrame(recs, infer_schema_length=None)
    df.write_parquet(OUT)
    gap = df.filter(~pl.col("is_oos"))
    oos = df.filter(pl.col("is_oos"))
    print(f"\nwrote {OUT}  N={df.height} (miss={miss})", flush=True)
    print(f"  IN-SAMPLE GAP (<= {CALIBRATION_CUTOFF_DATE}): {gap.height}  "
          f"pnl15 resolved: {gap['pnl15_resolved'].sum()}  -> usable NOW", flush=True)
    print(f"  TRUE OOS      (>  {CALIBRATION_CUTOFF_DATE}): {oos.height}  "
          f"pnl15 resolved: {oos['pnl15_resolved'].sum()}  -> ripens ~mid-July", flush=True)
    if gap.height:
        from scipy.stats import spearmanr
        g = gap.drop_nulls(["skew", "pnl15"])
        if g.height >= 40:
            r, _ = spearmanr(g["skew"].to_numpy(), g["pnl15"].to_numpy())
            print(f"  [gap-month opt_skew read] spearman(skew,pnl15)={r:+.3f} on N={g.height} "
                  f"(does OSK still show in the fresh month?)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
