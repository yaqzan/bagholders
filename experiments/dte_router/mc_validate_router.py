"""Monte Carlo validation for deterministic 30DTE/15DTE routing candidates.

Stage 3 experiment only: this runner keeps v60 Score.overall fixed and swaps
the option DTE outcome model per entry. It does not write score rows, persist MC
rows, or touch ALGORITHM_VERSION.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import multiprocessing
import os
import random
import statistics
import sys
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import bisect


os.environ.setdefault("MC_NO_DB_PERSIST", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DTE = 30
HOLDOUT_DISABLE = True  # Stage 3 portfolio validation intentionally tests holdout/stress windows.

import monte_carlo as mc30  # noqa: E402
import monte_carlo_15dte as mc15  # noqa: E402
from database.models.core import AlgorithmVersion, EarningsDate, MarketRegime, Stock  # noqa: E402
from database.trader_database import DB  # noqa: E402
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

GATE_FOCUS = {"2025", "dip", "22-now", "5y"}
ORIG_RESOLVE_30 = mc30.resolve
_MP_STATE: dict[str, Any] = {}
HAVEN_ETF_SYMBOLS = {"GLD", "IAU", "SLV", "TLT", "IEF", "HYG", "LQD", "SHY", "UUP", "BIL", "SGOL"}


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


def resolve_v60() -> AlgorithmVersion:
    # This experiment is intentionally pinned to v60 rows. Avoid touching
    # algorithm_versions during retry loops; that table can be blocked by
    # unrelated versioning work, while the runner only needs version_id=60.
    return SimpleNamespace(id=60, production_label="v60")


def version_label(version: AlgorithmVersion) -> str:
    label = str(version.production_label)
    return label if label.startswith("v") else f"v{label}"


def _score_signal_rows(sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    if DB.is_closed():
        DB.connect(reuse_if_open=True)
    cursor = DB.execute_sql(sql, params)
    return list(cursor.fetchall())


def _score_date_ranges(d_start: date, d_end: date) -> list[tuple[date, date]]:
    chunk_days = max(1, int(os.environ.get("DTE_ROUTER_SCORE_CHUNK_DAYS", "7")))
    ranges: list[tuple[date, date]] = []
    current = d_start
    while current <= d_end:
        chunk_end = min(d_end, current + timedelta(days=chunk_days - 1))
        ranges.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return ranges


def load_call_signals_fast(version_id: int, d_start: date, d_end: date) -> list[Any]:
    """Load calls without DB-side ordering.

    The stock MC loader orders by date/score in SQL. Under contention MySQL can
    spend minutes in "Creating sort index" for broad v60 windows; fetching via
    the version/date index and sorting locally keeps retries lighter.
    """

    rows: list[tuple[Any, ...]] = []
    for chunk_start, chunk_end in _score_date_ranges(d_start, d_end):
        rows.extend(
            _score_signal_rows(
                """
                SELECT symbol, date, overall, trend, weight_info, stoch
                FROM scores FORCE INDEX (scores_version_date_IDX)
                WHERE version_id = %s
                  AND date >= %s
                  AND date <= %s
                  AND overall >= %s
                """,
                (version_id, chunk_start, chunk_end, mc30.OVERFLOW_THRESHOLD),
            )
        )
    sigs = [
        SimpleNamespace(
            symbol_id=symbol,
            symbol=symbol,
            date=dt,
            overall=int(overall),
            trend=trend,
            weight_info=weight_info,
            stoch=stoch,
        )
        for symbol, dt, overall, trend, weight_info, stoch in rows
    ]
    sigs.sort(key=lambda s: (s.date, -int(s.overall), s.symbol_id))
    return mc30._apply_ctsl_to_signals(sigs, "call")


def load_put_signals_fast(version_id: int, d_start: date, d_end: date) -> list[Any]:
    """Load puts without DB-side ordering, matching monte_carlo.load_put_signals."""

    if not mc30.DESIGN_D_PUT_FLIP:
        rows: list[tuple[Any, ...]] = []
        for chunk_start, chunk_end in _score_date_ranges(d_start, d_end):
            rows.extend(
                _score_signal_rows(
                    """
                    SELECT symbol, date, overall, trend, weight_info, volume_signal
                    FROM scores FORCE INDEX (scores_version_date_IDX)
                    WHERE version_id = %s
                      AND date >= %s
                      AND date <= %s
                      AND overall <= %s
                    """,
                    (version_id, chunk_start, chunk_end, mc30.PUT_THRESHOLD),
                )
            )
        sigs = [
            SimpleNamespace(
                symbol_id=symbol,
                symbol=symbol,
                date=dt,
                overall=int(overall),
                trend=trend,
                weight_info=weight_info,
                volume_signal=volume_signal,
            )
            for symbol, dt, overall, trend, weight_info, volume_signal in rows
        ]
        sigs.sort(key=lambda s: (s.date, int(s.overall), s.symbol_id))
        return mc30._apply_ctsl_to_signals(sigs, "put")

    rows: list[tuple[Any, ...]] = []
    for chunk_start, chunk_end in _score_date_ranges(d_start, d_end):
        rows.extend(
            _score_signal_rows(
                """
                SELECT symbol, date, overall, trend, regime_multiplier, weight_info
                FROM scores FORCE INDEX (scores_version_date_IDX)
                WHERE version_id = %s
                  AND date >= %s
                  AND date <= %s
                  AND overall <= 40
                """,
                (version_id, chunk_start, chunk_end),
            )
        )
    flipped = []
    for symbol, dt, overall, trend, regime_multiplier, weight_info in rows:
        ov = int(overall)
        pre = None
        if weight_info:
            try:
                wi = json.loads(weight_info) if isinstance(weight_info, str) else weight_info
                if "pre_regime" in wi:
                    pre = int(wi["pre_regime"])
            except Exception:
                pre = None
        mult = float(regime_multiplier) if regime_multiplier is not None else 1.0
        if pre is None:
            if mult == 1.0:
                pre = ov
            elif ov >= 50:
                pre = round(50 + (ov - 50) / mult)
            else:
                pre = round(50 + (ov - 50) / (2.0 - mult))
        pre = max(0, min(100, pre))
        if pre >= 50:
            continue
        if mult == 1.0:
            new_ov = pre
        else:
            new_ov = int(max(0, min(100, round(50 + (pre - 50) * mult))))
        if new_ov > mc30.PUT_THRESHOLD:
            continue
        flipped.append(
            SimpleNamespace(
                symbol_id=symbol,
                symbol=symbol,
                date=dt,
                overall=new_ov,
                trend=trend,
                regime_multiplier=regime_multiplier,
                weight_info=weight_info,
            )
        )
    flipped.sort(key=lambda s: (s.date, int(s.overall), s.symbol_id))
    return flipped


def select_windows(raw: str) -> list[tuple[str, date, date]]:
    if raw.lower() in {"all", "*"}:
        return list(WINDOWS)
    wanted = {part.strip() for part in raw.split(",") if part.strip()}
    out = [w for w in WINDOWS if w[0] in wanted]
    missing = wanted - {w[0] for w in out}
    if missing:
        raise ValueError(f"Unknown windows: {sorted(missing)}")
    return out


def value_on_or_before(sorted_dates: list[date], mapping: dict[date, Any], d: date, default: Any = None) -> Any:
    if not sorted_dates:
        return default
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx < 0:
        return default
    return mapping.get(sorted_dates[idx], default)


def summarize(results: list[dict[str, Any]]) -> dict[str, float]:
    finals = [float(r["final"]) for r in results]
    dds = [float(r["max_dd"]) for r in results]
    call_tps = [float(r["call_tp"]) for r in results]
    put_tps = [float(r["put_tp"]) for r in results]
    call_trades = [float(r["call_trades"]) for r in results]
    put_trades = [float(r["put_trades"]) for r in results]
    collapses = sum(1 for f in finals if f <= mc30.STARTING_CASH * mc30.COLLAPSE_THRESHOLD)

    return {
        "mean_final": statistics.mean(finals),
        "median_final": statistics.median(finals),
        "p25_final": percentile(finals, 0.25),
        "p75_final": percentile(finals, 0.75),
        "mean_ret_pct": (statistics.mean(finals) / mc30.STARTING_CASH - 1) * 100,
        "median_ret_pct": (statistics.median(finals) / mc30.STARTING_CASH - 1) * 100,
        "mean_dd_pct": statistics.mean(dds) * 100,
        "median_dd_pct": statistics.median(dds) * 100,
        "worst_dd_pct": max(dds) * 100,
        "p_coll_pct": collapses / len(results) * 100 if results else 0.0,
        "call_tp_pct": statistics.mean(call_tps),
        "put_tp_pct": statistics.mean(put_tps),
        "call_trades": statistics.mean(call_trades),
        "put_trades": statistics.mean(put_trades),
    }


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def log_ratio(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return float("-inf")
    return math.log(a / b)


def apply_signal_filters(
    call_sigs: list[Any],
    put_sigs: list[Any],
    d_start: date,
    d_end: date,
    breadth_dates: list[date],
    breadth_map: dict[date, Any],
) -> tuple[list[Any], list[Any], dict[str, int]]:
    counts: dict[str, int] = {}

    if mc30.WEAK_WEEKLY_CALL_DROP and call_sigs:
        import json as _json_c

        kept_c = []
        dropped_c = 0
        for s in call_sigs:
            ov = int(s.overall)
            if not (mc30.WEAK_WEEKLY_CALL_MIN_OV <= ov <= mc30.WEAK_WEEKLY_CALL_MAX_OV):
                kept_c.append(s)
                continue
            wadj = None
            wi_raw = getattr(s, "weight_info", None)
            if wi_raw:
                try:
                    wi = _json_c.loads(wi_raw) if isinstance(wi_raw, str) else wi_raw
                    wa = wi.get("w_adj")
                    if wa is None:
                        wa = wi.get("weekly_adj")
                    if wa is not None:
                        wadj = float(wa)
                except Exception:
                    wadj = None
            if wadj is None or wadj >= mc30.WEAK_WEEKLY_CALL_WADJ:
                kept_c.append(s)
                continue
            if mc30.WEAK_WEEKLY_CALL_STOCH_GE > 0:
                stoch_v = getattr(s, "stoch", None)
                if stoch_v is None or int(stoch_v) < mc30.WEAK_WEEKLY_CALL_STOCH_GE:
                    kept_c.append(s)
                    continue
            dropped_c += 1
        counts["weak_weekly_calls_dropped"] = dropped_c
        call_sigs = kept_c

    if mc30.EARN_SUPP_PUT:
        put_sigs, dropped = mc30._earnings_suppress_filter(
            put_sigs,
            d_start,
            d_end,
            mc30.EARN_SUPP_PUT_DAYS,
            mc30.EARN_SUPP_PUT_MIN_OV,
            mc30.EARN_SUPP_PUT_MAX_OV,
            side="put",
        )
        counts["earn_supp_puts_dropped"] = dropped

    if mc30.WEAK_WEEKLY_PUT_DROP:
        import json as _json_p

        kept_p = []
        dropped_p = 0
        for s in put_sigs:
            ov = int(s.overall)
            if not (mc30.WEAK_WEEKLY_PUT_MIN_OV <= ov <= mc30.WEAK_WEEKLY_PUT_MAX_OV):
                kept_p.append(s)
                continue
            wadj = None
            wi_raw = getattr(s, "weight_info", None)
            if wi_raw:
                try:
                    wi = _json_p.loads(wi_raw) if isinstance(wi_raw, str) else wi_raw
                    wa = wi.get("w_adj")
                    if wa is None:
                        wa = wi.get("weekly_adj")
                    if wa is not None:
                        wadj = float(wa)
                except Exception:
                    wadj = None
            if wadj is None or wadj <= mc30.WEAK_WEEKLY_PUT_WADJ:
                kept_p.append(s)
                continue
            if mc30.WEAK_WEEKLY_PUT_VSIG_REJ_ONLY:
                vsig = getattr(s, "volume_signal", None)
                if vsig != "REJECTION":
                    kept_p.append(s)
                    continue
            dropped_p += 1
        counts["weak_weekly_puts_dropped"] = dropped_p
        put_sigs = kept_p

    if (mc30.PUT_TIGHTEN_BREADTH_LE > 0 or mc30.PUT_TIGHTEN_BREADTH_GE > 0) and put_sigs and breadth_dates:
        kept = []
        dropped_n = 0
        for s in put_sigs:
            ov = int(s.overall)
            if ov <= mc30.PUT_TIGHTEN_THRESH:
                kept.append(s)
                continue
            brd = mc30.breadth_on_or_before(breadth_dates, breadth_map, s.date)
            if brd is None:
                kept.append(s)
                continue
            drop = False
            if mc30.PUT_TIGHTEN_BREADTH_LE > 0 and brd <= mc30.PUT_TIGHTEN_BREADTH_LE:
                drop = True
            if mc30.PUT_TIGHTEN_BREADTH_GE > 0 and brd >= mc30.PUT_TIGHTEN_BREADTH_GE:
                drop = True
            if drop:
                dropped_n += 1
            else:
                kept.append(s)
        counts["put_tighten_dropped"] = dropped_n
        put_sigs = kept

    if mc30.MAX_PUTS_PER_DAY > 0 and put_sigs:
        by_date = defaultdict(list)
        for s in put_sigs:
            by_date[s.date].append(s)
        kept = []
        total_dropped = 0
        capped_days = 0
        for date_sigs in by_date.values():
            date_sigs.sort(key=lambda s: s.overall)
            kept.extend(date_sigs[: mc30.MAX_PUTS_PER_DAY])
            dropped = max(0, len(date_sigs) - mc30.MAX_PUTS_PER_DAY)
            total_dropped += dropped
            if dropped > 0:
                capped_days += 1
        put_sigs = sorted(kept, key=lambda s: (s.date, s.overall))
        counts["max_puts_per_day_dropped"] = total_dropped
        counts["max_puts_per_day_capped_days"] = capped_days

    return call_sigs, put_sigs, counts


def build_earnings_map(all_syms: set[int], d_start: date, d_end: date, trading_days: list[date]) -> dict[int, list[date]]:
    if not all_syms:
        return {}
    rows = list(
        EarningsDate.select(EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
        .where(
            EarningsDate.symbol.in_(list(all_syms)),
            EarningsDate.date >= d_start - timedelta(days=10),
            EarningsDate.date <= d_end + timedelta(days=mc30.HOLD_DAYS * 2 + 14),
        )
        .order_by(EarningsDate.symbol, EarningsDate.date)
        .tuples()
    )
    out: dict[int, list[date]] = defaultdict(list)
    for sym, d, ct in rows:
        out[sym].append(compute_effective_date(d, ct, trading_days))
    for sym in list(out.keys()):
        out[sym] = sorted(set(out[sym]))
    return out


def build_j_map(all_syms: set[int]) -> dict[int, float]:
    if not all_syms:
        return {}
    try:
        from database.earnings_jump_cache import load_per_stock_jumps

        return load_per_stock_jumps(all_syms)
    except Exception as exc:
        print(f"  [warn] per-stock earnings jumps unavailable, falling back to 15DTE defaults: {exc}")
        return {}


def build_inputs(label: str, d_start: date, d_end: date, version: AlgorithmVersion) -> dict[str, Any]:
    print(f"\n{'=' * 100}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})")
    print("=" * 100)

    call_sigs = load_call_signals_fast(int(version.id), d_start, d_end)
    put_sigs = load_put_signals_fast(int(version.id), d_start, d_end)
    print(f"Loaded signals: calls={len(call_sigs):,} puts={len(put_sigs):,}")
    signal_symbols = sorted({s.symbol for s in call_sigs} | {s.symbol for s in put_sigs})
    sector_by_symbol = {
        sym: sector
        for sym, sector in Stock.select(Stock.symbol, Stock.sector)
        .where(Stock.symbol.in_(signal_symbols))
        .tuples()
    } if signal_symbols else {}

    breadth_dates, breadth_map = mc30.load_breadth_map(d_start, d_end)
    regime_rows = list(
        MarketRegime.select(
            MarketRegime.date,
            MarketRegime.market_trend_score,
            MarketRegime.vix_close,
            MarketRegime.regime_composite,
            MarketRegime.fg_composite,
        )
        .where((MarketRegime.date >= d_start - timedelta(days=80)) & (MarketRegime.date <= d_end))
        .order_by(MarketRegime.date)
        .namedtuples()
    )
    market_trend_map = {
        r.date: float(r.market_trend_score)
        for r in regime_rows
        if r.market_trend_score is not None
    }
    vix_map = {
        r.date: float(r.vix_close)
        for r in regime_rows
        if r.vix_close is not None
    }
    regime_composite_map = {
        r.date: float(r.regime_composite)
        for r in regime_rows
        if r.regime_composite is not None
    }
    fg_composite_map = {
        r.date: float(r.fg_composite)
        for r in regime_rows
        if r.fg_composite is not None
    }
    regime_state_dates = sorted({*market_trend_map.keys(), *vix_map.keys(), *regime_composite_map.keys(), *fg_composite_map.keys()})
    call_sigs, put_sigs, filter_counts = apply_signal_filters(
        call_sigs,
        put_sigs,
        d_start,
        d_end,
        breadth_dates,
        breadth_map,
    )
    print(f"After filters: calls={len(call_sigs):,} puts={len(put_sigs):,} filters={filter_counts}")

    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph = mc30.load_price_history(sym_ids, d_start, d_end)

    regime_dates, regime_map = mc30.load_regime_map(d_start, d_end)
    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    close_by_sym_idx = None
    if mc30.REALLOC_STRATEGY:
        close_by_sym_idx = {}
        td_set = set(trading_days)
        td_idx = {d: i for i, d in enumerate(trading_days)}
        for sid, bars in ph.items():
            for b in bars:
                d = b[0]
                if d in td_set:
                    close_by_sym_idx[(sid, td_idx[d])] = b[1]

    ern_call_lookup: set[tuple[int, date]] = set()
    if mc30.EARN_BOOST_CALL:
        cal_syms = {s.symbol_id for s in call_sigs}
        rows = list(
            EarningsDate.select(EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
            .where(
                EarningsDate.symbol.in_(list(cal_syms)),
                EarningsDate.date >= d_start - timedelta(days=10),
                EarningsDate.date <= d_end + timedelta(days=mc30.EARN_BOOST_CALL_DAYS * 2 + 7),
            )
            .order_by(EarningsDate.symbol, EarningsDate.date)
            .tuples()
        )
        ern_by_sym = defaultdict(list)
        for sym, d, ct in rows:
            ern_by_sym[sym].append(compute_effective_date(d, ct))

        def _fwd_n(d: date, n: int) -> date:
            out = d
            while n > 0:
                out += timedelta(days=1)
                if mc30._is_trading_day(out):
                    n -= 1
            return out

        for sig in call_sigs:
            ov = int(sig.overall)
            if not (mc30.EARN_BOOST_CALL_MIN_OV <= ov <= mc30.EARN_BOOST_CALL_MAX_OV):
                continue
            win_end = _fwd_n(sig.date, mc30.EARN_BOOST_CALL_DAYS)
            if any(sig.date < ed <= win_end for ed in ern_by_sym.get(sig.symbol_id, [])):
                ern_call_lookup.add((sig.symbol_id, sig.date))

    calls_by_date = defaultdict(list)
    for sig in call_sigs:
        key = (sig.symbol_id, sig.date)
        ct = mc30.ct_tag(sig.overall, sig.trend, "call")
        ern = (sig.symbol_id, sig.date) in ern_call_lookup
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct, ern))

    puts_by_date = defaultdict(list)
    for sig in put_sigs:
        key = (sig.symbol_id, sig.date)
        ct = mc30.ct_tag(sig.overall, sig.trend, "put")
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct))

    call_pressure_by_date = {d: len(v) for d, v in calls_by_date.items()}
    put_pressure_by_date = {d: len(v) for d, v in puts_by_date.items()}

    all_syms = {s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs}
    ern_map = build_earnings_map(all_syms, d_start, d_end, trading_days)
    j_map = build_j_map(all_syms)

    count_map_call = {}
    if mc30.STRESS_MODE == "count":
        for sig in call_sigs:
            if sig.overall >= 75:
                count_map_call[sig.date] = count_map_call.get(sig.date, 0) + 1

    print("Precomputing 30DTE outcomes...", end=" ", flush=True)
    call_outcomes_30 = mc30.precompute_outcomes(
        call_sigs,
        ph,
        breadth_dates,
        breadth_map,
        ern_map=ern_map,
        trading_days=trading_days,
        count_map=count_map_call if mc30.STRESS_MODE == "count" else None,
    )
    put_outcomes = mc30.precompute_put_outcomes(
        put_sigs,
        ph,
        breadth_dates,
        breadth_map,
        ern_map=ern_map,
        trading_days=trading_days,
    )
    print(f"calls={len(call_outcomes_30):,} puts={len(put_outcomes):,}")

    print("Precomputing 15DTE call outcomes...", end=" ", flush=True)
    call_outcomes_15 = mc15.precompute_outcomes(
        call_sigs,
        ph,
        breadth_dates,
        breadth_map,
        ern_map=ern_map,
        j_map=j_map,
        trading_days=trading_days,
    )
    put_outcomes_15 = mc15.precompute_put_outcomes(
        put_sigs,
        ph,
        breadth_dates,
        breadth_map,
        ern_map=ern_map,
        j_map=j_map,
        trading_days=trading_days,
    )
    print(f"calls={len(call_outcomes_15):,} puts={len(put_outcomes_15):,}")

    return {
        "label": label,
        "d_start": d_start,
        "d_end": d_end,
        "call_sigs": call_sigs,
        "put_sigs": put_sigs,
        "calls_by_date": calls_by_date,
        "puts_by_date": puts_by_date,
        "call_outcomes_30": call_outcomes_30,
        "call_outcomes_15": call_outcomes_15,
        "put_outcomes": put_outcomes,
        "put_outcomes_15": put_outcomes_15,
        "trading_days": trading_days,
        "regime_dates": regime_dates,
        "regime_map": regime_map,
        "close_by_sym_idx": close_by_sym_idx,
        "filter_counts": filter_counts,
        "breadth_dates": breadth_dates,
        "breadth_map": breadth_map,
        "call_pressure_by_date": call_pressure_by_date,
        "put_pressure_by_date": put_pressure_by_date,
        "regime_state_dates": regime_state_dates,
        "market_trend_map": market_trend_map,
        "vix_map": vix_map,
        "regime_composite_map": regime_composite_map,
        "fg_composite_map": fg_composite_map,
        "sector_by_symbol": sector_by_symbol,
    }


def build_candidate_outcomes(
    inputs: dict[str, Any],
    candidate: str,
) -> tuple[
    dict[tuple[int, date], dict[str, Any]],
    dict[tuple[int, date], dict[str, Any]],
    dict[date, list[tuple]],
    dict[date, list[tuple]],
    int,
]:
    call_outcomes = {
        key: copy.copy(value)
        for key, value in inputs["call_outcomes_30"].items()
    }
    put_outcomes = {
        key: copy.copy(value)
        for key, value in inputs["put_outcomes"].items()
    }
    routed = 0
    routed_call_keys: set[tuple[int, date]] = set()
    routed_put_keys: set[tuple[int, date]] = set()
    sig_by_key = {(s.symbol_id, s.date): s for s in inputs["call_sigs"]}

    routed_call_by_date: dict[date, int] = defaultdict(int)
    call_items = sorted(sig_by_key.items(), key=lambda item: (item[0][1], -int(item[1].overall)))
    for key, sig in call_items:
        use_15 = False
        ov = int(sig.overall)
        brd = mc30.breadth_on_or_before(inputs["breadth_dates"], inputs["breadth_map"], sig.date)
        call_pressure = inputs["call_pressure_by_date"].get(sig.date, 0)
        put_pressure = inputs["put_pressure_by_date"].get(sig.date, 0)
        trend_score = value_on_or_before(
            inputs["regime_state_dates"],
            inputs["market_trend_map"],
            sig.date,
        )
        vix = value_on_or_before(
            inputs["regime_state_dates"],
            inputs["vix_map"],
            sig.date,
        )
        regime_comp = value_on_or_before(
            inputs["regime_state_dates"],
            inputs["regime_composite_map"],
            sig.date,
        )
        if candidate in {
            "call_ge_80",
            "call_ge_80_midalloc",
            "call_ge_80_lowalloc",
            "call_ge_80_daycap1",
            "call_ge_80_pressure_le_16",
            "call_ge_80_pressure_le_8",
            "call_ge_80_daycap1_breadth_le_55",
            "call_ge_80_daycap1_breadth_le_65",
            "call_ge_80_daycap1_breadth_36_65",
            "call_ge_80_daycap1_puts_ge_4",
            "call_ge_80_daycap1_puts_ge_8",
            "call_ge_80_daycap1_puts_ge_12",
            "call_ge_80_daycap1_trend_lt_65",
            "call_ge_80_daycap1_trend_lt_50",
            "call_ge_80_daycap1_trend_lt_40",
            "call_ge_80_daycap1_trend_lt_35",
            "call_ge_80_daycap1_trend_35_65",
            "call_ge_80_daycap1_trend_20_30",
            "call_ge_80_daycap1_trend_20_30_vix_ge_25_no_haven_etf_regime_50_85",
            "call_ge_80_daycap1_trend_lt_35_vix_25_35",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_no_haven_etf",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_no_haven_etf_regime_50_85",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_no_haven_etf_regime_50_85_midalloc",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_stock_only_regime_50_85",
            "call_ge_80_daycap1_trend_lt_35_vix_lt_35",
        }:
            use_15 = ov >= 80
        elif candidate in {
            "call_ge_85",
            "call_ge_85_midalloc",
            "call_ge_85_lowalloc",
            "call_ge_85_daycap1",
            "call_ge_85_pressure_le_16",
        }:
            use_15 = ov >= 85
        elif candidate == "call_80_84_breadth_le_35":
            use_15 = 80 <= ov <= 84 and brd is not None and brd <= 35
        elif candidate == "call_80_84_breadth_36_55":
            use_15 = 80 <= ov <= 84 and brd is not None and 36 <= brd <= 55
        elif candidate == "call_80_84_breadth_46_55":
            use_15 = 80 <= ov <= 84 and brd is not None and 46 <= brd <= 55
        elif candidate == "call_80_84_breadth_le_55":
            use_15 = 80 <= ov <= 84 and brd is not None and brd <= 55
        elif candidate.startswith("put_"):
            use_15 = False
        else:
            raise ValueError(f"Unknown candidate: {candidate}")
        if use_15 and "_breadth_le_55" in candidate and (brd is None or brd > 55):
            use_15 = False
        if use_15 and "_breadth_le_65" in candidate and (brd is None or brd > 65):
            use_15 = False
        if use_15 and "_breadth_36_65" in candidate and (brd is None or brd < 36 or brd > 65):
            use_15 = False
        if use_15 and "_puts_ge_4" in candidate and put_pressure < 4:
            use_15 = False
        if use_15 and "_puts_ge_8" in candidate and put_pressure < 8:
            use_15 = False
        if use_15 and "_puts_ge_12" in candidate and put_pressure < 12:
            use_15 = False
        if use_15 and "_trend_lt_65" in candidate and (trend_score is None or trend_score >= 65):
            use_15 = False
        if use_15 and "_trend_lt_50" in candidate and (trend_score is None or trend_score >= 50):
            use_15 = False
        if use_15 and "_trend_lt_40" in candidate and (trend_score is None or trend_score >= 40):
            use_15 = False
        if use_15 and "_trend_lt_35" in candidate and (trend_score is None or trend_score >= 35):
            use_15 = False
        if use_15 and "_trend_35_65" in candidate and (trend_score is None or trend_score < 35 or trend_score >= 65):
            use_15 = False
        if use_15 and "_trend_20_30" in candidate and (trend_score is None or trend_score < 20 or trend_score >= 30):
            use_15 = False
        if use_15 and "_vix_25_35" in candidate and (vix is None or vix < 25 or vix >= 35):
            use_15 = False
        if use_15 and "_vix_ge_25" in candidate and (vix is None or vix < 25):
            use_15 = False
        if use_15 and "_vix_lt_35" in candidate and (vix is None or vix >= 35):
            use_15 = False
        if use_15 and "_no_haven_etf" in candidate and sig.symbol in HAVEN_ETF_SYMBOLS:
            use_15 = False
        if use_15 and "_stock_only" in candidate and not inputs["sector_by_symbol"].get(sig.symbol):
            use_15 = False
        if use_15 and "_regime_50_85" in candidate and (
            regime_comp is None or regime_comp < 50 or regime_comp > 85
        ):
            use_15 = False
        if use_15 and "_daycap1" in candidate and routed_call_by_date[sig.date] >= 1:
            use_15 = False
        if use_15 and "_pressure_le_16" in candidate and call_pressure > 16:
            use_15 = False
        if use_15 and "_pressure_le_8" in candidate and call_pressure > 8:
            use_15 = False
        if not use_15:
            continue
        outcome_15 = inputs["call_outcomes_15"].get(key)
        if outcome_15 is None:
            continue
        tagged = copy.copy(outcome_15)
        tagged["_dte"] = "15"
        call_outcomes[key] = tagged
        routed_call_keys.add(key)
        routed_call_by_date[sig.date] += 1
        routed += 1

    put_sig_by_key = {(s.symbol_id, s.date): s for s in inputs["put_sigs"]}
    for key, sig in put_sig_by_key.items():
        use_15 = False
        ov = int(sig.overall)
        brd = mc30.breadth_on_or_before(inputs["breadth_dates"], inputs["breadth_map"], sig.date)
        if candidate == "put_le_15":
            use_15 = ov <= 15
        elif candidate == "put_6_10":
            use_15 = 6 <= ov <= 10
        elif candidate == "put_11_15_breadth_66_75":
            use_15 = 11 <= ov <= 15 and brd is not None and 66 <= brd <= 75
        elif candidate.startswith("call_"):
            use_15 = False
        else:
            raise ValueError(f"Unknown candidate: {candidate}")
        if not use_15:
            continue
        outcome_15 = inputs["put_outcomes_15"].get(key)
        if outcome_15 is None:
            continue
        tagged = copy.copy(outcome_15)
        tagged["_dte"] = "15"
        put_outcomes[key] = tagged
        routed_put_keys.add(key)
        routed += 1

    calls_by_date = defaultdict(list)
    for d, entries in inputs["calls_by_date"].items():
        for sid, score, key, ct, ern in entries:
            routed_entry = key in routed_call_keys
            alloc_score = score
            if routed_entry and candidate.endswith("_midalloc"):
                alloc_score = min(score, 80)
            elif routed_entry and candidate.endswith("_lowalloc"):
                alloc_score = min(score, 75)
            calls_by_date[d].append((sid, alloc_score, key, ct, ern))

    puts_by_date = defaultdict(list)
    for d, entries in inputs["puts_by_date"].items():
        for sid, score, key, ct in entries:
            puts_by_date[d].append((sid, score, key, ct))

    return call_outcomes, put_outcomes, calls_by_date, puts_by_date, routed


def router_resolve(outcome: dict[str, Any], rng: random.Random) -> tuple[str, float]:
    if outcome.get("_dte") == "15":
        return mc15.resolve(outcome, rng)
    return ORIG_RESOLVE_30(outcome, rng)


def _mc_init_worker(
    trading_days: list[date],
    calls_by_date: dict[date, list[tuple]],
    call_outcomes: dict[tuple[int, date], dict[str, Any]],
    puts_by_date: dict[date, list[tuple]],
    put_outcomes: dict[tuple[int, date], dict[str, Any]],
    regime_dates: list[date],
    regime_map: dict[date, Any],
    close_by_sym_idx: dict[tuple[int, int], float] | None,
) -> None:
    mc30.resolve = router_resolve
    _MP_STATE["trading_days"] = trading_days
    _MP_STATE["calls_by_date"] = calls_by_date
    _MP_STATE["call_outcomes"] = call_outcomes
    _MP_STATE["puts_by_date"] = puts_by_date
    _MP_STATE["put_outcomes"] = put_outcomes
    _MP_STATE["regime_dates"] = regime_dates
    _MP_STATE["regime_map"] = regime_map
    _MP_STATE["close_by_sym_idx"] = close_by_sym_idx


def _mc_iter_worker(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    return mc30.run_single_sim(
        _MP_STATE["trading_days"],
        _MP_STATE["calls_by_date"],
        _MP_STATE["call_outcomes"],
        _MP_STATE["puts_by_date"],
        _MP_STATE["put_outcomes"],
        rng,
        _MP_STATE["regime_dates"],
        _MP_STATE["regime_map"],
        collect_tape=False,
        close_by_sym_idx=_MP_STATE["close_by_sym_idx"],
    )


def run_many(
    inputs: dict[str, Any],
    call_outcomes: dict[tuple[int, date], dict[str, Any]],
    put_outcomes: dict[tuple[int, date], dict[str, Any]],
    calls_by_date: dict[date, list[tuple]],
    puts_by_date: dict[date, list[tuple]],
    seeds: list[int],
    *,
    use_mp: bool,
    workers: int,
) -> list[dict[str, Any]]:
    if use_mp and len(seeds) >= 16:
        n_workers = min(max(1, workers), len(seeds))
        with multiprocessing.Pool(
            processes=n_workers,
            initializer=_mc_init_worker,
            initargs=(
                inputs["trading_days"],
                calls_by_date,
                call_outcomes,
                puts_by_date,
                put_outcomes,
                inputs["regime_dates"],
                inputs["regime_map"],
                inputs["close_by_sym_idx"],
            ),
        ) as pool:
            return pool.map(_mc_iter_worker, seeds, chunksize=max(1, len(seeds) // (n_workers * 4)))

    mc30.resolve = router_resolve
    try:
        results = []
        for seed in seeds:
            rng = random.Random(seed)
            results.append(
                mc30.run_single_sim(
                    inputs["trading_days"],
                    calls_by_date,
                    call_outcomes,
                    puts_by_date,
                    put_outcomes,
                    rng,
                    inputs["regime_dates"],
                    inputs["regime_map"],
                    collect_tape=False,
                    close_by_sym_idx=inputs["close_by_sym_idx"],
                )
            )
        return results
    finally:
        mc30.resolve = ORIG_RESOLVE_30


def gate_row(row: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if row["candidate_mean_final"] < row["baseline_mean_final"]:
        reasons.append("mean_final_regression")
    if row["candidate_median_final"] < row["baseline_median_final"]:
        reasons.append("median_final_regression")
    if row["candidate_p_coll_pct"] > row["baseline_p_coll_pct"] + 0.1:
        reasons.append("collapse_regression")
    dd_delta = row["candidate_worst_dd_pct"] - row["baseline_worst_dd_pct"]
    dd_limit = 1.0 if row["window"] in {"5y", "22-now"} else 5.0
    if dd_delta > dd_limit:
        reasons.append(f"dd_regression_gt_{dd_limit:g}pp")
    return not reasons, reasons


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_findings(path: Path, rows: list[dict[str, Any]], candidate: str, n_iter: int) -> None:
    focus = [r for r in rows if r["window"] in GATE_FOCUS]
    passed = all(r["gate_pass"] for r in rows)
    focus_passed = all(r["gate_pass"] for r in focus)
    weighted_log = sum(r["mean_log_delta"] for r in focus)
    worst_dd_reg = max(r["worst_dd_delta_pp"] for r in rows)
    avg_routed = statistics.mean([r["routed15_trade_pct"] for r in focus]) if focus else 0.0

    lines = [
        "# DTE Router MC Validation",
        "",
        f"- candidate: `{candidate}`",
        f"- iterations per window: {n_iter}",
        f"- all-window gate pass: {passed}",
        f"- focus-window gate pass: {focus_passed}",
        f"- focus mean-log delta sum: {weighted_log:+.4f}",
        f"- worst DD regression: {worst_dd_reg:+.2f}pp",
        f"- average focus routed-to-15DTE signal share: {avg_routed:.1f}%",
        "",
        "## Window Summary",
        "",
        "| Window | Mean Log Delta | Median Log Delta | Worst DD Delta | Routed 15DTE | Gate |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        gate = "PASS" if row["gate_pass"] else "FAIL: " + ";".join(row["gate_reasons"])
        lines.append(
            f"| {row['window']} | {row['mean_log_delta']:+.4f} | "
            f"{row['median_log_delta']:+.4f} | {row['worst_dd_delta_pp']:+.2f}pp | "
            f"{row['routed15_trade_pct']:.1f}% | {gate} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    status = Status(run_dir)
    rows: list[dict[str, Any]] = []

    summary_path = run_dir / "mc_window_summary.csv"
    findings_path = run_dir / "findings.md"
    status.output("summary", summary_path)
    status.output("findings", findings_path)

    try:
        version = resolve_v60()
        windows = select_windows(args.windows)
        status.update(
            phase="window-validation",
            total=len(windows),
            version=version_label(version),
            version_id=int(version.id),
            candidate=args.candidate,
            n_iter=args.n_iter,
        )

        print("DTE Router MC Validation")
        print(f"candidate={args.candidate} n_iter={args.n_iter} version={version_label(version)} db:{version.id}")
        print(f"windows={','.join(w[0] for w in windows)}")
        print(f"MC_NO_DB_PERSIST={os.environ.get('MC_NO_DB_PERSIST')}")
        use_mp = not args.no_mp
        workers = args.workers or os.cpu_count() or 4
        print(f"multiprocessing={'on' if use_mp else 'off'} workers={workers}")

        for idx, (label, d_start, d_end) in enumerate(windows, start=1):
            status.update(phase=f"window:{label}", completed=idx - 1)
            inputs = build_inputs(label, d_start, d_end, version)
            (
                candidate_call_outcomes,
                candidate_put_outcomes,
                candidate_calls_by_date,
                candidate_puts_by_date,
                routed,
            ) = build_candidate_outcomes(inputs, args.candidate)
            seeds = [1000 * mc30._stable_label_seed(f"{label}:{args.seed_label}") + i for i in range(args.n_iter)]
            print(f"Routed call outcomes to 15DTE: {routed:,}/{len(inputs['call_sigs']):,}")
            print(f"Running baseline/candidate paired seeds: {len(seeds):,}")

            baseline_rs = run_many(
                inputs,
                inputs["call_outcomes_30"],
                inputs["put_outcomes"],
                inputs["calls_by_date"],
                inputs["puts_by_date"],
                seeds,
                use_mp=use_mp,
                workers=workers,
            )
            candidate_rs = run_many(
                inputs,
                candidate_call_outcomes,
                candidate_put_outcomes,
                candidate_calls_by_date,
                candidate_puts_by_date,
                seeds,
                use_mp=use_mp,
                workers=workers,
            )
            base = summarize(baseline_rs)
            cand = summarize(candidate_rs)

            row = {
                "candidate": args.candidate,
                "window": label,
                "start": d_start.isoformat(),
                "end": d_end.isoformat(),
                "n_iter": args.n_iter,
                "signals_call": len(inputs["call_sigs"]),
                "signals_put": len(inputs["put_sigs"]),
                "routed15_signals": routed,
                "routed15_trade_pct": routed / max(1, len(inputs["call_sigs"]) + len(inputs["put_sigs"])) * 100,
                "baseline_mean_final": base["mean_final"],
                "candidate_mean_final": cand["mean_final"],
                "mean_log_delta": log_ratio(cand["mean_final"], base["mean_final"]),
                "baseline_median_final": base["median_final"],
                "candidate_median_final": cand["median_final"],
                "median_log_delta": log_ratio(cand["median_final"], base["median_final"]),
                "baseline_worst_dd_pct": base["worst_dd_pct"],
                "candidate_worst_dd_pct": cand["worst_dd_pct"],
                "worst_dd_delta_pp": cand["worst_dd_pct"] - base["worst_dd_pct"],
                "baseline_mean_dd_pct": base["mean_dd_pct"],
                "candidate_mean_dd_pct": cand["mean_dd_pct"],
                "mean_dd_delta_pp": cand["mean_dd_pct"] - base["mean_dd_pct"],
                "baseline_p_coll_pct": base["p_coll_pct"],
                "candidate_p_coll_pct": cand["p_coll_pct"],
                "baseline_call_tp_pct": base["call_tp_pct"],
                "candidate_call_tp_pct": cand["call_tp_pct"],
                "baseline_put_tp_pct": base["put_tp_pct"],
                "candidate_put_tp_pct": cand["put_tp_pct"],
                "baseline_call_trades": base["call_trades"],
                "candidate_call_trades": cand["call_trades"],
                "baseline_put_trades": base["put_trades"],
                "candidate_put_trades": cand["put_trades"],
            }
            gate_pass, gate_reasons = gate_row(row)
            row["gate_pass"] = gate_pass
            row["gate_reasons"] = gate_reasons
            rows.append(row)
            write_csv(summary_path, rows)
            write_findings(findings_path, rows, args.candidate, args.n_iter)

            best = max(rows, key=lambda r: sum(x["mean_log_delta"] for x in rows if x["window"] in GATE_FOCUS))
            status.update(
                completed=idx,
                best_so_far={
                    "candidate": best["candidate"],
                    "latest_window": label,
                    "latest_mean_log_delta": row["mean_log_delta"],
                    "latest_worst_dd_delta_pp": row["worst_dd_delta_pp"],
                    "latest_gate_pass": row["gate_pass"],
                },
            )
            print(
                f"{label}: mean_log_delta={row['mean_log_delta']:+.4f} "
                f"median_log_delta={row['median_log_delta']:+.4f} "
                f"worst_dd_delta={row['worst_dd_delta_pp']:+.2f}pp "
                f"gate={'PASS' if row['gate_pass'] else 'FAIL'}"
            )

        terminal = {
            "state": "completed",
            "finished_at": utc_now(),
            "candidate": args.candidate,
            "version": version_label(version),
            "version_id": int(version.id),
            "n_iter": args.n_iter,
            "windows": [r["window"] for r in rows],
            "all_window_gate_pass": all(r["gate_pass"] for r in rows),
            "focus_window_gate_pass": all(r["gate_pass"] for r in rows if r["window"] in GATE_FOCUS),
            "focus_mean_log_delta_sum": sum(r["mean_log_delta"] for r in rows if r["window"] in GATE_FOCUS),
            "worst_dd_regression_pp": max(r["worst_dd_delta_pp"] for r in rows),
            "summary_path": str(summary_path),
            "findings_path": str(findings_path),
        }
        atomic_json(run_dir / "done.json", terminal)
        status.update(phase="done", state="completed", completed=len(windows), best_so_far=terminal)
    except Exception as exc:
        payload = {
            "state": "failed",
            "finished_at": utc_now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        atomic_json(run_dir / "failed.json", payload)
        status.update(phase="failed", state="failed", best_so_far=payload)
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument(
        "--candidate",
        default="call_ge_80",
        choices=[
            "call_ge_80",
            "call_ge_85",
            "call_ge_80_midalloc",
            "call_ge_80_lowalloc",
            "call_ge_85_midalloc",
            "call_ge_85_lowalloc",
            "call_ge_80_daycap1",
            "call_ge_85_daycap1",
            "call_ge_80_pressure_le_16",
            "call_ge_80_pressure_le_8",
            "call_ge_85_pressure_le_16",
            "call_ge_80_daycap1_breadth_le_55",
            "call_ge_80_daycap1_breadth_le_65",
            "call_ge_80_daycap1_breadth_36_65",
            "call_ge_80_daycap1_puts_ge_4",
            "call_ge_80_daycap1_puts_ge_8",
            "call_ge_80_daycap1_puts_ge_12",
            "call_ge_80_daycap1_trend_lt_65",
            "call_ge_80_daycap1_trend_lt_50",
            "call_ge_80_daycap1_trend_lt_40",
            "call_ge_80_daycap1_trend_lt_35",
            "call_ge_80_daycap1_trend_35_65",
            "call_ge_80_daycap1_trend_20_30",
            "call_ge_80_daycap1_trend_20_30_vix_ge_25_no_haven_etf_regime_50_85",
            "call_ge_80_daycap1_trend_lt_35_vix_25_35",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_no_haven_etf",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_no_haven_etf_regime_50_85",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_no_haven_etf_regime_50_85_midalloc",
            "call_ge_80_daycap1_trend_lt_35_vix_ge_25_stock_only_regime_50_85",
            "call_ge_80_daycap1_trend_lt_35_vix_lt_35",
            "call_80_84_breadth_le_35",
            "call_80_84_breadth_36_55",
            "call_80_84_breadth_46_55",
            "call_80_84_breadth_le_55",
            "put_le_15",
            "put_6_10",
            "put_11_15_breadth_66_75",
        ],
    )
    parser.add_argument("--windows", default="all")
    parser.add_argument("--n-iter", type=int, default=160)
    parser.add_argument("--seed-label", default="dte-router-mc")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--no-mp", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
