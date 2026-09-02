"""Smoke test: run a single MC window via the sim→MC bridge.

Small universe + 1 window + low N for fast confirmation that the patched
load_signals/load_put_signals work end-to-end without crashes and produce
sane output (compound, DD, TP rates non-degenerate).
"""
import os
import sys
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from simulator import ScoreSimulator
from experiments.sim_mc_bridge import build_score_lists, inject_into_mc
import monte_carlo as mc

SYMBOLS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL', 'TSLA', 'AVGO',
           'JPM', 'BAC', 'V', 'MA', 'WMT', 'PG', 'JNJ', 'XOM', 'CVX',
           'KO', 'PEP', 'ABBV', 'PFE', 'MRK', 'CRM', 'ORCL', 'CSCO',
           'AMD', 'INTC', 'IBM', 'QCOM', 'TXN', 'ADBE', 'NFLX', 'DIS',
           'CMCSA', 'T', 'VZ', 'NKE', 'COST', 'HD', 'LOW', 'UPS',
           'CAT', 'BA', 'GE', 'MMM', 'HON', 'LMT', 'RTX', 'SPY', 'QQQ']
LOOKBACK = 600  # 2024 window


def main():
    t0 = time.time()
    print(f"=== Smoke test: sim→MC bridge (N=50, 50 symbols, 2024 window) ===", flush=True)

    print("\n[1/3] Building simulator...", flush=True)
    sim = ScoreSimulator(symbols=SYMBOLS, lookback_days=LOOKBACK)
    rich = sim.simulate_full()
    rows = build_score_lists(rich)
    print(f"  produced {len(rows)} rich rows", flush=True)

    print("\n[2/3] Running MC via bridge (2024, N=50)...", flush=True)
    mc.N_ITER = 50
    os.environ['MC_NO_MP'] = '1'

    from database.models.core import AlgorithmVersion
    version = AlgorithmVersion.get_active_scores_version()

    with inject_into_mc(rows):
        res = mc.run_window('2024', date(2024, 1, 1), date(2024, 12, 31), version)
    r = res['seeded']

    print(f"\n[3/3] Results:", flush=True)
    print(f"  mean_ret  = {r['mean_ret']:>+15,.1f}%", flush=True)
    print(f"  worst_dd  = {r['worst_dd']:>5.1f}%", flush=True)
    print(f"  p_coll    = {r['p_coll']:>5.1f}%", flush=True)
    print(f"  call_tp   = {r['call_tp']:>5.1f}%  ({r['call_trades']} trades)", flush=True)
    print(f"  put_tp    = {r['put_tp']:>5.1f}%  ({r['put_trades']} trades)", flush=True)
    print(f"\nTotal elapsed: {(time.time() - t0):.1f}s", flush=True)

    # Sanity assertions
    assert r['mean_ret'] > -100, "Mean return implausibly low (collapse?)"
    assert 0 < r['call_tp'] < 100, f"Call TP out of range: {r['call_tp']}"
    assert r['call_trades'] > 0, "No call trades — load_signals patch may be broken"
    print("\n✓ All sanity checks passed", flush=True)


if __name__ == '__main__':
    main()
