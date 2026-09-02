"""Phase B — W1 cohort z pre-flight on v46 substrate.

Re-validates the Phase 1 findings (sector ETF state) at WR7 (new Stage 1 primary metric)
and tests 6 NEW hypothesis families. Output: cohort z table for each (hypothesis × side × tier).

Method: cohort z-score = (p_cohort - p_baseline) / sqrt(p_baseline*(1-p_baseline)/N_cohort)
where p_baseline is rest-of-tier WR7 (NOT global). N_cohort >= 30 floor.

W1 gate: |z| >= 3.0 in proposed direction.
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOCAL = ROOT / '.cache' / 'sector_etf_alpha'
COH = LOCAL / 'cohort_v46_1825.parquet'

WR_COL = 'wr_7'  # Stage 1 primary
N_FLOOR = 30


def cohort_stats(df, mask, label):
    """Return dict of {N, p_cohort, p_rest, delta_pp, z} for affected vs rest."""
    mask_filled = mask.fill_null(False)
    cohort = df.filter(mask_filled)
    rest = df.filter(~mask_filled)
    n_c = cohort.height
    n_r = rest.height
    if n_c < N_FLOOR or n_r < N_FLOOR:
        return None
    wr_c = cohort.select(pl.col(WR_COL).cast(pl.Float64).mean()).item()
    wr_r = rest.select(pl.col(WR_COL).cast(pl.Float64).mean()).item()
    if wr_c is None or wr_r is None:
        return None
    delta_pp = (wr_c - wr_r) * 100
    se = math.sqrt(wr_r * (1 - wr_r) / n_c) if n_c > 0 else 0.0
    z = (wr_c - wr_r) / se if se > 0 else 0.0
    return {
        'label': label,
        'N_cohort': n_c, 'N_rest': n_r,
        'wr_c_pct': wr_c * 100, 'wr_r_pct': wr_r * 100,
        'delta_pp': delta_pp, 'z': z,
    }


def fmt_row(r, prefix=''):
    if r is None:
        return f'  {prefix} N<{N_FLOOR} (skipped)'
    flag = '⭐' if abs(r['z']) >= 3.0 else ('•' if abs(r['z']) >= 2.0 else ' ')
    sign = '+' if r['delta_pp'] >= 0 else ''
    return (f"  {flag} {prefix:<55} N={r['N_cohort']:>5,d} | "
            f"wr_c={r['wr_c_pct']:5.2f}% wr_r={r['wr_r_pct']:5.2f}% | "
            f"Δ={sign}{r['delta_pp']:5.2f}pp | z={r['z']:+5.2f}")


def main():
    df_all = pl.read_parquet(COH)
    print(f'[load] cohort: {len(df_all):,} rows', flush=True)
    print(f'[load] WR7 baseline by signal_type:')
    for r in df_all.group_by('signal_type').agg(
        pl.len().alias('n'),
        (pl.col('wr_7').cast(pl.Float64).mean() * 100).alias('wr7'),
        (pl.col('wr_15').cast(pl.Float64).mean() * 100).alias('wr15'),
        (pl.col('wr_30').cast(pl.Float64).mean() * 100).alias('wr30'),
        (pl.col('wr_1').cast(pl.Float64).mean() * 100).alias('wr1'),
    ).iter_rows(named=True):
        print(f'  {r["signal_type"]:<5} N={r["n"]:>6,d}  WR1={r["wr1"]:5.2f}  WR7={r["wr7"]:5.2f}  WR15={r["wr15"]:5.2f}  WR30={r["wr30"]:5.2f}')

    # Subset by signal type and broad tier (cumulative)
    calls_70 = df_all.filter(pl.col('signal_type') == 'CALL').filter(pl.col('overall') >= 70)
    calls_75 = df_all.filter(pl.col('signal_type') == 'CALL').filter(pl.col('overall') >= 75)
    calls_85 = df_all.filter(pl.col('signal_type') == 'CALL').filter(pl.col('overall') >= 85)
    puts_25 = df_all.filter(pl.col('signal_type') == 'PUT').filter(pl.col('overall') <= 25)
    puts_15 = df_all.filter(pl.col('signal_type') == 'PUT').filter(pl.col('overall') <= 15)

    print(f'\n[tiers] CALL 70+: {calls_70.height:,} | 75+: {calls_75.height:,} | 85+: {calls_85.height:,}')
    print(f'[tiers] PUT <=25: {puts_25.height:,} | <=15: {puts_15.height:,}')

    print('\n' + '='*100)
    print('PHASE B — W1 cohort z pre-flight (WR7-anchored, v46 substrate)')
    print('='*100)

    rows = []

    # ------------------------------------------------------------------
    # A. RE-VALIDATION OF PHASE 1 FINDINGS
    # ------------------------------------------------------------------
    print('\n[A] RE-VALIDATION — sector ETF state (originally on v43 / WR15-aligned)')
    print('-'*100)

    print('\n[A1] sector ETF % above EMA50:')
    for tier_name, tier_df in [('CALL 70+', calls_70), ('CALL 75+', calls_75), ('CALL 85+', calls_85)]:
        if tier_df.height < N_FLOOR: continue
        for label, mask in [
            ('overheated >+5%',   pl.col('sec_etf_pct_ema50') >  5),
            ('overheated >+3%',   pl.col('sec_etf_pct_ema50') >  3),
            ('mild lift +1..+3%', (pl.col('sec_etf_pct_ema50') >= 1) & (pl.col('sec_etf_pct_ema50') <= 3)),
            ('neutral -1..+1%',   (pl.col('sec_etf_pct_ema50') >= -1) & (pl.col('sec_etf_pct_ema50') <= 1)),
            ('mild dip -3..-1%',  (pl.col('sec_etf_pct_ema50') >= -3) & (pl.col('sec_etf_pct_ema50') <= -1)),
            ('oversold <-5%',     pl.col('sec_etf_pct_ema50') < -5),
        ]:
            r = cohort_stats(tier_df, mask, f'{tier_name} × sec_pctEMA50 {label}')
            print(fmt_row(r))
            if r: rows.append({'family': 'A1_sec_pctEMA50', **r})

    print('\n[A2] sector ETF RSI (THE strongest in original screen):')
    for tier_name, tier_df in [('CALL 70+', calls_70), ('CALL 75+', calls_75), ('CALL 85+', calls_85)]:
        if tier_df.height < N_FLOOR: continue
        for label, mask in [
            ('RSI > 70 (overheated)',  pl.col('sec_etf_rsi') > 70),
            ('RSI > 65',               pl.col('sec_etf_rsi') > 65),
            ('RSI 30-45 (mild weak)',  (pl.col('sec_etf_rsi') >= 30) & (pl.col('sec_etf_rsi') <= 45)),
            ('RSI < 30 (deep oversold)', pl.col('sec_etf_rsi') < 30),
        ]:
            r = cohort_stats(tier_df, mask, f'{tier_name} × sec_RSI {label}')
            print(fmt_row(r))
            if r: rows.append({'family': 'A2_sec_RSI', **r})

    for tier_name, tier_df in [('PUT <=25', puts_25), ('PUT <=20', df_all.filter((pl.col('signal_type') == 'PUT') & (pl.col('overall') <= 20))), ('PUT <=15', puts_15)]:
        if tier_df.height < N_FLOOR: continue
        for label, mask in [
            ('RSI < 30 (deep oversold)', pl.col('sec_etf_rsi') < 30),
            ('RSI < 35',                 pl.col('sec_etf_rsi') < 35),
            ('RSI > 70 (overheated)',    pl.col('sec_etf_rsi') > 70),
        ]:
            r = cohort_stats(tier_df, mask, f'{tier_name} × sec_RSI {label}')
            print(fmt_row(r))
            if r: rows.append({'family': 'A2_sec_RSI', **r})

    # ------------------------------------------------------------------
    # B. NEW DIMENSIONS
    # ------------------------------------------------------------------
    print('\n\n[B] NEW HYPOTHESES')
    print('-'*100)

    # Compute relative strength deltas
    df_rs = df_all.with_columns([
        (pl.col('stock_ret_5d')  - pl.col('sec_etf_ret_5d')).alias('rs_5d'),
        (pl.col('stock_ret_20d') - pl.col('sec_etf_ret_20d')).alias('rs_20d'),
    ])

    print('\n[H5] stock-vs-sector relative strength (rs_5d = stock_5d - sec_5d):')
    for tier_name, base_df in [('CALL 70+', calls_70), ('CALL 75+', calls_75), ('CALL 85+', calls_85),
                                ('PUT <=25', puts_25), ('PUT <=15', puts_15)]:
        if base_df.height < N_FLOOR: continue
        tier_df = base_df.with_columns([
            (pl.col('stock_ret_5d')  - pl.col('sec_etf_ret_5d')).alias('rs_5d'),
            (pl.col('stock_ret_20d') - pl.col('sec_etf_ret_20d')).alias('rs_20d'),
        ])
        for label, mask in [
            ('rs_5d > +5pp (stock outpacing sector)',  pl.col('rs_5d') > 0.05),
            ('rs_5d < -5pp (stock lagging sector)',    pl.col('rs_5d') < -0.05),
            ('rs_20d > +10pp',                          pl.col('rs_20d') > 0.10),
            ('rs_20d < -10pp',                          pl.col('rs_20d') < -0.10),
        ]:
            r = cohort_stats(tier_df, mask, f'{tier_name} × {label}')
            print(fmt_row(r))
            if r: rows.append({'family': 'H5_rel_strength', **r})

    print('\n[H6] sector ETF MACD line (positive = above zero = momentum tailwind):')
    for tier_name, tier_df in [('CALL 70+', calls_70), ('CALL 75+', calls_75), ('CALL 85+', calls_85),
                                ('PUT <=25', puts_25), ('PUT <=15', puts_15)]:
        if tier_df.height < N_FLOOR: continue
        for label, mask in [
            ('sec MACD > 0',  pl.col('sec_etf_macd') > 0),
            ('sec MACD < 0',  pl.col('sec_etf_macd') < 0),
        ]:
            r = cohort_stats(tier_df, mask, f'{tier_name} × {label}')
            print(fmt_row(r))
            if r: rows.append({'family': 'H6_sec_MACD', **r})

    print('\n[H7] cross-sector dispersion — proxy via QQQ_pct_ema50 vs IWM_pct_ema50:')
    df_disp = df_all.with_columns(
        (pl.col('QQQ_pct_ema50') - pl.col('IWM_pct_ema50')).alias('mega_smid_gap')
    )
    for tier_name, base_df in [('CALL 70+', calls_70), ('PUT <=25', puts_25)]:
        if base_df.height < N_FLOOR: continue
        tier_df = base_df.with_columns(
            (pl.col('QQQ_pct_ema50') - pl.col('IWM_pct_ema50')).alias('mega_smid_gap')
        )
        for label, mask in [
            ('mega_smid_gap > 5pp (mega outperforming)',  pl.col('mega_smid_gap') > 5),
            ('mega_smid_gap < -2pp (smid outperforming)', pl.col('mega_smid_gap') < -2),
        ]:
            r = cohort_stats(tier_df, mask, f'{tier_name} × {label}')
            print(fmt_row(r))
            if r: rows.append({'family': 'H7_dispersion', **r})

    print('\n[H8] SPY backdrop (vs sector signal — control test):')
    for tier_name, tier_df in [('CALL 70+', calls_70), ('PUT <=25', puts_25)]:
        if tier_df.height < N_FLOOR: continue
        for label, mask in [
            ('SPY > +5% above EMA50',  pl.col('SPY_pct_ema50') > 5),
            ('SPY RSI > 70',           pl.col('SPY_rsi') > 70),
            ('SPY RSI < 30',           pl.col('SPY_rsi') < 30),
            ('SPY < -5% below EMA50',  pl.col('SPY_pct_ema50') < -5),
        ]:
            r = cohort_stats(tier_df, mask, f'{tier_name} × {label}')
            print(fmt_row(r))
            if r: rows.append({'family': 'H8_SPY_control', **r})

    print('\n[H9] sector ETF momentum (ret_20d):')
    for tier_name, tier_df in [('CALL 70+', calls_70), ('CALL 85+', calls_85),
                                ('PUT <=25', puts_25), ('PUT <=15', puts_15)]:
        if tier_df.height < N_FLOOR: continue
        for label, mask in [
            ('sec ret_20d > +5%',  pl.col('sec_etf_ret_20d') > 0.05),
            ('sec ret_20d < -5%',  pl.col('sec_etf_ret_20d') < -0.05),
            ('sec ret_20d < -10%', pl.col('sec_etf_ret_20d') < -0.10),
        ]:
            r = cohort_stats(tier_df, mask, f'{tier_name} × {label}')
            print(fmt_row(r))
            if r: rows.append({'family': 'H9_sec_momentum', **r})

    print('\n[H10] mcap × sector state interaction (small-cap responsiveness):')
    for tier_name, base_df in [('CALL 70+', calls_70), ('PUT <=25', puts_25)]:
        if base_df.height < N_FLOOR: continue
        # Filter to small-caps only for cohort, vs rest
        small = base_df.filter(pl.col('mcap_b') < 10)
        large = base_df.filter(pl.col('mcap_b') >= 50)
        if small.height < N_FLOOR or large.height < N_FLOOR:
            continue
        for label, mask_expr in [
            ('small + sec >+5%EMA',  pl.col('sec_etf_pct_ema50') > 5),
            ('small + sec RSI<30',    pl.col('sec_etf_rsi') < 30),
            ('small + sec RSI>70',    pl.col('sec_etf_rsi') > 70),
        ]:
            r = cohort_stats(small, mask_expr, f'{tier_name} × small_cap × {label}')
            print(fmt_row(r))
            if r: rows.append({'family': 'H10_mcap_x_sec', **r})

    # ------------------------------------------------------------------
    # B. NEW NEW — sector RSI on PUT side at multi-tier
    # ------------------------------------------------------------------
    print('\n[H11] continuous gradient signature (sec_RSI vs WR7):')
    # Show how WR7 trends across sec_RSI quintiles per tier — informative for SWPM gradient design
    df_q = df_all.with_columns(
        pl.col('sec_etf_rsi').qcut(5, labels=['Q1', 'Q2', 'Q3', 'Q4', 'Q5'], allow_duplicates=True).alias('sec_rsi_q')
    )
    for label, sub in [('CALL 70+', df_q.filter((pl.col('signal_type') == 'CALL') & (pl.col('overall') >= 70))),
                       ('CALL 75+', df_q.filter((pl.col('signal_type') == 'CALL') & (pl.col('overall') >= 75))),
                       ('PUT <=25', df_q.filter((pl.col('signal_type') == 'PUT') & (pl.col('overall') <= 25)))]:
        if sub.height < 100:
            continue
        agg = sub.group_by('sec_rsi_q').agg(
            pl.len().alias('n'),
            (pl.col('wr_7').cast(pl.Float64).mean() * 100).alias('wr7'),
            (pl.col('sec_etf_rsi').mean()).alias('sec_rsi_mid'),
        ).sort('sec_rsi_mid')
        print(f'  {label}:')
        for r in agg.iter_rows(named=True):
            print(f'    {r["sec_rsi_q"]:<5} sec_RSI~{r["sec_rsi_mid"]:5.1f}  N={r["n"]:>5,d}  WR7={r["wr7"]:5.2f}')

    # Save
    if rows:
        out = pl.DataFrame(rows)
        out.write_parquet(LOCAL / 'phase_b_cohort_z.parquet')
        print(f'\n[save] phase_b_cohort_z.parquet ({len(rows)} rows)')
        # W1-passing cells
        passing = out.filter(pl.col('z').abs() >= 3.0)
        print(f'\n[W1] cells with |z| >= 3.0: {passing.height} / {len(rows)}')
        if passing.height > 0:
            for r in passing.sort('z', descending=False).iter_rows(named=True):
                sign = '+' if r['delta_pp'] >= 0 else ''
                print(f"  z={r['z']:+5.2f}  Δ={sign}{r['delta_pp']:5.2f}pp  N={r['N_cohort']:>5,d}  | {r['family']:<20} | {r['label']}")


if __name__ == '__main__':
    main()
