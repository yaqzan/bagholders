"""15DTE ROUTER fine DAY_CAP gap-filler. The broadening sweep (run_router_sweep.py) tested
1 -> 5 -> 10 -> 14 and found DAY_CAP=1 strictly dominant (only collapse-safe AND highest return),
but it NEVER tested 2 or 3 at the LIVE eligibility. This isolates the fine margin between 1 and 5.

Q: at the live gate (score>=80, trend<50), is DAY_CAP=2 or 3 a SAFE Pareto improvement over 1,
   or does it also trip the 2020-COVID collapse floor? Test with and without the VIX<=28 crash-gate.

Runs on the apex+overflow base (TIER_OVERFLOW=0.035), honest v70, asymmetric cost. N=300.
GATE: collapse<=0.3% on 2020_crash AND 2020 (hard Apex floor). Among safe, maximize 10y / 22-now.
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

# Fine DAY_CAP axis at the LIVE eligibility (score>=80, trend<50). VIX_MAX axis: off vs 28.
#  name              day_cap score_min trend_lt vix_max
CANDS = [
    ("cap1_nogate",        1,  80, 50.0,  0.0),   # current live router (baseline)
    ("cap2_nogate",        2,  80, 50.0,  0.0),
    ("cap3_nogate",        3,  80, 50.0,  0.0),
    ("cap1_vix28",         1,  80, 50.0, 28.0),
    ("cap2_vix28",         2,  80, 50.0, 28.0),
    ("cap3_vix28",         3,  80, 50.0, 28.0),
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
        results[name] = run_candidate({"TIER_OVERFLOW": OVERFLOW}, n_iter=N, windows=WINDOWS, tag="daycap_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}

with open(os.path.join(_HERE, "router_daycap_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = [c[0] for c in CANDS]
print("\n\n===== 15DTE ROUTER DAY_CAP gap-filler (apex+overflow %.3f, N=%d) =====" % (OVERFLOW, N))
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-15s %16s %9s %10s %9s" % ("config", "MedRet%", "DD%", "collapse%", "callTr"))
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
base = results.get("cap1_nogate", {})
b10 = base.get("10y", {}).get("med_ret", 0); b22 = base.get("22-now", {}).get("med_ret", 0)
print("\n  baseline cap1_nogate: 10y=%.2e  22now=%.2e" % (b10, b22))
print("  -> SHIP a higher cap ONLY if collapse-safe AND beats cap1 on 10y/22-now; else 1/day stays.")
print("\nwrote router_daycap_results.json")
