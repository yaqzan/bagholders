"""Sector concentration DD probe for sector ETF alpha variants.

Runs monte_carlo with trade-tape collection enabled and writes isolated tape
artifacts under experiments/sector_etf_alpha/dd_probe so the shared
.cache/dd_ledger/* files are not overwritten.

Use this to answer: during the worst DD episodes, which sectors/sides carried
the actual loss, and did sector-specific concentration cause the V4 failure?
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.sector_etf_alpha.smoke_mc_v4 import V4_PARAMS
from experiments.sector_etf_alpha.smoke_mc_variant import build_lookup as build_g_lookup
from experiments.sector_etf_alpha.smoke_mc_variant import load_params as load_g_params
from experiments.sector_etf_alpha.swpm_v2 import apply_swpm_v2

LOCAL = ROOT / ".cache" / "sector_etf_alpha"
OUT = ROOT / "experiments" / "sector_etf_alpha" / "dd_probe"
OUT.mkdir(parents=True, exist_ok=True)


def load_variant_params(label: str):
    if label.upper() == "V4":
        return V4_PARAMS
    return load_g_params(label)


def build_lookup(label: str) -> dict:
    if label.upper() != "V4":
        return build_g_lookup(label, load_variant_params(label))

    cache = LOCAL / "v4_lookup.pkl"
    if cache.exists():
        with open(cache, "rb") as f:
            d = pickle.load(f)
        print(f"[lookup] V4 cache hit: {len(d):,} entries", flush=True)
        return d

    coh = pl.read_parquet(LOCAL / "cohort_v46_1825.parquet")
    out = apply_swpm_v2(coh, V4_PARAMS).select(["symbol", "date", "overall", "new_overall"])
    lookup = {(sym, dt): (int(ov), int(new_ov)) for sym, dt, ov, new_ov in out.iter_rows()}
    with open(cache, "wb") as f:
        pickle.dump(lookup, f)
    print(f"[lookup] V4 saved: {len(lookup):,} entries", flush=True)
    return lookup


def patch_signal_loaders(mc, lookup: dict, overflow: int = 70, put_thresh: int = 25):
    orig_call = mc.load_signals
    orig_put = mc.load_put_signals

    def patched_load_signals(version, d_start, d_end):
        sigs = orig_call(version, d_start, d_end)
        kept = []
        modified = 0
        dropped = 0
        for s in sigs:
            key = (s.symbol_id, s.date.isoformat() if hasattr(s.date, "isoformat") else str(s.date))
            entry = lookup.get(key)
            if entry is None:
                kept.append(s)
                continue
            orig_ov, new_ov = entry
            if new_ov >= overflow:
                if new_ov != orig_ov:
                    s.overall = new_ov
                    modified += 1
                kept.append(s)
            else:
                dropped += 1
        kept.sort(key=lambda s: (s.date, -int(s.overall)))
        print(f"[patch_call] orig={len(sigs):,} kept={len(kept):,} modified={modified:,} dropped={dropped:,}", flush=True)
        return kept

    def patched_load_put_signals(version, d_start, d_end):
        sigs = orig_put(version, d_start, d_end)
        kept = []
        modified = 0
        dropped = 0
        for s in sigs:
            key = (s.symbol_id, s.date.isoformat() if hasattr(s.date, "isoformat") else str(s.date))
            entry = lookup.get(key)
            if entry is None:
                kept.append(s)
                continue
            orig_ov, new_ov = entry
            if new_ov <= put_thresh:
                if new_ov != orig_ov:
                    s.overall = new_ov
                    modified += 1
                kept.append(s)
            else:
                dropped += 1
        kept.sort(key=lambda s: (s.date, int(s.overall)))
        print(f"[patch_put] orig={len(sigs):,} kept={len(kept):,} modified={modified:,} dropped={dropped:,}", flush=True)
        return kept

    mc.load_signals = patched_load_signals
    mc.load_put_signals = patched_load_put_signals
    return orig_call, orig_put


def patch_tape_dump(mc, variant: str):
    orig_dump = mc._dump_trade_tape

    def dump(label, seeds, results, trading_days):
        trade_cols = (
            "seed", "sym_id", "entry_date", "exit_date", "side", "score", "tier",
            "ct", "ern", "premium_cost", "option_pnl", "outcome",
            "entry_value", "entry_peak", "entry_dd",
            "entry_open_calls", "entry_open_puts",
        )
        rows = []
        for seed, r in zip(seeds, results):
            for t in r.get("_tape") or []:
                rows.append((seed,) + t)

        ep_cols = (
            "seed", "window_label", "rank",
            "start_date", "trough_date", "peak_value", "trough_value", "magnitude",
            "final_value", "max_dd",
        )
        ep_rows = []
        n_days = len(trading_days)

        def idx_to_date(i):
            if 0 <= i < n_days:
                d = trading_days[i]
                return d.isoformat() if hasattr(d, "isoformat") else str(d)
            return None

        for seed, r in zip(seeds, results):
            for rank, (s_idx, t_idx, peak, trough) in enumerate(r.get("_episodes", [])):
                mag = (peak - trough) / peak if peak > 0 else 0
                ep_rows.append((
                    seed, label, rank, idx_to_date(s_idx), idx_to_date(t_idx),
                    peak, trough, mag, r["final"], r["max_dd"],
                ))

        safe = variant.replace("/", "_")
        tape_path = OUT / f"{safe}_{label}_tape.parquet"
        ep_path = OUT / f"{safe}_{label}_episodes.parquet"
        pl.DataFrame(rows, schema=trade_cols, orient="row").write_parquet(tape_path)
        pl.DataFrame(ep_rows, schema=ep_cols, orient="row").write_parquet(ep_path)
        print(f"[probe] wrote {tape_path.name} rows={len(rows):,}", flush=True)
        print(f"[probe] wrote {ep_path.name} rows={len(ep_rows):,}", flush=True)

    mc._dump_trade_tape = dump
    return orig_dump


def analyze(variant: str, window: str) -> dict:
    safe = variant.replace("/", "_")
    tape_path = OUT / f"{safe}_{window}_tape.parquet"
    ep_path = OUT / f"{safe}_{window}_episodes.parquet"
    if not tape_path.exists() or not ep_path.exists():
        raise SystemExit(f"missing probe artifacts for {variant} {window}")

    tape = pl.read_parquet(tape_path)
    eps = pl.read_parquet(ep_path)
    worst = eps.filter(pl.col("rank") == 0).select(
        "seed",
        pl.col("start_date").alias("ep_start"),
        pl.col("trough_date").alias("ep_trough"),
        (pl.col("peak_value") - pl.col("trough_value")).alias("ep_loss_dollars"),
        "magnitude",
    )
    tape = tape.join(worst, on="seed", how="left").with_columns([
        ((pl.col("entry_date").cast(pl.Utf8) <= pl.col("ep_trough"))
         & (pl.col("exit_date").cast(pl.Utf8) >= pl.col("ep_start"))).fill_null(False).alias("in_worst_episode"),
        (pl.col("premium_cost") * pl.col("option_pnl")).alias("pnl_dollars"),
    ])

    stock_meta = pl.read_parquet(ROOT / ".cache" / "sector_etf_screen" / "stock_meta.parquet")
    meta_cols = [c for c in ["symbol", "sector", "industry", "market_cap"] if c in stock_meta.columns]
    tape = tape.join(stock_meta.select(meta_cols).unique("symbol"), left_on="sym_id", right_on="symbol", how="left")
    tape = tape.with_columns(pl.col("sector").fill_null("unknown"))

    ep = tape.filter(pl.col("in_worst_episode"))
    losses = ep.filter(pl.col("pnl_dollars") < 0)
    total_loss = abs(float(losses["pnl_dollars"].sum() or 0.0))

    sector = (
        losses.group_by(["side", "sector"])
        .agg([
            pl.len().alias("loss_trades"),
            pl.col("premium_cost").sum().alias("premium_at_risk"),
            pl.col("pnl_dollars").sum().alias("loss_dollars"),
            pl.col("option_pnl").mean().alias("avg_option_pnl"),
            pl.col("score").mean().alias("avg_score"),
        ])
        .with_columns((pl.col("loss_dollars").abs() / total_loss).alias("loss_share"))
        .sort("loss_share", descending=True)
    )

    by_seed_sector = (
        losses.group_by(["seed", "side", "sector"])
        .agg(pl.col("pnl_dollars").sum().alias("loss_dollars"))
        .with_columns(pl.col("loss_dollars").abs().alias("abs_loss"))
        .sort(["seed", "abs_loss"], descending=[False, True])
    )
    top_seed = by_seed_sector.group_by("seed").head(1)
    concentration = top_seed.select([
        pl.len().alias("seeds"),
        pl.col("abs_loss").median().alias("median_top_sector_loss"),
        pl.col("abs_loss").mean().alias("mean_top_sector_loss"),
    ])

    out = {
        "variant": variant,
        "window": window,
        "n_trades": tape.height,
        "n_episode_trades": ep.height,
        "n_episode_loss_trades": losses.height,
        "total_episode_loss_dollars": total_loss,
        "sector_rows": sector.head(20).to_dicts(),
        "top_seed_sector_summary": concentration.to_dicts()[0] if concentration.height else {},
    }
    with open(OUT / f"{safe}_{window}_sector_probe.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)

    print("\n[sector loss attribution: worst episodes]", flush=True)
    print(sector.head(20), flush=True)
    print("\n[top sector per seed summary]", flush=True)
    print(concentration, flush=True)
    return out


def run(variant: str, window: str, n: int) -> None:
    os.environ["MC_TRADE_TAPE"] = "1"
    os.environ["N_ITER_OVERRIDE"] = str(n)
    os.environ["WINDOWS_OVERRIDE"] = window
    os.environ["MC_NO_MP"] = "1"
    os.environ["MC_NO_DB_PERSIST"] = "1"

    import monte_carlo as mc

    baseline = variant.upper() in {"BASE", "BASELINE", "NONE"}
    if baseline:
        orig_call, orig_put = mc.load_signals, mc.load_put_signals
    else:
        lookup = build_lookup(variant)
        orig_call, orig_put = patch_signal_loaders(mc, lookup)
    orig_dump = patch_tape_dump(mc, variant)
    try:
        t0 = time.time()
        mc.main()
        print(f"[probe] MC elapsed={time.time() - t0:.0f}s", flush=True)
    finally:
        mc.load_signals = orig_call
        mc.load_put_signals = orig_put
        mc._dump_trade_tape = orig_dump


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="V4")
    ap.add_argument("--window", default="2024")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--analyze-only", action="store_true")
    args = ap.parse_args()

    if not args.analyze_only:
        run(args.variant, args.window, args.n)
    analyze(args.variant, args.window)


if __name__ == "__main__":
    main()
