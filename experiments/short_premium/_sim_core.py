"""experiments/short_premium/_sim_core.py -- shared deterministic sequential
portfolio-replay engine used by BOTH portfolio_sim.py (real short_ledger
trades, 2022-08+) and crash_stress.py (modeled crash-window trades). See
PREREGISTRATION.md "Portfolio phase". This module is pure w.r.t. its inputs:
given a resolved-position list and a per-day mark panel it does not touch any
parquet/DB/network -- both callers own their own data plumbing and hand this
module plain Python structures.

POSITION SCHEMA (one dict per contract_id; both callers build this):
    contract_id            str, unique key
    symbol                 str
    entry_day_idx          int, calendar index (see build_calendar) of open
    exit_day_idx           int, calendar index of the NATURAL (ledger-resolved)
                            close -- this is the day the position realizes
                            ledger_pnl_per_share if it survives that long
                            without a margin call
    margin0_per_share      float, entry margin/capital per share (used for
                            initial position sizing: n = floor(cap $ /
                            (margin0_per_share * 100)))
    entry_credit_per_share float, $ per share "banked" at entry:
                              kind='short': entry_premium_real * haircut
                              kind='pmcc' : short_prem_received - long_cost
                            (can be negative for pmcc -- it's a net debit)
    ledger_pnl_per_share   float, the ALREADY-RESOLVED $/share P&L at natural
                            exit (short_ledger's pnl_share, or pmcc_pnl_share)
                            -- used verbatim when a position survives to its
                            natural exit day (this is what makes the replay a
                            "deterministic replay of REAL paths": we do not
                            re-derive P&L, only capital allocation + margin
                            calls sit on top of the ledger's own economics)
    kind                   'short' or 'pmcc' -- pmcc positions never enter the
                            margin-call liquidation pool (defined-risk, no
                            margin machinery) but DO count toward the sizing
                            budget via their committed capital.

MARKS SCHEMA: marks_lookup[contract_id][day_idx] = (margin_per_share,
liability_per_share) for every day_idx in [entry_day_idx, exit_day_idx]
inclusive (both callers forward-fill so this is always dense over that range).
    kind='short': margin_per_share = _margin_expr(...) (broker formula, incl.
        + prem); liability_per_share = the current option mark (cost to buy
        back now).
    kind='pmcc' : margin_per_share unused (never read -- pmcc is excluded from
        the margin-call pool); liability_per_share = short_mark - long_mark
        (net cost to unwind the spread today).

EQUITY IDENTITY (uniform across both kinds):
    Equity_t = cash + sum_over_open(entry_credit_per_share * n * 100)
                    - sum_over_open(liability_per_share_today * n * 100)
`cash` only moves when a position fully closes (natural or forced), by the
dollar amount realized at that close. This is algebraically identical to
"cash (running realized P&L) + unrealized mark-to-market of open positions",
and for kind='pmcc' it collapses to unrealized long P&L + unrealized short
P&L (entry_credit = short_recv - long_cost; liability_today = short_mark_t -
long_mark_t => contribution = (long_mark_t - long_cost) - (short_mark_t -
short_recv), i.e. both legs' unrealized P&L, exactly as intended).

MARGIN CALL (kind='short' ONLY): if sum_over_open_shorts(margin_per_share_t *
n * 100) > equity_t: liquidate the largest-margin short first, realizing
(entry_credit_per_share - liability_per_share_today * (2 - haircut)) * n * 100
(a forced EOD exit pays the half-spread, same convention as short_ledger's
sl2x/sl3x forced exits) -- repeat until compliant or no shorts remain. If
equity_t <= 0 at that point (or at any point), collapse=True and the day loop
stops -- no further days are simulated for this path.

TRAPS HONORED: G5 ASCII-only prints. G54 n/a (no threads here). Determinism:
everything here is pure Python/numpy on inputs already materialized by the
caller -- no wall-clock, no unordered dict iteration relied upon for results
(explicit sort keys used everywhere order matters).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

CONTRACT_MULTIPLIER = 100.0


@dataclass
class SimConfig:
    start_capital: float = 100_000.0
    margin_budget_frac: float = 0.20     # fraction of equity budgeted for concurrent margin/capital
    per_trade_cap_frac: float = 0.05     # fraction of equity capped per single trade's margin/capital
    max_concurrent: int = 14
    haircut: float = 0.90
    contract_multiplier: float = CONTRACT_MULTIPLIER
    tag: str = ""


# ===========================================================================
# Small pure stats helpers
# ===========================================================================

def max_drawdown(values) -> float:
    """Max fractional drawdown of a positive-ish equity series. Returns a
    non-negative fraction (0.617 = 61.7% DD). Guards peak<=0 (can't compute a
    fraction against a non-positive peak -- treated as already-collapsed)."""
    peak = float("-inf")
    mdd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return mdd


def cagr(start: float, end: float, n_days: int) -> float:
    """Annualized return over n_days calendar days spanned. NaN if n_days<=0
    (degenerate window); -1.0 if the path ended at/below zero (collapse)."""
    if n_days <= 0 or start <= 0:
        return float("nan")
    if end <= 0:
        return -1.0
    years = n_days / 365.25
    if years <= 0:
        return float("nan")
    return (end / start) ** (1.0 / years) - 1.0


def per_year_returns(equity_curve) -> dict:
    """equity_curve: list of (date, equity) ascending by date. Per-year return
    = last_equity_of_year / first_equity_of_year - 1 (within-year span; NOT
    chained across year boundaries -- documented simplification, adequate for
    a frontier/triage read, not a tax-lot-accurate ledger)."""
    by_year: dict[int, list[float]] = defaultdict(list)
    for d, e in equity_curve:
        by_year[d.year].append(e)
    out = {}
    for yr, vals in by_year.items():
        if vals[0] and vals[0] > 0:
            out[str(yr)] = (vals[-1] / vals[0]) - 1.0
        else:
            out[str(yr)] = float("nan")
    return out


# ===========================================================================
# Core sequential replay
# ===========================================================================

def replay(positions: list[dict], marks_lookup: dict, day_seq: list[tuple[int, object]],
           config: SimConfig) -> dict:
    """day_seq: ascending list of (day_idx, date) spanning the full window to
    simulate (both entries and exits of every position in `positions` must
    fall within this range -- callers build it from the shared calendar).
    Deterministic: entries/exits/liquidations are always processed in a fixed
    sort order (contract_id) so re-running on identical inputs reproduces the
    identical path bit-for-bit."""
    by_entry: dict[int, list[dict]] = defaultdict(list)
    by_exit: dict[int, list[dict]] = defaultdict(list)
    for p in positions:
        by_entry[p["entry_day_idx"]].append(p)
        by_exit[p["exit_day_idx"]].append(p)
    for lst in by_entry.values():
        lst.sort(key=lambda p: p["contract_id"])
    for lst in by_exit.values():
        lst.sort(key=lambda p: p["contract_id"])

    mult = config.contract_multiplier
    cash = config.start_capital
    open_pos: dict[str, dict] = {}   # contract_id -> {**position, 'n': int}

    equity_curve: list[tuple[object, float]] = []
    skip_count = 0
    attempt_count = 0
    margin_call_days = 0
    forced_liq_count = 0
    forced_liq_pnl_impact = 0.0
    util_samples: list[float] = []
    collapse = False
    collapse_day_idx = None

    def _mark(cid, di):
        m = marks_lookup.get(cid)
        if m is None:
            return None
        return m.get(di)

    def _equity(exclude=()) -> float:
        e = cash
        for cid, p in open_pos.items():
            if cid in exclude:
                continue
            mk = _mark(cid, p["_last_marked_day"])
            liab = mk[1] if mk is not None else p["entry_credit_per_share"]
            e += (p["entry_credit_per_share"] - liab) * p["n"] * mult
        return e

    for di, date in day_seq:
        if collapse:
            break

        # -- 1. natural closes scheduled for today (ledger-resolved P&L, verbatim)
        for p in by_exit.get(di, ()):
            if p["contract_id"] not in open_pos:
                continue
            op = open_pos.pop(p["contract_id"])
            cash += op["ledger_pnl_per_share"] * op["n"] * mult

        # -- 2. mark all remaining open positions to today
        for cid, p in open_pos.items():
            mk = _mark(cid, di)
            if mk is not None:
                p["_last_marked_day"] = di
            # else: leave _last_marked_day at its previous value (forward-fill
            # semantics already baked into marks_lookup by the caller; this is
            # only a defensive fallback for a genuinely missing day)

        equity_today = _equity()

        # -- 3. margin call (kind='short' only)
        forced_this_day = 0
        while True:
            shorts = [(cid, p) for cid, p in open_pos.items() if p["kind"] != "pmcc"]
            if not shorts:
                break
            total_margin = 0.0
            margins = {}
            for cid, p in shorts:
                mk = _mark(cid, p["_last_marked_day"])
                m_share = mk[0] if mk is not None else 0.0
                margins[cid] = m_share * p["n"] * mult
                total_margin += margins[cid]
            equity_now = _equity()
            if total_margin <= equity_now:
                break
            # liquidate largest-margin first
            shorts.sort(key=lambda cp: margins[cp[0]], reverse=True)
            cid, p = shorts[0]
            mk = _mark(cid, p["_last_marked_day"])
            liab = mk[1] if mk is not None else p["entry_credit_per_share"]
            forced_close_liab = liab * (2.0 - config.haircut)
            realized = (p["entry_credit_per_share"] - forced_close_liab) * p["n"] * mult
            cash += realized
            forced_liq_pnl_impact += realized - p["ledger_pnl_per_share"] * p["n"] * mult
            del open_pos[cid]
            forced_liq_count += 1
            forced_this_day += 1
            equity_today = _equity()
        if forced_this_day:
            margin_call_days += 1

        if equity_today <= 0:
            collapse = True
            collapse_day_idx = di
            equity_curve.append((date, equity_today))
            break

        # -- 4. utilization sample (shorts margin / budget), pre-new-entries
        shorts_margin_now = 0.0
        for cid, p in open_pos.items():
            if p["kind"] == "pmcc":
                continue
            mk = _mark(cid, p["_last_marked_day"])
            if mk is not None:
                shorts_margin_now += mk[0] * p["n"] * mult
        budget_dollars = config.margin_budget_frac * equity_today
        if budget_dollars > 0:
            util_samples.append(shorts_margin_now / budget_dollars)

        # -- 5. new entries scheduled for today
        for p in by_entry.get(di, ()):
            attempt_count += 1
            cap_dollars = config.per_trade_cap_frac * equity_today
            n = int(math.floor(cap_dollars / (p["margin0_per_share"] * mult))) if p["margin0_per_share"] > 0 else 0
            if n <= 0:
                skip_count += 1
                continue
            if len(open_pos) >= config.max_concurrent:
                skip_count += 1
                continue
            this_committed = p["margin0_per_share"] * mult * n
            committed_now = shorts_margin_now + sum(
                (op["margin0_per_share"] * mult * op["n"]) for op in open_pos.values() if op["kind"] == "pmcc"
            )
            if committed_now + this_committed > budget_dollars:
                skip_count += 1
                continue
            newp = dict(p)
            newp["n"] = n
            newp["_last_marked_day"] = di
            open_pos[p["contract_id"]] = newp
            if p["kind"] != "pmcc":
                shorts_margin_now += this_committed

        equity_curve.append((date, equity_today))

    final_equity = equity_curve[-1][1] if equity_curve else config.start_capital
    values = [e for _, e in equity_curve]
    worst_dd = max_drawdown(values) if values else 0.0
    n_days_span = (equity_curve[-1][0] - equity_curve[0][0]).days if len(equity_curve) >= 2 else 0

    return dict(
        tag=config.tag,
        start_capital=config.start_capital,
        final_equity=final_equity,
        cagr=cagr(config.start_capital, final_equity, n_days_span),
        worst_dd=worst_dd,
        collapse=collapse,
        collapse_day_idx=collapse_day_idx,
        margin_call_days=margin_call_days,
        forced_liq_count=forced_liq_count,
        forced_liq_pnl_impact=forced_liq_pnl_impact,
        mean_util=float(np.mean(util_samples)) if util_samples else float("nan"),
        peak_util=float(np.max(util_samples)) if util_samples else float("nan"),
        skip_count=skip_count,
        attempt_count=attempt_count,
        skip_rate=(skip_count / attempt_count) if attempt_count else float("nan"),
        per_year=per_year_returns(equity_curve),
        equity_curve=equity_curve,
        n_positions=len(positions),
    )


# ===========================================================================
# Jackknife CI (signal-level subsampling, NOT a fill-model bootstrap)
# ===========================================================================

def jackknife(positions: list[dict], marks_lookup: dict, day_seq: list[tuple[int, object]],
              config: SimConfig, n: int = 200, seed: int = 42, drop_frac: float = 0.20) -> dict:
    """N replays, each dropping a random drop_frac of the SIGNALS (positions)
    and re-running the identical sequential replay on the remainder. Seeded
    per-replay as RandomState(seed * 1_000_003 + i) so the whole batch is a
    pure deterministic function of (seed, n, drop_frac, positions) -- same
    seed always reproduces the identical p05/p50/p95 (selftest case e)."""
    positions_sorted = sorted(positions, key=lambda p: p["contract_id"])
    finals = []
    dds = []
    for i in range(n):
        rng = np.random.RandomState((seed * 1_000_003 + i) % (2**32 - 1))
        keep_mask = rng.random_sample(len(positions_sorted)) >= drop_frac
        kept = [p for p, keep in zip(positions_sorted, keep_mask) if keep]
        res = replay(kept, marks_lookup, day_seq, config)
        finals.append(res["final_equity"])
        dds.append(res["worst_dd"])
    finals = np.asarray(finals, dtype=float)
    dds = np.asarray(dds, dtype=float)
    return dict(
        n=n, seed=seed, drop_frac=drop_frac,
        final_equity_p05=float(np.percentile(finals, 5)),
        final_equity_p50=float(np.percentile(finals, 50)),
        final_equity_p95=float(np.percentile(finals, 95)),
        worst_dd_p05=float(np.percentile(dds, 5)),
        worst_dd_p50=float(np.percentile(dds, 50)),
        worst_dd_p95=float(np.percentile(dds, 95)),
    )
