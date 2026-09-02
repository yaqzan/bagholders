"""N=500 ship-grade validation of the dead-hold popout win (Pareto: +return AND -DD at N=300).
DEAD_HOLD_POPOUT_PNL -0.25 -> -0.15 (+ TRIGGER -0.50 -> -0.40) captured ~2x 10y return AND -1.8pp 10y DD,
collapse=0, by letting dead-hold recoverers run to a better exit (dh_pop recoveries reach +357%). A
Pareto win this size needs N=500 scrutiny before wiring. Also test stacking F3F-floor30 (COVID crash
insurance) on top. baseline reused-style at N=500 for a clean compare.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate  # noqa: E402

N = int(os.environ.get("OVF_N", "500"))
WINDOWS = ["2020_crash", "2020", "2022", "2024", "2025", "dip", "22-now", "5y", "10y"]
OVF = 0.035

#  name                  trig   popout  f3f_floor f3f_low
CANDS = [
    ("baseline",          -0.50, -0.25,  0.50, 30),
    ("dh_trig40_pop15",   -0.40, -0.15,  0.50, 30),
    ("dh_pop15_f3f30",    -0.40, -0.15,  0.30, 40),   # stack: dead-hold win + crash insurance
]


def setenv(trig, pop, floor, low):
    os.environ["DEAD_HOLD_ENABLED"] = "1"
    os.environ["DEAD_HOLD_TRIGGER_PNL"] = str(trig)
    os.environ["DEAD_HOLD_POPOUT_PNL"] = str(pop)
    os.environ["F3F_CALL_FLOOR"] = str(floor)
    os.environ["F3F_CALL_LOW"] = str(low)
    os.environ["F3F_CALL_THRESH"] = "50"


results = {}
for name, trig, pop, floor, low in CANDS:
    setenv(trig, pop, floor, low)
    t0 = time.time()
    print("\n>>> %s (trig=%.2f pop=%.2f f3f=%.2f/%d) N=%d" % (name, trig, pop, floor, low, N), flush=True)
    try:
        results[name] = run_candidate({"TIER_OVERFLOW": OVF}, n_iter=N, windows=WINDOWS, tag="dhship_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}
    finally:
        for k in ("DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL", "F3F_CALL_FLOOR", "F3F_CALL_LOW", "F3F_CALL_THRESH"):
            os.environ.pop(k, None)

with open(os.path.join(_HERE, "deadhold_ship_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = [c[0] for c in CANDS]
print("\n\n===== DEAD-HOLD SHIP-GRADE (N=%d x %d windows, apex+overflow) =====" % (N, len(WINDOWS)))
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-16s %16s %9s %10s" % ("config", "MedRet%", "DD%", "collapse%"))
    for name in order:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-16s (missing)" % name); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
        print("  %-16s %16s %8.1f%% %9.1f%%" % (name, mr, r["worst_dd"], r["p_collapse"]))

b = results.get("baseline", {})
print("\n=== deltas vs baseline (target: +return AND -DD at collapse=0) ===")
for name in order[1:]:
    r = results.get(name, {})
    if not r or not b:
        continue
    rows = []
    for w in ["5y", "10y", "22-now", "2020_crash"]:
        dd = r.get(w, {}).get("worst_dd", 0) - b.get(w, {}).get("worst_dd", 0)
        rr = r.get(w, {}).get("med_ret", 0); bb = b.get(w, {}).get("med_ret", 0)
        rows.append("%s dDD%+.1f ret_x%.2f" % (w, dd, (rr / bb if bb else 0)))
    worst = max((r.get(w, {}).get("p_collapse", 99) for w in WINDOWS), default=99)
    print("  %-16s %s | collapse %s" % (name, " | ".join(rows), "SAFE" if worst <= 0.3 else "BREACH(%.1f)" % worst))
print("\nwrote deadhold_ship_results.json")
