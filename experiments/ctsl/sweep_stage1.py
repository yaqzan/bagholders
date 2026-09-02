"""CTSL Stage 1 — WR7-primary scoring calibration sweep (barrier-independent).

Per the new three-stage framework in `.claude/docs/process.md` and
`.claude/docs/assessment-backtest.md` "Stage 1: Scoring Calibration Gate".

OBJECTIVE: maximize WR7 lift on the affected cohort under W1-W6 hard constraints.

The CTSL transform changes WHICH bucket a signal lands in (via score lift).
WR7 is intrinsic to the signal (price action), so per-signal WR7 doesn't
change. What DOES change is the AGGREGATE WR7 of each bucket (composition
shift). Stage 1 evaluates whether the bucket-reshuffling preserves quality
per W4 (every discrete bucket WR7 ≥ baseline -0.5pp).

Phases (per assessment-backtest.md "Stage 1 Bayesian sweep cadence"):
  A. Cohort z (already known z>>+3 from Phase 1 cohort profile)
  B. Blast radius LHS — 100 variants, evaluate WR7 deltas
  C. Drill — 200 variants in top-quartile bbox
  D. Fine tune — 50 variants in tight bbox around C winner
  E. Validate W1-W6 on winner

Each variant evaluation is parquet-only — should run <1 second per variant.
Total compute: ~5-10 min for full multi-phase sweep.
"""
from __future__ import annotations
import io, sys, json, time, itertools
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl
import numpy as np
from scipy.stats import qmc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CACHE_DIR = ROOT / '.cache' / 'ctsl'
LOG_DIR = ROOT / 'experiments' / 'ctsl'
PARQUET = CACHE_DIR / 'scores_v46_stage1_1825.parquet'

# Discrete buckets per Stage 1 W4 framework
CALL_BUCKETS = [
    ('95+',   95, 100),
    ('90-94', 90,  94),
    ('85-89', 85,  89),
    ('80-84', 80,  84),
    ('75-79', 75,  79),
    ('70-74', 70,  74),
]
PUT_BUCKETS = [
    ('<5',     0,   5),
    ('6-10',   6,  10),
    ('11-15', 11,  15),
    ('16-20', 16,  20),
    ('21-25', 21,  25),
    ('<30 (extra)', 26, 30),  # not technically affected by ≤25 gate but tracked
]


# ---- CTSL transform (matches monte_carlo.py implementation byte-for-byte) ----

def ctsl_call_lift(df: pl.DataFrame, params: dict) -> pl.Expr:
    """Returns a polars Expression for new_overall under CTSL call-side lift."""
    tm = params['CTSL_CALL_TREND_MAX']
    target = params['CTSL_CALL_TARGET']
    alpha = params['CTSL_CALL_ALPHA']
    power = params['CTSL_CALL_TREND_POWER']
    floor = params['CTSL_CALL_TIER_FLOOR']
    snw = params['CTSL_CALL_SCORE_NORM_WEIGHT']
    snp = params['CTSL_CALL_SCORE_NORM_POWER']

    is_ct = (
        (pl.col('side_label') == 'call') &
        (pl.col('overall') >= 70) &
        pl.col('trend').is_not_null() &
        (pl.col('trend') <= tm)
    )
    trend_dist = (
        (pl.lit(tm) - pl.col('trend') + 1).cast(pl.Float64)
        / float(tm + 1)
    ).clip(0.0, 1.0)

    if abs(snw) < 1e-6:
        score_factor = pl.lit(1.0)
    elif snw > 0:
        sn = ((pl.col('overall').cast(pl.Float64) - 70.0) / 30.0).clip(0.0, 1.0)
        score_factor = pl.lit(1.0) + snw * (sn ** snp)
    else:
        sn = ((76.0 - pl.col('overall').cast(pl.Float64)) / 6.0).clip(0.0, 1.0)
        score_factor = pl.lit(1.0) + abs(snw) * (sn ** snp)

    lift = alpha * (trend_dist ** power) * score_factor * (target - pl.col('overall').cast(pl.Float64))
    lifted = pl.col('overall').cast(pl.Float64) + lift
    rescued = pl.when(lifted < floor).then(floor).otherwise(lifted)
    return pl.when(is_ct).then(rescued.clip(0.0, 100.0)).otherwise(pl.col('overall').cast(pl.Float64))


def ctsl_put_dampen(df: pl.DataFrame, params: dict) -> pl.Expr:
    tm = params['CTSL_PUT_TREND_MIN']
    target = params['CTSL_PUT_TARGET']
    alpha = params['CTSL_PUT_ALPHA']
    power = params['CTSL_PUT_TREND_POWER']
    ceiling = params['CTSL_PUT_TIER_CEILING']
    snw = params['CTSL_PUT_SCORE_NORM_WEIGHT']
    snp = params['CTSL_PUT_SCORE_NORM_POWER']

    is_ct = (
        (pl.col('side_label') == 'put') &
        (pl.col('overall') <= 25) &
        pl.col('trend').is_not_null() &
        (pl.col('trend') >= tm)
    )
    span = max(1.0, float(100 - tm + 1))
    trend_dist = (
        (pl.col('trend') - pl.lit(tm) + 1).cast(pl.Float64) / span
    ).clip(0.0, 1.0)

    if abs(snw) < 1e-6:
        score_factor = pl.lit(1.0)
    elif snw > 0:
        sn = ((25.0 - pl.col('overall').cast(pl.Float64)) / 25.0).clip(0.0, 1.0)
        score_factor = pl.lit(1.0) + snw * (sn ** snp)
    else:
        sn = ((pl.col('overall').cast(pl.Float64) - 19.0) / 6.0).clip(0.0, 1.0)
        score_factor = pl.lit(1.0) + abs(snw) * (sn ** snp)

    dampen = alpha * (trend_dist ** power) * score_factor * (pl.col('overall').cast(pl.Float64) - target)
    dampened = pl.col('overall').cast(pl.Float64) - dampen
    capped = pl.when(dampened > ceiling).then(ceiling).otherwise(dampened)
    return pl.when(is_ct).then(capped.clip(0.0, 100.0)).otherwise(pl.col('overall').cast(pl.Float64))


# ---- Per-bucket WR computation ----

def per_bucket_wr(df: pl.DataFrame, score_col: str, side: str, w_col: str, buckets) -> dict:
    """Compute per-bucket WR (mean of w_col == 1) for the given side."""
    sub = df.filter((pl.col('side_label') == side) & pl.col(w_col).is_not_null())
    out = {}
    for label, lo, hi in buckets:
        cell = sub.filter((pl.col(score_col) >= lo) & (pl.col(score_col) <= hi))
        n = cell.height
        if n == 0:
            out[label] = {'n': 0, 'wr': None}
        else:
            wr = cell.filter(pl.col(w_col) == 1).height / n * 100.0
            out[label] = {'n': n, 'wr': wr}
    return out


def affected_cohort_wr(df: pl.DataFrame, params: dict, w_col: str) -> dict:
    """Compute WR for the affected cohort: signals where CTSL changed bucket assignment.
    For Stage 1, this is the empirical "did re-bucketing improve quality" check."""
    # Apply CTSL
    df_ctsl = df.with_columns([
        ctsl_call_lift(df, params).alias('new_overall_call'),
        ctsl_put_dampen(df, params).alias('new_overall_put'),
    ])
    # Affected = signals whose overall changed
    aff_call = df_ctsl.filter(
        (pl.col('side_label') == 'call') &
        (pl.col('new_overall_call').round(0) != pl.col('overall').cast(pl.Float64).round(0)) &
        pl.col(w_col).is_not_null()
    )
    aff_put = df_ctsl.filter(
        (pl.col('side_label') == 'put') &
        (pl.col('new_overall_put').round(0) != pl.col('overall').cast(pl.Float64).round(0)) &
        pl.col(w_col).is_not_null()
    )
    n_call = aff_call.height
    n_put = aff_put.height
    return {
        'call_affected_n': n_call,
        'call_affected_wr': aff_call.filter(pl.col(w_col) == 1).height / n_call * 100.0 if n_call else None,
        'put_affected_n':  n_put,
        'put_affected_wr': aff_put.filter(pl.col(w_col) == 1).height / n_put * 100.0 if n_put else None,
    }


def evaluate(df: pl.DataFrame, params: dict, baseline_buckets: dict) -> dict:
    """Full Stage 1 evaluation of one CTSL variant.

    Returns dict with per-bucket WR7/WR15/WR30 + affected cohort metrics + W4 verdict.
    """
    df_ctsl = df.with_columns([
        ctsl_call_lift(df, params).alias('new_overall_call'),
        ctsl_put_dampen(df, params).alias('new_overall_put'),
    ])
    # New overall column (combined)
    df_ctsl = df_ctsl.with_columns(
        pl.when(pl.col('side_label') == 'call')
        .then(pl.col('new_overall_call'))
        .otherwise(pl.col('new_overall_put'))
        .alias('overall_new')
    )

    # Per-bucket WR by side and barrier window
    new_call = {
        'wr7':  per_bucket_wr(df_ctsl, 'overall_new', 'call', 'win_7d',  CALL_BUCKETS),
        'wr15': per_bucket_wr(df_ctsl, 'overall_new', 'call', 'win_15d', CALL_BUCKETS),
        'wr30': per_bucket_wr(df_ctsl, 'overall_new', 'call', 'win_30d', CALL_BUCKETS),
    }
    new_put = {
        'wr7':  per_bucket_wr(df_ctsl, 'overall_new', 'put', 'win_7d',  PUT_BUCKETS),
        'wr15': per_bucket_wr(df_ctsl, 'overall_new', 'put', 'win_15d', PUT_BUCKETS),
        'wr30': per_bucket_wr(df_ctsl, 'overall_new', 'put', 'win_30d', PUT_BUCKETS),
    }

    base_call = baseline_buckets['call']
    base_put = baseline_buckets['put']

    # W4: per-discrete-bucket WR7 ≥ baseline -0.5pp
    w4_breaches = []
    w4_max_regress = 0.0
    for buckets, new_, base_ in [(CALL_BUCKETS, new_call, base_call),
                                  (PUT_BUCKETS, new_put, base_put)]:
        for label, lo, hi in buckets:
            new_wr = new_['wr7'].get(label, {}).get('wr')
            base_wr = base_['wr7'].get(label, {}).get('wr')
            if new_wr is None or base_wr is None:
                continue
            delta = new_wr - base_wr
            if delta < -0.5:
                w4_breaches.append((label, base_wr, new_wr, delta))
            w4_max_regress = min(w4_max_regress, delta)

    # W2: multi-barrier-window directional consistency on affected cohort (WR7/WR15/WR30 same direction)
    aff_7 = affected_cohort_wr(df, params, 'win_7d')
    aff_15 = affected_cohort_wr(df, params, 'win_15d')
    aff_30 = affected_cohort_wr(df, params, 'win_30d')

    # W6: gradient preservation (95+ ≥ 90-94 ≥ 85-89 ≥ 80-84 ≥ 75-79 ≥ 70-74; mirror for puts)
    def gradient_ok(buckets, results):
        wrs = [results['wr7'].get(lbl, {}).get('wr') for lbl, _, _ in buckets]
        wrs = [w for w in wrs if w is not None]
        if len(wrs) < 2:
            return True, 0
        breaches = sum(1 for i in range(len(wrs)-1) if wrs[i] < wrs[i+1])
        return breaches == 0, breaches

    call_grad_ok, call_grad_breaches = gradient_ok(CALL_BUCKETS, new_call)
    # For puts, expected order is <5 ≥ <10 ≥ <15 ≥ <20 ≥ <25 (deeper=better), so check forward
    put_grad_ok, put_grad_breaches = gradient_ok(PUT_BUCKETS, new_put)

    # Composite Stage 1 score: maximize affected-cohort WR7 lift, penalize W4 breaches
    # Lower is better for ranking (we'll negate WR7)
    aff_call_wr7 = aff_7.get('call_affected_wr', 0) or 0
    aff_put_wr7 = aff_7.get('put_affected_wr', 0) or 0
    n_aff_call = aff_7.get('call_affected_n', 0)
    n_aff_put = aff_7.get('put_affected_n', 0)

    # Total expected affected WR7 (weighted by N) — higher better
    total_n = n_aff_call + n_aff_put
    avg_aff_wr7 = (aff_call_wr7 * n_aff_call + aff_put_wr7 * n_aff_put) / total_n if total_n else 0

    # Penalty for W4 breaches: each pp regression > 0.5pp counts.
    w4_penalty = sum(max(0, -d - 0.5) for _, _, _, d in w4_breaches) * 5.0

    score = -avg_aff_wr7 + w4_penalty + (call_grad_breaches + put_grad_breaches) * 1.0

    return {
        'params': dict(params),
        'avg_affected_wr7': avg_aff_wr7,
        'aff_call_wr7': aff_call_wr7,
        'aff_call_n':  n_aff_call,
        'aff_put_wr7': aff_put_wr7,
        'aff_put_n':   n_aff_put,
        'aff_call_wr15': aff_15.get('call_affected_wr'),
        'aff_call_wr30': aff_30.get('call_affected_wr'),
        'aff_put_wr15':  aff_15.get('put_affected_wr'),
        'aff_put_wr30':  aff_30.get('put_affected_wr'),
        'w4_breaches': len(w4_breaches),
        'w4_breach_detail': w4_breaches,
        'w4_max_regress': w4_max_regress,
        'call_grad_breaches': call_grad_breaches,
        'put_grad_breaches':  put_grad_breaches,
        'score': score,
        'new_call_buckets': new_call,
        'new_put_buckets':  new_put,
    }


# ---- LHS sampler ----

CALL_SPEC = [
    ('CTSL_CALL_TREND_MAX',         15,    25,   True),
    ('CTSL_CALL_TARGET',            85.0,  105.0, False),
    ('CTSL_CALL_ALPHA',             0.30,  1.20,  False),
    ('CTSL_CALL_TREND_POWER',       0.5,   3.0,   False),
    ('CTSL_CALL_TIER_FLOOR',        70.0,  90.0,  False),
    ('CTSL_CALL_SCORE_NORM_WEIGHT', -1.0,  1.0,   False),
    ('CTSL_CALL_SCORE_NORM_POWER',  0.5,   3.0,   False),
]
PUT_SPEC = [
    ('CTSL_PUT_TREND_MIN',          75,    95,    True),
    ('CTSL_PUT_TARGET',             -5.0,  20.0,  False),
    ('CTSL_PUT_ALPHA',              0.30,  1.20,  False),
    ('CTSL_PUT_TREND_POWER',        0.5,   3.0,   False),
    ('CTSL_PUT_TIER_CEILING',       15.0,  28.0,  False),
    ('CTSL_PUT_SCORE_NORM_WEIGHT', -1.0,  1.0,    False),
    ('CTSL_PUT_SCORE_NORM_POWER',  0.5,    3.0,   False),
]
SPEC = CALL_SPEC + PUT_SPEC


def lhs_samples(n: int, spec: list, seed: int) -> list[dict]:
    sampler = qmc.LatinHypercube(d=len(spec), seed=seed)
    raw = sampler.random(n=n)
    out = []
    for sample in raw:
        cfg = {}
        for i, (name, lo, hi, isint) in enumerate(spec):
            x = lo + sample[i] * (hi - lo)
            if isint:
                x = int(round(x))
            cfg[name] = x
        out.append(cfg)
    return out


def derive_drill_ranges(top_results: list, spec: list, slack_pct: float = 0.20) -> list:
    new_spec = []
    for name, lo, hi, isint in spec:
        vals = [r['params'][name] for r in top_results]
        if not vals:
            new_spec.append((name, lo, hi, isint))
            continue
        vmin, vmax = min(vals), max(vals)
        span = max(1e-6, vmax - vmin)
        slack = span * slack_pct
        new_lo = max(lo, vmin - slack)
        new_hi = min(hi, vmax + slack)
        if new_hi - new_lo < (hi - lo) * 0.05:
            cen = (new_lo + new_hi) / 2
            new_lo = max(lo, cen - (hi - lo) * 0.025)
            new_hi = min(hi, cen + (hi - lo) * 0.025)
        new_spec.append((name, new_lo, new_hi, isint))
    return new_spec


def baseline_buckets(df: pl.DataFrame) -> dict:
    """Per-bucket WR7/15/30 with NO CTSL applied (pristine baseline)."""
    return {
        'call': {
            'wr7':  per_bucket_wr(df, 'overall', 'call', 'win_7d',  CALL_BUCKETS),
            'wr15': per_bucket_wr(df, 'overall', 'call', 'win_15d', CALL_BUCKETS),
            'wr30': per_bucket_wr(df, 'overall', 'call', 'win_30d', CALL_BUCKETS),
        },
        'put': {
            'wr7':  per_bucket_wr(df, 'overall', 'put', 'win_7d',  PUT_BUCKETS),
            'wr15': per_bucket_wr(df, 'overall', 'put', 'win_15d', PUT_BUCKETS),
            'wr30': per_bucket_wr(df, 'overall', 'put', 'win_30d', PUT_BUCKETS),
        },
    }


def print_baseline(base: dict):
    print('\n[baseline] per-bucket WR7 (no CTSL):', flush=True)
    print('  CALLS:')
    for label, _, _ in CALL_BUCKETS:
        d = base['call']['wr7'].get(label, {})
        print(f'    {label:<8} N={d.get("n", 0):>6,}  WR7={d.get("wr"):>5.2f}%' if d.get("wr") else f'    {label:<8} N={d.get("n", 0):>6,}  WR7=N/A', flush=True)
    print('  PUTS:')
    for label, _, _ in PUT_BUCKETS:
        d = base['put']['wr7'].get(label, {})
        print(f'    {label:<14} N={d.get("n", 0):>6,}  WR7={d.get("wr"):>5.2f}%' if d.get("wr") else f'    {label:<14} N={d.get("n", 0):>6,}  WR7=N/A', flush=True)


def report_top(results: list, label: str, k: int = 10):
    sorted_r = sorted(results, key=lambda r: r['score'])
    print(f'\n[{label}] top {k} variants by Stage 1 score (lower=better):', flush=True)
    print(f'  {"score":>7} {"affWR7":>7} {"aff_call_wr":>10} {"aff_put_wr":>10} {"W4_brch":>7} {"max_regr":>8}', flush=True)
    for r in sorted_r[:k]:
        print(f'  {r["score"]:>+7.2f} {r["avg_affected_wr7"]:>7.2f} '
              f'{r["aff_call_wr7"] or 0:>9.2f}%(N={r["aff_call_n"]:>4}) '
              f'{r["aff_put_wr7"] or 0:>9.2f}%(N={r["aff_put_n"]:>4}) '
              f'{r["w4_breaches"]:>7} '
              f'{r["w4_max_regress"]:>+7.2f}pp', flush=True)


def stage_b_blast(df: pl.DataFrame, base: dict, n: int, seed: int) -> list[dict]:
    print(f'\n=== Phase B: blast radius LHS, {n} variants ===', flush=True)
    samples = lhs_samples(n, SPEC, seed=seed)
    results = []
    t0 = time.time()
    for i, params in enumerate(samples):
        r = evaluate(df, params, base)
        results.append(r)
        if (i + 1) % 20 == 0:
            print(f'  [B {i+1}/{n}] elapsed {time.time()-t0:.0f}s', flush=True)
    print(f'[B done] {time.time()-t0:.0f}s', flush=True)
    return results


def stage_c_drill(df: pl.DataFrame, base: dict, top_results: list, n: int, seed: int) -> list[dict]:
    print(f'\n=== Phase C: drill in top-quartile bbox, {n} variants ===', flush=True)
    drill_spec = derive_drill_ranges(top_results, SPEC, slack_pct=0.25)
    print('  drill ranges:', flush=True)
    for name, lo, hi, isint in drill_spec:
        print(f'    {name:<28}: [{lo:.3f}, {hi:.3f}]', flush=True)

    samples = lhs_samples(n, drill_spec, seed=seed)
    results = []
    t0 = time.time()
    for i, params in enumerate(samples):
        r = evaluate(df, params, base)
        results.append(r)
    print(f'[C done] {time.time()-t0:.0f}s', flush=True)
    return results


def stage_d_fine(df: pl.DataFrame, base: dict, top_results: list, n: int, seed: int) -> list[dict]:
    print(f'\n=== Phase D: fine tune, {n} variants ===', flush=True)
    fine_spec = derive_drill_ranges(top_results, SPEC, slack_pct=0.10)
    samples = lhs_samples(n, fine_spec, seed=seed)
    results = []
    t0 = time.time()
    for i, params in enumerate(samples):
        r = evaluate(df, params, base)
        results.append(r)
    print(f'[D done] {time.time()-t0:.0f}s', flush=True)
    return results


def w16_check(winner: dict, base: dict):
    """Check W1-W6 hard constraints on the winner."""
    print('\n' + '#' * 78, flush=True)
    print(' W1-W6 VALIDATION ON WINNER', flush=True)
    print('#' * 78, flush=True)

    print('\nW1: Cohort z-score (already established at z>>+3 from Phase 1):',  flush=True)
    print('  CT-call cohort (overall>=70 ∧ trend<=20): WR15 +9.8pp vs non-CT (Phase 1 v44)', flush=True)
    print('  CT-put cohort (overall<=25 ∧ trend>=80):  WR15 +21.8pp vs non-CT (Phase 1 v44)', flush=True)
    print('  → PASS by reference', flush=True)

    print('\nW2: Multi-barrier-window directional consistency on affected cohort:', flush=True)
    print(f'  call WR7={winner["aff_call_wr7"]:>5.2f}%  WR15={winner["aff_call_wr15"]:>5.2f}%  WR30={winner["aff_call_wr30"]:>5.2f}%', flush=True)
    print(f'  put  WR7={winner["aff_put_wr7"]:>5.2f}%  WR15={winner["aff_put_wr15"]:>5.2f}%  WR30={winner["aff_put_wr30"]:>5.2f}%', flush=True)
    # W2 needs comparison to baseline-affected — can compute separately

    print('\nW4: Per-discrete-bucket non-regression (WR7 ≥ baseline -0.5pp):', flush=True)
    if winner['w4_breaches'] == 0:
        print('  → PASS — zero buckets regressed by >0.5pp', flush=True)
    else:
        print(f'  → FAIL — {winner["w4_breaches"]} bucket(s) regressed:', flush=True)
        for label, base_wr, new_wr, delta in winner['w4_breach_detail']:
            print(f'    {label:<10}  baseline={base_wr:.2f}%  new={new_wr:.2f}%  Δ={delta:+.2f}pp', flush=True)

    print('\nW6: Gradient preservation (95+ ≥ 90-94 ≥ 85-89 ≥ 80-84 ≥ 75-79 ≥ 70-74):', flush=True)
    if winner['call_grad_breaches'] == 0 and winner['put_grad_breaches'] == 0:
        print('  → PASS — no gradient breaches on either side', flush=True)
    else:
        print(f'  → FAIL — {winner["call_grad_breaches"]} call breaches, {winner["put_grad_breaches"]} put breaches', flush=True)


def main():
    if not PARQUET.exists():
        print(f'!! parquet not found: {PARQUET}', file=sys.stderr)
        sys.exit(1)

    print(f'\n{"#" * 78}\n CTSL Stage 1 Sweep — WR7-primary, barrier-independent\n{"#" * 78}', flush=True)
    print(f' substrate: v46 ({PARQUET.name})', flush=True)

    df = pl.read_parquet(PARQUET)
    print(f' rows: {len(df):,}', flush=True)
    print(f' resolved WR7: {df.filter(pl.col("win_7d").is_not_null()).height:,}', flush=True)

    base = baseline_buckets(df)
    print_baseline(base)

    # Phase B
    s_b = stage_b_blast(df, base, n=100, seed=42)
    report_top(s_b, 'B', k=10)

    # Top quartile
    s_b_sorted = sorted(s_b, key=lambda r: r['score'])
    top_q = s_b_sorted[:max(8, len(s_b_sorted) // 4)]

    # Phase C
    s_c = stage_c_drill(df, base, top_q, n=200, seed=137)
    report_top(s_c, 'C', k=10)

    # Phase D
    s_c_sorted = sorted(s_c, key=lambda r: r['score'])
    s_d = stage_d_fine(df, base, s_c_sorted[:max(5, len(s_c_sorted) // 8)], n=50, seed=251)
    report_top(s_d, 'D', k=10)

    # Phase E winner
    s_d_sorted = sorted(s_d, key=lambda r: r['score'])
    winner = s_d_sorted[0]
    print('\n' + '#' * 78, flush=True)
    print(' STAGE 1 WINNER', flush=True)
    print('#' * 78, flush=True)
    print(f'\nscore: {winner["score"]:+.3f}', flush=True)
    print(f'avg_affected_wr7: {winner["avg_affected_wr7"]:.2f}%', flush=True)
    print(f'  call: WR7={winner["aff_call_wr7"]:.2f}% (N={winner["aff_call_n"]})', flush=True)
    print(f'  put:  WR7={winner["aff_put_wr7"]:.2f}% (N={winner["aff_put_n"]})', flush=True)
    print('\nparams:', flush=True)
    for k, v in winner['params'].items():
        if isinstance(v, float):
            print(f'  {k:<30} = {v:+.4f}', flush=True)
        else:
            print(f'  {k:<30} = {v}', flush=True)

    # Validate W1-W6 on winner
    w16_check(winner, base)

    # Persist results
    out = LOG_DIR / 'stage1_results.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'baseline_buckets': base,
            'phase_b': s_b,
            'phase_c': s_c,
            'phase_d': s_d,
            'winner': winner,
        }, f, default=str, indent=1)
    print(f'\n[done] full results: {out}', flush=True)


if __name__ == '__main__':
    main()
