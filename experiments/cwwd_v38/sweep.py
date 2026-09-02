"""CWWD (Call Weak-Weekly Dampener) — score-stage extension of CWCF below 75.

Mirror of call_wcf_mirror_sweep.py but gates on `70 <= overall < 75`. Targets
the v36/v37 wadj-neg miss residue at 70-74 (z=+9.2, miss 52.7%, N=1537/5y) that
CWCF leaves untreated since CWCF gate is `overall >= 75`.

Mechanism (gradient over thresholds, mirroring v34/v36 CSWC shape):

    if 70 <= overall < 75 and wadj < 0:
        # Gradient on stoch: 0 at stoch=GE_FLOOR, 1 at stoch=GE_FLOOR+RAMP
        stoch_grad = clip((stoch - GE_FLOOR) / RAMP, 0, 1)
        # Gradient on wadj: 0 at wadj=0, 1 at wadj=-WADJ_K
        wadj_grad  = clip(-wadj / WADJ_K, 0, 1)
        weakness   = stoch_grad * wadj_grad
        overall   -= ALPHA * weakness * (overall - 55)

Calibration goal: dampen the wadj-neg cohort firmly below 70 (out of cascade)
while leaving wadj-mild signals untouched. Match the filter's behavior with a
smooth gradient.

Per-trade gate (sub-75 H1 fix per assessment-backtest.md):
  - 70+ cumulative TP%: must improve (affected tier)
  - 75+/80+/85+/90+/95+: within ±0.3pp (no spillover)
  - 70+ N drop: ≤ ~15%
  - Multi-window: sign-consistent 1y/3y/5y on 70+

Output: console table + JSONL log of all variants.
"""
from __future__ import annotations
import io, sys, time, math, json
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
RUNNER_CACHE = ROOT / '.cache' / 'runner'
LOG = Path(__file__).parent / 'sweep.jsonl'

LOOKBACK_DAYS = 1825
CALL_BUCKETS = [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]
PUT_BUCKETS  = [(25, '<25'), (20, '<20'), (15, '<15'), (10, '<10'), (5, '<5')]


def load_data(version_id):
    scores_pq = RUNNER_CACHE / f'scores_v{version_id}_{LOOKBACK_DAYS}.parquet'
    barriers_pq = RUNNER_CACHE / f'barriers_30opt_w15_{LOOKBACK_DAYS}.parquet'
    scores = pl.read_parquet(scores_pq)
    barriers = pl.read_parquet(barriers_pq).select(['symbol', 'date', 'side', 'result'])
    print(f'[load] scores={len(scores):,} barriers={len(barriers):,}', flush=True)
    return scores, barriers


def apply_cwwd(df, alpha, wadj_k, ge_floor, ramp):
    """Apply CWWD dampener: gate 70-74, wadj<0, stoch>=GE_FLOOR (gradient).

    Need stoch column — load from MySQL since runner cache doesn't have it.
    Returns df with new_overall.
    """
    # CWWD only affects 70 <= overall < 75 ∧ wadj < 0.
    # weakness = clip((stoch - GE_FLOOR)/RAMP, 0,1) * clip(-wadj / WADJ_K, 0, 1)
    # new = overall - alpha * weakness * (overall - 55)
    if alpha <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))

    stoch_grad = ((pl.col('stoch') - ge_floor) / ramp).clip(0.0, 1.0)
    wadj_grad  = (-pl.col('wadj') / wadj_k).clip(0.0, 1.0)
    weakness   = (stoch_grad * wadj_grad).clip(0.0, 1.0)
    in_zone = (pl.col('overall') >= 70) & (pl.col('overall') < 75) & (pl.col('wadj') < 0.0)

    df = df.with_columns(
        pl.when(in_zone)
          .then(pl.col('overall') - alpha * weakness * (pl.col('overall') - 55))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )
    return df


def evaluate(scores, barriers, window_start, alpha, wadj_k, ge_floor, ramp):
    """Per-bucket TP%/N for the CWWD variant in the given window."""
    df = scores.filter(pl.col('date') >= window_start)
    df = apply_cwwd(df, alpha, wadj_k, ge_floor, ramp)
    df = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df = df.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )
    joined = df.join(barriers, on=['symbol', 'date', 'side'], how='inner')
    out = {}
    for thr, label in CALL_BUCKETS:
        sub = joined.filter(pl.col('new_overall') >= thr)
        n = len(sub)
        wins = int((sub['result'] == 1).sum()) if n > 0 else 0
        out[label] = {'n': n, 'tp_pct': (wins / n * 100) if n > 0 else None}
    for thr, label in PUT_BUCKETS:
        sub = joined.filter(pl.col('new_overall') <= thr)
        n = len(sub)
        wins = int((sub['result'] == 1).sum()) if n > 0 else 0
        out[label] = {'n': n, 'tp_pct': (wins / n * 100) if n > 0 else None}
    return out


def gate_check(base_5y, var_5y, base_3y, var_3y, base_1y, var_1y):
    """Sub-75 affected-tier gate per assessment-backtest.md."""
    out = {}
    # Affected tier: 70+
    b70 = base_5y['70+']; v70 = var_5y['70+']
    out['affected_delta'] = (v70['tp_pct'] - b70['tp_pct']) if (b70['tp_pct'] and v70['tp_pct']) else 0
    out['affected_n_pct'] = ((v70['n'] - b70['n']) / b70['n'] * 100) if b70['n'] else 0
    # Spillover: 75+ ... 95+
    spillover = []
    for thr in ('75+', '80+', '85+', '90+', '95+'):
        b = base_5y[thr]; v = var_5y[thr]
        if b['tp_pct'] is None or v['tp_pct'] is None:
            spillover.append((thr, None)); continue
        spillover.append((thr, v['tp_pct'] - b['tp_pct']))
    out['spillover'] = spillover
    # Multi-window sign on 70+
    d_3y = (var_3y['70+']['tp_pct'] - base_3y['70+']['tp_pct']) if (base_3y['70+']['tp_pct'] and var_3y['70+']['tp_pct']) else None
    d_1y = (var_1y['70+']['tp_pct'] - base_1y['70+']['tp_pct']) if (base_1y['70+']['tp_pct'] and var_1y['70+']['tp_pct']) else None
    out['mw_3y'] = d_3y; out['mw_1y'] = d_1y
    # Soft pass: affected positive, spillover small, mw consistent
    soft_pass = (
        out['affected_delta'] >= 0.3
        and all((d is None or abs(d) <= 0.4) for _, d in spillover)
        and out['affected_n_pct'] >= -15.0
        and (d_3y is None or d_3y >= -0.2)
        and (d_1y is None or d_1y >= -0.5)
    )
    out['soft_pass'] = soft_pass
    return out


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'[sweep] active version: id={av.id} commit={av.git_commit}', flush=True)

    # Need stoch in scores parquet — augment via MySQL
    scores, barriers = load_data(av.id)
    if 'stoch' not in scores.columns:
        print('[sweep] augmenting scores parquet with stoch column from MySQL...', flush=True)
        from database.trader_database import DB
        cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        cur = DB.execute_sql(f"""
            SELECT s.symbol, s.date, s.stoch
            FROM scores s
            WHERE s.version_id = {av.id} AND s.date >= '{cutoff}'
        """)
        rows = cur.fetchall()
        stoch_df = pl.DataFrame({
            'symbol': [r[0] for r in rows],
            'date':   [r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1]) for r in rows],
            'stoch':  [int(r[2]) if r[2] is not None else 50 for r in rows],
        })
        print(f'[sweep] stoch rows: {len(stoch_df):,}', flush=True)
        scores = scores.join(stoch_df, on=['symbol', 'date'], how='left')
        scores = scores.with_columns(pl.col('stoch').fill_null(50))

    today = date.today()
    w5y = (today - timedelta(days=1825)).isoformat()
    w3y = (today - timedelta(days=1095)).isoformat()
    w1y = (today - timedelta(days=365)).isoformat()

    # Baseline: ALPHA=0 (no dampener)
    print('\n[baseline] computing...', flush=True)
    base_5y = evaluate(scores, barriers, w5y, 0, 5, 35, 35)
    base_3y = evaluate(scores, barriers, w3y, 0, 5, 35, 35)
    base_1y = evaluate(scores, barriers, w1y, 0, 5, 35, 35)
    print(f'[baseline 5y] CALL TP%:')
    for thr, label in CALL_BUCKETS:
        b = base_5y[label]
        if b['tp_pct'] is not None:
            print(f"  {label:>6} | N={b['n']:>5} TP={b['tp_pct']:.1f}%")
        else:
            print(f"  {label:>6} | N={b['n']} (no data)")

    # ── Variant grid ───────────────────────────────────────────────────────
    # Anchor: ALPHA=0.95 (mirror CWCF), WADJ_K=5 (narrow cutoff matching D),
    #          GE_FLOOR=35 (matches D's stoch_ge), RAMP=35 (gradient saturates by stoch=70).
    candidates = [
        # Anchor with multiple ALPHA / RAMP variations
        ('A_a95_w5_g35_r35',  0.95, 5, 35, 35),  # base anchor
        ('A_a95_w5_g35_r25',  0.95, 5, 35, 25),  # tighter ramp (saturates faster)
        ('A_a95_w5_g35_r50',  0.95, 5, 35, 50),  # looser ramp (more gradual)
        ('A_a95_w3_g35_r35',  0.95, 3, 35, 35),  # narrower wadj cutoff (-3 saturates)
        ('A_a95_w7_g35_r35',  0.95, 7, 35, 35),  # wider wadj cutoff (-7 saturates)
        ('A_a70_w5_g35_r35',  0.70, 5, 35, 35),  # gentler alpha
        ('A_a50_w5_g35_r35',  0.50, 5, 35, 35),  # very gentle
        ('A_a95_w5_g25_r35',  0.95, 5, 25, 35),  # earlier stoch floor
        ('A_a95_w5_g45_r35',  0.95, 5, 45, 35),  # later stoch floor
        # No stoch gate (GE_FLOOR=0, RAMP=1 → grad always ~1 for stoch>0)
        ('B_a95_w5_no_stoch', 0.95, 5,  0,  1),
        ('B_a95_w3_no_stoch', 0.95, 3,  0,  1),
        ('B_a70_w5_no_stoch', 0.70, 5,  0,  1),
        # Composite shapes
        ('C_a95_w7_g30_r40',  0.95, 7, 30, 40),  # wider gate, broader gradient
        ('C_a95_w3_g40_r30',  0.95, 3, 40, 30),  # narrower gate, sharper gradient
    ]

    if LOG.exists(): LOG.unlink()

    print(f'\n[sweep] evaluating {len(candidates)} candidates...\n', flush=True)
    results = []
    for label, alpha, wadj_k, ge_floor, ramp in candidates:
        t0 = time.time()
        v_5y = evaluate(scores, barriers, w5y, alpha, wadj_k, ge_floor, ramp)
        v_3y = evaluate(scores, barriers, w3y, alpha, wadj_k, ge_floor, ramp)
        v_1y = evaluate(scores, barriers, w1y, alpha, wadj_k, ge_floor, ramp)
        gate = gate_check(base_5y, v_5y, base_3y, v_3y, base_1y, v_1y)
        dur = time.time() - t0
        rec = {
            'label': label, 'alpha': alpha, 'wadj_k': wadj_k,
            'ge_floor': ge_floor, 'ramp': ramp,
            'gate': gate, 'v_5y': v_5y, 'dur': dur,
        }
        results.append(rec)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')
        flag = '✓' if gate['soft_pass'] else ' '
        spillover_max = max((abs(d) for _, d in gate['spillover'] if d is not None), default=0.0)
        mw3 = gate['mw_3y']; mw1 = gate['mw_1y']
        mw3_s = '--' if mw3 is None else f'{mw3:+.2f}'
        mw1_s = '--' if mw1 is None else f'{mw1:+.2f}'
        print(f"  {label:24s} α={alpha} W={wadj_k} G={ge_floor:>2} R={ramp:>2} | "
              f"{flag} 70+ Δ={gate['affected_delta']:>+5.2f}pp / N {gate['affected_n_pct']:>+5.1f}% | "
              f"max spillover ±{spillover_max:.2f}pp | "
              f"3y={mw3_s} 1y={mw1_s} ({dur:.1f}s)", flush=True)

    # Final ranking
    print('\n' + '='*100)
    print('SHIP CANDIDATES (soft-pass) — ranked by 70+ Δ')
    print('='*100)
    passed = [r for r in results if r['gate']['soft_pass']]
    passed.sort(key=lambda r: -r['gate']['affected_delta'])
    for r in passed:
        g = r['gate']
        print(f"  {r['label']:24s} α={r['alpha']} W={r['wadj_k']} G={r['ge_floor']:>2} R={r['ramp']:>2} | "
              f"70+ Δ={g['affected_delta']:+.2f}pp N {g['affected_n_pct']:+.1f}% | "
              f"3y {g['mw_3y']:+.2f} 1y {g['mw_1y']:+.2f}")

    # Detailed view of top candidate
    if passed:
        winner = passed[0]
        print(f'\n--- TOP CANDIDATE: {winner["label"]} (α={winner["alpha"]} W={winner["wadj_k"]} '
              f'G={winner["ge_floor"]} R={winner["ramp"]}) ---')
        print('  CALLS (5y):')
        for thr, lbl in CALL_BUCKETS:
            b = base_5y[lbl]; v = winner['v_5y'][lbl]
            if b['tp_pct'] is None or v['tp_pct'] is None:
                print(f"    {lbl:>6} | base N={b['n']} | (no data)")
                continue
            d = v['tp_pct'] - b['tp_pct']
            d_n = (v['n'] - b['n']) / b['n'] * 100 if b['n'] else 0
            print(f"    {lbl:>6} | N {v['n']:>5} (Δ{d_n:+.1f}%) | TP%={v['tp_pct']:>5.1f} (Δ{d:+.2f}pp)")
        print('  PUTS (5y) — should be unchanged:')
        for thr, lbl in [(25, '<25'), (15, '<15'), (5, '<5')]:
            b = base_5y[lbl]; v = winner['v_5y'][lbl]
            if b['tp_pct'] is None or v['tp_pct'] is None: continue
            d = v['tp_pct'] - b['tp_pct']
            print(f"    {lbl:>6} | N {v['n']:>5} | TP%={v['tp_pct']:>5.1f} (Δ{d:+.2f}pp)")


if __name__ == '__main__':
    main()
