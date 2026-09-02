#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Replenishing-bankroll LADDER Monte Carlo  (CHARTER.md S2 engine)
================================================================
A small account ($2,000 start) receiving +$2,000 every month, trading the v74
75+ CALL signals with INTEGER option contracts. Question: how many months until
it reaches $20,000, and does that beat simply saving the money?

THIS IS NOT A FORK OF monte_carlo.py's PHYSICS.
-----------------------------------------------
Everything about *what a trade does* is imported from monte_carlo:
    load_signals / load_price_history / load_breadth_map     (data)
    compute_trade_outcome via precompute_outcomes            (barrier detection)
    resolve(outcome, rng)                                    (seeded bounded fill P&L)
    _prepare_window                                          (the whole ctx build)
monte_carlo.py is treated as READ-ONLY (trap #3: a mid-edit module makes Windows
MP workers die on re-import and pool.map blocks forever).

What IS new here is the CAPITAL LOOP (CHARTER section 4):
    * integer contracts (1 contract = 100 shares), affordability-aware, with
      cascade-down to the next affordable signal when a candidate is unbuyable
    * a monthly +$2,000 contribution stream with a pause rule at a stage ceiling
    * a cheap-end DOLLAR execution overlay (spread floor + per-contract fees)
    * NON-ABSORBING ruin: equity ~ 0 costs one month of waiting, not the run
    * stop-at-checkpoint accounting: months-to-$20k, censored at the horizon
    * the SAVINGS NULL computed exactly, in the same loop, on the same calendar,
      under the identical contribution + pause schedule (fairness symmetry)

OBJECTIVE (locked, CHARTER section 1 -- do not redesign):
    primary   = distribution of months-to-$20,000 + P(reach within T)
    null      = the savings stream (same schedule, zero trading), computed NOT
                hardcoded
    FUNDABLE  = median months <= 0.7 x null months AND P(beat null) >= 60%
    ruin      = a PRICED INPUT (months burned), not a zero floor
    DD        = a diagnostic, never the primary (deliberate inversion of the
                main book -- collapse=0 is NOT imported)

MODES
-----
  --selftest   fully offline synthetic-outcome assertions (no DB, no MC import)
  --mode validate  degenerate arm: big capital, contributions off, floor 0,
                   fees 0, flat sizing -> must reproduce monte_carlo's own
                   run_single_sim on the same ctx and the same seeds
  --mode single    one config, pooled monthly-roll starts, full metrics
  --mode screen    a list of configs from --config JSON, same pooling

WINDOWS / START SAMPLING
------------------------
Pooled MONTHLY-ROLL starts (the concentration_2x pattern): every month-start in
the available history is a separate run and the results pool, so the metric
means "starting at a RANDOM time, how long", not "starting in 2024, how long".
Starts inside 2020_crash and 2022 are deliberately included -- the ugly tapes
are where ruin gets priced -- and results are reported pooled AND split by
start-regime {2020_crash, 2022, bull, all}.

SWEEP AXES EXPOSED (CHARTER section 6 S3)
  dte {15, 30} primary + {7} probe | n_slots {1,2,3} | slot_frac {0.33,0.50,1.00}
  tp {0.30,0.50,1.00} | sl {-0.50,-0.70,dead-hold-analog} | spread_floor {0.03,0.05,0.10}

EXCLUDED BY PRIOR RESULT (documented, NOT implemented -- see DESIGN.md):
  * OTM strikes  -- tested 2026-07-20 (experiments/bankroll_ladder/otm_replay/),
                    PARKED: in-sample time-to-tier cut, out-of-sample collapse
                    including on a clean walk-forward. ATM only.
  * 45-DTE       -- the DTE axis above 30 was closed permanently 2026-07-14
                    (experiments/apex_dte_dd/DTE45_VERDICT.md).

DTE SCALING is NOT re-implemented here: it rides monte_carlo's own
CALENDAR_HOLD + NOMINAL_CAL_DTE mechanism (premium ~ sqrt(DTE/30), sigma
barriers scale identically) set via env BEFORE the monte_carlo import.

ASCII-ONLY OUTPUT. Run with PYTHONIOENCODING=utf-8 PYTHONUTF8=1 PYTHONHASHSEED=0.

CLI
---
  python experiments/bankroll_ladder/ladder_mc.py --selftest
  python experiments/bankroll_ladder/ladder_mc.py --mode validate [--window 2022]
  python experiments/bankroll_ladder/ladder_mc.py --mode single --config cfg.json \
      --n-iter 50 --workers 8 --out results/single.json
  python experiments/bankroll_ladder/ladder_mc.py --mode screen --config grid.json ...

DO NOT self-submit to the task queue. The caller submits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# Trap: a global PYTHONPATH can make a worktree import MAIN's code. Pin the
# repo this file actually lives in to the FRONT of sys.path.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DAYS_PER_MONTH = 30.4375   # 365.25 / 12 -- calendar months, used for the
                           # continuous months metric on both arms alike


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class LadderConfig:
    """One ladder cell. Every field is a swept axis or a pinned modeling choice."""
    name: str = 'cell'

    # --- sweep axes (CHARTER S3) -------------------------------------------
    dte: int = 30                  # {15, 30} primary, {7} probe. 45+ EXCLUDED.
    n_slots: int = 2               # {1, 2, 3}
    slot_frac: float = 0.50        # {0.33, 0.50, 1.00} of equity per slot
    tp: float = 0.30               # {+0.30, +0.50, +1.00}
    sl: float = -0.70              # {-0.50, -0.70}; or sl_dead_hold=True
    sl_dead_hold: bool = False     # the "dead-hold-analog" SL arm
    spread_floor: float = 0.05     # {0.03, 0.05, 0.10} dollars PER SHARE

    # --- cheap-end execution model (CHARTER 4.3) ---------------------------
    pct_spread: float = 0.03       # FULL spread as a fraction of premium.
                                   # House canon: forced exits pay HALF
                                   # (SLIP_SL = -0.015 = 0.5 * 0.03).
    fee_per_contract: float = 0.65 # dollars, per contract, PER LEG
    zero_slip: bool = False        # COST-CONTROL ARM ONLY. Zeroes monte_carlo's
                                   # percentage slippage (SLIP_ENTRY/TP/SL/HARD)
                                   # via its OWN env overrides -- monte_carlo is
                                   # never edited. IMPORT-TIME: a cell with
                                   # zero_slip=True cannot share a process with
                                   # one that has it False.
                                   # NOTE: zeroing SLIP alone does NOT make a
                                   # run frictionless -- the ladder overlay then
                                   # picks up the full pct spread (that is the
                                   # no-double-count design working). For a true
                                   # frictionless arm ALSO set pct_spread=0.0,
                                   # spread_floor=0.0, fee_per_contract=0.0.

    # --- bankroll ----------------------------------------------------------
    start_capital: float = 2000.0
    contribution: float = 2000.0
    checkpoint: float = 20000.0
    stage_ceiling: Optional[float] = None   # None -> checkpoint. Contributions
                                            # PAUSE at/above this (both arms).
    contributions_on: bool = True
    horizon_months: int = 36

    # --- instrument --------------------------------------------------------
    instrument: str = 'option'     # 'option' | 'equity'
                                   # 'equity' = buy SHARES of the same 75+
                                   # signals: no premium, no theta, no expiry.
                                   # Same entry dates, same underlying sigma
                                   # barriers, same max hold. See DESIGN.md
                                   # "Equity arm" for the exact mapping.
    equity_slippage_bps: float = 5.0   # per leg, basis points of notional
                                       # (commission modelled as $0)

    # --- signal universe ---------------------------------------------------
    min_score: int = 75            # 75+ calls (charter). 70-74 overflow never
                                   # sized, but its rng draws are preserved.

    # --- engine mode -------------------------------------------------------
    proceeds_floor_zero: bool = True
                                   # True  -> a long option can never return
                                   #          NEGATIVE cash (honest for a $2k
                                   #          account; resolve() can emit
                                   #          pnl < -1.0 once SLIP is added)
                                   # False -> monte_carlo's raw
                                   #          basis*(1+pnl) (validation arm)
    integer_contracts: bool = True # False -> fractional (validation arm only)
    budget_mode: str = 'clamp'     # 'clamp'  -> slot budget is capped by cash
                                   #             (the LADDER rule: a small
                                   #             account simply buys fewer
                                   #             contracts when cash is short)
                                   # 'skip'   -> if slot_frac*equity exceeds
                                   #             cash the signal is skipped
                                   #             (monte_carlo's own rule; used
                                   #             by the validation arm)
    collapse_break: bool = False   # True -> stop a path at 20% of start
                                   # (monte_carlo's rule; ladder OFF by design)
    hold_cal_days: Optional[int] = None   # None -> scaled from dte

    def resolved_stage_ceiling(self) -> float:
        return self.checkpoint if self.stage_ceiling is None else self.stage_ceiling

    def resolved_hold_cal_days(self) -> int:
        if self.hold_cal_days is not None:
            return int(self.hold_cal_days)
        # House 30-DTE default is HOLD_CAL_DAYS=27 (a 27-calendar-day hold on a
        # 30-DTE contract). Scale proportionally, floor at 3 calendar days.
        return max(3, int(round(27.0 * self.dte / 30.0)))


def config_from_dict(d: dict) -> LadderConfig:
    known = {f for f in LadderConfig.__dataclass_fields__}
    unknown = set(d) - known
    if unknown:
        raise SystemExit(f"unknown config keys: {sorted(unknown)}")
    return LadderConfig(**d)


# ---------------------------------------------------------------------------
# Physics constants snapshot (so the pure path runner never imports monte_carlo)
# ---------------------------------------------------------------------------
@dataclass
class Physics:
    slip_entry: float = 0.0
    slip_tp: float = 0.0
    slip_sl: float = -0.015
    slip_hard: float = -0.015
    dh_pop_slip: float = 0.0
    net_hard_sell: float = -0.515
    primary_threshold: int = 75
    collapse_threshold: float = 0.20

    @staticmethod
    def from_mc(mc) -> 'Physics':
        return Physics(
            slip_entry=float(mc.SLIP_ENTRY), slip_tp=float(mc.SLIP_TP),
            slip_sl=float(mc.SLIP_SL), slip_hard=float(mc.SLIP_HARD),
            dh_pop_slip=float(mc.DH_POP_SLIP),
            net_hard_sell=float(mc.NET_HARD_SELL),
            primary_threshold=int(mc.PRIMARY_THRESHOLD),
            collapse_threshold=float(mc.COLLAPSE_THRESHOLD),
        )


# Exit kinds resolve() can return, split by whether the exit was a resting
# LIMIT (we provide liquidity -> no spread crossed) or a FORCED liquidation
# (we take liquidity -> half the spread). 'both' is path-ambiguous; treated as
# forced (the pessimistic read).
LIMIT_EXIT_KINDS = ('tp', 'dh_pop')
FORCED_EXIT_KINDS = ('sl', 'hard', 'prem', 'trail', 'both', 'dh_expiry', 'dh_open')


# ---------------------------------------------------------------------------
# Cheap-end cost model (PURE -- unit-asserted by --selftest)
# ---------------------------------------------------------------------------
def spread_cost_per_share(leg: str, premium_per_share: float,
                          pct_spread: float, floor_dollars: float) -> float:
    """Dollars of SPREAD paid per share on one leg, CHARTER 4.3:

        spread_dollars = max(PCT_SPREAD * premium_dollars, FLOOR_DOLLARS)

    with the repo's ASYMMETRIC cost canon deciding how much of the pct spread
    that leg actually crosses:

        entry  : mid fill                 -> 0.0 x pct spread   (+ floor)
        tp     : resting limit            -> 0.0 x pct spread   (+ floor)
        forced : SL / hard / expiry cut   -> 0.5 x pct spread   (+ floor)

    The FLOOR always applies on BOTH legs (it is the minimum tick you can never
    negotiate away at the cheap end) -- that is exactly what makes a $0.05
    spread on a $0.40 contract cost 12.5%.
    """
    frac = {'entry': 0.0, 'tp': 0.0, 'limit': 0.0, 'forced': 0.5}[leg]
    return max(frac * pct_spread * max(0.0, premium_per_share), max(0.0, floor_dollars))


def spread_overlay_per_share(leg: str, premium_per_share: float,
                             pct_spread: float, floor_dollars: float,
                             already_charged_frac: float) -> float:
    """The INCREMENTAL dollars this harness must charge on top of what
    monte_carlo.resolve() already took out as a percentage slippage.

    resolve() returns pnl already net of SLIP_ENTRY + SLIP_{TP|SL|HARD}, i.e.
    the house percentage spread. Charging the full CHARTER 4.3 dollar spread on
    top would DOUBLE-COUNT it. So we charge only the excess:

        overlay = max(0, target_spread_dollars - already_charged_dollars)

    With the house defaults (SLIP_SL = -0.015, pct_spread = 0.03) the forced-exit
    pct terms cancel exactly and the overlay reduces to the dollar FLOOR excess.
    With floor = 0 the overlay is identically 0 -> the validation arm is
    bit-exact against monte_carlo.
    """
    target = spread_cost_per_share(leg, premium_per_share, pct_spread, floor_dollars)
    already = abs(already_charged_frac) * max(0.0, premium_per_share)
    return max(0.0, target - already)


def resolve_equity(outcome: dict, rng, slippage_bps: float = 5.0):
    """Underlying-only resolve: buy SHARES instead of the call.

    Reuses monte_carlo's OWN barrier detection and its CALIBRATED bounded-fill
    semantics (those were fitted on real underlying bars, so they transfer to
    shares directly) -- only the option-pricing layer is dropped:

        kind 'tp'   -> a resting limit sells AT the sigma TP barrier
        kind 'sl'   -> bimodal stop: intraday trigger fills AT the barrier,
                       a gap-through open fills Uniform(low, open)
        kind 'both' -> path ambiguous -> Uniform(low, high)
        kind 'hard' -> the max-hold deadline close (no expiry -- this is just
                       "the same time stop the option arm uses")

    The TP/SL levels ARE the underlying moves the option TP/SL correspond to
    (monte_carlo derives them as TP_SIGMA/SL_SIGMA x realized vol, DTE-scaled),
    which is what makes the two arms apples-to-apples.

    Returns (kind, net underlying return) where the return is relative to the
    MID entry price and already carries the EXIT-leg slippage. The entry-leg
    slippage is charged separately at fill time.

    Draws the SAME number of rng values as monte_carlo.resolve in the
    corresponding branch, so option and equity arms on a shared seed stay
    comparable rather than drifting apart on stream alignment.
    """
    kind = outcome['kind']
    entry = float(outcome['entry'])
    bps = slippage_bps / 10_000.0
    if kind in ('hard', 'prem'):
        u_fire = float(outcome['fire_close'])
    else:
        lo = float(outcome['fire_low'])
        hi = float(outcome['fire_high'])
        op = outcome.get('fire_open')
        tp_lvl = outcome.get('fire_tp_level')
        sl_lvl = outcome.get('fire_sl_level')
        if kind == 'tp':
            u_lo = u_hi = float(tp_lvl)
        elif kind == 'sl':
            if op is not None and op > sl_lvl:
                u_lo = u_hi = float(sl_lvl)
            else:
                u_lo, u_hi = lo, (float(op) if op is not None else float(sl_lvl))
        elif kind == 'trail':
            trail_lvl = float(outcome.get('fire_trail_level') or tp_lvl)
            if op is not None and op > trail_lvl:
                u_lo = u_hi = trail_lvl
            else:
                u_lo, u_hi = lo, (float(op) if op is not None else trail_lvl)
        else:                       # 'both'
            u_lo, u_hi = lo, hi
        if u_hi < u_lo:
            u_lo, u_hi = u_hi, u_lo
        u_fire = u_lo + rng.random() * (u_hi - u_lo) if u_hi > u_lo else u_lo
    if entry <= 0:
        return kind, 0.0
    return kind, (u_fire * (1.0 - bps)) / entry - 1.0


def contract_cost_dollars(premium_per_share: float, cfg: LadderConfig) -> float:
    """All-in cost of ONE contract at entry: 100 x premium + entry spread
    overlay + the per-contract fee. This is the number affordability is tested
    against (CHARTER 4.1)."""
    entry_overlay = spread_overlay_per_share(
        'entry', premium_per_share, cfg.pct_spread, cfg.spread_floor, 0.0)
    return 100.0 * (premium_per_share + entry_overlay) + cfg.fee_per_contract


# ---------------------------------------------------------------------------
# Savings null (PURE -- unit-asserted by --selftest)
# ---------------------------------------------------------------------------
def savings_null_on_calendar(days: list, start_capital: float, contribution: float,
                            checkpoint: float, stage_ceiling: float,
                            contributions_on: bool = True):
    """The savings null evaluated on THIS path's actual trading calendar.

    Computed OUTSIDE the trading loop on purpose: the traded arm stops the
    moment it hits the checkpoint, and tracking the null inline would then
    censor it exactly when the strategy wins -- which silently turns "I beat
    savings by 1 month" into "savings never got there". Same contribution
    schedule, same pause rule, same month boundaries.

    Returns (day_index, month_index) or (None, None) if the calendar runs out.
    """
    eq = float(start_capital)
    cur = (days[0].year, days[0].month)
    m = 0
    for i, d in enumerate(days):
        if (d.year, d.month) != cur:
            cur = (d.year, d.month)
            m += 1
            if contributions_on and eq < stage_ceiling:
                eq += contribution
        if eq >= checkpoint:
            return i, m
    return None, None


def savings_null_schedule(start_capital: float, contribution: float,
                          checkpoint: float, stage_ceiling: float,
                          horizon_months: int,
                          contributions_on: bool = True) -> Optional[int]:
    """Months (contribution-boundary index) at which a ZERO-TRADING savings
    stream first reaches `checkpoint`, or None if it never does within the
    horizon. Month 0 = the starting capital itself.

    The pause rule is IDENTICAL to the traded arm: no contribution is credited
    while equity is at/above `stage_ceiling` (CHARTER section 5, fairness
    symmetry -- pinned before any sweep).
    """
    eq = float(start_capital)
    if eq >= checkpoint:
        return 0
    for m in range(1, int(horizon_months) + 1):
        if contributions_on and eq < stage_ceiling:
            eq += contribution
        if eq >= checkpoint:
            return m
    return None


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------
def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, 1)


def monthly_starts(trading_days: list, hist_start: date, hist_end: date,
                   horizon_months: int, min_horizon_months: Optional[int] = None
                   ) -> list:
    """Every month-start across history is a separate run (concentration_2x
    monthly-roll pattern). Returns [(label, start_idx, end_idx, start_date)].

    start_idx = first trading day on/after the 1st of that month
    end_idx   = last trading day on/before start + horizon_months

    Starts whose remaining runway is shorter than min_horizon_months are
    DROPPED. Default = the full horizon: a start with only 8 months of tape
    left is censored MECHANICALLY, not because the strategy was slow, and
    mixing those in silently inflates the censoring rate and drags the median.
    """
    import bisect
    if min_horizon_months is None:
        min_horizon_months = horizon_months
    out = []
    cur = date(hist_start.year, hist_start.month, 1)
    if cur < hist_start:
        cur = add_months(cur, 1)
    guard = 0
    while cur <= hist_end and guard < 2000:
        guard += 1
        si = bisect.bisect_left(trading_days, cur)
        if si >= len(trading_days):
            break
        s_date = trading_days[si]
        e_date = min(add_months(s_date, horizon_months), hist_end)
        ei = bisect.bisect_right(trading_days, e_date) - 1
        runway_months = (trading_days[ei] - s_date).days / DAYS_PER_MONTH
        if ei > si and runway_months >= min_horizon_months - 0.2:
            out.append((f"roll_{s_date.isoformat()}", si, ei, s_date))
        cur = add_months(cur, 1)
    return out


def start_regime(d: date) -> str:
    """Start-regime bucket for the split report. 2020_crash and 2022 are
    mandatory (ruin needs the ugly tapes); everything else is 'bull'."""
    if date(2020, 2, 1) <= d <= date(2020, 4, 30):
        return '2020_crash'
    if d.year == 2022:
        return '2022'
    return 'bull'


def stable_label_seed(label: str) -> int:
    """blake2b label seed -- byte-identical to monte_carlo._stable_label_seed so
    a validation arm can be run on EXACTLY the seeds monte_carlo would use."""
    digest = hashlib.blake2b(str(label).encode('utf-8'), digest_size=8).digest()
    return int.from_bytes(digest, 'big')


def path_seed(label: str, it: int) -> int:
    """Seed depends ONLY on (window label, iteration index) -- never on the cell
    params -- so arms are PAIRED and an A/B delta is not seed noise."""
    return 1000 * stable_label_seed(label) + it


# ---------------------------------------------------------------------------
# THE LADDER CAPITAL LOOP
# ---------------------------------------------------------------------------
def run_ladder_path(cfg: LadderConfig, phys: Physics,
                    days: list, calls_by_date: dict, call_outcomes: dict,
                    resolve_fn: Callable, seed: int,
                    tape: Optional[list] = None) -> dict:
    """One path: one start date, one seed.

    Day order mirrors monte_carlo.run_single_sim so the two are comparable:
        1. settle every position whose exit bar has arrived
        2. (LADDER) credit the monthly contribution at a month boundary
        3. mark equity, update peak/DD, checkpoint test, (optional) collapse
        4. rank the day's candidates and fill slots

    Returns a per-path record (all diagnostics included).
    """
    rng = random.Random(seed)
    ceiling = cfg.resolved_stage_ceiling()

    cash = float(cfg.start_capital)
    contributed = float(cfg.start_capital)
    positions: list[dict] = []
    open_syms: set = set()

    # savings null -- SAME schedule, SAME calendar, computed (never hardcoded)
    null_reached_idx, null_reached_month = savings_null_on_calendar(
        days, cfg.start_capital, cfg.contribution, cfg.checkpoint, ceiling,
        cfg.contributions_on)

    start_date = days[0]
    peak = float(cfg.start_capital)
    max_dd = 0.0

    month_idx = 0
    cur_month = (start_date.year, start_date.month)
    fills_this_month = 0
    skips_this_month = 0
    months_burned = 0
    months_below_tranche = 0     # months entered with equity < one tranche
    stall_days = 0

    n_fills = 0
    n_contracts_total = 0
    premium_deployed = 0.0      # lifetime premium turned over (sum of entry basis)
    n_unaffordable = 0          # candidate considered, n_contracts == 0
    n_candidates = 0
    n_tp = n_sl = n_hard = n_other = 0
    fees_paid = 0.0
    spread_overlay_paid = 0.0
    slip_paid = 0.0

    reached_idx = None
    reached_month = None
    collapsed = False

    day_to_idx = {d: i for i, d in enumerate(days)}
    cap_slots = int(cfg.n_slots)

    for i, today in enumerate(days):
        # ---- 1. settle expiring positions --------------------------------
        keep = []
        for p in positions:
            if p['entry_idx'] + p['exit_bar'] <= i:
                cash += _exit_proceeds(p, cfg, phys)
                fees_paid += p['exit_fees']
                spread_overlay_paid += p['exit_overlay']
                slip_paid += p.get('slip_dollars', 0.0)
                k = p['kind']
                if k == 'tp':
                    n_tp += 1
                elif k == 'sl':
                    n_sl += 1
                elif k in ('hard', 'dh_expiry', 'dh_open'):
                    n_hard += 1
                else:
                    n_other += 1
                open_syms.discard(p['sym'])
            else:
                keep.append(p)
        positions = keep

        # ---- 2. month boundary -> contribution (BOTH arms) ---------------
        if (today.year, today.month) != cur_month:
            cur_month = (today.year, today.month)
            month_idx += 1
            equity_pre = cash + sum(p['basis'] for p in positions)
            # tranche-ruin bookkeeping: a month in which the account could not
            # buy anything it wanted is a MONTH BURNED (non-absorbing ruin --
            # the cost of going to ~0 is exactly one month of waiting).
            if fills_this_month == 0 and skips_this_month > 0:
                months_burned += 1
            # Direct tranche-ruin read (CHARTER section 1 "priced input"): the
            # month opened with less equity than a single $2k tranche, i.e. the
            # previous tranche(s) were effectively consumed.
            if cfg.contribution > 0 and equity_pre < cfg.contribution:
                months_below_tranche += 1
            fills_this_month = 0
            skips_this_month = 0
            if cfg.contributions_on:
                if equity_pre < ceiling:
                    cash += cfg.contribution
                    contributed += cfg.contribution

        # ---- 3. equity / DD / checkpoint ---------------------------------
        equity = cash + sum(p['basis'] for p in positions)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        if reached_idx is None and equity >= cfg.checkpoint:
            reached_idx = i
            reached_month = month_idx
            break                      # stop-at-checkpoint discipline
        if cfg.collapse_break and equity <= cfg.start_capital * phys.collapse_threshold:
            collapsed = True
            break

        # ---- 4. entries ---------------------------------------------------
        # The allocation base is FIXED at the day-start equity for every fill
        # this day -- exactly monte_carlo's `allocation_base = portfolio_value`.
        # Re-deriving it after each fill is algebraically identical but NOT
        # bit-identical (cash + sum(basis) re-rounds), which flips the boundary
        # case where a fill exactly exhausts cash. That single ulp silently
        # dropped trades monte_carlo takes.
        alloc_base = equity
        day_calls = calls_by_date.get(today, ())
        if day_calls:
            eligible = [e for e in day_calls
                        if e[2] in call_outcomes and e[0] not in open_syms]
            # Mirror monte_carlo._do_calls EXACTLY (including the rng draws in
            # the sort keys) so the validation arm is bit-exact. The
            # primary/overflow split is NOT redundant with one conviction sort:
            # a ct_call fills ahead of a plain 95+.
            primary = [e for e in eligible
                       if e[1] >= phys.primary_threshold or e[3] == 'ct_call']
            overflow = [e for e in eligible
                        if e[1] < phys.primary_threshold and e[3] != 'ct_call']
            primary.sort(key=lambda x: (0 if x[3] == 'ct_call' else 1, -x[1], rng.random()))
            overflow.sort(key=lambda x: (-x[1], rng.random()))

            for sym_id, score, key, ct, _ern in primary + overflow:
                if len(positions) >= cap_slots:
                    break
                # 70-74 overflow is never sized in the ladder (75+ book); its
                # rng draws above are still consumed, matching monte_carlo with
                # TIER_OVERFLOW_OV=0.
                if score < phys.primary_threshold and ct != 'ct_call':
                    continue
                if score < cfg.min_score and ct != 'ct_call':
                    continue
                n_candidates += 1
                o = call_outcomes[key]
                is_equity = cfg.instrument == 'equity'
                if is_equity:
                    # EQUITY ARM: the "unit" is one SHARE, not one 100-share
                    # contract. No premium, no theta, no expiry -- only the
                    # underlying move. Slippage is bps of notional, commission $0.
                    prem_share = float(o['entry'])
                    unit = 1.0
                    cost_1 = prem_share * (1.0 + cfg.equity_slippage_bps / 10_000.0)
                else:
                    prem_share = float(o['premium_pct']) * float(o['entry'])
                    unit = 100.0
                    cost_1 = contract_cost_dollars(prem_share, cfg)
                if prem_share <= 0:
                    continue
                budget = cfg.slot_frac * alloc_base
                if cfg.budget_mode == 'clamp':
                    budget = min(budget, cash)
                elif budget > cash:
                    # monte_carlo semantics: an unaffordable slot is skipped
                    # outright, never sized down.
                    n_unaffordable += 1
                    skips_this_month += 1
                    continue
                if cost_1 <= 0 or budget <= 0:
                    n_unaffordable += 1
                    skips_this_month += 1
                    continue
                if cfg.integer_contracts:
                    n_ct = int(math.floor(budget / cost_1))
                    if n_ct < 1:
                        # UNAFFORDABLE -> cascade down to the next-best signal
                        n_unaffordable += 1
                        skips_this_month += 1
                        continue
                    basis = n_ct * unit * prem_share
                else:
                    # Fractional sizing (validation arm only): the premium
                    # outlay IS monte_carlo's `allocation_base * alloc_frac`.
                    # It must be assigned DIRECTLY -- computing it as
                    # (budget/(100*prem))*100*prem can land 1 ulp ABOVE budget
                    # and then fail the `> cash` test on the fill that exactly
                    # exhausts cash, silently dropping a trade monte_carlo takes.
                    n_ct = budget / (unit * prem_share)
                    basis = budget
                    if n_ct <= 0:
                        n_unaffordable += 1
                        skips_this_month += 1
                        continue

                if is_equity:
                    entry_extra = n_ct * prem_share * (cfg.equity_slippage_bps / 10_000.0)
                else:
                    entry_overlay = spread_overlay_per_share(
                        'entry', prem_share, cfg.pct_spread, cfg.spread_floor,
                        phys.slip_entry)
                    entry_extra = n_ct * (100.0 * entry_overlay + cfg.fee_per_contract)
                if basis + entry_extra > cash or basis <= 0:
                    n_unaffordable += 1
                    skips_this_month += 1
                    continue

                kind, pnl = resolve_fn(o, rng)
                cash -= (basis + entry_extra)
                if is_equity:
                    spread_overlay_paid += entry_extra
                else:
                    fees_paid += n_ct * cfg.fee_per_contract
                    spread_overlay_paid += n_ct * 100.0 * entry_overlay
                positions.append(dict(
                    sym=sym_id, entry_idx=i, exit_bar=int(o['exit_bar']),
                    n=n_ct, basis=basis, prem_share=prem_share,
                    pnl=pnl, kind=kind, exit_fees=0.0, exit_overlay=0.0,
                    slip_dollars=0.0,
                ))
                if tape is not None:
                    tape.append((today.isoformat(), sym_id, score,
                                 round(basis, 6), round(pnl, 9), kind,
                                 days[min(i + int(o['exit_bar']),
                                          len(days) - 1)].isoformat()))
                open_syms.add(sym_id)
                n_fills += 1
                n_contracts_total += n_ct
                premium_deployed += basis
                fills_this_month += 1

        if not positions and n_candidates and skips_this_month:
            stall_days += 1

    # ---- horizon reached: liquidate whatever is open (forced) -------------
    # NOTE: monte_carlo.run_single_sim liquidates the open book even when it
    # broke early on the collapse rule, so this must not be gated on `collapsed`
    # (that mismatch is a real bit-exactness trap).
    if reached_idx is None:
        for p in positions:
            p = dict(p)
            if cfg.instrument != 'equity':
                # options: monte_carlo's forced day-15 hard-sell mark
                p['pnl'] = phys.net_hard_sell
                p['kind'] = 'hard'
            # equity: no expiry -- settle the still-open share position at its
            # already-resolved barrier/deadline return (exit slippage included)
            else:
                p['kind'] = 'hard'
            cash += _exit_proceeds(p, cfg, phys)
            fees_paid += p['exit_fees']
            spread_overlay_paid += p['exit_overlay']
            slip_paid += p.get('slip_dollars', 0.0)
            n_hard += 1
        positions = []
        equity = cash
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    else:
        equity = cash + sum(p['basis'] for p in positions)

    n_months_run = (days[min(reached_idx, len(days) - 1)] - start_date).days / DAYS_PER_MONTH \
        if reached_idx is not None else (days[-1] - start_date).days / DAYS_PER_MONTH

    months_to_cp = None
    if reached_idx is not None:
        months_to_cp = (days[reached_idx] - start_date).days / DAYS_PER_MONTH

    null_months = None
    if null_reached_idx is not None:
        null_months = (days[null_reached_idx] - start_date).days / DAYS_PER_MONTH

    return dict(
        seed=seed,
        start_date=start_date.isoformat(),
        regime=start_regime(start_date),
        reached=reached_idx is not None,
        months_to_checkpoint=months_to_cp,
        month_index_to_checkpoint=reached_month,
        null_reached=null_reached_idx is not None,
        null_months=null_months,
        null_month_index=null_reached_month,
        beat_null=_beat_null(months_to_cp, null_months),
        final_equity=equity,
        contributed=contributed,
        net_of_contributions=equity - contributed,
        max_dd=max_dd,
        months_burned=months_burned,
        months_below_tranche=months_below_tranche,
        month_boundaries=month_idx,
        stall_days=stall_days,
        months_run=n_months_run,
        n_fills=n_fills,
        n_contracts=n_contracts_total,
        premium_deployed=premium_deployed,
        n_unaffordable=n_unaffordable,
        n_candidates=n_candidates,
        tp=n_tp, sl=n_sl, hard=n_hard, other=n_other,
        fees_paid=fees_paid,
        spread_overlay_paid=spread_overlay_paid,
        slip_paid=slip_paid,
        total_cost=fees_paid + spread_overlay_paid + slip_paid,
        collapsed=collapsed,
    )


def _beat_null(strat_months, null_months) -> bool:
    """Beating the null = reaching the checkpoint STRICTLY sooner than the
    savings stream would have. Censored strategy never beats; a strategy that
    reaches while the null is censored always beats."""
    if strat_months is None:
        return False
    if null_months is None:
        return True
    return strat_months < null_months


def _exit_proceeds(p: dict, cfg: LadderConfig, phys: Physics) -> float:
    """Cash returned by closing one position, net of the cheap-end DOLLAR exit
    overlay + fees. Mutates p['exit_fees'] / p['exit_overlay'] for accounting.

    The percentage asymmetric slippage is ALREADY inside p['pnl'] (resolve()
    applied SLIP_ENTRY + SLIP_{TP|SL|HARD}); only the incremental dollar floor
    and the per-contract fee are added here.
    """
    gross = p['basis'] * (1.0 + p['pnl'])
    if cfg.proceeds_floor_zero:
        gross = max(0.0, gross)
    if cfg.instrument == 'equity':
        # Shares: no option spread overlay, no per-contract fee. The bps
        # slippage is already inside pnl (exit leg) and was charged at entry,
        # so it is reported under spread_overlay, not slip.
        p['exit_fees'] = 0.0
        p['exit_overlay'] = 0.0
        p['slip_dollars'] = 0.0
        return gross
    exit_prem_share = p['prem_share'] * (1.0 + p['pnl'])
    # A contract worth under a cent is not sold -- it expires. No exit costs.
    if exit_prem_share * 100.0 < 1.0:
        p['exit_fees'] = 0.0
        p['exit_overlay'] = 0.0
        _slip = phys.slip_hard if p['kind'] in ('hard', 'dh_expiry', 'dh_open')             else (phys.slip_tp if p['kind'] in LIMIT_EXIT_KINDS else phys.slip_sl)
        p['slip_dollars'] = p['basis'] * (abs(phys.slip_entry) + abs(_slip))
        return gross
    if p['kind'] in LIMIT_EXIT_KINDS:
        leg = 'limit'
        already = phys.dh_pop_slip if p['kind'] == 'dh_pop' else phys.slip_tp
    else:
        leg = 'forced'
        already = phys.slip_hard if p['kind'] in ('hard', 'dh_expiry', 'dh_open') \
            else phys.slip_sl
    overlay = spread_overlay_per_share(leg, p['prem_share'], cfg.pct_spread,
                                       cfg.spread_floor, already)
    # Dollars monte_carlo's PERCENTAGE slippage already removed inside pnl
    # (SLIP_ENTRY on the entry leg + SLIP_{TP|SL|HARD} on the exit leg). Tracked
    # so a cost decomposition can attribute total friction across all three
    # layers. ('both' is attributed to the forced leg -- ~0.3% of outcomes.)
    p['slip_dollars'] = p['basis'] * (abs(phys.slip_entry) + abs(already))
    fees = p['n'] * cfg.fee_per_contract
    ov_dollars = p['n'] * 100.0 * overlay
    p['exit_fees'] = fees
    p['exit_overlay'] = ov_dollars
    net = gross - fees - ov_dollars
    return max(0.0, net) if cfg.proceeds_floor_zero else net


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def peak_rss_gb() -> Optional[float]:
    """Peak RSS of this process TREE (parent + live MP workers), in GB.
    Used to size how many barrier-group processes can run concurrently --
    each worker holds a pickled copy of the window ctx."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        me = psutil.Process()
        total = me.memory_info().rss
        for c in me.children(recursive=True):
            try:
                total += c.memory_info().rss
            except Exception:
                pass
        return total / (1024.0 ** 3)
    except Exception:
        return None


def _quantile(sorted_vals: list, q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


T_CURVE_MONTHS = (6, 9, 12, 18, 24, 36)


def aggregate(paths: list, cfg: LadderConfig) -> dict:
    n = len(paths)
    if n == 0:
        return dict(n_paths=0)
    reach = [p for p in paths if p['reached']]
    m = sorted(p['months_to_checkpoint'] for p in reach)
    nulls = sorted(p['null_months'] for p in paths if p['null_months'] is not None)
    med = _quantile(m, 0.50)
    med_null = _quantile(nulls, 0.50)
    p_beat = sum(1 for p in paths if p['beat_null']) / n
    ratio = (med / med_null) if (med is not None and med_null) else None
    out = dict(
        cell=cfg.name,
        n_paths=n,
        p_reach=len(reach) / n,
        median_months=med,
        p25_months=_quantile(m, 0.25),
        p75_months=_quantile(m, 0.75),
        median_null_months=med_null,
        median_ratio_vs_null=ratio,
        p_beat_null=p_beat,
        # CHARTER section 1 FUNDABLE bar -- pre-registered, do not relax here.
        # BOTH bars must hold; they are reported SEPARATELY so a cell that
        # passes one and fails the other is never read as a near-miss.
        bar_speed_pass=bool(ratio is not None and ratio <= 0.70),
        bar_beat_pass=bool(p_beat >= 0.60),
        fundable=bool(ratio is not None and ratio <= 0.70 and p_beat >= 0.60),
        ruin_rate=sum(1 for p in paths if p['months_burned'] >= 1) / n,
        mean_months_burned=statistics.mean(p['months_burned'] for p in paths),
        max_months_burned=max(p['months_burned'] for p in paths),
        mean_stall_days=statistics.mean(p['stall_days'] for p in paths),
        tranche_loss_rate=(sum(p['months_below_tranche'] for p in paths)
                           / max(1, sum(p['month_boundaries'] for p in paths))),
        p_any_tranche_loss=sum(1 for p in paths
                               if p['months_below_tranche'] >= 1) / n,
        # diagnostics (DD is a DIAGNOSTIC here, never the primary)
        median_max_dd=_quantile(sorted(p['max_dd'] for p in paths), 0.50),
        worst_dd=max(p['max_dd'] for p in paths),
        median_final_equity=_quantile(sorted(p['final_equity'] for p in paths), 0.50),
        median_net_of_contributions=_quantile(
            sorted(p['net_of_contributions'] for p in paths), 0.50),
        mean_fills=statistics.mean(p['n_fills'] for p in paths),
        mean_contracts=statistics.mean(p['n_contracts'] for p in paths),
        unaffordable_skip_rate=(
            sum(p['n_unaffordable'] for p in paths)
            / max(1, sum(p['n_candidates'] for p in paths))),
        mean_fees_paid=statistics.mean(p['fees_paid'] for p in paths),
        mean_spread_overlay_paid=statistics.mean(p['spread_overlay_paid'] for p in paths),
        mean_slip_paid=statistics.mean(p['slip_paid'] for p in paths),
        mean_total_cost=statistics.mean(p['total_cost'] for p in paths),
        mean_premium_deployed=statistics.mean(p['premium_deployed'] for p in paths),
        mean_contributed=statistics.mean(p['contributed'] for p in paths),
        cost_pct_of_premium=(sum(p['total_cost'] for p in paths)
                             / max(1e-9, sum(p['premium_deployed'] for p in paths))),
        cost_pct_of_contributions=(sum(p['total_cost'] for p in paths)
                                   / max(1e-9, sum(p['contributed'] for p in paths))),
        median_total_cost=_quantile(sorted(p['total_cost'] for p in paths), 0.50),
    )
    for T in T_CURVE_MONTHS:
        out[f'p_reach_by_{T}m'] = sum(
            1 for p in paths
            if p['reached'] and p['months_to_checkpoint'] is not None
            and p['months_to_checkpoint'] <= T) / n
    tot = sum(p['tp'] + p['sl'] + p['hard'] + p['other'] for p in paths) or 1
    out['tp_pct'] = sum(p['tp'] for p in paths) / tot * 100.0
    out['sl_pct'] = sum(p['sl'] for p in paths) / tot * 100.0
    out['hard_pct'] = sum(p['hard'] for p in paths) / tot * 100.0
    return out


def aggregate_by_regime(paths: list, cfg: LadderConfig) -> dict:
    out = {'all': aggregate(paths, cfg)}
    for reg in ('2020_crash', '2022', 'bull'):
        sub = [p for p in paths if p['regime'] == reg]
        if sub:
            out[reg] = aggregate(sub, cfg)
    return out


# ---------------------------------------------------------------------------
# Environment bootstrap -- MUST run BEFORE `import monte_carlo`
# ---------------------------------------------------------------------------
def bootstrap_env(cfg: LadderConfig, quiet: bool = False) -> dict:
    """monte_carlo reads its config as MODULE-LEVEL constants at import time
    (trap #4). Every knob this harness varies is therefore set in os.environ
    BEFORE the import; nothing in monte_carlo.py is ever edited.

    Note the DTE knob rides monte_carlo's OWN CALENDAR_HOLD + NOMINAL_CAL_DTE
    mechanism (premium ~ sqrt(DTE/30) and the sigma barriers scale identically
    inside compute_trade_outcome) -- no DTE math is written here.
    """
    env = {
        'PYTHONHASHSEED': '0',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUTF8': '1',
        'MC_NO_DB_PERSIST': '1',
        # ---- DTE ----
        'CALENDAR_HOLD': '1',
        'NOMINAL_CAL_DTE': str(int(cfg.dte)),
        'HOLD_CAL_DAYS': str(cfg.resolved_hold_cal_days()),
        # ---- barriers ----
        'TP_BASE_OV': f'{cfg.tp:.6f}',
        'TP_STRESS_OV': f'{cfg.tp:.6f}',
        'SL_BASE_OV': f'{cfg.sl:.6f}',
        'SL_STRESS_OV': f'{cfg.sl:.6f}',
        # ---- calls-only ladder: no puts anywhere ----
        'MAX_POSITIONS_PUT': '0',
        'PUT_TIER_TOP_OV': '0.0',
        'PUT_TIER_MID_OV': '0.0',
        'PUT_TIER_LOW_OV': '0.0',
    }
    if cfg.zero_slip:
        # monte_carlo exposes these as first-class env overrides; nothing in
        # monte_carlo.py is edited. NET_HARD_SELL is recomputed from them at
        # import, so the end-of-horizon mark follows automatically.
        env['SLIP_ENTRY_OV'] = '0.0'
        env['SLIP_TP_OV'] = '0.0'
        env['SLIP_SL_OV'] = '0.0'
        env['SLIP_HARD_OV'] = '0.0'
    if cfg.sl_dead_hold:
        # "dead-hold-analog" SL arm: the SL barrier still fires, but the
        # position is NEVER liquidated into it -- every SL fire becomes the
        # pre-computed popout-or-expiry walk. That IS the dead-hold analog.
        env['DEAD_HOLD_ENABLED'] = '1'
        env['DEAD_HOLD_TRIGGER_PNL'] = '0.0'
    for k, v in env.items():
        os.environ[k] = v
    if not quiet:
        print('[env] ' + '  '.join(f'{k}={v}' for k, v in sorted(env.items())
                                   if k not in ('PYTHONIOENCODING', 'PYTHONUTF8')))
    return env


def import_mc():
    import monte_carlo as mc
    assert Path(mc.__file__).resolve().parent == REPO, (
        f"monte_carlo imported from {mc.__file__} -- expected {REPO} "
        "(PYTHONPATH worktree trap)")
    return mc


# ---------------------------------------------------------------------------
# Data preparation (calls-only, ONE prepare for the whole history)
# ---------------------------------------------------------------------------
def prepare(mc, label: str, d_start: date, d_end: date, version=None) -> dict:
    """Build the ctx via monte_carlo._prepare_window with the PUT side stripped.

    Puts are off in v74 (MAX_POSITIONS_PUT=0, put tier allocs 0) so removing
    them is behaviourally neutral -- it only saves a DB pass and a precompute,
    and it keeps the rng stream free of the put-sort draws (which matters for
    the bit-exact validation arm).

    ONE prepare covers the whole history: call_outcomes is keyed
    (symbol_id, signal_date) and is window-independent, so every monthly-roll
    start is just a SLICE of trading_days. That is ~120x fewer precomputes than
    preparing per start.
    """
    if version is None:
        version = mc.AlgorithmVersion.get_active_scores_version()
        print(f"Algorithm version: {version.git_commit} (id={version.id})")
    _orig = mc.load_put_signals
    mc.load_put_signals = lambda *a, **k: []
    try:
        ctx = mc._prepare_window(label, d_start, d_end, version)
    finally:
        mc.load_put_signals = _orig
    ctx['puts_by_date'] = {}
    ctx['put_outcomes'] = {}
    return ctx


# ---------------------------------------------------------------------------
# Multiprocessing
# ---------------------------------------------------------------------------
_W: dict = {}


def make_resolve(cfg: LadderConfig, mc):
    """Pick the P&L engine for this arm. The OPTION arm is monte_carlo.resolve
    verbatim (never reimplemented); the EQUITY arm is the underlying-only
    resolver above."""
    if cfg.instrument == 'equity':
        bps = cfg.equity_slippage_bps
        return lambda o, rng: resolve_equity(o, rng, bps)
    return mc.resolve


def _init_worker(trading_days, calls_by_date, call_outcomes, cfg_dict, phys_dict):
    global _W
    mc = import_mc()
    cfg = config_from_dict(cfg_dict)
    _W = dict(trading_days=trading_days, calls_by_date=calls_by_date,
              call_outcomes=call_outcomes,
              cfg=cfg, phys=Physics(**phys_dict),
              resolve=make_resolve(cfg, mc))


def _path_task(args):
    label, si, ei, seed = args
    cfg = _W['cfg']
    days = _W['trading_days'][si:ei + 1]
    return run_ladder_path(cfg, _W['phys'], days, _W['calls_by_date'],
                           _W['call_outcomes'], _W['resolve'], seed)


def run_cell(mc, ctx: dict, cfg: LadderConfig, n_iter: int,
             workers: int, starts: list, verbose: bool = True) -> tuple:
    """Run one cell over all monthly-roll starts x n_iter, pooled."""
    phys = Physics.from_mc(mc)
    tasks = []
    for (label, si, ei, _sd) in starts:
        for it in range(n_iter):
            tasks.append((label, si, ei, path_seed(label, it)))
    t0 = time.time()
    if workers and workers > 1 and len(tasks) >= 8:
        import multiprocessing
        with multiprocessing.Pool(
                processes=workers, initializer=_init_worker,
                initargs=(ctx['trading_days'], dict(ctx['calls_by_date']),
                          ctx['call_outcomes'], asdict(cfg), asdict(phys))) as pool:
            paths = pool.map(_path_task, tasks,
                             chunksize=max(1, len(tasks) // (workers * 8)))
    else:
        _init_worker(ctx['trading_days'], dict(ctx['calls_by_date']),
                     ctx['call_outcomes'], asdict(cfg), asdict(phys))
        paths = [_path_task(t) for t in tasks]
    rss = peak_rss_gb()
    elapsed = time.time() - t0
    if verbose:
        print(f"  [{cfg.name}] {len(tasks)} paths in {elapsed:.1f}s "
              f"({elapsed / max(1, len(tasks)) * 1000:.2f} ms/path)"
              + (f"  rss_tree={rss:.2f}GB" if rss else ""))
    return paths, elapsed


# ---------------------------------------------------------------------------
# VALIDATION ARM -- prove the harness reproduces monte_carlo's physics
# ---------------------------------------------------------------------------
def run_validation(window: str, n_iter: int, start_capital: float,
                   n_slots: int, slot_frac: float) -> bool:
    """Degenerate arm: big starting capital (integer granularity negligible),
    contributions OFF, spread floor 0, fees 0, flat slot sizing.

    Reference = monte_carlo.run_single_sim itself, on the SAME ctx and the SAME
    seeds, with the sizing stack neutralised down to `premium_cost = equity x
    slot_frac` (every dampener is a documented multiplicative scale on
    alloc_frac; each is patched to 1.0 and RESTORED in a finally block --
    monte_carlo.py itself is never edited).

    Two arms are printed:
      A. ladder FRACTIONAL  -- must be BIT-EXACT vs run_single_sim
      B. ladder INTEGER     -- the granularity delta at large capital, which
                               must be inside Monte-Carlo noise
    """
    cfg_frac = LadderConfig(
        name='validate_fractional', n_slots=n_slots, slot_frac=slot_frac,
        spread_floor=0.0, fee_per_contract=0.0, start_capital=start_capital,
        contribution=0.0, contributions_on=False, checkpoint=float('inf'),
        stage_ceiling=float('inf'), integer_contracts=False, collapse_break=False,
        horizon_months=999, budget_mode='skip', proceeds_floor_zero=False)
    bootstrap_env(cfg_frac)
    mc = import_mc()

    wins = {w[0]: (w[1], w[2]) for w in mc.WINDOWS}
    if window not in wins:
        raise SystemExit(f"unknown window {window!r}; choose from {sorted(wins)}")
    d_start, d_end = wins[window]

    version = mc.AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit} (id={version.id})")
    ctx = prepare(mc, window, d_start, d_end, version)
    trading_days = ctx['trading_days']
    calls_by_date = ctx['calls_by_date']
    call_outcomes = ctx['call_outcomes']
    phys = Physics.from_mc(mc)

    seeds = [1000 * mc._stable_label_seed(window) + it for it in range(n_iter)]

    # ---- reference: monte_carlo.run_single_sim with a neutralised stack -----
    saved = {}
    def _patch(name, value):
        saved[name] = getattr(mc, name)
        setattr(mc, name, value)

    ref = []
    try:
        flat = float(slot_frac)
        _patch('TIER_ALLOC', {'ultra': flat, 'top': flat, 'mid': flat,
                              'low': flat, 'overflow': 0.0})
        _patch('PUT_TIER_ALLOC', {'put_top': 0.0, 'put_mid': 0.0, 'put_low': 0.0})
        _patch('MAX_POSITIONS', int(n_slots))
        _patch('MAX_POSITIONS_CALL', int(n_slots))
        _patch('MAX_POSITIONS_PUT', 0)
        _patch('STARTING_CASH', float(start_capital))
        _patch('alloc_scale_for', lambda value, is_put=False: 1.0)
        _patch('DD_SOFT_BAND_LO', 0.0)
        _patch('DD_SOFT_BAND_HI', 0.0)
        _patch('OPP_SAT_CALL_REF', 0.0)
        _patch('OPP_SAT_PUT_REF', 0.0)
        _patch('PRACTICAL_CAPITAL_CEILING', 0.0)
        _patch('GROSS_PREMIUM_CAP', 0.0)
        _patch('CALL_PREMIUM_CAP', 0.0)
        _patch('PUT_PREMIUM_CAP', 0.0)
        _patch('SVR_ENABLED', False)
        _patch('SPREAD_TILT_ENABLED', False)
        _patch('AW_ENABLED', False)
        _patch('REALLOC_STRATEGY', '')
        _patch('_rxdd_call_scale', lambda *a, **k: 1.0)
        _patch('_mwdd_call_scale', lambda *a, **k: 1.0)
        _patch('_tvdd_call_scale', lambda *a, **k: 1.0)
        _patch('_dqt_call_scale', lambda *a, **k: 1.0)
        _patch('_vxmd_call_scale', lambda *a, **k: 1.0)
        _patch('_bdiv_call_scale', lambda *a, **k: 1.0)
        _patch('_put_wave_reserve_scale', lambda *a, **k: 1.0)
        _patch('saw_put_ucurve_scale', lambda *a, **k: 1.0)
        # Collapse-break OFF on BOTH arms: the ladder's ruin is non-absorbing by
        # design, so comparing the two through monte_carlo's absorbing collapse
        # rule would test the break rule, not the capital loop.
        _patch('COLLAPSE_THRESHOLD', -1.0)

        print(f"\n[validate] reference run_single_sim  N={n_iter} "
              f"(single-process, neutralised sizing stack)")
        t0 = time.time()
        for s in seeds:
            rng = random.Random(s)
            r = mc.run_single_sim(trading_days, calls_by_date, call_outcomes,
                                  {}, {}, rng, None, None)
            ref.append(r)
        print(f"[validate] reference done in {time.time() - t0:.1f}s")
    finally:
        for k, v in saved.items():
            setattr(mc, k, v)

    # ---- arm A: ladder fractional (expect bit-exact) -----------------------
    days = trading_days
    arm_a = [run_ladder_path(cfg_frac, phys, days, calls_by_date, call_outcomes,
                             mc.resolve, s) for s in seeds]
    # ---- arm B: ladder integer contracts -----------------------------------
    cfg_int = LadderConfig(**{**asdict(cfg_frac), 'name': 'validate_integer',
                              'integer_contracts': True})
    arm_b = [run_ladder_path(cfg_int, phys, days, calls_by_date, call_outcomes,
                             mc.resolve, s) for s in seeds]

    def _stats(finals, dds):
        return (statistics.mean(finals) / start_capital - 1) * 100.0, \
               (statistics.median(finals) / start_capital - 1) * 100.0, \
               max(dds) * 100.0, statistics.mean(dds) * 100.0

    r_mean, r_med, r_wdd, r_mdd = _stats([r['final'] for r in ref],
                                         [r['max_dd'] for r in ref])
    a_mean, a_med, a_wdd, a_mdd = _stats([p['final_equity'] for p in arm_a],
                                         [p['max_dd'] for p in arm_a])
    b_mean, b_med, b_wdd, b_mdd = _stats([p['final_equity'] for p in arm_b],
                                         [p['max_dd'] for p in arm_b])

    print('\n' + '=' * 100)
    print(f"VALIDATION ARM -- window={window}  N={n_iter}  start=${start_capital:,.0f}  "
          f"slots={n_slots}  slot_frac={slot_frac:.2f}  floor=$0  fees=$0  contributions=OFF")
    print('=' * 100)
    print(f"{'arm':<34} {'MeanRet%':>14} {'MedRet%':>14} {'WorstDD%':>10} {'MeanDD%':>9}")
    print('-' * 100)
    print(f"{'monte_carlo.run_single_sim (REF)':<34} {r_mean:>14,.2f} {r_med:>14,.2f} "
          f"{r_wdd:>10.2f} {r_mdd:>9.2f}")
    print(f"{'ladder  fractional (arm A)':<34} {a_mean:>14,.2f} {a_med:>14,.2f} "
          f"{a_wdd:>10.2f} {a_mdd:>9.2f}")
    print(f"{'ladder  INTEGER contracts (arm B)':<34} {b_mean:>14,.2f} {b_med:>14,.2f} "
          f"{b_wdd:>10.2f} {b_mdd:>9.2f}")
    print('-' * 100)

    # per-seed exactness
    n_exact = sum(1 for r, p in zip(ref, arm_a)
                  if abs(r['final'] - p['final_equity']) <= 1e-6 * max(1.0, abs(r['final'])))
    worst_rel = max((abs(r['final'] - p['final_equity']) / max(1.0, abs(r['final']))
                     for r, p in zip(ref, arm_a)), default=0.0)
    print(f"arm A per-seed bit-exact vs REF : {n_exact}/{len(ref)}   "
          f"worst relative deviation {worst_rel:.3e}")
    n_trade_match = sum(1 for r, p in zip(ref, arm_a)
                        if r['call_trades'] == p['n_fills'])
    print(f"arm A per-seed TRADE-COUNT match: {n_trade_match}/{len(ref)}   "
          f"(ref mean {statistics.mean(r['call_trades'] for r in ref):.1f} vs "
          f"ladder mean {statistics.mean(p['n_fills'] for p in arm_a):.1f})")
    if n_exact < len(ref):
        for r, p, s in list(zip(ref, arm_a, seeds))[:3]:
            print(f"   seed {s}: ref final ${r['final']:,.2f} trades {r['call_trades']} "
                  f"| ladder final ${p['final_equity']:,.2f} fills {p['n_fills']}")
    b_rel = max((abs(r['final'] - p['final_equity']) / max(1.0, abs(r['final']))
                 for r, p in zip(ref, arm_b)), default=0.0)
    b_mean_rel = abs(b_mean - r_mean) / max(1e-9, abs(r_mean))
    print(f"arm B integer-granularity delta : worst per-seed {b_rel:.3e}   "
          f"mean-return relative gap {b_mean_rel:.3e}")
    print('=' * 100)

    med_rel = statistics.median(
        abs(r['final'] - p['final_equity']) / max(1.0, abs(r['final']))
        for r, p in zip(ref, arm_b))
    print(f"arm B median per-seed relative deviation {med_rel:.3e} "
          f"(mean-return gaps are tail-dominated in bull windows -- read the "
          f"median, not the mean)")
    ok_a = n_exact == len(ref)
    ok_b = med_rel <= 0.05
    if ok_a:
        print("VALIDATION PASS (arm A bit-exact -- the ladder capital loop "
              "reproduces monte_carlo's physics AND its sizing decisions)")
    else:
        print("VALIDATION FAIL -- arm A is NOT bit-exact vs run_single_sim. "
              "STOP: do not run sweeps.")
    if not ok_b:
        print("WARNING: arm B integer-granularity MEDIAN deviation exceeds 5% "
              "-- granularity is NOT negligible at this capital.")
    # Arm A is the GATE (it isolates the capital loop from the physics). Arm B
    # is informational: integer flooring leaves residual cash, which reshuffles
    # marginal fills, and in a heavy-right-tail window that moves the MEAN a
    # lot while the median stays put.
    return ok_a


# ---------------------------------------------------------------------------
# SELFTEST -- fully offline, synthetic outcomes, NO DB and NO monte_carlo
# ---------------------------------------------------------------------------
def _synth_days(start: date, n: int) -> list:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _synth_outcome(entry: float, premium_pct: float, exit_bar: int = 5):
    return dict(kind='tp', exit_bar=exit_bar, premium_pct=premium_pct,
                entry=entry, signal_date=None)


def selftest() -> bool:
    fails = []

    def chk(cond, msg):
        if cond:
            print(f"  ok   {msg}")
        else:
            print(f"  FAIL {msg}")
            fails.append(msg)

    phys = Physics()
    print("SELFTEST -- offline synthetic outcomes (no DB, no monte_carlo import)")

    # ---- 1. asymmetric cost model ----------------------------------------
    print("\n[1] cheap-end asymmetric cost model")
    prem = 4.00           # $4.00/share = $400/contract
    chk(abs(spread_cost_per_share('tp', prem, 0.03, 0.0) - 0.0) < 1e-12,
        "limit TP with floor=0 pays NO spread")
    chk(abs(spread_cost_per_share('entry', prem, 0.03, 0.0) - 0.0) < 1e-12,
        "mid entry with floor=0 pays NO spread")
    chk(abs(spread_cost_per_share('forced', prem, 0.03, 0.0) - 0.06) < 1e-12,
        "forced exit pays HALF the 3% spread (0.5*0.03*4.00 = $0.06/share)")
    chk(abs(spread_cost_per_share('tp', 0.40, 0.03, 0.05) - 0.05) < 1e-12,
        "floor binds at the cheap end (a $0.05 floor on a $0.40 contract)")
    chk(abs(spread_overlay_per_share('forced', prem, 0.03, 0.0, phys.slip_sl)) < 1e-12,
        "forced-exit overlay is 0 when the house SLIP_SL already charged it")
    chk(abs(spread_overlay_per_share('tp', 0.40, 0.03, 0.05, phys.slip_tp) - 0.05) < 1e-12,
        "TP overlay = the full floor (SLIP_TP charges nothing)")
    cfg_c = LadderConfig(spread_floor=0.05, fee_per_contract=0.65)
    chk(abs(contract_cost_dollars(0.40, cfg_c) - (100 * 0.45 + 0.65)) < 1e-9,
        "contract cost = 100*(premium+floor) + fee = $45.65 on a $0.40 contract")

    # ---- 2. savings null --------------------------------------------------
    print("\n[2] savings null (computed, never hardcoded)")
    chk(savings_null_schedule(2000, 2000, 20000, 20000, 36) == 9,
        "hand schedule: 2000 start + 2000/mo -> 20000 at month 9 (10 tranches)")
    chk(savings_null_schedule(2000, 2000, 20000, 20000, 5) is None,
        "null is CENSORED when the horizon is shorter than the schedule")
    chk(savings_null_schedule(2000, 2000, 20000, 6000, 36) is None,
        "pause rule bites: ceiling 6000 freezes savings below the checkpoint")
    chk(savings_null_schedule(25000, 2000, 20000, 20000, 36) == 0,
        "already at/above the checkpoint -> month 0")

    # ---- 3. integer contracts + unaffordable cascade -----------------------
    print("\n[3] integer contracts + unaffordable -> skip -> cascade down")
    days = _synth_days(date(2021, 1, 4), 40)
    # candidate A: $300 underlying, 10% premium -> $30.00/share = $3,000/contract
    # candidate B: $20  underlying, 5%  premium -> $1.00/share  = $100/contract
    outs = {
        ('A', days[0]): _synth_outcome(300.0, 0.10, exit_bar=3),
        ('B', days[0]): _synth_outcome(20.0, 0.05, exit_bar=3),
    }
    cbd = {days[0]: [('A', 95, ('A', days[0]), None, False),
                     ('B', 90, ('B', days[0]), None, False)]}
    cfg = LadderConfig(name='t3', n_slots=1, slot_frac=1.00, spread_floor=0.0,
                       fee_per_contract=0.65, start_capital=2000.0,
                       contributions_on=False, checkpoint=1e12,
                       stage_ceiling=1e12, horizon_months=1)
    got = run_ladder_path(cfg, phys, days[:20], cbd, outs,
                          lambda o, rng: ('tp', 0.0), 1)
    # A is ranked first (score 95) but $3,000 > $2,000 budget -> unaffordable.
    # B costs 100*1.00 + 0.65 = $100.65 -> floor(2000/100.65) = 19 contracts.
    chk(got['n_unaffordable'] == 1, "the unbuyable top-ranked signal is counted as a skip")
    chk(got['n_fills'] == 1 and got['n_contracts'] == 19,
        f"cascade-down filled the affordable signal with 19 contracts (got {got['n_contracts']})")
    cfg1 = LadderConfig(**{**asdict(cfg), 'slot_frac': 0.01})
    got1 = run_ladder_path(cfg1, phys, days[:20], cbd, outs,
                           lambda o, rng: ('tp', 0.0), 1)
    chk(got1['n_fills'] == 0 and got1['n_unaffordable'] == 2,
        "a $20 slot budget can afford NOTHING -> both candidates skipped, zero fills")

    # ---- 4. contribution timing + pause rule ------------------------------
    print("\n[4] contribution timing and the pause rule")
    days90 = _synth_days(date(2021, 1, 4), 70)     # spans Jan, Feb, Mar, Apr
    cfg4 = LadderConfig(name='t4', start_capital=2000.0, contribution=2000.0,
                        checkpoint=1e12, stage_ceiling=1e12, horizon_months=12)
    got4 = run_ladder_path(cfg4, phys, days90, {}, {}, lambda o, r: ('tp', 0.0), 1)
    n_boundaries = len({(d.year, d.month) for d in days90}) - 1
    chk(abs(got4['final_equity'] - (2000.0 + 2000.0 * n_boundaries)) < 1e-9,
        f"no trades -> equity = 2000 + 2000 x {n_boundaries} month boundaries")
    chk(abs(got4['contributed'] - (2000.0 + 2000.0 * n_boundaries)) < 1e-9,
        "contributed total tracks the schedule")
    cfg4p = LadderConfig(**{**asdict(cfg4), 'stage_ceiling': 4000.0})
    got4p = run_ladder_path(cfg4p, phys, days90, {}, {}, lambda o, r: ('tp', 0.0), 1)
    chk(abs(got4p['final_equity'] - 4000.0) < 1e-9,
        "contributions PAUSE at the stage ceiling (2000 -> 4000 then frozen)")

    # ---- 5. months-to-checkpoint incl. censoring, and null symmetry --------
    print("\n[5] months-to-checkpoint accounting + censored case")
    cfg5 = LadderConfig(name='t5', start_capital=2000.0, contribution=2000.0,
                        checkpoint=20000.0, horizon_months=36)
    days_long = _synth_days(date(2021, 1, 4), 300)   # ~14 months of weekdays
    got5 = run_ladder_path(cfg5, phys, days_long, {}, {}, lambda o, r: ('tp', 0.0), 1)
    chk(got5['reached'] and got5['month_index_to_checkpoint'] == 9,
        "a non-trading account reaches 20k at contribution month 9 (== the null)")
    chk(got5['null_month_index'] == 9 and not got5['beat_null'],
        "the in-loop null agrees, and matching the null is NOT beating it")
    chk(abs(got5['months_to_checkpoint'] - got5['null_months']) < 1e-12,
        "strategy and null months coincide exactly under zero trading")
    got5c = run_ladder_path(LadderConfig(**{**asdict(cfg5), 'horizon_months': 36}),
                            phys, days_long[:100], {}, {},
                            lambda o, r: ('tp', 0.0), 1)
    chk((not got5c['reached']) and got5c['months_to_checkpoint'] is None,
        "a short calendar CENSORS the path (reached=False, months=None)")
    chk(savings_null_schedule(2000, 2000, 20000, 20000, 36)
        == got5['null_month_index'],
        "the in-loop null equals the standalone hand-computed schedule")

    # ---- 6. non-absorbing tranche ruin ------------------------------------
    print("\n[6] tranche ruin is NON-ABSORBING")
    d6 = _synth_days(date(2021, 1, 4), 70)
    jan = [d for d in d6 if d.month == 1][0]
    feb = [d for d in d6 if d.month == 2][0]
    mar = [d for d in d6 if d.month == 3][0]
    outs6 = {('X', jan): _synth_outcome(20.0, 0.50, exit_bar=2),   # $1000/contract
             ('X', feb): _synth_outcome(20.0, 0.50, exit_bar=2),
             ('Y', mar): _synth_outcome(20.0, 0.50, exit_bar=2)}
    cbd6 = {jan: [('X', 90, ('X', jan), None, False)],
            feb: [('X', 90, ('X', feb), None, False)],
            mar: [('Y', 90, ('Y', mar), None, False)]}
    cfg6 = LadderConfig(name='t6', n_slots=1, slot_frac=1.00, spread_floor=0.0,
                        fee_per_contract=0.0, start_capital=2000.0,
                        contribution=2000.0, checkpoint=1e12, stage_ceiling=1e12,
                        horizon_months=12)
    got6 = run_ladder_path(cfg6, phys, d6, cbd6, outs6,
                           lambda o, r: ('sl', -1.0), 1)
    chk(got6['n_fills'] == 3,
        f"after a total loss the NEXT contribution resumes trading "
        f"(3 fills across 3 months, got {got6['n_fills']})")
    chk(got6['net_of_contributions'] < 0
        and got6['final_equity'] < got6['contributed'] * 0.5,
        "every tranche is destroyed (net-of-contributions deeply negative) yet "
        "the run never terminates -- only the unspent latest tranche survives")
    # a month whose only candidate was unaffordable is a BURNED month
    outs7 = {('Z', feb): _synth_outcome(300.0, 0.50, exit_bar=2)}   # $15,000/contract
    cbd7 = {feb: [('Z', 90, ('Z', feb), None, False)]}
    cfg7 = LadderConfig(**{**asdict(cfg6), 'name': 't7'})
    got7 = run_ladder_path(cfg7, phys, d6, cbd7, outs7,
                           lambda o, r: ('tp', 1.0), 1)
    chk(got7['months_burned'] >= 1 and got7['n_fills'] == 0,
        f"an all-unaffordable month is counted as BURNED "
        f"(burned={got7['months_burned']})")

    # ---- 7. asymmetric cost applied through the loop ----------------------
    print("\n[7] asymmetric cost applied end-to-end")
    d7 = _synth_days(date(2021, 1, 4), 20)
    outs8 = {('A', d7[0]): _synth_outcome(20.0, 0.05, exit_bar=2)}  # $100/contract
    cbd8 = {d7[0]: [('A', 90, ('A', d7[0]), None, False)]}
    base = dict(name='t8', n_slots=1, slot_frac=1.00, spread_floor=0.10,
                fee_per_contract=0.65, start_capital=2000.0,
                contributions_on=False, checkpoint=1e12, stage_ceiling=1e12,
                horizon_months=1)
    tp_run = run_ladder_path(LadderConfig(**base), phys, d7, cbd8, outs8,
                             lambda o, r: ('tp', 0.30), 1)
    forced = run_ladder_path(LadderConfig(**base), phys, d7, cbd8, outs8,
                             lambda o, r: ('hard', 0.30), 1)
    # identical gross P&L; the forced exit must pay MORE (half spread vs none),
    # but at premium $1.00/share the floor ($0.10) exceeds half-spread ($0.015),
    # so both legs pay the floor and the difference is the SLIP already inside
    # pnl -- here pnl is identical, so the overlay difference must be zero and
    # the two runs must agree. Test the overlay directly instead:
    ov_tp = spread_overlay_per_share('limit', 1.30, 0.03, 0.10, phys.slip_tp)
    ov_fc = spread_overlay_per_share('forced', 1.30, 0.03, 0.10, phys.slip_hard)
    chk(ov_tp > ov_fc,
        "with a binding floor the FORCED leg has already paid part of it via "
        "SLIP_HARD, so its remaining overlay is smaller (no double count)")
    ov_tp0 = spread_overlay_per_share('limit', 1.30, 0.03, 0.0, phys.slip_tp)
    ov_fc0 = spread_overlay_per_share('forced', 1.30, 0.03, 0.0, phys.slip_hard)
    chk(ov_tp0 == 0.0 and ov_fc0 == 0.0,
        "floor=0 -> zero overlay on both legs (validation arm stays bit-exact)")
    chk(abs(tp_run['fees_paid'] - forced['fees_paid']) < 1e-9
        and tp_run['fees_paid'] > 0,
        "per-contract fees are charged on BOTH legs regardless of exit kind")
    n_ct_expected = int(math.floor(2000.0 / (100.0 * (1.0 + 0.10) + 0.65)))
    chk(tp_run['n_contracts'] == n_ct_expected,
        f"affordability uses the ALL-IN contract cost incl. floor+fee "
        f"({n_ct_expected} contracts)")

    # ---- 7b. EQUITY arm ---------------------------------------------------
    print("\n[7b] equity (shares) arm")
    o_eq = dict(kind='tp', exit_bar=3, premium_pct=0.10, entry=50.0,
                fire_open=52.0, fire_close=53.0, fire_high=55.0, fire_low=51.0,
                fire_tp_level=54.0, fire_sl_level=44.0, signal_date=None)
    k, ret = resolve_equity(o_eq, random.Random(1), slippage_bps=0.0)
    chk(k == 'tp' and abs(ret - (54.0 / 50.0 - 1.0)) < 1e-12,
        "equity TP fills AT the sigma barrier (+8.0% underlying, no option layer)")
    o_sl = dict(o_eq, kind='sl')
    k2, ret2 = resolve_equity(o_sl, random.Random(1), slippage_bps=0.0)
    chk(k2 == 'sl' and abs(ret2 - (44.0 / 50.0 - 1.0)) < 1e-12,
        "equity SL: intraday trigger (open > barrier) fills AT the stop (-12%)")
    _, ret3 = resolve_equity(o_eq, random.Random(1), slippage_bps=10.0)
    chk(ret3 < ret, "equity exit slippage (bps of notional) reduces the return")
    d9 = _synth_days(date(2021, 1, 4), 20)
    o9 = {('A', d9[0]): dict(kind='tp', exit_bar=2, premium_pct=0.10, entry=37.0,
                             fire_open=38.0, fire_close=39.0, fire_high=41.0,
                             fire_low=37.5, fire_tp_level=40.0,
                             fire_sl_level=30.0, signal_date=None)}
    c9 = {d9[0]: [('A', 90, ('A', d9[0]), None, False)]}
    cfg9 = LadderConfig(name='t9', instrument='equity', equity_slippage_bps=0.0,
                        n_slots=1, slot_frac=1.00, start_capital=2000.0,
                        contributions_on=False, checkpoint=1e12,
                        stage_ceiling=1e12, horizon_months=1)
    g9 = run_ladder_path(cfg9, phys, d9, c9, o9,
                         lambda o, r: resolve_equity(o, r, 0.0), 1)
    chk(g9['n_contracts'] == int(math.floor(2000.0 / 37.0)),
        f"equity sizing = floor(budget / share price) = "
        f"{int(math.floor(2000.0 / 37.0))} shares (no 100x contract multiplier)")
    exp_final = 2000.0 - 54 * 37.0 + 54 * 40.0
    chk(abs(g9['final_equity'] - exp_final) < 1e-6,
        f"equity P&L is the pure underlying move (expected ${exp_final:,.2f})")
    chk(g9['fees_paid'] == 0.0,
        "equity arm charges NO per-contract option fee")

    # ---- 8. determinism ---------------------------------------------------
    print("\n[8] determinism / paired seeds")
    r1 = run_ladder_path(cfg6, phys, d6, cbd6, outs6, lambda o, r: ('tp', r.random()), 7)
    r2 = run_ladder_path(cfg6, phys, d6, cbd6, outs6, lambda o, r: ('tp', r.random()), 7)
    chk(r1['final_equity'] == r2['final_equity'], "same seed -> same path")
    chk(path_seed('roll_2021-01-01', 3) == path_seed('roll_2021-01-01', 3)
        and path_seed('roll_2021-01-01', 3) != path_seed('roll_2021-02-01', 3),
        "seed depends only on (start label, iteration) -> arms are paired")

    print()
    if fails:
        print(f"SELFTEST FAIL -- {len(fails)} assertion(s) failed:")
        for f in fails:
            print(f"  - {f}")
        return False
    print("SELFTEST PASS")
    return True


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_cell_report(name: str, by_regime: dict) -> None:
    print('\n' + '=' * 118)
    print(f"LADDER RESULT -- {name}")
    print('=' * 118)
    hdr = (f"{'regime':<12} {'paths':>7} {'P(reach)':>9} {'medMo':>7} {'p25':>6} "
           f"{'p75':>6} {'null':>6} {'ratio':>6} {'<=.7?':>6} {'P(beat)':>8} "
           f"{'>=60?':>6} {'FUND':>5} {'medNET$':>10} {'trnchRn':>8} "
           f"{'unaff':>7} {'medDD':>7}")
    print(hdr)
    print('-' * 118)
    for reg in ('all', '2020_crash', '2022', 'bull'):
        m = by_regime.get(reg)
        if not m or not m.get('n_paths'):
            continue
        def f(x, spec='{:.2f}'):
            return spec.format(x) if x is not None else '  -  '
        print(f"{reg:<12} {m['n_paths']:>7} {m['p_reach'] * 100:>8.1f}% "
              f"{f(m['median_months']):>7} {f(m['p25_months']):>6} "
              f"{f(m['p75_months']):>6} {f(m['median_null_months']):>6} "
              f"{f(m['median_ratio_vs_null']):>6} "
              f"{('PASS' if m['bar_speed_pass'] else 'fail'):>6} "
              f"{m['p_beat_null'] * 100:>7.1f}% "
              f"{('PASS' if m['bar_beat_pass'] else 'fail'):>6} "
              f"{'YES' if m['fundable'] else 'no':>5} "
              f"{m['median_net_of_contributions']:>10,.0f} "
              f"{m['tranche_loss_rate'] * 100:>7.1f}% "
              f"{m['unaffordable_skip_rate'] * 100:>6.1f}% "
              f"{m['median_max_dd'] * 100:>6.1f}%")
    print('-' * 118)
    a = by_regime['all']
    curve = '  '.join(f"T={T}m:{a[f'p_reach_by_{T}m'] * 100:.1f}%" for T in T_CURVE_MONTHS)
    print(f"P(reach $ checkpoint within T):  {curve}")
    print(f"fills/path {a['mean_fills']:.1f}  contracts/path {a['mean_contracts']:.1f}  "
          f"TP {a['tp_pct']:.1f}%  SL {a['sl_pct']:.1f}%  HARD {a['hard_pct']:.1f}%  "
          f"fees/path ${a['mean_fees_paid']:,.0f}  spread-overlay/path "
          f"${a['mean_spread_overlay_paid']:,.0f}  slip/path "
          f"${a['mean_slip_paid']:,.0f}  TOTAL COST/path ${a['mean_total_cost']:,.0f}")
    print(f"premium turned over/path ${a['mean_premium_deployed']:,.0f} vs "
          f"${a['mean_contributed']:,.0f} contributed  |  cost = "
          f"{a['cost_pct_of_premium'] * 100:.2f}% of premium traded, "
          f"{a['cost_pct_of_contributions'] * 100:.1f}% of contributions")
    print(f"tranche-ruin: {a['tranche_loss_rate'] * 100:.1f}% of months opened below one "
          f"$tranche  |  P(any) {a['p_any_tranche_loss'] * 100:.1f}%  |  "
          f"burned months {a['mean_months_burned']:.2f}/path")
    print(f"median final equity ${a['median_final_equity']:,.0f}  "
          f"median net-of-contributions ${a['median_net_of_contributions']:,.0f}  "
          f"worst DD {a['worst_dd'] * 100:.1f}% (DIAGNOSTIC, not the primary)")
    print('=' * 118)


def print_summary_table(results: dict, rank: bool = False) -> None:
    """Side-by-side: every option cell, every equity cell, and the SAVINGS NULL
    (the null row is the same number every arm is measured against -- it is
    computed per path, so this prints the pooled median)."""
    rows = []
    for name, r in results.items():
        a = r['metrics']['all']
        rows.append((r['config'].get('instrument', 'option'), name, a))
    if not rows:
        return
    null_med = None
    for _, _, a in rows:
        if a.get('median_null_months'):
            null_med = a['median_null_months']
            break
    print('\n' + '=' * 118)
    print("SUMMARY -- option arms vs EQUITY arm vs the SAVINGS NULL "
          "(pooled over all monthly-roll starts)")
    print('=' * 118)
    print(f"{'instrument':<9} {'cell':<28} {'medMo':>7} {'ratio':>6} {'<=.7?':>6} "
          f"{'P(beat)':>8} {'>=60?':>6} {'medNET$':>10} {'P(<=12m)':>9} "
          f"{'trnchRn':>8} {'medDD':>7} {'FUND':>5}")
    print('-' * 118)
    if null_med is not None:
        print(f"{'SAVINGS':<9} {'null (zero trading)':<28} {null_med:>7.2f} "
              f"{1.00:>6.2f} {'--':>6} {'--':>8} {'--':>6} {0:>10,.0f} "
              f"{'--':>9} {0.0:>7.1f}% {0.0:>6.1f}% {'ref':>5}")
    if rank:
        # FUNDABLE first, then fastest median, then best net-of-contributions.
        rows.sort(key=lambda x: (not x[2]['fundable'],
                                 x[2]['median_months'] if x[2]['median_months']
                                 is not None else 1e9,
                                 -x[2]['median_net_of_contributions']))
    else:
        rows.sort(key=lambda x: (x[0], x[1]))
    for inst, name, a in rows:
        med = a['median_months']
        ratio = a['median_ratio_vs_null']
        print(f"{inst:<9} {name:<28} "
              f"{(f'{med:.2f}' if med is not None else '  -  '):>7} "
              f"{(f'{ratio:.2f}' if ratio is not None else '  -  '):>6} "
              f"{('PASS' if a['bar_speed_pass'] else 'fail'):>6} "
              f"{a['p_beat_null'] * 100:>7.1f}% "
              f"{('PASS' if a['bar_beat_pass'] else 'fail'):>6} "
              f"{a['median_net_of_contributions']:>10,.0f} "
              f"{a['p_reach_by_12m'] * 100:>8.1f}% "
              f"{a['tranche_loss_rate'] * 100:>7.1f}% {a['median_max_dd'] * 100:>6.1f}% "
              f"{('YES' if a['fundable'] else 'no'):>5}")
    print('-' * 118)
    print("FUNDABLE (CHARTER section 1, pre-registered) requires BOTH bars: "
          "'<=.7?' = median months <= 0.70 x null, '>=60?' = P(beat null) >= 60%.")
    print("medNET$ = MEDIAN TERMINAL EQUITY NET OF ALL CONTRIBUTIONS -- positive means "
          "the STRATEGY made money; negative means the DEPOSITS carried it.")
    print("trnchRn = share of months that OPENED with less equity than one contribution "
          "(the priced tranche loss).")
    print("DD is a DIAGNOSTIC here, never the primary.")
    print('=' * 118)


# ---------------------------------------------------------------------------
# Grid driver -- one process per barrier group
# ---------------------------------------------------------------------------
def run_grid(args) -> int:
    """Run every barrier-group config in --groups-dir, each as its OWN process.

    A separate process is MANDATORY, not a convenience: (dte, tp, sl,
    sl_dead_hold) are read by monte_carlo as module-level constants at IMPORT
    time, so two barrier groups cannot coexist in one interpreter. Within a
    group, n_slots / slot_frac / instrument are free axes and share the single
    ~23 s prepare.

    Groups already having a non-empty output file are SKIPPED, so a preempted
    run resumes at the group boundary (costing at most one group).
    """
    import subprocess
    gdir = Path(args.groups_dir)
    if not gdir.is_dir():
        raise SystemExit(f"--groups-dir {gdir} is not a directory")
    cfgs = sorted(f for f in gdir.glob('*.json')
                  if f.name != 'MANIFEST.json' and not f.name.startswith('out_'))
    if not cfgs:
        raise SystemExit(f"no group configs in {gdir}")
    jobs = []
    for c in cfgs:
        out = gdir / f"out_{c.stem}.json"
        if out.exists() and out.stat().st_size > 0:
            print(f"[grid] SKIP {c.stem} (output exists)", flush=True)
            continue
        jobs.append((c, out))
    print(f"[grid] {len(jobs)} group(s) to run, {len(cfgs) - len(jobs)} already done; "
          f"parallel_groups={args.parallel_groups} workers={args.workers or 'auto'}",
          flush=True)

    env = dict(os.environ)
    env.update(PYTHONHASHSEED='0', PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    t0 = time.time()
    failed = []
    running = []          # list of (proc, stem, logfile handle)

    def _launch(c, out):
        cmd = [sys.executable, '-u', str(Path(__file__).resolve()),
               '--mode', 'screen', '--config', str(c), '--out', str(out),
               '--n-iter', str(args.n_iter),
               '--horizon-months', str(args.horizon_months),
               '--start-capital', str(args.start_capital),
               '--contribution', str(args.contribution),
               '--checkpoint', str(args.checkpoint),
               '--hist-start', args.hist_start, '--hist-end', args.hist_end,
               '--min-runway-months', str(args.min_runway_months)]
        if args.workers:
            cmd += ['--workers', str(args.workers)]
        log = open(gdir / f"log_{c.stem}.txt", 'w', encoding='utf-8')
        print(f"[grid] START {c.stem}", flush=True)
        return (subprocess.Popen(cmd, env=env, cwd=str(REPO),
                                 stdout=log, stderr=subprocess.STDOUT), c.stem, log)

    queue = list(jobs)
    k = max(1, int(args.parallel_groups))
    while queue or running:
        while queue and len(running) < k:
            running.append(_launch(*queue.pop(0)))
        done = []
        for item in running:
            proc, stem, log = item
            rc = proc.poll()
            if rc is not None:
                log.close()
                el = time.time() - t0
                print(f"[grid] {'DONE ' if rc == 0 else 'FAIL '}{stem} "
                      f"rc={rc}  (+{el / 60:.1f} min elapsed)", flush=True)
                if rc != 0:
                    failed.append(stem)
                done.append(item)
        for d in done:
            running.remove(d)
        if running:
            time.sleep(2.0)

    el = time.time() - t0
    print(f"[grid] all groups finished in {el / 60:.1f} min; failures: "
          f"{failed if failed else 'none'}", flush=True)

    merged = {}
    for c in cfgs:
        out = gdir / f"out_{c.stem}.json"
        if out.exists() and out.stat().st_size > 0:
            merged.update(json.loads(out.read_text()).get('cells', {}))
    if merged:
        print(f"[grid] merged {len(merged)} cells")
        print_summary_table(merged, rank=True)
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description='Replenishing-bankroll ladder Monte Carlo (CHARTER S2).')
    ap.add_argument('--mode', choices=['validate', 'screen', 'single', 'grid'],
                    default=None)
    ap.add_argument('--groups-dir', default=None,
                    help="grid mode: directory of per-barrier-group config JSONs "
                         "(from make_s3_grid.py). Each group is re-exec'd as its "
                         "OWN process because (dte, tp, sl, dead_hold) are "
                         "import-time monte_carlo constants.")
    ap.add_argument('--parallel-groups', type=int, default=1,
                    help='grid mode: how many group PROCESSES to run at once. '
                         'Each holds ~1.2 GB at --workers 8 and opens its own '
                         'MySQL prepare, so keep this modest.')
    ap.add_argument('--config', default=None,
                    help='JSON: one config object (single) or a list (screen)')
    ap.add_argument('--n-iter', type=int, default=50)
    ap.add_argument('--workers', type=int, default=0, help='0 = auto (cpu_count)')
    ap.add_argument('--horizon-months', type=int, default=36)
    ap.add_argument('--start-capital', type=float, default=2000.0)
    ap.add_argument('--contribution', type=float, default=2000.0)
    ap.add_argument('--checkpoint', type=float, default=20000.0)
    ap.add_argument('--out', default=None)
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--merge', default=None,
                    help='glob of --out JSONs from finished barrier groups; '
                         'prints one ranked summary across every cell and exits')
    # validation-arm knobs
    ap.add_argument('--window', default='2022', help='validate: monte_carlo window label')
    ap.add_argument('--validate-capital', type=float, default=500_000.0)
    ap.add_argument('--validate-slots', type=int, default=4)
    ap.add_argument('--validate-slot-frac', type=float, default=0.15,
                    help='4 x 15%% = 60%% gross (Core-like). Do NOT use 4 x 25%% '
                         '(100%% gross): at exactly-full deployment the last '
                         'slot is a floating-point knife edge and the integer '
                         'arm gets systematically MORE 4th fills than the '
                         'fractional reference -- a config artifact, not a '
                         'granularity effect. See DESIGN.md.')
    # history bounds for the monthly roll
    ap.add_argument('--hist-start', default='2016-06-01')
    ap.add_argument('--hist-end', default='2026-04-15')
    ap.add_argument('--step-months', type=int, default=1)
    ap.add_argument('--min-runway-months', type=int, default=0,
                    help='0 = require the FULL horizon of runway (default)')
    ap.add_argument('--max-starts', type=int, default=0,
                    help='smoke: cap the number of monthly starts (0 = all)')
    args = ap.parse_args()

    if args.selftest:
        return 0 if selftest() else 1
    if args.merge:
        import glob as _glob
        merged = {}
        files = sorted(_glob.glob(args.merge))
        for f in files:
            d = json.loads(Path(f).read_text())
            merged.update(d.get('cells', {}))
        print(f"[merge] {len(files)} group files -> {len(merged)} cells")
        if not merged:
            return 1
        print_summary_table(merged, rank=True)
        return 0
    if args.mode is None:
        ap.error('--mode is required unless --selftest')

    if args.mode == 'grid':
        return run_grid(args)

    if args.mode == 'validate':
        ok = run_validation(args.window, args.n_iter, args.validate_capital,
                            args.validate_slots, args.validate_slot_frac)
        return 0 if ok else 1

    # ---- single / screen ---------------------------------------------------
    if args.config:
        raw = json.loads(Path(args.config).read_text())
    else:
        raw = {}
    cells = [config_from_dict(c) for c in raw] if isinstance(raw, list) \
        else [config_from_dict(raw)]
    for c in cells:
        c.start_capital = args.start_capital
        c.contribution = args.contribution
        c.checkpoint = args.checkpoint
        c.horizon_months = args.horizon_months
    if args.mode == 'single' and len(cells) != 1:
        raise SystemExit('--mode single expects exactly one config object')

    # Every cell in a screen shares the barrier/DTE env -- monte_carlo reads
    # those at IMPORT time, so a screen may only span cells that agree on
    # (dte, tp, sl, sl_dead_hold). Cells differing there need separate runs.
    # An EQUITY cell and an OPTION cell CAN share a process (they share the
    # barrier definitions -- that is the point of the arm), so `instrument` is
    # deliberately not part of this key.
    keys = {(c.dte, c.tp, c.sl, c.sl_dead_hold, c.resolved_hold_cal_days(),
             c.zero_slip) for c in cells}
    if len(keys) > 1:
        raise SystemExit(
            'cells disagree on (dte, tp, sl, dead_hold, zero_slip): those are IMPORT-TIME '
            'monte_carlo constants. Submit one process per barrier group.')

    bootstrap_env(cells[0])
    mc = import_mc()
    hist_start = date.fromisoformat(args.hist_start)
    hist_end = date.fromisoformat(args.hist_end)
    version = mc.AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit} (id={version.id})")

    t_prep = time.time()
    ctx = prepare(mc, 'ladder_hist', hist_start, hist_end, version)
    t_prep = time.time() - t_prep
    print(f"[prepare] {t_prep:.1f}s  trading_days={len(ctx['trading_days'])}  "
          f"call_outcomes={len(ctx['call_outcomes']):,}")

    starts = monthly_starts(ctx['trading_days'], hist_start, hist_end,
                            args.horizon_months,
                            args.min_runway_months or None)
    if args.step_months > 1:
        starts = starts[::args.step_months]
    if args.max_starts and len(starts) > args.max_starts:
        # EVEN STRIDE, never the head: a head slice of a monthly roll is all
        # 2016-2018 bull tape and would hide 2020_crash / 2022 entirely.
        stride = len(starts) / float(args.max_starts)
        starts = [starts[int(i * stride)] for i in range(args.max_starts)]
    regs = {}
    for s in starts:
        regs[start_regime(s[3])] = regs.get(start_regime(s[3]), 0) + 1
    print(f"[starts] {len(starts)} monthly-roll starts  " +
          '  '.join(f'{k}={v}' for k, v in sorted(regs.items())))

    workers = args.workers or (os.cpu_count() or 4)
    results = {}
    total_elapsed = 0.0
    for cfg in cells:
        paths, elapsed = run_cell(mc, ctx, cfg, args.n_iter, workers, starts)
        total_elapsed += elapsed
        by_regime = aggregate_by_regime(paths, cfg)
        print_cell_report(cfg.name, by_regime)
        results[cfg.name] = dict(config=asdict(cfg), metrics=by_regime,
                                 n_paths=len(paths), seconds=elapsed,
                                 seconds_per_path=elapsed / max(1, len(paths)))
    if len(cells) > 1:
        print_summary_table(results)

    print(f"\n[timing] prepare {t_prep:.1f}s  |  simulate {total_elapsed:.1f}s "
          f"for {len(cells)} cell(s) x {len(starts)} starts x {args.n_iter} iter")
    print(f"[timing] PER CELL: {total_elapsed / max(1, len(cells)):.1f}s "
          f"({total_elapsed / max(1, len(cells)) / max(1, len(starts) * args.n_iter) * 1000:.2f} "
          f"ms per path)")

    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(
            dict(meta=dict(n_iter=args.n_iter, n_starts=len(starts),
                           hist_start=args.hist_start, hist_end=args.hist_end,
                           horizon_months=args.horizon_months,
                           prepare_seconds=t_prep, simulate_seconds=total_elapsed),
                 cells=results), indent=2))
        print(f"[out] wrote {outp}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
