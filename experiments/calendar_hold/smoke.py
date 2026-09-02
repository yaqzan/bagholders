"""Calendar-hold smoke test — pure-function, NO MySQL, NO portfolio MC.

Verifies the env-gated CALENDAR_HOLD branch in monte_carlo.compute_trade_outcome
/ _compute_dead_hold_call / resolve:

  1. Baseline (CALENDAR_HOLD=False) is byte-identical to the prior trading-bar
     engine: hard-sell at exit_bar == HOLD_DAYS, theta over bars=15 / DTE=30.
  2. Calendar-21 (HOLD_CAL_DAYS=21) holds ~the same number of trading bars but
     decays theta over the TRUE ~21 calendar days elapsed -> deeper hard-sell.
  3. Calendar-15 (HOLD_CAL_DAYS=15, literal halfway of a 30 DTE option) exits
     fewer trading bars in, with honest halfway theta.

The functions read the module globals at call time, so we flip them in-process
(this avoids the MP-spawn env issue — that only matters for the real run_window
A/B, which is driven via subprocess + env vars in run_ab.py).
"""
import datetime as _dt
import random

import monte_carlo as mc
from option_pricing import theta_pnl_pct


def _business_days(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += _dt.timedelta(days=1)
    return out


def _make_bars():
    """80 leading noisy bars (so realized_vol > 0) + 40 flat forward bars.
    Flat forward => no TP/SL ever touched => deterministic hard-sell, so the
    only difference between modes is the theta clock. bars = (date, c, h, l, o).
    """
    rng = random.Random(0)
    dates = _business_days(_dt.date(2024, 1, 1), 140)
    bars = []
    px = 100.0
    for i, d in enumerate(dates):
        if i <= 80:
            ret = rng.gauss(0, 0.008)           # ~0.8%/day noise -> sigma > 0
            px *= (1 + ret)
            hi = px * 1.004; lo = px * 0.996; op = px
            bars.append((d, px, hi, lo, op))
        else:
            # flat forward: high=low=open=close=entry -> never breaches barriers
            entry = bars[80][1]
            bars.append((d, entry, entry, entry, entry))
    return bars, 80


def _run(label, calendar, hold_cal_days):
    mc.CALENDAR_HOLD = calendar
    mc.HOLD_CAL_DAYS = hold_cal_days
    mc.NOMINAL_CAL_DTE = 30
    bars, base_idx = _make_bars()
    sig_date = bars[base_idx][0]
    out = mc.compute_trade_outcome(bars, sig_date, stressed=False)
    assert out is not None, f"{label}: outcome is None"
    pnl_kind, pnl = mc.resolve(out, random.Random(1))
    print(f"{label:<14} kind={out['kind']:<5} exit_bar={out['exit_bar']:>3}  "
          f"cal_held={out.get('cal_held'):>3}  resolve=({pnl_kind},{pnl:+.4f})")
    return out, pnl


def main():
    print("theta sanity:  theta(15bars,30) = %.4f   theta(21bars,30) = %.4f   "
          "theta(11bars,30) = %.4f"
          % (theta_pnl_pct(15, 30), theta_pnl_pct(21, 30), theta_pnl_pct(11, 30)))
    print(f"HOLD_DAYS (bar engine) = {mc.HOLD_DAYS}\n")

    base, base_pnl = _run("baseline", False, 21)
    c21,  c21_pnl  = _run("calendar-21", True, 21)
    c15,  c15_pnl  = _run("calendar-15", True, 15)

    print()
    # 1. baseline unchanged: hard-sell at HOLD_DAYS bars
    assert base['kind'] == 'hard', f"baseline kind {base['kind']}"
    assert base['exit_bar'] == mc.HOLD_DAYS, f"baseline exit_bar {base['exit_bar']}"
    # baseline ALREADY knows ~21 cal days elapsed at the 15-bar hard-sell:
    assert 19 <= base['cal_held'] <= 23, f"baseline cal_held {base['cal_held']}"
    # 2. calendar-21 ~same bars held, but deeper hard theta (honest decay)
    assert 19 <= c21['cal_held'] <= 23, f"c21 cal_held {c21['cal_held']}"
    assert c21_pnl < base_pnl - 0.10, (
        f"c21 hard pnl should be materially deeper: {c21_pnl} vs {base_pnl}")
    # 3. calendar-15 exits fewer bars in (literal halfway of 30 DTE)
    assert c15['exit_bar'] < base['exit_bar'], (
        f"c15 exit_bar {c15['exit_bar']} !< {base['exit_bar']}")
    assert 13 <= c15['cal_held'] <= 16, f"c15 cal_held {c15['cal_held']}"

    print("SMOKE OK:")
    print(f"  baseline   : holds {base['exit_bar']} bars (~{base['cal_held']} cal days), "
          f"hard pnl {base_pnl:+.3f}  <- theta UNDER-stated (priced 15/30, really ~21/30)")
    print(f"  calendar-21: holds {c21['exit_bar']} bars (~{c21['cal_held']} cal days), "
          f"hard pnl {c21_pnl:+.3f}  <- honest theta (the realism fee), ~same exposure")
    print(f"  calendar-15: holds {c15['exit_bar']} bars (~{c15['cal_held']} cal days), "
          f"hard pnl {c15_pnl:+.3f}  <- literal halfway of a 30 DTE option")
    print(f"\n  theta-only hard-sell delta C21 vs baseline: {c21_pnl - base_pnl:+.3f} "
          f"(this is the per-slow-trade realism cost the A/B will compound)")


if __name__ == '__main__':
    main()
