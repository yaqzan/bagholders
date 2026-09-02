"""Pin the overflow COLLAPSE CLIFF (between 0.04 safe and 0.05 = 2% COVID collapse). Sweep
0.035/0.04/0.045/0.05 at N=300 to find the MAX-return alloc that still holds collapse=0 on
every window incl 2020-COVID. Winner escalates to N=500x8. Baseline reused (deterministic,
PYTHONHASHSEED=0) from overflow_refine_results.json.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate   # noqa: E402

N = int(os.environ.get("OVF_N", "300"))
WINDOWS = ["2020_crash", "2022", "22-now", "5y", "10y"]
ALLOCS = [0.035, 0.04, 0.045, 0.05]

# reuse deterministic baseline from the refinement run
results = {}
ref = os.path.join(_HERE, "overflow_refine_results.json")
if os.path.exists(ref):
    rj = json.load(open(ref))
    if rj.get("baseline_ovf0"):
        results["baseline"] = rj["baseline_ovf0"]
        print("reusing baseline from refinement", flush=True)

for a in ALLOCS:
    name = "ovf_%.3f" % a
    t0 = time.time()
    print("\n>>> %s (N=%d)" % (name, N), flush=True)
    try:
        results[name] = run_candidate({"TIER_OVERFLOW": a}, n_iter=N, windows=WINDOWS, tag="ovfedge_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}

with open(os.path.join(_HERE, "overflow_edge_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = (["baseline"] if "baseline" in results else []) + ["ovf_%.3f" % a for a in ALLOCS]
print("\n\n===== OVERFLOW COLLAPSE-CLIFF SWEEP (N=%d) =====" % N)
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-12s %16s %10s %11s %9s" % ("config", "MedRet%", "WorstDD%", "collapse%", "CallTrd"))
    for name in order:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-12s   (missing)" % name); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.3e" % r["med_ret"])
        print("  %-12s %16s %9.1f%% %10.1f%% %9s" % (
            name, mr, r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0)))))

# pick max-return alloc with collapse<=0.5% on EVERY window
print("\n=== max-return collapse-safe (<=0.5% all windows) ===")
safe = []
for name in order:
    if name == "baseline":
        continue
    rr = results.get(name, {})
    if rr and all(rr.get(w, {}).get("p_collapse", 99) <= 0.5 for w in WINDOWS):
        safe.append((name, rr.get("5y", {}).get("med_ret", 0), rr.get("10y", {}).get("med_ret", 0)))
for name, r5, r10 in safe:
    print("  SAFE %s  5y=%.0f%%  10y=%.3e%%" % (name, r5, r10))
print("  -> recommend the highest-alloc SAFE config for N=500x8" if safe else "  -> none safe; need crash-gate")
print("\nwrote overflow_edge_results.json")
