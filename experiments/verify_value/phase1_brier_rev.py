"""Phase 1 (keystone): forecast-verification decomposition + economic value, on the Apex predictand.

Three read-only analyses on v74 (active), 30dte_apex barrier (+0.30/-0.70/-0.40 EV), holdout-filtered:

  A. RESOLUTION MAP (Murphy/Brier, within-70+, calibration-free).
     Brier = Reliability - Resolution + Uncertainty. We compute Resolution &
     Uncertainty (no calibration assumption) over the tradable 70+ cohort, banded,
     to locate WHERE the score discriminates. potential-BSS = Resolution/Uncertainty
     = the skill available IF perfectly calibrated. Prediction under test: resolution
     concentrates in 85+/90+ and ~0 in 75-84.

  B. CALIBRATION GENERALIZATION (the #2 decider).
     Time-split the 70+ cohort (train=early, test=late, both pre-cutoff). Bin by
     score decile on TRAIN, carry the train-WR as the served probability, measure
     the OUT-OF-FOLD reliability on TEST = freq-weighted (P_train - WR_test)^2.
     Small => a served score->P(win) map generalizes (calibration is clean, not mud).
     Resolution is invariant to monotone calibration, so this is the WHOLE value of
     post-processing: does the map hold up out-of-fold. (Whether to SIZE off P is a
     separate Stage-3 MC question; this only certifies the map.)

  C. RELATIVE ECONOMIC VALUE (cost-loss), the rigorous apex-EV gate.
     At each score threshold tau: selectivity s, mean apex EV of score>=tau, vs the
     random-selection (climatology) and oracle (best-s-by-outcome) references at the
     same selectivity. REV = (EV_score - EV_clim)/(EV_oracle - EV_clim). Then STRESS
     the stop leg (-0.70 -> -0.80) and show how fast value at the operating point decays.

Read-only. In-sample (data ends < cutoff). EV v1 (no theta-on-win/dead-hold/slippage).
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

from database.bulk_cache import materialize_polars, cache_path
from database.barrier_cache import peaks_to_swing_results
from database.models.core import AlgorithmVersion
from experiments._holdout import pre_cutoff_filter, cutoff_iso

EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
EV_STRESS = {"win": +0.30, "stop": -0.80, "expire": -0.40}  # worse stop fill
PERIOD = "30d"
VID = AlgorithmVersion.get_active_scores_version().id
BANDS = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 100)]


def apex_join(symbols, dates):
    """Return dicts keyed (symbol,date) -> result in {win,stop,expire} via the apex barrier."""
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(symbols, dates)]
    results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    res = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EV:
            res[(r["symbol"], dk)] = sw["result"]
    return res, skipped


def main():
    out = {"version": VID, "barrier": "30dte_apex", "ev_map": EV, "cutoff": cutoff_iso()}
    print(f"\n################ PHASE 1  v{VID}  30dte_apex  (in-sample, cutoff {cutoff_iso()}) ################", flush=True)

    # ---- tradable 70+ cohort (full, accurate tail) ----
    df = pre_cutoff_filter(pl.read_parquet(cache_path("vv_v74_70plus")))
    res, sk = apex_join(df["symbol"], df["date"])
    df = df.with_columns(
        outc=pl.struct(["symbol", "date"]).map_elements(lambda x: res.get((x["symbol"], x["date"])), return_dtype=pl.Utf8)
    ).filter(pl.col("outc").is_not_null())
    df = df.with_columns(
        win=(pl.col("outc") == "win").cast(pl.Int8),
        ev=pl.col("outc").replace_strict(EV, default=None, return_dtype=pl.Float64),
        evs=pl.col("outc").replace_strict(EV_STRESS, default=None, return_dtype=pl.Float64),
    )
    sc = df["overall"].to_numpy(); win = df["win"].to_numpy().astype(float)
    ev = df["ev"].to_numpy(); evs = df["evs"].to_numpy()
    n70 = len(sc)
    print(f"70+ cohort (pre-cutoff, apex-matched): {n70:,}  [{sk:,} uncached]", flush=True)

    # ================= A. RESOLUTION MAP (within-70+) =================
    obar = win.mean()
    unc = obar * (1 - obar)
    print(f"\n=== A. RESOLUTION MAP (within-70+; Murphy/Brier, calibration-free) ===")
    print(f"  70+ base rate o_bar (P win) = {obar*100:.2f}%   Uncertainty = o_bar(1-o_bar) = {unc:.4f}")
    print(f"  {'band':>8} | {'N':>7} | {'freq':>6} | {'WR(win)':>8} | {'EV':>8} | {'(WR-base)':>9} | {'res_k':>8}")
    res_total = 0.0; bands_out = []
    for lo, hi in BANDS:
        m = (sc >= lo) & (sc <= hi); nk = int(m.sum())
        if nk == 0: continue
        fk = nk / n70; ok = win[m].mean(); evk = ev[m].mean()
        rk = fk * (ok - obar) ** 2; res_total += rk
        print(f"{lo}-{hi:>3} | {nk:>7,} | {fk*100:5.1f}% | {ok*100:6.2f}% | {evk*100:+7.2f}% | {(ok-obar)*100:+8.2f} | {rk:.5f}")
        bands_out.append({"band": f"{lo}-{hi}", "n": nk, "freq": fk, "wr": ok, "ev": evk, "res_k": rk})
    pot_bss = res_total / unc if unc > 0 else float("nan")
    print(f"  Resolution(within-70+) = {res_total:.5f}   potential-BSS = Res/Unc = {pot_bss:.4f}")
    print(f"  ^ fraction of the within-70+ resolution carried by each band's res_k shows WHERE the skill lives.")
    if bands_out:
        sh = sorted(bands_out, key=lambda b: -b["res_k"])
        print(f"    top resolution band: {sh[0]['band']} ({sh[0]['res_k']/res_total*100:.0f}% of within-70+ resolution)")
    out["resolution"] = {"obar": obar, "uncertainty": unc, "resolution": res_total,
                         "potential_bss": pot_bss, "bands": bands_out}

    # ================= B. CALIBRATION GENERALIZATION (the #2 decider) =================
    print(f"\n=== B. CALIBRATION GENERALIZATION (out-of-fold reliability of a served score->P map) ===")
    dts = df["date"].to_numpy()
    order = np.argsort(dts)
    split = dts[order][int(0.6 * n70)]  # ~60/40 time split
    tr = dts < split; te = ~tr
    print(f"  train = dates < {split} (N={int(tr.sum()):,})   test = dates >= {split} (N={int(te.sum()):,})")
    # decile bins of score on TRAIN; served prob per bin = train WR; reliability on TEST
    qs = np.quantile(sc[tr], np.linspace(0, 1, 11))
    qs[0] = -np.inf; qs[-1] = np.inf
    rel_test = 0.0; n_te = int(te.sum()); rows = []
    base_test = win[te].mean()
    brier_cal = 0.0  # Brier of the calibrated forecast on test
    for i in range(10):
        b_tr = (sc[tr] >= qs[i]) & (sc[tr] < qs[i + 1])
        b_te = (sc[te] >= qs[i]) & (sc[te] < qs[i + 1])
        if b_tr.sum() < 30 or b_te.sum() < 30:
            ptr = win[tr][b_tr].mean() if b_tr.sum() else float("nan")
            continue
        p_tr = win[tr][b_tr].mean()          # served probability for this score-bin
        wr_te = win[te][b_te].mean()         # observed test frequency
        nke = int(b_te.sum())
        rel_test += nke * (p_tr - wr_te) ** 2
        brier_cal += ((p_tr - win[te][b_te]) ** 2).sum()
        rows.append({"bin": i + 1, "score_lo": float(qs[i]) if np.isfinite(qs[i]) else 70.0,
                     "p_served": p_tr, "wr_test": wr_te, "n_test": nke})
        print(f"  bin{i+1:>2}: score>={qs[i] if np.isfinite(qs[i]) else 70:5.1f}  served P={p_tr*100:5.2f}%  test WR={wr_te*100:5.2f}%  (n_te={nke:,})  gap={ (p_tr-wr_te)*100:+5.2f}pp")
    rel_test /= n_te
    brier_cal /= n_te
    brier_clim = base_test * (1 - base_test)  # always-forecast-base-rate Brier on test
    print(f"  OUT-OF-FOLD RELIABILITY (test) = {rel_test:.5f}   (smaller = served map generalizes; ~0 => clean)")
    print(f"  test Brier(calibrated) = {brier_cal:.4f}  vs  test Brier(climatology) = {brier_clim:.4f}  "
          f"-> BSS_oof = {1 - brier_cal/brier_clim if brier_clim>0 else float('nan'):+.4f}")
    print(f"  ^ #2 verdict: reliability near 0 => a post-hoc P(win) lookup is honest & reproducible out-of-fold;")
    print(f"    large reliability => the calibration itself overfits (do NOT serve it). Resolution (A) is unchanged either way.")
    out["calibration"] = {"split": str(split), "reliability_oof": rel_test,
                          "brier_calibrated": brier_cal, "brier_clim": brier_clim,
                          "bss_oof": (1 - brier_cal / brier_clim) if brier_clim > 0 else None, "bins": rows}

    # ================= C. RELATIVE ECONOMIC VALUE (cost-loss) =================
    # universe sample for climatology base rate + oracle curve
    uni = pre_cutoff_filter(pl.read_parquet(cache_path("skill_v74_allscores_2016")))
    UN = min(200_000, uni.height)
    uni = uni.sample(n=UN, seed=17)
    ures, usk = apex_join(uni["symbol"], uni["date"])
    uni = uni.with_columns(
        outc=pl.struct(["symbol", "date"]).map_elements(lambda x: ures.get((x["symbol"], x["date"])), return_dtype=pl.Utf8)
    ).filter(pl.col("outc").is_not_null())
    u_sc = uni["overall"].to_numpy()
    u_ev = uni["outc"].replace_strict(EV, default=None, return_dtype=pl.Float64).to_numpy()
    u_evs = uni["outc"].replace_strict(EV_STRESS, default=None, return_dtype=pl.Float64).to_numpy()
    Nu = len(u_ev)
    ev_clim = u_ev.mean(); ev_clim_s = u_evs.mean()
    print(f"\n=== C. RELATIVE ECONOMIC VALUE (cost-loss) ===  universe sample N={Nu:,} [{usk:,} uncached]")
    print(f"  CLIMATOLOGY (random call) mean apex EV = {ev_clim*100:+.2f}%   (stress stop: {ev_clim_s*100:+.2f}%)")

    def rev_at(thr, ev_uni, ev_clim_):
        sel_mask = u_sc >= thr
        s = sel_mask.mean()                       # selectivity
        if s <= 0: return None
        ev_score = ev_uni[sel_mask].mean()        # EV of trading score>=thr
        k = max(1, int(round(s * Nu)))
        ev_oracle = np.sort(ev_uni)[::-1][:k].mean()  # best-s-by-outcome
        denom = ev_oracle - ev_clim_
        rev = (ev_score - ev_clim_) / denom if denom > 0 else float("nan")
        return s, ev_score, ev_oracle, rev

    print(f"  {'tau':>4} | {'sel%':>6} | {'EV_score':>9} | {'EV-clim':>8} | {'EV_oracle':>10} | {'REV':>7} | {'REV(stop-stress)':>16}")
    rev_rows = []
    for thr in (70, 72, 75, 78, 80, 82, 85, 88, 90):
        r = rev_at(thr, u_ev, ev_clim)
        rs = rev_at(thr, u_evs, ev_clim_s)
        if r is None: continue
        s, evsc, evor, rev = r
        revs = rs[3] if rs else float("nan")
        print(f"{thr:>4} | {s*100:5.2f}% | {evsc*100:+8.2f}% | {(evsc-ev_clim)*100:+7.2f} | {evor*100:+9.2f}% | {rev:+6.3f} | {revs:+15.3f}")
        rev_rows.append({"tau": thr, "sel": s, "ev_score": evsc, "ev_vs_clim": evsc - ev_clim,
                         "ev_oracle": evor, "rev": rev, "rev_stress": revs})
    print(f"  ^ REV>0 => the score's selection beats random at that selectivity; the stop-stress column is the")
    print(f"    fragility to a worse-than-modeled -70% fill (the asymmetry your whole edge rides on).")
    out["rev"] = {"ev_clim": ev_clim, "ev_clim_stress": ev_clim_s, "n_universe": Nu, "rows": rev_rows}

    jp = os.path.join(os.path.dirname(__file__), "phase1_results.json")
    with open(jp, "w") as f:
        json.dump(out, f, indent=2, default=float)
    print(f"\nwrote {jp}")


if __name__ == "__main__":
    main()
