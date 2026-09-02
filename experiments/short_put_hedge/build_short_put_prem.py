"""Real <=5 DTE ATM PUT premium calibration on the actual call-signal population.

The short-dated protective-put overlay's whole verdict pivots on the real premium of a
<=3 DTE ATM put vs the engine model (1.82*sqrt(DTE/30)*sigma_daily). The 30DTE anchor
(iv_ledger) shows real/model ~= 1.035 (iv_rv median 1.08 -> options ABOVE realized vol).
This measures the SHORT-DTE put directly: on each 75+ call-signal date (the day we'd buy
the hedge), find the nearest-ATM put with DTE in [1,5], record price, iv, dte, and the
real premium_pct = price/close vs the model premium_pct. If real/model >= 1.0, the gross
overlay sits in the clean-NULL column.

Holdout window is whatever option_prices covers (>=2025-02-10). db=heavy (62M rows);
indexed point lookups only. Queue it.

Run: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/short_put_hedge/build_short_put_prem.py
"""
import os, sys, time, math
import polars as pl

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, _ROOT)
from database.trader_database import DB

COV_START = "2025-02-10"
LED = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
OUT = os.path.join(_ROOT, ".cache", "short_put_hedge")
PREMIUM_MULT_30 = 1.82


def atm_put_short(sym, d):
    """nearest-ATM put with DTE in [1,5]: price, iv, stock close, dte, oi, volume."""
    row = DB.execute_sql(
        "SELECT op.price, op.iv, ph.close, DATEDIFF(o.expiration_date, op.date) AS dte, "
        "op.open_interest, op.volume "
        "FROM options o JOIN option_prices op ON op.option_id=o.id "
        "JOIN price_history ph ON ph.symbol=o.symbol AND ph.date=op.date "
        "WHERE o.symbol=%s AND o.option_type='put' AND op.date=%s "
        "AND DATEDIFF(o.expiration_date, op.date) BETWEEN 1 AND 5 "
        "AND op.price>0 "
        "ORDER BY ABS(o.strike_price - ph.close)/ph.close, "
        "         DATEDIFF(o.expiration_date, op.date) LIMIT 1", (sym, d)).fetchone()
    return row


def main():
    t0 = time.time()
    led = pl.read_parquet(LED)
    win = led.filter((pl.col("date") >= COV_START) & (pl.col("overall") >= 75))
    smoke = int(os.environ.get("SP_SMOKE", "0"))
    if smoke:
        win = win.sort("date").head(smoke)
        print(f"SMOKE: {win.height}", flush=True)
    print(f"covered 75+ call signals: {win.height}", flush=True)
    rows = list(zip(win["symbol"].to_list(), win["date"].to_list(),
                    win["overall"].to_list(), win["vol_pct"].to_list()))
    recs = []; miss = 0
    for i, (sym, d, ov, volp) in enumerate(rows):
        try:
            r = atm_put_short(sym, d)
            if not r or r[0] is None or r[2] is None or float(r[2]) <= 0:
                miss += 1; continue
            price, iv, close, dte = float(r[0]), (float(r[1]) if r[1] is not None else None), float(r[2]), int(r[3])
            oi, vol = (int(r[4]) if r[4] is not None else 0), (int(r[5]) if r[5] is not None else 0)
            sigma = float(volp) / 100.0 if volp else None
            prem_pct = price / close
            model_pct = PREMIUM_MULT_30 * math.sqrt(dte / 30.0) * sigma if sigma else None
            ratio = (prem_pct / model_pct) if (model_pct and model_pct > 0) else None
            recs.append({"symbol": sym, "date": d, "overall": int(ov), "dte": dte,
                         "prem_pct": round(prem_pct, 5), "model_pct": (round(model_pct, 5) if model_pct else None),
                         "real_over_model": (round(ratio, 4) if ratio else None),
                         "iv": (round(iv, 4) if iv else None), "oi": oi, "volume": vol,
                         "sigma_daily": (round(sigma, 5) if sigma else None)})
        except Exception as e:
            miss += 1
            if miss <= 3:
                print("  err", sym, d, repr(e)[:80], flush=True)
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(rows)} ({time.time()-t0:.0f}s) kept={len(recs)} miss={miss}", flush=True)
    print(f"kept={len(recs)} miss={miss} in {time.time()-t0:.0f}s", flush=True)
    os.makedirs(OUT, exist_ok=True)
    df = pl.DataFrame(recs, infer_schema_length=None)
    df.write_parquet(os.path.join(OUT, "short_put_prem.parquet"))
    rr = df["real_over_model"].drop_nulls()
    if rr.len():
        print(f"\n=== REAL <=5DTE ATM PUT premium vs model (1.82*sqrt(DTE/30)*sigma) ===")
        print(f"N={rr.len()}  real/model: p10={rr.quantile(0.1):.3f} p25={rr.quantile(0.25):.3f} "
              f"median={rr.median():.3f} p75={rr.quantile(0.75):.3f} mean={rr.mean():.3f}")
        print(f"  P(real>=model) = {(rr>=1.0).mean():.3f}   (>=1.0 => gross overlay in the NULL column)")
        for dte in (1, 2, 3, 4, 5):
            s = df.filter(pl.col("dte") == dte)["real_over_model"].drop_nulls()
            if s.len():
                print(f"  dte={dte}: N={s.len():5d} real/model median={s.median():.3f} mean={s.mean():.3f} "
                      f"medOI={df.filter(pl.col('dte')==dte)['oi'].median()} "
                      f"medVol={df.filter(pl.col('dte')==dte)['volume'].median()}")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
