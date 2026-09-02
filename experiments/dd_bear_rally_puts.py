"""Bear-rally put hold-through test.

Hypothesis: put DDs cluster on multi-day counter-trend rallies within bears.
During these rallies, puts get stopped out en-masse even though the thesis
(bearish breakdown) is still correct.

Signal: bear_rally = (SPY 5d return > +3%) AND (breadth_score <= 50)
  - SPY 5d return captures the rally magnitude
  - breadth_score <= 50 confirms the rally is on weak internals (bear rally,
    not a genuine regime change)

Variants tested:
  BASELINE  : current production (PUT_SL = -20%, no bear-rally logic)
  SUSPEND   : on bear-rally days, skip SL check entirely (SL becomes hard-sell
              if triggered; TP still fires normally). Puts "hold through" rallies.
  WIDEN30   : on bear-rally days, SL widens to -30% (1.092σ up move required
              instead of 0.728σ). Soft hold-through.
  WIDEN40   : on bear-rally days, SL widens to -40% (1.456σ up). Harder hold-through.
  BLOCK_ENT : baseline SL, but BLOCK new put entries on bear-rally days (cheap
              comparison — tests whether entry gating helps without exit changes).

Call side is unchanged. This test isolates put-side mitigation only.
"""
from __future__ import annotations
import os, sys, random, statistics
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

import monte_carlo as mc
from experiments.dd_panic_param_sweep import PosEx, prep_window
from database.models.core import AlgorithmVersion, MarketRegime

N_ITER = 300

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
]

# Bear-rally detection
SPY_RALLY_PCT   = 0.03     # +3% over 5 sessions
SPY_RALLY_DAYS  = 5
BREADTH_CUTOFF  = 50.0     # weak internals (same threshold as call TP/SL adapt)


def load_spy_series(d_start, d_end):
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.spy_close)
        .where(MarketRegime.date >= d_start - timedelta(days=30),
               MarketRegime.date <= d_end + timedelta(days=10),
               MarketRegime.spy_close.is_null(False))
        .order_by(MarketRegime.date)
        .tuples()
    )
    return {d: float(sp) for d, sp in rows}


def compute_bear_rally_flags(trading_days, spy_series, breadth_dates, breadth_map):
    """Returns list[bool] aligned with trading_days. True = bear rally day."""
    n = len(trading_days)
    flags = [False] * n
    spy = [spy_series.get(d) for d in trading_days]
    for i in range(n):
        if i < SPY_RALLY_DAYS or spy[i] is None:
            continue
        spy_prev = spy[i - SPY_RALLY_DAYS]
        if not spy_prev:
            continue
        ret_5d = spy[i] / spy_prev - 1
        if ret_5d < SPY_RALLY_PCT:
            continue
        brd = mc.breadth_on_or_before(breadth_dates, breadth_map, trading_days[i])
        if brd is None:
            continue
        if brd <= BREADTH_CUTOFF:
            flags[i] = True
    return flags


def compute_put_outcome_dynamic(sym_bars, signal_date, trading_days,
                                 br_flags, mode='baseline', widen_sl=None):
    """Walk each day; SL check conditional on bear_rally flag.
      mode = 'baseline'  -> normal SL always active
      mode = 'suspend'   -> SL disabled on bear-rally days (TP still checked)
      mode = 'widen'     -> SL widened to `widen_sl` (negative decimal) on bear-rally days
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
    net_tp = tp_pct + mc.SLIP_ENTRY + mc.SLIP_TP
    net_sl = sl_pct + mc.SLIP_ENTRY + mc.SLIP_SL
    tp_sigma = tp_pct * mc.PREMIUM_MULT / mc.DELTA
    sl_sigma = abs(sl_pct) * mc.PREMIUM_MULT / mc.DELTA
    tp_level = entry_price * (1 - tp_sigma * vol / 100)
    sl_level_base = entry_price * (1 + sl_sigma * vol / 100)

    sl_level_widened = None
    if mode == 'widen' and widen_sl is not None:
        w_sigma = abs(widen_sl) * mc.PREMIUM_MULT / mc.DELTA
        sl_level_widened = entry_price * (1 + w_sigma * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + mc.HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    td_index = {d: i for i, d in enumerate(trading_days)}

    kind = 'hard'; exit_bar = mc.HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        today = dates[i]
        is_rally = False
        ti = td_index.get(today)
        if ti is not None and ti < len(br_flags):
            is_rally = br_flags[ti]

        if mode == 'suspend' and is_rally:
            sl_active = False
        else:
            sl_active = True

        if mode == 'widen' and is_rally and sl_level_widened is not None:
            sl_eff = sl_level_widened
        else:
            sl_eff = sl_level_base

        tp_hit = lows[i]  <= tp_level
        sl_hit = sl_active and (highs[i] >= sl_eff)
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', i - base_idx; break
        if tp_hit:
            kind, exit_bar = 'tp',   i - base_idx; break
        if sl_hit:
            kind, exit_bar = 'sl',   i - base_idx; break

    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl,
                vol=vol, entry=entry_price)


def precompute_put_outcomes_variant(signals, ph, trading_days, br_flags, mode, widen_sl=None):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        r = compute_put_outcome_dynamic(sym_bars, sig.date, trading_days,
                                         br_flags, mode=mode, widen_sl=widen_sl)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def run_sim(trading_days, ph_by_sym, calls_by_date, call_outcomes,
            puts_by_date, put_outcomes, mode, rng,
            regime_dates, regime_map, br_flags=None, block_put_entry=False):
    cash = mc.STARTING_CASH
    positions = []
    peak_value = mc.STARTING_CASH
    max_dd = 0.0
    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c = sl_c = hard_c = 0
    tp_p = sl_p = hard_p = 0

    for day_idx, today in enumerate(trading_days):
        # natural exits
        keep = []
        for p in positions:
            base = day_to_idx.get(p.entry_date, -999)
            if base + p.exit_bar <= day_idx:
                cash += p.premium_cost * (1 + p.option_pnl)
                if p.side == 'call':
                    if p.outcome == 'tp': tp_c += 1
                    elif p.outcome == 'sl': sl_c += 1
                    else: hard_c += 1
                else:
                    if p.outcome == 'tp': tp_p += 1
                    elif p.outcome == 'sl': sl_p += 1
                    else: hard_p += 1
            else:
                keep.append(p)
        positions = keep

        pv = cash + sum(p.premium_cost for p in positions)
        if pv > peak_value: peak_value = pv
        dd = (peak_value - pv) / peak_value if peak_value > 0 else 0
        if dd > max_dd: max_dd = dd
        if pv <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD: break

        open_syms = {p.sym_id for p in positions}

        day_calls = calls_by_date.get(today, [])
        if day_calls:
            eligible = [(sid, sc, k) for sid, sc, k in day_calls
                        if k in call_outcomes and sid not in open_syms]
            primary  = [e for e in eligible if e[1] >= mc.PRIMARY_THRESHOLD]
            overflow = [e for e in eligible if e[1] <  mc.PRIMARY_THRESHOLD]
            primary.sort(key=lambda x: (-x[1], rng.random()))
            overflow.sort(key=lambda x: (-x[1], rng.random()))
            reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
            reg_scale_c = mc.alloc_scale_for(reg_mult, is_put=False)
            for sym_id, score, key in primary + overflow:
                if len(positions) >= mc.MAX_POSITIONS: break
                alloc_frac = mc.TIER_ALLOC[mc.score_to_tier(score)] * reg_scale_c
                premium_cost = pv * alloc_frac
                if premium_cost > cash or premium_cost <= 0: continue
                o = call_outcomes[key]
                outcome, pnl = mc.resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                cash -= premium_cost
                positions.append(PosEx(sym_id, today, o['exit_bar'],
                                       premium_cost, pnl, outcome, 'call',
                                       o['entry'], o['premium_pct']))
                open_syms.add(sym_id)

        # Put entries (optionally blocked on bear-rally days)
        block_today = block_put_entry and br_flags is not None and br_flags[day_idx]
        if not block_today and len(positions) < mc.MAX_POSITIONS:
            day_puts = puts_by_date.get(today, [])
            if day_puts:
                pe = [(sid, sc, k) for sid, sc, k in day_puts
                      if k in put_outcomes and sid not in open_syms]
                pe.sort(key=lambda x: (x[1], rng.random()))
                reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
                reg_scale_p = mc.alloc_scale_for(reg_mult, is_put=True)
                for sym_id, score, key in pe:
                    if len(positions) >= mc.MAX_POSITIONS: break
                    alloc_frac = mc.PUT_TIER_ALLOC[mc.put_score_to_tier(score)] * reg_scale_p
                    premium_cost = pv * alloc_frac
                    if premium_cost > cash or premium_cost <= 0: continue
                    o = put_outcomes[key]
                    outcome, pnl = mc.resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                    cash -= premium_cost
                    ppct = mc.PREMIUM_MULT * o['vol'] / 100
                    positions.append(PosEx(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'put',
                                           o['entry'], ppct))
                    open_syms.add(sym_id)

    for p in positions:
        cash += p.premium_cost * (1 + mc.NET_HARD_SELL)

    return dict(final=cash, max_dd=max_dd,
                put_tp=tp_p, put_sl=sl_p, put_hard=hard_p)


def run_variant(vname, put_outcomes, block_put_entry, br_flags, data):
    (trading_days, ph_by_sym, calls_by_date, call_outcomes,
     puts_by_date, _, regime_dates, regime_map) = data
    results = {}
    for mode in ['realistic', 'conservative']:
        finals = []; dds = []; tps = []; sls = []
        for it in range(N_ITER):
            rng = random.Random(1000 * hash(vname) + it)
            r = run_sim(trading_days, ph_by_sym, calls_by_date, call_outcomes,
                        puts_by_date, put_outcomes, mode, rng,
                        regime_dates, regime_map,
                        br_flags=br_flags, block_put_entry=block_put_entry)
            finals.append(r['final']); dds.append(r['max_dd'])
            tps.append(r['put_tp']); sls.append(r['put_sl'])
        mean_ret = (statistics.mean(finals) / mc.STARTING_CASH - 1) * 100
        put_tp_total = sum(tps); put_sl_total = sum(sls)
        put_tp_rate = put_tp_total / max(put_tp_total + put_sl_total, 1) * 100
        results[mode] = dict(
            mean_ret=mean_ret,
            worst_dd=max(dds) * 100,
            put_tp_rate=put_tp_rate,
            mean_put_tp=statistics.mean(tps),
            mean_put_sl=statistics.mean(sls),
        )
    return results


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit}")
    print(f"N_ITER = {N_ITER}")
    print(f"Bear-rally signal: SPY 5d ret > +{SPY_RALLY_PCT:.0%} AND breadth_score <= {BREADTH_CUTOFF:.0f}")

    all_results = {}
    rally_counts = {}

    for label, d_start, d_end in WINDOWS:
        print(f"\n{'='*100}\nWINDOW: {label}\n{'='*100}")
        data = prep_window(label, d_start, d_end, version)
        trading_days = data[0]
        core = data[:-1]

        # bear rally flags
        spy = load_spy_series(d_start, d_end)
        breadth_dates, breadth_map = mc.load_breadth_map(d_start, d_end)
        br_flags = compute_bear_rally_flags(trading_days, spy, breadth_dates, breadth_map)
        n_rally = sum(br_flags)
        rally_counts[label] = n_rally
        print(f"Bear rally days: {n_rally}/{len(trading_days)}")

        put_sigs = mc.load_put_signals(version, d_start, d_end)

        # Precompute put outcomes for each variant
        po_baseline = mc.precompute_put_outcomes(put_sigs, data[1], breadth_dates, breadth_map)
        po_suspend  = precompute_put_outcomes_variant(put_sigs, data[1], trading_days, br_flags, 'suspend')
        po_widen30  = precompute_put_outcomes_variant(put_sigs, data[1], trading_days, br_flags, 'widen', widen_sl=-0.30)
        po_widen40  = precompute_put_outcomes_variant(put_sigs, data[1], trading_days, br_flags, 'widen', widen_sl=-0.40)

        variants = [
            ('BASELINE',  po_baseline, False),
            ('SUSPEND',   po_suspend,  False),
            ('WIDEN30',   po_widen30,  False),
            ('WIDEN40',   po_widen40,  False),
            ('BLOCK_ENT', po_baseline, True),
        ]

        all_results[label] = {}
        for vname, po, block in variants:
            print(f"  {vname:<10}  puts_loaded={len(po)}  running...", flush=True)
            r = run_variant(vname, po, block, br_flags, core)
            all_results[label][vname] = r
            for mode in ['realistic', 'conservative']:
                rr = r[mode]
                print(f"    {mode:<13}: ret={rr['mean_ret']:>+14,.1f}%  DD={rr['worst_dd']:>5.1f}%  "
                      f"PutTP%={rr['put_tp_rate']:>5.1f}  "
                      f"(tp={rr['mean_put_tp']:>5.1f} sl={rr['mean_put_sl']:>5.1f})")

    # Summary
    variants_labels = ['BASELINE', 'SUSPEND', 'WIDEN30', 'WIDEN40', 'BLOCK_ENT']
    for mode in ['realistic', 'conservative']:
        print(f"\n{'='*135}\n{mode.upper()} SUMMARY — Δ Return vs BASELINE, absolute DD, Put TP%\n{'='*135}")
        header = f"{'Window':<10} {'RallyN':>7}"
        for v in variants_labels:
            header += f" | {v:>17}"
        print(header)
        print('-' * len(header))
        for label, _, _ in WINDOWS:
            base = all_results[label]['BASELINE'][mode]['mean_ret']
            row_ret = f"{label:<10} {rally_counts[label]:>7}"
            row_dd  = f"{'':<10} {'':<7}"
            row_tp  = f"{'':<10} {'':<7}"
            for v in variants_labels:
                r = all_results[label][v][mode]
                if v == 'BASELINE':
                    row_ret += f" | {r['mean_ret']:>+15,.0f}%  "
                    row_dd  += f" | DD={r['worst_dd']:>12.1f}%"
                    row_tp  += f" | PTP={r['put_tp_rate']:>12.1f}%"
                else:
                    delta = (r['mean_ret'] - base) / max(abs(base), 1.0) * 100
                    row_ret += f" | {delta:>+15.1f}%  "
                    row_dd  += f" | DD={r['worst_dd']:>12.1f}%"
                    row_tp  += f" | PTP={r['put_tp_rate']:>12.1f}%"
            print(row_ret); print(row_dd); print(row_tp)

    # Focus table
    print(f"\n{'='*100}\n22-NOW FOCUS\n{'='*100}")
    print(f"{'Variant':<12} {'Real Δret':>12} {'Real DDΔ':>10} {'PutTP%':>8} | {'Cons Δret':>12} {'Cons DDΔ':>10}")
    base_real = all_results['22-now']['BASELINE']['realistic']
    base_cons = all_results['22-now']['BASELINE']['conservative']
    for v in variants_labels:
        r_real = all_results['22-now'][v]['realistic']
        r_cons = all_results['22-now'][v]['conservative']
        if v == 'BASELINE':
            print(f"{v:<12} {'—':>12} {'—':>10} {r_real['put_tp_rate']:>7.1f}% | "
                  f"{'—':>12} {'—':>10}")
        else:
            d_real_ret = (r_real['mean_ret'] - base_real['mean_ret']) / max(abs(base_real['mean_ret']), 1.0) * 100
            d_real_dd  = r_real['worst_dd'] - base_real['worst_dd']
            d_cons_ret = (r_cons['mean_ret'] - base_cons['mean_ret']) / max(abs(base_cons['mean_ret']), 1.0) * 100
            d_cons_dd  = r_cons['worst_dd'] - base_cons['worst_dd']
            print(f"{v:<12} {d_real_ret:>+11.1f}% {d_real_dd:>+9.1f}pp {r_real['put_tp_rate']:>7.1f}% | "
                  f"{d_cons_ret:>+11.1f}% {d_cons_dd:>+9.1f}pp")


if __name__ == '__main__':
    main()
