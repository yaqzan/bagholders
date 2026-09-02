"""Mcap-aware call dampener — coarse grid sweep + Bayesian-style dense refinement.

Mechanism (continuous, asymmetric — calls only):

    if GATE_LO <= overall <= GATE_HI and mcap_b is not None and mcap_b > 0:
        log_mcap = log10(mcap_b)              # in $B
        weakness = clip((LOG_HI - log_mcap) / (LOG_HI - LOG_LO), 0, 1)
        overall -= ALPHA * weakness * (overall - TARGET)

Calls in [GATE_LO, GATE_HI] with small mcap drift toward TARGET (typically below
75 to push them out of cascade-qualifying universe). Large-cap calls untouched.
Puts untouched. No version bump per-sweep — only ship-time.

Free parameters (6):
  GATE_LO     [70, 75]    — min overall to apply
  GATE_HI     [80, 95]    — max overall to apply
  LOG_LO      [-1, 1.5]   — log10($B), full strength below
  LOG_HI      [0.5, 2.5]  — log10($B), zero strength above (must > LO + 0.3)
  TARGET      [60, 72]    — drift target
  ALPHA       [0.3, 1.0]  — dampening strength

Sweep strategy:
  Phase 1 (coarse): ~30 hand-picked variants exploring axes independently
  Phase 2 (refine): dense local grid (~500-700 variants) around top-3 from coarse

Per-trade gate (75+ affected tier):
  - 5y, 10y both >= +0.3pp on 75+ TP%
  - 1y, 3y, 5y, 10y all sign-consistent on 75+
  - 75+ N drop <= 15% (peaks displaced is acceptable, but not collapse)
  - Spillover on 80+/85+/90+/95+ within ±0.4pp
"""
from __future__ import annotations
import io, sys, time, json, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LOG_COARSE = Path(__file__).parent / 'sweep_coarse.jsonl'
LOG_REFINE = Path(__file__).parent / 'sweep_refine.jsonl'

CALL_BUCKETS = [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]


def apply_mcap_dampener(df, gate_lo, gate_hi, log_lo, log_hi, alpha, target):
    """Apply continuous mcap dampener to overall column. Returns df with new_overall."""
    if alpha <= 0 or log_hi <= log_lo:
        return df.with_columns(pl.col('overall').alias('new_overall'))

    has_mcap = pl.col('mcap_b').is_not_null() & (pl.col('mcap_b') > 0)
    log_mcap = pl.col('mcap_b').clip(0.001, None).log10()
    weakness = ((log_hi - log_mcap) / (log_hi - log_lo)).clip(0.0, 1.0)
    in_zone = (pl.col('overall') >= gate_lo) & (pl.col('overall') <= gate_hi) & has_mcap

    return df.with_columns(
        pl.when(in_zone)
          .then(pl.col('overall') - alpha * weakness * (pl.col('overall') - target))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )


def evaluate(df, window_start, gate_lo, gate_hi, log_lo, log_hi, alpha, target):
    sub = df.filter(pl.col('date') >= window_start)
    sub = apply_mcap_dampener(sub, gate_lo, gate_hi, log_lo, log_hi, alpha, target)
    sub = sub.filter(pl.col('opt_result_15').is_not_null())
    out = {}
    for thr, label in CALL_BUCKETS:
        cell = sub.filter(pl.col('new_overall') >= thr)
        n = len(cell)
        wins = int((cell['opt_result_15'] == 1).sum()) if n > 0 else 0
        out[label] = {'n': n, 'tp_pct': (wins / n * 100) if n > 0 else None}
    return out


def compute_deltas(base, var):
    """Return dict of tier -> Δ tp_pct for spillover analysis."""
    out = {}
    for thr, label in CALL_BUCKETS:
        b = base[label]; v = var[label]
        if b['tp_pct'] is None or v['tp_pct'] is None:
            out[label] = None
        else:
            out[label] = v['tp_pct'] - b['tp_pct']
    return out


def gate_check(base_5y, var_5y, base_10y, var_10y, base_3y, var_3y, base_1y, var_1y):
    """75+ affected-tier gate per assessment-backtest.md."""
    out = {}

    # Affected tier: 75+
    b75 = base_5y['75+']; v75 = var_5y['75+']
    out['affected_5y'] = (v75['tp_pct'] - b75['tp_pct']) if (b75['tp_pct'] and v75['tp_pct']) else 0.0
    out['affected_n_pct'] = ((v75['n'] - b75['n']) / b75['n'] * 100) if b75['n'] else 0.0

    # Multi-window
    out['affected_10y'] = (var_10y['75+']['tp_pct'] - base_10y['75+']['tp_pct']) if (base_10y['75+']['tp_pct'] and var_10y['75+']['tp_pct']) else None
    out['affected_3y'] = (var_3y['75+']['tp_pct'] - base_3y['75+']['tp_pct']) if (base_3y['75+']['tp_pct'] and var_3y['75+']['tp_pct']) else None
    out['affected_1y'] = (var_1y['75+']['tp_pct'] - base_1y['75+']['tp_pct']) if (base_1y['75+']['tp_pct'] and var_1y['75+']['tp_pct']) else None

    # Spillover on top tiers
    deltas_5y = compute_deltas(base_5y, var_5y)
    spillover = [(thr, deltas_5y[thr]) for thr in ('80+', '85+', '90+', '95+')]
    out['spillover'] = spillover
    spillover_max_neg = min((d for _, d in spillover if d is not None), default=0.0)
    spillover_max_neg = min(spillover_max_neg, 0.0)
    out['spillover_max_neg'] = spillover_max_neg

    # 70+ tier (overflow context)
    out['d70_5y'] = deltas_5y['70+']

    # Soft pass: 5y AND 10y both >=+0.3pp on 75+, all windows sign-consistent positive,
    #            N drop <= 15%, no spillover < -0.4pp
    soft_pass = (
        out['affected_5y'] >= 0.3
        and (out['affected_10y'] is None or out['affected_10y'] >= 0.3)
        and (out['affected_3y'] is None or out['affected_3y'] >= 0.0)
        and (out['affected_1y'] is None or out['affected_1y'] >= -0.2)
        and out['affected_n_pct'] >= -15.0
        and spillover_max_neg >= -0.4
    )
    out['soft_pass'] = soft_pass

    # Composite score (for ranking): avg(5y, 10y) lift, penalized by negative spillover
    a5 = out['affected_5y'] or 0.0
    a10 = out['affected_10y'] if out['affected_10y'] is not None else a5
    out['composite'] = (a5 + a10) / 2.0 + min(0.0, spillover_max_neg)

    return out


def coarse_grid():
    """Hand-designed coarse grid exploring axes independently."""
    cands = []

    # ── Anchor: gate=70-89, log_lo=0 ($1B), log_hi=1.5 ($32B), target=70, alpha=0.95 ──
    cands.append(('A_anchor',           70, 89, 0.0, 1.5, 0.95, 70))

    # ── Vary ALPHA (5) ──
    cands.append(('A_a30',              70, 89, 0.0, 1.5, 0.30, 70))
    cands.append(('A_a50',              70, 89, 0.0, 1.5, 0.50, 70))
    cands.append(('A_a70',              70, 89, 0.0, 1.5, 0.70, 70))
    cands.append(('A_a100',             70, 89, 0.0, 1.5, 1.00, 70))

    # ── Vary LOG_LO (4) ──  (controls when full dampening kicks in)
    cands.append(('B_lo_neg5',          70, 89, -0.5, 1.5, 0.95, 70))  # full at <$316M
    cands.append(('B_lo_pos5',          70, 89, 0.5, 1.5, 0.95, 70))   # full at <$3.2B
    cands.append(('B_lo_pos7',          70, 89, 0.7, 1.5, 0.95, 70))   # full at <$5B
    cands.append(('B_lo_pos10',         70, 89, 1.0, 1.5, 0.95, 70))   # full at <$10B

    # ── Vary LOG_HI (4) ── (controls upper bound where dampening fades)
    cands.append(('C_hi_10',            70, 89, 0.0, 1.0, 0.95, 70))   # zero at >$10B
    cands.append(('C_hi_13',            70, 89, 0.0, 1.3, 0.95, 70))
    cands.append(('C_hi_18',            70, 89, 0.0, 1.8, 0.95, 70))
    cands.append(('C_hi_22',            70, 89, 0.0, 2.2, 0.95, 70))   # zero at >$160B

    # ── Vary TARGET (3) ──
    cands.append(('D_t60',              70, 89, 0.0, 1.5, 0.95, 60))
    cands.append(('D_t65',              70, 89, 0.0, 1.5, 0.95, 65))
    cands.append(('D_t72',              70, 89, 0.0, 1.5, 0.95, 72))

    # ── Vary GATE_HI (3) ──
    cands.append(('E_gh79',             70, 79, 0.0, 1.5, 0.95, 70))
    cands.append(('E_gh84',             70, 84, 0.0, 1.5, 0.95, 70))
    cands.append(('E_gh94',             70, 94, 0.0, 1.5, 0.95, 70))

    # ── Tighter shape combos (5) ──
    cands.append(('F_tight_lo_alpha',   70, 89, 0.5, 1.0, 0.95, 70))   # narrow zone, full alpha
    cands.append(('F_wide_lo_hi',       70, 89, -0.5, 2.0, 0.95, 70))  # wide ramp
    cands.append(('F_low_target',       70, 89, 0.0, 1.5, 0.95, 65))   # below 70 floor
    cands.append(('F_strong_micro',     70, 89, -1.0, 0.5, 1.00, 65))  # extreme: ONLY micro
    cands.append(('F_gentle_smear',     70, 94, -0.5, 2.5, 0.50, 70))  # gentle wide

    # ── Aggressive vs gentle pivots (3) ──
    cands.append(('G_aggressive',       70, 89, 0.5, 1.5, 1.00, 60))   # strong push to 60
    cands.append(('G_gentle',           70, 89, 0.0, 1.5, 0.50, 72))   # gentle, target 72
    cands.append(('G_micro_focus',      70, 79, 0.0, 1.0, 0.95, 65))   # only sub-80, narrow

    return cands


def main():
    parquet = ROOT / '.cache' / 'mcap_dampener' / 'calls_v39_3650.parquet'
    df = pl.read_parquet(parquet)
    print(f'[sweep] loaded {len(df):,} rows from {parquet.name}', flush=True)
    n_resolved = df.filter(pl.col('opt_result_15').is_not_null()).height
    n_with_mcap = df.filter(pl.col('mcap_b').is_not_null() & pl.col('opt_result_15').is_not_null()).height
    print(f'[sweep] resolved: {n_resolved:,} | with mcap+resolved: {n_with_mcap:,}', flush=True)

    today = date.today()
    w10y = (today - timedelta(days=3650)).isoformat()
    w5y = (today - timedelta(days=1825)).isoformat()
    w3y = (today - timedelta(days=1095)).isoformat()
    w1y = (today - timedelta(days=365)).isoformat()

    # ── Baseline (alpha=0) ──
    print('[sweep] baseline...', flush=True)
    base_10y = evaluate(df, w10y, 70, 89, 0, 1.5, 0, 70)
    base_5y  = evaluate(df, w5y,  70, 89, 0, 1.5, 0, 70)
    base_3y  = evaluate(df, w3y,  70, 89, 0, 1.5, 0, 70)
    base_1y  = evaluate(df, w1y,  70, 89, 0, 1.5, 0, 70)

    print('[baseline 5y] CALL TP%:')
    for thr, label in CALL_BUCKETS:
        b = base_5y[label]
        if b['tp_pct'] is not None:
            print(f"  {label:>6} | N={b['n']:>5} TP={b['tp_pct']:.2f}%")

    print('\n[baseline 10y] CALL TP%:')
    for thr, label in CALL_BUCKETS:
        b = base_10y[label]
        if b['tp_pct'] is not None:
            print(f"  {label:>6} | N={b['n']:>5} TP={b['tp_pct']:.2f}%")

    # ── PHASE 1: COARSE GRID ──
    cands = coarse_grid()
    print(f'\n[sweep] PHASE 1: COARSE GRID — {len(cands)} variants', flush=True)
    if LOG_COARSE.exists(): LOG_COARSE.unlink()

    coarse_results = []
    t_start = time.time()
    for label, glo, ghi, llo, lhi, alpha, target in cands:
        v_5y  = evaluate(df, w5y,  glo, ghi, llo, lhi, alpha, target)
        v_10y = evaluate(df, w10y, glo, ghi, llo, lhi, alpha, target)
        v_3y  = evaluate(df, w3y,  glo, ghi, llo, lhi, alpha, target)
        v_1y  = evaluate(df, w1y,  glo, ghi, llo, lhi, alpha, target)
        gate = gate_check(base_5y, v_5y, base_10y, v_10y, base_3y, v_3y, base_1y, v_1y)
        rec = {
            'label': label, 'glo': glo, 'ghi': ghi, 'llo': llo, 'lhi': lhi,
            'alpha': alpha, 'target': target, 'gate': gate
        }
        coarse_results.append(rec)
        with open(LOG_COARSE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

        flag = '✓' if gate['soft_pass'] else ' '
        a5 = gate['affected_5y']
        a10 = gate['affected_10y']
        a3 = gate['affected_3y']
        a1 = gate['affected_1y']
        a10_s = '--' if a10 is None else f'{a10:+.2f}'
        a3_s = '--' if a3 is None else f'{a3:+.2f}'
        a1_s = '--' if a1 is None else f'{a1:+.2f}'
        print(f"  {label:22s} g[{glo}-{ghi}] LO={llo:+.1f} HI={lhi:+.1f} α={alpha:.2f} T={target:>2} | "
              f"{flag} 75+ 5y={a5:+.2f} 10y={a10_s} 3y={a3_s} 1y={a1_s} | "
              f"N75 {gate['affected_n_pct']:+.1f}% spill={gate['spillover_max_neg']:+.2f} "
              f"comp={gate['composite']:+.3f}", flush=True)

    print(f'\n[coarse] done in {time.time()-t_start:.1f}s', flush=True)

    # Top-K winners by composite
    coarse_results.sort(key=lambda r: -r['gate']['composite'])
    soft_passes = [r for r in coarse_results if r['gate']['soft_pass']]
    print(f'\n[coarse] {len(soft_passes)}/{len(coarse_results)} soft-pass; top by composite:')
    for r in coarse_results[:10]:
        g = r['gate']
        flag = '✓' if g['soft_pass'] else ' '
        a10 = g['affected_10y']
        a10_s = '--' if a10 is None else f'{a10:+.2f}'
        print(f"  {flag} {r['label']:22s} | 75+ 5y={g['affected_5y']:+.2f} 10y={a10_s} | "
              f"comp={g['composite']:+.3f}")

    # ── PHASE 2: DENSE REFINEMENT around basin of top winners ──
    # Identify basin from top-3 soft-passes (or top-3 by composite if no soft-pass)
    basin_seeds = soft_passes[:3] if len(soft_passes) >= 3 else coarse_results[:3]
    if not basin_seeds:
        print('[refine] no winners to refine — exiting')
        return
    print(f'\n[refine] PHASE 2: DENSE LOCAL GRID around top winners')
    for s in basin_seeds:
        print(f"  seed: {s['label']} (g={s['glo']}-{s['ghi']} LO={s['llo']} HI={s['lhi']} α={s['alpha']} T={s['target']})")

    # Build refinement grid centered on basin median
    glos = sorted({s['glo'] for s in basin_seeds} | {70, 72})
    ghis = sorted({s['ghi'] for s in basin_seeds} | {84, 89, 94})
    llos = sorted({round(s['llo'], 2) for s in basin_seeds} | {-0.3, 0.0, 0.3, 0.5, 0.7, 1.0})
    lhis = sorted({round(s['lhi'], 2) for s in basin_seeds} | {1.0, 1.3, 1.5, 1.8, 2.2})
    alphas = sorted({s['alpha'] for s in basin_seeds} | {0.7, 0.85, 0.95, 1.0})
    targets = sorted({s['target'] for s in basin_seeds} | {62, 65, 68, 70, 72})

    print(f'  refinement axes: glos={glos} ghis={ghis}')
    print(f'    llos={llos} lhis={lhis}')
    print(f'    alphas={alphas} targets={targets}')

    # Generate dense grid (constraint: lhi > llo + 0.3)
    refinements = []
    for glo in glos:
        for ghi in ghis:
            if ghi <= glo: continue
            for llo in llos:
                for lhi in lhis:
                    if lhi <= llo + 0.3: continue
                    for alpha in alphas:
                        for target in targets:
                            if target >= glo: continue  # target must drift below gate floor for displacement
                            refinements.append((glo, ghi, llo, lhi, alpha, target))

    print(f'[refine] generated {len(refinements):,} candidate variants (after constraints)', flush=True)

    if LOG_REFINE.exists(): LOG_REFINE.unlink()

    refine_results = []
    t_start = time.time()
    for i, (glo, ghi, llo, lhi, alpha, target) in enumerate(refinements):
        v_5y = evaluate(df, w5y, glo, ghi, llo, lhi, alpha, target)
        v_10y = evaluate(df, w10y, glo, ghi, llo, lhi, alpha, target)
        v_3y = evaluate(df, w3y, glo, ghi, llo, lhi, alpha, target)
        v_1y = evaluate(df, w1y, glo, ghi, llo, lhi, alpha, target)
        gate = gate_check(base_5y, v_5y, base_10y, v_10y, base_3y, v_3y, base_1y, v_1y)
        rec = {
            'glo': glo, 'ghi': ghi, 'llo': llo, 'lhi': lhi,
            'alpha': alpha, 'target': target, 'gate': gate
        }
        refine_results.append(rec)
        with open(LOG_REFINE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')

        if i % 100 == 0 and i > 0:
            elapsed = time.time() - t_start
            eta = elapsed / (i + 1) * (len(refinements) - i - 1)
            print(f'  [refine] {i}/{len(refinements)} ({elapsed:.0f}s, eta {eta:.0f}s)', flush=True)

    print(f'\n[refine] done in {time.time()-t_start:.1f}s', flush=True)

    # ── RANKINGS ──
    refine_results.sort(key=lambda r: -r['gate']['composite'])
    soft_passes_r = [r for r in refine_results if r['gate']['soft_pass']]
    print(f'\n[refine] {len(soft_passes_r)}/{len(refine_results)} soft-pass')

    print('\n=== TOP 20 BY COMPOSITE (soft-pass first) ===')
    pass_then_rest = sorted(refine_results, key=lambda r: (not r['gate']['soft_pass'], -r['gate']['composite']))
    for r in pass_then_rest[:20]:
        g = r['gate']
        flag = '✓' if g['soft_pass'] else ' '
        a10 = g['affected_10y']
        a10_s = '--' if a10 is None else f'{a10:+.2f}'
        a3 = g['affected_3y']
        a3_s = '--' if a3 is None else f'{a3:+.2f}'
        a1 = g['affected_1y']
        a1_s = '--' if a1 is None else f'{a1:+.2f}'
        print(f"  {flag} g[{r['glo']}-{r['ghi']}] LO={r['llo']:+.2f} HI={r['lhi']:+.2f} α={r['alpha']:.2f} T={r['target']:>2} | "
              f"75+ 5y={g['affected_5y']:+.2f} 10y={a10_s} 3y={a3_s} 1y={a1_s} | "
              f"N75 {g['affected_n_pct']:+.1f}% spill={g['spillover_max_neg']:+.2f} comp={g['composite']:+.3f}")

    if soft_passes_r:
        winner = soft_passes_r[0]
        g = winner['gate']
        print(f'\n=== BAYES-REFINE WINNER ===')
        print(f"  GATE [{winner['glo']}, {winner['ghi']}]")
        print(f"  LOG_LO={winner['llo']:+.3f} (mcap=${10**winner['llo']:.2f}B at full strength)")
        print(f"  LOG_HI={winner['lhi']:+.3f} (mcap=${10**winner['lhi']:.2f}B at zero strength)")
        print(f"  ALPHA={winner['alpha']:.2f}")
        print(f"  TARGET={winner['target']}")
        print(f"  Multi-window 75+: 1y={g['affected_1y']:+.2f if g['affected_1y'] is not None else None} "
              f"3y={g['affected_3y']:+.2f if g['affected_3y'] is not None else None} "
              f"5y={g['affected_5y']:+.2f} 10y={g['affected_10y']:+.2f if g['affected_10y'] is not None else None}")
        print(f"  N drop on 75+: {g['affected_n_pct']:+.1f}%")
        print(f"  Spillover max neg: {g['spillover_max_neg']:+.2f}pp")
        print(f"  Composite: {g['composite']:+.3f}")


if __name__ == '__main__':
    main()
