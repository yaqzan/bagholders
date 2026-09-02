"""Confound check: is daily_cash_demand's predictive power just regime?

If high-call-demand days predict high call TP rate ONLY because those days are
bull tape (calls fire abundantly + perform better), then the signal is just
regime, not "queue depth" alpha. To isolate:

  1. Compute SAME-SIDE demand and CROSS-SIDE demand per date.
     - For call peaks: same_side_demand = daily call alloc; cross_side = daily put alloc
     - For put peaks:  same_side_demand = daily put alloc;  cross_side = daily call alloc

  2. Cohort split by EACH separately.

If only same-side demand has predictive power → regime story (high call demand
day = bull tape = better call quality).

If cross-side demand ALSO predicts (or even inverts), → demand carries true
queue-depth information independent of side.

If cross-side demand has WEAKER effect than same-side → mixed: there's a real
queue-depth signal but most variance is regime.

Also: signal-COUNT (vs alloc-weighted demand) check — strip alloc weighting
to see whether the alpha is in raw N or in alloc-weighted mass.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl
from database.bulk_cache import cache_path


def quintile_split(s: pl.Series) -> pl.Series:
    """Bucket values into 5 quintiles 0..4."""
    bounds = [s.quantile(q) for q in [0.20, 0.40, 0.60, 0.80]]
    return pl.Series([
        next((i for i, b in enumerate(bounds) if v <= b), 4)
        for v in s.to_list()
    ], dtype=pl.Int32)


def _z_diff(p1, n1, p2, n2):
    if n1 == 0 or n2 == 0: return float('nan')
    p_p = (p1*n1 + p2*n2) / (n1 + n2)
    se = (p_p*(1-p_p)*(1/n1 + 1/n2))**0.5
    return (p2-p1)/se if se > 0 else float('nan')


def main():
    df = pl.read_parquet(cache_path('dynamic_tpsl_v46_5y'))
    df = df.filter(pl.col('result').is_not_null())
    print(f"Resolved peaks: {len(df):,}")

    # Per-date demand by side
    by_side = df.group_by(['date', 'side']).agg(
        side_demand=pl.col('tier_alloc').sum(),
        side_count=pl.len(),
    )
    call_dem = by_side.filter(pl.col('side') == 'low').select(
        date=pl.col('date'),
        call_demand=pl.col('side_demand'),
        call_count=pl.col('side_count'),
    )
    put_dem = by_side.filter(pl.col('side') == 'high').select(
        date=pl.col('date'),
        put_demand=pl.col('side_demand'),
        put_count=pl.col('side_count'),
    )
    daily = call_dem.join(put_dem, on='date', how='full', coalesce=True).fill_null(0.0)
    print(f"Distinct days: {len(daily):,}")
    print(daily.select(['call_demand', 'put_demand']).describe())

    # Join back per signal
    df = df.join(daily, on='date', how='left')

    for side, side_label, same_col, cross_col in [
        ('low',  'CALL', 'call_demand', 'put_demand'),
        ('high', 'PUT',  'put_demand',  'call_demand'),
    ]:
        sd = df.filter(pl.col('side') == side)
        if len(sd) == 0: continue
        print(f"\n{'='*72}")
        print(f"{side_label} (N={len(sd):,})")
        print(f"{'='*72}")

        # SAME-side cohort
        same_q = quintile_split(sd[same_col])
        sd_s = sd.with_columns(qbin=same_q)
        q1 = sd_s.filter(pl.col('qbin') == 0)
        q5 = sd_s.filter(pl.col('qbin') == 4)
        p1 = float(q1['result'].mean()); p5 = float(q5['result'].mean())
        z = _z_diff(p1, len(q1), p5, len(q5))
        print(f"  SAME-side ({same_col}):")
        print(f"    Q1 N={len(q1):>4} TP={p1*100:>5.2f}%  Q5 N={len(q5):>4} TP={p5*100:>5.2f}%  ΔTP={(p5-p1)*100:+.2f}pp  z={z:+.2f}")

        # CROSS-side cohort
        cross_q = quintile_split(sd[cross_col])
        sd_c = sd.with_columns(qbin=cross_q)
        q1 = sd_c.filter(pl.col('qbin') == 0)
        q5 = sd_c.filter(pl.col('qbin') == 4)
        p1 = float(q1['result'].mean()); p5 = float(q5['result'].mean())
        z = _z_diff(p1, len(q1), p5, len(q5))
        print(f"  CROSS-side ({cross_col}):")
        print(f"    Q1 N={len(q1):>4} TP={p1*100:>5.2f}%  Q5 N={len(q5):>4} TP={p5*100:>5.2f}%  ΔTP={(p5-p1)*100:+.2f}pp  z={z:+.2f}")

        # COUNT-based same-side (no alloc weight) — does raw signal count carry the alpha?
        count_col = same_col.replace('demand', 'count')
        cnt_q = quintile_split(sd[count_col])
        sd_n = sd.with_columns(qbin=cnt_q)
        q1 = sd_n.filter(pl.col('qbin') == 0)
        q5 = sd_n.filter(pl.col('qbin') == 4)
        p1 = float(q1['result'].mean()); p5 = float(q5['result'].mean())
        z = _z_diff(p1, len(q1), p5, len(q5))
        print(f"  COUNT-only same-side ({count_col}):")
        print(f"    Q1 N={len(q1):>4} TP={p1*100:>5.2f}%  Q5 N={len(q5):>4} TP={p5*100:>5.2f}%  ΔTP={(p5-p1)*100:+.2f}pp  z={z:+.2f}")

if __name__ == '__main__':
    main()
