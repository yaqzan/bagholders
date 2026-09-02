"""
build_floor_sweep.py -- Entry liquidity floor cost/benefit measurement.

REPORT-ONLY. Pure file analysis (polars). No MySQL, no monte_carlo import,
no task queue. Reads:
  - B:\\polygon_derived\\ledger_v2\\ledger.parquet        (FF-1 real-contract ledger, header rows)
  - B:\\polygon_derived\\ledger_v2\\paths\\year=*\\*.parquet (FF-1 real daily contract paths)
  - B:\\polygon_derived\\liquidity_map\\signal_liquidity.parquet (FF-3' trailing-30d liquidity)
  - experiments\\tp_fill_fidelity_30dte\\out\\events_arm30.parquet        (matched TP-declared events)
  - experiments\\tp_fill_fidelity_30dte\\out\\declarations_arm30.parquet  (full kind census)

Writes CSVs to experiments\\liquidity_floor_2026_08\\out\\. See README.md for
methodology notes and caveats. ASCII-only console output.
"""
import glob
import polars as pl

pl.Config.set_tbl_cols(50)
pl.Config.set_tbl_width_chars(200)

ROOT = r"C:\Development\Trader"
OUT = ROOT + r"\experiments\liquidity_floor_2026_08\out"

LEDGER_PATH = r"B:\polygon_derived\ledger_v2\ledger.parquet"
PATHS_GLOB = r"B:\polygon_derived\ledger_v2\paths\year=*\*.parquet"
SIGLIQ_PATH = r"B:\polygon_derived\liquidity_map\signal_liquidity.parquet"
EVENTS_PATH = ROOT + r"\experiments\tp_fill_fidelity_30dte\out\events_arm30.parquet"
DECL_PATH = ROOT + r"\experiments\tp_fill_fidelity_30dte\out\declarations_arm30.parquet"

TIER_EDGES = [320, 1191, 3486, 14524]   # opt_vol_30d_atm quintile edges, t1=least liquid..t5=most (FF-2/FF-4 R6 read)
TIER_LABELS = ["t1", "t2", "t3", "t4", "t5"]

PREMIUM_FLOORS = [None, 0.25, 0.50, 1.00, 2.00]
VOLUME_FLOORS = [None, 5, 10, 25, 50]

TP_MULT = 1.30
SL_MULT = 0.30


def log(msg):
    print(msg, flush=True)


def tier_expr(col: str) -> pl.Expr:
    e = TIER_EDGES
    return (
        pl.when(pl.col(col).is_null()).then(None)
        .when(pl.col(col) <= e[0]).then(pl.lit("t1"))
        .when(pl.col(col) <= e[1]).then(pl.lit("t2"))
        .when(pl.col(col) <= e[2]).then(pl.lit("t3"))
        .when(pl.col(col) <= e[3]).then(pl.lit("t4"))
        .otherwise(pl.lit("t5"))
    )


def load_base():
    ledger = pl.read_parquet(LEDGER_PATH)
    kept = ledger.filter(pl.col("status") == "kept")
    log(f"[load] ledger.parquet total={ledger.height} kept={kept.height}")

    sigliq = pl.read_parquet(SIGLIQ_PATH).select(["symbol", "date", "opt_vol_30d_atm"])
    kept = kept.join(sigliq, left_on=["symbol", "entry_date"], right_on=["symbol", "date"], how="left")
    n_tier_null = kept["opt_vol_30d_atm"].null_count()
    log(f"[load] signal_liquidity join: opt_vol_30d_atm null={n_tier_null} of {kept.height}")
    kept = kept.with_columns(tier_expr("opt_vol_30d_atm").alias("tier"))

    parts = sorted(glob.glob(PATHS_GLOB))
    paths = pl.concat([pl.read_parquet(p) for p in parts])
    log(f"[load] paths: {paths.height} rows, {paths['signal_id'].n_unique()} distinct signal_id, "
        f"partitions={len(parts)}")
    last_close = (
        paths.sort("date")
        .group_by("signal_id")
        .agg(pl.col("close").last().alias("last_path_close"), pl.col("date").last().alias("last_path_date"))
    )
    kept = kept.join(last_close, on="signal_id", how="left")

    # --- realized outcome (close-only touch, real prints only -- ARM-30 convention TP+30/SL-70) ---
    tp_first = pl.col("tp_touch_date").is_not_null() & (
        pl.col("sl_touch_date").is_null() | (pl.col("tp_touch_date") <= pl.col("sl_touch_date"))
    )
    sl_first = pl.col("sl_touch_date").is_not_null() & (
        pl.col("tp_touch_date").is_null() | (pl.col("sl_touch_date") < pl.col("tp_touch_date"))
    )
    fallback_mark = pl.when(pl.col("mark_cd27").is_not_null()).then(pl.col("mark_cd27")).otherwise(pl.col("last_path_close"))
    outcome_source = pl.when(tp_first).then(pl.lit("tp_touch")).when(sl_first).then(pl.lit("sl_touch")).when(
        pl.col("mark_cd27").is_not_null()
    ).then(pl.lit("mark_cd27_fallback")).when(pl.col("last_path_close").is_not_null()).then(
        pl.lit("last_path_close_fallback")
    ).otherwise(pl.lit("none"))
    outcome_mult = pl.when(tp_first).then(pl.lit(TP_MULT)).when(sl_first).then(pl.lit(SL_MULT)).otherwise(
        fallback_mark / pl.col("entry_premium")
    )
    kept = kept.with_columns([
        tp_first.alias("_tp_first"),
        sl_first.alias("_sl_first"),
        outcome_source.alias("outcome_source"),
        outcome_mult.alias("outcome_mult"),
    ]).with_columns((pl.col("outcome_mult") - 1.0).alias("outcome_pct"))

    n_ev_null = kept["outcome_mult"].null_count()
    log(f"[load] outcome_mult computed; null (no touch, no mark_cd27, no path close) = {n_ev_null} of {kept.height}")
    log("[load] outcome_source distribution:")
    log(str(kept.group_by("outcome_source").agg(pl.len()).sort("outcome_source")))

    # equal-dollar contract weight (proportional to 1/entry_premium under equal-dollar sizing)
    kept = kept.with_columns((1.0 / pl.col("entry_premium")).alias("contract_weight"))

    return kept


def load_events_decl():
    events = pl.read_parquet(EVENTS_PATH)
    decl = pl.read_parquet(DECL_PATH)
    return events, decl


def supply_and_tier(kept: pl.DataFrame, prem_floor, vol_floor) -> dict:
    n_total = kept.height
    mask = pl.lit(True)
    if prem_floor is not None:
        mask = mask & (pl.col("entry_premium") >= prem_floor)
    if vol_floor is not None:
        mask = mask & (pl.col("entry_volume") >= vol_floor)
    surv = kept.filter(mask)
    n_surv = surv.height
    row = {
        "premium_floor": prem_floor if prem_floor is not None else "none",
        "volume_floor": vol_floor if vol_floor is not None else "none",
        "n_total": n_total,
        "n_survivors": n_surv,
        "supply_cost_pct": round(100.0 * (1 - n_surv / n_total), 2),
    }
    # by-tier supply cost
    tier_counts_total = kept.group_by("tier").agg(pl.len().alias("n")).to_dict(as_series=False)
    tot_by_tier = dict(zip(tier_counts_total["tier"], tier_counts_total["n"]))
    surv_counts = surv.group_by("tier").agg(pl.len().alias("n")).to_dict(as_series=False)
    surv_by_tier = dict(zip(surv_counts["tier"], surv_counts["n"]))
    for t in TIER_LABELS:
        tot_t = tot_by_tier.get(t, 0)
        surv_t = surv_by_tier.get(t, 0)
        row[f"{t}_n_total"] = tot_t
        row[f"{t}_n_survivors"] = surv_t
        row[f"{t}_supply_cost_pct"] = round(100.0 * (1 - surv_t / tot_t), 2) if tot_t else None

    # realized EV: survivors vs full
    full_ev = kept["outcome_pct"].drop_nulls()
    surv_ev = surv["outcome_pct"].drop_nulls()
    row["ev_mean_full_pct"] = round(100.0 * float(full_ev.mean()), 3) if full_ev.len() else None
    row["ev_median_full_pct"] = round(100.0 * float(full_ev.median()), 3) if full_ev.len() else None
    row["ev_mean_survivors_pct"] = round(100.0 * float(surv_ev.mean()), 3) if surv_ev.len() else None
    row["ev_median_survivors_pct"] = round(100.0 * float(surv_ev.median()), 3) if surv_ev.len() else None
    row["n_ev_full"] = full_ev.len()
    row["n_ev_survivors"] = surv_ev.len()

    # equal-dollar contract weight surviving
    total_weight = kept["contract_weight"].sum()
    surv_weight = surv["contract_weight"].sum()
    row["weight_share_surviving_pct"] = round(100.0 * surv_weight / total_weight, 2)
    row["count_share_surviving_pct"] = round(100.0 * n_surv / n_total, 2)

    # capacity sanity at clip=5, clip=20 (fraction of SURVIVORS whose clip would exceed 25% of entry_volume)
    for clip in (5, 20):
        if n_surv:
            thresh = clip / 0.25  # entry_volume below this -> clip/entry_volume > 0.25
            frac = surv.filter(pl.col("entry_volume") < thresh).height / n_surv
            row[f"capacity_exceed25pct_clip{clip}_frac"] = round(frac, 4)
        else:
            row[f"capacity_exceed25pct_clip{clip}_frac"] = None

    return row, surv


def fill_fidelity_on_survivors(surv: pl.DataFrame, events: pl.DataFrame) -> dict:
    surv_ids = surv["signal_id"].to_list()
    ev_surv = events.filter(pl.col("signal_id").is_in(surv_ids))
    n_declared = ev_surv.height
    joinable = ev_surv.filter(pl.col("bucket") != "unjoinable")
    n_joinable = joinable.height
    n_no_print = ev_surv.filter(pl.col("bucket") == "miss_no_print").height
    n_never_fill = joinable.filter(pl.col("timing_bucket") == "never_fill").height
    n_fill = joinable.filter(pl.col("bucket") == "fill").height
    return {
        "n_tp_declared_survivors": n_declared,
        "n_joinable_survivors": n_joinable,
        "fill_rate_highbasis_survivors_pct": round(100.0 * n_fill / n_joinable, 2) if n_joinable else None,
        "no_print_rate_survivors_pct": round(100.0 * n_no_print / n_declared, 2) if n_declared else None,
        "never_fill_rate_survivors_pct": round(100.0 * n_never_fill / n_joinable, 2) if n_joinable else None,
    }


def run_sweep(kept, events, prem_axis, vol_axis, label):
    rows = []
    for pf in prem_axis:
        for vf in vol_axis:
            row, surv = supply_and_tier(kept, pf, vf)
            row.update(fill_fidelity_on_survivors(surv, events))
            rows.append(row)
    df = pl.DataFrame(rows)
    out_path = f"{OUT}\\{label}.csv"
    df.write_csv(out_path)
    log(f"[write] {out_path} ({df.height} rows)")
    return df


def cost_audit_crosscheck(kept: pl.DataFrame):
    """Reproduce COST_AUDIT.md's sub-$1 / sub-$2 count-share vs equal-dollar
    contract-weight-share stat on THIS ledger population, as a consistency
    check (NOT identical source data -- COST_AUDIT used the older 3,371-path
    .cache/polygon_real_premium/ ledger; this uses ledger_v2, N=4,403 kept)."""
    total_weight = kept["contract_weight"].sum()
    n_total = kept.height
    out = []
    for thresh in (1.0, 2.0):
        sub = kept.filter(pl.col("entry_premium") < thresh)
        n_sub = sub.height
        w_sub = sub["contract_weight"].sum()
        out.append({
            "premium_below": thresh,
            "count_share_pct": round(100.0 * n_sub / n_total, 1),
            "weight_share_pct": round(100.0 * w_sub / total_weight, 1),
        })
    df = pl.DataFrame(out)
    path = f"{OUT}\\cost_audit_crosscheck.csv"
    df.write_csv(path)
    log(f"[write] {path}")
    log(str(df))
    return df


def excluded_mass_attribution(kept, events, decl, prem_floor=0.50, vol_floor=10):
    mask = (pl.col("entry_premium") >= prem_floor) & (pl.col("entry_volume") >= vol_floor)
    survivors = kept.filter(mask)
    excluded = kept.filter(~mask)
    log(f"[excluded_mass] floor premium>={prem_floor} volume>={vol_floor}: "
        f"excluded={excluded.height} survivors={survivors.height} total={kept.height}")

    excl_ids = excluded["signal_id"].to_list()
    surv_ids = survivors["signal_id"].to_list()

    # what kind of declared outcome did the excluded population get, per the engine's own underlying-sigma walk
    decl_excl = decl.filter(pl.col("signal_id").is_in(excl_ids))
    kind_counts = decl_excl.group_by("kind").agg(pl.len().alias("n")).sort("kind")

    # of the TP-declared excluded events, what happened for real (bucket / timing_bucket)
    ev_excl = events.filter(pl.col("signal_id").is_in(excl_ids))
    ev_surv = events.filter(pl.col("signal_id").is_in(surv_ids))

    bucket_excl = ev_excl.group_by("bucket").agg(pl.len().alias("n")).sort("bucket")
    timing_excl = ev_excl.filter(pl.col("bucket") != "unjoinable").group_by("timing_bucket").agg(pl.len().alias("n")).sort("timing_bucket")

    # realized EV of excluded (all-kind) vs survivors (all-kind), and TP-declared-only subsets
    def ev_stats(df, col="outcome_pct"):
        s = df[col].drop_nulls()
        return (round(100 * float(s.mean()), 3) if s.len() else None,
                round(100 * float(s.median()), 3) if s.len() else None, s.len())

    excl_mean, excl_med, excl_n = ev_stats(excluded)
    surv_mean, surv_med, surv_n = ev_stats(survivors)

    excl_tp_ledger = excluded.filter(pl.col("signal_id").is_in(ev_excl["signal_id"].to_list()))
    surv_tp_ledger = survivors.filter(pl.col("signal_id").is_in(ev_surv["signal_id"].to_list()))
    excl_tp_mean, excl_tp_med, excl_tp_n = ev_stats(excl_tp_ledger)
    surv_tp_mean, surv_tp_med, surv_tp_n = ev_stats(surv_tp_ledger)

    # write attribution CSV: one row per (population, breakdown_type, value, n)
    rows = []
    for _, r in enumerate(kind_counts.iter_rows(named=True)):
        rows.append({"population": "excluded", "breakdown": "declared_kind", "value": r["kind"], "n": r["n"],
                     "pct_of_population": round(100.0 * r["n"] / excluded.height, 2)})
    for _, r in enumerate(bucket_excl.iter_rows(named=True)):
        rows.append({"population": "excluded_tp_declared", "breakdown": "fill_bucket", "value": r["bucket"], "n": r["n"],
                     "pct_of_population": round(100.0 * r["n"] / ev_excl.height, 2) if ev_excl.height else None})
    for _, r in enumerate(timing_excl.iter_rows(named=True)):
        joinable_n = ev_excl.filter(pl.col("bucket") != "unjoinable").height
        rows.append({"population": "excluded_tp_declared_joinable", "breakdown": "timing_bucket", "value": r["timing_bucket"], "n": r["n"],
                     "pct_of_population": round(100.0 * r["n"] / joinable_n, 2) if joinable_n else None})
    rows.append({"population": "excluded_all_kept", "breakdown": "realized_ev_mean_pct", "value": excl_mean, "n": excl_n, "pct_of_population": None})
    rows.append({"population": "excluded_all_kept", "breakdown": "realized_ev_median_pct", "value": excl_med, "n": excl_n, "pct_of_population": None})
    rows.append({"population": "survivors_all_kept", "breakdown": "realized_ev_mean_pct", "value": surv_mean, "n": surv_n, "pct_of_population": None})
    rows.append({"population": "survivors_all_kept", "breakdown": "realized_ev_median_pct", "value": surv_med, "n": surv_n, "pct_of_population": None})
    rows.append({"population": "excluded_tp_declared_only", "breakdown": "realized_ev_mean_pct", "value": excl_tp_mean, "n": excl_tp_n, "pct_of_population": None})
    rows.append({"population": "excluded_tp_declared_only", "breakdown": "realized_ev_median_pct", "value": excl_tp_med, "n": excl_tp_n, "pct_of_population": None})
    rows.append({"population": "survivors_tp_declared_only", "breakdown": "realized_ev_mean_pct", "value": surv_tp_mean, "n": surv_tp_n, "pct_of_population": None})
    rows.append({"population": "survivors_tp_declared_only", "breakdown": "realized_ev_median_pct", "value": surv_tp_med, "n": surv_tp_n, "pct_of_population": None})

    df = pl.DataFrame(rows)
    path = f"{OUT}\\excluded_mass_attribution.csv"
    df.write_csv(path)
    log(f"[write] {path} ({df.height} rows)")
    log(str(df))
    return df


def data_quality_checks(kept, events, decl):
    checks = []
    checks.append(("duplicate signal_id in kept ledger", kept.group_by("signal_id").agg(pl.len().alias("n")).filter(pl.col("n") > 1).height))
    checks.append(("duplicate (ticker, entry_date) in kept ledger", kept.group_by(["ticker", "entry_date"]).agg(pl.len().alias("n")).filter(pl.col("n") > 1).height))
    checks.append(("entry_premium <= 0", kept.filter(pl.col("entry_premium") <= 0).height))
    checks.append(("entry_volume <= 0 or null", kept.filter((pl.col("entry_volume").is_null()) | (pl.col("entry_volume") <= 0)).height))
    checks.append(("expiry < entry_date", kept.filter(pl.col("expiry") < pl.col("entry_date")).height))
    checks.append(("dte_actual outside [18,50]", kept.filter((pl.col("dte_actual") < 18) | (pl.col("dte_actual") > 50)).height))
    checks.append(("tier null (no opt_vol_30d_atm)", kept.filter(pl.col("tier").is_null()).height))
    checks.append(("outcome_mult null (no real terminal mark at all)", kept.filter(pl.col("outcome_mult").is_null()).height))
    checks.append(("both tp_touch_date and sl_touch_date null", kept.filter(pl.col("tp_touch_date").is_null() & pl.col("sl_touch_date").is_null()).height))
    checks.append(("tp_touch_date == sl_touch_date (same-day both touch, tie-break to TP)", kept.filter(pl.col("tp_touch_date").is_not_null() & (pl.col("tp_touch_date") == pl.col("sl_touch_date"))).height))
    checks.append(("events rows with bucket=unjoinable", events.filter(pl.col("bucket") == "unjoinable").height))
    checks.append(("min entry_date", str(kept["entry_date"].min())))
    checks.append(("max entry_date", str(kept["entry_date"].max())))
    checks.append(("min entry_premium", float(kept["entry_premium"].min())))
    checks.append(("max entry_premium", float(kept["entry_premium"].max())))
    checks.append(("min entry_volume", float(kept["entry_volume"].min())))
    checks.append(("max entry_volume", float(kept["entry_volume"].max())))
    for name, val in checks:
        log(f"[dq] {name}: {val}")
    df = pl.DataFrame({"check": [c[0] for c in checks], "value": [str(c[1]) for c in checks]})
    path = f"{OUT}\\data_quality_checks.csv"
    df.write_csv(path)
    log(f"[write] {path}")
    return df


def main():
    kept = load_base()
    events, decl = load_events_decl()

    log("\n=== tier distribution (kept, n=%d) ===" % kept.height)
    log(str(kept.group_by("tier").agg(pl.len()).sort("tier")))

    log("\n=== DATA QUALITY CHECKS ===")
    data_quality_checks(kept, events, decl)

    log("\n=== COST_AUDIT cross-check (equal-dollar weight, this ledger) ===")
    cost_audit_crosscheck(kept)

    log("\n=== PREMIUM AXIS SWEEP (volume floor = none) ===")
    prem_df = run_sweep(kept, events, PREMIUM_FLOORS, [None], "premium_axis")
    log(str(prem_df.select(["premium_floor", "volume_floor", "n_survivors", "supply_cost_pct",
                             "never_fill_rate_survivors_pct", "no_print_rate_survivors_pct",
                             "ev_mean_survivors_pct", "ev_median_survivors_pct", "weight_share_surviving_pct"])))

    log("\n=== VOLUME AXIS SWEEP (premium floor = none) ===")
    vol_df = run_sweep(kept, events, [None], VOLUME_FLOORS, "volume_axis")
    log(str(vol_df.select(["premium_floor", "volume_floor", "n_survivors", "supply_cost_pct",
                            "never_fill_rate_survivors_pct", "no_print_rate_survivors_pct",
                            "ev_mean_survivors_pct", "ev_median_survivors_pct", "weight_share_surviving_pct"])))

    log("\n=== JOINT COARSE GRID ===")
    joint_df = run_sweep(kept, events, PREMIUM_FLOORS, VOLUME_FLOORS, "joint_grid")
    log(str(joint_df.select(["premium_floor", "volume_floor", "n_survivors", "supply_cost_pct",
                              "never_fill_rate_survivors_pct", "weight_share_surviving_pct",
                              "capacity_exceed25pct_clip5_frac", "capacity_exceed25pct_clip20_frac"])))

    log("\n=== EXCLUDED-MASS ATTRIBUTION (floor premium>=0.50, volume>=10) ===")
    excluded_mass_attribution(kept, events, decl, prem_floor=0.50, vol_floor=10)

    log("\nDONE.")


if __name__ == "__main__":
    main()
