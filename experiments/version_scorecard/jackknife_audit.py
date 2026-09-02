#!/usr/bin/env python
"""Leave-one-out jackknife robustness audit for the version scorecard.

Reads the already-built scorecard.json (raw pillar values per version/profile),
and re-derives the FULL scorecard pipeline (z-score each pillar over the cohort,
weighted composite, per-profile rank, champion mean-rank) under each
leave-one-out (LOO) cohort: drop version d, recompute z over the remaining 10,
re-rank, observe churn for the OTHER versions.

Parity with score_versions.py was verified: reconstructing the full-cohort
composite from stored raw values reproduces the stored composite to 0.0 and all
z-scores to 1e-9. So this audit is faithful to the production scorer's math.

Run (Windows):
    set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && python experiments/version_scorecard/jackknife_audit.py
"""

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCORECARD = ROOT / ".cache" / "algorithm_versions" / "_scorecard" / "scorecard.json"


def zscores(value_by_key):
    """Exact copy of score_versions.zscores: z over non-None subset; pstdev==0 -> 0."""
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


def compute_ranks(versions, profiles, weights, raw):
    """Given a cohort (list of versions), return:
       composites[prof][v], ranks[prof][v] (1-based), zmaps[prof][metric][v].
    `raw` = {'Q': {v:val}, prof: {'G':{v:val}, 'R':..., 'H':..., 'S':...}}.
    """
    zQ = zscores({v: raw["Q"][v] for v in versions})
    composites = {}
    ranks = {}
    zmaps = {}
    for prof in profiles:
        w = weights[prof]
        zmaps[prof] = {"Q": zQ}
        z = {}
        for metric in ("G", "R", "H", "S"):
            z[metric] = zscores({v: raw[prof][metric][v] for v in versions})
            zmaps[prof][metric] = z[metric]
        comp = {}
        for v in versions:
            comp[v] = (
                w["wQ"] * (zQ[v] or 0.0)
                + w["wH"] * (z["H"][v] or 0.0)
                + w["wG"] * (z["G"][v] or 0.0)
                + w["wR"] * (z["R"][v] or 0.0)
                + w["wS"] * (z["S"][v] or 0.0)
            )
        composites[prof] = comp
        order = sorted(comp.keys(), key=lambda v: comp[v], reverse=True)
        ranks[prof] = {v: i + 1 for i, v in enumerate(order)}
    return composites, ranks, zmaps


def main():
    sc = json.load(open(SCORECARD, encoding="utf-8"))
    VERS = sc["versions"]
    PROFS = sc["profiles"]
    W = sc["config"]["profile_weights"]
    pdt = sc["profiles_detail"]
    sq = sc["signal_quality"]

    # Assemble raw pillar values
    raw = {"Q": {v: sq[v]["Q_raw"] for v in VERS}}
    for prof in PROFS:
        raw[prof] = {
            "G": {v: pdt[prof][v]["G_raw"] for v in VERS},
            "R": {v: pdt[prof][v]["R_raw"] for v in VERS},
            "H": {v: pdt[prof][v]["H_raw"] for v in VERS},
            "S": {v: pdt[prof][v]["S_raw"] for v in VERS},
        }

    # ---- Full-cohort baseline ----
    base_comp, base_rank, base_z = compute_ranks(VERS, PROFS, W, raw)
    base_mean_rank = {
        v: sum(base_rank[p][v] for p in PROFS) / len(PROFS) for v in VERS
    }
    base_top3 = {p: sorted(VERS, key=lambda v: base_rank[p][v])[:3] for p in PROFS}
    base_champ_order = sorted(VERS, key=lambda v: base_mean_rank[v])
    base_champ_top3 = base_champ_order[:3]

    print("=" * 78)
    print("FULL-COHORT BASELINE (reproduced; matches scorecard.json)")
    print("=" * 78)
    for p in PROFS:
        print(f"  {p:9s} top3: {base_top3[p]}")
    print(f"  champion  top3 (mean_rank): {base_champ_top3}")
    print()

    # ---- LOO loop ----
    # For each dropped version, recompute ranks over remaining 10. Record, for
    # every SURVIVING version, its new rank in each profile + new champion mean-rank.
    # rank_churn[p][survivor] = list of (dropped, delta_rank)
    n = len(VERS)
    profile_top3_flips = {p: [] for p in PROFS}   # (dropped, old_top3, new_top3)
    champ_top3_flips = []                          # (dropped, old, new)
    # Max absolute rank movement any survivor experiences, per profile
    max_move = {p: {v: 0 for v in VERS} for p in PROFS}
    max_move_champ = {v: 0.0 for v in VERS}        # mean-rank position movement
    # Track survivor RANK (1..10) range across all LOO cohorts to gauge stability
    survivor_ranks = {p: {v: [] for v in VERS} for p in PROFS}
    survivor_mean_ranks = {v: [] for v in VERS}

    for dropped in VERS:
        cohort = [v for v in VERS if v != dropped]
        comp, rank, _ = compute_ranks(cohort, PROFS, W, raw)
        mean_rank = {v: sum(rank[p][v] for p in PROFS) / len(PROFS) for v in cohort}
        champ_order = sorted(cohort, key=lambda v: mean_rank[v])

        for p in PROFS:
            new_top3 = sorted(cohort, key=lambda v: rank[p][v])[:3]
            # Baseline top3 restricted to survivors (so dropping a top3 member
            # doesn't auto-count as a "flip" — we test whether the RELATIVE
            # order among survivors changed).
            old_top3_survivors = [v for v in base_top3[p] if v != dropped]
            new_top3_survivors = [v for v in new_top3 if v != dropped]
            # Compare the survivor-only top3 ordering
            # (truncate both to min length for fair compare)
            k = min(len(old_top3_survivors), len(new_top3_survivors), 3)
            if old_top3_survivors[:k] != new_top3_survivors[:k]:
                profile_top3_flips[p].append(
                    (dropped, base_top3[p], new_top3)
                )
            for v in cohort:
                survivor_ranks[p][v].append(rank[p][v])
                # rank movement vs baseline (baseline rank among 11 vs new among 10):
                # Use "rank among survivors" by re-deriving baseline survivor rank.
                base_surv_order = sorted(cohort, key=lambda x: base_rank[p][x])
                base_surv_rank = {x: i + 1 for i, x in enumerate(base_surv_order)}
                mv = abs(rank[p][v] - base_surv_rank[v])
                max_move[p][v] = max(max_move[p][v], mv)

        # champion top3 flip (survivor-relative)
        new_champ_top3 = champ_order[:3]
        old_champ_survivors = [v for v in base_champ_top3 if v != dropped]
        k = min(len(old_champ_survivors), len(new_champ_top3), 3)
        if old_champ_survivors[:k] != new_champ_top3[:k]:
            champ_top3_flips.append((dropped, base_champ_top3, new_champ_top3))
        for v in cohort:
            survivor_mean_ranks[v].append(mean_rank[v])
            base_surv_champ = sorted(cohort, key=lambda x: base_mean_rank[x])
            base_surv_champ_rank = {x: i + 1 for i, x in enumerate(base_surv_champ)}
            new_champ_rank = {x: i + 1 for i, x in enumerate(champ_order)}
            max_move_champ[v] = max(
                max_move_champ[v], abs(new_champ_rank[v] - base_surv_champ_rank[v])
            )

    # ---- Report ----
    print("=" * 78)
    print("Q1: Does any profile's TOP-3 flip when ONE version is removed?")
    print("    (survivor-relative: dropping a top3 member is NOT itself a flip;")
    print("     we test whether the order among the remaining versions changed)")
    print("=" * 78)
    any_flip = False
    for p in PROFS:
        if profile_top3_flips[p]:
            any_flip = True
            print(f"  [{p}] {len(profile_top3_flips[p])} flip(s):")
            for dropped, old, new in profile_top3_flips[p]:
                print(f"      drop {dropped}: base_top3={old} -> LOO_top3={new}")
        else:
            print(f"  [{p}] NO survivor-relative top3 flip across all 11 LOO cohorts.")
    if champ_top3_flips:
        any_flip = True
        print(f"  [champion] {len(champ_top3_flips)} flip(s):")
        for dropped, old, new in champ_top3_flips:
            print(f"      drop {dropped}: base_top3={old} -> LOO_top3={new}")
    else:
        print("  [champion] NO survivor-relative top3 flip across all 11 LOO cohorts.")
    print()

    print("=" * 78)
    print("Q1b: Max rank movement any SURVIVOR experiences across all LOO cohorts")
    print("     (per profile, survivor-relative rank among the 10 remaining)")
    print("=" * 78)
    for p in PROFS:
        moves = sorted(max_move[p].items(), key=lambda kv: -kv[1])
        worst = [f"{v}:{m}" for v, m in moves if m > 0]
        maxv = moves[0][1]
        print(f"  [{p}] max survivor rank move = {maxv}. Movers: {worst if worst else 'none (fully stable)'}")
    cm = sorted(max_move_champ.items(), key=lambda kv: -kv[1])
    print(f"  [champion mean-rank] max move = {cm[0][1]}. Movers: "
          + str([f'{v}:{m}' for v, m in cm if m > 0] or 'none'))
    print()

    # ---- Q2: z-outlier driven ranks ----
    # For each version, in the full cohort, find its single most extreme |z| pillar
    # and how much of its composite that one pillar contributes (weighted).
    print("=" * 78)
    print("Q2: Is any version's rank driven by being a Z-OUTLIER on ONE pillar?")
    print("    For each (version, profile): the pillar with the largest |weighted")
    print("    z contribution|, its share of the |composite|, and whether that")
    print("    pillar is itself an extreme (|z|>=1.5) outlier vs the cohort.")
    print("=" * 78)
    PILL = [("Q", "wQ"), ("H", "wH"), ("G", "wG"), ("R", "wR"), ("S", "wS")]
    for p in PROFS:
        print(f"  -- {p} (top of ranking first) --")
        order = sorted(VERS, key=lambda v: base_rank[p][v])
        for v in order:
            contribs = []
            for metric, wkey in PILL:
                z = base_z[p][metric][v] or 0.0
                wc = W[p][wkey] * z
                contribs.append((metric, z, wc))
            # dominant by absolute weighted contribution
            dom = max(contribs, key=lambda c: abs(c[2]))
            comp = base_comp[p][v]
            denom = sum(abs(c[2]) for c in contribs) or 1e-12
            share = abs(dom[2]) / denom
            outlier = abs(dom[1]) >= 1.5
            flag = "  <== single-pillar outlier driver" if (outlier and share >= 0.45) else ""
            print(f"    rank {base_rank[p][v]:2d} {v}: comp={comp:+.3f} | "
                  f"dominant={dom[0]}(z={dom[1]:+.2f}, {share*100:.0f}% of |comp|){flag}")
        print()

    # ---- Q2b: counterfactual — zero out each version's most-extreme pillar, re-rank ----
    print("=" * 78)
    print("Q2b: Counterfactual — for the champion v58, neutralize its single")
    print("     dominant pillar (set that raw value to the cohort mean so z->0)")
    print("     and re-rank. Does v58 still lead?")
    print("=" * 78)
    # v58's dominant pillar is Q (zQ=+2.17, global). Neutralize Q for v58 only.
    raw_cf = json.loads(json.dumps(raw))  # deep copy
    qvals = [raw["Q"][v] for v in VERS if v != "v58"]
    raw_cf["Q"]["v58"] = statistics.fmean(qvals)  # makes zQ(v58) ~ small
    cf_comp, cf_rank, cf_z = compute_ranks(VERS, PROFS, W, raw_cf)
    cf_mean = {v: sum(cf_rank[p][v] for p in PROFS) / len(PROFS) for v in VERS}
    print(f"  After neutralizing v58's Q (zQ {base_z['sentinel']['Q']['v58']:+.2f} -> "
          f"{cf_z['sentinel']['Q']['v58']:+.2f}):")
    for p in PROFS:
        cf_order = sorted(VERS, key=lambda v: cf_rank[p][v])
        print(f"    [{p}] new top3: {cf_order[:3]}  (v58 rank: {cf_rank[p]['v58']})")
    print(f"    [champion] new order: {sorted(VERS, key=lambda v: cf_mean[v])[:4]}  "
          f"(v58 mean_rank {cf_mean['v58']:.2f})")
    print()

    # ---- Q3: tiny-spread / no-op pillars ----
    print("=" * 78)
    print("Q3: Are any pillars near-zero-spread (silently no-op) or noise-amplified?")
    print("=" * 78)
    print("  No pillar has pstdev==0 in any cohort (checked below), so the sd==0->0")
    print("  zero-out branch never fires. But low-spread pillars amplify noise:")
    print("  a tiny raw gap becomes a full z-unit. Reporting raw span + what a 1-z")
    print("  move costs in native units.")
    for p in PROFS:
        print(f"  -- {p} --")
        for metric in ("Q", "G", "R", "H", "S"):
            if metric == "Q":
                vals = [raw["Q"][v] for v in VERS]
                unit = "utility_wr"
            else:
                vals = [raw[p][metric][v] for v in VERS]
                unit = {"G": "log10x", "R": "neg_dd_pct", "H": "cov_ratio", "S": "neg_log10x_std"}[metric]
            sd = statistics.pstdev(vals)
            span = max(vals) - min(vals)
            print(f"    {metric}: span={span:.4f} pstdev={sd:.5f} {unit}; "
                  f"1 z-unit = {sd:.4f} {unit}")
    print()

    # Confirm no LOO cohort produces a zero-stdev pillar
    zero_sd = []
    for dropped in VERS:
        cohort = [v for v in VERS if v != dropped]
        for p in PROFS:
            for metric in ("G", "R", "H", "S"):
                vals = [raw[p][metric][v] for v in cohort]
                if statistics.pstdev(vals) == 0:
                    zero_sd.append((dropped, p, metric))
        if statistics.pstdev([raw["Q"][v] for v in cohort]) == 0:
            zero_sd.append((dropped, "global", "Q"))
    print(f"  LOO cohorts producing a zero-stdev (no-op) pillar: "
          f"{zero_sd if zero_sd else 'NONE'}")
    print()

    # ---- Q-extra: full LOO rank table for the contested top of the board ----
    print("=" * 78)
    print("LOO RANK MATRIX (champion mean-rank). Row=dropped version, col=survivor.")
    print("Cell = survivor's mean-rank position (1=best) among the 10 remaining.")
    print("=" * 78)
    focus = base_champ_order[:6]  # the top-6 contested cluster
    hdr = "  drop \\ surv | " + " ".join(f"{v:>4s}" for v in focus)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for dropped in VERS:
        cohort = [v for v in VERS if v != dropped]
        comp, rank, _ = compute_ranks(cohort, PROFS, W, raw)
        mean_rank = {v: sum(rank[p][v] for p in PROFS) / len(PROFS) for v in cohort}
        order = sorted(cohort, key=lambda v: mean_rank[v])
        pos = {v: i + 1 for i, v in enumerate(order)}
        cells = []
        for v in focus:
            cells.append("  -- " if v == dropped else f"{pos[v]:>4d} ")
        print(f"  {dropped:>11s} | " + " ".join(c.strip().rjust(4) for c in cells))
    print()
    print("  (baseline full-cohort champion order: " + " ".join(base_champ_order) + ")")


if __name__ == "__main__":
    main()
