"""Continuation-boost variant evaluator + Bayesian sweep.

Loads pre-built peak/prior parquets (build_features.py output), then evaluates
score-stage boost variants against per-bucket TP% (30dte_opt w=15d) and WR15
(30dte_generic w=15d) at 5y v32.

Per-variant: ~0.5-1.0s via Polars vectorization.

Sweep: Bayesian optimization over 8 hyperparameters. Utility maximizes weighted
TP% improvement on call tiers {95+, 90+, 85+, 80+, 75+}, penalizes N drift,
penalizes any tier regression > 1.0pp.

Usage:
    python -m experiments.continuation_boost.sweep [n_iter=120]
"""
from __future__ import annotations
import io, sys, time, json, argparse
from datetime import date
from pathlib import Path
from dataclasses import dataclass, asdict

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / '.cache' / 'continuation_boost'
PEAKS_PQ  = CACHE_DIR / 'peaks.parquet'
PRIORS_PQ = CACHE_DIR / 'priors_long.parquet'

OUT_DIR = Path(__file__).parent
RESULTS_JSON = OUT_DIR / 'sweep_results.json'

# Cumulative bucket thresholds — must match fast_variant_runner / assess_scores
CALL_BUCKETS = [95, 90, 85, 80, 75, 70]   # >= threshold
PUT_BUCKETS  = [5, 10, 15, 20, 25, 30]    # <= threshold

# Primary call tiers for the H1 gate.
PRIMARY_CALL_TIERS = ['95+', '90+', '85+', '80+', '75+']
N_FLOOR = 50  # bucket must have >= 50 peaks; otherwise excluded from gate calcs


# ---------------------------------------------------------------------------
# Variant definition
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    LIFT_MAX:    float = 4.0   # cap on lift per signal (pp)
    TAU:         float = 25.0  # recency exponential decay constant (days)
    LBMIN:       int   = 7     # lookback inner bound (gap_days >=)
    LBMAX:       int   = 60    # lookback outer bound (gap_days <=)
    W15_RATIO:   float = 1.0   # weight on win_15d relative to win_7d (=0.7 ref)
    MAG_EXP:     float = 1.0   # exponent on prior magnitude (conviction/50)
    SAT_K:       float = 4.0   # tanh saturation point
    WIN_NEG_W:   float = 0.0   # negative weight on prior losses (0 = ignore losses)
    MFE_FLOOR:        float = 0.0   # min mfe_room when MFE fully consumed (still in expected dir)
    REVERSAL_K_CALL:  float = 0.5   # penalty for calls on reversal (low: retracement = buy-dip OK)
    REVERSAL_K_PUT:   float = 1.5   # penalty for puts on reversal (high: bounce = thesis broken)


# ---------------------------------------------------------------------------
# Variant evaluation
# ---------------------------------------------------------------------------

def evaluate(peaks: pl.DataFrame, priors: pl.DataFrame, v: Variant) -> dict:
    """Apply boost formula, re-bucket, compute per-bucket TP%/WR/N.

    Returns dict {bucket: {tp_pct, n, n_resolved, wr15_resolved}}
    """
    # 1. Filter priors to LBMIN..LBMAX
    p = priors.filter(
        (pl.col('gap_days') >= v.LBMIN) &
        (pl.col('gap_days') <= v.LBMAX)
    )

    # 2. win_quality: gradient on (win_W, gap, neg_weight). win_15d > win_7d.
    #    Per-prior, take the BEST signal it carries — prefer win_15d if knowable, else win_7d.
    #    Loss carries negative weight (scaled by WIN_NEG_W).
    W7_WT  = 0.7
    W15_WT = v.W15_RATIO  # typically 1.0
    p = p.with_columns([
        # decay
        (-pl.col('gap_days').cast(pl.Float64) / v.TAU).exp().alias('decay'),
        # magnitude (gradient on prior conviction)
        ((pl.col('prior_conviction').cast(pl.Float64) / 50.0).pow(v.MAG_EXP)).alias('magnitude'),
        # win_quality: prefer win_15d signal if knowable, else win_7d
        pl.when((pl.col('gap_days') >= 15) & (pl.col('win_15d_30dgen') == 1))
          .then(W15_WT)
          .when((pl.col('gap_days') >= 15) & (pl.col('win_15d_30dgen') == 0))
          .then(-v.WIN_NEG_W * W15_WT)
          .when((pl.col('gap_days') >= 7)  & (pl.col('win_7d_30dgen')  == 1))
          .then(W7_WT)
          .when((pl.col('gap_days') >= 7)  & (pl.col('win_7d_30dgen')  == 0))
          .then(-v.WIN_NEG_W * W7_WT)
          .otherwise(0.0)
          .alias('win_quality'),
    ])
    p = p.with_columns(
        (pl.col('decay') * pl.col('magnitude') * pl.col('win_quality'))
        .alias('contribution')
    )

    # 3. Aggregate to per-peak.
    #    prior_signal = sum of contributions
    #    oldest_prior_close = first by descending gap_days (= largest gap)
    #    oldest_prior_mfe   = same
    peak_signals = p.group_by(['symbol', 'date']).agg([
        pl.col('contribution').sum().alias('prior_signal'),
        pl.col('prior_close').sort_by(pl.col('gap_days'), descending=True).first()
          .alias('oldest_prior_close'),
        pl.col('prior_mfe_anchor').sort_by(pl.col('gap_days'), descending=True).first()
          .alias('oldest_prior_mfe'),
    ])

    # 4. Join back to peaks; compute lift.
    pk = peaks.join(peak_signals, on=['symbol', 'date'], how='left').with_columns([
        pl.col('prior_signal').fill_null(0.0),
    ])

    # Realized move from oldest prior to today (side-adjusted, raw %)
    pk = pk.with_columns([
        pl.when(pl.col('oldest_prior_close').is_not_null() & (pl.col('oldest_prior_close') > 0))
          .then(
              pl.when(pl.col('side') == 'call')
                .then((pl.col('current_close') - pl.col('oldest_prior_close'))
                      / pl.col('oldest_prior_close') * 100)
                .otherwise((pl.col('oldest_prior_close') - pl.col('current_close'))
                           / pl.col('oldest_prior_close') * 100)
          )
          .otherwise(None)
          .alias('realized_pct'),
    ])

    # realized_ratio: SIGNED progress in expected direction
    #   +1   = stock has moved exactly the bucket's expected MFE (consumed full runway)
    #    0   = stock at the prior peak's price (no progress yet)
    #   -1   = stock reversed by full MFE (thesis broken)
    # Clip extreme values for numerical stability but keep sign.
    pk = pk.with_columns([
        pl.when(pl.col('oldest_prior_mfe').is_not_null() & (pl.col('oldest_prior_mfe') > 0)
                & pl.col('realized_pct').is_not_null())
          .then((pl.col('realized_pct') / pl.col('oldest_prior_mfe')).clip(-2.0, 2.0))
          .otherwise(0.0)
          .alias('realized_ratio'),
    ])

    # mfe_room: piecewise gradient, side-asymmetric on reversal
    #   ratio >= 0:  same for both sides — 1 - (1-MFE_FLOOR) * min(ratio, 1)
    #   ratio <  0:  REVERSAL_K_PUT applies for puts (counter-trend bounce, kill boost),
    #                REVERSAL_K_CALL applies for calls (often a buy-the-dip; smaller penalty OK)
    pk = pk.with_columns([
        pl.when(pl.col('side') == 'put').then(v.REVERSAL_K_PUT).otherwise(v.REVERSAL_K_CALL)
          .alias('reversal_k'),
    ])
    pk = pk.with_columns([
        pl.when(pl.col('realized_ratio') >= 0)
          .then(1.0 - (1.0 - v.MFE_FLOOR) * pl.col('realized_ratio').clip(0.0, 1.0))
          .otherwise(1.0 - pl.col('reversal_k') * (-pl.col('realized_ratio')).clip(0.0, 1.0))
          .clip(0.0, 1.0)
          .alias('mfe_room'),
    ])

    # tanh saturation on signed prior_signal (handles negative-weight losses too)
    pk = pk.with_columns([
        (pl.col('prior_signal') / v.SAT_K).tanh().alias('sat_signal'),
    ])

    # No conv_scale — lift is uniform across base scores so it can actually
    # promote weak peaks into higher tiers. The gradient lives in prior_signal
    # (sat_signal) and mfe_room (dampener); current score doesn't gate the lift.
    pk = pk.with_columns([
        pl.when(pl.col('side') == 'call').then(1.0).otherwise(-1.0)
          .alias('side_sign'),
    ])

    # Lift magnitude (always positive sign-wise; side_sign applied at apply-step)
    pk = pk.with_columns([
        (v.LIFT_MAX
         * pl.col('sat_signal')
         * pl.col('mfe_room')).alias('lift_mag'),
    ])

    # Apply boost: new_overall = overall + side_sign * lift_mag, clipped [0, 100]
    pk = pk.with_columns([
        (pl.col('overall').cast(pl.Float64) + pl.col('side_sign') * pl.col('lift_mag'))
        .round().clip(0, 100).cast(pl.Int64)
        .alias('new_overall'),
    ])

    # 5. Re-assign cumulative buckets and aggregate per-bucket TP%/WR/N.
    out = {}
    for t in CALL_BUCKETS:
        sub = pk.filter(pl.col('new_overall') >= t)
        n = sub.height
        n_res = sub.filter(pl.col('outcome_tp_15d').is_not_null()).height
        wins  = sub.filter(pl.col('outcome_tp_15d') == 1).height
        out[f'{t}+'] = {
            'n':       n,
            'n_resolved': n_res,
            'tp_pct':  (100 * wins / n_res) if n_res else None,
            'wins':    wins,
        }
    for t in PUT_BUCKETS:
        sub = pk.filter(pl.col('new_overall') <= t)
        n = sub.height
        n_res = sub.filter(pl.col('outcome_tp_15d').is_not_null()).height
        wins  = sub.filter(pl.col('outcome_tp_15d') == 1).height
        out[f'<{t}'] = {
            'n':       n,
            'n_resolved': n_res,
            'tp_pct':  (100 * wins / n_res) if n_res else None,
            'wins':    wins,
        }
    return out


# ---------------------------------------------------------------------------
# Utility function
# ---------------------------------------------------------------------------

def utility(stats_v: dict, stats_b: dict) -> dict:
    """Compute utility of variant `stats_v` vs baseline `stats_b`.

    Returns:
        utility (float, higher = better)
        deltas dict per bucket
        n_drift max abs % change in primary tier N
        regression_pp max single-tier TP% regression in primary tiers
    """
    deltas = {}
    n_changes = []
    primary_regressions = []
    primary_improvements = []
    for b in PRIMARY_CALL_TIERS + ['<25', '<15']:
        v = stats_v.get(b, {})
        bl = stats_b.get(b, {})
        if v.get('tp_pct') is None or bl.get('tp_pct') is None: continue
        d_tp = v['tp_pct'] - bl['tp_pct']
        d_n  = v['n'] - bl['n']
        n_pct = (100 * d_n / bl['n']) if bl['n'] else 0
        deltas[b] = {'d_tp_pct': d_tp, 'd_n_abs': d_n, 'd_n_pct': n_pct,
                     'n_v': v['n'], 'n_b': bl['n'],
                     'tp_v': v['tp_pct'], 'tp_b': bl['tp_pct']}
        if b in PRIMARY_CALL_TIERS:
            n_changes.append(abs(n_pct))
            if d_tp < 0:
                primary_regressions.append(-d_tp)  # positive = how much regressed
            else:
                primary_improvements.append(d_tp)

    # Utility: weighted sum of TP% improvements weighted by baseline N
    util = 0.0
    for b in PRIMARY_CALL_TIERS:
        if b not in deltas: continue
        util += deltas[b]['d_tp_pct'] * (deltas[b]['n_b'] ** 0.5)
    # H1 bonus: reward variants where ≥3 primary tiers clear +0.5pp
    n_clear = sum(1 for b in PRIMARY_CALL_TIERS
                  if deltas.get(b, {}).get('d_tp_pct', 0) >= 0.5)
    if n_clear >= 3:
        util += 25.0 + (n_clear - 3) * 10.0   # big bonus for clearing the gate
    # Penalty: N drift on any primary tier
    n_drift_max = max(n_changes) if n_changes else 0.0
    if n_drift_max > 15:
        util -= (n_drift_max - 15) * 5.0
    # Penalty: any primary call regression > 1.0pp
    max_regress = max(primary_regressions) if primary_regressions else 0.0
    if max_regress > 1.0:
        util -= (max_regress - 1.0) * 50.0
    # H4 penalty: put regression on <25 or <15 (puts must be neutral or better)
    for b in ['<25', '<15']:
        d = deltas.get(b)
        if d is not None and d['d_tp_pct'] < -0.2:
            util -= (-d['d_tp_pct'] - 0.2) * 20.0   # quadratic-ish penalty

    return {
        'utility': util,
        'deltas': deltas,
        'n_drift_max': n_drift_max,
        'max_regress_pp': max_regress,
        'avg_lift_pp': sum(primary_improvements) / len(PRIMARY_CALL_TIERS)
                       if primary_improvements else 0.0,
    }


# ---------------------------------------------------------------------------
# Bayesian sweep driver
# ---------------------------------------------------------------------------

# Parameter bounds (low, high). Uniform prior; can swap for log later if needed.
SEARCH_SPACE = {
    # Phase E: tighter bounds based on Phase D failure (dip -44%, DD breach on 2023/dip/5y)
    'LIFT_MAX':  (3.0,  8.0),    # was 2-12; lower bound on max boost magnitude
    'TAU':       (10.0, 45.0),
    'LBMIN':     (10,   16),     # was 7-14; favor longer recency exclusion (avoid in-dip priors)
    'LBMAX':     (25,   55),     # was 30-60; tighter
    'W15_RATIO': (0.7,  1.5),    # was 0.7-1.8
    'MAG_EXP':   (0.8,  2.0),    # was 0.5-2.0; favor concentrating boost on high-conviction priors
    'SAT_K':     (3.0,  8.0),    # was 2-8; raise floor (less aggressive saturation)
    'WIN_NEG_W': (0.2,  1.0),    # was 0-1; require some negative weight on losses
    'MFE_FLOOR': (0.0,  0.15),   # was 0-0.3; less residual boost when consumed
    'REVERSAL_K_CALL': (0.7, 2.5),   # was 0-1.5; more aggressive call reversal penalty
    'REVERSAL_K_PUT':  (1.0, 3.0),   # was 0-2.5; even harsher on put bounces
}


def sample_random(rng: np.random.Generator) -> Variant:
    """Random-uniform sample from search space."""
    kw = {}
    for name, (lo, hi) in SEARCH_SPACE.items():
        if name in ('LBMIN', 'LBMAX'):
            kw[name] = int(rng.integers(int(lo), int(hi) + 1))
        else:
            kw[name] = float(rng.uniform(lo, hi))
    if kw['LBMIN'] > kw['LBMAX']:
        kw['LBMIN'], kw['LBMAX'] = kw['LBMAX'], kw['LBMIN']
    return Variant(**kw)


def bayesian_local_perturb(best: Variant, rng: np.random.Generator,
                            scale: float = 0.2) -> Variant:
    """Local perturbation around best variant (cheap surrogate for full BO)."""
    kw = {}
    for name, (lo, hi) in SEARCH_SPACE.items():
        cur = getattr(best, name)
        span = hi - lo
        if name in ('LBMIN', 'LBMAX'):
            new = int(round(cur + rng.normal(0, span * scale)))
            new = max(int(lo), min(int(hi), new))
            kw[name] = new
        else:
            new = cur + rng.normal(0, span * scale)
            kw[name] = float(max(lo, min(hi, new)))
    if kw['LBMIN'] > kw['LBMAX']:
        kw['LBMIN'], kw['LBMAX'] = kw['LBMAX'], kw['LBMIN']
    return Variant(**kw)


def run_sweep(peaks: pl.DataFrame, priors: pl.DataFrame, n_iter: int = 120,
              seed: int = 42) -> list:
    """Run iterative random+local sampler. Returns list of (variant, util_info, stats)."""
    rng = np.random.default_rng(seed)

    # Baseline = no boost (LIFT_MAX = 0)
    baseline_v = Variant(LIFT_MAX=0.0)
    baseline_stats = evaluate(peaks, priors, baseline_v)
    print('\n--- BASELINE (no boost) ---')
    print(f'  primary tier TP%:')
    for b in PRIMARY_CALL_TIERS + ['<25', '<15']:
        s = baseline_stats.get(b, {})
        if s.get('tp_pct') is not None:
            print(f'    {b:<6}  N={s["n"]:>6}  N_resolved={s["n_resolved"]:>6}  TP%={s["tp_pct"]:.2f}')

    history = []
    best_util = -1e9
    best_v = baseline_v
    best_stats = baseline_stats

    print(f'\n--- SWEEP: {n_iter} variants ---')
    n_random_phase = max(20, n_iter // 3)

    for i in range(n_iter):
        if i < n_random_phase or rng.random() < 0.3:
            v = sample_random(rng)
        else:
            v = bayesian_local_perturb(best_v, rng, scale=0.15)
        t0 = time.time()
        try:
            stats = evaluate(peaks, priors, v)
            u = utility(stats, baseline_stats)
        except Exception as e:
            print(f'  [#{i:03d}] ERROR: {e}')
            continue
        elapsed = time.time() - t0

        record = {
            'iter': i,
            'variant': asdict(v),
            'utility': u['utility'],
            'avg_lift_pp': u['avg_lift_pp'],
            'n_drift_max': u['n_drift_max'],
            'max_regress_pp': u['max_regress_pp'],
            'deltas': u['deltas'],
            'stats': {b: stats.get(b, {}) for b in PRIMARY_CALL_TIERS + ['<25', '<15', '<30', '70+']},
            'elapsed_s': elapsed,
        }
        history.append(record)

        is_best = u['utility'] > best_util
        if is_best:
            best_util = u['utility']
            best_v = v
            best_stats = stats

        marker = '★' if is_best else ' '
        print(f'  [#{i:03d}]{marker} util={u["utility"]:+8.2f}  '
              f'avg_lift={u["avg_lift_pp"]:+.2f}pp  '
              f'n_drift={u["n_drift_max"]:.1f}%  '
              f'max_regr={u["max_regress_pp"]:.2f}pp  '
              f'L={v.LIFT_MAX:.2f} T={v.TAU:.1f} K={v.SAT_K:.1f} '
              f'W15={v.W15_RATIO:.2f} ({elapsed:.2f}s)')

    return history, best_v, best_stats, baseline_stats


def report_top(history: list, baseline_stats: dict, top_k: int = 5):
    print('\n' + '=' * 80)
    print(f'TOP {top_k} VARIANTS BY UTILITY')
    print('=' * 80)
    sorted_h = sorted(history, key=lambda r: r['utility'], reverse=True)
    for rank, rec in enumerate(sorted_h[:top_k], 1):
        v = rec['variant']
        print(f'\nRank #{rank}  util={rec["utility"]:+.2f}  '
              f'avg_lift={rec["avg_lift_pp"]:+.2f}pp  '
              f'n_drift={rec["n_drift_max"]:.1f}%  '
              f'max_regr={rec["max_regress_pp"]:.2f}pp')
        print(f'  Variant: LIFT_MAX={v["LIFT_MAX"]:.2f}  TAU={v["TAU"]:.1f}  '
              f'LBMIN={v["LBMIN"]} LBMAX={v["LBMAX"]}  '
              f'W15={v["W15_RATIO"]:.2f}  MAG_EXP={v["MAG_EXP"]:.2f}  '
              f'SAT_K={v["SAT_K"]:.2f}  WIN_NEG={v["WIN_NEG_W"]:.2f}  '
              f'MFE_FLOOR={v["MFE_FLOOR"]:.2f}')
        print(f'  per-tier deltas:')
        print(f'    {"bkt":<6} {"N_b":>6} {"N_v":>6} {"dN%":>6}  {"TP_b":>6} {"TP_v":>6} {"dTP":>6}')
        for b in PRIMARY_CALL_TIERS + ['<25', '<15']:
            d = rec['deltas'].get(b)
            if d is None: continue
            print(f'    {b:<6} {d["n_b"]:>6} {d["n_v"]:>6} '
                  f'{d["d_n_pct"]:>+6.1f}  '
                  f'{d["tp_b"]:>6.2f} {d["tp_v"]:>6.2f} '
                  f'{d["d_tp_pct"]:>+6.2f}')


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=120, help='number of sweep iterations')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    print(f'continuation-boost sweep  ({date.today().isoformat()})')
    print(f'  n_iter={args.n}  seed={args.seed}')

    if not PEAKS_PQ.exists() or not PRIORS_PQ.exists():
        raise SystemExit(f'Missing parquets — run build_features.py first')

    print(f'\n[load] {PEAKS_PQ.name}')
    peaks = pl.read_parquet(PEAKS_PQ)
    print(f'[load]   {len(peaks):,} peaks')
    print(f'[load] {PRIORS_PQ.name}')
    priors = pl.read_parquet(PRIORS_PQ)
    print(f'[load]   {len(priors):,} prior refs')

    history, best_v, best_stats, baseline_stats = run_sweep(
        peaks, priors, n_iter=args.n, seed=args.seed
    )
    report_top(history, baseline_stats, top_k=5)

    out = {
        'date': date.today().isoformat(),
        'n_iter': args.n,
        'seed': args.seed,
        'baseline_stats': baseline_stats,
        'best_variant': asdict(best_v),
        'best_stats': best_stats,
        'history': history,
    }
    with open(RESULTS_JSON, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f'\n[done] wrote {RESULTS_JSON}')


if __name__ == '__main__':
    main()
