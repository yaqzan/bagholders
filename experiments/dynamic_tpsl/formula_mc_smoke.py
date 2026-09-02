"""Smoke MC for the dynamic TP/SL formula.

Runs current fixed exits vs an experiment-local dynamic put-exit precompute.
No production files are changed. The dynamic precompute reads the formula
parameters produced by formula_bayes.py and monkey-patches only this process.

Default:
    python -u experiments/dynamic_tpsl/formula_mc_smoke.py --dte 15 --window 22-now --n 150
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from experiments.dynamic_tpsl import formula_bayes as fb

LOG_DIR = ROOT / "experiments" / "dynamic_tpsl" / "logs"

WINDOWS = {
    "2021": (date(2021, 1, 1), date(2021, 12, 31)),
    "2022": (date(2022, 1, 1), date(2022, 12, 31)),
    "2023": (date(2023, 1, 1), date(2023, 12, 31)),
    "2024": (date(2024, 1, 1), date(2024, 12, 31)),
    "2025": (date(2025, 1, 1), date(2025, 12, 31)),
    "dip": (date(2025, 11, 1), date(2026, 4, 15)),
    "22-now": (date(2022, 1, 1), date(2026, 4, 15)),
    "5y": (date(2021, 4, 15), date(2026, 4, 15)),
}
WINDOW_ORDER = ["2021", "2022", "2023", "2024", "2025", "dip", "22-now", "5y"]


def _latest_formula_json(dte: int, side: str) -> Path:
    paths = sorted(LOG_DIR.glob(f"formula_bayes_{dte}dte_{side}_*_seed*_n*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        paths = sorted(LOG_DIR.glob(f"formula_bayes_{dte}dte_{side}_seed*_n*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        raise FileNotFoundError(f"No formula_bayes JSON found for {dte}dte {side}")
    return paths[0]


def _load_mc(dte: int):
    if dte == 15:
        import monte_carlo_15dte as mc
    elif dte == 30:
        import monte_carlo as mc
    else:
        raise ValueError("dte must be 15 or 30")
    return mc


def _run_one(mc, label: str, start: date, end: date, version) -> dict:
    t0 = time.time()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = mc.run_window(label, start, end, version)
    seeded = out.get("seeded", {})
    seeded = {k: float(v) for k, v in seeded.items() if isinstance(v, (int, float))}
    seeded["duration_s"] = time.time() - t0
    return seeded


def _calibration_maps(dte: int, side: str):
    rows = fb.load_rows(dte, side)
    train = [r for r in rows if r.signal_date <= fb.TRAIN_END]
    stats = fb.build_stats(train)
    norm_by_date = {}
    for r in rows:
        norm_by_date[(r.signal_date, r.side)] = (r.count_norm, r.demand_norm)
    return stats, norm_by_date


def _patch_dynamic_put_precompute(mc, dte: int, params: dict, stats: dict, norm_by_date: dict,
                                  guard_override: dict | None = None,
                                  fixed_sl: bool = False):
    guard = dict(guard_override) if guard_override else fb.guardrails(dte, "put")
    original = mc.precompute_put_outcomes
    tpsl_rows = []

    def _derive_for_sig(sig, prem_mult: float):
        tier = mc.put_score_to_tier(int(sig.overall))
        count_norm, demand_norm = norm_by_date.get((sig.date, "put"), (0.0, 0.0))
        st = stats.get(("put", tier)) or stats[("put", "__global__")]
        row = SimpleNamespace(
            count_norm=count_norm,
            demand_norm=demand_norm,
            side="put",
            tier=tier,
            delta=float(mc.DELTA),
            premium_mult=float(prem_mult),
        )
        tp, sl, pressure, edge, tp_sigma = fb.derive_tpsl(row, st, params, guard)
        if fixed_sl:
            sl = guard.get("baseline_sl", guard["base_sl"])
        tpsl_rows.append((tp, sl, pressure, edge, tp_sigma))
        return tp, sl

    def _compute_dynamic_put_outcome(sym_bars, sig, put_stressed=False,
                                     spans_earn=False, j_pct=None):
        dates = [b[0] for b in sym_bars]
        closes = [float(b[1]) for b in sym_bars]
        highs = [float(b[2]) for b in sym_bars]
        lows = [float(b[3]) for b in sym_bars]
        opens = [float(b[4]) for b in sym_bars] if sym_bars and len(sym_bars[0]) >= 5 else closes
        try:
            base_idx = dates.index(sig.date)
        except ValueError:
            return None
        entry = closes[base_idx]
        if entry <= 0:
            return None
        vol = mc.realized_vol(closes, base_idx)
        if vol is None or vol <= 0:
            return None

        if dte == 15:
            prem_mult = mc.effective_premium_mult(vol, spans_earn, j_pct)
        else:
            prem_mult = mc.PREMIUM_MULT
        tp_pct, sl_pct = _derive_for_sig(sig, prem_mult)
        tp_sigma = abs(tp_pct) * prem_mult / mc.DELTA
        sl_sigma = abs(sl_pct) * prem_mult / mc.DELTA
        tp_level = entry * (1.0 - tp_sigma * vol / 100.0)
        sl_level = entry * (1.0 + sl_sigma * vol / 100.0)
        premium_pct = prem_mult * vol / 100.0

        end_idx = min(len(dates), base_idx + 1 + mc.HOLD_DAYS)
        if end_idx <= base_idx + 1:
            return None
        sl_hold = mc.PUT_SL_HOLD_BARS_MONDAY if sig.date.weekday() == 0 else mc.PUT_SL_HOLD_BARS_DEFAULT

        kind = "hard"
        exit_bar = mc.HOLD_DAYS
        fire_o = fire_c = fire_h = fire_l = None
        for i in range(base_idx + 1, end_idx):
            bar = i - base_idx
            tp_hit = lows[i] <= tp_level
            sl_hit = highs[i] >= sl_level and bar > sl_hold
            if tp_hit and sl_hit:
                kind, exit_bar = "both", bar
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                break
            if tp_hit:
                kind, exit_bar = "tp", bar
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                break
            if sl_hit:
                kind, exit_bar = "sl", bar
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                break

        if kind == "hard":
            last_idx = min(len(dates) - 1, base_idx + mc.HOLD_DAYS)
            fire_o, fire_c, fire_h, fire_l = opens[last_idx], closes[last_idx], highs[last_idx], lows[last_idx]

        out = dict(kind=kind, exit_bar=exit_bar, side="put", stressed=put_stressed,
                   premium_pct=premium_pct, vol=vol, entry=entry,
                   signal_date=sig.date,
                   fire_open=fire_o, fire_close=fire_c,
                   fire_high=fire_h, fire_low=fire_l,
                   fire_tp_level=tp_level, fire_sl_level=sl_level)
        if kind == "sl" and getattr(mc, "DEAD_HOLD_ENABLED", False):
            sl_fire_idx = base_idx + exit_bar
            if dte == 15:
                out["dead_hold"] = mc._compute_dh_put_15(
                    highs, lows, closes, opens, sl_fire_idx, end_idx, entry, premium_pct, base_idx)
            else:
                out["dead_hold"] = mc._compute_dead_hold_put(
                    highs, lows, closes, opens, sl_fire_idx, end_idx, entry, premium_pct, base_idx)
        return out

    def dynamic_precompute(signals, ph, breadth_dates=None, breadth_map=None,
                           ern_map=None, j_map=None, trading_days=None):
        outcomes = {}
        for sig in signals:
            sym_bars = ph.get(sig.symbol_id)
            if not sym_bars:
                continue
            put_stressed = False
            if getattr(mc, "PUT_BREADTH_MODE", "none") != "none" and breadth_dates is not None and breadth_map is not None:
                put_stressed = mc._is_put_stressed(breadth_dates, breadth_map, sig.date)
            ern_for_sym = ern_map.get(sig.symbol_id) if ern_map else None
            spans = mc._signal_spans_earnings(sig.date, ern_for_sym) if hasattr(mc, "_signal_spans_earnings") else False
            j_pct = j_map.get(sig.symbol_id) if j_map else None
            r = _compute_dynamic_put_outcome(
                sym_bars, sig, put_stressed=put_stressed, spans_earn=spans, j_pct=j_pct)
            if r is not None:
                mc._attach_earnings_span(r, ern_for_sym, trading_days)
                outcomes[(sig.symbol_id, sig.date)] = r
        return outcomes

    mc.precompute_put_outcomes = dynamic_precompute
    return original, tpsl_rows


def _patch_put_signal_filter(mc, params: dict, norm_by_date: dict,
                             min_pressure: float | None,
                             allowed_tiers: set[str] | None):
    original = mc.load_put_signals
    stats = {"before": 0, "after": 0}

    def filtered_load_put_signals(version, d_start, d_end):
        sigs = list(original(version, d_start, d_end))
        stats["before"] += len(sigs)
        kept = []
        for sig in sigs:
            tier = mc.put_score_to_tier(int(sig.overall))
            if allowed_tiers and tier not in allowed_tiers:
                continue
            if min_pressure is not None:
                count_norm, demand_norm = norm_by_date.get((sig.date, "put"), (0.0, 0.0))
                count_weight = float(params.get("count_weight", 0.5))
                mix = count_weight * count_norm + (1.0 - count_weight) * demand_norm
                pressure = fb._sigmoid(float(params.get("pressure_slope", 1.0))
                                       * (mix - float(params.get("pressure_mid", 0.5))))
                if pressure < min_pressure:
                    continue
            kept.append(sig)
        stats["after"] += len(kept)
        return kept

    mc.load_put_signals = filtered_load_put_signals
    return original, stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dte", type=int, choices=[15, 30], default=15)
    ap.add_argument("--window", choices=sorted(WINDOWS) + ["all"], default="22-now")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--formula-json", type=Path)
    ap.add_argument("--fixed-sl", action="store_true")
    ap.add_argument("--baseline-put-tp", type=float,
                    help="Override fixed baseline PUT_TP, e.g. 0.20 for execution-floor comparisons.")
    ap.add_argument("--baseline-put-sl", type=float,
                    help="Override fixed baseline PUT_SL, e.g. -0.20.")
    ap.add_argument("--min-put-pressure", type=float,
                    help="Candidate-only put admission filter on derived pressure [0,1].")
    ap.add_argument("--put-tier", action="append", choices=["put_top", "put_mid", "put_low"],
                    help="Candidate-only allowed put tier; repeatable.")
    ap.add_argument("--param", action="append", default=[],
                    help="Override formula param as name=value; repeatable.")
    args = ap.parse_args()

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["PYTHONHASHSEED"] = "0"
    if args.baseline_put_tp is not None:
        os.environ["PUT_TP_OV"] = str(args.baseline_put_tp)
    if args.baseline_put_sl is not None:
        os.environ["PUT_SL_OV"] = str(args.baseline_put_sl)

    formula_path = args.formula_json or _latest_formula_json(args.dte, "put")
    formula = json.loads(formula_path.read_text(encoding="utf-8"))
    params = formula["params"]
    guard = formula.get("guardrails")
    for item in args.param:
        name, raw = item.split("=", 1)
        try:
            params[name] = float(raw)
        except ValueError:
            params[name] = raw
    print(f"Formula: {formula_path}")
    if args.param:
        print(f"Param overrides: {args.param}")

    stats, norm_by_date = _calibration_maps(args.dte, "put")
    mc = _load_mc(args.dte)
    mc.N_ITER = args.n
    av = AlgorithmVersion.get_active_scores_version()
    labels = WINDOW_ORDER if args.window == "all" else [args.window]
    per_window = {}
    for label in labels:
        start, end = WINDOWS[label]
        print(f"\n=== Baseline fixed PUT_TP/SL: {args.dte} DTE {label} N={args.n} ===")
        baseline = _run_one(mc, label, start, end, av)
        print(
            f"baseline  ret={baseline.get('mean_ret', 0):+.2e}% "
            f"dd={baseline.get('worst_dd', 0):.2f}% "
            f"putTP={baseline.get('put_tp', 0):.2f}% "
            f"dur={baseline['duration_s']:.0f}s"
        )

        original, tpsl_rows = _patch_dynamic_put_precompute(
            mc, args.dte, params, stats, norm_by_date, guard_override=guard,
            fixed_sl=args.fixed_sl)
        filter_original = None
        filter_stats = None
        allowed_tiers = set(args.put_tier) if args.put_tier else None
        if args.min_put_pressure is not None or allowed_tiers:
            filter_original, filter_stats = _patch_put_signal_filter(
                mc, params, norm_by_date, args.min_put_pressure, allowed_tiers)
        try:
            print(f"\n=== Dynamic formula PUT_TP/SL: {args.dte} DTE {label} N={args.n} ===")
            dynamic = _run_one(mc, label, start, end, av)
        finally:
            mc.precompute_put_outcomes = original
            if filter_original is not None:
                mc.load_put_signals = filter_original

        if tpsl_rows:
            tp_avg = sum(r[0] for r in tpsl_rows) / len(tpsl_rows)
            sl_avg = sum(r[1] for r in tpsl_rows) / len(tpsl_rows)
            pressure_avg = sum(r[2] for r in tpsl_rows) / len(tpsl_rows)
        else:
            tp_avg = sl_avg = pressure_avg = 0.0
        print(
            f"dynamic   ret={dynamic.get('mean_ret', 0):+.2e}% "
            f"dd={dynamic.get('worst_dd', 0):.2f}% "
            f"putTP={dynamic.get('put_tp', 0):.2f}% "
            f"avgTP={tp_avg:.2%} avgSL={sl_avg:.2%} pressure={pressure_avg:.3f} "
            f"dur={dynamic['duration_s']:.0f}s"
        )
        if filter_stats:
            print(
                f"filter    puts={filter_stats['after']}/{filter_stats['before']} "
                f"min_pressure={args.min_put_pressure} tiers={sorted(allowed_tiers) if allowed_tiers else 'all'}"
            )
        print(
            f"delta     dd={dynamic.get('worst_dd', 0) - baseline.get('worst_dd', 0):+.2f}pp "
            f"putTP={dynamic.get('put_tp', 0) - baseline.get('put_tp', 0):+.2f}pp"
        )
        per_window[label] = {
            "baseline": baseline,
            "dynamic": dynamic,
            "dynamic_avg_tp": tp_avg,
            "dynamic_avg_sl": sl_avg,
            "dynamic_avg_pressure": pressure_avg,
            "filter": filter_stats,
        }

    print("\n=== Summary ===")
    print(f"{'window':>8s} {'baseDD':>7s} {'dynDD':>7s} {'dDD':>8s} {'basePTP':>8s} {'dynPTP':>8s} {'dPTP':>8s}")
    for label in labels:
        b = per_window[label]["baseline"]
        d = per_window[label]["dynamic"]
        print(
            f"{label:>8s} {b.get('worst_dd', 0):7.2f} {d.get('worst_dd', 0):7.2f} "
            f"{d.get('worst_dd', 0) - b.get('worst_dd', 0):+8.2f} "
            f"{b.get('put_tp', 0):8.2f} {d.get('put_tp', 0):8.2f} "
            f"{d.get('put_tp', 0) - b.get('put_tp', 0):+8.2f}"
        )

    out = {
        "dte": args.dte,
        "window": args.window,
        "n": args.n,
        "formula_json": str(formula_path),
        "baseline_put_tp": args.baseline_put_tp,
        "baseline_put_sl": args.baseline_put_sl,
        "windows": per_window,
    }
    suffix = "_fixedsl" if args.fixed_sl else ""
    if args.baseline_put_tp is not None:
        suffix += f"_ptp{int(args.baseline_put_tp * 100):02d}"
    if args.min_put_pressure is not None:
        suffix += f"_p{int(args.min_put_pressure * 100):02d}"
    if args.put_tier:
        suffix += "_" + "".join(t.replace("put_", "") for t in args.put_tier)
    out_path = LOG_DIR / f"formula_mc_smoke_{args.dte}dte_{args.window}_n{args.n}{suffix}.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"[saved] {out_path}")


if __name__ == "__main__":
    main()
