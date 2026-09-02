"""Synthesize Stage-1 growth-gate inputs for the v73 candidates (2026-06-12).

The growth gate (experiments/version_scorecard/stage1_growth_gate.py) reads
  .cache/algorithm_versions/<tok>/research_pack/utility_5y_wr15.json
  .cache/algorithm_versions/_scorecard/supply_burstiness.json
keyed by version token. Pre-ship candidates have no DB rows / packs, so this
builds both surfaces from the ReSim arm parquets — the SAME measurement for
baseline and candidates (apples-to-apples; ReSim baseline = 98.54% exact vs
stored v72).

Tokens:  v72sim <- arm_baseline   v73a <- arm_bundle_a   v73b <- arm_bundle_b

CALLS-ONLY by design: the live Apex book is calls-only (PUT_TIER_ALLOC all 0,
MAX_POSITIONS_PUT=0), but the gate's greedy fill still models puts competing
for slots with stale v60-era PUT_F allocs. The WCF retirement floods the put
band ~8x in BOTH candidates; modeling those non-traded puts would saturate the
filled book and mask the call-side trio signal entirely. Faithful model of the
funded book = call bands + call supply only. (Put-side W4 is checked manually
in the ship notes — the put flood is ~flat-WR, z=-0.83.)

Shrinkage/refs mirror algorithm_versions/research_pack.py UtilityParams
(shrink_k=100, call wr_ref=0.75, call tp_ref=0.66) — identical for all three
tokens, so dG is unaffected by the ref choice.

Run: python experiments/dampener_ablation_v72/gate_inputs.py
"""
import json
import os
import sys
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import duckdb
import polars as pl

from experiments.version_scorecard.signal_supply import (  # noqa: E402
    WINDOW_RANGES, _series_metrics)

OUT_DIR = os.path.join(_ROOT, ".cache", "dampener_ablation_v72")
DUCK = os.path.join(_ROOT, ".cache", "barrier_outcomes.duckdb").replace("\\", "/")
PACK_ROOT = os.path.join(_ROOT, ".cache", "algorithm_versions")
SUPPLY_JSON = os.path.join(PACK_ROOT, "_scorecard", "supply_burstiness.json")

SHRINK_K = 100.0
CALL_WR_REF = 0.75
CALL_TP_REF = 0.66
CALL_BANDS = [(95, 100, "95-100"), (90, 94, "90-94"), (85, 89, "85-89"),
              (80, 84, "80-84"), (75, 79, "75-79")]
CALL_GATE = 75

ARMS = {"v72sim": "baseline", "v73a": "bundle_a", "v73b": "bundle_b"}
META_MSG = {
    "v72sim": "ReSim baseline arm (exact v72 replica, 98.54% vs stored)",
    "v73a": "candidate A: retire WCF+ICH (bundle_a arm)",
    "v73b": "candidate B: retire WCF+ICH+CWCF+CSWC+SCW (bundle_b arm)",
}


def shrunk(rate, n, ref):
    if rate is None or n <= 0:
        return None
    return (rate * n + SHRINK_K * ref) / (n + SHRINK_K)


def load_arm_calls(con, arm):
    p = os.path.join(OUT_DIR, f"arm_{arm}.parquet").replace("\\", "/")
    con.execute(f"CREATE OR REPLACE TEMP TABLE arm AS SELECT * FROM read_parquet('{p}')")
    return con.execute("""
        SELECT a.symbol, a.date, a.overall,
               oc.result AS opt_call, gc.result AS gen_call
        FROM arm a
        LEFT JOIN barrier_outcomes oc ON oc.symbol=a.symbol AND oc.date=a.date
             AND oc.side='low' AND oc.barrier_set='30dte_opt'     AND oc.w_days=15
        LEFT JOIN barrier_outcomes gc ON gc.symbol=a.symbol AND gc.date=a.date
             AND gc.side='low' AND gc.barrier_set='30dte_generic' AND gc.w_days=15
        WHERE a.overall >= 70
    """).pl()


def bands_payload(df):
    bands = []
    for lo, hi, lab in CALL_BANDS:
        f = df.filter((pl.col("overall") >= lo) & (pl.col("overall") <= hi))
        g = f.filter(pl.col("gen_call").is_not_null())
        o = f.filter(pl.col("opt_call").is_not_null())
        n = g.height
        wr = (g.filter(pl.col("gen_call") == 1).height / n) if n else None
        tp_n = o.height
        tp = (o.filter(pl.col("opt_call") == 1).height / tp_n) if tp_n else None
        bands.append({
            "band": lab, "n": n, "wr": wr,
            "wr_shrunk": shrunk(wr, n, CALL_WR_REF),
            "tp_n": tp_n, "tp": tp,
            "tp_shrunk": shrunk(tp, tp_n, CALL_TP_REF),
        })
        print(f"    {lab:7} n={n:5d} wr={wr if wr is None else round(100*wr,1)} "
              f"tp={tp if tp is None else round(100*tp,1)}")
    return bands


def supply_payload(arm):
    """Calls-only per-day supply (>=75, ALL rows incl unresolved) over every
    active day in the arm parquet, mirroring signal_supply.py semantics."""
    p = os.path.join(OUT_DIR, f"arm_{arm}.parquet")
    df = pl.read_parquet(p).with_columns(pl.col("date").str.to_date())
    daily = (df.group_by("date")
               .agg((pl.col("overall") >= CALL_GATE).sum().alias("call_n"))
               .sort("date"))
    dates = daily["date"].to_list()
    call = [float(x) for x in daily["call_n"].to_list()]
    put = [0.0] * len(call)
    base = _series_metrics(call, put)
    windows = {}
    for name, (start, end) in WINDOW_RANGES.items():
        idx = [i for i, d in enumerate(dates)
               if (start is None or d >= start) and (end is None or d <= end)]
        if len(idx) < 20:
            continue
        m = _series_metrics([call[i] for i in idx], [put[i] for i in idx])
        if m:
            windows[name] = m
    out = {"complete": True, "lookback_days": (date.today() - dates[0]).days,
           "demand_per_day": 14.0 / 2.26, "calls_only": True}
    out.update(base)
    out["windows"] = windows
    return out


def main():
    con = duckdb.connect(DUCK, read_only=True)
    with open(SUPPLY_JSON, encoding="utf-8") as f:
        supply = json.load(f)
    for tok, arm in ARMS.items():
        print(f"[{tok}] <- arm_{arm}", flush=True)
        df = load_arm_calls(con, arm)
        pack = {
            "version": {"id": tok, "label": tok, "git_commit": "(resim)",
                        "git_message": META_MSG[tok],
                        "created_at": date.today().isoformat()},
            "note": "SYNTHETIC calls-only gate input from ReSim arm parquet "
                    "(experiments/dampener_ablation_v72/gate_inputs.py)",
            "params": {"shrink_k": SHRINK_K, "call_wr_ref": CALL_WR_REF,
                       "call_tp_ref": CALL_TP_REF},
            "sides": {"call": {"bands": bands_payload(df)}, "put": {"bands": []}},
        }
        d = os.path.join(PACK_ROOT, tok, "research_pack")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "utility_5y_wr15.json"), "w", encoding="utf-8") as f:
            json.dump(pack, f, indent=2)
        sp = supply_payload(arm)
        supply.setdefault("versions", {})[tok] = sp
        t = sp["total"]
        print(f"    supply/day={t['mean_per_day']:.2f} cov={sp['recycle_coverage']:.3f} "
              f"windows={ {w: round(m['recycle_coverage'],3) for w, m in sp['windows'].items()} }",
              flush=True)
    with open(SUPPLY_JSON, "w", encoding="utf-8") as f:
        json.dump(supply, f, indent=2, sort_keys=True, default=str)
    print("wrote synthetic packs + supply rows (v72sim, v73a, v73b)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
