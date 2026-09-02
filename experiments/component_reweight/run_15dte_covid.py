"""15DTE-apex on the COLLAPSE-BINDING windows the engine previously lacked (now added to its WINDOWS
list): 2020_crash, 2020, 10y. Robust parse of the engine's per-window 'seeded' lines (the driver's
SUMMARY-table parse breaks on the 15DTE engine's 3-table format). Answers the router-gating question:
does pure 15DTE-apex BREACH the collapse floor in COVID / over 10y? (recovered bull-window data showed
15DTE wins return on 22-now/5y but with +8.7pp worse 5y DD -> COVID is the decider.)
"""
import os, sys, json, time, subprocess, re
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
sys.path.insert(0, _V69)
from driver import SEEDED_RE, WINDOW_RE, _f  # 9-group seeded regex + window header
ROOT = os.path.dirname(os.path.dirname(_HERE))
MC15 = os.path.join(ROOT, "monte_carlo_15dte.py")

N = int(os.environ.get("OVF_N", "300"))
WINDOWS = ["2020_crash", "2020", "10y"]
APEX = {
    "TP_BASE_OV": "0.30", "TP_STRESS_OV": "0.30",
    "TIER_ULTRA_OV": "0.20", "TIER_TOP_OV": "0.15", "TIER_MID_OV": "0.10", "TIER_LOW_OV": "0.10",
    "PUT_TIER_TOP_OV": "0.0", "PUT_TIER_MID_OV": "0.0", "PUT_TIER_LOW_OV": "0.0",
    "MAX_POSITIONS_OVERRIDE": "14",
    "PRACTICAL_EXPOSURE_ENABLED": "1", "GROSS_PREMIUM_CAP": "0.50",
    "CALL_PREMIUM_CAP": "0.50", "PUT_PREMIUM_CAP": "0.0", "PRACTICAL_CAPITAL_CEILING": "0",
    "DD_SOFT_BAND_LO": "0.35", "DD_SOFT_BAND_HI": "0.55", "DD_SOFT_CALL_FLOOR": "0.40",
    "SLIP_ENTRY_OV": "0.0", "SLIP_TP_OV": "0.0", "SLIP_SL_OV": "-0.015", "SLIP_HARD_OV": "-0.015",
}
CANDS = [
    ("dte15_sl70_noovf",  {"SL_BASE_OV": "-0.70", "SL_STRESS_OV": "-0.70", "TIER_OVERFLOW_OV": "0.0"}),
    ("dte15_sl70_ovf035", {"SL_BASE_OV": "-0.70", "SL_STRESS_OV": "-0.70", "TIER_OVERFLOW_OV": "0.035"}),
    ("dte15_sl100_ovf035", {"SL_BASE_OV": "-1.00", "SL_STRESS_OV": "-1.00", "TIER_OVERFLOW_OV": "0.035"}),
]


def run(name, extra):
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONHASHSEED": "0",
                "MC_NO_DB_PERSIST": "1", "N_ITER_OVERRIDE": str(N), "WINDOWS_OVERRIDE": ",".join(WINDOWS)})
    env.update(APEX); env.update(extra)
    t0 = time.time()
    print("\n>>> %s (N=%d) %s" % (name, N, {k: extra[k] for k in ("SL_BASE_OV", "TIER_OVERFLOW_OV")}), flush=True)
    proc = subprocess.Popen([sys.executable, "-u", MC15], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    out, cur, res = [], None, {}
    for line in proc.stdout:
        out.append(line)
        wm = WINDOW_RE.match(line)
        if wm:
            cur = wm.group(1); continue
        sm = SEEDED_RE.match(line)
        if sm and cur:
            res[cur] = dict(call_tp=_f(sm.group(1)), call_trades=_f(sm.group(3)),
                            med_ret=_f(sm.group(6)), worst_dd=_f(sm.group(7)), p_collapse=_f(sm.group(9)))
            cur = None
    proc.wait(timeout=7200)
    print("    done %.0fs  parsed=%s" % (time.time() - t0, sorted(res.keys())), flush=True)
    if proc.returncode != 0:
        sys.stderr.write("".join(out[-40:]))
    return res


results = {}
for name, extra in CANDS:
    results[name] = run(name, extra)

# pull 30DTE arms for the same windows from the cliff sweep
edge = json.load(open(os.path.join(_HERE, "overflow_edge_results.json")))
results["dte30_baseline"] = {w: edge.get("baseline", {}).get(w) for w in WINDOWS}
results["dte30_ovf035"] = {w: edge.get("ovf_0.035", {}).get(w) for w in WINDOWS}

with open(os.path.join(_HERE, "dte15_covid_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = ["dte30_baseline", "dte30_ovf035", "dte15_sl70_noovf", "dte15_sl70_ovf035", "dte15_sl100_ovf035"]
print("\n\n===== 15DTE-apex on COLLAPSE-BINDING windows (N=%d) =====" % N)
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-18s %15s %9s %10s %8s" % ("config", "MedRet%", "DD%", "collapse%", "CallTrd"))
    for n in order:
        r = results.get(n, {}).get(w)
        if not r:
            print("  %-18s (n/a)" % n); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
        print("  %-18s %15s %8.1f%% %9.1f%% %8s" % (
            n, mr, r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0)))))
print("\nDECIDER: if any 15DTE arm has 2020_crash collapse > ~0.3% (baseline) => pure 15DTE breaches the")
print("Apex floor => a regime-aware DTE ROUTER (15DTE in bull, 30DTE in crash) is MANDATORY, not optional.")
print("wrote dte15_covid_results.json")
