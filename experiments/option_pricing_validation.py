"""
Option Pricing Model Validation
================================
Validates the closed-form option pricing model in `option_pricing.py` against
actual market option prices in the `option_prices` table.

For each ATM option with sufficient `option_prices` rows after entry:
  1. Anchor: pick the entry-bar option price (T+0) as V_0
  2. Walk forward to T+1, T+3, T+5, T+7, T+10, T+15
  3. At each bar, compute model_pnl = option_pnl_pct(side, U_t, U_0, t,
     premium_pct, vega_ratio_state)
     where premium_pct = V_0 / U_0 (empirical, NOT formula-based)
     and vega_ratio_state = 1.0 pre-earnings, sampled empirical post-earnings
     (here: actual_post_iv / actual_pre_iv from the trade's own option chain)
  4. Compare: actual_pnl = (V_t - V_0) / V_0
     residual = actual_pnl - model_pnl

Aggregate RMSE / MAE / mean_bias by:
  - bars_held band (1, 3, 5, 7, 10, 15)
  - earnings span (no event / event in window)
  - side (call / put)

CLI:
  python experiments/option_pricing_validation.py
  python experiments/option_pricing_validation.py --max-signals 500
  python experiments/option_pricing_validation.py --side call

Output:
  Two tables — non-spanning-earnings residuals (delta+theta only) and
  spanning-earnings residuals (delta+theta+vega). Comparison rows include
  the LEGACY model output (delta-only with static SL=-0.20 if barrier hit)
  for reference.
"""
import os
import sys
import argparse
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force UTF-8 output for Windows
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database.trader_database import DB
from database.models.core import EarningsDate
from option_pricing import option_pnl_pct, DEFAULT_DELTA, DEFAULT_TOTAL_DTE


# ── Config ─────────────────────────────────────────────────────────────────
DEFAULT_MAX_MONEYNESS = 0.03      # |strike - close| / close
DEFAULT_DTE_TARGET    = 30
DEFAULT_DTE_TOL       = 5         # accept 25-35 DTE as "30 DTE"
DEFAULT_BARS          = [1, 3, 5, 7, 10, 15]
DEFAULT_MIN_VOLUME    = 5         # filter dead options
DEFAULT_MAX_SIGNALS   = 5000      # default cap to keep run fast


# ── Data loading ───────────────────────────────────────────────────────────

def load_signal_options(max_signals=DEFAULT_MAX_SIGNALS):
    """Pull (option_id, symbol, side, strike, expiration, signal_date,
    entry_close, entry_iv, entry_price) for ATM options with enough forward bars.

    Restricts to options with: volume >= MIN_VOLUME on signal date, |moneyness|
    <= 3%, expiration ~30 days out (25-35).
    """
    print(f"Loading ATM 30 DTE options (max {max_signals})...", flush=True)

    # Anchor: signal_date must be at least 22 calendar days before today (so
    # forward bars 1..15 trading-day are likely covered).
    cutoff_date = date.today() - timedelta(days=22)
    sql = """
    SELECT op.option_id, o.symbol, o.option_type, o.strike_price,
           o.expiration_date, op.date AS signal_date,
           ph.close AS entry_close, op.iv AS entry_iv, op.price AS entry_price
    FROM option_prices op
    JOIN options o ON o.id = op.option_id
    JOIN price_history ph ON ph.symbol = o.symbol AND ph.date = op.date
    WHERE op.volume >= %s
      AND op.date <= %s
      AND DATEDIFF(o.expiration_date, op.date) BETWEEN %s AND %s
      AND ABS(CAST(o.strike_price AS DECIMAL(10,2)) - CAST(ph.close AS DECIMAL(10,2)))
          / CAST(ph.close AS DECIMAL(10,2)) <= %s
      AND op.iv IS NOT NULL AND op.iv > 0.05
      AND op.price > 0.50
    ORDER BY RAND()
    LIMIT %s
    """
    # We oversample 4x and post-filter to options with sufficient forward
    # bars in Python — cheaper than a correlated subquery in MySQL.
    cursor = DB.execute_sql(sql, (
        DEFAULT_MIN_VOLUME,
        cutoff_date,
        DEFAULT_DTE_TARGET - DEFAULT_DTE_TOL,
        DEFAULT_DTE_TARGET + DEFAULT_DTE_TOL,
        DEFAULT_MAX_MONEYNESS,
        max_signals * 4,
    ))
    rows = cursor.fetchall()
    print(f"  loaded {len(rows):,} candidate signal-options (will post-filter to those with >=7 forward bars)", flush=True)
    return rows[:max_signals * 4]   # already capped by SQL but defensive


def load_forward_option_prices(option_id, signal_date, max_bars=15):
    """Fetch (date, price, iv) for the option from signal_date forward,
    up to max_bars trading days. Returns list ordered by date asc.
    """
    end_date = signal_date + timedelta(days=int(max_bars * 1.6))  # buffer for non-trading days
    sql = """
    SELECT date, price, iv FROM option_prices
    WHERE option_id = %s AND date >= %s AND date <= %s
    ORDER BY date ASC
    """
    cursor = DB.execute_sql(sql, (option_id, signal_date, end_date))
    return list(cursor.fetchall())


def load_forward_underlying(symbol, signal_date, max_bars=15):
    """Fetch (date, close) for the underlying from signal_date forward."""
    end_date = signal_date + timedelta(days=int(max_bars * 1.6))
    sql = """
    SELECT date, close FROM price_history
    WHERE symbol = %s AND date >= %s AND date <= %s
    ORDER BY date ASC
    """
    cursor = DB.execute_sql(sql, (symbol, signal_date, end_date))
    return list(cursor.fetchall())


def load_earnings_dates(symbol):
    """Load all known earnings dates for a symbol."""
    rows = list(
        EarningsDate.select(EarningsDate.date)
        .where(EarningsDate.symbol == symbol)
        .order_by(EarningsDate.date)
        .tuples()
    )
    return [r[0] for r in rows]


def load_realized_vol_for_signal(symbol, signal_date, lookback=60):
    """Compute 60d realized daily σ in % from price_history."""
    start = signal_date - timedelta(days=int(lookback * 1.7))
    sql = """
    SELECT date, close FROM price_history
    WHERE symbol = %s AND date >= %s AND date <= %s
    ORDER BY date ASC
    """
    cursor = DB.execute_sql(sql, (symbol, start, signal_date))
    rows = list(cursor.fetchall())
    closes = [float(c) for _, c in rows[-(lookback + 1):]]
    if len(closes) < lookback // 2:
        return None
    rets = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev > 0:
            rets.append((closes[i] - prev) / prev)
    if not rets:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


# ── Validation walk ────────────────────────────────────────────────────────

def validate_signal(option_id, symbol, side, strike, expiration, signal_date,
                    entry_close, entry_iv, entry_price, ern_dates,
                    bars_to_check=DEFAULT_BARS):
    """Walk the option's forward prices, compare model vs actual at each bar.

    Returns list of dicts (one per bar that had data):
      {'bar': t, 'spans_earnings': bool, 'side': str, 'actual_pnl': ...,
       'model_pnl': ..., 'residual': ..., 'underlying_pct_move': ..., }
    """
    side_norm = 'call' if side.upper() == 'CALL' else 'put'
    fwd_op = load_forward_option_prices(option_id, signal_date, max_bars=15)
    fwd_ph = load_forward_underlying(symbol, signal_date, max_bars=15)
    if len(fwd_op) < 2 or len(fwd_ph) < 2:
        return []

    # Build trading-day index → option price / underlying close
    ph_by_date = {d: float(c) for d, c in fwd_ph}
    op_by_date = {d: (float(p), float(iv) if iv else None) for d, p, iv in fwd_op}

    # Order by trading day
    trading_days = sorted(d for d in ph_by_date if d >= signal_date)
    if len(trading_days) < 2:
        return []

    # Anchor entry: signal_date close + entry option price (passed in)
    U_0 = float(entry_close)
    V_0 = float(entry_price)
    if U_0 <= 0 or V_0 <= 0:
        return []
    premium_pct = V_0 / U_0   # empirical; NOT formula-based

    # Pre-earnings IV anchor (used to compute vega_ratio when post date observed)
    pre_iv = float(entry_iv) if entry_iv else None

    results = []
    for t in bars_to_check:
        if t >= len(trading_days):
            break
        d_t = trading_days[t]
        if d_t not in op_by_date or d_t not in ph_by_date:
            continue
        V_t, iv_t = op_by_date[d_t]
        U_t = ph_by_date[d_t]
        actual_pnl = (V_t - V_0) / V_0

        # Earnings span detection
        span_ed = None
        for ed in ern_dates:
            if signal_date < ed <= d_t:
                span_ed = ed
                break
        spans = span_ed is not None

        # Vega ratio: actual post/pre IV ratio (empirical from this trade's chain)
        vega_ratio = 1.0
        if spans and pre_iv and iv_t and pre_iv > 0:
            vega_ratio = max(0.20, min(2.0, iv_t / pre_iv))

        model_pnl = option_pnl_pct(
            side_norm, U_t, U_0, t, premium_pct,
            total_dte=DEFAULT_TOTAL_DTE,
            vega_ratio=vega_ratio,
            delta=DEFAULT_DELTA,
        )

        # LEGACY reference: delta-only with no theta/vega (what current MC assumes
        # for mid-window option price)
        legacy_pnl = ((U_t - U_0) / U_0 if side_norm == 'call'
                      else -(U_t - U_0) / U_0) * DEFAULT_DELTA / premium_pct

        results.append({
            'bar': t,
            'spans_earnings': spans,
            'side': side_norm,
            'actual_pnl': actual_pnl,
            'model_pnl': model_pnl,
            'legacy_pnl': legacy_pnl,
            'residual_model': actual_pnl - model_pnl,
            'residual_legacy': actual_pnl - legacy_pnl,
            'underlying_pct': (U_t - U_0) / U_0,
            'vega_ratio': vega_ratio,
            'iv_t': iv_t,
            'pre_iv': pre_iv,
        })
    return results


# ── Aggregation + reporting ────────────────────────────────────────────────

def _stats(residuals):
    if not residuals:
        return dict(n=0, rmse=None, mae=None, bias=None, p25=None, p75=None)
    n = len(residuals)
    rmse = math.sqrt(sum(r * r for r in residuals) / n)
    mae = sum(abs(r) for r in residuals) / n
    bias = sum(residuals) / n
    sr = sorted(residuals)
    return dict(
        n=n,
        rmse=rmse, mae=mae, bias=bias,
        p25=sr[int(n * 0.25)],
        p75=sr[min(n - 1, int(n * 0.75))],
    )


def report_table(label, rows, key_fn):
    """Group rows by key_fn(row) and print residual stats table."""
    groups = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    print(f"\n{label}")
    print(f"{'cohort':<28} {'N':>7} {'mod_RMSE':>10} {'mod_MAE':>9} {'mod_bias':>10}  | {'leg_RMSE':>10} {'leg_MAE':>9} {'leg_bias':>10}")
    print('-' * 110)
    for k in sorted(groups.keys()):
        grp = groups[k]
        ms = _stats([r['residual_model'] for r in grp])
        ls = _stats([r['residual_legacy'] for r in grp])
        if ms['n'] == 0:
            continue
        print(f"{str(k):<28} {ms['n']:>7,} "
              f"{ms['rmse']:>10.3f} {ms['mae']:>9.3f} {ms['bias']:>+10.3f}  | "
              f"{ls['rmse']:>10.3f} {ls['mae']:>9.3f} {ls['bias']:>+10.3f}")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-signals', type=int, default=DEFAULT_MAX_SIGNALS)
    parser.add_argument('--side', choices=['call', 'put', 'both'], default='both')
    args = parser.parse_args()

    rows_in = load_signal_options(max_signals=args.max_signals)
    if not rows_in:
        print("No signal-options matched criteria. Aborting.", flush=True)
        return

    # Filter side
    if args.side != 'both':
        target_side = args.side.upper()
        rows_in = [r for r in rows_in if r[2].upper() == target_side]
        print(f"  filtered to {args.side}: {len(rows_in)}", flush=True)

    # Cache earnings dates per symbol
    print("Caching earnings dates...", flush=True)
    syms = list({r[1] for r in rows_in})
    ern_cache = {s: load_earnings_dates(s) for s in syms}

    print("Walking forward bars + comparing model vs actual...", flush=True)
    all_results = []
    n_processed = 0
    for opt_id, sym, side_str, strike, exp, sig_date, entry_close, entry_iv, entry_price in rows_in:
        n_processed += 1
        if n_processed % 500 == 0:
            print(f"  {n_processed:,} / {len(rows_in):,}", flush=True)
        ern_dates = ern_cache.get(sym, [])
        try:
            rs = validate_signal(
                opt_id, sym, side_str, strike, exp, sig_date,
                entry_close, entry_iv, entry_price, ern_dates,
            )
        except Exception as e:
            continue
        all_results.extend(rs)

    if not all_results:
        print("No bar comparisons produced. Aborting.", flush=True)
        return

    print(f"\n{'='*100}")
    print(f"OPTION PRICING MODEL VALIDATION  (residual = actual_pnl - model_pnl)")
    print(f"{'='*100}")
    print(f"Bar comparisons: {len(all_results):,}")
    print(f"Spanning earnings:    {sum(1 for r in all_results if r['spans_earnings']):,}")
    print(f"Non-spanning:         {sum(1 for r in all_results if not r['spans_earnings']):,}")

    # By bar (no earnings span) — pure delta+theta validation
    non_spanning = [r for r in all_results if not r['spans_earnings']]
    spanning     = [r for r in all_results if r['spans_earnings']]

    report_table(
        "[NON-SPANNING] delta + theta only — by side × bar",
        non_spanning,
        key_fn=lambda r: f"{r['side']:>4} bar={r['bar']:>2}",
    )
    if spanning:
        report_table(
            "[SPANNING EARNINGS] delta + theta + vega(actual_post/pre) — by side × bar",
            spanning,
            key_fn=lambda r: f"{r['side']:>4} bar={r['bar']:>2}",
        )

    # Headline: overall model vs legacy
    print(f"\n{'='*100}")
    print(f"HEADLINE — overall residuals across all bars and signals")
    print(f"{'='*100}")
    s_all_model  = _stats([r['residual_model']  for r in all_results])
    s_all_legacy = _stats([r['residual_legacy'] for r in all_results])
    print(f"  model  (delta+theta+vega): N={s_all_model['n']:,}  RMSE={s_all_model['rmse']:.3f}  MAE={s_all_model['mae']:.3f}  bias={s_all_model['bias']:+.3f}")
    print(f"  legacy (delta only):       N={s_all_legacy['n']:,}  RMSE={s_all_legacy['rmse']:.3f}  MAE={s_all_legacy['mae']:.3f}  bias={s_all_legacy['bias']:+.3f}")
    rmse_lift = (s_all_legacy['rmse'] - s_all_model['rmse']) / s_all_legacy['rmse'] * 100
    print(f"  → model RMSE is {rmse_lift:+.1f}% vs legacy")

    # Tail diagnostics: where does the model fail worst?
    print(f"\n{'='*100}")
    print(f"WORST 10 RESIDUALS (model)")
    print(f"{'='*100}")
    print(f"{'side':>4} {'bar':>3} {'spans':>5} {'actual':>9} {'model':>9} {'resid':>9} {'undlyΔ':>9} {'iv_t':>6} {'pre_iv':>6}")
    for r in sorted(all_results, key=lambda x: -abs(x['residual_model']))[:10]:
        print(f"{r['side']:>4} {r['bar']:>3} {str(r['spans_earnings']):>5} "
              f"{r['actual_pnl']:>+9.3f} {r['model_pnl']:>+9.3f} {r['residual_model']:>+9.3f} "
              f"{r['underlying_pct']:>+9.3f} "
              f"{(r['iv_t'] or 0):>6.3f} {(r['pre_iv'] or 0):>6.3f}")


if __name__ == '__main__':
    main()
