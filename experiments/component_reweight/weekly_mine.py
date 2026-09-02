"""Thread 1 — WEEKLY COMPOSITE + ICH winner/loser mine on HONEST v70 scores.

User ask: group v70 tradeable-band CALL winners/losers by the weekly composite + its
constituents + the ICH score; find which predict W/L; re-weight; re-test. CRITICAL: avoid the
look-ahead bug that inflated past weekly versions.

v70 is the FIRST honest version (v69 point-in-time weekly blend). The blend is:
    w_adj = (1 - t)*wadj_completed + t*wadj_partial ,   t = weekly_transition_t (Mon~0.2 -> Fri 1.0)
  - wadj_completed: last COMPLETED week (W-1 vs W-2). ALWAYS honest (no future).
  - wadj_partial   : current week reconstructed Mon->signal-date ONLY. honest IF no future bars leaked.
  - ich_call_dampen: ICH (Ichimoku weekly Kijun) dampener already applied to the call (>0 = bearish).

LOOK-AHEAD GUARD (the heart of this script):
  A look-ahead feature predicts the outcome BEST early in the week (when the most future is hidden)
  and its edge DECAYS to ~0 by Friday (when the week is complete = no hidden future to leak).
  An HONEST feature is dow-stable, or its edge GROWS toward Friday (legitimately more current-week
  data observed, all in the past). So: split the high-vs-low WR spread by day-of-week / transition_t.
  Monday >> Friday  => LOOK-AHEAD (abort that feature).  Stable/rising => honest, usable.

Label = funded APEX barrier (TP+1.092sigma / SL-2.548sigma / 15 cal-day hold), the live calls-only config.
"""
import os, sys
import numpy as np
import polars as pl
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
from label import add_label  # noqa: E402

APEX = (1.092, 2.548, 15)


def wr(mask_win):
    """mask_win: polars/np bool of wins among a resolved subset; returns (n, winrate)."""
    n = len(mask_win)
    return n, (float(np.mean(mask_win)) if n else float("nan"))


def z2(p1, n1, p0, n0):
    """two-proportion z (cohort p1/n1 vs rest p0/n0)."""
    if n1 == 0 or n0 == 0:
        return float("nan")
    p = (p1 * n1 + p0 * n0) / (n1 + n0)
    se = (p * (1 - p) * (1 / n1 + 1 / n0)) ** 0.5
    return (p1 - p0) / se if se > 0 else float("nan")


def main():
    df = pl.read_parquet(LEDGER)
    df = df.filter(pl.col("vol_pct").is_finite() & (pl.col("vol_pct") > 0.3) & (pl.col("vol_pct") < 40))
    df = add_label(df, *APEX, name="apex")            # apex in {1,0,None}
    df = df.with_columns([
        pl.col("date").str.slice(0, 4).cast(pl.Int32).alias("yr"),
        # signed transition divergence: how much the partial week diverges from the completed week
        (pl.col("wadj_partial") - pl.col("wadj_completed")).alias("wadj_gap"),
    ])
    # day-of-week from ISO date (0=Mon..4=Fri). polars: weekday() 1=Mon..7=Sun
    df = df.with_columns((pl.col("date").str.to_date().dt.weekday() - 1).alias("dow"))

    res = df.filter(pl.col("apex").is_not_null())     # resolved only
    print("=" * 96)
    print("THREAD 1 — WEEKLY + ICH MINE on HONEST v70  (apex barrier 1.092/2.548/15d, calls)")
    print("resolved CALL signals [60,99]:", res.height,
          " | 75+:", res.filter(pl.col("overall") >= 75).height,
          " | 70-74:", res.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74)).height)
    print("=" * 96)

    # ---- 0. honesty sanity: does w_adj == blend(completed, partial; t)? -------------------
    chk = res.with_columns(
        ((1 - pl.col("weekly_transition_t")) * pl.col("wadj_completed")
         + pl.col("weekly_transition_t") * pl.col("wadj_partial")).alias("blend_recon"))
    d = (chk["w_adj"] - chk["blend_recon"]).abs()
    print("\n[0] blend sanity  |w_adj - ((1-t)*completed + t*partial)|  "
          "mean=%.3f  p95=%.3f  max=%.3f" % (d.mean(), d.quantile(0.95), d.max()))
    print("    (small => I'm reading the honest blended fields and the 3 are mutually consistent)")
    print("    weekly_transition_t dist: min=%.2f p50=%.2f max=%.2f" %
          (res["weekly_transition_t"].min(), res["weekly_transition_t"].median(),
           res["weekly_transition_t"].max()))

    # ---- helper: tercile WR table for a feature within a band -----------------------------
    def tercile_table(sub, feat, title):
        s = sub.filter(pl.col(feat).is_not_null())
        if s.height < 90:
            print("   (skip %s: N=%d)" % (title, s.height)); return
        q1, q2 = s[feat].quantile(0.3333), s[feat].quantile(0.6667)
        lo = s.filter(pl.col(feat) <= q1)
        mid = s.filter((pl.col(feat) > q1) & (pl.col(feat) <= q2))
        hi = s.filter(pl.col(feat) > q2)
        base = s["apex"].mean()
        print("   %-22s  base WR=%.1f%% (N=%d)   [terciles of %s]" %
              (title, base * 100, s.height, feat))
        for nm, g in [("LOW ", lo), ("MID ", mid), ("HIGH", hi)]:
            if g.height == 0:
                continue
            p = g["apex"].mean()
            rest = s.filter(~pl.col(feat).is_in(g[feat])) if False else None
            # rest = everything not in this tercile
            others = s.height - g.height
            p_other = (base * s.height - p * g.height) / others if others else float("nan")
            zz = z2(p, g.height, p_other, others)
            edge = "  range[%.1f,%.1f]" % (g[feat].min(), g[feat].max())
            print("      %s WR=%.1f%%  N=%5d  z=%+.2f%s" % (nm, p * 100, g.height, zz, edge))

    # ---- 1. weekly feature WR within tradeable bands --------------------------------------
    for lo, hi, nm in [(75, 99, "75+ POOL"), (70, 74, "70-74 OVERFLOW"), (75, 79, "75-79"), (85, 99, "85+")]:
        sub = res.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        print("\n[1] === %s  (resolved N=%d, base apex WR=%.1f%%) ===" %
              (nm, sub.height, sub["apex"].mean() * 100))
        tercile_table(sub, "w_adj", "blended w_adj")
        tercile_table(sub, "wadj_completed", "wadj_completed (honest)")
        tercile_table(sub, "wadj_partial", "wadj_partial")
        tercile_table(sub, "wadj_gap", "partial-completed gap")

    # ---- 2. ICH dampener: does it correctly flag losers among survivors? ------------------
    print("\n[2] === ICH dampener (ich_call_dampen): among calls that SURVIVED to >=75, are")
    print("       ICH-dampened ones lower-WR? (if yes, ICH is right but maybe not strong enough) ===")
    for lo, hi, nm in [(75, 99, "75+"), (70, 74, "70-74"), (75, 84, "75-84")]:
        sub = res.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        dz = sub.filter(pl.col("ich_call_dampen").abs() < 1e-9)
        dd = sub.filter(pl.col("ich_call_dampen").abs() >= 1e-9)
        n0, p0 = wr(dz["apex"].to_numpy())
        n1, p1 = wr(dd["apex"].to_numpy())
        zz = z2(p1, n1, p0, n0)
        amt = dd["ich_call_dampen"].abs().mean() if n1 else 0.0
        print("   %-7s  no-ICH WR=%.1f%% (N=%d)   ICH-dampened WR=%.1f%% (N=%d, avg|dampen|=%.2f)  z=%+.2f" %
              (nm, p0 * 100, n0, p1 * 100, n1, amt, zz))

    # ---- 3. THE LOOK-AHEAD GUARD: split high-vs-low weekly spread by day-of-week ----------
    print("\n[3] === LOOK-AHEAD GUARD — high-vs-low weekly-WR spread BY DAY-OF-WEEK ===")
    print("    Honest: spread stable or GROWS Mon->Fri (more current-wk data, all past).")
    print("    Look-ahead: spread LARGE Monday, DECAYS to ~0 Friday (hidden future leaked early).")
    pool = res.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 99))   # tradeable+promotable
    for feat in ["w_adj", "wadj_partial", "wadj_completed"]:
        s = pool.filter(pl.col(feat).is_not_null())
        med = s[feat].median()
        print("\n   feature=%-16s (split at median=%.2f; pooled-band [70,99] N=%d)" % (feat, med, s.height))
        print("      %-4s %7s %9s %9s %8s" % ("dow", "N", "lowWR", "highWR", "spread"))
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        for dw in range(5):
            g = s.filter(pl.col("dow") == dw)
            if g.height < 40:
                print("      %-4s  N=%d (skip)" % (dow_names[dw], g.height)); continue
            lo = g.filter(pl.col(feat) <= med)["apex"]
            hi = g.filter(pl.col(feat) > med)["apex"]
            plo = lo.mean() if len(lo) else float("nan")
            phi = hi.mean() if len(hi) else float("nan")
            print("      %-4s %7d %8.1f%% %8.1f%% %+7.1fpp" %
                  (dow_names[dw], g.height, plo * 100, phi * 100, (phi - plo) * 100))

    # ---- 4. continuous transition_t view (holiday-robust) ---------------------------------
    print("\n[4] === transition_t view (holiday-robust dow proxy): w_adj high-low spread by t-bucket ===")
    s = pool.filter(pl.col("w_adj").is_not_null())
    med = s["w_adj"].median()
    print("      %-14s %7s %9s %9s %8s" % ("t-bucket", "N", "lowWR", "highWR", "spread"))
    for tlo, thi, lab in [(0.0, 0.25, "Mon ~0.2"), (0.25, 0.45, "Tue ~0.4"),
                          (0.45, 0.65, "Wed ~0.6"), (0.65, 0.85, "Thu ~0.8"), (0.85, 1.01, "Fri ~1.0")]:
        g = s.filter((pl.col("weekly_transition_t") >= tlo) & (pl.col("weekly_transition_t") < thi))
        if g.height < 40:
            print("      %-14s N=%d (skip)" % (lab, g.height)); continue
        lo = g.filter(pl.col("w_adj") <= med)["apex"]
        hi = g.filter(pl.col("w_adj") > med)["apex"]
        print("      %-14s %7d %8.1f%% %8.1f%% %+7.1fpp" %
              (lab, g.height, (lo.mean() if len(lo) else float('nan')) * 100,
               (hi.mean() if len(hi) else float('nan')) * 100,
               ((hi.mean() if len(hi) else 0) - (lo.mean() if len(lo) else 0)) * 100))

    print("\nDONE. Read [3]/[4] FIRST: if any feature's edge is Monday-loaded and Friday-dead, it is")
    print("look-ahead — do NOT reweight on it. Reweight only on dow-stable/Friday-rising features.")


if __name__ == "__main__":
    main()
