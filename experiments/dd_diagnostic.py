"""DD Diagnostic — characterize max-drawdown episodes per window.

For each window, runs N_ITER single-sims, finds the iteration with the worst
max-DD, then dumps:
  - Peak date, trough date, DD% for that iteration
  - Day-by-day portfolio value / cash / open-calls / open-puts over the
    DD window (peak-5d .. trough+5d)
  - All exits that occurred during the DD window (date, side, outcome, $P&L)
  - Market context: SPY %change, VIX close, breadth_score, regime_mult
  - Aggregate across all iterations: per-side exit-loss contribution during
    DD windows vs outside them

Usage: python -m experiments.dd_diagnostic          # all windows
       python -m experiments.dd_diagnostic 2022      # one window
"""
from __future__ import annotations
import os, sys, math, random, statistics
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc
from database.models.core import AlgorithmVersion, MarketBreadth, MarketRegime

DIAG_N_ITER = 200     # fewer than canonical; we want worst-case characterization
CONTEXT_DAYS = 5      # days of trail before peak / after trough to print

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
]


def run_single_sim_instrumented(trading_days, calls_by_date, call_outcomes,
                                puts_by_date, put_outcomes, mode, rng,
                                regime_dates, regime_map):
    """Mirrors mc.run_single_sim but records a per-day trail + exit log."""
    cash = mc.STARTING_CASH
    positions = []
    peak_value = mc.STARTING_CASH
    max_dd = 0.0
    peak_date = None
    trough_date = None
    trough_value = mc.STARTING_CASH
    running_peak_date = trading_days[0] if trading_days else None

    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    trail = []          # list of (date, pv, cash, n_call, n_put)
    exits = []          # list of (date, side, outcome, premium_cost, option_pnl, pnl_$)
    entries = []        # list of (date, side, score, premium_cost)

    for day_idx, today in enumerate(trading_days):
        keep = []
        for p in positions:
            base = day_to_idx.get(p.entry_date, -999)
            if base + p.exit_bar <= day_idx:
                payout = p.premium_cost * (1 + p.option_pnl)
                cash += payout
                exits.append((today, p.side, p.outcome, p.premium_cost,
                              p.option_pnl, payout - p.premium_cost))
            else:
                keep.append(p)
        positions = keep

        pv = cash + sum(p.premium_cost for p in positions)
        if pv > peak_value:
            peak_value = pv
            running_peak_date = today
        dd = (peak_value - pv) / peak_value if peak_value > 0 else 0
        if dd > max_dd:
            max_dd = dd
            peak_date = running_peak_date
            trough_date = today
            trough_value = pv

        n_call = sum(1 for p in positions if p.side == 'call')
        n_put = sum(1 for p in positions if p.side == 'put')
        trail.append((today, pv, cash, n_call, n_put))

        if pv <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD:
            break

        open_syms = {p.sym_id for p in positions}
        cap_call = mc.MAX_POSITIONS_CALL if mc.MAX_POSITIONS_CALL is not None else mc.MAX_POSITIONS
        cap_put = mc.MAX_POSITIONS_PUT if mc.MAX_POSITIONS_PUT is not None else mc.MAX_POSITIONS
        side_capped = (mc.MAX_POSITIONS_CALL is not None) or (mc.MAX_POSITIONS_PUT is not None)
        call_open = n_call
        put_open = n_put

        day_calls = calls_by_date.get(today, [])
        if day_calls:
            eligible = [(sid, sc, k) for sid, sc, k in day_calls
                        if k in call_outcomes and sid not in open_syms]
            primary = [e for e in eligible if e[1] >= mc.PRIMARY_THRESHOLD]
            overflow = [e for e in eligible if e[1] < mc.PRIMARY_THRESHOLD]
            primary.sort(key=lambda x: (-x[1], rng.random()))
            overflow.sort(key=lambda x: (-x[1], rng.random()))
            reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
            reg_scale_c = mc.alloc_scale_for(reg_mult, is_put=False)
            for sym_id, score, key in primary + overflow:
                if len(positions) >= mc.MAX_POSITIONS: break
                if side_capped and call_open >= cap_call: break
                alloc_frac = mc.TIER_ALLOC[mc.score_to_tier(score)] * reg_scale_c
                premium_cost = pv * alloc_frac
                if premium_cost > cash or premium_cost <= 0: continue
                o = call_outcomes[key]
                outcome, pnl = mc.resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                cash -= premium_cost
                positions.append(mc.Position(sym_id, today, o['exit_bar'],
                                             premium_cost, pnl, outcome, 'call'))
                open_syms.add(sym_id); call_open += 1
                entries.append((today, 'call', score, premium_cost))

        remaining = mc.MAX_POSITIONS - len(positions)
        if side_capped: remaining = min(remaining, cap_put - put_open)
        if remaining > 0:
            day_puts = puts_by_date.get(today, [])
            if day_puts:
                pe = [(sid, sc, k) for sid, sc, k in day_puts
                      if k in put_outcomes and sid not in open_syms]
                pe.sort(key=lambda x: (x[1], rng.random()))
                reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
                reg_scale_p = mc.alloc_scale_for(reg_mult, is_put=True)
                for sym_id, score, key in pe:
                    if len(positions) >= mc.MAX_POSITIONS: break
                    if side_capped and put_open >= cap_put: break
                    alloc_frac = mc.PUT_TIER_ALLOC[mc.put_score_to_tier(score)] * reg_scale_p
                    premium_cost = pv * alloc_frac
                    if premium_cost > cash or premium_cost <= 0: continue
                    o = put_outcomes[key]
                    outcome, pnl = mc.resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                    cash -= premium_cost
                    positions.append(mc.Position(sym_id, today, o['exit_bar'],
                                                 premium_cost, pnl, outcome, 'put'))
                    open_syms.add(sym_id); put_open += 1
                    entries.append((today, 'put', score, premium_cost))

    for p in positions:
        payout = p.premium_cost * (1 + mc.NET_HARD_SELL)
        cash += payout
        exits.append((trading_days[-1] if trading_days else None, p.side, 'hard',
                      p.premium_cost, mc.NET_HARD_SELL, payout - p.premium_cost))

    final = cash
    return dict(final=final, max_dd=max_dd, peak_date=peak_date,
                trough_date=trough_date, peak_value=peak_value,
                trough_value=trough_value, trail=trail,
                exits=exits, entries=entries)


def load_market_context(d_start, d_end):
    """Return {date: (vix, spy, breadth, regime_mult)} keyed by date."""
    ctx = {}
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.vix_close,
                            MarketRegime.spy_close, MarketRegime.regime_multiplier)
        .where(MarketRegime.date >= d_start - timedelta(days=10),
               MarketRegime.date <= d_end + timedelta(days=10))
        .tuples())
    for d, vix, spy, mult in rows:
        ctx[d] = {'vix': float(vix) if vix else None,
                  'spy': float(spy) if spy else None,
                  'mult': float(mult) if mult else None,
                  'brd': None}
    brows = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
        .where(MarketBreadth.date >= d_start - timedelta(days=10),
               MarketBreadth.date <= d_end + timedelta(days=10))
        .tuples())
    for d, bs in brows:
        if d in ctx: ctx[d]['brd'] = float(bs) if bs else None
        else: ctx[d] = {'vix': None, 'spy': None, 'mult': None,
                        'brd': float(bs) if bs else None}
    return ctx


def diagnose_window(label, d_start, d_end, version):
    print(f"\n{'='*100}\nDIAGNOSTIC: {label}  ({d_start} -> {d_end})\n{'='*100}")

    call_sigs = mc.load_signals(version, d_start, d_end)
    put_sigs = mc.load_put_signals(version, d_start, d_end)
    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph = mc.load_price_history(sym_ids, d_start, d_end)
    breadth_dates, breadth_map = mc.load_breadth_map(d_start, d_end)
    regime_dates, regime_map = mc.load_regime_map(d_start, d_end)

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    calls_by_date = defaultdict(list)
    for sig in call_sigs:
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall, (sig.symbol_id, sig.date)))
    puts_by_date = defaultdict(list)
    for sig in put_sigs:
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall, (sig.symbol_id, sig.date)))

    call_outcomes = mc.precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map)
    put_outcomes = mc.precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map)

    ctx = load_market_context(d_start, d_end)

    print(f"Signals: calls={len(call_sigs)} puts={len(put_sigs)}  outcomes: C={len(call_outcomes)} P={len(put_outcomes)}")
    print(f"Running {DIAG_N_ITER} Realistic iterations to find worst-DD case...")

    best = None  # worst-DD iteration
    dd_values = []
    # aggregate: exit-loss $ by side, inside vs outside DD-window (trough±10d)
    agg_loss_call_in = 0.0; agg_loss_put_in = 0.0
    agg_loss_call_out = 0.0; agg_loss_put_out = 0.0
    agg_win_call_in = 0.0; agg_win_put_in = 0.0

    for it in range(DIAG_N_ITER):
        rng = random.Random(1000 * hash(label) + it)
        r = run_single_sim_instrumented(trading_days, calls_by_date, call_outcomes,
                                        puts_by_date, put_outcomes, 'realistic', rng,
                                        regime_dates, regime_map)
        dd_values.append(r['max_dd'])
        if r['peak_date'] and r['trough_date']:
            dd_start = r['peak_date'] - timedelta(days=10)
            dd_end = r['trough_date'] + timedelta(days=10)
            for ex in r['exits']:
                ex_date, side, outcome, prem, pnl, pnl_dollar = ex
                if ex_date is None: continue
                in_window = dd_start <= ex_date <= dd_end
                bucket_loss = pnl_dollar < 0
                if in_window:
                    if side == 'call':
                        if bucket_loss: agg_loss_call_in += abs(pnl_dollar)
                        else: agg_win_call_in += pnl_dollar
                    else:
                        if bucket_loss: agg_loss_put_in += abs(pnl_dollar)
                        else: agg_win_put_in += pnl_dollar
                else:
                    if bucket_loss:
                        if side == 'call': agg_loss_call_out += abs(pnl_dollar)
                        else: agg_loss_put_out += abs(pnl_dollar)
        if best is None or r['max_dd'] > best['max_dd']:
            best = r

    print(f"\nDD distribution across {DIAG_N_ITER} iters: "
          f"min={min(dd_values)*100:.1f}%  median={statistics.median(dd_values)*100:.1f}%  "
          f"mean={statistics.mean(dd_values)*100:.1f}%  max={max(dd_values)*100:.1f}%")

    # Aggregate loss contribution in DD windows vs outside
    total_loss_in = agg_loss_call_in + agg_loss_put_in
    total_loss_out = agg_loss_call_out + agg_loss_put_out
    if total_loss_in > 0:
        print(f"\nExit-loss $ during DD windows (trough±10d), aggregated across all {DIAG_N_ITER} iters:")
        print(f"  Calls loss: ${agg_loss_call_in:>14,.0f}  ({agg_loss_call_in/total_loss_in*100:.1f}% of DD-window losses)")
        print(f"  Puts loss:  ${agg_loss_put_in:>14,.0f}  ({agg_loss_put_in/total_loss_in*100:.1f}% of DD-window losses)")
        print(f"  Calls wins: ${agg_win_call_in:>14,.0f}  (offsetting gains during DD window)")
        print(f"  Puts wins:  ${agg_win_put_in:>14,.0f}  (offsetting gains during DD window)")
        if total_loss_out > 0:
            print(f"  Call/put loss ratio INSIDE DD window:  {agg_loss_call_in/(agg_loss_put_in+1):.2f}:1")
            print(f"  Call/put loss ratio OUTSIDE DD window: {agg_loss_call_out/(agg_loss_put_out+1):.2f}:1")

    # === WORST-CASE EPISODE DEEP DIVE ===
    print(f"\n--- WORST DD ITERATION ---")
    print(f"Peak: {best['peak_date']}  value=${best['peak_value']:>12,.0f}")
    print(f"Trough: {best['trough_date']}  value=${best['trough_value']:>12,.0f}  DD={best['max_dd']*100:.1f}%")
    print(f"Final: ${best['final']:>12,.0f}  ({(best['final']/mc.STARTING_CASH - 1)*100:+.1f}%)")

    if best['peak_date'] and best['trough_date']:
        dd_start = best['peak_date'] - timedelta(days=CONTEXT_DAYS*2)
        dd_end = best['trough_date'] + timedelta(days=CONTEXT_DAYS*2)

        # Day-by-day portfolio trail over the DD window
        print(f"\nPortfolio trail ({dd_start} to {dd_end}):")
        print(f"{'Date':<12} {'PV':>14} {'Cash':>14} {'C':>3} {'P':>3}  "
              f"{'VIX':>6} {'SPY':>8} {'Brd':>5} {'Reg':>5}  Exits")
        trail_map = {d: (pv, c, nc, np_) for d, pv, c, nc, np_ in best['trail']}
        exits_by_date = defaultdict(list)
        for ex in best['exits']:
            if ex[0]: exits_by_date[ex[0]].append(ex)

        dd_days = [d for d in sorted(trail_map.keys()) if dd_start <= d <= dd_end]
        for d in dd_days:
            pv, c, nc, np_ = trail_map[d]
            mc_ctx = ctx.get(d, {})
            vix = mc_ctx.get('vix'); spy = mc_ctx.get('spy')
            brd = mc_ctx.get('brd'); mult = mc_ctx.get('mult')
            exs = exits_by_date.get(d, [])
            ex_str = ''
            if exs:
                c_tp = sum(1 for e in exs if e[1]=='call' and e[2]=='tp')
                c_sl = sum(1 for e in exs if e[1]=='call' and e[2]=='sl')
                p_tp = sum(1 for e in exs if e[1]=='put' and e[2]=='tp')
                p_sl = sum(1 for e in exs if e[1]=='put' and e[2]=='sl')
                hard = sum(1 for e in exs if e[2]=='hard')
                pnl_sum = sum(e[5] for e in exs)
                ex_str = f"CTP={c_tp} CSL={c_sl} PTP={p_tp} PSL={p_sl} HD={hard}  $={pnl_sum:+,.0f}"
            marker = ''
            if d == best['peak_date']: marker = ' <-PEAK'
            elif d == best['trough_date']: marker = ' <-TROUGH'
            print(f"{str(d):<12} {pv:>14,.0f} {c:>14,.0f} {nc:>3} {np_:>3}  "
                  f"{vix or 0:>6.1f} {spy or 0:>8.1f} {brd or 0:>5.0f} {mult or 0:>5.2f}  {ex_str}{marker}")

        # Exit breakdown during the peak->trough window
        pk, tr = best['peak_date'], best['trough_date']
        window_exits = [e for e in best['exits'] if e[0] and pk <= e[0] <= tr]
        if window_exits:
            print(f"\nExit breakdown during peak->trough ({pk} to {tr}, {len(window_exits)} total exits):")
            by_side = defaultdict(lambda: {'tp': 0, 'sl': 0, 'hard': 0, 'pnl': 0.0})
            for d, side, out, prem, pnl, pnl_d in window_exits:
                by_side[side][out] += 1
                by_side[side]['pnl'] += pnl_d
            for side in ['call', 'put']:
                s = by_side[side]
                total = s['tp'] + s['sl'] + s['hard']
                if total:
                    print(f"  {side:>5}: TP={s['tp']:>3} SL={s['sl']:>3} HD={s['hard']:>3}  "
                          f"total={total:>3}  net P&L=${s['pnl']:>+12,.0f}")

        # SPY peak-to-trough
        spy_peak = ctx.get(best['peak_date'], {}).get('spy')
        spy_trough = ctx.get(best['trough_date'], {}).get('spy')
        if spy_peak and spy_trough:
            print(f"\nSPY during DD window: {spy_peak:.2f} -> {spy_trough:.2f}  ({(spy_trough/spy_peak-1)*100:+.2f}%)")
        vix_peak = ctx.get(best['peak_date'], {}).get('vix')
        vix_trough = ctx.get(best['trough_date'], {}).get('vix')
        if vix_peak and vix_trough:
            print(f"VIX during DD window: {vix_peak:.1f} -> {vix_trough:.1f}")
        brd_peak = ctx.get(best['peak_date'], {}).get('brd')
        brd_trough = ctx.get(best['trough_date'], {}).get('brd')
        if brd_peak and brd_trough:
            print(f"Breadth during DD: {brd_peak:.0f} -> {brd_trough:.0f}")

    return best


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit}")
    for label, d_start, d_end in WINDOWS:
        if target and label != target: continue
        diagnose_window(label, d_start, d_end, version)


if __name__ == '__main__':
    main()
