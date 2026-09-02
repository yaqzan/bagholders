"""
%-at-all-time-high (ATH) breadth-extreme DD mine on the v70/v71 Apex MC tape (calls-only).

User /research (2026-06-26): "Is there alpha in % of stocks at ATH / % underperforming as a
LOG predictor of drawdowns / call-capture risk? ~4% at highs + 85% underperforming = froth-
at-index / narrow-leadership tape that murders capture ratio. Want breadth+VIX de-rating long
calls until breadth broadens."

Prior work (G40 triage): the 'de-rate calls when breadth narrows / froth-at-highs' idea is
ALREADY operationalized -- BDIV (SPY-near-60d-high x breadth-rolling-over, 2026-06-11),
MWDD (McClellan flat/topping band, 2026-06-05), F3F (breadth LEVEL), RXDD (VIX 20-28).
NH/NL (new-52w-highs participation) FAILED the orthogonal slice (G23). The regime-MULTIPLIER
path is a documented no-op (regime amplifies on weak breadth for WR-reliability, not DD).

The ONE genuinely-untested slice: % at ALL-TIME-HIGH specifically (strictly stronger than the
52w-high NH/NL that already failed). Decisive cheap test (read-only, no MC):
  1. EV + loser-rate + dd_conc by pct_at_ATH band -- is the low-%-at-ATH ('few at highs') tape
     actually low-EV for our buy-weakness Apex calls, or INVERTED (mean-reversion winner, G19)?
  2. DD-active subset (entry_dd>=0.13) -- does it concentrate there? (G21)
  3. Per-window robustness -- crash-artifact guard (G3/G19).
  4. ORTHOGONAL slice (RXDD+F3F+MWDD all off) + the G44 2x2 vs MWDD's firing band -- does the
     low-EV cohort SURVIVE where the shipped levers are off, or INVERT to good (= redundant)?

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/breadth_ath_dd/mine.py
"""
import sys, math, json
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT = ROOT / "experiments" / "breadth_ath_dd"
OUT.mkdir(parents=True, exist_ok=True)
ATH_CACHE = ROOT / ".cache" / "breadth_ath_pct.parquet"

MIN_N = 200
LOWEV = 0.045   # mean option pnl below this == "low-EV" (RXDD's bleed band was +0.027)


def _norm_date(df, col):
    if col not in df.columns:
        return df
    dt = df.schema[col]
    if dt == pl.Date:
        return df
    if dt == pl.Datetime:
        return df.with_columns(pl.col(col).cast(pl.Date))
    if dt == pl.Utf8:
        return df.with_columns(pl.col(col).str.strptime(pl.Date, "%Y-%m-%d", strict=False))
    return df.with_columns(pl.col(col).cast(pl.Date, strict=False))


def build_ath_pct():
    """date -> % of tracked (sectored) universe at/near ATH and near long-lookback highs.

    pct_ath0  : within 0.5% of expanding all-time max close (the 'at ATH' the user means)
    pct_ath2  : within 2% of ATH
    pct_ath5  : within 5% of ATH
    pct_252   : within 0.5% of trailing-252d high (the NH/NL-adjacent 52w-high condition)
    pct_504   : within 0.5% of trailing-504d (2y) high
    """
    if ATH_CACHE.exists():
        return pl.read_parquet(ATH_CACHE)
    from database.bulk_cache import chunked_query_by_year
    print("   pulling sectored-universe closes from PriceHistory (chunked by year)...")
    rows = chunked_query_by_year(
        "SELECT ph.symbol, ph.date, ph.close FROM price_history ph "
        "JOIN stocks s ON s.symbol = ph.symbol "
        "WHERE s.sector IS NOT NULL AND ph.close > 0 "
        "AND ph.date BETWEEN '{y_start}' AND '{y_end}'",
        start_year=2010, end_year=2027,
    )
    print(f"   pulled {len(rows)} (sym,date,close) rows")
    df = pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date":   [r[1].isoformat() if hasattr(r[1], "isoformat") else r[1] for r in rows],
        "close":  [float(r[2]) for r in rows],
    })
    df = _norm_date(df, "date").sort(["symbol", "date"])
    df = df.with_columns([
        pl.col("close").cum_max().over("symbol").alias("ath"),
        pl.col("close").rolling_max(window_size=252, min_periods=60).over("symbol").alias("h252"),
        pl.col("close").rolling_max(window_size=504, min_periods=120).over("symbol").alias("h504"),
    ])
    df = df.with_columns([
        (pl.col("close") >= 0.995 * pl.col("ath")).alias("at_ath0"),
        (pl.col("close") >= 0.98  * pl.col("ath")).alias("at_ath2"),
        (pl.col("close") >= 0.95  * pl.col("ath")).alias("at_ath5"),
        (pl.col("close") >= 0.995 * pl.col("h252")).alias("at_252"),
        (pl.col("close") >= 0.995 * pl.col("h504")).alias("at_504"),
    ])
    agg = df.group_by("date").agg([
        pl.len().alias("nsym"),
        (100 * pl.col("at_ath0").mean()).alias("pct_ath0"),
        (100 * pl.col("at_ath2").mean()).alias("pct_ath2"),
        (100 * pl.col("at_ath5").mean()).alias("pct_ath5"),
        (100 * pl.col("at_252").mean()).alias("pct_252"),
        (100 * pl.col("at_504").mean()).alias("pct_504"),
    ]).sort("date").filter(pl.col("nsym") >= 100)  # need a real universe count
    agg.write_parquet(ATH_CACHE)
    print(f"   cached {agg.height} daily rows -> {ATH_CACHE}")
    print(f"   pct_ath0 range: {agg['pct_ath0'].min():.1f} .. {agg['pct_ath0'].max():.1f}  median {agg['pct_ath0'].median():.1f}")
    return agg


def load_tapes():
    files = sorted(TAPE_DIR.glob("tape_*.parquet"))
    frames = []
    for f in files:
        label = f.stem.replace("tape_", "")
        try:
            df = pl.read_parquet(f).with_columns(pl.lit(label).alias("window"))
        except Exception as e:
            print(f"   skip {f.name}: {e}"); continue
        frames.append(df)
        print(f"   loaded {f.name:28s} rows={df.height}")
    T = pl.concat(frames, how="diagonal_relaxed")
    T = _norm_date(T, "entry_date")
    T = T.filter(pl.col("side") == "call")
    T = T.with_columns([
        (pl.col("premium_cost") * pl.col("option_pnl")).alias("pnl_dollars"),
        (pl.col("option_pnl") < 0).alias("loser"),
    ])
    return T


def load_ctx():
    """date -> VIX + breadth + McClellan (for the orthogonal slice / MWDD 2x2)."""
    from database.models.core import MarketBreadth, MarketRegime
    brd = list(MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score,
                                    MarketBreadth.mcclellan_oscillator))
    B = pl.DataFrame({"date": [r.date for r in brd],
                      "breadth": [r.breadth_score for r in brd],
                      "mcc": [r.mcclellan_oscillator for r in brd]})
    B = _norm_date(B, "date").sort("date")
    reg = list(MarketRegime.select(MarketRegime.date, MarketRegime.vix_close))
    R = pl.DataFrame({"date": [r.date for r in reg], "vix": [r.vix_close for r in reg]})
    R = _norm_date(R, "date").sort("date")
    ctx = B.join(R, on="date", how="full", coalesce=True).sort("date")
    ctx = ctx.with_columns([pl.col(c).forward_fill() for c in ["breadth", "mcc", "vix"]])
    return ctx


def add_bins(T):
    # quintile-ish bands on pct_ath0 (the 'at ATH' metric). Low band = 'few at highs / narrow'.
    return T.with_columns([
        pl.when(pl.col("pct_ath0") < 2).then(pl.lit("ath0_1_vlo_u2"))
          .when(pl.col("pct_ath0") < 5).then(pl.lit("ath0_2_lo_2_5"))
          .when(pl.col("pct_ath0") < 10).then(pl.lit("ath0_3_mid_5_10"))
          .when(pl.col("pct_ath0") < 18).then(pl.lit("ath0_4_hi_10_18"))
          .otherwise(pl.lit("ath0_5_froth_18p")).alias("b_ath0"),
        pl.when(pl.col("pct_ath5") < 15).then(pl.lit("ath5_1_vlo_u15"))
          .when(pl.col("pct_ath5") < 30).then(pl.lit("ath5_2_lo_15_30"))
          .when(pl.col("pct_ath5") < 45).then(pl.lit("ath5_3_mid_30_45"))
          .otherwise(pl.lit("ath5_4_hi_45p")).alias("b_ath5"),
        # MWDD's firing band: |mcc| small (flat/topping) AND not panic. on==MWDD fires
        pl.when(pl.col("mcc").abs() <= 22).then(pl.lit("mwdd_ON_flat")).otherwise(pl.lit("mwdd_off")).alias("b_mwdd"),
    ])


def cohort(T, axes, base, title, topn=14):
    g = T.group_by(axes).agg(
        pl.len().alias("n"),
        pl.col("loser").mean().alias("rate"),
        pl.col("pnl_dollars").filter(pl.col("loser")).abs().sum().alias("loss_usd"),
        pl.col("option_pnl").mean().alias("mean_pnl"),
    ).filter(pl.col("n") >= MIN_N)
    total_loss = T.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    total_n = T.height
    rows = []
    for r in g.iter_rows(named=True):
        n, rate = r["n"], r["rate"]
        z = (rate - base) / math.sqrt(base * (1 - base) / n) if n else 0.0
        conc = ((r["loss_usd"] or 0.0) / total_loss) / (n / total_n) if n else 0.0
        rows.append({"cohort": " & ".join(str(r[a]) for a in axes), "n": n,
                     "rate": round(rate, 4), "lift": round(rate / base, 2), "z": round(z, 1),
                     "dd_conc": round(conc, 2), "mean_pnl": round(r["mean_pnl"], 4)})
    rows.sort(key=lambda x: x["cohort"])
    print(f"\n### {title}  (base loser-rate={base:.4f}, N={total_n})")
    print(f"{'cohort':34s} {'n':>9s} {'rate':>6s} {'lift':>5s} {'z':>7s} {'conc':>6s} {'mpnl':>7s}")
    for r in rows[:topn]:
        flag = "  <-LOW-EV" if r["mean_pnl"] < LOWEV else ""
        print(f"{r['cohort']:34s} {r['n']:>9d} {r['rate']:>6.3f} {r['lift']:>5.2f} "
              f"{r['z']:>7.1f} {r['dd_conc']:>6.2f} {r['mean_pnl']:>7.3f}{flag}")
    return rows


def robustness(T, bincol, target_vals, windows):
    print(f"\n=== ROBUSTNESS: {bincol} in {target_vals} mean_pnl BY WINDOW (crash-artifact guard) ===")
    out = {}
    for w in windows:
        sub = T.filter(pl.col("window") == w)
        if sub.height == 0:
            continue
        base_m = sub["option_pnl"].mean()
        pk = sub.filter(pl.col(bincol).is_in(target_vals))
        if pk.height >= 30:
            pm = pk["option_pnl"].mean(); pr = pk["loser"].mean()
            tag = "LOW-EV" if pm < LOWEV else "ok"
            print(f"  {w:12s} base={base_m:+.3f} | n={pk.height:6d} rate={pr:.3f} mpnl={pm:+.3f}  [{tag}]")
            out[w] = {"base": round(base_m, 4), "n": pk.height, "rate": round(pr, 4), "mpnl": round(pm, 4)}
    return out


def main():
    print("== building pct-at-ATH series ==")
    A = build_ath_pct()
    print("== loading tape ==")
    T = load_tapes()
    print(f"   call trades: {T.height}  windows: {sorted(T['window'].unique().to_list())}")
    ctx = load_ctx()
    A2 = A.select(["date", "pct_ath0", "pct_ath2", "pct_ath5", "pct_252", "pct_504"])
    T = T.sort("entry_date")
    T = T.join_asof(A2.rename({"date": "entry_date"}).sort("entry_date"), on="entry_date", strategy="backward")
    T = T.join_asof(ctx.rename({"date": "entry_date"}).sort("entry_date"), on="entry_date", strategy="backward")
    T = T.drop_nulls(["pct_ath0", "mcc", "vix", "breadth"])
    T = add_bins(T)
    base = T["loser"].mean()
    wins = sorted(T["window"].unique().to_list())
    rep = {"n": T.height, "base": round(base, 4), "windows": wins}

    print("\n########## 1. FULL TAPE: call-EV by pct-at-ATH band ##########")
    rep["ath0"] = cohort(T, ["b_ath0"], base, "pct_ath0 (within 0.5% of ATH)")
    rep["ath5"] = cohort(T, ["b_ath5"], base, "pct_ath5 (within 5% of ATH)")

    print("\n########## 2. DD-ACTIVE subset (entry_dd>=0.13) ##########")
    D = T.filter(pl.col("entry_dd") >= 0.13)
    if D.height >= MIN_N:
        db = D["loser"].mean()
        print(f"   DD-active trades: {D.height} ({100*D.height/T.height:.1f}%)  base={db:.4f}  mpnl={D['option_pnl'].mean():+.4f}")
        rep["dd_ath0"] = cohort(D, ["b_ath0"], db, "DD-active pct_ath0")

    print("\n########## 3. ROBUSTNESS (per-window crash-artifact guard) ##########")
    rep["rob_lo"] = robustness(T, "b_ath0", ["ath0_1_vlo_u2", "ath0_2_lo_2_5"], wins)   # 'few at highs'
    rep["rob_froth"] = robustness(T, "b_ath0", ["ath0_5_froth_18p"], wins)              # 'froth'

    print("\n########## 4a. ORTHOGONAL SLICE: vix<20 & breadth>=40 & |mcc|>22 (RXDD+F3F+MWDD off) ##########")
    O = T.filter((pl.col("vix") < 20) & (pl.col("breadth") >= 40) & (pl.col("mcc").abs() > 22))
    if O.height >= MIN_N:
        ob = O["loser"].mean()
        print(f"   slice n={O.height} ({100*O.height/T.height:.1f}%)  base={ob:.4f}  mpnl={O['option_pnl'].mean():+.4f}")
        rep["ortho"] = cohort(O, ["b_ath0"], ob, "ORTHO pct_ath0 (all shipped levers off)")
    else:
        print("   (orthogonal slice too small)")

    print("\n########## 4b. G44 2x2: pct_ath0-low x MWDD firing band ##########")
    rep["g44"] = cohort(T, ["b_ath0", "b_mwdd"], base, "pct_ath0 x MWDD(on/off)", topn=20)

    (OUT / "mine_report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(f"\n== wrote {OUT/'mine_report.json'} ==")


if __name__ == "__main__":
    main()
