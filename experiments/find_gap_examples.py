"""
Find concrete (symbol, date) examples illustrating the early-stage vs
late-stage extension hypothesis.

For each V_GRAD2 sim HIGH peak that does NOT exist as a V3 DB HIGH peak:
  - look up raw RSI / raw Stoch / bb_pct at that date
  - categorize: early-stage (raws < 60) vs late-stage (raws ≥ 65)
  - look up 30d forward return for outcome

Goal: produce a short list of "late-stage HIGH that V_GRAD2 fires but V3 didn't"
plus a control list of "early-stage HIGH that V3 also fired", so the user can
compare them directly via explain-scores.
"""
from collections import defaultdict
from datetime import date, timedelta
from simulator import ScoreSimulator
from experiments.score_variants import VARIANTS
from assess_scores import extract_peaks
from database.models.core import AlgorithmVersion
from database.models.technical import PriceHistory, Indicator


def main():
    LOOKBACK = 365
    sim = ScoreSimulator(symbols=None, lookback_days=LOOKBACK)
    sim.scoring_fn = VARIANTS['V_GRAD2']
    sim_scores = sim.simulate()

    cutoff = date.today() - timedelta(days=LOOKBACK)

    # ── 1. V3 DB HIGH peaks (set of (symbol, date)) ─────────────────
    v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
    db_peaks = extract_peaks(lookback_days=LOOKBACK, version=v3)
    db_high_set = {(p.symbol_id, p.date) for p in db_peaks if p.overall >= 70}
    print(f"V3 DB HIGH peaks: {len(db_high_set)}")

    # ── 2. V_GRAD2 sim peak detection (mirror simulator.diff_assess) ─
    by_symbol = defaultdict(list)
    for (sym, d), score in sim_scores.items():
        if d < cutoff or score < 70:
            continue
        by_symbol[sym].append((d, score))

    sim_peaks = []
    for sym, entries in by_symbol.items():
        date_set = {d for d, _ in entries}
        pruned = set()
        for d, score in sorted(entries, key=lambda x: -x[1]):
            if (sym, d) in pruned:
                continue
            sim_peaks.append((sym, d, score))
            for direction in (-1, 1):
                walk = d + timedelta(days=direction)
                while walk in date_set and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += timedelta(days=direction)
    print(f"V_GRAD2 sim HIGH peaks: {len(sim_peaks)}")

    # ── 3. Categorize NEW (V_GRAD2 only) sim peaks ───────────────────
    new_peaks = [(s, d, sc) for s, d, sc in sim_peaks if (s, d) not in db_high_set]
    print(f"NEW sim peaks (in V_GRAD2, not in V3): {len(new_peaks)}")

    # ── 4. Bulk-load indicators + price history for those symbols ────
    syms = list({s for s, _, _ in new_peaks})
    ind_rows = list(
        Indicator.select(Indicator.symbol, Indicator.date, Indicator.rsi,
                         Indicator.stoch, Indicator.upper_band, Indicator.lower_band)
        .where(Indicator.symbol.in_(syms), Indicator.date >= cutoff)
        .namedtuples()
    )
    ind_by = {(r.symbol, r.date): r for r in ind_rows}

    ph_rows = list(
        PriceHistory.select(PriceHistory.symbol, PriceHistory.date, PriceHistory.close)
        .where(PriceHistory.symbol.in_(syms), PriceHistory.date >= cutoff)
        .namedtuples()
    )
    ph_by = defaultdict(dict)
    for r in ph_rows:
        ph_by[r.symbol][r.date] = float(r.close)

    def fwd_30d(sym, d):
        prices = ph_by.get(sym, {})
        entry = prices.get(d)
        if entry is None:
            return None
        target = d + timedelta(days=30)
        # walk forward to find closest trading day
        for offset in range(0, 8):
            candidate = target - timedelta(days=offset)
            if candidate in prices:
                return ((prices[candidate] - entry) / entry) * 100
        return None

    # ── 5. Categorize and pick examples ──────────────────────────────
    early_stage, late_stage = [], []
    for sym, d, score in new_peaks:
        ind = ind_by.get((sym, d))
        if ind is None or ind.rsi is None or ind.stoch is None:
            continue
        raw_rsi = float(ind.rsi)
        raw_stoch = float(ind.stoch)
        prices_d = ph_by.get(sym, {})
        close = prices_d.get(d)
        if close is None or ind.upper_band is None or ind.lower_band is None:
            continue
        bw = float(ind.upper_band) - float(ind.lower_band)
        if bw <= 0:
            continue
        bb_pct = (close - float(ind.lower_band)) / bw

        ret30 = fwd_30d(sym, d)
        rec = (sym, d, score, raw_rsi, raw_stoch, bb_pct, ret30)

        # Late-stage: raws already at/past extension
        if raw_rsi >= 65 and raw_stoch >= 65 and bb_pct >= 0.75:
            late_stage.append(rec)
        # Early-stage: raws still well within neutral
        elif raw_rsi < 60 and raw_stoch < 60 and bb_pct < 0.6:
            early_stage.append(rec)

    print(f"\nNEW signals categorized:")
    print(f"  Late-stage  (raw RSI≥65, Stoch≥65, BB≥0.75): {len(late_stage)}")
    print(f"  Early-stage (raw RSI<60, Stoch<60, BB<0.6) : {len(early_stage)}")

    def show(title, recs, limit=10):
        print(f"\n=== {title} (top {limit}) ===")
        print(f"  {'sym':6} {'date':12} {'score':>5} {'rsi':>6} {'stoch':>6} {'bb_pct':>7} {'ret30':>8}")
        # Sort by absolute return so most-illustrative outcomes float up
        recs_sorted = sorted([r for r in recs if r[6] is not None],
                             key=lambda x: abs(x[6]), reverse=True)
        for sym, d, sc, rsi, st, bbp, ret in recs_sorted[:limit]:
            print(f"  {sym:6} {str(d):12} {sc:>5} {rsi:>6.1f} {st:>6.1f} {bbp:>7.3f} {ret:>+7.1f}%")

    show("LATE-STAGE NEW HIGHs (V_GRAD2 fires, V3 didn't)", late_stage)
    show("EARLY-STAGE NEW HIGHs (V_GRAD2 fires, V3 didn't)", early_stage)


if __name__ == '__main__':
    main()
