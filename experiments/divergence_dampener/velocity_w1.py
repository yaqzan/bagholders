"""W1 cohort-z for the ONE untested seam from the NET diagnosis (2026-06-08):
run-up VELOCITY at signal — does a sharp sigma-normalized vertical spike into the
signal separate option-revert (NET) from option-continue (NVDA), where the level
feature pct_from_ema50 (tested 2026-06-03) was FLAT?

Feature: ret_Nd_sigma = (close[i0]/close[i0-N] - 1) / (vol_frac * sqrt(N))
  = the N-trading-bar run-up INTO the signal, in units of the stock's own daily sigma
    (vol_pct from the ledger, the same 60-bar realized vol the barriers use).
  This is the call-side mirror of v37 PCD's ret_10d_sigma (puts).

Gate (Stage-1 W1, OPTION barrier opt15, holdout-locked <=2026-05-15):
  ship-worthy dampener needs cohort opt15 z <= -3 (cohort WORSE) AND apex15 not contradicting.
  The whole point: test it WITHIN the trend>=90 & stoch<=15 cohort the divergence rescue
  probe found unseparable by every other feature.
"""
import os, sys, math, time
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
import label as L  # noqa
from database.trader_database import DB  # noqa

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
CUTOFF = "2026-05-15"
NS = [1, 3, 5, 10]  # trailing windows (trading bars)


def zprop(w1, n1, w2, n2):
    if n1 < 5 or n2 < 5:
        return None, None, (None, None)
    p1, p2 = w1 / n1, w2 / n2
    pp = (w1 + w2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    if se == 0:
        return p1 - p2, None, (p1, p2)
    return p1 - p2, (p1 - p2) / se, (p1, p2)


def build_velocity(symbols):
    """One chunked bulk read of closes -> sigma-normalized trailing run-up per (symbol,date)."""
    t0 = time.time()
    frames = []
    symset = set(symbols)
    for yr in range(2015, 2027):  # 2015 start gives lookback for early-2016 signals
        sql = ("SELECT symbol, date, close FROM price_history "
               "WHERE date >= '%d-01-01' AND date <= '%d-12-31' AND date <= '%s'"
               % (yr, yr, CUTOFF))
        rows = DB.execute_sql(sql).fetchall()
        if rows:
            frames.append(pl.DataFrame(
                {"symbol": [r[0] for r in rows],
                 "date": [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
                 "close": [float(r[2]) for r in rows]}))
        print("  px %d: %d rows (%.1fs)" % (yr, len(rows), time.time() - t0), flush=True)
    px = pl.concat(frames).filter(pl.col("symbol").is_in(symset)).sort(["symbol", "date"])
    px = px.unique(subset=["symbol", "date"], keep="last").sort(["symbol", "date"])
    exprs = []
    for n in NS:
        prev = pl.col("close").shift(n).over("symbol")
        exprs.append(((pl.col("close") / prev - 1.0)).alias("ret%d" % n))
    px = px.with_columns(exprs)
    print("velocity panel: %d (sym,date) rows in %.1fs" % (px.height, time.time() - t0), flush=True)
    return px.select(["symbol", "date"] + ["ret%d" % n for n in NS])


def main():
    df = pl.read_parquet(LEDGER)
    assert df["date"].max() <= CUTOFF, "HOLDOUT LEAK"
    df = df.with_columns([
        L.win_expr(0.901, 0.772, 15).alias("opt15"),   # PRIMARY (option-aligned)
        L.win_expr(1.092, 2.548, 15).alias("apex15"),  # funded HOLD TP30/SL70
        L.win_expr(1.414, 3.536, 15).alias("gen15"),   # dashboard generic (the trap)
    ])
    syms = df["symbol"].unique().to_list()
    px = build_velocity(syms)

    df = df.join(px, on=["symbol", "date"], how="left")
    # sigma-normalize trailing returns by the stock's own daily sigma (vol_pct is %)
    sig_d = pl.col("vol_pct") / 100.0
    df = df.with_columns([
        (pl.col("ret%d" % n) / (sig_d * math.sqrt(n))).alias("v%d" % n) for n in NS
    ])
    cov = df.filter(pl.col("v5").is_not_null()).height
    print("v5 coverage: %d / %d (%.1f%%)\n" % (cov, df.height, 100 * cov / df.height), flush=True)

    BARRIERS = ["opt15", "apex15", "gen15"]

    def base_wr(sub, b):
        r = sub[b].drop_nulls()
        return (r.mean(), r.len()) if r.len() else (None, 0)

    # ---------- face validity: NET's own pre-cutoff signal velocities ----------
    print("=== NET pre-cutoff signals (face validity) ===")
    net = df.filter((pl.col("symbol") == "NET") & (pl.col("overall") >= 75)).sort("date")
    for r in net.select(["date", "overall", "c_trend", "c_stoch", "v1", "v3", "v5", "opt15"]).to_dicts():
        print("  %s ov=%d trend=%d stoch=%d  v1=%s v3=%s v5=%s  opt15=%s" % (
            r["date"], r["overall"], r["c_trend"], r["c_stoch"],
            _f(r["v1"]), _f(r["v3"]), _f(r["v5"]), r["opt15"]))

    # ---------- quintile gradient of opt15 WR by velocity, within 75+ ----------
    for vf in ["v5", "v3", "v1", "v10"]:
        g = df.filter((pl.col("overall") >= 75) & pl.col(vf).is_not_null())
        qs = g[vf].qcut(5, labels=[str(i) for i in range(5)], allow_duplicates=True)
        g = g.with_columns(qs.alias("q"))
        print("\n=== opt15/apex15/gen15 WR by %s quintile (within 75+, N=%d) ===" % (vf, g.height))
        for q in [str(i) for i in range(5)]:
            sub = g.filter(pl.col("q") == q)
            lo, hi = sub[vf].min(), sub[vf].max()
            cells = []
            for b in BARRIERS:
                wr, n = base_wr(sub, b)
                cells.append("%s=%4.1f%% N=%-5d" % (b, (wr * 100 if wr is not None else 0), n))
            print("  Q%s [%+5.2f..%+5.2f]  %s" % (q, lo if lo is not None else 0, hi if hi is not None else 0, "  ".join(cells)))

    # ---------- cohort z: high-velocity vs rest, within 75+ AND within the divergence cohort ----------
    DIV = (pl.col("c_trend") >= 90) & (pl.col("c_stoch") <= 15)   # NET's exact cohort
    POPS = {
        "75+ ALL": pl.col("overall") >= 75,
        "75+ & DIV(trend>=90 & stoch<=15)": (pl.col("overall") >= 75) & DIV,
        "75+ & trend>=95 & stoch<=10": (pl.col("overall") >= 75) & (pl.col("c_trend") >= 95) & (pl.col("c_stoch") <= 10),
    }
    THRESH = {"v5": [1.0, 1.5, 2.0], "v3": [1.0, 1.5, 2.0], "v1": [1.5, 2.0, 2.5]}
    for pname, pmask in POPS.items():
        pop = df.filter(pmask & pl.col("v5").is_not_null())
        print("\n=========== HIGH-VELOCITY cohort within [%s]  (popN=%d) ===========" % (pname, pop.height))
        for vf, ths in THRESH.items():
            for th in ths:
                coh = pop.filter(pl.col(vf) >= th)
                rest = pop.filter(pl.col(vf) < th)
                if coh.height < 20:
                    print("  %s>=%.1f : cohortN=%d (too small)" % (vf, th, coh.height)); continue
                line = "  %s>=%.1f  cohortN=%-5d (%.1f%%): " % (vf, th, coh.height, 100 * coh.height / pop.height)
                cz = []
                for b in BARRIERS:
                    cw = coh[b].drop_nulls(); rw = rest[b].drop_nulls()
                    if cw.len() < 20:
                        cz.append("%s n/a" % b); continue
                    diff, z, (p1, p2) = zprop(int(cw.sum()), cw.len(), int(rw.sum()), rw.len())
                    flag = ""
                    if z is not None:
                        if b == "opt15" and z <= -3: flag = " <==W1 PASS"
                        elif b == "opt15" and z >= 3: flag = " (BETTER)"
                    cz.append("%s d=%+4.1fpp z=%+5.2f%s" % (b, (diff or 0) * 100, (z if z is not None else float('nan')), flag))
                print(line + "  ".join(cz))

    print("\nW1 rule: opt15 z<=-3 (cohort WORSE) with apex15 not contradicting = a real velocity discriminator.")


def _f(x):
    return "  nan" if x is None else ("%+5.2f" % x)


if __name__ == "__main__":
    main()
