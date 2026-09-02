"""SHIP-GRADE evidence for the overflow handoff: N=500 x full canonical+crash window set.
Adaptive — reads the #6 strength-sweep result and, IF a collapse-safe weekly filter exists
(2020_crash collapse <= 0.3%), includes it as a config; otherwise ships plain overflow only.

This is the evidence package the portfolio agent validates against before flipping
strategy_config.TIER_ALLOC['overflow']. DD-primary, collapse=0 floor; return is the prize.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate  # noqa: E402

N = int(os.environ.get("OVF_N", "500"))
WINDOWS = ["2020_crash", "2020", "2022", "2024", "2025", "dip", "22-now", "5y", "10y"]

# Plain overflow frontier — the shippable candidate. (The #6 strength sweep CLOSED the weekly filter:
# the only return-boosting cut breaches the COVID collapse floor; every collapse-safe cut is within
# noise of plain. So no filter in the ship-grade — plain overflow's full breadth is load-bearing.)
CONFIGS = [
    ("baseline", {}, None),
    ("ovf_0.030", {"TIER_OVERFLOW": 0.030}, None),
    ("ovf_0.035", {"TIER_OVERFLOW": 0.035}, None),
    ("ovf_0.040", {"TIER_OVERFLOW": 0.040}, None),
]


def set_filter(cut):
    if cut is not None:
        os.environ["WEEKLY_OVF_FILTER"] = "1"
        os.environ["WEEKLY_OVF_WCOMP_MAX"] = str(cut)
    else:
        os.environ.pop("WEEKLY_OVF_FILTER", None)
        os.environ.pop("WEEKLY_OVF_WCOMP_MAX", None)


results = {}
for name, params, cut in CONFIGS:
    set_filter(cut)
    t0 = time.time()
    print("\n>>> %s (N=%d) %s filt=%s" % (name, N, params, cut), flush=True)
    try:
        results[name] = run_candidate(params, n_iter=N, windows=WINDOWS, tag="ship_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}
    finally:
        set_filter(None)

with open(os.path.join(_HERE, "overflow_shipgrade_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = [c[0] for c in CONFIGS]
print("\n\n===== OVERFLOW SHIP-GRADE (N=%d x %d windows, apex canon) =====" % (N, len(WINDOWS)))
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-16s %16s %9s %10s %8s" % ("config", "MedRet%", "DD%", "collapse%", "CallTrd"))
    for name in order:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-16s (missing)" % name); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
        print("  %-16s %16s %8.1f%% %9.1f%% %8s" % (
            name, mr, r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0)))))

print("\n=== collapse=0 (<=0.3%) on EVERY window? (the Apex floor) ===")
for name in order:
    r = results.get(name, {})
    if not r:
        continue
    worst = max((r.get(w, {}).get("p_collapse", 99) for w in WINDOWS), default=99)
    r10 = r.get("10y", {}).get("med_ret", 0)
    ok = "SAFE  " if worst <= 0.3 else "BREACH"
    print("  %s %-16s maxCollapse=%.1f%%  10y=%.2e%%" % (ok, name, worst, r10))
print("\nwrote overflow_shipgrade_results.json  (hand to portfolio agent w/ OVERFLOW_HANDOFF.md)")
