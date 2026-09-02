"""Discriminator probe ledger: the OVERBOUGHT-IN-UPTREND CALL cohort (the dampener-failed
cohort) with EXTRA orthogonal features the divergence components don't encode, so we can ask:
within this cohort, does ANY feature split 'reverts' (option loss) from 'continues' (option win)?

Cohort: trend >= TR_MIN(85) AND stoch <= ST_MAX(25) AND overall >= OV_MIN(70), <= holdout cutoff.
Extra features pulled DIRECTLY from the scores table (already-stored columns):
  pct_from_ema50, pct_from_ema200 (OVEREXTENSION), bb_position, regime_composite, score_velocity_7d
Plus vol_signal/vol_mag and w_adj (from weight_info) and the barrier-agnostic forward path
(t_up/t_dn for any TP/SL/W via label.derive). Holdout-locked.

Modeled faithfully on component_reweight/build_ledger.py's working forward-path walk.
"""
import os, sys, json, time
import numpy as np

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))

from database.trader_database import DB
from database.models.technical import PriceHistory
from experiments._holdout import CUTOFF as HOLDOUT_CUTOFF

CUTOFF = HOLDOUT_CUTOFF.isoformat()
START = os.environ.get("LEDGER_START", "2016-05-15")
VERSION_ID = int(os.environ.get("LEDGER_VERSION_ID", "70"))
TR_MIN = int(os.environ.get("DISC_TREND_MIN", "85"))
ST_MAX = int(os.environ.get("DISC_STOCH_MAX", "25"))
OV_MIN = int(os.environ.get("DISC_OV_MIN", "70"))
MAXW = 40
VOL_WIN = 60
UP_GRID = np.round(np.arange(0.40, 2.501, 0.05), 2)
DN_GRID = np.round(np.arange(0.40, 5.001, 0.05), 2)


def main():
    t0 = time.time()
    sql = (
        "SELECT symbol, date, overall, trend, bb, rsi, macd, stoch, technical_alignment, "
        "volume_signal, volume_magnitude, pct_from_ema50, pct_from_ema200, bb_position, "
        "regime_composite, score_velocity_7d, weight_info FROM scores "
        "WHERE version_id=%d AND trend >= %d AND stoch <= %d AND overall >= %d "
        "AND date BETWEEN '%s' AND '%s'"
        % (VERSION_ID, TR_MIN, ST_MAX, OV_MIN, START, CUTOFF)
    )
    rows = DB.execute_sql(sql).fetchall()
    print("pulled cohort:", len(rows), "in %.1fs" % (time.time() - t0), flush=True)
    if not rows:
        print("NO ROWS"); return

    by_sym = {}
    for r in rows:
        (sym, d, ov, ct, cb, cr, cm, cs, cta, vsig, vmag, pe50, pe200, bbp,
         rcomp, svel, wi) = r
        try:
            wij = json.loads(wi) if wi else {}
        except Exception:
            wij = {}
        by_sym.setdefault(sym, []).append(
            (d, int(ov), ct, cb, cr, cm, cs, cta, vsig, vmag, pe50, pe200, bbp, rcomp, svel, wij))
    syms = list(by_sym)
    print("symbols:", len(syms), flush=True)

    t1 = time.time()
    price = {}
    for sym in syms:
        rs = list(PriceHistory.select(PriceHistory.date, PriceHistory.close,
                                      PriceHistory.high, PriceHistory.low)
                  .where(PriceHistory.symbol == sym).order_by(PriceHistory.date))
        if not rs:
            continue
        dates = np.array([r.date for r in rs])
        close = np.array([float(r.close) for r in rs], dtype=float)
        high = np.array([float(r.high) for r in rs], dtype=float)
        low = np.array([float(r.low) for r in rs], dtype=float)
        ret = np.empty_like(close); ret[0] = 0.0
        ret[1:] = close[1:] / close[:-1] - 1.0
        sig = np.full(len(close), np.nan)
        for i in range(len(close)):
            lo = max(0, i - VOL_WIN + 1)
            if i - lo >= 20:
                sig[i] = np.std(ret[lo:i + 1], ddof=1)
        price[sym] = (dates, close, high, low, sig)
    print("price load: %d syms in %.1fs" % (len(price), time.time() - t1), flush=True)

    t2 = time.time()
    recs = []
    for sym in syms:
        if sym not in price:
            continue
        dates, close, high, low, sig = price[sym]
        idx = {d: i for i, d in enumerate(dates)}
        for (d, ov, ct, cb, cr, cm, cs, cta, vsig, vmag, pe50, pe200, bbp, rcomp, svel, wij) in by_sym[sym]:
            i0 = idx.get(d)
            if i0 is None:
                continue
            s = sig[i0]; entry = close[i0]
            if not (s and s > 1e-6 and entry > 0):
                continue
            j = i0 + 1
            caldays, up_ex, dn_ex = [], [], []
            while j < len(dates):
                cd = (dates[j] - d).days
                if cd > MAXW:
                    break
                if cd >= 1:
                    caldays.append(cd)
                    up_ex.append((high[j] - entry) / entry / s)
                    dn_ex.append((entry - low[j]) / entry / s)
                j += 1
            if not caldays:
                continue
            caldays = np.array(caldays)
            cum_up = np.maximum.accumulate(np.array(up_ex))
            cum_dn = np.maximum.accumulate(np.array(dn_ex))
            iu = np.searchsorted(cum_up, UP_GRID, side="left")
            idn = np.searchsorted(cum_dn, DN_GRID, side="left")
            t_up = np.where(iu < len(caldays), caldays[np.clip(iu, 0, len(caldays) - 1)], 999).astype(np.int16)
            t_dn = np.where(idn < len(caldays), caldays[np.clip(idn, 0, len(caldays) - 1)], 999).astype(np.int16)

            def at(arr, W):
                m = caldays <= W
                return float(arr[m].max()) if m.any() else None

            def fnum(x):
                try:
                    return float(x) if x is not None else None
                except Exception:
                    return None

            wadj = wij.get("w_adj")
            recs.append({
                "symbol": sym,
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                "overall": ov, "vol_pct": round(float(s) * 100, 4),
                "fwd_caldays": int(caldays[-1]),
                "t_up": t_up.tolist(), "t_dn": t_dn.tolist(),
                "mfe15": at(cum_up, 15), "mae15": at(cum_dn, 15),
                "c_trend": ct, "c_bb": cb, "c_rsi": cr, "c_macd": cm, "c_stoch": cs, "c_ta": cta,
                "vol_signal": vsig, "vol_mag": fnum(vmag),
                "pct_ema50": fnum(pe50), "pct_ema200": fnum(pe200), "bb_position": fnum(bbp),
                "regime_composite": fnum(rcomp), "score_vel7": fnum(svel),
                "w_adj": (float(wadj) if isinstance(wadj, (int, float)) and not isinstance(wadj, bool) else None),
            })
    print("forward walk: %d signals in %.1fs" % (len(recs), time.time() - t2), flush=True)

    import polars as pl
    outdir = os.path.join(_ROOT, ".cache", "divergence_dampener")
    os.makedirs(outdir, exist_ok=True)
    df = pl.DataFrame(recs, infer_schema_length=None)
    out = os.path.join(outdir, "ledger_disc.parquet")
    df.write_parquet(out)
    assert df["date"].max() <= CUTOFF, "HOLDOUT LEAK"
    print("DONE", out, "N=%d window=%s..%s" % (df.height, df["date"].min(), df["date"].max()), flush=True)


if __name__ == "__main__":
    main()
