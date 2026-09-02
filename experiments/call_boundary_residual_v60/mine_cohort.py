"""Phase A.2 — cohort-z mining within the 75-79 CALL boundary cohort (v60).

For each weight_info feature, split the 75-79 cohort into terciles and report
WR15 per bin + binomial z vs the cohort baseline. Surfaces the discriminator(s)
that separate weak 75-79 incumbents (losers) from sound ones. W1 gate: need a
within-cohort sub-group with z>=3 and N>=~150 to justify a dampener.
"""
import os, sys, json, math
import polars as pl

TAG = os.environ.get("TAG", "5y")
LED = ".cache/call_boundary_residual_v60/ledger_%s.parquet" % TAG

NUM_FEATURES = [
    "stoch", "trend", "macd", "bb", "rsi", "ta", "td",
    "w_adj", "w_comp", "w_bias", "w_mom",
    "wadj_completed", "wadj_partial", "weekly_adj_gap",
    "cont_lift", "cont_raw_lift", "mcd_dampen", "mcd_mcap_b",
    "daily_volume_authority_wave", "pre_boost", "pre_regime", "mis_stress",
]


def zbin(p, n, p0):
    if n <= 0 or p0 <= 0 or p0 >= 1:
        return 0.0
    se = math.sqrt(p0 * (1 - p0) / n)
    return (p - p0) / se if se > 0 else 0.0


def main():
    df = pl.read_parquet(LED)
    out = {"ledger": LED, "N_all": df.height}

    for tlo, thi in [(75, 79)]:
        tier = df.filter((pl.col("overall") >= tlo) & (pl.col("overall") <= thi))
        tier = tier.filter(pl.col("win15").is_not_null())
        base_p = tier["win15"].mean()
        base_n = tier.height
        out["cohort"] = {"tier": [tlo, thi], "N": base_n, "WR15": round(base_p * 100, 2)}

        feats = []
        for f in NUM_FEATURES:
            if f not in tier.columns:
                continue
            sub = tier.filter(pl.col(f).is_not_null())
            if sub.height < 300:
                continue
            vals = sub[f]
            # skip near-constant features
            if vals.n_unique() < 4:
                continue
            try:
                q33 = vals.quantile(0.33)
                q66 = vals.quantile(0.66)
            except Exception:
                continue
            if q33 is None or q66 is None or q33 == q66:
                continue
            bins = {
                "lo": sub.filter(pl.col(f) <= q33),
                "mid": sub.filter((pl.col(f) > q33) & (pl.col(f) <= q66)),
                "hi": sub.filter(pl.col(f) > q66),
            }
            bininfo = {}
            for name, b in bins.items():
                if b.height == 0:
                    continue
                p = b["win15"].mean()
                bininfo[name] = {
                    "N": b.height,
                    "WR15": round(p * 100, 2),
                    "z": round(zbin(p, b.height, base_p), 2),
                }
            # rank feature by the strongest |z| bin with N>=150
            strongest = 0.0
            for name, bi in bininfo.items():
                if bi["N"] >= 150 and abs(bi["z"]) > abs(strongest):
                    strongest = bi["z"]
            feats.append({"feature": f, "q33": round(float(q33), 3),
                          "q66": round(float(q66), 3),
                          "strongest_z": round(strongest, 2), "bins": bininfo})
        feats.sort(key=lambda x: -abs(x["strongest_z"]))
        out["feature_ranking"] = feats

    # targeted weak-cohort probes (hypotheses for the dampener discriminator)
    tier = df.filter((pl.col("overall") >= 75) & (pl.col("overall") <= 79) &
                     pl.col("win15").is_not_null())
    base_p = tier["win15"].mean()
    probes = {}

    def probe(name, expr):
        try:
            sub = tier.filter(expr)
        except Exception as e:
            probes[name] = {"err": str(e)[:60]}
            return
        if sub.height == 0:
            probes[name] = {"N": 0}
            return
        p = sub["win15"].mean()
        probes[name] = {"N": sub.height, "WR15": round(p * 100, 2),
                        "z": round(zbin(p, sub.height, base_p), 2),
                        "vs_base": round((p - base_p) * 100, 2)}

    probe("stoch_lt40", pl.col("stoch") < 40)
    probe("stoch_lt30", pl.col("stoch") < 30)
    probe("wadj_neg", pl.col("w_adj") < 0)
    probe("wadj_lt_-5", pl.col("w_adj") < -5)
    probe("cont_lift_on", pl.col("cont_lift") > 0)
    probe("mcd_on", pl.col("mcd_dampen") > 0)
    probe("dvaw_neg", pl.col("daily_volume_authority_wave") < 0)
    probe("vol_hi", pl.col("vol_pct") > tier["vol_pct"].quantile(0.66))
    probe("trend_mid", (pl.col("trend") >= 40) & (pl.col("trend") <= 60))
    probe("macd_mid", (pl.col("macd") >= 40) & (pl.col("macd") <= 60))
    probe("pre_boost_lt75", pl.col("pre_boost") < 75)
    probe("weekly_gap_pos", pl.col("weekly_adj_gap") > 0)
    out["weak_probes"] = probes

    outp = ".cache/call_boundary_residual_v60/cohort_%s.json" % TAG
    with open(outp, "w") as f:
        json.dump(out, f, indent=2)
    print("WROTE", outp)
    print("COHORT", json.dumps(out["cohort"]))
    print("TOP_FEATURES", json.dumps(out["feature_ranking"][:6]))
    print("PROBES", json.dumps(probes))


if __name__ == "__main__":
    main()
