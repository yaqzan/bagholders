"""Phase 2: multi-model ensemble + diverse-panel resolution test, on the Apex predictand.

Given Phase 1 (the v74 score has ~0 within-70+ resolution on apex), two questions:

  2a. LINEAGE ENSEMBLE (your #3). On the common 70+ keys of v70/v71/v73/v74:
      inter-version correlation of `overall` (are they ~the same forecast?), and
      does averaging change apex WR/EV. Lineage members share a derivation, so high
      correlation => ensemble is whiplash-smoothing, not new skill.

  2b. DIVERSE PANEL / "is apex predictable by ANYTHING?". Fit logistic models on a
      time-split of the 70+ cohort to predict apex-win, OUT-OF-FOLD:
        - momentum (pct_from_ema50/200)
        - vol (60d realized)
        - components (trend/bb/rsi/macd/stoch/ta)
        - components+mom+vol (kitchen sink)
        - the raw score itself (overall) as a 1-feature baseline
      Report OOF Brier-skill-score vs climatology + OOF resolution. If every panel
      is ~0, apex per-trade win is structurally unpredictable above the gate (the
      sigma-barrier compresses it) -> "independent scoring" can't beat post-processing
      because there's no signal to extract. If one panel has real BSS, that's a lead.

Read-only, in-sample. EV v1. sklearn LogisticRegression if available, else skip 2b.
"""
import os
import sys
import json
import math
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from database.bulk_cache import materialize_polars, cache_path, chunked_query_by_year
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion
from experiments._holdout import pre_cutoff_filter, cutoff_iso

EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
PERIOD = "30d"
MEMBERS = ["trend", "bb", "rsi", "macd", "stoch", "ta"]
LINEAGE = [70, 71, 73, 74]


def apex_label(symbols, dates):
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(symbols, dates)]
    results, sk = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    win = {}; evm = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EV:
            win[(r["symbol"], dk)] = 1 if sw["result"] == "win" else 0
            evm[(r["symbol"], dk)] = EV[sw["result"]]
    return win, evm, sk


def brier_skill(p, y):
    """OOF Brier skill score vs climatology (forecast = test base rate) + resolution."""
    p = np.clip(p, 1e-6, 1 - 1e-6); y = y.astype(float)
    base = y.mean()
    bs = ((p - y) ** 2).mean()
    bs_clim = base * (1 - base)
    bss = 1 - bs / bs_clim if bs_clim > 0 else float("nan")
    # resolution via decile bins of p
    q = np.quantile(p, np.linspace(0, 1, 11)); q[0] = -np.inf; q[-1] = np.inf
    res = 0.0; n = len(p)
    for i in range(10):
        m = (p >= q[i]) & (p < q[i + 1])
        if m.sum() == 0: continue
        res += m.sum() / n * (y[m].mean() - base) ** 2
    return bss, res, bs_clim


def main():
    out = {"barrier": "30dte_apex", "cutoff": cutoff_iso(), "lineage": LINEAGE}
    print(f"\n################ PHASE 2  ensemble + diverse panel  30dte_apex  (in-sample) ################", flush=True)

    # =========================== 2a. LINEAGE ENSEMBLE ===========================
    print(f"\n=== 2a. LINEAGE ENSEMBLE (v70/v71/v73/v74 on common 70+ keys) ===", flush=True)
    frames = []
    for v in LINEAGE:
        if v == 74:
            d = pl.read_parquet(cache_path("vv_v74_70plus")).select(["symbol", "date", "overall"]).rename({"overall": "o74"})
        else:
            d = pl.read_parquet(cache_path(f"vv_v{v}_70plus_overall")).select(["symbol", "date", f"o{v}"])
        frames.append(d)
    ens = frames[0]
    for d in frames[1:]:
        ens = ens.join(d, on=["symbol", "date"], how="inner")
    ens = pre_cutoff_filter(ens)
    print(f"  common 70+ keys (all four >=70, pre-cutoff): {ens.height:,}", flush=True)
    cols = [f"o{v}" for v in LINEAGE]
    M = np.column_stack([ens[c].to_numpy().astype(float) for c in cols])
    cc = np.corrcoef(M, rowvar=False)
    print("  inter-version corr(overall):")
    print("        " + "  ".join(f"v{v}" for v in LINEAGE))
    for i, v in enumerate(LINEAGE):
        print(f"   v{v}: " + "  ".join(f"{cc[i,j]:.3f}" for j in range(len(LINEAGE))))
    out["ensemble_corr"] = {f"v{LINEAGE[i]}": {f"v{LINEAGE[j]}": float(cc[i, j]) for j in range(len(LINEAGE))} for i in range(len(LINEAGE))}

    # apex label on the common keys; compare v74-alone vs ensemble-mean ranking at matched selectivity
    win, evm, sk = apex_label(ens["symbol"], ens["date"])
    ens = ens.with_columns(
        win=pl.struct(["symbol", "date"]).map_elements(lambda x: win.get((x["symbol"], x["date"]), -1), return_dtype=pl.Int64),
        ev=pl.struct(["symbol", "date"]).map_elements(lambda x: evm.get((x["symbol"], x["date"]), None), return_dtype=pl.Float64),
    ).filter(pl.col("win") >= 0)
    emean = ens.select(cols).to_numpy().mean(axis=1)
    w = ens["win"].to_numpy().astype(float); e = ens["ev"].to_numpy()
    o74 = ens["o74"].to_numpy().astype(float)
    print(f"  apex-matched common keys: {ens.height:,}  base WR={w.mean()*100:.2f}% EV={e.mean()*100:+.2f}%")
    for frac in (0.25, 0.10):
        k = max(1, int(frac * len(w)))
        top74 = np.argsort(o74)[::-1][:k]
        topen = np.argsort(emean)[::-1][:k]
        print(f"  top-{int(frac*100)}% by v74:       WR={w[top74].mean()*100:.2f}%  EV={e[top74].mean()*100:+.2f}%")
        print(f"  top-{int(frac*100)}% by ENSEMBLE:  WR={w[topen].mean()*100:.2f}%  EV={e[topen].mean()*100:+.2f}%   "
              f"(overlap {len(set(top74)&set(topen))/k*100:.0f}%)")
    out["ensemble_note"] = "high corr + matched-selectivity WR/EV ~equal => lineage ensemble = whiplash-smoothing, not skill"

    # =========================== 2b. DIVERSE PANEL ===========================
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except Exception as e:
        print(f"\n(2b skipped: sklearn unavailable: {e})")
        _write(out); return

    print(f"\n=== 2b. DIVERSE PANEL: is apex-win predictable above the gate by ANYTHING? (OOF) ===", flush=True)
    df = pre_cutoff_filter(pl.read_parquet(cache_path("vv_v74_70plus")))
    # join momentum (pct50/pct200) from all-scores
    alls = pl.read_parquet(cache_path("skill_v74_allscores_2016")).select(["symbol", "date", "pct50", "pct200"])
    df = df.join(alls, on=["symbol", "date"], how="left")
    # vol: 60d realized from price_feats
    pf = pl.read_parquet(cache_path("price_feats_raw_2015")).sort(["symbol", "date"])
    pf = pf.with_columns(ret=(pl.col("close") / pl.col("close").shift(1).over("symbol")).log())
    pf = pf.with_columns(vol60=pl.col("ret").rolling_std(60).over("symbol")).select(["symbol", "date", "vol60"])
    df = df.join(pf, on=["symbol", "date"], how="left")
    # regime (optional)
    try:
        rows = chunked_query_by_year(
            "SELECT date, regime_multiplier FROM market_regime WHERE date BETWEEN '{y_start}' AND '{y_end}'",
            start_year=2016, end_year=2027, verbose=False)
        rdf = pl.DataFrame({"date": [r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]) for r in rows],
                            "regime": [float(r[1]) if r[1] is not None else float("nan") for r in rows]})
        df = df.join(rdf.unique(subset=["date"]), on="date", how="left")
        has_reg = True
    except Exception as e:
        print(f"  (regime unavailable: {e})"); has_reg = False

    win, evm, sk = apex_label(df["symbol"], df["date"])
    df = df.with_columns(win=pl.struct(["symbol", "date"]).map_elements(lambda x: win.get((x["symbol"], x["date"]), -1), return_dtype=pl.Int64)).filter(pl.col("win") >= 0)
    df = df.drop_nulls(subset=["pct50", "vol60"])
    dts = df["date"].to_numpy(); order_split = np.quantile(np.argsort(np.argsort(dts)) / len(dts), 0.6)
    split = np.sort(dts)[int(0.6 * len(dts))]
    tr = (dts < split); te = ~tr
    y = df["win"].to_numpy().astype(int)
    print(f"  panel N={len(y):,}  train<{split} (N={int(tr.sum()):,}) test (N={int(te.sum()):,})  base WR_te={y[te].mean()*100:.2f}%")

    feat_sets = {
        "score(overall)": ["overall"],
        "momentum": ["pct50", "pct200"],
        "vol60": ["vol60"],
        "components": MEMBERS,
        "comp+mom+vol": MEMBERS + ["pct50", "pct200", "vol60"],
    }
    if has_reg and df["regime"].is_not_null().sum() > 0.8 * len(df):
        feat_sets["comp+mom+vol+regime"] = MEMBERS + ["pct50", "pct200", "vol60", "regime"]

    print(f"  {'feature set':>22} | {'OOF BSS':>9} | {'OOF resolution':>14}")
    panel = []
    for nm, cols in feat_sets.items():
        cc = [c for c in cols if c in df.columns]
        Xtr = df.select(cc).to_numpy()[tr]; Xte = df.select(cc).to_numpy()[te]
        ok = ~np.isnan(Xtr).any(axis=1); okte = ~np.isnan(Xte).any(axis=1)
        if ok.sum() < 2000 or okte.sum() < 2000:
            print(f"  {nm:>22} | (insufficient non-nan rows)"); continue
        sclr = StandardScaler().fit(Xtr[ok])
        clf = LogisticRegression(max_iter=2000, C=1.0).fit(sclr.transform(Xtr[ok]), y[tr][ok])
        p = clf.predict_proba(sclr.transform(Xte[okte]))[:, 1]
        bss, res, _ = brier_skill(p, y[te][okte])
        print(f"  {nm:>22} | {bss:+8.4f} | {res:14.6f}")
        panel.append({"features": nm, "bss_oof": bss, "resolution_oof": res, "n_test": int(okte.sum())})
    out["diverse_panel"] = panel
    print(f"  ^ BSS<=~0 across the board => apex per-trade win is ~unpredictable above the 70 gate")
    print(f"    (sigma-barrier compression); the score's job is the GATE + leverage, not per-trade ranking.")
    _write(out)


def _write(out):
    jp = os.path.join(os.path.dirname(__file__), "phase2_results.json")
    with open(jp, "w") as f:
        json.dump(out, f, indent=2, default=float)
    print(f"\nwrote {jp}")


if __name__ == "__main__":
    main()
