"""
Put SL Dynamic Hold — experiments/put_sl_dynamic_hold.py
=========================================================
Tests three variants of the put SL hard-hold mechanism:

  A (baseline)        : fixed hold=3 for all entry days
  B (day-normalized)  : hold=4 on Monday entry, hold=3 otherwise
                        Rationale: Monday+3 bars = Thursday close (no weekend
                        gap); Tue-Fri+3 bars already crosses a weekend.
  C (regime-gated)    : hold=5 when breadth_score ≤ 50 (stressed market),
                        else B-logic (4 on Monday, 3 otherwise).
                        Rationale: stressed tapes have higher shakeout rates;
                        same breadth_score ≤ 50 signal that drives adaptive TP/SL.

During the hold window (bars 1..hold_bars), only TP can trigger;
SL is checked from bar (hold_bars+1) onward, anchored to the ORIGINAL
entry-price SL level (entry × (1 + PUT_SL_SIGMA × vol/100)).

N_ITER = 500, all 6 standard windows × 3 collision modes.
"""

import os, sys, math, random, statistics, bisect
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import monte_carlo as mc

N_ITER = 500
VARIANTS = ['A', 'B', 'C']

# breadth threshold for regime-gated variant C (same as production BREADTH_THRESHOLD)
STRESSED_BREADTH = 50.0


# ---- Hold-bars logic --------------------------------------------------------

def get_hold_bars(signal_date, breadth_dates, breadth_map, variant):
    """Return the number of trading bars to suppress SL for this signal."""
    if variant == 'A':
        return 3
    dow = signal_date.weekday()  # 0=Mon, 4=Fri
    day_hold = 4 if dow == 0 else 3
    if variant == 'B':
        return day_hold
    # Variant C: regime-gated
    b = mc.breadth_on_or_before(breadth_dates, breadth_map, signal_date)
    stressed = b is not None and b <= STRESSED_BREADTH
    return 5 if stressed else day_hold


# ---- Modified put-outcome function ------------------------------------------

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

    tp_sigma = mc.PUT_TP_SIGMA
    sl_sigma = mc.PUT_SL_SIGMA
    net_tp   = mc.PUT_NET_TP
    net_sl   = mc.PUT_NET_SL

    tp_level = entry_price * (1 - tp_sigma * vol / 100)
    sl_level = entry_price * (1 + sl_sigma * vol / 100)

    end_idx = min(len(dates), base_idx + 1 + mc.HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind     = 'hard'
    exit_bar = mc.HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        bar    = i - base_idx      # 1-indexed trading bar
        tp_hit = lows[i] <= tp_level
        sl_hit = (highs[i] >= sl_level) and (bar > sl_hold_bars)  # ← hold gate
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', bar; break
        if tp_hit:
            kind, exit_bar = 'tp',   bar; break
        if sl_hit:
            kind, exit_bar = 'sl',   bar; break

    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl,
                vol=vol, entry=entry_price)


def precompute_put_outcomes_variant(put_sigs, ph, breadth_dates, breadth_map, variant):
    """Pre-compute all put outcomes with per-signal dynamic hold."""
    outcomes = {}
    for sig in put_sigs:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        hold = get_hold_bars(sig.date, breadth_dates, breadth_map, variant)
        r = compute_put_outcome_with_hold(sym_bars, sig.date, hold)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


# ---- Main sweep -------------------------------------------------------------

def run_sweep():
    av = mc.AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm: v{av.id}  ({av.git_commit[:7]})  {(av.git_message or '')[:60]}")
    print(f"N_ITER={N_ITER}  VARIANTS={VARIANTS}")
    print()

    # results[variant][window_label][mode] = list of run_single_sim dicts
    results = {v: {w[0]: {m: [] for m in mc.COLLISION_MODES}
                   for w in mc.WINDOWS}
               for v in VARIANTS}

    # raw TP% per variant per window
    raw_stats = {v: {} for v in VARIANTS}

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

        # Build trading calendar
        ph_dates = set()
        for bars in ph.values():
            for b in bars:
                if wstart <= b[0] <= wend + timedelta(days=20):
                    ph_dates.add(b[0])
        trading_days = sorted(ph_dates)

        # Build by-date signal dicts
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

        # Pre-compute call outcomes once
        call_outcomes = mc.precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map)

        for variant in VARIANTS:
            put_outcomes = precompute_put_outcomes_variant(
                put_sigs, ph, breadth_dates, breadth_map, variant)

            # Raw TP/SL stats
            tp_n = sum(1 for o in put_outcomes.values() if o['kind'] == 'tp')
            sl_n = sum(1 for o in put_outcomes.values() if o['kind'] == 'sl')
            tot  = len(put_outcomes) or 1
            raw_stats[variant][wlabel] = (tp_n / tot * 100, sl_n / tot * 100, len(put_outcomes))

            for mode in mc.COLLISION_MODES:
                for it in range(N_ITER):
                    rng = random.Random(88881 + hash(wlabel) + hash(mode) * 7 +
                                        hash(variant) * 31 + it * 1000)
                    r = mc.run_single_sim(
                        trading_days, calls_by_date, call_outcomes,
                        puts_by_date, put_outcomes,
                        mode, rng,
                        regime_dates, regime_map,
                    )
                    results[variant][wlabel][mode].append(r)

            tp_pct = tp_n / tot * 100
            sl_pct = sl_n / tot * 100
            print(f"  {variant}: raw_TP%={tp_pct:.1f}%  raw_SL%={sl_pct:.1f}%", flush=True)

        print()

    # ── Print results ─────────────────────────────────────────────────────────
    SEP = "=" * 120

    def pct_ret(runs):
        return (statistics.mean(r['final'] for r in runs) / mc.STARTING_CASH - 1) * 100

    def worst_dd(runs):
        return max(r['max_dd'] for r in runs) * 100

    def p_collapse(runs):
        return sum(1 for r in runs if r['final'] <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD) / len(runs) * 100

    def stats(runs, key):
        return statistics.mean(r[key] for r in runs)

    variant_labels = {'A': 'fixed h=3', 'B': 'Mon=4/else=3', 'C': 'breadth≤50→5 else B'}

    print()
    print(SEP)
    print("PUT SL DYNAMIC HOLD SWEEP — Realistic Mode")
    print("  A = fixed hold=3  |  B = Mon=4/else=3  |  C = breadth≤50→5 else B")
    print(SEP)

    for wlabel, _, _ in mc.WINDOWS:
        print(f"\n{'─'*120}")
        print(f"  Window: {wlabel}")
        print(f"  {'Var':>5}  {'Hold Logic':>18}  {'MeanRet':>12}  {'MedianRet':>12}  "
              f"{'WorstDD':>9}  {'P(col)':>7}  {'PutTP%':>8}  {'CallTP%':>8}  "
              f"{'RawTP%':>8}  {'Δ vs A':>12}")
        print(f"  {'─'*5}  {'─'*18}  {'─'*12}  {'─'*12}  {'─'*9}  "
              f"{'─'*7}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*12}")

        base_runs = results['A'][wlabel]['realistic']
        base_mean = pct_ret(base_runs) if base_runs else None

        for v in VARIANTS:
            runs = results[v][wlabel]['realistic']
            if not runs:
                print(f"  {v:>5}  {'[no data]':>18}"); continue
            mean_r  = pct_ret(runs)
            med_r   = (statistics.median(r['final'] for r in runs) / mc.STARTING_CASH - 1) * 100
            wdd     = worst_dd(runs)
            pc      = p_collapse(runs)
            put_tp  = stats(runs, 'put_tp')
            call_tp = stats(runs, 'call_tp')
            raw_tp  = raw_stats[v].get(wlabel, (0, 0, 0))[0]
            if v == 'A':
                delta_str = "—"
            else:
                delta_str = f"{mean_r - base_mean:+.1f}%" if base_mean is not None else "—"
            floor_flag = " ✗" if wdd > 80.0 else "  "
            print(f"  {v:>5}  {variant_labels[v]:>18}  {mean_r:>11.1f}%  {med_r:>11.1f}%  "
                  f"{wdd:>7.1f}%{floor_flag}  {pc:>6.1f}%  {put_tp:>7.1f}%  "
                  f"{call_tp:>7.1f}%  {raw_tp:>7.1f}%  {delta_str:>12}")

    # Conservative DD floor summary
    print()
    print(SEP)
    print("CONSERVATIVE MODE — WorstDD (must stay ≤80%)")
    print(SEP)
    print(f"  {'Var':>5}  {'Hold Logic':>18}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f" {wlabel:>13}", end='')
    print()
    for v in VARIANTS:
        print(f"  {v:>5}  {variant_labels[v]:>18}  ", end='')
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[v][wlabel]['conservative']
            if not runs:
                print(f" {'N/A':>13}", end=''); continue
            wdd = worst_dd(runs)
            mr  = pct_ret(runs)
            flag = "✗" if wdd > 80.0 else " "
            print(f" {mr:>+9.1f}%{flag}  ", end='')
        print()

    # Delta vs Variant A (Realistic)
    print()
    print(SEP)
    print("REALISTIC MEAN RET DELTA vs Variant A  (positive = better than baseline)")
    print(SEP)
    print(f"  {'Var':>5}  {'Hold Logic':>18}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f" {wlabel:>12}", end='')
    print(f"  {'AvgΔ':>8}")
    base_means = {wlabel: pct_ret(results['A'][wlabel]['realistic'])
                  for wlabel, _, _ in mc.WINDOWS
                  if results['A'][wlabel]['realistic']}
    for v in VARIANTS:
        print(f"  {v:>5}  {variant_labels[v]:>18}  ", end='')
        deltas = []
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[v][wlabel]['realistic']
            if not runs:
                print(f" {'N/A':>12}", end=''); continue
            delta = pct_ret(runs) - base_means.get(wlabel, 0)
            deltas.append(delta)
            print(f" {delta:>+11.1f}%", end='')
        avg = statistics.mean(deltas) if deltas else 0
        print(f"  {avg:>+7.1f}%")

    # Put TP% by variant
    print()
    print(SEP)
    print("PUT TP% (portfolio-level, Realistic)  ←  BE=43.5%")
    print(SEP)
    print(f"  {'Var':>5}  {'Hold Logic':>18}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f" {wlabel:>12}", end='')
    print()
    for v in VARIANTS:
        print(f"  {v:>5}  {variant_labels[v]:>18}  ", end='')
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[v][wlabel]['realistic']
            if not runs:
                print(f" {'N/A':>12}", end=''); continue
            ptp  = stats(runs, 'put_tp')
            flag = "✗" if ptp < 43.5 else " "
            print(f" {ptp:>10.1f}%{flag}", end='')
        print()

    # Raw underlying TP% by variant
    print()
    print(SEP)
    print("RAW UNDERLYING TP% (barrier-touch before portfolio simulation)")
    print(SEP)
    print(f"  {'Var':>5}  {'Hold Logic':>18}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f" {wlabel:>12}", end='')
    print()
    for v in VARIANTS:
        print(f"  {v:>5}  {variant_labels[v]:>18}  ", end='')
        for wlabel, _, _ in mc.WINDOWS:
            s = raw_stats[v].get(wlabel)
            if s is None:
                print(f" {'N/A':>12}", end=''); continue
            print(f" {s[0]:>11.1f}%", end='')
        print()

    # Hard-sell %
    print()
    print(SEP)
    print("PUT HARD-SELL% (portfolio-level, Realistic)")
    print(SEP)
    print(f"  {'Var':>5}  {'Hold Logic':>18}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f" {wlabel:>12}", end='')
    print()
    for v in VARIANTS:
        print(f"  {v:>5}  {variant_labels[v]:>18}  ", end='')
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[v][wlabel]['realistic']
            if not runs:
                print(f" {'N/A':>12}", end=''); continue
            hp = stats(runs, 'put_hard')
            print(f" {hp:>10.1f}% ", end='')
        print()

    # Hold distribution summary (Variant B & C only)
    print()
    print(SEP)
    print("HOLD=5 UTILIZATION — Variant C (% of put signals that get hold=5)")
    print(SEP)
    print(f"  {'Window':>8}  {'N puts':>8}  {'hold=3':>8}  {'hold=4':>8}  {'hold=5':>8}  {'% stressed':>12}")
    for wlabel, wstart, wend in mc.WINDOWS:
        call_sigs = mc.load_signals(av, wstart, wend)
        put_sigs  = mc.load_put_signals(av, wstart, wend)
        if not put_sigs:
            continue
        breadth_dates, breadth_map = mc.load_breadth_map(wstart, wend)
        h3 = h4 = h5 = 0
        for sig in put_sigs:
            h = get_hold_bars(sig.date, breadth_dates, breadth_map, 'C')
            if h == 3: h3 += 1
            elif h == 4: h4 += 1
            else: h5 += 1
        tot = len(put_sigs)
        print(f"  {wlabel:>8}  {tot:>8}  {h3/tot*100:>7.1f}%  {h4/tot*100:>7.1f}%  "
              f"{h5/tot*100:>7.1f}%  {h5/tot*100:>11.1f}%")

    print()
    print("Done.")


if __name__ == '__main__':
    run_sweep()
