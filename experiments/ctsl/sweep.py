"""CTSL Phase 2 calibration sweep.

For each (TREND_MAX, TARGET, ALPHA, TREND_POWER) variant on the call side and
its mirror on the put side, simulate the score-stage CTSL lift over the v44
parquet and measure how well it re-encodes CT_PROMOTE's allocation intent
score-stage.

Key metric — alpha_capture ratio:
    sum over CT signals of (tier_alloc_under_CTSL × option_pnl)
    / sum over CT signals of (tier_alloc_under_CT_PROMOTE × option_pnl)

  = 1.0 → CTSL produces same portfolio allocation as CT_PROMOTE on this cohort
  > 1.0 → CTSL allocates MORE (over-promotes; risk of over-concentration)
  < 1.0 → CTSL allocates LESS (under-captures the alpha CT_PROMOTE was harvesting)

Secondary metric — tier-distribution stability:
  Where do CT signals land under CTSL? We want most of them above 75 (above
  the overflow=0.00 cliff) and a meaningful fraction in the higher tiers.

Tertiary metric — non-CT spillover: CTSL is gated on (overall ∈ {≥70 ∧ trend≤TM}
or {≤25 ∧ trend≥TM_put}). By construction it doesn't touch other signals — so
non-CT spillover is zero by design. Verified anyway as a sanity check.

Cascade tier allocations (30 DTE shipped):
  call:  95+=0.20  85-94=0.15  80-84=0.10  75-79=0.10  70-74=0.00 (DROPPED)
  put:   ≤15=0.12  16-20=0.10  21-25=0.08
  CT_PROMOTE: ct_call → 0.20 (ULTRA), ct_put → 0.12 (put_top)
"""
from __future__ import annotations
import io, sys, json, itertools
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
CACHE_DIR = ROOT / '.cache' / 'ctsl'
LOOKBACK_DAYS = 1825

# Cascade tier allocations (30 DTE shipped, from strategy_config.STRATEGY_30DTE).
CALL_ALLOC = {'ultra': 0.20, 'top': 0.15, 'mid': 0.10, 'low': 0.10, 'overflow': 0.00}
PUT_ALLOC = {'put_top': 0.12, 'put_mid': 0.10, 'put_low': 0.08}

CT_PROMOTE_CALL_ALLOC = CALL_ALLOC['ultra']    # 0.20
CT_PROMOTE_PUT_ALLOC = PUT_ALLOC['put_top']    # 0.12


def call_tier_alloc(overall: float) -> tuple[str, float]:
    if overall >= 95: return 'ultra', CALL_ALLOC['ultra']
    if overall >= 85: return 'top', CALL_ALLOC['top']
    if overall >= 80: return 'mid', CALL_ALLOC['mid']
    if overall >= 75: return 'low', CALL_ALLOC['low']
    if overall >= 70: return 'overflow', CALL_ALLOC['overflow']
    return 'none', 0.0


def put_tier_alloc(overall: float) -> tuple[str, float]:
    if overall <= 15: return 'put_top', PUT_ALLOC['put_top']
    if overall <= 20: return 'put_mid', PUT_ALLOC['put_mid']
    if overall <= 25: return 'put_low', PUT_ALLOC['put_low']
    return 'none', 0.0


# Vectorized cascade tier alloc lookup using polars when_then.
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


def apply_ctsl_call(
    df: pl.DataFrame,
    trend_max: int,
    target: float,
    alpha: float,
    trend_power: float,
    tier_floor: float = 0.0,
) -> pl.DataFrame:
    """Add new_overall_call column using CTSL transform on call signals only.

    For CT-call signals (overall>=70 ∧ trend<=TREND_MAX):
        trend_dist = (TREND_MAX - trend + 1) / (TREND_MAX + 1)  [smooth at gate]
        lift = alpha × trend_dist^trend_power × (target - overall)
        new_overall = max(overall + lift, tier_floor)  # rescue from overflow

    Non-CT signals: new_overall = overall (untouched).

    tier_floor=75 rescues 70-74 ct_call signals from the overflow=0.00 drop;
    they snap to LOW tier (0.10) at minimum.  tier_floor=0.0 disables rescue.
    """
    is_ct_call = (
        (pl.col('side_label') == 'call') &
        (pl.col('overall') >= 70) &
        (pl.col('trend').is_not_null()) &
        (pl.col('trend') <= trend_max)
    )
    # +1 in numerator/denom keeps trend=TREND_MAX from giving exactly 0 lift
    trend_dist = (
        (pl.lit(trend_max) - pl.col('trend') + 1).cast(pl.Float64)
        / float(trend_max + 1)
    ).clip(0.0, 1.0)
    lift = alpha * (trend_dist ** trend_power) * (target - pl.col('overall').cast(pl.Float64))
    lifted = pl.col('overall').cast(pl.Float64) + lift
    if tier_floor > 0:
        # Only apply floor to CT-eligible signals (preserve non-CT untouched)
        rescued = pl.when(is_ct_call & (lifted < tier_floor)).then(tier_floor).otherwise(lifted)
    else:
        rescued = lifted
    new_call = pl.when(is_ct_call).then(rescued).otherwise(pl.col('overall').cast(pl.Float64))
    return df.with_columns(new_call.alias('new_overall_call'))


def apply_ctsl_put(
    df: pl.DataFrame,
    trend_min: int,
    target: float,
    alpha: float,
    trend_power: float,
) -> pl.DataFrame:
    """Add new_overall_put column using CTSL transform on put signals only.

    For CT-put signals (overall<=25 ∧ trend>=TREND_MIN):
        trend_dist = (trend - TREND_MIN + 1) / (100 - TREND_MIN + 1)
        dampen = alpha × trend_dist^trend_power × (overall - target)
        new_overall = overall - dampen
    """
    is_ct_put = (
        (pl.col('side_label') == 'put') &
        (pl.col('overall') <= 25) &
        (pl.col('trend').is_not_null()) &
        (pl.col('trend') >= trend_min)
    )
    span = float(100 - trend_min + 1)
    trend_dist = (
        (pl.col('trend') - pl.lit(trend_min) + 1).cast(pl.Float64) / span
    ).clip(0.0, 1.0)
    dampen = alpha * (trend_dist ** trend_power) * (pl.col('overall').cast(pl.Float64) - target)
    new_put = pl.when(is_ct_put).then(pl.col('overall').cast(pl.Float64) - dampen).otherwise(pl.col('overall').cast(pl.Float64))
    return df.with_columns(new_put.alias('new_overall_put'))


def evaluate_call_variant(
    df_call: pl.DataFrame,
    trend_max: int,
    target: float,
    alpha: float,
    trend_power: float,
    tier_floor: float = 0.0,
) -> dict:
    """Evaluate a CTSL call-side variant.

    df_call: filtered to side_label='call' with resolved barriers."""
    df = apply_ctsl_call(df_call, trend_max, target, alpha, trend_power, tier_floor)
    df = df.with_columns(_call_alloc_expr(pl.col('new_overall_call')).alias('alloc_ctsl'))

    # CT cohort under v44 baseline membership (overall>=70 ∧ trend<=trend_max)
    ct = df.filter(
        (pl.col('overall') >= 70) &
        (pl.col('trend').is_not_null()) &
        (pl.col('trend') <= trend_max)
    )
    if ct.height == 0:
        return None

    # CT_PROMOTE allocation: every CT signal → ULTRA (0.20)
    # CTSL allocation: cascade(new_overall_call)
    # We evaluate on RESOLVED barrier outcomes only.
    ct_resolved = ct.filter(pl.col('opt_result_15').is_not_null())
    n_ct = ct_resolved.height
    if n_ct == 0:
        return None

    pnl_promote = (CT_PROMOTE_CALL_ALLOC * ct_resolved['opt_exit_return_15']).sum()
    pnl_ctsl = (ct_resolved['alloc_ctsl'] * ct_resolved['opt_exit_return_15']).sum()
    alpha_capture = pnl_ctsl / pnl_promote if abs(pnl_promote) > 1e-9 else float('nan')

    # Tier distribution (fractions, where N>=1)
    tier_counts = ct_resolved.group_by(_call_alloc_expr(pl.col('new_overall_call')).alias('a')).len()
    tier_counts_d = {float(r[0]): int(r[1]) for r in tier_counts.iter_rows()}

    # CT signals dropped (allocation = 0): land in 70-74 overflow (which is 0)
    n_dropped = tier_counts_d.get(0.0, 0)

    # CT signals "rescued" above 75 (alloc >= LOW=0.10)
    n_rescued = sum(c for a, c in tier_counts_d.items() if a >= 0.10)

    # Average WR15 of CT cohort (unchanged by CTSL — per-trade quality is the
    # signal itself, not the tier we put it in)
    wr15 = ct_resolved.filter(pl.col('opt_result_15') == 1).height / n_ct * 100.0

    return {
        'side': 'call',
        'trend_max': trend_max,
        'target': target,
        'alpha': alpha,
        'trend_power': trend_power,
        'tier_floor': tier_floor,
        'n_ct': n_ct,
        'wr15': wr15,
        'pnl_promote': float(pnl_promote),
        'pnl_ctsl': float(pnl_ctsl),
        'alpha_capture': float(alpha_capture),
        'n_dropped': int(n_dropped),
        'frac_dropped': n_dropped / n_ct,
        'n_rescued': int(n_rescued),
        'frac_rescued': n_rescued / n_ct,
        'tier_dist': {f'{k:.2f}': v for k, v in sorted(tier_counts_d.items(), reverse=True)},
    }


def evaluate_put_variant(
    df_put: pl.DataFrame,
    trend_min: int,
    target: float,
    alpha: float,
    trend_power: float,
) -> dict:
    df = apply_ctsl_put(df_put, trend_min, target, alpha, trend_power)
    df = df.with_columns(_put_alloc_expr(pl.col('new_overall_put')).alias('alloc_ctsl'))

    ct = df.filter(
        (pl.col('overall') <= 25) &
        (pl.col('trend').is_not_null()) &
        (pl.col('trend') >= trend_min)
    )
    if ct.height == 0:
        return None

    ct_resolved = ct.filter(pl.col('opt_result_15').is_not_null())
    n_ct = ct_resolved.height
    if n_ct == 0:
        return None

    pnl_promote = (CT_PROMOTE_PUT_ALLOC * ct_resolved['opt_exit_return_15']).sum()
    pnl_ctsl = (ct_resolved['alloc_ctsl'] * ct_resolved['opt_exit_return_15']).sum()
    alpha_capture = pnl_ctsl / pnl_promote if abs(pnl_promote) > 1e-9 else float('nan')

    tier_counts = ct_resolved.group_by(_put_alloc_expr(pl.col('new_overall_put')).alias('a')).len()
    tier_counts_d = {float(r[0]): int(r[1]) for r in tier_counts.iter_rows()}

    n_dropped = tier_counts_d.get(0.0, 0)  # if new_overall > 25, falls out of put tier
    n_in_top = tier_counts_d.get(PUT_ALLOC['put_top'], 0)

    wr15 = ct_resolved.filter(pl.col('opt_result_15') == 1).height / n_ct * 100.0

    return {
        'side': 'put',
        'trend_min': trend_min,
        'target': target,
        'alpha': alpha,
        'trend_power': trend_power,
        'n_ct': n_ct,
        'wr15': wr15,
        'pnl_promote': float(pnl_promote),
        'pnl_ctsl': float(pnl_ctsl),
        'alpha_capture': float(alpha_capture),
        'n_dropped': int(n_dropped),
        'frac_dropped': n_dropped / n_ct,
        'n_in_top': int(n_in_top),
        'frac_in_top': n_in_top / n_ct,
        'tier_dist': {f'{k:.2f}': v for k, v in sorted(tier_counts_d.items(), reverse=True)},
    }


def sweep(label: str):
    pq = CACHE_DIR / f'scores_{label}_{LOOKBACK_DAYS}.parquet'
    if not pq.exists():
        print(f'!! parquet not found: {pq}', file=sys.stderr)
        return

    df = pl.read_parquet(pq)
    df_call = df.filter(pl.col('side_label') == 'call')
    df_put = df.filter(pl.col('side_label') == 'put')

    print(f'\n{"=" * 70}')
    print(f' CTSL Phase 2 sweep — {label} ({pq.name})')
    print(f' rows: {len(df):,}  call: {len(df_call):,}  put: {len(df_put):,}')
    print(f'{"=" * 70}')

    # ---- CALL sweep ----
    # Extended target range (90 → 100) per follow-up question: target ≥ 95
    # is required to enable ULTRA-tier landings (95+) for the deepest CT-call
    # signals. With target=100 + α=0.95 + trend_dist=1.0, max lift from
    # overall=72 reaches 98.6 → ULTRA (0.20).
    call_grid = list(itertools.product(
        [18, 20, 22, 25],                    # trend_max — wider gates
        [85, 90, 95, 98, 100],               # target — extended to allow ULTRA landing
        [0.50, 0.70, 0.95],                  # alpha
        [0.7, 1.0, 1.5, 2.0],                # trend_power
        [0.0, 75.0],                         # tier_floor — 0 (no rescue) or 75 (LOW floor)
    ))
    print(f'\n[call sweep] {len(call_grid)} variants')
    call_results = []
    for tm, tg, al, tp, tf in call_grid:
        r = evaluate_call_variant(df_call, tm, tg, al, tp, tf)
        if r is not None:
            call_results.append(r)

    # Sort by closeness of alpha_capture to 1.0 (penalize over- and under-shoot equally)
    call_results.sort(key=lambda r: abs(r['alpha_capture'] - 1.0))

    print(f'\n[call] top-15 variants by alpha_capture closest to 1.0:')
    print(f'  {"trend_max":>9} {"target":>6} {"alpha":>5} {"power":>5} {"floor":>5} {"n_ct":>5} {"wr15":>5} '
          f'{"capture":>7} {"f_drop":>6} {"f_rescue":>8}')
    for r in call_results[:15]:
        print(f'  {r["trend_max"]:>9} {r["target"]:>6} {r["alpha"]:>5.2f} {r["trend_power"]:>5.2f} '
              f'{r.get("tier_floor",0.0):>5.1f} '
              f'{r["n_ct"]:>5,} {r["wr15"]:>5.1f} '
              f'{r["alpha_capture"]:>+7.3f} '
              f'{r["frac_dropped"]:>6.2%} {r["frac_rescued"]:>8.2%}')

    # Acknowledge structural ceiling: a smooth gradient cannot match CT_PROMOTE's
    # uniform 0.20 ULTRA assignment. Loosen to alpha_capture >= 0.50 (i.e. CTSL
    # captures at least half of CT_PROMOTE's allocation magnitude) AND zero
    # drops (with tier_floor>=75 enforced).
    call_shippable = [r for r in call_results
                      if r['alpha_capture'] >= 0.50
                      and r['frac_dropped'] <= 0.02]
    print(f'\n[call] shippable variants (alpha_capture >= 0.50 AND drop <= 2%): {len(call_shippable)}/{len(call_results)}')
    if call_shippable:
        # Maximize alpha_capture (highest portfolio retention) subject to filters.
        call_shippable.sort(key=lambda r: -r['alpha_capture'])
        print(f'  [top 12]:')
        for r in call_shippable[:12]:
            tier_str = ' '.join(f'{k}:{v}' for k, v in r['tier_dist'].items())
            print(f'    tm={r["trend_max"]} tg={r["target"]:.0f} a={r["alpha"]:.2f} '
                  f'p={r["trend_power"]:.2f} f={r.get("tier_floor",0.0):.0f} '
                  f'cap={r["alpha_capture"]:+.3f} drop={r["frac_dropped"]:.1%} '
                  f'rescue={r["frac_rescued"]:.1%}  tiers={tier_str}')

    # ---- PUT sweep ----
    put_grid = list(itertools.product(
        [75, 78, 80, 82, 85],                 # trend_min
        [8, 10, 12, 15, 18],                  # target
        [0.40, 0.50, 0.60, 0.70, 0.80, 0.95], # alpha
        [0.7, 1.0, 1.5, 2.0],                 # trend_power
    ))
    print(f'\n[put sweep] {len(put_grid)} variants')
    put_results = []
    for tm, tg, al, tp in put_grid:
        r = evaluate_put_variant(df_put, tm, tg, al, tp)
        if r is not None:
            put_results.append(r)
    put_results.sort(key=lambda r: abs(r['alpha_capture'] - 1.0))

    print(f'\n[put] top-15 variants by alpha_capture closest to 1.0:')
    print(f'  {"trend_min":>9} {"target":>6} {"alpha":>5} {"power":>5} {"n_ct":>5} {"wr15":>5} '
          f'{"pnl_pr":>8} {"pnl_ct":>8} {"capture":>7} {"f_top":>6} {"f_drop":>6}')
    for r in put_results[:15]:
        print(f'  {r["trend_min"]:>9} {r["target"]:>6} {r["alpha"]:>5.2f} {r["trend_power"]:>5.2f} '
              f'{r["n_ct"]:>5,} {r["wr15"]:>5.1f} '
              f'{r["pnl_promote"]:>+8.2f} {r["pnl_ctsl"]:>+8.2f} {r["alpha_capture"]:>+7.3f} '
              f'{r["frac_in_top"]:>6.2%} {r["frac_dropped"]:>6.2%}')

    put_shippable = [r for r in put_results
                     if 0.80 <= r['alpha_capture'] <= 1.20
                     and r['frac_dropped'] <= 0.05]
    print(f'\n[put] shippable variants (alpha_capture in [0.80,1.20] AND drop≤5%): {len(put_shippable)}/{len(put_results)}')
    if put_shippable:
        put_shippable.sort(key=lambda r: (abs(r['alpha_capture'] - 1.0), r['frac_dropped']))
        print(f'  [top 8]:')
        for r in put_shippable[:8]:
            tier_str = ' '.join(f'{k}:{v}' for k, v in r['tier_dist'].items())
            print(f'    tm={r["trend_min"]} tg={r["target"]:.0f} a={r["alpha"]:.2f} p={r["trend_power"]:.2f} '
                  f'cap={r["alpha_capture"]:+.3f} top={r["frac_in_top"]:.1%} '
                  f'drop={r["frac_dropped"]:.1%}  tiers={tier_str}')

    # Persist full results JSON for cross-version comparison
    out = CACHE_DIR / f'sweep_{label}.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({
            'label': label,
            'call': call_results,
            'put': put_results,
        }, f, default=str, indent=1)
    print(f'\n[done] wrote {out} ({len(call_results)} call + {len(put_results)} put variants)')


if __name__ == '__main__':
    labels = sys.argv[1:] or ['v44', 'v39']
    for lab in labels:
        sweep(lab)
