"""0b: the maintained Verification Substrate scorecard.

For a given scoring version, report ABSOLUTE skill on the LIVE APEX predictand
(30dte_apex barrier: TP +30% win @1.092s / SL -70% stop @2.548s, 30d window) —
not version-vs-version deltas. The headline metric is per-trade option EV under
the real +0.30/-0.70/-0.40 payoff, which exposes the brutal stop asymmetry the
generic 2sigma/5sigma barrier hides.

Reference baselines (the weather discipline): CLIMATOLOGY (random call) and
PERSISTENCE (12-1 momentum). A version has skill only if it beats both.

`run_scorecard(vid, barrier, sample, oos)` is the library entrypoint: it returns
a machine-readable dict of every number + the 0d verdict and (by default) writes
the standing artifact `.cache/algorithm_versions/v{vid}/research_pack/
verify_scorecard.json`. The CLI is a thin wrapper that prints the same scorecard.

Holdout discipline (known-issues #11): the in-sample window is filtered to
`<= strategy_config.CALIBRATION_CUTOFF_DATE` via experiments._holdout; `--oos`
reads the post-cutoff complement (renders INSUFFICIENT_N until that window fills,
~2026-12-15). The cutoff is LIVE ("2026-06-15"); the filter is identity while the
data ends before it, so the in-sample CLI numbers are unchanged.

Usage: python verify_scorecard.py [--version vNN] [--barrier 30dte_apex]
                                   [--sample N] [--oos] [--no-json]
Read-only; reuses the cached all-scores + price-feature parquets.
"""
import os
import sys
import math
import json
import argparse
from collections import namedtuple
from datetime import date as _date

import numpy as np
import polars as pl

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from database.bulk_cache import materialize_polars
from database.barrier_cache import peaks_to_swing_results, BARRIER_SETS
from database.models.core import AlgorithmVersion
from experiments._holdout import pre_cutoff_filter, cutoff_iso, DISABLED as _HOLDOUT_DISABLED

# option-EV mapping from a barrier fire (Apex payoff; v1 approximation — no theta-on-win,
# dead-hold, or slippage yet. Captures the win/stop/expire asymmetry, which is the point).
EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
PERIOD = "30d"  # ~ the 27-cal Apex hold


def two_prop_z(x1, n1, x2, n2):
    if n1 == 0 or n2 == 0:
        return float("nan")
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se > 0 else float("nan")


def welch_t(m1, s1, n1, m2, s2, n2):
    se = math.sqrt(s1 * s1 / n1 + s2 * s2 / n2)
    return (m1 - m2) / se if se > 0 else float("nan")


def _jnum(x):
    """JSON-safe number: float, or None for NaN/inf (so the dashboard JSON stays valid)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def _scorecard_json_path(vid):
    return os.path.join(_ROOT, ".cache", "algorithm_versions", f"v{vid}",
                        "research_pack", "verify_scorecard.json")


def _write_json(vid, out):
    p = _scorecard_json_path(vid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(out, f, indent=2)
    return p


def run_scorecard(vid=None, barrier="30dte_apex", sample=None, oos=False,
                  verbose=False, write_json=True):
    """Compute the 0b Verification-Substrate scorecard on the apex predictand.

    Returns a dict of all numbers + the 0d verdict. Pure compute; optionally
    prints the scorecard (verbose=True, used by the CLI) and writes the standing
    artifact JSON (write_json=True). `oos=True` reads the post-cutoff window.

    The in-sample (oos=False) verbose output is byte-for-byte identical to the
    pre-refactor CLI while live data ends before the holdout cutoff.
    """
    if vid is None:
        vid = AlgorithmVersion.get_active_scores_version().id
    if isinstance(vid, str):
        vid = int(vid.lstrip("v"))
    if sample is None:
        sample = int(os.environ.get("SAMPLE_N", "300000"))
    assert barrier in BARRIER_SETS, f"unknown barrier set {barrier}"
    bs = BARRIER_SETS[barrier]

    def _p(*a, **k):
        if verbose:
            print(*a, **k)

    out = {
        "version": vid, "barrier": barrier, "period": PERIOD,
        "oos": bool(oos), "in_sample": (not oos),
        "cutoff": cutoff_iso(), "holdout_disabled": bool(_HOLDOUT_DISABLED),
        "sample_cap": int(sample), "ev_map": dict(EV),
    }

    _p(f"\n=== VERIFY SCORECARD  version=v{vid}  barrier={barrier} "
       f"(call win {bs['k_low']}s / stop {bs['m_low']}s, {PERIOD}) ===", flush=True)

    # cached parquets from the diagnostics (no re-pull)
    sc = pl.read_parquet(materialize_polars(f"skill_v{vid}_allscores_2016",
                                            lambda: (_ for _ in ()).throw(RuntimeError(
                                                f"run skill.py first to cache skill_v{vid}_allscores_2016"))))

    # --- holdout split (BUILD-FIX, known-issues #11) ---
    # in-sample = dates <= CALIBRATION_CUTOFF_DATE; oos = the post-cutoff complement.
    # Identity for in-sample while live data ends before the live "2026-06-15" cutoff,
    # which keeps the verbose CLI numbers byte-for-byte — but explicit + future-proof,
    # and enables a standing OOS read once the forward window fills.
    out["n_total"] = int(sc.height)
    if oos:
        sc = sc.filter(pl.col("date") > cutoff_iso())
    else:
        sc = pre_cutoff_filter(sc)
    out["n_window"] = int(sc.height)

    if sc.height < 50:
        out["verdict"] = "INSUFFICIENT_N"
        out["note"] = (f"{'OOS' if oos else 'in-sample'} window has only {sc.height} rows "
                       f"(cutoff {cutoff_iso()}); accumulating.")
        _p(f"\n*** {out['note']} ***")
        if write_json:
            out["json_path"] = _write_json(vid, out)
        return out

    if sc.height > sample:
        sc = sc.sample(n=sample, seed=17)
        _p(f"uniform sample: {sc.height:,}", flush=True)

    # barrier join (force call-side) on the requested set
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(s, _date.fromisoformat(d), 75) for s, d in zip(sc["symbol"], sc["date"])]
    _p(f"barrier join over {len(peaks):,} stock-days on {barrier}...", flush=True)
    results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set=barrier)
    ev = {}; wr = {}
    for r in results:
        dk = r["date"]; dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
        sw = r.get("swing", {}).get(PERIOD)
        if sw and sw.get("result") in EV:
            ev[(r["symbol"], dk)] = EV[sw["result"]]
            wr[(r["symbol"], dk)] = 1 if sw["result"] == "win" else 0
    match = len(ev)
    out["match"] = int(match); out["match_pct"] = _jnum(match / sc.height * 100); out["uncached"] = int(skipped)
    _p(f"apex-barrier match: {match:,}/{sc.height:,} ({match/sc.height*100:.1f}%) [{skipped:,} uncached]")
    if match < 0.5 * sc.height:
        out["verdict"] = "LOW_MATCH"
        out["note"] = "30dte_apex backfill not complete yet"
        _p("\n*** LOW MATCH — 30dte_apex backfill not complete yet. Re-run after 0a lands. ***")
        if write_json:
            out["json_path"] = _write_json(vid, out)
        return out

    sc = sc.with_columns(
        ev=pl.struct(["symbol", "date"]).map_elements(lambda x: ev.get((x["symbol"], x["date"]), None),
                                                      return_dtype=pl.Float64),
        wr=pl.struct(["symbol", "date"]).map_elements(lambda x: wr.get((x["symbol"], x["date"]), -1),
                                                      return_dtype=pl.Int64),
    ).filter(pl.col("wr") >= 0)

    # attach momentum + fwd_ret from the price-feature parquet (round-2 build)
    try:
        pf = pl.read_parquet(materialize_polars("price_feats_raw_2015",
                                                lambda: (_ for _ in ()).throw(RuntimeError("missing"))))
        FWD = 10
        hmax = pl.max_horizontal([pl.col("high").shift(-k).over("symbol") for k in range(1, FWD + 1)])
        pf = pf.sort(["symbol", "date"]).with_columns(
            mom=(pl.col("close").shift(21).over("symbol") / pl.col("close").shift(252).over("symbol") - 1.0),
        ).select(["symbol", "date", "mom"])
        sc = sc.join(pf, on=["symbol", "date"], how="left")
        have_mom = True
    except Exception as e:
        _p(f"(momentum unavailable: {e})"); have_mom = False

    overall = sc["overall"].to_numpy()
    evv = sc["ev"].to_numpy().astype(float)
    wrv = sc["wr"].to_numpy().astype(float)

    cev, cwr, cn = evv.mean(), wrv.mean() * 100, len(evv)
    out["climatology"] = {"ev_pct": _jnum(cev * 100), "wr_pct": _jnum(cwr), "n": int(cn)}
    _p(f"\n--- 1. CLIMATOLOGY (random call on Apex payoff) ---")
    _p(f"  mean option EV = {cev*100:+.2f}%   WR = {cwr:.2f}%   (N={cn:,})")
    _p(f"  ^ the per-trade EV the score must beat. NEGATIVE EV here => the -70% stop")
    _p(f"    dominates the +30% win at the base rate (invisible on the generic barrier).\n")

    t_clim_75 = float("nan"); t_mom_75 = float("nan")
    out["buckets"] = []
    _p("--- 2. SCORE skill: mean option EV by bucket vs climatology ---")
    _p(f"{'bucket':>8} | {'N':>7} | {'WR':>6} | {'EV':>8} | {'EV vs clim':>10} | {'t':>6}")
    for thr in (70, 75, 80, 85, 90):
        m = overall >= thr
        if m.sum() < 5:
            continue
        bev, bn = evv[m], int(m.sum())
        t = welch_t(bev.mean(), bev.std(), bn, cev, evv.std(), cn)
        _p(f"{('>='+str(thr)):>8} | {bn:>7,} | {wrv[m].mean()*100:5.1f}% | {bev.mean()*100:+7.2f}% "
           f"| {(bev.mean()-cev)*100:+9.2f} | {t:+5.2f}")
        out["buckets"].append({"thr": thr, "n": bn, "wr_pct": _jnum(wrv[m].mean() * 100),
                               "ev_pct": _jnum(bev.mean() * 100),
                               "ev_vs_clim_pp": _jnum((bev.mean() - cev) * 100), "t": _jnum(t)})
        if thr == 75:
            t_clim_75 = t

    if have_mom and sc["mom"].is_not_null().sum() > 5000:
        mm = sc["mom"].is_not_null().to_numpy()
        mv, ev_m, sv = sc["mom"].to_numpy()[mm], evv[mm], overall[mm]
        K = int((sv >= 75).sum()); sel = K / len(sv)
        topm = mv >= np.quantile(mv, 1 - sel); s75 = sv >= 75
        t = welch_t(ev_m[s75].mean(), ev_m[s75].std(), int(s75.sum()),
                    ev_m[topm].mean(), ev_m[topm].std(), int(topm.sum()))
        _p(f"\n--- 3. vs PERSISTENCE: score>=75 EV {ev_m[s75].mean()*100:+.2f}%  "
           f"vs top-{sel*100:.1f}% momentum EV {ev_m[topm].mean()*100:+.2f}%   "
           f"diff {(ev_m[s75].mean()-ev_m[topm].mean())*100:+.2f}pp  t={t:+.2f}")
        t_mom_75 = t
        out["momentum"] = {"score75_ev_pct": _jnum(ev_m[s75].mean() * 100),
                           "topmom_ev_pct": _jnum(ev_m[topm].mean() * 100),
                           "topmom_sel_pct": _jnum(sel * 100),
                           "diff_pp": _jnum((ev_m[s75].mean() - ev_m[topm].mean()) * 100), "t": _jnum(t)}
        # beyond-momentum (EV, decile-matched)
        dec = np.quantile(mv, np.linspace(0, 1, 11)); ctrl = sv < 70
        exp = 0.0; var = 0.0; nh_tot = int(s75.sum())
        for i in range(10):
            lo, hi = dec[i], dec[i + 1]
            d = (mv >= lo) & (mv <= hi) if i == 9 else (mv >= lo) & (mv < hi)
            hm, lm = d & s75, d & ctrl
            if hm.sum() == 0 or lm.sum() == 0:
                continue
            exp += int(hm.sum()) * ev_m[lm].mean()
            var += int(hm.sum()) ** 2 * (ev_m[lm].var() / int(lm.sum()))
        act = ev_m[s75]; se = math.sqrt(act.var() / nh_tot + var / nh_tot ** 2)
        t = (act.mean() - exp / nh_tot) / se if se > 0 else float("nan")
        _p(f"--- 4. SKILL BEYOND MOMENTUM (EV): actual {act.mean()*100:+.2f}% vs "
           f"momentum-matched {exp/nh_tot*100:+.2f}%  = {(act.mean()-exp/nh_tot)*100:+.2f}pp  t={t:+.2f}")
        out["beyond_momentum"] = {"actual_pct": _jnum(act.mean() * 100),
                                  "matched_pct": _jnum(exp / nh_tot * 100),
                                  "diff_pp": _jnum((act.mean() - exp / nh_tot) * 100), "t": _jnum(t)}

    # ---- 0d GATE VERDICT: absolute skill floor on the predictand (75+) ----
    beats_clim = t_clim_75 >= 2.0
    beats_mom = t_mom_75 >= 2.0
    if beats_clim and beats_mom:
        verdict = "SHIP (beats climatology AND momentum on the payoff)"
    elif beats_mom:
        verdict = "FLAG (risk-shaper: beats momentum, only marginal vs climatology)"
    elif beats_clim:
        verdict = "FLAG (beats climatology, not momentum)"
    else:
        verdict = "BLOCK (no skill vs baselines)"
    out["gate"] = {"t_clim": _jnum(t_clim_75), "t_mom": _jnum(t_mom_75),
                   "beats_clim": bool(beats_clim), "beats_mom": bool(beats_mom),
                   "floor_t": 2.0, "verdict": verdict}
    out["verdict"] = verdict.split(" ", 1)[0]  # SHIP / FLAG / BLOCK
    _p(f"\n=== 0d GATE (75+, Apex EV) ===")
    _p(f"  vs-climatology t={t_clim_75:+.2f}  (floor t>=2)")
    _p(f"  vs-momentum    t={t_mom_75:+.2f}  (floor t>=2)")
    _p(f"  VERDICT: {verdict}")

    _p(f"\nNOTE: EV map win/stop/expire = +0.30/-0.70/-0.40 (Apex payoff, v1 — no theta-on-win/"
       f"dead-hold/slippage yet). In-sample. The vs-baseline floor above IS the operational 0d gate.")

    if write_json:
        out["json_path"] = _write_json(vid, out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=None, help="vNN; default = active")
    ap.add_argument("--barrier", default="30dte_apex")
    ap.add_argument("--sample", type=int, default=int(os.environ.get("SAMPLE_N", "300000")))
    ap.add_argument("--oos", action="store_true", help="out-of-sample (post-cutoff) window")
    ap.add_argument("--no-json", action="store_true", help="skip writing the standing artifact")
    args = ap.parse_args()
    run_scorecard(vid=args.version, barrier=args.barrier, sample=args.sample,
                  oos=args.oos, verbose=True, write_json=not args.no_json)


if __name__ == "__main__":
    main()
