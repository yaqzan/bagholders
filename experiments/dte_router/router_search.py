"""Search deterministic 30DTE/15DTE routing overlays on active v60 rows.

This is a Stage 3 research runner: it keeps Score.overall frozen and chooses
which DTE outcome model to use per entry. It does not write score rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DTE = 30
HOLDOUT_DISABLE = True  # Stage 3 portfolio validation intentionally tests holdout/stress windows.

import backtest_cascade as bt30  # noqa: E402
import backtest_cascade_15dte as bt15  # noqa: E402
import portfolio_profiles  # noqa: E402
from database.models.core import AlgorithmVersion, EarningsDate, MarketBreadth, Stock  # noqa: E402
from iv_crush_model import compute_effective_date  # noqa: E402


WINDOWS = [
    ("2021", date(2021, 1, 1), date(2021, 12, 31)),
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023", date(2023, 1, 1), date(2023, 12, 31)),
    ("2024", date(2024, 1, 1), date(2024, 12, 31)),
    ("2025", date(2025, 1, 1), date(2025, 12, 31)),
    ("dip", date(2025, 11, 1), date(2026, 4, 24)),
    ("22-now", date(2022, 1, 1), date(2026, 4, 24)),
    ("5y", date(2021, 1, 1), date(2026, 4, 15)),
]

TRAIN_END = date(2024, 12, 31)
FOCUS_WINDOWS = ("2025", "dip", "22-now", "5y")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


class Status:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.started_at = utc_now()
        self.path = run_dir / "status.json"
        self.payload: dict[str, Any] = {
            "phase": "init",
            "state": "running",
            "completed": 0,
            "total": None,
            "best_so_far": None,
            "started_at": self.started_at,
            "updated_at": self.started_at,
            "output_paths": {},
        }

    def update(self, **kwargs: Any) -> None:
        self.payload.update(kwargs)
        self.payload["updated_at"] = utc_now()
        atomic_json(self.path, self.payload)

    def output(self, key: str, path: Path) -> None:
        outputs = dict(self.payload.get("output_paths") or {})
        outputs[key] = str(path)
        self.update(output_paths=outputs)


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and math.isfinite(out) else None


def on_or_before(sorted_dates: list[date], mapping: dict[date, Any], d: date, default: Any = None) -> Any:
    import bisect

    if not sorted_dates:
        return default
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx < 0:
        return default
    return mapping.get(sorted_dates[idx], default)


def resolve_v60() -> AlgorithmVersion:
    version = AlgorithmVersion.get_by_production_label(60)
    if version is None:
        active = AlgorithmVersion.get_active_scores_version()
        if int(active.id) != 60:
            raise RuntimeError(f"Expected v60 rows, active is {active.production_label} db:{active.id}")
        return active
    return version


def load_market_state(start: date, end: date) -> tuple[list[date], dict[date, dict[str, Any]]]:
    rows = (
        MarketBreadth.select(
            MarketBreadth.date,
            MarketBreadth.breadth_score,
            MarketBreadth.sector_etf_pct_above_ema50,
            MarketBreadth.sector_etf_pct_above_ema200,
            MarketBreadth.sector_etf_breadth_5d_change,
            MarketBreadth.sector_etf_breadth_10d_change,
            MarketBreadth.sector_etf_breadth_avg5,
            MarketBreadth.sector_etf_market_wave_score,
            MarketBreadth.sector_etf_market_wave_signed,
            MarketBreadth.sector_etf_market_wave_state,
        )
        .where((MarketBreadth.date >= start - timedelta(days=80)) & (MarketBreadth.date <= end))
        .order_by(MarketBreadth.date)
        .namedtuples()
    )
    state: dict[date, dict[str, Any]] = {}
    for r in rows:
        state[r.date] = {
            "breadth": safe_float(r.breadth_score),
            "sector_ema50": safe_float(r.sector_etf_pct_above_ema50),
            "sector_ema200": safe_float(r.sector_etf_pct_above_ema200),
            "sector_breadth_5d": safe_float(r.sector_etf_breadth_5d_change),
            "sector_breadth_10d": safe_float(r.sector_etf_breadth_10d_change),
            "sector_breadth_avg5": safe_float(r.sector_etf_breadth_avg5),
            "sector_wave_score": safe_float(r.sector_etf_market_wave_score),
            "sector_wave_signed": safe_float(r.sector_etf_market_wave_signed),
            "sector_wave_state": r.sector_etf_market_wave_state,
        }
    return sorted(state), state


def load_earnings_map(symbols: set[str], start: date, end: date) -> dict[str, list[date]]:
    if not symbols:
        return {}
    rows = (
        EarningsDate.select(EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
        .where(
            (EarningsDate.symbol.in_(list(symbols)))
            & (EarningsDate.date >= start - timedelta(days=10))
            & (EarningsDate.date <= end + timedelta(days=80))
        )
        .order_by(EarningsDate.symbol, EarningsDate.date)
        .tuples()
    )
    out: dict[str, list[date]] = defaultdict(list)
    for sym, d, ct in rows:
        out[str(sym)].append(compute_effective_date(d, ct))
    return dict(out)


def load_sector_map(symbols: set[str]) -> dict[str, str]:
    if not symbols:
        return {}
    rows = Stock.select(Stock.symbol, Stock.sector).where(Stock.symbol.in_(list(symbols))).tuples()
    return {str(sym): str(sector or "UNKNOWN") for sym, sector in rows}


def signal_key(sig: Any, side: str) -> tuple[str, date, str]:
    return (str(sig.symbol), sig.date, side)


def score_bucket(side: str, score: float) -> str:
    if side == "call":
        if score >= 95:
            return "95+"
        if score >= 90:
            return "90-94"
        if score >= 85:
            return "85-89"
        if score >= 80:
            return "80-84"
        if score >= 75:
            return "75-79"
        return "70-74"
    if score <= 5:
        return "0-5"
    if score <= 10:
        return "6-10"
    if score <= 15:
        return "11-15"
    if score <= 20:
        return "16-20"
    return "21-25"


def bin_value(value: float | None, cuts: list[float], labels: list[str], default: str = "missing") -> str:
    if value is None:
        return default
    for cut, label in zip(cuts, labels):
        if value <= cut:
            return label
    return labels[-1]


def build_meta(
    signals: list[tuple[Any, str]],
    outcomes30: dict[tuple[str, date, str], Any],
    outcomes15: dict[tuple[str, date, str], Any],
    market_dates: list[date],
    market_state: dict[date, dict[str, Any]],
    sector_map: dict[str, str],
) -> dict[tuple[str, date, str], dict[str, Any]]:
    valid = [(sig, side) for sig, side in signals if signal_key(sig, side) in outcomes30 and signal_key(sig, side) in outcomes15]
    call_pressure: Counter[date] = Counter()
    put_pressure: Counter[date] = Counter()
    sector_side_pressure: Counter[tuple[date, str, str]] = Counter()
    for sig, side in valid:
        sym = str(sig.symbol)
        sector = sector_map.get(sym, "UNKNOWN")
        if side == "call":
            call_pressure[sig.date] += 1
        else:
            put_pressure[sig.date] += 1
        sector_side_pressure[(sig.date, side, sector)] += 1

    meta: dict[tuple[str, date, str], dict[str, Any]] = {}
    for sig, side in valid:
        key = signal_key(sig, side)
        score = float(sig.overall)
        state = on_or_before(market_dates, market_state, sig.date, {}) or {}
        breadth = safe_float(state.get("breadth"))
        wave = safe_float(state.get("sector_wave_signed"))
        sector_ema50 = safe_float(state.get("sector_ema50"))
        sector_b5 = safe_float(state.get("sector_breadth_5d"))
        sector = sector_map.get(str(sig.symbol), "UNKNOWN")
        cp = int(call_pressure[sig.date])
        pp = int(put_pressure[sig.date])
        ssp = int(sector_side_pressure[(sig.date, side, sector)])
        sigma = safe_float(getattr(outcomes30[key], "sigma_daily", None))
        trend = safe_float(getattr(sig, "trend", None))
        delta = float(outcomes15[key].net_return) - float(outcomes30[key].net_return)
        meta[key] = {
            "key": key,
            "symbol": str(sig.symbol),
            "date": sig.date,
            "side": side,
            "score": score,
            "score_bucket": score_bucket(side, score),
            "trend": trend,
            "sector": sector,
            "breadth": breadth,
            "sector_wave_signed": wave,
            "sector_ema50": sector_ema50,
            "sector_breadth_5d": sector_b5,
            "call_pressure": cp,
            "put_pressure": pp,
            "total_pressure": cp + pp,
            "side_imbalance": cp - pp,
            "sector_side_pressure": ssp,
            "weekday": sig.date.weekday(),
            "month": sig.date.month,
            "sigma": sigma,
            "train": sig.date <= TRAIN_END,
            "dte_delta": delta,
            "ret30": float(outcomes30[key].net_return),
            "ret15": float(outcomes15[key].net_return),
            "outcome30": outcomes30[key].outcome,
            "outcome15": outcomes15[key].outcome,
            "hold30": int(outcomes30[key].hold_bars),
            "hold15": int(outcomes15[key].hold_bars),
        }
        meta[key].update(feature_bins(meta[key]))
    return meta


def feature_bins(m: dict[str, Any]) -> dict[str, str]:
    cp = int(m.get("call_pressure") or 0)
    pp = int(m.get("put_pressure") or 0)
    tp = int(m.get("total_pressure") or 0)
    imb = int(m.get("side_imbalance") or 0)
    ssp = int(m.get("sector_side_pressure") or 0)
    return {
        "breadth_bin": bin_value(m.get("breadth"), [35, 45, 55, 65, 75], ["<=35", "36-45", "46-55", "56-65", "66-75", ">75"]),
        "wave_bin": bin_value(m.get("sector_wave_signed"), [-10, -3, 0, 3, 10], ["<=-10", "-10--3", "-3-0", "0-3", "3-10", ">10"]),
        "sector_ema50_bin": bin_value(m.get("sector_ema50"), [35, 50, 65, 80], ["<=35", "36-50", "51-65", "66-80", ">80"]),
        "sector_b5_bin": bin_value(m.get("sector_breadth_5d"), [-20, -5, 5, 20], ["<=-20", "-20--5", "-5-5", "5-20", ">20"]),
        "call_pressure_bin": "<=8" if cp <= 8 else "<=16" if cp <= 16 else "<=24" if cp <= 24 else ">24",
        "put_pressure_bin": "<=2" if pp <= 2 else "<=4" if pp <= 4 else "<=8" if pp <= 8 else ">8",
        "total_pressure_bin": "<=10" if tp <= 10 else "<=20" if tp <= 20 else "<=35" if tp <= 35 else ">35",
        "imbalance_bin": "put_dom" if imb <= -4 else "balanced" if imb <= 8 else "call_dom" if imb <= 18 else "call_extreme",
        "sector_side_pressure_bin": "1" if ssp <= 1 else "2" if ssp == 2 else "3-4" if ssp <= 4 else ">=5",
        "sigma_bin": bin_value(m.get("sigma"), [1.2, 1.8, 2.5, 3.5], ["<=1.2", "1.2-1.8", "1.8-2.5", "2.5-3.5", ">3.5"]),
        "weekday_bin": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][int(m.get("weekday") or 0)],
    }


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    fn: Callable[[dict[str, Any]], bool]


def pred_side(side: str) -> Callable[[dict[str, Any]], bool]:
    return lambda m: m["side"] == side


def pred_num(field: str, op: str, threshold: float) -> Callable[[dict[str, Any]], bool]:
    def _fn(m: dict[str, Any]) -> bool:
        value = safe_float(m.get(field))
        if value is None:
            return False
        if op == ">=":
            return value >= threshold
        if op == "<=":
            return value <= threshold
        if op == ">":
            return value > threshold
        if op == "<":
            return value < threshold
        raise ValueError(op)

    return _fn


def base_predicates() -> list[Candidate]:
    preds: list[Candidate] = [
        Candidate("side_call", "CALL rows only", pred_side("call")),
        Candidate("side_put", "PUT rows only", pred_side("put")),
    ]
    specs = [
        ("call_score_ge_80", "CALL score >= 80", lambda m: m["side"] == "call" and m["score"] >= 80),
        ("call_score_ge_85", "CALL score >= 85", lambda m: m["side"] == "call" and m["score"] >= 85),
        ("call_score_75_84", "CALL marginal/mid score 75-84", lambda m: m["side"] == "call" and 75 <= m["score"] <= 84),
        ("put_score_le_15", "PUT score <= 15", lambda m: m["side"] == "put" and m["score"] <= 15),
        ("put_score_16_25", "PUT score 16-25", lambda m: m["side"] == "put" and 16 <= m["score"] <= 25),
        ("breadth_ge_65", "broad breadth >= 65", pred_num("breadth", ">=", 65)),
        ("breadth_ge_55", "broad breadth >= 55", pred_num("breadth", ">=", 55)),
        ("breadth_45_65", "broad breadth 45-65", lambda m: (m.get("breadth") is not None and 45 <= m["breadth"] <= 65)),
        ("breadth_le_45", "broad breadth <= 45", pred_num("breadth", "<=", 45)),
        ("breadth_le_35", "broad breadth <= 35", pred_num("breadth", "<=", 35)),
        ("sector_wave_ge_3", "sector ETF wave signed >= 3", pred_num("sector_wave_signed", ">=", 3)),
        ("sector_wave_ge_0", "sector ETF wave signed >= 0", pred_num("sector_wave_signed", ">=", 0)),
        ("sector_wave_le_neg3", "sector ETF wave signed <= -3", pred_num("sector_wave_signed", "<=", -3)),
        ("sector_wave_le_neg10", "sector ETF wave signed <= -10", pred_num("sector_wave_signed", "<=", -10)),
        ("sector_ema50_ge_65", "sector ETF EMA50 breadth >= 65", pred_num("sector_ema50", ">=", 65)),
        ("sector_ema50_le_45", "sector ETF EMA50 breadth <= 45", pred_num("sector_ema50", "<=", 45)),
        ("sector_breadth5_ge_10", "sector ETF 5d breadth thrust >= 10", pred_num("sector_breadth_5d", ">=", 10)),
        ("sector_breadth5_le_neg10", "sector ETF 5d breadth shock <= -10", pred_num("sector_breadth_5d", "<=", -10)),
        ("call_pressure_ge_16", "same-day call pressure >= 16", pred_num("call_pressure", ">=", 16)),
        ("call_pressure_ge_24", "same-day call pressure >= 24", pred_num("call_pressure", ">=", 24)),
        ("call_pressure_le_8", "same-day call pressure <= 8", pred_num("call_pressure", "<=", 8)),
        ("put_pressure_ge_4", "same-day put pressure >= 4", pred_num("put_pressure", ">=", 4)),
        ("put_pressure_le_2", "same-day put pressure <= 2", pred_num("put_pressure", "<=", 2)),
        ("total_pressure_ge_25", "total same-day signal pressure >= 25", pred_num("total_pressure", ">=", 25)),
        ("total_pressure_le_10", "total same-day signal pressure <= 10", pred_num("total_pressure", "<=", 10)),
        ("call_imbalance_ge_12", "call pressure exceeds put pressure by >= 12", pred_num("side_imbalance", ">=", 12)),
        ("put_imbalance_ge_4", "put pressure exceeds call pressure by >= 4", pred_num("side_imbalance", "<=", -4)),
        ("sector_side_pressure_ge_3", "same-sector same-side pressure >= 3", pred_num("sector_side_pressure", ">=", 3)),
        ("sector_side_pressure_ge_5", "same-sector same-side pressure >= 5", pred_num("sector_side_pressure", ">=", 5)),
        ("sigma_ge_25", "entry realized sigma >= 2.5", pred_num("sigma", ">=", 2.5)),
        ("sigma_le_18", "entry realized sigma <= 1.8", pred_num("sigma", "<=", 1.8)),
        ("monday", "Monday entries", lambda m: m["weekday"] == 0),
        ("friday", "Friday entries", lambda m: m["weekday"] == 4),
    ]
    preds.extend(Candidate(name, desc, fn) for name, desc, fn in specs)
    return preds


def domain_candidates() -> list[Candidate]:
    return [
        Candidate(
            "call_healthy_highscore_fast",
            "15DTE for high-score CALLs in healthy breadth",
            lambda m: m["side"] == "call" and m["score"] >= 80 and (m.get("breadth") or -999) >= 55,
        ),
        Candidate(
            "call_crowded_healthy_fast",
            "15DTE for CALLs on crowded but healthy call-pressure days",
            lambda m: m["side"] == "call" and m["call_pressure"] >= 16 and (m.get("breadth") or -999) >= 50,
        ),
        Candidate(
            "call_crowded_stress_fast",
            "15DTE for CALLs only when call pressure is high and breadth is weak",
            lambda m: m["side"] == "call" and m["call_pressure"] >= 16 and (m.get("breadth") or 999) <= 45,
        ),
        Candidate(
            "call_sector_repair_fast",
            "15DTE for CALLs when sector ETF wave is repairing",
            lambda m: m["side"] == "call" and (m.get("sector_wave_signed") or -999) >= 0,
        ),
        Candidate(
            "call_sector_shock_fast",
            "15DTE for CALLs during sector ETF shock states",
            lambda m: m["side"] == "call" and (m.get("sector_wave_signed") or 999) <= -3,
        ),
        Candidate(
            "call_low_sigma_fast",
            "15DTE for low-vol CALLs where the 15DTE sigma target is easier to reach",
            lambda m: m["side"] == "call" and (m.get("sigma") or 999) <= 1.8,
        ),
        Candidate(
            "call_high_sigma_fast",
            "15DTE for high-vol CALLs to shorten exposure",
            lambda m: m["side"] == "call" and (m.get("sigma") or -999) >= 2.5,
        ),
        Candidate(
            "put_weak_breadth_fast",
            "15DTE for PUTs in weak breadth",
            lambda m: m["side"] == "put" and (m.get("breadth") or 999) <= 45,
        ),
        Candidate(
            "put_strong_breadth_fast",
            "15DTE for PUTs in strong breadth headwinds",
            lambda m: m["side"] == "put" and (m.get("breadth") or -999) >= 70,
        ),
        Candidate(
            "put_crowded_fast",
            "15DTE for PUTs on crowded put-pressure days",
            lambda m: m["side"] == "put" and m["put_pressure"] >= 4,
        ),
        Candidate(
            "sector_cluster_fast",
            "15DTE for same-sector same-side clusters",
            lambda m: m["sector_side_pressure"] >= 3,
        ),
        Candidate(
            "crowded_book_fast",
            "15DTE for very crowded daily signal tape",
            lambda m: m["total_pressure"] >= 25,
        ),
    ]


def summarize_predicates(meta: dict[tuple[str, date, str], dict[str, Any]], preds: list[Candidate]) -> list[dict[str, Any]]:
    rows = []
    train_meta = [m for m in meta.values() if m["train"]]
    for pred in preds:
        selected = [m for m in train_meta if pred.fn(m)]
        if not selected:
            continue
        deltas = [m["dte_delta"] for m in selected]
        better = sum(1 for d in deltas if d > 0)
        rows.append(
            {
                "name": pred.name,
                "description": pred.description,
                "n_train": len(selected),
                "avg_delta_pp": statistics.mean(deltas) * 100.0,
                "median_delta_pp": statistics.median(deltas) * 100.0,
                "p15_better_pct": better / len(selected) * 100.0,
                "score": statistics.mean(deltas) * math.sqrt(len(selected)),
            }
        )
    rows.sort(key=lambda r: (r["score"], r["avg_delta_pp"], r["n_train"]), reverse=True)
    return rows


def make_edge_model_candidate(meta: dict[tuple[str, date, str], dict[str, Any]], min_n: int, threshold_pp: float, name: str) -> tuple[Candidate, dict[str, Any]]:
    dimensions = [
        ("side_score_breadth", ("side", "score_bucket", "breadth_bin")),
        ("side_score_wave", ("side", "score_bucket", "wave_bin")),
        ("side_pressure", ("side", "call_pressure_bin", "put_pressure_bin", "imbalance_bin")),
        ("side_sector_pressure", ("side", "sector_side_pressure_bin", "sector_ema50_bin")),
        ("side_sigma", ("side", "sigma_bin")),
        ("side_weekday", ("side", "weekday_bin")),
        ("side_sector_b5", ("side", "sector_b5_bin")),
    ]
    bucket_values: dict[tuple[str, tuple[Any, ...]], list[float]] = defaultdict(list)
    for m in meta.values():
        if not m["train"]:
            continue
        for dim_name, fields in dimensions:
            key = (dim_name, tuple(m.get(f) for f in fields))
            bucket_values[key].append(float(m["dte_delta"]))

    learned: dict[str, dict[str, Any]] = {}
    for key, vals in bucket_values.items():
        if len(vals) < min_n:
            continue
        avg = statistics.mean(vals) * 100.0
        if avg >= threshold_pp:
            learned[json.dumps(key, default=str)] = {
                "dimension": key[0],
                "values": list(key[1]),
                "n": len(vals),
                "avg_delta_pp": avg,
            }

    learned_keys = {
        (row["dimension"], tuple(row["values"]))
        for row in learned.values()
    }

    def _fn(m: dict[str, Any]) -> bool:
        for dim_name, fields in dimensions:
            if (dim_name, tuple(m.get(f) for f in fields)) in learned_keys:
                return True
        return False

    cand = Candidate(
        name,
        f"Train-only learned positive 15DTE bins, min_n={min_n}, edge>={threshold_pp:.2f}pp",
        _fn,
    )
    return cand, {"min_n": min_n, "threshold_pp": threshold_pp, "learned_bins": list(learned.values())}


def candidate_set(meta: dict[tuple[str, date, str], dict[str, Any]], max_candidates: int) -> tuple[list[Candidate], dict[str, Any]]:
    preds = base_predicates()
    pred_rows = summarize_predicates(meta, preds)
    pred_by_name = {p.name: p for p in preds}
    selected_pred_names = [
        r["name"]
        for r in pred_rows
        if r["n_train"] >= 250 and r["avg_delta_pp"] > 0.15
    ][:24]
    selected_preds = [pred_by_name[n] for n in selected_pred_names if n in pred_by_name]

    cands: list[Candidate] = [
        Candidate("always_30", "Baseline: every row uses 30DTE", lambda m: False),
        Candidate("always_15_same_alloc", "Every row uses 15DTE outcome with Sentinel allocation", lambda m: True),
    ]
    cands.extend(domain_candidates())
    cands.extend(Candidate(f"single_{p.name}", f"15DTE when {p.description}", p.fn) for p in selected_preds)

    for i, a in enumerate(selected_preds):
        for b in selected_preds[i + 1 :]:
            if len(cands) >= max_candidates - 12:
                break

            def _and(m: dict[str, Any], fa=a.fn, fb=b.fn) -> bool:
                return fa(m) and fb(m)

            cands.append(Candidate(f"and_{a.name}__{b.name}", f"15DTE when {a.description} AND {b.description}", _and))
        if len(cands) >= max_candidates - 12:
            break

    model_specs = []
    for min_n, threshold in ((150, 0.05), (250, 0.10), (400, 0.15), (600, 0.20)):
        cand, spec = make_edge_model_candidate(meta, min_n, threshold, f"edge_bins_n{min_n}_t{str(threshold).replace('.', 'p')}")
        cands.append(cand)
        model_specs.append({"name": cand.name, **spec})

    # De-duplicate names while preserving order.
    seen: set[str] = set()
    unique: list[Candidate] = []
    for cand in cands:
        if cand.name in seen:
            continue
        seen.add(cand.name)
        unique.append(cand)
    return unique[:max_candidates], {"predicate_edges": pred_rows, "edge_models": model_specs}


def outcome_sort_key(o: Any) -> tuple[Any, ...]:
    side_order = 0 if getattr(o, "side", "call") == "call" else 1
    ct_priority = 0 if (
        (o.side == "call" and o.tier == bt30.CT_CALL_TIER and o.score < 95)
        or (o.side == "put" and o.tier == bt30.CT_PUT_TIER and o.score > 15)
    ) else 1
    score_key = -o.score if o.side == "call" else o.score
    return (side_order, ct_priority, score_key, o.symbol)


def build_outcomes_by_date(
    keys: list[tuple[str, date, str]],
    outcomes30: dict[tuple[str, date, str], Any],
    outcomes15: dict[tuple[str, date, str], Any],
    meta: dict[tuple[str, date, str], dict[str, Any]],
    cand: Candidate,
    start: date,
    end: date,
) -> tuple[dict[date, list[Any]], dict[tuple[str, date, str], str]]:
    by_date: dict[date, list[Any]] = defaultdict(list)
    dte_by_trade: dict[tuple[str, date, str], str] = {}
    for key in keys:
        sym, d, side = key
        if d < start or d > end:
            continue
        use15 = cand.fn(meta[key])
        chosen = outcomes15[key] if use15 else outcomes30[key]
        by_date[d].append(chosen)
        dte_by_trade[(sym, d, side)] = "15" if use15 else "30"
    for d in by_date:
        by_date[d].sort(key=outcome_sort_key)
    return dict(by_date), dte_by_trade


def window_days(all_dates: list[date], start: date, end: date) -> list[date]:
    return [d for d in all_dates if start <= d <= end]


def run_candidate_window(
    cand: Candidate,
    window_name: str,
    start: date,
    end: date,
    keys: list[tuple[str, date, str]],
    outcomes30: dict[tuple[str, date, str], Any],
    outcomes15: dict[tuple[str, date, str], Any],
    meta: dict[tuple[str, date, str], dict[str, Any]],
    all_dates: list[date],
    cfg: dict[str, Any],
    regime_dates: list[date],
    regime_map: dict[date, float],
    ph: dict[str, Any],
) -> dict[str, Any]:
    outcomes_by_date, dte_by_trade = build_outcomes_by_date(keys, outcomes30, outcomes15, meta, cand, start, end)
    days = window_days(all_dates, start, end)
    result = bt30.run_backtest(outcomes_by_date, days, 50_000.0, regime_dates=regime_dates, regime_map=regime_map, cfg=cfg, ph=ph)
    trades = result.get("trade_log") or []
    routed15_offered = sum(1 for key in keys if start <= key[1] <= end and cand.fn(meta[key]))
    offered = sum(1 for key in keys if start <= key[1] <= end)
    routed15_trades = 0
    for t in trades:
        side = t.get("side", "call")
        key = (str(t["symbol"]), t["entry_date"], side)
        if dte_by_trade.get(key) == "15":
            routed15_trades += 1
    call_trades = [t for t in trades if t.get("side", "call") == "call"]
    put_trades = [t for t in trades if t.get("side") == "put"]
    n_tp = sum(1 for t in trades if t.get("outcome") == "tp")
    c_tp = sum(1 for t in call_trades if t.get("outcome") == "tp")
    p_tp = sum(1 for t in put_trades if t.get("outcome") == "tp")
    return {
        "candidate": cand.name,
        "window": window_name,
        "final_equity": float(result.get("final_equity") or 0.0),
        "max_dd_pct": float(result.get("max_dd") or 0.0) * 100.0,
        "trades": len(trades),
        "call_trades": len(call_trades),
        "put_trades": len(put_trades),
        "tp_pct": (n_tp / len(trades) * 100.0) if trades else 0.0,
        "call_tp_pct": (c_tp / len(call_trades) * 100.0) if call_trades else 0.0,
        "put_tp_pct": (p_tp / len(put_trades) * 100.0) if put_trades else 0.0,
        "routed15_offered": routed15_offered,
        "offered": offered,
        "routed15_offered_pct": (routed15_offered / offered * 100.0) if offered else 0.0,
        "routed15_trades": routed15_trades,
        "routed15_trade_pct": (routed15_trades / len(trades) * 100.0) if trades else 0.0,
        "avg_open_premium_base_pct": float(result.get("avg_open_premium_base_pct") or 0.0),
        "max_open_premium_base_pct": float(result.get("max_open_premium_base_pct") or 0.0),
    }


def add_deltas(rows: list[dict[str, Any]], baseline: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        base = baseline[row["window"]]
        final = max(float(row["final_equity"]), 1.0)
        base_final = max(float(base["final_equity"]), 1.0)
        enriched = dict(row)
        enriched["log_final_delta"] = math.log(final / base_final)
        enriched["final_delta_pct"] = (final / base_final - 1.0) * 100.0
        enriched["dd_delta_pp"] = float(row["max_dd_pct"]) - float(base["max_dd_pct"])
        enriched["trade_delta"] = int(row["trades"]) - int(base["trades"])
        enriched["call_tp_delta_pp"] = float(row["call_tp_pct"]) - float(base["call_tp_pct"])
        enriched["put_tp_delta_pp"] = float(row["put_tp_pct"]) - float(base["put_tp_pct"])
        out.append(enriched)
    return out


def aggregate_candidate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_window = {r["window"]: r for r in rows}
    def w(name: str, field: str, default: float = 0.0) -> float:
        return float((by_window.get(name) or {}).get(field, default) or default)

    dd_regressions = [
        w(name, "dd_delta_pp")
        for name in ("2021", "2022", "2023", "2024", "2025", "dip", "22-now", "5y")
    ]
    log_focus = sum(w(name, "log_final_delta") for name in FOCUS_WINDOWS)
    score = (
        3.0 * w("5y", "log_final_delta")
        + 2.0 * w("22-now", "log_final_delta")
        + 1.0 * w("2025", "log_final_delta")
        + 1.0 * w("dip", "log_final_delta")
        - 0.06 * max(0.0, w("5y", "dd_delta_pp"))
        - 0.03 * max(0.0, max(dd_regressions))
    )
    route_share = statistics.mean([float(r["routed15_trade_pct"]) for r in rows if r["window"] in FOCUS_WINDOWS])
    deterministic_pass = (
        w("5y", "log_final_delta") > 0.0
        and w("22-now", "log_final_delta") > 0.0
        and w("5y", "dd_delta_pp") <= 1.0
        and max(dd_regressions) <= 5.0
        and 3.0 <= route_share <= 85.0
    )
    return {
        "candidate": rows[0]["candidate"],
        "score": score,
        "focus_log_delta_sum": log_focus,
        "log_delta_5y": w("5y", "log_final_delta"),
        "log_delta_22_now": w("22-now", "log_final_delta"),
        "log_delta_2025": w("2025", "log_final_delta"),
        "log_delta_dip": w("dip", "log_final_delta"),
        "dd_delta_5y_pp": w("5y", "dd_delta_pp"),
        "max_dd_regression_pp": max(dd_regressions),
        "avg_focus_routed15_trade_pct": route_share,
        "deterministic_pass": deterministic_pass,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_findings(
    path: Path,
    version: AlgorithmVersion,
    profile: dict[str, Any],
    summary_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    predicate_rows: list[dict[str, Any]],
    edge_models: list[dict[str, Any]],
) -> None:
    top = summary_rows[0] if summary_rows else None
    passed = [r for r in summary_rows if r.get("deterministic_pass")]
    lines = [
        "# DTE Router Search Findings",
        "",
        f"- Generated: {utc_now()}",
        f"- Score rows: {version.production_label} db:{version.id} commit={version.git_commit}",
        f"- Portfolio profile: {profile.get('name')} ({profile.get('key')})",
        "- Scope: Stage 3 DTE/instrument routing overlay. Score.overall frozen; no ALGORITHM_VERSION change.",
        "- Screen: deterministic backtest using Sentinel allocation and per-entry 30DTE vs 15DTE option outcomes.",
        "",
        "## Verdict",
        "",
    ]
    if not top:
        lines.append("No candidates evaluated.")
    else:
        lines.extend(
            [
                f"Top deterministic candidate: `{top['candidate']}`.",
                f"- 5y log-final delta: {top['log_delta_5y']:+.4f}",
                f"- 22-now log-final delta: {top['log_delta_22_now']:+.4f}",
                f"- 2025 log-final delta: {top['log_delta_2025']:+.4f}",
                f"- dip log-final delta: {top['log_delta_dip']:+.4f}",
                f"- 5y DD delta: {top['dd_delta_5y_pp']:+.2f}pp",
                f"- max tested DD regression: {top['max_dd_regression_pp']:+.2f}pp",
                f"- focus routed-15 trade share: {top['avg_focus_routed15_trade_pct']:.1f}%",
                f"- deterministic pass: {bool(top['deterministic_pass'])}",
            ]
        )
        if passed:
            lines.append("")
            lines.append(f"Deterministic survivors needing MC integration: {len(passed)}.")
        else:
            lines.append("")
            lines.append("No deterministic survivor cleared the provisional Stage 3 screen. Treat any positive bin edge as research-only until a portfolio candidate survives.")

    lines.extend(["", "## Top Candidates", ""])
    for row in summary_rows[:15]:
        lines.append(
            f"- `{row['candidate']}` score={row['score']:+.4f}; "
            f"5y log={row['log_delta_5y']:+.4f}, 22-now log={row['log_delta_22_now']:+.4f}, "
            f"5y DD={row['dd_delta_5y_pp']:+.2f}pp, max DD reg={row['max_dd_regression_pp']:+.2f}pp, "
            f"r15={row['avg_focus_routed15_trade_pct']:.1f}%, pass={row['deterministic_pass']}"
        )

    lines.extend(["", "## Best Per-Trade 15DTE Edge Predicates", ""])
    for row in predicate_rows[:20]:
        lines.append(
            f"- `{row['name']}` n={row['n_train']:,}, avg_delta={row['avg_delta_pp']:+.2f}pp, "
            f"p15_better={row['p15_better_pct']:.1f}% - {row['description']}"
        )

    lines.extend(["", "## Learned Edge Models", ""])
    for spec in edge_models:
        lines.append(
            f"- `{spec['name']}` min_n={spec['min_n']} threshold={spec['threshold_pp']:.2f}pp "
            f"learned_bins={len(spec.get('learned_bins') or [])}"
        )

    lines.extend(["", "## Output Files", ""])
    for p in [
        "candidate_summary.csv",
        "candidate_windows.csv",
        "predicate_edges.csv",
        "edge_models.json",
        "significant_bins.csv",
    ]:
        lines.append(f"- `{p}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def significant_bins(meta: dict[tuple[str, date, str], dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        ("side_score_breadth", ("side", "score_bucket", "breadth_bin")),
        ("side_score_wave", ("side", "score_bucket", "wave_bin")),
        ("side_pressure", ("side", "call_pressure_bin", "put_pressure_bin", "imbalance_bin")),
        ("side_sector_pressure", ("side", "sector_side_pressure_bin", "sector_ema50_bin")),
        ("side_sigma", ("side", "sigma_bin")),
        ("side_weekday", ("side", "weekday_bin")),
        ("side_sector_b5", ("side", "sector_b5_bin")),
        ("side_sector", ("side", "sector")),
    ]
    buckets: dict[tuple[str, tuple[Any, ...]], list[dict[str, Any]]] = defaultdict(list)
    for m in meta.values():
        if not m["train"]:
            continue
        for name, fs in fields:
            buckets[(name, tuple(m.get(f) for f in fs))].append(m)
    rows = []
    for (name, values), items in buckets.items():
        if len(items) < 100:
            continue
        deltas = [m["dte_delta"] for m in items]
        rows.append(
            {
                "dimension": name,
                "values": "|".join(str(v) for v in values),
                "n_train": len(items),
                "avg_delta_pp": statistics.mean(deltas) * 100.0,
                "median_delta_pp": statistics.median(deltas) * 100.0,
                "p15_better_pct": sum(1 for d in deltas if d > 0) / len(deltas) * 100.0,
            }
        )
    rows.sort(key=lambda r: (abs(r["avg_delta_pp"]) * math.sqrt(r["n_train"]), abs(r["avg_delta_pp"])), reverse=True)
    return rows


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    status = Status(run_dir)
    status.update(phase="resolve_version", state="running")

    version = resolve_v60()
    profile_cfg, profile = portfolio_profiles.apply_profile_to_config({}, args.profile, ROOT)
    profile_cfg = dict(profile_cfg)
    status.update(best_so_far={"version": version.production_label, "profile": profile.get("key")})

    start = min(w[1] for w in WINDOWS)
    end = max(w[2] for w in WINDOWS)
    status.update(phase="load_signals")
    call_sigs = bt30.load_signals(version.id, args.min_score, from_date=start, to_date=end, flagged_only=args.flagged_only)
    put_sigs = bt30.load_put_signals(version.id, args.max_put_score, from_date=start, to_date=end, flagged_only=args.flagged_only)
    signals = [(s, "call") for s in call_sigs] + [(s, "put") for s in put_sigs]
    symbols = {str(s.symbol) for s, _ in signals}
    print(f"[load] {len(call_sigs):,} calls, {len(put_sigs):,} puts, {len(symbols):,} symbols", flush=True)

    status.update(phase="load_context")
    ph = bt30.load_price_history(symbols, start)
    b_dates, b_map = bt30.load_breadth_map(start)
    breadth_alloc = profile_cfg.get("breadth_alloc_enabled", bt30.BREADTH_ALLOC_ENABLED)
    r_dates, r_map = bt30.load_regime_map(start, breadth_enabled=breadth_alloc)
    market_dates, market_state = load_market_state(start, end)
    sector_map = load_sector_map(symbols)
    ern_map = load_earnings_map(symbols, start, end)
    cfg = dict(profile_cfg)
    cfg["ern_map"] = ern_map
    status.update(
        phase="compute_outcomes",
        total=len(signals) * 2,
        completed=0,
        best_so_far={"signals": len(signals), "symbols": len(symbols)},
    )

    outcomes30: dict[tuple[str, date, str], Any] = {}
    outcomes15: dict[tuple[str, date, str], Any] = {}
    completed = 0
    for idx, (sig, side) in enumerate(signals, start=1):
        rows = ph.get(str(sig.symbol), [])
        trend = safe_float(getattr(sig, "trend", None))
        key = signal_key(sig, side)
        if side == "call":
            stressed = bt30.is_stressed(b_dates, b_map, sig.date)
            o30 = bt30.compute_outcome(str(sig.symbol), sig.date, float(sig.overall), rows, stressed, trend=trend, cfg=cfg)
            o15 = bt15.compute_outcome(str(sig.symbol), sig.date, float(sig.overall), rows, stressed, trend=trend, cfg=cfg)
        else:
            o30 = bt30.compute_put_outcome(str(sig.symbol), sig.date, float(sig.overall), rows, trend=trend, cfg=cfg)
            o15 = bt15.compute_put_outcome(str(sig.symbol), sig.date, float(sig.overall), rows, trend=trend, cfg=cfg)
        if o30 is not None and o15 is not None:
            outcomes30[key] = o30
            outcomes15[key] = o15
        completed += 2
        if idx % 1000 == 0:
            status.update(completed=completed, best_so_far={"outcome_pairs": len(outcomes30), "last_signal": idx})
            print(f"[outcomes] {idx:,}/{len(signals):,} signals -> {len(outcomes30):,} paired", flush=True)

    keys = sorted(outcomes30.keys(), key=lambda k: (k[1], 0 if k[2] == "call" else 1, k[0]))
    all_dates = sorted({r.date for rows in ph.values() for r in rows if start <= r.date <= end})
    status.update(phase="build_meta", completed=completed, best_so_far={"outcome_pairs": len(keys)})
    meta = build_meta(signals, outcomes30, outcomes15, market_dates, market_state, sector_map)
    keys = [k for k in keys if k in meta]
    print(f"[meta] {len(keys):,} paired rows with metadata", flush=True)

    pred_rows_path = run_dir / "predicate_edges.csv"
    sig_bins_path = run_dir / "significant_bins.csv"
    candidates, cand_support = candidate_set(meta, args.max_candidates)
    write_csv(pred_rows_path, cand_support["predicate_edges"])
    write_csv(sig_bins_path, significant_bins(meta))
    edge_models_path = run_dir / "edge_models.json"
    atomic_json(edge_models_path, {"models": cand_support["edge_models"]})
    status.output("predicate_edges", pred_rows_path)
    status.output("significant_bins", sig_bins_path)
    status.output("edge_models", edge_models_path)

    status.update(phase="screen_candidates", total=len(candidates) * len(WINDOWS), completed=0)
    window_rows: list[dict[str, Any]] = []
    baseline_rows: dict[str, dict[str, Any]] = {}
    summary_rows: list[dict[str, Any]] = []
    cand_desc = {c.name: c.description for c in candidates}

    # Always run baseline first.
    baseline = next(c for c in candidates if c.name == "always_30")
    for name, w_start, w_end in WINDOWS:
        row = run_candidate_window(baseline, name, w_start, w_end, keys, outcomes30, outcomes15, meta, all_dates, cfg, r_dates, r_map, ph)
        baseline_rows[name] = row
        window_rows.append(row)
    completed_windows = len(WINDOWS)
    status.update(completed=completed_windows, best_so_far={"candidate": "always_30"})

    for cand_idx, cand in enumerate([c for c in candidates if c.name != "always_30"], start=1):
        rows = []
        for name, w_start, w_end in WINDOWS:
            row = run_candidate_window(cand, name, w_start, w_end, keys, outcomes30, outcomes15, meta, all_dates, cfg, r_dates, r_map, ph)
            rows.append(row)
            completed_windows += 1
            if completed_windows % 20 == 0:
                status.update(completed=completed_windows, best_so_far=(summary_rows[0] if summary_rows else {"candidate": cand.name}))
        enriched = add_deltas(rows, baseline_rows)
        window_rows.extend(enriched)
        agg = aggregate_candidate(enriched)
        agg["description"] = cand_desc.get(cand.name, "")
        summary_rows.append(agg)
        summary_rows.sort(key=lambda r: (r["deterministic_pass"], r["score"]), reverse=True)
        if cand_idx % 10 == 0 or summary_rows[0]["candidate"] == cand.name:
            print(
                f"[screen] {cand_idx:,}/{len(candidates)-1:,} best={summary_rows[0]['candidate']} "
                f"score={summary_rows[0]['score']:+.4f} pass={summary_rows[0]['deterministic_pass']}",
                flush=True,
            )
            status.update(completed=completed_windows, best_so_far=summary_rows[0])

    summary_rows.sort(key=lambda r: (r["deterministic_pass"], r["score"]), reverse=True)
    summary_path = run_dir / "candidate_summary.csv"
    windows_path = run_dir / "candidate_windows.csv"
    write_csv(summary_path, summary_rows)
    write_csv(windows_path, window_rows)
    status.output("candidate_summary", summary_path)
    status.output("candidate_windows", windows_path)

    findings_path = run_dir / "findings.md"
    write_findings(findings_path, version, profile, summary_rows, window_rows, cand_support["predicate_edges"], cand_support["edge_models"])
    status.output("findings", findings_path)

    passed = [r for r in summary_rows if r.get("deterministic_pass")]
    result = {
        "state": "completed",
        "version": version.production_label,
        "version_id": int(version.id),
        "profile": profile.get("key"),
        "candidates_evaluated": len(candidates),
        "deterministic_survivors": len(passed),
        "top_candidate": summary_rows[0] if summary_rows else None,
        "paths": {
            "summary": str(summary_path),
            "windows": str(windows_path),
            "predicate_edges": str(pred_rows_path),
            "significant_bins": str(sig_bins_path),
            "findings": str(findings_path),
            "edge_models": str(edge_models_path),
        },
    }
    done_path = run_dir / "done.json"
    atomic_json(done_path, {**result, "finished_at": utc_now(), "exit_code": 0})
    status.update(phase="done", state="completed", completed=len(candidates) * len(WINDOWS), best_so_far=result["top_candidate"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Search 30DTE/15DTE routing overlays on v60 rows")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--profile", default="sentinel")
    parser.add_argument("--max-candidates", type=int, default=240)
    parser.add_argument("--min-score", type=float, default=70.0)
    parser.add_argument("--max-put-score", type=float, default=None)
    parser.add_argument("--flagged-only", action="store_true")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    try:
        result = run(args)
        print(json.dumps(result, indent=2, default=str), flush=True)
        return 0
    except Exception as exc:
        run_dir.mkdir(parents=True, exist_ok=True)
        failed = {
            "state": "failed",
            "finished_at": utc_now(),
            "exit_code": 1,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        atomic_json(run_dir / "failed.json", failed)
        status_path = run_dir / "status.json"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
            status.update({"state": "failed", "phase": "failed", "updated_at": utc_now(), "error": str(exc)})
            atomic_json(status_path, status)
        except Exception:
            pass
        print(traceback.format_exc(), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
