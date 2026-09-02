"""
Extended mining for the Apex speed re-tune — adjacent-regime axes the user asked
to explore: sector-ETF breadth alignment, daily call/put signal SUPPLY (density),
and the call/put signal ratio. EV = mean option_pnl per cohort (the return lever).

Reuses the 2026-06-04 tape (.cache/dd_ledger/tape_*.parquet). Joins:
  - sector-ETF breadth (% of 11 SPDRs above EMA50) from
    .cache/market_wave/sector_breadth_daily_2020plus.csv  (2020+)
  - daily call/put signal supply (count of v70 qualifying scores per date) from DB
  - VIX/breadth/regime context (same as mine.py)

Usage: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/apex_speed_v70/extend_mine.py
"""
import sys, math, json, csv
from pathlib import Path
from datetime import date as _date
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT = ROOT / "experiments" / "apex_speed_v70"
MIN_N = 150
# recent-regime windows only (sector CSV is 2020+; keeps the join clean)
KEEP = {'2020', '2020_crash', '2021', '2022', '2023', '2024', '2025', 'dip', '22-now', '5y'}


def load_tape():
    frames = []
    for f in sorted(TAPE_DIR.glob("tape_*.parquet")):
        lbl = f.stem.replace("tape_", "")
        if lbl not in KEEP:
            continue
        df = pl.read_parquet(f).with_columns(pl.lit(lbl).alias("window"))
        frames.append(df)
    T = pl.concat(frames, how="diagonal_relaxed")
    if T.schema["entry_date"] != pl.Date:
        T = T.with_columns(pl.col("entry_date").cast(pl.Date, strict=False))
    if "side" in T.columns:
        T = T.filter(pl.col("side") == "call")
    return T.with_columns((pl.col("option_pnl") < 0).alias("loser"))


def load_sector_breadth():
    p = ROOT / ".cache" / "market_wave" / "sector_breadth_daily_2020plus.csv"
    rows = []
    with open(p) as f:
        for r in csv.DictReader(f):
            try:
                rows.append((_date.fromisoformat(r["date"][:10]), float(r["sec_brd_ema50"])))
            except Exception:
                continue
    rows.sort()
    return pl.DataFrame({"entry_date": [d for d, _ in rows],
                         "sec_brd": [v for _, v in rows]}).with_columns(
        pl.col("entry_date").cast(pl.Date))


def load_signal_supply():
    """date -> (#call signals >=75, #put signals <=25) for the active v70 version."""
    from database.models.core import Score, AlgorithmVersion
    vid = AlgorithmVersion.get_active_scores_version().id
    from peewee import fn
    ncall, nput = {}, {}
    q = (Score.select(Score.date,
                      fn.SUM(Score.overall >= 75).alias('nc'),
                      fn.SUM(Score.overall <= 25).alias('np'))
         .where(Score.version_id == vid)
         .group_by(Score.date))
    for r in q:
        ncall[r.date] = int(r.nc or 0)
        nput[r.date] = int(r.np or 0)
    dates = sorted(set(ncall) | set(nput))
    return pl.DataFrame({
        "entry_date": [d for d in dates],
        "n_call": [ncall.get(d, 0) for d in dates],
        "n_put": [nput.get(d, 0) for d in dates],
    }).with_columns(pl.col("entry_date").cast(pl.Date))


def cohort(T, axis, base, title):
    g = T.group_by(axis).agg(
        pl.len().alias("n"),
        pl.col("loser").mean().alias("rate"),
        pl.col("option_pnl").mean().alias("ev"),
    ).filter(pl.col("n") >= MIN_N)
    rows = []
    for r in g.iter_rows(named=True):
        n, rate = r["n"], r["rate"]
        z = (rate - base) / math.sqrt(base * (1 - base) / n) if n else 0.0
        rows.append({"cohort": str(r[axis]), "n": n, "ev": round(r["ev"], 4),
                     "rate": round(rate, 4), "z": round(z, 1)})
    rows.sort(key=lambda x: -x["ev"])
    print(f"\n=== {title} (sorted by EV; base loser-rate {base:.3f}) ===")
    print(f"{'cohort':16s} {'n':>9s} {'EV':>9s} {'rate':>6s} {'z':>7s}")
    for r in rows:
        print(f"{r['cohort']:16s} {r['n']:>9d} {r['ev']:>9.4f} {r['rate']:>6.3f} {r['z']:>7.1f}")
    return rows


def main():
    print("== load tape =="); T = load_tape()
    print(f"   call trades: {T.height}")
    rep = {}

    # --- sector ETF breadth ---
    try:
        sb = load_sector_breadth()
        T = T.sort("entry_date").join_asof(sb, on="entry_date", strategy="backward")
        T = T.with_columns(
            pl.when(pl.col("sec_brd") >= 80).then(pl.lit("secbrd_80p"))
              .when(pl.col("sec_brd") >= 60).then(pl.lit("secbrd_60_80"))
              .when(pl.col("sec_brd") >= 40).then(pl.lit("secbrd_40_60"))
              .when(pl.col("sec_brd") >= 20).then(pl.lit("secbrd_20_40"))
              .otherwise(pl.lit("secbrd_u20")).alias("b_secbrd"))
        base = T["loser"].mean()
        rep["sector_breadth"] = cohort(T.filter(pl.col("sec_brd").is_not_null()),
                                       "b_secbrd", base, "SECTOR-ETF breadth (% SPDRs > EMA50)")
    except Exception as e:
        print(f"!! sector breadth skipped: {e}")

    # --- daily call/put signal supply + ratio ---
    try:
        ss = load_signal_supply()
        T = T.join(ss, on="entry_date", how="left")
        T = T.with_columns([
            pl.when(pl.col("n_call") >= 200).then(pl.lit("ncall_200p"))
              .when(pl.col("n_call") >= 120).then(pl.lit("ncall_120_200"))
              .when(pl.col("n_call") >= 60).then(pl.lit("ncall_60_120"))
              .when(pl.col("n_call") >= 25).then(pl.lit("ncall_25_60"))
              .otherwise(pl.lit("ncall_u25")).alias("b_ncall"),
            (pl.col("n_call") / (pl.col("n_put") + 1.0)).alias("cp_ratio"),
        ])
        T = T.with_columns(
            pl.when(pl.col("cp_ratio") >= 8).then(pl.lit("cpr_8p_bull"))
              .when(pl.col("cp_ratio") >= 3).then(pl.lit("cpr_3_8"))
              .when(pl.col("cp_ratio") >= 1).then(pl.lit("cpr_1_3"))
              .when(pl.col("cp_ratio") >= 0.3).then(pl.lit("cpr_03_1"))
              .otherwise(pl.lit("cpr_u03_bear")).alias("b_cpr"))
        base = T["loser"].mean()
        sup = T.filter(pl.col("n_call").is_not_null())
        rep["signal_density"] = cohort(sup, "b_ncall", base, "DAILY CALL-SIGNAL SUPPLY (density)")
        rep["call_put_ratio"] = cohort(sup, "b_cpr", base, "CALL/PUT SIGNAL RATIO (sentiment proxy)")
        # also report mean daily supply (for sizing the mechanism thresholds)
        ds = ss.with_columns((pl.col("n_call") / (pl.col("n_put") + 1.0)).alias("cp_ratio"))
        print(f"\n   daily supply over {ds.height} dates: "
              f"n_call median={ds['n_call'].median():.0f} p10={ds['n_call'].quantile(0.1):.0f} "
              f"p90={ds['n_call'].quantile(0.9):.0f}; cp_ratio median={ds['cp_ratio'].median():.1f}")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"!! signal supply skipped: {e}")

    (OUT / "extend_mine_report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(f"\n== wrote {OUT/'extend_mine_report.json'} ==")


if __name__ == "__main__":
    main()
