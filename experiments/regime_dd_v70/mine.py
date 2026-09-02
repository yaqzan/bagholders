"""
Regime x DD pattern mining on the v70 Apex MC trade-tape (calls-only).

Overnight Stage-3 research (2026-06-04). Mines:
  (A) high-DD cohorts: loser-rate lift/z + episode DD-concentration over
      entry_dd x breadth x regime x concur_calls x tier
  (B) explosion winners: rank seeds by episodes.final_value, compare the
      entry-context / tier-mix / monster-trade composition of top-decile
      runs vs the rest.

Joins market context (breadth_score / regime_composite / regime_multiplier /
vix_close) by entry_date from MySQL (light read). No tape rebuild, no
miss-ledger dependency. Runs foreground (pure parquet/polars analysis).

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/regime_dd_v70/mine.py
"""
import sys, math, json
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT = ROOT / "experiments" / "regime_dd_v70"
OUT.mkdir(parents=True, exist_ok=True)

MIN_N = 200          # cohort floor for reporting
WINDOWS_SKIP = set() # mine everything present


def _norm_date(df: pl.DataFrame, col: str) -> pl.DataFrame:
    if col not in df.columns:
        return df
    dt = df.schema[col]
    if dt == pl.Date:
        return df
    if dt == pl.Datetime:
        return df.with_columns(pl.col(col).cast(pl.Date))
    if dt == pl.Utf8:
        return df.with_columns(
            pl.col(col).str.strptime(pl.Date, "%Y-%m-%d", strict=False)
        )
    return df.with_columns(pl.col(col).cast(pl.Date, strict=False))


def load_tapes() -> pl.DataFrame:
    files = sorted(TAPE_DIR.glob("tape_*.parquet"))
    if not files:
        print(f"!! no tape_*.parquet in {TAPE_DIR}")
        sys.exit(2)
    frames = []
    for f in files:
        label = f.stem.replace("tape_", "")
        if label in WINDOWS_SKIP:
            continue
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
    T = _norm_date(T, "exit_date")
    # Apex = calls only
    if "side" in T.columns:
        T = T.filter(pl.col("side") == "call")
    T = T.with_columns(
        (pl.col("premium_cost") * pl.col("option_pnl")).alias("pnl_dollars"),
        (pl.col("option_pnl") < 0).alias("loser"),
    )
    return T


def load_context() -> pl.DataFrame | None:
    """date -> breadth_score / regime_composite / regime_multiplier / vix_close."""
    try:
        from database.models.core import MarketBreadth, MarketRegime
    except Exception as e:
        print(f"!! context load failed (DB import): {e}\n   -> mining without breadth/regime axes")
        return None
    try:
        brd = {r.date: r.breadth_score for r in
               MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)}
        reg = {}
        for r in MarketRegime.select(MarketRegime.date, MarketRegime.regime_composite,
                                     MarketRegime.regime_multiplier, MarketRegime.vix_close):
            reg[r.date] = (r.regime_composite, r.regime_multiplier, r.vix_close)
    except Exception as e:
        print(f"!! context query failed: {e}\n   -> mining without breadth/regime axes")
        return None
    dates = sorted(set(brd) | set(reg))
    ctx = pl.DataFrame({
        "date": dates,
        "breadth": [brd.get(d) for d in dates],
        "reg_comp": [reg.get(d, (None, None, None))[0] for d in dates],
        "reg_mult": [reg.get(d, (None, None, None))[1] for d in dates],
        "vix": [reg.get(d, (None, None, None))[2] for d in dates],
    })
    ctx = _norm_date(ctx, "date").sort("date")
    # forward-fill so on-or-before semantics match the engine's regime_on_or_before
    ctx = ctx.with_columns([pl.col(c).forward_fill() for c in ("breadth", "reg_comp", "reg_mult", "vix")])
    print(f"   context rows={ctx.height}  ({ctx['date'].min()} .. {ctx['date'].max()})")
    return ctx


def attach_context(T: pl.DataFrame, ctx: pl.DataFrame | None) -> pl.DataFrame:
    if ctx is None:
        return T
    T = T.sort("entry_date").join_asof(
        ctx.rename({"date": "entry_date"}), on="entry_date", strategy="backward"
    )
    return T


def add_bins(T: pl.DataFrame) -> pl.DataFrame:
    exprs = [
        pl.when(pl.col("entry_dd") < 0.10).then(pl.lit("dd_00_10"))
          .when(pl.col("entry_dd") < 0.20).then(pl.lit("dd_10_20"))
          .when(pl.col("entry_dd") < 0.35).then(pl.lit("dd_20_35"))
          .when(pl.col("entry_dd") < 0.55).then(pl.lit("dd_35_55"))
          .otherwise(pl.lit("dd_55p")).alias("b_dd"),
        pl.when(pl.col("entry_open_calls") >= 10).then(pl.lit("concur_10p"))
          .when(pl.col("entry_open_calls") >= 7).then(pl.lit("concur_7_9"))
          .when(pl.col("entry_open_calls") >= 4).then(pl.lit("concur_4_6"))
          .otherwise(pl.lit("concur_0_3")).alias("b_concur"),
        pl.col("tier").alias("b_tier"),
    ]
    if "breadth" in T.columns:
        exprs.append(
            pl.when(pl.col("breadth") >= 70).then(pl.lit("brd_vhi_70p"))
              .when(pl.col("breadth") >= 55).then(pl.lit("brd_hi_55_70"))
              .when(pl.col("breadth") >= 40).then(pl.lit("brd_mid_40_55"))
              .when(pl.col("breadth") >= 25).then(pl.lit("brd_lo_25_40"))
              .otherwise(pl.lit("brd_vlo_u25")).alias("b_brd"))
    if "reg_mult" in T.columns:
        exprs.append(
            pl.when(pl.col("reg_mult") >= 1.02).then(pl.lit("reg_BULL"))
              .when(pl.col("reg_mult") >= 0.97).then(pl.lit("reg_HEALTHY"))
              .when(pl.col("reg_mult") >= 0.85).then(pl.lit("reg_NEUTRAL"))
              .otherwise(pl.lit("reg_STRESS")).alias("b_reg"))
    if "vix" in T.columns:
        exprs.append(
            pl.when(pl.col("vix") >= 28).then(pl.lit("vix_28p"))
              .when(pl.col("vix") >= 20).then(pl.lit("vix_20_28"))
              .when(pl.col("vix") >= 15).then(pl.lit("vix_15_20"))
              .otherwise(pl.lit("vix_u15")).alias("b_vix"))
    return T.with_columns(exprs)


def cohort_report(T: pl.DataFrame, axes: list[str], base_rate: float, title: str) -> list[dict]:
    rows = []
    g = T.group_by(axes).agg(
        pl.len().alias("n"),
        pl.col("loser").mean().alias("rate"),
        pl.col("pnl_dollars").filter(pl.col("loser")).abs().sum().alias("loss_usd"),
        pl.col("pnl_dollars").sum().alias("net_usd"),
        pl.col("option_pnl").mean().alias("mean_pnl"),
    ).filter(pl.col("n") >= MIN_N)
    total_loss = T.filter(pl.col("loser"))["pnl_dollars"].abs().sum() or 1.0
    total_n = T.height
    for r in g.iter_rows(named=True):
        n = r["n"]; rate = r["rate"]
        z = (rate - base_rate) / math.sqrt(base_rate * (1 - base_rate) / n) if n else 0.0
        dd_share = (r["loss_usd"] or 0.0) / total_loss
        n_share = n / total_n
        conc = dd_share / n_share if n_share else 0.0
        rows.append({
            "cohort": " & ".join(str(r[a]) for a in axes),
            "n": n, "rate": round(rate, 4), "lift": round(rate / base_rate, 2),
            "z": round(z, 1), "dd_conc": round(conc, 2),
            "mean_pnl": round(r["mean_pnl"], 4), "dd_share": round(dd_share, 4),
        })
    rows.sort(key=lambda x: -abs(x["z"]))
    print(f"\n### {title}  (baseline loser-rate={base_rate:.4f}, N={total_n})")
    print(f"{'cohort':42s} {'n':>8s} {'rate':>6s} {'lift':>5s} {'z':>6s} {'conc':>6s} {'mpnl':>7s}")
    for r in rows[:18]:
        print(f"{r['cohort']:42s} {r['n']:>8d} {r['rate']:>6.3f} {r['lift']:>5.2f} "
              f"{r['z']:>6.1f} {r['dd_conc']:>6.2f} {r['mean_pnl']:>7.3f}")
    return rows


def explosion_report(T: pl.DataFrame) -> dict:
    """Rank seeds by episodes.final_value; compare top-decile run composition vs rest."""
    epis = sorted(TAPE_DIR.glob("episodes_*.parquet"))
    if not epis:
        print("\n(no episodes_*.parquet yet — explosion analysis skipped)")
        return {}
    eframes = []
    for f in epis:
        label = f.stem.replace("episodes_", "")
        try:
            df = pl.read_parquet(f).with_columns(pl.lit(label).alias("window"))
        except Exception:
            continue
        eframes.append(df)
    E = pl.concat(eframes, how="diagonal_relaxed")
    # one row per (window, seed): the rank-0 episode carries final_value
    fv_col = "final_value" if "final_value" in E.columns else None
    if fv_col is None:
        print("\n(episodes has no final_value column — explosion analysis skipped)")
        return {}
    seed_fin = (E.filter(pl.col("rank") == 0) if "rank" in E.columns else E) \
        .group_by(["window", "seed"]).agg(pl.col(fv_col).first().alias("final_value"))
    out = {}
    print("\n=== EXPLOSION: top-decile vs rest run composition (per window) ===")
    for w in sorted(seed_fin["window"].unique().to_list()):
        sf = seed_fin.filter(pl.col("window") == w)
        if sf.height < 30:
            continue
        cut = sf["final_value"].quantile(0.90)
        top_seeds = set(sf.filter(pl.col("final_value") >= cut)["seed"].to_list())
        tw = T.filter(pl.col("window") == w)
        if tw.height == 0:
            continue
        tw = tw.with_columns(pl.col("seed").is_in(list(top_seeds)).alias("is_top"))

        def comp(sub):
            if sub.height == 0:
                return {}
            d = {
                "n_trades_per_seed": round(sub.height / max(1, sub["seed"].n_unique()), 1),
                "mean_pnl": round(sub["option_pnl"].mean(), 4),
                "win_rate": round((sub["option_pnl"] > 0).mean(), 3),
                "monster_share": round((sub["option_pnl"] >= 2.0).mean(), 4),  # >=+200% (dead-hold runners)
                "mean_entry_dd": round(sub["entry_dd"].mean(), 3),
                "mean_concur": round(sub["entry_open_calls"].mean(), 2),
            }
            if "breadth" in sub.columns:
                d["mean_breadth_at_entry"] = round(sub["breadth"].drop_nulls().mean() or 0.0, 1)
            if "vix" in sub.columns:
                d["mean_vix_at_entry"] = round(sub["vix"].drop_nulls().mean() or 0.0, 1)
            # tier mix
            tm = sub.group_by("tier").agg(pl.len().alias("c"))
            tot = tm["c"].sum()
            d["tier_mix"] = {r["tier"]: round(r["c"] / tot, 3) for r in tm.iter_rows(named=True)}
            return d

        top = comp(tw.filter(pl.col("is_top")))
        rest = comp(tw.filter(~pl.col("is_top")))
        out[w] = {"final_p90": float(cut), "top": top, "rest": rest}
        print(f"\n[{w}]  final_value p90 cut={cut:.3e}")
        for k in ("win_rate", "mean_pnl", "monster_share", "mean_entry_dd", "mean_concur",
                  "mean_breadth_at_entry", "mean_vix_at_entry", "n_trades_per_seed"):
            if k in top:
                print(f"   {k:22s} top={top[k]:<10} rest={rest.get(k)}")
        print(f"   tier_mix  top={top.get('tier_mix')}  rest={rest.get('tier_mix')}")
    return out


def main():
    print("== loading tapes ==")
    T = load_tapes()
    print(f"   total call trades: {T.height}   windows: {sorted(T['window'].unique().to_list())}")
    print("== loading market context ==")
    ctx = load_context()
    T = attach_context(T, ctx)
    T = add_bins(T)

    base = T["loser"].mean()
    report = {"n_trades": T.height, "base_loser_rate": round(base, 4),
              "windows": sorted(T["window"].unique().to_list()), "cohorts": {}}

    # single-axis
    for ax in ["b_dd", "b_brd", "b_reg", "b_vix", "b_concur", "b_tier"]:
        if ax in T.columns:
            report["cohorts"][ax] = cohort_report(T, [ax], base, f"single: {ax}")
    # two-axis (the regime x DD interaction is the headline)
    for axes in [["b_dd", "b_brd"], ["b_dd", "b_reg"], ["b_dd", "b_vix"],
                 ["b_dd", "b_concur"], ["b_tier", "b_dd"], ["b_dd", "b_brd"]]:
        if all(a in T.columns for a in axes):
            key = "x".join(axes)
            report["cohorts"][key] = cohort_report(T, axes, base, f"two-axis: {key}")
    # focused: the candidate pocket the shipped soft-band misses
    if "b_brd" in T.columns:
        pocket = T.filter((pl.col("entry_dd") >= 0.20) & (pl.col("entry_dd") < 0.35))
        if pocket.height >= MIN_N:
            cohort_report(pocket, ["b_brd"], base, "POCKET entry_dd[0.20,0.35] x breadth (soft-band gap)")

    # ROBUSTNESS: is the mid-breadth x DD pocket universal or crash-only?
    # Print loser-rate + mean option pnl for the candidate bad pocket, per window.
    if "breadth" in T.columns:
        print("\n=== ROBUSTNESS: candidate bad pocket loser-rate BY WINDOW ===")
        print("  pocket = entry_dd in [0.20,0.55) AND breadth in [40,55)")
        report["pocket_by_window"] = {}
        for w in sorted(T["window"].unique().to_list()):
            sub = T.filter(pl.col("window") == w)
            b = sub["loser"].mean()
            pk = sub.filter((pl.col("entry_dd") >= 0.20) & (pl.col("entry_dd") < 0.55)
                            & (pl.col("breadth") >= 40) & (pl.col("breadth") < 55))
            if pk.height > 0:
                pr = pk["loser"].mean(); pm = pk["option_pnl"].mean()
                # benign reference within same window: shallow dd (<0.20) any breadth
                ref = sub.filter(pl.col("entry_dd") < 0.20)
                rr = ref["loser"].mean() if ref.height else float("nan")
                print(f"  {w:12s} base={b:.3f} shallow_ref={rr:.3f} | pocket n={pk.height:6d} "
                      f"rate={pr:.3f} lift={pr/b:.2f} mpnl={pm:+.3f}")
                report["pocket_by_window"][w] = {
                    "base": round(b, 4), "pocket_n": pk.height,
                    "pocket_rate": round(pr, 4), "pocket_lift": round(pr / b, 2),
                    "pocket_mean_pnl": round(pm, 4)}

    expl = explosion_report(T)
    report["explosion"] = expl

    (OUT / "mine_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\n== wrote {OUT/'mine_report.json'} ==")


if __name__ == "__main__":
    main()
