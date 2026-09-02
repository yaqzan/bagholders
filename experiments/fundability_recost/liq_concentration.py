"""Liquidity-realizability — NON-INVASIVE tape analysis (no engine edits; concurrency-safe).

Answers: is the v70 Apex edge realizable, or is it concentrated in untradeable illiquid names?
Joins the existing MC trade-tape (.cache/dd_ledger/tape_*.parquet — already-computed, on the
live MWDD/RXDD/SVR config) to the trailing-30d $-volume liquidity proxy and reports, per
liquidity bin: trade share, per-trade EV (mean option_pnl), win-rate, and gross-$-PnL share.

Realizability logic: if (a) liquid names are the bulk of the supply AND (b) the liquid subset's
per-trade EV ≈ the full-book EV, then a liquid-only cascade (same slots, refilled with liquid
signals) compounds at ~the same rate -> the strategy is realizable. If the edge's EV is
concentrated in the illiquid tail, it's NOT (the liquidity analog of the v69 "3-names" concern).

Run: PYTHONIOENCODING=utf-8 python -u experiments/fundability_recost/liq_concentration.py
"""
from __future__ import annotations
import sys, glob
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from database.bulk_cache import cache_path  # noqa: E402

WINDOWS = ['5y', '10y', '22-now']
FLOORS = [5e6, 20e6, 50e6, 100e6, 250e6]
BIN_EDGES = [(0, 5e6, '<5M'), (5e6, 20e6, '5-20M'), (20e6, 50e6, '20-50M'),
             (50e6, 100e6, '50-100M'), (100e6, 250e6, '100-250M'), (250e6, 9e18, '>=250M')]


def load_liq():
    L = pl.read_parquet(cache_path('fundability_liq_dollar_vol_30d'))
    return L.with_columns(pl.col('date').dt.strftime('%Y-%m-%d').alias('entry_date')) \
            .rename({'symbol': 'sym_id'}).select(['sym_id', 'entry_date', 'liq30'])


def main():
    L = load_liq()
    for win in WINDOWS:
        f = ROOT / '.cache' / 'dd_ledger' / f'tape_{win}.parquet'
        if not f.exists():
            print(f'(no tape for {win})'); continue
        T = pl.read_parquet(f).filter(pl.col('side') == 'call')
        T = T.with_columns(pl.col('entry_date').cast(pl.Utf8))
        T = T.join(L, on=['sym_id', 'entry_date'], how='left')
        miss = T['liq30'].null_count()
        T = T.with_columns((pl.col('premium_cost') * pl.col('option_pnl')).alias('pnl_usd'))
        tot_trades = T.height
        tot_pnl = T['pnl_usd'].sum()
        full_ev = T['option_pnl'].mean()
        print(f"\n===== {win}: {tot_trades:,} call trades  (liq missing {miss})  full per-trade EV={full_ev:+.4f}  gross$PnL={tot_pnl:,.0f} =====")
        print(f"{'liq bin ($/day)':16s} {'trade%':>7s} {'mean_pnl':>9s} {'winrate':>8s} {'$PnL share':>11s}")
        for lo, hi, lab in BIN_EDGES:
            sub = T.filter((pl.col('liq30') >= lo) & (pl.col('liq30') < hi))
            if sub.height == 0:
                continue
            print(f"{lab:16s} {100*sub.height/tot_trades:>6.1f}% {sub['option_pnl'].mean():>+9.4f} "
                  f"{100*(sub['option_pnl']>0).mean():>7.1f}% {100*sub['pnl_usd'].sum()/tot_pnl:>10.1f}%")
        # cumulative "below floor" view (what filtering removes) + liquid-only EV
        print(f"  {'floor':>10s} {'drop trade%':>11s} {'drop $PnL%':>11s} {'liquid-only EV':>15s} {'(full EV':>9s} {full_ev:+.4f})")
        for fl in FLOORS:
            below = T.filter(pl.col('liq30') < fl)
            liquid = T.filter(pl.col('liq30') >= fl)
            dtr = 100 * below.height / tot_trades
            dpn = 100 * below['pnl_usd'].sum() / tot_pnl if tot_pnl else 0.0
            lev = liquid['option_pnl'].mean() if liquid.height else float('nan')
            print(f"  >=${int(fl/1e6):>4d}M {dtr:>10.1f}% {dpn:>10.1f}% {lev:>+15.4f}")
    print("\nReadout: if 'liquid-only EV' ~ full EV at a tradable floor (>=$20-50M/day) AND 'drop $PnL%'")
    print("is small, the edge is broad/realizable; if liquid-only EV collapses, it's concentrated in illiquid names.")


if __name__ == '__main__':
    main()
