"""Thread 2 PORTFOLIO confirmation — 15DTE-apex vs 30DTE-apex, clean DTE swap.

Per-trade (fifteen_dte.py) found 15DTE/WR7 has EQUAL-or-BETTER win rate but worse EV/day except in
strong-bull years (smaller premium => wide-sigma stop => -100% total-loss events; + shorter window).
The apex handoff (PROFILES_SHIP_HANDOFF.md) adds the portfolio mechanism: "velocity/fast-recycling is a
frictionless-era fiction; at real ~3% spread, high turnover IS the bleed (CUT collapsed 100%)." 15DTE =
MORE turnover => predicted strictly worse under apex cost, EXCEPT bull windows where SL rarely fires.

This is the apples-to-apples DTE swap: SAME apex config (TP+30% / wide-SL HOLD / puts-off / cascade
20-15-10-10 / MaxPos14 / gross-cap 0.50 / uncapped), only the engine differs (15DTE: premium_mult 1.29,
HOLD_DAYS=7 = ride to WR7 barrier). Compares to the 30DTE-apex arms (reused, deterministic HASHSEED=0).

GATE: is there ANY window where a 15DTE arm beats 30DTE on MedRet at collapse=0? (the user's question)
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
import driver  # noqa: E402
from driver import run_candidate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(_HERE))
driver.MC = os.path.join(ROOT, "monte_carlo_15dte.py")   # point the driver at the 15DTE engine

N = int(os.environ.get("OVF_N", "300"))
WINDOWS = ["2020_crash", "2022", "2024", "2025", "22-now", "5y", "10y"]

# apex base, forced onto the 15DTE engine (so the ONLY difference vs 30DTE is the DTE/premium/hold)
APEX = {
    "TP_BASE": 0.30, "TP_STRESS": 0.30,
    "TIER_ULTRA": 0.20, "TIER_TOP": 0.15, "TIER_MID": 0.10, "TIER_LOW": 0.10,
    "PUT_TIER_TOP": 0.0, "PUT_TIER_MID": 0.0, "PUT_TIER_LOW": 0.0,
    "MAX_POSITIONS": 14,
    "PRACTICAL_EXPOSURE_ENABLED": 1, "GROSS_PREMIUM_CAP": 0.50,
    "CALL_PREMIUM_CAP": 0.50, "PUT_PREMIUM_CAP": 0.0, "PRACTICAL_CAPITAL_CEILING": 0,
    "DD_SOFT_BAND_LO": 0.35, "DD_SOFT_BAND_HI": 0.55, "DD_SOFT_CALL_FLOOR": 0.40,
    "SLIP_ENTRY": 0.0, "SLIP_TP": 0.0, "SLIP_SL": -0.015, "SLIP_HARD": -0.015,
}
CANDS = [
    ("dte15_sl70_noovf",  {**APEX, "SL_BASE": -0.70, "SL_STRESS": -0.70, "TIER_OVERFLOW": 0.0}),
    ("dte15_sl70_ovf035", {**APEX, "SL_BASE": -0.70, "SL_STRESS": -0.70, "TIER_OVERFLOW": 0.035}),
    ("dte15_sl100_ovf035", {**APEX, "SL_BASE": -1.00, "SL_STRESS": -1.00, "TIER_OVERFLOW": 0.035}),
]

results = {}
# reuse the 30DTE arms (deterministic) for the comparison
edge = os.path.join(_HERE, "overflow_edge_results.json")
if os.path.exists(edge):
    ej = json.load(open(edge))
    if ej.get("baseline"):
        results["dte30_baseline"] = ej["baseline"]
    if ej.get("ovf_0.035"):
        results["dte30_ovf035"] = ej["ovf_0.035"]

for name, params in CANDS:
    t0 = time.time()
    print("\n>>> %s (N=%d) %s" % (name, N, {k: params[k] for k in ("SL_BASE", "TIER_OVERFLOW")}), flush=True)
    try:
        results[name] = run_candidate(params, n_iter=N, windows=WINDOWS, tag="dte15_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}

with open(os.path.join(_HERE, "dte15_apex_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = ["dte30_baseline", "dte30_ovf035", "dte15_sl70_noovf", "dte15_sl70_ovf035", "dte15_sl100_ovf035"]
print("\n\n===== 15DTE-apex vs 30DTE-apex (N=%d, apex canon, calls-only) =====" % N)
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-18s %16s %10s %11s %9s" % ("config", "MedRet%", "WorstDD%", "collapse%", "CallTrd"))
    best30 = results.get("dte30_ovf035", {}).get(w, {}).get("med_ret")
    for name in order:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-18s   (missing)" % name); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.3e" % r["med_ret"])
        win = ""
        if name.startswith("dte15") and best30 is not None and r["med_ret"] > best30 and r["p_collapse"] < 1.0:
            win = "  <-- 15DTE BEATS 30DTE-ovf"
        print("  %-18s %16s %9.1f%% %10.1f%% %9s%s" % (
            name, mr, r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0))), win))
print("\nwrote dte15_apex_results.json")
print("Read: any '<-- 15DTE BEATS' flags = a window where the user's WR7/15DTE thesis holds at portfolio")
print("scale. If none (predicted: 15DTE worse everywhere under apex cost / turnover-bleed), the answer to")
print("'why astronomically worse' = smaller premium's wide-sigma stop + shorter window + spread-on-turnover.")
