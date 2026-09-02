"""v74-lean Stage-3 cascade retune sweep (2026-06-17).

Context: v74 (the LEAN ship, `f9fb7b934`, 2026-06-15) retired the post-pre_boost
score-stage tail -> >=75 supply dropped ~38% (v73 ReSim ~4.6 sig/day -> v74 PRF
2.8/day). The live cascade is STILL the v73-density-tuned c04 selectivity trim
(ultra .20/top .15/mid .08/low .03), now applied to a 38%-SPARSER substrate with
NO v74 re-validation. This is the documented "portfolio re-tune on the new
substrate" follow-up (v71->c14, v73->c04 reliable-Pareto pattern), keyed on a
MEASURED supply shift -- not a dry-well lever / persist-crash wall / known null.

PRF seed (instrument-only, .cache/algorithm_versions/v74/research_pack/
derived_portfolio.json, generated 2026-06-17): F proposes
  tier ladder 0.20 / 0.15 / mid 0.10 / low 0.051, overflow 0
  (supply/day ultra .009 top .205 mid .633 low 1.961; recycle cov 0.975,
  supply_approx=false). F DIVERGES from canon (mid 0.08/low 0.03) -> there is a
  retune candidate. HONEST PRIOR: F says SIZE UP, but coverage is 0.975 (book
  saturated) and the 50% gross cap means sizing up may CONCENTRATE (G16
  over-deployment trap) rather than recapture compound -> MC decides ship vs
  validate-c04. TP/SL (Stage 2), DD levers, dead-hold, caps, MaxPos pass through
  canon (the "F not-owned" set) except where a candidate explicitly varies them.

Three theses tested:
  (1) SIZE UP (F): lower supply -> less crowding -> trimmed allocs under-deploy
      -> size mid/low back up (toward v71-era 0.10/0.05) to recapture compound.
  (2) CONCENTRATE INTO QUALITY: lower supply -> deploy freed capacity into the
      BEST-edge tiers (ultra .19/top .11 edge >> low .069), not the low tier.
  (3) VALIDATE c04 (null): coverage 0.975 -> book saturated, 50% cap binds ->
      c04 already optimal -> keep it (a clean closed question on the new substrate).

Phases (driver = experiments/v69_portfolio_retune/driver.py, deterministic
PYTHONHASHSEED=0 paired seeds, MC_NO_DB_PERSIST=1, active version = v74):
  B: structured candidates, N=100 x 6 windows (incl 2020_crash COVID-collapse
     check from the start -- G16/v74 has full 10y coverage).
  C: top-3 + base, N=300 x 8 windows incl COVID.
  D: winner + base, N=500 x 8 windows (ship gate).

Rank: collapse=0 mandatory (hard floor incl 2020-COVID); DD-primary (mean of
5y+22-now WorstDD delta vs base) with a compound guard (5y MedRet >= 0.8x base);
Pareto preferred (DD down AND compound flat/up).

Usage:
  python experiments/v74_cascade_retune/sweep.py --phase B --workers 6
  python experiments/v74_cascade_retune/sweep.py --report B
  python experiments/v74_cascade_retune/sweep.py --phase C --cands c_base,c00_prf,... --n 300
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
while _ROOT in sys.path:           # G29: worktree/path-pin hygiene (harmless on main)
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(1, os.path.join(_ROOT, "experiments", "v69_portfolio_retune"))

from driver import run_candidate  # noqa: E402

# v74 has full 10y coverage (recalc at ship) -> COVID in B from the start (G16).
WINDOWS_B = ["2020_crash", "2022", "2024", "dip", "22-now", "5y"]
WINDOWS_C = ["2020", "2020_crash", "2022", "2023", "2024", "dip", "22-now", "5y"]
WINDOWS_D = ["2020", "2020_crash", "2022", "2023", "2024", "dip", "22-now", "5y"]

# Current v74 live cascade (c04, the v73 retune result): ultra .20 top .15
# mid .08 low .03 ovf 0 | caps .50/.50 | MAX 14/14 | DD band .35/.55/.40.
# c_base = {} = that live config (all 6 levers ON). Candidates vary the cascade.
CANDS = {
    "c_base": {},  # current c04 -- BASELINE (validate-c04 thesis #3)

    # --- thesis (1) SIZE UP (the PRF direction) ---
    # 0) THE PRF-seeded candidate (deploy.md: seeded first candidate).
    "c00_prf": {"TIER_MID": 0.10, "TIER_LOW": 0.051},
    "c01_mid10_low05": {"TIER_MID": 0.10, "TIER_LOW": 0.05},   # clean v71-era ladder
    "c02_low05": {"TIER_LOW": 0.05},                            # low up only
    "c03_low04": {"TIER_LOW": 0.04},                            # mild low up
    "c04_mid10": {"TIER_MID": 0.10},                            # mid up only
    "c05_uniform115": {"TIER_ULTRA": 0.23, "TIER_TOP": 0.1725,  # uniform +15%
                       "TIER_MID": 0.092, "TIER_LOW": 0.0345},

    # --- thesis (2) CONCENTRATE INTO QUALITY (size up the best-edge tiers) ---
    "c06_qual_u22_t17": {"TIER_ULTRA": 0.22, "TIER_TOP": 0.17},        # keep mid/low c04
    "c07_qual_u24_t18": {"TIER_ULTRA": 0.24, "TIER_TOP": 0.18},        # stronger
    "c08_qual_u22_t17_mid10": {"TIER_ULTRA": 0.22, "TIER_TOP": 0.17,   # quality + mid up,
                               "TIER_MID": 0.10},                       # low stays selective

    # --- thesis (1)+cap/slots: let the bigger slugs deploy without the 50% cap binding ---
    "c09_prf_cap60": {"TIER_MID": 0.10, "TIER_LOW": 0.051,
                      "GROSS_PREMIUM_CAP": 0.60, "CALL_PREMIUM_CAP": 0.60},
    "c10_prf_mp18": {"TIER_MID": 0.10, "TIER_LOW": 0.051,
                     "MAX_POSITIONS": 18, "MAX_POSITIONS_CALL": 18},
}

OUT = os.path.join(_ROOT, ".cache", "v74_cascade_retune")
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
                            workers=workers, tag=f"v74{phase}_{name}")
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
    print(f"{'cand':24} {'dd5y':>6} {'dd22n':>6} {'ddFocusD':>8} {'med5y':>12} "
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
        print(f"{name:24} {res['5y']['worst_dd']:6.1f} "
              f"{res.get('22-now', {}).get('worst_dd', float('nan')):6.1f} "
              f"{dd_d:+8.2f} {med5:12.0f} {ratio:10.2f} {colmax:6.1f} "
              f"{res.get('2020_crash', {}).get('worst_dd', float('nan')):7.1f}")
    print("\nPareto = ddFocusD>0 (DD down) AND med5y/base>=1.0 (compound up). "
          "Hard floor: colMax=0.0 every window incl COVID.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default=None, choices=["B", "C", "D"])
    ap.add_argument("--cands", default=None, help="csv of candidate names")
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
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
