"""IV/skew feature + ACTUAL forward option-P&L build for in-window call signals.

Key: the barrier WR (opt15/apex15) is REALIZED-vol-normalized and ignores the PREMIUM (implied vol)
you pay. IV's edge lives there. So the honest outcome is the ACTUAL 15-trading-day option P&L from
option_prices. For each 70+ call signal in the covered window (>=2025-02-10, <=holdout cutoff):
  atm_iv  = IV of nearest-ATM ~30 DTE CALL on signal date
  entry_premium = that call's price
  iv_rv   = atm_iv / annualized realized vol (vol_pct/100 * sqrt(252))   [variance risk premium]
  skew    = (10% OTM put IV) - (10% OTM call IV)                         [crash-pricing / fear]
  pnl15   = (option price ~15 trading days fwd) / entry_premium - 1      [ACTUAL hold P&L]
  pnl_max/pnl_min over the 15d window (for TP/SL context)
  pnl15/pnl_max/pnl_min are NULL when <15 forward bars exist (unresolved horizon is never
  truncate-substituted with the last available price)
Indexed point/range lookups only (no full scans). Holdout-locked. db=heavy (hits 62M option_prices).
"""
import os, sys, time, math
import polars as pl

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, _ROOT)
from database.trader_database import DB

COV_START = "2025-02-10"
CUTOFF = "2026-05-15"
LED = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
OUT = os.path.join(_ROOT, ".cache", "iv_skew")


def one(sql, params):
    cur = DB.execute_sql(sql, params)
    return cur.fetchone()


def atm_call(sym, d):
    # nearest-ATM ~30DTE call: id, strike, price, iv, and the stock close
    row = one(
        "SELECT o.id, op.price, op.iv, ph.close, o.expiration_date "
        "FROM options o JOIN option_prices op ON op.option_id=o.id "
        "JOIN price_history ph ON ph.symbol=o.symbol AND ph.date=op.date "
        "WHERE o.symbol=%s AND o.option_type='call' AND op.date=%s "
        "AND DATEDIFF(o.expiration_date, op.date) BETWEEN 20 AND 45 "
        "AND op.iv BETWEEN 0.05 AND 5.0 AND op.price>0 AND op.open_interest>0 "
        "ORDER BY ABS(o.strike_price - ph.close)/ph.close LIMIT 1", (sym, d))
    return row


def otm_iv(sym, d, otype, close, frac):
    target = close * (1 + frac)  # frac<0 for OTM put, >0 for OTM call
    row = one(
        "SELECT op.iv FROM options o JOIN option_prices op ON op.option_id=o.id "
        "WHERE o.symbol=%s AND o.option_type=%s AND op.date=%s "
        "AND DATEDIFF(o.expiration_date, op.date) BETWEEN 20 AND 45 "
        "AND op.iv BETWEEN 0.05 AND 5.0 AND op.open_interest>0 "
        "ORDER BY ABS(o.strike_price - %s) LIMIT 1", (sym, otype, d, target))
    return float(row[0]) if row and row[0] is not None else None


def fwd_pnl(option_id, entry_price, d):
    # option price path over next ~22 calendar days; 15-trading-day-fwd pnl + max/min
    cur = DB.execute_sql(
        "SELECT date, price FROM option_prices WHERE option_id=%s AND date > %s "
        "AND date <= DATE_ADD(%s, INTERVAL 24 DAY) ORDER BY date", (option_id, d, d))
    rows = cur.fetchall()
    prices = [float(p) for (_, p) in rows if p is not None and float(p) > 0]
    if len(prices) < 15:
        # unresolved 15-bar horizon (signal near pull date / expired or illiquid chain):
        # null the labels, never truncate-substitute the last available price
        return None, None, None
    p15 = prices[14]  # 15th trading bar fwd (0-indexed 14)
    pnl15 = p15 / entry_price - 1.0
    pmax = max(prices[:15]) / entry_price - 1.0
    pmin = min(prices[:15]) / entry_price - 1.0
    return round(pnl15, 4), round(pmax, 4), round(pmin, 4)


def main():
    t0 = time.time()
    led = pl.read_parquet(LED)
    win = led.filter((pl.col("date") >= COV_START) & (pl.col("date") <= CUTOFF) & (pl.col("overall") >= 70))
    smoke = int(os.environ.get("IV_SMOKE", "0"))
    if smoke:
        win = win.sort(["date"]).head(smoke)
        print(f"SMOKE mode: {win.height} signals", flush=True)
    print(f"in-window 70+ signals: {win.height}", flush=True)
    recs = []
    miss = 0
    rows = list(zip(win["symbol"].to_list(), win["date"].to_list(), win["overall"].to_list(), win["vol_pct"].to_list()))
    for i, (sym, d, ov, volp) in enumerate(rows):
        try:
            ac = atm_call(sym, d)
            if not ac or ac[1] is None or ac[2] is None or float(ac[1]) <= 0:
                miss += 1; continue
            oid, eprice, eiv, close, exp = int(ac[0]), float(ac[1]), float(ac[2]), float(ac[3]), ac[4]
            put_iv = otm_iv(sym, d, 'put', close, -0.10)
            call_iv = otm_iv(sym, d, 'call', close, 0.10)
            skew = (put_iv - call_iv) if (put_iv is not None and call_iv is not None) else None
            rv_ann = (float(volp) / 100.0) * math.sqrt(252) if volp else None
            iv_rv = (eiv / rv_ann) if (rv_ann and rv_ann > 1e-6) else None
            pnl15, pmax, pmin = fwd_pnl(oid, eprice, d)
            recs.append({"symbol": sym, "date": d, "overall": int(ov), "vol_pct": float(volp) if volp else None,
                         "atm_iv": round(eiv, 4), "entry_premium": eprice, "iv_rv": (round(iv_rv, 4) if iv_rv else None),
                         "skew": (round(skew, 4) if skew is not None else None),
                         "pnl15": pnl15, "pnl_max": pmax, "pnl_min": pmin})
        except Exception as e:
            miss += 1
            if miss <= 3:
                print("  err", sym, d, repr(e)[:80], flush=True)
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(rows)} ({time.time()-t0:.0f}s) kept={len(recs)} miss={miss}", flush=True)
    print(f"kept={len(recs)} miss={miss} in {time.time()-t0:.0f}s", flush=True)
    os.makedirs(OUT, exist_ok=True)
    df = pl.DataFrame(recs, infer_schema_length=None)
    out = os.path.join(OUT, "iv_ledger.parquet")
    df.write_parquet(out)
    print("DONE", out, "N=%d" % df.height,
          "iv_rv_cov=%.0f%%" % (100 * df["iv_rv"].drop_nulls().len() / max(df.height, 1)),
          "pnl15_cov=%.0f%%" % (100 * df["pnl15"].drop_nulls().len() / max(df.height, 1)),
          "skew_cov=%.0f%%" % (100 * df["skew"].drop_nulls().len() / max(df.height, 1)), flush=True)


if __name__ == "__main__":
    main()
