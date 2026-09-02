"""Preliminary Friday-only 1DTE strategy test.

Uses prior-session scores, Friday open-to-close underlying movement, and
same-week option-chain rows to estimate whether a Friday-only 1DTE path is
worth deeper validation.

This is deliberately experiment-only:
  - no Score/AlgorithmVersion writes
  - no strategy_config changes
  - artifacts are written under the supplied run directory

Default test:
  - active research target: v58 scores
  - signal: Thursday close score, HIGH>=75 => call, LOW<=25 => put
  - trade: Friday open to Friday close
  - option payoff: ATM-at-Friday-open expiration payoff
  - premium modes:
      actual_thu: prior-session ATM weekly option price / Friday open
      iv_1d:      IV-calibrated one-day premium
      iv_open:    IV-calibrated one-session premium
  - earnings-aware stress: applies iv_crush_model kappa_pre/kappa_post to
    trades whose signal-to-Friday window spans an effective earnings date.
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
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.trader_database import DB  # noqa: E402
from experiments._holdout import CUTOFF  # noqa: E402
from iv_crush_model import adjust_pnl, find_spanning_earnings, kappa_post, kappa_pre  # noqa: E402


N_PRIME_0 = 1.0 / math.sqrt(2.0 * math.pi)
TRADING_DAYS_PER_YEAR = 252.0


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def median(values: Iterable[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.median(vals) if vals else None


def mean(values: Iterable[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(vals) / len(vals) if vals else None


def pct(value: float | None) -> float | None:
    return None if value is None else value * 100.0


def pct_round(value: float | None, ndigits: int = 2) -> float | None:
    return None if value is None else round(value * 100.0, ndigits)


def round_or_none(value: float | None, ndigits: int = 4) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, ndigits)


class RunStatus:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.status_path = run_dir / "status.json"
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def write(self, phase: str, state: str = "running", **extra: Any) -> None:
        payload = {
            "phase": phase,
            "state": state,
            "started_at": self.started_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "output_paths": {
                "run_dir": str(self.run_dir),
                "run_log": str(self.run_dir / "run.log"),
                "signals_csv": str(self.run_dir / "signals.csv"),
                "portfolio_csv": str(self.run_dir / "portfolio_equity.csv"),
                "summary_json": str(self.run_dir / "summary.json"),
            },
        }
        payload.update(extra)
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.status_path)


def db_execute(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    return list(DB.execute_sql(sql, params).fetchall())


def chunked(seq: list[Any], n: int) -> Iterable[list[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def sql_in_placeholders(items: list[Any]) -> str:
    return ",".join(["%s"] * len(items))


def latest_db_date(table: str, column: str = "date") -> date | None:
    row = DB.execute_sql(f"SELECT MAX({column}) FROM {table}").fetchone()
    return row[0] if row else None


def load_trading_days(start: date, end: date) -> list[date]:
    rows = db_execute(
        """
        SELECT date
        FROM price_history
        WHERE symbol = 'SPY' AND date BETWEEN %s AND %s
        ORDER BY date
        """,
        (start, end),
    )
    if not rows:
        rows = db_execute(
            """
            SELECT DISTINCT date
            FROM price_history FORCE INDEX (price_history_date_IDX)
            WHERE date BETWEEN %s AND %s
            ORDER BY date
            """,
            (start, end),
        )
    return [r[0] for r in rows]


def friday_signal_pairs(trading_days: list[date], start: date, end: date, strict_thursday: bool) -> list[dict[str, date]]:
    pairs: list[dict[str, date]] = []
    for i, d in enumerate(trading_days):
        if d < start or d > end or d.weekday() != 4 or i == 0:
            continue
        prev = trading_days[i - 1]
        if strict_thursday and prev.weekday() != 3:
            continue
        pairs.append({"signal_date": prev, "friday": d})
    return pairs


def load_score_signals(
    pairs: list[dict[str, date]],
    version: int,
    score_hi: int,
    score_lo: int,
    status: RunStatus,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    total = len(pairs)
    for idx, pair in enumerate(pairs, start=1):
        signal_date = pair["signal_date"]
        friday = pair["friday"]
        rows = db_execute(
            """
            SELECT s.symbol, s.overall, s.price,
                   ph_sig.close AS signal_close,
                   ph_fri.open AS friday_open,
                   ph_fri.high AS friday_high,
                   ph_fri.low AS friday_low,
                   ph_fri.close AS friday_close
            FROM scores s FORCE INDEX (scores_version_date_IDX)
            JOIN price_history ph_sig
              ON ph_sig.symbol = s.symbol AND ph_sig.date = s.date
            JOIN price_history ph_fri
              ON ph_fri.symbol = s.symbol AND ph_fri.date = %s
            WHERE s.version_id = %s
              AND s.date = %s
              AND (s.overall >= %s OR s.overall <= %s)
            """,
            (friday, version, signal_date, score_hi, score_lo),
        )
        for symbol, overall, score_price, signal_close, friday_open, friday_high, friday_low, friday_close in rows:
            overall_i = int(overall)
            side = "call" if overall_i >= score_hi else "put"
            entry = as_float(friday_open)
            exit_px = as_float(friday_close)
            if entry is None or exit_px is None or entry <= 0 or exit_px <= 0:
                continue
            signed_move = (exit_px / entry - 1.0) if side == "call" else (entry / exit_px - 1.0)
            direction_win = signed_move > 0
            out.append({
                "symbol": symbol,
                "signal_date": signal_date,
                "friday": friday,
                "overall": overall_i,
                "side": side,
                "score_price": as_float(score_price),
                "signal_close": as_float(signal_close),
                "friday_open": entry,
                "friday_high": as_float(friday_high),
                "friday_low": as_float(friday_low),
                "friday_close": exit_px,
                "signed_move": signed_move,
                "direction_win": direction_win,
                "rank_strength": abs(overall_i - 50),
            })
        if idx % 5 == 0 or idx == total:
            status.write(
                "score_extract",
                completed=idx,
                total=total,
                current_candidate=f"{signal_date}->{friday}",
                signals=len(out),
            )
            print(f"[score_extract] {idx}/{total} weeks, signals={len(out):,}", flush=True)
    return out


def load_realized_sigma(signals: list[dict[str, Any]], start: date, end: date, lookback: int = 60) -> dict[tuple[str, date], float]:
    symbols = sorted({s["symbol"] for s in signals})
    earliest = start - timedelta(days=int(lookback * 2.2) + 10)
    closes_by_symbol: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for chunk in chunked(symbols, 80):
        ph = sql_in_placeholders(chunk)
        rows = db_execute(
            f"""
            SELECT symbol, date, close
            FROM price_history
            WHERE symbol IN ({ph}) AND date BETWEEN %s AND %s
            ORDER BY symbol, date
            """,
            tuple(chunk + [earliest, end]),
        )
        for symbol, d, close in rows:
            c = as_float(close)
            if c is not None and c > 0:
                closes_by_symbol[symbol].append((d, c))

    sigma: dict[tuple[str, date], float] = {}
    wanted = {(s["symbol"], s["signal_date"]) for s in signals}
    wanted_by_symbol: dict[str, set[date]] = defaultdict(set)
    for sym, d in wanted:
        wanted_by_symbol[sym].add(d)

    for sym, rows in closes_by_symbol.items():
        closes: list[float] = []
        dates: list[date] = []
        rets: list[float] = []
        for d, close in rows:
            if closes and closes[-1] > 0:
                rets.append(close / closes[-1] - 1.0)
            closes.append(close)
            dates.append(d)
            if d not in wanted_by_symbol.get(sym, set()):
                continue
            recent = rets[-lookback:]
            if len(recent) < max(20, lookback // 2):
                continue
            mu = sum(recent) / len(recent)
            var = sum((r - mu) ** 2 for r in recent) / len(recent)
            sigma[(sym, d)] = math.sqrt(var) * 100.0
    return sigma


def load_earnings(symbols: list[str], start: date, end: date) -> dict[str, list[date]]:
    out: dict[str, list[date]] = defaultdict(list)
    for chunk in chunked(symbols, 120):
        ph = sql_in_placeholders(chunk)
        rows = db_execute(
            f"""
            SELECT symbol, COALESCE(effective_date, date) AS effective_date
            FROM earnings_dates
            WHERE symbol IN ({ph})
              AND COALESCE(effective_date, date) BETWEEN %s AND %s
            ORDER BY symbol, COALESCE(effective_date, date)
            """,
            tuple(chunk + [start, end]),
        )
        for symbol, effective_date in rows:
            out[symbol].append(effective_date)
    return {k: sorted(set(v)) for k, v in out.items()}


def load_weekly_options_for_pair(
    symbols: list[str],
    signal_date: date,
    friday: date,
    min_oi: int,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunked(symbols, 60):
        ph = sql_in_placeholders(chunk)
        rows = db_execute(
            f"""
            SELECT o.id, o.symbol, LOWER(o.option_type) AS option_type,
                   o.strike_price, op.price, op.iv, op.volume, op.open_interest,
                   op2.price AS friday_price, op2.iv AS friday_iv,
                   op2.volume AS friday_volume
            FROM options o FORCE INDEX (idx_opt_sym_type_exp_strike)
            JOIN option_prices op FORCE INDEX (idx_op_covering)
              ON op.option_id = o.id AND op.date = %s
            LEFT JOIN option_prices op2 FORCE INDEX (idx_op_covering)
              ON op2.option_id = o.id AND op2.date = %s
            WHERE o.symbol IN ({ph})
              AND o.expiration_date = %s
              AND LOWER(o.option_type) IN ('call', 'put')
              AND op.price > 0
              AND op.open_interest >= %s
            """,
            tuple([signal_date, friday] + chunk + [friday, min_oi]),
        )
        for row in rows:
            option_id, symbol, side, strike, price, iv, volume, open_interest, friday_price, friday_iv, friday_volume = row
            item = {
                "option_id": int(option_id),
                "symbol": symbol,
                "side": side,
                "strike": as_float(strike),
                "price": as_float(price),
                "iv": as_float(iv),
                "volume": as_int(volume) or 0,
                "open_interest": as_int(open_interest) or 0,
                "friday_price": as_float(friday_price),
                "friday_iv": as_float(friday_iv),
                "friday_volume": as_int(friday_volume) if friday_volume is not None else None,
            }
            out[(symbol, side)].append(item)
    return out


def choose_prior_atm(options: list[dict[str, Any]], signal_close: float | None) -> dict[str, Any] | None:
    if not options or signal_close is None or signal_close <= 0:
        return None
    valid = [o for o in options if o.get("strike") is not None and o.get("price") is not None and o["price"] > 0]
    if not valid:
        return None
    return min(valid, key=lambda o: (abs(o["strike"] - signal_close) / signal_close, -int(o.get("open_interest") or 0)))


def attach_option_rows(
    signals: list[dict[str, Any]],
    pairs: list[dict[str, date]],
    min_oi: int,
    status: RunStatus,
) -> None:
    by_pair: dict[tuple[date, date], list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        by_pair[(signal["signal_date"], signal["friday"])].append(signal)

    total = len(pairs)
    for idx, pair in enumerate(pairs, start=1):
        key = (pair["signal_date"], pair["friday"])
        week_signals = by_pair.get(key, [])
        if not week_signals:
            continue
        symbols = sorted({s["symbol"] for s in week_signals})
        opt_map = load_weekly_options_for_pair(symbols, pair["signal_date"], pair["friday"], min_oi=min_oi)
        for signal in week_signals:
            options = opt_map.get((signal["symbol"], signal["side"]), [])
            chosen = choose_prior_atm(options, signal["signal_close"])
            if not chosen:
                signal["has_option_match"] = False
                continue
            signal["has_option_match"] = True
            signal["option_id"] = chosen["option_id"]
            signal["option_strike"] = chosen["strike"]
            signal["option_price_thu"] = chosen["price"]
            signal["option_iv_thu"] = chosen["iv"]
            signal["option_oi_thu"] = chosen["open_interest"]
            signal["option_volume_thu"] = chosen["volume"]
            signal["option_price_fri"] = chosen["friday_price"]
            signal["option_iv_fri"] = chosen["friday_iv"]
            if signal["signal_close"]:
                signal["option_moneyness_thu"] = abs(chosen["strike"] - signal["signal_close"]) / signal["signal_close"]
                signal["premium_actual_thu_signal"] = chosen["price"] / signal["signal_close"]
            if signal["friday_open"]:
                signal["premium_actual_thu_open"] = chosen["price"] / signal["friday_open"]
            if chosen["friday_price"] is not None and chosen["price"] and chosen["price"] > 0:
                signal["actual_option_pnl_thu_to_fri_close"] = chosen["friday_price"] / chosen["price"] - 1.0
        if idx % 5 == 0 or idx == total:
            matched = sum(1 for s in signals if s.get("has_option_match"))
            status.write(
                "option_extract",
                completed=idx,
                total=total,
                current_candidate=f"{pair['signal_date']}->{pair['friday']}",
                matched=matched,
                signals=len(signals),
            )
            print(f"[option_extract] {idx}/{total} weeks, matched={matched:,}/{len(signals):,}", flush=True)


def calibrate_premiums(signals: list[dict[str, Any]], max_moneyness: float) -> dict[str, Any]:
    rows = [
        s for s in signals
        if s.get("has_option_match")
        and s.get("premium_actual_thu_signal") is not None
        and s.get("option_iv_thu") is not None
        and s.get("option_iv_thu") > 0.01
        and s.get("option_moneyness_thu") is not None
        and s["option_moneyness_thu"] <= max_moneyness
    ]
    out: dict[str, Any] = {"max_moneyness": max_moneyness, "all": {}, "by_side": {}}

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        k_iv = []
        k_sigma = []
        premiums = []
        ivs = []
        sigmas = []
        for s in items:
            prem = s.get("premium_actual_thu_signal")
            iv = s.get("option_iv_thu")
            sigma = s.get("sigma_60d")
            if prem is None or prem <= 0:
                continue
            premiums.append(prem)
            if iv is not None and iv > 0:
                base_iv = N_PRIME_0 * iv / math.sqrt(TRADING_DAYS_PER_YEAR)
                if base_iv > 0:
                    k_iv.append(prem / base_iv)
                    ivs.append(iv)
            if sigma is not None and sigma > 0:
                sigma_frac = sigma / 100.0
                k_sigma.append(prem / sigma_frac)
                sigmas.append(sigma)
        return {
            "n": len(items),
            "premium_median_pct": pct_round(median(premiums)),
            "premium_mean_pct": pct_round(mean(premiums)),
            "iv_median": round_or_none(median(ivs), 4),
            "sigma60_median_pct": round_or_none(median(sigmas), 3),
            "k_iv_median": round_or_none(median(k_iv), 4),
            "k_iv_mean": round_or_none(mean(k_iv), 4),
            "k_sigma_median": round_or_none(median(k_sigma), 4),
            "k_sigma_mean": round_or_none(mean(k_sigma), 4),
        }

    out["all"] = summarize(rows)
    for side in ("call", "put"):
        out["by_side"][side] = summarize([s for s in rows if s["side"] == side])

    fallback_k_iv = out["all"].get("k_iv_median") or 1.0
    fallback_k_sigma = out["all"].get("k_sigma_median") or N_PRIME_0
    for side in ("call", "put"):
        out["by_side"][side]["k_iv_used"] = out["by_side"][side].get("k_iv_median") or fallback_k_iv
        out["by_side"][side]["k_sigma_used"] = out["by_side"][side].get("k_sigma_median") or fallback_k_sigma
    return out


def base_iv_premium(iv: float | None, k_iv: float, session_fraction: float = 1.0) -> float | None:
    if iv is None or iv <= 0:
        return None
    return k_iv * N_PRIME_0 * iv / math.sqrt(TRADING_DAYS_PER_YEAR) * math.sqrt(max(session_fraction, 0.01))


def sigma_premium(sigma_pct: float | None, k_sigma: float, session_fraction: float = 1.0) -> float | None:
    if sigma_pct is None or sigma_pct <= 0:
        return None
    return k_sigma * (sigma_pct / 100.0) * math.sqrt(max(session_fraction, 0.01))


def payoff_pnl(side: str, entry_underlying: float, exit_underlying: float, premium_frac: float | None) -> float | None:
    if premium_frac is None or premium_frac <= 0 or entry_underlying <= 0 or exit_underlying <= 0:
        return None
    if side == "call":
        payoff_frac = max(exit_underlying - entry_underlying, 0.0) / entry_underlying
    else:
        payoff_frac = max(entry_underlying - exit_underlying, 0.0) / entry_underlying
    return max(-1.0, payoff_frac / premium_frac - 1.0)


def crush_adjusted_pnl(
    baseline_pnl: float | None,
    signal_date: date,
    friday: date,
    sigma_pct: float | None,
    earnings_dates: list[date],
) -> tuple[float | None, dict[str, Any]]:
    if baseline_pnl is None:
        return None, {}
    if not earnings_dates:
        return baseline_pnl, {}
    span_ed = find_spanning_earnings(signal_date, friday, earnings_dates)
    if span_ed is None:
        # A Friday-expiring contract does not carry next-week earnings risk.
        # Only same-window effective earnings dates get the IV-crush stress.
        return baseline_pnl, {}
    if span_ed is not None:
        d2e = (span_ed - signal_date).days
        kp = kappa_pre(d2e)
        kpost = kappa_post(sigma_pct)
        return adjust_pnl(baseline_pnl, kp, kpost), {
            "earnings_effective_date": span_ed,
            "earnings_spans_signal_to_friday": True,
            "iv_kpre": kp,
            "iv_kpost": kpost,
        }


def attach_pricing(
    signals: list[dict[str, Any]],
    premium_calibration: dict[str, Any],
    earnings: dict[str, list[date]],
    session_fraction: float,
) -> None:
    for s in signals:
        k_iv = premium_calibration["by_side"][s["side"]]["k_iv_used"]
        k_sigma = premium_calibration["by_side"][s["side"]]["k_sigma_used"]
        iv = s.get("option_iv_thu")
        sigma = s.get("sigma_60d")
        p_iv_1d = base_iv_premium(iv, k_iv, session_fraction=1.0)
        if p_iv_1d is None:
            p_iv_1d = sigma_premium(sigma, k_sigma, session_fraction=1.0)
            s["premium_iv_source"] = "sigma_fallback"
        else:
            s["premium_iv_source"] = "option_iv"

        p_iv_open = base_iv_premium(iv, k_iv, session_fraction=session_fraction)
        if p_iv_open is None:
            p_iv_open = sigma_premium(sigma, k_sigma, session_fraction=session_fraction)

        s["premium_iv_1d"] = p_iv_1d
        s["premium_iv_open"] = p_iv_open

        # Conservative actual premium: prior-session ATM weekly option close,
        # expressed against the Friday-open underlying.
        p_actual = s.get("premium_actual_thu_open")

        for mode, premium in (
            ("actual_thu", p_actual),
            ("iv_1d", p_iv_1d),
            ("iv_open", p_iv_open),
        ):
            pnl = payoff_pnl(s["side"], s["friday_open"], s["friday_close"], premium)
            s[f"pnl_{mode}_raw"] = pnl
            adj, meta = crush_adjusted_pnl(
                pnl,
                s["signal_date"],
                s["friday"],
                sigma,
                earnings.get(s["symbol"], []),
            )
            s[f"pnl_{mode}_crush"] = adj
            if meta:
                for k, v in meta.items():
                    s[k] = v
            if premium is not None:
                s[f"win_{mode}_raw"] = pnl is not None and pnl > 0
                s[f"win_{mode}_crush"] = adj is not None and adj > 0
                s[f"breakeven_move_{mode}"] = premium


def summarize_trades(signals: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    rows = [s for s in signals if s.get(f"pnl_{mode}") is not None]
    pnls = [s[f"pnl_{mode}"] for s in rows]
    moves = [s["signed_move"] for s in rows]
    premiums = [s.get(f"breakeven_move_{mode.replace('_crush', '').replace('_raw', '')}") for s in rows]
    wins = [p > 0 for p in pnls]
    direction_wins = [bool(s["direction_win"]) for s in rows]
    return {
        "n": len(rows),
        "direction_wr_pct": round_or_none(mean([1.0 if x else 0.0 for x in direction_wins]) * 100 if rows else None, 2),
        "option_wr_pct": round_or_none(mean([1.0 if x else 0.0 for x in wins]) * 100 if rows else None, 2),
        "mean_pnl_pct": pct_round(mean(pnls)),
        "median_pnl_pct": pct_round(median(pnls)),
        "p10_pnl_pct": pct_round(quantile(pnls, 0.10)),
        "p90_pnl_pct": pct_round(quantile(pnls, 0.90)),
        "mean_signed_move_pct": pct_round(mean(moves)),
        "median_signed_move_pct": pct_round(median(moves)),
        "median_breakeven_move_pct": pct_round(median([p for p in premiums if p is not None])),
        "earnings_spans": sum(1 for s in rows if s.get("earnings_spans_signal_to_friday")),
    }


def quantile(values: Iterable[float], q: float) -> float | None:
    vals = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def portfolio_runs(signals: list[dict[str, Any]], mode: str, topks: list[int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_friday: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for s in signals:
        if s.get(f"pnl_{mode}") is not None:
            by_friday[s["friday"]].append(s)

    equity_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for topk in topks:
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        weekly_rets: list[float] = []
        trade_counts: list[int] = []
        wins = 0
        weeks = 0
        worst_week = None
        best_week = None
        for friday in sorted(by_friday):
            candidates = sorted(
                by_friday[friday],
                key=lambda s: (s["rank_strength"], s["overall"] if s["side"] == "call" else 100 - s["overall"]),
                reverse=True,
            )[:topk]
            if not candidates:
                continue
            pnls = [s[f"pnl_{mode}"] for s in candidates]
            week_ret = sum(pnls) / len(pnls)
            equity *= max(0.0, 1.0 + week_ret)
            peak = max(peak, equity)
            dd = 0.0 if peak <= 0 else 1.0 - equity / peak
            max_dd = max(max_dd, dd)
            weekly_rets.append(week_ret)
            trade_counts.append(len(candidates))
            wins += 1 if week_ret > 0 else 0
            weeks += 1
            worst_week = week_ret if worst_week is None else min(worst_week, week_ret)
            best_week = week_ret if best_week is None else max(best_week, week_ret)
            equity_rows.append({
                "mode": mode,
                "topk": topk,
                "friday": friday.isoformat(),
                "n_trades": len(candidates),
                "week_return": week_ret,
                "equity": equity,
                "drawdown": dd,
                "symbols": ";".join(s["symbol"] for s in candidates),
            })
        summary[str(topk)] = {
            "weeks": weeks,
            "total_trades": sum(trade_counts),
            "avg_trades_per_week": round_or_none(mean(trade_counts), 2),
            "weekly_win_rate_pct": round_or_none((wins / weeks * 100.0) if weeks else None, 2),
            "final_equity_multiple": round_or_none(equity, 4),
            "total_return_pct": pct_round(equity - 1.0),
            "max_drawdown_pct": pct_round(max_dd),
            "mean_week_return_pct": pct_round(mean(weekly_rets)),
            "median_week_return_pct": pct_round(median(weekly_rets)),
            "worst_week_pct": pct_round(worst_week),
            "best_week_pct": pct_round(best_week),
        }
    return equity_rows, summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen = set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            clean = {}
            for k in fieldnames:
                v = row.get(k)
                if isinstance(v, (date, datetime)):
                    clean[k] = v.isoformat()
                elif isinstance(v, float):
                    clean[k] = round(v, 8) if math.isfinite(v) else ""
                elif v is None:
                    clean[k] = ""
                else:
                    clean[k] = v
            writer.writerow(clean)


def build_summary(
    signals: list[dict[str, Any]],
    pairs: list[dict[str, date]],
    premium_calibration: dict[str, Any],
    portfolio_summary: dict[str, Any],
    args: argparse.Namespace,
    start: date,
    end: date,
) -> dict[str, Any]:
    matched = [s for s in signals if s.get("has_option_match")]
    modes = [
        "actual_thu_raw",
        "actual_thu_crush",
        "iv_1d_raw",
        "iv_1d_crush",
        "iv_open_raw",
        "iv_open_crush",
    ]
    trade_summary = {m: summarize_trades(signals, m) for m in modes}
    side_summary: dict[str, Any] = {}
    for side in ("call", "put"):
        side_rows = [s for s in signals if s["side"] == side]
        side_summary[side] = {
            "signals": len(side_rows),
            "matched": sum(1 for s in side_rows if s.get("has_option_match")),
            "direction_wr_pct": round_or_none(mean([1.0 if s["direction_win"] else 0.0 for s in side_rows]) * 100 if side_rows else None, 2),
            "iv_open_crush": summarize_trades(side_rows, "iv_open_crush"),
        }

    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "version": args.version,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "cutoff": CUTOFF.isoformat()},
        "settings": {
            "score_hi": args.score_hi,
            "score_lo": args.score_lo,
            "strict_thursday": args.strict_thursday,
            "min_open_interest": args.min_oi,
            "premium_calibration_moneyness": args.premium_calibration_moneyness,
            "session_fraction": args.session_fraction,
            "topk": args.topk,
        },
        "coverage": {
            "friday_weeks": len(pairs),
            "score_signals": len(signals),
            "option_matched": len(matched),
            "option_match_rate_pct": round_or_none((len(matched) / len(signals) * 100.0) if signals else None, 2),
            "earnings_spans_signal_to_friday": sum(1 for s in signals if s.get("earnings_spans_signal_to_friday")),
        },
        "premium_calibration": premium_calibration,
        "trade_summary": trade_summary,
        "side_summary": side_summary,
        "portfolio_summary": portfolio_summary,
    }


def parse_topk(value: str) -> list[int]:
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--version", type=int, default=58)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--score-hi", type=int, default=75)
    parser.add_argument("--score-lo", type=int, default=25)
    parser.add_argument("--strict-thursday", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-oi", type=int, default=1)
    parser.add_argument("--session-fraction", type=float, default=0.5)
    parser.add_argument("--premium-calibration-moneyness", type=float, default=0.03)
    parser.add_argument("--topk", default="5,8,10,14,20")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    status = RunStatus(run_dir)
    status.write("init")

    started = time.time()
    try:
        DB.connect(reuse_if_open=True)
        max_price_date = latest_db_date("price_history")
        max_option_date = latest_db_date("option_prices")
        if max_price_date is None:
            raise RuntimeError("price_history has no rows")
        if max_option_date is None:
            raise RuntimeError("option_prices has no rows")
        end = parse_date(args.end) or min(max_price_date, max_option_date, date.today())
        start = parse_date(args.start) or (end - timedelta(days=370))
        status.write("calendar", start=start, end=end, max_price_date=max_price_date, max_option_date=max_option_date)
        print(f"[calendar] window={start}..{end} version=v{args.version}", flush=True)

        trading_days = load_trading_days(start - timedelta(days=10), end)
        pairs = friday_signal_pairs(trading_days, start, end, args.strict_thursday)
        print(f"[calendar] friday weeks={len(pairs)}", flush=True)
        status.write("score_extract", completed=0, total=len(pairs))

        signals = load_score_signals(pairs, args.version, args.score_hi, args.score_lo, status)
        if not signals:
            raise RuntimeError("no score signals matched the requested window/version/thresholds")

        status.write("sigma_extract", completed=0, total=len(signals))
        sigma = load_realized_sigma(signals, start, end)
        for s in signals:
            s["sigma_60d"] = sigma.get((s["symbol"], s["signal_date"]))
        print(f"[sigma_extract] sigma rows={len(sigma):,}", flush=True)

        symbols = sorted({s["symbol"] for s in signals})
        status.write("earnings_extract", symbols=len(symbols))
        earnings = load_earnings(symbols, start - timedelta(days=14), end + timedelta(days=14))
        print(f"[earnings_extract] symbols_with_earnings={len(earnings):,}", flush=True)

        status.write("option_extract", completed=0, total=len(pairs))
        attach_option_rows(signals, pairs, args.min_oi, status)

        status.write("pricing")
        premium_cal = calibrate_premiums(signals, max_moneyness=args.premium_calibration_moneyness)
        attach_pricing(signals, premium_cal, earnings, session_fraction=args.session_fraction)
        print("[pricing] attached premium and P&L modes", flush=True)

        topks = parse_topk(args.topk)
        portfolio_rows: list[dict[str, Any]] = []
        portfolio_summary: dict[str, Any] = {}
        for mode in ("actual_thu_crush", "iv_1d_crush", "iv_open_crush"):
            rows, summary = portfolio_runs(signals, mode, topks)
            portfolio_rows.extend(rows)
            portfolio_summary[mode] = summary

        signals_path = run_dir / "signals.csv"
        portfolio_path = run_dir / "portfolio_equity.csv"
        summary_path = run_dir / "summary.json"
        write_csv(signals_path, signals)
        write_csv(portfolio_path, portfolio_rows)

        summary = build_summary(signals, pairs, premium_cal, portfolio_summary, args, start, end)
        summary["runtime_sec"] = round(time.time() - started, 2)
        summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        done_path = run_dir / "done.json"
        done_path.write_text(
            json.dumps({
                "state": "done",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "runtime_sec": summary["runtime_sec"],
                "summary_json": str(summary_path),
                "signals_csv": str(signals_path),
                "portfolio_csv": str(portfolio_path),
            }, indent=2),
            encoding="utf-8",
        )
        status.write("done", state="done", runtime_sec=summary["runtime_sec"])
        print(f"[done] summary={summary_path}", flush=True)
        return 0
    except Exception as exc:
        failed_path = run_dir / "failed.json"
        failed_path.write_text(
            json.dumps({
                "state": "failed",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "error": repr(exc),
            }, indent=2),
            encoding="utf-8",
        )
        status.write("failed", state="failed", error=repr(exc))
        print(f"[failed] {exc!r}", flush=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
