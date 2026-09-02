"""HOLD-vs-CUT honest-theta re-test on the v71 substrate (2026-06-10).

The HOLD >> CUT axiom (wide SL -0.70 + ride to the day-N hard-sell + dead-hold)
was decided on the theta-OPTIMISTIC trading-bar engine. The 2026-06-09 honest
calendar-theta standardization under-charged theta ~16pp on slow trades and
flagged this exact re-test (experiments/calendar_hold/FINDINGS.md, point 3).
Never run on the honest engine, never on v71. v71 doubled 75+ supply -> a
CUT/fast-recycle strategy now has ~2x more signals to redeploy into.

Substrate = LIVE v71 Apex: scoring 04044b21b, CALENDAR_HOLD on / HOLD_CAL_DAYS 27
/ NOMINAL_CAL_DTE 30, TIER_LOW 0.05 / overflow 0, SL -0.70, dead-hold -0.40/-0.15,
puts off, caps 0.50. c_base carries NO overrides -> it IS that live config.

Wiring: SL_BASE + tiers go through run_candidate's `params` (driver ENV_MAP).
The calendar-hold + dead-hold knobs (HOLD_CAL_DAYS / CALENDAR_HOLD /
DEAD_HOLD_ENABLED / NOMINAL_CAL_DTE) are read DIRECTLY from os.environ by
monte_carlo.py and are NOT in the driver ENV_MAP (run_candidate would KeyError),
so we set them in os.environ around the call (driver does env = dict(os.environ)).

NOTE: staged in a depless container; smoke one cell on the compute box first:
  python experiments/holdcut_honest_v71/sweep.py --phase B --cands c_base --n 20 --workers 4

Gate (Stage-3 T1-T7): collapse=0 mandatory (hard veto, incl 2020_crash);
DD-primary = mean(5y,22-now) WorstDD vs c_base; honest-compound guard 5y MedRet
>= 0.8x base; Pareto preferred. B N=100x6 -> C N=300x8 -> D N=500x10.
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

# Each candidate: {"params": <ENV_MAP knobs>, "env": <direct os.environ knobs>}.
# Omit either. c_base = live v71 Apex defaults (HOLD: SL -0.70, hold 27, dh on).
CANDS = {
    "c_base": {},
    # --- CUT via stop width (hold 27, dead-hold ON) ---
    "cut_sl50":     {"params": {"SL_BASE": -0.50}},
    "cut_sl40":     {"params": {"SL_BASE": -0.40}},
    "cut_sl30":     {"params": {"SL_BASE": -0.30}},
    # --- CUT via shorter calendar hold (SL -0.70, dead-hold ON) ---
    "cut_hold21":   {"env": {"HOLD_CAL_DAYS": "21"}},
    "cut_hold18":   {"env": {"HOLD_CAL_DAYS": "18"}},
    "cut_hold14":   {"env": {"HOLD_CAL_DAYS": "14"}},
    # --- CUT combined (tighter stop + shorter hold) ---
    "cut_sl40_h18": {"params": {"SL_BASE": -0.40}, "env": {"HOLD_CAL_DAYS": "18"}},
    "cut_sl30_h14": {"params": {"SL_BASE": -0.30}, "env": {"HOLD_CAL_DAYS": "14"}},
    # --- collapse probe: dead-hold OFF under honest theta (expect collapse veto) ---
    "probe_dh_off": {"env": {"DEAD_HOLD_ENABLED": "0"}},
}

OUT = os.path.join(_ROOT, ".cache", "holdcut_honest_v71")
os.makedirs(OUT, exist_ok=True)


def jsonl_path(phase):
    return os.path.join(OUT, f"phase_{phase}.jsonl")


def _run_one(spec, n_iter, windows, workers, tag):
    """Apply direct-env knobs around the ENV_MAP-driven run_candidate call."""
    params = spec.get("params", {})
    direct = spec.get("env", {})
    saved = {k: os.environ.get(k) for k in direct}
    try:
        for k, v in direct.items():
            os.environ[k] = str(v)
        return run_candidate(params, n_iter=n_iter, windows=windows,
                             workers=workers, tag=tag)
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


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
        spec = CANDS[name]
        t0 = time.time()
        print(f"[run] {name} spec={spec}", flush=True)
        res = _run_one(spec, n_iter, windows, workers, tag=f"{phase}_{name}")
        rec = {"cand": name, "spec": spec, "n": n_iter, "res": res,
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
    print(f"{'cand':14} {'dd5y':>6} {'dd22n':>6} {'ddFocusD':>8} {'med5y':>10} "
          f"{'med/base':>9} {'colMax':>6} {'ddCrash':>7}  verdict")
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
        if colmax > 0:
            verdict = "VETO collapse"
        elif name == "c_base":
            verdict = "(baseline)"
        elif dd_d > 0 and ratio >= 0.8:
            verdict = "CUT Pareto/DD-win" if ratio >= 1.0 else "CUT DD-win"
        else:
            verdict = "HOLD>=CUT"
        print(f"{name:14} {res['5y']['worst_dd']:6.1f} "
              f"{res.get('22-now', {}).get('worst_dd', float('nan')):6.1f} "
              f"{dd_d:+8.2f} {med5:10.0f} {ratio:9.2f} {colmax:6.1f} "
              f"{res.get('2020_crash', {}).get('worst_dd', float('nan')):7.1f}  {verdict}")


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
