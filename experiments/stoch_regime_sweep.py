"""Stoch-Regime Dampener Sweep — v32 miss-ledger investigation.

Tests two independent scoring dampeners surfaced by v32 miss-ledger analysis:

CALL PATTERN  (z=+4.5, N=3,379, 46% of 75+ calls):
  stoch_comp < STOCH_GATE  AND  1 <= wadj < WADJ_GATE  AND  overall >= 75
  => Calls with bearish stochastic + barely-positive weekly.
     CWCF (v32) already handles wadj<1; this extension covers the mild-weekly residue.

  Dampener:
    stoch_w = clip((STOCH_GATE - stoch) / STOCH_GATE, 0, 1)
    wadj_w  = clip((WADJ_GATE  - wadj)  / (WADJ_GATE - 1), 0, 1)
    weakness = stoch_w * wadj_w
    new_overall = round(overall - K * weakness * (overall - LIFT_TARGET))

PUT PATTERN  (z=+4.9, N=3,615, 21% of <25 puts):
  stoch_comp > STOCH_GATE  AND  reg_comp > REGIME_GATE  AND  overall <= 25
  => Puts in healthy/bull market with overbought momentum = bounce traps.

  Dampener:
    stoch_w  = clip((stoch   - STOCH_GATE)  / (100 - STOCH_GATE),  0, 1)
    regime_w = clip((reg_comp - REGIME_GATE) / (100 - REGIME_GATE), 0, 1)
    weakness = stoch_w * regime_w
    new_overall = round(overall + K * weakness * (50 - overall))

Gates (H1-H5 for calls; H4-mirror, H3, H5 for puts):
  H1: >= +0.5pp on >= 3 call tiers at 5y; none regress > -1.0pp
  H3: N within +-15% per primary bucket at 5y
  H4-call: puts neutral or better (for call-side dampener)
  H4-put:  calls neutral or better (for put-side dampener)
  H5: TP% sign-consistent across 1y, 3y, 5y

Usage:
  python experiments/stoch_regime_sweep.py [--calls-only | --puts-only]
"""
from __future__ import annotations
import sys, io, time
from itertools import product
from pathlib import Path
from datetime import date, timedelta

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / '.cache' / 'miss_ledger' / 'ledger_v32_1825.parquet'

BUY_BUCKETS  = [('95+', 95, None), ('90+', 90, None), ('85+', 85, None),
                ('80+', 80, None), ('75+', 75, None), ('70+', 70, None)]
SELL_BUCKETS = [('<25', None, 25), ('<20', None, 20), ('<15', None, 15),
                ('<10', None, 10), ('<5',  None,  5)]

TODAY = date.fromisoformat('2026-05-04')
WINDOW_CUTOFFS = {
    '1y':  (TODAY - timedelta(days=365)).isoformat(),
    '3y':  (TODAY - timedelta(days=1095)).isoformat(),
    '5y':  (TODAY - timedelta(days=1825)).isoformat(),
}
PRIMARY_CALL_TIERS = ['95+', '90+', '85+', '80+', '75+']
PRIMARY_PUT_TIERS  = ['<25', '<20', '<15', '<10']

H1_MIN_IMPROVE_PP  = 0.5
H1_MIN_TIERS       = 3
H1_MAX_REGRESS_PP  = -1.0
H3_N_TOL           = 0.15   # ±15%


# ── helpers ─────────────────────────────────────────────────────────────────

def tp_rate(sub: pl.DataFrame) -> float:
    """Fraction of signals where result==1 (null and 0 both count as miss)."""
    n = len(sub)
    if n == 0:
        return float('nan')
    return float((sub['result'] == 1).sum()) / n


def bucket_stats(df: pl.DataFrame, score_col: str = 'new_overall') -> dict:
    """Compute N and TP% per (bucket, window)."""
    stats = {}
    for window, cutoff in WINDOW_CUTOFFS.items():
        sub = df.filter(pl.col('date') >= cutoff)
        for lbl, lo, hi in BUY_BUCKETS + SELL_BUCKETS:
            if lo is not None:
                s = sub.filter(pl.col(score_col) >= lo)
            else:
                s = sub.filter(pl.col(score_col) <= hi)
            n = len(s)
            stats[(lbl, window)] = {'n': n, 'tp': tp_rate(s)}
    return stats


def diff_table(baseline: dict, variant: dict,
               windows=('1y', '3y', '5y')) -> dict:
    """Compute ΔTP% and ΔN per (bucket, window)."""
    out = {}
    for key in baseline:
        b = baseline[key]; v = variant[key]
        b_tp = b['tp']; v_tp = v['tp']
        dtp = (v_tp - b_tp) * 100 if (not np.isnan(b_tp) and not np.isnan(v_tp)) else float('nan')
        dn_pct = (v['n'] - b['n']) / b['n'] if b['n'] > 0 else 0.0
        out[key] = {'dtp': dtp, 'dn_pct': dn_pct, 'n': v['n'], 'tp': v_tp}
    return out


def check_h1(diff: dict, primary_tiers=PRIMARY_CALL_TIERS) -> tuple[bool, str]:
    """H1: >=+0.5pp on >=3 primary call tiers at 5y; no tier regresses >-1pp."""
    gains, regressions = [], []
    for t in primary_tiers:
        d = diff.get((t, '5y'), {})
        dtp = d.get('dtp', float('nan'))
        if np.isnan(dtp): continue
        if dtp >= H1_MIN_IMPROVE_PP:
            gains.append(t)
        if dtp < H1_MAX_REGRESS_PP:
            regressions.append((t, dtp))
    pass_ = len(gains) >= H1_MIN_TIERS and len(regressions) == 0
    detail = f"gains={gains} regress={regressions}"
    return pass_, detail


def check_h3(diff: dict, primary_tiers=PRIMARY_CALL_TIERS + PRIMARY_PUT_TIERS) -> tuple[bool, str]:
    """H3: N within +-15% for primary buckets at 5y."""
    fails = []
    for t in primary_tiers:
        d = diff.get((t, '5y'), {})
        dn = d.get('dn_pct', 0.0)
        if abs(dn) > H3_N_TOL:
            fails.append((t, dn))
    return len(fails) == 0, f"out_of_range={fails}"


def check_h4_calls_neutral(diff: dict) -> tuple[bool, str]:
    """H4 (for call dampener): put buckets neutral or better at 5y."""
    regressions = []
    for t in PRIMARY_PUT_TIERS:
        d = diff.get((t, '5y'), {})
        dtp = d.get('dtp', float('nan'))
        if not np.isnan(dtp) and dtp < -0.5:
            regressions.append((t, dtp))
    return len(regressions) == 0, f"put_regress={regressions}"


def check_h4_puts_neutral(diff: dict) -> tuple[bool, str]:
    """H4 (for put dampener): call buckets neutral or better at 5y."""
    regressions = []
    for t in PRIMARY_CALL_TIERS:
        d = diff.get((t, '5y'), {})
        dtp = d.get('dtp', float('nan'))
        if not np.isnan(dtp) and dtp < -0.5:
            regressions.append((t, dtp))
    return len(regressions) == 0, f"call_regress={regressions}"


def check_h5_call(diff: dict) -> tuple[bool, str]:
    """H5: TP% sign-consistent 1y/3y/5y for primary call tiers."""
    fails = []
    for t in PRIMARY_CALL_TIERS:
        signs = {}
        for w in ('1y', '3y', '5y'):
            d = diff.get((t, w), {})
            dtp = d.get('dtp', float('nan'))
            if not np.isnan(dtp) and d.get('n', 0) >= 20:
                signs[w] = np.sign(dtp)
        vals = list(signs.values())
        if len(vals) >= 2 and len(set(vals)) > 1 and 0.0 not in set(vals):
            fails.append(t)
    return len(fails) == 0, f"inconsistent={fails}"


def check_h5_put(diff: dict) -> tuple[bool, str]:
    """H5 mirror for puts: sign-consistent 1y/3y/5y."""
    fails = []
    for t in PRIMARY_PUT_TIERS:
        signs = {}
        for w in ('1y', '3y', '5y'):
            d = diff.get((t, w), {})
            dtp = d.get('dtp', float('nan'))
            if not np.isnan(dtp) and d.get('n', 0) >= 20:
                signs[w] = np.sign(dtp)
        vals = list(signs.values())
        if len(vals) >= 2 and len(set(vals)) > 1 and 0.0 not in set(vals):
            fails.append(t)
    return len(fails) == 0, f"inconsistent={fails}"


def print_bucket_diff(diff: dict, label: str, primary_call=PRIMARY_CALL_TIERS,
                      primary_put=PRIMARY_PUT_TIERS):
    print(f"\n  {label}")
    print(f"  {'bucket':6s}  {'5y ΔTP':>8s}  {'5y N':>6s}  {'1y ΔTP':>8s}  {'3y ΔTP':>8s}")
    for t in primary_call:
        d5 = diff.get((t, '5y'), {}); d1 = diff.get((t, '1y'), {}); d3 = diff.get((t, '3y'), {})
        print(f"  {t:6s}  {d5.get('dtp', float('nan')):>+7.2f}pp  {d5.get('n', 0):>6d}  "
              f"{d1.get('dtp', float('nan')):>+7.2f}pp  {d3.get('dtp', float('nan')):>+7.2f}pp")
    print()
    for t in primary_put:
        d5 = diff.get((t, '5y'), {}); d1 = diff.get((t, '1y'), {}); d3 = diff.get((t, '3y'), {})
        print(f"  {t:6s}  {d5.get('dtp', float('nan')):>+7.2f}pp  {d5.get('n', 0):>6d}  "
              f"{d1.get('dtp', float('nan')):>+7.2f}pp  {d3.get('dtp', float('nan')):>+7.2f}pp")


# ── load data ────────────────────────────────────────────────────────────────

def load():
    print(f"[sweep] loading {LEDGER}", flush=True)
    df = pl.read_parquet(LEDGER)
    # Normalise result: null -> 0 (miss), keep 1 as win
    df = df.with_columns(
        pl.col('result').fill_null(0).cast(pl.Int8).alias('result')
    )
    print(f"[sweep] {len(df):,} rows, {df['date'].min()} to {df['date'].max()}", flush=True)
    return df


# ── CALL sweep: stoch-wadj dampener ─────────────────────────────────────────

CALL_PARAMS = {
    'stoch_gate':  [25, 30, 35],
    'wadj_gate':   [ 7, 10, 13],
    'K':           [0.40, 0.60, 0.80],
    'lift_target': [55],          # fixed: pull toward 55 (matches CWCF)
    'score_gate':  [75],          # only fire for 75+
}


def apply_call_dampener(df: pl.DataFrame, stoch_gate: int, wadj_gate: int,
                        K: float, lift_target: int = 55, score_gate: int = 75):
    """Apply stoch-wadj call dampener to produce new_overall."""
    stoch_w = ((pl.lit(stoch_gate) - pl.col('stoch')) / pl.lit(float(stoch_gate))).clip(0.0, 1.0)
    wadj_w  = ((pl.lit(wadj_gate)  - pl.col('wadj'))  / pl.lit(float(wadj_gate - 1))).clip(0.0, 1.0)
    weakness = stoch_w * wadj_w
    fire = (
        (pl.col('overall') >= score_gate) &
        (pl.col('stoch') < stoch_gate) &
        (pl.col('wadj') >= 1.0) &
        (pl.col('wadj') < wadj_gate) &
        (pl.col('signal_type') == 'CALL')
    )
    new_ov = (
        pl.when(fire)
          .then((pl.col('overall') - K * weakness * (pl.col('overall') - lift_target)).round())
          .otherwise(pl.col('overall'))
          .clip(0, 100).cast(pl.Int64)
    )
    return df.with_columns(new_ov.alias('new_overall'))


def run_call_sweep(df: pl.DataFrame, baseline_stats: dict):
    print("\n" + "="*80)
    print("CALL SWEEP — stoch-lo + wadj-mild dampener")
    print("Gates: H1 (call TP% +0.5pp on 3+ tiers), H3 (N +-15%), H4 (puts neutral), H5 (3-window consistent)")
    print("="*80)

    results = []
    for stoch_gate, wadj_gate, K, lift_target, score_gate in product(
        CALL_PARAMS['stoch_gate'], CALL_PARAMS['wadj_gate'],
        CALL_PARAMS['K'], CALL_PARAMS['lift_target'], CALL_PARAMS['score_gate']
    ):
        label = f"C_sg{stoch_gate}_wg{wadj_gate}_K{K:.2f}"
        t0 = time.perf_counter()
        vdf = apply_call_dampener(df, stoch_gate, wadj_gate, K, lift_target, score_gate)
        vstats = bucket_stats(vdf, 'new_overall')
        diff = diff_table(baseline_stats, vstats)
        h1_pass, h1_det = check_h1(diff)
        h3_pass, h3_det = check_h3(diff, PRIMARY_CALL_TIERS)
        h4_pass, h4_det = check_h4_calls_neutral(diff)
        h5_pass, h5_det = check_h5_call(diff)
        all_pass = h1_pass and h3_pass and h4_pass and h5_pass
        elapsed = time.perf_counter() - t0

        # count how many affected signals got dampened
        fired_mask = (
            (vdf['overall'] >= score_gate) &
            (vdf['stoch'] < stoch_gate) &
            (vdf['wadj'] >= 1.0) &
            (vdf['wadj'] < wadj_gate) &
            (vdf['signal_type'] == 'CALL')
        )
        n_fired = int(fired_mask.sum())
        # quick ΔTP on primary 75+ 5y
        dtp_75 = diff.get(('75+', '5y'), {}).get('dtp', float('nan'))
        dtp_80 = diff.get(('80+', '5y'), {}).get('dtp', float('nan'))
        dtp_85 = diff.get(('85+', '5y'), {}).get('dtp', float('nan'))

        status = "SHIP" if all_pass else ("H1✗" if not h1_pass else ("H3✗" if not h3_pass else ("H4✗" if not h4_pass else "H5✗")))
        print(f"  {label:30s} n_fired={n_fired:5d}  75+:{dtp_75:+.2f}pp 80+:{dtp_80:+.2f}pp 85+:{dtp_85:+.2f}pp  H1={'✓' if h1_pass else '✗'} H3={'✓' if h3_pass else '✗'} H4={'✓' if h4_pass else '✗'} H5={'✓' if h5_pass else '✗'}  [{status}]  {elapsed:.2f}s")
        results.append({
            'label': label, 'stoch_gate': stoch_gate, 'wadj_gate': wadj_gate, 'K': K,
            'n_fired': n_fired, 'dtp_75': dtp_75, 'dtp_80': dtp_80, 'dtp_85': dtp_85,
            'h1': h1_pass, 'h3': h3_pass, 'h4': h4_pass, 'h5': h5_pass,
            'all_pass': all_pass, 'diff': diff,
        })

    # Show top passing variants
    passing = [r for r in results if r['all_pass']]
    print(f"\n  {len(passing)}/{len(results)} variants pass all gates.")
    if passing:
        best = sorted(passing, key=lambda r: r['dtp_75'] + r['dtp_80'] + r['dtp_85'], reverse=True)
        print(f"\n  Top 3 CALL variants:")
        for r in best[:3]:
            print(f"    {r['label']:30s}  75+:{r['dtp_75']:+.2f}pp  80+:{r['dtp_80']:+.2f}pp  85+:{r['dtp_85']:+.2f}pp")
            print_bucket_diff(r['diff'], r['label'])
    return results


# ── PUT sweep: stoch-regime dampener ────────────────────────────────────────

PUT_PARAMS = {
    'stoch_gate':   [60, 65, 70],   # stoch above this = overbought on puts
    'regime_gate':  [50, 55, 60],   # reg_comp above this = healthy market
    'K':            [0.40, 0.60, 0.80],
    'lift_target':  [45, 50],       # lift toward (50 = neutral)
    'score_gate':   [25],
}


def apply_put_dampener(df: pl.DataFrame, stoch_gate: int, regime_gate: int,
                       K: float, lift_target: int = 50, score_gate: int = 25):
    """Apply stoch-regime put dampener to produce new_overall."""
    stoch_w  = ((pl.col('stoch')    - pl.lit(float(stoch_gate)))  / pl.lit(float(100 - stoch_gate))).clip(0.0, 1.0)
    regime_w = ((pl.col('reg_comp') - pl.lit(float(regime_gate))) / pl.lit(float(100 - regime_gate))).clip(0.0, 1.0)
    weakness = stoch_w * regime_w
    fire = (
        (pl.col('overall') <= score_gate) &
        (pl.col('stoch') > stoch_gate) &
        (pl.col('reg_comp') > regime_gate) &
        (pl.col('signal_type') == 'PUT')
    )
    new_ov = (
        pl.when(fire)
          .then((pl.col('overall') + K * weakness * (pl.lit(lift_target) - pl.col('overall'))).round())
          .otherwise(pl.col('overall'))
          .clip(0, 100).cast(pl.Int64)
    )
    return df.with_columns(new_ov.alias('new_overall'))


def run_put_sweep(df: pl.DataFrame, baseline_stats: dict):
    print("\n" + "="*80)
    print("PUT SWEEP — stoch-hi + regime-HEALTHY dampener (lift toward neutral)")
    print("Gates: H3 (N +-15%), H4-mirror (calls neutral), H5 (3-window consistent)")
    print("Primary gate: PUT TP% improves on <25/<20/<15 at 5y (H1-mirror)")
    print("="*80)

    results = []
    for stoch_gate, regime_gate, K, lift_target, score_gate in product(
        PUT_PARAMS['stoch_gate'], PUT_PARAMS['regime_gate'],
        PUT_PARAMS['K'], PUT_PARAMS['lift_target'], PUT_PARAMS['score_gate']
    ):
        label = f"P_sg{stoch_gate}_rg{regime_gate}_K{K:.2f}_t{lift_target}"
        t0 = time.perf_counter()
        vdf = apply_put_dampener(df, stoch_gate, regime_gate, K, lift_target, score_gate)
        vstats = bucket_stats(vdf, 'new_overall')
        diff = diff_table(baseline_stats, vstats)
        h3_pass, h3_det = check_h3(diff, PRIMARY_PUT_TIERS)
        h4_pass, h4_det = check_h4_puts_neutral(diff)
        h5_pass, h5_det = check_h5_put(diff)
        # H1-mirror: TP% improves on 3+ put tiers at 5y
        put_gains = [t for t in PRIMARY_PUT_TIERS
                     if not np.isnan(diff.get((t, '5y'), {}).get('dtp', float('nan')))
                     and diff[(t, '5y')]['dtp'] >= H1_MIN_IMPROVE_PP]
        h1m_pass = len(put_gains) >= H1_MIN_TIERS
        all_pass = h1m_pass and h3_pass and h4_pass and h5_pass
        elapsed = time.perf_counter() - t0

        fired_mask = (
            (vdf['overall'] <= score_gate) &
            (vdf['stoch'] > stoch_gate) &
            (vdf['reg_comp'] > regime_gate) &
            (vdf['signal_type'] == 'PUT')
        )
        n_fired = int(fired_mask.sum())
        dtp_25 = diff.get(('<25', '5y'), {}).get('dtp', float('nan'))
        dtp_20 = diff.get(('<20', '5y'), {}).get('dtp', float('nan'))
        dtp_15 = diff.get(('<15', '5y'), {}).get('dtp', float('nan'))

        status = "SHIP" if all_pass else ("H1m✗" if not h1m_pass else ("H3✗" if not h3_pass else ("H4✗" if not h4_pass else "H5✗")))
        print(f"  {label:35s} n_fired={n_fired:5d}  <25:{dtp_25:+.2f}pp <20:{dtp_20:+.2f}pp <15:{dtp_15:+.2f}pp  H1m={'✓' if h1m_pass else '✗'} H3={'✓' if h3_pass else '✗'} H4={'✓' if h4_pass else '✗'} H5={'✓' if h5_pass else '✗'}  [{status}]  {elapsed:.2f}s")
        results.append({
            'label': label, 'stoch_gate': stoch_gate, 'regime_gate': regime_gate,
            'K': K, 'lift_target': lift_target,
            'n_fired': n_fired, 'dtp_25': dtp_25, 'dtp_20': dtp_20, 'dtp_15': dtp_15,
            'h1m': h1m_pass, 'h3': h3_pass, 'h4': h4_pass, 'h5': h5_pass,
            'all_pass': all_pass, 'diff': diff,
        })

    passing = [r for r in results if r['all_pass']]
    print(f"\n  {len(passing)}/{len(results)} variants pass all gates.")
    if passing:
        best = sorted(passing, key=lambda r: r['dtp_25'] + r['dtp_20'] + r['dtp_15'], reverse=True)
        print(f"\n  Top 3 PUT variants:")
        for r in best[:3]:
            print(f"    {r['label']:35s}  <25:{r['dtp_25']:+.2f}pp  <20:{r['dtp_20']:+.2f}pp  <15:{r['dtp_15']:+.2f}pp")
            print_bucket_diff(r['diff'], r['label'])
    return results


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    run_calls = '--puts-only' not in args
    run_puts  = '--calls-only' not in args

    df = load()

    # Baseline: use existing overall scores as new_overall
    df = df.with_columns(pl.col('overall').alias('new_overall'))
    print("[sweep] computing baseline ...", flush=True)
    baseline_stats = bucket_stats(df, 'new_overall')

    # Print baseline for reference
    print("\nBaseline TP% (5y):")
    for t in PRIMARY_CALL_TIERS:
        n = baseline_stats[(t, '5y')]['n']
        tp = baseline_stats[(t, '5y')]['tp']
        print(f"  {t:6s}  N={n:6d}  TP={tp*100:.1f}%")
    for t in PRIMARY_PUT_TIERS:
        n = baseline_stats[(t, '5y')]['n']
        tp = baseline_stats[(t, '5y')]['tp']
        print(f"  {t:6s}  N={n:6d}  TP={tp*100:.1f}%")

    call_results, put_results = [], []
    if run_calls:
        call_results = run_call_sweep(df, baseline_stats)
    if run_puts:
        put_results  = run_put_sweep(df, baseline_stats)

    # Summary table
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    if run_calls:
        call_pass = [r for r in call_results if r['all_pass']]
        print(f"CALL variants passing H1+H3+H4+H5: {len(call_pass)}/{len(call_results)}")
        if call_pass:
            best_c = sorted(call_pass, key=lambda r: r['dtp_75'] + r['dtp_80'] + r['dtp_85'], reverse=True)[0]
            print(f"  Best call: {best_c['label']}  75+:{best_c['dtp_75']:+.2f}pp  80+:{best_c['dtp_80']:+.2f}pp  85+:{best_c['dtp_85']:+.2f}pp")
    if run_puts:
        put_pass = [r for r in put_results if r['all_pass']]
        print(f"PUT  variants passing H1m+H3+H4+H5: {len(put_pass)}/{len(put_results)}")
        if put_pass:
            best_p = sorted(put_pass, key=lambda r: r['dtp_25'] + r['dtp_20'] + r['dtp_15'], reverse=True)[0]
            print(f"  Best put:  {best_p['label']}  <25:{best_p['dtp_25']:+.2f}pp  <20:{best_p['dtp_20']:+.2f}pp  <15:{best_p['dtp_15']:+.2f}pp")


if __name__ == '__main__':
    main()
