"""Per-trade OPTION CURVE — the real per-trade optimum (DTE x hold), honest theta.

Foundation step BEFORE portfolio levers: for the funded 75+ CALL cohort, find the
(DTE bought, hold duration) that maximizes per-trade option WIN RATE / option EV,
with HONEST calendar theta + honest sqrt(DTE) premium scaling. No portfolio / cascade /
allocation here — that's the next layer, built on this optimum.

Resolves the codebase cal-vs-trading inconsistency by measuring in CALENDAR days (the
honest unit: options expire on calendar dates, theta is calendar). Trading-bar
equivalent ~= cal_days x 0.69.

Per (D=DTE, W=hold cal days) over every 75+ call signal (active version, holdout-safe
<= CALIBRATION_CUTOFF), buy a D-DTE ATM call, exit at TP / wide-SL / hard-sell@W with
honest theta over D; tally:
  wr      = option P&L > 0 rate (true win rate, after asymmetric exec cost)
  tp_rate = TP-barrier-hit rate (the 'WRn' analog)
  ev      = mean option P&L (fraction of premium)
  med     = median option P&L

Reuses monte_carlo's data loaders + compute_trade_outcome/resolve (honest-theta branch).
Runs in-process (per-trade is cheap; flips mc globals, which the functions read at call time).

NOTE (v1): vega/earnings-crush is OFF (vega=1.0) so longer holds are marginally
optimistic (more likely to span earnings) — the true optimum may be a touch shorter.
Dead-hold is ON (strategy-faithful); DH_POP_SLIP default (popout realism tested in #92).
"""
import json
import os
import random
from datetime import date
from pathlib import Path

import monte_carlo as mc

RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(exist_ok=True)

D_START = date(2016, 6, 1)
HOLDOUT_END = date(2026, 5, 15)          # CALIBRATION_CUTOFF_DATE — no leak
MIN_SCORE = 75
DTES = [int(x) for x in os.environ.get("DTES", "15,21,30,45,60").split(",")]
W_BASE = [3, 5, 7, 9, 11, 13, 15, 18, 21, 25, 30, 35, 40, 45, 50, 55]


def w_grid(D):
    g = [w for w in W_BASE if w <= D - 1]
    return g or [max(2, D - 1)]


def main():
    version = mc.AlgorithmVersion.get_active_scores_version()
    print(f"Active version: {version.git_commit} (id={version.id})")

    sigs = [s for s in mc.load_signals(version, D_START, HOLDOUT_END)
            if s.overall >= MIN_SCORE]
    sym_ids = list({s.symbol_id for s in sigs})
    print(f"75+ CALL signals (holdout-safe): {len(sigs)}  across {len(sym_ids)} symbols")
    ph = mc.load_price_history(sym_ids, D_START, HOLDOUT_END)

    mc.CALENDAR_HOLD = True
    results = {}
    for D in DTES:
        mc.NOMINAL_CAL_DTE = D
        for W in w_grid(D):
            mc.HOLD_CAL_DAYS = W
            rng = random.Random(12345)
            n = wr = tp = 0
            ev = 0.0
            pnls = []
            for s in sigs:
                bars = ph.get(s.symbol_id)
                if not bars:
                    continue
                out = mc.compute_trade_outcome(bars, s.date, stressed=False)
                if out is None:
                    continue
                kind, pnl = mc.resolve(out, rng)
                n += 1
                ev += pnl
                pnls.append(pnl)
                if pnl > 0:
                    wr += 1
                if kind == "tp":
                    tp += 1
            if n:
                pnls.sort()
                results[f"D{D}_W{W}"] = {
                    "dte": D, "hold_cal": W, "n": n,
                    "wr": round(wr / n, 4), "tp_rate": round(tp / n, 4),
                    "ev": round(ev / n, 4), "med": round(pnls[len(pnls) // 2], 4),
                }

    # Per-DTE curves
    for D in DTES:
        print(f"\n=== DTE {D} (buy a {D}-cal-day ATM call) ===")
        print(f"{'holdCal':>8}{'~trdBars':>9}{'WR(P&L>0)':>11}{'TP-rate':>9}{'optEV':>9}{'med':>9}{'N':>8}")
        for W in w_grid(D):
            r = results.get(f"D{D}_W{W}")
            if r:
                print(f"{W:>8}{round(W*0.69):>9}{r['wr']*100:>10.1f}%{r['tp_rate']*100:>8.1f}%"
                      f"{r['ev']:>+9.3f}{r['med']:>+9.3f}{r['n']:>8}")

    # Optima
    by_ev = sorted(results.values(), key=lambda r: r["ev"], reverse=True)
    by_wr = sorted(results.values(), key=lambda r: r["wr"], reverse=True)
    print("\nTOP 8 by option EV (the return-relevant optimum):")
    for r in by_ev[:8]:
        print(f"  DTE={r['dte']:>3}  hold={r['hold_cal']:>3}cal (~{round(r['hold_cal']*0.69)}trd)  "
              f"EV={r['ev']:+.3f}  WR={r['wr']*100:.1f}%  TP={r['tp_rate']*100:.1f}%  N={r['n']}")
    print("\nTOP 8 by win rate (P&L>0):")
    for r in by_wr[:8]:
        print(f"  DTE={r['dte']:>3}  hold={r['hold_cal']:>3}cal  WR={r['wr']*100:.1f}%  "
              f"EV={r['ev']:+.3f}  TP={r['tp_rate']*100:.1f}%  N={r['n']}")

    best = by_ev[0]
    print(f"\nPER-TRADE OPTION OPTIMUM (by EV): buy ~{best['dte']} cal-DTE, hold ~{best['hold_cal']} "
          f"calendar days (~{round(best['hold_cal']*0.69)} trading bars)  ->  "
          f"optEV={best['ev']:+.3f}, WR={best['wr']*100:.1f}%")
    print("Canonical unit = CALENDAR days (options expire on calendar dates; theta is calendar).")

    (RESULTS / "curve.json").write_text(json.dumps(results, indent=2))
    print(f"\nWrote {RESULTS / 'curve.json'}")


if __name__ == "__main__":
    main()
