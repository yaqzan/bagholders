"""Numba JIT barrier walk for cache misses.

When a sweep tests barrier parameters not pre-cached (e.g., custom K, M, W),
fall back to this fast forward-walk implementation. Compiles to native code
on first call; ~50-100x faster than the interpreted Python version.

API mirrors the inner _walk_outcome from barrier_cache.py for compatibility.

Return tuple (10 values):
    result      : int   — -1=insufficient, 0=stop/expire, 1=win(TP)
    exit_offset : int   — trading bars from base to exit
    exit_close  : float — barrier price (TP/SL) or last close (expire)
    exit_return : float — side-adjusted % return
    mae         : float — max adverse excursion %
    mfe         : float — max favorable excursion %
    fire_type   : float — 0.0=expire(hard-sell), 1.0=tp, 2.0=sl
    fire_open   : float — open of the bar where barrier fired (0.0 for expire)
    fire_high   : float — high of the bar where barrier fired (0.0 for expire)
    fire_low    : float — low  of the bar where barrier fired (0.0 for expire)
"""
from __future__ import annotations
import numpy as np
import math
import numba


@numba.njit(cache=True, fastmath=True)
def walk_one_put(closes, highs, lows, opens, base_idx, w_bars, sigma_pct, K, M):
    """Single put barrier walk. side='high' (win on drop).

    Returns 10-tuple: (result, exit_offset, exit_close, exit_return, mae, mfe,
                       fire_type, fire_open, fire_high, fire_low)
    fire_type: 0.0=expire, 1.0=tp, 2.0=sl
    """
    if sigma_pct <= 0 or base_idx + 1 >= len(closes):
        return -1, -1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    entry = closes[base_idx]
    if entry <= 0:
        return -1, -1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    t_win  = entry * (1.0 - K * sigma_pct / 100.0)
    t_stop = entry * (1.0 + M * sigma_pct / 100.0)
    end = min(len(closes), base_idx + 1 + w_bars)
    running_max = entry
    running_min = entry
    last_close = -1.0
    last_offset = 0
    for j in range(base_idx + 1, end):
        offset = j - base_idx
        last_offset = offset
        last_close = closes[j]
        hi = highs[j]
        lo = lows[j]
        op = opens[j]
        if hi > running_max: running_max = hi
        if lo < running_min: running_min = lo
        if lo <= t_win:
            ret = (entry - t_win) / entry * 100.0
            return (1, offset, t_win, ret,
                    (running_max - entry) / entry * 100.0,
                    (entry - running_min) / entry * 100.0,
                    1.0, op, hi, lo)
        if hi >= t_stop:
            ret = (entry - t_stop) / entry * 100.0
            return (0, offset, t_stop, ret,
                    (running_max - entry) / entry * 100.0,
                    (entry - running_min) / entry * 100.0,
                    2.0, op, hi, lo)
    if last_close <= 0:
        return -1, -1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    if end < base_idx + 1 + w_bars:
        return -1, -1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    ret = (entry - last_close) / entry * 100.0
    return (0, last_offset, last_close, ret,
            (running_max - entry) / entry * 100.0,
            (entry - running_min) / entry * 100.0,
            0.0, 0.0, 0.0, 0.0)


@numba.njit(cache=True, fastmath=True)
def walk_one_call(closes, highs, lows, opens, base_idx, w_bars, sigma_pct, K, M):
    """Single call barrier walk. side='low' (win on rise).

    Returns 10-tuple: (result, exit_offset, exit_close, exit_return, mae, mfe,
                       fire_type, fire_open, fire_high, fire_low)
    fire_type: 0.0=expire, 1.0=tp, 2.0=sl
    """
    if sigma_pct <= 0 or base_idx + 1 >= len(closes):
        return -1, -1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    entry = closes[base_idx]
    if entry <= 0:
        return -1, -1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    t_win  = entry * (1.0 + K * sigma_pct / 100.0)
    t_stop = entry * (1.0 - M * sigma_pct / 100.0)
    end = min(len(closes), base_idx + 1 + w_bars)
    running_max = entry
    running_min = entry
    last_close = -1.0
    last_offset = 0
    for j in range(base_idx + 1, end):
        offset = j - base_idx
        last_offset = offset
        last_close = closes[j]
        hi = highs[j]
        lo = lows[j]
        op = opens[j]
        if hi > running_max: running_max = hi
        if lo < running_min: running_min = lo
        if hi >= t_win:
            ret = (t_win - entry) / entry * 100.0
            return (1, offset, t_win, ret,
                    (entry - running_min) / entry * 100.0,
                    (running_max - entry) / entry * 100.0,
                    1.0, op, hi, lo)
        if lo <= t_stop:
            ret = (t_stop - entry) / entry * 100.0
            return (0, offset, t_stop, ret,
                    (entry - running_min) / entry * 100.0,
                    (running_max - entry) / entry * 100.0,
                    2.0, op, hi, lo)
    if last_close <= 0:
        return -1, -1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    if end < base_idx + 1 + w_bars:
        return -1, -1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    ret = (last_close - entry) / entry * 100.0
    return (0, last_offset, last_close, ret,
            (entry - running_min) / entry * 100.0,
            (running_max - entry) / entry * 100.0,
            0.0, 0.0, 0.0, 0.0)


@numba.njit(cache=True, parallel=True)
def walk_batch(closes, highs, lows, opens, base_indices, side_codes, w_bars_arr, sigmas, K, M):
    """Batch walk over many (base_idx, side, w_bars, sigma) tuples.

    side_codes: 0 for put (high), 1 for call (low)
    Returns 2D array (N, 10):
        [result, exit_offset, exit_close, exit_return, mae, mfe,
         fire_type, fire_open, fire_high, fire_low]
    result: -1=insufficient, 0=stop/expire, 1=win
    fire_type: 0.0=expire, 1.0=tp, 2.0=sl
    """
    n = len(base_indices)
    out = np.zeros((n, 10), dtype=np.float64)
    for i in numba.prange(n):
        if side_codes[i] == 0:
            r, eo, ec, er, mae, mfe, ft, fo, fh, fl = walk_one_put(
                closes, highs, lows, opens,
                base_indices[i], w_bars_arr[i], sigmas[i], K, M)
        else:
            r, eo, ec, er, mae, mfe, ft, fo, fh, fl = walk_one_call(
                closes, highs, lows, opens,
                base_indices[i], w_bars_arr[i], sigmas[i], K, M)
        out[i, 0] = r
        out[i, 1] = eo
        out[i, 2] = ec
        out[i, 3] = er
        out[i, 4] = mae
        out[i, 5] = mfe
        out[i, 6] = ft
        out[i, 7] = fo
        out[i, 8] = fh
        out[i, 9] = fl
    return out


@numba.njit(cache=True, fastmath=True)
def walk_call_option_outcome(highs, lows, closes, ordinals, sig_i,
                              tp_price, sl_price, deadline_ord):
    """Walk forward bars for a call-option-level barrier (TP_SIGMA / SL_SIGMA
    expressed as price thresholds). Used by backtest_cascade compute_outcome.

    Returns (kind_code, exit_bar, exit_price):
        kind_code: 0=hard, 1=tp, 2=sl, 3=open
        exit_bar:  bars held from signal to exit (0 if open)
        exit_price: barrier hit price (tp/sl) or close at exit (hard/open)
    """
    for j in range(sig_i + 1, len(highs)):
        high = highs[j]
        low  = lows[j]
        bars_held = j - sig_i
        if high >= tp_price:
            return 1, bars_held, tp_price
        if low <= sl_price:
            return 2, bars_held, sl_price
        if ordinals[j] >= deadline_ord:
            return 0, bars_held, closes[j]
    return 3, len(highs) - 1 - sig_i, closes[-1]


@numba.njit(cache=True, fastmath=True)
def walk_put_option_outcome(highs, lows, closes, ordinals, sig_i,
                             tp_price, sl_price, deadline_ord, sl_hold_bars):
    """Walk forward bars for a put-option-level barrier.
    sl_hold_bars: number of bars at the start where SL is suppressed.

    Returns (kind_code, exit_bar, exit_price)."""
    for j in range(sig_i + 1, len(highs)):
        high = highs[j]
        low  = lows[j]
        bars_held = j - sig_i
        if low <= tp_price:
            return 1, bars_held, tp_price
        if (high >= sl_price) and (bars_held > sl_hold_bars):
            return 2, bars_held, sl_price
        if ordinals[j] >= deadline_ord:
            return 0, bars_held, closes[j]
    return 3, len(highs) - 1 - sig_i, closes[-1]


# ─── Dead-hold post-SL mechanism (Spec C, in flight 2026-04-30) ──────────────
#
# When SL fires AND realized option pnl ≤ DEAD_HOLD_TRIGGER_PNL, the position
# is held forward bar-by-bar instead of sold. Each subsequent bar's intraday
# extreme is checked against DEAD_HOLD_POPOUT_PNL — if reached, exit there.
# Otherwise hold to expiry close.
#
# Models real-account behavior: a deep-OTM 1-2 week option has no liquid bid,
# so you can't actually realize the -100% loss the σ-barrier model assumes.
# The position sits in the account; the slot stays occupied; capital stays
# locked. If the underlying recovers within DTE, partial recovery is captured.

@numba.njit(cache=True, fastmath=True, inline='always')
def _opt_pnl_inline(side_sign, u_t, u_0, bars_held, premium_pct, total_dte):
    """Inline option pnl computation (vega=1.0, no IV crush).
    side_sign: +1 for call, -1 for put.
    Returns pnl as fraction of premium, capped at -1.0.
    """
    if u_0 <= 0.0 or premium_pct <= 0.0:
        return 0.0
    move_frac = (u_t - u_0) / u_0
    # delta hardcoded at 0.5 to match production OptionStrategyConfig.DELTA.
    # If a future ship changes DELTA, update this constant too.
    intrinsic = side_sign * 0.5 * move_frac / premium_pct
    bars_eff = bars_held
    if bars_eff < 0: bars_eff = 0
    if bars_eff > total_dte: bars_eff = total_dte
    if total_dte <= 0:
        theta = 0.0
    else:
        theta = math.sqrt((total_dte - bars_eff) / total_dte) - 1.0
    pnl = intrinsic + theta
    if pnl < -1.0:
        return -1.0
    return pnl


@numba.njit(cache=True, fastmath=True)
def walk_call_dead_hold(highs, lows, closes, opens, ordinals, sig_i,
                        tp_price, sl_price, deadline_ord, entry_price,
                        premium_pct, total_dte,
                        trigger_pnl, popout_pnl):
    """Call-side walk with dead-hold post-SL mechanism.

    Returns (kind_code, exit_bar, exit_pnl_pct):
        kind_code: 0=hard_at_expiry, 1=tp, 2=sl_normal, 3=open,
                   4=dead_hold_popout, 5=dead_hold_to_expiry
        exit_bar:  bars held from signal to exit
        exit_pnl_pct: realized option pnl as fraction of premium (-1.0 to +inf)
    """
    n = len(highs)
    in_dead_hold = False
    dh_entry_bar = -1

    for j in range(sig_i + 1, n):
        high = highs[j]
        low  = lows[j]
        op   = opens[j]
        bars_held = j - sig_i

        if not in_dead_hold:
            # Standard barrier check
            if high >= tp_price:
                # TP fires: realized pnl on call at TP barrier (mid-bar fill
                # convention matches backtest_cascade._option_aware_pnl)
                u_fill = (high + low) * 0.5
                pnl = _opt_pnl_inline(1.0, u_fill, entry_price, bars_held,
                                      premium_pct, total_dte)
                return 1, bars_held, pnl
            if low <= sl_price:
                # SL fires: bimodal fill
                if op > sl_price:
                    u_fill = sl_price            # intraday trigger
                else:
                    u_fill = (low + op) * 0.5    # gap-through midpoint
                sl_pnl = _opt_pnl_inline(1.0, u_fill, entry_price, bars_held,
                                         premium_pct, total_dte)
                if sl_pnl > trigger_pnl:
                    # Normal SL exit
                    return 2, bars_held, sl_pnl
                # Convert to dead-hold; do NOT exit here
                in_dead_hold = True
                dh_entry_bar = bars_held
            if ordinals[j] >= deadline_ord:
                # Hard sell at expiry, close-bar fill
                pnl = _opt_pnl_inline(1.0, closes[j], entry_price, bars_held,
                                      premium_pct, total_dte)
                return 0, bars_held, pnl
        else:
            # Dead-hold: monitor intraday high for popout (calls recover when
            # underlying rises, so check high at each bar). If the high's
            # implied option pnl crosses popout_pnl, a GTC limit-sell at the
            # popout level would have filled — so realized pnl = popout_pnl
            # exactly (limit-order semantics). If the bar opens above the
            # popout level (gap up), we'd actually fill at that better open
            # price; capture that with the open-pnl floor.
            high_pnl = _opt_pnl_inline(1.0, high, entry_price, bars_held,
                                       premium_pct, total_dte)
            if high_pnl >= popout_pnl:
                open_pnl = _opt_pnl_inline(1.0, op, entry_price, bars_held,
                                           premium_pct, total_dte)
                fill_pnl = popout_pnl if open_pnl < popout_pnl else open_pnl
                return 4, bars_held, fill_pnl
            if ordinals[j] >= deadline_ord:
                pnl = _opt_pnl_inline(1.0, closes[j], entry_price, bars_held,
                                      premium_pct, total_dte)
                return 5, bars_held, pnl

    # Fell off end of price history
    last_bar = n - 1 - sig_i
    if n > sig_i + 1:
        pnl = _opt_pnl_inline(1.0, closes[n - 1], entry_price, last_bar,
                              premium_pct, total_dte)
    else:
        pnl = 0.0
    return 3, last_bar, pnl


@numba.njit(cache=True, fastmath=True)
def walk_put_dead_hold(highs, lows, closes, opens, ordinals, sig_i,
                       tp_price, sl_price, deadline_ord, entry_price,
                       premium_pct, total_dte,
                       trigger_pnl, popout_pnl, sl_hold_bars):
    """Put-side walk with dead-hold post-SL mechanism.

    sl_hold_bars: bars at start where SL is suppressed (matches put SL hold
    mechanism in production).

    Returns (kind_code, exit_bar, exit_pnl_pct).
    """
    n = len(highs)
    in_dead_hold = False
    dh_entry_bar = -1

    for j in range(sig_i + 1, n):
        high = highs[j]
        low  = lows[j]
        op   = opens[j]
        bars_held = j - sig_i

        if not in_dead_hold:
            if low <= tp_price:
                # Put TP: underlying fell to TP barrier
                u_fill = (high + low) * 0.5
                pnl = _opt_pnl_inline(-1.0, u_fill, entry_price, bars_held,
                                      premium_pct, total_dte)
                return 1, bars_held, pnl
            if (high >= sl_price) and (bars_held > sl_hold_bars):
                # Put SL bimodal fill
                if op < sl_price:
                    u_fill = sl_price
                else:
                    u_fill = (high + op) * 0.5
                sl_pnl = _opt_pnl_inline(-1.0, u_fill, entry_price, bars_held,
                                         premium_pct, total_dte)
                if sl_pnl > trigger_pnl:
                    return 2, bars_held, sl_pnl
                in_dead_hold = True
                dh_entry_bar = bars_held
            if ordinals[j] >= deadline_ord:
                pnl = _opt_pnl_inline(-1.0, closes[j], entry_price, bars_held,
                                      premium_pct, total_dte)
                return 0, bars_held, pnl
        else:
            # Dead-hold for puts: check intraday low (puts recover when
            # underlying falls). Limit-sell semantics — fill at popout_pnl
            # unless the bar's open already exceeded it (gap-down through),
            # in which case fill at the better open price.
            low_pnl = _opt_pnl_inline(-1.0, low, entry_price, bars_held,
                                      premium_pct, total_dte)
            if low_pnl >= popout_pnl:
                open_pnl = _opt_pnl_inline(-1.0, op, entry_price, bars_held,
                                           premium_pct, total_dte)
                fill_pnl = popout_pnl if open_pnl < popout_pnl else open_pnl
                return 4, bars_held, fill_pnl
            if ordinals[j] >= deadline_ord:
                pnl = _opt_pnl_inline(-1.0, closes[j], entry_price, bars_held,
                                      premium_pct, total_dte)
                return 5, bars_held, pnl

    last_bar = n - 1 - sig_i
    if n > sig_i + 1:
        pnl = _opt_pnl_inline(-1.0, closes[n - 1], entry_price, last_bar,
                              premium_pct, total_dte)
    else:
        pnl = 0.0
    return 3, last_bar, pnl


@numba.njit(cache=True, fastmath=True)
def trace_option_pnl_path(highs, lows, closes, opens, sig_i,
                          entry_price, premium_pct, total_dte,
                          side_sign, n_bars_max):
    """Trace per-bar (worst_pnl, best_pnl, close_pnl) over the full hold
    window. Used by experiments/option_recovery_histogram.py to characterize
    the recovery distribution.

    Returns (n_bars_walked, worst_pnl_overall, best_pnl_overall_after_worst).

    'best_pnl_overall_after_worst' = the highest option pnl reached on any
    bar AFTER the bar where the worst pnl was recorded. This is the
    quantity that DEAD_HOLD_POPOUT_PNL is calibrated against.
    """
    n = len(highs)
    end = sig_i + 1 + n_bars_max
    if end > n:
        end = n

    worst_pnl = 1.0e9
    worst_bar = -1
    best_after_worst = -2.0
    best_bar_after = -1

    for j in range(sig_i + 1, end):
        bars_held = j - sig_i
        # Intraday adverse extreme for the side
        if side_sign > 0.0:
            u_adverse = lows[j]
            u_favor   = highs[j]
        else:
            u_adverse = highs[j]
            u_favor   = lows[j]
        adverse_pnl = _opt_pnl_inline(side_sign, u_adverse, entry_price,
                                      bars_held, premium_pct, total_dte)
        favor_pnl = _opt_pnl_inline(side_sign, u_favor, entry_price,
                                    bars_held, premium_pct, total_dte)
        if adverse_pnl < worst_pnl:
            worst_pnl = adverse_pnl
            worst_bar = bars_held
            # Reset 'best after worst' since we have a new worst
            best_after_worst = favor_pnl
            best_bar_after = bars_held
        else:
            if favor_pnl > best_after_worst:
                best_after_worst = favor_pnl
                best_bar_after = bars_held

    if worst_bar < 0:
        return 0, 0.0, 0.0
    return end - sig_i - 1, worst_pnl, best_after_worst


def warmup():
    """Trigger JIT compilation. Call once before timing."""
    n = 50
    closes = np.linspace(100.0, 110.0, n)
    highs  = closes * 1.01
    lows   = closes * 0.99
    opens  = closes * 1.002
    ords   = np.arange(n, dtype=np.int64) + 738000
    walk_one_put(closes, highs, lows, opens, 30, 7, 2.0, 1.0, 2.0)
    walk_one_call(closes, highs, lows, opens, 30, 7, 2.0, 2.0, 5.0)
    walk_batch(closes, highs, lows, opens,
               np.array([30, 31, 32]), np.array([0, 1, 0]),
               np.array([7, 15, 30]), np.array([2.0, 2.5, 3.0]),
               1.0, 2.0)
    walk_call_option_outcome(highs, lows, closes, ords, 30, 105.0, 95.0, ords[40])
    walk_put_option_outcome(highs, lows, closes, ords, 30, 95.0, 105.0, ords[40], 3)
    walk_call_dead_hold(highs, lows, closes, opens, ords, 30,
                        105.0, 95.0, ords[40], 100.0, 0.025, 30, -0.5, -0.3)
    walk_put_dead_hold(highs, lows, closes, opens, ords, 30,
                       95.0, 105.0, ords[40], 100.0, 0.025, 30, -0.5, -0.3, 3)
    trace_option_pnl_path(highs, lows, closes, opens, 30,
                          100.0, 0.025, 30, 1.0, 15)


if __name__ == '__main__':
    import time
    print("Warming up JIT compilation ...")
    warmup()
    print("Done.")

    n = 100000
    np.random.seed(42)
    closes = 100.0 * np.cumprod(1 + np.random.randn(n) * 0.01)
    highs  = closes * (1.0 + np.abs(np.random.randn(n)) * 0.005)
    lows   = closes * (1.0 - np.abs(np.random.randn(n)) * 0.005)
    opens  = closes * (1.0 + np.random.randn(n) * 0.002)

    indices = np.random.randint(60, n - 200, 10000)
    sides   = np.random.randint(0, 2, 10000)
    ws      = np.random.choice([7, 15, 30, 60], 10000)
    sigmas  = np.random.uniform(1.0, 5.0, 10000)

    t0 = time.time()
    out = walk_batch(closes, highs, lows, opens, indices, sides, ws, sigmas, 1.0, 2.0)
    print(f"10,000 batch walks: {(time.time()-t0)*1000:.0f}ms")
    print(f"  win rate:    {(out[:,0]==1).sum()/len(out)*100:.1f}%")
    print(f"  TP fires:    {(out[:,6]==1.0).sum()}")
    print(f"  SL fires:    {(out[:,6]==2.0).sum()}")
    print(f"  Expire:      {(out[:,6]==0.0).sum()}")
    print(f"  insufficient:{(out[:,0]==-1).sum()}")
