"""
15 DTE / 7-bar-hold per-trade A/B vs current 30 DTE / 15-bar-hold

Hypothesis: v28 7d call WRs are strong; the 15 DTE option needs only 0.774σ
underlying move for TP=30% (vs 1.092σ for 30 DTE), so option-aligned barrier
WR could clear BE faster, enabling capital-velocity compounding.

Method:
  1. Load all v28 peaks (calls >=70 OR puts <=25) over a lookback window.
  2. Per peak, walk option-aligned barriers under two configs:
       30 DTE: K_tp=1.092, M_call=1.274, M_put=0.728, hold=15 trading bars
       15 DTE: K_tp=0.774, M_call=0.910, M_put=0.516, hold=7  trading bars
     Barrier units are sigma multiples of underlying price. Premium-to-underlying
     conversion uses delta=0.5 ATM and DTE-specific premium multipliers.
  3. Bucket WR / SL% / Expire% by score tier (calls: 70+, 75+, 80+, 85+, 90+, 95+;
     puts: <30, <25, <20, <15, <10, <5).
  4. Compute net EV per side using the same per-exit slippage model as production.
  5. Flag whether each (DTE x bucket) clears its BE threshold.

What this answers:
  - At v28 signal quality, does 15 DTE call option-TP rate clear call BE (~56%)?
  - Does 15 DTE put option-TP rate clear put BE (~43.5%)?
  - How does the rate compare to 30 DTE current production?

What this does NOT answer:
  - Portfolio-level compounding (needs MC) or DD profile (needs MC).
  - Same-stock re-entry effects (also MC-side).
  - Premium pricing accuracy in absolute terms — see option_price_assessment.py.
"""
from __future__ import annotations
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from collections import defaultdict
from datetime import date, timedelta

from database.trader_database import DB
from database.models.core import Score, AlgorithmVersion
from database.models.technical import PriceHistory
from database.barrier_walk_numba import walk_batch, warmup

# ───────────────────────────── Config ──────────────────────────────
LOOKBACK_DAYS = 1825  # 5y to match canonical MC + assessment windows
PREMIUM_MULT_15 = 1.29
PREMIUM_MULT_30 = 1.82
DELTA = 0.5

# Production option-level params (gross %)
TP_PCT_CALL = 0.30   # +30% on premium
SL_PCT_CALL = 0.35   # -35% on premium
TP_PCT_PUT  = 0.30
SL_PCT_PUT  = 0.20

# Per-exit slippage (matches monte_carlo.py)
SLIP_ENTRY = 0.010   # -1% on entry
SLIP_TP    = 0.000   # 0% (limit sell)
SLIP_SL    = 0.013   # -1.3% on stop fill
SLIP_HARD  = 0.005   # -0.5% mechanical close

# Hard-sell P&L (gross). 30 DTE day-15 = -50% (empirical: theta + IV crush + spread).
# 15 DTE day-7 = -45% (theoretical 0.270/0.293 ratio of -50% at same residual fraction).
HARD_PCT_30 = 0.50
HARD_PCT_15 = 0.45  # See `experiments/v27_optimization` hard-sell scaling notes

# Convert option-level pct to underlying sigma multiple:
#   underlying_move_pct = (option_pct * premium_pct) / DELTA
#   underlying_sigma_mult = option_pct * premium_mult / DELTA
def sigma_mult(option_pct: float, premium_mult: float, delta: float = DELTA) -> float:
    return option_pct * premium_mult / delta

CFG = {
    '30DTE': {
        'hold_bars': 15,
        'k_tp_call': sigma_mult(TP_PCT_CALL, PREMIUM_MULT_30),  # 1.092
        'm_sl_call': sigma_mult(SL_PCT_CALL, PREMIUM_MULT_30),  # 1.274
        'k_tp_put':  sigma_mult(TP_PCT_PUT,  PREMIUM_MULT_30),  # 1.092
        'm_sl_put':  sigma_mult(SL_PCT_PUT,  PREMIUM_MULT_30),  # 0.728
        'hard_pct': HARD_PCT_30,
    },
    '15DTE': {
        'hold_bars': 7,
        'k_tp_call': sigma_mult(TP_PCT_CALL, PREMIUM_MULT_15),  # 0.774
        'm_sl_call': sigma_mult(SL_PCT_CALL, PREMIUM_MULT_15),  # 0.903
        'k_tp_put':  sigma_mult(TP_PCT_PUT,  PREMIUM_MULT_15),  # 0.774
        'm_sl_put':  sigma_mult(SL_PCT_PUT,  PREMIUM_MULT_15),  # 0.516
        'hard_pct': HARD_PCT_15,
    },
}

# Net P&L per exit type (after slippage)
def net_tp_call() -> float:  return  TP_PCT_CALL - SLIP_ENTRY - SLIP_TP            # +0.290
def net_sl_call() -> float:  return -SL_PCT_CALL - SLIP_ENTRY - SLIP_SL            # -0.373
def net_tp_put()  -> float:  return  TP_PCT_PUT  - SLIP_ENTRY - SLIP_TP            # +0.290
def net_sl_put()  -> float:  return -SL_PCT_PUT  - SLIP_ENTRY - SLIP_SL            # -0.223
def net_hard(hard_pct: float) -> float: return -hard_pct - SLIP_ENTRY - SLIP_HARD  # -0.515 / -0.465

# Score buckets (cumulative)
CALL_BUCKETS = [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]
PUT_BUCKETS  = [(5,  '<=5'), (10, '<=10'), (15, '<=15'), (20, '<=20'), (25, '<=25'), (30, '<=30')]

# ───────────────────────── Vol estimation ──────────────────────────
def realized_vol_pct(closes_window: np.ndarray) -> float:
    """60-day realized daily vol % (matches production)."""
    if len(closes_window) < 30:
        return 0.0
    rets = np.diff(closes_window) / closes_window[:-1]
    if len(rets) < 20:
        return 0.0
    return float(np.std(rets[-60:]) * 100.0)


# ───────────────────────────── Main ────────────────────────────────
def load_peaks(version_id: int, lookback_days: int):
    """Pull v28 peaks: calls >=70 or puts <=25, deduped one-per-(symbol, date)."""
    cutoff = date.today() - timedelta(days=lookback_days)
    rows = list(DB.execute_sql(
        "SELECT symbol, date, overall FROM scores "
        "WHERE version_id=%s AND date>=%s AND (overall>=70 OR overall<=25) "
        "ORDER BY symbol, date",
        (version_id, cutoff)
    ).fetchall())
    return rows


def load_prices_per_symbol(symbols: list[str], cutoff: date) -> dict:
    """Return {symbol: (dates_arr, closes_arr, highs_arr, lows_arr)} as numpy."""
    out = {}
    chunk_size = 50
    sym_list = sorted(set(symbols))
    for i in range(0, len(sym_list), chunk_size):
        chunk = sym_list[i:i+chunk_size]
        ph = ','.join(['%s'] * len(chunk))
        sql = (f"SELECT symbol, date, close, high, low FROM price_history "
               f"WHERE symbol IN ({ph}) AND date>=%s ORDER BY symbol, date")
        # Need extra back-history for vol calc → load 90 days earlier
        rows = list(DB.execute_sql(sql, list(chunk) + [cutoff - timedelta(days=120)]).fetchall())
        bysym = defaultdict(list)
        for r in rows:
            bysym[r[0]].append(r)
        for sym, sym_rows in bysym.items():
            sym_rows.sort(key=lambda r: r[1])
            dates  = np.array([r[1] for r in sym_rows], dtype=object)
            closes = np.array([float(r[2]) for r in sym_rows], dtype=np.float64)
            highs  = np.array([float(r[3]) for r in sym_rows], dtype=np.float64)
            lows   = np.array([float(r[4]) for r in sym_rows], dtype=np.float64)
            out[sym] = (dates, closes, highs, lows)
    return out


def walk_peaks_for_config(peaks_by_sym, prices, cfg_label: str):
    """Walk all peaks under one DTE config (calls + puts).
    Returns list of dicts: {score, side, result, exit_bar, mae, mfe, sigma}.
    result codes: 1=TP, 0=SL, -1=insufficient/expire→hard.
    """
    cfg = CFG[cfg_label]
    out = []
    n_skipped = 0

    for sym, peaks in peaks_by_sym.items():
        ph = prices.get(sym)
        if ph is None:
            n_skipped += len(peaks)
            continue
        dates, closes, highs, lows = ph

        # Build date → idx
        date_to_idx = {d: i for i, d in enumerate(dates)}

        # Group peaks by side for separate batched walks
        call_peaks = []  # (peak_idx_in_dates, score)
        put_peaks  = []
        for sym_id, pdate, score in peaks:
            i = date_to_idx.get(pdate)
            if i is None:
                n_skipped += 1
                continue
            if i + cfg['hold_bars'] >= len(dates):
                n_skipped += 1  # insufficient forward bars
                continue
            sigma = realized_vol_pct(closes[max(0, i-70):i+1])
            if sigma <= 0:
                n_skipped += 1
                continue
            if score >= 70:
                call_peaks.append((i, score, sigma))
            else:
                put_peaks.append((i, score, sigma))

        # Walk calls (side_code=1)
        if call_peaks:
            idx_arr = np.array([p[0] for p in call_peaks], dtype=np.int64)
            sigma_arr = np.array([p[2] for p in call_peaks], dtype=np.float64)
            sides = np.ones(len(call_peaks), dtype=np.int64)
            wbars = np.full(len(call_peaks), cfg['hold_bars'], dtype=np.int64)
            res = walk_batch(closes, highs, lows, idx_arr, sides, wbars, sigma_arr,
                             cfg['k_tp_call'], cfg['m_sl_call'])
            for k, (idx, score, sigma) in enumerate(call_peaks):
                out.append({
                    'side': 'call', 'score': score, 'sigma': sigma,
                    'result': int(res[k, 0]), 'exit_bar': int(res[k, 1]),
                    'mae': res[k, 4], 'mfe': res[k, 5],
                })

        # Walk puts (side_code=0)
        if put_peaks:
            idx_arr = np.array([p[0] for p in put_peaks], dtype=np.int64)
            sigma_arr = np.array([p[2] for p in put_peaks], dtype=np.float64)
            sides = np.zeros(len(put_peaks), dtype=np.int64)
            wbars = np.full(len(put_peaks), cfg['hold_bars'], dtype=np.int64)
            res = walk_batch(closes, highs, lows, idx_arr, sides, wbars, sigma_arr,
                             cfg['k_tp_put'], cfg['m_sl_put'])
            for k, (idx, score, sigma) in enumerate(put_peaks):
                out.append({
                    'side': 'put', 'score': score, 'sigma': sigma,
                    'result': int(res[k, 0]), 'exit_bar': int(res[k, 1]),
                    'mae': res[k, 4], 'mfe': res[k, 5],
                })
    return out, n_skipped


def bucket_results(results: list, side: str, buckets: list, cfg_label: str):
    """Aggregate per cumulative bucket, return rows for printing."""
    cfg = CFG[cfg_label]
    hard_pct = cfg['hard_pct']
    rows = []
    side_results = [r for r in results if r['side'] == side]

    for thr, label in buckets:
        if side == 'call':
            cohort = [r for r in side_results if r['score'] >= thr]
        else:
            cohort = [r for r in side_results if r['score'] <= thr]
        n = len(cohort)
        if n == 0:
            continue
        n_tp = sum(1 for r in cohort if r['result'] == 1)
        n_sl = sum(1 for r in cohort if r['result'] == 0)
        n_hard = sum(1 for r in cohort if r['result'] == -1)
        n_known = n_tp + n_sl + n_hard
        tp_rate = n_tp / n_known * 100.0 if n_known else 0.0
        sl_rate = n_sl / n_known * 100.0 if n_known else 0.0
        hard_rate = n_hard / n_known * 100.0 if n_known else 0.0

        if side == 'call':
            ev = (tp_rate/100.0) * net_tp_call() + (sl_rate/100.0) * net_sl_call() + (hard_rate/100.0) * net_hard(hard_pct)
            be = abs(net_sl_call()) / (net_tp_call() + abs(net_sl_call())) * 100.0
        else:
            ev = (tp_rate/100.0) * net_tp_put() + (sl_rate/100.0) * net_sl_put() + (hard_rate/100.0) * net_hard(hard_pct)
            be = abs(net_sl_put()) / (net_tp_put() + abs(net_sl_put())) * 100.0

        # Avg exit bar (TP only)
        tp_bars = [r['exit_bar'] for r in cohort if r['result'] == 1]
        avg_tp_bar = np.mean(tp_bars) if tp_bars else 0.0

        # MAE on winners (sigma-normalized)
        win_mae = [r['mae'] / r['sigma'] for r in cohort if r['result'] == 1 and r['sigma'] > 0]
        mae_win_sigma = np.mean(win_mae) if win_mae else 0.0

        rows.append({
            'bucket': label, 'n': n, 'tp%': tp_rate, 'sl%': sl_rate, 'hard%': hard_rate,
            'be%': be, 'margin_pp': tp_rate - be, 'ev%': ev * 100.0,
            'avg_tp_bar': avg_tp_bar, 'mae_win_σ': mae_win_sigma,
        })
    return rows


def print_table(rows: list, side: str, cfg_label: str):
    cfg = CFG[cfg_label]
    print(f"\n  {cfg_label} {side.upper()}S — hold={cfg['hold_bars']} bars, "
          f"K_tp={cfg['k_tp_call' if side=='call' else 'k_tp_put']:.3f}σ, "
          f"M_sl={cfg['m_sl_call' if side=='call' else 'm_sl_put']:.3f}σ, "
          f"hard={cfg['hard_pct']*100:.0f}%")
    fmt = '{:>6}  {:>6}  {:>7}  {:>7}  {:>7}  {:>7}  {:>9}  {:>7}  {:>9}  {:>10}'
    print(fmt.format('Bucket', 'N', 'TP%', 'SL%', 'Hard%', 'BE%', 'Margin pp', 'EV%', 'AvgTPBar', 'MAEwin σ'))
    print('  ' + '─' * 96)
    for r in rows:
        marker = ' ✓' if r['margin_pp'] > 0 else ' ✗'
        print(fmt.format(
            r['bucket'], r['n'],
            f"{r['tp%']:.1f}", f"{r['sl%']:.1f}", f"{r['hard%']:.1f}",
            f"{r['be%']:.1f}",
            f"{r['margin_pp']:+.1f}{marker}",
            f"{r['ev%']:+.2f}",
            f"{r['avg_tp_bar']:.1f}",
            f"{r['mae_win_σ']:.3f}",
        ))


def diff_table(rows_30: list, rows_15: list, side: str):
    print(f"\n  Δ (15DTE − 30DTE) {side.upper()}S")
    fmt = '{:>6}  {:>9}  {:>9}  {:>9}  {:>10}  {:>11}'
    print(fmt.format('Bucket', 'ΔTP%', 'ΔBE%', 'ΔMargin', 'ΔEV%', 'AvgTPBar 15→30'))
    print('  ' + '─' * 70)
    by_bucket_30 = {r['bucket']: r for r in rows_30}
    for r15 in rows_15:
        r30 = by_bucket_30.get(r15['bucket'])
        if not r30:
            continue
        d_tp = r15['tp%'] - r30['tp%']
        d_be = r15['be%'] - r30['be%']
        d_margin = r15['margin_pp'] - r30['margin_pp']
        d_ev = r15['ev%'] - r30['ev%']
        print(fmt.format(
            r15['bucket'],
            f"{d_tp:+.1f}", f"{d_be:+.1f}", f"{d_margin:+.1f}", f"{d_ev:+.2f}",
            f"{r15['avg_tp_bar']:.1f} → {r30['avg_tp_bar']:.1f}"
        ))


def main():
    print("Warming up JIT walks ...", flush=True)
    warmup()

    DB.connect(reuse_if_open=True)
    av = AlgorithmVersion.get_active_scores_version()
    print(f"Active version: id={av.id} hash={av.git_commit}", flush=True)
    print(f"Lookback: {LOOKBACK_DAYS} days\n", flush=True)

    print(f"  net_tp_call = {net_tp_call():+.3f}, net_sl_call = {net_sl_call():+.3f}, "
          f"net_hard_30 = {net_hard(HARD_PCT_30):+.3f}, net_hard_15 = {net_hard(HARD_PCT_15):+.3f}")
    print(f"  net_tp_put  = {net_tp_put():+.3f}, net_sl_put  = {net_sl_put():+.3f}")
    print(f"  Call BE = {abs(net_sl_call()) / (net_tp_call() + abs(net_sl_call())) * 100:.1f}%")
    print(f"  Put  BE = {abs(net_sl_put())  / (net_tp_put()  + abs(net_sl_put()))  * 100:.1f}%")

    print("\nLoading v28 peaks ...", flush=True)
    raw_peaks = load_peaks(av.id, LOOKBACK_DAYS)
    print(f"  raw v28 peaks (calls>=70 OR puts<=25): {len(raw_peaks):,}")

    # Group by symbol
    peaks_by_sym = defaultdict(list)
    for sym, d, score in raw_peaks:
        peaks_by_sym[sym].append((sym, d, score))
    syms = list(peaks_by_sym.keys())
    print(f"  symbols: {len(syms):,}")

    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    print(f"\nLoading price history ({len(syms)} symbols) ...", flush=True)
    prices = load_prices_per_symbol(syms, cutoff)
    print(f"  loaded prices for {len(prices):,} symbols")

    # Walk both configs
    print("\nWalking 30DTE config ...", flush=True)
    res_30, sk_30 = walk_peaks_for_config(peaks_by_sym, prices, '30DTE')
    print(f"  {len(res_30):,} resolved, {sk_30:,} skipped (insufficient bars)")

    print("Walking 15DTE config ...", flush=True)
    res_15, sk_15 = walk_peaks_for_config(peaks_by_sym, prices, '15DTE')
    print(f"  {len(res_15):,} resolved, {sk_15:,} skipped")

    # Tables
    SEP = '═' * 100
    print(f"\n\n{SEP}")
    print(f"  PER-TRADE A/B: 30DTE/15-bar vs 15DTE/7-bar  (v28 e3c8678, {LOOKBACK_DAYS}d lookback)")
    print(SEP)

    for side, buckets in [('call', CALL_BUCKETS), ('put', PUT_BUCKETS)]:
        rows_30 = bucket_results(res_30, side, buckets, '30DTE')
        rows_15 = bucket_results(res_15, side, buckets, '15DTE')
        print_table(rows_30, side, '30DTE')
        print_table(rows_15, side, '15DTE')
        diff_table(rows_30, rows_15, side)

    print(f"\n{SEP}\n  Notes:")
    print("  - TP/SL/Hard% are conditional on result being known (no-data signals dropped per cohort).")
    print("  - EV% assumes per-exit slippage (SLIP_ENTRY=1.0%, SLIP_TP=0%, SLIP_SL=1.3%, SLIP_HARD=0.5%).")
    print("  - 15 DTE hard sell modeled at -45% (theta scaling from -50% @ 30 DTE day-15).")
    print("  - Premium-to-underlying: σ_mult = option_pct × premium_mult / Δ; Δ=0.5 ATM.")
    print(f"  - 30 DTE: TP=1.092σ, SL_call=1.274σ, SL_put=0.728σ; 15 DTE: TP=0.774σ, SL_call=0.910σ, SL_put=0.516σ.")

    DB.close()


if __name__ == '__main__':
    main()
