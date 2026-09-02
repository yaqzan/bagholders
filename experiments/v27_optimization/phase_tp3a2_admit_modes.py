"""
Phase TP3A.2 — admit-mode cross-product on the W5/B10 + W5/B15 winners

Phase 3A showed earnings boost helps 70+/75+ buckets cleanly but DILUTES the
broad <25 put bucket because boundary signals (overall=26-29) get pushed
into <25 at lower per-trade quality. This sub-phase tests four admit modes
across the two leading params:

  AA — both admit (Phase 3A baseline behavior)
  AN — calls admit, puts no-admit  (block put boundary admissions)
  NA — calls no-admit, puts admit  (test if call admissions are real alpha)
  NN — both no-admit                (pure score-stage tier-promotion)

If AN cleans the <25 dilution while preserving 70+/75+ gains, it's the ship.
If NN matches AN at the top buckets, the call admissions weren't load-bearing.

Run: python experiments/v27_optimization/phase_tp3a2_admit_modes.py
"""
from __future__ import annotations
import io, sys, os, time
from pathlib import Path

# Ensure UTF-8 stdout without conflicting with colorama
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.v27_optimization.phase_tp3a_earnings_boost import (
    build_earnings_lookup, compute_days_to_earnings,
    evaluate_earnings_boost, evaluate_baseline,
)
from experiments.fast_variant_runner import FastVariantRunner
import polars as pl


PARAM_GRID = [
    # (window, max_boost, put_ratio, label)
    (5, 0.10, 1.0, 'W5_B10'),
    (5, 0.15, 1.0, 'W5_B15'),
    (3, 0.15, 1.0, 'W3_B15'),
]

ADMIT_MODES = [
    ('AA', True,  True),    # both admit
    ('AN', True,  False),   # calls admit, puts no-admit
    ('NA', False, True),    # calls no-admit, puts admit
    ('NN', False, False),   # both no-admit
]


def main():
    t0 = time.time()
    runner = FastVariantRunner(version_id=None, lookback_days=1825)
    runner.load(verbose=True)
    print(f"[runner] loaded in {time.time()-t0:.1f}s", flush=True)

    t1 = time.time()
    symbols = runner.df['symbol'].unique().to_list()
    by_sym = build_earnings_lookup(symbols, runner.lookback_days)
    runner.df = compute_days_to_earnings(runner.df, by_sym, max_window=10)
    print(f"[earnings] step took {time.time()-t1:.1f}s", flush=True)

    print('\n[variant] baseline (no boost)', flush=True)
    baseline = evaluate_baseline(runner)

    rows = [('BASELINE', baseline)]
    for window, max_boost, put_ratio, plabel in PARAM_GRID:
        for mlabel, ca, pa in ADMIT_MODES:
            t = time.time()
            res = evaluate_earnings_boost(runner, window, max_boost, put_ratio,
                                          call_admit=ca, put_admit=pa)
            label = f"{plabel}_{mlabel}"
            print(f"[variant] {label} ({time.time()-t:.2f}s)", flush=True)
            rows.append((label, res))

    # Print full bucket-level diff table
    print('\n' + '='*120)
    print('ADMIT MODE CROSS-PRODUCT — bucket × variant grid (5y)')
    print('='*120)

    BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+', '<25', '<20', '<15', '<10', '<5']

    bs = baseline['bucketed_stats']
    def get(s, key, metric):
        d = s.get(key, {}) if isinstance(s, dict) else s['bucketed_stats'].get(key, {})
        n = d.get('sample_count', 0)
        wr15 = d.get('win_rate_15d')
        wr30 = d.get('win_rate_30d')
        return n, wr15, wr30

    # Compact summary: per variant, show the ΔWR15 across key buckets + ΔN
    print(f"\n{'Variant':<14} | {'70+ ΔN':>8} {'70+ ΔWR':>8} | {'75+ ΔN':>8} {'75+ ΔWR':>8} | "
          f"{'80+ ΔN':>8} {'80+ ΔWR':>8} | {'<25 ΔN':>8} {'<25 ΔWR':>8} | {'<15 ΔN':>8} {'<15 ΔWR':>8} | "
          f"{'<5 ΔN':>8} {'<5 ΔWR':>8}")
    print('-' * 165)

    def _delta_row(label, stats):
        out = [label]
        for k in ['70+', '75+', '80+', '<25', '<15', '<5']:
            nb, wb15, _ = get(bs, k, None)
            nv, wv15, _ = get(stats, k, None)
            if wb15 is None or wv15 is None:
                out.append(f"{nv-nb:>+8d}")
                out.append(f"{'  -  ':>8}")
            else:
                out.append(f"{nv-nb:>+8d}")
                out.append(f"{wv15-wb15:>+7.2f}")
        return out

    # baseline row first (shows raw N and WR15 for reference)
    print(f"{'BASELINE (raw)':<14} | ", end='')
    for k in ['70+', '75+', '80+', '<25', '<15', '<5']:
        n, w15, _ = get(bs, k, None)
        print(f"{n:>8d} {w15:>7.2f}% | ", end='')
    print()
    print('-' * 165)

    for label, stats in rows[1:]:
        r = _delta_row(label, stats['bucketed_stats'])
        print(f"{r[0]:<14} | {r[1]:>8} {r[2]:>8} | {r[3]:>8} {r[4]:>8} | {r[5]:>8} {r[6]:>8} | "
              f"{r[7]:>8} {r[8]:>8} | {r[9]:>8} {r[10]:>8} | {r[11]:>8} {r[12]:>8}")

    print(f"\n[total] {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
