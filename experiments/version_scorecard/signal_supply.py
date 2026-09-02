#!/usr/bin/env python
"""Tier-2 hydration depth: per-version signal-supply burstiness.

The research packs only expose AVERAGE signals/day, which hides whether supply
is steady (good — you can keep the 14-slot book full and recycle ~2-day holds)
or bursty (bad — you cannot bank slots, so a 50-signal day overflows and a
0-signal day starves). This computes the supply DISTRIBUTION shape directly from
the score rows (a scoring-surface property, so profile-independent):

  - mean tradable signals/day (calls overall>=75, puts overall<=25; 70-74
    overflow is disabled in the live cascade so excluded)
  - CV (std/mean) and Gini of daily supply  -> burstiness (lower = steadier)
  - dry_day_rate                            -> % active days with ZERO supply
  - p20 of 20-day rolling mean              -> clustered-drought catcher
  - recycle_coverage vs a nominal demand    -> "just-enough" hydration

Read-only: a single indexed GROUP BY date COUNT per version. Writes
supply_burstiness.json into the scorecard dir; score_versions.py merges it.

Run (Windows):
    set PYTHONIOENCODING=utf-8 && set PYTHONUTF8=1 && \
    python experiments/version_scorecard/signal_supply.py --versions v44,v52,v57,v59,v60,v65
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_versions import manager  # noqa: E402
from database.trader_database import DB  # noqa: E402

OUT_DIR = ROOT / ".cache" / "algorithm_versions" / "_scorecard"
DEFAULT_VERSIONS = ["v44", "v46", "v50", "v52", "v53", "v57", "v58", "v59", "v60", "v65", "v66"]
CALL_GATE = 75   # tradable call supply (75+; 70-74 overflow disabled)
PUT_GATE = 25    # tradable put supply (<=25)
# Nominal daily demand = MAX_POSITIONS / avg_hold_bars (~14 / 2.26). The book is
# calls-first into a shared 14-slot pool with ~2-day holds.
DEMAND_PER_DAY = 14.0 / 2.26
ROLL = 20

# Per-window slices for the bear-tape "binding drought" check. The 5y mean is
# never the binding constraint (supply ~12-14/day >> ~6.2 demand); thin regimes
# are. Quality (ebar) is era-stable, so the gate keeps ebar at 5y and only takes
# lambda_eff = demand*recycle_coverage per window. These are sliced from the same
# 1825d pull (no extra query); windows with < 20 active days are skipped.
WINDOW_RANGES = {
    "2022": (date(2022, 1, 1), date(2022, 12, 31)),   # bear tape -- usual binding window
    "2023": (date(2023, 1, 1), date(2023, 12, 31)),
    "2024": (date(2024, 1, 1), date(2024, 12, 31)),
    "2025": (date(2025, 1, 1), date(2025, 12, 31)),
    "dip": (date(2025, 11, 1), date(2026, 3, 31)),    # Nov-2025..Mar-2026 5-month dip
}


def gini(values: list[float]) -> float | None:
    """Gini coefficient of a non-negative series (0 = perfectly even supply,
    ->1 = all supply concentrated on a few days)."""
    xs = sorted(float(v) for v in values if v is not None and v >= 0)
    n = len(xs)
    if n == 0:
        return None
    total = sum(xs)
    if total <= 0:
        return 0.0
    cum = 0.0
    for i, x in enumerate(xs, start=1):
        cum += i * x
    return (2.0 * cum) / (n * total) - (n + 1.0) / n


def rolling_mean(values: list[float], window: int) -> list[float]:
    if len(values) < window:
        return []
    out = []
    s = sum(values[:window])
    out.append(s / window)
    for i in range(window, len(values)):
        s += values[i] - values[i - window]
        out.append(s / window)
    return out


def _pctile(seq: list[float], q: float):
    if not seq:
        return None
    s = sorted(seq)
    i = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[i]


def _series_metrics(call: list[float], put: list[float]) -> dict | None:
    """Burstiness + recycle_coverage block for one day-series (full or a window slice)."""
    n = len(call)
    if n == 0:
        return None
    total = [c + p for c, p in zip(call, put)]

    def stats(series: list[float]) -> dict:
        mean = statistics.fmean(series) if series else 0.0
        sd = statistics.pstdev(series) if len(series) > 1 else 0.0
        roll = rolling_mean(series, ROLL)
        return {
            "mean_per_day": mean,
            "cv": (sd / mean) if mean > 0 else None,
            "gini": gini(series),
            "dry_day_rate": sum(1 for x in series if x <= 0) / n,
            "p20_rolling20": _pctile(roll, 0.20) if roll else None,
        }

    # recycle coverage: fraction of nominal demand the version's OWN supply could
    # fill each active day, summed (saturating at demand, so over-supply earns no
    # extra credit -- the "just enough" semantics).
    filled = sum(min(t, DEMAND_PER_DAY) for t in total)
    recycle_coverage = filled / (DEMAND_PER_DAY * n) if n else None
    return {
        "active_days": n,
        "call": stats(call),
        "put": stats(put),
        "total": stats(total),
        "recycle_coverage": recycle_coverage,
        # steadiness: 1/(1+CV) on total supply (1.0 = perfectly steady).
        "steadiness": (1.0 / (1.0 + (statistics.pstdev(total) / statistics.fmean(total))))
                      if (statistics.fmean(total) > 0 and n > 1) else None,
    }


def supply_for_version(version_id: int, lookback_days: int) -> dict:
    rows = DB.execute_sql(
        """
        SELECT date,
               SUM(CASE WHEN overall >= %s THEN 1 ELSE 0 END) AS call_n,
               SUM(CASE WHEN overall <= %s THEN 1 ELSE 0 END) AS put_n
        FROM scores FORCE INDEX(scores_version_date_IDX)
        WHERE version_id = %s AND date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        GROUP BY date
        ORDER BY date
        """,
        (CALL_GATE, PUT_GATE, int(version_id), int(lookback_days)),
    ).fetchall()
    if not rows:
        return {"complete": False, "active_days": 0}

    def _d(x):
        return x.date() if hasattr(x, "date") else x

    dates = [_d(r[0]) for r in rows]
    call = [float(r[1] or 0) for r in rows]
    put = [float(r[2] or 0) for r in rows]

    base = _series_metrics(call, put)  # full 5y (back-compat top-level)

    # Per-window slices from the SAME pull -- the bear-tape binding-drought check.
    windows = {}
    for name, (start, end) in WINDOW_RANGES.items():
        idx = [i for i, d in enumerate(dates)
               if (start is None or d >= start) and (end is None or d <= end)]
        if len(idx) < 20:
            continue
        m = _series_metrics([call[i] for i in idx], [put[i] for i in idx])
        if m:
            windows[name] = m

    out = {"complete": True, "lookback_days": lookback_days, "demand_per_day": DEMAND_PER_DAY}
    out.update(base)          # call/put/total/recycle_coverage/steadiness/active_days (5y)
    out["windows"] = windows  # {name: {recycle_coverage, call/put/total burstiness, active_days}}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--versions", default=",".join(DEFAULT_VERSIONS))
    ap.add_argument("--lookback-days", type=int, default=1825)
    ap.add_argument("--out", default=str(OUT_DIR / "supply_burstiness.json"))
    args = ap.parse_args()

    tokens = [t.strip() for t in args.versions.split(",") if t.strip()]
    # Generous timeouts + reconnect: this runs concurrently with the MC grid,
    # which contends on the scores table (same pattern as sweep_profiles /
    # version_velocity_ranker).
    try:
        DB.connect_params["read_timeout"] = 600
        DB.connect_params["write_timeout"] = 600
    except Exception:
        pass
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out: dict = {
        "schema": "signal_supply.v1",
        "lookback_days": args.lookback_days,
        "call_gate": CALL_GATE, "put_gate": PUT_GATE,
        "demand_per_day": DEMAND_PER_DAY, "rolling_window": ROLL,
        "versions": {},
    }
    # Resume: keep already-complete versions from a prior (possibly killed) run.
    if out_path.exists():
        try:
            prior = json.loads(out_path.read_text(encoding="utf-8")) or {}
            for k, val in (prior.get("versions") or {}).items():
                # require windows so pre-per-window cache entries get recomputed
                if isinstance(val, dict) and val.get("complete") and val.get("windows"):
                    out["versions"][k] = val
        except Exception:
            pass

    def _flush():
        out_path.write_text(json.dumps(out, indent=2, sort_keys=True, default=str), encoding="utf-8")

    DB.connect(reuse_if_open=True)
    for tok in tokens:
        _cached = out["versions"].get(tok, {})
        if _cached.get("complete") and _cached.get("windows"):
            print(f"{tok}: cached (with windows), skipping", flush=True)
            continue
        v = manager.resolve_algorithm_version(tok, create_current=False)
        m = None
        for attempt in range(1, 4):
            try:
                m = supply_for_version(int(v.id), args.lookback_days)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"{tok}: attempt {attempt}/3 failed ({type(exc).__name__}); reconnecting", flush=True)
                try:
                    if not DB.is_closed():
                        DB.close()
                except Exception:
                    pass
                DB.connect(reuse_if_open=False)
        if m is None:
            out["versions"][tok] = {"complete": False, "error": "query failed after retries"}
            print(f"{tok}: FAILED after retries", flush=True)
            _flush()
            continue
        out["versions"][tok] = m
        t = m.get("total", {})
        if m.get("complete"):
            print(f"{tok}: supply/day={t.get('mean_per_day'):.1f} CV={t.get('cv'):.2f} "
                  f"gini={t.get('gini'):.3f} dry={t.get('dry_day_rate'):.1%} "
                  f"recycle_cov={m.get('recycle_coverage'):.3f} steady={m.get('steadiness'):.3f}",
                  flush=True)
        else:
            print(f"{tok}: no rows", flush=True)
        _flush()  # incremental: persist after every version so a kill keeps progress

    print(f"wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
