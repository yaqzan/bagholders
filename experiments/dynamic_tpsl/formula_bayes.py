"""Dynamic TP/SL formula fit using WR7 geometry + opportunity pressure.

This is a Stage 2 research harness, not production wiring. It tests the
derived-rule framing:

    TP/SL = f(WR7 tier quality, MFE/MAE path geometry, same-day signal pressure)

The prior dynamic_tpsl null used daily signal count as a stress-regime switch.
This script uses count/demand only as an opportunity-cost term that compresses
targets when replacement signals are abundant.

Default:
    python -u experiments/dynamic_tpsl/formula_bayes.py --dte 15 --side put --calls 80

Outputs JSON under experiments/dynamic_tpsl/logs/.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from experiments._holdout import CUTOFF
from option_pricing import option_pnl_pct

LOG_DIR = ROOT / "experiments" / "dynamic_tpsl" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

WINDOW_START = date(2021, 4, 15)
WINDOW_END = min(date(2026, 4, 15), CUTOFF - timedelta(days=30))
TRAIN_END = date(2024, 12, 31)
EXEC_TP_FLOOR_15DTE = 0.20
EXEC_SL_FLOOR_15DTE = 0.20


@dataclass(frozen=True)
class GeometryStats:
    wr7: float
    mfe_p25: float
    mfe_p50: float
    mfe_p75: float
    mae_p50: float
    mae_win_p75: float


@dataclass(frozen=True)
class SignalRow:
    symbol: str
    signal_date: date
    side: str
    score: int
    tier: str
    entry: float
    vol_pct: float
    premium_mult: float
    delta: float
    total_dte: int
    hold_days: int
    put_sl_hold: int
    same_count: float
    same_demand: float
    all_count: float
    all_demand: float
    count_norm: float
    demand_norm: float
    wr7_win: int
    mfe_sigma: float
    mae_sigma: float
    future: tuple[tuple[int, float, float, float], ...]  # bar, high, low, close


def _sigmoid(x: float) -> float:
    if x >= 40:
        return 1.0
    if x <= -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _quantile(xs: list[float], q: float, default: float = 0.0) -> float:
    vals = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not vals:
        return default
    if len(vals) == 1:
        return vals[0]
    pos = q * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def _mean(xs: Iterable[float], default: float = 0.0) -> float:
    vals = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(vals) / len(vals) if vals else default


def _side_for_score(score: int) -> str:
    return "call" if score >= 50 else "put"


def _generic_wr7(side: str, entry: float, vol_pct: float, base_date: date,
                 future: tuple[tuple[int, date, float, float, float], ...]) -> int | None:
    """Directional WR7 check using assessment's generic asymmetric barriers."""
    if entry <= 0 or vol_pct <= 0:
        return None
    scale = math.sqrt(7.0 / 30.0)
    if side == "put":
        k = 1.0 * scale
        m = 2.0 * scale
        target = entry * (1.0 - k * vol_pct / 100.0)
        stop = entry * (1.0 + m * vol_pct / 100.0)
    else:
        k = 2.0 * scale
        m = 5.0 * scale
        target = entry * (1.0 + k * vol_pct / 100.0)
        stop = entry * (1.0 - m * vol_pct / 100.0)
    cutoff = base_date + timedelta(days=7)
    saw_bar = False
    for _bar, d, high, low, close in future:
        if d > cutoff:
            break
        saw_bar = True
        if side == "put":
            tp = low <= target
            sl = high >= stop
            if tp and sl:
                return 1 if close < entry else 0
        else:
            tp = high >= target
            sl = low <= stop
            if tp and sl:
                return 1 if close > entry else 0
        if tp:
            return 1
        if sl:
            return 0
    return 0 if saw_bar else None


def _path_geometry(side: str, entry: float, vol_pct: float,
                   future: tuple[tuple[int, float, float, float], ...]) -> tuple[float, float]:
    if entry <= 0 or vol_pct <= 0 or not future:
        return 0.0, 0.0
    if side == "put":
        mfe_pct = max((entry - low) / entry * 100.0 for _bar, _high, low, _close in future)
        mae_pct = max((high - entry) / entry * 100.0 for _bar, high, _low, _close in future)
    else:
        mfe_pct = max((high - entry) / entry * 100.0 for _bar, high, _low, _close in future)
        mae_pct = max((entry - low) / entry * 100.0 for _bar, _high, low, _close in future)
    return max(0.0, mfe_pct / vol_pct), max(0.0, mae_pct / vol_pct)


def _load_mc(dte: int):
    if dte == 15:
        import monte_carlo_15dte as mc
    elif dte == 30:
        import monte_carlo as mc
    else:
        raise ValueError("dte must be 15 or 30")
    return mc


def _tier(mc, side: str, score: int) -> str:
    return mc.put_score_to_tier(score) if side == "put" else mc.score_to_tier(score)


def _tier_alloc(mc, side: str, score: int) -> float:
    tier = _tier(mc, side, score)
    table = mc.PUT_TIER_ALLOC if side == "put" else mc.TIER_ALLOC
    return float(table.get(tier, 0.0))


def _put_sl_hold(mc, d: date) -> int:
    return int(mc.PUT_SL_HOLD_BARS_MONDAY if d.weekday() == 0 else mc.PUT_SL_HOLD_BARS_DEFAULT)


def load_rows(dte: int, side_filter: str) -> list[SignalRow]:
    mc = _load_mc(dte)
    av = AlgorithmVersion.get_active_scores_version()
    start = WINDOW_START
    end = WINDOW_END

    call_sigs = mc.load_signals(av, start, end) if side_filter in ("call", "both") else []
    put_sigs = mc.load_put_signals(av, start, end) if side_filter in ("put", "both") else []
    all_sigs = list(call_sigs) + list(put_sigs)
    sym_ids = {s.symbol_id for s in all_sigs}
    ph = mc.load_price_history(sym_ids, start, end)

    day_side_count: dict[tuple[date, str], float] = {}
    day_side_demand: dict[tuple[date, str], float] = {}
    for sig in call_sigs:
        d = sig.date
        alloc = _tier_alloc(mc, "call", int(sig.overall))
        day_side_count[(d, "call")] = day_side_count.get((d, "call"), 0.0) + 1.0
        day_side_demand[(d, "call")] = day_side_demand.get((d, "call"), 0.0) + alloc
    for sig in put_sigs:
        d = sig.date
        alloc = _tier_alloc(mc, "put", int(sig.overall))
        day_side_count[(d, "put")] = day_side_count.get((d, "put"), 0.0) + 1.0
        day_side_demand[(d, "put")] = day_side_demand.get((d, "put"), 0.0) + alloc

    side_rows = []
    for sig in all_sigs:
        score = int(sig.overall)
        side = _side_for_score(score)
        if side_filter != "both" and side != side_filter:
            continue
        bars = ph.get(sig.symbol_id)
        if not bars:
            continue
        date_to_idx = {b[0]: i for i, b in enumerate(bars)}
        base_idx = date_to_idx.get(sig.date)
        if base_idx is None:
            continue
        closes = [float(b[1]) for b in bars]
        vol = mc.realized_vol(closes, base_idx)
        if vol is None or vol <= 0:
            continue
        entry = closes[base_idx]
        if entry <= 0:
            continue

        # Need enough forward bars for hard-sell proxy.
        fut_with_dates = []
        fut = []
        for j in range(base_idx + 1, min(len(bars), base_idx + 1 + mc.HOLD_DAYS)):
            d, _close, high, low = bars[j][0], float(bars[j][1]), float(bars[j][2]), float(bars[j][3])
            close = float(bars[j][1])
            bar = j - base_idx
            fut_with_dates.append((bar, d, high, low, close))
            fut.append((bar, high, low, close))
        if len(fut) < mc.HOLD_DAYS:
            continue

        wr7 = _generic_wr7(side, entry, vol, sig.date, tuple(fut_with_dates))
        if wr7 is None:
            continue
        mfe_s, mae_s = _path_geometry(side, entry, vol, tuple(fut))

        same_count = day_side_count.get((sig.date, side), 0.0)
        same_demand = day_side_demand.get((sig.date, side), 0.0)
        other = "call" if side == "put" else "put"
        all_count = same_count + day_side_count.get((sig.date, other), 0.0)
        all_demand = same_demand + day_side_demand.get((sig.date, other), 0.0)
        side_rows.append({
            "symbol": sig.symbol_id,
            "signal_date": sig.date,
            "side": side,
            "score": score,
            "tier": _tier(mc, side, score),
            "entry": entry,
            "vol_pct": vol,
            "premium_mult": float(mc.PREMIUM_MULT),
            "delta": float(mc.DELTA),
            "total_dte": 15 if dte == 15 else 30,
            "hold_days": int(mc.HOLD_DAYS),
            "put_sl_hold": _put_sl_hold(mc, sig.date) if side == "put" else 0,
            "same_count": same_count,
            "same_demand": same_demand,
            "all_count": all_count,
            "all_demand": all_demand,
            "wr7_win": int(wr7),
            "mfe_sigma": mfe_s,
            "mae_sigma": mae_s,
            "future": tuple(fut),
        })

    q_count = _quantile([r["same_count"] for r in side_rows], 0.95, 1.0) or 1.0
    q_demand = _quantile([r["same_demand"] for r in side_rows], 0.95, 1.0) or 1.0
    rows = []
    for r in side_rows:
        rows.append(SignalRow(
            **r,
            count_norm=_clamp(r["same_count"] / q_count, 0.0, 1.0),
            demand_norm=_clamp(r["same_demand"] / q_demand, 0.0, 1.0),
        ))
    return rows


def build_stats(train_rows: list[SignalRow]) -> dict[tuple[str, str], GeometryStats]:
    grouped: dict[tuple[str, str], list[SignalRow]] = {}
    for r in train_rows:
        grouped.setdefault((r.side, r.tier), []).append(r)
        grouped.setdefault((r.side, "__global__"), []).append(r)
    stats = {}
    for key, rows in grouped.items():
        wr7 = _mean([r.wr7_win for r in rows], 0.5)
        mfe = [r.mfe_sigma for r in rows]
        mae = [r.mae_sigma for r in rows]
        mae_win = [r.mae_sigma for r in rows if r.wr7_win == 1]
        stats[key] = GeometryStats(
            wr7=wr7,
            mfe_p25=_quantile(mfe, 0.25, 0.1),
            mfe_p50=_quantile(mfe, 0.50, 0.2),
            mfe_p75=_quantile(mfe, 0.75, 0.4),
            mae_p50=_quantile(mae, 0.50, 0.1),
            mae_win_p75=_quantile(mae_win, 0.75, _quantile(mae, 0.50, 0.1)),
        )
    return stats


def derive_tpsl(row: SignalRow, st: GeometryStats, p: dict[str, float],
                guard: dict[str, float]) -> tuple[float, float, float, float, float]:
    """Return (tp_pct, sl_pct, pressure, edge, tp_sigma)."""
    pressure_mix = (
        p["count_weight"] * row.count_norm
        + (1.0 - p["count_weight"]) * row.demand_norm
    )
    pressure = _sigmoid(p["pressure_slope"] * (pressure_mix - p["pressure_mid"]))
    edge = _clamp((st.wr7 - p["edge_floor"]) / max(1e-6, 1.0 - p["edge_floor"]), 0.0, 1.0)

    if p.get("_family") == "anchored":
        edge_shape = edge ** p["edge_power"]
        mfe_shape = _clamp((st.mfe_p50 - st.mfe_p25) / max(st.mfe_p50, 1e-6), 0.0, 1.0)
        mfe_shape = mfe_shape ** p["mfe_power"]
        tp_mult = 1.0
        tp_mult += p["tp_widen_k"] * (1.0 - pressure) * edge_shape * mfe_shape
        tp_mult -= p["tp_compress_k"] * pressure / max(edge + p["edge_cushion"], 0.05)
        tp_mult = _clamp(tp_mult, 0.55, 2.00)
        anchor_tp = guard.get("anchor_tp", max(guard["base_tp"], guard["tp_min"]))
        tp_pct = _clamp(anchor_tp * tp_mult, guard["tp_min"], guard["tp_max"])

        mae_shape = _clamp(st.mae_win_p75 / max(st.mae_p50, 1e-6), 0.5, 2.0)
        sl_mult = 1.0
        sl_mult += p["sl_widen_k"] * (1.0 - pressure) * edge_shape * mae_shape
        sl_mult -= p["sl_tighten_k"] * pressure
        sl_mult = _clamp(sl_mult, 0.60, 1.40)
        anchor_sl = guard.get("anchor_sl", -max(abs(guard["base_sl"]), guard["sl_min"]))
        sl_pct = -_clamp(abs(anchor_sl) * sl_mult, guard["sl_min"], guard["sl_max"])
        tp_sigma = abs(tp_pct) * row.premium_mult / row.delta
        return tp_pct, sl_pct, pressure, edge, tp_sigma

    lower_span = max(0.0, st.mfe_p50 - st.mfe_p25)
    upper_span = max(0.0, st.mfe_p75 - st.mfe_p50)
    anchor = st.mfe_p50
    anchor += p["tp_edge_w"] * edge * (1.0 - pressure) * upper_span
    anchor -= p["tp_pressure_w"] * pressure * lower_span
    anchor = max(st.mfe_p25 * 0.50, anchor)

    recycle_denom = 1.0 + p["tp_recycle_k"] * pressure / max(edge + p["edge_cushion"], 0.05)
    tp_sigma = max(0.01, p["tp_scale"] * anchor / recycle_denom)
    tp_pct = _clamp(tp_sigma * row.delta / row.premium_mult, guard["tp_min"], guard["tp_max"])

    mae_anchor = max(st.mae_win_p75, st.mae_p50, 0.01)
    sl_sigma = p["sl_mult"] * mae_anchor * (1.0 + p["sl_edge_w"] * edge)
    sl_sigma /= 1.0 + p["sl_pressure_k"] * pressure
    sl_pct = -_clamp(sl_sigma * row.delta / row.premium_mult, guard["sl_min"], guard["sl_max"])
    return tp_pct, sl_pct, pressure, edge, tp_sigma


def eval_trade(row: SignalRow, tp_pct: float, sl_pct: float) -> tuple[str, float, int]:
    side = row.side
    tp_sigma = abs(tp_pct) * row.premium_mult / row.delta
    sl_sigma = abs(sl_pct) * row.premium_mult / row.delta
    premium_pct = row.premium_mult * row.vol_pct / 100.0
    entry = row.entry
    for bar, high, low, close in row.future:
        if side == "put":
            tp_level = entry * (1.0 - tp_sigma * row.vol_pct / 100.0)
            sl_level = entry * (1.0 + sl_sigma * row.vol_pct / 100.0)
            tp_hit = low <= tp_level
            sl_hit = high >= sl_level and bar > row.put_sl_hold
            if tp_hit and sl_hit:
                tp_hit = close < entry
                sl_hit = not tp_hit
            if tp_hit:
                # Match canonical bounded-fill semantics: a TP trigger fills
                # limit-or-better inside the trigger bar. Use midpoint as the
                # deterministic expectation for the Bayesian proxy.
                u_fire = (low + tp_level) / 2.0
                pnl = option_pnl_pct("put", u_fire, entry, bar, premium_pct,
                                     total_dte=row.total_dte, delta=row.delta)
                return "tp", pnl, bar
            if sl_hit:
                pnl = option_pnl_pct("put", sl_level, entry, bar, premium_pct,
                                     total_dte=row.total_dte, delta=row.delta)
                return "sl", pnl, bar
        else:
            tp_level = entry * (1.0 + tp_sigma * row.vol_pct / 100.0)
            sl_level = entry * (1.0 - sl_sigma * row.vol_pct / 100.0)
            tp_hit = high >= tp_level
            sl_hit = low <= sl_level
            if tp_hit and sl_hit:
                tp_hit = close > entry
                sl_hit = not tp_hit
            if tp_hit:
                # Limit-or-better midpoint, mirroring monte_carlo.resolve().
                u_fire = (tp_level + high) / 2.0
                pnl = option_pnl_pct("call", u_fire, entry, bar, premium_pct,
                                     total_dte=row.total_dte, delta=row.delta)
                return "tp", pnl, bar
            if sl_hit:
                pnl = option_pnl_pct("call", sl_level, entry, bar, premium_pct,
                                     total_dte=row.total_dte, delta=row.delta)
                return "sl", pnl, bar
    bar, _high, _low, close = row.future[-1]
    pnl = option_pnl_pct(side, close, entry, bar, premium_pct,
                         total_dte=row.total_dte, delta=row.delta)
    return "hard", pnl, bar


def fixed_tpsl(mc, side: str) -> tuple[float, float]:
    if side == "put":
        return float(mc.PUT_TP), float(mc.PUT_SL)
    return float(mc.TP_BASE), float(mc.SL_BASE)


def baseline_tpsl(mc, side: str, guard: dict[str, float] | None = None) -> tuple[float, float]:
    tp, sl = fixed_tpsl(mc, side)
    if guard:
        tp = float(guard.get("baseline_tp", tp))
        sl = float(guard.get("baseline_sl", sl))
    return tp, sl


def summarize(outcomes: list[tuple[str, float, int, float, float, float]]) -> dict[str, float]:
    n = len(outcomes)
    if n == 0:
        return {}
    kinds = [o[0] for o in outcomes]
    pnls = [o[1] for o in outcomes]
    bars = [o[2] for o in outcomes]
    pressures = [o[3] for o in outcomes]
    tp_pcts = [o[4] for o in outcomes]
    sl_pcts = [o[5] for o in outcomes]
    pd = [pnl / max(bar, 1) for _kind, pnl, bar, _pr, _tp, _sl in outcomes]
    wpd = [pnl / max(bar, 1) * (1.0 + 1.5 * pr) for _kind, pnl, bar, pr, _tp, _sl in outcomes]
    return {
        "n": n,
        "tp_rate": kinds.count("tp") / n,
        "sl_rate": kinds.count("sl") / n,
        "hard_rate": kinds.count("hard") / n,
        "mean_pnl": _mean(pnls),
        "median_pnl": statistics.median(pnls),
        "mean_premium_day": _mean(pd),
        "weighted_premium_day": _mean(wpd),
        "avg_bars": _mean(bars),
        "avg_pressure": _mean(pressures),
        "avg_tp_pct": _mean(tp_pcts),
        "avg_sl_pct": _mean(sl_pcts),
        "p10_pnl": _quantile(pnls, 0.10),
        "p05_pnl": _quantile(pnls, 0.05),
    }


def evaluate_formula(rows: list[SignalRow], stats: dict[tuple[str, str], GeometryStats],
                     params: dict[str, float], guard: dict[str, float]) -> dict[str, float]:
    outcomes = []
    for r in rows:
        st = stats.get((r.side, r.tier)) or stats[(r.side, "__global__")]
        tp, sl, pressure, _edge, _tp_sigma = derive_tpsl(r, st, params, guard)
        kind, pnl, bars = eval_trade(r, tp, sl)
        outcomes.append((kind, pnl, bars, pressure, tp, sl))
    return summarize(outcomes)


def evaluate_fixed(rows: list[SignalRow], side: str, dte: int,
                   guard: dict[str, float] | None = None) -> dict[str, float]:
    mc = _load_mc(dte)
    tp, sl = baseline_tpsl(mc, side, guard)
    outcomes = []
    for r in rows:
        kind, pnl, bars = eval_trade(r, tp, sl)
        outcomes.append((kind, pnl, bars, 0.0, tp, sl))
    return summarize(outcomes)


def guardrails(dte: int, side: str, exec_tp_floor: float | None = None,
               exec_sl_floor: float | None = None) -> dict[str, float]:
    mc = _load_mc(dte)
    base_tp, base_sl = fixed_tpsl(mc, side)
    if dte == 15:
        exec_tp_floor = EXEC_TP_FLOOR_15DTE if exec_tp_floor is None else exec_tp_floor
        exec_sl_floor = EXEC_SL_FLOOR_15DTE if exec_sl_floor is None else exec_sl_floor
    exec_tp_floor = 0.0 if exec_tp_floor is None else max(0.0, exec_tp_floor)
    exec_sl_floor = 0.0 if exec_sl_floor is None else max(0.0, exec_sl_floor)

    def _with_exec_floor(tp_min: float, tp_max: float, sl_min: float, sl_max: float) -> dict[str, float]:
        tp_min = min(tp_max, max(tp_min, exec_tp_floor))
        sl_min = min(sl_max, max(sl_min, exec_sl_floor))
        baseline_tp = max(base_tp, tp_min)
        baseline_sl = -max(abs(base_sl), sl_min)
        return {
            "tp_min": tp_min,
            "tp_max": tp_max,
            "sl_min": sl_min,
            "sl_max": sl_max,
            "base_tp": base_tp,
            "base_sl": base_sl,
            "anchor_tp": baseline_tp,
            "anchor_sl": baseline_sl,
            "baseline_tp": baseline_tp,
            "baseline_sl": baseline_sl,
            "exec_tp_floor": exec_tp_floor,
            "exec_sl_floor": exec_sl_floor,
        }

    if side == "put":
        if dte == 15:
            return _with_exec_floor(0.025, 0.50, 0.08, 0.35)
        return _with_exec_floor(0.04, 0.40, 0.10, 0.40)
    if dte == 15:
        return _with_exec_floor(0.08, 0.50, 0.10, 0.45)
    return _with_exec_floor(0.10, 0.55, 0.10, 0.50)


PARAM_NAMES_FREE = [
    "count_weight",
    "pressure_mid",
    "pressure_slope",
    "edge_floor",
    "edge_cushion",
    "tp_scale",
    "tp_edge_w",
    "tp_pressure_w",
    "tp_recycle_k",
    "sl_mult",
    "sl_edge_w",
    "sl_pressure_k",
]

PARAM_NAMES_ANCHORED = [
    "count_weight",
    "pressure_mid",
    "pressure_slope",
    "edge_floor",
    "edge_cushion",
    "edge_power",
    "mfe_power",
    "tp_widen_k",
    "tp_compress_k",
    "sl_widen_k",
    "sl_tighten_k",
]


def param_names(family: str) -> list[str]:
    return PARAM_NAMES_ANCHORED if family == "anchored" else PARAM_NAMES_FREE


def params_from_vector(x: list[float], family: str) -> dict[str, float]:
    params = {name: float(val) for name, val in zip(param_names(family), x)}
    params["_family"] = family
    return params


def objective(summary: dict[str, float], baseline: dict[str, float]) -> float:
    """Higher is better.

    Stage 2's job is option capture and cash recycling. A formula that lifts
    per-trade mean PnL by waiting for larger winners but gives up TP rate can
    still be a portfolio DD loser. Treat TP-rate preservation as a hard-ish
    term, then optimize pressure-weighted premium-day EV inside that boundary.
    """
    score = summary["weighted_premium_day"]
    score += 0.10 * (summary["tp_rate"] - baseline["tp_rate"])
    # Strongly reject "wide TP, low hit-rate" artifacts. The first MC smoke
    # showed a -13pp PutTP hit converts into DD/compound failure.
    if summary["tp_rate"] < baseline["tp_rate"] - 0.01:
        score -= 1.50 * (baseline["tp_rate"] - 0.01 - summary["tp_rate"])
    # Keep raw expectancy from slipping too far under the fixed baseline.
    if summary["mean_pnl"] < baseline["mean_pnl"] - 0.02:
        score -= 3.0 * (baseline["mean_pnl"] - 0.02 - summary["mean_pnl"])
    if summary["p05_pnl"] < baseline["p05_pnl"] - 0.05:
        score -= 1.5 * (baseline["p05_pnl"] - 0.05 - summary["p05_pnl"])
    if summary["avg_bars"] > baseline["avg_bars"] + 0.5:
        score -= 0.05 * (summary["avg_bars"] - baseline["avg_bars"] - 0.5)
    return score


def fit(rows: list[SignalRow], stats: dict[tuple[str, str], GeometryStats],
        side: str, dte: int, calls: int, seed: int,
        family: str, exec_tp_floor: float | None,
        exec_sl_floor: float | None) -> tuple[dict[str, float], list[dict]]:
    train = [r for r in rows if r.signal_date <= TRAIN_END]
    guard = guardrails(dte, side, exec_tp_floor, exec_sl_floor)
    baseline = evaluate_fixed(train, side, dte, guard)

    if family == "anchored":
        space_bounds = [
            (0.0, 1.0),    # count_weight
            (0.30, 0.90),  # pressure_mid; avoid saturating ordinary put flow
            (0.5, 8.0),    # pressure_slope
            (0.45, 0.75),  # edge_floor
            (0.05, 0.40),  # edge_cushion
            (0.5, 3.0),    # edge_power
            (0.5, 3.0),    # mfe_power
            (0.0, 1.5),    # tp_widen_k
            (0.0, 0.8),    # tp_compress_k
            (0.0, 0.6),    # sl_widen_k
            (0.0, 0.6),    # sl_tighten_k
        ]
    else:
        space_bounds = [
            (0.0, 1.0),    # count_weight
            (0.05, 0.95),  # pressure_mid
            (0.5, 8.0),    # pressure_slope
            (0.45, 0.75),  # edge_floor
            (0.05, 0.40),  # edge_cushion
            (0.35, 1.60),  # tp_scale
            (0.0, 2.0),    # tp_edge_w
            (0.0, 2.0),    # tp_pressure_w
            (0.0, 5.0),    # tp_recycle_k
            (0.60, 2.80),  # sl_mult
            (0.0, 2.0),    # sl_edge_w
            (0.0, 4.0),    # sl_pressure_k
        ]

    traces: list[dict] = []

    def eval_x(x):
        p = params_from_vector(list(x), family)
        s = evaluate_formula(train, stats, p, guard)
        util = objective(s, baseline)
        traces.append({"params": p, "utility": util, "summary": s})
        return -util

    try:
        from skopt import forest_minimize
        from skopt.space import Real
        names = param_names(family)
        space = [Real(lo, hi, name=name) for name, (lo, hi) in zip(names, space_bounds)]
        res = forest_minimize(
            eval_x,
            space,
            n_calls=calls,
            n_initial_points=min(24, max(8, calls // 3)),
            random_state=seed,
            base_estimator="ET",
            acq_func="EI",
        )
        best = params_from_vector(list(res.x), family)
    except Exception:
        rng = random.Random(seed)
        best = None
        best_score = -1e9
        for _ in range(calls):
            x = [rng.uniform(lo, hi) for lo, hi in space_bounds]
            loss = eval_x(x)
            score = -loss
            if score > best_score:
                best_score = score
                best = params_from_vector(x, family)
        assert best is not None

    traces.sort(key=lambda r: r["utility"], reverse=True)
    return best, traces


def print_summary(label: str, summary: dict[str, float]) -> None:
    print(
        f"{label:<18} n={summary.get('n', 0):>5} "
        f"TP={summary.get('tp_rate', 0)*100:>6.2f}% "
        f"SL={summary.get('sl_rate', 0)*100:>6.2f}% "
        f"Hard={summary.get('hard_rate', 0)*100:>6.2f}% "
        f"pnl={summary.get('mean_pnl', 0):>+7.3f} "
        f"pnl/day={summary.get('mean_premium_day', 0):>+7.3f} "
        f"w_pnl/day={summary.get('weighted_premium_day', 0):>+7.3f} "
        f"bars={summary.get('avg_bars', 0):>4.2f} "
        f"TPpct={summary.get('avg_tp_pct', 0):>5.2%} "
        f"SLpct={summary.get('avg_sl_pct', 0):>6.2%}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dte", type=int, choices=[15, 30], default=15)
    ap.add_argument("--side", choices=["put", "call", "both"], default="put")
    ap.add_argument("--calls", type=int, default=80)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--family", choices=["anchored", "free"], default="anchored")
    ap.add_argument("--exec-tp-floor", type=float, default=None,
                    help="Minimum actionable TP. Defaults to 20%% for 15 DTE; use 0 to disable.")
    ap.add_argument("--exec-sl-floor", type=float, default=None,
                    help="Minimum actionable absolute SL. Defaults to 20%% for 15 DTE; use 0 to disable.")
    args = ap.parse_args()

    if args.side == "both":
        for side in ("call", "put"):
            main_for_side(args.dte, side, args.calls, args.seed, args.family,
                          args.exec_tp_floor, args.exec_sl_floor)
    else:
        main_for_side(args.dte, args.side, args.calls, args.seed, args.family,
                      args.exec_tp_floor, args.exec_sl_floor)


def main_for_side(dte: int, side: str, calls: int, seed: int, family: str,
                  exec_tp_floor: float | None, exec_sl_floor: float | None) -> None:
    t0 = time.perf_counter()
    print(f"\n=== Dynamic TP/SL formula fit: {dte} DTE {side.upper()} ({family}) ===", flush=True)
    print(f"Window: {WINDOW_START} -> {WINDOW_END}  train<= {TRAIN_END}  cutoff={CUTOFF}")
    rows = load_rows(dte, side)
    train = [r for r in rows if r.signal_date <= TRAIN_END]
    valid = [r for r in rows if r.signal_date > TRAIN_END]
    print(f"Rows: total={len(rows):,} train={len(train):,} valid={len(valid):,}", flush=True)
    if len(train) < 100 or len(valid) < 50:
        raise RuntimeError("Not enough rows for formula fit")

    stats = build_stats(train)
    print("\nTier geometry anchors (train):")
    for key, st in sorted(stats.items()):
        if key[0] != side or key[1] == "__global__":
            continue
        print(
            f"  {key[1]:>9s}: WR7={st.wr7*100:5.2f}% "
            f"MFEσ p25/p50/p75={st.mfe_p25:.3f}/{st.mfe_p50:.3f}/{st.mfe_p75:.3f} "
            f"MAEσ win_p75={st.mae_win_p75:.3f}"
        )

    guard = guardrails(dte, side, exec_tp_floor, exec_sl_floor)
    current_train = evaluate_fixed(train, side, dte)
    current_valid = evaluate_fixed(valid, side, dte)
    fixed_train = evaluate_fixed(train, side, dte, guard)
    fixed_valid = evaluate_fixed(valid, side, dte, guard)
    print("\nFixed current baseline:")
    print_summary("train_current", current_train)
    print_summary("valid_current", current_valid)
    print("\nExecution-floor baseline:")
    print_summary("train_floor", fixed_train)
    print_summary("valid_floor", fixed_valid)

    best, traces = fit(rows, stats, side, dte, calls, seed, family,
                       exec_tp_floor, exec_sl_floor)
    form_train = evaluate_formula(train, stats, best, guard)
    form_valid = evaluate_formula(valid, stats, best, guard)
    print("\nBest formula params:")
    for k in param_names(family):
        print(f"  {k:<16} = {best[k]:.6f}")
    print("\nFormula result:")
    print_summary("train_formula", form_train)
    print_summary("valid_formula", form_valid)

    # Pressure bucket readout on validation: confirms N is doing work rather
    # than formula degenerating to a fixed constant.
    print("\nValidation by same-side pressure tercile:")
    valid_sorted = sorted(valid, key=lambda r: (r.count_norm + r.demand_norm) / 2.0)
    chunks = [
        ("lowP", valid_sorted[: len(valid_sorted) // 3]),
        ("midP", valid_sorted[len(valid_sorted) // 3: 2 * len(valid_sorted) // 3]),
        ("highP", valid_sorted[2 * len(valid_sorted) // 3:]),
    ]
    for label, chunk in chunks:
        if not chunk:
            continue
        print_summary(f"{label}_formula", evaluate_formula(chunk, stats, best, guard))

    out = {
        "dte": dte,
        "side": side,
        "family": family,
        "window": {"start": WINDOW_START.isoformat(), "end": WINDOW_END.isoformat(),
                   "train_end": TRAIN_END.isoformat(), "cutoff": CUTOFF.isoformat()},
        "rows": {"total": len(rows), "train": len(train), "valid": len(valid)},
        "guardrails": guard,
        "params": best,
        "current_train": current_train,
        "current_valid": current_valid,
        "fixed_train": fixed_train,
        "fixed_valid": fixed_valid,
        "formula_train": form_train,
        "formula_valid": form_valid,
        "top10": traces[:10],
        "elapsed_s": time.perf_counter() - t0,
    }
    floor_tag = f"_floor{int(guard['tp_min'] * 100):02d}"
    out_path = LOG_DIR / f"formula_bayes_{dte}dte_{side}_{family}{floor_tag}_seed{seed}_n{calls}.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {out_path}")
    print(f"elapsed={out['elapsed_s']:.1f}s", flush=True)


if __name__ == "__main__":
    main()
