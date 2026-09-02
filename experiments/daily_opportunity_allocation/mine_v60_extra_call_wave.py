"""Mine v60-only call toxicity and live-safe market-structure wave proxies.

Experiment-only research. This reads existing score rows, daily-state artifacts,
and price history. It does not write scores, strategy config, or algorithm
version metadata.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    mean_absolute_error,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.tree import DecisionTreeClassifier, export_text


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.core import AlgorithmVersion
from database.trader_database import DB
from experiments.daily_opportunity_allocation.mine_conditional_alpha import _add_derived
from experiments.daily_opportunity_allocation.validate_usable_policy_mc import _apply_v60_portfolio_env


DEFAULT_DAILY = ROOT / ".codex" / "runs" / "v60_daily_opportunity_smooth_20260519_082415" / "daily_state.csv"
DEFAULT_OUT = ROOT / ".codex" / "runs" / f"v60_extra_call_wave_{datetime.now():%Y%m%d_%H%M%S}"

WINDOWS: dict[str, tuple[date, date]] = {
    "2020-crash": (date(2020, 2, 18), date(2020, 4, 30)),
    "2022": (date(2022, 1, 1), date(2022, 12, 31)),
    "2024": (date(2024, 1, 1), date(2024, 12, 31)),
    "2025": (date(2025, 1, 1), date(2025, 12, 31)),
    "22-now": (date(2022, 1, 1), date(2026, 4, 24)),
    "5y": (date(2021, 4, 24), date(2026, 4, 24)),
}

BASE_SCORE_COLUMNS = [
    "bb",
    "trend",
    "volume",
    "rsi",
    "macd",
    "stoch",
    "technical_alignment",
    "overall",
    "daily_change",
    "price_target_growth",
    "growth_score",
    "pct_from_ema50",
    "pct_from_ema200",
    "bb_position",
    "score_velocity_7d",
    "regime_composite",
    "regime_multiplier",
    "volume_magnitude",
]

WEIGHT_KEYS = [
    "pre_regime",
    "pre_boost",
    "td",
    "trend",
    "bb",
    "rsi",
    "macd",
    "stoch",
    "ta",
    "w_comp",
    "w_bias",
    "w_mom",
    "w_adj",
    "w_adj_raw",
    "cont_lift",
    "cont_raw_lift",
    "cont_sig",
    "cwcf_dampen",
    "cwwd_dampen",
    "cswc_dampen",
    "scw_dampen",
    "scw_base_dampen",
    "scw_scalar",
    "scw_conf",
    "scw_raw_stoch",
    "scw_ext_idx",
    "scw_ext_taper",
    "mcd_dampen",
    "mcd_mcap_b",
    "ich_call_dampen",
    "wvd_lift",
    "wvd_dampen",
    "wv_force1",
    "ern_boost",
    "mis_stress",
    "put_regime_mult",
]

NESTED_WEIGHT_KEYS = {
    "sector_breadth_wave": ["delta", "crash", "bull", "pressure"],
    "daily_volume_authority_wave": ["delta", "lift", "dampen", "score_guard", "authority", "raw_delta", "wv_force1"],
}

DAILY_CANDIDATE_FEATURES = [
    "post_call_primary_n",
    "post_put_primary_n",
    "post_total_primary_n",
    "post_call_primary_alloc_demand",
    "post_put_primary_alloc_demand",
    "post_net_call_minus_put_demand",
    "post_call_primary_share",
    "post_put_primary_share",
    "post_call_max_sector_share",
    "post_call_sector_hhi",
    "open_call_n",
    "open_put_n",
    "open_put_share",
    "filled_call_n",
    "filled_put_n",
    "free_slots",
    "cash_pct",
    "open_premium_pct",
    "dd",
    "prev5_entry_tp_rate",
    "prev5_entry_call_tp_rate",
    "prev5_entry_avg_pnl_pct",
    "prev5_entry_trade_n",
    "prev5_dd",
    "dd_5d_delta",
    "breadth_breadth_score",
    "breadth_pct_above_ema50",
    "breadth_pct_above_ema200",
    "breadth_mcclellan_oscillator",
    "breadth_sector_etf_pct_above_ema50",
    "breadth_sector_etf_pct_above_ema200",
    "breadth_sector_etf_breadth_1d_change",
    "breadth_sector_etf_breadth_5d_change",
    "breadth_sector_etf_breadth_10d_change",
    "breadth_sector_etf_breadth_15d_change",
    "breadth_sector_etf_breadth_avg5",
    "breadth_sector_etf_breadth_10d_position",
    "breadth_sector_etf_breadth_30d_position",
    "breadth_sector_etf_market_wave_score",
    "breadth_sector_etf_market_wave_signed",
    "regime_vix_close",
    "regime_vix_10d_change",
    "regime_internal_breadth_score",
    "regime_vix_score",
    "regime_market_trend_score",
    "regime_regime_composite",
    "regime_regime_multiplier",
    "regime_spy_pct_from_ema50",
    "regime_spy_pct_from_ema200",
]

SIGNAL_LIVE_FEATURES = [
    "v60_bb",
    "v60_trend",
    "v60_volume",
    "v60_rsi",
    "v60_macd",
    "v60_stoch",
    "v60_technical_alignment",
    "v60_overall",
    "v60_pct_from_ema50",
    "v60_pct_from_ema200",
    "v60_bb_position",
    "v60_score_velocity_7d",
    "v60_regime_composite",
    "v60_regime_multiplier",
    "v60_volume_magnitude",
    "wi_pre_regime",
    "wi_pre_boost",
    "wi_td",
    "wi_w_comp",
    "wi_w_bias",
    "wi_w_mom",
    "wi_w_adj",
    "wi_w_adj_raw",
    "wi_cont_lift",
    "wi_cont_sig",
    "wi_cwcf_dampen",
    "wi_cwwd_dampen",
    "wi_cswc_dampen",
    "wi_scw_dampen",
    "wi_scw_scalar",
    "wi_scw_conf",
    "wi_mcd_dampen",
    "wi_wvd_lift",
    "wi_wvd_dampen",
    "wi_wv_force1",
    "wi_ern_boost",
    "wi_mis_stress",
    "wi_sector_breadth_wave_delta",
    "wi_sector_breadth_wave_crash",
    "wi_sector_breadth_wave_pressure",
    "wi_daily_volume_authority_wave_delta",
    "wi_daily_volume_authority_wave_authority",
]


@dataclass
class Status:
    out_dir: Path
    started_at: str
    phase: str = "starting"
    state: str = "running"
    completed: int = 0
    total: int = 8
    current: str = ""

    def write(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        payload = {
            "phase": self.phase,
            "state": self.state,
            "started_at": self.started_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "completed": self.completed,
            "total": self.total,
            "current": self.current,
            "out_dir": str(self.out_dir),
            "run_log": str(self.out_dir / "run.log"),
        }
        tmp = self.out_dir / "extra_call_wave_status.json.tmp"
        final = self.out_dir / "extra_call_wave_status.json"
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(final)


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "None"):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _safe_feature_name(name: str) -> str:
    return (
        name.replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("%", "pct")
    )


def _parse_weight(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _flatten_weight(raw: Any, prefix: str = "wi") -> dict[str, float]:
    info = _parse_weight(raw)
    out: dict[str, float] = {}
    for key in WEIGHT_KEYS:
        val = _float(info.get(key))
        if val is not None:
            out[f"{prefix}_{key}"] = val
    for parent, children in NESTED_WEIGHT_KEYS.items():
        node = info.get(parent)
        if isinstance(node, dict):
            for child in children:
                val = _float(node.get(child))
                if val is not None:
                    out[f"{prefix}_{parent}_{child}"] = val
    return out


def _read_daily(path: Path) -> dict[date, dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda row: row["date"])
    _add_derived(rows)
    out: dict[date, dict[str, Any]] = {}
    for row in rows:
        d = _as_date(row["date"])
        out[d] = row
    return out


def _iter_month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        if cur.month == 12:
            nxt = date(cur.year + 1, 1, 1)
        else:
            nxt = date(cur.year, cur.month + 1, 1)
        chunk_end = min(end, nxt - timedelta(days=1))
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


def _query_v60_calls(
    v59_id: int,
    v60_id: int,
    start: date,
    end: date,
    status: Status,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    chunks = _iter_month_chunks(start, end)
    score_select = []
    for col in BASE_SCORE_COLUMNS:
        score_select.append(f"s60.{col} AS v60_{col}")
        score_select.append(f"s59.{col} AS v59_{col}")
    sql = f"""
        SELECT
          s60.`date`,
          s60.symbol,
          st.sector,
          {", ".join(score_select)},
          s60.volume_signal AS v60_volume_signal,
          s59.volume_signal AS v59_volume_signal,
          s60.weight_info AS v60_weight_info,
          s59.weight_info AS v59_weight_info
        FROM scores s60 FORCE INDEX(scores_version_date_IDX)
        LEFT JOIN scores s59 FORCE INDEX(scores_version_date_IDX)
          ON s59.version_id = %s
         AND s59.symbol = s60.symbol
         AND s59.`date` = s60.`date`
        LEFT JOIN stocks st ON st.symbol = s60.symbol
        WHERE s60.version_id = %s
          AND s60.`date` BETWEEN %s AND %s
          AND s60.overall >= 70
          AND s60.overall IS NOT NULL
        ORDER BY s60.`date`, s60.overall DESC
    """
    for idx, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        status.write(
            phase="query_scores",
            current=f"{chunk_start.isoformat()}..{chunk_end.isoformat()}",
            completed=1,
        )
        cur = DB.execute_sql(
            sql,
            (v59_id, v60_id, chunk_start.isoformat(), chunk_end.isoformat()),
        )
        cols = [desc[0] for desc in cur.description]
        for values in cur.fetchall():
            row = dict(zip(cols, values))
            row["date"] = _as_date(row["date"])
            rows.append(row)
        if idx % 12 == 0:
            print(f"  queried {idx}/{len(chunks)} month chunks; rows={len(rows):,}", flush=True)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for raw_col in ("v60_weight_info", "v59_weight_info"):
        prefix = "wi" if raw_col == "v60_weight_info" else "v59_wi"
        flat = pd.DataFrame([_flatten_weight(raw, prefix=prefix) for raw in df[raw_col]])
        df = pd.concat([df.reset_index(drop=True), flat.reset_index(drop=True)], axis=1)
    for col in [c for c in df.columns if c.startswith("v60_") or c.startswith("v59_") or c.startswith("wi_")]:
        if col.endswith("_weight_info") or col.endswith("_volume_signal"):
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["is_extra_vs_v59"] = (df["v59_overall"].isna()) | (df["v59_overall"] < 70)
    df["is_common_with_v59"] = df["v59_overall"] >= 70
    df["v60_minus_v59_overall"] = df["v60_overall"] - df["v59_overall"]
    df["v60_margin_over_70"] = df["v60_overall"] - 70
    df["v60_is_overflow_70_74"] = ((df["v60_overall"] >= 70) & (df["v60_overall"] < 75)).astype(int)
    return df


def _attach_daily_features(df: pd.DataFrame, daily: dict[date, dict[str, Any]]) -> pd.DataFrame:
    records = []
    for d in df["date"].tolist():
        row = daily.get(_as_date(d), {})
        records.append(
            {
                f"day_{k}": _float(row.get(k))
                for k in DAILY_CANDIDATE_FEATURES
                if row.get(k) not in (None, "")
            }
        )
    daily_df = pd.DataFrame(records)
    return pd.concat([df.reset_index(drop=True), daily_df.reset_index(drop=True)], axis=1)


def _precompute_call_outcomes(df: pd.DataFrame, v60_id: int, start: date, end: date, status: Status) -> pd.DataFrame:
    status.write(phase="precompute_outcomes", current="loading monte_carlo outcomes", completed=2)
    _apply_v60_portfolio_env()
    import monte_carlo as mc

    try:
        if not DB.is_closed():
            DB.close()
    except Exception:
        pass
    DB.connect(reuse_if_open=True)
    av60 = AlgorithmVersion.get_by_id(v60_id)
    call_sigs = mc.load_signals(av60, start, end)
    wanted = set(zip(df["symbol"].astype(str), df["date"].map(_as_date)))
    call_sigs = [sig for sig in call_sigs if (str(sig.symbol_id), sig.date) in wanted]
    sym_ids = sorted({sig.symbol_id for sig in call_sigs})
    print(f"  call outcome universe: signals={len(call_sigs):,}, symbols={len(sym_ids):,}", flush=True)
    ph = mc.load_price_history(sym_ids, start, end)
    breadth_dates, breadth_map = mc.load_breadth_map(start, end)
    count_map_call = None
    if getattr(mc, "STRESS_MODE", "") == "count":
        count_map_call = defaultdict(int)
        for sig in call_sigs:
            if int(sig.overall) >= 75:
                count_map_call[sig.date] += 1
    outcomes = mc.precompute_outcomes(
        call_sigs,
        ph,
        breadth_dates,
        breadth_map,
        trading_days=sorted({bar[0] for bars in ph.values() for bar in bars}),
        count_map=count_map_call,
    )
    rng = random.Random(26060)
    records: list[dict[str, Any]] = []
    for sym, d in zip(df["symbol"].astype(str), df["date"].map(_as_date)):
        outcome = outcomes.get((sym, d))
        if not outcome:
            records.append({"outcome_kind": "", "outcome_exit_bar": np.nan, "outcome_pnl_sample": np.nan})
            continue
        kind, pnl = mc.resolve(outcome, rng)
        records.append(
            {
                "outcome_kind": str(kind),
                "outcome_exit_bar": int(outcome.get("exit_bar", 0)),
                "outcome_pnl_sample": float(pnl),
                "outcome_premium_pct": float(outcome.get("premium_pct") or np.nan),
                "outcome_stressed": int(bool(outcome.get("stressed"))),
            }
        )
    out_df = pd.DataFrame(records)
    merged = pd.concat([df.reset_index(drop=True), out_df.reset_index(drop=True)], axis=1)
    merged["call_win"] = (merged["outcome_pnl_sample"] > 0).astype(int)
    merged["call_bad"] = (
        merged["outcome_kind"].isin(["sl", "prem"]) | (merged["outcome_pnl_sample"] < -0.35)
    ).astype(int)
    return merged


def _window_name(d: date) -> str:
    for name, (start, end) in WINDOWS.items():
        if start <= d <= end:
            return name
    return "other"


def _write_df(path: Path, df: pd.DataFrame) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)


def _feature_frame(df: pd.DataFrame, include_daily: bool = True, include_score: bool = True) -> tuple[pd.DataFrame, list[str]]:
    cols: list[str] = []
    if include_score:
        cols.extend([c for c in SIGNAL_LIVE_FEATURES if c in df.columns])
        cols.extend(["v60_margin_over_70", "v60_is_overflow_70_74"])
    if include_daily:
        cols.extend([c for c in df.columns if c.startswith("day_")])
    seen = set()
    cols = [c for c in cols if not (c in seen or seen.add(c))]
    X = df[cols].copy()
    for col in cols:
        X[col] = pd.to_numeric(X[col], errors="coerce")
    cols = [c for c in cols if X[c].notna().any()]
    X = X[cols]
    return X, cols


def _drop_empty_or_constant_train_cols(
    X_train: pd.DataFrame,
    X_test: pd.DataFrame,
    cols: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    keep: list[str] = []
    for col in cols:
        series = pd.to_numeric(X_train[col], errors="coerce")
        if not series.notna().any():
            continue
        if series.nunique(dropna=True) <= 1:
            continue
        keep.append(col)
    return X_train[keep], X_test[keep], keep


def _classification_metrics(y_true: pd.Series, y_pred: np.ndarray, y_score: np.ndarray | None = None) -> dict[str, float]:
    out = {
        "n": int(len(y_true)),
        "positive_rate": float(np.mean(y_true)) if len(y_true) else 0.0,
        "accuracy": float(accuracy_score(y_true, y_pred)) if len(y_true) else 0.0,
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)) if len(y_true) else 0.0,
    }
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        average="binary",
        zero_division=0,
    )
    out.update({"precision": float(precision), "recall": float(recall), "f1": float(f1)})
    if y_score is not None and len(set(y_true)) > 1:
        try:
            out["roc_auc"] = float(roc_auc_score(y_true, y_score))
        except Exception:
            pass
        try:
            out["avg_precision"] = float(average_precision_score(y_true, y_score))
        except Exception:
            pass
    return out


def _fit_extra_model(df: pd.DataFrame, out_dir: Path, status: Status) -> dict[str, Any]:
    status.write(phase="ml_extra_calls", current="predicting v60-only call membership", completed=3)
    data = df[df["v59_overall"].notna()].copy()
    data = data[data["date"] <= date(2026, 4, 24)]
    X, cols = _feature_frame(data, include_daily=True, include_score=True)
    y = data["is_extra_vs_v59"].astype(int)
    if y.nunique() < 2 or len(data) < 100:
        return {"skipped": "not enough extra/common variation", "n": int(len(data)), "positive_rate": float(y.mean()) if len(y) else 0.0}
    train_mask = data["date"] < date(2025, 1, 1)
    if train_mask.sum() < 100 or (~train_mask).sum() < 50:
        tr_idx, te_idx = train_test_split(np.arange(len(data)), test_size=0.25, random_state=60, stratify=y)
        train_mask = pd.Series(False, index=data.index)
        train_mask.iloc[tr_idx] = True
    X_train, X_test = X.loc[train_mask], X.loc[~train_mask]
    y_train, y_test = y.loc[train_mask], y.loc[~train_mask]
    X_train, X_test, cols = _drop_empty_or_constant_train_cols(X_train, X_test, cols)
    if not cols:
        return {"skipped": "no usable non-empty features", "n": int(len(data)), "positive_rate": float(y.mean())}
    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                RandomForestClassifier(
                    n_estimators=260,
                    min_samples_leaf=18,
                    class_weight="balanced_subsample",
                    random_state=60,
                    n_jobs=-1,
                ),
            ),
        ]
    )
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    score = model.predict_proba(X_test)[:, 1]
    metrics = _classification_metrics(y_test, pred, score)
    importances = model.named_steps["clf"].feature_importances_
    imp_df = pd.DataFrame({"feature": cols, "importance": importances}).sort_values("importance", ascending=False)
    _write_df(out_dir / "extra_call_membership_importance.csv", imp_df)
    tree = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                DecisionTreeClassifier(
                    max_depth=4,
                    min_samples_leaf=max(25, int(len(X_train) * 0.01)),
                    class_weight="balanced",
                    random_state=61,
                ),
            ),
        ]
    )
    tree.fit(X_train, y_train)
    tree_text = export_text(tree.named_steps["clf"], feature_names=cols, max_depth=4)
    (out_dir / "extra_call_membership_tree.txt").write_text(tree_text, encoding="utf-8")
    return {"metrics": metrics, "top_features": imp_df.head(15).to_dict("records"), "tree": "extra_call_membership_tree.txt"}


def _fit_toxic_model(df: pd.DataFrame, out_dir: Path, status: Status) -> dict[str, Any]:
    status.write(phase="ml_toxic_calls", current="predicting bad call outcomes", completed=4)
    data = df[df["outcome_kind"] != ""].copy()
    X, cols = _feature_frame(data, include_daily=True, include_score=True)
    y = data["call_bad"].astype(int)
    if y.nunique() < 2 or len(data) < 100:
        return {"skipped": "not enough bad/good variation", "n": int(len(data)), "positive_rate": float(y.mean()) if len(y) else 0.0}
    train_mask = data["date"] < date(2025, 1, 1)
    if train_mask.sum() < 100 or (~train_mask).sum() < 50:
        tr_idx, te_idx = train_test_split(np.arange(len(data)), test_size=0.25, random_state=62, stratify=y)
        train_mask = pd.Series(False, index=data.index)
        train_mask.iloc[tr_idx] = True
    X_train, X_test = X.loc[train_mask], X.loc[~train_mask]
    y_train, y_test = y.loc[train_mask], y.loc[~train_mask]
    X_train, X_test, cols = _drop_empty_or_constant_train_cols(X_train, X_test, cols)
    if not cols:
        return {"skipped": "no usable non-empty features", "n": int(len(data)), "positive_rate": float(y.mean())}
    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                ExtraTreesClassifier(
                    n_estimators=360,
                    min_samples_leaf=25,
                    class_weight="balanced",
                    random_state=62,
                    n_jobs=-1,
                ),
            ),
        ]
    )
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    score = model.predict_proba(X_test)[:, 1]
    metrics = _classification_metrics(y_test, pred, score)
    importances = model.named_steps["clf"].feature_importances_
    imp_df = pd.DataFrame({"feature": cols, "importance": importances}).sort_values("importance", ascending=False)
    _write_df(out_dir / "toxic_call_importance.csv", imp_df)
    data["toxic_pred"] = model.predict_proba(X[cols])[:, 1]
    _write_df(out_dir / "call_signal_predictions.csv", data)
    tree = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                DecisionTreeClassifier(
                    max_depth=4,
                    min_samples_leaf=max(30, int(len(X_train) * 0.008)),
                    class_weight="balanced",
                    random_state=63,
                ),
            ),
        ]
    )
    tree.fit(X_train, y_train)
    tree_text = export_text(tree.named_steps["clf"], feature_names=cols, max_depth=4)
    (out_dir / "toxic_call_tree.txt").write_text(tree_text, encoding="utf-8")
    return {"metrics": metrics, "top_features": imp_df.head(20).to_dict("records"), "tree": "toxic_call_tree.txt"}


def _summarize_windows(df: pd.DataFrame, out_dir: Path, status: Status) -> pd.DataFrame:
    status.write(phase="summaries", current="window overlap/outcome tables", completed=5)
    data = df.copy()
    data["window"] = data["date"].map(_window_name)
    rows: list[dict[str, Any]] = []
    for name, (start, end) in WINDOWS.items():
        w = data[(data["date"] >= start) & (data["date"] <= end)]
        if w.empty:
            continue
        extra = w[w["is_extra_vs_v59"]]
        common = w[w["is_common_with_v59"]]
        for label, part in [("all_v60_calls", w), ("v60_extra_vs_v59", extra), ("v60_common_v59", common)]:
            rows.append(
                {
                    "window": name,
                    "cohort": label,
                    "n": int(len(part)),
                    "days": int(part["date"].nunique()) if len(part) else 0,
                    "mean_v60_overall": float(part["v60_overall"].mean()) if len(part) else np.nan,
                    "pct_70_74": float(part["v60_is_overflow_70_74"].mean()) if len(part) else np.nan,
                    "mean_v59_overall": float(part["v59_overall"].mean()) if len(part) else np.nan,
                    "mean_delta": float(part["v60_minus_v59_overall"].mean()) if len(part) else np.nan,
                    "call_win_rate": float(part["call_win"].mean()) if len(part) else np.nan,
                    "call_bad_rate": float(part["call_bad"].mean()) if len(part) else np.nan,
                    "mean_pnl_sample": float(part["outcome_pnl_sample"].mean()) if len(part) else np.nan,
                    "median_pnl_sample": float(part["outcome_pnl_sample"].median()) if len(part) else np.nan,
                    "mean_w_adj": float(part["wi_w_adj"].mean()) if "wi_w_adj" in part and len(part) else np.nan,
                    "mean_sector_crash": float(part["wi_sector_breadth_wave_crash"].mean()) if "wi_sector_breadth_wave_crash" in part and len(part) else np.nan,
                }
            )
    out = pd.DataFrame(rows)
    _write_df(out_dir / "window_cohort_summary.csv", out)
    return out


def _rule_candidates(df: pd.DataFrame, out_dir: Path, status: Status) -> pd.DataFrame:
    status.write(phase="rule_mining", current="signal-level shippable proxy predicates", completed=6)
    data = df[df["outcome_kind"] != ""].copy()
    X, cols = _feature_frame(data, include_daily=True, include_score=True)
    base_bad = float(data["call_bad"].mean())
    base_win = float(data["call_win"].mean())
    rows: list[dict[str, Any]] = []
    feature_pool = [c for c in cols if X[c].notna().sum() >= 100]
    for feature in feature_pool:
        values = pd.to_numeric(X[feature], errors="coerce").dropna()
        if values.nunique() < 8:
            continue
        qs = sorted(set(float(values.quantile(q)) for q in [0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.50, 0.67, 0.75, 0.80, 0.85, 0.90, 0.95]))
        for threshold in qs:
            for op in ("<=", ">="):
                mask = X[feature] <= threshold if op == "<=" else X[feature] >= threshold
                n = int(mask.sum())
                if n < 40 or n > len(data) * 0.65:
                    continue
                part = data[mask]
                bad = float(part["call_bad"].mean())
                win = float(part["call_win"].mean())
                extra_rate = float(part["is_extra_vs_v59"].mean())
                crash = part[(part["date"] >= WINDOWS["2020-crash"][0]) & (part["date"] <= WINDOWS["2020-crash"][1])]
                hold = part[part["date"] >= date(2025, 1, 1)]
                score = (bad - base_bad) * math.log1p(n) + extra_rate * 0.25 - max(0.0, base_win - win) * 0.50
                rows.append(
                    {
                        "feature": feature,
                        "op": op,
                        "threshold": threshold,
                        "n": n,
                        "support": n / len(data),
                        "bad_rate": bad,
                        "bad_edge": bad - base_bad,
                        "win_rate": win,
                        "win_edge": win - base_win,
                        "extra_rate": extra_rate,
                        "mean_pnl": float(part["outcome_pnl_sample"].mean()),
                        "crash_n": int(len(crash)),
                        "crash_bad_rate": float(crash["call_bad"].mean()) if len(crash) else np.nan,
                        "holdout_2025p_n": int(len(hold)),
                        "holdout_2025p_bad_rate": float(hold["call_bad"].mean()) if len(hold) else np.nan,
                        "score": score,
                    }
                )
    out = pd.DataFrame(rows).sort_values(["score", "bad_edge"], ascending=False)
    _write_df(out_dir / "toxic_call_rules_ranked.csv", out)
    return out


def _daily_wave(df: pd.DataFrame, out_dir: Path, status: Status) -> dict[str, Any]:
    status.write(phase="daily_wave_ml", current="market-structure wave proxy", completed=7)
    data = df[df["outcome_kind"] != ""].copy()
    daily_rows = []
    for d, g in data.groupby("date"):
        row = {"date": d}
        row["call_n"] = int(len(g))
        row["extra_call_n"] = int(g["is_extra_vs_v59"].sum())
        row["extra_call_share"] = float(g["is_extra_vs_v59"].mean())
        row["call_bad_rate"] = float(g["call_bad"].mean())
        row["call_win_rate"] = float(g["call_win"].mean())
        row["call_mean_pnl"] = float(g["outcome_pnl_sample"].mean())
        for col in [c for c in data.columns if c.startswith("day_")]:
            vals = pd.to_numeric(g[col], errors="coerce").dropna()
            if len(vals):
                row[col] = float(vals.iloc[0])
        daily_rows.append(row)
    daily_df = pd.DataFrame(daily_rows).sort_values("date")
    feature_cols = [c for c in daily_df.columns if c.startswith("day_")]
    feature_cols += ["call_n", "extra_call_n", "extra_call_share"]
    feature_cols = [c for c in feature_cols if daily_df[c].notna().sum() >= 80]
    train = daily_df[daily_df["date"] < date(2025, 1, 1)].copy()
    test = daily_df[daily_df["date"] >= date(2025, 1, 1)].copy()
    if len(train) < 80 or len(test) < 20:
        test = train.tail(max(20, len(train) // 4))
        train = train.iloc[:-len(test)]
    X_train, y_train = train[feature_cols], train["call_bad_rate"]
    X_test, y_test = test[feature_cols], test["call_bad_rate"]
    X_train, X_test, feature_cols = _drop_empty_or_constant_train_cols(X_train, X_test, feature_cols)
    if not feature_cols:
        return {"skipped": "no usable daily-wave features", "test_n": int(len(test))}
    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "reg",
                ExtraTreesRegressor(
                    n_estimators=420,
                    min_samples_leaf=12,
                    random_state=64,
                    n_jobs=-1,
                ),
            ),
        ]
    )
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    mae = float(mean_absolute_error(y_test, pred))
    importances = model.named_steps["reg"].feature_importances_
    imp_df = pd.DataFrame({"feature": feature_cols, "importance": importances}).sort_values("importance", ascending=False)
    _write_df(out_dir / "daily_wave_feature_importance.csv", imp_df)
    try:
        perm = permutation_importance(model, X_test, y_test, n_repeats=8, random_state=65, n_jobs=-1)
        perm_df = pd.DataFrame(
            {
                "feature": feature_cols,
                "perm_importance_mean": perm.importances_mean,
                "perm_importance_std": perm.importances_std,
            }
        ).sort_values("perm_importance_mean", ascending=False)
        _write_df(out_dir / "daily_wave_permutation_importance.csv", perm_df)
    except Exception as exc:
        perm_df = pd.DataFrame({"error": [str(exc)]})
        _write_df(out_dir / "daily_wave_permutation_importance.csv", perm_df)
    daily_df["wave_ml_pred_bad_rate"] = model.predict(daily_df[feature_cols])
    q80 = float(daily_df["wave_ml_pred_bad_rate"].quantile(0.80))
    q90 = float(daily_df["wave_ml_pred_bad_rate"].quantile(0.90))
    daily_df["wave_ml_top20"] = daily_df["wave_ml_pred_bad_rate"] >= q80
    daily_df["wave_ml_top10"] = daily_df["wave_ml_pred_bad_rate"] >= q90
    _write_df(out_dir / "daily_wave_predictions.csv", daily_df)
    eval_rows = []
    for label, mask in [
        ("all", pd.Series(True, index=daily_df.index)),
        ("top20", daily_df["wave_ml_top20"]),
        ("top10", daily_df["wave_ml_top10"]),
        ("not_top20", ~daily_df["wave_ml_top20"]),
    ]:
        part = daily_df[mask]
        eval_rows.append(
            {
                "cohort": label,
                "days": int(len(part)),
                "mean_call_bad_rate": float(part["call_bad_rate"].mean()) if len(part) else np.nan,
                "mean_call_win_rate": float(part["call_win_rate"].mean()) if len(part) else np.nan,
                "mean_extra_call_share": float(part["extra_call_share"].mean()) if len(part) else np.nan,
                "mean_call_n": float(part["call_n"].mean()) if len(part) else np.nan,
            }
        )
    eval_df = pd.DataFrame(eval_rows)
    _write_df(out_dir / "daily_wave_cohorts.csv", eval_df)
    return {
        "test_mae": mae,
        "test_n": int(len(test)),
        "top_features": imp_df.head(20).to_dict("records"),
        "cohorts": eval_df.to_dict("records"),
    }


def _score_dampen_screen(df: pd.DataFrame, out_dir: Path, status: Status) -> pd.DataFrame:
    status.write(phase="candidate_dampen_screen", current="signal-level score demotion proxy", completed=8)
    data = df[df["outcome_kind"] != ""].copy()
    pred_path = out_dir / "daily_wave_predictions.csv"
    if pred_path.exists():
        wave = pd.read_csv(pred_path)
        wave["date"] = pd.to_datetime(wave["date"]).dt.date
        data = data.merge(wave[["date", "wave_ml_pred_bad_rate"]], on="date", how="left")
    else:
        data["wave_ml_pred_bad_rate"] = np.nan
    base = {
        "n": int(len(data)),
        "bad_saved": 0,
        "wins_lost": 0,
        "mean_pnl_removed": 0.0,
        "removed_n": 0,
        "utility": 0.0,
    }
    rows = [dict(candidate="baseline_no_dampen", **base)]
    q_values = [0.70, 0.75, 0.80, 0.85, 0.90]
    risk_thresholds = [float(data["wave_ml_pred_bad_rate"].quantile(q)) for q in q_values if data["wave_ml_pred_bad_rate"].notna().any()]
    vulnerability_masks = {
        "overflow_70_74": data["v60_overall"].between(70, 74),
        "overflow_plus_wadj": data["v60_overall"].between(70, 74) & (pd.to_numeric(data.get("wi_w_adj"), errors="coerce") >= 2.0),
        "extra_like_overflow": data["v60_overall"].between(70, 74)
        & (pd.to_numeric(data.get("wi_w_adj"), errors="coerce") >= 2.0)
        & (pd.to_numeric(data.get("wi_td"), errors="coerce") >= 0.25),
        "sector_crash_overflow": data["v60_overall"].between(70, 74)
        & (pd.to_numeric(data.get("wi_sector_breadth_wave_crash"), errors="coerce").fillna(0) >= 0.10),
    }
    for risk_q, risk_threshold in zip(q_values, risk_thresholds):
        risk_mask = data["wave_ml_pred_bad_rate"] >= risk_threshold
        for name, vuln in vulnerability_masks.items():
            mask = risk_mask & vuln
            removed = data[mask]
            if len(removed) < 10:
                continue
            crash_removed = removed[(removed["date"] >= WINDOWS["2020-crash"][0]) & (removed["date"] <= WINDOWS["2020-crash"][1])]
            rows.append(
                {
                    "candidate": f"wave_q{int(risk_q*100)}__{name}",
                    "risk_quantile": risk_q,
                    "risk_threshold": risk_threshold,
                    "removed_n": int(len(removed)),
                    "removed_support": float(len(removed) / len(data)),
                    "bad_saved": int(removed["call_bad"].sum()),
                    "wins_lost": int(removed["call_win"].sum()),
                    "bad_rate_removed": float(removed["call_bad"].mean()),
                    "win_rate_removed": float(removed["call_win"].mean()),
                    "mean_pnl_removed": float(removed["outcome_pnl_sample"].mean()),
                    "crash_removed_n": int(len(crash_removed)),
                    "crash_bad_saved": int(crash_removed["call_bad"].sum()) if len(crash_removed) else 0,
                    "crash_wins_lost": int(crash_removed["call_win"].sum()) if len(crash_removed) else 0,
                    "utility": float(removed["call_bad"].sum() - 0.8 * removed["call_win"].sum() - max(0, len(removed) - removed["call_bad"].sum()) * 0.15),
                }
            )
    out = pd.DataFrame(rows).sort_values("utility", ascending=False, na_position="last")
    _write_df(out_dir / "score_dampen_proxy_candidates.csv", out)
    return out


def _write_summary(out_dir: Path, payload: dict[str, Any]) -> None:
    summary = out_dir / "extra_call_wave_summary.md"
    wc = pd.read_csv(out_dir / "window_cohort_summary.csv")
    rules = pd.read_csv(out_dir / "toxic_call_rules_ranked.csv")
    damp = pd.read_csv(out_dir / "score_dampen_proxy_candidates.csv")
    extra_imp = pd.read_csv(out_dir / "extra_call_membership_importance.csv") if (out_dir / "extra_call_membership_importance.csv").exists() else pd.DataFrame()
    toxic_imp = pd.read_csv(out_dir / "toxic_call_importance.csv") if (out_dir / "toxic_call_importance.csv").exists() else pd.DataFrame()
    wave_imp = pd.read_csv(out_dir / "daily_wave_feature_importance.csv") if (out_dir / "daily_wave_feature_importance.csv").exists() else pd.DataFrame()
    lines: list[str] = []
    lines.append("# v60 Extra-Call / Market-Wave Research")
    lines.append("")
    lines.append("Experiment-local research. No score rows, strategy config, or algorithm metadata were written.")
    lines.append("")
    lines.append("## Metadata")
    lines.append("")
    for key in ["generated_at", "start", "end", "v59_id", "v60_id", "signals_n"]:
        lines.append(f"- {key}: {payload.get(key)}")
    lines.append("")
    lines.append("## Window Cohorts")
    lines.append("")
    lines.append("| window | cohort | n | days | win | bad | mean pnl | pct 70-74 | mean w_adj |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for _, row in wc.iterrows():
        lines.append(
            f"| {row['window']} | `{row['cohort']}` | {int(row['n'])} | {int(row['days'])} | "
            f"{row['call_win_rate']:.3f} | {row['call_bad_rate']:.3f} | {row['mean_pnl_sample']:.3f} | "
            f"{row['pct_70_74']:.3f} | {row['mean_w_adj']:.3f} |"
        )
    lines.append("")
    lines.append("## ML Reads")
    lines.append("")
    lines.append("### v60-only membership top features")
    if not extra_imp.empty:
        for _, row in extra_imp.head(12).iterrows():
            lines.append(f"- `{row['feature']}` importance={row['importance']:.4f}")
    else:
        lines.append("- skipped")
    lines.append("")
    lines.append("### Toxic-call top features")
    if not toxic_imp.empty:
        for _, row in toxic_imp.head(12).iterrows():
            lines.append(f"- `{row['feature']}` importance={row['importance']:.4f}")
    else:
        lines.append("- skipped")
    lines.append("")
    lines.append("### Daily market-wave top features")
    if not wave_imp.empty:
        for _, row in wave_imp.head(12).iterrows():
            lines.append(f"- `{row['feature']}` importance={row['importance']:.4f}")
    else:
        lines.append("- skipped")
    lines.append("")
    lines.append("## Top Transparent Rules")
    lines.append("")
    lines.append("| feature | op | threshold | n | bad edge | win edge | extra rate | crash n | holdout bad |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for _, row in rules.head(20).iterrows():
        lines.append(
            f"| `{row['feature']}` | {row['op']} | {row['threshold']:.4g} | {int(row['n'])} | "
            f"{row['bad_edge']:+.3f} | {row['win_edge']:+.3f} | {row['extra_rate']:.3f} | "
            f"{int(row['crash_n'])} | {row['holdout_2025p_bad_rate']:.3f} |"
        )
    lines.append("")
    lines.append("## Score-Dampen Proxy Screen")
    lines.append("")
    lines.append("| candidate | removed | bad saved | wins lost | mean pnl removed | crash removed | utility |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for _, row in damp.head(20).iterrows():
        lines.append(
            f"| `{row['candidate']}` | {int(row.get('removed_n', 0))} | {int(row.get('bad_saved', 0))} | "
            f"{int(row.get('wins_lost', 0))} | {row.get('mean_pnl_removed', float('nan')):.3f} | "
            f"{int(row.get('crash_removed_n', 0)) if not pd.isna(row.get('crash_removed_n', np.nan)) else 0} | "
            f"{row.get('utility', float('nan')):.2f} |"
        )
    lines.append("")
    lines.append("## Interpretation Contract")
    lines.append("")
    lines.append("- `v59` overlap features are diagnostic labels, not shippable inputs.")
    lines.append("- Candidate score dampeners here are signal-level proxies; any winner still needs staging-native scoring and W1/MC validation.")
    lines.append("- Prefer wave-shaped dampening around marginal 70-74 calls over a hard cliff unless downstream validation proves otherwise.")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--v59-id", type=int, default=59)
    parser.add_argument("--v60-id", type=int, default=60)
    parser.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2020, 1, 1))
    parser.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date(2026, 4, 24))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    status = Status(args.out_dir, datetime.now(timezone.utc).isoformat())
    status.write()
    t0 = time.time()
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "v59_id": args.v59_id,
        "v60_id": args.v60_id,
    }
    print(f"[start] v60 extra-call wave research -> {args.out_dir}", flush=True)
    print(f"[inputs] daily={args.daily}", flush=True)
    daily = _read_daily(args.daily)
    status.write(phase="daily_loaded", current=f"daily rows={len(daily):,}", completed=1)
    df = _query_v60_calls(args.v59_id, args.v60_id, args.start, args.end, status)
    if df.empty:
        raise RuntimeError("No v60 call rows found")
    df = _attach_daily_features(df, daily)
    df = _precompute_call_outcomes(df, args.v60_id, args.start, args.end, status)
    payload["signals_n"] = int(len(df))
    _write_df(args.out_dir / "v60_call_signal_dataset.csv", df)
    _summarize_windows(df, args.out_dir, status)
    extra_read = _fit_extra_model(df, args.out_dir, status)
    toxic_read = _fit_toxic_model(df, args.out_dir, status)
    rules = _rule_candidates(df, args.out_dir, status)
    wave_read = _daily_wave(df, args.out_dir, status)
    damp = _score_dampen_screen(df, args.out_dir, status)
    payload.update(
        {
            "elapsed_s": round(time.time() - t0, 2),
            "extra_model": extra_read,
            "toxic_model": toxic_read,
            "daily_wave_model": wave_read,
            "top_rule": rules.head(1).to_dict("records")[0] if len(rules) else None,
            "top_dampen_proxy": damp.head(1).to_dict("records")[0] if len(damp) else None,
        }
    )
    (args.out_dir / "extra_call_wave_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    _write_summary(args.out_dir, payload)
    status.write(phase="done", state="done", current="complete", completed=status.total)
    print(f"[done] elapsed={time.time() - t0:.1f}s summary={args.out_dir / 'extra_call_wave_summary.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
