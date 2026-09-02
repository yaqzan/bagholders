"""Spread-skill ROUND 2 — does component disagreement predict OPTION EV (not just
generic directional WR), CONTROLLING FOR the overall score?

Round 1 (`spread_skill.py`) asked the directional question on the GENERIC barrier
(K=2sigma/M=5sigma, the cached dashboard metric) and found a low-spread 75-79
edge of +1.76pp WR15. Round 2 re-asks it on the LIVE APEX predictand and the real
asymmetric payoff:

  - barrier_set='30dte_apex'  (CALL win @1.092sigma / stop @2.548sigma, 30d window)
  - EV map  {win:+0.30, stop:-0.70, expire:-0.40}  (the Apex +30%/-70%/-40% payoff)
  - per-band low-vs-high spread tercile compared via a WELCH t-test on MEAN EV
    (unequal-variance two-sample t — NOT a two-proportion z; we are comparing the
    mean of a 3-valued EV variable, whose tercile variances differ, not a binary).

The question this answers: the round-1 generic-barrier 75-79 low-spread +1.76pp WR
edge — does it SURVIVE on the option predictand, where the -70% stop dominates the
+30% win? A WR edge that doesn't move EV is cosmetic for the funded Apex book.

DATA SHORTCUT (verified): reads the EXISTING v73 component parquet
('ensemble_spread_v73_calls_2016') directly. v74==v73 at the pre_boost checkpoint
bit-exact, and component spread is computed on the PRE-pre_boost component columns
(trend/bb/rsi/macd/stoch), which are identical across v73/v74. So the v73 spread ==
the v74 spread — zero-cost, no recalc, no recompute.

In-sample only. The 30dte_apex barrier_set is version-agnostic (keyed on price
path, not score), so it applies cleanly to these peaks regardless of score version.
Holdout (known-issues #11): cutoff 2026-06-15; data ends 2026-06-12, so this read
is fully in-sample (the OOS window has not opened). pre_cutoff_filter applied as a
belt-and-suspenders no-op + reported.

Read-only: no re-score, no MC, no recalculate, no writes.
"""
import os
import sys
import math
from collections import namedtuple

import numpy as np
import polars as pl

# Pin imports to THIS checkout (avoid the worktree-PYTHONPATH trap; harmless on main).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
assert os.path.abspath(__file__).startswith(_ROOT), "running from an unexpected checkout"

from database.bulk_cache import materialize_polars, chunked_query_by_year
from database.barrier_cache import peaks_to_swing_results, BARRIER_SETS

# Round-2 constants
BARRIER = "30dte_apex"
PERIOD = "30d"                                   # apex predictand is the 30d window
EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}  # Apex payoff (matches verify_scorecard)
MEMBERS = ["trend", "bb", "rsi", "macd", "stoch"]    # 5-member ensemble (identical to round 1)

# v73 == v74 at pre_boost bit-exact; spread is on pre-pre_boost components => identical.
# Reuse the EXISTING round-1 v73 parquet by name (cache hit, no rebuild).
DATA_NAME = "ensemble_spread_v73_calls_2016"
DATA_VID = 73
START_YEAR, END_YEAR = 2016, 2027


def _build():
    """Fallback builder — only runs if the v73 parquet is somehow absent."""
    rows = chunked_query_by_year(
        "SELECT symbol, date, overall, trend, bb, rsi, macd, stoch, technical_alignment "
        "FROM scores WHERE version_id={vid} AND overall>=70 "
        "AND date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=START_YEAR, end_year=END_YEAR, vid=DATA_VID,
    )
    return pl.DataFrame(
        {
            "symbol":  [r[0] for r in rows],
            "date":    [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "overall": [int(r[2]) for r in rows],
            "trend":   [float(r[3]) for r in rows],
            "bb":      [float(r[4]) for r in rows],
            "rsi":     [float(r[5]) for r in rows],
            "macd":    [float(r[6]) for r in rows],
            "stoch":   [float(r[7]) for r in rows],
            "ta":      [float(r[8]) for r in rows],
        }
    )


def welch_t(a, b):
    """Welch's unequal-variance two-sample t-test on means(a) vs means(b).

    Returns (t, mean_a - mean_b). Positive t => mean(a) > mean(b), i.e. for the
    low-vs-high tercile call, positive t means the LOW-spread tercile has higher EV.
    """
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan"), float("nan")
    ma, mb = a.mean(), b.mean()
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return float("nan"), ma - mb
    return (ma - mb) / se, ma - mb


def main():
    parquet = materialize_polars(DATA_NAME, _build)
    df = pl.read_parquet(parquet)
    n_raw = df.height

    # --- holdout discipline (no-op here: data ends 2026-06-12 < cutoff 2026-06-15) ---
    try:
        from experiments._holdout import pre_cutoff_filter, cutoff_iso
        df = pre_cutoff_filter(df)
        cutoff = cutoff_iso()
    except Exception:
        cutoff = "(holdout helper unavailable)"
    n_insample = df.height
    print(f"\nROUND 2 — Apex spread-skill  (barrier={BARRIER}, predictand={PERIOD}, "
          f"EV={EV})")
    print(f"data: {DATA_NAME} (v73 == v74 pre_boost; spread on pre-pre_boost components => identical)")
    print(f"rows: {n_raw:,} pulled, {n_insample:,} in-sample (<= {cutoff})")

    # --- component spread (5-member, population variance — IDENTICAL to round 1) ---
    mean5 = sum(pl.col(c) for c in MEMBERS) / 5.0
    var5 = sum((pl.col(c) - mean5) ** 2 for c in MEMBERS) / 5.0
    df = df.with_columns(spread=var5.sqrt())

    # --- apex barrier outcome -> option EV via the version-agnostic cache ---
    from datetime import date as _date
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [
        Peak(s, _date.fromisoformat(d), int(o))
        for s, d, o in zip(df["symbol"], df["date"], df["overall"])
    ]
    results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set=BARRIER)
    ev_map, res_map = {}, {}
    for r in results:
        dk = r["date"]
        dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EV:
            ev_map[(r["symbol"], dk)] = EV[sw["result"]]
            res_map[(r["symbol"], dk)] = sw["result"]
    print(f"apex barrier join: {len(ev_map):,} peaks resolved "
          f"({skipped:,} uncached/insufficient)")

    df = df.with_columns(
        ev=pl.struct(["symbol", "date"]).map_elements(
            lambda x: ev_map.get((x["symbol"], x["date"]), float("nan")),
            return_dtype=pl.Float64,
        )
    ).filter(pl.col("ev").is_not_nan())
    n_usable = df.height
    print(f"usable (component spread + apex EV): {n_usable:,} rows\n")

    sc = df["overall"].to_numpy()
    sp = df["spread"].to_numpy()
    ev = df["ev"].to_numpy()

    bands = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 100)]

    print("=== MEAN apex option EV by SPREAD tercile, WITHIN score band  "
          "(does disagreement predict EV at fixed score?) ===")
    print(f"{'band':>8} | {'N':>6} | {'baseEV':>8} | {'loSpr EV/N':>15} | "
          f"{'midSpr EV/N':>15} | {'hiSpr EV/N':>15} | {'lo-hi pp':>8} | {'Welch t':>8}")

    agg_lo, agg_hi = [], []
    band_rows = []
    for lo, hi in bands:
        m = (sc >= lo) & (sc <= hi)
        if m.sum() < 60:
            continue
        spv, evv = sp[m], ev[m]
        q1, q2 = np.quantile(spv, [1 / 3, 2 / 3])
        loT = spv <= q1                 # LOW spread (agreement)
        hiT = spv > q2                  # HIGH spread (disagreement)
        midT = (~loT) & (~hiT)
        base = evv.mean() * 100
        lo_ev = evv[loT]
        hi_ev = evv[hiT]
        mid_ev = evv[midT]
        t, diff = welch_t(lo_ev, hi_ev)
        ldd = lo_ev.mean() * 100 if len(lo_ev) else float("nan")
        mdd = mid_ev.mean() * 100 if len(mid_ev) else float("nan")
        hdd = hi_ev.mean() * 100 if len(hi_ev) else float("nan")
        print(f"{lo}-{hi:>3} | {int(m.sum()):>6} | {base:7.2f}% | "
              f"{ldd:7.2f}% {len(lo_ev):>5} | {mdd:7.2f}% {len(mid_ev):>5} | "
              f"{hdd:7.2f}% {len(hi_ev):>5} | {(ldd-hdd):+7.2f} | {t:+7.2f}")
        agg_lo.append(lo_ev)
        agg_hi.append(hi_ev)
        band_rows.append((f"{lo}-{hi}", int(m.sum()), ldd, hdd, ldd - hdd, t, len(lo_ev), len(hi_ev)))

    # pooled (concatenate all low-tercile EVs vs all high-tercile EVs)
    if agg_lo:
        pl_lo = np.concatenate(agg_lo)
        pl_hi = np.concatenate(agg_hi)
        pt, pdiff = welch_t(pl_lo, pl_hi)
        print(f"{'POOLED':>8} | {'':>6} | {'':>8} | "
              f"{pl_lo.mean()*100:7.2f}% {len(pl_lo):>5} | {'':>15} | "
              f"{pl_hi.mean()*100:7.2f}% {len(pl_hi):>5} | "
              f"{(pl_lo.mean()-pl_hi.mean())*100:+7.2f} | {pt:+7.2f}")
    print("  (lo tercile = LOW spread/agreement; hi tercile = HIGH spread/disagreement;")
    print("   positive lo-hi => LOW-spread signals carry higher apex EV at fixed score.)\n")

    # ---- result-mix sanity: how the EV moves (win/stop/expire shares) lo vs hi at 75-79 ----
    print("=== result-mix at 75-79 (why EV moves) ===")
    m = (sc >= 75) & (sc <= 79)
    if m.sum() >= 60:
        spv = sp[m]
        q1, q2 = np.quantile(spv, [1 / 3, 2 / 3])
        # rebuild keys for the 75-79 band to look up result strings
        sub = df.filter((pl.col("overall") >= 75) & (pl.col("overall") <= 79))
        sub_keys = list(zip(sub["symbol"], sub["date"]))
        sub_sp = sub["spread"].to_numpy()
        sq1, sq2 = np.quantile(sub_sp, [1 / 3, 2 / 3])
        for label, mask in (("loSpr", sub_sp <= sq1), ("hiSpr", sub_sp > sq2)):
            keys = [sub_keys[i] for i in np.where(mask)[0]]
            counts = {"win": 0, "stop": 0, "expire": 0}
            for k in keys:
                r = res_map.get(k)
                if r in counts:
                    counts[r] += 1
            tot = sum(counts.values()) or 1
            print(f"  {label}: N={tot:>5}  win {counts['win']/tot*100:5.1f}%  "
                  f"stop {counts['stop']/tot*100:5.1f}%  expire {counts['expire']/tot*100:5.1f}%")
    print()

    # ---- headline ----
    print("=== HEADLINE ===")
    for lab, n, ldd, hdd, diff, t, ln, hn in band_rows:
        verdict = ("EDGE" if t >= 2 else "anti" if t <= -2 else "flat")
        print(f"  {lab:>6}: lo-spread EV {ldd:+6.2f}% vs hi-spread {hdd:+6.2f}%  "
              f"(lo-hi {diff:+5.2f}pp, Welch t {t:+5.2f}, N lo/hi {ln}/{hn})  [{verdict}]")
    if agg_lo:
        print(f"  POOLED: lo {pl_lo.mean()*100:+6.2f}% vs hi {pl_hi.mean()*100:+6.2f}%  "
              f"(lo-hi {(pl_lo.mean()-pl_hi.mean())*100:+5.2f}pp, Welch t {pt:+5.2f})")
    print("\nROUND-1 reference: generic-barrier 75-79 low-spread WR15 edge was +1.76pp.")
    print("This round-2 read asks whether that edge SURVIVES as APEX EV "
          "(+0.30/-0.70/-0.40). In-sample only; Stage-3 hypothesis, not ship evidence.")
    print("85+/90+ are N-underpowered — do not over-read.")


if __name__ == "__main__":
    main()
