"""Authoritative per-year clean-Apex MedRet/WorstDD (canonical profile_frontier Apex config)."""
import os, sys, json, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
os.environ['ALGORITHM_VERSION_PIN'] = 'c70d16d22'
from experiments.v69_portfolio_retune.driver import run_candidate

APEX = dict(
    TP_BASE=0.30, TP_STRESS=0.30, SL_BASE=-0.70, SL_STRESS=-0.70,
    HOLD_DAYS=15, MAX_POSITIONS=14, MAX_POSITIONS_CALL=14,
    TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.10, TIER_LOW=0.10, TIER_OVERFLOW=0.0,
    PUT_TIER_TOP=0.0, PUT_TIER_MID=0.0, PUT_TIER_LOW=0.0, PUT_PREMIUM_CAP=0.0,
    SLIP_ENTRY=-0.015, SLIP_TP=-0.015, SLIP_SL=-0.015, SLIP_HARD=-0.015,
    GROSS_PREMIUM_CAP=0.50, CALL_PREMIUM_CAP=0.50, PRACTICAL_CAPITAL_CEILING=0,
)
WINDOWS = ['2018', '2021', '2022', '2023', '2024', '2025', '5y', '10y']
n = int(os.environ.get('N_ITER', '200'))
t0 = time.time()
res = run_candidate(APEX, n, WINDOWS, tag='apex_peryear')
print("\n=== CLEAN APEX per-year (canonical, puts-off, no DD levers) | N=%d ===" % n)
print(f"{'window':<8} {'MedRet':>16} {'MeanRet':>16} {'WorstDD':>8} {'CTP%':>6} {'CTrd':>7} {'collapse':>9}")
for w in WINDOWS:
    r = res.get(w, {})
    if not r: continue
    print(f"{w:<8} {r.get('med_ret','?'):>15}% {r.get('mean_ret','?'):>15}% "
          f"{r.get('worst_dd','?'):>7}% {r.get('call_tp','?'):>5}% {r.get('call_trades','?'):>7} {r.get('p_collapse','?'):>8}%")
out = Path(__file__).parent / 'apex_peryear.json'
out.write_text(json.dumps(res, indent=2, default=str))
print(f"\nwrote {out}  ({time.time()-t0:.0f}s)")
