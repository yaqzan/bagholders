"""OSK Stage-3 tilt -- z-score map builder (experiments/osk_tilt/).

Builds the 4 locked (source, lag) z-score maps used by tilt_runner.py:
  raw_lag0    -- z(skew) from .cache/osk_era/osk_era_ledger_65.parquet   (lag_days=0)
  raw_lag1    -- z(skew) from .cache/osk_era/osk_era_ledger_lag1.parquet (lag_days=1)
  resid_lag0  -- z(skew orthogonalized on stock_r20+stock_r60 via in-era OLS), lag0 ledger
  resid_lag1  -- z(skew orthogonalized on stock_r20+stock_r60 via in-era OLS), lag1 ledger

Methodology precedent (matches experiments/osk_era/FAITHFUL_PATH_RESULTS.md):
z-score reference population = the SKEW-COVERED rows of the given ledger file
(NOT restricted to 75+ -- the ledger's own score_min floor, 65 or 70, is the
population). This is a broader, more stable standardization population than
the narrow 75+ tilt-eligible band itself.

Residualization: OLS skew ~ a + b1*stock_r20 + b2*stock_r60 fit on the same
covered population (dropping rows missing stock_r20/stock_r60 from the
rel_strength ledger join), residual = skew - fitted, then z-score the residual
over that same (now doubly-filtered) population.

Read-only, local-parquet, no MySQL -- safe to run in the foreground.
"""
import json
import os
import sys
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import polars as pl

from experiments._holdout import assert_no_holdout_leak

OSK_ERA_DIR = os.path.join(_ROOT, ".cache", "osk_era")
RS_LEDGER = os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet")
OUT_DIR = os.path.join(_ROOT, ".cache", "osk_tilt")

LEDGER_PATHS = {
    "lag0": os.path.join(OSK_ERA_DIR, "osk_era_ledger_65.parquet"),
    "lag1": os.path.join(OSK_ERA_DIR, "osk_era_ledger_lag1.parquet"),
}


def _load_ledger(lag_key: str) -> pl.DataFrame:
    df = pl.read_parquet(LEDGER_PATHS[lag_key])
    assert_no_holdout_leak(df, context="osk_tilt")
    df = df.filter(pl.col("skew").is_not_null())
    return df


def _zscore(x: np.ndarray) -> tuple[np.ndarray, float, float]:
    mean = float(np.mean(x))
    std = float(np.std(x, ddof=0))
    if std <= 1e-12:
        return np.zeros_like(x), mean, std
    return (x - mean) / std, mean, std


def _ols_resid(skew: np.ndarray, r20: np.ndarray, r60: np.ndarray) -> tuple[np.ndarray, dict]:
    """OLS skew ~ a + b1*r20 + b2*r60. Returns (residuals, coef_dict)."""
    X = np.column_stack([np.ones_like(skew), r20, r60])
    coef, res, rank, sv = np.linalg.lstsq(X, skew, rcond=None)
    fitted = X @ coef
    resid = skew - fitted
    ss_tot = np.sum((skew - skew.mean()) ** 2)
    ss_res = np.sum(resid ** 2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return resid, {
        "intercept": float(coef[0]), "b_stock_r20": float(coef[1]), "b_stock_r60": float(coef[2]),
        "r2": float(r2), "n": int(len(skew)),
    }


def build_all() -> dict:
    """Returns {'raw_lag0': {...}, 'raw_lag1': {...}, 'resid_lag0': {...}, 'resid_lag1': {...}}
    each with keys: 'map' ({(symbol,date_str): z}), 'mean', 'std', 'n', 'coverage_pct', and
    (residualized only) 'ols'.
    """
    rs = pl.read_parquet(RS_LEDGER).select(
        "symbol", "date", "stock_r20", "stock_r60"
    )
    assert_no_holdout_leak(rs.filter(pl.col("date") <= "2026-06-15"), context="osk_tilt rs_ledger slice")

    out = {}
    for lag_key in ("lag0", "lag1"):
        led = _load_ledger(lag_key)
        n_total_rows_in_window = pl.read_parquet(LEDGER_PATHS[lag_key]).height

        # --- raw ---
        sym = led["symbol"].to_list()
        dt = [str(d) for d in led["date"].to_list()]
        skew = led["skew"].to_numpy().astype(float)
        z_raw, mean_raw, std_raw = _zscore(skew)
        raw_map = {(s, d): float(z) for s, d, z in zip(sym, dt, z_raw)}
        out[f"raw_{lag_key}"] = {
            "map": raw_map, "mean": mean_raw, "std": std_raw,
            "n": len(sym), "n_total_rows_in_window": n_total_rows_in_window,
            "coverage_pct": round(100.0 * len(sym) / max(n_total_rows_in_window, 1), 1),
        }

        # --- residualized: join stock_r20/stock_r60, drop missing, OLS, z-score resid ---
        led_j = led.join(rs, on=["symbol", "date"], how="left")
        led_j = led_j.filter(pl.col("stock_r20").is_not_null() & pl.col("stock_r60").is_not_null())
        sym_j = led_j["symbol"].to_list()
        dt_j = [str(d) for d in led_j["date"].to_list()]
        skew_j = led_j["skew"].to_numpy().astype(float)
        r20_j = led_j["stock_r20"].to_numpy().astype(float)
        r60_j = led_j["stock_r60"].to_numpy().astype(float)
        resid, ols_info = _ols_resid(skew_j, r20_j, r60_j)
        z_resid, mean_resid, std_resid = _zscore(resid)
        resid_map = {(s, d): float(z) for s, d, z in zip(sym_j, dt_j, z_resid)}
        out[f"resid_{lag_key}"] = {
            "map": resid_map, "mean": mean_resid, "std": std_resid,
            "n": len(sym_j), "n_total_rows_in_window": n_total_rows_in_window,
            "coverage_pct": round(100.0 * len(sym_j) / max(n_total_rows_in_window, 1), 1),
            "ols": ols_info,
        }
    return out


def _json_safe_summary(zmaps: dict) -> dict:
    return {
        k: {kk: vv for kk, vv in v.items() if kk != "map"} | {"map_size": len(v["map"])}
        for k, v in zmaps.items()
    }


if __name__ == "__main__":
    zmaps = build_all()
    summary = _json_safe_summary(zmaps)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "zmap_manifest.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
