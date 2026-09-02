"""Search for a smooth, call-preserving replacement for PUT_MACD_GATE.

This is a read-only calibration harness. It does not write Score rows.

The v60 cliff is evaluated first. A second "full MACD restored" score is then
computed by disabling the cliff. Candidate formulas only blend from the v60
score toward the full-MACD score when that move is put-beneficial and the v60
final score is still on the put side. This avoids the v62 failure mode where
low pre-MACD rows later became call candidates and were damaged by the change.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import duckdb
import polars as pl

import database.utils.scoring as scoring_mod
from database.models.core import AlgorithmVersion
from experiments._holdout import CUTOFF, assert_no_holdout_leak


CALL_THRESHOLDS = [95, 90, 85, 80, 75, 70]
PUT_THRESHOLDS = [30, 25, 20, 15, 10, 5]
ALL_BUCKETS = [f"{t}+" for t in CALL_THRESHOLDS] + [f"<{t}" for t in PUT_THRESHOLDS]
BASE_GATE = 45.0
FULL_MACD_GATE = -1_000_000.0


@dataclass(frozen=True, slots=True)
class Variant:
    label: str
    family: str
    scope_hi: int
    alpha: float
    width: float
    center: float | None = None
    max_drop: int | None = None
    bearish_only: bool = True
    macd_below: float | None = None


@dataclass(slots=True)
class RowFeature:
    symbol: str
    d: date
    base: int
    full: int
    pre_no_macd: float
    macd: float
    trend: int
    weight_info: dict[str, Any]
    vol_signal: str
    regime_mult: float


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tail_lines(path: Path, limit: int = 80) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    return lines[-limit:]


def _refresh_recent(run_dir: Path) -> None:
    log_path = run_dir / "run.log"
    recent_path = run_dir / "run.recent.log"
    lines = _tail_lines(log_path, 120)
    tmp = recent_path.with_suffix(".log.tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp.replace(recent_path)


def _status(run_dir: Path, **updates: Any) -> None:
    path = run_dir / "status.json"
    current: dict[str, Any] = {}
    if path.exists():
        try:
            current = _read_json(path)
        except Exception:
            current = {}
    current.update(updates)
    current["updated_at"] = _now()
    _write_json(path, current)
    _refresh_recent(run_dir)


class _Tee:
    def __init__(self, *streams: Any) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


@contextlib.contextmanager
def _logging(run_dir: Path):
    run_dir.mkdir(parents=True, exist_ok=True)
    log = (run_dir / "run.log").open("a", encoding="utf-8", buffering=1)
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(old_out, log)  # type: ignore[assignment]
    sys.stderr = _Tee(old_err, log)  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.stdout = old_out
        sys.stderr = old_err
        log.close()
        _refresh_recent(run_dir)


@contextlib.contextmanager
def _macd_gate(gate: float):
    old = scoring_mod.PUT_MACD_GATE
    scoring_mod.PUT_MACD_GATE = gate
    try:
        yield
    finally:
        scoring_mod.PUT_MACD_GATE = old


def _cache_root() -> Path:
    local = ROOT / ".cache"
    if (local / "barrier_outcomes.duckdb").exists():
        return local
    return Path(os.environ.get("TRADER_SHARED_CACHE", r"C:\Development\Trader\.cache"))


def _barrier_source() -> tuple[str, Path]:
    root = _cache_root()
    duck_path = root / "barrier_outcomes.duckdb"
    if duck_path.exists():
        return "duckdb", duck_path
    parquet_path = root / "barrier_outcomes.parquet"
    if parquet_path.exists():
        return "parquet", parquet_path
    sqlite_path = root / "barrier_outcomes.db"
    if sqlite_path.exists():
        return "sqlite", sqlite_path
    raise FileNotFoundError(f"barrier cache not found under {root}")


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    return date.fromisoformat(value)


def _load_barriers(start: date, end: date) -> pl.DataFrame:
    source_kind, source_path = _barrier_source()
    con = duckdb.connect(":memory:" if source_kind != "duckdb" else str(source_path), read_only=(source_kind == "duckdb"))
    try:
        if source_kind == "duckdb":
            return con.execute(
                """
                SELECT symbol, CAST(date AS VARCHAR) AS date, side, result
                FROM barrier_outcomes
                WHERE barrier_set = '30dte_opt'
                  AND w_days = 15
                  AND date BETWEEN ? AND ?
                """,
                [start.isoformat(), end.isoformat()],
            ).pl()
        if source_kind == "parquet":
            return con.execute(
                """
                SELECT symbol, CAST(date AS VARCHAR) AS date, side, result
                FROM read_parquet(?)
                WHERE barrier_set = '30dte_opt'
                  AND w_days = 15
                  AND date BETWEEN ? AND ?
                """,
                [str(source_path), start.isoformat(), end.isoformat()],
            ).pl()
        return con.execute(
            """
            SELECT symbol, CAST(date AS VARCHAR) AS date, side, result
            FROM sqlite_scan(?, 'barrier_outcomes')
            WHERE barrier_set = '30dte_opt'
              AND w_days = 15
              AND date BETWEEN ? AND ?
            """,
            [str(source_path), start.isoformat(), end.isoformat()],
        ).pl()
    finally:
        con.close()


def _dynamic_pre_no_macd(
    trend: float,
    bb: float,
    rsi: float,
    macd: float,
    stoch: float,
    ta: float,
    bb_pct: float | None,
) -> float:
    trend_bias = float(math.tanh((trend - 50.0) * scoring_mod.TREND_BIAS_SCALE))
    trend_strength = abs(trend_bias)
    trend_dominance = trend_strength ** scoring_mod.TREND_DOMINANCE_POWER

    if bb_pct is not None:
        bull_ext = max(0.0, (bb_pct - 0.80) / 0.20) * max(0.0, trend_bias)
        bear_ext = max(0.0, (0.20 - bb_pct) / 0.20) * max(0.0, -trend_bias)
        trend_dominance *= 1.0 - scoring_mod.BB_DOMINANCE_DAMP * (bull_ext + bear_ext)

    osc_avg = (rsi + macd) / 2.0
    bull_div = max(0.0, (40.0 - osc_avg) / 40.0) * max(0.0, trend_bias)
    bear_div = max(0.0, (osc_avg - 60.0) / 40.0) * max(0.0, -trend_bias)
    osc_divergence = bull_div + bear_div
    if bb_pct is not None:
        osc_divergence = min(1.0, osc_divergence * (1.0 + abs(bb_pct - 0.5)))
    trend_dominance *= 1.0 - scoring_mod.OSC_DOMINANCE_DAMP * osc_divergence

    d = trend_dominance
    w_trend = scoring_mod.W_TREND_BASE + scoring_mod.W_TREND_SLOPE * d
    w_bb = scoring_mod.W_BB_BASE
    w_rsi = scoring_mod.W_RSI_BASE + scoring_mod.W_RSI_SLOPE * d
    w_macd = scoring_mod.W_MACD_BASE + scoring_mod.W_MACD_SLOPE * d
    w_stoch = scoring_mod.W_STOCH_BASE
    w_ta = scoring_mod.W_TA_BASE + scoring_mod.W_TA_SLOPE * d

    scale_no_macd = 100.0 / (100.0 - w_macd)
    return 50.0 + (
        (trend - 50.0) * w_trend
        + (bb - 50.0) * w_bb
        + (rsi - 50.0) * w_rsi
        + (stoch - 50.0) * w_stoch
        + (ta - 50.0) * w_ta
    ) * scale_no_macd / 100.0


def _build_features(run_dir: Path, start: date, end: date) -> list[RowFeature]:
    from simulator import ScoreSimulator, _week_start
    from volume_amplifier import get_volume_multiplier_from_cache

    lookback_days = (date.today() - start).days
    _status(
        run_dir,
        state="running",
        phase="load_simulator",
        start=start.isoformat(),
        end=end.isoformat(),
        lookback_days=lookback_days,
    )
    sim = ScoreSimulator(symbols=None, lookback_days=lookback_days, scoring_fn=None)
    features: list[RowFeature] = []
    total_contexts = len(sim._contexts)
    t0 = time.time()

    for sym_idx, (sym, ctx) in enumerate(sim._contexts.items(), start=1):
        stock = ctx.stock
        prior_vol_signals: dict[date, tuple[str, float, int]] = {}
        for ind_row in ctx.ind_rows:
            d = ind_row.date
            if d < start or d > end:
                continue

            ind_cache = ctx.ind_cache_for(d)
            trend = stock.calculate_trend_score(d, _ind_cache=ind_cache, _ph_cache=ctx.ph_map)
            if trend is None:
                continue

            ph = ctx.ph_map.get(d)
            pct_ema50 = None
            pct_ema200 = None
            if ph and ph.close and ind_row.ema_50:
                pct_ema50 = (float(ph.close) - float(ind_row.ema_50)) / float(ind_row.ema_50) * 100.0
            if ph and ph.close and ind_row.ema_200:
                pct_ema200 = (float(ph.close) - float(ind_row.ema_200)) / float(ind_row.ema_200) * 100.0

            rsi = stock.calculate_rsi_score(
                d,
                trend_score=trend,
                _ind_cache=ind_cache,
                _ph_cache=ctx.ph_map,
                macdh_raw=float(ind_row.macd_hist) if ind_row.macd_hist is not None else None,
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
            sig_str = min(abs(avg_dist) / 50.0, 1.0)
            turbo = 1.0 + 0.5 * sig_str * agreement
            ta = round(max(0, min(100, 50 + avg_dist * sig_str * (agreement ** 1.5) * turbo)))

            ws = ctx.ws_map.get(_week_start(d))
            pws = ctx.ws_map.get(_week_start(d) - timedelta(weeks=1))
            ws_rsi = ws.rsi if ws and ws.rsi and ws.macd else None
            ws_macd = ws.macd if ws and ws.rsi and ws.macd else None

            ph_slice = ctx.ph_slice_desc_for(d)
            vol_mult, vol_raw, vol_sig, vol_mag, blend_w, vol_target = get_volume_multiplier_from_cache(
                d, ph_slice, prior_vol_signals, ctx.earnings_set
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

            kwargs = dict(
                bb_pct=bb_pct,
                pct_from_ema50=pct_ema50,
                pct_from_ema200=pct_ema200,
                ws_trend=ws.trend if ws else None,
                ws_rsi=ws_rsi,
                ws_macd=ws_macd,
                prev_ws_trend=pws.trend if pws else None,
                prev_ws_rsi=pws.rsi if pws else None,
                prev_ws_macd=pws.macd if pws else None,
                vol_mult=vol_mult,
                vol_raw=vol_raw,
                vol_sig=vol_sig,
                vol_mag=vol_mag,
                blend_w=blend_w,
                vol_target=vol_target,
                raw_rsi=float(ind_row.rsi) if ind_row.rsi is not None else None,
                raw_stoch=float(ind_row.stoch) if ind_row.stoch is not None else None,
                macdh_raw=float(ind_row.macd_hist) if ind_row.macd_hist is not None else None,
                regime_multiplier=sim._regime_map.get(d),
                mis_stress=sim._mis_stress_map.get(d, 0.0),
                days_to_earnings=days_to_earnings,
                ret_10d_sigma=ctx.ret10d_sigma_map.get(d),
                mcap_b=ctx.mcap_b,
                kijun_pct=ctx.kijun_pct_map.get(d),
                wv_force1=ctx.wv_force1_map.get(d),
                score_date=d,
            )

            pre_no_macd = _dynamic_pre_no_macd(
                float(trend), float(bb), float(rsi), float(macd), float(stoch), float(ta), bb_pct
            )

            with _macd_gate(BASE_GATE):
                base, _, weight_info = scoring_mod.compute_overall_score(
                    trend, bb, rsi, macd, stoch, ta, **kwargs
                )
            if base is None:
                continue

            with _macd_gate(FULL_MACD_GATE):
                full, _, _ = scoring_mod.compute_overall_score(
                    trend, bb, rsi, macd, stoch, ta, **kwargs
                )
            if full is None:
                continue

            regime_mult = float(sim._regime_map.get(d) or 1.0)
            features.append(RowFeature(
                symbol=sym,
                d=d,
                base=int(base),
                full=int(full),
                pre_no_macd=float(pre_no_macd),
                macd=float(macd),
                trend=int(trend),
                weight_info=weight_info or {},
                vol_signal=str(vol_sig),
                regime_mult=regime_mult,
            ))
            prior_vol_signals[d] = (vol_sig, vol_mag, vol_raw)

        if sym_idx % 25 == 0 or sym_idx == total_contexts:
            _status(
                run_dir,
                state="running",
                phase="build_features",
                completed=sym_idx,
                total=total_contexts,
                rows=len(features),
                elapsed_s=round(time.time() - t0, 1),
                current=sym,
            )
            print(f"[features] {sym_idx}/{total_contexts} symbols, rows={len(features)}", flush=True)

    print(f"[features] built {len(features)} rows in {time.time() - t0:.1f}s", flush=True)
    _write_feature_profile(run_dir, features, start, end)
    return features


def _write_feature_profile(run_dir: Path, features: list[RowFeature], start: date, end: date) -> None:
    rows = []
    for f in features:
        if not (start <= f.d <= end):
            continue
        delta = f.full - f.base
        rows.append({
            "symbol": f.symbol,
            "date": f.d.isoformat(),
            "base": f.base,
            "full": f.full,
            "pre_bin": int(math.floor(f.pre_no_macd / 2.5) * 2.5),
            "pre_no_macd": f.pre_no_macd,
            "macd_bin": (
                "<35" if f.macd < 35 else
                "35-44" if f.macd < 45 else
                "45-54" if f.macd < 55 else
                "55-64" if f.macd < 65 else
                "65+"
            ),
            "full_minus_base": delta,
            "put_beneficial": delta < 0,
            "base_put": f.base <= 25,
            "base_call": f.base >= 75,
        })
    df = pl.DataFrame(rows)
    if df.is_empty():
        return
    assert_no_holdout_leak(df, context="macd_gradient feature profile")
    prof = (
        df
        .filter(pl.col("pre_no_macd") < 55.0)
        .group_by(["pre_bin", "macd_bin", "base_put", "base_call"])
        .agg([
            pl.len().alias("rows"),
            pl.col("put_beneficial").sum().alias("put_beneficial_rows"),
            pl.col("full_minus_base").mean().alias("avg_full_minus_base"),
            pl.col("full_minus_base").min().alias("min_full_minus_base"),
            pl.col("full_minus_base").max().alias("max_full_minus_base"),
        ])
        .sort(["pre_bin", "macd_bin", "base_put", "base_call"])
    )
    prof_path = run_dir / "macd_gradient_level_profile.csv"
    prof.write_csv(prof_path)
    _write_json(run_dir / "macd_gradient_level_profile.json", {
        "rows": df.height,
        "profile_csv": str(prof_path),
        "profile": prof.to_dicts(),
    })


def _smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _sigmoid(x: float) -> float:
    if x >= 40:
        return 1.0
    if x <= -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _factor(v: Variant, pre_no_macd: float) -> float:
    if pre_no_macd >= BASE_GATE:
        return 1.0
    width = max(0.001, float(v.width))
    if v.family == "left_smooth":
        return _smoothstep((pre_no_macd - (BASE_GATE - width)) / width)
    if v.family == "linear":
        return max(0.0, min(1.0, (pre_no_macd - (BASE_GATE - width)) / width))
    if v.family == "sigmoid":
        center = BASE_GATE if v.center is None else float(v.center)
        return _sigmoid((pre_no_macd - center) / width)
    if v.family == "gaussian":
        center = BASE_GATE if v.center is None else float(v.center)
        return math.exp(-0.5 * ((pre_no_macd - center) / width) ** 2)
    raise ValueError(f"unsupported family: {v.family}")


def _candidate_score(f: RowFeature, v: Variant) -> int:
    if v.label == "baseline_cliff":
        return f.base
    if f.pre_no_macd >= BASE_GATE:
        return f.base
    if f.base > v.scope_hi:
        return f.base
    if v.macd_below is not None and f.macd >= v.macd_below:
        return f.base
    delta = f.full - f.base
    if v.bearish_only and delta >= 0:
        return f.base
    factor = max(0.0, min(1.0, v.alpha * _factor(v, f.pre_no_macd)))
    if factor <= 0.0:
        return f.base
    raw = f.base + factor * delta
    if v.max_drop is not None:
        raw = max(float(f.base - v.max_drop), raw)
    return int(max(0, min(100, round(raw))))


def _build_variants(family: str, limit: int | None = None) -> list[Variant]:
    variants = [Variant("baseline_cliff", "baseline", scope_hi=25, alpha=0.0, width=1.0)]

    if family in ("broad", "all"):
        for scope in (25, 30, 35, 40):
            for width in (2.0, 4.0, 6.0, 8.0, 12.0, 16.0):
                for alpha in (0.25, 0.5, 0.75, 1.0):
                    variants.append(Variant(
                        f"left_s{scope}_w{width:g}_a{alpha:g}",
                        "left_smooth", scope, alpha, width,
                    ))
        for scope in (25, 30, 35, 40, 50):
            for center in (39.0, 41.0, 43.0, 44.5):
                for width in (1.5, 3.0, 5.0):
                    for alpha in (0.5, 1.0):
                        variants.append(Variant(
                            f"sig_s{scope}_c{center:g}_w{width:g}_a{alpha:g}",
                            "sigmoid", scope, alpha, width, center=center,
                        ))
        for scope in (25, 30, 35, 40):
            for center in (34.0, 38.0, 42.0, 44.0):
                for width in (2.0, 4.0, 7.0):
                    for alpha in (0.5, 1.0):
                        variants.append(Variant(
                            f"gauss_s{scope}_c{center:g}_w{width:g}_a{alpha:g}",
                            "gaussian", scope, alpha, width, center=center,
                        ))

    if family in ("capped", "all"):
        for scope in (25, 30, 35, 40):
            for width in (4.0, 8.0, 12.0):
                for alpha in (0.75, 1.0, 1.25):
                    for max_drop in (4, 8, 12):
                        variants.append(Variant(
                            f"cap_s{scope}_w{width:g}_a{alpha:g}_d{max_drop}",
                            "left_smooth", scope, alpha, width, max_drop=max_drop,
                        ))

    if family in ("pure", "all"):
        for scope in (25, 30, 35, 40):
            for width in (2.0, 4.0, 8.0, 12.0):
                for alpha in (0.1, 0.25, 0.5):
                    variants.append(Variant(
                        f"pure_s{scope}_w{width:g}_a{alpha:g}",
                        "left_smooth", scope, alpha, width,
                        bearish_only=False,
                    ))

    if family not in ("broad", "capped", "pure", "all"):
        raise ValueError(f"unsupported family: {family}")
    return variants if limit is None else variants[:limit]


def _score_peaks(features: Iterable[RowFeature], variant: Variant, start: date, end: date) -> pl.DataFrame:
    by_symbol: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for f in features:
        if f.d < start or f.d > end:
            continue
        score = _candidate_score(f, variant)
        if score >= 70 or score <= 25:
            by_symbol[f.symbol].append((f.d, score))

    rows: list[dict[str, Any]] = []
    one_day = timedelta(days=1)
    for sym, entries in by_symbol.items():
        date_set = {d for d, _ in entries}
        pruned: set[date] = set()
        for d, score in sorted(entries, key=lambda x: abs(x[1] - 50), reverse=True):
            if d in pruned:
                continue
            rows.append({
                "symbol": sym,
                "date": d.isoformat(),
                "score": score,
                "side": "low" if score >= 50 else "high",
            })
            for step in (-one_day, one_day):
                walk = d + step
                while walk in date_set and walk not in pruned:
                    pruned.add(walk)
                    walk += step

    if not rows:
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.String, "score": pl.Int64, "side": pl.String})
    df = pl.DataFrame(rows)
    assert_no_holdout_leak(df, context=f"macd_gradient peaks {variant.label}")
    return df


def _wr_summary(peaks: pl.DataFrame, barriers: pl.DataFrame) -> dict[str, Any]:
    if peaks.is_empty():
        return {"peaks": 0, "joined": 0, "buckets": {}}
    joined = peaks.join(barriers, on=["symbol", "date", "side"], how="inner")
    buckets: dict[str, Any] = {}

    def bucket_stats(label: str, sub: pl.DataFrame) -> None:
        resolved = sub.filter(pl.col("result").is_not_null())
        n = resolved.height
        wins = int(resolved.filter(pl.col("result") == 1).height)
        buckets[label] = {
            "n": int(sub.height),
            "resolved": int(n),
            "wins": wins,
            "wr15": None if n == 0 else 100.0 * wins / n,
        }

    for threshold in CALL_THRESHOLDS:
        bucket_stats(f"{threshold}+", joined.filter(pl.col("score") >= threshold))
    for threshold in PUT_THRESHOLDS:
        bucket_stats(f"<{threshold}", joined.filter(pl.col("score") <= threshold))
    return {"peaks": int(peaks.height), "joined": int(joined.height), "buckets": buckets}


def _score_changes(features: Iterable[RowFeature], variant: Variant, start: date, end: date) -> dict[str, Any]:
    changed = lifted = damped = 0
    deltas: list[int] = []
    call_drop = {str(t): 0 for t in CALL_THRESHOLDS}
    call_promote = {str(t): 0 for t in CALL_THRESHOLDS}
    put_drop = {str(t): 0 for t in PUT_THRESHOLDS}
    put_promote = {str(t): 0 for t in PUT_THRESHOLDS}
    touched_put_beneficial = 0
    touched_non_beneficial = 0

    for f in features:
        if f.d < start or f.d > end:
            continue
        new_score = _candidate_score(f, variant)
        old_score = f.base
        if old_score == new_score:
            continue
        delta = int(new_score) - int(old_score)
        changed += 1
        lifted += int(delta > 0)
        damped += int(delta < 0)
        deltas.append(delta)
        touched_put_beneficial += int(f.full < f.base)
        touched_non_beneficial += int(f.full >= f.base)
        for t in CALL_THRESHOLDS:
            call_drop[str(t)] += int(old_score >= t > new_score)
            call_promote[str(t)] += int(old_score < t <= new_score)
        for t in PUT_THRESHOLDS:
            put_promote[str(t)] += int(old_score > t >= new_score)
            put_drop[str(t)] += int(old_score <= t < new_score)

    return {
        "changed": changed,
        "lifted": lifted,
        "damped": damped,
        "avg_delta": None if not deltas else sum(deltas) / len(deltas),
        "min_delta": None if not deltas else min(deltas),
        "max_delta": None if not deltas else max(deltas),
        "call_drop": call_drop,
        "call_promote": call_promote,
        "put_drop": put_drop,
        "put_promote": put_promote,
        "touched_put_beneficial": touched_put_beneficial,
        "touched_non_beneficial": touched_non_beneficial,
    }


def _bucket_deltas(metrics: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rb = metrics.get("buckets", {})
    bb = baseline.get("buckets", {})
    for bucket in ALL_BUCKETS:
        r = rb.get(bucket, {})
        b = bb.get(bucket, {})
        wr_r = r.get("wr15")
        wr_b = b.get("wr15")
        out[bucket] = {
            "resolved_delta": int((r.get("resolved") or 0) - (b.get("resolved") or 0)),
            "wins_delta": int((r.get("wins") or 0) - (b.get("wins") or 0)),
            "wr15_delta": None if wr_r is None or wr_b is None else wr_r - wr_b,
        }
    return out


def _objective(result: dict[str, Any], baseline: dict[str, Any]) -> float:
    deltas = result["bucket_deltas"]
    score = 0.0
    for bucket, wr_w, win_w in (
        ("<25", 2.2, 0.35),
        ("<20", 1.7, 0.30),
        ("<15", 1.4, 0.25),
        ("<10", 0.7, 0.12),
    ):
        d = deltas[bucket]
        wrd = d["wr15_delta"] or 0.0
        score += wr_w * wrd + win_w * d["wins_delta"]
        if wrd < -0.25:
            score -= 12.0 * abs(wrd)
    for bucket in ("75+", "80+", "85+", "90+", "95+"):
        d = deltas[bucket]
        wrd = d["wr15_delta"] or 0.0
        if d["resolved_delta"] < 0:
            score -= 200.0 * abs(d["resolved_delta"])
        if d["wins_delta"] < 0:
            score -= 300.0 * abs(d["wins_delta"])
        if wrd < -0.01:
            score -= 200.0 * abs(wrd)
    changes = result["score_changes"]
    score -= 500.0 * sum(changes["call_drop"].values())
    score += 0.001 * changes["changed"]
    return score


def _passes_guardrails(result: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    changes = result["score_changes"]
    deltas = result["bucket_deltas"]
    if result["label"] == "baseline_cliff":
        return False, ["baseline"]
    if changes["changed"] <= 0:
        reasons.append("no_score_change")
    for t in ("75", "80", "85", "90", "95"):
        if changes["call_drop"].get(t, 0) != 0:
            reasons.append(f"call_score_drop_{t}")
    for bucket in ("75+", "80+", "85+", "90+", "95+"):
        d = deltas[bucket]
        if d["resolved_delta"] < 0:
            reasons.append(f"{bucket}_n_down")
        if d["wins_delta"] < 0:
            reasons.append(f"{bucket}_wins_down")
        if d["wr15_delta"] is not None and d["wr15_delta"] < -0.05:
            reasons.append(f"{bucket}_wr_down")
    for bucket in ("<25", "<20", "<15"):
        d = deltas[bucket]
        if d["wins_delta"] < 0:
            reasons.append(f"{bucket}_wins_down")
        if d["wr15_delta"] is not None and d["wr15_delta"] < -0.25:
            reasons.append(f"{bucket}_wr_down")
    if result["objective"] <= 0:
        reasons.append("objective_non_positive")
    return len(reasons) == 0, reasons


def _run_sweep(run_dir: Path, start: date, end: date, family: str, limit: int | None) -> dict[str, Any]:
    _status(run_dir, state="running", phase="load_barriers")
    barriers = _load_barriers(start, end)
    features = _build_features(run_dir, start, end)
    variants = _build_variants(family, limit)
    _status(
        run_dir,
        state="running",
        phase="sweep",
        completed=0,
        total=len(variants),
        feature_rows=len(features),
        barrier_rows=barriers.height,
        variant_family=family,
    )
    print(f"[sweep] variants={len(variants)} feature_rows={len(features)}", flush=True)

    baseline_variant = variants[0]
    baseline_peaks = _score_peaks(features, baseline_variant, start, end)
    baseline_metrics = _wr_summary(baseline_peaks, barriers)
    baseline_result = {
        "label": baseline_variant.label,
        "variant": asdict(baseline_variant),
        "metrics": baseline_metrics,
        "bucket_deltas": _bucket_deltas(baseline_metrics, baseline_metrics),
        "score_changes": _score_changes(features, baseline_variant, start, end),
        "objective": 0.0,
        "passes_guardrails": False,
        "guardrail_reasons": ["baseline"],
    }

    results: list[dict[str, Any]] = [baseline_result]
    with (run_dir / "sweep_results.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(baseline_result, default=str) + "\n")

    t0 = time.time()
    for idx, variant in enumerate(variants[1:], start=2):
        vt0 = time.time()
        peaks = _score_peaks(features, variant, start, end)
        metrics = _wr_summary(peaks, barriers)
        changes = _score_changes(features, variant, start, end)
        result = {
            "label": variant.label,
            "variant": asdict(variant),
            "elapsed_s": time.time() - vt0,
            "metrics": metrics,
            "bucket_deltas": _bucket_deltas(metrics, baseline_metrics),
            "score_changes": changes,
        }
        result["objective"] = _objective(result, baseline_metrics)
        ok, reasons = _passes_guardrails(result)
        result["passes_guardrails"] = ok
        result["guardrail_reasons"] = reasons
        results.append(result)
        with (run_dir / "sweep_results.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, default=str) + "\n")

        if idx % 10 == 0 or idx == len(variants):
            ranked_so_far = sorted(results, key=lambda r: r["objective"], reverse=True)[:20]
            best = ranked_so_far[0]
            _write_json(run_dir / "sweep_results.json", {"results": results})
            _write_json(run_dir / "sweep_ranked.partial.json", {"ranked": ranked_so_far})
            _status(
                run_dir,
                state="running",
                phase="sweep",
                completed=idx,
                total=len(variants),
                current=variant.label,
                best=best["label"],
                best_objective=round(best["objective"], 4),
                elapsed_s=round(time.time() - t0, 1),
            )
        if idx % 25 == 0 or result["passes_guardrails"]:
            b = metrics["buckets"]
            d = result["bucket_deltas"]
            print(
                "[{}/{}] {} obj={:+.3f} pass={} <25 WRd={} Wd={} Nd={} <15 WRd={} Wd={} 75+ Nd={} Wd={}".format(
                    idx,
                    len(variants),
                    variant.label,
                    result["objective"],
                    result["passes_guardrails"],
                    None if d["<25"]["wr15_delta"] is None else round(d["<25"]["wr15_delta"], 3),
                    d["<25"]["wins_delta"],
                    d["<25"]["resolved_delta"],
                    None if d["<15"]["wr15_delta"] is None else round(d["<15"]["wr15_delta"], 3),
                    d["<15"]["wins_delta"],
                    d["75+"]["resolved_delta"],
                    d["75+"]["wins_delta"],
                ),
                flush=True,
            )

    ranked = sorted(results, key=lambda r: r["objective"], reverse=True)
    survivors = [r for r in ranked if r.get("passes_guardrails")]
    payload = {
        "ranked": ranked,
        "survivors": survivors,
        "variant_count": len(results),
        "feature_rows": len(features),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "finished_at": _now(),
    }
    _write_json(run_dir / "sweep_results.json", {"results": results})
    _write_json(run_dir / "sweep_ranked.json", payload)
    _write_json(run_dir / "survivors.json", {"survivors": survivors})
    _status(
        run_dir,
        state="running",
        phase="sweep_complete",
        completed=len(variants),
        total=len(variants),
        survivors=len(survivors),
        ranked=str(run_dir / "sweep_ranked.json"),
    )
    return payload


def _active_version_meta() -> dict[str, Any]:
    try:
        version = AlgorithmVersion.get_active_scores_version()
        return {
            "version_id": version.id,
            "production_label": version.production_label,
            "git_commit": version.git_commit,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _git_commit() -> str | None:
    import subprocess

    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return None


def run(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    start = _parse_date(args.start, CUTOFF - timedelta(days=365 * 5))
    end = _parse_date(args.end, CUTOFF)
    if end > CUTOFF and os.environ.get("HOLDOUT_DISABLE", "0") != "1":
        raise ValueError(f"end {end} exceeds calibration cutoff {CUTOFF}")

    _write_json(run_dir / "launch.json", {
        "started_at": _now(),
        "pid": os.getpid(),
        "cwd": str(ROOT),
        "argv": sys.argv,
        "python": sys.executable,
        "git_commit": _git_commit(),
        "active_version": _active_version_meta(),
        "env": {
            "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING"),
            "PYTHONUTF8": os.environ.get("PYTHONUTF8"),
            "TRADER_SHARED_CACHE": os.environ.get("TRADER_SHARED_CACHE"),
        },
        "expected_outputs": [
            str(run_dir / "sweep_ranked.json"),
            str(run_dir / "survivors.json"),
            str(run_dir / "done.json"),
            str(run_dir / "failed.json"),
        ],
    })
    (run_dir / "pid.txt").write_text(str(os.getpid()), encoding="utf-8")
    _status(run_dir, state="running", phase="start", started_at=_now())
    print(f"[launch] run_dir={run_dir}", flush=True)
    print(f"[launch] start={start} end={end} family={args.family} limit={args.limit}", flush=True)
    print(f"[launch] active_version={_active_version_meta()}", flush=True)
    source_kind, source_path = _barrier_source()
    print(f"[launch] barrier_source={source_kind}:{source_path}", flush=True)

    payload = _run_sweep(run_dir, start, end, args.family, args.limit)
    _write_json(run_dir / "done.json", {
        "finished_at": _now(),
        "state": "done",
        "variant_count": payload["variant_count"],
        "survivor_count": len(payload["survivors"]),
        "sweep_ranked": str(run_dir / "sweep_ranked.json"),
        "survivors": str(run_dir / "survivors.json"),
        "profile": str(run_dir / "macd_gradient_level_profile.json"),
    })
    _status(run_dir, state="done", phase="done", completed=payload["variant_count"], total=payload["variant_count"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--family", default="broad", choices=["broad", "capped", "pure", "all"])
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    with _logging(run_dir):
        try:
            run(args)
            return 0
        except Exception as exc:
            print("[failed]", exc, flush=True)
            traceback.print_exc()
            _write_json(run_dir / "failed.json", {
                "finished_at": _now(),
                "state": "failed",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            })
            _status(run_dir, state="failed", phase="failed", error=str(exc))
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
