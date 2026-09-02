"""Calendar-hold A/B driver (queued sweep).

Runs the live monte_carlo.py portfolio engine under three call-side hold regimes
on the SAME active scoring version + Apex portfolio config, and compares them on
the Stage-3 gate metrics (WorstDD primary, return, collapse):

  A   baseline      trading-bar hold (HOLD_DAYS=15 bars ~21 cal days), theta over 30 bars
  C21 calendar-21   hold to signal_date + 21 CALENDAR days (~same bar exposure),
                    theta over TRUE calendar days elapsed / 30 cal DTE  -> isolates the
                    theta-honesty realism fee
  C15 calendar-15   hold to signal_date + 15 CALENDAR days (literal halfway of a 30 DTE
                    option) + honest theta -> shorter hold AND honest decay

Each variant is a fresh subprocess with env vars (CALENDAR_HOLD / HOLD_CAL_DAYS /
NOMINAL_CAL_DTE) so the change propagates to MC's spawn workers (the documented
v32_optim MP-spawn lesson: module-global patches don't reach workers; env vars do).

Directional, NOT a ship gate: N defaults to 200 x 7 windows. A real Stage-3 ship
of a calendar engine would need N=500 x full windows incl COVID. This answers
"is 30dte-calendar materially better/worse than 30dte-trading-bar?" and quantifies
the theta-honesty cost.

    python experiments/calendar_hold/run_ab.py            # N=200, default windows
    N_ITER=300 python experiments/calendar_hold/run_ab.py # heavier
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(exist_ok=True)

N_ITER = os.environ.get("N_ITER", "200")
WINDOWS = os.environ.get("WINDOWS", "2022,2024,2025,dip,22-now,5y,2020_crash")

VARIANTS = [
    ("A_baseline",   {"CALENDAR_HOLD": "0"}),
    ("C21_calendar", {"CALENDAR_HOLD": "1", "HOLD_CAL_DAYS": "21", "NOMINAL_CAL_DTE": "30"}),
    ("C15_calendar", {"CALENDAR_HOLD": "1", "HOLD_CAL_DAYS": "15", "NOMINAL_CAL_DTE": "30"}),
]


def run_variant(name, extra_env):
    rj = RESULTS / f"{name}.json"
    log = RESULTS / f"{name}.log"
    env = dict(os.environ)
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "N_ITER_OVERRIDE": N_ITER,
        "WINDOWS_OVERRIDE": WINDOWS,
        "MC_RESULTS_JSON": str(rj),
    })
    env.update(extra_env)
    print(f"\n=== {name}  (N={N_ITER}, windows={WINDOWS}, env={extra_env}) ===", flush=True)
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.run([sys.executable, "-u", "monte_carlo.py"],
                              cwd=str(ROOT), env=env, stdout=lf,
                              stderr=subprocess.STDOUT)
    if proc.returncode != 0 or not rj.exists():
        print(f"  !! {name} FAILED (rc={proc.returncode}); see {log}", flush=True)
        return None
    data = json.loads(rj.read_text())
    print(f"  done -> {rj.name}", flush=True)
    return data


def main():
    results = {}
    for name, extra in VARIANTS:
        results[name] = run_variant(name, extra)

    base = results.get("A_baseline")
    if not base:
        print("\nBaseline failed; cannot compare.")
        return

    win_labels = [w for w in WINDOWS.split(",") if w in base]

    def cell(d, w, k):
        return d.get(w, {}).get(k) if d else None

    print("\n" + "=" * 118)
    print("CALENDAR-HOLD A/B  —  WorstDD (primary) / MedRet / P(collapse)   "
          f"[N={N_ITER}]")
    print("=" * 118)
    hdr = f"{'window':<10}"
    for nm, _ in VARIANTS:
        hdr += f"{nm:>22}"
    print(hdr)
    print(f"{'':<10}" + ("  WorstDD   MedRet  Pcoll" * len(VARIANTS)))
    print("-" * 118)
    for w in win_labels:
        row = f"{w:<10}"
        for nm, _ in VARIANTS:
            d = results.get(nm)
            wd = cell(d, w, "worst_dd"); mr = cell(d, w, "med_ret"); pc = cell(d, w, "p_coll")
            row += f"  {wd:>7.1f}% {mr:>+10.0f}% {pc:>5.1f}%" if wd is not None else f"  {'--':>24}"
        print(row)

    # Deltas vs baseline on the headline windows
    print("\nDELTA vs baseline (negative WorstDD = improvement; collapse must stay 0):")
    for nm, _ in VARIANTS:
        if nm == "A_baseline":
            continue
        d = results.get(nm)
        if not d:
            continue
        print(f"  {nm}:")
        for w in win_labels:
            b_dd = cell(base, w, "worst_dd"); v_dd = cell(d, w, "worst_dd")
            b_pc = cell(base, w, "p_coll");   v_pc = cell(d, w, "p_coll")
            if b_dd is None or v_dd is None:
                continue
            ddd = v_dd - b_dd
            coll_flag = "" if (v_pc or 0) <= (b_pc or 0) + 0.01 else "  <-- COLLAPSE WORSE"
            print(f"    {w:<10} dWorstDD {ddd:>+6.2f}pp   "
                  f"baseP(coll) {b_pc:.1f}% -> {v_pc:.1f}%{coll_flag}")

    combined = RESULTS / "ab_combined.json"
    combined.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {combined}")
    print("\nREAD: C21 isolates theta-honesty (same ~exposure); C15 = literal halfway + honest theta.")
    print("Expect honest calendar variants to show DEEPER theta on slow/hard/dead-hold trades")
    print("(a realism fee, likely modestly LOWER return) — collapse-safety is the gate that matters.")


if __name__ == "__main__":
    main()
