"""
Per-day-of-week stability breakdown — quantifies how much score volatility
the optimizer is removing on each weekday vs each other.

Loads the optimization result, then for the best variant computes:
- mean |Δoverall| day-over-day, broken out by:
  * day-of-week of the LATER day (Mon-Fri)
  * pre_boost-near-70 cohort (signals at the earnings-boost cliff)
  * recent (last 90 days) vs full window

Produces stability_breakdown.json + a small summary table.
"""
from __future__ import annotations
import io
import json
import sys
from pathlib import Path

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module
runner_mod = import_module('01_runner')
WadjDowRunner = runner_mod.WadjDowRunner

OUT_DIR = Path(__file__).resolve().parent
OPT_PATH = OUT_DIR / 'opt_result.json'
STAB_PATH = OUT_DIR / 'stability_breakdown.json'


def stability_for(runner, coeffs):
    """Returns df with columns: symbol, date, dow, new_overall, prev_overall, abs_delta."""
    df = runner._apply_dow_dampener(coeffs)
    df = df.sort(['symbol', 'date'])
    df = df.with_columns(
        pl.col('new_overall').shift(1).over('symbol').alias('prev_overall')
    )
    df = df.filter(pl.col('prev_overall').is_not_null())
    df = df.with_columns(
        (pl.col('new_overall') - pl.col('prev_overall')).abs().alias('abs_delta')
    )
    return df


def main():
    print('Loading runner...', flush=True)
    r = WadjDowRunner()
    r.load(verbose=False)
    print(f'  v{r.version_id}, {len(r.df):,} rows', flush=True)

    if OPT_PATH.exists():
        opt = json.loads(OPT_PATH.read_text())
        b = opt['best_coeffs']
        best = [b['mon'], b['tue'], b['wed'], b['thu'], b['fri']]
    else:
        print('  no opt_result yet — using neutral baseline', flush=True)
        best = [1.0]*5

    print(f'\nBest variant: Mon={best[0]:.3f} Tue={best[1]:.3f} Wed={best[2]:.3f} Thu={best[3]:.3f} Fri={best[4]:.3f}', flush=True)

    base_df = stability_for(r, [1.0]*5)
    best_df = stability_for(r, best)

    print(f'\n{"day":<6} {"baseline_|Δ|":>13} {"best_|Δ|":>10} {"reduction":>10}')
    out = {'overall': {}, 'by_dow': {}, 'by_pre_boost_band': {}}
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']

    base_total = float(base_df['abs_delta'].mean())
    best_total = float(best_df['abs_delta'].mean())
    out['overall'] = {'baseline': base_total, 'best': best_total,
                      'reduction_pct': (base_total - best_total) / base_total * 100}

    print(f'{"all":<6} {base_total:>13.3f} {best_total:>10.3f} {out["overall"]["reduction_pct"]:>9.1f}%')

    # By day-of-week of the later day in the pair
    for dow_idx, dow_name in enumerate(days):
        b_d = base_df.filter(pl.col('dow') == dow_idx)
        n_d = best_df.filter(pl.col('dow') == dow_idx)
        if len(b_d) == 0:
            continue
        bv = float(b_d['abs_delta'].mean())
        nv = float(n_d['abs_delta'].mean())
        red = (bv - nv) / bv * 100 if bv > 0 else 0
        out['by_dow'][dow_name] = {'baseline': bv, 'best': nv, 'reduction_pct': red, 'n': len(b_d)}
        print(f'{dow_name:<6} {bv:>13.3f} {nv:>10.3f} {red:>9.1f}%   N={len(b_d):,}')

    # Earnings-boost-cliff cohort: pre_regime in [65, 75]
    print('\nPre-regime-cliff cohort (pre_regime ∈ [65, 75]):')
    base_cliff = base_df.filter((pl.col('pre_regime') >= 65) & (pl.col('pre_regime') <= 75))
    best_cliff = best_df.filter((pl.col('pre_regime') >= 65) & (pl.col('pre_regime') <= 75))
    if len(base_cliff) > 0:
        bv = float(base_cliff['abs_delta'].mean())
        nv = float(best_cliff['abs_delta'].mean())
        red = (bv - nv) / bv * 100 if bv > 0 else 0
        out['by_pre_boost_band']['near_70'] = {'baseline': bv, 'best': nv,
                                                'reduction_pct': red, 'n': len(base_cliff)}
        print(f'  baseline |Δ|={bv:.3f}  best |Δ|={nv:.3f}  reduction={red:.1f}%  N={len(base_cliff):,}')

    # Earnings-cliff put cohort: pre_regime in [25, 32]
    print('\nPut-cliff cohort (pre_regime ∈ [25, 32]):')
    base_pcliff = base_df.filter((pl.col('pre_regime') >= 25) & (pl.col('pre_regime') <= 32))
    best_pcliff = best_df.filter((pl.col('pre_regime') >= 25) & (pl.col('pre_regime') <= 32))
    if len(base_pcliff) > 0:
        bv = float(base_pcliff['abs_delta'].mean())
        nv = float(best_pcliff['abs_delta'].mean())
        red = (bv - nv) / bv * 100 if bv > 0 else 0
        out['by_pre_boost_band']['near_30'] = {'baseline': bv, 'best': nv,
                                                'reduction_pct': red, 'n': len(base_pcliff)}
        print(f'  baseline |Δ|={bv:.3f}  best |Δ|={nv:.3f}  reduction={red:.1f}%  N={len(base_pcliff):,}')

    # Distribution of large swings (|Δ| > 10) — proxy for whiplash incidents
    print('\nLarge-swing incidents (|Δoverall| > 10):')
    n_base_big = int((base_df['abs_delta'] > 10).sum())
    n_best_big = int((best_df['abs_delta'] > 10).sum())
    out['large_swings_gt_10'] = {'baseline': n_base_big, 'best': n_best_big,
                                  'reduction_pct': (n_base_big - n_best_big) / n_base_big * 100 if n_base_big else 0}
    print(f'  baseline: {n_base_big:,}    best: {n_best_big:,}    '
          f'reduction: {out["large_swings_gt_10"]["reduction_pct"]:.1f}%')

    n_base_huge = int((base_df['abs_delta'] > 20).sum())
    n_best_huge = int((best_df['abs_delta'] > 20).sum())
    out['large_swings_gt_20'] = {'baseline': n_base_huge, 'best': n_best_huge,
                                  'reduction_pct': (n_base_huge - n_best_huge) / n_base_huge * 100 if n_base_huge else 0}
    print(f'  baseline: {n_base_huge:,}    best: {n_best_huge:,}   (>20pt swings)   '
          f'reduction: {out["large_swings_gt_20"]["reduction_pct"]:.1f}%')

    STAB_PATH.write_text(json.dumps(out, indent=2))
    print(f'\n[OK] saved {STAB_PATH}', flush=True)


if __name__ == '__main__':
    main()
