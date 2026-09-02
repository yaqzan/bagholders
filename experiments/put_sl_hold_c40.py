"""
Put SL Hold — Variant C breadth threshold sweep (experiments/put_sl_hold_c40.py)
=================================================================================
Follow-up to put_sl_dynamic_hold.py.

Variant C (breadth≤50→hold=5) failed the 25% regression gate on 2025 (−28% vs A)
because 23.2% of 2025 signals were firing the gate — over-firing in a choppy tape.

This script tests threshold variants for C:

  B  (shipped)     : Mon=4 / else=3, no regime gate
  C40              : hold=5 when breadth_score ≤ 40 (stricter gate), else B-logic
  C35              : hold=5 when breadth_score ≤ 35
  C30              : hold=5 when breadth_score ≤ 30

Acceptance gate (same as production):
  - No Realistic window regresses > 25% vs Variant B (shipped baseline)
  - All Conservative WorstDD ≤ 80%
  - 5y Realistic ≥ Variant B

N_ITER = 500, all 6 standard windows × 3 collision modes.
"""

import os, sys, math, random, statistics, bisect
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import monte_carlo as mc

N_ITER = 500

THRESHOLD_VARIANTS = {
    'B':   None,   # shipped baseline — no regime gate
    'C40': 40.0,
    'C35': 35.0,
    'C30': 30.0,
}
VARIANT_LABELS = {
    'B':   'Mon=4/else=3 (shipped)',
    'C40': 'breadth≤40→5 else B',
    'C35': 'breadth≤35→5 else B',
    'C30': 'breadth≤30→5 else B',
}


def get_hold_bars(signal_date, breadth_dates, breadth_map, threshold):
    """Return hold bars; threshold=None means pure day-normalized (Variant B)."""
    dow = signal_date.weekday()  # 0=Mon, 4=Fri
    day_hold = 4 if dow == 0 else 3
    if threshold is None:
        return day_hold
    b = mc.breadth_on_or_before(breadth_dates, breadth_map, signal_date)
    stressed = b is not None and b <= threshold
    return 5 if stressed else day_hold


def compute_put_outcome_with_hold(sym_bars, signal_date, sl_hold_bars):
    """Identical to mc.compute_put_outcome but suppresses SL for bars 1..sl_hold_bars."""
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

    tp_level = entry_price * (1 - mc.PUT_TP_SIGMA * vol / 100)
    sl_level = entry_price * (1 + mc.PUT_SL_SIGMA * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + mc.HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind     = 'hard'
    exit_bar = mc.HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        bar    = i - base_idx
        tp_hit = lows[i]  <= tp_level
        sl_hit = (highs[i] >= sl_level) and (bar > sl_hold_bars)
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', bar; break
        if tp_hit:
            kind, exit_bar = 'tp',   bar; break
        if sl_hit:
            kind, exit_bar = 'sl',   bar; break

    return dict(kind=kind, exit_bar=exit_bar,
                net_tp=mc.PUT_NET_TP, net_sl=mc.PUT_NET_SL,
                vol=vol, entry=entry_price)


def precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map, threshold):
    outcomes = {}
    for sig in put_sigs:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        hold = get_hold_bars(sig.date, breadth_dates, breadth_map, threshold)
        r = compute_put_outcome_with_hold(sym_bars, sig.date, hold)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def run_sweep():
    av = mc.AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm: v{av.id}  ({av.git_commit[:7]})  {(av.git_message or '')[:60]}")
    print(f"N_ITER={N_ITER}  Threshold variants: {list(THRESHOLD_VARIANTS.keys())}")
    print()

    vkeys = list(THRESHOLD_VARIANTS.keys())

    results  = {v: {w[0]: {m: [] for m in mc.COLLISION_MODES} for w in mc.WINDOWS} for v in vkeys}
    raw_stats = {v: {} for v in vkeys}
    hold5_pct = {v: {} for v in vkeys}  # fraction of put signals getting hold=5

    for wlabel, wstart, wend in mc.WINDOWS:
        print(f"─── {wlabel} ({wstart} → {wend}) ─────────────────────────", flush=True)

        call_sigs = mc.load_signals(av, wstart, wend)
        put_sigs  = mc.load_put_signals(av, wstart, wend)
        print(f"  calls={len(call_sigs)}  puts={len(put_sigs)}", flush=True)
        if not call_sigs and not put_sigs:
            continue

        sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
        ph      = mc.load_price_history(sym_ids, wstart, wend)

        breadth_dates, breadth_map = mc.load_breadth_map(wstart, wend)
        regime_dates,  regime_map  = mc.load_regime_map(wstart, wend)

        ph_dates = set()
        for bars in ph.values():
            for b in bars:
                if wstart <= b[0] <= wend + timedelta(days=20):
                    ph_dates.add(b[0])
        trading_days = sorted(ph_dates)

        calls_by_date = defaultdict(list)
        for sig in call_sigs:
            ct = mc.ct_tag(sig.overall, sig.trend, 'call')
            calls_by_date[sig.date].append((sig.symbol_id, sig.overall,
                                            (sig.symbol_id, sig.date), ct))
        puts_by_date = defaultdict(list)
        for sig in put_sigs:
            ct = mc.ct_tag(sig.overall, sig.trend, 'put')
            puts_by_date[sig.date].append((sig.symbol_id, sig.overall,
                                           (sig.symbol_id, sig.date), ct))

        call_outcomes = mc.precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map)

        for vkey, threshold in THRESHOLD_VARIANTS.items():
            put_outcomes = precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map, threshold)

            tp_n = sum(1 for o in put_outcomes.values() if o['kind'] == 'tp')
            sl_n = sum(1 for o in put_outcomes.values() if o['kind'] == 'sl')
            tot  = len(put_outcomes) or 1
            raw_stats[vkey][wlabel] = (tp_n / tot * 100, sl_n / tot * 100)

            # hold=5 utilization
            if threshold is not None:
                h5 = sum(1 for sig in put_sigs
                         if get_hold_bars(sig.date, breadth_dates, breadth_map, threshold) == 5)
                hold5_pct[vkey][wlabel] = h5 / len(put_sigs) * 100 if put_sigs else 0.0
            else:
                hold5_pct[vkey][wlabel] = 0.0

            for mode in mc.COLLISION_MODES:
                for it in range(N_ITER):
                    rng = random.Random(88881 + hash(wlabel) + hash(mode) * 7 +
                                        hash(vkey) * 31 + it * 1000)
                    r = mc.run_single_sim(
                        trading_days, calls_by_date, call_outcomes,
                        puts_by_date, put_outcomes,
                        mode, rng,
                        regime_dates, regime_map,
                    )
                    results[vkey][wlabel][mode].append(r)

            tp_pct = tp_n / tot * 100
            h5_pct = hold5_pct[vkey][wlabel]
            print(f"  {vkey:>4}: raw_TP%={tp_pct:.1f}%  hold5%={h5_pct:.1f}%", flush=True)

        print()

    # ── Print results ──────────────────────────────────────────────────────────
    SEP = "=" * 120

    def pct_ret(runs):
        return (statistics.mean(r['final'] for r in runs) / mc.STARTING_CASH - 1) * 100

    def worst_dd(runs):
        return max(r['max_dd'] for r in runs) * 100

    def stats(runs, key):
        return statistics.mean(r[key] for r in runs)

    print()
    print(SEP)
    print("PUT SL HOLD THRESHOLD SWEEP — Realistic Mode (baseline = Variant B shipped)")
    print(SEP)

    for wlabel, _, _ in mc.WINDOWS:
        print(f"\n{'─'*120}")
        print(f"  Window: {wlabel}")
        print(f"  {'Var':>5}  {'Logic':>24}  {'MeanRet':>12}  {'WorstDD':>9}  "
              f"{'PutTP%':>8}  {'RawTP%':>8}  {'Hold5%':>8}  {'Δ vs B':>12}")
        print(f"  {'─'*5}  {'─'*24}  {'─'*12}  {'─'*9}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*12}")

        base_runs = results['B'][wlabel]['realistic']
        base_mean = pct_ret(base_runs) if base_runs else None

        for vkey in vkeys:
            runs = results[vkey][wlabel]['realistic']
            if not runs:
                print(f"  {vkey:>5}  [no data]"); continue
            mean_r  = pct_ret(runs)
            wdd     = worst_dd(runs)
            put_tp  = stats(runs, 'put_tp')
            raw_tp  = raw_stats[vkey].get(wlabel, (0, 0))[0]
            h5p     = hold5_pct[vkey].get(wlabel, 0.0)
            if vkey == 'B':
                delta_str = "—"
            else:
                delta_str = f"{mean_r - base_mean:+.1f}%" if base_mean is not None else "—"
            floor_flag = " ✗" if wdd > 80.0 else "  "
            print(f"  {vkey:>5}  {VARIANT_LABELS[vkey]:>24}  {mean_r:>11.1f}%  "
                  f"{wdd:>7.1f}%{floor_flag}  {put_tp:>7.1f}%  "
                  f"{raw_tp:>7.1f}%  {h5p:>7.1f}%  {delta_str:>12}")

    # Conservative summary
    print()
    print(SEP)
    print("CONSERVATIVE MODE — WorstDD (must stay ≤80%)")
    print(SEP)
    print(f"  {'Var':>5}  {'Logic':>24}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f"  {wlabel:>12}", end='')
    print()
    for vkey in vkeys:
        print(f"  {vkey:>5}  {VARIANT_LABELS[vkey]:>24}  ", end='')
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[vkey][wlabel]['conservative']
            if not runs:
                print(f"  {'N/A':>12}", end=''); continue
            wdd = worst_dd(runs)
            mr  = pct_ret(runs)
            flag = "✗" if wdd > 80.0 else " "
            print(f"  {mr:>+9.1f}%{flag}", end='')
        print()

    # Delta vs B
    print()
    print(SEP)
    print("REALISTIC MEAN RET DELTA vs B (positive = better than shipped)")
    print(SEP)
    print(f"  {'Var':>5}  {'Logic':>24}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f"  {wlabel:>12}", end='')
    print(f"  {'AvgΔ':>9}")

    base_means = {wlabel: pct_ret(results['B'][wlabel]['realistic'])
                  for wlabel, _, _ in mc.WINDOWS
                  if results['B'][wlabel]['realistic']}
    for vkey in vkeys:
        print(f"  {vkey:>5}  {VARIANT_LABELS[vkey]:>24}  ", end='')
        deltas = []
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[vkey][wlabel]['realistic']
            if not runs:
                print(f"  {'N/A':>12}", end=''); continue
            d = pct_ret(runs) - base_means.get(wlabel, 0)
            deltas.append(d)
            print(f"  {d:>+11.1f}%", end='')
        avg = statistics.mean(deltas) if deltas else 0
        print(f"  {avg:>+8.1f}%")

    # Hold=5 utilization table
    print()
    print(SEP)
    print("HOLD=5 UTILIZATION (% of put signals getting extended hold)")
    print(SEP)
    print(f"  {'Var':>5}  {'Logic':>24}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f"  {wlabel:>12}", end='')
    print()
    for vkey in vkeys:
        print(f"  {vkey:>5}  {VARIANT_LABELS[vkey]:>24}  ", end='')
        for wlabel, _, _ in mc.WINDOWS:
            h5p = hold5_pct[vkey].get(wlabel, 0.0)
            print(f"  {h5p:>11.1f}%", end='')
        print()

    print()
    print("Done.")


if __name__ == '__main__':
    run_sweep()
