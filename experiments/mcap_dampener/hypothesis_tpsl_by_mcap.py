"""Quick screening — does MFE/MAE distribution differ by mcap cohort
in ways that would justify per-mcap TP/SL barriers?

Hypothesis to test:
  Beyond the "small-caps fire fewer TPs" cohort signal we already exploited
  (MCD score-stage dampener), do small-cap signals show characteristically
  different MFE/MAE patterns? Three flavors:

  (1) Small-cap winners with deeper MAE than large-cap winners → tighter SL
      would have falsely stopped them out. Implication: small-caps need
      WIDER SL.
  (2) Small-cap losers with higher MFE than large-cap losers → "almost won"
      signals that reversed late. Implication: tighter TP would capture
      these before reversal — small-caps need NARROWER TP.
  (3) Small-cap losers with lower MFE than large-cap losers → bearish
      breakdowns with no TP-side run. Implication: nothing TP-side helps;
      could go wider SL or tighter SL depending on the MAE distribution.

We use the existing 30dte_opt @ W=15d barrier (TP=1.274σ / SL=0.983σ) as
the labeling reference. opt_mfe_15 and opt_mae_15 are RAW PERCENTAGES of
entry price, NOT sigma-normalized — a key caveat below.

Outputs: per-mcap-bin tables of MFE/MAE distributions split by win/loss.
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / '.cache' / 'mcap_dampener'


def add_mcap_bin(df):
    return df.with_columns(
        pl.when(pl.col('mcap_b').is_null()).then(pl.lit('etf_or_unknown'))
          .when(pl.col('mcap_b') < 2).then(pl.lit('micro_lt2B'))
          .when(pl.col('mcap_b') < 10).then(pl.lit('small_2-10B'))
          .when(pl.col('mcap_b') < 50).then(pl.lit('mid_10-50B'))
          .when(pl.col('mcap_b') < 200).then(pl.lit('large_50-200B'))
          .when(pl.col('mcap_b') < 1000).then(pl.lit('xl_200B-1T'))
          .otherwise(pl.lit('mega_1T+')).alias('mcap_bin')
    )


def main():
    parquet = LOCAL / 'calls_v39_3650.parquet'
    df = pl.read_parquet(parquet)
    # Resolved with mcap, 5y window
    today = date.today()
    w5y = (today - timedelta(days=1825)).isoformat()
    df = df.filter(
        pl.col('opt_result_15').is_not_null()
        & pl.col('mcap_b').is_not_null()
        & (pl.col('date') >= w5y)
    )
    df = add_mcap_bin(df)
    print(f'Loaded {len(df):,} 5y resolved call peaks (overall>=70) with mcap')

    # Restrict to qualifying calls (75+) — that's where MCD acts and where
    # alternate TP/SL would be applied in production
    df75 = df.filter(pl.col('overall') >= 75)
    print(f'75+ subset: {len(df75):,}')
    print()

    print('='*110)
    print('MFE/MAE DISTRIBUTIONS BY mcap_bin × outcome (75+ peaks, 5y) — values in raw % move')
    print('='*110)
    print('(Note: opt_mfe_15 / opt_mae_15 stored as raw percent, e.g., 5.66 = 5.66% move)')
    print('(Reporting MEDIAN + p25/p75 to avoid mean contamination from meme-rally outliers)')
    print()
    print(f"{'mcap_bin':<16} {'outcome':<6} {'N':>6} | {'p25_MFE':>9} {'p50_MFE':>9} {'p75_MFE':>9} | {'p25_MAE':>9} {'p50_MAE':>9} {'p75_MAE':>9}")
    print('-'*100)
    bin_order = ['micro_lt2B', 'small_2-10B', 'mid_10-50B', 'large_50-200B', 'xl_200B-1T', 'mega_1T+']
    for bin_lbl in bin_order:
        for outcome_lbl, outcome_val in [('TP', 1), ('SL', 0)]:
            sub = df75.filter((pl.col('mcap_bin') == bin_lbl) & (pl.col('opt_result_15') == outcome_val))
            if sub.height < 30:
                print(f'{bin_lbl:<16} {outcome_lbl:<6} {sub.height:>6} (sparse)')
                continue
            stats = sub.select([
                pl.col('opt_mfe_15').quantile(0.25).alias('mfe_p25'),
                pl.col('opt_mfe_15').quantile(0.50).alias('mfe_p50'),
                pl.col('opt_mfe_15').quantile(0.75).alias('mfe_p75'),
                pl.col('opt_mae_15').quantile(0.25).alias('mae_p25'),
                pl.col('opt_mae_15').quantile(0.50).alias('mae_p50'),
                pl.col('opt_mae_15').quantile(0.75).alias('mae_p75'),
            ]).row(0, named=True)
            print(f'{bin_lbl:<16} {outcome_lbl:<6} {sub.height:>6} | '
                  f'{stats["mfe_p25"]:>7.2f}% {stats["mfe_p50"]:>7.2f}% {stats["mfe_p75"]:>7.2f}% | '
                  f'{stats["mae_p25"]:>7.2f}% {stats["mae_p50"]:>7.2f}% {stats["mae_p75"]:>7.2f}%')
        print()

    # Hypothesis 1: do small-cap winners have deeper MAE?
    print('='*100)
    print('H1 — Winner MAE depth (would tighter SL falsely stop more small-cap winners?)')
    print('='*100)
    print(f"{'mcap_bin':<16} {'TP N':>6} {'p50_MAE':>10} {'p75_MAE':>10} {'p90_MAE':>10} {'pct_MAE>5%':>12} {'pct_MAE>10%':>13}")
    print('-'*90)
    for bin_lbl in bin_order:
        sub = df75.filter((pl.col('mcap_bin') == bin_lbl) & (pl.col('opt_result_15') == 1))
        n = sub.height
        if n < 30:
            print(f'{bin_lbl:<16} {n:>6} (sparse)')
            continue
        mae_p50 = sub.select(pl.col('opt_mae_15').quantile(0.50)).item()
        mae_p75 = sub.select(pl.col('opt_mae_15').quantile(0.75)).item()
        mae_p90 = sub.select(pl.col('opt_mae_15').quantile(0.90)).item()
        pct_5 = sub.select((pl.col('opt_mae_15') > 5.0).cast(pl.Float64).mean()).item()
        pct_10 = sub.select((pl.col('opt_mae_15') > 10.0).cast(pl.Float64).mean()).item()
        print(f'{bin_lbl:<16} {n:>6} {mae_p50:>9.2f}% {mae_p75:>9.2f}% {mae_p90:>9.2f}% {pct_5*100:>11.1f}% {pct_10*100:>12.1f}%')
    print()

    # Hypothesis 2: do small-cap losers have higher MFE (almost won)?
    print('='*100)
    print('H2 — Loser MFE height (would tighter TP capture more small-cap signals before reversal?)')
    print('='*100)
    print(f"{'mcap_bin':<16} {'SL N':>6} {'p50_MFE_loss':>13} {'p75_MFE_loss':>13} {'p90_MFE_loss':>13} {'pct_MFE>3%':>12} {'pct_MFE>5%':>12}")
    print('-'*100)
    for bin_lbl in bin_order:
        sub = df75.filter((pl.col('mcap_bin') == bin_lbl) & (pl.col('opt_result_15') == 0))
        n = sub.height
        if n < 30:
            print(f'{bin_lbl:<16} {n:>6} (sparse)')
            continue
        mfe_p50 = sub.select(pl.col('opt_mfe_15').quantile(0.50)).item()
        mfe_p75 = sub.select(pl.col('opt_mfe_15').quantile(0.75)).item()
        mfe_p90 = sub.select(pl.col('opt_mfe_15').quantile(0.90)).item()
        pct_3 = sub.select((pl.col('opt_mfe_15') > 3.0).cast(pl.Float64).mean()).item()
        pct_5 = sub.select((pl.col('opt_mfe_15') > 5.0).cast(pl.Float64).mean()).item()
        print(f'{bin_lbl:<16} {n:>6} {mfe_p50:>12.2f}% {mfe_p75:>12.2f}% {mfe_p90:>12.2f}% {pct_3*100:>11.1f}% {pct_5*100:>11.1f}%')
    print()

    # Hypothesis 3: how does exit_return distribute by mcap (for context)
    print('='*100)
    print('H3 — Exit return distribution (for context)')
    print('='*100)
    print(f"{'mcap_bin':<16} {'N':>6} {'TP rate':>8} {'p25_ret':>9} {'p50_ret':>9} {'p75_ret':>9}")
    print('-'*65)
    for bin_lbl in bin_order:
        sub = df75.filter(pl.col('mcap_bin') == bin_lbl)
        n = sub.height
        if n < 30:
            print(f'{bin_lbl:<16} {n:>6} (sparse)')
            continue
        tp_rate = sub.select((pl.col('opt_result_15') == 1).cast(pl.Float64).mean()).item()
        ret_p25 = sub.select(pl.col('opt_exit_return_15').quantile(0.25)).item()
        ret_p50 = sub.select(pl.col('opt_exit_return_15').quantile(0.50)).item()
        ret_p75 = sub.select(pl.col('opt_exit_return_15').quantile(0.75)).item()
        print(f'{bin_lbl:<16} {n:>6} {tp_rate*100:>7.2f}% {ret_p25:>8.2f}% {ret_p50:>8.2f}% {ret_p75:>8.2f}%')
    print()

    # Bonus: split losers by "near-miss" (high MFE) vs "outright fail" (low MFE)
    print('='*100)
    print('H4 — Near-miss losers: MFE > 5% before SL fires (interpreted: TP was almost reached then reversed)')
    print('='*100)
    print(f"{'mcap_bin':<16} {'N_total':>8} {'N_loss':>7} {'N_loss_MFE>5%':>15} {'pct_near_miss':>14}")
    print('-'*70)
    for bin_lbl in bin_order:
        sub_all = df75.filter(pl.col('mcap_bin') == bin_lbl)
        sub_loss = sub_all.filter(pl.col('opt_result_15') == 0)
        sub_near = sub_loss.filter(pl.col('opt_mfe_15') > 0.05)
        if sub_all.height < 30: continue
        pct_near = sub_near.height / sub_loss.height * 100 if sub_loss.height > 0 else 0
        print(f'{bin_lbl:<16} {sub_all.height:>8,} {sub_loss.height:>7,} {sub_near.height:>14,} {pct_near:>13.1f}%')


if __name__ == '__main__':
    main()
