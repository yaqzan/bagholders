#!/usr/bin/env python
"""PRF — Portfolio Response Function (Phase 1: sizing instrument).

Derives the supply-coupled portfolio knobs (call tier allocs, 70-74 overflow,
implied tradable threshold) from a version's Stage-1 observables phi:

    phi = { tp_shrunk by band, supply/day by band, recycle_coverage }
    derived = F(phi)

This formalizes the response the project already executed by hand twice on the
honest engine:

  * v70 Apex (supply 5.8/day, cov 0.660 -> starved book): low tier 0.10,
    overflow 0.035 ("fills the idle book").
  * v71 retune (supply 7.8/day, cov 0.781 -> dense book): low tier 0.05,
    overflow 0 ("selectivity on the doubled tier"; overflow retired as
    supply-density-conditional).

Both retune decisions are directly legible in two hydration variables — F is
those decisions written as a formula, fit so it REPRODUCES both hand-tuned
N=500-validated winners exactly (see selftest).

STATUS: **INSTRUMENT ONLY** — output is written to the research pack
(`derived_portfolio.json`) for VersionCompare / cross-version matched-policy
comparison and as the proposed starting candidate for a new version's Stage-3
confirm. It is NOT wired to the live engine, does NOT change strategy_config,
and a derived config still requires the one-shot Stage-3 T1-T7 MC confirm
(collapse=0) before any live use. F proposes; MC disposes.

WHAT F OWNS (Phase 1)            | WHAT F DOES NOT OWN
---------------------------------+---------------------------------------------
call TIER_ALLOC (ultra/top/      | DD levers (RXDD/SVR/MWDD/TVDD/BDIV) —
  mid/low)                       |   market-state, version-agnostic
70-74 overflow                   | dead-hold (collapse-preventing engine physics)
implied min-call threshold       | exposure caps / MaxPos / puts (canon)
  (emerges from edge admission)  | TP/SL (Phase 2 at most — confounded with the
                                 |   cost canon + HOLD>>CUT findings)

KNOWN LIMITATION — cross-engine anchors: the v69-honest retune (85+-only,
TP 0.28) was tuned under the OLD barrier set / pre-HOLD engine, where sub-85
tiers ran below ITS break-even. Under the fixed gate payoff and the current
HOLD engine, v69's tiers all clear admission — F deliberately does NOT
reproduce v69 (and should not: that config answered a different engine).
Fit substrates are the same-engine pair v70/v71 only; anything F derives for a
new substrate is validated by MC, not by faith in the fit.

Run (Windows):
  set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && ^
  python experiments/version_scorecard/portfolio_response.py --derive v72
  python experiments/version_scorecard/portfolio_response.py --materialize v69,v70,v71,v72
  python experiments/version_scorecard/portfolio_response.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from stage1_growth_gate import (  # noqa: E402
    PACK_ROOT, load_json, load_supply, DEFAULT_ACTIVE_DAYS,
)

# ---------------------------------------------------------------------------
# FIXED payoff (the growth-gate convention: held constant so neither a scoring
# sweep nor a version can game the edge measure; reproduces call BE = 45.0%).
# ---------------------------------------------------------------------------
CALL_W, CALL_L = 0.33, 0.27
BE = CALL_L / (CALL_W + CALL_L)                    # 0.45

# Band -> tier mapping (call side; puts are off in every active profile).
TIER_BANDS = {
    "ultra": ["95-100"],
    "top": ["85-89", "90-94"],
    "mid": ["80-84"],
    "low": ["75-79"],
}
TIER_THRESHOLD = {"ultra": 95, "top": 85, "mid": 80, "low": 75}
BASE_LADDER = {"ultra": 0.20, "top": 0.15, "mid": 0.10, "low": 0.10}  # canon anchors

# ---------------------------------------------------------------------------
# COEFFICIENTS — fit 2026-06-12 on the two same-engine hand-tuned winners
# (v70 Apex 2026-06-02/03 + v71 retune 2026-06-10, both N=500-MC-validated).
#   BETA solves  (S_REF_LOW / s_low(v71))^BETA = 0.05/0.10  exactly.
#   COV_FLOOR/COV_TARGET are the v70/v71 recycle coverages (ramp endpoints).
# ---------------------------------------------------------------------------
COEFFS = {
    "EDGE_MIN": 0.05,      # tier admitted iff tp_shrunk - BE > EDGE_MIN
    "S_REF_LOW": 1.125,    # 75-79 supply/day at which the marginal squeeze = 1.0 (v70)
    "BETA": 1.221,         # marginal-tier supply-inverse exponent, squeeze capped at 1.0
    "OVF_MAX": 0.035,      # validated collapse-safe overflow ceiling (<=0.040; cliff 0.045)
    "COV_FLOOR": 0.66,     # full overflow at/below this recycle coverage (v70)
    "COV_TARGET": 0.78,    # zero overflow at/above (v71)
}


def load_phi(version):
    """Stage-1 observables for a version: per-tier pooled tp_shrunk + supply/day,
    plus recycle coverage. Returns None when the pack is missing/partial."""
    d = load_json(PACK_ROOT / version / "research_pack" / "utility_5y_wr15.json")
    if not isinstance(d, dict):
        return None
    bands = {b.get("band"): b for b in ((d.get("sides") or {}).get("call") or {}).get("bands") or []}
    sup = load_supply(version)
    active_days = sup.get("active_days") or DEFAULT_ACTIVE_DAYS
    tiers = {}
    for tier, names in TIER_BANDS.items():
        rows = [bands[x] for x in names if x in bands and bands[x].get("tp_shrunk") is not None]
        if not rows:
            return None  # partial pack — refuse to derive from incomplete phi
        n = sum(float(r["n"]) for r in rows)
        tp = sum(float(r["n"]) * float(r["tp_shrunk"]) for r in rows) / n
        tiers[tier] = {"n": n, "tp_shrunk": tp, "supply_per_day": n / active_days}
    return {
        "version": version,
        "tiers": tiers,
        "recycle_coverage": sup.get("coverage"),
        "supply_approx": bool(sup.get("approx")),
        "active_days": active_days,
        "pack_meta": d.get("version") or {},
    }


def derive(phi, coeffs=COEFFS):
    """F(phi) -> derived sizing config. Pure function of phi + coefficients."""
    c = coeffs
    tiers = {}
    admitted = []
    for tier in ("ultra", "top", "mid", "low"):
        t = phi["tiers"][tier]
        edge = t["tp_shrunk"] - BE
        if edge <= c["EDGE_MIN"]:
            tiers[tier] = 0.0
            continue
        alloc = BASE_LADDER[tier]
        if tier == "low":
            squeeze = min(1.0, (c["S_REF_LOW"] / max(t["supply_per_day"], 1e-9)) ** c["BETA"])
            alloc = round(BASE_LADDER["low"] * squeeze, 3)
        tiers[tier] = alloc
        admitted.append(tier)
    cov = phi["recycle_coverage"]
    if cov is None or phi.get("supply_approx"):
        overflow = None  # refuse: hydration unknown -> no overflow opinion
    else:
        ramp = (c["COV_TARGET"] - cov) / (c["COV_TARGET"] - c["COV_FLOOR"])
        overflow = round(c["OVF_MAX"] * min(1.0, max(0.0, ramp)), 3)
    threshold = min((TIER_THRESHOLD[t] for t in admitted), default=None)
    return {
        "instrument_only": True,
        "engine_basis": "HOLD core (calendar-hold honest theta, asymmetric cost canon)",
        "tier_alloc": tiers,
        "tier_overflow": overflow,
        "min_call_threshold": threshold,
        "edges_vs_fixed_BE45": {t: round(phi["tiers"][t]["tp_shrunk"] - BE, 4)
                                for t in phi["tiers"]},
        "inputs": {
            "supply_per_day": {t: round(phi["tiers"][t]["supply_per_day"], 3)
                               for t in phi["tiers"]},
            "recycle_coverage": cov,
            "supply_approx": phi.get("supply_approx"),
        },
        "coefficients": dict(c),
        "not_owned": "DD levers / dead-hold / caps / MaxPos / puts / TP-SL pass through canon",
        "note": ("F proposes, MC disposes: a derived config still requires the "
                 "one-shot Stage-3 T1-T7 confirm (collapse=0) before any live use."),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def materialize(version):
    phi = load_phi(version)
    if phi is None:
        print(f"  {version}: pack missing/partial — refusing to derive")
        return None
    if phi.get("supply_approx"):
        print(f"  {version}: supply APPROXIMATED — run signal_supply.py first; refusing")
        return None
    out = derive(phi)
    path = PACK_ROOT / version / "research_pack" / "derived_portfolio.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    t = out["tier_alloc"]
    print(f"  {version}: ladder {t['ultra']:.3f}/{t['top']:.3f}/{t['mid']:.3f}/{t['low']:.3f} "
          f"ovf {out['tier_overflow']}  thresh {out['min_call_threshold']}+  -> {path.name}")
    return out


def _phi_clone(phi, low_supply_mult=1.0, cov=None, tp_shift=0.0):
    import copy
    p = copy.deepcopy(phi)
    p["tiers"]["low"]["supply_per_day"] *= low_supply_mult
    if cov is not None:
        p["recycle_coverage"] = cov
    for t in p["tiers"].values():
        t["tp_shrunk"] += tp_shift
    return p


def selftest():
    """Fit-reproduction + directional sanity. Exits 1 on any failure."""
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL':4s}  {name}  {detail}")

    # --- fit reproduction: the two same-engine hand-tuned winners ---
    truth = {
        "v70": {"low": 0.10, "ovf": 0.035},   # Apex 2026-06-02/03 (starved book)
        "v71": {"low": 0.05, "ovf": 0.0},     # retune c14 2026-06-10 (dense book)
    }
    base_phi = None
    for v, want in truth.items():
        phi = load_phi(v)
        if phi is None or phi.get("supply_approx"):
            check(f"{v} phi available (pack + real supply row)", False)
            continue
        base_phi = base_phi or phi
        d = derive(phi)
        t = d["tier_alloc"]
        check(f"{v} upper ladder = canon 0.20/0.15/0.10",
              (t["ultra"], t["top"], t["mid"]) == (0.20, 0.15, 0.10))
        check(f"{v} low tier ~= {want['low']}", abs(t["low"] - want["low"]) <= 0.005,
              f"got {t['low']}")
        check(f"{v} overflow ~= {want['ovf']}", abs((d["tier_overflow"] or 0) - want["ovf"]) <= 0.003,
              f"got {d['tier_overflow']}")
        check(f"{v} threshold 75+", d["min_call_threshold"] == 75)

    if base_phi is None:
        print("\nSELFTEST FAILED (no usable phi)")
        return False

    # --- directional sanity battery (synthetic phi) ---
    d2 = derive(_phi_clone(base_phi, low_supply_mult=2.0))
    d1 = derive(base_phi)
    dh = derive(_phi_clone(base_phi, low_supply_mult=0.5))
    check("supply x2 -> low alloc shrinks", d2["tier_alloc"]["low"] < d1["tier_alloc"]["low"])
    check("supply x0.5 -> low alloc capped at canon 0.10", dh["tier_alloc"]["low"] == 0.10)
    covs = [derive(_phi_clone(base_phi, cov=c))["tier_overflow"] for c in (0.60, 0.70, 0.78, 0.90)]
    check("overflow monotone non-increasing in coverage",
          all(covs[i] >= covs[i + 1] for i in range(len(covs) - 1)), f"{covs}")
    check("overflow never exceeds collapse-safe ceiling", max(covs) <= COEFFS["OVF_MAX"] + 1e-9)
    check("overflow 0 at/above target coverage", covs[-1] == 0.0)
    dthin = derive(_phi_clone(base_phi, tp_shift=-0.035))  # push low/mid edges under EDGE_MIN
    check("thin edge -> low tier zeroed + threshold rises",
          dthin["tier_alloc"]["low"] == 0.0 and dthin["min_call_threshold"] > 75,
          f"thresh {dthin['min_call_threshold']}")

    print(f"\nSELFTEST {'OK' if ok else 'FAILED'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--derive", metavar="VERSION", help="print F(phi) for one version")
    ap.add_argument("--materialize", metavar="V,V,..",
                    help="write derived_portfolio.json into each version's research pack")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    if args.derive:
        phi = load_phi(args.derive)
        if phi is None:
            sys.exit(f"pack missing/partial for {args.derive}")
        print(json.dumps(derive(phi), indent=2))
        return
    if args.materialize:
        print("PRF materialize (instrument-only; F proposes, MC disposes):")
        for v in [s.strip() for s in args.materialize.split(",") if s.strip()]:
            materialize(v)
        return
    ap.error("give --derive vNN, --materialize v..,v.., or --selftest")


if __name__ == "__main__":
    main()
