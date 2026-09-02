"""E1-E6 kill-tests for the ONE surviving cell: prior_earn_jump_pct T3_high.

Bars are locked in PREREGISTRATION.md Amendment A7. Every test is reported
unconditionally, pass or fail -- selecting among them post hoc is prohibited.

E1 realized-vol control   E2 score-band control   E3 orthogonality vs SVR
E4 mechanism (stop vs win decomposition)   E5 PIT audit   E6 supply cost

ASCII output only.
"""
import sys
import importlib.util
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

LEDGER = ROOT / ".cache/event_noise_decomp/classified_ledger.parquet"
OHLC = ROOT / ".cache/experiment_data/event_noise_ohlc_2019.parquet"
SVR = ROOT / ".cache/apex_speed_v70/semivol_map.parquet"

VOL_WINDOW, VOL_MIN = 60, 20        # matches trend_ma_lattice.realized_vol
EV_MAP = {"win": 0.30, "stop": -0.70, "expire": -0.40}


# --- CR1 clustered sandwich, imported from the peak_fakeout harness ----------
def _load_cr1():
    p = ROOT / "experiments/peak_fakeout/mine.py"
    spec = importlib.util.spec_from_file_location("_pf_mine_private", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_pf_mine_private"] = mod
    old = sys.argv
    sys.argv = ["mine.py", "--noop"]
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        sys.argv = old
    return mod.clustered_ols_effect


try:
    clustered_ols_effect = _load_cr1()
    CR1_SRC = "peak_fakeout.clustered_ols_effect"
except Exception as e:                                        # noqa: BLE001
    print(f"[warn] CR1 import failed ({e!r}); using local sandwich")
    CR1_SRC = "local"

    def clustered_ols_effect(X, y, clusters):
        X = np.asarray(X, float)
        y = np.asarray(y, float)
        XtX_inv = np.linalg.pinv(X.T @ X)
        beta = XtX_inv @ (X.T @ y)
        resid = y - X @ beta
        meat = np.zeros((X.shape[1], X.shape[1]))
        for g in np.unique(clusters):
            m = clusters == g
            sg = X[m].T @ resid[m]
            meat += np.outer(sg, sg)
        G = len(np.unique(clusters))
        n, k = X.shape
        adj = (G / max(G - 1, 1)) * ((n - 1) / max(n - k, 1))
        V = XtX_inv @ (adj * meat) @ XtX_inv
        return dict(beta=beta[1], t=beta[1] / np.sqrt(max(V[1, 1], 1e-300)),
                    n_used=n, n_clusters=G, method="ols")


def cr1_t(sub, flag_col, y_col):
    """Clustered t on y ~ 1 + flag, clusters = entry date. Returns (dEV, t, n)."""
    d = sub.filter(pl.col(flag_col).is_not_null() & pl.col(y_col).is_not_null())
    if d.height < 60:
        return None
    y = d[y_col].to_numpy().astype(float)
    f = d[flag_col].to_numpy().astype(float)
    fin = np.isfinite(y) & np.isfinite(f)                     # trap 4: mask non-finite
    y, f = y[fin], f[fin]
    cl = np.array([str(x) for x in d["date"].to_list()])[fin]
    if f.sum() < 20 or (1 - f).sum() < 20:                    # MIN_CELL_CONTROL_SIDE
        return None
    share = f.mean()
    if not (0.05 <= share <= 0.95):                           # degenerate-bucket gate
        return None
    X = np.column_stack([np.ones(len(f)), f])
    r = clustered_ols_effect(X, y, cl)
    t = float(r["t"])
    if not np.isfinite(t):
        raise RuntimeError(f"non-finite t for {flag_col}/{y_col} -- refusing false NULL")
    return dict(dEV=float(r["beta"]), t=t, n_hi=int(f.sum()), n_lo=int((1 - f).sum()))


def show(tag, r):
    if r is None:
        print(f"    {tag:<34} [gated / insufficient N]")
    else:
        print(f"    {tag:<34} dEV={r['dEV'] * 100:+6.2f}pp  t={r['t']:+6.2f}  "
              f"N_hi={r['n_hi']:>5} N_lo={r['n_lo']:>5}")


# --- realized vol at entry (causal 60-bar, matches the ledger builder) -------
def build_vol_map():
    df = pl.read_parquet(OHLC).sort(["symbol", "date"])
    out = {}
    for sym, g in df.group_by("symbol", maintain_order=True):
        s = sym[0] if isinstance(sym, tuple) else sym
        closes = g["close"].to_numpy().astype(float)
        n = len(closes)
        if n < 2:
            continue
        ret = np.zeros(n)
        ret[1:] = closes[1:] / closes[:-1] - 1.0
        csum = np.concatenate([[0.0], np.cumsum(ret)])
        csum2 = np.concatenate([[0.0], np.cumsum(ret ** 2)])
        idx = np.arange(n)
        lo = np.maximum(1, idx - VOL_WINDOW + 1)
        cnt = idx - lo + 1
        s_ = csum[idx + 1] - csum[lo]
        s2 = csum2[idx + 1] - csum2[lo]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = s_ / cnt
            var = (s2 - cnt * mean * mean) / np.maximum(cnt - 1, 1)
        sig = np.where(cnt >= VOL_MIN, np.sqrt(np.maximum(var, 0.0)), np.nan)
        for d, v in zip(g["date"].to_list(), sig):
            out[(s, d)] = float(v) if np.isfinite(v) else None
    return out


def main():
    print("=" * 92)
    print("A7 KILL-TESTS -- prior_earn_jump_pct T3_high   (CR1 via %s)" % CR1_SRC)
    print("=" * 92)

    df = pl.read_parquet(LEDGER).filter(
        (pl.col("coverage") == "ok") & (~pl.col("post_cutoff"))
    )
    df = df.filter(pl.col("prior_earn_jump_pct").is_not_null())
    cuts = df["prior_earn_jump_pct"].quantile(1 / 3), df["prior_earn_jump_pct"].quantile(2 / 3)
    print(f"  N with prior_earn_jump_pct = {df.height}   terciles at "
          f"{cuts[0]:.4f} / {cuts[1]:.4f}")

    df = df.with_columns([
        (pl.col("prior_earn_jump_pct") > cuts[1]).cast(pl.Int8).alias("t3hi"),
        pl.col("apex_ev").alias("ev"),
        pl.col("apex_win").cast(pl.Float64).alias("win"),
    ])

    base = df.filter(pl.col("t3hi") == 0)
    hi = df.filter(pl.col("t3hi") == 1)
    print(f"\n  HEADLINE  T3_high N={hi.height} WR={hi['win'].mean() * 100:.2f}% "
          f"EV={hi['ev'].mean():+.4f}   rest N={base.height} "
          f"WR={base['win'].mean() * 100:.2f}% EV={base['ev'].mean():+.4f}")
    show("pooled (EV)", cr1_t(df, "t3hi", "ev"))
    show("pooled (WIN)", cr1_t(df, "t3hi", "win"))

    # ---------------- E4 mechanism: stop vs win vs expire ------------------
    print("\n" + "-" * 92)
    print("E4 MECHANISM -- outcome-label decomposition (stated prediction: STOP rate rises)")
    print("-" * 92)
    print(f"    {'cohort':<12}{'N':>7}{'win%':>9}{'stop%':>9}{'expire%':>10}{'EV':>10}")
    for lab, sub in [("rest", base), ("T3_high", hi)]:
        n = sub.height
        w = (sub["orig_label"] == "win").mean() * 100
        s = (sub["orig_label"] == "stop").mean() * 100
        e = (sub["orig_label"] == "expire").mean() * 100
        print(f"    {lab:<12}{n:>7}{w:>9.2f}{s:>9.2f}{e:>10.2f}{sub['ev'].mean():>10.4f}")
    d_stop = ((hi["orig_label"] == "stop").mean() - (base["orig_label"] == "stop").mean()) * 100
    d_win = ((hi["orig_label"] == "win").mean() - (base["orig_label"] == "win").mean()) * 100
    d_exp = ((hi["orig_label"] == "expire").mean() - (base["orig_label"] == "expire").mean()) * 100
    print(f"    deltas: win {d_win:+.2f}pp   stop {d_stop:+.2f}pp   expire {d_exp:+.2f}pp")
    ev_from_stop = (d_stop / 100) * (EV_MAP["stop"] - EV_MAP["win"])
    ev_from_exp = (d_exp / 100) * (EV_MAP["expire"] - EV_MAP["win"])
    print(f"    EV attributable to stop-shift  = {ev_from_stop:+.4f}")
    print(f"    EV attributable to expire-shift= {ev_from_exp:+.4f}")

    # ---------------- E1 realized-vol control ------------------------------
    print("\n" + "-" * 92)
    print("E1 REALIZED-VOL CONTROL (causal 60-bar sigma at entry)")
    print("-" * 92)
    vmap = build_vol_map()
    vols = [vmap.get((s, d)) for s, d in zip(df["symbol"].to_list(), df["date"].to_list())]
    df = df.with_columns(pl.Series("rvol", vols, dtype=pl.Float64))
    dv = df.filter(pl.col("rvol").is_not_null())
    print(f"    vol coverage {dv.height}/{df.height}")
    q1, q2 = dv["rvol"].quantile(1 / 3), dv["rvol"].quantile(2 / 3)
    print(f"    vol terciles at {q1:.4f} / {q2:.4f}")
    print(f"    corr(prior_earn_jump_pct, rvol) = "
          f"{np.corrcoef(dv['prior_earn_jump_pct'].to_numpy(), dv['rvol'].to_numpy())[0, 1]:+.4f}")
    for lab, sub in [
        ("vol T1 (low)", dv.filter(pl.col("rvol") <= q1)),
        ("vol T2 (mid)", dv.filter((pl.col("rvol") > q1) & (pl.col("rvol") <= q2))),
        ("vol T3 (high)", dv.filter(pl.col("rvol") > q2)),
    ]:
        show(lab + " EV", cr1_t(sub, "t3hi", "ev"))

    # ---------------- E2 score-band control --------------------------------
    print("\n" + "-" * 92)
    print("E2 SCORE-BAND CONTROL (A6 2-band)")
    print("-" * 92)
    show("75-79 EV", cr1_t(df.filter(pl.col("overall") < 80), "t3hi", "ev"))
    show("80+   EV", cr1_t(df.filter(pl.col("overall") >= 80), "t3hi", "ev"))

    # ---------------- E3 orthogonality vs SVR ------------------------------
    print("\n" + "-" * 92)
    print("E3 ORTHOGONALITY vs SVR (semivol_r)")
    print("-" * 92)
    if SVR.exists():
        sv = pl.read_parquet(SVR)
        sv = sv.with_columns(pl.col("date").cast(pl.Utf8).alias("_d"))
        j = df.with_columns(pl.col("date").cast(pl.Utf8).alias("_d")).join(
            sv.select(["sym_id", "_d", "semivol_r"]),
            left_on=["symbol", "_d"], right_on=["sym_id", "_d"], how="left")
        k = j.filter(pl.col("semivol_r").is_not_null())
        print(f"    semivol_r join coverage {k.height}/{df.height}")
        if k.height > 200:
            rho = np.corrcoef(k["prior_earn_jump_pct"].to_numpy(),
                              k["semivol_r"].to_numpy())[0, 1]
            print(f"    corr(prior_earn_jump_pct, semivol_r) = {rho:+.4f}   "
                  f"bar |rho|<0.7 -> {'PASS' if abs(rho) < 0.7 else 'FAIL'}")
            med = k["semivol_r"].median()
            show("svr below median EV", cr1_t(k.filter(pl.col("semivol_r") <= med), "t3hi", "ev"))
            show("svr above median EV", cr1_t(k.filter(pl.col("semivol_r") > med), "t3hi", "ev"))
    else:
        print(f"    [absent] {SVR}")

    # ---------------- E5 PIT audit -----------------------------------------
    print("\n" + "-" * 92)
    print("E5 PIT AUDIT -- contributing events must be REPORTED and STRICTLY BEFORE entry")
    print("-" * 92)
    from database.models.core import EarningsDate
    rows = list(EarningsDate.select(
        EarningsDate.symbol, EarningsDate.effective_date, EarningsDate.reported_eps).tuples())
    from collections import defaultdict
    by = defaultdict(list)
    for s, eff, rep in rows:
        if eff is not None:
            by[s].append((eff, rep))
    for s in by:
        by[s].sort()
    import bisect
    bad_future, bad_unreported, ok = 0, 0, 0
    for s, d in zip(df["symbol"].to_list(), df["date"].to_list()):
        lst = by.get(s)
        if not lst:
            continue
        i = bisect.bisect_left([e for e, _ in lst], d) - 1
        if i < 0:
            continue
        eff, rep = lst[i]
        if eff >= d:
            bad_future += 1
        elif rep is None:
            bad_unreported += 1
        else:
            ok += 1
    print(f"    contributing events: reported+strictly-prior={ok}  "
          f"unreported={bad_unreported}  not-strictly-prior={bad_future}")
    print(f"    leakage rate = {(bad_future) / max(ok + bad_unreported + bad_future, 1):.4f}")

    # ---------------- E6 supply cost ---------------------------------------
    print("\n" + "-" * 92)
    print("E6 SUPPLY COST of excluding T3_high")
    print("-" * 92)
    tot = df.height
    print(f"    T3_high share of 75+ supply = {hi.height / tot:.3f}  "
          f"({hi.height} of {tot})")
    print(f"    post-exclusion pooled EV    = {base['ev'].mean():+.4f} "
          f"(vs {df['ev'].mean():+.4f} all)")
    print("    per-year T3_high share:")
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        sub = df.filter(pl.col("date").dt.year() == y)
        if sub.height:
            print(f"      {y}: {sub.filter(pl.col('t3hi') == 1).height / sub.height:.3f} "
                  f"(N={sub.height})")


if __name__ == "__main__":
    sys.exit(main())
