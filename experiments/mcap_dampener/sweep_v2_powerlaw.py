"""Mcap dampener v2 — power-law mechanism + monotonicity preservation.

Extends the v1 linear-ramp dampener with TWO additional shape parameters:

  weakness = mcap_factor^MCAP_POWER * score_factor^SCORE_POWER
  where:
    mcap_factor  = clip((LOG_HI - log10(mcap_b)) / (LOG_HI - LOG_LO), 0, 1)
    score_factor = clip((overall - GATE_LO) / (GATE_HI - GATE_LO), 0, 1)
  overall -= ALPHA * weakness * (overall - TARGET)

Defaults MCAP_POWER=1, SCORE_POWER=1 reduce to v1 linear (modulo the additive
score_factor multiplier which is NEW vs v1).

Why score_factor:
  - In v1, dampening at overall=70 (full mcap weakness, α=0.30, T=65) drops 1.5pp
  - In v1, dampening at overall=84 drops 5.7pp
  - The user observation: signal magnitude differs between tiers; more aggressive
    dampening should be reserved for the tiers where over-confidence is strongest
  - SCORE_POWER>1 concentrates dampening at the top of the gate range
  - SCORE_POWER<1 spreads dampening more uniformly

Why mcap_power:
  - In v1, mcap_factor is linear in log10(mcap_b)
  - MCAP_POWER>1: concentrate dampening at smallest caps (micro_lt2B), barely touch mid-caps
  - MCAP_POWER<1: smooth dampening across all caps, large-caps still get nudge

OBJECTIVE: maximize cumulative 75+ TP% lift subject to:
  H1 — Monotonic dampened TP rate: 70-74 < 75-79 < 80-84 < 85-89 < 90+
  H1b — Minimum inter-bucket gap >= 1.0pp (preserve "natural scoring wave")
  H2 — Cumulative 75+ lift >= +1.0pp
  H3 — Spillover on 90+/95+ within ±0.4pp (untouched buckets)
  H4 — N drop on 75+ within -40% (matches v27 WCF precedent)
"""
from __future__ import annotations
import io, sys, time, json
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LOCAL = ROOT / '.cache' / 'mcap_dampener'
LOG_PQ = Path(__file__).parent / 'sweep_v2.jsonl'

BUCKETS = [('70-74', 70, 74), ('75-79', 75, 79), ('80-84', 80, 84),
           ('85-89', 85, 89), ('90+', 90, 100)]


def apply_dampener_v2(df, gate_lo, gate_hi, log_lo, log_hi, alpha, target,
                     mcap_power=1.0, score_power=1.0):
    if alpha <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))
    has_mcap = pl.col('mcap_b').is_not_null() & (pl.col('mcap_b') > 0)
    log_mcap = pl.col('mcap_b').clip(0.001, None).log10()

    # mcap factor: 1 at log_lo (smallest); 0 at log_hi (largest)
    mcap_raw = ((log_hi - log_mcap) / (log_hi - log_lo)).clip(0.0, 1.0)
    mcap_factor = mcap_raw.pow(mcap_power)

    # score factor: 0 at gate_lo (lowest); 1 at gate_hi (highest)
    score_raw = ((pl.col('overall') - gate_lo) / (gate_hi - gate_lo)).clip(0.0, 1.0)
    score_factor = score_raw.pow(score_power)

    weakness = mcap_factor * score_factor
    in_zone = (pl.col('overall') >= gate_lo) & (pl.col('overall') <= gate_hi) & has_mcap

    return df.with_columns(
        pl.when(in_zone)
          .then(pl.col('overall') - alpha * weakness * (pl.col('overall') - target))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )


def bucket_tp_n(df, score_col, lo, hi):
    sub = df.filter((pl.col(score_col) >= lo) & (pl.col(score_col) <= hi))
    n = sub.height
    if n == 0:
        return None, 0
    tp = sub.select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item() * 100
    return tp, n


def cumulative_tp_n(df, score_col, threshold):
    sub = df.filter(pl.col(score_col) >= threshold)
    n = sub.height
    if n == 0:
        return None, 0
    tp = sub.select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item() * 100
    return tp, n


def evaluate_variant(df, base_per_bucket, base_cum, **params):
    """Returns dict with per-bucket TP/N + cumulative + monotonicity diagnostics."""
    df_d = apply_dampener_v2(df, **params)
    out = {'per_bucket': {}, 'cum': {}}
    for label, lo, hi in BUCKETS:
        tp, n = bucket_tp_n(df_d, 'new_overall', lo, hi)
        out['per_bucket'][label] = {'tp': tp, 'n': n,
                                    'base_tp': base_per_bucket[label]['tp'],
                                    'base_n': base_per_bucket[label]['n'],
                                    'delta': (tp - base_per_bucket[label]['tp']) if (tp is not None and base_per_bucket[label]['tp'] is not None) else None}
    for thr in [75, 80, 85, 90, 95]:
        tp, n = cumulative_tp_n(df_d, 'new_overall', thr)
        out['cum'][f'{thr}+'] = {'tp': tp, 'n': n,
                                 'base_tp': base_cum[f'{thr}+']['tp'],
                                 'base_n': base_cum[f'{thr}+']['n'],
                                 'delta': (tp - base_cum[f'{thr}+']['tp']) if (tp is not None and base_cum[f'{thr}+']['tp'] is not None) else None}

    # Monotonicity: TP must be strictly increasing across buckets
    tps = [out['per_bucket'][b[0]]['tp'] for b in BUCKETS]
    out['monotonic'] = all(
        (tps[i] is None or tps[i+1] is None or tps[i+1] >= tps[i])
        for i in range(len(tps) - 1)
    )
    # Min gap between adjacent populated buckets
    gaps = []
    for i in range(len(tps) - 1):
        if tps[i] is not None and tps[i+1] is not None:
            gaps.append(tps[i+1] - tps[i])
    out['min_gap'] = min(gaps) if gaps else None
    out['gaps'] = gaps

    # Calibration RMSE (N-weighted)
    weighted_sse = 0.0
    total_n = 0
    for label, _, _ in BUCKETS:
        c = out['per_bucket'][label]
        if c['delta'] is not None and c['n'] > 0:
            weighted_sse += (c['delta'] ** 2) * c['n']
            total_n += c['n']
    out['rmse'] = (weighted_sse / total_n) ** 0.5 if total_n > 0 else float('inf')

    return out


def main():
    parquet = LOCAL / 'calls_v39_3650.parquet'
    df = pl.read_parquet(parquet)
    df = df.filter(pl.col('opt_result_15').is_not_null())
    today = date.today()
    df_5y = df.filter(pl.col('date') >= (today - timedelta(days=1825)).isoformat())
    df_10y = df  # full 10y window already filtered

    print(f'Loaded — 5y resolved: {len(df_5y):,} | 10y resolved: {len(df_10y):,}')

    # Baselines (per-bucket and cumulative)
    def make_base(d):
        per_b = {}
        cum_b = {}
        for label, lo, hi in BUCKETS:
            tp, n = bucket_tp_n(d, 'overall', lo, hi)
            per_b[label] = {'tp': tp, 'n': n}
        for thr in [75, 80, 85, 90, 95]:
            tp, n = cumulative_tp_n(d, 'overall', thr)
            cum_b[f'{thr}+'] = {'tp': tp, 'n': n}
        return per_b, cum_b

    base_5y_per, base_5y_cum = make_base(df_5y)
    base_10y_per, base_10y_cum = make_base(df_10y)

    print('\n== Baseline 5y per-bucket TP ==')
    for label, _, _ in BUCKETS:
        b = base_5y_per[label]
        print(f"  {label}: TP={b['tp']:.2f}% N={b['n']:,}")

    print('\n== Baseline 5y MONOTONICITY check ==')
    base_tps = [base_5y_per[b[0]]['tp'] for b in BUCKETS]
    print(f"  TPs: {base_tps}")
    print(f"  Gaps: {[base_tps[i+1] - base_tps[i] for i in range(len(base_tps)-1)]}")

    # ── COMPREHENSIVE SWEEP ──
    # 6 free params now: log_lo, log_hi, alpha, target, mcap_power, score_power
    # Hold gate at [70, 84] (validated as best)
    candidates = []
    for log_lo in [-0.3, 0.0, 0.3, 0.5, 0.7]:
        for log_hi in [1.0, 1.3, 1.5, 1.8, 2.2]:
            if log_hi <= log_lo + 0.3: continue
            for alpha in [0.30, 0.50, 0.70, 0.95]:
                for target in [62, 65, 68, 70]:
                    if target >= 70: continue
                    for mcap_power in [0.5, 1.0, 2.0]:
                        for score_power in [0.5, 1.0, 2.0, 3.0]:
                            candidates.append({
                                'gate_lo': 70, 'gate_hi': 84,
                                'log_lo': log_lo, 'log_hi': log_hi,
                                'alpha': alpha, 'target': target,
                                'mcap_power': mcap_power, 'score_power': score_power,
                            })
    print(f'\n[sweep v2] {len(candidates):,} candidates')

    if LOG_PQ.exists(): LOG_PQ.unlink()

    results = []
    t_start = time.time()
    for i, params in enumerate(candidates):
        v_5y = evaluate_variant(df_5y, base_5y_per, base_5y_cum, **params)
        v_10y = evaluate_variant(df_10y, base_10y_per, base_10y_cum, **params)

        cum_lift_5y = v_5y['cum']['75+']['delta']
        cum_lift_10y = v_10y['cum']['75+']['delta']
        n_drop_5y_pct = (v_5y['cum']['75+']['n'] - base_5y_cum['75+']['n']) / base_5y_cum['75+']['n'] * 100

        # Spillover on top tiers (cumulative)
        spill_85 = v_5y['cum']['85+']['delta']
        spill_90 = v_5y['cum']['90+']['delta']
        worst_spill = min((d for d in [spill_85, spill_90] if d is not None), default=0.0)
        worst_spill = min(worst_spill, 0.0)

        # Constraints
        passes = (
            v_5y['monotonic']
            and v_5y['min_gap'] is not None and v_5y['min_gap'] >= 1.0
            and v_10y['monotonic']
            and cum_lift_5y is not None and cum_lift_5y >= 1.0
            and cum_lift_10y is not None and cum_lift_10y >= 1.0
            and n_drop_5y_pct >= -40.0
            and worst_spill >= -0.4
        )

        rec = {**params, 'cum_lift_5y': cum_lift_5y, 'cum_lift_10y': cum_lift_10y,
               'monotonic_5y': v_5y['monotonic'], 'monotonic_10y': v_10y['monotonic'],
               'min_gap_5y': v_5y['min_gap'], 'gaps_5y': v_5y['gaps'],
               'rmse_5y': v_5y['rmse'], 'rmse_10y': v_10y['rmse'],
               'n_drop_5y_pct': n_drop_5y_pct,
               'worst_spill': worst_spill, 'pass': passes,
               'per_bucket_5y': v_5y['per_bucket']}
        results.append(rec)
        with open(LOG_PQ, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

        if (i+1) % 200 == 0:
            elapsed = time.time() - t_start
            eta = elapsed / (i+1) * (len(candidates) - i - 1)
            print(f'  [{i+1}/{len(candidates)}] {elapsed:.0f}s eta {eta:.0f}s', flush=True)

    print(f'\n[sweep v2] done in {time.time()-t_start:.1f}s')
    n_pass = sum(1 for r in results if r['pass'])
    n_mono = sum(1 for r in results if r['monotonic_5y'])
    print(f'  Monotonic 5y: {n_mono}/{len(results)} ({100*n_mono/len(results):.1f}%)')
    print(f'  Soft-pass:    {n_pass}/{len(results)} ({100*n_pass/len(results):.1f}%)')

    # ── RANKINGS ──
    print('\n=== TOP 20 SOFT-PASS BY CUMULATIVE LIFT 5y (monotonic + min_gap >= 1.0pp) ===')
    pass_results = sorted([r for r in results if r['pass']], key=lambda r: -r['cum_lift_5y'])
    print(f"{'LO':>5} {'HI':>5} {'α':>5} {'T':>3} {'mp':>4} {'sp':>4} | "
          f"{'lift5y':>7} {'lift10y':>7} {'gap':>5} {'rmse':>5} {'spill':>6} {'Ndrop':>7}")
    for r in pass_results[:20]:
        print(f"  {r['log_lo']:>4.1f} {r['log_hi']:>4.1f} {r['alpha']:>5.2f} {r['target']:>3} "
              f"{r['mcap_power']:>4.1f} {r['score_power']:>4.1f} | "
              f"{r['cum_lift_5y']:>+5.2f}pp {r['cum_lift_10y']:>+5.2f}pp "
              f"{r['min_gap_5y']:>4.2f} {r['rmse_5y']:>4.2f} {r['worst_spill']:>+5.2f} {r['n_drop_5y_pct']:>+5.1f}%")

    print('\n=== TOP 10 BY MIN-GAP (most natural-looking gradient) — soft-pass ===')
    by_gap = sorted([r for r in results if r['pass']], key=lambda r: -r['min_gap_5y'])
    for r in by_gap[:10]:
        print(f"  {r['log_lo']:>4.1f} {r['log_hi']:>4.1f} {r['alpha']:>5.2f} {r['target']:>3} "
              f"{r['mcap_power']:>4.1f} {r['score_power']:>4.1f} | "
              f"{r['cum_lift_5y']:>+5.2f}pp {r['cum_lift_10y']:>+5.2f}pp "
              f"gaps={[round(g,2) for g in r['gaps_5y']]}")

    print('\n=== TOP 5 OVERALL CHAMPION (combined: lift + monotonic + nice gradient) ===')
    # composite: cum_lift_5y - 0.5*max(0, 2-min_gap) - 0.3*rmse_5y
    def composite(r):
        if not r['pass']: return -100
        gap_penalty = max(0, 2.0 - (r['min_gap_5y'] or 0)) * 0.5
        rmse_penalty = (r['rmse_5y'] or 0) * 0.3
        return r['cum_lift_5y'] - gap_penalty - rmse_penalty

    by_comp = sorted(results, key=composite, reverse=True)
    for r in by_comp[:5]:
        flag = '✓' if r['pass'] else ' '
        print(f"  {flag} LO={r['log_lo']:>4.1f} HI={r['log_hi']:>4.1f} α={r['alpha']:>4.2f} T={r['target']:>2} "
              f"mp={r['mcap_power']:>3.1f} sp={r['score_power']:>3.1f} | "
              f"5y lift={r['cum_lift_5y']:+.2f}pp 10y={r['cum_lift_10y']:+.2f}pp "
              f"gaps={[round(g,2) for g in r['gaps_5y']]} rmse={r['rmse_5y']:.2f}")

    if by_comp and by_comp[0]['pass']:
        winner = by_comp[0]
        print('\n' + '='*80)
        print('CHAMPION CANDIDATE')
        print('='*80)
        print(f"  GATE [70, 84]")
        print(f"  LOG_LO = {winner['log_lo']:+.3f} (mcap=${10**winner['log_lo']:.2f}B at full strength)")
        print(f"  LOG_HI = {winner['log_hi']:+.3f} (mcap=${10**winner['log_hi']:.2f}B at zero strength)")
        print(f"  ALPHA = {winner['alpha']:.2f}, TARGET = {winner['target']}")
        print(f"  MCAP_POWER = {winner['mcap_power']:.2f}, SCORE_POWER = {winner['score_power']:.2f}")
        print(f"\n  Per-bucket TP rates (5y):")
        for label, _, _ in BUCKETS:
            c = winner['per_bucket_5y'][label]
            base = c['base_tp']
            damp = c['tp']
            print(f"    {label}: base {base:.2f}% → damp {damp:.2f}%  Δ {damp-base:+.2f}pp  N={c['n']}")
        print(f"\n  Cumulative 75+ lift: 5y {winner['cum_lift_5y']:+.2f}pp / 10y {winner['cum_lift_10y']:+.2f}pp")
        print(f"  Min inter-bucket gap: {winner['min_gap_5y']:.2f}pp")
        print(f"  Calibration RMSE: 5y {winner['rmse_5y']:.2f}pp / 10y {winner['rmse_10y']:.2f}pp")


if __name__ == '__main__':
    main()
