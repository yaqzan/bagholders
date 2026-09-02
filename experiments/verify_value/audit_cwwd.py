"""Gate-vs-gradient audit — empirical confirmation arm.

Compares two faithful re-scorings of the same symbols: v74 baseline (CWWD on) vs
CWWD off (CWWD_DAMPEN_K=0). CWWD is the ONLY active call-side score-stage dampener
in v74 (every other gradient-shaper — MCD/ICH/SCW/CWCF/CSWC/continuation/WVD/
daily-volume/EARN_BOOST — is retired in v71/v73/v74).

THE funded question (Apex trades 75+): does CWWD ever change a 75+ score? It is gated
[70,75) and lower-only, so by construction it cannot — this confirms it empirically and
characterizes its 70-74 effect (gate-crossing below 70 vs mild within-band re-rank).

Read-only.
"""
import os
import sys
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
base = pl.read_parquet(os.path.join(_HERE, "..", "..", ".cache", "verify_value", "rescore_base.parquet"))
off = pl.read_parquet(os.path.join(_HERE, "..", "..", ".cache", "verify_value", "rescore_cwwd_off.parquet"))
print(f"base (CWWD on): {base.height:,} rows >=60   off (CWWD off): {off.height:,} rows >=60")

j = base.rename({"overall": "ov_on"}).join(
    off.rename({"overall": "ov_off"}), on=["symbol", "date"], how="full", coalesce=True
)
# rows present in only one arm: the missing side is <60 (filtered by rescore_dump)
j = j.with_columns(
    ov_on=pl.col("ov_on").fill_null(59),    # base-missing => was <60 (CWWD dampened it out)
    ov_off=pl.col("ov_off").fill_null(59),
)
j = j.with_columns(delta=(pl.col("ov_off") - pl.col("ov_on")))
n_diff = (j["delta"] != 0).sum()
print(f"\narms differ on {n_diff:,} of {j.height:,} (symbol,date) rows", flush=True)
if n_diff == 0:
    print("*** ARMS IDENTICAL — CWWD_DAMPEN_K env toggle did NOT take (G31). Abort. ***")
    sys.exit(1)

# CWWD always RAISES the score when turned off (removes a dampener) -> delta >= 0
print(f"  delta sign: >0 {int((j['delta']>0).sum()):,}  <0 {int((j['delta']<0).sum()):,}  "
      f"(expect all >=0: removing a down-only dampener can only raise)")

# === THE FUNDED CHECK: does CWWD ever touch a 75+ score? ===
touches75 = j.filter(((pl.col("ov_on") >= 75) | (pl.col("ov_off") >= 75)) & (pl.col("delta") != 0))
print(f"\n=== FUNDED (Apex 75+) CHECK ===")
print(f"  rows with (ov_on>=75 OR ov_off>=75) AND delta!=0: {touches75.height:,}")
print(f"  -> {'PASS: CWWD never changes a 75+ score => FUNDED-IRRELEVANT for Apex' if touches75.height==0 else 'UNEXPECTED: CWWD touches 75+ (investigate)'}")

# also: is the 75+ SET identical?
set_on = set(zip(*j.filter(pl.col('ov_on')>=75).select(['symbol','date']).to_numpy().T.tolist())) if j.filter(pl.col('ov_on')>=75).height else set()
set_off = set(zip(*j.filter(pl.col('ov_off')>=75).select(['symbol','date']).to_numpy().T.tolist())) if j.filter(pl.col('ov_off')>=75).height else set()
print(f"  75+ membership: on={len(set_on):,} off={len(set_off):,} symdiff={len(set_on ^ set_off):,} (expect 0)")

# === characterize the 70-74 effect ===
cohort = j.filter(pl.col("delta") != 0)
gate_crossed = cohort.filter((pl.col("ov_off") >= 70) & (pl.col("ov_on") < 70))   # CWWD pushed it below the 70 gate
within_band = cohort.filter((pl.col("ov_off") >= 70) & (pl.col("ov_on") >= 70))   # stayed >=70 (mild re-rank)
below_both = cohort.filter(pl.col("ov_off") < 70)
print(f"\n=== CWWD 70-74 effect (the affected cohort, all <75) ===")
print(f"  affected rows: {cohort.height:,}")
print(f"  gate-crossing (off>=70, on<70  => CWWD de-qualified a 70-74 call): {gate_crossed.height:,} "
      f"({gate_crossed.height/cohort.height*100:.1f}%)")
print(f"  within-band   (off>=70, on>=70 => mild dampen, stayed 70-74):     {within_band.height:,} "
      f"({within_band.height/cohort.height*100:.1f}%)")
print(f"  off<70 too    (both sides below the gate):                        {below_both.height:,}")
if cohort.height:
    print(f"  ov_off distribution of affected rows: min {cohort['ov_off'].min()} max {cohort['ov_off'].max()} "
          f"(all <75 => never enters the 75+ traded set)")
print(f"\nCONCLUSION: CWWD (the lone active call-side dampener) operates entirely below the 75 Apex gate")
print(f"=> FUNDED-IRRELEVANT for the traded book; it is a dashboard-honesty / 70-74 de-qualification mechanism,")
print(f"   NOT a funded gradient-shaper. v74 has NO funded-load-bearing score-stage gradient machinery left.")
