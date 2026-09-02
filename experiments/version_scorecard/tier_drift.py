#!/usr/bin/env python
"""Cross-version tier-drift control chart (the slow-rot detector).

Per-ship gates cannot see degradation distributed across many ships: each step
sits inside its own noise band, but three versions later a tier has quietly
bled 3pp. This tool reads the per-version research packs already on disk and
flags, per (side, band):

  trend : K consecutive version-over-version declines in the shrunk metric
          (default K=3 -- each step may individually be noise; the streak is
          the signal)
  cum   : the cumulative decline from K versions back is statistically real
          (two-proportion z <= -2 on the RAW metric with its N)

This is the per-ship gates' complement, not a replacement: a 'trend' hit on a
thin tier (95-100) is exactly the rot the pooled W6 gradient check cannot see
per-ship. Run it as part of every Stage-1 ship review (seconds, pack-only).

CAVEAT: packs are built at different times, so each version's "5y" window is
date-shifted. A drift hit is a SIGNAL to confirm with a fixed-window assess
(`trader assess --force 5y --version vNN`), not proof by itself. Reverted /
research-only versions in the lineage (e.g. v58) will appear if their packs
exist -- pass an explicit shipped lineage via --versions for a clean chart.

Run (Windows):
  set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && ^
  python experiments/version_scorecard/tier_drift.py
  python experiments/version_scorecard/tier_drift.py --versions v60,v69,v70,v71,v72
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / ".cache" / "algorithm_versions"

BANDS = {
    "call": ["95-100", "90-94", "85-89", "80-84", "75-79"],
    "put": ["0-5", "6-10", "11-15", "16-20", "21-25"],
}


def load_pack(version):
    path = PACK_ROOT / version / "research_pack" / "utility_5y_wr15.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def discover_versions():
    """All vNN dirs with a hydrated utility pack, ascending by numeric id."""
    out = []
    for p in PACK_ROOT.glob("v*"):
        m = re.fullmatch(r"v(\d+)", p.name)
        if m and (p / "research_pack" / "utility_5y_wr15.json").exists():
            out.append((int(m.group(1)), p.name))
    return [name for _, name in sorted(out)]


def band_rows(pack, side, band):
    for b in ((pack.get("sides") or {}).get(side) or {}).get("bands") or []:
        if b.get("band") == band:
            return b
    return None


def two_prop_z(p1, n1, p2, n2):
    """z of (p2 - p1); negative = decline from p1 to p2."""
    se = math.sqrt(max(p1 * (1 - p1), 1e-9) / n1 + max(p2 * (1 - p2), 1e-9) / n2)
    return ((p2 - p1) / se) if se > 0 else 0.0


def run(versions, metric, k):
    raw_key, shrunk_key = metric, f"{metric}_shrunk"
    packs = {}
    for v in versions:
        d = load_pack(v)
        if d:
            packs[v] = d
        else:
            print(f"  (no pack for {v} -- skipped)")
    vs = [v for v in versions if v in packs]
    if len(vs) < k + 1:
        print(f"need at least {k + 1} hydrated versions for K={k}; have {len(vs)}")
        return []

    flags = []
    print("=" * 100)
    print(f"TIER DRIFT CHART  metric={shrunk_key} (display) / {raw_key}+n (z)   "
          f"K={k} consecutive declines = trend flag")
    print(f"versions: {' -> '.join(vs)}")
    print("=" * 100)
    for side in ("call", "put"):
        for band in BANDS[side]:
            series = []
            for v in vs:
                b = band_rows(packs[v], side, band)
                if b and b.get(shrunk_key) is not None and b.get("n"):
                    series.append((v, b[shrunk_key], b.get(raw_key), float(b["n"])))
                else:
                    series.append((v, None, None, None))
            cells = "  ".join(
                f"{v}:{'--' if p is None else f'{p * 100:5.1f}'}" for v, p, _, _ in series
            )
            tags = []
            usable = [(v, p, r, n) for v, p, r, n in series if p is not None]
            # trend: K consecutive shrunk-metric declines ending at the latest version
            if len(usable) >= k + 1:
                tail = usable[-(k + 1):]
                if all(tail[i + 1][1] < tail[i][1] for i in range(k)):
                    tags.append(f"TREND({k}x declining)")
                # cum: latest vs K back, raw two-proportion z
                v0, _, r0, n0 = tail[0]
                v1, _, r1, n1 = tail[-1]
                if r0 is not None and r1 is not None and n0 and n1:
                    z = two_prop_z(r0, n0, r1, n1)
                    if z <= -2.0:
                        tags.append(f"CUM({v0}->{v1} {100 * (r1 - r0):+.1f}pp z={z:+.2f})")
            line = f"{side:4s} {band:6s}  {cells}"
            if tags:
                flags.append({"side": side, "band": band, "tags": tags})
                line += "   <<< " + "; ".join(tags)
            print(line)
    print()
    if flags:
        print("FLAGGED tiers (confirm with a fixed-window assess before acting):")
        for f in flags:
            print(f"  {f['side']} {f['band']}: {'; '.join(f['tags'])}")
    else:
        print("No tier shows a sustained or statistically real decline across the lineage.")
    return flags


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--versions", help="comma list, ascending ship order "
                                       "(default: all hydrated packs by numeric id)")
    ap.add_argument("--metric", choices=("tp", "wr"), default="tp",
                    help="tp = option-aligned (PRIMARY), wr = generic")
    ap.add_argument("--k", type=int, default=3, help="consecutive declines for a trend flag")
    args = ap.parse_args()
    versions = ([s.strip() for s in args.versions.split(",") if s.strip()]
                if args.versions else discover_versions())
    run(versions, args.metric, args.k)


if __name__ == "__main__":
    main()
