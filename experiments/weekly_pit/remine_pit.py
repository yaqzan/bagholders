"""Phase 3 — re-mine for REAL (non-look-ahead) weekly alpha on PIT features.

Joins the point-in-time weekly reconstruction (.cache/weekly_pit/pit_weekly_5y.parquet)
to the option-augmented ledger (.cache/rqc_v60/ledger_5y.parquet), then:

  (A) DAY-OF-WEEK FLATNESS TEST — the look-ahead detector.
      The recalc ledger's w_mom/w_adj are CURRENT-COMPLETE-week (look-ahead). The
      70-74 w_mom in [5,8) cohort optTP15 ran Mon 68 -> Fri 50 (the edge vanishes
      as the week completes => look-ahead). We recompute the same dow split on:
        - ledger w_mom (look-ahead, should still show Mon-spike/Fri-collapse)
        - pit_w_mom   (PARTIAL PIT, should be FLAT if look-ahead is removed)
        - comp_w_mom  (COMPLETED PIT = live convention, should be FLAT)
      Flatness on PIT == look-ahead removed.

  (B) RE-MINE the 70-74 PROMOTE / 75-79 DEMOTE boundary on PIT features at
      OPTION-TP (opt15). A cohort beating the 75-79 marginal (~61.35% optTP)
      with z>=3 on a PIT (point-in-time-safe) predicate would be REAL weekly
      alpha. Prior evidence says it is null (the edge was look-ahead).

Holdout-locked: ledger and PIT parquet are both materialized <= CUTOFF.
"""
import os
import sys
import json
import math

import polars as pl

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = os.path.join(ROOT, ".cache", "rqc_v60", "ledger_5y.parquet")
PIT = os.path.join(ROOT, ".cache", "weekly_pit", "pit_weekly_5y.parquet")
OUT = os.path.join(ROOT, ".cache", "weekly_pit", "remine_5y.json")
CUTOFF = "2026-05-15"


def z(p, n, p0):
    if n <= 0 or p0 <= 0 or p0 >= 1:
        return 0.0
    se = math.sqrt(p0 * (1 - p0) / n)
    return round((p - p0) / se, 2) if se > 0 else 0.0


def _load():
    led = pl.read_parquet(LEDGER)
    pit = pl.read_parquet(PIT)
    # holdout guard
    sys.path.insert(0, ROOT)
    from experiments._holdout import assert_no_holdout_leak
    assert_no_holdout_leak(led.select("date"), context="remine.ledger")
    assert_no_holdout_leak(pit.select("date"), context="remine.pit")
    df = led.join(pit, on=["symbol", "date"], how="left")
    # add weekday
    df = df.with_columns(
        pl.col("date").str.strptime(pl.Date, "%Y-%m-%d").dt.weekday().alias("dow")
    )  # polars weekday: Mon=1..Sun=7
    return df


def _dow_table(df, cohort_expr, feat_expr, col, label):
    """optTP by day-of-week for rows in cohort_expr & feat_expr."""
    sub = df.filter(cohort_expr & feat_expr & pl.col(col).is_not_null())
    rows = {}
    for d in range(1, 6):  # Mon..Fri
        s = sub.filter(pl.col("dow") == d)
        if s.height == 0:
            rows[d] = {"N": 0, "optTP": None}
        else:
            rows[d] = {"N": s.height, "optTP": round(s[col].mean() * 100, 2)}
    vals = [rows[d]["optTP"] for d in range(1, 6) if rows[d]["optTP"] is not None and rows[d]["N"] >= 30]
    spread = round(max(vals) - min(vals), 2) if len(vals) >= 2 else None
    return {"label": label, "N_total": sub.height, "by_dow": rows, "spread_minN30": spread}


def main():
    df = _load()
    n_join = df.filter(pl.col("pit_w_mom").is_not_null()).height
    print("joined rows:", df.height, "| with pit_w_mom:", n_join, flush=True)

    out = {"ledger": LEDGER, "pit": PIT, "n_rows": df.height, "n_pit_nonnull": n_join}

    t7074 = df.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74))
    t7579 = df.filter((pl.col("overall") >= 75) & (pl.col("overall") <= 79))
    marg = t7579.filter(pl.col("opt15").is_not_null())["opt15"].mean()
    out["marginal_75_79_optTP"] = round(marg * 100, 2)
    out["base_70_74_optTP"] = round(t7074.filter(pl.col("opt15").is_not_null())["opt15"].mean() * 100, 2)

    # ---- (A) DAY-OF-WEEK FLATNESS TEST on the canonical w_mom in [5,8) cohort ----
    print("\n=== (A) DAY-OF-WEEK FLATNESS — 70-74 w_mom in [5,8) cohort, optTP15 ===", flush=True)
    cohort = (pl.col("overall") >= 70) & (pl.col("overall") <= 74)
    dow = {}
    dow["lookahead_ledger_wmom"] = _dow_table(
        df, cohort, (pl.col("w_mom") >= 5) & (pl.col("w_mom") < 8), "opt15", "ledger w_mom[5,8) (LOOK-AHEAD)")
    dow["pit_partial_wmom"] = _dow_table(
        df, cohort, (pl.col("pit_w_mom") >= 5) & (pl.col("pit_w_mom") < 8), "opt15", "pit_w_mom[5,8) (PARTIAL PIT)")
    dow["pit_completed_wmom"] = _dow_table(
        df, cohort, (pl.col("comp_w_mom") >= 5) & (pl.col("comp_w_mom") < 8), "opt15", "comp_w_mom[5,8) (COMPLETED PIT)")
    out["dow_flatness"] = dow
    for k, t in dow.items():
        bydow = " ".join("%s:%s(N%s)" % (["", "Mon", "Tue", "Wed", "Thu", "Fri"][d],
                                         t["by_dow"][d]["optTP"], t["by_dow"][d]["N"]) for d in range(1, 6))
        print("  %-26s spread(minN30)=%-6s  %s" % (t["label"], t["spread_minN30"], bydow), flush=True)

    # ---- (B) RE-MINE boundary on PIT features at option-TP ----
    print("\n=== (B) RE-MINE 70-74 PROMOTE / 75-79 DEMOTE on PIT features (opt15) ===", flush=True)
    out["marginal_note"] = "PROMOTE target: optTP > %.2f (75-79 marginal) with z>=3" % out["marginal_75_79_optTP"]

    def probe(tier, baseline, name, expr, store):
        sub = tier.filter(expr & pl.col("opt15").is_not_null())
        if sub.height < 50:
            store[name] = {"N": sub.height}
            return
        p = sub["opt15"].mean()
        store[name] = {"N": sub.height, "optTP": round(p * 100, 2),
                       "z_vs_tier": z(p, sub.height, baseline),
                       "z_vs_marg75": z(p, sub.height, marg),
                       "vs_marg75_pp": round((p - marg) * 100, 2)}

    b74 = t7074.filter(pl.col("opt15").is_not_null())["opt15"].mean()
    pro = {}
    # PIT-partial promote predicates (freshest current-week momentum/confirmation)
    probe(t7074, b74, "pit_wmom_5to8", (pl.col("pit_w_mom") >= 5) & (pl.col("pit_w_mom") < 8), pro)
    probe(t7074, b74, "pit_wmom_pos", pl.col("pit_w_mom") > 2, pro)
    probe(t7074, b74, "pit_wadj_pos", pl.col("pit_w_adj") > 5, pro)
    probe(t7074, b74, "pit_wcomp_hi", pl.col("pit_w_comp") > 60, pro)
    probe(t7074, b74, "pit_wmom_strong", pl.col("pit_w_mom") > 4, pro)
    # COMPLETED-PIT promote predicates (live convention, point-in-time-safe)
    probe(t7074, b74, "comp_wmom_5to8", (pl.col("comp_w_mom") >= 5) & (pl.col("comp_w_mom") < 8), pro)
    probe(t7074, b74, "comp_wmom_pos", pl.col("comp_w_mom") > 2, pro)
    probe(t7074, b74, "comp_wadj_pos", pl.col("comp_w_adj") > 5, pro)
    probe(t7074, b74, "comp_wcomp_hi", pl.col("comp_w_comp") > 60, pro)
    # divergence: partial says fresh-bullish but completed didn't (the "transition" signal)
    probe(t7074, b74, "pit_gt_comp_wmom", (pl.col("pit_w_mom") - pl.col("comp_w_mom")) > 4, pro)
    out["promote_probes_pit"] = pro

    b79 = t7579.filter(pl.col("opt15").is_not_null())["opt15"].mean()
    dem = {}
    probe(t7579, b79, "pit_wmom_neg", pl.col("pit_w_mom") < 0, dem)
    probe(t7579, b79, "pit_wadj_neg", pl.col("pit_w_adj") < 0, dem)
    probe(t7579, b79, "pit_wcomp_lo", pl.col("pit_w_comp") < 45, dem)
    probe(t7579, b79, "comp_wmom_neg", pl.col("comp_w_mom") < 0, dem)
    probe(t7579, b79, "comp_wadj_neg", pl.col("comp_w_adj") < 0, dem)
    probe(t7579, b79, "pit_lt_comp_wmom", (pl.col("pit_w_mom") - pl.col("comp_w_mom")) < -4, dem)
    out["demote_probes_pit"] = dem

    # ---- compare look-ahead vs PIT on the SAME promote predicate (the headline) ----
    head = {}
    for name, la_expr, pit_expr, comp_expr in [
        ("wmom_5to8",
         (pl.col("w_mom") >= 5) & (pl.col("w_mom") < 8),
         (pl.col("pit_w_mom") >= 5) & (pl.col("pit_w_mom") < 8),
         (pl.col("comp_w_mom") >= 5) & (pl.col("comp_w_mom") < 8)),
        ("wmom_pos",
         pl.col("w_mom") > 2, pl.col("pit_w_mom") > 2, pl.col("comp_w_mom") > 2),
    ]:
        def stat(expr):
            s = t7074.filter(expr & pl.col("opt15").is_not_null())
            if s.height < 50:
                return {"N": s.height}
            p = s["opt15"].mean()
            return {"N": s.height, "optTP": round(p * 100, 2),
                    "vs_marg75_pp": round((p - marg) * 100, 2), "z_vs_marg75": z(p, s.height, marg)}
        head[name] = {"lookahead": stat(la_expr), "pit_partial": stat(pit_expr), "pit_completed": stat(comp_expr)}
    out["headline_lookahead_vs_pit"] = head

    # ---- internal consistency: on FRIDAY signals the current-partial week IS the
    # current-complete week, so pit_w_comp should equal the ledger look-ahead
    # w_comp (state 3) for those rows. Confirms the partial aggregation matches
    # the production weekly aggregation when the week is complete. ----
    fri = df.filter((pl.col("dow") == 5) & pl.col("pit_w_comp").is_not_null() & pl.col("w_comp").is_not_null())
    if fri.height:
        d_fri = (fri["pit_w_comp"] - fri["w_comp"]).abs()
        out["friday_partial_vs_ledger_lookahead"] = {
            "N": fri.height, "mean_abs_d": round(float(d_fri.mean()), 3),
            "pct_within2": round(float((d_fri <= 2).sum()) / fri.height * 100, 1),
            "note": "Friday partial==complete; should match ledger state-3 w_comp closely",
        }
        print("\nFRIDAY consistency (pit_w_comp vs ledger look-ahead w_comp): N=%d mean|d|=%.3f %%<=2=%.1f"
              % (fri.height, d_fri.mean(), (d_fri <= 2).sum() / fri.height * 100), flush=True)

    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)
    print("\nPROMOTE PROBES (PIT):", json.dumps(pro), flush=True)
    print("DEMOTE PROBES (PIT):", json.dumps(dem), flush=True)
    print("\nHEADLINE look-ahead vs PIT (70-74 promote, optTP vs 75-79 marginal %.2f):" % out["marginal_75_79_optTP"], flush=True)
    print(json.dumps(head, indent=2), flush=True)
    print("\nwrote", OUT, flush=True)


if __name__ == "__main__":
    main()
