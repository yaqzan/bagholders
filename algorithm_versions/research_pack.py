from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import namedtuple
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from algorithm_versions import manager
from portfolio_profiles import DEFAULT_PROFILE_KEY as DEFAULT_PORTFOLIO_PROFILE


DEFAULT_LOOKBACKS = (365, 730, 1095, 1825, 3650)
DEFAULT_PERIODS = ("3d", "5d", "7d", "15d", "30d", "60d", "90d")
PRIMARY_UTILITY_PERIOD = "15d"
LEGACY_UTILITY_PERIOD = "7d"
DEFAULT_DTE = "30"
INITIAL_PORTFOLIO_CAPITAL = 50_000.0
PORTFOLIO_WINDOW_DB_TIMEOUT_SECONDS = 180
_PORTFOLIO_WINDOW_BASE_CACHE: dict[tuple[str, int, str, str | None], dict[str, Any]] = {}


@dataclass(frozen=True)
class UtilityParams:
    shrink_k: float = 100.0
    gamma_wr: float = 2.0
    gamma_tp: float = 0.75
    call_wr_ref: float = 0.75
    put_wr_ref: float = 0.76
    call_tp_ref: float = 0.66
    put_tp_ref: float = 0.61
    call_weights: Mapping[str, float] | None = None
    put_weights: Mapping[str, float] | None = None

    def weight_for(self, side: str, band: str) -> float:
        weights = self.call_weights if side == "call" else self.put_weights
        if weights is None:
            weights = DEFAULT_CALL_WEIGHTS if side == "call" else DEFAULT_PUT_WEIGHTS
        return float(weights.get(band, 1.0))

    def wr_ref_for(self, side: str) -> float:
        return self.call_wr_ref if side == "call" else self.put_wr_ref

    def tp_ref_for(self, side: str) -> float:
        return self.call_tp_ref if side == "call" else self.put_tp_ref


@dataclass(frozen=True)
class BandDefinition:
    side: str
    band: str
    cumulative_bucket: str
    subtract_bucket: str | None


@dataclass(frozen=True)
class PortfolioWindow:
    name: str
    start: str
    end: str | None
    primary_metric: str
    description: str


def _calendar_year_windows(start_year: int, end_year: int) -> tuple[PortfolioWindow, ...]:
    """Individual fresh-capital calendar-year windows, `year_YYYY` naming (matches
    the pre-existing year_2020..year_2025 entries below). Kept as a generator so the
    1998-2019 backfill (added 2026-07-29 once Sharadar delisted-equity data pushed
    price/score coverage back to 1997-12-31) doesn't hand-duplicate the pattern."""
    return tuple(
        PortfolioWindow(
            name=f"year_{year}",
            start=f"{year}-01-01",
            end=f"{year}-12-31",
            primary_metric="compound_and_drawdown",
            description=f"Calendar-year {year} portfolio path.",
        )
        for year in range(start_year, end_year + 1)
    )


DEFAULT_PORTFOLIO_WINDOWS = (
    *_calendar_year_windows(1998, 2019),
    PortfolioWindow(
        name="dotcom_crash_2000_2002",
        start="2000-03-01",
        end="2002-10-31",
        primary_metric="max_drawdown",
        description="Dot-com crash DD capture (Nasdaq peak Mar-2000 -> bottom Oct-2002). Honest delisted-inclusive universe as of the 2026-07-29 Sharadar rebuild (previously survivor-only, which read optimistically).",
    ),
    PortfolioWindow(
        name="gfc_crash_2007_2009",
        start="2007-10-01",
        end="2009-03-31",
        primary_metric="max_drawdown",
        description="2008 GFC DD capture (S&P peak Oct-2007 -> bottom Mar-2009). Honest delisted-inclusive universe as of the 2026-07-29 Sharadar rebuild (previously survivor-only, which read optimistically).",
    ),
    PortfolioWindow(
        name="deep_1995_now",
        start="1995-01-03",
        end=None,
        primary_metric="compound_and_drawdown",
        description="Full ~31y path incl dot-com + GFC. Permanently NOT ready: price/score data starts 1997-12-31 (Sharadar coverage floor), so this window can never satisfy its own 1995-01-03 start. Use year_1998+ or deep_1995_now's near-equivalent, the individual year_1998..year_2019 windows, for honest coverage of this era.",
    ),
    PortfolioWindow(
        name="covid_crash_2020",
        start="2020-01-01",
        end="2020-04-30",
        primary_metric="max_drawdown",
        description="Crash-window DD capture for the March 2020 drawdown regime.",
    ),
    PortfolioWindow(
        name="covid_cycle_2020_2021",
        start="2020-01-01",
        end="2021-12-31",
        primary_metric="compound_and_drawdown",
        description="Major DD year plus following bull year; catches crash survival and rebound participation.",
    ),
    PortfolioWindow(
        name="year_2020",
        start="2020-01-01",
        end="2020-12-31",
        primary_metric="compound_and_drawdown",
        description="Calendar-year 2020 portfolio path.",
    ),
    PortfolioWindow(
        name="year_2021",
        start="2021-01-01",
        end="2021-12-31",
        primary_metric="compound_and_drawdown",
        description="Calendar-year 2021 portfolio path.",
    ),
    PortfolioWindow(
        name="year_2022",
        start="2022-01-01",
        end="2022-12-31",
        primary_metric="compound_and_drawdown",
        description="Calendar-year 2022 portfolio path.",
    ),
    PortfolioWindow(
        name="year_2023",
        start="2023-01-01",
        end="2023-12-31",
        primary_metric="compound_and_drawdown",
        description="Calendar-year 2023 portfolio path.",
    ),
    PortfolioWindow(
        name="year_2024",
        start="2024-01-01",
        end="2024-12-31",
        primary_metric="compound_and_drawdown",
        description="Calendar-year 2024 portfolio path.",
    ),
    PortfolioWindow(
        name="year_2025",
        start="2025-01-01",
        end="2025-12-31",
        primary_metric="compound_and_drawdown",
        description="Calendar-year 2025 portfolio path.",
    ),
    PortfolioWindow(
        name="year_2026_ytd",
        start="2026-01-01",
        end=None,
        primary_metric="compound_and_drawdown",
        description="Current 2026 year-to-date portfolio path.",
    ),
    PortfolioWindow(
        name="2020_now",
        start="2020-01-01",
        end=None,
        primary_metric="compound_and_drawdown",
        description="Full post-COVID path from the crash setup through current recalc coverage.",
    ),
    PortfolioWindow(
        name="22_now",
        start="2022-01-01",
        end=None,
        primary_metric="compound_and_drawdown",
        description="Existing 2022-now production-equivalent compounding/DD gate.",
    ),
)


DEFAULT_CALL_WEIGHTS = {
    "75-79": 1.0,
    "80-84": 1.0,
    "85-89": 1.0,
    "90-94": 1.0,
    "95-100": 1.67,
}
DEFAULT_PUT_WEIGHTS = {
    "21-25": 0.80,
    "16-20": 0.80,
    "11-15": 1.0,
    "6-10": 1.0,
    "0-5": 1.0,
}
DEFAULT_PARAMS = UtilityParams(
    call_weights=DEFAULT_CALL_WEIGHTS,
    put_weights=DEFAULT_PUT_WEIGHTS,
)

CALL_BANDS = (
    BandDefinition("call", "75-79", "75+", "80+"),
    BandDefinition("call", "80-84", "80+", "85+"),
    BandDefinition("call", "85-89", "85+", "90+"),
    BandDefinition("call", "90-94", "90+", "95+"),
    BandDefinition("call", "95-100", "95+", None),
)
PUT_BANDS = (
    BandDefinition("put", "21-25", "<25", "<20"),
    BandDefinition("put", "16-20", "<20", "<15"),
    BandDefinition("put", "11-15", "<15", "<10"),
    BandDefinition("put", "6-10", "<10", "<5"),
    BandDefinition("put", "0-5", "<5", None),
)
ALL_BANDS = CALL_BANDS + PUT_BANDS


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_safe(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_csv(value: str | None, default: Iterable[Any]) -> list[str]:
    if value is None or str(value).strip() == "":
        return [str(v) for v in default]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def parse_int_csv(value: str | None, default: Iterable[int]) -> list[int]:
    return [int(v) for v in parse_csv(value, default)]


def parse_portfolio_profiles(value: str | Iterable[str] | None) -> list[str]:
    import portfolio_profiles

    root = manager.project_root()
    if value is None:
        raw_profiles = [DEFAULT_PORTFOLIO_PROFILE]
    elif isinstance(value, str):
        if value.strip().lower() == "all":
            raw_profiles = list(portfolio_profiles.PROFILE_ORDER)
        else:
            raw_profiles = parse_csv(value.replace(";", ","), [DEFAULT_PORTFOLIO_PROFILE])
    else:
        raw_profiles = list(value)

    keys = []
    for raw in raw_profiles:
        if str(raw).strip().lower() == "all":
            keys.extend(portfolio_profiles.PROFILE_ORDER)
        else:
            keys.append(portfolio_profiles.normalize_profile_key(str(raw), root))
    return list(dict.fromkeys(keys))


def parse_portfolio_windows(value: str | Iterable[str] | None) -> tuple[PortfolioWindow, ...]:
    if value is None:
        return tuple(DEFAULT_PORTFOLIO_WINDOWS)
    if isinstance(value, str):
        if value.strip() == "":
            return tuple(DEFAULT_PORTFOLIO_WINDOWS)
        raw_windows = parse_csv(value.replace(";", ","), [window.name for window in DEFAULT_PORTFOLIO_WINDOWS])
    else:
        raw_windows = [str(item) for item in value]

    if any(str(item).strip().lower() == "all" for item in raw_windows):
        return tuple(DEFAULT_PORTFOLIO_WINDOWS)

    by_name = {window.name: window for window in DEFAULT_PORTFOLIO_WINDOWS}
    selected: list[PortfolioWindow] = []
    for raw in raw_windows:
        name = str(raw).strip()
        if not name:
            continue
        try:
            selected.append(by_name[name])
        except KeyError as exc:
            raise ValueError(f"Unknown portfolio window: {name}") from exc
    return tuple(dict.fromkeys(selected))


def _row_value(row: Any, field: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(field, default)
    return getattr(row, field, default)


def _rate_count(row: Any, period: str) -> tuple[int, float | None]:
    n = int(_row_value(row, "sample_count", 0) or 0)
    rate = _row_value(row, f"win_rate_{period}", None)
    if rate is None:
        return n, None
    return n, float(rate) / 100.0


def _cumulative_stats(rows_by_bucket: Mapping[str, Any], bucket: str, period: str) -> tuple[int, float | None]:
    return _rate_count(rows_by_bucket.get(bucket), period)


def _independent_stats(
    rows_by_bucket: Mapping[str, Any],
    band: BandDefinition,
    period: str,
) -> tuple[int, float | None]:
    total_n, total_rate = _cumulative_stats(rows_by_bucket, band.cumulative_bucket, period)
    sub_n = 0
    sub_rate = None
    if band.subtract_bucket:
        sub_n, sub_rate = _cumulative_stats(rows_by_bucket, band.subtract_bucket, period)

    independent_n = max(total_n - sub_n, 0)
    if independent_n <= 0 or total_rate is None:
        return independent_n, None

    total_wins = total_n * total_rate
    sub_wins = sub_n * sub_rate if sub_rate is not None else 0.0
    independent_wins = min(max(total_wins - sub_wins, 0.0), float(independent_n))
    return independent_n, independent_wins / independent_n


def shrunk_rate(rate: float | None, n: int, reference: float, shrink_k: float) -> float | None:
    if rate is None or n <= 0:
        return None
    return (rate * n + shrink_k * reference) / (n + shrink_k)


def build_utility_from_rows(
    wr_rows_by_bucket: Mapping[str, Any],
    tp_rows_by_bucket: Mapping[str, Any] | None,
    *,
    period: str = PRIMARY_UTILITY_PERIOD,
    params: UtilityParams = DEFAULT_PARAMS,
) -> dict[str, Any]:
    """Build N-weighted independent-band utility from cumulative assessment rows.

    Assessment bucket rows are cumulative by design. This function converts them
    into non-overlapping score bands before applying the WR-primary utility
    formula and optional TP shadow confirmation.
    """
    sides: dict[str, dict[str, Any]] = {
        "call": {"side": "call", "bands": [], "utility_wr": 0.0, "utility_wrtp": 0.0, "n": 0},
        "put": {"side": "put", "bands": [], "utility_wr": 0.0, "utility_wrtp": 0.0, "n": 0},
    }

    for band in ALL_BANDS:
        wr_n, wr_rate = _independent_stats(wr_rows_by_bucket, band, period)
        tp_n = 0
        tp_rate = None
        if tp_rows_by_bucket is not None:
            tp_n, tp_rate = _independent_stats(tp_rows_by_bucket, band, period)

        side_payload = sides[band.side]
        weight = params.weight_for(band.side, band.band)
        wr_ref = params.wr_ref_for(band.side)
        tp_ref = params.tp_ref_for(band.side)
        wr_shrunk = shrunk_rate(wr_rate, wr_n, wr_ref, params.shrink_k)
        tp_shrunk = shrunk_rate(tp_rate, tp_n, tp_ref, params.shrink_k)

        wr_quality = (wr_shrunk / wr_ref) ** params.gamma_wr if wr_shrunk is not None else None
        utility_wr = wr_n * weight * wr_quality if wr_quality is not None else 0.0

        tp_quality = None
        utility_wrtp = None
        if wr_quality is not None and tp_shrunk is not None:
            tp_quality = (tp_shrunk / tp_ref) ** params.gamma_tp
            utility_wrtp = wr_n * weight * wr_quality * tp_quality

        side_payload["bands"].append(
            {
                "band": band.band,
                "cumulative_bucket": band.cumulative_bucket,
                "subtract_bucket": band.subtract_bucket,
                "n": wr_n,
                "wr": wr_rate,
                "wr_shrunk": wr_shrunk,
                "tp_n": tp_n if tp_rows_by_bucket is not None else None,
                "tp": tp_rate,
                "tp_shrunk": tp_shrunk,
                "weight": weight,
                "wr_quality": wr_quality,
                "tp_quality": tp_quality,
                "utility_wr": utility_wr,
                "utility_wrtp": utility_wrtp,
            }
        )
        side_payload["utility_wr"] += utility_wr
        if utility_wrtp is not None:
            side_payload["utility_wrtp"] += utility_wrtp
        side_payload["n"] += wr_n

    total_wr = sides["call"]["utility_wr"] + sides["put"]["utility_wr"]
    total_wrtp = sides["call"]["utility_wrtp"] + sides["put"]["utility_wrtp"]
    return {
        "period": period,
        "params": asdict(params),
        "reference_note": "Primary scoring comparisons now use WR15 to reduce major DD risk. Non-15d utility remains diagnostic unless compared with period-specific references.",
        "sides": sides,
        "total": {
            "n": sides["call"]["n"] + sides["put"]["n"],
            "utility_wr": total_wr,
            "utility_wrtp": total_wrtp,
        },
    }


def ensure_db_open(force_reconnect: bool = False) -> None:
    from database.trader_database import DB

    if force_reconnect and not DB.is_closed():
        DB.close()
    if DB.is_closed():
        DB.connect(reuse_if_open=True)


def resolve_version(token: str | None):
    ensure_db_open()
    return manager.resolve_algorithm_version(token, create_current=False)


def git_state(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", *args],
                cwd=str(root),
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=30,
            ).strip()
        except Exception:
            return ""

    status = run("status", "--short").splitlines()
    return {
        "head": run("rev-parse", "HEAD"),
        "head_short": run("rev-parse", "--short", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(status),
        "dirty_status": status,
    }


def version_payload(version) -> dict[str, Any]:
    return {
        "id": int(version.id),
        "label": version.production_label,
        "git_commit": version.git_commit,
        "git_message": version.git_message,
        "notes": version.notes,
        "created_at": version.created_at,
    }


def score_coverage_by_year(version, start_year: int = 1990, end_year: int | None = None) -> dict[str, Any]:
    # start_year lowered 2010 -> 1990 (2026-07-05) so coverage reflects the deep
    # backfill (v74 now spans 1995->present incl dot-com + GFC); the old 2010
    # default silently truncated coverage and failed the pre-2010 window readiness gate.
    from database.trader_database import DB

    if end_year is None:
        end_year = datetime.now().year

    years: list[dict[str, Any]] = []
    total_rows = 0
    total_dates = 0
    min_date = None
    max_date = None

    for year in range(start_year, end_year + 1):
        start = f"{year}-01-01"
        stop = f"{year}-12-31"
        rows, dates, year_min, year_max = DB.execute_sql(
            """
            SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date)
            FROM scores FORCE INDEX(scores_version_date_IDX)
            WHERE version_id = %s AND date BETWEEN %s AND %s
            """,
            (version.id, start, stop),
        ).fetchone()
        rows = int(rows or 0)
        dates = int(dates or 0)
        years.append(
            {
                "year": year,
                "score_rows": rows,
                "distinct_dates": dates,
                "min_date": year_min,
                "max_date": year_max,
            }
        )
        total_rows += rows
        total_dates += dates
        if year_min and (min_date is None or year_min < min_date):
            min_date = year_min
        if year_max and (max_date is None or year_max > max_date):
            max_date = year_max

    nonempty = [row["year"] for row in years if row["score_rows"] > 0]
    return {
        "version": version_payload(version),
        "total_score_rows": total_rows,
        "distinct_score_dates": total_dates,
        "min_date": min_date,
        "max_date": max_date,
        "nonempty_years": nonempty,
        "years": years,
    }


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def score_lookback_readiness(coverage: Mapping[str, Any], lookbacks: Iterable[int]) -> dict[str, Any]:
    min_dt = _as_date(coverage.get("min_date"))
    max_dt = _as_date(coverage.get("max_date"))
    span_days = (max_dt - min_dt).days if min_dt and max_dt else 0
    return {
        str(int(lookback)): {
            "ready": span_days >= int(lookback),
            "span_days": span_days,
            "min_date": min_dt,
            "max_date": max_dt,
        }
        for lookback in lookbacks
    }


def portfolio_window_readiness(
    coverage: Mapping[str, Any],
    windows: Iterable[PortfolioWindow] = DEFAULT_PORTFOLIO_WINDOWS,
) -> dict[str, Any]:
    min_dt = _as_date(coverage.get("min_date"))
    max_dt = _as_date(coverage.get("max_date"))
    readiness = {}
    for window in windows:
        start_dt = date.fromisoformat(window.start)
        end_dt = date.fromisoformat(window.end) if window.end else max_dt
        missing = []
        if min_dt is None:
            missing.append("no score min_date")
        elif min_dt > start_dt:
            missing.append(f"score coverage starts {min_dt.isoformat()} after window start {window.start}")
        if max_dt is None:
            missing.append("no score max_date")
        elif end_dt is not None and max_dt < end_dt:
            missing.append(f"score coverage ends {max_dt.isoformat()} before window end {end_dt.isoformat()}")
        readiness[window.name] = {
            "ready": not missing,
            "missing": missing,
            "start": window.start,
            "end": window.end,
            "coverage_min_date": min_dt,
            "coverage_max_date": max_dt,
            "primary_metric": window.primary_metric,
            "description": window.description,
        }
    return readiness


def assessment_runs_payload(version, lookbacks: Iterable[int], dte: str = DEFAULT_DTE) -> dict[str, Any]:
    from database.models.core import ScoreAssessmentResult, ScoreAssessmentRun

    expected = []
    runs = []
    by_key: dict[tuple[int, str], Any] = {}

    query = (
        ScoreAssessmentRun.select()
        .where(
            (ScoreAssessmentRun.version == version)
            & ((ScoreAssessmentRun.symbol == "") | ScoreAssessmentRun.symbol.is_null(True))
            & (ScoreAssessmentRun.dte_strategy == str(dte))
        )
        .order_by(ScoreAssessmentRun.lookback_days, ScoreAssessmentRun.metric)
    )
    for run in query:
        key = (int(run.lookback_days), str(run.metric or "wr"))
        by_key[key] = run
        result_count = (
            ScoreAssessmentResult.select()
            .where(ScoreAssessmentResult.run == run)
            .count()
        )
        runs.append(
            {
                "id": run.id,
                "lookback_days": int(run.lookback_days),
                "metric": run.metric,
                "dte_strategy": run.dte_strategy,
                "total_peaks": run.total_peaks,
                "run_at": run.run_at,
                "result_rows": result_count,
                "git_commit": run.git_commit,
            }
        )

    missing = []
    for lookback in lookbacks:
        for metric in ("wr", "tp"):
            expected.append({"lookback_days": int(lookback), "metric": metric})
            if (int(lookback), metric) not in by_key:
                missing.append({"lookback_days": int(lookback), "metric": metric})

    return {
        "version": version_payload(version),
        "dte_strategy": str(dte),
        "expected": expected,
        "runs": runs,
        "missing": missing,
        "complete": not missing,
    }


def get_assessment_run(version, lookback: int, metric: str, dte: str = DEFAULT_DTE):
    from database.models.core import ScoreAssessmentRun

    return ScoreAssessmentRun.get_or_none(
        (ScoreAssessmentRun.version == version)
        & ((ScoreAssessmentRun.symbol == "") | ScoreAssessmentRun.symbol.is_null(True))
        & (ScoreAssessmentRun.lookback_days == int(lookback))
        & (ScoreAssessmentRun.dte_strategy == str(dte))
        & (ScoreAssessmentRun.metric == metric)
    )


def rows_by_bucket(run) -> dict[str, Any]:
    from database.models.core import ScoreAssessmentResult

    if run is None:
        return {}
    return {
        row.bucket: row
        for row in ScoreAssessmentResult.select().where(ScoreAssessmentResult.run == run)
    }


def compute_utility_for_version(
    version,
    *,
    lookback: int = 1825,
    period: str = PRIMARY_UTILITY_PERIOD,
    dte: str = DEFAULT_DTE,
    params: UtilityParams = DEFAULT_PARAMS,
) -> dict[str, Any]:
    wr_run = get_assessment_run(version, lookback, "wr", dte=dte)
    tp_run = get_assessment_run(version, lookback, "tp", dte=dte)
    payload = build_utility_from_rows(
        rows_by_bucket(wr_run),
        rows_by_bucket(tp_run) if tp_run is not None else None,
        period=period,
        params=params,
    )
    payload.update(
        {
            "version": version_payload(version),
            "lookback_days": int(lookback),
            "dte_strategy": str(dte),
            "wr_run_id": wr_run.id if wr_run else None,
            "tp_run_id": tp_run.id if tp_run else None,
            "complete": wr_run is not None and tp_run is not None,
        }
    )
    return payload


def utility_grid_for_version(
    version,
    *,
    lookbacks: Iterable[int] = DEFAULT_LOOKBACKS,
    periods: Iterable[str] = DEFAULT_PERIODS,
    dte: str = DEFAULT_DTE,
    params: UtilityParams = DEFAULT_PARAMS,
) -> list[dict[str, Any]]:
    rows = []
    for lookback in lookbacks:
        for period in periods:
            try:
                payload = compute_utility_for_version(
                    version,
                    lookback=int(lookback),
                    period=str(period),
                    dte=dte,
                    params=params,
                )
                rows.append(
                    {
                        "lookback_days": int(lookback),
                        "period": str(period),
                        "complete": payload["complete"],
                        "call_n": payload["sides"]["call"]["n"],
                        "put_n": payload["sides"]["put"]["n"],
                        "total_n": payload["total"]["n"],
                        "call_utility_wr": payload["sides"]["call"]["utility_wr"],
                        "put_utility_wr": payload["sides"]["put"]["utility_wr"],
                        "total_utility_wr": payload["total"]["utility_wr"],
                        "call_utility_wrtp": payload["sides"]["call"]["utility_wrtp"],
                        "put_utility_wrtp": payload["sides"]["put"]["utility_wrtp"],
                        "total_utility_wrtp": payload["total"]["utility_wrtp"],
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "lookback_days": int(lookback),
                        "period": str(period),
                        "complete": False,
                        "error": str(exc),
                    }
                )
    return rows


def temporal_stats_payload(version) -> dict[str, Any]:
    from database.models.core import BacktestTemporalStats

    rows = []
    for row in (
        BacktestTemporalStats.select()
        .where(BacktestTemporalStats.version == version)
        .order_by(BacktestTemporalStats.dte_strategy)
    ):
        rows.append(
            {
                "dte_strategy": row.dte_strategy,
                "portfolio_profile": row.portfolio_profile,
                "computed_at": row.computed_at,
                "initial_capital": row.initial_capital,
                "summary": row.summary(),
                "yearly": row.yearly(),
                "monthly": row.monthly(),
                "monthly_avg": row.monthly_avg(),
            }
        )
    return {
        "version": version_payload(version),
        "rows": rows,
        "row_key": ["dte_strategy", "portfolio_profile"],
        "complete": bool(rows),
    }


def max_drawdown_event(equity_curve: list[tuple[Any, float]]) -> dict[str, Any]:
    if not equity_curve:
        return {"max_dd_pct": None, "peak_date": None, "trough_date": None}
    peak_date, peak_equity = equity_curve[0]
    max_dd = 0.0
    max_peak_date = peak_date
    trough_date = peak_date
    for point_date, equity in equity_curve:
        equity = float(equity)
        if equity > peak_equity:
            peak_date, peak_equity = point_date, equity
        if peak_equity <= 0:
            continue
        drawdown = (peak_equity - equity) / peak_equity
        if drawdown > max_dd:
            max_dd = drawdown
            max_peak_date = peak_date
            trough_date = point_date
    return {
        "max_dd_pct": max_dd * 100.0,
        "peak_date": max_peak_date,
        "trough_date": trough_date,
    }


def summarize_portfolio_result(window: PortfolioWindow, result: dict[str, Any]) -> dict[str, Any]:
    trade_log = result.get("trade_log", []) or []
    equity_curve = result.get("equity_curve", []) or []
    outcomes_by_date = result.get("outcomes_by_date") or {}
    initial = float(result.get("initial") or INITIAL_PORTFOLIO_CAPITAL)
    terminal_equity = float(equity_curve[-1][1]) if equity_curve else float(result.get("final_equity") or initial)
    call_trades = [row for row in trade_log if row.get("side", "call") == "call"]
    put_trades = [row for row in trade_log if row.get("side") == "put"]
    n_days = len(equity_curve)

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    def median(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2.0

    def percentile(values: list[float], pct: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        idx = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * pct) - 1))
        return ordered[idx]

    def tp_rate(rows: list[dict[str, Any]]) -> float | None:
        if not rows:
            return None
        return sum(1 for row in rows if row.get("outcome") == "tp") / len(rows) * 100.0

    def outcome_rate(rows: list[dict[str, Any]], outcome: str) -> float | None:
        if not rows:
            return None
        return sum(1 for row in rows if row.get("outcome") == outcome) / len(rows) * 100.0

    def hold_values(rows: list[dict[str, Any]]) -> list[float]:
        values = []
        for row in rows:
            try:
                values.append(float(row.get("hold_bars")))
            except (TypeError, ValueError):
                pass
        return values

    def monthly_returns() -> list[dict[str, Any]]:
        months: dict[tuple[int, int], list[tuple[Any, float]]] = {}
        for point_date, equity in equity_curve:
            months.setdefault((point_date.year, point_date.month), []).append((point_date, float(equity)))
        rows = []
        for (year, month), points in sorted(months.items()):
            start_equity = points[0][1]
            end_equity = points[-1][1]
            rows.append({
                "year": year,
                "month": month,
                "start_date": points[0][0],
                "end_date": points[-1][0],
                "return_pct": (end_equity / start_equity - 1.0) * 100.0 if start_equity else None,
            })
        return rows

    def milestone(target: float) -> dict[str, Any] | None:
        if not equity_curve:
            return None
        start_date = equity_curve[0][0]
        for index, (point_date, equity) in enumerate(equity_curve, start=1):
            if float(equity) >= target:
                return {
                    "target": target,
                    "hit_date": point_date,
                    "trading_bars": index,
                    "calendar_days": (point_date - start_date).days,
                }
        return None

    monthly = monthly_returns()
    monthly_values = [row["return_pct"] for row in monthly if row.get("return_pct") is not None]
    hold_bars = hold_values(trade_log)
    call_hold_bars = hold_values(call_trades)
    put_hold_bars = hold_values(put_trades)
    raw_signal_rows = [outcome for rows in outcomes_by_date.values() for outcome in rows]
    raw_call_signals = sum(1 for outcome in raw_signal_rows if getattr(outcome, "side", "call") == "call")
    raw_put_signals = sum(1 for outcome in raw_signal_rows if getattr(outcome, "side", "call") == "put")
    raw_signals = raw_call_signals + raw_put_signals
    avg_hold_bars = mean(hold_bars)
    call_avg_hold_bars = mean(call_hold_bars)
    put_avg_hold_bars = mean(put_hold_bars)
    accepted_entries_per_day = len(trade_log) / n_days if n_days else None
    max_positions = result.get("max_positions")
    max_call_positions = result.get("max_call_positions")
    max_put_positions = result.get("max_put_positions")

    def target_rate(cap: Any, avg_hold: float | None) -> float | None:
        try:
            cap_value = float(cap)
        except (TypeError, ValueError):
            return None
        if not avg_hold or avg_hold <= 0:
            return None
        return cap_value / avg_hold

    target_entries_per_day = target_rate(max_positions, avg_hold_bars)
    call_target_entries_per_day = target_rate(max_call_positions, call_avg_hold_bars)
    put_target_entries_per_day = target_rate(max_put_positions, put_avg_hold_bars)
    hydration_gap_per_day = (
        max(0.0, target_entries_per_day - accepted_entries_per_day)
        if target_entries_per_day is not None and accepted_entries_per_day is not None
        else None
    )

    def slot_util(avg_open: Any, cap: Any) -> float | None:
        try:
            cap_value = float(cap)
            avg_value = float(avg_open)
        except (TypeError, ValueError):
            return None
        if cap_value <= 0:
            return None
        return avg_value / cap_value * 100.0

    return {
        "window": asdict(window),
        "actual_start_date": result.get("start_date"),
        "actual_end_date": result.get("end_date"),
        "initial_capital": initial,
        "terminal_equity": terminal_equity,
        "total_return_pct": (terminal_equity / initial - 1.0) * 100.0 if initial else None,
        "log10_equity_multiple": math.log10(terminal_equity / initial) if initial and terminal_equity > 0 else None,
        "max_dd_pct": float(result.get("max_dd", 0.0)) * 100.0,
        "max_dd_event": max_drawdown_event(equity_curve),
        "n_trades": len(trade_log),
        "call_trades": len(call_trades),
        "put_trades": len(put_trades),
        "tp_rate": tp_rate(trade_log),
        "call_tp_rate": tp_rate(call_trades),
        "put_tp_rate": tp_rate(put_trades),
        "sl_rate": outcome_rate(trade_log, "sl"),
        "hard_rate": outcome_rate(trade_log, "hard"),
        "avg_hold_bars": avg_hold_bars,
        "call_avg_hold_bars": call_avg_hold_bars,
        "put_avg_hold_bars": put_avg_hold_bars,
        "median_hold_bars": median(hold_bars),
        "p75_hold_bars": percentile(hold_bars, 0.75),
        "p90_hold_bars": percentile(hold_bars, 0.90),
        "monthly_returns": monthly,
        "avg_monthly_return_pct": mean(monthly_values),
        "median_monthly_return_pct": median(monthly_values),
        "monthly_return_count": len(monthly_values),
        "raw_signals": raw_signals,
        "call_raw_signals": raw_call_signals,
        "put_raw_signals": raw_put_signals,
        "raw_signals_per_day": raw_signals / n_days if n_days else None,
        "call_raw_signals_per_day": raw_call_signals / n_days if n_days else None,
        "put_raw_signals_per_day": raw_put_signals / n_days if n_days else None,
        "accepted_entries_per_day": accepted_entries_per_day,
        "call_entries_per_day": len(call_trades) / n_days if n_days else None,
        "put_entries_per_day": len(put_trades) / n_days if n_days else None,
        "target_entries_per_day": target_entries_per_day,
        "call_target_entries_per_day": call_target_entries_per_day,
        "put_target_entries_per_day": put_target_entries_per_day,
        "hydration_gap_per_day": hydration_gap_per_day,
        "avg_open_positions": result.get("avg_open_positions"),
        "avg_call_open_positions": result.get("avg_call_open_positions"),
        "avg_put_open_positions": result.get("avg_put_open_positions"),
        "max_open_positions": result.get("max_open_positions"),
        "avg_slot_utilization_pct": slot_util(result.get("avg_open_positions"), max_positions),
        "call_slot_utilization_pct": slot_util(result.get("avg_call_open_positions"), max_call_positions),
        "put_slot_utilization_pct": slot_util(result.get("avg_put_open_positions"), max_put_positions),
        "pool_full_rate": result.get("pool_full_rate"),
        "call_pool_full_rate": result.get("call_pool_full_rate"),
        "put_pool_full_rate": result.get("put_pool_full_rate"),
        "avg_reserved_cash_pct": result.get("avg_reserved_cash_pct"),
        "max_reserved_cash_pct": result.get("max_reserved_cash_pct"),
        "avg_open_premium_pct": result.get("avg_open_premium_pct"),
        "max_open_premium_pct": result.get("max_open_premium_pct"),
        "avg_open_premium_base_pct": result.get("avg_open_premium_base_pct"),
        "max_open_premium_base_pct": result.get("max_open_premium_base_pct"),
        "avg_call_open_premium_base_pct": result.get("avg_call_open_premium_base_pct"),
        "avg_put_open_premium_base_pct": result.get("avg_put_open_premium_base_pct"),
        "avg_saturation_scale": result.get("avg_saturation_scale"),
        "time_to_1m": milestone(1_000_000.0),
        "time_to_10m": milestone(10_000_000.0),
    }


def _install_fast_signal_loaders(cascade_module) -> None:
    if getattr(cascade_module, "_research_pack_fast_signal_loaders", False):
        return

    CallSignal = namedtuple("CallSignal", "symbol date overall trend weight_info stoch")
    PutSignal = namedtuple("PutSignal", "symbol date overall trend")

    def _raw_signal_rows(
        *,
        version_id: int,
        threshold: float,
        from_date,
        to_date,
        side: str,
        flagged_only: bool,
    ):
        from database.trader_database import DB

        ensure_db_open(force_reconnect=True)
        columns = (
            "s.symbol, s.date, s.overall, s.trend, s.weight_info, s.stoch"
            if side == "call"
            else "s.symbol, s.date, s.overall, s.trend"
        )
        comparator = ">=" if side == "call" else "<="
        order_dir = "DESC" if side == "call" else "ASC"
        join_sql = " JOIN stocks AS st ON s.symbol = st.symbol AND st.flagged = 1" if flagged_only else ""
        sql = (
            f"SELECT {columns} "
            "FROM scores AS s FORCE INDEX(scores_version_overall_date_IDX)"
            f"{join_sql} "
            f"WHERE s.version_id = %s AND s.overall {comparator} %s AND s.date >= %s"
        )
        params: list[Any] = [int(version_id), threshold, from_date or cascade_module.MIN_DATE]
        if to_date:
            sql += " AND s.date <= %s"
            params.append(to_date)
        sql += f" ORDER BY s.date, s.overall {order_dir}, s.symbol"
        return DB.execute_sql(sql, params).fetchall()

    def _apply_weak_weekly_call_drop(sigs):
        if not getattr(cascade_module, "WEAK_WEEKLY_CALL_DROP", False) or not sigs:
            return sigs
        kept = []
        dropped = 0
        for sig in sigs:
            overall = int(sig.overall)
            if not (cascade_module.WEAK_WEEKLY_CALL_MIN_OV <= overall <= cascade_module.WEAK_WEEKLY_CALL_MAX_OV):
                kept.append(sig)
                continue
            weekly_adj = None
            raw_weight_info = getattr(sig, "weight_info", None)
            if raw_weight_info:
                try:
                    weight_info = json.loads(raw_weight_info) if isinstance(raw_weight_info, str) else raw_weight_info
                    weekly_raw = weight_info.get("w_adj")
                    if weekly_raw is None:
                        weekly_raw = weight_info.get("weekly_adj")
                    if weekly_raw is not None:
                        weekly_adj = float(weekly_raw)
                except Exception:
                    weekly_adj = None
            if weekly_adj is None or weekly_adj >= cascade_module.WEAK_WEEKLY_CALL_WADJ_LT:
                kept.append(sig)
                continue
            if cascade_module.WEAK_WEEKLY_CALL_STOCH_GE > 0:
                stoch_value = getattr(sig, "stoch", None)
                if stoch_value is None or int(stoch_value) < cascade_module.WEAK_WEEKLY_CALL_STOCH_GE:
                    kept.append(sig)
                    continue
            dropped += 1
        if dropped:
            print(
                f"  WEAK_WEEKLY_CALL_DROP: dropped {dropped}/{len(sigs)} calls "
                f"(overall in [{cascade_module.WEAK_WEEKLY_CALL_MIN_OV},{cascade_module.WEAK_WEEKLY_CALL_MAX_OV}] "
                f"AND w_adj<{cascade_module.WEAK_WEEKLY_CALL_WADJ_LT}"
                f"{' AND stoch>=' + str(cascade_module.WEAK_WEEKLY_CALL_STOCH_GE) if cascade_module.WEAK_WEEKLY_CALL_STOCH_GE > 0 else ''})"
            )
        return kept

    def fast_load_signals(version_id: int, min_score: float, from_date=None, to_date=None, flagged_only: bool = False):
        rows = _raw_signal_rows(
            version_id=version_id,
            threshold=min_score,
            from_date=from_date,
            to_date=to_date,
            side="call",
            flagged_only=flagged_only,
        )
        sigs = [CallSignal(*row) for row in rows]
        if getattr(cascade_module, "CTSL_ENABLED", False):
            sigs = cascade_module._apply_ctsl_to_signals(sigs, "call")
            sigs.sort(key=lambda sig: (sig.date, -int(sig.overall), sig.symbol))
        return _apply_weak_weekly_call_drop(sigs)

    def fast_load_put_signals(
        version_id: int,
        max_put_score: float = None,
        from_date=None,
        to_date=None,
        flagged_only: bool = False,
    ):
        threshold = max_put_score if max_put_score is not None else cascade_module.PUT_THRESHOLD
        rows = _raw_signal_rows(
            version_id=version_id,
            threshold=threshold,
            from_date=from_date,
            to_date=to_date,
            side="put",
            flagged_only=flagged_only,
        )
        sigs = [PutSignal(*row) for row in rows]
        if getattr(cascade_module, "CTSL_ENABLED", False):
            sigs = cascade_module._apply_ctsl_to_signals(sigs, "put")
            sigs.sort(key=lambda sig: (sig.date, int(sig.overall), sig.symbol))
        if getattr(cascade_module, "EARN_SUPP_PUT", False) and sigs:
            ensure_db_open(force_reconnect=True)
            sigs = cascade_module._earnings_suppress_puts(sigs)
        return sigs

    cascade_module.load_signals = fast_load_signals
    cascade_module.load_put_signals = fast_load_put_signals
    cascade_module._research_pack_fast_signal_loaders = True


def run_portfolio_window(
    version,
    window: PortfolioWindow,
    dte: str = DEFAULT_DTE,
    portfolio_profile: str = DEFAULT_PORTFOLIO_PROFILE,
) -> dict[str, Any]:
    start = date.fromisoformat(window.start)
    end = date.fromisoformat(window.end) if window.end else None
    import portfolio_profiles

    cfg, profile = portfolio_profiles.apply_profile_to_config(
        {}, portfolio_profile, root=manager.project_root()
    )
    if str(dte) == "15":
        import backtest_cascade_15dte as cascade_module
    else:
        import backtest_cascade as cascade_module
    _install_fast_signal_loaders(cascade_module)
    run_cascade_backtest = cascade_module.run_cascade_backtest

    from database.trader_database import DB

    DB.connect_params["read_timeout"] = max(
        int(DB.connect_params.get("read_timeout") or 0),
        PORTFOLIO_WINDOW_DB_TIMEOUT_SECONDS,
    )
    DB.connect_params["write_timeout"] = max(
        int(DB.connect_params.get("write_timeout") or 0),
        PORTFOLIO_WINDOW_DB_TIMEOUT_SECONDS,
    )
    ensure_db_open(force_reconnect=True)
    cache_key = (str(dte), int(version.id), window.start, window.end)
    cached_base = _PORTFOLIO_WINDOW_BASE_CACHE.get(cache_key)
    if cached_base and not cfg.get("realloc_strategy"):
        # The RXDD VIX map MUST be threaded into the cached path too. The cache key
        # is profile-independent (outcomes/regime are the same across profiles), so
        # every profile after the first (cache-miss) one lands here — and omitting
        # vix_dates/vix_map silently runs RXDD-inert (vix=None), understating return
        # and overstating DD for Core/Apex. 15 DTE has no RXDD, so skip the kwargs.
        _bt_extra = {}
        if str(dte) != "15":
            _bt_extra["vix_dates"] = cached_base.get("vix_dates", [])
            _bt_extra["vix_map"] = cached_base.get("vix_map", {})
        result = cascade_module.run_backtest(
            cached_base["outcomes_by_date"],
            cached_base["all_dates"],
            INITIAL_PORTFOLIO_CAPITAL,
            regime_dates=cached_base["regime_dates"],
            regime_map=cached_base["regime_map"],
            cfg=cfg,
            **_bt_extra,
        )
        result["start_date"] = cached_base["start_date"]
        result["end_date"] = cached_base["end_date"]
        result["min_score"] = 70.0
        result["outcomes_by_date"] = cached_base["outcomes_by_date"]
        result["all_dates"] = cached_base["all_dates"]
        result["regime_dates"] = cached_base["regime_dates"]
        result["regime_map"] = cached_base["regime_map"]
    else:
        result = run_cascade_backtest(
            int(version.id),
            min_score=70.0,
            from_date=start,
            to_date=end,
            initial=INITIAL_PORTFOLIO_CAPITAL,
            verbose=False,
            cfg=cfg,
        )
        if result and not cfg.get("realloc_strategy"):
            _PORTFOLIO_WINDOW_BASE_CACHE[cache_key] = {
                "start_date": result.get("start_date"),
                "end_date": result.get("end_date"),
                "outcomes_by_date": result.get("outcomes_by_date") or {},
                "all_dates": result.get("all_dates") or [],
                "regime_dates": result.get("regime_dates") or [],
                "regime_map": result.get("regime_map") or {},
                "vix_dates": result.get("vix_dates") or [],
                "vix_map": result.get("vix_map") or {},
            }
    if not result:
        return {"window": asdict(window), "complete": False, "error": "No qualifying signals found."}
    return {
        "complete": True,
        "portfolio_profile": profile.get("key"),
        "portfolio_profile_name": profile.get("name"),
        "portfolio_profile_version": profile.get("version"),
        **summarize_portfolio_result(window, result),
    }


def portfolio_windows_payload(
    version,
    coverage: Mapping[str, Any],
    *,
    dte: str = DEFAULT_DTE,
    portfolio_profile: str = DEFAULT_PORTFOLIO_PROFILE,
    version_info: Mapping[str, Any] | None = None,
    run: bool = False,
    windows: Iterable[PortfolioWindow] = DEFAULT_PORTFOLIO_WINDOWS,
) -> dict[str, Any]:
    import portfolio_profiles

    root = manager.project_root()
    profile_key = portfolio_profiles.normalize_profile_key(portfolio_profile, root)
    profile = portfolio_profiles.get_profile(profile_key, root)
    version_info = dict(version_info or version_payload(version))
    windows = tuple(windows)
    readiness = portfolio_window_readiness(coverage, windows)
    rows = []
    for window in windows:
        row = {
            "window": asdict(window),
            "ready": readiness[window.name]["ready"],
            "readiness": readiness[window.name],
            "metrics": None,
        }
        if run:
            if not row["ready"]:
                row["metrics"] = {"complete": False, "skipped_reason": "; ".join(readiness[window.name]["missing"])}
            else:
                try:
                    row["metrics"] = run_portfolio_window(
                        version,
                        window,
                        dte=dte,
                        portfolio_profile=profile_key,
                    )
                except Exception as exc:
                    ensure_db_open(force_reconnect=True)
                    row["metrics"] = {"complete": False, "error": str(exc)}
        rows.append(row)
    return {
        "version": version_info,
        "dte_strategy": str(dte),
        "portfolio_profile": profile_key,
        "portfolio_profile_name": profile.get("name"),
        "portfolio_profile_version": profile.get("version"),
        "run_requested": bool(run),
        "windows": rows,
    }


def parquet_manifest(root: Path, version, version_info: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cache = root / ".cache"
    needles = {f"v{int(version.id)}", f"_v{int(version.id)}_"}
    files = []
    if cache.exists():
        for path in cache.glob("*/*.parquet"):
            name = path.name
            if any(needle in name for needle in needles):
                files.append(file_payload(path, root))
        version_cache = cache / "algorithm_versions" / f"v{int(version.id)}"
        if version_cache.exists():
            for path in version_cache.rglob("*.parquet"):
                files.append(file_payload(path, root))
    files = sorted({row["path"]: row for row in files}.values(), key=lambda row: row["path"])
    return {"version": dict(version_info or version_payload(version)), "files": files, "count": len(files)}


def file_payload(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    return {
        "path": rel,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def default_pack_dir(root: Path, version) -> Path:
    return manager.cache_path_for(int(version.id), root) / "research_pack"


def _portfolio_window_row_name(row: Mapping[str, Any]) -> str | None:
    window = row.get("window")
    if isinstance(window, Mapping):
        name = window.get("name")
        return str(name) if name else None
    name = row.get("name")
    return str(name) if name else None


def merge_portfolio_windows_payload(
    existing: Mapping[str, Any] | None,
    partial: Mapping[str, Any],
    canonical_windows: Iterable[PortfolioWindow] = DEFAULT_PORTFOLIO_WINDOWS,
) -> dict[str, Any]:
    """Merge a partial window rebuild without dropping previously built rows."""
    if not isinstance(existing, Mapping):
        return dict(partial)

    merged = dict(existing)
    merged.update({key: value for key, value in partial.items() if key != "windows"})

    rows_by_name: dict[str, Any] = {}
    for row in existing.get("windows") or []:
        if isinstance(row, Mapping):
            name = _portfolio_window_row_name(row)
            if name:
                rows_by_name[name] = row
    for row in partial.get("windows") or []:
        if isinstance(row, Mapping):
            name = _portfolio_window_row_name(row)
            if name:
                rows_by_name[name] = row

    ordered = []
    for window in canonical_windows:
        row = rows_by_name.pop(window.name, None)
        if row is not None:
            ordered.append(row)
    ordered.extend(rows_by_name.values())
    merged["windows"] = ordered
    return merged


# ---------------------------------------------------------------------------
# P1.8 -- component-skill drift panel (READ-ONLY diagnostic; gameplan section
# 5 P1.8, alpha-frontier table). Factored from
# experiments/weather_components/verify_components.py Section 2 ("MEMBER
# SKILL"): per-component (TREND/BB/RSI/MACD/STOCH/TA) univariate apex-EV skill
# vs the climatology (random-call) baseline. CHARTER: diagnostic-only. Any
# Score.overall re-weighting/reweighting/normalization driven by this signal
# is a CLOSED axis (gameplan.md section 4 "Any Score.overall re-shaping ...
# DEAD") -- this function must never be called from, or feed, any scoring
# path; it exists solely to give the Dec-2026 OOS read + future agents a
# component-lens on skill drift (TREND dominant-but-unskillful, MACD/RSI
# skillful, per experiments/weather_components/FINDINGS.md).
# ---------------------------------------------------------------------------
COMPONENT_SCORECARD_COMPS = ("trend", "bb", "rsi", "macd", "stoch", "ta")
COMPONENT_SCORECARD_EVMAP = {"win": 0.30, "stop": -0.70, "expire": -0.40}
COMPONENT_SCORECARD_PERIOD = "30d"
COMPONENT_SCORECARD_BARRIER_SET = "30dte_apex"
COMPONENT_SCORECARD_SAMPLE_N = 300_000
COMPONENT_SCORECARD_START_YEAR = 2016


def components_scorecard_payload(version, sample_n: int = COMPONENT_SCORECARD_SAMPLE_N) -> dict[str, Any]:
    """Per-component univariate apex-EV skill vs climatology (READ-ONLY
    diagnostic; see module-level comment above for charter + provenance).

    Reads the bulk_cache ledger built by
    experiments/weather_components/build_ledger.py (one row per stored
    stock-day: overall + the 6 ensemble members) for THIS version's id, joins
    the 30dte_apex CALL barrier outcome via database.barrier_cache (read-only
    cache lookup), and reports N / WR / EV / dEV-vs-climatology / t-stat per
    component on a fixed 300k-row seeded sample (matching
    verify_components.py's own SAMPLE_N default) for reproducibility.

    Never raises: the ledger is only built for whichever version(s)
    experiments/weather_components/build_ledger.py was run against, so a pack
    build for a version without a cached ledger (or any other failure)
    degrades to {"available": False, "reason": ...} rather than blocking the
    rest of the research pack.
    """
    try:
        import numpy as np
        import polars as pl
        from database.bulk_cache import materialize_polars
        from database.barrier_cache import peaks_to_swing_results

        vid = int(version.id)
        cache_key = f"weather_comp_v{vid}_{COMPONENT_SCORECARD_START_YEAR}"
        try:
            parquet_path = materialize_polars(cache_key, None)
        except Exception as exc:
            return {
                "schema_version": 1,
                "available": False,
                "reason": (
                    f"no cached component ledger for v{vid} (cache_key={cache_key}): {exc}. "
                    f"Build via experiments/weather_components/build_ledger.py (queued, MySQL) "
                    f"to populate this diagnostic for this version."
                ),
            }
        full = pl.read_parquet(parquet_path)
        if full.height == 0:
            return {"schema_version": 1, "available": False, "reason": "cached ledger is empty"}

        uni = full.sample(n=min(sample_n, full.height), seed=17) if full.height > sample_n else full

        def _apex_result_map(df):
            peaks = [
                namedtuple("Peak", "symbol_id date overall")(s, date.fromisoformat(d), 75)
                for s, d in zip(df["symbol"], df["date"])
            ]
            results, skipped = peaks_to_swing_results(
                peaks, verbose=False, barrier_set=COMPONENT_SCORECARD_BARRIER_SET
            )
            m = {}
            for r in results:
                dk = r["date"]
                dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
                sw = r.get("swing", {}).get(COMPONENT_SCORECARD_PERIOD)
                if sw and sw.get("result") in COMPONENT_SCORECARD_EVMAP:
                    m[(r["symbol"], dk)] = sw["result"]
            return m, skipped

        def _attach_apex(df):
            m, skipped = _apex_result_map(df)
            res = [m.get((s, d)) for s, d in zip(df["symbol"], df["date"])]
            ev = [COMPONENT_SCORECARD_EVMAP[r] if r is not None else None for r in res]
            win = [(1 if r == "win" else 0) if r is not None else None for r in res]
            out = df.with_columns(
                apex_res=pl.Series(res, dtype=pl.Utf8),
                apex_ev=pl.Series(ev, dtype=pl.Float64),
                apex_win=pl.Series(win, dtype=pl.Int64),
            ).filter(pl.col("apex_res").is_not_null())
            return out, skipped

        def _grp(df, mask):
            sub = df.filter(mask)
            if sub.height == 0:
                return 0, float("nan"), float("nan"), np.array([])
            ev = sub["apex_ev"].to_numpy()
            wr = float(sub["apex_win"].mean()) * 100.0
            return sub.height, wr, float(ev.mean()) * 100.0, ev

        def _mean_diff_t(a, b):
            a, b = np.asarray(a, float), np.asarray(b, float)
            if len(a) < 2 or len(b) < 2:
                return float("nan")
            se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
            return float((a.mean() - b.mean()) / se) if se > 0 else float("nan")

        uni_attached, skipped_uni = _attach_apex(uni)
        if uni_attached.height == 0:
            return {
                "schema_version": 1,
                "available": False,
                "reason": "no apex-barrier-matched rows in the sampled universe",
            }

        cn, cwr, cev, cevarr = _grp(uni_attached, pl.lit(True))

        members = []
        for c in list(COMPONENT_SCORECARD_COMPS) + ["overall"]:
            if c not in uni_attached.columns:
                members.append({"component": c, "available": False})
                continue
            n, wr, ev, evarr = _grp(uni_attached, pl.col(c) >= 70)
            t = _mean_diff_t(evarr, cevarr)
            d_ev = ev - cev if (ev == ev and cev == cev) else float("nan")
            members.append({
                "component": c,
                "available": True,
                "n_ge70": n,
                "wr_pct": round(wr, 3) if wr == wr else None,
                "apex_ev_pct": round(ev, 4) if ev == ev else None,
                "delta_ev_vs_climatology_pct": round(d_ev, 4) if d_ev == d_ev else None,
                "t_stat": round(t, 3) if t == t else None,
                "skillful": bool(t == t and t >= 2.0 and d_ev == d_ev and d_ev > 0),
            })

        return {
            "schema_version": 1,
            "available": True,
            "source": "factored from experiments/weather_components/verify_components.py Section 2 (MEMBER SKILL)",
            "charter": (
                "READ-ONLY diagnostic. Score.overall re-weighting/reweighting driven by this "
                "signal is a CLOSED axis (gameplan.md section 4 alpha-frontier table) -- "
                "never wire into scoring."
            ),
            "version_id": vid,
            "cache_key": cache_key,
            "barrier_set": COMPONENT_SCORECARD_BARRIER_SET,
            "period": COMPONENT_SCORECARD_PERIOD,
            "ev_map": COMPONENT_SCORECARD_EVMAP,
            "universe_rows_total": int(full.height),
            "universe_rows_sampled": int(uni.height),
            "universe_apex_matched": int(uni_attached.height),
            "universe_apex_unmatched_skipped": int(skipped_uni),
            "climatology": {
                "n": cn,
                "wr_pct": round(cwr, 3) if cwr == cwr else None,
                "apex_ev_pct": round(cev, 4) if cev == cev else None,
            },
            "members": members,
            "generated_at": utc_now(),
        }
    except Exception as exc:
        return {
            "schema_version": 1,
            "available": False,
            "reason": f"components_scorecard_payload failed: {type(exc).__name__}: {exc}",
        }


def build_research_pack(
    *,
    version_token: str | None = None,
    out_dir: Path | None = None,
    lookbacks: Iterable[int] = DEFAULT_LOOKBACKS,
    periods: Iterable[str] = DEFAULT_PERIODS,
    dte: str = DEFAULT_DTE,
    portfolio_profiles: Iterable[str] | str | None = None,
    portfolio_windows: Iterable[str] | str | None = None,
    run_portfolio_windows: bool = False,
    params: UtilityParams = DEFAULT_PARAMS,
) -> dict[str, Any]:
    ensure_db_open()
    root = manager.project_root()
    version = resolve_version(version_token)
    version_info = version_payload(version)
    lookbacks = [int(v) for v in lookbacks]
    periods = [str(v) for v in periods]
    target_dir = out_dir or default_pack_dir(root, version)
    target_dir.mkdir(parents=True, exist_ok=True)
    profile_keys = parse_portfolio_profiles(portfolio_profiles)
    window_specs = parse_portfolio_windows(portfolio_windows)
    partial_window_build = tuple(window.name for window in window_specs) != tuple(
        window.name for window in DEFAULT_PORTFOLIO_WINDOWS
    )

    outputs: dict[str, str] = {}

    coverage = score_coverage_by_year(version)
    coverage["requested_lookback_readiness"] = score_lookback_readiness(coverage, lookbacks)
    coverage["portfolio_window_readiness"] = portfolio_window_readiness(coverage, window_specs)
    coverage_path = target_dir / "score_coverage.json"
    write_json(coverage_path, coverage)
    outputs["score_coverage"] = str(coverage_path)

    assessment_coverage = assessment_runs_payload(version, lookbacks, dte=dte)
    assessment_path = target_dir / "assessment_coverage.json"
    write_json(assessment_path, assessment_coverage)
    outputs["assessment_coverage"] = str(assessment_path)

    utility_5y_wr15 = compute_utility_for_version(
        version,
        lookback=1825,
        period=PRIMARY_UTILITY_PERIOD,
        dte=dte,
        params=params,
    )
    utility_5y_path = target_dir / "utility_5y_wr15.json"
    write_json(utility_5y_path, utility_5y_wr15)
    outputs["utility_5y_wr15"] = str(utility_5y_path)

    # Keep the old headline artifact for older dashboards/scripts while agents
    # and new UI use WR15 as the primary comparison surface.
    utility_5y_wr7 = compute_utility_for_version(
        version,
        lookback=1825,
        period=LEGACY_UTILITY_PERIOD,
        dte=dte,
        params=params,
    )
    utility_5y_legacy_path = target_dir / "utility_5y_wr7.json"
    write_json(utility_5y_legacy_path, utility_5y_wr7)
    outputs["utility_5y_wr7"] = str(utility_5y_legacy_path)

    utility_grid = utility_grid_for_version(version, lookbacks=lookbacks, periods=periods, dte=dte, params=params)
    utility_grid_path = target_dir / "utility_by_horizon.json"
    write_json(utility_grid_path, utility_grid)
    outputs["utility_by_horizon"] = str(utility_grid_path)

    temporal = temporal_stats_payload(version)
    temporal_path = target_dir / "temporal_summary.json"
    write_json(temporal_path, temporal)
    outputs["temporal_summary"] = str(temporal_path)

    stress_windows_by_profile: dict[str, str] = {}
    stress_windows_complete_by_profile: dict[str, bool] = {}
    stress_windows_payloads: dict[str, dict[str, Any]] = {}
    legacy_stress_windows_path = target_dir / "stress_windows.json"
    for profile_key in profile_keys:
        stress_windows = portfolio_windows_payload(
            version,
            coverage,
            dte=dte,
            portfolio_profile=profile_key,
            version_info=version_info,
            run=run_portfolio_windows,
            windows=window_specs,
        )
        stress_windows_path = target_dir / f"stress_windows_{profile_key}.json"
        if partial_window_build and stress_windows_path.exists():
            try:
                existing_stress_windows = json.loads(stress_windows_path.read_text(encoding="utf-8"))
            except Exception:
                existing_stress_windows = None
            stress_windows = merge_portfolio_windows_payload(existing_stress_windows, stress_windows)
        write_json(stress_windows_path, stress_windows)
        stress_windows_payloads[profile_key] = stress_windows
        stress_windows_by_profile[profile_key] = str(stress_windows_path)
        stress_windows_complete_by_profile[profile_key] = all(
            bool((row.get("metrics") or {}).get("complete"))
            for row in stress_windows.get("windows") or []
            if row.get("ready")
        )
        if profile_key == DEFAULT_PORTFOLIO_PROFILE:
            write_json(legacy_stress_windows_path, stress_windows)
            outputs["stress_windows"] = str(legacy_stress_windows_path)
    if "stress_windows" not in outputs and profile_keys:
        first_profile = profile_keys[0]
        write_json(legacy_stress_windows_path, stress_windows_payloads[first_profile])
        outputs["stress_windows"] = str(legacy_stress_windows_path)
    outputs["stress_windows_by_profile"] = stress_windows_by_profile

    components_scorecard = components_scorecard_payload(version)
    components_scorecard_path = target_dir / "components_scorecard.json"
    write_json(components_scorecard_path, components_scorecard)
    outputs["components_scorecard"] = str(components_scorecard_path)

    parquets = parquet_manifest(root, version, version_info=version_info)
    parquet_path = target_dir / "parquet_manifest.json"
    write_json(parquet_path, parquets)
    outputs["parquet_manifest"] = str(parquet_path)

    manifest = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "root": str(root),
        "version": version_info,
        "dte_strategy": str(dte),
        "portfolio_profiles": profile_keys,
        "portfolio_windows": [window.name for window in window_specs],
        "lookbacks": lookbacks,
        "periods": periods,
        "utility_params": asdict(params),
        "git_state": git_state(root),
        "outputs": outputs,
        "health": {
            "score_rows": coverage["total_score_rows"],
            "score_date_min": coverage["min_date"],
            "score_date_max": coverage["max_date"],
            "score_lookback_readiness": coverage["requested_lookback_readiness"],
            "portfolio_window_readiness": coverage["portfolio_window_readiness"],
            "portfolio_windows_run": bool(run_portfolio_windows),
            "portfolio_windows_profiles": profile_keys,
            "portfolio_windows_complete_by_profile": stress_windows_complete_by_profile,
            "assessment_complete": assessment_coverage["complete"],
            "utility_5y_wr15_complete": utility_5y_wr15["complete"],
            "utility_5y_wr7_complete": utility_5y_wr7["complete"],
            "temporal_complete": temporal["complete"],
            "parquet_count": parquets["count"],
            "components_scorecard_available": bool(components_scorecard.get("available")),
        },
    }
    manifest_path = target_dir / "manifest.json"
    write_json(manifest_path, manifest)
    outputs["manifest"] = str(manifest_path)
    manifest["outputs"] = outputs
    write_json(manifest_path, manifest)
    return manifest


def _run_comparability_unit(version_token: str) -> dict:
    """Build the remaining two parts of the deploy.md post-recalc comparability
    unit so a ship can never elevate a pack-only (un-gateable / VersionCompare-blank)
    version again — the gap that left v74 (and the whole v70-v72 lineage before it)
    missing its supply + PRF cells:

      (2) per-version supply/hydration row -> experiments/version_scorecard/signal_supply.py
          (merges into the shared supply_burstiness.json; keeps other versions)
      (3) PRF matched-sizing instrument    -> experiments/version_scorecard/portfolio_response.py --materialize
          (writes derived_portfolio.json into this version's pack)

    Best-effort: a failure warns but does NOT fail the pack build. PRF depends on
    the supply row (it refuses while supply is approximated), so order is fixed and
    PRF is skipped if supply fails.
    """
    import os
    import subprocess
    import sys as _sys

    proj_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    steps = [
        ("supply", [_sys.executable, "experiments/version_scorecard/signal_supply.py",
                    "--versions", version_token]),
        ("prf", [_sys.executable, "experiments/version_scorecard/portfolio_response.py",
                 "--materialize", version_token]),
    ]
    status: dict[str, str] = {}
    for name, cmd in steps:
        print(f"[comparability] {name}: {' '.join(cmd)}", flush=True)
        try:
            subprocess.run(cmd, cwd=str(proj_root), env=env, check=True)
            status[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            status[name] = f"FAILED: {exc}"
            print(f"[comparability] {name} FAILED: {exc}", flush=True)
            break  # PRF depends on supply
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a version-scoped post-recalc research pack.")
    parser.add_argument("--version", default=None, help="Algorithm version token, e.g. v58. Defaults to active version.")
    parser.add_argument("--out-dir", default=None, help="Override output directory. Defaults under .cache/algorithm_versions/vNN/research_pack.")
    parser.add_argument("--lookbacks", default=None, help="Comma-separated lookback days. Default: 365,730,1095,1825,3650.")
    parser.add_argument("--periods", default=None, help="Comma-separated periods. Default: 3d,5d,7d,15d,30d,60d,90d.")
    parser.add_argument("--dte", default=DEFAULT_DTE, help="DTE assessment strategy. Default: 30.")
    parser.add_argument("--profiles", default="all", help="Portfolio profiles for stress windows: all or comma-separated keys. Default: all — every profile, so the version appears under EVERY VersionCompare profile toggle (the v74 gap: a sentinel-only pack is filtered out of the apex/core views). Bulk backfill passes explicit profiles.")
    parser.add_argument("--portfolio-windows", default=None, help="Comma-separated portfolio window names to build. Default: all.")
    parser.add_argument("--run-portfolio-windows", action="store_true", help="Run deterministic backtests for named stress/compound windows.")
    parser.add_argument("--comparability", dest="comparability", action="store_true", default=True,
                        help="After the pack, also build the deploy.md comparability unit: the supply/"
                             "hydration row (signal_supply.py) + PRF matched-sizing "
                             "(portfolio_response.py --materialize). Default ON so a ship cannot overlook it.")
    parser.add_argument("--no-comparability", dest="comparability", action="store_false",
                        help="Skip the supply-row + PRF tail (use for bulk backfills of many versions).")
    args = parser.parse_args(argv)

    manifest = build_research_pack(
        version_token=args.version,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        lookbacks=parse_int_csv(args.lookbacks, DEFAULT_LOOKBACKS),
        periods=parse_csv(args.periods, DEFAULT_PERIODS),
        dte=args.dte,
        portfolio_profiles=args.profiles,
        portfolio_windows=args.portfolio_windows,
        run_portfolio_windows=args.run_portfolio_windows,
    )
    print(json.dumps(json_safe(manifest["health"]), indent=2, sort_keys=True))
    print(f"manifest={manifest['outputs']['manifest']}")

    # Comparability unit (deploy.md): pack alone is NOT comparable/gateable —
    # the supply/hydration row + PRF matched-sizing complete it. Default ON so a
    # ship can't ship a pack-only version (the v74 / v70-v72 VersionCompare-blank gap).
    if getattr(args, "comparability", True):
        token = f"v{int(manifest['version']['id'])}"
        status = _run_comparability_unit(token)
        complete = status.get("supply") == "ok" and status.get("prf") == "ok"
        print(f"comparability_unit={'COMPLETE' if complete else 'INCOMPLETE'} "
              f"(pack=ok supply={status.get('supply', '-')} prf={status.get('prf', '-')})", flush=True)
        if not complete:
            print(f"  ^ INCOMPLETE: rerun `signal_supply.py --versions {token}` then "
                  f"`portfolio_response.py --materialize {token}` to finish the unit.", flush=True)
    else:
        print("comparability_unit=SKIPPED (--no-comparability) — pack only; "
              "VersionCompare supply/PRF cells will be blank until built separately.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
