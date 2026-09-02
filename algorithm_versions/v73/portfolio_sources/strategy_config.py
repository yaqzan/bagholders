"""Single source of truth for shipped strategy parameters.

Exists to collapse the 7-location duplication documented in CLAUDE.md
"Shipping a Portfolio Strategy Change" into one file. Consumer files
(monte_carlo.py, backtest_cascade.py, trader.py CLI, api.py, JS) import
from here as the canonical default; their module-level constants remain
as a stable API surface but their VALUES come from the dataclasses below.

Two layers:
  * `OptionStrategyConfig` — premium-side rules (TP%, SL%, slippage,
    PUT_TP, PUT_SL, hold-bars-after-entry). Aliased between DTE configs
    when both ship with identical option mechanics, which is the current
    state. The shared instance is `SHARED_OPTION` below.
  * `DteStrategyConfig` — DTE-specific knobs (HOLD_DAYS, PREMIUM_MULT,
    HARD_SELL_LOSS, cascade allocs, MaxPos, F3f floors, regime slopes,
    drawdown soft-band, earnings put suppression). One instance per
    shipped DTE strategy; embeds an `OptionStrategyConfig` via composition.

Derived values (NET_TP, TP_SIGMA, etc.) are computed as `@property` on
the dataclass — never duplicated, never get out of sync.

Env-var override pattern (for MC sweeps): consumer modules wrap each
field in `float(os.environ.get('NAME', _cfg.field))`. The dataclass
itself is `frozen` and never mutated; sweeps mutate the consumer's
module-level binding. See monte_carlo.py for the canonical example.

Strategy equivalence (for assess dedupe): `assess_combos()` returns the
(dte, metric) combos to run. When `STRATEGY_30DTE.option is
STRATEGY_15DTE.option` (Python identity — both alias `SHARED_OPTION`),
the function emits one TP% combo instead of two. If a future ship
constructs a separate `OptionStrategyConfig` instance for one DTE, the
identity check fails and both DTE TP% runs are emitted again.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, Mapping, Optional, Tuple


# ─── Calibration discipline ──────────────────────────────────────────────────
#
# CALIBRATION_CUTOFF_DATE — the out-of-sample holdout line.
#
# RE-LOCKED 2026-06-11 at 2026-06-15 (ship-process reform). History: the original
# 2026-05-15 lock was removed 2026-06-04 per user direction because its forward
# window was only ~3 weeks old — too few barrier-resolved peaks to validate
# anything, while it DID block calibrating on the most recent (current-regime)
# data. That objection does not apply to a FRESH lock: it is not being evaluated
# today, it accumulates. The v71 integrity ship (2026-06-10) removed the known
# look-ahead leaks, making this the cleanest substrate the project has had — the
# best possible moment to start an honest forward window. With 30+ mechanisms
# calibrated on overlapping 5y data, stacking risk is otherwise unmeasured.
#
# Effect: `experiments._holdout.assert_no_holdout_leak` / `pre_cutoff_filter`
# enforce date <= 2026-06-15 in calibration sweeps. `HOLDOUT_DISABLE=1` remains
# the explicit bypass for live-trading evaluation (NOT calibration). To disable
# entirely, set back to None (and update the docs that claim OOS protection).
#
# Re-evaluation target: ≈ 2026-12-15 — first OOS read on the v71/v72 stack.
CALIBRATION_CUTOFF_DATE: str | None = "2026-06-15"


# ─── Score-stage configuration (DTE-agnostic) ────────────────────────────────
#
# All knobs touching `compute_overall_score` and its helpers
# (`calculate_weekly_adjustment`, `calculate_weekly_composite`). Surfaced here
# so:
#   1. Sweep scripts can override any field via env var
#      (e.g. `MCD_ALPHA=0.85 python -m experiments.my_sweep`).
#   2. `tests/test_strategy_config_drift.py` enforces that scoring.py
#      module-level constants exactly equal these defaults — drift-guard
#      catches the same class of "edited scoring.py without bumping
#      strategy_config" bugs the portfolio drift-guard catches today.
#   3. Per-mechanism Stage 1 sweeps under the three-stage framework
#      (assessment-backtest.md) can be expressed as `dataclasses.replace(
#      SCORING, FIELD=NEW_VALUE)` rather than monkey-patching scoring.py.
#
# Single singleton `SCORING` — scoring is DTE-agnostic by design (same
# `compute_overall_score` runs for both 30 DTE and 15 DTE strategies). If a
# future ship splits per-DTE score-stage tuning, add `SCORING_30DTE` and
# `SCORING_15DTE` and embed via composition into `DteStrategyConfig`.

@dataclass(frozen=True)
class ScoringConfig:
    """Single source of truth for `database/utils/scoring.py` constants.

    Field names match the module-level binding name in scoring.py exactly so
    the drift-guard can pair them by string identity. New fields land here
    first; the corresponding scoring.py constant is then derived from
    `SCORING.<FIELD>` via `os.environ.get(<FIELD>, str(SCORING.<FIELD>))`
    so sweeps preserve their env-var override pattern.
    """

    # ── Component weighting (compute_overall_score) ──────────────────────
    # Sideways weights: w_X applied when trend_dominance d=0.
    # Trend slopes: increment on top of base when d=1 (full trend regime).
    # `w_trend = W_TREND_BASE + W_TREND_SLOPE * d`, etc.
    # V6 weights (cap trend at 28 vs old 35; redistribute 7pts to RSI/MACD).
    # See compute_overall_score docstring rationale.
    W_TREND_BASE: float           # 18 — sideways weight on TREND component
    W_TREND_SLOPE: float          # 10 — added at full trend dominance
    W_BB_BASE: float              # 18 — flat across regimes
    W_RSI_BASE: float             # 25
    W_RSI_SLOPE: float            # -9
    W_MACD_BASE: float            # 25
    W_MACD_SLOPE: float           # -6
    W_STOCH_BASE: float           # 5  — flat
    W_TA_BASE: float              # 9
    W_TA_SLOPE: float             # 6

    TREND_BIAS_SCALE: float       # 0.06  — `tanh((trend - 50) * scale)`
    TREND_DOMINANCE_POWER: float  # 0.7   — `trend_strength ** power`
    BB_DOMINANCE_DAMP: float      # 0.5   — `1 - damp * (bull_ext + bear_ext)`
    OSC_DOMINANCE_DAMP: float     # 0.7   — `1 - damp * osc_divergence`

    # ── Asymmetric MACD/RSI gates (compute_overall_score) ────────────────
    PUT_MACD_GATE: float          # 45.0  — pre-MACD score < this → zero MACD weight
    PUT_RSI_ZERO_GATE: float      # 40    — pre-RSI score < this → zero RSI input

    # ── Volume amplification (compute_overall_score middle block) ────────
    VOL_BOOST_TREND_SCALE: float  # 0.6   — `vol_boost = 1.0 + scale * trend_strength`
    VOL_EMA_DAMP_THRESH: float    # 15.0  — |pct_from_ema50| > thresh → dampen
    VOL_EMA_DAMP_K: float         # 0.5   — max dampening magnitude
    VOL_EMA_DAMP_RANGE: float     # 25.0  — dampening saturates at thresh+range
    BLEND_W_CAP: float            # 0.60  — REVERSAL blend weight ceiling
    BLEND_REVERSAL_HALF: float    # 0.50  — halve blend if MACD contradicts
    BLEND_REVERSAL_GATE: float    # 25.0  — `abs(weighted_sum - 50) < this` → contradiction check
    BLEND_VOL_TARGET_LO: float    # 35.0  — vol_target < this = bearish reversal
    BLEND_VOL_TARGET_HI: float    # 65.0  — vol_target > this = bullish reversal
    BLEND_MACD_LO: float          # 40.0  — macd < this = bearish momentum
    BLEND_MACD_HI: float          # 60.0  — macd > this = bullish momentum
    CONVICTION_MACD_CAP: float    # 0.25  — cap CONVICTION amp magnitude on contradiction

    # ── JA4 put-regime blend (compute_overall_score) ─────────────────────
    # No values to surface — currently `put_regime_multiplier` is computed
    # upstream (in regime loader) and passed into compute_overall_score.

    # ── Mis-stress softener (Priority #6/#8) ─────────────────────────────
    MIS_STRESS_CALL_DAMPEN: float  # 0.25

    # ── Capitulation gradient dampener (post-regime) ─────────────────────
    CAP_GATE_SCORE: int           # 0     — only fires at overall == this
    CAP_EMA_THRESH: float         # -10.0 — pct_from_ema50 < this required
    CAP_BASE_LIFT: float          # 5.0   — `5 + (ext - 10)`
    CAP_RAMP_OFFSET: float        # 10.0  — subtracted from ext before ramp
    CAP_LIFT_CAP: int             # 20    — clamp output to this max

    # ── Exhaustion gradient dampener ─────────────────────────────────────
    EXH_GATE: int                 # 9     — overall <= this fires
    EXH_GATE_DENOM: float         # 9.0   — `(9 - overall) / denom`
    EXH_MACDH_SCALE: float        # 0.05  — `tanh(macdh / scale)`
    EXH_K: float                  # 0.5   — max pull magnitude
    EXH_TARGET: float             # 10.0  — drift toward

    # ── Ext-focal gradient dampener ──────────────────────────────────────
    EXT_FOCAL_GATE: int           # 25    — overall <= this fires
    EXT_FOCAL_RAMP: float         # 10.0  — ext / ramp = saturating ratio
    EXT_FOCAL_K: float            # 0.5   — max lift fraction

    # ── WCF lift (Priority #13 ship 2026-04-27) ──────────────────────────
    # Lifts puts toward 50 when weekly is non-confirming.
    WCF_LIFT_GATE: int            # 28    — overall < this fires
    WCF_LIFT_WADJ_CUTOFF: float   # -17.0 — wadj > this required
    WCF_LIFT_K: float             # 0.95
    WCF_LIFT_TARGET: int          # 50
    # Score-axis ramp top (candidate 2026-06-11): full lift at overall <= GATE-1,
    # fading linearly to zero at RAMP_TOP. RAMP_TOP == GATE reproduces the
    # original binary gate bit-exactly. Smooths the 27/28 intraday score cliff
    # (21-pt wcf_lift toggle — GIS/CBRE/GEHC fakeout family, 2026-06-10).
    # Calibration: experiments/wcf_score_ramp/ — 33 beats 31 on the stability
    # replay (-60% vs -23% affected fakeout groups) at identical bucket safety.
    WCF_LIFT_RAMP_TOP: int        # 33   — overall >= this gets zero lift

    # ── Call WCF mirror dampener (v32 ship 2026-05-01) ───────────────────
    # Mirror of v27 put WCF on calls: pull score down toward 55.
    CWCF_DAMPEN_GATE: int         # 75
    CWCF_DAMPEN_WADJ_CUTOFF: float  # 1.0
    CWCF_DAMPEN_WADJ_RANGE: float   # 1.0  — `(cutoff - wadj) / range`
    CWCF_DAMPEN_K: float          # 0.95
    CWCF_DAMPEN_TARGET: int       # 55

    # ── Call Weak-Weekly Dampener CWWD (v38 ship 2026-05-06) ─────────────
    # Extends CWCF below 75 (70-74 zone with wadj<0).
    CWWD_DAMPEN_GATE_LO: int      # 70
    CWWD_DAMPEN_GATE_HI: int      # 75   — exclusive
    CWWD_DAMPEN_STOCH_FLOOR: float  # 25.0  — stoch grad starts at this value
    CWWD_DAMPEN_STOCH_RANGE: float  # 35.0  — stoch grad saturates at floor+range
    CWWD_DAMPEN_WADJ_RANGE: float   # 5.0   — `-wadj / range`
    CWWD_DAMPEN_K: float          # 0.95
    CWWD_DAMPEN_TARGET: int       # 55

    # ── Stoch-Weekly Contradiction CSWC (v36 recalibration 2026-05-05) ───
    CSWC_DAMPEN_GATE: int         # 75
    CSWC_DAMPEN_WADJ_LO: float    # 1.0   — only fires when wadj in [LO, HI)
    CSWC_DAMPEN_WADJ_HI: float    # 14.0
    CSWC_DAMPEN_STOCH_RANGE: float  # 35.0  — `(35 - stoch) / range`
    CSWC_DAMPEN_WADJ_DENOM: float   # 13.0  — `(HI - wadj) / denom`
    CSWC_DAMPEN_K: float          # 0.50
    CSWC_DAMPEN_TARGET: int       # 55

    # ── SCW — Stoch Conviction Wave (conservative v48 candidate 2026-05-11)
    SCW_ENABLED: bool             # True
    SCW_GATE_CALL: int            # 70
    SCW_MAX_PENALTY: float        # 8.0
    SCW_STOCH_POWER: float        # 1.5
    SCW_DECAY_POWER: float        # 6.0
    SCW_WEEKLY_HI: float          # 14.0
    SCW_SCALE: float
    SCW_BOUNDARY_RELIEF: float
    SCW_BOUNDARY_WIDTH: float
    SCW_CONFIRM_RELIEF: float
    SCW_CONFIRM_MID: float
    SCW_RAW_STOCH_RELIEF: float
    SCW_RAW_STOCH_MID: float
    SCW_EXT_TAPER_STRENGTH: float
    SCW_EXT_TAPER_MID: float
    SCW_EXT_TAPER_WIDTH: float

    # ── Continuation echo wave (v51 ship; v58 retune retired 2026-05-15) ──
    CONT_BOOST_ENABLED: bool      # True
    CONT_BOOST_SIG_MIN: float     # 0.0254 — minimum positive echo
    CONT_BOOST_TAU: float         # 38.16  — exp decay on gap days
    CONT_BOOST_MAG_EXP: float     # 0.8436 — prior conviction exponent
    CONT_BOOST_SIG_NORM: float    # 0.9033 — final tanh divisor
    CONT_BOOST_GATE_LO: int       # 50     — call-side only
    CONT_BOOST_GATE_HI: int       # 84     — target zone below 85
    CONT_BOOST_PROMOTE_TARGET: int  # 75    — retained only if new score reaches this
    CONT_BOOST_TARGET: float      # 85.0   — smooth lift target
    CONT_BOOST_ALPHA: float       # 1.1669 — echo lift scale
    CONT_BOOST_MAX_LIFT: float    # 4.4739 — cap per signal
    CONT_BOOST_W7: float          # W7 continuation weight
    CONT_BOOST_W15: float         # W15 continuation weight
    CONT_BOOST_W30: float         # W30 continuation weight
    CONT_BOOST_W60: float         # W60 continuation weight
    CONT_BOOST_LOSS_PENALTY: float
    CONT_BOOST_FIZZLER_PENALTY: float

    # ── Sector breadth wave ──────────────────────────────────────────────
    # Score-stage sector ETF participation echo/thrust wave. Crash echo pulls
    # CALL signal-band scores toward neutrality; bull-repair thrust pulls PUT
    # signal-band scores toward neutrality. Grouped as a parameter map so this
    # does not leak 20+ research-specific scalar names into the core config API.
    SECTOR_BREADTH_WAVE_ENABLED: bool
    SECTOR_BREADTH_WAVE_CALL_MIN: int
    SECTOR_BREADTH_WAVE_PUT_MAX: int
    SECTOR_BREADTH_WAVE_PARAMS: Mapping[str, float]

    # ── Post-Crash put Dampener PCD (v37 ship 2026-05-05) ────────────────
    PCD_ENABLED: bool             # True
    PCD_GATE: int                 # 25    — overall <= this required
    PCD_RET10D_SIGMA: float       # -1.0  — 10-bar ret in σ-units
    PCD_TARGET: int               # 30    — lift to at least this

    # ── MCD — Mcap Dampener (v43 ship 2026-05-07) ────────────────────────
    MCD_ENABLED: bool             # True
    MCD_GATE_LO: int              # 70
    MCD_GATE_HI: int              # 84
    MCD_LOG_LO: float             # 0.50  — $3.16B
    MCD_LOG_HI: float             # 1.90  — $79.43B
    MCD_ALPHA: float              # 0.80
    MCD_TARGET: int               # 61
    MCD_MCAP_POWER: float         # 0.70
    MCD_SCORE_POWER: float        # 1.50

    # ── ICH — Ichimoku Kijun-sen state dampener (v44 ship 2026-05-07) ────
    ICH_ENABLED: bool             # True
    ICH_GATE_CALL_LO: int         # 69
    ICH_GATE_CALL_HI: int         # 90
    ICH_K_CALL: float             # 0.359
    ICH_K_CALL_POWER: float       # 2.68  — power on score_norm
    ICH_KIJ_SAT_CALL: float       # 18.4
    ICH_TARGET_CALL: float        # 63.8
    ICH_GATE_PUT_LO: int          # 10
    ICH_GATE_PUT_HI: int          # 27
    ICH_K_PUT: float              # 0.278
    ICH_KIJ_SAT_PUT: float        # 8.8
    ICH_TARGET_PUT: float         # 33.4
    ICH_IND_RAMP_CALL: str        # 'linear'
    ICH_IND_RAMP_PUT: str         # 'log'

    # ── WVD-Wave (Phase Wave ship 2026-05-08) ────────────────────────────
    WVD_WAVE_ENABLED: bool        # True
    WVD_WAVE_GATE_LO: int         # 70
    WVD_WAVE_GATE_HI: int         # 85
    WVD_WAVE_SCORE_POWER: float   # 1.0
    WVD_WAVE_PEAK: float          # 0.0   — Gaussian center on wv_force1
    WVD_WAVE_WIDTH: float         # 0.08
    WVD_WAVE_K_LIFT: float        # 0.15
    WVD_WAVE_TARGET_LIFT: float   # 82.0
    WVD_WAVE_CLIMAX_THRESH: float  # 0.05  — dampen ramp starts above this
    WVD_WAVE_CLIMAX_SAT: float    # 0.15
    WVD_WAVE_K_DAMPEN: float      # 0.40
    WVD_WAVE_TARGET_DAMPEN: float  # 55.0

    # ── Daily Volume Authority Wave (staging candidate) ─────────────────
    # Late score-stage CALL-side daily volume conviction wave. Daily
    # conviction supplies the raw impulse, weekly volume force governs how
    # much authority that impulse gets, and a smooth high-score fade preserves
    # the 90+ tier.
    DAILY_VOLUME_AUTHORITY_WAVE_ENABLED: bool
    DAILY_VOLUME_AUTHORITY_WAVE_PARAMS: Mapping[str, float]

    # ── PESS — Put Earnings Score Suppression (v39 ship 2026-05-06) ──────
    PESS_GATE_LO: int             # 16    — overall in [LO, HI] required
    PESS_GATE_HI: int             # 20
    PESS_DAYS_MIN: int            # 1     — d_to_ern in [MIN, MAX] required
    PESS_DAYS_MAX: int            # 7
    PESS_FADE_WIDTH: float        # 3.0   — score-grad fade width on edges
    PESS_PROXIMITY_FULL_DAY: int  # 5     — proximity = 1 for d <= this
    PESS_PROXIMITY_FADE_END: int  # 8     — proximity = 0 at d >= this
    PESS_K: float                 # 0.95
    PESS_TARGET: int              # 28

    # ── Earnings meta-score boost (Phase 3C / v35 recalibration) ─────────
    EARN_BOOST_ENABLED: bool      # True
    EARN_BOOST_WINDOW: int        # 5     — trading days window
    EARN_BOOST_MAX: float         # 0.55  — max boost magnitude
    EARN_BOOST_LIFT_NORM_CALL: float  # 14.0  — log-norm divisor for calls
    EARN_BOOST_LIFT_NORM_PUT: float   # 16.3
    EARN_BOOST_MIN_N: int         # 10
    EARN_BOOST_PUT_ADMIT: bool    # False — admit boundary puts via boost

    # ── Weekly adjustment (calculate_weekly_adjustment) ──────────────────
    WEEKLY_BASE_BIAS_MAX: float          # 15.0 — `15 * tanh(...)`
    WEEKLY_BASE_BIAS_DEV_SCALE: float    # 1.5  — `tanh(deviation * scale)`
    WEEKLY_AGREEMENT_BASE: float         # 0.8  — agreement floor
    WEEKLY_AGREEMENT_AMP: float          # 0.6  — `base + amp * consistency`
    WEEKLY_MOMENTUM_MAX: float           # 8.0  — `8 * tanh(delta / scale)`
    WEEKLY_MOMENTUM_DELTA_SCALE: float   # 15.0
    WEEKLY_PUT_SCALE: float              # 1.5  — asymmetric put-side amp
    WEEKLY_PUT_WAVE_ENABLED: bool        # True — smooth put-side amp by |raw wadj|
    WEEKLY_PUT_WAVE_FLOOR: float         # 1.0  — tiny bearish weekly amp
    WEEKLY_PUT_WAVE_PEAK: float          # 1.5  — saturated bearish weekly amp
    WEEKLY_PUT_WAVE_WIDTH: float         # 10.0 — tanh width in score points
    WEEKLY_PUT_WAVE_POWER: float         # 1.0  — curvature on |raw wadj| / width
    WEEKLY_AGREEMENT_AVG_THRESH: float   # 0.01 — `avg_signal > thresh` consistency check

    # ── Weekly composite (calculate_weekly_composite) ────────────────────
    WCOMP_NO_TREND_RSI: float            # 0.55
    WCOMP_NO_TREND_MACD: float           # 0.45
    WCOMP_TREND_BASE: float              # 0.35
    WCOMP_RSI_BASE: float                # 0.35
    WCOMP_MACD_BASE: float               # 0.30
    WCOMP_DAMPEN_HALF_GAP: float         # 50.0 — gap saturation point
    WCOMP_DAMPEN_K: float                # 0.5  — dampening magnitude

    # ── Misc legacy ──────────────────────────────────────────────────────
    MOVING_AVERAGE_PERIOD: int    # 20
    MOMENTUM_LOOKBACK_DAYS: int   # 3


SCORING = ScoringConfig(
    # Component weighting
    W_TREND_BASE=18.0,
    W_TREND_SLOPE=10.0,
    W_BB_BASE=18.0,
    W_RSI_BASE=25.0,
    W_RSI_SLOPE=-9.0,
    W_MACD_BASE=25.0,
    W_MACD_SLOPE=-6.0,
    W_STOCH_BASE=5.0,
    W_TA_BASE=9.0,
    W_TA_SLOPE=6.0,
    TREND_BIAS_SCALE=0.06,
    TREND_DOMINANCE_POWER=0.7,
    BB_DOMINANCE_DAMP=0.5,
    OSC_DOMINANCE_DAMP=0.7,
    # Asymmetric gates
    PUT_MACD_GATE=45.0,
    PUT_RSI_ZERO_GATE=40.0,
    # Volume amplification
    VOL_BOOST_TREND_SCALE=0.6,
    VOL_EMA_DAMP_THRESH=15.0,
    VOL_EMA_DAMP_K=0.5,
    VOL_EMA_DAMP_RANGE=25.0,
    BLEND_W_CAP=0.60,
    BLEND_REVERSAL_HALF=0.50,
    BLEND_REVERSAL_GATE=25.0,
    BLEND_VOL_TARGET_LO=35.0,
    BLEND_VOL_TARGET_HI=65.0,
    BLEND_MACD_LO=40.0,
    BLEND_MACD_HI=60.0,
    CONVICTION_MACD_CAP=0.25,
    # Mis-stress — RETIRED 2026-06-10 (v71 integrity audit). The detector read
    # the SPY weekly composite with current-week look-ahead (F2); on the
    # leak-fixed substrate its 75+ admits ran BELOW the shared-cohort baseline
    # (50.7% vs 55.2% optWR15, z=-1.03; 5y full universe). 0.0 = softener off.
    MIS_STRESS_CALL_DAMPEN=0.0,
    # Capitulation
    CAP_GATE_SCORE=0,
    CAP_EMA_THRESH=-10.0,
    CAP_BASE_LIFT=5.0,
    CAP_RAMP_OFFSET=10.0,
    CAP_LIFT_CAP=20,
    # Exhaustion
    EXH_GATE=9,
    EXH_GATE_DENOM=9.0,
    EXH_MACDH_SCALE=0.05,
    EXH_K=0.5,
    EXH_TARGET=10.0,
    # Ext-focal
    EXT_FOCAL_GATE=25,
    EXT_FOCAL_RAMP=10.0,
    EXT_FOCAL_K=0.5,
    # WCF lift — ⚠ RETIRED 2026-06-12 in v73 (K=0): the v27 founding evidence
    # (+5.9pp <25, Q1/Q4 -25pp discriminator) was look-ahead-era artifact (the
    # MCD pattern). Honest v72 ReSim A/B: removals 41.3% vs shared 41.7%
    # (z=-0.43 at <=25) — deletes ~85% of the put band with ZERO quality
    # discrimination. Assessment-surface honesty win (puts OFF portfolio-wide).
    # experiments/dampener_ablation_v72/FINDINGS.md. 0.0 = lift off.
    WCF_LIFT_GATE=28,
    WCF_LIFT_WADJ_CUTOFF=-17.0,
    WCF_LIFT_K=0.0,
    WCF_LIFT_TARGET=50,
    WCF_LIFT_RAMP_TOP=33,         # v72 ramp (inert while K=0; kept for replay)
    # CWCF — ⚠ RETIRED 2026-06-12 in v73 (K=0): honest v72 ReSim A/B — its
    # removals run 50.4% optWR15 vs 54.3% shared (z=-1.96, not statistically
    # real) while suppressing 19% of potential 75+ N; restored supply clears
    # call BE 45% by ~5pp. Part of the trio retirement adjudicated by the
    # Stage-1 growth gate (dG +19.6% option / +36.3% generic, every window).
    # experiments/dampener_ablation_v72/FINDINGS.md. 0.0 = dampener off.
    CWCF_DAMPEN_GATE=75,
    CWCF_DAMPEN_WADJ_CUTOFF=1.0,
    CWCF_DAMPEN_WADJ_RANGE=1.0,
    CWCF_DAMPEN_K=0.0,
    CWCF_DAMPEN_TARGET=55,
    # CWWD
    CWWD_DAMPEN_GATE_LO=70,
    CWWD_DAMPEN_GATE_HI=75,
    CWWD_DAMPEN_STOCH_FLOOR=25.0,
    CWWD_DAMPEN_STOCH_RANGE=35.0,
    CWWD_DAMPEN_WADJ_RANGE=5.0,
    CWWD_DAMPEN_K=0.95,
    CWWD_DAMPEN_TARGET=55,
    # CSWC — ⚠ RETIRED 2026-06-12 in v73 (K=0): honest v72 ReSim A/B — removals
    # 51.2% vs 54.2% shared (z=-1.63, not real) at 22% of potential 75+ N.
    # Trio retirement (with CWCF/SCW); growth-gate adjudicated.
    # experiments/dampener_ablation_v72/FINDINGS.md. 0.0 = dampener off.
    CSWC_DAMPEN_GATE=75,
    CSWC_DAMPEN_WADJ_LO=1.0,
    CSWC_DAMPEN_WADJ_HI=14.0,
    CSWC_DAMPEN_STOCH_RANGE=35.0,
    CSWC_DAMPEN_WADJ_DENOM=13.0,
    CSWC_DAMPEN_K=0.0,
    CSWC_DAMPEN_TARGET=55,
    # SCW — ⚠ RETIRED 2026-06-12 in v73 (False): honest v72 ReSim A/B — >=75
    # removals 50.3% vs 54.3% shared (z=-1.69) at 14% of N; the real signal at
    # >=70 (z=-2.47) lands mostly in zero-alloc 70-74. Trio retirement;
    # growth-gate adjudicated. experiments/dampener_ablation_v72/FINDINGS.md.
    SCW_ENABLED=False,
    SCW_GATE_CALL=70,
    SCW_MAX_PENALTY=8.0,
    SCW_STOCH_POWER=1.5,
    SCW_DECAY_POWER=6.0,
    SCW_WEEKLY_HI=14.0,
    SCW_SCALE=1.3,
    SCW_BOUNDARY_RELIEF=1.35,
    SCW_BOUNDARY_WIDTH=0.65,
    SCW_CONFIRM_RELIEF=0.0,
    SCW_CONFIRM_MID=0.2673067886722032,
    SCW_RAW_STOCH_RELIEF=0.05,
    SCW_RAW_STOCH_MID=71.21723387348588,
    SCW_EXT_TAPER_STRENGTH=0.25,
    SCW_EXT_TAPER_MID=1.0,
    SCW_EXT_TAPER_WIDTH=0.3,
    # Continuation echo wave
    CONT_BOOST_ENABLED=True,
    CONT_BOOST_SIG_MIN=0.025421147106629318,
    CONT_BOOST_TAU=38.15912046629926,
    CONT_BOOST_MAG_EXP=0.8435514312118402,
    CONT_BOOST_SIG_NORM=0.9032562577567048,
    CONT_BOOST_GATE_LO=50,
    CONT_BOOST_GATE_HI=84,
    CONT_BOOST_PROMOTE_TARGET=75,
    CONT_BOOST_TARGET=85.0,
    CONT_BOOST_ALPHA=1.1669054395351226,
    CONT_BOOST_MAX_LIFT=4.473918889785761,
    CONT_BOOST_W7=0.18104215061573875,
    CONT_BOOST_W15=0.022856284102839568,
    CONT_BOOST_W30=0.6756014121839538,
    CONT_BOOST_W60=0.7661245978520328,
    CONT_BOOST_LOSS_PENALTY=0.3845330519083049,
    CONT_BOOST_FIZZLER_PENALTY=0.4175780458412032,
    # Sector Market Wave direct score transform. v57 bayes_185 replaces the
    # older echo transform after v56 replay held the DD reduction on fresh
    # scores: 30DTE DD 84.08% -> 62.88%, trades 7,021 -> 6,927, COVID
    # +1506.78, tariff +32.39, preserve -50.46.
    # RETIRED 2026-06-10 (v71 integrity audit): the source CSV silently
    # vanished — the wave has been INERT in every stored row set (v60/v69/v70
    # were all recalced wave-less). An honest A/B on a rebuilt source showed
    # it removes ABOVE-baseline call winners (-28% N at 75+, removals 56.4%
    # vs shared 54.4% optWR15) — the documented breadth-crash-artifact trap.
    # False = config now tells the truth; a loud guard fires if re-enabled
    # without a fresh source (database/utils/sector_breadth_wave.py).
    SECTOR_BREADTH_WAVE_ENABLED=False,
    SECTOR_BREADTH_WAVE_CALL_MIN=70,
    SECTOR_BREADTH_WAVE_PUT_MAX=25,
    SECTOR_BREADTH_WAVE_PARAMS={
        'mode': 'direct_market_wave',
        'source': 'market_wave',
        'path': '.cache/market_wave/predictive_market_wave_v57_source.csv',
        'call_k': 0.482941,
        'call_target': 62.971200,
        'stress_start': 35.236419,
        'stress_full': 5.442232,
        'stress_power': 2.265087,
        'overlay_scale': 0.0,
        'overlay_confirm_relief': 0.32724843440236834,
        'overlay_confirm_mid': 0.4595284966001591,
        'overlay_confirm_width': 0.12,
        'put_k': 0.356669,
        'put_target': 28.643463,
        'repair_start': 67.196295,
        'repair_full': 74.911472,
        'repair_power': 1.660247,
    },
    # PCD
    PCD_ENABLED=True,
    PCD_GATE=25,
    PCD_RET10D_SIGMA=-1.0,
    PCD_TARGET=30,
    # MCD — RETIRED 2026-06-10 (v71 integrity audit, F4). The 8.2pp mcap<->TP
    # ladder was substantially a SURVIVORSHIP artifact of using today's
    # Stock.market_cap on historical dates: with point-in-time mcap the ladder
    # collapses to 2.6pp, z=+2.61 (< the W1 z>=3 bar) and goes non-monotonic.
    # MCD's removals were near-baseline quality (53.0% vs 55.0% optWR15,
    # z=-1.16) while consuming ~42% of 75+ N — retirement is the largest
    # honest N-recovery on the board (75-79 +67%, 80-84 +135%).
    MCD_ENABLED=False,
    MCD_GATE_LO=70,
    MCD_GATE_HI=84,
    MCD_LOG_LO=0.50,
    MCD_LOG_HI=1.90,
    MCD_ALPHA=0.80,
    MCD_TARGET=61,
    MCD_MCAP_POWER=0.70,
    MCD_SCORE_POWER=1.50,
    # ICH — ⚠ RETIRED 2026-06-12 in v73 (False): honest v72 ReSim A/B — the
    # call leg is INERT (>=75 ON-removes N=1 over 5y full universe; the v44
    # founding kijun<0 high-conviction cohort vanished post-honest-weekly) and
    # the put leg is WRONG-WAY (deletes 686 <=25 puts at 45.0% vs 41.7% shared,
    # z=+1.65 — removing better-than-average puts). Nothing earned either side.
    # experiments/dampener_ablation_v72/FINDINGS.md.
    ICH_ENABLED=False,
    ICH_GATE_CALL_LO=69,
    ICH_GATE_CALL_HI=90,
    ICH_K_CALL=0.359,
    ICH_K_CALL_POWER=2.68,
    ICH_KIJ_SAT_CALL=18.4,
    ICH_TARGET_CALL=63.8,
    ICH_GATE_PUT_LO=10,
    ICH_GATE_PUT_HI=27,
    ICH_K_PUT=0.278,
    ICH_KIJ_SAT_PUT=8.8,
    ICH_TARGET_PUT=33.4,
    ICH_IND_RAMP_CALL='linear',
    ICH_IND_RAMP_PUT='log',
    # WVD-Wave
    WVD_WAVE_ENABLED=True,
    WVD_WAVE_GATE_LO=70,
    WVD_WAVE_GATE_HI=85,
    WVD_WAVE_SCORE_POWER=1.0,
    WVD_WAVE_PEAK=0.0,
    WVD_WAVE_WIDTH=0.08,
    WVD_WAVE_K_LIFT=0.15,
    WVD_WAVE_TARGET_LIFT=82.0,
    WVD_WAVE_CLIMAX_THRESH=0.05,
    WVD_WAVE_CLIMAX_SAT=0.15,
    WVD_WAVE_K_DAMPEN=0.40,
    WVD_WAVE_TARGET_DAMPEN=55.0,
    # Daily Volume Authority Wave
    DAILY_VOLUME_AUTHORITY_WAVE_ENABLED=True,
    DAILY_VOLUME_AUTHORITY_WAVE_PARAMS={
        'lift_k': 0.6156692848396584,
        'lift_target': 80.10688367262878,
        'lift_peak': 0.08803824538588041,
        'lift_width': 0.36519750239702536,
        'lift_gate_lo': 71.40966934017388,
        'lift_gate_hi': 73.40966934017388,
        'lift_power': 2.1835391672074738,
        'max_lift': 4.365319543060418,
        'damp_k': 1.2315722683736037,
        'damp_target': 72.18430402574569,
        'damp_mag_mid': 0.22796926991766234,
        'damp_mag_slope': 3.077358834523319,
        'ema_mid': 26.03622155920991,
        'ema_slope': 0.12720210302809656,
        'impulse_mid': 0.51878283945562,
        'impulse_slope': 2.4988191070931958,
        'dv_mid': 0.7350114747173406,
        'dv_slope': 2.176388827573777,
        'damp_gate_lo': 85.8134945749787,
        'damp_gate_hi': 93.98705157221204,
        'damp_power': 1.3130647510499514,
        'max_dampen': 12.746774026718285,
        'score_fade_family': 1.0,
        'score_fade_lo': 84.54276129739753,
        'score_fade_hi': 90.99116665400278,
        'score_fade_k': 9.36636838849028,
        'weekly_mix': 0.5326512514671466,
        'weekly_base': 0.7125268309354742,
        'weekly_constructive_k': 0.30430179245890515,
        'weekly_climax_k': 0.40620731460639137,
        'weekly_peak': 0.004986727864798569,
        'weekly_width': 0.13223279956660822,
        'weekly_climax_thresh': 0.020054912281759624,
        'weekly_climax_sat': 0.06390647291307336,
    },
    # PESS
    PESS_GATE_LO=16,
    PESS_GATE_HI=20,
    PESS_DAYS_MIN=1,
    PESS_DAYS_MAX=5,  # v70: was 7 but the shared days_to_earnings was capped at
                      # EARN_BOOST_WINDOW=5, so PESS only ever fired at d<=5.
                      # Set explicitly to 5 to PRESERVE that behavior now that
                      # EARN_BOOST_WINDOW widened to 7 (decouples PESS from the boost).
    PESS_FADE_WIDTH=3.0,
    PESS_PROXIMITY_FULL_DAY=5,
    PESS_PROXIMITY_FADE_END=8,
    PESS_K=0.95,
    PESS_TARGET=28,
    # EARN_BOOST
    EARN_BOOST_ENABLED=True,
    EARN_BOOST_WINDOW=7,  # v70: widened 5->7 so the boost sees the pre7 run-up
                          # (4-7 cal days out), where the honest edge lives. PESS
                          # decoupled via PESS_DAYS_MAX=5 above (behavior-preserving).
    EARN_BOOST_MAX=0.55,
    EARN_BOOST_LIFT_NORM_CALL=14.0,
    EARN_BOOST_LIFT_NORM_PUT=16.3,
    EARN_BOOST_MIN_N=10,
    EARN_BOOST_PUT_ADMIT=False,
    # Weekly adjustment
    WEEKLY_BASE_BIAS_MAX=15.0,
    WEEKLY_BASE_BIAS_DEV_SCALE=1.5,
    WEEKLY_AGREEMENT_BASE=0.8,
    WEEKLY_AGREEMENT_AMP=0.6,
    WEEKLY_MOMENTUM_MAX=8.0,
    WEEKLY_MOMENTUM_DELTA_SCALE=15.0,
    WEEKLY_PUT_SCALE=1.5,
    WEEKLY_PUT_WAVE_ENABLED=True,
    WEEKLY_PUT_WAVE_FLOOR=1.0,
    WEEKLY_PUT_WAVE_PEAK=1.5,
    WEEKLY_PUT_WAVE_WIDTH=10.0,
    WEEKLY_PUT_WAVE_POWER=1.0,
    WEEKLY_AGREEMENT_AVG_THRESH=0.01,
    # Weekly composite
    WCOMP_NO_TREND_RSI=0.55,
    WCOMP_NO_TREND_MACD=0.45,
    WCOMP_TREND_BASE=0.35,
    WCOMP_RSI_BASE=0.35,
    WCOMP_MACD_BASE=0.30,
    WCOMP_DAMPEN_HALF_GAP=50.0,
    WCOMP_DAMPEN_K=0.5,
    # Misc
    MOVING_AVERAGE_PERIOD=20,
    MOMENTUM_LOOKBACK_DAYS=3,
)


# ─── Option-side rules (shared across DTE variants when aliased) ─────────────

@dataclass(frozen=True)
class OptionStrategyConfig:
    """Premium-side TP/SL/slippage rules. Identical for both 30 DTE and
    15 DTE under current ship — both alias `SHARED_OPTION` below."""

    # Call-side TP/SL on premium. Stress band activates when
    # MarketBreadth.breadth_score <= BREADTH_THRESHOLD on signal date.
    TP_BASE: float           # +0.35 → close call at +35% premium
    TP_STRESS: float         # +0.40 → wider TP under low breadth
    SL_BASE: float           # -0.30
    SL_STRESS: float         # -0.35
    BREADTH_THRESHOLD: int   # breadth_score ≤ this triggers stress band

    # Put-side TP/SL on premium. No breadth switch by default
    # (PUT_BREADTH_MODE='none').
    PUT_TP: float            # +0.35
    PUT_SL: float            # -0.20
    PUT_BREADTH_MODE: str    # 'none' | 'invert' | 'same'
    PUT_BREADTH_THRESHOLD: int
    PUT_TP_STRESS: float
    PUT_SL_STRESS: float

    # Put SL hard-hold: suppress SL check for first N trading bars after
    # entry. Hold=0 ships (Phase H1/H5 winner under bug-fixed MC).
    PUT_SL_HOLD_BARS_DEFAULT: int  # Tue–Fri entries
    PUT_SL_HOLD_BARS_MONDAY: int   # Monday entries (4 calendar-day reach)

    # Per-exit slippage. Zeroed 2026-04-30: Wealthsimple has $0 commissions
    # and we default to mid-of-bid-ask fills on liquid ATM options. Kept
    # in the dataclass as override knobs but defaulted to 0 in SHARED_OPTION.
    SLIP_ENTRY: float
    SLIP_TP: float
    SLIP_SL: float
    SLIP_HARD: float

    # ATM option delta assumption (used for σ ↔ % conversion).
    DELTA: float             # 0.50

    @property
    def NET_TP_BASE(self) -> float:
        return self.TP_BASE + self.SLIP_ENTRY + self.SLIP_TP

    @property
    def NET_TP_STRESS(self) -> float:
        return self.TP_STRESS + self.SLIP_ENTRY + self.SLIP_TP

    @property
    def NET_SL_BASE(self) -> float:
        return self.SL_BASE + self.SLIP_ENTRY + self.SLIP_SL

    @property
    def NET_SL_STRESS(self) -> float:
        return self.SL_STRESS + self.SLIP_ENTRY + self.SLIP_SL

    @property
    def PUT_NET_TP(self) -> float:
        return self.PUT_TP + self.SLIP_ENTRY + self.SLIP_TP

    @property
    def PUT_NET_SL(self) -> float:
        return self.PUT_SL + self.SLIP_ENTRY + self.SLIP_SL


# 15 DTE option params. Calls remain at the original 15 DTE C1 values.
# PUT_TP was briefly tightened to 0.06 by the 2026-05-09 Stage 2 transfer
# test, then reverted 2026-05-10 because the 6-15% premium TP band is inside
# 15 DTE execution/mark noise and is not a tradeable production target.
SHARED_OPTION = OptionStrategyConfig(
    TP_BASE=0.35,
    TP_STRESS=0.40,
    SL_BASE=-0.30,
    SL_STRESS=-0.35,
    BREADTH_THRESHOLD=50,
    # Reverted from 0.06 to 0.35 on 2026-05-10. Dynamic TP/SL follow-up showed
    # the 0.06 result is a bounded-fill micro-exit artifact for 15 DTE options,
    # not an actionable target after applying a 20% execution floor.
    PUT_TP=0.35,
    PUT_SL=-0.20,
    PUT_BREADTH_MODE='none',
    PUT_BREADTH_THRESHOLD=50,
    PUT_TP_STRESS=0.30,
    PUT_SL_STRESS=-0.20,
    PUT_SL_HOLD_BARS_DEFAULT=0,
    PUT_SL_HOLD_BARS_MONDAY=0,
    # ASYMMETRIC execution cost canon (2026-06-02) — mid-entry + limit-TP cross no
    # spread (0); forced exits (SL/hard) pay the half-spread. See trading-strategy.md.
    SLIP_ENTRY=0.0,
    SLIP_TP=0.0,
    SLIP_SL=-0.015,
    SLIP_HARD=-0.015,
    DELTA=0.50,
)

# 30 DTE option params — shipped 2026-05-04 from v32_optim Bayesian campaign.
# Phase B/C/D/E findings (vs prior SHARED_OPTION values):
#   TP_BASE   0.35 → 0.33  (narrower; v34 algo has stronger per-trade WR than 0.35 was tuned for)
#   SL_BASE   -0.30 → -0.27 (tighter; faster capital recycling on losers)
#   TP_STRESS 0.40 → 0.42  (wider stress TP)
#   SL_STRESS -0.35 → -0.40 (wider stress SL)
#   BREADTH_THRESHOLD 50 → 40 (stress band fires LESS often, only at deep stress)
# N=500 × 8-window canonical MC: 5y compound +6,707% (68× higher) vs prior;
# 22-now +186% (2.86× higher); 5y DD 72.4% → 72.9% (within ±2pp MC noise).
# Per-trade quality preserved: call TP 59.4 → 58.9, put TP 45.7 → 45.0.
# See experiments/v32_optim/phase_e_n500_ship.log for full validation.
# Identity-check breaks SHARED_OPTION alias → assess_combos() now emits two
# TP% combos (one per DTE).
OPT_30DTE = OptionStrategyConfig(
    # v70 Apex CALL config — 2026-06-02 (leading candidate; portfolio-only, NO
    # ALGORITHM_VERSION bump). HOLD strategy: wide SL (-0.70) so winners are not
    # stopped out early; still hard-sold at day 15 (HOLD_DAYS). 75+ / TP30 /
    # SL-70 / 50% exposure / MaxPos 14 / calls-only / ASYMMETRIC execution cost
    # (canon 2026-06-02 — see trading-strategy.md "Execution Cost Model"). On
    # Wealthsimple ($0 commission) we BUY at mid and SELL the TP via a resting
    # limit — both legs PROVIDE liquidity and cross no spread, so SLIP_ENTRY=0
    # and SLIP_TP=0. The spread is paid only on FORCED exits (SL stop + day-15
    # hard sell, incl. dead-hold expiry) where we TAKE liquidity into a widened
    # book: SLIP_SL=SLIP_HARD=-0.015 (~1.5% half-spread; raise toward -0.02..-0.03
    # to model adverse-move widening). The prior symmetric -1.5%/leg over-taxed
    # the limit-fill winners. v70 honest 10y (under the old symmetric cost)
    # N=300: uncapped +17,026% MedRet / 88% DD / 0% collapse, survives 2020-COVID
    # (down-years negative but never collapse). Apex runs UNCAPPED for max compounding
    # (+17,026%); Core/Sentinel profiles apply base caps. Replaces the v69-honest
    # hygiene config (85+-only / TP0.28) which did not compound.
    # experiments/v69_portfolio_retune/ (n300_confirm.py, ceiling_curve.py).
    TP_BASE=0.30,
    TP_STRESS=0.30,
    SL_BASE=-0.70,
    SL_STRESS=-0.70,
    BREADTH_THRESHOLD=40,   # inert now (base==stress for both TP and SL)
    # Reverted from 0.14 to 0.35 on 2026-05-11. The 0.14 Stage 2 result
    # improved bounded-fill MC, but the user-directed execution-realism pass
    # treats sub-20% TP candidates as too close to option mark/intraday noise.
    PUT_TP=0.35,
    PUT_SL=-0.20,
    PUT_BREADTH_MODE='none',
    PUT_BREADTH_THRESHOLD=50,
    PUT_TP_STRESS=0.30,
    PUT_SL_STRESS=-0.20,
    PUT_SL_HOLD_BARS_DEFAULT=0,
    PUT_SL_HOLD_BARS_MONDAY=0,
    SLIP_ENTRY=0.0,    # mid fill (liquidity provider) — no spread crossed
    SLIP_TP=0.0,       # TP via resting limit (liquidity provider) — no spread crossed
    SLIP_SL=-0.015,    # forced stop exit (liquidity taker) — half-spread + widening
    SLIP_HARD=-0.015,  # forced day-15 hard sell (liquidity taker)
    DELTA=0.50,
)


# ─── DTE-specific overrides + composition with option config ─────────────────

@dataclass(frozen=True)
class DteStrategyConfig:
    """Full per-DTE strategy parameters. Embeds an `OptionStrategyConfig`
    via composition; aliasing `SHARED_OPTION` between two DTE configs
    signals 'these strategies share option mechanics' to assess_combos()."""

    name: str

    # DTE-specific
    HOLD_DAYS: int                    # trading bars to hard sell (legacy bar-hold path)
    # Calendar-day hold + honest-theta standard (shipped 2026-06-09). When
    # CALENDAR_HOLD=True the hold window AND option theta are CALENDAR-based: hard-sell
    # at signal + HOLD_CAL_DAYS calendar days, theta decaying over NOMINAL_CAL_DTE
    # calendar days (honest — real options expire on calendar dates). HOLD_DAYS is
    # retained for the legacy trading-bar path / back-compat.
    CALENDAR_HOLD: bool               # True = calendar hold + honest theta (the standard)
    HOLD_CAL_DAYS: int                # calendar-day hard-sell deadline (CALENDAR_HOLD path)
    NOMINAL_CAL_DTE: int              # option calendar life used for honest theta
    PREMIUM_MULT: float               # ATM premium ≈ this × σ_daily
    HARD_SELL_LOSS: float             # gross premium loss at hard sell

    # Position sizing / pool
    MAX_POSITIONS: int
    MAX_POSITIONS_CALL: Optional[int]  # None = share global pool
    MAX_POSITIONS_PUT: Optional[int]
    # Practical exposure saturation (Stage 3 portfolio controller). Caps
    # deployable premium against a practical base and smoothly scales fills
    # when same-day opportunity supply is dense. All zero/False = disabled.
    PRACTICAL_EXPOSURE_ENABLED: bool
    PRACTICAL_CAPITAL_CEILING: float
    GROSS_PREMIUM_CAP: float
    CALL_PREMIUM_CAP: float
    PUT_PREMIUM_CAP: float
    OPP_SAT_CALL_REF: float
    OPP_SAT_PUT_REF: float
    OPP_SAT_POWER: float
    OPP_SAT_FLOOR: float
    # DTE router (Stage 3 portfolio overlay). Applies only on the
    # 30 DTE strategy today: selected call entries keep the 30 DTE portfolio
    # sizing stack but use 15 DTE option outcomes/exits.
    DTE_ROUTER_ENABLED: bool
    DTE_ROUTER_TARGET_DTE: int
    DTE_ROUTER_SCORE_MIN: int
    DTE_ROUTER_TREND_LT: float
    DTE_ROUTER_VIX_MIN: float
    DTE_ROUTER_VIX_MAX: float           # crash-gate: route to 15DTE only when VIX <= this (0=off)
    DTE_ROUTER_REGIME_MIN: float
    DTE_ROUTER_REGIME_MAX: float
    DTE_ROUTER_DAY_CAP: int
    DTE_ROUTER_ALLOC_SCORE_CAP: int
    DTE_ROUTER_EXCLUDED_SYMBOLS: Tuple[str, ...]
    PRIMARY_THRESHOLD: int             # call entry threshold (75)
    OVERFLOW_THRESHOLD: int            # call overflow tier (70)
    PUT_THRESHOLD: int                 # put entry threshold (25)

    # Cascade allocation (calls). Keys: 'ultra' (95+), 'top' (85-94),
    # 'mid' (80-84), 'low' (75-79), 'overflow' (70-74).
    TIER_ALLOC: Dict[str, float]
    # Cascade allocation (puts). Keys: 'put_top' (≤15), 'put_mid' (16-20),
    # 'put_low' (21-25).
    PUT_TIER_ALLOC: Dict[str, float]

    # F3f breadth-driven allocation (shipped 2026-04-24, replaces
    # composite-driven scaling). Asymmetric: cut calls when breadth low,
    # cut puts when breadth high.
    F3F_CALL_THRESH: float            # breadth ≥ this → no call cut
    F3F_CALL_FLOOR: float             # min scale at deepest weak breadth
    F3F_CALL_LOW: float               # breadth at which floor reached
    F3F_PUT_THRESH: float             # breadth ≤ this → no put cut
    F3F_PUT_FLOOR: float
    F3F_PUT_HIGH: float
    ALLOC_SCALE_FLOOR: float
    ALLOC_SCALE_CEIL: float

    # Regime-aware allocation (asymmetric CUT_ONLY shipped 2026-04-17).
    # alloc_scale = 1.0 + slope × (regime_mult - 1.0), clamped.
    # slope_up=0 → no bull boost; slope_down=1 → full stress cut.
    REGIME_SLOPE: float
    REGIME_SLOPE_PUT: float
    REGIME_SLOPE_UP: float
    REGIME_SLOPE_DOWN: float
    REGIME_SLOPE_PUT_UP: float
    REGIME_SLOPE_PUT_DOWN: Optional[float]
    BREADTH_ALLOC_ENABLED: bool       # False = legacy regime path

    # H3 — DD-soft-band call alloc contraction (shipped 2026-05-04).
    # When running portfolio DD ∈ [DD_SOFT_BAND_LO, DD_SOFT_BAND_HI], scale
    # call alloc linearly from 1.0 down to DD_SOFT_CALL_FLOOR. Above HI =
    # full floor. Below LO = no effect (1.0×).
    # Distinct from F3F (breadth-only). Operates on the call side only.
    # All zero defaults / floor=1.0 = mechanism disabled.
    DD_SOFT_BAND_LO: float
    DD_SOFT_BAND_HI: float
    DD_SOFT_CALL_FLOOR: float

    # RXDD — VIX-regime call-alloc dampener (shipped 2026-06-04, 30 DTE).
    # Smooth Gaussian contraction of CALL alloc in the low-EV VIX "slow-bleed"
    # band (~20-26): alloc *= 1 - DEPTH*exp(-0.5*((vix-VIX_C)/VIX_W)^2), gated to
    # fire only when running DD >= DD_MIN. Panic (VIX>=28) and calm (<20) tape are
    # untouched (collapse-safe). RXDD_ENABLED=False → no-op. Validated N=500x8:
    # 5y WorstDD -5.6pp AND compound +9.4%, collapse=0 incl 2020-COVID.
    RXDD_ENABLED: bool
    RXDD_VIX_C: float
    RXDD_VIX_W: float
    RXDD_DEPTH: float
    RXDD_DD_MIN: float

    # MWDD — McClellan (breadth-momentum / "market wave") flat-band CALL alloc
    # dampener (shipped 2026-06-05, 30 DTE). Smooth Gaussian contraction of CALL
    # alloc in the low-EV flat/topping McClellan band (~0): alloc *= 1 -
    # DEPTH*exp(-0.5*((mcc-MCC_C)/MCC_W)^2), gated to fire only when running DD >=
    # DD_MIN, and VIX-panic-excluded (>=VIX_PANIC: capitulation = mean-reversion
    # winners, left alone -> COVID untouched). Orthogonal to RXDD(VIX) + F3F(breadth
    # level). MWDD_ENABLED=False → no-op. Validated N=500x10: 5y WorstDD -2.6pp /
    # 22-now -5.5pp, every window DD down, collapse=0 incl 2020-COVID.
    MWDD_ENABLED: bool
    MWDD_MCC_C: float
    MWDD_MCC_W: float
    MWDD_DEPTH: float
    MWDD_DD_MIN: float
    MWDD_VIX_PANIC: float

    # TVDD — TRIN (Arms-index, volume-FLOW) neutral-band CALL alloc dampener
    # (shipped 2026-06-07, 30 DTE). Smooth Gaussian contraction of CALL alloc in the
    # low-EV neutral volume-flow band (TRIN ~1.0-1.3 = balanced/mild-distribution):
    # alloc *= 1 - DEPTH*exp(-0.5*((trin-TRIN_C)/TRIN_W)^2), gated to fire only when
    # running DD >= DD_MIN, and VIX-panic-excluded (>=VIX_PANIC). TRIN extremes (froth
    # <0.7, panic >1.8) are mean-reversion/momentum winners left alone by the bump.
    # 4th orthogonal DD lever: distinct from RXDD(VIX) + MWDD(McClellan count-momentum)
    # + F3F(breadth level) — it's a volume-flow-vs-breadth-momentum divergence.
    # TVDD_ENABLED=False → no-op. Validated N=500x10: 5y WorstDD -3.1pp AND compound
    # +17% (22-now +28%; 2020_crash DD -8.3pp), collapse=0 incl 2020-COVID.
    TVDD_ENABLED: bool
    TVDD_TRIN_C: float
    TVDD_TRIN_W: float
    TVDD_DEPTH: float
    TVDD_DD_MIN: float
    TVDD_VIX_PANIC: float

    # BDIV — pre-top Breadth-DIVergence-at-highs CALL alloc dampener (shipped
    # 2026-06-10, 30 DTE). The DD-episode-onset mine (experiments/dd_onset_omens/,
    # fresh v71 tape) killed the literal "Hindenburg omen precedes drawdowns"
    # hypothesis (0/24 omen days preceded a major onset; omen-day entries are
    # mean-reversion WINNERS) but surfaced the classic PRE-TOP BREADTH DIVERGENCE:
    # SPY within ~1-2% of its 60d high WHILE internal breadth_score has dropped
    # ~5-10pts in 10d is low-EV (mpnl -0.018 vs +0.029, z+25, dd_conc 1.82),
    # sign-stable across years incl 2024/2025, and orthogonal to all shipped levers.
    # alloc *= 1 - DEPTH * prox_ramp(spy_from60h; PROX_CUT->PROX_FULL)
    #              * exp(-0.5*((brd_det10 - GAP_C)/GAP_W)^2)
    # 5th orthogonal DD lever, the first LEADING one (fires PRE-onset at the top,
    # where running dd ~ 0): hence NO DD-gate and NO VIX-panic knob — the
    # SPY-near-highs requirement is the structural crash guard (cannot fire
    # mid-crash; 2022 delta is exactly 0.0 by construction). Gap extremes (>12 =
    # sharp shakeout) are mean-reversion winners left alone by the Gaussian.
    # BDIV_ENABLED=False → no-op.
    BDIV_ENABLED: bool
    BDIV_PROX_CUT: float
    BDIV_PROX_FULL: float
    BDIV_GAP_C: float
    BDIV_GAP_W: float
    BDIV_DEPTH: float

    # SVR — semivol_r (skew-bridge) entry filter (shipped 2026-06-05, 30 DTE).
    # semivol_r = std(downside 60d returns)/std(upside 60d returns): the live,
    # 10y-MC-computable cousin of option put-skew. Low (~0.5, euphoric/EXPENSIVE
    # call) is the WORST per-trade call cohort; very-high (~1.4, crash-mode) weak;
    # ~0.9-1.1 is the sweet spot. Smooth band-pass: CALL alloc *= scale, contracting
    # toward SVR_FLOOR below SVR_LO_FULL and above SVR_HI_FULL, full in the sweet
    # spot. SVR_ENABLED=False → no-op. Validated N=500x8: 5y WorstDD -5.8pp AND
    # compound +28.6% (22-now -5.6pp/+40%), collapse=0 incl 2020-COVID.
    SVR_ENABLED: bool
    SVR_LO_CUT: float
    SVR_LO_FULL: float
    SVR_HI_FULL: float
    SVR_HI_CUT: float
    SVR_FLOOR: float

    # Dead-hold post-SL mechanism (Spec C, in flight 2026-04-30).
    # Per-DTE because optimal trigger/popout values differ — 15 DTE
    # has tighter theta windows so popout dynamics differ from 30 DTE.
    # When SL fires AND realized option pnl ≤ DEAD_HOLD_TRIGGER_PNL, do
    # NOT sell. Hold forward bar-by-bar; on any subsequent bar, if option
    # value at intraday extreme reaches DEAD_HOLD_POPOUT_PNL, exit there.
    # At hard-sell day, exit at close-bar option value.
    DEAD_HOLD_ENABLED: bool
    DEAD_HOLD_TRIGGER_PNL: float
    DEAD_HOLD_POPOUT_PNL: float

    # Earnings-window put suppression (shipped 2026-04-26). Drops puts in
    # [MIN_OV, MAX_OV] when an EarningsDate falls in
    # (signal_date, signal_date + DAYS trading days].
    EARN_SUPP_PUT: bool
    EARN_SUPP_PUT_DAYS: int
    EARN_SUPP_PUT_MIN_OV: int
    EARN_SUPP_PUT_MAX_OV: int

    # Weak-weekly call filter (shipped 2026-05-05). Drops calls in
    # [MIN_OV, MAX_OV] when w_adj < WADJ_LT AND stoch >= STOCH_GE.
    # Targets the v36/v37 wadj-neg residue at 70-74 (CWCF gates on >=75
    # so 70-74 is untreated). Per-trade z=+9.2 (miss 52.7%, N=1537/5y).
    # Mirrors WEAK_WEEKLY_PUT_DROP shape; v37-validated portfolio gate.
    WEAK_WEEKLY_CALL_DROP: bool
    WEAK_WEEKLY_CALL_MIN_OV: int
    WEAK_WEEKLY_CALL_MAX_OV: int
    WEAK_WEEKLY_CALL_WADJ_LT: float
    WEAK_WEEKLY_CALL_STOCH_GE: int

    # Counter-trend cascade promotion (Path B/V2 shipped 2026-04-21).
    # ct_call (overall≥70 AND TREND≤MAX) → ultra tier.
    # ct_put (overall≤25 AND TREND≥MIN) → put_top tier.
    CT_PROMOTE: bool
    CT_PUT_TREND_MIN: int
    CT_CALL_TREND_MAX: int

    # CTSL — Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08).
    # Score-stage continuous lift mechanism that ADDITIVELY stacks on top of
    # CT_PROMOTE. Tighter call gate (tm=15) lifts only deepest CT-call subset
    # (~5% of cohort) toward ULTRA-tier-quality scores; wider put gate (tm=76)
    # dampens deep CT-puts toward target=0 (put_top tier). 15 DTE disabled —
    # half-DTE strategy not validated under bounded-fill MC for this calibration.
    # Stage 3 evidence (B config: CTSL stacked on CT_PROMOTE): 5y DD -0.40pp,
    # 22-now DD -3.40pp, 2023 DD -3.20pp, 2025 DD -5.00pp; T1-T7 hard gates
    # all PASS. The C-substitute path (CTSL replacing CT_PROMOTE) was tested
    # and rejected (FAIL T4 with +2.20pp 5y DD). See experiments/ctsl/FINDINGS.md.
    CTSL_ENABLED: bool
    CTSL_CALL_TREND_MAX: int
    CTSL_CALL_TARGET: float
    CTSL_CALL_ALPHA: float
    CTSL_CALL_TREND_POWER: float
    CTSL_CALL_TIER_FLOOR: float
    CTSL_CALL_SCORE_NORM_WEIGHT: float
    CTSL_CALL_SCORE_NORM_POWER: float
    CTSL_PUT_TREND_MIN: int
    CTSL_PUT_TARGET: float
    CTSL_PUT_ALPHA: float
    CTSL_PUT_TREND_POWER: float
    CTSL_PUT_TIER_CEILING: float
    CTSL_PUT_SCORE_NORM_WEIGHT: float
    CTSL_PUT_SCORE_NORM_POWER: float

    # SAW Put U-curve — sector-breadth-driven put alloc gradient (shipped
    # 2026-05-08). At each put signal date, lookup cross-sector ETF breadth
    # (% of 11 SPDRs above EMA50) and apply a U-curve scale to put alloc:
    # contracts in the "bad zone" (mid ± hw) where puts have lowest pnl%,
    # amplifies at extremes (deep oversold AND extreme overheat) where puts
    # have highest pnl%. Quadratic shape with d_norm^POWER curvature.
    # 30 DTE: shipped (Region B winner: mid=72/hw=18/floor=0.55/ceil=1.35/pk=3.0).
    # 15 DTE: disabled (not validated under bounded-fill MC).
    # Stage C N=300×8 evidence: 5y 22-now compound +182.6%, 5y DD −1.1pp,
    # 22-now DD −2.4pp, 7-of-8 annual windows IMPROVE compound vs baseline.
    # See `experiments/saw_put_ucurve/OVERNIGHT_SHIP_REPORT.md`.
    SAW_PUT_UCURVE_ENABLED: bool
    SAW_PUT_UCURVE_SHAPE: str            # 'quadratic' | 'sigmoid'
    SAW_PUT_UCURVE_MIDPOINT: float       # center of "bad zone" trough
    SAW_PUT_UCURVE_HALFWIDTH: float      # half-width of dampener zone
    SAW_PUT_UCURVE_FLOOR: float          # min scale at trough
    SAW_PUT_UCURVE_CEIL: float           # max scale at extremes
    SAW_PUT_UCURVE_POWER: float          # quadratic curvature
    SAW_PUT_UCURVE_K: float              # sigmoid sharpness (unused if shape=quadratic)

    # Volatility lookback (60 trading bars, consistent with assess σ).
    VOL_LOOKBACK: int

    # Portfolio-collapse threshold for MC reporting (drops below this
    # fraction of starting capital → counted as "collapsed iteration").
    COLLAPSE_THRESHOLD: float

    # Composed option-side rules (aliased SHARED_OPTION when DTEs match).
    option: OptionStrategyConfig

    # ─── Derived (computed; never duplicated) ────────────────────────────

    @property
    def TP_SIGMA_BASE(self) -> float:
        """Underlying σ move required for TP_BASE on premium."""
        return self.option.TP_BASE * self.PREMIUM_MULT / self.option.DELTA

    @property
    def TP_SIGMA_STRESS(self) -> float:
        return self.option.TP_STRESS * self.PREMIUM_MULT / self.option.DELTA

    @property
    def SL_SIGMA_BASE(self) -> float:
        return abs(self.option.SL_BASE) * self.PREMIUM_MULT / self.option.DELTA

    @property
    def SL_SIGMA_STRESS(self) -> float:
        return abs(self.option.SL_STRESS) * self.PREMIUM_MULT / self.option.DELTA

    @property
    def PUT_TP_SIGMA(self) -> float:
        return self.option.PUT_TP * self.PREMIUM_MULT / self.option.DELTA

    @property
    def PUT_SL_SIGMA(self) -> float:
        return abs(self.option.PUT_SL) * self.PREMIUM_MULT / self.option.DELTA

    @property
    def NET_HARD_SELL(self) -> float:
        return self.HARD_SELL_LOSS + self.option.SLIP_ENTRY + self.option.SLIP_HARD


# ─── Shipped configurations ──────────────────────────────────────────────────

# 30 DTE — Phase H5_HOLD15_H40 winner shipped 2026-04-28.
STRATEGY_30DTE = DteStrategyConfig(
    name='30dte_H5_HOLD15_H40',
    HOLD_DAYS=15,
    CALENDAR_HOLD=True,    # calendar hold + honest theta — standardized 2026-06-09
    HOLD_CAL_DAYS=27,      # optimal: #93 per-trade plateau + #89/#92 portfolio (robust to realistic popout cost)
    NOMINAL_CAL_DTE=30,    # 30 calendar-day option
    PREMIUM_MULT=1.82,
    HARD_SELL_LOSS=-0.40,

    # v70 Apex CALL config — 2026-06-02 (leading candidate). Full 14-slot pool,
    # all calls (puts off — see TIER_ALLOC / PUT_TIER_ALLOC). On v70 honest 10y the
    # HOLD/75+ call engine returns +15,323% MedRet / 83% DD / 0% collapse. The
    # prior v69-honest 85+-only hygiene config (8/7/2, TP0.28) did not compound;
    # this is the validated leading replacement. experiments/v69_portfolio_retune/.
    MAX_POSITIONS=14,
    MAX_POSITIONS_CALL=14,
    MAX_POSITIONS_PUT=0,
    # v70 Apex practical exposure — 2026-06-02. 50% gross/call premium cap is the
    # capital-velocity exposure peak (down-check: 50% > 65% > 100%/off on 10y MedRet
    # — over-deployment deepens DD so less capital survives to compound). Puts off
    # (cap 0). PRACTICAL_CAPITAL_CEILING=0 => UNCAPPED: Apex maximizes compounding off
    # the full portfolio value (it's the explosive early-stage profile — the liquidity
    # cap never binds while the book is small, and you migrate to Core/Sentinel, which
    # DO cap the base, before the book is large enough for the ~3% spread to break).
    # Engine _allocation_base() returns full value when ceiling<=0. Per-profile ceilings
    # live in algorithm_versions/portfolio_profiles.json (Apex 0 / Core+Sentinel capped).
    PRACTICAL_EXPOSURE_ENABLED=True,
    PRACTICAL_CAPITAL_CEILING=0.0,
    GROSS_PREMIUM_CAP=0.50,
    CALL_PREMIUM_CAP=0.50,
    PUT_PREMIUM_CAP=0.0,
    OPP_SAT_CALL_REF=16.0,
    OPP_SAT_PUT_REF=4.0,
    OPP_SAT_POWER=0.50,
    OPP_SAT_FLOOR=0.55,
    # Broad DTE router — portfolio-only ship, no ALGORITHM_VERSION bump.
    # N=500 confirmation against v60 fixed score rows:
    #   2022 mean +0.388 / median +0.437 / DD -4.34pp
    #   2023 mean +0.033 / median +0.051 / DD -2.32pp
    #   2025 mean +0.042 / median +0.029 / DD -0.31pp
    #   22-now mean +0.017 / median +0.018 / worst-DD +1.54pp,
    #          mean-DD -2.37pp
    #   5y mean +0.013 / median +0.018 / DD +0.00pp
    # The strict 22-now worst-DD gate misses by 0.54pp, but the broader router
    # gives 117 routed signals over 5y vs 41 for the prior stress sleeve.
    DTE_ROUTER_ENABLED=True,
    DTE_ROUTER_TARGET_DTE=15,
    DTE_ROUTER_SCORE_MIN=80,
    DTE_ROUTER_TREND_LT=50.0,
    DTE_ROUTER_VIX_MIN=0.0,
    DTE_ROUTER_VIX_MAX=0.0,   # crash-gate off by default; the router sweep tunes this for broad 15DTE
    DTE_ROUTER_REGIME_MIN=0.0,
    DTE_ROUTER_REGIME_MAX=100.0,
    DTE_ROUTER_DAY_CAP=1,
    DTE_ROUTER_ALLOC_SCORE_CAP=0,
    DTE_ROUTER_EXCLUDED_SYMBOLS=(),
    PRIMARY_THRESHOLD=75,
    OVERFLOW_THRESHOLD=70,
    PUT_THRESHOLD=25,

    # Monotonic cascade gradient — ships 2026-05-01 as Phase 7 winner (mono_B_steep)
    # superseding the H4 "asymmetric" pattern (top=0.12 < mid=0.15 was a noise
    # artifact at H4's N=100 single-window screen, per v27-optimization-log.md).
    #
    # Phase 7 N=500 × 8-window canonical sweep tested 5 cascade variants under
    # the DD<80% gate. ALL passed gate; ranked by composite (avg log 5y/22-now −
    # DD penalty):
    #   1. mono_B_steep  (0.20/0.15/0.12/0.10) — composite 54.07, max DD 74.0% ← winner
    #   2. mono_A_swap   (0.18/0.15/0.12/0.12) — composite 53.78, max DD 78.8%
    #   3. mono_C_smooth (0.18/0.14/0.13/0.12) — composite 53.34, max DD 76.4%
    #   4. current_asym  (0.18/0.12/0.15/0.12) — composite 53.17, max DD 78.8% (4th of 5)
    #   5. concentrated  (0.22/0.13/0.10/0.10) — composite 52.89, max DD 73.8%
    #
    # vs prior current_asym: 5y compound 4.5×, max DD -4.8pp, monotonic by score
    # (matches per-trade WR gradient: 95+=69.2%, 85-89=67.2%, 80-84=60.9%,
    # 75-79=58.3%). 22-now compound -55% but within 1.81× noise floor (per
    # Phase 4-5 noise audit); per Phase OP1 lesson, 5y is the locking metric.
    #
    # Portfolio-stage only — no ALGORITHM_VERSION bump.
    # See known-issues.md "Closed — Portfolio v32: monotonic cascade ship".
    # Cascade refined 2026-05-04 by v32_optim Bayesian campaign on v34 algorithm.
    # Phase B Bayesian (16 evals × N=100 × 8 windows) found mid=0.10 + monotonic
    # puts wins on combined utility. Phase D N=300 + Phase E N=500 confirmed.
    # Joint with OPT_30DTE TP/SL: 5y compound 68× higher at neutral DD.
    # v69-honest retune 2026-05-31: 85+-ONLY (mid/low=0 -> 80-84 & 75-79 dropped).
    # On honest scores the per-trade edge lives ENTIRELY in 85+ (TP 52% > 49% BE);
    # 80-84 (48%) and 75-79 drag below BE (selectivity probe: sel80 5y -52% vs
    # sel85 +86%). Downsized to cs0.65 (ultra 0.13/top 0.0975) for ~35% DD.
    # mid=low=0 -> premium<=0 -> signal skipped (no wasted slot).
    # v70 Apex 75+ full monotonic cascade — 2026-06-02. The HOLD barrier (wide
    # SL-70, sell day 15) makes the full 75+ ladder tradeable where the prior
    # CUT/tight-SL barrier did not: instead of cutting the thin honest per-trade
    # edge, the wide SL holds winners to the day-15 window, and the abundant 75+
    # signal flow is the compounding engine (capital velocity). Monotonic by score
    # 20/15/10/10 (matches the per-trade WR gradient). Validated +15,323% 10y.
    TIER_ALLOC={
        'ultra':    0.20,   # 95+
        'top':      0.15,   # 85-94
        'mid':      0.10,   # 80-84
        # v71 retune 2026-06-10: low 0.10 -> 0.05. v71 DOUBLED 75-79 supply
        # (+83% 75+ at flat honest WR); at 0.10 the tier dominated correlated
        # book exposure. Halving the slug = same tier participation, better
        # diversification, faster recycling. Phase D N=500x10 incl COVID
        # (c14_low05_ovf0): EVERY window's DD improves (5y 74.3->67.6, 22-now
        # 72.9->65.0, 2024 -18.1pp), 5y compound +35% (+1,230->+1,660%),
        # collapse=0 everywhere. experiments/v71_portfolio_retune/FINDINGS.md.
        'low':      0.05,   # 75-79
        # 70-74 OVERFLOW retired 2026-06-10 (v71 retune): the 2026-06-03 edge
        # was supply-density-conditional — it filled the IDLE v70 book
        # (hydration 22%->89%). On v71's doubled 75+ supply the overflow ran
        # 45.6% of ALL fills and became strictly compound-dilutive (paired-seed
        # ladder: ovf 0 -> x1.57 vs 0.035 -> x1.00; 0.05 -> x0.81) — each slug
        # intertemporally displaces tomorrow's 75+ signal from the shared 50%
        # cap. Re-evaluate ONLY if a future version deflates 75+ supply again.
        'overflow': 0.0,    # 70-74
    },
    # Puts OFF: on honest v69 scores put TP at 5y is 25-36% (below 36.4% BE);
    # net-negative at any real size, unrescuable by barrier tuning (Stage B put
    # arm: all configs collapsed). The bear-hedge value was itself look-ahead.
    PUT_TIER_ALLOC={
        'put_top':  0.00,   # OFF
        'put_mid':  0.00,   # OFF
        'put_low':  0.00,   # OFF
    },

    F3F_CALL_THRESH=50.0,
    F3F_CALL_FLOOR=0.50,
    F3F_CALL_LOW=30.0,   # raised from 20.0 — extends floor to breadth<=30 to catch Sep 2022 cluster (Phase 8/9)
    F3F_PUT_THRESH=75.0,
    F3F_PUT_FLOOR=0.50,
    F3F_PUT_HIGH=95.0,
    ALLOC_SCALE_FLOOR=0.25,
    ALLOC_SCALE_CEIL=1.75,

    REGIME_SLOPE=1.0,
    REGIME_SLOPE_PUT=0.0,
    REGIME_SLOPE_UP=0.0,
    REGIME_SLOPE_DOWN=1.0,
    REGIME_SLOPE_PUT_UP=-0.5,
    REGIME_SLOPE_PUT_DOWN=None,
    BREADTH_ALLOC_ENABLED=True,

    # H3 / v60 DD-soft-band — shipped 2026-05-04, recalibrated 2026-05-19 with
    # the r054 SCW score stack.
    # Surfaced via experiments/dd_ledger/FINDINGS.md "H3" hypothesis from a
    # cohort lift/z mining run (CALL 75-79 × entry_dd=mid × regime=HEALTHY
    # carried DD-conc 19.7×). Mild calibration (LO=0.40, HI=0.60) only fires
    # in deep-DD episodes — preserves compounding in realistic-capital windows
    # (2022/dip) while cutting allocation on the worst tail paths.
    #   N=500 vs baseline (PYTHONHASHSEED=0):
    #     5y DD-C: 75.8% → 71.4% (-4.4pp)
    #     22-now DD-C: 75.5% → 72.4% (-3.1pp)
    #     2025 DD-C: 70.9% → 68.1% (-2.8pp)
    #     2023 DD-C: 62.0% → 58.5% (-3.5pp)
    #     5y MedRet: -8.9% (within ±10-25% N=500 5y noise floor)
    #     Per-trade quality unchanged (Call TP 58.3 → 58.4)
    # H3 v1 (LO=0.20 HI=0.40 FLOOR=0.50) was tested first — failed P4 with
    # widespread compound regression. v2 milder calibration is the ship.
    # Earlier standalone call caps failed against older scoring, but the r054
    # stack plus a milder earlier DD response selected callcap12_dd035055f040:
    # avg focus DD improvement +0.90pp, no material max-DD worsening, log-return
    # guardrail +0.054, call TP drift -0.02pp.
    # See .codex/runs/v60_r054_portfolio_dd_short_retry_20260518_055226.
    DD_SOFT_BAND_LO=0.35,
    DD_SOFT_BAND_HI=0.55,
    DD_SOFT_CALL_FLOOR=0.40,

    # RXDD VIX-band call dampener — c00 winner (2026-06-04 overnight regime/DD run).
    # Mining: VIX 20-28 = worst call cohort (break-even EV); VIX>=28 = best. Phase
    # B(N=100)/C(N=300)/D(N=500x8 ship-gate) all PASS; see experiments/regime_dd_v70/.
    RXDD_ENABLED=True,
    RXDD_VIX_C=22.701,
    RXDD_VIX_W=3.14,
    RXDD_DEPTH=0.447,
    RXDD_DD_MIN=0.077,

    # MWDD McClellan flat-band call dampener — c00 winner (2026-06-05 overnight
    # market-wave/DD run). Mining: flat McClellan (~0) = low-EV + DD-concentrated
    # (orthogonal to RXDD/F3F); crash McClellan = mean-reversion winner (left alone).
    # Phase B(N=100)/C(N=300)/D(N=500x10 ship-gate) all PASS; see experiments/market_wave_dd_v70/.
    MWDD_ENABLED=True,
    MWDD_MCC_C=-0.336,
    MWDD_MCC_W=22.185,
    MWDD_DEPTH=0.337,
    MWDD_DD_MIN=0.128,
    MWDD_VIX_PANIC=28.0,

    # TVDD TRIN neutral volume-flow-band call dampener — Phase-D winner (2026-06-07
    # /research residual-DD run). Mining the full-lever (RXDD+SVR+MWDD) tape's DD-active
    # subset: neutral TRIN (~1.0-1.3) = low-EV + DD-concentrated AND orthogonal (survives
    # the all-levers-off slice at mpnl -0.060, z+57 — a volume-flow-vs-breadth-momentum
    # divergence). TRIN extremes (froth/panic) = mean-reversion winners, left alone.
    # Phase B(N=100x6)/C(N=300x10)/D(N=500x10 ship-gate) all PASS T1-T7: 5y WorstDD -3.1pp
    # AND compound +17%, 2020_crash DD -8.3pp, collapse=0 every window incl 2020-COVID.
    # See experiments/dd_residual_v70/.
    TVDD_ENABLED=True,
    TVDD_TRIN_C=1.042,
    TVDD_TRIN_W=0.268,
    TVDD_DEPTH=0.426,
    TVDD_DD_MIN=0.291,
    TVDD_VIX_PANIC=28.0,

    # BDIV pre-top breadth-divergence-at-highs call dampener — Phase-D winner
    # (2026-06-10 /research DD-episode-onset run; user's "Hindenburg omen" ask —
    # the omen itself is NULL/inverted, the pre-top divergence is the survivor).
    # SPY near 60d highs + breadth rolling over = the slow-bleed top zone; the
    # FIRST leading DD lever (no DD-gate: fires pre-onset; SPY-near-highs is the
    # structural crash guard — 2022/COVID untouched by construction).
    # Phase B(N=100x6)/C(N=300x10)/D(N=500x10 ship-gate) all PASS T1-T7:
    # 5y WorstDD 67.6->64.6 (-3.0pp) AND compound +21% (1,660->2,010%); dip DD
    # 40.3->26.3 (-14.0pp) at compound +49%; 2021 DD -2.5pp (med -36.5->-28.6);
    # 2022 delta exactly 0.0 and COVID flat (the structural crash guard);
    # worst annual DD regression 0.1pp; collapse=0 every window incl 2020-COVID.
    # See experiments/dd_onset_omens/FINDINGS.md.
    BDIV_ENABLED=True,
    BDIV_PROX_CUT=0.0198,
    BDIV_PROX_FULL=0.0075,
    BDIV_GAP_C=7.716,
    BDIV_GAP_W=3.4571,
    BDIV_DEPTH=0.53,

    # SVR semivol_r skew-bridge entry filter — gentleband c00 winner (2026-06-05
    # apex-speed overnight). Band-pass: full call alloc in the ~0.7-1.25 semivol_r
    # sweet spot, contract toward 0.5x below 0.7 (euphoric/expensive call cohort)
    # and above 1.25 (crash-mode), zero-out the contraction ramps at LO_CUT/HI_CUT.
    # Phase B(N=100)/C(N=300)/D(N=500x8 ship-gate) all PASS; experiments/apex_speed_v70/.
    SVR_ENABLED=True,
    SVR_LO_CUT=0.50,
    SVR_LO_FULL=0.70,
    SVR_HI_FULL=1.25,
    SVR_HI_CUT=1.65,
    SVR_FLOOR=0.50,

    # Dead-hold post-SL mechanism — shipped 2026-05-01 after N=300 hardening
    # sweep. T-0.50_P-0.25 is the only variant clearing the 80% Conservative
    # DD ceiling on every window for BOTH DTEs:
    #   30 DTE: avg DD 75.4% / max 79.3%, 5y compound +1.00e+29% (vs OFF +8.5e+25%)
    #   15 DTE: avg DD 78.0% / max 79.7%, 5y compound +5.38e+09% (vs OFF +1.1e+03%)
    # Initial N=150 ship picked POPOUT=-0.30; N=300 hardening (the documented
    # P1 gate) showed -0.30 breaches 80% on 5y (30 DTE) and on 5y+2022 (15 DTE).
    # Tighter popout (-0.25) exits earlier on recoveries and wins both DTEs.
    # See known-issues.md "Dead-hold post-SL mechanism (#20)".
    DEAD_HOLD_ENABLED=True,
    # 2026-06-03 (30 DTE): POPOUT -0.25->-0.15 + TRIGGER -0.50->-0.40. N=500 apex+overflow:
    # 10y ret x2.07 AND -2.7pp DD, collapse=0 — letting dead-hold recoverers run to a -15%
    # exit captures the strong rebounds (dh_pop reaches +357%) => +return AND -DD. NOTE: the
    # dead-hold is collapse-PREVENTING: dh_off (clean -70% SL) = 100% collapse (deferral
    # avoids simultaneous crash realization). 15 DTE left at -0.50/-0.25 (not re-validated).
    DEAD_HOLD_TRIGGER_PNL=-0.40,
    DEAD_HOLD_POPOUT_PNL=-0.15,

    # EARN_SUPP_PUT — RETIRED 2026-05-06.
    # Replaced by score-stage PESS dampener in scoring.py (v39). The score
    # itself now lifts puts in [16,20] with earnings within 5 trd days
    # OUT of put-qualifying range (target=28), so the cascade-stage filter
    # is redundant.
    # Original ship validated 2026-04-26 via canonical N=1000 MC (5y compound
    # +44.7% Realistic).  Schema parity preserved.
    EARN_SUPP_PUT=False,
    EARN_SUPP_PUT_DAYS=5,
    EARN_SUPP_PUT_MIN_OV=16,
    EARN_SUPP_PUT_MAX_OV=20,

    # Weak-weekly call filter — RETIRED 2026-05-06.
    # Replaced by score-stage CWWD dampener in scoring.py (v38). The score
    # itself now drifts wadj-neg 70-74 cohorts below 70 (out of qualifying
    # universe), so the cascade-stage filter is redundant.
    # Original D variant validated on v36/v37 before retirement (see
    # experiments/call_wadj_70_filter/FINDINGS.md). Schema parity preserved.
    WEAK_WEEKLY_CALL_DROP=False,
    WEAK_WEEKLY_CALL_MIN_OV=70,
    WEAK_WEEKLY_CALL_MAX_OV=84,
    WEAK_WEEKLY_CALL_WADJ_LT=0.0,
    WEAK_WEEKLY_CALL_STOCH_GE=35,

    CT_PROMOTE=True,
    CT_PUT_TREND_MIN=80,
    CT_CALL_TREND_MAX=20,

    # CTSL — Counter-Trend Score Lift (Stage 1 winner, shipped 2026-05-08).
    # ADDITIVELY stacks on CT_PROMOTE (B-config Stage 3 winner).
    # 3-stage WR7-primary calibration (process.md three-stage framework).
    # Stage 3 N=500×8 evidence vs A baseline (CT_PROMOTE only, no CTSL):
    #   - 5y DD: 71.0% → 70.6% (−0.40pp; T4 PASS)
    #   - 22-now DD: 73.1% → 69.7% (−3.40pp)
    #   - 2023 DD: 72.2% → 69.0% (−3.20pp)
    #   - 2025 DD: 64.0% → 59.0% (−5.00pp)
    #   - Per-trade WR7 lift on affected put cohort: 88.72% (N=266 5y)
    #   - Per-trade WR7 lift on affected call cohort: 78.26% (N=69 5y)
    #   - W4/T5 per-window stability: PASS (no >5pp regression any window)
    #   - T6 collapse: 0% on every cell
    # Substitution path (C config: CTSL replacing CT_PROMOTE) was REJECTED
    # at Stage 3 T4 (+2.20pp 5y DD). CT_PROMOTE earns its keep via accidental
    # ULTRA-slot capping that CTSL alone cannot replicate.
    # See experiments/ctsl/FINDINGS.md.
    CTSL_ENABLED=True,
    CTSL_CALL_TREND_MAX=15,            # tighter than CT_PROMOTE (20); only deepest CT-call
    CTSL_CALL_TARGET=98.4,             # lift target near top of cascade
    CTSL_CALL_ALPHA=0.56,
    CTSL_CALL_TREND_POWER=2.82,        # concave — concentrates lift on deepest trend
    CTSL_CALL_TIER_FLOOR=74.7,         # rescues 70-74 from overflow=0.00 cliff
    CTSL_CALL_SCORE_NORM_WEIGHT=0.75,  # positive = lift stronger CT signals more
    CTSL_CALL_SCORE_NORM_POWER=2.27,
    CTSL_PUT_TREND_MIN=76,             # wider than CT_PROMOTE (80); more puts touched
    CTSL_PUT_TARGET=-0.13,             # push toward 0 (deep put_top tier)
    CTSL_PUT_ALPHA=0.83,
    CTSL_PUT_TREND_POWER=0.99,         # near-linear in trend distance
    CTSL_PUT_TIER_CEILING=27.9,        # essentially no ceiling (puts don't get reverse-lifted)
    CTSL_PUT_SCORE_NORM_WEIGHT=-0.22,  # negative = lift weaker puts more (rescue mode)
    CTSL_PUT_SCORE_NORM_POWER=1.68,

    # SAW Put U-curve — sector-breadth-driven put alloc gradient
    # Shipped 2026-05-08 (Phase B/C Bayesian sweep, 30 DTE Region B winner).
    # At each put signal date, lookup cross-sec ETF breadth (% of 11 SPDRs
    # above EMA50) and compute U-curve scale: contracts in 60-90 "bad zone"
    # (where pnl% lowest) and amplifies at <20 / >95 extremes (where pnl%
    # highest, mean-reversion-driven).
    # Stage C N=300×8 evidence vs baseline:
    #   - 7 of 8 annual windows IMPROVE compound (2021 +103%, 2022 +57%,
    #     2023 +54%, 2024 +98%, 2025 +11%, 22-now +182%; only dip −33%, 5y
    #     compound-chain artifact −35%)
    #   - 5y DD 71.7% → 70.6% (−1.1pp); 22-now 72.4% → 70.0% (−2.4pp)
    #   - Bear-year DD: 2022 73.0% → 68.4% (−4.6pp), 2023 71.6% → 70.5%
    #   - 0% collapse on every cell
    # See experiments/saw_put_ucurve/OVERNIGHT_SHIP_REPORT.md.
    SAW_PUT_UCURVE_ENABLED=True,
    SAW_PUT_UCURVE_SHAPE='quadratic',
    SAW_PUT_UCURVE_MIDPOINT=72.0,    # Region B center (vs Region A's 77)
    SAW_PUT_UCURVE_HALFWIDTH=18.0,   # narrow band — wider hw fails DD
    SAW_PUT_UCURVE_FLOOR=0.55,       # min scale at trough (Region B requires looser)
    SAW_PUT_UCURVE_CEIL=1.35,        # max amplification at extremes
    SAW_PUT_UCURVE_POWER=3.0,        # quadratic curvature (sharper dampener)
    SAW_PUT_UCURVE_K=5.0,            # unused (shape='quadratic')

    VOL_LOOKBACK=60,
    COLLAPSE_THRESHOLD=0.20,

    # 30 DTE has its own option config as of 2026-05-04 (v32_optim ship).
    # Identity check in assess_combos() will emit separate TP% pass for each DTE.
    option=OPT_30DTE,
)

# 15 DTE — Phase 15B C1 winner shipped 2026-04-28.
STRATEGY_15DTE = DteStrategyConfig(
    name='15dte_C1',
    HOLD_DAYS=7,
    CALENDAR_HOLD=False,   # 15 DTE not yet separately optimized on the honest engine;
    HOLD_CAL_DAYS=10,      #   keep the legacy path (schema parity; inert while False).
    NOMINAL_CAL_DTE=15,    #   honest-calendar 15 DTE port is a documented fast-follow.
    PREMIUM_MULT=1.29,
    HARD_SELL_LOSS=-0.45,   # 15 DTE day-7 ≈ -46% empirical (theta scaling)

    MAX_POSITIONS=8,        # cap concurrent exposure for DD safety
    MAX_POSITIONS_CALL=None,
    MAX_POSITIONS_PUT=None,
    # Practical exposure saturation is not validated for 15 DTE. Keep schema
    # parity with neutral values; half-DTE allocation has different tail
    # dynamics and already uses tighter max-position sizing.
    PRACTICAL_EXPOSURE_ENABLED=False,
    PRACTICAL_CAPITAL_CEILING=0.0,
    GROSS_PREMIUM_CAP=0.0,
    CALL_PREMIUM_CAP=0.0,
    PUT_PREMIUM_CAP=0.0,
    OPP_SAT_CALL_REF=0.0,
    OPP_SAT_PUT_REF=0.0,
    OPP_SAT_POWER=1.0,
    OPP_SAT_FLOOR=0.0,
    # Router is a 30 DTE portfolio overlay that selectively borrows 15 DTE
    # call outcomes. It is not a standalone 15 DTE strategy change.
    DTE_ROUTER_ENABLED=False,
    DTE_ROUTER_TARGET_DTE=15,
    DTE_ROUTER_SCORE_MIN=80,
    DTE_ROUTER_TREND_LT=50.0,
    DTE_ROUTER_VIX_MIN=0.0,
    DTE_ROUTER_VIX_MAX=0.0,   # crash-gate off by default; the router sweep tunes this for broad 15DTE
    DTE_ROUTER_REGIME_MIN=0.0,
    DTE_ROUTER_REGIME_MAX=100.0,
    DTE_ROUTER_DAY_CAP=0,
    DTE_ROUTER_ALLOC_SCORE_CAP=0,
    DTE_ROUTER_EXCLUDED_SYMBOLS=(),
    PRIMARY_THRESHOLD=75,
    OVERFLOW_THRESHOLD=70,
    PUT_THRESHOLD=25,

    # Cascade refined 2026-05-05 by v15_optim Bayesian campaign (4-phase B/C/D/E).
    # Phase B sweep (16 evals × N=100 × 8 windows on v35 algorithm) found that
    # 15 DTE's optimum is fundamentally DIFFERENT from 30 DTE — wants TOP-heavy
    # concentration (top=0.17) and minimal puts (all 0.08) rather than 30 DTE's
    # mid-cut shape. Phase D N=300 + Phase E N=500 confirmed cascade-only ship
    # delivers astronomical compound improvement at meaningful DD reduction.
    #
    # N=500 ship gate vs prior baseline:
    #   5y:     +1.53e8% / DD 78.9% → +3.26e19% / DD 74.5% (10¹¹× compound, −4.4pp DD)
    #   22-now: +1.93e4% / DD 79.8% → +4.04e13% / DD 74.6% (2×10⁹× compound, −5.2pp DD)
    #   2022:   +1.67e4% / DD 79.4% → +3.21e5% / DD 74.0% (+1818%, −5.4pp DD)
    #   dip:    +2,578% / DD 77.9% → +7,605% / DD 74.2% (+195%, −3.7pp DD)
    #   7 of 8 windows DD improved; only 2024 −77% return (bull-year multiple
    #   reduced — accepted DD/return tradeoff). 0% collapse on every cell.
    #
    # JOINT (B1 cascade + Phase C TP/SL) was tested and REJECTED: 2024 DD
    # spiked to 86% under JOINT vs 85% under B1-alone. TP/SL changes don't
    # transfer between DTEs — 15 DTE TP/SL is at a local optimum (Phase C
    # never improved meaningfully on current values).
    #
    # 70% absolute DD target unreachable for 15 DTE under bounded-fill MC —
    # relative DD reduction is what's available. See experiments/v15_optim/.
    TIER_ALLOC={
        'ultra':    0.18,   # 95+   (unchanged — top-of-cascade kept lower than 30 DTE's 0.20)
        'top':      0.17,   # 85-94 (was 0.12 — bumped UP; opposite of 30 DTE's mid-heavy shape)
        'mid':      0.12,   # 80-84 (was 0.15 — cut toward 30 DTE's mid)
        'low':      0.08,   # 75-79 (was 0.15 — significantly cut; 15 DTE volume engine smaller than 30 DTE)
        'overflow': 0.00,
    },
    PUT_TIER_ALLOC={
        'put_top':  0.08,   # ≤15  (was 0.10 — cut to floor; 15 DTE put TP rates much lower than 30 DTE)
        'put_mid':  0.08,   # 16-20 (was 0.12)
        'put_low':  0.08,   # 21-25 (was 0.12 — all puts at floor for DD safety)
    },

    F3F_CALL_THRESH=50.0,
    F3F_CALL_FLOOR=0.40,    # tighter than 30 DTE for stronger weak-tape contraction
    F3F_CALL_LOW=20.0,
    F3F_PUT_THRESH=75.0,
    F3F_PUT_FLOOR=0.40,
    F3F_PUT_HIGH=95.0,
    ALLOC_SCALE_FLOOR=0.25,
    ALLOC_SCALE_CEIL=1.75,

    REGIME_SLOPE=1.0,
    REGIME_SLOPE_PUT=0.0,
    REGIME_SLOPE_UP=0.0,
    REGIME_SLOPE_DOWN=1.0,
    REGIME_SLOPE_PUT_UP=-0.5,
    REGIME_SLOPE_PUT_DOWN=None,
    BREADTH_ALLOC_ENABLED=True,

    # H3 DD-soft-band — disabled for 15 DTE (not tested under bounded-fill MC
    # for this strategy; the 30 DTE calibration LO=0.40/HI=0.60 may not
    # transfer cleanly to a half-DTE strategy with different tail dynamics).
    # Keeping the field present for schema parity; floor=1.0 → no contraction.
    DD_SOFT_BAND_LO=0.0,
    DD_SOFT_BAND_HI=0.0,
    DD_SOFT_CALL_FLOOR=1.0,

    # RXDD — disabled for 15 DTE (not validated under bounded-fill MC for the
    # half-DTE strategy; fields present for schema parity, ENABLED=False → no-op).
    RXDD_ENABLED=False,
    RXDD_VIX_C=22.701,
    RXDD_VIX_W=3.14,
    RXDD_DEPTH=0.447,
    RXDD_DD_MIN=0.077,

    # MWDD — disabled for 15 DTE (not validated under bounded-fill MC for the
    # half-DTE strategy; fields present for schema parity, ENABLED=False → no-op).
    MWDD_ENABLED=False,
    MWDD_MCC_C=-0.336,
    MWDD_MCC_W=22.185,
    MWDD_DEPTH=0.337,
    MWDD_DD_MIN=0.128,
    MWDD_VIX_PANIC=28.0,

    # TVDD — disabled for 15 DTE (not validated under bounded-fill MC for the
    # half-DTE strategy; fields present for schema parity, ENABLED=False → no-op).
    TVDD_ENABLED=False,
    TVDD_TRIN_C=1.042,
    TVDD_TRIN_W=0.268,
    TVDD_DEPTH=0.426,
    TVDD_DD_MIN=0.291,
    TVDD_VIX_PANIC=28.0,

    # BDIV — disabled for 15 DTE (not validated under bounded-fill MC for the
    # half-DTE strategy; fields present for schema parity, ENABLED=False → no-op).
    BDIV_ENABLED=False,
    BDIV_PROX_CUT=0.0198,
    BDIV_PROX_FULL=0.0075,
    BDIV_GAP_C=7.716,
    BDIV_GAP_W=3.4571,
    BDIV_DEPTH=0.53,

    # SVR — disabled for 15 DTE (not validated under bounded-fill MC for the
    # half-DTE strategy; fields present for schema parity, ENABLED=False → no-op).
    SVR_ENABLED=False,
    SVR_LO_CUT=0.50,
    SVR_LO_FULL=0.70,
    SVR_HI_FULL=1.25,
    SVR_HI_CUT=1.65,
    SVR_FLOOR=0.50,

    # Dead-hold post-SL mechanism — shipped 2026-05-01 after N=300 hardening
    # sweep. T-0.50_P-0.25 is the only variant clearing the 80% Conservative
    # DD ceiling on every window for BOTH DTEs:
    #   30 DTE: avg DD 75.4% / max 79.3%, 5y compound +1.00e+29% (vs OFF +8.5e+25%)
    #   15 DTE: avg DD 78.0% / max 79.7%, 5y compound +5.38e+09% (vs OFF +1.1e+03%)
    # Initial N=150 ship picked POPOUT=-0.30; N=300 hardening (the documented
    # P1 gate) showed -0.30 breaches 80% on 5y (30 DTE) and on 5y+2022 (15 DTE).
    # Tighter popout (-0.25) exits earlier on recoveries and wins both DTEs.
    # See known-issues.md "Dead-hold post-SL mechanism (#20)".
    DEAD_HOLD_ENABLED=True,
    DEAD_HOLD_TRIGGER_PNL=-0.50,
    DEAD_HOLD_POPOUT_PNL=-0.25,

    # EARN_SUPP_PUT — RETIRED 2026-05-06 (replaced by score-stage PESS in v39).
    EARN_SUPP_PUT=False,
    EARN_SUPP_PUT_DAYS=5,
    EARN_SUPP_PUT_MIN_OV=16,
    EARN_SUPP_PUT_MAX_OV=20,

    # Weak-weekly call filter — disabled for 15 DTE (not validated under
    # bounded-fill MC for half-DTE strategy; the 30 DTE per-trade signal
    # is identical but cascade dynamics differ enough that re-validation
    # is required before enabling). Schema parity only.
    WEAK_WEEKLY_CALL_DROP=False,
    WEAK_WEEKLY_CALL_MIN_OV=70,
    WEAK_WEEKLY_CALL_MAX_OV=84,
    WEAK_WEEKLY_CALL_WADJ_LT=0.0,
    WEAK_WEEKLY_CALL_STOCH_GE=35,

    CT_PROMOTE=True,
    CT_PUT_TREND_MIN=80,
    CT_CALL_TREND_MAX=20,

    # CTSL — DISABLED for 15 DTE per registry not_wired status. Schema parity
    # only; values are no-op since CTSL_ENABLED=False prevents the engine from
    # invoking the lift/dampen path. Calibration owed before enable: re-run
    # Stage 1 WR7 + Stage 3 N=500×8 portfolio MC on 15 DTE substrate.
    CTSL_ENABLED=False,
    CTSL_CALL_TREND_MAX=15,
    CTSL_CALL_TARGET=98.4,
    CTSL_CALL_ALPHA=0.56,
    CTSL_CALL_TREND_POWER=2.82,
    CTSL_CALL_TIER_FLOOR=74.7,
    CTSL_CALL_SCORE_NORM_WEIGHT=0.75,
    CTSL_CALL_SCORE_NORM_POWER=2.27,
    CTSL_PUT_TREND_MIN=76,
    CTSL_PUT_TARGET=-0.13,
    CTSL_PUT_ALPHA=0.83,
    CTSL_PUT_TREND_POWER=0.99,
    CTSL_PUT_TIER_CEILING=27.9,
    CTSL_PUT_SCORE_NORM_WEIGHT=-0.22,
    CTSL_PUT_SCORE_NORM_POWER=1.68,

    # SAW Put U-curve — SHIPPED 2026-05-09 for 15 DTE (Phase D winner).
    # Calibration distinct from 30 DTE: 15 DTE optimum is sigmoid (vs quad)
    # with ceil=1.00 (vs 1.35) — NO breadth-extreme amplification, ONLY
    # contraction in mid zone. 30 DTE Region B applied to 15 DTE smoke
    # produced 5y_dd +2.9pp (worse), so a fresh search was required.
    # Stage 3 N=500x8 evidence: 5y DD 80.8% → 78.9% (−1.9pp), 22-now DD
    # 80.7% → 78.9% (−1.8pp), all 8 windows DD reduces, 7-of-8 windows
    # compound IMPROVES, all T1-T7 gates PASS, 0% collapse.
    # See `experiments/saw_put_ucurve_15dte/phase_d.log`.
    SAW_PUT_UCURVE_ENABLED=True,
    SAW_PUT_UCURVE_SHAPE='sigmoid',
    SAW_PUT_UCURVE_MIDPOINT=70.0,    # bad-zone center
    SAW_PUT_UCURVE_HALFWIDTH=25.0,   # wider than 30 DTE (18) — broader contraction band
    SAW_PUT_UCURVE_FLOOR=0.65,       # milder contraction than 30 DTE (0.55)
    SAW_PUT_UCURVE_CEIL=1.00,        # NO amplification — pure contraction
    SAW_PUT_UCURVE_POWER=2.0,        # unused (shape='sigmoid')
    SAW_PUT_UCURVE_K=12.0,           # sigmoid sharpness (mapped from power_k=4.0)

    VOL_LOOKBACK=60,
    COLLAPSE_THRESHOLD=0.20,

    option=SHARED_OPTION,   # ← aliased; assess_combos() dedupes TP% pass
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

ALL_STRATEGIES: Tuple[DteStrategyConfig, ...] = (STRATEGY_30DTE, STRATEGY_15DTE)


def by_dte(dte: str) -> DteStrategyConfig:
    """Look up a shipped strategy by its '30' / '15' DTE label."""
    s = str(dte).strip().lower().rstrip('dte').strip()
    for cfg in ALL_STRATEGIES:
        if cfg.name.lower().startswith(f'{s}dte'):
            return cfg
    raise KeyError(f'No strategy for dte={dte!r}; known={[c.name for c in ALL_STRATEGIES]}')


def assess_combos(
    strategies: Tuple[DteStrategyConfig, ...] = ALL_STRATEGIES,
) -> list[Tuple[str, str]]:
    """Return the (dte_label, metric) combos to run for `trader assess`.

    WR is always emitted under '30' (it's directional accuracy, generic
    barriers, DTE-agnostic by design — Phase 17, 2026-04-29).

    TP% is emitted once per unique `option` instance — Python identity
    check, not structural equality. When both shipped strategies alias
    `SHARED_OPTION`, returns one '30 tp' combo; the API serves both
    `dte=30&metric=tp` and `dte=15&metric=tp` from this single run.

    If a future ship constructs a separate OptionStrategyConfig instance
    for one DTE (because TP/SL on premium diverged), identity fails and
    both TP% combos are emitted independently.
    """
    combos: list[Tuple[str, str]] = [('30', 'wr')]

    seen_options: list[int] = []
    for cfg in strategies:
        oid = id(cfg.option)
        if oid in seen_options:
            continue
        seen_options.append(oid)
        # Pick the DTE label of the FIRST strategy aliasing this option
        # instance — gives a stable, deterministic combo list.
        dte_label = cfg.name.split('dte')[0]
        combos.append((dte_label, 'tp'))

    return combos


def to_json_dict(cfg: DteStrategyConfig) -> dict:
    """Serialize a DteStrategyConfig (with embedded OptionStrategyConfig
    and computed properties) to a JSON-safe dict, for the
    /api/strategy/config endpoint that JS consumers will fetch.
    """
    base = asdict(cfg)
    # Inject computed @property values explicitly (asdict doesn't include them)
    base['NET_HARD_SELL'] = cfg.NET_HARD_SELL
    base['TP_SIGMA_BASE'] = cfg.TP_SIGMA_BASE
    base['TP_SIGMA_STRESS'] = cfg.TP_SIGMA_STRESS
    base['SL_SIGMA_BASE'] = cfg.SL_SIGMA_BASE
    base['SL_SIGMA_STRESS'] = cfg.SL_SIGMA_STRESS
    base['PUT_TP_SIGMA'] = cfg.PUT_TP_SIGMA
    base['PUT_SL_SIGMA'] = cfg.PUT_SL_SIGMA
    base['option']['NET_TP_BASE'] = cfg.option.NET_TP_BASE
    base['option']['NET_TP_STRESS'] = cfg.option.NET_TP_STRESS
    base['option']['NET_SL_BASE'] = cfg.option.NET_SL_BASE
    base['option']['NET_SL_STRESS'] = cfg.option.NET_SL_STRESS
    base['option']['PUT_NET_TP'] = cfg.option.PUT_NET_TP
    base['option']['PUT_NET_SL'] = cfg.option.PUT_NET_SL
    return base
