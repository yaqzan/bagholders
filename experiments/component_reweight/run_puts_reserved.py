"""PUTS reserved-cash-ledger prototype (task #14, user's 'ours'/<15 hedge request).

The handoff closed puts as a SLOT-FILLER (they share + consume the call cash buffer -> breach collapse).
The ONE untested path: a RESERVED-CASH put ledger where puts draw from a SEPARATE bucket the call
engine can't touch. Cleanest no-engine-change model: run PUTS-ONLY (deep <15, own cascade) on
standalone capital per window to get the put-bucket return, then combine POST-HOC as a fixed-fraction
reserved bucket: total = (1-f)*(1+call_ret) + f*(1+put_ret) - 1, for f in {2%, 5%}.

By construction a put loss can only hit the f-bucket -> the 95-98% call engine (collapse-safe) is
untouched -> collapse stays ~0. The real question: does the put bucket ADD value (positive/convex
return in bear/inflation windows like 2022) or just DRAG (puts are net-negative on honest scores)?
This is a FIRST-PASS no-rebalance estimate; if promising, build the rebalancing reserved ledger.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate  # noqa: E402

N = int(os.environ.get("OVF_N", "300"))
WINDOWS = ["2020_crash", "2020", "2022", "22-now", "5y", "10y"]

# PUTS-ONLY config: calls fully off, deep <=15 puts only (put_top on, mid/low off), own exposure.
PUTS_ONLY = {
    "TIER_ULTRA": 0.0, "TIER_TOP": 0.0, "TIER_MID": 0.0, "TIER_LOW": 0.0, "TIER_OVERFLOW": 0.0,
    "PUT_TIER_TOP": 0.10, "PUT_TIER_MID": 0.0, "PUT_TIER_LOW": 0.0,
    "MAX_POSITIONS": 14, "MAX_POSITIONS_CALL": 0, "MAX_POSITIONS_PUT": 14,
    "PRACTICAL_EXPOSURE_ENABLED": 1, "GROSS_PREMIUM_CAP": 0.50,
    "CALL_PREMIUM_CAP": 0.0, "PUT_PREMIUM_CAP": 0.50, "PRACTICAL_CAPITAL_CEILING": 0,
    "SLIP_ENTRY": 0.0, "SLIP_TP": 0.0, "SLIP_SL": -0.015, "SLIP_HARD": -0.015,
}

t0 = time.time()
print(">>> puts-only (deep <=15, own cascade) N=%d" % N, flush=True)
puts = run_candidate(PUTS_ONLY, n_iter=N, windows=WINDOWS, tag="puts_only")
print("    done %.0fs" % (time.time() - t0), flush=True)

# the apex+overflow CALL arm: reuse the N=500 ship-grade ovf_0.035 (the live strategy)
ship = os.path.join(_HERE, "overflow_shipgrade_results.json")
calls = {}
if os.path.exists(ship):
    sj = json.load(open(ship))
    calls = sj.get("ovf_0.035", {})

out = {"puts_only": puts, "calls_apex_ovf035": calls}
with open(os.path.join(_HERE, "puts_reserved_results.json"), "w") as f:
    json.dump(out, f, indent=2, default=str)

print("\n\n===== PUTS-ONLY (deep <=15) per window =====")
print("  %-10s %16s %9s %10s %9s" % ("window", "MedRet%", "DD%", "collapse%", "PutTP%"))
for w in WINDOWS:
    r = puts.get(w)
    if not r:
        print("  %-10s (missing)" % w); continue
    mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
    print("  %-10s %16s %8.1f%% %9.1f%% %8s" % (w, mr, r["worst_dd"], r["p_collapse"], str(r.get("put_tp", "-"))))

print("\n===== RESERVED-BUCKET COMBINATION (post-hoc, no-rebalance) =====")
print("  total = (1-f)*(1+call) + f*(1+put) - 1 ;  collapse=0 by construction (put loss bounded to f-bucket)")
print("  %-10s %14s | %14s | %14s %14s" % ("window", "call(apex)", "put(<=15)", "+2% put", "+5% put"))
for w in WINDOWS:
    c = calls.get(w, {}).get("med_ret")
    p = puts.get(w, {}).get("med_ret")
    if c is None or p is None:
        print("  %-10s  (need ship-grade call arm; run after overflow ship-grade)" % w); continue
    cr, pr = c / 100.0, p / 100.0
    for f, lab in []:
        pass
    t2 = ((1 - 0.02) * (1 + cr) + 0.02 * (1 + pr) - 1) * 100.0
    t5 = ((1 - 0.05) * (1 + cr) + 0.05 * (1 + pr) - 1) * 100.0
    fc = lambda x: ("%.1f" % x) if abs(x) < 1e6 else ("%.2e" % x)
    print("  %-10s %14s | %14s | %14s %14s" % (w, fc(c), fc(p), fc(t2), fc(t5)))

print("\nVERDICT: a reserved put bucket helps ONLY if 'put(<=15)' return is positive/convex in the")
print("stress windows (2020_crash/2022) AND the blended total beats call-only there. If puts are")
print("net-negative every window (expected on honest scores), the bucket DRAGS return -> document and")
print("recommend AGAINST for Apex (which wants max compounding, not a return-dragging hedge).")
print("wrote puts_reserved_results.json")
