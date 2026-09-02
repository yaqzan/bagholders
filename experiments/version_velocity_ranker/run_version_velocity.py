"""Rank score versions by risk-bounded capital velocity.

This is a read-only research runner. It compares persisted AlgorithmVersion
score rows through the current cascade portfolio engine and portfolio-profile
overrides. It does not write score rows, assessment rows, or version metadata.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion  # noqa: E402
from database.trader_database import DB  # noqa: E402
import backtest_cascade  # noqa: E402
import portfolio_profiles  # noqa: E402


INITIAL_CAPITAL = 50_000.0
TARGET_CAPITAL = 1_000_000.0
DD_BARRIER = 0.55
COLLAPSE_FRACTION = 0.20

ANNUAL_WINDOWS = [
    ("2021", date(2021, 1, 1), date(2021, 12, 31)),
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023", date(2023, 1, 1), date(2023, 12, 31)),
    ("2024", date(2024, 1, 1), date(2024, 12, 31)),
    ("2025", date(2025, 1, 1), date(2025, 12, 31)),
]

PORTFOLIO_WINDOWS = [
    ("22-now", date(2022, 1, 1), date(2026, 4, 24)),
    ("5y", date(2021, 1, 1), date(2026, 4, 15)),
]

EXTRA_WINDOWS = [
    ("dip", date(2025, 11, 1), date(2026, 4, 24)),
]


@dataclass
class WindowMetrics:
    version_id: int
    version_label: str
    git_commit: str
    git_message: str
    profile: str
    profile_name: str
    window: str
    start_date: str
    end_date: str
    n_trades: int = 0
    n_call: int = 0
    n_put: int = 0
    final_equity: float | None = None
    return_pct: float | None = None
    max_dd_pct: float | None = None
    ulcer_index_pct: float | None = None
    max_days_underwater: int | None = None
    max_5d_drop_pct: float | None = None
    max_10d_drop_pct: float | None = None
    target_hit: bool = False
    target_hit_before_dd55: bool = False
    days_to_target: int | None = None
    trading_days_to_target: int | None = None
    first_dd55_date: str | None = None
    max_dd_before_target_pct: float | None = None
    collapse: bool = False
    error: str | None = None


@dataclass
class RankingRow:
    rank: int
    version_id: int
    version_label: str
    git_commit: str
    git_message: str
    profile: str
    profile_name: str
    velocity_score: float
    annual_safe_hit_rate_pct: float
    annual_hit_rate_pct: float
    annual_median_days_to_target: float | None
    annual_avg_censored_days_to_target: float
    worst_annual_dd_pct: float | None
    avg_annual_ulcer_pct: float | None
    worst_year_return_pct: float | None
    collapse_rate_pct: float
    five_year_final_equity: float | None
    five_year_return_pct: float | None
    five_year_max_dd_pct: float | None
    five_year_target_hit_before_dd55: bool | None
    twenty_two_now_return_pct: float | None
    twenty_two_now_max_dd_pct: float | None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _pct(value: float | None) -> float | None:
    return None if value is None else value * 100.0


def _format_money(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value) >= 1_000_000_000:
        return f"${value/1_000_000_000:,.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value/1_000_000:,.2f}M"
    return f"${value:,.0f}"


def _max_rolling_drop_pct(values: list[float], bars: int) -> float | None:
    if len(values) <= bars:
        return None
    worst = 0.0
    for i in range(bars, len(values)):
        prev = values[i - bars]
        cur = values[i]
        if prev > 0:
            worst = max(worst, (prev - cur) / prev)
    return worst * 100.0


def _path_metrics(equity_curve: list, start: date, target: float, dd_barrier: float) -> dict:
    if not equity_curve:
        return {
            "target_hit": False,
            "target_hit_before_dd55": False,
            "days_to_target": None,
            "trading_days_to_target": None,
            "first_dd55_date": None,
            "max_dd_before_target_pct": None,
            "ulcer_index_pct": None,
            "max_days_underwater": None,
            "max_5d_drop_pct": None,
            "max_10d_drop_pct": None,
        }

    peak = INITIAL_CAPITAL
    dds: list[float] = []
    values: list[float] = []
    first_target_idx = None
    first_target_date = None
    first_dd_idx = None
    first_dd_date = None
    max_dd_before_target = 0.0
    underwater_start = None
    max_days_underwater = 0

    for idx, row in enumerate(equity_curve):
        d, equity = row[0], float(row[1])
        values.append(equity)
        if equity >= peak:
            if underwater_start is not None:
                max_days_underwater = max(max_days_underwater, (d - underwater_start).days)
                underwater_start = None
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        dds.append(dd)
        if dd > 0 and underwater_start is None:
            underwater_start = d
        if first_dd_idx is None and dd >= dd_barrier:
            first_dd_idx = idx
            first_dd_date = d
        if first_target_idx is None:
            max_dd_before_target = max(max_dd_before_target, dd)
            if equity >= target:
                first_target_idx = idx
                first_target_date = d

    if underwater_start is not None:
        max_days_underwater = max(max_days_underwater, (equity_curve[-1][0] - underwater_start).days)

    target_before_dd = (
        first_target_idx is not None
        and (first_dd_idx is None or first_target_idx <= first_dd_idx)
    )
    ulcer = math.sqrt(statistics.mean([dd * dd for dd in dds])) * 100.0 if dds else None

    return {
        "target_hit": first_target_idx is not None,
        "target_hit_before_dd55": target_before_dd,
        "days_to_target": (first_target_date - start).days if first_target_date else None,
        "trading_days_to_target": first_target_idx + 1 if first_target_idx is not None else None,
        "first_dd55_date": first_dd_date.isoformat() if first_dd_date else None,
        "max_dd_before_target_pct": max_dd_before_target * 100.0 if first_target_idx is not None else None,
        "ulcer_index_pct": ulcer,
        "max_days_underwater": max_days_underwater,
        "max_5d_drop_pct": _max_rolling_drop_pct(values, 5),
        "max_10d_drop_pct": _max_rolling_drop_pct(values, 10),
    }


def _version_token(version: AlgorithmVersion) -> dict:
    version_id = int(version.id)
    commit = version.git_commit or ""
    label = f"db:{version_id}" if commit.startswith("shadow/") else f"v{version_id}"
    return {
        "version_id": version_id,
        "version_label": label,
        "git_commit": commit,
        "git_message": version.git_message or "",
    }


def _version_label(version: AlgorithmVersion) -> str:
    return _version_token(version)["version_label"]


def _ensure_db_connection() -> None:
    try:
        if DB.is_closed():
            DB.connect(reuse_if_open=True)
        else:
            DB.connection().ping(reconnect=True)
    except Exception:
        try:
            if not DB.is_closed():
                DB.close()
        except Exception:
            pass
        DB.connect(reuse_if_open=True)


def _fresh_db_connection() -> None:
    try:
        if not DB.is_closed():
            DB.close()
    except Exception:
        pass
    DB.connect(reuse_if_open=True)


def _resolve_versions(raw: str, min_version: int | None, max_version: int | None) -> list[AlgorithmVersion]:
    if raw.strip().lower() == "all":
        query = (
            AlgorithmVersion.select()
            .where(~(AlgorithmVersion.git_commit.startswith("shadow/")))
            .order_by(AlgorithmVersion.id)
        )
        versions = list(query)
    else:
        versions = []
        for token in raw.replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            if token.lower().startswith("v"):
                token = token[1:]
            version = AlgorithmVersion.get_or_none(AlgorithmVersion.id == int(token))
            if version is None:
                raise SystemExit(f"Unknown version token: {token}")
            versions.append(version)
    if min_version is not None:
        versions = [v for v in versions if int(v.id) >= min_version]
    if max_version is not None:
        versions = [v for v in versions if int(v.id) <= max_version]
    return versions


def _resolve_profiles(raw: str) -> list[dict]:
    if raw.strip().lower() == "all":
        keys = list(portfolio_profiles.PROFILE_ORDER)
    else:
        keys = [
            portfolio_profiles.normalize_profile_key(tok.strip(), ROOT)
            for tok in raw.replace(";", ",").split(",")
            if tok.strip()
        ]
    return [portfolio_profiles.get_profile(key, ROOT) for key in keys]


def _resolve_windows(raw: str) -> list[tuple[str, date, date]]:
    raw = raw.strip().lower()
    if raw == "core":
        return ANNUAL_WINDOWS + PORTFOLIO_WINDOWS
    if raw == "all":
        return ANNUAL_WINDOWS + EXTRA_WINDOWS + PORTFOLIO_WINDOWS
    if raw == "annual":
        return ANNUAL_WINDOWS
    if raw == "portfolio":
        return PORTFOLIO_WINDOWS
    by_label = {label: (label, start, end) for label, start, end in ANNUAL_WINDOWS + EXTRA_WINDOWS + PORTFOLIO_WINDOWS}
    out = []
    for token in raw.replace(";", ",").split(","):
        token = token.strip()
        if token not in by_label:
            valid = ", ".join(sorted(by_label))
            raise SystemExit(f"Unknown window {token!r}; valid labels: {valid}")
        out.append(by_label[token])
    return out


def _has_extreme_scores(version_id: int, min_score: float, max_put_score: float) -> bool:
    _ensure_db_connection()
    call = DB.execute_sql(
        "SELECT 1 FROM scores FORCE INDEX(scores_version_overall_date_IDX) "
        "WHERE version_id=%s AND overall >= %s LIMIT 1",
        (version_id, min_score),
    ).fetchone()
    if call:
        return True
    put = DB.execute_sql(
        "SELECT 1 FROM scores FORCE INDEX(scores_version_overall_date_IDX) "
        "WHERE version_id=%s AND overall <= %s LIMIT 1",
        (version_id, max_put_score),
    ).fetchone()
    return bool(put)


def _run_one_window(
    version: AlgorithmVersion,
    profile: dict,
    label: str,
    start: date,
    end: date,
    min_score: float,
    max_put_score: float,
) -> WindowMetrics:
    cfg, _profile = portfolio_profiles.apply_profile_to_config({}, profile["key"], ROOT)
    try:
        _fresh_db_connection()
        result = backtest_cascade.run_cascade_backtest(
            int(version.id),
            min_score=min_score,
            max_put_score=max_put_score,
            from_date=start,
            to_date=end,
            initial=INITIAL_CAPITAL,
            verbose=False,
            cfg=cfg,
        )
        return _metrics_from_result(version, profile, label, start, end, result)
    except Exception as exc:
        row = _empty_metrics_row(version, profile, label, start, end)
        row.error = f"{type(exc).__name__}: {exc}"
        return row


def _empty_metrics_row(
    version: AlgorithmVersion,
    profile: dict,
    label: str,
    start: date,
    end: date,
) -> WindowMetrics:
    token = _version_token(version)
    return WindowMetrics(
        **token,
        profile=profile["key"],
        profile_name=profile.get("name") or profile["key"],
        window=label,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )


def _metrics_from_result(
    version: AlgorithmVersion,
    profile: dict,
    label: str,
    start: date,
    end: date,
    result: dict | None,
) -> WindowMetrics:
    row = _empty_metrics_row(version, profile, label, start, end)
    if not result:
        row.error = "no qualifying signals"
        return row
    try:
        equity_curve = list(result.get("equity_curve") or [])
        trade_log = list(result.get("trade_log") or [])
        final_equity = _safe_float(result.get("final_equity"))
        max_dd = _safe_float(result.get("max_dd"))
        if final_equity is None and equity_curve:
            final_equity = float(equity_curve[-1][1])
        path = _path_metrics(equity_curve, start, TARGET_CAPITAL, DD_BARRIER)
        row.n_trades = len(trade_log)
        row.n_call = sum(1 for t in trade_log if t.get("side", "call") == "call")
        row.n_put = sum(1 for t in trade_log if t.get("side") == "put")
        row.final_equity = final_equity
        row.return_pct = ((final_equity / INITIAL_CAPITAL) - 1.0) * 100.0 if final_equity is not None else None
        row.max_dd_pct = _pct(max_dd)
        row.ulcer_index_pct = path["ulcer_index_pct"]
        row.max_days_underwater = path["max_days_underwater"]
        row.max_5d_drop_pct = path["max_5d_drop_pct"]
        row.max_10d_drop_pct = path["max_10d_drop_pct"]
        row.target_hit = path["target_hit"]
        row.target_hit_before_dd55 = path["target_hit_before_dd55"]
        row.days_to_target = path["days_to_target"]
        row.trading_days_to_target = path["trading_days_to_target"]
        row.first_dd55_date = path["first_dd55_date"]
        row.max_dd_before_target_pct = path["max_dd_before_target_pct"]
        row.collapse = bool(final_equity is not None and final_equity <= INITIAL_CAPITAL * COLLAPSE_FRACTION)
        return row
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"
        return row


def _run_precomputed_windows(
    version: AlgorithmVersion,
    profile: dict,
    windows: list[tuple[str, date, date]],
    min_score: float,
    max_put_score: float,
) -> list[WindowMetrics]:
    """Run one broad cascade precompute, then replay requested windows in memory."""
    _fresh_db_connection()
    cfg, _profile = portfolio_profiles.apply_profile_to_config({}, profile["key"], ROOT)
    parent_start = min(start for _label, start, _end in windows)
    parent_end = max(end for _label, _start, end in windows)
    parent = backtest_cascade.run_cascade_backtest(
        int(version.id),
        min_score=min_score,
        max_put_score=max_put_score,
        from_date=parent_start,
        to_date=parent_end,
        initial=INITIAL_CAPITAL,
        verbose=False,
        cfg=cfg,
    )
    if not parent:
        return [
            _metrics_from_result(version, profile, label, start, end, None)
            for label, start, end in windows
        ]

    outcomes_by_date = parent.get("outcomes_by_date") or {}
    all_dates = parent.get("all_dates") or []
    regime_dates = parent.get("regime_dates")
    regime_map = parent.get("regime_map")

    rows: list[WindowMetrics] = []
    for label, start, end in windows:
        sliced_outcomes = {
            d: list(outcomes)
            for d, outcomes in outcomes_by_date.items()
            if start <= d <= end and outcomes
        }
        sliced_dates = [d for d in all_dates if start <= d <= end]
        if not sliced_outcomes or not sliced_dates:
            rows.append(_metrics_from_result(version, profile, label, start, end, None))
            continue
        result = backtest_cascade.run_backtest(
            sliced_outcomes,
            sliced_dates,
            INITIAL_CAPITAL,
            regime_dates=regime_dates,
            regime_map=regime_map,
            cfg=cfg,
            ph=None,
        )
        rows.append(_metrics_from_result(version, profile, label, start, end, result))
    return rows


def _censored_days(row: WindowMetrics) -> float:
    start = date.fromisoformat(row.start_date)
    end = date.fromisoformat(row.end_date)
    window_days = max(1, (end - start).days)
    if row.target_hit_before_dd55 and row.days_to_target is not None:
        return float(row.days_to_target)
    if row.target_hit and row.days_to_target is not None:
        return float(row.days_to_target + 90)
    if row.first_dd55_date:
        return float(window_days + 180)
    return float(window_days + 90)


def _summarize_group(rows: list[WindowMetrics]) -> RankingRow:
    first = rows[0]
    annual = [r for r in rows if r.window in {w[0] for w in ANNUAL_WINDOWS} and not r.error]
    five = next((r for r in rows if r.window == "5y" and not r.error), None)
    now22 = next((r for r in rows if r.window == "22-now" and not r.error), None)

    n = len(annual) or 1
    safe_hit_rate = sum(1 for r in annual if r.target_hit_before_dd55) / n * 100.0
    hit_rate = sum(1 for r in annual if r.target_hit) / n * 100.0
    success_days = [float(r.days_to_target) for r in annual if r.target_hit_before_dd55 and r.days_to_target is not None]
    censored = [_censored_days(r) for r in annual] if annual else [545.0]
    dd_values = [r.max_dd_pct for r in annual if r.max_dd_pct is not None]
    ulcer_values = [r.ulcer_index_pct for r in annual if r.ulcer_index_pct is not None]
    ret_values = [r.return_pct for r in annual if r.return_pct is not None]
    collapse_rate = sum(1 for r in annual if r.collapse) / n * 100.0
    five_ret_mult = (five.final_equity / INITIAL_CAPITAL) if five and five.final_equity and five.final_equity > 0 else 0.0
    capped_log5 = max(-3.0, min(6.0, math.log10(five_ret_mult))) if five_ret_mult > 0 else -3.0

    worst_annual_dd = max(dd_values) if dd_values else None
    avg_ulcer = statistics.mean(ulcer_values) if ulcer_values else None
    avg_censored = statistics.mean(censored)
    five_dd = five.max_dd_pct if five else None

    velocity_score = (
        safe_hit_rate * 2.0
        + hit_rate * 0.75
        - avg_censored * 0.10
        - (worst_annual_dd or 100.0) * 1.0
        - (five_dd or 100.0) * 0.50
        - (avg_ulcer or 100.0) * 1.0
        - collapse_rate * 2.0
        + capped_log5 * 8.0
    )

    return RankingRow(
        rank=0,
        version_id=first.version_id,
        version_label=first.version_label,
        git_commit=first.git_commit,
        git_message=first.git_message,
        profile=first.profile,
        profile_name=first.profile_name,
        velocity_score=velocity_score,
        annual_safe_hit_rate_pct=safe_hit_rate,
        annual_hit_rate_pct=hit_rate,
        annual_median_days_to_target=statistics.median(success_days) if success_days else None,
        annual_avg_censored_days_to_target=avg_censored,
        worst_annual_dd_pct=worst_annual_dd,
        avg_annual_ulcer_pct=avg_ulcer,
        worst_year_return_pct=min(ret_values) if ret_values else None,
        collapse_rate_pct=collapse_rate,
        five_year_final_equity=five.final_equity if five else None,
        five_year_return_pct=five.return_pct if five else None,
        five_year_max_dd_pct=five.max_dd_pct if five else None,
        five_year_target_hit_before_dd55=five.target_hit_before_dd55 if five else None,
        twenty_two_now_return_pct=now22.return_pct if now22 else None,
        twenty_two_now_max_dd_pct=now22.max_dd_pct if now22 else None,
    )


def _sort_rankings(rows: Iterable[RankingRow]) -> list[RankingRow]:
    out = sorted(
        rows,
        key=lambda r: (
            -r.annual_safe_hit_rate_pct,
            -r.annual_hit_rate_pct,
            r.annual_avg_censored_days_to_target,
            r.worst_annual_dd_pct if r.worst_annual_dd_pct is not None else 999.0,
            -(r.five_year_return_pct if r.five_year_return_pct is not None else -1e18),
            -r.velocity_score,
        ),
    )
    for i, row in enumerate(out, start=1):
        row.rank = i
    return out


def _write_csv(path: Path, rows: list[dataclass]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    dicts = [asdict(row) for row in rows]
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(dicts[0].keys()))
        writer.writeheader()
        writer.writerows(dicts)
    tmp.replace(path)


def _write_recent(path: Path, text: str) -> None:
    try:
        _atomic_write_text(path, text)
    except PermissionError:
        pass


def _csv_bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def _csv_int(value) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _csv_float(value) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _load_window_metrics_csv(path: Path) -> list[WindowMetrics]:
    rows: list[WindowMetrics] = []
    if not path or not path.exists():
        return rows
    int_fields = {"version_id", "n_trades", "n_call", "n_put", "days_to_target", "trading_days_to_target", "max_days_underwater"}
    float_fields = {
        "final_equity", "return_pct", "max_dd_pct", "ulcer_index_pct",
        "max_5d_drop_pct", "max_10d_drop_pct", "max_dd_before_target_pct",
    }
    bool_fields = {"target_hit", "target_hit_before_dd55", "collapse"}
    with path.open("r", newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            clean = dict(raw)
            for field in int_fields:
                clean[field] = _csv_int(clean.get(field))
            for field in float_fields:
                clean[field] = _csv_float(clean.get(field))
            for field in bool_fields:
                clean[field] = _csv_bool(clean.get(field))
            rows.append(WindowMetrics(**clean))
    return rows


def _report(rankings: list[RankingRow], windows: list[tuple[str, date, date]], args: argparse.Namespace) -> str:
    lines = [
        "# Version Velocity Ranking",
        "",
        f"Generated: {_now_iso()}",
        "",
        "This is a read-only deterministic cascade comparison. It applies current portfolio-profile mechanics to persisted score rows.",
        "",
        "## Method",
        "",
        f"- Start capital: ${INITIAL_CAPITAL:,.0f}",
        f"- Target capital: ${TARGET_CAPITAL:,.0f}",
        f"- DD barrier: {DD_BARRIER:.0%}",
        f"- Annual windows reset to ${INITIAL_CAPITAL:,.0f}: {', '.join(w[0] for w in ANNUAL_WINDOWS)}",
        f"- Computed windows: {', '.join(w[0] for w in windows)}",
        f"- Versions: {args.versions}",
        f"- Profiles: {args.profiles}",
        "",
        "## Top Rankings",
        "",
        "| Rank | Version | Profile | Safe hit% | Hit% | Median days | Worst annual DD | 5y return | 5y DD | Score |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rankings[:25]:
        median_days = "" if row.annual_median_days_to_target is None else f"{row.annual_median_days_to_target:.0f}"
        worst_dd = "" if row.worst_annual_dd_pct is None else f"{row.worst_annual_dd_pct:.1f}%"
        five_ret = "" if row.five_year_return_pct is None else f"{row.five_year_return_pct:,.0f}%"
        five_dd = "" if row.five_year_max_dd_pct is None else f"{row.five_year_max_dd_pct:.1f}%"
        lines.append(
            f"| {row.rank} | {row.version_label} | {row.profile} | "
            f"{row.annual_safe_hit_rate_pct:.0f}% | {row.annual_hit_rate_pct:.0f}% | "
            f"{median_days} | {worst_dd} | {five_ret} | {five_dd} | {row.velocity_score:.1f} |"
        )
    lines.extend([
        "",
        "## Caveats",
        "",
        "- This is deterministic backtest velocity, not N-iteration Monte Carlo target-hit probability.",
        "- Historical score versions are compared through the current portfolio engine/profile, so this ranks score-row usefulness under today's execution model.",
        "- 5y compound is log-capped inside the score because uncapped fantasy compounding should not dominate drawdown survivability.",
        "",
        "## Files",
        "",
        "- `window_metrics.csv`: one row per version/profile/window.",
        "- `rankings.csv`: ranked version/profile rows.",
        "- `rankings.json`: structured version of the ranking table.",
    ])
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--versions", default="all", help="'all' or comma list like v58,v60,63")
    ap.add_argument("--profiles", default="sentinel", help="'all' or comma list: sentinel,core,apex")
    ap.add_argument("--windows", default="core", help="'core', 'all', 'annual', 'portfolio', or comma labels")
    ap.add_argument("--min-version", type=int, default=None)
    ap.add_argument("--max-version", type=int, default=None)
    ap.add_argument("--min-score", type=float, default=70.0)
    ap.add_argument("--max-put-score", type=float, default=25.0)
    ap.add_argument("--db-timeout", type=int, default=180, help="Read/write timeout seconds for this research runner only")
    ap.add_argument("--no-precompute", action="store_true", help="Run each window through the full cascade path independently")
    ap.add_argument("--resume-from", default="", help="Existing window_metrics.csv to seed completed version/profile rows")
    ap.add_argument("--run-dir", required=True)
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    DB.connect_params["read_timeout"] = int(args.db_timeout)
    DB.connect_params["write_timeout"] = int(args.db_timeout)
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    started = _now_iso()
    status_path = run_dir / "status.json"
    recent_path = run_dir / "run.recent.log"
    done_path = run_dir / "done.json"
    failed_path = run_dir / "failed.json"
    rows_path = run_dir / "window_metrics.csv"
    rankings_path = run_dir / "rankings.csv"
    rankings_json_path = run_dir / "rankings.json"
    report_path = run_dir / "REPORT.md"

    _atomic_write_json(status_path, {
        "phase": "initializing",
        "state": "running",
        "started_at": started,
        "updated_at": _now_iso(),
        "run_dir": str(run_dir),
        "outputs": {
            "window_metrics": str(rows_path),
            "rankings": str(rankings_path),
            "rankings_json": str(rankings_json_path),
            "report": str(report_path),
        },
    })

    try:
        versions = _resolve_versions(args.versions, args.min_version, args.max_version)
        profiles = _resolve_profiles(args.profiles)
        windows = _resolve_windows(args.windows)
        total = len(versions) * len(profiles) * len(windows)
        completed = 0
        skipped_versions: list[dict] = []
        requested_labels = {label for label, _start, _end in windows}
        window_rows: list[WindowMetrics] = _load_window_metrics_csv(Path(args.resume_from)) if args.resume_from else []
        allowed_version_ids = {int(v.id) for v in versions}
        allowed_profiles = {p["key"] for p in profiles}
        window_rows = [
            row for row in window_rows
            if row.version_id in allowed_version_ids and row.profile in allowed_profiles
        ]
        groups: dict[tuple[int, str], list[WindowMetrics]] = {}
        for row in window_rows:
            groups.setdefault((row.version_id, row.profile), []).append(row)
        complete_existing = {
            key
            for key, rows in groups.items()
            if {row.window for row in rows} >= requested_labels
            and all(not row.error for row in rows)
        }
        if complete_existing:
            window_rows = [
                row for row in window_rows
                if (row.version_id, row.profile) in complete_existing
            ]
            groups = {
                key: rows for key, rows in groups.items()
                if key in complete_existing
            }
            completed = len(complete_existing) * len(windows)
            print(f"Resumed {len(complete_existing)} complete version/profile groups from {args.resume_from}", flush=True)

        print(f"Version velocity run: versions={len(versions)} profiles={len(profiles)} windows={len(windows)} total_windows={total}", flush=True)

        for version in versions:
            profile_keys = [p["key"] for p in profiles]
            if all((int(version.id), pkey) in complete_existing for pkey in profile_keys):
                continue
            if not _has_extreme_scores(int(version.id), args.min_score, args.max_put_score):
                skipped_versions.append(_version_token(version) | {"reason": "no extreme score rows"})
                completed += len(profiles) * len(windows)
                _atomic_write_json(status_path, {
                    "phase": "running",
                    "state": "running",
                    "started_at": started,
                    "updated_at": _now_iso(),
                    "completed": completed,
                    "total": total,
                    "current_version": _version_label(version),
                    "skipped_versions": len(skipped_versions),
                    "rows": len(window_rows),
                })
                continue
            for profile in profiles:
                key = (int(version.id), profile["key"])
                if key in complete_existing:
                    continue
                groups.setdefault(key, [])
                if args.no_precompute:
                    window_iter = []
                    for label, start, end in windows:
                        window_iter.append(
                            _run_one_window(
                                version, profile, label, start, end,
                                min_score=args.min_score,
                                max_put_score=args.max_put_score,
                            )
                        )
                else:
                    version_label = _version_label(version)
                    current = f"{version_label}/{profile['key']}/precompute"
                    print(f"[{completed + 1}/{total}] {current}", flush=True)
                    _atomic_write_json(status_path, {
                        "phase": "running",
                        "state": "running",
                        "started_at": started,
                        "updated_at": _now_iso(),
                        "completed": completed,
                        "total": total,
                        "current": current,
                        "skipped_versions": len(skipped_versions),
                        "rows": len(window_rows),
                    })
                    try:
                        window_iter = _run_precomputed_windows(
                            version,
                            profile,
                            windows,
                            min_score=args.min_score,
                            max_put_score=args.max_put_score,
                        )
                    except Exception as exc:
                        print(
                            f"[warn] {_version_label(version)}/{profile['key']} precompute failed; "
                            f"falling back to independent windows: {type(exc).__name__}: {exc}",
                            flush=True,
                        )
                        try:
                            if not DB.is_closed():
                                DB.close()
                        except Exception:
                            pass
                        _ensure_db_connection()
                        window_iter = [
                            _run_one_window(
                                version, profile, label, start, end,
                                min_score=args.min_score,
                                max_put_score=args.max_put_score,
                            )
                            for label, start, end in windows
                        ]
                for row in window_iter:
                    current = f"{_version_label(version)}/{profile['key']}/{row.window}"
                    print(f"[{completed + 1}/{total}] {current}", flush=True)
                    window_rows.append(row)
                    groups[key].append(row)
                    completed += 1
                    status_line = (
                        f"{current}: final={_format_money(row.final_equity)} "
                        f"ret={'' if row.return_pct is None else f'{row.return_pct:,.0f}%'} "
                        f"dd={'' if row.max_dd_pct is None else f'{row.max_dd_pct:.1f}%'} "
                        f"target={'Y' if row.target_hit else 'N'} safe={'Y' if row.target_hit_before_dd55 else 'N'} "
                        f"err={row.error or ''}"
                    )
                    _write_recent(recent_path, status_line + "\n")
                    if completed % 10 == 0:
                        _write_csv(rows_path, window_rows)
                    try:
                        if not DB.is_closed():
                            DB.close()
                    except Exception:
                        pass

        _write_csv(rows_path, window_rows)
        ranking_rows = [_summarize_group(rows) for rows in groups.values() if any(not r.error for r in rows)]
        rankings = _sort_rankings(ranking_rows)
        _write_csv(rankings_path, rankings)
        _atomic_write_json(rankings_json_path, {
            "generated_at": _now_iso(),
            "method": {
                "initial_capital": INITIAL_CAPITAL,
                "target_capital": TARGET_CAPITAL,
                "dd_barrier": DD_BARRIER,
                "windows": [{"label": l, "start": s.isoformat(), "end": e.isoformat()} for l, s, e in windows],
                "versions": args.versions,
                "profiles": args.profiles,
                "ranking_sort": [
                    "annual_safe_hit_rate_pct desc",
                    "annual_hit_rate_pct desc",
                    "annual_avg_censored_days_to_target asc",
                    "worst_annual_dd_pct asc",
                    "five_year_return_pct desc",
                    "velocity_score desc",
                ],
            },
            "skipped_versions": skipped_versions,
            "rankings": [asdict(r) for r in rankings],
        })
        _atomic_write_text(report_path, _report(rankings, windows, args))
        _atomic_write_json(done_path, {
            "state": "done",
            "started_at": started,
            "finished_at": _now_iso(),
            "completed": completed,
            "total": total,
            "rows": len(window_rows),
            "rankings": len(rankings),
            "skipped_versions": skipped_versions,
            "outputs": {
                "window_metrics": str(rows_path),
                "rankings": str(rankings_path),
                "rankings_json": str(rankings_json_path),
                "report": str(report_path),
            },
        })
        _atomic_write_json(status_path, {
            "phase": "complete",
            "state": "done",
            "started_at": started,
            "updated_at": _now_iso(),
            "completed": completed,
            "total": total,
            "rows": len(window_rows),
            "rankings": len(rankings),
            "outputs": {
                "window_metrics": str(rows_path),
                "rankings": str(rankings_path),
                "rankings_json": str(rankings_json_path),
                "report": str(report_path),
            },
        })
        print(f"Done. Report: {report_path}", flush=True)
        return 0
    except Exception as exc:
        payload = {
            "state": "failed",
            "started_at": started,
            "finished_at": _now_iso(),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        _atomic_write_json(failed_path, payload)
        _atomic_write_json(status_path, payload | {"phase": "failed"})
        print(payload["traceback"], flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
