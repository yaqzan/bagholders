"""
Why is 2024 so explosive under v70 Apex? — per-year DRIVER decomposition.

Source: .cache/dd_ledger/tape_<year>.parquet  (the live full-lever Apex MC tape,
300 seeds, calls-only, RXDD+SVR+MWDD live; from the 2026-06-07 TVDD mining run).

The levers only scale premium_cost (dollar weight), NOT per-trade option_pnl, so the
per-trade pnl distribution here IS the pure underlying x strategy. We decompose each
year into the orthogonal multiplicative drivers of a compounding options sleeve:

  compound growth  ~  (signal density)  x  (per-trade edge: E[log(1+f*pnl)])
                      x  (capital velocity: 1/hold)  x  (uninterrupted-ness: low DD)

and the convexity tail (a few monster dh_pop / tp winners).

Zero new compute — pure tape mining.
"""
import sys, math
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
TAPE = ROOT / ".cache" / "dd_ledger"

YEARS = ["2018", "2020", "2021", "2022", "2023", "2024", "2025"]

def load(year):
    df = pl.read_parquet(TAPE / f"tape_{year}.parquet")
    # parse dates, compute hold in calendar days
    df = df.with_columns(
        pl.col("entry_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False).alias("ed"),
        pl.col("exit_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False).alias("xd"),
    )
    df = df.with_columns((pl.col("xd") - pl.col("ed")).dt.total_days().alias("hold_days"))
    return df

def pct(x): return f"{x:6.2f}"

rows = []
for y in YEARS:
    f = TAPE / f"tape_{y}.parquet"
    if not f.exists():
        continue
    df = load(y)
    n_seeds = df["seed"].n_unique()
    n = df.height
    per_seed = n / n_seeds                      # trades per seed (density proxy)
    # trading-day span per seed -> trades/day
    span_days = (df["xd"].max() - df["ed"].min()).days
    trd_days = span_days * 5 / 7 if span_days else 1
    trades_per_day = per_seed / max(trd_days, 1)

    pnl = df["option_pnl"]
    # outcomes
    oc = df.group_by("outcome").len()
    oc_map = {r["outcome"]: r["len"] for r in oc.iter_rows(named=True)}
    tp_rate = 100 * oc_map.get("tp", 0) / n
    dhpop_rate = 100 * oc_map.get("dh_pop", 0) / n
    dhexp_rate = 100 * oc_map.get("dh_expiry", 0) / n
    hard_rate = 100 * oc_map.get("hard", 0) / n

    # per-trade edge
    mean_pnl = pnl.mean()
    med_pnl = pnl.median()
    win_rate = 100 * (pnl > 0).sum() / n        # any positive pnl
    # dollar-weighted (premium-weighted) edge — what actually compounds
    dollar_w = (df["option_pnl"] * df["premium_cost"]).sum() / df["premium_cost"].sum()
    # geometric per-trade edge proxy at f=full-tier (use realized pnl as the per-trade
    # multiplicative return on the deployed premium): E[log(1+pnl)]
    logret = df.select((1.0 + pl.col("option_pnl")).log().alias("lr"))["lr"]
    elog = logret.mean()

    # convexity tail
    p90 = pnl.quantile(0.90); p99 = pnl.quantile(0.99); pmax = pnl.max()
    # share of total positive pnl from the top 1% of trades
    pos = df.filter(pl.col("option_pnl") > 0).sort("option_pnl", descending=True)
    tot_pos = pos["option_pnl"].sum()
    k1 = max(1, pos.height // 100)
    top1_share = 100 * pos.head(k1)["option_pnl"].sum() / tot_pos if tot_pos else 0

    # capital velocity + book state
    hold = df["hold_days"].mean()
    open_calls = df["entry_open_calls"].mean()
    entry_dd = df["entry_dd"].mean()            # avg portfolio DD at entry (uninterrupted-ness)
    frac_entries_in_dd = 100 * (df["entry_dd"] > 0.15).sum() / n  # entries struck while >15% underwater

    rows.append(dict(
        year=y, n_per_seed=per_seed, trd_per_day=trades_per_day,
        tp=tp_rate, dhpop=dhpop_rate, dhexp=dhexp_rate, hard=hard_rate,
        winrate=win_rate, mean_pnl=mean_pnl, med_pnl=med_pnl, dollar_w=dollar_w,
        elog=elog, p90=p90, p99=p99, pmax=pmax, top1=top1_share,
        hold=hold, open_calls=open_calls, entry_dd=entry_dd, in_dd=frac_entries_in_dd,
    ))

print("\n=== v70 APEX per-year DRIVER decomposition (live full-lever tape, 300 seeds, calls-only) ===\n")
hdr = ("year  trd/sd  trd/day |  TP%   dhPop  dhExp  hard | win%  meanPnl medPnl  $-wtd  E[log] |"
       "  p90   p99    max   top1% | hold  oCalls  entryDD  in_dd%")
print(hdr); print("-"*len(hdr))
for r in rows:
    print(f"{r['year']:<5} {r['n_per_seed']:6.0f} {r['trd_per_day']:7.2f} | "
          f"{r['tp']:5.1f} {r['dhpop']:6.1f} {r['dhexp']:6.1f} {r['hard']:5.1f} | "
          f"{r['winrate']:4.1f} {r['mean_pnl']:+7.3f} {r['med_pnl']:+6.3f} {r['dollar_w']:+6.3f} {r['elog']:+6.3f} | "
          f"{r['p90']:+5.2f} {r['p99']:+5.2f} {r['pmax']:6.1f} {r['top1']:5.1f} | "
          f"{r['hold']:4.1f} {r['open_calls']:6.2f} {r['entry_dd']:7.3f} {r['in_dd']:6.1f}")

# FAITHFUL compound reconstruction from the tape:
#   each trade changes equity by frac = (premium_cost/entry_value) * pnl
#   per seed, accumulate log10(1+frac) over trades in exit-date order -> log10 equity multiple
# 1+frac is always > 0 (frac in [-tier,+tier*pnl], tier ~0.1-0.2) so no nan.
print("\n=== FAITHFUL compound reconstruction (per-trade equity-fraction, exit-ordered) ===")
print("mean over 300 seeds of log10(final/start); x100 -> approx %; also median.\n")
print(" year   log10_mult   ~compound%        med_log10   p10_log10  p90_log10")
print(" " + "-"*70)
comp_rows = []
for y in YEARS:
    f = TAPE / f"tape_{y}.parquet"
    if not f.exists():
        continue
    df = load(y)
    df = df.with_columns(
        ((pl.col("premium_cost") / pl.col("entry_value")) * pl.col("option_pnl")).alias("frac")
    )
    df = df.with_columns((1.0 + pl.col("frac")).clip(1e-6, None).log10().alias("lg"))
    per_seed = (df.sort("xd").group_by("seed").agg(pl.col("lg").sum().alias("log10mult")))
    lm = per_seed["log10mult"]
    mean_lm = lm.mean(); med_lm = lm.median()
    p10 = lm.quantile(0.10); p90 = lm.quantile(0.90)
    comp = (10 ** mean_lm - 1) * 100
    comp_rows.append((y, mean_lm, comp, med_lm, p10, p90))
    cs = f"{comp:,.0f}%" if abs(comp) < 1e9 else f"{comp:.2e}%"
    print(f" {y:<5}  {mean_lm:9.3f}   {cs:>14}      {med_lm:8.3f}   {p10:8.3f}  {p90:8.3f}")

print("\n  (note: tape is the LIVE full-lever config — RXDD+SVR+MWDD on — so these are")
print("   slightly below the clean-Apex headline; the YEAR RANKING is the point.)")
