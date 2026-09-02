"""Combined Stage 3 portfolio-candidate Monte Carlo validation.

Experiment-only. This runner combines the strongest v60 daily-opportunity
findings without changing scores or writing database rows:

- practical exposure saturation around the g80/g85 families;
- acute-crash CT-call promotion suppression;
- smooth put-wave reserve accounting.

The runner is durable-run friendly: pass a timestamped --out-dir, redirect
stdout to run.log, and it will maintain status.json, run.recent.log, CSV
outputs, findings.md, done.json, or failed.json.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_versions import manager
from database.trader_database import DB
from experiments.daily_opportunity_allocation.validate_ct_crash_suppressor_mc import (
    DEFAULT_WAVE,
    CtPolicy,
    _matches_ct_crash,
    _mutate_signal,
    _read_daily_rows,
)
from experiments.daily_opportunity_allocation.validate_production_reserve_mc import (
    CANDIDATE_ENVS as RESERVE_ENVS,
    DEFAULT_DAILY,
)
from experiments.daily_opportunity_allocation.validate_score_dampen_proxy_mc import _read_wave

try:
    # This is an experiment runner, often running beside other long research
    # jobs. Keep production defaults untouched, but avoid losing multi-hour MC
    # screens when a read-only signal query stalls under temporary DB pressure.
    DB._connect_params["read_timeout"] = max(180, int(DB._connect_params.get("read_timeout", 30)))  # type: ignore[attr-defined]
    DB._connect_params["write_timeout"] = max(180, int(DB._connect_params.get("write_timeout", 30)))  # type: ignore[attr-defined]
except Exception:
    pass


WINDOWS: dict[str, tuple[date, date]] = {
    "2020_crash": (date(2020, 2, 18), date(2020, 4, 30)),
    "covid_peak": (date(2020, 3, 1), date(2020, 3, 31)),
    "2020_full": (date(2020, 1, 1), date(2020, 12, 31)),
    "2022": (date(2022, 1, 1), date(2022, 12, 31)),
    "2024": (date(2024, 1, 1), date(2024, 12, 31)),
    "2025": (date(2025, 1, 1), date(2025, 12, 31)),
    "2025_dip": (date(2025, 11, 1), date(2026, 4, 24)),
    "2026_resolved": (date(2026, 1, 1), date(2026, 4, 24)),
    "2022_now": (date(2022, 1, 1), date(2026, 4, 24)),
    "2020_now": (date(2020, 1, 1), date(2026, 4, 24)),
    "5y": (date(2021, 1, 1), date(2026, 4, 15)),
}

FROZEN_V60_BASE_ENV = {
    "DD_SOFT_BAND_LO": "0.35",
    "DD_SOFT_BAND_HI": "0.55",
    "DD_SOFT_CALL_FLOOR": "0.40",
    "MC_NO_DB_PERSIST": "1",
    "REALLOC_STRATEGY": "",
    "MC_TRADE_TAPE": "0",
}

PRACTICAL_ENV_KEYS = (
    "PRACTICAL_CAPITAL_CEILING",
    "GROSS_PREMIUM_CAP",
    "CALL_PREMIUM_CAP",
    "PUT_PREMIUM_CAP",
    "OPP_SAT_CALL_REF",
    "OPP_SAT_PUT_REF",
    "OPP_SAT_POWER",
    "OPP_SAT_FLOOR",
    "MAX_POSITIONS_OVERRIDE",
    "MAX_POSITIONS_CALL",
    "MAX_POSITIONS_PUT",
)

RESERVE_ENV_KEYS = (
    "PUT_WAVE_RESERVE_ENABLED",
    "PUT_WAVE_RESERVE_DAILY_STATE_CSV",
    *tuple(RESERVE_ENVS["reserve_0124"].keys()),
)

RECENT_LINES: list[str] = []
RUN_DIR: Path
STARTED_AT: str
_RUN_STDOUT = None
_RUN_STDERR = None


@dataclass(frozen=True)
class ComboCandidate:
    name: str
    capital_ceiling: float = 0.0
    gross_cap: float = 0.0
    call_cap: float = 0.0
    put_cap: float = 0.0
    call_ref: float = 0.0
    put_ref: float = 0.0
    sat_power: float = 1.0
    sat_floor: float = 0.0
    max_positions: int = 14
    call_max: int | None = 12
    put_max: int | None = None
    reserve: str = ""
    ct_suppress: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def output_paths() -> dict[str, str]:
    return {
        "run_log": str(RUN_DIR / "run.log"),
        "run_recent_log": str(RUN_DIR / "run.recent.log"),
        "status_json": str(RUN_DIR / "status.json"),
        "phase1_results": str(RUN_DIR / "phase1_results.csv"),
        "phase1_summary": str(RUN_DIR / "phase1_summary.csv"),
        "phase2_results": str(RUN_DIR / "phase2_results.csv"),
        "phase2_summary": str(RUN_DIR / "phase2_summary.csv"),
        "phase3_results": str(RUN_DIR / "phase3_results.csv"),
        "phase3_summary": str(RUN_DIR / "phase3_summary.csv"),
        "findings": str(RUN_DIR / "findings.md"),
        "done_json": str(RUN_DIR / "done.json"),
        "failed_json": str(RUN_DIR / "failed.json"),
    }


def ensure_std_streams() -> None:
    global _RUN_STDOUT, _RUN_STDERR
    try:
        sys.stdout.write("")
        sys.stdout.flush()
        stdout_bad = getattr(sys.stdout, "closed", False)
    except Exception:
        stdout_bad = True
    try:
        sys.stderr.write("")
        sys.stderr.flush()
        stderr_bad = getattr(sys.stderr, "closed", False)
    except Exception:
        stderr_bad = True
    if stdout_bad:
        _RUN_STDOUT = (RUN_DIR / "run.stdout.fallback.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = _RUN_STDOUT
    if stderr_bad:
        _RUN_STDERR = (RUN_DIR / "run.err.log").open("a", encoding="utf-8", buffering=1)
        sys.stderr = _RUN_STDERR


def note(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    try:
        with (RUN_DIR / "run.log").open("a", encoding="utf-8", buffering=1) as fh:
            fh.write(line + "\n")
    except Exception:
        ensure_std_streams()
    try:
        ensure_std_streams()
        print(line, flush=True)
    except Exception:
        pass
    RECENT_LINES.append(line)
    del RECENT_LINES[:-80]
    (RUN_DIR / "run.recent.log").write_text("\n".join(reversed(RECENT_LINES)) + "\n", encoding="utf-8")


def write_status(**kwargs: Any) -> None:
    payload = {
        "started_at": STARTED_AT,
        "updated_at": utc_now(),
        "run_dir": str(RUN_DIR),
        "root": str(ROOT),
        "outputs": output_paths(),
    }
    payload.update(kwargs)
    atomic_json(RUN_DIR / "status.json", payload)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def parse_csv_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def resolve_versions(tokens: list[str]) -> list[Any]:
    DB.connect(reuse_if_open=True)
    return [manager.resolve_algorithm_version(token, create_current=False) for token in tokens]


def finite_log_final(final_value: float, wealth_cap: float) -> float:
    capped = min(max(float(final_value), 1.0), wealth_cap)
    return math.log10(capped / 50_000.0)


def candidate_pool() -> list[ComboCandidate]:
    g80 = dict(
        capital_ceiling=25_000_000,
        gross_cap=0.80,
        call_cap=0.65,
        put_cap=0.25,
        call_ref=16,
        put_ref=4,
        sat_power=0.50,
        sat_floor=0.55,
        max_positions=14,
        call_max=12,
        put_max=8,
    )
    g85_call_preserve = dict(
        capital_ceiling=25_000_000,
        gross_cap=0.85,
        call_cap=0.70,
        put_cap=0.22,
        call_ref=18,
        put_ref=4,
        sat_power=0.50,
        sat_floor=0.58,
        max_positions=14,
        call_max=12,
        put_max=8,
    )
    g90_call_preserve = dict(
        capital_ceiling=25_000_000,
        gross_cap=0.90,
        call_cap=0.72,
        put_cap=0.20,
        call_ref=20,
        put_ref=4,
        sat_power=0.50,
        sat_floor=0.60,
        max_positions=14,
        call_max=12,
        put_max=8,
    )
    g80_put_thin = dict(
        capital_ceiling=25_000_000,
        gross_cap=0.80,
        call_cap=0.68,
        put_cap=0.20,
        call_ref=16,
        put_ref=3,
        sat_power=0.50,
        sat_floor=0.58,
        max_positions=14,
        call_max=12,
        put_max=7,
    )

    out = [
        ComboCandidate("baseline"),
        ComboCandidate("ct_crash_suppress", ct_suppress=True),
        ComboCandidate("reserve_0124", reserve="reserve_0124"),
        ComboCandidate("reserve_0124_ct", reserve="reserve_0124", ct_suppress=True),
        ComboCandidate("g80_c65_p25_ref16_4_pow05_floor55_25m", **g80),
        ComboCandidate("g80_c65_p25_ref16_4_pow05_floor55_25m_ct", **g80, ct_suppress=True),
        ComboCandidate("g80_c65_p25_ref16_4_pow05_floor55_25m_reserve0124", **g80, reserve="reserve_0124"),
        ComboCandidate("g80_c65_p25_ref16_4_pow05_floor55_25m_reserve0124_ct", **g80, reserve="reserve_0124", ct_suppress=True),
        ComboCandidate("g85_c70_p22_ref18_4_pow05_floor58_25m", **g85_call_preserve),
        ComboCandidate("g85_c70_p22_ref18_4_pow05_floor58_25m_ct", **g85_call_preserve, ct_suppress=True),
        ComboCandidate("g85_c70_p22_ref18_4_pow05_floor58_25m_reserve0124_ct", **g85_call_preserve, reserve="reserve_0124", ct_suppress=True),
        ComboCandidate("g90_c72_p20_ref20_4_pow05_floor60_25m_ct", **g90_call_preserve, ct_suppress=True),
        ComboCandidate("g80_c68_p20_ref16_3_pow05_floor58_25m_ct", **g80_put_thin, ct_suppress=True),
    ]
    return out


def apply_candidate_env(candidate: ComboCandidate, n_iter: int, daily_state: Path, workers: int | None) -> None:
    for key in (*PRACTICAL_ENV_KEYS, *RESERVE_ENV_KEYS):
        os.environ.pop(key, None)
    os.environ.update(FROZEN_V60_BASE_ENV)
    os.environ["N_ITER_OVERRIDE"] = str(n_iter)
    if workers is not None:
        os.environ["MC_WORKERS"] = str(workers)
    os.environ["MAX_POSITIONS_OVERRIDE"] = str(candidate.max_positions)
    os.environ["MAX_POSITIONS_CALL"] = str(candidate.call_max) if candidate.call_max is not None else "none"
    os.environ["MAX_POSITIONS_PUT"] = str(candidate.put_max) if candidate.put_max is not None else "none"
    knobs = {
        "PRACTICAL_CAPITAL_CEILING": candidate.capital_ceiling,
        "GROSS_PREMIUM_CAP": candidate.gross_cap,
        "CALL_PREMIUM_CAP": candidate.call_cap,
        "PUT_PREMIUM_CAP": candidate.put_cap,
        "OPP_SAT_CALL_REF": candidate.call_ref,
        "OPP_SAT_PUT_REF": candidate.put_ref,
        "OPP_SAT_POWER": candidate.sat_power,
        "OPP_SAT_FLOOR": candidate.sat_floor,
    }
    for key, value in knobs.items():
        os.environ[key] = str(value)
    if candidate.reserve:
        os.environ["PUT_WAVE_RESERVE_ENABLED"] = "1"
        os.environ["PUT_WAVE_RESERVE_DAILY_STATE_CSV"] = str(daily_state)
        os.environ.update(RESERVE_ENVS[candidate.reserve])
    else:
        os.environ["PUT_WAVE_RESERVE_ENABLED"] = "0"


def reload_mc():
    import monte_carlo

    return importlib.reload(monte_carlo)


def refresh_db_connection() -> None:
    try:
        if not DB.is_closed():
            DB.close()
    except Exception:
        try:
            DB._state.reset()  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        DB.connect(reuse_if_open=False)
    except Exception:
        try:
            DB._state.reset()  # type: ignore[attr-defined]
        except Exception:
            pass
        DB.connect(reuse_if_open=False)
    try:
        cursor = DB.execute_sql("SELECT 1")
        cursor.close()
    except Exception:
        try:
            if not DB.is_closed():
                DB.close()
        except Exception:
            pass
        try:
            DB._state.reset()  # type: ignore[attr-defined]
        except Exception:
            pass
        raise


def make_ct_load_signals(
    original,
    candidate: ComboCandidate,
    wave: dict[date, float],
    daily: dict[date, dict[str, Any]],
    stats: dict[tuple[str, str, str], dict[str, int]],
    current: dict[str, str],
):
    policy = CtPolicy("ct_crash_suppress", action="suppress")

    def load_signals(version, d_start, d_end):
        sigs = list(original(version, d_start, d_end))
        if not candidate.ct_suppress:
            return sigs
        out = []
        touched = 0
        days: set[date] = set()
        for sig in sigs:
            if _matches_ct_crash(sig, policy, wave, daily):
                touched += 1
                days.add(sig.date)
                out.append(_mutate_signal(sig, policy))
            else:
                out.append(sig)
        key = (candidate.name, current.get("version", "unknown"), current.get("window", "unknown"))
        stats[key] = {"ct_suppressed_calls": touched, "ct_suppressed_days": len(days), "loaded_calls": len(sigs)}
        note(f"CT suppress {candidate.name}: touched {touched}/{len(sigs)} calls across {len(days)} days")
        return out

    return load_signals


def run_window_with_retry(mc, label: str, start: date, end: date, version: Any, attempts: int = 8) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            refresh_db_connection()
            return mc.run_window(label, start, end, version)["seeded"]
        except Exception as exc:
            last_exc = exc
            note(f"retry {label} attempt {attempt}/{attempts} failed: {type(exc).__name__}: {exc}")
            try:
                if not DB.is_closed():
                    DB.close()
            except Exception:
                pass
            try:
                DB._state.reset()  # type: ignore[attr-defined]
            except Exception:
                pass
            if attempt < attempts:
                time.sleep(20 * attempt)
    assert last_exc is not None
    raise last_exc


def run_phase(
    phase: str,
    candidates: list[ComboCandidate],
    versions: list[Any],
    windows: list[str],
    n_iter: int,
    out_csv: Path,
    summary_csv: Path,
    wealth_cap: float,
    wealth_floor: float,
    daily_state: Path,
    wave_predictions: Path,
    workers: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = read_csv(out_csv)
    completed_keys = {
        (r.get("phase"), r.get("candidate"), r.get("version"), r.get("window"))
        for r in rows
        if r.get("phase") == phase
    }
    wave = _read_wave(wave_predictions)
    daily = _read_daily_rows(wave_predictions)
    total = len(candidates) * len(versions) * len(windows)
    completed = len(completed_keys)
    if rows:
        note(f"{phase} resuming with {completed}/{total} completed cells from {out_csv.name}")
        write_csv(summary_csv, summarize(rows, wealth_cap, wealth_floor))
    stats: dict[tuple[str, str, str], dict[str, int]] = {}
    current: dict[str, str] = {}

    for candidate in candidates:
        apply_candidate_env(candidate, n_iter, daily_state, workers)
        mc = reload_mc()
        original_load_signals = mc.load_signals
        mc.load_signals = make_ct_load_signals(original_load_signals, candidate, wave, daily, stats, current)
        note(f"{phase} candidate {candidate.name} env={json.dumps(asdict(candidate), sort_keys=True)}")
        try:
            for version in versions:
                for window_name in windows:
                    cell_key = (phase, candidate.name, version.production_label, window_name)
                    if cell_key in completed_keys:
                        continue
                    start, end = WINDOWS[window_name]
                    current["version"] = version.production_label
                    current["window"] = window_name
                    write_status(
                        state="running",
                        phase=phase,
                        completed=completed,
                        total=total,
                        current_candidate=candidate.name,
                        current_version=version.production_label,
                        current_window=window_name,
                        n_iter=n_iter,
                    )
                    t0 = time.time()
                    result = run_window_with_retry(mc, window_name, start, end, version)
                    row = {
                        "phase": phase,
                        "candidate": candidate.name,
                        "version": version.production_label,
                        "version_id": int(version.id),
                        "git_commit": version.git_commit,
                        "window": window_name,
                        "n_iter": n_iter,
                        "elapsed_s": round(time.time() - t0, 2),
                        **asdict(candidate),
                    }
                    row.update(stats.get((candidate.name, version.production_label, window_name), {"ct_suppressed_calls": 0, "ct_suppressed_days": 0}))
                    for key in (
                        "mean_ret",
                        "med_ret",
                        "mean_dd",
                        "worst_dd",
                        "p_coll",
                        "call_tp",
                        "put_tp",
                        "call_trades",
                        "put_trades",
                        "mean_final",
                        "avg_open_premium_pct",
                        "max_open_premium_pct",
                        "avg_open_premium_base_pct",
                        "max_open_premium_base_pct",
                        "avg_call_open_premium_base_pct",
                        "avg_put_open_premium_base_pct",
                        "avg_saturation_scale",
                        "avg_reserved_cash_pct",
                        "max_reserved_cash_pct",
                    ):
                        row[key] = result.get(key)
                    rows.append(row)
                    completed_keys.add(cell_key)
                    completed += 1
                    write_csv(out_csv, rows)
                    summary = summarize(rows, wealth_cap, wealth_floor)
                    write_csv(summary_csv, summary)
                    note(
                        f"{phase} {candidate.name} {version.production_label} {window_name}: "
                        f"mean_final=${float(row.get('mean_final') or 0):,.0f} "
                        f"worstDD={float(row.get('worst_dd') or 0):.1f}% "
                        f"meanDD={float(row.get('mean_dd') or 0):.1f}% "
                        f"openPremBase={float(row.get('avg_open_premium_base_pct') or 0):.1f}% "
                        f"callBase={float(row.get('avg_call_open_premium_base_pct') or 0):.1f}% "
                        f"putBase={float(row.get('avg_put_open_premium_base_pct') or 0):.1f}%"
                    )
        finally:
            mc.load_signals = original_load_signals

    summary = summarize(rows, wealth_cap, wealth_floor)
    write_csv(out_csv, rows)
    write_csv(summary_csv, summary)
    return rows, summary


def summarize(rows: list[dict[str, Any]], wealth_cap: float, wealth_floor: float) -> list[dict[str, Any]]:
    by_key = {(r["version"], r["window"], r["candidate"]): r for r in rows}
    candidates = sorted({r["candidate"] for r in rows})
    summary: list[dict[str, Any]] = []
    for candidate in candidates:
        cand_rows = [r for r in rows if r["candidate"] == candidate]
        deltas = []
        worst_regression: dict[str, Any] | None = None
        covid_dd = None
        covid_peak_dd = None
        v60_2025_dip_worse = None
        for row in cand_rows:
            base = by_key.get((row["version"], row["window"], "baseline"))
            if not base:
                continue
            cand_final = float(row.get("mean_final") or 0.0)
            base_final = float(base.get("mean_final") or 0.0)
            dd_improve = float(base.get("worst_dd") or 0.0) - float(row.get("worst_dd") or 0.0)
            mean_dd_improve = float(base.get("mean_dd") or 0.0) - float(row.get("mean_dd") or 0.0)
            if dd_improve < 0 and (worst_regression is None or -dd_improve > worst_regression["worse_pp"]):
                worst_regression = {
                    "version": row["version"],
                    "window": row["window"],
                    "worse_pp": -dd_improve,
                    "base_worst_dd": float(base.get("worst_dd") or 0.0),
                    "candidate_worst_dd": float(row.get("worst_dd") or 0.0),
                }
            if row["version"] == "v60" and row["window"] == "2020_crash":
                covid_dd = dd_improve
            if row["version"] == "v60" and row["window"] == "covid_peak":
                covid_peak_dd = dd_improve
            if row["version"] == "v60" and row["window"] == "2025_dip":
                v60_2025_dip_worse = max(0.0, -dd_improve)
            deltas.append(
                {
                    "dd_improve": dd_improve,
                    "mean_dd_improve": mean_dd_improve,
                    "log_final_delta": finite_log_final(cand_final, wealth_cap) - finite_log_final(base_final, wealth_cap),
                    "floor_pass": 1.0 if cand_final >= wealth_floor else 0.0,
                    "million_floor": 1.0 if cand_final >= 1_000_000 else 0.0,
                    "open_prem_base": float(row.get("avg_open_premium_base_pct") or 0.0),
                    "call_open_base": float(row.get("avg_call_open_premium_base_pct") or 0.0),
                    "put_open_base": float(row.get("avg_put_open_premium_base_pct") or 0.0),
                    "sat": float(row.get("avg_saturation_scale") or 1.0),
                    "reserved": float(row.get("avg_reserved_cash_pct") or 0.0),
                    "ct_touched": float(row.get("ct_suppressed_calls") or 0.0),
                }
            )
        if not deltas:
            continue
        avg_dd = sum(d["dd_improve"] for d in deltas) / len(deltas)
        avg_mean_dd = sum(d["mean_dd_improve"] for d in deltas) / len(deltas)
        max_worse = max(max(0.0, -d["dd_improve"]) for d in deltas)
        avg_log = sum(d["log_final_delta"] for d in deltas) / len(deltas)
        floor_pass_rate = sum(d["floor_pass"] for d in deltas) / len(deltas)
        million_pass_rate = sum(d["million_floor"] for d in deltas) / len(deltas)
        floor_shortfall = max(0.0, 1.0 - floor_pass_rate)
        covid_bonus = max(0.0, covid_dd or 0.0) + 0.5 * max(0.0, covid_peak_dd or 0.0)
        objective = (
            9.0 * avg_dd
            + 3.0 * avg_mean_dd
            + 20.0 * avg_log
            + 20.0 * floor_pass_rate
            + 8.0 * million_pass_rate
            + 1.5 * covid_bonus
            - 35.0 * max_worse
            - 90.0 * float(v60_2025_dip_worse or 0.0)
            - 200.0 * floor_shortfall
        )
        first = cand_rows[0]
        summary.append(
            {
                "candidate": candidate,
                "objective": objective,
                "avg_worst_dd_improve_pp": avg_dd,
                "avg_mean_dd_improve_pp": avg_mean_dd,
                "max_worst_dd_worse_pp": max_worse,
                "worst_regression_version": (worst_regression or {}).get("version"),
                "worst_regression_window": (worst_regression or {}).get("window"),
                "worst_regression_base_dd": (worst_regression or {}).get("base_worst_dd"),
                "worst_regression_candidate_dd": (worst_regression or {}).get("candidate_worst_dd"),
                "v60_2020_crash_dd_improve_pp": covid_dd,
                "v60_covid_peak_dd_improve_pp": covid_peak_dd,
                "v60_2025_dip_worse_pp": v60_2025_dip_worse,
                "avg_practical_log_final_delta": avg_log,
                "wealth_floor_pass_rate": floor_pass_rate,
                "million_floor_pass_rate": million_pass_rate,
                "avg_open_premium_base_pct": sum(d["open_prem_base"] for d in deltas) / len(deltas),
                "avg_call_open_premium_base_pct": sum(d["call_open_base"] for d in deltas) / len(deltas),
                "avg_put_open_premium_base_pct": sum(d["put_open_base"] for d in deltas) / len(deltas),
                "avg_saturation_scale": sum(d["sat"] for d in deltas) / len(deltas),
                "avg_reserved_cash_pct": sum(d["reserved"] for d in deltas) / len(deltas),
                "avg_ct_suppressed_calls": sum(d["ct_touched"] for d in deltas) / len(deltas),
                "windows": len(deltas),
                **{k: first[k] for k in asdict(ComboCandidate("x")).keys() if k in first},
            }
        )
    summary.sort(key=lambda r: float(r["objective"]), reverse=True)
    return summary


def select_top(summary: list[dict[str, Any]], top_k: int) -> list[str]:
    eligible = [
        row for row in summary
        if row["candidate"] != "baseline"
        and float(row.get("avg_worst_dd_improve_pp") or 0.0) > 0.0
        and float(row.get("max_worst_dd_worse_pp") or 0.0) <= 0.75
        and float(row.get("wealth_floor_pass_rate") or 0.0) >= 0.75
        and float(row.get("avg_practical_log_final_delta") or 0.0) >= -0.05
    ]
    fallback = [row for row in summary if row["candidate"] != "baseline" and row not in eligible]
    return [row["candidate"] for row in (eligible + fallback)[:top_k]]


def merge_required_candidates(selected: list[str], required: list[str]) -> list[str]:
    """Preserve rank order while forcing production-clean controls forward."""
    out: list[str] = []
    for name in [*selected, *required]:
        if name and name not in out:
            out.append(name)
    return out


def write_findings(phase_summaries: dict[str, list[dict[str, Any]]], meta: dict[str, Any]) -> None:
    lines = [
        "# Combined Stage 3 Portfolio Candidate Screen",
        "",
        "Experiment-only. No score rows, recalc, version bump, or DB persistence.",
        "",
        "## Scope",
        "",
        f"- phase1: versions={', '.join(meta['phase1_versions'])}, windows={', '.join(meta['phase1_windows'])}, n={meta['phase1_n']}",
        f"- phase2: versions={', '.join(meta['phase2_versions'])}, windows={', '.join(meta['phase2_windows'])}, n={meta['phase2_n']}, enabled={meta.get('run_phase2', True)}",
        f"- phase3: versions={', '.join(meta['phase3_versions'])}, windows={', '.join(meta['phase3_windows'])}, n={meta['phase3_n']}, enabled={meta['run_phase3']}",
        f"- wealth objective cap: ${meta['wealth_cap']:,.0f}",
        f"- wealth floor: ${meta['wealth_floor']:,.0f}",
        "",
    ]
    for phase in ("phase3", "phase2", "phase1"):
        summary = phase_summaries.get(phase) or []
        if not summary:
            continue
        lines.extend(
            [
                f"## {phase.title()} Ranking",
                "",
                "| rank | candidate | objective | avg worst-DD improve | max DD worse | covid DD | v60 2025 worse | practical log delta | floor pass | open/base | call/base | put/base | reserve | ct touched |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for i, row in enumerate(summary[:12], 1):
            lines.append(
                f"| {i} | `{row['candidate']}` | {float(row['objective']):+.2f} | "
                f"{float(row['avg_worst_dd_improve_pp']):+.2f}pp | {float(row['max_worst_dd_worse_pp']):+.2f}pp | "
                f"{float(row.get('v60_2020_crash_dd_improve_pp') or 0.0):+.2f}pp | "
                f"{float(row.get('v60_2025_dip_worse_pp') or 0.0):+.2f}pp | "
                f"{float(row['avg_practical_log_final_delta']):+.3f} | {float(row['wealth_floor_pass_rate']):.0%} | "
                f"{float(row['avg_open_premium_base_pct']):.1f}% | {float(row['avg_call_open_premium_base_pct']):.1f}% | "
                f"{float(row['avg_put_open_premium_base_pct']):.1f}% | {float(row['avg_reserved_cash_pct']):.2f}% | "
                f"{float(row['avg_ct_suppressed_calls']):.1f} |"
            )
        lines.append("")
    (RUN_DIR / "findings.md").write_text("\n".join(lines), encoding="utf-8")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--daily-state", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--wave-predictions", type=Path, default=DEFAULT_WAVE)
    parser.add_argument("--phase1-n", type=int, default=160)
    parser.add_argument("--phase2-n", type=int, default=300)
    parser.add_argument("--phase3-n", type=int, default=700)
    parser.add_argument("--phase1-versions", default="v60")
    parser.add_argument("--phase2-versions", default="v42,v54,v58,v60")
    parser.add_argument("--phase3-versions", default="v42,v54,v58,v60")
    parser.add_argument("--phase1-windows", default="2020_crash,covid_peak,2020_full,2022,2024,2025,2025_dip,2022_now,5y")
    parser.add_argument("--phase2-windows", default="2022,2024,2025_dip,2022_now,2020_now")
    parser.add_argument("--phase3-windows", default="2022,2024,2025_dip,2022_now,2020_now")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--phase3-top-k", type=int, default=2)
    parser.add_argument("--skip-phase2", action="store_true")
    parser.add_argument(
        "--only-candidates",
        default="",
        help="Comma-separated candidate names to run in phase1; baseline is added automatically.",
    )
    parser.add_argument(
        "--must-candidates",
        default="g80_c65_p25_ref16_4_pow05_floor55_25m",
        help="Comma-separated candidate names to force into phase2/phase3 for deployability checks.",
    )
    parser.add_argument("--skip-phase3", action="store_true")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    parser.add_argument("--wealth-cap", type=float, default=5_000_000)
    parser.add_argument("--wealth-floor", type=float, default=2_000_000)
    args = parser.parse_args(argv)

    global RUN_DIR, STARTED_AT
    RUN_DIR = args.out_dir.resolve()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    for terminal_name in ("done.json", "failed.json"):
        try:
            (RUN_DIR / terminal_name).unlink()
        except FileNotFoundError:
            pass
    STARTED_AT = utc_now()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    meta = {
        "phase1_versions": parse_csv_list(args.phase1_versions),
        "phase2_versions": parse_csv_list(args.phase2_versions),
        "phase3_versions": parse_csv_list(args.phase3_versions),
        "phase1_windows": parse_csv_list(args.phase1_windows),
        "phase2_windows": parse_csv_list(args.phase2_windows),
        "phase3_windows": parse_csv_list(args.phase3_windows),
        "phase1_n": args.phase1_n,
        "phase2_n": args.phase2_n,
        "phase3_n": args.phase3_n,
        "run_phase2": not args.skip_phase2,
        "run_phase3": not args.skip_phase3,
        "wealth_cap": args.wealth_cap,
        "wealth_floor": args.wealth_floor,
        "daily_state": str(args.daily_state),
        "wave_predictions": str(args.wave_predictions),
        "workers": args.workers,
        "must_candidates": parse_csv_list(args.must_candidates),
    }

    note("starting combined Stage 3 portfolio screen")
    write_status(state="starting", phase="init", meta=meta)

    all_candidates = candidate_pool()
    by_name = {candidate.name: candidate for candidate in all_candidates}
    only_names = parse_csv_list(args.only_candidates)
    if only_names:
        selected = ["baseline", *only_names]
        all_candidates = [by_name[name] for name in selected if name in by_name]
        missing = [name for name in selected if name not in by_name]
        if missing:
            raise KeyError(f"Unknown --only-candidates entries: {missing}")
    p1_versions = resolve_versions(meta["phase1_versions"])
    p2_versions = resolve_versions(meta["phase2_versions"])
    p3_versions = resolve_versions(meta["phase3_versions"])

    phase_summaries: dict[str, list[dict[str, Any]]] = {}

    _p1_rows, p1_summary = run_phase(
        "phase1",
        all_candidates,
        p1_versions,
        meta["phase1_windows"],
        args.phase1_n,
        RUN_DIR / "phase1_results.csv",
        RUN_DIR / "phase1_summary.csv",
        args.wealth_cap,
        args.wealth_floor,
        args.daily_state,
        args.wave_predictions,
        args.workers,
    )
    phase_summaries["phase1"] = p1_summary
    p2_summary: list[dict[str, Any]] = []
    if not args.skip_phase2:
        phase2_names = merge_required_candidates(select_top(p1_summary, args.top_k), meta["must_candidates"])
        phase2_candidates = [by_name["baseline"]] + [by_name[name] for name in phase2_names if name in by_name]
        note(f"phase2 candidates: {', '.join(candidate.name for candidate in phase2_candidates)}")

        _p2_rows, p2_summary = run_phase(
            "phase2",
            phase2_candidates,
            p2_versions,
            meta["phase2_windows"],
            args.phase2_n,
            RUN_DIR / "phase2_results.csv",
            RUN_DIR / "phase2_summary.csv",
            args.wealth_cap,
            args.wealth_floor,
            args.daily_state,
            args.wave_predictions,
            args.workers,
        )
        phase_summaries["phase2"] = p2_summary

    if not args.skip_phase2 and not args.skip_phase3:
        phase3_names = merge_required_candidates(select_top(p2_summary, args.phase3_top_k), meta["must_candidates"])
        phase3_candidates = [by_name["baseline"]] + [by_name[name] for name in phase3_names if name in by_name]
        note(f"phase3 candidates: {', '.join(candidate.name for candidate in phase3_candidates)}")
        _p3_rows, p3_summary = run_phase(
            "phase3",
            phase3_candidates,
            p3_versions,
            meta["phase3_windows"],
            args.phase3_n,
            RUN_DIR / "phase3_results.csv",
            RUN_DIR / "phase3_summary.csv",
            args.wealth_cap,
            args.wealth_floor,
            args.daily_state,
            args.wave_predictions,
            args.workers,
        )
        phase_summaries["phase3"] = p3_summary

    write_findings(phase_summaries, meta)
    top_source = phase_summaries.get("phase3") or phase_summaries.get("phase2") or phase_summaries.get("phase1") or []
    done = {
        "state": "done",
        "started_at": STARTED_AT,
        "finished_at": utc_now(),
        "top_candidate": top_source[0]["candidate"] if top_source else None,
        "outputs": output_paths(),
        "meta": meta,
    }
    atomic_json(RUN_DIR / "done.json", done)
    write_status(**done)
    note(f"done; top_candidate={done['top_candidate']}")
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:
        failed = {
            "state": "failed",
            "started_at": globals().get("STARTED_AT", utc_now()),
            "finished_at": utc_now(),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "outputs": output_paths() if "RUN_DIR" in globals() else {},
        }
        out_dir = globals().get("RUN_DIR")
        if out_dir:
            atomic_json(out_dir / "failed.json", failed)
            write_status(**failed)
            note(f"failed: {failed['error']}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
