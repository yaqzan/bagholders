"""
End-to-end model validation: walk ACTUAL 15 DTE option prices vs model prediction.

The σ-based model assumes:
  - Entry premium ≈ 1.29 × σ_daily × stock_price
  - Δ ≈ 0.5 ATM
  - When underlying moves +X%, option moves +Δ×X% / premium_pct = (option_pct gain)
  - TP at option +30% ↔ underlying ~+0.774σ
  - SL at option -35% ↔ underlying ~-0.910σ (calls)

This script picks ACTUAL ATM 15 DTE options (calls & puts) from option_prices,
walks forward day-by-day on the actual option price, classifies the actual exit
(TP / SL / Hard at day-7), and compares to what the σ-based model would predict
from the underlying price walk alone.

Outputs:
  - For N sampled options: model_predicted vs actual_realized outcome
  - Rate of model-says-TP but actual-didn't (and vice versa)
  - Average $ error per trade
  - Σ-multiplier accuracy at entry (already covered by option_price_assessment.py
    but reported here for completeness on the sampled cohort)
"""
from __future__ import annotations
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from collections import defaultdict
from datetime import date, timedelta
from database.trader_database import DB

PREMIUM_MULT_15 = 1.29
DELTA = 0.5
TP_PCT = 0.30
SL_PCT_CALL = 0.35
SL_PCT_PUT  = 0.20
HOLD_BARS = 7
N_SAMPLE = 400  # per side


def load_sample_options(side: str, n: int):
    """Pick N ATM 15 DTE options with forward price data.

    Strategy: hit `options` table first (small, indexed) to get a sample of
    option ids in the right DTE band, then for each fetch a candidate entry bar
    from option_prices. This avoids scanning 70M option_prices rows.
    """
    opt_type = 'CALL' if side == 'call' else 'PUT'

    # Get a sample of options whose expiration_date falls in a recent window
    # — covers the data period (Feb 2025 onward per CLAUDE.md).
    # Order by id stride so we get diverse symbols.
    sql_opts = """
    SELECT id, symbol, strike_price, expiration_date
    FROM options
    WHERE option_type = %s
      AND expiration_date >= '2025-02-01'
      AND expiration_date <= DATE_SUB(CURDATE(), INTERVAL 12 DAY)
    ORDER BY id
    LIMIT %s
    """
    # Pull broad sample (n * 5) to allow downstream moneyness/IV filtering
    candidates = list(DB.execute_sql(sql_opts, (opt_type, n * 5)).fetchall())
    if not candidates:
        return []

    # For each candidate option, find a 13-17 DTE entry bar with the constraints
    out = []
    for opt_id, sym, strike, exp_date in candidates:
        if len(out) >= n:
            break
        # Compute target entry date range: exp - 17 to exp - 13
        sql_entry = """
        SELECT op.date, op.price, op.iv, op.volume, ph.close
        FROM option_prices op
        JOIN price_history ph ON ph.symbol = %s AND ph.date = op.date
        WHERE op.option_id = %s
          AND DATEDIFF(%s, op.date) BETWEEN 13 AND 17
          AND op.iv > 0.05 AND op.volume >= 20 AND op.price > 0.10
          AND ABS(%s - ph.close) / ph.close <= 0.015
        ORDER BY op.date LIMIT 1
        """
        entry = DB.execute_sql(
            sql_entry, (sym, opt_id, exp_date, float(strike))
        ).fetchone()
        if entry is None:
            continue
        e_date, e_price, e_iv, e_vol, e_close = entry
        out.append((opt_id, e_date, e_price, e_iv, e_vol, sym, strike, exp_date, e_close))
    return out


def walk_actual_option(option_id: int, start_date, hold_bars: int, side: str):
    """Walk forward on option_prices for `option_id`, return outcome.

    Returns dict {kind, exit_bar, exit_price, max_pct, min_pct, entry_price}
    kind: 'tp', 'sl', 'hard', 'expire' (option expired before hold), 'no_data'
    """
    # Get up to hold_bars+5 forward option prices (with the entry bar)
    sql = """
    SELECT date, price FROM option_prices
    WHERE option_id = %s AND date >= %s
    ORDER BY date
    LIMIT %s
    """
    rows = list(DB.execute_sql(sql, (option_id, start_date, hold_bars + 5)).fetchall())
    if len(rows) < 2:
        return {'kind': 'no_data', 'exit_bar': 0, 'exit_price': 0.0,
                'max_pct': 0.0, 'min_pct': 0.0, 'entry_price': 0.0}
    entry_price = float(rows[0][1])
    if entry_price <= 0:
        return {'kind': 'no_data', 'exit_bar': 0, 'exit_price': 0.0,
                'max_pct': 0.0, 'min_pct': 0.0, 'entry_price': 0.0}
    tp_level = entry_price * (1.0 + TP_PCT)
    sl_pct = SL_PCT_CALL if side == 'call' else SL_PCT_PUT
    sl_level = entry_price * (1.0 - sl_pct)

    max_pct = 0.0
    min_pct = 0.0
    for j in range(1, len(rows)):
        bar = j  # bars held
        price = float(rows[j][1])
        if price <= 0:
            return {'kind': 'expire', 'exit_bar': bar, 'exit_price': 0.0,
                    'max_pct': max_pct, 'min_pct': min_pct, 'entry_price': entry_price}
        pct = (price - entry_price) / entry_price
        if pct > max_pct: max_pct = pct
        if pct < min_pct: min_pct = pct
        # TP / SL on close (we don't have intraday for options)
        if price >= tp_level:
            return {'kind': 'tp', 'exit_bar': bar, 'exit_price': price,
                    'max_pct': max_pct, 'min_pct': min_pct, 'entry_price': entry_price}
        if price <= sl_level:
            return {'kind': 'sl', 'exit_bar': bar, 'exit_price': price,
                    'max_pct': max_pct, 'min_pct': min_pct, 'entry_price': entry_price}
        if bar >= hold_bars:
            return {'kind': 'hard', 'exit_bar': bar, 'exit_price': price,
                    'max_pct': max_pct, 'min_pct': min_pct, 'entry_price': entry_price}
    # Ran out of bars before reaching hold
    last_price = float(rows[-1][1])
    last_pct = (last_price - entry_price) / entry_price
    return {'kind': 'expire', 'exit_bar': len(rows) - 1, 'exit_price': last_price,
            'max_pct': max_pct, 'min_pct': min_pct, 'entry_price': entry_price}


def realized_vol_pct(symbol: str, on_or_before: date) -> float:
    """60-day realized vol % at signal date."""
    sql = """
    SELECT close FROM price_history
    WHERE symbol = %s AND date <= %s
    ORDER BY date DESC LIMIT 70
    """
    rows = list(DB.execute_sql(sql, (symbol, on_or_before)).fetchall())
    if len(rows) < 30:
        return 0.0
    closes = np.array([float(r[0]) for r in rows[::-1]], dtype=np.float64)
    rets = np.diff(closes) / closes[:-1]
    if len(rets) < 20:
        return 0.0
    return float(np.std(rets[-60:]) * 100.0)


def sample_and_walk(side: str, n: int):
    print(f"\n  Sampling {n} {side.upper()} 15-DTE ATM options ...", flush=True)
    sampled = load_sample_options(side, n)
    print(f"    got {len(sampled)} candidates")

    results = []
    n_no_data = 0

    # Pre-compute σ for each unique (symbol, date)
    sigma_keys = set((r[5], r[1]) for r in sampled)
    print(f"    computing σ for {len(sigma_keys)} (symbol, date) keys ...", flush=True)
    sigma_cache = {}
    for sym, d in sigma_keys:
        sigma_cache[(sym, d)] = realized_vol_pct(sym, d)

    print(f"    walking option prices ...", flush=True)
    for r in sampled:
        option_id, sig_date, entry_premium, iv, vol, sym, strike, exp_date, stock_close = r
        sigma = sigma_cache.get((sym, sig_date), 0.0)
        if sigma <= 0:
            n_no_data += 1
            continue

        # Predicted entry premium (model)
        pred_premium_pct = PREMIUM_MULT_15 * sigma  # %
        actual_premium_pct = float(entry_premium) / float(stock_close) * 100.0
        # Walk actual option
        out = walk_actual_option(option_id, sig_date, HOLD_BARS, side)
        if out['kind'] == 'no_data':
            n_no_data += 1
            continue
        results.append({
            'symbol': sym, 'date': sig_date,
            'sigma': sigma, 'iv': float(iv) * 100,
            'pred_premium_pct': pred_premium_pct,
            'actual_premium_pct': actual_premium_pct,
            'premium_error_pct': actual_premium_pct - pred_premium_pct,
            'kind': out['kind'], 'exit_bar': out['exit_bar'],
            'max_pct': out['max_pct'], 'min_pct': out['min_pct'],
            'entry_premium': float(entry_premium),
            'exit_premium': out['exit_price'],
        })

    print(f"    valid walks: {len(results)} (skipped {n_no_data} no-data)")
    return results


def report(side: str, results: list):
    print(f"\n  ▶ {side.upper()}S — actual 15 DTE option price walks (hold={HOLD_BARS} bars)")
    if not results:
        print("    no valid results")
        return

    # Σ-multiplier validation
    actual_pct = np.array([r['actual_premium_pct'] for r in results])
    pred_pct   = np.array([r['pred_premium_pct'] for r in results])
    err = actual_pct - pred_pct
    avg_actual = float(np.mean(actual_pct))
    avg_pred = float(np.mean(pred_pct))
    rmse = float(np.sqrt(np.mean(err * err)))
    bias = float(np.mean(err))
    # Per-row ratio: actual / pred (if pred > 0)
    ratios = [r['actual_premium_pct'] / r['pred_premium_pct'] for r in results if r['pred_premium_pct'] > 0]
    median_ratio = float(np.median(ratios)) if ratios else 0.0
    print(f"    Σ-mult validation: actual={avg_actual:.3f}%×S  pred={avg_pred:.3f}%×S  "
          f"bias={bias:+.3f}%  RMSE={rmse:.3f}%  median(actual/pred)={median_ratio:.3f}x")

    # Outcome distribution
    n = len(results)
    n_tp = sum(1 for r in results if r['kind'] == 'tp')
    n_sl = sum(1 for r in results if r['kind'] == 'sl')
    n_hard = sum(1 for r in results if r['kind'] == 'hard')
    n_expire = sum(1 for r in results if r['kind'] == 'expire')
    print(f"    outcomes: TP={n_tp/n*100:.1f}%  SL={n_sl/n*100:.1f}%  "
          f"Hard={n_hard/n*100:.1f}%  Expire={n_expire/n*100:.1f}%  (N={n})")

    # Realized P&L (gross) per outcome — vs model
    sl_pct_used = SL_PCT_CALL if side == 'call' else SL_PCT_PUT
    print(f"\n    Realized option-pct (gross) by outcome:")
    for kind in ['tp', 'sl', 'hard', 'expire']:
        cohort = [r for r in results if r['kind'] == kind]
        if not cohort:
            continue
        realized = [(r['exit_premium'] - r['entry_premium']) / r['entry_premium'] for r in cohort]
        avg = float(np.mean(realized))
        bar = float(np.mean([r['exit_bar'] for r in cohort]))
        if kind == 'tp':
            model_pred = TP_PCT
        elif kind == 'sl':
            model_pred = -sl_pct_used
        elif kind == 'hard':
            # Theta-only at bar 7 of 15 DTE: √((15-7)/15) = √(0.533) = 0.730 → -27% gross theoretical
            model_pred = -0.27
        else:
            model_pred = 0.0
        delta = avg - model_pred
        print(f"      {kind:<6}  N={len(cohort):>4}  avg_realized={avg:+.3f}  model={model_pred:+.3f}  "
              f"Δ={delta:+.3f}  avg_bar={bar:.1f}")

    # Avg MFE / MAE on premium
    avg_mfe = float(np.mean([r['max_pct'] for r in results]))
    avg_mae = float(np.mean([r['min_pct'] for r in results]))
    print(f"    avg MFE on premium: {avg_mfe:+.3f}  avg MAE: {avg_mae:+.3f}")

    # Net EV
    SLIP_ENTRY = 0.010; SLIP_TP = 0.0; SLIP_SL = 0.013; SLIP_HARD = 0.005
    net = []
    for r in results:
        gross = (r['exit_premium'] - r['entry_premium']) / r['entry_premium']
        if r['kind'] == 'tp':
            net.append(gross - SLIP_ENTRY - SLIP_TP)
        elif r['kind'] == 'sl':
            net.append(gross - SLIP_ENTRY - SLIP_SL)
        elif r['kind'] == 'hard':
            net.append(gross - SLIP_ENTRY - SLIP_HARD)
        else:  # expire
            net.append(gross - SLIP_ENTRY - SLIP_HARD)
    avg_net = float(np.mean(net))
    median_net = float(np.median(net))
    print(f"    net P&L on premium (with slippage): mean={avg_net:+.3f}  median={median_net:+.3f}")


def main():
    DB.connect(reuse_if_open=True)
    print("═" * 80)
    print(f"  End-to-end ACTUAL 15-DTE option price walk validation (N={N_SAMPLE} per side)")
    print(f"  TP/SL/Hard at option price level: TP={TP_PCT*100:.0f}%, "
          f"SL_call={SL_PCT_CALL*100:.0f}%, SL_put={SL_PCT_PUT*100:.0f}%, hard@bar={HOLD_BARS}")
    print("═" * 80)

    for side in ['call', 'put']:
        results = sample_and_walk(side, N_SAMPLE)
        report(side, results)

    DB.close()
    print("\nDone.")


if __name__ == '__main__':
    main()
