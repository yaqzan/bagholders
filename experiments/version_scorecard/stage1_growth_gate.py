#!/usr/bin/env python
"""Stage 1 Hydration-Adjusted Growth gate (deterministic, pack-only, no MC).

Nets per-trade quality (WR15) against cash-rotation velocity (saturating
hydration) into ONE growth-rate proxy g, so a "WR up / N down" candidate gets an
automatic SHIP / FLAG / BLOCK verdict instead of a human reading the W4/W5/W6
sub-metrics by hand.

    g = ebar * lambda_eff

  ebar       per-trade LOG EDGE of the *filled* book. The cascade fills only
             `demand` slots/day, highest conviction first, so over-supply cannot
             inflate ebar -- it only changes WHICH tiers fill. Driven by WR15.
  lambda_eff demand * recycle_coverage. The velocity term. SATURATES at the
             14-slot / ~2.26-bar-hold book (extra supply earns no credit) and is
             drought/burstiness-aware through recycle_coverage (sum of per-day
             min(supply, demand), so a 0-signal day next to a 50-signal day
             scores worse than two steady days).

Why this is legitimately a Stage-1 (barrier-independent, MC-free) device:
  * deterministic -- no MC noise
  * the payoff (f, w, l) is HELD CONSTANT, so the scoring sweep cannot tune a
    barrier to win; g moves only when WR15 / supply move
  * g is a relative RANKING number (arbitrary units); only dG vs baseline is read

Two barriers, both must hold (closes the SVD generic-vs-option divergence):
  g_option   p = tp_shrunk  (option-aligned 30dte_opt TP rate; PRIMARY, tradable)
  g_generic  p = wr_shrunk  (generic K=2sigma/M=5sigma WR15; directional sanity)

The growth scalar can mask within-tier asymmetry (the ICH put-<10 trap), so W4
(per-discrete-bucket non-regression) stays a SEPARATE hard guard, never folded in.

W6 (gradient preservation) is computed here too (2026-06-11 reform), demoted from
a hard raw-monotonicity constraint to a noise-aware check that can FLAG but never
BLOCK: thin bands pool upward (95-100 at N~15-130/5y has a +/-7pp binomial CI --
gating it raw is statistical theater), an inversion counts only at two-proportion
z <= -2 on the pooled bands, and only inversions the candidate INTRODUCED escalate
(baseline-inherited inversions -- the CTSL 2026-05-08 false-fail class -- report
without escalating).

Inputs (already on disk -- no recalc, no MC):
  .cache/algorithm_versions/<v>/research_pack/utility_5y_wr15.json  (WR/TP by band)
  .cache/algorithm_versions/_scorecard/supply_burstiness.json       (recycle_coverage)

Run (Windows):
  set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && ^
  python experiments/version_scorecard/stage1_growth_gate.py --baseline v57 --candidate v58
  python experiments/version_scorecard/stage1_growth_gate.py --cohort v44,v52,v57,v58,v59,v60
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / ".cache" / "algorithm_versions"
SUPPLY_JSON = PACK_ROOT / "_scorecard" / "supply_burstiness.json"

# ---------------------------------------------------------------------------
# CONFIG -- nominal 30 DTE book. Held CONSTANT across candidates (that is what
# keeps the gate barrier-independent: a sweep cannot move these to win).
# ---------------------------------------------------------------------------

DEMAND_PER_DAY = 14.0 / 2.26          # match signal_supply.py: 14 slots / ~2.26-bar hold
DEFAULT_ACTIVE_DAYS = 1281            # 5y trading days (fallback if supply row missing)
FALLBACK_COVERAGE = 0.92             # cohort-median recycle_coverage when supply absent

# Per-tier allocation fraction (current 30 DTE cascade).
CALL_F = {"95-100": 0.20, "90-94": 0.15, "85-89": 0.15, "80-84": 0.10, "75-79": 0.10}
PUT_F = {"0-5": 0.12, "6-10": 0.12, "11-15": 0.12, "16-20": 0.10, "21-25": 0.08}

# Nominal option payoff as fraction of premium (30 DTE base TP/SL).
CALL_W, CALL_L = 0.33, 0.27           # +33% TP / -27% SL  -> BE ~ 45.0%
PUT_W, PUT_L = 0.35, 0.20             # +35% TP / -20% SL  -> BE ~ 36.4%

# Conviction midpoint (|score - 50|) per band -> fill order across BOTH sides.
CALL_CONV = {"95-100": 47.5, "90-94": 42.0, "85-89": 37.0, "80-84": 32.0, "75-79": 27.0}
PUT_CONV = {"0-5": 47.5, "6-10": 42.0, "11-15": 37.0, "16-20": 32.0, "21-25": 27.0}

# Verdict tolerances.
EPS = 0.01            # dG noise band (1%); FLAG within [-2eps,-eps], BLOCK below -2eps
W4_FLAG = 0.005       # per-band WR/TP regression that FLAGS (0.5pp -- matches doc W4)
W4_BLOCK = 0.015      # per-band regression that BLOCKS (1.5pp -- clear tail loss)
BOOTSTRAP = 2000      # binomial resamples for the dG confidence interval
MIN_GRADIENT_N = 100  # W6: bands thinner than this pool upward before the gradient check


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def log_edge(p, f, w, l):
    """Expected per-trade log return on TOTAL capital for a binary win/loss.
    win -> +f*w of capital, loss -> -f*l. Monotone increasing in p."""
    return p * math.log1p(f * w) + (1.0 - p) * math.log1p(-f * l)


def load_bands(version):
    """Return list of band dicts with conviction, side, f, payoff, n, wr, tp.
    Uses *_shrunk WR/TP (shrinkage so 95+ N~15 cannot swing the result)."""
    d = load_json(PACK_ROOT / version / "research_pack" / "utility_5y_wr15.json")
    if not isinstance(d, dict):
        return None, None
    sides = d.get("sides") or {}
    meta = d.get("version") or {}
    out = []
    for side, conv_map, fmap, w, l in (
        ("call", CALL_CONV, CALL_F, CALL_W, CALL_L),
        ("put", PUT_CONV, PUT_F, PUT_W, PUT_L),
    ):
        for b in (sides.get(side) or {}).get("bands") or []:
            band = b.get("band")
            if band not in conv_map:
                continue
            n = b.get("n")
            wr = b.get("wr_shrunk")
            tp = b.get("tp_shrunk")
            if n is None or (wr is None and tp is None):
                continue
            out.append({
                "band": band, "side": side, "conv": conv_map[band],
                "f": fmap[band], "w": w, "l": l,
                "n": float(n), "wr": wr, "tp": tp,
            })
    return out, meta


def load_supply(version):
    """recycle_coverage + active_days from supply_burstiness.json (fallback if absent),
    plus per-window coverages for the bear-tape binding-drought check."""
    d = load_json(SUPPLY_JSON) or {}
    row = (d.get("versions") or {}).get(version)
    if isinstance(row, dict) and row.get("complete"):
        wins = {}
        for name, m in (row.get("windows") or {}).items():
            cov = (m or {}).get("recycle_coverage")
            if cov is not None:
                wins[name] = {"coverage": cov,
                              "total_mean": (m.get("total") or {}).get("mean_per_day"),
                              "call_dry": (m.get("call") or {}).get("dry_day_rate")}
        return {
            "coverage": row.get("recycle_coverage", FALLBACK_COVERAGE),
            "active_days": row.get("active_days", DEFAULT_ACTIVE_DAYS),
            "windows": wins,
            "call_dry": (row.get("call") or {}).get("dry_day_rate"),
            "total_mean": (row.get("total") or {}).get("mean_per_day"),
            "approx": False,
        }
    return {"coverage": FALLBACK_COVERAGE, "active_days": DEFAULT_ACTIVE_DAYS,
            "windows": {}, "call_dry": None, "total_mean": None, "approx": True}


def greedy_fill(bands, active_days, demand, metric):
    """Fill `demand` slots/day, highest conviction first, from each band's mean
    daily supply (n/active_days). Returns (ebar, filled_total, fill_rows).

    metric in {'wr','tp'}. Bands missing that metric are skipped for ebar but
    still consume supply at their conviction rank (they DO compete for slots)."""
    rows = sorted(bands, key=lambda x: x["conv"], reverse=True)
    remaining = demand
    num = 0.0
    den = 0.0
    fills = []
    for b in rows:
        if remaining <= 1e-9:
            break
        supply = b["n"] / active_days
        take = min(supply, remaining)
        remaining -= take
        p = b.get(metric)
        e = log_edge(p, b["f"], b["w"], b["l"]) if p is not None else None
        fills.append({"band": b["band"], "side": b["side"], "take": take,
                      "p": p, "edge": e})
        if e is not None:
            num += take * e
            den += take
    ebar = (num / den) if den > 0 else 0.0
    return ebar, (demand - remaining), fills


def growth(bands, supply, metric, demand=DEMAND_PER_DAY):
    ebar, filled, fills = greedy_fill(bands, supply["active_days"], demand, metric)
    lam = demand * supply["coverage"]
    return {"g": ebar * lam, "ebar": ebar, "lambda_eff": lam,
            "filled": filled, "fills": fills}


def _dg_cov(bb, cb, ad_b, cov_b, ad_c, cov_c, metric):
    """dG (point + bootstrap CI) holding ebar at 5y (active_days drives the fill)
    and swapping ONLY lambda_eff coverage -- the per-window binding-drought calc.
    Quality is era-stable, so window-to-window dG varies almost entirely through
    coverage (where supply droughts live)."""
    sb = {"active_days": ad_b, "coverage": cov_b}
    sc = {"active_days": ad_c, "coverage": cov_c}
    gb = growth(bb, sb, metric)["g"]
    gc = growth(cb, sc, metric)["g"]
    dg = (gc / gb - 1.0) if gb else None
    _, p05, p95 = bootstrap_dg(bb, cb, sb, sc, metric)
    return {"dg": dg, "p05": p05, "p95": p95}


def growth_windows(bb, cb, bs, cs, metric):
    """Per-window dG: '5y' plus every named window present in BOTH versions. The
    gate gates on the BINDING (worst) window, not the 5y average -- a candidate
    that prunes supply specifically in thin bear tape is caught even when its
    5y-mean coverage looks fine."""
    ad_b, ad_c = bs["active_days"], cs["active_days"]
    res = {"5y": _dg_cov(bb, cb, ad_b, bs["coverage"], ad_c, cs["coverage"], metric)}
    shared = set(bs.get("windows", {})) & set(cs.get("windows", {}))
    for w in sorted(shared):
        res[w] = _dg_cov(bb, cb, ad_b, bs["windows"][w]["coverage"],
                         ad_c, cs["windows"][w]["coverage"], metric)
    return res


def bootstrap_dg(base_bands, cand_bands, base_sup, cand_sup, metric, seed=12345):
    """Binomial resample each band's win count -> recompute dG. Returns
    (p50, p05, p95) of the dG distribution so the noise band is the real
    sampling error of the thin top tiers, not an arbitrary constant."""
    rng = random.Random(seed)

    def resample(bands):
        # Normal approximation to Binomial(n, p)/n: mean p, sd sqrt(p(1-p)/n).
        # Equivalent to drawing n Bernoullis for these n (>=15) but O(1) per band.
        out = []
        for b in bands:
            nb = dict(b)
            p = b.get(metric)
            n = b["n"]
            if p is not None and n > 0:
                sd = math.sqrt(max(p * (1.0 - p), 1e-12) / n)
                nb[metric] = min(1.0, max(0.0, rng.gauss(p, sd)))
            out.append(nb)
        return out

    g0 = growth(base_bands, base_sup, metric)["g"]
    if g0 == 0:
        return None, None, None
    dgs = []
    for _ in range(BOOTSTRAP):
        gb = growth(resample(base_bands), base_sup, metric)["g"]
        gc = growth(resample(cand_bands), cand_sup, metric)["g"]
        if gb != 0:
            dgs.append(gc / gb - 1.0)
    if not dgs:
        return None, None, None
    dgs.sort()
    q = lambda f: dgs[max(0, min(len(dgs) - 1, int(round(f * (len(dgs) - 1)))))]
    return q(0.50), q(0.05), q(0.95)


def w4_check(base_bands, cand_bands):
    """Per-discrete-band WR15 & TP non-regression (the ICH-trap guard), made
    NOISE-AWARE so a thin-N wiggle (e.g. the N=78 90-94 tier) is not mistaken for
    a real tail loss. Reports every band with a >0.5pp drop, but only escalates to
    flag/BLOCK when the drop is statistically real (two-proportion z):

        noise  : drop < 0.5pp OR |z| < 2          (printed, ignored by verdict)
        flag   : drop >= 0.5pp AND z <= -2        (real but small/uncertain)
        BLOCK  : drop >= 1.5pp AND z <= -3 AND base_n >= 100   (large, significant)
    """
    bk = {(b["side"], b["band"]): b for b in base_bands}
    issues = []
    for c in cand_bands:
        b = bk.get((c["side"], c["band"]))
        if not b:
            continue
        for metric in ("wr", "tp"):
            bv, cv = b.get(metric), c.get(metric)
            bn, cn = b.get("n"), c.get("n")
            if bv is None or cv is None or not bn or not cn:
                continue
            d = cv - bv
            if d >= -W4_FLAG:
                continue
            se = math.sqrt(max(bv * (1 - bv), 1e-9) / bn + max(cv * (1 - cv), 1e-9) / cn)
            z = d / se if se > 0 else 0.0
            if z <= -3.0 and d <= -W4_BLOCK and bn >= 100:
                level = "BLOCK"
            elif z <= -2.0:
                level = "flag"
            else:
                level = "noise"
            issues.append({
                "side": c["side"], "band": c["band"], "metric": metric,
                "delta_pp": d * 100.0, "z": z, "base_n": bn, "n": cn, "level": level,
            })
    return issues


def _gradient_groups(cand_bands, side):
    """Group one side's bands conviction-descending; a group closes once its
    pooled CANDIDATE N reaches MIN_GRADIENT_N (so 95-100 at N~25 pools into
    90-94 -> a measurable '95-100+90-94'). A too-thin tail merges into the
    previous group. The grouping is decided on the candidate and REUSED for the
    baseline so adjacent pairs align between the two versions."""
    order = [b for b in sorted(cand_bands, key=lambda x: x["conv"], reverse=True)
             if b["side"] == side]
    groups, cur, cur_n = [], [], 0.0
    for b in order:
        cur.append(b["band"])
        cur_n += b["n"]
        if cur_n >= MIN_GRADIENT_N:
            groups.append(cur)
            cur, cur_n = [], 0.0
    if cur:
        if groups:
            groups[-1].extend(cur)
        else:
            groups.append(cur)
    return groups


def _pooled_series(bands, groups, side, metric):
    """n-weighted pooled (shrunk) metric per group, conviction-descending.
    None for a group with no usable bands in this version."""
    bk = {b["band"]: b for b in bands if b["side"] == side}
    out = []
    for g in groups:
        rows = [bk[x] for x in g if x in bk and bk[x].get(metric) is not None]
        if not rows:
            out.append(None)
            continue
        n = sum(r["n"] for r in rows)
        p = sum(r["n"] * r[metric] for r in rows) / n
        out.append({"label": "+".join(g), "n": n, "p": p})
    return out


def _pair_z(hi, lo):
    """Two-proportion z for adjacent pooled bands (negative = inversion)."""
    d = hi["p"] - lo["p"]
    se = math.sqrt(max(hi["p"] * (1 - hi["p"]), 1e-9) / hi["n"]
                   + max(lo["p"] * (1 - lo["p"]), 1e-9) / lo["n"])
    return d, (d / se if se > 0 else 0.0)


def w6_gradient(base_bands, cand_bands):
    """W6 gradient preservation, demoted (2026-06-11 reform) from a hard
    raw-monotonicity constraint to a noise-aware report that can FLAG but never
    BLOCK:

        noise     : adjacent inversion exists but |z| < 2 -- printed, ignored
        inherited : statistically real (z <= -2) but ALREADY present and real in
                    the baseline (the CTSL 2026-05-08 false-fail class) -- printed,
                    never escalated; candidates don't answer for inherited anomalies
        flag      : real (z <= -2) AND introduced by the candidate -- FLAGs the
                    verdict (blocks auto-SHIP, never BLOCKs)
    """
    issues = []
    for side in ("call", "put"):
        groups = _gradient_groups(cand_bands, side)
        if len(groups) < 2:
            continue
        for metric in ("tp", "wr"):
            cs = _pooled_series(cand_bands, groups, side, metric)
            bs_ = _pooled_series(base_bands, groups, side, metric)
            for i in range(len(cs) - 1):
                hi, lo = cs[i], cs[i + 1]
                if hi is None or lo is None:
                    continue
                d, z = _pair_z(hi, lo)
                if d >= 0:
                    continue
                if z > -2.0:
                    level = "noise"
                else:
                    inherited = False
                    bh, bl = bs_[i], bs_[i + 1]
                    if bh is not None and bl is not None:
                        bd, bz = _pair_z(bh, bl)
                        inherited = bd < 0 and bz <= -2.0
                    level = "inherited" if inherited else "flag"
                issues.append({"side": side, "metric": metric,
                               "hi": hi["label"], "lo": lo["label"],
                               "delta_pp": d * 100.0, "z": z, "level": level,
                               "n_hi": hi["n"], "n_lo": lo["n"]})
    return issues


def verdict(stats, w4_issues, w6_issues=()):
    """SHIP / FLAG / BLOCK, statistically rather than on a point estimate, so a
    tie-band candidate (CI straddling 0) is never stopped out:

      BLOCK : a *confident* regression -- worst-barrier p95 (upper bound) still
              < -eps -- OR a large+significant W4 tail loss (z<=-3).
      SHIP  : *confidently* fine -- worst-barrier point dG >= -eps AND worst p05
              >= -2eps AND no real W4 dip.
      FLAG  : everything between (tie, wide CI, a real-but-small W4 dip, or a
              candidate-introduced real W6 gradient inversion) -->
              scoring-neutral, route to Stage 2/3 confirmation, do not auto-green.

    W6 can FLAG but never BLOCK (gradient damage alone is a quality-shape
    warning, not a tail loss -- W4 owns tail losses).

    stats = {'tp':{dg,p05,p95}, 'wr':{...}}; min() across barriers = dual-barrier
    'both must hold'."""
    w4_block = any(i["level"] == "BLOCK" for i in w4_issues)
    w4_flag = any(i["level"] == "flag" for i in w4_issues)
    w6_flag = any(i["level"] == "flag" for i in w6_issues)
    pts = [stats[m]["dg"] for m in ("tp", "wr") if stats[m]["dg"] is not None]
    p05s = [stats[m]["p05"] for m in ("tp", "wr") if stats[m]["p05"] is not None]
    p95s = [stats[m]["p95"] for m in ("tp", "wr") if stats[m]["p95"] is not None]
    worst_pt = min(pts) if pts else None
    worst_p05 = min(p05s) if p05s else None
    worst_p95 = min(p95s) if p95s else None
    if w4_block:
        return "BLOCK", "W4 large + significant discrete-bucket tail loss (z<=-3)"
    # BLOCK if EITHER barrier confidently regresses (dual-barrier veto -- guards
    # against an option-aligned metric that is itself a barrier-overfit artifact).
    if worst_p95 is not None and worst_p95 < -EPS:
        return "BLOCK", f"confident growth regression: best-case dG (p95) = {worst_p95*100:+.1f}% < -eps"
    if worst_pt is None:
        return "FLAG", "insufficient data for dG"
    # SHIP keys on the PRIMARY (option) barrier being confidently fine. The generic
    # barrier only has to NOT confidently regress (already guaranteed above) -- a
    # flat directional cross-check must not veto a confident tradable win.
    opt = stats.get("tp", {})
    if (opt.get("dg") is not None and opt["dg"] >= -EPS
            and (opt.get("p05") is None or opt["p05"] >= -2 * EPS)
            and not w4_flag and not w6_flag):
        return "SHIP", (f"option dG {opt['dg']*100:+.2f}% confidently >= -eps; "
                        f"generic not regressed; W4/W6 clean")
    bits = []
    if worst_pt < -EPS:
        bits.append(f"dG {worst_pt*100:+.2f}% point-negative but CI straddles 0 (tie)")
    if w4_flag:
        bits.append("real W4 bucket dip (z<=-2)")
    if w6_flag:
        bits.append("candidate-introduced real W6 gradient inversion (z<=-2)")
    if worst_p05 is not None and worst_p05 < -2 * EPS:
        bits.append(f"wide CI (p05 {worst_p05*100:+.1f}%)")
    return "FLAG", "; ".join(bits) if bits else "scoring-neutral; route to Stage 2/3"


def fmt(x, nd=4):
    return "n/a" if x is None else f"{float(x):.{nd}f}"


def run_pair(baseline, candidate):
    bb, bm = load_bands(baseline)
    cb, cm = load_bands(candidate)
    if not bb or not cb:
        print(f"missing utility pack for {baseline if not bb else candidate}")
        return
    bs, cs = load_supply(baseline), load_supply(candidate)
    # SUPPLY-FALLBACK GUARD (2026-05-31): an absent candidate supply row makes
    # load_supply use FALLBACK_COVERAGE (0.92) -- HIGHER than real coverages
    # (~0.84-0.86), which INFLATES lambda_eff and produces a FALSE SHIP for any
    # N-cutting candidate (this is what made v63/BBLT look like a SHIP when its
    # real coverage 0.844 actually BLOCKs). Neutralize: borrow the baseline's
    # coverage so dG reflects only the ebar (quality) change, and never auto-SHIP
    # on approximated supply. Real fix = run signal_supply.py for the candidate.
    _cand_supply_approx = bool(cs.get("approx"))
    if _cand_supply_approx:
        cs["coverage"] = bs["coverage"]
        cs["windows"] = {w: {"coverage": m.get("coverage")} for w, m in (bs.get("windows") or {}).items()}

    print("=" * 88)
    print(f"STAGE-1 GROWTH GATE   baseline={baseline}  candidate={candidate}")
    print(f"  {baseline}: {bm.get('git_commit')} {bm.get('git_message')}")
    print(f"  {candidate}: {cm.get('git_commit')} {cm.get('git_message')}")
    print("=" * 88)
    stats = {}
    for metric, label in (("tp", "OPTION (tp_shrunk)  PRIMARY"), ("wr", "GENERIC (wr_shrunk)")):
        gb = growth(bb, bs, metric)
        gc = growth(cb, cs, metric)
        rw = growth_windows(bb, cb, bs, cs, metric)
        valid = {w: r for w, r in rw.items() if r["dg"] is not None}
        binding = min(valid, key=lambda w: valid[w]["dg"]) if valid else "5y"
        b = rw[binding]
        # the verdict gates on the BINDING (worst) window, not the 5y mean
        stats[metric] = {"dg": b["dg"], "p05": b["p05"], "p95": b["p95"],
                         "binding": binding, "dg_5y": rw["5y"]["dg"]}
        print(f"\n[{label}]")
        print(f"  baseline  ebar={fmt(gb['ebar'])}  g_5y={fmt(gb['g'],5)}  "
              f"cov_5y={fmt(bs['coverage'],3)}{'~' if bs['approx'] else ''}")
        print(f"  candidate ebar={fmt(gc['ebar'])}  g_5y={fmt(gc['g'],5)}  "
              f"cov_5y={fmt(cs['coverage'],3)}{'~' if cs['approx'] else ''}")
        wstr = "  ".join(f"{w}:{rw[w]['dg']*100:+.1f}%" for w in rw
                         if w != "5y" and rw[w]["dg"] is not None)
        print(f"  dG 5y = {fmt(rw['5y']['dg']*100 if rw['5y']['dg'] is not None else None, 2)}%"
              f"   per-window [{wstr or 'none'}]")
        print(f"  BINDING window = {binding}: dG {b['dg']*100:+.2f}%  p05/p95 = "
              f"{fmt(b['p05']*100 if b['p05'] is not None else None,1)}% / "
              f"{fmt(b['p95']*100 if b['p95'] is not None else None,1)}%")

    issues = w4_check(bb, cb)
    print("\n[W4 per-discrete-bucket guard]  (separate guard; noise-aware z; not folded into g)")
    escalated = [i for i in issues if i["level"] != "noise"]
    if not escalated:
        print("  clean -- no statistically real (z<=-2) tradable-band WR15/TP regression")
    for i in issues:
        tag = i["level"] if i["level"] != "noise" else "noise"
        print(f"  {tag:5s} {i['side']:4s} {i['band']:6s} {i['metric']}  {i['delta_pp']:+.2f}pp "
              f"z={i['z']:+.2f}  N {int(i['base_n'])}->{int(i['n'])}")

    w6 = w6_gradient(bb, cb)
    print(f"\n[W6 gradient (noise-aware; thin bands pooled at N<{MIN_GRADIENT_N}; "
          f"FLAG-only, never BLOCK)]")
    w6_esc = [i for i in w6 if i["level"] == "flag"]
    if not w6:
        print("  clean -- pooled conviction gradient monotone on both barriers")
    elif not w6_esc:
        print("  no candidate-INTRODUCED real inversion (inherited/noise only)")
    for i in w6:
        print(f"  {i['level']:9s} {i['side']:4s} {i['metric']}  "
              f"{i['hi']} < {i['lo']}  {i['delta_pp']:+.2f}pp z={i['z']:+.2f}  "
              f"N {int(i['n_hi'])}/{int(i['n_lo'])}")

    v, why = verdict(stats, issues, w6)
    if _cand_supply_approx:
        print(f"\n  [!] candidate '{candidate}' supply was APPROXIMATED (no row in "
              f"supply_burstiness.json); coverage borrowed from baseline.")
        print(f"      Run:  python experiments/version_scorecard/signal_supply.py "
              f"--versions {baseline},{candidate}   for a real verdict.")
        if v == "SHIP":
            v, why = "FLAG", "candidate supply approximated -> NOT auto-SHIP; compute real supply first"
    print(f"\n  ==> VERDICT: {v}   ({why})")
    print()
    return v


def selftest(base_version="v71"):
    """Pack-independent verdict regression test (2026-06-11). The original
    --replay anchors were INVALIDATED when pre-v69 versions were honest-recalced
    (the 2026-06-01 cross-version campaign overwrote e.g. v40's rows, erasing the
    inflated-era disaster delta the v40->v42 BLOCK was validated against). This
    self-test synthesizes the cases from any live pack so it cannot rot:

      identity  : candidate == baseline                       -> must FLAG
                  (scoring-neutral ties FLAG by design -- Stage 1 never
                  auto-greens a change whose value is downstream)
      big win   : WR/TP +13pp uniform (reverse-v42 signature) -> must SHIP
      disaster  : WR/TP -13pp, N x2.37 (the v42 signature)    -> must BLOCK
      tail-loss : one big band's TP -3pp only (W4 trap)       -> must FLAG/BLOCK, not SHIP
    """
    import copy
    bb, _ = load_bands(base_version)
    if not bb:
        print(f"need a hydrated pack for {base_version}")
        return False
    bs = load_supply(base_version)

    def mutate(bands, dwr=0.0, n_mult=1.0, only_band=None):
        out = []
        for b in bands:
            nb = copy.deepcopy(b)
            if only_band is None or (nb["side"], nb["band"]) == only_band:
                for m in ("wr", "tp"):
                    if nb.get(m) is not None:
                        nb[m] = min(1.0, max(0.0, nb[m] + dwr))
                nb["n"] = nb["n"] * n_mult
            out.append(nb)
        return out

    big_call = max((b for b in bb if b["side"] == "call"), key=lambda x: x["n"])
    cases = [
        ("identity / scoring-neutral tie (expect FLAG by design)", bb, "FLAG"),
        ("reverse-v42 signature: WR/TP +13pp uniform (expect SHIP)",
         mutate(bb, dwr=+0.13), "SHIP"),
        ("v42-signature disaster: WR/TP -13pp, N x2.37 (expect BLOCK)",
         mutate(bb, dwr=-0.13, n_mult=2.37), "BLOCK"),
        (f"single-band tail loss: {big_call['band']} TP/WR -3pp (expect not-SHIP)",
         mutate(bb, dwr=-0.03, only_band=("call", big_call["band"])), "not-SHIP"),
    ]
    ok = True
    for note, cb, want in cases:
        stats = {}
        for metric in ("tp", "wr"):
            rw = growth_windows(bb, cb, bs, bs, metric)
            valid = {w: r for w, r in rw.items() if r["dg"] is not None}
            binding = min(valid, key=lambda w: valid[w]["dg"]) if valid else "5y"
            b = rw[binding]
            stats[metric] = {"dg": b["dg"], "p05": b["p05"], "p95": b["p95"]}
        v, why = verdict(stats, w4_check(bb, cb), w6_gradient(bb, cb))
        passed = (v == want) if want != "not-SHIP" else (v != "SHIP")
        ok &= passed
        print(f"  {'PASS' if passed else 'FAIL':4s}  want {want:8s} got {v:5s}  {note}")
        if not passed:
            print(f"        ({why})")
    print(f"\nSELFTEST {'OK' if ok else 'FAILED'} (substrate: {base_version} pack)")
    return ok


def demo_drought(base_version, window="2022", cut=0.65):
    """Teeth test: clone a real version as its own candidate, identical WR, but cut
    ONE window's coverage. The 5y mean barely moves; the binding-window gate fires.
    Proves the bear-tape check catches a supply drought the 5y average would miss."""
    import copy
    bb, _ = load_bands(base_version)
    bs = load_supply(base_version)
    if not bb or window not in bs.get("windows", {}):
        print(f"need {base_version} bands + a '{window}' window in supply")
        return
    cs = copy.deepcopy(bs)
    orig = cs["windows"][window]["coverage"]
    cs["windows"][window]["coverage"] = orig * cut
    print("=" * 88)
    print(f"DEMO drought: {base_version} clone, WR IDENTICAL, only {window} coverage "
          f"cut x{cut} ({orig:.3f} -> {orig*cut:.3f})")
    print("=" * 88)
    stats = {}
    for metric in ("tp", "wr"):
        rw = growth_windows(bb, bb, bs, cs, metric)
        valid = {w: r for w, r in rw.items() if r["dg"] is not None}
        binding = min(valid, key=lambda w: valid[w]["dg"]) if valid else "5y"
        b = rw[binding]
        stats[metric] = {"dg": b["dg"], "p05": b["p05"], "p95": b["p95"]}
        print(f"  [{metric}] dG 5y={fmt(rw['5y']['dg']*100,2)}%  ->  BINDING={binding} "
              f"dG={fmt(b['dg']*100,2)}%  p95={fmt(b['p95']*100 if b['p95'] is not None else None,1)}%")
    v, why = verdict(stats, [])  # WR identical => no W4 issues
    print(f"\n  ==> VERDICT: {v}  ({why})")
    print(f"  (5y mean would have said tie/SHIP; the {window} binding window catches the drought)\n")


def run_cohort(versions):
    rows = []
    for v in versions:
        bands, meta = load_bands(v)
        if not bands:
            continue
        sup = load_supply(v)
        go = growth(bands, sup, "tp")
        gg = growth(bands, sup, "wr")
        n75 = sum(b["n"] for b in bands if b["side"] == "call")
        rows.append({
            "v": v, "g_opt": go["g"], "ebar_opt": go["ebar"], "g_gen": gg["g"],
            "ebar_gen": gg["ebar"], "cov": sup["coverage"], "approx": sup["approx"],
            "call_n": n75, "msg": (meta or {}).get("git_message"),
        })
    rows.sort(key=lambda r: r["g_opt"], reverse=True)
    print("=" * 100)
    print("COHORT RANK by g_option (primary). g = ebar(filled-book log-edge) x lambda_eff(demand x recycle_cov)")
    print("=" * 100)
    print(f"{'rank':>4} {'ver':>5} {'g_opt':>9} {'ebar_opt':>9} {'g_gen':>9} {'cov':>6} "
          f"{'callN':>6}  message")
    for i, r in enumerate(rows, 1):
        print(f"{i:>4} {r['v']:>5} {r['g_opt']*1000:>9.4f} {r['ebar_opt']*1000:>9.4f} "
              f"{r['g_gen']*1000:>9.4f} {fmt(r['cov'],3):>6}{'~' if r['approx'] else ' '} "
              f"{int(r['call_n']):>6}  {r['msg']}")
    print("\n(g/ebar shown x1000 for readability; cov '~' = supply approximated)")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline")
    ap.add_argument("--candidate")
    ap.add_argument("--cohort", help="comma list of versions to rank")
    ap.add_argument("--demo-drought", metavar="VERSION",
                    help="teeth test: clone VERSION, identical WR, cut 2022 coverage, show BLOCK")
    ap.add_argument("--selftest", nargs="?", const="v71", metavar="VERSION",
                    help="pack-independent verdict regression test (identity->SHIP, "
                         "v42-signature disaster->BLOCK, single-band tail loss->not-SHIP)")
    ap.add_argument("--replay", action="store_true",
                    help="run the 4 documented case pairs. WARNING: anchors INVALIDATED "
                         "2026-06 -- pre-v69 packs were honest-recalced, erasing the "
                         "inflated-era deltas these expectations encode. Use --selftest "
                         "for verdict regression testing; --replay is historical only.")
    args = ap.parse_args()

    if args.selftest:
        import sys
        sys.exit(0 if selftest(args.selftest) else 1)

    if args.replay:
        print("[!] NOTE: replay anchors were invalidated by the 2026-06 honest-era "
              "recalcs of pre-v69 packs (e.g. v40's inflated rows are gone, so the "
              "v40->v42 disaster delta no longer exists on disk). Expectations in "
              "the case notes are HISTORICAL. Use --selftest for regression testing.")
        cases = [
            ("v39", "v43", "MCD ship (expect SHIP: WR up, N down, supply over-provisioned)"),
            ("v43", "v44", "ICH ship (expect SHIP on g; W4 should FLAG the put <10 tail)"),
            ("v40", "v42", "rolling-weekly (expect BLOCK: N +137%, WR15 -13pp)"),
            ("v57", "v58", "continuation retune (expect BLOCK/FLAG: reverted in prod)"),
        ]
        results = []
        for base, cand, note in cases:
            print("\n" + "#" * 88)
            print(f"# CASE: {note}")
            print("#" * 88)
            results.append((note, run_pair(base, cand)))
        print("\n" + "=" * 88)
        print("REPLAY SUMMARY")
        print("=" * 88)
        for note, v in results:
            print(f"  {str(v):5s}  {note}")
        return

    if args.demo_drought:
        demo_drought(args.demo_drought)
        return

    if args.cohort:
        run_cohort([s.strip() for s in args.cohort.split(",") if s.strip()])
        return

    if args.baseline and args.candidate:
        run_pair(args.baseline, args.candidate)
        return

    ap.error("give --replay, --cohort v..,v.., or --baseline X --candidate Y")


if __name__ == "__main__":
    main()
