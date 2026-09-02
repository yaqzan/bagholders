"""Friday 1DTE option-premium analysis for top-score baskets.

This script consumes `depth_analysis.py` output and adds the option-premium
surface we can support from the current DB:

  - actual DB mark change: Thursday option-chain lastPrice to Friday lastPrice
  - modeled strategy P&L: Friday-open ATM entry premium to Friday expiration
    payoff, using IV-calibrated and realized-vol-calibrated premium estimates

The DB does not store option OHLC or timestamps for open/close option marks.
That means exact Friday-open-to-Friday-close premium change cannot be read
directly; the modeled rows are the live-strategy equivalent, while the actual
DB marks are empirical checks on same-contract premium behavior.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.trader_database import DB  # noqa: E402
from experiments.one_dte_friday.prelim_1dte_friday import (  # noqa: E402
    N_PRIME_0,
    TRADING_DAYS_PER_YEAR,
    load_earnings,
    load_weekly_options_for_pair,
    quantile,
    write_csv,
)
from iv_crush_model import adjust_pnl, find_spanning_earnings, kappa_post, kappa_pre  # noqa: E402


FLOAT_FIELDS = {
    "score_price",
    "signal_close",
    "friday_open",
    "friday_high",
    "friday_low",
    "friday_close",
    "signed_move",
    "sigma_60d",
    "signed_sigma_depth",
    "abs_sigma_depth",
    "signed_move_pct",
}

INT_FIELDS = {"overall", "rank_strength", "score_depth"}

DATE_FIELDS = {"signal_date", "friday"}


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def mean(values: Iterable[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(vals) / len(vals) if vals else None


def median(values: Iterable[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.median(vals) if vals else None


def pct(value: float | None, ndigits: int = 2) -> float | None:
    return None if value is None else round(value * 100.0, ndigits)


def rnd(value: float | None, ndigits: int = 4) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, ndigits)


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def read_signals(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for order, raw in enumerate(reader):
            row: dict[str, Any] = dict(raw)
            for key in DATE_FIELDS:
                row[key] = date.fromisoformat(str(row[key]))
            for key in FLOAT_FIELDS:
                row[key] = as_float(row.get(key))
            for key in INT_FIELDS:
                row[key] = int(float(row[key])) if row.get(key) not in (None, "") else None
            for key in ("direction_win", "win"):
                row[key] = parse_bool(row.get(key))
            row["score_depth"] = int(row.get("score_depth") or abs(int(row["overall"]) - 50))
            row["rank_strength"] = int(row.get("rank_strength") or row["score_depth"])
            row["_input_order"] = order
            out.append(row)
    return out


def threshold_pass(row: dict[str, Any], call_min: int | None, put_max: int | None) -> bool:
    if row["side"] == "call":
        return call_min is None or int(row["overall"]) >= call_min
    return put_max is None or int(row["overall"]) <= put_max


def select_variants(
    signals: list[dict[str, Any]],
    top_n: int,
    variants: dict[str, tuple[int | None, int | None]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_friday: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in signals:
        by_friday[row["friday"]].append(row)

    selected_by_variant: dict[str, list[dict[str, Any]]] = {}
    selected_keys: set[tuple[str, date, str, int]] = set()
    selected_rows: list[dict[str, Any]] = []

    for name, (call_min, put_max) in variants.items():
        rows: list[dict[str, Any]] = []
        for friday, day_rows in sorted(by_friday.items()):
            candidates = [r for r in day_rows if threshold_pass(r, call_min, put_max)]
            ranked = sorted(
                candidates,
                key=lambda r: int(r["score_depth"]),
                reverse=True,
            )[:top_n]
            for idx, row in enumerate(ranked, start=1):
                key = (row["symbol"], friday, row["side"], int(row["overall"]))
                clone = dict(row)
                clone["variant"] = name
                clone["variant_rank"] = idx
                clone["call_min"] = call_min
                clone["put_max"] = put_max
                rows.append(clone)
                if key not in selected_keys:
                    selected_keys.add(key)
                    selected_rows.append(row)
        selected_by_variant[name] = rows
    return selected_rows, selected_by_variant


def choose_atm(options: list[dict[str, Any]], underlying: float | None) -> dict[str, Any] | None:
    if not options or underlying is None or underlying <= 0:
        return None
    valid = [
        o for o in options
        if o.get("strike") is not None and o.get("price") is not None and o["price"] > 0
    ]
    if not valid:
        return None
    return min(valid, key=lambda o: (abs(o["strike"] - underlying) / underlying, -int(o.get("open_interest") or 0)))


def attach_options(selected_rows: list[dict[str, Any]], min_oi: int) -> None:
    by_pair: dict[tuple[date, date], list[dict[str, Any]]] = defaultdict(list)
    for row in selected_rows:
        by_pair[(row["signal_date"], row["friday"])].append(row)

    for idx, ((signal_date, friday), rows) in enumerate(sorted(by_pair.items()), start=1):
        symbols = sorted({r["symbol"] for r in rows})
        options = load_weekly_options_for_pair(symbols, signal_date, friday, min_oi=min_oi)
        for row in rows:
            chosen = choose_atm(options.get((row["symbol"], row["side"]), []), row.get("friday_open"))
            if not chosen:
                row["has_option_match"] = False
                continue
            row["has_option_match"] = True
            row["option_id"] = chosen["option_id"]
            row["option_strike"] = chosen["strike"]
            row["option_price_thu"] = chosen["price"]
            row["option_iv_thu"] = chosen["iv"]
            row["option_oi_thu"] = chosen["open_interest"]
            row["option_volume_thu"] = chosen["volume"]
            row["option_price_fri"] = chosen["friday_price"]
            row["option_iv_fri"] = chosen["friday_iv"]
            row["option_volume_fri"] = chosen["friday_volume"]

            friday_open = row.get("friday_open")
            signal_close = row.get("signal_close")
            if friday_open and friday_open > 0:
                row["option_moneyness_open"] = abs(chosen["strike"] - friday_open) / friday_open
                row["premium_actual_thu_open"] = chosen["price"] / friday_open
            if signal_close and signal_close > 0:
                row["option_moneyness_signal"] = abs(chosen["strike"] - signal_close) / signal_close
                row["premium_actual_thu_signal"] = chosen["price"] / signal_close
            if chosen["price"] and chosen["price"] > 0 and chosen["friday_price"] is not None:
                row["pnl_actual_db_thu_to_fri_mark"] = chosen["friday_price"] / chosen["price"] - 1.0

            payoff = expiration_payoff(row)
            row["expiry_payoff_abs"] = payoff
            row["expiry_payoff_frac_open"] = payoff / friday_open if friday_open and friday_open > 0 else None
            if chosen["friday_price"] is not None:
                row["fri_mark_minus_intrinsic"] = chosen["friday_price"] - payoff
        if idx % 20 == 0 or idx == len(by_pair):
            matched = sum(1 for r in selected_rows if r.get("has_option_match"))
            print(f"[option] {idx}/{len(by_pair)} weeks, matched={matched:,}/{len(selected_rows):,}", flush=True)


def propagate_option_fields(
    selected_rows: list[dict[str, Any]],
    selected_by_variant: dict[str, list[dict[str, Any]]],
) -> None:
    lookup = {
        (r["symbol"], r["friday"], r["side"], int(r["overall"])): r
        for r in selected_rows
    }
    for rows in selected_by_variant.values():
        for row in rows:
            source = lookup.get((row["symbol"], row["friday"], row["side"], int(row["overall"])))
            if source:
                for key, value in source.items():
                    if key not in {"variant", "variant_rank", "call_min", "put_max"}:
                        row[key] = value


def expiration_payoff(row: dict[str, Any]) -> float:
    strike = row.get("option_strike")
    close = row.get("friday_close")
    if strike is None or close is None:
        return 0.0
    if row["side"] == "call":
        return max(close - strike, 0.0)
    return max(strike - close, 0.0)


def calibrate(rows: list[dict[str, Any]], max_moneyness: float) -> dict[str, Any]:
    usable = [
        r for r in rows
        if r.get("has_option_match")
        and r.get("premium_actual_thu_signal") is not None
        and r.get("premium_actual_thu_signal") > 0
        and r.get("option_iv_thu") is not None
        and r.get("option_iv_thu") > 0.01
        and r.get("option_moneyness_signal") is not None
        and r["option_moneyness_signal"] <= max_moneyness
    ]

    def one(items: list[dict[str, Any]]) -> dict[str, Any]:
        premiums = []
        ivs = []
        sigmas = []
        k_iv = []
        k_sigma = []
        for row in items:
            prem = row.get("premium_actual_thu_signal")
            iv = row.get("option_iv_thu")
            sigma = row.get("sigma_60d")
            if prem is None or prem <= 0:
                continue
            premiums.append(prem)
            if iv is not None and iv > 0:
                ivs.append(iv)
                base = N_PRIME_0 * iv / math.sqrt(TRADING_DAYS_PER_YEAR)
                if base > 0:
                    k_iv.append(prem / base)
            if sigma is not None and sigma > 0:
                sigmas.append(sigma)
                k_sigma.append(prem / (sigma / 100.0))
        return {
            "n": len(items),
            "premium_median_pct": pct(median(premiums)),
            "premium_mean_pct": pct(mean(premiums)),
            "iv_median": rnd(median(ivs)),
            "sigma60_median_pct": rnd(median(sigmas), 3),
            "k_iv_median": rnd(median(k_iv)),
            "k_sigma_median": rnd(median(k_sigma)),
        }

    out = {"max_moneyness": max_moneyness, "all": one(usable), "by_side": {}}
    fallback_k_iv = out["all"].get("k_iv_median") or 1.0
    fallback_k_sigma = out["all"].get("k_sigma_median") or N_PRIME_0
    for side in ("call", "put"):
        rec = one([r for r in usable if r["side"] == side])
        rec["k_iv_used"] = rec.get("k_iv_median") or fallback_k_iv
        rec["k_sigma_used"] = rec.get("k_sigma_median") or fallback_k_sigma
        out["by_side"][side] = rec
    return out


def model_premium_iv(row: dict[str, Any], k_iv: float, session_fraction: float) -> float | None:
    iv = row.get("option_iv_thu")
    if iv is None or iv <= 0:
        return None
    return k_iv * N_PRIME_0 * iv / math.sqrt(TRADING_DAYS_PER_YEAR) * math.sqrt(max(session_fraction, 0.01))


def model_premium_sigma(row: dict[str, Any], k_sigma: float, session_fraction: float) -> float | None:
    sigma = row.get("sigma_60d")
    if sigma is None or sigma <= 0:
        return None
    return k_sigma * (sigma / 100.0) * math.sqrt(max(session_fraction, 0.01))


def apply_pricing(
    rows: list[dict[str, Any]],
    calibration: dict[str, Any],
    earnings: dict[str, list[date]],
    session_fraction: float,
) -> None:
    for row in rows:
        if not row.get("has_option_match"):
            continue
        k_iv = calibration["by_side"][row["side"]]["k_iv_used"]
        k_sigma = calibration["by_side"][row["side"]]["k_sigma_used"]
        p_iv = model_premium_iv(row, k_iv, session_fraction) or model_premium_sigma(row, k_sigma, session_fraction)
        p_sigma = model_premium_sigma(row, k_sigma, session_fraction)
        p_actual_thu = row.get("premium_actual_thu_open")
        payoff_frac = row.get("expiry_payoff_frac_open")

        row["premium_model_iv_open"] = p_iv
        row["premium_model_sigma_open"] = p_sigma
        row["breakeven_sigma_depth_iv"] = p_iv / (row["sigma_60d"] / 100.0) if p_iv and row.get("sigma_60d") else None
        row["breakeven_sigma_depth_sigma"] = p_sigma / (row["sigma_60d"] / 100.0) if p_sigma and row.get("sigma_60d") else None

        for mode, premium in (
            ("model_iv_open", p_iv),
            ("model_sigma_open", p_sigma),
            ("actual_thu_proxy", p_actual_thu),
        ):
            if premium is None or premium <= 0 or payoff_frac is None:
                row[f"pnl_{mode}"] = None
            else:
                row[f"pnl_{mode}"] = max(-1.0, payoff_frac / premium - 1.0)

        span = find_spanning_earnings(row["signal_date"], row["friday"], earnings.get(row["symbol"], []))
        if span:
            kp = kappa_pre((span - row["signal_date"]).days)
            kpost = kappa_post(row.get("sigma_60d"))
            row["earnings_effective_date"] = span
            row["earnings_spans_signal_to_friday"] = True
            row["iv_kpre"] = kp
            row["iv_kpost"] = kpost
            for mode in ("actual_thu_proxy", "actual_db_thu_to_fri_mark"):
                key = f"pnl_{mode}" if mode == "actual_thu_proxy" else "pnl_actual_db_thu_to_fri_mark"
                val = row.get(key)
                row[f"{key}_crush"] = adjust_pnl(val, kp, kpost) if val is not None else None
        else:
            row["earnings_spans_signal_to_friday"] = False
            row["pnl_actual_thu_proxy_crush"] = row.get("pnl_actual_thu_proxy")
            row["pnl_actual_db_thu_to_fri_mark_crush"] = row.get("pnl_actual_db_thu_to_fri_mark")


def apply_synthetic_sigma_pricing(
    rows: list[dict[str, Any]],
    calibration: dict[str, Any],
    session_fraction: float,
) -> None:
    for row in rows:
        k_sigma = calibration["by_side"][row["side"]]["k_sigma_used"]
        premium = model_premium_sigma(row, k_sigma, session_fraction)
        entry = row.get("friday_open")
        close = row.get("friday_close")
        if not premium or not entry or not close or entry <= 0 or close <= 0:
            row["pnl_synthetic_sigma_open"] = None
            continue
        if row["side"] == "call":
            payoff_frac = max(close - entry, 0.0) / entry
        else:
            payoff_frac = max(entry - close, 0.0) / entry
        row["premium_synthetic_sigma_open"] = premium
        row["breakeven_sigma_depth_synthetic"] = (
            premium / (row["sigma_60d"] / 100.0)
            if row.get("sigma_60d") and row["sigma_60d"] > 0
            else None
        )
        row["pnl_synthetic_sigma_open"] = max(-1.0, payoff_frac / premium - 1.0)


def summarize_trades(rows: list[dict[str, Any]], pnl_key: str) -> dict[str, Any]:
    usable = [r for r in rows if r.get(pnl_key) is not None]
    pnls = [r[pnl_key] for r in usable]
    sigma_depths = [r["signed_sigma_depth"] for r in usable if r.get("signed_sigma_depth") is not None]
    wins = [r for r in usable if r[pnl_key] > 0]
    return {
        "n": len(usable),
        "option_wr_pct": pct(mean([1.0 if p > 0 else 0.0 for p in pnls])),
        "mean_pnl_pct": pct(mean(pnls)),
        "median_pnl_pct": pct(median(pnls)),
        "p10_pnl_pct": pct(quantile(pnls, 0.10)),
        "p90_pnl_pct": pct(quantile(pnls, 0.90)),
        "avg_sigma_depth": rnd(mean(sigma_depths), 3),
        "median_sigma_depth": rnd(median(sigma_depths), 3),
        "avg_win_sigma_depth": rnd(mean([r["signed_sigma_depth"] for r in wins if r.get("signed_sigma_depth") is not None]), 3),
        "median_breakeven_sigma_depth_iv": rnd(median([r.get("breakeven_sigma_depth_iv") for r in usable]), 3),
        "earnings_spans": sum(1 for r in usable if r.get("earnings_spans_signal_to_friday")),
    }


def portfolio_summary(rows: list[dict[str, Any]], pnl_key: str, risk_fraction: float) -> dict[str, Any]:
    by_friday: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get(pnl_key) is not None:
            by_friday[row["friday"]].append(row)

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    week_rows = []
    for friday, trades in sorted(by_friday.items()):
        raw_trade_return = mean([r[pnl_key] for r in trades])
        if raw_trade_return is None:
            continue
        week_return = risk_fraction * raw_trade_return
        equity *= max(0.0, 1.0 + week_return)
        peak = max(peak, equity)
        dd = 0.0 if peak <= 0 else 1.0 - equity / peak
        max_dd = max(max_dd, dd)
        week_rows.append({
            "friday": friday,
            "week_return": week_return,
            "raw_option_return": raw_trade_return,
            "n": len(trades),
            "equity": equity,
            "drawdown": dd,
        })

    rets = [r["week_return"] for r in week_rows]
    raw = [r["raw_option_return"] for r in week_rows]
    return {
        "active_fridays": len(week_rows),
        "avg_trades_per_active_friday": rnd(mean([r["n"] for r in week_rows]), 2),
        "weekly_win_rate_pct": pct(mean([1.0 if r > 0 else 0.0 for r in rets])),
        "risk_fraction_per_friday_pct": pct(risk_fraction),
        "equity_multiple": rnd(equity, 4),
        "total_return_pct": pct(equity - 1.0),
        "max_drawdown_pct": pct(max_dd),
        "mean_week_return_pct": pct(mean(rets)),
        "median_week_return_pct": pct(median(rets)),
        "worst_week_pct": pct(min(rets) if rets else None),
        "best_week_pct": pct(max(rets) if rets else None),
        "mean_raw_option_return_pct": pct(mean(raw)),
        "median_raw_option_return_pct": pct(median(raw)),
    }


def variant_summary(
    rows: list[dict[str, Any]],
    calendar_fridays: int,
    risk_fraction: float,
) -> dict[str, Any]:
    selected = rows
    matched = [r for r in rows if r.get("has_option_match")]
    active_fridays = len({r["friday"] for r in selected})
    matched_fridays = len({r["friday"] for r in matched})
    return {
        "coverage": {
            "calendar_fridays": calendar_fridays,
            "selected_fridays": active_fridays,
            "selected_trades": len(selected),
            "matched_fridays": matched_fridays,
            "matched_trades": len(matched),
            "trade_match_rate_pct": pct(len(matched) / len(selected) if selected else None),
            "calls_selected": sum(1 for r in selected if r["side"] == "call"),
            "puts_selected": sum(1 for r in selected if r["side"] == "put"),
            "calls_matched": sum(1 for r in matched if r["side"] == "call"),
            "puts_matched": sum(1 for r in matched if r["side"] == "put"),
        },
        "depth": {
            "direction_wr_pct": pct(mean([1.0 if r.get("signed_move", 0) > 0 else 0.0 for r in selected])),
            "avg_signed_sigma_depth": rnd(mean([r.get("signed_sigma_depth") for r in selected]), 3),
            "matched_avg_signed_sigma_depth": rnd(mean([r.get("signed_sigma_depth") for r in matched]), 3),
        },
        "trade_pnl": {
            "model_iv_open": summarize_trades(matched, "pnl_model_iv_open"),
            "model_sigma_open": summarize_trades(matched, "pnl_model_sigma_open"),
            "actual_thu_proxy": summarize_trades(matched, "pnl_actual_thu_proxy"),
            "actual_thu_proxy_crush": summarize_trades(matched, "pnl_actual_thu_proxy_crush"),
        "actual_db_thu_to_fri_mark": summarize_trades(matched, "pnl_actual_db_thu_to_fri_mark"),
        "actual_db_thu_to_fri_mark_crush": summarize_trades(matched, "pnl_actual_db_thu_to_fri_mark_crush"),
        "synthetic_sigma_open_all": summarize_trades(selected, "pnl_synthetic_sigma_open"),
        },
        "portfolio": {
            "model_iv_open": portfolio_summary(matched, "pnl_model_iv_open", risk_fraction),
            "model_sigma_open": portfolio_summary(matched, "pnl_model_sigma_open", risk_fraction),
            "actual_thu_proxy": portfolio_summary(matched, "pnl_actual_thu_proxy", risk_fraction),
            "actual_thu_proxy_crush": portfolio_summary(matched, "pnl_actual_thu_proxy_crush", risk_fraction),
            "actual_db_thu_to_fri_mark": portfolio_summary(matched, "pnl_actual_db_thu_to_fri_mark", risk_fraction),
            "actual_db_thu_to_fri_mark_crush": portfolio_summary(matched, "pnl_actual_db_thu_to_fri_mark_crush", risk_fraction),
            "synthetic_sigma_open_all": portfolio_summary(selected, "pnl_synthetic_sigma_open", risk_fraction),
        },
    }


def annual_summary(rows: list[dict[str, Any]], risk_fraction: float) -> dict[str, Any]:
    years = sorted({r["friday"].year for r in rows})
    out: dict[str, Any] = {}
    for year in years:
        year_rows = [r for r in rows if r["friday"].year == year]
        out[str(year)] = {
            "coverage": {
                "selected_fridays": len({r["friday"] for r in year_rows}),
                "selected_trades": len(year_rows),
                "matched_fridays": len({r["friday"] for r in year_rows if r.get("has_option_match")}),
                "matched_trades": sum(1 for r in year_rows if r.get("has_option_match")),
            },
            "portfolio_model_iv_open": portfolio_summary(
                [r for r in year_rows if r.get("has_option_match")],
                "pnl_model_iv_open",
                risk_fraction,
            ),
            "portfolio_actual_db_mark": portfolio_summary(
                [r for r in year_rows if r.get("has_option_match")],
                "pnl_actual_db_thu_to_fri_mark",
                risk_fraction,
            ),
            "portfolio_synthetic_sigma_all": portfolio_summary(
                year_rows,
                "pnl_synthetic_sigma_open",
                risk_fraction,
            ),
            "trade_model_iv_open": summarize_trades(
                [r for r in year_rows if r.get("has_option_match")],
                "pnl_model_iv_open",
            ),
            "trade_synthetic_sigma_all": summarize_trades(
                year_rows,
                "pnl_synthetic_sigma_open",
            ),
        }
    return out


def parse_variants(value: str) -> dict[str, tuple[int | None, int | None]]:
    presets = {
        "none": (None, None),
        "call85_put15": (85, 15),
        "call80_put20": (80, 20),
        "call85_put20": (85, 20),
        "call80_put15": (80, 15),
    }
    out: dict[str, tuple[int | None, int | None]] = {}
    for token in [p.strip() for p in value.split(",") if p.strip()]:
        if token in presets:
            out[token] = presets[token]
            continue
        if ":" not in token:
            raise ValueError(f"unknown variant '{token}'")
        name, spec = token.split(":", 1)
        call_min = None
        put_max = None
        for part in spec.split("/"):
            side, raw = part.split(">=" if ">=" in part else "<=", 1)
            side = side.strip().lower()
            val = int(raw)
            if side in {"call", "c"}:
                call_min = val
            elif side in {"put", "p"}:
                put_max = val
        out[name] = (call_min, put_max)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals-csv", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--min-oi", type=int, default=1)
    parser.add_argument("--session-fraction", type=float, default=0.5)
    parser.add_argument("--premium-calibration-moneyness", type=float, default=0.03)
    parser.add_argument("--risk-fraction", type=float, default=0.10)
    parser.add_argument("--variants", default="none,call85_put15,call80_put20")
    args = parser.parse_args()

    started = time.time()
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    DB.connect(reuse_if_open=True)
    signals = read_signals(Path(args.signals_csv))
    calendar_fridays = len({r["friday"] for r in signals})
    variants = parse_variants(args.variants)
    selected_rows, selected_by_variant = select_variants(signals, args.top_n, variants)
    print(
        f"[select] calendar_fridays={calendar_fridays} unique_selected={len(selected_rows)} "
        f"variants={','.join(variants)}",
        flush=True,
    )

    attach_options(selected_rows, min_oi=args.min_oi)
    calibration = calibrate(selected_rows, args.premium_calibration_moneyness)
    print(f"[calibrate] {json.dumps(calibration, default=str)}", flush=True)

    symbols = sorted({r["symbol"] for r in selected_rows})
    min_date = min(r["signal_date"] for r in selected_rows)
    max_date = max(r["friday"] for r in selected_rows)
    earnings = load_earnings(symbols, min_date, max_date)
    apply_pricing(selected_rows, calibration, earnings, args.session_fraction)
    apply_synthetic_sigma_pricing(selected_rows, calibration, args.session_fraction)
    propagate_option_fields(selected_rows, selected_by_variant)

    summary = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "contract": (
            "Thursday scores select Friday top-N combined CALL/PUT signals; "
            "actual DB option P&L is Thu lastPrice to Fri lastPrice; "
            "modeled P&L is Friday-open ATM premium to Friday expiration payoff."
        ),
        "data_caveat": (
            "option_prices stores one daily yfinance lastPrice/IV row, not option OHLC; "
            "exact Friday open-to-close option premium is therefore modeled."
        ),
        "settings": {
            "signals_csv": str(Path(args.signals_csv).resolve()),
            "top_n": args.top_n,
            "min_open_interest": args.min_oi,
            "session_fraction": args.session_fraction,
            "premium_calibration_moneyness": args.premium_calibration_moneyness,
            "risk_fraction": args.risk_fraction,
            "variants": args.variants,
        },
        "coverage": {
            "calendar_fridays": calendar_fridays,
            "input_signals": len(signals),
            "unique_selected_trades": len(selected_rows),
            "unique_matched_trades": sum(1 for r in selected_rows if r.get("has_option_match")),
            "unique_match_rate_pct": pct(
                sum(1 for r in selected_rows if r.get("has_option_match")) / len(selected_rows)
                if selected_rows else None
            ),
            "option_date_span": {
                "min_selected_signal_date": min_date.isoformat(),
                "max_selected_friday": max_date.isoformat(),
            },
        },
        "premium_calibration": calibration,
        "variants": {
            name: {
                "total": variant_summary(rows, calendar_fridays, args.risk_fraction),
                "annual": annual_summary(rows, args.risk_fraction),
            }
            for name, rows in selected_by_variant.items()
        },
        "runtime_sec": round(time.time() - started, 2),
    }

    write_csv(run_dir / "selected_option_trades.csv", selected_rows)
    variant_rows = []
    for rows in selected_by_variant.values():
        variant_rows.extend(rows)
    write_csv(run_dir / "variant_option_trades.csv", variant_rows)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (run_dir / "done.json").write_text(
        json.dumps(
            {
                "state": "done",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "summary_json": str(run_dir / "summary.json"),
                "selected_csv": str(run_dir / "selected_option_trades.csv"),
                "variant_csv": str(run_dir / "variant_option_trades.csv"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[done] summary={run_dir / 'summary.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
