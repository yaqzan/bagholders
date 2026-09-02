"""
Phase 2-4: Parameterized confidence function + Bayesian optimization.

Confidence shape (4 parameters):
    stat_conf  = vol_completion ** stat_exp       # ∈ [0, 1]
    closing    = σ((30 − mins_to_close) / closing_scale) × closing_weight
    stability  = signal_stability × stability_weight
    confidence = clip(stat_conf + closing + stability, 0, 1)

Where signal_stability = fraction of last K=3 snapshots with same signal_type
as current (1.0 = all match, 0.0 = none match).

Effective multiplier with confidence weighting:
    eff_mult(snap) = 1 + confidence(snap) × (mult_intraday(snap) − 1)

Loss: mean squared error between eff_mult(snap) and mult_close (the 16:00
snapshot's multiplier — ground truth from 1-min-derived close-of-day signal).

Optimizer: gp_minimize from scikit-optimize, 80 evaluations.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from skopt import gp_minimize
from skopt.space import Real

TRAJ = Path('experiments/intraday_confidence/trajectories.parquet')
PROFILE = Path('experiments/intraday_confidence/volume_profile.json')
OUT = Path('experiments/intraday_confidence/optimization_result.json')


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def signal_stability(traj_for_day: pd.DataFrame, snap: int, K: int = 3) -> float:
    """Fraction of last K snapshots (incl current) with same signal_type."""
    history = traj_for_day[traj_for_day['snap_min'] <= snap].sort_values('snap_min')
    if len(history) == 0:
        return 0.0
    cur = history.iloc[-1]['signal_type']
    last_k = history.tail(K)
    return float((last_k['signal_type'] == cur).sum()) / len(last_k)


def confidence(snap_min, vol_completion, stab, params):
    stat_exp, closing_scale, closing_weight, stability_weight = params
    stat = vol_completion ** stat_exp
    mins_to_close = max(0, 390 - snap_min)
    closing = sigmoid((30 - mins_to_close) / closing_scale) * closing_weight
    stab_term = stab * stability_weight
    return float(np.clip(stat + closing + stab_term, 0, 1))


def build_pairs():
    """Each row: (snap features, ground-truth close-of-day multiplier).

    Includes snap=390 (close) for completeness but optimizer should skip it
    since confidence at close is irrelevant (always trust the close).
    """
    df = pd.read_parquet(TRAJ)
    profile = json.loads(PROFILE.read_text())['universal']
    profile = {int(k): v for k, v in profile.items()}

    # ground truth = the snap=390 multiplier per (sym, day)
    close_map = (df[df['snap_min'] == 390]
                 .set_index(['symbol', 'date'])['multiplier'].to_dict())

    pairs = []
    for (sym, day), grp in df.groupby(['symbol', 'date']):
        mult_close = close_map.get((sym, day))
        if mult_close is None:
            continue
        for _, row in grp.iterrows():
            if row['snap_min'] >= 390:
                continue  # skip close itself
            stab = signal_stability(grp, row['snap_min'])
            vol_complete = profile.get(row['snap_min'], 0.5)
            pairs.append({
                'symbol': sym,
                'date': str(day.date() if hasattr(day, 'date') else day),
                'snap_min': int(row['snap_min']),
                'mult_intraday': float(row['multiplier']),
                'signal_intraday': row['signal_type'],
                'signal_close': close_map.get((sym, day)) is not None and df[
                    (df['symbol']==sym) & (df['date']==day) & (df['snap_min']==390)
                ].iloc[0]['signal_type'],
                'mult_close': float(mult_close),
                'vol_completion': float(vol_complete),
                'stability': float(stab),
            })
    return pd.DataFrame(pairs)


def loss(params, pairs_df):
    """MSE between confidence-weighted eff_mult and close-of-day mult."""
    confs = np.array([
        confidence(r.snap_min, r.vol_completion, r.stability, params)
        for r in pairs_df.itertuples()
    ])
    mult_intra = pairs_df['mult_intraday'].values
    mult_close = pairs_df['mult_close'].values
    eff_mult = 1.0 + confs * (mult_intra - 1.0)
    err = eff_mult - mult_close
    return float(np.mean(err ** 2))


def baseline_losses(pairs_df):
    """Reference points for context."""
    # Baseline 1: never trust intraday — eff_mult = 1.0 always
    eff_neutral = np.ones(len(pairs_df))
    mult_close = pairs_df['mult_close'].values
    l_neutral = float(np.mean((eff_neutral - mult_close) ** 2))

    # Baseline 2: always fully trust intraday (current behavior past 10:30)
    eff_full = pairs_df['mult_intraday'].values
    l_full = float(np.mean((eff_full - mult_close) ** 2))

    # Baseline 3: current _intraday_confidence (linear ramp 0→1 over first 60 min)
    # Beyond first 60 min, confidence=1.0
    confs_current = np.minimum(1.0, np.maximum(0.0, pairs_df['snap_min'].values / 60))
    eff_current = 1.0 + confs_current * (pairs_df['mult_intraday'].values - 1.0)
    l_current = float(np.mean((eff_current - mult_close) ** 2))

    return {
        'neutral_always_1.0': l_neutral,
        'full_trust_intraday': l_full,
        'current_intraday_conf': l_current,
    }


def main():
    pairs = build_pairs()
    print(f'Optimization pairs: {len(pairs)} (across {pairs.groupby(["symbol","date"]).ngroups} stock-days × 12 snapshots)')
    print(f'Snap min range: {pairs["snap_min"].min()}–{pairs["snap_min"].max()}')

    # Baselines
    base = baseline_losses(pairs)
    print('\nBaselines (MSE):')
    for k, v in base.items():
        print(f'  {k:<26} {v:.5f}')

    # Bayesian opt
    space = [
        Real(0.1, 4.0, name='stat_exp'),         # 0.5 = sqrt, 1.0 = linear, 2.0 = quadratic
        Real(5.0, 60.0, name='closing_scale'),   # minutes for sigmoid steepness
        Real(0.0, 0.5, name='closing_weight'),   # boost magnitude near close
        Real(0.0, 0.4, name='stability_weight'), # bonus for sig persistence
    ]

    def objective(params):
        return loss(params, pairs)

    print('\nRunning Bayesian optimization (80 evaluations)...')
    result = gp_minimize(objective, space, n_calls=80, random_state=42,
                         acq_func='EI', verbose=False)

    best_params = dict(zip(['stat_exp', 'closing_scale', 'closing_weight', 'stability_weight'],
                            result.x))
    best_loss = result.fun

    print('\nBest parameters:')
    for k, v in best_params.items():
        print(f'  {k:<20} {v:.4f}')
    print(f'\nBest MSE: {best_loss:.5f}')

    # Improvement vs baselines
    print('\nImprovement vs baselines:')
    for k, v in base.items():
        impr = (v - best_loss) / v * 100
        print(f'  {k:<26} loss={v:.5f}  improvement={impr:+.1f}%')

    # Inspect confidence at canonical snap times
    print('\nLearned confidence shape (vol_completion from universal profile):')
    profile = json.loads(PROFILE.read_text())['universal']
    profile = {int(k): v for k, v in profile.items()}
    print(f'{"snap":<6} {"clock":>6} {"vol_done":>9} {"conf(stab=0)":>13} {"conf(stab=1)":>13}')
    for snap in [30, 60, 90, 120, 180, 240, 300, 330, 360, 389]:
        clock = f'{9 + (30 + snap)//60}:{(30 + snap) % 60:02d}'
        vc = profile.get(snap, 0.5)
        params_tuple = tuple(result.x)
        c0 = confidence(snap, vc, 0.0, params_tuple)
        c1 = confidence(snap, vc, 1.0, params_tuple)
        print(f'{snap:<6} {clock:>6}    {vc:.3f}      {c0:.3f}        {c1:.3f}')

    # Save full result
    out_data = {
        'n_pairs': len(pairs),
        'best_params': best_params,
        'best_loss': best_loss,
        'baselines': base,
        'improvements_vs_baselines': {
            k: (v - best_loss) / v * 100 for k, v in base.items()
        },
        'all_evaluations': [
            {'params': list(x), 'loss': float(y)}
            for x, y in zip(result.x_iters, result.func_vals)
        ],
    }
    OUT.write_text(json.dumps(out_data, indent=2))
    print(f'\n[OK] saved {OUT}')

    # Save predictions for visualization phase
    pairs['confidence_learned'] = pairs.apply(
        lambda r: confidence(r['snap_min'], r['vol_completion'], r['stability'],
                             tuple(result.x)), axis=1)
    pairs['eff_mult_learned'] = 1.0 + pairs['confidence_learned'] * (pairs['mult_intraday'] - 1.0)
    pairs.to_parquet('experiments/intraday_confidence/optimization_pairs.parquet', index=False)
    print('[OK] saved optimization_pairs.parquet')


if __name__ == '__main__':
    main()
