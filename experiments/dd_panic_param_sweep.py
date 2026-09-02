"""Panic-close parameter sweep.

Previous sweep found PANIC_CLOSE (VIX +25%/3d -> MTM close open calls) is the
only standalone DD-reduction rule that also adds return in some windows.
This sweep tests whether tightening or loosening the VIX threshold/window
improves the tradeoff, particularly on the 22-now continuous compound where
baseline costs -19% return for -4pp DD.

Asymmetric note: only CALLS are panic-closed. Puts benefit from vol spikes
(the underlying is usually selling off) — they should NOT be closed early.
Current impl (run_sim in dd_mitigation_sweep.py) already only closes calls,
and this sweep preserves that.

Grid:
  VIX thresholds: 15%, 20%, 25% (baseline), 30%
  Day windows   : 2, 3 (baseline), 5

Outputs per window x mode: mean return, worst DD, panic_closes/iter.
"""
from __future__ import annotations
import os, sys, random, statistics
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

import monte_carlo as mc
from database.models.core import AlgorithmVersion, MarketRegime

N_ITER = 150

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
]

# Parameter grid — 6 variants + baseline. 25%/3d is the prior best, centered.
PANIC_VARIANTS = [
    ('BASELINE',  None,  None),   # no panic close
    ('v15_3d',    0.15,  3),
    ('v20_3d',    0.20,  3),
    ('v25_3d',    0.25,  3),      # prior
    ('v30_3d',    0.30,  3),
    ('v20_5d',    0.20,  5),
    ('v25_5d',    0.25,  5),
]


def load_spy_vix(d_start, d_end):
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.spy_close, MarketRegime.vix_close)
        .where(MarketRegime.date >= d_start - timedelta(days=60),
               MarketRegime.date <= d_end + timedelta(days=10))
        .tuples()
    )
    return {d: (float(s) if s else None, float(v) if v else None) for d, s, v in rows}


def compute_panic_flags(trading_days, spy_vix, pct, days):
    n = len(trading_days)
    flags = [False] * n
    vix = [spy_vix.get(d, (None, None))[1] for d in trading_days]
    for i in range(n):
        if i >= days and vix[i] is not None:
            vp = vix[i - days]
            if vp and vp > 0 and (vix[i] / vp - 1) >= pct:
                flags[i] = True
    return flags


class PosEx:
    __slots__ = ['sym_id','entry_date','exit_bar','premium_cost','option_pnl',
                 'outcome','side','entry_price','premium_pct']
    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl,
                 outcome, side, entry_price, premium_pct):
        self.sym_id=sym_id; self.entry_date=entry_date; self.exit_bar=exit_bar
        self.premium_cost=premium_cost; self.option_pnl=option_pnl
        self.outcome=outcome; self.side=side
        self.entry_price=entry_price; self.premium_pct=premium_pct


def mtm_close_pnl(pos, today_close):
    if pos.entry_price <= 0 or pos.premium_pct <= 0:
        return mc.NET_SL_BASE
    raw_ret = (today_close - pos.entry_price) / pos.entry_price
    if pos.side == 'put':
        raw_ret = -raw_ret
    pnl = mc.DELTA * raw_ret / pos.premium_pct + mc.SLIP_ENTRY + mc.SLIP_SL
    if pnl < -0.80: pnl = -0.80
    if pnl >  1.50: pnl =  1.50
    return pnl


def run_sim(trading_days, ph_by_sym, calls_by_date, call_outcomes,
            puts_by_date, put_outcomes, mode, rng,
            regime_dates, regime_map, panic_flags):
    cash = mc.STARTING_CASH
    positions = []
    peak_value = mc.STARTING_CASH
    max_dd = 0.0
    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c = sl_c = hard_c = 0
    tp_p = sl_p = hard_p = 0
    panic_closes = 0

    for day_idx, today in enumerate(trading_days):
        # panic-close calls only
        if panic_flags is not None and panic_flags[day_idx]:
            keep = []
            for p in positions:
                if p.side != 'call':
                    keep.append(p); continue
                base = day_to_idx.get(p.entry_date, -999)
                if base + p.exit_bar <= day_idx:
                    keep.append(p); continue
                sym_bars = ph_by_sym.get(p.sym_id)
                if not sym_bars:
                    keep.append(p); continue
                tc = None
                for b in sym_bars:
                    if b[0] == today:
                        tc = b[1]; break
                if tc is None:
                    keep.append(p); continue
                pnl = mtm_close_pnl(p, tc)
                cash += p.premium_cost * (1 + pnl)
                hard_c += 1
                panic_closes += 1
            positions = keep

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
        put_open = sum(1 for p in positions if p.side == 'put')

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

        remaining = mc.MAX_POSITIONS - len(positions)
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
                    open_syms.add(sym_id); put_open += 1

    for p in positions:
        cash += p.premium_cost * (1 + mc.NET_HARD_SELL)
        if p.side == 'call': hard_c += 1
        else: hard_p += 1

    return dict(final=cash, max_dd=max_dd, panic_closes=panic_closes)


def run_variant(label, variant_name, panic_flags, data):
    (trading_days, ph_by_sym, calls_by_date, call_outcomes,
     puts_by_date, put_outcomes, regime_dates, regime_map) = data
    modes = ['conservative', 'realistic', 'optimistic']
    results = {}
    for mode in modes:
        finals = []; dds = []; collapses = 0; pcl = []
        for it in range(N_ITER):
            rng = random.Random(1000 * hash(label) + it)
            r = run_sim(trading_days, ph_by_sym, calls_by_date, call_outcomes,
                        puts_by_date, put_outcomes, mode, rng,
                        regime_dates, regime_map, panic_flags)
            finals.append(r['final']); dds.append(r['max_dd'])
            pcl.append(r['panic_closes'])
            if r['final'] <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD: collapses += 1
        mean_ret = (statistics.mean(finals) / mc.STARTING_CASH - 1) * 100
        results[mode] = dict(
            mean_ret=mean_ret,
            worst_dd=max(dds) * 100,
            mean_dd=statistics.mean(dds) * 100,
            p_coll=collapses / N_ITER * 100,
            mean_panic=statistics.mean(pcl),
        )
    return results


def prep_window(label, d_start, d_end, version):
    call_sigs = mc.load_signals(version, d_start, d_end)
    put_sigs = mc.load_put_signals(version, d_start, d_end)
    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph = mc.load_price_history(sym_ids, d_start, d_end)
    breadth_dates, breadth_map = mc.load_breadth_map(d_start, d_end)
    regime_dates, regime_map = mc.load_regime_map(d_start, d_end)

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20): ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    calls_by_date = defaultdict(list)
    for sig in call_sigs:
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall, (sig.symbol_id, sig.date)))
    puts_by_date = defaultdict(list)
    for sig in put_sigs:
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall, (sig.symbol_id, sig.date)))

    call_outcomes = mc.precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map)
    put_outcomes = mc.precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map)
    spy_vix = load_spy_vix(d_start, d_end)

    return (trading_days, ph, calls_by_date, call_outcomes,
            puts_by_date, put_outcomes, regime_dates, regime_map, spy_vix)


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit}")
    print(f"Iterations per (variant x window x mode): {N_ITER}")
    print(f"Variants: {len(PANIC_VARIANTS)}  Windows: {len(WINDOWS)}")
    print(f"Grid: VIX threshold x days")

    all_results = {}  # {window: {variant: results}}
    day_counts = {}   # {window: {variant: #panic_days}}

    for label, d_start, d_end in WINDOWS:
        print(f"\n{'='*100}\nWINDOW: {label}\n{'='*100}")
        data = prep_window(label, d_start, d_end, version)
        trading_days = data[0]
        spy_vix = data[-1]
        all_results[label] = {}
        day_counts[label] = {}

        core_data = data[:-1]  # drop spy_vix
        for vname, pct, days in PANIC_VARIANTS:
            if pct is None:
                flags = None; nd = 0
            else:
                flags = compute_panic_flags(trading_days, spy_vix, pct, days)
                nd = sum(flags)
            day_counts[label][vname] = nd
            print(f"  {vname:<12}  panic_days={nd:>3}/{len(trading_days)}  running...", flush=True)
            r = run_variant(label, vname, flags, core_data)
            all_results[label][vname] = r
            for mode in ['realistic', 'conservative']:
                rr = r[mode]
                print(f"    {mode:<13}: ret={rr['mean_ret']:>+14,.1f}%  DD={rr['worst_dd']:>5.1f}%  "
                      f"closes={rr['mean_panic']:>5.1f}")

    # Summary tables
    print(f"\n{'='*130}\nREALISTIC MEAN RETURN (Δ% vs BASELINE)\n{'='*130}")
    print(f"{'Window':<10} " + ' '.join(f"{v[0]:>15}" for v in PANIC_VARIANTS))
    for label, _, _ in WINDOWS:
        row = [f"{label:<10}"]
        base = all_results[label]['BASELINE']['realistic']['mean_ret']
        for vname, _, _ in PANIC_VARIANTS:
            r = all_results[label][vname]['realistic']['mean_ret']
            if vname == 'BASELINE':
                row.append(f"{r:>+14,.0f}% ")
            else:
                delta = (r - base) / max(abs(base), 1.0) * 100
                row.append(f"{delta:>+8.0f}%        ")
        print(' '.join(row))

    print(f"\n{'='*130}\nREALISTIC WORST DD (Δpp vs BASELINE)\n{'='*130}")
    print(f"{'Window':<10} " + ' '.join(f"{v[0]:>15}" for v in PANIC_VARIANTS))
    for label, _, _ in WINDOWS:
        row = [f"{label:<10}"]
        base = all_results[label]['BASELINE']['realistic']['worst_dd']
        for vname, _, _ in PANIC_VARIANTS:
            r = all_results[label][vname]['realistic']['worst_dd']
            if vname == 'BASELINE':
                row.append(f"{r:>13.1f}%   ")
            else:
                delta = r - base
                row.append(f"{r:>7.1f}% ({delta:+4.1f})")
        print(' '.join(row))

    print(f"\n{'='*130}\nCONSERVATIVE WORST DD (Δpp vs BASELINE; 80% floor)\n{'='*130}")
    print(f"{'Window':<10} " + ' '.join(f"{v[0]:>15}" for v in PANIC_VARIANTS))
    for label, _, _ in WINDOWS:
        row = [f"{label:<10}"]
        base = all_results[label]['BASELINE']['conservative']['worst_dd']
        for vname, _, _ in PANIC_VARIANTS:
            r = all_results[label][vname]['conservative']['worst_dd']
            mark = 'X' if r >= 80 else ' '
            if vname == 'BASELINE':
                row.append(f"{r:>12.1f}%{mark}   ")
            else:
                delta = r - base
                row.append(f"{r:>6.1f}%{mark}({delta:+4.1f})")
        print(' '.join(row))

    # Panic-days count table
    print(f"\n{'='*130}\nPANIC DAYS FIRED PER WINDOW\n{'='*130}")
    print(f"{'Window':<10} " + ' '.join(f"{v[0]:>15}" for v in PANIC_VARIANTS))
    for label, _, _ in WINDOWS:
        row = [f"{label:<10}"]
        for vname, _, _ in PANIC_VARIANTS:
            nd = day_counts[label][vname]
            row.append(f"{nd:>14} ")
        print(' '.join(row))


if __name__ == '__main__':
    main()
