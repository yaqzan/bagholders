"""EDGE-ON-EDGE: does the honest WEEKLY-MATURITY filter improve the OVERFLOW book?

Plain overflow (run_overflow_edge.py): collapse-safe up to alloc 0.040; 0.045 breaches COVID
(1.3%). Return-optimal safe alloc ~0.035 (10y +35.8M%). The filter drops the sub-break-even
mature-weekly 70-74 tail (wadj_completed top tercile, 66.5% apex WR, z=-4.29, HONEST/dow-stable).
Hypothesis: dropping that tail = fewer simultaneous sub-BE losers in a crash => lower COVID
collapse / DD at equal alloc, OR a higher collapse-safe alloc ceiling => more return.

Compares (N=300, collapse-critical + return windows):
  plain   0.035 / 0.040 / 0.045   (REUSED from overflow_edge_results.json, deterministic HASHSEED=0)
  FILTER  0.035 / 0.040 / 0.045   (fresh; WEEKLY_OVF_FILTER=1 via parent env)
Decision: filter EARNS its place if it (a) keeps COVID collapse=0 at alloc>=0.045 (raises the
cliff) OR (b) lowers DD/collapse at equal alloc with >= return. Else it's a wash -> document.
"""
import os, sys, json, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_V69 = os.path.abspath(os.path.join(_HERE, "..", "v69_portfolio_retune"))
if _V69 not in sys.path:
    sys.path.insert(0, _V69)
from driver import run_candidate  # noqa: E402

N = int(os.environ.get("OVF_N", "300"))
WINDOWS = ["2020_crash", "2022", "22-now", "5y", "10y"]
WCOMP_MAX = os.environ.get("WEEKLY_OVF_WCOMP_MAX", "8.4")
ALLOCS = [0.035, 0.040, 0.045]

results = {}
# reuse deterministic PLAIN baselines from the cliff sweep
edge = os.path.join(_HERE, "overflow_edge_results.json")
if os.path.exists(edge):
    ej = json.load(open(edge))
    for a in ALLOCS:
        k = "ovf_%.3f" % a
        if ej.get(k):
            results["plain_%.3f" % a] = ej[k]
    if ej.get("baseline"):
        results["baseline"] = ej["baseline"]
    print("reused plain baselines:", [k for k in results], flush=True)


def set_filter(on):
    if on:
        os.environ["WEEKLY_OVF_FILTER"] = "1"
        os.environ["WEEKLY_OVF_WCOMP_MAX"] = str(WCOMP_MAX)
    else:
        os.environ.pop("WEEKLY_OVF_FILTER", None)


# fresh FILTERED runs
for a in ALLOCS:
    name = "filt_%.3f" % a
    set_filter(True)
    t0 = time.time()
    print("\n>>> %s (N=%d, WEEKLY_OVF_FILTER=1 wcomp>%s)" % (name, N, WCOMP_MAX), flush=True)
    try:
        results[name] = run_candidate({"TIER_OVERFLOW": a}, n_iter=N, windows=WINDOWS, tag="ovfwk_" + name)
        print("    done %.0fs" % (time.time() - t0), flush=True)
    except Exception as e:
        print("    FAILED:", e, flush=True)
        results[name] = {}
    finally:
        set_filter(False)

with open(os.path.join(_HERE, "overflow_weekly_results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)

order = (["baseline"] if "baseline" in results else [])
for a in ALLOCS:
    order += ["plain_%.3f" % a, "filt_%.3f" % a]
print("\n\n===== WEEKLY-FILTERED vs PLAIN OVERFLOW (N=%d, apex canon) =====" % N)
print("GATE: COVID(2020_crash) collapse must stay ~0. Return is prize; DD is budget.")
for w in WINDOWS:
    print("\n--- %s ---" % w)
    print("  %-13s %16s %10s %11s %9s" % ("config", "MedRet%", "WorstDD%", "collapse%", "CallTrd"))
    for name in order:
        r = results.get(name, {}).get(w)
        if not r:
            print("  %-13s   (missing)" % name); continue
        mr = ("%.1f" % r["med_ret"]) if abs(r["med_ret"]) < 1e6 else ("%.3e" % r["med_ret"])
        print("  %-13s %16s %9.1f%% %10.1f%% %9s" % (
            name, mr, r["worst_dd"], r["p_collapse"], str(int(r.get("call_trades", 0)))))

# explicit plain-vs-filter delta at each alloc
print("\n=== filter EFFECT at equal alloc (filt - plain) ===")
for a in ALLOCS:
    p = results.get("plain_%.3f" % a, {})
    fl = results.get("filt_%.3f" % a, {})
    if not p or not fl:
        continue
    print("  alloc %.3f:" % a)
    for w in WINDOWS:
        pr, fr = p.get(w), fl.get(w)
        if not pr or not fr:
            continue
        d_col = fr["p_collapse"] - pr["p_collapse"]
        d_dd = fr["worst_dd"] - pr["worst_dd"]
        print("     %-11s  d_collapse=%+.1fpp  d_DD=%+.1fpp  d_CallTrd=%+d" % (
            w, d_col, d_dd, int(fr.get("call_trades", 0)) - int(pr.get("call_trades", 0))))
print("\nwrote overflow_weekly_results.json")
