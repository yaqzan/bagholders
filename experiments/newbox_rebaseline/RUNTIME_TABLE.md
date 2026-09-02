# RUNTIME_TABLE — measured new-box wall-clocks (P1.D)

**Provenance:** measured 2026-07-29 on bookmaker (9950X3D, 32 threads) from the P2.A Sharadar
rebuild chain's queue-persisted task timings — NOT from a dedicated `run_refresh_10y.py` run
(P1.D satisfied-by-proxy; a second full refresh would have recomputed identical rows purely to
time them). Universe: **1,638 symbols (600 delisted)** vs the old-box-era 895 — scale per-symbol
estimates accordingly. Substrate: post-convention-rebuild, DECIMAL(18,6).

| Stage | Wall-clock | Notes |
|---|---|---|
| Sharadar bulk pull (4 tables, 967 MB) | 45s | network-bound |
| CSV -> ticker-sorted parquet (46.2M rows) | 31s | |
| price_history rebuild (1,622 syms / 7.1M bars) | 3m56s | **plain chunked INSERTs, NOT `PriceHistory.bulk_build`** (REPLACE INTO blew the 30s read_timeout) — do not cite as a bulk_build benchmark |
| precision widening (2 tables, 8 cols) | 2m50s | DECIMAL(10,2) -> (18,6) |
| rebuild re-run at DECIMAL(18,6) | 4m11s | |
| indicators + weekly aggregates (1,638 syms) | 33m18s | 8 workers |
| breadth backfill, full history (7,531 days) | 27m02s | |
| regime backfill, full history (7,531 days) | 59m02s | |
| score recalc 10y + assess/temporal tail | 38m18s | 22m49s scoring; **EXCLUDES --dte** |
| score recalc 30y (1997-12-31 ->) + assess/temporal tail | 65m53s | 51m38s scoring; **EXCLUDES --dte** |
| orphan-score purge (281k rows) | 55s | |

**The single biggest runtime lever:** DTE regeneration. Both recalc figures EXCLUDE `--dte`;
`recalculate_dte_for_stock` runs a full per-date pass and measured at **~85% of total chain
runtime** — with it included, the 10y pass projected to **~6.7h instead of 38m**. Any queue-ops
estimate for `trader recalculate --all` must budget for this explicitly.

**MC engine timings (same box, same day, measured by this program directly):**

| Run | Wall-clock |
|---|---|
| Parity gate: 1 arm x 12 windows x N=500 (8 cores) | 4m17s |
| E-cert: 1 arm x (12 std N=2000 + 4 deep N=1000) (10 cores) | 9m25s-10m14s |
| E-cert: 2 arms sequential (10 cores) | 17m36s |
| Noise floor: 8 batches x 4 tiers x 3 windows (10 cores) | 17m03s |

Old-box comparison figures are deliberately omitted: cross-box comparisons are dead per the
P0.B parity verdict (R1) — these are the reference wall-clocks going forward.
