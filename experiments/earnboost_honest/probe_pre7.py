"""Pre7-only / both-barrier-gated EARN_BOOST shape probe (CALLS), holdout-locked.

Motivation: the honest edge is in pre7 (4-7 cal days out); pre1 is dead/negative.
But the production proximity `log(W+1-d)/log(W+1)` peaks at d=1 (pre1) and is 0 at
the window edge — backwards. The cohort table already zeroes pre1 (lift<=0 ->
strength 0), so proximity only modulates the pre3/pre7 cells that DO boost, and it
throttles exactly the pre7 cells carrying the edge.

Probe: vary proximity shape x WINDOW x table-gate x MAX. Evaluate on the TRADABLE
(opt) barrier: affected-cohort WR15o, boundary-admit WR15o/N, and tier pools.
Question: can a tighter pre7 shape get a materially better tradable edge than the
plain table swap? (Outcomes are fixed per signal; the boost only re-buckets.)
"""
from __future__ import annotations
import io, sys, os, math
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import polars as pl

PQ = ROOT / '.cache' / 'earnboost_honest' / 'call_ledger_v69_holdout.parquet'
BUCKETS = ['95+', '90-94', '85-89', '80-84', '75-79', '70-74']
COHORTS = ['pre1', 'pre3', 'pre7']
MIN_N = 10
NORM = 14.0


def bucket(pb):
    if pb >= 95: return '95+'
    if pb >= 90: return '90-94'
    if pb >= 85: return '85-89'
    if pb >= 80: return '80-84'
    if pb >= 75: return '75-79'
    return '70-74'


def ern_cohort(d, window):
    if d is None or d < 1 or d > window: return None
    if d <= 1: return 'pre1'
    if d <= 3: return 'pre3'
    return 'pre7'


def build_lifts(df):
    """returns {(cohort,bucket)->(gen_lift,opt_lift,n)}."""
    base = df.filter(pl.col('cohort') == 'none')
    bg, bo = {}, {}
    for b in BUCKETS:
        s = base.filter((pl.col('pb_bucket') == b))
        sg = s.filter(pl.col('w15g').is_not_null()); so = s.filter(pl.col('w15o').is_not_null())
        bg[b] = (100.0 * sg['w15g'].sum() / len(sg)) if len(sg) else None
        bo[b] = (100.0 * so['w15o'].sum() / len(so)) if len(so) else None
    out = {}
    for c in COHORTS:
        for b in BUCKETS:
            s = df.filter((pl.col('cohort') == c) & (pl.col('pb_bucket') == b))
            sg = s.filter(pl.col('w15g').is_not_null()); so = s.filter(pl.col('w15o').is_not_null())
            n = len(sg)
            if n == 0 or bg[b] is None: continue
            gl = 100.0 * sg['w15g'].sum() / n - bg[b]
            ol = (100.0 * so['w15o'].sum() / len(so) - bo[b]) if (len(so) and bo[b] is not None) else None
            out[(c, b)] = (gl, ol, n)
    return out


def proximity(shape, d, window):
    """all shapes in [0,1] over d in [1,window]."""
    if shape == 'current':
        return math.log(window + 1 - d) / math.log(window + 1)
    if shape == 'flat':
        return 1.0
    if shape == 'ramp_up':            # 0 at d=1 -> 1 at d=window (weight the run-up)
        return (d - 1) / max(1, (window - 1))
    if shape == 'pre7peak':           # triangular peak at d=5.5 (center of 4-7)
        peak = 5.5
        return max(0.0, 1.0 - abs(d - peak) / 4.5)
    return 1.0


def strength(lifts, c, b, basis):
    g, o, n = lifts.get((c, b), (0.0, None, 0))
    if n < MIN_N: return 0.0
    if basis == 'both_pos':           # require BOTH barriers positive; magnitude from opt
        if g <= 0 or o is None or o <= 0: return 0.0
        lift = o
    else:                              # 'generic'
        if g <= 0: return 0.0
        lift = g
    return min(1.0, math.log(1 + lift) / math.log(1 + NORM))


def main():
    df = pl.read_parquet(PQ).with_columns(
        pl.col('pre_boost').map_elements(bucket, return_dtype=pl.Utf8).alias('pb_bucket'))
    lifts = build_lifts(df)
    rows = list(zip(df['pre_boost'].to_list(), df['days_to_ern'].to_list(),
                    df['cohort'].to_list(), df['w15o'].to_list(), df['w15g'].to_list()))

    base75o = [wo for pb, d, cf, wo, wg in rows if pb >= 75 and wo is not None]
    OFF75 = 100.0 * sum(base75o) / len(base75o)
    print(f"[ledger] {len(rows):,} signals  | OFF 75+ WR15o baseline = {OFF75:.1f}%\n")

    def evaluate(shape, window, basis, MAX):
        post = []; admit = []; affected = []
        for pb, d, cf, wo, wg in rows:
            bs = pb
            if cf != 'none' and MAX > 0:
                c = ern_cohort(d, window)
                if c is not None:
                    st = strength(lifts, c, bucket(pb), basis)
                    if st > 0:
                        coeff = MAX * proximity(shape, d, window) * st
                        nb = int(max(0, min(100, round(50 + (pb - 50) * (1 + coeff)))))
                        if nb != pb:
                            affected.append(wo)
                        bs = nb
            post.append((bs, wo))
            if pb < 75 <= bs and wo is not None:
                admit.append(wo)
        def wr(lst):
            lst = [v for v in lst if v is not None]
            return (100.0 * sum(lst) / len(lst), len(lst)) if lst else (None, 0)
        o75 = [wo for bs, wo in post if bs >= 75 and wo is not None]
        o80 = [wo for bs, wo in post if bs >= 80 and wo is not None]
        return {
            'aff_wr': wr(affected), 'admit_wr': wr(admit),
            't75': (100.0*sum(o75)/len(o75), len(o75)) if o75 else (None,0),
            't80': (100.0*sum(o80)/len(o80), len(o80)) if o80 else (None,0),
        }

    configs = [
        ('OFF',                       'flat',    5, 'generic',  0.0),
        ('table-swap (cur prox W5)',  'current', 5, 'generic',  0.55),
        ('table-swap (cur prox W7)',  'current', 7, 'generic',  0.55),
        ('pre7 flat W7 both',         'flat',    7, 'both_pos', 0.55),
        ('pre7 flat W7 both MAX.35',  'flat',    7, 'both_pos', 0.35),
        ('pre7 flat W7 both MAX.75',  'flat',    7, 'both_pos', 0.75),
        ('pre7 rampup W7 both',       'ramp_up', 7, 'both_pos', 0.55),
        ('pre7 peak W7 both',         'pre7peak',7, 'both_pos', 0.55),
        ('pre7 flat W7 generic',      'flat',    7, 'generic',  0.55),
        ('pre7 flat W5 both',         'flat',    5, 'both_pos', 0.55),
    ]

    print("=" * 112)
    print("PRE7 SHAPE PROBE — tradable (opt) barrier.  aff=signals the boost moved, admit=70-74->75+ promoted")
    print("=" * 112)
    print(f"{'config':<28} | {'aff N':>5} {'aff WRo':>7} | {'admit N':>7} {'admit WRo':>9} | "
          f"{'75+ N':>6} {'75+ WRo':>7} | {'80+ N':>6} {'80+ WRo':>7}")
    print("-" * 112)
    for name, shape, win, basis, MAX in configs:
        r = evaluate(shape, win, basis, MAX)
        def fmt(t):
            wr, n = t; return f"{n:>6} {('%5.1f%%'%wr) if wr is not None else '   -':>7}"
        aff_wr, aff_n = r['aff_wr']; ad_wr, ad_n = r['admit_wr']
        print(f"{name:<28} | {aff_n:>5} {('%5.1f%%'%aff_wr) if aff_wr is not None else '  -':>7} | "
              f"{ad_n:>7} {('%5.1f%%'%ad_wr) if ad_wr is not None else '   -':>9} | {fmt(r['t75'])} | {fmt(r['t80'])}")
    print("-" * 112)
    print(f"OFF 75+ WR15o = {OFF75:.1f}%.  'admit WRo' above this = boost promotes net-winners into the tradable pool.")
    print("Materially-better tradable edge = higher admit/aff WRo at meaningful N AND 75+/80+ WRo nudged up vs table-swap.")


if __name__ == '__main__':
    main()
