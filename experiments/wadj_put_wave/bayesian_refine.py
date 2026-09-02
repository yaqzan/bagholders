"""Bayesian refinement for the put-side weekly adjustment wave.

This is a staging-native scoring experiment. It uses the checkout scoring path
(`ScoreSimulator(scoring_fn=None)`) and hot-swaps only the wave constants in
`database.utils.scoring`. It never writes Score rows.

Candidate formula in `calculate_weekly_adjustment`:

    scale = floor + (peak - floor) * tanh((abs(raw_wadj) / width) ** power)
    weekly_adj = raw_wadj * scale     # only when raw_wadj < 0

The baseline for comparison is the legacy hard step:

    weekly_adj = raw_wadj * WEEKLY_PUT_SCALE
"""
from __future__ import annotations

import argparse
import bisect
import io
import json
import math
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skopt import Optimizer
from skopt.space import Real

from experiments._holdout import assert_no_holdout_leak, cutoff_iso


CALL_TIERS = [("95+", 95), ("90+", 90), ("85+", 85), ("80+", 80), ("75+", 75), ("70+", 70)]
PUT_TIERS = [("<25", 25), ("<20", 20), ("<15", 15), ("<10", 10), ("<5", 5)]
WINDOWS = {"1y": 365, "3y": 1095, "5y": 1825}
PRIMARY_PUT_WEIGHTS = {"<25": 0.45, "<20": 0.30, "<15": 0.20, "<10": 0.05}
CALL_GUARDS = ("75+", "80+", "85+")

DIMENSIONS = [
    Real(0.80, 1.55, name="floor"),
    Real(1.00, 1.85, name="peak"),
    Real(2.00, 30.00, name="width"),
    Real(0.40, 2.80, name="power"),
]
PARAM_NAMES = [d.name for d in DIMENSIONS]
ScoringCase = tuple[Any, ...]


@dataclass(frozen=True)
class WaveParams:
    floor: float
    peak: float
    width: float
    power: float

    def normalized(self) -> "WaveParams":
        floor = max(0.0, float(self.floor))
        peak = max(float(self.peak), floor)
        width = max(1e-6, float(self.width))
        power = max(1e-6, float(self.power))
        return WaveParams(floor=floor, peak=peak, width=width, power=power)

    def label(self) -> str:
        p = self.normalized()
        return f"fl{p.floor:.3f}_pk{p.peak:.3f}_w{p.width:.2f}_pw{p.power:.2f}"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, default=str) + "\n")


def status_write(run_dir: Path, **payload: Any) -> None:
    write_json(
        run_dir / "status.json",
        {
            "updated_at": now_iso(),
            **payload,
            "outputs": {
                "results": str(run_dir / "bayes_results.jsonl"),
                "summary": str(run_dir / "bayes_summary.json"),
                "status": str(run_dir / "status.json"),
                "done": str(run_dir / "done.json"),
                "failed": str(run_dir / "failed.json"),
            },
        },
    )


def barrier_db_path() -> Path:
    env_path = os.environ.get("TRADER_BARRIER_DB")
    if env_path:
        return Path(env_path)
    local = ROOT / ".cache" / "barrier_outcomes.db"
    if local.exists():
        return local
    shared = Path(r"C:\Development\Trader\.cache\barrier_outcomes.db")
    return shared


def barrier_duckdb_path() -> Path | None:
    env_path = os.environ.get("TRADER_BARRIER_DUCKDB")
    if env_path:
        path = Path(env_path)
        return path if path.exists() else None
    candidates = [
        ROOT / ".cache" / "barrier_outcomes.duckdb",
        Path(r"C:\Development\Trader\.cache\barrier_outcomes.duckdb"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def load_barriers(since: date, w_days: int) -> dict[tuple[str, str, str], int]:
    duck_path = barrier_duckdb_path()
    if duck_path is not None:
        try:
            import duckdb

            print(f"[barriers] loading WR{w_days} generic outcomes from DuckDB {duck_path}", flush=True)
            t0 = time.time()
            conn = duckdb.connect(str(duck_path), read_only=True)
            try:
                rows = conn.execute(
                    """
                    SELECT symbol, CAST(date AS VARCHAR), side, result
                    FROM barrier_outcomes
                    WHERE barrier_set='30dte_generic'
                      AND w_days=?
                      AND side IN ('low', 'high')
                      AND date >= ?
                      AND result IS NOT NULL
                    """,
                    [w_days, since.isoformat()],
                ).fetchall()
            finally:
                conn.close()
            out = {
                (str(sym), str(d)[:10], str(side)): int(result)
                for sym, d, side, result in rows
            }
            print(f"[barriers] loaded {len(out):,} resolved rows in {time.time() - t0:.1f}s", flush=True)
            return out
        except Exception as exc:
            print(f"[barriers] DuckDB unavailable ({exc!r}); falling back to SQLite", flush=True)

    db_path = barrier_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"barrier cache not found: {db_path}")
    print(f"[barriers] loading WR{w_days} generic outcomes from {db_path}", flush=True)
    t0 = time.time()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            """
            SELECT symbol, date, side, result
            FROM barrier_outcomes
            WHERE barrier_set='30dte_generic'
              AND w_days=?
              AND side IN ('low', 'high')
              AND date >= ?
              AND result IS NOT NULL
            """,
            (w_days, since.isoformat()),
        )
        out = {
            (str(sym), str(d)[:10], str(side)): int(result)
            for sym, d, side, result in cur.fetchall()
        }
    finally:
        conn.close()
    print(f"[barriers] loaded {len(out):,} resolved rows in {time.time() - t0:.1f}s", flush=True)
    return out


def params_from_x(x: list[float]) -> WaveParams:
    return WaveParams(**{name: float(value) for name, value in zip(PARAM_NAMES, x)}).normalized()


def x_from_params(params: WaveParams) -> list[float]:
    p = params.normalized()
    return [p.floor, p.peak, p.width, p.power]


def seed_params() -> list[WaveParams]:
    return [
        WaveParams(1.00, 1.50, 10.0, 1.00),
        WaveParams(1.00, 1.50, 15.0, 1.00),
        WaveParams(1.00, 1.60, 8.0, 1.10),
        WaveParams(0.95, 1.55, 8.0, 1.00),
        WaveParams(1.10, 1.55, 12.0, 1.35),
        WaveParams(1.35, 1.55, 4.0, 0.75),
        WaveParams(1.50, 1.50, 10.0, 1.00),  # near-legacy static step
    ]


def set_wave(enabled: bool, params: WaveParams | None = None) -> None:
    import database.utils.scoring as scoring_mod

    scoring_mod.WEEKLY_PUT_WAVE_ENABLED = bool(enabled)
    if params is not None:
        p = params.normalized()
        scoring_mod.WEEKLY_PUT_WAVE_FLOOR = p.floor
        scoring_mod.WEEKLY_PUT_WAVE_PEAK = p.peak
        scoring_mod.WEEKLY_PUT_WAVE_WIDTH = p.width
        scoring_mod.WEEKLY_PUT_WAVE_POWER = p.power


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def build_scoring_cases(sim: Any, lookback_days: int, run_dir: Path) -> list[ScoringCase]:
    """Extract the invariant daily scoring inputs once for all wave candidates."""
    from volume_amplifier import get_volume_multiplier_from_cache

    cutoff = date.today() - timedelta(days=lookback_days)
    contexts = list(sim._contexts.items())
    cases: list[ScoringCase] = []

    status_write(
        run_dir,
        phase="build_cases",
        state="running",
        completed=0,
        total=len(contexts),
        scored_cases=0,
    )
    print(f"[cache] extracting scoring inputs from {len(contexts)} contexts", flush=True)

    for idx, (sym, ctx) in enumerate(contexts, start=1):
        stock = ctx.stock
        prior_vol_signals: dict[date, tuple[str, float, int]] = {}
        ph_dates = [p.date for p in ctx.ph_rows_asc]

        for ind_row in ctx.ind_rows:
            d = ind_row.date
            if d < cutoff:
                continue

            ind_cache = ctx.ind_cache_for(d)
            trend = stock.calculate_trend_score(d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
            if trend is None:
                continue

            ph = ctx.ph_map.get(d)
            pct_ema50 = (
                (float(ph.close) - float(ind_row.ema_50)) / float(ind_row.ema_50) * 100
                if ph and ph.close and ind_row.ema_50 is not None
                else None
            )
            macdh_raw = float(ind_row.macd_hist) if ind_row.macd_hist is not None else None
            rsi = stock.calculate_rsi_score(
                d,
                trend_score=trend,
                _ind_cache=ind_cache,
                _ph_cache=ctx.ph_map,
                macdh_raw=macdh_raw,
                pct_from_ema50=pct_ema50,
            )
            bb = stock.calculate_bollinger_bands_score(
                d, trend_score=trend, _ind_cache=ind_cache, _ph_cache=ctx.ph_map
            )
            macd = stock.calculate_macd_score(d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
            stoch = stock.calculate_stoch_score(d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
            if None in (rsi, bb, macd, stoch):
                continue

            scores_list = [bb, rsi, macd]
            total = len(scores_list)
            bullish = sum(s >= 50 for s in scores_list)
            bearish = total - bullish
            agreement = max(bullish, bearish) / total
            avg_dist = sum(s - 50 for s in scores_list) / total
            sig_str = min(abs(avg_dist) / 50, 1.0)
            turbo = 1 + 0.5 * sig_str * agreement
            ta = round(max(0, min(100, 50 + avg_dist * sig_str * (agreement ** 1.5) * turbo)))

            ws = ctx.ws_map.get(week_start(d))
            pws = ctx.ws_map.get(week_start(d) - timedelta(weeks=1))
            ws_rsi = ws.rsi if ws and ws.rsi and ws.macd else None
            ws_macd = ws.macd if ws and ws.rsi and ws.macd else None

            ph_idx = bisect.bisect_right(ph_dates, d)
            ph_slice = ctx.ph_rows_desc[len(ctx.ph_rows_asc) - ph_idx:]
            vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = (
                get_volume_multiplier_from_cache(d, ph_slice, prior_vol_signals, ctx.earnings_set)
            )

            bb_pct = None
            if None not in (ind_row.upper_band, ind_row.lower_band):
                bw = float(ind_row.upper_band) - float(ind_row.lower_band)
                if bw > 0 and ph:
                    bb_pct = (float(ph.close) - float(ind_row.lower_band)) / bw

            days_to_earnings = None
            if ctx.earnings_set:
                for delta in range(6):
                    if (d + timedelta(days=delta)) in ctx.earnings_set:
                        days_to_earnings = delta
                        break

            cases.append(
                (
                    sym,
                    d,
                    trend,
                    bb,
                    rsi,
                    macd,
                    stoch,
                    ta,
                    bb_pct,
                    pct_ema50,
                    ws.trend if ws else None,
                    ws_rsi,
                    ws_macd,
                    pws.trend if pws else None,
                    pws.rsi if pws else None,
                    pws.macd if pws else None,
                    vol_mult,
                    vol_raw,
                    vol_sig,
                    vol_mag,
                    blend_w,
                    vol_target,
                    macdh_raw,
                    sim._regime_map.get(d),
                    sim._mis_stress_map.get(d, 0.0),
                    days_to_earnings,
                    ctx.ret10d_sigma_map.get(d),
                    ctx.mcap_b,
                    ctx.kijun_pct_map.get(d),
                    ctx.wv_force1_map.get(d),
                )
            )
            prior_vol_signals[d] = (vol_sig, vol_mag, vol_raw)

        if idx == len(contexts) or idx % 25 == 0:
            status_write(
                run_dir,
                phase="build_cases",
                state="running",
                completed=idx,
                total=len(contexts),
                scored_cases=len(cases),
            )
            print(f"[cache] contexts {idx}/{len(contexts)} cases={len(cases):,}", flush=True)

    print(f"[cache] extracted {len(cases):,} scoring cases", flush=True)
    return cases


def score_cases_to_peaks(
    cases: list[ScoringCase],
    lookback_days: int,
    label: str,
) -> list[dict[str, Any]]:
    from database.utils.scoring import compute_overall_score

    cutoff = date.today() - timedelta(days=lookback_days)
    by_symbol: dict[str, list[tuple[date, int]]] = {}
    scored = 0

    for case in cases:
        (
            sym,
            d,
            trend,
            bb,
            rsi,
            macd,
            stoch,
            ta,
            bb_pct,
            pct_ema50,
            ws_trend,
            ws_rsi,
            ws_macd,
            prev_ws_trend,
            prev_ws_rsi,
            prev_ws_macd,
            vol_mult,
            vol_raw,
            vol_sig,
            vol_mag,
            blend_w,
            vol_target,
            macdh_raw,
            regime_multiplier,
            mis_stress,
            days_to_earnings,
            ret_10d_sigma,
            mcap_b,
            kijun_pct,
            wv_force1,
        ) = case
        if d < cutoff:
            continue

        overall, _, _ = compute_overall_score(
            trend,
            bb,
            rsi,
            macd,
            stoch,
            ta,
            bb_pct=bb_pct,
            pct_from_ema50=pct_ema50,
            ws_trend=ws_trend,
            ws_rsi=ws_rsi,
            ws_macd=ws_macd,
            prev_ws_trend=prev_ws_trend,
            prev_ws_rsi=prev_ws_rsi,
            prev_ws_macd=prev_ws_macd,
            vol_mult=vol_mult,
            vol_raw=vol_raw,
            vol_sig=vol_sig,
            vol_mag=vol_mag,
            blend_w=blend_w,
            vol_target=vol_target,
            macdh_raw=macdh_raw,
            regime_multiplier=regime_multiplier,
            mis_stress=mis_stress,
            days_to_earnings=days_to_earnings,
            ret_10d_sigma=ret_10d_sigma,
            mcap_b=mcap_b,
            kijun_pct=kijun_pct,
            wv_force1=wv_force1,
            score_date=d,
        )
        if overall is None:
            continue
        scored += 1
        if overall >= 70 or overall <= 25:
            by_symbol.setdefault(sym, []).append((d, int(overall)))

    peaks: list[dict[str, Any]] = []
    for sym, entries in by_symbol.items():
        date_set = {d for d, _ in entries}
        pruned: set[tuple[str, date]] = set()
        for d, score in sorted(entries, key=lambda item: abs(item[1] - 50), reverse=True):
            if (sym, d) in pruned:
                continue
            peaks.append({"symbol": sym, "date": d, "score": score})
            for direction in (-1, 1):
                walk = d + timedelta(days=direction)
                while walk in date_set and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += timedelta(days=direction)

    if peaks:
        assert_no_holdout_leak([p["date"] for p in peaks], f"wadj_put_wave {label} simulated peaks")
    print(f"[score {label}] scored {scored:,} cases -> {len(peaks):,} peaks", flush=True)
    return peaks


def build_peaks(scores: dict[tuple[str, date], int], lookback_days: int) -> list[dict[str, Any]]:
    cutoff = date.today() - timedelta(days=lookback_days)
    by_symbol: dict[str, list[tuple[date, int]]] = {}
    for (sym, d), score in scores.items():
        if d < cutoff:
            continue
        if score >= 70 or score <= 25:
            by_symbol.setdefault(sym, []).append((d, int(score)))

    peaks: list[dict[str, Any]] = []
    for sym, entries in by_symbol.items():
        date_set = {d for d, _ in entries}
        pruned: set[tuple[str, date]] = set()
        for d, score in sorted(entries, key=lambda item: abs(item[1] - 50), reverse=True):
            if (sym, d) in pruned:
                continue
            peaks.append({"symbol": sym, "date": d, "score": score})
            for direction in (-1, 1):
                walk = d + timedelta(days=direction)
                while walk in date_set and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += timedelta(days=direction)

    if peaks:
        assert_no_holdout_leak([p["date"] for p in peaks], "wadj_put_wave simulated peaks")
    return peaks


def rate_key(w_days: int) -> str:
    return f"wr{w_days}"


def summarize(
    peaks: list[dict[str, Any]],
    barriers: dict[tuple[str, str, str], int],
    w_days: int,
) -> dict[str, Any]:
    key = rate_key(w_days)
    end = min(date.today(), date.fromisoformat(cutoff_iso()))
    summary: dict[str, Any] = {}
    for wlabel, days in WINDOWS.items():
        cutoff = end - timedelta(days=days)
        wpeaks = [p for p in peaks if p["date"] >= cutoff]
        rows: dict[str, dict[str, float | int | None]] = {}
        for label, threshold in CALL_TIERS:
            vals = [
                barriers.get((p["symbol"], p["date"].isoformat(), "low"))
                for p in wpeaks
                if p["score"] >= threshold
            ]
            vals = [v for v in vals if v is not None]
            rows[label] = {
                "n": len(vals),
                "wins": int(sum(vals)),
                key: (float(sum(vals)) / len(vals) * 100.0) if vals else None,
            }
        for label, threshold in PUT_TIERS:
            vals = [
                barriers.get((p["symbol"], p["date"].isoformat(), "high"))
                for p in wpeaks
                if p["score"] <= threshold
            ]
            vals = [v for v in vals if v is not None]
            rows[label] = {
                "n": len(vals),
                "wins": int(sum(vals)),
                key: (float(sum(vals)) / len(vals) * 100.0) if vals else None,
            }
        summary[wlabel] = {"n_peaks": len(wpeaks), "buckets": rows}
    return summary


def bucket(summary: dict[str, Any], wlabel: str, label: str) -> dict[str, Any]:
    return summary[wlabel]["buckets"][label]


def safe_delta_wr(new_row: dict[str, Any], base_row: dict[str, Any], key: str) -> float:
    if new_row[key] is None or base_row[key] is None:
        return 0.0
    return float(new_row[key]) - float(base_row[key])


def nwr_delta(new_row: dict[str, Any], base_row: dict[str, Any]) -> float:
    n_base = max(int(base_row["n"]), 1)
    return (int(new_row["wins"]) - int(base_row["wins"])) / n_base * 100.0


def evaluate_summary(summary: dict[str, Any], baseline: dict[str, Any], w_days: int) -> dict[str, Any]:
    key = rate_key(w_days)
    utility = 0.0
    penalties: list[str] = []
    metrics: dict[str, Any] = {}

    for label, weight in PRIMARY_PUT_WEIGHTS.items():
        b = bucket(baseline, "5y", label)
        n = bucket(summary, "5y", label)
        d_wr = safe_delta_wr(n, b, key)
        d_nwr = nwr_delta(n, b)
        n_ratio = int(n["n"]) / max(int(b["n"]), 1)
        utility += weight * d_nwr
        metrics[f"{label}_dwr{w_days}_5y"] = d_wr
        metrics[f"{label}_dnwr_5y"] = d_nwr
        metrics[f"{label}_n_ratio_5y"] = n_ratio
        if n_ratio < 0.70:
            penalties.append(f"{label} N ratio {n_ratio:.1%} < 70%")
            utility -= (0.70 - n_ratio) * 20.0
        if d_wr < -0.50:
            penalties.append(f"{label} WR{w_days} regression {d_wr:+.2f}pp")
            utility += d_wr

    for label in CALL_GUARDS:
        b = bucket(baseline, "5y", label)
        n = bucket(summary, "5y", label)
        d_wr = safe_delta_wr(n, b, key)
        n_ratio = int(n["n"]) / max(int(b["n"]), 1)
        metrics[f"{label}_dwr{w_days}_5y"] = d_wr
        metrics[f"{label}_n_ratio_5y"] = n_ratio
        if d_wr < -0.50:
            penalties.append(f"{label} call WR{w_days} regression {d_wr:+.2f}pp")
            utility += d_wr * 0.5
        if n_ratio < 0.85:
            penalties.append(f"{label} call N ratio {n_ratio:.1%} < 85%")
            utility -= (0.85 - n_ratio) * 10.0

    # Sign consistency: primary put N*WR should not flip negative on 3y and 1y
    # when that tier has enough resolved examples.
    for label in ("<25", "<20", "<15"):
        signs = []
        for wlabel in ("1y", "3y", "5y"):
            b = bucket(baseline, wlabel, label)
            n = bucket(summary, wlabel, label)
            if int(b["n"]) >= 50 and int(n["n"]) >= 50:
                d = nwr_delta(n, b)
                signs.append((wlabel, d))
                metrics[f"{label}_dnwr_{wlabel}"] = d
        if any(d < -0.25 for _, d in signs):
            penalties.append(f"{label} negative N*WR window {signs}")
            utility -= sum(abs(d) for _, d in signs if d < -0.25) * 0.5

    return {"objective": utility, "penalties": penalties, "metrics": metrics}


def print_baseline(summary: dict[str, Any], w_days: int) -> None:
    key = rate_key(w_days)
    print("[baseline] legacy static weekly put scale", flush=True)
    for label, _ in CALL_TIERS + PUT_TIERS:
        row = bucket(summary, "5y", label)
        wr = "--" if row[key] is None else f"{row[key]:.2f}%"
        print(f"  {label:>4} N={row['n']:>6} WR{w_days}={wr:>8}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--lookback-days", type=int, default=1825)
    parser.add_argument("--budget", type=int, default=32)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--w-days", type=int, default=15, help="Barrier outcome horizon; defaults to WR15.")
    parser.add_argument("--symbols", default="", help="Comma-separated smoke-test symbols. Empty means full universe.")
    args = parser.parse_args()

    run_dir = args.run_dir or ROOT / ".codex" / "runs" / f"wadj_put_wave_bayes_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "bayes_results.jsonl"
    summary_path = run_dir / "bayes_summary.json"

    status_write(
        run_dir,
        phase="startup",
        state="running",
        completed=0,
        total=args.budget,
        started_at=now_iso(),
        cwd=str(ROOT),
        lookback_days=args.lookback_days,
        w_days=args.w_days,
        seed=args.seed,
    )

    try:
        since = date.today() - timedelta(days=args.lookback_days)
        barriers = load_barriers(since, args.w_days)

        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
        print(f"[simulator] loading contexts symbols={symbols or 'ALL'} lookback={args.lookback_days}", flush=True)
        from simulator import ScoreSimulator

        sim = ScoreSimulator(symbols=symbols, lookback_days=args.lookback_days, scoring_fn=None)
        cases = build_scoring_cases(sim, args.lookback_days, run_dir)

        status_write(run_dir, phase="baseline", state="running", completed=0, total=args.budget)
        set_wave(False)
        baseline = summarize(
            score_cases_to_peaks(cases, args.lookback_days, "baseline"),
            barriers,
            args.w_days,
        )
        print_baseline(baseline, args.w_days)

        optimizer = Optimizer(DIMENSIONS, base_estimator="ET", acq_func="EI", random_state=args.seed, n_initial_points=0)
        evaluated: list[dict[str, Any]] = []
        best: dict[str, Any] | None = None

        seeds = seed_params()
        for i in range(args.budget):
            if i < len(seeds):
                params = seeds[i].normalized()
                source = "seed"
            else:
                params = params_from_x(optimizer.ask())
                source = "bayes"

            status_write(
                run_dir,
                phase="bayes",
                state="running",
                completed=i,
                total=args.budget,
                current_candidate=asdict(params),
                best=best,
            )

            t0 = time.time()
            set_wave(True, params)
            peaks = score_cases_to_peaks(cases, args.lookback_days, f"eval {i + 1:03d}")
            candidate_summary = summarize(peaks, barriers, args.w_days)
            eval_info = evaluate_summary(candidate_summary, baseline, args.w_days)
            objective = float(eval_info["objective"])
            optimizer.tell(x_from_params(params), -objective)

            rec = {
                "idx": i + 1,
                "source": source,
                "params": asdict(params),
                "label": params.label(),
                "objective": objective,
                "penalties": eval_info["penalties"],
                "metrics": eval_info["metrics"],
                "n_peaks_5y": candidate_summary["5y"]["n_peaks"],
                "elapsed_sec": round(time.time() - t0, 2),
                "finished_at": now_iso(),
            }
            append_jsonl(results_path, rec)
            evaluated.append(rec)

            if best is None or rec["objective"] > best["objective"]:
                best = rec
            print(
                f"[eval {i + 1:03d}/{args.budget}] obj={objective:+.4f} "
                f"{params.label()} penalties={len(eval_info['penalties'])} "
                f"elapsed={rec['elapsed_sec']:.1f}s",
                flush=True,
            )

        ranked = sorted(evaluated, key=lambda r: r["objective"], reverse=True)
        summary = {
            "finished_at": now_iso(),
            "state": "done",
            "budget": args.budget,
            "lookback_days": args.lookback_days,
            "w_days": args.w_days,
            "baseline": baseline,
            "best": ranked[0] if ranked else None,
            "top10": ranked[:10],
            "result_path": str(results_path),
            "formula": "scale = floor + (peak - floor) * tanh((abs(raw_wadj) / width) ** power)",
            "notes": [
                "Baseline is the legacy static WEEKLY_PUT_SCALE path.",
                f"Objective is WR{args.w_days}/N-aware: primary put tiers use delta wins per baseline N, with call and N preservation penalties.",
                "This runner writes no Score rows.",
            ],
        }
        write_json(summary_path, summary)
        status_write(run_dir, phase="complete", state="done", completed=args.budget, total=args.budget, best=ranked[0] if ranked else None)
        write_json(run_dir / "done.json", {"finished_at": now_iso(), "summary": str(summary_path), "results": str(results_path)})
        print(f"[done] summary: {summary_path}", flush=True)
        return 0
    except Exception as exc:
        status_write(run_dir, phase="failed", state="failed", error=str(exc), completed=None, total=args.budget)
        write_json(run_dir / "failed.json", {"finished_at": now_iso(), "error": repr(exc), "summary": str(summary_path)})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
