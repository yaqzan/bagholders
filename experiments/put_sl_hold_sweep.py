"""
Put SL Hard-Hold Sweep — experiments/put_sl_hold_sweep.py
==========================================================
Tests delaying the put stop-loss check for the first N trading bars.
During the hold window (bars 1..hold_bars), only TP can trigger;
SL is checked from bar (hold_bars + 1) onward, still anchored to the
ORIGINAL entry-price SL level (entry × (1 + PUT_SL_SIGMA × vol/100)).

Motivation
----------
put_shakeout_profile.py shows:
  MILD shakeouts (0.73-1.0σ): 88-95% P(recovery) → EV +21-26% if held vs +5% recycle
  MOD  shakeouts (1.0-1.5σ):  77-87% P(recovery) → EV +11-19% if held
  EXTREME (>1.5σ):             27-31% P(recovery) → EV -25-29% (recycling wins)

However, 65-74% of all SL triggers are EXTREME. Hard-hold for N bars
converts those early EXTREME exits from −20% to −50% (hard sell), which
hurts capital velocity. The question is whether the MILD/MOD recovery
savings offset the EXTREME hold cost at portfolio scale.

Sweep: PUT_SL_HOLD_BARS ∈ {0, 1, 2, 3, 5, 7}
  0 = production baseline (no hold, current -20% SL active from bar 1)
  N = suppress SL for first N bars; TP still fires in all bars

All other parameters locked at production defaults (v21, aba4f5d).

Strategy:
  Approach: import production run_single_sim() verbatim; only override
  precompute_put_outcomes() to inject sl_hold_bars into the put walk.
"""

import os, sys, math, random, statistics, bisect
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

# Import production constants + simulation engine from monte_carlo
import monte_carlo as mc

HOLD_BARS_SWEEP = [3, 4, 5]
N_ITER_SWEEP    = 500


# ---- Modified put-outcome function ------------------------------------------

def compute_put_outcome_with_hold(sym_bars, signal_date, sl_hold_bars,
                                   put_stressed=False):
    """Identical to mc.compute_put_outcome but suppresses SL for bars 1..sl_hold_bars.

    TP is active in all bars throughout.  SL uses the original entry-price
    threshold anchored at signal_date close — holding doesn't shift the level.
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

    # Put TP/SL levels (no breadth switching; production PUT_BREADTH_MODE='none')
    tp_sigma = mc.PUT_TP_SIGMA
    sl_sigma = mc.PUT_SL_SIGMA
    net_tp   = mc.PUT_NET_TP
    net_sl   = mc.PUT_NET_SL

    tp_level = entry_price * (1 - tp_sigma * vol / 100)   # price must FALL below here
    sl_level = entry_price * (1 + sl_sigma * vol / 100)   # price must RISE above here

    end_idx = min(len(dates), base_idx + 1 + mc.HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind     = 'hard'
    exit_bar = mc.HOLD_DAYS
    for i in range(base_idx + 1, end_idx):
        bar    = i - base_idx          # 1-indexed trading bar
        tp_hit = lows[i]  <= tp_level
        sl_hit = (highs[i] >= sl_level) and (bar > sl_hold_bars)   # ← hold gate
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', bar; break
        if tp_hit:
            kind, exit_bar = 'tp',   bar; break
        if sl_hit:
            kind, exit_bar = 'sl',   bar; break

    return dict(kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl,
                vol=vol, entry=entry_price)


def precompute_put_outcomes_with_hold(put_sigs, ph, sl_hold_bars):
    """Pre-compute all put outcomes with the given hold delay."""
    outcomes = {}
    for sig in put_sigs:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        r = compute_put_outcome_with_hold(sym_bars, sig.date, sl_hold_bars)
        if r is not None:
            outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


# ---- Main sweep --------------------------------------------------------------

def run_sweep():
    av = mc.AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm: v{av.id}  ({av.git_commit[:7]})  {(av.git_message or '')[:60]}")
    print(f"N_ITER={N_ITER_SWEEP}  HOLD_BARS_SWEEP={HOLD_BARS_SWEEP}")
    print()

    # results[hold_bars][window_label][mode] = list of run_single_sim dicts
    results = {h: {w[0]: {m: [] for m in mc.COLLISION_MODES}
                   for w in mc.WINDOWS}
               for h in HOLD_BARS_SWEEP}

    for wlabel, wstart, wend in mc.WINDOWS:
        print(f"─── {wlabel} ({wstart} → {wend}) ─────────────────────────", flush=True)

        # Load signals & price history once per window
        call_sigs = mc.load_signals(av, wstart, wend)
        put_sigs  = mc.load_put_signals(av, wstart, wend)
        print(f"  calls={len(call_sigs)}  puts={len(put_sigs)}", flush=True)
        if not call_sigs and not put_sigs:
            continue

        sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
        ph      = mc.load_price_history(sym_ids, wstart, wend)

        breadth_dates, breadth_map = mc.load_breadth_map(wstart, wend)
        regime_dates,  regime_map  = mc.load_regime_map(wstart, wend)

        # Build trading calendar (all dates with price data in window)
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

        for hold_bars in HOLD_BARS_SWEEP:
            # Pre-compute put outcomes with this hold value
            put_outcomes = precompute_put_outcomes_with_hold(put_sigs, ph, hold_bars)

            for mode in mc.COLLISION_MODES:
                for it in range(N_ITER_SWEEP):
                    rng = random.Random(99991 + hash(wlabel) + hash(mode) * 7 +
                                        hold_bars * 31 + it * 1000)
                    r = mc.run_single_sim(
                        trading_days, calls_by_date, call_outcomes,
                        puts_by_date, put_outcomes,
                        mode, rng,
                        regime_dates, regime_map,
                    )
                    results[hold_bars][wlabel][mode].append(r)

            tp_h0 = sum(1 for o in precompute_put_outcomes_with_hold(put_sigs, ph, hold_bars).values()
                        if o['kind'] == 'tp')
            sl_h  = sum(1 for o in precompute_put_outcomes_with_hold(put_sigs, ph, hold_bars).values()
                        if o['kind'] == 'sl')
            tot   = len(put_outcomes) or 1
            print(f"  hold={hold_bars}: raw_TP%={tp_h0/tot*100:.1f}%  raw_SL%={sl_h/tot*100:.1f}%",
                  flush=True)

        print()

    # ── Print results ──────────────────────────────────────────────────────────
    SEP = "=" * 112

    def stats(runs, key):
        vals = [r[key] for r in runs]
        return statistics.mean(vals)

    def pct_ret(runs):
        return (statistics.mean(r['final'] for r in runs) / mc.STARTING_CASH - 1) * 100

    def worst_dd(runs):
        return max(r['max_dd'] for r in runs) * 100

    def p_collapse(runs):
        return sum(1 for r in runs if r['final'] <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD) / len(runs) * 100

    print()
    print(SEP)
    print("PUT SL HARD-HOLD SWEEP — Realistic Mode")
    print(f"  hold=0 = production baseline  |  hold=N = suppress SL for first N bars, TP active throughout")
    print(SEP)

    for wlabel, _, _ in mc.WINDOWS:
        print(f"\n{'─'*112}")
        print(f"  Window: {wlabel}")
        print(f"  {'Hold':>5}  {'MeanRet':>12}  {'MedianRet':>12}  {'WorstDD':>9}  "
              f"{'P(col)':>7}  {'PutTP%':>8}  {'CallTP%':>8}  {f'Δ vs h={HOLD_BARS_SWEEP[0]}':>12}")
        print(f"  {'─'*5}  {'─'*12}  {'─'*12}  {'─'*9}  {'─'*7}  {'─'*8}  {'─'*8}  {'─'*12}")

        base_runs = results[HOLD_BARS_SWEEP[0]][wlabel]['realistic']
        base_mean = pct_ret(base_runs) if base_runs else None
        for h in HOLD_BARS_SWEEP:
            runs = results[h][wlabel]['realistic']
            if not runs:
                print(f"  {h:>5}  [no data]"); continue
            mean_r  = pct_ret(runs)
            med_r   = (statistics.median(r['final'] for r in runs) / mc.STARTING_CASH - 1) * 100
            wdd     = worst_dd(runs)
            pc      = p_collapse(runs)
            put_tp  = stats(runs, 'put_tp')
            call_tp = stats(runs, 'call_tp')
            if h == HOLD_BARS_SWEEP[0]:
                delta_str = "—"
            else:
                delta_str = f"{mean_r - base_mean:+.1f}%" if base_mean is not None else "—"
            floor_flag = " ✗" if wdd > 80.0 else "  "
            print(f"  {h:>5}  {mean_r:>11.1f}%  {med_r:>11.1f}%  "
                  f"{wdd:>7.1f}%{floor_flag}  {pc:>6.1f}%  {put_tp:>7.1f}%  "
                  f"{call_tp:>7.1f}%  {delta_str:>12}")

    # Conservative DD floor summary
    print()
    print(SEP)
    print("CONSERVATIVE MODE — WorstDD (must stay ≤80%)")
    print(SEP)
    print(f"  {'Hold':>5}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f" {wlabel:>13}", end='')
    print()
    for h in HOLD_BARS_SWEEP:
        print(f"  {h:>5}  ", end='')
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[h][wlabel]['conservative']
            if not runs:
                print(f" {'N/A':>13}", end=''); continue
            wdd   = worst_dd(runs)
            mr    = pct_ret(runs)
            flag  = "✗" if wdd > 80.0 else " "
            print(f" {mr:>+9.1f}%{flag}  ", end='')
        print()

    # Delta vs baseline (Realistic)
    print()
    print(SEP)
    base_h = HOLD_BARS_SWEEP[0]
    print(f"REALISTIC MEAN RET DELTA vs hold={base_h}  (positive = better than baseline)")
    print(SEP)
    print(f"  {'Hold':>5}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f" {wlabel:>12}", end='')
    print(f"  {'AvgΔ':>8}")
    base_means = {wlabel: pct_ret(results[base_h][wlabel]['realistic'])
                  for wlabel, _, _ in mc.WINDOWS
                  if results[base_h][wlabel]['realistic']}
    for h in HOLD_BARS_SWEEP:
        print(f"  {h:>5}  ", end='')
        deltas = []
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[h][wlabel]['realistic']
            if not runs:
                print(f" {'N/A':>12}", end=''); continue
            delta = pct_ret(runs) - base_means.get(wlabel, 0)
            deltas.append(delta)
            print(f" {delta:>+11.1f}%", end='')
        avg = statistics.mean(deltas) if deltas else 0
        print(f"  {avg:>+7.1f}%")

    # Put TP% by hold (raw underlying, before portfolio simulation)
    print()
    print(SEP)
    print("PUT TP% (portfolio-level, Realistic)  ←  BE=43.5%; hard-hold raises TP% but may add hard-sells")
    print(SEP)
    print(f"  {'Hold':>5}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f" {wlabel:>12}", end='')
    print()
    for h in HOLD_BARS_SWEEP:
        print(f"  {h:>5}  ", end='')
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[h][wlabel]['realistic']
            if not runs:
                print(f" {'N/A':>12}", end=''); continue
            ptp  = stats(runs, 'put_tp')
            flag = "✗" if ptp < 43.5 else " "
            print(f" {ptp:>10.1f}%{flag}", end='')
        print()

    # Hard-sell % (how many extra hard sells from held positions)
    print()
    print(SEP)
    print("PUT HARD-SELL% (portfolio-level, Realistic)  ←  hard-hold converts some SL exits to hard sells")
    print(SEP)
    print(f"  {'Hold':>5}  ", end='')
    for wlabel, _, _ in mc.WINDOWS:
        print(f" {wlabel:>12}", end='')
    print()
    for h in HOLD_BARS_SWEEP:
        print(f"  {h:>5}  ", end='')
        for wlabel, _, _ in mc.WINDOWS:
            runs = results[h][wlabel]['realistic']
            if not runs:
                print(f" {'N/A':>12}", end=''); continue
            hard_p = stats(runs, 'put_hard')
            print(f" {hard_p:>10.1f}% ", end='')
        print()

    print()
    print("Done.")


if __name__ == '__main__':
    run_sweep()
