"""Feasibility recon for the IV/skew probe.
1. option_prices date coverage (indexed MIN/MAX/count).
2. How many of our 60-99 CALL signals fall in the covered window (W1 power check).
3. Sample ATM-IV + skew lookup for a couple signals to confirm the join works.
"""
import os, sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, _ROOT)
import polars as pl
from database.trader_database import DB

print("=== option_prices coverage (indexed MIN/MAX only — no heavy scans) ===")
r = DB.execute_sql("SELECT MIN(date), MAX(date) FROM option_prices").fetchone()
print("date range:", r[0], "->", r[1])
cov_start = str(r[0])
# IV non-null rate on ONE recent date (bounded by date index — fast)
r3 = DB.execute_sql("SELECT COUNT(*), SUM(iv IS NOT NULL) FROM option_prices WHERE date=%s", (str(r[1]),)).fetchone()
print(f"on latest date {r[1]}: {r3[0]} contracts, {r3[1]} with IV ({100*(r3[1] or 0)/max(r3[0],1):.0f}%)")

print("\n=== signal overlap (component_reweight 60-99 call ledger) ===")
led = pl.read_parquet(os.path.join(_ROOT, ".cache", "rel_strength", "rs_ledger.parquet"))
inwin = led.filter(pl.col("date") >= cov_start)
print(f"total ledger N={led.height}; in covered window (>= {cov_start}): {inwin.height}")
for lo, hi, nm in [(75, 99, "75+"), (70, 74, "70-74"), (80, 99, "80+")]:
    n = inwin.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi)).height
    print(f"  {nm}: {n} signals in covered window")
print(f"  distinct symbols (75+ in-window): {inwin.filter(pl.col('overall')>=75)['symbol'].n_unique()}")

print("\n=== sample ATM-IV + skew lookup (confirm join) ===")
samp = inwin.filter(pl.col("overall") >= 75).head(3)
for sym, d in zip(samp["symbol"].to_list(), samp["date"].to_list()):
    # nearest-ATM ~30 DTE CALL + its IV on date; plus a put for skew
    q = ("SELECT o.option_type, o.strike_price, o.expiration_date, op.iv, op.volume "
         "FROM options o JOIN option_prices op ON op.option_id=o.id "
         "JOIN price_history ph ON ph.symbol=o.symbol AND ph.date=op.date "
         "WHERE o.symbol=%s AND op.date=%s AND DATEDIFF(o.expiration_date, op.date) BETWEEN 20 AND 45 "
         "AND op.iv IS NOT NULL "
         "ORDER BY o.option_type, ABS(o.strike_price - ph.close)/ph.close LIMIT 6")
    rows = DB.execute_sql(q, (sym, d)).fetchall()
    print(f"  {sym} {d}: {len(rows)} near-ATM contracts")
    for ot, k, exp, iv, vol in rows[:4]:
        print(f"      {ot:5} K={float(k):.1f} exp={exp} iv={float(iv) if iv else None} vol={vol}")
