"""Bayesian refinement for a daily CONVICTION volume wave replacement.

This is an experiment-local score-stage proxy. It does not modify production
scoring. The tested hypothesis is that daily CONVICTION should not scale
monotonically with volume magnitude: low/moderate conviction can confirm a
setup, while high magnitude plus extension/impulse/dollar-volume expansion is
often exhaustion.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import random
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl
from skopt import Optimizer
from skopt.space import Real

from experiments._holdout import assert_no_holdout_leak, pre_cutoff_filter
from experiments.mcd_cwwd_wvd_recal.participation_wave import (
    CALL_BUCKETS,
    CALL_TIERS,
    _extract_peaks,
    _row_dicts,
)


LOCAL_CACHE = ROOT / ".cache" / "mcd_cwwd_wvd_recal"
TRAIN_CONTEXT = LOCAL_CACHE / "domain_context_v46_from20200101_min60.parquet"
VALIDATION_CONTEXT = LOCAL_CACHE / "domain_context_v46_from20160101_to20201231_min60.parquet"

PERIODS = {
    "2020_2021": ("2020-01-01", "2021-12-31"),
    "2022": ("2022-01-01", "2022-12-31"),
    "2023_2024": ("2023-01-01", "2024-12-31"),
    "2025_2026": ("2025-01-01", "2026-05-14"),
}


@dataclass(frozen=True)
class ConvictionWaveParams:
    lift_k: float
    lift_target: float
    lift_peak: float
    lift_width: float
    lift_gate_lo: float
    lift_gate_hi: float
    lift_power: float
    max_lift: float
    damp_k: float
    damp_target: float
    damp_mag_mid: float
    damp_mag_slope: float
    ema_mid: float
    ema_slope: float
    impulse_mid: float
    impulse_slope: float
    dv_mid: float
    dv_slope: float
    damp_gate_lo: float
    damp_gate_hi: float
    damp_power: float
    max_dampen: float
    score_fade_family: float = 0.0  # 0 none, 1 smoothstep, 2 log, 3 logistic
    score_fade_lo: float = 82.0
    score_fade_hi: float = 90.0
    score_fade_k: float = 6.0
    weekly_mix: float = 0.0
    weekly_base: float = 0.75
    weekly_constructive_k: float = 0.25
    weekly_climax_k: float = 0.45
    weekly_peak: float = 0.0
    weekly_width: float = 0.08
    weekly_climax_thresh: float = 0.05
    weekly_climax_sat: float = 0.15

    def label(self) -> str:
        fade_family = _fade_family_name(self.score_fade_family)
        gov = "wg0" if self.weekly_mix <= 0.02 else f"wg{self.weekly_mix:.2f}"
        return (
            f"lk{self.lift_k:.2f}_lt{self.lift_target:.1f}_lp{self.lift_peak:.2f}_"
            f"lw{self.lift_width:.2f}_lg{self.lift_gate_lo:.0f}-{self.lift_gate_hi:.0f}_"
            f"dk{self.damp_k:.2f}_dt{self.damp_target:.1f}_mm{self.damp_mag_mid:.2f}_"
            f"ema{self.ema_mid:.1f}_imp{self.impulse_mid:.1f}_dv{self.dv_mid:.1f}_"
            f"dg{self.damp_gate_lo:.0f}-{self.damp_gate_hi:.0f}_"
            f"sf{fade_family}{self.score_fade_lo:.0f}-{self.score_fade_hi:.0f}_"
            f"{gov}"
        )


DIMENSIONS = [
    Real(0.00, 0.75, name="lift_k"),
    Real(74.0, 84.0, name="lift_target"),
    Real(0.00, 0.25, name="lift_peak"),
    Real(0.04, 0.40, name="lift_width"),
    Real(58.0, 72.0, name="lift_gate_lo"),
    Real(68.0, 83.0, name="lift_gate_hi"),
    Real(0.6, 2.6, name="lift_power"),
    Real(0.0, 10.0, name="max_lift"),
    Real(0.00, 1.55, name="damp_k"),
    Real(58.0, 76.0, name="damp_target"),
    Real(0.15, 0.82, name="damp_mag_mid"),
    Real(2.0, 12.0, name="damp_mag_slope"),
    Real(8.0, 42.0, name="ema_mid"),
    Real(0.04, 0.24, name="ema_slope"),
    Real(0.35, 2.80, name="impulse_mid"),
    Real(0.7, 4.5, name="impulse_slope"),
    Real(-0.15, 1.15, name="dv_mid"),
    Real(0.8, 5.0, name="dv_slope"),
    Real(68.0, 86.0, name="damp_gate_lo"),
    Real(74.0, 96.0, name="damp_gate_hi"),
    Real(0.7, 3.0, name="damp_power"),
    Real(0.0, 18.0, name="max_dampen"),
    Real(0.0, 3.0, name="score_fade_family"),
    Real(80.0, 85.0, name="score_fade_lo"),
    Real(87.0, 93.0, name="score_fade_hi"),
    Real(1.2, 12.0, name="score_fade_k"),
    Real(0.0, 1.0, name="weekly_mix"),
    Real(0.40, 1.00, name="weekly_base"),
    Real(0.0, 0.45, name="weekly_constructive_k"),
    Real(0.0, 0.85, name="weekly_climax_k"),
    Real(-0.04, 0.08, name="weekly_peak"),
    Real(0.04, 0.20, name="weekly_width"),
    Real(0.02, 0.12, name="weekly_climax_thresh"),
    Real(0.06, 0.28, name="weekly_climax_sat"),
]
PARAM_NAMES = [d.name for d in DIMENSIONS]

DEFAULT_PARAM_VALUES = {
    "score_fade_family": 0.0,
    "score_fade_lo": 82.0,
    "score_fade_hi": 90.0,
    "score_fade_k": 6.0,
    "weekly_mix": 0.0,
    "weekly_base": 0.75,
    "weekly_constructive_k": 0.25,
    "weekly_climax_k": 0.45,
    "weekly_peak": 0.0,
    "weekly_width": 0.08,
    "weekly_climax_thresh": 0.05,
    "weekly_climax_sat": 0.15,
}


def _fade_family_name(value: float) -> str:
    code = int(round(max(0.0, min(3.0, float(value)))))
    return ("none", "smooth", "log", "logistic")[code]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _sigmoid_expr(x: pl.Expr, mid: float, slope: float) -> pl.Expr:
    z = ((x - mid) * slope).clip(-40.0, 40.0)
    return 1.0 / (1.0 + (-z).exp())


def _score_gate(score: pl.Expr, lo: float, hi: float, power: float) -> pl.Expr:
    span = max(1.0, hi - lo)
    return (((score - lo) / span).clip(0.0, 1.0) ** max(0.1, power))


def _params_from_x(x: list[float]) -> ConvictionWaveParams:
    values = {name: float(value) for name, value in zip(PARAM_NAMES, x)}
    if values["lift_gate_hi"] <= values["lift_gate_lo"] + 2.0:
        values["lift_gate_hi"] = values["lift_gate_lo"] + 2.0
    if values["damp_gate_hi"] <= values["damp_gate_lo"] + 2.0:
        values["damp_gate_hi"] = values["damp_gate_lo"] + 2.0
    if values["score_fade_hi"] <= values["score_fade_lo"] + 2.0:
        values["score_fade_hi"] = values["score_fade_lo"] + 2.0
    values["lift_width"] = max(0.02, values["lift_width"])
    values["weekly_width"] = max(0.02, values["weekly_width"])
    values["weekly_climax_sat"] = max(0.03, values["weekly_climax_sat"])
    values["max_lift"] = max(0.0, values["max_lift"])
    values["max_dampen"] = max(0.0, values["max_dampen"])
    values["score_fade_family"] = int(round(max(0.0, min(3.0, values["score_fade_family"]))))
    values["weekly_mix"] = max(0.0, min(1.0, values["weekly_mix"]))
    return ConvictionWaveParams(**values)


def _x_from_params(params: dict[str, Any]) -> list[float]:
    return [float(params.get(name, DEFAULT_PARAM_VALUES.get(name, 0.0))) for name in PARAM_NAMES]


def load_context(path: Path, *, validation: bool = False) -> pl.DataFrame:
    df = pl.read_parquet(path)
    df = pre_cutoff_filter(df)
    assert_no_holdout_leak(df, f"daily_volume_conviction_wave/{path.name}")
    if validation:
        df = df.filter(pl.col("date") < "2020-01-01")
    if "wv_force1" not in df.columns:
        df = df.with_columns(pl.lit(None).alias("wv_force1"))
    return df.with_columns(
        [
            pl.col("date").cast(pl.Utf8),
            pl.col("volume_signal").fill_null("NEUTRAL"),
            pl.col("volume_magnitude").fill_null(0.0),
            pl.col("pct_from_ema50").fill_null(0.0),
            pl.col("price_impulse_sigma").fill_null(0.0),
            pl.col("log_dv_expansion").fill_null(0.0),
            pl.col("wv_force1").is_not_null().alias("wv_force1_present"),
            pl.col("wv_force1").fill_null(0.0),
            pl.col("win_3d").cast(pl.Float64),
            pl.col("win_5d").cast(pl.Float64),
            pl.col("win_7d").cast(pl.Float64),
            pl.col("win_15d").cast(pl.Float64),
            pl.col("win_30d").cast(pl.Float64),
            pl.col("opt_result_15").cast(pl.Float64),
        ]
    )


def _score_fade_guard(score: pl.Expr, p: ConvictionWaveParams) -> pl.Expr:
    family = _fade_family_name(p.score_fade_family)
    if family == "none":
        return pl.lit(1.0)

    span = max(1.0, p.score_fade_hi - p.score_fade_lo)
    x = ((score - p.score_fade_lo) / span).clip(0.0, 1.0)

    if family == "smooth":
        fade = x * x * (3.0 - 2.0 * x)
    elif family == "log":
        k = max(0.1, p.score_fade_k)
        fade = (1.0 + k * x).log() / math.log1p(k)
    else:
        k = max(0.5, p.score_fade_k)
        raw = 1.0 / (1.0 + (-(x - 0.5) * k).exp())
        lo = 1.0 / (1.0 + math.exp(0.5 * k))
        hi = 1.0 / (1.0 + math.exp(-0.5 * k))
        fade = ((raw - lo) / max(1e-9, hi - lo)).clip(0.0, 1.0)

    return (1.0 - fade).clip(0.0, 1.0)


def _tanh_expr(x: pl.Expr) -> pl.Expr:
    z = x.clip(-40.0, 40.0)
    return 2.0 / (1.0 + (-2.0 * z).exp()) - 1.0


def _weekly_authority(score_delta_guard: pl.Expr, p: ConvictionWaveParams) -> pl.Expr:
    if p.weekly_mix <= 0.02:
        return score_delta_guard

    wv = pl.col("wv_force1")
    bell = (-(((wv - p.weekly_peak) / max(0.02, p.weekly_width)) ** 2)).exp()
    excess = (wv - p.weekly_climax_thresh).clip(0.0, None) / max(0.03, p.weekly_climax_sat)
    climax = _tanh_expr(excess)
    raw_authority = (
        p.weekly_base
        + p.weekly_constructive_k * bell
        - p.weekly_climax_k * climax
    ).clip(0.25, 1.10)
    authority = (1.0 - p.weekly_mix) + p.weekly_mix * raw_authority
    authority = pl.when(pl.col("wv_force1_present")).then(authority).otherwise(1.0)
    return score_delta_guard * authority


def apply_conviction_wave(df: pl.DataFrame, p: ConvictionWaveParams) -> pl.DataFrame:
    conviction = pl.col("volume_signal") == "CONVICTION"
    score = pl.col("overall").cast(pl.Float64)
    mag = pl.col("volume_magnitude").clip(0.0, 1.0)

    lift_bell = (-(((mag - p.lift_peak) / p.lift_width) ** 2)).exp()
    lift_gate = _score_gate(score, p.lift_gate_lo, p.lift_gate_hi, p.lift_power)

    high_mag = _sigmoid_expr(mag, p.damp_mag_mid, p.damp_mag_slope)
    ema_ext = _sigmoid_expr(pl.col("pct_from_ema50").clip(0.0, None), p.ema_mid, p.ema_slope)
    impulse_ext = _sigmoid_expr(pl.col("price_impulse_sigma"), p.impulse_mid, p.impulse_slope)
    dv_ext = _sigmoid_expr(pl.col("log_dv_expansion"), p.dv_mid, p.dv_slope)
    exhaustion = high_mag * (0.25 + 0.25 * ema_ext + 0.25 * impulse_ext + 0.25 * dv_ext)
    damp_gate = _score_gate(score, p.damp_gate_lo, p.damp_gate_hi, p.damp_power)

    lift = (
        pl.when(conviction & (score < p.lift_target))
        .then((p.lift_k * lift_gate * lift_bell * (1.0 - exhaustion) * (p.lift_target - score)).clip(0.0, p.max_lift))
        .otherwise(0.0)
    )
    dampen = (
        pl.when(conviction & (score > p.damp_target))
        .then((p.damp_k * damp_gate * exhaustion * (score - p.damp_target)).clip(0.0, p.max_dampen))
        .otherwise(0.0)
    )
    raw_delta = lift - dampen
    score_guard = _score_fade_guard(score, p)
    delta_guard = _weekly_authority(score_guard, p)
    adjusted_delta = raw_delta * delta_guard
    return df.with_columns(
        [
            lift.alias("dcw_lift"),
            dampen.alias("dcw_dampen"),
            raw_delta.alias("dcw_raw_delta"),
            score_guard.alias("dcw_score_guard"),
            delta_guard.alias("dcw_delta_guard"),
            adjusted_delta.alias("dcw_delta"),
            (score + adjusted_delta).round().clip(0, 100).cast(pl.Int64).alias("new_score"),
        ]
    )


def _wr(df: pl.DataFrame, col: str) -> tuple[float | None, int]:
    resolved = df.filter(pl.col(col).is_not_null())
    n = resolved.height
    if n == 0:
        return None, 0
    return 100.0 * resolved.filter(pl.col(col) == 1).height / n, n


def _wr_list(vals: list[Any]) -> tuple[float | None, int]:
    resolved = [v for v in vals if v is not None]
    if not resolved:
        return None, 0
    return 100.0 * sum(1 for v in resolved if int(v) == 1) / len(resolved), len(resolved)


def _wins(wr: float | None, n: int) -> float:
    return 0.0 if wr is None else wr * n / 100.0


def _tier_metrics(df: pl.DataFrame, score_col: str) -> dict[str, Any]:
    tiers: dict[str, Any] = {}
    for tier in CALL_TIERS:
        sub = df.filter(pl.col(score_col) >= tier)
        wr7, n7 = _wr(sub, "win_7d")
        wr15, n15 = _wr(sub, "win_15d")
        opt, nopt = _wr(sub, "opt_result_15")
        tiers[f"{tier}+"] = {
            "n": sub.height,
            "wr7": wr7,
            "wr7_n": n7,
            "wr15": wr15,
            "wr15_n": n15,
            "opt15": opt,
            "opt15_n": nopt,
        }
    return tiers


def _peak_eval_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    peaks = _extract_peaks(rows)
    tiers = {}
    for tier in CALL_TIERS:
        sub = [r for r in peaks if r["new_score"] >= tier]
        wr7, n7 = _wr_list([r.get("win_7d") for r in sub])
        wr15, n15 = _wr_list([r.get("win_15d") for r in sub])
        opt, nopt = _wr_list([r.get("opt_result_15") for r in sub])
        tiers[f"{tier}+"] = {
            "n": len(sub),
            "wr7": wr7,
            "wr7_n": n7,
            "wr15": wr15,
            "wr15_n": n15,
            "opt15": opt,
            "opt15_n": nopt,
        }

    buckets = {}
    for lo, hi in CALL_BUCKETS:
        sub = [r for r in peaks if lo <= r["new_score"] <= hi]
        wr7, n7 = _wr_list([r.get("win_7d") for r in sub])
        wr15, n15 = _wr_list([r.get("win_15d") for r in sub])
        buckets[f"{lo}+" if hi == 100 else f"{lo}-{hi}"] = {
            "n": len(sub),
            "wr7": wr7,
            "wr7_n": n7,
            "wr15": wr15,
            "wr15_n": n15,
        }
    return {"tiers": tiers, "buckets": buckets}


def _row_bucket_metrics(df: pl.DataFrame, score_col: str) -> dict[str, Any]:
    buckets: dict[str, Any] = {}
    for lo, hi in CALL_BUCKETS:
        sub = df.filter((pl.col(score_col) >= lo) & (pl.col(score_col) <= hi))
        wr7, n7 = _wr(sub, "win_7d")
        buckets[f"{lo}+" if hi == 100 else f"{lo}-{hi}"] = {"n": sub.height, "wr7": wr7, "wr7_n": n7}
    return buckets


def _baseline_fast(df: pl.DataFrame) -> dict[str, Any]:
    return {
        "tiers": _tier_metrics(df, "overall"),
        "buckets": _row_bucket_metrics(df, "overall"),
    }


def _tier_deltas(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tier in [f"{t}+" for t in CALL_TIERS]:
        c = candidate["tiers"][tier]
        b = baseline["tiers"][tier]
        n_base = b["wr7_n"] or 0
        n_new = c["wr7_n"] or 0
        out[tier] = {
            "n_delta": n_new - n_base,
            "n_delta_pct": None if n_base == 0 else 100.0 * (n_new - n_base) / n_base,
            "wr7_delta": None if c["wr7"] is None or b["wr7"] is None else c["wr7"] - b["wr7"],
            "wr15_delta": None if c["wr15"] is None or b["wr15"] is None else c["wr15"] - b["wr15"],
            "opt15_delta": None if c["opt15"] is None or b["opt15"] is None else c["opt15"] - b["opt15"],
            "wr7_wins_delta": _wins(c["wr7"], n_new) - _wins(b["wr7"], n_base),
            "wr15_wins_delta": _wins(c["wr15"], c["wr15_n"] or 0) - _wins(b["wr15"], b["wr15_n"] or 0),
        }
    return out


def _period_fast(df: pl.DataFrame, varied: pl.DataFrame, baseline_by_period: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, (start, stop) in PERIODS.items():
        sub = varied.filter((pl.col("date") >= start) & (pl.col("date") <= stop))
        cand = {"tiers": _tier_metrics(sub, "new_score"), "buckets": _row_bucket_metrics(sub, "new_score")}
        out[label] = {
            "tiers": cand["tiers"],
            "deltas": _tier_deltas(cand, baseline_by_period[label]),
        }
    return out


def _changed_stats(varied: pl.DataFrame) -> dict[str, Any]:
    changed = (pl.col("new_score") != pl.col("overall"))
    lifted = pl.col("new_score") > pl.col("overall")
    damped = pl.col("new_score") < pl.col("overall")
    promoted_70 = (pl.col("overall") < 70) & (pl.col("new_score") >= 70)
    promoted_75 = (pl.col("overall") < 75) & (pl.col("new_score") >= 75)
    dropped_75 = (pl.col("overall") >= 75) & (pl.col("new_score") < 75)
    out: dict[str, Any] = {}
    for label, pred in {
        "changed": changed,
        "lifted": lifted,
        "damped": damped,
        "promoted_70": promoted_70,
        "promoted_75": promoted_75,
        "dropped_75": dropped_75,
    }.items():
        sub = varied.filter(pred)
        wr7, n7 = _wr(sub, "win_7d")
        wr15, n15 = _wr(sub, "win_15d")
        opt, nopt = _wr(sub, "opt_result_15")
        out[label] = {
            "rows": sub.height,
            "wr7": wr7,
            "wr7_n": n7,
            "wr15": wr15,
            "wr15_n": n15,
            "opt15": opt,
            "opt15_n": nopt,
            "avg_delta": None if sub.is_empty() else sub["dcw_delta"].mean(),
        }
    return out


def _fast_eval(
    df: pl.DataFrame,
    baseline: dict[str, Any],
    baseline_by_period: dict[str, Any],
    params: ConvictionWaveParams,
    primary_window: int = 7,
) -> dict[str, Any]:
    varied = apply_conviction_wave(df, params)
    candidate = {"tiers": _tier_metrics(varied, "new_score"), "buckets": _row_bucket_metrics(varied, "new_score")}
    deltas = _tier_deltas(candidate, baseline)
    periods = _period_fast(df, varied, baseline_by_period)
    changed = _changed_stats(varied)

    score = 0.0
    primary_delta = f"wr{primary_window}_delta"
    primary_wins_delta = f"wr{primary_window}_wins_delta"
    primary_metric = f"wr{primary_window}"

    # Primary: improve CALL 75+/80+ without buying that with large N loss or
    # low-quality N expansion. The wins term is deliberately secondary; a large
    # N increase with a sharp WR drop should fail, not dominate.
    for tier, weights in {
        "70+": (0.55, 0.002, 0.15),
        "75+": (2.40, 0.006, 0.60),
        "80+": (1.80, 0.007, 0.42),
        "85+": (1.00, 0.005, 0.25),
    }.items():
        d = deltas[tier]
        score += weights[0] * (d[primary_delta] or 0.0)
        score += weights[1] * d[primary_wins_delta]
        score += weights[2] * (d["opt15_delta"] or 0.0)

    # N preservation penalties. This keeps a dampener from "winning" by simply
    # deleting useful call supply.
    n_floors = {"70+": -8.0, "75+": -12.0, "80+": -18.0, "85+": -25.0}
    for tier, floor in n_floors.items():
        n_pct = deltas[tier]["n_delta_pct"]
        if n_pct is not None and n_pct < floor:
            score -= (floor - n_pct) * 0.85

    wr_floors = {"70+": -0.50, "75+": -0.25, "80+": -0.25, "85+": -0.50}
    for tier, floor in wr_floors.items():
        wr_delta = deltas[tier][primary_delta]
        if wr_delta is not None and wr_delta < floor:
            score -= (floor - wr_delta) * {"70+": 10.0, "75+": 28.0, "80+": 22.0, "85+": 12.0}[tier]
        opt_delta = deltas[tier]["opt15_delta"]
        if opt_delta is not None and opt_delta < -0.50:
            score -= (-0.50 - opt_delta) * {"70+": 4.0, "75+": 12.0, "80+": 9.0, "85+": 5.0}[tier]

    n_ceilings = {"70+": 35.0, "75+": 35.0, "80+": 25.0, "85+": 20.0}
    for tier, ceiling in n_ceilings.items():
        n_pct = deltas[tier]["n_delta_pct"]
        wr_delta = deltas[tier][primary_delta] or 0.0
        if n_pct is not None and n_pct > ceiling and wr_delta < 0.25:
            score -= (n_pct - ceiling) * {"70+": 0.35, "75+": 0.90, "80+": 0.70, "85+": 0.45}[tier]

    # N * WR is a binding gate. A candidate can trade a little capacity for
    # quality at very high tiers, but it cannot be "optimal" if the expected
    # number of resolved winners drops materially.
    wins_floors = {"70+": -25.0, "75+": -10.0, "80+": -5.0, "85+": -2.0}
    for tier, floor in wins_floors.items():
        wins_delta = deltas[tier][primary_wins_delta]
        if wins_delta < floor:
            score -= (floor - wins_delta) * {"70+": 0.15, "75+": 0.45, "80+": 0.60, "85+": 1.75}[tier]

    # High-tier preservation: the daily-volume wave can help score admission
    # around 75/80, but it should not buy cleaner headline rates by deleting
    # the 90+ supply that already passed stronger structural tests.
    high_tier = deltas.get("90+")
    if high_tier:
        n_pct = high_tier["n_delta_pct"]
        if n_pct is not None and n_pct < -1.0:
            score -= (-1.0 - n_pct) * 10.0
        wins_delta = high_tier[primary_wins_delta]
        if wins_delta < -1.0:
            score -= (-1.0 - wins_delta) * 4.0
        opt_delta = high_tier["opt15_delta"]
        if opt_delta is not None and opt_delta < -0.25:
            score -= (-0.25 - opt_delta) * 8.0

    tier_85 = deltas.get("85+")
    if tier_85:
        n_pct = tier_85["n_delta_pct"]
        if n_pct is not None and n_pct < -3.0:
            score -= (-3.0 - n_pct) * 5.0

    # Bucket preservation: don't wreck the visible score ladder.
    bucket_breaches = []
    for bucket, base_row in baseline["buckets"].items():
        cand_row = candidate["buckets"][bucket]
        if (base_row.get("wr7_n") or 0) < 60 or (cand_row.get("wr7_n") or 0) < 60:
            continue
        delta = (cand_row.get("wr7") or 0.0) - (base_row.get("wr7") or 0.0)
        if delta < -0.60:
            bucket_breaches.append({"bucket": bucket, "delta_wr7_pp": delta, "n": cand_row.get("wr7_n")})
            score -= abs(delta) * 4.0

    # Period robustness: 75+ and 80+ should not depend on one market tape.
    period_breaches = []
    for period, row in periods.items():
        for tier in ("75+", "80+"):
            d = row["deltas"][tier]
            n_pct = d["n_delta_pct"]
            wr_delta = d[primary_delta]
            if wr_delta is not None and wr_delta < -0.75:
                period_breaches.append({"period": period, "tier": tier, f"wr{primary_window}_delta": wr_delta, "n_delta_pct": n_pct})
                score -= abs(wr_delta) * 3.0
            if n_pct is not None and n_pct < -22.0:
                period_breaches.append({"period": period, "tier": tier, f"wr{primary_window}_delta": wr_delta, "n_delta_pct": n_pct})
                score -= (-22.0 - n_pct) * 0.45

    # A useful replacement should move enough rows to matter, but not reshape
    # the whole score distribution.
    changed_n = changed["changed"]["rows"]
    if changed_n < 250:
        score -= (250 - changed_n) * 0.015
    if changed_n > 30000:
        score -= (changed_n - 30000) * 0.012

    # The moved cohorts should be directionally plausible.
    if changed["lifted"]["rows"] > 0 and (changed["lifted"][primary_metric] or 0.0) < (baseline["tiers"]["70+"][primary_metric] or 0.0) - 0.25:
        score -= ((baseline["tiers"]["70+"][primary_metric] or 0.0) - 0.25 - (changed["lifted"][primary_metric] or 0.0)) * 2.5
    if changed["promoted_75"]["rows"] > 50 and (changed["promoted_75"][primary_metric] or 0.0) < (baseline["tiers"]["75+"][primary_metric] or 0.0) - 0.50:
        score -= ((baseline["tiers"]["75+"][primary_metric] or 0.0) - 0.50 - (changed["promoted_75"][primary_metric] or 0.0)) * 10.0
    if changed["promoted_70"]["rows"] > 100 and (changed["promoted_70"][primary_metric] or 0.0) < (baseline["tiers"]["70+"][primary_metric] or 0.0) - 0.50:
        score -= ((baseline["tiers"]["70+"][primary_metric] or 0.0) - 0.50 - (changed["promoted_70"][primary_metric] or 0.0)) * 5.0
    if changed["dropped_75"]["rows"] > 0 and (changed["dropped_75"][primary_metric] or 100.0) > (baseline["tiers"]["75+"][primary_metric] or 0.0):
        score -= ((changed["dropped_75"][primary_metric] or 0.0) - (baseline["tiers"]["75+"][primary_metric] or 0.0)) * 0.75

    # The replacement thesis is a participation/timing lift with protection,
    # not a pure high-score dampener. Avoid rediscovering dampener-only variants
    # that were already falsified in the weekly-volume work.
    if changed["lifted"]["rows"] == 0 and changed["damped"]["rows"] > 100:
        score -= 25.0
    if changed["promoted_75"]["rows"] == 0 and (deltas["75+"][primary_wins_delta] <= 0):
        score -= 12.0

    score -= len(bucket_breaches) * 3.0
    score -= len(period_breaches) * 2.0

    return {
        "label": params.label(),
        "params": asdict(params),
        "objective": score,
        "candidate_tiers": candidate["tiers"],
        "candidate_buckets": candidate["buckets"],
        "deltas": deltas,
        "periods": periods,
        "changed": changed,
        "primary_window": primary_window,
        "bucket_breaches": bucket_breaches,
        "period_breaches": period_breaches,
    }


def _full_peak_eval(df: pl.DataFrame, baseline_peaks: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    params = ConvictionWaveParams(**rec["params"])
    varied = apply_conviction_wave(df, params)
    candidate = _peak_eval_rows(_row_dicts(varied))
    deltas = _tier_deltas(candidate, baseline_peaks)
    return {
        "label": rec["label"],
        "params": rec["params"],
        "objective": rec["objective"],
        "fast_deltas": rec["deltas"],
        "fast_changed": rec["changed"],
        "peak_candidate_tiers": candidate["tiers"],
        "peak_candidate_buckets": candidate["buckets"],
        "peak_deltas": deltas,
        "poet": candidate.get("poet", []),
    }


def _seed_records() -> list[dict[str, Any]]:
    prior_top = {
        "lift_k": 0.5198469416444662,
        "lift_target": 81.0911476399029,
        "lift_peak": 0.06611558819673823,
        "lift_width": 0.1908983852167872,
        "lift_gate_lo": 71.33358562733558,
        "lift_gate_hi": 73.33358562733558,
        "lift_power": 2.093158486811798,
        "max_lift": 1.8295877189877643,
        "damp_k": 1.057649816167875,
        "damp_target": 71.95526412796062,
        "damp_mag_mid": 0.7060918609831665,
        "damp_mag_slope": 2.2730955657146428,
        "ema_mid": 31.088178269644477,
        "ema_slope": 0.0919698974947952,
        "impulse_mid": 2.618146835329802,
        "impulse_slope": 2.256019542067418,
        "dv_mid": 0.45401991216819637,
        "dv_slope": 2.654634404760605,
        "damp_gate_lo": 85.79234100950595,
        "damp_gate_hi": 87.79234100950595,
        "damp_power": 1.0641863960856655,
        "max_dampen": 2.746027894276995,
    }
    prior_softer = {
        "lift_k": 0.22230671477909253,
        "lift_target": 78.7223563641901,
        "lift_peak": 0.17020003781080095,
        "lift_width": 0.2587523738674531,
        "lift_gate_lo": 65.09089778486823,
        "lift_gate_hi": 74.00498755608264,
        "lift_power": 1.6379151777890169,
        "max_lift": 4.963958956693076,
        "damp_k": 0.27823138693998417,
        "damp_target": 68.83846387525155,
        "damp_mag_mid": 0.22094757557005648,
        "damp_mag_slope": 6.852374055379883,
        "ema_mid": 18.289403161161722,
        "ema_slope": 0.15169714523125688,
        "impulse_mid": 1.3853266395320705,
        "impulse_slope": 0.8069079097682053,
        "dv_mid": 0.3877636705553188,
        "dv_slope": 3.648016237477597,
        "damp_gate_lo": 84.53080774586252,
        "damp_gate_hi": 87.39755210279695,
        "damp_power": 2.9545581730644397,
        "max_dampen": 3.0258880186289043,
    }

    def guarded(base: dict[str, Any], **updates: float) -> ConvictionWaveParams:
        params = {**base, **DEFAULT_PARAM_VALUES, **updates}
        return ConvictionWaveParams(**params)

    seeds = [
        # No-op baseline shape.
        ConvictionWaveParams(
            lift_k=0.0,
            lift_target=78.0,
            lift_peak=0.05,
            lift_width=0.15,
            lift_gate_lo=62.0,
            lift_gate_hi=76.0,
            lift_power=1.0,
            max_lift=0.0,
            damp_k=0.0,
            damp_target=70.0,
            damp_mag_mid=0.55,
            damp_mag_slope=6.0,
            ema_mid=24.0,
            ema_slope=0.10,
            impulse_mid=1.3,
            impulse_slope=2.0,
            dv_mid=0.45,
            dv_slope=2.5,
            damp_gate_lo=75.0,
            damp_gate_hi=88.0,
            damp_power=1.4,
            max_dampen=0.0,
        ),
        # Empirical prior from the quick quantile check: low-magnitude
        # CONVICTION was strongest; high magnitude was weakest.
        ConvictionWaveParams(
            lift_k=0.25,
            lift_target=78.0,
            lift_peak=0.04,
            lift_width=0.16,
            lift_gate_lo=62.0,
            lift_gate_hi=75.0,
            lift_power=1.0,
            max_lift=4.0,
            damp_k=0.55,
            damp_target=68.0,
            damp_mag_mid=0.42,
            damp_mag_slope=7.0,
            ema_mid=18.0,
            ema_slope=0.12,
            impulse_mid=1.1,
            impulse_slope=2.2,
            dv_mid=0.25,
            dv_slope=2.8,
            damp_gate_lo=72.0,
            damp_gate_hi=86.0,
            damp_power=1.3,
            max_dampen=8.0,
        ),
        # v58 WR15 best, then guarded variants around the 90+ N problem.
        guarded(prior_top),
        guarded(prior_top, score_fade_family=1.0, score_fade_lo=82.0, score_fade_hi=88.0),
        guarded(prior_top, score_fade_family=2.0, score_fade_lo=82.0, score_fade_hi=90.0, score_fade_k=6.0),
        guarded(prior_top, score_fade_family=2.0, score_fade_lo=80.0, score_fade_hi=90.0, score_fade_k=3.0),
        guarded(
            prior_top,
            score_fade_family=2.0,
            score_fade_lo=82.0,
            score_fade_hi=90.0,
            score_fade_k=6.0,
            weekly_mix=0.75,
            weekly_base=0.70,
            weekly_constructive_k=0.35,
            weekly_climax_k=0.55,
        ),
        guarded(
            prior_softer,
            score_fade_family=2.0,
            score_fade_lo=82.0,
            score_fade_hi=90.0,
            score_fade_k=6.0,
            weekly_mix=0.75,
            weekly_base=0.75,
            weekly_constructive_k=0.25,
            weekly_climax_k=0.45,
        ),
    ]
    return [asdict(s) for s in seeds]


def _baseline_by_period(df: pl.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, (start, stop) in PERIODS.items():
        sub = df.filter((pl.col("date") >= start) & (pl.col("date") <= stop))
        out[label] = _baseline_fast(sub)
    return out


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "experiments" / "daily_volume_conviction_wave" / "runs" / f"bayes_refine_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    results_path = run_dir / "bayes_results.jsonl"
    summary_path = run_dir / "bayes_summary.json"

    _write_json(
        status_path,
        {
            "phase": "setup",
            "state": "running",
            "started_at": _now(),
            "run_dir": str(run_dir),
            "context": str(args.context),
            "validation_context": str(args.validation_context),
            "primary_window": args.primary_window,
        },
    )

    df = load_context(Path(args.context))
    baseline = _baseline_fast(df)
    baseline_by_period = _baseline_by_period(df)
    baseline_peaks = _peak_eval_rows(_row_dicts(df.with_columns(pl.col("overall").alias("new_score"), pl.lit(0.0).alias("dcw_lift"), pl.lit(0.0).alias("dcw_dampen"), pl.lit(0.0).alias("dcw_delta"))))

    optimizer = Optimizer(DIMENSIONS, base_estimator="ET", acq_func="EI", random_state=args.seed, n_initial_points=0)
    evaluated: list[dict[str, Any]] = []

    seed_xs = [_x_from_params(p) for p in _seed_records()]
    rng = random.Random(args.seed)
    while len(seed_xs) < max(args.initial_random, 1):
        seed_xs.append([rng.uniform(dim.low, dim.high) for dim in DIMENSIONS])

    total = len(seed_xs) + args.trials
    with results_path.open("w", encoding="utf-8") as fh:
        for x in seed_xs:
            params = _params_from_x(x)
            rec = _fast_eval(df, baseline, baseline_by_period, params, primary_window=args.primary_window)
            rec["source"] = "seed"
            evaluated.append(rec)
            optimizer.tell(x, -rec["objective"])
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()

        for i in range(1, args.trials + 1):
            x = optimizer.ask()
            params = _params_from_x(x)
            rec = _fast_eval(df, baseline, baseline_by_period, params, primary_window=args.primary_window)
            rec["source"] = "optimizer"
            evaluated.append(rec)
            optimizer.tell(x, -rec["objective"])
            fh.write(json.dumps(rec, default=str) + "\n")
            if i % args.status_every == 0 or i == args.trials:
                fh.flush()
                best = max(evaluated, key=lambda r: r["objective"])
                _write_json(
                    status_path,
                    {
                        "phase": "optimize",
                        "state": "running",
                        "primary_window": args.primary_window,
                        "completed": len(evaluated),
                        "total": total,
                        "best_so_far": {
                            "label": best["label"],
                            "objective": best["objective"],
                            "deltas": {
                                tier: best["deltas"][tier]
                                for tier in ("70+", "75+", "80+", "85+")
                            },
                            "changed": best["changed"],
                        },
                        "updated_at": _now(),
                        "results": str(results_path),
                        "summary": str(summary_path),
                    },
                )
                print(f"[opt] {len(evaluated)}/{total} best={best['label']} obj={best['objective']:.3f}", flush=True)

    ranked = sorted(evaluated, key=lambda r: r["objective"], reverse=True)
    top_full = [_full_peak_eval(df, baseline_peaks, rec) for rec in ranked[: args.full_gate_top]]

    validation: list[dict[str, Any]] = []
    validation_path = Path(args.validation_context)
    if validation_path.exists() and args.validation_top > 0:
        vdf = load_context(validation_path, validation=True)
        if not vdf.is_empty():
            vbase = _peak_eval_rows(_row_dicts(vdf.with_columns(pl.col("overall").alias("new_score"), pl.lit(0.0).alias("dcw_lift"), pl.lit(0.0).alias("dcw_dampen"), pl.lit(0.0).alias("dcw_delta"))))
            validation = [_full_peak_eval(vdf, vbase, rec) for rec in ranked[: args.validation_top]]

    summary = {
        "generated_at": _now(),
        "context": str(args.context),
        "validation_context": str(args.validation_context),
        "primary_window": args.primary_window,
        "scope_note": (
            "Experiment-local post-hoc score-stage proxy over row packets. "
            "A passing candidate still requires migration into staging scoring and production-equivalent recalc/portfolio validation."
        ),
        "trials": len(evaluated),
        "baseline_fast": baseline,
        "baseline_peaks": baseline_peaks,
        "top_fast": ranked[:50],
        "top_full_peak": top_full,
        "validation_pre2020": validation,
    }
    _write_json(summary_path, summary)
    _write_json(
        status_path,
        {
            "phase": "done",
            "state": "done",
            "primary_window": args.primary_window,
            "completed": len(evaluated),
            "total": len(evaluated),
            "finished_at": _now(),
            "summary": str(summary_path),
            "results": str(results_path),
        },
    )
    _write_json(run_dir / "done.json", {"state": "done", "finished_at": _now(), "summary": str(summary_path), "results": str(results_path)})
    print(f"[done] {summary_path}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=500)
    parser.add_argument("--initial-random", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260515)
    parser.add_argument("--status-every", type=int, default=25)
    parser.add_argument("--primary-window", type=int, choices=[7, 15], default=7)
    parser.add_argument("--full-gate-top", type=int, default=30)
    parser.add_argument("--validation-top", type=int, default=10)
    parser.add_argument("--context", default=str(TRAIN_CONTEXT))
    parser.add_argument("--validation-context", default=str(VALIDATION_CONTEXT))
    parser.add_argument("--run-dir")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
