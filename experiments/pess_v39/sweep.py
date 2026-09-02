"""PESS (Put Earnings Score Suppression) — score-stage replacement for the
EARN_SUPP_PUT cascade-stage filter.

The filter drops puts in [16,20] when an EarningsDate falls within 5 trd days
post-signal.  PESS encodes the same judgment in the score itself: lift these
signals out of the put-qualifying universe (>25) so they don't show on the
dashboard as red badges and don't enter cascade.

Mechanism (gradient over thresholds):

    if 16 <= overall <= 20 and 1 <= days_to_ern <= 7:
        # Score gradient: peaks at 18, fades at 14, 22
        score_grad = clip( ... , 0, 1)
        # Proximity gradient: 1.0 at d=1-3, fades 0 at d=8
        proximity = clip( ... , 0, 1)
        weakness = score_grad * proximity
        overall += ALPHA * weakness * (TARGET - overall)

Applied BEFORE EARN_BOOST so that signals lifted past 25 don't re-amplify
on the put side (EARN_BOOST gate is overall<=25).

Per-trade gate (sub-25 H1 fix per assessment-backtest.md):
  - <25 cumulative TP%: must improve (affected tier)
  - <20, <15, <10, <5: within ±0.5pp (only the upper-edge of <25 should change)
  - <25 N drop: ≤ ~15%
  - Multi-window: sign-consistent 1y/3y/5y on <25
  - Calls: byte-identical (gate is puts-only)
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
    return scores, barriers


def load_earnings_proximity(version_id, scores):
    """For each (symbol, date) in scores, compute days to next earnings.
    Returns scores with `d_to_ern` column added (None if >30 days away)."""
    from database.trader_database import DB
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS + 60)).isoformat()
    cur = DB.execute_sql(f"""
        SELECT e.symbol, e.effective_date
        FROM earnings_dates e
        WHERE e.effective_date >= '{cutoff}' AND e.effective_date IS NOT NULL
        ORDER BY e.symbol, e.effective_date
    """)
    rows = cur.fetchall()
    print(f'[earnings] loaded {len(rows):,} earnings dates', flush=True)

    # Build per-symbol sorted list
    from collections import defaultdict
    ern_by_sym = defaultdict(list)
    for sym, ed in rows:
        ern_by_sym[sym].append(ed.toordinal() if hasattr(ed, 'toordinal') else ed)

    # Trading-calendar approximation: weekdays only.
    # For each (symbol, date), find next earnings; compute trading days between.
    # Use the same approximation as the EARN_SUPP_PUT filter for fairness.
    from database.utils.trading_calendar import is_trading_day

    sym_col = scores['symbol'].to_list()
    date_col = scores['date'].to_list()
    d_to_ern = []
    for sym, d_str in zip(sym_col, date_col):
        s_date = date.fromisoformat(d_str) if isinstance(d_str, str) else d_str
        ern_dates = ern_by_sym.get(sym, [])
        if not ern_dates:
            d_to_ern.append(None); continue
        s_ord = s_date.toordinal()
        # Find first earnings strictly after s_date
        nxt = None
        for ed_ord in ern_dates:
            if ed_ord > s_ord:
                nxt = ed_ord; break
        if nxt is None:
            d_to_ern.append(None); continue
        # Calendar diff
        cal_diff = nxt - s_ord
        if cal_diff > 30:
            d_to_ern.append(None); continue
        # Trading-day diff — quick approx via weekdays in [s+1, nxt]
        td = 0
        for i in range(1, cal_diff + 1):
            check_date = date.fromordinal(s_ord + i)
            if is_trading_day(check_date):
                td += 1
        d_to_ern.append(td if td > 0 else None)
    scores = scores.with_columns(pl.Series('d_to_ern', d_to_ern, dtype=pl.Int64))
    return scores


def apply_pess(df, alpha, target, prox_full, prox_fade_end, score_peak_lo, score_peak_hi, score_fade_w):
    """Apply PESS gradient lift.

    score_grad: 1.0 in [score_peak_lo, score_peak_hi], linear fade over score_fade_w on each side.
    proximity:  1.0 in [1, prox_full], linear fade to 0 at prox_fade_end+1.
    """
    if alpha <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))

    # Score gradient
    sgrad_lo = ((pl.col('overall') - (score_peak_lo - score_fade_w)) / score_fade_w).clip(0.0, 1.0)
    sgrad_hi = (((score_peak_hi + score_fade_w) - pl.col('overall')) / score_fade_w).clip(0.0, 1.0)
    score_grad = pl.min_horizontal([sgrad_lo, sgrad_hi]).clip(0.0, 1.0)
    # Out-of-range: zero
    score_grad = pl.when(
        (pl.col('overall') < score_peak_lo - score_fade_w) | (pl.col('overall') > score_peak_hi + score_fade_w)
    ).then(0.0).otherwise(score_grad)

    # Proximity gradient
    prox_grad = pl.when(pl.col('d_to_ern').is_null()).then(0.0)\
        .when(pl.col('d_to_ern') <= 0).then(0.0)\
        .when(pl.col('d_to_ern') <= prox_full).then(1.0)\
        .when(pl.col('d_to_ern') <= prox_fade_end).then(
            (prox_fade_end + 1 - pl.col('d_to_ern')) / (prox_fade_end - prox_full + 1)
        ).otherwise(0.0).clip(0.0, 1.0)

    weakness = (score_grad * prox_grad).clip(0.0, 1.0)
    in_zone = pl.col('d_to_ern').is_not_null()

    df = df.with_columns(
        pl.when(in_zone & (weakness > 0))
          .then(pl.col('overall') + alpha * weakness * (target - pl.col('overall')))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )
    return df


def evaluate(scores, barriers, window_start, alpha, target, prox_full, prox_fade_end,
             score_peak_lo, score_peak_hi, score_fade_w):
    df = scores.filter(pl.col('date') >= window_start)
    df = apply_pess(df, alpha, target, prox_full, prox_fade_end, score_peak_lo, score_peak_hi, score_fade_w)
    df = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df = df.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high')).alias('side')
    )
    joined = df.join(barriers, on=['symbol', 'date', 'side'], how='inner')
    out = {}
    for thr, label in CALL_BUCKETS:
        sub = joined.filter(pl.col('new_overall') >= thr)
        n = len(sub); wins = int((sub['result'] == 1).sum()) if n > 0 else 0
        out[label] = {'n': n, 'tp_pct': (wins/n*100) if n > 0 else None}
    for thr, label in PUT_BUCKETS:
        sub = joined.filter(pl.col('new_overall') <= thr)
        n = len(sub); wins = int((sub['result'] == 1).sum()) if n > 0 else 0
        out[label] = {'n': n, 'tp_pct': (wins/n*100) if n > 0 else None}
    return out


def gate_check(base_5y, var_5y, base_3y, var_3y, base_1y, var_1y):
    """Sub-25 affected-tier gate (puts)."""
    out = {}
    b25 = base_5y['<25']; v25 = var_5y['<25']
    out['affected_delta'] = (v25['tp_pct'] - b25['tp_pct']) if (b25['tp_pct'] and v25['tp_pct']) else 0
    out['affected_n_pct'] = ((v25['n'] - b25['n']) / b25['n'] * 100) if b25['n'] else 0
    spillover = []
    for thr in ('<20', '<15', '<10', '<5'):
        b = base_5y[thr]; v = var_5y[thr]
        if b['tp_pct'] is None or v['tp_pct'] is None:
            spillover.append((thr, None)); continue
        spillover.append((thr, v['tp_pct'] - b['tp_pct']))
    out['spillover'] = spillover
    # Calls should be unchanged
    call_drift = max(
        (abs(var_5y[t]['tp_pct'] - base_5y[t]['tp_pct']) for t in ('70+', '75+', '80+', '85+', '90+', '95+')
         if base_5y[t]['tp_pct'] and var_5y[t]['tp_pct']), default=0
    )
    out['call_drift'] = call_drift
    d_3y = (var_3y['<25']['tp_pct'] - base_3y['<25']['tp_pct']) if (base_3y['<25']['tp_pct'] and var_3y['<25']['tp_pct']) else None
    d_1y = (var_1y['<25']['tp_pct'] - base_1y['<25']['tp_pct']) if (base_1y['<25']['tp_pct'] and var_1y['<25']['tp_pct']) else None
    out['mw_3y'] = d_3y; out['mw_1y'] = d_1y
    soft_pass = (
        out['affected_delta'] >= 0.3
        and all((d is None or abs(d) <= 0.5) for _, d in spillover)
        and out['affected_n_pct'] >= -15.0
        and call_drift <= 0.05
        and (d_3y is None or d_3y >= -0.2)
        and (d_1y is None or d_1y >= -0.5)
    )
    out['soft_pass'] = soft_pass
    return out


def main():
    from database.models.core import AlgorithmVersion
    # Use v37 cache while v38 recalc is running.  PESS logic is identical
    # across versions; the population mix differs only marginally.
    av_pin_id = 37
    print(f'[sweep] using v37 cache (id={av_pin_id}) since v38 recalc in flight', flush=True)
    scores, barriers = load_data(av_pin_id)
    print(f'[sweep] augmenting with d_to_ern column...', flush=True)
    scores = load_earnings_proximity(av_pin_id, scores)
    print(f'[sweep] scores with d_to_ern populated: {scores.filter(pl.col("d_to_ern").is_not_null()).height:,}', flush=True)

    today = date.today()
    w5y = (today - timedelta(days=1825)).isoformat()
    w3y = (today - timedelta(days=1095)).isoformat()
    w1y = (today - timedelta(days=365)).isoformat()

    print('\n[baseline] (alpha=0)...', flush=True)
    base_5y = evaluate(scores, barriers, w5y, 0, 28, 3, 7, 17, 19, 3)
    base_3y = evaluate(scores, barriers, w3y, 0, 28, 3, 7, 17, 19, 3)
    base_1y = evaluate(scores, barriers, w1y, 0, 28, 3, 7, 17, 19, 3)
    print(f'[baseline 5y] PUT TP%:')
    for thr, label in PUT_BUCKETS:
        b = base_5y[label]
        if b['tp_pct'] is not None:
            print(f"  {label:>6} | N={b['n']:>5} TP={b['tp_pct']:.1f}%")

    # Variants — vary alpha, target, window, score-gradient shape
    candidates = [
        # (label, alpha, target, prox_full, prox_fade_end, score_peak_lo, score_peak_hi, score_fade_w)
        ('A_a95_t28_W3-7_p17-19', 0.95, 28, 3, 7, 17, 19, 3),  # anchor
        ('A_a95_t28_W5-7_p17-19', 0.95, 28, 5, 7, 17, 19, 3),  # full lift through d=5
        ('A_a95_t28_W5-7_p16-20', 0.95, 28, 5, 7, 16, 20, 2),  # tighter score gate match filter
        ('A_a95_t28_W5-7_p16-20_w3', 0.95, 28, 5, 7, 16, 20, 3),  # filter score range, gradient edges
        ('B_a70_t28_W5-7_p16-20', 0.70, 28, 5, 7, 16, 20, 2),  # gentler alpha
        ('C_a95_t30_W5-7_p16-20', 0.95, 30, 5, 7, 16, 20, 2),  # higher target
        ('C_a95_t26_W5-7_p16-20', 0.95, 26, 5, 7, 16, 20, 2),  # lower target
        ('D_a100_t28_W5-7_p16-20', 1.00, 28, 5, 7, 16, 20, 2),  # alpha=1.0 saturate
        ('D_a100_t30_W5-7_p16-20', 1.00, 30, 5, 7, 16, 20, 2),  # alpha=1.0, target=30
        ('E_a95_t28_W3-5_p16-20', 0.95, 28, 3, 5, 16, 20, 2),  # narrow window matching filter
        ('E_a95_t28_W3-5_p17-19_w2', 0.95, 28, 3, 5, 17, 19, 2),  # tighter both
    ]

    if LOG.exists(): LOG.unlink()

    print(f'\n[sweep] {len(candidates)} candidates...\n', flush=True)
    results = []
    for variant in candidates:
        label, alpha, target, pf, pe, slo, shi, sw = variant
        t0 = time.time()
        v_5y = evaluate(scores, barriers, w5y, alpha, target, pf, pe, slo, shi, sw)
        v_3y = evaluate(scores, barriers, w3y, alpha, target, pf, pe, slo, shi, sw)
        v_1y = evaluate(scores, barriers, w1y, alpha, target, pf, pe, slo, shi, sw)
        gate = gate_check(base_5y, v_5y, base_3y, v_3y, base_1y, v_1y)
        dur = time.time() - t0
        rec = {
            'label': label, 'alpha': alpha, 'target': target,
            'prox_full': pf, 'prox_fade_end': pe,
            'score_peak_lo': slo, 'score_peak_hi': shi, 'score_fade_w': sw,
            'gate': gate, 'v_5y': v_5y, 'dur': dur,
        }
        results.append(rec)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, default=str) + '\n')
        flag = '✓' if gate['soft_pass'] else ' '
        sp_max = max((abs(d) for _, d in gate['spillover'] if d is not None), default=0.0)
        mw3 = gate['mw_3y']; mw1 = gate['mw_1y']
        mw3_s = '--' if mw3 is None else f'{mw3:+.2f}'
        mw1_s = '--' if mw1 is None else f'{mw1:+.2f}'
        print(f"  {label:30s} a={alpha} t={target} pW={pf}-{pe} sp={slo}-{shi}/{sw} | "
              f"{flag} <25 Δ={gate['affected_delta']:>+5.2f}pp / N {gate['affected_n_pct']:>+5.1f}% | "
              f"sp ±{sp_max:.2f}pp | call ±{gate['call_drift']:.3f} | "
              f"3y={mw3_s} 1y={mw1_s} ({dur:.1f}s)", flush=True)

    print('\n' + '='*100)
    print('SHIP CANDIDATES — ranked by <25 Δ')
    print('='*100)
    passed = sorted([r for r in results if r['gate']['soft_pass']],
                    key=lambda r: -r['gate']['affected_delta'])
    for r in passed:
        g = r['gate']
        print(f"  {r['label']:30s} a={r['alpha']} t={r['target']} | <25 Δ={g['affected_delta']:+.2f}pp "
              f"N {g['affected_n_pct']:+.1f}% | mw 3y {g['mw_3y']:+.2f} 1y {g['mw_1y']:+.2f}")

    if passed:
        winner = passed[0]
        print(f'\n--- TOP CANDIDATE: {winner["label"]} ---')
        for thr, lbl in PUT_BUCKETS:
            b = base_5y[lbl]; v = winner['v_5y'][lbl]
            if b['tp_pct'] is None or v['tp_pct'] is None: continue
            d = v['tp_pct'] - b['tp_pct']
            d_n = (v['n'] - b['n']) / b['n'] * 100 if b['n'] else 0
            print(f"    {lbl:>6} | N {v['n']:>5} (Δ{d_n:+.1f}%) | TP%={v['tp_pct']:>5.1f} (Δ{d:+.2f}pp)")


if __name__ == '__main__':
    main()
