"""Runtime helpers for WR-calibrated, log-smoothed earnings boost (Phase 3C ship).

Imported by monte_carlo.py / backtest_cascade.py / trader.py to mutate
signal.overall in place at signal-load time. The boost depends on:
  - calibrated lift table: per (side, cohort, bucket) empirical WR15 lift
  - per-signal earnings proximity (days to next earnings)
  - log-smoothed proximity AND strength curves

NOT a scoring.py change — this is a portfolio-stage modification (no
recalculate, no ALGORITHM_VERSION bump). The boost is a pure function of
(signal_date, symbol, current_overall, earnings_calendar, lift_table).

To disable: set EARN_BOOST_ENABLED=0 (default ON post-ship).
"""
from __future__ import annotations
import os, math, json, bisect
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Tuple, List, Dict


# ── Defaults (Phase 3C W5_B0.50 winner) ─────────────────────────────────────
EARN_BOOST_ENABLED        = os.environ.get('EARN_BOOST_ENABLED', '1') == '1'
EARN_BOOST_WINDOW         = int(os.environ.get('EARN_BOOST_WINDOW', '5'))     # trading days lookahead
EARN_BOOST_MAX            = float(os.environ.get('EARN_BOOST_MAX', '0.50'))   # peak boost coefficient
EARN_BOOST_LIFT_NORM_CALL = float(os.environ.get('EARN_BOOST_LIFT_NORM_CALL', '22.3'))
EARN_BOOST_LIFT_NORM_PUT  = float(os.environ.get('EARN_BOOST_LIFT_NORM_PUT',  '16.3'))
EARN_BOOST_MIN_N          = int(os.environ.get('EARN_BOOST_MIN_N', '10'))     # ignore lift cells with N < min_n
EARN_BOOST_PUT_ADMIT      = os.environ.get('EARN_BOOST_PUT_ADMIT', '0') == '1'  # default: NO put admissions

LIFT_TABLE_PATH = Path(__file__).resolve().parent / 'phase_tp3b_lift_table.json'


def _call_bucket(overall: int) -> Optional[str]:
    if overall >= 95: return '95+'
    if overall >= 90: return '90-94'
    if overall >= 85: return '85-89'
    if overall >= 80: return '80-84'
    if overall >= 75: return '75-79'
    if overall >= 70: return '70-74'
    return None


def _put_bucket(overall: int) -> Optional[str]:
    if overall <= 5:  return '0-5'
    if overall <= 10: return '6-10'
    if overall <= 15: return '11-15'
    if overall <= 20: return '16-20'
    if overall <= 25: return '21-25'
    return None


def _cohort_label(d_to_ern: int) -> Optional[str]:
    if d_to_ern <= 1: return 'pre1'
    if d_to_ern <= 3: return 'pre3'
    if d_to_ern <= 7: return 'pre7'
    return None


_LIFT_TABLE_CACHE: Optional[Dict[Tuple[str, str, str], Tuple[float, int]]] = None
_STRENGTH_MAP_CACHE: Optional[Dict[Tuple[str, str, str], float]] = None


def load_lift_table() -> Dict[Tuple[str, str, str], Tuple[float, int]]:
    """Load the calibration table from JSON. Cached."""
    global _LIFT_TABLE_CACHE
    if _LIFT_TABLE_CACHE is not None:
        return _LIFT_TABLE_CACHE
    if not LIFT_TABLE_PATH.exists():
        raise FileNotFoundError(f"Lift table not found at {LIFT_TABLE_PATH}; run phase_tp3b_calibration.py")
    with open(LIFT_TABLE_PATH) as f:
        raw = json.load(f)
    table = {}
    for k, v in raw.items():
        side, cohort, bucket = k.split('|')
        lift, n = v
        table[(side, cohort, bucket)] = (float(lift), int(n))
    _LIFT_TABLE_CACHE = table
    return table


def build_strength_map() -> Dict[Tuple[str, str, str], float]:
    """Pre-compute strength = log(1+lift)/log(1+lift_norm) per cell, clamped to [0,1].
    Cells with lift <= 0 or N < min_n get strength=0 (no boost)."""
    global _STRENGTH_MAP_CACHE
    if _STRENGTH_MAP_CACHE is not None:
        return _STRENGTH_MAP_CACHE
    table = load_lift_table()
    strength_map = {}
    log_norm_call = math.log(1 + EARN_BOOST_LIFT_NORM_CALL)
    log_norm_put  = math.log(1 + EARN_BOOST_LIFT_NORM_PUT)
    for (side, cohort, bucket), (lift, n) in table.items():
        if n < EARN_BOOST_MIN_N or lift <= 0:
            continue
        log_norm = log_norm_call if side == 'low' else log_norm_put
        strength = min(1.0, math.log(1 + lift) / log_norm)
        strength_map[(side, cohort, bucket)] = strength
    _STRENGTH_MAP_CACHE = strength_map
    return strength_map


def _sig_symbol(sig) -> str:
    """Support both `symbol_id` (monte_carlo path) and `symbol` (backtest_cascade path)."""
    return getattr(sig, 'symbol_id', None) or sig.symbol


def load_earnings_for_signals(signals, EarningsDate, d_start: date, d_end: date) -> Dict[str, List[date]]:
    """Build {symbol: sorted [earnings_dates]} for the relevant universe."""
    syms = list({_sig_symbol(s) for s in signals})
    if not syms:
        return {}
    rows = list(
        EarningsDate.select(EarningsDate.symbol, EarningsDate.date)
        .where(
            EarningsDate.symbol.in_(syms),
            EarningsDate.date >= d_start - timedelta(days=10),
            EarningsDate.date <= d_end + timedelta(days=EARN_BOOST_WINDOW + 10),
        )
        .tuples()
    )
    by_sym = defaultdict(list)
    for sym, d in rows:
        by_sym[sym].append(d)
    for sym in by_sym:
        by_sym[sym].sort()
    return by_sym


def days_to_next_earnings(by_sym: Dict[str, List[date]], symbol: str, signal_date: date,
                          window: int) -> Optional[int]:
    """Smallest non-negative calendar-day delta to next earnings within [0, window]."""
    eds = by_sym.get(symbol)
    if not eds:
        return None
    # Binary search: first earnings >= signal_date
    idx = bisect.bisect_left(eds, signal_date)
    if idx >= len(eds):
        return None
    delta = (eds[idx] - signal_date).days
    if 0 <= delta <= window:
        return delta
    return None


def apply_earnings_boost(signals, side: str, by_sym: Dict[str, List[date]],
                         verbose: bool = False) -> int:
    """Mutate `signals[i].overall` in place if boost applies. Returns count of
    signals modified. Idempotent if called twice with same lift table — but
    don't call twice (overall would compound).

    side: 'call' or 'put'.
    """
    if not EARN_BOOST_ENABLED:
        return 0
    strength_map = build_strength_map()
    side_label = 'low' if side == 'call' else 'high'
    log_w_plus_1 = math.log(EARN_BOOST_WINDOW + 1)

    boosted = 0
    promoted_to_75plus = 0
    promoted_to_90plus = 0
    for sig in signals:
        sym = _sig_symbol(sig)
        d_to_ern = days_to_next_earnings(by_sym, sym, sig.date, EARN_BOOST_WINDOW)
        if d_to_ern is None:
            continue
        cohort = _cohort_label(d_to_ern)
        if cohort is None:
            continue
        bucket = _call_bucket(sig.overall) if side == 'call' else _put_bucket(sig.overall)
        if bucket is None:
            # not in qualifying range; admit only if put_admit (default False)
            if side == 'put' and not EARN_BOOST_PUT_ADMIT:
                continue
            # calls: never admit boundary signals (no lift table for sub-70)
            continue
        strength = strength_map.get((side_label, cohort, bucket), 0.0)
        if strength <= 0.0:
            continue
        proximity = math.log(EARN_BOOST_WINDOW + 1 - d_to_ern) / log_w_plus_1
        boost_mult = 1.0 + EARN_BOOST_MAX * proximity * strength
        new_overall = max(0, min(100, round(50 + (sig.overall - 50) * boost_mult)))
        if new_overall != sig.overall:
            old = sig.overall
            sig.overall = new_overall
            boosted += 1
            if side == 'call':
                if old < 75 and new_overall >= 75:
                    promoted_to_75plus += 1
                if old < 90 and new_overall >= 90:
                    promoted_to_90plus += 1

    if verbose:
        print(f"  EARN_BOOST [{side}]: boosted {boosted}/{len(signals)} signals "
              f"(W={EARN_BOOST_WINDOW}, MAX={EARN_BOOST_MAX}); "
              f"promoted→75+: {promoted_to_75plus}, →90+: {promoted_to_90plus}")
    return boosted


def boost_namedtuples(signals, side: str, by_sym: Dict[str, List[date]],
                      verbose: bool = False):
    """Variant of apply_earnings_boost for immutable namedtuples (uses _replace).
    Returns (new_list, boosted_count). Use this from trader.py CLI backtest.
    """
    if not EARN_BOOST_ENABLED or not signals:
        return list(signals), 0
    strength_map = build_strength_map()
    side_label = 'low' if side == 'call' else 'high'
    log_w_plus_1 = math.log(EARN_BOOST_WINDOW + 1)
    out = []
    boosted = 0
    for sig in signals:
        sym = _sig_symbol(sig)
        d_to_ern = days_to_next_earnings(by_sym, sym, sig.date, EARN_BOOST_WINDOW)
        if d_to_ern is None:
            out.append(sig); continue
        cohort = _cohort_label(d_to_ern)
        if cohort is None:
            out.append(sig); continue
        ov = int(sig.overall)
        bucket = _call_bucket(ov) if side == 'call' else _put_bucket(ov)
        if bucket is None:
            if side == 'put' and not EARN_BOOST_PUT_ADMIT:
                out.append(sig); continue
            out.append(sig); continue
        strength = strength_map.get((side_label, cohort, bucket), 0.0)
        if strength <= 0.0:
            out.append(sig); continue
        proximity = math.log(EARN_BOOST_WINDOW + 1 - d_to_ern) / log_w_plus_1
        boost_mult = 1.0 + EARN_BOOST_MAX * proximity * strength
        new_overall = max(0, min(100, round(50 + (ov - 50) * boost_mult)))
        if new_overall != ov:
            out.append(sig._replace(overall=new_overall))
            boosted += 1
        else:
            out.append(sig)
    if verbose and boosted:
        print(f"  EARN_BOOST [{side}] (namedtuples): boosted {boosted}/{len(signals)} signals")
    return out, boosted
