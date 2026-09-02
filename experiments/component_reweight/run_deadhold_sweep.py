"""Dead-hold / deep-loss-tail DD optimization (user request + 7-SL audit follow-up).

AUDIT FINDING: the -70% SL never executes (sl=7) — every SL touch is intercepted by the dead-hold
(pnl<=-50% -> hold for recovery). It saves 546 (dh_pop, mean -20.7%) but rides 990 to EXPIRY at
mean -90% (worse than the -70% SL it bypassed). That -90% dh_expiry cluster is a real DD/crash driver
(14.2% of all trades lose >=70%). Theta IS modeled (option_pnl_pct), DD is honest — no deceptive bug.

GOAL: drill down DD by capping that deep-loss tail WITHOUT killing returns (the dead-hold is only
marginally +EV per-trade: ~-65% avg vs -70% clean SL). Candidates (apex+overflow):
  baseline      dead-hold on (trigger -50, popout -25)
  dh_off        clean -70% SL, no gamble (caps every loss at ~-70%, kills the -90% tail)
  dh_off_prem75 no dead-hold + premium stop -75% (exit when option bleeds to -75%, theta or move)
  dh_off_prem80 premium stop -80%
  dh_popout15   dead-hold on but easier recovery target (-15% -> more dh_pop, fewer -90% expiries)
  dh_trig40_pop15  trigger earlier (-40%) + easier popout
GATE: lower 10y/5y/2023 WorstDD at collapse=0, with 10y return not gutted.
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

#  name              dh_enabled trigger popout prem_stop
CANDS = [
    ("baseline",         "1", -0.50, -0.25, 0.0),
    ("dh_off",           "0", -0.50, -0.25, 0.0),    # clean -70% SL, no -90% tail
    ("dh_off_prem75",    "0", -0.50, -0.25, -0.75),  # premium stop caps loss at -75%
    ("dh_off_prem80",    "0", -0.50, -0.25, -0.80),
    ("dh_popout15",      "1", -0.50, -0.15, 0.0),    # easier recovery -> fewer -90% expiries
    ("dh_trig40_pop15",  "1", -0.40, -0.15, 0.0),
]


def set_dh(enabled, trig, pop, prem):
    os.environ["DEAD_HOLD_ENABLED"] = enabled
    os.environ["DEAD_HOLD_TRIGGER_PNL"] = str(trig)
    os.environ["DEAD_HOLD_POPOUT_PNL"] = str(pop)
    os.environ["PREM_STOP_LOSS"] = str(prem)


results = {}
for name, en, trig, pop, prem in CANDS:
    set_dh(en, trig, pop, prem)
    t0 = time.time()
    print("\n>>> %s (dh=%s trig=%.2f pop=%.2f prem=%.2f)" % (name, en, trig, pop, prem), flush=True)
    try:
        results[name] = run_candidate({"TIER_OVERFLOW": OVF}, n_iter=N, windows=WINDOWS, tag="dh_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}
    finally:
        for k in ("DEAD_HOLD_ENABLED", "DEAD_HOLD_TRIGGER_PNL", "DEAD_HOLD_POPOUT_PNL", "PREM_STOP_LOSS"):
            os.environ.pop(k, None)

with open(os.path.join(_HERE, "deadhold_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = [c[0] for c in CANDS]
print("\n\n===== DEAD-HOLD / DEEP-LOSS-TAIL DD SWEEP (apex+overflow, N=%d) =====" % N)
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-16s %16s %9s %10s %9s" % ("config", "MedRet%", "DD%", "collapse%", "CallTP%"))
    for name in order:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-16s (missing)" % name); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
        print("  %-16s %16s %8.1f%% %9.1f%% %8s" % (name, mr, r["worst_dd"], r["p_collapse"], str(r.get("call_tp", "-"))))

b = results.get("baseline", {})
print("\n=== deltas vs baseline (goal: lower DD at collapse=0 without gutting return) ===")
for name in order[1:]:
    r = results.get(name, {})
    if not r or not b:
        continue
    d5 = r.get("5y", {}).get("worst_dd", 0) - b.get("5y", {}).get("worst_dd", 0)
    d10 = r.get("10y", {}).get("worst_dd", 0) - b.get("10y", {}).get("worst_dd", 0)
    dcov = r.get("2020_crash", {}).get("worst_dd", 0) - b.get("2020_crash", {}).get("worst_dd", 0)
    r10, b10 = r.get("10y", {}).get("med_ret", 0), b.get("10y", {}).get("med_ret", 0)
    worst = max((r.get(w, {}).get("p_collapse", 99) for w in WINDOWS), default=99)
    print("  %-16s dDD: 5y%+.1f 10y%+.1f COVID%+.1f pp | 10yRet x%.2f | collapse %s" % (
        name, d5, d10, dcov, (r10 / b10 if b10 else 0), "SAFE" if worst <= 0.3 else "BREACH(%.1f)" % worst))
print("\nwrote deadhold_results.json")
