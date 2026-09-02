"""Path B: TP%-calibrated direct routing + Q1 ship gate.

For each peak, compute prior_signal_strength from priors. Bin by strength.
Measure empirical TP% per (strength_bin, current_tier) cell. Route promoted
peaks to existing tier whose baseline TP% matches the cohort's empirical TP%.

Q1 SHIP GATE: After routing, re-measure destination tier TP%. If routing
preserves or improves the destination tier baseline, PASS.

Sweep axes (small):
  TAU       — recency decay constant
  MAG_EXP   — exponent on prior magnitude
  SIG_NORM  — normalization for prior_signal aggregation
  STRENGTH_THRESHOLD — minimum prior_signal to enable routing
"""
from __future__ import annotations
import io, sys, json, time
from datetime import date
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, asdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl
import numpy as np

CACHE_DIR = ROOT / '.cache' / 'continuation_boost'
PEAKS_PQ_V2 = CACHE_DIR / 'peaks_v4.parquet'   # v4: widest universe (50-100 calls)
PRIORS_PQ_V2 = CACHE_DIR / 'priors_v4.parquet'

OUT_DIR = Path(__file__).parent
TIMESTAMP = time.strftime('%Y%m%d_%H%M%S')
RESULTS_JSON = OUT_DIR / f'path_b_results_{TIMESTAMP}.json'
LOG_PATH = OUT_DIR / f'path_b_run_{TIMESTAMP}.log'

# Existing cumulative call buckets (for routing destination)
CALL_TIERS = [95, 90, 85, 80, 75, 70]   # >= threshold

# Q1 GATE THRESHOLDS
Q1_PASS_TOLERANCE_PP = 0.5   # destination tier TP% may drop ≤0.5pp post-routing

# Sub-threshold-only constraint per user requirement
SUB_THRESHOLD_BOUND = 75   # boost only applies to peaks with overall < this


@dataclass
class Variant:
    TAU:       float = 25.0    # recency exponential decay
    MAG_EXP:   float = 1.0     # exponent on prior magnitude
    SIG_NORM:  float = 5.0     # normalize prior_signal_total by this in tanh
    SIG_MIN:   float = 0.3     # min prior_signal to enable routing


def aggregate_prior_signal(peaks: pl.DataFrame, priors: pl.DataFrame, v: Variant) -> pl.DataFrame:
    """Compute prior_signal aggregated per peak.

    prior_signal = tanh(SUM_priors(decay × magnitude × sig) / SIG_NORM)
                ∈ [-1, +1]
    """
    p = priors.with_columns([
        (-pl.col('gap_days').cast(pl.Float64) / v.TAU).exp().alias('decay'),
        ((pl.col('prior_conviction').cast(pl.Float64) / 50.0).pow(v.MAG_EXP)).alias('magnitude'),
    ])
    p = p.with_columns(
        (pl.col('decay') * pl.col('magnitude') * pl.col('sig')).alias('contribution')
    )
    agg = p.group_by(['symbol', 'date']).agg(
        pl.col('contribution').sum().alias('prior_signal_raw')
    )
    agg = agg.with_columns(
        (pl.col('prior_signal_raw') / v.SIG_NORM).tanh().alias('prior_signal')
    )
    return peaks.join(agg, on=['symbol', 'date'], how='left').with_columns(
        pl.col('prior_signal').fill_null(0.0)
    )


def measure_baseline_tier_tp(peaks: pl.DataFrame) -> dict:
    """Measure baseline TP% for each cumulative call tier."""
    out = {}
    for t in CALL_TIERS:
        sub = peaks.filter(pl.col('overall') >= t)
        n = sub.height
        wins = sub.filter(pl.col('outcome_tp_15d') == 1).height
        losses = sub.filter(pl.col('outcome_tp_15d') == 0).height
        resolved = wins + losses
        tp_pct = 100 * wins / resolved if resolved else None
        out[f'{t}+'] = {'N': n, 'wins': wins, 'losses': losses,
                        'tp_pct': tp_pct}
    return out


def measure_strength_cohort_tp(peaks_with_signal: pl.DataFrame, v: Variant) -> dict:
    """For each prior_signal strength bin, measure empirical TP% on
    sub-threshold (overall < 75) peaks. This tells us what tier's baseline
    a routed promotion would land near."""
    sub = peaks_with_signal.filter(
        (pl.col('overall') < SUB_THRESHOLD_BOUND) &
        (pl.col('prior_signal') >= v.SIG_MIN)
    )
    out = {}
    # Bin by prior_signal in 0.1 increments
    for lo in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        hi = lo + 0.1
        cohort = sub.filter((pl.col('prior_signal') >= lo) & (pl.col('prior_signal') < hi))
        n = cohort.height
        wins = cohort.filter(pl.col('outcome_tp_15d') == 1).height
        losses = cohort.filter(pl.col('outcome_tp_15d') == 0).height
        resolved = wins + losses
        tp_pct = 100 * wins / resolved if resolved else None
        out[f'{lo:.1f}-{hi:.1f}'] = {
            'N': n, 'wins': wins, 'losses': losses,
            'tp_pct': tp_pct,
        }
    # Also report overall sub-threshold cohort with prior_signal>=SIG_MIN
    n = sub.height
    wins = sub.filter(pl.col('outcome_tp_15d') == 1).height
    losses = sub.filter(pl.col('outcome_tp_15d') == 0).height
    resolved = wins + losses
    out['ALL_PROMOTABLE'] = {
        'N': n, 'wins': wins, 'losses': losses,
        'tp_pct': 100 * wins / resolved if resolved else None,
    }
    return out


def route_to_tier_by_tp(cohort_tp: float, baseline_tier_tp: dict) -> str:
    """Route to the tier whose baseline TP% best matches the cohort TP%
    (and equal-or-greater allocation justified)."""
    best_tier = '75+'   # default destination (just into trade universe)
    best_diff = float('inf')
    for tier, stats in baseline_tier_tp.items():
        tier_tp = stats.get('tp_pct')
        if tier_tp is None: continue
        # Prefer routing to tier where cohort TP% ≥ baseline (means promotion preserves quality)
        if cohort_tp >= tier_tp - 1.0:   # within 1pp
            # Among qualifying tiers, pick the HIGHEST one (biggest allocation)
            tier_pct = int(tier.rstrip('+'))
            best_tier_pct = int(best_tier.rstrip('+'))
            if tier_pct > best_tier_pct:
                best_tier = tier
    return best_tier


def evaluate_with_routing(peaks: pl.DataFrame, priors: pl.DataFrame, v: Variant) -> dict:
    """Apply Path B routing + Q1 ship gate evaluation."""
    pk = aggregate_prior_signal(peaks, priors, v)

    # Baseline (no routing)
    baseline = measure_baseline_tier_tp(pk)

    # Compute per-strength-bin cohort TP% on sub-threshold promotables
    strength_cohorts = measure_strength_cohort_tp(pk, v)

    # Determine destination tier per peak
    # For each promotable (overall<75, signal>=SIG_MIN), find its strength bin
    # and assign destination tier based on cohort TP% lookup.
    # For non-promotables, keep their original tier.
    pk = pk.with_columns(
        pl.when(pl.col('overall') >= 95).then(pl.lit('95+'))
          .when(pl.col('overall') >= 90).then(pl.lit('90+'))
          .when(pl.col('overall') >= 85).then(pl.lit('85+'))
          .when(pl.col('overall') >= 80).then(pl.lit('80+'))
          .when(pl.col('overall') >= 75).then(pl.lit('75+'))
          .when(pl.col('overall') >= 70).then(pl.lit('70+'))
          .otherwise(pl.lit('<70'))
          .alias('original_tier')
    )

    # Route promotable peaks based on their strength bin
    def route_for_strength(s):
        if s is None or s < v.SIG_MIN: return None  # not promoted
        # Find the strength bin
        for lo in [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]:
            if s >= lo:
                hi = lo + 0.1
                key = f'{lo:.1f}-{hi:.1f}'
                cohort = strength_cohorts.get(key, {})
                cohort_tp = cohort.get('tp_pct')
                if cohort_tp is None or cohort['N'] < 30:
                    return '75+'   # too few samples; default to lowest entry tier
                return route_to_tier_by_tp(cohort_tp, baseline)
        return None

    # Apply: for each peak with overall<75 and signal>=SIG_MIN, route to chosen tier
    routed_tier_col = []
    for r in pk.iter_rows(named=True):
        if r['overall'] >= SUB_THRESHOLD_BOUND:
            routed_tier_col.append(r['original_tier'])  # already qualifying, no change
        else:
            new = route_for_strength(r['prior_signal'])
            if new is None:
                routed_tier_col.append(r['original_tier'])  # not promoted
            else:
                routed_tier_col.append(new)
    pk = pk.with_columns(pl.Series('routed_tier', routed_tier_col))

    # Q1 SHIP GATE: re-measure destination tier TP% post-routing
    # Each tier T's NEW population = original peaks with overall>=T + promoted peaks with routed_tier in {T or higher}
    # For cumulative tier T: count any peak whose routed_tier is T or higher
    tier_order = ['70+', '75+', '80+', '85+', '90+', '95+']
    def tier_rank(t): return tier_order.index(t) if t in tier_order else -1

    post_routing = {}
    for t in CALL_TIERS:
        # Cumulative: routed_tier rank >= rank of T
        t_str = f'{t}+'
        threshold_rank = tier_rank(t_str)
        sub = pk.filter(pl.col('routed_tier').map_elements(
            lambda x: tier_rank(x) >= threshold_rank, return_dtype=pl.Boolean
        ))
        n = sub.height
        wins = sub.filter(pl.col('outcome_tp_15d') == 1).height
        losses = sub.filter(pl.col('outcome_tp_15d') == 0).height
        resolved = wins + losses
        tp_pct = 100 * wins / resolved if resolved else None
        post_routing[t_str] = {'N': n, 'wins': wins, 'losses': losses, 'tp_pct': tp_pct}

    # Compare: baseline vs post-routing
    gate_pass = True
    deltas = {}
    for t_str in [f'{t}+' for t in CALL_TIERS]:
        b = baseline[t_str]
        p = post_routing[t_str]
        if b['tp_pct'] is None or p['tp_pct'] is None:
            deltas[t_str] = {'pass': True, 'reason': 'no data'}
            continue
        d_tp = p['tp_pct'] - b['tp_pct']
        d_n  = p['N'] - b['N']
        passed = d_tp >= -Q1_PASS_TOLERANCE_PP
        if not passed: gate_pass = False
        deltas[t_str] = {
            'pass': passed,
            'd_tp': d_tp, 'd_n': d_n,
            'baseline_tp': b['tp_pct'], 'post_tp': p['tp_pct'],
            'baseline_N': b['N'], 'post_N': p['N'],
        }

    n_promoted = sum(1 for r, o in zip(routed_tier_col, pk['original_tier'].to_list())
                     if r != o)

    return {
        'variant': asdict(v),
        'baseline': baseline,
        'strength_cohorts': strength_cohorts,
        'post_routing': post_routing,
        'deltas': deltas,
        'q1_pass': gate_pass,
        'n_promoted': n_promoted,
    }


def report(res: dict, log):
    v = res['variant']
    log(f'\n  Variant: TAU={v["TAU"]:.1f}  MAG_EXP={v["MAG_EXP"]:.2f}  '
        f'SIG_NORM={v["SIG_NORM"]:.1f}  SIG_MIN={v["SIG_MIN"]:.2f}')
    log(f'  N promoted: {res["n_promoted"]:,}')
    log(f'  Strength cohorts (sub-threshold only):')
    log(f'    {"bin":<12} {"N":>6} {"TP%":>6}')
    for k, v_ in res['strength_cohorts'].items():
        if v_['N'] < 30: continue
        tp = v_["tp_pct"]
        log(f'    {k:<12} {v_["N"]:>6} {(f"{tp:.1f}%" if tp is not None else "-"):>6}')

    log(f'\n  Q1 GATE (destination tier TP% post-routing):')
    log(f'    {"tier":<6} {"N_b":>6} {"N_p":>6} {"dN":>7}  {"TP_b":>6} {"TP_p":>6} {"dTP":>7}  PASS')
    for t_str, d in res['deltas'].items():
        if 'reason' in d:
            log(f'    {t_str:<6} no-data')
            continue
        marker = '✓' if d['pass'] else '✗'
        log(f'    {t_str:<6} {d["baseline_N"]:>6} {d["post_N"]:>6} {d["d_n"]:>+7}  '
            f'{d["baseline_tp"]:>6.2f} {d["post_tp"]:>6.2f} {d["d_tp"]:>+7.2f}  {marker}')

    log(f'\n  OVERALL Q1 GATE: {"PASS" if res["q1_pass"] else "FAIL"}')


def main():
    log_f = open(LOG_PATH, 'w', encoding='utf-8')
    def log(s): print(s, flush=True); log_f.write(s + '\n'); log_f.flush()

    log(f'Path B router + Q1 ship gate  ({date.today().isoformat()})')
    log(f'  sub-threshold bound: overall < {SUB_THRESHOLD_BOUND}')
    log(f'  Q1 tolerance: dest tier TP% may drop ≤ {Q1_PASS_TOLERANCE_PP}pp')

    log(f'\n[load] features ...')
    peaks = pl.read_parquet(PEAKS_PQ_V2)
    priors = pl.read_parquet(PRIORS_PQ_V2)
    log(f'  peaks: {len(peaks):,}  priors: {len(priors):,}')

    # Sweep small grid
    variants = []
    for TAU in [15.0, 25.0, 40.0]:
        for MAG_EXP in [0.7, 1.0, 1.5]:
            for SIG_NORM in [3.0, 5.0, 8.0]:
                for SIG_MIN in [0.2, 0.4, 0.6]:
                    variants.append(Variant(TAU=TAU, MAG_EXP=MAG_EXP,
                                              SIG_NORM=SIG_NORM, SIG_MIN=SIG_MIN))

    log(f'\n[sweep] {len(variants)} variants ...')
    all_results = []
    pass_results = []
    for i, v in enumerate(variants):
        try:
            res = evaluate_with_routing(peaks, priors, v)
        except Exception as e:
            log(f'  variant #{i} ERROR: {e}')
            continue
        all_results.append(res)
        if res['q1_pass'] and res['n_promoted'] >= 50:
            pass_results.append(res)

    log(f'\n[sweep] {len(pass_results)}/{len(variants)} variants PASS Q1 with N_promoted ≥ 50')

    # Sort pass_results by N_promoted (more promotions = more deployable signal)
    pass_results.sort(key=lambda r: r['n_promoted'], reverse=True)

    log(f'\n{"="*78}')
    log(f'TOP Q1-PASSING VARIANTS')
    log(f'{"="*78}')
    for i, r in enumerate(pass_results[:5]):
        log(f'\n  Rank #{i+1}')
        report(r, log)

    # Save results
    with open(RESULTS_JSON, 'w') as f:
        json.dump({
            'timestamp': TIMESTAMP,
            'all_n': len(all_results),
            'pass_n': len(pass_results),
            'top_pass': pass_results[:5],
            'baseline': all_results[0]['baseline'] if all_results else {},
        }, f, indent=2, default=str)
    log(f'\n[done] wrote {RESULTS_JSON}')


if __name__ == '__main__':
    main()
