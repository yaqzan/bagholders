"""Mine Sentinel/Core/Apex portfolio profiles against active scoring rows.

This is read-only Stage 3 portfolio research. It varies only portfolio
deployment knobs that are already env-overridable by ``monte_carlo.py``:

- practical exposure caps / opportunity saturation;
- global and side-specific MaxPos;
- DD soft-band shape;
- call and put cascade tier allocation.

The runner writes durable Codex artifacts under ``--out-dir`` and never writes
score rows, assessment rows, or AlgorithmVersion state.
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

try:
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
    "2022_now": (date(2022, 1, 1), date(2026, 4, 24)),
    "5y": (date(2021, 1, 1), date(2026, 4, 15)),
}

ENV_KEYS = (
    "PRACTICAL_EXPOSURE_ENABLED",
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
    "DD_SOFT_BAND_LO",
    "DD_SOFT_BAND_HI",
    "DD_SOFT_CALL_FLOOR",
    "TIER_ULTRA_OV",
    "TIER_TOP_OV",
    "TIER_MID_OV",
    "TIER_LOW_OV",
    "TIER_OVERFLOW_OV",
    "PUT_TIER_TOP_OV",
    "PUT_TIER_MID_OV",
    "PUT_TIER_LOW_OV",
    "PUT_WAVE_RESERVE_ENABLED",
    "REALLOC_STRATEGY",
    "MC_TRADE_TAPE",
    "MC_NO_DB_PERSIST",
)

RUN_DIR: Path
STARTED_AT: str
RECENT_LINES: list[str] = []
_RUN_STDOUT = None
_RUN_STDERR = None


@dataclass(frozen=True)
class ProfileCandidate:
    candidate: str
    family: str
    profile_hint: str
    practical_enabled: bool = True
    capital_ceiling: float = 25_000_000.0
    gross_cap: float = 0.80
    call_cap: float = 0.65
    put_cap: float = 0.25
    call_ref: float = 16.0
    put_ref: float = 4.0
    sat_power: float = 0.50
    sat_floor: float = 0.55
    max_positions: int = 14
    call_max: int | None = 12
    put_max: int | None = 8
    dd_lo: float = 0.35
    dd_hi: float = 0.55
    dd_floor: float = 0.40
    tier_ultra: float = 0.20
    tier_top: float = 0.15
    tier_mid: float = 0.10
    tier_low: float = 0.10
    tier_overflow: float = 0.00
    put_top: float = 0.12
    put_mid: float = 0.10
    put_low: float = 0.08


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
        "profile_winners": str(RUN_DIR / "profile_winners.json"),
        "findings": str(RUN_DIR / "findings.md"),
        "done_json": str(RUN_DIR / "done.json"),
        "failed_json": str(RUN_DIR / "failed.json"),
    }


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
    del RECENT_LINES[:-120]
    (RUN_DIR / "run.recent.log").write_text("\n".join(reversed(RECENT_LINES)) + "\n", encoding="utf-8")


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


def parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def resolve_versions(tokens: list[str]) -> list[Any]:
    DB.connect(reuse_if_open=True)
    return [manager.resolve_algorithm_version(token, create_current=False) for token in tokens]


def _none_text(value: int | None) -> str:
    return "none" if value is None else str(value)


def apply_candidate_env(candidate: ProfileCandidate, n_iter: int, workers: int | None) -> None:
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(
        {
            "MC_NO_DB_PERSIST": "1",
            "REALLOC_STRATEGY": "",
            "MC_TRADE_TAPE": "0",
            "PUT_WAVE_RESERVE_ENABLED": "0",
            "N_ITER_OVERRIDE": str(n_iter),
            "PRACTICAL_EXPOSURE_ENABLED": "1" if candidate.practical_enabled else "0",
            "PRACTICAL_CAPITAL_CEILING": str(candidate.capital_ceiling),
            "GROSS_PREMIUM_CAP": str(candidate.gross_cap),
            "CALL_PREMIUM_CAP": str(candidate.call_cap),
            "PUT_PREMIUM_CAP": str(candidate.put_cap),
            "OPP_SAT_CALL_REF": str(candidate.call_ref),
            "OPP_SAT_PUT_REF": str(candidate.put_ref),
            "OPP_SAT_POWER": str(candidate.sat_power),
            "OPP_SAT_FLOOR": str(candidate.sat_floor),
            "MAX_POSITIONS_OVERRIDE": str(candidate.max_positions),
            "MAX_POSITIONS_CALL": _none_text(candidate.call_max),
            "MAX_POSITIONS_PUT": _none_text(candidate.put_max),
            "DD_SOFT_BAND_LO": str(candidate.dd_lo),
            "DD_SOFT_BAND_HI": str(candidate.dd_hi),
            "DD_SOFT_CALL_FLOOR": str(candidate.dd_floor),
            "TIER_ULTRA_OV": str(candidate.tier_ultra),
            "TIER_TOP_OV": str(candidate.tier_top),
            "TIER_MID_OV": str(candidate.tier_mid),
            "TIER_LOW_OV": str(candidate.tier_low),
            "TIER_OVERFLOW_OV": str(candidate.tier_overflow),
            "PUT_TIER_TOP_OV": str(candidate.put_top),
            "PUT_TIER_MID_OV": str(candidate.put_mid),
            "PUT_TIER_LOW_OV": str(candidate.put_low),
        }
    )
    if workers is not None:
        os.environ["MC_WORKERS"] = str(workers)


def reload_mc():
    import monte_carlo

    module = importlib.reload(monte_carlo)
    ensure_std_streams()
    return module


def refresh_db_connection() -> None:
    try:
        if not DB.is_closed():
            DB.close()
    except Exception:
        try:
            DB._state.reset()  # type: ignore[attr-defined]
        except Exception:
            pass
    DB.connect(reuse_if_open=False)


def run_window_with_retry(mc, label: str, start: date, end: date, version: Any, attempts: int = 8) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            ensure_std_streams()
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


def candidates() -> list[ProfileCandidate]:
    base = ProfileCandidate("sentinel_v1_current", "sentinel", "sentinel")
    common = {
        "family": "profile_sweep",
        "profile_hint": "screen",
        "tier_overflow": 0.0,
    }

    core = [
        ProfileCandidate("core_g85_c70_p25_ref18_4_floor60_dd405565", **common, gross_cap=0.85, call_cap=0.70, put_cap=0.25, call_ref=18, put_ref=4, sat_floor=0.60, max_positions=14, call_max=12, put_max=8, dd_lo=0.40, dd_hi=0.55, dd_floor=0.65),
        ProfileCandidate("core_g90_c72_p25_ref20_4_floor62_dd456070", **common, gross_cap=0.90, call_cap=0.72, put_cap=0.25, call_ref=20, put_ref=4, sat_floor=0.62, max_positions=14, call_max=12, put_max=8, dd_lo=0.45, dd_hi=0.60, dd_floor=0.70),
        ProfileCandidate("core_m16_g90_c74_p24_ref20_4_floor65_dd456575", **common, gross_cap=0.90, call_cap=0.74, put_cap=0.24, call_ref=20, put_ref=4, sat_floor=0.65, max_positions=16, call_max=14, put_max=8, dd_lo=0.45, dd_hi=0.65, dd_floor=0.75),
        ProfileCandidate("core_tier22_17_m16_g90_c74_ref20_dd456575", **common, gross_cap=0.90, call_cap=0.74, put_cap=0.24, call_ref=20, put_ref=4, sat_floor=0.65, max_positions=16, call_max=14, put_max=8, dd_lo=0.45, dd_hi=0.65, dd_floor=0.75, tier_ultra=0.22, tier_top=0.17, tier_mid=0.11, tier_low=0.11),
        ProfileCandidate("core_putthin_g90_c76_p20_ref20_3_floor66_dd456575", **common, gross_cap=0.90, call_cap=0.76, put_cap=0.20, call_ref=20, put_ref=3, sat_floor=0.66, max_positions=16, call_max=14, put_max=7, dd_lo=0.45, dd_hi=0.65, dd_floor=0.75),
        ProfileCandidate("core_noceiling_g90_c74_p24_ref20_dd456575", **common, capital_ceiling=100_000_000.0, gross_cap=0.90, call_cap=0.74, put_cap=0.24, call_ref=20, put_ref=4, sat_floor=0.65, max_positions=16, call_max=14, put_max=8, dd_lo=0.45, dd_hi=0.65, dd_floor=0.75),
        ProfileCandidate("core_mid_m50_g82_c67_p25_ref17_floor57_dd385852", **common, capital_ceiling=50_000_000.0, gross_cap=0.82, call_cap=0.67, put_cap=0.25, call_ref=17, put_ref=4, sat_floor=0.57, max_positions=14, call_max=12, put_max=8, dd_lo=0.38, dd_hi=0.58, dd_floor=0.52),
        ProfileCandidate("core_mid_m75_g84_c69_p25_ref18_floor60_dd406058", **common, capital_ceiling=75_000_000.0, gross_cap=0.84, call_cap=0.69, put_cap=0.25, call_ref=18, put_ref=4, sat_floor=0.60, max_positions=14, call_max=12, put_max=8, dd_lo=0.40, dd_hi=0.60, dd_floor=0.58),
        ProfileCandidate("core_mid_m100_g86_c71_p25_ref19_floor62_dd426265", **common, capital_ceiling=100_000_000.0, gross_cap=0.86, call_cap=0.71, put_cap=0.25, call_ref=19, put_ref=4, sat_floor=0.62, max_positions=15, call_max=13, put_max=8, dd_lo=0.42, dd_hi=0.62, dd_floor=0.65),
        ProfileCandidate("core_mid_full_g82_c67_p25_ref17_floor57_dd385852", **common, capital_ceiling=0.0, gross_cap=0.82, call_cap=0.67, put_cap=0.25, call_ref=17, put_ref=4, sat_floor=0.57, max_positions=14, call_max=12, put_max=8, dd_lo=0.38, dd_hi=0.58, dd_floor=0.52),
        ProfileCandidate("core_mid_full_g84_c69_p25_ref18_floor60_dd406058", **common, capital_ceiling=0.0, gross_cap=0.84, call_cap=0.69, put_cap=0.25, call_ref=18, put_ref=4, sat_floor=0.60, max_positions=14, call_max=12, put_max=8, dd_lo=0.40, dd_hi=0.60, dd_floor=0.58),
        ProfileCandidate("core_mid_full_g85_c70_p25_ref18_floor60_dd405565", **common, capital_ceiling=0.0, gross_cap=0.85, call_cap=0.70, put_cap=0.25, call_ref=18, put_ref=4, sat_floor=0.60, max_positions=14, call_max=12, put_max=8, dd_lo=0.40, dd_hi=0.55, dd_floor=0.65),
        ProfileCandidate("core_mid_full_g86_c71_p25_ref19_floor62_dd426265", **common, capital_ceiling=0.0, gross_cap=0.86, call_cap=0.71, put_cap=0.25, call_ref=19, put_ref=4, sat_floor=0.62, max_positions=15, call_max=13, put_max=8, dd_lo=0.42, dd_hi=0.62, dd_floor=0.65),
        ProfileCandidate("core_mid_full_g88_c72_p24_ref19_floor63_dd436870", **common, capital_ceiling=0.0, gross_cap=0.88, call_cap=0.72, put_cap=0.24, call_ref=19, put_ref=4, sat_floor=0.63, max_positions=15, call_max=13, put_max=8, dd_lo=0.43, dd_hi=0.68, dd_floor=0.70),
    ]

    apex = [
        ProfileCandidate("apex_fullportfolio_g90_c74_p24_ref20_dd456575", **common, capital_ceiling=0.0, gross_cap=0.90, call_cap=0.74, put_cap=0.24, call_ref=20, put_ref=4, sat_floor=0.65, max_positions=16, call_max=14, put_max=8, dd_lo=0.45, dd_hi=0.65, dd_floor=0.75),
        ProfileCandidate("apex_g95_c82_p26_ref24_5_floor75_dd607585", **common, capital_ceiling=100_000_000.0, gross_cap=0.95, call_cap=0.82, put_cap=0.26, call_ref=24, put_ref=5, sat_floor=0.75, max_positions=18, call_max=16, put_max=8, dd_lo=0.60, dd_hi=0.75, dd_floor=0.85, tier_ultra=0.25, tier_top=0.18, tier_mid=0.12, tier_low=0.12),
        ProfileCandidate("apex_g100_c88_p28_ref28_5_floor82_ddoff", **common, capital_ceiling=250_000_000.0, gross_cap=1.00, call_cap=0.88, put_cap=0.28, call_ref=28, put_ref=5, sat_floor=0.82, max_positions=20, call_max=18, put_max=8, dd_lo=0.0, dd_hi=0.0, dd_floor=1.0, tier_ultra=0.25, tier_top=0.18, tier_mid=0.12, tier_low=0.12),
        ProfileCandidate("apex_uncapped_m20_tier25_18_12_12", **common, practical_enabled=False, capital_ceiling=0.0, gross_cap=0.0, call_cap=0.0, put_cap=0.0, call_ref=0.0, put_ref=0.0, sat_power=1.0, sat_floor=0.0, max_positions=20, call_max=18, put_max=8, dd_lo=0.0, dd_hi=0.0, dd_floor=1.0, tier_ultra=0.25, tier_top=0.18, tier_mid=0.12, tier_low=0.12),
        ProfileCandidate("apex_uncapped_m18_tier24_17_12_11_putthin", **common, practical_enabled=False, capital_ceiling=0.0, gross_cap=0.0, call_cap=0.0, put_cap=0.0, call_ref=0.0, put_ref=0.0, sat_power=1.0, sat_floor=0.0, max_positions=18, call_max=16, put_max=7, dd_lo=0.0, dd_hi=0.0, dd_floor=1.0, tier_ultra=0.24, tier_top=0.17, tier_mid=0.12, tier_low=0.11, put_top=0.10, put_mid=0.08, put_low=0.06),
        ProfileCandidate("apex_g100_c90_p25_ref30_4_floor85_m20_putthin", **common, capital_ceiling=250_000_000.0, gross_cap=1.00, call_cap=0.90, put_cap=0.25, call_ref=30, put_ref=4, sat_floor=0.85, max_positions=20, call_max=18, put_max=7, dd_lo=0.60, dd_hi=0.80, dd_floor=0.90, tier_ultra=0.28, tier_top=0.20, tier_mid=0.13, tier_low=0.12, put_top=0.10, put_mid=0.08, put_low=0.06),
        ProfileCandidate("apex_g100_c92_p30_ref32_6_floor90_m22", **common, capital_ceiling=500_000_000.0, gross_cap=1.00, call_cap=0.92, put_cap=0.30, call_ref=32, put_ref=6, sat_floor=0.90, max_positions=22, call_max=20, put_max=9, dd_lo=0.65, dd_hi=0.85, dd_floor=0.95, tier_ultra=0.28, tier_top=0.20, tier_mid=0.14, tier_low=0.12),
    ]
    return [base, *core, *apex]


def candidate_by_name() -> dict[str, ProfileCandidate]:
    return {c.candidate: c for c in candidates()}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isfinite(out):
            return out
    except (TypeError, ValueError):
        pass
    return default


def finite_log_final(final_value: float, wealth_cap: float) -> float:
    capped = min(max(float(final_value), 1.0), wealth_cap)
    return math.log10(capped / 50_000.0)


def run_phase(
    phase: str,
    phase_candidates: list[ProfileCandidate],
    versions: list[Any],
    window_names: list[str],
    n_iter: int,
    out_csv: Path,
    summary_csv: Path,
    wealth_cap: float,
    workers: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows = read_csv(out_csv)
    completed_keys = {
        (r.get("phase"), r.get("candidate"), r.get("version"), r.get("window"))
        for r in rows
        if r.get("phase") == phase
    }
    total = len(phase_candidates) * len(versions) * len(window_names)
    completed = len(completed_keys)
    if rows:
        note(f"{phase} resuming {completed}/{total} cells from {out_csv.name}")

    for candidate in phase_candidates:
        apply_candidate_env(candidate, n_iter, workers)
        mc = reload_mc()
        note(f"{phase} candidate {candidate.candidate} env={json.dumps(asdict(candidate), sort_keys=True)}")
        for version in versions:
            for window_name in window_names:
                key = (phase, candidate.candidate, version.production_label, window_name)
                if key in completed_keys:
                    continue
                start, end = WINDOWS[window_name]
                write_status(
                    state="running",
                    phase=phase,
                    completed=completed,
                    total=total,
                    current_candidate=candidate.candidate,
                    current_version=version.production_label,
                    current_window=window_name,
                    n_iter=n_iter,
                )
                t0 = time.time()
                result = run_window_with_retry(mc, window_name, start, end, version)
                row = {
                    "phase": phase,
                    "candidate": candidate.candidate,
                    "version": version.production_label,
                    "version_id": int(version.id),
                    "git_commit": version.git_commit,
                    "window": window_name,
                    "n_iter": n_iter,
                    "elapsed_s": round(time.time() - t0, 2),
                    **asdict(candidate),
                }
                for result_key in (
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
                ):
                    row[result_key] = result.get(result_key)
                rows.append(row)
                completed += 1
                completed_keys.add(key)
                write_csv(out_csv, rows)
                summary, winners = summarize(rows, wealth_cap)
                write_csv(summary_csv, summary)
                atomic_json(RUN_DIR / "profile_winners.json", winners)
                note(
                    f"{phase} {candidate.candidate} {version.production_label} {window_name}: "
                    f"mean_final=${_as_float(row.get('mean_final')):,.0f} "
                    f"worstDD={_as_float(row.get('worst_dd')):.1f}% "
                    f"meanDD={_as_float(row.get('mean_dd')):.1f}% "
                    f"pColl={_as_float(row.get('p_coll')):.1f}%"
                )

    summary, winners = summarize(rows, wealth_cap)
    write_csv(out_csv, rows)
    write_csv(summary_csv, summary)
    atomic_json(RUN_DIR / "profile_winners.json", winners)
    return rows, summary, winners


def summarize(rows: list[dict[str, Any]], wealth_cap: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_key = {(r["version"], r["window"], r["candidate"]): r for r in rows}
    names = sorted({r["candidate"] for r in rows})
    summary: list[dict[str, Any]] = []
    for name in names:
        if name == "sentinel_v1_current":
            continue
        cand_rows = [r for r in rows if r["candidate"] == name]
        deltas: list[dict[str, float]] = []
        for row in cand_rows:
            base = by_key.get((row["version"], row["window"], "sentinel_v1_current"))
            if not base:
                continue
            base_final = _as_float(base.get("mean_final"))
            cand_final = _as_float(row.get("mean_final"))
            base_dd = _as_float(base.get("worst_dd"))
            cand_dd = _as_float(row.get("worst_dd"))
            deltas.append(
                {
                    "log_delta": finite_log_final(cand_final, wealth_cap) - finite_log_final(base_final, wealth_cap),
                    "dd_worse": cand_dd - base_dd,
                    "mean_dd_worse": _as_float(row.get("mean_dd")) - _as_float(base.get("mean_dd")),
                    "p_coll": _as_float(row.get("p_coll")),
                    "final": cand_final,
                    "base_final": base_final,
                    "worst_dd": cand_dd,
                    "open_base": _as_float(row.get("avg_open_premium_base_pct")),
                    "call_open_base": _as_float(row.get("avg_call_open_premium_base_pct")),
                    "put_open_base": _as_float(row.get("avg_put_open_premium_base_pct")),
                    "sat": _as_float(row.get("avg_saturation_scale"), 1.0),
                    "is_5y": 1.0 if row["window"] == "5y" else 0.0,
                    "is_22_now": 1.0 if row["window"] == "2022_now" else 0.0,
                    "is_2025_dip": 1.0 if row["window"] == "2025_dip" else 0.0,
                }
            )
        if not deltas:
            continue
        avg_log = sum(d["log_delta"] for d in deltas) / len(deltas)
        max_worse = max(d["dd_worse"] for d in deltas)
        avg_worse = sum(d["dd_worse"] for d in deltas) / len(deltas)
        max_abs_dd = max(d["worst_dd"] for d in deltas)
        max_p_coll = max(d["p_coll"] for d in deltas)
        dip_worse = max((d["dd_worse"] for d in deltas if d["is_2025_dip"]), default=0.0)
        log_5y = sum(d["log_delta"] for d in deltas if d["is_5y"]) or avg_log
        log_22_now = sum(d["log_delta"] for d in deltas if d["is_22_now"]) or avg_log
        core_penalty = (
            7.0 * max(0.0, max_worse - 8.0)
            + 4.0 * max(0.0, avg_worse - 4.0)
            + 10.0 * max(0.0, dip_worse - 5.0)
            + 250.0 * max_p_coll
        )
        core_score = 70.0 * avg_log + 20.0 * log_5y + 10.0 * log_22_now - core_penalty
        apex_score = (
            85.0 * avg_log
            + 45.0 * log_5y
            + 30.0 * log_22_now
            - 60.0 * max_p_coll
            - 0.20 * max_abs_dd
        )
        first = cand_rows[0]
        summary.append(
            {
                "candidate": name,
                "profile_hint": first.get("profile_hint"),
                "core_score": core_score,
                "apex_score": apex_score,
                "avg_log_final_delta": avg_log,
                "log_5y_delta": log_5y,
                "log_2022_now_delta": log_22_now,
                "avg_worst_dd_worse_pp": avg_worse,
                "max_worst_dd_worse_pp": max_worse,
                "max_candidate_worst_dd_pct": max_abs_dd,
                "v60_2025_dip_worse_pp": dip_worse,
                "max_p_coll_pct": max_p_coll,
                "avg_open_premium_base_pct": sum(d["open_base"] for d in deltas) / len(deltas),
                "avg_call_open_premium_base_pct": sum(d["call_open_base"] for d in deltas) / len(deltas),
                "avg_put_open_premium_base_pct": sum(d["put_open_base"] for d in deltas) / len(deltas),
                "avg_saturation_scale": sum(d["sat"] for d in deltas) / len(deltas),
                "windows": len(deltas),
                **{k: first[k] for k in asdict(ProfileCandidate("x", "x", "x")).keys() if k in first},
            }
        )

    core_pool = [
        row for row in summary
        if row["candidate"] != "sentinel_v1_current"
        and _as_float(row.get("max_p_coll_pct")) <= 0.0
        and _as_float(row.get("max_worst_dd_worse_pp")) <= 14.0
        and _as_float(row.get("avg_log_final_delta")) > 0.0
    ]
    apex_pool = [
        row for row in summary
        if row["candidate"] != "sentinel_v1_current"
        and _as_float(row.get("max_p_coll_pct")) <= 5.0
        and _as_float(row.get("avg_log_final_delta")) > 0.0
    ]
    core_winner = max(core_pool or summary, key=lambda r: _as_float(r.get("core_score")), default=None)
    apex_distinct_pool = [
        row for row in apex_pool
        if not core_winner or row.get("candidate") != core_winner.get("candidate")
    ]
    apex_winner = max(apex_distinct_pool or apex_pool or summary, key=lambda r: _as_float(r.get("apex_score")), default=None)
    winners = {
        "sentinel": {
            "candidate": "sentinel_v1_current",
            "selection": "current active Sentinel portfolio profile on active score rows",
        },
        "core": core_winner,
        "apex": apex_winner,
    }
    summary.sort(key=lambda r: _as_float(r.get("apex_score")), reverse=True)
    return summary, winners


def pick_phase_candidates(summary: list[dict[str, Any]], winners: dict[str, Any], top_each: int) -> list[ProfileCandidate]:
    by_name = candidate_by_name()
    names = ["sentinel_v1_current"]
    for key in ("core", "apex"):
        row = winners.get(key)
        if row and row.get("candidate"):
            names.append(row["candidate"])
    core_ranked = sorted(summary, key=lambda r: _as_float(r.get("core_score")), reverse=True)
    apex_ranked = sorted(summary, key=lambda r: _as_float(r.get("apex_score")), reverse=True)
    for row in [*core_ranked[:top_each], *apex_ranked[:top_each]]:
        names.append(row["candidate"])
    out = []
    seen = set()
    for name in names:
        if name in seen or name not in by_name:
            continue
        seen.add(name)
        out.append(by_name[name])
    return out


def write_findings(phase_summaries: dict[str, list[dict[str, Any]]], winners: dict[str, Any], meta: dict[str, Any]) -> None:
    lines = [
        "# Portfolio Profile Sweep",
        "",
        "Read-only Stage 3 sweep. Sentinel is the current active practical-exposure portfolio profile on active score rows.",
        "",
        "## Scope",
        "",
        f"- versions: {', '.join(meta['versions'])}",
        f"- phase1: n={meta['phase1_n']}, windows={', '.join(meta['phase1_windows'])}",
        f"- phase2: n={meta['phase2_n']}, windows={', '.join(meta['phase2_windows'])}, enabled={meta['run_phase2']}",
        f"- phase3: n={meta['phase3_n']}, windows={', '.join(meta['phase3_windows'])}, enabled={meta['run_phase3']}",
        f"- wealth cap for log objective: ${meta['wealth_cap']:,.0f}",
        "",
        "## Selected Profiles",
        "",
        "| profile | candidate | core score | apex score | avg log delta | max DD worse | max DD | pColl |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for profile in ("sentinel", "core", "apex"):
        row = winners.get(profile) or {}
        lines.append(
            f"| {profile.title()} | `{row.get('candidate', '-')}` | "
            f"{_as_float(row.get('core_score')):+.2f} | {_as_float(row.get('apex_score')):+.2f} | "
            f"{_as_float(row.get('avg_log_final_delta')):+.3f} | "
            f"{_as_float(row.get('max_worst_dd_worse_pp')):+.2f}pp | "
            f"{_as_float(row.get('max_candidate_worst_dd_pct')):.1f}% | "
            f"{_as_float(row.get('max_p_coll_pct')):.1f}% |"
        )
    lines.append("")
    for phase in ("phase3", "phase2", "phase1"):
        summary = phase_summaries.get(phase) or []
        if not summary:
            continue
        lines.extend(
            [
                f"## {phase.title()} Ranking",
                "",
                "| rank | candidate | core | apex | avg log delta | 5y log | 22-now log | avg DD worse | max DD worse | max DD | pColl | open/base |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for i, row in enumerate(summary[:16], 1):
            lines.append(
                f"| {i} | `{row['candidate']}` | {_as_float(row.get('core_score')):+.2f} | "
                f"{_as_float(row.get('apex_score')):+.2f} | {_as_float(row.get('avg_log_final_delta')):+.3f} | "
                f"{_as_float(row.get('log_5y_delta')):+.3f} | {_as_float(row.get('log_2022_now_delta')):+.3f} | "
                f"{_as_float(row.get('avg_worst_dd_worse_pp')):+.2f}pp | {_as_float(row.get('max_worst_dd_worse_pp')):+.2f}pp | "
                f"{_as_float(row.get('max_candidate_worst_dd_pct')):.1f}% | {_as_float(row.get('max_p_coll_pct')):.1f}% | "
                f"{_as_float(row.get('avg_open_premium_base_pct')):.1f}% |"
            )
        lines.append("")
    (RUN_DIR / "findings.md").write_text("\n".join(lines), encoding="utf-8")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--versions", default="v60")
    parser.add_argument("--phase1-n", type=int, default=100)
    parser.add_argument("--phase2-n", type=int, default=220)
    parser.add_argument("--phase3-n", type=int, default=500)
    parser.add_argument("--phase1-windows", default="2020_crash,covid_peak,2020_full,2022,2024,2025,2025_dip,2022_now,5y")
    parser.add_argument("--phase2-windows", default="2020_crash,covid_peak,2020_full,2022,2024,2025,2025_dip,2022_now,5y")
    parser.add_argument("--phase3-windows", default="2020_crash,covid_peak,2020_full,2022,2024,2025,2025_dip,2022_now,5y")
    parser.add_argument("--skip-phase2", action="store_true")
    parser.add_argument("--skip-phase3", action="store_true")
    parser.add_argument(
        "--only-candidates",
        default="",
        help="Comma-separated candidate names to run in phase1; sentinel is added automatically.",
    )
    parser.add_argument("--top-each", type=int, default=3)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    parser.add_argument("--wealth-cap", type=float, default=100_000_000)
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
    try:
        DB._connect_params["read_timeout"] = max(180, int(DB._connect_params.get("read_timeout", 30)))  # type: ignore[attr-defined]
        DB._connect_params["write_timeout"] = max(180, int(DB._connect_params.get("write_timeout", 30)))  # type: ignore[attr-defined]
    except Exception:
        pass

    meta = {
        "versions": parse_csv(args.versions),
        "phase1_windows": parse_csv(args.phase1_windows),
        "phase2_windows": parse_csv(args.phase2_windows),
        "phase3_windows": parse_csv(args.phase3_windows),
        "phase1_n": args.phase1_n,
        "phase2_n": args.phase2_n,
        "phase3_n": args.phase3_n,
        "run_phase2": not args.skip_phase2,
        "run_phase3": not args.skip_phase3,
        "workers": args.workers,
        "wealth_cap": args.wealth_cap,
        "candidate_count": len(candidates()),
    }
    note("starting portfolio profile sweep")
    write_status(state="starting", phase="init", meta=meta)

    versions = resolve_versions(meta["versions"])
    all_candidates = candidates()
    only_names = parse_csv(args.only_candidates)
    if only_names:
        by_name = {candidate.candidate: candidate for candidate in all_candidates}
        selected = ["sentinel_v1_current", *only_names]
        missing = [name for name in selected if name not in by_name]
        if missing:
            raise KeyError(f"Unknown --only-candidates entries: {missing}")
        seen = set()
        all_candidates = [
            by_name[name]
            for name in selected
            if not (name in seen or seen.add(name))
        ]
    phase_summaries: dict[str, list[dict[str, Any]]] = {}

    _rows1, summary1, winners = run_phase(
        "phase1",
        all_candidates,
        versions,
        meta["phase1_windows"],
        args.phase1_n,
        RUN_DIR / "phase1_results.csv",
        RUN_DIR / "phase1_summary.csv",
        args.wealth_cap,
        args.workers,
    )
    phase_summaries["phase1"] = summary1

    if not args.skip_phase2:
        phase2_candidates = pick_phase_candidates(summary1, winners, args.top_each)
        note(f"phase2 candidates: {', '.join(c.candidate for c in phase2_candidates)}")
        _rows2, summary2, winners = run_phase(
            "phase2",
            phase2_candidates,
            versions,
            meta["phase2_windows"],
            args.phase2_n,
            RUN_DIR / "phase2_results.csv",
            RUN_DIR / "phase2_summary.csv",
            args.wealth_cap,
            args.workers,
        )
        phase_summaries["phase2"] = summary2

    if not args.skip_phase2 and not args.skip_phase3:
        phase3_candidates = pick_phase_candidates(phase_summaries["phase2"], winners, top_each=1)
        note(f"phase3 candidates: {', '.join(c.candidate for c in phase3_candidates)}")
        _rows3, summary3, winners = run_phase(
            "phase3",
            phase3_candidates,
            versions,
            meta["phase3_windows"],
            args.phase3_n,
            RUN_DIR / "phase3_results.csv",
            RUN_DIR / "phase3_summary.csv",
            args.wealth_cap,
            args.workers,
        )
        phase_summaries["phase3"] = summary3

    write_findings(phase_summaries, winners, meta)
    done = {
        "state": "done",
        "started_at": STARTED_AT,
        "finished_at": utc_now(),
        "winners": winners,
        "outputs": output_paths(),
        "meta": meta,
    }
    atomic_json(RUN_DIR / "done.json", done)
    write_status(**done)
    note(
        "done; core={core} apex={apex}".format(
            core=(winners.get("core") or {}).get("candidate"),
            apex=(winners.get("apex") or {}).get("candidate"),
        )
    )
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
