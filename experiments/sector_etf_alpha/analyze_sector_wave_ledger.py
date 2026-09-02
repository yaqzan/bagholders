"""Analyze sector-wave DD ledger surfaces.

This is Stage A.2 for the sector ETF alpha portfolio work. It converts the
trade-level sector-wave ledger into allocation-design evidence: where worst-DD
losses concentrate relative to normal premium exposure.

Outputs:
  experiments/sector_etf_alpha/dd_probe/sector_wave_surface_*.csv
  experiments/sector_etf_alpha/FINDINGS_SECTOR_WAVE_LEDGER.md
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "experiments" / "sector_etf_alpha" / "dd_probe"
LEDGER = PROBE / "sector_wave_ledger.parquet"
OUT_MD = ROOT / "experiments" / "sector_etf_alpha" / "FINDINGS_SECTOR_WAVE_LEDGER.md"

BASE = ["variant", "window", "side"]


def surface(df: pl.LazyFrame, group_cols: list[str]) -> pl.DataFrame:
    """Return loss-vs-premium lift for a side-local grouping."""
    keys = BASE + group_cols
    condition = pl.col("in_worst_episode") & (pl.col("pnl_dollars") < 0)
    grouped = (
        df.group_by(keys)
        .agg([
            pl.len().alias("n_trades"),
            pl.col("premium_cost").sum().alias("premium_sum"),
            pl.col("open_side_sector_exposure").mean().alias("avg_side_sector_exposure"),
            condition.sum().alias("n_worst_loss_trades"),
            pl.when(condition).then(-pl.col("pnl_dollars")).otherwise(0.0).sum().alias("worst_loss_abs"),
            pl.when(condition).then(pl.col("dd_contribution_share")).otherwise(0.0).sum().alias("dd_contribution_sum"),
            pl.col("entry_dd").mean().alias("avg_entry_dd"),
            pl.col("option_pnl").mean().alias("avg_option_pnl"),
        ])
    )
    totals = (
        grouped.group_by(BASE)
        .agg([
            pl.col("n_trades").sum().alias("total_trades_side"),
            pl.col("premium_sum").sum().alias("total_premium_side"),
            pl.col("worst_loss_abs").sum().alias("total_worst_loss_side"),
        ])
    )
    return (
        grouped.join(totals, on=BASE, how="left")
        .with_columns([
            (pl.col("n_trades") / pl.col("total_trades_side")).alias("trade_share"),
            (pl.col("premium_sum") / pl.col("total_premium_side")).alias("premium_share"),
            pl.when(pl.col("total_worst_loss_side") > 0)
              .then(pl.col("worst_loss_abs") / pl.col("total_worst_loss_side"))
              .otherwise(0.0)
              .alias("loss_share"),
        ])
        .with_columns([
            pl.when(pl.col("premium_share") > 0)
              .then(pl.col("loss_share") / pl.col("premium_share"))
              .otherwise(None)
              .alias("lift_vs_premium"),
            pl.when(pl.col("premium_sum") > 0)
              .then(pl.col("worst_loss_abs") / pl.col("premium_sum"))
              .otherwise(None)
              .alias("loss_per_premium"),
        ])
        .collect()
        .sort(BASE + group_cols)
    )


def write_csv(df: pl.DataFrame, name: str) -> Path:
    out = PROBE / f"sector_wave_surface_{name}.csv"
    df.write_csv(out)
    return out


def fmt_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:.1f}%"


def fmt_num(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:.2f}"


def markdown_table(df: pl.DataFrame, cols: list[str], max_rows: int = 12) -> str:
    rows = df.head(max_rows).to_dicts()
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        vals = []
        for col in cols:
            val = row.get(col)
            if isinstance(val, float):
                if "share" in col or col.endswith("_dd") or "exposure" in col:
                    vals.append(fmt_pct(val))
                else:
                    vals.append(fmt_num(val))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def build_findings(
    exposure: pl.DataFrame,
    phase: pl.DataFrame,
    rsi: pl.DataFrame,
    rs: pl.DataFrame,
    sector_diag: pl.DataFrame,
) -> str:
    side_share = (
        exposure.group_by(["variant", "window", "side"])
        .agg(pl.col("worst_loss_abs").sum().alias("worst_loss_abs"))
        .with_columns(
            (pl.col("worst_loss_abs") / pl.col("worst_loss_abs").sum().over(["variant", "window"]))
            .alias("worst_loss_share")
        )
        .sort(["variant", "window", "side"])
    )
    v4_put_exp = (
        exposure.filter((pl.col("variant") == "V4") & (pl.col("side") == "put"))
        .select([
            "window", "open_side_sector_exp_bin", "n_trades", "n_worst_loss_trades",
            "premium_share", "loss_share", "lift_vs_premium", "avg_entry_dd",
        ])
        .sort(["window", "open_side_sector_exp_bin"])
    )
    risky_phase = (
        phase.filter((pl.col("variant") == "V4") & (pl.col("n_worst_loss_trades") >= 50))
        .sort(["lift_vs_premium", "loss_share"], descending=[True, True])
        .select([
            "window", "side", "sector_phase_bin", "open_side_sector_exp_bin",
            "n_worst_loss_trades", "premium_share", "loss_share", "lift_vs_premium",
        ])
    )
    risky_rsi = (
        rsi.filter((pl.col("variant") == "V4") & (pl.col("n_worst_loss_trades") >= 50))
        .sort(["lift_vs_premium", "loss_share"], descending=[True, True])
        .select([
            "window", "side", "sector_rsi_bin", "open_side_sector_exp_bin",
            "n_worst_loss_trades", "premium_share", "loss_share", "lift_vs_premium",
        ])
    )
    risky_rs = (
        rs.filter((pl.col("variant") == "V4") & (pl.col("n_worst_loss_trades") >= 50))
        .sort(["lift_vs_premium", "loss_share"], descending=[True, True])
        .select([
            "window", "side", "stock_rs_abs_bin", "open_side_sector_exp_bin",
            "n_worst_loss_trades", "premium_share", "loss_share", "lift_vs_premium",
        ])
    )
    sector_top = (
        sector_diag.filter((pl.col("variant") == "V4") & (pl.col("n_worst_loss_trades") >= 50))
        .sort(["loss_share", "lift_vs_premium"], descending=[True, True])
        .select([
            "window", "side", "sector", "open_side_sector_exp_bin",
            "n_worst_loss_trades", "premium_share", "loss_share", "lift_vs_premium",
        ])
    )

    return "\n".join([
        "# Sector Wave DD Ledger Findings",
        "",
        "Stage A ledger evidence for portfolio-scale sector ETF alpha refinement.",
        "",
        "## Inputs",
        "",
        f"- Ledger: `{LEDGER.relative_to(ROOT)}`",
        "- Windows: 2022, 2024, 22-now, 5y.",
        "- Variants: BASELINE and V4 sector ETF alpha.",
        "- Metric: worst-episode loss share divided by normal premium share inside each side/window.",
        "",
        "## Side Loss Share",
        "",
        markdown_table(side_share, ["variant", "window", "side", "worst_loss_share"], 20),
        "",
        "## V4 PUT Sector Exposure Shape",
        "",
        markdown_table(
            v4_put_exp,
            ["window", "open_side_sector_exp_bin", "n_worst_loss_trades", "premium_share", "loss_share", "lift_vs_premium", "avg_entry_dd"],
            40,
        ),
        "",
        "## Highest V4 Phase Cells",
        "",
        markdown_table(risky_phase, ["window", "side", "sector_phase_bin", "open_side_sector_exp_bin", "n_worst_loss_trades", "premium_share", "loss_share", "lift_vs_premium"], 16),
        "",
        "## Highest V4 RSI Cells",
        "",
        markdown_table(risky_rsi, ["window", "side", "sector_rsi_bin", "open_side_sector_exp_bin", "n_worst_loss_trades", "premium_share", "loss_share", "lift_vs_premium"], 16),
        "",
        "## Highest V4 Stock-vs-Sector Divergence Cells",
        "",
        markdown_table(risky_rs, ["window", "side", "stock_rs_abs_bin", "open_side_sector_exp_bin", "n_worst_loss_trades", "premium_share", "loss_share", "lift_vs_premium"], 16),
        "",
        "## Sector Diagnostic Cells",
        "",
        markdown_table(sector_top, ["window", "side", "sector", "open_side_sector_exp_bin", "n_worst_loss_trades", "premium_share", "loss_share", "lift_vs_premium"], 16),
        "",
        "## Next Experiment",
        "",
        "Build `SECTOR_WAVE_ALLOC` as a portfolio-stage experiment-local MC variant:",
        "",
        "- First family: `SWA-PUT`, scaling only new PUT allocation by side-sector open exposure.",
        "- Use a smooth exposure ramp, not a sector blacklist: start near 1.0 below 10% open side-sector premium, fade progressively above 20-35%, and saturate above 50%.",
        "- Modulate the fade by sector wave state only if Phase/RSI cells show consistent lift across 2022, 2024, 22-now, and 5y.",
        "- Keep sector names diagnostic only; the sector top cells rotate by window.",
        "",
    ])


def main() -> None:
    if not LEDGER.exists():
        raise SystemExit(f"missing ledger: {LEDGER}")

    df = pl.scan_parquet(LEDGER)
    exposure = surface(df, ["open_side_sector_exp_bin"])
    phase = surface(df, ["sector_phase_bin", "open_side_sector_exp_bin"])
    rsi = surface(df, ["sector_rsi_bin", "open_side_sector_exp_bin"])
    rs = surface(df, ["stock_rs_abs_bin", "open_side_sector_exp_bin"])
    sector_diag = surface(df, ["sector", "open_side_sector_exp_bin"])

    for name, table in [
        ("exposure", exposure),
        ("phase", phase),
        ("rsi", rsi),
        ("rs", rs),
        ("sector_diag", sector_diag),
    ]:
        out = write_csv(table, name)
        print(f"[save] {out.relative_to(ROOT)} rows={table.height:,}", flush=True)

    OUT_MD.write_text(build_findings(exposure, phase, rsi, rs, sector_diag), encoding="utf-8")
    print(f"[save] {OUT_MD.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
