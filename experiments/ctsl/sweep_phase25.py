"""CTSL Phase 2.5 — staged Latin Hypercube + drill, extended parameter space.

Per the staged-Bayesian methodology in .claude/docs/assessment-backtest.md
"Calibration Sweep Methodology", uniform grid is an anti-pattern. This sweep:

  Stage 1 — Blast radius:  LHS over the FULL high-dim space (~150 variants)
  Stage 2 — Drill:         LHS in narrowed ranges around top decile (~200)
  Stage 3 — Final tune:    Dense LHS around Stage 2 winner (~80)

vs the prior 480-variant uniform grid which spent ~95% of compute outside the
productive basin and missed several parameter dimensions entirely.

NEW parameters added per the post-Phase-2 review:

  - score_norm_weight ∈ [-1.0, +1.0]  How much source-bucket modulates lift.
        +1.0: stronger CT signals (75-79) get MORE lift  (over-promote strong)
         0.0: trend_dist alone drives lift                (Phase 2 default)
        -1.0: weaker CT signals (70-72) get MORE lift    (rescue mode)

  - score_norm_power ∈ [0.5, 3.0]    Shape of score_norm coupling.

  - kijun_gate ∈ {none, require_neg, ramp_neg}
        none:        ignore Ichimoku weekly state
        require_neg: only fire CTSL when kijun_pct < 0 (confirms counter-trend)
        ramp_neg:    multiplicative penalty (1 + tanh(-kijun_pct/k)) (smooth)

  - trend_max_call ∈ [15, 35]        WIDER than CT_PROMOTE's 20. v44 grid
                                      shows alpha extends through trend=30.
  - trend_min_put  ∈ [60, 95]        WIDER than CT_PROMOTE's 80.

  - tier_floor (call) ∈ [70, 90]     Snap-to-floor for rescued signals.
  - tier_ceiling (put) ∈ [15, 28]    Cap on dampened put scores.

Objective — composite portfolio impact, NOT raw alpha_capture:

  pnl_ctsl   = sum over CT cohort (under variant gate) of:
                (cascade_alloc(new_overall) × option_pnl)
  pnl_native = sum of (cascade_alloc(overall) × option_pnl)  [no CT mechanism]
  pnl_promote = sum of (CT_PROMOTE_alloc × option_pnl)       [current shipped]

  The ABSOLUTE bar to clear is pnl_ctsl > pnl_native (CTSL beats no-CT).
  The COMPETITIVE bar is pnl_ctsl > pnl_promote (CTSL beats current).

  Composite utility:
    util = (pnl_ctsl - pnl_native) / max(1e-6, pnl_promote - pnl_native)
         - 0.30 * frac_dropped         (penalize drops)
         - 0.10 * |frac_in_ultra - 0.20| (penalize over- or under-promotion
                                          to ULTRA — sanity gradient)

  util > 1.0 means CTSL exceeds CT_PROMOTE on net portfolio impact.
  util ∈ [0.5, 1.0] means CTSL captures most of CT_PROMOTE's portfolio gain.
  util < 0 means CTSL is worse than NO mechanism at all (red flag).
"""
from __future__ import annotations
import io, sys, json, time, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl
import numpy as np
from scipy.stats import qmc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CACHE_DIR = ROOT / '.cache' / 'ctsl'
LOOKBACK_DAYS = 1825

# Cascade tier allocations (30 DTE shipped).
CALL_ALLOC = {'ultra': 0.20, 'top': 0.15, 'mid': 0.10, 'low': 0.10, 'overflow': 0.00}
PUT_ALLOC = {'put_top': 0.12, 'put_mid': 0.10, 'put_low': 0.08}
CT_PROMOTE_CALL_ALLOC = CALL_ALLOC['ultra']    # 0.20
CT_PROMOTE_PUT_ALLOC = PUT_ALLOC['put_top']    # 0.12


def _call_alloc_expr(col: pl.Expr) -> pl.Expr:
    return (
        pl.when(col >= 95).then(CALL_ALLOC['ultra'])
        .when(col >= 85).then(CALL_ALLOC['top'])
        .when(col >= 80).then(CALL_ALLOC['mid'])
        .when(col >= 75).then(CALL_ALLOC['low'])
        .when(col >= 70).then(CALL_ALLOC['overflow'])
        .otherwise(0.0)
    )


def _put_alloc_expr(col: pl.Expr) -> pl.Expr:
    return (
        pl.when(col <= 15).then(PUT_ALLOC['put_top'])
        .when(col <= 20).then(PUT_ALLOC['put_mid'])
        .when(col <= 25).then(PUT_ALLOC['put_low'])
        .otherwise(0.0)
    )


# ---- CTSL transforms (extended) ----

def apply_ctsl_call(
    df: pl.DataFrame,
    trend_max: float,
    target: float,
    alpha: float,
    trend_power: float,
    tier_floor: float,
    score_norm_weight: float,
    score_norm_power: float,
    kijun_gate: str,
    kijun_k: float = 5.0,
) -> pl.DataFrame:
    """Extended CTSL call lift.

    lift = α × trend_dist^P × score_norm_factor × kijun_factor × (target - overall)
    new_overall = max(overall + lift, tier_floor) for CT-eligible signals

      score_norm_factor:
        if score_norm_weight == 0: factor = 1.0
        if score_norm_weight > 0:
            sn = (overall - 70) / 30, clipped to [0,1]   (1.0 at overall=100)
            factor = 1 + score_norm_weight × sn^score_norm_power
        if score_norm_weight < 0:
            sn = (76 - overall) / 6, clipped to [0,1]    (1.0 at overall=70)
            factor = 1 + |score_norm_weight| × sn^score_norm_power

      kijun_factor (multiplicative gate on lift):
        none:        1.0
        require_neg: 1.0 if kijun_pct < 0 else 0.0
        ramp_neg:    clip(1 - kijun_pct/kijun_k, 0, 2)  (smooth penalty)
    """
    is_ct_call = (
        (pl.col('side_label') == 'call') &
        (pl.col('overall') >= 70) &
        (pl.col('trend').is_not_null()) &
        (pl.col('trend') <= trend_max)
    )
    trend_dist = (
        (pl.lit(trend_max) - pl.col('trend') + 1).cast(pl.Float64)
        / float(trend_max + 1)
    ).clip(0.0, 1.0)

    if abs(score_norm_weight) < 1e-6:
        score_factor = pl.lit(1.0)
    elif score_norm_weight > 0:
        sn = ((pl.col('overall').cast(pl.Float64) - 70.0) / 30.0).clip(0.0, 1.0)
        score_factor = pl.lit(1.0) + score_norm_weight * (sn ** score_norm_power)
    else:
        sn = ((76.0 - pl.col('overall').cast(pl.Float64)) / 6.0).clip(0.0, 1.0)
        score_factor = pl.lit(1.0) + abs(score_norm_weight) * (sn ** score_norm_power)

    kp = pl.col('kijun_pct').cast(pl.Float64)
    if kijun_gate == 'require_neg':
        kij_factor = pl.when(kp.is_null()).then(0.5)\
                       .when(kp < 0).then(1.0)\
                       .otherwise(0.0)
    elif kijun_gate == 'ramp_neg':
        # Smooth: 1.0 if kijun_pct=0; rises if negative; falls if positive
        kij_factor = (pl.lit(1.0) - kp / kijun_k).clip(0.0, 2.0)
        kij_factor = pl.when(kp.is_null()).then(0.5).otherwise(kij_factor)
    else:  # 'none'
        kij_factor = pl.lit(1.0)

    lift = (
        alpha
        * (trend_dist ** trend_power)
        * score_factor
        * kij_factor
        * (target - pl.col('overall').cast(pl.Float64))
    )
    lifted = pl.col('overall').cast(pl.Float64) + lift
    rescued = pl.when(is_ct_call & (lifted < tier_floor)).then(tier_floor).otherwise(lifted)
    new_call = pl.when(is_ct_call).then(rescued.clip(0.0, 100.0)).otherwise(pl.col('overall').cast(pl.Float64))
    return df.with_columns(new_call.alias('new_overall_call'))


def apply_ctsl_put(
    df: pl.DataFrame,
    trend_min: float,
    target: float,
    alpha: float,
    trend_power: float,
    tier_ceiling: float,
    score_norm_weight: float,
    score_norm_power: float,
    kijun_gate: str,
    kijun_k: float = 5.0,
) -> pl.DataFrame:
    """Mirror CTSL for puts. Pushes overall DOWN toward target."""
    is_ct_put = (
        (pl.col('side_label') == 'put') &
        (pl.col('overall') <= 25) &
        (pl.col('trend').is_not_null()) &
        (pl.col('trend') >= trend_min)
    )
    span = max(1.0, float(100 - trend_min + 1))
    trend_dist = (
        (pl.col('trend') - pl.lit(trend_min) + 1).cast(pl.Float64) / span
    ).clip(0.0, 1.0)

    if abs(score_norm_weight) < 1e-6:
        score_factor = pl.lit(1.0)
    elif score_norm_weight > 0:
        sn = ((25.0 - pl.col('overall').cast(pl.Float64)) / 25.0).clip(0.0, 1.0)
        score_factor = pl.lit(1.0) + score_norm_weight * (sn ** score_norm_power)
    else:
        sn = ((pl.col('overall').cast(pl.Float64) - 19.0) / 6.0).clip(0.0, 1.0)
        score_factor = pl.lit(1.0) + abs(score_norm_weight) * (sn ** score_norm_power)

    kp = pl.col('kijun_pct').cast(pl.Float64)
    if kijun_gate == 'require_pos':
        kij_factor = pl.when(kp.is_null()).then(0.5)\
                       .when(kp > 0).then(1.0)\
                       .otherwise(0.0)
    elif kijun_gate == 'ramp_pos':
        kij_factor = (pl.lit(1.0) + kp / kijun_k).clip(0.0, 2.0)
        kij_factor = pl.when(kp.is_null()).then(0.5).otherwise(kij_factor)
    else:
        kij_factor = pl.lit(1.0)

    dampen = (
        alpha
        * (trend_dist ** trend_power)
        * score_factor
        * kij_factor
        * (pl.col('overall').cast(pl.Float64) - target)
    )
    dampened = pl.col('overall').cast(pl.Float64) - dampen
    capped = pl.when(is_ct_put & (dampened > tier_ceiling)).then(tier_ceiling).otherwise(dampened)
    new_put = pl.when(is_ct_put).then(capped.clip(0.0, 100.0)).otherwise(pl.col('overall').cast(pl.Float64))
    return df.with_columns(new_put.alias('new_overall_put'))


# ---- Evaluation ----

def evaluate_call(df_call: pl.DataFrame, params: dict) -> dict:
    df = apply_ctsl_call(df_call, **params)
    df = df.with_columns([
        _call_alloc_expr(pl.col('overall').cast(pl.Float64)).alias('alloc_native'),
        _call_alloc_expr(pl.col('new_overall_call')).alias('alloc_ctsl'),
    ])
    ct = df.filter(
        (pl.col('overall') >= 70) &
        (pl.col('trend').is_not_null()) &
        (pl.col('trend') <= params['trend_max']) &
        pl.col('opt_result_15').is_not_null()
    )
    n_ct = ct.height
    if n_ct == 0:
        return None

    pnl_native = (ct['alloc_native'] * ct['opt_exit_return_15']).sum()
    pnl_promote = (CT_PROMOTE_CALL_ALLOC * ct['opt_exit_return_15']).sum()
    pnl_ctsl = (ct['alloc_ctsl'] * ct['opt_exit_return_15']).sum()

    n_dropped = ct.filter(pl.col('alloc_ctsl') == 0).height
    n_ultra = ct.filter(pl.col('alloc_ctsl') == CALL_ALLOC['ultra']).height
    n_top = ct.filter(pl.col('alloc_ctsl') == CALL_ALLOC['top']).height
    n_mid_low = ct.filter(pl.col('alloc_ctsl').is_in([CALL_ALLOC['mid'], CALL_ALLOC['low']])).height

    frac_drop = n_dropped / max(1, n_ct)
    frac_ultra = n_ultra / max(1, n_ct)

    # Composite utility: how much of the (pnl_promote - pnl_native) gain
    # CTSL captures, minus penalties for drops and over-/under-promotion.
    gain_promote = float(pnl_promote - pnl_native)
    gain_ctsl = float(pnl_ctsl - pnl_native)
    capture_ratio = gain_ctsl / gain_promote if abs(gain_promote) > 1e-6 else 0.0
    util = (
        capture_ratio
        - 0.30 * frac_drop
        # Sanity penalty: drift toward 20% in ULTRA (rough match to CT_PROMOTE's ULTRA-uniform target).
        # Soft — we want to permit configs that put more or fewer in ULTRA if they help util.
        - 0.10 * abs(frac_ultra - 0.20)
    )

    return {
        'side': 'call',
        'params': dict(params),
        'n_ct': n_ct,
        'pnl_native': float(pnl_native),
        'pnl_promote': float(pnl_promote),
        'pnl_ctsl': float(pnl_ctsl),
        'gain_promote': gain_promote,
        'gain_ctsl': gain_ctsl,
        'capture_ratio': capture_ratio,
        'util': util,
        'n_dropped': n_dropped,
        'frac_drop': frac_drop,
        'n_ultra': n_ultra,
        'n_top': n_top,
        'n_mid_low': n_mid_low,
        'frac_ultra': frac_ultra,
    }


def evaluate_put(df_put: pl.DataFrame, params: dict) -> dict:
    df = apply_ctsl_put(df_put, **params)
    df = df.with_columns([
        _put_alloc_expr(pl.col('overall').cast(pl.Float64)).alias('alloc_native'),
        _put_alloc_expr(pl.col('new_overall_put')).alias('alloc_ctsl'),
    ])
    ct = df.filter(
        (pl.col('overall') <= 25) &
        (pl.col('trend').is_not_null()) &
        (pl.col('trend') >= params['trend_min']) &
        pl.col('opt_result_15').is_not_null()
    )
    n_ct = ct.height
    if n_ct == 0:
        return None

    pnl_native = (ct['alloc_native'] * ct['opt_exit_return_15']).sum()
    pnl_promote = (CT_PROMOTE_PUT_ALLOC * ct['opt_exit_return_15']).sum()
    pnl_ctsl = (ct['alloc_ctsl'] * ct['opt_exit_return_15']).sum()

    n_dropped = ct.filter(pl.col('alloc_ctsl') == 0).height
    n_top = ct.filter(pl.col('alloc_ctsl') == PUT_ALLOC['put_top']).height

    frac_drop = n_dropped / max(1, n_ct)
    frac_top = n_top / max(1, n_ct)

    gain_promote = float(pnl_promote - pnl_native)
    gain_ctsl = float(pnl_ctsl - pnl_native)
    capture_ratio = gain_ctsl / gain_promote if abs(gain_promote) > 1e-6 else 0.0
    util = (
        capture_ratio
        - 0.30 * frac_drop
        - 0.05 * abs(frac_top - 0.65)  # sanity drift toward ~65% in put_top
    )
    return {
        'side': 'put',
        'params': dict(params),
        'n_ct': n_ct,
        'pnl_native': float(pnl_native),
        'pnl_promote': float(pnl_promote),
        'pnl_ctsl': float(pnl_ctsl),
        'gain_promote': gain_promote,
        'gain_ctsl': gain_ctsl,
        'capture_ratio': capture_ratio,
        'util': util,
        'n_dropped': n_dropped,
        'frac_drop': frac_drop,
        'n_top': n_top,
        'frac_top': frac_top,
    }


# ---- LHS sampler ----

def lhs(n_samples: int, ranges: list, integer_dims: set | None = None, seed: int = 0):
    """Latin Hypercube samples in the box defined by `ranges` = list of (lo, hi) per dim.
    Dims listed in `integer_dims` (by index) are rounded to int."""
    integer_dims = integer_dims or set()
    sampler = qmc.LatinHypercube(d=len(ranges), seed=seed)
    raw = sampler.random(n=n_samples)  # uniform [0,1]^d
    out = []
    for sample in raw:
        cfg = {}
        for i, (lo, hi) in enumerate(ranges):
            x = lo + sample[i] * (hi - lo)
            if i in integer_dims:
                x = int(round(x))
            cfg[i] = x
        out.append(cfg)
    return out


# ---- Stage runners ----

CALL_PARAM_SPEC = [
    ('trend_max',          15, 35,  True),   # int gate
    ('target',             85, 105, False),  # float
    ('alpha',              0.30, 1.20, False),
    ('trend_power',        0.5, 3.0, False),
    ('tier_floor',         70, 90, False),
    ('score_norm_weight', -1.0, 1.0, False),
    ('score_norm_power',   0.5, 3.0, False),
]
CALL_KIJUN_GATES = ['none', 'require_neg', 'ramp_neg']

PUT_PARAM_SPEC = [
    ('trend_min',          60, 95, True),
    ('target',            -5, 20, False),
    ('alpha',              0.30, 1.20, False),
    ('trend_power',        0.5, 3.0, False),
    ('tier_ceiling',      15, 28, False),
    ('score_norm_weight', -1.0, 1.0, False),
    ('score_norm_power',   0.5, 3.0, False),
]
PUT_KIJUN_GATES = ['none', 'require_pos', 'ramp_pos']


def sample_call_lhs(n: int, seed: int, ranges_override=None) -> list[dict]:
    spec = ranges_override or CALL_PARAM_SPEC
    ranges = [(lo, hi) for (_, lo, hi, _) in spec]
    int_dims = {i for i, (_, _, _, isint) in enumerate(spec) if isint}
    samples = lhs(n, ranges, integer_dims=int_dims, seed=seed)
    out = []
    rng = np.random.default_rng(seed + 7)
    for s in samples:
        cfg = {spec[i][0]: s[i] for i in range(len(spec))}
        cfg['kijun_gate'] = CALL_KIJUN_GATES[rng.integers(0, len(CALL_KIJUN_GATES))]
        out.append(cfg)
    return out


def sample_put_lhs(n: int, seed: int, ranges_override=None) -> list[dict]:
    spec = ranges_override or PUT_PARAM_SPEC
    ranges = [(lo, hi) for (_, lo, hi, _) in spec]
    int_dims = {i for i, (_, _, _, isint) in enumerate(spec) if isint}
    samples = lhs(n, ranges, integer_dims=int_dims, seed=seed)
    out = []
    rng = np.random.default_rng(seed + 13)
    for s in samples:
        cfg = {spec[i][0]: s[i] for i in range(len(spec))}
        cfg['kijun_gate'] = PUT_KIJUN_GATES[rng.integers(0, len(PUT_KIJUN_GATES))]
        out.append(cfg)
    return out


def derive_drill_ranges(top_results: list, spec: list) -> list:
    """Tighten ranges to the bounding box of top-decile results, plus 25% slack on each side."""
    new_spec = []
    for name, lo, hi, isint in spec:
        vals = [r['params'][name] for r in top_results]
        if not vals:
            new_spec.append((name, lo, hi, isint))
            continue
        vmin, vmax = min(vals), max(vals)
        span = max(1e-6, vmax - vmin)
        slack = span * 0.25
        new_lo = max(lo, vmin - slack)
        new_hi = min(hi, vmax + slack)
        # Ensure non-degenerate range
        if new_hi - new_lo < (hi - lo) * 0.1:
            cen = (new_lo + new_hi) / 2
            new_lo = max(lo, cen - (hi - lo) * 0.05)
            new_hi = min(hi, cen + (hi - lo) * 0.05)
        new_spec.append((name, new_lo, new_hi, isint))
    return new_spec


def stage(label: str, side: str, evals: list, top_pct: float = 0.10) -> tuple[list, list]:
    """Sort evals by util desc, return (top_decile, all_sorted)."""
    evals = sorted(evals, key=lambda r: -r['util'])
    n_top = max(5, int(round(len(evals) * top_pct)))
    top = evals[:n_top]
    print(f'\n[{label}/{side}] {len(evals)} evals — top util={top[0]["util"]:+.4f}  '
          f'top decile (N={n_top}): util range [{top[-1]["util"]:+.4f}, {top[0]["util"]:+.4f}]')
    return top, evals


def run_call_sweep(df_call: pl.DataFrame, log_path: Path):
    print('\n' + '=' * 78)
    print(' CALL — Stage 1 blast radius (LHS over 8-dim space)')
    print('=' * 78)
    s1 = sample_call_lhs(n=200, seed=42)
    s1_evals = []
    for cfg in s1:
        r = evaluate_call(df_call, cfg)
        if r is not None:
            s1_evals.append(r)
    s1_top, s1_all = stage('S1', 'call', s1_evals, top_pct=0.10)

    print('\n' + '=' * 78)
    print(' CALL — Stage 2 drill (LHS in narrowed ranges around top decile)')
    print('=' * 78)
    drill_spec = derive_drill_ranges(s1_top, CALL_PARAM_SPEC)
    print('  drill ranges:')
    for name, lo, hi, isint in drill_spec:
        print(f'    {name:>20}: [{lo}, {hi}]')
    # Restrict kijun_gate to what's seen in top decile (most common)
    kgs = [r['params']['kijun_gate'] for r in s1_top]
    top_gate_set = list(set(kgs)) or CALL_KIJUN_GATES
    print(f'    kijun_gate: {top_gate_set}')

    s2 = sample_call_lhs(n=300, seed=137, ranges_override=drill_spec)
    rng = np.random.default_rng(99)
    for cfg in s2:
        cfg['kijun_gate'] = top_gate_set[rng.integers(0, len(top_gate_set))]
    s2_evals = []
    for cfg in s2:
        r = evaluate_call(df_call, cfg)
        if r is not None:
            s2_evals.append(r)
    s2_top, s2_all = stage('S2', 'call', s2_evals, top_pct=0.05)

    print('\n' + '=' * 78)
    print(' CALL — Stage 3 final tune (dense LHS around Stage 2 winner)')
    print('=' * 78)
    fine_spec = derive_drill_ranges(s2_top, drill_spec)
    s3 = sample_call_lhs(n=120, seed=251, ranges_override=fine_spec)
    fine_gate_set = list(set([r['params']['kijun_gate'] for r in s2_top])) or top_gate_set
    rng = np.random.default_rng(257)
    for cfg in s3:
        cfg['kijun_gate'] = fine_gate_set[rng.integers(0, len(fine_gate_set))]
    s3_evals = []
    for cfg in s3:
        r = evaluate_call(df_call, cfg)
        if r is not None:
            s3_evals.append(r)
    s3_top, s3_all = stage('S3', 'call', s3_evals, top_pct=0.05)

    # Persist
    with open(log_path, 'w', encoding='utf-8') as f:
        for stage_label, evals in [('S1', s1_all), ('S2', s2_all), ('S3', s3_all)]:
            for r in evals:
                f.write(json.dumps({'stage': stage_label, **r}, default=str) + '\n')

    return {'s1': s1_all, 's2': s2_all, 's3': s3_all, 'winner': s3_top[0] if s3_top else None}


def run_put_sweep(df_put: pl.DataFrame, log_path: Path):
    print('\n' + '=' * 78)
    print(' PUT — Stage 1 blast radius (LHS over 8-dim space)')
    print('=' * 78)
    s1 = sample_put_lhs(n=200, seed=43)
    s1_evals = []
    for cfg in s1:
        r = evaluate_put(df_put, cfg)
        if r is not None:
            s1_evals.append(r)
    s1_top, s1_all = stage('S1', 'put', s1_evals, top_pct=0.10)

    print('\n' + '=' * 78)
    print(' PUT — Stage 2 drill')
    print('=' * 78)
    drill_spec = derive_drill_ranges(s1_top, PUT_PARAM_SPEC)
    for name, lo, hi, isint in drill_spec:
        print(f'    {name:>20}: [{lo}, {hi}]')
    kgs = [r['params']['kijun_gate'] for r in s1_top]
    top_gate_set = list(set(kgs)) or PUT_KIJUN_GATES
    print(f'    kijun_gate: {top_gate_set}')

    s2 = sample_put_lhs(n=300, seed=139, ranges_override=drill_spec)
    rng = np.random.default_rng(101)
    for cfg in s2:
        cfg['kijun_gate'] = top_gate_set[rng.integers(0, len(top_gate_set))]
    s2_evals = []
    for cfg in s2:
        r = evaluate_put(df_put, cfg)
        if r is not None:
            s2_evals.append(r)
    s2_top, s2_all = stage('S2', 'put', s2_evals, top_pct=0.05)

    print('\n' + '=' * 78)
    print(' PUT — Stage 3 final tune')
    print('=' * 78)
    fine_spec = derive_drill_ranges(s2_top, drill_spec)
    s3 = sample_put_lhs(n=120, seed=253, ranges_override=fine_spec)
    fine_gate_set = list(set([r['params']['kijun_gate'] for r in s2_top])) or top_gate_set
    rng = np.random.default_rng(259)
    for cfg in s3:
        cfg['kijun_gate'] = fine_gate_set[rng.integers(0, len(fine_gate_set))]
    s3_evals = []
    for cfg in s3:
        r = evaluate_put(df_put, cfg)
        if r is not None:
            s3_evals.append(r)
    s3_top, s3_all = stage('S3', 'put', s3_evals, top_pct=0.05)

    with open(log_path, 'w', encoding='utf-8') as f:
        for stage_label, evals in [('S1', s1_all), ('S2', s2_all), ('S3', s3_all)]:
            for r in evals:
                f.write(json.dumps({'stage': stage_label, **r}, default=str) + '\n')

    return {'s1': s1_all, 's2': s2_all, 's3': s3_all, 'winner': s3_top[0] if s3_top else None}


def print_winner(side: str, w: dict | None):
    if w is None:
        print(f'\n[{side}] no winner — empty result set')
        return
    print(f'\n  [{side} WINNER]')
    print(f'    util          = {w["util"]:+.4f}')
    print(f'    capture_ratio = {w["capture_ratio"]:+.4f}  (1.0 = matches CT_PROMOTE on net gain)')
    print(f'    n_ct          = {w["n_ct"]}')
    print(f'    pnl_native    = {w["pnl_native"]:+.3f}')
    print(f'    pnl_promote   = {w["pnl_promote"]:+.3f}')
    print(f'    pnl_ctsl      = {w["pnl_ctsl"]:+.3f}')
    print(f'    gain_vs_promote = {w["pnl_ctsl"] - w["pnl_promote"]:+.3f}')
    print(f'    frac_drop     = {w["frac_drop"]:.2%}')
    if side == 'call':
        print(f'    n_ultra/top/mid_low = {w["n_ultra"]}/{w["n_top"]}/{w["n_mid_low"]}')
    else:
        print(f'    n_top         = {w["n_top"]}')
    print(f'    PARAMS:')
    for k, v in w['params'].items():
        if isinstance(v, float):
            print(f'      {k:>22} = {v:+.4f}')
        else:
            print(f'      {k:>22} = {v}')


def main(label: str):
    pq = CACHE_DIR / f'scores_{label}_{LOOKBACK_DAYS}.parquet'
    if not pq.exists():
        print(f'!! parquet not found: {pq}', file=sys.stderr)
        return

    df = pl.read_parquet(pq)
    df_call = df.filter(pl.col('side_label') == 'call')
    df_put = df.filter(pl.col('side_label') == 'put')

    print(f'\n{"#" * 78}')
    print(f'  CTSL Phase 2.5 sweep — {label} ({pq.name})')
    print(f'  rows: {len(df):,}  call: {len(df_call):,}  put: {len(df_put):,}')
    print(f'{"#" * 78}')

    t0 = time.time()
    call_log = CACHE_DIR / f'phase25_{label}_call.jsonl'
    call_results = run_call_sweep(df_call, call_log)
    print(f'\n[{label}] call sweep: {time.time()-t0:.1f}s')

    t0 = time.time()
    put_log = CACHE_DIR / f'phase25_{label}_put.jsonl'
    put_results = run_put_sweep(df_put, put_log)
    print(f'\n[{label}] put sweep: {time.time()-t0:.1f}s')

    print('\n' + '#' * 78)
    print(f'  CTSL Phase 2.5 — {label} WINNERS')
    print('#' * 78)
    print_winner('call', call_results['winner'])
    print_winner('put', put_results['winner'])


if __name__ == '__main__':
    labels = sys.argv[1:] or ['v46']
    for lab in labels:
        main(lab)
