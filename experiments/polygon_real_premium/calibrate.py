"""Does the engine's MODELED option return match REAL traded contracts?

The engine never observes an option price. It prices a call from realized vol
(premium_pct = PREMIUM_MULT * vol/100, PREMIUM_MULT=1.82 at 30 DTE) and then
decides win/stop by walking SIGMA barriers on the UNDERLYING: +1.092 sigma is
declared a +30% option win, -2.548 sigma a -70% option stop (the apex15 map).

With 3,339 real contract price paths we can test both links directly:
  L1  COST      : entry_premium_real vs the modeled premium
  L2  PAYOFF    : does the real contract actually reach +30% / -70%, and how
                  often, vs the modeled win/stop/expire rates?
  L3  HORIZON   : when does it get there vs the modeled hold?

Restricted to the apex15 horizon (15 market days) for an apples-to-apples read,
and to a tight DTE sub-band for the cost calibration (premium ~ sqrt(DTE), so a
21-day and a 50-day contract are not comparable on price).

Read-only. ASCII output only.
"""
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / ".cache/polygon_real_premium/real_premium_ledger.parquet"
FUNDED = ROOT / ".cache/trend_ma_lattice/ledger_v74_full.parquet"

APEX_HOLD_MKT_DAYS = 15          # apex15 barrier horizon
DTE_TIGHT = (25, 38)             # cost-calibration sub-band around the 30d nominal


def q(s, ps=(0.05, 0.25, 0.5, 0.75, 0.95)):
    return [float(s.quantile(p)) for p in ps]


def main():
    df = pl.read_parquet(LEDGER)
    kept = df.filter(pl.col("status") == "kept")
    print("=" * 92)
    print("REAL vs MODELED OPTION RETURNS -- Polygon traded contracts, v74 funded 75+ calls")
    print("=" * 92)
    print(f"  ledger rows {df.height}  kept {kept.height}  "
          f"symbols {kept['symbol'].n_unique()}")
    print(f"  window {kept['signal_date'].min()} .. {kept['signal_date'].max()}")
    liq = kept.filter(pl.col("liquid_entry")) if "liquid_entry" in kept.columns else kept
    print(f"  liquid_entry rows {liq.height} ({liq.height / max(kept.height,1):.3f})")

    # ---------------- L1 COST ------------------------------------------------
    print("\n" + "-" * 92)
    print("L1  COST -- real entry premium vs the engine's modeled premium")
    print("-" * 92)
    col = "real_model_ratio" if "real_model_ratio" in kept.columns else None
    if col is None:
        if "model_premium_abs" in kept.columns:
            kept = kept.with_columns(
                (pl.col("entry_premium_real") / pl.col("model_premium_abs")).alias("real_model_ratio"))
        else:
            kept = kept.with_columns(
                (pl.col("entry_premium_real")
                 / (pl.col("model_premium_pct") * pl.col("entry_price"))).alias("real_model_ratio"))
    tight = kept.filter((pl.col("dte_cal") >= DTE_TIGHT[0]) & (pl.col("dte_cal") <= DTE_TIGHT[1]))
    tight = tight.filter(pl.col("real_model_ratio").is_finite())
    for lab, sub in [("all kept", kept.filter(pl.col("real_model_ratio").is_finite())),
                     (f"DTE {DTE_TIGHT[0]}-{DTE_TIGHT[1]}", tight),
                     (f"DTE {DTE_TIGHT[0]}-{DTE_TIGHT[1]} + liquid",
                      tight.filter(pl.col("liquid_entry")) if "liquid_entry" in tight.columns else tight)]:
        if sub.height < 30:
            continue
        r = sub["real_model_ratio"]
        p = q(r)
        print(f"  {lab:<26} N={sub.height:>5}  p05={p[0]:.3f} p25={p[1]:.3f} "
              f"MED={p[2]:.3f} p75={p[3]:.3f} p95={p[4]:.3f}")
    med = float(tight["real_model_ratio"].median())
    print(f"\n  => the engine's modeled premium is {'OVER' if med < 1 else 'UNDER'}stated by "
          f"{abs(1 - med) * 100:.1f}% at the median (DTE {DTE_TIGHT[0]}-{DTE_TIGHT[1]})")
    print("  per-year median ratio:")
    for y in sorted(set(int(s[:4]) for s in tight["signal_date"].to_list())):
        s = tight.filter(pl.col("signal_date").str.starts_with(str(y)))
        if s.height >= 30:
            print(f"    {y}: {float(s['real_model_ratio'].median()):.3f}  (N={s.height})")

    # ---------------- L2 PAYOFF ---------------------------------------------
    print("\n" + "-" * 92)
    print(f"L2  PAYOFF -- did the REAL contract reach +30% / -70% within {APEX_HOLD_MKT_DAYS} market days?")
    print("-" * 92)
    k = kept.with_columns([
        (pl.col("tp30_touch_bar").is_not_null()
         & (pl.col("tp30_touch_bar") <= APEX_HOLD_MKT_DAYS)).alias("tp_hit"),
        (pl.col("sl70_touch_bar").is_not_null()
         & (pl.col("sl70_touch_bar") <= APEX_HOLD_MKT_DAYS)).alias("sl_hit"),
    ])
    n = k.height
    tp = k["tp_hit"].sum()
    sl = k["sl_hit"].sum()
    both = k.filter(pl.col("tp_hit") & pl.col("sl_hit")).height
    tp_first = k.filter(
        pl.col("tp_hit") & (~pl.col("sl_hit")
                            | (pl.col("tp30_touch_bar") <= pl.col("sl70_touch_bar")))).height
    neither = k.filter(~pl.col("tp_hit") & ~pl.col("sl_hit")).height
    print(f"  N={n}")
    print(f"    reached +30% (limit, intraday high)   : {tp:>5}  {tp / n:.4f}")
    print(f"    reached -70% (EOD close)              : {sl:>5}  {sl / n:.4f}")
    print(f"    reached BOTH                          : {both:>5}  {both / n:.4f}")
    print(f"    TP first (path-resolved win)          : {tp_first:>5}  {tp_first / n:.4f}")
    print(f"    reached NEITHER (expire-like)         : {neither:>5}  {neither / n:.4f}")

    fd = pl.read_parquet(FUNDED)
    print("\n  ENGINE-MODELED distribution on the same predictand (full funded ledger):")
    print(f"    modeled win rate  (apex15)            : {fd['apex_win'].mean():.4f}  (N={fd.height})")
    print("    NOTE: the modeled rate is measured on sigma barriers on the UNDERLYING;")
    print("          the real rate above is measured on the CONTRACT's own price.")
    ov = fd.with_columns(pl.col("date").cast(pl.Utf8).alias("_d")).join(
        k.select(["symbol", "signal_date", "tp_hit", "sl_hit", "real_pnl_d15"]),
        left_on=["symbol", "_d"], right_on=["symbol", "signal_date"], how="inner")
    if ov.height > 100:
        print(f"\n  MATCHED overlap (same symbol+date in both): N={ov.height}")
        print(f"    modeled apex_win on the overlap       : {ov['apex_win'].mean():.4f}")
        print(f"    real contract TP+30% reached          : {ov['tp_hit'].mean():.4f}")
        print(f"    real contract SL-70% reached          : {ov['sl_hit'].mean():.4f}")
        agree = ov.filter(pl.col("apex_win").cast(pl.Boolean) == pl.col("tp_hit")).height
        print(f"    label agreement (modeled win == real TP): {agree / ov.height:.4f}")
        for lab, cond in [("modeled WIN", pl.col("apex_win") == 1),
                          ("modeled LOSS", pl.col("apex_win") == 0)]:
            s = ov.filter(cond)
            if s.height > 30:
                print(f"      {lab:<14} N={s.height:>5}  real TP rate={s['tp_hit'].mean():.4f}  "
                      f"real SL rate={s['sl_hit'].mean():.4f}  "
                      f"median real d15 pnl={float(s['real_pnl_d15'].drop_nulls().median() or 0):+.3f}")

    # ---------------- L3 HORIZON --------------------------------------------
    print("\n" + "-" * 92)
    print("L3  HORIZON + realized return distribution (real contracts)")
    print("-" * 92)
    t = k.filter(pl.col("tp_hit"))["tp30_touch_bar"]
    if t.len() > 30:
        p = q(t)
        print(f"  market days to +30% | p05={p[0]:.0f} p25={p[1]:.0f} MED={p[2]:.0f} "
              f"p75={p[3]:.0f} p95={p[4]:.0f}  (N={t.len()})")
    for c in ["real_pnl_d5", "real_pnl_d10", "real_pnl_d15"]:
        s = k[c].drop_nulls()
        if s.len() > 100:
            p = q(s)
            print(f"  {c:<15} N={s.len():>5}  p05={p[0]:+.3f} p25={p[1]:+.3f} "
                  f"MED={p[2]:+.3f} p75={p[3]:+.3f} p95={p[4]:+.3f}  mean={float(s.mean()):+.3f}")
    mx = k["real_max_mult"].drop_nulls()
    if mx.len() > 100:
        p = q(mx)
        print(f"  {'real_max_mult':<15} N={mx.len():>5}  p05={p[0]:.3f} p25={p[1]:.3f} "
              f"MED={p[2]:.3f} p75={p[3]:.3f} p95={p[4]:.3f}")

    # ---------------- affordability (feeds the bankroll ladder) -------------
    print("\n" + "-" * 92)
    print("AFFORDABILITY -- real contract cost in dollars (1 contract = 100 shares)")
    print("-" * 92)
    cost = (kept["entry_premium_real"] * 100).drop_nulls()
    p = q(cost)
    print(f"  N={cost.len()}  p05=${p[0]:,.0f} p25=${p[1]:,.0f} MED=${p[2]:,.0f} "
          f"p75=${p[3]:,.0f} p95=${p[4]:,.0f}")
    for budget in [250, 500, 1000, 2000]:
        print(f"    affordable at a ${budget:>5} slot: {float((cost <= budget).mean()):.3f}")


if __name__ == "__main__":
    sys.exit(main())
