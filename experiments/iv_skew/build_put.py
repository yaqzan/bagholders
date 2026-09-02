"""Deepen step 2: PUT-side symmetry test of the skew signal (the mechanism question).
Call-side found: HIGH put-skew (put_iv>>call_iv) -> BETTER calls, read as "the call is CHEAP relative
to the put". If the mechanism is 'buy whichever side is cheap', then for PUT signals the put is cheap
when skew is LOW (call_iv>=put_iv) -> LOW skew should -> BETTER puts. If instead HIGH skew -> better
puts (puts expensive=fear=bearish continuation), it's directional sentiment, not cheap-side.

For each PUT signal (overall<=30, in covered window): ATM ~30DTE PUT entry + fwd 15d PUT P&L
(put gains when price falls; track the put's own price) + skew(10% OTM) + atm_iv. Indexed lookups,
IV-sanity + OI filters. Holdout-respecting. db=heavy.
"""
import os, sys, time
import polars as pl

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, _ROOT)
from database.trader_database import DB

COV_START = "2025-02-10"
CUTOFF = "2026-05-15"
OUT = os.path.join(_ROOT, ".cache", "iv_skew")


def one(sql, params):
    return DB.execute_sql(sql, params).fetchone()


def atm_put(sym, d):
    return one(
        "SELECT o.id, op.price, op.iv, ph.close FROM options o JOIN option_prices op ON op.option_id=o.id "
        "JOIN price_history ph ON ph.symbol=o.symbol AND ph.date=op.date "
        "WHERE o.symbol=%s AND o.option_type='put' AND op.date=%s "
        "AND DATEDIFF(o.expiration_date, op.date) BETWEEN 20 AND 45 "
        "AND op.iv BETWEEN 0.05 AND 5.0 AND op.price>0 AND op.open_interest>0 "
        "ORDER BY ABS(o.strike_price - ph.close)/ph.close LIMIT 1", (sym, d))


def otm_iv(sym, d, otype, close, frac):
    row = one("SELECT op.iv FROM options o JOIN option_prices op ON op.option_id=o.id "
              "WHERE o.symbol=%s AND o.option_type=%s AND op.date=%s "
              "AND DATEDIFF(o.expiration_date, op.date) BETWEEN 20 AND 45 "
              "AND op.iv BETWEEN 0.05 AND 5.0 AND op.open_interest>0 "
              "ORDER BY ABS(o.strike_price - %s) LIMIT 1", (sym, otype, d, close*(1+frac)))
    return float(row[0]) if row and row[0] is not None else None


def fwd_pnl(option_id, entry, d):
    rows = DB.execute_sql("SELECT price FROM option_prices WHERE option_id=%s AND date > %s "
                          "AND date <= DATE_ADD(%s, INTERVAL 24 DAY) ORDER BY date", (option_id, d, d)).fetchall()
    pr = [float(p[0]) for p in rows if p[0] is not None and float(p[0]) > 0]
    if len(pr) < 15:
        # unresolved 15-bar horizon (late signal / illiquid chain): null the labels,
        # never truncate-substitute the last available price as the 15-bar exit
        # (mirrors build_iv.fwd_pnl / osk_era build_osk_ledger.fwd_pnl).
        return None, None, None
    p15 = pr[14]  # 15th trading bar fwd (0-indexed 14)
    return round(p15/entry-1, 4), round(max(pr[:15])/entry-1, 4), round(min(pr[:15])/entry-1, 4)


def main():
    t0 = time.time()
    rows = DB.execute_sql(
        "SELECT symbol, date, overall FROM scores WHERE version_id=70 AND overall<=30 "
        "AND overall IS NOT NULL AND date BETWEEN %s AND %s", (COV_START, CUTOFF)).fetchall()
    print(f"put signals (<=30) in window: {len(rows)}", flush=True)
    recs = []; miss = 0
    for i, (sym, d, ov) in enumerate(rows):
        try:
            ap = atm_put(sym, d.isoformat() if hasattr(d, "isoformat") else str(d))
            if not ap or ap[1] is None or ap[2] is None or float(ap[1]) <= 0:
                miss += 1; continue
            oid, eprice, eiv, close = int(ap[0]), float(ap[1]), float(ap[2]), float(ap[3])
            ds = d.isoformat() if hasattr(d, "isoformat") else str(d)
            piv = otm_iv(sym, ds, 'put', close, -0.10); civ = otm_iv(sym, ds, 'call', close, 0.10)
            skew = (piv - civ) if (piv is not None and civ is not None) else None
            pnl15, pmax, pmin = fwd_pnl(oid, eprice, ds)
            recs.append({"symbol": sym, "date": ds, "overall": int(ov), "atm_iv": round(eiv, 4),
                         "skew": (round(skew, 4) if skew is not None else None),
                         "pnl15": pnl15, "pnl_max": pmax, "pnl_min": pmin})
        except Exception:
            miss += 1
        if (i+1) % 500 == 0:
            print(f"  {i+1}/{len(rows)} ({time.time()-t0:.0f}s) kept={len(recs)} miss={miss}", flush=True)
    os.makedirs(OUT, exist_ok=True)
    df = pl.DataFrame(recs, infer_schema_length=None)
    out = os.path.join(OUT, "put_ledger.parquet")
    df.write_parquet(out)
    print("DONE", out, "N=%d kept, %d miss in %ds, skew_cov=%.0f%%"
          % (df.height, miss, time.time()-t0, 100*df["skew"].drop_nulls().len()/max(df.height, 1)), flush=True)


if __name__ == "__main__":
    main()
