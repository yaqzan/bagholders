"""The 'crashes' question is a LEFT-TAIL / DD problem, not a mean-WR one (which is why every
mean-WR test was flat). This mines the exhaustion/spike/divergence/earnings cohorts for
LEFT-TAIL concentration on the FUNDED apex barrier (the live strategy) — SL-rate, MAE
crash-zone touch, and PER-YEAR EV — to tell a real bear-concentrated Stage-3 sizing lever
from the crash-artifact trap (low-EV only in bull tape). Same holdout-locked v70 ledger.

apex15 = TP 1.092s / SL 2.548s / 15d (funded HOLD). 'crash' proxy = MAE15 >= 2.548s (SL zone).
NEW indicators: (1) bearish RSI divergence drsi5<=0 while price ran up; (2) price-spike-but-
weak-momentum-component; (3) earnings inside the 21-cal-day hold window.
"""
import os, sys, math, time, bisect
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa
from database.trader_database import DB  # noqa

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
PXCACHE = os.path.join(_ROOT, ".cache", "divergence_dampener", "px_panel.parquet")
CUTOFF = "2026-05-15"
APEX_SL_IDX = L._idx(L.DN_GRID, 2.548)
APEX_TP_IDX = L._idx(L.UP_GRID, 1.092)


def zprop(w1, n1, w2, n2):
    if n1 < 20 or n2 < 20:
        return None, None
    p1, p2 = w1 / n1, w2 / n2
    pp = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    return (p1 - p2), ((p1 - p2) / se if se else None)


def build_px(symset):
    if os.path.exists(PXCACHE):
        px = pl.read_parquet(PXCACHE)
        if set(["v5", "drsi5"]).issubset(px.columns):
            print("px cache hit:", PXCACHE, px.height, flush=True)
            return px
    t0 = time.time(); frames = []
    for yr in range(2015, 2027):
        rows = DB.execute_sql(
            "SELECT symbol,date,close FROM price_history WHERE date>='%d-01-01' AND date<='%d-12-31' AND date<='%s'"
            % (yr, yr, CUTOFF)).fetchall()
        if rows:
            frames.append(pl.DataFrame({"symbol": [r[0] for r in rows],
                "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
                "close": [float(r[2]) for r in rows]}))
    px = pl.concat(frames).filter(pl.col("symbol").is_in(symset)).unique(["symbol", "date"], keep="last").sort(["symbol", "date"])
    # velocity
    for n in (1, 3, 5, 10):
        px = px.with_columns(((pl.col("close") / pl.col("close").shift(n).over("symbol")) - 1.0).alias("ret%d" % n))
    # Wilder RSI-14 (alpha=1/14) + 5-bar RSI change (drsi5)
    d = pl.col("close").diff().over("symbol")
    px = px.with_columns([pl.when(d > 0).then(d).otherwise(0.0).alias("g"),
                          pl.when(d < 0).then(-d).otherwise(0.0).alias("l")])
    px = px.with_columns([pl.col("g").ewm_mean(alpha=1 / 14, adjust=False).over("symbol").alias("ag"),
                          pl.col("l").ewm_mean(alpha=1 / 14, adjust=False).over("symbol").alias("al")])
    px = px.with_columns((100 - 100 / (1 + pl.col("ag") / (pl.col("al") + 1e-9))).alias("rsi14"))
    px = px.with_columns((pl.col("rsi14") - pl.col("rsi14").shift(5).over("symbol")).alias("drsi5"))
    px = px.select(["symbol", "date", "ret1", "ret3", "ret5", "ret10", "drsi5"])
    os.makedirs(os.path.dirname(PXCACHE), exist_ok=True)
    px.write_parquet(PXCACHE)
    print("px built+cached: %d rows (%.1fs)" % (px.height, time.time() - t0), flush=True)
    return px


def earn_in_hold(df):
    """flag: any earnings date in (signal, signal+21 cal days] per symbol."""
    rows = DB.execute_sql("SELECT symbol,date FROM earnings_dates").fetchall()
    by = {}
    for s, d in rows:
        by.setdefault(s, []).append(d if hasattr(d, "isoformat") else d)
    from datetime import date as _date
    def _pd(x):
        y, m, dd = str(x)[:10].split("-"); return _date(int(y), int(m), int(dd))
    by = {s: sorted(_pd(x) for x in v) for s, v in by.items()}
    flags = []
    for s, ds in zip(df["symbol"].to_list(), df["date"].to_list()):
        sd = _pd(ds); arr = by.get(s)
        f = 0
        if arr:
            i = bisect.bisect_right(arr, sd)
            if i < len(arr) and (arr[i] - sd).days <= 21:
                f = 1
        flags.append(f)
    return pl.Series("earn_hold", flags)


def main():
    df = pl.read_parquet(LEDGER)
    assert df["date"].max() <= CUTOFF, "HOLDOUT LEAK"
    df = df.with_columns([
        L.win_expr(0.901, 0.772, 15).alias("opt15"),
        L.win_expr(1.092, 2.548, 15).alias("apex15"),
        # apex SL-touch within 15d (lost via the funded stop) and MAE crash-zone
        ((pl.col("t_dn").list.get(APEX_SL_IDX) <= 15) &
         (pl.col("t_dn").list.get(APEX_SL_IDX) <= pl.col("t_up").list.get(APEX_TP_IDX))).cast(pl.Int8).alias("apex_sl"),
        (pl.col("mae15") >= 2.548).cast(pl.Int8).alias("crash"),   # touched apex SL zone (= dead-hold/crash)
        pl.col("date").str.slice(0, 4).alias("yr"),
    ])
    syms = set(df["symbol"].unique().to_list())
    px = build_px(syms)
    df = df.join(px, on=["symbol", "date"], how="left")
    sig = pl.col("vol_pct") / 100.0
    df = df.with_columns([(pl.col("ret%d" % n) / (sig * math.sqrt(n))).alias("v%d" % n) for n in (1, 3, 5, 10)])
    df = df.with_columns(earn_in_hold(df))

    base = df.filter(pl.col("overall") >= 75)
    N = base.height
    # base rates
    b_opt = base["opt15"].drop_nulls(); b_apex = base["apex15"].drop_nulls()
    b_sl = base["apex_sl"]; b_cr = base["crash"]
    print("75+ base: N=%d  opt15=%.1f%%  apex15=%.1f%%  apexSLrate=%.1f%%  crash(MAE>=2.55s)=%.1f%%\n" % (
        N, b_opt.mean()*100, b_apex.mean()*100, b_sl.mean()*100, b_cr.mean()*100))

    EXH = (pl.col("c_trend") >= 90) & (pl.col("c_stoch") <= 15)
    COHORTS = {
        "EXH trend>=90&stoch<=15 (NET)":     EXH,
        "EXH & v5>=1.5 (spike-top)":          EXH & (pl.col("v5") >= 1.5),
        "SPIKE v5>=1.5":                      pl.col("v5") >= 1.5,
        "SPIKE v1>=2.0":                      pl.col("v1") >= 2.0,
        "DIV bearish-rsi v5>=1 & drsi5<=0":   (pl.col("v5") >= 1.0) & (pl.col("drsi5") <= 0),
        "DIV spike&weakMACD v5>=1 & cmacd<=40": (pl.col("v5") >= 1.0) & (pl.col("c_macd") <= 40),
        "DIV spike&weakRSI v5>=1 & crsi<=40":  (pl.col("v5") >= 1.0) & (pl.col("c_rsi") <= 40),
        "EARN in hold window":                pl.col("earn_hold") == 1,
        "EXH & EARN":                         EXH & (pl.col("earn_hold") == 1),
    }
    print("%-38s %6s %6s  %-22s %-22s %-24s" % ("cohort (within 75+)", "N", "%", "opt15 (z)", "apex15 (z)", "apexSL-rate (z)  crash%"))
    print("-" * 130)
    for nm, m in COHORTS.items():
        coh = base.filter(m); rest = base.filter(~m)
        n = coh.height
        if n < 20:
            print("%-38s %6d  too small" % (nm, n)); continue
        def cell(col, hi_is_bad=False):
            cw = coh[col].drop_nulls(); rw = rest[col].drop_nulls()
            d, z = zprop(int(cw.sum()), cw.len(), int(rw.sum()), rw.len())
            fl = ""
            if z is not None:
                if col == "opt15" and z <= -3: fl = "<W1"
                if col == "apex_sl" and z >= 3: fl = "<<TAIL"
            return "%4.1f%% (%+4.1f)%s" % (cw.mean()*100, (z if z is not None else float('nan')), fl)
        crashpct = coh["crash"].mean()*100
        print("%-38s %6d %5.1f  %-22s %-22s %-16s %5.1f%%" % (
            nm, n, 100*n/N, cell("opt15"), cell("apex15"), cell("apex_sl", True), crashpct))

    # ---- per-year apex15 WR for the two key cohorts (crash-artifact-trap detector) ----
    print("\n=== per-year apex15 WR (cohort vs 75+ base) — bear-concentrated = real lever, bull-bad = trap ===")
    yrs = sorted(base["yr"].unique().to_list())
    hdr = "  %-30s " % "cohort" + " ".join("%6s" % y for y in yrs)
    print(hdr)
    base_by = {r["yr"]: r["apex15"] for r in base.group_by("yr").agg(pl.col("apex15").mean()).to_dicts()}
    print("  %-30s " % "BASE 75+" + " ".join("%5.0f%%" % ((base_by.get(y) or 0)*100) for y in yrs))
    for nm, m in [("EXH (NET cohort)", EXH), ("EXH & v5>=1.5", EXH & (pl.col("v5") >= 1.5)), ("EARN in hold", pl.col("earn_hold") == 1)]:
        coh = base.filter(m)
        by = {r["yr"]: (r["apex15"], r["N"]) for r in coh.group_by("yr").agg(pl.col("apex15").mean().alias("apex15"), pl.len().alias("N")).to_dicts()}
        cells = []
        for y in yrs:
            v = by.get(y)
            cells.append("%5.0f%%" % (v[0]*100) if v and v[1] >= 10 else "   . ")
        print("  %-30s " % nm + " ".join(cells))

    print("\nVerdict rule: a real Stage-3 exhaustion sizing-lever needs apexSL-rate z>=+3 (fatter funded-stop tail)")
    print("AND the badness bear-concentrated (not bull-tape, which = crash-artifact trap) AND orthogonal to RXDD/MWDD/SVR/TVDD.")


if __name__ == "__main__":
    main()
