"""
Phase TP2A — HOLD_DAYS sweep (per-trade, deterministic, asymmetric call/put)

Goal: per-trade EV impact of reducing HOLD_DAYS from 15 toward shorter horizons.
Phase 1 + assessment data shows put TP curve is essentially flat WR7→WR30, while
call TP curve gains modestly. Test asymmetric: HOLD_CALL ∈ {7, 10, 12, 15} ×
HOLD_PUT ∈ {5, 7, 10, 15} grid. Pure deterministic per-signal walk; no MC.

Output: TP / SL / Hard exit rates and per-trade EV per cell. Top-3 candidates
become inputs to Phase 2C Bayesian sweep (TP/SL fine-tune around new HOLD).

Run: python experiments/v27_optimization/phase_tp2a_holddays.py
"""
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

os.environ['MC_NO_MP'] = '1'

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from database.models.core import AlgorithmVersion


# -----------------------------------------------------------------------------
# Per-side outcome computation with overridable HOLD_DAYS

def compute_call_with_hold(sym_bars, signal_date, stressed, hold_call):
    """Replicates compute_trade_outcome but parameterized on hold_call."""
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None
    entry_price = closes[base_idx]
    if entry_price <= 0:
        return None
    vol = mc.realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    tp_sigma = mc.TP_SIGMA_STRESS if stressed else mc.TP_SIGMA_BASE
    sl_sigma = mc.SL_SIGMA_STRESS if stressed else mc.SL_SIGMA_BASE
    net_tp   = mc.NET_TP_STRESS   if stressed else mc.NET_TP_BASE
    net_sl_default = mc.NET_SL_STRESS if stressed else mc.NET_SL_BASE
    base_sl_pct  = mc.SL_STRESS if stressed else mc.SL_BASE
    premium_pct = mc.PREMIUM_MULT * vol / 100
    tp_level    = entry_price * (1 + tp_sigma * vol / 100)
    sl_level    = entry_price * (1 - sl_sigma * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + hold_call)
    if end_idx <= base_idx + 1:
        return None

    kind = 'hard'; exit_bar = hold_call
    realized_sl_pnl = None
    for i in range(base_idx + 1, end_idx):
        tp_hit = highs[i] >= tp_level
        sl_hit = lows[i]  <= sl_level
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', i - base_idx
            prev_close = closes[i-1] if i > base_idx else entry_price
            gap_already = prev_close <= sl_level
            if gap_already:
                close_adv = (closes[i] - entry_price) / entry_price
                realized_sl_pnl = max(mc.DELTA * close_adv / premium_pct, -1.0)
            else:
                realized_sl_pnl = base_sl_pct
            break
        if tp_hit:
            kind, exit_bar = 'tp', i - base_idx; break
        if sl_hit:
            kind, exit_bar = 'sl', i - base_idx
            prev_close = closes[i-1] if i > base_idx else entry_price
            gap_already = prev_close <= sl_level
            if gap_already:
                close_adv = (closes[i] - entry_price) / entry_price
                realized_sl_pnl = max(mc.DELTA * close_adv / premium_pct, -1.0)
            else:
                realized_sl_pnl = base_sl_pct
            break

    net_sl = (realized_sl_pnl + mc.SLIP_ENTRY + mc.SLIP_SL) if realized_sl_pnl is not None else net_sl_default
    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl)


def compute_put_with_hold(sym_bars, signal_date, hold_put):
    """Replicates compute_put_outcome with overridable hold_put. Uses
    PUT_SL_HOLD_BARS_DEFAULT/MONDAY at entry (currently both 0 in production).
    """
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None
    entry_price = closes[base_idx]
    if entry_price <= 0:
        return None
    vol = mc.realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    tp_pct = mc.PUT_TP
    sl_pct = mc.PUT_SL
    tp_sigma = tp_pct * mc.PREMIUM_MULT / mc.DELTA
    net_tp   = tp_pct + mc.SLIP_ENTRY + mc.SLIP_TP
    tp_level = entry_price * (1 - tp_sigma * vol / 100)
    sl_sigma = abs(sl_pct) * mc.PREMIUM_MULT / mc.DELTA
    sl_level = entry_price * (1 + sl_sigma * vol / 100)
    premium_pct = mc.PREMIUM_MULT * vol / 100

    hold_bars_sl_off = mc.PUT_SL_HOLD_BARS_MONDAY if signal_date.weekday() == 0 else mc.PUT_SL_HOLD_BARS_DEFAULT

    end_idx = min(len(dates), base_idx + 1 + hold_put)
    if end_idx <= base_idx + 1:
        return None

    kind = 'hard'; exit_bar = hold_put
    realized_sl_pnl = None
    for i in range(base_idx + 1, end_idx):
        bar = i - base_idx
        sl_active = bar > hold_bars_sl_off
        tp_hit = lows[i] <= tp_level
        sl_hit = sl_active and (highs[i] >= sl_level)
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', bar
            prev_close = closes[i-1] if i > base_idx else entry_price
            gap_already = prev_close >= sl_level
            if gap_already:
                close_adv = (closes[i] - entry_price) / entry_price
                realized_sl_pnl = max(-mc.DELTA * close_adv / premium_pct, -1.0)
            else:
                realized_sl_pnl = sl_pct
            break
        if tp_hit:
            kind, exit_bar = 'tp', bar; break
        if sl_hit:
            kind, exit_bar = 'sl', bar
            prev_close = closes[i-1] if i > base_idx else entry_price
            gap_already = prev_close >= sl_level
            if gap_already:
                close_adv = (closes[i] - entry_price) / entry_price
                realized_sl_pnl = max(-mc.DELTA * close_adv / premium_pct, -1.0)
            else:
                realized_sl_pnl = sl_pct
            break

    net_sl = (realized_sl_pnl + mc.SLIP_ENTRY + mc.SLIP_SL) if realized_sl_pnl is not None else (sl_pct + mc.SLIP_ENTRY + mc.SLIP_SL)
    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl)


# -----------------------------------------------------------------------------

def aggregate_outcomes(outcomes_iter, mode='realistic'):
    """outcomes_iter yields dicts with kind / net_tp / net_sl / exit_bar.
    Returns (n, tp_rate%, sl_rate%, hard_rate%, ev_per_trade) under collision mode.
    """
    n = 0; tp = sl = hard = 0; both = 0
    ev_sum = 0.0
    for o in outcomes_iter:
        n += 1
        k = o['kind']
        if k == 'tp':
            tp += 1
            ev_sum += o['net_tp']
        elif k == 'sl':
            sl += 1
            ev_sum += o['net_sl']
        elif k == 'hard':
            hard += 1
            ev_sum += mc.NET_HARD_SELL
        elif k == 'both':
            both += 1
            if mode == 'conservative':
                sl += 1
                ev_sum += o['net_sl']
            elif mode == 'optimistic':
                tp += 1
                ev_sum += o['net_tp']
            else:  # realistic 50/50
                tp += 0.5; sl += 0.5
                ev_sum += 0.5 * (o['net_tp'] + o['net_sl'])
    if n == 0:
        return n, 0, 0, 0, 0.0
    return n, tp/n*100, sl/n*100, hard/n*100, ev_sum / n * 100  # EV in % of premium


def run_sweep(d_start, d_end):
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Using algorithm version: id={version.id}\n")

    call_sigs = mc.load_signals(version, d_start, d_end)
    put_sigs  = mc.load_put_signals(version, d_start, d_end)
    print(f"Loaded {len(call_sigs)} call signals, {len(put_sigs)} put signals")

    sym_ids = list(set(s.symbol_id for s in call_sigs) | set(s.symbol_id for s in put_sigs))
    ph = mc.load_price_history(sym_ids, d_start, d_end)
    breadth_dates, breadth_map = mc.load_breadth_map(d_start, d_end)
    print(f"Loaded price history for {len(ph)} symbols\n")

    # Pre-tag stressed flag per call signal (deterministic)
    call_stressed = {}
    for s in call_sigs:
        call_stressed[(s.symbol_id, s.date)] = mc.is_stressed(breadth_dates, breadth_map, s.date)

    # Per-year aggregates
    def split_by_year(rows):
        by_year = defaultdict(list)
        for sig, out in rows:
            by_year[sig.date.year].append(out)
        return by_year

    print("="*100)
    print("CALL HOLD_DAYS SWEEP (TP=+30%/+35% breadth-adapt, SL=-30%/-35%, Hard=-40%)")
    print("="*100)
    print(f"{'Hold':>5} {'Year':<6} | {'N':>5} {'TP%':>6} {'SL%':>6} {'Hard%':>6} | {'EV%':>7} {'BE':>6}")
    print("-" * 80)
    for hold_call in [5, 7, 10, 12, 15]:
        rows = []
        for s in call_sigs:
            sym_bars = ph.get(s.symbol_id)
            if not sym_bars:
                continue
            stressed = call_stressed[(s.symbol_id, s.date)]
            o = compute_call_with_hold(sym_bars, s.date, stressed, hold_call)
            if o is not None:
                rows.append((s, o))
        by_year = split_by_year(rows)
        # 5y aggregate
        outs_all = [o for _, o in rows]
        n, tp, sl, hard, ev = aggregate_outcomes(outs_all)
        print(f"{hold_call:>5d} {'5y':<6} | {n:>5} {tp:>5.1f}% {sl:>5.1f}% {hard:>5.1f}% | {ev:>+6.2f}% {56.3:>5.1f}%")
        for yr in sorted(by_year.keys()):
            n, tp, sl, hard, ev = aggregate_outcomes(by_year[yr])
            margin = tp - 56.3
            flag = ' THIN' if margin < 3 else ('  win' if margin > 6 else '')
            print(f"{hold_call:>5d} {yr:<6} | {n:>5} {tp:>5.1f}% {sl:>5.1f}% {hard:>5.1f}% | {ev:>+6.2f}% {flag}")
        print()

    print("="*100)
    print("PUT HOLD_DAYS SWEEP (TP=+35%, SL=-20%, Hard=-40%)")
    print("="*100)
    print(f"{'Hold':>5} {'Year':<6} | {'N':>5} {'TP%':>6} {'SL%':>6} {'Hard%':>6} | {'EV%':>7} {'BE':>6}")
    print("-" * 80)
    for hold_put in [5, 7, 10, 12, 15]:
        rows = []
        for s in put_sigs:
            sym_bars = ph.get(s.symbol_id)
            if not sym_bars:
                continue
            o = compute_put_with_hold(sym_bars, s.date, hold_put)
            if o is not None:
                rows.append((s, o))
        by_year = split_by_year(rows)
        outs_all = [o for _, o in rows]
        n, tp, sl, hard, ev = aggregate_outcomes(outs_all)
        print(f"{hold_put:>5d} {'5y':<6} | {n:>5} {tp:>5.1f}% {sl:>5.1f}% {hard:>5.1f}% | {ev:>+6.2f}% {39.6:>5.1f}%")
        for yr in sorted(by_year.keys()):
            n, tp, sl, hard, ev = aggregate_outcomes(by_year[yr])
            margin = tp - 39.6
            flag = ' THIN' if margin < 3 else ('  win' if margin > 6 else '')
            print(f"{hold_put:>5d} {yr:<6} | {n:>5} {tp:>5.1f}% {sl:>5.1f}% {hard:>5.1f}% | {ev:>+6.2f}% {flag}")
        print()


def main():
    run_sweep(date(2021, 1, 1), date(2026, 4, 28))


if __name__ == '__main__':
    main()
