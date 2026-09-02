"""
Phase-A-style per-trade sweep for the mis_stress_call_softener design.

Builds one ScoreSimulator (full universe, 22-now lookback), then for each
(call_dampen, mis_stress_full) variant:
  1. swap scoring_mod.compute_overall_score with the variant fn
  2. sim.simulate() (loads mis_stress_map keyed on mis_stress_full)
  3. peak-pick non-overlapping (overall>=70 or <=25)
  4. walk WR15 per cohort (calls 75+ split by mis-stress origin)
  5. emit per-window N + WR15

Gate (per-trade, must clear before portfolio MC):
  - 22-now CALL75+ N grows by 5-25%
  - 22-now CALL75+ overall WR15 stays within -1pp of baseline
  - 2024 N inflation <= 30% (avoid v22-style bull dilution)
  - 2023 CALL75+ N grows AND WR15 improves vs baseline (the headline win)

Reference baseline (v21 stored): 22-now CALL75+ N=4982, WR15=75.2%
"""
from __future__ import annotations
import math
import sys
import time
from collections import defaultdict, namedtuple
from datetime import date, timedelta

import numpy as np

import database.utils.scoring as scoring_mod
from experiments.ct_designs import make_ct_score, _ORIGINAL


# Lightweight peak record (mirrors phase A approach)
_FakePeak = namedtuple('_FakePeak', 'symbol date overall')


def _pick_peaks(scores, window_start, window_end):
    """Pick non-overlapping >=70 / <=25 peaks per symbol within window.
    Cluster gap = 7 trading days (matches historic_peaks)."""
    by_sym = defaultdict(list)
    for (sym, d), score in scores.items():
        if d < window_start or d > window_end:
            continue
        if score >= 70 or score <= 25:
            by_sym[sym].append((d, score))

    peaks = []
    for sym, lst in by_sym.items():
        lst.sort()
        last_d = None
        last_score = None
        cluster = []  # (date, score)
        for d, s in lst:
            if last_d is None or (d - last_d).days > 7:
                if cluster:
                    # pick most extreme in cluster
                    pick = max(cluster, key=lambda x: abs(x[1] - 50))
                    peaks.append(_FakePeak(sym, pick[0], pick[1]))
                cluster = [(d, s)]
            else:
                cluster.append((d, s))
            last_d = d
        if cluster:
            pick = max(cluster, key=lambda x: abs(x[1] - 50))
            peaks.append(_FakePeak(sym, pick[0], pick[1]))
    return peaks


def _vol_pct(rows, idx, lookback=60):
    start = max(0, idx - lookback)
    closes = [float(r.close) for r in rows[start:idx + 1] if r.close]
    if len(closes) < 20:
        return None
    rets = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
    return float(np.std(rets, ddof=0)) * 100.0


def _walk_wr15(peaks, ph_by_sym):
    """Vol-adjusted barrier-touch WR15 per peak.  K=2.0sigma calls, K=1.0 puts."""
    wins = total = 0
    scale = math.sqrt(15.0 / 30.0)
    for p in peaks:
        rows = ph_by_sym.get(p.symbol, [])
        if not rows:
            continue
        idx = next((i for i, r in enumerate(rows) if r.date == p.date), None)
        if idx is None or idx == 0:
            continue
        v = _vol_pct(rows, idx)
        if v is None or v <= 0:
            continue
        is_call = p.overall >= 50
        K = 2.0 if is_call else 1.0
        M = 5.0 if is_call else 2.0
        entry = float(rows[idx].close)
        if is_call:
            target = entry * (1.0 + K * v / 100.0 * scale)
            stop = entry * (1.0 - M * v / 100.0 * scale)
        else:
            target = entry * (1.0 - K * v / 100.0 * scale)
            stop = entry * (1.0 + M * v / 100.0 * scale)
        cutoff = p.date.toordinal() + 15
        verdict = None
        last = idx
        for j in range(idx + 1, len(rows)):
            if rows[j].date.toordinal() > cutoff:
                break
            last = j
            hi = float(rows[j].high)
            lo = float(rows[j].low)
            if is_call:
                if hi >= target:
                    verdict = 1; break
                if lo <= stop:
                    verdict = 0; break
            else:
                if lo <= target:
                    verdict = 1; break
                if hi >= stop:
                    verdict = 0; break
        if verdict is None and last > idx and rows[last].date.toordinal() > cutoff - 1:
            verdict = 0
        if verdict is None:
            continue
        wins += verdict
        total += 1
    return wins, total


def _walk_call75(peaks_75plus, ph_by_sym, mis_stress_map):
    """Walk and split by mis_stress origin."""
    res = {
        'all':         [0, 0],
        'misstress':   [0, 0],
        'neutral':     [0, 0],
    }
    scale = math.sqrt(15.0 / 30.0)
    for p in peaks_75plus:
        rows = ph_by_sym.get(p.symbol, [])
        if not rows:
            continue
        idx = next((i for i, r in enumerate(rows) if r.date == p.date), None)
        if idx is None or idx == 0:
            continue
        v = _vol_pct(rows, idx)
        if v is None or v <= 0:
            continue
        entry = float(rows[idx].close)
        target = entry * (1.0 + 2.0 * v / 100.0 * scale)
        stop = entry * (1.0 - 5.0 * v / 100.0 * scale)
        cutoff = p.date.toordinal() + 15
        verdict = None
        last = idx
        for j in range(idx + 1, len(rows)):
            if rows[j].date.toordinal() > cutoff:
                break
            last = j
            hi = float(rows[j].high)
            lo = float(rows[j].low)
            if hi >= target:
                verdict = 1; break
            if lo <= stop:
                verdict = 0; break
        if verdict is None and last > idx and rows[last].date.toordinal() > cutoff - 1:
            verdict = 0
        if verdict is None:
            continue
        res['all'][0] += 1
        res['all'][1] += verdict
        ms = mis_stress_map.get(p.date, 0.0)
        bucket = 'misstress' if ms >= 0.5 else 'neutral'
        res[bucket][0] += 1
        res[bucket][1] += verdict
    return res


def _build_variants():
    out = [('baseline_v21', None)]
    # Conservative first
    for cd in (0.20, 0.25, 0.30, 0.50):
        out.append((f'MS_cd{int(cd*100):03d}',
                    make_ct_score('mis_stress_call_softener', call_dampen=cd)))
    return out


def _windows_22now():
    return [
        ('2022', date(2022, 1, 1), date(2022, 12, 31)),
        ('2023', date(2023, 1, 1), date(2023, 12, 31)),
        ('2024', date(2024, 1, 1), date(2024, 12, 31)),
        ('2025', date(2025, 1, 1), date(2025, 12, 31)),
        ('22-now', date(2022, 1, 1), date(2026, 4, 25)),
    ]


def main():
    from simulator import ScoreSimulator
    from database.models.technical import PriceHistory

    print(f"[mis-stress sweep] building simulator (full universe, 1597d) ...")
    sys.stdout.flush()
    t0 = time.time()
    sim = ScoreSimulator(symbols=None, lookback_days=1597)  # 22-now ~ 4.4y
    print(f"[mis-stress sweep] sim ready in {time.time()-t0:.0f}s, "
          f"mis_stress_map has {len(sim._mis_stress_map)} dates "
          f"(full={sum(1 for v in sim._mis_stress_map.values() if v >= 0.99)})")

    # Pre-load all price history once (heavy)
    print("[mis-stress sweep] loading price history ...")
    t0 = time.time()
    syms = list(sim._contexts.keys())
    sym_list = ','.join(f"'{s}'" for s in syms)
    Row = namedtuple('Row', 'date close high low')
    ph_by_sym = defaultdict(list)
    from database.trader_database import DB
    for sym, d, c, h, l in DB.execute_sql(f"""
        SELECT symbol, date, close, high, low FROM price_history
        WHERE symbol IN ({sym_list})
          AND date >= '2021-12-01' AND date < '2026-05-01'
        ORDER BY symbol, date
    """).fetchall():
        ph_by_sym[sym].append(Row(d, c, h, l))
    print(f"[mis-stress sweep] price history loaded in {time.time()-t0:.0f}s")

    variants = _build_variants()
    windows = _windows_22now()
    mis_stress_map = sim._mis_stress_map

    for label, fn in variants:
        print("\n" + "=" * 100)
        print(f"  VARIANT: {label}")
        if fn is not None:
            print(f"    design={fn._design}  params={fn._params}")
        print("=" * 100)
        sys.stdout.flush()

        scoring_mod.compute_overall_score = _ORIGINAL if fn is None else fn
        t0 = time.time()
        scores = sim.simulate()
        print(f"  [sim {time.time()-t0:.0f}s scored {len(scores)}]")
        sys.stdout.flush()

        for wlabel, ws, we in windows:
            peaks = _pick_peaks(scores, ws, we)
            calls75 = [p for p in peaks if p.overall >= 75]
            calls70_74 = [p for p in peaks if 70 <= p.overall < 75]
            puts25 = [p for p in peaks if p.overall <= 25]

            res = _walk_call75(calls75, ph_by_sym, mis_stress_map)
            n_all, w_all = res['all']
            n_ms, w_ms = res['misstress']
            n_nu, w_nu = res['neutral']
            wr_all = (w_all / n_all * 100) if n_all else 0
            wr_ms = (w_ms / n_ms * 100) if n_ms else 0
            wr_nu = (w_nu / n_nu * 100) if n_nu else 0

            # also count total puts and 70-74 calls
            print(f"  [{wlabel:>6}]  CALL75+ N={n_all:5d} WR={wr_all:5.1f}% | "
                  f"ms N={n_ms:4d} WR={wr_ms:5.1f}% | "
                  f"non-ms N={n_nu:4d} WR={wr_nu:5.1f}% | "
                  f"call70-74={len(calls70_74):4d}  put<=25={len(puts25):5d}")
            sys.stdout.flush()


if __name__ == '__main__':
    main()
