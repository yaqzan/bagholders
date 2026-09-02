"""Call-side WCF-mirror lift — calibration sweep + H1-H5 ship-gate evaluation.

Mirrors the v27 put WCF lift to the call side:

    if overall >= call_gate AND wadj < call_wadj_cutoff:
        weakness = clip((call_wadj_cutoff - wadj) / |call_wadj_cutoff|, 0, 1)
        new_overall = overall - K * weakness * (overall - call_lift_target)

Validated against the **option-aligned 30dte_opt barriers at w=15d** (TP=+0.35, SL=-0.30
calls / -0.20 puts) — this measures TP% directly, the H1 gate metric in
known-issues.md.

H1 gate threshold:
    >= +0.5pp on >=3 of {95+, 90+, 85+, 80+, 75+} at 5y; none regress > -1.0pp

H5 multi-window:
    Sign of TP% delta consistent across 1y, 3y, 5y windows.

Usage:
    python experiments/miss_ledger/call_wcf_mirror_sweep.py
"""
from __future__ import annotations
import io, sys, time, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
RUNNER_CACHE = ROOT / '.cache' / 'runner'

LOOKBACK_DAYS = 1825
CALL_BUCKETS = [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]
PUT_BUCKETS  = [(25, '<25'), (20, '<20'), (15, '<15'), (10, '<10'), (5, '<5')]


def load_data(version_id):
    scores_pq = RUNNER_CACHE / f'scores_v{version_id}_{LOOKBACK_DAYS}.parquet'
    barriers_pq = RUNNER_CACHE / f'barriers_30opt_w15_{LOOKBACK_DAYS}.parquet'
    if not scores_pq.exists():
        raise RuntimeError(f'Missing {scores_pq}')
    if not barriers_pq.exists():
        raise RuntimeError(f'Missing {barriers_pq}')

    scores = pl.read_parquet(scores_pq)
    barriers = pl.read_parquet(barriers_pq).select(
        ['symbol', 'date', 'side', 'result']
    )
    print(f'[load] scores={len(scores):,} barriers={len(barriers):,}', flush=True)
    return scores, barriers


def apply_call_mirror(df, call_k, call_wadj_cutoff, call_gate, call_lift_target):
    """Apply call-side wcf-mirror dampener. Returns df with new_overall."""
    if call_k <= 0:
        return df.with_columns(pl.col('overall').alias('new_overall'))
    cw = ((call_wadj_cutoff - pl.col('wadj')) / abs(call_wadj_cutoff)).clip(0.0, 1.0)
    df = df.with_columns(cw.alias('weakness_c'))
    df = df.with_columns(
        pl.when((pl.col('overall') >= call_gate) & (pl.col('weakness_c') > 0))
          .then(pl.col('overall') - call_k * pl.col('weakness_c') * (pl.col('overall') - call_lift_target))
          .otherwise(pl.col('overall'))
          .round().clip(0, 100).cast(pl.Int64)
          .alias('new_overall')
    )
    return df


def evaluate(scores, barriers, window_start, call_k, call_wadj_cutoff, call_gate, call_lift_target):
    """Return per-bucket TP%/N for the variant in the given window."""
    df = scores.filter(pl.col('date') >= window_start)
    df = apply_call_mirror(df, call_k, call_wadj_cutoff, call_gate, call_lift_target)

    # Filter to peaks
    df = df.filter((pl.col('new_overall') >= 70) | (pl.col('new_overall') <= 25))
    df = df.with_columns(
        pl.when(pl.col('new_overall') >= 50).then(pl.lit('low')).otherwise(pl.lit('high'))
          .alias('side')
    )
    joined = df.join(barriers, on=['symbol', 'date', 'side'], how='inner')

    out = {}
    for thr, label in CALL_BUCKETS:
        sub = joined.filter(pl.col('new_overall') >= thr)
        n = len(sub)
        wins = int((sub['result'] == 1).sum()) if n > 0 else 0
        tp_pct = (wins / n * 100) if n > 0 else None
        out[label] = {'n': n, 'tp_pct': tp_pct}
    for thr, label in PUT_BUCKETS:
        sub = joined.filter(pl.col('new_overall') <= thr)
        n = len(sub)
        wins = int((sub['result'] == 1).sum()) if n > 0 else 0
        tp_pct = (wins / n * 100) if n > 0 else None
        out[label] = {'n': n, 'tp_pct': tp_pct}
    return out


def fmt_row(label, baseline, variant, n_baseline_total=None):
    bn = baseline['n']; vn = variant['n']
    btp = baseline['tp_pct']; vtp = variant['tp_pct']
    if btp is None or vtp is None:
        return f"  {label:>6} | n={vn:>5} (base={bn:>5}) | -- | --"
    delta = vtp - btp
    n_pct = (vn - bn) / bn * 100 if bn > 0 else 0
    flag = '✓' if delta >= 0.5 else ('✗' if delta < -1.0 else ' ')
    return f"  {label:>6} | n={vn:>5} (Δ{n_pct:+.1f}%) | TP%={vtp:>5.1f} (Δ{delta:+.2f}pp) {flag}"


def h1_h5_gate(baseline_5y, variant_5y, baseline_3y, variant_3y, baseline_1y, variant_1y):
    """Return (passed, summary)."""
    call_labels = ['95+', '90+', '85+', '80+', '75+']
    put_labels = ['<25', '<15']

    n_pass = 0
    n_regress = 0
    deltas = []
    for label in call_labels:
        b = baseline_5y[label]; v = variant_5y[label]
        if b['tp_pct'] is None or v['tp_pct'] is None:
            continue
        d = v['tp_pct'] - b['tp_pct']
        deltas.append((label, d))
        if d >= 0.5:
            n_pass += 1
        if d < -1.0:
            n_regress += 1
    h1 = (n_pass >= 3 and n_regress == 0)

    # H4: puts neutral or better
    h4 = True
    for label in put_labels:
        b = baseline_5y[label]; v = variant_5y[label]
        if b['tp_pct'] is None or v['tp_pct'] is None:
            continue
        d = v['tp_pct'] - b['tp_pct']
        if d < -0.5:
            h4 = False

    # H5: multi-window sign consistency
    h5 = True
    for label in call_labels:
        for window in (variant_3y, variant_1y):
            b_w = baseline_3y[label] if window is variant_3y else baseline_1y[label]
            if b_w['tp_pct'] is None or window[label]['tp_pct'] is None:
                continue
            d_w = window[label]['tp_pct'] - b_w['tp_pct']
            d_5y = next((d for l, d in deltas if l == label), 0)
            # Sign consistency: if 5y is positive, multi-window shouldn't be < -0.5
            if d_5y > 0 and d_w < -0.5:
                h5 = False

    # H3: N stability — no primary tier drops > 15%
    h3 = True
    for label in call_labels:
        b = baseline_5y[label]; v = variant_5y[label]
        if b['n'] > 0 and v['n'] / b['n'] < 0.85:
            h3 = False

    passed = h1 and h3 and h4 and h5
    return passed, {'H1': h1, 'H3': h3, 'H4': h4, 'H5': h5,
                    'n_call_pass': n_pass, 'n_call_regress': n_regress,
                    'call_deltas': deltas}


def main():
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_active_scores_version()
    print(f'[sweep] active version: id={av.id} commit={av.git_commit}', flush=True)

    scores, barriers = load_data(av.id)

    today = date.today()
    w5y = (today - timedelta(days=1825)).isoformat()
    w3y = (today - timedelta(days=1095)).isoformat()
    w1y = (today - timedelta(days=365)).isoformat()

    # Baseline (no dampener)
    print('\n[baseline] computing...', flush=True)
    base_5y = evaluate(scores, barriers, w5y, 0.0, 13.0, 75, 65)
    base_3y = evaluate(scores, barriers, w3y, 0.0, 13.0, 75, 65)
    base_1y = evaluate(scores, barriers, w1y, 0.0, 13.0, 75, 65)
    print(f'[baseline 5y] CALL TP%:')
    for thr, label in CALL_BUCKETS:
        b = base_5y[label]
        print(f"  {label:>6} | N={b['n']:>5} TP={b['tp_pct']:.1f}%" if b['tp_pct'] else f"  {label}: N={b['n']} (insufficient)")

    # Variant grid
    # Axis: K (lift coefficient), wadj_cutoff (threshold below which weakness fires),
    #       gate (min overall to fire), lift_target (pull toward this).
    # Anchor at the v27 put-side: K=0.95, wadj_cutoff=-17, gate=28, target=50.
    # Mirror: K=0.95, wadj_cutoff=+17, gate=72, target=50.
    # Sweep around it.
    # Fine sweep around the winning region: cut~1-7 (only fire when wadj weakly bullish or
    # negative), gate=75, target=55-62, K=0.5-1.0.
    candidates = [
        # Anchor (passing variants from initial sweep)
        ('A_K70_C01_G75_T55', 0.70,  1, 75, 55),
        ('A_K95_C01_G75_T55', 0.95,  1, 75, 55),
        # Extend wadj_cutoff (still narrow)
        ('B_K70_C03_G75_T55', 0.70,  3, 75, 55),
        ('B_K70_C05_G75_T55', 0.70,  5, 75, 55),
        ('B_K70_C07_G75_T55', 0.70,  7, 75, 55),
        ('B_K95_C03_G75_T55', 0.95,  3, 75, 55),
        ('B_K95_C05_G75_T55', 0.95,  5, 75, 55),
        ('B_K95_C07_G75_T55', 0.95,  7, 75, 55),
        # Different K
        ('C_K50_C05_G75_T55', 0.50,  5, 75, 55),
        ('C_K50_C07_G75_T55', 0.50,  7, 75, 55),
        ('C_K100_C03_G75_T55', 1.00, 3, 75, 55),
        ('C_K100_C05_G75_T55', 1.00, 5, 75, 55),
        # Different target — gentler pulls
        ('D_K95_C05_G75_T58', 0.95,  5, 75, 58),
        ('D_K95_C05_G75_T60', 0.95,  5, 75, 60),
        ('D_K95_C03_G75_T58', 0.95,  3, 75, 58),
        ('D_K95_C07_G75_T58', 0.95,  7, 75, 58),
        # gate=80 (preserve 75-79 untouched)
        ('E_K95_C05_G80_T55', 0.95,  5, 80, 55),
        ('E_K95_C05_G80_T58', 0.95,  5, 80, 58),
    ]

    print(f'\n[sweep] testing {len(candidates)} candidates...\n', flush=True)
    results = []
    for label, K, cut, gate, tgt in candidates:
        t0 = time.time()
        v_5y = evaluate(scores, barriers, w5y, K, cut, gate, tgt)
        v_3y = evaluate(scores, barriers, w3y, K, cut, gate, tgt)
        v_1y = evaluate(scores, barriers, w1y, K, cut, gate, tgt)
        passed, gate_info = h1_h5_gate(base_5y, v_5y, base_3y, v_3y, base_1y, v_1y)
        dur = time.time() - t0
        results.append((label, K, cut, gate, tgt, v_5y, v_3y, v_1y, passed, gate_info, dur))
        flag = '✓ SHIP' if passed else ' '
        print(f'  {label:30s} K={K} cut={cut:>3} gate={gate} tgt={tgt} | '
              f'{flag} | H1pass={gate_info["n_call_pass"]} regress={gate_info["n_call_regress"]} '
              f'H3={gate_info["H3"]} H4={gate_info["H4"]} H5={gate_info["H5"]} ({dur:.1f}s)', flush=True)

    # Report
    print('\n' + '='*100)
    print('PER-VARIANT PER-BUCKET (5y) — sorted by # call tiers passing')
    print('='*100)

    results.sort(key=lambda r: (r[8], r[9]['n_call_pass'], -r[9]['n_call_regress']), reverse=True)

    for label, K, cut, gate, tgt, v_5y, v_3y, v_1y, passed, gate_info, dur in results[:6]:
        print(f'\n--- {label} (K={K} cut={cut} gate={gate} tgt={tgt}) {"✓ SHIPS" if passed else "[fail]"}')
        print('  CALLS (5y):')
        for thr, lbl in CALL_BUCKETS:
            print(fmt_row(lbl, base_5y[lbl], v_5y[lbl]))
        print('  PUTS (5y):')
        for thr, lbl in [(25, '<25'), (15, '<15'), (5, '<5')]:
            print(fmt_row(lbl, base_5y[lbl], v_5y[lbl]))
        # Multi-window check on 75+
        for win, b, v in [('1y', base_1y, v_1y), ('3y', base_3y, v_3y)]:
            print(f'  CALL 75+ {win}: '
                  f'baseN={b["75+"]["n"]:>4} bTP={b["75+"]["tp_pct"]:.1f}% | '
                  f'varN={v["75+"]["n"]:>4} vTP={v["75+"]["tp_pct"]:.1f}% '
                  f'Δ={v["75+"]["tp_pct"]-b["75+"]["tp_pct"]:+.2f}pp')

    print('\n' + '='*100)
    print('SHIP-CANDIDATE SUMMARY')
    print('='*100)
    for label, K, cut, gate, tgt, v_5y, v_3y, v_1y, passed, gate_info, _ in results:
        if not passed:
            continue
        print(f'  {label:30s} | call_deltas (5y): ' +
              ' '.join(f'{l}={d:+.2f}' for l, d in gate_info['call_deltas']))

    print('\n[sweep] done.', flush=True)


if __name__ == '__main__':
    main()
