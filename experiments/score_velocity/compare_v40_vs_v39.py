"""v40 (SVD shipped) vs v39 (no SVD) baseline per-trade comparison.

Both parquets must already exist via build_features.py. Buckets the call peaks
each parquet exposes (overall >= 70) and reports option-aligned WR15 per tier
on identical date windows. The expected pattern after a clean v40 ship:

  - 75+ N drops a few % (SVD displaces the decel cohort below 75)
  - 75+ TP% lifts +0.3-0.5pp (the displaced cohort was the EV-negative tail)
  - 95+ / 90+ unchanged (high-conviction signals don't decel)

If 75+ TP% does NOT lift, SVD is mis-wired or the recalc didn't actually fire.
"""
from __future__ import annotations
import io, sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import date, timedelta
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
TIERS = [(95, '95+'), (90, '90+'), (85, '85+'), (80, '80+'), (75, '75+'), (70, '70+')]


def load(vid: int):
    """Load whichever parquet exists for this version (3650 preferred for breadth)."""
    cache = ROOT / '.cache' / 'score_velocity'
    for lb in (3650, 1825):
        p = cache / f'calls_v{vid}_{lb}.parquet'
        if p.exists():
            return pl.read_parquet(p), lb
    raise FileNotFoundError(f'no parquet for v{vid}')


def bucket(df, window_start: str):
    sub = df.filter(pl.col('date') >= window_start).filter(pl.col('opt_result_15').is_not_null())
    out = {}
    for thr, label in TIERS:
        cell = sub.filter(pl.col('overall') >= thr)
        n = len(cell)
        wins = int((cell['opt_result_15'] == 1).sum()) if n > 0 else 0
        out[label] = (n, (wins / n * 100) if n else None)
    return out


def main():
    df_v39, lb39 = load(39)
    df_v40, lb40 = load(40)
    print(f'v39 parquet: {len(df_v39):,} call peaks (lookback={lb39}d)  date range: [{df_v39["date"].min()}, {df_v39["date"].max()}]')
    print(f'v40 parquet: {len(df_v40):,} call peaks (lookback={lb40}d)  date range: [{df_v40["date"].min()}, {df_v40["date"].max()}]')

    today = date.today()
    windows = [
        ('1y', (today - timedelta(days=365)).isoformat()),
        ('3y', (today - timedelta(days=1095)).isoformat()),
        ('5y', (today - timedelta(days=1825)).isoformat()),
    ]
    print()
    for w_label, w_start in windows:
        b39 = bucket(df_v39, w_start)
        b40 = bucket(df_v40, w_start)
        print(f'═══ {w_label} (since {w_start}) ═══')
        print(f'  {"tier":>5}  {"v39 N":>6} {"v39 TP%":>8}    {"v40 N":>6} {"v40 TP%":>8}    {"ΔTP":>7}  {"ΔN%":>6}')
        for thr, label in TIERS:
            n39, tp39 = b39[label]
            n40, tp40 = b40[label]
            d_tp = (tp40 - tp39) if (tp39 is not None and tp40 is not None) else None
            d_n_pct = ((n40 - n39) / n39 * 100) if n39 else 0
            tp39_s = f'{tp39:.2f}%' if tp39 is not None else '   --  '
            tp40_s = f'{tp40:.2f}%' if tp40 is not None else '   --  '
            d_tp_s = f'{d_tp:+.2f}pp' if d_tp is not None else '  --  '
            print(f'  {label:>5}  {n39:>6} {tp39_s:>8}    {n40:>6} {tp40_s:>8}    {d_tp_s:>7}  {d_n_pct:>+5.1f}%')
        print()


if __name__ == '__main__':
    main()
