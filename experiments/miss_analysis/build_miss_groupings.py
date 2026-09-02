#!/usr/bin/env python3
"""Build comprehensive miss groupings for active score rows.

Read-only Stage 1 diagnostic:
- persisted Score rows only;
- no Score writes, no recalculate/update calls;
- joins prebuilt barrier_outcomes DuckDB rows;
- emits compact Markdown plus full CSV/JSONL surfaces for future agents.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
ROOT_DB = ROOT / "database"
sys.path = [p for p in sys.path if Path(p or ".").resolve() not in {ROOT, ROOT_DB}]
sys.path.insert(0, str(ROOT_DB))
sys.path.insert(0, str(ROOT))

from experiments._holdout import CUTOFF


BASE_SCORE_FIELDS = (
    "trend",
    "bb",
    "rsi",
    "macd",
    "stoch",
    "ta",
    "td",
    "pre_regime",
    "pre_boost",
    "w_comp",
    "w_bias",
    "w_mom",
    "w_adj",
    "w_adj_raw",
    "w_put_wave_scale",
    "put_regime_mult",
    "mis_stress",
    "cap_dampened",
    "ext_damp",
    "exh_damp",
    "wcf_lift",
    "cwcf_dampen",
    "cwwd_dampen",
    "cswc_dampen",
    "scw_dampen",
    "scw_base_dampen",
    "scw_scalar",
    "scw_conf",
    "scw_raw_stoch",
    "scw_ext_idx",
    "scw_ext_taper",
    "cont_lift",
    "cont_raw_lift",
    "cont_sig",
    "pcd_active",
    "pcd_r10sigma",
    "mcd_dampen",
    "mcd_mcap_b",
    "ich_call_dampen",
    "ich_put_lift",
    "pess_lift",
    "kijun_pct",
    "wvd_lift",
    "wvd_dampen",
    "wv_force1",
    "ern_boost",
    "days_to_ern",
)

NESTED_SCORE_FIELDS = {
    "sector_breadth_wave": (
        "side",
        "before",
        "after",
        "delta",
        "crash",
        "bull",
        "pressure",
        "overlay_delta",
        "overlay_scale",
    ),
    "daily_volume_authority_wave": (
        "before",
        "after",
        "delta",
        "lift",
        "dampen",
        "score_guard",
        "authority",
        "raw_delta",
        "wv_force1",
    ),
}

COMP_BINS = ((-1e9, 35, "lo"), (35, 65, "mid"), (65, 1e9, "hi"))
EMA_BINS = ((-1e9, -20, "vneg"), (-20, -8, "neg"), (-8, 8, "near"), (8, 20, "pos"), (20, 1e9, "vpos"))
BBPOS_BINS = ((-1e9, -75, "deep_low"), (-75, -25, "lo"), (-25, 25, "mid"), (25, 75, "hi"), (75, 1e9, "deep_hi"))
VMAG_BINS = ((-1e9, 0.001, "none"), (0.001, 0.3, "lo"), (0.3, 0.6, "mid"), (0.6, 1e9, "hi"))
SIGMA_BINS = ((-1e9, 1.5, "tight"), (1.5, 3.0, "mid"), (3.0, 5.0, "wide"), (5.0, 1e9, "vwide"))
VEL_BINS = ((-1e9, -12, "cool"), (-12, 5, "flat"), (5, 15, "warm"), (15, 1e9, "hot"))
WADJ_CALL_BINS = ((-1e9, 0, "neg"), (0, 13, "mild"), (13, 25, "mid"), (25, 1e9, "strong"))
WADJ_PUT_BINS = ((-1e9, -25, "vneg"), (-25, -13, "neg"), (-13, 0, "mild"), (0, 1e9, "pos"))

COHORTS = (
    ("CALL 75+", "CALL", "overall >= 75"),
    ("CALL 80+", "CALL", "overall >= 80"),
    ("CALL 85+", "CALL", "overall >= 85"),
    ("PUT <=25", "PUT", "overall <= 25"),
    ("PUT <=20", "PUT", "overall <= 20"),
    ("PUT <=15", "PUT", "overall <= 15"),
    ("TRADEABLE", "ALL", "(signal_type == 'CALL' and overall >= 75) or (signal_type == 'PUT' and overall <= 25)"),
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def _update_status(path: Path, **kwargs: Any) -> None:
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    payload.update(kwargs)
    payload["updated_at"] = _iso_now()
    _write_json(path, payload)


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _as_int(value: Any) -> int | None:
    f = _as_float(value)
    return None if f is None else int(round(f))


def _load_weight_info(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _flatten_weight_info(wi: dict[str, Any], prefix: str = "wi") -> dict[str, Any]:
    flat: dict[str, Any] = {}

    def walk(key: str, value: Any) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(f"{key}_{child_key}", child_value)
        elif isinstance(value, (list, tuple)):
            flat[key] = json.dumps(value, separators=(",", ":"), sort_keys=True)
        else:
            flat[key] = value

    for key, value in wi.items():
        if isinstance(value, dict):
            expected = NESTED_SCORE_FIELDS.get(key)
            if expected:
                for child_key in expected:
                    if child_key in value:
                        walk(f"{prefix}_{key}_{child_key}", value.get(child_key))
                for child_key, child_value in value.items():
                    if child_key not in expected:
                        walk(f"{prefix}_{key}_{child_key}", child_value)
            else:
                walk(f"{prefix}_{key}", value)
        else:
            walk(f"{prefix}_{key}", value)
    return flat


def _bin(value: Any, bins: Iterable[tuple[float, float, str]], null_label: str = "na") -> str:
    v = _as_float(value)
    if v is None:
        return null_label
    for lo, hi, label in bins:
        if lo <= v < hi:
            return label
    return null_label


def _flag(value: Any, threshold: float = 0.1) -> str:
    v = _as_float(value)
    return "on" if v is not None and v > threshold else "off"


def _regime_label(mult: Any) -> str:
    value = _as_float(mult)
    if value is None:
        return "unk"
    if value <= 0.78:
        return "STRESS"
    if value <= 0.88:
        return "CAUTION"
    if value <= 1.00:
        return "NEUTRAL"
    if value <= 1.05:
        return "HEALTHY"
    return "BULL"


def _score_bin(score: int) -> str:
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
    if score <= 5:
        return "0-5"
    if score <= 10:
        return "6-10"
    if score <= 15:
        return "11-15"
    if score <= 20:
        return "16-20"
    return "21-25"


def _hypothesis_for(pattern: str, side: str, row: dict[str, Any]) -> tuple[str, str]:
    p = pattern.lower()
    if "weekly" in p or "wadj" in p or "w_mom" in p or "w_comp" in p:
        return (
            "Weekly context is contradicting the final thesis after daily components admitted the signal.",
            "Probe a smooth weekly-confirmation taper around final score distance, not a binary weekly gate.",
        )
    if "scw" in p or "stoch" in p:
        return (
            "Stochastic phase or SCW timing is not fully removing late/weak entries in this cohort.",
            "Mine SCW scalar/residual rows by score distance and test a smooth phase-width or taper adjustment.",
        )
    if "mcd" in p or "mcap" in p:
        return (
            "Structural market-cap confidence is still leaving lower-quality call signals tradeable.",
            "Check whether MCD should widen or steepen in this bucket while preserving high-tier N.",
        )
    if "regime" in p or "stress" in p or "caution" in p or "bull" in p or "healthy" in p:
        return (
            "Market state is interacting with signal side, likely changing follow-through odds.",
            "Fit a side-specific smooth regime/breadth residual wave and validate against W1-W6 before portfolio work.",
        )
    if "breadth" in p or "sector" in p or "dvaw" in p or "volume" in p or "vsig" in p or "vmag" in p:
        return (
            "Participation/volume authority is noisy for this cohort despite a strong final score.",
            "Bucket by volume authority plus breadth pressure; test a small continuous authority scalar.",
        )
    if "earn" in p:
        return (
            "Earnings proximity is changing directional payoff distribution around the signal.",
            "Separate pre-earnings admission from post-reaction continuation and test a proximity-weighted modifier.",
        )
    if "ema" in p or "bb" in p or "overextended" in p or "oversold" in p or "velocity" in p:
        return (
            "Price location or velocity suggests exhaustion/reversal risk at entry.",
            "Probe a volatility-normalized location/velocity gradient, capped near the tradeable boundary.",
        )
    if "ich" in p or "kijun" in p:
        return (
            "Ichimoku/Kijun context is pushing scores through the boundary with mixed follow-through.",
            "Audit ICH residuals by Kijun percent and final-score band before changing the lift curve.",
        )
    if "macd" in p or "rsi" in p or "trend" in p or "ta" in p:
        return (
            "One base component is overpowering contradictory components inside the final score.",
            "Mine component-agreement confidence and test a smooth weight gating term.",
        )
    return (
        "The grouping is high-count but not yet tied to a single clean mechanism.",
        "Inspect row samples and pair it with score-path metadata before proposing a scoring change.",
    )


def _root_labels(row: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    side = row["signal_type"]
    score = int(row["overall"])
    w_adj = _as_float(row.get("wi_w_adj"))
    w_mom = _as_float(row.get("wi_w_mom"))
    w_comp = _as_float(row.get("wi_w_comp"))
    ema50 = _as_float(row.get("ema50_pct"))
    ema200 = _as_float(row.get("ema200_pct"))
    velocity = _as_float(row.get("velocity_7d"))
    reg_mult = _as_float(row.get("reg_mult"))
    vol_mag = _as_float(row.get("vol_mag"))
    stoch = _as_float(row.get("stoch"))
    macd = _as_float(row.get("macd"))
    rsi = _as_float(row.get("rsi"))
    trend = _as_float(row.get("trend"))
    bb = _as_float(row.get("bb"))
    days_to_ern = _as_float(row.get("wi_days_to_ern"))

    if side == "CALL":
        if w_adj is not None and w_adj < 0:
            labels.append("call_weekly_drag")
        if w_mom is not None and w_mom < 0:
            labels.append("call_weekly_momentum_drag")
        if reg_mult is not None and reg_mult <= 0.88:
            labels.append("call_in_stress_or_caution_tape")
        if ema50 is not None and ema50 >= 20:
            labels.append("call_overextended_vs_ema50")
        if ema200 is not None and ema200 >= 35:
            labels.append("call_overextended_vs_ema200")
        if velocity is not None and velocity >= 15:
            labels.append("call_hot_velocity")
        if stoch is not None and stoch <= 35:
            labels.append("call_stoch_low_score_proxy")
        if macd is not None and macd <= 35:
            labels.append("call_macd_contra")
        if rsi is not None and rsi <= 35:
            labels.append("call_rsi_contra")
        if trend is not None and trend >= 65 and ((rsi is not None and rsi <= 35) or (macd is not None and macd <= 35)):
            labels.append("call_trend_overpowers_reversal_components")
        if _flag(row.get("wi_scw_dampen"), 0.5) == "on":
            labels.append("call_scw_dampened_but_tradeable")
        if _flag(row.get("wi_mcd_dampen"), 0.5) == "on":
            labels.append("call_mcd_dampened_but_tradeable")
        if _flag(row.get("wi_cwcf_dampen"), 0.5) == "on":
            labels.append("call_cwcf_dampened_but_tradeable")
        if _flag(row.get("wi_cwwd_dampen"), 0.5) == "on":
            labels.append("call_cwwd_dampened_but_tradeable")
    else:
        if w_adj is not None and w_adj > 0:
            labels.append("put_weekly_bullish_drag")
        if w_comp is not None and w_comp > 0:
            labels.append("put_weekly_composite_bullish")
        if reg_mult is not None and reg_mult >= 1.05:
            labels.append("put_in_bull_tape")
        if ema50 is not None and ema50 <= -20:
            labels.append("put_oversold_vs_ema50")
        if ema200 is not None and ema200 <= -35:
            labels.append("put_oversold_vs_ema200")
        if velocity is not None and velocity <= -12:
            labels.append("put_falling_knife_velocity")
        if stoch is not None and stoch >= 65:
            labels.append("put_stoch_hi_bounce_proxy")
        if macd is not None and macd >= 65:
            labels.append("put_macd_bullish_contra")
        if rsi is not None and rsi >= 65:
            labels.append("put_rsi_bullish_contra")
        if bb is not None and bb <= 35 and ema50 is not None and ema50 <= -15:
            labels.append("put_bollinger_oversold_bounce")
        if _flag(row.get("wi_wcf_lift"), 0.5) == "on":
            labels.append("put_wcf_lifted")
        if _flag(row.get("wi_ich_put_lift"), 0.5) == "on":
            labels.append("put_ichimoku_lifted")
        if _flag(row.get("wi_pess_lift"), 0.5) == "on":
            labels.append("put_pess_lifted")

    if vol_mag is not None and vol_mag < 0.3:
        labels.append(f"{side.lower()}_weak_volume_confirmation")
    if days_to_ern is not None and -2 <= days_to_ern <= 7:
        labels.append("earnings_window")
    if _flag(row.get("wi_ern_boost"), 0.001) == "on":
        labels.append("earnings_score_boost")
    if _flag(row.get("wi_mis_stress"), 0.1) == "on":
        labels.append("mis_stress_active")
    if _flag(row.get("wi_cont_lift"), 0.5) == "on":
        labels.append("continuation_echo_lifted")
    if _flag(row.get("wi_sector_breadth_wave_delta"), 0.1) == "on":
        labels.append("sector_breadth_wave_adjusted")
    if _flag(row.get("wi_daily_volume_authority_wave_delta"), 0.1) == "on":
        labels.append("daily_volume_authority_wave_adjusted")
    if not labels:
        labels.append("no_simple_label")
    return sorted(set(labels))


def _add_bins(row: dict[str, Any]) -> dict[str, Any]:
    side = row["signal_type"]
    out = dict(row)
    out["score_bin"] = _score_bin(int(row["overall"]))
    out["b_bb"] = _bin(row.get("bb"), COMP_BINS)
    out["b_trend"] = _bin(row.get("trend"), COMP_BINS)
    out["b_rsi"] = _bin(row.get("rsi"), COMP_BINS)
    out["b_macd"] = _bin(row.get("macd"), COMP_BINS)
    out["b_stoch"] = _bin(row.get("stoch"), COMP_BINS)
    out["b_ta"] = _bin(row.get("ta"), COMP_BINS)
    out["b_ema50"] = _bin(row.get("ema50_pct"), EMA_BINS)
    out["b_ema200"] = _bin(row.get("ema200_pct"), EMA_BINS)
    out["b_bb_pos"] = _bin(row.get("bb_pos"), BBPOS_BINS)
    out["b_vmag"] = _bin(row.get("vol_mag"), VMAG_BINS)
    out["b_sigma"] = _bin(row.get("wr15_sigma_pct"), SIGMA_BINS)
    out["b_velocity"] = _bin(row.get("velocity_7d"), VEL_BINS)
    out["b_wadj"] = _bin(row.get("wi_w_adj"), WADJ_CALL_BINS if side == "CALL" else WADJ_PUT_BINS)
    out["b_wmom"] = _bin(row.get("wi_w_mom"), ((-1e9, -10, "neg"), (-10, 10, "flat"), (10, 1e9, "pos")))
    out["b_wcomp"] = _bin(row.get("wi_w_comp"), ((-1e9, -20, "bear"), (-20, 20, "mid"), (20, 1e9, "bull")))
    out["b_vsig"] = str(row.get("vol_sig") or "NEUTRAL")
    out["b_regime"] = _regime_label(row.get("reg_mult"))
    out["b_mis_stress"] = _flag(row.get("wi_mis_stress"))
    out["b_earnings"] = "near" if (_as_float(row.get("wi_days_to_ern")) is not None and -2 <= _as_float(row.get("wi_days_to_ern")) <= 7) else _flag(row.get("wi_ern_boost"), 0.001)
    modifier_fields = (
        "wcf_lift",
        "cwcf_dampen",
        "cwwd_dampen",
        "cswc_dampen",
        "scw_dampen",
        "mcd_dampen",
        "ich_call_dampen",
        "ich_put_lift",
        "pess_lift",
        "wvd_lift",
        "wvd_dampen",
        "cont_lift",
        "pcd_active",
        "daily_volume_authority_wave_delta",
        "sector_breadth_wave_delta",
    )
    for field in modifier_fields:
        out[f"b_{field}"] = _flag(row.get(f"wi_{field}"), 0.5 if field != "pcd_active" else 0.1)
    out["root_labels"] = ";".join(_root_labels(out))
    out["root_label_count"] = len(out["root_labels"].split(";"))
    return out


FEATURE_COLUMNS = (
    "score_bin",
    "b_bb",
    "b_trend",
    "b_rsi",
    "b_macd",
    "b_stoch",
    "b_ta",
    "b_ema50",
    "b_ema200",
    "b_bb_pos",
    "b_vmag",
    "b_sigma",
    "b_velocity",
    "b_wadj",
    "b_wmom",
    "b_wcomp",
    "b_vsig",
    "b_regime",
    "b_mis_stress",
    "b_earnings",
    "b_wcf_lift",
    "b_cwcf_dampen",
    "b_cwwd_dampen",
    "b_cswc_dampen",
    "b_scw_dampen",
    "b_mcd_dampen",
    "b_ich_call_dampen",
    "b_ich_put_lift",
    "b_pess_lift",
    "b_wvd_lift",
    "b_wvd_dampen",
    "b_cont_lift",
    "b_pcd_active",
    "b_daily_volume_authority_wave_delta",
    "b_sector_breadth_wave_delta",
)


def _passes_condition(row: dict[str, Any], expr: str) -> bool:
    signal_type = row["signal_type"]
    overall = int(row["overall"])
    return bool(eval(expr, {"__builtins__": {}}, {"signal_type": signal_type, "overall": overall}))


def _cohort_rows(rows: list[dict[str, Any]], cohort: tuple[str, str, str]) -> list[dict[str, Any]]:
    label, side, expr = cohort
    return [
        row for row in rows
        if (side == "ALL" or row["signal_type"] == side) and _passes_condition(row, expr)
    ]


def _z(cell_rate: float, baseline: float, n: int) -> float:
    if n <= 0 or baseline <= 0 or baseline >= 1:
        return 0.0
    se = math.sqrt(baseline * (1 - baseline) / n)
    return (cell_rate - baseline) / se if se > 0 else 0.0


def _cell_row(kind: str, cohort_label: str, pattern: str, rows: list[dict[str, Any]], cohort_rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    cohort_n = len(cohort_rows)
    miss_count = sum(int(row["is_wr15_miss"]) for row in rows)
    cohort_miss_count = sum(int(row["is_wr15_miss"]) for row in cohort_rows)
    miss_rate = miss_count / n if n else 0.0
    baseline = cohort_miss_count / cohort_n if cohort_n else 0.0
    side = "ALL"
    if rows and all(row["signal_type"] == "CALL" for row in rows):
        side = "CALL"
    elif rows and all(row["signal_type"] == "PUT" for row in rows):
        side = "PUT"
    hypothesis, next_probe = _hypothesis_for(pattern, side, {"kind": kind})
    return {
        "grouping_type": kind,
        "cohort": cohort_label,
        "pattern": pattern,
        "signal_side": side,
        "n": n,
        "miss_count": miss_count,
        "win_count": n - miss_count,
        "cohort_n": cohort_n,
        "cohort_miss_count": cohort_miss_count,
        "miss_rate": miss_rate,
        "win_rate": 1.0 - miss_rate,
        "baseline_miss_rate": baseline,
        "lift": miss_rate / baseline if baseline else None,
        "z": _z(miss_rate, baseline, n),
        "pct_of_cohort": n / cohort_n if cohort_n else None,
        "pct_of_cohort_misses": miss_count / cohort_miss_count if cohort_miss_count else None,
        "hypothesis": hypothesis,
        "next_probe": next_probe,
    }


def _feature_groupings(rows: list[dict[str, Any]], cohort_label: str, feature: str, min_n: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(feature, "na"))].append(row)
    out = []
    for value, cell_rows in buckets.items():
        if len(cell_rows) >= min_n:
            out.append(_cell_row("feature", cohort_label, f"{feature}={value}", cell_rows, rows))
    return out


def _pair_groupings(rows: list[dict[str, Any]], cohort_label: str, features: list[str], min_n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, f1 in enumerate(features):
        for f2 in features[idx + 1:]:
            buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                buckets[(str(row.get(f1, "na")), str(row.get(f2, "na")))].append(row)
            for (v1, v2), cell_rows in buckets.items():
                if len(cell_rows) >= min_n:
                    out.append(_cell_row("pair", cohort_label, f"{f1}={v1} & {f2}={v2}", cell_rows, rows))
    return out


def _root_groupings(rows: list[dict[str, Any]], cohort_label: str, min_n: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for label in str(row.get("root_labels") or "no_simple_label").split(";"):
            buckets[label].append(row)
    out = []
    for label, cell_rows in buckets.items():
        if len(cell_rows) >= min_n:
            out.append(_cell_row("root_label", cohort_label, label, cell_rows, rows))
    return out


def _fetch_scores(version_id: int, start: date, end: date, high_min: int, low_max: int, status_path: Path) -> list[dict[str, Any]]:
    from database.trader_database import DB

    sql = """
        SELECT s.symbol, s.date, s.overall,
               s.bb, s.trend, s.rsi, s.macd, s.stoch, s.technical_alignment,
               s.volume_signal, s.volume_magnitude,
               s.pct_from_ema50, s.pct_from_ema200, s.bb_position,
               s.regime_composite, s.regime_multiplier,
               s.score_velocity_7d, s.weight_info
        FROM scores s
        WHERE s.version_id = %s
          AND s.date BETWEEN %s AND %s
          AND s.overall IS NOT NULL
          AND (s.overall >= %s OR s.overall <= %s)
        ORDER BY s.symbol, s.date
    """

    def month_ranges(start_date: date, end_date: date) -> list[tuple[date, date]]:
        ranges = []
        current = date(start_date.year, start_date.month, 1)
        while current <= end_date:
            next_month = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
            chunk_start = max(start_date, current)
            chunk_end = min(end_date, next_month - timedelta(days=1))
            ranges.append((chunk_start, chunk_end))
            current = next_month
        return ranges

    rows: list[dict[str, Any]] = []
    chunks = month_ranges(start, end)
    for chunk_idx, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        try:
            cur = DB.execute_sql(sql, (version_id, chunk_start.isoformat(), chunk_end.isoformat(), high_min, low_max))
        except Exception:
            try:
                DB.close()
            except Exception:
                pass
            cur = DB.execute_sql(sql, (version_id, chunk_start.isoformat(), chunk_end.isoformat(), high_min, low_max))
        chunk_n = 0
        for rec in cur:
            (
                symbol,
                score_date,
                overall,
                bb,
                trend,
                rsi,
                macd,
                stoch,
                ta,
                vol_sig,
                vol_mag,
                ema50_pct,
                ema200_pct,
                bb_pos,
                reg_comp,
                reg_mult,
                velocity_7d,
                weight_info,
            ) = rec
            overall_i = int(overall)
            wi = _load_weight_info(weight_info)
            row: dict[str, Any] = {
                "symbol": symbol,
                "date": score_date.isoformat() if hasattr(score_date, "isoformat") else str(score_date),
                "overall": overall_i,
                "signal_type": "CALL" if overall_i >= high_min else "PUT",
                "side": "low" if overall_i >= high_min else "high",
                "bb": _as_int(bb),
                "trend": _as_int(trend),
                "rsi": _as_int(rsi),
                "macd": _as_int(macd),
                "stoch": _as_int(stoch),
                "ta": _as_int(ta),
                "vol_sig": vol_sig or "NEUTRAL",
                "vol_mag": _as_float(vol_mag),
                "ema50_pct": _as_float(ema50_pct),
                "ema200_pct": _as_float(ema200_pct),
                "bb_pos": _as_float(bb_pos),
                "reg_comp": _as_float(reg_comp),
                "reg_mult": _as_float(reg_mult),
                "velocity_7d": _as_int(velocity_7d),
            }
            row.update(_flatten_weight_info(wi))
            for field in BASE_SCORE_FIELDS:
                row.setdefault(f"wi_{field}", None)
            for field, children in NESTED_SCORE_FIELDS.items():
                for child in children:
                    row.setdefault(f"wi_{field}_{child}", None)
            rows.append(row)
            chunk_n += 1
        _update_status(
            status_path,
            phase="fetch_scores",
            completed=chunk_idx,
            total=len(chunks),
            current_chunk=f"{chunk_start.isoformat()}..{chunk_end.isoformat()}",
            fetched_rows=len(rows),
        )
        print(
            f"[miss-analysis] chunk {chunk_idx}/{len(chunks)} "
            f"{chunk_start.isoformat()}..{chunk_end.isoformat()} rows={chunk_n:,} total={len(rows):,}",
            flush=True,
        )
    return rows


def _prune_peaks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[row["symbol"]].append(row)

    peaks: list[dict[str, Any]] = []
    for _symbol, symbol_rows in by_symbol.items():
        date_map = {_parse_date(row["date"]): row for row in symbol_rows}
        ranked = sorted(symbol_rows, key=lambda r: abs(int(r["overall"]) - 50), reverse=True)
        pruned: set[date] = set()
        for row in ranked:
            d = _parse_date(row["date"])
            if d in pruned:
                continue
            peaks.append(row)
            for step in (-1, 1):
                walk = d + timedelta(days=step)
                while walk in date_map and walk not in pruned:
                    pruned.add(walk)
                    walk += timedelta(days=step)
    return sorted(peaks, key=lambda r: (r["symbol"], r["date"]))


def _join_barriers(rows: list[dict[str, Any]], out_dir: Path, cache_dir: Path, status_path: Path) -> list[dict[str, Any]]:
    score_path = out_dir / "score_peaks_pre_barrier.parquet"
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(score_path)

    duck_path = cache_dir / "barrier_outcomes.duckdb"
    if not duck_path.exists():
        raise FileNotFoundError(f"Missing {duck_path}")
    _update_status(status_path, phase="join_barriers", score_peaks=str(score_path), barrier_duckdb=str(duck_path))
    con = duckdb.connect(str(duck_path), read_only=True)
    joined = con.execute(
        f"""
        WITH score_rows AS (
            SELECT *, CAST(date AS DATE) AS score_date
            FROM read_parquet('{score_path.as_posix()}')
        )
        SELECT s.*,
               wr.result AS wr15_result,
               wr.exit_offset AS wr15_exit_offset,
               wr.exit_return AS wr15_exit_return,
               wr.mae_pct AS wr15_mae_pct,
               wr.mfe_pct AS wr15_mfe_pct,
               wr.entry_close AS wr15_entry_close,
               wr.sigma_pct AS wr15_sigma_pct,
               tp.result AS tp15_result,
               tp.exit_offset AS tp15_exit_offset,
               tp.exit_return AS tp15_exit_return,
               tp.mae_pct AS tp15_mae_pct,
               tp.mfe_pct AS tp15_mfe_pct,
               tp.entry_close AS tp15_entry_close,
               tp.sigma_pct AS tp15_sigma_pct
        FROM score_rows s
        LEFT JOIN barrier_outcomes wr
          ON wr.symbol = s.symbol
         AND wr.date = s.score_date
         AND wr.side = s.side
         AND wr.w_days = 15
         AND wr.barrier_set = '30dte_generic'
        LEFT JOIN barrier_outcomes tp
          ON tp.symbol = s.symbol
         AND tp.date = s.score_date
         AND tp.side = s.side
         AND tp.w_days = 15
         AND tp.barrier_set = '30dte_opt'
        """
    ).pl()
    con.close()
    joined_path = out_dir / "score_peaks_with_barriers.parquet"
    joined.write_parquet(joined_path)
    _update_status(status_path, joined_rows=joined.height, joined_path=str(joined_path))
    print(f"[miss-analysis] joined barriers rows={joined.height:,}", flush=True)
    return joined.to_dicts()


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: list[str] = []
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, separators=(",", ":"), sort_keys=True) if isinstance(value, (dict, list, tuple)) else value
                for key, value in row.items()
            })


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _fmt_pct(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.1f}%"


def _summary(all_rows: list[dict[str, Any]], resolved: list[dict[str, Any]]) -> dict[str, Any]:
    cohorts = {}
    for label, side, expr in COHORTS:
        sub = _cohort_rows(resolved, (label, side, expr))
        n = len(sub)
        wins = sum(1 for row in sub if int(row["wr15_result"]) == 1)
        cohorts[label] = {
            "n": n,
            "wr15": wins / n if n else None,
            "miss_rate": 1.0 - wins / n if n else None,
            "miss_count": n - wins,
        }
    return {
        "total_peaks": len(all_rows),
        "resolved_wr15_peaks": len(resolved),
        "unresolved_wr15_peaks": len(all_rows) - len(resolved),
        "cohorts": cohorts,
    }


def _top_by_count(rows: list[dict[str, Any]], *, min_miss_count: int, min_lift: float | None, limit: int) -> list[dict[str, Any]]:
    filtered = [
        row for row in rows
        if int(row.get("miss_count") or 0) >= min_miss_count
        and _is_informative_group(row)
        and (min_lift is None or float(row.get("lift") or 0.0) >= min_lift)
    ]
    return sorted(
        filtered,
        key=lambda r: (
            int(r.get("miss_count") or 0),
            float(r.get("pct_of_cohort_misses") or 0.0),
            float(r.get("z") or 0.0),
        ),
        reverse=True,
    )[:limit]


def _is_informative_group(row: dict[str, Any]) -> bool:
    """Drop whole-cohort/null buckets that dominate count sort without adding signal."""
    pattern = str(row.get("pattern") or "")
    pct = float(row.get("pct_of_cohort") or 0.0)
    lift = float(row.get("lift") or 0.0)
    z = abs(float(row.get("z") or 0.0))
    if pct >= 0.999:
        return False
    if pct >= 0.98 and (pattern.endswith("=off") or pattern.endswith("=na") or "=off &" in pattern or "=na &" in pattern):
        return False
    if "=na" in pattern and z < 1.0:
        return False
    if pct >= 0.95 and abs(lift - 1.0) < 0.02 and z < 1.0:
        return False
    return True


def _write_report(report_path: Path, *, args: argparse.Namespace, version_meta: dict[str, Any],
                  summary: dict[str, Any], groupings: list[dict[str, Any]], outputs: dict[str, str]) -> None:
    top_all = _top_by_count(groupings, min_miss_count=args.min_miss_count, min_lift=None, limit=args.report_limit)
    top_harm = _top_by_count(groupings, min_miss_count=args.min_miss_count, min_lift=args.min_lift, limit=args.report_limit)
    lines = [
        "# Comprehensive Miss Analysis",
        "",
        "Read-only active-score miss grouping for future agent analysis. No score rows were written.",
        "",
        "## Setup",
        "",
        f"- Version: `{version_meta.get('production_label')}` / db `{version_meta.get('id')}` / `{version_meta.get('git_commit')}`",
        f"- Window: `{args.start}` to `{min(_parse_date(args.end), CUTOFF).isoformat()}`",
        f"- Holdout cutoff enforced: `{CUTOFF.isoformat()}`",
        f"- Signal range: CALL `>= {args.high_min}` and PUT `<= {args.low_max}`.",
        "- Primary outcome: `30dte_generic`, `w_days=15` WR15 directional barrier.",
        "- Secondary context: `30dte_opt`, `w_days=15` option TP barrier.",
        "",
        "## Coverage",
        "",
        f"- Total pruned signal peaks: `{summary['total_peaks']:,}`",
        f"- Resolved WR15 peaks: `{summary['resolved_wr15_peaks']:,}`",
        f"- Unresolved WR15 peaks excluded from groupings: `{summary['unresolved_wr15_peaks']:,}`",
        "",
        "| cohort | N | WR15 | misses | miss rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for cohort, row in summary["cohorts"].items():
        lines.append(
            f"| {cohort} | {int(row['n']):,} | {_fmt_pct(row['wr15'])} | "
            f"{int(row['miss_count']):,} | {_fmt_pct(row['miss_rate'])} |"
        )

    lines.extend([
        "",
        "## Highest Informative Miss Groupings By Count",
        "",
        "These are sorted by miss count first after dropping whole-cohort/null buckets. They are communication-throughput leads, not ship evidence.",
        "",
        "| rank | type | cohort | pattern | misses | N | miss% | lift | z | hypothesis | next probe |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|---|",
    ])
    for idx, row in enumerate(top_all, start=1):
        lines.append(
            f"| {idx} | {row['grouping_type']} | {row['cohort']} | `{row['pattern']}` | "
            f"{int(row['miss_count']):,} | {int(row['n']):,} | {_fmt_pct(row['miss_rate'])} | "
            f"{float(row.get('lift') or 0.0):.2f} | {float(row.get('z') or 0.0):+.2f} | "
            f"{row['hypothesis']} | {row['next_probe']} |"
        )

    lines.extend([
        "",
        "## Highest Harmful Groupings",
        "",
        f"Same count sort, filtered to lift >= `{args.min_lift}` so high-count but baseline-quality groups do not dominate.",
        "",
        "| rank | type | cohort | pattern | misses | N | miss% | lift | z | hypothesis | next probe |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|---|",
    ])
    for idx, row in enumerate(top_harm, start=1):
        lines.append(
            f"| {idx} | {row['grouping_type']} | {row['cohort']} | `{row['pattern']}` | "
            f"{int(row['miss_count']):,} | {int(row['n']):,} | {_fmt_pct(row['miss_rate'])} | "
            f"{float(row.get('lift') or 0.0):.2f} | {float(row.get('z') or 0.0):+.2f} | "
            f"{row['hypothesis']} | {row['next_probe']} |"
        )

    lines.extend(["", "## Artifacts", ""])
    for label, path in outputs.items():
        lines.append(f"- `{label}`: `{path}`")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "status.json"
    _update_status(
        status_path,
        state="running",
        phase="start",
        started_at=_iso_now(),
        start=args.start,
        end=args.end,
        high_min=args.high_min,
        low_max=args.low_max,
        output_dir=str(out_dir),
    )

    from database.models.core import AlgorithmVersion

    version = AlgorithmVersion.get_by_id(args.version_id) if args.version_id else AlgorithmVersion.get_active_scores_version()
    version_meta = {
        "id": version.id,
        "production_label": version.production_label,
        "git_commit": version.git_commit,
        "git_message": version.git_message,
    }
    start = _parse_date(args.start)
    end = min(_parse_date(args.end), CUTOFF)
    if start > end:
        raise ValueError(f"start {start} is after end {end}")

    print(f"[miss-analysis] version={version.production_label} db:{version.id} {version.git_commit}", flush=True)
    print(f"[miss-analysis] window={start}..{end} signal=>={args.high_min}/<={args.low_max}", flush=True)

    raw_rows = _fetch_scores(version.id, start, end, args.high_min, args.low_max, status_path)
    _update_status(status_path, phase="prune_peaks", source_rows=len(raw_rows))
    peaks = raw_rows if args.no_peak_prune else _prune_peaks(raw_rows)
    print(f"[miss-analysis] signal peaks={len(peaks):,} prune={'off' if args.no_peak_prune else 'on'}", flush=True)

    joined = _join_barriers(peaks, out_dir, Path(args.cache_dir), status_path)
    binned_rows = []
    for row in joined:
        wr = row.get("wr15_result")
        tp = row.get("tp15_result")
        row["wr15_resolved"] = wr is not None
        row["tp15_resolved"] = tp is not None
        row["is_wr15_miss"] = None if wr is None else int(int(wr) != 1)
        row["is_tp15_miss"] = None if tp is None else int(int(tp) != 1)
        binned_rows.append(_add_bins(row))

    resolved = [row for row in binned_rows if row.get("wr15_result") is not None]
    missed = [row for row in resolved if int(row["is_wr15_miss"]) == 1]
    print(f"[miss-analysis] resolved={len(resolved):,} misses={len(missed):,}", flush=True)

    _update_status(status_path, phase="group_misses", resolved=len(resolved), missed=len(missed), total=len(binned_rows))
    groupings: list[dict[str, Any]] = []
    for cohort in COHORTS:
        label = cohort[0]
        cohort_data = _cohort_rows(resolved, cohort)
        if len(cohort_data) < args.min_cohort_n:
            continue
        root_rows = _root_groupings(cohort_data, label, args.min_single_n)
        feature_rows: list[dict[str, Any]] = []
        for feature in FEATURE_COLUMNS:
            feature_rows.extend(_feature_groupings(cohort_data, label, feature, args.min_single_n))
        ranked_features = []
        for row in sorted(feature_rows, key=lambda r: (int(r["miss_count"]), abs(float(r.get("z") or 0.0))), reverse=True):
            feature = row["pattern"].split("=", 1)[0]
            if feature not in ranked_features:
                ranked_features.append(feature)
            if len(ranked_features) >= args.pair_top_features:
                break
        pair_rows = _pair_groupings(cohort_data, label, ranked_features, args.min_pair_n)
        groupings.extend(root_rows)
        groupings.extend(feature_rows)
        groupings.extend(pair_rows)

    groupings_sorted = sorted(
        groupings,
        key=lambda r: (
            int(r.get("miss_count") or 0),
            float(r.get("pct_of_cohort_misses") or 0.0),
            abs(float(r.get("z") or 0.0)),
        ),
        reverse=True,
    )
    summary = _summary(binned_rows, resolved)
    outputs = {
        "all_peaks_csv": str(out_dir / "signal_peaks_with_outcomes.csv"),
        "all_peaks_jsonl": str(out_dir / "signal_peaks_with_outcomes.jsonl"),
        "misses_csv": str(out_dir / "wr15_missed_signals.csv"),
        "miss_groupings_csv": str(out_dir / "miss_groupings_by_count.csv"),
        "summary_json": str(out_dir / "summary.json"),
        "report_md": str(out_dir / "REPORT.md"),
    }

    base_fields = [
        "symbol",
        "date",
        "signal_type",
        "overall",
        "score_bin",
        "wr15_result",
        "is_wr15_miss",
        "wr15_exit_return",
        "wr15_mae_pct",
        "wr15_mfe_pct",
        "tp15_result",
        "is_tp15_miss",
        "ema50_pct",
        "ema200_pct",
        "bb_pos",
        "reg_mult",
        "vol_sig",
        "vol_mag",
        "bb",
        "trend",
        "rsi",
        "macd",
        "stoch",
        "ta",
        "velocity_7d",
        "root_labels",
    ]
    wi_fields = sorted(key for key in binned_rows[0].keys() if key.startswith("wi_")) if binned_rows else []
    _write_csv(Path(outputs["all_peaks_csv"]), binned_rows, base_fields + wi_fields)
    _write_jsonl(Path(outputs["all_peaks_jsonl"]), binned_rows)
    _write_csv(Path(outputs["misses_csv"]), missed, base_fields + wi_fields)
    _write_csv(Path(outputs["miss_groupings_csv"]), groupings_sorted)
    _write_json(Path(outputs["summary_json"]), {"version": version_meta, "summary": summary, "outputs": outputs})
    _write_report(
        Path(outputs["report_md"]),
        args=args,
        version_meta=version_meta,
        summary=summary,
        groupings=groupings_sorted,
        outputs=outputs,
    )

    top_groupings = _top_by_count(
        groupings_sorted,
        min_miss_count=args.min_miss_count,
        min_lift=None,
        limit=args.report_limit,
    )
    top_harmful_groupings = _top_by_count(
        groupings_sorted,
        min_miss_count=args.min_miss_count,
        min_lift=args.min_lift,
        limit=args.report_limit,
    )
    done = {
        "state": "done",
        "finished_at": _iso_now(),
        "version": version_meta,
        "summary": summary,
        "top_groupings": top_groupings,
        "top_harmful_groupings": top_harmful_groupings,
        "outputs": outputs,
    }
    _write_json(out_dir / "done.json", done)
    _update_status(status_path, state="done", phase="complete", finished_at=done["finished_at"], output_paths=outputs)
    return done


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build comprehensive miss groupings from active score rows")
    parser.add_argument("--version-id", type=int, default=None, help="DB AlgorithmVersion id; defaults to active scores version")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=CUTOFF.isoformat())
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--cache-dir", default=str(ROOT / ".cache"))
    parser.add_argument("--high-min", type=int, default=75)
    parser.add_argument("--low-max", type=int, default=25)
    parser.add_argument("--no-peak-prune", action="store_true")
    parser.add_argument("--min-cohort-n", type=int, default=80)
    parser.add_argument("--min-single-n", type=int, default=40)
    parser.add_argument("--min-pair-n", type=int, default=30)
    parser.add_argument("--pair-top-features", type=int, default=9)
    parser.add_argument("--min-miss-count", type=int, default=80)
    parser.add_argument("--min-lift", type=float, default=1.08)
    parser.add_argument("--report-limit", type=int, default=30)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        run(args)
    except Exception as exc:
        traceback_text = traceback.format_exc()
        print(traceback_text, file=sys.stderr, flush=True)
        _write_json(
            out_dir / "failed.json",
            {
                "state": "failed",
                "finished_at": _iso_now(),
                "error": str(exc),
                "traceback": traceback_text,
            },
        )
        _update_status(out_dir / "status.json", state="failed", phase="failed", error=str(exc))
        raise


if __name__ == "__main__":
    main()
