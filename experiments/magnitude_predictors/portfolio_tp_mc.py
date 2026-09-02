"""Portfolio MC validation for magnitude-conditioned TP widening.

This is experiment-local by design. It imports the canonical 30 DTE MC engine,
then replaces only the in-process precompute functions so per-signal TP distance
can be widened from magnitude features. Production strategy_config and shipped
MC code remain untouched.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import polars as pl

from database.models.core import AlgorithmVersion

OUT_DIR = ROOT / ".cache" / "magnitude_predictors"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _norm(values: pd.Series) -> np.ndarray:
    arr = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(arr)
    out = np.zeros(len(arr), dtype=float)
    if finite.sum() < 50:
        return out
    p10 = np.nanpercentile(arr[finite], 10)
    p90 = np.nanpercentile(arr[finite], 90)
    if not math.isfinite(p10) or not math.isfinite(p90) or p10 == p90:
        return out
    out[finite] = np.clip((arr[finite] - p10) / (p90 - p10) * 2.0 - 1.0, -1.0, 1.0)
    return out


def _load_signal_features(lookback: int, feature_version_id: int | None = None) -> dict[tuple[Any, Any], dict[str, float]]:
    av = AlgorithmVersion.get_active_scores_version()
    version_id = feature_version_id if feature_version_id is not None else av.id
    path = OUT_DIR / f"signals_v{version_id}_{lookback}.parquet"
    if not path.exists():
        raise SystemExit(
            f"Missing {path}. Build it first:\n"
            f"  python experiments/magnitude_predictors/build_features.py {lookback}"
        )

    cols = ["symbol", "date", "side", "overall", "pct_above_ema200_mkt", "stoch_score"]
    df = pl.read_parquet(path, columns=cols).to_pandas()
    df["symbol_id"] = df["symbol"]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["is_call"] = df["side"].eq("call")
    df["stoch_edge"] = np.where(df["is_call"], df["stoch_score"], 100.0 - df["stoch_score"])

    call_mask = df["is_call"].to_numpy(dtype=bool)
    put_mask = ~call_mask
    df["call_breadth_pos"] = 0.0
    df["put_stoch_pos"] = 0.0
    if call_mask.any():
        df.loc[call_mask, "call_breadth_pos"] = np.maximum(
            _norm(df.loc[call_mask, "pct_above_ema200_mkt"]), 0.0
        )
    if put_mask.any():
        df.loc[put_mask, "put_stoch_pos"] = np.maximum(
            _norm(df.loc[put_mask, "stoch_edge"]), 0.0
        )

    out: dict[tuple[int, Any], dict[str, float]] = {}
    for r in df.itertuples(index=False):
        out[(r.symbol_id, r.date)] = {
            "overall": float(r.overall),
            "call_breadth_pos": float(r.call_breadth_pos),
            "put_stoch_pos": float(r.put_stoch_pos),
        }
    return out


def _variant_mult(variant: str, side: str, feature: dict[str, float] | None) -> float:
    if not feature or variant == "baseline":
        return 1.0

    overall = feature.get("overall", 50.0)

    def call_breadth(amp: float) -> float:
        return 1.0 + amp * feature.get("call_breadth_pos", 0.0)

    def put_stoch(amp: float) -> float:
        return 1.0 + amp * feature.get("put_stoch_pos", 0.0)

    mult = 1.0
    parts = variant.split("__")
    for part in parts:
        if side == "call" and part.startswith("call_breadth_widen_pos"):
            amp = int(part.removeprefix("call_breadth_widen_pos")) / 100.0
            mult *= call_breadth(amp)
        elif side == "call" and part.startswith("call_breadth_80_89_widen_pos"):
            if 80 <= overall < 90:
                amp = int(part.removeprefix("call_breadth_80_89_widen_pos")) / 100.0
                mult *= call_breadth(amp)
        elif side == "put" and part.startswith("put_stoch_widen_pos"):
            amp = int(part.removeprefix("put_stoch_widen_pos")) / 100.0
            mult *= put_stoch(amp)
    return float(np.clip(mult, 0.50, 1.50))


def _compute_call_dynamic(mc: Any, sym_bars: list, signal_date: Any, stressed: bool, tp_mult: float) -> dict[str, Any] | None:
    dates = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs = [b[2] for b in sym_bars]
    lows = [b[3] for b in sym_bars]
    opens = [b[4] for b in sym_bars]

    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None
    entry_price = closes[base_idx]
    if entry_price <= 0:
        return None
    vol = mc.realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    tp_sigma = (mc.TP_SIGMA_STRESS if stressed else mc.TP_SIGMA_BASE) * tp_mult
    sl_sigma = mc.SL_SIGMA_STRESS if stressed else mc.SL_SIGMA_BASE
    premium_pct = mc.PREMIUM_MULT * vol / 100
    tp_level = entry_price * (1 + tp_sigma * vol / 100)
    sl_level = entry_price * (1 - sl_sigma * vol / 100)
    end_idx = min(len(dates), base_idx + 1 + mc.HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind = "hard"
    exit_bar = mc.HOLD_DAYS
    fire_o = fire_c = fire_h = fire_l = None
    prem_stop = mc.PREM_STOP_LOSS
    if prem_stop < 0.0:
        from option_pricing import option_pnl_pct as _opn
    for i in range(base_idx + 1, end_idx):
        tp_hit = highs[i] >= tp_level
        sl_hit = lows[i] <= sl_level
        if tp_hit and sl_hit:
            kind, exit_bar = "both", i - base_idx
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            break
        if tp_hit:
            kind, exit_bar = "tp", i - base_idx
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            break
        if sl_hit:
            kind, exit_bar = "sl", i - base_idx
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            break
        if prem_stop < 0.0:
            bars_held = i - base_idx
            cur_pnl = _opn("call", closes[i], entry_price, bars_held, premium_pct, vega_ratio=1.0, delta=mc.DELTA)
            if cur_pnl <= prem_stop:
                kind, exit_bar = "prem", bars_held
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                break

    if kind == "hard":
        last_idx = min(base_idx + mc.HOLD_DAYS, len(dates) - 1)
        fire_o, fire_c, fire_h, fire_l = opens[last_idx], closes[last_idx], highs[last_idx], lows[last_idx]

    out = dict(
        kind=kind,
        exit_bar=exit_bar,
        side="call",
        stressed=stressed,
        premium_pct=premium_pct,
        vol=vol,
        entry=entry_price,
        signal_date=signal_date,
        fire_open=fire_o,
        fire_close=fire_c,
        fire_high=fire_h,
        fire_low=fire_l,
        fire_tp_level=tp_level,
        fire_sl_level=sl_level,
        tp_mult=tp_mult,
    )
    if kind == "sl" and mc.DEAD_HOLD_ENABLED:
        sl_fire_idx = base_idx + exit_bar
        out["dead_hold"] = mc._compute_dead_hold_call(
            highs, lows, closes, opens, sl_fire_idx, end_idx, entry_price, premium_pct, base_idx
        )
    return out


def _compute_put_dynamic(mc: Any, sym_bars: list, signal_date: Any, put_stressed: bool, tp_mult: float) -> dict[str, Any] | None:
    dates = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs = [b[2] for b in sym_bars]
    lows = [b[3] for b in sym_bars]
    opens = [b[4] for b in sym_bars]

    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None
    entry_price = closes[base_idx]
    if entry_price <= 0:
        return None
    vol = mc.realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    if put_stressed and mc.PUT_BREADTH_MODE != "none":
        sl_pct = mc.PUT_SL_STRESS
        tp_pct = mc.PUT_TP_STRESS
    else:
        sl_pct = mc.PUT_SL
        tp_pct = mc.PUT_TP
    tp_sigma = tp_pct * mc.PREMIUM_MULT / mc.DELTA * tp_mult
    tp_level = entry_price * (1 - tp_sigma * vol / 100)

    sl_schedule = list(mc.PUT_SL_STEPPED) if mc.PUT_SL_MODE == "stepped" else [(mc.HOLD_DAYS, sl_pct)]

    def sl_for_bar(bar: int) -> float:
        for max_bar, pct in sl_schedule:
            if bar <= max_bar:
                return pct
        return sl_schedule[-1][1]

    end_idx = min(len(dates), base_idx + 1 + mc.HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    sl_hold = mc._put_sl_hold_bars(signal_date)
    premium_pct = mc.PREMIUM_MULT * vol / 100
    kind = "hard"
    exit_bar = mc.HOLD_DAYS
    fire_o = fire_c = fire_h = fire_l = None
    fire_sl_level = None
    prem_stop = mc.PREM_STOP_LOSS
    if prem_stop < 0.0:
        from option_pricing import option_pnl_pct as _opn
    for i in range(base_idx + 1, end_idx):
        bar = i - base_idx
        sl_pct_t = sl_for_bar(bar)
        sl_sigma_t = abs(sl_pct_t) * mc.PREMIUM_MULT / mc.DELTA
        sl_level_t = entry_price * (1 + sl_sigma_t * vol / 100)
        tp_hit = lows[i] <= tp_level
        sl_hit = (highs[i] >= sl_level_t) and (bar > sl_hold)
        if tp_hit and sl_hit:
            kind, exit_bar = "both", bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        if tp_hit:
            kind, exit_bar = "tp", bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        if sl_hit:
            kind, exit_bar = "sl", bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        if prem_stop < 0.0 and bar > sl_hold:
            cur_pnl = _opn("put", closes[i], entry_price, bar, premium_pct, vega_ratio=1.0, delta=mc.DELTA)
            if cur_pnl <= prem_stop:
                kind, exit_bar = "prem", bar
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                fire_sl_level = sl_level_t
                break

    if kind == "hard":
        last_idx = min(base_idx + mc.HOLD_DAYS, len(dates) - 1)
        fire_o, fire_c, fire_h, fire_l = opens[last_idx], closes[last_idx], highs[last_idx], lows[last_idx]
        last_bar = last_idx - base_idx
        fire_sl_level = entry_price * (1 + abs(sl_for_bar(last_bar)) * mc.PREMIUM_MULT / mc.DELTA * vol / 100)

    out = dict(
        kind=kind,
        exit_bar=exit_bar,
        side="put",
        stressed=put_stressed,
        premium_pct=premium_pct,
        vol=vol,
        entry=entry_price,
        signal_date=signal_date,
        fire_open=fire_o,
        fire_close=fire_c,
        fire_high=fire_h,
        fire_low=fire_l,
        fire_tp_level=tp_level,
        fire_sl_level=fire_sl_level,
        tp_mult=tp_mult,
    )
    if kind == "sl" and mc.DEAD_HOLD_ENABLED:
        sl_fire_idx = base_idx + exit_bar
        out["dead_hold"] = mc._compute_dead_hold_put(
            highs, lows, closes, opens, sl_fire_idx, end_idx, entry_price, premium_pct, base_idx
        )
    return out


def _install_dynamic_precompute(mc: Any, variant: str, features: dict[tuple[Any, Any], dict[str, float]]) -> None:
    def precompute_outcomes(signals, ph, breadth_dates, breadth_map, ern_map=None, trading_days=None, count_map=None):
        outcomes = {}
        for sig in signals:
            sym_bars = ph.get(sig.symbol_id)
            if not sym_bars:
                continue
            stressed = mc.is_stressed(breadth_dates, breadth_map, sig.date, count_map=count_map)
            feat = features.get((sig.symbol_id, sig.date))
            mult = _variant_mult(variant, "call", feat)
            r = _compute_call_dynamic(mc, sym_bars, sig.date, stressed, mult)
            if r is None:
                continue
            mc._attach_earnings_span(r, (ern_map or {}).get(sig.symbol_id, []), trading_days)
            outcomes[(sig.symbol_id, sig.date)] = r
        return outcomes

    def precompute_put_outcomes(signals, ph, breadth_dates=None, breadth_map=None, ern_map=None, trading_days=None):
        outcomes = {}
        for sig in signals:
            sym_bars = ph.get(sig.symbol_id)
            if not sym_bars:
                continue
            put_stressed = False
            if mc.PUT_BREADTH_MODE != "none" and breadth_dates is not None and breadth_map is not None:
                put_stressed = mc._is_put_stressed(breadth_dates, breadth_map, sig.date)
            feat = features.get((sig.symbol_id, sig.date))
            mult = _variant_mult(variant, "put", feat)
            r = _compute_put_dynamic(mc, sym_bars, sig.date, put_stressed, mult)
            if r is None:
                continue
            mc._attach_earnings_span(r, (ern_map or {}).get(sig.symbol_id, []), trading_days)
            outcomes[(sig.symbol_id, sig.date)] = r
        return outcomes

    mc.precompute_outcomes = precompute_outcomes
    mc.precompute_put_outcomes = precompute_put_outcomes


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback", type=int, default=1825)
    parser.add_argument("--feature-version-id", type=int, default=0)
    parser.add_argument("--variants", default="baseline,call_breadth_widen_pos10,call_breadth_widen_pos20,call_breadth_80_89_widen_pos30,put_stoch_widen_pos10,put_stoch_widen_pos20,call_breadth_widen_pos10__put_stoch_widen_pos10,call_breadth_widen_pos20__put_stoch_widen_pos20")
    parser.add_argument("--windows", default="2022,2024,5y")
    parser.add_argument("--n-iter", type=int, default=100)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else OUT_DIR / "runs" / f"portfolio_tp_mc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    results_jsonl = run_dir / "results.jsonl"
    summary_csv = run_dir / "summary.csv"

    os.environ["MC_NO_DB_PERSIST"] = "1"
    os.environ["N_ITER_OVERRIDE"] = str(args.n_iter)
    os.environ["MC_WORKERS"] = str(args.workers)

    import monte_carlo as mc

    version = AlgorithmVersion.get_active_scores_version()
    features = _load_signal_features(args.lookback, args.feature_version_id or None)
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    wanted_windows = {w.strip() for w in args.windows.split(",") if w.strip()}
    windows = [w for w in mc.WINDOWS if w[0] in wanted_windows]
    if not windows:
        raise SystemExit(f"No matching windows for {sorted(wanted_windows)}")

    metadata = {
        "phase": "portfolio_tp_mc",
        "state": "running",
        "started_at": _now(),
        "updated_at": _now(),
        "cwd": str(ROOT),
        "run_dir": str(run_dir),
        "summary_csv": str(summary_csv),
        "results_jsonl": str(results_jsonl),
        "version_id": version.id,
        "version_data": dict(getattr(version, "__data__", {})),
        "feature_version_id": args.feature_version_id or version.id,
        "n_iter": args.n_iter,
        "workers": args.workers,
        "variants": variants,
        "windows": [w[0] for w in windows],
        "completed": 0,
        "total": len(variants) * len(windows),
    }
    _write_json(status_path, metadata)

    rows: list[dict[str, Any]] = []
    total = len(variants) * len(windows)
    completed = 0
    try:
        for variant in variants:
            _install_dynamic_precompute(mc, variant, features)
            for label, d_start, d_end in windows:
                metadata.update(
                    {
                        "state": "running",
                        "phase": f"{variant}:{label}",
                        "updated_at": _now(),
                        "completed": completed,
                        "current_variant": variant,
                        "current_window": label,
                    }
                )
                _write_json(status_path, metadata)
                t0 = time.time()
                res = mc.run_window(label, d_start, d_end, version)["seeded"]
                elapsed = time.time() - t0
                row = {
                    "variant": variant,
                    "window": label,
                    "elapsed_sec": elapsed,
                    **res,
                }
                rows.append(row)
                _append_jsonl(results_jsonl, row)
                pd.DataFrame(rows).to_csv(summary_csv, index=False)
                completed += 1
                metadata.update(
                    {
                        "updated_at": _now(),
                        "completed": completed,
                        "best_so_far": _best(rows),
                    }
                )
                _write_json(status_path, metadata)
        done = dict(metadata)
        done.update({"state": "done", "phase": "complete", "finished_at": _now(), "completed": total})
        _write_json(run_dir / "done.json", done)
        _write_json(status_path, done)
    except Exception as exc:
        failed = dict(metadata)
        failed.update(
            {
                "state": "failed",
                "phase": "failed",
                "finished_at": _now(),
                "completed": completed,
                "error": repr(exc),
            }
        )
        _write_json(run_dir / "failed.json", failed)
        _write_json(status_path, failed)
        raise


def _best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    five_y = [r for r in rows if r.get("window") == "5y" and r.get("variant") != "baseline"]
    if not five_y:
        return None
    return max(five_y, key=lambda r: (float(r.get("mean_ret", -1e99)), -float(r.get("worst_dd", 1e99))))


if __name__ == "__main__":
    main()
