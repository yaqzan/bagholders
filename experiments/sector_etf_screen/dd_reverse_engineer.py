"""Reverse-engineer the v44 max DD: find the peak-to-trough window, identify which
trades fired during the drawdown, and test which sector ETF breadth knobs would have
discriminated bad-cohort trades from good ones during the DD.

Pipeline:
  1. Run backtest_cascade.run_backtest at v44 active.
  2. Scan equity_curve to locate peak-to-trough max DD window.
  3. Pull trades whose entry_date ∈ [peak, trough].
  4. Join trades to sector ETF state at entry date.
  5. For each candidate knob (cross-sec breadth, sec_etf_pct_ema50, sec_etf_rsi,
     sec_avg_rsi, prod-vs-sec divergence, etc.):
        - Compute knob value at signal date for each trade.
        - Score knob's discrimination: TP rate of "danger" subset vs "ok" subset
          BOTH during-DD AND outside-DD.
        - Effective knob: large discrimination during DD, small/null discrimination
          outside DD (catches the bad trades without killing the good ones).
  6. Output ranked knob effectiveness.

Read-only against MySQL via backtest_cascade. No production changes.
"""
from __future__ import annotations
import io, sys, time, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date as _date
from pathlib import Path
import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOCAL_CACHE = ROOT / '.cache' / 'sector_etf_screen'
LOCAL_CACHE.mkdir(parents=True, exist_ok=True)

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


def find_max_dd_window(equity_curve: list) -> dict:
    """Locate the peak-to-trough maximum drawdown window.

    Returns dict with: peak_date, peak_value, trough_date, trough_value, dd_pct,
    recovery_date (or None if not yet recovered).
    """
    peak_eq = -float('inf')
    peak_date = None
    max_dd = 0.0
    max_dd_peak_date = None
    max_dd_peak_eq = None
    max_dd_trough_date = None
    max_dd_trough_eq = None

    running_peak_eq = -float('inf')
    running_peak_date = None

    for d, eq in equity_curve:
        if eq > running_peak_eq:
            running_peak_eq = eq
            running_peak_date = d
        dd = (running_peak_eq - eq) / running_peak_eq if running_peak_eq > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_peak_date = running_peak_date
            max_dd_peak_eq = running_peak_eq
            max_dd_trough_date = d
            max_dd_trough_eq = eq

    # Recovery date: first day after trough where equity >= peak again
    recovery_date = None
    found_trough = False
    for d, eq in equity_curve:
        if d == max_dd_trough_date:
            found_trough = True
            continue
        if found_trough and eq >= max_dd_peak_eq:
            recovery_date = d
            break

    return {
        'peak_date': max_dd_peak_date,
        'peak_eq': max_dd_peak_eq,
        'trough_date': max_dd_trough_date,
        'trough_eq': max_dd_trough_eq,
        'dd_pct': max_dd * 100,
        'recovery_date': recovery_date,
        'duration_days': (max_dd_trough_date - max_dd_peak_date).days
                         if max_dd_peak_date and max_dd_trough_date else None,
    }


def load_etf_features() -> pl.DataFrame:
    """Load the ETF features wide-format parquet."""
    pq = LOCAL_CACHE / 'etf_features_wide.parquet'
    return pl.read_parquet(pq).with_columns(pl.col('date').cast(pl.Utf8))


def build_per_signal_features(trade_log: list,
                              etf_wide: pl.DataFrame,
                              sec_brd: pl.DataFrame) -> pl.DataFrame:
    """For each trade, build a row with entry_date features:
       - sec_etf_pct_ema50, sec_etf_rsi (per stock's primary sector)
       - cross-sec breadth (% of 11 SPDRs > EMA50)
       - sec_avg_rsi (mean RSI across 11 SPDRs)
       - production breadth_score (from breadth parquet)
       - tp (1=win, 0=loss)
       - tier
       - in_dd window flag
    """
    # Build sector lookup from Stock table
    from database.models.core import Stock
    sec_lookup = {s.symbol: (s.sector or 'unknown') for s in Stock.select(Stock.symbol, Stock.sector)}

    rows = []
    for t in trade_log:
        sym = t.get('symbol')
        side_raw = t.get('side', 'call')
        # backtest_cascade uses 'call'/'put' string; trader uses 'low'/'high'
        if side_raw in ('call', 'low'):
            side = 'CALL'
        else:
            side = 'PUT'
        rows.append({
            'symbol': sym,
            'entry_date': t.get('entry_date'),
            'side': side,
            'tier': t.get('tier'),
            'overall': float(t.get('score', 0)),
            'sector': sec_lookup.get(sym, 'unknown'),
            'pnl_pct': float(t.get('pnl_pct', 0) or 0),
            'tp': 1 if (t.get('outcome') or '').upper() == 'TP' else 0,
            'sl': 1 if (t.get('outcome') or '').upper() == 'SL' else 0,
            'outcome': t.get('outcome'),
            'stressed': bool(t.get('stressed', False)),
        })
    df = pl.DataFrame(rows)
    df = df.with_columns(pl.col('entry_date').cast(pl.Utf8).alias('date'))

    # Join cross-sec breadth (already daily)
    df = df.join(sec_brd.with_columns(pl.col('date').cast(pl.Utf8)),
                 on='date', how='left')

    # Join ETF wide for per-sector ETF state
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

    return df


def evaluate_knob(df: pl.DataFrame, knob_name: str, condition_expr,
                  inside_dd_col='in_dd', label='') -> dict:
    """Score a knob by:
       - inside-DD: how many trades does the knob flag as 'danger'? what's their TP rate?
       - outside-DD: how many trades flagged outside? what's their TP rate?
    Effective knob: high coverage of bad trades inside DD, low coverage of good trades outside DD."""

    # Apply condition — flag as danger
    flagged = df.filter(condition_expr)

    inside = df.filter(pl.col(inside_dd_col) == 1)
    outside = df.filter(pl.col(inside_dd_col) == 0)

    inside_flagged = inside.filter(condition_expr)
    outside_flagged = outside.filter(condition_expr)

    res = {
        'knob': knob_name,
        'label': label,
        'total_flagged': len(flagged),
        'inside_dd_total': len(inside),
        'inside_flagged': len(inside_flagged),
        'inside_flagged_pct': round(len(inside_flagged)/len(inside)*100, 1) if len(inside) > 0 else 0,
        'inside_unflagged_tp': round(inside.filter(~condition_expr)['tp'].mean()*100, 2) if len(inside) > 0 else 0,
        'inside_flagged_tp':   round(inside_flagged['tp'].mean()*100, 2) if len(inside_flagged) > 0 else None,
        'outside_dd_total': len(outside),
        'outside_flagged': len(outside_flagged),
        'outside_flagged_pct': round(len(outside_flagged)/len(outside)*100, 1) if len(outside) > 0 else 0,
        'outside_unflagged_tp': round(outside.filter(~condition_expr)['tp'].mean()*100, 2) if len(outside) > 0 else 0,
        'outside_flagged_tp':  round(outside_flagged['tp'].mean()*100, 2) if len(outside_flagged) > 0 else None,
    }
    # Discrimination metric: TP gap (unflagged - flagged) inside vs outside.
    # Inside DD: knob should flag bad trades → unflagged TP > flagged TP (positive gap)
    # Outside DD: knob should NOT discriminate → small gap
    res['inside_tp_gap']  = (res['inside_unflagged_tp'] - (res['inside_flagged_tp'] or 0))
    res['outside_tp_gap'] = (res['outside_unflagged_tp'] - (res['outside_flagged_tp'] or 0))
    # Net selectivity: high inside gap and low outside gap = ideal
    res['net_selectivity'] = round(res['inside_tp_gap'] - res['outside_tp_gap'], 2)
    return res


def main():
    t0 = time.time()
    print('=' * 76)
    print('v44 DD Reverse-Engineering — sector ETF knob discrimination test')
    print('=' * 76)

    # 1) Run backtest_cascade in-process via run_cascade_backtest
    cache_pkl = LOCAL_CACHE / 'v44_backtest_result.pkl'
    import pickle
    if cache_pkl.exists():
        print(f'[backtest] cache hit: {cache_pkl}')
        with open(cache_pkl, 'rb') as f:
            result = pickle.load(f)
    else:
        from backtest_cascade import run_cascade_backtest
        from database.models.core import AlgorithmVersion
        from datetime import date as _d
        av = AlgorithmVersion.get_active_scores_version()
        print(f'[backtest] running v{av.id} (commit {av.git_commit[:8]}) from 2020-01-01...')
        result = run_cascade_backtest(
            version_id=av.id,
            min_score=70.0,
            from_date=_d(2020, 1, 1),
            initial=50_000.0,
            verbose=True,
        )
        # outcomes_by_date contains Outcome objects which may not pickle cleanly;
        # strip what we don't need
        slim = {
            'equity_curve': result['equity_curve'],
            'trade_log':    result['trade_log'],
            'max_dd':       result['max_dd'],
            'final_equity': result.get('final_equity'),
            'initial':      result.get('initial'),
            'start_date':   result.get('start_date'),
            'end_date':     result.get('end_date'),
        }
        with open(cache_pkl, 'wb') as f:
            pickle.dump(slim, f)
        result = slim

    eq_curve = result['equity_curve']
    trade_log = result.get('trade_log', [])
    print(f'\n[backtest] eq_curve={len(eq_curve):,} days, trade_log={len(trade_log):,} trades')

    # 2) Find max DD window
    dd = find_max_dd_window(eq_curve)
    print(f'\n[max DD] peak {dd["peak_date"]} (eq=${dd["peak_eq"]:,.0f}) → '
          f'trough {dd["trough_date"]} (eq=${dd["trough_eq"]:,.0f})')
    print(f'         duration = {dd["duration_days"]} days, dd_pct = {dd["dd_pct"]:.1f}%')
    if dd.get('recovery_date'):
        rec_days = (dd['recovery_date'] - dd['trough_date']).days
        print(f'         recovered by {dd["recovery_date"]} ({rec_days} days post-trough)')
    else:
        print(f'         not yet recovered (still at trough or post-trough)')

    # 3) Build per-trade dataframe with entry_date features
    etf_wide = load_etf_features()
    sec_brd_csv = LOCAL_CACHE / 'sector_breadth_daily_2020plus.csv'
    sec_brd = pl.read_csv(sec_brd_csv)
    df = build_per_signal_features(trade_log, etf_wide, sec_brd)

    # Tag each trade as in-DD or outside-DD by entry_date
    peak_iso = dd['peak_date'].isoformat() if hasattr(dd['peak_date'], 'isoformat') else str(dd['peak_date'])
    trough_iso = dd['trough_date'].isoformat() if hasattr(dd['trough_date'], 'isoformat') else str(dd['trough_date'])
    df = df.with_columns(
        ((pl.col('date') >= peak_iso) & (pl.col('date') <= trough_iso))
        .cast(pl.Int32).alias('in_dd')
    )
    n_in = df.filter(pl.col('in_dd') == 1).height
    n_out = df.filter(pl.col('in_dd') == 0).height
    print(f'\n[trades] in DD window: {n_in:,}  outside: {n_out:,}')
    print(f'         in-DD TP rate: {df.filter(pl.col("in_dd")==1)["tp"].mean()*100:.1f}%')
    print(f'         outside TP rate: {df.filter(pl.col("in_dd")==0)["tp"].mean()*100:.1f}%')

    # Trade-mix during DD
    print(f'\n  in-DD trade composition:')
    by_side = df.filter(pl.col('in_dd') == 1).group_by(['side']).agg(
        pl.len().alias('n'),
        pl.col('tp').mean().alias('tp_rate')
    ).sort('side')
    for r in by_side.iter_rows(named=True):
        print(f'    {r["side"]}: N={r["n"]:,}  TP={r["tp_rate"]*100:.1f}%')

    # 4) Define knobs and evaluate
    print('\n' + '=' * 76)
    print('Knob discrimination tests')
    print('=' * 76)
    print('\n  Each knob flags trades as "danger" at signal date. Effective knob:')
    print('  - HIGH inside-DD coverage (catches the bad cohort)')
    print('  - HIGH inside-DD TP gap (flagged subset has lower TP)')
    print('  - LOW outside-DD TP gap (no false positives in non-DD periods)')
    print('  - HIGH net selectivity (inside_tp_gap − outside_tp_gap)\n')

    knobs = [
        # CALL knobs — flag CALL signals into overheated sectors / market
        ('K1_call_secETF_RSI_gt70',      "CALL & sec_etf_rsi > 70 (overheated sector)",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_rsi') > 70)),
        ('K2_call_secETF_pct_ema50_gt5', "CALL & sec_etf_pct_ema50 > +5%",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_pct_ema50') > 5)),
        ('K3_call_xsec_brd_gt90',        "CALL & cross_sec_brd > 90 (sectors saturated up)",
         (pl.col('side') == 'CALL') & (pl.col('sec_brd_ema50') > 90)),
        ('K4_call_secAvgRSI_gt62',       "CALL & sec_avg_rsi > 62",
         (pl.col('side') == 'CALL') & (pl.col('sec_avg_rsi') > 62)),
        ('K5_call_xsec_brd_lt30',        "CALL & cross_sec_brd < 30 (broad sector weakness)",
         (pl.col('side') == 'CALL') & (pl.col('sec_brd_ema50') < 30)),
        ('K6_call_secETF_RSI_lt35',      "CALL & sec_etf_rsi < 35 (counter-trend signal sector)",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_rsi') < 35)),

        # PUT knobs — flag PUT signals into oversold / weak sectors
        ('P1_put_secETF_RSI_lt30',       "PUT & sec_etf_rsi < 30 (sector capitulation)",
         (pl.col('side') == 'PUT') & (pl.col('sec_etf_rsi') < 30)),
        ('P2_put_secETF_pct_ema50_ltn5', "PUT & sec_etf_pct_ema50 < -5%",
         (pl.col('side') == 'PUT') & (pl.col('sec_etf_pct_ema50') < -5)),
        ('P3_put_xsec_brd_lt20',         "PUT & cross_sec_brd < 20 (broad capitulation)",
         (pl.col('side') == 'PUT') & (pl.col('sec_brd_ema50') < 20)),
        ('P4_put_xsec_brd_gt75',         "PUT & cross_sec_brd > 75 (broad strength → mean revert good for puts?)",
         (pl.col('side') == 'PUT') & (pl.col('sec_brd_ema50') > 75)),

        # COMBINED — overheated wave: both narrow and broad sectors stretched
        ('C1_call_RSI70_xsec80',         "CALL & sec_etf_rsi>70 & cross_brd>80",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_rsi') > 70) & (pl.col('sec_brd_ema50') > 80)),
        ('C2_put_RSIlt30_xsecLt20',      "PUT & sec_etf_rsi<30 & cross_brd<20 (deep cap)",
         (pl.col('side') == 'PUT') & (pl.col('sec_etf_rsi') < 30) & (pl.col('sec_brd_ema50') < 20)),

        # Divergence — sector says X but production breadth disagrees
        ('D1_call_secWeak_prodOK',       "CALL & sec_etf_pct_ema50<-3 & breadth_score>50",
         (pl.col('side') == 'CALL') & (pl.col('sec_etf_pct_ema50') < -3) & (pl.col('breadth_score') > 50)),
    ]

    results = []
    for name, desc, cond in knobs:
        try:
            r = evaluate_knob(df, name, cond, inside_dd_col='in_dd', label=desc)
            results.append(r)
        except Exception as e:
            print(f'  {name}: ERROR {e}')

    # Sort by net selectivity
    results.sort(key=lambda r: -r['net_selectivity'])

    print(f'\n{"knob":<30s} {"desc":<55s}')
    print(f'{"-"*30} {"-"*55}')
    print(f'{"name":<30s} {"in-DD flag%":>11s} {"in-flag-TP":>11s} {"in-unflag-TP":>13s} '
          f'{"in-gap":>7s} {"out-gap":>8s} {"net":>6s}')
    print(f'{"-"*30} {"-"*11} {"-"*11} {"-"*13} {"-"*7} {"-"*8} {"-"*6}')
    for r in results:
        ifg = r['inside_flagged_tp']
        if ifg is None: ifg_s = '   --'
        else: ifg_s = f'{ifg:>11.1f}'
        print(f'{r["knob"]:<30s} {r["inside_flagged_pct"]:>11.1f} {ifg_s} '
              f'{r["inside_unflagged_tp"]:>13.1f} {r["inside_tp_gap"]:>+7.1f} '
              f'{r["outside_tp_gap"]:>+8.1f} {r["net_selectivity"]:>+6.1f}')
        print(f'  {"":30s} → {r["label"]}')

    # Save
    pl.DataFrame(results).write_csv(LOCAL_CACHE / 'dd_knob_effectiveness.csv')
    print(f'\n[saved] dd_knob_effectiveness.csv')

    # 5) Also output trade-by-trade data during DD for hand inspection
    in_dd_trades = df.filter(pl.col('in_dd') == 1).select([
        'date', 'symbol', 'sector', 'side', 'tier', 'overall',
        'outcome', 'pnl_pct',
        'sec_etf_pct_ema50', 'sec_etf_rsi',
        'sec_brd_ema50', 'sec_brd_ema200', 'sec_avg_rsi', 'breadth_score',
    ]).sort(['date', 'symbol'])
    in_dd_trades.write_csv(LOCAL_CACHE / 'dd_trades_full.csv')
    print(f'[saved] dd_trades_full.csv ({len(in_dd_trades)} trades)')

    print(f'\n[done] {time.time()-t0:.1f}s')


if __name__ == '__main__':
    main()
