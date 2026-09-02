"""
Dead-hold cohort economics on the v70 Apex MC trade-tape (calls-only).

Answers the user's question directly: "I'm holding a position down 60% and
instead of buying the 88 on the board I'm forced to hold to -70% / day-15.
Isn't cutting it to redeploy better?"

The dead-hold cohort = trades that hit the SL trigger (option_pnl <= -0.40) and
were NOT cut; they rode to a -15% popout (dh_pop) or expired deep (dh_expiry).
That cohort IS the population of "bags down >=40%". For each we have the FINAL
option_pnl, so we can measure:

  1. Per-outcome economics (n, $-share, mean pnl).
  2. Dead-hold resolution split: recovery rate (dh_pop %) + mean final pnl,
     PER WINDOW (is recovery crash-concentrated?).
  3. HOLD-vs-CUT+REDEPLOY forward-multiple math at decision points c in
     {-0.40 trigger, -0.60 user}, vs redeploying into a fresh TOP-tier (88) call.
  4. Discriminator: within the dead-hold cohort, does any DECISION-TIME feature
     (entry_dd / regime / vix / breadth / score / concur) separate recoverers
     (dh_pop) from expirers (dh_expiry)? If none -> can't cut selectively ->
     cutting kills babies with the bathwater.

Usage:
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/dead_hold_realloc_v70/mine.py
"""
import sys, math, json
from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
TAPE_DIR = ROOT / ".cache" / "dd_ledger"
OUT = ROOT / "experiments" / "dead_hold_realloc_v70"
OUT.mkdir(parents=True, exist_ok=True)

SPREAD = 0.015          # SLIP_HARD: forced-exit half-spread (cutting pays this)
DH_OUTS = {"dh_pop", "dh_expiry", "dh_open"}


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
        print(f"!! no tape_*.parquet in {TAPE_DIR}"); sys.exit(2)
    frames = []
    for f in files:
        label = f.stem.replace("tape_", "")
        try:
            df = pl.read_parquet(f).with_columns(pl.lit(label).alias("window"))
        except Exception as e:
            print(f"   skip {f.name}: {e}"); continue
        frames.append(df)
    T = pl.concat(frames, how="diagonal_relaxed")
    T = _norm_date(T, "entry_date")
    if "side" in T.columns:
        T = T.filter(pl.col("side") == "call")
    T = T.with_columns(pl.col("outcome").is_in(list(DH_OUTS)).alias("is_dh"))
    return T


def load_context():
    try:
        from database.models.core import MarketBreadth, MarketRegime
        brd = {r.date: r.breadth_score for r in
               MarketBreadth.select(MarketBreadth.date, MarketBreadth.breadth_score)}
        reg = {}
        for r in MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier,
                                     MarketRegime.vix_close):
            reg[r.date] = (r.regime_multiplier, r.vix_close)
    except Exception as e:
        print(f"!! context load failed: {e}"); return None
    dates = sorted(set(brd) | set(reg))
    ctx = pl.DataFrame({
        "date": dates,
        "breadth": [brd.get(d) for d in dates],
        "reg_mult": [reg.get(d, (None, None))[0] for d in dates],
        "vix": [reg.get(d, (None, None))[1] for d in dates],
    })
    ctx = _norm_date(ctx, "date").sort("date")
    ctx = ctx.with_columns([pl.col(c).forward_fill() for c in ("breadth", "reg_mult", "vix")])
    return ctx


def attach(T, ctx):
    if ctx is None:
        return T
    return T.sort("entry_date").join_asof(
        ctx.rename({"date": "entry_date"}), on="entry_date", strategy="backward")


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main():
    print("== loading tapes ==")
    T = load_tapes()
    full = T.filter(pl.col("window") == "5y") if "5y" in T["window"].unique().to_list() else T
    print(f"   5y call trades: {full.height}   seeds: {full['seed'].n_unique()}")
    ctx = load_context()
    full = attach(full, ctx)
    report = {}

    # ---- 1. per-outcome economics (5y) ----
    section("1. PER-OUTCOME ECONOMICS (5y, calls)")
    g = full.group_by("outcome").agg(
        pl.len().alias("n"),
        pl.col("option_pnl").mean().alias("mean_pnl"),
        pl.col("option_pnl").median().alias("med_pnl"),
        (pl.col("premium_cost") * (1 + pl.col("option_pnl"))).sum().alias("ret_usd"),
        pl.col("premium_cost").sum().alias("cost_usd"),
    ).sort("n", descending=True)
    tot_n = full.height
    print(f"{'outcome':12s} {'n':>9s} {'share':>7s} {'mean_pnl':>9s} {'med_pnl':>8s} {'$mult':>7s}")
    o_rows = []
    for r in g.iter_rows(named=True):
        mult = (r["ret_usd"] / r["cost_usd"]) if r["cost_usd"] else 0.0
        print(f"{r['outcome']:12s} {r['n']:>9d} {r['n']/tot_n:>7.3f} "
              f"{r['mean_pnl']:>9.3f} {r['med_pnl']:>8.3f} {mult:>7.3f}")
        o_rows.append({"outcome": r["outcome"], "n": r["n"], "share": round(r["n"]/tot_n, 4),
                       "mean_pnl": round(r["mean_pnl"], 4), "dollar_mult": round(mult, 4)})
    report["per_outcome_5y"] = o_rows

    # ---- 2. fresh-signal EV by tier (the redeploy target) ----
    section("2. FRESH-SIGNAL EXPECTED RETURN BY TIER (the 'redeploy into the 88' target)")
    gt = full.group_by("tier").agg(
        pl.len().alias("n"),
        pl.col("option_pnl").mean().alias("mean_pnl"),
        (pl.col("option_pnl") > 0).mean().alias("win"),
    ).sort("mean_pnl", descending=True)
    e_fresh = {}
    print(f"{'tier':10s} {'n':>9s} {'mean_pnl':>9s} {'win':>6s}")
    for r in gt.iter_rows(named=True):
        e_fresh[r["tier"]] = r["mean_pnl"]
        print(f"{r['tier']:10s} {r['n']:>9d} {r['mean_pnl']:>9.3f} {r['win']:>6.3f}")
    report["fresh_ev_by_tier_5y"] = {k: round(v, 4) for k, v in e_fresh.items()}
    # the user's "88" = top tier (85-94); fall back to best available
    e88 = e_fresh.get("top", max(e_fresh.values()))
    print(f"\n   E[fresh top-tier 'the 88'] mean option_pnl = {e88:+.4f}")

    # ---- 3. dead-hold resolution + HOLD vs CUT forward math, ALL WINDOWS ----
    section("3. DEAD-HOLD RESOLUTION + HOLD-vs-CUT FORWARD MULTIPLE (per window)")
    print("  dh cohort = bags that hit the -40% trigger and were NOT cut.")
    print("  HOLD_mult(c) = E[1+final_pnl] / (1+c)        # what holding does to the residual")
    print("  CUT_mult(c)  = (1+c-0.015)/(1+c) * (1+E88)   # realize at c (pay spread), redeploy into the 88")
    print("  -> if HOLD_mult > CUT_mult, holding the bag beats cutting it for the 88.\n")
    print(f"  {'window':12s} {'dh_n':>7s} {'dh%':>6s} {'pop%':>6s} {'Efin':>7s} "
          f"{'HOLD-.4':>8s} {'CUT-.4':>7s} {'HOLD-.6':>8s} {'CUT-.6':>7s} {'verdict':>9s}")
    dh_by_win = {}
    for w in sorted(T["window"].unique().to_list()):
        sub = attach(T.filter(pl.col("window") == w), ctx)
        dh = sub.filter(pl.col("is_dh"))
        if dh.height == 0:
            continue
        # window-specific fresh top EV
        topw = sub.filter(pl.col("tier") == "top")
        e88_w = topw["option_pnl"].mean() if topw.height > 50 else e88
        dh_share = dh.height / sub.height
        pop = dh.filter(pl.col("outcome") == "dh_pop").height / dh.height
        efin = dh["option_pnl"].mean()
        rows = {}
        for c in (-0.40, -0.60):
            hold = (1 + efin) / (1 + c)
            cut = (1 + c - SPREAD) / (1 + c) * (1 + e88_w)
            rows[c] = (hold, cut)
        verdict = "HOLD" if rows[-0.40][0] > rows[-0.40][1] and rows[-0.60][0] > rows[-0.60][1] else \
                  ("CUT" if rows[-0.40][0] < rows[-0.40][1] and rows[-0.60][0] < rows[-0.60][1] else "MIXED")
        print(f"  {w:12s} {dh.height:>7d} {dh_share:>6.3f} {pop:>6.3f} {efin:>7.3f} "
              f"{rows[-0.40][0]:>8.3f} {rows[-0.40][1]:>7.3f} "
              f"{rows[-0.60][0]:>8.3f} {rows[-0.60][1]:>7.3f} {verdict:>9s}")
        dh_by_win[w] = {"dh_n": dh.height, "dh_share": round(dh_share, 4),
                        "pop_rate": round(pop, 4), "mean_final_pnl": round(efin, 4),
                        "e88_window": round(e88_w, 4),
                        "hold_m40": round(rows[-0.40][0], 4), "cut_m40": round(rows[-0.40][1], 4),
                        "hold_m60": round(rows[-0.60][0], 4), "cut_m60": round(rows[-0.60][1], 4),
                        "verdict": verdict}
    report["dead_hold_by_window"] = dh_by_win

    # ---- 4. DISCRIMINATOR: can decision-time features separate dh_pop from dh_expiry? (5y) ----
    section("4. DISCRIMINATOR: recovery rate (dh_pop / dh) by decision-time feature (5y)")
    print("  If recovery rate is FLAT across a feature -> that feature can't tell you")
    print("  which bag to cut -> selective cutting impossible.\n")
    dh = full.filter(pl.col("is_dh"))
    base_pop = dh.filter(pl.col("outcome") == "dh_pop").height / max(1, dh.height)
    print(f"  baseline dh recovery (pop) rate = {base_pop:.3f}  (dh_n={dh.height})\n")

    def disc(colexpr, label, name):
        d2 = dh.with_columns(colexpr.alias("_bin"))
        gg = d2.group_by("_bin").agg(
            pl.len().alias("n"),
            (pl.col("outcome") == "dh_pop").mean().alias("pop"),
            pl.col("option_pnl").mean().alias("efin"),
        ).filter(pl.col("n") >= 150).sort("_bin")
        print(f"  -- {label} --")
        rs = []
        for r in gg.iter_rows(named=True):
            print(f"     {str(r['_bin']):16s} n={r['n']:>7d} pop={r['pop']:.3f} Efin={r['efin']:+.3f}")
            rs.append({"bin": str(r["_bin"]), "n": r["n"], "pop": round(r["pop"], 4),
                       "efin": round(r["efin"], 4)})
        report.setdefault("discriminators", {})[name] = rs

    disc(pl.when(pl.col("entry_dd") < 0.10).then(pl.lit("dd_00_10"))
           .when(pl.col("entry_dd") < 0.20).then(pl.lit("dd_10_20"))
           .when(pl.col("entry_dd") < 0.35).then(pl.lit("dd_20_35"))
           .when(pl.col("entry_dd") < 0.55).then(pl.lit("dd_35_55"))
           .otherwise(pl.lit("dd_55p")), "entry_dd", "entry_dd")
    disc(pl.col("tier"), "tier", "tier")
    disc(pl.when(pl.col("score") >= 85).then(pl.lit("s_85p"))
           .when(pl.col("score") >= 80).then(pl.lit("s_80_84"))
           .when(pl.col("score") >= 75).then(pl.lit("s_75_79"))
           .otherwise(pl.lit("s_70_74")), "entry score", "score")
    if "vix" in dh.columns:
        disc(pl.when(pl.col("vix") >= 28).then(pl.lit("vix_28p"))
               .when(pl.col("vix") >= 20).then(pl.lit("vix_20_28"))
               .when(pl.col("vix") >= 15).then(pl.lit("vix_15_20"))
               .otherwise(pl.lit("vix_u15")), "VIX at entry", "vix")
    if "breadth" in dh.columns:
        disc(pl.when(pl.col("breadth") >= 55).then(pl.lit("brd_hi_55p"))
               .when(pl.col("breadth") >= 40).then(pl.lit("brd_mid_40_55"))
               .when(pl.col("breadth") >= 25).then(pl.lit("brd_lo_25_40"))
               .otherwise(pl.lit("brd_vlo_u25")), "breadth at entry", "breadth")
    if "reg_mult" in dh.columns:
        disc(pl.when(pl.col("reg_mult") >= 1.02).then(pl.lit("BULL"))
               .when(pl.col("reg_mult") >= 0.97).then(pl.lit("HEALTHY"))
               .when(pl.col("reg_mult") >= 0.85).then(pl.lit("NEUTRAL"))
               .otherwise(pl.lit("STRESS")), "regime at entry", "regime")
    disc(pl.when(pl.col("entry_open_calls") >= 12).then(pl.lit("concur_12p"))
           .when(pl.col("entry_open_calls") >= 9).then(pl.lit("concur_9_11"))
           .when(pl.col("entry_open_calls") >= 6).then(pl.lit("concur_6_8"))
           .otherwise(pl.lit("concur_u6")), "concurrent calls at entry", "concur")
    report["base_pop_rate_5y"] = round(base_pop, 4)

    (OUT / "mine_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\n== wrote {OUT/'mine_report.json'} ==")


if __name__ == "__main__":
    main()
