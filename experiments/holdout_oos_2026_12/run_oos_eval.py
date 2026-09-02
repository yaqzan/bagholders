#!/usr/bin/env python
"""December-2026 OOS evaluator -- mechanical H1-H6 verdict skeleton.

Part of the pre-registered 2026-12-15 OOS evaluation package
(experiments/holdout_oos_2026_12/PREREGISTRATION.md, gameplan.md row P1.6). Loads the FROZEN
reference numbers from references.json, computes H1-H6 against LIVE data, and prints a
per-hypothesis verdict table. Refuses a full evaluation before 2026-12-15 unless --force-early
(and even then, loudly marks the result as not-the-pre-registered-read).

Two modes:
  --selftest      Confirms every reference loads and every data source is reachable TODAY.
                   Deliberately does NOT compute or print any post-cutoff-derived NUMBER beyond a
                   bare row count (accruing-N context only -- no EV/WR/verdict). Safe to run any
                   time; this is how the package stays testable without peeking at the OOS read.
  (no --selftest)  The real evaluation. Gated on today's date >= 2026-12-15 (or --force-early).

This is a SKELETON per its own charter (DECEMBER-PREREGISTRATION Block C task), amended per the
FABLE rulings of 2026-07-13 (PREREGISTRATION.md section 8): every hypothesis computable from
existing, already-proven repo tooling is implemented for real (H1, H2, H4, H5-stage-1,
H6 lines (a)+(b), H3-via-envelope-file). Pre-registered pieces deliberately NOT built yet:
EXT_H5_DD_ABLATION (H5 stage 2 -- runs only on a t>=2-confirmed stage-1 regret flag, per RULING
OQ-4) and the SVR per-signal lever-drift slot (marked measured_at_eval_time, per RULING OQ-5's
cheap-only rule).

Usage:
    python experiments/holdout_oos_2026_12/run_oos_eval.py --selftest
    python experiments/holdout_oos_2026_12/run_oos_eval.py                     # refuses before 2026-12-15
    python experiments/holdout_oos_2026_12/run_oos_eval.py --force-early       # early rehearsal, loudly marked
    python experiments/holdout_oos_2026_12/run_oos_eval.py --hypothesis H1     # single hypothesis
    python experiments/holdout_oos_2026_12/run_oos_eval.py --marking-verified  # attest the H3 marking
                                                                               # pre-step ran clean (RULING OQ-3a)
    python experiments/holdout_oos_2026_12/run_oos_eval.py --freeze-lever-reference
                                                                               # authoring/reproduction utility:
                                                                               # prints the H6 line-(a) IN-SAMPLE
                                                                               # reference block (reads only
                                                                               # pre-cutoff data; never writes)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Worktree-safety pattern (traps.md "Worktree PYTHONPATH trap"): pin sys.path[0] to THIS file's
# repo root, never trust an inherited global PYTHONPATH that might point at a different checkout.
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
assert (ROOT / "strategy_config.py").exists(), f"unexpected ROOT resolution: {ROOT}"
sys.path.insert(0, str(ROOT))

EVAL_DATE = date(2026, 12, 15)
EXTENSION_DATE = date(2027, 6, 15)
REFERENCES_PATH = HERE / "references.json"
ENVELOPE_PATH = HERE / "envelope_h3.json"
RESULTS_DIR = HERE / "results"
PARITY_HARNESS = ROOT / "experiments" / "portfolio_engine_parity" / "validate.py"

API_BASE = "http://127.0.0.1:5000"
API_TIMEOUT_S = 5

# Runtime options threaded from main(). RULING OQ-3a (FABLE, 2026-07-13): the December H3 read
# MUST verify ledger-marking integrity FIRST (the known MTM/pending_requal divergence class,
# via experiments/portfolio_engine_parity/validate.py) before adjudicating any breach;
# --marking-verified attests that pre-step ran clean.
_OPTS = {"marking_verified": False}

# Apex option-EV map -- identical to experiments/skill_vs_baseline/verify_scorecard.py's EV
# constant (the 30dte_apex predictand payoff). Used by H6 line (a)'s apex-EV-on-active-days.
APEX_EV = {"win": +0.30, "stop": -0.70, "expire": -0.40}
APEX_PERIOD = "30d"


@dataclass
class HypothesisResult:
    id: str
    verdict: str
    detail: dict = field(default_factory=dict)
    consequence: str = ""
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {"id": self.id, "verdict": self.verdict, "detail": self.detail,
                "consequence": self.consequence, "error": self.error}


@dataclass
class ReachabilityResult:
    id: str
    reachable: bool
    detail: str


def load_references() -> dict:
    with open(REFERENCES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _active_version_id() -> int:
    from database.models.core import AlgorithmVersion
    return AlgorithmVersion.get_active_scores_version().id


def _fetch_live_portfolio_state() -> Optional[dict]:
    """GET /api/portfolio/state, stdlib-only (no new dependency). Returns None on any failure --
    callers must handle that as LIVE_UNREACHABLE, never silently substitute a default."""
    try:
        req = urllib.request.Request(f"{API_BASE}/api/portfolio/state")
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _welch_t(m1, s1, n1, m2, s2, n2):
    se = math.sqrt((s1 * s1) / n1 + (s2 * s2) / n2) if n1 and n2 else float("nan")
    return (m1 - m2) / se if se and se > 0 else float("nan")


# ================================================================================================
# H1 -- 0d-gate OOS
# ================================================================================================

def check_h1_reachable(refs: dict) -> ReachabilityResult:
    try:
        from experiments.skill_vs_baseline.verify_scorecard import run_scorecard
    except Exception as e:
        return ReachabilityResult("H1", False, f"import failed: {e}")
    try:
        vid = _active_version_id()
        out = run_scorecard(vid=vid, oos=True, sample=5000, write_json=False, verbose=False)
        # selftest discloses ONLY the accruing row count -- never an EV/WR/t/verdict value, so
        # running --selftest repeatedly before 2026-12-15 cannot leak directional information.
        n = out.get("n_window")
        return ReachabilityResult("H1", True, f"run_scorecard(vid={vid}, oos=True) reachable; "
                                               f"OOS n_window={n} (accruing, not a verdict)")
    except Exception as e:
        return ReachabilityResult("H1", False, f"call failed: {e}\n{traceback.format_exc(limit=2)}")


def compute_h1(refs: dict) -> HypothesisResult:
    from experiments.skill_vs_baseline.verify_scorecard import run_scorecard
    h1ref = refs["hypotheses"]["H1"]
    vid = _active_version_id()
    out = run_scorecard(vid=vid, oos=True, sample=300000, write_json=False, verbose=False)
    if out.get("verdict") == "INSUFFICIENT_N":
        return HypothesisResult("H1", "INSUFFICIENT_N", out,
                                 "OOS window too thin (<50 matched rows) -- accumulate further, no verdict yet.")
    gate = out["gate"]
    n75 = next((b["n"] for b in out.get("buckets", []) if b["thr"] == 75), 0)
    verdict = gate["verdict"]  # SHIP / FLAG / BLOCK
    detail = {"n75": n75, "t_clim": gate["t_clim"], "t_mom": gate["t_mom"],
              "oos_n_window": out.get("n_window"), "active_version": vid}
    if verdict != "BLOCK":
        consequence = {"SHIP": "exceeds expectations; note only",
                        "FLAG": "accepted, no auto-revert (expected outcome -- v69-v74 all FLAG in-sample)"}[verdict]
    else:
        decisive = (n75 >= 300) and (gate["t_mom"] < 1.0)
        if decisive:
            consequence = ("ESCALATE: freeze new Stage-1/Stage-3 ships; surface to FABLE/user "
                            "for a de-risking decision (PREREGISTRATION.md section 4 H1)")
        else:
            consequence = (f"SINGLE-WINDOW MISS (N={n75}, t_mom={gate['t_mom']:.2f}) -- document, "
                            f"do NOT escalate, extend observation to {EXTENSION_DATE.isoformat()}")
    return HypothesisResult("H1", verdict, detail, consequence)


# ================================================================================================
# H2 -- apex-EV vs climatology floor
# ================================================================================================

def check_h2_reachable(refs: dict) -> ReachabilityResult:
    # Shares H1's data source entirely -- if H1 is reachable, H2 is too.
    r = check_h1_reachable(refs)
    return ReachabilityResult("H2", r.reachable, f"(shares H1's source) {r.detail}")


def compute_h2(refs: dict) -> HypothesisResult:
    from experiments.skill_vs_baseline.verify_scorecard import run_scorecard
    floor = refs["hypotheses"]["H2"]["frozen_climatology_floor_pct"]
    vid = _active_version_id()
    out = run_scorecard(vid=vid, oos=True, sample=300000, write_json=False, verbose=False)
    if out.get("verdict") == "INSUFFICIENT_N":
        return HypothesisResult("H2", "INSUFFICIENT_N", out, "accumulate further, no verdict yet.")
    b75 = next((b for b in out.get("buckets", []) if b["thr"] == 75), None)
    ev = b75["ev_pct"] if b75 else None
    oos_clim = out.get("climatology", {}).get("ev_pct")
    verdict = "PASS" if (ev is not None and ev >= floor) else "FLAG"
    detail = {"oos_75plus_ev_pct": ev, "frozen_climatology_floor_pct": floor,
              "oos_window_own_climatology_pct": oos_clim}
    consequence = ("context-only, no BLOCK tier" if verdict == "PASS" else
                   "FLAG -- OOS 75+ apex-EV point estimate fell below the frozen in-sample "
                   "climatology floor; cross-check oos_window_own_climatology_pct for a "
                   "regime-shift read before treating this as a scoring problem")
    return HypothesisResult("H2", verdict, detail, consequence)


# ================================================================================================
# H3 -- live ledger inside the MC envelope
# ================================================================================================

def check_h3_reachable(refs: dict) -> ReachabilityResult:
    problems = []
    if not ENVELOPE_PATH.exists():
        problems.append(f"{ENVELOPE_PATH.name} missing -- run run_h3_envelope.py first")
    else:
        try:
            with open(ENVELOPE_PATH, "r", encoding="utf-8") as f:
                env = json.load(f)
            if "percentile_bands" not in env:
                problems.append("envelope_h3.json missing 'percentile_bands' key")
        except Exception as e:
            problems.append(f"envelope_h3.json unreadable: {e}")
    live = _fetch_live_portfolio_state()
    if live is None:
        problems.append(f"{API_BASE}/api/portfolio/state unreachable")
    elif "summary" not in live or "run" not in live:
        problems.append("live portfolio payload missing expected keys ('summary'/'run')")
    # RULING OQ-3a: the marking-verification pre-step's harness must exist on disk.
    if not PARITY_HARNESS.exists():
        problems.append(f"marking-verification harness missing: {PARITY_HARNESS}")
    # RULING OQ-7: the piecewise segments structure must load.
    segs = (refs["hypotheses"]["H3"].get("segments") or [])
    if not segs:
        problems.append("references.json H3.segments missing/empty (piecewise protocol, RULING OQ-7)")
    if problems:
        return ReachabilityResult("H3", False, "; ".join(problems))
    return ReachabilityResult("H3", True, f"envelope file + live API + parity harness reachable; "
                                           f"{len(segs)} pre-registered segment(s)")


def compute_h3(refs: dict) -> HypothesisResult:
    # RULING OQ-7 (FABLE, 2026-07-13): H3 is PIECEWISE on live-profile changes. references.json
    # carries a "segments" list (one entry per profile-era). With one segment (no profile change
    # since 2026-06-01) the whole-run summary path below applies. If a profile change lands before
    # the eval, a new segment is frozen at switch time -- and per-segment live metrics must then
    # come from equity-curve slicing (spec in PREREGISTRATION.md section 4 H3), which this
    # skeleton deliberately does not implement until a second segment actually exists.
    segments = (refs["hypotheses"]["H3"].get("segments") or [])
    if len(segments) > 1:
        return HypothesisResult(
            "H3", "MULTI_SEGMENT_NOT_IMPLEMENTED",
            {"n_segments": len(segments), "segments": segments},
            "A live-profile change split the H3 window into multiple pre-registered segments. "
            "Implement the per-segment evaluation at eval time per PREREGISTRATION.md section 4 "
            "H3 (equity-curve slicing per segment against that segment's own frozen envelope; "
            "overall verdict = worst segment) before adjudicating.")

    if not ENVELOPE_PATH.exists():
        return HypothesisResult("H3", "MISSING_ENVELOPE", {},
                                 f"{ENVELOPE_PATH.name} not found -- re-run run_h3_envelope.py "
                                 f"(same frozen recipe, --win-end extended toward the eval date) "
                                 f"before evaluating H3.")
    with open(ENVELOPE_PATH, "r", encoding="utf-8") as f:
        envelope = json.load(f)

    live = _fetch_live_portfolio_state()
    if live is None:
        return HypothesisResult("H3", "LIVE_UNREACHABLE", {},
                                 f"{API_BASE}/api/portfolio/state unreachable -- start the trader-api "
                                 f"server (see .claude/skills/frontend-ops/SKILL.md) and retry.")

    summary = live.get("summary", {})
    live_ret = summary.get("total_return_pct")
    live_dd = summary.get("max_dd_pct")
    ret_band = envelope["percentile_bands"]["finals_pct_return"]
    dd_band = envelope["percentile_bands"]["dds_pct_drawdown"]
    ret_p05, ret_p95 = ret_band.get("p05"), ret_band.get("p95")
    dd_p05, dd_p95 = dd_band.get("p05"), dd_band.get("p95")

    if live_ret is None or live_dd is None:
        verdict = "INCOMPLETE"
        consequence = "live portfolio payload missing return/DD fields -- check API shape."
    else:
        # DD-primary + DIRECTION-AWARE: only DD *above* p95 (worse than modeled) or return
        # *below* p05 (worse than modeled) are the decision-relevant misses. A DD *below* p05
        # (a lower/better drawdown than the model's tightest cluster) or a return *above* p95
        # is a favorable outlier, not a fidelity concern -- do not FLAG a good outcome.
        dd_worse = (dd_p95 is not None and live_dd > dd_p95)
        dd_better = (dd_p05 is not None and live_dd < dd_p05)
        ret_worse = (ret_p05 is not None and live_ret < ret_p05)
        ret_better = (ret_p95 is not None and live_ret > ret_p95)

        breach = dd_worse or ret_worse
        if breach and not _OPTS.get("marking_verified"):
            # RULING OQ-3a: marking-verification is a MANDATORY pre-step before adjudicating any
            # breach (the known MTM/pending_requal ~$295 display-divergence class could otherwise
            # masquerade as an engine-fidelity breach). A PASS needs no such gate -- there is no
            # breach to adjudicate.
            verdict = "BREACH_PENDING_MARKING_VERIFICATION"
            consequence = ("A band breach is indicated, but ledger-marking integrity has not been "
                            "attested for this run. Run the parity harness first "
                            "(python experiments/portfolio_engine_parity/validate.py -- positions/"
                            "closes/pnl must be bit-exact; the known ~$295 MTM-curve divergence is "
                            "display-only and must be confirmed still display-only), then re-run "
                            "with --marking-verified. RULING OQ-3a (FABLE, 2026-07-13).")
        elif dd_worse:
            verdict = "FLAG_DD"
            consequence = ("Live max DD EXCEEDS the modeled p95 (marking-verified) -- triggers a "
                            "MANDATORY INVESTIGATION of engine/fill fidelity (P3.7 real-fill log "
                            "if it exists by eval time), NOT a ship freeze (RULING OQ-6, FABLE "
                            "2026-07-13). Not automatically a verdict on the score's own edge "
                            "(that's H1/H2/H4).")
        elif ret_worse:
            verdict = "NOTE_RETURN_LOW"
            consequence = ("Live return is below the modeled p05 but DD is not above p95 "
                            "(marking-verified) -- reported as context per the DD-primary framing, "
                            "not gated. Still worth a look given the framing's own noise caveat "
                            "(thin-window bands can be tight; a large gap is not automatically "
                            "boundary noise).")
        else:
            verdict = "PASS"
            notes = []
            if dd_better:
                notes.append("DD better than modeled p05")
            if ret_better:
                notes.append("return better than modeled p95")
            consequence = "within (or favorably outside) the modeled envelope" + \
                          (f" ({'; '.join(notes)})" if notes else "")

    detail = {
        "envelope_window": envelope.get("window"),
        "live_total_return_pct": live_ret, "live_max_dd_pct": live_dd,
        "return_band_p05_p95": [ret_p05, ret_p95],
        "dd_band_p05_p95": [dd_p05, dd_p95],
        "run_profile": live.get("run", {}).get("profile"),
        "marking_verified": bool(_OPTS.get("marking_verified")),
        "n_segments": len(segments) or 1,
    }
    return HypothesisResult("H3", verdict, detail, consequence)


# ================================================================================================
# H4 -- WR15 within CI
# ================================================================================================

BAND_EDGES = [("75-79", 75, 80), ("80-84", 80, 85), ("85-89", 85, 90),
              ("90-94", 90, 95), ("95-100", 95, 101)]


def check_h4_reachable(refs: dict) -> ReachabilityResult:
    try:
        from database.trader_database import DB
        from database.barrier_cache import peaks_to_swing_results, BARRIER_SETS
        assert "30dte_generic" in BARRIER_SETS
        cur = DB.execute_sql("SELECT COUNT(*) FROM scores WHERE date > %s LIMIT 1",
                              (refs["frozen_anchors"]["calibration_cutoff_date"],))
        n = cur.fetchone()[0]
        return ReachabilityResult("H4", True, f"DB + barrier_cache reachable; "
                                               f"{n} OOS score rows exist (accruing, not a verdict)")
    except Exception as e:
        return ReachabilityResult("H4", False, f"failed: {e}\n{traceback.format_exc(limit=2)}")


def _wilson_ci(wr: float, n: int, z: float = 1.96):
    """Normal-approx CI (the formula PREREGISTRATION.md section 4 H4 specifies): wr +/- z*SE."""
    if not n:
        return (None, None)
    se = math.sqrt(max(wr * (1 - wr), 0.0) / n)
    return (wr - z * se, wr + z * se)


def compute_h4(refs: dict) -> HypothesisResult:
    from database.trader_database import DB
    from database.barrier_cache import peaks_to_swing_results
    from collections import namedtuple
    from datetime import date as _date

    cutoff = refs["frozen_anchors"]["calibration_cutoff_date"]
    vid = _active_version_id()
    in_sample = refs["hypotheses"]["H4"]["in_sample_reference_call_bands"]
    n_floor = refs["hypotheses"]["H4"]["n_floor_for_gating"]

    cur = DB.execute_sql(
        "SELECT symbol, date, overall FROM scores WHERE version_id=%s AND date > %s AND overall >= 75",
        (vid, cutoff))
    rows = cur.fetchall()

    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(sym, d if isinstance(d, _date) else _date.fromisoformat(str(d)), ov)
             for sym, d, ov in rows]
    results, _skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_generic")
    # Result vocabulary is {"win", "stop", "expire"} (confirmed against verify_scorecard.py's own
    # EV map, NOT {"win", "loss"}) -- and the swing dict's own 'date' must be normalized to a
    # string the SAME way the SQL side is (peaks_to_swing_results may hand back a date object OR
    # an already-stringified value depending on the swing-cache internals; verify_scorecard.py
    # defensively normalizes with the identical isoformat()-or-str() pattern used here).
    wr_by_key = {}
    for r in results:
        sw = (r.get("swing") or {}).get("15d")
        if sw and sw.get("result") in ("win", "stop"):
            dk = r["date"]
            dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
            wr_by_key[(r["symbol"], dk)] = 1 if sw["result"] == "win" else 0

    band_reports = {}
    for band, lo, hi in BAND_EDGES:
        n_oos = 0
        wins = 0
        for sym, d, ov in rows:
            if lo <= ov < hi:
                dk = d.isoformat() if hasattr(d, "isoformat") else str(d)
                key = (sym, dk)
                if key in wr_by_key:
                    n_oos += 1
                    wins += wr_by_key[key]
        oos_wr = (wins / n_oos) if n_oos else None
        ref = in_sample.get(band, {})
        ci_lo, ci_hi = _wilson_ci(ref.get("wr", 0.0), ref.get("n", 0)) if ref else (None, None)
        gating = bool(ref.get("gating"))
        if n_oos < 30 or oos_wr is None:
            # Mission floor (references.json commitment 3, mirrored from miss-ledger precedent):
            # N<30 cells are dropped from consideration entirely, not silently scored as a miss.
            state = "INSUFFICIENT_N"
        elif ci_lo is not None and ci_lo <= oos_wr <= ci_hi:
            state = "IN_CI"
        else:
            state = "MISS"
        band_reports[band] = {
            "n_oos": n_oos, "oos_wr": oos_wr,
            "in_sample_wr": ref.get("wr"), "in_sample_n": ref.get("n"),
            "ci_95": [ci_lo, ci_hi], "state": state, "gating": gating,
        }

    gating_states = {b: r["state"] for b, r in band_reports.items() if r["gating"]}
    gating_misses = [b for b, s in gating_states.items() if s == "MISS"]
    gating_evaluable = [b for b, s in gating_states.items() if s != "INSUFFICIENT_N"]

    if gating_misses:
        verdict = "SINGLE_WINDOW_MISS"
        consequence = (f"gating band(s) {gating_misses} outside their frozen CI -- document, do "
                        f"NOT block, extend observation to {EXTENSION_DATE.isoformat()}")
    elif not gating_evaluable:
        verdict = "INSUFFICIENT_N"
        consequence = ("every gating band (75-79/80-84/85-89) has N<30 OOS-resolved rows so far -- "
                        "no band is evaluable yet; accumulate further.")
    else:
        verdict = "PASS"
        consequence = f"all evaluable gating bands ({gating_evaluable}) within their frozen in-sample CI"
    return HypothesisResult("H4", verdict, {"bands": band_reports}, consequence)


# ================================================================================================
# H5 -- v73-vs-v74 retirement-regret on OOS rows
# ================================================================================================

def check_h5_reachable(refs: dict) -> ReachabilityResult:
    try:
        from experiments.skill_vs_baseline.verify_scorecard import run_scorecard
    except Exception as e:
        return ReachabilityResult("H5", False, f"import failed: {e}")
    try:
        n73 = run_scorecard(vid=73, oos=True, sample=5000, write_json=False, verbose=False).get("n_window")
        n74 = run_scorecard(vid=74, oos=True, sample=5000, write_json=False, verbose=False).get("n_window")
        return ReachabilityResult("H5", True, f"v73/v74 both reachable; OOS n_window "
                                               f"v73={n73} v74={n74} (accruing, not a verdict)")
    except Exception as e:
        return ReachabilityResult("H5", False, f"call failed: {e}\n{traceback.format_exc(limit=2)}")


def compute_h5(refs: dict) -> HypothesisResult:
    from experiments.skill_vs_baseline.verify_scorecard import run_scorecard
    out73 = run_scorecard(vid=73, oos=True, sample=300000, write_json=False, verbose=False)
    out74 = run_scorecard(vid=74, oos=True, sample=300000, write_json=False, verbose=False)
    if out73.get("verdict") == "INSUFFICIENT_N" or out74.get("verdict") == "INSUFFICIENT_N":
        return HypothesisResult("H5", "INSUFFICIENT_N", {"v73": out73, "v74": out74},
                                 "accumulate further, no verdict yet.")
    b73 = next((b for b in out73.get("buckets", []) if b["thr"] == 75), None)
    b74 = next((b for b in out74.get("buckets", []) if b["thr"] == 75), None)
    detail = {
        "v73": {"n": b73["n"] if b73 else 0, "ev_pct": b73["ev_pct"] if b73 else None,
                "gate_verdict": out73["gate"]["verdict"]},
        "v74": {"n": b74["n"] if b74 else 0, "ev_pct": b74["ev_pct"] if b74 else None,
                "gate_verdict": out74["gate"]["verdict"]},
    }
    if not b73 or not b74:
        return HypothesisResult("H5", "INSUFFICIENT_N", detail, "one or both versions have no 75+ OOS bucket yet.")

    # RULING OQ-4 (FABLE, 2026-07-13) -- H5 is TWO-STAGE, pre-registered:
    #   STAGE 1 (this function) = the cheap apex-EV comparison. It IS the H5 gate.
    #   STAGE 2 (EXT_H5_DD_ABLATION) = the full DD-primary re-ablation (queued N=300, the
    #     ORIGINAL v74 retirement methodology), run ONLY on a t>=2-confirmed stage-1 regret flag,
    #     and REQUIRED before any regret conclusion is drawn. Build nothing further until then.
    # Welch-t caveat: the point-gap is computed from summary aggregates; a proper t>=2
    # confirmation needs the per-trade EV vectors (a manual/vectorized pass at eval time --
    # run_scorecard's summary dict does not persist them). A REGRET_CANDIDATE therefore means
    # "run the t-test; if t>=2, stage 2 is mandatory before concluding" -- never a conclusion.
    ev_gap = (b73["ev_pct"] or 0) - (b74["ev_pct"] or 0)
    verdict = "REGRET_CANDIDATE" if ev_gap > 0 else "NO_REGRET"
    consequence = (
        "v73's OOS 75+ EV exceeds v74's by "
        f"{ev_gap:+.3f}pp (stage-1 point gap). Per RULING OQ-4 the pre-registered path is: "
        "(1) confirm the gap at Welch t>=2 on the per-trade EV vectors; (2) if confirmed, "
        "escalate to STAGE 2 -- the full DD-primary re-ablation (queued N=300, original v74 "
        "retirement methodology) -- BEFORE any regret conclusion. A stage-1 flag alone concludes "
        "nothing: pooled EV can favor v73 while the DD-primary retirement stays correct "
        "(the supply-dilution mechanism documented in PREREGISTRATION.md section 4 H5)."
        if verdict == "REGRET_CANDIDATE" else
        "v74 >= v73 on OOS 75+ EV -- retirement stays validated at stage 1; stage 2 not triggered."
    )
    detail["ev_gap_pp"] = ev_gap
    detail["two_stage_protocol"] = "RULING OQ-4: stage-1 EV gate -> t>=2 -> stage-2 DD re-ablation"
    return HypothesisResult("H5", verdict, detail, consequence)


def EXT_H5_DD_ABLATION(*_a, **_kw):
    """H5 STAGE 2 -- pre-registered escalation per RULING OQ-4 (FABLE, 2026-07-13): the full
    DD-primary re-ablation (queued N=300, the ORIGINAL v74 retirement methodology --
    whole-tail-ablation MC per experiments/skill_vs_baseline/OVERNIGHT_FINDINGS.md) on OOS rows.
    Runs ONLY on a t>=2-confirmed stage-1 regret flag, and is REQUIRED before any regret
    conclusion. Deliberately not built until that trigger fires ('build nothing further now')."""
    raise NotImplementedError(
        "H5 stage 2 (DD-primary re-ablation) is pre-registered but deliberately unbuilt until a "
        "t>=2-confirmed stage-1 regret flag triggers it (RULING OQ-4). Submit it as its own "
        "queued MC job at that time; do not approximate a DD-primary claim from stage 1.")


# ================================================================================================
# H6 -- per-lever drift = the only sanctioned lever-consolidation revisit trigger
# RULING OQ-5 (FABLE, 2026-07-13): H6 carries BOTH lines --
#   line (a) the NEW pre-registered per-lever drift metric (RXDD/SVR/MWDD/TVDD/BDIV + F3F:
#            days-active fraction, mean contraction depth, apex-EV on active days; OOS vs the
#            frozen in-sample reference in references.json). Implemented below for the five
#            market-state levers (cheap from MarketRegime/MarketBreadth/PriceHistory + the
#            engine's own importable scale functions); SVR is per-signal (needs semivol_r per
#            entry) and is marked measured_at_eval_time per the ruling's cheap-only rule.
#   line (b) tier_drift.py as the component-lens DIAGNOSTIC line.
# Neither line triggers anything except H6's documented lever-consolidation revisit.
# ================================================================================================

# The five market-state levers implemented for line (a). SVR handled as an eval-time slot.
_LEVER_IDS = ("RXDD", "MWDD", "TVDD", "BDIV", "F3F")
_ACTIVE_SCALE_THRESHOLD = 0.995  # scale below this = the lever is materially contracting


def _load_vix_map_mysql(earliest):
    """MarketRegime.vix_close by date, direct from MySQL (light: ~8k rows total). Deliberately
    NOT backtest_cascade._load_vix_series() -- that reads a pre-materialized parquet that can go
    stale exactly like the skill parquets (the December pre-step problem); the DB row is the
    canonical daily source and the engine's own MC path builds its RXDD map from the same table."""
    from database.models.core import MarketRegime
    rows = (MarketRegime
            .select(MarketRegime.date, MarketRegime.vix_close)
            .where((MarketRegime.date >= earliest)
                   & (MarketRegime.vix_close.is_null(False)))
            .order_by(MarketRegime.date)
            .tuples())
    m = {d: float(v) for d, v in rows}
    return sorted(m.keys()), m


def _lever_day_scales(win_start, win_end):
    """Per-trading-day market-state scale for each of the 5 implemented levers over the window.

    Uses backtest_cascade's OWN scale functions + map builders + on-or-before lookup semantics
    (imported, not re-derived), with the shipped constants as loaded by that module. The
    path-dependent DD-gate on RXDD/MWDD/TVDD is BYPASSED by passing dd=1.0 (always >= dd_min) --
    this isolates the observable MARKET-STATE component, which is the only path-independent
    definition that admits an in-sample reference (the in-sample era has no single canonical
    book-DD path). The VIX-panic exclusions (MWDD/TVDD) are market-state and are KEPT. Disclosed
    in PREREGISTRATION.md section 4 H6.

    Returns (spine_dates, {lever_id: {date: scale}}).
    """
    import backtest_cascade as bc
    from dte_router import value_on_or_before
    from datetime import timedelta as _td

    vix_dates, vix_map = _load_vix_map_mysql(win_start - _td(days=60))
    mcc_dates, mcc_map = bc.load_mcclellan_map(win_start)
    trin_dates, trin_map = bc.load_trin_map(win_start)
    bdiv_dates, bdiv_map = bc.load_bdiv_map(win_start)
    brd_dates, brd_map = bc.load_breadth_map(win_start)

    # Day spine = MarketBreadth trading days inside the window (the canonical daily market table).
    spine = [d for d in brd_dates if win_start <= d <= win_end]

    scales = {lever: {} for lever in _LEVER_IDS}
    for d in spine:
        vix = value_on_or_before(vix_dates, vix_map, d) if vix_dates else None
        mcc = value_on_or_before(mcc_dates, mcc_map, d) if mcc_dates else None
        trin = value_on_or_before(trin_dates, trin_map, d) if trin_dates else None
        bdiv = value_on_or_before(bdiv_dates, bdiv_map, d) if bdiv_dates else None
        brd = value_on_or_before(brd_dates, brd_map, d) if brd_dates else None
        # enabled=True deliberately: the metric measures the shipped BAND CONDITION itself;
        # the live enabled flags are snapshot separately (PREREGISTRATION.md section 1b).
        scales["RXDD"][d] = bc._rxdd_call_scale(1.0, vix, True, bc.RXDD_VIX_C, bc.RXDD_VIX_W,
                                                 bc.RXDD_DEPTH, bc.RXDD_DD_MIN)
        scales["MWDD"][d] = bc._mwdd_call_scale(1.0, mcc, vix, True, bc.MWDD_MCC_C, bc.MWDD_MCC_W,
                                                 bc.MWDD_DEPTH, bc.MWDD_DD_MIN, bc.MWDD_VIX_PANIC)
        scales["TVDD"][d] = bc._tvdd_call_scale(1.0, trin, vix, True, bc.TVDD_TRIN_C, bc.TVDD_TRIN_W,
                                                 bc.TVDD_DEPTH, bc.TVDD_DD_MIN, bc.TVDD_VIX_PANIC)
        scales["BDIV"][d] = bc._bdiv_call_scale(bdiv, True, bc.BDIV_PROX_CUT, bc.BDIV_PROX_FULL,
                                                 bc.BDIV_GAP_C, bc.BDIV_GAP_W, bc.BDIV_DEPTH)
        scales["F3F"][d] = bc._breadth_alloc_scale(brd, is_put=False)
    return spine, scales


def _apex_ev_join(rows):
    """(symbol, date, overall) rows -> {(symbol, iso_date): apex_ev} via the 30dte_apex barrier
    (APEX_PERIOD window, APEX_EV payoff map) -- the same join verify_scorecard.py performs."""
    from database.barrier_cache import peaks_to_swing_results
    from collections import namedtuple
    from datetime import date as _date
    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(sym, d if isinstance(d, _date) else _date.fromisoformat(str(d)), ov)
             for sym, d, ov in rows]
    results, _skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set="30dte_apex")
    ev_by_key = {}
    for r in results:
        sw = (r.get("swing") or {}).get(APEX_PERIOD)
        if sw and sw.get("result") in APEX_EV:
            dk = r["date"]
            dk = dk.isoformat() if hasattr(dk, "isoformat") else str(dk)
            ev_by_key[(r["symbol"], dk)] = APEX_EV[sw["result"]]
    return ev_by_key


def _lever_metrics_for_window(rows, win_start, win_end):
    """The RULING OQ-5 line-(a) metric block for one window.

    rows = [(symbol, date, overall)] for the window's 75+ CALL signal population.
    Returns {lever: {n_days, days_active, days_active_fraction, mean_contraction_depth_active,
                     n_signals_total, n_signals_active_days, apex_ev_active_mean,
                     apex_ev_inactive_mean}} plus an SVR eval-time placeholder and the constants
    snapshot. Identical code path for the frozen in-sample reference (--freeze-lever-reference)
    and the December OOS side (compute_h6) -- by construction, not by convention.
    """
    import backtest_cascade as bc
    spine, scales = _lever_day_scales(win_start, win_end)
    ev_by_key = _apex_ev_join(rows)

    # signal -> iso date + ev (only signals that resolved on the apex barrier)
    sig_by_date = {}
    for sym, d, _ov in rows:
        dk = d.isoformat() if hasattr(d, "isoformat") else str(d)
        ev = ev_by_key.get((sym, dk))
        if ev is not None:
            sig_by_date.setdefault(dk, []).append(ev)
    n_signals_total = sum(len(v) for v in sig_by_date.values())

    out = {}
    for lever in _LEVER_IDS:
        smap = scales[lever]
        active_days = {d for d, s in smap.items() if s < _ACTIVE_SCALE_THRESHOLD}
        depths = [1.0 - smap[d] for d in active_days]
        ev_active, ev_inactive = [], []
        for dk, evs in sig_by_date.items():
            d = date.fromisoformat(dk)
            (ev_active if d in active_days else ev_inactive).extend(evs)
        out[lever] = {
            "n_days": len(spine),
            "days_active": len(active_days),
            "days_active_fraction": (len(active_days) / len(spine)) if spine else None,
            "mean_contraction_depth_active": (sum(depths) / len(depths)) if depths else None,
            "n_signals_total": n_signals_total,
            "n_signals_active_days": len(ev_active),
            "apex_ev_active_mean": (sum(ev_active) / len(ev_active)) if ev_active else None,
            "apex_ev_inactive_mean": (sum(ev_inactive) / len(ev_inactive)) if ev_inactive else None,
        }
    out["SVR"] = {
        "status": "measured_at_eval_time",
        "reason": "per-signal metric (semivol_r must be computed per entry from PriceHistory -- "
                  "not cheap from existing daily tables; RULING OQ-5 cheap-only rule)",
        "spec": "fraction of 75+ call signals with _svr_call_scale(semivol_r) < 0.995, mean "
                "contraction depth over scaled signals, apex-EV of scaled vs unscaled signals; "
                "constants from backtest_cascade SVR_* at eval time",
    }
    out["_constants"] = {
        "RXDD": {"vix_c": bc.RXDD_VIX_C, "vix_w": bc.RXDD_VIX_W, "depth": bc.RXDD_DEPTH,
                  "dd_min_bypassed": bc.RXDD_DD_MIN},
        "MWDD": {"mcc_c": bc.MWDD_MCC_C, "mcc_w": bc.MWDD_MCC_W, "depth": bc.MWDD_DEPTH,
                  "dd_min_bypassed": bc.MWDD_DD_MIN, "vix_panic": bc.MWDD_VIX_PANIC},
        "TVDD": {"trin_c": bc.TVDD_TRIN_C, "trin_w": bc.TVDD_TRIN_W, "depth": bc.TVDD_DEPTH,
                  "dd_min_bypassed": bc.TVDD_DD_MIN, "vix_panic": bc.TVDD_VIX_PANIC},
        "BDIV": {"prox_cut": bc.BDIV_PROX_CUT, "prox_full": bc.BDIV_PROX_FULL,
                  "gap_c": bc.BDIV_GAP_C, "gap_w": bc.BDIV_GAP_W, "depth": bc.BDIV_DEPTH},
        "F3F": {"call_thresh": bc.F3F_CALL_THRESH, "call_low": bc.F3F_CALL_LOW,
                 "call_floor": bc.F3F_CALL_FLOOR},
        "active_scale_threshold": _ACTIVE_SCALE_THRESHOLD,
        "dd_gate_note": "RXDD/MWDD/TVDD computed with dd=1.0 (market-state-only; the engine's "
                         "realized activity is additionally DD-gated, path-dependent)",
    }
    return out


def freeze_lever_reference() -> int:
    """Authoring/reproduction utility: print the H6 line-(a) IN-SAMPLE reference block.

    Reads ONLY pre-cutoff data (the skill_v74 parquet -- in-sample-complete by construction --
    defensively re-filtered through pre_cutoff_filter) and prints the JSON block that was frozen
    into references.json on 2026-07-13. Never writes anything. Re-running it later must reproduce
    the frozen values (modulo a barrier-cache rebuild changing late-window resolutions)."""
    import polars as pl
    from database.bulk_cache import materialize_polars
    from experiments._holdout import pre_cutoff_filter, cutoff_iso

    sc = pl.read_parquet(materialize_polars(
        "skill_v74_allscores_2016",
        lambda: (_ for _ in ()).throw(RuntimeError("skill_v74_allscores_2016 cache missing"))))
    sc = pre_cutoff_filter(sc)
    sc = sc.filter(pl.col("overall") >= 75)
    rows = list(zip(sc["symbol"].to_list(), sc["date"].to_list(), sc["overall"].to_list()))
    dates = sorted(sc["date"].to_list())
    win_start = date.fromisoformat(dates[0])
    win_end = date.fromisoformat(cutoff_iso())
    print(f"[freeze-lever-reference] in-sample 75+ population: {len(rows):,} rows, "
          f"{dates[0]} .. {dates[-1]} (cutoff {cutoff_iso()})", file=sys.stderr)
    metrics = _lever_metrics_for_window(rows, win_start, win_end)
    block = {"window": {"start": win_start.isoformat(), "end": win_end.isoformat()},
             "n_signals_75plus": len(rows), "levers": metrics}
    print(json.dumps(block, indent=2, default=str))
    return 0


def check_h6_reachable(refs: dict) -> ReachabilityResult:
    problems = []
    # line (b) -- tier_drift
    try:
        from experiments.version_scorecard import tier_drift
        versions = tier_drift.discover_versions()
        line_b = f"tier_drift reachable ({len(versions)} hydrated packs)"
    except Exception as e:
        problems.append(f"tier_drift import/discover failed: {e}")
        line_b = None
    # line (a) -- lever scale functions + market maps (import + tiny map load; no metric computed)
    try:
        import backtest_cascade as bc
        from dte_router import value_on_or_before  # noqa: F401 -- import check
        for fn in ("_rxdd_call_scale", "_mwdd_call_scale", "_tvdd_call_scale",
                    "_bdiv_call_scale", "_breadth_alloc_scale",
                    "load_mcclellan_map", "load_trin_map", "load_bdiv_map", "load_breadth_map"):
            assert hasattr(bc, fn), f"backtest_cascade.{fn} missing (engine drifted?)"
        ref = (refs["hypotheses"]["H6"].get("lever_drift") or {}).get("in_sample_reference")
        if not ref:
            problems.append("references.json H6.lever_drift.in_sample_reference missing")
        line_a = "lever scale fns + map builders importable; frozen reference present"
    except Exception as e:
        problems.append(f"line (a) reachability failed: {e}")
        line_a = None
    if problems:
        return ReachabilityResult("H6", False, "; ".join(problems))
    return ReachabilityResult("H6", True, f"line (a): {line_a}; line (b): {line_b} "
                                           f"(no drift computed yet)")


def compute_h6(refs: dict) -> HypothesisResult:
    from experiments.version_scorecard import tier_drift
    from database.trader_database import DB
    from datetime import timedelta as _td

    h6 = refs["hypotheses"]["H6"]
    cutoff = date.fromisoformat(refs["frozen_anchors"]["calibration_cutoff_date"])

    # ---- line (a): per-lever drift metric, OOS vs frozen in-sample reference (RULING OQ-5) ----
    vid = _active_version_id()
    cur = DB.execute_sql(
        "SELECT symbol, date, overall FROM scores WHERE version_id=%s AND date > %s AND overall >= 75",
        (vid, cutoff.isoformat()))
    oos_rows = cur.fetchall()
    oos_metrics = _lever_metrics_for_window(oos_rows, cutoff + _td(days=1), datetime.now().date())

    ref_block = (h6.get("lever_drift") or {}).get("in_sample_reference") or {}
    ref_levers = ref_block.get("levers") or {}
    thresholds = (h6.get("lever_drift") or {}).get("review_flag_thresholds") or {}
    min_days = int(thresholds.get("min_oos_trading_days", 60))
    ratio_hi = float(thresholds.get("days_active_ratio_flag_above", 2.0))
    ratio_lo = float(thresholds.get("days_active_ratio_flag_below", 0.5))
    eps = float(thresholds.get("ratio_epsilon", 0.01))

    lever_flags = []
    lever_table = {}
    for lever in _LEVER_IDS:
        oos_m = oos_metrics.get(lever) or {}
        ref_m = ref_levers.get(lever) or {}
        row = {"oos": oos_m, "in_sample_reference": ref_m}
        n_days = oos_m.get("n_days") or 0
        if n_days < min_days or ref_m.get("days_active_fraction") is None:
            row["state"] = "INSUFFICIENT_DAYS" if n_days < min_days else "NO_REFERENCE"
        else:
            # Pre-registered review-grade ratio test (epsilon-stabilized near zero):
            #   ratio = (oos_frac + eps) / (ref_frac + eps); flag if > 2.0 or < 0.5.
            ratio = (float(oos_m["days_active_fraction"]) + eps) / \
                    (float(ref_m["days_active_fraction"]) + eps)
            row["days_active_ratio"] = ratio
            if ratio > ratio_hi or ratio < ratio_lo:
                row["state"] = "DRIFT_FLAG"
                lever_flags.append(lever)
            else:
                row["state"] = "IN_RANGE"
        lever_table[lever] = row
    lever_table["SVR"] = {"state": "MEASURED_AT_EVAL_TIME",
                           "spec": oos_metrics.get("SVR", {}).get("spec")}

    # ---- line (b): tier_drift component-lens diagnostic ----
    frozen = h6["frozen_baseline"]
    versions = tier_drift.discover_versions()
    flags = tier_drift.run(versions, metric="tp", k=frozen["k_consecutive"])
    frozen_keys = {(f["side"], f["band"]) for f in frozen["flags"]}
    frozen_cum = {(f["side"], f["band"]) for f in frozen["flags"] if f.get("cum_significant")}
    now_keys = {(f["side"], f["band"]) for f in flags}
    now_cum = {(f["side"], f["band"]) for f in flags if any("CUM(" in t for t in f.get("tags", []))}
    new_flags = now_keys - frozen_keys
    newly_cum = now_cum - frozen_cum

    triggers = []
    if lever_flags:
        triggers.append(f"line (a) lever-drift: {sorted(lever_flags)}")
    if newly_cum:
        triggers.append(f"line (b) newly CUM-significant: {sorted(newly_cum)}")
    if new_flags:
        triggers.append(f"line (b) new tier/band flags: {sorted(new_flags)}")

    if triggers:
        verdict = "REVIEW_TRIGGER"
        consequence = ("; ".join(triggers) + " -- per weatherization.md Flaw #4/#5 and RULING "
                        "OQ-5, this OPENS the documented lever-consolidation revisit (a review, "
                        "never an automatic action; neither line triggers anything else).")
    else:
        verdict = "QUIET"
        consequence = ("no lever-drift flag (line a) and no new/worsened tier-drift flag (line b) "
                        "vs the frozen baselines -- levers stay separately validated, no action.")
    detail = {"line_a_lever_drift": lever_table,
              "line_a_oos_constants": oos_metrics.get("_constants"),
              "line_b_frozen_flags": frozen["flags"], "line_b_current_flags": flags,
              "note": "line (a) = market-state lever bands (DD-gate bypassed, disclosed); "
                      "line (b) = tier_drift.py score-tier lens. RULING OQ-5 (FABLE, 2026-07-13)."}
    return HypothesisResult("H6", verdict, detail, consequence)


# ================================================================================================
# Orchestration
# ================================================================================================

REACHABILITY_CHECKS = {"H1": check_h1_reachable, "H2": check_h2_reachable, "H3": check_h3_reachable,
                        "H4": check_h4_reachable, "H5": check_h5_reachable, "H6": check_h6_reachable}
COMPUTES = {"H1": compute_h1, "H2": compute_h2, "H3": compute_h3,
            "H4": compute_h4, "H5": compute_h5, "H6": compute_h6}


def run_selftest(refs: dict, only: Optional[str] = None) -> int:
    print("=" * 100)
    print("SELFTEST -- reachability only. No post-cutoff outcome is evaluated or printed below.")
    print("=" * 100)
    ok = True
    ids = [only] if only else list(REACHABILITY_CHECKS.keys())
    for hid in ids:
        try:
            r = REACHABILITY_CHECKS[hid](refs)
        except Exception as e:
            r = ReachabilityResult(hid, False, f"unhandled exception: {e}\n{traceback.format_exc(limit=2)}")
        status = "OK  " if r.reachable else "FAIL"
        print(f"[{status}] {r.id}: {r.detail}")
        ok = ok and r.reachable
    print("-" * 100)
    print(f"SELFTEST {'PASSED' if ok else 'FAILED'} -- {'all' if ok else 'not all'} sources reachable.")
    return 0 if ok else 1


def run_full(refs: dict, only: Optional[str] = None, force_early: bool = False) -> int:
    today = datetime.now().date()
    if today < EVAL_DATE and not force_early:
        print(f"REFUSED: today ({today.isoformat()}) is before the pre-registered eval date "
              f"({EVAL_DATE.isoformat()}). A full run before this date would be exactly the "
              f"'unregistered read' the holdout lock exists to prevent (PREREGISTRATION.md "
              f"preamble). Use --selftest to verify reachability, or --force-early to run anyway "
              f"(loudly marked as NOT the pre-registered read).", file=sys.stderr)
        return 2

    early_marker = today < EVAL_DATE
    print("=" * 100)
    if early_marker:
        print(f"*** --force-early: today={today.isoformat()} is BEFORE {EVAL_DATE.isoformat()}. ***")
        print(f"*** This is a REHEARSAL, NOT the pre-registered OOS read. Do not act on these  ***")
        print(f"*** numbers as if they were the Dec-15 evaluation.                             ***")
        print("=" * 100)
    print(f"DECEMBER 2026 OOS EVALUATION  --  run at {datetime.now(timezone.utc).isoformat()}")
    print("=" * 100)

    ids = [only] if only else list(COMPUTES.keys())
    results = []
    for hid in ids:
        try:
            res = COMPUTES[hid](refs)
        except Exception as e:
            res = HypothesisResult(hid, "ERROR", {}, "", error=f"{e}\n{traceback.format_exc(limit=3)}")
        results.append(res)

    print(f"{'ID':<4} {'VERDICT':<20} CONSEQUENCE")
    print("-" * 100)
    for r in results:
        print(f"{r.id:<4} {r.verdict:<20} {r.consequence[:140]}")
        if r.error:
            print(f"      ERROR: {r.error[:300]}")
    print("-" * 100)

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"eval_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "is_early_rehearsal": early_marker,
        "eval_date": EVAL_DATE.isoformat(),
        "results": [r.as_dict() for r in results],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nwrote {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true",
                     help="reachability-only check; never evaluates a post-cutoff outcome")
    ap.add_argument("--force-early", action="store_true",
                     help="allow a full run before 2026-12-15 (rehearsal only, loudly marked)")
    ap.add_argument("--hypothesis", choices=list(COMPUTES.keys()), default=None,
                     help="restrict to a single hypothesis (default: all)")
    ap.add_argument("--marking-verified", action="store_true",
                     help="attest the H3 marking-verification pre-step ran clean (parity harness "
                          "bit-exact; RULING OQ-3a) -- required before H3 adjudicates any breach")
    ap.add_argument("--freeze-lever-reference", action="store_true",
                     help="authoring/reproduction utility: print the H6 line-(a) in-sample "
                          "reference block (reads only pre-cutoff data; never writes files)")
    args = ap.parse_args()

    _OPTS["marking_verified"] = bool(args.marking_verified)

    if args.freeze_lever_reference:
        return freeze_lever_reference()

    refs = load_references()

    if args.selftest:
        if args.force_early:
            print("NOTE: --force-early is a no-op with --selftest (selftest never date-gates).",
                  file=sys.stderr)
        return run_selftest(refs, only=args.hypothesis)

    return run_full(refs, only=args.hypothesis, force_early=args.force_early)


if __name__ == "__main__":
    sys.exit(main())
