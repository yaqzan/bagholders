"""Honest EARN_BOOST lift table + verdict (CALLS), from the holdout-locked ledger.

Question: after look-ahead removal (v69), do near-earnings signals at a given
pre_boost score still win MORE than non-earnings signals at the same score?
That residual lift is the ONLY thing that justifies EARN_BOOST.

Outputs:
  1. Lift table: (cohort pre1/pre3/pre7 x pre_boost bucket) WR15 gen+opt vs the
     same-bucket non-earnings baseline, with two-proportion z and N.  (pooled 10y)
  2. Multi-window (1y/3y/5y/10y) WR15 lift on the pooled near-earnings 75+/80+
     cohorts (Stage-1 W3 sign-consistency).
  3. Boundary-admission test: near pre_boost 70-74 (what the boost promotes to
     75+) WR15 vs the 75+ non-earnings baseline.
  4. Verdict scaffold (W1 cohort z>=+3 on a tradable barrier?).
"""
from __future__ import annotations
import io, sys, os, math
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import polars as pl

PQ = ROOT / '.cache' / 'earnboost_honest' / 'call_ledger_v69_holdout.parquet'
CUTOFF = date(2026, 5, 15)
BUCKETS = ['95+', '90-94', '85-89', '80-84', '75-79', '70-74']
COHORTS = ['pre1', 'pre3', 'pre7']


def bucket(pb):
    if pb >= 95: return '95+'
    if pb >= 90: return '90-94'
    if pb >= 85: return '85-89'
    if pb >= 80: return '80-84'
    if pb >= 75: return '75-79'
    return '70-74'


def z2(x1, n1, x2, n2):
    """two-proportion z, group1 vs group2."""
    if n1 == 0 or n2 == 0: return None
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    den = p * (1 - p) * (1 / n1 + 1 / n2)
    if den <= 0: return None
    return (p1 - p2) / math.sqrt(den)


def wr(df, col):
    s = df.filter(pl.col(col).is_not_null())
    n = len(s)
    if n == 0: return (None, 0, 0)
    x = int(s[col].sum())
    return (100.0 * x / n, x, n)


def main():
    if not PQ.exists():
        print(f"missing ledger {PQ} — run build_ledger.py first"); return
    df = pl.read_parquet(PQ)
    df = df.with_columns(pl.col('pre_boost').map_elements(bucket, return_dtype=pl.Utf8).alias('pb_bucket'))
    print(f"[ledger] {len(df):,} call signals (pre_boost>=70, <= {CUTOFF})")
    print(f"  near (cohort!=none): {len(df.filter(pl.col('cohort')!='none')):,} | "
          f"baseline: {len(df.filter(pl.col('cohort')=='none')):,}")
    print(f"  ern_boost active (stored): {len(df.filter(pl.col('ern_boost')>0)):,}\n")

    # ---- 1. lift table (pooled 10y) ----
    print("=" * 118)
    print("HONEST EARN_BOOST LIFT TABLE (v69, holdout-locked).  near-earnings vs same-bucket non-earnings baseline.")
    print("WR15g = generic 2s/5s ; WR15o = option-aligned 1.274s/1.092s.  z = two-proportion (near vs baseline).")
    print("=" * 118)
    base = df.filter(pl.col('cohort') == 'none')
    for col, lbl in [('w15g', 'WR15 GENERIC'), ('w15o', 'WR15 OPTION-ALIGNED (tradable)')]:
        print(f"\n--- {lbl} ---")
        print(f"{'bucket':<8} | {'baseline':>16} | " + " | ".join(f"{c+' (lift / z / N)':>22}" for c in COHORTS))
        for b in BUCKETS:
            bsub = base.filter(pl.col('pb_bucket') == b)
            bwr, bx, bn = wr(bsub, col)
            cells = [f"{(f'{bwr:4.1f}% n={bn:>4}' if bwr is not None else 'n/a'):>16}"]
            for c in COHORTS:
                csub = df.filter((pl.col('cohort') == c) & (pl.col('pb_bucket') == b))
                cwr, cx, cn = wr(csub, col)
                if cwr is None or bwr is None or cn < 5:
                    cells.append(f"{('-' if cn==0 else f'n={cn}'):>22}")
                    continue
                lift = cwr - bwr
                z = z2(cx, cn, bx, bn)
                zs = f"{z:+.1f}" if z is not None else "  -"
                cells.append(f"{lift:+5.1f} / {zs:>5} / {cn:>4}")
            print(f"{b:<8} | " + " | ".join(cells))

    # pooled near vs baseline at cumulative tiers
    print("\n--- POOLED near-earnings vs non-earnings (cumulative pre_boost tiers) ---")
    print(f"{'tier':<6} | {'NEAR WR15g/o (N)':>26} | {'BASE WR15g/o (N)':>26} | {'lift g':>7} {'lift o':>7} | {'z g':>6} {'z o':>6}")
    for thr, tlbl in [(70, '70+'), (75, '75+'), (80, '80+'), (85, '85+')]:
        nr = df.filter((pl.col('cohort') != 'none') & (pl.col('pre_boost') >= thr))
        br = df.filter((pl.col('cohort') == 'none') & (pl.col('pre_boost') >= thr))
        ng, ngx, ngn = wr(nr, 'w15g'); no_, nox, non = wr(nr, 'w15o')
        bg, bgx, bgn = wr(br, 'w15g'); bo, box, bon = wr(br, 'w15o')
        lg = (ng - bg) if (ng is not None and bg is not None) else None
        lo = (no_ - bo) if (no_ is not None and bo is not None) else None
        zg = z2(ngx, ngn, bgx, bgn); zo = z2(nox, non, box, bon)
        print(f"{tlbl:<6} | {f'{ng:4.1f}/{no_:4.1f} (n={ngn})':>26} | {f'{bg:4.1f}/{bo:4.1f} (n={bgn})':>26} | "
              f"{(f'{lg:+5.1f}' if lg is not None else '-'):>7} {(f'{lo:+5.1f}' if lo is not None else '-'):>7} | "
              f"{(f'{zg:+.1f}' if zg is not None else '-'):>6} {(f'{zo:+.1f}' if zo is not None else '-'):>6}")

    # ---- 2. multi-window W3 sign-consistency (pooled near 75+ / 80+) ----
    print("\n" + "=" * 90)
    print("MULTI-WINDOW WR15 LIFT (pooled near vs baseline) — Stage-1 W3 sign consistency")
    print("=" * 90)
    print(f"{'window':<6} | {'75+ lift g':>11} {'75+ lift o':>11} | {'80+ lift g':>11} {'80+ lift o':>11}")
    for wlabel, days in [('1y', 365), ('3y', 1095), ('5y', 1825), ('10y', 3650)]:
        start = (CUTOFF - timedelta(days=days)).isoformat()
        w = df.filter(pl.col('date') >= start)
        cells = []
        for thr in (75, 80):
            nr = w.filter((pl.col('cohort') != 'none') & (pl.col('pre_boost') >= thr))
            br = w.filter((pl.col('cohort') == 'none') & (pl.col('pre_boost') >= thr))
            ng = wr(nr, 'w15g')[0]; no_ = wr(nr, 'w15o')[0]
            bg = wr(br, 'w15g')[0]; bo = wr(br, 'w15o')[0]
            lg = (ng - bg) if (ng is not None and bg is not None) else None
            lo = (no_ - bo) if (no_ is not None and bo is not None) else None
            cells += [f"{lg:+6.1f}" if lg is not None else '   -', f"{lo:+6.1f}" if lo is not None else '   -']
        print(f"{wlabel:<6} | {cells[0]:>11} {cells[1]:>11} | {cells[2]:>11} {cells[3]:>11}")

    # ---- 3. boundary-admission test ----
    print("\n" + "=" * 90)
    print("BOUNDARY ADMISSION: near pre_boost 70-74 (what the boost promotes into 75+)")
    print("vs the 75+ NON-earnings baseline it joins.  If below baseline -> boost DILUTES the tradable tier.")
    print("=" * 90)
    adm = df.filter((pl.col('cohort') != 'none') & (pl.col('pre_boost') >= 70) & (pl.col('pre_boost') < 75))
    b75 = df.filter((pl.col('cohort') == 'none') & (pl.col('pre_boost') >= 75))
    for col, lbl in [('w15g', 'generic'), ('w15o', 'option-aligned')]:
        aw, ax, an = wr(adm, col); bw, bx, bn = wr(b75, col)
        z = z2(ax, an, bx, bn) if (an and bn) else None
        print(f"  {lbl:<16}: admitted 70-74 near WR15={aw:4.1f}% (n={an})  vs  75+ baseline WR15={bw:4.1f}% (n={bn})  "
              f"delta={aw-bw:+.1f}pp  z={z:+.1f}" if (aw is not None and bw is not None) else f"  {lbl}: n/a")

    print("\nVERDICT INPUTS: W1 needs cohort z>=+3 (tradable/opt barrier) on the boosted cohort.")
    print("If pooled near-75+ opt z<+3 and boundary-admission delta_o<0 -> EARN_BOOST does not earn its place honestly.")


if __name__ == '__main__':
    main()
