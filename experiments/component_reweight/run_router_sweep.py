"""15DTE ROUTER broadening sweep (task #12). The live router is tiny+safe (DAY_CAP=1, ~0.36% of
trades). The 10x bull prize needs BROADENING (more 15DTE in bull tape) WITHOUT the crash-ruin
(pure 15DTE = 75-100% COVID collapse). The new DTE_ROUTER_VIX_MAX crash-gate routes to 15DTE ONLY
when VIX <= ceiling (low-vol/bull) and falls back to 30DTE when VIX is elevated.

Q: does a VIX_MAX gate let me crank DAY_CAP (route more) while holding 2020-COVID collapse=0?
Runs on the apex+overflow base (TIER_OVERFLOW=0.035). Router params set via os.environ (monte_carlo
reads them from env). Windows incl the collapse-binding 2020_crash/2020. N=300.

GATE: a candidate ships ONLY if collapse<=0.3% on 2020_crash AND 2020 (the hard Apex floor). Among
those, maximize 10y / 22-now return (the bull-tape prize).
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate  # noqa: E402

N = int(os.environ.get("OVF_N", "300"))
WINDOWS = ["2020_crash", "2020", "2022", "22-now", "5y", "10y"]
OVERFLOW = float(os.environ.get("ROUTER_OVERFLOW", "0.035"))   # the live apex+overflow base

# router knobs set via env (monte_carlo reads DTE_ROUTER_* from os.environ).
#  name                 day_cap score_min trend_lt vix_max
CANDS = [
    ("live_tiny",            1,  80, 50.0,  0.0),   # current live router (no crash gate, tiny)
    ("broad_nogate",        10,  80, 100.0, 0.0),   # broad, NO crash gate (should breach COVID)
    ("broad_vix22",         10,  80, 100.0, 22.0),  # broad + tight crash gate
    ("broad_vix28",         10,  80, 100.0, 28.0),
    ("broad_vix35",         10,  80, 100.0, 35.0),
    ("mid_vix25_s75",        5,  75, 100.0, 25.0),  # broader score floor + mid cap + gate
    ("wide_vix22_s75",      14,  75, 100.0, 22.0),  # max breadth + tightest gate
]


def set_router(day_cap, score_min, trend_lt, vix_max):
    os.environ["DTE_ROUTER_ENABLED"] = "1"
    os.environ["DTE_ROUTER_TARGET_DTE"] = "15"
    os.environ["DTE_ROUTER_DAY_CAP"] = str(day_cap)
    os.environ["DTE_ROUTER_SCORE_MIN"] = str(score_min)
    os.environ["DTE_ROUTER_TREND_LT"] = str(trend_lt)
    os.environ["DTE_ROUTER_VIX_MAX"] = str(vix_max)
    os.environ["DTE_ROUTER_VIX_MIN"] = "0.0"


results = {}
for name, dc, sm, tl, vmax in CANDS:
    set_router(dc, sm, tl, vmax)
    t0 = time.time()
    print("\n>>> %s (cap=%d score>=%d trend<%.0f vix<=%.0f)" % (name, dc, sm, tl, vmax), flush=True)
    try:
        results[name] = run_candidate({"TIER_OVERFLOW": OVERFLOW}, n_iter=N, windows=WINDOWS, tag="router_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}

with open(os.path.join(_HERE, "router_sweep_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = [c[0] for c in CANDS]
print("\n\n===== 15DTE ROUTER BROADENING (apex+overflow %.3f, N=%d) =====" % (OVERFLOW, N))
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-15s %16s %9s %10s %9s" % ("config", "MedRet%", "DD%", "collapse%", "routedCT"))
    for name in order:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-15s (missing)" % name); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
        print("  %-15s %16s %8.1f%% %9.1f%% %9s" % (name, mr, r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0)))))

print("\n=== collapse-safe (2020_crash AND 2020 <= 0.3%) ranked by 10y ===")
safe = []
for name in order:
    r = results.get(name, {})
    cc = r.get("2020_crash", {}).get("p_collapse", 99)
    c20 = r.get("2020", {}).get("p_collapse", 99)
    if cc <= 0.3 and c20 <= 0.3:
        safe.append((name, r.get("10y", {}).get("med_ret", 0), r.get("22-now", {}).get("med_ret", 0), cc, c20))
safe.sort(key=lambda x: -x[1])
for name, r10, r22, cc, c20 in safe:
    print("  SAFE %-15s 10y=%.2e 22now=%.2e COVIDcrash=%.1f%% COVID2020=%.1f%%" % (name, r10, r22, cc, c20))
if not safe:
    print("  NONE collapse-safe -> broadening the router breaches COVID at every gate; keep the live tiny")
    print("  sleeve. The 10x bull prize is NOT safely capturable (gap-down-entry kills 15DTE).")
else:
    print("  -> ship the highest-10y SAFE config; that's the safely-broadened 15DTE router.")
print("\nwrote router_sweep_results.json")
