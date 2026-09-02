#!/usr/bin/env python
"""Deterministic version-comparison scorecard (pack-only, no DB).

Collapses per-version research-pack metrics into a 5-pillar composite
(Q=signal quality, H=hydration efficiency, G=growth velocity,
R=drawdown resilience, S=stability) plus a Realizable-Growth-Efficiency
(RGE) number, per portfolio profile, plus a champion matrix.

Pure stdlib. No DB, no pandas. Reads JSON under
.cache/algorithm_versions/<vN>/research_pack/.

Run (Windows):
    set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && python experiments/version_scorecard/score_versions.py
"""

import json
import math
import os
import statistics
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG (editable)
# ---------------------------------------------------------------------------

# Repo root: this file lives at <root>/experiments/version_scorecard/score_versions.py
ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / ".cache" / "algorithm_versions"
OUT_DIR = PACK_ROOT / "_scorecard"
OUT_JSON = OUT_DIR / "scorecard.json"

VERSIONS = ["v44", "v46", "v50", "v52", "v53", "v57", "v58", "v59", "v60", "v65", "v66"]
PROFILES = ["sentinel", "core", "apex"]

# Windows that MUST be metrics.complete==true for a (version, profile) cell to be usable.
REQUIRED_WINDOWS = ["2020_now", "22_now", "covid_crash_2020"]

# Growth / resilience window sets.
# G uses two NON-OVERLAPPING regimes (de-overlap fix): 2020-2021 crash+rebound
# and 2022-now bear-start+recent, instead of the +0.94-correlated {2020_now,22_now}
# (22_now is the tail of 2020_now). Falls back to the old pair if the de-overlapped
# window is missing for a version.
G_WINDOWS = ["covid_cycle_2020_2021", "22_now"]
G_WINDOWS_FALLBACK = ["2020_now", "22_now"]
R_WINDOWS = ["covid_crash_2020", "2020_now", "22_now"]
S_YEAR_WINDOWS = ["year_2020", "year_2021", "year_2022", "year_2023", "year_2024", "year_2025"]

# Per-profile composite weights + RGE risk dial (theta). Editable.
PROFILE_WEIGHTS = {
    "sentinel": {"wQ": 0.20, "wH": 0.15, "wG": 0.10, "wR": 0.35, "wS": 0.20, "theta": 1.00},
    "core":     {"wQ": 0.20, "wH": 0.15, "wG": 0.25, "wR": 0.25, "wS": 0.15, "theta": 0.50},
    "apex":     {"wQ": 0.20, "wH": 0.10, "wG": 0.45, "wR": 0.10, "wS": 0.15, "theta": 0.25},
}

# Call bands top->bottom for the gradient monotonicity check.
CALL_BAND_ORDER = ["95-100", "90-94", "85-89", "80-84", "75-79"]
GRADIENT_TOL = 0.005  # 0.5pp tolerance for monotone non-increasing wr_shrunk

# Profile-specialist flag threshold: rank spread (max-min across profiles) >= this.
SPECIALIST_SPREAD = 4

# Adjacent composite gap below which two versions are a statistical tie (one band, not a rank).
TIE_EPS = 0.05
# A composite is "single-pillar dependent" if one weighted |z| term is >= this fraction of total |contribution|.
SINGLE_PILLAR_FRAC = 0.45


# ---------------------------------------------------------------------------
# IO helpers (never crash the whole run on one bad cell)
# ---------------------------------------------------------------------------

def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def pack_dir(version):
    return PACK_ROOT / version / "research_pack"


def safe_num(x):
    """Return float(x) if it's a finite real number, else None."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


# ---------------------------------------------------------------------------
# Raw metric extraction
# ---------------------------------------------------------------------------

def extract_Q(version):
    """Profile-independent signal-quality block from utility_5y_wr15.json.

    Returns dict with: complete, Q_raw, cum_wr15_75plus, cum_wr15_25minus,
    useful_N, gradient_ok, plus version meta.
    """
    out = {
        "version": version,
        "complete": False,
        "Q_raw": None,             # per-trade QUALITY fraction (N-NEUTRAL): mean shrunk WR15 of call/put cohorts
        "quality_call": None,      # shrunk N-weighted cum WR15 over 75+ call bands (fraction)
        "quality_put": None,       # shrunk N-weighted cum WR15 over <=25 put bands (fraction)
        "utility_wr": None,        # OLD volume metric (sum n*wr); reported as a diagnostic only, NOT Q
        "cum_wr15_75plus": None,   # % (raw, dashboard-style)
        "cum_wr15_25minus": None,  # % (raw)
        "useful_N": None,
        "gradient_ok": 0,
        "git_commit": None,
        "git_message": None,
    }
    d = load_json(pack_dir(version) / "utility_5y_wr15.json")
    if not isinstance(d, dict):
        return out

    ver = d.get("version") or {}
    out["git_commit"] = ver.get("git_commit")
    out["git_message"] = ver.get("git_message")

    total = d.get("total") or {}
    out["utility_wr"] = safe_num(total.get("utility_wr"))  # diagnostic only (volume-weighted)
    out["useful_N"] = safe_num(total.get("n"))

    sides = d.get("sides") or {}
    call = sides.get("call") or {}
    put = sides.get("put") or {}
    call_bands = call.get("bands") or []
    put_bands = put.get("bands") or []

    def nweighted_wr(bands):
        num = 0.0
        den = 0.0
        for b in bands:
            wr = safe_num(b.get("wr"))
            n = safe_num(b.get("n"))
            if wr is None or n is None:
                continue
            num += n * wr
            den += n
        if den <= 0:
            return None
        return 100.0 * (num / den)

    out["cum_wr15_75plus"] = nweighted_wr(call_bands)
    out["cum_wr15_25minus"] = nweighted_wr(put_bands)

    # Q (signal QUALITY) = N-neutral per-trade WR15, shrinkage-adjusted so tiny
    # top tiers (95+ N~16) do not swing it. Replaces the old volume-weighted
    # utility_wr, which rewarded raw signal COUNT (the v58 mis-crowning bug).
    def shrunk_cum_wr(bands):
        num = 0.0
        den = 0.0
        for b in bands:
            ws = safe_num(b.get("wr_shrunk"))
            n = safe_num(b.get("n"))
            if ws is None or n is None:
                continue
            num += n * ws
            den += n
        if den <= 0:
            return None
        return num / den  # fraction 0..1, rate not count -> volume-neutral

    out["quality_call"] = shrunk_cum_wr(call_bands)
    out["quality_put"] = shrunk_cum_wr(put_bands)
    q_parts = [x for x in (out["quality_call"], out["quality_put"]) if x is not None]
    out["Q_raw"] = (sum(q_parts) / len(q_parts)) if q_parts else None

    # gradient_ok: call bands' wr_shrunk monotone non-increasing 95-100 -> 75-79
    # within GRADIENT_TOL tolerance.
    by_band = {}
    for b in call_bands:
        band = b.get("band")
        ws = safe_num(b.get("wr_shrunk"))
        if band is not None and ws is not None:
            by_band[band] = ws
    seq = [by_band[bn] for bn in CALL_BAND_ORDER if bn in by_band]
    grad_ok = 1
    if len(seq) >= 2:
        for i in range(1, len(seq)):
            # going DOWN the tiers (95->75): higher tier should be >= lower tier
            # i.e. seq[i-1] >= seq[i] - tol  (non-increasing within tolerance)
            if seq[i] > seq[i - 1] + GRADIENT_TOL:
                grad_ok = 0
                break
    else:
        # not enough data to assess -> treat as not-ok (conservative)
        grad_ok = 0
    out["gradient_ok"] = grad_ok

    # Completeness for Q: needs the utility file with a Q_raw value.
    out["complete"] = out["Q_raw"] is not None and bool(call_bands)
    return out


def load_profile_windows(version, profile):
    """Return {window_name: metrics_dict} for a profile, or None if file missing."""
    d = load_json(pack_dir(version) / f"stress_windows_{profile}.json")
    if not isinstance(d, dict):
        return None
    out = {}
    for w in d.get("windows") or []:
        win = w.get("window") or {}
        name = win.get("name")
        if name is None:
            continue
        out[name] = w.get("metrics") or {}
    return out


def profile_cell_usable(windows_map):
    """A (version,profile) cell is usable only if all REQUIRED_WINDOWS have complete==true."""
    if not windows_map:
        return False
    for nm in REQUIRED_WINDOWS:
        m = windows_map.get(nm)
        if not isinstance(m, dict) or m.get("complete") is not True:
            return False
    return True


def _days_between(peak_date, trough_date):
    """Inclusive-exclusive day delta between two YYYY-MM-DD strings; None on failure."""
    from datetime import date
    try:
        py, pm, pd = (int(x) for x in str(peak_date).split("-"))
        ty, tm, td = (int(x) for x in str(trough_date).split("-"))
        return (date(ty, tm, td) - date(py, pm, pd)).days
    except Exception:
        return None


def extract_profile_metrics(version, profile, windows_map):
    """Compute G,R,H,S raw metrics + reported diagnostics for one (version, profile)."""
    out = {
        "version": version,
        "profile": profile,
        "usable": False,
        # G
        "G_raw": None,
        "log10x_2020_now": None,
        "log10x_22_now": None,
        "days_to_1m": None,
        "days_to_1m_source": None,
        # R
        "R_raw": None,
        "worst_dd_pct": None,
        "covid_crash_dd_pct": None,
        "dd_2020_now_pct": None,
        "underwater_days": None,
        # H
        "H_raw": None,
        "hydration_gap_per_day": None,
        "slot_util_pct": None,
        "pool_full_rate": None,
        "raw_signals_per_day": None,
        "avg_hold_bars": None,
        "accepted_entries_per_day": None,
        "target_entries_per_day": None,
        "supply_ratio": None,
        # S
        "S_raw": None,
        "year_log10x_complete_n": 0,
        "horizon_consistency": None,
    }
    if not profile_cell_usable(windows_map):
        return out
    out["usable"] = True

    def wm(name):
        return windows_map.get(name) or {}

    # ---- G: mean log10_equity_multiple over two NON-OVERLAPPING regimes ----
    # (covid_cycle_2020_2021 + 22_now); fall back to {2020_now,22_now} if the
    # de-overlapped window is missing. Fixes the +0.94 correlation of the old pair.
    def _g_over(window_names):
        vals = []
        for nm in window_names:
            x = safe_num(wm(nm).get("log10_equity_multiple"))
            if x is not None:
                vals.append(x)
        return (sum(vals) / len(vals)) if vals else None, len(vals)

    out["log10x_2020_now"] = safe_num(wm("2020_now").get("log10_equity_multiple"))
    out["log10x_22_now"] = safe_num(wm("22_now").get("log10_equity_multiple"))
    g_primary, g_n = _g_over(G_WINDOWS)
    if g_n >= 2:
        out["G_raw"] = g_primary
    else:
        g_fb, _ = _g_over(G_WINDOWS_FALLBACK)
        out["G_raw"] = g_fb

    # days_to_1m: 22_now calendar_days, fallback 2020_now
    t22 = wm("22_now").get("time_to_1m")
    t20 = wm("2020_now").get("time_to_1m")
    d22 = safe_num((t22 or {}).get("calendar_days")) if isinstance(t22, dict) else None
    d20 = safe_num((t20 or {}).get("calendar_days")) if isinstance(t20, dict) else None
    if d22 is not None:
        out["days_to_1m"] = d22
        out["days_to_1m_source"] = "22_now"
    elif d20 is not None:
        out["days_to_1m"] = d20
        out["days_to_1m_source"] = "2020_now"

    # ---- R: -max(max_dd_pct over R_WINDOWS) ; report worst + covid_crash ----
    dd_vals = []
    for nm in R_WINDOWS:
        v = safe_num(wm(nm).get("max_dd_pct"))
        if v is not None:
            dd_vals.append(v)
    out["covid_crash_dd_pct"] = safe_num(wm("covid_crash_2020").get("max_dd_pct"))
    out["dd_2020_now_pct"] = safe_num(wm("2020_now").get("max_dd_pct"))
    if dd_vals:
        worst = max(dd_vals)
        out["worst_dd_pct"] = worst
        out["R_raw"] = -1.0 * worst

    # underwater_days: trough - peak for 2020_now max_dd_event
    ev = wm("2020_now").get("max_dd_event")
    if isinstance(ev, dict):
        out["underwater_days"] = _days_between(ev.get("peak_date"), ev.get("trough_date"))

    # ---- H: SATURATING 'just-enough' hydration band ----
    # H = min(1, accepted/target) for 2020_now. Saturates at 1.0 (a full 14-slot
    # book) so over-supply earns NO extra credit -- excess signals can only help via
    # Q (per-trade WR), never by inflating hydration. Below 1.0 = book-starved.
    h = wm("2020_now")
    acc = safe_num(h.get("accepted_entries_per_day"))
    tgt = safe_num(h.get("target_entries_per_day"))
    out["accepted_entries_per_day"] = acc
    out["target_entries_per_day"] = tgt
    if acc is not None and tgt is not None and tgt > 0:
        out["H_raw"] = min(1.0, acc / tgt)
    out["hydration_gap_per_day"] = safe_num(h.get("hydration_gap_per_day"))
    out["slot_util_pct"] = safe_num(h.get("avg_slot_utilization_pct"))
    out["pool_full_rate"] = safe_num(h.get("pool_full_rate"))
    out["raw_signals_per_day"] = safe_num(h.get("raw_signals_per_day"))
    out["avg_hold_bars"] = safe_num(h.get("avg_hold_bars"))
    # supply_ratio (diagnostic): raw signal supply vs slot demand. >1 = over-supplied
    # (excess N that should convert to WR, not hydration); <1 = genuinely starved.
    if out["raw_signals_per_day"] is not None and tgt is not None and tgt > 0:
        out["supply_ratio"] = out["raw_signals_per_day"] / tgt

    # ---- S: -popstdev(log10_equity_multiple over complete year windows) ----
    year_vals = []
    for nm in S_YEAR_WINDOWS:
        m = wm(nm)
        if m.get("complete") is True:
            v = safe_num(m.get("log10_equity_multiple"))
            if v is not None:
                year_vals.append(v)
    out["year_log10x_complete_n"] = len(year_vals)
    if len(year_vals) >= 2:
        out["S_raw"] = -1.0 * statistics.pstdev(year_vals)
    elif len(year_vals) == 1:
        out["S_raw"] = 0.0  # zero variance with a single point

    # horizon_consistency (reported only): fraction of 15d lookbacks whose
    # total_utility_wr has same sign as / within 25% of the 1825d value.
    out["horizon_consistency"] = compute_horizon_consistency(version)

    return out


def compute_horizon_consistency(version):
    """Recency-generalization proxy: fraction of period==15d lookbacks whose
    PER-SIGNAL quality (utility_wr / N, volume-neutral) is within 25% of the
    5y (1825d) value. Uses utility_wr/N rather than raw utility_wr because the
    latter scales with N (1y has ~1/5 the signals of 5y), which would make every
    version look inconsistent. Higher = the per-trade edge holds across eras.
    """
    d = load_json(pack_dir(version) / "utility_by_horizon.json")
    rows = None
    if isinstance(d, list):
        rows = d
    elif isinstance(d, dict):
        rows = d.get("rows")
    if not isinstance(rows, list):
        return None

    vals = {}  # lookback -> utility per signal (volume-neutral quality index)
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("period") != "15d":
            continue
        lb = safe_num(r.get("lookback_days"))
        uw = safe_num(r.get("total_utility_wr"))
        n = safe_num(r.get("total_n"))
        if lb is None or uw is None or n is None or n <= 0:
            continue
        vals[int(lb)] = uw / n

    if 1825 not in vals:
        return None
    base = vals[1825]
    recent = vals.get(365, vals.get(730))
    if not base or recent is None:
        return None
    # ratio ~1.0 = recent per-trade quality matches 5y (edge holds); <1 = recent
    # decay; >1 = improving. Per-trade quality is era-stable here, so spreads are
    # small but real (this is a generalization read, not the true holdout test).
    return recent / base


# ---------------------------------------------------------------------------
# z-scoring
# ---------------------------------------------------------------------------

def zscores(value_by_key):
    """value_by_key: {key: raw or None}. Returns {key: z or None}.
    z over the non-None subset; popstdev==0 -> 0 for all present keys.
    """
    present = {k: v for k, v in value_by_key.items() if v is not None}
    out = {k: None for k in value_by_key}
    if not present:
        return out
    vals = list(present.values())
    mean = statistics.fmean(vals)
    sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    for k, v in present.items():
        out[k] = 0.0 if sd == 0 else (v - mean) / sd
    return out


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt(x, nd=3):
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def fmt_int(x):
    if x is None:
        return "n/a"
    try:
        return f"{int(round(float(x)))}"
    except (TypeError, ValueError):
        return "n/a"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1) Q block (profile-independent)
    q_by_ver = {v: extract_Q(v) for v in VERSIONS}
    q_complete_versions = [v for v in VERSIONS if q_by_ver[v]["complete"]]

    # zQ over ALL versions with a Q_raw (Q always included per spec)
    zQ_map = zscores({v: q_by_ver[v]["Q_raw"] for v in VERSIONS})

    # Tier-2: recency-generalization proxy (version-level) + optional signal-supply
    # burstiness (written by signal_supply.py). Both are MC-independent.
    horizon_by_ver = {v: compute_horizon_consistency(v) for v in VERSIONS}
    supply_by_ver = {}
    supply_path = OUT_DIR / "supply_burstiness.json"
    if supply_path.exists():
        try:
            supply_by_ver = (json.loads(supply_path.read_text(encoding="utf-8")) or {}).get("versions", {})
        except Exception:
            supply_by_ver = {}

    # 2) Per-profile raw metrics
    # windows_map cache
    prof_metrics = {}      # prof -> {ver -> metrics dict}
    prof_usable = {}       # prof -> [versions usable]
    for prof in PROFILES:
        prof_metrics[prof] = {}
        usable = []
        for v in VERSIONS:
            wm = load_profile_windows(v, prof)
            mm = extract_profile_metrics(v, prof, wm)
            prof_metrics[prof][v] = mm
            if mm["usable"]:
                usable.append(v)
        prof_usable[prof] = usable

    # 3) z-score G,R,H,S within each profile over usable versions
    prof_z = {}  # prof -> {metric -> {ver -> z}}
    for prof in PROFILES:
        usable = prof_usable[prof]
        mm = prof_metrics[prof]
        prof_z[prof] = {}
        for metric, key in (("G", "G_raw"), ("R", "R_raw"), ("H", "H_raw"), ("S", "S_raw")):
            prof_z[prof][metric] = zscores({v: mm[v][key] for v in usable})

    # 4) Composite + RGE per (profile, version)
    composites = {}  # prof -> {ver -> {...}}
    for prof in PROFILES:
        w = PROFILE_WEIGHTS[prof]
        theta = w["theta"]
        usable = prof_usable[prof]
        mm = prof_metrics[prof]
        composites[prof] = {}
        for v in usable:
            zQ = zQ_map.get(v)
            zH = prof_z[prof]["H"].get(v)
            zG = prof_z[prof]["G"].get(v)
            zR = prof_z[prof]["R"].get(v)
            zS = prof_z[prof]["S"].get(v)
            # Composite (treat any missing z as 0 contribution, but all usable
            # cells have full data here).
            comp = (
                w["wQ"] * (zQ if zQ is not None else 0.0)
                + w["wH"] * (zH if zH is not None else 0.0)
                + w["wG"] * (zG if zG is not None else 0.0)
                + w["wR"] * (zR if zR is not None else 0.0)
                + w["wS"] * (zS if zS is not None else 0.0)
            )
            # single-pillar dependency: largest weighted |z| term as a share of
            # total |weighted z|. A high share means the rank hangs on one axis.
            terms = {
                "Q": w["wQ"] * abs(zQ) if zQ is not None else 0.0,
                "H": w["wH"] * abs(zH) if zH is not None else 0.0,
                "G": w["wG"] * abs(zG) if zG is not None else 0.0,
                "R": w["wR"] * abs(zR) if zR is not None else 0.0,
                "S": w["wS"] * abs(zS) if zS is not None else 0.0,
            }
            tot_abs = sum(terms.values())
            dom_pillar = max(terms, key=terms.get) if tot_abs > 0 else None
            dom_frac = (terms[dom_pillar] / tot_abs) if (tot_abs > 0 and dom_pillar) else None
            single_pillar = bool(dom_frac is not None and dom_frac >= SINGLE_PILLAR_FRAC)

            # RGE (display-only diagnostic, NOT a composite input): SAME-window
            # growth/DD so we never divide one window's return by another's DD.
            g = mm[v]["log10x_2020_now"]
            dd_same = mm[v]["dd_2020_now_pct"]
            worst_dd = mm[v]["worst_dd_pct"]
            rge = None
            if g is not None and dd_same is not None:
                dd = dd_same / 100.0
                if dd > 0:
                    rge = g / (dd ** theta)
            composites[prof][v] = {
                "composite": comp,
                "zQ": zQ, "zH": zH, "zG": zG, "zR": zR, "zS": zS,
                "RGE": rge,
                "single_pillar_dep": single_pillar,
                "dominant_pillar": dom_pillar,
                "dominant_pillar_frac": dom_frac,
                "log10x_2020_now": g,
                "worst_dd_pct": worst_dd,
                "days_to_1m": mm[v]["days_to_1m"],
                "coverage_ratio": mm[v]["H_raw"],
            }

    # 5) Per-profile rankings (sorted by composite desc)
    prof_rank = {}  # prof -> {ver -> rank(1-based)}
    prof_order = {}  # prof -> [ver sorted]
    for prof in PROFILES:
        order = sorted(composites[prof].keys(),
                       key=lambda v: composites[prof][v]["composite"], reverse=True)
        prof_order[prof] = order
        prof_rank[prof] = {v: i + 1 for i, v in enumerate(order)}

    # 5b) Tie bands: consecutive versions whose composite gap < TIE_EPS share a band
    # (their relative order is below the resolution of 11 deterministic data points).
    prof_tie_band = {}  # prof -> {ver -> band_id}
    for prof in PROFILES:
        order = prof_order[prof]
        bands = {}
        band_id = 0
        for i, v in enumerate(order):
            if i > 0:
                gap = composites[prof][order[i - 1]]["composite"] - composites[prof][v]["composite"]
                if gap >= TIE_EPS:
                    band_id += 1
            bands[v] = band_id
        prof_tie_band[prof] = bands

    # 6) Champion matrix: mean rank over profiles where version is ranked
    champ_rows = []
    for v in VERSIONS:
        ranks = {}
        for prof in PROFILES:
            if v in prof_rank[prof]:
                ranks[prof] = prof_rank[prof][v]
        if not ranks:
            continue
        rank_vals = list(ranks.values())
        mean_rank = sum(rank_vals) / len(rank_vals)
        spread = (max(rank_vals) - min(rank_vals)) if len(rank_vals) > 1 else 0
        champ_rows.append({
            "version": v,
            "sentinel_rank": ranks.get("sentinel"),
            "core_rank": ranks.get("core"),
            "apex_rank": ranks.get("apex"),
            "mean_rank": mean_rank,
            "rank_spread": spread,
            "specialist": spread >= SPECIALIST_SPREAD,
        })
    champ_rows.sort(key=lambda r: r["mean_rank"])

    # 7) Completeness matrix
    completeness = []
    for v in VERSIONS:
        completeness.append({
            "version": v,
            "utility_complete": q_by_ver[v]["complete"],
            "sentinel": prof_metrics["sentinel"][v]["usable"],
            "core": prof_metrics["core"][v]["usable"],
            "apex": prof_metrics["apex"][v]["usable"],
        })

    incomplete_versions = sorted({
        c["version"] for c in completeness
        if not (c["utility_complete"] and c["sentinel"] and c["core"] and c["apex"])
    })

    # Native pillar spans (raw min..max) so a z-unit can be read against the real
    # spread it normalizes -- e.g. G in sentinel/core is rounding-level noise.
    pillar_spans = {}
    for prof in PROFILES:
        usable = prof_usable[prof]
        mm = prof_metrics[prof]
        spans = {}
        for metric, key in (("G", "G_raw"), ("R", "R_raw"), ("H", "H_raw"), ("S", "S_raw")):
            vals = [mm[v][key] for v in usable if mm[v][key] is not None]
            spans[metric] = ({"min": min(vals), "max": max(vals), "span": max(vals) - min(vals)}
                             if vals else None)
        pillar_spans[prof] = spans
    q_vals_all = [q_by_ver[v]["Q_raw"] for v in VERSIONS if q_by_ver[v]["Q_raw"] is not None]
    q_span = ({"min": min(q_vals_all), "max": max(q_vals_all), "span": max(q_vals_all) - min(q_vals_all)}
              if q_vals_all else None)

    # ----- Build markdown tables -----
    md = []

    # 1. Signal-Quality leaderboard
    md.append("## 1. Signal-Quality Leaderboard (profile-independent)")
    md.append("")
    md.append("Sorted by Q = **per-trade WR15 quality** (N-neutral, shrinkage-adjusted) descending. "
              "Profile-independent. `utility_wr` (old volume metric) and `useful_N` shown for contrast — "
              "Q no longer rewards raw signal count, so a high-N / low-WR version no longer wins here.")
    md.append("")
    md.append("`recency` = recent(1y) / 5y per-trade quality ratio (~1.0 = edge holds across eras; "
              "<1 = recent decay). Era-stable here, so spreads are small but real; true overfit test is the ~Nov-2026 holdout.")
    md.append("")
    md.append("| Rank | Version | Q (per-trade WR15 %) | cum_WR15_75+ % | cum_WR15_<25 % | useful_N | utility_wr (vol) | recency | gradient_ok |")
    md.append("|---:|---|---:|---:|---:|---:|---:|---:|:---:|")
    q_order = sorted(VERSIONS, key=lambda v: (q_by_ver[v]["Q_raw"] is not None, q_by_ver[v]["Q_raw"] or 0.0), reverse=True)
    for i, v in enumerate(q_order):
        q = q_by_ver[v]
        rec = horizon_by_ver.get(v)
        md.append("| {r} | {v} | {Q} | {c75} | {c25} | {N} | {U} | {rec} | {g} |".format(
            r=i + 1, v=v,
            Q=fmt(100.0 * q["Q_raw"], 2) if q["Q_raw"] is not None else "n/a",
            c75=fmt(q["cum_wr15_75plus"], 2),
            c25=fmt(q["cum_wr15_25minus"], 2),
            N=fmt_int(q["useful_N"]),
            U=fmt(q["utility_wr"], 0),
            rec=fmt(rec, 3) if rec is not None else "n/a",
            g="yes" if q["gradient_ok"] else "no",
        ))
    md.append("")

    # 2. Per-profile ranking tables
    prof_label = {"sentinel": "Sentinel (DD-safe)", "core": "Core (balanced)", "apex": "Apex (explosive)"}
    prof_letter = {"sentinel": "a", "core": "b", "apex": "c"}
    for prof in PROFILES:
        w = PROFILE_WEIGHTS[prof]
        md.append(f"## 2{prof_letter[prof]}. Per-Profile Ranking — {prof_label[prof]}")
        md.append("")
        md.append("Weights: Q={wQ} H={wH} G={wG} R={wR} S={wS} | theta(RGE)={th}. Sorted by Composite desc. "
                  "`tie` = statistical band (adjacent gap < {eps}); `dep` flags a single-pillar-dependent rank "
                  "(one axis >= {frac:.0%} of the score).".format(
                      wQ=w["wQ"], wH=w["wH"], wG=w["wG"], wR=w["wR"], wS=w["wS"], th=w["theta"],
                      eps=TIE_EPS, frac=SINGLE_PILLAR_FRAC))
        md.append("")
        md.append("| Rank | tie | Version | Composite | dep | zQ | zH | zG | zR | zS | RGE | 2020_now_log10x | worst_dd% | days_to_1m | coverage |")
        md.append("|---:|:---:|---|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for i, v in enumerate(prof_order[prof]):
            c = composites[prof][v]
            dep = c.get("dominant_pillar") if c.get("single_pillar_dep") else ""
            md.append("| {r} | {tb} | {v} | {comp} | {dep} | {zQ} | {zH} | {zG} | {zR} | {zS} | {rge} | {g} | {dd} | {d1m} | {cov} |".format(
                r=i + 1, tb=prof_tie_band[prof][v], v=v,
                comp=fmt(c["composite"], 3),
                dep=(dep or ""),
                zQ=fmt(c["zQ"], 2), zH=fmt(c["zH"], 2), zG=fmt(c["zG"], 2),
                zR=fmt(c["zR"], 2), zS=fmt(c["zS"], 2),
                rge=fmt(c["RGE"], 3),
                g=fmt(c["log10x_2020_now"], 3),
                dd=fmt(c["worst_dd_pct"], 2),
                d1m=fmt_int(c["days_to_1m"]),
                cov=fmt(c["coverage_ratio"], 3),
            ))
        md.append("")

    # 2d. Native pillar spans
    md.append("## 2d. Native pillar spans (raw min..max across versions)")
    md.append("")
    md.append("A z-unit is only meaningful if the underlying spread is. Where a span is tiny "
              "(e.g. G in sentinel/core), a full z-SD is rounding-level noise inflated to look decisive.")
    md.append("")
    md.append("| Profile | G span (log10x) | R span (dd pp) | H span (cov) | S span |")
    md.append("|---|---:|---:|---:|---:|")
    for prof in PROFILES:
        sp = pillar_spans[prof]

        def _spn(m):
            return fmt(sp[m]["span"], 3) if sp.get(m) else "n/a"

        md.append(f"| {prof} | {_spn('G')} | {_spn('R')} | {_spn('H')} | {_spn('S')} |")
    md.append("")
    md.append("Q (per-trade WR15) span across versions: {} pp.".format(
        fmt(100.0 * q_span["span"], 2) if q_span else "n/a"))
    md.append("")

    # 2e. Hydration depth (signal-supply burstiness) — rendered only if computed
    if supply_by_ver:
        md.append("## 2e. Hydration depth — signal-supply burstiness (version-only)")
        md.append("")
        md.append("Distribution shape of daily tradable supply (calls >=75, puts <=25) that the pack "
                  "averages hide. Steady supply keeps the 14-slot book full; bursty supply overflows "
                  "then starves (you cannot bank slots). demand~6.2/day (14 slots / ~2.26-bar hold); "
                  "recycle-cov saturates at demand so over-supply earns no credit.")
        md.append("")
        md.append("| Version | supply/day | CV | Gini | call dry% | put dry% | recycle-cov | steadiness | p20 roll20 |")
        md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for v in VERSIONS:
            s = supply_by_ver.get(v) or {}
            if not s.get("complete"):
                continue
            tot = s.get("total") or {}
            call = s.get("call") or {}
            put = s.get("put") or {}
            md.append("| {v} | {spd} | {cv} | {gini} | {cdry} | {pdry} | {rec} | {steady} | {p20} |".format(
                v=v,
                spd=fmt(tot.get("mean_per_day"), 1),
                cv=fmt(tot.get("cv"), 2),
                gini=fmt(tot.get("gini"), 3),
                cdry=(fmt((call.get("dry_day_rate") or 0) * 100, 0) + "%") if call.get("dry_day_rate") is not None else "n/a",
                pdry=(fmt((put.get("dry_day_rate") or 0) * 100, 0) + "%") if put.get("dry_day_rate") is not None else "n/a",
                rec=fmt(s.get("recycle_coverage"), 3),
                steady=fmt(s.get("steadiness"), 3),
                p20=fmt(tot.get("p20_rolling20"), 1),
            ))
        md.append("")

    # 3. Champion matrix
    md.append("## 3. Champion Matrix")
    md.append("")
    md.append("Sorted by mean_rank ascending (robust champions on top). * marks profile-specialists (rank spread >= {s}).".format(s=SPECIALIST_SPREAD))
    md.append("")
    md.append("| Version | sentinel_rank | core_rank | apex_rank | mean_rank | spread | specialist |")
    md.append("|---|---:|---:|---:|---:|---:|:---:|")
    for r in champ_rows:
        md.append("| {v} | {s} | {c} | {a} | {m} | {sp} | {spec} |".format(
            v=r["version"],
            s=fmt_int(r["sentinel_rank"]),
            c=fmt_int(r["core_rank"]),
            a=fmt_int(r["apex_rank"]),
            m=fmt(r["mean_rank"], 2),
            sp=fmt_int(r["rank_spread"]),
            spec="*" if r["specialist"] else "",
        ))
    md.append("")

    # 4. Completeness matrix
    md.append("## 4. Completeness Matrix")
    md.append("")
    md.append("| Version | utility | sentinel | core | apex |")
    md.append("|---|:---:|:---:|:---:|:---:|")
    for c in completeness:
        md.append("| {v} | {u} | {s} | {co} | {a} |".format(
            v=c["version"],
            u="Y" if c["utility_complete"] else "N",
            s="Y" if c["sentinel"] else "N",
            co="Y" if c["core"] else "N",
            a="Y" if c["apex"] else "N",
        ))
    md.append("")

    tables_markdown = "\n".join(md)

    # ----- Assemble scorecard.json -----
    scorecard = {
        "schema": "version_scorecard.v1",
        "pack_root": str(PACK_ROOT),
        "versions": VERSIONS,
        "profiles": PROFILES,
        "config": {
            "required_windows": REQUIRED_WINDOWS,
            "G_windows": G_WINDOWS,
            "R_windows": R_WINDOWS,
            "S_year_windows": S_YEAR_WINDOWS,
            "profile_weights": PROFILE_WEIGHTS,
            "gradient_tolerance": GRADIENT_TOL,
            "specialist_spread": SPECIALIST_SPREAD,
        },
        "signal_quality": {
            v: {
                "Q_raw": q_by_ver[v]["Q_raw"],
                "zQ": zQ_map.get(v),
                "horizon_consistency": horizon_by_ver.get(v),
                "supply_burstiness": supply_by_ver.get(v),
                "quality_call": q_by_ver[v]["quality_call"],
                "quality_put": q_by_ver[v]["quality_put"],
                "utility_wr": q_by_ver[v]["utility_wr"],
                "cum_wr15_75plus": q_by_ver[v]["cum_wr15_75plus"],
                "cum_wr15_25minus": q_by_ver[v]["cum_wr15_25minus"],
                "useful_N": q_by_ver[v]["useful_N"],
                "gradient_ok": q_by_ver[v]["gradient_ok"],
                "complete": q_by_ver[v]["complete"],
                "git_commit": q_by_ver[v]["git_commit"],
                "git_message": q_by_ver[v]["git_message"],
            }
            for v in VERSIONS
        },
        "profiles_detail": {
            prof: {
                v: {
                    "usable": prof_metrics[prof][v]["usable"],
                    # raw
                    "G_raw": prof_metrics[prof][v]["G_raw"],
                    "R_raw": prof_metrics[prof][v]["R_raw"],
                    "H_raw": prof_metrics[prof][v]["H_raw"],
                    "S_raw": prof_metrics[prof][v]["S_raw"],
                    # z
                    "zG": prof_z[prof]["G"].get(v),
                    "zR": prof_z[prof]["R"].get(v),
                    "zH": prof_z[prof]["H"].get(v),
                    "zS": prof_z[prof]["S"].get(v),
                    "zQ": zQ_map.get(v),
                    # composite + rge
                    "composite": composites[prof].get(v, {}).get("composite"),
                    "RGE": composites[prof].get(v, {}).get("RGE"),
                    "rank": prof_rank[prof].get(v),
                    "tie_band": prof_tie_band[prof].get(v),
                    "single_pillar_dep": composites[prof].get(v, {}).get("single_pillar_dep"),
                    "dominant_pillar": composites[prof].get(v, {}).get("dominant_pillar"),
                    "dominant_pillar_frac": composites[prof].get(v, {}).get("dominant_pillar_frac"),
                    # reported diagnostics
                    "log10x_2020_now": prof_metrics[prof][v]["log10x_2020_now"],
                    "log10x_22_now": prof_metrics[prof][v]["log10x_22_now"],
                    "days_to_1m": prof_metrics[prof][v]["days_to_1m"],
                    "days_to_1m_source": prof_metrics[prof][v]["days_to_1m_source"],
                    "worst_dd_pct": prof_metrics[prof][v]["worst_dd_pct"],
                    "covid_crash_dd_pct": prof_metrics[prof][v]["covid_crash_dd_pct"],
                    "dd_2020_now_pct": prof_metrics[prof][v]["dd_2020_now_pct"],
                    "underwater_days": prof_metrics[prof][v]["underwater_days"],
                    "coverage_ratio": prof_metrics[prof][v]["H_raw"],
                    "hydration_gap_per_day": prof_metrics[prof][v]["hydration_gap_per_day"],
                    "slot_util_pct": prof_metrics[prof][v]["slot_util_pct"],
                    "pool_full_rate": prof_metrics[prof][v]["pool_full_rate"],
                    "raw_signals_per_day": prof_metrics[prof][v]["raw_signals_per_day"],
                    "supply_ratio": prof_metrics[prof][v]["supply_ratio"],
                    "avg_hold_bars": prof_metrics[prof][v]["avg_hold_bars"],
                    "accepted_entries_per_day": prof_metrics[prof][v]["accepted_entries_per_day"],
                    "target_entries_per_day": prof_metrics[prof][v]["target_entries_per_day"],
                    "year_log10x_complete_n": prof_metrics[prof][v]["year_log10x_complete_n"],
                    "horizon_consistency": prof_metrics[prof][v]["horizon_consistency"],
                }
                for v in VERSIONS
            }
            for prof in PROFILES
        },
        "champion_matrix": champ_rows,
        "completeness": completeness,
        "incomplete_versions": incomplete_versions,
        "per_profile_order": prof_order,
        "signal_quality_order": q_order,
        "tie_bands": prof_tie_band,
        "pillar_spans": pillar_spans,
        "q_span": q_span,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(scorecard, fh, indent=2, sort_keys=True)

    # Stamp the markdown path + per-profile top3 + sq top3 in stdout for the runner.
    print(tables_markdown)
    print("")
    print("## Quick Reads")
    print("")
    print("scorecard_json_path: " + str(OUT_JSON))
    print("script_path: " + str(Path(__file__).resolve()))
    for prof in PROFILES:
        top3 = prof_order[prof][:3]
        print(f"per_profile_top3[{prof}]: " + ", ".join(top3))
    print("signal_quality_top3: " + ", ".join(q_order[:3]))
    print("incomplete_versions: " + (", ".join(incomplete_versions) if incomplete_versions else "(none)"))
    print("")
    print("CHAMPION_MATRIX_JSON: " + json.dumps(champ_rows))
    print("COMPLETENESS_JSON: " + json.dumps(completeness))
    print("SIGNAL_QUALITY_JSON: " + json.dumps(scorecard["signal_quality"], sort_keys=True))
    print("PROFILES_DETAIL_JSON: " + json.dumps(scorecard["profiles_detail"], sort_keys=True))


if __name__ == "__main__":
    main()
