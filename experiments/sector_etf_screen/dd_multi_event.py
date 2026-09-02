"""Multi-event DD analysis — find ALL drawdown events ≥10% over the 6y v44 backtest,
classify each as market-driven (SPY crashing too) vs strategy-driven, and identify
which sector ETF breadth knobs would have effectively flagged bad cohorts WITHOUT
killing alpha during normal periods.

Key reframe: instead of a single-window analysis, analyze:
  - All drawdown events ≥10% (peak-to-trough) → larger N, more stable stats
  - Per-event SPY return during same window → market-driven vs strategy-driven
  - Sector breadth PATTERN in 5 days before peak (lead indicator?)
  - Knob effectiveness on combined in-DD trades vs combined outside trades
"""
from __future__ import annotations
import io, sys, time, math, pickle
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date as _date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'
RUNNER_CACHE = ROOT / '.cache' / 'runner'

SECTOR_ETF_MAP = {
    'technology':            'XLK',
    'healthcare':            'XLV',
    'consumer-cyclical':     'XLY',
    'industrials':           'XLI',
    'basic-materials':       'XLB',
    'real-estate':           'XLRE',
    'financial-services':    'XLF',
    'energy':                'XLE',
    'consumer-defensive':    'XLP',
    'utilities':             'XLU',
    'communication-services':'XLC',
}


def find_dd_events(equity_curve: list, min_dd_pct: float = 10.0) -> list:
    """Find all non-overlapping peak-to-trough-to-recovery DD events ≥ min_dd_pct.

    Algorithm: walk the equity curve. When eq ≥ running_peak, we're at a peak (no DD).
    When eq < running_peak, we're in a DD; track the deepest trough. When eq returns
    to ≥ running_peak (recovery), close out the event if its DD was ≥ min_dd_pct,
    and reset to a fresh peak.
    """
    events = []
    if not equity_curve:
        return events

    running_peak_date, running_peak_eq = equity_curve[0]
    trough_date, trough_eq = running_peak_date, running_peak_eq
    in_dd = False

    for d, eq in equity_curve[1:]:
        if eq >= running_peak_eq:
            # Recovered to or above peak — close event if it was deep enough
            if in_dd:
                dd = (running_peak_eq - trough_eq) / running_peak_eq
                if dd >= min_dd_pct / 100:
                    events.append({
                        'peak_date': running_peak_date,
                        'peak_eq': running_peak_eq,
                        'trough_date': trough_date,
                        'trough_eq': trough_eq,
                        'dd_pct': dd * 100,
                        'recovery_date': d,
                        'duration_days': (trough_date - running_peak_date).days,
                        'recovery_days': (d - trough_date).days,
                    })
                in_dd = False
            running_peak_date = d
            running_peak_eq = eq
            trough_date = d
            trough_eq = eq
        else:
            # Below peak — track deepest trough
            if eq < trough_eq:
                trough_eq = eq
                trough_date = d
            in_dd = True

    # Close final event if still in DD at end of data
    if in_dd:
        dd = (running_peak_eq - trough_eq) / running_peak_eq
        if dd >= min_dd_pct / 100:
            events.append({
                'peak_date': running_peak_date,
                'peak_eq': running_peak_eq,
                'trough_date': trough_date,
                'trough_eq': trough_eq,
                'dd_pct': dd * 100,
                'recovery_date': None,
                'duration_days': (trough_date - running_peak_date).days,
                'recovery_days': None,
            })

    return events


def load_spy_close() -> dict:
    """Daily SPY close price for context."""
    from database.trader_database import DB
    cur = DB.execute_sql("""
        SELECT date, close FROM price_history WHERE symbol = 'SPY' ORDER BY date
    """)
    return {d.isoformat() if hasattr(d, 'isoformat') else str(d): float(c)
            for d, c in cur.fetchall()}


def main():
    t0 = time.time()
    print('=' * 76)
    print('v44 Multi-Event DD Analysis')
    print('=' * 76)

    # 1) Load cached backtest result
    cache_pkl = LOCAL_CACHE / 'v44_backtest_result.pkl'
    with open(cache_pkl, 'rb') as f:
        result = pickle.load(f)
    eq_curve = result['equity_curve']
    trade_log = result['trade_log']
    print(f'[backtest] cached: eq_curve={len(eq_curve):,} days, trade_log={len(trade_log):,} trades')

    # 2) Find ALL DD events ≥10%
    events = find_dd_events(eq_curve, min_dd_pct=10.0)
    print(f'\n[DD events ≥10%]  {len(events)} found')

    # 3) SPY context per event
    spy_close = load_spy_close()
    print(f'\n  {"#":>2s}  {"peak_date":<11s}  {"trough_date":<11s}  {"dur":>4s}  '
          f'{"dd%":>6s}  {"rec_d":>6s}  {"SPY_ret":>8s}  {"market":>6s}')
    print(f'  {"-"*2}  {"-"*11}  {"-"*11}  {"-"*4}  {"-"*6}  {"-"*6}  {"-"*8}  {"-"*6}')
    for i, e in enumerate(events, 1):
        pd_iso = e['peak_date'].isoformat() if hasattr(e['peak_date'], 'isoformat') else str(e['peak_date'])
        td_iso = e['trough_date'].isoformat() if hasattr(e['trough_date'], 'isoformat') else str(e['trough_date'])
        spy_p = spy_close.get(pd_iso)
        spy_t = spy_close.get(td_iso)
        if spy_p and spy_t:
            spy_ret = (spy_t - spy_p) / spy_p * 100
        else:
            spy_ret = None
        # Classify: SPY < -3% during window = market-driven; else strategy-driven
        if spy_ret is None:
            mkt = '?'
        elif spy_ret < -5:
            mkt = 'mkt'
        elif spy_ret < -1:
            mkt = 'mixed'
        else:
            mkt = 'strat'
        rec = f'{e["recovery_days"]}d' if e['recovery_days'] else 'open'
        spy_s = f'{spy_ret:+7.1f}%' if spy_ret is not None else '   --   '
        print(f'  {i:>2d}  {pd_iso:<11s}  {td_iso:<11s}  {e["duration_days"]:>4d}  '
              f'{e["dd_pct"]:>6.1f}  {rec:>6s}  {spy_s:>8s}  {mkt:>6s}')
        e['spy_ret_pct'] = spy_ret
        e['market_class'] = mkt
        e['peak_iso'] = pd_iso
        e['trough_iso'] = td_iso

    # 4) Bucket trades into per-event in_dd flags
    from database.models.core import Stock
    sec_lookup = {s.symbol: (s.sector or 'unknown')
                  for s in Stock.select(Stock.symbol, Stock.sector)}

    rows = []
    for t in trade_log:
        sym = t.get('symbol')
        side_raw = t.get('side', 'call')
        side = 'CALL' if side_raw in ('call', 'low') else 'PUT'
        ed = t.get('entry_date')
        ed_iso = ed.isoformat() if hasattr(ed, 'isoformat') else str(ed)
        # Tag with event index if entry_date in a DD window
        event_idx = -1
        for i, e in enumerate(events, 1):
            if e['peak_iso'] <= ed_iso <= e['trough_iso']:
                event_idx = i
                break
        rows.append({
            'symbol': sym,
            'date': ed_iso,
            'side': side,
            'tier': t.get('tier'),
            'overall': float(t.get('score', 0)),
            'sector': sec_lookup.get(sym, 'unknown'),
            'tp': 1 if (t.get('outcome') or '').upper() == 'TP' else 0,
            'pnl_pct': float(t.get('pnl_pct') or 0),
            'in_dd': 1 if event_idx > 0 else 0,
            'dd_event': event_idx,
        })
    df = pl.DataFrame(rows)
    print(f'\n[trades]  total={len(df):,}  in_dd={df.filter(pl.col("in_dd")==1).height:,}  '
          f'outside={df.filter(pl.col("in_dd")==0).height:,}')

    # Per-event trade composition
    print('\n  Trade composition per event:')
    print(f'  {"#":>2s}  {"window":<25s}  {"N":>5s}  {"call":>5s}  {"put":>5s}  '
          f'{"TP%":>6s}  {"call_TP":>8s}  {"put_TP":>7s}')
    for i, e in enumerate(events, 1):
        sub = df.filter(pl.col('dd_event') == i)
        if len(sub) == 0:
            continue
        n_call = sub.filter(pl.col('side') == 'CALL').height
        n_put = sub.filter(pl.col('side') == 'PUT').height
        tp_overall = sub['tp'].mean() * 100
        tp_call = sub.filter(pl.col('side') == 'CALL')['tp'].mean() * 100 if n_call > 0 else None
        tp_put = sub.filter(pl.col('side') == 'PUT')['tp'].mean() * 100 if n_put > 0 else None
        win = f'{e["peak_iso"]}→{e["trough_iso"][5:]}'
        c_tp_s = f'{tp_call:.1f}%' if tp_call is not None else '--'
        p_tp_s = f'{tp_put:.1f}%' if tp_put is not None else '--'
        print(f'  {i:>2d}  {win:<25s}  {len(sub):>5d}  {n_call:>5d}  {n_put:>5d}  '
              f'{tp_overall:>5.1f}%  {c_tp_s:>8s}  {p_tp_s:>7s}')

    # 5) Sector breadth lead-indicator: state in 5 days BEFORE peak per event
    sec_brd_csv = LOCAL_CACHE / 'sector_breadth_daily_2020plus.csv'
    sec_brd = pl.read_csv(sec_brd_csv)
    print('\n  Sector breadth pattern in 5 days BEFORE peak per event:')
    print(f'  {"#":>2s}  {"peak":<11s}  {"sec_brd_5d_avg":>14s}  {"trend":>15s}')
    for i, e in enumerate(events, 1):
        peak_d = _date.fromisoformat(e['peak_iso'])
        prior_start = (peak_d - timedelta(days=8)).isoformat()
        prior_end = peak_d.isoformat()
        prior = sec_brd.filter(
            (pl.col('date') >= prior_start) & (pl.col('date') <= prior_end)
        ).sort('date')
        if len(prior) == 0:
            continue
        avg = prior['sec_brd_ema50'].mean()
        first = prior['sec_brd_ema50'][0]
        last = prior['sec_brd_ema50'][-1]
        trend = f'{first:.0f} → {last:.0f}'
        print(f'  {i:>2d}  {e["peak_iso"]:<11s}  {avg:>14.1f}  {trend:>15s}')

    # 6) Knob effectiveness on combined in-DD vs outside-DD (with SPY classification)
    # First, attach sector ETF features
    etf_wide = pl.read_parquet(LOCAL_CACHE / 'etf_features_wide.parquet').with_columns(
        pl.col('date').cast(pl.Utf8))
    df = df.join(sec_brd.with_columns(pl.col('date').cast(pl.Utf8)), on='date', how='left')
    df = df.join(etf_wide, on='date', how='left')

    # Per-stock-sector ETF features
    expr_pct = pl.lit(None, dtype=pl.Float64)
    expr_rsi = pl.lit(None, dtype=pl.Float64)
    for sec, etf in SECTOR_ETF_MAP.items():
        if f'{etf}_pct_ema50' in df.columns:
            expr_pct = pl.when(pl.col('sector') == sec).then(pl.col(f'{etf}_pct_ema50')).otherwise(expr_pct)
        if f'{etf}_rsi' in df.columns:
            expr_rsi = pl.when(pl.col('sector') == sec).then(pl.col(f'{etf}_rsi')).otherwise(expr_rsi)
    df = df.with_columns([
        expr_pct.alias('sec_etf_pct_ema50'),
        expr_rsi.alias('sec_etf_rsi'),
    ])

    # Production breadth from runner cache
    prod_brd_pq = RUNNER_CACHE / 'breadth_1825.parquet'
    if prod_brd_pq.exists():
        prod_brd = pl.read_parquet(prod_brd_pq).with_columns(pl.col('date').cast(pl.Utf8))
        df = df.join(prod_brd, on='date', how='left')
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias('breadth_score'))

    # Tag each trade by SPY classification of its DD event
    spy_class_map = {i: e.get('market_class', 'strat') for i, e in enumerate(events, 1)}
    df = df.with_columns(
        pl.col('dd_event').map_elements(
            lambda i: spy_class_map.get(i, 'none'), return_dtype=pl.Utf8
        ).alias('mkt_class')
    )

    print('\n  In-DD TP rate by market classification:')
    for cls in ['mkt', 'mixed', 'strat']:
        sub = df.filter(pl.col('mkt_class') == cls)
        if len(sub) == 0: continue
        print(f'    {cls}: N={len(sub):,}  TP={sub["tp"].mean()*100:.1f}%  '
              f'(call N={sub.filter(pl.col("side")=="CALL").height}, '
              f'put N={sub.filter(pl.col("side")=="PUT").height})')

    # Knob analysis on STRATEGY-driven DD trades only (exclude COVID-style market crashes)
    print('\n' + '=' * 76)
    print('Knob discrimination — STRATEGY-DRIVEN DDs only (excl. market crashes)')
    print('=' * 76)

    df_strat = df.filter((pl.col('mkt_class') == 'strat') | (pl.col('mkt_class') == 'mixed'))
    df_outside = df.filter(pl.col('in_dd') == 0)
    print(f'\n  in-DD strat/mixed: N={len(df_strat):,}  TP={df_strat["tp"].mean()*100:.1f}%')
    print(f'  outside-DD: N={len(df_outside):,}  TP={df_outside["tp"].mean()*100:.1f}%')

    knobs = [
        ('K1_call_secETF_RSI_gt70',     "CALL & sec_etf_rsi > 70",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_rsi') > 70)),
        ('K2_call_secETF_pct_gt5',      "CALL & sec_etf_pct_ema50 > +5%",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_pct_ema50') > 5)),
        ('K3_call_xsec_brd_gt90',       "CALL & cross_sec_brd > 90",
         (pl.col('side') == 'CALL') & (pl.col('sec_brd_ema50') > 90)),
        ('K4_call_secAvgRSI_gt62',      "CALL & sec_avg_rsi > 62",
         (pl.col('side') == 'CALL') & (pl.col('sec_avg_rsi') > 62)),
        ('K5_call_xsec_brd_lt30',       "CALL & cross_sec_brd < 30 (broad weakness)",
         (pl.col('side') == 'CALL') & (pl.col('sec_brd_ema50') < 30)),
        ('K6_call_secETF_RSI_lt35',     "CALL & sec_etf_rsi < 35",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_rsi') < 35)),
        ('P1_put_secETF_RSI_lt30',      "PUT & sec_etf_rsi < 30",
         (pl.col('side') == 'PUT') & (pl.col('sec_etf_rsi') < 30)),
        ('P2_put_secETF_pct_ltn5',      "PUT & sec_etf_pct_ema50 < -5%",
         (pl.col('side') == 'PUT') & (pl.col('sec_etf_pct_ema50') < -5)),
        ('P3_put_xsec_brd_lt20',        "PUT & cross_sec_brd < 20",
         (pl.col('side') == 'PUT') & (pl.col('sec_brd_ema50') < 20)),
        ('C1_call_RSI70_xsec80',        "CALL & sec_etf_rsi>70 & xsec_brd>80",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_rsi') > 70) & (pl.col('sec_brd_ema50') > 80)),
        ('D1_call_secWeak_prodOK',      "CALL & sec_etf_pct<-3 & prod_brd>50 (divergence)",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_pct_ema50') < -3) & (pl.col('breadth_score') > 50)),
    ]

    print(f'\n  {"knob":<28s} {"in-DD-flag%":>11s} {"flag-TP":>8s} {"unflag-TP":>10s} '
          f'{"in-gap":>7s} {"out-gap":>8s} {"net":>6s}  description')
    print(f'  {"-"*28} {"-"*11} {"-"*8} {"-"*10} {"-"*7} {"-"*8} {"-"*6}  {"-"*40}')

    results = []
    for name, desc, cond in knobs:
        try:
            inside = df_strat
            outside = df_outside
            ifl = inside.filter(cond)
            iuf = inside.filter(~cond)
            ofl = outside.filter(cond)
            ouf = outside.filter(~cond)
            in_flag_pct = len(ifl)/len(inside)*100 if len(inside) > 0 else 0
            in_flag_tp  = ifl['tp'].mean()*100 if len(ifl) > 0 else None
            in_unflag_tp = iuf['tp'].mean()*100 if len(iuf) > 0 else None
            out_flag_tp = ofl['tp'].mean()*100 if len(ofl) > 0 else None
            out_unflag_tp = ouf['tp'].mean()*100 if len(ouf) > 0 else None

            in_gap = (in_unflag_tp - in_flag_tp) if (in_flag_tp is not None) else 0
            out_gap = (out_unflag_tp - out_flag_tp) if (out_flag_tp is not None) else 0
            net = round(in_gap - out_gap, 1)

            if_tp_s = f'{in_flag_tp:>7.1f}' if in_flag_tp is not None else '    --'
            iuf_tp_s = f'{in_unflag_tp:>9.1f}' if in_unflag_tp is not None else '       --'
            print(f'  {name:<28s} {in_flag_pct:>11.1f} {if_tp_s} {iuf_tp_s} '
                  f'{in_gap:>+7.1f} {out_gap:>+8.1f} {net:>+6.1f}  {desc}')

            results.append({
                'knob': name,
                'in_flag_pct': round(in_flag_pct, 1),
                'in_flag_tp': round(in_flag_tp, 2) if in_flag_tp else None,
                'in_unflag_tp': round(in_unflag_tp, 2) if in_unflag_tp else None,
                'in_gap': round(in_gap, 2),
                'out_gap': round(out_gap, 2),
                'net_selectivity': net,
                'in_flagged_n': len(ifl),
                'out_flagged_n': len(ofl),
            })
        except Exception as e:
            print(f'  {name}: ERROR {e}')

    pl.DataFrame(results).sort('net_selectivity', descending=True).write_csv(
        LOCAL_CACHE / 'dd_multi_knob.csv')
    print(f'\n[saved] dd_multi_knob.csv')
    print(f'\n[done] {time.time()-t0:.1f}s')


if __name__ == '__main__':
    main()
