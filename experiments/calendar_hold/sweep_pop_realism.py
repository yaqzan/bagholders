"""Dead-hold popout-realism gate for the calendar hold-window decision.

The hold-window sweep (#89) showed return rises monotonically with hold length
under honest theta — cal27 (~near expiry) > cal21 > short holds. But a longer hold
leans harder on the DEAD-HOLD POPOUT: a resting-limit fill at -15% on a deeply
underwater call as it recovers, which is LEAST realistic near expiry (deep-OTM,
worst liquidity/spread).

This run charges the popout a half-spread (DH_POP_SLIP) and asks: does cal27's edge
over cal21 SURVIVE realistic popout fills? If yes -> "hold longer" is genuine TP
capture, standardize long. If the edge evaporates -> it was popout-fill optimism,
standardize shorter (more execution-robust).

  pop00 = 0.0    free limit (current model)
  pop03 = -0.03  3% half-spread on the popout (realistic-to-pessimistic near expiry)

Honest theta throughout (NOMINAL_CAL_DTE=30). 30 DTE instrument, Apex config.

    python experiments/calendar_hold/sweep_pop_realism.py
"""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results_pop"
RESULTS.mkdir(exist_ok=True)

N_ITER = os.environ.get("N_ITER", "300")
WINDOWS = os.environ.get("WINDOWS", "2022,dip,22-now,5y,2020_crash")
RK = "med_ret"

# (label, CALENDAR_HOLD, HOLD_CAL_DAYS, DH_POP_SLIP)
VARIANTS = [
    ("A_bar",      "0", "21", "0.0"),
    ("h21_pop00",  "1", "21", "0.0"),
    ("h21_pop03",  "1", "21", "-0.03"),
    ("h27_pop00",  "1", "27", "0.0"),
    ("h27_pop03",  "1", "27", "-0.03"),
    ("h30_pop00",  "1", "30", "0.0"),
    ("h30_pop03",  "1", "30", "-0.03"),
]


def run_variant(label, cal, hold, popslip):
    rj = RESULTS / f"{label}.json"
    log = RESULTS / f"{label}.log"
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "N_ITER_OVERRIDE": N_ITER, "WINDOWS_OVERRIDE": WINDOWS,
                "MC_RESULTS_JSON": str(rj),
                "CALENDAR_HOLD": cal, "HOLD_CAL_DAYS": hold,
                "NOMINAL_CAL_DTE": "30", "DH_POP_SLIP": popslip})
    print(f"=== {label}  cal={cal} hold={hold} popslip={popslip} ===", flush=True)
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.run([sys.executable, "-u", "monte_carlo.py"],
                              cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT)
    if proc.returncode != 0 or not rj.exists():
        print(f"  !! {label} FAILED rc={proc.returncode}; see {log}", flush=True)
        return None
    return json.loads(rj.read_text())


def main():
    results = {lbl: run_variant(lbl, c, h, p) for (lbl, c, h, p) in VARIANTS}
    wins = [w for w in WINDOWS.split(",")]

    def cell(d, w, k):
        return d.get(w, {}).get(k) if d else None

    def maxcoll(d):
        return max((cell(d, w, "p_coll") or 0.0) for w in wins) if d else 999.0

    print("\n" + "=" * 104)
    print(f"DEAD-HOLD POPOUT-REALISM GATE   MedRet by window   [N={N_ITER}]")
    print("=" * 104)
    hdr = f"{'variant':<11}{'maxColl':>8}"
    for w in wins:
        hdr += f"{w:>12}"
    hdr += f"{'5yDD':>7}"
    print(hdr); print("-" * 104)
    for lbl, _, _, _ in VARIANTS:
        d = results.get(lbl)
        line = f"{lbl:<11}{maxcoll(d):>7.1f}%"
        for w in wins:
            v = cell(d, w, RK)
            line += f"{v:>+11.0f}%" if v is not None else f"{'--':>12}"
        dd = cell(d, "5y", "worst_dd")
        line += f"{dd:>6.1f}%" if dd is not None else f"{'--':>7}"
        print(line)

    # The gate: does the hold-longer edge survive realistic popout cost?
    def hd(lbl, w):
        return cell(results.get(lbl), w, RK)

    print("\nPOPOUT-REALISM VERDICT (5y / 22-now MedRet):")
    for hold in ("21", "27", "30"):
        a = f"h{hold}_pop00"; b = f"h{hold}_pop03"
        for w in ("5y", "22-now"):
            v0, v3 = hd(a, w), hd(b, w)
            if v0 is not None and v3 is not None:
                drop = (v3 - v0) / max(abs(v0), 1e-9) * 100.0
                print(f"  hold={hold} {w:<7}: pop00={v0:>+10.0f}%  pop03={v3:>+10.0f}%  "
                      f"({drop:+.0f}% from realistic popout cost)")
    print("\n  KEY: if h27_pop03 still >> h21_pop03, hold-longer is real TP capture (standardize long).")
    print("       if h27_pop03 ~<= h21_pop03, the long-hold edge was popout optimism (standardize ~21).")

    (RESULTS / "pop_combined.json").write_text(json.dumps(results, indent=2))
    print(f"\nWrote {RESULTS / 'pop_combined.json'}")


if __name__ == "__main__":
    main()
