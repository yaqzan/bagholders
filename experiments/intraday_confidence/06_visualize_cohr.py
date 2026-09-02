"""
Phase 5: COHR-specific evaluation + visualization.

Output:
- cohr_trajectory.png: 7 days of COHR's signal evolution + learned confidence
- interesting_only_loss.txt: re-run loss analysis on non-NEUTRAL close-of-day
  pairs only (the ones that matter for trading decisions)
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

TRAJ = Path('experiments/intraday_confidence/trajectories.parquet')
OPT_PAIRS = Path('experiments/intraday_confidence/optimization_pairs.parquet')
RESULT = Path('experiments/intraday_confidence/optimization_result.json')
OUT_DIR = Path('experiments/intraday_confidence')


def main():
    df = pd.read_parquet(TRAJ)
    pairs = pd.read_parquet(OPT_PAIRS)
    result = json.loads(RESULT.read_text())
    best_params = result['best_params']

    # ── COHR trajectory plot ──
    cohr = df[df['symbol'] == 'COHR'].copy()
    cohr['date_str'] = pd.to_datetime(cohr['date']).dt.strftime('%m-%d')
    cohr_pairs = pairs[pairs['symbol'] == 'COHR'].copy()
    cohr_pairs['date_str'] = pd.to_datetime(cohr_pairs['date']).dt.strftime('%m-%d')

    days = sorted(cohr['date_str'].unique())
    n_days = len(days)

    fig, axes = plt.subplots(n_days, 1, figsize=(11, 2.5 * n_days), sharex=True)
    if n_days == 1:
        axes = [axes]

    for ax, day in zip(axes, days):
        d = cohr[cohr['date_str'] == day].sort_values('snap_min')
        dp = cohr_pairs[cohr_pairs['date_str'] == day].sort_values('snap_min')
        # add the 16:00 close for completeness on the multiplier line
        x = d['snap_min'].values
        mult = d['multiplier'].values
        sig_close = d[d['snap_min'] == 390]['signal_type'].iloc[0] if len(d[d['snap_min'] == 390]) > 0 else 'N/A'
        mult_close = d[d['snap_min'] == 390]['multiplier'].iloc[0] if len(d[d['snap_min'] == 390]) > 0 else 1.0

        ax.axhline(1.0, color='gray', alpha=0.3, linewidth=0.7)
        ax.axhline(mult_close, color='red', alpha=0.4, linestyle='--',
                   label=f'close-of-day mult={mult_close:.3f} ({sig_close})')

        ax.plot(x, mult, marker='o', markersize=4, color='black', label='intraday raw multiplier')

        # confidence-weighted effective multiplier
        if len(dp) > 0:
            x2 = dp['snap_min'].values
            eff = dp['eff_mult_learned'].values
            ax.plot(x2, eff, marker='s', markersize=4, color='blue', alpha=0.8,
                    label='effective mult (learned conf)')
            # overlay confidence on twin axis
            ax2 = ax.twinx()
            ax2.plot(x2, dp['confidence_learned'].values, color='green',
                     alpha=0.5, linestyle=':', linewidth=1.5)
            ax2.set_ylim(0, 1.05)
            ax2.set_ylabel('confidence', color='green', fontsize=8)
            ax2.tick_params(axis='y', labelcolor='green', labelsize=7)

        # signal type annotations
        for _, row in d.iterrows():
            if row['signal_type'] != 'NEUTRAL':
                ax.text(row['snap_min'], row['multiplier'],
                        row['signal_type'][:3],
                        fontsize=7, ha='center', va='bottom', alpha=0.7)

        ax.set_title(f'COHR {day} — close-of-day signal: {sig_close}',
                     fontsize=10, loc='left')
        ax.set_ylabel('multiplier')
        ax.set_ylim(0.4, 1.7)
        ax.legend(loc='upper left', fontsize=7)
        ax.grid(True, alpha=0.2)

    axes[-1].set_xlabel('minutes since 9:30 ET (390 = close)')
    axes[-1].set_xticks([30, 90, 150, 210, 270, 330, 390])
    axes[-1].set_xticklabels(['10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00'])

    plt.suptitle('COHR — intraday volume signal evolution\n'
                 'black=raw classification, blue=learned-confidence-weighted, '
                 'green=confidence, red=close-of-day truth', fontsize=11)
    plt.tight_layout()
    plt.savefig(OUT_DIR / 'cohr_trajectory.png', dpi=110, bbox_inches='tight')
    plt.close()
    print(f'[OK] saved cohr_trajectory.png')

    # ── Sub-analysis: interesting-only loss ──
    # Only stock-days where close-of-day signal != NEUTRAL (the ones that matter)
    # Date roundtrip via parquet may not preserve types — match by (sym, date_str)
    df_close = df[df['snap_min'] == 390].copy()
    df_close['date_str'] = pd.to_datetime(df_close['date']).dt.strftime('%Y-%m-%d')
    close_signal_by_day = dict(zip(
        zip(df_close['symbol'], df_close['date_str']),
        df_close['signal_type']))

    pairs['date_str'] = pd.to_datetime(pairs['date']).dt.strftime('%Y-%m-%d')
    pairs['close_signal'] = pairs.apply(
        lambda r: close_signal_by_day.get((r['symbol'], r['date_str']), 'UNKNOWN'), axis=1)
    interesting = pairs[pairs['close_signal'] != 'NEUTRAL'].copy()

    # Reload pairs with date as datetime — the parquet roundtrip may have made strings
    print(f'\nFull pairs: {len(pairs)}')
    print(f'Interesting pairs (close ≠ NEUTRAL): {len(interesting)} '
          f'across {interesting.groupby(["symbol","date"]).ngroups} stock-days')

    # Recompute losses on interesting-only set
    def mse(eff, truth):
        return float(np.mean((np.asarray(eff) - np.asarray(truth)) ** 2))

    if len(interesting) > 0:
        l_neutral = mse(np.ones(len(interesting)), interesting['mult_close'].values)
        l_full = mse(interesting['mult_intraday'].values, interesting['mult_close'].values)
        l_learned = mse(interesting['eff_mult_learned'].values, interesting['mult_close'].values)

        sub_summary = (
            '\n=== Interesting-only sub-analysis (close != NEUTRAL) ===\n'
            f'Pairs: {len(interesting)} ({len(interesting)/len(pairs)*100:.1f}% of full set)\n\n'
            'MSE on this subset:\n'
            f'  always neutral (1.0)         {l_neutral:.5f}\n'
            f'  full-trust intraday          {l_full:.5f}\n'
            f'  learned confidence           {l_learned:.5f}\n\n'
            'Improvement on interesting subset:\n'
            f'  vs always-neutral            {(l_neutral - l_learned) / l_neutral * 100:+.1f}%\n'
            f'  vs full-trust intraday       {(l_full - l_learned) / l_full * 100:+.1f}%\n'
        )
        print(sub_summary)
        (OUT_DIR / 'interesting_only_loss.txt').write_text(sub_summary, encoding='utf-8')

        # Per-close-signal-type breakdown
        print('\nBy close-of-day signal type:')
        for sig, grp in interesting.groupby('close_signal'):
            l_n = mse(np.ones(len(grp)), grp['mult_close'].values)
            l_f = mse(grp['mult_intraday'].values, grp['mult_close'].values)
            l_l = mse(grp['eff_mult_learned'].values, grp['mult_close'].values)
            print(f'  {sig:<10} N={len(grp):3}  '
                  f'neutral={l_n:.4f}  full_trust={l_f:.4f}  learned={l_l:.4f}')

    # ── Confusion matrix: how often does intraday signal type match close? ──
    print('\nConfusion matrix (intraday signal vs close-of-day signal, all pairs):')
    conf_mat = pd.crosstab(pairs['signal_intraday'], pairs['close_signal'],
                            margins=True, normalize='columns')
    print(conf_mat.round(2).to_string())

    print('\nFraction of intraday snapshots where signal_type == close-of-day signal_type:')
    pairs['matches_close'] = pairs['signal_intraday'] == pairs['close_signal']
    by_snap = pairs.groupby('snap_min')['matches_close'].mean().round(3)
    print(by_snap.to_string())


if __name__ == '__main__':
    main()
