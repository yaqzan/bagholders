"""
Market-Wave / breadth-crash DD mining on the v70 Apex MC trade-tape (calls-only).

Overnight Stage-3 research (2026-06-05). User asked: "the portfolio drew down on a
market pullback today; can we use the (sector-ETF) Market Wave to protect ourselves,
and improve it?" Today's pullback (2026-06-05) had VIX only ~20 (bottom edge of RXDD's
band) + regime_mult 1.03 (HEALTHY) but breadth_score crashed 42->27 and the sector
Market Wave went Stress -> the kind of breadth-deterioration pullback that VIX/regime
(and thus RXDD) largely MISS.

Goal: find a market-crash-state signal whose call cohort is *robustly* low-EV AND
DD-concentrated AND orthogonal to the shipped levers (RXDD=VIX 20-28, F3F=breadth LEVEL),
so contracting its call sizing cuts DD at minimal compound cost (the RXDD recipe).
Reject anything that is a CRASH ARTIFACT (low-EV only in crash windows, positive-EV in
bull years) -- the documented "DD-mitigation is structural" trap.

Candidate signals (all FULL-history):
  - Sector-ETF Market Wave score / signed / crash_echo (rebuilt from SPDR prices, 1999+)
  - McClellan oscillator (internal breadth momentum, DB, full history)  <- not used by any lever
  - breadth_score 5d velocity (internal breadth momentum)               <- F3F uses LEVEL only
  - pct_above_ema50 (level), TRIN (reference)
  - VIX, breadth_score level (reference: what RXDD/F3F already cover)

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/market_wave_dd_v70/mine.py
"""
import sys, math, json
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT = ROOT / "experiments" / "market_wave_dd_v70"
OUT.mkdir(parents=True, exist_ok=True)
WAVE_CACHE = ROOT / ".cache" / "market_wave_dd_v70_wave.parquet"

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


def build_wave():
    """Rebuild production-faithful sector-ETF Market Wave (1999+) -> cached parquet."""
    if WAVE_CACHE.exists():
        return pl.read_parquet(WAVE_CACHE)
    from market_breadth import _load_sector_etf_breadth_rows
    rows = _load_sector_etf_breadth_rows()
    recs = [{
        "date": r["date"],
        "mw": r.get("market_wave_score"),
        "mwsig": r.get("market_wave_signed"),
        "crash_echo": r.get("crash_echo"),
        "eff_crash": r.get("effective_crash_echo"),
        "bull_wave": r.get("bull_wave"),
        "sbd50": r.get("pct_above_ema50"),
        "sbd5chg": r.get("breadth_5d_change"),
        "sbd10chg": r.get("breadth_10d_change"),
    } for r in rows if r.get("market_wave_score") is not None]
    W = pl.DataFrame(recs)
    W = _norm_date(W, "date").sort("date")
    # 5-row velocity of the wave itself
    W = W.with_columns((pl.col("mw") - pl.col("mw").shift(5)).alias("mwvel5"))
    W.write_parquet(WAVE_CACHE)
    return W


def load_context():
    """date -> internal breadth (DB) + VIX (DB) + sector Market Wave (rebuilt)."""
    from database.models.core import MarketBreadth, MarketRegime
    brd_rows = list(MarketBreadth.select(
        MarketBreadth.date, MarketBreadth.breadth_score, MarketBreadth.mcclellan_oscillator,
        MarketBreadth.pct_above_ema50, MarketBreadth.pct_above_ema200, MarketBreadth.trin,
        MarketBreadth.ad_line, MarketBreadth.new_highs_52w, MarketBreadth.new_lows_52w))
    B = pl.DataFrame({
        "date": [r.date for r in brd_rows],
        "breadth": [r.breadth_score for r in brd_rows],
        "mcc": [r.mcclellan_oscillator for r in brd_rows],
        "pe50": [r.pct_above_ema50 for r in brd_rows],
        "pe200": [r.pct_above_ema200 for r in brd_rows],
        "trin": [r.trin for r in brd_rows],
        "adline": [r.ad_line for r in brd_rows],
        "nh": [r.new_highs_52w for r in brd_rows],
        "nl": [r.new_lows_52w for r in brd_rows],
    })
    B = _norm_date(B, "date").sort("date")
    B = B.with_columns([
        (pl.col("breadth") - pl.col("breadth").shift(5)).alias("bvel5"),
        (pl.col("pe50") - pl.col("pe50").shift(5)).alias("pe50vel5"),
        ((pl.col("nh") - pl.col("nl"))).alias("nhnl"),
    ])
    reg_rows = list(MarketRegime.select(MarketRegime.date, MarketRegime.vix_close,
                                        MarketRegime.regime_multiplier, MarketRegime.regime_composite))
    R = pl.DataFrame({
        "date": [r.date for r in reg_rows],
        "vix": [r.vix_close for r in reg_rows],
        "reg_mult": [r.regime_multiplier for r in reg_rows],
        "reg_comp": [r.regime_composite for r in reg_rows],
    })
    R = _norm_date(R, "date").sort("date")
    W = build_wave()
    ctx = B.join(R, on="date", how="full", coalesce=True).join(W, on="date", how="full", coalesce=True).sort("date")
    fill = [c for c in ctx.columns if c != "date"]
    ctx = ctx.with_columns([pl.col(c).forward_fill() for c in fill])
    print(f"   context rows={ctx.height}  ({ctx['date'].min()} .. {ctx['date'].max()})")
    return ctx


def add_bins(T):
    e = [
        pl.when(pl.col("mw") < 20).then(pl.lit("mw1_severe_u20"))
          .when(pl.col("mw") < 35).then(pl.lit("mw2_stress_20_35"))
          .when(pl.col("mw") < 50).then(pl.lit("mw3_weak_35_50"))
          .when(pl.col("mw") < 65).then(pl.lit("mw4_neutral_50_65"))
          .otherwise(pl.lit("mw5_strong_65p")).alias("b_mw"),
        pl.when(pl.col("crash_echo") >= 0.5).then(pl.lit("ce4_hi_50p"))
          .when(pl.col("crash_echo") >= 0.2).then(pl.lit("ce3_mid_20_50"))
          .when(pl.col("crash_echo") > 0.0).then(pl.lit("ce2_lo_0_20"))
          .otherwise(pl.lit("ce1_zero")).alias("b_ce"),
        pl.when(pl.col("mcc") < -50).then(pl.lit("mcc1_sev_un50"))
          .when(pl.col("mcc") < -15).then(pl.lit("mcc2_neg_50_15"))
          .when(pl.col("mcc") < 15).then(pl.lit("mcc3_mid_15_15"))
          .otherwise(pl.lit("mcc4_pos_15p")).alias("b_mcc"),
        pl.when(pl.col("bvel5") < -12).then(pl.lit("bv1_crash_un12"))
          .when(pl.col("bvel5") < -4).then(pl.lit("bv2_dn_12_4"))
          .when(pl.col("bvel5") < 4).then(pl.lit("bv3_flat_4_4"))
          .otherwise(pl.lit("bv4_up_4p")).alias("b_bvel"),
        pl.when(pl.col("mwvel5") < -20).then(pl.lit("mwv1_crash_un20"))
          .when(pl.col("mwvel5") < -8).then(pl.lit("mwv2_dn_20_8"))
          .when(pl.col("mwvel5") < 8).then(pl.lit("mwv3_flat"))
          .otherwise(pl.lit("mwv4_up_8p")).alias("b_mwvel"),
        pl.when(pl.col("vix") >= 28).then(pl.lit("vix_28p"))
          .when(pl.col("vix") >= 20).then(pl.lit("vix_20_28"))
          .when(pl.col("vix") >= 15).then(pl.lit("vix_15_20"))
          .otherwise(pl.lit("vix_u15")).alias("b_vix"),
        pl.when(pl.col("breadth") >= 55).then(pl.lit("brd_hi_55p"))
          .when(pl.col("breadth") >= 40).then(pl.lit("brd_mid_40_55"))
          .when(pl.col("breadth") >= 25).then(pl.lit("brd_lo_25_40"))
          .otherwise(pl.lit("brd_vlo_u25")).alias("b_brd"),
        pl.col("tier").alias("b_tier"),
    ]
    return T.with_columns(e)


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
    print(f"{'cohort':40s} {'n':>9s} {'rate':>6s} {'lift':>5s} {'z':>7s} {'conc':>6s} {'mpnl':>7s}")
    for r in rows[:topn]:
        flag = "  <-LOW-EV" if r["mean_pnl"] < LOWEV else ""
        print(f"{r['cohort']:40s} {r['n']:>9d} {r['rate']:>6.3f} {r['lift']:>5.2f} "
              f"{r['z']:>7.1f} {r['dd_conc']:>6.2f} {r['mean_pnl']:>7.3f}{flag}")
    return rows


def robustness(T, bincol, target_vals, windows):
    """Per-window mean option pnl for target bin value(s) vs window base -> crash-artifact guard."""
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
            print(f"  {w:12s} base_mpnl={base_m:+.3f} | cohort n={pk.height:6d} rate={pr:.3f} mpnl={pm:+.3f}  [{tag}]")
            out[w] = {"base_mpnl": round(base_m, 4), "n": pk.height, "rate": round(pr, 4), "mean_pnl": round(pm, 4)}
        else:
            print(f"  {w:12s} base_mpnl={base_m:+.3f} | cohort n={pk.height:6d} (<30 skip)")
    return out


def main():
    print("== loading tapes ==")
    T = load_tapes()
    print(f"   total call trades: {T.height}   windows: {sorted(T['window'].unique().to_list())}")
    print("== loading + rebuilding context ==")
    ctx = load_context()
    T = T.sort("entry_date").join_asof(ctx.rename({"date": "entry_date"}), on="entry_date", strategy="backward")
    T = add_bins(T)
    base = T["loser"].mean()
    wins = sorted(T["window"].unique().to_list())
    report = {"n_trades": T.height, "base_loser_rate": round(base, 4), "windows": wins, "cohorts": {}, "robustness": {}}

    # single-axis EV scan for every candidate signal
    for ax in ["b_mw", "b_ce", "b_mcc", "b_bvel", "b_mwvel", "b_vix", "b_brd", "b_tier"]:
        report["cohorts"][ax] = cohort(T, [ax], base, f"single: {ax}")

    # ROBUSTNESS guard on the most-promising low-EV bins (per-window EV)
    report["robustness"]["mw_stress"] = robustness(T, "b_mw", ["mw1_severe_u20", "mw2_stress_20_35"], wins)
    report["robustness"]["mcc_neg"] = robustness(T, "b_mcc", ["mcc1_sev_un50", "mcc2_neg_50_15"], wins)
    report["robustness"]["bvel_dn"] = robustness(T, "b_bvel", ["bv1_crash_un12", "bv2_dn_12_4"], wins)
    report["robustness"]["crash_echo"] = robustness(T, "b_ce", ["ce3_mid_20_50", "ce4_hi_50p"], wins)

    # ORTHOGONALITY: where RXDD (VIX>=20) and F3F (breadth<40) are essentially OFF,
    # does any market-wave/breadth-momentum signal STILL isolate a low-EV DD cohort?
    print("\n\n########## ORTHOGONAL SLICE: vix<20 AND breadth>=40 (RXDD off, F3F ~off) ##########")
    O = T.filter((pl.col("vix") < 20) & (pl.col("breadth") >= 40))
    if O.height >= MIN_N:
        ob = O["loser"].mean()
        print(f"   slice trades: {O.height}  ({100*O.height/T.height:.1f}% of book)  slice base loser-rate={ob:.4f}  slice mean_pnl={O['option_pnl'].mean():+.4f}")
        report["ortho_slice_n"] = O.height
        report["ortho"] = {}
        for ax in ["b_mw", "b_ce", "b_mcc", "b_bvel", "b_mwvel"]:
            report["ortho"][ax] = cohort(O, [ax], ob, f"ORTHO {ax}")
    else:
        print("   (orthogonal slice too small)")

    (OUT / "mine_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\n== wrote {OUT/'mine_report.json'} ==")


if __name__ == "__main__":
    main()
