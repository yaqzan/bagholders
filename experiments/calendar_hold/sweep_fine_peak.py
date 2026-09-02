"""Fine hold-window peak search around 27 (honest theta, 30 DTE Apex).

#92 tested {21, 27, 30} and found 27 > 21 and 27 > 30 — but never sampled the
immediate neighbors, so the true local optimum in (25, 30) is unresolved (could be
28/29). #93's per-trade D30 curve only went to W25. This brackets the peak finely.

Crucially, ranks by REALISTIC popout cost (pop03 = -3% half-spread on dead-hold
popouts), because longer holds lean more on near-expiry popout fills (#92: h30
took a -38% popout haircut vs h27 -30%) — so the pop03 peak can sit SHORTER than
the free-fill (pop00) peak. We report both and decide on pop03.

    python experiments/calendar_hold/sweep_fine_peak.py
    HOLDS=24,25,26,27,28,29 N_ITER=300 python experiments/calendar_hold/sweep_fine_peak.py
"""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results_fine"
RESULTS.mkdir(exist_ok=True)

N_ITER = os.environ.get("N_ITER", "300")
WINDOWS = os.environ.get("WINDOWS", "2022,dip,22-now,5y,2020_crash")
HOLDS = [int(x) for x in os.environ.get("HOLDS", "24,25,26,27,28,29").split(",")]
RK = "med_ret"
HEADLINE = ["5y", "22-now"]

VARIANTS = [("A_bar", "0", "21", "0.0")]
for h in HOLDS:
    VARIANTS.append((f"h{h}_pop00", "1", str(h), "0.0"))
    VARIANTS.append((f"h{h}_pop03", "1", str(h), "-0.03"))


def run_variant(label, cal, hold, popslip):
    rj = RESULTS / f"{label}.json"
    log = RESULTS / f"{label}.log"
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "N_ITER_OVERRIDE": N_ITER, "WINDOWS_OVERRIDE": WINDOWS,
                "MC_RESULTS_JSON": str(rj), "CALENDAR_HOLD": cal,
                "HOLD_CAL_DAYS": hold, "NOMINAL_CAL_DTE": "30", "DH_POP_SLIP": popslip})
    print(f"=== {label}  hold={hold} popslip={popslip} ===", flush=True)
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.run([sys.executable, "-u", "monte_carlo.py"],
                              cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT)
    if proc.returncode != 0 or not rj.exists():
        print(f"  !! {label} FAILED rc={proc.returncode}; see {log}", flush=True)
        return None
    return json.loads(rj.read_text())


def main():
    results = {lbl: run_variant(lbl, c, h, p) for (lbl, c, h, p) in VARIANTS}
    wins = WINDOWS.split(",")

    def cell(d, w, k):
        return d.get(w, {}).get(k) if d else None

    def maxcoll(d):
        return max((cell(d, w, "p_coll") or 0.0) for w in wins) if d else 999.0

    print("\n" + "=" * 96)
    print(f"FINE HOLD-PEAK   5y / 22-now MedRet (pop00 free-fill | pop03 realistic)   [N={N_ITER}]")
    print("=" * 96)
    print(f"{'hold':>5}{'5y pop00':>13}{'5y pop03':>13}{'22n pop00':>13}{'22n pop03':>13}{'5yDD03':>8}{'coll':>6}")
    print("-" * 96)
    for h in HOLDS:
        d0 = results.get(f"h{h}_pop00"); d3 = results.get(f"h{h}_pop03")
        c = max(maxcoll(d0), maxcoll(d3))
        print(f"{h:>5}{cell(d0,'5y',RK):>+12.0f}%{cell(d3,'5y',RK):>+12.0f}%"
              f"{cell(d0,'22-now',RK):>+12.0f}%{cell(d3,'22-now',RK):>+12.0f}%"
              f"{cell(d3,'5y','worst_dd'):>7.1f}%{c:>5.1f}%")

    # Rank by REALISTIC popout cost (pop03), headline windows, collapse-safe only.
    def score(h):
        d3 = results.get(f"h{h}_pop03")
        if not d3 or maxcoll(d3) > 0.01:
            return -1e18
        s = 0.0
        for w in HEADLINE:
            r = cell(d3, w, RK)
            if r is None:
                return -1e18
            s += math.log1p(max(r, -99.0) / 100.0)
        return s

    ranked = sorted(HOLDS, key=score, reverse=True)
    print("\nRANK by realistic pop03 (5y+22-now MedRet), collapse-safe:")
    for h in ranked:
        d3 = results.get(f"h{h}_pop03")
        print(f"  hold={h}: pop03 5y={cell(d3,'5y',RK):>+9.0f}%  22-now={cell(d3,'22-now',RK):>+9.0f}%  "
              f"5yDD={cell(d3,'5y','worst_dd'):.1f}%  maxColl={maxcoll(d3):.1f}%")
    best = ranked[0]
    print(f"\nFINE PEAK (under realistic popout cost): HOLD_CAL_DAYS = {best}")
    print(f"  current shipped = 27; {'CONFIRMED' if best == 27 else 'UPDATE to %d' % best}")

    (RESULTS / "fine_combined.json").write_text(json.dumps(results, indent=2))
    print(f"\nWrote {RESULTS / 'fine_combined.json'}")


if __name__ == "__main__":
    main()
