import polars as pl
df = pl.read_parquet('.cache/honest_miss/ledger_puts_5y.parquet').with_columns(pl.col('overall').cast(pl.Int64))

pool = df.filter(pl.col('overall') <= 25)
yrs = 5.0
p_opt = pool['opt15'].mean()
ev_pool = p_opt * 35 - (1 - p_opt) * 20
print('=== HONEST TRADABLE PUT POOL (overall<=25) ===')
print('total N=%d  (~%.0f/yr)' % (pool.height, pool.height / yrs))
print('pool optTP15 = %.2f%%  (BE=36.4)' % (p_opt * 100))
print('pool EV/trade(35/-20) = %+.3f%%' % ev_pool)
print()

rm = df.filter((pl.col('overall') >= 21) & (pl.col('overall') <= 25) & (pl.col('w_mom') <= -4))
r_opt = rm['opt15'].mean()
print('WMPD removal set: N=%d (~%.0f/yr)' % (rm.height, rm.height / yrs))
print('  = %.1f%% of total tradable put pool' % (rm.height / pool.height * 100))
print('  removal-set optTP15=%.2f%%  EV=%+.3f%%' % (r_opt * 100, r_opt * 35 - (1 - r_opt) * 20))
print()

kept = pool.filter(~((pl.col('overall') >= 21) & (pl.col('overall') <= 25) & (pl.col('w_mom') <= -4)))
k_opt = kept['opt15'].mean()
ev_kept = k_opt * 35 - (1 - k_opt) * 20
print('pool AFTER WMPD removal: N=%d (~%.0f/yr)' % (kept.height, kept.height / yrs))
print('  pool optTP15 -> %.2f%%  (was %.2f%%)  delta=%+.2fpp' % (k_opt * 100, p_opt * 100, (k_opt - p_opt) * 100))
print('  pool EV/trade -> %+.3f%%  (was %+.3f%%)' % (ev_kept, ev_pool))
print()

# breakdown: where in the pool does WMPD removal sit
print('=== pool composition (tradable <=25) by tier ===')
for lo, hi, nm in [(21, 25, '21-25'), (16, 20, '16-20'), (0, 15, '<=15')]:
    sub = pool.filter((pl.col('overall') >= lo) & (pl.col('overall') <= hi))
    so = sub['opt15'].mean()
    print('  %s: N=%d (%.0f%% of pool)  optTP15=%.2f%%  EV=%+.3f%%' % (
        nm, sub.height, sub.height / pool.height * 100, so * 100, so * 35 - (1 - so) * 20))
print()
# what fraction of the *21-25 tier* is the removal set
t = pool.filter((pl.col('overall') >= 21) & (pl.col('overall') <= 25))
print('21-25 tier: N=%d, WMPD removes %d (%.0f%% of tier)' % (t.height, rm.height, rm.height / t.height * 100))
