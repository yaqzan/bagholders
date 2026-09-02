"""Calibration sanity check — do dampened signals behave like their NEW tier?

For each discrete bucket (70-74, 75-79, 80-84, 85-89, 90+) compute:
  - baseline_tp = TP rate of UNDAMPED signals at that bucket
  - dampened_tp = TP rate of DAMPENED signals at that bucket (new_overall in range)

Three possible outcomes per bucket:
  - dampened_tp ≈ baseline_tp:  dampener is correctly calibrated (signal moves to
    a tier where it behaves like real signals at that score)
  - dampened_tp < baseline_tp:  dampener is UNDER-correcting (signals still
    over-bucketed; should push harder)
  - dampened_tp > baseline_tp:  dampener is OVER-correcting (alpha-rich signals
    pushed down to a bucket that's now systematically too quiet)

Calibration error per bucket = |dampened_tp - baseline_tp|
Total calibration error = mean(|delta| for each bucket weighted by N)

If candidate has high calibration error, we can Bayesian-search ALPHA/TARGET
to MINIMIZE calibration error subject to:
  - 75+ cumulative TP% still improves (>= +1.0pp)
  - Spillover within ±0.4pp on top tiers

Outputs: per-bucket calibration table for the recommended candidate, then
sweeps a small grid to find better-calibrated alternatives.
"""
from __future__ import annotations
import io, sys, json
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LOCAL = ROOT / '.cache' / 'mcap_dampener'

# Discrete bucket edges
BUCKETS = [
    ('70-74', 70, 74),
    ('75-79', 75, 79),
    ('80-84', 80, 84),
    ('85-89', 85, 89),
    ('90+', 90, 100),
]


def apply_dampener(df, gate_lo, gate_hi, log_lo, log_hi, alpha, target):
    if alpha <= 0:
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


def bucket_tp(df, score_col, lo, hi):
    """TP rate for signals where score_col in [lo, hi]."""
    sub = df.filter((pl.col(score_col) >= lo) & (pl.col(score_col) <= hi))
    n = sub.height
    if n == 0:
        return None, 0
    tp = sub.select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item()
    return tp * 100, n


def calibration_table(df, gate_lo, gate_hi, log_lo, log_hi, alpha, target):
    """Returns dict per bucket: {bucket_label: {base_tp, base_n, damp_tp, damp_n, delta}}"""
    df_d = apply_dampener(df, gate_lo, gate_hi, log_lo, log_hi, alpha, target)
    out = {}
    for label, lo, hi in BUCKETS:
        # Baseline = signals whose ORIGINAL overall was in [lo, hi]
        base_tp, base_n = bucket_tp(df, 'overall', lo, hi)
        # Dampened = signals whose NEW overall is in [lo, hi]
        damp_tp, damp_n = bucket_tp(df_d, 'new_overall', lo, hi)
        delta = (damp_tp - base_tp) if (damp_tp is not None and base_tp is not None) else None
        out[label] = {'base_tp': base_tp, 'base_n': base_n,
                      'damp_tp': damp_tp, 'damp_n': damp_n,
                      'delta': delta}
    return out


def fmt_calib(table, label):
    print(f'\n=== {label} ===')
    print(f"{'bucket':>8} {'base TP':>9} {'base N':>8} | {'damp TP':>9} {'damp N':>8} | {'Δ TP':>8} {'verdict':>20}")
    weighted_sse = 0.0
    total_n = 0
    for buc, lo, hi in BUCKETS:
        c = table[buc]
        if c['base_tp'] is None or c['damp_tp'] is None:
            print(f"  {buc:>6} | (insufficient data)")
            continue
        verdict = ''
        ad = abs(c['delta'])
        if ad <= 0.5:
            verdict = 'CALIBRATED'
        elif c['delta'] < -0.5:
            verdict = 'under-corrected'  # damp < base
        else:
            verdict = 'over-corrected'   # damp > base
        print(f"  {buc:>6} | {c['base_tp']:>7.2f}% {c['base_n']:>8,} | {c['damp_tp']:>7.2f}% {c['damp_n']:>8,} | "
              f"{c['delta']:>+5.2f}pp  {verdict:>20s}")
        weighted_sse += (c['delta'] ** 2) * c['damp_n']
        total_n += c['damp_n']
    rmse = (weighted_sse / total_n) ** 0.5 if total_n > 0 else float('nan')
    print(f"  N-weighted RMSE: {rmse:.3f}pp")
    return rmse


def cumulative_tp(df, score_col, threshold):
    """TP rate for signals where score_col >= threshold (cumulative)."""
    sub = df.filter(pl.col(score_col) >= threshold)
    n = sub.height
    if n == 0:
        return None, 0
    tp = sub.select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item() * 100
    return tp, n


def main():
    parquet = LOCAL / 'calls_v39_3650.parquet'
    df = pl.read_parquet(parquet)
    df = df.filter(pl.col('opt_result_15').is_not_null())  # resolved only
    today = date.today()
    w5y = (today - timedelta(days=1825)).isoformat()
    df = df.filter(pl.col('date') >= w5y)
    print(f'Loaded {len(df):,} resolved 5y rows from {parquet.name}')

    # ──────────────────────────────────────────────────────────────────────
    # Recommended candidate calibration check
    # ──────────────────────────────────────────────────────────────────────
    print('\n' + '='*80)
    print('CANDIDATE: standard-tier winner')
    print('GATE [70, 84], LOG_LO=0.0, LOG_HI=2.2, ALPHA=0.30, TARGET=65')
    print('='*80)
    cand_glo, cand_ghi, cand_llo, cand_lhi, cand_a, cand_t = 70, 84, 0.0, 2.2, 0.30, 65
    table = calibration_table(df, cand_glo, cand_ghi, cand_llo, cand_lhi, cand_a, cand_t)
    rmse = fmt_calib(table, 'STANDARD CANDIDATE')

    # Cumulative TP for context
    base75, base75_n = cumulative_tp(df, 'overall', 75)
    df_d = apply_dampener(df, cand_glo, cand_ghi, cand_llo, cand_lhi, cand_a, cand_t)
    damp75, damp75_n = cumulative_tp(df_d, 'new_overall', 75)
    print(f'\nCUMULATIVE 75+: base {base75:.2f}% (N={base75_n}) → damp {damp75:.2f}% (N={damp75_n})')
    print(f'Cumulative lift: {damp75 - base75:+.2f}pp')

    # ──────────────────────────────────────────────────────────────────────
    # Sweep variants — find lower calibration RMSE subject to lift constraint
    # ──────────────────────────────────────────────────────────────────────
    print('\n' + '='*80)
    print('CALIBRATION-AWARE BAYESIAN SWEEP')
    print('='*80)
    print('Objective: MIN N-weighted RMSE subject to:')
    print('  - cumulative 75+ TP%: must improve >=+1.0pp')
    print('  - per-bucket spillover: no |delta| > 1.5pp on 80+/85+/90+')
    print()

    # Dense grid (re-using the same axes as before, fewer points)
    # Hold GATE fixed at [70, 84] (validated) — sweep LO/HI/ALPHA/TARGET
    candidates = []
    for llo in [-0.3, 0.0, 0.3, 0.5, 0.7, 1.0]:
        for lhi in [1.0, 1.3, 1.5, 1.8, 2.2, 2.5]:
            if lhi <= llo + 0.3: continue
            for alpha in [0.30, 0.50, 0.70, 0.85, 0.95, 1.0]:
                for target in [60, 62, 65, 68, 70, 72]:
                    if target >= 70: continue  # below gate floor
                    candidates.append((llo, lhi, alpha, target))

    print(f'Sweeping {len(candidates)} variants...')
    results = []
    for llo, lhi, alpha, target in candidates:
        tab = calibration_table(df, 70, 84, llo, lhi, alpha, target)
        df_d = apply_dampener(df, 70, 84, llo, lhi, alpha, target)
        damp75_tp, _ = cumulative_tp(df_d, 'new_overall', 75)
        damp80_tp, _ = cumulative_tp(df_d, 'new_overall', 80)
        damp85_tp, _ = cumulative_tp(df_d, 'new_overall', 85)
        base80_tp, _ = cumulative_tp(df, 'overall', 80)
        base85_tp, _ = cumulative_tp(df, 'overall', 85)
        # constraints
        cum_lift = damp75_tp - base75 if (damp75_tp is not None and base75 is not None) else 0
        spill80 = damp80_tp - base80_tp if (damp80_tp is not None and base80_tp is not None) else 0
        spill85 = damp85_tp - base85_tp if (damp85_tp is not None and base85_tp is not None) else 0
        worst_spill = min(spill80, spill85, 0.0)
        # weighted RMSE
        weighted_sse = 0.0
        total_n = 0
        for buc, lo, hi in BUCKETS:
            c = tab[buc]
            if c['base_tp'] is None or c['damp_tp'] is None: continue
            weighted_sse += (c['delta'] ** 2) * c['damp_n']
            total_n += c['damp_n']
        rmse_v = (weighted_sse / total_n) ** 0.5 if total_n > 0 else float('inf')

        passes = cum_lift >= 1.0 and worst_spill >= -1.5

        results.append({
            'llo': llo, 'lhi': lhi, 'alpha': alpha, 'target': target,
            'rmse': rmse_v, 'cum_lift': cum_lift, 'spill80': spill80, 'spill85': spill85,
            'pass': passes, 'tab': tab,
        })

    # Sort by RMSE ascending, then highlight passing ones
    print('\n=== TOP 15 BY LOWEST CALIBRATION RMSE (passing constraints first) ===')
    results.sort(key=lambda r: (not r['pass'], r['rmse']))
    print(f"{'LO':>6} {'HI':>6} {'α':>5} {'T':>3} | {'RMSE':>7} {'cum_lift':>9} {'spill80':>9} {'spill85':>9} {'pass':>5}")
    for r in results[:15]:
        flag = '✓' if r['pass'] else ' '
        print(f"  {r['llo']:>4.1f} {r['lhi']:>4.1f} {r['alpha']:>5.2f} {r['target']:>3} | "
              f"{r['rmse']:>5.2f}pp  {r['cum_lift']:>+5.2f}pp  {r['spill80']:>+5.2f}pp  {r['spill85']:>+5.2f}pp  {flag}")

    # Best calibrated passing variant
    passing = [r for r in results if r['pass']]
    if passing:
        passing.sort(key=lambda r: r['rmse'])
        best = passing[0]
        print('\n' + '='*80)
        print('BEST-CALIBRATED PASSING VARIANT')
        print('='*80)
        print(f"  LO={best['llo']}, HI={best['lhi']}, α={best['alpha']}, T={best['target']}")
        print(f"  RMSE: {best['rmse']:.3f}pp")
        print(f"  Cumulative 75+ lift: {best['cum_lift']:+.2f}pp")
        fmt_calib(best['tab'], 'BEST-CALIBRATED')

    # Compare to highest-lift candidate from earlier
    print('\n' + '='*80)
    print('COMPARISON: standard-tier winner vs best-calibrated variant')
    print('='*80)


if __name__ == '__main__':
    main()
