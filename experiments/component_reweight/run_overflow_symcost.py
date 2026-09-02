"""COST-MODEL ROBUSTNESS for overflow. My ship-grade used the strategy_config DEFAULT = asymmetric
canon (entry/TP FREE, only forced exits -0.015). The portfolio agent's profile_frontier.py used
SYMMETRIC -0.015 on ALL legs (~3% round-trip, every trade incl winners taxed) -> 140x lower returns.

The 70-74 overflow is ~68% apex-WR vs the 70% break-even. Under asymmetric (wins free) it's +EV;
under symmetric (wins pay ~3%) break-even rises and overflow could go marginal/negative. GATE: overflow
must still HELP and stay collapse-safe under the CONSERVATIVE symmetric cost before it ships live.

Runs baseline + overflow 0.030/0.035/0.040 under BOTH cost models, same windows, N=300.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate  # noqa: E402

N = int(os.environ.get("OVF_N", "300"))
WINDOWS = ["2020_crash", "2022", "22-now", "5y", "10y"]
ALLOCS = [0.030, 0.035, 0.040]

# the two cost models
ASYM = {"SLIP_ENTRY": 0.0, "SLIP_TP": 0.0, "SLIP_SL": -0.015, "SLIP_HARD": -0.015}      # live canon
SYM = {"SLIP_ENTRY": -0.015, "SLIP_TP": -0.015, "SLIP_SL": -0.015, "SLIP_HARD": -0.015}  # frontier ~3%

results = {}
for cost_name, cost in [("asym", ASYM), ("sym", SYM)]:
    results[cost_name] = {}
    base_params = dict(cost)
    t0 = time.time()
    print("\n>>> %s baseline" % cost_name, flush=True)
    results[cost_name]["baseline"] = run_candidate(base_params, n_iter=N, windows=WINDOWS, tag="cost_%s_base" % cost_name)
    for a in ALLOCS:
        print(">>> %s ovf_%.3f" % (cost_name, a), flush=True)
        p = dict(cost); p["TIER_OVERFLOW"] = a
        results[cost_name]["ovf_%.3f" % a] = run_candidate(p, n_iter=N, windows=WINDOWS, tag="cost_%s_%.3f" % (cost_name, a))
    print("    %s done %.0fs" % (cost_name, time.time() - t0), flush=True)

with open(os.path.join(_HERE, "overflow_symcost_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

for cost_name in ["asym", "sym"]:
    print("\n\n===== COST=%s (N=%d) =====" % (cost_name.upper(), N))
    d = results[cost_name]
    order = ["baseline"] + ["ovf_%.3f" % a for a in ALLOCS]
    for w in WINDOWS:
        print("\n--- %s ---" % w)
        for n in order:
            r = d.get(n, {}).get(w)
            if not r:
                print("  %-11s (missing)" % n); continue
            mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
            print("  %-11s ret=%-12s DD=%5.1f%% col=%4.1f%%" % (n, mr, r["worst_dd"], r["p_collapse"]))

print("\n=== VERDICT: does overflow HELP + stay collapse-safe under BOTH cost models? ===")
for cost_name in ["asym", "sym"]:
    d = results[cost_name]
    b10 = d.get("baseline", {}).get("10y", {}).get("med_ret", 0)
    b5 = d.get("baseline", {}).get("5y", {}).get("med_ret", 0)
    print("\n  [%s] baseline 10y=%.2e 5y=%.2e" % (cost_name, b10, b5))
    for a in ALLOCS:
        r = d.get("ovf_%.3f" % a, {})
        r10 = r.get("10y", {}).get("med_ret", 0); r5 = r.get("5y", {}).get("med_ret", 0)
        worst = max((r.get(w, {}).get("p_collapse", 99) for w in WINDOWS), default=99)
        help10 = "HELP" if r10 > b10 else "HURT"
        help5 = "HELP" if r5 > b5 else "HURT"
        safe = "SAFE" if worst <= 0.3 else "BREACH(%.1f%%)" % worst
        print("    ovf_%.3f  10y=%.2e(%s) 5y=%.2e(%s)  collapse=%s" % (a, r10, help10, r5, help5, safe))
print("\nIf overflow HELPs + SAFE under SYM too -> robustly shippable. If it only helps under ASYM -> the")
print("edge is cost-model-dependent (optimistic) -> ship conservative or flag prominently.")
print("wrote overflow_symcost_results.json")
