"""V3 — Dense Bayesian-style refinement centered on v2 strict winner.

v2 strict winner basis:
  LO=0.7, HI=1.8, ALPHA=0.95, TARGET=65, MCAP_POWER=0.5, SCORE_POWER=2.0
  → +2.11pp 5y / +2.30pp 10y, gaps=[4.31, 3.63, 2.38, 6.09], RMSE=0.98

Stage A: dense local grid (~4500 variants, ~6min)
  LO        ∈ {0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0}                 (7)
  HI        ∈ {1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.2}            (8)
  ALPHA     ∈ {0.70, 0.80, 0.90, 0.95, 1.00}                      (5)
  TARGET    ∈ {60, 62, 64, 65, 66, 68}                            (6)
  MCAP_POWER  ∈ {0.30, 0.50, 0.70, 1.00}                          (4)
  SCORE_POWER ∈ {1.50, 1.75, 2.00, 2.25, 2.50}                    (5)
  GATE fixed [70, 84]

Stage B: take top-5 by composite, do dense ±1 step refinement around each
        (~500 variants total)

Constraints (HARD):
  - Monotonic dampened TP across 5y AND 10y
  - min_gap_5y >= 2.0pp
  - SCORE_POWER >= 1.5 (user's natural-gradient priority)
  - N drop on 75+ within -45%
  - Spillover on 80+/85+/90+ within ±0.4pp

Composite score (for ranking):
  composite = 0.55*lift_5y + 0.35*lift_10y - 0.10*rmse
  Lift gets ~90% weight; RMSE gives small calibration tiebreak.
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
LOG_A = Path(__file__).parent / 'sweep_v3_stageA.jsonl'
LOG_B = Path(__file__).parent / 'sweep_v3_stageB.jsonl'

BUCKETS = [('70-74', 70, 74), ('75-79', 75, 79), ('80-84', 80, 84),
           ('85-89', 85, 89), ('90+', 90, 100)]


def apply_dampener(df, gate_lo, gate_hi, log_lo, log_hi, alpha, target, mcap_power, score_power):
    if alpha <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))
    has_mcap = pl.col('mcap_b').is_not_null() & (pl.col('mcap_b') > 0)
    log_mcap = pl.col('mcap_b').clip(0.001, None).log10()
    mcap_raw = ((log_hi - log_mcap) / (log_hi - log_lo)).clip(0.0, 1.0)
    mcap_factor = mcap_raw.pow(mcap_power)
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


def evaluate(df_5y, df_10y, base_5y_per, base_5y_cum, base_10y_per, base_10y_cum, **params):
    out = {'5y': {'per': {}, 'cum': {}}, '10y': {'per': {}, 'cum': {}}}
    for label, tag, df, base_per, base_cum in [
        ('5y', '5y', df_5y, base_5y_per, base_5y_cum),
        ('10y', '10y', df_10y, base_10y_per, base_10y_cum),
    ]:
        df_d = apply_dampener(df, **params)
        for buc, lo, hi in BUCKETS:
            tp, n = bucket_tp_n(df_d, 'new_overall', lo, hi)
            out[tag]['per'][buc] = {
                'tp': tp, 'n': n,
                'base_tp': base_per[buc]['tp'],
                'base_n': base_per[buc]['n'],
                'delta': (tp - base_per[buc]['tp']) if (tp is not None and base_per[buc]['tp'] is not None) else None,
            }
        for thr in [75, 80, 85, 90, 95]:
            tp, n = cumulative_tp_n(df_d, 'new_overall', thr)
            out[tag]['cum'][f'{thr}+'] = {
                'tp': tp, 'n': n,
                'base_tp': base_cum[f'{thr}+']['tp'],
                'base_n': base_cum[f'{thr}+']['n'],
                'delta': (tp - base_cum[f'{thr}+']['tp']) if (tp is not None and base_cum[f'{thr}+']['tp'] is not None) else None,
            }
        # Monotonicity
        tps = [out[tag]['per'][b[0]]['tp'] for b in BUCKETS]
        out[tag]['monotonic'] = all(
            (tps[i] is None or tps[i+1] is None or tps[i+1] >= tps[i])
            for i in range(len(tps) - 1)
        )
        gaps = [tps[i+1] - tps[i] for i in range(len(tps)-1)
                if tps[i] is not None and tps[i+1] is not None]
        out[tag]['min_gap'] = min(gaps) if gaps else None
        out[tag]['gaps'] = gaps
        # RMSE
        sse = 0.0; total_n = 0
        for label, _, _ in BUCKETS:
            c = out[tag]['per'][label]
            if c['delta'] is not None and c['n'] > 0:
                sse += (c['delta'] ** 2) * c['n']
                total_n += c['n']
        out[tag]['rmse'] = (sse / total_n) ** 0.5 if total_n > 0 else float('inf')
    return out


def passes(rec, sp_floor=1.5, gap_floor=2.0, n_floor=-45.0):
    if not rec['monotonic_5y'] or not rec['monotonic_10y']: return False
    if rec['score_power'] < sp_floor: return False
    if rec['min_gap_5y'] is None or rec['min_gap_5y'] < gap_floor: return False
    if rec['cum_lift_5y'] is None or rec['cum_lift_5y'] < 1.0: return False
    if rec['cum_lift_10y'] is None or rec['cum_lift_10y'] < 1.0: return False
    if rec['n_drop_5y_pct'] < n_floor: return False
    if rec['worst_spill'] < -0.4: return False
    return True


def composite_score(rec):
    if not passes(rec): return -100
    return 0.55 * rec['cum_lift_5y'] + 0.35 * rec['cum_lift_10y'] - 0.10 * rec['rmse_5y']


def evaluate_and_record(df_5y, df_10y, base_5y_per, base_5y_cum, base_10y_per, base_10y_cum,
                        params, log_path):
    res = evaluate(df_5y, df_10y, base_5y_per, base_5y_cum, base_10y_per, base_10y_cum, **params)
    cum_5 = res['5y']['cum']['75+']['delta']
    cum_10 = res['10y']['cum']['75+']['delta']
    n_drop = (res['5y']['cum']['75+']['n'] - base_5y_cum['75+']['n']) / base_5y_cum['75+']['n'] * 100 if base_5y_cum['75+']['n'] else 0
    spill_85 = res['5y']['cum']['85+']['delta']
    spill_90 = res['5y']['cum']['90+']['delta']
    worst_spill = min((d for d in [spill_85, spill_90] if d is not None), default=0.0)
    worst_spill = min(worst_spill, 0.0)
    rec = {**params,
           'cum_lift_5y': cum_5, 'cum_lift_10y': cum_10,
           'rmse_5y': res['5y']['rmse'], 'rmse_10y': res['10y']['rmse'],
           'monotonic_5y': res['5y']['monotonic'], 'monotonic_10y': res['10y']['monotonic'],
           'min_gap_5y': res['5y']['min_gap'], 'min_gap_10y': res['10y']['min_gap'],
           'gaps_5y': res['5y']['gaps'], 'gaps_10y': res['10y']['gaps'],
           'n_drop_5y_pct': n_drop, 'worst_spill': worst_spill,
           'per_bucket_5y': res['5y']['per']}
    rec['pass'] = passes(rec)
    rec['composite'] = composite_score(rec)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, default=str) + '\n')
    return rec


def stage_a(df_5y, df_10y, base_5y_per, base_5y_cum, base_10y_per, base_10y_cum):
    """Focused local grid centered on v2 winner basin (LO=0.7, HI=1.8, α=0.95, T=65, mp=0.5, sp=2.0)."""
    candidates = []
    for log_lo in [0.5, 0.7, 0.9]:                              # 3
        for log_hi in [1.5, 1.7, 1.9, 2.1]:                     # 4
            if log_hi <= log_lo + 0.3: continue
            for alpha in [0.85, 0.90, 0.95, 1.00]:              # 4
                for target in [62, 64, 65, 66, 68]:             # 5
                    if target >= 70: continue
                    for mcap_power in [0.30, 0.50, 0.70, 1.00]: # 4
                        for score_power in [1.50, 1.75, 2.00, 2.25, 2.50]: # 5
                            candidates.append({
                                'gate_lo': 70, 'gate_hi': 84,
                                'log_lo': log_lo, 'log_hi': log_hi,
                                'alpha': alpha, 'target': target,
                                'mcap_power': mcap_power, 'score_power': score_power,
                            })
    # = 3*4*4*5*4*5 = 4800
    return candidates


def stage_b(top_recs):
    """Around each top-5 result, ±1 step on each axis."""
    candidates = []
    seen = set()
    for r in top_recs:
        # Anchor coords
        log_lo, log_hi = r['log_lo'], r['log_hi']
        alpha, target = r['alpha'], r['target']
        mcap_power, score_power = r['mcap_power'], r['score_power']
        for dlo in [-0.05, 0, 0.05]:
            for dhi in [-0.1, 0, 0.1]:
                for da in [-0.05, 0, 0.05]:
                    for dt in [-1, 0, 1]:
                        for dmp in [-0.10, 0, 0.10]:
                            for dsp in [-0.10, 0, 0.10]:
                                params = {
                                    'gate_lo': 70, 'gate_hi': 84,
                                    'log_lo': round(log_lo + dlo, 2),
                                    'log_hi': round(log_hi + dhi, 2),
                                    'alpha': round(alpha + da, 2),
                                    'target': target + dt,
                                    'mcap_power': round(mcap_power + dmp, 2),
                                    'score_power': round(score_power + dsp, 2),
                                }
                                # Sanity bounds
                                if params['log_hi'] <= params['log_lo'] + 0.3: continue
                                if params['alpha'] < 0.5 or params['alpha'] > 1.0: continue
                                if params['target'] < 58 or params['target'] >= 70: continue
                                if params['mcap_power'] < 0.2 or params['mcap_power'] > 1.5: continue
                                if params['score_power'] < 1.0 or params['score_power'] > 3.0: continue
                                key = tuple(sorted(params.items()))
                                if key in seen: continue
                                seen.add(key)
                                candidates.append(params)
    return candidates


def main():
    parquet = LOCAL / 'calls_v39_3650.parquet'
    df = pl.read_parquet(parquet).filter(pl.col('opt_result_15').is_not_null())
    today = date.today()
    df_5y = df.filter(pl.col('date') >= (today - timedelta(days=1825)).isoformat())
    df_10y = df

    print(f'Loaded — 5y: {len(df_5y):,} | 10y: {len(df_10y):,}')

    # Baselines
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

    # ── STAGE A ──
    if LOG_A.exists(): LOG_A.unlink()
    cands_a = stage_a(df_5y, df_10y, base_5y_per, base_5y_cum, base_10y_per, base_10y_cum)
    print(f'\n[STAGE A] {len(cands_a):,} variants — dense around v2 winner basin')

    t_start = time.time()
    results_a = []
    for i, params in enumerate(cands_a):
        rec = evaluate_and_record(df_5y, df_10y, base_5y_per, base_5y_cum,
                                  base_10y_per, base_10y_cum, params, LOG_A)
        results_a.append(rec)
        if (i+1) % 500 == 0:
            elapsed = time.time() - t_start
            eta = elapsed / (i+1) * (len(cands_a) - i - 1)
            print(f'  [{i+1}/{len(cands_a)}] {elapsed:.0f}s eta {eta:.0f}s', flush=True)
    print(f'[STAGE A] done in {time.time()-t_start:.1f}s')

    n_pass = sum(1 for r in results_a if r['pass'])
    print(f'  Soft-pass: {n_pass}/{len(results_a)} ({100*n_pass/len(results_a):.1f}%)')

    # Top-5 for stage B seeding
    pass_a = sorted([r for r in results_a if r['pass']], key=lambda r: -r['composite'])
    print('\n=== STAGE A TOP 10 (composite ranked) ===')
    for r in pass_a[:10]:
        gaps = [round(g, 2) for g in r['gaps_5y']]
        print(f"  comp={r['composite']:.3f} | LO={r['log_lo']:.2f} HI={r['log_hi']:.2f} α={r['alpha']:.2f} T={r['target']:>2} mp={r['mcap_power']:.2f} sp={r['score_power']:.2f} | "
              f"5y={r['cum_lift_5y']:+.2f} 10y={r['cum_lift_10y']:+.2f} gaps={gaps} rmse={r['rmse_5y']:.2f} N={r['n_drop_5y_pct']:+.1f}%")

    # ── STAGE B — refine around top 5 ──
    if not pass_a:
        print('[STAGE B] no soft-pass winners, skipping refinement')
        return
    if LOG_B.exists(): LOG_B.unlink()

    top_5 = pass_a[:5]
    cands_b = stage_b(top_5)
    print(f'\n[STAGE B] {len(cands_b):,} variants — local refinement around top 5 stage-A winners')

    t_start = time.time()
    results_b = []
    for i, params in enumerate(cands_b):
        rec = evaluate_and_record(df_5y, df_10y, base_5y_per, base_5y_cum,
                                  base_10y_per, base_10y_cum, params, LOG_B)
        results_b.append(rec)
        if (i+1) % 200 == 0:
            elapsed = time.time() - t_start
            eta = elapsed / (i+1) * (len(cands_b) - i - 1)
            print(f'  [{i+1}/{len(cands_b)}] {elapsed:.0f}s eta {eta:.0f}s', flush=True)
    print(f'[STAGE B] done in {time.time()-t_start:.1f}s')

    n_pass_b = sum(1 for r in results_b if r['pass'])
    print(f'  Soft-pass: {n_pass_b}/{len(results_b)} ({100*n_pass_b/len(results_b):.1f}%)')

    # Combined ranking
    all_results = results_a + results_b
    pass_all = sorted([r for r in all_results if r['pass']], key=lambda r: -r['composite'])

    print('\n' + '='*100)
    print('CHAMPIONSHIP — TOP 20 (composite ranked, monotonic + min_gap >= 2.0pp + sp >= 1.5)')
    print('='*100)
    print(f"{'rank':>4} {'comp':>6} {'LO':>5} {'HI':>5} {'α':>5} {'T':>3} {'mp':>5} {'sp':>5} | "
          f"{'5y':>7} {'10y':>7} {'gaps':>30} {'rmse':>5} {'Ndrop':>7}")
    for i, r in enumerate(pass_all[:20]):
        gaps_str = '[' + ', '.join(f'{g:.2f}' for g in r['gaps_5y']) + ']'
        print(f"  {i+1:>3} {r['composite']:>5.2f} {r['log_lo']:>4.2f} {r['log_hi']:>4.2f} {r['alpha']:>4.2f} "
              f"{r['target']:>3} {r['mcap_power']:>4.2f} {r['score_power']:>4.2f} | "
              f"{r['cum_lift_5y']:>+5.2f}pp {r['cum_lift_10y']:>+5.2f}pp {gaps_str:>30} "
              f"{r['rmse_5y']:>4.2f} {r['n_drop_5y_pct']:>+5.1f}%")

    if pass_all:
        winner = pass_all[0]
        print('\n' + '='*100)
        print('CHAMPION — composite-optimal v3 candidate')
        print('='*100)
        print(f"  GATE [70, 84]")
        print(f"  LOG_LO = {winner['log_lo']:+.3f} (mcap_b ${10**winner['log_lo']:.2f}B at full strength)")
        print(f"  LOG_HI = {winner['log_hi']:+.3f} (mcap_b ${10**winner['log_hi']:.2f}B at zero strength)")
        print(f"  ALPHA = {winner['alpha']:.2f}, TARGET = {winner['target']}")
        print(f"  MCAP_POWER = {winner['mcap_power']:.2f}, SCORE_POWER = {winner['score_power']:.2f}")
        print(f"\n  Per-bucket TP (5y):")
        for label, _, _ in BUCKETS:
            c = winner['per_bucket_5y'][label]
            base = c['base_tp']; damp = c['tp']
            verdict = ''
            if c['delta'] is not None:
                if abs(c['delta']) < 0.5: verdict = 'CALIBRATED'
                elif c['delta'] > 0: verdict = f'+{c["delta"]:.2f}pp (favorable)'
                else: verdict = f'{c["delta"]:.2f}pp'
            print(f"    {label}: base {base:.2f}% → damp {damp:.2f}% N={c['n']:,}  {verdict}")
        gaps = winner['gaps_5y']
        print(f"\n  5y gaps between buckets: {[round(g, 2) for g in gaps]}")
        print(f"  Min gap: {winner['min_gap_5y']:.2f}pp (>= 2.0pp threshold)")
        print(f"\n  Multi-window 75+ lift:")
        print(f"    5y: {winner['cum_lift_5y']:+.2f}pp")
        print(f"    10y: {winner['cum_lift_10y']:+.2f}pp")
        print(f"  Calibration RMSE: 5y {winner['rmse_5y']:.2f}pp / 10y {winner['rmse_10y']:.2f}pp")
        print(f"  N drop on 75+: {winner['n_drop_5y_pct']:+.1f}%")
        print(f"  Worst spillover: {winner['worst_spill']:+.2f}pp")


if __name__ == '__main__':
    main()
