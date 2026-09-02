"""Phase F: scorched-earth v27 substitution test + call N clumping analysis.

Phase C measured ADDITIVE (Ichimoku on top of v39 which still has v27). To
validate the actual ship candidate (Ichimoku REPLACES v27), we need the
substitutive test: invert v27, then apply Ichimoku, compare to current
production.

Four score variants compared:
  (a) v39 baseline      — current production (with v27 active)
  (b) v27 stripped      — pure scorched earth, no replacement
  (c) v39 + Ichimoku    — additive (Phase C result)
  (d) v27→Ichimoku      — true substitution (ship candidate)

For each variant: compute affected-tier WR15 deltas vs (a).

ALSO: investigate the call N drop "clumping" — break down displaced calls
by year, indicator-state cohort, and check for concentration.

v27 reversal math:
  v27 formula:  overall_post = pre + 0.95 × w × (50 - pre)
                where w = clip((wadj + 17) / 17, 0, 1)
  Inverse:      pre = (overall_post - 47.5 × w) / (1 - 0.95 × w)
  But weight_info already exposes `wcf_lift` = 0.95 × w × (50 - pre)
  So pre_v27 = overall - wcf_lift (when wcf_lift is non-null)

Usage:
    python experiments/weekly_avwap/phase_f_substitution.py [rank]
"""
from __future__ import annotations

import io
import math
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache' / 'weekly_avwap'
BARRIER_DB = ROOT / '.cache' / 'barrier_outcomes.db'

RANK = int(sys.argv[1]) if len(sys.argv) > 1 else 1


# ---------------------------------------------------------------------------
# Ichimoku application (matches phase_c_sweep)
# ---------------------------------------------------------------------------

def apply_ichimoku(df: pl.DataFrame, p: dict, score_col: str = 'overall') -> pl.DataFrame:
    """Apply log-magnifier dampener to df[score_col], output as 'overall_new'."""
    if p['RAMP_SHAPE'] == 'log':
        ramp = lambda x, sat: ((x.clip(0.0, None) + 1).log() / math.log(1 + sat)).clip(0.0, 1.0)
    else:
        ramp = lambda x, sat: (x.clip(0.0, None) / sat).clip(0.0, 1.0)

    ind_dist = (-pl.col('price_vs_kijun_pct')).clip(0.0, None)

    call_sg = ramp(pl.col(score_col) - p['GATE_CALL_LO'],
                   p['GATE_CALL_HI'] - p['GATE_CALL_LO'])
    call_ig = ramp(ind_dist, p['KIJ_SAT_CALL'])
    call_w  = call_sg * call_ig
    call_d  = -p['K_CALL'] * call_w * (pl.col(score_col) - p['LIFT_TARGET_CALL'])

    put_sg = ramp(p['GATE_PUT_HI'] - pl.col(score_col),
                  p['GATE_PUT_HI'] - p['GATE_PUT_LO'])
    put_ig = ramp(ind_dist, p['KIJ_SAT_PUT'])
    put_w  = put_sg * put_ig
    put_d  = p['K_PUT'] * put_w * (p['LIFT_TARGET_PUT'] - pl.col(score_col))

    return df.with_columns(
        (pl.col(score_col) + call_d.fill_null(0) + put_d.fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_new')
    )


# ---------------------------------------------------------------------------
# Tier WR helpers
# ---------------------------------------------------------------------------

def tier_wr_for_score(df: pl.DataFrame, score_col: str, side: str) -> dict:
    """For each call/put tier, compute WR15 + N using df[score_col] as the score.
    side ∈ {'call', 'put'} determines which barrier column to use."""
    out = {}
    if side == 'call':
        result_col = 'opt_result_15'
        tiers = [('70+', pl.col(score_col) >= 70),
                 ('75+', pl.col(score_col) >= 75),
                 ('80+', pl.col(score_col) >= 80),
                 ('85+', pl.col(score_col) >= 85),
                 ('90+', pl.col(score_col) >= 90),
                 ('95+', pl.col(score_col) >= 95)]
    else:
        result_col = 'put_result_15'
        tiers = [('<30', pl.col(score_col) <= 30),
                 ('<25', pl.col(score_col) <= 25),
                 ('<20', pl.col(score_col) <= 20),
                 ('<15', pl.col(score_col) <= 15),
                 ('<10', pl.col(score_col) <= 10),
                 ('<5',  pl.col(score_col) <= 5)]

    for label, mask in tiers:
        sub = df.filter(mask & pl.col(result_col).is_not_null())
        n = len(sub)
        wr = float(sub[result_col].mean()) * 100 if n > 0 else float('nan')
        out[label] = (wr, n)
    return out


def print_tier_table(name: str, base: dict, this: dict, side: str) -> None:
    print(f'\n  {name}:')
    for tier in base.keys():
        b_wr, b_n = base[tier]
        t_wr, t_n = this[tier]
        dwr = t_wr - b_wr if not math.isnan(t_wr) and not math.isnan(b_wr) else float('nan')
        dn  = (t_n / b_n - 1) * 100 if b_n > 0 else 0
        print(f'    {side.upper():<5s} {tier:<5s}  '
              f'WR15: {b_wr:5.2f}% → {t_wr:5.2f}% ({dwr:+5.2f})  '
              f'N: {b_n:>6,} → {t_n:>6,} ({dn:+5.1f}%)')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sweep_results = CACHE / 'phase_c_sweep_results.parquet'
    res = pl.read_parquet(sweep_results)
    top_row = res.sort('composite', descending=True).head(RANK).tail(1).row(0, named=True)

    p = {k: top_row[k] for k in [
        'GATE_CALL_LO', 'GATE_CALL_HI', 'K_CALL', 'KIJ_SAT_CALL', 'LIFT_TARGET_CALL',
        'GATE_PUT_LO', 'GATE_PUT_HI', 'K_PUT', 'KIJ_SAT_PUT', 'LIFT_TARGET_PUT',
        'RAMP_SHAPE',
    ]}

    print(f'=== Phase F substitution test — Rank #{RANK} config ===')
    print(f'  RAMP={p["RAMP_SHAPE"]}  CALL: gate={p["GATE_CALL_LO"]}-{p["GATE_CALL_HI"]} '
          f'K={p["K_CALL"]:.2f} sat={p["KIJ_SAT_CALL"]:.1f} target={p["LIFT_TARGET_CALL"]:.1f}')
    print(f'                     PUT:  gate={p["GATE_PUT_LO"]}-{p["GATE_PUT_HI"]} '
          f'K={p["K_PUT"]:.2f} sat={p["KIJ_SAT_PUT"]:.1f} target={p["LIFT_TARGET_PUT"]:.1f}')

    # Load main parquet
    df = pl.read_parquet(CACHE / 'calls_v39_1825d_min0.parquet')
    df = df.with_columns(
        pl.when(pl.col('w52_low_pct').is_finite()).then(pl.col('w52_low_pct'))
          .otherwise(None).alias('w52_low_pct')
    )
    df = df.filter(pl.col('price_vs_kijun_pct').is_not_null()
                   & pl.col('price_vs_kijun_pct').is_finite())
    print(f'\n[load] parquet: {len(df):,} rows', flush=True)

    # Attach put barriers
    print('[load] put-side WR15...', flush=True)
    conn = sqlite3.connect(str(BARRIER_DB))
    try:
        put_wr15 = pl.read_database(
            """SELECT symbol, date, result AS put_result_15
               FROM barrier_outcomes
               WHERE side='high' AND barrier_set='30dte_opt' AND w_days=15""",
            connection=conn,
        )
    finally:
        conn.close()
    df = df.join(put_wr15, on=['symbol', 'date'], how='left')

    # Attach wadj cache (has wcf_lift for v27 reversal)
    wadj_df = pl.read_parquet(CACHE / 'wadj_v39_1825d_min0.parquet')
    df = df.join(wadj_df, on=['symbol', 'date'], how='left')
    n_with_wcf = df.filter(pl.col('wcf_lift').is_not_null() & (pl.col('wcf_lift') > 0)).height
    print(f'[wadj] joined: {df["w_adj"].drop_nulls().len():,} have w_adj  |  '
          f'{n_with_wcf:,} had v27 lift fire', flush=True)

    # ============================================================================
    # Build the four score variants
    # ============================================================================
    # (a) baseline = current overall (v39 with v27 active)
    # (b) v27 stripped: subtract wcf_lift if present, else no change
    # (c) v39 + Ichimoku: apply Ichimoku to current overall
    # (d) v27→Ichimoku: apply Ichimoku to v27-stripped score

    df = df.with_columns(
        # Pre-v27 score: overall - wcf_lift (clamped to [0, 100])
        (pl.col('overall') - pl.col('wcf_lift').fill_null(0))
        .clip(0.0, 100.0)
        .alias('overall_pre_v27')
    )

    # (c) Apply Ichimoku to current overall
    df_c = apply_ichimoku(df, p, score_col='overall').rename({'overall_new': 'overall_c'})
    df = df.with_columns(df_c['overall_c'])

    # (d) Apply Ichimoku to pre-v27
    df_d = apply_ichimoku(df, p, score_col='overall_pre_v27').rename({'overall_new': 'overall_d'})
    df = df.with_columns(df_d['overall_d'])

    # ============================================================================
    # Compute tier metrics under each variant
    # ============================================================================
    base_call = tier_wr_for_score(df, 'overall', 'call')
    base_put  = tier_wr_for_score(df, 'overall', 'put')

    b_call = tier_wr_for_score(df, 'overall_pre_v27', 'call')
    b_put  = tier_wr_for_score(df, 'overall_pre_v27', 'put')

    c_call = tier_wr_for_score(df, 'overall_c', 'call')
    c_put  = tier_wr_for_score(df, 'overall_c', 'put')

    d_call = tier_wr_for_score(df, 'overall_d', 'call')
    d_put  = tier_wr_for_score(df, 'overall_d', 'put')

    # ============================================================================
    # Reports
    # ============================================================================
    print('\n' + '=' * 92)
    print('VARIANT (a): baseline = current v39 production (with v27 active)')
    print('=' * 92)
    for tier in base_call.keys():
        wr, n = base_call[tier]
        print(f'  CALL  {tier:<5s}  WR15: {wr:5.2f}%  N: {n:>6,}')
    for tier in base_put.keys():
        wr, n = base_put[tier]
        print(f'  PUT   {tier:<5s}  WR15: {wr:5.2f}%  N: {n:>6,}')

    print('\n' + '=' * 92)
    print('VARIANT (b): v27 STRIPPED — pure scorched earth, no replacement')
    print('  → tests "does removing v27 alone hurt? what alpha was v27 carrying?"')
    print('=' * 92)
    print_tier_table('vs (a) baseline', base_call, b_call, 'call')
    print_tier_table('', base_put, b_put, 'put')

    print('\n' + '=' * 92)
    print('VARIANT (c): v39 + Ichimoku ADDITIVE — Phase C measured this')
    print('=' * 92)
    print_tier_table('vs (a) baseline', base_call, c_call, 'call')
    print_tier_table('', base_put, c_put, 'put')

    print('\n' + '=' * 92)
    print('VARIANT (d): v27→Ichimoku TRUE SUBSTITUTION — ship candidate')
    print('=' * 92)
    print_tier_table('vs (a) baseline', base_call, d_call, 'call')
    print_tier_table('', base_put, d_put, 'put')

    # ============================================================================
    # CLUMPING analysis: where do displaced calls go?
    # ============================================================================
    print('\n' + '=' * 92)
    print('CLUMPING ANALYSIS — call cohort displaced by Ichimoku (variant c, 75+)')
    print('=' * 92)

    # Peaks that were 75+ in baseline but moved BELOW 75 under variant (c)
    displaced = df.filter(
        (pl.col('overall') >= 75) & (pl.col('overall_c') < 75)
    )
    print(f'\nDisplaced N: {len(displaced):,}  '
          f'({len(displaced)/df.filter(pl.col("overall") >= 75).height*100:.1f}% '
          f'of baseline 75+ cohort)')

    if len(displaced) > 100:
        # By year
        displaced = displaced.with_columns(
            pl.col('date').str.slice(0, 4).alias('year')
        )
        baseline_75p = df.filter(pl.col('overall') >= 75).with_columns(
            pl.col('date').str.slice(0, 4).alias('year')
        )

        print('\n  By year (% of year-baseline displaced):')
        for yr in sorted(baseline_75p['year'].unique().to_list()):
            base_n = baseline_75p.filter(pl.col('year') == yr).height
            disp_n = displaced.filter(pl.col('year') == yr).height
            pct = disp_n / base_n * 100 if base_n > 0 else 0
            bar = '█' * int(pct / 2)
            print(f'    {yr}  base={base_n:>5,}  displaced={disp_n:>4,}  '
                  f'({pct:>5.1f}%)  {bar}')

        # By indicator state (kijun_pct buckets within displaced cohort)
        print('\n  Displaced cohort kijun_pct distribution:')
        for label, mask in [
            ('< -20',  pl.col('price_vs_kijun_pct') < -20),
            ('-20 to -10', (pl.col('price_vs_kijun_pct') >= -20) & (pl.col('price_vs_kijun_pct') < -10)),
            ('-10 to -5',  (pl.col('price_vs_kijun_pct') >= -10) & (pl.col('price_vs_kijun_pct') < -5)),
            ('-5 to 0',    (pl.col('price_vs_kijun_pct') >= -5)  & (pl.col('price_vs_kijun_pct') < 0)),
            ('>= 0 (above kijun, should NOT fire)', pl.col('price_vs_kijun_pct') >= 0),
        ]:
            n = displaced.filter(mask).height
            pct = n / len(displaced) * 100
            print(f'    kijun_pct {label:<35s}  N={n:>4,}  ({pct:5.1f}%)')

        # Top symbols (if any single symbol dominates)
        print('\n  Top 10 symbols in displaced cohort (concentration check):')
        top_syms = displaced.group_by('symbol').len().sort('len', descending=True).head(10)
        total = len(displaced)
        for r in top_syms.iter_rows(named=True):
            pct = r['len'] / total * 100
            print(f'    {r["symbol"]:<6s}  {r["len"]:>4,}  ({pct:>5.1f}% of displaced)')

        top1_pct = top_syms.head(1).row(0)[1] / total * 100
        top10_pct = sum(top_syms.head(10).get_column('len').to_list()) / total * 100
        if top1_pct > 5:
            print(f'\n    ⚠ Top-1 symbol concentration: {top1_pct:.1f}% (>5% threshold suggests stock-specific clumping)')
        elif top10_pct > 25:
            print(f'\n    ⚠ Top-10 concentration: {top10_pct:.1f}% (>25% threshold suggests dataset bias)')
        else:
            print(f'\n    ✓ Top-1: {top1_pct:.1f}%, Top-10: {top10_pct:.1f}% — no concerning concentration')

    # ============================================================================
    # Verdict
    # ============================================================================
    print('\n' + '=' * 92)
    print('VERDICT — does scorched-earth substitution beat current production?')
    print('=' * 92)
    print()
    # Compare (d) substitution to (a) baseline on affected tiers
    d_call_70p = (d_call['70+'][0] - base_call['70+'][0],
                  (d_call['70+'][1]/base_call['70+'][1]-1)*100)
    d_call_75p = (d_call['75+'][0] - base_call['75+'][0],
                  (d_call['75+'][1]/base_call['75+'][1]-1)*100)
    d_put_25  = (d_put['<25'][0] - base_put['<25'][0],
                 (d_put['<25'][1]/base_put['<25'][1]-1)*100)

    print(f'  Substitution (d) vs current production (a):')
    print(f'    Call 70+:  ΔWR={d_call_70p[0]:+.2f}pp  ΔN={d_call_70p[1]:+.1f}%')
    print(f'    Call 75+:  ΔWR={d_call_75p[0]:+.2f}pp  ΔN={d_call_75p[1]:+.1f}%')
    print(f'    Put  <25:  ΔWR={d_put_25[0]:+.2f}pp  ΔN={d_put_25[1]:+.1f}%')

    # Same metrics for additive (c)
    c_call_70p = (c_call['70+'][0] - base_call['70+'][0],
                  (c_call['70+'][1]/base_call['70+'][1]-1)*100)
    c_put_25  = (c_put['<25'][0] - base_put['<25'][0],
                 (c_put['<25'][1]/base_put['<25'][1]-1)*100)

    print(f'\n  vs additive (c) [Phase C measurement]:')
    print(f'    Call 70+:  Δsub-add ΔWR={d_call_70p[0]-c_call_70p[0]:+.2f}pp  '
          f'(sub: {d_call_70p[0]:+.2f}, add: {c_call_70p[0]:+.2f})')
    print(f'    Put  <25:  Δsub-add ΔWR={d_put_25[0]-c_put_25[0]:+.2f}pp  '
          f'(sub: {d_put_25[0]:+.2f}, add: {c_put_25[0]:+.2f})')


if __name__ == '__main__':
    main()
