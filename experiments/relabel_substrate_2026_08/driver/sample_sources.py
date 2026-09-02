#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sample_sources.py -- PREREG "sample-first" step for the honest_ledger build
(experiments/relabel_substrate_2026_08/PREREG.md, locked 7fa41127).

Inspects 50 rows of EVERY source the ledger build touches, BEFORE any parser
is written against them: Score (MySQL), barrier_outcomes (DuckDB mirror),
B:\\polygon_derived\\liquidity_map\\signal_liquidity.parquet,
B:\\polygon_derived\\ledger_v2\\ledger.parquet, Stock.market_cap + PriceHistory
(PIT mcap inputs), and tools/ct_predicate.py's availability.

Pure read-only recon. Prints schemas + samples; writes nothing except its own
log. Safe to run directly (no DB writes, no queue needed -- "genuinely light"
per CLAUDE.md's queue-vs-direct rule: a handful of LIMIT-50 / head() reads).

Usage: python experiments/relabel_substrate_2026_08/driver/sample_sources.py
"""
from __future__ import annotations

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_THIS_DIR)
_EXPERIMENTS_DIR = os.path.dirname(_EXP_DIR)
_REPO_ROOT = os.path.dirname(_EXPERIMENTS_DIR)

for _d in (_THIS_DIR, _REPO_ROOT):
    if _d not in sys.path:
        sys.path.insert(0, _d)

assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} (from __file__={__file__!r})"
)


def hr(title):
    print(f"\n{'='*100}\n{title}\n{'='*100}")


def sample_scores():
    hr("1. Score (MySQL) -- version=74, overall>=70, sample 50")
    from database.models.core import Score, AlgorithmVersion
    active = AlgorithmVersion.get_active_scores_version()
    print(f"[INFO] AlgorithmVersion.get_active_scores_version() -> id={getattr(active, 'id', None)} "
          f"commit={getattr(active, 'git_commit', None)}")
    rows = list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                     Score.macd, Score.rsi, Score.bb, Score.stoch, Score.technical_alignment,
                     Score.regime_multiplier, Score.volume_signal, Score.version,
                     Score.weight_info)
        .where(Score.version == 74, Score.overall >= 70)
        .order_by(Score.date.desc())
        .limit(50)
    )
    print(f"[INFO] sample n={len(rows)}")
    for r in rows[:10]:
        print(f"  {r.symbol_id} {r.date} overall={r.overall} trend={r.trend} macd={r.macd} "
              f"rsi={r.rsi} bb={r.bb} stoch={r.stoch} ta={r.technical_alignment} "
              f"regime_mult={r.regime_multiplier} vsig={r.volume_signal}")
    wi = rows[0].weight_info if rows else None
    print(f"[INFO] weight_info sample (row 0), first 400 chars: {str(wi)[:400]}")

    # Full in-window population counts for acceptance check (a) -- the two
    # PREREG-cited reference windows, computed directly off Score (not via
    # monte_carlo.load_signals(), which mutates .overall via CTSL in-place --
    # see driver docstring / FINDINGS for why raw Score is population truth).
    from datetime import date, timedelta
    today = date.today()
    win_5y = today - timedelta(days=365 * 5)
    win_22now = date(2022, 1, 1)
    for label, d0 in (('5y', win_5y), ('22-now', win_22now)):
        n = (Score.select().where(Score.version == 74, Score.overall >= 70,
                                   Score.date >= d0, Score.date <= today).count())
        print(f"[POPULATION] {label} ({d0}..{today}): raw Score count version=74 overall>=70 -> {n}")

    n_full = (Score.select().where(Score.version == 74, Score.overall >= 70,
                                    Score.date >= date(2021, 1, 1), Score.date <= today).count())
    print(f"[POPULATION] PREREG range (2021-01-01..{today}): raw Score count -> {n_full}")

    # Delisted-inclusion sanity: does this population already include symbols
    # with a non-null Stock.delisted_date (PREREG "delisted INCLUDED")?
    from database.models.core import Stock
    delisted_syms = {s.symbol for s in Stock.select(Stock.symbol).where(Stock.delisted_date.is_null(False))}
    print(f"[INFO] {len(delisted_syms)} symbols carry a delisted_date in `stocks`")
    n_delisted_in_pop = (Score.select()
                          .where(Score.version == 74, Score.overall >= 70,
                                 Score.date >= date(2021, 1, 1), Score.date <= today,
                                 Score.symbol.in_(list(delisted_syms)) if delisted_syms else False)
                          .count()) if delisted_syms else 0
    print(f"[POPULATION] of which rows belong to a delisted symbol: {n_delisted_in_pop}")


def sample_barrier_cache():
    hr("2. barrier_outcomes (DuckDB mirror) -- side=low, w_days=15, barrier_set=30dte_generic")
    from database.barrier_cache import _get_duck_con, CACHE_DUCK, _select_backend
    backend = _select_backend()
    print(f"[INFO] backend={backend} duck_path={CACHE_DUCK} exists={CACHE_DUCK.exists()}")
    if backend != 'duck':
        print("[WARN] duck mirror not selected -- L1 build will fall back to sqlite path")
        return
    con = _get_duck_con()
    df = con.execute("""
        SELECT symbol, date, side, w_days, barrier_set, result, exit_offset,
               exit_close, exit_return, mae_pct, mfe_pct, result_u, entry_close,
               sigma_pct, exit_bars, fire_type, fire_open, fire_high, fire_low
        FROM barrier_outcomes
        WHERE side='low' AND w_days=15 AND barrier_set='30dte_generic'
        ORDER BY date DESC LIMIT 50
    """).fetchdf()
    print(f"[INFO] sample n={len(df)}, columns={list(df.columns)}")
    print(df.head(10).to_string())
    n_total = con.execute("""
        SELECT COUNT(*) FROM barrier_outcomes
        WHERE side='low' AND w_days=15 AND barrier_set='30dte_generic'
    """).fetchone()[0]
    print(f"[INFO] total rows at (side=low, w_days=15, barrier_set=30dte_generic): {n_total:,}")
    # Duplicate-key check per the ctsl_vehicle trap #2 (w_days must be pinned).
    n_dupe_check = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT symbol, date, side, w_days, barrier_set, COUNT(*) c
            FROM barrier_outcomes
            WHERE side='low' AND w_days=15 AND barrier_set='30dte_generic'
            GROUP BY 1,2,3,4,5 HAVING c > 1
        )
    """).fetchone()[0]
    print(f"[CHECK] duplicate (symbol,date,side,w_days,barrier_set) keys at our pinned tuple: {n_dupe_check} "
          f"(expect 0 -- primary key already enforces this, this is a belt-and-suspenders check)")


def sample_liquidity_map():
    hr("3. B:\\polygon_derived\\liquidity_map\\signal_liquidity.parquet")
    import polars as pl
    path = r'B:\polygon_derived\liquidity_map\signal_liquidity.parquet'
    if not os.path.isfile(path):
        print(f"[STOP] missing: {path}")
        return
    df = pl.read_parquet(path)
    print(f"[INFO] shape={df.shape} columns={df.columns}")
    print(f"[INFO] dtypes={dict(zip(df.columns, df.dtypes))}")
    print(df.head(50).to_pandas().to_string())
    # date span + coverage sanity
    date_col = 'date' if 'date' in df.columns else ('signal_date' if 'signal_date' in df.columns else None)
    if date_col:
        print(f"[INFO] date span ({date_col}): {df[date_col].min()} .. {df[date_col].max()}")
    print(f"[INFO] n_distinct symbols: "
          f"{df['symbol'].n_unique() if 'symbol' in df.columns else 'NO symbol COLUMN'}")

    # Compare against monte_carlo._liquidity_load()'s own default source
    # (cache_path('liquidity_option_volume_30d')) -- are these the SAME file,
    # a copy, or genuinely different data?
    try:
        from database.bulk_cache import cache_path
        p2 = cache_path('liquidity_option_volume_30d')
        print(f"[INFO] monte_carlo._liquidity_load() default path: {p2} exists={p2.exists()}")
        if p2.exists():
            df2 = pl.read_parquet(p2)
            print(f"[INFO] that file: shape={df2.shape} columns={df2.columns}")
    except Exception as e:
        print(f"[WARN] cache_path probe failed: {e}")


def sample_ledger_v2():
    hr("4. B:\\polygon_derived\\ledger_v2\\ledger.parquet")
    import polars as pl
    path = r'B:\polygon_derived\ledger_v2\ledger.parquet'
    if not os.path.isfile(path):
        print(f"[STOP] missing: {path}")
        return
    df = pl.read_parquet(path)
    print(f"[INFO] shape={df.shape} columns={df.columns}")
    print(f"[INFO] dtypes={dict(zip(df.columns, df.dtypes))}")
    print(df.head(50).to_pandas().to_string())

    paths_dir = r'B:\polygon_derived\ledger_v2\paths'
    if os.path.isdir(paths_dir):
        entries = os.listdir(paths_dir)[:10]
        print(f"[INFO] paths/ dir sample entries (first 10 of {len(os.listdir(paths_dir))}): {entries}")

    smoke_dir = r'B:\polygon_derived\ledger_v2\_smoke'
    if os.path.isdir(smoke_dir):
        print(f"[INFO] _smoke/ dir entries: {os.listdir(smoke_dir)}")


def sample_pit_mcap_inputs():
    hr("5. PIT mcap inputs -- Stock.market_cap coverage + delisted overlap")
    from database.models.core import Stock
    n_total = Stock.select().count()
    n_mcap = Stock.select().where(Stock.market_cap.is_null(False)).count()
    n_delisted = Stock.select().where(Stock.delisted_date.is_null(False)).count()
    n_delisted_with_mcap = Stock.select().where(
        Stock.delisted_date.is_null(False), Stock.market_cap.is_null(False)).count()
    print(f"[INFO] stocks total={n_total} with market_cap={n_mcap} ({n_mcap/max(1,n_total)*100:.1f}%)")
    print(f"[INFO] delisted={n_delisted}, of which with market_cap={n_delisted_with_mcap} "
          f"({n_delisted_with_mcap/max(1,n_delisted)*100:.1f}%)")
    sample = list(Stock.select(Stock.symbol, Stock.market_cap, Stock.delisted_date).limit(20))
    for s in sample:
        print(f"  {s.symbol} market_cap={s.market_cap} delisted_date={s.delisted_date}")


def check_ct_predicate():
    hr("6. tools/ct_predicate.py -- Builder B dependency status")
    path = os.path.join(_REPO_ROOT, 'tools', 'ct_predicate.py')
    print(f"[INFO] path checked: {path}")
    print(f"[INFO] exists: {os.path.isfile(path)}")
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            head = f.read(2000)
        print(f"[INFO] first 2000 chars:\n{head}")
    else:
        print("[STATUS] NOT YET AVAILABLE -- ct_flag will be built LAST; if still "
              "missing at that point, ct_flag ships as NULL per the orchestrator's "
              "explicit instruction (no self-extraction).")


def main():
    sample_scores()
    sample_barrier_cache()
    sample_liquidity_map()
    sample_ledger_v2()
    sample_pit_mcap_inputs()
    check_ct_predicate()
    print("\n\n=== sample_sources.py DONE ===")


if __name__ == '__main__':
    main()
