"""Optimal calendar HOLD window sweep (honest theta, 30 DTE instrument).

Finds the calendar-day hard-sell deadline (HOLD_CAL_DAYS) that maximizes RETURN
subject to collapse==0, on the live v70 Apex config, with the HONEST calendar
theta clock (NOMINAL_CAL_DTE=30). Holds the bought instrument fixed at 30 cal DTE
(PREMIUM_MULT=1.82) and the current TP/SL/cascade fixed — only the exit window varies.

Includes the trading-bar baseline ('A_bar') as the realism-fee reference point.

Apex objective (per trading-strategy.md): maximize cost-adjusted MedRet s.t.
collapse_rate==0 across all windows; DD reported as a budget, not constrained.

    python experiments/calendar_hold/sweep_holdwin.py
    N_ITER=500 HOLDS=11,13,15 python experiments/calendar_hold/sweep_holdwin.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results_holdwin"
RESULTS.mkdir(exist_ok=True)

N_ITER = os.environ.get("N_ITER", "200")
WINDOWS = os.environ.get("WINDOWS", "2022,2024,2025,dip,22-now,5y,2020_crash")
HOLDS = [int(x) for x in os.environ.get("HOLDS", "7,9,11,13,15,17,19,21,24,27").split(",")]
RETURN_KEY = "med_ret"            # rank metric (compound proxy)
HEADLINE_WINDOWS = ["5y", "22-now"]

VARIANTS = [("A_bar", {"CALENDAR_HOLD": "0"})]
for h in HOLDS:
    VARIANTS.append((f"cal{h:02d}",
                     {"CALENDAR_HOLD": "1", "HOLD_CAL_DAYS": str(h), "NOMINAL_CAL_DTE": "30"}))


def run_variant(name, extra_env):
    rj = RESULTS / f"{name}.json"
    log = RESULTS / f"{name}.log"
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "N_ITER_OVERRIDE": N_ITER, "WINDOWS_OVERRIDE": WINDOWS,
                "MC_RESULTS_JSON": str(rj)})
    env.update(extra_env)
    print(f"=== {name}  env={extra_env} ===", flush=True)
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.run([sys.executable, "-u", "monte_carlo.py"],
                              cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT)
    if proc.returncode != 0 or not rj.exists():
        print(f"  !! {name} FAILED rc={proc.returncode}; see {log}", flush=True)
        return None
    return json.loads(rj.read_text())


def main():
    results = {name: run_variant(name, extra) for name, extra in VARIANTS}
    win_labels = [w for w in WINDOWS.split(",")]

    def cell(d, w, k):
        return d.get(w, {}).get(k) if d else None

    def max_collapse(d):
        return max((cell(d, w, "p_coll") or 0.0) for w in win_labels) if d else 999.0

    print("\n" + "=" * 100)
    print(f"HOLD-WINDOW SWEEP (honest theta, 30 DTE)  —  MedRet by window   [N={N_ITER}]")
    print("=" * 100)
    hdr = f"{'variant':<8}{'maxColl':>8}"
    for w in win_labels:
        hdr += f"{w:>11}"
    hdr += f"{'5yDD':>8}{'22nDD':>8}"
    print(hdr)
    print("-" * 100)
    rows = []
    for name, _ in VARIANTS:
        d = results.get(name)
        mc_ = max_collapse(d)
        line = f"{name:<8}{mc_:>7.1f}%"
        for w in win_labels:
            mr = cell(d, w, RETURN_KEY)
            line += f"{mr:>+10.0f}%" if mr is not None else f"{'--':>11}"
        dd5 = cell(d, "5y", "worst_dd"); dd22 = cell(d, "22-now", "worst_dd")
        line += f"{dd5:>7.1f}%" if dd5 is not None else f"{'--':>8}"
        line += f"{dd22:>7.1f}%" if dd22 is not None else f"{'--':>8}"
        print(line)
        if d and name != "A_bar":
            rows.append((name, d, mc_))

    # Pick optimum: collapse-safe (max collapse == 0) AND maximize headline return.
    # Score = geometric-ish mean of headline-window MedRet ranks (use sum of logs of (1+ret%)).
    import math
    def headline_score(d):
        s = 0.0
        for w in HEADLINE_WINDOWS:
            r = cell(d, w, RETURN_KEY)
            if r is None:
                return -1e18
            s += math.log1p(max(r, -99.0) / 100.0)
        return s

    safe = [(n, d, mc_) for (n, d, mc_) in rows if mc_ <= 0.01]
    pool = safe if safe else rows
    pool.sort(key=lambda t: headline_score(t[1]), reverse=True)

    print("\nRANK (collapse-safe only, by headline 5y+22-now MedRet):")
    for n, d, mc_ in pool[:6]:
        hd = int(n[3:]) if n.startswith("cal") else None
        print(f"  {n:<8} hold={hd}cal  5y={cell(d,'5y',RETURN_KEY):>+10.0f}%  "
              f"22-now={cell(d,'22-now',RETURN_KEY):>+10.0f}%  "
              f"5yDD={cell(d,'5y','worst_dd'):.1f}%  22nDD={cell(d,'22-now','worst_dd'):.1f}%  "
              f"maxColl={mc_:.1f}%")

    if pool:
        win_name, win_d, win_mc = pool[0]
        print(f"\nOPTIMAL RETURN WINDOW: {win_name} (hold={int(win_name[3:])} calendar days on a 30 DTE option)")
        print(f"  collapse-safe={win_mc <= 0.01}  5y MedRet={cell(win_d,'5y',RETURN_KEY):+.0f}%  "
              f"5y WorstDD={cell(win_d,'5y','worst_dd'):.1f}%")

    (RESULTS / "sweep_combined.json").write_text(json.dumps(results, indent=2))
    print(f"\nWrote {RESULTS / 'sweep_combined.json'}")


if __name__ == "__main__":
    main()
