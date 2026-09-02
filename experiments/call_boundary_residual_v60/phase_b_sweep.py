"""Phase B — fast-sim Bayesian/LHS sweep of the Vol-Confidence Boundary Wave.

The VCBW modifier depends only on (overall, vol_pct), both present per-peak in
the ledger, so each candidate is evaluated INSTANTLY by re-tiering every peak and
re-aggregating WR15/N (the barrier outcome win15 is invariant to the score label).

Mechanism (calls only; applied as a score-stage residual):
  LIFT  (70 <= overall <= 74): low realized vol -> promote toward TL (<80)
      strength = clip((VL_HI - vol)/(VL_HI - VL_LO), 0, 1)
      overall += KL * strength * (TL - overall)
  DAMP  (75 <= overall <= 79): high realized vol -> demote toward TD (<75)
      weakness = clip((vol - VD_LO)/(VD_HI - VD_LO), 0, 1)
      overall -= KD * weakness * (overall - TD)
  80+ is never touched -> 80+/85+/90+/95+ WR15 identical to baseline by construction.

Ranking objective (encodes the user's "improve WR AND N"):
  Among candidates whose 75+ N >= baseline 75+ N (N must not fall) AND 75-79
  discrete WR15 >= baseline AND gradient preserved, MAXIMIZE 75+ WR15, tiebreak
  on 75+ N. Reported deltas are vs the unmodified v60 ledger.
"""
import os, json, math, random
import polars as pl

TAG = os.environ.get("TAG", "5y")
df = pl.read_parquet(".cache/call_boundary_residual_v60/ledger_%s.parquet" % TAG)
df = df.filter(pl.col("win15").is_not_null() & pl.col("vol_pct").is_not_null())

ov = df["overall"].to_numpy().astype(float)
vol = df["vol_pct"].to_numpy().astype(float)
w15 = df["win15"].to_numpy().astype(float)
w7 = df["win7"].to_numpy()
w30 = df["win30"].to_numpy()
yr = [d[:4] for d in df["date"].to_list()]
import numpy as np
w15 = np.array(w15); w7 = np.array([x if x is not None else np.nan for x in w7], float)
w30 = np.array([x if x is not None else np.nan for x in w30], float)
yr = np.array(yr)
N = len(ov)


def apply_mod(p):
    o = ov.copy()
    # LIFT 70-74
    m = (ov >= 70) & (ov <= 74)
    strength = np.clip((p["VL_HI"] - vol) / max(1e-6, (p["VL_HI"] - p["VL_LO"])), 0, 1)
    o = np.where(m, o + p["KL"] * strength * (p["TL"] - o), o)
    # DAMP 75-79
    m2 = (ov >= 75) & (ov <= 79)
    weak = np.clip((vol - p["VD_LO"]) / max(1e-6, (p["VD_HI"] - p["VD_LO"])), 0, 1)
    o = np.where(m2, o - p["KD"] * weak * (o - p["TD"]), o)
    return o


def wr(mask, arr=w15):
    a = arr[mask]
    a = a[~np.isnan(a)]
    return (float(np.mean(a)) * 100 if len(a) else None), int(len(a))


def evaluate(p, base):
    o = apply_mod(p)
    # round like the scorer (overall is int)
    onew = np.rint(o)
    res = {}
    def tier_stats(lo, hi):
        mask = (onew >= lo) & (onew <= hi)
        w, n = wr(mask)
        return w, n
    for lo, hi, name in [(70, 74, "70_74"), (75, 79, "75_79"), (80, 84, "80_84"), (85, 89, "85_89")]:
        w, n = tier_stats(lo, hi)
        res[name] = {"WR15": round(w, 2) if w else None, "N": n}
    # cumulative
    for thr in [75, 80, 70]:
        mask = onew >= thr
        w, n = wr(mask)
        w7v, _ = wr(mask, w7); w30v, _ = wr(mask, w30)
        res["ge%d" % thr] = {"WR15": round(w, 2) if w else None, "N": n,
                             "WR7": round(w7v, 2) if w7v else None,
                             "WR30": round(w30v, 2) if w30v else None}
    # per-year 75+ WR15 (W3)
    yrstats = {}
    for y in ["2021", "2022", "2023", "2024", "2025"]:
        mask = (onew >= 75) & (yr == y)
        w, n = wr(mask)
        yrstats[y] = round(w, 2) if w else None
    res["ge75_by_year"] = yrstats
    return res


def main():
    # baseline (identity)
    ident = {"VL_LO": 0, "VL_HI": 0, "KL": 0, "TL": 0, "VD_LO": 1e9, "VD_HI": 1e9 + 1, "KD": 0, "TD": 0}
    base = evaluate(ident, None)
    b75_wr = base["ge75"]["WR15"]; b75_n = base["ge75"]["N"]
    b8084 = base["80_84"]["WR15"]; b7579 = base["75_79"]["WR15"]
    b80_wr = base["ge80"]["WR15"]; b80_n = base["ge80"]["N"]
    print("BASELINE ge75 WR15=%.2f N=%d | 75-79=%.2f 80-84=%.2f ge80=%.2f" % (b75_wr, b75_n, b7579, b8084, b80_wr))

    rng = random.Random(12345)
    n_lhs = int(os.environ.get("NLHS", "300"))
    cands = []
    for _ in range(n_lhs):
        p = {
            "VL_LO": rng.uniform(1.2, 1.9),
            "VL_HI": rng.uniform(2.0, 2.8),
            "KL":    rng.uniform(0.2, 0.95),
            "TL":    rng.uniform(75.5, 79.0),
            "VD_LO": rng.uniform(2.0, 2.8),
            "VD_HI": rng.uniform(3.0, 4.5),
            "KD":    rng.uniform(0.2, 0.95),
            "TD":    rng.uniform(70.0, 73.5),
        }
        if p["VL_HI"] <= p["VL_LO"] or p["VD_HI"] <= p["VD_LO"]:
            continue
        r = evaluate(p, base)
        rec = {"p": p,
               "ge75_WR15": r["ge75"]["WR15"], "ge75_N": r["ge75"]["N"],
               "d75_WR": round(r["ge75"]["WR15"] - b75_wr, 3),
               "d75_N": r["ge75"]["N"] - b75_n,
               "wr7579": r["75_79"]["WR15"], "n7579": r["75_79"]["N"],
               "ge80_WR15": r["ge80"]["WR15"], "ge80_N": r["ge80"]["N"],
               "yr": r["ge75_by_year"]}
        cands.append(rec)

    # ship-aware filter: N must not fall (>=base), 80+ untouched (auto), 75-79 not regress
    def passes(c):
        if c["ge75_N"] < b75_n:            # N must not decrease
            return False
        if c["ge80_WR15"] is None or abs(c["ge80_WR15"] - b80_wr) > 0.01:  # 80+ identical
            return False
        if c["wr7579"] < b7579 - 0.01:     # 75-79 discrete must improve/hold
            return False
        # W3: every full year 75+ WR15 must not drop vs baseline year
        for y in ["2021", "2022", "2023", "2024", "2025"]:
            if c["yr"][y] is None or base["ge75_by_year"][y] is None:
                return False
            if c["yr"][y] < base["ge75_by_year"][y] - 0.5:
                return False
        return True

    good = [c for c in cands if passes(c)]
    # rank: maximize WR15 then N
    good.sort(key=lambda c: (-c["d75_WR"], -c["d75_N"]))
    outdir = ".cache/call_boundary_residual_v60"
    with open(os.path.join(outdir, "phase_b_%s.jsonl" % TAG), "w") as f:
        for c in cands:
            f.write(json.dumps(c) + "\n")
    summ = {
        "baseline": {"ge75_WR15": b75_wr, "ge75_N": b75_n, "wr7579": b7579, "ge80_WR15": b80_wr,
                     "ge75_by_year": base["ge75_by_year"]},
        "n_cands": len(cands), "n_pass": len(good),
        "top": good[:8],
    }
    with open(os.path.join(outdir, "phase_b_%s_summary.json" % TAG), "w") as f:
        json.dump(summ, f, indent=2)
    print("CANDS=%d PASS=%d" % (len(cands), len(good)))
    if good:
        c = good[0]
        print("BEST d75_WR=%+.2f d75_N=%+d ge75_WR15=%.2f ge75_N=%d wr7579=%.2f" % (
            c["d75_WR"], c["d75_N"], c["ge75_WR15"], c["ge75_N"], c["wr7579"]))
        print("BEST_P=" + json.dumps({k: round(v, 3) for k, v in c["p"].items()}))


if __name__ == "__main__":
    main()
