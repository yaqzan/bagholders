"""
Monte Carlo — Canonical Optimal Strategy
=========================================
Runs the locked-in optimal strategy across per-year windows (2021-2025) and
the continuous 5-year window (Jan 2021 - Apr 2026).

Per-iteration MC dispersion:
  - Random fill within the trigger bar: U_fire ~ Uniform(open, close)
  - Vega ratio sampled empirically from iv_crush_samples.csv when the trade
    spans an earnings event; else 1.0
  - Same-bar TP+SL collision: 50/50 coin flip per iteration (preserves the
    Realistic-mode behavior; per-iter RNG provides natural dispersion)
  Replaces the prior 3-mode (Conservative/Realistic/Optimistic) system —
  the seed-driven distribution IS the variance band.

Realized P&L on fire is computed via `option_pricing.option_pnl_pct` with
delta + theta + vega closed form (validated +24% RMSE vs delta-only legacy
on N=13,712 real option_prices bars; see experiments/option_pricing_validation.py).

Strategy parameters: NEVER read them from this docstring — every prior version of
this header rotted within weeks (it variously claimed TP+30/35 breadth-adaptive,
SL-35/40, symmetric slippage — all long dead). `strategy_config.py`
(STRATEGY_30DTE / OPT_30DTE) is the single source of truth, loaded at import; the
per-run "[CONFIG]"/NET_* echo lines print what is ACTUALLY loaded — trust those.
Canon as of 2026-08-10 (tpsl_refine ship): calls-only scalp-and-dead-hold, tight
TP + disaster-stop SL, day-15 hard sell, asymmetric execution cost (entry/limit-TP
free, forced exits pay slip). Same-symbol re-entry blocked. $50k start, N=500
default, seeded bounded-fill (single 'seeded' mode).

Usage: python monte_carlo.py
"""

import os
import sys
import io
import csv
import math
import random
import hashlib
import statistics
import multiprocessing
from collections import defaultdict
from datetime import date, timedelta

# Force utf-8 stdout. Guarded so importlib.reload (used by profile/version sweep
# runners) is a no-op here instead of re-wrapping and closing the shared buffer.
if getattr(sys.stdout, 'encoding', '') and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except (ValueError, AttributeError):
        pass

import bisect

from database.models.core import Score, AlgorithmVersion, MarketBreadth, MarketRegime, EarningsDate
from database.models.technical import PriceHistory
from database.utils.trading_calendar import is_trading_day as _is_trading_day

# ---- Strategy constants ----------------------------------------------------
# Values flow from strategy_config.py (single source of truth). Module-level
# names persist for back-compat with code that does `from monte_carlo import
# TP_BASE`. Sweeps mutate via env vars; the dataclass is frozen and never
# touched. See CLAUDE.md "Shipping a Portfolio Strategy Change" — the 7-
# location duplication that motivated this collapse, plus the drift test
# at tests/test_strategy_config_drift.py that guards against regressions.
import strategy_config as _sc
_cfg = _sc.STRATEGY_30DTE   # this module is the 30 DTE engine
_opt = _cfg.option
import monte_carlo_15dte as _mc15

STARTING_CASH      = float(os.environ.get('STARTING_CASH_OV', '') or 50_000.0)
N_ITER             = 500
# (overridden later by N_ITER_OVERRIDE env var if set)
VOL_LOOKBACK       = _cfg.VOL_LOOKBACK
HOLD_DAYS          = int(os.environ.get('HOLD_DAYS_OV', _cfg.HOLD_DAYS))   # HOLD_DAYS_OV=30 => hold to expiry (no day-15 hard-sell)
# --- Calendar-day hold + honest theta — the STANDARD (shipped 2026-06-09) ---
# Defaults read from strategy_config (CALENDAR_HOLD/HOLD_CAL_DAYS/NOMINAL_CAL_DTE);
# env vars still override for experiments. CALENDAR_HOLD re-indexes the call hold
# window + option theta to CALENDAR days so the engine matches real Wealthsimple DTE
# semantics (weekend/holiday aware) and prices theta honestly (the option decays over
# NOMINAL_CAL_DTE calendar days, not trading bars). HOLD_CAL_DAYS = calendar-day
# hard-sell deadline. Scoped to the call path (puts off under Apex). See
# experiments/calendar_hold/STANDARDIZATION_PLAN.md.
CALENDAR_HOLD      = (os.environ.get('CALENDAR_HOLD', '').strip() or ('1' if _cfg.CALENDAR_HOLD else '0')) == '1'
HOLD_CAL_DAYS      = int(os.environ.get('HOLD_CAL_DAYS', '').strip() or _cfg.HOLD_CAL_DAYS)
NOMINAL_CAL_DTE    = int(os.environ.get('NOMINAL_CAL_DTE', '').strip() or _cfg.NOMINAL_CAL_DTE)
# Dead-hold popout fill realism (env-gated, default 0.0 = current free-limit fill).
# Set negative (e.g. -0.015 / -0.03) to charge the popout a half-spread — tests
# whether a "hold longer" edge rides on optimistic near-expiry deep-OTM popout
# fills (worst liquidity) vs genuine TP capture. -0.0 leaves resolve byte-identical.
DH_POP_SLIP        = float(os.environ.get('DH_POP_SLIP', '0.0'))
# Audit probe P7 (HOSTILE_REVIEW A-9): pin TP limit fills AT the barrier instead
# of sampling Uniform(barrier, favorable extreme). A resting limit fills at its
# price, not better; default-OFF preserves production behavior bit-identically
# (guard branch draws no rng; the ON path burns one draw to keep paired-seed
# streams aligned with the OFF arm).
TP_FILL_AT_BARRIER = os.environ.get('TP_FILL_AT_BARRIER', '0').strip() == '1'
# A-9 honest middle: limit fills at max(open, barrier) for calls (min for
# puts) — gap-open price improvement credited, intrabar overshoot not.
# DEFAULT ON since 2026-08-12 (MC-realism default flip, measured vs real OPRA
# prints in experiments/tp_fill_fidelity_30dte: default Uniform(barrier,high)
# credit is ~1.5x real limit mechanics). Canon (optimistic) lens = set '0'.
TP_FILL_GAP_AWARE  = os.environ.get('TP_FILL_GAP_AWARE', '1').strip() == '1'
PREMIUM_MULT       = _cfg.PREMIUM_MULT
DELTA              = _opt.DELTA

# ---------------------------------------------------------------------------
# Gamma x IV Phase B -- real-IV option premium plug (env-gated, default OFF;
# bit-identical to production when unset). Mirrors option_pricing.py's
# GAMMA_AWARE pattern. See experiments/gamma_iv_phaseb/DESIGN.md.
#
# When IV_PREMIUM=1: premium_pct is sourced from a real ATM-IV panel keyed by
# (symbol, signal_date) wherever a panel hit exists (Brenner-Subrahmanyam ATM
# approx: 0.4 * atm_iv * sqrt(dte/365), fraction-of-underlying -- same units
# as the existing PREMIUM_MULT*vol/100 proxy). On a miss (or flag unset), the
# caller's already-computed realized-vol premium_pct is used unchanged. IV
# changes option COST only -- tp_level/sl_level (K-sigma trigger barriers)
# are derived from realized vol upstream and are never touched by this plug.
# ---------------------------------------------------------------------------
IV_PREMIUM        = os.environ.get('IV_PREMIUM', '0').strip() == '1'
IV_PREMIUM_PANEL  = os.environ.get('IV_PREMIUM_PANEL',
                                    r'.cache/polygon_iv/iv_ledger_polygon.parquet')
IV_COVERAGE_DIR   = os.environ.get('IV_COVERAGE_DIR', '').strip()

# Amendment 2 (2026-07-10): strictly-backward as-of join tolerance (calendar days).
# Fixed module constant per DESIGN.md spec, not env-gated.
IV_ASOF_MAX_DAYS  = 14

_IV_PANEL       = None   # lazy {(symbol, date): atm_iv} exact-match dict; {} if load failed
_IV_PANEL_ASOF  = None   # lazy {symbol: (sorted_dates, atm_ivs)} for the as-of bisect fallback
_IV_HITS   = 0
_IV_MISSES = 0
_IV_STALE_0D    = 0   # exact-date hits (fast path)
_IV_STALE_1_3D  = 0   # as-of hits, 1-3 calendar days stale
_IV_STALE_4_7D  = 0   # as-of hits, 4-7 calendar days stale
_IV_STALE_8_14D = 0   # as-of hits, 8-14 calendar days stale

# ---------------------------------------------------------------------------
# IV Premium MODEL -- Amendment (2026-07-12, FABLE ruling on
# experiments/iv_premium_model/DESIGN.md). IV_MODEL is a MODIFIER of the
# IV_PREMIUM path above, meaningful only when IV_PREMIUM=1 (when IV_PREMIUM=0,
# _iv_premium_pct early-returns before IV_MODEL is ever consulted -- flags-off
# path is provably untouched by construction). When IV_PREMIUM=1 and
# IV_MODEL=1, the raw signal-keyed panel join (~19% dosed pre-2022, unusable
# for the 2016-2022 A/B grid) is bypassed entirely in favor of a calibrated
# F2 affine-RV+VIX model of atm_iv, applied at ~100% dose using only inputs
# the engine ALWAYS has (its own realized_vol + MarketRegime.vix_close).
#
# Form + coefficients: F2 (RECOMMENDED, chosen over the mechanical-parsimony
# winner F0 per FABLE's ruling -- see DESIGN.md AMENDMENTS A1). Fit provenance:
# experiments/iv_premium_model/fit_report.json / fit_report.txt (numpy lstsq,
# n=8,642 fit-eligible rows, full-sample coefficients). Clamp = panel's own
# atm_iv [p1, p99].
#
# The model changes premium_pct (option COST) ONLY (DESIGN.md AMENDMENTS A2)
# -- it never touches vol/sigma, tp_level/sl_level, or any barrier definition;
# those are all computed from realized vol upstream, before this plug runs.
# ---------------------------------------------------------------------------
IV_MODEL          = os.environ.get('IV_MODEL', '0').strip() == '1'
VIX_SERIES_PATH   = os.environ.get('VIX_SERIES_PATH',
                                    r'.cache/iv_premium_model/vix_series.parquet')

IV_MODEL_FORM        = 'F2_affine_rv_vix'   # experiments/iv_premium_model/DESIGN.md
IV_MODEL_COEF_A      = 0.14920
IV_MODEL_COEF_B_RV   = 0.09545
IV_MODEL_COEF_C_VIX  = 0.006242
IV_MODEL_CLAMP_FLOOR = 0.163326
IV_MODEL_CLAMP_CAP   = 1.758980

_VIX_SERIES = None   # lazy {date: vix_close} dict; {} if load failed (never raises)
_MODEL_HITS = 0
_MODEL_MISSES = 0
_MODEL_CLAMP_FLOOR_HITS = 0
_MODEL_CLAMP_CAP_HITS   = 0
# model_premium_pct / fallback(rv)_premium_pct, hits only (Amendment A3 telemetry).
_MODEL_RATIOS: list = []


def _load_vix_series():
    """Lazily load MarketRegime.vix_close by date from a small pre-materialized
    parquet (experiments/iv_premium_model/build_vix_series.py), mirroring
    _load_iv_panel()'s shape exactly: module-level cache dict, guarded lazy
    read, safe all-miss degrade, never raises. Deliberately NOT a live MySQL
    query per sweep worker -- same on-demand-parquet-cache precedent as the
    IV panel itself and database/bulk_cache.py (DESIGN.md section 6)."""
    global _VIX_SERIES
    if _VIX_SERIES is not None:
        return _VIX_SERIES
    try:
        import polars as _pl
        df = _pl.read_parquet(VIX_SERIES_PATH, columns=['date', 'vix_close'])
        dts  = [date.fromisoformat(s) for s in df['date'].to_list()]
        vixs = df['vix_close'].to_list()
        _VIX_SERIES = dict(zip(dts, vixs))
    except Exception as _e:
        print(f"  [IV_MODEL] vix series load failed ({_e}); degraded to all-miss fallback")
        _VIX_SERIES = {}
    return _VIX_SERIES


def _load_iv_panel():
    """Lazily load the real-IV panel into two structures:
      - exact {(symbol, date): atm_iv} dict -- the O(1) fast path (unchanged).
      - asof  {symbol: (sorted_dates, atm_ivs)} -- per-symbol sorted arrays for
        the strictly-backward as-of bisect fallback (Amendment 2, 2026-07-10).
    polars NaN != null trap: fill_nan(None) BEFORE drop_nulls. Returns ({}, {})
    (safe all-miss fallback) if the panel is absent/unreadable -- never raises."""
    global _IV_PANEL, _IV_PANEL_ASOF
    if _IV_PANEL is not None:
        return _IV_PANEL, _IV_PANEL_ASOF
    try:
        import polars as _pl
        df = _pl.read_parquet(IV_PREMIUM_PANEL, columns=['symbol', 'date', 'atm_iv'])
        df = df.with_columns(_pl.col('atm_iv').fill_nan(None)).drop_nulls(subset=['atm_iv'])
        syms = df['symbol'].to_list()
        dts  = [date.fromisoformat(s) for s in df['date'].to_list()]
        ivs  = df['atm_iv'].to_list()
        _IV_PANEL = dict(zip(zip(syms, dts), ivs))
        _by_date = defaultdict(dict)   # symbol -> {date: atm_iv}; last-write-wins, same as _IV_PANEL
        for sym, d, iv in zip(syms, dts, ivs):
            _by_date[sym][d] = iv
        _IV_PANEL_ASOF = {}
        for sym, dm in _by_date.items():
            sorted_dates = sorted(dm.keys())
            _IV_PANEL_ASOF[sym] = (sorted_dates, [dm[d] for d in sorted_dates])
    except Exception as _e:
        print(f"  [IV_PREMIUM] panel load failed ({_e}); degraded to all-miss fallback")
        _IV_PANEL = {}
        _IV_PANEL_ASOF = {}
    return _IV_PANEL, _IV_PANEL_ASOF


def _iv_premium_pct(symbol, signal_date, dte, fallback_premium_pct, rv=None):
    """Real-IV premium_pct on a panel hit; else the caller's already-computed
    fallback, unchanged. No-op when IV_PREMIUM is unset (bit-identical to
    production). Counts hits/misses (+ a staleness histogram) for the
    per-window coverage report emitted from _prepare_window.

    Join (Amendment 2, 2026-07-10): exact (symbol, signal_date) dict lookup
    first (fast path, 0d-stale). On a miss, binary-search the symbol's sorted
    panel dates for the most recent panel_date <= signal_date -- STRICTLY
    BACKWARD, the search never considers a later date, so this cannot
    introduce look-ahead. Accepted only if signal_date - panel_date <=
    IV_ASOF_MAX_DAYS calendar days; otherwise falls through to the miss path.

    Amendment (2026-07-12, FABLE): when IV_MODEL=1 (meaningful only if
    IV_PREMIUM=1 too), skips the raw panel join entirely and instead prices
    atm_iv from the calibrated F2 RV+VIX model, applied at ~100% dose
    engine-wide. Requires the caller's own realized-vol (`rv`, optional kwarg,
    default None preserves prior behavior) threaded through; a missing `rv`
    or a missing VIX row is a model miss -- falls back to
    fallback_premium_pct, counted. Model changes premium_pct (COST) ONLY --
    never sigma/vol/tp_level/sl_level (DESIGN.md AMENDMENTS A2)."""
    global _IV_HITS, _IV_MISSES, _IV_STALE_0D, _IV_STALE_1_3D, _IV_STALE_4_7D, _IV_STALE_8_14D
    global _MODEL_HITS, _MODEL_MISSES, _MODEL_CLAMP_FLOOR_HITS, _MODEL_CLAMP_CAP_HITS
    if not IV_PREMIUM or symbol is None:
        return fallback_premium_pct
    if IV_MODEL:
        if rv is None:
            _MODEL_MISSES += 1
            return fallback_premium_pct
        vix = _load_vix_series().get(signal_date)
        if vix is None:
            _MODEL_MISSES += 1
            return fallback_premium_pct
        atm_iv_hat = IV_MODEL_COEF_A + IV_MODEL_COEF_B_RV * rv + IV_MODEL_COEF_C_VIX * vix
        clamped = atm_iv_hat
        if clamped < IV_MODEL_CLAMP_FLOOR:
            clamped = IV_MODEL_CLAMP_FLOOR
            _MODEL_CLAMP_FLOOR_HITS += 1
        elif clamped > IV_MODEL_CLAMP_CAP:
            clamped = IV_MODEL_CLAMP_CAP
            _MODEL_CLAMP_CAP_HITS += 1
        model_premium_pct = 0.4 * clamped * math.sqrt(dte / 365.0)
        _MODEL_HITS += 1
        if fallback_premium_pct and fallback_premium_pct > 0:
            _MODEL_RATIOS.append(model_premium_pct / fallback_premium_pct)
        return model_premium_pct
    exact, asof = _load_iv_panel()
    atm_iv = exact.get((symbol, signal_date))
    age_days = 0
    if atm_iv is None:
        rows = asof.get(symbol)
        if rows:
            dates_arr, ivs_arr = rows
            idx = bisect.bisect_right(dates_arr, signal_date) - 1
            if idx >= 0:
                age = (signal_date - dates_arr[idx]).days
                if age <= IV_ASOF_MAX_DAYS:
                    atm_iv = ivs_arr[idx]
                    age_days = age
    if atm_iv is None:
        _IV_MISSES += 1
        return fallback_premium_pct
    _IV_HITS += 1
    if age_days == 0:
        _IV_STALE_0D += 1
    elif age_days <= 3:
        _IV_STALE_1_3D += 1
    elif age_days <= 7:
        _IV_STALE_4_7D += 1
    else:
        _IV_STALE_8_14D += 1
    return 0.4 * atm_iv * math.sqrt(dte / 365.0)


# ---------------------------------------------------------------------------
# VEGA_STATE -- crash-vega credit for OPEN (held) positions (env-gated,
# default OFF; bit-identical to production when unset). Gameplan P1.4.
#
# Distinct from IV_PREMIUM/IV_MODEL above (which reprice ENTRY premium/cost
# only, once, at signal time): VEGA_STATE marks a position's `vega_ratio`
# DURING the hold via the calibrated ATM-IV-vs-VIX response fit on the
# OWN-panel real-contract IV ledger (experiments/vega_state/
# build_calibration.py -> experiments/vega_state/calibration.json; distinct
# from the iv_premium_model/ Polygon-panel fit). Applied ONLY inside the
# dead-hold CALL walk (_compute_dead_hold_call), which today hardcodes
# vega_ratio=1.0 for its entire walk -- see that function and the comment in
# resolve() ("dead-hold walk uses vega=1.0 ... vega-aware re-walk is
# deferred"). ratio = response(vix_now) / response(vix_entry): >1.0 credits
# a held call when VIX has risen since entry (the intended crash-vega
# credit), <1.0 haircuts it when VIX has fallen. When VEGA_STATE is unset,
# the ratio is always 1.0 -> byte-identical to the prior hardcoded behavior.
# Puts are unwired (puts are OFF portfolio-wide; see traps.md).
#
# CAVEAT (full detail in calibration.json "caveats"): the own-panel fit
# (n=2560, 2025-02..2026-06, never covers a real crash) has r2~0.001 and a
# slightly NEGATIVE VIX coefficient -- i.e. no detectable single-name VIX
# response in this narrow, calm-biased sample. Wired exactly as calibrated,
# no hand-tuning, per the evidence-honesty doctrine (traps.md); whether this
# measurably moves WorstDD/collapse/lever rankings is exactly what the
# queued N=300x8-incl-2020_crash A/B (gameplan P1.4) is for.
# ---------------------------------------------------------------------------
VEGA_STATE              = os.environ.get('VEGA_STATE', '0').strip() == '1'

VEGA_STATE_FORM         = 'affine_vix'   # experiments/vega_state/calibration.json
VEGA_STATE_COEF_A       = 0.636511
VEGA_STATE_COEF_B_VIX   = -0.002647
VEGA_STATE_CLAMP_FLOOR  = 0.0625
VEGA_STATE_CLAMP_CAP    = 1.643014
VEGA_STATE_VIX_HARD_CAP = 84.69

_VEGA_STATE_HITS   = 0   # both entry-VIX and current-bar-VIX resolved
_VEGA_STATE_MISSES = 0   # either lookup missed -> ratio defaulted to 1.0


def _vega_state_atm_iv_hat(vix):
    """Calibrated ATM-IV response at a given VIX level (see calibration.json
    for fit provenance + caveats). Returns None only if vix itself is None."""
    if vix is None:
        return None
    v = min(vix, VEGA_STATE_VIX_HARD_CAP)
    raw = VEGA_STATE_COEF_A + VEGA_STATE_COEF_B_VIX * v
    if raw < VEGA_STATE_CLAMP_FLOOR:
        return VEGA_STATE_CLAMP_FLOOR
    if raw > VEGA_STATE_CLAMP_CAP:
        return VEGA_STATE_CLAMP_CAP
    return raw


def _vega_state_ratio(vix_entry, vix_now):
    """Ratio of the calibrated ATM-IV response at vix_now vs vix_entry, fed
    into option_pricing.option_pnl_pct's `vega_ratio` param for the dead-hold
    call walk. Returns 1.0 (no-op) if either VIX lookup is missing. No-op
    (never called) when VEGA_STATE is unset."""
    global _VEGA_STATE_HITS, _VEGA_STATE_MISSES
    iv_entry = _vega_state_atm_iv_hat(vix_entry)
    iv_now = _vega_state_atm_iv_hat(vix_now)
    if iv_entry is None or iv_now is None or iv_entry <= 0:
        _VEGA_STATE_MISSES += 1
        return 1.0
    _VEGA_STATE_HITS += 1
    return iv_now / iv_entry


# Breadth-adaptive exits — same signal (breadth_score <= BREADTH_THRESHOLD)
# switches BOTH TP and SL into the "stressed" band.
# Env-var overrides allow Bayesian sweeps to vary these in MP workers (which
# re-import this module on spawn and would otherwise revert to strategy_config
# defaults — see experiments/v32_optim/).
TP_BASE            = float(os.environ.get('TP_BASE_OV',   _opt.TP_BASE))
TP_STRESS          = float(os.environ.get('TP_STRESS_OV', _opt.TP_STRESS))
SL_BASE            = float(os.environ.get('SL_BASE_OV',   _opt.SL_BASE))
SL_STRESS          = float(os.environ.get('SL_STRESS_OV', _opt.SL_STRESS))
HARD_SELL_LOSS     = float(os.environ.get('HARD_SELL_LOSS_OV', _cfg.HARD_SELL_LOSS))
BREADTH_THRESHOLD  = int(os.environ.get('BREADTH_THRESHOLD_OV', _opt.BREADTH_THRESHOLD))

# Experiment: stress switch can be driven by per-day same-side signal count
# instead of breadth_score. STRESS_MODE='breadth' (default) | 'count'. When
# 'count', `is_stressed(call)` fires when the day's CALL signal count
# (overall>=75) is <= STRESS_COUNT_THRESHOLD_CALL. Calls-only — puts already
# use PUT_BREADTH_MODE='none' (always BASE). See experiments/dynamic_tpsl/.
STRESS_MODE                  = os.environ.get('STRESS_MODE', 'breadth').lower()
STRESS_COUNT_THRESHOLD_CALL  = int(os.environ.get('STRESS_COUNT_THRESHOLD_CALL', '5'))

# Env-overridable so a realistic-cost diagnostic can model options bid-ask spread
# (crossing half the spread on entry and half on exit). Default = _opt (0 for
# Wealthsimple $0 commissions). e.g. SLIP_ENTRY_OV=-0.05 SLIP_TP_OV=-0.05
# SLIP_SL_OV=-0.05 SLIP_HARD_OV=-0.05 models a ~10% round-trip premium spread.
SLIP_ENTRY = float(os.environ.get('SLIP_ENTRY_OV', _opt.SLIP_ENTRY))
SLIP_TP    = float(os.environ.get('SLIP_TP_OV',    _opt.SLIP_TP))
SLIP_SL    = float(os.environ.get('SLIP_SL_OV',    _opt.SLIP_SL))
SLIP_HARD  = float(os.environ.get('SLIP_HARD_OV',  _opt.SLIP_HARD))

# ---------------------------------------------------------------------------
# P1.5 execution-pessimism certification knobs (env-gated, default = exact
# current behavior -- see experiments/pessimism_cert/). Siblings of SLIP_SL_OV
# above, not a re-tune: these exist so a certification sweep can ask "does a
# shipped lever/param keep its keep-decision under a more pessimistic fill
# model", never to become a new default.
#   TP_FILL_MISS_P    : probability a triggered TP touch fails to fill (thin
#                        resting-limit queue priority) -- wired in resolve().
#   ENTRY_FILL_MISS_P : probability an entry order never fills at all -- wired
#                        in the call/put open closures (_try_fill_call/_put).
#   NEXT_OPEN_ANCHOR  : sensitivity-only flag, entry priced at the NEXT
#                        trading day's open instead of the signal day's close
#                        -- wired in compute_trade_outcome/compute_put_outcome.
# ENTRY_FILL_MISS_P / NEXT_OPEN_ANCHOR default 0/off. TP_FILL_MISS_P defaults
# to the MEASURED economic never-fill rate 0.15 since 2026-08-12 (MC-realism
# default flip; experiments/tp_fill_fidelity_30dte: 15.8%/14.1% of declared
# TPs never fill a resting limit within the hold). Canon (optimistic) lens =
# set TP_FILL_MISS_P=0 TP_FILL_GAP_AWARE=0; quote canon numbers ONLY labeled.
# ---------------------------------------------------------------------------
TP_FILL_MISS_P    = float(os.environ.get('TP_FILL_MISS_P', '0.15'))
ENTRY_FILL_MISS_P = float(os.environ.get('ENTRY_FILL_MISS_P', '0.0'))
NEXT_OPEN_ANCHOR  = os.environ.get('NEXT_OPEN_ANCHOR', '0').strip() == '1'

# Derived: net P&L after slippage. Recomputed from env-overridden TP/SL AND the
# env-overridden SLIP module vars above (NOT from _opt directly) so sweeps that
# vary TP/SL or spread get consistent net values in MP workers.
NET_TP_BASE   = TP_BASE   + SLIP_ENTRY + SLIP_TP
NET_TP_STRESS = TP_STRESS + SLIP_ENTRY + SLIP_TP
NET_SL_BASE   = SL_BASE   + SLIP_ENTRY + SLIP_SL
NET_SL_STRESS = SL_STRESS + SLIP_ENTRY + SLIP_SL
NET_HARD_SELL = HARD_SELL_LOSS + SLIP_ENTRY + SLIP_HARD

# ---------------------------------------------------------------------------
# Track A — regime/equity aggression-scaling wave (PORTFOLIO-STAGE research infra)
# ---------------------------------------------------------------------------
# Additive, DEFAULT-OFF. When AW_ENABLED is unset the per-day aw_scale is 1.0 and
# the call alloc_frac is byte-identical to baseline. NO scoring change, NO version
# bump. AW_MODE selects which scaler drives aw_scale:
#   'market' -> regime_strength_index(date-level VIX/breadth/McClellan/TRIN/BDIV)
#               -> strength_to_aggression. "Does the MARKET say it's a strong regime."
#   'equity' -> EquityNativeScaler on the running (weekly-downsampled) equity curve
#               (drawdown-from-peak + flat-whipsaw-suppressed weekly state). "Does the
#               CURVE ITSELF carry signal."
# Bounded by wave.py construction so collapse/premium floors can never be violated.
# Any missing input falls back to a neutral 0.5 -> aggression ~1.0 (never crashes).
# Primitives live in experiments/equity_wave/wave.py (pure, unit-tested green).
AW_ENABLED = os.environ.get('AW_ENABLED', '0').strip() == '1'
AW_MODE    = os.environ.get('AW_MODE', 'market').strip().lower()
# MC_EMIT_CURVE=1 -> capture a WEEKLY-downsampled equity curve per iteration so the
# experiments/equity_wave/analyze.py A/B can extract no-look-ahead curve predictors.
# Default-OFF => no capture => byte-identical.
MC_EMIT_CURVE = os.environ.get('MC_EMIT_CURVE', '0').strip() == '1'

_AW_WAVE = None      # lazily-imported experiments.equity_wave.wave module (or None)
_AW_SP = None        # cached StrengthParams instance
_AW_AP = None        # cached AggressionParams instance


def _aw_wave():
    """Lazily import the pure wave primitives via an absolute-path sys.path insert
    so it resolves from the queue subprocess CWD. Returns the module or None (so a
    missing module degrades to aw_scale=1.0, never crashes)."""
    global _AW_WAVE, _AW_SP, _AW_AP
    if _AW_WAVE is not None:
        return _AW_WAVE
    try:
        import sys as _sys
        _wd = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'experiments', 'equity_wave')
        if _wd not in _sys.path:
            _sys.path.insert(0, _wd)
        import wave as _wave   # experiments/equity_wave/wave.py
        _AW_WAVE = _wave
        _AW_SP = _wave.StrengthParams()
        _AW_AP = _wave.AggressionParams()
    except Exception as _e:
        print(f"  [AW] wave import failed ({_e}); aggression wave inactive (aw_scale=1.0)")
        _AW_WAVE = False   # sentinel: tried-and-failed (don't retry every call)
    return _AW_WAVE


def _aw_market_scale(vix, breadth, mcclellan, trin, bdiv, semivol_r=None):
    """Date-level market-context aggression multiplier in [agg_min, agg_max].
    Returns 1.0 if the wave module is unavailable or every input is missing
    (strength -> 0.5 -> aggression ~1.0). NaN/None-safe via wave.py's _safe/_clip."""
    w = _aw_wave()
    if not w:
        return 1.0
    try:
        strength = w.regime_strength_index(
            vix=vix, breadth=breadth, mcclellan=mcclellan, trin=trin,
            bdiv=bdiv, semivol_r=semivol_r, params=_AW_SP)
        return float(w.strength_to_aggression(strength, params=_AW_AP))
    except Exception:
        return 1.0

# Derived: underlying σ thresholds for TP/SL barrier-touch detection. Same
# recomputation rationale — must use env-overridden TP/SL.
TP_SIGMA_BASE   = TP_BASE       * _cfg.PREMIUM_MULT / _opt.DELTA
TP_SIGMA_STRESS = TP_STRESS     * _cfg.PREMIUM_MULT / _opt.DELTA
SL_SIGMA_BASE   = abs(SL_BASE)   * _cfg.PREMIUM_MULT / _opt.DELTA
SL_SIGMA_STRESS = abs(SL_STRESS) * _cfg.PREMIUM_MULT / _opt.DELTA

# Put-side fixed parameters (no breadth switch by default).
# Env overrides PUT_TP_OV / PUT_SL_OV added 2026-05-09 for Stage 2 SL-tax sweep.
PUT_TP            = float(os.environ.get('PUT_TP_OV', _opt.PUT_TP))
PUT_SL            = float(os.environ.get('PUT_SL_OV', _opt.PUT_SL))
PUT_NET_TP        = _opt.PUT_NET_TP        # +0.340
PUT_NET_SL        = _opt.PUT_NET_SL        # -0.223
# Recompute σ thresholds from possibly-overridden values
PUT_TP_SIGMA      = abs(PUT_TP) * _cfg.PREMIUM_MULT / _opt.DELTA
PUT_SL_SIGMA      = abs(PUT_SL) * _cfg.PREMIUM_MULT / _opt.DELTA

# Put breadth-adaptive exits (disabled by default; mode in {'none','invert','same'}).
PUT_BREADTH_MODE       = _opt.PUT_BREADTH_MODE
PUT_BREADTH_THRESHOLD  = _opt.PUT_BREADTH_THRESHOLD
PUT_TP_STRESS          = _opt.PUT_TP_STRESS
PUT_SL_STRESS          = _opt.PUT_SL_STRESS

# Put SL hard-hold: suppress SL check for first N trading bars after entry.
# Hold=0 ships (Phase H1/H5 winner under bug-fixed MC).
PUT_SL_HOLD_BARS_DEFAULT = _opt.PUT_SL_HOLD_BARS_DEFAULT
PUT_SL_HOLD_BARS_MONDAY  = _opt.PUT_SL_HOLD_BARS_MONDAY

# Put SL period-stepped mode (research, env-gated 2026-04-27).
# Anchors stop-loss to MAE_winner per period — winners' adverse excursion grows
# with hold time, so a wider SL late in the hold cuts fewer winners.
# 'static' = use PUT_SL throughout (current production)
# 'stepped' = use PUT_SL_STEPPED bands  e.g., [(5,-0.08),(10,-0.13),(15,-0.18)]
#             active band = first whose max_bar >= current_bar.
# v27 5y MAE_winner sigma: 7d=-0.32, 15d=-0.39, 30d=-0.51 -> option SL @1x:
#             bar 1-5  -> ~-9%
#             bar 6-10 -> ~-11%
#             bar 11-15-> ~-14%
PUT_SL_MODE      = os.environ.get('PUT_SL_MODE', 'static')   # 'static' | 'stepped'
PUT_SL_STEPPED   = [(5, -0.09), (10, -0.11), (15, -0.14)]   # default principled MAE-anchored

# Put-vs-call signal priority (research, env-gated 2026-04-27).
# 'calls_first'   - production default: walk calls first, puts get residual slots
# 'puts_first'    - reverse: puts first, calls get residual
# 'merged'        - unify queue, sort by abs(score-50) (conviction), fill in conviction order
# 'wr_merged'     - unify queue, sort by per-tier empirical priority (assessment-driven, 2026-04-28).
#                   Requires WR_PRIORITY_TABLE to be populated by the experiment harness;
#                   each signal gets priority = WR_PRIORITY_TABLE[tier_key]. Higher = better.
#                   Falls back to WR_PRIORITY_FALLBACK for unknown tiers (e.g. overflow).
# Rationale: v28 PutTP rates now match or beat call tier WRs; raw abs(score-50) priority
# under-weights puts vs their empirical edge.
PUT_PRIORITY = os.environ.get('PUT_PRIORITY', 'calls_first')   # 'calls_first' | 'puts_first' | 'merged' | 'wr_merged'

# WR-merged priority table — populated by experiment harness (Phase Pri-1+).
# Maps tier_key (str) -> priority float. Tier keys are the same as TIER_ALLOC /
# PUT_TIER_ALLOC keys: 'ultra', 'top', 'mid', 'low', 'overflow' (calls);
# 'put_top', 'put_mid', 'put_low' (puts). Plus 'ct_call' / 'ct_put' override keys
# (CT-tagged signals always rank ahead via the (0 vs 1) sort prefix, but their
# value within the CT block uses these table entries when present).
WR_PRIORITY_TABLE: dict = {}
WR_PRIORITY_FALLBACK: float = 50.0

# Separate per-side MaxPos caps (None = share the global MAX_POSITIONS pool).
def _env_optional_int(name, default=None):
    raw = os.environ.get(name)
    if raw is None:
        return default
    v = raw.strip()
    if not v:
        return default
    if v.lower() in ('none', 'null', 'shared'):
        return None
    try:
        return int(v)
    except ValueError:
        return default
MAX_POSITIONS_CALL = _env_optional_int('MAX_POSITIONS_CALL', _cfg.MAX_POSITIONS_CALL)
MAX_POSITIONS_PUT  = _env_optional_int('MAX_POSITIONS_PUT', _cfg.MAX_POSITIONS_PUT)

# H3 — DD-soft band call alloc contraction (shipped 2026-05-04, see strategy_config).
# When running portfolio DD ∈ [LO, HI], scale call alloc linearly from 1.0
# down to FLOOR. Above HI = full floor. Below LO = no effect (1.0×).
# 30 DTE ships LO=0.40, HI=0.60, FLOOR=0.50 (mild — only fires deep tail).
DD_SOFT_BAND_LO     = float(os.environ.get('DD_SOFT_BAND_LO', str(_cfg.DD_SOFT_BAND_LO)))
DD_SOFT_BAND_HI     = float(os.environ.get('DD_SOFT_BAND_HI', str(_cfg.DD_SOFT_BAND_HI)))
DD_SOFT_CALL_FLOOR  = float(os.environ.get('DD_SOFT_CALL_FLOOR', str(_cfg.DD_SOFT_CALL_FLOOR)))

# RXDD — VIX-regime-aware CALL alloc dampener (Stage-3 sweep candidate, env-only).
# Mining (v70 Apex N=300 tape, 2026-06-04) found VIX 20-28 entries are the worst
# call cohort (loser-rate lift 1.07, z+52, ~break-even EV) while VIX>=28 panic
# entries are the best (lift 0.80, mpnl +0.15). This smoothly contracts call alloc
# in the mid-VIX 'slow-bleed' band via a Gaussian bump, leaving calm/panic/bull
# tape untouched. Default OFF (RXDD_ENABLED=0) => scale==1.0 => baseline identical.
RXDD_ENABLED  = os.environ.get('RXDD_ENABLED', '1' if getattr(_cfg, 'RXDD_ENABLED', False) else '0') == '1'
RXDD_VIX_C    = float(os.environ.get('RXDD_VIX_C',  str(getattr(_cfg, 'RXDD_VIX_C', 24.0))))    # bump center (VIX)
RXDD_VIX_W    = float(os.environ.get('RXDD_VIX_W',  str(getattr(_cfg, 'RXDD_VIX_W', 4.0))))     # bump width (VIX)
RXDD_DEPTH    = float(os.environ.get('RXDD_DEPTH',  str(getattr(_cfg, 'RXDD_DEPTH', 0.30))))    # max contraction at center (0..1)
RXDD_DD_MIN   = float(os.environ.get('RXDD_DD_MIN', str(getattr(_cfg, 'RXDD_DD_MIN', 0.0))))    # only act when running dd >= this

# LIQUIDITY_FLOOR — P3.6 / known-issues.md #10, Stage-3 A/B ONLY (gameplan.md
# P3.6). Drops a 75+ CALL signal in load_signals() when its per-signal
# option_volume_30d feature (nearest-ATM 20-45 DTE call, trailing-30-cal-day
# avg REAL contract volume where option_prices covers the date [2025-02-10+],
# tagged Stock.average_volume*0.005 fallback otherwise — see
# experiments/liquidity_cascade/DESIGN.md) is below floor. Default OFF (0.0)
# => no-op, bit-identical production. No strategy_config.py field this pass
# (getattr degrades to the literal 0.0 fallback exactly like the RXDD comment
# above — forward-compatible with an eventual ship, not a behavior change now).
LIQUIDITY_FLOOR = float(os.environ.get('LIQUIDITY_FLOOR', str(getattr(_cfg, 'LIQUIDITY_FLOOR', 0.0))))

# LIQUIDITY_MAP_FILE — Stage B' path override for _liquidity_load() (FF3_STAGEB_
# PREREG.md hook #1). Default '' preserves the exact current source
# (cache_path('liquidity_option_volume_30d'), the 2026-07-14 P3.6 artifact).
# Stage B' sweeps point this at the FF-3' real-4y map export
# (.cache/experiment_data/liquidity_option_volume_30d_ff3.parquet,
# FF3_AMENDMENT.md "Stage A -- locked map spec") WITHOUT touching the 07-14
# file -- same 3-column schema (symbol, date, option_volume_30d[, is_fallback])
# so the reader below needs no changes, only its source path. No
# strategy_config field (forward-compatible stub like LIQUIDITY_FLOOR above).
LIQUIDITY_MAP_FILE = os.environ.get('LIQUIDITY_MAP_FILE', str(getattr(_cfg, 'LIQUIDITY_MAP_FILE', ''))).strip()

_LIQUIDITY_MAP = None
def _liquidity_load():
    """Lazy {(symbol_str, date): option_volume_30d} map. Source is
    LIQUIDITY_MAP_FILE when set, else the P3.6 feature parquet (cache_path(
    'liquidity_option_volume_30d'), unchanged default). Keyed by symbol STRING
    (Score PK is the symbol, matches sym_id used at the fill path -- same
    convention as _spread_load/_svr_load). No-op (empty dict -> filter/tier
    lookups never fire) on failure."""
    global _LIQUIDITY_MAP
    if _LIQUIDITY_MAP is not None:
        return _LIQUIDITY_MAP
    _LIQUIDITY_MAP = {}
    try:
        import polars as _pl
        if LIQUIDITY_MAP_FILE:
            from pathlib import Path as _Path
            path = _Path(LIQUIDITY_MAP_FILE)
        else:
            from database.bulk_cache import cache_path as _cp
            path = _cp('liquidity_option_volume_30d')
        if path.exists():
            df = _pl.read_parquet(path)
            for sym, d, v in zip(df['symbol'].to_list(), df['date'].to_list(),
                                 df['option_volume_30d'].to_list()):
                _LIQUIDITY_MAP[(sym, d)] = v
    except Exception as _e:
        print(f"  [LIQUIDITY_FLOOR] map load failed ({_e}); filter inactive")
    return _LIQUIDITY_MAP


def _apply_liquidity_floor_filter(signals):
    """Drop 75+ CALL signals whose option_volume_30d < LIQUIDITY_FLOOR.
    Gated default-OFF; mirrors _apply_weekly_overflow_filter's drop-from-list
    shape. Missing-from-map signals pass through unfiltered."""
    if LIQUIDITY_FLOOR <= 0.0 or not signals:
        return signals
    liq_map = _liquidity_load()
    if not liq_map:
        return signals
    kept = []
    dropped = 0
    for s in signals:
        v = liq_map.get((s.symbol_id, s.date))
        if v is not None and v < LIQUIDITY_FLOOR:
            dropped += 1
            continue
        kept.append(s)
    if dropped:
        print(f"  LIQUIDITY_FLOOR={LIQUIDITY_FLOOR}: dropped {dropped}/{len(signals)} calls "
              f"(option_volume_30d below floor)")
    return kept


# ---------------------------------------------------------------------------
# LIQ_PENALTY -- Stage B' penalized-engine liquidity cost (FF3_STAGEB_PREREG.md
# hook #2). Two-component per-trade cost keyed by the signal's FF3-map liquidity
# tier (same map/keying as LIQUIDITY_FLOOR above), REPLACING (never stacking on)
# the flat SLIP_ENTRY/SLIP_SL/SLIP_HARD canon for penalized trades. SLIP_TP is
# NEVER touched -- limit-TP fills stay free under every arm.
#
# Component (i) -- spread: the current canon prices ONE half-spread on forced
# exits only (SLIP_SL=SLIP_HARD=-0.015, SLIP_ENTRY=SLIP_TP=0.0 -- "mid entry +
# limit-TP cross no spread"; R6 canon, FF3_AMENDMENT.md). Component (i)
# replaces that flat -3%-round-trip-equivalent with the FF2_RESULTS.md §a
# measured per-tier FULL round-trip spread (2.84% t1/illiquid down to 1.04%
# t5/liquid, all <= the modeled 3%), split as a half-spread on ENTRY (which
# now also crosses, unlike the flat canon) and a half-spread on each FORCED
# exit (SL/HARD); TP stays a free limit fill. A penalized trade's round trip
# costs the full tier spread via a forced exit, or half of it via a TP.
#
# Component (ii) -- fill-reality: on SL exits ONLY (literal kind=='sl', pre-
# dead-hold-override -- 'hard'/'prem'/'trail'/'both'/dead-hold-derived kinds
# are NOT extended), an extra deterministic overshoot cost = the
# FF4_RESULTS.md §b per-tier first-breach gap-depth MEDIAN (8.1% t1 -> 4.3% t5,
# "beyond the stop, as a fraction of entry premium") x LIQ_GAP_INCIDENCE.
# SIMPLE DETERMINISTIC form per the brief -- this is NOT a per-iteration
# Bernoulli draw of "did this SL gap through today"; every penalized SL exit
# pays the same probability-weighted EXPECTED addend, so resolve() burns no
# extra rng draw and the paired-seed stream stays aligned with the OFF arm
# bit-for-bit.
#
# LIQ_GAP_INCIDENCE derivation (HONEST, APPROXIMATED -- documented per the
# brief's explicit instruction, env-overridable for orchestrator sensitivity
# checks): default 0.12 = FF4_RESULTS.md §b's 2,543 first-breach gap days
# divided by an APPROXIMATED ~21k "SL-exit-class events" denominator that is
# NOT independently re-derived or verified in this build (FF4's own base is
# 71,277 barrier-relevant contract-days total, not an SL-exit-only count; the
# ~21k figure is the brief's own approximation, carried through as-given
# rather than invented or "improved" here). Treat 0.12 as a documented,
# sweepable placeholder, not a validated empirical constant -- this is the
# deviation flagged most prominently in the builder's final report.
#
# Default OFF (LIQ_PENALTY_ENABLED=0) => every trade's outcome carries
# liq_tier=None => resolve() falls through to the unmodified SLIP_ENTRY/
# SLIP_SL/SLIP_HARD/SLIP_TP globals => baseline byte-identical (G4 mandatory
# proof). Signals absent from the map (pre-2022-08 dates, or any (symbol,date)
# the FF3 map doesn't cover) also resolve liq_tier=None -- coverage-gated
# exactly like LIQUIDITY_FLOOR, "no penalty change".
# ---------------------------------------------------------------------------
LIQ_PENALTY_ENABLED = os.environ.get('LIQ_PENALTY_ENABLED', '1' if getattr(_cfg, 'LIQ_PENALTY_ENABLED', False) else '0') == '1'

# FF2_RESULTS.md §a R6 tripwire table, MEDIAN full round-trip spread by
# FF-3' liquidity-map quintile (tier 1=illiquid .. 5=liquid). Edges below.
LIQ_SPREAD_T1 = float(os.environ.get('LIQ_SPREAD_T1', str(getattr(_cfg, 'LIQ_SPREAD_T1', 0.0284))))
LIQ_SPREAD_T2 = float(os.environ.get('LIQ_SPREAD_T2', str(getattr(_cfg, 'LIQ_SPREAD_T2', 0.0228))))
LIQ_SPREAD_T3 = float(os.environ.get('LIQ_SPREAD_T3', str(getattr(_cfg, 'LIQ_SPREAD_T3', 0.0213))))
LIQ_SPREAD_T4 = float(os.environ.get('LIQ_SPREAD_T4', str(getattr(_cfg, 'LIQ_SPREAD_T4', 0.0192))))
LIQ_SPREAD_T5 = float(os.environ.get('LIQ_SPREAD_T5', str(getattr(_cfg, 'LIQ_SPREAD_T5', 0.0104))))
_LIQ_SPREAD_BY_TIER = {1: LIQ_SPREAD_T1, 2: LIQ_SPREAD_T2, 3: LIQ_SPREAD_T3,
                        4: LIQ_SPREAD_T4, 5: LIQ_SPREAD_T5}

# FF4_RESULTS.md §b first-breach gap-depth MEDIAN by the same tiers (fraction
# of entry premium beyond the stop on a gap-through SL).
LIQ_GAP_OVERSHOOT_T1 = float(os.environ.get('LIQ_GAP_OVERSHOOT_T1', str(getattr(_cfg, 'LIQ_GAP_OVERSHOOT_T1', 0.081))))
LIQ_GAP_OVERSHOOT_T2 = float(os.environ.get('LIQ_GAP_OVERSHOOT_T2', str(getattr(_cfg, 'LIQ_GAP_OVERSHOOT_T2', 0.058))))
LIQ_GAP_OVERSHOOT_T3 = float(os.environ.get('LIQ_GAP_OVERSHOOT_T3', str(getattr(_cfg, 'LIQ_GAP_OVERSHOOT_T3', 0.055))))
LIQ_GAP_OVERSHOOT_T4 = float(os.environ.get('LIQ_GAP_OVERSHOOT_T4', str(getattr(_cfg, 'LIQ_GAP_OVERSHOOT_T4', 0.053))))
LIQ_GAP_OVERSHOOT_T5 = float(os.environ.get('LIQ_GAP_OVERSHOOT_T5', str(getattr(_cfg, 'LIQ_GAP_OVERSHOOT_T5', 0.043))))
_LIQ_GAP_OVERSHOOT_BY_TIER = {1: LIQ_GAP_OVERSHOOT_T1, 2: LIQ_GAP_OVERSHOOT_T2, 3: LIQ_GAP_OVERSHOOT_T3,
                              4: LIQ_GAP_OVERSHOOT_T4, 5: LIQ_GAP_OVERSHOOT_T5}
# See the derivation note in the block comment above -- documented approximation, sweepable.
LIQ_GAP_INCIDENCE = float(os.environ.get('LIQ_GAP_INCIDENCE', str(getattr(_cfg, 'LIQ_GAP_INCIDENCE', 0.12))))

# FF2_RESULTS.md §a quintile edges (option_volume_30d, contracts/day).
_LIQ_TIER_EDGES = (320.0, 1191.0, 3486.0, 14524.0)


def _liq_tier(v):
    """Map tier 1 (illiquid, <=320 c/d) .. 5 (liquid, >14,524 c/d) from an
    option_volume_30d value, per the FF2_RESULTS.md §a R6 quintile edges.
    None on v=None (unknown/uncovered)."""
    if v is None:
        return None
    if v <= _LIQ_TIER_EDGES[0]:
        return 1
    if v <= _LIQ_TIER_EDGES[1]:
        return 2
    if v <= _LIQ_TIER_EDGES[2]:
        return 3
    if v <= _LIQ_TIER_EDGES[3]:
        return 4
    return 5


def _liq_tier_for(symbol, sig_date):
    """Signal's FF3-map liquidity tier (1..5), or None when LIQ_PENALTY is
    disabled, the map is inactive/empty, or the (symbol,date) is absent from
    it -- coverage-gated exactly like LIQUIDITY_FLOOR (pre-2022-08 signals and
    any signal outside the FF3 map get NO penalty change). Computed ONCE per
    signal at precompute time (compute_trade_outcome/compute_put_outcome),
    not per MC iteration -- resolve() just reads outcome['liq_tier']."""
    if not LIQ_PENALTY_ENABLED or symbol is None:
        return None
    liq_map = _liquidity_load()
    if not liq_map:
        return None
    return _liq_tier(liq_map.get((symbol, sig_date)))


# ---------------------------------------------------------------------------
# LIQUIDITY_RANDOM_DROP -- Stage B' random-drop control arm (FF3_STAGEB_PREREG.
# md hook #3, arms C2-C4). Drops exactly K call signals uniformly at random PER
# WINDOW (mutually exclusive with LIQUIDITY_FLOOR -- the sweep driver runs the
# A2-A4 floor arms first, parses their printed per-window drop counts, then
# sets K per-window for the matched C2-C4 control). Nets the concentration
# effect out BY CONSTRUCTION (FF3_AMENDMENT.md item 1 / the liquidity_cascade
# VERDICT's own re-open recipe) instead of discovering it post-hoc as that
# VERDICT did. A dedicated numpy RandomState seeded from
# LIQUIDITY_RANDOM_DROP_SEED (not the shared `random` module used elsewhere in
# this file) keeps the draw fully deterministic and independent of MC's own
# per-iteration rng streams, so the SAME K against the SAME window's signal
# list reproduces the identical drop set on every rerun/resubmit.
# ---------------------------------------------------------------------------
LIQUIDITY_RANDOM_DROP = int(os.environ.get('LIQUIDITY_RANDOM_DROP', str(getattr(_cfg, 'LIQUIDITY_RANDOM_DROP', 0))))
LIQUIDITY_RANDOM_DROP_SEED = int(os.environ.get('LIQUIDITY_RANDOM_DROP_SEED', str(getattr(_cfg, 'LIQUIDITY_RANDOM_DROP_SEED', 20260807))))


def _apply_liquidity_random_drop_filter(signals):
    """Drop exactly LIQUIDITY_RANDOM_DROP calls, uniformly at random from this
    window's post-upstream-filter signal list, via a RandomState seeded from
    LIQUIDITY_RANDOM_DROP_SEED. No-op when LIQUIDITY_RANDOM_DROP<=0. Requires
    LIQUIDITY_FLOOR==0 (the two knobs are mutually exclusive by design -- both
    active would double-drop and confound the control; guarded rather than
    silently compounded)."""
    if LIQUIDITY_RANDOM_DROP <= 0 or not signals:
        return signals
    if LIQUIDITY_FLOOR > 0.0:
        print(f"  [LIQUIDITY_RANDOM_DROP] LIQUIDITY_FLOOR={LIQUIDITY_FLOOR}>0 set simultaneously "
              f"-- random-drop control is mutually exclusive with the floor filter, skipping")
        return signals
    import numpy as _np
    k = min(LIQUIDITY_RANDOM_DROP, len(signals))
    rs = _np.random.RandomState(LIQUIDITY_RANDOM_DROP_SEED)
    drop_idx = set(rs.choice(len(signals), size=k, replace=False).tolist())
    kept = [s for i, s in enumerate(signals) if i not in drop_idx]
    print(f"  LIQUIDITY_RANDOM_DROP={LIQUIDITY_RANDOM_DROP}: dropped {k}/{len(signals)} calls "
          f"(uniform random, seed={LIQUIDITY_RANDOM_DROP_SEED})")
    return kept


def _rxdd_call_scale(dd, vix):
    """Smooth VIX-band CALL alloc multiplier in [1-DEPTH, 1.0]. No-op (1.0) when
    disabled, vix unavailable, or running drawdown < RXDD_DD_MIN."""
    if not RXDD_ENABLED or vix is None:
        return 1.0
    if dd < RXDD_DD_MIN or RXDD_VIX_W <= 0:
        return 1.0
    z = (vix - RXDD_VIX_C) / RXDD_VIX_W
    bump = math.exp(-0.5 * z * z)        # 1.0 at center -> 0 in the tails
    return 1.0 - RXDD_DEPTH * bump

# EXR — Early eXposure Ramp: size- & VIX-conditional CALL exposure cap (Stage-3
# sweep candidate, env-only). The user's "run hotter while the book is small"
# idea made collapse-safe: the effective gross/call premium cap is lifted toward
# EXR_HOT_CAP while equity is small (the 50k->200k phase), throttles back to the
# base cap as the book grows past EXR_SIZE_HI*start (the capital-velocity penalty
# bites at scale), and FADES the hot bonus out as VIX rises so a small book can't
# run hot into a crash (the one robust crash signal, per RXDD). Default OFF
# (EXR_ENABLED=0) => effective cap == base cap => baseline byte-identical.
EXR_ENABLED  = os.environ.get('EXR_ENABLED', '1' if getattr(_cfg, 'EXR_ENABLED', False) else '0') == '1'
EXR_HOT_CAP  = float(os.environ.get('EXR_HOT_CAP',  str(getattr(_cfg, 'EXR_HOT_CAP', 1.00))))   # cap while small & calm
EXR_SIZE_LO  = float(os.environ.get('EXR_SIZE_LO',  str(getattr(_cfg, 'EXR_SIZE_LO', 4.0))))    # equity/start at/below which full hot cap
EXR_SIZE_HI  = float(os.environ.get('EXR_SIZE_HI',  str(getattr(_cfg, 'EXR_SIZE_HI', 40.0))))   # equity/start at/above which throttled to base
EXR_VIX_FADE = float(os.environ.get('EXR_VIX_FADE', str(getattr(_cfg, 'EXR_VIX_FADE', 22.0))))  # VIX at/below which hot bonus is full
EXR_VIX_W    = float(os.environ.get('EXR_VIX_W',    str(getattr(_cfg, 'EXR_VIX_W', 8.0))))      # VIX fade width (bonus->0 by FADE+W)
EXR_DD_FADE  = float(os.environ.get('EXR_DD_FADE',  str(getattr(_cfg, 'EXR_DD_FADE', 0.30))))   # running-DD at which hot bonus->0 (0 disables); reacts faster than VIX to a crash


def _exr_eff_cap(base_cap, equity, vix, dd):
    """Effective premium cap: lifts base_cap toward EXR_HOT_CAP while the book is
    small AND VIX is calm AND not already drawing down. No-op (returns base_cap)
    when disabled, base_cap<=0, or EXR_HOT_CAP not above base. The DD-gate is the
    fast crash brake (running-DD jumps on the first gap-down, before VIX spikes).
    Result is bounded to [base_cap, EXR_HOT_CAP]."""
    if not EXR_ENABLED or base_cap <= 0 or EXR_HOT_CAP <= base_cap:
        return base_cap
    mult = equity / STARTING_CASH if STARTING_CASH > 0 else 0.0
    if EXR_SIZE_HI <= EXR_SIZE_LO:
        size_f = 1.0 if mult <= EXR_SIZE_LO else 0.0
    else:
        size_f = (EXR_SIZE_HI - mult) / (EXR_SIZE_HI - EXR_SIZE_LO)
        size_f = 0.0 if size_f < 0.0 else 1.0 if size_f > 1.0 else size_f
    if vix is None or EXR_VIX_W <= 0:
        vix_f = 1.0
    else:
        vix_f = (EXR_VIX_FADE + EXR_VIX_W - vix) / EXR_VIX_W
        vix_f = 0.0 if vix_f < 0.0 else 1.0 if vix_f > 1.0 else vix_f
    if EXR_DD_FADE <= 0:
        dd_f = 1.0
    else:
        dd_f = 1.0 - (dd / EXR_DD_FADE)
        dd_f = 0.0 if dd_f < 0.0 else 1.0 if dd_f > 1.0 else dd_f
    return base_cap + (EXR_HOT_CAP - base_cap) * size_f * vix_f * dd_f

# TSL — Trailing Stop on WINNERS (Stage-3 sweep candidate, env-only). After a
# call reaches +TP, instead of hard-selling at +TP, trail the underlying by
# TSL_SIGMA*σ below its running peak (let the monster runs run). Gated to
# high-conviction winners (overall >= TSL_MIN_SCORE): the recycle opportunity
# cost of holding a 75+/85+ winner is low on honest v70 because the freed slot
# usually refills with 70-74 overflow, not another 75+ signal. Default OFF
# (TSL_ENABLED=0 => no trade trails) => baseline byte-identical.
TSL_ENABLED   = os.environ.get('TSL_ENABLED', '1' if getattr(_cfg, 'TSL_ENABLED', False) else '0') == '1'
TSL_SIGMA     = float(os.environ.get('TSL_SIGMA', str(getattr(_cfg, 'TSL_SIGMA', 0.6))))        # giveback from peak (σ units)
TSL_MIN_SCORE = float(os.environ.get('TSL_MIN_SCORE', str(getattr(_cfg, 'TSL_MIN_SCORE', 75))))  # trail only signals with overall >= this

# SVR — semivol_r (skew-bridge) entry filter (Stage-3 sweep candidate, env-only).
# semivol_r = 60d downside/upside realized-vol ratio (10y MC-computable cousin of option
# put-skew). Cohort (10y, by quintile within 75+/80+): low (~0.5, euphoric/expensive call)
# is the WORST; very-high (~1.4, crash-mode) weak; middle-high (~0.9-1.1) best. This
# downweights call alloc toward SVR_FLOOR below SVR_LO_FULL and above SVR_HI_FULL, leaving
# the [LO_FULL, HI_FULL] sweet spot at full. Default OFF (SVR_ENABLED=0) => 1.0 => identical.
SVR_ENABLED  = os.environ.get('SVR_ENABLED', '1' if getattr(_cfg, 'SVR_ENABLED', False) else '0') == '1'
SVR_LO_CUT   = float(os.environ.get('SVR_LO_CUT',  str(getattr(_cfg, 'SVR_LO_CUT', 0.60))))
SVR_LO_FULL  = float(os.environ.get('SVR_LO_FULL', str(getattr(_cfg, 'SVR_LO_FULL', 0.80))))
SVR_HI_FULL  = float(os.environ.get('SVR_HI_FULL', str(getattr(_cfg, 'SVR_HI_FULL', 9.0))))
SVR_HI_CUT   = float(os.environ.get('SVR_HI_CUT',  str(getattr(_cfg, 'SVR_HI_CUT', 99.0))))
SVR_FLOOR    = float(os.environ.get('SVR_FLOOR',   str(getattr(_cfg, 'SVR_FLOOR', 0.50))))

_SVR_MAP = None
def _svr_load():
    global _SVR_MAP
    if _SVR_MAP is not None:
        return _SVR_MAP
    _SVR_MAP = {}
    try:
        import polars as _pl
        from datetime import date as _date
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '.cache', 'apex_speed_v70', 'semivol_map.parquet')
        if os.path.exists(p):
            df = _pl.read_parquet(p)
            for sid, ds, sv in zip(df['sym_id'].to_list(), df['date'].to_list(), df['semivol_r'].to_list()):
                y, m, d = ds[:10].split('-')
                _SVR_MAP[(sid, _date(int(y), int(m), int(d)))] = sv
    except Exception as _e:
        print(f"  [SVR] map load failed ({_e}); SVR inactive")
    return _SVR_MAP


def _svr_scale(svr):
    """Call-alloc scale in [SVR_FLOOR, 1.0]. No-op (1.0) when disabled or svr missing.
    Contracts toward FLOOR below SVR_LO_FULL (euphoric calls) and above SVR_HI_FULL
    (crash-mode calls); full in the [LO_FULL, HI_FULL] sweet spot."""
    if not SVR_ENABLED or svr is None:
        return 1.0
    if svr < SVR_LO_FULL:
        if SVR_LO_FULL <= SVR_LO_CUT:
            t = 0.0 if svr <= SVR_LO_CUT else 1.0
        else:
            t = (svr - SVR_LO_CUT) / (SVR_LO_FULL - SVR_LO_CUT)
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        return SVR_FLOOR + (1.0 - SVR_FLOOR) * t
    if svr > SVR_HI_FULL:
        if SVR_HI_CUT <= SVR_HI_FULL:
            t = 0.0 if svr >= SVR_HI_CUT else 1.0
        else:
            t = (SVR_HI_CUT - svr) / (SVR_HI_CUT - SVR_HI_FULL)
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        return SVR_FLOOR + (1.0 - SVR_FLOOR) * t
    return 1.0

# SPREAD_TILT — component-disagreement (ensemble-spread) CALL alloc haircut, GATED STRICTLY
# to the 75-79 band, DOWN-WEIGHT ONLY (weatherization #6, Stage-3 MC-validation arm, env-only).
# spread = sqrt(pop-variance of the 5 members [trend,bb,rsi,macd,stoch] / 5). Kill-test
# (experiments/ensemble_spread/spread_kill_test.py) PASSED SHIP-TO-MC: within 75-79, HIGH-spread
# (disagreement) calls carry ~-1.76pp lower apex option-EV than LOW-spread (Welch t +2.05/+2.17),
# orthogonal to `overall` (corr -0.013), sign-stable 5/5 years. 80-84 INVERTS so the band gate is
# load-bearing. Linear ramp: alloc *= 1 at/below SPREAD_TILT_LO, contract toward (1-DEPTH) at/above
# SPREAD_TILT_HI; never up-size. Default OFF (SPREAD_TILT_ENABLED=0) => 1.0 => baseline identical.
SPREAD_TILT_ENABLED = os.environ.get('SPREAD_TILT_ENABLED', '1' if getattr(_cfg, 'SPREAD_TILT_ENABLED', False) else '0') == '1'
SPREAD_TILT_LO      = float(os.environ.get('SPREAD_TILT_LO',    str(getattr(_cfg, 'SPREAD_TILT_LO', 26.9))))
SPREAD_TILT_HI      = float(os.environ.get('SPREAD_TILT_HI',    str(getattr(_cfg, 'SPREAD_TILT_HI', 31.3))))
SPREAD_TILT_DEPTH   = float(os.environ.get('SPREAD_TILT_DEPTH', str(getattr(_cfg, 'SPREAD_TILT_DEPTH', 0.40))))
# the 5-member ensemble + the band the kill-test flagged (strict; 80-84 inverts).
_SPREAD_MEMBERS = ['trend', 'bb', 'rsi', 'macd', 'stoch']
_SPREAD_BAND_LO, _SPREAD_BAND_HI = 75, 79
_SPREAD_DATA_NAME = 'ensemble_spread_v73_calls_2016'   # v73==v74 pre_boost; spread on pre-pre_boost comps => identical

_SPREAD_MAP = None
def _spread_load():
    """Lazy {(symbol_str, date): component_spread} map. Built once per run from the
    existing v73 component parquet (cache hit; v73==v74 pre_boost so spread is identical).
    Keyed by symbol STRING (Score PK is the symbol) + datetime.date — matches the fill
    path's (sym_id, today). No-op map on any failure (=> SPREAD_TILT inactive)."""
    global _SPREAD_MAP
    if _SPREAD_MAP is not None:
        return _SPREAD_MAP
    _SPREAD_MAP = {}
    try:
        import polars as _pl
        from datetime import date as _date
        from database.bulk_cache import materialize_polars as _mat
        parquet = _mat(_SPREAD_DATA_NAME, lambda: None)
        df = _pl.read_parquet(parquet)
        mean5 = sum(_pl.col(c) for c in _SPREAD_MEMBERS) / 5.0
        var5 = sum((_pl.col(c) - mean5) ** 2 for c in _SPREAD_MEMBERS) / 5.0
        df = df.with_columns(_spread=var5.sqrt())
        for sym, ds, sp in zip(df['symbol'].to_list(), df['date'].to_list(), df['_spread'].to_list()):
            y, m, d = ds[:10].split('-')
            _SPREAD_MAP[(sym, _date(int(y), int(m), int(d)))] = float(sp)
    except Exception as _e:
        print(f"  [SPREAD_TILT] map load failed ({_e}); SPREAD_TILT inactive this run")
    return _SPREAD_MAP


def _spread_tilt_scale(score, spread):
    """CALL alloc scale in [1-DEPTH, 1.0]. No-op (1.0) unless enabled AND 75<=score<=79
    AND spread known. Down-weight only: contracts high-disagreement (high-spread) 75-79
    calls toward (1-DEPTH); full alloc at/below SPREAD_TILT_LO. 80-84/85+/<75 untouched."""
    if not SPREAD_TILT_ENABLED or spread is None:
        return 1.0
    if score < _SPREAD_BAND_LO or score > _SPREAD_BAND_HI:
        return 1.0
    if SPREAD_TILT_HI <= SPREAD_TILT_LO or SPREAD_TILT_DEPTH <= 0.0:
        return 1.0
    if spread <= SPREAD_TILT_LO:
        return 1.0
    if spread >= SPREAD_TILT_HI:
        return 1.0 - SPREAD_TILT_DEPTH
    t = (spread - SPREAD_TILT_LO) / (SPREAD_TILT_HI - SPREAD_TILT_LO)
    return 1.0 - SPREAD_TILT_DEPTH * t

# MWDD — Market-Wave / breadth-momentum DD dampener (Stage-3 sweep candidate, env-only).
# Mining (v70 Apex N=300 tape, 2026-06-05) found the flat/topping McClellan band (~0,
# the breadth-momentum analog of RXDD's VIX 20-28 'slow-bleed') is low-EV (mpnl ~+0.046)
# AND DD-concentrated (conc 2.04) AND orthogonal to RXDD(VIX) + F3F(breadth LEVEL).
# Deep-negative McClellan (breadth capitulation) and strong-positive (healthy momentum)
# are mean-reversion / velocity WINNERS -> left alone via a Gaussian bump centered at ~0.
# Also VIX-panic-excluded (>= MWDD_VIX_PANIC) so COVID-scale capitulation stays untouched
# (per RXDD; that's where the flat-band cohort is a winner), and DD-gated (only acts while
# running dd >= MWDD_DD_MIN). McClellan is "another component of the market wave" (breadth
# momentum). Default OFF (MWDD_ENABLED=0) => scale==1.0 => baseline byte-identical.
MWDD_ENABLED   = os.environ.get('MWDD_ENABLED', '1' if getattr(_cfg, 'MWDD_ENABLED', False) else '0') == '1'
MWDD_MCC_C     = float(os.environ.get('MWDD_MCC_C',     str(getattr(_cfg, 'MWDD_MCC_C', 0.0))))      # bump center (McClellan osc)
MWDD_MCC_W     = float(os.environ.get('MWDD_MCC_W',     str(getattr(_cfg, 'MWDD_MCC_W', 22.0))))     # bump width (McClellan osc)
MWDD_DEPTH     = float(os.environ.get('MWDD_DEPTH',     str(getattr(_cfg, 'MWDD_DEPTH', 0.30))))     # max contraction at center (0..1)
MWDD_DD_MIN    = float(os.environ.get('MWDD_DD_MIN',    str(getattr(_cfg, 'MWDD_DD_MIN', 0.10))))    # only act when running dd >= this
MWDD_VIX_PANIC = float(os.environ.get('MWDD_VIX_PANIC', str(getattr(_cfg, 'MWDD_VIX_PANIC', 28.0)))) # leave VIX panic/capitulation alone


def _mwdd_call_scale(dd, mcc, vix):
    """Smooth McClellan-flat-band CALL alloc multiplier in [1-DEPTH, 1.0]. No-op (1.0)
    when disabled, McClellan unavailable, running dd < MWDD_DD_MIN, or VIX in the panic
    band (>= MWDD_VIX_PANIC, capitulation = mean-reversion winners)."""
    if not MWDD_ENABLED or mcc is None:
        return 1.0
    if dd < MWDD_DD_MIN or MWDD_MCC_W <= 0:
        return 1.0
    if vix is not None and MWDD_VIX_PANIC > 0 and vix >= MWDD_VIX_PANIC:
        return 1.0
    z = (mcc - MWDD_MCC_C) / MWDD_MCC_W
    bump = math.exp(-0.5 * z * z)        # 1.0 at center -> 0 in the tails
    return 1.0 - MWDD_DEPTH * bump


# TVDD — TRIN (Arms-index, volume-FLOW) neutral-band CALL alloc dampener (Stage-3 sweep
# candidate, env-only). Mining (v70 Apex N=300 full-lever tape, 2026-06-07, DD-active subset
# dd>=0.13) found the NEUTRAL volume-flow band (TRIN ~1.0-1.3 = balanced/mild-distribution
# flow) is low-EV (mpnl ~+0.025) AND DD-concentrated AND ORTHOGONAL to RXDD(VIX)+MWDD(McClellan
# count-momentum)+F3F(breadth level): in the all-shipped-levers-off slice (vix<20, |mcc|>30,
# breadth>=40) the TRIN 1.0-1.3 cohort still runs mpnl -0.060 (loser-rate 41%, z+57). TRIN
# extremes are mean-reversion/momentum WINNERS left alone by the Gaussian bump: froth (<0.7,
# heavy up-volume) mpnl +0.085 and panic (>1.8, capitulation volume) mpnl +0.101 are high-EV.
# It's the volume-flow analog of MWDD's flat-McClellan / RXDD's VIX-slow-bleed mid-band.
# VIX-panic-excluded (>= TVDD_VIX_PANIC, keep COVID untouched) and DD-gated (act while running
# dd >= TVDD_DD_MIN). Default OFF (TVDD_ENABLED=0) => scale==1.0 => baseline byte-identical.
TVDD_ENABLED   = os.environ.get('TVDD_ENABLED', '1' if getattr(_cfg, 'TVDD_ENABLED', False) else '0') == '1'
TVDD_TRIN_C    = float(os.environ.get('TVDD_TRIN_C',    str(getattr(_cfg, 'TVDD_TRIN_C', 1.15))))    # bump center (TRIN)
TVDD_TRIN_W    = float(os.environ.get('TVDD_TRIN_W',    str(getattr(_cfg, 'TVDD_TRIN_W', 0.30))))    # bump width (TRIN)
TVDD_DEPTH     = float(os.environ.get('TVDD_DEPTH',     str(getattr(_cfg, 'TVDD_DEPTH', 0.30))))     # max contraction at center (0..1)
TVDD_DD_MIN    = float(os.environ.get('TVDD_DD_MIN',    str(getattr(_cfg, 'TVDD_DD_MIN', 0.13))))    # only act when running dd >= this
TVDD_VIX_PANIC = float(os.environ.get('TVDD_VIX_PANIC', str(getattr(_cfg, 'TVDD_VIX_PANIC', 28.0)))) # leave VIX panic/capitulation alone


def _tvdd_call_scale(dd, trin, vix):
    """Smooth TRIN-neutral-band CALL alloc multiplier in [1-DEPTH, 1.0]. No-op (1.0)
    when disabled, TRIN unavailable, running dd < TVDD_DD_MIN, or VIX in the panic band
    (>= TVDD_VIX_PANIC, capitulation = mean-reversion winners). The Gaussian bump centered
    in the neutral-flow trough leaves the froth (low TRIN) and panic (high TRIN) extremes
    -- both high-EV -- at full alloc."""
    if not TVDD_ENABLED or trin is None:
        return 1.0
    if dd < TVDD_DD_MIN or TVDD_TRIN_W <= 0:
        return 1.0
    if vix is not None and TVDD_VIX_PANIC > 0 and vix >= TVDD_VIX_PANIC:
        return 1.0
    z = (trin - TVDD_TRIN_C) / TVDD_TRIN_W
    bump = math.exp(-0.5 * z * z)        # 1.0 at center -> 0 in the tails
    return 1.0 - TVDD_DEPTH * bump


# DQT — Drawdown Quality Tilt: a DD-gated, CONVICTION-tier contraction of the LOW-conviction
# call tiers (Stage-3 sweep candidate, env-only). ORTHOGONAL to all 4 regime levers
# (RXDD/SVR/MWDD/TVDD key on market context: VIX/skew/McClellan/TRIN; DQT keys on the
# per-signal cascade TIER). Mining the v70 Apex MC tape (DD-active subset, dd>=0.13): the
# LOW (75-79) tier is the single biggest DD contributor (~44% of drawdown-dollars) at low EV
# (mean opt pnl +0.044), and OVERFLOW (70-74) adds ~38% (diffuse, low conc) -- while the
# high-conviction tiers (ultra/top/mid, +0.087..+0.121) are high-EV but only ~19% of DD-dollars.
# "During drawdown, be more selective: contract the WEAKEST tradable signals, keep the conviction
# tiers full." Surgical vs the tier-BLIND DD_SOFT_BAND. DD-gated (only acts while running
# dd >= DQT_DD_MIN), so it weights toward bear drawdowns (where the low tier is genuinely low-EV)
# and away from bull tape (where the low tier is high-EV but the book is rarely drawn down --
# the same DD-gate that keeps RXDD/MWDD/TVDD off the crash-artifact trap). Linear ramp from
# 1.0 at DD_MIN to the per-tier floor at DD_FULL. Default OFF (DQT_ENABLED=0) => scale==1.0
# => baseline byte-identical (low/overflow untouched). OVF_FLOOR defaults to 1.0 (overflow off).
DQT_ENABLED   = os.environ.get('DQT_ENABLED', '1' if getattr(_cfg, 'DQT_ENABLED', False) else '0') == '1'
DQT_DD_MIN    = float(os.environ.get('DQT_DD_MIN',   str(getattr(_cfg, 'DQT_DD_MIN', 0.13))))    # contraction starts at this running dd
DQT_DD_FULL   = float(os.environ.get('DQT_DD_FULL',  str(getattr(_cfg, 'DQT_DD_FULL', 0.45))))   # contraction reaches the floor at this dd
DQT_FLOOR     = float(os.environ.get('DQT_FLOOR',    str(getattr(_cfg, 'DQT_FLOOR', 0.50))))     # LOW (75-79) tier alloc floor (0..1)
DQT_OVF_FLOOR = float(os.environ.get('DQT_OVF_FLOOR', str(getattr(_cfg, 'DQT_OVF_FLOOR', 1.0))))  # OVERFLOW (70-74) tier floor (1.0 = off)


def _dqt_call_scale(dd, tier):
    """Smooth DD-gated CALL alloc multiplier in [floor, 1.0] for the low-conviction tiers.
    No-op (1.0) when disabled, the tier is high-conviction (ultra/top/mid), the tier's floor
    is >=1.0, or running dd < DQT_DD_MIN. Linear ramp 1.0 -> floor over [DD_MIN, DD_FULL]."""
    if not DQT_ENABLED:
        return 1.0
    if tier == 'low':
        floor = DQT_FLOOR
    elif tier == 'overflow':
        floor = DQT_OVF_FLOOR
    else:
        return 1.0
    if floor >= 1.0 or dd < DQT_DD_MIN or DQT_DD_FULL <= DQT_DD_MIN:
        return 1.0
    t = min(1.0, (dd - DQT_DD_MIN) / (DQT_DD_FULL - DQT_DD_MIN))
    return 1.0 - t * (1.0 - floor)


# VXMD — VIX Weekly-MACD momentum CALL alloc dampener (Stage-3 sweep candidate, env-only).
# User hypothesis (2026-06-09): contract calls when VIX *momentum* (weekly MACD building)
# is rising, not just when VIX *level* is high (RXDD). Mining the v70 Apex full-lever tape
# (RXDD+SVR+MWDD live, 2026-06-09) found the rising-weekly-MACD cohort IS DD-concentrated
# (63-70% of DD-dollars) BUT, once implemented POINT-IN-TIME (no within-week look-ahead),
# is HIGH-EV (mpnl +0.08..+0.10 -- the dead-hold mean-reversion runners cluster there) and
# a CRASH ARTIFACT (low-EV bear 2022 -0.17, high-EV bull 2024 +0.09). So env-gated OFF
# reversible null-infra (the DQT/EXR/CDR pattern), kept for the confirmatory screen +
# revisit. Feature `whist` = daily-equivalent weekly MACD histogram of VIX (EMA60-EMA130,
# signal EMA45; point-in-time recursive EMA -> no look-ahead). Smooth ramp contraction as
# whist rises WHIST_LO->WHIST_HI, DD-gated (dd >= DD_MIN), VIX-panic-excluded (>= VIX_PANIC,
# capitulation = mean-reversion winner per RXDD). Default OFF => scale 1.0 => baseline.
VXMD_ENABLED   = os.environ.get('VXMD_ENABLED', '1' if getattr(_cfg, 'VXMD_ENABLED', False) else '0') == '1'
VXMD_WHIST_LO  = float(os.environ.get('VXMD_WHIST_LO',  str(getattr(_cfg, 'VXMD_WHIST_LO', 0.0))))    # whist at/below which no contraction
VXMD_WHIST_HI  = float(os.environ.get('VXMD_WHIST_HI',  str(getattr(_cfg, 'VXMD_WHIST_HI', 0.8))))    # whist at/above which full DEPTH
VXMD_DEPTH     = float(os.environ.get('VXMD_DEPTH',     str(getattr(_cfg, 'VXMD_DEPTH', 0.30))))      # max contraction (0..1)
VXMD_DD_MIN    = float(os.environ.get('VXMD_DD_MIN',    str(getattr(_cfg, 'VXMD_DD_MIN', 0.10))))     # only act when running dd >= this
VXMD_VIX_PANIC = float(os.environ.get('VXMD_VIX_PANIC', str(getattr(_cfg, 'VXMD_VIX_PANIC', 28.0))))  # leave VIX panic/capitulation alone


def _vxmd_call_scale(dd, whist, vix):
    """Smooth VIX-weekly-MACD-momentum CALL alloc multiplier in [1-DEPTH, 1.0]. No-op (1.0)
    when disabled, whist unavailable, running dd < VXMD_DD_MIN, or VIX in panic (>= VXMD_VIX_PANIC).
    Ramp: more contraction as the weekly-MACD histogram rises (vol momentum building)."""
    if not VXMD_ENABLED or whist is None:
        return 1.0
    if dd < VXMD_DD_MIN or VXMD_WHIST_HI <= VXMD_WHIST_LO:
        return 1.0
    if vix is not None and VXMD_VIX_PANIC > 0 and vix >= VXMD_VIX_PANIC:
        return 1.0
    t = (whist - VXMD_WHIST_LO) / (VXMD_WHIST_HI - VXMD_WHIST_LO)
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return 1.0 - VXMD_DEPTH * t


# BDIV — Breadth-DIVergence-at-highs CALL alloc dampener (SHIPPED 2026-06-10, 30 DTE;
# reads strategy_config.BDIV_* with env overrides for sweeps).
# DD-EPISODE-ONSET mine (2026-06-10, fresh v71 tape, experiments/dd_onset_omens/): the user's
# "Hindenburg-style omens precede drawdowns" is NULL/inverted (0 of 24 omen days preceded a
# major onset; omen-day entries are mean-reversion WINNERS), but the classic PRE-TOP BREADTH
# DIVERGENCE survives every kill-test: SPY within ~1% of its 60d high WHILE internal breadth
# has rolled over (breadth_score 10d-change ~ -5) is low-EV (mpnl -0.018 vs +0.029 off, z+25,
# dd_conc 1.82, 12% of trades), sign-stable in 5/6 years INCLUDING both momentum-persistent
# years (2024 -0.028, 2025 -0.053), and survives the all-levers-off orthogonal slice (-0.068).
# Axes: proximity-to-highs is MONOTONIC (closer = worse); the breadth-gap is the usual
# INVERTED-U (gap ~5-8 = slow-bleed trough; gap >12 = sharp shakeout = mean-reversion winners
# left alone by the Gaussian). UNLIKE RXDD/MWDD/TVDD there is NO DD-gate: the lever's value is
# PRE-onset contraction (book dd ~ 0 at the top), and the SPY-near-highs requirement replaces
# the crash-trap protection structurally (the flag CANNOT fire mid-crash -- SPY is never near
# its 60d high in a bear leg; 2022 has ~no firings by construction). BDIV_ENABLED=False
# (or env BDIV_ENABLED=0) => scale==1.0 => byte-identical no-op.
BDIV_ENABLED   = os.environ.get('BDIV_ENABLED', '1' if getattr(_cfg, 'BDIV_ENABLED', False) else '0') == '1'
BDIV_PROX_CUT  = float(os.environ.get('BDIV_PROX_CUT',  str(getattr(_cfg, 'BDIV_PROX_CUT', 0.020))))   # spy_from60h <= -CUT -> no contraction
BDIV_PROX_FULL = float(os.environ.get('BDIV_PROX_FULL', str(getattr(_cfg, 'BDIV_PROX_FULL', 0.005))))  # spy_from60h >= -FULL -> full proximity weight
BDIV_GAP_C     = float(os.environ.get('BDIV_GAP_C',     str(getattr(_cfg, 'BDIV_GAP_C', 6.5))))        # breadth-deterioration bump center (pts/10d)
BDIV_GAP_W     = float(os.environ.get('BDIV_GAP_W',     str(getattr(_cfg, 'BDIV_GAP_W', 2.5))))        # bump width
BDIV_DEPTH     = float(os.environ.get('BDIV_DEPTH',     str(getattr(_cfg, 'BDIV_DEPTH', 0.40))))       # max contraction (0..1)


def _bdiv_call_scale(bdiv):
    """Smooth pre-top breadth-divergence CALL alloc multiplier in [1-DEPTH, 1.0].
    bdiv = (spy_from60h, brd_det10) where spy_from60h <= 0 is SPY's distance below its 60d
    high and brd_det10 = -(breadth_score 10d change) (positive = internals deteriorating).
    scale = 1 - DEPTH * prox_ramp(spy_from60h) * gauss(brd_det10; GAP_C, GAP_W).
    No-op (1.0) when disabled or the map value is unavailable. No DD-gate / no VIX-panic
    knob by design (see block comment: the proximity requirement self-excludes crashes)."""
    if not BDIV_ENABLED or bdiv is None:
        return 1.0
    spyh, det = bdiv
    if spyh is None or det is None or BDIV_GAP_W <= 0 or BDIV_PROX_CUT <= BDIV_PROX_FULL:
        return 1.0
    prox = (spyh + BDIV_PROX_CUT) / (BDIV_PROX_CUT - BDIV_PROX_FULL)
    prox = 0.0 if prox < 0.0 else 1.0 if prox > 1.0 else prox
    if prox <= 0.0:
        return 1.0
    z = (det - BDIV_GAP_C) / BDIV_GAP_W
    bump = math.exp(-0.5 * z * z)
    return 1.0 - BDIV_DEPTH * prox * bump

# Practical exposure controls (Stage 3, env-overridable for sweeps).
# These deliberately avoid drawdown-reactive behavior. They only change how much
# capital the portfolio is allowed to deploy when signal supply is dense.
PRACTICAL_EXPOSURE_ENABLED = os.environ.get(
    'PRACTICAL_EXPOSURE_ENABLED',
    '1' if getattr(_cfg, 'PRACTICAL_EXPOSURE_ENABLED', False) else '0',
) == '1'
_practical_default = lambda name, disabled='0.0': str(getattr(_cfg, name, float(disabled))) if PRACTICAL_EXPOSURE_ENABLED else disabled
PRACTICAL_CAPITAL_CEILING = float(os.environ.get('PRACTICAL_CAPITAL_CEILING', _practical_default('PRACTICAL_CAPITAL_CEILING')))
GROSS_PREMIUM_CAP         = float(os.environ.get('GROSS_PREMIUM_CAP', _practical_default('GROSS_PREMIUM_CAP')))
CALL_PREMIUM_CAP          = float(os.environ.get('CALL_PREMIUM_CAP', _practical_default('CALL_PREMIUM_CAP')))
PUT_PREMIUM_CAP           = float(os.environ.get('PUT_PREMIUM_CAP', _practical_default('PUT_PREMIUM_CAP')))
OPP_SAT_CALL_REF          = float(os.environ.get('OPP_SAT_CALL_REF', _practical_default('OPP_SAT_CALL_REF')))
OPP_SAT_PUT_REF           = float(os.environ.get('OPP_SAT_PUT_REF', _practical_default('OPP_SAT_PUT_REF')))
OPP_SAT_POWER             = float(os.environ.get('OPP_SAT_POWER', _practical_default('OPP_SAT_POWER', '1.0')))
OPP_SAT_FLOOR             = float(os.environ.get('OPP_SAT_FLOOR', _practical_default('OPP_SAT_FLOOR')))

# Put-wave cash reserve (Stage 3 research, env-gated; default inert).
# When enabled with a daily_state.csv, weak recent execution plus constructive
# Market Wave and put overhang scales PUT premium down, then reserves the
# unspent base premium until that position's normal exit bar. The reserve
# remains cash/equity, but cannot be recycled into other fills while held.
PUT_WAVE_RESERVE_ENABLED       = os.environ.get('PUT_WAVE_RESERVE_ENABLED', '0') == '1'
PUT_WAVE_RESERVE_DAILY_STATE   = os.environ.get('PUT_WAVE_RESERVE_DAILY_STATE_CSV', '').strip()
PUT_WAVE_RESERVE_STRENGTH      = float(os.environ.get('PUT_WAVE_RESERVE_STRENGTH', '0.75'))
PUT_WAVE_RESERVE_FLOOR         = float(os.environ.get('PUT_WAVE_RESERVE_FLOOR', '0.40'))
PUT_WAVE_RESERVE_WEAK_TP_FLOOR = float(os.environ.get('PUT_WAVE_RESERVE_WEAK_TP_FLOOR', '0.539'))
PUT_WAVE_RESERVE_WEAK_TP_WIDTH = float(os.environ.get('PUT_WAVE_RESERVE_WEAK_TP_WIDTH', '0.08'))
PUT_WAVE_RESERVE_OPEN_PUT_TRIG = float(os.environ.get('PUT_WAVE_RESERVE_OPEN_PUT_TRIGGER', '13'))
PUT_WAVE_RESERVE_OPEN_PUT_W    = float(os.environ.get('PUT_WAVE_RESERVE_OPEN_PUT_WIDTH', '10'))
PUT_WAVE_RESERVE_FILLED_TRIG   = float(os.environ.get('PUT_WAVE_RESERVE_FILLED_PUT_TRIGGER', '1'))
PUT_WAVE_RESERVE_FILLED_W      = float(os.environ.get('PUT_WAVE_RESERVE_FILLED_PUT_WIDTH', '3'))
PUT_WAVE_RESERVE_SHARE_TRIG    = float(os.environ.get('PUT_WAVE_RESERVE_OPEN_PUT_SHARE_TRIGGER', '0.75'))
PUT_WAVE_RESERVE_SHARE_W       = float(os.environ.get('PUT_WAVE_RESERVE_OPEN_PUT_SHARE_WIDTH', '0.15'))
PUT_WAVE_RESERVE_WAVE_TRIG     = float(os.environ.get('PUT_WAVE_RESERVE_WAVE_THRESHOLD', '0'))
PUT_WAVE_RESERVE_WAVE_W        = float(os.environ.get('PUT_WAVE_RESERVE_WAVE_WIDTH', '16'))
_PUT_WAVE_RESERVE_DAILY_MAP    = None


def _float_env_surface(value, default=0.0):
    if value in (None, '', 'None'):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _smoothstep01(t):
    x = max(0.0, min(1.0, t))
    return x * x * (3.0 - 2.0 * x)


def _above_wave(value, trigger, width):
    if width <= 0:
        return 1.0 if value >= trigger else 0.0
    return _smoothstep01((value - trigger) / width)


def _below_wave(value, trigger, width):
    if width <= 0:
        return 1.0 if value <= trigger else 0.0
    return _smoothstep01((trigger - value) / width)


def _load_put_wave_reserve_daily():
    global _PUT_WAVE_RESERVE_DAILY_MAP
    if _PUT_WAVE_RESERVE_DAILY_MAP is not None:
        return _PUT_WAVE_RESERVE_DAILY_MAP
    rows = []
    if PUT_WAVE_RESERVE_ENABLED and PUT_WAVE_RESERVE_DAILY_STATE:
        try:
            with open(PUT_WAVE_RESERVE_DAILY_STATE, 'r', encoding='utf-8', newline='') as fh:
                for row in csv.DictReader(fh):
                    try:
                        row['_date'] = date.fromisoformat(str(row.get('date', ''))[:10])
                        rows.append(row)
                    except ValueError:
                        continue
        except OSError:
            rows = []
    rows.sort(key=lambda row: row['_date'])
    tp5 = []
    pnl5 = []
    for row in rows:
        open_call = _float_env_surface(row.get('open_call_n'))
        open_put = _float_env_surface(row.get('open_put_n'))
        if row.get('open_put_share') in (None, '', 'None'):
            row['open_put_share'] = open_put / (open_call + open_put) if open_call + open_put else 0.0
        if row.get('prev5_entry_tp_rate') in (None, '', 'None'):
            row['prev5_entry_tp_rate'] = sum(tp5) / len(tp5) if tp5 else None
        if row.get('prev5_entry_avg_pnl_pct') in (None, '', 'None'):
            row['prev5_entry_avg_pnl_pct'] = sum(pnl5) / len(pnl5) if pnl5 else None
        tp = _float_env_surface(row.get('entry_tp_rate'), None)
        pnl = _float_env_surface(row.get('entry_avg_pnl_pct'), None)
        if tp is not None:
            tp5.append(tp)
            tp5 = tp5[-5:]
        if pnl is not None:
            pnl5.append(pnl)
            pnl5 = pnl5[-5:]
    _PUT_WAVE_RESERVE_DAILY_MAP = {row['_date']: row for row in rows}
    return _PUT_WAVE_RESERVE_DAILY_MAP


def _put_wave_reserve_scale(today):
    if not PUT_WAVE_RESERVE_ENABLED:
        return 1.0
    row = _load_put_wave_reserve_daily().get(today)
    if not row:
        return 1.0
    recent_weak = _below_wave(
        _float_env_surface(row.get('prev5_entry_tp_rate'), 0.63),
        PUT_WAVE_RESERVE_WEAK_TP_FLOOR,
        PUT_WAVE_RESERVE_WEAK_TP_WIDTH,
    )
    put_overhang = max(
        _above_wave(_float_env_surface(row.get('open_put_n')), PUT_WAVE_RESERVE_OPEN_PUT_TRIG, PUT_WAVE_RESERVE_OPEN_PUT_W),
        _above_wave(_float_env_surface(row.get('filled_put_n')), PUT_WAVE_RESERVE_FILLED_TRIG, PUT_WAVE_RESERVE_FILLED_W),
        _above_wave(_float_env_surface(row.get('open_put_share')), PUT_WAVE_RESERVE_SHARE_TRIG, PUT_WAVE_RESERVE_SHARE_W),
    )
    wave_divergence = _above_wave(
        _float_env_surface(row.get('breadth_sector_etf_market_wave_signed'), -20.0),
        PUT_WAVE_RESERVE_WAVE_TRIG,
        PUT_WAVE_RESERVE_WAVE_W,
    )
    pressure = recent_weak * wave_divergence * (0.5 + 0.5 * put_overhang)
    scale = max(PUT_WAVE_RESERVE_FLOOR, 1.0 - PUT_WAVE_RESERVE_STRENGTH * pressure)
    return max(0.35, min(1.35, scale))

# Cascade allocation per score tier (calls + puts). Copied from strategy_config
# into mutable module-level dicts so sweeps can edit them in-place; the dataclass
# itself is frozen.
# Env overrides (TIER_ULTRA_OV, etc.) are critical for MP-worker sweeps —
# see comment block above on TP_BASE_OV / experiments/v32_optim/.
def _env_alloc(name, default):
    v = os.environ.get(name, '')
    return float(v) if v else default

TIER_ALLOC = {
    'ultra':    _env_alloc('TIER_ULTRA_OV',    _cfg.TIER_ALLOC.get('ultra', 0.0)),
    'top':      _env_alloc('TIER_TOP_OV',      _cfg.TIER_ALLOC.get('top', 0.0)),
    'mid':      _env_alloc('TIER_MID_OV',      _cfg.TIER_ALLOC.get('mid', 0.0)),
    'low':      _env_alloc('TIER_LOW_OV',      _cfg.TIER_ALLOC.get('low', 0.0)),
    'overflow': _env_alloc('TIER_OVERFLOW_OV', _cfg.TIER_ALLOC.get('overflow', 0.0)),
}
PUT_TIER_ALLOC = {
    'put_top': _env_alloc('PUT_TIER_TOP_OV', _cfg.PUT_TIER_ALLOC.get('put_top', 0.0)),
    'put_mid': _env_alloc('PUT_TIER_MID_OV', _cfg.PUT_TIER_ALLOC.get('put_mid', 0.0)),
    'put_low': _env_alloc('PUT_TIER_LOW_OV', _cfg.PUT_TIER_ALLOC.get('put_low', 0.0)),
}
MAX_POSITIONS      = int(os.environ.get('MAX_POSITIONS_OVERRIDE', str(_cfg.MAX_POSITIONS)))
PRIMARY_THRESHOLD  = _cfg.PRIMARY_THRESHOLD
OVERFLOW_THRESHOLD = _cfg.OVERFLOW_THRESHOLD

def _env_symbol_tuple(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return tuple(default)
    return tuple(s.strip().upper() for s in raw.split(',') if s.strip())

# DTE router — portfolio-only overlay. The 30 DTE engine keeps its sizing stack,
# but eligible call outcomes resolve through the 15 DTE engine.
DTE_ROUTER_ENABLED = os.environ.get(
    'DTE_ROUTER_ENABLED',
    '1' if getattr(_cfg, 'DTE_ROUTER_ENABLED', False) else '0',
) == '1'
DTE_ROUTER_TARGET_DTE = int(os.environ.get('DTE_ROUTER_TARGET_DTE', str(getattr(_cfg, 'DTE_ROUTER_TARGET_DTE', 15))))
DTE_ROUTER_SCORE_MIN = int(os.environ.get('DTE_ROUTER_SCORE_MIN', str(getattr(_cfg, 'DTE_ROUTER_SCORE_MIN', 80))))
DTE_ROUTER_TREND_LT = float(os.environ.get('DTE_ROUTER_TREND_LT', str(getattr(_cfg, 'DTE_ROUTER_TREND_LT', 50.0))))
DTE_ROUTER_VIX_MIN = float(os.environ.get('DTE_ROUTER_VIX_MIN', str(getattr(_cfg, 'DTE_ROUTER_VIX_MIN', 0.0))))
DTE_ROUTER_VIX_MAX = float(os.environ.get('DTE_ROUTER_VIX_MAX', str(getattr(_cfg, 'DTE_ROUTER_VIX_MAX', 0.0))))
DTE_ROUTER_REGIME_MIN = float(os.environ.get('DTE_ROUTER_REGIME_MIN', str(getattr(_cfg, 'DTE_ROUTER_REGIME_MIN', 0.0))))
DTE_ROUTER_REGIME_MAX = float(os.environ.get('DTE_ROUTER_REGIME_MAX', str(getattr(_cfg, 'DTE_ROUTER_REGIME_MAX', 100.0))))
DTE_ROUTER_DAY_CAP = int(os.environ.get('DTE_ROUTER_DAY_CAP', str(getattr(_cfg, 'DTE_ROUTER_DAY_CAP', 0))))
DTE_ROUTER_ALLOC_SCORE_CAP = int(os.environ.get('DTE_ROUTER_ALLOC_SCORE_CAP', str(getattr(_cfg, 'DTE_ROUTER_ALLOC_SCORE_CAP', 0))))
DTE_ROUTER_EXCLUDED_SYMBOLS = _env_symbol_tuple(
    'DTE_ROUTER_EXCLUDED_SYMBOLS',
    getattr(_cfg, 'DTE_ROUTER_EXCLUDED_SYMBOLS', ()),
)

# Design B — premium-based liquidation stop. End-of-bar check on
# option_pnl_pct (delta + theta only at trigger time; vega applied at resolve).
# Disabled by default. Tested values: -0.40, -0.50, -0.60.
PREM_STOP_LOSS = float(os.environ.get('PREM_STOP_LOSS', '0.0'))

# Dead-hold post-SL mechanism (Spec C, in flight 2026-04-30). When SL fires
# AND sampled-vega realized pnl ≤ trigger, override with pre-computed dead-hold
# outcome (popout-or-expiry walk under vega=1.0). See backtest_cascade.py and
# strategy_config.py for design rationale. Walk done at outcome-detection time
# (cheap, deterministic per signal); resolve() applies the override per-iter.
DEAD_HOLD_ENABLED      = (os.environ.get('DEAD_HOLD_ENABLED', '1' if _cfg.DEAD_HOLD_ENABLED else '0') == '1')
DEAD_HOLD_TRIGGER_PNL  = float(os.environ.get('DEAD_HOLD_TRIGGER_PNL', _cfg.DEAD_HOLD_TRIGGER_PNL))
DEAD_HOLD_POPOUT_PNL   = float(os.environ.get('DEAD_HOLD_POPOUT_PNL',  _cfg.DEAD_HOLD_POPOUT_PNL))

# Reallocation — when pool capped and a high-conviction signal arrives, displace
# an existing position by the configured strategy. Disabled by default.
# Strategies: entry_score_low, current_pnl_high, current_pnl_low,
# days_held_high, pnl_high_or_score_low.
REALLOC_STRATEGY        = os.environ.get('REALLOC_STRATEGY', '')                  # '' = off
REALLOC_MIN_ADVANTAGE   = float(os.environ.get('REALLOC_MIN_ADVANTAGE', '5.0'))   # min score-conviction edge required to displace
REALLOC_MIN_HOLD_BARS   = int(os.environ.get('REALLOC_MIN_HOLD_BARS', '2'))       # min days held before realloc-eligible
REALLOC_MAX_PER_DAY     = int(os.environ.get('REALLOC_MAX_PER_DAY', '999'))       # cap realloc events per day
# CDR — Conditional Deep-loss Reallocation gates. *** VALIDATED NULL 2026-06-06 ***
# (kept env-gated OFF as reversible null-infra, like REALLOC/EXR/TSL — DO NOT enable).
# The user's "cut the bag down 60% for the 88 on the board" idea, made regime-safe.
# N=500x8 incl COVID: every gate config (blunt + 11 CDR variants) has 5y compound
# within +-1% of baseline (sign-flipped -3/+3/-1 across N=100/300/500 = pure noise),
# NEVER cuts DD, WORSENS dip DD by +4.5pp, collapse 0. The tape mine (2026-06-06,
# experiments/dead_hold_realloc_v70) showed dead-held bags RECOVER most in panic
# (VIX>=28 pop 0.58) / BULL / oversold tape and LEAST in calm-VIX low-conviction
# tape — so a blunt cut kills recoverers exactly where the 88s fire (crash-collapse).
# These gates restrict displacement to the targetable "expirer" cohort: DEEP losers,
# LOW-conviction entries, in CALM low-DD tape only. ALL default-OFF => byte-identical
# to the existing REALLOC behavior (and to baseline when REALLOC_STRATEGY='').
REALLOC_MIN_LOSS        = float(os.environ.get('REALLOC_MIN_LOSS', '0.0'))        # 0=off; only displace targets down >= this (e.g. 0.40)
REALLOC_MAX_VIX         = float(os.environ.get('REALLOC_MAX_VIX', '999'))         # 999=off; only fire when today's VIX < this (calm-tape gate)
REALLOC_MAX_DD          = float(os.environ.get('REALLOC_MAX_DD', '1.0'))          # 1.0=off; only fire when running dd < this (shallow-DD gate)
REALLOC_MAX_TARGET_SCORE= float(os.environ.get('REALLOC_MAX_TARGET_SCORE', '999'))# 999=off; only displace targets with entry score <= this (low-conviction gate)
REALLOC_CUT_SLIP        = float(os.environ.get('REALLOC_CUT_SLIP', '0.0'))        # 0=off; forced-exit half-spread paid on the cut (asymmetric-cost canon, e.g. 0.015)
# Closed-form option pricing for mark-to-model (vega=1.0; ignored for displacement decisions).
from option_pricing import option_pnl_pct as _opn_for_mark  # noqa: E402

# Regime-aware allocation: alloc_scale = 1.0 + slope * (regime_mult - 1.0).
# Shipped 2026-04-17 asymmetric CUT_ONLY (slope_up=0, slope_down=1.0):
# beats SC100 by +58% compound on 22-now Realistic with lower DD-C.
# Bull-side boost was actively hurting; alpha is entirely stress-side contraction.
REGIME_SLOPE          = _cfg.REGIME_SLOPE
REGIME_SLOPE_PUT      = _cfg.REGIME_SLOPE_PUT
ALLOC_SCALE_FLOOR     = _cfg.ALLOC_SCALE_FLOOR
ALLOC_SCALE_CEIL      = _cfg.ALLOC_SCALE_CEIL
REGIME_SLOPE_UP       = _cfg.REGIME_SLOPE_UP
REGIME_SLOPE_DOWN     = _cfg.REGIME_SLOPE_DOWN
REGIME_SLOPE_PUT_UP   = _cfg.REGIME_SLOPE_PUT_UP
REGIME_SLOPE_PUT_DOWN = _cfg.REGIME_SLOPE_PUT_DOWN

# Breadth-driven allocation knob (F3f) — shipped 2026-04-24.
# Anchors alloc directly on MarketBreadth.breadth_score. Asymmetric:
# cut calls at low breadth, cut puts at high breadth.
BREADTH_ALLOC_ENABLED = _cfg.BREADTH_ALLOC_ENABLED
F3F_CALL_THRESH       = float(os.environ.get('F3F_CALL_THRESH', str(_cfg.F3F_CALL_THRESH)))
F3F_CALL_FLOOR        = float(os.environ.get('F3F_CALL_FLOOR', str(_cfg.F3F_CALL_FLOOR)))
F3F_CALL_LOW          = float(os.environ.get('F3F_CALL_LOW',   str(_cfg.F3F_CALL_LOW)))
F3F_PUT_THRESH        = _cfg.F3F_PUT_THRESH
F3F_PUT_FLOOR         = _cfg.F3F_PUT_FLOOR
F3F_PUT_HIGH          = _cfg.F3F_PUT_HIGH

# Stress-side F3f cut for puts (research, env-gated). Mirrors the call F3f
# but in the stress direction: scale put allocation DOWN when breadth is
# very weak (extreme bear tape). 0 = disabled (production default).
# Targets Phase 1B correlated-DD days (brd ≤ 25, p95 of put-fire density).
F3F_PUT_STRESS_THRESH = float(os.environ.get('F3F_PUT_STRESS_THRESH', '0'))   # breadth below which cut activates
F3F_PUT_STRESS_FLOOR  = float(os.environ.get('F3F_PUT_STRESS_FLOOR',  '0.70')) # min scale at extreme stress
F3F_PUT_STRESS_LOW    = float(os.environ.get('F3F_PUT_STRESS_LOW',    '10'))   # breadth at which floor is reached

PUT_THRESHOLD      = int(os.environ.get('PUT_THRESHOLD_OVERRIDE', str(_cfg.PUT_THRESHOLD)))
COLLAPSE_THRESHOLD = _cfg.COLLAPSE_THRESHOLD

# Design D — asymmetric put-only score-stage flip (research, env-gated).
# When DESIGN_D_PUT_FLIP=1, put-side overall is recomputed from pre_regime
# using the flipped apply (drops the (2.0-mult) mirror, applies mult directly).
# Per-trade 5y diff-assess (experiments/design_d_put_flip.py): <25 WR15 +3.6pp,
# WR30 +4.5pp; ~60% fewer puts (concentration). Calls byte-identical.
DESIGN_D_PUT_FLIP  = os.environ.get('DESIGN_D_PUT_FLIP', '0') == '1'
N_ITER_OVERRIDE    = int(os.environ.get('N_ITER_OVERRIDE', '0'))
if N_ITER_OVERRIDE > 0:
    N_ITER = N_ITER_OVERRIDE
WINDOWS_OVERRIDE   = os.environ.get('WINDOWS_OVERRIDE', '')  # comma list e.g. '5y' or '2024,2025,5y'

# Earnings-window put suppression — SHIPPED 2026-04-26.
# Drops puts in [MIN_OV, MAX_OV] when an EarningsDate falls in
# (signal_date, signal_date + DAYS trading days]. Defaults from
# strategy_config; env overrides preserved for sweeps.
EARN_SUPP_PUT          = os.environ.get('EARN_SUPP_PUT', '1' if _cfg.EARN_SUPP_PUT else '0') == '1'
EARN_SUPP_PUT_DAYS     = int(os.environ.get('EARN_SUPP_PUT_DAYS', str(_cfg.EARN_SUPP_PUT_DAYS)))
EARN_SUPP_PUT_MIN_OV   = int(os.environ.get('EARN_SUPP_PUT_MIN_OV', str(_cfg.EARN_SUPP_PUT_MIN_OV)))
EARN_SUPP_PUT_MAX_OV   = int(os.environ.get('EARN_SUPP_PUT_MAX_OV', str(_cfg.EARN_SUPP_PUT_MAX_OV)))

# Regime-gated PUT_THRESHOLD tightening (research, env-gated).
# When PUT_TIGHTEN_BREADTH_LE is set (>0), drops put signals where
# overall > PUT_TIGHTEN_THRESH AND breadth_score on signal date <= the
# given threshold. Selectively concentrates puts to <=PUT_TIGHTEN_THRESH
# only during stressed-breadth tape, leaving normal-tape volume unchanged.
# Hypothesis: combines the live-observed "max put 20 in stress" anecdote
# with the put_concentration_sweep finding that D+PT20 wins +119% on
# 5-month dip but loses 5y compound. Untested 2026-04-27.
PUT_TIGHTEN_BREADTH_LE = int(os.environ.get('PUT_TIGHTEN_BREADTH_LE', '0'))  # 0 = disabled (drop when breadth <= LE)
PUT_TIGHTEN_BREADTH_GE = int(os.environ.get('PUT_TIGHTEN_BREADTH_GE', '0'))  # 0 = disabled (drop when breadth >= GE)
PUT_TIGHTEN_THRESH     = int(os.environ.get('PUT_TIGHTEN_THRESH', '20'))     # tighten to this when gate fires

# Daily put cap (portfolio-stage, env-gated). Cap puts admitted per calendar day,
# sorted by conviction (lower overall = more bearish = higher priority). 0 = disabled.
# Directly targets Phase 1B finding: 2.1% of days have >=50 concurrent puts,
# driving correlated-SL DD spikes. Median is only 11/day; cap fires on tail days.
MAX_PUTS_PER_DAY       = int(os.environ.get('MAX_PUTS_PER_DAY', '0'))

# Weak-weekly put filter (research, env-gated 2026-04-27).
# Per-trade evidence (experiments/put_wadj_cross_buckets.py, 5y): puts with
# weight_info.w_adj > -13 (weak weekly drag) carry WR15 -8 to -15pp below
# strong-weekly siblings across every put bucket. The score=5 dip (66.9% WR
# vs neighbors 73-75%) is half-composed of weak-weekly REJECTION puts at
# 52% WR (N=75 / 5y). When WEAK_WEEKLY_PUT_DROP=1, drop puts where overall
# in [MIN_OV, MAX_OV] AND w_adj > WADJ_GT.
# Score-stage interpretation: weak weekly = thesis not confirmed across
# timeframes; if extreme score reached only via stress regime amplification,
# it's a stress-noise artifact rather than a confirmed bearish setup.
WEAK_WEEKLY_PUT_DROP   = os.environ.get('WEAK_WEEKLY_PUT_DROP', '0') == '1'
WEAK_WEEKLY_PUT_MIN_OV = int(os.environ.get('WEAK_WEEKLY_PUT_MIN_OV', '0'))
WEAK_WEEKLY_PUT_MAX_OV = int(os.environ.get('WEAK_WEEKLY_PUT_MAX_OV', '10'))
WEAK_WEEKLY_PUT_WADJ   = float(os.environ.get('WEAK_WEEKLY_PUT_WADJ', '-13.0'))
WEAK_WEEKLY_PUT_VSIG_REJ_ONLY = os.environ.get('WEAK_WEEKLY_PUT_VSIG_REJ_ONLY', '0') == '1'

# Weak-weekly CALL filter (research, env-gated 2026-05-05).
# v36 miss-ledger evidence (CALL 70+ wadj=neg: z=+9.2, miss 52.7%, N=1537/5y).
# CWCF dampener (v32) handles overall>=75 ∧ wadj<1, but the 70-74 range is
# untreated. 70-74 wadj<0 cohort: N=1501, miss 52.9% (TP 47.1%) vs 70-74
# baseline 57.6% TP — 10.5pp below the cohort. Mostly outside cascade today
# (overflow=0), but ~33 enter via CT promotion (miss 54.5%) and ~13 stragglers
# at 75-79 (post-CWCF residue). Filter shape mirrors WEAK_WEEKLY_PUT_DROP:
# drop CALLS where overall in [MIN_OV, MAX_OV] AND wadj < WADJ_LT.
WEAK_WEEKLY_CALL_DROP     = os.environ.get('WEAK_WEEKLY_CALL_DROP', '1' if _cfg.WEAK_WEEKLY_CALL_DROP else '0') == '1'
WEAK_WEEKLY_CALL_MIN_OV   = int(os.environ.get('WEAK_WEEKLY_CALL_MIN_OV', str(_cfg.WEAK_WEEKLY_CALL_MIN_OV)))
WEAK_WEEKLY_CALL_MAX_OV   = int(os.environ.get('WEAK_WEEKLY_CALL_MAX_OV', str(_cfg.WEAK_WEEKLY_CALL_MAX_OV)))
WEAK_WEEKLY_CALL_WADJ     = float(os.environ.get('WEAK_WEEKLY_CALL_WADJ', str(_cfg.WEAK_WEEKLY_CALL_WADJ_LT)))
WEAK_WEEKLY_CALL_STOCH_GE = int(os.environ.get('WEAK_WEEKLY_CALL_STOCH_GE', str(_cfg.WEAK_WEEKLY_CALL_STOCH_GE)))

# Earnings-window call PROMOTION (research, env-gated).
# Per-trade A/B (experiments/earn_call_pretest.py, 5y) shows calls firing within
# 3 trd days BEFORE earnings have +10..+16pp WR15 uplift over the population:
#   75-79 PRE-ern: 88.3% WR15 (N=265) vs other 72.2%  -> +16.1pp
#   80-84 PRE-ern: 85.5% WR15 (N=62)  vs other 71.8%  -> +13.7pp
#   70-74 PRE-ern: 80.8% WR15 (N=992) vs other 70.8%  -> +10.0pp (overflow disabled)
# Knob promotes signals in [min_ov, max_ov] to the named tier when earnings
# falls in (D, D + days trading days].
EARN_BOOST_CALL          = os.environ.get('EARN_BOOST_CALL', '0') == '1'
EARN_BOOST_CALL_DAYS     = int(os.environ.get('EARN_BOOST_CALL_DAYS', '3'))
EARN_BOOST_CALL_MIN_OV   = int(os.environ.get('EARN_BOOST_CALL_MIN_OV', '75'))
EARN_BOOST_CALL_MAX_OV   = int(os.environ.get('EARN_BOOST_CALL_MAX_OV', '79'))
EARN_BOOST_CALL_TIER     = os.environ.get('EARN_BOOST_CALL_TIER', 'top')   # 'ultra'=25%, 'top'=15%, 'mid'=15%, 'low'=15%

# Counter-trend (CT) cascade promotion — Path B follow-up to v22 revert.
# Tag put signals with TREND>=70 (counter-trend reversal) and call signals
# with TREND<=30 (counter-trend bounce). Promote tagged signals to the
# highest-conviction tier and fill them ahead of the score-sorted queue.
# Source: experiments/x_conf_counter_trend.py — CT-PUT bucket WR15=81.5%
# (n=232, 2y), +14.8pp over put baseline. Preserves score distribution
# (no recalculate, no version bump).
CT_PROMOTE         = os.environ.get('CT_PROMOTE', '1') == '1'
CT_PUT_TREND_MIN   = int(os.environ.get('CT_PUT_TREND_MIN', '80'))
CT_CALL_TREND_MAX  = int(os.environ.get('CT_CALL_TREND_MAX', '20'))  # set to -1 to disable ct_call
CT_CALL_TIER       = os.environ.get('CT_CALL_TIER', 'ultra')         # ultra=25%, top=15%, mid=15%, low=15%
CT_PUT_TIER        = os.environ.get('CT_PUT_TIER', 'put_top')        # put_top=15%, put_mid=12%, put_low=12%


def ct_tag(overall, trend, side):
    """Return 'ct_call' / 'ct_put' / None for a signal at (overall, trend)."""
    if not CT_PROMOTE or trend is None:
        return None
    if side == 'put' and overall <= PUT_THRESHOLD and trend >= CT_PUT_TREND_MIN:
        return 'ct_put'
    if side == 'call' and overall >= OVERFLOW_THRESHOLD and trend <= CT_CALL_TREND_MAX:
        return 'ct_call'
    return None


def _dte_router_value_on_or_before(sorted_dates, value_map, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx < 0:
        return None
    return value_map.get(sorted_dates[idx])


def _dte_router_call_eligible(sig, router_dates, vix_map, regime_map, routed_today):
    if not DTE_ROUTER_ENABLED or DTE_ROUTER_TARGET_DTE != 15:
        return False
    if routed_today >= DTE_ROUTER_DAY_CAP:
        return False
    if int(sig.overall) < DTE_ROUTER_SCORE_MIN:
        return False
    if sig.trend is None or float(sig.trend) >= DTE_ROUTER_TREND_LT:
        return False
    if DTE_ROUTER_VIX_MIN > 0.0:
        vix = _dte_router_value_on_or_before(router_dates, vix_map, sig.date)
        if vix is None or vix < DTE_ROUTER_VIX_MIN:
            return False
    if DTE_ROUTER_VIX_MAX > 0.0:   # crash-gate: route to 15DTE only in low-vol/bull
        vix = _dte_router_value_on_or_before(router_dates, vix_map, sig.date)
        if vix is None or vix > DTE_ROUTER_VIX_MAX:
            return False
    if DTE_ROUTER_REGIME_MIN > 0.0 or DTE_ROUTER_REGIME_MAX < 100.0:
        regime_comp = _dte_router_value_on_or_before(router_dates, regime_map, sig.date)
        if regime_comp is None or regime_comp < DTE_ROUTER_REGIME_MIN or regime_comp > DTE_ROUTER_REGIME_MAX:
            return False
    symbol = str(getattr(sig, 'symbol_id', '')).upper()
    if symbol in DTE_ROUTER_EXCLUDED_SYMBOLS:
        return False
    return True


def _load_dte_router_market_maps(d_start, d_end):
    from dte_router import load_router_market_maps

    return load_router_market_maps(d_start, d_end)


def _load_dte_router_j_map(sym_ids):
    if not sym_ids:
        return {}
    try:
        from database.earnings_jump_cache import load_per_stock_jumps

        return load_per_stock_jumps(set(sym_ids))
    except Exception:
        return {}


# ---------- CTSL — Counter-Trend Score Lift (Phase 3 MC research, OFF by default) ----------
# Score-stage substitute candidate for CT_PROMOTE per Phase 2.5 calibration.
# When CTSL_ENABLED=1, applied at signal-load time: lifts CT-eligible call signals
# UP (trend ≤ TREND_MAX) and dampens CT-eligible put signals DOWN (trend ≥ TREND_MIN).
# See experiments/ctsl/FINDINGS.md for full design + Phase 2.5 calibration evidence.
#
# Phase 3 test matrix:
#   Config A (production):     CTSL_ENABLED=0  CT_PROMOTE=1
#   Config B (stack):          CTSL_ENABLED=1  CT_PROMOTE=1
#   Config C (substitute):     CTSL_ENABLED=1  CT_PROMOTE=0
CTSL_ENABLED = (os.environ.get('CTSL_ENABLED', '1' if _cfg.CTSL_ENABLED else '0') == '1')
# Call-side knobs (Stage 1 winner shipped 2026-05-08 — see strategy_config.py)
CTSL_CALL_TREND_MAX        = int(os.environ.get('CTSL_CALL_TREND_MAX',         str(_cfg.CTSL_CALL_TREND_MAX)))
CTSL_CALL_TARGET           = float(os.environ.get('CTSL_CALL_TARGET',          str(_cfg.CTSL_CALL_TARGET)))
CTSL_CALL_ALPHA            = float(os.environ.get('CTSL_CALL_ALPHA',           str(_cfg.CTSL_CALL_ALPHA)))
CTSL_CALL_TREND_POWER      = float(os.environ.get('CTSL_CALL_TREND_POWER',     str(_cfg.CTSL_CALL_TREND_POWER)))
CTSL_CALL_TIER_FLOOR       = float(os.environ.get('CTSL_CALL_TIER_FLOOR',      str(_cfg.CTSL_CALL_TIER_FLOOR)))
CTSL_CALL_SCORE_NORM_WEIGHT = float(os.environ.get('CTSL_CALL_SCORE_NORM_WEIGHT', str(_cfg.CTSL_CALL_SCORE_NORM_WEIGHT)))
CTSL_CALL_SCORE_NORM_POWER = float(os.environ.get('CTSL_CALL_SCORE_NORM_POWER', str(_cfg.CTSL_CALL_SCORE_NORM_POWER)))
# Put-side knobs (Stage 1 winner shipped 2026-05-08)
CTSL_PUT_TREND_MIN         = int(os.environ.get('CTSL_PUT_TREND_MIN',          str(_cfg.CTSL_PUT_TREND_MIN)))
CTSL_PUT_TARGET            = float(os.environ.get('CTSL_PUT_TARGET',           str(_cfg.CTSL_PUT_TARGET)))
CTSL_PUT_ALPHA             = float(os.environ.get('CTSL_PUT_ALPHA',            str(_cfg.CTSL_PUT_ALPHA)))
CTSL_PUT_TREND_POWER       = float(os.environ.get('CTSL_PUT_TREND_POWER',      str(_cfg.CTSL_PUT_TREND_POWER)))
CTSL_PUT_TIER_CEILING      = float(os.environ.get('CTSL_PUT_TIER_CEILING',     str(_cfg.CTSL_PUT_TIER_CEILING)))
CTSL_PUT_SCORE_NORM_WEIGHT = float(os.environ.get('CTSL_PUT_SCORE_NORM_WEIGHT', str(_cfg.CTSL_PUT_SCORE_NORM_WEIGHT)))
CTSL_PUT_SCORE_NORM_POWER  = float(os.environ.get('CTSL_PUT_SCORE_NORM_POWER', str(_cfg.CTSL_PUT_SCORE_NORM_POWER)))


def _ctsl_call_lift(overall: float, trend: int) -> float:
    """Compute new_overall for a CT-call signal under CTSL transform.
    Applied only when overall >= 70 AND trend <= CTSL_CALL_TREND_MAX."""
    if trend is None or trend > CTSL_CALL_TREND_MAX:
        return float(overall)
    tm = float(CTSL_CALL_TREND_MAX + 1)
    trend_dist = max(0.0, min(1.0, (CTSL_CALL_TREND_MAX - trend + 1) / tm))
    snw = CTSL_CALL_SCORE_NORM_WEIGHT
    snp = CTSL_CALL_SCORE_NORM_POWER
    if abs(snw) < 1e-6:
        score_factor = 1.0
    elif snw > 0:
        sn = max(0.0, min(1.0, (overall - 70.0) / 30.0))
        score_factor = 1.0 + snw * (sn ** snp)
    else:
        sn = max(0.0, min(1.0, (76.0 - overall) / 6.0))
        score_factor = 1.0 + abs(snw) * (sn ** snp)
    lift = (CTSL_CALL_ALPHA * (trend_dist ** CTSL_CALL_TREND_POWER)
            * score_factor * (CTSL_CALL_TARGET - overall))
    new_overall = overall + lift
    if new_overall < CTSL_CALL_TIER_FLOOR:
        new_overall = CTSL_CALL_TIER_FLOOR
    return max(0.0, min(100.0, new_overall))


def _ctsl_put_dampen(overall: float, trend: int) -> float:
    """Compute new_overall for a CT-put signal under CTSL transform.
    Applied only when overall <= 25 AND trend >= CTSL_PUT_TREND_MIN."""
    if trend is None or trend < CTSL_PUT_TREND_MIN:
        return float(overall)
    span = max(1.0, float(100 - CTSL_PUT_TREND_MIN + 1))
    trend_dist = max(0.0, min(1.0, (trend - CTSL_PUT_TREND_MIN + 1) / span))
    snw = CTSL_PUT_SCORE_NORM_WEIGHT
    snp = CTSL_PUT_SCORE_NORM_POWER
    if abs(snw) < 1e-6:
        score_factor = 1.0
    elif snw > 0:
        sn = max(0.0, min(1.0, (25.0 - overall) / 25.0))
        score_factor = 1.0 + snw * (sn ** snp)
    else:
        sn = max(0.0, min(1.0, (overall - 19.0) / 6.0))
        score_factor = 1.0 + abs(snw) * (sn ** snp)
    dampen = (CTSL_PUT_ALPHA * (trend_dist ** CTSL_PUT_TREND_POWER)
              * score_factor * (overall - CTSL_PUT_TARGET))
    new_overall = overall - dampen
    if new_overall > CTSL_PUT_TIER_CEILING:
        new_overall = CTSL_PUT_TIER_CEILING
    return max(0.0, min(100.0, new_overall))


def _apply_ctsl_to_signals(signals, side: str):
    """Mutate signal.overall in-place per CTSL transform. Idempotent if CTSL_ENABLED=0."""
    if not CTSL_ENABLED:
        return signals
    fn = _ctsl_call_lift if side == 'call' else _ctsl_put_dampen
    for s in signals:
        try:
            ov = int(s.overall)
            tr = int(s.trend) if s.trend is not None else None
            new_ov = fn(float(ov), tr)
            if new_ov != ov:
                s.overall = int(round(new_ov))
        except (TypeError, ValueError, AttributeError):
            continue
    return signals


# --- Experimental (default OFF): weekly-maturity overflow filter -------------
# Thread-1 finding (experiments/component_reweight/weekly_mine.py, honest v70):
# among 70-74 OVERFLOW calls, those whose prior-COMPLETED-week weekly is mature
# (wadj_completed in the top tercile, >~8.4) win ~4-5pp LESS on the apex barrier
# (z=-4.29) -- the sub-break-even "buying an over-extended weekly" tail. The
# signal is HONEST (day-of-week stable; wadj_completed is the prior completed
# week so it cannot leak forward; the edge persists on Friday). This optional
# filter drops that tail from the overflow band so overflow can run safer / at a
# higher collapse-safe alloc. Inert unless WEEKLY_OVF_FILTER=1. Read-only;
# touches no defaults, profiles, scoring, or strategy_config.
WEEKLY_OVF_FILTER     = os.environ.get('WEEKLY_OVF_FILTER', '0') == '1'
WEEKLY_OVF_WCOMP_MAX  = float(os.environ.get('WEEKLY_OVF_WCOMP_MAX', '8.4'))
WEEKLY_OVF_BAND_LO    = int(os.environ.get('WEEKLY_OVF_BAND_LO', '70'))
WEEKLY_OVF_BAND_HI    = int(os.environ.get('WEEKLY_OVF_BAND_HI', '74'))


def _apply_weekly_overflow_filter(signals):
    """Drop 70-74 overflow calls whose prior-completed-week weekly is mature
    (wadj_completed > WEEKLY_OVF_WCOMP_MAX). Gated default-OFF; read-only."""
    if not WEEKLY_OVF_FILTER:
        return signals
    import json as _json_w
    kept = []
    for s in signals:
        try:
            ov = int(s.overall)
        except (TypeError, ValueError):
            kept.append(s); continue
        if WEEKLY_OVF_BAND_LO <= ov <= WEEKLY_OVF_BAND_HI:
            wi = getattr(s, 'weight_info', None)
            wc = None
            if wi:
                try:
                    d = _json_w.loads(wi) if isinstance(wi, str) else wi
                    wc = d.get('wadj_completed')
                except Exception:
                    wc = None
            if wc is not None and float(wc) > WEEKLY_OVF_WCOMP_MAX:
                continue   # drop mature-weekly overflow loser
        kept.append(s)
    return kept


WINDOWS = [
    ('2018',       date(2018, 1, 1),  date(2018, 12, 31)),   # 2018-Q4 selloff (v70 10y data)
    ('2020',       date(2020, 1, 1),  date(2020, 12, 31)),   # COVID crash + V-recovery
    ('2020_crash', date(2020, 2, 1),  date(2020, 4, 30)),    # sharp COVID drawdown
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('dip',    date(2025, 11, 1), date(2026, 4, 24)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 24)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('5y',     date(2021, 1, 1),  date(2026, 4, 15)),
    ('10y',    date(2016, 6, 1),  date(2026, 4, 15)),        # full honest-v70 history incl crashes
]

# ---------------------------------------------------------------------------
# P1.2 -- opt-in deep-window SCREEN tier (env MC_WINDOW_SET={default|deep}).
# Default path (env unset or 'default') is BIT-IDENTICAL: WINDOWS is the
# exact 12-row list above, untouched. Setting MC_WINDOW_SET=deep APPENDS 4
# named pre-2016 crash/long windows riding the 1995-01-03 v74 score+regime+
# breadth backfill (survivor-only). Per assessment-backtest.md's
# SCREEN-not-GATE doctrine (see that doc's "Deep-window screens" section):
# these are NEVER a calibration/tuning/ship-gate target -- a deep FAIL is a
# mandatory investigation, a deep PASS is weak comfort, never collapse-proof.
# "Clip to data": a window whose start predates a symbol's/series' coverage
# simply yields fewer or zero signals for that portion (load_signals /
# load_breadth_map / load_regime_map already tolerate sparse coverage) --
# not an error, no special-casing needed here.
# ---------------------------------------------------------------------------
MC_WINDOW_SET = os.environ.get('MC_WINDOW_SET', 'default').strip().lower()

DEEP_WINDOWS = [
    ('ltcm_1998',              date(1998, 7, 1), date(1998, 10, 31)),  # LTCM/Russia/Asia crisis
    ('dotcom_crash_2000_2002', date(2000, 3, 1), date(2002, 10, 31)),  # dot-com bust (2.5y grind)
    ('gfc_crash_2007_2009',    date(2007, 10, 1), date(2009, 3, 31)),  # global financial crisis
    ('2007_now',               date(2007, 1, 1), date(2026, 4, 24)),  # GFC + everything since (long compound read)
]

if MC_WINDOW_SET == 'deep':
    WINDOWS = WINDOWS + DEEP_WINDOWS

# Removed 2026-04-29: 3-mode collision system replaced by per-iter seeded
# random fill within the trigger bar. Single MC mode now ('seeded').
# COLLISION_MODES kept as a back-compat label for external scripts that
# iterate over modes; only 'seeded' is meaningful.
COLLISION_MODES = ['seeded']


def score_to_tier(score):
    if score >= 95: return 'ultra'
    if score >= 85: return 'top'
    if score >= 80: return 'mid'
    if score >= 75: return 'low'
    return 'overflow'


def put_score_to_tier(score):
    if score <= 15: return 'put_top'
    if score <= 20: return 'put_mid'
    return 'put_low'  # 21-25


# ---- Data loading -----------------------------------------------------------

def load_breadth_map(d_start, d_end):
    """Return (sorted_dates, {date: breadth_score}) — most-recent-on-or-before lookup."""
    rows = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
        .where(
            MarketBreadth.date >= d_start - timedelta(days=60),
            MarketBreadth.date <= d_end,
            MarketBreadth.breadth_score.is_null(False),
        )
        .order_by(MarketBreadth.date)
        .tuples()
    )
    m = {d: float(bs) for d, bs in rows}
    return sorted(m.keys()), m


def load_mcclellan_map(d_start, d_end):
    """Return (sorted_dates, {date: mcclellan_oscillator}) — on-or-before lookup (MWDD)."""
    rows = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.mcclellan_oscillator)
        .where(
            MarketBreadth.date >= d_start - timedelta(days=60),
            MarketBreadth.date <= d_end,
            MarketBreadth.mcclellan_oscillator.is_null(False),
        )
        .order_by(MarketBreadth.date)
        .tuples()
    )
    m = {d: float(v) for d, v in rows}
    return sorted(m.keys()), m


def load_trin_map(d_start, d_end):
    """Return (sorted_dates, {date: trin}) — on-or-before lookup (TVDD)."""
    rows = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.trin)
        .where(
            MarketBreadth.date >= d_start - timedelta(days=60),
            MarketBreadth.date <= d_end,
            MarketBreadth.trin.is_null(False),
        )
        .order_by(MarketBreadth.date)
        .tuples()
    )
    m = {d: float(v) for d, v in rows}
    return sorted(m.keys()), m


def load_vix_whist_map(d_start, d_end):
    """Return (sorted_dates, {date: weekly-MACD-hist of VIX}) — on-or-before lookup (VXMD).
    Daily-equivalent weekly MACD: EMA60-EMA130 of daily VIX, signal EMA45, hist=macd-signal.
    250d warmup so EMA130 is warm at d_start; recursive EMA is point-in-time (no look-ahead)."""
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.vix_close)
        .where(
            MarketRegime.date >= d_start - timedelta(days=250),
            MarketRegime.date <= d_end,
            MarketRegime.vix_close.is_null(False),
        )
        .order_by(MarketRegime.date)
        .tuples()
    )
    if not rows:
        return [], {}
    dates = [d for d, _ in rows]
    vix = [float(v) for _, v in rows]

    def _ema(xs, span):
        a = 2.0 / (span + 1.0)
        out, e = [], None
        for x in xs:
            e = x if e is None else a * x + (1.0 - a) * e
            out.append(e)
        return out

    macd = [a - b for a, b in zip(_ema(vix, 60), _ema(vix, 130))]
    sig = _ema(macd, 45)
    m = {d: (mc - s) for d, mc, s in zip(dates, macd, sig)}
    return sorted(m.keys()), m


def load_bdiv_map(d_start, d_end):
    """Return (sorted_dates, {date: (spy_from60h, brd_det10)}) — on-or-before lookup (BDIV).
    spy_from60h = SPY close / rolling-60-trading-day max - 1 (<= 0; ~0 = at the 60d high).
    brd_det10   = -(breadth_score - breadth_score 10 trading days earlier) (positive =
    internal breadth deteriorating). 120d warmup so the rolling max + 10d diff are warm."""
    from database.models.technical import PriceHistory
    spy_rows = list(
        PriceHistory.select(PriceHistory.date, PriceHistory.close)
        .where(
            PriceHistory.symbol == 'SPY',
            PriceHistory.date >= d_start - timedelta(days=120),
            PriceHistory.date <= d_end,
            PriceHistory.close.is_null(False),
        )
        .order_by(PriceHistory.date)
        .tuples()
    )
    brd_rows = list(
        MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
        .where(
            MarketBreadth.date >= d_start - timedelta(days=120),
            MarketBreadth.date <= d_end,
            MarketBreadth.breadth_score.is_null(False),
        )
        .order_by(MarketBreadth.date)
        .tuples()
    )
    if not spy_rows or not brd_rows:
        return [], {}
    spyh = {}
    closes = []
    for d, c in spy_rows:
        closes.append(float(c))
        win = closes[-60:]
        spyh[d] = closes[-1] / max(win) - 1.0
    det = {}
    bvals = [float(v) for _, v in brd_rows]
    bdates = [d for d, _ in brd_rows]
    for i, d in enumerate(bdates):
        if i >= 10:
            det[d] = -(bvals[i] - bvals[i - 10])
    m = {d: (spyh[d], det[d]) for d in det if d in spyh}
    return sorted(m.keys()), m


def load_regime_map(d_start, d_end):
    """Return (sorted_dates, {date: alloc_scalar}) — most-recent-on-or-before lookup.

    When BREADTH_ALLOC_ENABLED (default), returns breadth_score per date
    (F3f knob). Otherwise returns regime_multiplier per date (legacy).
    The function name is preserved for call-site compatibility; the value
    semantics are determined by BREADTH_ALLOC_ENABLED at the alloc_scale_for
    consumer.
    """
    if BREADTH_ALLOC_ENABLED:
        rows = list(
            MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)
            .where(
                MarketBreadth.date >= d_start - timedelta(days=60),
                MarketBreadth.date <= d_end,
                MarketBreadth.breadth_score.is_null(False),
            )
            .order_by(MarketBreadth.date)
            .tuples()
        )
        m = {d: float(brd) for d, brd in rows}
    else:
        rows = list(
            MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier)
            .where(
                MarketRegime.date >= d_start - timedelta(days=60),
                MarketRegime.date <= d_end,
                MarketRegime.regime_multiplier.is_null(False),
            )
            .order_by(MarketRegime.date)
            .tuples()
        )
        m = {d: float(mult) for d, mult in rows}
    return sorted(m.keys()), m


def regime_on_or_before(sorted_dates, rmap, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    if idx >= 0:
        return rmap[sorted_dates[idx]]
    # No coverage: neutral default. For breadth that's 50.0 (no scaling);
    # for regime_mult that's 1.0 (no scaling). Both produce alloc_scale=1.0.
    return 50.0 if BREADTH_ALLOC_ENABLED else 1.0


# ------------------------------------------------------------------------
# SAW Put U-curve — sector ETF breadth-driven put alloc scaler.
# Mechanism: at each signal date, look up cross-sector ETF breadth (% of 11
# SPDRs above EMA50) and compute a U-curve scale. Multiplies into put alloc.
# Env-gated; default OFF (uniform 1.0).
# ------------------------------------------------------------------------
SAW_PUT_UCURVE_ENABLED   = int(os.environ.get('SAW_PUT_UCURVE_ENABLED', '1' if _cfg.SAW_PUT_UCURVE_ENABLED else '0'))
SAW_PUT_UCURVE_SHAPE     = os.environ.get('SAW_PUT_UCURVE_SHAPE',  _cfg.SAW_PUT_UCURVE_SHAPE)
SAW_PUT_UCURVE_MIDPOINT  = float(os.environ.get('SAW_PUT_UCURVE_MIDPOINT',  str(_cfg.SAW_PUT_UCURVE_MIDPOINT)))
SAW_PUT_UCURVE_HALFWIDTH = float(os.environ.get('SAW_PUT_UCURVE_HALFWIDTH', str(_cfg.SAW_PUT_UCURVE_HALFWIDTH)))
SAW_PUT_UCURVE_FLOOR     = float(os.environ.get('SAW_PUT_UCURVE_FLOOR',     str(_cfg.SAW_PUT_UCURVE_FLOOR)))
SAW_PUT_UCURVE_CEIL      = float(os.environ.get('SAW_PUT_UCURVE_CEIL',      str(_cfg.SAW_PUT_UCURVE_CEIL)))
SAW_PUT_UCURVE_POWER     = float(os.environ.get('SAW_PUT_UCURVE_POWER',     str(_cfg.SAW_PUT_UCURVE_POWER)))
SAW_PUT_UCURVE_K         = float(os.environ.get('SAW_PUT_UCURVE_K',         str(_cfg.SAW_PUT_UCURVE_K)))

_SAW_SEC_BRD_MAP   = None
_SAW_SEC_BRD_DATES = None

def _saw_load_sec_brd():
    global _SAW_SEC_BRD_MAP, _SAW_SEC_BRD_DATES
    if _SAW_SEC_BRD_MAP is not None:
        return
    pq_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '.cache', 'sector_etf_screen',
                           'sector_breadth_daily_2020plus.csv')
    m = {}
    if os.path.exists(pq_path):
        import csv as _csv
        from datetime import date as _d
        with open(pq_path, 'r') as f:
            rdr = _csv.DictReader(f)
            for row in rdr:
                try:
                    m[_d.fromisoformat(row['date'])] = float(row['sec_brd_ema50'])
                except (KeyError, ValueError):
                    continue
    _SAW_SEC_BRD_MAP = m
    _SAW_SEC_BRD_DATES = sorted(m.keys())

def saw_sec_brd_on_or_before(d):
    _saw_load_sec_brd()
    if not _SAW_SEC_BRD_DATES:
        return 50.0
    idx = bisect.bisect_right(_SAW_SEC_BRD_DATES, d) - 1
    if idx >= 0:
        return _SAW_SEC_BRD_MAP[_SAW_SEC_BRD_DATES[idx]]
    return 50.0

def saw_put_ucurve_scale(d):
    """U-curve scale at signal date d. Returns 1.0 when disabled."""
    if not SAW_PUT_UCURVE_ENABLED:
        return 1.0
    brd = saw_sec_brd_on_or_before(d)
    floor = SAW_PUT_UCURVE_FLOOR
    ceil  = SAW_PUT_UCURVE_CEIL
    if SAW_PUT_UCURVE_SHAPE == 'sigmoid':
        lo_thresh = SAW_PUT_UCURVE_MIDPOINT - SAW_PUT_UCURVE_HALFWIDTH
        hi_thresh = SAW_PUT_UCURVE_MIDPOINT + SAW_PUT_UCURVE_HALFWIDTH
        import math as _m
        try:
            sig_lo = 1.0 / (1.0 + _m.exp(-(lo_thresh - brd) / max(0.5, SAW_PUT_UCURVE_K)))
        except OverflowError:
            sig_lo = 0.0 if (lo_thresh - brd) < 0 else 1.0
        try:
            sig_hi = 1.0 / (1.0 + _m.exp(-(brd - hi_thresh) / max(0.5, SAW_PUT_UCURVE_K)))
        except OverflowError:
            sig_hi = 0.0 if (brd - hi_thresh) < 0 else 1.0
        activation = min(1.0, sig_lo + sig_hi)
        return floor + activation * (ceil - floor)
    else:
        d_norm = abs(brd - SAW_PUT_UCURVE_MIDPOINT) / max(1.0, SAW_PUT_UCURVE_HALFWIDTH)
        if d_norm > 1.0: d_norm = 1.0
        return floor + (d_norm ** SAW_PUT_UCURVE_POWER) * (ceil - floor)


def _breadth_alloc_scale(breadth, is_put):
    """F3f curve: breadth -> alloc scale. See F3F_* constants for shape."""
    if breadth is None:
        return 1.0
    if is_put:
        # Stress-side cut: reduce put allocation in extreme bear tape
        if F3F_PUT_STRESS_THRESH > 0 and breadth < F3F_PUT_STRESS_THRESH:
            if breadth <= F3F_PUT_STRESS_LOW:
                return F3F_PUT_STRESS_FLOOR
            frac = (F3F_PUT_STRESS_THRESH - breadth) / max(1.0, F3F_PUT_STRESS_THRESH - F3F_PUT_STRESS_LOW)
            return 1.0 - frac * (1.0 - F3F_PUT_STRESS_FLOOR)
        # Bull-side cut: reduce put allocation in strong bull tape (existing)
        if breadth <= F3F_PUT_THRESH:
            return 1.0
        if breadth >= F3F_PUT_HIGH:
            return F3F_PUT_FLOOR
        return 1.0 - (breadth - F3F_PUT_THRESH) / (F3F_PUT_HIGH - F3F_PUT_THRESH) * (1.0 - F3F_PUT_FLOOR)
    else:
        if breadth >= F3F_CALL_THRESH:
            return 1.0
        if breadth <= F3F_CALL_LOW:
            return F3F_CALL_FLOOR
        return F3F_CALL_FLOOR + (breadth - F3F_CALL_LOW) / (F3F_CALL_THRESH - F3F_CALL_LOW) * (1.0 - F3F_CALL_FLOOR)


def alloc_scale_for(value, is_put=False):
    """Return [floor..ceil] clamped scale factor.

    When BREADTH_ALLOC_ENABLED (default), `value` is a breadth_score and the
    F3f curves apply. Otherwise `value` is a regime_multiplier and the legacy
    asymmetric REGIME_SLOPE_* logic applies.
    """
    if BREADTH_ALLOC_ENABLED:
        s = _breadth_alloc_scale(value, is_put)
        if s < ALLOC_SCALE_FLOOR: return ALLOC_SCALE_FLOOR
        if s > ALLOC_SCALE_CEIL:  return ALLOC_SCALE_CEIL
        return s

    # Legacy regime_multiplier path
    delta = value - 1.0
    if is_put:
        if delta >= 0 and REGIME_SLOPE_PUT_UP is not None:
            slope = REGIME_SLOPE_PUT_UP
        elif delta < 0 and REGIME_SLOPE_PUT_DOWN is not None:
            slope = REGIME_SLOPE_PUT_DOWN
        else:
            slope = REGIME_SLOPE_PUT if REGIME_SLOPE_PUT is not None else REGIME_SLOPE
    else:
        if delta >= 0 and REGIME_SLOPE_UP is not None:
            slope = REGIME_SLOPE_UP
        elif delta < 0 and REGIME_SLOPE_DOWN is not None:
            slope = REGIME_SLOPE_DOWN
        else:
            slope = REGIME_SLOPE
    if slope == 0.0:
        return 1.0
    s = 1.0 + slope * delta
    if s < ALLOC_SCALE_FLOOR: return ALLOC_SCALE_FLOOR
    if s > ALLOC_SCALE_CEIL:  return ALLOC_SCALE_CEIL
    return s


def breadth_on_or_before(sorted_dates, bmap, d):
    idx = bisect.bisect_right(sorted_dates, d) - 1
    return bmap[sorted_dates[idx]] if idx >= 0 else None


def is_stressed(sorted_dates, bmap, d, count_map=None):
    """Stress switch for CALL TP/SL band selection.

    Default mode: breadth_score <= BREADTH_THRESHOLD on signal date.
    Experiment mode (STRESS_MODE='count'): same-side call count on date d
    <= STRESS_COUNT_THRESHOLD_CALL. count_map is {date: int}, supplied by
    caller. Falls back to breadth if count_map is None.
    """
    if STRESS_MODE == 'count' and count_map is not None:
        n = count_map.get(d)
        return n is not None and n <= STRESS_COUNT_THRESHOLD_CALL
    b = breadth_on_or_before(sorted_dates, bmap, d)
    return b is not None and b <= BREADTH_THRESHOLD


# --- Momentum-vs-score override hook (read-only experiment; INERT unless MOM_SCORE_PARQUET set) ---
MOM_SCORE_PARQUET = os.environ.get('MOM_SCORE_PARQUET', '').strip()
MOM_SCORE_COL     = os.environ.get('MOM_SCORE_COL', 'score_real').strip()
_MOM_SCORE_MAP = None   # {(symbol_str, date_obj): int}; built lazily once


def _load_mom_score_map():
    """Load the (symbol,date)->score override dict once (parent process)."""
    global _MOM_SCORE_MAP
    if _MOM_SCORE_MAP is not None or not MOM_SCORE_PARQUET:
        return _MOM_SCORE_MAP
    import polars as pl
    from datetime import date as _date
    df = pl.read_parquet(MOM_SCORE_PARQUET)
    df = df.filter(pl.col(MOM_SCORE_COL) >= OVERFLOW_THRESHOLD)  # only tradable+overflow tier matters
    m = {}
    for sym, ds, sc in zip(df['symbol'].to_list(), df['date'].to_list(), df[MOM_SCORE_COL].to_list()):
        d = _date.fromisoformat(ds) if isinstance(ds, str) else ds
        m[(sym, d)] = int(sc)
    _MOM_SCORE_MAP = m
    print(f"  [mom-override] {len(m):,} (symbol,date)->{MOM_SCORE_COL} overrides loaded", flush=True)
    return _MOM_SCORE_MAP


# --- Universe allow-list filter (read-only experiment; INERT unless MC_UNIVERSE_FILE set) ---
# Restricts which symbols' signals may enter the portfolio (both sides).
# Portfolio-stage restriction only -- scores/breadth/regime substrate untouched.
# Built for arm B of the P2.A survivorship decomposition
# (experiments/survivorship_decomposition/): clean substrate, survivor-only universe.
MC_UNIVERSE_FILE = os.environ.get('MC_UNIVERSE_FILE', '').strip()
_UNIVERSE_ALLOW = None   # frozenset of UPPER symbols; built lazily once


def _load_universe_allow():
    global _UNIVERSE_ALLOW
    if _UNIVERSE_ALLOW is not None or not MC_UNIVERSE_FILE:
        return _UNIVERSE_ALLOW
    with open(MC_UNIVERSE_FILE, 'r', encoding='utf-8') as f:
        _UNIVERSE_ALLOW = frozenset(
            ln.strip().upper() for ln in f
            if ln.strip() and not ln.strip().startswith('#'))
    print(f"  [universe-filter] {len(_UNIVERSE_ALLOW):,} symbols allow-listed "
          f"from {MC_UNIVERSE_FILE}", flush=True)
    return _UNIVERSE_ALLOW


def _apply_universe_filter(sigs, side):
    allow = _load_universe_allow()
    if allow is None:
        return sigs
    kept = [s for s in sigs if str(s.symbol_id).upper() in allow]
    print(f"  [universe-filter] {side}: kept {len(kept):,}/{len(sigs):,} signals",
          flush=True)
    return kept


def load_signals(version, d_start, d_end):
    """Call signals: overall >= OVERFLOW_THRESHOLD (70).

    Includes weight_info + stoch so portfolio-stage filters
    (WEAK_WEEKLY_CALL_DROP) can read wadj/stoch without a second DB pass.

    When CTSL_ENABLED=1, applies CTSL call-side transform to signal.overall
    in-place after load. This re-tiers CT-eligible signals score-stage
    instead of via cascade-stage CT_PROMOTE override.
    """
    _mom = _load_mom_score_map()
    if _mom is None:
        sigs = list(
            Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                         Score.weight_info, Score.stoch)
            .where(
                Score.version == version,
                Score.date >= d_start,
                Score.date <= d_end,
                Score.overall >= OVERFLOW_THRESHOLD,
            )
            .order_by(Score.date, Score.overall.desc())
        )
    else:
        # Override active: load the full universe (floor 0) CHUNKED BY YEAR to stay
        # under the client read_timeout, then keep only parquet rows + override overall.
        from datetime import date as _dd
        sigs = []
        for _yr in range(d_start.year, d_end.year + 1):
            _cs = d_start if _yr == d_start.year else _dd(_yr, 1, 1)
            _ce = d_end if _yr == d_end.year else _dd(_yr, 12, 31)
            for s in (Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                                   Score.weight_info, Score.stoch)
                      .where(Score.version == version, Score.date >= _cs,
                             Score.date <= _ce, Score.overall >= 0)
                      .order_by(Score.date, Score.overall.desc())):
                _sc = _mom.get((s.symbol_id, s.date))
                if _sc is None:
                    continue
                s.overall = int(_sc)
                sigs.append(s)
    return _apply_liquidity_random_drop_filter(_apply_liquidity_floor_filter(
        _apply_weekly_overflow_filter(_apply_ctsl_to_signals(
            _apply_universe_filter(sigs, 'call'), 'call'))))


def load_put_signals(version, d_start, d_end):
    """Put signals: overall <= PUT_THRESHOLD (25).

    When DESIGN_D_PUT_FLIP=1, broaden the pre-filter (load <= 35 + recover
    pre_regime via weight_info), apply the flipped put-side formula
    `adj = 50 + (pre_regime - 50) * mult` (no `2.0 - mult` mirror), and
    re-filter to <= PUT_THRESHOLD.
    """
    if not DESIGN_D_PUT_FLIP:
        # Always include weight_info + volume_signal so portfolio-stage filters
        # (WEAK_WEEKLY_PUT_DROP) can read w_adj/vsig without a second DB pass.
        sigs = list(
            Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                         Score.weight_info, Score.volume_signal)
            .where(
                Score.version == version,
                Score.date >= d_start,
                Score.date <= d_end,
                Score.overall <= PUT_THRESHOLD,
            )
            .order_by(Score.date, Score.overall.asc())
        )
        return _apply_ctsl_to_signals(_apply_universe_filter(sigs, 'put'), 'put')

    # Design D path — broaden filter, recover pre_regime, re-apply flipped, re-filter.
    import json as _json
    rows = list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend,
                     Score.regime_multiplier, Score.weight_info)
        .where(
            Score.version == version,
            Score.date >= d_start,
            Score.date <= d_end,
            Score.overall <= 40,  # broaden — D can lift some up out of <=25 and pull others down in
        )
    )
    flipped = []
    for s in rows:
        ov = int(s.overall)
        pre = None
        if s.weight_info:
            try:
                wi = _json.loads(s.weight_info) if isinstance(s.weight_info, str) else s.weight_info
                if 'pre_regime' in wi:
                    pre = int(wi['pre_regime'])
            except Exception:
                pre = None
        mult = float(s.regime_multiplier) if s.regime_multiplier is not None else 1.0
        if pre is None:
            # Recover pre_regime from overall via inverse of B_current
            if mult == 1.0:
                pre = ov
            else:
                if ov >= 50:
                    pre = round(50 + (ov - 50) / mult)
                else:
                    pre = round(50 + (ov - 50) / (2.0 - mult))
        pre = max(0, min(100, pre))
        if pre >= 50:
            continue   # call path; D doesn't apply here, and it's not a put anyway
        # Design D apply on put path: drop the (2.0 - mult) mirror
        if mult == 1.0:
            new_ov = pre
        else:
            new_ov = int(max(0, min(100, round(50 + (pre - 50) * mult))))
        if new_ov > PUT_THRESHOLD:
            continue
        # Mutate the score's overall in-place; keep symbol/date/trend
        s.overall = new_ov
        flipped.append(s)
    flipped.sort(key=lambda s: (s.date, s.overall))
    return _apply_universe_filter(flipped, 'put')


def _earnings_suppress_filter(signals, d_start, d_end, days, min_ov, max_ov, side):
    """Drop signals where any earnings_date falls in (signal_date, signal_date + days trading days].

    side: 'put' filters scores with overall in [min_ov, max_ov]; 'call' similar.
    Returns (kept, dropped_count).
    """
    syms = {s.symbol_id for s in signals}
    if not syms:
        return signals, 0
    ed_rows = list(EarningsDate.select(
            EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                   .where(EarningsDate.symbol.in_(list(syms)),
                          EarningsDate.date >= d_start - timedelta(days=10),
                          EarningsDate.date <= d_end + timedelta(days=days*2 + 7))
                   .order_by(EarningsDate.symbol, EarningsDate.date)
                   .tuples())
    from iv_crush_model import compute_effective_date
    ed_map = defaultdict(list)
    for sym, d, ct in ed_rows:
        ed_map[sym].append(compute_effective_date(d, ct))   # AMC shifted forward

    def _fwd_n(d, n):
        out = d
        while n > 0:
            out += timedelta(days=1)
            if _is_trading_day(out):
                n -= 1
        return out

    kept, dropped = [], 0
    for s in signals:
        ov = int(s.overall)
        # bucket gating
        if not (min_ov <= ov <= max_ov):
            kept.append(s); continue
        sym_ed = ed_map.get(s.symbol_id, [])
        if not sym_ed:
            kept.append(s); continue
        # earnings in (signal_date, signal_date + days trading days]?
        win_end = _fwd_n(s.date, days)
        if any(s.date < ed <= win_end for ed in sym_ed):
            dropped += 1
            continue
        kept.append(s)
    return kept, dropped


def load_price_history(sym_ids, d_start, d_end):
    ph_start = d_start - timedelta(days=120)
    ph_end   = d_end   + timedelta(days=30)
    rows = list(
        PriceHistory.select(
            PriceHistory.symbol, PriceHistory.date,
            PriceHistory.close, PriceHistory.high, PriceHistory.low,
            PriceHistory.open
        )
        .where(
            PriceHistory.symbol.in_(sym_ids),
            PriceHistory.date >= ph_start,
            PriceHistory.date <= ph_end,
        )
        .order_by(PriceHistory.symbol, PriceHistory.date)
        .tuples()
    )
    ph = defaultdict(list)
    for sym_id, d, c, h, l, o in rows:
        ph[sym_id].append((d, float(c), float(h), float(l), float(o)))
    return ph


def realized_vol(closes, base_idx, lookback=VOL_LOOKBACK):
    if base_idx < lookback:
        return None
    rets = []
    for j in range(base_idx - lookback + 1, base_idx + 1):
        prev = closes[j - 1]
        if prev > 0:
            rets.append((closes[j] - prev) / prev)
    if len(rets) < lookback // 2:
        return None
    mean = sum(rets) / len(rets)
    var  = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


# ---- Per-trade outcome (collision-aware) ------------------------------------

def compute_trade_outcome(sym_bars, signal_date, stressed, trail=False, symbol=None):
    """
    Returns dict with keys:
      kind     : 'tp' | 'sl' | 'hard' | 'both' | 'trail'
      exit_bar : int (trading bars from signal_date)
      side     : 'call'
      stressed : bool (for slippage band selection at resolve-time)
      premium_pct, vol, entry, signal_date
      fire_open, fire_close, fire_high, fire_low : OHLC of trigger bar (for
                 random-fill resolution in resolve()). For 'hard' kind, these
                 are the day-15 close (no underlying-move sampling).

    NOTE: trigger detection still uses σ-barriers on the underlying (cheap,
    JIT-compatible). The realized P&L on fire is computed in resolve() via
    `option_pricing.option_pnl_pct` with theta + vega applied at random fill,
    not the static SL/TP barrier values. This replaces the old static
    `realized_sl_pnl = base_sl_pct` assumption.
    """
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    opens  = [b[4] for b in sym_bars]

    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None

    # P1.5 NEXT_OPEN_ANCHOR (sensitivity-only, default off): price entry at
    # the NEXT trading day's open instead of the signal day's close. Hold
    # window / calendar deadline / exit_bar counting all stay anchored on
    # base_idx (signal date) unchanged -- only the fill-price reference
    # shifts, so slot/capital timing in run_single_sim is untouched. The
    # trigger scan (range(base_idx+1, end_idx), below) already starts at the
    # same bar used for entry here, so an intraday TP/SL check on that bar's
    # own high/low relative to its own open is coherent.
    if NEXT_OPEN_ANCHOR:
        if base_idx + 1 >= len(dates):
            return None
        entry_price = opens[base_idx + 1]
    else:
        entry_price = closes[base_idx]
    if entry_price <= 0:
        return None

    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    tp_sigma = TP_SIGMA_STRESS if stressed else TP_SIGMA_BASE
    sl_sigma = SL_SIGMA_STRESS if stressed else SL_SIGMA_BASE

    premium_pct = PREMIUM_MULT * vol / 100
    tp_level    = entry_price * (1 + tp_sigma * vol / 100)
    sl_level    = entry_price * (1 - sl_sigma * vol / 100)
    if CALENDAR_HOLD and NOMINAL_CAL_DTE != 30:
        # Honest DTE scaling: ATM premium ~ sqrt(DTE) (1.82x@30d, 1.29x@15d = x sqrt(0.5)).
        # A longer-dated option costs more (less leverage / smaller intrinsic per move)
        # and needs a proportionally bigger underlying move for the same %P&L, so the
        # sigma barriers scale identically. Theta total_dte is set to NOMINAL_CAL_DTE elsewhere.
        _ds = math.sqrt(NOMINAL_CAL_DTE / 30.0)
        premium_pct = PREMIUM_MULT * _ds * vol / 100
        tp_level    = entry_price * (1 + tp_sigma * _ds * vol / 100)
        sl_level    = entry_price * (1 - sl_sigma * _ds * vol / 100)
    # Gamma x IV Phase B: real-IV premium override (COST only; tp_level/sl_level
    # barriers above are computed from realized vol and are untouched by this).
    # rv=vol (Amendment 2026-07-12): the model path (IV_MODEL=1) needs the
    # engine's own realized vol; no-op when IV_MODEL is unset.
    _iv_dte = NOMINAL_CAL_DTE if CALENDAR_HOLD else 30
    premium_pct = _iv_premium_pct(symbol, signal_date, _iv_dte, premium_pct, rv=vol)

    if CALENDAR_HOLD:
        # Real-option calendar deadline: hold every trading bar whose date is
        # on/before signal_date + HOLD_CAL_DAYS calendar days (weekend-aware).
        _deadline = dates[base_idx] + timedelta(days=HOLD_CAL_DAYS)
        end_idx = base_idx + 1
        while end_idx < len(dates) and dates[end_idx] <= _deadline:
            end_idx += 1
    else:
        end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    kind = 'hard'; exit_bar = (end_idx - 1 - base_idx) if CALENDAR_HOLD else HOLD_DAYS
    fire_o = fire_c = fire_h = fire_l = None
    trail_fire_level = None
    do_trail = trail and TSL_ENABLED and TSL_SIGMA > 0
    trailing = False
    peak_h = None
    prem_stop = PREM_STOP_LOSS
    if prem_stop < 0.0:
        from option_pricing import option_pnl_pct as _opn
    for i in range(base_idx + 1, end_idx):
        tp_hit = highs[i] >= tp_level
        sl_hit = lows[i]  <= sl_level
        if not trailing:
            if tp_hit and sl_hit:
                kind, exit_bar = 'both', i - base_idx
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                break
            if tp_hit:
                if do_trail:
                    # Winner reached +TP: instead of selling, start trailing — ride the run.
                    trailing = True
                    peak_h = highs[i]
                    continue
                kind, exit_bar = 'tp', i - base_idx
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                break
            if sl_hit:
                kind, exit_bar = 'sl', i - base_idx
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                break
            # Design B premium stop — end-of-bar check on option pnl (vega=1.0
            # at trigger; sampled vega applied in resolve()).
            if prem_stop < 0.0:
                bars_held = i - base_idx
                cur_pnl = _opn('call', closes[i], entry_price, bars_held,
                               premium_pct, vega_ratio=1.0, delta=DELTA)
                if cur_pnl <= prem_stop:
                    kind, exit_bar = 'prem', bars_held
                    fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                    break
        else:
            # Trailing: update peak, exit when underlying gives back TSL_SIGMA*σ
            # from the peak. Floor the trail at the +TP barrier so a trailed
            # winner never realizes worse than a fixed-TP exit (locks the TP gain,
            # rides the upside). Forced exit -> pays SLIP_SL (taking liquidity).
            if highs[i] > peak_h:
                peak_h = highs[i]
            trail_level = peak_h * (1 - TSL_SIGMA * vol / 100)
            if trail_level < tp_level:
                trail_level = tp_level
            if lows[i] <= trail_level:
                kind, exit_bar = 'trail', i - base_idx
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                trail_fire_level = trail_level
                break
            if lows[i] <= sl_level:   # original SL backstop (rare while in profit)
                kind, exit_bar = 'sl', i - base_idx
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                break

    if kind == 'hard':
        last_idx = (end_idx - 1) if CALENDAR_HOLD else base_idx + HOLD_DAYS
        if last_idx >= len(dates):
            last_idx = len(dates) - 1
        fire_o = opens[last_idx]; fire_c = closes[last_idx]
        fire_h = highs[last_idx]; fire_l = lows[last_idx]

    out = dict(kind=kind, exit_bar=exit_bar, side='call', stressed=stressed,
               premium_pct=premium_pct, vol=vol, entry=entry_price,
               signal_date=signal_date, liq_tier=_liq_tier_for(symbol, signal_date),
               fire_open=fire_o, fire_close=fire_c,
               fire_high=fire_h, fire_low=fire_l,
               fire_tp_level=tp_level, fire_sl_level=sl_level,
               fire_trail_level=trail_fire_level)
    # Calendar days elapsed at the fire bar (calendar-honest theta in resolve()).
    _fire_idx = min(base_idx + exit_bar, len(dates) - 1)
    out['cal_held'] = (dates[_fire_idx] - dates[base_idx]).days

    # Pre-compute dead-hold outcome for SL fires. Walk forward from the fire
    # bar with vega=1.0 — vega-aware re-walk is deferred for now (see code
    # comment in resolve() on the approximation).
    if kind == 'sl' and DEAD_HOLD_ENABLED:
        sl_fire_idx = base_idx + exit_bar
        dh = _compute_dead_hold_call(
            highs, lows, closes, opens, sl_fire_idx, end_idx,
            entry_price, premium_pct, base_idx, dates=dates)
        out['dead_hold'] = dh
    return out


def _compute_dead_hold_call(highs, lows, closes, opens, fire_idx, end_idx,
                             entry, premium_pct, base_idx, dates=None):
    """Walk forward from fire_idx+1 looking for an intraday-high popout.
    Returns (kind_str, exit_bar, exit_pnl) where:
      kind_str: 'dh_pop' | 'dh_expiry' | 'dh_open'
      exit_bar: bars from base_idx
      exit_pnl: realized option pnl at exit (vega=1.0 unless VEGA_STATE=1)

    Theta clock honors CALENDAR_HOLD: calendar-days-elapsed + NOMINAL_CAL_DTE
    when set (and dates supplied), else trading-bars + DEFAULT_TOTAL_DTE
    (byte-identical to the prior bar-based behavior).

    VEGA_STATE (env-gated, default OFF; see the module-level block above
    _iv_premium_pct): when enabled, each walked bar's vega_ratio is
    _vega_state_ratio(vix at entry, vix at this bar) instead of the fixed
    1.0 -- a held long call gets vega credit if VIX rose since entry (the
    crash-vega-credit hypothesis), a haircut if it fell. Requires `dates`
    (always supplied by the one call site in compute_trade_outcome). Default
    OFF: vega_ratio stays 1.0 at every bar, bit-identical to prior behavior.
    """
    from option_pricing import option_pnl_pct, DEFAULT_TOTAL_DTE
    def _clk(k):
        if CALENDAR_HOLD and dates is not None:
            return (dates[k] - dates[base_idx]).days, NOMINAL_CAL_DTE
        return k - base_idx, DEFAULT_TOTAL_DTE
    _vs_active = VEGA_STATE and dates is not None
    _vix_entry = _load_vix_series().get(dates[base_idx]) if _vs_active else None
    def _vega_k(k):
        if not _vs_active:
            return 1.0
        return _vega_state_ratio(_vix_entry, _load_vix_series().get(dates[k]))
    for k in range(fire_idx + 1, end_idx):
        held_k, tdte_k = _clk(k)
        vega_k = _vega_k(k)
        high_k = highs[k]; open_k = opens[k]
        high_pnl = option_pnl_pct('call', high_k, entry, held_k,
                                  premium_pct, total_dte=tdte_k, vega_ratio=vega_k, delta=DELTA)
        if high_pnl >= DEAD_HOLD_POPOUT_PNL:
            open_pnl = option_pnl_pct('call', open_k, entry, held_k,
                                      premium_pct, total_dte=tdte_k, vega_ratio=vega_k, delta=DELTA)
            fill_pnl = max(DEAD_HOLD_POPOUT_PNL, open_pnl)
            return ('dh_pop', k - base_idx, fill_pnl)
    # No popout within hold window — exit at end-of-window close
    last_k = min(end_idx - 1, fire_idx + (end_idx - fire_idx - 1))
    if last_k <= fire_idx:
        return ('dh_open', fire_idx - base_idx, -1.0)
    held_last, tdte_last = _clk(last_k)
    vega_last = _vega_k(last_k)
    last_pnl = option_pnl_pct('call', closes[last_k], entry, held_last,
                              premium_pct, total_dte=tdte_last, vega_ratio=vega_last, delta=DELTA)
    return ('dh_expiry', last_k - base_idx, last_pnl)


def _compute_dead_hold_put(highs, lows, closes, opens, fire_idx, end_idx,
                            entry, premium_pct, base_idx):
    """Put-side dead-hold walk — checks intraday LOW for popout."""
    from option_pricing import option_pnl_pct
    for k in range(fire_idx + 1, end_idx):
        bars_k = k - base_idx
        low_k = lows[k]; open_k = opens[k]
        low_pnl = option_pnl_pct('put', low_k, entry, bars_k,
                                 premium_pct, vega_ratio=1.0, delta=DELTA)
        if low_pnl >= DEAD_HOLD_POPOUT_PNL:
            open_pnl = option_pnl_pct('put', open_k, entry, bars_k,
                                      premium_pct, vega_ratio=1.0, delta=DELTA)
            fill_pnl = max(DEAD_HOLD_POPOUT_PNL, open_pnl)
            return ('dh_pop', bars_k, fill_pnl)
    last_k = min(end_idx - 1, fire_idx + (end_idx - fire_idx - 1))
    if last_k <= fire_idx:
        return ('dh_open', fire_idx - base_idx, -1.0)
    bars_last = last_k - base_idx
    last_pnl = option_pnl_pct('put', closes[last_k], entry, bars_last,
                              premium_pct, vega_ratio=1.0, delta=DELTA)
    return ('dh_expiry', bars_last, last_pnl)


def _put_sl_hold_bars(signal_date):
    """Return number of trading bars to suppress SL check after entry.
    Monday entries get 4 bars (same calendar-day coverage as Tue-Fri 3-bar hold).
    """
    return PUT_SL_HOLD_BARS_MONDAY if signal_date.weekday() == 0 else PUT_SL_HOLD_BARS_DEFAULT


def compute_put_outcome(sym_bars, signal_date, put_stressed=False, symbol=None):
    """Put trade: win = underlying falls; stop = rises. Breadth-adaptive when put_stressed=True.

    Trigger uses σ-barriers on underlying high/low. Realized P&L is computed
    in resolve() via option_pricing.option_pnl_pct with theta + vega applied
    at random fill within the trigger bar (not static SL/TP barrier values).
    """
    dates  = [b[0] for b in sym_bars]
    closes = [b[1] for b in sym_bars]
    highs  = [b[2] for b in sym_bars]
    lows   = [b[3] for b in sym_bars]
    opens  = [b[4] for b in sym_bars]

    try:
        base_idx = dates.index(signal_date)
    except ValueError:
        return None
    # P1.5 NEXT_OPEN_ANCHOR (sensitivity-only, default off) -- see the call-side
    # comment in compute_trade_outcome for the full rationale; same pattern here.
    if NEXT_OPEN_ANCHOR:
        if base_idx + 1 >= len(dates):
            return None
        entry_price = opens[base_idx + 1]
    else:
        entry_price = closes[base_idx]
    if entry_price <= 0:
        return None
    vol = realized_vol(closes, base_idx)
    if vol is None or vol <= 0:
        return None

    if put_stressed and PUT_BREADTH_MODE != 'none':
        sl_pct = PUT_SL_STRESS
    else:
        sl_pct = PUT_SL
    tp_pct = PUT_TP if not (put_stressed and PUT_BREADTH_MODE != 'none') else PUT_TP_STRESS
    tp_sigma = tp_pct * PREMIUM_MULT / DELTA
    tp_level = entry_price * (1 - tp_sigma * vol / 100)

    # SL schedule: list of (max_bar_inclusive, sl_pct) — first whose max_bar
    # >= current_bar wins. Static mode = single (HOLD_DAYS, sl_pct) entry.
    if PUT_SL_MODE == 'stepped':
        sl_schedule = list(PUT_SL_STEPPED)
    else:
        sl_schedule = [(HOLD_DAYS, sl_pct)]

    def _sl_for_bar(b):
        for max_b, p in sl_schedule:
            if b <= max_b:
                return p
        return sl_schedule[-1][1]

    end_idx = min(len(dates), base_idx + 1 + HOLD_DAYS)
    if end_idx <= base_idx + 1:
        return None

    sl_hold = _put_sl_hold_bars(signal_date)
    premium_pct = PREMIUM_MULT * vol / 100
    # Gamma x IV Phase B: real-IV premium override (COST only; no cal-DTE scaling
    # on the put premium path today, so dte=30 matches the existing formula).
    # rv=vol (Amendment 2026-07-12): threaded for the IV_MODEL path.
    premium_pct = _iv_premium_pct(symbol, signal_date, 30, premium_pct, rv=vol)
    kind = 'hard'; exit_bar = HOLD_DAYS
    fire_o = fire_c = fire_h = fire_l = None
    fire_sl_level = None
    prem_stop = PREM_STOP_LOSS
    if prem_stop < 0.0:
        from option_pricing import option_pnl_pct as _opn
    for i in range(base_idx + 1, end_idx):
        bar    = i - base_idx          # 1-indexed
        sl_pct_t   = _sl_for_bar(bar)
        sl_sigma_t = abs(sl_pct_t) * PREMIUM_MULT / DELTA
        sl_level_t = entry_price * (1 + sl_sigma_t * vol / 100)
        tp_hit = lows[i]  <= tp_level
        sl_hit = (highs[i] >= sl_level_t) and (bar > sl_hold)
        if tp_hit and sl_hit:
            kind, exit_bar = 'both', bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        if tp_hit:
            kind, exit_bar = 'tp',   bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        if sl_hit:
            kind, exit_bar = 'sl',   bar
            fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
            fire_sl_level = sl_level_t
            break
        # Design B premium stop (puts) — end-of-bar; suppressed during sl_hold.
        if prem_stop < 0.0 and bar > sl_hold:
            cur_pnl = _opn('put', closes[i], entry_price, bar,
                           premium_pct, vega_ratio=1.0, delta=DELTA)
            if cur_pnl <= prem_stop:
                kind, exit_bar = 'prem', bar
                fire_o, fire_c, fire_h, fire_l = opens[i], closes[i], highs[i], lows[i]
                fire_sl_level = sl_level_t
                break

    if kind == 'hard':
        last_idx = base_idx + HOLD_DAYS
        if last_idx >= len(dates):
            last_idx = len(dates) - 1
        fire_o = opens[last_idx]; fire_c = closes[last_idx]
        fire_h = highs[last_idx]; fire_l = lows[last_idx]
        # Last bar's SL level (used only for diagnostic — hard fires don't sample)
        last_bar_idx = last_idx - base_idx
        fire_sl_level = entry_price * (1 + abs(_sl_for_bar(last_bar_idx)) * PREMIUM_MULT / DELTA * vol / 100)

    out = dict(kind=kind, exit_bar=exit_bar, side='put', stressed=put_stressed,
               premium_pct=premium_pct, vol=vol, entry=entry_price,
               signal_date=signal_date, liq_tier=_liq_tier_for(symbol, signal_date),
               fire_open=fire_o, fire_close=fire_c,
               fire_high=fire_h, fire_low=fire_l,
               fire_tp_level=tp_level, fire_sl_level=fire_sl_level)

    # Pre-compute dead-hold outcome for SL fires.
    if kind == 'sl' and DEAD_HOLD_ENABLED:
        sl_fire_idx = base_idx + exit_bar
        out['dead_hold'] = _compute_dead_hold_put(
            highs, lows, closes, opens, sl_fire_idx, end_idx,
            entry_price, premium_pct, base_idx)
    return out


def _is_put_stressed(sorted_dates, bmap, d):
    """Returns True if the put-breadth switch should fire on date d."""
    if PUT_BREADTH_MODE == 'none':
        return False
    b = breadth_on_or_before(sorted_dates, bmap, d)
    if b is None:
        return False
    if PUT_BREADTH_MODE == 'invert':
        return b >= PUT_BREADTH_THRESHOLD    # strong breadth = headwind for puts
    if PUT_BREADTH_MODE == 'same':
        return b <= PUT_BREADTH_THRESHOLD    # same logic as calls
    return False


def _attach_earnings_span(outcome, ern_for_sym, trading_days):
    """Set outcome['spans_earnings']=True if any earnings event in
    (signal_date, exit_date]. Used by resolve() to decide whether to sample
    a vega ratio at fire time.
    """
    outcome['spans_earnings'] = False
    if not ern_for_sym or not trading_days:
        return outcome
    try:
        from iv_crush_model import estimate_exit_date, find_spanning_earnings
    except ImportError:
        return outcome
    exit_d = estimate_exit_date(outcome['signal_date'],
                                outcome.get('exit_bar', HOLD_DAYS),
                                trading_days)
    spans = find_spanning_earnings(outcome['signal_date'], exit_d, ern_for_sym)
    outcome['spans_earnings'] = spans is not None
    return outcome


def precompute_outcomes(signals, ph, breadth_dates, breadth_map,
                        ern_map=None, trading_days=None, count_map=None):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        stressed = is_stressed(breadth_dates, breadth_map, sig.date, count_map=count_map)
        sig_trail = TSL_ENABLED and (sig.overall >= TSL_MIN_SCORE)
        r = compute_trade_outcome(sym_bars, sig.date, stressed, trail=sig_trail,
                                  symbol=sig.symbol_id)
        if r is None:
            continue
        ern_for_sym = (ern_map or {}).get(sig.symbol_id, [])
        _attach_earnings_span(r, ern_for_sym, trading_days)
        outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


def precompute_put_outcomes(signals, ph, breadth_dates=None, breadth_map=None,
                            ern_map=None, trading_days=None):
    outcomes = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        put_stressed = False
        if PUT_BREADTH_MODE != 'none' and breadth_dates is not None and breadth_map is not None:
            put_stressed = _is_put_stressed(breadth_dates, breadth_map, sig.date)
        r = compute_put_outcome(sym_bars, sig.date, put_stressed=put_stressed,
                                symbol=sig.symbol_id)
        if r is None:
            continue
        ern_for_sym = (ern_map or {}).get(sig.symbol_id, [])
        _attach_earnings_span(r, ern_for_sym, trading_days)
        outcomes[(sig.symbol_id, sig.date)] = r
    return outcomes


# Module-level guard for one-time vega sample pool load
_VEGA_INIT = False

def _ensure_vega_pool_loaded():
    global _VEGA_INIT
    if _VEGA_INIT:
        return
    try:
        from iv_crush_model import _load_samples
        _load_samples()
    except Exception:
        pass
    _VEGA_INIT = True


def resolve(outcome, rng):
    """Return ('tp' | 'sl' | 'hard' | 'both', net_option_pnl).

    Random-fill semantics, bounded by which barrier actually triggered (so
    a TP-only fire cannot resolve below TP, and an SL-only fire cannot
    resolve above SL — matching real limit/stop order semantics):

      kind='tp', call : fill ~ Uniform(tp_level, high)         limit-or-better
      kind='tp', put  : fill ~ Uniform(low, tp_level)          limit-or-better
      kind='sl', call : conditional bimodal — see below        stop-or-gap
      kind='sl', put  : conditional bimodal — see below        stop-or-gap
      kind='both'     : fill ~ Uniform(low, high)              path ambiguous
      kind='hard'     : fill = day-15 close                    deterministic

    SL bimodal fill (calibrated 2026-04-30 from sl_fill_bias_audit on
    124,872 real bars; mean(pos)=0.698, median=0.992 confirmed bimodal):

      Calls (sl_level < entry):
        intraday trigger (open > sl_level): fill = sl_level deterministically
            — opened above barrier, dropped to it; stop fills at the barrier.
        gap-through (open <= sl_level): fill ~ Uniform(low, open)
            — gapped below SL at open; stop fills somewhere in the gap region.

      Puts (sl_level > entry):
        intraday trigger (open < sl_level): fill = sl_level deterministically
            — opened below barrier, rose to it; stop fills at the barrier.
        gap-through (open >= sl_level): fill ~ Uniform(open, high)
            — gapped above SL at open; stop fills somewhere in the gap region.

    Replaces uniform [low, sl_level] / [sl_level, high] which over-estimated
    SL realized loss by ~5-15% of premium per fire (the audit measured a
    +0.198 bias of mean fill position vs uniform 0.5).

    Realized option P&L is computed via option_pricing.option_pnl_pct with
    delta + theta + (sampled empirical vega when spans-earnings).

    Slippage:
      - tp/both-favorable: SLIP_TP (~0, limit fill)
      - sl/both-adverse:   SLIP_SL (~-1.3%, stop fill)
      - hard:              SLIP_HARD (~-0.5%)

    LIQ_PENALTY (FF3_STAGEB_PREREG.md, default OFF): when outcome['liq_tier']
    is not None, the SLIP_ENTRY/SLIP_SL/SLIP_HARD above are REPLACED (not
    stacked) by tier-keyed half-spread values for this trade, and SL exits pay
    an extra deterministic fill-reality addend. SLIP_TP is never touched. See
    the LIQ_PENALTY_ENABLED block comment near LIQUIDITY_FLOOR for the full
    two-component design. tier=None (disabled/no coverage) -> byte-identical
    to the paragraph above.
    """
    if outcome.get('_dte') == '15':
        return _mc15.resolve(outcome, rng)

    from option_pricing import option_pnl_pct, sample_vega_ratio, DEFAULT_TOTAL_DTE

    # LIQ_PENALTY effective per-leg slip for THIS trade (module globals when
    # tier is None -- disabled, pre-coverage, or no map hit -- so every use
    # below is byte-identical to pre-hook behavior in the OFF arm; G4 proof).
    _liq_tier_ix = outcome.get('liq_tier')
    if _liq_tier_ix is not None:
        _liq_half = _LIQ_SPREAD_BY_TIER[_liq_tier_ix] / 2.0
        _slip_entry, _slip_sl, _slip_hard = -_liq_half, -_liq_half, -_liq_half
    else:
        _slip_entry, _slip_sl, _slip_hard = SLIP_ENTRY, SLIP_SL, SLIP_HARD

    kind = outcome['kind']
    side = outcome.get('side', 'call')
    spans = outcome.get('spans_earnings', False)
    bars_held = outcome.get('exit_bar', HOLD_DAYS)
    if CALENDAR_HOLD:
        _held = outcome.get('cal_held', bars_held); _tdte = NOMINAL_CAL_DTE
    else:
        _held = bars_held; _tdte = DEFAULT_TOTAL_DTE
    entry = outcome['entry']
    premium_pct = outcome['premium_pct']

    vega_ratio = 1.0
    if spans:
        _ensure_vega_pool_loaded()
        side_name = 'CALL' if side == 'call' else 'PUT'
        vega_ratio = sample_vega_ratio(side_name, dte=DEFAULT_TOTAL_DTE, rng=rng)

    # Hard exit: forced day-15 close, deterministic.
    if kind == 'hard':
        u_fire = outcome['fire_close']
        pnl = option_pnl_pct(side, u_fire, entry, _held, premium_pct,
                             total_dte=_tdte, vega_ratio=vega_ratio)
        return 'hard', pnl + _slip_entry + _slip_hard

    # Design B premium-based stop — fire at close (deterministic), apply
    # sampled vega for spans-earnings consistency with σ-fires.
    if kind == 'prem':
        u_fire = outcome['fire_close']
        pnl = option_pnl_pct(side, u_fire, entry, _held, premium_pct,
                             total_dte=_tdte, vega_ratio=vega_ratio)
        return 'prem', pnl + _slip_entry + _slip_sl

    lo = outcome['fire_low']
    hi = outcome['fire_high']
    op = outcome.get('fire_open')
    tp_lvl = outcome.get('fire_tp_level')
    sl_lvl = outcome.get('fire_sl_level')

    # Pick fill range bounded by trigger semantics
    if kind == 'tp':
        # P1.5 execution-pessimism cert: TP touch fails to fill (thin
        # resting-limit queue priority) -- env-gated, default 0.0. Guarded by
        # `TP_FILL_MISS_P > 0.0 and ...` so rng.random() is never drawn at the
        # default, preserving the exact random-stream sequence (bit-identical).
        _tp_missed = TP_FILL_MISS_P > 0.0 and rng.random() < TP_FILL_MISS_P
        if _tp_missed:
            # Missed fill: unprotected price anywhere in the bar's full range
            # (not the limit-or-better band), forced-exit half-spread instead
            # of the limit-fill slip.
            u_lo, u_hi = lo, hi
            slip = _slip_sl
        elif TP_FILL_GAP_AWARE:
            # Probe arm 2 (the honest middle): a resting limit fills AT the
            # limit on an intraday touch, but a gap-through OPEN fills at the
            # open (real price improvement a limit order does receive). No
            # credit for post-open overshoot to the high. Burn one draw for
            # stream alignment with the default arm.
            rng.random()
            if side == 'call':
                u_lo = u_hi = op if (op is not None and op > tp_lvl) else tp_lvl
            else:
                u_lo = u_hi = op if (op is not None and op < tp_lvl) else tp_lvl
            slip = SLIP_TP
        elif TP_FILL_AT_BARRIER:
            # Probe arm: a resting limit fills AT the limit price — no credit
            # for the bar's later overshoot. Burn one draw so the downstream
            # random stream stays aligned with the default arm (which consumes
            # one draw sampling the fill range).
            rng.random()
            u_lo = u_hi = tp_lvl
            slip = SLIP_TP
        elif side == 'call':
            u_lo, u_hi = tp_lvl, hi    # at or above TP barrier (limit-or-better)
            slip = SLIP_TP
        else:                          # put
            u_lo, u_hi = lo, tp_lvl    # at or below TP barrier
            slip = SLIP_TP
    elif kind == 'sl':
        # Bimodal: intraday trigger fills at barrier; gap-through samples gap.
        if side == 'call':
            # call SL: sl_level < entry; intraday trigger when open > sl_level
            if op is not None and op > sl_lvl:
                u_lo = u_hi = sl_lvl                 # intraday — fill at barrier
            else:
                gap_open = op if op is not None else sl_lvl
                u_lo, u_hi = lo, gap_open            # gap-through — Uniform(low, open)
        else:                          # put
            # put SL: sl_level > entry; intraday trigger when open < sl_level
            if op is not None and op < sl_lvl:
                u_lo = u_hi = sl_lvl                 # intraday — fill at barrier
            else:
                gap_open = op if op is not None else sl_lvl
                u_lo, u_hi = gap_open, hi            # gap-through — Uniform(open, high)
        slip = _slip_sl
    elif kind == 'trail':
        # Trailing-stop exit on a call winner: underlying gave back to the trail
        # level from its peak (trail_lvl >= +TP barrier, so P&L is positive).
        # Forced exit (stop) -> fill at-or-worse than trail level, pay half-spread.
        trail_lvl = outcome.get('fire_trail_level') or tp_lvl
        if op is not None and op > trail_lvl:
            u_lo = u_hi = trail_lvl            # intraday — fill at the trail stop
        else:
            gap_open = op if op is not None else trail_lvl
            u_lo, u_hi = lo, gap_open          # gap-through — Uniform(low, open)
        slip = _slip_sl
    else:                              # 'both' — path ambiguous
        u_lo, u_hi = lo, hi
        slip = None                    # set after computing P&L sign

    # Defensive: ensure ordering even with floating-point edge cases
    if u_hi < u_lo:
        u_lo, u_hi = u_hi, u_lo
    u_fire = u_lo + rng.random() * (u_hi - u_lo) if u_hi > u_lo else u_lo

    pnl = option_pnl_pct(side, u_fire, entry, _held, premium_pct,
                         total_dte=_tdte, vega_ratio=vega_ratio)

    # Dead-hold override: if this is an SL fire and the realized pnl is too
    # deep to actually liquidate (no liquid bid on a deep-OTM option), use the
    # pre-computed popout-or-expiry walk instead of recycling capital here.
    # Approximation: dead-hold walk uses vega=1.0 even when sampled vega < 1.0
    # for earnings-spanning trades — vega-aware re-walk per-iter is deferred.
    # The mechanism is primarily a measurement-realism fix for non-earnings
    # collapses (e.g., DeepSeek-shock APH/PWR), where vega=1.0 is correct.
    if (DEAD_HOLD_ENABLED and kind == 'sl'
            and pnl <= DEAD_HOLD_TRIGGER_PNL
            and 'dead_hold' in outcome):
        dh_kind, dh_bars, dh_pnl = outcome['dead_hold']
        # Asymmetric cost: 'dh_pop' recovers to the popout target via a resting
        # limit (liquidity provider, no spread crossed); 'dh_expiry'/'dh_open'
        # are FORCED day-15 closes (liquidity taker) and pay the hard-sell spread.
        dh_exit_slip = DH_POP_SLIP if dh_kind == 'dh_pop' else _slip_hard
        return dh_kind, dh_pnl + _slip_entry + dh_exit_slip

    if slip is None:
        slip = SLIP_TP if pnl >= 0 else _slip_sl
    # LIQ_PENALTY component (ii), literal kind=='sl' only (SIMPLE DETERMINISTIC
    # form -- see block comment near LIQUIDITY_FLOOR): every penalized SL exit
    # pays the tier's expected fill-reality overshoot; no extra rng draw, so
    # the paired-seed stream is untouched. 'hard'/'prem'/'trail'/'both'/dead-
    # hold-derived kinds are intentionally NOT extended (out of the brief's
    # literal scope).
    _liq_extra = 0.0
    if kind == 'sl' and _liq_tier_ix is not None:
        _liq_extra = -_LIQ_GAP_OVERSHOOT_BY_TIER[_liq_tier_ix] * LIQ_GAP_INCIDENCE
    return kind, pnl + _slip_entry + slip + _liq_extra


# ---- Portfolio simulation ---------------------------------------------------

class Position:
    __slots__ = ['sym_id', 'entry_date', 'entry_idx', 'exit_bar', 'premium_cost', 'option_pnl',
                 'outcome', 'side', 'score', 'tier', 'ct', 'ern',
                 'entry_value', 'entry_peak', 'entry_dd',
                 'entry_open_calls', 'entry_open_puts',
                 'entry_underlying', 'premium_pct']

    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl, outcome, side,
                 score=None, tier=None, ct=None, ern=None,
                 entry_value=None, entry_peak=None, entry_dd=None,
                 entry_open_calls=None, entry_open_puts=None,
                 entry_idx=None, entry_underlying=None, premium_pct=None):
        self.sym_id       = sym_id
        self.entry_date   = entry_date
        self.entry_idx    = entry_idx       # for bars_held / mark-to-model under realloc
        self.exit_bar     = exit_bar
        self.premium_cost = premium_cost
        self.option_pnl   = option_pnl
        self.outcome      = outcome
        self.side         = side  # 'call' or 'put'
        # Tape metadata (None unless MC_TRADE_TAPE collection is active)
        self.score            = score
        self.tier             = tier
        self.ct               = ct
        self.ern              = ern
        self.entry_value      = entry_value
        self.entry_peak       = entry_peak
        self.entry_dd         = entry_dd
        self.entry_open_calls = entry_open_calls
        self.entry_open_puts  = entry_open_puts
        # Realloc mark-to-model fields (always populated)
        self.entry_underlying = entry_underlying
        self.premium_pct      = premium_pct


# DD-ledger trade-tape collection (env-flagged; off by default).
# When MC_TRADE_TAPE=1, run_single_sim records:
#   - one row per closed trade with entry context + outcome + dollar PnL
#   - one row per seed with worst-DD episode bounds (start_date, end_date, magnitude)
# Output is collected in run_window and written to .cache/dd_ledger/tape_{label}.parquet
TRADE_TAPE_ENABLED = os.environ.get('MC_TRADE_TAPE', '0') == '1'


def run_single_sim(trading_days, calls_by_date, call_outcomes,
                   puts_by_date, put_outcomes, rng,
                   regime_dates=None, regime_map=None,
                   vix_dates=None, vix_map=None, mcc_dates=None, mcc_map=None,
                   trin_dates=None, trin_map=None, whist_dates=None, whist_map=None,
                   bdiv_dates=None, bdiv_map=None,
                   breadth_dates=None, breadth_map=None,
                   collect_tape=False, close_by_sym_idx=None):
    cash       = STARTING_CASH
    positions  = []
    peak_value = STARTING_CASH
    max_dd     = 0.0
    t1m_bar    = None  # first bar idx where equity >= $1M (days-to-1M survival metric)
    # time-to-2x instrumentation (additive; surfaced only via MC_RETURN_PATHS).
    # t2x_bar      = first bar idx where equity >= 2 x STARTING_CASH (the 2x milestone)
    # t_50dd_bar   = first bar idx where running drawdown-from-peak >= 0.50
    # These two together answer "did 2x happen before a 50% drawdown" path-wise.
    t2x_bar    = None
    t_50dd_bar = None
    _two_x_target = STARTING_CASH * 2.0

    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c  = sl_c  = hard_c  = 0
    tp_p  = sl_p  = hard_p  = 0

    # Trade-tape collection (per-trade rows + DD-episode bounds)
    trade_rows = []  if collect_tape else None
    # Daily portfolio path — needed post-walk to identify worst DD episode bounds
    daily_path = []  if collect_tape else None
    # Track current peak-to-trough episode in-line; flush when a new peak is set
    cur_episode_start_idx = 0  if collect_tape else None
    cur_episode_trough_idx = 0  if collect_tape else None
    cur_episode_trough_val = STARTING_CASH  if collect_tape else None
    episodes = []  if collect_tape else None  # list of (start_idx, trough_idx, peak_val, trough_val)

    cap_call = MAX_POSITIONS_CALL if MAX_POSITIONS_CALL is not None else MAX_POSITIONS
    cap_put  = MAX_POSITIONS_PUT  if MAX_POSITIONS_PUT  is not None else MAX_POSITIONS
    side_capped = (MAX_POSITIONS_CALL is not None) or (MAX_POSITIONS_PUT is not None)

    # Reallocation per-day counter (reset each daily iteration). Hoisted here
    # so that _try_realloc's `nonlocal today_realloc_count` binding resolves.
    today_realloc_count = 0
    open_premium_pcts = []
    open_premium_base_pcts = []
    call_open_premium_base_pcts = []
    put_open_premium_base_pcts = []
    sat_scales = []
    cash_reserves = []
    reserved_cash = 0.0
    reserve_pct_sum = 0.0
    reserve_pct_n = 0
    max_reserved_cash_pct = 0.0

    # Track A — equity-curve capture (MC_EMIT_CURVE) + equity-native scaler (AW equity mode).
    # Both default-OFF: eq_series stays empty and the scaler is never built unless the
    # respective env knob is set, so baseline runs are byte-identical.
    _emit_curve = MC_EMIT_CURVE
    eq_series = [] if _emit_curve else None
    _aw_on = AW_ENABLED
    _aw_equity_mode = _aw_on and AW_MODE == 'equity'
    _aw_market_mode = _aw_on and AW_MODE == 'market'
    _eq_scaler = None
    _eq_prev_weekly = None     # prior weekly equity value (for weekly_log_ret)
    aw_scale_eq = 1.0          # persists between weekly boundaries in equity mode
    if _aw_equity_mode:
        _w = _aw_wave()
        if _w:
            _eq_scaler = _w.EquityNativeScaler()
        else:
            _aw_equity_mode = False   # module missing -> stay at 1.0 baseline

    for day_idx, today in enumerate(trading_days):
        keep = []
        for p in positions:
            base = day_to_idx.get(p.entry_date, -999)
            if base + p.exit_bar <= day_idx:
                cash += p.premium_cost * (1 + p.option_pnl)
                if p.side == 'call':
                    if   p.outcome == 'tp':   tp_c   += 1
                    elif p.outcome == 'sl':   sl_c   += 1
                    else:                     hard_c += 1
                else:
                    if   p.outcome == 'tp':   tp_p   += 1
                    elif p.outcome == 'sl':   sl_p   += 1
                    else:                     hard_p += 1
                if collect_tape:
                    trade_rows.append((
                        p.sym_id, p.entry_date, today, p.side, p.score, p.tier,
                        p.ct, p.ern, p.premium_cost, p.option_pnl, p.outcome,
                        p.entry_value, p.entry_peak, p.entry_dd,
                        p.entry_open_calls, p.entry_open_puts,
                    ))
            else:
                keep.append(p)
        positions = keep
        if cash_reserves:
            cash_reserves = [
                (release_idx, amount)
                for release_idx, amount in cash_reserves
                if release_idx > day_idx and amount > 0
            ]
        reserved_cash = sum(amount for _, amount in cash_reserves)

        portfolio_value = cash + sum(p.premium_cost for p in positions)
        if t1m_bar is None and portfolio_value >= 1_000_000.0:
            t1m_bar = day_idx
        if t2x_bar is None and portfolio_value >= _two_x_target:
            t2x_bar = day_idx
        if portfolio_value > peak_value:
            # Flush completed episode (if any non-trivial drawdown occurred)
            if collect_tape and cur_episode_trough_val < peak_value:
                episodes.append((cur_episode_start_idx, cur_episode_trough_idx,
                                 peak_value, cur_episode_trough_val))
            peak_value = portfolio_value
            if collect_tape:
                cur_episode_start_idx  = day_idx
                cur_episode_trough_idx = day_idx
                cur_episode_trough_val = portfolio_value
        else:
            if collect_tape and portfolio_value < cur_episode_trough_val:
                cur_episode_trough_idx = day_idx
                cur_episode_trough_val = portfolio_value
        dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
        if dd > max_dd:
            max_dd = dd
        if t_50dd_bar is None and dd >= 0.50:
            t_50dd_bar = day_idx
        if collect_tape:
            daily_path.append((today, portfolio_value, peak_value, dd))
        # Track A — capture the equity bar (MC_EMIT_CURVE) and advance the
        # equity-native scaler on weekly (Fri-close) boundaries (AW equity mode).
        # Both no-ops when their env knob is off.
        if _emit_curve:
            eq_series.append(portfolio_value)
        if _aw_equity_mode and _eq_scaler is not None and (day_idx % 5 == 4):
            if _eq_prev_weekly is not None and _eq_prev_weekly > 0 and portfolio_value > 0:
                import math as _math
                wlr = _math.log(portfolio_value / _eq_prev_weekly)
            else:
                wlr = 0.0
            aw_scale_eq = _eq_scaler.scale(wlr, dd)
            _eq_prev_weekly = portfolio_value
        if portfolio_value <= STARTING_CASH * COLLAPSE_THRESHOLD:
            break

        open_syms = {p.sym_id for p in positions}
        call_pressure = sum(
            1 for sid, _sc, key, _ct, _ern in calls_by_date.get(today, [])
            if key in call_outcomes and sid not in open_syms
        )
        put_pressure = sum(
            1 for sid, _sc, key, _ct in puts_by_date.get(today, [])
            if key in put_outcomes and sid not in open_syms
        )

        call_open = sum(1 for p in positions if p.side == 'call')
        put_open  = sum(1 for p in positions if p.side == 'put')

        reg_mult = regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
        reg_scale_c = alloc_scale_for(reg_mult, is_put=False)
        reg_scale_p = alloc_scale_for(reg_mult, is_put=True)
        rxdd_vix_today = _dte_router_value_on_or_before(vix_dates, vix_map, today) if vix_dates else None
        mwdd_mcc_today = _dte_router_value_on_or_before(mcc_dates, mcc_map, today) if mcc_dates else None
        tvdd_trin_today = _dte_router_value_on_or_before(trin_dates, trin_map, today) if trin_dates else None
        vxmd_whist_today = _dte_router_value_on_or_before(whist_dates, whist_map, today) if whist_dates else None
        bdiv_today = _dte_router_value_on_or_before(bdiv_dates, bdiv_map, today) if bdiv_dates else None

        # Track A — per-day aggression-wave multiplier (calls-only; puts off under
        # Apex). DEFAULT-OFF => aw_scale stays 1.0 => alloc_frac byte-identical.
        #   market mode: date-level regime-strength index from the per-day maps
        #     (semivol is symbol-specific, contributes neutral 0.5 at date level).
        #   equity mode: aw_scale_eq, advanced on weekly boundaries above.
        aw_scale = 1.0
        if _aw_market_mode:
            brd_today = (breadth_on_or_before(breadth_dates, breadth_map, today)
                         if breadth_dates else None)
            aw_scale = _aw_market_scale(rxdd_vix_today, brd_today, mwdd_mcc_today,
                                        tvdd_trin_today, bdiv_today)
        elif _aw_equity_mode:
            aw_scale = aw_scale_eq

        def _allocation_base(value):
            if PRACTICAL_CAPITAL_CEILING > 0:
                return min(value, PRACTICAL_CAPITAL_CEILING)
            return value

        def _premium_cap_remaining(side, base_value):
            # EXR lifts the gross/call cap while the book is small & VIX calm
            # (no-op => base cap when EXR disabled). base_value == equity for the
            # uncapped Apex base; rxdd_vix_today is the on-or-before VIX.
            g_cap = _exr_eff_cap(GROSS_PREMIUM_CAP, base_value, rxdd_vix_today, dd)
            c_cap = _exr_eff_cap(CALL_PREMIUM_CAP, base_value, rxdd_vix_today, dd)
            active = g_cap > 0 or c_cap > 0 or PUT_PREMIUM_CAP > 0
            if not active:
                return None
            total_open = sum(p.premium_cost for p in positions)
            remaining = float('inf')
            if g_cap > 0:
                remaining = min(remaining, base_value * g_cap - total_open)
            if side == 'call' and c_cap > 0:
                side_open = sum(p.premium_cost for p in positions if p.side == 'call')
                remaining = min(remaining, base_value * c_cap - side_open)
            if side == 'put' and PUT_PREMIUM_CAP > 0:
                side_open = sum(p.premium_cost for p in positions if p.side == 'put')
                remaining = min(remaining, base_value * PUT_PREMIUM_CAP - side_open)
            return max(0.0, remaining)

        def _opportunity_saturation_scale(side):
            ref = OPP_SAT_CALL_REF if side == 'call' else OPP_SAT_PUT_REF
            pressure = call_pressure if side == 'call' else put_pressure
            if ref <= 0 or pressure <= ref:
                return 1.0
            scale = (ref / max(1.0, float(pressure))) ** max(0.0, OPP_SAT_POWER)
            return max(OPP_SAT_FLOOR, min(1.0, scale))

        def _mark_position(p):
            """Mark-to-model: estimate option pnl% at today's close given delta+theta
            (vega=1.0 — ignored for realloc decision since the same expected vega
            applies to the fill that would replace it). Returns pnl fraction or
            None if no close data available for the symbol on day_idx."""
            if p.entry_underlying is None or p.premium_pct is None or p.entry_idx is None:
                return None
            cu = close_by_sym_idx.get((p.sym_id, day_idx)) if close_by_sym_idx else None
            if cu is None:
                return None
            bars = day_idx - p.entry_idx
            if bars < 0:
                return None
            return _opn_for_mark(p.side, cu, p.entry_underlying, bars,
                                 p.premium_pct, total_dte=HOLD_DAYS,
                                 vega_ratio=1.0, delta=DELTA)

        def _try_realloc(side, new_score):
            """Attempt to displace a same-side held position to free a slot.
            Returns True if a position was closed."""
            nonlocal cash, call_open, put_open, today_realloc_count
            if not REALLOC_STRATEGY:
                return False
            if today_realloc_count >= REALLOC_MAX_PER_DAY:
                return False
            # CDR regime gates (collapse-dodge): never cut-to-redeploy in panic /
            # deep-DD tape — that is exactly where dead-held bags recover (V-shape)
            # AND where the fresh signal is a falling knife. Default-OFF no-ops.
            if (REALLOC_MAX_VIX < 900 and rxdd_vix_today is not None
                    and rxdd_vix_today >= REALLOC_MAX_VIX):
                return False
            if REALLOC_MAX_DD < 1.0 and dd >= REALLOC_MAX_DD:
                return False
            cands = [p for p in positions if p.side == side]
            if not cands:
                return False
            cands = [p for p in cands if (day_idx - (p.entry_idx or 0)) >= REALLOC_MIN_HOLD_BARS]
            if not cands:
                return False
            target = None
            strat = REALLOC_STRATEGY
            if strat == 'entry_score_low':
                if side == 'call':
                    target = min(cands, key=lambda p: (p.score if p.score is not None else 9999))
                else:
                    target = max(cands, key=lambda p: (p.score if p.score is not None else -9999))
            elif strat in ('current_pnl_high', 'current_pnl_low'):
                marks = [(p, _mark_position(p)) for p in cands]
                marks = [(p, m) for p, m in marks if m is not None]
                if not marks:
                    return False
                if strat == 'current_pnl_high':
                    target = max(marks, key=lambda x: x[1])[0]
                else:
                    target = min(marks, key=lambda x: x[1])[0]
            elif strat == 'days_held_high':
                target = max(cands, key=lambda p: day_idx - (p.entry_idx or 0))
            elif strat == 'pnl_high_or_score_low':
                marks = [(p, _mark_position(p)) for p in cands]
                gainers = [(p, m) for p, m in marks if m is not None and m >= 0.20]
                if gainers:
                    target = max(gainers, key=lambda x: x[1])[0]
                else:
                    if side == 'call':
                        target = min(cands, key=lambda p: (p.score if p.score is not None else 9999))
                    else:
                        target = max(cands, key=lambda p: (p.score if p.score is not None else -9999))
            else:
                return False
            if target is None:
                return False
            if side == 'call':
                advantage = new_score - (target.score if target.score is not None else 0)
            else:
                advantage = (target.score if target.score is not None else 100) - new_score
            if advantage < REALLOC_MIN_ADVANTAGE:
                return False
            # CDR low-conviction gate: only displace the expirer cohort (low entry
            # score). High-score bags recover most (mine: top/ultra pop 0.57-0.61).
            if (REALLOC_MAX_TARGET_SCORE < 900 and target.score is not None
                    and target.score > REALLOC_MAX_TARGET_SCORE):
                return False
            mark_pnl = _mark_position(target)
            if mark_pnl is None:
                return False
            # CDR deep-loss gate: only cut bags that are genuinely deep underwater
            # (the user's "down 60%"), not mildly-down positions that recover to TP.
            if REALLOC_MIN_LOSS > 0.0 and mark_pnl > -REALLOC_MIN_LOSS:
                return False
            # Asymmetric-cost canon: cutting is a FORCED exit -> pay the half-spread.
            cash += target.premium_cost * (1.0 + mark_pnl - REALLOC_CUT_SLIP)
            positions.remove(target)
            open_syms.discard(target.sym_id)
            if target.side == 'call':
                call_open -= 1
            else:
                put_open -= 1
            today_realloc_count += 1
            return True

        def _try_fill_call(sym_id, score, key, ct, ern):
            """Attempt to open a call position. Returns True if filled."""
            nonlocal cash, call_open
            at_cap = (len(positions) >= MAX_POSITIONS) or (side_capped and call_open >= cap_call)
            if at_cap:
                if not _try_realloc('call', score):
                    return False
            if ct == 'ct_call':
                tier = CT_CALL_TIER
            elif ern and EARN_BOOST_CALL:
                tier = EARN_BOOST_CALL_TIER
            else:
                tier = score_to_tier(score)
            # H3: DD-soft-band call alloc contraction
            dd_scale = 1.0
            if DD_SOFT_BAND_HI > DD_SOFT_BAND_LO and dd > DD_SOFT_BAND_LO:
                if dd >= DD_SOFT_BAND_HI:
                    dd_scale = DD_SOFT_CALL_FLOOR
                else:
                    t = (dd - DD_SOFT_BAND_LO) / (DD_SOFT_BAND_HI - DD_SOFT_BAND_LO)
                    dd_scale = 1.0 - t * (1.0 - DD_SOFT_CALL_FLOOR)
            sat_scale = _opportunity_saturation_scale('call')
            rxdd_scale = _rxdd_call_scale(dd, rxdd_vix_today)
            svr_scale = _svr_scale(_svr_load().get((sym_id, today))) if SVR_ENABLED else 1.0
            mwdd_scale = _mwdd_call_scale(dd, mwdd_mcc_today, rxdd_vix_today)
            tvdd_scale = _tvdd_call_scale(dd, tvdd_trin_today, rxdd_vix_today)
            dqt_scale = _dqt_call_scale(dd, tier)
            vxmd_scale = _vxmd_call_scale(dd, vxmd_whist_today, rxdd_vix_today)
            bdiv_scale = _bdiv_call_scale(bdiv_today)
            spread_tilt_scale = _spread_tilt_scale(score, _spread_load().get((sym_id, today))) if SPREAD_TILT_ENABLED else 1.0
            alloc_frac   = TIER_ALLOC[tier] * reg_scale_c * dd_scale * sat_scale * rxdd_scale * svr_scale * mwdd_scale * tvdd_scale * dqt_scale * vxmd_scale * bdiv_scale * spread_tilt_scale * aw_scale
            allocation_base = _allocation_base(portfolio_value)
            premium_cost = allocation_base * alloc_frac
            cap_remaining = _premium_cap_remaining('call', allocation_base)
            if cap_remaining is not None:
                if cap_remaining <= 0:
                    return False
                premium_cost = min(premium_cost, cap_remaining)
            if premium_cost > cash - reserved_cash or premium_cost <= 0:
                return False
            # P1.5 execution-pessimism cert: entry order fails to fill (env-
            # gated, default 0.0 -> rng never drawn -> bit-identical). A miss
            # frees the slot/capital for the next candidate this day, same as
            # any other "couldn't open" path above.
            if ENTRY_FILL_MISS_P > 0.0 and rng.random() < ENTRY_FILL_MISS_P:
                return False
            sat_scales.append(sat_scale)
            o = call_outcomes[key]
            outcome, pnl = resolve(o, rng)
            cash -= premium_cost
            if collect_tape:
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'call',
                                           score=score, tier=tier, ct=ct, ern=ern,
                                           entry_value=portfolio_value, entry_peak=peak_value,
                                           entry_dd=dd, entry_open_calls=call_open,
                                           entry_open_puts=put_open,
                                           entry_idx=day_idx,
                                           entry_underlying=o.get('entry'),
                                           premium_pct=o.get('premium_pct')))
            else:
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'call',
                                           score=score,
                                           entry_idx=day_idx,
                                           entry_underlying=o.get('entry'),
                                           premium_pct=o.get('premium_pct')))
            open_syms.add(sym_id)
            call_open += 1
            return True

        def _try_fill_put(sym_id, score, key, ct):
            """Attempt to open a put position. Returns True if filled."""
            nonlocal cash, put_open, reserved_cash
            at_cap = (len(positions) >= MAX_POSITIONS) or (side_capped and put_open >= cap_put)
            if at_cap:
                if not _try_realloc('put', score):
                    return False
            tier = CT_PUT_TIER if ct == 'ct_put' else put_score_to_tier(score)
            sat_scale = _opportunity_saturation_scale('put')
            alloc_frac   = PUT_TIER_ALLOC[tier] * reg_scale_p * saw_put_ucurve_scale(today) * sat_scale
            allocation_base = _allocation_base(portfolio_value)
            base_premium_cost = allocation_base * alloc_frac
            cap_remaining = _premium_cap_remaining('put', allocation_base)
            if cap_remaining is not None:
                if cap_remaining <= 0:
                    return False
                base_premium_cost = min(base_premium_cost, cap_remaining)
            reserve_scale = _put_wave_reserve_scale(today)
            premium_cost = base_premium_cost * reserve_scale
            required_cash = base_premium_cost if PUT_WAVE_RESERVE_ENABLED and reserve_scale < 0.999 else premium_cost
            if required_cash > cash - reserved_cash or premium_cost <= 0:
                return False
            # P1.5 execution-pessimism cert: same entry-fill-miss knob as the
            # call path (env-gated, default 0.0 -> rng never drawn).
            if ENTRY_FILL_MISS_P > 0.0 and rng.random() < ENTRY_FILL_MISS_P:
                return False
            sat_scales.append(sat_scale)
            o = put_outcomes[key]
            outcome, pnl = resolve(o, rng)
            cash -= premium_cost
            if PUT_WAVE_RESERVE_ENABLED and reserve_scale < 0.999:
                reserve_amount = max(0.0, base_premium_cost - premium_cost)
                if reserve_amount >= 10.0:
                    cash_reserves.append((day_idx + int(o['exit_bar']), reserve_amount))
                    reserved_cash += reserve_amount
            if collect_tape:
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'put',
                                           score=score, tier=tier, ct=ct, ern=False,
                                           entry_value=portfolio_value, entry_peak=peak_value,
                                           entry_dd=dd, entry_open_calls=call_open,
                                           entry_open_puts=put_open,
                                           entry_idx=day_idx,
                                           entry_underlying=o.get('entry'),
                                           premium_pct=o.get('premium_pct')))
            else:
                positions.append(Position(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'put',
                                           score=score,
                                           entry_idx=day_idx,
                                           entry_underlying=o.get('entry'),
                                           premium_pct=o.get('premium_pct')))
            open_syms.add(sym_id)
            put_open += 1
            return True

        def _do_calls():
            day_calls = calls_by_date.get(today, [])
            if not day_calls:
                return
            eligible = [(sid, sc, k, ct, ern) for sid, sc, k, ct, ern in day_calls
                        if k in call_outcomes and sid not in open_syms]
            # NOT redundant with a single conviction sort, despite appearances:
            #  (1) the primary sort key (0 if ct_call) fills CT-tagged 70-74 calls AHEAD
            #      of plain 95+ (CT_PROMOTE) — a pure score sort cannot express that.
            #  (2) the primary/overflow split keeps 75+ > 70-74 priority independent of
            #      the intra-list sort key (matters under the wr_priority / merged modes).
            # Collapsing this into one conviction sort silently breaks CT promotion.
            primary  = [e for e in eligible if e[1] >= PRIMARY_THRESHOLD or e[3] == 'ct_call']
            overflow = [e for e in eligible if e[1] <  PRIMARY_THRESHOLD and e[3] != 'ct_call']
            primary.sort(key=lambda x: (0 if x[3] == 'ct_call' else 1, -x[1], rng.random()))
            overflow.sort(key=lambda x: (-x[1], rng.random()))
            for sym_id, score, key, ct, ern in primary + overflow:
                # When realloc enabled, defer cap checks to _try_fill_call (which
                # may displace a held position to free a slot). Otherwise short-
                # circuit out of the loop to skip unhelpful iterations.
                if not REALLOC_STRATEGY:
                    if len(positions) >= MAX_POSITIONS:
                        break
                    if side_capped and call_open >= cap_call:
                        break
                _try_fill_call(sym_id, score, key, ct, ern)

        def _do_puts():
            day_puts = puts_by_date.get(today, [])
            if not day_puts:
                return
            pe = [(sid, sc, k, ct) for sid, sc, k, ct in day_puts
                  if k in put_outcomes and sid not in open_syms]
            pe.sort(key=lambda x: (0 if x[3] == 'ct_put' else 1, x[1], rng.random()))
            for sym_id, score, key, ct in pe:
                if not REALLOC_STRATEGY:
                    if len(positions) >= MAX_POSITIONS:
                        break
                    if side_capped and put_open >= cap_put:
                        break
                _try_fill_put(sym_id, score, key, ct)

        if PUT_PRIORITY == 'merged':
            # Unified queue: sort all signals by abs(score-50) (conviction).
            # CT-tagged signals always fill first regardless of raw conviction.
            day_calls = calls_by_date.get(today, [])
            day_puts  = puts_by_date.get(today, [])
            merged = []
            for sid, sc, k, ct, ern in day_calls:
                if k in call_outcomes and sid not in open_syms:
                    is_ct = ct == 'ct_call'
                    merged.append((0 if is_ct else 1, -abs(sc - 50), 'call', sid, sc, k, ct, ern))
            for sid, sc, k, ct in day_puts:
                if k in put_outcomes and sid not in open_syms:
                    is_ct = ct == 'ct_put'
                    merged.append((0 if is_ct else 1, -abs(sc - 50), 'put',  sid, sc, k, ct, None))
            merged.sort(key=lambda x: (x[0], x[1], rng.random()))
            for _ct_pri, _conv, side, sym_id, score, key, ct, ern in merged:
                if not REALLOC_STRATEGY and len(positions) >= MAX_POSITIONS:
                    break
                if side == 'call':
                    if sym_id in open_syms:    # may have been filled this loop on the put side
                        continue
                    _try_fill_call(sym_id, score, key, ct, ern)
                else:
                    if sym_id in open_syms:
                        continue
                    _try_fill_put(sym_id, score, key, ct)
        elif PUT_PRIORITY == 'wr_merged':
            # Unified queue: sort all signals by per-tier empirical priority
            # from WR_PRIORITY_TABLE (assessment-driven). Higher table value =
            # earlier in queue. CT-tagged signals still take their CT_*_TIER's
            # priority; if absent in the table, fall back to WR_PRIORITY_FALLBACK.
            day_calls = calls_by_date.get(today, [])
            day_puts  = puts_by_date.get(today, [])
            tbl = WR_PRIORITY_TABLE
            fb  = WR_PRIORITY_FALLBACK
            merged = []
            for sid, sc, k, ct, ern in day_calls:
                if k in call_outcomes and sid not in open_syms:
                    if ct == 'ct_call':
                        tier = CT_CALL_TIER
                    elif ern and EARN_BOOST_CALL:
                        tier = EARN_BOOST_CALL_TIER
                    else:
                        tier = score_to_tier(sc)
                    pri = tbl.get(tier, fb)
                    merged.append((-pri, 'call', sid, sc, k, ct, ern))
            for sid, sc, k, ct in day_puts:
                if k in put_outcomes and sid not in open_syms:
                    tier = CT_PUT_TIER if ct == 'ct_put' else put_score_to_tier(sc)
                    pri = tbl.get(tier, fb)
                    merged.append((-pri, 'put',  sid, sc, k, ct, None))
            merged.sort(key=lambda x: (x[0], rng.random()))
            for _pri, side, sym_id, score, key, ct, ern in merged:
                if not REALLOC_STRATEGY and len(positions) >= MAX_POSITIONS:
                    break
                if sym_id in open_syms:
                    continue
                if side == 'call':
                    _try_fill_call(sym_id, score, key, ct, ern)
                else:
                    _try_fill_put(sym_id, score, key, ct)
        elif PUT_PRIORITY == 'puts_first':
            _do_puts()
            _do_calls()
        else:  # 'calls_first' — production default
            _do_calls()
            _do_puts()

        end_value = cash + sum(p.premium_cost for p in positions)
        end_base = _allocation_base(end_value)
        total_open_premium = sum(p.premium_cost for p in positions)
        call_open_premium = sum(p.premium_cost for p in positions if p.side == 'call')
        put_open_premium = total_open_premium - call_open_premium
        denom_value = end_value if end_value > 0 else 1.0
        denom_base = end_base if end_base > 0 else 1.0
        open_premium_pcts.append(total_open_premium / denom_value)
        open_premium_base_pcts.append(total_open_premium / denom_base)
        call_open_premium_base_pcts.append(call_open_premium / denom_base)
        put_open_premium_base_pcts.append(put_open_premium / denom_base)
        if end_value > 0:
            reserve_pct = reserved_cash / end_value
            reserve_pct_sum += reserve_pct
            reserve_pct_n += 1
            max_reserved_cash_pct = max(max_reserved_cash_pct, reserve_pct)

    last_idx = len(trading_days) - 1
    last_day = trading_days[-1] if trading_days else None
    for p in positions:
        cash += p.premium_cost * (1 + NET_HARD_SELL)
        if p.side == 'call': hard_c += 1
        else:                hard_p += 1
        if collect_tape:
            trade_rows.append((
                p.sym_id, p.entry_date, last_day, p.side, p.score, p.tier,
                p.ct, p.ern, p.premium_cost, NET_HARD_SELL, 'hard',
                p.entry_value, p.entry_peak, p.entry_dd,
                p.entry_open_calls, p.entry_open_puts,
            ))
    portfolio_value = cash

    final_dd = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0
    max_dd   = max(max_dd, final_dd)
    if collect_tape:
        # Flush final episode
        if cur_episode_trough_val < peak_value:
            episodes.append((cur_episode_start_idx, cur_episode_trough_idx,
                             peak_value, cur_episode_trough_val))
        # If final value is below peak (final_dd > 0), record it as episode too
        if portfolio_value < peak_value and last_idx >= 0:
            episodes.append((cur_episode_start_idx, last_idx, peak_value, portfolio_value))
    ct = tp_c + sl_c + hard_c or 1
    pt = tp_p + sl_p + hard_p or 1
    result = dict(
        final = portfolio_value,
        max_dd = max_dd,
        t1m_bar = t1m_bar,
        t2x_bar = t2x_bar,
        t_50dd_bar = t_50dd_bar,
        call_tp = tp_c / ct * 100, call_sl = sl_c / ct * 100, call_hard = hard_c / ct * 100,
        put_tp  = tp_p / pt * 100, put_sl  = sl_p / pt * 100, put_hard  = hard_p / pt * 100,
        call_trades = tp_c + sl_c + hard_c,
        put_trades  = tp_p + sl_p + hard_p,
        avg_open_premium_pct = statistics.mean(open_premium_pcts) * 100 if open_premium_pcts else 0.0,
        max_open_premium_pct = max(open_premium_pcts) * 100 if open_premium_pcts else 0.0,
        avg_open_premium_base_pct = statistics.mean(open_premium_base_pcts) * 100 if open_premium_base_pcts else 0.0,
        max_open_premium_base_pct = max(open_premium_base_pcts) * 100 if open_premium_base_pcts else 0.0,
        avg_call_open_premium_base_pct = statistics.mean(call_open_premium_base_pcts) * 100 if call_open_premium_base_pcts else 0.0,
        avg_put_open_premium_base_pct = statistics.mean(put_open_premium_base_pcts) * 100 if put_open_premium_base_pcts else 0.0,
        avg_saturation_scale = statistics.mean(sat_scales) if sat_scales else 1.0,
        avg_reserved_cash_pct = reserve_pct_sum / reserve_pct_n * 100 if reserve_pct_n else 0.0,
        max_reserved_cash_pct = max_reserved_cash_pct * 100,
    )
    if collect_tape:
        # Only return top-3 worst episodes per seed (sorted by magnitude desc)
        ranked = sorted(episodes, key=lambda e: (e[2] - e[3]) / e[2] if e[2] > 0 else 0, reverse=True)
        result['_tape']     = trade_rows
        result['_episodes'] = ranked[:3]
    # Track A — weekly-downsampled equity curve (MC_EMIT_CURVE). Append the final
    # settled value then downsample to weekly (last bar of each 5-bar block + the
    # final point) to keep the JSON payload bounded. Default-OFF => key absent.
    if _emit_curve and eq_series is not None:
        eq_series.append(portfolio_value)
        w = _aw_wave()
        if w:
            _, _eqw = w.weekly_downsample(None, eq_series, bars_per_week=5)
            result['eq_weekly'] = [float(x) for x in _eqw]
        else:
            # fallback weekly downsample if the wave module is unavailable
            result['eq_weekly'] = [float(eq_series[i]) for i in range(4, len(eq_series), 5)] \
                                  + ([float(eq_series[-1])] if eq_series and (len(eq_series) - 1) % 5 != 4 else [])
    return result


# ---- Multiprocessing worker (must be at module scope for pickling) ----------
#
# Heavy data (calls_by_date, call_outcomes, puts_by_date, put_outcomes,
# trading_days, regime_dates, regime_map) is pickled ONCE per worker via
# `initializer`. Per-task args are tiny (just mode + seed). This avoids
# re-pickling the large dicts on every iteration.

_MP_STATE = {}


def _mc_init_worker(trading_days, calls_by_date, call_outcomes,
                    puts_by_date, put_outcomes, regime_dates, regime_map,
                    close_by_sym_idx=None, vix_dates=None, vix_map=None, mcc_dates=None, mcc_map=None,
                    trin_dates=None, trin_map=None, whist_dates=None, whist_map=None,
                    bdiv_dates=None, bdiv_map=None,
                    breadth_dates=None, breadth_map=None,
                    cell_params=None):
    """Pool initializer — runs once per worker process.

    cell_params (optional): a dict of the per-cell sizing knobs
    {TIER_ALLOC, PUT_TIER_ALLOC, MAX_POSITIONS, MAX_POSITIONS_CALL,
    MAX_POSITIONS_PUT}. run_single_sim reads these as MODULE GLOBALS, and on
    Windows spawn each worker re-imports the module fresh (so it would otherwise
    see only the import-time env values, NOT a parent's in-process mutation).
    Applying cell_params inside the worker initializer makes the in-process
    load-once-per-window sweep bit-exact under MP. Default None = no override
    (single-config path is unchanged)."""
    if cell_params is not None:
        _apply_cell_params(cell_params)
    _MP_STATE['trading_days']     = trading_days
    _MP_STATE['calls_by_date']    = calls_by_date
    _MP_STATE['call_outcomes']    = call_outcomes
    _MP_STATE['puts_by_date']     = puts_by_date
    _MP_STATE['put_outcomes']     = put_outcomes
    _MP_STATE['regime_dates']     = regime_dates
    _MP_STATE['regime_map']       = regime_map
    _MP_STATE['close_by_sym_idx'] = close_by_sym_idx
    _MP_STATE['vix_dates']        = vix_dates
    _MP_STATE['vix_map']          = vix_map
    _MP_STATE['mcc_dates']        = mcc_dates
    _MP_STATE['mcc_map']          = mcc_map
    _MP_STATE['trin_dates']       = trin_dates
    _MP_STATE['trin_map']         = trin_map
    _MP_STATE['whist_dates']      = whist_dates
    _MP_STATE['whist_map']        = whist_map
    _MP_STATE['bdiv_dates']       = bdiv_dates
    _MP_STATE['bdiv_map']         = bdiv_map
    _MP_STATE['breadth_dates']    = breadth_dates
    _MP_STATE['breadth_map']      = breadth_map


def _set_worker_cell_params(cell_params):
    """One-shot task mapped to every worker to reset the per-cell sizing globals
    between cells when a window-level pool is reused across cells (load-once sweep).
    Returns the pid so the broadcast can confirm it hit every distinct worker.
    cell_params=None is a no-op (leaves the worker's current globals as-is)."""
    if cell_params is not None:
        _apply_cell_params(cell_params)
    return os.getpid()


def _broadcast_cell_params(pool, n_workers, cell_params):
    """Apply cell_params in EVERY worker of a reused pool before mapping seeds.

    We submit n_workers * 4 copies of the one-shot task with chunksize=1 so the
    pool's round-robin dispatch lands the update on every distinct worker process
    (a plain map of n_workers tasks can double-dispatch to a fast worker and miss
    a slow one). Verified by collecting the distinct pids; if any worker was
    missed we retry once with more tasks, then fall back to a hard assert so a
    silent stale-globals bug can never corrupt a cell's result."""
    if cell_params is None:
        return
    tasks = max(n_workers * 4, 8)
    pids = set(pool.map(_set_worker_cell_params, [cell_params] * tasks, chunksize=1))
    if len(pids) < n_workers:
        # Retry once with a much larger fan-out to cover a stuck/slow worker.
        more = set(pool.map(_set_worker_cell_params, [cell_params] * (n_workers * 16), chunksize=1))
        pids |= more
    assert len(pids) >= n_workers, (
        f"_broadcast_cell_params hit only {len(pids)}/{n_workers} workers — "
        f"refusing to simulate with possibly-stale worker globals")


def _make_window_pool(ctx, n_workers):
    """Build an MP pool whose workers hold this window's ctx in _MP_STATE (pickled
    ONCE here, not per cell). The load-once sweep builds this once per window and
    reuses it across all cells, eliminating the ~27x ctx-pickle + worker-spawn cost.
    cell_params are applied per-cell afterwards via _broadcast_cell_params, so the
    initializer passes cell_params=None (workers start at the live module globals)."""
    return multiprocessing.Pool(
        processes=n_workers,
        initializer=_mc_init_worker,
        initargs=(ctx['trading_days'], ctx['calls_by_date'], ctx['call_outcomes'],
                  ctx['puts_by_date'], ctx['put_outcomes'], ctx['regime_dates'], ctx['regime_map'],
                  ctx['close_by_sym_idx'], ctx['rxdd_vix_dates'], ctx['rxdd_vix_map'],
                  ctx['mwdd_mcc_dates'], ctx['mwdd_mcc_map'],
                  ctx['tvdd_trin_dates'], ctx['tvdd_trin_map'],
                  ctx['vxmd_whist_dates'], ctx['vxmd_whist_map'],
                  ctx['bdiv_dates'], ctx['bdiv_map'],
                  ctx['breadth_dates'], ctx['breadth_map'], None),
    )


def _mc_iter_worker(seed):
    """Run one MC iteration. Receives seed — pulls heavy data from worker globals."""
    rng = random.Random(seed)
    return run_single_sim(
        _MP_STATE['trading_days'],  _MP_STATE['calls_by_date'], _MP_STATE['call_outcomes'],
        _MP_STATE['puts_by_date'],  _MP_STATE['put_outcomes'],  rng,
        _MP_STATE['regime_dates'],  _MP_STATE['regime_map'],
        vix_dates=_MP_STATE.get('vix_dates'), vix_map=_MP_STATE.get('vix_map'),
        mcc_dates=_MP_STATE.get('mcc_dates'), mcc_map=_MP_STATE.get('mcc_map'),
        trin_dates=_MP_STATE.get('trin_dates'), trin_map=_MP_STATE.get('trin_map'),
        whist_dates=_MP_STATE.get('whist_dates'), whist_map=_MP_STATE.get('whist_map'),
        bdiv_dates=_MP_STATE.get('bdiv_dates'), bdiv_map=_MP_STATE.get('bdiv_map'),
        breadth_dates=_MP_STATE.get('breadth_dates'), breadth_map=_MP_STATE.get('breadth_map'),
        collect_tape=TRADE_TAPE_ENABLED,
        close_by_sym_idx=_MP_STATE.get('close_by_sym_idx'),
    )


def _stable_label_seed(label):
    digest = hashlib.blake2b(str(label).encode('utf-8'), digest_size=8).digest()
    return int.from_bytes(digest, 'big')


def _apply_cell_params(cell_params):
    """Set the per-cell sizing globals in THIS process. Used by the in-process
    load-once sweep (experiments/concentration_2x/sweep.py) to reparameterize a
    prepared window without re-precomputing outcomes — and inside MP workers so
    spawn-reimported workers see the same knobs. Only the keys present are set;
    run_single_sim reads all of these as module globals at call time.

    cell_params keys (all optional):
      TIER_ALLOC          (dict) -> replaces module TIER_ALLOC
      PUT_TIER_ALLOC      (dict) -> replaces module PUT_TIER_ALLOC
      MAX_POSITIONS       (int)
      MAX_POSITIONS_CALL  (int or None)
      MAX_POSITIONS_PUT   (int or None)
    """
    global TIER_ALLOC, PUT_TIER_ALLOC, MAX_POSITIONS, MAX_POSITIONS_CALL, MAX_POSITIONS_PUT
    if 'TIER_ALLOC' in cell_params:
        TIER_ALLOC = dict(cell_params['TIER_ALLOC'])
    if 'PUT_TIER_ALLOC' in cell_params:
        PUT_TIER_ALLOC = dict(cell_params['PUT_TIER_ALLOC'])
    if 'MAX_POSITIONS' in cell_params:
        MAX_POSITIONS = int(cell_params['MAX_POSITIONS'])
    if 'MAX_POSITIONS_CALL' in cell_params:
        v = cell_params['MAX_POSITIONS_CALL']
        MAX_POSITIONS_CALL = None if v is None else int(v)
    if 'MAX_POSITIONS_PUT' in cell_params:
        v = cell_params['MAX_POSITIONS_PUT']
        MAX_POSITIONS_PUT = None if v is None else int(v)


# ---- Trade-tape parquet dump (DD-ledger feed) -------------------------------

def _dump_trade_tape(label, seeds, results, trading_days):
    """Write per-trade tape + per-seed DD episodes to .cache/dd_ledger/."""
    from pathlib import Path
    out_dir = Path(__file__).resolve().parent / '.cache' / 'dd_ledger'
    out_dir.mkdir(parents=True, exist_ok=True)

    trade_cols = ('seed', 'sym_id', 'entry_date', 'exit_date', 'side', 'score', 'tier',
                  'ct', 'ern', 'premium_cost', 'option_pnl', 'outcome',
                  'entry_value', 'entry_peak', 'entry_dd',
                  'entry_open_calls', 'entry_open_puts')
    rows = []
    for seed, r in zip(seeds, results):
        tape = r.get('_tape')
        if not tape:
            continue
        for t in tape:
            rows.append((seed,) + t)

    ep_cols = ('seed', 'window_label', 'rank',
               'start_date', 'trough_date', 'peak_value', 'trough_value', 'magnitude',
               'final_value', 'max_dd')
    ep_rows = []
    n_days = len(trading_days)
    def _idx_to_date(i):
        if 0 <= i < n_days:
            d = trading_days[i]
            return d.isoformat() if hasattr(d, 'isoformat') else str(d)
        return None
    for seed, r in zip(seeds, results):
        eps = r.get('_episodes', [])
        for rank, (s_idx, t_idx, peak, trough) in enumerate(eps):
            mag = (peak - trough) / peak if peak > 0 else 0
            ep_rows.append((seed, label, rank,
                            _idx_to_date(s_idx), _idx_to_date(t_idx),
                            peak, trough, mag,
                            r['final'], r['max_dd']))

    try:
        import polars as pl
    except ImportError:
        # Fallback: write JSON
        import json
        out_json = out_dir / f'tape_{label}.json'
        with open(out_json, 'w') as f:
            json.dump({'trades': [list(r) for r in rows], 'episodes': ep_rows,
                       'cols': {'trades': trade_cols, 'episodes': ep_cols}}, f, default=str)
        print(f"  [tape] wrote {len(rows):,} trades + {len(ep_rows)} episodes -> {out_json}")
        return

    # Polars DataFrames
    if rows:
        # Convert dates to ISO strings for parquet portability
        rows_str = [(r[0], r[1],
                     r[2].isoformat() if hasattr(r[2], 'isoformat') else str(r[2]),
                     r[3].isoformat() if hasattr(r[3], 'isoformat') else str(r[3]),
                     ) + r[4:] for r in rows]
        df = pl.DataFrame(rows_str, schema=list(trade_cols), orient='row', infer_schema_length=None)
        out_pq = out_dir / f'tape_{label}.parquet'
        df.write_parquet(out_pq)
        print(f"  [tape] wrote {len(df):,} trades -> {out_pq}")

    if ep_rows:
        df_ep = pl.DataFrame(ep_rows, schema=list(ep_cols), orient='row', infer_schema_length=None)
        out_pq = out_dir / f'episodes_{label}.parquet'
        df_ep.write_parquet(out_pq)
        print(f"  [tape] wrote {len(df_ep):,} episodes -> {out_pq}")


# ---- Window runner ----------------------------------------------------------
#
# run_window is split into a PREPARE step (_prepare_window) and a SIMULATE step
# (_simulate_window). The expensive per-window work — signal load, every per-date
# map, the call/put barrier-outcome precompute — happens ONCE in _prepare_window
# and is returned as a plain dict `ctx`. _simulate_window then runs the N-iteration
# sim against that ctx, reading the per-cell sizing knobs (TIER_ALLOC, PUT_TIER_ALLOC,
# MAX_POSITIONS, MAX_POSITIONS_CALL, MAX_POSITIONS_PUT) live from module globals so a
# sweep can re-invoke SIMULATE many times for different cell parameterizations against
# the SAME prepared ctx (~27x fewer precomputes). run_window calls both, so the
# single-config path is byte-identical to before. See experiments/concentration_2x/sweep.py.

def _prepare_window(label, d_start, d_end, version):
    """PREPARE step: load signals, build every per-date map, precompute call/put
    barrier outcomes. Returns a `ctx` dict consumed by _simulate_window. This is
    the dominant cost; it is identical across all flat-alloc / MaxPos cells of a
    window, so a sweep prepares ONCE and simulates many cells against it."""
    print(f"\n{'='*110}")
    print(f"WINDOW: {label}  ({d_start} -> {d_end})")
    print('='*110)

    call_sigs = load_signals(version, d_start, d_end)
    put_sigs  = load_put_signals(version, d_start, d_end)
    primary_n  = sum(1 for s in call_sigs if s.overall >= PRIMARY_THRESHOLD)
    overflow_n = len(call_sigs) - primary_n
    print(f"Call signals: {len(call_sigs)}  (75+={primary_n}, 70-74={overflow_n})  |  Put signals (<=25): {len(put_sigs)}")

    if WEAK_WEEKLY_CALL_DROP and call_sigs:
        import json as _json_c
        n_before_c = len(call_sigs)
        kept_c = []
        dropped_c = 0
        for s in call_sigs:
            ov = int(s.overall)
            if not (WEAK_WEEKLY_CALL_MIN_OV <= ov <= WEAK_WEEKLY_CALL_MAX_OV):
                kept_c.append(s); continue
            wadj = None
            wi_raw = getattr(s, 'weight_info', None)
            if wi_raw:
                try:
                    wi = _json_c.loads(wi_raw) if isinstance(wi_raw, str) else wi_raw
                    wa = wi.get('w_adj')
                    if wa is None: wa = wi.get('weekly_adj')
                    if wa is not None: wadj = float(wa)
                except Exception:
                    wadj = None
            # Drop if wadj is below cutoff (signal has bearish weekly drag)
            if wadj is None or wadj >= WEAK_WEEKLY_CALL_WADJ:
                kept_c.append(s); continue
            # Optional stoch gate: only drop when stoch >= GE (avoid touching
            # genuinely oversold setups that may bounce)
            if WEAK_WEEKLY_CALL_STOCH_GE > 0:
                stoch_v = getattr(s, 'stoch', None)
                if stoch_v is None or int(stoch_v) < WEAK_WEEKLY_CALL_STOCH_GE:
                    kept_c.append(s); continue
            dropped_c += 1
        call_sigs = kept_c
        stoch_str = f" AND stoch>={WEAK_WEEKLY_CALL_STOCH_GE}" if WEAK_WEEKLY_CALL_STOCH_GE > 0 else ""
        print(f"  WEAK_WEEKLY_CALL_DROP=ON: dropped {dropped_c}/{n_before_c} calls "
              f"(overall in [{WEAK_WEEKLY_CALL_MIN_OV},{WEAK_WEEKLY_CALL_MAX_OV}] AND w_adj<{WEAK_WEEKLY_CALL_WADJ}{stoch_str}); "
              f"remaining calls: {len(call_sigs)}")

    if EARN_SUPP_PUT:
        put_sigs, dropped = _earnings_suppress_filter(
            put_sigs, d_start, d_end,
            EARN_SUPP_PUT_DAYS, EARN_SUPP_PUT_MIN_OV, EARN_SUPP_PUT_MAX_OV, side='put')
        print(f"  EARN_SUPP_PUT=ON: dropped {dropped} puts (overall in [{EARN_SUPP_PUT_MIN_OV},{EARN_SUPP_PUT_MAX_OV}], earnings <= {EARN_SUPP_PUT_DAYS} trd days post-signal); remaining puts: {len(put_sigs)}")

    if WEAK_WEEKLY_PUT_DROP:
        import json as _json
        n_before = len(put_sigs)
        kept = []
        dropped_n = 0
        for s in put_sigs:
            ov = int(s.overall)
            if not (WEAK_WEEKLY_PUT_MIN_OV <= ov <= WEAK_WEEKLY_PUT_MAX_OV):
                kept.append(s)
                continue
            wadj = None
            wi_raw = getattr(s, 'weight_info', None)
            if wi_raw:
                try:
                    wi = _json.loads(wi_raw) if isinstance(wi_raw, str) else wi_raw
                    wa = wi.get('w_adj')
                    if wa is None: wa = wi.get('weekly_adj')
                    if wa is not None:
                        wadj = float(wa)
                except Exception:
                    wadj = None
            if wadj is None or wadj <= WEAK_WEEKLY_PUT_WADJ:
                # strong (or unknown) weekly drag -> keep
                kept.append(s)
                continue
            if WEAK_WEEKLY_PUT_VSIG_REJ_ONLY:
                vsig = getattr(s, 'volume_signal', None)
                if vsig != 'REJECTION':
                    kept.append(s)
                    continue
            dropped_n += 1
        put_sigs = kept
        rej_str = " AND vsig=REJECTION" if WEAK_WEEKLY_PUT_VSIG_REJ_ONLY else ""
        print(f"  WEAK_WEEKLY_PUT_DROP=ON: dropped {dropped_n}/{n_before} puts "
              f"(overall in [{WEAK_WEEKLY_PUT_MIN_OV},{WEAK_WEEKLY_PUT_MAX_OV}] AND w_adj > {WEAK_WEEKLY_PUT_WADJ}{rej_str}); "
              f"remaining puts: {len(put_sigs)}")

    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph      = load_price_history(sym_ids, d_start, d_end)

    breadth_dates, breadth_map = load_breadth_map(d_start, d_end)
    if call_sigs and breadth_dates:
        n_str = sum(1 for s in call_sigs if is_stressed(breadth_dates, breadth_map, s.date))
        print(f"Breadth map: {len(breadth_map)} dates  |  stressed call signals: {n_str/len(call_sigs)*100:.1f}%")
    if DTE_ROUTER_ENABLED:
        dte_router_dates, dte_router_vix_map, dte_router_regime_map = _load_dte_router_market_maps(d_start, d_end)
        router_market_desc = (
            f"VIX>={DTE_ROUTER_VIX_MIN:.0f}, regime {DTE_ROUTER_REGIME_MIN:.0f}-{DTE_ROUTER_REGIME_MAX:.0f}"
            if DTE_ROUTER_VIX_MIN > 0.0 or DTE_ROUTER_REGIME_MIN > 0.0 or DTE_ROUTER_REGIME_MAX < 100.0
            else "market filters off"
        )
        router_alloc_desc = str(DTE_ROUTER_ALLOC_SCORE_CAP) if DTE_ROUTER_ALLOC_SCORE_CAP > 0 else "off"
        print(
            f"DTE router map: {len(dte_router_dates)} dates  |  "
            f"score>={DTE_ROUTER_SCORE_MIN}, trend<{DTE_ROUTER_TREND_LT:.0f}, "
            f"{router_market_desc}, daycap={DTE_ROUTER_DAY_CAP}, "
            f"alloc_score_cap={router_alloc_desc}"
        )
    else:
        dte_router_dates, dte_router_vix_map, dte_router_regime_map = [], {}, {}

    if (PUT_TIGHTEN_BREADTH_LE > 0 or PUT_TIGHTEN_BREADTH_GE > 0) and put_sigs and breadth_dates:
        n_before = len(put_sigs)
        kept = []
        dropped_n = 0
        for s in put_sigs:
            ov = int(s.overall)
            if ov <= PUT_TIGHTEN_THRESH:
                kept.append(s)
                continue
            brd = breadth_on_or_before(breadth_dates, breadth_map, s.date)
            if brd is None:
                kept.append(s)
                continue
            drop = False
            if PUT_TIGHTEN_BREADTH_LE > 0 and brd <= PUT_TIGHTEN_BREADTH_LE:
                drop = True
            if PUT_TIGHTEN_BREADTH_GE > 0 and brd >= PUT_TIGHTEN_BREADTH_GE:
                drop = True
            if drop:
                dropped_n += 1
            else:
                kept.append(s)
        put_sigs = kept
        gate_str = []
        if PUT_TIGHTEN_BREADTH_LE > 0: gate_str.append(f"brd<={PUT_TIGHTEN_BREADTH_LE}")
        if PUT_TIGHTEN_BREADTH_GE > 0: gate_str.append(f"brd>={PUT_TIGHTEN_BREADTH_GE}")
        print(f"  PUT_TIGHTEN: dropped {dropped_n}/{n_before} puts (overall in ({PUT_TIGHTEN_THRESH},{PUT_THRESHOLD}] AND ({' OR '.join(gate_str)})); remaining puts: {len(put_sigs)}")

    if MAX_PUTS_PER_DAY > 0 and put_sigs:
        from collections import defaultdict as _dd
        by_date = _dd(list)
        for s in put_sigs:
            by_date[s.date].append(s)
        kept, total_dropped, capped_days = [], 0, 0
        for date_sigs in by_date.values():
            date_sigs.sort(key=lambda s: s.overall)  # lower overall = more bearish = higher priority
            kept.extend(date_sigs[:MAX_PUTS_PER_DAY])
            dropped = max(0, len(date_sigs) - MAX_PUTS_PER_DAY)
            total_dropped += dropped
            if dropped > 0:
                capped_days += 1
        put_sigs = sorted(kept, key=lambda s: (s.date, s.overall))
        if total_dropped > 0:
            print(f"  MAX_PUTS_PER_DAY={MAX_PUTS_PER_DAY}: dropped {total_dropped} puts on {capped_days} high-density days; remaining: {len(put_sigs)}")

    regime_dates, regime_map = load_regime_map(d_start, d_end)
    rxdd_vix_dates, rxdd_vix_map = [], {}
    if RXDD_ENABLED or EXR_ENABLED or VXMD_ENABLED:   # EXR/VXMD also need the VIX series (crash-fade / panic gate)
        try:
            rxdd_vix_dates, rxdd_vix_map, _ = _load_dte_router_market_maps(d_start, d_end)
            if RXDD_ENABLED:
                print(f"RXDD map: {len(rxdd_vix_dates)} vix dates  |  "
                      f"center={RXDD_VIX_C} width={RXDD_VIX_W} depth={RXDD_DEPTH} dd_min={RXDD_DD_MIN}")
            if EXR_ENABLED:
                print(f"EXR map: {len(rxdd_vix_dates)} vix dates  |  hot_cap={EXR_HOT_CAP} "
                      f"size_lo={EXR_SIZE_LO} size_hi={EXR_SIZE_HI} vix_fade={EXR_VIX_FADE} vix_w={EXR_VIX_W}")
        except Exception as _rxdd_e:
            print(f"  [RXDD/EXR] vix map load failed ({_rxdd_e}); inactive this window")
            rxdd_vix_dates, rxdd_vix_map = [], {}
    mwdd_mcc_dates, mwdd_mcc_map = [], {}
    if MWDD_ENABLED:
        try:
            mwdd_mcc_dates, mwdd_mcc_map = load_mcclellan_map(d_start, d_end)
            print(f"MWDD map: {len(mwdd_mcc_dates)} mcclellan dates  |  "
                  f"center={MWDD_MCC_C} width={MWDD_MCC_W} depth={MWDD_DEPTH} dd_min={MWDD_DD_MIN} vix_panic={MWDD_VIX_PANIC}")
        except Exception as _mwdd_e:
            print(f"  [MWDD] mcclellan map load failed ({_mwdd_e}); inactive this window")
            mwdd_mcc_dates, mwdd_mcc_map = [], {}
    tvdd_trin_dates, tvdd_trin_map = [], {}
    if TVDD_ENABLED:
        try:
            tvdd_trin_dates, tvdd_trin_map = load_trin_map(d_start, d_end)
            print(f"TVDD map: {len(tvdd_trin_dates)} trin dates  |  "
                  f"center={TVDD_TRIN_C} width={TVDD_TRIN_W} depth={TVDD_DEPTH} dd_min={TVDD_DD_MIN} vix_panic={TVDD_VIX_PANIC}")
        except Exception as _tvdd_e:
            print(f"  [TVDD] trin map load failed ({_tvdd_e}); inactive this window")
            tvdd_trin_dates, tvdd_trin_map = [], {}
    vxmd_whist_dates, vxmd_whist_map = [], {}
    if VXMD_ENABLED:
        try:
            vxmd_whist_dates, vxmd_whist_map = load_vix_whist_map(d_start, d_end)
            print(f"VXMD map: {len(vxmd_whist_dates)} vix-whist dates  |  "
                  f"lo={VXMD_WHIST_LO} hi={VXMD_WHIST_HI} depth={VXMD_DEPTH} dd_min={VXMD_DD_MIN} vix_panic={VXMD_VIX_PANIC}")
        except Exception as _vxmd_e:
            print(f"  [VXMD] vix-whist map load failed ({_vxmd_e}); inactive this window")
            vxmd_whist_dates, vxmd_whist_map = [], {}
    bdiv_dates, bdiv_map = [], {}
    if BDIV_ENABLED:
        try:
            bdiv_dates, bdiv_map = load_bdiv_map(d_start, d_end)
            print(f"BDIV map: {len(bdiv_dates)} divergence dates  |  "
                  f"prox_cut={BDIV_PROX_CUT} prox_full={BDIV_PROX_FULL} gap_c={BDIV_GAP_C} gap_w={BDIV_GAP_W} depth={BDIV_DEPTH}")
        except Exception as _bdiv_e:
            print(f"  [BDIV] divergence map load failed ({_bdiv_e}); inactive this window")
            bdiv_dates, bdiv_map = [], {}
    if SPREAD_TILT_ENABLED:
        _sm = _spread_load()
        print(f"SPREAD_TILT map: {len(_sm)} (sym,date) spreads  |  band 75-79 only, "
              f"lo={SPREAD_TILT_LO} hi={SPREAD_TILT_HI} depth={SPREAD_TILT_DEPTH} (down-weight only)")
    if BREADTH_ALLOC_ENABLED:
        stress_info = (f" | STRESS cut: <{F3F_PUT_STRESS_THRESH:.0f}→{F3F_PUT_STRESS_FLOOR:.2f} at {F3F_PUT_STRESS_LOW:.0f}"
                       if F3F_PUT_STRESS_THRESH > 0 else "")
        print(f"Alloc map (F3f breadth): {len(regime_map)} dates  |  "
              f"call: 1.0 if brd>=  {F3F_CALL_THRESH:.0f} else linear to {F3F_CALL_FLOOR:.2f} at {F3F_CALL_LOW:.0f}  |  "
              f"put: 1.0 if brd<={F3F_PUT_THRESH:.0f} else linear to {F3F_PUT_FLOOR:.2f} at {F3F_PUT_HIGH:.0f}{stress_info}")
    else:
        active_slope = REGIME_SLOPE_UP is not None or REGIME_SLOPE_DOWN is not None or REGIME_SLOPE != 0.0
        if active_slope or REGIME_SLOPE_PUT not in (None, 0.0):
            if REGIME_SLOPE_UP is not None or REGIME_SLOPE_DOWN is not None:
                print(f"Regime map: {len(regime_map)} dates  |  slope_up={REGIME_SLOPE_UP} slope_dn={REGIME_SLOPE_DOWN} put_slope={REGIME_SLOPE_PUT}")
            else:
                print(f"Regime map: {len(regime_map)} dates  |  slope={REGIME_SLOPE} put_slope={REGIME_SLOPE_PUT}")

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20):
                ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    # close_by_sym_idx: lookup (sym_id, day_idx) -> close_price for mark-to-model
    # under reallocation. Built once per window. Only populated when
    # REALLOC_STRATEGY != '' to avoid the ~Nsyms*Ndays dict overhead by default.
    close_by_sym_idx = None
    if REALLOC_STRATEGY:
        close_by_sym_idx = {}
        td_set = set(trading_days)
        td_idx = {d: i for i, d in enumerate(trading_days)}
        for sid, bars in ph.items():
            for b in bars:
                d = b[0]
                if d in td_set:
                    close_by_sym_idx[(sid, td_idx[d])] = b[1]  # close

    # Earnings-window tag for calls (when EARN_BOOST_CALL=1).
    ern_call_lookup = set()  # set of (sym_id, signal_date) that are PRE-earnings within window
    if EARN_BOOST_CALL:
        cal_syms = {s.symbol_id for s in call_sigs}
        ern_rows = list(EarningsDate.select(
                EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                        .where(EarningsDate.symbol.in_(list(cal_syms)),
                               EarningsDate.date >= d_start - timedelta(days=10),
                               EarningsDate.date <= d_end + timedelta(days=EARN_BOOST_CALL_DAYS*2 + 7))
                        .order_by(EarningsDate.symbol, EarningsDate.date)
                        .tuples())
        from iv_crush_model import compute_effective_date as _eff
        ern_by_sym = defaultdict(list)
        for sym, d, ct in ern_rows:
            ern_by_sym[sym].append(_eff(d, ct))   # AMC shifted forward

        def _fwd_n(d, n):
            out = d
            while n > 0:
                out += timedelta(days=1)
                if _is_trading_day(out):
                    n -= 1
            return out

        for sig in call_sigs:
            ov = int(sig.overall)
            if not (EARN_BOOST_CALL_MIN_OV <= ov <= EARN_BOOST_CALL_MAX_OV):
                continue
            sym_ed = ern_by_sym.get(sig.symbol_id, [])
            if not sym_ed:
                continue
            win_end = _fwd_n(sig.date, EARN_BOOST_CALL_DAYS)
            if any(sig.date < ed <= win_end for ed in sym_ed):
                ern_call_lookup.add((sig.symbol_id, sig.date))

    calls_by_date = defaultdict(list)
    ct_call_n = 0
    ern_boost_n = 0
    for sig in call_sigs:
        key = (sig.symbol_id, sig.date)
        ct  = ct_tag(sig.overall, sig.trend, 'call')
        if ct: ct_call_n += 1
        ern = (sig.symbol_id, sig.date) in ern_call_lookup
        if ern: ern_boost_n += 1
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct, ern))
    if EARN_BOOST_CALL:
        print(f"  EARN_BOOST_CALL=ON: tagged {ern_boost_n} calls in [{EARN_BOOST_CALL_MIN_OV},{EARN_BOOST_CALL_MAX_OV}] within {EARN_BOOST_CALL_DAYS} trd days BEFORE earnings -> tier='{EARN_BOOST_CALL_TIER}' (alloc={TIER_ALLOC[EARN_BOOST_CALL_TIER]:.0%})")
    puts_by_date = defaultdict(list)
    ct_put_n = 0
    for sig in put_sigs:
        key = (sig.symbol_id, sig.date)
        ct  = ct_tag(sig.overall, sig.trend, 'put')
        if ct: ct_put_n += 1
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall, key, ct))
    if CT_PROMOTE:
        print(f"CT-tagged signals: ct_call={ct_call_n} (call overall>={OVERFLOW_THRESHOLD} & trend<={CT_CALL_TREND_MAX})  "
              f"ct_put={ct_put_n} (put overall<={PUT_THRESHOLD} & trend>={CT_PUT_TREND_MIN})")

    # Build earnings map (effective dates — AMC shifted to next trading day) for:
    #   - find_spanning_earnings → vega application in option_pricing-aware resolve()
    #   - EARN_SUPP_PUT (portfolio-stage put suppression)
    #   - EARN_BOOST_CALL (portfolio-stage call cascade promotion)
    # Always loaded — option-aware MC needs it for vega sampling regardless of
    # the legacy IV_CRUSH_ENABLED flag (which now only controls the post-hoc
    # iv_adjust_outcome wrapper, no longer relevant to the inline path).
    ern_map_for_iv = None
    all_syms = {s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs}
    if all_syms:
        iv_ern_rows = list(EarningsDate.select(
                EarningsDate.symbol, EarningsDate.date, EarningsDate.call_time)
                           .where(EarningsDate.symbol.in_(list(all_syms)),
                                  EarningsDate.date >= d_start - timedelta(days=10),
                                  EarningsDate.date <= d_end + timedelta(days=HOLD_DAYS*2 + 14))
                           .order_by(EarningsDate.symbol, EarningsDate.date)
                           .tuples())
        from iv_crush_model import compute_effective_date
        ern_map_for_iv = defaultdict(list)
        for sym, d, ct in iv_ern_rows:
            eff = compute_effective_date(d, ct, trading_days)
            ern_map_for_iv[sym].append(eff)
        # Sort + dedupe (AMC shift may collide an AMC event with a later BMO event)
        for sym in list(ern_map_for_iv.keys()):
            ern_map_for_iv[sym] = sorted(set(ern_map_for_iv[sym]))
        amc_count = sum(1 for _, _, ct in iv_ern_rows if ct and str(ct) >= '16:00:00')
        print(f"  Earnings map: {len(ern_map_for_iv)} symbols, {len(iv_ern_rows)} events ({amc_count} AMC shifted to next trading day)")

    # Build per-day same-side count map for STRESS_MODE='count' (experiment).
    # Counts only signals at cascade-eligible levels (overall >= 75) so the
    # 70-74 overflow tier doesn't dilute the regime signal.
    count_map_call = {}
    if STRESS_MODE == 'count':
        for s in call_sigs:
            if s.overall >= 75:
                count_map_call[s.date] = count_map_call.get(s.date, 0) + 1
        print(f"  STRESS_MODE=count: count_map_call has {len(count_map_call)} dates, "
              f"threshold={STRESS_COUNT_THRESHOLD_CALL}")

    # Gamma x IV Phase B: snapshot the process-global IV hit/miss/staleness
    # counters so the coverage emitted below after put-precompute is THIS
    # WINDOW's delta only (module counters otherwise accumulate across every
    # window this process prepares in the sweep's main loop). No-op
    # bookkeeping when IV_PREMIUM=0.
    _iv_h0, _iv_m0 = _IV_HITS, _IV_MISSES
    _iv_s0_0, _iv_s13_0, _iv_s47_0, _iv_s814_0 = (
        _IV_STALE_0D, _IV_STALE_1_3D, _IV_STALE_4_7D, _IV_STALE_8_14D)
    # IV Premium MODEL -- Amendment (2026-07-12, FABLE): same delta-snapshot
    # pattern as the panel counters above, for the model's own hit/miss/clamp/
    # ratio counters (DESIGN.md AMENDMENTS A3 telemetry). No-op when IV_MODEL=0.
    _model_h0, _model_m0 = _MODEL_HITS, _MODEL_MISSES
    _model_cf0, _model_cc0 = _MODEL_CLAMP_FLOOR_HITS, _MODEL_CLAMP_CAP_HITS
    _model_ratio_n0 = len(_MODEL_RATIOS)
    # VEGA_STATE: same delta-snapshot pattern (P1.4). No-op bookkeeping when
    # VEGA_STATE=0 (dead-hold walk never calls _vega_state_ratio, counters
    # stay at 0 for the whole run).
    _vs_h0, _vs_m0 = _VEGA_STATE_HITS, _VEGA_STATE_MISSES

    print("Precomputing call outcomes...", end=' ', flush=True)
    call_outcomes = precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map,
                                        ern_map=ern_map_for_iv, trading_days=trading_days,
                                        count_map=count_map_call if STRESS_MODE == 'count' else None)
    both_n = sum(1 for o in call_outcomes.values() if o['kind'] == 'both')
    tp_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'tp')
    sl_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'sl')
    hard_only = sum(1 for o in call_outcomes.values() if o['kind'] == 'hard')
    ct = len(call_outcomes) or 1
    print(f"N={len(call_outcomes)}  TP={tp_only/ct*100:.1f}%  SL={sl_only/ct*100:.1f}%  "
          f"Both={both_n/ct*100:.1f}%  Hard={hard_only/ct*100:.1f}%")

    routed_15_keys = set()
    if DTE_ROUTER_ENABLED and DTE_ROUTER_TARGET_DTE == 15 and call_sigs:
        print("Precomputing routed 15DTE call outcomes...", end=' ', flush=True)
        router_j_map = _load_dte_router_j_map({s.symbol_id for s in call_sigs})
        call_outcomes_15 = _mc15.precompute_outcomes(
            call_sigs,
            ph,
            breadth_dates,
            breadth_map,
            ern_map=ern_map_for_iv,
            j_map=router_j_map,
            trading_days=trading_days,
        )
        routed_by_date = defaultdict(int)
        routed_missing = 0
        for sig in sorted(call_sigs, key=lambda s: (s.date, -int(s.overall), str(s.symbol_id))):
            key = (sig.symbol_id, sig.date)
            if key not in call_outcomes:
                continue
            if not _dte_router_call_eligible(
                sig,
                dte_router_dates,
                dte_router_vix_map,
                dte_router_regime_map,
                routed_by_date[sig.date],
            ):
                continue
            routed_15 = call_outcomes_15.get(key)
            if routed_15 is None:
                routed_missing += 1
                continue
            tagged = dict(routed_15)
            tagged['_dte'] = '15'
            call_outcomes[key] = tagged
            routed_by_date[sig.date] += 1
            routed_15_keys.add(key)
        if routed_15_keys:
            rebuilt_calls_by_date = defaultdict(list)
            for d, entries in calls_by_date.items():
                for sid, score, key, ct_tag_value, ern in entries:
                    alloc_score = min(score, DTE_ROUTER_ALLOC_SCORE_CAP) if key in routed_15_keys else score
                    rebuilt_calls_by_date[d].append((sid, alloc_score, key, ct_tag_value, ern))
            calls_by_date = rebuilt_calls_by_date
        print(f"N={len(call_outcomes_15):,} routed={len(routed_15_keys):,} missing={routed_missing:,}")

    print("Precomputing put outcomes... ", end=' ', flush=True)
    put_outcomes = precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map,
                                           ern_map=ern_map_for_iv, trading_days=trading_days)
    pt_tp = sum(1 for o in put_outcomes.values() if o['kind'] == 'tp')
    pt_sl = sum(1 for o in put_outcomes.values() if o['kind'] == 'sl')
    pt_both = sum(1 for o in put_outcomes.values() if o['kind'] == 'both')
    pt_hard = sum(1 for o in put_outcomes.values() if o['kind'] == 'hard')
    pt = len(put_outcomes) or 1
    print(f"N={len(put_outcomes)}  TP={pt_tp/pt*100:.1f}%  SL={pt_sl/pt*100:.1f}%  "
          f"Both={pt_both/pt*100:.1f}%  Hard={pt_hard/pt*100:.1f}%")

    # Gamma x IV Phase B: emit this window's IV coverage (delta since _iv_h0/_iv_m0
    # above) to stdout, and to IV_COVERAGE_DIR as a small JSON if set, so a sweep's
    # per-window coverage can be read back programmatically by results collectors.
    # Amendment 2 (2026-07-10): as-of join staleness histogram (0d exact / 1-3d /
    # 4-7d / 8-14d) reported alongside hits/misses.
    if IV_PREMIUM:
        _iv_dh, _iv_dm = _IV_HITS - _iv_h0, _IV_MISSES - _iv_m0
        _iv_dt = _iv_dh + _iv_dm
        if _iv_dt:
            _iv_d0   = _IV_STALE_0D    - _iv_s0_0
            _iv_d13  = _IV_STALE_1_3D  - _iv_s13_0
            _iv_d47  = _IV_STALE_4_7D  - _iv_s47_0
            _iv_d814 = _IV_STALE_8_14D - _iv_s814_0
            print(f"  [IV_PREMIUM] window={label} hits={_iv_dh} misses={_iv_dm} "
                  f"coverage={_iv_dh/_iv_dt*100:.1f}% "
                  f"stale[0d={_iv_d0} 1-3d={_iv_d13} 4-7d={_iv_d47} 8-14d={_iv_d814}]")
            if IV_COVERAGE_DIR:
                import json as _json
                os.makedirs(IV_COVERAGE_DIR, exist_ok=True)
                _wp = os.path.join(IV_COVERAGE_DIR, f"ivwin_mc_{label}_{os.getpid()}.json")
                with open(_wp, 'w') as _f:
                    _json.dump({'window': label, 'hits': _iv_dh, 'misses': _iv_dm,
                               'coverage_pct': _iv_dh / _iv_dt * 100.0,
                               'stale_0d': _iv_d0, 'stale_1_3d': _iv_d13,
                               'stale_4_7d': _iv_d47, 'stale_8_14d': _iv_d814}, _f)

    # IV Premium MODEL -- Amendment (2026-07-12, FABLE, DESIGN.md AMENDMENTS A3):
    # this window's model hit/miss/clamp counts + the model/RV premium ratio
    # distribution (median + tercile means), so the eventual verdict can
    # attribute compression to a calm-cohort markup vs a crash-window VIX term
    # (DESIGN.md section 5). Pre-registered dose bar >=95% -- a WARNING is
    # printed below it (does not block the run; the A/B driver / verdict reader
    # must not read outcomes off a doubted dose per DESIGN.md section 9).
    if IV_PREMIUM and IV_MODEL:
        _model_dh, _model_dm = _MODEL_HITS - _model_h0, _MODEL_MISSES - _model_m0
        _model_dt = _model_dh + _model_dm
        if _model_dt:
            _model_dcf = _MODEL_CLAMP_FLOOR_HITS - _model_cf0
            _model_dcc = _MODEL_CLAMP_CAP_HITS - _model_cc0
            _dose_pct = _model_dh / _model_dt * 100.0
            _ratio_slice = _MODEL_RATIOS[_model_ratio_n0:]
            _median = _t_means = None
            if _ratio_slice:
                _sorted = sorted(_ratio_slice)
                _n = len(_sorted)
                _median = (_sorted[_n // 2] if _n % 2
                          else 0.5 * (_sorted[_n // 2 - 1] + _sorted[_n // 2]))
                _b1 = _sorted[: _n // 3] or _sorted
                _b2 = _sorted[_n // 3: 2 * _n // 3] or _sorted
                _b3 = _sorted[2 * _n // 3:] or _sorted
                _t_means = [sum(_b) / len(_b) for _b in (_b1, _b2, _b3)]
                print(f"  [IV_MODEL] window={label} model_hits={_model_dh} "
                      f"model_misses={_model_dm} dose={_dose_pct:.1f}% "
                      f"clamp_floor={_model_dcf} clamp_cap={_model_dcc} "
                      f"ratio_median={_median:.3f} "
                      f"ratio_tercile_means=[{_t_means[0]:.3f},{_t_means[1]:.3f},{_t_means[2]:.3f}]")
            else:
                print(f"  [IV_MODEL] window={label} model_hits={_model_dh} "
                      f"model_misses={_model_dm} dose={_dose_pct:.1f}% "
                      f"clamp_floor={_model_dcf} clamp_cap={_model_dcc}")
            if _dose_pct < 95.0:
                print(f"  [IV_MODEL] WARNING window={label}: dose {_dose_pct:.1f}% "
                      f"< 95% pre-registered bar -- COVERAGE-BLOCKED, do not read outcomes")
            if IV_COVERAGE_DIR:
                import json as _json
                os.makedirs(IV_COVERAGE_DIR, exist_ok=True)
                _wp = os.path.join(IV_COVERAGE_DIR, f"ivmodel_win_mc_{label}_{os.getpid()}.json")
                with open(_wp, 'w') as _f:
                    _json.dump({'window': label, 'model_hits': _model_dh,
                               'model_misses': _model_dm, 'dose_pct': _dose_pct,
                               'clamp_floor_hits': _model_dcf, 'clamp_cap_hits': _model_dcc,
                               'ratio_median': _median, 'ratio_tercile_means': _t_means,
                               'ratio_n': len(_ratio_slice)}, _f)

    # VEGA_STATE (P1.4): this window's dead-hold-walk VIX-lookup hit/miss
    # delta. "Hit" = both entry-VIX and current-bar-VIX resolved (ratio
    # computed from the calibration); "miss" = ratio defaulted to 1.0. Only
    # meaningful when DEAD_HOLD_ENABLED fires at all (SL-fire subset), so a
    # near-zero hit+miss total on a calm window is expected, not a bug.
    if VEGA_STATE:
        _vs_dh, _vs_dm = _VEGA_STATE_HITS - _vs_h0, _VEGA_STATE_MISSES - _vs_m0
        _vs_dt = _vs_dh + _vs_dm
        if _vs_dt:
            print(f"  [VEGA_STATE] window={label} hits={_vs_dh} misses={_vs_dm} "
                  f"coverage={_vs_dh/_vs_dt*100:.1f}% (of dead-hold-walk bars)")

    # Bundle every prepared input. _simulate_window reads ONLY from this ctx plus
    # the live module-global sizing knobs; nothing else from PREPARE leaks in.
    return {
        'label': label, 'd_start': d_start, 'd_end': d_end, 'version': version,
        'trading_days': trading_days,
        'calls_by_date': calls_by_date, 'call_outcomes': call_outcomes,
        'puts_by_date': puts_by_date, 'put_outcomes': put_outcomes,
        'regime_dates': regime_dates, 'regime_map': regime_map,
        'close_by_sym_idx': close_by_sym_idx,
        'rxdd_vix_dates': rxdd_vix_dates, 'rxdd_vix_map': rxdd_vix_map,
        'mwdd_mcc_dates': mwdd_mcc_dates, 'mwdd_mcc_map': mwdd_mcc_map,
        'tvdd_trin_dates': tvdd_trin_dates, 'tvdd_trin_map': tvdd_trin_map,
        'vxmd_whist_dates': vxmd_whist_dates, 'vxmd_whist_map': vxmd_whist_map,
        'bdiv_dates': bdiv_dates, 'bdiv_map': bdiv_map,
        'breadth_dates': breadth_dates, 'breadth_map': breadth_map,
    }


def _simulate_window(ctx, cell_params=None, persist=None, pool=None):
    """SIMULATE step: run N MC iterations against a prepared ctx + the live module
    globals (TIER_ALLOC, PUT_TIER_ALLOC, MAX_POSITIONS, MAX_POSITIONS_CALL,
    MAX_POSITIONS_PUT, STARTING_CASH, N_ITER). Returns {'seeded': result}. Seeds
    depend only on the window label + iteration index (NOT on cell params), and each
    iteration spins a fresh random.Random(seed), so results are bit-identical to the
    standalone path regardless of how many cells are simulated against this ctx.

    cell_params (optional): per-cell sizing knobs applied via _apply_cell_params in
    THIS process AND propagated to MP workers (Windows-spawn safe). When None, the
    live module globals are used unchanged (single-config path).

    pool (optional): a pre-built window-level MP pool from _make_window_pool whose
    workers already hold this ctx (pickled ONCE per window). When given, the ctx is
    NOT re-pickled and workers are NOT re-spawned — cell_params are broadcast to the
    live workers via _broadcast_cell_params, then seeds are mapped. This is the
    load-once sweep's ~10-30x win. When None, a fresh pool is built+torn-down here
    (single-config path, byte-identical to before). The caller owns the lifecycle of
    a passed-in pool (must close/join it after the last cell).

    persist (optional): override the DB-persist decision. None = honor the
    MC_NO_DB_PERSIST env var (default behavior); False = never persist (sweep use)."""
    if cell_params is not None:
        _apply_cell_params(cell_params)
    label            = ctx['label']
    d_start          = ctx['d_start']
    d_end            = ctx['d_end']
    version          = ctx['version']
    trading_days     = ctx['trading_days']
    calls_by_date    = ctx['calls_by_date']
    call_outcomes    = ctx['call_outcomes']
    puts_by_date     = ctx['puts_by_date']
    put_outcomes     = ctx['put_outcomes']
    regime_dates     = ctx['regime_dates']
    regime_map       = ctx['regime_map']
    close_by_sym_idx = ctx['close_by_sym_idx']
    rxdd_vix_dates   = ctx['rxdd_vix_dates']
    rxdd_vix_map     = ctx['rxdd_vix_map']
    mwdd_mcc_dates   = ctx['mwdd_mcc_dates']
    mwdd_mcc_map     = ctx['mwdd_mcc_map']
    tvdd_trin_dates  = ctx['tvdd_trin_dates']
    tvdd_trin_map    = ctx['tvdd_trin_map']
    vxmd_whist_dates = ctx['vxmd_whist_dates']
    vxmd_whist_map   = ctx['vxmd_whist_map']
    bdiv_dates       = ctx['bdiv_dates']
    bdiv_map         = ctx['bdiv_map']
    breadth_dates    = ctx['breadth_dates']
    breadth_map      = ctx['breadth_map']

    print(f"\n{'Mode':<13}  {'CTP%':>5}  {'PTP%':>5}  {'CTrd':>6}  {'PTrd':>6}  "
          f"{'MeanRet':>14}  {'MedRet':>14}  {'WorstDD':>8}  {'MeanDD':>7}  {'P(col)':>7}")
    print('-'*120)

    # Multiprocessing across iterations: ~8x speedup on 8-core CPU.
    # Each iteration is independent (different RNG, different intraday-fill
    # realization). Per-iter RNG dispersion replaces the old 3-mode
    # Conservative/Realistic/Optimistic system — single seeded run gives
    # the variance distribution.
    USE_MP = os.environ.get('MC_NO_MP', '0') != '1'
    n_workers = int(os.environ.get('MC_WORKERS', '0')) or os.cpu_count() or 4

    # Pool lifecycle:
    #  - external `pool` passed in (load-once sweep): reuse it. ctx is ALREADY
    #    pickled into its workers; broadcast cell_params to them, then map seeds.
    #    The caller owns close/join. n_workers comes from the pool size.
    #  - no external pool: build a local pool here (single-config path), use it,
    #    and close/join it at the end — byte-identical to before.
    _own_pool = pool is None
    if not _own_pool:
        # Reused external pool: use ITS actual worker count for the broadcast
        # target + chunksize (the env value may differ from how the caller sized it).
        n_workers = getattr(pool, '_processes', None) or n_workers
        _broadcast_cell_params(pool, n_workers, cell_params)
    elif USE_MP and N_ITER >= 16:
        n_workers = min(n_workers, N_ITER)
        pool = multiprocessing.Pool(
            processes=n_workers,
            initializer=_mc_init_worker,
            initargs=(trading_days, calls_by_date, call_outcomes,
                      puts_by_date, put_outcomes, regime_dates, regime_map,
                      close_by_sym_idx, rxdd_vix_dates, rxdd_vix_map,
                      mwdd_mcc_dates, mwdd_mcc_map,
                      tvdd_trin_dates, tvdd_trin_map,
                      vxmd_whist_dates, vxmd_whist_map,
                      bdiv_dates, bdiv_map,
                      breadth_dates, breadth_map, cell_params),
        )

    finals=[]; dds=[]; ctps=[]; ptps=[]; ctrd=[]; ptrd=[]; collapses=0
    seeds = [1000 * _stable_label_seed(label) + it for it in range(N_ITER)]
    if pool is not None:
        rs = pool.map(_mc_iter_worker, seeds, chunksize=max(1, N_ITER // (n_workers * 4)))
    else:
        rs = []
        for s in seeds:
            rng = random.Random(s)
            rs.append(run_single_sim(trading_days, calls_by_date, call_outcomes,
                                     puts_by_date, put_outcomes, rng,
                                     regime_dates, regime_map,
                                     vix_dates=rxdd_vix_dates, vix_map=rxdd_vix_map,
                                     mcc_dates=mwdd_mcc_dates, mcc_map=mwdd_mcc_map,
                                     trin_dates=tvdd_trin_dates, trin_map=tvdd_trin_map,
                                     whist_dates=vxmd_whist_dates, whist_map=vxmd_whist_map,
                                     bdiv_dates=bdiv_dates, bdiv_map=bdiv_map,
                                     breadth_dates=breadth_dates, breadth_map=breadth_map,
                                     collect_tape=TRADE_TAPE_ENABLED,
                                     close_by_sym_idx=close_by_sym_idx))
    for r in rs:
        finals.append(r['final']); dds.append(r['max_dd'])
        ctps.append(r['call_tp']); ptps.append(r['put_tp'])
        ctrd.append(r['call_trades']); ptrd.append(r['put_trades'])
        if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD:
            collapses += 1

    # Dump trade tape + DD episodes to parquet (one file per window)
    if TRADE_TAPE_ENABLED:
        _dump_trade_tape(label, seeds, rs, trading_days)

    mean_ret = (statistics.mean(finals) / STARTING_CASH - 1) * 100
    med_ret  = (statistics.median(finals) / STARTING_CASH - 1) * 100
    mean_dd  = statistics.mean(dds) * 100
    worst_dd = max(dds) * 100
    p_coll   = collapses / N_ITER * 100

    # Quartile metrics for distribution-shape display on the dashboard.
    finals_sorted = sorted(finals)
    def _pct(seq, q):
        if not seq:
            return None
        i = max(0, min(len(seq) - 1, int(round(q * (len(seq) - 1)))))
        return seq[i]
    p25_final = _pct(finals_sorted, 0.25)
    p75_final = _pct(finals_sorted, 0.75)
    p25_ret_pct = (p25_final / STARTING_CASH - 1) * 100 if p25_final is not None else None
    p75_ret_pct = (p75_final / STARTING_CASH - 1) * 100 if p75_final is not None else None
    med_dd_pct  = statistics.median(dds) * 100 if dds else None

    result = dict(
        mean_ret=mean_ret, med_ret=med_ret,
        mean_dd=mean_dd, worst_dd=worst_dd, p_coll=p_coll,
        call_tp=statistics.mean(ctps), put_tp=statistics.mean(ptps),
        call_trades=statistics.mean(ctrd), put_trades=statistics.mean(ptrd),
        mean_final=statistics.mean(finals),
    )
    for key in (
        'avg_open_premium_pct',
        'max_open_premium_pct',
        'avg_open_premium_base_pct',
        'max_open_premium_base_pct',
        'avg_call_open_premium_base_pct',
        'avg_put_open_premium_base_pct',
        'avg_saturation_scale',
        'avg_reserved_cash_pct',
        'max_reserved_cash_pct',
    ):
        vals = [r.get(key) for r in rs if r.get(key) is not None]
        if vals:
            result[key] = statistics.mean(vals)
    # Opt-in per-iteration arrays for survival / probability-of-dominance analysis
    # (default OFF so normal runs + DB persist payloads are unchanged).
    if os.environ.get('MC_RETURN_PATHS') == '1':
        result['finals'] = list(finals)
        result['dds'] = list(dds)
        result['t1m_bars'] = [r.get('t1m_bar') for r in rs]
        result['t2x_bars'] = [r.get('t2x_bar') for r in rs]
        result['t_50dd_bars'] = [r.get('t_50dd_bar') for r in rs]
        result['n_trading_days'] = len(trading_days)
        result['starting_cash'] = STARTING_CASH
        # Track A — per-iteration WEEKLY equity curves (only present when
        # MC_EMIT_CURVE=1 produced run_single_sim 'eq_weekly'; else all None).
        if MC_EMIT_CURVE:
            result['eq_weeklies'] = [r.get('eq_weekly') for r in rs]
    print(f"{'seeded':<13}  {result['call_tp']:>4.1f}%  {result['put_tp']:>4.1f}%  "
          f"{result['call_trades']:>5.1f}  {result['put_trades']:>5.1f}  "
          f"{mean_ret:>+13.1f}%  {med_ret:>+13.1f}%  "
          f"{worst_dd:>7.1f}%  {mean_dd:>6.1f}%  {p_coll:>6.1f}%")

    # Only tear down a pool WE built. An external (reused) pool is the caller's
    # to close after the last cell of the window.
    if pool is not None and _own_pool:
        pool.close()
        pool.join()

    # Persist this window's result to the DB so the Assessment page can
    # surface it without re-running. Best-effort — skipped if MC_NO_DB_PERSIST=1
    # is set (sweep scripts that don't want to pollute the table can opt out).
    # `persist=False` (in-process sweep) forces no-persist regardless of env.
    _do_persist = (os.environ.get('MC_NO_DB_PERSIST', '0') != '1') if persist is None else bool(persist)
    if _do_persist:
        try:
            import sys as _sys
            from database.utils.mc_persist import (
                persist_mc_window_result, canonical_params_30dte,
            )
            params_snapshot = canonical_params_30dte(_sys.modules[__name__])
            persist_mc_window_result(
                version=version,
                dte_strategy='30',
                window_label=label,
                window_start=d_start,
                window_end=d_end,
                n_iter=N_ITER,
                engine='seeded',
                engine_version='bounded-fill',
                result=result,
                params=params_snapshot,
                starting_cash=STARTING_CASH,
                p25_ret_pct=p25_ret_pct,
                p75_ret_pct=p75_ret_pct,
                med_dd_pct=med_dd_pct,
            )
        except Exception as _e:
            # Persistence failure must never break the MC run itself.
            print(f"  [warn] MC persist skipped: {_e}")

    # Return results dict keyed by single mode for back-compat with downstream
    # summary tables that loop over keys. 'seeded' is the only mode now.
    return {'seeded': result}


def run_window(label, d_start, d_end, version):
    """Single-config window runner: PREPARE once, SIMULATE once. Byte-identical
    behavior to the pre-split implementation (this is the only path used by main())."""
    ctx = _prepare_window(label, d_start, d_end, version)
    return _simulate_window(ctx)


# ---- Main -------------------------------------------------------------------

def main():
    print('='*100)
    print("MONTE CARLO - Canonical Optimal Strategy")
    print('='*100)
    print(f"Strategy : 30 DTE | breadth-adaptive (brd<=50) | Hard=-50%@day15")
    print(f"  Calls TP: +30% base / +35% stressed  (h30->35 Regime-TP sweep 2026-04-16)")
    print(f"  Calls SL: -35% base / -40% stressed  (h35->40 VIX/breadth decomp 2026-04-16)")
    print(f"  Puts TP : {PUT_TP:+.0%} fixed        (asym weekly+tight SL, 2026-04-17)")
    print(f"  Puts SL : {PUT_SL:+.0%} fixed")
    print(f"Slippage : entry -1.0% | TP 0% (limit sell) | SL -1.3% | Hard -0.5%")
    print(f"  NET_CTP: base={NET_TP_BASE:+.3f} stressed={NET_TP_STRESS:+.3f}")
    print(f"  NET_CSL: base={NET_SL_BASE:+.3f} stressed={NET_SL_STRESS:+.3f}")
    print(f"  NET_PTP: {PUT_NET_TP:+.3f}  NET_PSL: {PUT_NET_SL:+.3f}  NET_HD: {NET_HARD_SELL:+.3f}")
    print(f"C Alloc  : 95+=25%  85-94=15%  80-84=15%  75-79=15%  70-74=0% (disabled)")
    print(f"P Alloc  : <=15=15%  16-20=12%  21-25=12%  (shared 14-slot pool with calls)")
    print(f"MaxPos   : {MAX_POSITIONS}  (upgraded from 10; MaxPos sweep 2026-04-16, monte_carlo_maxpos_sweep.py)")
    if BREADTH_ALLOC_ENABLED:
        print(f"Alloc    : F3f breadth knob (shipped 2026-04-24, +121% 5y compound vs composite-driven)")
        print(f"           Calls scale 1.0 if brd>={F3F_CALL_THRESH:.0f} else linear to {F3F_CALL_FLOOR:.2f} at brd={F3F_CALL_LOW:.0f}")
        print(f"           Puts  scale 1.0 if brd<={F3F_PUT_THRESH:.0f} else linear to {F3F_PUT_FLOOR:.2f} at brd={F3F_PUT_HIGH:.0f}")
    else:
        print(f"Alloc    : Legacy regime_multiplier slope (CUT_ONLY: up={REGIME_SLOPE_UP} dn={REGIME_SLOPE_DOWN} put_up={REGIME_SLOPE_PUT_UP})")
    if DD_SOFT_BAND_HI > DD_SOFT_BAND_LO:
        print(f"DD soft  : H3 — scale call alloc 1.0->{DD_SOFT_CALL_FLOOR:.2f} linearly when DD in [{DD_SOFT_BAND_LO:.2f}, {DD_SOFT_BAND_HI:.2f}] (calls only; shipped 2026-05-04)")
    if LIQUIDITY_FLOOR > 0:
        print(f"Liquidity: floor={LIQUIDITY_FLOOR:.0f} c/d — drop 75+ CALL signals below trailing-30d avg option_volume_30d (calls only; Core-evidence-only staged ship-candidate 2026-08-07)")
    if PRACTICAL_EXPOSURE_ENABLED:
        print(
            f"Practical: base<=${PRACTICAL_CAPITAL_CEILING:,.0f} "
            f"gross<={GROSS_PREMIUM_CAP:.0%} call<={CALL_PREMIUM_CAP:.0%} put<={PUT_PREMIUM_CAP:.0%}; "
            f"sat call_ref={OPP_SAT_CALL_REF:.0f} put_ref={OPP_SAT_PUT_REF:.0f} "
            f"power={OPP_SAT_POWER:.2f} floor={OPP_SAT_FLOOR:.2f}"
        )
    if PUT_WAVE_RESERVE_ENABLED:
        print(f"Put wave : reserve enabled floor={PUT_WAVE_RESERVE_FLOOR:.2f} strength={PUT_WAVE_RESERVE_STRENGTH:.2f} daily_state={PUT_WAVE_RESERVE_DAILY_STATE or 'missing'}")
    print(f"         : Primary threshold: {PRIMARY_THRESHOLD}+  |  Overflow: {OVERFLOW_THRESHOLD}-74")
    print(f"Start    : ${STARTING_CASH:,.0f}  |  Iterations: {N_ITER}")
    print(f"Sigma    : TP base={TP_SIGMA_BASE:.3f}/stressed={TP_SIGMA_STRESS:.3f}  "
          f"SL base={SL_SIGMA_BASE:.3f}/stressed={SL_SIGMA_STRESS:.3f}")
    print(f"Modes    : seeded (single mode; per-iter random fill within bar replaces 3-mode collision)")
    if CT_PROMOTE:
        print(f"CT       : ENABLED  put_trend>={CT_PUT_TREND_MIN}->{CT_PUT_TIER}  "
              f"call_trend<={CT_CALL_TREND_MAX}->{CT_CALL_TIER}")
    else:
        print(f"CT       : OFF (baseline)")

    pin = os.environ.get('ALGORITHM_VERSION_PIN', '').strip()
    if pin:
        # Match by exact commit or unique prefix
        try:
            version = AlgorithmVersion.get(AlgorithmVersion.git_commit == pin)
        except AlgorithmVersion.DoesNotExist:
            cands = list(AlgorithmVersion.select().where(
                AlgorithmVersion.git_commit.startswith(pin)))
            if len(cands) != 1:
                raise SystemExit(f"ALGORITHM_VERSION_PIN={pin!r}: {len(cands)} matches")
            version = cands[0]
        print(f"\nAlgorithm version (PINNED): {version.git_commit} (id={version.id})")
    else:
        version = AlgorithmVersion.get_active_scores_version()
        print(f"\nAlgorithm version: {version.git_commit}")

    all_results = {}
    _windows = WINDOWS
    # Custom single arbitrary window (for monthly-rolling start-date sweeps).
    # WIN_START / WIN_END are ISO dates; WIN_LABEL names the row. When set, this
    # fully replaces the preset WINDOWS list. Default-OFF (byte-identical).
    _win_start = os.environ.get('WIN_START', '').strip()
    _win_end   = os.environ.get('WIN_END', '').strip()
    if _win_start and _win_end:
        from datetime import date as _date
        _wl = os.environ.get('WIN_LABEL', '').strip() or f'{_win_start}_{_win_end}'
        _windows = [(_wl, _date.fromisoformat(_win_start), _date.fromisoformat(_win_end))]
    elif WINDOWS_OVERRIDE:
        wanted = {x.strip() for x in WINDOWS_OVERRIDE.split(',') if x.strip()}
        _windows = [w for w in WINDOWS if w[0] in wanted]
    for label, d_start, d_end in _windows:
        all_results[label] = run_window(label, d_start, d_end, version)

    # Final summary table
    print('\n' + '='*100)
    print("SUMMARY - Mean Return / Worst DD / P(collapse) by Window")
    print('='*100)
    print(f"{'Window':<10} {'MeanRet':>20} {'MedRet':>16} {'WorstDD':>10} {'MeanDD':>9} {'P(coll)':>9}")
    print('-'*100)
    for label, _, _ in _windows:
        r = all_results[label]['seeded']
        print(f"{label:<10} {r['mean_ret']:>+18,.1f}%  "
              f"{r['med_ret']:>+14,.1f}%  "
              f"{r['worst_dd']:>9.1f}%  "
              f"{r['mean_dd']:>8.1f}%  "
              f"{r['p_coll']:>8.1f}%")

    # Optional machine-readable dump for A/B drivers (env-gated, harmless if unset)
    _rj = os.environ.get('MC_RESULTS_JSON', '').strip()
    if _rj:
        import json as _json
        _dump = {'_meta': {'calendar_hold': CALENDAR_HOLD,
                           'hold_cal_days': HOLD_CAL_DAYS,
                           'nominal_cal_dte': NOMINAL_CAL_DTE,
                           'hold_days': HOLD_DAYS, 'n_iter': N_ITER}}
        _emit_paths = os.environ.get('MC_RETURN_PATHS') == '1'
        for label, _, _ in _windows:
            r = all_results[label]['seeded']
            _dump[label] = {k: float(r[k]) for k in
                            ('mean_ret', 'med_ret', 'worst_dd', 'mean_dd', 'p_coll')
                            if k in r}
            # When MC_RETURN_PATHS=1, also carry the per-iteration arrays so an
            # external driver (subprocess) can compute first-passage / survival
            # metrics. None entries (milestone never reached) are preserved.
            if _emit_paths and 'finals' in r:
                _dump[label]['paths'] = {
                    'finals': [float(x) for x in r['finals']],
                    'dds': [float(x) for x in r['dds']],
                    't1m_bars': r.get('t1m_bars'),
                    't2x_bars': r.get('t2x_bars'),
                    't_50dd_bars': r.get('t_50dd_bars'),
                    'n_trading_days': r.get('n_trading_days'),
                    'starting_cash': r.get('starting_cash'),
                }
                # Track A — weekly equity curves for the equity-vs-market A/B
                # analyzer (present only when MC_EMIT_CURVE=1).
                if MC_EMIT_CURVE and r.get('eq_weeklies') is not None:
                    _dump[label]['paths']['eq_weeklies'] = r.get('eq_weeklies')
        with open(_rj, 'w') as _f:
            _json.dump(_dump, _f, indent=2)
        print(f"\n[MC_RESULTS_JSON] wrote {len(_dump) - 1} windows -> {_rj}")


if __name__ == '__main__':
    main()
