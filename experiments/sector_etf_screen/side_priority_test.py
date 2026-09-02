"""Test: does sector breadth at DD-event time predict which SIDE wins during the DD?

For each of the 72 v44 DD events, compute:
  - avg cross-sec breadth during the peak-to-trough window
  - call TP rate during the event
  - put TP rate during the event
  - side delta (call_tp - put_tp)

If breadth correlates with side_delta — low breadth → puts win, high breadth → calls win —
then sector-breadth-driven side prioritization in the cascade is mechanically correct.

If correlation is weak/zero, the score system is already getting side selection right on
average and pure side-priority adds no alpha.
"""
from __future__ import annotations
import io, sys, pickle
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date as _date, timedelta
from pathlib import Path
import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'

# Re-use find_dd_events from dd_multi_event
sys.path.insert(0, str(ROOT / 'experiments' / 'sector_etf_screen'))
from dd_multi_event import find_dd_events


def main():
    print('=' * 76)
    print('Side priority hypothesis test — does sec breadth predict which side wins?')
    print('=' * 76)

    with open(LOCAL_CACHE / 'v44_backtest_result.pkl', 'rb') as f:
        result = pickle.load(f)
    eq_curve = result['equity_curve']
    trade_log = result['trade_log']
    events = find_dd_events(eq_curve, min_dd_pct=10.0)
    print(f'\n[{len(events)} DD events ≥10%]')

    # Sector breadth daily
    sec_brd = pl.read_csv(LOCAL_CACHE / 'sector_breadth_daily_2020plus.csv')

    # Build per-trade df with side+date+tp
    rows = []
    for t in trade_log:
        ed = t.get('entry_date')
        ed_iso = ed.isoformat() if hasattr(ed, 'isoformat') else str(ed)
        side_raw = t.get('side', 'call')
        side = 'CALL' if side_raw in ('call', 'low') else 'PUT'
        rows.append({
            'date': ed_iso,
            'side': side,
            'tp': 1 if (t.get('outcome') or '').upper() == 'TP' else 0,
        })
    df = pl.DataFrame(rows)
    df_brd = df.join(sec_brd.with_columns(pl.col('date').cast(pl.Utf8)),
                     on='date', how='left')

    # Per-event analysis
    rows_evt = []
    for i, e in enumerate(events, 1):
        peak_iso = e['peak_date'].isoformat() if hasattr(e['peak_date'], 'isoformat') else str(e['peak_date'])
        trough_iso = e['trough_date'].isoformat() if hasattr(e['trough_date'], 'isoformat') else str(e['trough_date'])

        evt_brd = sec_brd.filter(
            (pl.col('date') >= peak_iso) & (pl.col('date') <= trough_iso)
        )
        avg_brd = float(evt_brd['sec_brd_ema50'].mean()) if len(evt_brd) > 0 else None

        evt_trades = df_brd.filter(
            (pl.col('date') >= peak_iso) & (pl.col('date') <= trough_iso)
        )
        n_total = len(evt_trades)
        if n_total == 0:
            continue
        n_call = evt_trades.filter(pl.col('side') == 'CALL').height
        n_put = evt_trades.filter(pl.col('side') == 'PUT').height
        call_tp = (evt_trades.filter(pl.col('side') == 'CALL')['tp'].mean() * 100) if n_call > 0 else None
        put_tp = (evt_trades.filter(pl.col('side') == 'PUT')['tp'].mean() * 100) if n_put > 0 else None

        rows_evt.append({
            'event': i,
            'peak': peak_iso,
            'trough': trough_iso,
            'dd_pct': round(e['dd_pct'], 1),
            'avg_brd': round(avg_brd, 1) if avg_brd is not None else None,
            'n_total': n_total,
            'n_call': n_call,
            'n_put': n_put,
            'call_tp': round(call_tp, 1) if call_tp is not None else None,
            'put_tp': round(put_tp, 1) if put_tp is not None else None,
        })

    edf = pl.DataFrame(rows_evt)
    edf = edf.with_columns(
        (pl.col('call_tp') - pl.col('put_tp')).alias('side_delta_pp')
    )

    print(f'\n{"#":>3s}  {"peak":<11s}  {"dd%":>5s}  {"brd":>5s}  '
          f'{"N":>3s}  {"call_tp":>7s}  {"put_tp":>7s}  {"delta":>6s}')
    print(f'{"-"*3}  {"-"*11}  {"-"*5}  {"-"*5}  {"-"*3}  {"-"*7}  {"-"*7}  {"-"*6}')
    for r in edf.iter_rows(named=True):
        ct = f'{r["call_tp"]:.0f}%' if r['call_tp'] is not None else ' --'
        pt = f'{r["put_tp"]:.0f}%' if r['put_tp'] is not None else ' --'
        dl = f'{r["side_delta_pp"]:+.0f}pp' if r['side_delta_pp'] is not None else '   --'
        brd = f'{r["avg_brd"]:.0f}' if r['avg_brd'] is not None else '--'
        print(f'{r["event"]:>3d}  {r["peak"]:<11s}  {r["dd_pct"]:>5.1f}  {brd:>5s}  '
              f'{r["n_total"]:>3d}  {ct:>7s}  {pt:>7s}  {dl:>6s}')

    # Bin events by breadth quartile and compute average side_delta
    valid = edf.filter(pl.col('side_delta_pp').is_not_null() & pl.col('avg_brd').is_not_null())
    print(f'\n[N={len(valid)} events have both call AND put trades, valid for side_delta]')

    if len(valid) >= 10:
        b = valid['avg_brd'].to_numpy().astype('float64')
        d = valid['side_delta_pp'].to_numpy().astype('float64')
        rho = float(np.corrcoef(b, d)[0, 1])
        print(f'\n  Pearson correlation(avg sec_brd, call_tp - put_tp): {rho:+.4f}')
        print(f'    interpretation:')
        print(f'    +1 = high breadth → calls win (and low breadth → puts win) → SIDE PRIORITY VALIDATED')
        print(f'     0 = no relationship → score system already captures regime → side priority adds nothing')
        print(f'    -1 = inverted (would be a counter-trend reversion play, not what user proposed)')

        # Quartile bins
        p25 = np.quantile(b, 0.25)
        p50 = np.quantile(b, 0.5)
        p75 = np.quantile(b, 0.75)
        print(f'\n  Breadth quartile cuts: p25={p25:.0f}  p50={p50:.0f}  p75={p75:.0f}')
        print(f'  {"bucket":<15s} {"N":>3s} {"avg_brd":>8s} {"avg_call_tp":>11s} {"avg_put_tp":>10s} {"avg_delta":>9s}')
        for label, lo, hi in [('Q1_lowbrd', -1, p25), ('Q2', p25, p50),
                              ('Q3', p50, p75), ('Q4_hibrd', p75, 9999)]:
            sub = valid.filter((pl.col('avg_brd') > lo) & (pl.col('avg_brd') <= hi))
            if len(sub) == 0: continue
            print(f'  {label:<15s} {len(sub):>3d} '
                  f'{sub["avg_brd"].mean():>8.1f} '
                  f'{sub["call_tp"].mean():>10.1f}% '
                  f'{sub["put_tp"].mean():>9.1f}% '
                  f'{sub["side_delta_pp"].mean():>+8.1f}pp')

    # Also include events with only one side (treat missing side as "no signal of that type")
    print(f'\n  Per-event side-density vs breadth:')
    print(f'  Quartile         N_evts  Calls/event  Puts/event  Call:Put ratio')
    valid2 = edf.filter(pl.col('avg_brd').is_not_null())
    if len(valid2) >= 10:
        b = valid2['avg_brd'].to_numpy().astype('float64')
        p25 = np.quantile(b, 0.25)
        p50 = np.quantile(b, 0.5)
        p75 = np.quantile(b, 0.75)
        for label, lo, hi in [('Q1_lowbrd', -1, p25), ('Q2', p25, p50),
                              ('Q3', p50, p75), ('Q4_hibrd', p75, 9999)]:
            sub = valid2.filter((pl.col('avg_brd') > lo) & (pl.col('avg_brd') <= hi))
            if len(sub) == 0: continue
            avg_call = sub['n_call'].mean()
            avg_put = sub['n_put'].mean()
            ratio = (avg_call / avg_put) if avg_put > 0 else float('inf')
            ratio_s = f'{ratio:.2f}' if avg_put > 0 else 'inf'
            print(f'  {label:<15s} {len(sub):>3d}  {avg_call:>11.1f}  {avg_put:>10.1f}  {ratio_s:>14s}')

    edf.write_csv(LOCAL_CACHE / 'side_priority_per_event.csv')
    print(f'\n[saved] side_priority_per_event.csv')


if __name__ == '__main__':
    main()
