"""Diagnostic (NOT a ship hypothesis): is the SCORE itself inflated by a violent
prior earnings gap?

Motivated by E2: the prior_earn_jump_pct effect lives entirely in the 75-79 band
and is exactly zero at 80+. One reading is score CONTAMINATION -- a big gap lifts
the momentum-family components (rsi/macd/stoch), pushing a name that is really a
70 across the 75 gate on an unrepeatable event. If so, T3_high 75-79 signals
should show a systematically different component MIX than clean 75-79 signals:
momentum-family up, trend (the slow, structural member) down.

This is descriptive. It carries no verdict leg and licenses no mechanism.
ASCII output only.
"""
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / ".cache/event_noise_decomp/classified_ledger.parquet"

COMPS = ["c_trend", "c_bb", "c_rsi", "c_macd", "c_stoch", "c_technical_alignment"]


def main():
    df = pl.read_parquet(LEDGER).filter(
        (pl.col("coverage") == "ok") & (~pl.col("post_cutoff"))
        & pl.col("prior_earn_jump_pct").is_not_null()
    )
    cut = df["prior_earn_jump_pct"].quantile(2 / 3)
    df = df.with_columns((pl.col("prior_earn_jump_pct") > cut).alias("t3hi"))

    print("=" * 94)
    print("SCORE-CONTAMINATION DIAGNOSTIC -- component mix, T3_high vs rest")
    print("=" * 94)

    for band_lab, band in [
        ("ALL 75+", df),
        ("75-79 (where the effect lives)", df.filter(pl.col("overall") < 80)),
        ("80+ (where it vanishes)", df.filter(pl.col("overall") >= 80)),
    ]:
        hi = band.filter(pl.col("t3hi"))
        lo = band.filter(~pl.col("t3hi"))
        if hi.height < 50 or lo.height < 50:
            continue
        print(f"\n[{band_lab}]  N_hi={hi.height}  N_lo={lo.height}")
        print(f"  {'component':<26}{'T3_high':>10}{'rest':>10}{'delta':>10}{'t':>9}")
        for c in COMPS:
            a = hi[c].drop_nulls().to_numpy().astype(float)
            b = lo[c].drop_nulls().to_numpy().astype(float)
            if len(a) < 30 or len(b) < 30:
                continue
            d = a.mean() - b.mean()
            se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
            print(f"  {c:<26}{a.mean():>10.2f}{b.mean():>10.2f}{d:>+10.2f}"
                  f"{(d / se if se else 0):>+9.2f}")
        # overall score itself
        a = hi["overall"].to_numpy().astype(float)
        b = lo["overall"].to_numpy().astype(float)
        d = a.mean() - b.mean()
        se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        print(f"  {'overall':<26}{a.mean():>10.2f}{b.mean():>10.2f}{d:>+10.2f}"
              f"{(d / se if se else 0):>+9.2f}")

    print("\n" + "=" * 94)
    print("SCORE DISTRIBUTION -- is T3_high crowded at the gate boundary?")
    print("=" * 94)
    print(f"  {'cohort':<10}{'N':>7}{'mean':>8}{'p25':>7}{'p50':>7}{'p75':>7}"
          f"{'share 75-77':>13}{'share 80+':>11}")
    for lab, sub in [("rest", df.filter(~pl.col("t3hi"))),
                     ("T3_high", df.filter(pl.col("t3hi")))]:
        o = sub["overall"]
        print(f"  {lab:<10}{sub.height:>7}{o.mean():>8.2f}{o.quantile(.25):>7.0f}"
              f"{o.quantile(.5):>7.0f}{o.quantile(.75):>7.0f}"
              f"{(o <= 77).mean():>13.3f}{(o >= 80).mean():>11.3f}")

    print("\n" + "=" * 94)
    print("TIME SINCE THE CONTAMINATING EVENT -- does the effect decay?")
    print("=" * 94)
    print("  (if a violent gap contaminates the score, the damage should fade as the")
    print("   gap ages out of the indicator lookbacks)")
    if "jump_rate_252" in df.columns:
        print(f"  corr(prior_earn_jump_pct, jump_rate_252) = "
              f"{np.corrcoef(df['prior_earn_jump_pct'].to_numpy(), df['jump_rate_252'].to_numpy())[0, 1]:+.4f}")
    print("\n  EV by (T3_high x pre_jump_20) -- was there ALSO a recent jump?")
    print(f"  {'cell':<28}{'N':>7}{'WR%':>9}{'EV':>10}")
    for t3 in [False, True]:
        for pj in [False, True]:
            sub = df.filter((pl.col("t3hi") == t3) & (pl.col("pre_jump_20") == pj))
            if sub.height < 50:
                continue
            print(f"  t3hi={str(t3):<6} pre_jump20={str(pj):<6}{sub.height:>7}"
                  f"{sub['apex_win'].mean() * 100:>9.2f}{sub['apex_ev'].mean():>10.4f}")


if __name__ == "__main__":
    sys.exit(main())
