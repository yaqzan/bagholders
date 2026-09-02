"""DD Gate Sweep — test price-velocity entry gates against baseline.

Tests four variants across the canonical 6 windows x 3 collision modes:

  BASELINE : no gate (current production)
  PUT_GATE : skip new puts when SPY made 10d high on D or D-1 (rally breakout)
  CALL_GATE: skip new calls when VIX rose >25% in last 3 trading days
  BOTH     : both gates active

Key data requirement: SPY/VIX close history (via MarketRegime rows, which
already contain spy_close and vix_close columns).

Metrics per variant per window: MeanRet, WorstDD, P(collapse) for each
collision mode. Aggregation: utility = weighted log return - DD penalty
(same formula as bayes_mc.py for comparability).
"""
from __future__ import annotations
import os, sys, math, random, statistics
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

import monte_carlo as mc
from database.models.core import AlgorithmVersion, MarketRegime

N_ITER = 200   # per (variant x window x mode)

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
]

# --- Gate configuration ------------------------------------------------------
PUT_GATE_LOOKBACK  = 10    # trading days
CALL_GATE_VIX_PCT  = 0.25  # +25% in 3 trading days
CALL_GATE_VIX_DAYS = 3


def load_spy_vix(d_start, d_end):
    """Return per-date dict: {date: (spy_close, vix_close)} covering window + buffer."""
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.spy_close, MarketRegime.vix_close)
        .where(MarketRegime.date >= d_start - timedelta(days=60),
               MarketRegime.date <= d_end + timedelta(days=10))
        .tuples()
    )
    return {d: (float(s) if s else None, float(v) if v else None) for d, s, v in rows}


def compute_gate_flags(trading_days, spy_vix):
    """For each trading day, return (put_gated, call_gated) flags.

    put_gated: True if SPY closed at >=10d high on D or D-1 (rally breakout)
    call_gated: True if VIX rose >25% in last 3 trading days (vol spike)
    """
    n = len(trading_days)
    put_gated = [False] * n
    call_gated = [False] * n
    spy = [spy_vix.get(d, (None, None))[0] for d in trading_days]
    vix = [spy_vix.get(d, (None, None))[1] for d in trading_days]

    for i, d in enumerate(trading_days):
        # PUT gate: SPY at 10d high on D or D-1
        for offset in (0, 1):
            j = i - offset
            if j < PUT_GATE_LOOKBACK: continue
            if spy[j] is None: continue
            window = [spy[k] for k in range(j - PUT_GATE_LOOKBACK, j + 1) if spy[k] is not None]
            if len(window) >= PUT_GATE_LOOKBACK and spy[j] >= max(window):
                put_gated[i] = True; break

        # CALL gate: VIX rose >25% in 3 trading days
        if i >= CALL_GATE_VIX_DAYS and vix[i] is not None:
            v_now = vix[i]
            v_prev = vix[i - CALL_GATE_VIX_DAYS]
            if v_prev and v_prev > 0 and (v_now / v_prev - 1) >= CALL_GATE_VIX_PCT:
                call_gated[i] = True

    return put_gated, call_gated


def run_gated_sim(trading_days, calls_by_date, call_outcomes,
                  puts_by_date, put_outcomes, mode, rng,
                  regime_dates, regime_map, put_gated, call_gated):
    """Copy of mc.run_single_sim with per-day entry gating."""
    cash = mc.STARTING_CASH
    positions = []
    peak_value = mc.STARTING_CASH
    max_dd = 0.0
    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c = sl_c = hard_c = 0
    tp_p = sl_p = hard_p = 0
    cap_call = mc.MAX_POSITIONS
    cap_put  = mc.MAX_POSITIONS

    for day_idx, today in enumerate(trading_days):
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
        call_open = sum(1 for p in positions if p.side == 'call')
        put_open  = sum(1 for p in positions if p.side == 'put')

        # Calls first — gate: skip if call_gated[day_idx]
        if not call_gated[day_idx]:
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
                    alloc_frac = mc.TIER_ALLOC[mc.score_to_tier(score)] * reg_scale_c
                    premium_cost = pv * alloc_frac
                    if premium_cost > cash or premium_cost <= 0: continue
                    o = call_outcomes[key]
                    outcome, pnl = mc.resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                    cash -= premium_cost
                    positions.append(mc.Position(sym_id, today, o['exit_bar'],
                                                 premium_cost, pnl, outcome, 'call'))
                    open_syms.add(sym_id); call_open += 1

        # Puts — gate: skip if put_gated[day_idx]
        if not put_gated[day_idx]:
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
                        positions.append(mc.Position(sym_id, today, o['exit_bar'],
                                                     premium_cost, pnl, outcome, 'put'))
                        open_syms.add(sym_id); put_open += 1

    for p in positions:
        cash += p.premium_cost * (1 + mc.NET_HARD_SELL)
        if p.side == 'call': hard_c += 1
        else: hard_p += 1
    return dict(final=cash, max_dd=max_dd)


def run_variant(label, d_start, d_end, variant_name, put_on, call_on, data):
    trading_days, calls_by_date, call_outcomes, puts_by_date, put_outcomes, \
        regime_dates, regime_map, spy_vix = data

    if put_on or call_on:
        put_gated_all, call_gated_all = compute_gate_flags(trading_days, spy_vix)
        put_gated = put_gated_all if put_on else [False] * len(trading_days)
        call_gated = call_gated_all if call_on else [False] * len(trading_days)
        n_put_gated = sum(put_gated); n_call_gated = sum(call_gated)
    else:
        put_gated = [False] * len(trading_days)
        call_gated = [False] * len(trading_days)
        n_put_gated = n_call_gated = 0

    modes = ['conservative', 'realistic', 'optimistic']
    results = {}
    for mode in modes:
        finals=[]; dds=[]; collapses=0
        for it in range(N_ITER):
            rng = random.Random(1000 * hash(label) + it)
            r = run_gated_sim(trading_days, calls_by_date, call_outcomes,
                              puts_by_date, put_outcomes, mode, rng,
                              regime_dates, regime_map, put_gated, call_gated)
            finals.append(r['final']); dds.append(r['max_dd'])
            if r['final'] <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD: collapses += 1
        mean_ret = (statistics.mean(finals)/mc.STARTING_CASH - 1) * 100
        results[mode] = dict(mean_ret=mean_ret,
                             worst_dd=max(dds)*100,
                             mean_dd=statistics.mean(dds)*100,
                             p_coll=collapses/N_ITER*100)
    return dict(results=results, n_put_gated=n_put_gated, n_call_gated=n_call_gated,
                n_days=len(trading_days))


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

    return trading_days, calls_by_date, call_outcomes, puts_by_date, put_outcomes, \
           regime_dates, regime_map, spy_vix


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit}")
    print(f"Gates: PUT_GATE = SPY at {PUT_GATE_LOOKBACK}d-high on D or D-1")
    print(f"       CALL_GATE = VIX +{CALL_GATE_VIX_PCT*100:.0f}% in {CALL_GATE_VIX_DAYS}d")
    print(f"Iterations per (variant x window x mode): {N_ITER}")

    variants = [
        ('BASELINE',  False, False),
        ('PUT_GATE',  True,  False),
        ('CALL_GATE', False, True),
        ('BOTH',      True,  True),
    ]

    all_results = {}  # {window: {variant: result}}
    for label, d_start, d_end in WINDOWS:
        print(f"\n{'='*100}\nWINDOW: {label}\n{'='*100}")
        data = prep_window(label, d_start, d_end, version)
        all_results[label] = {}
        for vname, put_on, call_on in variants:
            print(f"  Running {vname}...", flush=True)
            r = run_variant(label, d_start, d_end, vname, put_on, call_on, data)
            all_results[label][vname] = r
            gf = f"put_gated={r['n_put_gated']}/{r['n_days']}  call_gated={r['n_call_gated']}/{r['n_days']}"
            print(f"    {gf}")
            for mode in ['realistic', 'conservative']:
                rr = r['results'][mode]
                print(f"    {mode:<13}: ret={rr['mean_ret']:>+14,.1f}%  DD={rr['worst_dd']:>5.1f}%")

    # ---- Final summary --------------------------------------------------------
    print(f"\n{'='*110}")
    print("REALISTIC MEAN RETURN (% vs BASELINE)")
    print('='*110)
    print(f"{'Window':<10} " + ' '.join(f"{v[0]:>20}" for v in variants))
    for label, _, _ in WINDOWS:
        row = [f"{label:<10}"]
        base = all_results[label]['BASELINE']['results']['realistic']['mean_ret']
        for vname, _, _ in variants:
            r = all_results[label][vname]['results']['realistic']['mean_ret']
            if vname == 'BASELINE':
                row.append(f"{r:>+19.1f}%")
            else:
                delta = (r - base) / max(abs(base), 1.0) * 100 if base else 0.0
                row.append(f"{r:>+13,.0f}% ({delta:+.0f}%)")
        print(' '.join(row))

    print(f"\n{'='*110}")
    print("WORST DD - REALISTIC (%)")
    print('='*110)
    print(f"{'Window':<10} " + ' '.join(f"{v[0]:>20}" for v in variants))
    for label, _, _ in WINDOWS:
        row = [f"{label:<10}"]
        base = all_results[label]['BASELINE']['results']['realistic']['worst_dd']
        for vname, _, _ in variants:
            r = all_results[label][vname]['results']['realistic']['worst_dd']
            mark = ' <' if vname != 'BASELINE' and r < base - 1 else ''
            row.append(f"{r:>16.1f}%{mark}")
        print(' '.join(row))

    print(f"\n{'='*110}")
    print("WORST DD - CONSERVATIVE (floor: keep <=80%)")
    print('='*110)
    print(f"{'Window':<10} " + ' '.join(f"{v[0]:>20}" for v in variants))
    for label, _, _ in WINDOWS:
        row = [f"{label:<10}"]
        for vname, _, _ in variants:
            r = all_results[label][vname]['results']['conservative']['worst_dd']
            mark = ' X' if r >= 80.0 else '  '
            row.append(f"{r:>16.1f}%{mark}")
        print(' '.join(row))


if __name__ == '__main__':
    main()
