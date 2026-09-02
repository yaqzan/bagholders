"""v73 Stage-3 portfolio retune sweep (2026-06-12).

Context: v73 (dampener-retirement ship, `07e9722b5`) raised 75+ supply +77%
on top of v71's already-doubled density (ReSim 5y 3,262 -> 5,769 signals; the
restored CWCF/CSWC/SCW cohort spans 75-94). The pre-ship MC smoke (N=300x7,
paired ReSim arms) showed collapse=0 everywhere but DD +5..+13pp at the
UNCHANGED Apex params — the exact v71 signature. This sweep re-fits the
cascade / exposure / DD-band to the v73 density. TP/SL (Stage 2) is FROZEN.

PRF seed (instrument-only, `portfolio_response.py --derive v73`,
.cache/algorithm_versions/v73/research_pack/derived_portfolio.json):
  tier ladder 0.20 / 0.15 / 0.10 / **low 0.03**, overflow 0
  (supply/day ultra .007 top .203 mid .895 low 3.014; recycle cov 0.978,
  supply_approx=false). F proposes, MC disposes — T1-T7 confirm mandatory.

Phases (driver = experiments/v69_portfolio_retune/driver.py, deterministic
PYTHONHASHSEED=0 paired seeds, MC_NO_DB_PERSIST=1, active version = v73):
  B: structured candidates, N=100 x 6 windows.
     NOTE: v73 5y-recalc coverage starts 2020-12-31, so the 2020 COVID windows
     are NOT available until queue #157 (10y full recalc, off-market) lands.
     B uses {2021, 2022, 2024, dip, 22-now, 5y}; COVID enters at C/D.
  C: top-3 + base, N=300 x 8 windows (adds 2020_crash, 2025)
  D: winner + base, N=500 x 10 windows (adds 2020, 2023)

Rank: collapse=0 mandatory; DD-primary (mean of 5y+22-now WorstDD delta vs
base) with a compound guard (5y MedRet >= 0.8x base); Pareto preferred.

Usage:
  python experiments/v73_portfolio_retune/sweep.py --phase B --workers 6
  python experiments/v73_portfolio_retune/sweep.py --phase C --cands c_base,c00_prf_low03 --n 300
  python experiments/v73_portfolio_retune/sweep.py --report B
"""
import argparse
import json
import os
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(1, os.path.join(_ROOT, "experiments", "v69_portfolio_retune"))

from driver import run_candidate  # noqa: E402

# v73 5y coverage starts 2020-12-31 -> 2020/2020_crash deferred to C/D (after
# queue #157 10y recalc).
WINDOWS_B = ["2021", "2022", "2024", "dip", "22-now", "5y"]
WINDOWS_C = ["2020_crash", "2021", "2022", "2024", "2025", "dip", "22-now", "5y"]
WINDOWS_D = ["2020_crash", "2020", "2021", "2022", "2023", "2024", "2025", "dip",
             "22-now", "5y"]

# Current Apex (v73 live = post-v71-retune): ultra .20 top .15 mid .10 low .05
# ovf 0 | caps .50/.50 | MAX 14/14 | DD band .35/.55/.40
def scaled(f):
    return {
        "TIER_ULTRA": round(0.20 * f, 4), "TIER_TOP": round(0.15 * f, 4),
        "TIER_MID": round(0.10 * f, 4), "TIER_LOW": round(0.05 * f, 4),
    }


CANDS = {
    "c_base": {},
    # 0) THE PRF-seeded candidate (deploy.md: seeded first candidate for the
    #    post-ship Stage-3 retune) — squeeze the low slug to the matched value.
    "c00_prf_low03": {"TIER_LOW": 0.03},
    # 1) low-slug ladder around the PRF point (the c14-direction lever)
    "c01_low02": {"TIER_LOW": 0.02},
    "c02_low04": {"TIER_LOW": 0.04},
    # 2) mid trim — 80-84 density also rose on v73 (trio removals spanned 75-94)
    "c03_mid08": {"TIER_MID": 0.08},
    "c04_low03_mid08": {"TIER_LOW": 0.03, "TIER_MID": 0.08},
    # 3) top trim — 85-94 N +67% on the ReSim (132->220 at 85-89)
    "c05_top12": {"TIER_TOP": 0.12},
    "c06_topheavy_trim": {"TIER_TOP": 0.12, "TIER_MID": 0.08, "TIER_LOW": 0.04},
    # 4) uniform per-trade alloc scale-down (same exposure cap, smaller slugs)
    "c07_alloc080": scaled(0.80),
    "c08_alloc065": scaled(0.65),
    # 5) exposure cap (capital-velocity law re-test on the densest substrate yet)
    "c09_cap40": {"GROSS_PREMIUM_CAP": 0.40, "CALL_PREMIUM_CAP": 0.40},
    "c10_cap45": {"GROSS_PREMIUM_CAP": 0.45, "CALL_PREMIUM_CAP": 0.45},
    "c11_cap60": {"GROSS_PREMIUM_CAP": 0.60, "CALL_PREMIUM_CAP": 0.60},
    # 6) more slots x smaller slugs (diversify the same exposure)
    "c12_mp18_low03": {"MAX_POSITIONS": 18, "MAX_POSITIONS_CALL": 18,
                       "TIER_LOW": 0.03},
    # 7) DD soft-band earlier (hotter book -> react earlier)
    "c13_dd305040": {"DD_SOFT_BAND_LO": 0.30, "DD_SOFT_BAND_HI": 0.50,
                     "DD_SOFT_CALL_FLOOR": 0.40},
    # 8) combos
    "c14_prf_dd": {"TIER_LOW": 0.03, "DD_SOFT_BAND_LO": 0.30,
                   "DD_SOFT_BAND_HI": 0.50, "DD_SOFT_CALL_FLOOR": 0.40},
    "c15_prf_cap45": {"TIER_LOW": 0.03, "GROSS_PREMIUM_CAP": 0.45,
                      "CALL_PREMIUM_CAP": 0.45},
}

OUT = os.path.join(_ROOT, ".cache", "v73_portfolio_retune")
os.makedirs(OUT, exist_ok=True)


def jsonl_path(phase):
    return os.path.join(OUT, f"phase_{phase}.jsonl")


def run_phase(phase, cands, n_iter, windows, workers):
    done = set()
    p = jsonl_path(phase)
    if os.path.exists(p):
        with open(p) as f:
            done = {json.loads(l)["cand"] for l in f if l.strip()}
    for name in cands:
        if name in done:
            print(f"[skip] {name} already in {p}", flush=True)
            continue
        params = CANDS[name]
        t0 = time.time()
        print(f"[run] {name} params={params}", flush=True)
        res = run_candidate(params, n_iter=n_iter, windows=windows,
                            workers=workers, tag=f"v73{phase}_{name}")
        rec = {"cand": name, "params": params, "n": n_iter, "res": res,
               "took_s": round(time.time() - t0, 1)}
        with open(p, "a") as f:
            f.write(json.dumps(rec) + "\n")
        line = "  ".join(
            f"{w}: med={res[w]['med_ret']:+.0f}% dd={res[w]['worst_dd']:.1f} "
            f"col={res[w]['p_collapse']:.1f}"
            for w in windows if w in res)
        print(f"[done] {name} ({rec['took_s']}s)  {line}", flush=True)


def report(phase):
    p = jsonl_path(phase)
    recs = [json.loads(l) for l in open(p) if l.strip()]
    by = {r["cand"]: r for r in recs}
    base = by.get("c_base")
    if base is None:
        print("no c_base yet")
        return
    bw = base["res"]
    focus = [w for w in ("5y", "22-now") if w in bw]
    print(f"{'cand':22} {'dd5y':>6} {'dd22n':>6} {'ddFocusD':>8} {'med5y':>10} "
          f"{'med5y/base':>10} {'colMax':>6} {'ddCrash':>7}")
    rows = []
    for name, r in by.items():
        res = r["res"]
        if not all(w in res for w in focus):
            continue
        dd_d = sum(bw[w]["worst_dd"] - res[w]["worst_dd"] for w in focus) / len(focus)
        med5 = res.get("5y", {}).get("med_ret", float("nan"))
        ratio = med5 / bw["5y"]["med_ret"] if bw["5y"]["med_ret"] else float("nan")
        colmax = max(v["p_collapse"] for v in res.values())
        rows.append((dd_d, name, res, med5, ratio, colmax))
    rows.sort(reverse=True)
    for dd_d, name, res, med5, ratio, colmax in rows:
        print(f"{name:22} {res['5y']['worst_dd']:6.1f} "
              f"{res.get('22-now', {}).get('worst_dd', float('nan')):6.1f} "
              f"{dd_d:+8.2f} {med5:10.0f} {ratio:10.2f} {colmax:6.1f} "
              f"{res.get('2020_crash', {}).get('worst_dd', float('nan')):7.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default=None, choices=["B", "C", "D"])
    ap.add_argument("--cands", default=None, help="csv of candidate names")
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--report", default=None, choices=["B", "C", "D"])
    a = ap.parse_args()
    if a.report:
        report(a.report)
        return 0
    windows = {"B": WINDOWS_B, "C": WINDOWS_C, "D": WINDOWS_D}[a.phase]
    n = a.n or {"B": 100, "C": 300, "D": 500}[a.phase]
    cands = (a.cands.split(",") if a.cands else list(CANDS.keys()))
    for c in cands:
        if c not in CANDS:
            raise KeyError(c)
    run_phase(a.phase, cands, n, windows, a.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
