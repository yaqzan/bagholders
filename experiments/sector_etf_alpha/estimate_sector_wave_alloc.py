"""Fast first-order estimator for sector-wave allocation curves.

This replays existing MC trade tapes and rescales premium per trade using
sector-wave rules. It is not a replacement for a full MC engine sweep because
cash/cap admission and reallocation are kept fixed from the original tape. It
is the Bayesian screening step: identify allocation curves that plausibly cut
worst DD before paying the cost to wire a full portfolio-stage variant.

Output:
  experiments/sector_etf_alpha/dd_probe/sector_wave_alloc_estimates.csv
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "experiments" / "sector_etf_alpha" / "dd_probe"
LEDGER = PROBE / "sector_wave_ledger.parquet"
OUT = PROBE / "sector_wave_alloc_estimates.csv"
STARTING_CASH = 10_000.0
COLLAPSE_THRESHOLD = 0.20

EXP_SOFT = {
    "exp_lt10": 1.00,
    "exp_10_20": 0.85,
    "exp_20_35": 0.65,
    "exp_35_50": 0.50,
    "exp_ge50": 0.35,
}
EXP_BALANCED = {
    "exp_lt10": 1.00,
    "exp_10_20": 0.90,
    "exp_20_35": 0.75,
    "exp_35_50": 0.60,
    "exp_ge50": 0.45,
}
EXP_STRONG = {
    "exp_lt10": 1.00,
    "exp_10_20": 0.75,
    "exp_20_35": 0.45,
    "exp_35_50": 0.30,
    "exp_ge50": 0.20,
}
NON_OVERSOLD_RSI = {"rsi_45_55", "rsi_55_70", "rsi_gt70"}
NON_OVERSOLD_PHASE = {"phase_mid", "phase_overheated", "phase_deep_overheated"}


def scale_for(row: dict, curve: str) -> float:
    if curve == "V4_REPLAY":
        return 1.0

    side = row["side"]
    exp_bin = row["open_side_sector_exp_bin"]
    rsi_bin = row["sector_rsi_bin"]
    phase_bin = row["sector_phase_bin"]

    if curve == "SWA_PUT_BALANCED":
        return EXP_BALANCED.get(exp_bin, 1.0) if side == "put" else 1.0
    if curve == "SWA_PUT_SOFT":
        return EXP_SOFT.get(exp_bin, 1.0) if side == "put" else 1.0
    if curve == "SWA_PUT_STRONG":
        return EXP_STRONG.get(exp_bin, 1.0) if side == "put" else 1.0
    if curve == "SWA_PUT_RSI45_SOFT":
        if side == "put" and rsi_bin in NON_OVERSOLD_RSI:
            return EXP_SOFT.get(exp_bin, 1.0)
        return 1.0
    if curve == "SWA_PUT_PHASEMID_SOFT":
        if side == "put" and phase_bin in NON_OVERSOLD_PHASE:
            return EXP_SOFT.get(exp_bin, 1.0)
        return 1.0
    if curve == "SWA_BOTH_SOFT":
        return EXP_SOFT.get(exp_bin, 1.0)

    raise ValueError(f"unknown curve {curve}")


def replay_group(rows: list[dict], curve: str) -> dict:
    entries_by_day: dict[object, list[dict]] = defaultdict(list)
    exits_by_day: dict[object, list[dict]] = defaultdict(list)
    days = set()
    total_orig_premium = 0.0
    total_scaled_premium = 0.0
    call_trades = 0
    put_trades = 0

    for i, row in enumerate(rows):
        scale = scale_for(row, curve)
        premium = float(row["premium_cost"] or 0.0)
        scaled = premium * scale
        rec = {
            "id": i,
            "entry_date": row["entry_date"],
            "exit_date": row["exit_date"],
            "side": row["side"],
            "premium": scaled,
            "pnl": float(row["option_pnl"] or 0.0),
        }
        entries_by_day[row["entry_date"]].append(rec)
        exits_by_day[row["exit_date"]].append(rec)
        days.add(row["entry_date"])
        days.add(row["exit_date"])
        total_orig_premium += premium
        total_scaled_premium += scaled
        if row["side"] == "call":
            call_trades += 1
        else:
            put_trades += 1

    cash = STARTING_CASH
    open_premium_by_id: dict[int, float] = {}
    peak = STARTING_CASH
    max_dd = 0.0

    for day in sorted(days):
        for rec in exits_by_day.get(day, []):
            premium = open_premium_by_id.pop(rec["id"], rec["premium"])
            cash += premium * (1.0 + rec["pnl"])

        value = cash + sum(open_premium_by_id.values())
        if value > peak:
            peak = value
        dd = (peak - value) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        if value <= STARTING_CASH * COLLAPSE_THRESHOLD:
            break

        for rec in entries_by_day.get(day, []):
            premium = rec["premium"]
            cash -= premium
            open_premium_by_id[rec["id"]] = premium

    final_value = cash + sum(open_premium_by_id.values())
    final_dd = (peak - final_value) / peak if peak > 0 else 0.0
    max_dd = max(max_dd, final_dd)
    return {
        "final": final_value,
        "max_dd": max_dd,
        "call_trades": call_trades,
        "put_trades": put_trades,
        "premium_scale": total_scaled_premium / total_orig_premium if total_orig_premium > 0 else 1.0,
    }


def pct(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def summarize(window: str, curve: str, seed_results: list[dict]) -> dict:
    finals = [r["final"] for r in seed_results]
    dds = [r["max_dd"] for r in seed_results]
    final_sorted = sorted(finals)
    return {
        "window": window,
        "curve": curve,
        "n": len(seed_results),
        "mean_ret_pct": (sum(finals) / len(finals) / STARTING_CASH - 1.0) * 100,
        "median_ret_pct": (pct(final_sorted, 0.50) / STARTING_CASH - 1.0) * 100,
        "p25_ret_pct": (pct(final_sorted, 0.25) / STARTING_CASH - 1.0) * 100,
        "p75_ret_pct": (pct(final_sorted, 0.75) / STARTING_CASH - 1.0) * 100,
        "worst_dd_pct": max(dds) * 100,
        "mean_dd_pct": sum(dds) / len(dds) * 100,
        "median_dd_pct": pct(sorted(dds), 0.50) * 100,
        "p_coll_pct": sum(1 for f in finals if f <= STARTING_CASH * COLLAPSE_THRESHOLD) / len(finals) * 100,
        "avg_premium_scale": sum(r["premium_scale"] for r in seed_results) / len(seed_results),
        "avg_call_trades": sum(r["call_trades"] for r in seed_results) / len(seed_results),
        "avg_put_trades": sum(r["put_trades"] for r in seed_results) / len(seed_results),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="V4")
    ap.add_argument("--windows", default="2022,2024,22-now,5y")
    ap.add_argument("--curves", default="V4_REPLAY,SWA_PUT_BALANCED,SWA_PUT_SOFT,SWA_PUT_STRONG,SWA_PUT_RSI45_SOFT,SWA_PUT_PHASEMID_SOFT,SWA_BOTH_SOFT")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    windows = [x.strip() for x in args.windows.split(",") if x.strip()]
    curves = [x.strip() for x in args.curves.split(",") if x.strip()]

    cols = [
        "variant", "window", "seed", "entry_date", "exit_date", "side",
        "premium_cost", "option_pnl", "open_side_sector_exp_bin",
        "sector_rsi_bin", "sector_phase_bin",
    ]
    df = (
        pl.read_parquet(LEDGER, columns=cols)
        .filter((pl.col("variant") == args.variant) & pl.col("window").is_in(windows))
        .sort(["window", "seed", "entry_date", "exit_date"])
    )
    print(f"[load] rows={df.height:,} variant={args.variant} windows={windows}", flush=True)

    output_rows = []
    for window in windows:
        wdf = df.filter(pl.col("window") == window)
        seed_groups = wdf.partition_by("seed", as_dict=True)
        print(f"[window] {window} seeds={len(seed_groups):,} rows={wdf.height:,}", flush=True)
        for curve in curves:
            results = []
            for (_seed,), sdf in seed_groups.items():
                results.append(replay_group(sdf.to_dicts(), curve))
            output_rows.append(summarize(window, curve, results))
            last = output_rows[-1]
            print(
                f"  {curve:<22} mean={last['mean_ret_pct']:+.1f}% "
                f"worstDD={last['worst_dd_pct']:.1f}% prem={last['avg_premium_scale']:.3f}",
                flush=True,
            )

    out = Path(args.out)
    pl.DataFrame(output_rows).write_csv(out)
    print(f"[save] {out.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
