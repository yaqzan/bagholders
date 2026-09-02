"""Declarative manifest of every editable PORTFOLIO (Stage-2 / Stage-3) knob.

THE single source of truth for three consumers:
  1. The Backtest "Advanced parameters" UI  — auto-rendered from this manifest
     (src/pages/Backtest.js fetches it via /api/strategy/param-manifest), so a
     NEW knob appears in the UI automatically with its live default.
  2. The /api/backtest/run override mapping  — the endpoint iterates this manifest
     to read each param from the request and apply it to the run_cascade_backtest
     cfg (replacing ~130 lines of hand-wired _float() calls).
  3. The CI guard  — tests/test_portfolio_param_manifest.py asserts that EVERY
     field on strategy_config.DteStrategyConfig + OptionStrategyConfig is either a
     surfaced PARAM here or an explicit EXCLUDED entry. A new portfolio field with
     neither fails CI, forcing the author to decide "surface it" or "structural".

This is the portfolio-stage analog of the scoring-ship ALGORITHM_VERSION process:
when you ship a portfolio change you edit strategy_config (source of truth) and
this manifest auto-propagates it site-wide (UI defaults + overrides + drift guard).
See .claude/docs/deploy.md "Shipping a Portfolio Change — Full Consumer Checklist".

Param.src grammar (where the live default comes from, read at request time):
    'opt.<ATTR>'        -> STRATEGY.option.<ATTR>
    'dte.<ATTR>'        -> STRATEGY.<ATTR>
    'tier.<KEY>'        -> STRATEGY.TIER_ALLOC[<KEY>]
    'puttier.<KEY>'     -> STRATEGY.PUT_TIER_ALLOC[<KEY>]
default value shown/seeded = (raw * scale), abs() first if absval=True.

Param.cfg_key / transform = how the value reaches the run_cascade_backtest cfg:
    cfg_key set, transform '' -> cfg[cfg_key] = value
    cfg_key set, transform 'pct' -> cfg[cfg_key] = value/100
    transform 'tier:<KEY>' -> cfg['tier_alloc'][<KEY>] = value/100
    transform 'puttier:<KEY>' -> cfg['put_tier_alloc'][<KEY>] = value/100
    transform 'call_tp'/'call_sl'/'put_tp'/'put_sl' -> σ + net-pnl barrier pair
       (handled specially by the endpoint, which owns _to_sigma/_net_tp/_net_sl)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class Param:
    key: str                       # request/UI param name
    group: str                     # UI group heading
    label: str                     # display label
    src: str                       # live-default source (grammar above); '' = no config seed
    kind: str = "float"            # float | int | bool | choice
    unit: str = ""                 # %, σ, $, d, ...
    minv: float | None = None
    maxv: float | None = None
    step: float | None = None
    choices: Tuple[str, ...] = ()
    cfg_key: str = ""              # target key in run_cascade_backtest cfg (simple 1:1)
    transform: str = ""            # '', 'pct', 'tier:<k>', 'puttier:<k>', 'call_tp'...
    scale: float = 1.0             # default seed = raw * scale
    absval: bool = False           # abs(raw) first (for negative SL defaults shown positive)
    dte: str = "both"              # both | 30 | 15
    tip: str = ""


# ── Surfaced knobs ────────────────────────────────────────────────────────────
PARAMS: list[Param] = [
    # ── Call exits ────────────────────────────────────────────────────────────
    Param("tp_pct", "Call exits", "Call TP", "opt.TP_BASE", unit="%", minv=0, maxv=300, step=1,
          scale=100, transform="call_tp", tip="Take-profit on call premium (base regime)."),
    Param("tp_stress_pct", "Call exits", "Call TP (stress)", "opt.TP_STRESS", unit="%", minv=0, maxv=300,
          step=1, scale=100, transform="call_tp_stress", tip="Call TP when breadth ≤ stress threshold."),
    Param("sl_pct", "Call exits", "Call SL", "opt.SL_BASE", unit="%", minv=0, maxv=100, step=1,
          scale=100, absval=True, transform="call_sl", tip="Stop-loss on call premium (base regime)."),
    Param("sl_stress_pct", "Call exits", "Call SL (stress)", "opt.SL_STRESS", unit="%", minv=0, maxv=100,
          step=1, scale=100, absval=True, transform="call_sl_stress", tip="Call SL under breadth stress."),
    Param("breadth_threshold", "Call exits", "Stress breadth ≤", "opt.BREADTH_THRESHOLD", kind="int",
          minv=0, maxv=100, step=1, cfg_key="breadth_threshold",
          tip="Breadth score at/below which the stress TP/SL band switches in."),
    Param("hard_sell_day", "Call exits", "Hard-sell day", "dte.HOLD_DAYS", kind="int", unit="d", minv=1,
          maxv=60, step=1, cfg_key="hold_calendar_days", tip="Force-close day if no TP/SL hit."),
    Param("hard_sell_loss", "Call exits", "Hard-sell P&L", "dte.HARD_SELL_LOSS", unit="%", minv=-100,
          maxv=100, step=1, scale=100, cfg_key="net_hard", transform="pct",
          tip="Theta-priced nominal P&L at the day-N hard sell."),
    Param("breadth_adaptive", "Call exits", "Breadth-adaptive TP/SL", "", kind="bool", cfg_key="breadth_adaptive",
          tip="Switch to the stress TP/SL band when breadth ≤ threshold."),

    # ── Put exits ─────────────────────────────────────────────────────────────
    Param("put_tp_pct", "Put exits", "Put TP", "opt.PUT_TP", unit="%", minv=0, maxv=300, step=1,
          scale=100, transform="put_tp", tip="Take-profit on put premium."),
    Param("put_sl_pct", "Put exits", "Put SL", "opt.PUT_SL", unit="%", minv=0, maxv=100, step=1,
          scale=100, absval=True, transform="put_sl", tip="Stop-loss on put premium."),
    Param("put_sl_hold_default", "Put exits", "Put SL hold (def)", "opt.PUT_SL_HOLD_BARS_DEFAULT",
          kind="int", unit="bar", minv=0, maxv=10, step=1, cfg_key="put_sl_hold_default",
          tip="Bars to suppress put SL after entry (non-Monday)."),
    Param("put_sl_hold_monday", "Put exits", "Put SL hold (Mon)", "opt.PUT_SL_HOLD_BARS_MONDAY",
          kind="int", unit="bar", minv=0, maxv=10, step=1, cfg_key="put_sl_hold_monday",
          tip="Bars to suppress put SL for Monday entries."),

    # ── Cascade allocation (calls) ────────────────────────────────────────────
    # cfg goes into the single tier_alloc dict under DISPLAY keys (95+, p<=15, ...);
    # defaults are sourced from the semantic TIER_ALLOC / PUT_TIER_ALLOC keys.
    Param("alloc_95plus", "Cascade — calls", "95+ %", "tier.ultra", unit="%", minv=0, maxv=100, step=0.5,
          scale=100, transform="tier:95+", tip="Per-trade allocation for 95+ (ultra) calls."),
    Param("alloc_85_94", "Cascade — calls", "85-94 %", "tier.top", unit="%", minv=0, maxv=100, step=0.5,
          scale=100, transform="tier:85-94"),
    Param("alloc_80_84", "Cascade — calls", "80-84 %", "tier.mid", unit="%", minv=0, maxv=100, step=0.5,
          scale=100, transform="tier:80-84"),
    Param("alloc_75_79", "Cascade — calls", "75-79 %", "tier.low", unit="%", minv=0, maxv=100, step=0.5,
          scale=100, transform="tier:75-79"),
    Param("alloc_70_74", "Cascade — calls", "70-74 overflow %", "tier.overflow", unit="%", minv=0, maxv=100,
          step=0.5, scale=100, transform="tier:70-74",
          tip="Overflow tier (70-74). Filled only after all 75+ slots, inside the gross cap. "
              "Requires the score floor to include 70-74 (auto when this is > 0)."),
    # ── Cascade allocation (puts) — same tier_alloc dict, display keys ─────────
    Param("alloc_p15", "Cascade — puts", "≤15 %", "puttier.put_top", unit="%", minv=0, maxv=100, step=0.5,
          scale=100, transform="tier:p<=15"),
    Param("alloc_p16_20", "Cascade — puts", "16-20 %", "puttier.put_mid", unit="%", minv=0, maxv=100,
          step=0.5, scale=100, transform="tier:p16-20"),
    Param("alloc_p21_25", "Cascade — puts", "21-25 %", "puttier.put_low", unit="%", minv=0, maxv=100,
          step=0.5, scale=100, transform="tier:p21-25"),

    # ── Position caps ─────────────────────────────────────────────────────────
    Param("max_positions", "Position caps", "Max positions", "dte.MAX_POSITIONS", kind="int", minv=1,
          maxv=60, step=1, cfg_key="max_positions"),
    Param("max_positions_call", "Position caps", "Max calls", "dte.MAX_POSITIONS_CALL", kind="int", minv=0,
          maxv=60, step=1, cfg_key="max_positions_call"),
    Param("max_positions_put", "Position caps", "Max puts", "dte.MAX_POSITIONS_PUT", kind="int", minv=0,
          maxv=60, step=1, cfg_key="max_positions_put"),

    # ── Practical exposure ────────────────────────────────────────────────────
    Param("practical_exposure_enabled", "Practical exposure", "Enabled", "dte.PRACTICAL_EXPOSURE_ENABLED",
          kind="bool", cfg_key="practical_exposure_enabled"),
    Param("practical_capital_ceiling", "Practical exposure", "Capital ceiling", "dte.PRACTICAL_CAPITAL_CEILING",
          unit="$", minv=0, maxv=1e9, step=1e6, cfg_key="practical_capital_ceiling",
          tip="0 = uncapped (Apex). Sizing base = min(equity, ceiling)."),
    Param("gross_premium_cap", "Practical exposure", "Gross premium cap", "dte.GROSS_PREMIUM_CAP", unit="%",
          minv=0, maxv=100, step=1, scale=100, cfg_key="gross_premium_cap", transform="pct",
          tip="Max open premium as % of base. Apex 50 (the capital-velocity optimum)."),
    Param("call_premium_cap", "Practical exposure", "Call premium cap", "dte.CALL_PREMIUM_CAP", unit="%",
          minv=0, maxv=100, step=1, scale=100, cfg_key="call_premium_cap", transform="pct"),
    Param("put_premium_cap", "Practical exposure", "Put premium cap", "dte.PUT_PREMIUM_CAP", unit="%",
          minv=0, maxv=100, step=1, scale=100, cfg_key="put_premium_cap", transform="pct"),
    Param("opp_sat_call_ref", "Practical exposure", "Opp-sat call ref", "dte.OPP_SAT_CALL_REF", minv=0,
          maxv=100, step=1, cfg_key="opp_sat_call_ref"),
    Param("opp_sat_put_ref", "Practical exposure", "Opp-sat put ref", "dte.OPP_SAT_PUT_REF", minv=0,
          maxv=100, step=1, cfg_key="opp_sat_put_ref"),
    Param("opp_sat_power", "Practical exposure", "Opp-sat power", "dte.OPP_SAT_POWER", minv=0, maxv=2,
          step=0.05, cfg_key="opp_sat_power"),
    Param("opp_sat_floor", "Practical exposure", "Opp-sat floor", "dte.OPP_SAT_FLOOR", minv=0, maxv=1,
          step=0.05, cfg_key="opp_sat_floor"),

    # ── DD soft-band (call alloc contraction) ─────────────────────────────────
    Param("dd_soft_lo", "DD soft-band", "Band low", "dte.DD_SOFT_BAND_LO", minv=0, maxv=1, step=0.01,
          cfg_key="dd_soft_band_lo", tip="DD where call-alloc contraction starts."),
    Param("dd_soft_hi", "DD soft-band", "Band high", "dte.DD_SOFT_BAND_HI", minv=0, maxv=1, step=0.01,
          cfg_key="dd_soft_band_hi", tip="DD where contraction reaches the floor."),
    Param("dd_soft_floor", "DD soft-band", "Call floor", "dte.DD_SOFT_CALL_FLOOR", minv=0, maxv=1, step=0.01,
          cfg_key="dd_soft_call_floor", tip="Min call-alloc multiplier at/above band high."),

    # ── RXDD (VIX-band call dampener) ─────────────────────────────────────────
    Param("rxdd_enabled", "RXDD (VIX band)", "Enabled", "dte.RXDD_ENABLED", kind="bool", cfg_key="rxdd_enabled"),
    Param("rxdd_vix_c", "RXDD (VIX band)", "VIX center", "dte.RXDD_VIX_C", minv=0, maxv=80, step=0.1,
          cfg_key="rxdd_vix_c"),
    Param("rxdd_vix_w", "RXDD (VIX band)", "VIX width", "dte.RXDD_VIX_W", minv=0.1, maxv=20, step=0.1,
          cfg_key="rxdd_vix_w"),
    Param("rxdd_depth", "RXDD (VIX band)", "Depth", "dte.RXDD_DEPTH", minv=0, maxv=1, step=0.01,
          cfg_key="rxdd_depth", tip="Max call-alloc contraction at band center."),
    Param("rxdd_dd_min", "RXDD (VIX band)", "DD min", "dte.RXDD_DD_MIN", minv=0, maxv=1, step=0.01,
          cfg_key="rxdd_dd_min", tip="Only contract once running DD ≥ this."),

    # ── MWDD (McClellan breadth-momentum flat-band call dampener) ─────────────
    Param("mwdd_enabled", "MWDD (McClellan band)", "Enabled", "dte.MWDD_ENABLED", kind="bool",
          cfg_key="mwdd_enabled"),
    Param("mwdd_mcc_c", "MWDD (McClellan band)", "McClellan center", "dte.MWDD_MCC_C", minv=-100, maxv=100,
          step=0.1, cfg_key="mwdd_mcc_c"),
    Param("mwdd_mcc_w", "MWDD (McClellan band)", "McClellan width", "dte.MWDD_MCC_W", minv=0.1, maxv=80,
          step=0.1, cfg_key="mwdd_mcc_w"),
    Param("mwdd_depth", "MWDD (McClellan band)", "Depth", "dte.MWDD_DEPTH", minv=0, maxv=1, step=0.01,
          cfg_key="mwdd_depth", tip="Max call-alloc contraction at band center."),
    Param("mwdd_dd_min", "MWDD (McClellan band)", "DD min", "dte.MWDD_DD_MIN", minv=0, maxv=1, step=0.01,
          cfg_key="mwdd_dd_min", tip="Only contract once running DD ≥ this."),
    Param("mwdd_vix_panic", "MWDD (McClellan band)", "VIX panic", "dte.MWDD_VIX_PANIC", minv=0, maxv=80,
          step=0.5, cfg_key="mwdd_vix_panic", tip="No contraction when VIX ≥ this (capitulation left alone)."),

    # ── SVR (semivol_r skew-bridge entry filter) ──────────────────────────────
    Param("svr_enabled", "SVR (semivol skew)", "Enabled", "dte.SVR_ENABLED", kind="bool",
          cfg_key="svr_enabled"),
    Param("svr_lo_cut", "SVR (semivol skew)", "Lo cut", "dte.SVR_LO_CUT", minv=0, maxv=3, step=0.01,
          cfg_key="svr_lo_cut"),
    Param("svr_lo_full", "SVR (semivol skew)", "Lo full", "dte.SVR_LO_FULL", minv=0, maxv=3, step=0.01,
          cfg_key="svr_lo_full"),
    Param("svr_hi_full", "SVR (semivol skew)", "Hi full", "dte.SVR_HI_FULL", minv=0, maxv=10, step=0.01,
          cfg_key="svr_hi_full"),
    Param("svr_hi_cut", "SVR (semivol skew)", "Hi cut", "dte.SVR_HI_CUT", minv=0, maxv=100, step=0.01,
          cfg_key="svr_hi_cut"),
    Param("svr_floor", "SVR (semivol skew)", "Floor", "dte.SVR_FLOOR", minv=0, maxv=1, step=0.01,
          cfg_key="svr_floor", tip="Min call-alloc scale outside the semivol_r sweet spot."),

    # ── TVDD (TRIN volume-flow neutral-band call dampener) ────────────────────
    Param("tvdd_enabled", "TVDD (TRIN band)", "Enabled", "dte.TVDD_ENABLED", kind="bool",
          cfg_key="tvdd_enabled"),
    Param("tvdd_trin_c", "TVDD (TRIN band)", "TRIN center", "dte.TVDD_TRIN_C", minv=0, maxv=5, step=0.01,
          cfg_key="tvdd_trin_c"),
    Param("tvdd_trin_w", "TVDD (TRIN band)", "TRIN width", "dte.TVDD_TRIN_W", minv=0.05, maxv=2, step=0.01,
          cfg_key="tvdd_trin_w"),
    Param("tvdd_depth", "TVDD (TRIN band)", "Depth", "dte.TVDD_DEPTH", minv=0, maxv=1, step=0.01,
          cfg_key="tvdd_depth", tip="Max call-alloc contraction at band center."),
    Param("tvdd_dd_min", "TVDD (TRIN band)", "DD min", "dte.TVDD_DD_MIN", minv=0, maxv=1, step=0.01,
          cfg_key="tvdd_dd_min", tip="Only contract once running DD ≥ this."),
    Param("tvdd_vix_panic", "TVDD (TRIN band)", "VIX panic", "dte.TVDD_VIX_PANIC", minv=0, maxv=80,
          step=0.5, cfg_key="tvdd_vix_panic", tip="No contraction when VIX ≥ this (capitulation left alone)."),

    # ── BDIV (pre-top breadth-divergence-at-highs call dampener) ──────────────
    Param("bdiv_enabled", "BDIV (pre-top divergence)", "Enabled", "dte.BDIV_ENABLED", kind="bool",
          cfg_key="bdiv_enabled",
          tip="Contract calls when SPY is near its 60d high while breadth rolls over (leading lever, no DD-gate)."),
    Param("bdiv_prox_cut", "BDIV (pre-top divergence)", "Prox cut", "dte.BDIV_PROX_CUT", minv=0, maxv=0.10,
          step=0.001, cfg_key="bdiv_prox_cut", tip="Contraction starts when SPY is within this fraction of its 60d high."),
    Param("bdiv_prox_full", "BDIV (pre-top divergence)", "Prox full", "dte.BDIV_PROX_FULL", minv=0, maxv=0.05,
          step=0.001, cfg_key="bdiv_prox_full", tip="Full proximity weight within this fraction of the 60d high."),
    Param("bdiv_gap_c", "BDIV (pre-top divergence)", "Gap center", "dte.BDIV_GAP_C", minv=0, maxv=20, step=0.1,
          cfg_key="bdiv_gap_c", tip="Gaussian center on the 10d breadth deterioration (deeper drops mean-revert; left alone)."),
    Param("bdiv_gap_w", "BDIV (pre-top divergence)", "Gap width", "dte.BDIV_GAP_W", minv=0.1, maxv=10, step=0.1,
          cfg_key="bdiv_gap_w", tip="Gaussian band width on the 10d breadth deterioration."),
    Param("bdiv_depth", "BDIV (pre-top divergence)", "Depth", "dte.BDIV_DEPTH", minv=0, maxv=1, step=0.01,
          cfg_key="bdiv_depth", tip="Max call-alloc contraction at band center and full proximity."),

    # ── SPREAD_TILT (75-79 component-spread call alloc haircut) ───────────────
    Param("spread_tilt_enabled", "SPREAD_TILT (component spread)", "Enabled", "dte.SPREAD_TILT_ENABLED",
          kind="bool", cfg_key="spread_tilt_enabled",
          tip="Haircut high-disagreement 75-79 calls (sqrt pop-var of the 5 component scores); down-weight only, 80-84 inverts."),
    Param("spread_tilt_lo", "SPREAD_TILT (component spread)", "Lo (full alloc)", "dte.SPREAD_TILT_LO",
          minv=0, maxv=60, step=0.1, cfg_key="spread_tilt_lo", tip="Spread at/below this = full alloc (low disagreement)."),
    Param("spread_tilt_hi", "SPREAD_TILT (component spread)", "Hi (full haircut)", "dte.SPREAD_TILT_HI",
          minv=0, maxv=60, step=0.1, cfg_key="spread_tilt_hi", tip="Spread at/above this = full haircut (high disagreement)."),
    Param("spread_tilt_depth", "SPREAD_TILT (component spread)", "Depth", "dte.SPREAD_TILT_DEPTH",
          minv=0, maxv=1, step=0.01, cfg_key="spread_tilt_depth", tip="Max contraction at/above Hi (floor 1-depth)."),

    # ── LIQUIDITY_FLOOR (option-volume admission filter) ──────────────────────
    Param("liquidity_floor", "Liquidity floor", "Floor (option vol 30d)", "dte.LIQUIDITY_FLOOR",
          unit="c/d", minv=0, maxv=50000, step=10, cfg_key="liquidity_floor",
          tip="Drop 75+ CALL signals whose trailing-30d avg option contract volume is below this. "
              "0 = off. Core-evidence-only staged ship-candidate (FF3_STAGEB_RESULTS.md); enable "
              "gated on P2.B live-fill confirmation. Apex failed T4 -- keep 0 there."),

    # ── Dead-hold ─────────────────────────────────────────────────────────────
    Param("dead_hold_enabled", "Dead-hold", "Enabled", "dte.DEAD_HOLD_ENABLED", kind="bool",
          cfg_key="dead_hold_enabled", tip="On SL with pnl ≤ trigger, hold for popout/day-N (collapse-preventing)."),
    Param("dead_hold_trigger_pnl", "Dead-hold", "Trigger P&L", "dte.DEAD_HOLD_TRIGGER_PNL", minv=-1, maxv=0,
          step=0.01, cfg_key="dead_hold_trigger_pnl"),
    Param("dead_hold_popout_pnl", "Dead-hold", "Popout P&L", "dte.DEAD_HOLD_POPOUT_PNL", minv=-1, maxv=0,
          step=0.01, cfg_key="dead_hold_popout_pnl"),

    # ── F3F breadth-driven allocation ─────────────────────────────────────────
    Param("breadth_alloc_enabled", "F3F breadth", "Enabled", "dte.BREADTH_ALLOC_ENABLED", kind="bool",
          cfg_key="breadth_alloc_enabled"),
    Param("f3f_call_thresh", "F3F breadth", "Call thresh", "dte.F3F_CALL_THRESH", minv=0, maxv=100, step=1,
          cfg_key="f3f_call_thresh"),
    Param("f3f_call_floor", "F3F breadth", "Call floor", "dte.F3F_CALL_FLOOR", minv=0, maxv=2, step=0.05,
          cfg_key="f3f_call_floor"),
    Param("f3f_call_low", "F3F breadth", "Call low brd", "dte.F3F_CALL_LOW", minv=0, maxv=100, step=1,
          cfg_key="f3f_call_low"),
    Param("f3f_put_thresh", "F3F breadth", "Put thresh", "dte.F3F_PUT_THRESH", minv=0, maxv=100, step=1,
          cfg_key="f3f_put_thresh"),
    Param("f3f_put_floor", "F3F breadth", "Put floor", "dte.F3F_PUT_FLOOR", minv=0, maxv=2, step=0.05,
          cfg_key="f3f_put_floor"),
    Param("f3f_put_high", "F3F breadth", "Put high brd", "dte.F3F_PUT_HIGH", minv=0, maxv=100, step=1,
          cfg_key="f3f_put_high"),

    # ── Regime slopes ─────────────────────────────────────────────────────────
    Param("alloc_scale_floor", "Regime slopes", "Scale floor", "dte.ALLOC_SCALE_FLOOR", minv=0, maxv=2,
          step=0.05, cfg_key="alloc_scale_floor"),
    Param("alloc_scale_ceil", "Regime slopes", "Scale ceil", "dte.ALLOC_SCALE_CEIL", minv=0, maxv=3,
          step=0.05, cfg_key="alloc_scale_ceil"),
    Param("regime_slope_up", "Regime slopes", "Call slope ↑", "dte.REGIME_SLOPE_UP", minv=-2, maxv=2,
          step=0.05, cfg_key="regime_slope_up"),
    Param("regime_slope_down", "Regime slopes", "Call slope ↓", "dte.REGIME_SLOPE_DOWN", minv=-2, maxv=2,
          step=0.05, cfg_key="regime_slope_down"),

    # ── CT promotion ──────────────────────────────────────────────────────────
    Param("ct_promote", "Counter-trend", "CT promote", "dte.CT_PROMOTE", kind="bool", cfg_key="ct_promote"),
    Param("ct_put_trend_min", "Counter-trend", "CT put trend ≥", "dte.CT_PUT_TREND_MIN", kind="int", minv=0,
          maxv=100, step=1, cfg_key="ct_put_trend_min"),
    Param("ct_call_trend_max", "Counter-trend", "CT call trend ≤", "dte.CT_CALL_TREND_MAX", kind="int", minv=0,
          maxv=100, step=1, cfg_key="ct_call_trend_max"),

    # ── CTSL (Counter-Trend Score Lift) — 30 DTE ──────────────────────────────
    Param("ctsl_enabled", "CTSL", "Enabled", "dte.CTSL_ENABLED", kind="bool", cfg_key="ctsl_enabled", dte="30"),
    Param("ctsl_call_trend_max", "CTSL", "Call trend ≤", "dte.CTSL_CALL_TREND_MAX", kind="int", minv=0,
          maxv=100, step=1, cfg_key="ctsl_call_trend_max", dte="30"),
    Param("ctsl_call_target", "CTSL", "Call target", "dte.CTSL_CALL_TARGET", minv=0, maxv=100, step=0.1,
          cfg_key="ctsl_call_target", dte="30"),
    Param("ctsl_call_alpha", "CTSL", "Call α", "dte.CTSL_CALL_ALPHA", minv=0, maxv=1, step=0.01,
          cfg_key="ctsl_call_alpha", dte="30"),
    Param("ctsl_call_trend_power", "CTSL", "Call trend pow", "dte.CTSL_CALL_TREND_POWER", minv=0, maxv=5,
          step=0.01, cfg_key="ctsl_call_trend_power", dte="30"),
    Param("ctsl_call_tier_floor", "CTSL", "Call tier floor", "dte.CTSL_CALL_TIER_FLOOR", minv=0, maxv=100,
          step=0.1, cfg_key="ctsl_call_tier_floor", dte="30"),
    Param("ctsl_call_score_norm_weight", "CTSL", "Call snorm wt", "dte.CTSL_CALL_SCORE_NORM_WEIGHT", minv=-2,
          maxv=2, step=0.01, cfg_key="ctsl_call_score_norm_weight", dte="30"),
    Param("ctsl_call_score_norm_power", "CTSL", "Call snorm pow", "dte.CTSL_CALL_SCORE_NORM_POWER", minv=0,
          maxv=5, step=0.01, cfg_key="ctsl_call_score_norm_power", dte="30"),
    Param("ctsl_put_trend_min", "CTSL", "Put trend ≥", "dte.CTSL_PUT_TREND_MIN", kind="int", minv=0, maxv=100,
          step=1, cfg_key="ctsl_put_trend_min", dte="30"),
    Param("ctsl_put_target", "CTSL", "Put target", "dte.CTSL_PUT_TARGET", minv=-5, maxv=100, step=0.1,
          cfg_key="ctsl_put_target", dte="30"),
    Param("ctsl_put_alpha", "CTSL", "Put α", "dte.CTSL_PUT_ALPHA", minv=0, maxv=1, step=0.01,
          cfg_key="ctsl_put_alpha", dte="30"),
    Param("ctsl_put_trend_power", "CTSL", "Put trend pow", "dte.CTSL_PUT_TREND_POWER", minv=0, maxv=5,
          step=0.01, cfg_key="ctsl_put_trend_power", dte="30"),
    Param("ctsl_put_tier_ceiling", "CTSL", "Put tier ceil", "dte.CTSL_PUT_TIER_CEILING", minv=0, maxv=100,
          step=0.1, cfg_key="ctsl_put_tier_ceiling", dte="30"),
    Param("ctsl_put_score_norm_weight", "CTSL", "Put snorm wt", "dte.CTSL_PUT_SCORE_NORM_WEIGHT", minv=-2,
          maxv=2, step=0.01, cfg_key="ctsl_put_score_norm_weight", dte="30"),
    Param("ctsl_put_score_norm_power", "CTSL", "Put snorm pow", "dte.CTSL_PUT_SCORE_NORM_POWER", minv=0,
          maxv=5, step=0.01, cfg_key="ctsl_put_score_norm_power", dte="30"),

    # ── SAW Put U-curve ───────────────────────────────────────────────────────
    Param("saw_put_ucurve_enabled", "SAW put U-curve", "Enabled", "dte.SAW_PUT_UCURVE_ENABLED", kind="bool",
          cfg_key="saw_put_ucurve_enabled"),
    Param("saw_put_ucurve_shape", "SAW put U-curve", "Shape", "dte.SAW_PUT_UCURVE_SHAPE", kind="choice",
          choices=("quadratic", "sigmoid"), cfg_key="saw_put_ucurve_shape"),
    Param("saw_put_ucurve_midpoint", "SAW put U-curve", "Midpoint", "dte.SAW_PUT_UCURVE_MIDPOINT", minv=0,
          maxv=100, step=1, cfg_key="saw_put_ucurve_midpoint"),
    Param("saw_put_ucurve_halfwidth", "SAW put U-curve", "Half-width", "dte.SAW_PUT_UCURVE_HALFWIDTH", minv=1,
          maxv=100, step=1, cfg_key="saw_put_ucurve_halfwidth"),
    Param("saw_put_ucurve_floor", "SAW put U-curve", "Floor", "dte.SAW_PUT_UCURVE_FLOOR", minv=0, maxv=2,
          step=0.05, cfg_key="saw_put_ucurve_floor"),
    Param("saw_put_ucurve_ceil", "SAW put U-curve", "Ceil", "dte.SAW_PUT_UCURVE_CEIL", minv=0, maxv=3,
          step=0.05, cfg_key="saw_put_ucurve_ceil"),
    Param("saw_put_ucurve_power", "SAW put U-curve", "Power", "dte.SAW_PUT_UCURVE_POWER", minv=0, maxv=6,
          step=0.1, cfg_key="saw_put_ucurve_power"),
    Param("saw_put_ucurve_k", "SAW put U-curve", "Sigmoid K", "dte.SAW_PUT_UCURVE_K", minv=0, maxv=30,
          step=0.5, cfg_key="saw_put_ucurve_k"),

    # ── DTE router (15DTE sleeve) ─────────────────────────────────────────────
    Param("dte_router_enabled", "DTE router", "Enabled", "dte.DTE_ROUTER_ENABLED", kind="bool",
          cfg_key="dte_router_enabled", dte="30"),
    Param("dte_router_score_min", "DTE router", "Score ≥", "dte.DTE_ROUTER_SCORE_MIN", kind="int", minv=0,
          maxv=100, step=1, cfg_key="dte_router_score_min", dte="30"),
    Param("dte_router_trend_lt", "DTE router", "Trend <", "dte.DTE_ROUTER_TREND_LT", minv=0, maxv=100, step=1,
          cfg_key="dte_router_trend_lt", dte="30"),
    Param("dte_router_day_cap", "DTE router", "Per-day cap", "dte.DTE_ROUTER_DAY_CAP", kind="int", minv=0,
          maxv=20, step=1, cfg_key="dte_router_day_cap", dte="30",
          tip="Max calls/day routed to 15 DTE. Validated optimum = 1 (broadening breaches collapse)."),
    Param("dte_router_vix_min", "DTE router", "VIX ≥", "dte.DTE_ROUTER_VIX_MIN", minv=0, maxv=80, step=0.5,
          cfg_key="dte_router_vix_min", dte="30"),
    Param("dte_router_vix_max", "DTE router", "VIX ≤ (crash-gate)", "dte.DTE_ROUTER_VIX_MAX", minv=0, maxv=80,
          step=0.5, cfg_key="dte_router_vix_max", dte="30", tip="0 = off. Route only when VIX ≤ this."),
    Param("dte_router_alloc_score_cap", "DTE router", "Alloc score cap", "dte.DTE_ROUTER_ALLOC_SCORE_CAP",
          kind="int", minv=0, maxv=100, step=1, cfg_key="dte_router_alloc_score_cap", dte="30"),

    # ── Slippage (asymmetric execution-cost canon) ────────────────────────────
    Param("slip_entry", "Slippage", "Entry", "opt.SLIP_ENTRY", minv=-0.1, maxv=0, step=0.001,
          cfg_key="slip_entry", tip="Mid-entry provides liquidity → 0 by canon."),
    Param("slip_tp", "Slippage", "TP exit", "opt.SLIP_TP", minv=-0.1, maxv=0, step=0.001, cfg_key="slip_tp",
          tip="Limit-TP provides liquidity → 0 by canon."),
    Param("slip_sl", "Slippage", "SL exit", "opt.SLIP_SL", minv=-0.1, maxv=0, step=0.001, cfg_key="slip_sl",
          tip="Forced exit takes liquidity → −0.015 (half-spread)."),
    Param("slip_hard", "Slippage", "Hard/expiry", "opt.SLIP_HARD", minv=-0.1, maxv=0, step=0.001,
          cfg_key="slip_hard"),
]


# ── Explicitly NOT surfaced (structural / derived / retired) ──────────────────
# Every excluded field needs a one-line reason. The CI guard requires this.
EXCLUDED: dict[str, str] = {
    # structural / identity / derived
    "name": "profile/strategy identity label, not a knob",
    "PREMIUM_MULT": "structural: ATM premium-to-σ multiple, fixed per DTE",
    "CALENDAR_HOLD": "honest-calendar theta toggle (standard on); profile-set, not a per-run UI scalar",
    "NOMINAL_CAL_DTE": "option calendar tenor for honest theta; profile-derived (e.g. Apex 15-DTE elbow), not a UI knob",
    "HOLD_CAL_DAYS": "calendar-day hard-sell deadline (honest-calendar); profile-set, surfaced via hard_sell_day (HOLD_DAYS)",
    "VOL_LOOKBACK": "structural: realized-vol lookback for σ barriers",
    "COLLAPSE_THRESHOLD": "structural: definition of portfolio collapse (ruin floor)",
    "DELTA": "structural: option delta for σ↔% conversion",
    "PRIMARY_THRESHOLD": "structural call gate (75); the UI 'min call score' field covers it",
    "OVERFLOW_THRESHOLD": "structural overflow floor (70); auto-engaged when alloc_70_74 > 0",
    "PUT_THRESHOLD": "structural put gate; the UI 'max put score' field covers it",
    "DTE_ROUTER_TARGET_DTE": "structural: router target is always 15 DTE",
    "DTE_ROUTER_REGIME_MIN": "advanced router regime band, off by default; not a primary knob",
    "DTE_ROUTER_REGIME_MAX": "advanced router regime band, off by default; not a primary knob",
    "DTE_ROUTER_EXCLUDED_SYMBOLS": "symbol tuple, not a scalar knob (config-only)",
    # put-side regime slopes / breadth (kept config-only; rarely tuned via UI)
    "REGIME_SLOPE": "legacy symmetric fallback, superseded by UP/DOWN; config-only",
    "REGIME_SLOPE_PUT": "legacy symmetric fallback; config-only",
    "REGIME_SLOPE_PUT_UP": "put-side regime slope, config-only (rarely UI-tuned)",
    "REGIME_SLOPE_PUT_DOWN": "put-side regime slope, config-only (rarely UI-tuned)",
    "PUT_BREADTH_MODE": "put breadth-adaptive mode (off in production); config-only",
    "PUT_BREADTH_THRESHOLD": "put breadth-adaptive threshold (off); config-only",
    "PUT_TP_STRESS": "put stress TP (breadth-adaptive off for puts); config-only",
    "PUT_SL_STRESS": "put stress SL (breadth-adaptive off for puts); config-only",
    # retired mechanisms (replaced by score-stage equivalents)
    "EARN_SUPP_PUT": "RETIRED 2026-05-06 → score-stage PESS (v39)",
    "EARN_SUPP_PUT_DAYS": "RETIRED → PESS",
    "EARN_SUPP_PUT_MIN_OV": "RETIRED → PESS",
    "EARN_SUPP_PUT_MAX_OV": "RETIRED → PESS",
    "WEAK_WEEKLY_CALL_DROP": "RETIRED 2026-05-06 → score-stage CWWD (v38)",
    "WEAK_WEEKLY_CALL_MIN_OV": "RETIRED → CWWD",
    "WEAK_WEEKLY_CALL_MAX_OV": "RETIRED → CWWD",
    "WEAK_WEEKLY_CALL_WADJ_LT": "RETIRED → CWWD",
    "WEAK_WEEKLY_CALL_STOCH_GE": "RETIRED → CWWD",
}


# ── Engine cfg-read surface (the truly-overridable-per-request knobs) ─────────
# Keys that backtest_cascade.run_backtest reads from the per-request cfg. A
# manifest Param is "editable" iff its cfg target lands here; the rest apply from
# strategy_config (profiles still reproduce, but per-run editing of them is a
# tracked engine-wiring follow-up — see deploy.md "Shipping a Portfolio Change").
# tests/test_portfolio_param_manifest.py::test_engine_cfg_keys_in_sync asserts
# this matches the engine source, so wiring a new param into run_backtest (adding
# its cfg.get) auto-flips it to editable here.
ENGINE_CFG_KEYS: frozenset = frozenset({
    "breadth_adaptive", "hold_calendar_days", "net_hard",
    "max_positions", "max_positions_call", "max_positions_put",
    "practical_exposure_enabled", "practical_capital_ceiling",
    "gross_premium_cap", "call_premium_cap", "put_premium_cap",
    "opp_sat_call_ref", "opp_sat_put_ref", "opp_sat_power", "opp_sat_floor",
    "tier_alloc",
    "tp_sigma_base", "tp_sigma_stress", "sl_sigma_base", "sl_sigma_stress",
    "net_tp_base", "net_tp_stress", "net_sl_base", "net_sl_stress",
    "put_tp_sigma", "put_sl_sigma", "put_net_tp", "put_net_sl",
    "put_sl_hold_default", "put_sl_hold_monday",
    "dd_soft_band_lo", "dd_soft_band_hi", "dd_soft_call_floor",
    "rxdd_enabled", "rxdd_vix_c", "rxdd_vix_w", "rxdd_depth", "rxdd_dd_min",
    "mwdd_enabled", "mwdd_mcc_c", "mwdd_mcc_w", "mwdd_depth", "mwdd_dd_min", "mwdd_vix_panic",
    "svr_enabled", "svr_lo_cut", "svr_lo_full", "svr_hi_full", "svr_hi_cut", "svr_floor",
    "tvdd_enabled", "tvdd_trin_c", "tvdd_trin_w", "tvdd_depth", "tvdd_dd_min", "tvdd_vix_panic",
    "bdiv_enabled", "bdiv_prox_cut", "bdiv_prox_full", "bdiv_gap_c", "bdiv_gap_w", "bdiv_depth",
    "spread_tilt_enabled", "spread_tilt_lo", "spread_tilt_hi", "spread_tilt_depth",
    "liquidity_floor",
})

# Engine cfg keys read by run_backtest that are NOT manifest knobs (internal /
# derived); excluded when the CI test compares ENGINE_CFG_KEYS to the source.
_NON_KNOB_CFG_KEYS: frozenset = frozenset({
    "delta", "ern_map", "prem_stop_loss", "premium_mult",
    "realloc_min_advantage", "realloc_strategy", "total_dte",
    # per-call honest-calendar tenor (profile-derived; e.g. Apex 15-DTE elbow)
    "calendar_hold", "nominal_cal_dte",
})

# cfg keys each barrier transform writes (so editability + the applier are exact).
_TRANSFORM_CFG_KEYS: dict = {
    "call_tp":        ("tp_sigma_base", "net_tp_base"),
    "call_tp_stress": ("tp_sigma_stress", "net_tp_stress"),
    "call_sl":        ("sl_sigma_base", "net_sl_base"),
    "call_sl_stress": ("sl_sigma_stress", "net_sl_stress"),
    "put_tp":         ("put_tp_sigma", "put_net_tp"),
    "put_sl":         ("put_sl_sigma", "put_net_sl"),
}

# cfg keys applied via backtest_cascade's per-request module-global override (the
# 48 mechanism knobs whose engine code reads module constants, not cfg.get). They
# are editable through that mechanism. MUST equal backtest_cascade._CFG_KNOB_GLOBALS
# keys (CI: test_engine_global_keys_in_sync).
ENGINE_GLOBAL_KEYS: frozenset = frozenset({
    'breadth_threshold', 'breadth_alloc_enabled',
    'f3f_call_thresh', 'f3f_call_floor', 'f3f_call_low', 'f3f_put_thresh', 'f3f_put_floor', 'f3f_put_high',
    'alloc_scale_floor', 'alloc_scale_ceil', 'regime_slope_up', 'regime_slope_down',
    'ct_promote', 'ct_put_trend_min', 'ct_call_trend_max',
    'ctsl_enabled', 'ctsl_call_trend_max', 'ctsl_call_target', 'ctsl_call_alpha', 'ctsl_call_trend_power',
    'ctsl_call_tier_floor', 'ctsl_call_score_norm_weight', 'ctsl_call_score_norm_power',
    'ctsl_put_trend_min', 'ctsl_put_target', 'ctsl_put_alpha', 'ctsl_put_trend_power', 'ctsl_put_tier_ceiling',
    'ctsl_put_score_norm_weight', 'ctsl_put_score_norm_power',
    'saw_put_ucurve_enabled', 'saw_put_ucurve_shape', 'saw_put_ucurve_midpoint', 'saw_put_ucurve_halfwidth',
    'saw_put_ucurve_floor', 'saw_put_ucurve_ceil', 'saw_put_ucurve_power', 'saw_put_ucurve_k',
    'dte_router_enabled', 'dte_router_score_min', 'dte_router_trend_lt', 'dte_router_vix_min', 'dte_router_vix_max',
    'dte_router_day_cap', 'dte_router_alloc_score_cap',
    'dead_hold_enabled', 'dead_hold_trigger_pnl', 'dead_hold_popout_pnl',
})

# Slippage knobs: applied in /api/backtest/run barrier math (net_tp/net_sl/net_hard
# recompute), not via cfg.get or the global override.
ENDPOINT_SLIP_KEYS: frozenset = frozenset({'slip_entry', 'slip_tp', 'slip_sl', 'slip_hard'})


def cfg_targets(p: Param) -> tuple:
    """The cfg key(s) a Param writes — for editability + the backend applier."""
    if p.transform in _TRANSFORM_CFG_KEYS:
        return _TRANSFORM_CFG_KEYS[p.transform]
    if p.transform.startswith(("tier:", "puttier:")):
        return ("tier_alloc",)
    if p.cfg_key:
        return (p.cfg_key,)
    return ()


def is_editable(p: Param) -> bool:
    """True iff the Param is overridable per-request: its cfg target is read by
    run_backtest (ENGINE_CFG_KEYS), OR it is applied via the module-global override
    (ENGINE_GLOBAL_KEYS), OR it is a slippage knob applied in the endpoint barrier
    math (ENDPOINT_SLIP_KEYS). Otherwise display-only (applies from config)."""
    tg = cfg_targets(p)
    if tg and all(k in ENGINE_CFG_KEYS for k in tg):
        return True
    return bool(p.cfg_key) and (p.cfg_key in ENGINE_GLOBAL_KEYS or p.cfg_key in ENDPOINT_SLIP_KEYS)


# ── Helpers ───────────────────────────────────────────────────────────────────
# Map a surfaced Param.key back to the strategy_config field it is sourced from,
# so coverage checking is exact (a key may source a dict member, e.g. tier.ultra).
def _src_field(p: Param) -> str | None:
    """Top-level strategy_config field name a Param draws its default from."""
    if not p.src:
        return None
    kind, _, rest = p.src.partition(".")
    return {
        "opt": rest,                 # OptionStrategyConfig.<rest>
        "dte": rest,                 # DteStrategyConfig.<rest>
        "tier": "TIER_ALLOC",        # dict member
        "puttier": "PUT_TIER_ALLOC",
    }.get(kind)


def surfaced_config_fields() -> set[str]:
    out: set[str] = set()
    for p in PARAMS:
        f = _src_field(p)
        if f:
            out.add(f)
    return out


def params_for_dte(dte: str) -> list[Param]:
    return [p for p in PARAMS if p.dte in ("both", str(dte))]


def seed_default(p: Param, cfg_obj) -> object:
    """Live default for a Param read from a strategy-config-like object
    (STRATEGY_30DTE, or a profiled view exposing the same attrs + .option +
    TIER_ALLOC / PUT_TIER_ALLOC). Returns the display value (raw * scale, abs'd)."""
    if not p.src:
        return None
    kind, _, rest = p.src.partition(".")
    try:
        if kind == "opt":
            raw = getattr(cfg_obj.option, rest)
        elif kind == "dte":
            raw = getattr(cfg_obj, rest)
        elif kind == "tier":
            raw = cfg_obj.TIER_ALLOC.get(rest)
        elif kind == "puttier":
            raw = cfg_obj.PUT_TIER_ALLOC.get(rest)
        else:
            return None
    except Exception:
        return None
    if raw is None:
        return None
    if p.kind == "bool":
        return bool(raw)
    if p.kind == "choice":
        return str(raw)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return raw
    if p.absval:
        val = abs(val)
    val = val * p.scale
    return int(round(val)) if p.kind == "int" else round(val, 6)


def manifest_payload(cfg_obj, dte: str) -> list[dict]:
    """JSON-able manifest for the API: ordered groups, each with its params
    (UI metadata + editable flag + live default seeded from cfg_obj)."""
    order: list[str] = []
    by_group: dict[str, list[dict]] = {}
    for p in params_for_dte(dte):
        if p.group not in by_group:
            by_group[p.group] = []
            order.append(p.group)
        by_group[p.group].append({
            "key": p.key,
            "label": p.label,
            "kind": p.kind,
            "unit": p.unit,
            "min": p.minv,
            "max": p.maxv,
            "step": p.step,
            "choices": list(p.choices),
            "editable": is_editable(p),
            "shared": bool(p.cfg_key) and (p.cfg_key in ENGINE_GLOBAL_KEYS or p.cfg_key in ENDPOINT_SLIP_KEYS),
            "default": seed_default(p, cfg_obj),
            "tip": p.tip,
            "dte": p.dte,
        })
    return [{"group": g, "params": by_group[g]} for g in order]


def _coerce_request(raw, kind: str):
    if raw is None:
        return None
    if kind == "bool":
        return raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
    if kind == "choice":
        return str(raw)
    if kind == "int":
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def request_cfg_overrides(getter, dte: str) -> dict:
    """{cfg_key: value} (+ a 'tier_alloc' sub-dict of display_key->fraction) for the
    editable params the request EXPLICITLY sets. Applied by /api/backtest/run AFTER
    profile_config_overrides so a user edit WINS over the profile default (otherwise
    the profile clobbers gross_premium_cap, the allocations, opp-sat, max-positions,
    dd-soft, etc.). Excludes the barrier params (tp/sl — computed pre-profile in the
    endpoint, where the σ + slip math lives) and slip knobs (folded into the barriers
    there). Explicit-only → a default run (or one whose fields equal the profile) is
    byte-identical. Covers BOTH the cfg-read knobs and the module-global knobs."""
    out: dict = {}
    tier: dict = {}
    for p in params_for_dte(dte):
        if not is_editable(p):
            continue
        if p.transform in _TRANSFORM_CFG_KEYS:      # call_tp/put_sl/... handled pre-profile
            continue
        if p.cfg_key in ENDPOINT_SLIP_KEYS:          # slip folded into the barriers
            continue
        raw = getter(p.key)
        if raw is None or raw == "":
            continue
        v = _coerce_request(raw, p.kind)
        if v is None:
            continue
        if p.transform.startswith("tier:"):
            tier[p.transform.split(":", 1)[1]] = float(v) / 100.0
        elif p.transform == "pct":
            out[p.cfg_key] = float(v) / 100.0
        elif p.cfg_key:
            out[p.cfg_key] = v
    if tier:
        out["tier_alloc"] = tier
    return out
