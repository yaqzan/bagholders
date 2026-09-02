"""Note 2 (regime-knob crash-protection) + Note 1 (hard-sell timing) VALIDATION, on apex+overflow.

Regime analysis (run_regime_analysis.py on the workflow dataset) found Apex's forward-20d return turns
NEGATIVE in: breadth<30 (-0.086, the strongest loss signal; worst trough Oct-2023 was a weak-breadth
pullback) and VIX 25-30 (-0.017). The F3F breadth knob is the right mechanism but TOO SHALLOW — it
floors at 0.50, still deploying half-size into the breadth<30 bleed zone. DEEPEN it.

Hard-sell (Note 1): the day-15 cohort is 54% eventual-TP recoverers, but fresh signals win ~68% — so
cut+recycle COULD beat hold IF hydrated (overflow helps). Test HOLD_DAYS 7/10 vs 15.

GOAL: F3F candidates should LOWER 10y/5y WorstDD (and the 2023 trough) WITHOUT gutting 10y return,
collapse=0. Hard-sell candidates: does earlier forced-exit + recycle beat hold-to-15? N=300.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate  # noqa: E402

N = int(os.environ.get("OVF_N", "300"))
WINDOWS = ["2020_crash", "2020", "2022", "2023", "22-now", "5y", "10y"]
OVF = 0.035

# F3F set via os.environ (monte_carlo reads F3F_CALL_FLOOR/LOW/THRESH from env). HOLD_DAYS via params.
#  name                 f3f_floor f3f_low hold_days
CANDS = [
    ("baseline",            0.50, 30, 15),
    ("f3f_floor30",         0.30, 30, 15),   # contract harder below breadth 30 (the loss zone)
    ("f3f_floor30_low40",   0.30, 40, 15),   # + start contracting at breadth 40 (the weak 30-45 zone)
    ("f3f_floor25_low45",   0.25, 45, 15),   # aggressive breadth contraction
    ("hardsell_7",          0.50, 30, 7),    # Note 1: cut unresolved cohort at day 7 + recycle
    ("hardsell_10",         0.50, 30, 10),
    ("f3f30_low40_hs10",    0.30, 40, 10),   # combined: deeper breadth contraction + day-10 hard sell
]


def set_f3f(floor, low):
    os.environ["F3F_CALL_FLOOR"] = str(floor)
    os.environ["F3F_CALL_LOW"] = str(low)
    os.environ["F3F_CALL_THRESH"] = "50"


results = {}
for name, floor, low, hold in CANDS:
    set_f3f(floor, low)
    t0 = time.time()
    print("\n>>> %s (f3f_floor=%.2f low=%d hold=%d)" % (name, floor, low, hold), flush=True)
    try:
        results[name] = run_candidate({"TIER_OVERFLOW": OVF, "HOLD_DAYS": hold},
                                      n_iter=N, windows=WINDOWS, tag="rk_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}
    finally:
        os.environ.pop("F3F_CALL_FLOOR", None); os.environ.pop("F3F_CALL_LOW", None); os.environ.pop("F3F_CALL_THRESH", None)

with open(os.path.join(_HERE, "regime_knob_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = [c[0] for c in CANDS]
print("\n\n===== REGIME-KNOB + HARD-SELL SWEEP (apex+overflow, N=%d) =====" % N)
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-18s %16s %9s %10s" % ("config", "MedRet%", "DD%", "collapse%"))
    for name in order:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-18s (missing)" % name); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
        print("  %-18s %16s %8.1f%% %9.1f%%" % (name, mr, r["worst_dd"], r["p_collapse"]))

b = results.get("baseline", {})
print("\n=== deltas vs baseline (the goal: F3F lowers DD; hard-sell tests recycle) ===")
for name in order[1:]:
    r = results.get(name, {})
    if not r or not b:
        continue
    d5 = r.get("5y", {}).get("worst_dd", 0) - b.get("5y", {}).get("worst_dd", 0)
    d10 = r.get("10y", {}).get("worst_dd", 0) - b.get("10y", {}).get("worst_dd", 0)
    d23 = r.get("2023", {}).get("worst_dd", 0) - b.get("2023", {}).get("worst_dd", 0)
    r10 = r.get("10y", {}).get("med_ret", 0); b10 = b.get("10y", {}).get("med_ret", 0)
    ret = "HELP" if r10 > b10 else "HURT"
    worst = max((r.get(w, {}).get("p_collapse", 99) for w in WINDOWS), default=99)
    print("  %-18s dDD: 5y%+.1fpp 10y%+.1fpp 2023%+.1fpp | 10yRet %s | collapse %s" % (
        name, d5, d10, d23, ret, "SAFE" if worst <= 0.3 else "BREACH(%.1f)" % worst))
print("\nwrote regime_knob_results.json")
