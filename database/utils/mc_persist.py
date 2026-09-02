"""Persist Monte Carlo per-window results to the `monte_carlo_run` table.

Called by `monte_carlo.py` and `monte_carlo_15dte.py` at the end of each
window's iteration loop so the dashboard can surface canonical N-iteration
results without re-running. Upserts on (version, dte, window_label, params_hash).

Why this lives in `database/utils/` rather than inline in monte_carlo.py:
- `monte_carlo_15dte.py` needs the same logic; one helper avoids drift.
- Multiprocessing-spawn workers re-import monte_carlo.py; the persistence
  helper is small and import-safe everywhere.
- Future alternative engines (e.g. a Numba-CUDA MC) can persist via the
  same entry point.
"""
import hashlib
import json
import subprocess
from datetime import date, datetime
from typing import Optional


def _canonical_params_hash(params: dict) -> str:
    """md5 of canonical-JSON-encoded params. Stable across run-to-run on the
    same config, so re-runs upsert. Two configs differing in any param produce
    different hashes -> different rows."""
    blob = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(blob.encode('utf-8')).hexdigest()


def _git_head_commit() -> Optional[str]:
    """Best-effort HEAD commit at run time. Returns None on any error so
    persistence never fails because git is unavailable."""
    try:
        out = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                      stderr=subprocess.DEVNULL,
                                      timeout=5)
        return out.decode('ascii').strip() or None
    except Exception:
        return None


def persist_mc_window_result(*,
                              version,                  # AlgorithmVersion
                              dte_strategy: str,        # '30' or '15'
                              window_label: str,
                              window_start: date,
                              window_end: date,
                              n_iter: int,
                              engine: str = 'seeded',
                              engine_version: str = 'bounded-fill',
                              result: dict,             # dict from run_window()
                              params: dict,             # canonical strategy snapshot
                              starting_cash: float = 50000.0,
                              runtime_sec: Optional[float] = None,
                              notes: Optional[str] = None,
                              p25_ret_pct: Optional[float] = None,
                              p75_ret_pct: Optional[float] = None,
                              med_dd_pct: Optional[float] = None) -> int:
    """Upsert one window's MC result. Returns the persisted row id.

    `result` must contain (at minimum) the keys produced by monte_carlo.py's
    run_window():
        mean_ret, med_ret, mean_dd, worst_dd, p_coll,
        call_tp, put_tp, call_trades, put_trades, mean_final

    `params` is the canonical strategy snapshot used for the params_hash.
    Include EVERY param the run depends on (TP/SL/cascade/MaxPos/F3F/DD-breaker/
    dead-hold/hold-days/breadth-threshold/etc.) so re-running with a tweaked
    sweep variable produces a new row instead of overwriting.
    """
    # Lazy import so this module is import-safe even if peewee isn't ready
    # (e.g. inside an MP worker that hasn't initialized DB yet — workers
    # don't persist; only the parent process does).
    from database.models.core import MonteCarloRun
    MonteCarloRun.ensure_schema()

    params_hash = _canonical_params_hash(params)
    git_commit  = _git_head_commit()

    # Upsert by (version, dte, window_label, params_hash). Peewee doesn't have
    # a clean atomic upsert across all backends, so we delete-then-create —
    # safe because the unique key gates duplicates anyway.
    MonteCarloRun.delete().where(
        (MonteCarloRun.version == version)
        & (MonteCarloRun.dte_strategy == dte_strategy)
        & (MonteCarloRun.window_label == window_label)
        & (MonteCarloRun.params_hash == params_hash)
    ).execute()

    row = MonteCarloRun.create(
        version=version,
        dte_strategy=dte_strategy,
        window_label=window_label,
        window_start=window_start,
        window_end=window_end,
        engine=engine,
        engine_version=engine_version,
        n_iter=n_iter,
        params_hash=params_hash,
        params_json=json.dumps(params, sort_keys=True, default=str),
        git_commit_at_run=git_commit,
        mean_ret_pct=result.get('mean_ret'),
        med_ret_pct=result.get('med_ret'),
        p25_ret_pct=p25_ret_pct,
        p75_ret_pct=p75_ret_pct,
        mean_final_usd=result.get('mean_final'),
        worst_dd_pct=result.get('worst_dd'),
        mean_dd_pct=result.get('mean_dd'),
        med_dd_pct=med_dd_pct,
        collapse_rate_pct=result.get('p_coll'),
        call_tp_rate_pct=result.get('call_tp'),
        put_tp_rate_pct=result.get('put_tp'),
        n_call_trades=result.get('call_trades'),
        n_put_trades=result.get('put_trades'),
        starting_cash=starting_cash,
        runtime_sec=runtime_sec,
        notes=notes,
        run_at=datetime.now(),
    )
    return row.id


def canonical_params_30dte(strategy_module) -> dict:
    """Build the canonical params snapshot for `monte_carlo.py` (30 DTE).

    Reads from the live module (post-env-var-override) so a sweep that
    sets e.g. TP_BASE_OV=0.40 produces a distinct hash. Strategy params
    that affect outcomes go here; non-deterministic things (random seeds,
    runtime) stay out.
    """
    sm = strategy_module
    return {
        'dte_strategy':       '30',
        'engine':             'seeded',
        'engine_version':     'bounded-fill',
        'n_iter':             getattr(sm, 'N_ITER', None),
        'starting_cash':      getattr(sm, 'STARTING_CASH', None),
        # Exits
        'tp_base':            getattr(sm, 'TP_BASE', None),
        'tp_stress':          getattr(sm, 'TP_STRESS', None),
        'sl_base':            getattr(sm, 'SL_BASE', None),
        'sl_stress':          getattr(sm, 'SL_STRESS', None),
        'put_tp':             getattr(sm, 'PUT_TP', None),
        'put_sl':             getattr(sm, 'PUT_SL', None),
        'hard_sell_loss':     getattr(sm, 'HARD_SELL_LOSS', None),
        'breadth_threshold':  getattr(sm, 'BREADTH_THRESHOLD', None),
        'hold_days':          getattr(sm, 'HOLD_DAYS', None),
        'premium_mult':       getattr(sm, 'PREMIUM_MULT', None),
        # Cascade
        'tier_alloc':         dict(getattr(sm, 'TIER_ALLOC', {}) or {}),
        'put_tier_alloc':     dict(getattr(sm, 'PUT_TIER_ALLOC', {}) or {}),
        'max_positions':      getattr(sm, 'MAX_POSITIONS', None),
        'primary_threshold':  getattr(sm, 'PRIMARY_THRESHOLD', None),
        'overflow_threshold': getattr(sm, 'OVERFLOW_THRESHOLD', None),
        'put_threshold':      getattr(sm, 'PUT_THRESHOLD', None),
        # F3F breadth knob
        'breadth_alloc_enabled': getattr(sm, 'BREADTH_ALLOC_ENABLED', None),
        'f3f_call_thresh':    getattr(sm, 'F3F_CALL_THRESH', None),
        'f3f_call_floor':     getattr(sm, 'F3F_CALL_FLOOR', None),
        'f3f_call_low':       getattr(sm, 'F3F_CALL_LOW', None),
        'f3f_put_thresh':     getattr(sm, 'F3F_PUT_THRESH', None),
        'f3f_put_floor':      getattr(sm, 'F3F_PUT_FLOOR', None),
        'f3f_put_high':       getattr(sm, 'F3F_PUT_HIGH', None),
        # Regime slope (legacy fallback)
        'regime_slope_up':       getattr(sm, 'REGIME_SLOPE_UP', None),
        'regime_slope_down':     getattr(sm, 'REGIME_SLOPE_DOWN', None),
        'regime_slope_put_up':   getattr(sm, 'REGIME_SLOPE_PUT_UP', None),
        'regime_slope_put_down': getattr(sm, 'REGIME_SLOPE_PUT_DOWN', None),
        'alloc_scale_floor':     getattr(sm, 'ALLOC_SCALE_FLOOR', None),
        'alloc_scale_ceil':      getattr(sm, 'ALLOC_SCALE_CEIL', None),
        # DD breaker + soft band + dead-hold
        'dd_circuit_breaker':    getattr(sm, 'DD_CIRCUIT_BREAKER', None),
        'dd_soft_band_lo':       getattr(sm, 'DD_SOFT_BAND_LO', None),
        'dd_soft_band_hi':       getattr(sm, 'DD_SOFT_BAND_HI', None),
        'dd_soft_call_floor':    getattr(sm, 'DD_SOFT_CALL_FLOOR', None),
        'dead_hold_enabled':     getattr(sm, 'DEAD_HOLD_ENABLED', None),
        'dead_hold_trigger':     getattr(sm, 'DEAD_HOLD_TRIGGER_PNL', None),
        'dead_hold_popout':      getattr(sm, 'DEAD_HOLD_POPOUT_PNL', None),
        # Portfolio mechanisms
        'ct_promote':            getattr(sm, 'CT_PROMOTE', None),
        'earn_supp_put':         getattr(sm, 'EARN_SUPP_PUT', None),
        'earn_boost_call':       getattr(sm, 'EARN_BOOST_CALL', None),
        'weak_weekly_call_drop': getattr(sm, 'WEAK_WEEKLY_CALL_DROP', None),
        # Slippage (currently zero on Wealthsimple)
        'slip_entry':            getattr(sm, 'SLIP_ENTRY', None),
        'slip_tp':               getattr(sm, 'SLIP_TP', None),
        'slip_sl':                getattr(sm, 'SLIP_SL', None),
        'slip_hard':             getattr(sm, 'SLIP_HARD', None),
        # SAW Put U-curve (30 DTE only)
        'saw_put_ucurve_enabled': getattr(sm, 'SAW_PUT_UCURVE_ENABLED', None),
        'saw_put_ucurve_mid':     getattr(sm, 'SAW_PUT_UCURVE_MID', None),
        'saw_put_ucurve_hw':      getattr(sm, 'SAW_PUT_UCURVE_HW', None),
        'saw_put_ucurve_floor':   getattr(sm, 'SAW_PUT_UCURVE_FLOOR', None),
        'saw_put_ucurve_ceil':    getattr(sm, 'SAW_PUT_UCURVE_CEIL', None),
        'saw_put_ucurve_power':   getattr(sm, 'SAW_PUT_UCURVE_POWER', None),
    }


def canonical_params_15dte(strategy_module) -> dict:
    """Build the canonical params snapshot for `monte_carlo_15dte.py` (15 DTE).

    Same shape as 30 DTE but with 15-DTE-specific fields (PUT_SL_HOLD_BARS,
    DD_CIRCUIT_BREAKER which is the binary stop). SAW_PUT_UCURVE is 30-DTE
    only — 15 DTE doesn't have it wired.
    """
    p = canonical_params_30dte(strategy_module)
    p['dte_strategy'] = '15'
    sm = strategy_module
    p['put_sl_hold_bars_default'] = getattr(sm, 'PUT_SL_HOLD_BARS_DEFAULT', None)
    p['put_sl_hold_bars_monday']  = getattr(sm, 'PUT_SL_HOLD_BARS_MONDAY', None)
    # 15 DTE has no SAW Put U-curve — drop those keys to avoid stale defaults
    # affecting the hash.
    for k in list(p.keys()):
        if k.startswith('saw_put_ucurve_'):
            p.pop(k, None)
    return p
