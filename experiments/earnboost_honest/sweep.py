"""EARN_BOOST parameter sweep on the honest holdout-locked ledger (CALLS).

The boost only RE-BUCKETS near-earnings signals (their fixed win outcome does not
change); so a config's value = the WR15 quality of the tradable tiers it produces.
Evaluated analytically against the fixed per-signal outcomes.

Honest lift table is computed in-script from the ledger (generic WR15 lift, the
form the production mechanism consumes), holdout-locked by construction.

For each config we report the tradable-tier pools (75+/80+/85+ by POST-boost
score): N and WR15 option-aligned (the tradable barrier), plus the boundary
signals the boost ADMITS (pre_boost 70-74 -> boosted>=75) and their WR15o.

Caveat: SCW dampener runs AFTER the boost in production, a small sub-boundary
effect on 70-72; the winning config is re-confirmed via the real score path.
"""
from __future__ import annotations
import io, sys, os, math, json
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import polars as pl

PQ = ROOT / '.cache' / 'earnboost_honest' / 'call_ledger_v69_holdout.parquet'
V34_TABLE = ROOT / 'experiments' / 'v34_calibration' / 'lift_table_v34.json'
MIN_N = 10
BUCKETS = ['95+', '90-94', '85-89', '80-84', '75-79', '70-74']


def bucket(pb):
    if pb >= 95: return '95+'
    if pb >= 90: return '90-94'
    if pb >= 85: return '85-89'
    if pb >= 80: return '80-84'
    if pb >= 75: return '75-79'
    return '70-74'


def ern_cohort(d, window):
    if d is None or d < 0 or d > window: return None
    if d <= 1: return 'pre1'
    if d <= 3: return 'pre3'
    if d <= 7: return 'pre7'
    return None


def build_honest_table(df, col='w15g'):
    """{(cohort,bucket)->lift_pp} near vs same-bucket non-earnings baseline (generic)."""
    base = df.filter(pl.col('cohort') == 'none')
    base_wr = {}
    for b in BUCKETS:
        s = base.filter((pl.col('pb_bucket') == b) & pl.col(col).is_not_null())
        base_wr[b] = (100.0 * s[col].sum() / len(s)) if len(s) else None
    tbl = {}
    for c in ['pre1', 'pre3', 'pre7']:
        for b in BUCKETS:
            s = df.filter((pl.col('cohort') == c) & (pl.col('pb_bucket') == b) & pl.col(col).is_not_null())
            n = len(s)
            if n < MIN_N or base_wr[b] is None:
                tbl[(c, b)] = (0.0, n); continue
            lift = 100.0 * s[col].sum() / n - base_wr[b]
            tbl[(c, b)] = (lift, n)
    return tbl


def load_v34_table():
    if not V34_TABLE.exists(): return {}
    raw = json.load(open(V34_TABLE))
    out = {}
    for k, v in raw.items():
        side, cohort, b = k.split('|')
        if side == 'low':
            out[(cohort, b)] = (v[0], v[1])
    return out


def strength(tbl, cohort, b, norm):
    lift, n = tbl.get((cohort, b), (0.0, 0))
    if n < MIN_N or lift <= 0: return 0.0
    return min(1.0, math.log(1 + lift) / math.log(1 + norm))


def boosted_score(pb, days, cohort_field, tbl, MAX, norm, window):
    c = ern_cohort(days, window)
    if c is None or cohort_field == 'none':
        return pb
    b = bucket(pb)
    st = strength(tbl, c, b, norm)
    if st <= 0: return pb
    prox = math.log(window + 1 - days) / math.log(window + 1)
    coeff = MAX * prox * st
    return int(max(0, min(100, round(50 + (pb - 50) * (1 + coeff)))))


def tier_stats(rows, thr):
    """rows: list of (boosted, w15o, w15g). returns (N, WR15o, WR15g) for boosted>=thr."""
    sel = [(o, wg) for (bs, wo, wg) in rows if bs >= thr for o in [wo] ]
    # collect option + generic separately
    o = [r[1] for r in rows if r[0] >= thr and r[1] is not None]
    g = [r[2] for r in rows if r[0] >= thr and r[2] is not None]
    wro = (100.0 * sum(o) / len(o)) if o else None
    wrg = (100.0 * sum(g) / len(g)) if g else None
    return (len(o), wro, wrg)


def main():
    if not PQ.exists():
        print(f"missing ledger {PQ} — run build_ledger.py first"); return
    df = pl.read_parquet(PQ).with_columns(
        pl.col('pre_boost').map_elements(bucket, return_dtype=pl.Utf8).alias('pb_bucket'))
    honest = build_honest_table(df, 'w15g')
    honest_opt = build_honest_table(df, 'w15o')
    v34 = load_v34_table()

    print(f"[ledger] {len(df):,} signals | near={len(df.filter(pl.col('cohort')!='none')):,}")
    print("\nHonest generic-WR15 lift cells (cohort,bucket -> lift_pp, N), positive only:")
    for k in sorted(honest):
        l, n = honest[k]
        if l > 0 and n >= MIN_N:
            print(f"   {k[0]:>4} {k[1]:<6}: +{l:4.1f}pp  n={n}")
    print()

    base_rows = list(zip(df['pre_boost'].to_list(), df['days_to_ern'].to_list(),
                         df['cohort'].to_list(), df['w15o'].to_list(), df['w15g'].to_list()))

    def eval_cfg(name, tbl, MAX, norm, window):
        rows = []
        admitted = []  # (w15o) for pre_boost<75 boosted>=75
        for pb, days, cf, wo, wg in base_rows:
            bs = pb if (MAX == 0.0) else boosted_score(pb, days, cf, tbl, MAX, norm, window)
            rows.append((bs, wo, wg))
            if pb < 75 <= bs and wo is not None:
                admitted.append(wo)
        cells = {}
        for thr in (75, 80, 85):
            cells[thr] = tier_stats(rows, thr)
        adm_n = len(admitted)
        adm_wr = (100.0 * sum(admitted) / adm_n) if adm_n else None
        return cells, adm_n, adm_wr

    configs = [
        ('OFF',                 None,   0.00, 14, 5),
        ('CURRENT v34 (0.55/14/5)', v34, 0.55, 14, 5),
        ('honest gen (0.55/14/5)',  honest, 0.55, 14, 5),
        ('honest gen (0.35/14/5)',  honest, 0.35, 14, 5),
        ('honest gen (0.20/14/5)',  honest, 0.20, 14, 5),
        ('honest gen (0.55/22/5)',  honest, 0.55, 22, 5),
        ('honest gen (0.55/8/5)',   honest, 0.55, 8, 5),
        ('honest gen (0.55/14/3)',  honest, 0.55, 14, 3),
        ('honest gen (0.55/14/7)',  honest, 0.55, 14, 7),
        ('honest OPT (0.55/14/5)',  honest_opt, 0.55, 14, 5),
    ]

    print("=" * 110)
    print("EARN_BOOST CONFIG SWEEP — tradable-tier pools by POST-boost score (WR15o = option-aligned)")
    print("=" * 110)
    print(f"{'config':<26} | {'75+ N':>6} {'75+ WRo':>8} | {'80+ N':>6} {'80+ WRo':>8} | "
          f"{'85+ N':>6} {'85+ WRo':>8} | {'admit':>6} {'admWRo':>7}")
    print("-" * 110)
    for name, tbl, MAX, norm, win in configs:
        cells, adn, adwr = eval_cfg(name, tbl, MAX, norm, win)
        def c(thr):
            n, wo, wg = cells[thr]
            return f"{n:>6} {('%4.1f%%'%wo) if wo is not None else '   -':>8}"
        print(f"{name:<26} | {c(75)} | {c(80)} | {c(85)} | "
              f"{adn:>6} {('%4.1f%%'%adwr) if adwr is not None else '   -':>7}")
    print("-" * 110)
    print("Read: vs OFF, a boost EARNS its place only if it raises a tradable tier's WR15o at equal/greater N,")
    print("or admits boundary signals (admit) that win ABOVE the OFF 75+ baseline. Else it only inflates scores.")


if __name__ == '__main__':
    main()
