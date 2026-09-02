"""
Theta-drag audit — quantify the gap between σ-trigger SL and actual option P&L.

Question: under the option-pricing-aware MC (commit 3432fb8), trigger detection
still uses static σ-barriers on the underlying — so an option can sit at a deep
premium loss (theta + adverse-but-not-extreme underlying move) for several bars
without firing SL, then either eventually fire SL late or hit hard-sell at day
HOLD_DAYS at deep loss.

This script walks each MC-eligible signal bar-by-bar with `option_pnl_pct` and
reports:

  1. For trades that end as 'hard' (no σ-trigger fire): distribution of the
     worst per-bar option P&L hit during the hold. % that crossed -30%, -40%,
     -50%, -60% mid-trade *without* firing the σ-SL.

  2. For trades that end as 'sl': the actual option P&L at the σ-trigger fire
     bar vs the static SL_BASE assumption. How often does SL fire at much
     deeper realized loss than the nominal -30% / -35%?

  3. Bar distribution of the worst per-bar P&L (early-vs-late).

Vega is held at 1.0 (non-spanning earnings only contribute theta + intrinsic).
This isolates the theta-drag question; spanning-earnings trades add a separate
vega dimension that is orthogonal to the trigger gap.

Usage:
    PYTHONIOENCODING=utf-8 python experiments/theta_drag_audit.py             # default 22-now
    PYTHONIOENCODING=utf-8 python experiments/theta_drag_audit.py 5y          # 2021->now
    PYTHONIOENCODING=utf-8 python experiments/theta_drag_audit.py 2024        # single year

Reads from active scoring version. Read-only — no DB writes, no MC randomness.
"""
import os
import sys
import math
from collections import defaultdict
from datetime import date, timedelta

# Repo root on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import monte_carlo as mc
from option_pricing import option_pnl_pct, DEFAULT_TOTAL_DTE, DEFAULT_DELTA
from database.models.core import AlgorithmVersion


WINDOWS = {
    '2021':   (date(2021, 1, 1),  date(2021, 12, 31)),
    '2022':   (date(2022, 1, 1),  date(2022, 12, 31)),
    '2023':   (date(2023, 1, 1),  date(2023, 12, 31)),
    '2024':   (date(2024, 1, 1),  date(2024, 12, 31)),
    '2025':   (date(2025, 1, 1),  date(2025, 12, 31)),
    '22-now': (date(2022, 1, 1),  date.today()),
    '5y':     (date.today() - timedelta(days=1825), date.today()),
}

# Loss thresholds tracked
DD_THRESHOLDS = [-0.20, -0.30, -0.40, -0.50, -0.60, -0.70, -0.80]


def per_bar_pnl_walk(side, entry_price, premium_pct, future_closes,
                     hold_days=mc.HOLD_DAYS, total_dte=DEFAULT_TOTAL_DTE):
    """Walk bar-by-bar, computing option P&L at each close. Returns list of
    (bar, pnl) for bars 1..min(hold_days, len(future_closes)).
    """
    out = []
    n = min(hold_days, len(future_closes))
    for t in range(1, n + 1):
        u_t = future_closes[t - 1]
        pnl = option_pnl_pct(side, u_t, entry_price, t, premium_pct,
                             total_dte=total_dte, vega_ratio=1.0,
                             delta=DEFAULT_DELTA)
        out.append((t, pnl))
    return out


def audit_window(label, d_start, d_end, version):
    print(f"\n{'='*100}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})")
    print('='*100)

    call_sigs = mc.load_signals(version, d_start, d_end)
    put_sigs  = mc.load_put_signals(version, d_start, d_end)
    sym_ids   = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph        = mc.load_price_history(sym_ids, d_start, d_end)

    print(f"Signals: calls={len(call_sigs)}  puts={len(put_sigs)}  symbols={len(sym_ids)}")

    rows = []   # one dict per analyzed trade

    def process(sig, side):
        bars = ph.get(sig.symbol_id)
        if not bars:
            return
        # Build aligned arrays for σ-trigger via mc.compute_*_outcome
        if side == 'call':
            outcome = mc.compute_trade_outcome(bars, sig.date, stressed=False)
        else:
            outcome = mc.compute_put_outcome(bars, sig.date, put_stressed=False)
        if outcome is None:
            return

        # Find entry idx, compute future closes from bars list
        try:
            base_idx = next(i for i, b in enumerate(bars) if b[0] == sig.date)
        except StopIteration:
            return
        future_closes = [b[1] for b in bars[base_idx + 1:]]
        if not future_closes:
            return

        entry_price = outcome['entry']
        premium_pct = outcome['premium_pct']

        walk = per_bar_pnl_walk(side, entry_price, premium_pct, future_closes,
                                hold_days=mc.HOLD_DAYS)
        if not walk:
            return

        max_dd_bar, max_dd = min(walk, key=lambda x: x[1])
        terminal_bar, terminal_pnl = walk[-1]
        # P&L at the σ-trigger fire bar (for SL/TP outcomes)
        fire_bar = outcome['exit_bar']
        if 1 <= fire_bar <= len(walk):
            pnl_at_fire = walk[fire_bar - 1][1]
        else:
            pnl_at_fire = None

        rows.append(dict(
            side=side,
            kind=outcome['kind'],
            fire_bar=fire_bar,
            max_dd=max_dd,
            max_dd_bar=max_dd_bar,
            terminal_pnl=terminal_pnl,
            pnl_at_fire=pnl_at_fire,
            walk_len=len(walk),
        ))

    for s in call_sigs:
        process(s, 'call')
    for s in put_sigs:
        process(s, 'put')

    return rows


def report(rows, label):
    print(f"\n--- REPORT: {label} ---")
    if not rows:
        print("  (no trades)")
        return

    # Group by (side, kind)
    by_side_kind = defaultdict(list)
    for r in rows:
        by_side_kind[(r['side'], r['kind'])].append(r)

    for side in ('call', 'put'):
        kinds = ['hard', 'sl', 'tp', 'both']
        side_rows = [r for r in rows if r['side'] == side]
        if not side_rows:
            continue

        print(f"\n  {side.upper()} (N={len(side_rows)})")
        print(f"    kind  N      pct    avg_max_dd  med_max_dd  avg_dd_bar")
        for kind in kinds:
            sub = by_side_kind.get((side, kind), [])
            if not sub:
                continue
            n = len(sub)
            pct = 100.0 * n / len(side_rows)
            mds = sorted(r['max_dd'] for r in sub)
            avg = sum(mds) / n
            med = mds[n // 2]
            avg_bar = sum(r['max_dd_bar'] for r in sub) / n
            print(f"    {kind:5s} {n:6d} {pct:5.1f}%  {avg:+10.3f}  {med:+10.3f}  {avg_bar:9.2f}")

        # HARD-SELL DEEP-DRAWDOWN AUDIT
        hard = by_side_kind.get((side, 'hard'), [])
        if hard:
            print(f"\n  {side.upper()} HARD-SELL drawdown crossings (N={len(hard)})")
            print(f"    threshold  count   pct of hard")
            for thr in DD_THRESHOLDS:
                n_cross = sum(1 for r in hard if r['max_dd'] <= thr)
                pct = 100.0 * n_cross / len(hard)
                print(f"    {thr:+9.2f}  {n_cross:5d}   {pct:5.1f}%")
            # Distribution of bar-of-max-dd among hard sells
            bars = sorted(r['max_dd_bar'] for r in hard)
            print(f"    bar-of-max-DD: p25={bars[len(bars)//4]}  median={bars[len(bars)//2]}  p75={bars[3*len(bars)//4]}  max={bars[-1]}")

        # SL FIRE-BAR P&L AUDIT
        sl = by_side_kind.get((side, 'sl'), [])
        sl_with_fire = [r for r in sl if r['pnl_at_fire'] is not None]
        if sl_with_fire:
            print(f"\n  {side.upper()} SL P&L at σ-trigger fire bar (N={len(sl_with_fire)})")
            pnls = sorted(r['pnl_at_fire'] for r in sl_with_fire)
            n = len(pnls)
            print(f"    avg={sum(pnls)/n:+.3f}  p10={pnls[n//10]:+.3f}  p25={pnls[n//4]:+.3f}  median={pnls[n//2]:+.3f}  p75={pnls[3*n//4]:+.3f}  p90={pnls[9*n//10]:+.3f}")
            for thr in [-0.30, -0.40, -0.50, -0.60, -0.70]:
                n_worse = sum(1 for p in pnls if p <= thr)
                pct = 100.0 * n_worse / n
                print(f"    SL fire P&L <= {thr:+.2f}:  {n_worse:5d}   {pct:5.1f}%")


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else '22-now'
    if label not in WINDOWS:
        print(f"Unknown window '{label}'. Choices: {list(WINDOWS.keys())}")
        sys.exit(1)
    d_start, d_end = WINDOWS[label]

    av = AlgorithmVersion.get_active_scores_version()
    print(f"Active scoring version: id={av.id}  commit={av.git_commit}")
    print(f"MC config: HOLD_DAYS={mc.HOLD_DAYS}  PREMIUM_MULT={mc.PREMIUM_MULT}  "
          f"TP={mc.TP_BASE}/{mc.TP_STRESS}  SL={mc.SL_BASE}/{mc.SL_STRESS}  "
          f"PUT_TP={mc.PUT_TP}  PUT_SL={mc.PUT_SL}")
    print(f"option_pricing: total_dte={DEFAULT_TOTAL_DTE}  delta={DEFAULT_DELTA}  vega_ratio=1.0 (theta-only audit)")

    rows = audit_window(label, d_start, d_end, av)
    report(rows, label)


if __name__ == '__main__':
    main()
