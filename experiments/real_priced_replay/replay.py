"""Real-Priced Ledger Replay -- TWIN CLOSE-GRAIN REPLAY.

Runner for experiments/real_priced_replay/DESIGN.md sections 1-3. Loads the
parquet ledger built by build_replay_ledger.py (offline, no DB access -- see
that script's module docstring / DESIGN.md OPEN QUESTIONS #1 for why the
ledger persists the underlying's own forward OHLC) and runs THREE resolution
arms against the identical matched-signal population:

  MODEL-CG   -- option_pricing.option_pnl_pct (RV premium 1.82 x sigma_daily),
                exits triggered on the underlying's DAILY CLOSES only.
  REAL-CG    -- the real contract's own daily price prints, exits triggered
                on the same daily-close grain.
  intraday-ref -- the standard PRODUCTION intraday (high/low) trigger path,
                reported alongside as context only (NOT part of the decision
                comparison; see DESIGN.md section 1 + OPEN QUESTIONS #5).

MODEL-CG vs REAL-CG (identical trigger grain) is "the decision comparison"
(DESIGN section 1). Exit rules (both close-grain arms, identical): TP >=+30%,
SL <=-70% -> dead-hold analog (hold until recovery to >=-15% [popout] or the
earlier of {day-27 hard-sell, contract expiry}), day-27 calendar hard-sell,
expiry at terminal value. Costs: shipped asymmetric canon (entry/TP free;
forced exits -1.5%; SL-popout free, matching production's DH_POP_SLIP=0.0
default -- see OPEN QUESTIONS #3). Sizing: Core cascade (0.20/0.15/0.08/0.03
by tier), MaxPos 14, $50k start, one open position per symbol -- see OPEN
QUESTIONS #2 for what is and is not reproduced from the full production
alloc-scaling stack.

All DESIGN.md section 1/5 constants are IMPORTED from strategy_config /
monte_carlo / option_pricing, never hardcoded duplicates (this repo's
standing convention -- see build_ledger.py precedent).

Outputs: DESIGN.md section 2's pinned metric tables (per-trade gap
distribution, exit-class agreement matrix, popout reality read, entry-premium
reality read, portfolio compound+MaxDD), computed for the liquid-primary
stratum as headline and all-tier as appendix, plus the section 3 flag
evaluations (mechanical). Printed ASCII AND appended to DESIGN.md's RESULTS
section. Every invocation appends one line to
experiments/real_priced_replay/results/attempt_log.md.

"Replay walk is seconds" (DESIGN section 5): this script performs NO MySQL
access at all -- every input is either the persisted parquet ledger or an
imported Python constant.

ASCII-only stdout. PYTHONUTF8=1/PYTHONIOENCODING=utf-8 set here defensively.
"""
from __future__ import annotations

import os
import sys
import time
import json
import statistics
from collections import defaultdict, Counter
from datetime import date, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

import numpy as np                      # noqa: E402
import polars as pl                     # noqa: E402

from option_pricing import option_pnl_pct                          # noqa: E402
import monte_carlo as mc                                            # noqa: E402
from strategy_config import STRATEGY_30DTE as _CFG                  # noqa: E402
# NOTE: none of these imports perform DB or filesystem I/O at module scope --
# `_CFG.TP_SIGMA_BASE` etc. below are pure Python @property computations.

LEDGER_PATH = os.path.join(_ROOT, ".cache", "real_priced_replay", "replay_ledger.parquet")
DESIGN_PATH = os.path.join(_HERE, "DESIGN.md")
RESULTS_DIR = os.path.join(_HERE, "results")
ATTEMPT_LOG = os.path.join(RESULTS_DIR, "attempt_log.md")

# ---------------------------------------------------------------------------
# DESIGN.md-pinned constants, IMPORTED (never hardcoded) -- strategy_config
# is the single source of truth (CLAUDE.md). TP_STRESS==TP_BASE and
# SL_STRESS==SL_BASE for the shipped Core config ("inert now" per
# strategy_config.py's own OPT_30DTE comment) -- asserted below so a future
# strategy_config edit that breaks this assumption fails loudly rather than
# silently mis-pricing.
# ---------------------------------------------------------------------------
TP_PCT = _CFG.option.TP_BASE              # +0.30
SL_PCT = _CFG.option.SL_BASE              # -0.70
POPOUT_PCT = _CFG.DEAD_HOLD_POPOUT_PNL    # -0.15
HOLD_CAL_DAYS = _CFG.HOLD_CAL_DAYS        # 27 (calendar-day hard-sell deadline)
NOMINAL_CAL_DTE = _CFG.NOMINAL_CAL_DTE    # 30 (honest-theta clock)
DELTA = _CFG.option.DELTA                 # 0.50
PREMIUM_MULT = _CFG.PREMIUM_MULT          # 1.82
SLIP_ENTRY = _CFG.option.SLIP_ENTRY       # 0.0
SLIP_TP = _CFG.option.SLIP_TP             # 0.0
SLIP_HARD = _CFG.option.SLIP_HARD         # -0.015
DH_POP_SLIP = 0.0                         # monte_carlo.DH_POP_SLIP default (env-gated realism knob,
                                           # unset in this probe) -- see DESIGN.md OPEN QUESTIONS #3
TIER_ALLOC = dict(_CFG.TIER_ALLOC)        # {'ultra':0.20,'top':0.15,'mid':0.08,'low':0.03,'overflow':0.0}
MAX_POSITIONS = _CFG.MAX_POSITIONS_CALL or _CFG.MAX_POSITIONS   # 14
GROSS_CAP = _CFG.CALL_PREMIUM_CAP if _CFG.CALL_PREMIUM_CAP > 0 else _CFG.GROSS_PREMIUM_CAP  # 0.50
STARTING_CASH = 50_000.0                  # DESIGN.md section 1
LIQUID_N_FLOOR = 800                      # DESIGN.md section 5

assert abs(_CFG.option.TP_STRESS - TP_PCT) < 1e-9, \
    "TP_STRESS != TP_BASE -- the inert-stress-switch assumption behind DESIGN's flat +30%/-70% no longer holds"
assert abs(_CFG.option.SL_STRESS - SL_PCT) < 1e-9, \
    "SL_STRESS != SL_BASE -- the inert-stress-switch assumption behind DESIGN's flat +30%/-70% no longer holds"

_TIER_PRIORITY = {"ultra": 0, "top": 1, "mid": 2, "low": 3, "overflow": 4}

EXIT_CLASSES = ("TP", "SL_POPOUT", "SL_HELD", "HARD", "EXPIRY", "UNRESOLVED")

_SLIP_BY_CLASS = {
    "TP": SLIP_TP,              # 0.0  -- limit fill, free ("entry/TP free")
    "SL_POPOUT": DH_POP_SLIP,   # 0.0  -- resting-limit recovery fill, free (production default)
    "SL_HELD": SLIP_HARD,       # -0.015 -- forced deadline/expiry close while still dead-held
    "HARD": SLIP_HARD,          # -0.015 -- forced day-27 close, TP/SL never touched
    "EXPIRY": SLIP_HARD,        # -0.015 -- forced contract-expiry close (REAL-CG only)
}


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Attempt log (append-only)
# ---------------------------------------------------------------------------
def _git_head():
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_ROOT,
                              capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()[:12]
    except Exception:
        pass
    return "unknown"


def _append_attempt_log(outcome):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    head = _git_head()
    cmd = " ".join(sys.argv)
    line = f"- {ts} | HEAD={head} | cmd: {cmd} | outcome: {outcome}\n"
    try:
        with open(ATTEMPT_LOG, "a", encoding="ascii", errors="backslashreplace") as f:
            f.write(line)
    except Exception as e:
        log(f"WARNING: failed to append attempt log: {e!r}")


# ---------------------------------------------------------------------------
# Resolution engines
# ---------------------------------------------------------------------------
def walk_close_grain(dates_seq, pnl_seq, deadline_date, hard_stop_date=None):
    """Shared TP/SL/dead-hold/deadline walker over a (date, pnl) sequence,
    DAILY-CLOSE-GRAIN ONLY (DESIGN.md section 1 -- no intraday high/low).
    Used for BOTH MODEL-CG and REAL-CG -- the walk logic is identical; only
    how pnl_seq[i] was computed differs (see resolve_model_cg/resolve_real_cg).

    hard_stop_date: the matched contract's own expiration_date (REAL-CG) or
    None (MODEL-CG -- NOMINAL_CAL_DTE=30 > HOLD_CAL_DAYS=27 always, so the
    modeled option's own "expiry" never arrives before the day-27 deadline).

    Returns dict: exit_class in {TP, SL_POPOUT, SL_HELD, HARD, EXPIRY,
    UNRESOLVED}, exit_date, pnl (raw, pre-slippage), sl_breach_date,
    days_to_popout.
    """
    if hard_stop_date is None:
        effective_stop = deadline_date
        terminal_is_expiry = False
    else:
        effective_stop = min(deadline_date, hard_stop_date)
        terminal_is_expiry = hard_stop_date < deadline_date

    state = "active"
    sl_breach_date = None
    last_date = None
    last_pnl = None
    for d, pnl in zip(dates_seq, pnl_seq):
        if d > effective_stop:
            break
        last_date, last_pnl = d, pnl
        if state == "active":
            if pnl >= TP_PCT:
                return {"exit_class": "TP", "exit_date": d, "pnl": pnl,
                        "sl_breach_date": None, "days_to_popout": None}
            if pnl <= SL_PCT:
                state = "dead_hold"
                sl_breach_date = d
        elif state == "dead_hold":
            if pnl >= POPOUT_PCT:
                return {"exit_class": "SL_POPOUT", "exit_date": d, "pnl": pnl,
                        "sl_breach_date": sl_breach_date,
                        "days_to_popout": (d - sl_breach_date).days}

    if last_date is None:
        return {"exit_class": "UNRESOLVED", "exit_date": None, "pnl": None,
                "sl_breach_date": None, "days_to_popout": None}
    if state == "dead_hold":
        cls = "SL_HELD"
    else:
        cls = "EXPIRY" if terminal_is_expiry else "HARD"
    return {"exit_class": cls, "exit_date": last_date, "pnl": last_pnl,
            "sl_breach_date": sl_breach_date, "days_to_popout": None}


def resolve_model_cg(row):
    """MODEL-CG: option_pnl_pct over the underlying's forward daily closes,
    calendar-day theta clock (CALENDAR_HOLD honest-theta convention: bars_held
    = calendar days elapsed, total_dte fixed at NOMINAL_CAL_DTE -- matches
    monte_carlo.py's _compute_dead_hold_call._clk exactly), vega_ratio=1.0
    (DESIGN section 1 describes no earnings-vega mechanism for this probe)."""
    entry_date = date.fromisoformat(row["date"])
    entry_close = row["entry_close"]
    model_premium_pct = row["model_premium_pct"]
    m_dates_raw = row["model_dates"]
    m_closes = row["model_closes"]
    if not m_dates_raw or model_premium_pct is None or model_premium_pct <= 0:
        return {"exit_class": "UNRESOLVED", "exit_date": None, "pnl": None,
                "sl_breach_date": None, "days_to_popout": None}
    m_dates = [date.fromisoformat(s) for s in m_dates_raw]
    pnl_seq = []
    for d, c in zip(m_dates, m_closes):
        cal_days = (d - entry_date).days
        pnl_seq.append(option_pnl_pct("call", c, entry_close, cal_days, model_premium_pct,
                                       total_dte=NOMINAL_CAL_DTE, vega_ratio=1.0, delta=DELTA))
    deadline = entry_date + timedelta(days=HOLD_CAL_DAYS)
    return walk_close_grain(m_dates, pnl_seq, deadline, hard_stop_date=None)


def resolve_real_cg(row):
    """REAL-CG: the real contract's own daily prints, no theta model needed
    (theta is already embedded in the real market price)."""
    entry_date = date.fromisoformat(row["date"])
    entry_premium = row["entry_premium"]
    r_dates_raw = row["real_dates"]
    r_prices = row["real_prices"]
    if not r_dates_raw or entry_premium is None or entry_premium <= 0:
        return {"exit_class": "UNRESOLVED", "exit_date": None, "pnl": None,
                "sl_breach_date": None, "days_to_popout": None}
    r_dates = [date.fromisoformat(s) for s in r_dates_raw]
    pnl_seq = [p / entry_premium - 1.0 for p in r_prices]
    deadline = entry_date + timedelta(days=HOLD_CAL_DAYS)
    expiry = date.fromisoformat(row["expiration_date"])
    return walk_close_grain(r_dates, pnl_seq, deadline, hard_stop_date=expiry)


def resolve_intraday_reference(row):
    """Deterministic reference-only column (DESIGN section 1 "reported
    alongside as reference" / section 4 "single-path, no MC dispersion").
    Mirrors monte_carlo.compute_trade_outcome's STATIC entry-fixed sigma
    barriers (STRATEGY_30DTE.TP_SIGMA_BASE/SL_SIGMA_BASE) tested against
    intraday high/low, and _compute_dead_hold_call's popout check (intraday
    high, open-price-floored fill) -- but with a deterministic barrier fill
    instead of production's per-iteration Uniform(lo,hi) random fill (no RNG
    in a single-path accounting replay). Same-bar TP+SL collisions resolve to
    a realized SL_HELD at the SL barrier (conservative tie-break; see
    DESIGN.md OPEN QUESTIONS #5). NOT part of the MODEL-CG vs REAL-CG
    decision comparison."""
    entry_date = date.fromisoformat(row["date"])
    entry_close = row["entry_close"]
    rv = row["rv"]
    model_premium_pct = row["model_premium_pct"]
    m_dates_raw = row["model_dates"]
    if not m_dates_raw or model_premium_pct is None or model_premium_pct <= 0 or rv is None:
        return {"exit_class": "UNRESOLVED", "exit_date": None, "pnl": None,
                "sl_breach_date": None, "days_to_popout": None}
    m_dates = [date.fromisoformat(s) for s in m_dates_raw]
    m_opens, m_highs, m_lows, m_closes = row["model_opens"], row["model_highs"], row["model_lows"], row["model_closes"]

    tp_level = entry_close * (1 + _CFG.TP_SIGMA_BASE * rv / 100.0)
    sl_level = entry_close * (1 - _CFG.SL_SIGMA_BASE * rv / 100.0)
    deadline = entry_date + timedelta(days=HOLD_CAL_DAYS)

    def _pnl_at(price, cal_days):
        return option_pnl_pct("call", price, entry_close, cal_days, model_premium_pct,
                               total_dte=NOMINAL_CAL_DTE, vega_ratio=1.0, delta=DELTA)

    state = "active"
    sl_breach_date = None
    last_date = None
    last_pnl = None
    for d, o, h, l, c in zip(m_dates, m_opens, m_highs, m_lows, m_closes):
        if d > deadline:
            break
        cal_days = (d - entry_date).days
        last_date = d
        last_pnl = _pnl_at(c, cal_days)
        if state == "active":
            tp_hit = h >= tp_level
            sl_hit = l <= sl_level
            if tp_hit and sl_hit:
                # Same-bar collision: an extreme/ambiguous bar. Route straight
                # to a realized SL at the barrier (conservative tie-break,
                # not a dead-hold chance) rather than production's 50/50 RNG
                # coin flip -- collapses into SL_HELD for reporting.
                return {"exit_class": "SL_HELD", "exit_date": d, "pnl": _pnl_at(sl_level, cal_days),
                        "sl_breach_date": d, "days_to_popout": None}
            if tp_hit:
                return {"exit_class": "TP", "exit_date": d, "pnl": _pnl_at(tp_level, cal_days),
                        "sl_breach_date": None, "days_to_popout": None}
            if sl_hit:
                state = "dead_hold"
                sl_breach_date = d
        elif state == "dead_hold":
            high_pnl = _pnl_at(h, cal_days)
            if high_pnl >= POPOUT_PCT:
                open_pnl = _pnl_at(o, cal_days)
                fill_pnl = max(POPOUT_PCT, open_pnl)
                return {"exit_class": "SL_POPOUT", "exit_date": d, "pnl": fill_pnl,
                        "sl_breach_date": sl_breach_date, "days_to_popout": (d - sl_breach_date).days}

    if last_date is None:
        return {"exit_class": "UNRESOLVED", "exit_date": None, "pnl": None,
                "sl_breach_date": None, "days_to_popout": None}
    cls = "SL_HELD" if state == "dead_hold" else "HARD"
    return {"exit_class": cls, "exit_date": last_date, "pnl": last_pnl,
            "sl_breach_date": sl_breach_date, "days_to_popout": None}


def net_pnl(resolved):
    raw = resolved.get("pnl")
    cls = resolved.get("exit_class")
    if raw is None or cls not in _SLIP_BY_CLASS:
        return None
    return raw + SLIP_ENTRY + _SLIP_BY_CLASS[cls]


# ---------------------------------------------------------------------------
# Portfolio simulator (single-path, day-stepped, cost-basis marking)
# ---------------------------------------------------------------------------
def run_portfolio(rows, resolved_list, starting_cash=STARTING_CASH, max_pos=MAX_POSITIONS,
                   gross_cap=GROSS_CAP, tier_alloc=None):
    """Single-path, day-stepped cascade portfolio replay (DESIGN.md section 1
    "Core cascade ... identical capital rules both arms; $50k start"; see
    DESIGN.md OPEN QUESTIONS #2 for exactly what is/isn't reproduced from the
    full production alloc-scaling stack).

    Mirrors monte_carlo.py's OWN convention exactly: `portfolio_value = cash
    + sum(open-position cost basis)` (monte_carlo.py ~line 2993) -- NOT a
    mark-to-market curve; production's own MaxDD/equity-curve tracking uses
    this identical convention, so this replay's DD numbers are apples-to-
    apples with the rest of this project's Stage-3 evidence base.

    rows: list of ledger dict rows (already filtered to the desired
    liquid-primary/all-tier population).
    resolved_list: list, same length/order as `rows`, of resolved dicts from
    one of the resolve_* functions above.

    Returns dict(compound_pct, max_dd_pct, n_trades, n_skipped_maxpos,
    n_skipped_cap, n_skipped_same_symbol, n_forced_open_at_end, trades=[...]).
    """
    tier_alloc = tier_alloc or TIER_ALLOC
    entries_by_date = defaultdict(list)
    for i, (r, res) in enumerate(zip(rows, resolved_list)):
        if res.get("exit_date") is None or res.get("pnl") is None:
            continue
        npnl = net_pnl(res)
        if npnl is None:
            continue
        tier = r.get("tier") or mc.score_to_tier(r["overall"])
        entries_by_date[date.fromisoformat(r["date"])].append(
            (i, r["symbol"], tier, r["overall"], res["exit_date"], npnl, res["exit_class"]))

    exits_by_date = defaultdict(list)
    for d, sigs in entries_by_date.items():
        for (i, sym, tier, overall, exit_date, npnl, cls) in sigs:
            exits_by_date[exit_date].append((i, sym, npnl, cls, d))

    all_days = sorted(set(entries_by_date) | set(exits_by_date))
    cash = starting_cash
    open_positions = []      # dict(row, symbol, entry_date, exit_date, alloc, tier)
    trades = []
    equity_curve = []
    peak = starting_cash
    max_dd = 0.0
    n_skipped_maxpos = 0
    n_skipped_cap = 0
    n_skipped_same_symbol = 0

    for day in all_days:
        for (i, sym, npnl, cls, entry_date) in exits_by_date.get(day, []):
            match = None
            for p in open_positions:
                if p["row"] == i:
                    match = p
                    break
            if match is None:
                continue
            realized = match["alloc"] * (1.0 + npnl)
            cash += realized
            open_positions.remove(match)
            trades.append({"row": i, "symbol": sym, "entry_date": entry_date, "exit_date": day,
                            "tier": match["tier"], "alloc": match["alloc"], "net_pnl": npnl,
                            "exit_class": cls, "pnl_dollars": realized - match["alloc"]})

        todays = sorted(entries_by_date.get(day, []),
                         key=lambda t: (_TIER_PRIORITY.get(t[2], 9), -t[3], t[1]))
        open_syms = {p["symbol"] for p in open_positions}
        for (i, sym, tier, overall, exit_date, npnl, cls) in todays:
            if sym in open_syms:
                n_skipped_same_symbol += 1
                continue
            if len(open_positions) >= max_pos:
                n_skipped_maxpos += 1
                continue
            port_val = cash + sum(p["alloc"] for p in open_positions)
            committed = sum(p["alloc"] for p in open_positions)
            headroom = gross_cap * port_val - committed
            target = tier_alloc.get(tier, 0.0) * port_val
            size = min(target, headroom, cash)
            if size <= 1.0:
                n_skipped_cap += 1
                continue
            open_positions.append({"row": i, "symbol": sym, "entry_date": day,
                                    "exit_date": exit_date, "alloc": size, "tier": tier})
            open_syms.add(sym)
            cash -= size

        port_val = cash + sum(p["alloc"] for p in open_positions)
        if port_val > peak:
            peak = port_val
        dd = (peak - port_val) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        equity_curve.append((day.isoformat(), port_val))

    # Boundary effect: anything still open when the observed ledger window
    # ends (the tail of the latest signals) is closed at COST BASIS (no
    # gain/loss assumed) -- conservative and explicit, not silently folded in.
    n_forced_open_at_end = len(open_positions)
    for p in open_positions:
        cash += p["alloc"]
    final_value = cash

    compound_pct = (final_value / starting_cash - 1.0) * 100.0 if starting_cash > 0 else None

    return {
        "equity_curve": equity_curve, "trades": trades,
        "final_value": final_value, "compound_pct": compound_pct,
        "max_dd_pct": max_dd * 100.0,
        "n_trades": len(trades), "n_skipped_maxpos": n_skipped_maxpos,
        "n_skipped_cap": n_skipped_cap, "n_skipped_same_symbol": n_skipped_same_symbol,
        "n_forced_open_at_end": n_forced_open_at_end,
    }


# ---------------------------------------------------------------------------
# Stratification helpers
# ---------------------------------------------------------------------------
def rv_tercile_cuts(rows):
    vals = [r["rv"] for r in rows if r.get("rv") is not None]
    if len(vals) < 3:
        return None, None
    arr = np.array(vals, dtype=float)
    return float(np.quantile(arr, 1.0 / 3)), float(np.quantile(arr, 2.0 / 3))


def rv_tercile_label(rv, t1, t2):
    if rv is None or t1 is None:
        return None
    if rv <= t1:
        return "T1_low"
    if rv <= t2:
        return "T2_mid"
    return "T3_high"


def vix_band_label(vix):
    """DESIGN.md does not pin VIX band cut points (OPEN QUESTIONS #4). Uses
    <20 / 20-28 / >=28, matching the center/panic thresholds already shipped
    in RXDD (VIX_C=22.7) and MWDD (VIX_PANIC=28.0)."""
    if vix is None:
        return None
    if vix < 20.0:
        return "calm(<20)"
    if vix < 28.0:
        return "elevated(20-28)"
    return "panic(>=28)"


def year_label(date_str):
    return date_str[:4]


def summarize(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    arr = np.array(values, dtype=float)
    out = {"n": int(arr.size), "mean": float(np.mean(arr)), "median": float(np.median(arr))}
    if arr.size > 1:
        out["std"] = float(np.std(arr, ddof=1))
    out["p10"] = float(np.percentile(arr, 10))
    out["p25"] = float(np.percentile(arr, 25))
    out["p75"] = float(np.percentile(arr, 75))
    out["p90"] = float(np.percentile(arr, 90))
    out["min"] = float(np.min(arr))
    out["max"] = float(np.max(arr))
    return out


def _fmt_stats(s, pct=True, digits=2):
    if not s or s.get("n", 0) == 0:
        return "n=0"
    mult = 100.0 if pct else 1.0
    unit = "%" if pct else ""
    return (f"n={s['n']} mean={s['mean']*mult:+.{digits}f}{unit} median={s['median']*mult:+.{digits}f}{unit} "
            f"p25={s['p25']*mult:+.{digits}f}{unit} p75={s['p75']*mult:+.{digits}f}{unit}")


# ---------------------------------------------------------------------------
# Metric computation for one population (liquid-primary headline / all-tier appendix)
# ---------------------------------------------------------------------------
def compute_population_metrics(rows, label, vix_has_coverage):
    n = len(rows)
    log(f"\n[{label}] N={n} -- resolving 3 arms per trade...")
    model_res = [resolve_model_cg(r) for r in rows]
    real_res = [resolve_real_cg(r) for r in rows]
    ref_res = [resolve_intraday_reference(r) for r in rows]

    t1, t2 = rv_tercile_cuts(rows)
    strata = []
    for r in rows:
        strata.append({
            "liquidity": "liquid_ge5" if r.get("liquid_ge5") else ("liquid_gt0" if r.get("liquid_gt0") else "illiquid"),
            "rv_tercile": rv_tercile_label(r.get("rv"), t1, t2),
            "year": year_label(r["date"]),
            "vix_band": vix_band_label(r.get("vix_entry")) if vix_has_coverage else None,
        })

    # ---- 1. Per-trade gap distribution (REAL-CG - MODEL-CG, net of costs) ----
    gaps = []
    gap_strata = {"by_liquidity": defaultdict(list), "by_rv_tercile": defaultdict(list),
                  "by_year": defaultdict(list), "by_vix_band": defaultdict(list)}
    for i in range(n):
        mn, rn = net_pnl(model_res[i]), net_pnl(real_res[i])
        if mn is None or rn is None:
            continue
        gap = rn - mn
        gaps.append(gap)
        s = strata[i]
        gap_strata["by_liquidity"][s["liquidity"]].append(gap)
        if s["rv_tercile"]:
            gap_strata["by_rv_tercile"][s["rv_tercile"]].append(gap)
        gap_strata["by_year"][s["year"]].append(gap)
        if s["vix_band"]:
            gap_strata["by_vix_band"][s["vix_band"]].append(gap)
    gap_overall = summarize(gaps)
    gap_by = {k: {kk: summarize(vv) for kk, vv in v.items()} for k, v in gap_strata.items()}

    # ---- 2. Exit-class agreement matrix (model vs real) ----
    matrix = defaultdict(int)
    for i in range(n):
        mc_cls = model_res[i]["exit_class"]
        rc_cls = real_res[i]["exit_class"]
        matrix[(mc_cls, rc_cls)] += 1

    # ---- 3. Popout reality read (A-1) ----
    def _popout_stats(res_list):
        breached = [r for r in res_list if r["exit_class"] in ("SL_POPOUT", "SL_HELD")]
        popped = [r for r in breached if r["exit_class"] == "SL_POPOUT"]
        rate = (len(popped) / len(breached)) if breached else None
        days = summarize([r["days_to_popout"] for r in popped])
        return {"n_breached": len(breached), "n_popped": len(popped), "popout_rate": rate,
                "days_to_popout": days}
    popout_model = _popout_stats(model_res)
    popout_real = _popout_stats(real_res)

    # ---- 4. Entry-premium reality read (A-3) ----
    ratios = []
    ratio_by_vix = defaultdict(list)
    ratio_by_rv = defaultdict(list)
    for i, r in enumerate(rows):
        mp = r.get("model_premium_pct")
        ep = r.get("entry_premium")
        ec = r.get("entry_close")
        if not mp or mp <= 0 or ep is None or not ec:
            continue
        model_dollars = mp * ec
        if model_dollars <= 0:
            continue
        ratio = ep / model_dollars
        ratios.append(ratio)
        s = strata[i]
        if s["vix_band"]:
            ratio_by_vix[s["vix_band"]].append(ratio)
        if s["rv_tercile"]:
            ratio_by_rv[s["rv_tercile"]].append(ratio)
    ratio_overall = summarize(ratios)
    ratio_by_vix_s = {k: summarize(v) for k, v in ratio_by_vix.items()}
    ratio_by_rv_s = {k: summarize(v) for k, v in ratio_by_rv.items()}

    # ---- 5. Portfolio compound + MaxDD, 3 arms ----
    port_model = run_portfolio(rows, model_res)
    port_real = run_portfolio(rows, real_res)
    port_ref = run_portfolio(rows, ref_res)

    return {
        "label": label, "n": n,
        "rv_tercile_cuts": {"t1": t1, "t2": t2},
        "gap_overall": gap_overall, "gap_by": gap_by,
        "exit_class_matrix": {f"{a}|{b}": v for (a, b), v in matrix.items()},
        "popout_model": popout_model, "popout_real": popout_real,
        "premium_ratio_overall": ratio_overall,
        "premium_ratio_by_vix": ratio_by_vix_s, "premium_ratio_by_rv": ratio_by_rv_s,
        "portfolio_model": port_model, "portfolio_real": port_real, "portfolio_ref": port_ref,
        "n_unresolved_model": sum(1 for r in model_res if r["exit_class"] == "UNRESOLVED"),
        "n_unresolved_real": sum(1 for r in real_res if r["exit_class"] == "UNRESOLVED"),
    }


# ---------------------------------------------------------------------------
# Section 3 flag evaluation (mechanical)
# ---------------------------------------------------------------------------
def evaluate_flags(headline, n_floor_pass):
    flags = {"FILL_MODEL_OPTIMISTIC": False, "PREMIUM_MODEL_CHEAP": False, "reasons": []}
    if not n_floor_pass:
        flags["report_only"] = True
        flags["reasons"].append(f"N-floor not met (liquid-primary N={headline['n']} < {LIQUID_N_FLOOR}) -- report-only, no flags evaluated")
        return flags
    flags["report_only"] = False

    pm = headline["portfolio_model"]["compound_pct"]
    pr = headline["portfolio_real"]["compound_pct"]
    if pm is not None and pr is not None and pm > 0:
        rel_shortfall = (pm - pr) / abs(pm)
        if rel_shortfall > 0.25:
            flags["FILL_MODEL_OPTIMISTIC"] = True
            flags["reasons"].append(f"REAL-CG compound ({pr:+.1f}%) < MODEL-CG ({pm:+.1f}%) by "
                                     f"{rel_shortfall:.1%} relative (>25% threshold)")
    elif pm is not None and pr is not None:
        flags["reasons"].append(f"MODEL-CG compound ({pm:+.1f}%) <= 0 -- relative-shortfall test "
                                 f"ill-defined, reported not flagged (raw: MODEL={pm:+.1f}% REAL={pr:+.1f}%)")

    dm = headline["portfolio_model"]["max_dd_pct"]
    dr = headline["portfolio_real"]["max_dd_pct"]
    if dm is not None and dr is not None:
        dd_gap = dr - dm
        if dd_gap > 5.0:
            flags["FILL_MODEL_OPTIMISTIC"] = True
            flags["reasons"].append(f"REAL-CG MaxDD ({dr:.1f}%) > MODEL-CG ({dm:.1f}%) by "
                                     f"{dd_gap:.1f}pp (>5pp threshold)")

    prm = headline["popout_model"]["popout_rate"]
    prr = headline["popout_real"]["popout_rate"]
    if prm is not None and prr is not None:
        pop_gap = prm - prr
        if pop_gap > 0.15:
            flags["FILL_MODEL_OPTIMISTIC"] = True
            flags["reasons"].append(f"REAL popout rate ({prr:.1%}) < model popout rate ({prm:.1%}) by "
                                     f"{pop_gap:.1%} absolute (>15pp threshold)")
    else:
        flags["reasons"].append(f"popout rate undefined for one/both arms "
                                 f"(model N_breached={headline['popout_model']['n_breached']}, "
                                 f"real N_breached={headline['popout_real']['n_breached']}) -- skipped")

    ratio_med = headline["premium_ratio_overall"].get("median") if headline["premium_ratio_overall"].get("n", 0) else None
    if ratio_med is not None and ratio_med > 1.15:
        flags["PREMIUM_MODEL_CHEAP"] = True
        flags["reasons"].append(f"median(real/model entry premium) overall = {ratio_med:.3f} (>1.15 threshold)")
    for band, s in headline["premium_ratio_by_vix"].items():
        if s.get("n", 0) >= 100 and s.get("median", 0) > 1.40:
            flags["PREMIUM_MODEL_CHEAP"] = True
            flags["reasons"].append(f"median(real/model entry premium) in VIX band {band} = "
                                     f"{s['median']:.3f} (>1.40 threshold, N={s['n']})")

    if not flags["reasons"]:
        flags["reasons"].append("no threshold crossed on any of the four checks")
    return flags


# ---------------------------------------------------------------------------
# ASCII report
# ---------------------------------------------------------------------------
def _fmt_matrix(matrix_dict):
    lines = []
    for cls_m in EXIT_CLASSES:
        for cls_r in EXIT_CLASSES:
            v = matrix_dict.get(f"{cls_m}|{cls_r}", 0)
            if v:
                lines.append(f"    model={cls_m:12s} real={cls_r:12s} N={v}")
    return "\n".join(lines) if lines else "    (no matched pairs)"


def _fmt_strata_block(d, pct=True, digits=2):
    lines = []
    for k in sorted(d.keys()):
        lines.append(f"    {k:20s} {_fmt_stats(d[k], pct=pct, digits=digits)}")
    return "\n".join(lines) if lines else "    (none)"


def build_report_text(headline, appendix, flags, vix_has_coverage, meta):
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    lines = []
    lines.append(f"Run timestamp: {ts}")
    lines.append(f"Ledger: {LEDGER_PATH}  (built_at={meta.get('built_at', '?')}, "
                 f"matched_n={meta.get('matched_n', '?')}, liquid_primary_n={meta.get('liquid_primary_n', '?')})")
    lines.append(f"VIX coverage over signal window: {'OK' if vix_has_coverage else 'SKIPPED-NO-DATA'}")
    lines.append(f"N-floor (>= {LIQUID_N_FLOOR} liquid-primary): "
                 f"{'PASS' if not flags.get('report_only') else 'FAIL -- report-only, no flags'} "
                 f"(N={headline['n']})")

    for pop in (headline, appendix):
        lines.append(f"\n{'='*78}")
        lines.append(f"POPULATION: {pop['label']}  (N={pop['n']})")
        lines.append(f"{'='*78}")

        lines.append("\n-- 1. Per-trade gap distribution (REAL-CG - MODEL-CG net pnl) --")
        lines.append(f"  overall: {_fmt_stats(pop['gap_overall'])}")
        for stratum_name, stratum_dict in pop["gap_by"].items():
            lines.append(f"  by {stratum_name}:")
            lines.append(_fmt_strata_block(stratum_dict))

        lines.append("\n-- 2. Exit-class agreement matrix (model|real: N) --")
        lines.append(_fmt_matrix(pop["exit_class_matrix"]))

        lines.append("\n-- 3. Popout reality read (A-1) --")
        for arm_name, pstat in (("model", pop["popout_model"]), ("real", pop["popout_real"])):
            rate = pstat["popout_rate"]
            rate_s = f"{rate:.1%}" if rate is not None else "n/a"
            lines.append(f"  {arm_name}: N_breached(SL)={pstat['n_breached']} N_popped={pstat['n_popped']} "
                         f"popout_rate={rate_s}")
            lines.append(f"    days_to_popout: {_fmt_stats(pstat['days_to_popout'], pct=False, digits=1)}")

        lines.append("\n-- 4. Entry-premium reality read (A-3): real / model entry premium --")
        lines.append(f"  overall: {_fmt_stats(pop['premium_ratio_overall'], pct=False, digits=3)}")
        lines.append("  by VIX band:" if vix_has_coverage else "  by VIX band: SKIPPED-NO-DATA")
        if vix_has_coverage:
            lines.append(_fmt_strata_block(pop["premium_ratio_by_vix"], pct=False, digits=3))
        lines.append("  by RV tercile:")
        lines.append(_fmt_strata_block(pop["premium_ratio_by_rv"], pct=False, digits=3))

        lines.append("\n-- 5. Portfolio: compound + MaxDD (17-month window, $50k start) --")
        for arm_name, port in (("MODEL-CG", pop["portfolio_model"]), ("REAL-CG", pop["portfolio_real"]),
                                ("intraday-ref", pop["portfolio_ref"])):
            cp = port["compound_pct"]
            cp_s = f"{cp:+.1f}%" if cp is not None else "n/a"
            lines.append(f"  {arm_name:14s} compound={cp_s:>10s}  MaxDD={port['max_dd_pct']:.1f}%  "
                         f"trades={port['n_trades']}  skipped(maxpos/cap/same-sym)="
                         f"{port['n_skipped_maxpos']}/{port['n_skipped_cap']}/{port['n_skipped_same_symbol']}  "
                         f"forced-open-at-end={port['n_forced_open_at_end']}")

        lines.append(f"\n  N unresolved (no forward data through deadline/expiry): "
                     f"model={pop['n_unresolved_model']} real={pop['n_unresolved_real']}")

    lines.append(f"\n{'='*78}")
    lines.append("SECTION 3 -- PINNED FLAG EVALUATION (mechanical, liquid-primary headline)")
    lines.append(f"{'='*78}")
    if flags.get("report_only"):
        lines.append("REPORT-ONLY: N-floor not met, no flags evaluated.")
    else:
        lines.append(f"FILL-MODEL-OPTIMISTIC: {'FLAGGED' if flags['FILL_MODEL_OPTIMISTIC'] else 'not flagged'}")
        lines.append(f"PREMIUM-MODEL-CHEAP:   {'FLAGGED' if flags['PREMIUM_MODEL_CHEAP'] else 'not flagged'}")
    lines.append("Reasons:")
    for r in flags["reasons"]:
        lines.append(f"  - {r}")
    if not flags.get("report_only"):
        if flags["FILL_MODEL_OPTIMISTIC"] or flags["PREMIUM_MODEL_CHEAP"]:
            lines.append("\n=> Escalate the corresponding Tier-2/3 audit probes (P6/P8/P10) to certificate "
                         "grade on this box (DESIGN.md section 3). Nothing else licensed by this flag.")
        else:
            lines.append("\n=> No flag: the canon gains its first market-data corroboration on this era; "
                         "the live fill log (P2.B) remains the final arbiter (DESIGN.md section 3).")

    return "\n".join(lines)


def update_design_results(report_text):
    with open(DESIGN_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    placeholder = "(empty — filled by the runner)"
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    block = f"### Run {ts}\n\n```\n{report_text}\n```\n"
    if placeholder in content:
        new_content = content.replace(placeholder, block, 1)
    else:
        new_content = content.rstrip("\n") + "\n\n---\n\n" + block
    with open(DESIGN_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    t_start = time.time()
    log("=== real_priced_replay TWIN REPLAY ===")

    if not os.path.exists(LEDGER_PATH):
        raise FileNotFoundError(
            f"{LEDGER_PATH} not found -- run build_replay_ledger.py (via the queue) first")

    meta_path = os.path.join(_ROOT, ".cache", "real_priced_replay", "replay_ledger_meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="ascii", errors="replace") as f:
            meta = json.load(f)

    log(f"loading ledger: {LEDGER_PATH}")
    df = pl.read_parquet(LEDGER_PATH)
    rows = df.to_dicts()
    log(f"ledger rows: N={len(rows)}")

    vix_present = [r.get("vix_entry") for r in rows if r.get("vix_entry") is not None]
    vix_has_coverage = (len(vix_present) / len(rows)) >= 0.90 if rows else False
    log(f"VIX coverage in loaded ledger: {len(vix_present)}/{len(rows)} rows "
        f"({'OK' if vix_has_coverage else 'SKIPPED-NO-DATA -- VIX-band breakdowns will be empty'})")

    liquid_rows = [r for r in rows if r.get("liquid_ge5")]
    all_rows = rows
    log(f"liquid-primary N={len(liquid_rows)}  all-tier N={len(all_rows)}")

    headline = compute_population_metrics(liquid_rows, "liquid-primary (HEADLINE)", vix_has_coverage)
    appendix = compute_population_metrics(all_rows, "all-tier (APPENDIX)", vix_has_coverage)

    n_floor_pass = len(liquid_rows) >= LIQUID_N_FLOOR
    flags = evaluate_flags(headline, n_floor_pass)

    report_text = build_report_text(headline, appendix, flags, vix_has_coverage, meta)

    log("\n" + report_text)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    snap_ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    snap_path = os.path.join(RESULTS_DIR, f"replay_results_{snap_ts}.txt")
    with open(snap_path, "w", encoding="ascii", errors="backslashreplace") as f:
        f.write(report_text)
    log(f"\nwrote {snap_path}")

    update_design_results(report_text)
    log(f"appended results to {DESIGN_PATH}")

    elapsed = time.time() - t_start
    log(f"\n=== DONE in {elapsed:.1f}s ===")
    outcome = (f"SUCCESS liquid_n={len(liquid_rows)} all_n={len(all_rows)} "
               f"flags=[FILL_MODEL_OPTIMISTIC={flags.get('FILL_MODEL_OPTIMISTIC')},"
               f"PREMIUM_MODEL_CHEAP={flags.get('PREMIUM_MODEL_CHEAP')},"
               f"report_only={flags.get('report_only')}] elapsed={elapsed:.1f}s")
    _append_attempt_log(outcome)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _append_attempt_log(f"FAILED: {repr(e)[:300]}")
        raise
