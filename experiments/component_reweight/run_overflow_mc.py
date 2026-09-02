"""APEX MC: does enabling the 70-74 overflow tier boost Apex compound return at acceptable DD,
with collapse-rate held at 0 (the one hard floor)?

Re-opens the disabled 70-74 overflow under the NEW conditions: asymmetric cost canon (untaxed
wins -> turnover no longer a 'thousand cuts') + Apex risk budget (high recoverable DD accepted;
only collapse=0 is the floor). The 2026-04 disable was a Sentinel/80%-floor decision under the
OLD symmetric cost. Filled-book occupancy showed 70-74 nearly TRIPLES the filled book (3204 ->
8821) at ~68% WR (the 75+ book sits idle most days). Portfolio knob only: reads v70 DB scores,
no recalc, asymmetric canon live by default.

Baseline = Apex (overflow off, only ct_call 70-74 enter). Variants add the overflow alloc.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate   # noqa: E402

N = int(os.environ.get("OVF_N", "150"))
WINDOWS = ["2020_crash", "2022", "2025", "22-now", "5y", "10y"]
CONFIGS = [
    ("baseline_ovf0", {}),
    ("ovf_0.05", {"TIER_OVERFLOW": 0.05}),
    ("ovf_0.10", {"TIER_OVERFLOW": 0.10}),
]

results = {}
for name, params in CONFIGS:
    t0 = time.time()
    print("\n>>> running %s (N=%d) params=%s" % (name, N, params), flush=True)
    try:
        results[name] = run_candidate(params, n_iter=N, windows=WINDOWS, tag="ovf_" + name)
        print("    done in %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}

with open(os.path.join(_HERE, "overflow_mc_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

print("\n\n===== APEX 70-74 OVERFLOW MC (N=%d, asymmetric canon, v70 scores) =====" % N)
print("MedRet = median compound return; collapse must stay 0; CTrд = call trades (supply).")
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-14s %18s %10s %10s %10s" % ("config", "MedRet%", "WorstDD%", "collapse%", "CallTrд"))
    for name, _ in CONFIGS:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-14s   (missing)" % name); continue
        print("  %-14s %18s %9.1f%% %9.1f%% %10s" % (
            name,
            ("%.1f" % r["med_ret"] if abs(r["med_ret"]) < 1e6 else "%.3e" % r["med_ret"]),
            r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0)))))
print("\nwrote overflow_mc_results.json")
