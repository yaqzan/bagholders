"""experiments/short_premium/crash_stress.py -- MODELED crash-window stress
test for the short-premium study (see PREREGISTRATION.md "Portfolio phase"
and CLAUDE.md's blunt framing: "the real-price window (2022-08..now) contains
no COVID-class crash, and short premium's defining risk is exactly that
tail"). This script does NOT touch real option prices -- it prices a
synthetic short put at every 75+ v74 signal inside three historical windows
using a calibrated Black-Scholes model, marks it daily under an IV-expansion
scenario grid, and replays the resulting positions through the SAME
sequential portfolio engine (_sim_core.py) that portfolio_sim.py uses on the
real 2022-08+ ledger.

    ****************************************************************
    * MODELED STRESS -- premiums and marks are model-derived; the  *
    * measured region is 2022-08+ only.                            *
    ****************************************************************

WHAT THIS BUILDS
-----------------
Real mode (--run, MySQL peewee, single-threaded reads, never Score.id --
composite key (symbol, date, version_id)):
  1. per window, pulls v74 Score rows with overall>=75 in [start,end]; skips
     (reports, doesn't fabricate) the window if supply < MIN_WINDOW_SIGNALS.
  2. for each signal, pulls that symbol's PriceHistory close series (ADJUSTED
     space, consistently on BOTH strike and path -- the G51 trap does not
     bite here because both sides of this synthetic contract share the one
     adjusted-price space; there is no real strike to collide with).
  3. sigma_entry = 20d realized vol (repo convention, percent/day) -> sigma_ann
     per the locked spec: vol_daily * sqrt(252) / 100.
  4. synthetic short put at K = target_moneyness * S0, calibrated premium =
     BS_put(S0,K,T,sigma_entry) * PREMIUM_ANCHOR_MULT (1.022, the validated
     real/model anchor from experiments/polygon_real_premium) * HAIRCUT
     (0.90) on receive.
  5. daily mark = max(BS_put(S_t,K,T-t,sigma_entry*ivmult_t), intrinsic_t) *
     PREMIUM_ANCHOR_MULT, walked day-by-day against the SAME exit-policy grid
     semantics as short_ledger.py (TP/SL thresholds vs the un-haircut entry
     premium, SL beats TP same-bar, expiry settles at raw intrinsic). Since
     marks are modeled daily CLOSES (no intraday low), TP checks are
     PESSIMISTIC for the seller relative to short_ledger's real low-based
     TP (real intraday touches would trigger more early profit-taking than
     this close-only proxy captures) -- SL checks are unaffected (short_ledger
     already used close for SL).
  6. replays through _sim_core.replay()/jackknife() -- IDENTICAL capital-
     allocation/margin-call machinery as portfolio_sim.py.

Selftest (--selftest) exercises the PURE pieces offline: the BS pricer (put-
call parity), the ivmult ramp shape, a hand-built crash path (assignment
loss > premium -- the entire point of this exercise), and the window-supply
guard. It does not touch MySQL.

IV-EXPANSION SCENARIO GRID (locked, see PREREGISTRATION.md "Portfolio
phase" + the crash_stress brief): ivmult_t in {1.0 flat, ramp to 2.0, ramp to
3.0}, linear ramp from 1.0 starting at the window's VIX-spike segment start,
peaking at the segment end, then linear decay back to 1.0 over 60 market
days. Segment dates are HARDCODED per window (no VIX data pulled for
1995-2008 history):
    covid:  ramp 2020-02-19 -> peak 2020-03-23, then 60d decay
    gfc:    ramp 2008-09-01 -> peak 2008-11-20, then 60d decay
    dotcom: no clean single VIX-spike segment (prolonged grind, not a spike)
            -- DEVIATION from the ramp grid: dotcom runs ONLY a flat 1.5x
            plateau for the whole window, not the {1,2,3} ramp scenarios.
            Documented explicitly, not silently substituted.

TRAPS HONORED
  G5  ASCII-only stdout (this docstring's box above uses plain '*', not any
      unicode box-drawing character).
  DB reads single-threaded, Score via (symbol,date,version_id), never
  Score.id, per database/models/core.py's composite primary key.
  Long compute must NOT be launched by the builder -- --run is real-DB +
  potentially large grid; only --selftest is exercised here.

USAGE
-----
    python experiments/short_premium/crash_stress.py --selftest
    python experiments/short_premium/crash_stress.py --run --window covid --grid
"""
from __future__ import annotations

import argparse
import datetime as _dt
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
assert (_ROOT / "strategy_config.py").exists(), (
    f"sys.path[0] pin didn't land on the repo root: {_ROOT}"
)
_HERE_DIR = str(_HERE.parent)
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

import short_ledger as SL                    # noqa: E402  (TP_GRID/SL_GRID/POLICIES reuse)
import _sim_core as SC                        # noqa: E402

CACHE_DIR = SL.CACHE_DIR
STRESS_DIR = CACHE_DIR / "crash_stress"

VERSION_ID = 74
MIN_WINDOW_SIGNALS = 30
VOL_LOOKBACK = 20                 # locked: 20d realized vol, not the repo's tunable default
R_RATE = 0.04
PREMIUM_ANCHOR_MULT = 1.022       # validated real/model anchor (reference_polygon_real_premium_ledger.md)
HAIRCUT = 0.90
START_CAPITAL = 100_000.0

MONEYNESS_GRID = [1.00, 0.95, 0.90, 0.85, 0.80]
DTE_BANDS = {"d15": 12, "d30": 30, "d60": 60}   # target calendar DTE, mirrors pull_short.BAND_DEFS

MARGIN_BUDGET_FRACS = [0.10, 0.20, 0.33, 0.50]
PER_TRADE_CAP_FRACS = [0.02, 0.05, 0.10]
MAX_CONCURRENT_GRID = [8, 14, 20]
DEFAULT_SIZING = dict(margin_budget_frac=0.20, per_trade_cap_frac=0.05, max_concurrent=14)

WINDOWS = {
    "covid":  dict(start=_dt.date(2019, 11, 1), end=_dt.date(2020, 6, 30)),
    "gfc":    dict(start=_dt.date(2008, 6, 1), end=_dt.date(2009, 6, 30)),
    "dotcom": dict(start=_dt.date(2000, 3, 1), end=_dt.date(2001, 6, 30)),
}

# ramp/peak segment per window (covid/gfc use the {1,2,3}x ramp grid; dotcom
# has no clean spike segment and is special-cased to a flat 1.5x plateau --
# see module docstring "DEVIATION").
SEGMENTS = {
    "covid": dict(ramp_start=_dt.date(2020, 2, 19), peak=_dt.date(2020, 3, 23), decay_days=60),
    "gfc":   dict(ramp_start=_dt.date(2008, 9, 1), peak=_dt.date(2008, 11, 20), decay_days=60),
}
DOTCOM_PLATEAU_MULT = 1.5

SCENARIOS = {"flat": 1.0, "ramp2x": 2.0, "ramp3x": 3.0}

BANNER = (
    "*" * 66 + "\n"
    "* MODELED STRESS -- premiums and marks are model-derived;      *\n"
    "* measured region is 2022-08+ only.                            *\n"
    + "*" * 66
)


def _log(msg: str) -> None:
    print(msg, flush=True)


# ===========================================================================
# Section 1 -- pure math: normal CDF, Black-Scholes put/call, put-call parity
# ===========================================================================

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_put(S: float, K: float, T: float, sigma: float, r: float = R_RATE) -> float:
    """European BS put, per-share. T in years. Degenerate guards: T<=0 or
    sigma<=0 -> pure intrinsic (no time/vol value left)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_call(S: float, K: float, T: float, sigma: float, r: float = R_RATE) -> float:
    """European BS call, per-share -- used only for the put-call parity
    selftest (this module never trades a synthetic call)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _margin_put_scalar(S: float, K: float, prem: float) -> float:
    """Scalar mirror of short_ledger._margin_expr's PUT branch, bit-for-bit:
        max(0.20*S - max(S-K,0), 0.10*K) + prem
    Kept in scalar form (rather than imported) because this module's daily
    walk is a plain Python loop over DB-sourced per-symbol series, not a
    polars frame -- short_ledger._margin_expr only operates on column
    expressions. If the broker formula in short_ledger.py ever changes, this
    mirror must be updated too (there is no way to share the polars Expr
    with a scalar caller)."""
    term1 = 0.20 * S - max(S - K, 0.0)
    return max(term1, 0.10 * K) + prem


# ===========================================================================
# Section 2 -- ivmult scenario grid
# ===========================================================================

def ivmult_at(window: str, scenario: str, d: _dt.date) -> float:
    if window == "dotcom":
        return DOTCOM_PLATEAU_MULT if scenario == "plateau" else 1.0
    peak_mult = SCENARIOS[scenario]
    if peak_mult <= 1.0:
        return 1.0
    seg = SEGMENTS[window]
    rs, pk, decay = seg["ramp_start"], seg["peak"], seg["decay_days"]
    if d < rs:
        return 1.0
    if d <= pk:
        span = (pk - rs).days
        frac = (d - rs).days / span if span > 0 else 1.0
        return 1.0 + frac * (peak_mult - 1.0)
    days_after = (d - pk).days
    if days_after >= decay:
        return 1.0
    frac = 1.0 - (days_after / decay)
    return 1.0 + frac * (peak_mult - 1.0)


def scenarios_for_window(window: str) -> list[str]:
    return ["flat", "plateau"] if window == "dotcom" else list(SCENARIOS.keys())


# ===========================================================================
# Section 3 -- window signal supply guard
# ===========================================================================

def check_window_supply(window: str, n_signals: int, min_n: int = MIN_WINDOW_SIGNALS):
    if n_signals < min_n:
        return False, f"{window}: only {n_signals} signals (< {min_n} floor) -- SKIPPING window"
    return True, f"{window}: {n_signals} signals -- OK"


# ===========================================================================
# Section 4 -- one synthetic trade's daily walk (pure: dates/closes supplied
# by the caller, real-mode or synthetic-mode alike)
# ===========================================================================

def resolve_synthetic_trade(dates: list, closes: list, idx0: int, S0: float, sigma_entry: float,
                             moneyness: float, dte_cal: int, window: str, scenario: str,
                             tp_frac, sl_mult, r: float = R_RATE) -> dict:
    """dates/closes: this symbol's full ordered close series; idx0 = the
    signal's index into it. Returns the resolved trade + a `daily` list of
    (date, S_t, mark) covering every day from idx0 (entry) through the
    resolution day inclusive -- the raw material for the _sim_core marks
    panel. Mirrors short_ledger's exit-grid semantics (SL beats TP same-day;
    no exit -> expiry intrinsic) with the documented CLOSE-only substitution
    for TP (see module docstring)."""
    K = moneyness * S0
    expiry_date = dates[idx0] + _dt.timedelta(days=dte_cal)
    T0 = dte_cal / 365.0
    entry_raw = bs_put(S0, K, T0, sigma_entry, r)
    entry_premium = entry_raw * PREMIUM_ANCHOR_MULT
    premium_received = entry_premium * HAIRCUT

    daily = [(dates[idx0], S0, entry_premium)]
    exit_reason = None
    exit_date = None
    buyback = None
    j = idx0
    while j + 1 < len(dates) and dates[j + 1] <= expiry_date:
        j += 1
        d_t, S_t = dates[j], closes[j]
        T_t = max((expiry_date - d_t).days, 0) / 365.0
        sigma_t = sigma_entry * ivmult_at(window, scenario, d_t)
        intrinsic = max(K - S_t, 0.0)
        mark_raw = bs_put(S_t, K, T_t, sigma_t, r) if T_t > 0 else intrinsic
        mark = max(mark_raw, intrinsic) * PREMIUM_ANCHOR_MULT
        daily.append((d_t, S_t, mark))
        sl_hit = sl_mult is not None and mark >= sl_mult * entry_premium
        tp_hit = tp_frac is not None and mark <= tp_frac * entry_premium
        if sl_hit:
            exit_reason, exit_date, buyback = "sl", d_t, mark * (2.0 - HAIRCUT)
            break
        if tp_hit:
            exit_reason, exit_date, buyback = "tp", d_t, tp_frac * entry_premium
            break

    if exit_reason is None:
        S_T = closes[j]
        intrinsic = max(K - S_T, 0.0)
        exit_reason = "expiry_itm" if intrinsic > 0 else "expiry_otm"
        exit_date = dates[j]
        buyback = intrinsic     # settlement, no haircut -- same canon as short_ledger

    pnl_share = premium_received - buyback
    return dict(
        exit_reason=exit_reason, exit_date=exit_date, entry_premium=entry_premium,
        premium_received=premium_received, buyback=buyback, pnl_share=pnl_share,
        K=K, S0=S0, sigma_entry=sigma_entry, dte_cal=dte_cal,
        entry_idx=idx0, exit_idx=j, daily=daily,
    )


# ===========================================================================
# Section 5 -- realized vol (locked 20d, percent/day -- same formula as
# experiments/polygon_real_premium/pull.realized_vol, fixed lookback here)
# ===========================================================================

def realized_vol_20d(closes: list, base_idx: int, lookback: int = VOL_LOOKBACK):
    if base_idx < lookback:
        return None
    rets = []
    for j in range(base_idx - lookback + 1, base_idx + 1):
        prev = closes[j - 1]
        if prev and prev > 0:
            rets.append((closes[j] - prev) / prev)
    if len(rets) < lookback // 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / len(rets)
    return math.sqrt(var) * 100.0   # percent/day, matches the repo convention


def sigma_ann_from_vol_daily_pct(vol_daily_pct: float) -> float:
    return (vol_daily_pct / 100.0) * math.sqrt(252.0)


# ===========================================================================
# Section 6 -- real-mode: pull v74 75+ signals + PriceHistory closes
# ===========================================================================

def pull_window_signals(window: str, min_n: int = MIN_WINDOW_SIGNALS):
    """MySQL, single-threaded. Returns (signals: list[dict], ok: bool, msg: str).
    signals rows: symbol, signal_date, S0, sigma_entry, dates(list), closes(list),
    idx0 (index of signal_date in dates/closes)."""
    from database.models.core import Score, AlgorithmVersion
    from database.models.technical import PriceHistory

    win = WINDOWS[window]
    ver = AlgorithmVersion.get_by_id(VERSION_ID)
    q = (Score.select(Score.symbol, Score.date, Score.overall)
         .where(Score.version == ver, Score.date >= win["start"], Score.date <= win["end"],
                Score.overall >= 75)
         .order_by(Score.date, Score.symbol))
    raw = [{"symbol": s.symbol_id, "signal_date": s.date} for s in q]
    ok, msg = check_window_supply(window, len(raw), min_n)
    _log(f"[crash_stress] {msg}")
    if not ok:
        return [], False, msg

    symbols = sorted(set(r["symbol"] for r in raw))
    series: dict[str, tuple[list, list]] = {}
    for sym in symbols:
        ph = list(PriceHistory.select(PriceHistory.date, PriceHistory.close)
                   .where(PriceHistory.symbol == sym)
                   .order_by(PriceHistory.date))
        series[sym] = ([r.date for r in ph], [float(r.close) for r in ph])

    signals = []
    for r in raw:
        dates, closes = series.get(r["symbol"], ([], []))
        try:
            idx0 = dates.index(r["signal_date"])
        except ValueError:
            continue
        vol = realized_vol_20d(closes, idx0)
        if vol is None:
            continue
        signals.append(dict(
            symbol=r["symbol"], signal_date=r["signal_date"], S0=closes[idx0],
            sigma_entry=sigma_ann_from_vol_daily_pct(vol), dates=dates, closes=closes, idx0=idx0,
        ))
    return signals, True, msg


# ===========================================================================
# Section 7 -- build _sim_core positions + marks_lookup for one
# (window, scenario, moneyness, band, policy) cell
# ===========================================================================

def build_cell(signals: list, window: str, scenario: str, moneyness: float, dte_cal: int,
               policy: str) -> tuple[list, dict, list]:
    policy_map = {label: (tp, sl) for label, tp, sl in SL.POLICIES}
    tp_frac, sl_mult = policy_map[policy]

    all_dates = set()
    resolved = []
    for sig in signals:
        idx0 = sig["idx0"]
        if idx0 + 1 >= len(sig["dates"]):
            continue
        r = resolve_synthetic_trade(
            sig["dates"], sig["closes"], idx0, sig["S0"], sig["sigma_entry"],
            moneyness, dte_cal, window, scenario, tp_frac, sl_mult,
        )
        r["symbol"] = sig["symbol"]
        r["contract_id"] = f"{sig['symbol']}|{sig['signal_date'].isoformat()}|{moneyness}|{dte_cal}"
        resolved.append(r)
        for d, _, _ in r["daily"]:
            all_dates.add(d)

    if not resolved:
        return [], {}, []

    cal_days = sorted(all_dates)
    cal_idx = {d: i for i, d in enumerate(cal_days)}

    positions = []
    marks_lookup: dict[str, dict[int, tuple[float, float]]] = {}
    for r in resolved:
        cid = r["contract_id"]
        entry_credit = r["premium_received"]
        margin0 = _margin_put_scalar(r["S0"], r["K"], r["entry_premium"])
        day_marks = {}
        for d, S_t, mark in r["daily"]:
            di = cal_idx[d]
            margin_t = _margin_put_scalar(S_t, r["K"], mark)
            day_marks[di] = (margin_t, mark)
        marks_lookup[cid] = day_marks
        entry_di = cal_idx[r["daily"][0][0]]
        exit_di = cal_idx[r["daily"][-1][0]]
        positions.append(dict(
            contract_id=cid, symbol=r["symbol"], entry_day_idx=entry_di, exit_day_idx=exit_di,
            margin0_per_share=margin0, entry_credit_per_share=entry_credit,
            ledger_pnl_per_share=r["pnl_share"], kind="short",
        ))

    day_seq = [(i, d) for i, d in enumerate(cal_days)]
    return positions, marks_lookup, day_seq


# ===========================================================================
# Section 8 -- real-mode run + ASCII summary
# ===========================================================================

def sizing_grid(full: bool) -> list[dict]:
    if not full:
        return [dict(DEFAULT_SIZING)]
    return [dict(margin_budget_frac=b, per_trade_cap_frac=c, max_concurrent=m)
            for b in MARGIN_BUDGET_FRACS for c in PER_TRADE_CAP_FRACS for m in MAX_CONCURRENT_GRID]


def run(windows: list[str], full_grid: bool, policies: list[str] | None = None) -> None:
    _log(BANNER)
    grid = sizing_grid(full_grid)
    pol_list = policies or [p[0] for p in SL.POLICIES]
    rows = []
    for window in windows:
        signals, ok, msg = pull_window_signals(window)
        if not ok:
            continue
        for scenario in scenarios_for_window(window):
            for mny in MONEYNESS_GRID:
                for band, dte_cal in DTE_BANDS.items():
                    for policy in pol_list:
                        positions, marks_lookup, day_seq = build_cell(
                            signals, window, scenario, mny, dte_cal, policy,
                        )
                        if not positions:
                            continue
                        for cfg in grid:
                            config = SC.SimConfig(
                                start_capital=START_CAPITAL, margin_budget_frac=cfg["margin_budget_frac"],
                                per_trade_cap_frac=cfg["per_trade_cap_frac"], max_concurrent=cfg["max_concurrent"],
                                haircut=HAIRCUT,
                                tag=f"{window}_{scenario}_{mny}_{band}_{policy}",
                            )
                            res = SC.replay(positions, marks_lookup, day_seq, config)
                            _log(f"  [{config.tag:<32}] b={cfg['margin_budget_frac']:.2f} "
                                 f"c={cfg['per_trade_cap_frac']:.2f} m={cfg['max_concurrent']:>2} "
                                 f"final_eq={res['final_equity']:>12,.0f} worstDD={res['worst_dd']:.3f} "
                                 f"collapse={res['collapse']} mcalls={res['margin_call_days']} "
                                 f"n_pos={res['n_positions']}")
                            row = {k: v for k, v in res.items()
                                   if k not in ("equity_curve", "per_year", "collapse_day_idx")}
                            row.update(window=window, scenario=scenario, moneyness=mny, band=band,
                                       policy=policy, **{f"cfg_{k}": v for k, v in cfg.items()})
                            rows.append(row)
    if rows:
        import polars as pl
        STRESS_DIR.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(STRESS_DIR / "results.parquet")
        _log(f"\n[crash_stress] wrote {STRESS_DIR / 'results.parquet'} ({len(rows)} rows)")
    _log(BANNER)


# ===========================================================================
# Section 9 -- selftest (offline, no DB/network)
# ===========================================================================

def _weekday_seq(n, start=_dt.date(2020, 1, 2)):
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d = d + _dt.timedelta(days=1)
    return days


def selftest() -> bool:
    results = {}

    # -----------------------------------------------------------------
    # (f) BS put price sanity: put-call parity C - P = S - K*exp(-rT)
    # -----------------------------------------------------------------
    S, K, T, sigma, r = 100.0, 105.0, 0.5, 0.35, R_RATE
    P = bs_put(S, K, T, sigma, r)
    C = bs_call(S, K, T, sigma, r)
    lhs = C - P
    rhs = S - K * math.exp(-r * T)
    ok_f1 = abs(lhs - rhs) < 1e-9
    # sanity bounds: put price in [intrinsic, K*exp(-rT)]
    ok_f2 = (max(K - S, 0.0) - 1e-9) <= P <= (K * math.exp(-r * T) + 1e-9)
    ok_f = ok_f1 and ok_f2
    results["f_bs_put_call_parity"] = ok_f
    _log(f"[selftest f] C-P={lhs:.6f} S-Ke^-rT={rhs:.6f} P={P:.4f} -> {'PASS' if ok_f else 'FAIL'}")

    # -----------------------------------------------------------------
    # (g) ivmult ramp shape
    # -----------------------------------------------------------------
    seg = SEGMENTS["covid"]
    before = ivmult_at("covid", "ramp3x", seg["ramp_start"] - _dt.timedelta(days=5))
    at_peak = ivmult_at("covid", "ramp3x", seg["peak"])
    mid_ramp = ivmult_at("covid", "ramp3x", seg["ramp_start"] + (seg["peak"] - seg["ramp_start"]) / 2)
    after_decay = ivmult_at("covid", "ramp3x", seg["peak"] + _dt.timedelta(days=seg["decay_days"] + 1))
    mid_decay = ivmult_at("covid", "ramp3x", seg["peak"] + _dt.timedelta(days=seg["decay_days"] // 2))
    flat_always_one = ivmult_at("covid", "flat", seg["peak"])
    dotcom_plateau = ivmult_at("dotcom", "plateau", WINDOWS["dotcom"]["start"])
    dotcom_flat = ivmult_at("dotcom", "flat", WINDOWS["dotcom"]["start"])
    ok_g = (
        abs(before - 1.0) < 1e-9
        and abs(at_peak - 3.0) < 1e-9
        and 1.0 < mid_ramp < 3.0
        and abs(after_decay - 1.0) < 1e-9
        and 1.0 < mid_decay < 3.0
        and mid_decay < at_peak
        and abs(flat_always_one - 1.0) < 1e-9
        and abs(dotcom_plateau - 1.5) < 1e-9
        and abs(dotcom_flat - 1.0) < 1e-9
    )
    results["g_ivmult_ramp_shape"] = ok_g
    _log(f"[selftest g] before={before:.3f} mid_ramp={mid_ramp:.3f} peak={at_peak:.3f} "
         f"mid_decay={mid_decay:.3f} after_decay={after_decay:.3f} dotcom_plateau={dotcom_plateau:.3f} "
         f"-> {'PASS' if ok_g else 'FAIL'}")

    # -----------------------------------------------------------------
    # (h) synthetic crash path -> assignment loss > premium
    # -----------------------------------------------------------------
    days = _weekday_seq(40)
    # S drifts flat then crashes hard through the put's strike by expiry
    closes_flat = [100.0] * 10
    n_crash = 30
    closes_crash = [100.0 - 30.0 * (i / (n_crash - 1)) for i in range(n_crash)]   # 100 -> 70 linear
    closes = closes_flat + closes_crash
    idx0 = 9   # last flat day = signal day, S0=100
    r_h = resolve_synthetic_trade(
        days, closes, idx0, S0=100.0, sigma_entry=0.30, moneyness=1.00, dte_cal=25,
        window="covid", scenario="ramp3x", tp_frac=None, sl_mult=None,   # no early exit -> forced to expiry
    )
    ok_h1 = r_h["exit_reason"] == "expiry_itm"
    ok_h2 = r_h["buyback"] > r_h["premium_received"]     # assignment loss exceeds premium collected
    ok_h3 = r_h["pnl_share"] < 0
    ok_h = ok_h1 and ok_h2 and ok_h3
    results["h_crash_assignment_loss_gt_premium"] = ok_h
    _log(f"[selftest h] exit_reason={r_h['exit_reason']} premium_received={r_h['premium_received']:.4f} "
         f"buyback(intrinsic)={r_h['buyback']:.4f} pnl_share={r_h['pnl_share']:.4f} "
         f"-> {'PASS' if ok_h else 'FAIL'}")

    # sub-case: the SAME setup with an early SL should exit before the full
    # crash lands (demonstrating the exit grid still functions in stress)
    r_h2 = resolve_synthetic_trade(
        days, closes, idx0, S0=100.0, sigma_entry=0.30, moneyness=1.00, dte_cal=25,
        window="covid", scenario="ramp3x", tp_frac=None, sl_mult=2.0,
    )
    ok_h2b = r_h2["exit_reason"] == "sl" and r_h2["exit_idx"] < r_h["exit_idx"]
    results["h2_sl_fires_before_full_crash"] = ok_h2b
    _log(f"[selftest h2] sl exit_reason={r_h2['exit_reason']} exit_idx={r_h2['exit_idx']} "
         f"(vs no-exit expiry idx={r_h['exit_idx']}) -> {'PASS' if ok_h2b else 'FAIL'}")

    # -----------------------------------------------------------------
    # (i) window-supply guard
    # -----------------------------------------------------------------
    ok_i1, msg_i1 = check_window_supply("covid", 10, min_n=30)
    ok_i2, msg_i2 = check_window_supply("covid", 45, min_n=30)
    ok_i = (ok_i1 is False) and ("SKIP" in msg_i1) and (ok_i2 is True) and ("OK" in msg_i2)
    results["i_window_supply_guard"] = ok_i
    _log(f"[selftest i] n=10 -> ok={ok_i1} msg={msg_i1!r} | n=45 -> ok={ok_i2} msg={msg_i2!r} "
         f"-> {'PASS' if ok_i else 'FAIL'}")

    # -----------------------------------------------------------------
    # (j) bonus glue check: feed a resolved synthetic trade through
    # _sim_core.replay end-to-end (not in the required f-i list, but cheap
    # insurance that the real-mode plumbing this selftest can't reach --
    # build_cell -- would actually work on resolved output)
    # -----------------------------------------------------------------
    K = 1.00 * 100.0
    margin0 = _margin_put_scalar(100.0, K, r_h["entry_premium"])
    cal_idx = {d: i for i, d in enumerate(sorted({d for d, _, _ in r_h["daily"]}))}
    marks = {}
    for d, S_t, mark in r_h["daily"]:
        marks[cal_idx[d]] = (_margin_put_scalar(S_t, K, mark), mark)
    pos = [dict(contract_id="J1", symbol="ZZZ", entry_day_idx=0,
                exit_day_idx=cal_idx[r_h["exit_date"]], margin0_per_share=margin0,
                entry_credit_per_share=r_h["premium_received"], ledger_pnl_per_share=r_h["pnl_share"],
                kind="short")]
    day_seq_j = sorted((i, d) for d, i in cal_idx.items())
    cfg_j = SC.SimConfig(start_capital=100_000.0, margin_budget_frac=1.0, per_trade_cap_frac=0.5,
                          max_concurrent=5, haircut=HAIRCUT)
    res_j = SC.replay(pos, marks, day_seq_j, cfg_j)
    ok_j = res_j["final_equity"] < cfg_j.start_capital   # the crash trade should be a net loser through the sim
    results["j_end_to_end_glue_smoke"] = ok_j
    _log(f"[selftest j] final_equity={res_j['final_equity']:,.2f} (start={cfg_j.start_capital:,.0f}) "
         f"-> {'PASS' if ok_j else 'FAIL'}")

    _log("\n" + "=" * 60)
    all_ok = all(results.values())
    for k, v in results.items():
        _log(f"  {k}: {'PASS' if v else 'FAIL'}")
    _log("=" * 60)
    _log(f"SELFTEST {'PASS' if all_ok else 'FAIL'} ({sum(results.values())}/{len(results)})")
    return all_ok


# ===========================================================================
# CLI
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(description="short_premium MODELED crash-window stress test")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--window", action="append", choices=list(WINDOWS.keys()),
                     help="repeatable; default = all three windows")
    ap.add_argument("--policy", action="append", help="repeatable; default = all 9 exit policies")
    ap.add_argument("--grid", action="store_true", help="run the full sizing grid (36 combos/cell)")
    args = ap.parse_args()

    if args.selftest:
        ok = selftest()
        sys.exit(0 if ok else 1)
    if args.run:
        windows = args.window or list(WINDOWS.keys())
        run(windows, args.grid, policies=args.policy)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
