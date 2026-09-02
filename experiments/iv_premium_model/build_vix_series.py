"""IV Premium Model -- VIX series exporter (wiring-support script, not recon).

Materializes MarketRegime.vix_close by date to a small parquet ONCE, so the
engine's _load_vix_series() (monte_carlo.py / backtest_cascade.py) never issues
a live MySQL query per sweep worker -- same on-demand-parquet-cache precedent
as the IV panel itself (.cache/polygon_iv/iv_ledger_polygon.parquet) and
database/bulk_cache.py. See DESIGN.md section 6 (wiring spec).

MarketRegime spans 1995-01-03..present with full vix_close coverage (verified
2026-07-12: 0 NULL rows across the whole table) -- the full 2016-2026 A/B grid
is priceable from this single small export (~7,900 rows).

Read-only: MySQL SELECT only. No writes to MySQL, no engine edits.

Output: .cache/iv_premium_model/vix_series.parquet (columns: date [ISO string],
vix_close [float]).

Usage: python experiments/iv_premium_model/build_vix_series.py
ASCII-only output.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

import polars as pl

from database.trader_database import DB  # noqa: E402

OUT_DIR = os.path.join(_ROOT, ".cache", "iv_premium_model")
OUT_PATH = os.path.join(OUT_DIR, "vix_series.parquet")


def main():
    cur = DB.execute_sql(
        "SELECT date, vix_close FROM market_regime "
        "WHERE vix_close IS NOT NULL ORDER BY date")
    rows = cur.fetchall()
    if not rows:
        raise SystemExit("[build_vix_series] no rows returned -- refusing to write empty cache")
    dates = [d.isoformat() for d, _ in rows]
    vixs = [float(v) for _, v in rows]
    df = pl.DataFrame({"date": dates, "vix_close": vixs})
    os.makedirs(OUT_DIR, exist_ok=True)
    df.write_parquet(OUT_PATH, compression="snappy")
    print(f"[build_vix_series] wrote {len(df)} rows -> {OUT_PATH}")
    print(f"[build_vix_series] date range: {dates[0]} .. {dates[-1]}")


if __name__ == "__main__":
    main()
