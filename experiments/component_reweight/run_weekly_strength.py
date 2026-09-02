"""Weekly-filter STRENGTH sweep — is there a collapse-safe filter that still beats plain overflow?

MC #4 finding: full-tercile weekly filter (drop wadj_completed>8.4 in 70-74) ~DOUBLES 5y/10y return
(concentrates overflow into higher-WR low-weekly calls) BUT breaches the COVID collapse floor
(2020_crash 0.0%->2.0%) -- dropping ~34 crash-window positions removes diversification/cash-buffer.
This is the return-vs-survival dial. Apex floor = collapse must be ~0 (<=0.3% = baseline).

Question: a MILDER filter (higher wcomp cut = drop fewer, only the most-extreme mature-weekly) may
keep enough diversification to stay collapse-safe while still capturing some of the 2x return boost.
Sweep wcomp cut 8.4(tercile)/11/14/16(~decile->5%) at alloc 0.035; COVID + return windows; N=400 for
the collapse tail. Decision: the highest-return config with 2020_crash collapse <= 0.3%.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate  # noqa: E402

N = int(os.environ.get("OVF_N", "400"))
WINDOWS = ["2020_crash", "2020", "5y", "10y"]
ALLOC = 0.035
CUTS = [11.0, 14.0, 16.0]   # milder than the 8.4 tercile (drop fewer, most-extreme only)

results = {}
# reuse plain_0.035 and filt(8.4)_0.035 baselines (NOTE: those were N=300; re-run plain@N=400 for a
# clean collapse-tail comparison since collapse is rare-event sensitive to N).
def set_filter(on, cut=None):
    if on:
        os.environ["WEEKLY_OVF_FILTER"] = "1"
        os.environ["WEEKLY_OVF_WCOMP_MAX"] = str(cut)
    else:
        os.environ.pop("WEEKLY_OVF_FILTER", None)
        os.environ.pop("WEEKLY_OVF_WCOMP_MAX", None)

def run(name, cut):
    t0 = time.time()
    print("\n>>> %s (N=%d, cut=%s)" % (name, N, cut), flush=True)
    set_filter(cut is not None, cut)
    try:
        results[name] = run_candidate({"TIER_OVERFLOW": ALLOC}, n_iter=N, windows=WINDOWS, tag="wkstr_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}
    finally:
        set_filter(False)

run("plain", None)                  # plain overflow @0.035, N=400 (clean collapse-tail baseline)
run("filt_8.4", 8.4)                # full tercile (the MC#4 breaching config), re-confirm at N=400
for c in CUTS:
    run("filt_%.0f" % c, c)

with open(os.path.join(_HERE, "weekly_strength_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = ["plain", "filt_8.4"] + ["filt_%.0f" % c for c in CUTS]
print("\n\n===== WEEKLY-FILTER STRENGTH @ alloc=%.3f (N=%d) =====" % (ALLOC, N))
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-10s %16s %9s %10s %8s" % ("config", "MedRet%", "DD%", "collapse%", "CallTrd"))
    for n in order:
        r = results.get(n, {}).get(w)
        if not r:
            print("  %-10s (missing)" % n); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.2e" % r["med_ret"])
        print("  %-10s %16s %8.1f%% %9.1f%% %8s" % (
            n, mr, r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0)))))

print("\n=== collapse-safe (<=0.3% on 2020_crash) ranked by 10y return ===")
safe = []
for n in order:
    r = results.get(n, {})
    cc = r.get("2020_crash", {}).get("p_collapse", 99)
    if cc <= 0.3:
        safe.append((n, r.get("10y", {}).get("med_ret", 0), r.get("5y", {}).get("med_ret", 0), cc))
safe.sort(key=lambda x: -x[1])
for n, r10, r5, cc in safe:
    print("  SAFE %-10s 10y=%.2e%%  5y=%.1f%%  COVIDcollapse=%.1f%%" % (n, r10, r5, cc))
if not safe:
    print("  NONE collapse-safe -> weekly filter is a return-vs-survival dial that breaches the Apex")
    print("  floor at every strength; plain overflow @0.035 stands as the validated edge.")
print("\nwrote weekly_strength_results.json")
