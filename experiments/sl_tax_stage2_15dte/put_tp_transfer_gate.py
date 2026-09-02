"""15 DTE PUT_TP transfer test after 30 DTE PUT_TP=0.14 ship.

Purpose:
  Check whether the 30 DTE Stage 2 SL-tax finding transfers to 15 DTE.
  Do NOT assume transfer: 15 DTE uses PREMIUM_MULT=1.29, HOLD_DAYS=7, and
  faster theta/bounded-fill dynamics.

Default run:
  candidates = 0.35 baseline, 0.20, 0.16, 0.14, 0.12
  N=300, seeded collision mode, 8 canonical windows.

Usage:
  PYTHONIOENCODING=utf-8 python -u experiments/sl_tax_stage2_15dte/put_tp_transfer_gate.py
  N_ITER=500 CANDIDATES=0.35,0.14 python -u experiments/sl_tax_stage2_15dte/put_tp_transfer_gate.py
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import os
import sys
import time
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "experiments" / "sl_tax_stage2_15dte"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WINDOWS = [
    ("2021", date(2021, 1, 1), date(2021, 12, 31)),
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023", date(2023, 1, 1), date(2023, 12, 31)),
    ("2024", date(2024, 1, 1), date(2024, 12, 31)),
    ("2025", date(2025, 1, 1), date(2025, 12, 31)),
    ("dip", date(2025, 11, 1), date(2026, 4, 15)),
    ("22-now", date(2022, 1, 1), date(2026, 4, 15)),
    ("5y", date(2021, 4, 15), date(2026, 4, 15)),
]


def run_one_window(label: str, start: date, end: date, version):
    import monte_carlo_15dte as mc15

    t0 = time.time()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wr = mc15.run_window(label, start, end, version)
    r = wr.get("seeded", {})
    return {
        "label": label,
        "duration_s": time.time() - t0,
        "mean_ret": float(r.get("mean_ret", 0)),
        "med_ret": float(r.get("med_ret", 0)),
        "worst_dd": float(r.get("worst_dd", 0)),
        "mean_dd": float(r.get("mean_dd", 0)),
        "p_collapse": float(r.get("p_coll", 0)),
        "call_tp": float(r.get("call_tp", 0)),
        "put_tp_rate": float(r.get("put_tp", 0)),
        "call_trades": float(r.get("call_trades", 0)),
        "put_trades": float(r.get("put_trades", 0)),
    }


def run_candidate(put_tp: float, version, n_iter: int):
    import monte_carlo_15dte as mc15

    os.environ["PUT_TP_OV"] = str(put_tp)
    os.environ["N_ITER_OVERRIDE"] = str(n_iter)
    os.environ["MC_NO_DB_PERSIST"] = "1"
    mc15.PUT_TP = put_tp
    mc15.PUT_TP_SIGMA = abs(put_tp) * mc15.PREMIUM_MULT / mc15.DELTA
    mc15.N_ITER = n_iter
    mc15.COLLISION_MODES = ["seeded"]

    print(f"\n=== 15 DTE PUT_TP={put_tp:.2f} (N={n_iter}) ===", flush=True)
    rows = []
    for label, start, end in WINDOWS:
        r = run_one_window(label, start, end, version)
        rows.append(r)
        print(
            f"  {label:>7s}  ret={r['mean_ret']:+.2e}%  dd={r['worst_dd']:5.1f}%  "
            f"col={r['p_collapse']:4.1f}%  putTP={r['put_tp_rate']:5.2f}%  "
            f"callTP={r['call_tp']:5.2f}%  ({r['duration_s']:.0f}s)",
            flush=True,
        )
    return {row["label"]: row for row in rows}


def gate_vs_baseline(baseline_w: dict, candidate_w: dict):
    annual = ["2021", "2022", "2023", "2024", "2025", "dip"]
    t4_delta = candidate_w["5y"]["worst_dd"] - baseline_w["5y"]["worst_dd"]
    t4 = t4_delta <= 1.0
    t5_violations = [
        (w, candidate_w[w]["worst_dd"] - baseline_w[w]["worst_dd"])
        for w in annual
        if candidate_w[w]["worst_dd"] - baseline_w[w]["worst_dd"] > 5.0
    ]
    t5 = not t5_violations
    t6_violations = [
        (w, candidate_w[w]["p_collapse"])
        for w in candidate_w
        if candidate_w[w]["p_collapse"] > 0
    ]
    t6 = not t6_violations
    base_ret = baseline_w["5y"]["mean_ret"]
    cand_ret = candidate_w["5y"]["mean_ret"]
    oom_delta = math.log10(cand_ret) - math.log10(base_ret) if base_ret > 0 and cand_ret > 0 else 0.0
    # For fantasy-scale positive gains, T7 is sanity-only. Mark as pass unless candidate regresses by >3 OOM.
    t7 = oom_delta >= -3.0
    return {
        "t4": t4,
        "t4_delta": t4_delta,
        "t5": t5,
        "t5_violations": t5_violations,
        "t6": t6,
        "t6_violations": t6_violations,
        "t7": t7,
        "oom_delta": oom_delta,
        "all_pass": t4 and t5 and t6 and t7,
    }


def main():
    from database.models.core import AlgorithmVersion

    av = AlgorithmVersion.get_active_scores_version()
    n_iter = int(os.environ.get("N_ITER", "300"))
    candidates_s = os.environ.get("CANDIDATES", "0.35,0.20,0.16,0.14,0.12")
    candidates = [float(x.strip()) for x in candidates_s.split(",") if x.strip()]
    if 0.35 not in candidates:
        candidates = [0.35] + candidates

    print(f"Active version: id={av.id} commit={av.git_commit}")
    print(f"Candidates: {candidates}  N={n_iter}")

    all_results = {}
    for put_tp in candidates:
        all_results[put_tp] = run_candidate(put_tp, av, n_iter)

    out_json = OUT_DIR / f"put_tp_transfer_n{n_iter}.json"
    out_json.write_text(json.dumps({str(k): v for k, v in all_results.items()}, indent=2))
    print(f"\n[saved] {out_json}")

    baseline = all_results[0.35]
    print("\n" + "=" * 100)
    print(f"15 DTE PUT_TP TRANSFER GATE (N={n_iter} x 8 windows vs baseline PUT_TP=0.35)")
    print("=" * 100)
    print(
        f"{'PUT_TP':>7s}  {'5y_DD':>7s}  {'Δ_DD':>8s}  {'22n_DD':>8s}  "
        f"{'5y_ret':>11s}  {'OOM_Δ':>7s}  {'PutTP5y':>8s}  T4 T5 T6 T7 PASS"
    )
    for put_tp in candidates:
        if put_tp == 0.35:
            continue
        cw = all_results[put_tp]
        gate = gate_vs_baseline(baseline, cw)
        cells = " ".join("Y" if gate[k] else "N" for k in ["t4", "t5", "t6", "t7"])
        print(
            f"{put_tp:>7.2f}  {cw['5y']['worst_dd']:>6.2f}%  {gate['t4_delta']:>+7.2f}pp  "
            f"{cw['22-now']['worst_dd']:>7.2f}%  {cw['5y']['mean_ret']:>+11.2e}%  "
            f"{gate['oom_delta']:>+7.2f}  {cw['5y']['put_tp_rate']:>7.2f}%  "
            f"{cells}  {'YES' if gate['all_pass'] else 'NO'}"
        )
        if gate["t5_violations"]:
            print(f"           T5 violations: {gate['t5_violations']}")

    print("\n=== Per-window WorstDD ===")
    print(f"{'window':>8s}  " + "  ".join(f"{put_tp:>5.2f}" for put_tp in candidates))
    for label, _, _ in WINDOWS:
        print(f"{label:>8s}  " + "  ".join(f"{all_results[put_tp][label]['worst_dd']:>5.1f}" for put_tp in candidates))


if __name__ == "__main__":
    main()
