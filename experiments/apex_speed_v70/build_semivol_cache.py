"""
Build a sym_id-keyed semivol_r feature cache for the MC SVR entry-filter.

semivol_r = std(downside 60d returns) / std(upside 60d returns), strictly-prior
(reuses the prior hunt's precomputed `.cache/iv_skew/proxy_ledger.parquet`).
Cohort signal (10y, v70): INVERTED-U — low semivol_r (~0.5, call-euphoric/expensive)
is the WORST call cohort (75+ win 0.35 / 80+ pnl −0.14), very-high (~1.4, crash-mode)
also weak, middle-high (~0.9-1.1) is the sweet spot. Filter target = downweight the
low extreme (and optionally taper the very-high).

Writes `.cache/apex_speed_v70/semivol_map.parquet` (sym_id, date, semivol_r) that
monte_carlo loads into {(sym_id, date): semivol_r}. Version-independent (pure price
history) so one cache serves every candidate/window.

Usage: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/apex_speed_v70/build_semivol_cache.py
"""
import sys
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LEDGER = ROOT / ".cache" / "iv_skew" / "proxy_ledger.parquet"
OUT = ROOT / ".cache" / "apex_speed_v70"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    # Stock PK is `symbol` (string) => the MC's sym_id IS the ticker string =>
    # the ledger's `symbol` is the key directly, no mapping needed.
    L = pl.read_parquet(LEDGER).select(["symbol", "date", "semivol_r"]).filter(
        pl.col("semivol_r").is_not_null())
    print(f"ledger rows w/ semivol_r: {L.height}  syms={L['symbol'].n_unique()}")
    out = L.with_columns(
        pl.col("date").cast(pl.Utf8).str.slice(0, 10).alias("date"),
    ).rename({"symbol": "sym_id"}).select(["sym_id", "date", "semivol_r"])
    out.write_parquet(OUT / "semivol_map.parquet")
    print(f"wrote {OUT/'semivol_map.parquet'}  rows={out.height}")
    print(f"  semivol_r median={out['semivol_r'].median():.3f} "
          f"p10={out['semivol_r'].quantile(0.1):.3f} p90={out['semivol_r'].quantile(0.9):.3f}")


if __name__ == "__main__":
    main()
