"""Adversarial refutation of the WMPD (Weekly-Momentum Put Dampener) candidate.

Candidate claim: in the 21-25 put tier, signals with w_mom<=-4 have
optTP15 = 32.24% (N=974, z=-4.46 vs 39.21% tier marginal) => EV-NEGATIVE
(break-even on option-TP = 36.4%).

Tests:
 1. Symbol concentration (jackknife: drop top-5 most-frequent symbols)
 2. Date/year concentration (exclude 2022, exclude 2022+2024)
 3. Is w_mom<=-4 just a proxy for overall (cluster at 21 vs 25)?
 4. Does it hold on opt7 and opt30 too (not just opt15)?
"""
import polars as pl
import math

BE = 0.364  # option-TP break-even
LEDGER = '.cache/honest_miss/ledger_puts_5y.parquet'

df = pl.read_parquet(LEDGER)

# the candidate cohort: 21-25 tier, w_mom non-null
tier = df.filter(
    (pl.col('overall') >= 21) & (pl.col('overall') <= 25) &
    (pl.col('w_mom').is_not_null())
)
coh = tier.filter(pl.col('w_mom') <= -4)
rest = tier.filter(pl.col('w_mom') > -4)


def wr(frame, col):
    return frame[col].mean()


def z_vs_marginal(coh_frame, full_frame, col):
    """z of cohort WR vs full-population marginal WR (one-sample binomial)."""
    n1 = coh_frame.height
    if n1 == 0:
        return float('nan')
    p1 = coh_frame[col].mean()
    p0 = full_frame[col].mean()
    se = math.sqrt(p0 * (1 - p0) / n1)
    return (p1 - p0) / se if se > 0 else 0.0


def z_two_prop(a, b, col):
    """two-proportion z (cohort a vs comparison b)."""
    n1, n2 = a.height, b.height
    if n1 == 0 or n2 == 0:
        return float('nan')
    p1, p2 = a[col].mean(), b[col].mean()
    p = (a[col].sum() + b[col].sum()) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se > 0 else 0.0


print("=" * 70)
print("BASELINE (21-25 tier, w_mom non-null)")
print("=" * 70)
print(f"tier N={tier.height}  tier optTP15={wr(tier,'opt15')*100:.2f}%")
print(f"cohort (w_mom<=-4)  N={coh.height}  optTP15={wr(coh,'opt15')*100:.2f}%")
print(f"rest   (w_mom>-4)   N={rest.height}  optTP15={wr(rest,'opt15')*100:.2f}%")
print(f"z cohort vs tier marginal: {z_vs_marginal(coh,tier,'opt15'):.3f}")
print(f"z cohort vs rest         : {z_two_prop(coh,rest,'opt15'):.3f}")
print(f"break-even = {BE*100:.1f}%  -> cohort is "
      f"{'EV-NEGATIVE' if wr(coh,'opt15')<BE else 'EV-POSITIVE'} "
      f"({(wr(coh,'opt15')-BE)*100:+.2f}pp vs BE)")

# ----------------------------------------------------------------------
# TEST 1 — Symbol concentration / jackknife
# ----------------------------------------------------------------------
print()
print("=" * 70)
print("TEST 1 — SYMBOL CONCENTRATION (jackknife drop top-5 frequent symbols)")
print("=" * 70)
freq = (coh.group_by('symbol')
        .agg(pl.len().alias('n'),
             pl.col('opt15').mean().alias('wr'),
             pl.col('opt15').sum().alias('wins'))
        .sort('n', descending=True))
print("Top-12 symbols in cohort by frequency:")
for r in freq.head(12).iter_rows(named=True):
    print(f"  {r['symbol']:<8} N={r['n']:>4}  optTP15={r['wr']*100:>6.2f}%  "
          f"wins={int(r['wins'])}  ({r['n']/coh.height*100:.1f}% of cohort)")

top5 = freq.head(5)['symbol'].to_list()
print(f"\nTop-5 symbols: {top5}")
top5_share = coh.filter(pl.col('symbol').is_in(top5)).height / coh.height
print(f"Top-5 share of cohort N: {top5_share*100:.1f}%")

coh_jk = coh.filter(~pl.col('symbol').is_in(top5))
# rebuild the tier-marginal excluding those same symbols, for a fair z
tier_jk = tier.filter(~pl.col('symbol').is_in(top5))
print(f"\nAfter dropping top-5 symbols:")
print(f"  cohort N={coh_jk.height}  optTP15={wr(coh_jk,'opt15')*100:.2f}%")
print(f"  tier   N={tier_jk.height}  optTP15={wr(tier_jk,'opt15')*100:.2f}%")
print(f"  z cohort_jk vs tier_jk marginal: {z_vs_marginal(coh_jk,tier_jk,'opt15'):.3f}")
print(f"  cohort_jk is "
      f"{'EV-NEGATIVE' if wr(coh_jk,'opt15')<BE else 'EV-POSITIVE'} "
      f"({(wr(coh_jk,'opt15')-BE)*100:+.2f}pp vs BE)")

# Also a leave-one-out per-symbol: how much does each symbol move the cohort WR?
print("\nLeave-one-out: cohort optTP15 with each top-5 symbol removed individually:")
for s in top5:
    sub = coh.filter(pl.col('symbol') != s)
    print(f"  drop {s:<8} -> N={sub.height}  optTP15={wr(sub,'opt15')*100:.2f}%")

# ----------------------------------------------------------------------
# TEST 2 — Date / year concentration
# ----------------------------------------------------------------------
print()
print("=" * 70)
print("TEST 2 — YEAR CONCENTRATION")
print("=" * 70)
cohY = coh.with_columns(pl.col('date').str.slice(0, 4).alias('yr'))
tierY = tier.with_columns(pl.col('date').str.slice(0, 4).alias('yr'))

yr_tbl = (cohY.group_by('yr')
          .agg(pl.len().alias('n'), pl.col('opt15').mean().alias('wr'))
          .sort('yr'))
tier_yr = {r['yr']: r['wr'] for r in
           tierY.group_by('yr').agg(pl.col('opt15').mean().alias('wr')).iter_rows(named=True)}
print("Per-year cohort optTP15 vs tier-marginal optTP15 (delta):")
for r in yr_tbl.iter_rows(named=True):
    tm = tier_yr.get(r['yr'], float('nan'))
    print(f"  {r['yr']}  cohort N={r['n']:>4}  optTP15={r['wr']*100:>6.2f}%  "
          f"tier={tm*100:>6.2f}%  delta={(r['wr']-tm)*100:+.2f}pp")

for excl in [['2022'], ['2022', '2024'], ['2022', '2023', '2024']]:
    c = cohY.filter(~pl.col('yr').is_in(excl))
    t = tierY.filter(~pl.col('yr').is_in(excl))
    lbl = "+".join(excl)
    print(f"\nExcluding {lbl}:")
    print(f"  cohort N={c.height}  optTP15={wr(c,'opt15')*100:.2f}%")
    print(f"  tier   N={t.height}  optTP15={wr(t,'opt15')*100:.2f}%")
    print(f"  z cohort vs tier marginal: {z_vs_marginal(c,t,'opt15'):.3f}")
    print(f"  cohort is {'EV-NEGATIVE' if wr(c,'opt15')<BE else 'EV-POSITIVE'} "
          f"({(wr(c,'opt15')-BE)*100:+.2f}pp vs BE)")

# ----------------------------------------------------------------------
# TEST 3 — Is w_mom<=-4 a proxy for overall (21 vs 25 clustering)?
# ----------------------------------------------------------------------
print()
print("=" * 70)
print("TEST 3 — IS w_mom<=-4 A PROXY FOR THE SCORE ITSELF?")
print("=" * 70)
print("overall distribution in cohort vs rest:")
for v in [21, 22, 23, 24, 25]:
    cn = coh.filter(pl.col('overall') == v).height
    rn = rest.filter(pl.col('overall') == v).height
    print(f"  overall={v}  cohort={cn:>4} ({cn/coh.height*100:>5.1f}%)   "
          f"rest={rn:>4} ({rn/rest.height*100:>5.1f}%)")
print(f"  cohort mean overall: {coh['overall'].mean():.2f}   "
      f"rest mean overall: {rest['overall'].mean():.2f}")

print("\nKEY TEST: within each fixed overall value, does w_mom<=-4 still underperform?")
print("(if it's a pure score proxy, the within-bucket delta vanishes)")
for v in [21, 22, 23, 24, 25]:
    cv = coh.filter(pl.col('overall') == v)
    rv = rest.filter(pl.col('overall') == v)
    if cv.height < 20 or rv.height < 20:
        print(f"  overall={v}  cohort N={cv.height} rest N={rv.height} "
              f"(too few - skip z)")
        continue
    print(f"  overall={v}  cohort optTP15={wr(cv,'opt15')*100:>6.2f}% (N={cv.height})  "
          f"rest={wr(rv,'opt15')*100:>6.2f}% (N={rv.height})  "
          f"delta={(wr(cv,'opt15')-wr(rv,'opt15'))*100:+.2f}pp  "
          f"z={z_two_prop(cv,rv,'opt15'):+.2f}")

# Pooled within-overall (stratified) comparison: combine 21..25 holding overall fixed
# by comparing cohort-vs-rest at each level then pooling the z via stouffer
zs = []
ws = []
for v in [21, 22, 23, 24, 25]:
    cv = coh.filter(pl.col('overall') == v)
    rv = rest.filter(pl.col('overall') == v)
    if cv.height >= 20 and rv.height >= 20:
        z = z_two_prop(cv, rv, 'opt15')
        zs.append(z)
        ws.append(math.sqrt(cv.height))
if zs:
    stouffer = sum(w * z for w, z in zip(ws, zs)) / math.sqrt(sum(w * w for w in ws))
    print(f"\n  Stouffer-combined within-overall z (cohort vs rest, score held fixed): {stouffer:.3f}")

# ----------------------------------------------------------------------
# TEST 4 — opt7 and opt30
# ----------------------------------------------------------------------
print()
print("=" * 70)
print("TEST 4 — DOES IT HOLD ON opt7 AND opt30?")
print("=" * 70)
# break-evens differ by horizon? candidate states BE on option-TP = 36.4%.
# We'll report cohort vs tier-marginal for each barrier; the EV sign vs 36.4
# is the relevant gate for opt15. For 7/30 we report relative underperformance.
for col in ['opt7', 'opt15', 'opt30']:
    cm = wr(coh, col)
    tm = wr(tier, col)
    rm = wr(rest, col)
    print(f"  {col}: cohort={cm*100:>6.2f}% (N={coh.height})  "
          f"tier-marginal={tm*100:>6.2f}%  rest={rm*100:>6.2f}%  "
          f"delta_vs_rest={(cm-rm)*100:+.2f}pp  z_vs_marg={z_vs_marginal(coh,tier,col):+.2f}")

# also generic barriers as a cross-check
print("\nGeneric barriers (cross-check, not the tradable target):")
for col in ['gen7', 'gen15', 'gen30']:
    cm = wr(coh, col)
    rm = wr(rest, col)
    print(f"  {col}: cohort={cm*100:>6.2f}%  rest={rm*100:>6.2f}%  "
          f"delta_vs_rest={(cm-rm)*100:+.2f}pp  z={z_two_prop(coh,rest,col):+.2f}")
