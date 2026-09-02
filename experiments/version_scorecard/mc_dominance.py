#!/usr/bin/env python
"""Tier-3 MC dominance layer for the version scorecard.

Resolves the deterministic scorecard's tie-band middle by running N-iteration
Monte Carlo per (version x profile x window) and computing the things a single
deterministic path cannot:

  - reach_fraction   : P(equity reaches $1M within the window)
  - collapse_fraction: P(equity falls to <= 20% of start)
  - days/bars to $1M : Kaplan-Meier-style median with censoring of non-reachers
  - drawdown DIST    : median / p90 / worst over paths (not a single point)
  - dominance        : pairwise P(final_A > final_B) over COMMON seeds -> a
                       Copeland-style dominance score that ORDERS the tie band

Read-only research. Sets MC_NO_DB_PERSIST=1 so it never writes score/assessment/
MC rows. Profiles are applied through the same env-override + importlib.reload
path that experiments/portfolio_profiles/sweep_profiles.py uses, sourced from the
canonical algorithm_versions/portfolio_profiles.json so Sentinel/Core/Apex match
exactly what is deployed.

Per-path arrays come from monte_carlo.run_window under MC_RETURN_PATHS=1 (an
opt-in, default-off instrumentation flag), aligned by index = common seed.

Run (Windows):
    set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && \
    python experiments/version_scorecard/mc_dominance.py --run-dir .cache/algorithm_versions/_scorecard/mc/run1
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import statistics
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_versions import manager  # noqa: E402
from database.trader_database import DB  # noqa: E402

PROFILES_JSON = ROOT / "algorithm_versions" / "portfolio_profiles.json"
STARTING_CASH = 50_000.0
TARGET_1M = 1_000_000.0
COLLAPSE_FRACTION = 0.20
TRADING_DAYS_PER_YEAR = 252.0
CALENDAR_DAYS_PER_YEAR = 365.0

# Env keys we own per profile; cleared between profiles so nothing leaks.
PROFILE_ENV_KEYS = (
    "PRACTICAL_EXPOSURE_ENABLED", "PRACTICAL_CAPITAL_CEILING",
    "GROSS_PREMIUM_CAP", "CALL_PREMIUM_CAP", "PUT_PREMIUM_CAP",
    "OPP_SAT_CALL_REF", "OPP_SAT_PUT_REF", "OPP_SAT_POWER", "OPP_SAT_FLOOR",
    "MAX_POSITIONS_OVERRIDE", "MAX_POSITIONS_CALL", "MAX_POSITIONS_PUT",
    "DD_SOFT_BAND_LO", "DD_SOFT_BAND_HI", "DD_SOFT_CALL_FLOOR",
    "TIER_ULTRA_OV", "TIER_TOP_OV", "TIER_MID_OV", "TIER_LOW_OV", "TIER_OVERFLOW_OV",
    "PUT_TIER_TOP_OV", "PUT_TIER_MID_OV", "PUT_TIER_LOW_OV",
)

# Default scoped grid: the contested leaders from the deterministic scorecard
# (top of the champion matrix + Sentinel/Apex contenders). NOT the full 11.
DEFAULT_VERSIONS = ["v44", "v52", "v57", "v59", "v60", "v65"]
DEFAULT_PROFILES = ["sentinel", "core", "apex"]


def _today() -> date:
    return date.today()


def windows_for(names: list[str]) -> list[tuple[str, date, date]]:
    end = _today()
    table = {
        "covid_crash_2020": (date(2020, 1, 1), date(2020, 4, 30)),
        "covid_cycle_2020_2021": (date(2020, 1, 1), date(2021, 12, 31)),
        "2020_now": (date(2020, 1, 1), end),
        "22_now": (date(2022, 1, 1), end),
        "year_2022": (date(2022, 1, 1), date(2022, 12, 31)),
        "year_2025": (date(2025, 1, 1), date(2025, 12, 31)),
    }
    out = []
    for nm in names:
        if nm not in table:
            raise SystemExit(f"Unknown window {nm!r}; valid: {', '.join(table)}")
        s, e = table[nm]
        out.append((nm, s, e))
    return out


# --------------------------------------------------------------------------- IO

def utc_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def log(run_dir: Path, message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    try:
        with (run_dir / "run.log").open("a", encoding="utf-8", buffering=1) as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


# ------------------------------------------------------------------- profiles

def load_profiles() -> dict[str, dict]:
    data = json.loads(PROFILES_JSON.read_text(encoding="utf-8"))
    return {p["key"]: p for p in data.get("profiles", [])}


def profile_env(params: dict) -> dict[str, str]:
    """Map a portfolio_profiles.json `params` block to monte_carlo env overrides."""
    return {
        "PRACTICAL_EXPOSURE_ENABLED": "1" if params.get("practical_enabled", True) else "0",
        "PRACTICAL_CAPITAL_CEILING": str(float(params["capital_ceiling"])),
        "GROSS_PREMIUM_CAP": str(float(params["gross_cap"])),
        "CALL_PREMIUM_CAP": str(float(params["call_cap"])),
        "PUT_PREMIUM_CAP": str(float(params["put_cap"])),
        "OPP_SAT_CALL_REF": str(float(params["call_ref"])),
        "OPP_SAT_PUT_REF": str(float(params["put_ref"])),
        "OPP_SAT_POWER": str(float(params["sat_power"])),
        "OPP_SAT_FLOOR": str(float(params["sat_floor"])),
        "MAX_POSITIONS_OVERRIDE": str(int(params["max_positions"])),
        "MAX_POSITIONS_CALL": str(int(params["call_max"])),
        "MAX_POSITIONS_PUT": str(int(params["put_max"])),
        "DD_SOFT_BAND_LO": str(float(params["dd_lo"])),
        "DD_SOFT_BAND_HI": str(float(params["dd_hi"])),
        "DD_SOFT_CALL_FLOOR": str(float(params["dd_floor"])),
        "TIER_ULTRA_OV": str(float(params["tier_ultra"])),
        "TIER_TOP_OV": str(float(params["tier_top"])),
        "TIER_MID_OV": str(float(params["tier_mid"])),
        "TIER_LOW_OV": str(float(params["tier_low"])),
        "TIER_OVERFLOW_OV": str(float(params["tier_overflow"])),
        "PUT_TIER_TOP_OV": str(float(params["put_top"])),
        "PUT_TIER_MID_OV": str(float(params["put_mid"])),
        "PUT_TIER_LOW_OV": str(float(params["put_low"])),
    }


def apply_profile_env(params: dict, n_iter: int, workers: int | None) -> None:
    for k in PROFILE_ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(profile_env(params))
    os.environ.update({
        "MC_NO_DB_PERSIST": "1",
        "MC_TRADE_TAPE": "0",
        "MC_RETURN_PATHS": "1",
        "REALLOC_STRATEGY": "",
        "PUT_WAVE_RESERVE_ENABLED": "0",
        "N_ITER_OVERRIDE": str(int(n_iter)),
    })
    if workers is not None:
        os.environ["MC_WORKERS"] = str(int(workers))


def reload_mc():
    import monte_carlo  # noqa: WPS433
    return importlib.reload(monte_carlo)


# ------------------------------------------------------------------------- DB

def refresh_db() -> None:
    try:
        if not DB.is_closed():
            DB.close()
    except Exception:
        pass
    DB.connect(reuse_if_open=False)


def run_window_retry(mc, label: str, start: date, end: date, version, attempts: int = 6) -> dict:
    last = None
    for attempt in range(1, attempts + 1):
        try:
            refresh_db()
            return mc.run_window(label, start, end, version)["seeded"]
        except Exception as exc:  # noqa: BLE001
            last = exc
            try:
                if not DB.is_closed():
                    DB.close()
            except Exception:
                pass
            if attempt < attempts:
                time.sleep(15 * attempt)
    raise last  # type: ignore[misc]


# -------------------------------------------------------------------- analysis

def _pct(seq: list[float], q: float):
    if not seq:
        return None
    s = sorted(seq)
    i = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[i]


def km_median_bars(t1m_bars: list, n_trading_days: int):
    """Kaplan-Meier-style median bars-to-$1M with censoring.

    Non-reachers (None) are censored at n_trading_days+1. If >=50% of paths
    reach $1M the median is a real bar count; otherwise it is censored (None).
    """
    if not t1m_bars:
        return None, 0.0, None
    sentinel = (n_trading_days or 0) + 1
    filled = [(b if b is not None else sentinel) for b in t1m_bars]
    reached = [b for b in t1m_bars if b is not None]
    reach_frac = len(reached) / len(t1m_bars)
    med = statistics.median(sorted(filled))
    median_bars = None if med >= sentinel else float(med)
    median_reached = statistics.median(reached) if reached else None
    return median_bars, reach_frac, median_reached


def bars_to_cal_days(bars):
    if bars is None:
        return None
    return round(bars * CALENDAR_DAYS_PER_YEAR / TRADING_DAYS_PER_YEAR)


def cell_metrics(cell: dict) -> dict:
    finals = [float(x) for x in (cell.get("finals") or [])]
    dds = [float(x) for x in (cell.get("dds") or [])]
    t1m = cell.get("t1m_bars") or []
    n_td = int(cell.get("n_trading_days") or 0)
    start_cash = float(cell.get("starting_cash") or STARTING_CASH)
    n = len(finals)
    median_bars, reach_frac, median_reached = km_median_bars(t1m, n_td)
    collapse_frac = (sum(1 for f in finals if f <= start_cash * COLLAPSE_FRACTION) / n) if n else None
    log_mults = [math.log10(max(f, 1.0) / start_cash) for f in finals]
    return {
        "n_paths": n,
        "reach_fraction": reach_frac,
        "collapse_fraction": collapse_frac,
        "median_bars_to_1m": median_bars,
        "median_cal_days_to_1m": bars_to_cal_days(median_bars),
        "median_bars_to_1m_reached_only": median_reached,
        "median_cal_days_reached_only": bars_to_cal_days(median_reached),
        "dd_median_pct": (statistics.median(dds) * 100.0) if dds else None,
        "dd_p90_pct": (_pct(dds, 0.90) * 100.0) if dds else None,
        "dd_worst_pct": (max(dds) * 100.0) if dds else None,
        "dd_mean_pct": (statistics.mean(dds) * 100.0) if dds else None,
        "log10x_median": statistics.median(log_mults) if log_mults else None,
        "log10x_p25": _pct(log_mults, 0.25) if log_mults else None,
        "log10x_p75": _pct(log_mults, 0.75) if log_mults else None,
        "n_trading_days": n_td,
    }


def _paired_winrate(a_vals: list, b_vals: list, better) -> float | None:
    """Fraction of COMMON-seed paths where a beats b (ties = 0.5).
    better(x, y) -> +1 if x wins, 0 tie, -1 if y wins."""
    m = min(len(a_vals), len(b_vals))
    if m == 0:
        return None
    wins = ties = 0
    for i in range(m):
        c = better(a_vals[i], b_vals[i])
        if c > 0:
            wins += 1
        elif c == 0:
            ties += 1
    return (wins + 0.5 * ties) / m


def _speed_better(ta, tb) -> int:
    # smaller bars-to-$1M = faster; non-reach (None) treated as slowest (inf)
    a = ta if ta is not None else math.inf
    b = tb if tb is not None else math.inf
    return 1 if a < b else (-1 if a > b else 0)


def _safety_better(da, db) -> int:
    # smaller max drawdown = safer
    return 1 if da < db else (-1 if da > db else 0)


def paired_matrices(cells_by_version: dict[str, dict]) -> dict:
    """Paired (common-seed) dominance on the REALIZABLE axes, not fantasy final
    wealth: speed = reaches $1M faster; safety = shallower max drawdown.
    Terminal wealth at these scales is unrealizable (DD-primary doctrine), so
    P(final_A > final_B) is deliberately NOT the headline dominance."""
    versions = sorted(cells_by_version)
    speed: dict[str, dict[str, float | None]] = {}
    safety: dict[str, dict[str, float | None]] = {}
    for a in versions:
        speed[a], safety[a] = {}, {}
        for b in versions:
            if a == b:
                speed[a][b] = safety[a][b] = None
                continue
            ca, cb = cells_by_version[a], cells_by_version[b]
            speed[a][b] = _paired_winrate(ca.get("t1m_bars") or [], cb.get("t1m_bars") or [], _speed_better)
            safety[a][b] = _paired_winrate(
                [float(x) for x in (ca.get("dds") or [])],
                [float(x) for x in (cb.get("dds") or [])],
                _safety_better,
            )
    return {"speed": speed, "safety": safety}


# ------------------------------------------------------------------------ grid

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--versions", default=",".join(DEFAULT_VERSIONS))
    ap.add_argument("--profiles", default=",".join(DEFAULT_PROFILES))
    ap.add_argument("--windows", default="covid_crash_2020,2020_now,22_now")
    ap.add_argument("--n-iter", type=int, default=400)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--min-score", type=float, default=70.0)
    ap.add_argument("--db-timeout", type=int, default=240)
    args = ap.parse_args()

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    paths_json = run_dir / "mc_paths.json"
    dominance_json = run_dir / "mc_dominance.json"
    report_md = run_dir / "REPORT.md"
    status_json = run_dir / "status.json"
    done_json = run_dir / "done.json"
    failed_json = run_dir / "failed.json"
    started = utc_now()

    try:
        DB.connect_params["read_timeout"] = int(args.db_timeout)
        DB.connect_params["write_timeout"] = int(args.db_timeout)
    except Exception:
        pass

    version_tokens = [t.strip() for t in args.versions.split(",") if t.strip()]
    profile_keys = [t.strip() for t in args.profiles.split(",") if t.strip()]
    window_specs = windows_for([t.strip() for t in args.windows.split(",") if t.strip()])
    profiles = load_profiles()
    for pk in profile_keys:
        if pk not in profiles:
            raise SystemExit(f"Unknown profile {pk!r}; have {', '.join(profiles)}")

    DB.connect(reuse_if_open=True)
    versions = [manager.resolve_algorithm_version(tok, create_current=False) for tok in version_tokens]
    ver_label = {int(v.id): tok for v, tok in zip(versions, version_tokens)}

    total = len(profile_keys) * len(versions) * len(window_specs)
    completed = 0
    log(run_dir, f"MC dominance grid: versions={version_tokens} profiles={profile_keys} "
                 f"windows={[w[0] for w in window_specs]} n_iter={args.n_iter} workers={args.workers} "
                 f"total_cells={total}")
    atomic_json(status_json, {"state": "running", "started_at": started, "total": total,
                              "completed": 0, "run_dir": str(run_dir)})

    # paths[profile][window][version_label] = run_window result (with per-path arrays)
    paths: dict[str, dict[str, dict[str, dict]]] = {}
    if paths_json.exists():
        try:
            paths = json.loads(paths_json.read_text(encoding="utf-8"))
        except Exception:
            paths = {}

    for pk in profile_keys:
        apply_profile_env(profiles[pk]["params"], args.n_iter, args.workers)
        mc = reload_mc()
        log(run_dir, f"profile={pk} env applied + monte_carlo reloaded")
        paths.setdefault(pk, {})
        for (wlabel, wstart, wend) in window_specs:
            paths[pk].setdefault(wlabel, {})
            for v, tok in zip(versions, version_tokens):
                if tok in paths[pk][wlabel] and paths[pk][wlabel][tok].get("finals"):
                    completed += 1
                    continue
                t0 = time.time()
                atomic_json(status_json, {"state": "running", "started_at": started, "total": total,
                                          "completed": completed, "current": f"{pk}/{wlabel}/{tok}",
                                          "updated_at": utc_now(), "run_dir": str(run_dir)})
                try:
                    res = run_window_retry(mc, wlabel, wstart, wend, v)
                except Exception as exc:  # noqa: BLE001
                    log(run_dir, f"  FAILED {pk}/{wlabel}/{tok}: {type(exc).__name__}: {exc}")
                    paths[pk][wlabel][tok] = {"error": f"{type(exc).__name__}: {exc}"}
                    completed += 1
                    atomic_json(paths_json, paths)
                    continue
                cell = {
                    "version": tok, "version_id": int(v.id),
                    "git_commit": getattr(v, "git_commit", None),
                    "profile": pk, "window": wlabel,
                    "n_iter": args.n_iter,
                    "finals": res.get("finals"),
                    "dds": res.get("dds"),
                    "t1m_bars": res.get("t1m_bars"),
                    "n_trading_days": res.get("n_trading_days"),
                    "starting_cash": res.get("starting_cash", STARTING_CASH),
                    "mean_ret": res.get("mean_ret"), "med_ret": res.get("med_ret"),
                    "worst_dd": res.get("worst_dd"), "p_coll": res.get("p_coll"),
                }
                paths[pk][wlabel][tok] = cell
                completed += 1
                atomic_json(paths_json, paths)
                m = cell_metrics(cell)
                log(run_dir, f"  [{completed}/{total}] {pk}/{wlabel}/{tok} "
                             f"reach={m['reach_fraction']:.0%} coll={m['collapse_fraction']:.1%} "
                             f"medDD={m['dd_median_pct']:.1f}% worstDD={m['dd_worst_pct']:.1f}% "
                             f"d1M={m['median_cal_days_to_1m']} ({round(time.time()-t0)}s)")

    # ---- analysis -----------------------------------------------------------
    log(run_dir, "computing reach/collapse/survival + pairwise dominance")
    analysis: dict[str, Any] = {
        "generated_at": utc_now(),
        "method": {
            "n_iter": args.n_iter, "starting_cash": STARTING_CASH, "target": TARGET_1M,
            "collapse_fraction": COLLAPSE_FRACTION,
            "versions": version_tokens, "profiles": profile_keys,
            "windows": [w[0] for w in window_specs],
            "note": "Paired dominance uses common seeds per window (same RNG stream, "
                    "each version's own signals). Read-only; no DB writes.",
        },
        "cells": {}, "dominance": {}, "ranking": {},
    }

    DOM_WINDOWS = [w[0] for w in window_specs if w[0] in ("2020_now", "22_now")] or [w[0] for w in window_specs]

    for pk in profile_keys:
        analysis["cells"].setdefault(pk, {})
        analysis["dominance"].setdefault(pk, {})
        # per-cell metrics
        for (wlabel, _s, _e) in window_specs:
            analysis["cells"][pk].setdefault(wlabel, {})
            for tok in version_tokens:
                cell = paths.get(pk, {}).get(wlabel, {}).get(tok)
                if not cell or cell.get("error") or not cell.get("finals"):
                    analysis["cells"][pk][wlabel][tok] = {"error": (cell or {}).get("error", "missing")}
                    continue
                analysis["cells"][pk][wlabel][tok] = cell_metrics(cell)
        # paired (common-seed) dominance on REALIZABLE axes: speed (reach $1M
        # faster) + safety (shallower DD). Final-wealth dominance omitted as
        # headline (terminal wealth at these scales is unrealizable).
        speed_acc: dict[str, list[float]] = {tok: [] for tok in version_tokens}
        safety_acc: dict[str, list[float]] = {tok: [] for tok in version_tokens}
        for wlabel in DOM_WINDOWS:
            cells_by_version = {}
            for tok in version_tokens:
                cell = paths.get(pk, {}).get(wlabel, {}).get(tok)
                if cell and not cell.get("error") and cell.get("finals"):
                    cells_by_version[tok] = cell
            if len(cells_by_version) < 2:
                continue
            mats = paired_matrices(cells_by_version)
            analysis["dominance"][pk][wlabel] = mats
            for a in cells_by_version:
                sp = [mats["speed"][a][b] for b in cells_by_version if b != a and mats["speed"][a][b] is not None]
                sf = [mats["safety"][a][b] for b in cells_by_version if b != a and mats["safety"][a][b] is not None]
                if sp:
                    speed_acc[a].append(statistics.mean(sp))
                if sf:
                    safety_acc[a].append(statistics.mean(sf))
        # profile dial = weight on SPEED vs SAFETY: Sentinel safety-led, Apex speed-led.
        speed_w = {"sentinel": 0.20, "core": 0.50, "apex": 0.80}.get(pk, 0.50)
        rank_rows = []
        for tok in version_tokens:
            sp = statistics.mean(speed_acc[tok]) if speed_acc[tok] else None
            sf = statistics.mean(safety_acc[tok]) if safety_acc[tok] else None
            if sp is None and sf is None:
                chosen = None
            else:
                chosen = speed_w * (sp if sp is not None else 0.5) + (1 - speed_w) * (sf if sf is not None else 0.5)
            reach, days1m, meddd, worstdd, coll, log10x = [], [], [], [], [], []
            for wlabel in DOM_WINDOWS:
                cm = analysis["cells"][pk].get(wlabel, {}).get(tok, {})
                if cm.get("reach_fraction") is not None:
                    reach.append(cm["reach_fraction"])
                if cm.get("median_cal_days_to_1m") is not None:
                    days1m.append(cm["median_cal_days_to_1m"])
                if cm.get("dd_median_pct") is not None:
                    meddd.append(cm["dd_median_pct"])
                if cm.get("dd_worst_pct") is not None:
                    worstdd.append(cm["dd_worst_pct"])
                if cm.get("collapse_fraction") is not None:
                    coll.append(cm["collapse_fraction"])
                if cm.get("log10x_median") is not None:
                    log10x.append(cm["log10x_median"])
            rank_rows.append({
                "version": tok,
                "speed_dominance": sp,
                "safety_dominance": sf,
                "chosen_dominance": chosen,
                "speed_weight": speed_w,
                "reach_fraction": statistics.mean(reach) if reach else None,
                "median_cal_days_to_1m": statistics.median(days1m) if days1m else None,
                "dd_median_pct": statistics.mean(meddd) if meddd else None,
                "dd_worst_pct": max(worstdd) if worstdd else None,
                "collapse_fraction": max(coll) if coll else None,
                "log10x_median": statistics.median(log10x) if log10x else None,
            })
        rank_rows.sort(key=lambda r: (r["chosen_dominance"] is not None, r["chosen_dominance"] or -1), reverse=True)
        for i, r in enumerate(rank_rows, 1):
            r["rank"] = i
        analysis["ranking"][pk] = rank_rows

    atomic_json(dominance_json, analysis)
    _write_report(report_md, analysis, version_tokens, profile_keys, DOM_WINDOWS)
    atomic_json(done_json, {"state": "done", "started_at": started, "finished_at": utc_now(),
                            "completed": completed, "total": total,
                            "outputs": {"paths": str(paths_json), "dominance": str(dominance_json),
                                        "report": str(report_md)}})
    atomic_json(status_json, {"state": "done", "started_at": started, "finished_at": utc_now(),
                              "completed": completed, "total": total, "run_dir": str(run_dir)})
    log(run_dir, f"DONE. report={report_md}")
    return 0


def _fmt(x, nd=2, suffix=""):
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return "n/a"


def _write_report(path: Path, analysis: dict, versions: list[str], profiles: list[str], dom_windows: list[str]) -> None:
    pretty = {"sentinel": "Sentinel (DD-safe)", "core": "Core (balanced)", "apex": "Apex (explosive)"}
    dial = {"sentinel": "safety-led (0.2 speed / 0.8 safety)",
            "core": "balanced (0.5 / 0.5)", "apex": "speed-led (0.8 speed / 0.2 safety)"}
    L = ["# MC Dominance Layer — Tier-3 Version Comparison", "",
         f"Generated: {analysis['generated_at']}", "",
         f"N={analysis['method']['n_iter']} iters/cell · paired common seeds · "
         f"dominance windows: {', '.join(dom_windows)} · ${int(analysis['method']['starting_cash']):,}→$1M.", "",
         "**Dominance is paired (common-seed) on REALIZABLE axes**, not fantasy final wealth:",
         "- **speed** = P(this version reaches $1M faster than the peer, same seed)",
         "- **safety** = P(this version has a shallower max drawdown than the peer, same seed)",
         "",
         "`Dominance` column = the profile-dialed blend used to rank (Sentinel safety-led, "
         "Apex speed-led, Core balanced). >0.5 = beats the average peer on that profile's objective.", ""]
    for pk in profiles:
        L.append(f"## {pretty.get(pk, pk)} — {dial.get(pk, '')}")
        L.append("")
        L.append("| Rank | Version | Dominance | speed | safety | reach $1M | med days→1M | med DD | worst DD | collapse |")
        L.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in analysis["ranking"].get(pk, []):
            L.append("| {rank} | {v} | {dom} | {sp} | {sf} | {reach} | {d1m} | {mdd} | {wdd} | {coll} |".format(
                rank=r.get("rank"), v=r["version"],
                dom=_fmt(r.get("chosen_dominance"), 3),
                sp=_fmt(r.get("speed_dominance"), 3),
                sf=_fmt(r.get("safety_dominance"), 3),
                reach=_fmt((r["reach_fraction"] or 0) * 100, 0, "%") if r.get("reach_fraction") is not None else "n/a",
                d1m=_fmt(r["median_cal_days_to_1m"], 0) if r.get("median_cal_days_to_1m") is not None else ">window",
                mdd=_fmt(r.get("dd_median_pct"), 1, "%"),
                wdd=_fmt(r.get("dd_worst_pct"), 1, "%"),
                coll=_fmt((r["collapse_fraction"] or 0) * 100, 1, "%") if r.get("collapse_fraction") is not None else "n/a",
            ))
        L.append("")
    L += ["## Notes", "",
          "- Deterministic v1 scorecard could not order the tie-band middle; paired speed/safety dominance does.",
          "- `reach $1M` and `med days→1M` are the survival view (KM-style, non-reachers censored).",
          "- `collapse` = P(equity ≤ 20% of start) — a path-distribution risk the single-path scorecard cannot see.",
          "- Final wealth is intentionally NOT a ranking axis (unrealizable at MC scale; DD-primary doctrine).",
          "- Read-only: no score/assessment/MC-DB rows written.", ""]
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        rd = None
        for i, a in enumerate(sys.argv):
            if a == "--run-dir" and i + 1 < len(sys.argv):
                rd = Path(sys.argv[i + 1]).resolve()
        if rd:
            rd.mkdir(parents=True, exist_ok=True)
            (rd / "failed.json").write_text(
                json.dumps({"state": "failed", "error": traceback.format_exc()}, indent=2), encoding="utf-8")
        traceback.print_exc()
        raise
