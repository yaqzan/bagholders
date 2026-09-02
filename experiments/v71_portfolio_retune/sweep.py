"""v71 Stage-3 portfolio retune sweep (2026-06-10).

Context: v71 (integrity-audit ship) DOUBLED 75+ signal supply (+83% 5y) at flat
honest WR. Under the unchanged Apex params the book saturates: N=300 smoke
showed 5y MedRet +1,292% / WorstDD 74.3% vs v70's +2,574% / 65.1% on the same
engine. The cascade/exposure/overflow/DD-band were fitted to v70's density —
this sweep re-fits them to v71. TP/SL (Stage 2) is FROZEN.

Phases (driver = experiments/v69_portfolio_retune/driver.py, deterministic
PYTHONHASHSEED=0 paired seeds, MC_NO_DB_PERSIST=1, active version = v71):
  B: structured candidates, N=100 x 6 windows (incl 2020_crash per G16)
  C: top-3 + base,        N=300 x 8 windows (adds 2021, 2025)
  D: winner + base,       N=500 x 10 windows (adds 2020, 2023)

Rank: collapse=0 mandatory; DD-primary (mean of 5y+22-now WorstDD delta vs
base) with a compound guard (5y MedRet >= 0.8x base); Pareto preferred.

Usage:
  python experiments/v71_portfolio_retune/sweep.py --phase B --cands c01,c02 --n 100 --workers 3
  python experiments/v71_portfolio_retune/sweep.py --report B
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

WINDOWS_B = ["2020_crash", "2022", "2024", "dip", "22-now", "5y"]
WINDOWS_C = ["2020_crash", "2021", "2022", "2024", "2025", "dip", "22-now", "5y"]
WINDOWS_D = ["2020_crash", "2020", "2021", "2022", "2023", "2024", "2025", "dip",
             "22-now", "5y"]

# Current Apex (v71 live): ultra .20 top .15 mid .10 low .10 ovf .035 |
# caps .50/.50 | MAX 14/14 | DD band .35/.55/.40
def scaled(f, ovf=None):
    return {
        "TIER_ULTRA": round(0.20 * f, 4), "TIER_TOP": round(0.15 * f, 4),
        "TIER_MID": round(0.10 * f, 4), "TIER_LOW": round(0.10 * f, 4),
        "TIER_OVERFLOW": round((0.035 if ovf is None else ovf) * (f if ovf is None else 1.0), 4),
    }


CANDS = {
    "c_base": {},
    # 1) per-trade alloc scale-down (same exposure cap, smaller slugs)
    "c01_alloc080": scaled(0.80),
    "c02_alloc065": scaled(0.65),
    "c03_alloc050": scaled(0.50),
    # 2) overflow off (+ low/mid trims) — idle-book premise gone
    "c04_ovf0": {"TIER_OVERFLOW": 0.0},
    "c05_ovf0_low08": {"TIER_OVERFLOW": 0.0, "TIER_LOW": 0.08},
    "c06_ovf0_midlow08": {"TIER_OVERFLOW": 0.0, "TIER_MID": 0.08, "TIER_LOW": 0.08},
    # 3) exposure cap (capital-velocity law re-test on the doubled-supply substrate)
    "c07_cap40": {"GROSS_PREMIUM_CAP": 0.40, "CALL_PREMIUM_CAP": 0.40},
    "c08_cap45": {"GROSS_PREMIUM_CAP": 0.45, "CALL_PREMIUM_CAP": 0.45},
    "c09_cap60": {"GROSS_PREMIUM_CAP": 0.60, "CALL_PREMIUM_CAP": 0.60},
    # 4) more slots x smaller slugs (diversify the same exposure)
    "c10_mp20_a070": dict(scaled(0.70), MAX_POSITIONS=20, MAX_POSITIONS_CALL=20),
    "c11_mp18_a080": dict(scaled(0.80), MAX_POSITIONS=18, MAX_POSITIONS_CALL=18),
    # 5) DD soft-band earlier / deeper (hotter book -> react earlier)
    "c12_dd305040": {"DD_SOFT_BAND_LO": 0.30, "DD_SOFT_BAND_HI": 0.50,
                     "DD_SOFT_CALL_FLOOR": 0.40},
    "c13_ddfloor30": {"DD_SOFT_CALL_FLOOR": 0.30},
    # 6) selectivity (the proven DD lever)
    "c14_low05_ovf0": {"TIER_LOW": 0.05, "TIER_OVERFLOW": 0.0},
    "c15_topheavy": {"TIER_ULTRA": 0.20, "TIER_TOP": 0.15, "TIER_MID": 0.08,
                     "TIER_LOW": 0.06, "TIER_OVERFLOW": 0.0},
    # 7) combos
    "c16_a080_ovf0_dd": dict(scaled(0.80, ovf=0.0), DD_SOFT_BAND_LO=0.30,
                             DD_SOFT_BAND_HI=0.50, DD_SOFT_CALL_FLOOR=0.40),
    "c17_cap45_a080_ovf02": dict(scaled(0.80, ovf=0.02), GROSS_PREMIUM_CAP=0.45,
                                 CALL_PREMIUM_CAP=0.45),
    # 8) overflow ladder rungs (user: "explore the 70 overflow logic";
    #    0 / 0.02 / 0.035 covered above & by base)
    "c18_ovf01": {"TIER_OVERFLOW": 0.01},
    "c19_ovf05": {"TIER_OVERFLOW": 0.05},
    # 9) put reintroduction probes on the v71 substrate (user request).
    #    PRIOR = HARD NULL on v70 (2026-06-02: every sliver collapsed 0.5-8%,
    #    +5-8pp 2022 DD, gutted returns). New condition: v71 retired JA4 (puts
    #    use the standard regime mult) + put WR +1.1pp. KILL CRITERIA (from the
    #    documented null): ANY p_collapse>0 anywhere, 2022 DD +5pp, return gut.
    "c20_put_sliver15": {"PUT_TIER_TOP": 0.05, "PUT_TIER_MID": 0.0,
                         "PUT_TIER_LOW": 0.0, "MAX_POSITIONS_PUT": 1,
                         "PUT_PREMIUM_CAP": 0.05},
    "c21_put_mod": {"PUT_TIER_TOP": 0.08, "PUT_TIER_MID": 0.05,
                    "PUT_TIER_LOW": 0.0, "MAX_POSITIONS_PUT": 2,
                    "PUT_PREMIUM_CAP": 0.10},
    "c22_put_half_v60": {"PUT_TIER_TOP": 0.06, "PUT_TIER_MID": 0.05,
                         "PUT_TIER_LOW": 0.04, "MAX_POSITIONS_PUT": 4,
                         "PUT_PREMIUM_CAP": 0.15},
}

OUT = os.path.join(_ROOT, ".cache", "v71_portfolio_retune")
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
                            workers=workers, tag=f"{phase}_{name}")
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
