"""WVD — Weekly Volume Dampener sweep.

Post-hoc dampener applied AFTER v44 ICH. Operates on (overall, weekly-volume features)
to dampen calls with single-week vol climax (wv_force1) and lift weak puts with
distribution-divergence (wv_cmf4).

Smooth-gradient v44-ICH-style architecture:

  CALL side (dampener):
    score_norm  = max(0, (overall - GATE_LO) / (GATE_HI - GATE_LO))
    K_eff       = K_CALL * score_norm ** K_CALL_POWER
    force_grad  = tanh(max(0, wv_force1) / FORCE_SAT)
    overall   -= K_eff * force_grad * (overall - TARGET_CALL)

  PUT side (lift OUT of put zone):
    score_grad  = max(0, (PUT_HI - overall) / (PUT_HI - PUT_LO))
    K_eff       = K_PUT * score_grad ** K_PUT_POWER
    cmf_grad    = tanh(max(0, -wv_cmf4) / CMF_SAT)
    overall   += K_eff * cmf_grad * (TARGET_PUT - overall)

Apply order: AFTER v44 ICH (already baked into parquet 'overall' column),
BEFORE PESS / EARN_BOOST. Since the parquet 'overall' is the FINAL v44 value
post-ICH-post-PESS-post-boost, this sweep represents a hypothetical NEW
algorithm v45 = v44 + WVD.

Caveat — the parquet 'overall' is post-EARN_BOOST. A real production
implementation would apply WVD BEFORE EARN_BOOST so the boost can re-amplify
displaced signals. For per-trade gate evaluation this approximation is
acceptable: signals dampened OUT of the qualifying universe (75+) by WVD
won't be earnings-boosted anyway. Some 70-74 signals that should boost UP
into 75+ may be slightly miscounted but the magnitude is small (EARN_BOOST
only fires within d≤5 of earnings).
"""
from __future__ import annotations
import io, sys, time, json
from pathlib import Path
from itertools import product

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl
import numpy as np

import os
_VID = os.environ.get('WVD_VID', '45')
CALLS_PARQ = ROOT / '.cache' / 'weekly_volume' / f'calls_v{_VID}_1825.parquet'
PUTS_PARQ  = ROOT / '.cache' / 'weekly_volume' / f'puts_v{_VID}_1825.parquet'

CALL_TIERS = [70, 75, 80, 85, 90, 95]
PUT_TIERS  = [30, 25, 20, 15, 10]


def apply_wvd_calls(df: pl.DataFrame, gate_lo, gate_hi, k_call, k_power, force_sat, target_call):
    """Apply call-side WVD dampener. Smooth gradient on positive wv_force1."""
    if k_call <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))
    has_force = pl.col('wv_force1').is_not_null()
    force_pos = pl.col('wv_force1').clip(lower_bound=0)
    # tanh ramp via approximation: tanh(x) = (exp(2x)-1) / (exp(2x)+1)
    # polars doesn't have tanh; use 2/(1+exp(-2x)) - 1
    x = (force_pos / force_sat) * 2.0
    force_grad = (2.0 / (1.0 + (-x).exp())) - 1.0   # tanh(force_pos / force_sat)
    score_range = max(1, gate_hi - gate_lo)
    score_raw = ((pl.col('overall') - gate_lo) / score_range).clip(0.0, None)  # no upper clip
    score_factor = score_raw.pow(k_power)
    k_eff = k_call * score_factor
    weakness = k_eff * force_grad
    in_zone = (pl.col('overall') >= gate_lo) & has_force
    return df.with_columns(
        pl.when(in_zone)
          .then(pl.col('overall') - weakness * (pl.col('overall') - target_call))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )


def apply_wvd_puts(df: pl.DataFrame, put_lo, put_hi, k_put, k_power, cmf_sat, target_put):
    """Apply put-side WVD lift. Smooth gradient on negative wv_cmf4 (distribution-divergence)."""
    if k_put <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))
    has_cmf = pl.col('wv_cmf4').is_not_null()
    cmf_neg = (-pl.col('wv_cmf4')).clip(lower_bound=0)
    x = (cmf_neg / cmf_sat) * 2.0
    cmf_grad = (2.0 / (1.0 + (-x).exp())) - 1.0   # tanh(-wv_cmf4 / cmf_sat)
    put_range = max(1, put_hi - put_lo)
    score_raw = ((put_hi - pl.col('overall')) / put_range).clip(0.0, 1.0)
    score_factor = score_raw.pow(k_power)
    k_eff = k_put * score_factor
    weakness = k_eff * cmf_grad
    in_zone = (pl.col('overall') <= put_hi) & has_cmf
    return df.with_columns(
        pl.when(in_zone)
          .then(pl.col('overall') + weakness * (target_put - pl.col('overall')))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )


def cumulative_wr_n(df: pl.DataFrame, side: str, score_col: str, tier: int):
    if side == 'call':
        sub = df.filter(pl.col(score_col) >= tier)
    else:
        sub = df.filter(pl.col(score_col) <= tier)
    sub = sub.filter(pl.col('opt_result_15').is_not_null())
    n = sub.height
    if n == 0:
        return None, 0
    wr = sub.select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item() * 100
    return wr, n


def evaluate_call_variant(calls_df, params):
    """Apply WVD-call to calls parquet, compute per-tier WR + N."""
    new_df = apply_wvd_calls(
        calls_df,
        params['gate_lo'], params['gate_hi'],
        params['k_call'], params['k_power'],
        params['force_sat'], params['target_call'],
    )
    out = {'name': params.get('name', '?'), 'side': 'call'}
    for tier in CALL_TIERS:
        base_wr, base_n = cumulative_wr_n(calls_df, 'call', 'overall', tier)
        new_wr,  new_n  = cumulative_wr_n(new_df, 'call', 'new_overall', tier)
        out[f'+{tier}_base_wr'] = base_wr
        out[f'+{tier}_base_n']  = base_n
        out[f'+{tier}_new_wr']  = new_wr
        out[f'+{tier}_new_n']   = new_n
        out[f'+{tier}_d_wr']    = (new_wr - base_wr) if (new_wr is not None and base_wr is not None) else None
        out[f'+{tier}_d_n']     = ((new_n - base_n) / base_n * 100) if base_n else None
    return out


def evaluate_put_variant(puts_df, params):
    """Apply WVD-put to puts parquet."""
    new_df = apply_wvd_puts(
        puts_df,
        params['put_lo'], params['put_hi'],
        params['k_put'], params['k_put_power'],
        params['cmf_sat'], params['target_put'],
    )
    out = {'name': params.get('name', '?'), 'side': 'put'}
    for tier in PUT_TIERS:
        base_wr, base_n = cumulative_wr_n(puts_df, 'put', 'overall', tier)
        new_wr,  new_n  = cumulative_wr_n(new_df, 'put', 'new_overall', tier)
        out[f'<{tier}_base_wr'] = base_wr
        out[f'<{tier}_base_n']  = base_n
        out[f'<{tier}_new_wr']  = new_wr
        out[f'<{tier}_new_n']   = new_n
        out[f'<{tier}_d_wr']    = (new_wr - base_wr) if (new_wr is not None and base_wr is not None) else None
        out[f'<{tier}_d_n']     = ((new_n - base_n) / base_n * 100) if base_n else None
    return out


def print_call_variant(r, w=14):
    print(f"  {r['name']:<24s}", end='')
    for t in [75, 80, 85, 90, 95]:
        d_wr = r.get(f'+{t}_d_wr')
        d_n  = r.get(f'+{t}_d_n')
        new_n = r.get(f'+{t}_new_n')
        if d_wr is None:
            print('   N/A     ', end='')
        else:
            tag = ' '
            if abs(d_wr) >= 0.5:
                tag = '*' if d_wr > 0 else '!'
            print(f'  {d_wr:>+5.2f}/{d_n:>+5.1f}%{tag}', end='')
    print()


def print_put_variant(r):
    print(f"  {r['name']:<24s}", end='')
    for t in [25, 20, 15, 10]:
        d_wr = r.get(f'<{t}_d_wr')
        d_n  = r.get(f'<{t}_d_n')
        if d_wr is None:
            print('   N/A     ', end='')
        else:
            tag = ' '
            if abs(d_wr) >= 0.5:
                tag = '*' if d_wr > 0 else '!'
            print(f'  {d_wr:>+5.2f}/{d_n:>+5.1f}%{tag}', end='')
    print()


def main():
    print('WVD — Weekly Volume Dampener Sweep  (v44 baseline parquet, 30dte_opt @ w=15)\n')

    calls = pl.read_parquet(CALLS_PARQ).filter(pl.col('opt_result_15').is_not_null())
    puts  = pl.read_parquet(PUTS_PARQ).filter(pl.col('opt_result_15').is_not_null())
    print(f'[setup] resolved peaks: calls={len(calls):,}  puts={len(puts):,}\n')

    # ── CALL SIDE: sweep K, POWER, FORCE_SAT, TARGET ──
    print('=' * 130)
    print('  CALL SWEEP — dampen 75+ on positive wv_force1 (single-week vol climax)')
    print('=' * 130)
    print(f'  {"variant":<24s}  {"75+ ΔWR/ΔN":<14s}  {"80+ ΔWR/ΔN":<14s}  {"85+ ΔWR/ΔN":<14s}  {"90+ ΔWR/ΔN":<14s}  {"95+ ΔWR/ΔN":<14s}')
    print('  ' + '-' * 122)

    call_baseline = {
        'name': 'baseline (v44)', 'gate_lo': 70, 'gate_hi': 90, 'k_call': 0.0,
        'k_power': 1.0, 'force_sat': 0.10, 'target_call': 60.0,
    }
    base_r = evaluate_call_variant(calls, call_baseline)
    print_call_variant(base_r)
    print('  ' + '-' * 122)

    # Coarse grid
    call_results = []
    grid_call = list(product(
        [70, 75],                       # gate_lo
        [85, 90, 95],                   # gate_hi
        [0.20, 0.30, 0.40, 0.50],        # k_call
        [1.0, 1.5, 2.0, 2.5],           # k_power
        [0.05, 0.08, 0.10, 0.15],        # force_sat
        [55, 60, 62, 65],               # target_call
    ))
    print(f'  [grid] {len(grid_call)} variants', flush=True)
    for (glo, ghi, kc, kp, fsat, tg) in grid_call:
        if glo >= ghi:
            continue
        params = {
            'name': f'gl{glo}_gh{ghi}_k{kc:.2f}_p{kp:.1f}_fs{fsat:.2f}_t{tg}',
            'gate_lo': glo, 'gate_hi': ghi, 'k_call': kc,
            'k_power': kp, 'force_sat': fsat, 'target_call': tg,
        }
        r = evaluate_call_variant(calls, params)
        # Composite score: weighted ΔWR sum across 75+/80+/85+ minus N drop penalty
        wr_score = 0.0
        n_pen = 0.0
        for tier, w in [(75, 1.0), (80, 0.8), (85, 0.7)]:
            d_wr = r.get(f'+{tier}_d_wr') or 0.0
            d_n = r.get(f'+{tier}_d_n') or 0.0
            wr_score += w * d_wr
            n_pen += abs(d_n) * 0.02   # 0.02pp per 1% N drop
        # Penalty for primary tier N drop > 25%
        n75_drop = -(r.get('+75_d_n') or 0)
        if n75_drop > 25:
            n_pen += (n75_drop - 25) * 0.5
        # Hard penalty if ANY tier regresses by more than 1.0pp
        max_neg = min(r.get(f'+{t}_d_wr') or 0.0 for t in [75, 80, 85, 90])
        if max_neg < -1.0:
            n_pen += (-max_neg - 1.0) * 5
        composite = wr_score - n_pen
        r['composite'] = composite
        r['n_pen'] = n_pen
        r['wr_score'] = wr_score
        r['params'] = params
        call_results.append(r)

    # Sort by composite
    call_results.sort(key=lambda r: -r.get('composite', -999))
    print(f'  [top 15 call variants by composite]')
    for r in call_results[:15]:
        print_call_variant(r)
        print(f'    [composite={r["composite"]:+.2f} = wr_score{r["wr_score"]:+.2f} - n_pen{r["n_pen"]:.2f}]')

    # ── PUT SIDE ──
    print('\n' + '=' * 130)
    print('  PUT SWEEP — lift weak puts on negative wv_cmf4 (distribution-divergence)')
    print('=' * 130)
    print(f'  {"variant":<24s}  {"<25 ΔWR/ΔN":<14s}  {"<20 ΔWR/ΔN":<14s}  {"<15 ΔWR/ΔN":<14s}  {"<10 ΔWR/ΔN":<14s}')
    print('  ' + '-' * 100)

    put_baseline = {
        'name': 'baseline (v44)', 'put_lo': 5, 'put_hi': 25, 'k_put': 0.0,
        'k_put_power': 1.0, 'cmf_sat': 0.30, 'target_put': 27.0,
    }
    base_pr = evaluate_put_variant(puts, put_baseline)
    print_put_variant(base_pr)
    print('  ' + '-' * 100)

    put_results = []
    grid_put = list(product(
        [5, 10],                        # put_lo
        [20, 22, 25],                   # put_hi
        [0.20, 0.30, 0.40, 0.50],        # k_put
        [1.0, 1.5, 2.0],                 # k_put_power
        [0.20, 0.30, 0.40],              # cmf_sat
        [27, 30, 33],                    # target_put
    ))
    print(f'  [grid] {len(grid_put)} variants', flush=True)
    for (plo, phi, kp_, kpp, csat, tg) in grid_put:
        if plo >= phi:
            continue
        params = {
            'name': f'pl{plo}_ph{phi}_k{kp_:.2f}_p{kpp:.1f}_cs{csat:.2f}_t{tg}',
            'put_lo': plo, 'put_hi': phi, 'k_put': kp_,
            'k_put_power': kpp, 'cmf_sat': csat, 'target_put': tg,
        }
        r = evaluate_put_variant(puts, params)
        # Composite: weighted ΔWR across <25/<20/<15 minus N drop penalty
        wr_score = 0.0
        n_pen = 0.0
        for tier, w in [(25, 1.0), (20, 0.8), (15, 0.6)]:
            d_wr = r.get(f'<{tier}_d_wr') or 0.0
            d_n = r.get(f'<{tier}_d_n') or 0.0
            wr_score += w * d_wr
            n_pen += abs(d_n) * 0.02
        # N drop penalty
        n25_drop = -(r.get('<25_d_n') or 0)
        if n25_drop > 25:
            n_pen += (n25_drop - 25) * 0.5
        max_neg = min(r.get(f'<{t}_d_wr') or 0.0 for t in [25, 20, 15])
        if max_neg < -1.0:
            n_pen += (-max_neg - 1.0) * 5
        composite = wr_score - n_pen
        r['composite'] = composite
        r['n_pen'] = n_pen
        r['wr_score'] = wr_score
        r['params'] = params
        put_results.append(r)

    put_results.sort(key=lambda r: -r.get('composite', -999))
    print(f'  [top 15 put variants by composite]')
    for r in put_results[:15]:
        print_put_variant(r)
        print(f'    [composite={r["composite"]:+.2f} = wr_score{r["wr_score"]:+.2f} - n_pen{r["n_pen"]:.2f}]')

    # ── Save full sweep ──
    out_dir = Path(__file__).parent
    with open(out_dir / 'sweep_calls.jsonl', 'w') as f:
        for r in call_results:
            f.write(json.dumps(r, default=str) + '\n')
    with open(out_dir / 'sweep_puts.jsonl', 'w') as f:
        for r in put_results:
            f.write(json.dumps(r, default=str) + '\n')
    print(f'\n[done] wrote sweep_calls.jsonl ({len(call_results)} variants)')
    print(f'[done] wrote sweep_puts.jsonl  ({len(put_results)} variants)')

    # ── Print top 3 with full numbers ──
    print('\n' + '=' * 130)
    print('  TOP-3 CALL VARIANTS — full per-tier breakdown')
    print('=' * 130)
    for r in call_results[:3]:
        print(f"\n  [{r['name']}]")
        for t in CALL_TIERS:
            base_wr = r.get(f'+{t}_base_wr')
            new_wr = r.get(f'+{t}_new_wr')
            base_n = r.get(f'+{t}_base_n')
            new_n  = r.get(f'+{t}_new_n')
            d_wr = r.get(f'+{t}_d_wr')
            d_n  = r.get(f'+{t}_d_n')
            if base_wr is not None and new_wr is not None:
                print(f'    +{t}: base WR={base_wr:>5.2f}% (N={base_n:>5d})  →  new WR={new_wr:>5.2f}% (N={new_n:>5d})    ΔWR={d_wr:>+5.2f}pp  ΔN={d_n:>+5.1f}%')

    print('\n' + '=' * 130)
    print('  TOP-3 PUT VARIANTS — full per-tier breakdown')
    print('=' * 130)
    for r in put_results[:3]:
        print(f"\n  [{r['name']}]")
        for t in PUT_TIERS:
            base_wr = r.get(f'<{t}_base_wr')
            new_wr = r.get(f'<{t}_new_wr')
            base_n = r.get(f'<{t}_base_n')
            new_n  = r.get(f'<{t}_new_n')
            d_wr = r.get(f'<{t}_d_wr')
            d_n  = r.get(f'<{t}_d_n')
            if base_wr is not None and new_wr is not None:
                print(f'    <{t}: base WR={base_wr:>5.2f}% (N={base_n:>5d})  →  new WR={new_wr:>5.2f}% (N={new_n:>5d})    ΔWR={d_wr:>+5.2f}pp  ΔN={d_n:>+5.1f}%')


if __name__ == '__main__':
    main()
