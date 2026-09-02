"""
N=300 confirmation of the steel-man CDR configs vs baseline, full window set incl
2020-COVID. Phase B (N=100, 11 configs) showed a comprehensive null (every config
loses 5y compound, none cuts DD). This pins the two least-bad configs at proper N
so the null is airtight at the Stage-3 reliability floor (N>=300 for DD).

  deep65    : only cut >=65%-down, low-conviction (<=79) bags in calm (VIX<22),
              shallow-DD (<0.35) tape. The most conservative "surely safe" cut.
  abl_novix : deep (>=55%), low-conviction, shallow-DD, NO VIX gate (the Phase-B
              config closest to baseline).

Usage (queue it): python -u experiments/dead_hold_realloc_v70/confirm_cdr_n300.py
"""
import os, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "v69_portfolio_retune"))
import driver  # noqa
driver.ENV_MAP.update({k: k for k in (
    'REALLOC_STRATEGY', 'REALLOC_MIN_ADVANTAGE', 'REALLOC_MIN_LOSS',
    'REALLOC_MAX_VIX', 'REALLOC_MAX_DD', 'REALLOC_MAX_TARGET_SCORE', 'REALLOC_CUT_SLIP')})

OUT = Path(__file__).resolve().parent
N = int(os.environ.get('AB_N', '300'))
CPU = int(os.environ.get('AB_CPU', '6'))
WINDOWS = ['2020_crash', '2021', '2022', '2024', '2025', 'dip', '5y', '22-now']

CANDS = [
    ("baseline", {}),
    ("deep65",   {'REALLOC_STRATEGY': 'current_pnl_low', 'REALLOC_MIN_ADVANTAGE': 8,
                  'REALLOC_MIN_LOSS': 0.65, 'REALLOC_MAX_VIX': 22, 'REALLOC_MAX_DD': 0.35,
                  'REALLOC_MAX_TARGET_SCORE': 79, 'REALLOC_CUT_SLIP': 0.015}),
    ("abl_novix", {'REALLOC_STRATEGY': 'current_pnl_low', 'REALLOC_MIN_ADVANTAGE': 8,
                   'REALLOC_MIN_LOSS': 0.55, 'REALLOC_MAX_VIX': 999, 'REALLOC_MAX_DD': 0.35,
                   'REALLOC_MAX_TARGET_SCORE': 79, 'REALLOC_CUT_SLIP': 0.015}),
]


def main():
    print(f"== CDR N={N} confirmation | windows={WINDOWS} ==\n")
    res = {}
    for tag, params in CANDS:
        print(f"-- {tag} {params} --")
        res[tag] = driver.run_candidate(params, n_iter=N, windows=WINDOWS, workers=CPU, tag=f"cdrN{N}_{tag}")
    (OUT / f"confirm_cdr_n{N}_results.json").write_text(json.dumps({"n": N, "results": res}, indent=2))
    base = res["baseline"]
    print("\n" + "=" * 96)
    print(f"CDR N={N}: WorstDD / MedRet / collapse vs baseline")
    print("=" * 96)
    for tag in ("deep65", "abl_novix"):
        print(f"\n### {tag}")
        print(f"  {'window':12s} {'baseDD':>7s} {'candDD':>7s} {'dDD':>6s} {'baseMed%':>13s} {'candMed%':>13s} {'dComp%':>8s} {'coll':>5s}")
        for w in WINDOWS:
            b, c = base.get(w), res[tag].get(w)
            if not b or not c:
                continue
            dd = c['worst_dd'] - b['worst_dd']
            dcomp = (c['med_ret'] - b['med_ret']) / (abs(b['med_ret']) + 1e-9) * 100
            print(f"  {w:12s} {b['worst_dd']:>7.1f} {c['worst_dd']:>7.1f} {dd:>+6.1f} "
                  f"{b['med_ret']:>+13.1f} {c['med_ret']:>+13.1f} {dcomp:>+8.1f} {c['p_collapse']:>5.1f}")
    print(f"\n== wrote {OUT/'confirm_cdr_n300_results.json'} ==")


if __name__ == "__main__":
    main()
