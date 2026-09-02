"""Weekly-whiplash kill-switch probe (weatherization item W, the last open sub-item).

Closes Priorities #7/#9 (weekly-adjustment day-over-day score whiplash — COHR 86->52,
VICR 85->61) IF the v69 transition blend (point-in-time partial-week + completed-week,
weighted by bars-elapsed — live since v69, baked into all v74 recalc'd scores) tamed it.

The GROSS 75->{<75} flip rate is NOT the metric — it is ~58%, dominated by benign
boundary jitter (median day-over-day |Δ|=5: a 76 ticking to 74) and by GENUINE
price-driven de-qualification. The real COHR/VICR pathology is a LARGE score swing
(|Δoverall|>=15) with LITTLE underlying price movement (score-instability, not a real
move). So the whiplash metric is: among 75+ adjacent-day pairs, the fraction with
|Δoverall|>=15 AND |next-day price move| < 2%.

Per known-issues #9 floors (now applied to the score-instability metric, not the gross
rate): < 5% -> tamed -> CLOSE NULL ; >=15% -> high-value fix -> INVESTIGATE.

READ-ONLY: two indexed windowed reads (scores v74 + PriceHistory for the 75+ symbols),
3 columns each. No writes.
Run: PYTHONIOENCODING=utf-8 python experiments/weekly_flip_probe/probe.py
"""
import statistics as st
from collections import defaultdict
from datetime import date as _date
from database.models.core import Score, AlgorithmVersion
from database.models.technical import PriceHistory

FULL_CUTOFF = "2024-06-01"            # ~2y for N + regime coverage
RECENT_CUTOFF = _date(2026, 1, 1)     # current-regime sub-window
BIG = 15.0                            # "large swing" (docs flag >15-pt swings)
BIG2 = 20.0                           # COHR/VICR-magnitude (24-34 pt)
FLAT = 0.02                           # "little price move" (<2%)
FLAT1 = 0.01                          # stricter (<1%)


def _adjacent(d0, d1):
    return 0 < (d1 - d0).days <= 4    # adjacent trading day (allow a weekend gap)


def main():
    v = AlgorithmVersion.get_active_scores_version()
    vid = v.id
    cur = Score._meta.database.execute_sql(
        "SELECT symbol, date, overall FROM scores "
        "WHERE version_id=%s AND date>=%s AND overall IS NOT NULL ORDER BY symbol, date",
        (vid, FULL_CUTOFF))
    rows = cur.fetchall()
    print(f"active v{vid} | score rows={len(rows):,} from {FULL_CUTOFF}")

    by_sym = defaultdict(list)
    for sym, d, ov in rows:
        by_sym[sym].append((d, float(ov)))

    # symbols that ever hit 75+ in the window -> pull their close prices
    hot = sorted({s for s, seq in by_sym.items() if any(o >= 75 for _, o in seq)})
    close = {}
    if hot:
        ph = PriceHistory.select(PriceHistory.symbol, PriceHistory.date, PriceHistory.close).where(
            (PriceHistory.symbol.in_(hot)) & (PriceHistory.date >= FULL_CUTOFF)).tuples()
        for s, d, c in ph:
            if c is not None:
                close[(s, d)] = float(c)
    print(f"75+ symbols={len(hot)} | price points={len(close):,}")

    def tally(min_d0=None):
        n75 = flip_out = big = big2 = 0
        whip = whip2 = whip1 = 0          # |Δ|>=15 & flat ; >=20 & flat ; >=15 & <1%
        priced = 0
        deltas = []
        for s, seq in by_sym.items():
            for i in range(len(seq) - 1):
                d0, o0 = seq[i]
                d1, o1 = seq[i + 1]
                if not _adjacent(d0, d1):
                    continue
                if min_d0 is not None and d0 < min_d0:
                    continue
                if o0 < 75:
                    continue
                n75 += 1
                dd = abs(o1 - o0)
                deltas.append(dd)
                if o1 < 75:
                    flip_out += 1
                if dd >= BIG:
                    big += 1
                if dd >= BIG2:
                    big2 += 1
                c0 = close.get((s, d0))
                c1 = close.get((s, d1))
                if c0 and c1:
                    priced += 1
                    pm = abs(c1 - c0) / c0
                    if dd >= BIG and pm < FLAT:
                        whip += 1
                    if dd >= BIG and pm < FLAT1:
                        whip1 += 1
                    if dd >= BIG2 and pm < FLAT:
                        whip2 += 1
        return dict(n75=n75, flip_out=flip_out, big=big, big2=big2, deltas=deltas,
                    priced=priced, whip=whip, whip2=whip2, whip1=whip1)

    def report(label, t):
        n = t['n75']
        if not n:
            print(f"\n[{label}] no 75+ pairs"); return None
        d = sorted(t['deltas'])
        p95 = d[min(len(d) - 1, int(0.95 * len(d)))]
        pr = max(1, t['priced'])
        whip_rate = 100 * t['whip'] / pr        # the score-instability metric
        print(f"\n[{label}] 75+ adjacent-day pairs N={n:,}  (priced {t['priced']:,})")
        print(f"  gross flip-out (75+ -> <75):     {100*t['flip_out']/n:5.2f}%  "
              f"(boundary jitter + genuine; NOT the whiplash metric)")
        print(f"  large swing |Δ|>=15:             {100*t['big']/n:5.2f}%   "
              f"|Δ|>=20: {100*t['big2']/n:5.2f}%   (median |Δ|={st.median(d):.0f}, p95={p95:.0f})")
        print(f"  *** WHIPLASH |Δ|>=15 & price<2%: {whip_rate:5.2f}%  ({t['whip']}/{t['priced']}) ***")
        print(f"      |Δ|>=15 & price<1%:          {100*t['whip1']/pr:5.2f}%   "
              f"|Δ|>=20 & price<2%: {100*t['whip2']/pr:5.2f}%")
        return whip_rate

    wr_full = report(f"FULL {FULL_CUTOFF}+", tally())
    wr_rec = report(f"RECENT {RECENT_CUTOFF.isoformat()}+", tally(min_d0=RECENT_CUTOFF))

    print("\n" + "=" * 64)
    print("  VERDICT (score-instability whiplash = |Δ|>=15 with <2% price move):")
    for lbl, r in (("full", wr_full), ("recent", wr_rec)):
        if r is None:
            continue
        verdict = ("CLOSE NULL — blend tamed it" if r < 5 else
                   "INVESTIGATE — high-value fix" if r >= 15 else
                   "BORDERLINE")
        print(f"    [{lbl:6}] {r:.2f}%  -> {verdict}")
    print("=" * 64)


if __name__ == "__main__":
    main()
