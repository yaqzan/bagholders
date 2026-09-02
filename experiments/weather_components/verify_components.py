"""Forecast-verification scorecard for the scoring ENSEMBLE MEMBERS.

The substrate (skill_vs_baseline) verified the OVERALL score on the funded
30dte_apex CALL payoff. This verifies the 6 ensemble members
(trend, bb, rsi, macd, stoch, technical_alignment) the way a forecast scientist
verifies ensemble members:

  1. CLIMATOLOGY              base rate every member must beat.
  2. MEMBER SKILL (univariate) is each component a skillful CALL forecaster alone?
  3. DIVERSITY / REDUNDANCY    pairwise correlation -> effective ensemble size.
  4. TANDEM / ANTI-SYNERGY     does pair-agreement beat the best single? (the core ask)
  5. CONDITIONAL INFORMATION   multivariate (OLS) betas -> who adds skill GIVEN the rest;
                               a univariate-strong / multivariate-dead member is redundant.
  6. RELIABILITY / RESOLUTION  per-member calibration curve + Murphy resolution.
  7. ENSEMBLE-SPREAD SKILL     disagreement -> apex risk (generalizes SPREAD_TILT),
                               by tier + leave-one-out (whose disagreement carries the signal).

EV map (apex payoff): win/stop/expire = +0.30 / -0.70 / -0.40, 30d window.
Read-only: stored scores + barrier cache. In-sample (data end < holdout cutoff).
"""
import os
import sys
import math
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion

VID = AlgorithmVersion.get_active_scores_version().id
START_YEAR = 2016
COMPS = ["trend", "bb", "rsi", "macd", "stoch", "ta"]
EVMAP = {"win": +0.30, "stop": -0.70, "expire": -0.40}
PERIOD = "30d"
SAMPLE_N = int(os.environ.get("SAMPLE_N", "300000"))


def two_prop_z(x1, n1, x2, n2):
    if not n1 or not n2:
        return float("nan")
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se > 0 else float("nan")


def mean_diff_t(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return (a.mean() - b.mean()) / se if se > 0 else float("nan")


def apex_result_map(df):
    """{(sym,date): 'win'|'stop'|'expire'} on the 30dte_apex CALL payoff."""
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(df["symbol"], df["date"])]
    results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    m = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EVMAP:
            m[(r["symbol"], dk)] = sw["result"]
    return m, skipped


def attach_apex(df):
    m, skipped = apex_result_map(df)
    res = [m.get((s, d)) for s, d in zip(df["symbol"], df["date"])]
    ev = [EVMAP[r] if r is not None else None for r in res]
    win = [(1 if r == "win" else 0) if r is not None else None for r in res]
    df = df.with_columns(
        apex_res=pl.Series(res, dtype=pl.Utf8),
        apex_ev=pl.Series(ev, dtype=pl.Float64),
        apex_win=pl.Series(win, dtype=pl.Int64),
    ).filter(pl.col("apex_res").is_not_null())
    return df, skipped


def grp(df, mask):
    sub = df.filter(mask)
    if sub.height == 0:
        return 0, float("nan"), float("nan"), np.array([])
    ev = sub["apex_ev"].to_numpy()
    wr = sub["apex_win"].mean() * 100
    return sub.height, wr, ev.mean() * 100, ev


def main():
    parquet = materialize_polars(f"weather_comp_v{VID}_{START_YEAR}", None)
    full = pl.read_parquet(parquet)
    print(f"\n{'='*78}\nWEATHER-FORECAST COMPONENT VERIFICATION  (v{VID}, 30dte_apex CALL payoff)\n{'='*78}")
    print(f"universe rows (v{VID}, {START_YEAR}+): {full.height:,}")

    # ---- universe sample (unbiased for climatology / member skill / tandem) ----
    uni = full.sample(n=min(SAMPLE_N, full.height), seed=17) if full.height > SAMPLE_N else full
    uni, sk1 = attach_apex(uni)
    print(f"universe apex-matched: {uni.height:,} (sample {min(SAMPLE_N, full.height):,}; {sk1:,} uncached)")

    # ---- funded book: all >=75 (the Apex traded set), full (no sample) ----
    f75 = full.filter(pl.col("overall") >= 75)
    f75, sk2 = attach_apex(f75)
    print(f"funded >=75 apex-matched: {f75.height:,} ({sk2:,} uncached)\n")

    cn, cwr, cev, cevarr = grp(uni, pl.lit(True))
    base_ev = cev

    # ===================== 1. CLIMATOLOGY =====================
    print("=== 1. CLIMATOLOGY (random call, apex payoff) ===")
    print(f"  WR={cwr:.2f}%   EV={cev:+.2f}%   N={cn:,}")
    print("  ^ base rate every member must beat.\n")

    # ===================== 2. MEMBER SKILL (univariate) =====================
    print("=== 2. MEMBER SKILL — univariate apex skill of each component as a CALL forecaster ===")
    print(f"{'member':>9} | {'N(>=70)':>9} | {'WR':>6} | {'EV':>7} | {'dEV vs clim':>11} | {'t(EV)':>6} | {'EV decile 1->10 (low->high score)':>40}")
    for c in COMPS + ["overall"]:
        n, wr, ev, evarr = grp(uni, pl.col(c) >= 70)
        t = mean_diff_t(evarr, cevarr)
        vals = uni[c].to_numpy().astype(float)
        evall = uni["apex_ev"].to_numpy()
        dec = np.quantile(vals, np.linspace(0, 1, 11))
        dstr = []
        for i in range(10):
            lo, hi = dec[i], dec[i + 1]
            dm = (vals >= lo) & (vals <= hi) if i == 9 else (vals >= lo) & (vals < hi)
            dstr.append(f"{evall[dm].mean()*100:+4.1f}" if dm.sum() else "  . ")
        print(f"{c:>9} | {n:>9,} | {wr:5.1f}% | {ev:+6.2f}% | {ev-base_ev:+11.2f} | {t:+5.2f} | {' '.join(dstr)}")
    print("  ^ dEV>0 & t>=2 = individually skillful. decile shows monotonic vs inverted-U.\n")

    # ===================== 3. DIVERSITY / REDUNDANCY =====================
    print("=== 3. DIVERSITY — pairwise correlation of component scores (redundant members add no info) ===")
    M = np.column_stack([uni[c].to_numpy().astype(float) for c in COMPS])
    C = np.corrcoef(M, rowvar=False)
    print(f"{'':>6} " + " ".join(f"{c:>6}" for c in COMPS))
    for i, c in enumerate(COMPS):
        print(f"{c:>6} " + " ".join(f"{C[i,j]:+.2f}" if j <= i else "    . " for j in range(len(COMPS))))
    ov = uni["overall"].to_numpy().astype(float)
    print("  corr(component, overall): " + "  ".join(f"{c}={np.corrcoef(uni[c].to_numpy().astype(float), ov)[0,1]:+.2f}" for c in COMPS))
    # effective ensemble size (participation ratio of eigenvalues of corr matrix)
    eig = np.linalg.eigvalsh(C)
    eff = (eig.sum() ** 2) / (eig ** 2).sum()
    print(f"  effective ensemble size (corr-matrix participation ratio) = {eff:.2f} of {len(COMPS)} members\n")

    # ===================== 4. TANDEM / ANTI-SYNERGY =====================
    print("=== 4. TANDEM — does pair-agreement (both bullish >=70) beat the BEST single? ===")
    print("  lift = EV(both>=70) - max(EV(A only), EV(B only)).  <0 = anti-synergy (don't combine these signs)")
    print(f"{'pair':>12} | {'N both':>7} | {'EV both':>7} | {'EV A-only':>9} | {'EV B-only':>9} | {'tandem lift':>11} | {'t':>6}")
    pairs = []
    base5 = COMPS[:5]  # the 5 base members SPREAD_TILT uses
    for i in range(len(base5)):
        for j in range(i + 1, len(base5)):
            A, B = base5[i], base5[j]
            nb, wb, eb, evb = grp(uni, (pl.col(A) >= 70) & (pl.col(B) >= 70))
            na, wa, ea, eva = grp(uni, (pl.col(A) >= 70) & (pl.col(B) < 70))
            nbo, wbo, ebo, evbo = grp(uni, (pl.col(B) >= 70) & (pl.col(A) < 70))
            if nb < 50 or na < 30 or nbo < 30:
                continue
            best_single = max(ea, ebo)
            best_arr = eva if ea >= ebo else evbo
            lift = eb - best_single
            t = mean_diff_t(evb, best_arr)
            pairs.append((f"{A}+{B}", nb, eb, ea, ebo, lift, t))
    for p in sorted(pairs, key=lambda x: x[5]):
        print(f"{p[0]:>12} | {p[1]:>7,} | {p[2]:+6.2f}% | {p[3]:+8.2f}% | {p[4]:+8.2f}% | {p[5]:+11.2f} | {p[6]:+5.2f}")
    print()

    # ===================== 5. CONDITIONAL INFORMATION (multivariate OLS) =====================
    print("=== 5. CONDITIONAL INFORMATION — OLS of apex_win on standardized members (who adds GIVEN the rest) ===")
    y = uni["apex_win"].to_numpy().astype(float)
    Xcols = []
    for c in COMPS:
        v = uni[c].to_numpy().astype(float)
        Xcols.append((v - v.mean()) / (v.std() + 1e-9))
    X = np.column_stack([np.ones(len(y))] + Xcols)
    XtX = X.T @ X
    XtXi = np.linalg.inv(XtX)
    beta = XtXi @ (X.T @ y)
    resid = y - X @ beta
    s2 = (resid @ resid) / (len(y) - X.shape[1])
    se = np.sqrt(np.diag(s2 * XtXi))
    tb = beta / se
    # univariate slope (standardized) for comparison
    print(f"{'member':>9} | {'multivar beta(/SD on win)':>24} | {'t':>7} | {'univar dWin/SD':>15} | {'verdict':>22}")
    for k, c in enumerate(COMPS):
        b, t = beta[k + 1] * 100, tb[k + 1]
        v = uni[c].to_numpy().astype(float)
        vs = (v - v.mean()) / (v.std() + 1e-9)
        uni_b = np.polyfit(vs, y, 1)[0] * 100
        if abs(t) < 2:
            verdict = "redundant (no add)"
        elif (b > 0) != (uni_b > 0):
            verdict = "SIGN-FLIP (suppressor)"
        elif b > 0:
            verdict = "adds skill"
        else:
            verdict = "NEGATIVE in ensemble"
        print(f"{c:>9} | {b:+23.2f} | {t:+6.2f} | {uni_b:+14.2f} | {verdict:>22}")
    print("  ^ multivar~0 but univar>0 = its apparent skill is borrowed from a correlated member.\n")

    # ===================== 6. RELIABILITY / RESOLUTION =====================
    print("=== 6. RELIABILITY — realized apex-WR by score band (monotonic? + Murphy resolution) ===")
    bands = [(50, 60), (60, 70), (70, 75), (75, 80), (80, 85), (85, 90), (90, 101)]
    base_wr = uni["apex_win"].mean()
    print(f"{'member':>9} | " + " | ".join(f"{lo}-{hi-1}" for lo, hi in bands) + " | resolution")
    for c in COMPS + ["overall"]:
        v = uni[c].to_numpy().astype(float)
        w = uni["apex_win"].to_numpy().astype(float)
        cells, resol = [], 0.0
        for lo, hi in bands:
            bm = (v >= lo) & (v < hi)
            if bm.sum() >= 30:
                wr = w[bm].mean()
                cells.append(f"{wr*100:4.1f}")
                resol += bm.sum() * (wr - base_wr) ** 2
            else:
                cells.append("  . ")
        resol = resol / len(w)
        print(f"{c:>9} | " + " | ".join(f"{x:>5}" for x in cells) + f" | {resol*1e4:6.1f}")
    print("  ^ a calibrated member rises monotonically L->R. resolution(x1e4) = informativeness (higher=better).\n")

    # ===================== 7. ENSEMBLE-SPREAD SKILL (funded book) =====================
    print("=== 7. ENSEMBLE-SPREAD SKILL on the FUNDED >=75 book (generalizes SPREAD_TILT) ===")
    sp = np.std(np.column_stack([f75[c].to_numpy().astype(float) for c in base5]), axis=1)
    f75 = f75.with_columns(spread=pl.Series(sp))
    for label, mask in [("ALL 75+", pl.lit(True)), ("75-79", (pl.col("overall") >= 75) & (pl.col("overall") < 80)),
                        ("80-84", (pl.col("overall") >= 80) & (pl.col("overall") < 85)),
                        ("85+", pl.col("overall") >= 85)]:
        sub = f75.filter(mask)
        if sub.height < 90:
            print(f"  {label:>8}: N={sub.height} (too thin)")
            continue
        s = sub["spread"].to_numpy()
        qs = np.quantile(s, [1/3, 2/3])
        lo = sub.filter(pl.col("spread") <= qs[0]); hi = sub.filter(pl.col("spread") > qs[1])
        loev, hiev = lo["apex_ev"].to_numpy(), hi["apex_ev"].to_numpy()
        t = mean_diff_t(loev, hiev)
        print(f"  {label:>8}: low-spread EV {loev.mean()*100:+5.2f}% (N={len(loev):,}) | "
              f"high-spread EV {hiev.mean()*100:+5.2f}% (N={len(hiev):,}) | low-hi {(loev.mean()-hiev.mean())*100:+5.2f}pp t={t:+.2f}")
    # leave-one-out: whose disagreement carries the spread-risk signal (75-79, where SPREAD_TILT lives)
    print("  leave-one-out spread-skill at 75-79 (drop each member from the spread; weaker low-hi = that member CARRIED the signal):")
    band = f75.filter((pl.col("overall") >= 75) & (pl.col("overall") < 80))
    if band.height >= 90:
        full_arr = np.column_stack([band[c].to_numpy().astype(float) for c in base5])
        for drop in range(len(base5)):
            keep = [k for k in range(len(base5)) if k != drop]
            spd = np.std(full_arr[:, keep], axis=1)
            qs = np.quantile(spd, [1/3, 2/3])
            ev = band["apex_ev"].to_numpy()
            loev, hiev = ev[spd <= qs[0]], ev[spd > qs[1]]
            print(f"     drop {base5[drop]:>6}: low-hi {(loev.mean()-hiev.mean())*100:+5.2f}pp")
    print("\nNOTE: in-sample (data end < holdout 2026-06-15). apex EV map +0.30/-0.70/-0.40, 30d.")


if __name__ == "__main__":
    main()
