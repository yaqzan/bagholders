"""Inline comprehensive evaluation of per-stock score normalization (workflow fallback).
Covers: scheme evals (z/pctile/xs) | decomposition (the crux) | does-ps_z-add-info-beyond-overall
(the killer question) | concentration/PIT/option-vs-gen skeptic checks | bridge supply.
All on the OPTION barrier, point-in-time ledger, vs known absolute base rates.
"""
import os, sys, math
import polars as pl
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_norm import load, BASE75, zprop, wr

# known ABSOLUTE base rates by tier (from w1_preflight on the real 60-99 ledger):
ABS_BASE = {  # opt15, apex15
    '75+': (0.475, 0.700), '70-74': (0.468, 0.681), '80-84': (0.450, 0.688), '85+': (0.480, 0.735),
}
df = load()
# per-stock max overall (to find 'pure bridge' stocks that can NEVER hit 75)
stmax = df.group_by('symbol').agg(pl.col('overall').max().alias('mx'))
mute_syms = set(stmax.filter(pl.col('mx') < 75)['symbol'].to_list())
df = df.with_columns(pl.col('symbol').is_in(list(mute_syms)).alias('is_mute'))
print(f"ledger N={df.height}  abs>=75={int((df['overall']>=75).sum())}  pure-bridge(mute, max<75) stocks={len(mute_syms)}")
print(f"absolute base: 75+ opt15={ABS_BASE['75+'][0]*100:.1f}% apex15={ABS_BASE['75+'][1]*100:.1f}%  |  70-74 opt15={ABS_BASE['70-74'][0]*100:.1f}%")

def cohort(mask, label, base=('75+',)):
    sub = df.filter(mask)
    o = wr(sub['opt15']); a = wr(sub['apex15']); g = wr(sub['gen15'])
    if o[1] < 5:
        print(f"  {label:42} N={sub.height:6} (too few resolved)"); return
    b75o, b75a = ABS_BASE['75+']
    _, zo = zprop(o[0], o[1], round(b75o*o[1]), o[1])
    _, za = zprop(a[0], a[1], round(b75a*a[1]), a[1])
    # also vs 70-74 abs base (the 'is it just lowering the threshold' bar)
    b70o = ABS_BASE['70-74'][0]; _, zo70 = zprop(o[0], o[1], round(b70o*o[1]), o[1])
    tag = '  ALPHA' if (zo>=3 and za>=3) else ('  DILUTIVE' if (zo<=-3 or za<=-3) else '  ~flat')
    print(f"  {label:42} N={o[1]:6}  opt15={o[2]*100:4.1f}%(z{zo:+.1f}/vs70:{zo70:+.1f}) apex15={a[2]*100:4.1f}%(z{za:+.1f}) gen15={g[2]*100:4.1f}%{tag}")

print("\n########## A. SCHEME EVALS — NEW signals (scheme fires, absolute<75) vs 75+ base ##########")
print("z-score (ps_z>=th & overall<75):")
for th in (1.0, 1.5, 2.0, 2.5, 3.0):
    cohort((pl.col('ps_z')>=th) & (pl.col('overall')<75), f"ps_z>={th} & abs<75")
print("percentile (ps_pctile>=th & overall<75):")
for th in (0.90, 0.95, 0.98, 0.99):
    cohort((pl.col('ps_pctile')>=th) & (pl.col('overall')<75), f"ps_pctile>={th} & abs<75")
print("cross-sectional (xs_pctile>=th & overall<75):")
for th in (0.90, 0.95, 0.98, 0.99):
    cohort((pl.col('xs_pctile')>=th) & (pl.col('overall')<75), f"xs_pctile>={th} & abs<75")

print("\n########## B. DECOMPOSITION (crux): ps_z>=1.5 & abs<75, split by... ##########")
NEW = (pl.col('ps_z')>=1.5) & (pl.col('overall')<75)
print("by SIGNAL overall band:")
for lo,hi in [(50,59),(60,64),(65,69),(70,72),(73,74)]:
    cohort(NEW & (pl.col('overall')>=lo) & (pl.col('overall')<=hi), f"overall {lo}-{hi}")
print("pure-BRIDGE (mute stocks, max overall<75) only — the user's exact case:")
cohort(NEW & pl.col('is_mute'), "mute-stock new signals")
cohort(NEW & ~pl.col('is_mute'), "non-mute new signals")
print("by realized-vol quintile:")
vq = df.filter(NEW)['vol_pct']
edges=[vq.quantile(q) for q in (0,.2,.4,.6,.8,1.0)]
for i in range(5):
    cohort(NEW & (pl.col('vol_pct')>=edges[i]) & (pl.col('vol_pct')<=edges[i+1]), f"vol {edges[i]:.1f}-{edges[i+1]:.1f}%/d")
print("by YEAR:")
for y in ('2018','2020','2021','2022','2024'):
    cohort(NEW & pl.col('date').str.starts_with(y), f"year {y}")
print("by n_prior (warmup adequacy):")
for lo,hi in [(120,250),(250,500),(500,9999)]:
    cohort(NEW & (pl.col('n_prior')>=lo) & (pl.col('n_prior')<hi), f"n_prior {lo}-{hi}")

print("\n########## C. KILLER Q: does ps_z add info BEYOND overall at the SAME level? ##########")
print("Within each overall band, split by ps_z bucket — if WR is FLAT across ps_z, ps_z is redundant:")
for lo,hi in [(60,69),(70,74)]:
    band = df.filter((pl.col('overall')>=lo)&(pl.col('overall')<=hi))
    print(f"  overall {lo}-{hi} (N={band.height}): ps_z bins ->")
    for zlo,zhi in [(-9,1.0),(1.0,1.5),(1.5,2.0),(2.0,9)]:
        b = band.filter((pl.col('ps_z')>=zlo)&(pl.col('ps_z')<zhi))
        o=wr(b['opt15']); a=wr(b['apex15'])
        if o[1]>=20:
            print(f"      ps_z[{zlo:+.1f},{zhi:+.1f}) N={o[1]:6} opt15={o[2]*100:4.1f}% apex15={a[2]*100:4.1f}%")
print("  -> if opt15 is ~constant across ps_z within a band, normalization = 'lower the threshold', known-null.")

print("\n########## D. SKEPTIC checks ##########")
print("concentration: ps_z>=1.5&abs<75 with top-5 contributing symbols DROPPED:")
topsyms = (df.filter(NEW).group_by('symbol').agg(pl.len().alias('n')).sort('n',descending=True).head(5)['symbol'].to_list())
print(f"   top-5 new-signal symbols: {topsyms}")
cohort(NEW & ~pl.col('symbol').is_in(topsyms), "ps_z>=1.5&abs<75 minus top-5 syms")
print("PIT robustness (n_prior>=250):")
cohort(NEW & (pl.col('n_prior')>=250), "ps_z>=1.5&abs<75 & n_prior>=250")

print("\n########## E. BRIDGE supply ##########")
abs_syms = df.filter(pl.col('overall')>=75)['symbol'].n_unique()
new_syms = df.filter(NEW)['symbol'].n_unique()
mute_contrib = df.filter(NEW & pl.col('is_mute'))['symbol'].n_unique()
print(f"  distinct stocks: absolute>=75 fires for {abs_syms} stocks | ps_z>=1.5&abs<75 adds {new_syms} stocks "
      f"({mute_contrib} of them are mute stocks that could NEVER signal absolutely)")
print(f"  signal count: abs>=75={int((df['overall']>=75).sum())}  vs  ps_z>=1.5&abs<75={int(df.filter(NEW).height)}")
