"""Relative-strength (vs SPY) W1 feature build.
The score is single-stock-ABSOLUTE (trend = price vs own EMAs). It has NO relative-strength
feature. RS vs SPY is orthogonal momentum. Question: does RS add OPTION-barrier info BEYOND overall?

Reuse the existing component_reweight 60-99 CALL ledger (symbol,date,overall,t_up,t_dn — outcomes
already walked) and ADD per-signal trailing relative strength vs SPY:
  rs20 = stock_ret_20bar - spy_ret_20bar ; rs60 = stock_ret_60bar - spy_ret_60bar
  plus stock_ret_20/60 (own momentum) and spy_ret_20/60 (market) so we can isolate the RELATIVE part.
No forward walk (outcomes come from the ledger). Holdout-locked (ledger is <=2026-05-15). db=light read.
"""
import os, sys, time
import numpy as np
import polars as pl

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)
from database.trader_database import DB
from database.models.technical import PriceHistory

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
OUT = os.path.join(_ROOT, ".cache", "rel_strength")


def closes(sym):
    rs = list(PriceHistory.select(PriceHistory.date, PriceHistory.close)
              .where(PriceHistory.symbol == sym).order_by(PriceHistory.date))
    dts = [r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date) for r in rs]
    cl = np.array([float(r.close) if r.close else np.nan for r in rs])
    return dts, cl, {d: i for i, d in enumerate(dts)}


def main():
    t0 = time.time()
    led = pl.read_parquet(LEDGER)
    print("ledger N=", led.height, flush=True)
    syms = led["symbol"].unique().to_list()

    # SPY market series
    spy_dts, spy_cl, spy_idx = closes("SPY")
    if not spy_dts:
        print("NO SPY DATA — abort"); return
    print(f"SPY bars={len(spy_dts)}", flush=True)

    def ret_at(cl, idx, d, n):
        i = idx.get(d)
        if i is None or i - n < 0:
            return None
        a, b = cl[i - n], cl[i]
        if not (a and b and a > 0):
            return None
        return b / a - 1.0

    # precompute SPY returns per date
    spy_r20 = {d: ret_at(spy_cl, spy_idx, d, 20) for d in spy_dts}
    spy_r60 = {d: ret_at(spy_cl, spy_idx, d, 60) for d in spy_dts}

    t1 = time.time()
    rs20 = {}; rs60 = {}; sr20 = {}; sr60 = {}; mr20 = {}; mr60 = {}
    rows_by_sym = {}
    for s, d in zip(led["symbol"].to_list(), led["date"].to_list()):
        rows_by_sym.setdefault(s, []).append(d)
    done = 0
    for sym in syms:
        dts, cl, idx = closes(sym)
        if not dts:
            continue
        for d in rows_by_sym.get(sym, []):
            s20 = ret_at(cl, idx, d, 20); s60 = ret_at(cl, idx, d, 60)
            m20 = spy_r20.get(d); m60 = spy_r60.get(d)
            key = (sym, d)
            sr20[key] = s20; sr60[key] = s60; mr20[key] = m20; mr60[key] = m60
            rs20[key] = (s20 - m20) if (s20 is not None and m20 is not None) else None
            rs60[key] = (s60 - m60) if (s60 is not None and m60 is not None) else None
        done += 1
        if done % 150 == 0:
            print(f"  {done}/{len(syms)} syms ({time.time()-t1:.0f}s)", flush=True)
    print(f"RS compute: {time.time()-t1:.0f}s", flush=True)

    def col(dct):
        return [dct.get((s, d)) for s, d in zip(led["symbol"].to_list(), led["date"].to_list())]
    led = led.with_columns([
        pl.Series("rs20", col(rs20)), pl.Series("rs60", col(rs60)),
        pl.Series("stock_r20", col(sr20)), pl.Series("stock_r60", col(sr60)),
        pl.Series("spy_r20", col(mr20)), pl.Series("spy_r60", col(mr60)),
    ])
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, "rs_ledger.parquet")
    led.write_parquet(out)
    cov = led["rs20"].drop_nulls().len()
    print(f"DONE {out} N={led.height} rs20-coverage={100*cov/led.height:.1f}% time={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
