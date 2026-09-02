"""Recover the 15DTE-apex MC numbers from the child logs (#5's driver parse failed on the 15DTE
engine's 3-table SUMMARY + its WINDOWS list lacking 2020_crash/10y). The compute is done; the
per-window 'seeded' lines hold MedRet/WorstDD/collapse. Parse them and compare to the 30DTE arms.
"""
import os, sys, re, json, glob
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
LOGDIR = os.path.join(_V69, "_childlogs")
sys.path.insert(0, _V69)
from driver import SEEDED_RE, WINDOW_RE, _f  # reuse the 9-group seeded regex

def parse_log(path):
    out, cur = {}, None
    for line in open(path, encoding="utf-8", errors="replace"):
        wm = WINDOW_RE.match(line)
        if wm:
            cur = wm.group(1); continue
        sm = SEEDED_RE.match(line)
        if sm and cur:
            out[cur] = dict(call_tp=_f(sm.group(1)), call_trades=_f(sm.group(3)),
                            mean_ret=_f(sm.group(5)), med_ret=_f(sm.group(6)),
                            worst_dd=_f(sm.group(7)), p_collapse=_f(sm.group(9)))
            cur = None
    return out

logs = {
    "dte15_sl70_noovf":  os.path.join(LOGDIR, "dte15_dte15_sl70_noovf.log"),
    "dte15_sl70_ovf035": os.path.join(LOGDIR, "dte15_dte15_sl70_ovf035.log"),
    "dte15_sl100_ovf035": os.path.join(LOGDIR, "dte15_dte15_sl100_ovf035.log"),
}
res = {}
for name, p in logs.items():
    if os.path.exists(p):
        res[name] = parse_log(p)
        print("parsed %-20s windows=%s" % (name, sorted(res[name].keys())))
    else:
        print("MISSING", name)

# pull the 30DTE arms (apex) from the cliff sweep for comparison
edge = json.load(open(os.path.join(_HERE, "overflow_edge_results.json")))
res["dte30_baseline"] = edge.get("baseline", {})
res["dte30_ovf035"] = edge.get("ovf_0.035", {})

WINS = ["2022", "2024", "2025", "22-now", "5y"]   # what the 15DTE engine actually ran
order = ["dte30_baseline", "dte30_ovf035", "dte15_sl70_noovf", "dte15_sl70_ovf035", "dte15_sl100_ovf035"]
print("\n===== 15DTE-apex vs 30DTE-apex (recovered; calls-only, apex barriers) =====")
print("NOTE: 15DTE engine lacks 2020_crash & 10y windows -> COVID-collapse & 10y NOT testable here.")
for w in WINS:
    print("\n--- %s ---" % w)
    print("  %-18s %15s %9s %10s %8s" % ("config", "MedRet%", "DD%", "collapse%", "CallTrd"))
    for n in order:
        r = res.get(n, {}).get(w)
        if not r:
            print("  %-18s (n/a)" % n); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
        print("  %-18s %15s %8.1f%% %9.1f%% %8s" % (
            n, mr, r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0)))))

with open(os.path.join(_HERE, "dte15_apex_recovered.json"), "w") as f:
    json.dump(res, f, indent=2, default=str)
print("\nwrote dte15_apex_recovered.json")
