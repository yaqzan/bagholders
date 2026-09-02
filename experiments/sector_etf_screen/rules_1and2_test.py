"""Empirically test the two-rule proposal:
  Rule 1: when sec_brd < 20, prioritize puts in cascade
  Rule 2: when sec_brd > 80, do not fire puts at all

For Rule 2: directly testable from trade_log. Filter the puts that would have been
suppressed and measure their actual TP rate + cumulative pnl contribution.

For Rule 1: the cascade reordering effect can't be exactly recovered from trade_log
(we only see filled trades, not bumped ones). But we can compute:
  - At brd<20 days with active signals, how often did puts AND calls coexist?
  - On those days, what was call TP vs put TP?
  - If puts CONSISTENTLY win on those days, the priority rule has merit;
    if mixed, the rule's signal is weaker than it looks.
"""
from __future__ import annotations
import io, sys, pickle
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'


def main():
    print('=' * 76)
    print('Rules 1 & 2 — empirical test on v44 trade_log')
    print('=' * 76)

    with open(LOCAL_CACHE / 'v44_backtest_result.pkl', 'rb') as f:
        result = pickle.load(f)
    trade_log = result['trade_log']

    sec_brd = pl.read_csv(LOCAL_CACHE / 'sector_breadth_daily_2020plus.csv')

    rows = []
    for t in trade_log:
        ed = t.get('entry_date')
        ed_iso = ed.isoformat() if hasattr(ed, 'isoformat') else str(ed)
        side_raw = t.get('side', 'call')
        side = 'CALL' if side_raw in ('call', 'low') else 'PUT'
        rows.append({
            'date': ed_iso,
            'side': side,
            'tier': t.get('tier'),
            'overall': float(t.get('score', 0)),
            'tp': 1 if (t.get('outcome') or '').upper() == 'TP' else 0,
            'pnl_pct': float(t.get('pnl_pct') or 0),
            'pnl': float(t.get('pnl') or 0),
            'premium': float(t.get('premium') or 0),
        })
    df = pl.DataFrame(rows).join(
        sec_brd.with_columns(pl.col('date').cast(pl.Utf8)), on='date', how='left'
    )

    # Filter to trades with breadth available
    df = df.filter(pl.col('sec_brd_ema50').is_not_null())
    print(f'\n[trades joined to sec_brd] N={len(df):,}')

    base_call = df.filter(pl.col('side') == 'CALL')
    base_put  = df.filter(pl.col('side') == 'PUT')
    print(f'  baseline:   CALL N={len(base_call):,}  TP={base_call["tp"].mean()*100:.1f}%  '
          f'mean pnl%={base_call["pnl_pct"].mean()*100:+.2f}%')
    print(f'              PUT  N={len(base_put):,}  TP={base_put["tp"].mean()*100:.1f}%  '
          f'mean pnl%={base_put["pnl_pct"].mean()*100:+.2f}%')

    # ───── Rule 2: brd > 80 → no puts ────────────────────────────────────────
    print('\n' + '=' * 76)
    print('Rule 2 — suppress PUTs when sec_brd > 80')
    print('=' * 76)

    suppressed = df.filter((pl.col('side') == 'PUT') & (pl.col('sec_brd_ema50') > 80))
    surviving_puts = df.filter((pl.col('side') == 'PUT') & (pl.col('sec_brd_ema50') <= 80))

    print(f'\n  PUTs at brd > 80 (would be suppressed): N={len(suppressed):,}')
    print(f'    TP rate: {suppressed["tp"].mean()*100:.1f}%')
    print(f'    Mean pnl%: {suppressed["pnl_pct"].mean()*100:+.2f}%')
    total_pnl_suppressed = suppressed['pnl'].sum()
    print(f'    Cumulative dollar pnl: ${total_pnl_suppressed:,.0f}')
    n_winners = suppressed.filter(pl.col('tp') == 1).height
    n_losers = suppressed.filter(pl.col('tp') == 0).height
    print(f'    Wins/Losses: {n_winners} TP / {n_losers} non-TP')

    print(f'\n  PUTs at brd ≤ 80 (would survive): N={len(surviving_puts):,}')
    print(f'    TP rate: {surviving_puts["tp"].mean()*100:.1f}%')
    print(f'    Mean pnl%: {surviving_puts["pnl_pct"].mean()*100:+.2f}%')

    # Compare TP rate across breadth bands for puts
    print(f'\n  PUT TP rate by sec_brd band:')
    for label, lo, hi in [('<20',  0,  20), ('20-40', 20, 40), ('40-60', 40, 60),
                          ('60-80', 60, 80), ('>80', 80, 999)]:
        sub = base_put.filter((pl.col('sec_brd_ema50') > lo) & (pl.col('sec_brd_ema50') <= hi))
        if len(sub) == 0: continue
        print(f'    sec_brd {label:<8s} N={len(sub):>5d}  TP={sub["tp"].mean()*100:>5.1f}%  '
              f'meanPnL%={sub["pnl_pct"].mean()*100:>+6.2f}%')

    # Net pnl%-weighted contribution by band — this is what really matters for portfolio
    print(f'\n  PUT total $-pnl by sec_brd band:')
    for label, lo, hi in [('<20',  0,  20), ('20-40', 20, 40), ('40-60', 40, 60),
                          ('60-80', 60, 80), ('>80', 80, 999)]:
        sub = base_put.filter((pl.col('sec_brd_ema50') > lo) & (pl.col('sec_brd_ema50') <= hi))
        if len(sub) == 0: continue
        print(f'    sec_brd {label:<8s} N={len(sub):>5d}  '
              f'sum_pnl=${sub["pnl"].sum():>+18,.0f}  '
              f'avg_premium=${sub["premium"].mean():>12,.0f}')

    # ───── Rule 1: brd < 20 → prioritize puts ───────────────────────────────
    print('\n' + '=' * 76)
    print('Rule 1 — prioritize PUTs when sec_brd < 20')
    print('=' * 76)

    # Days where brd<20 AND both call+put fired
    dates_brd_lo = df.filter(pl.col('sec_brd_ema50') < 20)['date'].unique().to_list()
    print(f'\n  Days with sec_brd < 20: {len(dates_brd_lo):,}')

    days_lo_with_calls = df.filter(
        (pl.col('sec_brd_ema50') < 20) & (pl.col('side') == 'CALL')
    )['date'].unique().to_list()
    days_lo_with_puts = df.filter(
        (pl.col('sec_brd_ema50') < 20) & (pl.col('side') == 'PUT')
    )['date'].unique().to_list()
    days_with_both = set(days_lo_with_calls) & set(days_lo_with_puts)
    print(f'  Days w/ CALL fired: {len(days_lo_with_calls)}')
    print(f'  Days w/ PUT fired:  {len(days_lo_with_puts)}')
    print(f'  Days w/ BOTH:       {len(days_with_both)}  ← rule 1 changes ordering on these days only')

    # On days with both, compare: if puts had filled the slots, what TP would we get?
    df_brd_lo = df.filter(pl.col('sec_brd_ema50') < 20)
    call_lo = df_brd_lo.filter(pl.col('side') == 'CALL')
    put_lo  = df_brd_lo.filter(pl.col('side') == 'PUT')
    print(f'\n  At sec_brd < 20:')
    print(f'    CALL N={len(call_lo):>4d}  TP={call_lo["tp"].mean()*100:>5.1f}%  '
          f'mean_pnl%={call_lo["pnl_pct"].mean()*100:>+6.2f}%')
    print(f'    PUT  N={len(put_lo):>4d}  TP={put_lo["tp"].mean()*100:>5.1f}%  '
          f'mean_pnl%={put_lo["pnl_pct"].mean()*100:>+6.2f}%')

    # On the subset of days_with_both:
    if len(days_with_both) > 0:
        days_b_list = list(days_with_both)
        sub_b = df_brd_lo.filter(pl.col('date').is_in(days_b_list))
        call_b = sub_b.filter(pl.col('side') == 'CALL')
        put_b = sub_b.filter(pl.col('side') == 'PUT')
        print(f'\n  Restricted to {len(days_with_both)} BOTH-fired-on-same-day cases:')
        print(f'    CALL N={len(call_b):>4d}  TP={call_b["tp"].mean()*100:>5.1f}%  '
              f'mean_pnl%={call_b["pnl_pct"].mean()*100:>+6.2f}%')
        print(f'    PUT  N={len(put_b):>4d}  TP={put_b["tp"].mean()*100:>5.1f}%  '
              f'mean_pnl%={put_b["pnl_pct"].mean()*100:>+6.2f}%')
        print(f'    side_delta: {(call_b["tp"].mean() - put_b["tp"].mean())*100:+.1f}pp '
              f'(positive = calls won, rule 1 would have hurt; negative = puts won, rule 1 would have helped)')

    # Asymmetry check at threshold extremes
    print('\n' + '=' * 76)
    print('Threshold sensitivity — finer breadth bands')
    print('=' * 76)
    print(f'\n  PUT TP rate, dollar pnl by FINE breadth band:')
    print(f'  {"band":<10s}  {"N":>4s}  {"TP%":>6s}  {"meanPnL%":>9s}  {"sumPnL$":>20s}')
    for label, lo, hi in [('<10', 0, 10), ('10-15', 10, 15), ('15-20', 15, 20),
                          ('20-30', 20, 30), ('30-50', 30, 50), ('50-70', 50, 70),
                          ('70-85', 70, 85), ('85-95', 85, 95), ('>95', 95, 999)]:
        sub = base_put.filter((pl.col('sec_brd_ema50') > lo) & (pl.col('sec_brd_ema50') <= hi))
        if len(sub) == 0: continue
        print(f'  {label:<10s}  {len(sub):>4d}  {sub["tp"].mean()*100:>5.1f}%  '
              f'{sub["pnl_pct"].mean()*100:>+8.2f}%  ${sub["pnl"].sum():>+18,.0f}')

    print(f'\n  CALL TP rate, dollar pnl by FINE breadth band:')
    print(f'  {"band":<10s}  {"N":>5s}  {"TP%":>6s}  {"meanPnL%":>9s}  {"sumPnL$":>20s}')
    for label, lo, hi in [('<10', 0, 10), ('10-15', 10, 15), ('15-20', 15, 20),
                          ('20-30', 20, 30), ('30-50', 30, 50), ('50-70', 50, 70),
                          ('70-85', 70, 85), ('85-95', 85, 95), ('>95', 95, 999)]:
        sub = base_call.filter((pl.col('sec_brd_ema50') > lo) & (pl.col('sec_brd_ema50') <= hi))
        if len(sub) == 0: continue
        print(f'  {label:<10s}  {len(sub):>5d}  {sub["tp"].mean()*100:>5.1f}%  '
              f'{sub["pnl_pct"].mean()*100:>+8.2f}%  ${sub["pnl"].sum():>+18,.0f}')


if __name__ == '__main__':
    main()
