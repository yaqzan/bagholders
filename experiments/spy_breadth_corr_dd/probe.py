"""
SPY<->breadth correlation / divergence DD probe (read-only, no MC, no recalc).

User /research ask (2026-06-23): "use the marketwave or refine it and find the
correlation with the SPY to detect market breadth collapses where it would be a
bad time to be holding call options."

This is a CLOSED-AXIS triage confirmation. The idea is already operationalized:
  - MWDD (shipped 2026-06-05): Market-Wave / McClellan breadth-momentum flat-band
    CALL-alloc dampener. Crash-state contraction FALSIFIED (crash cohorts are
    mean-reversion winners); flat/topping band shipped.
  - BDIV (shipped 2026-06-11): SPY within ~1-2% of 60d high WHILE breadth rolls
    over -> CALL-alloc dampener (the SPY-breadth divergence at the top).
Both LIVE in v74. The one literally-untested formulation is a *rolling Pearson
correlation* between SPY and breadth. This probe tests whether THAT formulation
has any orthogonal low-EV DD signal AFTER the 5 shipped levers (RXDD/SVR/MWDD/
TVDD/BDIV) + F3F.

Substrate: the existing .cache/dd_ledger MC tapes (v71-era). The market-context
-> per-trade-EV relationship being tested is version-robust (it's about how calls
bought in different market regimes perform, dominated by the underlying market,
not the marginal v73->v74 lean; MWDD/BDIV themselves mined v70/v71 tapes and
shipped to the v74-active book).

Decisive tests:
  1. EV-by-rolling-corr bucket (full tape + per-window robustness).
  2. EV-by-SPY/breadth-divergence bucket (the economic "leadership narrowing").
  3. The ALL-5-LEVERS-OFF orthogonal slice: does either feature STILL isolate a
     low-EV, DD-concentrated cohort there? (G21/G23 decisive test.)

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/spy_breadth_corr_dd/probe.py
"""
import sys, math, json
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
# G29: in a worktree the global PYTHONPATH may front the main checkout; force this root first.
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))
TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT = ROOT / "experiments" / "spy_breadth_corr_dd"
OUT.mkdir(parents=True, exist_ok=True)

MIN_N = 200
LOWEV = 0.045   # mean option pnl below this == "low-EV" (RXDD bleed band was +0.027; MWDD flat band +0.046)
CORR_WIN = 20   # rolling window for the SPY<->breadth correlation


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


def load_tapes():
    files = sorted(TAPE_DIR.glob("tape_*.parquet"))
    if not files:
        print(f"!! no tape_*.parquet in {TAPE_DIR}")
        sys.exit(2)
    frames = []
    for f in files:
        label = f.stem.replace("tape_", "")
        try:
            df = pl.read_parquet(f)
        except Exception as e:
            print(f"   skip {f.name}: {e}")
            continue
        df = df.with_columns(pl.lit(label).alias("window"))
        frames.append(df)
        print(f"   loaded {f.name:28s} rows={df.height}")
    T = pl.concat(frames, how="diagonal_relaxed")
    T = _norm_date(T, "entry_date")
    if "side" in T.columns:
        T = T.filter(pl.col("side") == "call")
    T = T.with_columns(
        (pl.col("premium_cost") * pl.col("option_pnl")).alias("pnl_dollars"),
        (pl.col("option_pnl") < 0).alias("loser"),
    )
    return T


def load_context():
    """date -> breadth/McClellan/TRIN (DB) + VIX (DB) + SPY close (PriceHistory)
    -> the user's SPY<->breadth correlation + divergence features + the shipped-lever
    active conditions."""
    from database.models.core import MarketBreadth, MarketRegime
    from database.models.technical import PriceHistory

    brd_rows = list(MarketBreadth.select(
        MarketBreadth.date, MarketBreadth.breadth_score, MarketBreadth.mcclellan_oscillator,
        MarketBreadth.trin))
    B = pl.DataFrame({
        "date": [r.date for r in brd_rows],
        "breadth": [r.breadth_score for r in brd_rows],
        "mcc": [r.mcclellan_oscillator for r in brd_rows],
        "trin": [r.trin for r in brd_rows],
    })
    B = _norm_date(B, "date").sort("date")

    reg_rows = list(MarketRegime.select(MarketRegime.date, MarketRegime.vix_close))
    R = pl.DataFrame({"date": [r.date for r in reg_rows], "vix": [r.vix_close for r in reg_rows]})
    R = _norm_date(R, "date").sort("date")

    spy_rows = list(PriceHistory.select(PriceHistory.date, PriceHistory.close)
                    .where(PriceHistory.symbol == 'SPY').order_by(PriceHistory.date))
    S = pl.DataFrame({"date": [r.date for r in spy_rows], "spy": [float(r.close) for r in spy_rows]})
    S = _norm_date(S, "date").sort("date")

    ctx = (B.join(R, on="date", how="full", coalesce=True)
             .join(S, on="date", how="full", coalesce=True).sort("date"))
    fill = [c for c in ctx.columns if c != "date"]
    ctx = ctx.with_columns([pl.col(c).forward_fill() for c in fill])

    # --- daily change series for the correlation ---
    ctx = ctx.with_columns([
        (pl.col("spy") / pl.col("spy").shift(1) - 1.0).alias("spy_ret"),
        (pl.col("breadth") - pl.col("breadth").shift(1)).alias("brd_d1"),
    ])
    # --- rolling Pearson corr(spy_ret, brd_d1) over CORR_WIN days (the literal "correlation with SPY") ---
    x, y = pl.col("spy_ret"), pl.col("brd_d1")
    mx = x.rolling_mean(CORR_WIN)
    my = y.rolling_mean(CORR_WIN)
    mxy = (x * y).rolling_mean(CORR_WIN)
    vx = (x * x).rolling_mean(CORR_WIN) - mx * mx
    vy = (y * y).rolling_mean(CORR_WIN) - my * my
    cov = mxy - mx * my
    ctx = ctx.with_columns(
        (cov / (vx.sqrt() * vy.sqrt())).alias("corr_spy_brd")
    )
    # --- 10d divergence: SPY trend vs breadth trend (leadership narrowing) ---
    ctx = ctx.with_columns([
        (pl.col("spy") / pl.col("spy").shift(10) - 1.0).alias("spy_chg10"),
        (pl.col("breadth") - pl.col("breadth").shift(10)).alias("brd_chg10"),
        # SPY 60d-high distance (the BDIV proximity term), <= 0
        (pl.col("spy") / pl.col("spy").rolling_max(60) - 1.0).alias("spy_from60h"),
    ])
    # divergence score: SPY up while breadth down = positive narrowing pressure
    ctx = ctx.with_columns(
        (pl.col("spy_chg10") * 100.0 - pl.col("brd_chg10")).alias("div_score")
    )
    print(f"   context rows={ctx.height}  ({ctx['date'].min()} .. {ctx['date'].max()})")
    print(f"   corr_spy_brd non-null: {ctx['corr_spy_brd'].drop_nulls().len()}  "
          f"median={ctx['corr_spy_brd'].median():.3f}")
    return ctx


def add_bins(T):
    return T.with_columns([
        # rolling SPY<->breadth correlation buckets
        pl.when(pl.col("corr_spy_brd") < 0.0).then(pl.lit("corr1_neg_decoupled"))
          .when(pl.col("corr_spy_brd") < 0.2).then(pl.lit("corr2_lo_0_20"))
          .when(pl.col("corr_spy_brd") < 0.4).then(pl.lit("corr3_mid_20_40"))
          .when(pl.col("corr_spy_brd") < 0.6).then(pl.lit("corr4_hi_40_60"))
          .otherwise(pl.lit("corr5_vhi_60p")).alias("b_corr"),
        # SPY-up/breadth-down divergence (leadership narrowing) buckets
        pl.when(pl.col("div_score") < -5).then(pl.lit("div1_brd_leads"))
          .when(pl.col("div_score") < 5).then(pl.lit("div2_aligned"))
          .when(pl.col("div_score") < 15).then(pl.lit("div3_spy_lead_mild"))
          .otherwise(pl.lit("div4_spy_lead_strong")).alias("b_div"),
        # explicit "breadth collapse while SPY holds" flag (the user's literal phrasing)
        ((pl.col("spy_chg10") > 0) & (pl.col("brd_chg10") < -5)).alias("collapse_flag"),
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
    print(f"\n### {title}  (base loser-rate={base:.4f}, base mpnl varies, N={total_n})")
    print(f"{'cohort':28s} {'n':>9s} {'rate':>6s} {'lift':>5s} {'z':>7s} {'conc':>6s} {'mpnl':>7s}")
    for r in rows[:topn]:
        flag = "  <-LOW-EV" if r["mean_pnl"] < LOWEV else ("  WINNER" if r["mean_pnl"] > 0.07 else "")
        print(f"{r['cohort']:28s} {r['n']:>9d} {r['rate']:>6.3f} {r['lift']:>5.2f} "
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
        pk = sub.filter(pl.col(bincol).is_in(target_vals) if isinstance(target_vals, list)
                        else (pl.col(bincol) == target_vals))
        if pk.height >= 30:
            pm = pk["option_pnl"].mean(); pr = pk["loser"].mean()
            tag = "LOW-EV" if pm < LOWEV else ("WINNER" if pm > 0.07 else "ok")
            print(f"  {w:12s} base_mpnl={base_m:+.3f} | cohort n={pk.height:6d} rate={pr:.3f} mpnl={pm:+.3f}  [{tag}]")
            out[w] = {"base_mpnl": round(base_m, 4), "n": pk.height, "rate": round(pr, 4), "mean_pnl": round(pm, 4)}
        else:
            print(f"  {w:12s} base_mpnl={base_m:+.3f} | cohort n={pk.height:6d} (<30 skip)")
    return out


def main():
    print("== loading tapes ==")
    T = load_tapes()
    print(f"   total call trades: {T.height}   windows: {sorted(T['window'].unique().to_list())}")
    print("== loading context (breadth + VIX + SPY) + building corr/divergence ==")
    ctx = load_context()
    T = T.sort("entry_date").join_asof(ctx.rename({"date": "entry_date"}), on="entry_date", strategy="backward")
    T = add_bins(T)
    base = T["loser"].mean()
    base_mpnl = T["option_pnl"].mean()
    wins = sorted(T["window"].unique().to_list())
    print(f"   base loser-rate={base:.4f}  base mean_pnl={base_mpnl:+.4f}")
    report = {"n_trades": T.height, "base_loser_rate": round(base, 4),
              "base_mpnl": round(base_mpnl, 4), "windows": wins, "cohorts": {}, "robustness": {}}

    print("\n\n########## (1) FULL TAPE — does the user's signal isolate low-EV DD? ##########")
    report["cohorts"]["corr"] = cohort(T, ["b_corr"], base, "rolling SPY<->breadth correlation")
    report["cohorts"]["div"] = cohort(T, ["b_div"], base, "SPY-up/breadth-down divergence (10d)")
    report["cohorts"]["collapse_flag"] = cohort(T, ["collapse_flag"], base,
                                                "collapse_flag (SPY up 10d & breadth -5+/10d) -- the user's literal phrasing")

    print("\n\n########## (2) ROBUSTNESS (crash-artifact guard) ##########")
    report["robustness"]["corr_decoupled"] = robustness(T, "b_corr", ["corr1_neg_decoupled", "corr2_lo_0_20"], wins)
    report["robustness"]["div_narrowing"] = robustness(T, "b_div", ["div3_spy_lead_mild", "div4_spy_lead_strong"], wins)
    report["robustness"]["collapse_flag"] = robustness(T, "collapse_flag", [True], wins)

    print("\n\n########## (3) ALL-5-LEVERS-OFF ORTHOGONAL SLICE ##########")
    print("   RXDD off (vix<20) AND F3F off (breadth>=40) AND MWDD off (|mcc|>15)")
    print("   AND BDIV off (NOT SPY within 2% of 60d high with breadth_chg10<-5)")
    print("   AND TVDD off (TRIN outside 1.0-1.3)")
    bdiv_active = (pl.col("spy_from60h") > -0.02) & (pl.col("brd_chg10") < -5)
    tvdd_active = (pl.col("trin") >= 1.0) & (pl.col("trin") <= 1.3)
    O = T.filter(
        (pl.col("vix") < 20) &
        (pl.col("breadth") >= 40) &
        (pl.col("mcc").abs() > 15) &
        (~bdiv_active) &
        (~tvdd_active)
    )
    if O.height >= MIN_N:
        ob = O["loser"].mean(); om = O["option_pnl"].mean()
        print(f"   slice trades: {O.height}  ({100*O.height/T.height:.1f}% of book)  "
              f"slice base loser-rate={ob:.4f}  slice mean_pnl={om:+.4f}")
        report["ortho_slice_n"] = O.height
        report["ortho_slice_mpnl"] = round(om, 4)
        report["ortho"] = {}
        report["ortho"]["corr"] = cohort(O, ["b_corr"], ob, "ORTHO rolling SPY<->breadth correlation")
        report["ortho"]["div"] = cohort(O, ["b_div"], ob, "ORTHO SPY-up/breadth-down divergence")
        report["ortho"]["collapse_flag"] = cohort(O, ["collapse_flag"], ob, "ORTHO collapse_flag")
    else:
        print("   (orthogonal slice too small)")

    (OUT / "probe_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\n== wrote {OUT/'probe_report.json'} ==")


if __name__ == "__main__":
    main()
