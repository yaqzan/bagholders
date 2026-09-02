"""Phase I — Ichimoku slow-axis blend INTO calculate_weekly_adjustment.

RESEARCH ONLY. Production scoring code (`database/utils/scoring.py`) is
NOT modified — this script monkey-patches `calculate_weekly_adjustment` at
runtime via the simulator's scoring path. Failed hypotheses leave zero
trace in production.

Hypothesis: blend a slow-axis Ichimoku signal INTO the wadj computation:

    fast_total = base_bias + momentum_bias    # current calendar-week composite
    slow_axis  = MAX × tanh(kijun_pct / KIJ_SAT)
    total      = (1 − α) × fast_total + α × slow_axis

When kijun_pct disagrees with fast_total (e.g. fast wadj=+10 but kijun_pct=-12
in a slow downtrend), slow_axis pulls the wadj toward bearish, dampening
calendar-week whiplash. When they agree, the blend is near-pass-through.

Tested via the FULL faithful simulator (not parquet approximation) because
the modified wadj cascades through volume amplification, regime mult, and
6+ downstream dampeners — none of which are linear in wadj.

Usage:
    python -u experiments/weekly_avwap/phase_i_wadj_blend_sweep.py
    PHASE_I_SAMPLE=20 PHASE_I_LOOKBACK=730 python -u experiments/weekly_avwap/phase_i_wadj_blend_sweep.py
"""
from __future__ import annotations

import io
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments._holdout import assert_no_holdout_leak  # noqa: E402

import numpy as np
import database.utils.scoring as _sc

# ── Runtime monkey-patch infrastructure ───────────────────────────────
# We do NOT modify production scoring.py. Instead, we patch
# `calculate_weekly_adjustment` at runtime with a wrapper that closes over
# the current (alpha, sat, max) parameters. The simulator's compute_overall_score
# call path imports this function by name, so the patch is picked up at call time.

_ORIGINAL_CALC_WADJ = _sc.calculate_weekly_adjustment
_ORIGINAL_COMPUTE_OVERALL = _sc.compute_overall_score

# Mutable parameter slots — set per variant before each sim run
_PHASE_I_PARAMS = {'alpha': 0.0, 'sat': 15.0, 'max': 15.0, 'current_kijun_pct': None}


def _patched_calculate_weekly_adjustment(trend, rsi, macd,
                                          prev_trend=None, prev_rsi=None, prev_macd=None):
    """Phase I wrapper — blends Ichimoku slow-axis into the original wadj total.
    Reads kijun_pct from _PHASE_I_PARAMS slot, populated by patched compute_overall_score.
    """
    total, detail = _ORIGINAL_CALC_WADJ(trend, rsi, macd, prev_trend, prev_rsi, prev_macd)
    if detail is None:
        return total, detail

    alpha = _PHASE_I_PARAMS['alpha']
    kp = _PHASE_I_PARAMS.get('current_kijun_pct')
    if alpha <= 0 or kp is None:
        return total, detail

    sat = _PHASE_I_PARAMS['sat']
    cap = _PHASE_I_PARAMS['max']

    # w_bias + w_mom are pre-asymmetric; original `total` may have ×1.5 applied.
    fast_total = float(detail['w_bias']) + float(detail['w_mom'])
    slow_axis = cap * float(np.tanh(float(kp) / sat))
    blended = (1.0 - alpha) * fast_total + alpha * slow_axis
    if blended < 0:
        blended *= 1.5

    detail = dict(detail)
    detail['w_adj'] = round(blended, 1)
    detail['w_slow'] = round(slow_axis, 1)
    detail['w_fast'] = round(fast_total, 1)
    return blended, detail


def _patched_compute_overall_score(*args, **kwargs):
    """Wrapper that captures kijun_pct kwarg into the params slot,
    then calls the original. The inner patched calculate_weekly_adjustment
    reads from the slot."""
    _PHASE_I_PARAMS['current_kijun_pct'] = kwargs.get('kijun_pct')
    try:
        return _ORIGINAL_COMPUTE_OVERALL(*args, **kwargs)
    finally:
        _PHASE_I_PARAMS['current_kijun_pct'] = None


def _install_patches():
    _sc.calculate_weekly_adjustment = _patched_calculate_weekly_adjustment
    _sc.compute_overall_score = _patched_compute_overall_score
    # Reach into simulator module which imports compute_overall_score by name
    import simulator as _sim_mod
    _sim_mod.compute_overall_score = _patched_compute_overall_score


def _uninstall_patches():
    _sc.calculate_weekly_adjustment = _ORIGINAL_CALC_WADJ
    _sc.compute_overall_score = _ORIGINAL_COMPUTE_OVERALL
    import simulator as _sim_mod
    _sim_mod.compute_overall_score = _ORIGINAL_COMPUTE_OVERALL


LOOKBACK_DAYS = int(os.environ.get('PHASE_I_LOOKBACK', '1825'))
SAMPLE_N = int(os.environ.get('PHASE_I_SAMPLE', '40'))
RNG_SEED = 13
VARIANT_SET = os.environ.get('PHASE_I_VARIANTS', 'broad')  # 'broad' or 'finalists'

# Variants (α, kij_sat, max_pts) — α=0 is identity (v44 baseline)
_VARIANT_SETS = {
    'broad': [
        {'name': 'baseline',     'alpha': 0.00, 'sat': 15.0, 'max': 15.0},
        {'name': 'a015_s15_m15', 'alpha': 0.15, 'sat': 15.0, 'max': 15.0},
        {'name': 'a030_s15_m15', 'alpha': 0.30, 'sat': 15.0, 'max': 15.0},
        {'name': 'a050_s15_m15', 'alpha': 0.50, 'sat': 15.0, 'max': 15.0},
        {'name': 'a030_s10_m15', 'alpha': 0.30, 'sat': 10.0, 'max': 15.0},
        {'name': 'a030_s20_m15', 'alpha': 0.30, 'sat': 20.0, 'max': 15.0},
        {'name': 'a030_s15_m10', 'alpha': 0.30, 'sat': 15.0, 'max': 10.0},
        {'name': 'a030_s15_m20', 'alpha': 0.30, 'sat': 15.0, 'max': 20.0},
    ],
    'finalists': [
        {'name': 'baseline',     'alpha': 0.00, 'sat': 15.0, 'max': 15.0},
        {'name': 'a030_s20_m15', 'alpha': 0.30, 'sat': 20.0, 'max': 15.0},
        {'name': 'a030_s15_m10', 'alpha': 0.30, 'sat': 15.0, 'max': 10.0},
    ],
}
VARIANTS = _VARIANT_SETS[VARIANT_SET]


def select_sample_symbols(n: int, seed: int) -> list[str]:
    """Pick a representative sample of symbols. n=0 means full universe (no shuffle)."""
    import random
    from database.models.core import Stock

    syms = [
        s.symbol for s in Stock.select(Stock.symbol)
        .where(Stock.symbol.is_null(False))
        .namedtuples()
    ]
    if n == 0:
        return syms
    random.seed(seed)
    random.shuffle(syms)
    return syms[:n]


def build_v44_baseline_lookup(symbols: list[str], lookback_days: int) -> dict:
    """Pull v44 stored scores for the sample symbols.

    Returns {(sym, date): overall} dict for fast lookup during diff.
    """
    from database.models.core import Score, AlgorithmVersion

    av = AlgorithmVersion.get(AlgorithmVersion.git_commit == 'd8024b9')
    cutoff = date.today() - timedelta(days=lookback_days)
    rows = Score.select(Score.symbol, Score.date, Score.overall).where(
        Score.symbol.in_(symbols),
        Score.date >= cutoff,
        Score.version == av,
    ).namedtuples()
    out = {}
    for r in rows:
        out[(r.symbol, r.date)] = int(r.overall)
    return out


def fetch_barrier_results(side: str, w_days: int, symbols: list[str]) -> dict:
    """Pull barrier_outcomes (option-aligned) for sample symbols.

    Returns {(sym, date): result(0/1)}.
    """
    import sqlite3
    cache = ROOT / '.cache' / 'barrier_outcomes.db'
    conn = sqlite3.connect(str(cache))
    placeholders = ','.join('?' * len(symbols))
    q = f"""
        SELECT symbol, date, result FROM barrier_outcomes
        WHERE side=? AND barrier_set='30dte_opt' AND w_days=?
          AND symbol IN ({placeholders})
    """
    out = {}
    for sym, d_str, res in conn.execute(q, [side, w_days, *symbols]):
        if res is None:
            continue
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        out[(sym, d)] = int(res)
    conn.close()
    return out


def extract_peaks(scores: dict, lookback_days: int) -> tuple[list, list]:
    """From {(sym, date): overall}, extract call peaks (>=70) and put peaks (<=25).

    Returns (call_peaks, put_peaks) as lists of (sym, date, score).
    """
    cutoff = date.today() - timedelta(days=lookback_days)
    calls, puts = [], []
    for (sym, d), s in scores.items():
        if d < cutoff:
            continue
        if s >= 70:
            calls.append((sym, d, s))
        elif s <= 25:
            puts.append((sym, d, s))
    return calls, puts


def wr_by_tier(peaks: list, results: dict, side_label: str) -> dict:
    """Compute WR15 + N for cumulative tiers.

    side_label: 'call' or 'put'
    """
    if side_label == 'call':
        tiers = [70, 75, 80, 85, 90, 95]
    else:
        tiers = [30, 25, 20, 15, 10, 5]

    out = {}
    for t in tiers:
        if side_label == 'call':
            cohort = [p for p in peaks if p[2] >= t]
        else:
            cohort = [p for p in peaks if p[2] <= t]
        results_for = [results[(p[0], p[1])] for p in cohort if (p[0], p[1]) in results]
        n = len(results_for)
        wr = (sum(results_for) / n * 100) if n > 0 else float('nan')
        out[t] = {'n': n, 'wr15': wr}
    return out


def compute_stability(sim_scores: dict) -> dict:
    """For each (sym, date) pair where the prior trading day's score exists,
    bucket |Δoverall| by weekday of the LATER day. Mon-class swings dominate
    because the calendar weekly bar flipped over the weekend.

    Returns {weekday_label: mean_abs_delta} plus aggregate metrics.
    """
    by_sym = defaultdict(list)
    for (sym, d), s in sim_scores.items():
        by_sym[sym].append((d, s))

    deltas_by_dow = defaultdict(list)  # 0=Mon ... 4=Fri
    for sym, entries in by_sym.items():
        entries.sort(key=lambda x: x[0])
        for i in range(1, len(entries)):
            d_now, s_now = entries[i]
            d_prev, s_prev = entries[i - 1]
            # Only adjacent trading days (≤4 cal days apart, covers weekend gap)
            gap = (d_now - d_prev).days
            if gap < 1 or gap > 4:
                continue
            dow = d_now.weekday()
            deltas_by_dow[dow].append(abs(s_now - s_prev))

    labels = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri'}
    out = {}
    for dow, label in labels.items():
        d = deltas_by_dow.get(dow, [])
        if d:
            out[label] = {'n': len(d), 'mean_abs_delta': sum(d) / len(d)}
        else:
            out[label] = {'n': 0, 'mean_abs_delta': float('nan')}

    mon = out['Mon']['mean_abs_delta']
    tue_fri = []
    for dow in (1, 2, 3, 4):
        tue_fri.extend(deltas_by_dow.get(dow, []))
    tue_fri_mean = sum(tue_fri) / len(tue_fri) if tue_fri else float('nan')
    out['Mon_TueFri_ratio'] = mon / tue_fri_mean if tue_fri_mean and tue_fri_mean == tue_fri_mean else float('nan')
    out['TueFri_mean'] = tue_fri_mean
    out['Mon_mean'] = mon
    return out


def run_variant(variant: dict, sim, baseline_scores: dict | None,
                call_results: dict, put_results: dict) -> dict:
    """Apply variant params via runtime slot, run simulator, compute WR15 + stability."""
    _PHASE_I_PARAMS['alpha'] = variant['alpha']
    _PHASE_I_PARAMS['sat']   = variant['sat']
    _PHASE_I_PARAMS['max']   = variant['max']

    print(f"  [variant] α={variant['alpha']} sat={variant['sat']} max={variant['max']}", flush=True)
    t0 = time.time()
    sim_scores = sim.simulate()
    print(f"  [sim] {len(sim_scores):,} scores in {time.time()-t0:.0f}s", flush=True)

    # Holdout protection
    sim_dates = {d for (_, d) in sim_scores.keys()}
    assert_no_holdout_leak(sim_dates, context=f"phase_i {variant['name']}")

    calls, puts = extract_peaks(sim_scores, LOOKBACK_DAYS)
    stability = compute_stability(sim_scores)

    return {
        'name': variant['name'],
        'sim_scores_n': len(sim_scores),
        'calls': wr_by_tier(calls, call_results, 'call'),
        'puts':  wr_by_tier(puts,  put_results,  'put'),
        'stability': stability,
    }


def main():
    print('=' * 100)
    print(f'Phase I — wadj-blend sweep  (lookback={LOOKBACK_DAYS}d, sample={SAMPLE_N} stocks)')
    print('=' * 100)

    # 1) Sample symbols
    syms = select_sample_symbols(SAMPLE_N, RNG_SEED)
    print(f'[setup] {len(syms)} symbols sampled (seed={RNG_SEED})')
    print(f'[setup] sample: {", ".join(syms[:10])}{"..." if len(syms) > 10 else ""}')

    # 2) Pre-fetch barrier outcomes for these symbols (one-shot)
    t0 = time.time()
    call_results = fetch_barrier_results('low', 15, syms)   # 'low' = call territory
    put_results  = fetch_barrier_results('high', 15, syms)  # 'high' = put territory
    print(f'[setup] barrier outcomes loaded — calls: {len(call_results):,}  puts: {len(put_results):,}  ({time.time()-t0:.0f}s)')

    # 3) Build simulator once (heavy load — reused across variants)
    print('[setup] building ScoreSimulator (loading data into memory)...')
    t0 = time.time()
    from simulator import ScoreSimulator
    sim = ScoreSimulator(symbols=syms, lookback_days=LOOKBACK_DAYS)
    print(f'[setup] simulator ready  ({time.time()-t0:.0f}s)  contexts={len(sim._contexts)}')

    # 4) Install runtime patches (production scoring.py is NEVER modified)
    _install_patches()
    print('[setup] runtime patches installed (calculate_weekly_adjustment, compute_overall_score)')

    # 5) Run baseline + each variant
    all_results = []
    try:
        for v in VARIANTS:
            print(f'\n[run] {v["name"]}')
            r = run_variant(v, sim, None, call_results, put_results)
            all_results.append(r)
    finally:
        _uninstall_patches()
        print('[teardown] patches removed; production scoring restored')

    # 5) Compare each variant to baseline
    base = next(r for r in all_results if r['name'] == 'baseline')
    print('\n' + '=' * 110)
    print('PHASE I — RESULTS  (Δ = variant vs baseline α=0)')
    print('=' * 110)
    print(f'  {"variant":<18s}'
          f'{"95+ WR / N":>14s}{"Δ":>7s}{"":<2s}'
          f'{"85+ WR / N":>14s}{"Δ":>7s}{"":<2s}'
          f'{"75+ WR / N":>14s}{"Δ":>7s}{"":<2s}'
          f'{"<25 WR / N":>14s}{"Δ":>7s}{"":<2s}'
          f'{"<15 WR / N":>14s}{"Δ":>7s}')
    print('-' * 132)

    for r in all_results:
        b95 = base['calls'][95]; v95 = r['calls'][95]
        b85 = base['calls'][85]; v85 = r['calls'][85]
        b75 = base['calls'][75]; v75 = r['calls'][75]
        bp25 = base['puts'][25]; vp25 = r['puts'][25]
        bp15 = base['puts'][15]; vp15 = r['puts'][15]
        d95 = (v95['wr15'] - b95['wr15']) if not (v95['wr15'] != v95['wr15']) else 0.0
        d85 = (v85['wr15'] - b85['wr15']) if not (v85['wr15'] != v85['wr15']) else 0.0
        d75 = (v75['wr15'] - b75['wr15']) if not (v75['wr15'] != v75['wr15']) else 0.0
        dp25 = (vp25['wr15'] - bp25['wr15']) if not (vp25['wr15'] != vp25['wr15']) else 0.0
        dp15 = (vp15['wr15'] - bp15['wr15']) if not (vp15['wr15'] != vp15['wr15']) else 0.0
        print(f'  {r["name"]:<18s}'
              f'{v95["wr15"]:>5.1f}/{v95["n"]:>5d}  {d95:>+5.1f}  '
              f'{v85["wr15"]:>5.1f}/{v85["n"]:>5d}  {d85:>+5.1f}  '
              f'{v75["wr15"]:>5.1f}/{v75["n"]:>5d}  {d75:>+5.1f}  '
              f'{vp25["wr15"]:>5.1f}/{vp25["n"]:>5d}  {dp25:>+5.1f}  '
              f'{vp15["wr15"]:>5.1f}/{vp15["n"]:>5d}  {dp15:>+5.1f}')

    print('\nNote: WR15 measured against option-aligned barriers (30dte_opt, w=15).')
    print(f'      Sample is {SAMPLE_N} stocks — directional signal only; full universe needed for ship gate.')

    # Stability — Mon vs Tue-Fri |Δoverall|
    print('\n' + '=' * 110)
    print('STABILITY — mean |Δoverall| by weekday  (lower Mon and lower Mon/Tue-Fri ratio = smoother wadj)')
    print('=' * 110)
    print(f'  {"variant":<18s}{"Mon":>8s}{"Tue":>8s}{"Wed":>8s}{"Thu":>8s}{"Fri":>8s}'
          f'{"Mon mean":>12s}{"Tue-Fri":>10s}{"ratio":>8s}{"Δratio":>9s}')
    print('-' * 105)
    base_ratio = base['stability']['Mon_TueFri_ratio']
    for r in all_results:
        st = r['stability']
        d_ratio = st['Mon_TueFri_ratio'] - base_ratio
        print(f'  {r["name"]:<18s}'
              f'{st["Mon"]["mean_abs_delta"]:>8.2f}'
              f'{st["Tue"]["mean_abs_delta"]:>8.2f}'
              f'{st["Wed"]["mean_abs_delta"]:>8.2f}'
              f'{st["Thu"]["mean_abs_delta"]:>8.2f}'
              f'{st["Fri"]["mean_abs_delta"]:>8.2f}'
              f'{st["Mon_mean"]:>12.3f}'
              f'{st["TueFri_mean"]:>10.3f}'
              f'{st["Mon_TueFri_ratio"]:>8.3f}'
              f'{d_ratio:>+9.3f}')

    # Save raw data for follow-up analysis
    import json
    tag = f'n{SAMPLE_N}' if SAMPLE_N > 0 else 'full'
    out_path = ROOT / '.cache' / 'weekly_avwap' / f'phase_i_results_{tag}.json'
    out_path.parent.mkdir(exist_ok=True, parents=True)
    with open(out_path, 'w') as fh:
        json.dump(all_results, fh, default=str, indent=2)
    print(f'\n[done] raw results → {out_path}')


if __name__ == '__main__':
    main()
