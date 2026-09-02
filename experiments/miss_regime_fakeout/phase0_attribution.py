"""Phase 0 -- Layer B fakeout attribution tally (AMENDED per A1: holdout-restricted).

Pre-cutoff (date <= CALIBRATION_CUTOFF_DATE) rows ONLY, versions v69+ (post-weekly-blend
era; v60 excluded). Tallies fakeout events (spread >= 15 pts AND |price_move| < 3%,
the intraday_diagnostics definition) by dominant-stage attribution x version.
Retired-dampener-stage attributions are reported SEPARATELY and EXCLUDED from the
live-mechanism ranking (those mechanisms cannot fire on v74).

The post-cutoff tally is FORBIDDEN until the Dec-2026 OOS unlock (DESIGN.md A1).
Phase 0 does NOT gate Phase 1 -- narrative ordering only.

Foreground-safe: one aggregate SQL + ~90 small indexed snapshot loads.
Run: PYTHONUTF8=1 python experiments/miss_regime_fakeout/phase0_attribution.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from experiments._holdout import CUTOFF as HOLDOUT_CUTOFF  # noqa: E402

RESULTS_DIR = os.path.join(_HERE, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)
OUT_TXT = os.path.join(RESULTS_DIR, "phase0_attribution.txt")
OUT_JSON = os.path.join(RESULTS_DIR, "phase0_attribution.json")

# Versions in scope (A1): post-weekly-blend era only, pre-cutoff.
VERSIONS = [69, 70, 71, 72, 73, 74]

# Fakeout definition (intraday_diagnostics.py constants)
FAKEOUT_MIN_SWING = 15
FAKEOUT_MAX_PRICE_MOVE_PCT = 3.0

# weight_info keys whose mechanisms are RETIRED on v74 (cannot fire on the live
# stack): v71 retirements (mis_stress, mcd, sector wave), v73 (wcf/ich/cwcf/cswc/scw),
# v74 lean (cont/wvd/daily-volume-authority/ern).
RETIRED_KEYS = {
    "wcf_lift", "cwcf_dampen", "cswc_dampen", "scw_dampen",
    "ich_call_dampen", "ich_put_lift", "wvd_lift", "wvd_dampen",
    "mcd_dampen", "cont_lift", "ern_boost",
    "daily_volume_authority_wave", "sector_breadth_wave", "mis_stress",
}

REVERSAL_SIGNALS = {"ABSORPTION", "CLIMAX"}


def log(msg):
    print(msg, flush=True)


def main():
    t0 = time.time()
    from database.trader_database import DB
    import intraday_diagnostics as idg
    from database.models.core import AlgorithmVersion

    cutoff_iso = HOLDOUT_CUTOFF.isoformat()
    log("=== Phase 0: fakeout attribution tally (pre-cutoff <= %s, versions v69+) ===" % cutoff_iso)

    # ---- 1. fakeout group list (aggregate SQL, holdout-restricted) ----
    ver_list = ",".join(str(v) for v in VERSIONS)
    cur = DB.execute_sql(
        """
        SELECT symbol, date, version_id, spread, price_move_pct, n_snap FROM (
            SELECT symbol, date, version_id,
                   (MAX(overall) - MIN(overall)) AS spread,
                   (100.0 * (MAX(price) - MIN(price)) / NULLIF(MIN(price),0)) AS price_move_pct,
                   COUNT(*) AS n_snap
            FROM score_intraday_logs
            WHERE overall IS NOT NULL
              AND date <= %s
              AND version_id IN (""" + ver_list + """)
            GROUP BY symbol, date, version_id
            HAVING COUNT(*) >= 2
        ) g
        WHERE g.spread >= %s AND ABS(g.price_move_pct) < %s
        ORDER BY version_id, date, symbol
        """,
        (cutoff_iso, FAKEOUT_MIN_SWING, FAKEOUT_MAX_PRICE_MOVE_PCT),
    )
    groups = [
        {"symbol": r[0], "date": r[1], "version_id": int(r[2]),
         "spread": int(r[3]), "price_move_pct": float(r[4]), "n_snap": int(r[5])}
        for r in cur.fetchall()
    ]
    log("fakeout groups (pre-cutoff, v69+): N=%d" % len(groups))

    # sanity: nothing past the cutoff
    max_d = max((g["date"] for g in groups), default=date.min)
    assert (not groups) or max_d <= HOLDOUT_CUTOFF, "HOLDOUT LEAK in phase 0 group list: %s" % max_d

    version_objs = {v: AlgorithmVersion.get_by_id(v) for v in VERSIONS}

    # ---- 2. per-group attribution via intraday_diagnostics ----
    events = []
    for g in groups:
        snaps = idg.load_snapshots(g["symbol"], g["date"], version_objs[g["version_id"]])
        attr = idg.attribute_swing(snaps) if snaps else None
        if attr is None:
            events.append({**g, "date": g["date"].isoformat(), "dominant": "UNATTRIBUTABLE",
                           "mechanism_class": "unattributable", "detail": ""})
            continue

        dominant = attr["dominant"]
        changed = attr.get("changed_keys") or []
        changed_names = [k for k, _, _ in changed]

        detail = ""
        mech_class = "live"
        if dominant in ("dampener", "boost"):
            stage_keys = [k for k in changed_names]
            retired_hits = [k for k in stage_keys if k in RETIRED_KEYS]
            live_hits = [k for k in stage_keys if k not in RETIRED_KEYS]
            if retired_hits and not live_hits:
                mech_class = "retired"
            elif retired_hits and live_hits:
                mech_class = "mixed"
            detail = "keys=" + ",".join(stage_keys[:6]) if stage_keys else "no-traced-key"
        elif dominant == "volume":
            a, b = attr["earlier"], attr["later"]
            sa, sb = a.get("volume_signal") or "-", b.get("volume_signal") or "-"
            rev = (sa in REVERSAL_SIGNALS) or (sb in REVERSAL_SIGNALS)
            detail = "%s->%s%s" % (sa, sb, " [REVERSAL-CLASS]" if rev else "")
        elif dominant == "weekly":
            detail = "closed #7/#9 class (weekly transition)"

        events.append({
            **g, "date": g["date"].isoformat(),
            "dominant": dominant, "mechanism_class": mech_class, "detail": detail,
            "d_overall": attr["d_overall"],
            "fakeout_family": attr.get("fakeout_family"),
        })

    # ---- 3. tallies ----
    tally_stage_ver = defaultdict(int)      # (dominant, version) -> n
    tally_stage = defaultdict(int)          # dominant -> n (live-mechanism ranking, retired EXCLUDED)
    tally_retired = defaultdict(int)        # dominant -> n (retired-only, separate)
    tally_vol_pairs = defaultdict(int)      # volume transition pair -> n
    for e in events:
        tally_stage_ver[(e["dominant"], e["version_id"])] += 1
        if e["mechanism_class"] == "retired":
            tally_retired[e["dominant"]] += 1
        else:
            tally_stage[e["dominant"]] += 1
        if e["dominant"] == "volume":
            tally_vol_pairs[e["detail"]] += 1

    # ---- 4. report ----
    lines = []
    lines.append("=== Phase 0: fakeout dominant-stage attribution (HOLDOUT-RESTRICTED per A1) ===\n")
    lines.append("window: score_intraday_logs date <= %s, versions %s\n" % (cutoff_iso, VERSIONS))
    lines.append("fakeout def: spread >= %d pts AND |price_move| < %.1f%% (intraday_diagnostics)\n" %
                 (FAKEOUT_MIN_SWING, FAKEOUT_MAX_PRICE_MOVE_PCT))
    lines.append("N fakeout events = %d\n" % len(events))
    lines.append("POST-CUTOFF TALLY: FORBIDDEN until Dec-2026 OOS unlock (A1). Not computed.\n")

    lines.append("\n--- dominant stage x version (all events) ---\n")
    stages = sorted({s for s, _ in tally_stage_ver})
    header = "%-14s" % "stage" + "".join("%8s" % ("v%d" % v) for v in VERSIONS) + "%8s" % "TOTAL"
    lines.append(header + "\n")
    for s in stages:
        row = "%-14s" % s
        tot = 0
        for v in VERSIONS:
            n = tally_stage_ver.get((s, v), 0)
            tot += n
            row += "%8d" % n
        row += "%8d" % tot
        lines.append(row + "\n")

    lines.append("\n--- LIVE-mechanism ranking (retired-dampener attributions EXCLUDED per A1) ---\n")
    for s, n in sorted(tally_stage.items(), key=lambda kv: -kv[1]):
        lines.append("  %-14s %d\n" % (s, n))

    lines.append("\n--- RETIRED-mechanism attributions (separate; cannot fire on v74) ---\n")
    if tally_retired:
        for s, n in sorted(tally_retired.items(), key=lambda kv: -kv[1]):
            lines.append("  %-14s %d\n" % (s, n))
    else:
        lines.append("  (none)\n")

    lines.append("\n--- volume-stage transition pairs ---\n")
    for pair, n in sorted(tally_vol_pairs.items(), key=lambda kv: -kv[1]):
        lines.append("  %-44s %d\n" % (pair, n))

    n_rev = sum(n for p, n in tally_vol_pairs.items() if "REVERSAL-CLASS" in p)
    n_vol = sum(tally_vol_pairs.values())
    lines.append("\nvolume-stage events involving ABSORPTION/CLIMAX: %d / %d\n" % (n_rev, n_vol))

    lines.append("\n--- per-event detail ---\n")
    lines.append("%-8s %-12s %-5s %-6s %-11s %-9s %s\n" %
                 ("SYM", "DATE", "ver", "swing", "dominant", "class", "detail"))
    for e in events:
        lines.append("%-8s %-12s %-5s %-6d %-11s %-9s %s\n" %
                     (e["symbol"], e["date"], "v%d" % e["version_id"], e["spread"],
                      e["dominant"], e["mechanism_class"], e["detail"]))

    elapsed = time.time() - t0
    lines.append("\ndone in %.1fs\n" % elapsed)

    with open(OUT_TXT, "w", encoding="ascii", errors="replace") as f:
        f.writelines(lines)
    with open(OUT_JSON, "w", encoding="ascii") as f:
        json.dump({
            "window_max_date": cutoff_iso, "versions": VERSIONS,
            "fakeout_def": {"min_swing": FAKEOUT_MIN_SWING, "max_price_move_pct": FAKEOUT_MAX_PRICE_MOVE_PCT},
            "n_events": len(events),
            "tally_stage_x_version": {"%s|v%d" % k: v for k, v in tally_stage_ver.items()},
            "live_mechanism_ranking": dict(tally_stage),
            "retired_mechanism_tally": dict(tally_retired),
            "volume_transition_pairs": dict(tally_vol_pairs),
            "events": events,
            "elapsed_s": round(elapsed, 1),
        }, f, indent=2, default=str)

    for ln in lines[:60]:
        sys.stdout.write(ln)
    log("\nwrote %s" % OUT_TXT)
    log("wrote %s" % OUT_JSON)


if __name__ == "__main__":
    main()
