"""
Put Shakeout Profile — Bayesian SL Optimization for v21 puts.

Core question: given a put signal (score <=25) where the underlying subsequently
rises past the MC SL threshold (+0.728s), what is P(recovery to TP) before day 15?

If P(recovery | SL breached) x TP_gain > EV(recycled trade), holding through the
shakeout is more profitable than the -20% SL rule.

Conditioned on:
  - Score bucket (<=5 / 6-10 / 11-15 / 16-20 / 21-25)
  - breadth_score at signal date (HIGH >65, MID 35-65, LOW <35)
  - Shakeout magnitude (MILD 0.728-1.0s, MOD 1.0-1.5s, EXTREME >1.5s)

MC parameters used (matching monte_carlo.py):
  SL trigger:  underlying rises  +0.728s from entry
  TP trigger:  underlying falls  -1.092s from entry
  Hold window: 15 calendar days
  Put SL P&L:  -20% on premium
  Put TP P&L:  +30% on premium
  Hard sell:   -50% on premium at day 15

Recycle EV baseline: avg put trade EV from MC = ~+5% per trade (conservative).

Run: python experiments/put_shakeout_profile.py
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

from database.trader_database import DB
from database.models.core import AlgorithmVersion

# MC exit parameters
SL_SIGMA   = 0.728   # underlying rises this many sigma -> SL hit
TP_SIGMA   = 1.092   # underlying falls this many sigma -> TP hit
HOLD_DAYS  = 15      # calendar-day window
PUT_TP_PNL = 0.30    # +30% option gain at TP
PUT_SL_PNL = -0.20   # -20% option loss at SL
HARD_PNL   = -0.50   # -50% hard sell at day 15

# EV of a recycled put trade (conservative from MC canonical results)
RECYCLE_EV = 0.05    # +5% per trade

LOOKBACK_DAYS = 1825  # 5y


def _realized_vol(closes: list[float]) -> float | None:
    if len(closes) < 30:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    window = rets[-60:] if len(rets) >= 60 else rets
    if len(window) < 20:
        return None
    n = len(window)
    mean = sum(window) / n
    var = sum((r - mean) ** 2 for r in window) / (n - 1)
    return math.sqrt(var) * 100.0  # daily % sigma


def _breadth_bucket(score: float | None) -> str:
    if score is None:
        return "MID"
    if score > 65:
        return "HIGH"
    if score < 35:
        return "LOW"
    return "MID"


def _score_bucket(overall: int) -> str:
    if overall <= 5:
        return "<=5"
    if overall <= 10:
        return "6-10"
    if overall <= 15:
        return "11-15"
    if overall <= 20:
        return "16-20"
    return "21-25"


def _shakeout_bucket(magnitude: float) -> str:
    """magnitude in sigma units (positive = adverse for puts)"""
    if magnitude < 1.0:
        return "MILD(0.73-1.0s)"
    if magnitude < 1.5:
        return "MOD(1.0-1.5s)"
    return "EXTREME(>1.5s)"


def main():
    av = AlgorithmVersion.get_active_scores_version()
    vid = av.id
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    # Leave 30 days of forward data margin
    end_date = (date.today() - timedelta(days=30)).isoformat()

    print(f"Put Shakeout Profile — v{av.id} ({av.git_commit[:8]})")
    print(f"Lookback: {cutoff} -> {end_date}  |  SL=+{SL_SIGMA}s  TP=-{TP_SIGMA}s  Window={HOLD_DAYS}d")
    print()

    # Load all put signals for active version
    q_signals = f"""
        SELECT s.symbol, s.date, s.overall
        FROM scores s
        WHERE s.version_id = {vid}
          AND s.date >= '{cutoff}'
          AND s.date <= '{end_date}'
          AND s.overall <= 25
        ORDER BY s.date, s.symbol
    """
    signals = DB.execute_sql(q_signals).fetchall()
    print(f"Put signals (<=25) loaded: {len(signals)}")

    # Bulk-load price history (close, high, low) per symbol
    print("Loading price history...")
    q_prices = f"""
        SELECT symbol, date, close, high, low
        FROM price_history
        WHERE date >= '{(date.today() - timedelta(days=LOOKBACK_DAYS + 90)).isoformat()}'
        ORDER BY symbol, date
    """
    price_rows = DB.execute_sql(q_prices).fetchall()
    # Index: symbol -> sorted list of (date_str, close, high, low)
    ph: dict[str, list] = defaultdict(list)
    for sym, dt, cl, hi, lo in price_rows:
        ph[sym].append((str(dt), float(cl), float(hi), float(lo)))

    # Bulk-load breadth scores by date
    print("Loading breadth data...")
    q_breadth = f"""
        SELECT date, breadth_score
        FROM market_breadth
        WHERE date >= '{cutoff}'
        ORDER BY date
    """
    breadth_rows = DB.execute_sql(q_breadth).fetchall()
    breadth_map: dict[str, float] = {}
    for dt, bs in breadth_rows:
        if bs is not None:
            breadth_map[str(dt)] = float(bs)

    # --- Main walk ---
    # Accumulators keyed by (score_bucket, breadth_bucket)
    # Each value: dict with total, sl_hit, recovery_after_sl, by shakeout_bucket
    stats: dict[tuple, dict] = defaultdict(lambda: {
        "total": 0,
        "no_sl": 0,       # TP hit without SL breach
        "sl_hit": 0,      # SL was breached first (or equal)
        "sl_then_tp": 0,  # SL breached, THEN TP hit within window
        "sl_then_hard": 0,# SL breached, window expired
        "by_shakeout": defaultdict(lambda: {"sl_hit": 0, "sl_then_tp": 0}),
        "sl_mag_sum": 0.0,  # sum of max adverse sigma among SL-hit trades
    })

    # Also collect raw data for EV calculation
    ev_rows: list[tuple[str, str, str, str, float]] = []  # sb, bb, outcome, shakeout_b, sl_mag

    skipped = 0
    processed = 0

    for sym, sig_date_raw, overall in signals:
        sig_date = str(sig_date_raw)
        bars = ph.get(sym, [])
        if not bars:
            skipped += 1
            continue

        # Find signal date index
        dates_list = [b[0] for b in bars]
        try:
            idx = dates_list.index(sig_date)
        except ValueError:
            skipped += 1
            continue

        if idx < 30:
            skipped += 1
            continue

        # Realized vol from prior 60 trading closes
        prior_closes = [b[1] for b in bars[max(0, idx - 60):idx + 1]]
        sigma = _realized_vol(prior_closes)
        if sigma is None or sigma <= 0:
            skipped += 1
            continue

        entry = prior_closes[-1]  # close on signal date
        sl_price = entry * (1.0 + SL_SIGMA * sigma / 100.0)
        tp_price = entry * (1.0 - TP_SIGMA * sigma / 100.0)

        # Walk forward HOLD_DAYS calendar days
        sig_dt = date.fromisoformat(sig_date)
        cutoff_dt = sig_dt + timedelta(days=HOLD_DAYS)

        forward_bars = []
        for b in bars[idx + 1:]:
            b_dt = date.fromisoformat(b[0])
            if b_dt > cutoff_dt:
                break
            forward_bars.append((b_dt, float(b[2]), float(b[3])))  # (date, high, low)

        if len(forward_bars) < 2:
            skipped += 1
            continue

        # Determine outcome: scan bar by bar
        outcome = "hard"  # default: neither hit
        sl_hit_bar = None
        sl_max_adverse = 0.0  # in sigma units
        tp_after_sl = False

        # Phase 1: find first event (TP or SL)
        for bdt, hi, lo in forward_bars:
            hi_ret = (hi - entry) / entry * 100.0
            hi_sigma = hi_ret / sigma

            if lo <= tp_price:  # TP hit
                if sl_hit_bar is None:
                    outcome = "tp_direct"
                else:
                    outcome = "tp_after_sl"
                    tp_after_sl = True
                break
            if hi >= sl_price:  # SL hit
                if sl_hit_bar is None:
                    sl_hit_bar = bdt
                    sl_max_adverse = hi_sigma
                # continue walking to check recovery
                if hi_sigma > sl_max_adverse:
                    sl_max_adverse = hi_sigma

        if sl_hit_bar is not None and outcome == "hard":
            outcome = "sl_hard"  # SL hit, no recovery, window expired

        # Phase 2: if SL was hit first, continue scanning for TP recovery
        if sl_hit_bar is not None and outcome not in ("tp_direct", "tp_after_sl"):
            for bdt, hi, lo in forward_bars:
                if bdt <= sl_hit_bar:
                    continue
                if lo <= tp_price:
                    outcome = "sl_then_tp"
                    tp_after_sl = True
                    break

        # Bucket assignments
        sb = _score_bucket(int(overall))
        bs_val = breadth_map.get(sig_date)
        bb = _breadth_bucket(bs_val)
        key = (sb, bb)

        acc = stats[key]
        acc["total"] += 1

        if sl_hit_bar is None:
            if outcome in ("tp_direct", "hard"):
                acc["no_sl"] += 1
        else:
            acc["sl_hit"] += 1
            shakeout_b = _shakeout_bucket(sl_max_adverse)
            acc["by_shakeout"][shakeout_b]["sl_hit"] += 1
            if tp_after_sl:
                acc["sl_then_tp"] += 1
                acc["by_shakeout"][shakeout_b]["sl_then_tp"] += 1
                acc["by_shakeout"][shakeout_b]["sl_hit"] += 0  # already counted above
            else:
                acc["sl_then_hard"] += 1
            acc["sl_mag_sum"] += sl_max_adverse
            ev_rows.append((sb, bb, "sl_then_tp" if tp_after_sl else "sl_hard", shakeout_b, sl_max_adverse))

        processed += 1

    print(f"Processed: {processed}  Skipped: {skipped}")
    print()

    # --- Results ---
    score_buckets = ["<=5", "6-10", "11-15", "16-20", "21-25"]
    breadth_buckets = ["LOW", "MID", "HIGH"]

    print("=" * 110)
    print("P(RECOVERY TO TP | SL THRESHOLD BREACHED)  —  by score bucket x breadth regime")
    print("  SL breached = underlying rose >+0.728s.  Recovery = subsequently fell to -1.092s within 15d window.")
    print("=" * 110)

    # Compute break-even P(recovery) for EV comparison
    # EV(hold) = P_rec * TP_PNL + (1-P_rec) * HARD_PNL
    # Break-even: EV(hold) = RECYCLE_EV
    # P_rec * 0.30 + (1-P_rec) * (-0.50) = 0.05
    # 0.30*P_rec - 0.50 + 0.50*P_rec = 0.05
    # 0.80*P_rec = 0.55
    # P_rec = 0.6875
    be_prec = (RECYCLE_EV - HARD_PNL) / (PUT_TP_PNL - HARD_PNL)
    print(f"\nBreak-even P(recovery) to beat recycling (EV={RECYCLE_EV:.0%}): {be_prec:.1%}")
    print()

    hdr = f"{'Bucket':<10} {'Breadth':<8} {'N':>6} {'SL%':>7} {'P(rec|SL)':>11} {'EV(hold)':>10} {'Beat?':>7} {'AvgSLmag':>9}"
    print(hdr)
    print("-" * len(hdr))

    summary: dict[str, list] = defaultdict(list)

    for sb in score_buckets:
        for bb in breadth_buckets:
            key = (sb, bb)
            if key not in stats:
                continue
            acc = stats[key]
            n = acc["total"]
            if n < 10:
                continue
            sl = acc["sl_hit"]
            rec = acc["sl_then_tp"]
            sl_pct = sl / n if n > 0 else 0
            p_rec = rec / sl if sl > 0 else 0
            avg_sl_mag = acc["sl_mag_sum"] / sl if sl > 0 else 0
            ev_hold = p_rec * PUT_TP_PNL + (1 - p_rec) * HARD_PNL
            beats = "YES *" if p_rec >= be_prec else "no"
            print(f"{sb:<10} {bb:<8} {n:>6} {sl_pct:>6.1%} {p_rec:>11.1%} {ev_hold:>+10.1%} {beats:>7} {avg_sl_mag:>9.3f}s")
            summary[sb].append((bb, n, sl, rec, p_rec, ev_hold, sl_pct))
        print()

    # --- Shakeout magnitude breakdown ---
    print("=" * 110)
    print("P(RECOVERY | SL BREACHED)  —  by shakeout magnitude  (aggregated across all breadth regimes)")
    print("=" * 110)
    shakeout_buckets_order = ["MILD(0.73-1.0s)", "MOD(1.0-1.5s)", "EXTREME(>1.5s)"]

    # Aggregate by score bucket x shakeout magnitude
    shakeout_agg: dict[tuple, dict] = defaultdict(lambda: {"sl_hit": 0, "sl_then_tp": 0})
    for sb in score_buckets:
        for bb in breadth_buckets:
            key = (sb, bb)
            if key not in stats:
                continue
            for shk, d in stats[key]["by_shakeout"].items():
                shakeout_agg[(sb, shk)]["sl_hit"] += d["sl_hit"]
                shakeout_agg[(sb, shk)]["sl_then_tp"] += d["sl_then_tp"]

    hdr2 = f"{'Bucket':<10} {'Shakeout':<20} {'SL_N':>6} {'Rec_N':>6} {'P(rec|SL)':>11} {'EV(hold)':>10} {'Beat?':>7}"
    print(hdr2)
    print("-" * len(hdr2))
    for sb in score_buckets:
        for shk in shakeout_buckets_order:
            d = shakeout_agg.get((sb, shk), {"sl_hit": 0, "sl_then_tp": 0})
            sl = d["sl_hit"]; rec = d["sl_then_tp"]
            if sl < 5:
                continue
            p_rec = rec / sl
            ev_hold = p_rec * PUT_TP_PNL + (1 - p_rec) * HARD_PNL
            beats = "YES *" if p_rec >= be_prec else "no"
            print(f"{sb:<10} {shk:<20} {sl:>6} {rec:>6} {p_rec:>11.1%} {ev_hold:>+10.1%} {beats:>7}")
        print()

    # --- 3x3 regime table: breadth x shakeout for <=15 bucket only ---
    print("=" * 110)
    print("REGIME TABLE: breadth x shakeout magnitude  (put signals <=15, the extreme bucket)")
    print("=" * 110)
    print(f"{'Shakeout':<22} {'LOW breadth':>14} {'MID breadth':>14} {'HIGH breadth':>14}")
    print(f"{'':22} {'P(rec) EV':>14} {'P(rec) EV':>14} {'P(rec) EV':>14}")
    print("-" * 66)

    extreme_agg: dict[tuple, dict] = defaultdict(lambda: {"sl_hit": 0, "sl_then_tp": 0})
    for sb in ["<=5", "6-10", "11-15"]:  # <=15 bucket
        for bb in breadth_buckets:
            key = (sb, bb)
            if key not in stats:
                continue
            for shk, d in stats[key]["by_shakeout"].items():
                extreme_agg[(shk, bb)]["sl_hit"] += d["sl_hit"]
                extreme_agg[(shk, bb)]["sl_then_tp"] += d["sl_then_tp"]

    for shk in shakeout_buckets_order:
        row = f"{shk:<22}"
        for bb in breadth_buckets:
            d = extreme_agg.get((shk, bb), {"sl_hit": 0, "sl_then_tp": 0})
            sl = d["sl_hit"]; rec = d["sl_then_tp"]
            if sl < 3:
                row += f"{'  N/A':>14}"
            else:
                p_rec = rec / sl
                ev_hold = p_rec * PUT_TP_PNL + (1 - p_rec) * HARD_PNL
                flag = "*" if p_rec >= be_prec else ""
                row += f"  {p_rec:.0%} {ev_hold:>+5.0%}{flag:>2}".rjust(14)
        print(row)

    print()
    print(f"* = P(recovery) >= {be_prec:.1%} break-even vs recycling at EV={RECYCLE_EV:.0%}")
    print()

    # --- Summary recommendation ---
    print("=" * 110)
    print("SUMMARY: EV(hold through shakeout) vs EV(recycle at -20% SL)")
    print(f"  Recycle EV: +{RECYCLE_EV:.0%} per trade")
    print(f"  Hold EV formula: P(rec) x +{PUT_TP_PNL:.0%}  +  (1-P(rec)) x {HARD_PNL:.0%}")
    print(f"  Break-even P(recovery) = {be_prec:.1%}")
    print()
    print("  Cells marked YES * have sufficient recovery rate to justify holding over recycling.")
    print("  If no cells hit YES: current -20% SL is correct. Widen only in marked cells.")
    print("=" * 110)


if __name__ == "__main__":
    main()
