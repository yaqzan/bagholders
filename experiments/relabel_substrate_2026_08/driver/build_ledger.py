#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_ledger.py -- the honest_ledger builder.
(experiments/relabel_substrate_2026_08/PREREG.md, LOCKED commit 7fa41127. Do
not edit that file; construction rules below implement it, they don't amend
it.)

======================================================================
OBJECT
======================================================================
One row per (symbol, date) call signal, version 74, overall >= 70, date in
[2021-01-01 .. today], delisted INCLUDED. Written to
.cache/relabel_substrate/honest_ledger_v1.parquet.

======================================================================
COLUMN FAMILIES (PREREG section "Object")
======================================================================
IDENTITY+FEATURES: symbol, date, overall, tier components, regime, volume
  signal, opt_vol_30d_atm liquidity (NULL where FF-3' doesn't cover), ct_flag
  (tools.ct_predicate, the shared predicate -- see DEPENDENCY note below),
  delisted flag, PIT mcap ($B).
L1 LEGACY: barrier_outcomes cache read at (side='low', w_days=15,
  barrier_set='30dte_generic') -- "WR15" as assessed today, carried for
  continuity. Known caveat (documented, not fixed here): D1 double-touch
  optimism (experiments/double_touch_d1_2026_08/PREREG.md), unresolved
  upstream.
L2 HONEST-SIM: per-signal option P&L under CALIBRATED engine defaults --
  entry at signal close, monte_carlo.py's own premium model
  (compute_trade_outcome + resolve()), shipped Core barriers (TP 0.10 /
  SL -1.00 dead-hold, 30-DTE NOMINAL_CAL_DTE=30, HOLD_CAL_DAYS=27 calendar),
  GAP_AWARE fill semantics, never-fill resolved BOTH ways:
    l2_expected  = mean of N_DRAWS seeded resolve() draws, TP_FILL_MISS_P set
                   to the signal's OWN liquidity-tier never-fill rate (the
                   tp_fill_fidelity_30dte ARM-30 measured table), NOT the flat
                   0.15 module default.
    l2_sampled   = the FIRST of those N draws (one seeded draw, same RNG
                   stream that produces l2_expected -- see "RNG design" below).
L3 REAL: B:\\polygon_derived\\ledger_v2\\ledger.parquet -- realized option P&L
  from real Polygon OPRA prints, 2022-08+, ~4,403 kept rows. CAVEAT (load-
  bearing, repeat in FINDINGS): ledger_v2 was built under the OLD incumbent
  TP+30%/SL-70% barrier convention (target_dte_cal=30), NOT Core's current
  shipped TP10/SL100 -- it validates the ENGINE'S PREMIUM-MODEL FIDELITY
  against real prints, it is NOT "Core's real money outcome" under a
  different barrier config. Acceptance check (b) is a pure JOIN-RATE check,
  not a P&L-agreement check.

======================================================================
DEPENDENCY (Builder B, tools/ct_predicate.py)
======================================================================
ct_flag imports tools.ct_predicate.tag_values(overall, trend, side) -- the
single shared CT_PROMOTE-qualification predicate (verified byte-identical to
monte_carlo.ct_tag / backtest_cascade.ct_tag by that module's own
verify_against_production() selfcheck). Per the orchestrator's instruction:
build everything else first, ct_flag LAST; if the module is still missing
when reached, ship ct_flag=NULL and STOP without self-extracting the
predicate (single-source rule, drift prevention). As of this build the
module IS present (landed while this driver was in research) -- confirmed
via `python tools/ct_predicate.py selfcheck` (374 grid + 2000 live DB rows,
AGREE) before being wired in below.

======================================================================
WHY A DIRECT Score QUERY, NOT monte_carlo.load_signals()
======================================================================
load_signals() mutates .overall IN PLACE when a CTSL/mom-score override is
active, and unconditionally pipes every load through
_apply_universe_filter/_apply_ctsl_to_signals/_apply_weekly_overflow_filter/
_apply_liquidity_floor_filter/_apply_liquidity_random_drop_filter -- these
are PORTFOLIO-STAGE selection filters (does Core's OWN book trade this
signal), not population membership. The ledger's whole purpose (residual-
mining, ct-bounce-formula validation on the population Core does NOT
currently select) requires the population to be exactly what PREREG says:
"version 74, overall >= 70" off the raw `scores` table. Verified empirically
(sample_sources.py): a raw Score query at the exact monte_carlo.WINDOWS
boundaries reproduces PREREG's cited populations almost exactly --
22-now (2022-01-01..2026-04-24): 19,262 vs cited 19,261 (delta 1);
5y (2021-01-01..2026-04-15): 25,728 vs cited 25,703 (delta 25) --
both within score-history-mutability noise (traps.md "Score HISTORY is
mutable, and replace-semantics writers blind timestamp forensics").

======================================================================
WHY DTE_ROUTER / CTSL tier-funding / cascade sizing are NOT modeled in L2
======================================================================
PREREG's L2 spec is explicit and literal: "shipped Core barriers (TP 0.10 /
SL -1.00 dead-hold, 30-DTE, 27cd hold)" -- uniform 30-DTE for EVERY signal,
not "whichever DTE the live DTE_ROUTER would have picked" and not gated by
whether CTSL/tier-funding would have actually bought it. L2 is a per-signal
P&L instrument, not a portfolio replay -- portfolio selection/sizing
mechanics (DTE_ROUTER, CT_PROMOTE tier override, cascade capacity,
MAX_POSITIONS, GROSS_PREMIUM_CAP) are exactly the things downstream mining
needs to be able to re-examine INSIDE the ledger's full >=70 population, so
none of them may gate which rows the ledger carries.

======================================================================
WHY `stressed` IS ALWAYS PASSED AS False (documented simplification, proven
byte-identical for Core, not a general truth)
======================================================================
compute_trade_outcome(stressed=...) only changes which of
TP_SIGMA_BASE/TP_SIGMA_STRESS (and the SL analogues) get used. Core's live
OPT_30DTE dataclass instance has TP_BASE==TP_STRESS==0.10 and
SL_BASE==SL_STRESS==-1.00 literally (strategy_config.py, "BREADTH_THRESHOLD
... inert (base==stress both TP and SL)"), so TP_SIGMA_BASE==TP_SIGMA_STRESS
and SL_SIGMA_BASE==SL_SIGMA_STRESS by construction -- confirmed independently
by experiments/tp_fill_fidelity_30dte's own runtime_assert ("post-set_tpsl:
TP_SIGMA_BASE == TP_SIGMA_STRESS"). Passing stressed=False therefore produces
IDENTICAL sigma barriers to computing the real breadth-based stress flag; we
skip loading MarketBreadth (a real DB query) and just document why it's
provably inert for this shipped config -- this is NOT true for any other
profile/config, do not copy this shortcut elsewhere without re-checking.
Same reasoning covers TSL (TSL_ENABLED has no field on DteStrategyConfig ->
getattr default False -> the engine's own TSL_ENABLED global is False
regardless -- trail=False is passed for clarity, not because it changes
anything) and PREM_STOP_LOSS (module default '0.0', never negative for any
shipped config -- the 'prem' kind literally cannot fire).

======================================================================
RIPENESS GATE (traps.md "build_iv.py pnl15 silently truncates near the
build's end date" -- same failure family, fixed here at construction time
rather than discovered downstream)
======================================================================
compute_trade_outcome's CALENDAR_HOLD walk does NOT require a bar PAST the
HOLD_CAL_DAYS deadline to declare 'hard' -- it just uses however many bars
are available. Called on a signal from the last ~40 calendar days (as of
build time), this would silently label "today's price" as if it were "the
day-27 price" -- an unripe label wearing a resolved one's badge. Rule
applied below: a 'hard'-kind outcome is RIPE only if (a) the last available
price bar reaches signal_date + HOLD_CAL_DAYS, OR (b) the symbol is
delisted and its last available bar is at/after its delisted_date (a
genuinely terminal state, not a "haven't gotten there yet" state). Any
other 'hard' outcome, and any outcome compute_trade_outcome couldn't
resolve at all (returns None), is NULL'd in L2 with l2_ripe=False. tp/sl/
both/trail kinds are always ripe (resolved by an actual historical barrier
touch, independent of "today").

======================================================================
RNG DESIGN
======================================================================
One random.Random(stable_seed(symbol, date)) per signal (hashlib-based,
reproducible across runs/processes -- NEVER Python's built-in hash(), which
is salted per-process). N_DRAWS resolve() calls are drawn from that ONE
stream; draw #1 IS l2_sampled, mean(all N) IS l2_expected. resolve() is
read-only on `outcome` (verified against source -- every branch returns a
fresh (kind, pnl) tuple, nothing is written back), so calling it N times
against the same outcome dict is safe. mc.TP_FILL_MISS_P is mutated to the
signal's own tier rate immediately before its N draws (module-global
reassignment -- safe here because this driver runs single-process, single
monte_carlo import, no multiprocessing.Pool spawned by this file at any
point -- compute_trade_outcome/resolve/precompute_outcomes never create one;
only monte_carlo._simulate_window does, and this driver never calls it).

======================================================================
LIQUIDITY-TIER SOURCE (verified against experiments/tp_fill_fidelity_30dte/
driver/run.py -- the study that measured the tier-rate table used below)
======================================================================
B:\\polygon_derived\\liquidity_map\\signal_liquidity.parquet's opt_vol_30d_atm
column, tier edges (320.0, 1191.0, 3486.0, 14524.0) -> t1..t5 (t1=least
liquid), IDENTICAL to monte_carlo._LIQ_TIER_EDGES/_liq_tier (same FF-2
quintile edges, confirmed in tp_fill_fidelity's own bindings_echo.json:
tier_edges_source=B:\\polygon_derived\\minute_fidelity\\bindings.json). This
file has only 4,936 rows (it is ledger_v2's own companion liquidity sidecar,
NOT a full-population liquidity map) -- coverage against the ledger's full
population is PARTIAL even within 2022-08+, not just "NULL before 2022-08"
as PREREG's one-line gloss suggests. l2_rate_source records the true reason
per row: 'tier_lookup' (found, tier rate used) vs 'flat_fallback' (no match
-- either pre-2022-08 or a coverage gap inside the covered era; the built
ledger's own summary counts break this down honestly, see FINDINGS).

======================================================================
CHUNKING / RESUMABILITY (PREREG "resumable (state.json cursor by
date-chunk), atomic appends")
======================================================================
Phase A (population + features + L1 + L3 + CT + one-shot global maps:
mcap_b_latest, anchor_close, liquidity sidecar, ledger_v2) is a single cheap
pass (bulk reads/joins, no MC engine), tracked by ONE state flag -- cheap
enough to just rerun if interrupted, no chunk-level resume needed. Phase B
(the L2 walk: price-history load + earnings map + compute_trade_outcome +
resolve() sampling -- CPU-bound, the dominant cost) is chunked by CALENDAR
QUARTER, each chunk's L2 rows written to its own parquet under
driver/state/l2_chunks/, and state.json's done_chunks list gates re-entry
after a crash/timeout/restart. A final merge step joins Phase A's base
frame with the concatenated L2 chunks on (symbol, date) and writes the
final honest_ledger_v1.parquet.

Usage
-----
    python experiments/relabel_substrate_2026_08/driver/build_ledger.py --selftest

    python experiments/relabel_substrate_2026_08/driver/build_ledger.py \\
        --stage smoke [--n-draws 200] [--smoke-start 2024-01-01] [--smoke-end 2024-01-31]

    python experiments/relabel_substrate_2026_08/driver/build_ledger.py \\
        --stage build [--start 2021-01-01] [--end today] [--n-draws 200] [--resume]

    python experiments/relabel_substrate_2026_08/driver/build_ledger.py \\
        --stage acceptance [--ledger PATH]

Console output is ASCII-only (repo convention -- traps.md ".ps1 files stay
ASCII-only" / queue stdout buffering trap; no em-dash/smart-quote/unicode).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

# --- repo-root bootstrap -- explicit + asserted, never inferred from CWD
# (traps.md "Worktree PYTHONPATH trap"). Idempotent, safe to re-run. --------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))          # .../relabel_substrate_2026_08/driver
_EXP_DIR = os.path.dirname(_THIS_DIR)                            # .../relabel_substrate_2026_08
_EXPERIMENTS_DIR = os.path.dirname(_EXP_DIR)                     # .../experiments
_REPO_ROOT = os.path.dirname(_EXPERIMENTS_DIR)                   # repo root

for _d in (_THIS_DIR, _REPO_ROOT):
    if _d not in sys.path:
        sys.path.insert(0, _d)

assert os.path.isfile(os.path.join(_REPO_ROOT, 'monte_carlo.py')), (
    f"repo root resolution failed: {_REPO_ROOT!r} (from __file__={__file__!r})"
)

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
STATE_DIR = os.path.join(_THIS_DIR, 'state')
L2_CHUNK_DIR = os.path.join(STATE_DIR, 'l2_chunks')
STATE_JSON = os.path.join(STATE_DIR, 'state.json')
BASE_PARQUET = os.path.join(STATE_DIR, 'ledger_base.parquet')
LEDGER_DIR = os.path.join(_REPO_ROOT, '.cache', 'relabel_substrate')
LEDGER_PATH = os.path.join(LEDGER_DIR, 'honest_ledger_v1.parquet')
SMOKE_PATH = os.path.join(OUT_DIR, 'honest_ledger_SMOKE.parquet')

for _d in (OUT_DIR, LOG_DIR, STATE_DIR, L2_CHUNK_DIR, LEDGER_DIR):
    os.makedirs(_d, exist_ok=True)

# ---------------------------------------------------------------------------
# LOCKED constants (PREREG)
# ---------------------------------------------------------------------------
PREREG_START = date(2021, 1, 1)
VERSION_ID = 74
BARRIER_SET_L1 = '30dte_generic'
W_DAYS_L1 = 15
SIDE_L1 = 'low'          # overall>=70 (call/HIGH bucket) -> barrier_cache 'low' side (win-on-rise)
HOLD_CAL_DAYS = 27        # Core shipped; re-verified live against mc.HOLD_CAL_DAYS at runtime, not trusted blind
LEDGER_V2_PATH = r'B:\polygon_derived\ledger_v2\ledger.parquet'
LIQUIDITY_PARQUET = r'B:\polygon_derived\liquidity_map\signal_liquidity.parquet'

# FF-2 quintile edges -- IDENTICAL to monte_carlo._LIQ_TIER_EDGES; verified
# against experiments/tp_fill_fidelity_30dte/driver/run.py's own tier_edges
# (bindings_echo.json: source=B:\polygon_derived\minute_fidelity\bindings.json).
LIQ_TIER_EDGES = (320.0, 1191.0, 3486.0, 14524.0)
LIQ_TIER_LABELS = ('t1', 't2', 't3', 't4', 't5')

# ARM-30 pooled matched-filter never-fill rate by tier -- the "fidelity
# study's measured table" PREREG cites (t1 20.4% .. t4 7.8%).
# Source: experiments/tp_fill_fidelity_30dte/out/knob_calibration_draft.md
# "Per-tier (matched-filter)" block, ARM-30 (incumbent TP+30/SL-70, the arm
# that measured the same barrier-crossing mechanics Core's own TP/SL walk
# uses -- ARM-15 exists too but PREREG cites ARM-30's numbers specifically).
TIER_NEVER_FILL_RATE = {
    't1': 0.2041,
    't2': 0.1498,
    't3': 0.1367,
    't4': 0.0778,
    't5': 0.1064,
}
FLAT_FALLBACK_RATE = 0.15   # PREREG "flat 0.15" -- also monte_carlo.py's own default TP_FILL_MISS_P

N_DRAWS_DEFAULT = 200

CT_RECONCILE_START = date(2022, 1, 1)          # exact V6 in-sample window (ctsl_vehicle campaign)
CT_RECONCILE_END = date(2026, 6, 15)           # == strategy_config.CALIBRATION_CUTOFF_DATE
CT_RECONCILE_EXPECT = 133
CT_RECONCILE_TOLERANCE = 0.10


def _tee(msg, log_path):
    print(msg, flush=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def _load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return default


def _save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def stable_seed(symbol, sig_date, salt=''):
    """Deterministic, process/run-independent seed. NEVER Python's built-in
    hash() -- it is salted per-process (PYTHONHASHSEED) and would make the
    ledger non-reproducible across runs."""
    h = hashlib.sha256(f'{symbol}|{sig_date}|{salt}'.encode('utf-8')).hexdigest()
    return int(h[:15], 16)   # fits in a 63-bit int, plenty of entropy for random.Random


def liq_tier_for_value(v):
    """v -> 't1'..'t5' or None. Matches monte_carlo._liq_tier / the
    tp_fill_fidelity_30dte study's liquidity_tier_for() exactly (v<=edge
    boundary convention, verified against that module's own selftest
    assertions: 320->t1, 320.0001->t2, 14524->t4, 14525->t5)."""
    if v is None:
        return None
    edges = LIQ_TIER_EDGES
    if v <= edges[0]:
        return 't1'
    if v <= edges[1]:
        return 't2'
    if v <= edges[2]:
        return 't3'
    if v <= edges[3]:
        return 't4'
    return 't5'


def quarter_chunks(d_start, d_end):
    """List of (label, c_start, c_end) calendar-quarter chunks covering
    [d_start, d_end] inclusive. Last chunk is clipped to d_end."""
    chunks = []
    y, q = d_start.year, (d_start.month - 1) // 3 + 1
    while True:
        q_start_month = (q - 1) * 3 + 1
        c_start = date(y, q_start_month, 1)
        if c_start < d_start:
            c_start = d_start
        if q == 4:
            c_end = date(y, 12, 31)
        else:
            c_end = date(y, q_start_month + 3, 1) - timedelta(days=1)
        if c_end > d_end:
            c_end = d_end
        label = f'{y}Q{q}'
        chunks.append((label, c_start, c_end))
        if c_end >= d_end:
            break
        q += 1
        if q > 4:
            q = 1
            y += 1
    return chunks


# ---------------------------------------------------------------------------
# Phase A -- population, identity/features, L1, L3, CT flag, one-shot maps.
# Cheap: bulk reads + joins, no MC engine import, no per-signal walk.
# ---------------------------------------------------------------------------
def load_population(d_start, d_end, version=VERSION_ID, log_path=None):
    from database.models.core import Score
    rows = list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                     Score.macd, Score.rsi, Score.bb, Score.stoch,
                     Score.technical_alignment, Score.regime_composite,
                     Score.regime_multiplier, Score.volume_signal,
                     Score.volume_magnitude, Score.weight_info)
        .where(Score.version == version, Score.overall >= 70,
               Score.date >= d_start, Score.date <= d_end)
        .order_by(Score.date, Score.symbol)
        .tuples()
    )
    if log_path:
        _tee(f"[POP] version={version} overall>=70 [{d_start}..{d_end}]: {len(rows):,} rows", log_path)
    return rows


_WEEKLY_KEYS = ('w_comp', 'w_bias', 'w_mom', 'w_adj', 'wadj_completed',
                 'wadj_partial', 'pre_regime', 'pre_boost', 'td',
                 'weekly_transition_t', 'weekly_adj_gap')


def parse_weekly_fields(weight_info_raw):
    """Flatten the commonly-mined weekly sub-fields out of weight_info JSON
    (kept as its own raw column too -- future-proof, avoids re-deriving from
    Score if a field this driver didn't anticipate is needed later)."""
    out = {k: None for k in _WEEKLY_KEYS}
    if not weight_info_raw:
        return out
    try:
        wi = json.loads(weight_info_raw) if isinstance(weight_info_raw, str) else weight_info_raw
        for k in _WEEKLY_KEYS:
            if k in wi:
                out[k] = wi[k]
    except Exception:
        pass
    return out


def build_stock_maps(log_path=None):
    """{symbol: market_cap_dollars_or_None}, {symbol: delisted_date_or_None}.
    One bulk read of the whole (small, ~1600-row) `stocks` table."""
    from database.models.core import Stock
    mcap_map = {}
    delisted_map = {}
    for sym, mcap, ddate in Stock.select(Stock.symbol, Stock.market_cap, Stock.delisted_date).tuples():
        mcap_map[sym] = float(mcap) if mcap is not None else None
        delisted_map[sym] = ddate
    if log_path:
        n_mcap = sum(1 for v in mcap_map.values() if v is not None)
        n_del = sum(1 for v in delisted_map.values() if v is not None)
        _tee(f"[STOCK-MAPS] {len(mcap_map)} symbols; market_cap coverage {n_mcap} "
             f"({n_mcap/max(1,len(mcap_map))*100:.1f}%); delisted {n_del}", log_path)
    return mcap_map, delisted_map


def build_anchor_close_map(log_path=None):
    """{symbol: latest available close in price_history (as of build time)}.
    ONE bulk aggregate query over the whole table -- avoids a per-symbol
    IN-list / N+1 pattern. This is `anchor_close` in the F4 PIT-mcap formula
    (database/utils/scoring.py compute_pit_mcap_b / build_pit_mcap_map) --
    "the price state matching the [yfinance] market_cap snapshot"."""
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)
    t0 = time.perf_counter()
    cur = DB.execute_sql("""
        SELECT ph.symbol, ph.close
        FROM price_history ph
        INNER JOIN (
            SELECT symbol, MAX(date) AS maxd FROM price_history WHERE close IS NOT NULL GROUP BY symbol
        ) mx ON ph.symbol = mx.symbol AND ph.date = mx.maxd
    """)
    out = {sym: float(close) for sym, close in cur.fetchall() if close is not None}
    if log_path:
        _tee(f"[ANCHOR-CLOSE] {len(out)} symbols in {time.perf_counter()-t0:.1f}s", log_path)
    return out


def load_liquidity_sidecar(log_path=None):
    """{(symbol, date): {opt_vol_30d_atm, opt_vol_30d_allcall, opt_vol_30d_total,
    n_window_sessions, partial_window}} from the FF-3' companion parquet.
    Small (~4,936 rows) -- held fully in memory."""
    import polars as pl
    if not os.path.isfile(LIQUIDITY_PARQUET):
        if log_path:
            _tee(f"[LIQUIDITY] MISSING: {LIQUIDITY_PARQUET} -- opt_vol_30d_atm and tier rates "
                 f"will be NULL/flat-fallback for every row", log_path)
        return {}
    df = pl.read_parquet(LIQUIDITY_PARQUET)
    out = {}
    for row in df.iter_rows(named=True):
        out[(row['symbol'], row['date'])] = row
    if log_path:
        _tee(f"[LIQUIDITY] {len(out)} (symbol,date) rows from {LIQUIDITY_PARQUET} "
             f"({df['symbol'].n_unique()} symbols, {df['date'].min()}..{df['date'].max()})", log_path)
    return out


def load_ledger_v2_map(log_path=None):
    """{(symbol, entry_date): row_dict} from ledger_v2 -- the L3 REAL source."""
    import polars as pl
    if not os.path.isfile(LEDGER_V2_PATH):
        if log_path:
            _tee(f"[L3] MISSING: {LEDGER_V2_PATH} -- every l3_* column will be NULL", log_path)
        return {}
    df = pl.read_parquet(LEDGER_V2_PATH)
    out = {}
    for row in df.iter_rows(named=True):
        out[(row['symbol'], row['entry_date'])] = row
    if log_path:
        n_kept = sum(1 for r in out.values() if r.get('status') == 'kept')
        _tee(f"[L3] {len(out)} ledger_v2 rows loaded ({n_kept} status=kept) from {LEDGER_V2_PATH}", log_path)
    return out


def build_l1_map(pop_keys, log_path=None):
    """{(symbol, date): row_dict} from barrier_outcomes, pinned to
    (side='low', w_days=15, barrier_set='30dte_generic') -- the ctsl_vehicle
    trap #2 discipline (never key on (symbol,date) alone; assert no dupes).
    Zero-copy Arrow/polars registration + JOIN against the DuckDB mirror,
    same pattern as barrier_cache._peaks_to_swing_results_duck."""
    import polars as pl
    from database.barrier_cache import _get_duck_con, _select_backend

    backend = _select_backend()
    if backend != 'duck':
        raise SystemExit(f"[STOP] barrier_outcomes DuckDB mirror not selected (backend={backend}) "
                          f"-- run `python -m database.barrier_cache rebuild-duck` first")

    keys_df = pl.DataFrame({
        'symbol': [k[0] for k in pop_keys],
        'date': [k[1] for k in pop_keys],
    })
    con = _get_duck_con()
    con.register('_pop_keys', keys_df)
    try:
        joined = con.execute(f"""
            SELECT b.symbol, b.date, b.result, b.exit_offset, b.exit_close, b.exit_return,
                   b.mae_pct, b.mfe_pct, b.result_u, b.entry_close, b.sigma_pct,
                   b.exit_bars, b.fire_type
            FROM _pop_keys p
            JOIN barrier_outcomes b
              ON b.symbol = p.symbol AND b.date = CAST(p.date AS DATE)
            WHERE b.side = '{SIDE_L1}' AND b.w_days = {W_DAYS_L1} AND b.barrier_set = '{BARRIER_SET_L1}'
        """).fetchall()
    finally:
        con.unregister('_pop_keys')

    out = {}
    dupe_count = 0
    for (sym, d, result, eo, ec, er, mae, mfe, ru, entry, sigma, eb, ft) in joined:
        d_key = d if not hasattr(d, 'date') else d  # already a date from DuckDB CAST
        key = (sym, d_key)
        if key in out:
            dupe_count += 1
            continue
        kind = None
        if result is not None:
            if result == 1:
                kind = 'win'
            elif ft is not None:
                kind = 'stop' if ft == 2 else 'expire'
            else:
                kind = 'stop_or_expire_unknown'   # pre-fire_type legacy row fallback
        out[key] = {
            'l1_result': result, 'l1_kind': kind, 'l1_exit_offset': eo,
            'l1_exit_close': ec, 'l1_exit_return': er, 'l1_mae_pct': mae,
            'l1_mfe_pct': mfe, 'l1_result_u': ru, 'l1_entry_close': entry,
            'l1_sigma_pct': sigma, 'l1_exit_bars': eb, 'l1_fire_type': ft,
        }
    if log_path:
        _tee(f"[L1] {len(out)}/{len(pop_keys)} population rows joined against barrier_outcomes "
             f"(side={SIDE_L1}, w_days={W_DAYS_L1}, barrier_set={BARRIER_SET_L1}); "
             f"duplicate-key collisions dropped: {dupe_count} (expect 0)", log_path)
    return out


def ct_flag_for(overall, trend):
    """Returns (ct_flag: bool, ct_tag: str_or_None). NULL/None ct_flag if
    tools/ct_predicate.py is unavailable -- see module docstring DEPENDENCY
    note. Imports lazily so the rest of the driver still works if the
    dependency is missing (everything-but-ct_flag build)."""
    try:
        from tools.ct_predicate import tag_values
    except ImportError:
        return None, None
    tag = tag_values(overall, trend, 'call')
    return (tag is not None), tag


def build_base_rows(d_start, d_end, log_path):
    """Phase A: assembles every column EXCEPT L2 (identity/features, L1, L3,
    ct_flag). Returns a list of row dicts, one per (symbol, date)."""
    _tee(f"\n{'='*100}\nPHASE A -- base build [{d_start}..{d_end}]\n{'='*100}", log_path)

    pop = load_population(d_start, d_end, log_path=log_path)
    mcap_map, delisted_map = build_stock_maps(log_path=log_path)
    anchor_close_map = build_anchor_close_map(log_path=log_path)
    liq_sidecar = load_liquidity_sidecar(log_path=log_path)
    ledger_v2_map = load_ledger_v2_map(log_path=log_path)

    pop_keys = [(sym, d) for (sym, d, *_rest) in pop]
    l1_map = build_l1_map(pop_keys, log_path=log_path)

    ct_available = True
    try:
        from tools.ct_predicate import tag_values as _probe
    except ImportError:
        ct_available = False
    _tee(f"[CT-FLAG] tools/ct_predicate.py available: {ct_available}", log_path)

    rows = []
    n_ct = 0
    n_l3 = 0
    for (sym, d, overall, trend, macd, rsi, bb, stoch, ta, regime_comp,
         regime_mult, vsig, vmag, weight_info) in pop:
        key = (sym, d)
        weekly = parse_weekly_fields(weight_info)

        mcap_latest = mcap_map.get(sym)
        anchor_close = anchor_close_map.get(sym)
        mcap_b_latest = (mcap_latest / 1e9) if mcap_latest is not None else None

        liq = liq_sidecar.get(key)
        opt_vol_30d_atm = liq['opt_vol_30d_atm'] if liq else None
        liquidity_tier = liq_tier_for_value(opt_vol_30d_atm) if liq else None

        ct_flag, ct_tag = ct_flag_for(overall, trend)
        if ct_flag:
            n_ct += 1

        l1 = l1_map.get(key, {})

        l3 = ledger_v2_map.get(key)
        l3_out = {}
        if l3 is not None:
            n_l3 += 1
            entry_prem = l3.get('entry_premium')
            mark27 = l3.get('mark_cd27')
            l3_out = {
                'l3_status': l3.get('status'),
                'l3_signal_id': l3.get('signal_id'),
                'l3_ticker': l3.get('ticker'),
                'l3_strike': l3.get('strike'),
                'l3_expiry': l3.get('expiry'),
                'l3_dte_actual': l3.get('dte_actual'),
                'l3_entry_premium': entry_prem,
                'l3_entry_spot_unadj': l3.get('entry_spot_unadj'),
                'l3_tp_touch_date': l3.get('tp_touch_date'),
                'l3_sl_touch_date': l3.get('sl_touch_date'),
                'l3_max_path_premium': l3.get('max_path_premium'),
                'l3_min_path_premium': l3.get('min_path_premium'),
                'l3_mark_cd13': l3.get('mark_cd13'),
                'l3_mark_cd15': l3.get('mark_cd15'),
                'l3_mark_cd27': mark27,
                'l3_entry_volume': l3.get('entry_volume'),
                'l3_liquid_entry': l3.get('liquid_entry'),
                'l3_path_end_reason': l3.get('path_end_reason'),
                'l3_realized_pnl_cd27': (mark27 / entry_prem - 1.0)
                    if (mark27 is not None and entry_prem not in (None, 0)) else None,
            }
        else:
            l3_out = {k: None for k in (
                'l3_status', 'l3_signal_id', 'l3_ticker', 'l3_strike', 'l3_expiry',
                'l3_dte_actual', 'l3_entry_premium', 'l3_entry_spot_unadj',
                'l3_tp_touch_date', 'l3_sl_touch_date', 'l3_max_path_premium',
                'l3_min_path_premium', 'l3_mark_cd13', 'l3_mark_cd15', 'l3_mark_cd27',
                'l3_entry_volume', 'l3_liquid_entry', 'l3_path_end_reason',
                'l3_realized_pnl_cd27')}

        row = {
            'symbol': sym, 'date': d, 'overall': int(overall), 'trend': trend,
            'macd': macd, 'rsi': rsi, 'bb': bb, 'stoch': stoch, 'technical_alignment': ta,
            'regime_composite': float(regime_comp) if regime_comp is not None else None,
            'regime_multiplier': float(regime_mult) if regime_mult is not None else None,
            'volume_signal': vsig,
            'volume_magnitude': float(vmag) if vmag is not None else None,
            'weight_info_raw': weight_info,
            **weekly,
            'opt_vol_30d_atm': opt_vol_30d_atm,
            'opt_vol_30d_allcall': liq['opt_vol_30d_allcall'] if liq else None,
            'opt_vol_30d_total': liq['opt_vol_30d_total'] if liq else None,
            'liquidity_tier': liquidity_tier,
            'liquidity_n_window_sessions': liq['n_window_sessions'] if liq else None,
            'liquidity_partial_window': liq['partial_window'] if liq else None,
            'ct_flag': ct_flag, 'ct_tag': ct_tag,
            'delisted': delisted_map.get(sym) is not None,
            'delisted_date': delisted_map.get(sym),
            'mcap_b_latest': mcap_b_latest,
            'anchor_close': anchor_close,   # carried through so Phase B can compute PIT mcap
            **l1,
            **l3_out,
            'version': VERSION_ID,
        }
        rows.append(row)

    _tee(f"[PHASE-A-DONE] {len(rows):,} rows; ct_flag=True: {n_ct:,}; L3-matched: {n_l3:,}", log_path)
    return rows


# ---------------------------------------------------------------------------
# Phase B -- L2 walk, chunked by calendar quarter.
# ---------------------------------------------------------------------------
def _import_mc(log_path):
    """env-before-import discipline (traps.md ctsl_vehicle trap #1 family):
    Core needs ZERO overrides (flipR_run.py PROFILE_ENV['core'] == {}), but
    we still pin the calibrated lens explicitly for self-documentation/
    reproducibility, matching every other driver in this program, BEFORE
    `import monte_carlo`."""
    os.environ.setdefault('TP_FILL_MISS_P', '0.15')
    os.environ.setdefault('TP_FILL_GAP_AWARE', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    import monte_carlo as mc
    # Hard verification -- fail fast rather than silently building L2 against
    # the wrong config (same discipline as flipR_run.py's run_one_cell).
    assert mc.CALENDAR_HOLD is True, f"mc.CALENDAR_HOLD={mc.CALENDAR_HOLD}, expected True"
    assert mc.HOLD_CAL_DAYS == HOLD_CAL_DAYS, f"mc.HOLD_CAL_DAYS={mc.HOLD_CAL_DAYS}, expected {HOLD_CAL_DAYS}"
    assert mc.NOMINAL_CAL_DTE == 30, f"mc.NOMINAL_CAL_DTE={mc.NOMINAL_CAL_DTE}, expected 30"
    assert abs(mc.TP_BASE - 0.10) < 1e-9, f"mc.TP_BASE={mc.TP_BASE}, expected 0.10"
    assert abs(mc.SL_BASE - (-1.00)) < 1e-9, f"mc.SL_BASE={mc.SL_BASE}, expected -1.00"
    assert abs(mc.TP_BASE - mc.TP_STRESS) < 1e-9, "TP_BASE != TP_STRESS -- the stressed=False shortcut is invalid, STOP"
    assert abs(mc.SL_BASE - mc.SL_STRESS) < 1e-9, "SL_BASE != SL_STRESS -- the stressed=False shortcut is invalid, STOP"
    assert not mc.TSL_ENABLED, f"mc.TSL_ENABLED={mc.TSL_ENABLED}, expected False"
    assert mc.PREM_STOP_LOSS >= 0.0, f"mc.PREM_STOP_LOSS={mc.PREM_STOP_LOSS}, expected >=0 (prem kind must be dead)"
    assert mc.DEAD_HOLD_ENABLED, f"mc.DEAD_HOLD_ENABLED={mc.DEAD_HOLD_ENABLED}, expected True (Core shipped)"
    _tee(f"[MC-VERIFY] CALENDAR_HOLD={mc.CALENDAR_HOLD} HOLD_CAL_DAYS={mc.HOLD_CAL_DAYS} "
         f"NOMINAL_CAL_DTE={mc.NOMINAL_CAL_DTE} TP_BASE={mc.TP_BASE} SL_BASE={mc.SL_BASE} "
         f"TP_FILL_MISS_P(default)={mc.TP_FILL_MISS_P} TP_FILL_GAP_AWARE={mc.TP_FILL_GAP_AWARE} "
         f"DELTA={mc.DELTA} PREMIUM_MULT={mc.PREMIUM_MULT}", log_path)
    return mc


def build_ern_map(mc, symbols, d_start, d_end, trading_days):
    from database.models.core import EarningsDate
    from iv_crush_model import compute_effective_date
    if not symbols:
        return {}
    rows = list(
        EarningsDate.select(EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
        .where(EarningsDate.symbol.in_(list(symbols)),
               EarningsDate.date >= d_start - timedelta(days=10),
               EarningsDate.date <= d_end + timedelta(days=mc.HOLD_DAYS * 2 + 14))
        .order_by(EarningsDate.symbol, EarningsDate.date)
        .tuples()
    )
    ern_map = defaultdict(list)
    for sym, d, ct in rows:
        eff = compute_effective_date(d, ct, trading_days)
        ern_map[sym].append(eff)
    for sym in list(ern_map.keys()):
        ern_map[sym] = sorted(set(ern_map[sym]))
    return ern_map


class _SigStub:
    """Minimal stand-in for a Score row, exposing exactly the attributes
    compute_trade_outcome/precompute_outcomes read (.symbol_id, .date,
    .overall). NOT a peewee model instance -- no lazy-FK risk (traps.md
    peewee FK-per-row trap does not apply, this is a plain synthetic object)."""
    __slots__ = ('symbol_id', 'date', 'overall')

    def __init__(self, symbol_id, d, overall):
        self.symbol_id = symbol_id
        self.date = d
        self.overall = overall


def process_chunk(mc, chunk_label, c_start, c_end, chunk_pop_rows, base_by_key,
                   n_draws, log_path):
    """Runs the L2 walk for one quarter's signals. `chunk_pop_rows` is the
    subset of Phase-A `rows` (dicts) whose `date` falls in [c_start, c_end].
    Returns a list of dicts, one per (symbol,date), with l2_*/pit-mcap
    columns -- NOT written to disk here (caller handles the parquet write so
    an interrupted chunk never leaves a half-written file)."""
    t0 = time.perf_counter()
    _tee(f"\n{'-'*100}\nCHUNK {chunk_label} [{c_start}..{c_end}] -- {len(chunk_pop_rows)} signals", log_path)
    if not chunk_pop_rows:
        return []

    sym_ids = sorted({r['symbol'] for r in chunk_pop_rows})
    ph = mc.load_price_history(sym_ids, c_start, c_end)
    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if c_start <= b[0] <= c_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    ern_map = build_ern_map(mc, sym_ids, c_start, c_end, trading_days)

    sig_objs = [_SigStub(r['symbol'], r['date'], r['overall']) for r in chunk_pop_rows]
    outcomes = mc.precompute_outcomes(sig_objs, ph, breadth_dates=[], breadth_map={},
                                       ern_map=ern_map, trading_days=trading_days, count_map=None)
    _tee(f"  compute_trade_outcome resolved {len(outcomes)}/{len(sig_objs)} signals "
         f"(unresolved -> l2_ripe=False, no forward price data at all)", log_path)

    out_rows = []
    n_ripe = 0
    n_tier_lookup = 0
    n_flat_fallback = 0
    for r in chunk_pop_rows:
        key = (r['symbol'], r['date'])
        outcome = outcomes.get(key)

        close_by_date = None  # lazily built per-symbol below only when needed

        pit_mcap = None
        mcap_b_latest = r['mcap_b_latest']
        anchor_close = r['anchor_close']
        if mcap_b_latest is not None:
            bars = ph.get(r['symbol'])
            close_t = None
            if bars:
                if close_by_date is None:
                    close_by_date = {b[0]: b[1] for b in bars}
                close_t = close_by_date.get(r['date'])
            if close_t is not None and anchor_close:
                pit_mcap = mcap_b_latest * close_t / anchor_close
            else:
                pit_mcap = mcap_b_latest  # fallback: static (F4 formula's own degenerate case)

        row_out = {'symbol': r['symbol'], 'date': r['date'], 'mcap_b_pit': pit_mcap}

        if outcome is None:
            row_out.update({
                'l2_ripe': False, 'l2_kind': None, 'l2_exit_bar': None, 'l2_cal_held': None,
                'l2_premium_pct': None, 'l2_entry': None, 'l2_spans_earnings': None,
                'l2_liquidity_tier_used': None, 'l2_miss_p_used': None, 'l2_rate_source': None,
                'l2_expected': None, 'l2_expected_n_draws': 0, 'l2_sampled': None,
                'l2_unripe_reason': 'compute_trade_outcome_returned_none',
            })
            out_rows.append(row_out)
            continue

        kind = outcome['kind']
        ripe = True
        unripe_reason = None
        if kind == 'hard':
            last_bar_date = ph[r['symbol']][-1][0] if ph.get(r['symbol']) else None
            deadline = r['date'] + timedelta(days=HOLD_CAL_DAYS)
            d_date = r['delisted_date']
            if last_bar_date is not None and last_bar_date >= deadline:
                ripe = True
            elif d_date is not None and last_bar_date is not None and last_bar_date >= d_date - timedelta(days=3):
                ripe = True   # genuinely terminal: symbol delisted, no more bars will ever exist
            else:
                ripe = False
                unripe_reason = 'hard_exit_before_build_time_deadline'

        if not ripe:
            row_out.update({
                'l2_ripe': False, 'l2_kind': kind, 'l2_exit_bar': outcome.get('exit_bar'),
                'l2_cal_held': outcome.get('cal_held'), 'l2_premium_pct': outcome.get('premium_pct'),
                'l2_entry': outcome.get('entry'), 'l2_spans_earnings': outcome.get('spans_earnings'),
                'l2_liquidity_tier_used': None, 'l2_miss_p_used': None, 'l2_rate_source': None,
                'l2_expected': None, 'l2_expected_n_draws': 0, 'l2_sampled': None,
                'l2_unripe_reason': unripe_reason,
            })
            out_rows.append(row_out)
            continue

        n_ripe += 1
        tier = r['liquidity_tier']
        if tier is not None and tier in TIER_NEVER_FILL_RATE:
            miss_p = TIER_NEVER_FILL_RATE[tier]
            rate_source = 'tier_lookup'
            n_tier_lookup += 1
        else:
            miss_p = FLAT_FALLBACK_RATE
            rate_source = 'flat_fallback'
            n_flat_fallback += 1

        mc.TP_FILL_MISS_P = miss_p
        rng = random.Random(stable_seed(r['symbol'], r['date']))
        draws = [mc.resolve(outcome, rng)[1] for _ in range(n_draws)]
        l2_sampled = draws[0]
        l2_expected = sum(draws) / len(draws)

        row_out.update({
            'l2_ripe': True, 'l2_kind': kind, 'l2_exit_bar': outcome.get('exit_bar'),
            'l2_cal_held': outcome.get('cal_held'), 'l2_premium_pct': outcome.get('premium_pct'),
            'l2_entry': outcome.get('entry'), 'l2_spans_earnings': outcome.get('spans_earnings'),
            'l2_liquidity_tier_used': tier, 'l2_miss_p_used': miss_p, 'l2_rate_source': rate_source,
            'l2_expected': l2_expected, 'l2_expected_n_draws': n_draws, 'l2_sampled': l2_sampled,
            'l2_unripe_reason': None,
        })
        out_rows.append(row_out)

    elapsed = time.perf_counter() - t0
    _tee(f"  CHUNK {chunk_label} DONE: {len(out_rows)} rows, ripe={n_ripe} "
         f"(tier_lookup={n_tier_lookup}, flat_fallback={n_flat_fallback}), "
         f"unripe={len(out_rows)-n_ripe}, elapsed={elapsed:.1f}s", log_path)
    return out_rows


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_build(d_start, d_end, n_draws, resume, out_path, log_path, state_path=STATE_JSON,
              base_parquet=BASE_PARQUET, l2_chunk_dir=L2_CHUNK_DIR):
    import polars as pl

    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    os.makedirs(os.path.dirname(base_parquet), exist_ok=True)
    os.makedirs(l2_chunk_dir, exist_ok=True)

    state = _load_json(state_path, {'done_chunks': [], 'base_done': False,
                                     'base_range': None})
    range_key = f'{d_start}_{d_end}'
    if state.get('base_range') != range_key:
        if resume and state.get('base_done'):
            _tee(f"[WARN] --resume requested but the base range changed "
                 f"({state.get('base_range')} -> {range_key}) -- rebuilding Phase A fresh", log_path)
        state = {'done_chunks': [], 'base_done': False, 'base_range': range_key}

    # ---- Phase A ----
    if state['base_done'] and resume and os.path.isfile(base_parquet):
        _tee(f"[RESUME] Phase A already done for {range_key} -- loading {base_parquet}", log_path)
        base_df = pl.read_parquet(base_parquet)
        base_rows = base_df.to_dicts()
    else:
        base_rows = build_base_rows(d_start, d_end, log_path)
        base_df = pl.DataFrame(base_rows, infer_schema_length=None)
        base_df.write_parquet(base_parquet)
        state['base_done'] = True
        _save_json(state_path, state)

    base_by_key = {(r['symbol'], r['date']): r for r in base_rows}

    # ---- Phase B (chunked) ----
    mc = _import_mc(log_path)
    chunks = quarter_chunks(d_start, d_end)
    done_set = set(state.get('done_chunks', []))
    _tee(f"\n[PHASE-B] {len(chunks)} quarterly chunks; {len(done_set)} already done "
         f"(resume={resume})", log_path)

    for label, c_start, c_end in chunks:
        chunk_path = os.path.join(l2_chunk_dir, f'l2_{label}.parquet')
        if resume and label in done_set and os.path.isfile(chunk_path):
            _tee(f"[{label}] SKIP (already done)", log_path)
            continue
        chunk_pop_rows = [r for r in base_rows if c_start <= r['date'] <= c_end]
        l2_rows = process_chunk(mc, label, c_start, c_end, chunk_pop_rows, base_by_key,
                                 n_draws, log_path)
        if l2_rows:
            l2_df = pl.DataFrame(l2_rows, infer_schema_length=None)
            tmp = chunk_path + '.tmp'
            l2_df.write_parquet(tmp)
            os.replace(tmp, chunk_path)   # atomic
        else:
            # Empty chunk (no signals that quarter) -- write an empty marker so
            # resume logic doesn't retry it forever.
            pl.DataFrame({'symbol': [], 'date': []}).write_parquet(chunk_path)
        done_set.add(label)
        state['done_chunks'] = sorted(done_set)
        _save_json(state_path, state)

    # ---- Merge ----
    _tee(f"\n[MERGE] concatenating {len(chunks)} L2 chunk parquets + base", log_path)
    l2_frames = []
    for label, _c0, _c1 in chunks:
        p = os.path.join(l2_chunk_dir, f'l2_{label}.parquet')
        if os.path.isfile(p):
            df = pl.read_parquet(p)
            if len(df) > 0:
                l2_frames.append(df)
    l2_all = pl.concat(l2_frames, how='diagonal_relaxed') if l2_frames else pl.DataFrame(
        {'symbol': [], 'date': []})

    final = base_df.drop(['anchor_close']).join(l2_all, on=['symbol', 'date'], how='left')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_out = out_path + '.tmp'
    final.write_parquet(tmp_out)
    os.replace(tmp_out, out_path)
    _tee(f"[WRITE] {out_path}: {len(final):,} rows, {len(final.columns)} columns", log_path)
    return final


# ---------------------------------------------------------------------------
# Acceptance checks (PREREG "ACCEPTANCE")
# ---------------------------------------------------------------------------
def acceptance_a_population(ledger, log_path):
    """(a) population reconciliation against the monte_carlo.WINDOWS-exact
    22-now / 5y boundaries (verified in sample_sources.py to reproduce
    PREREG's cited 19,261/25,703 almost exactly off a raw Score query)."""
    from datetime import date as _d
    win_22now = (_d(2022, 1, 1), _d(2026, 4, 24), 19261)
    win_5y = (_d(2021, 1, 1), _d(2026, 4, 15), 25703)
    results = []
    for label, (d0, d1, expected) in (('22-now', win_22now), ('5y', win_5y)):
        n = ledger.filter((pl_col('date') >= d0) & (pl_col('date') <= d1)).height
        pct_diff = (n - expected) / expected * 100
        results.append({'window': label, 'ledger_n': n, 'expected': expected,
                         'pct_diff': round(pct_diff, 2)})
        _tee(f"  [{label}] ledger={n}  expected={expected}  diff={pct_diff:+.2f}%", log_path)
    return results


def pl_col(name):
    import polars as pl
    return pl.col(name)


def acceptance_b_l3_join_rate(ledger, log_path):
    """(b) L3 join-rate on 75+, 2022-08-01+ (the frontier tripwire population;
    tripwire bar 98%+)."""
    import polars as pl
    sub = ledger.filter((pl_col('overall') >= 75) & (pl_col('date') >= date(2022, 8, 1)))
    n = sub.height
    n_joined = sub.filter(pl_col('l3_status').is_not_null()).height
    n_kept = sub.filter(pl_col('l3_status') == 'kept').height
    rate_any = n_joined / n * 100 if n else 0.0
    rate_kept = n_kept / n * 100 if n else 0.0
    _tee(f"  75+ 2022-08+ population: {n}", log_path)
    _tee(f"  L3 join rate (any ledger_v2 row present, incl. no_chain/no_atm/etc status rows): "
         f"{n_joined}/{n} = {rate_any:.1f}%", log_path)
    _tee(f"  L3 join rate (status=='kept' only -- the gold usable subset): "
         f"{n_kept}/{n} = {rate_kept:.1f}%", log_path)
    _tee(f"  [NOTE] ledger_v2's OWN universe is 4,936 candidate rows total (not full-population "
         f"coverage) -- these rates are bounded above by ledger_v2's own build scope, not a "
         f"failure of this driver's join logic.", log_path)
    return {'n_population': n, 'n_joined_any': n_joined, 'n_joined_kept': n_kept,
            'rate_any_pct': round(rate_any, 2), 'rate_kept_pct': round(rate_kept, 2)}


def acceptance_c_spot_check(ledger, log_path, n=20, seed=20260813):
    """(c) 20-row spot-check: re-run compute_trade_outcome+resolve() fresh for
    a random sample of ripe L2 rows, assert bit-consistency with the stored
    l2_sampled value (same seed => same draw => exact match)."""
    import polars as pl
    ripe = ledger.filter(pl_col('l2_ripe') == True)
    if ripe.height == 0:
        _tee("  [STOP] no ripe L2 rows to spot-check", log_path)
        return {'n_checked': 0, 'n_match': 0}
    rng = random.Random(seed)
    idx = sorted(rng.sample(range(ripe.height), min(n, ripe.height)))
    sample = ripe[idx]

    mc = _import_mc(log_path)
    n_match = 0
    mismatches = []
    for row in sample.iter_rows(named=True):
        sym, d = row['symbol'], row['date']
        d0, d1 = d - timedelta(days=5), d + timedelta(days=40)
        bars = mc.load_price_history([sym], d0, d1).get(sym)
        if not bars:
            mismatches.append({'symbol': sym, 'date': str(d), 'reason': 'no fresh price history'})
            continue
        fresh_outcome = mc.compute_trade_outcome(bars, d, stressed=False, trail=False, symbol=sym)
        if fresh_outcome is None:
            mismatches.append({'symbol': sym, 'date': str(d), 'reason': 'fresh compute_trade_outcome returned None'})
            continue
        # Must replicate precompute_outcomes' full pipeline, not just
        # compute_trade_outcome alone -- _attach_earnings_span sets
        # outcome['spans_earnings'], which gates an EXTRA rng.randrange() draw
        # (sample_vega_ratio) inside resolve(). Omitting it desyncs the RNG
        # stream on any earnings-spanning trade and produces a false mismatch
        # (caught live: FLEX 2024-01-30 diverged fresh=0.4326 vs stored=0.2177
        # before this fix -- root-caused to exactly this omission, not a bug
        # in the build pipeline itself, which already calls precompute_outcomes
        # end-to-end).
        trading_days = sorted({b[0] for b in bars})
        ern_map = build_ern_map(mc, [sym], d0, d1, trading_days)
        mc._attach_earnings_span(fresh_outcome, ern_map.get(sym, []), trading_days)
        assert fresh_outcome['kind'] == row['l2_kind'], \
            f"{sym} {d}: kind mismatch fresh={fresh_outcome['kind']} stored={row['l2_kind']}"
        assert fresh_outcome['spans_earnings'] == row['l2_spans_earnings'], \
            f"{sym} {d}: spans_earnings mismatch fresh={fresh_outcome['spans_earnings']} " \
            f"stored={row['l2_spans_earnings']}"
        mc.TP_FILL_MISS_P = row['l2_miss_p_used']
        fresh_rng = random.Random(stable_seed(sym, d))
        fresh_sampled = mc.resolve(fresh_outcome, fresh_rng)[1]
        stored = row['l2_sampled']
        if stored is not None and abs(fresh_sampled - stored) < 1e-9:
            n_match += 1
        else:
            mismatches.append({'symbol': sym, 'date': str(d), 'fresh': fresh_sampled, 'stored': stored})
    _tee(f"  spot-check: {n_match}/{len(idx)} bit-consistent with a fresh engine call", log_path)
    for m in mismatches:
        _tee(f"    MISMATCH: {m}", log_path)
    return {'n_checked': len(idx), 'n_match': n_match, 'mismatches': mismatches}


def acceptance_d_ct_count(ledger, log_path):
    """(d) CT-flag count cross-check against the ctsl_vehicle campaign's
    133-signals figure -- ONLY runs if ct_flag landed (non-NULL)."""
    import polars as pl
    if 'ct_flag' not in ledger.columns or ledger.filter(pl_col('ct_flag').is_not_null()).height == 0:
        _tee("  [SKIP] ct_flag is NULL for every row -- tools/ct_predicate.py was unavailable "
             "at build time. Per instructions: not re-run automatically here.", log_path)
        return {'skipped': True}
    sub = ledger.filter((pl_col('date') >= CT_RECONCILE_START) & (pl_col('date') <= CT_RECONCILE_END))
    n_ct = sub.filter(pl_col('ct_flag') == True).height
    lo = CT_RECONCILE_EXPECT * (1 - CT_RECONCILE_TOLERANCE)
    hi = CT_RECONCILE_EXPECT * (1 + CT_RECONCILE_TOLERANCE)
    within = lo <= n_ct <= hi
    _tee(f"  window [{CT_RECONCILE_START}..{CT_RECONCILE_END}] (== CALIBRATION_CUTOFF_DATE, "
         f"the exact V6 in-sample window): ledger ct_flag=True count = {n_ct}", log_path)
    _tee(f"  vehicle campaign figure: {CT_RECONCILE_EXPECT}  tolerance band [{lo:.1f}, {hi:.1f}]  "
         f"within band: {within}", log_path)
    return {'n_ct': n_ct, 'expected': CT_RECONCILE_EXPECT, 'within_tolerance': within}


def run_acceptance(ledger_path, log_path):
    import polars as pl
    _tee(f"\n{'='*100}\nACCEPTANCE CHECKS -- {ledger_path}\n{'='*100}", log_path)
    ledger = pl.read_parquet(ledger_path)
    _tee(f"[LOAD] {len(ledger):,} rows, {len(ledger.columns)} columns", log_path)

    _tee("\n(a) population reconciliation", log_path)
    a = acceptance_a_population(ledger, log_path)

    _tee("\n(b) L3 join rate", log_path)
    b = acceptance_b_l3_join_rate(ledger, log_path)

    _tee("\n(c) 20-row engine spot-check", log_path)
    c = acceptance_c_spot_check(ledger, log_path)

    _tee("\n(d) CT-flag count cross-check", log_path)
    d = acceptance_d_ct_count(ledger, log_path)

    report = {'population': a, 'l3_join': b, 'spot_check': c, 'ct_count': d,
              'generated_at': str(time.strftime('%Y-%m-%d %H:%M:%S'))}
    return report


# ---------------------------------------------------------------------------
# --selftest -- pure logic, no DB, no MC import.
# ---------------------------------------------------------------------------
def selftest():
    log = print
    log("=== build_ledger.py OFFLINE SELF-TESTS ===")

    assert liq_tier_for_value(None) is None
    assert liq_tier_for_value(320) == 't1'
    assert liq_tier_for_value(320.0001) == 't2'
    assert liq_tier_for_value(1191) == 't2'
    assert liq_tier_for_value(1191.0001) == 't3'
    assert liq_tier_for_value(3486) == 't3'
    assert liq_tier_for_value(14524) == 't4'
    assert liq_tier_for_value(14525) == 't5'
    log("  [1] liq_tier_for_value matches tp_fill_fidelity_30dte's own selftest boundaries OK")

    s1 = stable_seed('AAPL', date(2024, 1, 5))
    s2 = stable_seed('AAPL', date(2024, 1, 5))
    s3 = stable_seed('AAPL', date(2024, 1, 6))
    assert s1 == s2, "stable_seed not deterministic"
    assert s1 != s3, "stable_seed collided across different dates"
    r1 = random.Random(s1).random()
    r2 = random.Random(s2).random()
    assert r1 == r2, "same seed produced different draws"
    log("  [2] stable_seed: deterministic, hashlib-based (not built-in hash()) OK")

    chunks = quarter_chunks(date(2021, 1, 1), date(2021, 8, 16))
    assert chunks[0] == ('2021Q1', date(2021, 1, 1), date(2021, 3, 31)), chunks[0]
    assert chunks[-1][0] == '2021Q3' and chunks[-1][2] == date(2021, 8, 16), chunks[-1]
    assert len(chunks) == 3, chunks
    full = quarter_chunks(PREREG_START, date(2026, 8, 16))
    assert full[0][1] == PREREG_START
    assert full[-1][2] == date(2026, 8, 16)
    # no gaps/overlaps
    for i in range(1, len(full)):
        assert full[i][1] == full[i - 1][2] + timedelta(days=1), f"gap/overlap at chunk {i}: {full[i-1]} -> {full[i]}"
    log(f"  [3] quarter_chunks: {len(full)} chunks {PREREG_START}..2026-08-16, "
        f"no gaps/overlaps, correctly clipped OK")

    wk = parse_weekly_fields('{"w_comp": 65, "w_adj": 7.4, "pre_regime": 75}')
    assert wk['w_comp'] == 65 and wk['w_adj'] == 7.4 and wk['pre_regime'] == 75
    assert wk['w_mom'] is None
    wk_none = parse_weekly_fields(None)
    assert all(v is None for v in wk_none.values())
    log("  [4] parse_weekly_fields: extracts present keys, None-safe on missing/absent OK")

    try:
        from tools.ct_predicate import tag_values
        assert tag_values(82, 12, 'call') == 'ct_call'
        assert tag_values(60, 90, 'call') is None
        assert tag_values(70, 20, 'call') == 'ct_call'   # boundary: trend==CT_CALL_TREND_MAX
        assert tag_values(70, 21, 'call') is None
        log("  [5] tools.ct_predicate wired correctly (boundary cases at trend==20) OK")
    except ImportError:
        log("  [5] tools.ct_predicate NOT YET AVAILABLE -- ct_flag will ship NULL (expected fallback path) SKIPPED-OK")

    log("=== SELFTEST PASS ===")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--selftest', action='store_true')
    p.add_argument('--stage', choices=['smoke', 'build', 'acceptance'], default=None)
    p.add_argument('--start', default=None, help='YYYY-MM-DD, default PREREG 2021-01-01')
    p.add_argument('--end', default=None, help='YYYY-MM-DD, default today')
    p.add_argument('--smoke-start', default='2024-01-01')
    p.add_argument('--smoke-end', default='2024-01-31')
    p.add_argument('--n-draws', type=int, default=N_DRAWS_DEFAULT)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--ledger', default=None, help='path override for --stage acceptance')
    return p.parse_args()


def main():
    args = parse_args()
    if args.selftest:
        raise SystemExit(selftest())

    if args.stage == 'smoke':
        d0 = date.fromisoformat(args.smoke_start)
        d1 = date.fromisoformat(args.smoke_end)
        log_path = os.path.join(LOG_DIR, 'smoke.log')
        _tee(f"\n{'#'*100}\nSMOKE BUILD [{d0}..{d1}] n_draws={args.n_draws}\n{'#'*100}", log_path)
        run_build(d0, d1, args.n_draws, resume=False, out_path=SMOKE_PATH, log_path=log_path,
                  state_path=os.path.join(STATE_DIR, 'state_smoke.json'),
                  base_parquet=os.path.join(STATE_DIR, 'ledger_base_smoke.parquet'),
                  l2_chunk_dir=os.path.join(STATE_DIR, 'l2_chunks_smoke'))
        report = run_acceptance(SMOKE_PATH, log_path)
        _save_json(os.path.join(OUT_DIR, 'smoke_acceptance_report.json'), report)
        return

    if args.stage == 'build':
        d0 = date.fromisoformat(args.start) if args.start else PREREG_START
        d1 = date.fromisoformat(args.end) if args.end else date.today()
        log_path = os.path.join(LOG_DIR, 'build.log')
        _tee(f"\n{'#'*100}\nFULL BUILD [{d0}..{d1}] n_draws={args.n_draws} resume={args.resume}\n{'#'*100}",
             log_path)
        run_build(d0, d1, args.n_draws, resume=args.resume, out_path=LEDGER_PATH, log_path=log_path)
        return

    if args.stage == 'acceptance':
        ledger_path = args.ledger or LEDGER_PATH
        log_path = os.path.join(LOG_DIR, 'acceptance.log')
        report = run_acceptance(ledger_path, log_path)
        _save_json(os.path.join(OUT_DIR, 'acceptance_report.json'), report)
        return

    raise SystemExit("specify --selftest or --stage {smoke,build,acceptance}")


if __name__ == '__main__':
    main()
