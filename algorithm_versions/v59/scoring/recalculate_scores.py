#! python3
"""Recalculate scores for stocks.
Called via trader.py: python trader.py recalculate [symbol] [days] [--trend] [--bb] [--rsi] [--macd] [--stoch] [--ta] [--weekly] [--overall] [--all]
                                                   [--workers N] [--no-batch] [--force] [--full]
                                                   [--scores-only] [--tail-only] [--tail-window LOOKBACK]
                                                   [--reuse-components-from VERSION]
                                                   [--score-versions vNN[,vMM]]
Args can be in any order. Multiple flags supported. Defaults to --overall.

With no symbol and no explicit day lookback, `trader recalculate` runs **staged** passes
(1d → 7d → 30d → 1y → 2y → 3y → 5y → 7y): each pass persists a disjoint date band; each pass
loads from its lookback through the slice upper bound so volume decay stays correct. With
`force=False`, already-computed dates are not redone.

--workers N   : parallel workers. Defaults to cpu_count - 1. Pass 1 to force serial.
--no-batch    : use the original per-row DB query path instead of the batched path.
--force       : recalculate all scores even if already computed for the current version.
--full        : extend progressive passes after 7y with 10y and 25y.
--scores-only : skip the historic/assessment/temporal tail after score writes.
--tail-only   : run only the historic/assessment/temporal tail.
--reuse-components-from VERSION : clone component rows from a prior version before overall-only recompute.
--score-versions vNN[,vMM] : write historical sidecar score rows in the same batched pass.
"""

from database.models.core import Stock, MarketRegime, _load_spy_wk_composite_map, _load_mis_stress_map
from colorama import init, Fore
from datetime import date, timedelta
import multiprocessing
import os
from tqdm import tqdm

init(autoreset=True, convert=True)

DEFAULT_LOOKBACK = 365
DAILY_COMPONENTS = {'trend', 'bb', 'rsi', 'macd', 'stoch', 'ta', 'overall'}
ALL_MODES = DAILY_COMPONENTS | {'weekly', 'dte'}

# Mirror of trader.py progressive passes (outer_days, inner_days) → band [today−outer, today−inner]
STAGED_DEPTHS = [
    (1, None), (7, 1), (30, 7), (365, 30), (730, 365), (1095, 730),
    (1825, 1095), (2555, 1825),
]
STAGED_LABELS = [
    'last 1d', 'days 2–7', 'days 8–30', 'days 31–365 (1y)',
    'years 1–2', 'years 2–3', 'years 3–5', 'years 5–7',
]

# Module-level map caches — populated once per process (main + each worker)
# to avoid re-querying per stock (was N× identical queries).
_SHARED_REGIME_MAP = None
_SHARED_SPY_WK_MAP = None
_SHARED_MIS_STRESS_MAP = None
_SHARED_EXTRA_SCORE_ENGINES = None


def _default_workers():
    """Auto-pick worker count: cpu_count - 1, optionally capped by env."""
    try:
        n = os.cpu_count() or 1
    except Exception:
        n = 1
    workers = max(1, n - 1)
    cap = os.environ.get("TRADER_RECALC_MAX_WORKERS")
    if cap:
        try:
            workers = min(workers, max(1, int(cap)))
        except ValueError:
            pass
    return workers


def _load_regime_map():
    """Load MarketRegime rows into {date: (multiplier, composite)}."""
    rows = list(
        MarketRegime.select(
            MarketRegime.date, MarketRegime.regime_multiplier, MarketRegime.regime_composite
        )
        .where(MarketRegime.regime_multiplier.is_null(False))
        .namedtuples()
    )
    return {
        r.date: (
            float(r.regime_multiplier),
            float(r.regime_composite) if r.regime_composite is not None else None,
        )
        for r in rows
    }


def _normalize_score_version_tokens(raw_versions):
    if not raw_versions:
        return []
    from algorithm_versions.runtime import parse_version_tokens

    return parse_version_tokens(raw_versions)


def _load_extra_score_engines(raw_versions):
    """Resolve runtime-loadable scoring engines for historical sidecar writes."""
    tokens = _normalize_score_version_tokens(raw_versions)
    if not tokens:
        return [], []

    from algorithm_versions.runtime import resolve_versions, load_scoring_engine
    from database.models.core import AlgorithmVersion

    current = AlgorithmVersion.get_or_create_current()
    engines = []
    skipped = []
    for version in resolve_versions(tokens):
        key = f"v{int(version.id)}"
        if int(version.id) == int(current.id):
            skipped.append({"key": key, "reason": "current version already scored"})
            continue
        engines.append(load_scoring_engine(version))
    return engines, skipped


def _yearly_chunks(start, end):
    cur = start
    while cur <= end:
        chunk_end = min(end, date(cur.year, 12, 31))
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def copy_score_components_from_version(source_token, days=DEFAULT_LOOKBACK, *,
                                       symbol=None, cutoff_end=None, overwrite=True):
    """Copy existing Score component rows from another version into current.

    This is the fast path for formula-only scoring ships: clone the expensive
    component/context columns, then run an `overall` recompute for the current
    algorithm version.
    """
    from assess_scores import resolve_algorithm_version
    from database.models.core import AlgorithmVersion, StagingScore
    from database.trader_database import DB

    source = resolve_algorithm_version(source_token)
    target = AlgorithmVersion.get_or_create_current()
    if int(source.id) == int(target.id):
        raise ValueError("source version is the current target version")

    start = date.today() - timedelta(days=days)
    end = cutoff_end if cutoff_end is not None else date.today()
    cols = StagingScore.score_column_names()
    dst_cols = ", ".join(f"`{c}`" for c in ["symbol", "date", "version_id", *cols])
    src_cols = ", ".join(f"`{c}`" for c in cols)

    if overwrite:
        insert_head = "INSERT INTO"
        conflict = " ON DUPLICATE KEY UPDATE " + ", ".join(
            f"`{c}`=VALUES(`{c}`)" for c in cols
        )
    else:
        insert_head = "INSERT IGNORE INTO"
        conflict = ""

    source_rows = 0
    affected_rows = 0
    chunks = 0
    for lo, hi in _yearly_chunks(start, end):
        where = ["`version_id`=%s", "`date` >= %s", "`date` <= %s"]
        where_params = [source.id, lo, hi]
        if symbol:
            where.append("`symbol`=%s")
            where_params.append(symbol)
        where_sql = " AND ".join(where)

        count_sql = f"SELECT COUNT(*) FROM `scores` WHERE {where_sql}"
        source_rows += int(DB.execute_sql(count_sql, tuple(where_params)).fetchone()[0])

        sql = (
            f"{insert_head} `scores` ({dst_cols}) "
            f"SELECT `symbol`, `date`, %s, {src_cols} "
            f"FROM `scores` WHERE {where_sql}{conflict}"
        )
        cur = DB.execute_sql(sql, tuple([target.id, *where_params]))
        affected_rows += int(getattr(cur, "rowcount", 0) or 0)
        chunks += 1

    return {
        "source": source.production_label,
        "target": target.production_label,
        "source_rows": source_rows,
        "affected_rows": affected_rows,
        "chunks": chunks,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def recalc_db_tuning_snapshot():
    """Return lightweight MySQL sizing/tuning data for recalc preflight."""
    from database.trader_database import DB

    DB.connect(reuse_if_open=True)
    variables = {}
    for name, value in DB.execute_sql(
        "SHOW VARIABLES WHERE Variable_name IN "
        "('innodb_buffer_pool_size','innodb_log_file_size','max_connections')"
    ).fetchall():
        try:
            variables[name] = int(value)
        except Exception:
            variables[name] = value

    table_row = DB.execute_sql(
        "SELECT table_rows, data_length, index_length "
        "FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_name='scores'"
    ).fetchone()
    table_rows, data_length, index_length = table_row or (None, None, None)
    return {
        "variables": variables,
        "scores_table_rows": int(table_rows or 0),
        "scores_data_bytes": int(data_length or 0),
        "scores_index_bytes": int(index_length or 0),
    }


def print_db_tuning_hint():
    try:
        snap = recalc_db_tuning_snapshot()
    except Exception as exc:
        print(Fore.YELLOW + f"DB tuning preflight skipped: {type(exc).__name__}: {exc}")
        return

    vars_ = snap["variables"]
    pool = int(vars_.get("innodb_buffer_pool_size") or 0)
    log_file = int(vars_.get("innodb_log_file_size") or 0)
    hot_bytes = snap["scores_data_bytes"] + snap["scores_index_bytes"]
    if hot_bytes <= 0:
        return
    pool_gb = pool / (1024 ** 3)
    hot_gb = hot_bytes / (1024 ** 3)
    log_mb = log_file / (1024 ** 2)
    if pool < min(hot_bytes * 0.20, 8 * 1024 ** 3):
        print(
            Fore.YELLOW
            + "DB tuning warning: scores table is "
            + f"{hot_gb:.1f} GB data+index but innodb_buffer_pool_size is "
            + f"{pool_gb:.2f} GB. Recalc will be disk-bound; target 8-16 GB "
            + "on this machine before expecting worker scaling to help."
        )
    if log_file and log_file < 256 * 1024 ** 2:
        print(
            Fore.YELLOW
            + f"DB tuning warning: innodb_log_file_size is {log_mb:.0f} MB; "
            + "large recalc upserts usually behave better with >=256 MB."
        )


def recalculate(symbol, days=DEFAULT_LOOKBACK, components=None, use_batch=True,
                cutoff_end=None, force=False, inner_progress=True, regime_map=None,
                spy_wk_map=None, mis_stress_map=None, extra_score_engines=None):
    if components is None:
        components = {'overall'}

    stock = Stock.get_or_none(Stock.symbol == symbol)
    if not stock:
        print(Fore.RED + f"Stock {symbol} not found")
        return 0, 0, 0

    cutoff_start = date.today() - timedelta(days=days)

    weekly_u = weekly_e = 0
    if 'weekly' in components:
        weekly_u, weekly_e = stock.recalculate_weekly_scores(
            cutoff_start, show_progress=inner_progress, cutoff_end=cutoff_end)

    dte_u = dte_s = dte_e = 0
    if 'dte' in components:
        from recalculate_dte import recalculate_dte_for_stock
        dte_u, dte_s, dte_e = recalculate_dte_for_stock(
            symbol, days=days, cutoff_end=cutoff_end, force=force,
            show_progress=inner_progress,
        )

    daily = components & DAILY_COMPONENTS
    if not daily:
        return weekly_u + dte_u, dte_s, weekly_e + dte_e

    u, s, e = (
        stock.recalculate_scores_full(
            cutoff_start, daily, cutoff_end=cutoff_end, force=force,
            show_progress=inner_progress, regime_map=regime_map,
            spy_wk_map=spy_wk_map, mis_stress_map=mis_stress_map,
            extra_score_engines=extra_score_engines,
        )
        if use_batch
        else stock.recalculate_scores(
            cutoff_start, daily, show_progress=inner_progress)
    )
    return u + weekly_u + dte_u, s + dte_s, e + weekly_e + dte_e


def _worker_init(extra_score_versions=None):
    """Re-open the DB connection in each worker and preload all shared maps."""
    global _SHARED_REGIME_MAP, _SHARED_SPY_WK_MAP, _SHARED_MIS_STRESS_MAP, _SHARED_EXTRA_SCORE_ENGINES
    from database.trader_database import DB
    if DB.is_closed():
        DB.connect()
    _SHARED_REGIME_MAP = _load_regime_map()
    _SHARED_SPY_WK_MAP = _load_spy_wk_composite_map()
    _SHARED_MIS_STRESS_MAP = _load_mis_stress_map()
    _SHARED_EXTRA_SCORE_ENGINES, _ = _load_extra_score_engines(extra_score_versions)


def _worker(args):
    symbol, days, components, use_batch, cutoff_end, force = args
    try:
        u, s, e = recalculate(symbol, days, components, use_batch,
                               cutoff_end=cutoff_end, force=force,
                               inner_progress=False,
                               regime_map=_SHARED_REGIME_MAP,
                               spy_wk_map=_SHARED_SPY_WK_MAP,
                               mis_stress_map=_SHARED_MIS_STRESS_MAP,
                               extra_score_engines=_SHARED_EXTRA_SCORE_ENGINES)
        return symbol, u, s, e, None
    except Exception as exc:
        return symbol, 0, 0, 1, str(exc)


def _run_one_stage(stocks, load_cutoff, persist_lo, persist_hi_exclusive, components,
                   workers, use_batch, stage_desc, show_inner):
    label = '+'.join(sorted(components))
    mode_tag = 'batch' if use_batch else 'per-row'
    workers_tag = f'{workers}w' if workers > 1 else 'serial'
    hi_note = ''
    if persist_hi_exclusive is not None:
        hi_note = f", persist < {persist_hi_exclusive}"
    print(Fore.CYAN + f"  {stage_desc} [{label}] ({mode_tag}, {workers_tag}) "
          f"load≥{load_cutoff}{hi_note}")

def run(symbol=None, days=DEFAULT_LOOKBACK, components=None,
        workers=None, use_batch=True, cutoff_end=None, force=False,
        reuse_components_from=None, score_versions=None):
    """workers=None -> auto (cpu_count - 1). Pass 1 for serial."""
    global _SHARED_REGIME_MAP, _SHARED_SPY_WK_MAP, _SHARED_MIS_STRESS_MAP, _SHARED_EXTRA_SCORE_ENGINES
    if components is None:
        components = {'overall'}
    if workers is None:
        workers = _default_workers()

    if symbol:
        stocks = [Stock.get_or_none(Stock.symbol == symbol)]
        if not stocks[0]:
            print(Fore.RED + f"Stock {symbol} not found")
            return
    else:
        stocks = list(Stock.select().order_by(Stock.symbol))

    label      = '+'.join(sorted(components))
    mode_tag   = 'batch' if use_batch else 'per-row'
    workers_tag = f'{workers}w' if workers > 1 else 'serial'
    force_tag  = ' [force]' if force else ''
    print(Fore.CYAN + f"Recalculating [{label}] for {len(stocks)} stock(s) "
          f"({days}d lookback, {mode_tag}, {workers_tag}){force_tag} ...")

    if reuse_components_from:
        if not force:
            print(Fore.YELLOW + "--reuse-components-from implies force=True so copied overalls are recomputed")
            force = True
        copy_meta = copy_score_components_from_version(
            reuse_components_from,
            days=days,
            symbol=symbol,
            cutoff_end=cutoff_end,
            overwrite=True,
        )
        print(
            Fore.CYAN
            + "Component reuse: copied "
            + f"{copy_meta['source_rows']:,} source rows from {copy_meta['source']} "
            + f"to {copy_meta['target']} over {copy_meta['chunks']} chunk(s)"
        )
    if score_versions and not force:
        print(Fore.YELLOW + "--score-versions implies force=True so historical sidecar rows are written")
        force = True

    # Load shared maps once for the whole run (serial path reuses these directly;
    # worker processes re-load via _worker_init since they aren't picklable across processes).
    _SHARED_REGIME_MAP = _load_regime_map()
    _SHARED_SPY_WK_MAP = _load_spy_wk_composite_map()
    _SHARED_MIS_STRESS_MAP = _load_mis_stress_map()
    _SHARED_EXTRA_SCORE_ENGINES, skipped_versions = _load_extra_score_engines(score_versions)
    if _SHARED_EXTRA_SCORE_ENGINES:
        print(
            Fore.CYAN
            + "Historical sidecar score versions: "
            + ", ".join(engine.key for engine in _SHARED_EXTRA_SCORE_ENGINES)
        )
    for skipped in skipped_versions:
        print(Fore.YELLOW + f"Skipping {skipped['key']}: {skipped['reason']}")

    total_u, total_s, total_e = 0, 0, 0
    multi = len(stocks) > 1
    inner_progress = (not multi) and workers == 1

    if workers > 1:
        args_list = [(s.symbol, days, components, use_batch, cutoff_end, force)
                     for s in stocks]
        with multiprocessing.Pool(
            processes=workers,
            initializer=_worker_init,
            initargs=(_normalize_score_version_tokens(score_versions),),
        ) as pool:
            it = pool.imap_unordered(_worker, args_list)
            if multi:
                pbar = tqdm(
                    it, total=len(args_list), desc="Stocks", unit="stk",
                    mininterval=0.25, smoothing=0.05, dynamic_ncols=True,
                    ascii=True,
                )
            else:
                pbar = it
            for row in pbar:
                sym, u, s, e, err = row
                if multi:
                    pbar.set_postfix_str(sym, refresh=False)
                if err:
                    tqdm.write(Fore.RED + f"  {sym}: {err}")
                total_u += u
                total_s += s
                total_e += e
    else:
        if multi:
            pbar = tqdm(
                total=len(stocks), desc="Stocks", unit="stk",
                mininterval=0.25, smoothing=0.05, dynamic_ncols=True,
                ascii=True,
            )
            for stock in stocks:
                pbar.set_postfix_str(stock.symbol, refresh=False)
                u, s, e = recalculate(
                    stock.symbol, days, components, use_batch,
                    cutoff_end=cutoff_end, force=force,
                    inner_progress=inner_progress,
                    regime_map=_SHARED_REGIME_MAP,
                    spy_wk_map=_SHARED_SPY_WK_MAP,
                    mis_stress_map=_SHARED_MIS_STRESS_MAP,
                    extra_score_engines=_SHARED_EXTRA_SCORE_ENGINES,
                )
                total_u += u
                total_s += s
                total_e += e
                pbar.update(1)
            pbar.close()
        else:
            for stock in stocks:
                u, s, e = recalculate(
                    stock.symbol, days, components, use_batch,
                    cutoff_end=cutoff_end, force=force,
                    inner_progress=inner_progress,
                    regime_map=_SHARED_REGIME_MAP,
                    spy_wk_map=_SHARED_SPY_WK_MAP,
                    mis_stress_map=_SHARED_MIS_STRESS_MAP,
                    extra_score_engines=_SHARED_EXTRA_SCORE_ENGINES,
                )
                total_u += u
                total_s += s
                total_e += e

    print(Fore.GREEN + f"Done: {total_u} updated, {total_s} skipped, {total_e} errors")
