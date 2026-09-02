"""Skill-vs-baseline, round 2 — high-information outcome + true momentum baseline.

Round 1 used the generic 2sigma/5sigma barrier (near-saturated, ~63% for
everyone -> almost no dynamic range). Round 2 uses MEAN FORWARD 15d UNDERLYING
RETURN (and forward MFE) as the outcome, computed straight from PriceHistory,
and a real MTUM-style 12-1 momentum baseline.

Questions (signal level, on scored stock-days):
  1. CLIMATOLOGY: mean fwd 15d return of a random scored stock-day.
  2. SCORE skill: mean fwd ret by bucket vs climatology (Welch t).
  3. MOMENTUM: mean fwd ret by 12-1 momentum decile.
  4. MATCHED SELECTIVITY: score>=75 vs top-K momentum (same # picks).
  5. SKILL BEYOND MOMENTUM: score>=75 vs the MOMENTUM-MATCHED base rate.
     ^ adjudicates 'real discrimination' vs 'leveraged-momentum sleeve'.

fwd window = 10 trading bars (~14 cal days, ~ the 15-cal barrier). Read-only;
all polars/vectorized (no slow barrier API). In-sample (data < holdout cutoff).
"""
import os
import sys
import math

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.bulk_cache import materialize_polars, chunked_query_by_year
from database.models.core import AlgorithmVersion

VID = AlgorithmVersion.get_active_scores_version().id
FWD_BARS = 10  # ~15 calendar days


def _build_prices():
    rows = chunked_query_by_year(
        "SELECT symbol, date, close, high FROM price_history "
        "WHERE date BETWEEN '{y_start}' AND '{y_end}' AND close IS NOT NULL AND close>0",
        start_year=2015, end_year=2027, vid=VID, timeout_ms=300_000,
    )
    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date":   [r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows],
            "close":  [float(r[2]) for r in rows],
            "high":   [float(r[3]) for r in rows],
        }
    )


def welch_t(m1, s1, n1, m2, s2, n2):
    se = math.sqrt(s1 * s1 / n1 + s2 * s2 / n2)
    return (m1 - m2) / se if se > 0 else float("nan")


def main():
    prices = materialize_polars("price_feats_raw_2015", _build_prices)
    pf = pl.read_parquet(prices).sort(["symbol", "date"])
    print(f"\nprice_history rows (2015+): {pf.height:,}", flush=True)

    hmax = pl.max_horizontal([pl.col("high").shift(-k).over("symbol") for k in range(1, FWD_BARS + 1)])
    pf = pf.with_columns(
        mom=(pl.col("close").shift(21).over("symbol") / pl.col("close").shift(252).over("symbol") - 1.0),
        fwd_ret=(pl.col("close").shift(-FWD_BARS).over("symbol") / pl.col("close") - 1.0),
        fwd_mfe=(hmax / pl.col("close") - 1.0),
    ).select(["symbol", "date", "mom", "fwd_ret", "fwd_mfe"])

    # scored universe (cached round-1 parquet: symbol, date, overall)
    sc = pl.read_parquet(materialize_polars(f"skill_v{VID}_allscores_2016", lambda: (_ for _ in ()).throw(RuntimeError("missing"))))
    df = sc.join(pf, on=["symbol", "date"], how="inner").drop_nulls(["fwd_ret"])
    print(f"scored stock-days with fwd_ret: {df.height:,}  (mom coverage: "
          f"{df['mom'].is_not_null().sum():,})\n", flush=True)

    overall = df["overall"].to_numpy()
    fr = df["fwd_ret"].to_numpy().astype(float) * 100.0   # %
    mfe = df["fwd_mfe"].to_numpy().astype(float) * 100.0
    mom = df["mom"].to_numpy().astype(float)

    cm, cs, cn = fr.mean(), fr.std(), len(fr)
    print("=== 1. CLIMATOLOGY (mean fwd 15d underlying return, random scored stock-day) ===")
    print(f"  mean fwd_ret = {cm:+.3f}%   mean fwd_MFE = {mfe.mean():+.3f}%   (N={cn:,})\n")

    print("=== 2. SCORE skill: mean fwd_ret by bucket vs climatology ===")
    print(f"{'bucket':>8} | {'N':>7} | {'fwd_ret':>9} | {'vs clim':>9} | {'t':>6} | {'fwd_MFE':>9}")
    for thr in (70, 75, 80, 85, 90):
        m = overall >= thr
        if m.sum() < 5:
            continue
        bm, bs, bn = fr[m].mean(), fr[m].std(), int(m.sum())
        t = welch_t(bm, bs, bn, cm, cs, cn)
        print(f"{('>='+str(thr)):>8} | {bn:>7,} | {bm:+8.3f}% | {bm-cm:+8.3f} | {t:+5.2f} | {mfe[m].mean():+8.3f}%")
    print()

    mm = ~np.isnan(mom)
    if mm.sum() < 5000:
        print("=== 3-5 SKIPPED: momentum coverage too low.\n"); return
    mv, fv, sv, mfev = mom[mm], fr[mm], overall[mm], mfe[mm]
    dec = np.quantile(mv, np.linspace(0, 1, 11))
    print(f"=== 3. MOMENTUM baseline: mean fwd_ret by 12-1 momentum decile (N={mm.sum():,}) ===")
    print(f"{'decile':>7} | {'mom range':>20} | {'N':>7} | {'fwd_ret':>9}")
    for i in range(10):
        lo, hi = dec[i], dec[i + 1]
        d = (mv >= lo) & (mv <= hi) if i == 9 else (mv >= lo) & (mv < hi)
        if d.sum():
            print(f"{i+1:>7} | {lo:>+8.2%}..{hi:>+8.2%} | {d.sum():>7,} | {fv[d].mean():+8.3f}%")
    print()

    K = int((sv >= 75).sum()); sel = K / len(sv)
    topm = mv >= np.quantile(mv, 1 - sel)
    s75 = sv >= 75
    print("=== 4. MATCHED SELECTIVITY: score>=75 vs top-momentum (same # picks) ===")
    print(f"  score>=75       : fwd_ret {fv[s75].mean():+.3f}%  MFE {mfev[s75].mean():+.3f}%  (N={int(s75.sum()):,})")
    print(f"  top-{sel*100:.1f}% momentum : fwd_ret {fv[topm].mean():+.3f}%  MFE {mfev[topm].mean():+.3f}%  (N={int(topm.sum()):,})")
    t = welch_t(fv[s75].mean(), fv[s75].std(), int(s75.sum()), fv[topm].mean(), fv[topm].std(), int(topm.sum()))
    print(f"  score - momentum = {fv[s75].mean()-fv[topm].mean():+.3f}pp   t={t:+.2f}\n")

    print("=== 5. SKILL BEYOND MOMENTUM (score>=75 vs momentum-matched base rate) ===")
    ctrl = sv < 70
    n_hi_tot = int(s75.sum())
    exp = 0.0; var_exp = 0.0
    print(f"{'decile':>7} | {'N(75+)':>7} | {'fwd 75+':>9} | {'fwd ctrl<70':>11} | {'lift':>6}")
    for i in range(10):
        lo, hi = dec[i], dec[i + 1]
        d = (mv >= lo) & (mv <= hi) if i == 9 else (mv >= lo) & (mv < hi)
        hi_m, lo_m = d & s75, d & ctrl
        nh, nl = int(hi_m.sum()), int(lo_m.sum())
        if nh == 0 or nl == 0:
            continue
        m_hi, m_lo, s_lo = fv[hi_m].mean(), fv[lo_m].mean(), fv[lo_m].std()
        exp += nh * m_lo
        var_exp += (nh ** 2) * (s_lo * s_lo / nl)
        print(f"{i+1:>7} | {nh:>7,} | {m_hi:+8.3f}% | {m_lo:+10.3f}% | {m_hi-m_lo:+5.2f}")
    exp_wr = exp / n_hi_tot
    act = fv[s75]
    act_wr = act.mean()
    se = math.sqrt(act.var() / n_hi_tot + var_exp / (n_hi_tot ** 2))
    t = (act_wr - exp_wr) / se if se > 0 else float("nan")
    print(f"  ACTUAL 75+ mean fwd_ret    = {act_wr:+.3f}%  (N={n_hi_tot:,})")
    print(f"  momentum-MATCHED base rate = {exp_wr:+.3f}%   (what 75+ would get from its momentum mix alone)")
    print(f"  SKILL BEYOND MOMENTUM      = {act_wr-exp_wr:+.3f}pp   t={t:+.2f}")
    print("  ^ ~0 => leveraged-momentum sleeve; clearly +ve, t>2 => discrimination beyond trend-following.\n")
    print("NOTE: fwd = 10 trading bars (~15 cal). momentum = 12-1 (close[t-21]/close[t-252]). In-sample.")


if __name__ == "__main__":
    main()
