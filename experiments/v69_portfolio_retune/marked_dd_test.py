"""Marked-to-market drawdown test (honest paper DD of a HOLD strategy).

The deterministic backtest values OPEN positions at cost (premium paid), so its
reported max_dd closes its eyes between entry and exit. That's fine for a CUT
strategy (positions resolve in 2-7 bars) but understates a HOLD strategy, where a
position can sit 15 days deeply underwater before recovering to TP. backtest_cascade.py
now also tracks max_dd_mtm — open positions valued each bar via the validated
option_pnl_pct (delta+theta+vega) model. This script runs CUT vs HOLD on the full
honest-v69 2016+ window and prints cost-DD next to marked-DD.

  CUT  = shipped SL (~0.98 sigma) — stops early.
  HOLD = BC_SL_SIGMA_OV=10 — SL barrier ~never touched; rides to TP or day-15 hard-sell.

Validation expectation:
  CUT  -> marked DD ~= cost DD   (fast resolution, little hidden paper loss)
  HOLD -> marked DD >> cost DD   (the artifact: positions sit underwater)

Run: PYTHONIOENCODING=utf-8 python -u experiments/v69_portfolio_retune/marked_dd_test.py
"""
from __future__ import annotations
import os, sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database.models.core import AlgorithmVersion          # noqa: E402
import backtest_cascade as bc                               # noqa: E402

FROM = date(2016, 1, 1)
CAP = 50000.0

# full original cascade (denser 75+ book) — restores 80-84 and 75-79 tiers that the
# shipped 85+-only hygiene config zeroes out. Puts stay off (calls_only book).
T75_ALLOC = {'95+': 0.20, '85-94': 0.15, '80-84': 0.10, '75-79': 0.10, '70-74': 0.0,
             'p<=15': 0.0, 'p16-20': 0.0, 'p21-25': 0.0}


def run_one(version_id: int, min_score: float, hold: bool, tier_alloc: dict | None = None):
    if hold:
        os.environ['BC_SL_SIGMA_OV'] = '10'   # SL barrier 10 sigma below entry => never fires
    else:
        os.environ.pop('BC_SL_SIGMA_OV', None)
    cfg = {'tier_alloc': tier_alloc} if tier_alloc else None
    res = bc.run_cascade_backtest(version_id, min_score=min_score, from_date=FROM,
                                  initial=CAP, verbose=False, calls_only=True, cfg=cfg)
    tl = res['trade_log']
    n = len(tl)
    n_tp = sum(1 for t in tl if t['outcome'] == 'tp')
    n_sl = sum(1 for t in tl if t['outcome'] == 'sl')
    n_hard = sum(1 for t in tl if t['outcome'] == 'hard')
    holds = [t.get('hold_bars', 0) for t in tl]
    return dict(
        ret=res['final_equity'] / res['initial'] - 1.0,
        dd_cost=res['max_dd'],
        dd_mtm=res.get('max_dd_mtm', float('nan')),
        n=n,
        tp_pct=(n_tp / n * 100) if n else 0.0,
        sl_pct=(n_sl / n * 100) if n else 0.0,
        hard_pct=(n_hard / n * 100) if n else 0.0,
        avg_hold=(sum(holds) / n) if n else 0.0,
    )


# v60  = inflated (look-ahead weekly in recalc) — the original
# v65  = "unify weekly partial context" = v60-base made HONEST (pure current-partial,
#        point-in-time) WITHOUT the v69 blend. This is "v60 without the inflation".
# v69  = honest + bars-weighted transition blend (the shipped methodology change).
VERSIONS = [('v60 inflated', 60), ('v60-honest (v65)', 65), ('v69 blend', 69)]
CONFIGS = [
    ('85+ CUT ', 85, False, None),
    ('85+ HOLD', 85, True,  None),
    ('75+ CUT ', 75, False, T75_ALLOC),
    ('75+ HOLD', 75, True,  T75_ALLOC),
]


def main():
    print(f"window {FROM}.. cap ${CAP:,.0f}  | frictionless/SLIP=0 (TFSA mid-fills), calls-only")
    print("v60 = inflated look-ahead scores (read-only); v69 = honest weekly. Same engine, same window.\n")
    results = {}
    for vlabel, vid in VERSIONS:
        for clabel, ms, hold, ta in CONFIGS:
            print(f"running {vlabel:<28} {clabel} ...", flush=True)
            results[(vid, clabel)] = run_one(vid, ms, hold, ta)

    hdr = (f"{'config':<10}{'totRet':>14}{'DD(cost)':>10}{'DD(mark)':>10}{'gap':>7}"
           f"{'N':>6}{'TP%':>7}{'SL%':>7}{'hard%':>7}{'hold':>6}")
    for vlabel, vid in VERSIONS:
        print(f"\n=== {vlabel} ===")
        print(hdr)
        for clabel, ms, hold, ta in CONFIGS:
            r = results[(vid, clabel)]
            gap = r['dd_mtm'] - r['dd_cost']
            print(f"{clabel:<10}{r['ret']*100:>+13.0f}%{r['dd_cost']*100:>9.1f}%{r['dd_mtm']*100:>9.1f}%"
                  f"{gap*100:>+6.1f}{r['n']:>6}{r['tp_pct']:>6.1f}%{r['sl_pct']:>6.1f}%"
                  f"{r['hard_pct']:>6.1f}%{r['avg_hold']:>6.1f}")

    def _ratio(a, b):
        return ((1 + a['ret']) / (1 + b['ret'])) if (1 + b['ret']) > 0 else float('nan')

    print("\n=== (1) DE-INFLATION: v60 inflated -> v65 honest (look-ahead removed, ~same methodology) ===")
    for clabel, ms, hold, ta in CONFIGS:
        r60, r65 = results[(60, clabel)], results[(65, clabel)]
        print(f"  {clabel}: v60 {r60['ret']*100:+.0f}% -> v65 {r65['ret']*100:+.0f}%  ({_ratio(r60, r65):.1f}x lower)  | "
              f"N {r60['n']}->{r65['n']}  TP {r60['tp_pct']:.0f}%->{r65['tp_pct']:.0f}%  "
              f"markDD {r60['dd_mtm']*100:.0f}%->{r65['dd_mtm']*100:.0f}%")

    print("\n=== (2) METHODOLOGY (your question): v65 honest-partial -> v69 honest-blend (both honest) ===")
    for clabel, ms, hold, ta in CONFIGS:
        r65, r69 = results[(65, clabel)], results[(69, clabel)]
        print(f"  {clabel}: v65 {r65['ret']*100:+.0f}% -> v69 {r69['ret']*100:+.0f}%  ({_ratio(r69, r65):.2f}x)  | "
              f"N {r65['n']}->{r69['n']}  TP {r65['tp_pct']:.0f}%->{r69['tp_pct']:.0f}%  "
              f"markDD {r65['dd_mtm']*100:.0f}%->{r69['dd_mtm']*100:.0f}%")

    print("\nRead: DD(cost)=open positions at premium paid (engine default). DD(mark)=open positions via")
    print("  option_pnl_pct (delta+theta+vega) each bar. gap=mark-cost = hidden paper drawdown.")
    print("  CUT validates wiring (gap~0). HOLD vs CUT = does riding beat early-stopping on HONEST DD.")


if __name__ == '__main__':
    main()
