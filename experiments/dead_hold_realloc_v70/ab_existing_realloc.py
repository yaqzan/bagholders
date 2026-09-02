"""
A/B the user's LITERAL idea on the honest-v70 Apex substrate:
  "cut the bag that's deep underwater to fund the higher-score fresh signal."

This is the already-wired REALLOC_STRATEGY=current_pnl_low mechanism
(displace the lowest-current-pnl same-side held position when the book is at-cap
and a fresh signal clears MIN_ADVANTAGE over the displaced position's ENTRY score).
It is the documented v32 Stage-3 NULL — re-tested here on the new substrate
(honest v70 + asymmetric cost + dead-hold -40/-15 + RXDD/SVR/MWDD + uncapped Apex).

Includes 2020_crash (the collapse-critical window). Gate is DD-primary +
collapse=0. Paired seeds (PYTHONHASHSEED=0) so baseline vs candidate is apples-to-apples.

Usage (queue it):
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/dead_hold_realloc_v70/ab_existing_realloc.py
"""
import os, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "v69_portfolio_retune"))
import driver  # noqa

# REALLOC env keys are read directly by monte_carlo at import -> identity map.
driver.ENV_MAP.update({
    'REALLOC_STRATEGY': 'REALLOC_STRATEGY',
    'REALLOC_MIN_ADVANTAGE': 'REALLOC_MIN_ADVANTAGE',
    'REALLOC_MIN_HOLD_BARS': 'REALLOC_MIN_HOLD_BARS',
    'REALLOC_MAX_PER_DAY': 'REALLOC_MAX_PER_DAY',
})

OUT = Path(__file__).resolve().parent
N = int(os.environ.get('AB_N', '150'))
WINDOWS = ['2020_crash', '2020', '2022', '2025', 'dip', '5y', '22-now']
CPU = int(os.environ.get('AB_CPU', '6'))

CANDS = [
    ("baseline",        {}),
    ("cut_pnl_low_a5",  {'REALLOC_STRATEGY': 'current_pnl_low', 'REALLOC_MIN_ADVANTAGE': 5}),
    ("cut_pnl_low_a10", {'REALLOC_STRATEGY': 'current_pnl_low', 'REALLOC_MIN_ADVANTAGE': 10}),
    ("cut_pnl_low_a15", {'REALLOC_STRATEGY': 'current_pnl_low', 'REALLOC_MIN_ADVANTAGE': 15}),
]


def main():
    print(f"== A/B existing REALLOC (current_pnl_low) | N={N} | windows={WINDOWS} ==\n")
    results = {}
    for tag, params in CANDS:
        print(f"-- running {tag} {params} --")
        try:
            r = driver.run_candidate(params, n_iter=N, windows=WINDOWS, workers=CPU, tag=f"abre_{tag}")
        except Exception as e:
            print(f"   !! {tag} failed: {e}")
            continue
        results[tag] = r
    (OUT / "ab_existing_realloc_results.json").write_text(json.dumps(results, indent=2))

    base = results.get("baseline", {})
    print("\n" + "=" * 100)
    print("RESULT: WorstDD / MedRet / P(collapse) vs baseline   (Delta = cand - baseline)")
    print("=" * 100)
    for tag, _ in CANDS:
        if tag == "baseline" or tag not in results:
            continue
        print(f"\n### {tag}")
        print(f"  {'window':12s} {'baseDD':>7s} {'candDD':>7s} {'dDD':>7s} "
              f"{'baseMed':>11s} {'candMed':>11s} {'baseColl':>8s} {'candColl':>8s}")
        for w in WINDOWS:
            b = base.get(w); c = results[tag].get(w)
            if not b or not c:
                continue
            ddd = c['worst_dd'] - b['worst_dd']
            flag = "  <-DD UP" if ddd > 1.0 else ("  collapse!" if c['p_collapse'] > 0 else "")
            print(f"  {w:12s} {b['worst_dd']:>7.1f} {c['worst_dd']:>7.1f} {ddd:>+7.1f} "
                  f"{b['med_ret']:>+11.1f} {c['med_ret']:>+11.1f} "
                  f"{b['p_collapse']:>8.1f} {c['p_collapse']:>8.1f}{flag}")
    print(f"\n== wrote {OUT/'ab_existing_realloc_results.json'} ==")


if __name__ == "__main__":
    main()
