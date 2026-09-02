"""Analyze daily opportunity supply against regime and portfolio outcomes."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_DAILY = ROOT / "daily_state.csv"

OPPORTUNITY_COLUMNS = [
    "post_call_primary_n",
    "post_put_primary_n",
    "post_total_primary_n",
    "post_call_primary_alloc_demand",
    "post_put_primary_alloc_demand",
    "post_total_primary_alloc_demand",
    "post_net_call_minus_put_n",
    "post_net_call_minus_put_demand",
    "pre_boost_call_primary_n",
    "pre_boost_put_primary_n",
    "pre_boost_total_primary_n",
    "score_processing_call_delta_n",
    "score_processing_put_delta_n",
    "outcome_call_n",
    "outcome_put_n",
    "effective_call_n",
    "effective_put_n",
    "filled_call_n",
    "filled_put_n",
    "open_call_n",
    "open_put_n",
    "free_slots",
    "cash_pct",
    "open_premium_pct",
    "post_call_max_sector_share",
    "post_put_max_sector_share",
    "post_call_sector_hhi",
    "post_put_sector_hhi",
]

REGIME_COLUMNS = [
    "breadth_breadth_score",
    "breadth_pct_above_ema50",
    "breadth_pct_above_ema200",
    "breadth_mcclellan_oscillator",
    "breadth_mcclellan_summation",
    "breadth_ad_ratio",
    "breadth_trin",
    "breadth_sector_etf_pct_above_ema50",
    "breadth_sector_etf_pct_above_ema200",
    "breadth_sector_etf_avg_rsi",
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
    "regime_fg_composite",
    "regime_fg_multiplier",
    "regime_credit_spread_score",
    "regime_haven_score",
    "regime_skew_score",
    "regime_spy_pct_from_ema50",
    "regime_spy_pct_from_ema200",
    "dd",
]

OUTCOME_COLUMNS = [
    "offered_tp_rate",
    "offered_call_tp_rate",
    "offered_put_tp_rate",
    "offered_avg_hold_bars",
    "entry_tp_rate",
    "entry_call_tp_rate",
    "entry_put_tp_rate",
    "entry_avg_hold_bars",
    "entry_avg_pnl_pct",
    "next_15d_equity_return",
    "next_30d_equity_return",
    "next_15d_worst_equity_return",
    "next_30d_worst_equity_return",
    "next_15d_dd_increase",
    "next_30d_dd_increase",
]


def _float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / math.sqrt(vx * vy)


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def _paired(rows: list[dict[str, Any]], left: str, right: str) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        x = _float(row.get(left))
        y = _float(row.get(right))
        if x is None or y is None:
            continue
        xs.append(x)
        ys.append(y)
    return xs, ys


def _correlations(
    rows: list[dict[str, Any]],
    left_columns: list[str],
    right_columns: list[str],
    min_n: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for left in left_columns:
        for right in right_columns:
            xs, ys = _paired(rows, left, right)
            if len(xs) < min_n:
                continue
            pearson = _pearson(xs, ys)
            spearman = _pearson(_rank(xs), _rank(ys))
            if pearson is None and spearman is None:
                continue
            out.append(
                {
                    "left": left,
                    "right": right,
                    "n": len(xs),
                    "pearson": pearson,
                    "spearman": spearman,
                    "abs_spearman": abs(spearman or 0.0),
                    "left_mean": sum(xs) / len(xs),
                    "right_mean": sum(ys) / len(ys),
                }
            )
    out.sort(key=lambda r: (r["abs_spearman"], abs(r["pearson"] or 0.0)), reverse=True)
    return out


def _quantile(values: list[float], q: float) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    idx = int(round((len(clean) - 1) * q))
    return clean[idx]


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _cohort_summary(rows: list[dict[str, Any]], features: list[str], targets: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for feature in features:
        vals = [_float(row.get(feature)) for row in rows]
        clean = [v for v in vals if v is not None]
        if len(clean) < 50:
            continue
        bounds = [_quantile(clean, q) for q in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
        if any(b is None for b in bounds) or len(set(bounds)) < 3:
            continue
        for i in range(5):
            lo = bounds[i]
            hi = bounds[i + 1]
            if lo is None or hi is None:
                continue
            bucket = []
            for row in rows:
                v = _float(row.get(feature))
                if v is None:
                    continue
                if i == 0:
                    in_bin = lo <= v <= hi
                else:
                    in_bin = lo < v <= hi
                if in_bin:
                    bucket.append(row)
            rec: dict[str, Any] = {
                "feature": feature,
                "cohort": f"Q{i + 1}",
                "lo": lo,
                "hi": hi,
                "days": len(bucket),
            }
            for target in targets:
                rec[target] = _mean([_float(row.get(target)) for row in bucket])
            out.append(rec)
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Daily Opportunity Allocation Research",
        "",
        "This is a Stage 3 research surface. It does not change scores, barriers, or shipped portfolio constants.",
        "",
        "## Dataset",
        "",
        f"- Rows: {summary['n_rows']:,} daily rows",
        f"- Date range: {summary['date_min']} to {summary['date_max']}",
        "",
        "## Strongest Opportunity-Regime Relationships",
        "",
        "| Opportunity | Regime factor | n | Spearman | Pearson |",
        "|---|---|---:|---:|---:|",
    ]
    for row in summary["top_regime"][:20]:
        lines.append(
            f"| `{row['left']}` | `{row['right']}` | {row['n']} | "
            f"{row['spearman']:+.3f} | {row['pearson']:+.3f} |"
        )
    lines.extend(
        [
            "",
            "## Strongest Opportunity-Outcome Relationships",
            "",
            "| Opportunity | Outcome | n | Spearman | Pearson |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in summary["top_outcome"][:20]:
        lines.append(
            f"| `{row['left']}` | `{row['right']}` | {row['n']} | "
            f"{row['spearman']:+.3f} | {row['pearson']:+.3f} |"
        )
    lines.extend(
        [
            "",
            "## Initial Read",
            "",
            "- Use the correlation files as discovery, not a ship gate.",
            "- Any candidate must later run through Stage 3 N=500x8 DD-primary validation.",
            "- Count signals that improve fixed-barrier quality are allocation candidates, not regime/barrier switches.",
            "",
            "## Artifacts",
            "",
            "- `daily_state.csv`: per-day opportunity, regime, portfolio, and outcome surface.",
            "- `opportunity_regime_correlations.csv`: daily N/demand vs market state.",
            "- `opportunity_outcome_correlations.csv`: daily N/demand vs portfolio/outcome state.",
            "- `opportunity_cohorts.csv`: quintile summaries for candidate allocation waves.",
            "- `ml_probe.json`: experiment-local ML feature-importance probe when scikit-learn is available.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", default=str(DEFAULT_DAILY))
    parser.add_argument("--out-dir", default=str(ROOT))
    parser.add_argument("--min-n", type=int, default=80)
    args = parser.parse_args(argv)

    daily_path = Path(args.daily)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _read_rows(daily_path)
    if not rows:
        raise RuntimeError(f"No rows loaded from {daily_path}")

    opp_cols = [c for c in OPPORTUNITY_COLUMNS if c in rows[0]]
    regime_cols = [c for c in REGIME_COLUMNS if c in rows[0]]
    outcome_cols = [c for c in OUTCOME_COLUMNS if c in rows[0]]
    regime_corr = _correlations(rows, opp_cols, regime_cols, args.min_n)
    outcome_corr = _correlations(rows, opp_cols, outcome_cols, args.min_n)
    cohorts = _cohort_summary(
        rows,
        [
            "post_call_primary_n",
            "post_put_primary_n",
            "post_total_primary_n",
            "post_total_primary_alloc_demand",
            "post_call_primary_alloc_demand",
            "post_put_primary_alloc_demand",
            "post_net_call_minus_put_n",
            "post_net_call_minus_put_demand",
            "pre_boost_call_primary_n",
            "pre_boost_put_primary_n",
            "score_processing_call_delta_n",
            "score_processing_put_delta_n",
            "outcome_call_n",
            "outcome_put_n",
            "filled_call_n",
            "filled_put_n",
            "open_call_n",
            "open_put_n",
            "free_slots",
            "cash_pct",
            "open_premium_pct",
            "post_call_sector_hhi",
            "post_put_sector_hhi",
            "post_call_max_sector_share",
            "post_put_max_sector_share",
            "effective_call_n",
            "effective_put_n",
        ],
        [
            "offered_tp_rate",
            "offered_call_tp_rate",
            "offered_put_tp_rate",
            "entry_tp_rate",
            "entry_call_tp_rate",
            "entry_put_tp_rate",
            "entry_avg_pnl_pct",
            "entry_avg_hold_bars",
            "next_15d_equity_return",
            "next_30d_equity_return",
            "next_15d_dd_increase",
            "next_30d_dd_increase",
            "next_15d_worst_equity_return",
            "next_30d_worst_equity_return",
        ],
    )

    _write_csv(out_dir / "opportunity_regime_correlations.csv", regime_corr)
    _write_csv(out_dir / "opportunity_outcome_correlations.csv", outcome_corr)
    _write_csv(out_dir / "opportunity_cohorts.csv", cohorts)
    summary = {
        "n_rows": len(rows),
        "date_min": rows[0]["date"],
        "date_max": rows[-1]["date"],
        "top_regime": regime_corr[:50],
        "top_outcome": outcome_corr[:50],
    }
    (out_dir / "analysis_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_markdown(out_dir / "FINDINGS.md", summary)
    print(f"Wrote analysis artifacts to {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
