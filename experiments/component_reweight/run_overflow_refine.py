"""Refine the 70-74 overflow: find an alloc that captures the (large) return upside while keeping
2020-COVID collapse at ~0 (the hard Apex floor). Collapse scaled 0.7%->2%->21% at alloc 0/0.05/0.10,
so sweep LOWER allocs. N=300 for a reliable collapse-tail estimate (rare event). Baseline re-run
at N=300 to confirm whether the shipped Apex COVID collapse is truly ~0 or ~0.7%.

Collapse-critical + return-decision windows. Asymmetric canon, v70 scores, no recalc.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate   # noqa: E402

N = int(os.environ.get("OVF_N", "300"))
WINDOWS = ["2020_crash", "2022", "22-now", "5y", "10y"]
CONFIGS = [
    ("baseline_ovf0", {}),
    ("ovf_0.02", {"TIER_OVERFLOW": 0.02}),
    ("ovf_0.03", {"TIER_OVERFLOW": 0.03}),
    ("ovf_0.04", {"TIER_OVERFLOW": 0.04}),
]

results = {}
for name, params in CONFIGS:
    t0 = time.time()
    print("\n>>> %s (N=%d) %s" % (name, N, params), flush=True)
    try:
        results[name] = run_candidate(params, n_iter=N, windows=WINDOWS, tag="ovfref_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}

with open(os.path.join(_HERE, "overflow_refine_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

print("\n\n===== 70-74 OVERFLOW REFINEMENT (N=%d, asymmetric canon) =====" % N)
print("GATE: collapse must be ~0 on EVERY window incl 2020-COVID. Return is the prize; DD is the budget.")
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-14s %16s %10s %11s %9s" % ("config", "MedRet%", "WorstDD%", "collapse%", "CallTrd"))
    for name, _ in CONFIGS:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-14s   (missing)" % name); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.3e" % r["med_ret"])
        print("  %-14s %16s %9.1f%% %10.1f%% %9s" % (
            name, mr, r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0)))))
print("\nwrote overflow_refine_results.json")
