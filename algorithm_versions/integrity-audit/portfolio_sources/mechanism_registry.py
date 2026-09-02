"""Single source of truth for portfolio-mechanism DTE coverage.

Every shipped mechanism that affects portfolio behavior (TP/SL / cascade /
breadth-driven scaling / DD / earnings filtering / etc.) gets one entry
here. The registry records:

- Which DTE strategies it ships under (`enabled` / `disabled` / `n/a`)
- Why a status is `disabled` (mandatory free-text reason — forces ship-time
  documentation)
- Which engine files wire the mechanism when enabled
- The config field names that define the mechanism

The companion test (`tests/test_mechanism_registry.py`) walks this registry
and verifies the engines actually match what the registry claims:

- `enabled` mechanisms must have their config fields wired in the listed
  engine files
- `disabled` mechanisms in `wiring_mode='not_wired'` mode must have ZERO
  trace of their config fields in mc15/bc15 (the SAW pattern)
- `disabled` mechanisms in `wiring_mode='wired_neutral'` mode must have
  the fields wired but at no-op values per `strategy_config.STRATEGY_15DTE`
  (the DD_SOFT_BAND pattern: floor=1.0 means no contraction)

Adding a new portfolio mechanism: per `deploy.md` "Adding a NEW Portfolio
Mechanism" Step 0, add an entry here BEFORE editing the engine. The drift-
guard will refuse to pass if the registry says enabled-on-15-DTE but the
15 DTE engine isn't wired (or vice versa).

Retired mechanisms: leave the entry in place with both statuses set to
`disabled` and a reason of "Retired YYYY-MM-DD: replaced by ..." so future
audits can find historical context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


# ─── Status enum (just strings for trivial JSON-ability) ─────────────────


# Allowed values: 'enabled' | 'disabled' | 'n/a'
#   enabled       — engine wires constants, values are non-no-op per config
#   disabled      — engine may or may not wire constants; values are no-op
#                   (see wiring_mode below)
#   n/a           — mechanism does not apply to this DTE (rare; e.g. a
#                   30-DTE-only theta-decay knob)
_VALID_STATUSES = ('enabled', 'disabled', 'n/a')

# Allowed values for wiring_mode (only meaningful when status='disabled'):
#   not_wired     — engine MUST NOT define the config field constants
#                   (catches "someone added wiring without flipping the
#                   config flag" failure mode). Used for mechanisms where
#                   adding the constants is non-trivial — e.g. SAW Put
#                   U-curve has its own helper function `saw_put_ucurve_scale()`
#                   that the engine entry path calls.
#   wired_neutral — engine wires the constants but config sets no-op values
#                   (e.g. DD_SOFT_CALL_FLOOR=1.0 means no contraction). Used
#                   for mechanisms where the wiring is cheap and a flag flip
#                   plus value change is enough to enable.
#   n/a           — used only with status in {'enabled', 'n/a'}
_VALID_WIRING_MODES = ('not_wired', 'wired_neutral', 'n/a')


@dataclass(frozen=True)
class MechanismSpec:
    """Single registry entry for one portfolio mechanism."""

    name: str
    """Stable identifier — uppercase snake_case. Used in error messages."""

    config_fields: Tuple[str, ...]
    """Config field names defining the mechanism (DteStrategyConfig
    attributes; not OptionStrategyConfig). Must match strategy_config.py."""

    dte_30_status: str
    """One of _VALID_STATUSES."""
    dte_15_status: str

    dte_30_reason: str = ''
    """Mandatory free-text when status != 'enabled'. Surfaces in test
    failures so future Claude knows WHY the mechanism is disabled — was it
    retired? Pending validation? N/A by design?"""
    dte_15_reason: str = ''

    dte_30_wiring_mode: str = 'n/a'
    """Required when status='disabled'. One of _VALID_WIRING_MODES."""
    dte_15_wiring_mode: str = 'n/a'

    engine_files_30: Tuple[str, ...] = ()
    """Engine files that wire this mechanism when enabled on 30 DTE.
    Typically ('monte_carlo.py', 'backtest_cascade.py')."""
    engine_files_15: Tuple[str, ...] = ()

    ship_date_30: str = ''
    """ISO date the mechanism shipped to 30 DTE (or '' if never shipped)."""
    ship_date_15: str = ''

    notes: str = ''
    """Free-text — calibration trail, validation methodology, retirement
    rationale. Ends up in test output for context."""


# ─── Validation helpers ──────────────────────────────────────────────────


def _validate_spec(s: MechanismSpec) -> None:
    """Run consistency checks on a single spec — called at module load."""
    assert s.dte_30_status in _VALID_STATUSES, \
        f'{s.name}: dte_30_status {s.dte_30_status!r} not in {_VALID_STATUSES}'
    assert s.dte_15_status in _VALID_STATUSES, \
        f'{s.name}: dte_15_status {s.dte_15_status!r} not in {_VALID_STATUSES}'
    assert s.dte_30_wiring_mode in _VALID_WIRING_MODES, \
        f'{s.name}: dte_30_wiring_mode {s.dte_30_wiring_mode!r} not in {_VALID_WIRING_MODES}'
    assert s.dte_15_wiring_mode in _VALID_WIRING_MODES, \
        f'{s.name}: dte_15_wiring_mode {s.dte_15_wiring_mode!r} not in {_VALID_WIRING_MODES}'
    if s.dte_30_status != 'enabled':
        assert s.dte_30_reason, \
            f'{s.name}: dte_30_status={s.dte_30_status!r} requires dte_30_reason (non-empty)'
    if s.dte_15_status != 'enabled':
        assert s.dte_15_reason, \
            f'{s.name}: dte_15_status={s.dte_15_status!r} requires dte_15_reason (non-empty)'
    if s.dte_30_status == 'disabled':
        assert s.dte_30_wiring_mode in ('not_wired', 'wired_neutral'), \
            f'{s.name}: disabled-on-30 requires wiring_mode in (not_wired, wired_neutral); got {s.dte_30_wiring_mode!r}'
    if s.dte_15_status == 'disabled':
        assert s.dte_15_wiring_mode in ('not_wired', 'wired_neutral'), \
            f'{s.name}: disabled-on-15 requires wiring_mode in (not_wired, wired_neutral); got {s.dte_15_wiring_mode!r}'
    assert s.config_fields, f'{s.name}: config_fields cannot be empty'


# ─── The registry ────────────────────────────────────────────────────────


REGISTRY: Tuple[MechanismSpec, ...] = (
    # ── DTE Router — broad high-score call sleeve to 15 DTE ──────────────
    # Portfolio-only overlay shipped 2026-05-27 on top of active v60 scoring.
    # It does not change Score.overall or ALGORITHM_VERSION. Eligible 30 DTE
    # call entries (score>=80, trend<50, one/day) use 15 DTE call
    # outcomes/exits while keeping the 30 DTE portfolio sizing stack. Existing
    # CT promotion still applies when trend qualifies.
    MechanismSpec(
        name='DTE_ROUTER',
        config_fields=(
            'DTE_ROUTER_ENABLED',
            'DTE_ROUTER_TARGET_DTE',
            'DTE_ROUTER_SCORE_MIN',
            'DTE_ROUTER_TREND_LT',
            'DTE_ROUTER_VIX_MIN',
            'DTE_ROUTER_VIX_MAX',
            'DTE_ROUTER_REGIME_MIN',
            'DTE_ROUTER_REGIME_MAX',
            'DTE_ROUTER_DAY_CAP',
            'DTE_ROUTER_ALLOC_SCORE_CAP',
            'DTE_ROUTER_EXCLUDED_SYMBOLS',
        ),
        dte_30_status='enabled',
        dte_15_status='disabled',
        dte_15_wiring_mode='not_wired',
        dte_15_reason=(
            'The router is itself a 30 DTE portfolio overlay that borrows 15 DTE '
            'call outcomes for a high-score trend pocket. It is not a separate '
            '15 DTE strategy mechanism; enabling it inside monte_carlo_15dte.py '
            'would double-route the half-DTE substrate.'
        ),
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py'),
        engine_files_15=(),
        ship_date_30='2026-05-27',
        notes=(
            'N=500 all-window evidence for call_ge_80_daycap1_trend_lt_50: '
            '2022 mean_log +0.388 / median +0.437 / DD -4.34pp; 2023 '
            '+0.033 / +0.051 / DD -2.32pp; 2025 +0.042 / +0.029 / DD -0.31pp; '
            '22-now +0.017 / +0.018 with worst-DD +1.54pp but mean-DD -2.37pp; '
            '5y +0.013 / +0.018 / DD +0.00pp. Run artifact: '
            '.codex/runs/dte_router_mc_trend50_n500_20260528_0026.'
        ),
    ),

    # ── CTSL — Counter-Trend Score Lift (Stage 1 winner) ──────────────────
    # Shipped 30 DTE 2026-05-08. Score-stage continuous lift mechanism that
    # additively stacks on top of CT_PROMOTE. Tighter call gate (tm=15) lifts
    # only deepest CT-call subset (~5% of cohort) to ULTRA-tier-quality scores;
    # wider put gate (tm=76) dampens deep CT-puts toward target=0 (put_top
    # tier). 15 DTE intentionally not wired: half-DTE strategy not validated
    # under bounded-fill MC for this calibration; CTSL Stage 1 was calibrated
    # exclusively on 30 DTE WR7 + Stage 3 N=500×8 windows (B config: 5y DD
    # -0.40pp vs A baseline; T1-T7 hard gates all PASS).
    MechanismSpec(
        name='CTSL',
        config_fields=(
            'CTSL_ENABLED',
            'CTSL_CALL_TREND_MAX',
            'CTSL_CALL_TARGET',
            'CTSL_CALL_ALPHA',
            'CTSL_CALL_TREND_POWER',
            'CTSL_CALL_TIER_FLOOR',
            'CTSL_CALL_SCORE_NORM_WEIGHT',
            'CTSL_CALL_SCORE_NORM_POWER',
            'CTSL_PUT_TREND_MIN',
            'CTSL_PUT_TARGET',
            'CTSL_PUT_ALPHA',
            'CTSL_PUT_TREND_POWER',
            'CTSL_PUT_TIER_CEILING',
            'CTSL_PUT_SCORE_NORM_WEIGHT',
            'CTSL_PUT_SCORE_NORM_POWER',
        ),
        dte_30_status='enabled',
        dte_15_status='disabled',
        dte_15_wiring_mode='not_wired',
        dte_15_reason=(
            'Not validated under bounded-fill MC for half-DTE strategy. '
            'Stage 1 WR7 calibration + Stage 3 N=500×8 portfolio MC ran on '
            '30 DTE only. 15 DTE has different barrier dynamics (PREMIUM_MULT=1.29 '
            'vs 1.82, HOLD_DAYS=7 vs 15) that may shift the WR7→option TP% gap '
            'and DD profile. Re-run Stage 1+3 on 15 DTE substrate before enabling.'
        ),
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py'),
        engine_files_15=(),  # not wired — engine_files_15 must remain empty
        ship_date_30='2026-05-08',
        notes=(
            'Score-stage continuous lift applied at signal-load time inside '
            'monte_carlo.load_signals / load_put_signals (and mirrors in '
            'backtest_cascade.py / api.py / trader.py). Stacks ADDITIVELY on '
            'CT_PROMOTE (Stage 3 result: B stack passes T1-T7; C substitute '
            'fails T4 with 5y DD +2.20pp). The original "phased removal of '
            'CT_PROMOTE" goal was rejected as NULL — CT_PROMOTE earns its keep '
            'via accidental ULTRA-slot capping that CTSL alone cannot replicate. '
            'Stage 3 evidence: 5y DD -0.40pp, 22-now DD -3.40pp, 2023 DD -3.20pp, '
            '2025 DD -5.00pp; per-trade quality preserved (CTP%/PTP% drift ±0.5pp). '
            'Calibration trail: experiments/ctsl/FINDINGS.md.'
        ),
    ),

    # ── SAW Put U-curve — sector-breadth-driven put alloc gradient ────────
    # Shipped 30 DTE 2026-05-08 (commit fa1b099). 15 DTE intentionally not
    # wired: bounded-fill MC dynamics differ enough at half-DTE that the
    # 30 DTE Region B optimum (mid=72/hw=18/floor=0.55/ceil=1.35/pk=3.0)
    # may not transfer. Calibration is queued (handoff
    # 2026-05-08-1743_saw-put-ucurve-15dte-calibration.md).
    MechanismSpec(
        name='SAW_PUT_UCURVE',
        config_fields=(
            'SAW_PUT_UCURVE_ENABLED',
            'SAW_PUT_UCURVE_SHAPE',
            'SAW_PUT_UCURVE_MIDPOINT',
            'SAW_PUT_UCURVE_HALFWIDTH',
            'SAW_PUT_UCURVE_FLOOR',
            'SAW_PUT_UCURVE_CEIL',
            'SAW_PUT_UCURVE_POWER',
            'SAW_PUT_UCURVE_K',
        ),
        dte_30_status='enabled',
        dte_15_status='enabled',
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py'),
        engine_files_15=('monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        ship_date_30='2026-05-08',
        ship_date_15='2026-05-09',
        notes=(
            'At each put signal date, lookup cross-sec ETF breadth (% of 11 '
            'SPDRs above EMA50) and compute U-curve scale that contracts put '
            'alloc in the "bad zone" where put pnl% is lowest. '
            '30 DTE: quadratic shape, mid=72/hw=18/floor=0.55/ceil=1.35/pow=3.0 '
            '— Region B winner with breadth-extreme amplification at <20 / >95. '
            'Stage C N=300×8: 5y DD -1.1pp, 22-now DD -2.4pp. '
            '15 DTE: sigmoid shape, mid=70/hw=25/floor=0.65/ceil=1.00/K=12.0 '
            '— SHIPPED 2026-05-09 via fresh Bayesian calibration. KEY DIFFERENCE: '
            'ceil=1.00 (NO breadth-extreme amplification — only contraction). '
            '30 DTE Region B applied to 15 DTE smoke produced 5y_dd +2.9pp '
            '(worse), so a fresh sweep was required. Stage 3 N=500×8 evidence: '
            '5y DD 80.8% → 78.9% (-1.92pp), 22-now DD 80.7% → 78.9% (-1.76pp), '
            'all 8 windows DD reduces, 7-of-8 windows compound IMPROVES, '
            '0% collapse, all T1-T7 PASS. See '
            '`experiments/saw_put_ucurve_15dte/phase_d.log`.'
        ),
    ),

    # ── H3 — DD-soft band call alloc contraction ──────────────────────────
    # Shipped 30 DTE 2026-05-04. 15 DTE wired with neutral values
    # (LO=0.0, HI=0.0, FLOOR=1.0 → no contraction at any DD level).
    # Distinct from SAW because the engine fields ARE present in mc15/bc15
    # but the values are no-op. Drift-guard pairs_mc15/pairs_bc15 already
    # cover this — registry just documents the asymmetry.
    MechanismSpec(
        name='DD_SOFT_BAND',
        config_fields=(
            'DD_SOFT_BAND_LO',
            'DD_SOFT_BAND_HI',
            'DD_SOFT_CALL_FLOOR',
        ),
        dte_30_status='enabled',
        dte_15_status='disabled',
        dte_15_wiring_mode='wired_neutral',
        dte_15_reason=(
            'Calibration not transferred from 30 DTE — half-DTE strategy has '
            'different tail dynamics (smaller σ-cushion, faster theta). '
            'STRATEGY_15DTE sets LO=0.0, HI=0.0, FLOOR=1.0 → mechanism is a '
            'no-op even though the constants are wired in mc15/bc15.'
        ),
        engine_files_30=(
            'monte_carlo.py', 'backtest_cascade.py',
            'monte_carlo_15dte.py', 'backtest_cascade_15dte.py',
        ),
        engine_files_15=(
            'monte_carlo_15dte.py', 'backtest_cascade_15dte.py',
        ),
        ship_date_30='2026-05-04',
        notes=(
            'When running portfolio DD ∈ [LO, HI], scale call alloc linearly '
            'from 1.0 down to FLOOR. Above HI = full floor. Below LO = no '
            'effect. 30 DTE was recalibrated with v60 r054 to '
            'LO=0.35, HI=0.55, FLOOR=0.40 plus MAX_POSITIONS_CALL=12. '
            'The original H3 calibration was LO=0.40, HI=0.60, FLOOR=0.50 '
            'with N=500 5y DD-C -4.4pp at zero per-trade quality cost; '
            'the v60 short sweep selected the earlier/lower floor as part '
            'of callcap12_dd035055f040.'
        ),
    ),

    # ── RXDD VIX-regime call-alloc dampener (shipped 2026-06-04, 30 DTE) ───
    MechanismSpec(
        name='RXDD',
        config_fields=(
            'RXDD_ENABLED',
            'RXDD_VIX_C',
            'RXDD_VIX_W',
            'RXDD_DEPTH',
            'RXDD_DD_MIN',
        ),
        dte_30_status='enabled',
        dte_15_status='disabled',
        dte_15_wiring_mode='not_wired',
        dte_15_reason=(
            'VIX-band call dampener not validated under bounded-fill MC for the '
            'half-DTE strategy (smaller premium-mult, faster theta). Not wired '
            'into mc15/bc15; STRATEGY_15DTE.RXDD_ENABLED=False for schema parity.'
        ),
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py'),
        ship_date_30='2026-06-04',
        notes=(
            'Smooth Gaussian contraction of CALL alloc in the low-EV VIX ~20-26 '
            'slow-bleed band: alloc *= 1 - DEPTH*exp(-0.5*((vix-VIX_C)/VIX_W)^2), '
            'gated to running DD >= DD_MIN. Panic (VIX>=28) and calm (<20) tape '
            'untouched (collapse-safe). c00: VIX_C=22.701/W=3.14/DEPTH=0.447/'
            'DD_MIN=0.077. Validated N=500x8 ship-gate: 5y WorstDD -5.6pp AND '
            'compound +9.4%, collapse=0 incl 2020-COVID. experiments/regime_dd_v70/.'
        ),
    ),

    # ── MWDD McClellan breadth-momentum flat-band call dampener (shipped 2026-06-05, 30 DTE) ─
    MechanismSpec(
        name='MWDD',
        config_fields=(
            'MWDD_ENABLED',
            'MWDD_MCC_C',
            'MWDD_MCC_W',
            'MWDD_DEPTH',
            'MWDD_DD_MIN',
            'MWDD_VIX_PANIC',
        ),
        dte_30_status='enabled',
        dte_15_status='disabled',
        dte_15_wiring_mode='not_wired',
        dte_15_reason=(
            'McClellan flat-band call dampener not validated under bounded-fill MC '
            'for the half-DTE strategy. Not wired into mc15/bc15; '
            'STRATEGY_15DTE.MWDD_ENABLED=False for schema parity.'
        ),
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py'),
        ship_date_30='2026-06-05',
        notes=(
            'Smooth Gaussian contraction of CALL alloc in the low-EV flat/topping '
            'McClellan band (~0): alloc *= 1 - DEPTH*exp(-0.5*((mcc-MCC_C)/MCC_W)^2), '
            'gated to running DD >= DD_MIN, VIX-panic-excluded (>=VIX_PANIC: '
            'capitulation = mean-reversion winners, left alone -> COVID untouched). '
            'Orthogonal to RXDD(VIX) + F3F(breadth level). c00: MCC_C=-0.336/W=22.185/'
            'DEPTH=0.337/DD_MIN=0.128/VIX_PANIC=28. Validated N=500x10 ship-gate: 5y '
            'WorstDD -2.6pp / 22-now -5.5pp, every window DD down, collapse=0 incl '
            '2020-COVID. experiments/market_wave_dd_v70/.'
        ),
    ),

    # ── TVDD TRIN volume-flow neutral-band call dampener (shipped 2026-06-07, 30 DTE) ─
    MechanismSpec(
        name='TVDD',
        config_fields=(
            'TVDD_ENABLED',
            'TVDD_TRIN_C',
            'TVDD_TRIN_W',
            'TVDD_DEPTH',
            'TVDD_DD_MIN',
            'TVDD_VIX_PANIC',
        ),
        dte_30_status='enabled',
        dte_15_status='disabled',
        dte_15_wiring_mode='not_wired',
        dte_15_reason=(
            'TRIN volume-flow neutral-band call dampener not validated under bounded-fill '
            'MC for the half-DTE strategy. Not wired into mc15/bc15; '
            'STRATEGY_15DTE.TVDD_ENABLED=False for schema parity.'
        ),
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py'),
        ship_date_30='2026-06-07',
        notes=(
            'Smooth Gaussian contraction of CALL alloc in the low-EV neutral volume-flow '
            'band (TRIN ~1.0-1.3): alloc *= 1 - DEPTH*exp(-0.5*((trin-TRIN_C)/TRIN_W)^2), '
            'gated to running DD >= DD_MIN, VIX-panic-excluded (>=VIX_PANIC). TRIN extremes '
            '(froth <0.7, panic >1.8) = mean-reversion/momentum winners, left alone. 4th '
            'orthogonal DD lever: distinct from RXDD(VIX) + MWDD(McClellan count-momentum) + '
            'F3F(breadth level) -- a volume-flow-vs-breadth-momentum divergence (survives the '
            'all-levers-off slice at mpnl -0.060, z+57). c00: TRIN_C=1.042/W=0.268/DEPTH=0.426/'
            'DD_MIN=0.291/VIX_PANIC=28. Validated N=500x10 ship-gate: 5y WorstDD -3.1pp AND '
            'compound +17% (22-now +28%), 2020_crash DD -8.3pp, collapse=0 incl 2020-COVID. '
            'experiments/dd_residual_v70/.'
        ),
    ),

    # ── SVR semivol_r skew-bridge entry filter (shipped 2026-06-05, 30 DTE) ─
    MechanismSpec(
        name='SVR',
        config_fields=(
            'SVR_ENABLED',
            'SVR_LO_CUT',
            'SVR_LO_FULL',
            'SVR_HI_FULL',
            'SVR_HI_CUT',
            'SVR_FLOOR',
        ),
        dte_30_status='enabled',
        dte_15_status='disabled',
        dte_15_wiring_mode='not_wired',
        dte_15_reason=(
            'semivol_r skew-bridge entry filter not validated under bounded-fill MC '
            'for the half-DTE strategy. Not wired into mc15/bc15; '
            'STRATEGY_15DTE.SVR_ENABLED=False for schema parity.'
        ),
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py'),
        ship_date_30='2026-06-05',
        notes=(
            'Smooth band-pass CALL-alloc scale by per-signal semivol_r = std(downside '
            '60d returns)/std(upside) — the live, 10y-MC-computable cousin of option '
            'put-skew. Low (~0.5, euphoric/expensive call) = worst per-trade cohort; '
            'very-high (~1.4, crash-mode) weak; ~0.7-1.25 sweet spot. alloc contracts '
            'toward FLOOR below LO_FULL and above HI_FULL. gentleband c00: LO_CUT=0.5/'
            'LO_FULL=0.7/HI_FULL=1.25/HI_CUT=1.65/FLOOR=0.5. Validated N=500x8: 5y '
            'WorstDD -5.8pp AND compound +28.6%, collapse=0 incl 2020-COVID. '
            'experiments/apex_speed_v70/.'
        ),
    ),

    # ── Practical exposure saturation ─────────────────────────────────────
    MechanismSpec(
        name='PRACTICAL_EXPOSURE_SATURATION',
        config_fields=(
            'PRACTICAL_EXPOSURE_ENABLED',
            'PRACTICAL_CAPITAL_CEILING',
            'GROSS_PREMIUM_CAP', 'CALL_PREMIUM_CAP', 'PUT_PREMIUM_CAP',
            'OPP_SAT_CALL_REF', 'OPP_SAT_PUT_REF',
            'OPP_SAT_POWER', 'OPP_SAT_FLOOR',
        ),
        dte_30_status='enabled',
        dte_15_status='disabled',
        dte_15_wiring_mode='not_wired',
        dte_15_reason=(
            'Not validated for 15 DTE. The half-DTE portfolio already uses a '
            'smaller eight-slot pool and different tail/theta dynamics; ship '
            'requires a separate 15 DTE bounded-fill MC validation.'
        ),
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py'),
        ship_date_30='2026-05-21',
        notes=(
            'Stage 3 Sentinel profile candidate g80_c65_p25_ref16_4_pow05_floor55_25m. '
            'Caps deployable premium to a practical base: capital ceiling '
            '$25M, gross 80%, call 65%, put 25%; side caps calls=12, puts=8. '
            'Smooth opportunity saturation scales same-day dense supply with '
            'call_ref=16, put_ref=4, power=0.50, floor=0.55. Prior N=1000 '
            'cross-version confirmation showed avg worst-DD +10.49pp, max DD '
            'worse +0.00pp, and v60 2025_dip worst DD 56.70% -> 48.09%.'
        ),
    ),

    # ── F3F breadth-driven allocation ─────────────────────────────────────
    # Both DTEs enabled with different floor calibrations (30 DTE floor=0.50,
    # 15 DTE floor=0.40). Documented for completeness so the registry has
    # a record of "this knob exists on both, calibration differs by DTE".
    MechanismSpec(
        name='F3F_BREADTH_ALLOC',
        config_fields=(
            'F3F_CALL_THRESH', 'F3F_CALL_FLOOR', 'F3F_CALL_LOW',
            'F3F_PUT_THRESH', 'F3F_PUT_FLOOR', 'F3F_PUT_HIGH',
            'ALLOC_SCALE_FLOOR', 'ALLOC_SCALE_CEIL',
            'BREADTH_ALLOC_ENABLED',
        ),
        dte_30_status='enabled',
        dte_15_status='enabled',
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py',
                         'monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        engine_files_15=('monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        ship_date_30='2026-04-24',
        ship_date_15='2026-04-24',
        notes=(
            'At each signal date, lookup breadth_score and scale tier alloc. '
            'Asymmetric: cuts calls when breadth low, cuts puts when breadth '
            'high. 15 DTE floors are tighter (0.40 vs 0.50) for stronger '
            'weak-tape contraction.'
        ),
    ),

    # ── Dead-hold post-SL ──────────────────────────────────────────────────
    # Both DTEs enabled with identical T=-0.50 / P=-0.25.
    MechanismSpec(
        name='DEAD_HOLD_POST_SL',
        config_fields=(
            'DEAD_HOLD_ENABLED',
            'DEAD_HOLD_TRIGGER_PNL',
            'DEAD_HOLD_POPOUT_PNL',
        ),
        dte_30_status='enabled',
        dte_15_status='enabled',
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py',
                         'monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        engine_files_15=('monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        ship_date_30='2026-05-01',
        ship_date_15='2026-05-01',
        notes=(
            'When SL fires AND realized option pnl ≤ TRIGGER_PNL, hold '
            'forward bar-by-bar instead of selling. Exit at intraday extreme '
            '≥ POPOUT_PNL or hard-sell day. T=-0.50/P=-0.25 only variant '
            'clearing 80% Conservative DD floor on every window for both DTEs.'
        ),
    ),

    # ── CT cascade promotion ───────────────────────────────────────────────
    MechanismSpec(
        name='CT_CASCADE_PROMOTION',
        config_fields=('CT_PROMOTE', 'CT_PUT_TREND_MIN', 'CT_CALL_TREND_MAX'),
        dte_30_status='enabled',
        dte_15_status='enabled',
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py',
                         'monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        engine_files_15=('monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        ship_date_30='2026-04-21',
        ship_date_15='2026-04-21',
        notes=(
            'ct_call (overall≥70 AND TREND≤MAX) → ultra tier. '
            'ct_put (overall≤25 AND TREND≥MIN) → put_top tier. '
            'Both DTEs: put_min=80, call_max=20.'
        ),
    ),

    # ── EARN_SUPP_PUT (RETIRED) ────────────────────────────────────────────
    # Retired 2026-05-06 — replaced by score-stage PESS in v39. Both DTEs
    # set the flag to False. Schema parity preserved for instant re-enable.
    MechanismSpec(
        name='EARN_SUPP_PUT',
        config_fields=(
            'EARN_SUPP_PUT',
            'EARN_SUPP_PUT_DAYS',
            'EARN_SUPP_PUT_MIN_OV',
            'EARN_SUPP_PUT_MAX_OV',
        ),
        dte_30_status='disabled',
        dte_15_status='disabled',
        dte_30_wiring_mode='wired_neutral',
        dte_15_wiring_mode='wired_neutral',
        dte_30_reason=(
            'RETIRED 2026-05-06: replaced by score-stage PESS in v39 (commit '
            '200f33a). The score itself now lifts puts in [16,20] with '
            'earnings within 5 trd days OUT of put-qualifying range '
            '(target=28), so the cascade-stage filter is redundant. Schema '
            'parity preserved (flag=False) for instant re-enable.'
        ),
        dte_15_reason=(
            'RETIRED 2026-05-06: same as 30 DTE. Replaced by score-stage PESS.'
        ),
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py',
                         'monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        engine_files_15=('monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        ship_date_30='2026-04-26',  # original ship date
        ship_date_15='2026-04-26',
        notes='See known-issues.md "CLOSED — SHIPPED" timeline for retirement context.',
    ),

    # ── WEAK_WEEKLY_CALL_DROP (RETIRED) ────────────────────────────────────
    # Retired 2026-05-06 — replaced by score-stage CWWD in v38.
    MechanismSpec(
        name='WEAK_WEEKLY_CALL_DROP',
        config_fields=(
            'WEAK_WEEKLY_CALL_DROP',
            'WEAK_WEEKLY_CALL_MIN_OV',
            'WEAK_WEEKLY_CALL_MAX_OV',
            'WEAK_WEEKLY_CALL_WADJ_LT',
            'WEAK_WEEKLY_CALL_STOCH_GE',
        ),
        dte_30_status='disabled',
        dte_15_status='disabled',
        dte_30_wiring_mode='wired_neutral',
        dte_15_wiring_mode='wired_neutral',
        dte_30_reason=(
            'RETIRED 2026-05-06: replaced by score-stage CWWD in v38 '
            '(commit b093e2d). The score itself now drifts wadj-neg 70-74 '
            'cohorts below 70 (out of qualifying universe), so the '
            'cascade-stage filter is redundant.'
        ),
        dte_15_reason=(
            'Was never validated for 15 DTE (filter shipped 30 DTE only); '
            'now superseded by v38 CWWD score-stage which is DTE-agnostic.'
        ),
        engine_files_30=('monte_carlo.py', 'backtest_cascade.py',
                         'monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        engine_files_15=('monte_carlo_15dte.py', 'backtest_cascade_15dte.py'),
        ship_date_30='2026-05-05',
        ship_date_15='',  # never shipped to 15 DTE
        notes='See experiments/call_wadj_70_filter/FINDINGS.md for history.',
    ),
)


# Validate at module load — surfaces typos / missing reasons immediately
for _spec in REGISTRY:
    _validate_spec(_spec)


# ─── Query helpers (used by tests + ship-procedure tooling) ──────────────


def get_by_name(name: str) -> MechanismSpec:
    for s in REGISTRY:
        if s.name == name:
            return s
    raise KeyError(f'No mechanism named {name!r}; known: {[s.name for s in REGISTRY]}')


def asymmetric_mechanisms() -> Tuple[MechanismSpec, ...]:
    """Mechanisms where 30 DTE and 15 DTE statuses differ.

    Useful for the ship procedure: "before claiming a mechanism is shipped,
    check it against this list — if asymmetric, both statuses must have
    explicit reasons."
    """
    return tuple(s for s in REGISTRY if s.dte_30_status != s.dte_15_status)


def all_config_fields() -> set:
    """Union of every config field claimed by any mechanism in the registry.

    Used to detect "field exists in strategy_config but isn't claimed by any
    registry entry" — that's the signal a mechanism shipped without being
    documented here. Not a hard error (some fields like VOL_LOOKBACK are
    pure infrastructure, not mechanisms), but useful as a soft warning.
    """
    out = set()
    for s in REGISTRY:
        out.update(s.config_fields)
    return out
