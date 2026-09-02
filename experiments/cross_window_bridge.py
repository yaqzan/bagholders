"""
Cross-window conditional win rate + bridge (salvage) analysis.

Questions answered:
  1. Conditional win rates: P(win_15d | win_7d) vs P(win_15d | fail_7d)
     Does 7d failure predict 15d/30d failure, or is it recoverable?

  2. Bridge analysis: for 7d *expire* cases (still in trade, stop not hit),
     treat the day-7 close as a fresh entry. Does that new position hit
     K*sigma scaled to the remaining window (8 cal days to 15d, 23 to 30d)?

Usage:
    python experiments/cross_window_bridge.py [days]
    python experiments/cross_window_bridge.py 365    # 1y (default)
    python experiments/cross_window_bridge.py 1825   # 5y
"""
import sys, os, math
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from database.models.core import AlgorithmVersion, Score
from database.models.technical import PriceHistory
from assess_scores import (
    _peak_side, _realized_vol_pct, _swing_walk,
    SWING_K_LOW, SWING_K_HIGH, SWING_M_LOW, SWING_M_HIGH,
    SWING_REFERENCE_DAYS,
)

DEFAULT_DAYS = 365
BUY_THRESHOLDS  = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]


def bridge_walk(closes, start_idx, side, vol_pct, highs, lows, dates, bridge_windows):
    """Walk forward from start_idx (the day-7 close used as fresh entry).

    bridge_windows: list of (label, remaining_cal_days)
    Returns dict of label -> 'win'/'stop'/'expire'/None (None = insufficient data)
    """
    if start_idx is None or start_idx >= len(closes) - 1:
        return {}
    entry = closes[start_idx]
    if entry <= 0 or vol_pct is None:
        return {}
    base_date = dates[start_idx]
    K_base = SWING_K_LOW  if side == 'low' else SWING_K_HIGH
    M_base = SWING_M_LOW  if side == 'low' else SWING_M_HIGH

    results = {}
    for label, W_cal in bridge_windows:
        cutoff = base_date + timedelta(days=W_cal)
        # Need at least one bar past the cutoff to call 'expire'
        if dates[-1] <= cutoff:
            results[label] = None
            continue

        scale  = math.sqrt(W_cal / SWING_REFERENCE_DAYS)
        k = K_base * scale
        m = M_base * scale
        if side == 'low':   # call: win = price rises
            t_win  = entry * (1 + k * vol_pct / 100)
            t_stop = entry * (1 - m * vol_pct / 100)
        else:               # put: win = price falls
            t_win  = entry * (1 - k * vol_pct / 100)
            t_stop = entry * (1 + m * vol_pct / 100)

        result = None
        for i in range(start_idx + 1, len(closes)):
            bar_date = dates[i]
            h = highs[i]
            l = lows[i]
            c = closes[i]

            if bar_date > cutoff:
                result = 'expire'
                break

            if side == 'low':
                win_touch  = h >= t_win
                stop_touch = l <= t_stop
            else:
                win_touch  = l <= t_win
                stop_touch = h >= t_stop

            if win_touch and stop_touch:
                result = 'win' if c >= entry else 'stop'
                break
            elif win_touch:
                result = 'win'
                break
            elif stop_touch:
                result = 'stop'
                break
        else:
            if result is None:
                result = 'expire'

        results[label] = result
    return results


def bucket_label(score):
    if score >= 50:
        # Check highest threshold first → non-overlapping bands (70-74, 75-79, ...)
        for t in BUY_THRESHOLDS:
            if score >= t:
                return f'{t}+'
    else:
        # Check tightest threshold first → non-overlapping bands (21-25, 16-20, ...)
        for t in reversed(SELL_THRESHOLDS):
            if score < t:
                return f'<{t}'
    return None


def empty_bucket():
    return dict(total=0, win7=0, win15=0, win30=0, win60=0, win90=0,
                # 7d -> 15d (expire bridge)
                fail7=0, fail7_stop=0, fail7_expire=0,
                fail7_win15=0, fail7_win30=0,
                bridge15_win=0, bridge15_n=0,
                bridge30_win=0, bridge30_n=0,
                # 15d -> 30d (expire bridge)
                fail15=0, fail15_stop=0, fail15_expire=0,
                fail15_win30=0,
                bridge30_from15_win=0, bridge30_from15_n=0,
                # 30d -> 60d (expire bridge)
                fail30=0, fail30_stop=0, fail30_expire=0,
                fail30_win60=0,
                bridge60_from30_win=0, bridge60_from30_n=0,
                # 60d -> 90d (expire bridge)
                fail60=0, fail60_stop=0, fail60_expire=0,
                fail60_win90=0,
                bridge90_from60_win=0, bridge90_from60_n=0,
                # Roll-up bridges: fresh ATM entry from day-X close after WIN at day-X
                roll7_n=0,  roll7_win=0,   # day-7 win  → fresh 8-day ATM entry
                roll15_n=0, roll15_win=0,  # day-15 win → fresh 15-day ATM entry
                roll30_n=0, roll30_win=0,  # day-30 win → fresh 30-day ATM entry
                roll60_n=0, roll60_win=0)  # day-60 win → fresh 30-day ATM entry


def main():
    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DAYS
    cutoff_date = date.today() - timedelta(days=lookback)

    version = AlgorithmVersion.get_active_scores_version()
    print(f'Algorithm : {version.git_commit[:8]}  ({getattr(version, "git_message", "")[:60]})')
    print(f'Lookback  : {lookback}d  (since {cutoff_date})')

    scores = list(Score.select(Score.symbol, Score.date, Score.overall).where(
        (Score.version == version) &
        (Score.date >= cutoff_date) &
        ((Score.overall >= 70) | (Score.overall <= 30))
    ).order_by(Score.symbol, Score.date))

    print(f'Signals   : {len(scores)} qualifying (>=70 or <=30)')

    symbols = set(s.symbol_id for s in scores)
    print(f'Symbols   : {len(symbols)} — loading price history...')

    price_cache = {}
    for sym in symbols:
        rows = list(PriceHistory.select(
            PriceHistory.date, PriceHistory.close, PriceHistory.high, PriceHistory.low
        ).where(PriceHistory.symbol == sym).order_by(PriceHistory.date))
        price_cache[sym] = [(r.date, float(r.close), float(r.high), float(r.low)) for r in rows]

    print('Running analysis...\n')

    buckets = {}

    skipped_no_data = 0
    skipped_no_vol  = 0
    skipped_no_fwd  = 0

    for s in scores:
        rows = price_cache.get(s.symbol_id, [])
        if not rows:
            skipped_no_data += 1
            continue

        dates  = [r[0] for r in rows]
        closes = [r[1] for r in rows]
        highs  = [r[2] for r in rows]
        lows   = [r[3] for r in rows]

        date_idx = {d: i for i, d in enumerate(dates)}
        base_idx = date_idx.get(s.date)
        if base_idx is None:
            skipped_no_data += 1
            continue

        side    = _peak_side(s.overall)
        vol_pct = _realized_vol_pct(closes, base_idx)
        if vol_pct is None:
            skipped_no_vol += 1
            continue

        swing = _swing_walk(closes, base_idx, side, vol_pct,
                            highs=highs, lows=lows, dates=dates)
        if swing is None:
            skipped_no_vol += 1
            continue

        r7  = swing.get('7d',  {}).get('result')
        r15 = swing.get('15d', {}).get('result')
        r30 = swing.get('30d', {}).get('result')
        r60 = swing.get('60d', {}).get('result')
        r90 = swing.get('90d', {}).get('result')
        if r7 is None or r15 is None or r30 is None:
            skipped_no_fwd += 1
            continue

        win7  = r7  == 'win'
        win15 = r15 == 'win'
        win30 = r30 == 'win'
        win60 = r60 == 'win' if r60 is not None else None
        win90 = r90 == 'win' if r90 is not None else None

        lbl = bucket_label(s.overall)
        if lbl is None:
            continue
        if lbl not in buckets:
            buckets[lbl] = empty_bucket()
        b = buckets[lbl]

        b['total'] += 1
        if win7:  b['win7']  += 1
        if win15: b['win15'] += 1
        if win30: b['win30'] += 1
        if win60: b['win60'] += 1
        if win90: b['win90'] += 1

        base_date = dates[base_idx]

        # ── 7d failure analysis ──────────────────────────────────────────────
        if not win7:
            b['fail7'] += 1
            if r7 == 'stop':
                b['fail7_stop'] += 1
            if win15: b['fail7_win15'] += 1
            if win30: b['fail7_win30'] += 1

            # Bridge from day-7: only valid for 7d expire (still in trade)
            if r7 == 'expire':
                b['fail7_expire'] += 1
                d7_cutoff = base_date + timedelta(days=7)
                d7_bar_idx = None
                for fi in range(base_idx + 1, len(dates)):
                    if dates[fi] <= d7_cutoff:
                        d7_bar_idx = fi
                    else:
                        break

                if d7_bar_idx is not None:
                    br = bridge_walk(closes, d7_bar_idx, side, vol_pct,
                                     highs, lows, dates,
                                     [('b15d', 8), ('b30d', 23)])
                    if br.get('b15d') is not None:
                        b['bridge15_n'] += 1
                        if br['b15d'] == 'win':
                            b['bridge15_win'] += 1
                    if br.get('b30d') is not None:
                        b['bridge30_n'] += 1
                        if br['b30d'] == 'win':
                            b['bridge30_win'] += 1

        # ── 15d failure analysis ─────────────────────────────────────────────
        if not win15:
            b['fail15'] += 1
            if r15 == 'stop':
                b['fail15_stop'] += 1
            if win30: b['fail15_win30'] += 1

            # Bridge from day-15: 15 remaining cal days to the 30d mark.
            # Valid only for expire (not stopped out — still technically in trade).
            if r15 == 'expire':
                b['fail15_expire'] += 1
                d15_cutoff = base_date + timedelta(days=15)
                d15_bar_idx = None
                for fi in range(base_idx + 1, len(dates)):
                    if dates[fi] <= d15_cutoff:
                        d15_bar_idx = fi
                    else:
                        break

                if d15_bar_idx is not None:
                    br15 = bridge_walk(closes, d15_bar_idx, side, vol_pct,
                                       highs, lows, dates,
                                       [('b30d_from15', 15)])
                    if br15.get('b30d_from15') is not None:
                        b['bridge30_from15_n'] += 1
                        if br15['b30d_from15'] == 'win':
                            b['bridge30_from15_win'] += 1

        # ── 30d failure analysis ─────────────────────────────────────────────
        if r60 is not None and not win30:
            b['fail30'] += 1
            if r30 == 'stop':
                b['fail30_stop'] += 1
            if win60: b['fail30_win60'] += 1

            if r30 == 'expire':
                b['fail30_expire'] += 1
                d30_cutoff = base_date + timedelta(days=30)
                d30_bar_idx = None
                for fi in range(base_idx + 1, len(dates)):
                    if dates[fi] <= d30_cutoff:
                        d30_bar_idx = fi
                    else:
                        break

                if d30_bar_idx is not None:
                    br30 = bridge_walk(closes, d30_bar_idx, side, vol_pct,
                                       highs, lows, dates,
                                       [('b60d_from30', 30)])
                    if br30.get('b60d_from30') is not None:
                        b['bridge60_from30_n'] += 1
                        if br30['b60d_from30'] == 'win':
                            b['bridge60_from30_win'] += 1

        # ── 60d failure analysis ─────────────────────────────────────────────
        if r60 is not None and r90 is not None and not win60:
            b['fail60'] += 1
            if r60 == 'stop':
                b['fail60_stop'] += 1
            if win90: b['fail60_win90'] += 1

            if r60 == 'expire':
                b['fail60_expire'] += 1
                d60_cutoff = base_date + timedelta(days=60)
                d60_bar_idx = None
                for fi in range(base_idx + 1, len(dates)):
                    if dates[fi] <= d60_cutoff:
                        d60_bar_idx = fi
                    else:
                        break

                if d60_bar_idx is not None:
                    br60 = bridge_walk(closes, d60_bar_idx, side, vol_pct,
                                       highs, lows, dates,
                                       [('b90d_from60', 30)])
                    if br60.get('b90d_from60') is not None:
                        b['bridge90_from60_n'] += 1
                        if br60['b90d_from60'] == 'win':
                            b['bridge90_from60_win'] += 1

        # ── 7d WIN: roll-up bridge — fresh ATM from day-7 close ─────────────
        if win7:
            d7_cutoff = base_date + timedelta(days=7)
            d7_bar_idx = None
            for fi in range(base_idx + 1, len(dates)):
                if dates[fi] <= d7_cutoff:
                    d7_bar_idx = fi
                else:
                    break
            if d7_bar_idx is not None:
                br7w = bridge_walk(closes, d7_bar_idx, side, vol_pct,
                                   highs, lows, dates, [('roll7', 8)])
                if br7w.get('roll7') is not None:
                    b['roll7_n'] += 1
                    if br7w['roll7'] == 'win':
                        b['roll7_win'] += 1

        # ── 15d WIN: roll-up bridge — fresh ATM from day-15 close ───────────
        if win15:
            d15_cutoff = base_date + timedelta(days=15)
            d15_bar_idx = None
            for fi in range(base_idx + 1, len(dates)):
                if dates[fi] <= d15_cutoff:
                    d15_bar_idx = fi
                else:
                    break
            if d15_bar_idx is not None:
                br15w = bridge_walk(closes, d15_bar_idx, side, vol_pct,
                                    highs, lows, dates, [('roll15', 15)])
                if br15w.get('roll15') is not None:
                    b['roll15_n'] += 1
                    if br15w['roll15'] == 'win':
                        b['roll15_win'] += 1

        # ── 30d WIN: roll-up bridge — fresh ATM from day-30 close ───────────
        if win30 and r60 is not None:
            d30_cutoff = base_date + timedelta(days=30)
            d30_bar_idx = None
            for fi in range(base_idx + 1, len(dates)):
                if dates[fi] <= d30_cutoff:
                    d30_bar_idx = fi
                else:
                    break
            if d30_bar_idx is not None:
                br30w = bridge_walk(closes, d30_bar_idx, side, vol_pct,
                                    highs, lows, dates, [('roll30', 30)])
                if br30w.get('roll30') is not None:
                    b['roll30_n'] += 1
                    if br30w['roll30'] == 'win':
                        b['roll30_win'] += 1

        # ── 60d WIN: roll-up bridge — fresh ATM from day-60 close ───────────
        if win60 and r90 is not None:
            d60_cutoff = base_date + timedelta(days=60)
            d60_bar_idx = None
            for fi in range(base_idx + 1, len(dates)):
                if dates[fi] <= d60_cutoff:
                    d60_bar_idx = fi
                else:
                    break
            if d60_bar_idx is not None:
                br60w = bridge_walk(closes, d60_bar_idx, side, vol_pct,
                                    highs, lows, dates, [('roll60', 30)])
                if br60w.get('roll60') is not None:
                    b['roll60_n'] += 1
                    if br60w['roll60'] == 'win':
                        b['roll60_win'] += 1

    print(f'Skipped: {skipped_no_data} no price data | {skipped_no_vol} no vol | {skipped_no_fwd} insufficient fwd\n')

    # ── Print tables ──────────────────────────────────────────────────────────
    hdr7 = (f"{'Bucket':>7} {'N':>5} | "
            f"{'WR7':>6} {'WR15':>6} {'WR30':>6} | "
            f"{'Fail7':>5} {'Stop':>5} {'Exp':>5} | "
            f"{'@15d':>6} {'@30d':>6} | "
            f"{'BrgN':>5} {'Brg15':>6} {'Brg30':>6}")
    hdr15 = (f"{'Bucket':>7} {'N':>5} | "
             f"{'WR15':>6} {'WR30':>6} | "
             f"{'Fail15':>6} {'Stop':>5} {'Exp':>5} | "
             f"{'@30d':>6} | "
             f"{'BrgN':>5} {'Brg30*':>7}")
    hdr30 = (f"{'Bucket':>7} {'N':>5} | "
             f"{'WR30':>6} {'WR60':>6} | "
             f"{'Fail30':>6} {'Stop':>5} {'Exp':>5} | "
             f"{'@60d':>6} | "
             f"{'BrgN':>5} {'Brg60*':>7}")
    hdr60 = (f"{'Bucket':>7} {'N':>5} | "
             f"{'WR60':>6} {'WR90':>6} | "
             f"{'Fail60':>6} {'Stop':>5} {'Exp':>5} | "
             f"{'@90d':>6} | "
             f"{'BrgN':>5} {'Brg90*':>7}")

    def pct(a, b):
        return f'{a/b*100:>5.1f}%' if b else '   n/a'

    def print_group_7d(thresholds, side_char):
        lbls = [f'{t}+' if side_char == 'C' else f'<{t}' for t in thresholds]
        print(hdr7)
        print('-' * len(hdr7))
        for lbl in lbls:
            b = buckets.get(lbl)
            if not b or b['total'] == 0:
                continue
            n  = b['total']
            f7 = b['fail7'] or 1
            print(
                f"{lbl:>7} {n:>5} | "
                f"{pct(b['win7'],n)} {pct(b['win15'],n)} {pct(b['win30'],n)} | "
                f"{b['fail7']:>5} {b['fail7_stop']:>5} {b['fail7_expire']:>5} | "
                f"{pct(b['fail7_win15'],f7)} {pct(b['fail7_win30'],f7)} | "
                f"{b['bridge15_n']:>5} {pct(b['bridge15_win'],b['bridge15_n'] or 1)} {pct(b['bridge30_win'],b['bridge30_n'] or 1)}"
            )
        print()

    def print_group_15d(thresholds, side_char):
        lbls = [f'{t}+' if side_char == 'C' else f'<{t}' for t in thresholds]
        print(hdr15)
        print('-' * len(hdr15))
        for lbl in lbls:
            b = buckets.get(lbl)
            if not b or b['total'] == 0:
                continue
            n   = b['total']
            f15 = b['fail15'] or 1
            print(
                f"{lbl:>7} {n:>5} | "
                f"{pct(b['win15'],n)} {pct(b['win30'],n)} | "
                f"{b['fail15']:>6} {b['fail15_stop']:>5} {b['fail15_expire']:>5} | "
                f"{pct(b['fail15_win30'],f15)} | "
                f"{b['bridge30_from15_n']:>5} {pct(b['bridge30_from15_win'],b['bridge30_from15_n'] or 1)}"
            )
        print()

    def print_group_30d(thresholds, side_char):
        lbls = [f'{t}+' if side_char == 'C' else f'<{t}' for t in thresholds]
        print(hdr30)
        print('-' * len(hdr30))
        for lbl in lbls:
            b = buckets.get(lbl)
            if not b or b['total'] == 0:
                continue
            n   = b['total']
            f30 = b['fail30'] or 1
            print(
                f"{lbl:>7} {n:>5} | "
                f"{pct(b['win30'],n)} {pct(b['win60'],n)} | "
                f"{b['fail30']:>6} {b['fail30_stop']:>5} {b['fail30_expire']:>5} | "
                f"{pct(b['fail30_win60'],f30)} | "
                f"{b['bridge60_from30_n']:>5} {pct(b['bridge60_from30_win'],b['bridge60_from30_n'] or 1)}"
            )
        print()

    def print_group_60d(thresholds, side_char):
        lbls = [f'{t}+' if side_char == 'C' else f'<{t}' for t in thresholds]
        print(hdr60)
        print('-' * len(hdr60))
        for lbl in lbls:
            b = buckets.get(lbl)
            if not b or b['total'] == 0:
                continue
            n   = b['total']
            f60 = b['fail60'] or 1
            print(
                f"{lbl:>7} {n:>5} | "
                f"{pct(b['win60'],n)} {pct(b['win90'],n)} | "
                f"{b['fail60']:>6} {b['fail60_stop']:>5} {b['fail60_expire']:>5} | "
                f"{pct(b['fail60_win90'],f60)} | "
                f"{b['bridge90_from60_n']:>5} {pct(b['bridge90_from60_win'],b['bridge90_from60_n'] or 1)}"
            )
        print()

    print('=' * 60)
    print('7d -> 15d/30d TRANSITION')
    print('=' * 60)
    print('CALLS (score >= 70)')
    print_group_7d(BUY_THRESHOLDS, 'C')
    print('PUTS (score <= 30)')
    print_group_7d(SELL_THRESHOLDS, 'P')

    print('=' * 60)
    print('15d -> 30d TRANSITION  (* fresh 15-day entry from day-15 close)')
    print('=' * 60)
    print('CALLS (score >= 70)')
    print_group_15d(BUY_THRESHOLDS, 'C')
    print('PUTS (score <= 30)')
    print_group_15d(SELL_THRESHOLDS, 'P')

    print('=' * 60)
    print('30d -> 60d TRANSITION  (* fresh 30-day entry from day-30 close)')
    print('=' * 60)
    print('CALLS (score >= 70)')
    print_group_30d(BUY_THRESHOLDS, 'C')
    print('PUTS (score <= 30)')
    print_group_30d(SELL_THRESHOLDS, 'P')

    print('=' * 60)
    print('60d -> 90d TRANSITION  (* fresh 30-day entry from day-60 close)')
    print('=' * 60)
    print('CALLS (score >= 70)')
    print_group_60d(BUY_THRESHOLDS, 'C')
    print('PUTS (score <= 30)')
    print_group_60d(SELL_THRESHOLDS, 'P')

    # ── Roll-up bridge print section ─────────────────────────────────────────
    hdr_roll = (f"{'Bucket':>7} {'N':>5} | "
                f"{'WR7':>6} {'WR15':>6} {'WR30':>6} {'WR60':>6} | "
                f"{'R7N':>5} {'Roll7%':>7}  "
                f"{'R15N':>5} {'Roll15%':>8}  "
                f"{'R30N':>5} {'Roll30%':>8}  "
                f"{'R60N':>5} {'Roll60%':>8}")

    def print_group_rollup(thresholds, side_char):
        lbls = [f'{t}+' if side_char == 'C' else f'<{t}' for t in thresholds]
        print(hdr_roll)
        print('-' * len(hdr_roll))
        for lbl in lbls:
            b = buckets.get(lbl)
            if not b or b['total'] == 0:
                continue
            n = b['total']
            print(
                f"{lbl:>7} {n:>5} | "
                f"{pct(b['win7'],n)} {pct(b['win15'],n)} {pct(b['win30'],n)} {pct(b['win60'],n)} | "
                f"{b['roll7_n']:>5} {pct(b['roll7_win'], b['roll7_n'] or 1):>7}  "
                f"{b['roll15_n']:>5} {pct(b['roll15_win'], b['roll15_n'] or 1):>8}  "
                f"{b['roll30_n']:>5} {pct(b['roll30_win'], b['roll30_n'] or 1):>8}  "
                f"{b['roll60_n']:>5} {pct(b['roll60_win'], b['roll60_n'] or 1):>8}"
            )
        print()

    print('=' * 60)
    print('ROLL-UP BRIDGE WR (* fresh ATM entry opened AFTER winning)')
    print('  Roll7%:  day-7 close as new entry, fresh 8-day K*sigma target')
    print('  Roll15%: day-15 close as new entry, fresh 15-day K*sigma target')
    print('  Roll30%: day-30 close as new entry, fresh 30-day K*sigma target')
    print('  Roll60%: day-60 close as new entry, fresh 30-day K*sigma target')
    print('=' * 60)
    print('CALLS (score >= 70)')
    print_group_rollup(BUY_THRESHOLDS, 'C')
    print('PUTS (score <= 30)')
    print_group_rollup(SELL_THRESHOLDS, 'P')

    print('Column guide (7d section):')
    print('  WR7/15/30   Overall win rate at each horizon (from original entry)')
    print('  Fail7       Count of 7d failures  |  Stop = stop triggered  |  Exp = expired (still in trade)')
    print('  @15d/@30d   Recovery rate to ORIGINAL 15d/30d target within the 7d-fail cohort')
    print('  BrgN        Expire cases with enough fwd data for bridge')
    print('  Brg15       Bridge WR: day-7 close as new entry, fresh 8-day K*sigma target')
    print('  Brg30       Bridge WR: day-7 close as new entry, fresh 23-day K*sigma target')
    print()
    print('Column guide (15d section):')
    print('  Fail15      Count of 15d failures  |  Stop = stop triggered  |  Exp = expired (still in trade)')
    print('  @30d        Recovery rate to ORIGINAL 30d target within the 15d-fail cohort')
    print('  BrgN        Expire cases with enough fwd data')
    print('  Brg30*      Bridge WR: day-15 close as new entry, fresh 15-day K*sigma target')
    print()
    print('Column guide (30d section):')
    print('  Fail30      Count of 30d failures  |  Stop = stop triggered  |  Exp = expired (still in trade)')
    print('  @60d        Recovery rate to ORIGINAL 60d target within the 30d-fail cohort')
    print('  BrgN        Expire cases with enough fwd data')
    print('  Brg60*      Bridge WR: day-30 close as new entry, fresh 30-day K*sigma target')
    print()
    print('Column guide (60d section):')
    print('  Fail60      Count of 60d failures  |  Stop = stop triggered  |  Exp = expired (still in trade)')
    print('  @90d        Recovery rate to ORIGINAL 90d target within the 60d-fail cohort')
    print('  BrgN        Expire cases with enough fwd data')
    print('  Brg90*      Bridge WR: day-60 close as new entry, fresh 30-day K*sigma target')
    print()
    print('Column guide (roll-up section):')
    print('  RxN         Winner cases with enough fwd data for the roll-up bridge')
    print('  RollX%      Fresh ATM entry from day-X close (same K*sigma, fresh window)')
    print('              Answers: "I closed my ITM option at target — what WR does a fresh entry get?"')
    print('              Distinct from P(win_next|win_prior) which measures the ORIGINAL position continuing.')


if __name__ == '__main__':
    main()
