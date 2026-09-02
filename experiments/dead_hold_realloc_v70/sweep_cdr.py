"""
CDR - Conditional Deep-loss Reallocation sweep (Stage-3, Apex calls-only, honest v70).

The user's idea made regime-safe: when the book is at-cap and a fresh higher-score
signal arrives, cut the DEEPEST loser to fund it -- but ONLY the targetable
"expirer" cohort the tape mine identified (DEEP underwater, LOW-conviction entry,
in CALM low-DD tape), never in panic/crash tape where dead-held bags recover (the
collapse-dodge). This is NOT the documented blunt-REALLOC null (which had no
deep/calm/conviction gates).

Mechanism = existing REALLOC_STRATEGY=current_pnl_low + the CDR gates
(REALLOC_MIN_LOSS / REALLOC_MAX_VIX / REALLOC_MAX_DD / REALLOC_MAX_TARGET_SCORE)
+ honest forced-exit spread on the cut (REALLOC_CUT_SLIP=0.015). All gates
default-OFF => byte-identical baseline.

Gate (Stage-3 T1-T7, DD primary): collapse=0 every window incl 2020-COVID;
5y WorstDD <= baseline +1.0pp; compound non-regression. For CDR the *hoped* win
is calm-year COMPOUND up at flat DD (it is gated off in crashes by design).

Usage (queue it):
  AB_N=100 PHASE=B python -u experiments/dead_hold_realloc_v70/sweep_cdr.py
"""
import os, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "v69_portfolio_retune"))
import driver  # noqa

driver.ENV_MAP.update({
    'REALLOC_STRATEGY': 'REALLOC_STRATEGY',
    'REALLOC_MIN_ADVANTAGE': 'REALLOC_MIN_ADVANTAGE',
    'REALLOC_MIN_HOLD_BARS': 'REALLOC_MIN_HOLD_BARS',
    'REALLOC_MAX_PER_DAY': 'REALLOC_MAX_PER_DAY',
    'REALLOC_MIN_LOSS': 'REALLOC_MIN_LOSS',
    'REALLOC_MAX_VIX': 'REALLOC_MAX_VIX',
    'REALLOC_MAX_DD': 'REALLOC_MAX_DD',
    'REALLOC_MAX_TARGET_SCORE': 'REALLOC_MAX_TARGET_SCORE',
    'REALLOC_CUT_SLIP': 'REALLOC_CUT_SLIP',
})

OUT = Path(__file__).resolve().parent
PHASE = os.environ.get('PHASE', 'B').upper()
N = int(os.environ.get('AB_N', '100'))
CPU = int(os.environ.get('AB_CPU', '6'))

PHASE_WINDOWS = {
    'B': ['2020_crash', '2021', '2024', 'dip', '5y'],
    'C': ['2020', '2020_crash', '2021', '2022', '2024', '2025', 'dip', '5y', '22-now'],
    'D': ['2020', '2020_crash', '2021', '2022', '2024', '2025', 'dip', '5y', '22-now'],
}
WINDOWS = json.loads(os.environ['AB_WINDOWS']) if os.environ.get('AB_WINDOWS') else PHASE_WINDOWS[PHASE]


def cdr(min_loss, max_vix, max_dd, score, adv):
    return {'REALLOC_STRATEGY': 'current_pnl_low', 'REALLOC_MIN_ADVANTAGE': adv,
            'REALLOC_MIN_LOSS': min_loss, 'REALLOC_MAX_VIX': max_vix,
            'REALLOC_MAX_DD': max_dd, 'REALLOC_MAX_TARGET_SCORE': score,
            'REALLOC_CUT_SLIP': 0.015}


# Phase-B candidate grid: diverse spread over the gate space + 2 ablations
# (no-VIX-gate, no-DD-gate) to test which gate is load-bearing for collapse.
CANDS_B = [
    ("baseline",       {}),
    ("tight",          cdr(0.40, 18, 0.30, 79, 10)),
    ("mid",            cdr(0.40, 22, 0.35, 79, 8)),
    ("loose",          cdr(0.40, 26, 0.45, 84, 5)),
    ("deep55",         cdr(0.55, 22, 0.35, 79, 8)),
    ("deep65",         cdr(0.65, 22, 0.35, 79, 8)),
    ("calm_strict",    cdr(0.40, 16, 0.25, 79, 8)),
    ("score84",        cdr(0.40, 22, 0.35, 84, 8)),
    ("adv5",           cdr(0.40, 22, 0.35, 79, 5)),
    ("abl_novix",      cdr(0.55, 999, 0.35, 79, 8)),   # ablation: no calm gate
    ("abl_nodd",       cdr(0.40, 20, 1.0, 79, 8)),     # ablation: no DD gate
]

# Phase C/D candidates are written by the runner after reading Phase B (top-3).
CANDS = CANDS_B
if os.environ.get('AB_CANDS'):
    raw = json.loads(os.environ['AB_CANDS'])  # [[tag, paramsdict], ...]
    CANDS = [(t, p) for t, p in raw]


def main():
    print(f"== CDR sweep PHASE={PHASE} | N={N} | windows={WINDOWS} | {len(CANDS)} cands ==\n")
    results = {}
    for tag, params in CANDS:
        print(f"-- {tag} {params} --")
        try:
            r = driver.run_candidate(params, n_iter=N, windows=WINDOWS, workers=CPU, tag=f"cdr_{PHASE}_{tag}")
        except Exception as e:
            print(f"   !! {tag} FAILED: {e}")
            continue
        results[tag] = r
    out_path = OUT / f"cdr_phase{PHASE}_results.json"
    out_path.write_text(json.dumps({"n": N, "windows": WINDOWS,
                                    "cands": {t: p for t, p in CANDS},
                                    "results": results}, indent=2))

    base = results.get("baseline", {})
    print("\n" + "=" * 110)
    print(f"CDR PHASE {PHASE}: WorstDD / MedRet / collapse vs baseline   (focus: collapse=0 + DD flat + compound up)")
    print("=" * 110)
    # rank by: collapse-safe AND (compound lift summed across calm windows)
    rankrows = []
    for tag, _ in CANDS:
        if tag == "baseline" or tag not in results:
            continue
        r = results[tag]
        any_coll = any((r.get(w, {}).get('p_collapse', 0) or 0) > 0 for w in WINDOWS)
        dd5 = r.get('5y', {}).get('worst_dd')
        bdd5 = base.get('5y', {}).get('worst_dd')
        ddelta = (dd5 - bdd5) if (dd5 is not None and bdd5 is not None) else None
        # compound proxy: sum of log10-ish med_ret rank vs baseline across windows
        comp_better = 0
        for w in WINDOWS:
            b = base.get(w, {}).get('med_ret'); c = r.get(w, {}).get('med_ret')
            if b is not None and c is not None and c > b:
                comp_better += 1
        rankrows.append((tag, any_coll, ddelta, comp_better))
        print(f"\n### {tag}   collapse_any={any_coll}  d5yDD={ddelta}")
        print(f"  {'window':12s} {'baseDD':>7s} {'candDD':>7s} {'dDD':>6s} "
              f"{'baseMed%':>13s} {'candMed%':>13s} {'coll':>5s}")
        for w in WINDOWS:
            b = base.get(w); c = r.get(w)
            if not b or not c:
                continue
            dd = c['worst_dd'] - b['worst_dd']
            print(f"  {w:12s} {b['worst_dd']:>7.1f} {c['worst_dd']:>7.1f} {dd:>+6.1f} "
                  f"{b['med_ret']:>+13.1f} {c['med_ret']:>+13.1f} {c['p_collapse']:>5.1f}")
    print("\n-- SUMMARY (collapse-safe candidates, sorted by #windows compound-better) --")
    safe = [r for r in rankrows if not r[1]]
    for tag, coll, dd, cb in sorted(safe, key=lambda x: -x[3]):
        print(f"  {tag:14s} d5yDD={dd}  compound-better-in {cb}/{len(WINDOWS)} windows")
    print(f"\n== wrote {out_path} ==")


if __name__ == "__main__":
    main()
