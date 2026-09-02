"""
Targeted Monday-only dampener test.

Hypothesis: Monday's baseline |Δoverall| is ~2× Tue-Fri (6.23 vs 3.0-3.3),
because Monday's wadj is computed from a 1-day partial weekly bar. Dampening
Mon-specific should reduce whiplash without creating new day-boundary effects.

Tests Mon coefficient ∈ {0.0, 0.3, 0.5, 0.7, 1.0} with all other days at 1.0.
Reports per-trade WR15 and stability metrics for each.
"""
from __future__ import annotations
import io
import json
import sys
from pathlib import Path

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module
runner_mod = import_module('01_runner')
WadjDowRunner = runner_mod.WadjDowRunner

OUT_DIR = Path(__file__).resolve().parent
OUT_PATH = OUT_DIR / 'monday_only.json'


def main():
    print('Loading runner...', flush=True)
    r = WadjDowRunner()
    r.load(verbose=False)
    print(f'  v{r.version_id}, {len(r.df):,} rows', flush=True)

    test_mon_values = [0.0, 0.3, 0.5, 0.7, 0.85, 1.0]
    out = {'baseline_dow_stability': {}, 'variants': []}

    # Baseline per-day stability
    base_df = r._apply_dow_dampener([1.0]*5)
    base_df = base_df.sort(['symbol', 'date'])
    base_df = base_df.with_columns(pl.col('new_overall').shift(1).over('symbol').alias('prev_overall'))
    base_df = base_df.filter(pl.col('prev_overall').is_not_null())
    base_df = base_df.with_columns((pl.col('new_overall') - pl.col('prev_overall')).abs().alias('abs_d'))

    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    print('\nBaseline per-day stability:')
    for i, dn in enumerate(days):
        v = float(base_df.filter(pl.col('dow') == i)['abs_d'].mean())
        out['baseline_dow_stability'][dn] = v
        print(f'  {dn}: |Δ|={v:.3f}')
    base_overall = float(base_df['abs_d'].mean())
    out['baseline_overall_stability'] = base_overall
    print(f'  ALL: {base_overall:.3f}')

    print(f'\nBaseline per-trade WR15 (5y):')
    base = r.evaluate([1.0]*5, lookback_days=1825)
    for b in ['95+', '90+', '85+', '80+', '75+', '70+', '<5', '<15', '<25']:
        s = base['bucketed_stats'].get(b, {})
        wr = s.get('win_rate_15d')
        n = s.get('sample_count', 0)
        if wr:
            print(f'  {b:<5}  N={n:>6}  WR15={wr:.2f}%')

    print(f'\n--- Mon-only dampener sweep ---')
    print(f'{"mon":>5} {"95+_n":>5} {"95+_wr":>6} {"80+_n":>6} {"80+_wr":>6} '
          f'{"<25_n":>6} {"<25_wr":>6} {"all_|Δ|":>8} {"Mon_|Δ|":>8} {"Fri_|Δ|":>8}')

    for mon_v in test_mon_values:
        coeffs = [mon_v, 1.0, 1.0, 1.0, 1.0]
        stats = r.evaluate(coeffs, lookback_days=1825)
        # Stability
        df = r._apply_dow_dampener(coeffs)
        df = df.sort(['symbol', 'date'])
        df = df.with_columns(pl.col('new_overall').shift(1).over('symbol').alias('prev_overall'))
        df = df.filter(pl.col('prev_overall').is_not_null())
        df = df.with_columns((pl.col('new_overall') - pl.col('prev_overall')).abs().alias('abs_d'))
        stab_all = float(df['abs_d'].mean())
        stab_mon = float(df.filter(pl.col('dow') == 0)['abs_d'].mean())
        stab_fri = float(df.filter(pl.col('dow') == 4)['abs_d'].mean())

        def s_wr(b):
            return stats['bucketed_stats'].get(b, {}).get('win_rate_15d', 0)
        def s_n(b):
            return stats['bucketed_stats'].get(b, {}).get('sample_count', 0)

        print(f'{mon_v:>5.2f} {s_n("95+"):>5} {s_wr("95+"):>5.1f}% '
              f'{s_n("80+"):>6} {s_wr("80+"):>5.2f}% '
              f'{s_n("<25"):>6} {s_wr("<25"):>5.2f}% '
              f'{stab_all:>8.3f} {stab_mon:>8.3f} {stab_fri:>8.3f}')

        out['variants'].append({
            'mon': mon_v,
            'wr15': {b: s_wr(b) for b in ['95+', '90+', '85+', '80+', '75+', '70+', '<5', '<15', '<25']},
            'n':    {b: s_n(b) for b in ['95+', '90+', '85+', '80+', '75+', '70+', '<5', '<15', '<25']},
            'stability_all': stab_all,
            'stability_mon': stab_mon,
            'stability_fri': stab_fri,
        })

    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f'\n[OK] saved {OUT_PATH}')

    # Also test Mon=0.5 + multi-window
    print(f'\n--- Multi-window for Mon=0.5 (best stability candidate) ---')
    print(f'{"window":>7} {"95+_wr":>7} {"90+_wr":>7} {"80+_wr":>7} {"75+_wr":>7} {"<15_wr":>7} {"<25_wr":>7}')
    for win, days in [('1y', 365), ('3y', 1095), ('5y', 1825)]:
        b_stats = r.evaluate([1.0]*5, lookback_days=days)
        v_stats = r.evaluate([0.5, 1.0, 1.0, 1.0, 1.0], lookback_days=days)
        deltas = []
        for b in ['95+', '90+', '80+', '75+', '<15', '<25']:
            bv = b_stats['bucketed_stats'].get(b, {}).get('win_rate_15d')
            vv = v_stats['bucketed_stats'].get(b, {}).get('win_rate_15d')
            if bv is not None and vv is not None:
                deltas.append(f'{vv-bv:+5.2f}pp')
            else:
                deltas.append(' n/a ')
        print(f'{win:>7} ' + ' '.join(d.rjust(7) for d in deltas))


if __name__ == '__main__':
    main()
