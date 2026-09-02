"""
roll_continuation.py — "does rolling a loser still have edge?"

For each CALL peak (score >= 80, peak_type='HIGH') in the historic_peaks cache,
measures whether signals that did NOT hit the win target within 15d eventually
hit it by 30d or 60d — quantifying the continuation edge implied by the
74% -> 81% WR jump from 15d to 30d in the assessment data.

Also splits fwd_peak_delta_30d / _60d by win_15d outcome to show how much
additional rally (in raw %) happens AFTER the 15d window for winners vs losers.

Usage:
    python -m experiments.roll_continuation
"""

from collections import defaultdict
from colorama import Fore, Style

from database.models.core import HistoricPeak


BUCKETS = [
    ('90+',   lambda s: s >= 90),
    ('85-89', lambda s: 85 <= s < 90),
    ('80-84', lambda s: 80 <= s < 85),
]


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _pct(num, den):
    return f"{100 * num / den:.1f}%" if den else "  —  "


def _fmt_num(v, width=7, dec=2):
    if v is None:
        return " " * width
    return f"{v:{width}.{dec}f}"


def run():
    peaks = list(
        HistoricPeak
        .select()
        .where(HistoricPeak.peak_type == 'HIGH')
    )

    print(Fore.CYAN + f"\nLoaded {len(peaks)} HIGH (call) peaks from historic_peaks "
          f"(last 365 days, active algorithm version)\n" + Style.RESET_ALL)

    # Group by bucket
    grouped = defaultdict(list)
    for p in peaks:
        for label, fn in BUCKETS:
            if fn(p.peak_score):
                grouped[label].append(p)
                break

    # --- Table 1: Unconditional vs Conditional win rates -----------------
    print(Fore.YELLOW + "=" * 100)
    print("TABLE 1 — Unconditional vs Conditional Win Rates (CALL side, score >= 80)")
    print("=" * 100 + Style.RESET_ALL)
    header = (
        f"{'Bucket':<7} {'N':>5} | "
        f"{'WR15':>7} {'WR30':>7} {'WR60':>7} | "
        f"{'P(W30|L15)':>11} {'P(W60|L15)':>11} {'P(W60|L30)':>11} | "
        f"{'Lift30':>8} {'Lift60':>8}"
    )
    print(header)
    print("-" * len(header))

    for label, _ in BUCKETS:
        rows = grouped[label]
        # Require win_15d and win_30d non-null for 30d conditional;
        # win_60d also non-null for 60d conditional
        n15 = sum(1 for p in rows if p.win_15d is not None)
        n30 = sum(1 for p in rows if p.win_30d is not None)
        n60 = sum(1 for p in rows if p.win_60d is not None)
        w15 = sum(p.win_15d for p in rows if p.win_15d is not None)
        w30 = sum(p.win_30d for p in rows if p.win_30d is not None)
        w60 = sum(p.win_60d for p in rows if p.win_60d is not None)

        # Conditional P(win_30d=1 | win_15d=0):
        #   denominator = peaks where win_15d=0 AND win_30d not null
        #   numerator   = those where win_30d=1
        cond_30_g_L15 = [(p.win_30d) for p in rows
                         if p.win_15d == 0 and p.win_30d is not None]
        cond_60_g_L15 = [(p.win_60d) for p in rows
                         if p.win_15d == 0 and p.win_60d is not None]
        cond_60_g_L30 = [(p.win_60d) for p in rows
                         if p.win_30d == 0 and p.win_60d is not None]

        wr15 = w15 / n15 if n15 else None
        wr30 = w30 / n30 if n30 else None
        wr60 = w60 / n60 if n60 else None
        p30_g_L15 = sum(cond_30_g_L15) / len(cond_30_g_L15) if cond_30_g_L15 else None
        p60_g_L15 = sum(cond_60_g_L15) / len(cond_60_g_L15) if cond_60_g_L15 else None
        p60_g_L30 = sum(cond_60_g_L30) / len(cond_60_g_L30) if cond_60_g_L30 else None

        # Lift = conditional rate minus unconditional WR at the same horizon.
        # A positive lift means "losers at 15d ROLL into 30d at a rate above
        # the baseline 15d-WR" — the rolling edge. But the correct comparison
        # is conditional-30d vs unconditional-15d (both measure 15 more days
        # of forward opportunity from the respective entry points).
        lift30 = (p30_g_L15 - wr15) if (p30_g_L15 is not None and wr15 is not None) else None
        lift60 = (p60_g_L30 - wr15) if (p60_g_L30 is not None and wr15 is not None) else None

        print(
            f"{label:<7} {n15:>5} | "
            f"{_pct(w15, n15):>7} {_pct(w30, n30):>7} {_pct(w60, n60):>7} | "
            f"{_pct(sum(cond_30_g_L15), len(cond_30_g_L15)):>11} "
            f"{_pct(sum(cond_60_g_L15), len(cond_60_g_L15)):>11} "
            f"{_pct(sum(cond_60_g_L30), len(cond_60_g_L30)):>11} | "
            f"{_fmt_num(lift30*100 if lift30 is not None else None, 8, 1):>8} "
            f"{_fmt_num(lift60*100 if lift60 is not None else None, 8, 1):>8}"
        )

    print()
    print(Fore.WHITE + "Legend:" + Style.RESET_ALL)
    print("  WR{N}       = unconditional P(win within N days)")
    print("  P(W30|L15)  = P(win_30d=1 | win_15d=0)   — the 'roll a 15d loser' rate")
    print("  P(W60|L30)  = P(win_60d=1 | win_30d=0)   — the 'roll a 30d loser' rate")
    print("  Lift30      = P(W30|L15) - WR15  (positive = rolling loser beats fresh entry)")
    print("  Lift60      = P(W60|L30) - WR15")

    # --- Table 2: fwd_peak_delta split by win_15d -----------------------
    print()
    print(Fore.YELLOW + "=" * 100)
    print("TABLE 2 — Post-15d Rally Extension (fwd_peak_delta, raw %)")
    print("=" * 100 + Style.RESET_ALL)
    print("    delta_30d = fwd_peak_30d - fwd_peak_15d   (additional rally in days 15->30)")
    print("    delta_60d = fwd_peak_60d - fwd_peak_30d   (additional rally in days 30->60)")
    print()
    header2 = (
        f"{'Bucket':<7} | "
        f"{'Winners_15d':>12} {'mean_d30':>10} {'mean_d60':>10} | "
        f"{'Losers_15d':>12} {'mean_d30':>10} {'mean_d60':>10}"
    )
    print(header2)
    print("-" * len(header2))

    for label, _ in BUCKETS:
        rows = grouped[label]
        win15_rows = [p for p in rows if p.win_15d == 1]
        los15_rows = [p for p in rows if p.win_15d == 0]

        win_d30 = _mean([p.fwd_peak_delta_30d for p in win15_rows])
        win_d60 = _mean([p.fwd_peak_delta_60d for p in win15_rows])
        los_d30 = _mean([p.fwd_peak_delta_30d for p in los15_rows])
        los_d60 = _mean([p.fwd_peak_delta_60d for p in los15_rows])

        print(
            f"{label:<7} | "
            f"{len(win15_rows):>12} {_fmt_num(win_d30,10,2)} {_fmt_num(win_d60,10,2)} | "
            f"{len(los15_rows):>12} {_fmt_num(los_d30,10,2)} {_fmt_num(los_d60,10,2)}"
        )

    # --- Table 3: Among "rolled winners" (L15 then W30), what was MFE? ---
    print()
    print(Fore.YELLOW + "=" * 100)
    print("TABLE 3 — 'Rolled Winner' Profile (win_15d=0 AND win_30d=1)")
    print("=" * 100 + Style.RESET_ALL)
    print("    These are the signals that missed the 15d target but hit the 30d target.")
    print("    If rolling works, we'd buy a NEW 30 DTE option at day 15 targeting this group.")
    print()
    header3 = (
        f"{'Bucket':<7} | "
        f"{'N':>5} {'fwd_peak_15d':>13} {'fwd_peak_30d':>13} "
        f"{'delta_30d':>11} {'delta_60d':>11}"
    )
    print(header3)
    print("-" * len(header3))

    for label, _ in BUCKETS:
        rows = grouped[label]
        rolled = [p for p in rows if p.win_15d == 0 and p.win_30d == 1]
        if not rolled:
            print(f"{label:<7} | {0:>5}")
            continue

        mean_pk15 = _mean([p.fwd_peak_15d for p in rolled])
        mean_pk30 = _mean([p.fwd_peak_30d for p in rolled])
        mean_d30  = _mean([p.fwd_peak_delta_30d for p in rolled])
        mean_d60  = _mean([p.fwd_peak_delta_60d for p in rolled])

        print(
            f"{label:<7} | {len(rolled):>5} "
            f"{_fmt_num(mean_pk15,13,2)} {_fmt_num(mean_pk30,13,2)} "
            f"{_fmt_num(mean_d30,11,2)} {_fmt_num(mean_d60,11,2)}"
        )

    print()


if __name__ == '__main__':
    run()
