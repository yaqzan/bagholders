"""Generic smoke MC runner for sector ETF alpha variants.

Given a label from phase_g_winners.json, build a (symbol, date) score lookup and
patch monte_carlo signal loaders at runtime. Production code is not modified.
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

from experiments.sector_etf_alpha.swpm_v2 import SWPMv2Params, apply_swpm_v2

LOCAL = ROOT / ".cache" / "sector_etf_alpha"
SCREEN = ROOT / ".cache" / "sector_etf_screen"
OUT = ROOT / "experiments" / "sector_etf_alpha"


SECTOR_ETF_MAP = {
    "technology": "XLK",
    "healthcare": "XLV",
    "consumer-cyclical": "XLY",
    "industrials": "XLI",
    "basic-materials": "XLB",
    "real-estate": "XLRE",
    "financial-services": "XLF",
    "energy": "XLE",
    "consumer-defensive": "XLP",
    "utilities": "XLU",
    "communication-services": "XLC",
}


def load_params(label: str) -> SWPMv2Params:
    with open(OUT / "phase_g_winners.json", "r", encoding="utf-8") as f:
        winners = json.load(f)
    if label not in winners:
        raise SystemExit(f"label {label!r} not found in phase_g_winners.json")
    return SWPMv2Params(**winners[label]["params"])


def _add_features_for_unresolved(unr_df: pl.DataFrame) -> pl.DataFrame:
    meta = pl.read_parquet(SCREEN / "stock_meta.parquet")
    etf_wide = pl.read_parquet(SCREEN / "etf_features_wide.parquet")
    stock_ret = pl.read_parquet(LOCAL / "stock_returns_v46_1825.parquet")

    out = unr_df.join(meta, on="symbol", how="left")
    out = out.with_columns((pl.col("market_cap") / 1e9).alias("mcap_b"))
    out = out.join(stock_ret, on=["symbol", "date"], how="left")
    out = out.join(etf_wide, on="date", how="left")

    sec_map = pl.DataFrame({"sector": list(SECTOR_ETF_MAP), "sector_etf": list(SECTOR_ETF_MAP.values())})
    out = out.join(sec_map, on="sector", how="left")
    for k in ["pct_ema50", "pct_ema200", "rsi", "macd", "ret_5d", "ret_20d"]:
        expr = pl.lit(None, dtype=pl.Float64)
        for etf in SECTOR_ETF_MAP.values():
            col = f"{etf}_{k}"
            if col in out.columns:
                expr = pl.when(pl.col("sector_etf") == etf).then(pl.col(col)).otherwise(expr)
        out = out.with_columns(expr.alias(f"sec_etf_{k}"))
    return out


def build_lookup(label: str, params: SWPMv2Params) -> dict:
    cache = LOCAL / f"{label}_lookup.pkl"
    if cache.exists():
        with open(cache, "rb") as f:
            lookup = pickle.load(f)
        print(f"[lookup] cache hit {label}: {len(lookup):,} entries", flush=True)
        return lookup

    print(f"[lookup] building {label}", flush=True)
    coh = pl.read_parquet(LOCAL / "cohort_v46_1825.parquet")
    out = apply_swpm_v2(coh, params).select(["symbol", "date", "overall", "new_overall"])

    peaks = pl.read_parquet(LOCAL / "peaks_v46_1825.parquet")
    coh_keys = set(zip(coh["symbol"].to_list(), coh["date"].to_list()))
    peak_keys = set(zip(peaks["symbol"].to_list(), peaks["date"].to_list()))
    unresolved = peak_keys - coh_keys
    print(f"  resolved cohort={len(coh):,} unresolved peaks={len(unresolved):,}", flush=True)

    if unresolved:
        unr_df = peaks.filter(
            pl.struct(["symbol", "date"]).map_elements(
                lambda x: (x["symbol"], x["date"]) in unresolved, return_dtype=pl.Boolean
            )
        )
        out_unr = apply_swpm_v2(_add_features_for_unresolved(unr_df), params)
        out_unr = out_unr.select(["symbol", "date", "overall", "new_overall"])
        out = pl.concat([out, out_unr], how="vertical_relaxed")

    lookup = {}
    for sym, dt, ov, new_ov in out.iter_rows():
        lookup[(sym, dt)] = (int(ov), int(new_ov))

    with open(cache, "wb") as f:
        pickle.dump(lookup, f)
    print(f"[lookup] saved {cache} entries={len(lookup):,}", flush=True)
    return lookup


def patch_mc(mc_module, lookup: dict, overflow: int = 70, put_thresh: int = 25):
    orig_load_signals = mc_module.load_signals
    orig_load_put_signals = mc_module.load_put_signals

    def patched_load_signals(version, d_start, d_end):
        sigs = orig_load_signals(version, d_start, d_end)
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
        print(
            f"[patch_call] orig={len(sigs):,} kept={len(kept):,} modified={modified:,} dropped={dropped:,}",
            flush=True,
        )
        return kept

    def patched_load_put_signals(version, d_start, d_end):
        sigs = orig_load_put_signals(version, d_start, d_end)
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
        print(
            f"[patch_put] orig={len(sigs):,} kept={len(kept):,} modified={modified:,} dropped={dropped:,}",
            flush=True,
        )
        return kept

    mc_module.load_signals = patched_load_signals
    mc_module.load_put_signals = patched_load_put_signals
    return orig_load_signals, orig_load_put_signals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--mode", choices=["baseline", "treatment"], required=True)
    ap.add_argument("--window", default="2024")
    ap.add_argument("--n", type=int, default=150)
    args = ap.parse_args()

    lookup = None
    if args.mode == "treatment":
        lookup = build_lookup(args.label, load_params(args.label))

    os.environ["N_ITER_OVERRIDE"] = str(args.n)
    os.environ["WINDOWS_OVERRIDE"] = args.window
    os.environ["MC_NO_MP"] = "1"

    print(f"[smoke] label={args.label} mode={args.mode} window={args.window} n={args.n}", flush=True)
    import monte_carlo as mc

    if args.mode == "treatment":
        orig_call, orig_put = patch_mc(mc, lookup)
    else:
        orig_call = orig_put = None

    try:
        t0 = time.time()
        mc.main()
        print(f"[smoke] done elapsed={time.time() - t0:.0f}s", flush=True)
    finally:
        if args.mode == "treatment":
            mc.load_signals = orig_call
            mc.load_put_signals = orig_put
            print("[smoke] patches removed", flush=True)


if __name__ == "__main__":
    main()
