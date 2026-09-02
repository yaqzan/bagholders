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
    DD circuit breaker, earnings put suppression). One instance per
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
from types import MappingProxyType
from typing import Dict, Optional, Tuple


# ─── Calibration discipline ──────────────────────────────────────────────────
#
# CALIBRATION_CUTOFF_DATE — the line in the sand for out-of-sample validation.
# All score-stage dampeners shipped to date (v25 mis-stress, v27 WCF,
# v28/v35 EARN_BOOST, v32 CWCF, v37 PCD, v38 CWWD, v39 PESS, v40 SVD) were
# calibrated against the same 5-10y barrier-touch outcomes. There is currently
# no clean holdout to validate that the cumulative stack is not collectively
# overfitting that distribution.
#
# Locked 2026-05-06 per known-issues.md Priority #11. Calibration sweeps,
# miss-ledger mining, lift-table rebuilds, and any `experiments/*/sweep.py`
# MUST filter to dates ≤ this cutoff via `experiments._holdout` helpers
# (assert_no_holdout_leak / pre_cutoff_filter). Forward data accumulates
# untouched as a true out-of-sample window.
#
# Re-evaluation target: ~6 months from lock (≈ 2026-11-15) — at that point
# the post-cutoff window will have enough barrier-resolved peaks to validate
# whether the v37→v40 stack generalizes.
CALIBRATION_CUTOFF_DATE: str = "2026-05-15"  # ISO yyyy-mm-dd


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


# 15 DTE option params (preserved at v32 ship values — optimization campaign
# 2026-05-04 was 30 DTE only; 15 DTE retune is documented as deferred work).
SHARED_OPTION = OptionStrategyConfig(
    TP_BASE=0.35,
    TP_STRESS=0.40,
    SL_BASE=-0.30,
    SL_STRESS=-0.35,
    BREADTH_THRESHOLD=50,
    PUT_TP=0.35,
    PUT_SL=-0.20,
    PUT_BREADTH_MODE='none',
    PUT_BREADTH_THRESHOLD=50,
    PUT_TP_STRESS=0.30,
    PUT_SL_STRESS=-0.20,
    PUT_SL_HOLD_BARS_DEFAULT=0,
    PUT_SL_HOLD_BARS_MONDAY=0,
    SLIP_ENTRY=0.0,
    SLIP_TP=0.0,
    SLIP_SL=0.0,
    SLIP_HARD=0.0,
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
    TP_BASE=0.33,
    TP_STRESS=0.42,
    SL_BASE=-0.27,
    SL_STRESS=-0.40,
    BREADTH_THRESHOLD=40,
    PUT_TP=0.35,
    PUT_SL=-0.20,
    PUT_BREADTH_MODE='none',
    PUT_BREADTH_THRESHOLD=50,
    PUT_TP_STRESS=0.30,
    PUT_SL_STRESS=-0.20,
    PUT_SL_HOLD_BARS_DEFAULT=0,
    PUT_SL_HOLD_BARS_MONDAY=0,
    SLIP_ENTRY=0.0,
    SLIP_TP=0.0,
    SLIP_SL=0.0,
    SLIP_HARD=0.0,
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
    HOLD_DAYS: int                    # trading bars to hard sell
    PREMIUM_MULT: float               # ATM premium ≈ this × σ_daily
    HARD_SELL_LOSS: float             # gross premium loss at hard sell

    # Position sizing / pool
    MAX_POSITIONS: int
    MAX_POSITIONS_CALL: Optional[int]  # None = share global pool
    MAX_POSITIONS_PUT: Optional[int]
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

    # DD circuit breaker — pause new entries when running portfolio DD
    # exceeds threshold. Existing positions still resolve normally.
    DD_CIRCUIT_BREAKER: float

    # H3 — DD-soft-band call alloc contraction (shipped 2026-05-04).
    # When running portfolio DD ∈ [DD_SOFT_BAND_LO, DD_SOFT_BAND_HI], scale
    # call alloc linearly from 1.0 down to DD_SOFT_CALL_FLOOR. Above HI =
    # full floor. Below LO = no effect (1.0×).
    # Distinct from DD circuit breaker (binary stop at 0.60) and F3F
    # (breadth-only). Operates on the call side only.
    # All zero defaults / floor=1.0 = mechanism disabled.
    DD_SOFT_BAND_LO: float
    DD_SOFT_BAND_HI: float
    DD_SOFT_CALL_FLOOR: float

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

# 30 DTE — Phase H5_HOLD15_H40 winner shipped 2026-04-28, plus B68 DD
# breaker shipped 2026-04-29 (V6 + DD circuit breaker).
STRATEGY_30DTE = DteStrategyConfig(
    name='30dte_H5_HOLD15_H40',
    HOLD_DAYS=15,
    PREMIUM_MULT=1.82,
    HARD_SELL_LOSS=-0.40,

    MAX_POSITIONS=14,
    MAX_POSITIONS_CALL=None,
    MAX_POSITIONS_PUT=None,
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
    TIER_ALLOC={
        'ultra':    0.20,   # 95+   (unchanged)
        'top':      0.15,   # 85-94 (unchanged)
        'mid':      0.10,   # 80-84 (was 0.12 — cut to flat the mid-low band, free velocity for 75-79)
        'low':      0.10,   # 75-79 (unchanged; 75-79 is volume engine, can't go lower)
        'overflow': 0.00,   # 70-74 disabled
    },
    # Put cascade now monotonic (was inverted: 0.10/0.12/0.12 — more allocation
    # to weaker 16-25 puts than to strongest ≤15). Phase B winner monotonic
    # 0.12/0.10/0.08 — strongest puts get most capital.
    PUT_TIER_ALLOC={
        'put_top':  0.12,   # ≤15  (was 0.10 — heavier on highest-conviction puts)
        'put_mid':  0.10,   # 16-20 (was 0.12)
        'put_low':  0.08,   # 21-25 (was 0.12 — weakest tier cut)
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

    DD_CIRCUIT_BREAKER=0.60,

    # H3 DD-soft-band — shipped 2026-05-04 after dd_ledger investigation.
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
    # H4 (MAX_POSITIONS_CALL=8/10/12) tested in same investigation — all
    # failed P4 due to narrow-bull regime cost (2023 -33% MedRet).
    # See experiments/dd_ledger/FINDINGS.md.
    DD_SOFT_BAND_LO=0.40,
    DD_SOFT_BAND_HI=0.60,
    DD_SOFT_CALL_FLOOR=0.50,

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
    PREMIUM_MULT=1.29,
    HARD_SELL_LOSS=-0.45,   # 15 DTE day-7 ≈ -46% empirical (theta scaling)

    MAX_POSITIONS=8,        # cap concurrent exposure for DD safety
    MAX_POSITIONS_CALL=None,
    MAX_POSITIONS_PUT=None,
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

    DD_CIRCUIT_BREAKER=0.60,   # tighter than 30 DTE (clears 80% Cons floor)

    # H3 DD-soft-band — disabled for 15 DTE (not tested under bounded-fill MC
    # for this strategy; the 30 DTE calibration LO=0.40/HI=0.60 may not
    # transfer cleanly to a half-DTE strategy with different tail dynamics).
    # Keeping the field present for schema parity; floor=1.0 → no contraction.
    DD_SOFT_BAND_LO=0.0,
    DD_SOFT_BAND_HI=0.0,
    DD_SOFT_CALL_FLOOR=1.0,

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

    # SAW Put U-curve — disabled for 15 DTE (only validated under 30 DTE
    # bounded-fill MC; cascade dynamics differ enough that re-validation
    # is required before enabling). Schema parity only.
    SAW_PUT_UCURVE_ENABLED=False,
    SAW_PUT_UCURVE_SHAPE='quadratic',
    SAW_PUT_UCURVE_MIDPOINT=72.0,
    SAW_PUT_UCURVE_HALFWIDTH=18.0,
    SAW_PUT_UCURVE_FLOOR=0.55,
    SAW_PUT_UCURVE_CEIL=1.35,
    SAW_PUT_UCURVE_POWER=3.0,
    SAW_PUT_UCURVE_K=5.0,

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
