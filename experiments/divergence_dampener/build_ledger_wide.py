"""Component-reweight ledger — v70 CALL signals with RAW component scores + a BARRIER-AGNOSTIC
forward-path capture, so win/loss for ANY (TP_sigma, SL_sigma, max_hold) can be derived with
zero rebuilds (the apex/sentinel/core knobs are being retuned by another agent).

For each 75+ CALL signal we record, over the forward window (<= MAXW calendar days):
  t_up[g]  = first calendar-day the cumulative-max FAVORABLE excursion reached g sigma
  t_dn[g]  = first calendar-day the cumulative-max ADVERSE  excursion reached g sigma
for fine sigma grids (UP_GRID 0.50..2.00, DN_GRID 0.50..4.00, step 0.05; 999 = untouched).

Win/loss for a funded config (TP_sigma, SL_sigma, W) is then:
  win  = t_up[TP_sigma] <= W  AND  t_up[TP_sigma] < t_dn[SL_sigma]      (same-bar tie => loss, conservative)
  unresolved (None) = neither touched AND fwd_caldays < W

sigma = 60-day realized daily vol at entry; excursions are intraday (high/low) vs entry close.
FIXED thresholds (no sqrt(W/30) scaling) — matches monte_carlo.py's exit convention exactly,
unlike assess._peak_to_result which scales by sqrt(W/30). Holdout-locked at CUTOFF.

Also pulls the raw 6 component scores (c_trend/c_bb/c_rsi/c_macd/c_stoch/c_ta) + volume +
weight_info weights/traces for the Stage-A combination analysis.
"""
import os, sys, json, time
import numpy as np

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# label.py lives in the component_reweight harness dir
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))

from database.trader_database import DB
from database.models.technical import PriceHistory
from experiments._holdout import CUTOFF as HOLDOUT_CUTOFF

CUTOFF = HOLDOUT_CUTOFF.isoformat()                 # "2026-05-15"
START = os.environ.get("LEDGER_START", "2016-05-15")
LO, HI = int(os.environ.get("LEDGER_LO","40")), int(os.environ.get("LEDGER_HI","99"))
VERSION_ID = int(os.environ.get("LEDGER_VERSION_ID", "70"))
MAXW = 40                                            # forward window, calendar days
VOL_WIN = 60                                         # realized-vol lookback (trading bars)

UP_GRID = np.round(np.arange(0.40, 2.501, 0.05), 2)  # favorable sigma thresholds
DN_GRID = np.round(np.arange(0.40, 5.001, 0.05), 2)  # adverse sigma thresholds

WI_WEIGHTS = ["trend", "bb", "rsi", "macd", "stoch", "ta", "td", "pre_boost", "pre_regime"]
WI_TRACES = ["w_adj", "wadj_completed", "wadj_partial", "weekly_transition_t",
             "scw_dampen", "scw_scalar", "mcd_dampen", "ich_call_dampen",
             "cwcf_dampen", "cwwd_dampen", "cswc_dampen", "cont_lift", "mis_stress"]


def main():
    t0 = time.time()
    # TARGETED dip-divergence cohort: saturated DOWNtrend-break + oversold stoch.
    # trend<=TR_MAX (structural break) AND stoch>=ST_MIN (oversold raw -> high stoch component).
    # overall band kept wide (DIP_OV_LO..99) so we also capture the few that already qualify.
    TR_MAX = int(os.environ.get("DIP_TREND_MAX", "45"))
    ST_MIN = int(os.environ.get("DIP_STOCH_MIN", "80"))
    DIP_OV_LO = int(os.environ.get("DIP_OV_LO", "30"))
    sql = (
        "SELECT symbol, date, overall, trend, bb, rsi, macd, stoch, technical_alignment, "
        "volume_signal, volume_magnitude, weight_info FROM scores "
        "WHERE version_id=%d AND trend <= %d AND stoch >= %d AND overall >= %d "
        "AND date BETWEEN '%s' AND '%s'"
        % (VERSION_ID, TR_MAX, ST_MIN, DIP_OV_LO, START, CUTOFF)
    )
    rows = DB.execute_sql(sql).fetchall()
    print("pulled scores:", len(rows), "in %.1fs" % (time.time() - t0), flush=True)
    if not rows:
        print("NO ROWS"); return

    by_sym = {}          # sym -> list[(date, overall, comps..., vsig, vmag, wij)]
    for (sym, d, ov, ct, cb, cr, cm, cs, cta, vsig, vmag, wi) in rows:
        try:
            wij = json.loads(wi) if wi else {}
        except Exception:
            wij = {}
        by_sym.setdefault(sym, []).append((d, int(ov), ct, cb, cr, cm, cs, cta, vsig, vmag, wij))
    syms = list(by_sym)
    print("symbols:", len(syms), flush=True)

    # bulk price load once
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
        # 60-bar realized daily vol (rolling std of simple returns), as a fraction
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
    nU, nD = len(UP_GRID), len(DN_GRID)
    for sym in syms:
        if sym not in price:
            continue
        dates, close, high, low, sig = price[sym]
        # map date -> index
        idx = {d: i for i, d in enumerate(dates)}
        for (d, ov, ct, cb, cr, cm, cs, cta, vsig, vmag, wij) in by_sym[sym]:
            i0 = idx.get(d)
            if i0 is None:
                continue
            s = sig[i0]
            entry = close[i0]
            if not (s and s > 1e-6 and entry > 0):
                continue
            # forward window: bars after i0 within MAXW calendar days
            j = i0 + 1
            caldays, up_ex, dn_ex = [], [], []
            while j < len(dates):
                cd = (dates[j] - d).days
                if cd > MAXW:
                    break
                if cd >= 1:
                    caldays.append(cd)
                    up_ex.append((high[j] - entry) / entry / s)   # favorable sigma
                    dn_ex.append((entry - low[j]) / entry / s)    # adverse sigma
                j += 1
            if not caldays:
                continue
            caldays = np.array(caldays)
            cum_up = np.maximum.accumulate(np.array(up_ex))
            cum_dn = np.maximum.accumulate(np.array(dn_ex))
            fwd_caldays = int(caldays[-1])
            # first cal-day each grid level is reached (cum arrays are monotone non-decreasing,
            # caldays non-decreasing) -> searchsorted on the cum array.
            iu = np.searchsorted(cum_up, UP_GRID, side="left")
            idn = np.searchsorted(cum_dn, DN_GRID, side="left")
            t_up = np.where(iu < len(caldays), caldays[np.clip(iu, 0, len(caldays) - 1)], 999)
            t_up = np.where(iu < len(caldays), t_up, 999).astype(np.int16)
            t_dn = np.where(idn < len(caldays), caldays[np.clip(idn, 0, len(caldays) - 1)], 999)
            t_dn = np.where(idn < len(caldays), t_dn, 999).astype(np.int16)

            def mfe_at(W):
                m = caldays <= W
                return float(cum_up[m].max()) if m.any() else None

            def mae_at(W):
                m = caldays <= W
                return float(cum_dn[m].max()) if m.any() else None

            rec = {
                "symbol": sym,
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                "overall": ov,
                "vol_pct": round(float(s) * 100, 4),
                "fwd_caldays": fwd_caldays,
                "t_up": t_up.tolist(),
                "t_dn": t_dn.tolist(),
                "mfe15": mfe_at(15), "mae15": mae_at(15),
                "mfe30": mfe_at(30), "mae30": mae_at(30),
                "c_trend": ct, "c_bb": cb, "c_rsi": cr, "c_macd": cm, "c_stoch": cs, "c_ta": cta,
                "vol_signal": vsig, "vol_mag": (float(vmag) if vmag is not None else None),
            }
            for f in WI_WEIGHTS:
                v = wij.get(f)
                key = ("w_%s" % f) if f in ("trend", "bb", "rsi", "macd", "stoch", "ta") else f
                rec[key] = (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
                            else (float(int(v)) if isinstance(v, bool) else v))
            for f in WI_TRACES:
                v = wij.get(f)
                rec[f] = (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
                          else (float(int(v)) if isinstance(v, bool) else None))
            recs.append(rec)
    print("forward-path walk: %d signals in %.1fs" % (len(recs), time.time() - t2), flush=True)

    # full-universe per-date v70 supply (75+ call / <=25 put / total) for recycle coverage
    sup_sql = (
        "SELECT date, SUM(overall>=75) c75, SUM(overall<=25) p25, COUNT(*) n FROM scores "
        "WHERE version_id=%d AND date BETWEEN '%s' AND '%s' GROUP BY date"
        % (VERSION_ID, START, CUTOFF))
    supply = [{"date": (d.isoformat() if hasattr(d, "isoformat") else str(d)),
               "c75": int(c or 0), "p25": int(p or 0), "n": int(n or 0)}
              for d, c, p, n in DB.execute_sql(sup_sql).fetchall()]

    import polars as pl
    outdir = os.path.join(_ROOT, ".cache", "divergence_dampener")
    os.makedirs(outdir, exist_ok=True)
    df = pl.DataFrame(recs, infer_schema_length=None)
    out = os.path.join(outdir, "ledger_v%d_wide.parquet" % VERSION_ID)
    df.write_parquet(out)
    with open(os.path.join(outdir, "supply_v%d_wide.json" % VERSION_ID), "w") as f:
        json.dump(supply, f)
    meta = {"version_id": VERSION_ID, "window": [df["date"].min(), df["date"].max()],
            "N": df.height, "maxw": MAXW, "up_grid": UP_GRID.tolist(), "dn_grid": DN_GRID.tolist(),
            "supply_days": len(supply)}
    with open(os.path.join(outdir, "ledger_v%d_wide_meta.json" % VERSION_ID), "w") as f:
        json.dump(meta, f, indent=2)
    assert df["date"].max() <= CUTOFF, "HOLDOUT LEAK"

    # sanity: derive the current best-guess APEX label (TP30=+1.092, SL70=-2.548, W=15)
    from label import derive
    for (tp, sl, w, nm) in [(1.092, 2.548, 15, "APEX TP30/SL70/15d"),
                            (0.910, 2.548, 15, "TP25/SL70/15d"),
                            (1.274, 1.092, 15, "opt 1.274/1.092/15d")]:
        wcol = derive(df, tp, sl, w)
        for lo, hi in [(70, 74), (75, 79), (80, 84), (85, 99)]:
            m = (df["overall"] >= lo) & (df["overall"] <= hi)
            sub = wcol.filter(m)
            res = sub.drop_nulls()
            wr = round(res.mean() * 100, 1) if res.len() else None
            print("  [%s] %d-%d N=%d win%%=%s" % (nm, lo, hi, res.len(), wr), flush=True)
        m = df["overall"] >= 75
        res = wcol.filter(m).drop_nulls()
        print("  [%s] 75+   N=%d win%%=%s" % (nm, res.len(),
              round(res.mean() * 100, 1) if res.len() else None), flush=True)
    print("DONE", out, "N=%d" % df.height, flush=True)


if __name__ == "__main__":
    main()
