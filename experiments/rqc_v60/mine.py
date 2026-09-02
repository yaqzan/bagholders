"""Phase A.2 — option-TP discriminator mining for the RQC two-sided boundary.

Mines the v60 ledger on OPTION-TP (opt15 = 30dte_opt @ W=15d, the growth gate's
PRIMARY barrier), NOT generic WR15 (VCBW's fatal lens). Two cohorts:

  * 70-74 PROMOTE candidates: sub-cohorts whose opt15 is ABOVE the 75-79 marginal
    baseline -> accretive to the filled book (raises ebar AND lambda_eff).
  * 75-79 DEMOTE candidates: sub-cohorts whose opt15 is BELOW baseline -> the
    z>3 miss cohort to purify.

A clean RQC needs: a 70-74 predicate with opt15 >> 0.61 (the 75-79 marginal) and
N large enough to backfill, AND a 75-79 predicate with opt15 << baseline.
"""
import os, json, math
import polars as pl

TAG = os.environ.get("TAG", "5y")
LED = ".cache/rqc_v60/ledger_%s.parquet" % TAG

# discriminator candidates that exist in clean v60 weight_info
NUM = ["stoch", "trend", "macd", "bb", "rsi", "ta", "td",
       "w_adj", "w_comp", "w_bias", "w_mom", "vol_pct",
       "cont_lift", "cont_raw_lift", "mcd_dampen", "ich_dampen",
       "scw_dampen", "scw_scalar", "scw_raw_stoch", "scw_ext_index",
       "daily_volume_authority_wave", "sector_breadth_wave", "pre_boost", "pre_regime"]


def z(p, n, p0):
    if n <= 0 or p0 <= 0 or p0 >= 1:
        return 0.0
    se = math.sqrt(p0 * (1 - p0) / n)
    return round((p - p0) / se, 2) if se > 0 else 0.0


def main():
    df = pl.read_parquet(LED)
    col = "opt15"   # OPTION TP, the gate primary
    df = df.filter(pl.col(col).is_not_null())

    t7579 = df.filter((pl.col("overall") >= 75) & (pl.col("overall") <= 79))
    t7074 = df.filter((pl.col("overall") >= 70) & (pl.col("overall") <= 74))
    base_marginal = t7579[col].mean()       # the marginal filled-tier option-TP
    out = {"ledger": LED, "metric": col,
           "marginal_75_79_optTP": round(base_marginal * 100, 2),
           "N_75_79": t7579.height, "N_70_74": t7074.height,
           "optTP_70_74": round(t7074[col].mean() * 100, 2),
           "genWR15_75_79": round(df.filter((pl.col('overall')>=75)&(pl.col('overall')<=79))['gen15'].mean()*100,2)}

    def feature_scan(tier, baseline):
        feats = []
        for f in NUM:
            if f not in tier.columns:
                continue
            sub = tier.filter(pl.col(f).is_not_null())
            if sub.height < 300 or sub[f].n_unique() < 5:
                continue
            q33, q66 = sub[f].quantile(0.33), sub[f].quantile(0.66)
            if q33 is None or q66 is None or q33 == q66:
                continue
            bins = {"lo": sub.filter(pl.col(f) <= q33),
                    "mid": sub.filter((pl.col(f) > q33) & (pl.col(f) <= q66)),
                    "hi": sub.filter(pl.col(f) > q66)}
            bi, strongest = {}, 0.0
            for nm, b in bins.items():
                if b.height < 100:
                    continue
                p = b[col].mean()
                bi[nm] = {"N": b.height, "optTP": round(p * 100, 2), "z": z(p, b.height, baseline)}
                if abs(bi[nm]["z"]) > abs(strongest):
                    strongest = bi[nm]["z"]
            feats.append({"f": f, "q33": round(float(q33), 3), "q66": round(float(q66), 3),
                          "strong_z": strongest, "bins": bi})
        feats.sort(key=lambda x: -abs(x["strong_z"]))
        return feats[:10]

    out["demote_75_79_features"] = feature_scan(t7579, t7579[col].mean())
    out["promote_70_74_features"] = feature_scan(t7074, t7074[col].mean())

    # targeted predicates: 75-79 DEMOTE (want optTP << marginal) on the miss surface
    def probe(tier, baseline, name, expr, store):
        try:
            sub = tier.filter(expr)
        except Exception as e:
            store[name] = {"err": str(e)[:50]}; return
        if sub.height < 50:
            store[name] = {"N": sub.height}; return
        p = sub[col].mean()
        store[name] = {"N": sub.height, "optTP": round(p * 100, 2),
                       "z": z(p, sub.height, baseline), "vs_marg": round((p - base_marginal) * 100, 2)}

    dem = {}
    b79 = t7579[col].mean()
    probe(t7579, b79, "scw_dampened", pl.col("scw_dampen") > 0.5, dem)
    probe(t7579, b79, "cont_lifted", pl.col("cont_lift") > 0, dem)
    probe(t7579, b79, "stoch_lt40", pl.col("stoch") < 40, dem)
    probe(t7579, b79, "wadj_neg", pl.col("w_adj") < 0, dem)
    probe(t7579, b79, "trend_mid", (pl.col("trend") >= 40) & (pl.col("trend") <= 60), dem)
    probe(t7579, b79, "macd_mid", (pl.col("macd") >= 40) & (pl.col("macd") <= 60), dem)
    probe(t7579, b79, "vol_hi", pl.col("vol_pct") > t7579["vol_pct"].quantile(0.75), dem)
    probe(t7579, b79, "dvaw_neg", pl.col("daily_volume_authority_wave") < 0, dem)
    probe(t7579, b79, "wmom_lo", pl.col("w_mom") < 0, dem)
    out["demote_probes"] = dem

    # 70-74 PROMOTE (want optTP > marginal 0.61): fresh-momentum / strong-confirmation
    pro = {}
    b74 = t7074[col].mean()
    probe(t7074, b74, "wmom_5to8", (pl.col("w_mom") >= 5) & (pl.col("w_mom") < 8), pro)
    probe(t7074, b74, "wmom_pos", pl.col("w_mom") > 2, pro)
    probe(t7074, b74, "wadj_pos", pl.col("w_adj") > 5, pro)
    probe(t7074, b74, "stoch_hi", pl.col("stoch") > 55, pro)
    probe(t7074, b74, "trend_hi", pl.col("trend") > 65, pro)
    probe(t7074, b74, "macd_hi", pl.col("macd") > 60, pro)
    probe(t7074, b74, "wcomp_hi", pl.col("w_comp") > 60, pro)
    probe(t7074, b74, "no_scw", pl.col("scw_dampen") <= 0.5, pro)
    probe(t7074, b74, "vol_lo", pl.col("vol_pct") < t7074["vol_pct"].quantile(0.33), pro)
    probe(t7074, b74, "agree_hi", (pl.col("trend") > 60) & (pl.col("macd") > 55) & (pl.col("stoch") > 50), pro)
    out["promote_probes"] = pro

    outp = ".cache/rqc_v60/mine_%s.json" % TAG
    with open(outp, "w") as f:
        json.dump(out, f, indent=2)
    print("WROTE", outp)
    print("MARGINAL_75_79_optTP", out["marginal_75_79_optTP"], "| optTP_70_74", out["optTP_70_74"])
    print("DEMOTE_TOP_FEATURES", json.dumps([{"f": x["f"], "z": x["strong_z"]} for x in out["demote_75_79_features"][:6]]))
    print("PROMOTE_TOP_FEATURES", json.dumps([{"f": x["f"], "z": x["strong_z"]} for x in out["promote_70_74_features"][:6]]))
    print("DEMOTE_PROBES", json.dumps(dem))
    print("PROMOTE_PROBES", json.dumps(pro))


if __name__ == "__main__":
    main()
