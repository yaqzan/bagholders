"""
mc_patch.py -- reusable in-process patch helpers for the TP/SL Phase A sweep
(experiments/tpsl_refine_2026_08/PREREG.md section 7 "Execution mechanics").

HARD RULE: this module NEVER edits monte_carlo.py / strategy_config.py / any
tracked production file. Every effect here is either:
  (a) an os.environ mutation, read by monte_carlo.py's own existing env-var
      scaffolding (the *_OV / *_OVERRIDE knobs it already supports), or
  (b) post-import monkey-patching of the ALREADY-IMPORTED `mc` module object
      (reassigning module globals / functions the same way
      monte_carlo.py's own `_apply_cell_params` does).

IMPORT ORDER MATTERS. Several knobs (TIER_ALLOC, MAX_POSITIONS, GROSS_PREMIUM_CAP/
CALL_PREMIUM_CAP, NOMINAL_CAL_DTE, HOLD_CAL_DAYS, ALGORITHM_VERSION_PIN) are read
from os.environ ONCE, at `import monte_carlo` time (verified against source,
2026-08-10 recon):
  - TIER_ALLOC / PUT_TIER_ALLOC are built as module-level dicts via `_env_alloc()`
    at monte_carlo.py:1329-1339 -- there is no later re-read, so setting
    TIER_ULTRA_OV etc AFTER import is a silent no-op.
  - GROSS_PREMIUM_CAP / CALL_PREMIUM_CAP are gated through `_practical_default()`
    (monte_carlo.py:1189-1200): when PRACTICAL_EXPOSURE_ENABLED (True in the
    shipped Core config -- verified live) the *default* comes from
    strategy_config's CORE value (0.50), so an Apex arm that forgets to set
    these EXPLICITLY silently inherits Core's 50% cap.
So: `apply_frozen_pins()` + `apply_profile_env()` + `resolve_and_pin_version()`
must ALL run before `import monte_carlo as mc`; the phaseA_run.py entrypoint
enforces that ordering.

TP/SL themselves are the one exception -- they're read LIVE (module globals
re-read at call time inside compute_trade_outcome/run_single_sim), so
`set_tpsl()` below is a post-import patch, called once per cell, immediately
before that cell's `_prepare_window()`.
"""
from __future__ import annotations

import json
import os
import time

# ---------------------------------------------------------------------------
# PRE-IMPORT helpers -- call BEFORE `import monte_carlo`.
# ---------------------------------------------------------------------------

# Frozen recipe pins, PREREG.md section 7. MC_RETURN_PATHS is additive/inert on
# RNG (monte_carlo.py only reads it AFTER all simulation is complete, to decide
# whether to attach `finals`/`dds`/... arrays to the already-computed result
# dict) -- included here so per-iteration data is always captured, satisfying
# PREREG section 7's "per-iteration return summaries ... raw per-iter arrays to
# a compact parquet" requirement.
FROZEN_PINS = {
    'MC_NO_DB_PERSIST': '1',
    'LIQUIDITY_FLOOR': '0.0',
    'PYTHONIOENCODING': 'utf-8',
    'MC_RETURN_PATHS': '1',
}

# Apex arm env diff (PREREG section 7 "Apex arm env"), copied verbatim from the
# shipped P0.3 Option-B recipe (experiments/apex_dte_dd/run_p03_evidence.py
# OPTION_A_DIFF's DTE fields + OPTION_B_EXTRA_DIFF) -- this IS the live Apex
# profile as of the 2026-08-02 P0.3 ship. SL_BASE_OV/SL_STRESS_OV are
# deliberately excluded: SL comes from the swept cell (set_tpsl), never the
# profile.
APEX_ENV_DIFF = {
    'NOMINAL_CAL_DTE': '30',
    'HOLD_CAL_DAYS': '27',
    'TIER_ULTRA_OV': '0.10',
    'TIER_TOP_OV': '0.10',
    'TIER_MID_OV': '0.10',
    'TIER_LOW_OV': '0.10',
    'TIER_OVERFLOW_OV': '0',
    'MAX_POSITIONS_OVERRIDE': '10',
    'MAX_POSITIONS_CALL': '10',
    'GROSS_PREMIUM_CAP': '1.0',
    'CALL_PREMIUM_CAP': '1.0',
}


def apply_frozen_pins(max_workers=6):
    """Set the frozen recipe pins as os.environ, plus the sim-worker cap.
    MUST be called BEFORE `import monte_carlo`. Idempotent.

    Worker-cap mechanism: monte_carlo._simulate_window(ctx) -- called here
    WITHOUT an external `pool=` argument on every cell -- builds its OWN fresh
    multiprocessing.Pool sized `n_workers = int(os.environ.get('MC_WORKERS','0'))
    or os.cpu_count() or 4` (verified monte_carlo.py:4600-4628) and tears it
    down at the end of that same call (`_own_pool=True` since no pool was
    passed in). So (1) every cell already gets a FRESH pool with no manual
    _make_window_pool bookkeeping needed -- reuse-across-cells (the stale-
    barrier trap in LESSONS.md) is structurally impossible when no pool is
    ever held onto -- and (2) setting MC_WORKERS here caps each of those
    per-cell pools at `max_workers`, so 4 concurrent queue jobs spawn at most
    4 x max_workers OS processes rather than 4 x os.cpu_count().
    """
    for k, v in FROZEN_PINS.items():
        os.environ[k] = v
    os.environ['MC_WORKERS'] = str(int(max_workers))


def apply_profile_env(profile):
    """Set profile-specific overrides as os.environ. MUST be called BEFORE
    `import monte_carlo` (see module docstring). 'core' sets NOTHING beyond the
    frozen pins -- strategy_config.py's STRATEGY_30DTE defaults already ARE the
    Core profile (verified live 2026-08-10: TIER_ALLOC
    ultra/top/mid/low/overflow = .20/.15/.08/.03/0, MAX_POSITIONS=14,
    GROSS_PREMIUM_CAP=CALL_PREMIUM_CAP=0.50, CALENDAR_HOLD=True,
    HOLD_CAL_DAYS=27, NOMINAL_CAL_DTE=30). 'apex' applies APEX_ENV_DIFF."""
    profile = profile.lower().strip()
    if profile == 'core':
        return
    if profile == 'apex':
        for k, v in APEX_ENV_DIFF.items():
            os.environ[k] = v
        return
    raise ValueError(f"unknown profile {profile!r} (expected 'core' or 'apex')")


def resolve_and_pin_version(meta_path):
    """Resolve the ACTIVE AlgorithmVersion exactly ONCE for the whole Phase A
    campaign (first job to run wins) and persist {id, git_commit, resolved_at}
    to meta_path. Every later call (any job, any process, any restart) reads
    the pin back from disk instead of re-resolving live, so a ship landing
    mid-sweep can never silently shift which version's scores a still-running
    job reads. Sets os.environ['ALGORITHM_VERSION_PIN'] to the pinned
    git_commit -- monte_carlo.py's own PIN matching is BY GIT_COMMIT, not by
    bare numeric id (verified main(): `AlgorithmVersion.get(git_commit==pin)`
    with a startswith-prefix fallback). MUST be called BEFORE
    `import monte_carlo`. Returns the meta dict ({'id':.., 'git_commit':..,
    'resolved_at':..})."""
    if os.path.exists(meta_path):
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
    else:
        # Lazy import -- only this cold-start path needs DB connectivity, and
        # only after the caller has already put the repo root on sys.path.
        from database.models.core import AlgorithmVersion
        v = AlgorithmVersion.get_active_scores_version()
        meta = {
            'id': v.id,
            'git_commit': v.git_commit,
            'resolved_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        atomic_write_json(meta_path, meta)
    os.environ['ALGORITHM_VERSION_PIN'] = meta['git_commit']
    return meta


def atomic_write_json(path, obj):
    """Atomic (tmp + os.replace) JSON write -- safe for a --restartable queue
    job that may be killed and re-run at any point."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# POST-IMPORT helpers -- call AFTER `import monte_carlo as mc`, passing `mc`.
# ---------------------------------------------------------------------------

def set_tpsl(mc, tp, sl, tp_stress=None, sl_stress=None):
    """Set TP/SL for the NEXT _prepare_window() call on `mc`. Re-derives EVERY
    import-time derivation chain that actually feeds the barrier walk:
    compute_trade_outcome reads TP_SIGMA_BASE/STRESS + SL_SIGMA_BASE/STRESS
    (monte_carlo.py:2450-2451), NOT TP_BASE/SL_BASE directly -- patching
    TP_BASE alone is a silent no-op (LESSONS.md). Mirrors monte_carlo.py:
    429-432 (NET_*) and 501-504 (*_SIGMA_*) exactly, term for term.

    Phase A is flat (stress=base): tp_stress/sl_stress default to tp/sl.
    Must be called BEFORE the cell's `mc._prepare_window(...)` -- barriers bake
    there (precompute_outcomes -> compute_trade_outcome), not at simulate time.
    """
    if tp_stress is None:
        tp_stress = tp
    if sl_stress is None:
        sl_stress = sl
    mc.TP_BASE = float(tp)
    mc.TP_STRESS = float(tp_stress)
    mc.SL_BASE = float(sl)
    mc.SL_STRESS = float(sl_stress)
    # NET_* -- mirrors monte_carlo.py:429-432. SLIP_* are frozen/live module
    # values (Wealthsimple $0-commission defaults); never varied by this sweep.
    mc.NET_TP_BASE = mc.TP_BASE + mc.SLIP_ENTRY + mc.SLIP_TP
    mc.NET_TP_STRESS = mc.TP_STRESS + mc.SLIP_ENTRY + mc.SLIP_TP
    mc.NET_SL_BASE = mc.SL_BASE + mc.SLIP_ENTRY + mc.SLIP_SL
    mc.NET_SL_STRESS = mc.SL_STRESS + mc.SLIP_ENTRY + mc.SLIP_SL
    # sigma chain -- mirrors monte_carlo.py:501-504 exactly (same literal
    # expression: _cfg.PREMIUM_MULT / _opt.DELTA, not the module-level
    # PREMIUM_MULT/DELTA copies, to match source term-for-term).
    mc.TP_SIGMA_BASE = mc.TP_BASE * mc._cfg.PREMIUM_MULT / mc._opt.DELTA
    mc.TP_SIGMA_STRESS = mc.TP_STRESS * mc._cfg.PREMIUM_MULT / mc._opt.DELTA
    mc.SL_SIGMA_BASE = abs(mc.SL_BASE) * mc._cfg.PREMIUM_MULT / mc._opt.DELTA
    mc.SL_SIGMA_STRESS = abs(mc.SL_STRESS) * mc._cfg.PREMIUM_MULT / mc._opt.DELTA


# The 8 DB loaders `_prepare_window` calls (PREREG section 7 "Loader
# memoization"; names confirmed against source 2026-08-10 recon).
_LOADER_NAMES = (
    'load_signals', 'load_price_history', 'load_breadth_map',
    'load_mcclellan_map', 'load_trin_map', 'load_vix_whist_map',
    'load_bdiv_map', 'load_regime_map',
)


def _cache_key(args):
    """Normalize positional args into a hashable key. list/set args (e.g.
    load_price_history's sym_ids) become sorted tuples so argument-content
    (not object identity or incidental set-iteration order) determines cache
    hits."""
    key = []
    for a in args:
        if isinstance(a, (list, set, frozenset)):
            try:
                key.append(tuple(sorted(a)))
            except TypeError:
                key.append(tuple(a))
        else:
            key.append(a)
    return tuple(key)


def install_loader_cache(mc):
    """Memoize the 8 DB loaders `_prepare_window` calls, keyed on their exact
    positional args, so MySQL is hit ONCE per window (the first cell) instead
    of once per (TP,SL) cell. Each wrapped function's FULL body -- including
    load_signals' in-place CTSL overall-transform (monte_carlo.py:2199-2201
    docstring: "applies CTSL call-side transform to signal.overall in-place
    after load") -- runs exactly once per unique arg-set; every later call for
    the SAME window returns the cached result object unchanged, so there is no
    double-mutation risk from re-invoking a function that mutates its own
    output in place. Pure in-process wrapping -- monte_carlo.py on disk is
    never touched. Returns the dict of per-loader cache dicts (diagnostic
    only, e.g. to report hit counts)."""
    cache_store = {}

    def _make_wrapper(fn, cache):
        def _wrapped(*args):
            key = _cache_key(args)
            if key not in cache:
                cache[key] = fn(*args)
            return cache[key]
        return _wrapped

    for name in _LOADER_NAMES:
        original = getattr(mc, name)
        cache = {}
        cache_store[name] = cache
        setattr(mc, name, _make_wrapper(original, cache))
    return cache_store


def disable_puts(mc):
    """Puts are OFF in both profiles (PREREG baseline; PUT_TIER_ALLOC is all
    0.0 in strategy_config.py, verified live). Replace load_put_signals
    outright with a stub returning [] -- cheaper than hitting the DB and
    throwing the result away, and it structurally guarantees
    put_outcomes == {} (precompute_put_outcomes([], ...) returns {} on the
    very first loop check), so run_single_sim's put path can never fire a
    trade. Order-independent relative to install_loader_cache (this replaces
    the attribute outright; whichever call runs last wins, and there's
    nothing expensive left to cache on an O(1) stub)."""
    def _no_put_signals(version, d_start, d_end):
        return []
    mc.load_put_signals = _no_put_signals


def call_outcome_rates(ctx):
    """Deterministic (N_ITER-independent) barrier-touch rates from the baked
    ctx['call_outcomes'] -- kind in {'tp','sl','hard','both'}. This is exactly
    what _prepare_window's own stdout line reports (monte_carlo.py
    ~4374-4380), computed here directly from ctx instead of stdout-scraped, and
    it's what the S1 patch-bite acceptance check reads (deterministic given the
    signal set -- independent of N_ITER / simulation RNG). Returns
    (n, tp_rate_pct, sl_rate_pct, hard_rate_pct, both_rate_pct); n=0 -> all
    rates None."""
    outcomes = ctx['call_outcomes']
    n = len(outcomes)
    if n == 0:
        return 0, None, None, None, None
    tp = sl = hard = both = 0
    for o in outcomes.values():
        k = o['kind']
        if k == 'tp':
            tp += 1
        elif k == 'sl':
            sl += 1
        elif k == 'hard':
            hard += 1
        elif k == 'both':
            both += 1
    return n, tp / n * 100.0, sl / n * 100.0, hard / n * 100.0, both / n * 100.0


def pct(sorted_seq, q):
    """Nearest-rank percentile -- mirrors monte_carlo.py's own `_pct` helper
    (_simulate_window, ~4668-4672) for consistency. `sorted_seq` must already
    be sorted ascending. Returns None on an empty sequence."""
    if not sorted_seq:
        return None
    i = max(0, min(len(sorted_seq) - 1, int(round(q * (len(sorted_seq) - 1)))))
    return sorted_seq[i]
