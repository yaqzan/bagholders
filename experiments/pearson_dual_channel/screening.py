"""Dual-channel Pearson screening.

Thesis under test: the score-stage dampeners (PCD/CWWD/PESS/SVD/etc.) DISPLACE
known-bad cohorts out of qualifying buckets. That mechanically degrades
Pearson(overall, win) — but the underlying directional signal stored as
weight_info['pre_regime'] should remain calibrated.

If the thesis is right: Pearson(pre_regime, win) >> Pearson(overall, win).
If the thesis is wrong: both correlations track each other, and the dampener
layer is corrupting signal, not just routing it.

Runs against the currently-active scoring version. Single screening pass —
not a multi-version sweep. If the gap is real on one version it's worth
wiring into assess; if not, the architectural argument doesn't hold.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

# Ensure project root on path when run as `python experiments/pearson_dual_channel/screening.py`
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from assess_scores import (  # noqa: E402
    extract_peaks,
    calculate_forward_returns,
    PERIOD_LABELS,
)
from database.models.core import AlgorithmVersion  # noqa: E402
from experiments._holdout import assert_no_holdout_leak  # noqa: E402


def _pre_regime_of(peak) -> float | None:
    if not peak.weight_info:
        return None
    try:
        wi = json.loads(peak.weight_info)
    except (ValueError, TypeError):
        return None
    pr = wi.get('pre_regime')
    return float(pr) if pr is not None else None


def _pearson(pairs: list[tuple[float, int]]) -> tuple[float | None, int]:
    if len(pairs) < 5:
        return None, len(pairs)
    xs, ys = zip(*pairs)
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, len(pairs)
    c = np.corrcoef(xs, ys)[0, 1]
    return (None if np.isnan(c) else float(c)), len(pairs)


def main(lookback_days: int = 1825) -> None:
    version = AlgorithmVersion.get_active_scores_version()
    print(f"[pearson_dual_channel] active version: id={version.id} "
          f"commit={version.git_commit} lookback={lookback_days}d")

    peaks = extract_peaks(lookback_days=lookback_days, version=version)
    print(f"  extracted {len(peaks)} peaks (pre-holdout filter)")

    # Holdout filter (Priority #11 lock)
    peak_dates = [p.date for p in peaks]
    if peak_dates:
        assert_no_holdout_leak(peak_dates, "pearson_dual_channel.screening")

    # Parse pre_regime per peak; track coverage
    peak_pre_regime: dict[tuple[int, object], float] = {}
    n_missing = 0
    for p in peaks:
        pr = _pre_regime_of(p)
        if pr is None:
            n_missing += 1
            continue
        peak_pre_regime[(p.symbol_id, p.date)] = pr
    print(f"  pre_regime coverage: {len(peak_pre_regime)}/{len(peaks)} "
          f"({100*len(peak_pre_regime)/max(1,len(peaks)):.1f}%) — {n_missing} missing")

    if not peak_pre_regime:
        print("  no peaks with pre_regime in weight_info — aborting")
        return

    # Forward returns via existing cache-aware path
    results = calculate_forward_returns(peaks)
    print(f"  forward outcomes: {len(results)} results")

    # Distribution check: how often does pre_regime != overall?
    deltas = []
    high_displaced_in = 0   # overall qualifies (≥70) but pre_regime doesn't
    high_displaced_out = 0  # pre_regime qualifies but overall doesn't
    low_displaced_in = 0
    low_displaced_out = 0
    for r in results:
        key = (r['symbol'], r['date'])
        pr = peak_pre_regime.get(key)
        if pr is None:
            continue
        ov = r['score']
        deltas.append(ov - pr)
        if ov >= 70 and pr < 70:
            high_displaced_out += 1  # post-dampener kept it; raw says no
        if ov < 70 and pr >= 70:
            high_displaced_in += 1
        if ov <= 30 and pr > 30:
            low_displaced_out += 1
        if ov > 30 and pr <= 30:
            low_displaced_in += 1
    if deltas:
        arr = np.array(deltas)
        print(f"  Δ(overall − pre_regime): mean={arr.mean():+.2f} "
              f"median={np.median(arr):+.2f} "
              f"p10={np.percentile(arr, 10):+.2f} p90={np.percentile(arr, 90):+.2f} "
              f"|Δ|>=5: {(np.abs(arr) >= 5).sum()}/{len(arr)} "
              f"({100*(np.abs(arr) >= 5).mean():.1f}%)")
        print(f"  bucket displacement (overall vs pre_regime):")
        print(f"    HIGH side (≥70): kept-by-overall {high_displaced_out} ; "
              f"kept-by-pre_regime {high_displaced_in}")
        print(f"    LOW  side (≤30): kept-by-overall {low_displaced_out} ; "
              f"kept-by-pre_regime {low_displaced_in}")

    # Compute dual Pearson per period.
    # Side classification follows current assess: by overall (so we measure
    # what the current assess sees, comparing the two score channels on the
    # exact same peak universe).
    print()
    print("Pearson by period (HIGH = ≥50 by overall; LOW = <50 by overall):")
    print(f"  {'period':<6} {'side':<5} {'N':>7} {'overall':>10} {'pre_regime':>12} "
          f"{'Δ':>8}")
    for label in PERIOD_LABELS:
        high_o, high_p = [], []
        low_o, low_p = [], []
        for r in results:
            sw = r.get('swing', {}).get(label)
            if sw is None or sw.get('result') is None:
                continue
            pr = peak_pre_regime.get((r['symbol'], r['date']))
            if pr is None:
                continue
            win = 1 if sw['result'] == 'win' else 0
            ov = r['score']
            if ov >= 50:
                high_o.append((ov, win))
                high_p.append((pr, win))
            else:
                low_o.append((50 - ov, win))
                low_p.append((50 - pr, win))

        for side, lst_o, lst_p in [('HIGH', high_o, high_p), ('LOW', low_o, low_p)]:
            c_o, n_o = _pearson(lst_o)
            c_p, n_p = _pearson(lst_p)
            n = n_o  # same peaks both lists
            o_str = f"{c_o:+.3f}" if c_o is not None else "  n/a"
            p_str = f"{c_p:+.3f}" if c_p is not None else "  n/a"
            if c_o is not None and c_p is not None:
                d = c_p - c_o
                d_str = f"{d:+.3f}"
            else:
                d_str = "  n/a"
            print(f"  {label:<6} {side:<5} {n:>7d} {o_str:>10} {p_str:>12} {d_str:>8}")
    print()
    print("Reading guide: positive Δ = pre_regime channel correlates more strongly")
    print("with outcomes than overall does → dampeners are displacement, not corruption.")
    print("Negative Δ = post-dampener score predicts BETTER than the directional core,")
    print("which would mean the dampeners are adding signal, not just routing it.")


if __name__ == "__main__":
    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 1825
    main(lookback)
