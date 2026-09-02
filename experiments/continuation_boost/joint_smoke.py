"""5-minute smoke test for joint_sweep harness.
Loads scores, applies a tiny boost, runs MC at N=20 on 22-now. Verifies pipeline."""
from __future__ import annotations
import io, sys, os, time
from datetime import date
from pathlib import Path

# Don't re-wrap sys.stdout — PYTHONIOENCODING=utf-8 is already set by env
# and re-wrapping can break tee/pipe redirects

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl
from experiments.continuation_boost.joint_sweep import (
    Variant, load_v32_scores_full, apply_boost_to_scores,
    build_sim_rows, PEAKS_PQ, PRIORS_PQ
)

print('=== smoke ===')
peaks  = pl.read_parquet(PEAKS_PQ)
priors = pl.read_parquet(PRIORS_PQ)
print(f'peaks={len(peaks):,}  priors={len(priors):,}')

scores = load_v32_scores_full()
print(f'scores={len(scores):,}')

# Apply baseline (no boost) — should be no-op
t0 = time.time()
baseline_scores = apply_boost_to_scores(scores, peaks, priors, Variant(LIFT_MAX=0.0))
print(f'baseline apply: {time.time()-t0:.2f}s  shape={baseline_scores.shape}')

# Apply real variant
v = Variant(LIFT_MAX=10.07, TAU=35.6, LBMIN=8, LBMAX=36, W15_RATIO=1.80,
            MAG_EXP=2.0, SAT_K=2.81, WIN_NEG_W=0.07, MFE_FLOOR=0.29,
            REVERSAL_K_CALL=0.5, REVERSAL_K_PUT=1.5)
t0 = time.time()
boosted = apply_boost_to_scores(scores, peaks, priors, v)
print(f'boost apply: {time.time()-t0:.2f}s  shape={boosted.shape}')

# Diff: how many scores changed?
joined = scores.select(['symbol', 'date', 'overall']).rename({'overall': 'overall_orig'}).join(
    boosted.select(['symbol', 'date', 'overall']).rename({'overall': 'overall_new'}),
    on=['symbol', 'date'], how='inner')
n_diff = joined.filter(pl.col('overall_orig') != pl.col('overall_new')).height
print(f'scores changed by boost: {n_diff:,} / {len(joined):,}')

# Build sim rows
t0 = time.time()
rows = build_sim_rows(boosted)
print(f'build_sim_rows: {time.time()-t0:.1f}s, {len(rows):,} rows')

# Tiny MC: N=20 on 22-now
print('\n=== MC smoke (N=20, 22-now) ===')
import monte_carlo as mc
from experiments.sim_mc_bridge import inject_into_mc
mc.N_ITER = 20
os.environ['MC_NO_MP'] = '1'
from database.models.core import AlgorithmVersion
av = AlgorithmVersion.get_active_scores_version()

t0 = time.time()
with inject_into_mc(rows):
    res = mc.run_window('22-now', date(2022,1,1), date(2026,4,24), av)
print(f'MC wall: {time.time()-t0:.1f}s')
r = res['seeded']
print(f"meanRet={r['mean_ret']:+.1f}%  WorstDD={r['worst_dd']:.1f}%  "
      f"P(coll)={r['p_coll']:.1f}%  C/P TP=({r['call_tp']:.1f}/{r['put_tp']:.1f})  "
      f"trades=({r['call_trades']}/{r['put_trades']})")
print('SMOKE OK')
