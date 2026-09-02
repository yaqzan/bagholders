"""WVD-Wave — score-stage inverted-U modulator on wv_force1.

Replaces the dampener architecture (which failed portfolio MC) with a smooth
score-stage gradient that captures the FULL cohort signal:

  Cohort data on calls 75+ (v45):
    Q1 (force < -0.05): WR15 = 65.2%  (z=-0.3, mild lag)
    Q3 (force ~  0):    WR15 = 75.0%  (z=+3.26, +8.96pp PEAK)
    Q5 (force > +0.06): WR15 = 60.7%  (z=-1.9, climax-bad)

  Inverted-U: best at moderate, worst at extreme. Mathematical form:

    bell_lift   = exp(-((force - PEAK)/WIDTH)^2)         # Gaussian peak
    excess      = max(0, force - CLIMAX_THRESH)
    dampen_grad = tanh(excess / CLIMAX_SAT)              # Climax dampen
    score_norm  = clip((overall - GATE_LO)/(GATE_HI - GATE_LO), 0, 1)
    score_w     = score_norm ** SCORE_POWER

    overall += K_LIFT   * score_w * bell_lift   * (TARGET_LIFT   - overall)
    overall -= K_DAMPEN * score_w * dampen_grad * (overall - TARGET_DAMPEN)

Pure score-stage (no labels, no tier overrides). Architecturally consistent
with v37 PCD / v38 CWWD / v39 PESS / v43 MCD / v44 ICH.

Sweep ranks by composite ΔWR15 across primary call tiers minus N-drop penalty.
"""
from __future__ import annotations
import io, sys, os, time, json
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

_VID = os.environ.get('WVD_VID', '45')
CALLS_PARQ = ROOT / '.cache' / 'weekly_volume' / f'calls_v{_VID}_1825.parquet'

CALL_TIERS = [70, 75, 80, 85, 90, 95]


def apply_wave_modulator(df, gate_lo, gate_hi, score_power,
                          peak, width, k_lift, target_lift,
                          climax_thresh, climax_sat, k_dampen, target_dampen):
    """Apply bell-lift + tanh-dampen wave modulator to overall scores."""
    has_force = pl.col('wv_force1').is_not_null()

    # Bell lift: Gaussian centered at peak, std=width
    delta = pl.col('wv_force1') - peak
    bell_arg = (delta / width) ** 2
    # exp(-bell_arg) — but polars only has exp on float columns
    bell_lift = (-bell_arg).exp()

    # Climax dampen: tanh(excess / sat)
    excess = (pl.col('wv_force1') - climax_thresh).clip(lower_bound=0.0)
    x = (excess / climax_sat) * 2.0
    dampen_grad = (2.0 / (1.0 + (-x).exp())) - 1.0

    # Score-zone weight
    score_range = max(1, gate_hi - gate_lo)
    score_norm = ((pl.col('overall') - gate_lo) / score_range).clip(0.0, 1.0)
    score_w = score_norm.pow(score_power)

    in_zone = (pl.col('overall') >= gate_lo) & has_force

    # Apply lift then dampen (additive both)
    lift_amt = k_lift * score_w * bell_lift * (target_lift - pl.col('overall'))
    dampen_amt = k_dampen * score_w * dampen_grad * (pl.col('overall') - target_dampen)

    return df.with_columns(
        pl.when(in_zone)
          .then(pl.col('overall') + lift_amt - dampen_amt)
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )


def cum_wr_n(df, score_col, tier, result_col='opt_result_15'):
    sub = df.filter(pl.col(score_col) >= tier).filter(pl.col(result_col).is_not_null())
    n = sub.height
    if n == 0:
        return None, 0
    wr = sub.select((pl.col(result_col) == 1).cast(pl.Float64).mean()).item() * 100
    return wr, n


def evaluate_variant(df, params):
    new_df = apply_wave_modulator(df,
        params['gate_lo'], params['gate_hi'], params['score_power'],
        params['peak'], params['width'], params['k_lift'], params['target_lift'],
        params['climax_thresh'], params['climax_sat'], params['k_dampen'], params['target_dampen'],
    )
    out = {}
    for t in CALL_TIERS:
        b_wr, b_n = cum_wr_n(df, 'overall', t)
        n_wr, n_n = cum_wr_n(new_df, 'new_overall', t)
        out[t] = {
            'b_wr': b_wr, 'n_wr': n_wr, 'b_n': b_n, 'n_n': n_n,
            'd_wr': (n_wr - b_wr) if (n_wr is not None and b_wr is not None) else None,
            'd_n_pct': ((n_n - b_n) / b_n * 100) if b_n else None,
        }
    return out


def composite_score(results, params):
    """Composite ranking metric — rewards ΔWR with tier weights, penalizes N drift."""
    wr_score = 0.0
    n_pen = 0.0
    weights = {75: 1.0, 80: 0.9, 85: 0.8, 90: 0.6}
    for tier, w in weights.items():
        r = results.get(tier, {})
        d_wr = r.get('d_wr') or 0.0
        d_n = r.get('d_n_pct') or 0.0
        wr_score += w * d_wr
        n_pen += abs(d_n) * 0.015   # gentler N penalty than dampener-only sweep
    # Hard penalty if any primary tier regresses by >0.5pp
    max_neg = min(results.get(t, {}).get('d_wr') or 0.0 for t in [75, 80, 85, 90])
    if max_neg < -0.5:
        n_pen += (-max_neg - 0.5) * 8
    # Hard penalty if 75+ N drops > 25%
    n75_drop = -(results.get(75, {}).get('d_n_pct') or 0)
    if n75_drop > 25:
        n_pen += (n75_drop - 25) * 0.5
    return wr_score - n_pen, wr_score, n_pen


def main():
    print('WVD-Wave Sweep — score-stage inverted-U modulator on wv_force1')
    print(f'  Baseline: v{_VID}, 30dte_opt @ w=15, 5y\n')

    df = pl.read_parquet(CALLS_PARQ).filter(pl.col('opt_result_15').is_not_null())
    print(f'[setup] resolved call peaks: {len(df):,}')
    base = {}
    for t in CALL_TIERS:
        wr, n = cum_wr_n(df, 'overall', t)
        base[t] = (wr, n)
        print(f'  baseline +{t}: WR15={wr:>5.2f}% N={n:>6d}')
    print()

    # ── Sweep grid ──
    grid = list(product(
        [70],                              # gate_lo
        [85, 90],                          # gate_hi
        [1.0, 1.5],                        # score_power
        [0.0],                             # peak (Q3 cohort centroid)
        [0.04, 0.06, 0.08, 0.12],           # width (Gaussian decay)
        [0.10, 0.15, 0.20, 0.25],           # k_lift
        [82, 85, 88, 92],                   # target_lift
        [0.05, 0.08, 0.12],                # climax_thresh (Q5 lower edge ~+0.06)
        [0.15],                            # climax_sat (fixed)
        [0.20, 0.30, 0.40],                # k_dampen
        [55, 60],                          # target_dampen
    ))
    grid = [g for g in grid if g[1] > g[0] and g[6] >= 80]
    print(f'[sweep] {len(grid):,} variants', flush=True)

    results = []
    t0 = time.time()
    for i, (glo, ghi, sp, pk, w, klft, tlft, cth, csat, kdmp, tdmp) in enumerate(grid):
        params = {
            'gate_lo': glo, 'gate_hi': ghi, 'score_power': sp,
            'peak': pk, 'width': w, 'k_lift': klft, 'target_lift': tlft,
            'climax_thresh': cth, 'climax_sat': csat,
            'k_dampen': kdmp, 'target_dampen': tdmp,
        }
        res = evaluate_variant(df, params)
        composite, wr_s, n_p = composite_score(res, params)
        results.append({'params': params, 'tiers': res,
                         'composite': composite, 'wr_score': wr_s, 'n_pen': n_p})
        if (i+1) % 200 == 0:
            print(f'  [progress] {i+1}/{len(grid)} ({time.time()-t0:.0f}s)', flush=True)

    print(f'[sweep] {len(results)} variants evaluated ({time.time()-t0:.0f}s)\n')

    results.sort(key=lambda r: -r['composite'])

    print('=' * 130)
    print('  TOP 15 — wave-modulator variants (ranked by composite)')
    print('=' * 130)
    print(f'  {"#":<3s}  {"GH":<3s} {"SP":<4s} {"W":<5s} {"KL":<5s} {"TL":<3s} {"CT":<5s} {"KD":<5s} {"TD":<3s}    +75 ΔWR/ΔN     +80 ΔWR/ΔN     +85 ΔWR/ΔN     +90 ΔWR/ΔN     comp')
    print('  ' + '-' * 128)
    for i, r in enumerate(results[:15]):
        p = r['params']
        ts = r['tiers']
        row = (f'  #{i+1:<2d}  {p["gate_hi"]:<3d} {p["score_power"]:<4.1f} '
               f'{p["width"]:<5.2f} {p["k_lift"]:<5.2f} {p["target_lift"]:<3d} '
               f'{p["climax_thresh"]:<5.2f} {p["k_dampen"]:<5.2f} {p["target_dampen"]:<3d}  ')
        for t in [75, 80, 85, 90]:
            d_wr = ts[t]['d_wr']
            d_n = ts[t]['d_n_pct']
            if d_wr is None:
                row += '   N/A         '
            else:
                row += f' {d_wr:>+5.2f}/{d_n:>+5.1f}% '
        row += f'   {r["composite"]:+.2f}'
        print(row)

    # Write full results
    out = ROOT / 'experiments' / 'weekly_volume' / 'wave_sweep.jsonl'
    with open(out, 'w', encoding='utf-8') as f:
        for r in results:
            f.write(json.dumps(r, default=str) + '\n')
    print(f'\n[done] wrote {out} ({len(results)} variants)')

    # Top-1 detailed
    top = results[0]
    print('\n' + '=' * 130)
    print(f'  TOP-1 detailed: {top["params"]}')
    print('=' * 130)
    for t in CALL_TIERS:
        r = top['tiers'][t]
        if r['b_wr'] is not None and r['n_wr'] is not None:
            print(f'    +{t}: base WR={r["b_wr"]:>5.2f}% (N={r["b_n"]:>5d})  →  new WR={r["n_wr"]:>5.2f}% (N={r["n_n"]:>5d})    ΔWR={r["d_wr"]:>+5.2f}pp  ΔN={r["d_n_pct"]:>+5.1f}%')


if __name__ == '__main__':
    main()
