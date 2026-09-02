"""
phaseC_patch.py -- reusable in-process patch helpers for the TP/SL Phase C
regime/market-wave-conditional sweep (experiments/tpsl_refine_2026_08/PREREG.md
section 5 + the CLARIFICATIONS block). NEW FILE -- mc_patch.py (Phase A/B,
currently in flight) is never edited or imported-and-monkeypatched-further;
this module is a standalone sibling that reuses mc_patch's primitives
(set_tpsl, install_loader_cache, atomic_write_json, pct, ...) by calling them,
never by copying or altering them.

HARD RULE (same as mc_patch.py): this module NEVER edits monte_carlo.py /
strategy_config.py / any tracked production file. Every effect here is
post-import monkey-patching of the ALREADY-IMPORTED `mc` module object,
exactly like mc_patch.set_tpsl / install_loader_cache / disable_puts do.

=============================================================================
INJECTION MECHANISM -- grep evidence (2026-08-10 recon, before any Phase C
cell is run)
=============================================================================
PREREG's PRECONDITION: "grep every consumer of is_stressed in monte_carlo.py
-- the patch is valid ONLY if it feeds the barrier/stress classification path
exclusively." Full-file grep of `is_stressed` in monte_carlo.py returns
EXACTLY two call sites:
  1. monte_carlo.py:2824, inside precompute_outcomes() -- `stressed =
     is_stressed(breadth_dates, breadth_map, sig.date, count_map=count_map)`
     -- this result feeds compute_trade_outcome(..., stressed, ...) directly,
     which at :2450-2451 does `tp_sigma = TP_SIGMA_STRESS if stressed else
     TP_SIGMA_BASE` (same for sl_sigma). This IS the barrier/stress
     classification path -- the one we want.
  2. monte_carlo.py:4101, inside _prepare_window() -- a DIAGNOSTIC PRINT ONLY
     (`n_str = sum(...); print(f"... stressed call signals: {n_str/...}%")`)
     -- n_str is never stored, never returned in ctx, never read by anything
     else. No downstream effect.
No third call site exists. In particular the ALLOC/SIZING path (Track A
"aggression-wave" aw_scale, monte_carlo.py:3296-3306, `_aw_market_scale`) reads
`breadth_on_or_before(...)` DIRECTLY -- NOT through is_stressed -- and is
additionally gated behind `AW_ENABLED` which defaults OFF ('0', monte_carlo.py
:449) and is never set by mc_patch.py/phaseA_run.py/phaseB_run.py/this driver.
CONCLUSION: monkey-patching `mc.is_stressed` is scope-correct -- it can only
ever affect the barrier/stress classification path (precompute_outcomes ->
compute_trade_outcome), never alloc/sizing. The "fall back to wrapping
precompute_outcomes" branch of the spec is NOT needed.

CAVEAT DISCOVERED DURING THIS RECON (documented, not fixed -- see below):
monte_carlo.py's own precompute_outcomes has a "DTE-router second pass"
(:4382-4425, gated `if DTE_ROUTER_ENABLED and DTE_ROUTER_TARGET_DTE == 15 and
call_sigs`). strategy_config.STRATEGY_30DTE (the config this whole experiment
runs under -- `_cfg = _sc.STRATEGY_30DTE` at monte_carlo.py:72, true for BOTH
Core and Apex profiles since profiles only layer TIER/MAX_POSITIONS/premium-cap
env diffs on top) ships DTE_ROUTER_ENABLED=True, DTE_ROUTER_TARGET_DTE=15,
DTE_ROUTER_SCORE_MIN=80, DTE_ROUTER_DAY_CAP=1 (strategy_config.py:1192-1201).
For the >=1/day subset of call signals meeting score>=80 AND trend<50 that get
routed, monte_carlo.py calls `_mc15.precompute_outcomes(...)` -- a completely
SEPARATE function in monte_carlo_15dte.py, which has ITS OWN `is_stressed`
(monte_carlo_15dte.py:608) and ITS OWN TP_STRESS/SL_STRESS/TP_SIGMA_STRESS
module globals captured ONCE at THAT module's import time
(monte_carlo_15dte.py:144-169). Patching `mc.is_stressed` (this module's
`mc` = the 30DTE monte_carlo module) has ZERO effect on that routed subset's
barrier classification -- those signals' `stressed` flag (and hence TP/SL
band) is governed entirely by monte_carlo_15dte.py's own state, which neither
mc_patch.set_tpsl nor this module ever touches.
THIS IS NOT A NEW LEAK INTRODUCED BY PHASE C -- it is an INHERITED property
of the whole `mc_patch.set_tpsl`/post-import-monkeypatch architecture that
Phase A (already complete) and Phase B (currently running) share identically:
neither of THEIR drivers touches `_mc15` either, so their TP/SL sweep never
reached the DTE-routed subset either. Given (a) this file may not edit
mc_patch.py/phaseA_run.py/phaseB_run.py, (b) Phase B is running right now on
the un-patched-for-this version, and (c) diverging Phase C's methodology from
Phase A/B's on this exact point would make Phase D's finalist comparison
apples-to-oranges, the correct action is to LEAVE THIS ALONE (stay consistent
with Phase A/B) and DOCUMENT + DIAGNOSE it, not silently patch around it.
`stress_source_diagnostics()` below reports the DTE-routed fraction of each
prepared window's call_outcomes (free -- read off the same ctx['call_outcomes']
dict Phase A/B/C already prepare) so the orchestrator can see the actual
empirical scope. DTE_ROUTER_DAY_CAP=1 bounds this to at most one signal per
calendar day regardless of how many qualify, i.e. structurally small.

=============================================================================
PREDICATE DEFINITIONS -- source evidence (PREREG section 5 CLARIFICATIONS)
=============================================================================
CLARIFICATIONS text: "MWDD = McClellan flat/topping band INCLUDING its
VIX-panic guard; RXDD = the VIX Gaussian band condition; regime =
regime_multiplier(on-or-before) < 1.0." Both MWDD/RXDD source functions
(_mwdd_call_scale monte_carlo.py:1020-1032, _rxdd_call_scale :820-829)
implement a CONTINUOUS Gaussian bump (`bump = exp(-0.5*z*z)`, no binary
in/out flag), so converting to a binary "band" predicate requires picking a
cutoff on z=(x-center)/width. Evidence for `abs(z) <= 1.0` (i.e. the band is
literally [center-width, center+width]): the MWDD constant-block's own
code comment (monte_carlo.py:1002-1011) describes the McClellan band as
"the breadth-momentum analog of RXDD's VIX 20-28 'slow-bleed'" -- and
RXDD_VIX_C=24.0, RXDD_VIX_W=4.0 (monte_carlo.py:593-594, STRATEGY_30DTE
defaults) give EXACTLY center-width=20, center+width=28. This is an exact,
non-coincidental match, so `abs(z) <= 1.0` is adopted as "the band" for both
RXDD (VIX in [20,28] by default) and MWDD (McClellan in [-22,+22] by default,
MWDD_MCC_C=0.0/MWDD_MCC_W=22.0, monte_carlo.py:1013-1014). This is a
documented MODELING DECISION, not a spec-literal value -- flagged in the
builder's final report for the orchestrator to veto/adjust if desired.

TRAP AVOIDED -- load_regime_map() is POLYMORPHIC: `if BREADTH_ALLOC_ENABLED:
return breadth_score ... else: return regime_multiplier` (monte_carlo.py
:1935-1968). strategy_config.STRATEGY_30DTE ships BREADTH_ALLOC_ENABLED=True
(strategy_config.py:1297, live default, never overridden by mc_patch.py), so
`mc.load_regime_map()` returns BREADTH SCORE in every Phase A/B/C run, NOT
regime_multiplier. A naive "regime_down = load_regime_map(...) < 1.0"
predicate would therefore be checking a ~0-100-scale breadth score against
1.0 and would almost NEVER fire (DEGENERATE, ~0% fired-fraction) -- exactly
the kind of silent bug PREREG's mandatory identity validation + fired-fraction
reporting exists to catch. `load_regime_multiplier_map()` below is a
DEDICATED loader that queries `MarketRegime.regime_multiplier` directly
(mirroring monte_carlo.py:1956-1967's `else` branch verbatim), independent of
BREADTH_ALLOC_ENABLED, so predicate #4 measures the field PREREG's prose
actually names.

TRAP AVOIDED -- RXDD's "vix" is NOT monte_carlo.load_vix_whist_map() (that
loads a DIFFERENT derived series, the weekly-MACD-histogram of VIX used by
the separate VXMD lever, monte_carlo.py:1857-1887). The actual VIX source
`_rxdd_call_scale`'s caller feeds it (monte_carlo.py:3290,
`_dte_router_value_on_or_before(vix_dates, vix_map, today)`) is loaded via
`_load_dte_router_market_maps` -> `dte_router.load_router_market_maps`
(dte_router.py:79-107), which returns raw `MarketRegime.vix_close`.
`load_raw_vix_map()` below wraps that same function.

TRAP AVOIDED -- ctx['rxdd_vix_map']/ctx['mwdd_mcc_map'] (the maps
_prepare_window itself loads for the shipped RXDD/MWDD ALLOC dampeners) are
NOT usable here even though RXDD_ENABLED=MWDD_ENABLED=True are STRATEGY_30DTE
defaults (strategy_config.py:1327,1337, so those ctx maps DO get populated) --
is_stressed is called *during* _prepare_window's execution of
precompute_outcomes, before _prepare_window assembles/returns ctx, so a
patched mc.is_stressed closure has no access to _prepare_window's locals
regardless. Every predicate here loads its own per-day maps independently,
via the SAME loader functions the engine itself uses, cached at the phaseC
level (own dicts below) since these particular loaders are not among
mc_patch.install_loader_cache's 8 memoized names.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Locked cell-grid constants -- PREREG.md section 5 + the Phase C task spec's
# literal restatement of it.
# ---------------------------------------------------------------------------
SOURCES = ('breadth', 'mwdd_band', 'rxdd_band', 'regime_down')   # canonical order, PREREG's own 1-4 enumeration
BREADTH_THRESHOLDS = (40, 50)
TP_STRESS_OFFSETS = (0.0, 0.05, 0.10, 0.15)
SL_STRESS_OFFSETS = (0.0, -0.15, 0.10)
SL_CLAMP_MIN, SL_CLAMP_MAX = -1.00, -0.25   # PREREG section 5's "(clamped >= -1.00)", widened to the
                                             # established Phase A/B SL_MIN/SL_MAX per the Phase C task spec
PHASE_C_WINDOWS = ('2022', '2024', '22-now', '5y', '2020_crash')   # PREREG section 5, locked
N_ITER_FULL = 300     # PREREG section 5
FLAT_SOURCE = 'flat_base'   # tag for the in-phase paired baseline row (PREREG CLARIFICATIONS)


def _r2(x):
    return round(float(x) + 0.0, 2)


def _clamp_sl(sl):
    return _r2(max(SL_CLAMP_MIN, min(SL_CLAMP_MAX, sl)))


def build_stress_cells(tp_b, sl_b):
    """(tp_stress, sl_stress) grid for ONE (source, thr) arm, given a base
    pair. PREREG section 5 + Phase C task spec: TP_STRESS = TP_B + {0,+0.05,
    +0.10,+0.15}; SL_STRESS = SL_B + {0,-0.15,+0.10}, clamped to
    [SL_CLAMP_MIN, SL_CLAMP_MAX]; "drop identity combos (0,0) and
    clamp-created duplicates".

    Mechanical dedup (first-seen-wins in TP-major, SL-minor generation order,
    same convention as phaseA_run.py's TP-major grid and
    build_phaseB_cells.py's first-seen-wins fill dedup): pre-seed `seen` with
    the literal (tp_b, sl_b) identity pair so BOTH the true (tp_off=0,
    sl_off=0) combo AND any later combo that happens to CLAMP onto the exact
    same pair (e.g. sl_off=-0.15 clamping back to sl_b when sl_b is already
    -1.00, i.e. the dead-hold base cell) are excluded by the same mechanism.
    Any two later combos that clamp onto the SAME new pair as each other are
    deduped the same way (first generated survives). This can legitimately
    yield FEWER than 12 cells for base pairs at/near the SL clamp boundary
    (sl_b == -1.00 or -0.25) -- documented, not a bug.

    Returns an ordered list of up to 12 distinct (tp_stress, sl_stress)
    tuples, never including (tp_b, sl_b) itself.
    """
    tp_b = _r2(tp_b)
    sl_b = _r2(sl_b)
    seen = {(tp_b, sl_b)}
    cells = []
    for tp_off in TP_STRESS_OFFSETS:
        for sl_off in SL_STRESS_OFFSETS:
            ts = _r2(tp_b + tp_off)
            ss = _clamp_sl(sl_b + sl_off)
            pair = (ts, ss)
            if pair in seen:
                continue
            seen.add(pair)
            cells.append(pair)
    return cells


def build_all_cells(sources, tp_b, sl_b):
    """Full per-profile grid (excluding the flat baseline row, which the
    runner always handles separately/unconditionally): ordered list of
    (source, thr_or_None, tp_stress, sl_stress) tuples. `sources` may be
    given in ANY order/duplication -- internally canonicalized (deduped,
    reordered) to SOURCES' fixed order (breadth, mwdd_band, rxdd_band,
    regime_down) so output is deterministic regardless of caller/CLI
    argument order (defensive: callers should not have to remember to
    pre-sort). breadth expands x2 (thr=40, thr=50); the other three sources
    are single-arm (thr=None). With no clamp-collisions this is
    2*11 + 11 + 11 + 11 = 55 non-identity cells (+ 1 flat baseline the runner
    adds separately = 56 total), matching the Phase C task spec's "~56
    cells/profile" (breadth alone is 2*12=24 raw combos minus 2 identity
    drops = 22, per source is 12-1=11). Raises ValueError on any token not in
    SOURCES -- a typo-guard even though phaseC_run.py's own --sources parsing
    already validates before calling this (defense in depth, matches
    phaseB_run.py's parse_cells_arg "not a typo-tolerant flag by design")."""
    unknown = set(sources) - set(SOURCES)
    if unknown:
        raise ValueError(f"unknown Phase C source(s) {sorted(unknown)} (expected one of {SOURCES})")
    ordered_sources = [s for s in SOURCES if s in set(sources)]
    out = []
    for source in ordered_sources:
        if source == 'breadth':
            for thr in BREADTH_THRESHOLDS:
                for ts, ss in build_stress_cells(tp_b, sl_b):
                    out.append(('breadth', thr, ts, ss))
        else:   # mwdd_band, rxdd_band, regime_down -- already validated above, single-arm (thr=None)
            for ts, ss in build_stress_cells(tp_b, sl_b):
                out.append((source, None, ts, ss))
    return out


# ---------------------------------------------------------------------------
# Auxiliary per-day map loaders -- NOT among mc_patch.install_loader_cache's
# 8 memoized names, so this module keeps its own tiny (d_start,d_end)-keyed
# caches (same "MySQL hit once per window" discipline PREREG section 7
# mandates for the 8 named loaders). Every DB touch is a LAZY import inside
# the function body, mirroring mc_patch.py's own restraint (module-level
# `import phaseC_patch` must stay safe even before monte_carlo/DB env is
# configured -- required for the pure-Python unit tests in test_phaseC.py).
# ---------------------------------------------------------------------------
_vix_cache = {}
_regime_mult_cache = {}


def load_raw_vix_map(mc, d_start, d_end):
    """(sorted_dates, {date: vix_close}) -- the SAME raw-VIX source RXDD's
    shipped alloc-scale lever reads (mc._load_dte_router_market_maps ->
    dte_router.load_router_market_maps -> MarketRegime.vix_close), NOT
    mc.load_vix_whist_map (a different derived series, see module docstring).
    Cached per (d_start, d_end) -- one MySQL round trip per window regardless
    of how many cells/sources in that window need VIX (MWDD's panic guard
    AND RXDD's band both read this same cached map)."""
    key = (d_start, d_end)
    if key not in _vix_cache:
        dates, vix_map, _regime_composite_unused = mc._load_dte_router_market_maps(d_start, d_end)
        _vix_cache[key] = (dates, vix_map)
    return _vix_cache[key]


def load_regime_multiplier_map(d_start, d_end):
    """(sorted_dates, {date: regime_multiplier}) -- dedicated loader for the
    TRUE MarketRegime.regime_multiplier field, mirroring monte_carlo.py
    load_regime_map()'s legacy `else` branch verbatim (same table, same
    field, same 60-day lookback pad) but WITHOUT that function's
    BREADTH_ALLOC_ENABLED branch -- see module docstring "TRAP AVOIDED".
    Cached per (d_start, d_end)."""
    key = (d_start, d_end)
    if key not in _regime_mult_cache:
        from datetime import timedelta
        from database.models.core import MarketRegime
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
        _regime_mult_cache[key] = (sorted(m.keys()), m)
    return _regime_mult_cache[key]


def load_window_maps(mc, d_start, d_end):
    """One-shot per-window loader for all three auxiliary market-state maps
    Phase C's non-breadth predicates need. mc.load_mcclellan_map is one of
    mc_patch.install_loader_cache's 8 memoized names, so calling it here
    (AFTER install_loader_cache(mc) has run -- enforced by phaseC_run.py's
    import-order) hits that same cache; the other two use this module's own
    caches above. Returns a plain dict (mirrors the ctx-dict convention used
    throughout monte_carlo.py/mc_patch.py)."""
    mcc_dates, mcc_map = mc.load_mcclellan_map(d_start, d_end)
    vix_dates, vix_map = load_raw_vix_map(mc, d_start, d_end)
    regime_dates, regime_mult_map = load_regime_multiplier_map(d_start, d_end)
    return {
        'mcc_dates': mcc_dates, 'mcc_map': mcc_map,
        'vix_dates': vix_dates, 'vix_map': vix_map,
        'regime_dates': regime_dates, 'regime_mult_map': regime_mult_map,
    }


# ---------------------------------------------------------------------------
# Predicate closures. Each factory takes `mc` (for mc.breadth_on_or_before --
# a fully generic bisect on-or-before {date:float} lookup with NO
# BREADTH_ALLOC_ENABLED-dependent default, unlike mc.regime_on_or_before, see
# module docstring -- and the relevant *_C/*_W/*_VIX_PANIC constants, which
# are unconditional module-level floats regardless of the shipped lever's
# ENABLED flag) plus the pre-loaded per-day maps. The returned closure's
# signature matches is_stressed's call site EXACTLY (positional
# sorted_dates/bmap/d + keyword count_map) even though it ignores the first
# two positional args -- precompute_outcomes calls
# `is_stressed(breadth_dates, breadth_map, sig.date, count_map=count_map)`
# unconditionally, so the replacement must accept (and discard) that shape.
# ---------------------------------------------------------------------------

def _band_membership(x, center, width):
    """True iff x falls within one width of center (see module docstring's
    "PREDICATE DEFINITIONS" section for the [center-width, center+width]
    derivation). False (never fires) if x is unknown or width<=0 -- mirrors
    the shipped _mwdd_call_scale/_rxdd_call_scale's own `width<=0 -> 1.0
    (no-op)` guard."""
    if x is None or width is None or width <= 0:
        return False
    z = (x - center) / width
    return abs(z) <= 1.0


def make_breadth_predicate(original_is_stressed):
    """breadth source: NO closure needed -- the engine's own is_stressed
    ALREADY implements exactly this (breadth_score(entry) <= BREADTH_THRESHOLD).
    Returns the untouched original function; caller separately sets
    mc.BREADTH_THRESHOLD to the requested thr. Kept as a named function (not
    inlined) so activate_source's dispatch table stays uniform/readable."""
    return original_is_stressed


def make_mwdd_predicate(mc, maps):
    """MWDD source: McClellan flat/topping band INCLUDING the VIX-panic guard,
    EXCLUDING the running-DD gate (MWDD_DD_MIN) and the ENABLED flag itself --
    both are alloc-scale-specific concerns from _mwdd_call_scale
    (monte_carlo.py:1020-1032) that PREREG's CLARIFICATIONS explicitly says
    Phase C's predicate must NOT carry (a path-dependent running-DD state
    cannot classify a signal at precompute time)."""
    mcc_dates, mcc_map = maps['mcc_dates'], maps['mcc_map']
    vix_dates, vix_map = maps['vix_dates'], maps['vix_map']
    center, width, vix_panic = mc.MWDD_MCC_C, mc.MWDD_MCC_W, mc.MWDD_VIX_PANIC

    def _mwdd_predicate(sorted_dates, bmap, d, count_map=None):
        mcc = mc.breadth_on_or_before(mcc_dates, mcc_map, d) if mcc_dates else None
        if mcc is None:
            return False
        if vix_dates:
            vix = mc.breadth_on_or_before(vix_dates, vix_map, d)
            if vix is not None and vix_panic > 0 and vix >= vix_panic:
                return False
        return _band_membership(mcc, center, width)
    return _mwdd_predicate


def make_rxdd_predicate(mc, maps):
    """RXDD source: VIX Gaussian band condition ONLY (market-state part of
    _rxdd_call_scale, monte_carlo.py:820-829), excluding its RXDD_DD_MIN
    running-DD gate and ENABLED flag for the same reason as MWDD above."""
    vix_dates, vix_map = maps['vix_dates'], maps['vix_map']
    center, width = mc.RXDD_VIX_C, mc.RXDD_VIX_W

    def _rxdd_predicate(sorted_dates, bmap, d, count_map=None):
        vix = mc.breadth_on_or_before(vix_dates, vix_map, d) if vix_dates else None
        return _band_membership(vix, center, width)
    return _rxdd_predicate


def make_regime_down_predicate(mc, maps):
    """regime_down source: TRUE regime_multiplier(on-or-before entry) < 1.0
    (see module docstring "TRAP AVOIDED" -- deliberately NOT
    mc.load_regime_map/mc.regime_on_or_before, which are BREADTH_ALLOC_ENABLED
    -polymorphic in this shipped config and would silently measure breadth
    instead). Missing coverage (mc.breadth_on_or_before returns None) ->
    False (not stressed / use base barriers) -- the same "unknown -> neutral"
    convention the engine's own is_stressed uses for missing breadth."""
    regime_dates, regime_mult_map = maps['regime_dates'], maps['regime_mult_map']

    def _regime_down_predicate(sorted_dates, bmap, d, count_map=None):
        rm = mc.breadth_on_or_before(regime_dates, regime_mult_map, d) if regime_dates else None
        return rm is not None and rm < 1.0
    return _regime_down_predicate


def activate_source(mc, source, thr, maps, original_is_stressed, original_threshold):
    """Install the correct mc.is_stressed / mc.BREADTH_THRESHOLD state for
    the given (source, thr) BEFORE calling mc._prepare_window() for a cell.
    source in {FLAT_SOURCE, 'breadth', 'mwdd_band', 'rxdd_band',
    'regime_down'}. FLAT_SOURCE and 'breadth' reuse the engine's own
    (unpatched) is_stressed; the other three install a closure. Always fully
    determines both mc.is_stressed and mc.BREADTH_THRESHOLD (no partial
    state carried over from a previous cell in the same process)."""
    if source == FLAT_SOURCE:
        mc.is_stressed = original_is_stressed
        mc.BREADTH_THRESHOLD = original_threshold
        return
    if source == 'breadth':
        mc.is_stressed = make_breadth_predicate(original_is_stressed)
        mc.BREADTH_THRESHOLD = int(thr)
        return
    if source == 'mwdd_band':
        mc.is_stressed = make_mwdd_predicate(mc, maps)
        mc.BREADTH_THRESHOLD = original_threshold
        return
    if source == 'rxdd_band':
        mc.is_stressed = make_rxdd_predicate(mc, maps)
        mc.BREADTH_THRESHOLD = original_threshold
        return
    if source == 'regime_down':
        mc.is_stressed = make_regime_down_predicate(mc, maps)
        mc.BREADTH_THRESHOLD = original_threshold
        return
    raise ValueError(f"unknown Phase C source {source!r} (expected {FLAT_SOURCE!r} or one of {SOURCES})")


# ---------------------------------------------------------------------------
# Diagnostics -- pure functions of an already-prepared ctx, no extra DB/mc
# calls (call_outcomes' per-signal 'stressed'/'_dte' keys are already
# computed as a byproduct of _prepare_window; this just tallies them).
# ---------------------------------------------------------------------------

def stress_source_diagnostics(ctx):
    """From an already-prepared ctx (mc._prepare_window's return value):
    (n_calls, stressed_frac_pct, dte_routed_n, dte_routed_frac_pct).
    stressed_frac_pct = % of call_outcomes with stressed=True -- this IS the
    predicate's "fired-fraction" PREREG/the task spec asks for, read directly
    off the deterministic (N_ITER-independent) baked classification, same
    data source as mc_patch.call_outcome_rates' tp_rate/sl_rate.
    dte_routed_n/frac = % of call_outcomes carrying a '_dte'=='15' tag, i.e.
    routed around BOTH this predicate AND mc_patch.set_tpsl entirely via the
    monte_carlo_15dte second pass (module docstring "CAVEAT DISCOVERED...";
    reported here as free, honest scope-of-coverage evidence, not something
    this predicate can fix). n_calls=0 -> (0, None, 0, None)."""
    outcomes = ctx['call_outcomes']
    n = len(outcomes)
    if n == 0:
        return 0, None, 0, None
    n_stressed = sum(1 for o in outcomes.values() if o.get('stressed'))
    n_dte15 = sum(1 for o in outcomes.values() if o.get('_dte') == '15')
    return n, n_stressed / n * 100.0, n_dte15, n_dte15 / n * 100.0
