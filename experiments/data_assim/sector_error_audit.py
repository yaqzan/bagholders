"""ITEM C — Sector-error diagnostic (READ-ONLY; DIAGNOSTIC, not a scoring change).

Question: does a per-GICS-sector, strictly PIT-lagged, shrunk realized-apex-EV
"error" term (sector EV minus the pooled EV) have OUT-OF-SAMPLE, sign-stable,
MULTI-REGIME predictive value BEYOND what `overall` already encodes?

The predictand is the LIVE Apex barrier (30dte_apex: win @1.092s / stop @2.548s,
30d window) under the option-EV map win/stop/expire = +0.30/-0.70/-0.40 — the same
substrate as experiments/skill_vs_baseline/verify_scorecard.py.

WHY THIS IS THE DEFAULT-NULL DESIGN (the documented traps it must avoid):

  1. Score-norm trap (.claude/docs/known-issues.md WHAT NOT TO DO): re-grading the
     score per stock/sector is just "lower the threshold" and is dilutive. So this
     test does NOT re-rank `overall`. It holds `overall` FIXED (integer-bucket
     stratified) and asks whether the sector-error term predicts the WITHIN-BUCKET
     EV residual — i.e. ORTHOGONAL info the score does not already encode.

  2. Single-regime sector-rotation artifact (the risk-shaper edge was a 2024
     artifact): a sector-momentum/rotation fit looks great in one regime and
     flips in the next. So the headline test is OUT-OF-SAMPLE and reported
     PER-REGIME-YEAR with an explicit sign-stability verdict. A signal that only
     works in one year is reported as NULL.

  3. Survivorship/look-ahead: the sector-error term at signal date D uses ONLY
     signals whose 30d apex barrier RESOLVED strictly before D (D - LAG_DAYS
     calendar days), so no future bar leaks in. Estimated on the dense 70+ call
     population; evaluated on the tradable 75+ cohort.

Shrinkage: James-Stein toward the pooled mean across the (here 11) sector cells,
so thin sector-year cells are pulled to ~0 rather than chased.

Bounded/sampled by construction (reads two cached parquets + the barrier cache;
no full re-pull, no DB score writes). Run inline:
    PYTHONIOENCODING=utf-8 python experiments/data_assim/sector_error_audit.py
"""
import os
import sys
import math
import json
import argparse
from collections import namedtuple, defaultdict
from datetime import date as _date, timedelta

import numpy as np
import polars as pl

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from database.bulk_cache import materialize_polars            # noqa: E402
from database.barrier_cache import peaks_to_swing_results     # noqa: E402
from database.models.core import AlgorithmVersion, Stock       # noqa: E402
from experiments._holdout import pre_cutoff_filter, cutoff_iso # noqa: E402

EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}   # Apex payoff (matches verify_scorecard)
PERIOD = "30d"
BARRIER = "30dte_apex"
EST_THR = 70      # estimate the sector-EV on the dense call-eligible population
EVAL_THR = 75     # evaluate predictive value on the tradable cohort
LAG_DAYS = 45     # calendar-day gap so the contributing signal's 30d barrier has resolved (PIT)
ROLL_DAYS = 252   # trailing window for the sector-EV estimate (~1 trading year, calendar approx 365)
ROLL_CAL = 372    # calendar days ~ 252 trading days
MIN_CELL = 30     # min contributing signals for a sector cell to carry a raw estimate


def welch_t(m1, s1, n1, m2, s2, n2):
    se = math.sqrt(s1 * s1 / max(n1, 1) + s2 * s2 / max(n2, 1))
    return (m1 - m2) / se if se > 0 else float("nan")


def _jnum(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def _load_labeled(vid, sample_cap):
    """Cached scores (>=EST_THR) joined to apex-EV labels + sector. Read-only."""
    sc = pl.read_parquet(materialize_polars(
        f"skill_v{vid}_allscores_2016",
        lambda: (_ for _ in ()).throw(RuntimeError(f"run skill.py first (skill_v{vid}_allscores_2016)"))))
    sc = pre_cutoff_filter(sc)                       # holdout discipline (identity while pre-cutoff)
    sc = sc.filter(pl.col("overall") >= EST_THR).select(["symbol", "date", "overall"])
    if sc.height > sample_cap:
        sc = sc.sample(n=sample_cap, seed=17)
        print(f"  uniform sample -> {sc.height:,}", flush=True)

    # sector map (small one-time DB read)
    sec_map = {r["symbol"]: (r["sector"] or None)
               for r in Stock.select(Stock.symbol, Stock.sector).dicts()}

    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(sc["symbol"], sc["date"])]
    print(f"  barrier join over {len(peaks):,} stock-days on {BARRIER}...", flush=True)
    results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set=BARRIER)
    evd, wrd = {}, {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EV:
            evd[(r["symbol"], dk)] = EV[sw["result"]]
            wrd[(r["symbol"], dk)] = 1 if sw["result"] == "win" else 0

    rows = []
    for sym, d, ov in zip(sc["symbol"], sc["date"], sc["overall"]):
        key = (sym, d)
        if key not in evd:
            continue
        sec = sec_map.get(sym)
        if not sec:                 # NULL sector = ETF; excluded
            continue
        rows.append((sym, d, int(ov), sec, float(evd[key]), int(wrd[key])))
    df = pl.DataFrame(rows, schema=["symbol", "date", "overall", "sector", "ev", "wr"], orient="row")
    df = df.with_columns(
        pl.col("date").str.to_date().alias("d"),
        pl.col("date").str.slice(0, 4).alias("yr"),
    ).sort("d")
    print(f"  labeled+sectored rows: {df.height:,} (uncached {skipped:,})", flush=True)
    return df


def _james_stein(cell_means, cell_ns, grand):
    """Shrink per-sector means toward `grand`. Returns dict sector->shrunk mean.

    JS factor 1 - (k-3)*sigma2_bar / sum((x-grand)^2), clipped to [0,1] per cell
    via a pooled-variance estimate; thin cells (small n) get a per-cell variance
    that is large -> stronger pull to grand.
    """
    secs = list(cell_means.keys())
    k = len(secs)
    if k < 4:
        return {s: grand for s in secs}
    # per-cell sampling variance proxy ~ pooled var / n
    var_proxy = {s: cell_means[s][1] / max(cell_ns[s], 1) for s in secs}  # (mean, var)->var/n
    sig2_bar = float(np.mean([var_proxy[s] for s in secs]))
    ss = sum((cell_means[s][0] - grand) ** 2 for s in secs)
    if ss <= 0:
        return {s: grand for s in secs}
    factor = max(0.0, 1.0 - (k - 3) * sig2_bar / ss)
    return {s: grand + factor * (cell_means[s][0] - grand) for s in secs}


def _sector_error_at(df_est, asof_d, grand_pool):
    """Strictly PIT-lagged shrunk sector-EV error as-of `asof_d`.

    Uses only EST-population signals whose 30d barrier resolved before asof_d
    (signal date <= asof_d - LAG_DAYS) and within the trailing ROLL_CAL window.
    Returns dict sector -> (shrunk_sector_EV - pooled_EV_over_window).
    """
    hi = asof_d - timedelta(days=LAG_DAYS)
    lo = hi - timedelta(days=ROLL_CAL)
    w = df_est.filter((pl.col("d") <= hi) & (pl.col("d") > lo))
    if w.height < 200:
        return {}, None
    pooled = float(w["ev"].mean())
    cell_means, cell_ns = {}, {}
    for sec, g in w.group_by("sector"):
        s = sec[0] if isinstance(sec, (tuple, list)) else sec
        n = g.height
        if n < MIN_CELL:
            continue
        cell_means[s] = (float(g["ev"].mean()), float(g["ev"].var() if n > 1 else 1.0))
        cell_ns[s] = n
    if not cell_means:
        return {}, pooled
    shrunk = _james_stein(cell_means, cell_ns, pooled)
    return {s: shrunk[s] - pooled for s in shrunk}, pooled


def run(vid=None, sample_cap=900000, verbose=True):
    if vid is None:
        vid = AlgorithmVersion.get_active_scores_version().id
    if isinstance(vid, str):
        vid = int(vid.lstrip("v"))

    def p(*a, **k):
        if verbose:
            print(*a, **k, flush=True)

    p(f"\n=== SECTOR-ERROR DIAGNOSTIC  v{vid}  barrier={BARRIER} ({PERIOD}) ===")
    p(f"  est-pop>={EST_THR}, eval-cohort>={EVAL_THR}, PIT lag={LAG_DAYS}d, "
      f"roll~{ROLL_CAL}cal-d, JS-shrink, min-cell={MIN_CELL}")
    p(f"  cutoff={cutoff_iso()} (read-only diagnostic; NOT a scoring change)\n")

    df = _load_labeled(vid, sample_cap)
    out = {"version": vid, "barrier": BARRIER, "period": PERIOD, "ev_map": dict(EV),
           "est_thr": EST_THR, "eval_thr": EVAL_THR, "lag_days": LAG_DAYS,
           "roll_cal_days": ROLL_CAL, "min_cell": MIN_CELL,
           "n_labeled": int(df.height), "cutoff": cutoff_iso()}

    df_eval = df.filter(pl.col("overall") >= EVAL_THR)
    out["n_eval"] = int(df_eval.height)
    grand_pool = float(df["ev"].mean())

    # --- 0. context: raw in-sample sector EV spread on 75+ (full-sample, NOT PIT) ---
    p("--- 0. CONTEXT: raw 75+ apex-EV by sector (full sample, look-ahead; for shape only) ---")
    p(f"{'sector':>24} | {'N':>6} | {'WR':>6} | {'EV':>8} | {'EV-pool':>8}")
    pooled75 = float(df_eval["ev"].mean())
    ctx = []
    for sec, g in sorted(df_eval.group_by("sector"), key=lambda kv: -kv[1]["ev"].mean()):
        s = sec[0] if isinstance(sec, (tuple, list)) else sec
        n = g.height
        ev = float(g["ev"].mean()); wr = float(g["wr"].mean()) * 100
        p(f"{s:>24} | {n:>6,} | {wr:5.1f}% | {ev*100:+7.2f}% | {(ev-pooled75)*100:+7.2f}")
        ctx.append({"sector": s, "n": n, "wr_pct": _jnum(wr * 1), "ev_pct": _jnum(ev * 100),
                    "ev_minus_pool_pp": _jnum((ev - pooled75) * 100)})
    out["context_75_raw"] = {"pooled_ev_pct": _jnum(pooled75 * 100), "sectors": ctx}
    p(f"  pooled 75+ EV = {pooled75*100:+.2f}%   (raw spread is the most a sector tilt could ever add)\n")

    # --- build the PIT sector-error feature for every EVAL row (walk-forward) ---
    p(f"--- 1. building PIT-lagged shrunk sector-error feature for {df_eval.height:,} eval rows ---")
    df_est = df  # estimate on the full 70+ population
    # cache the as-of error map per (date) to avoid recompute per row
    uniq_dates = df_eval["d"].unique().sort()
    asof_cache = {}
    for d in uniq_dates:
        asof_cache[d] = _sector_error_at(df_est, d, grand_pool)
    feat, kept_idx = [], []
    secs = df_eval["sector"].to_list(); ds = df_eval["d"].to_list()
    for i, (s, d) in enumerate(zip(secs, ds)):
        emap, _pool = asof_cache.get(d, ({}, None))
        if s in emap:
            feat.append(emap[s]); kept_idx.append(i)
    df_eval = df_eval.with_row_index("ridx")
    fmap = {kept_idx[j]: feat[j] for j in range(len(feat))}
    df_eval = df_eval.with_columns(
        pl.col("ridx").map_elements(lambda i: fmap.get(i, None), return_dtype=pl.Float64).alias("serr"))
    df_f = df_eval.filter(pl.col("serr").is_not_null())
    out["n_eval_with_feature"] = int(df_f.height)
    p(f"  eval rows with a PIT sector-error feature: {df_f.height:,}/{df_eval.height:,}\n")

    if df_f.height < 500:
        out["verdict"] = "INSUFFICIENT_N"
        return out

    serr = df_f["serr"].to_numpy()
    evv = df_f["ev"].to_numpy()
    ov = df_f["overall"].to_numpy()

    # --- 2. BEYOND-THE-SCORE (within-overall-bucket) test on full PIT sample ---
    # Hold `overall` fixed (per integer bucket), de-mean EV within bucket, then
    # correlate the residual EV with the sector-error term. If the score already
    # encodes this, the within-bucket correlation is ~0 (the score-norm trap).
    p("--- 2. BEYOND-THE-SCORE: within-overall-bucket EV residual vs sector-error ---")
    resid = np.full_like(evv, np.nan)
    for b in np.unique(ov):
        m = ov == b
        if m.sum() < 20:
            continue
        resid[m] = evv[m] - evv[m].mean()
    mm = ~np.isnan(resid)
    rs, ry = serr[mm], resid[mm]
    # correlation + its t-stat
    if rs.std() > 0 and ry.std() > 0:
        r = float(np.corrcoef(rs, ry)[0, 1])
        n = int(mm.sum())
        t = r * math.sqrt(max(n - 2, 1) / max(1 - r * r, 1e-9))
    else:
        r, t, n = float("nan"), float("nan"), int(mm.sum())
    # also a high-vs-low sector-error EV spread, within-bucket-residual space
    hi_e = rs >= np.quantile(rs, 0.8)
    lo_e = rs <= np.quantile(rs, 0.2)
    spread_pp = (ry[hi_e].mean() - ry[lo_e].mean()) * 100 if hi_e.sum() and lo_e.sum() else float("nan")
    p(f"  within-bucket corr(sector_error, EV_residual) r={r:+.4f}  t={t:+.2f}  N={n:,}")
    p(f"  top-20% vs bottom-20% sector-error -> within-bucket EV-residual spread {spread_pp:+.2f}pp")
    p(f"  (r~0 / t<2 => the score ALREADY encodes it; nothing orthogonal to add)\n")
    out["beyond_score"] = {"corr": _jnum(r), "t": _jnum(t), "n": n,
                           "hi_lo_resid_spread_pp": _jnum(spread_pp)}

    # --- 3. OUT-OF-SAMPLE, PER-REGIME-YEAR sign stability (the headline) ---
    # For each year, split the EVAL rows by the sign of the (already PIT-lagged,
    # so OOS-by-construction) sector-error feature and report the EV spread of
    # high-sector-error vs low-sector-error signals. Sign must be stable across
    # 2022 (bear) / 2023 (chop) / 2024 (bull) / 2025 to be anything but a
    # single-regime rotation artifact.
    p("--- 3. OUT-OF-SAMPLE per-regime EV spread (high vs low sector-error tertile) ---")
    p(f"{'year':>6} | {'N':>6} | {'EV_hi':>8} | {'EV_lo':>8} | {'spread':>8} | {'t':>6} | {'sign':>5}")
    yrs = df_f["yr"].to_numpy()
    per_year = []
    signs = []
    for y in sorted(set(yrs.tolist())):
        m = yrs == y
        if m.sum() < 120:
            continue
        sy, ey = serr[m], evv[m]
        q_hi = np.quantile(sy, 2 / 3.0)
        q_lo = np.quantile(sy, 1 / 3.0)
        hi = sy >= q_hi
        lo = sy <= q_lo
        if hi.sum() < 20 or lo.sum() < 20:
            continue
        sp = (ey[hi].mean() - ey[lo].mean()) * 100
        t = welch_t(ey[hi].mean(), ey[hi].std(), int(hi.sum()),
                    ey[lo].mean(), ey[lo].std(), int(lo.sum()))
        sgn = "+" if sp > 0 else ("-" if sp < 0 else "0")
        signs.append(1 if sp > 0 else (-1 if sp < 0 else 0))
        p(f"{y:>6} | {int(m.sum()):>6,} | {ey[hi].mean()*100:+7.2f}% | {ey[lo].mean()*100:+7.2f}% "
          f"| {sp:+7.2f} | {t:+5.2f} | {sgn:>5}")
        per_year.append({"year": y, "n": int(m.sum()), "ev_hi_pct": _jnum(ey[hi].mean() * 100),
                         "ev_lo_pct": _jnum(ey[lo].mean() * 100), "spread_pp": _jnum(sp), "t": _jnum(t)})
    out["per_year"] = per_year

    # multi-regime sign stability over the canonical regime years
    regime_years = [y for y in ("2022", "2023", "2024", "2025") if any(py["year"] == y for py in per_year)]
    reg = [py for py in per_year if py["year"] in regime_years]
    reg_signs = [1 if (py["spread_pp"] or 0) > 0 else (-1 if (py["spread_pp"] or 0) < 0 else 0) for py in reg]
    sig_reg = [py for py in reg if py["t"] is not None and abs(py["t"]) >= 2.0]
    all_same_sign = len(set(reg_signs)) == 1 and 0 not in reg_signs if reg_signs else False
    n_sig_consistent = sum(1 for py in sig_reg if (py["spread_pp"] or 0) > 0) if all_same_sign and reg_signs and reg_signs[0] > 0 \
        else (sum(1 for py in sig_reg if (py["spread_pp"] or 0) < 0) if all_same_sign else 0)

    # --- 4. pooled OOS beyond-score t (full PIT sample) is the single-number gate ---
    bs_t = out["beyond_score"]["t"]
    out["regime_years_present"] = regime_years
    out["regime_signs"] = reg_signs
    out["regime_all_same_sign"] = bool(all_same_sign)
    out["n_regime_years_sig"] = int(len(sig_reg))

    # ---- VERDICT ----
    # Default expectation: NULL. Declare a (weak) signal only if BOTH: the
    # within-bucket beyond-score t >= 2 (orthogonal to the score) AND the
    # per-regime spread sign is stable across all present regime years with
    # >=2 of them statistically significant (t>=2). Otherwise NULL / single-regime.
    beyond = (bs_t is not None and bs_t >= 2.0)
    stable = all_same_sign and len(sig_reg) >= 2
    if beyond and stable:
        verdict = "SIGNAL (orthogonal to score AND sign-stable multi-regime) — escalate to a careful Stage-1/3 test"
        vshort = "SIGNAL"
    elif beyond and not stable:
        verdict = "NULL (orthogonal info exists but is NOT sign-stable across regimes — single-regime artifact)"
        vshort = "NULL"
    elif (not beyond) and stable:
        verdict = "NULL (sign-stable spread is re-ranking the score, not orthogonal to it — score-norm trap)"
        vshort = "NULL"
    else:
        verdict = "NULL (no orthogonal info AND not sign-stable — default expectation confirmed)"
        vshort = "NULL"
    out["verdict"] = vshort
    out["verdict_long"] = verdict

    p(f"\n=== VERDICT ===")
    p(f"  beyond-score (within-bucket) t = {bs_t}  (need >=2 to be orthogonal to the score)")
    p(f"  regime years present: {regime_years}  signs: {reg_signs}  "
      f"all-same-sign={all_same_sign}  #sig(t>=2)={len(sig_reg)}")
    p(f"  {verdict}")
    p(f"\nNOTE: DIAGNOSTIC ONLY. No scoring/config/version change. The sector-error term must add info "
      f"BEYOND `overall` (within-bucket test) AND be sign-stable multi-regime; a single-regime "
      f"sector-rotation fit is the documented default failure mode.")

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=None, help="vNN; default = active scoring version")
    ap.add_argument("--sample", type=int, default=900000)
    ap.add_argument("--json", default=None, help="optional path to write the result JSON")
    args = ap.parse_args()
    res = run(vid=args.version, sample_cap=args.sample, verbose=True)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(res, f, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
