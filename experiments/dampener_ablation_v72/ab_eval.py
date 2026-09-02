"""N1 dampener-stack ablation on the honest v72 substrate (2026-06-12) — ReSim pattern.

Clones the proven experiments/integrity_audit_2026_06/ab_eval.py sharded ReSim
harness. Builds ScoreSimulator ONCE per shard on the CURRENT checkout (v72:
honest weekly blend, lagged spy_wk, PIT mcap, WCF ramp), then re-simulates the
full universe per arm by patching module-level scoring constants.

Why: every remaining score-stage dampener — WCF, CWCF, CSWC, CWWD, SCW, ICH,
WVD — was calibrated pre-v69 on look-ahead-contaminated scores (NEW_LEADS N1).
The founding wadj-neg evidence (z=+10.1) collapsed to noise on the honest
re-mine; v69 changed the wadj feature itself. This run asks each mechanism to
re-earn its keep via delta-cohort WR on the option-aligned barrier.

Arms (one-at-a-time ablation; baseline = exact v72 replica + validation):
  baseline  : no patches (validation reference vs stored v72 rows)
  wcf_off   : WCF_LIFT_K=0          (v27 put-floor lift + v72 ramp)
  cwcf_off  : CWCF_DAMPEN_K=0       (v32 call WCF-mirror, >=75 wadj<1)
  cwwd_off  : CWWD_DAMPEN_K=0       (v38 call weak-weekly, 70-74)
  cswc_off  : CSWC_DAMPEN_K=0       (call stoch/weekly conviction dampener)
  scw_off   : SCW_ENABLED=False     (v60 r054 stoch conviction wave)
  ich_off   : ICH_ENABLED=False     (v44 Ichimoku kijun state dampener)
  wvd_off   : WVD_WAVE_ENABLED=False (v46/v59 weekly-volume wave modulator)

NOTE: the simulator has no continuation-echo path — all arms share that lack,
so arm-vs-arm deltas are internally valid; baseline-vs-stored validation
tolerates cont-lift mismatches (v71 campaign: 98.43% exact).

Run from the MAIN checkout root:
    python experiments/dampener_ablation_v72/ab_eval.py --parallel 6
    python experiments/dampener_ablation_v72/ab_eval.py --shard 0/6
    python experiments/dampener_ablation_v72/ab_eval.py --smoke
Env: EVAL_START (default 2021-06-01), EVAL_SYMBOLS (csv, smoke), EVAL_ARMS (csv)
"""
import json
import os
import sys
import time
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
# FORCE-insert at index 0 (worktree/PYTHONPATH trap — see G29).
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import polars as pl

OUT_DIR = os.path.join(_ROOT, ".cache", "dampener_ablation_v72")
os.makedirs(OUT_DIR, exist_ok=True)

EVAL_START = date.fromisoformat(os.environ.get("EVAL_START", "2021-06-01"))

# arm -> module-constant patch on database.utils.scoring
ARM_PATCHES = {
    "baseline": {},
    "wcf_off":  {"WCF_LIFT_K": 0.0},
    "cwcf_off": {"CWCF_DAMPEN_K": 0.0},
    "cwwd_off": {"CWWD_DAMPEN_K": 0.0},
    "cswc_off": {"CSWC_DAMPEN_K": 0.0},
    "scw_off":  {"SCW_ENABLED": False},
    "ich_off":  {"ICH_ENABLED": False},
    "wvd_off":  {"WVD_WAVE_ENABLED": False},
    # ship-handoff bundle-confirm arms (2026-06-12): interactions matter —
    # CWCF/CWWD/CSWC/SCW overlap on weak-weekly/low-stoch call cohorts.
    "bundle_a": {"WCF_LIFT_K": 0.0, "ICH_ENABLED": False},
    "bundle_b": {"WCF_LIFT_K": 0.0, "ICH_ENABLED": False,
                 "CWCF_DAMPEN_K": 0.0, "CSWC_DAMPEN_K": 0.0,
                 "SCW_ENABLED": False},
}
ARM_ORDER = ["baseline", "wcf_off", "cwcf_off", "cwwd_off", "cswc_off",
             "scw_off", "ich_off", "wvd_off", "bundle_a", "bundle_b"]

SHARD_TAG = ""


def persist(name, results):
    rows = [{"symbol": s, "date": d.isoformat(), "overall": int(o)}
            for (s, d), o in results.items() if o is not None]
    df = pl.DataFrame(rows, schema={"symbol": pl.Utf8, "date": pl.Utf8,
                                    "overall": pl.Int64})
    p = os.path.join(OUT_DIR, f"arm_{name}{SHARD_TAG}.parquet")
    df.write_parquet(p)
    n_call = df.filter(pl.col("overall") >= 70).height if df.height else 0
    n_put = df.filter(pl.col("overall") <= 30).height if df.height else 0
    print(f"  [{name}] rows={df.height}  calls70+={n_call}  puts<=30={n_put}  -> {p}",
          flush=True)


def run_arm(sim, name, sc_patch, since=EVAL_START):
    import database.utils.scoring as SC
    t0 = time.time()
    saved = {}
    try:
        for k, v in sc_patch.items():
            saved[k] = getattr(SC, k)
            setattr(SC, k, v)
        res = sim.simulate(since=since)
        persist(name, res)
        print(f"  [{name}] simulate took {time.time()-t0:.0f}s", flush=True)
        return res
    finally:
        for k, v in saved.items():
            setattr(SC, k, v)


def validate_vs_stored(base_res, shard_syms=None):
    """Match-rate of the baseline arm vs stored ACTIVE (v72) rows.
    Persists RAW COUNTS so shard reports merge by summation."""
    from database.models.core import Score, AlgorithmVersion
    ver = AlgorithmVersion.get_active_scores_version()
    q = Score.select(Score.symbol, Score.date, Score.overall).where(
        (Score.version == ver.id) & (Score.date >= EVAL_START)
        & (Score.overall.is_null(False)))
    if shard_syms:
        q = q.where(Score.symbol.in_(list(shard_syms)))
    stored = {(s, d): int(o) for s, d, o in q.tuples()}
    common = [k for k in stored if k in base_res and base_res[k] is not None]
    if not common:
        print("VALIDATION: no overlap!?", flush=True)
        return
    exact = sum(1 for k in common if int(base_res[k]) == stored[k])
    sum_abs = sum(abs(int(base_res[k]) - stored[k]) for k in common)
    big = [k for k in common if abs(int(base_res[k]) - stored[k]) >= 3]
    n_cont = n_sampled = 0
    if big:
        import random
        sample = random.sample(big, min(150, len(big)))
        n_sampled = len(sample)
        for s, d in sample:
            r = Score.get_or_none((Score.symbol == s) & (Score.date == d)
                                  & (Score.version == ver.id))
            if r is not None and r.weight_info and "cont_lift" in str(r.weight_info):
                n_cont += 1
    rep = {"active_version": ver.id, "stored_rows": len(stored),
           "sim_rows": len(base_res), "common": len(common), "exact": exact,
           "sum_abs_diff": sum_abs, "n_ge3": len(big),
           "cont_sampled": n_sampled, "cont_hits": n_cont,
           "exact_match_pct": 100.0 * exact / len(common)}
    print("VALIDATION:", json.dumps(rep), flush=True)
    with open(os.path.join(OUT_DIR, f"validation{SHARD_TAG}.json"), "w") as f:
        json.dump(rep, f, indent=2)


def universe_symbols():
    from database.models.core import Stock
    return sorted({s.symbol for s in Stock.select(Stock.symbol)})


def run_parallel(n_workers):
    import subprocess
    script = os.path.abspath(__file__)
    procs = []
    for i in range(n_workers):
        env = dict(os.environ)
        cmd = [sys.executable, "-u", script, "--shard", f"{i}/{n_workers}"]
        logf = open(os.path.join(OUT_DIR, f"shard{i}.log"), "w", encoding="utf-8")
        procs.append((i, subprocess.Popen(cmd, env=env, cwd=_ROOT,
                                          stdout=logf, stderr=subprocess.STDOUT), logf))
    fails = 0
    for i, p, logf in procs:
        rc = p.wait()
        logf.close()
        print(f"shard {i} exit={rc}", flush=True)
        if rc != 0:
            fails += 1
    if fails:
        print(f"ERROR: {fails} shard(s) failed", flush=True)
        return 1
    for a in ARM_ORDER:
        parts = [p for p in
                 (os.path.join(OUT_DIR, f"arm_{a}.shard{i}.parquet")
                  for i in range(n_workers)) if os.path.exists(p)]
        if not parts:
            continue
        df = pl.concat([pl.read_parquet(p) for p in parts])
        df.write_parquet(os.path.join(OUT_DIR, f"arm_{a}.parquet"))
        print(f"merged arm_{a}: {df.height} rows", flush=True)
    tot = {"stored_rows": 0, "sim_rows": 0, "common": 0, "exact": 0,
           "sum_abs_diff": 0, "n_ge3": 0, "cont_sampled": 0, "cont_hits": 0}
    for i in range(n_workers):
        vp = os.path.join(OUT_DIR, f"validation.shard{i}.json")
        if os.path.exists(vp):
            with open(vp) as f:
                v = json.load(f)
            for k in tot:
                tot[k] += v.get(k, 0)
    if tot["common"]:
        tot["exact_match_pct"] = 100.0 * tot["exact"] / tot["common"]
        tot["mean_abs_diff"] = tot["sum_abs_diff"] / tot["common"]
        tot["ge3_diff_pct"] = 100.0 * tot["n_ge3"] / tot["common"]
        if tot["cont_sampled"]:
            tot["ge3_cont_lift_pct"] = 100.0 * tot["cont_hits"] / tot["cont_sampled"]
    with open(os.path.join(OUT_DIR, "validation.json"), "w") as f:
        json.dump(tot, f, indent=2)
    print("MERGED VALIDATION:", json.dumps(tot, indent=2), flush=True)
    print("ALL SHARDS MERGED", flush=True)
    return 0


def main():
    global SHARD_TAG
    syms = None
    shard = None
    if "--parallel" in sys.argv:
        n = int(sys.argv[sys.argv.index("--parallel") + 1])
        return run_parallel(n)
    if "--shard" in sys.argv:
        spec = sys.argv[sys.argv.index("--shard") + 1]
        i, n = (int(x) for x in spec.split("/"))
        shard = (i, n)
        SHARD_TAG = f".shard{i}"
        uni = universe_symbols()
        syms = uni[i::n]
        print(f"shard {i}/{n}: {len(syms)} symbols", flush=True)
    elif "--smoke" in sys.argv or os.environ.get("EVAL_SYMBOLS"):
        syms = [s.strip() for s in os.environ.get(
            "EVAL_SYMBOLS", "AAPL,MSFT,NVDA,GIS,COHR").split(",") if s.strip()]

    from simulator import ScoreSimulator
    import database.utils.scoring as SC
    print("simulator module:", sys.modules['simulator'].__file__, flush=True)
    print("scoring module  :", SC.__file__, flush=True)
    # module-origin assert (G29): both must come from THIS checkout
    assert SC.__file__.lower().startswith(_ROOT.lower()), \
        f"scoring imported from wrong checkout: {SC.__file__}"

    lookback = (date.today() - EVAL_START).days + 420
    print(f"building ScoreSimulator lookback={lookback}d "
          f"symbols={'ALL' if not syms else len(syms)}", flush=True)
    t0 = time.time()
    sim = ScoreSimulator(symbols=syms, lookback_days=lookback)
    print(f"simulator ready in {time.time()-t0:.0f}s", flush=True)

    only = set(os.environ.get("EVAL_ARMS", "").split(",")) - {""}
    for name in ARM_ORDER:
        if only and name not in only:
            continue
        # resume guard: skip arms whose shard parquet already exists
        if SHARD_TAG and os.path.exists(
                os.path.join(OUT_DIR, f"arm_{name}{SHARD_TAG}.parquet")):
            print(f"  [{name}] shard parquet exists — skipping", flush=True)
            continue
        res = run_arm(sim, name, ARM_PATCHES[name])
        if name == "baseline":
            validate_vs_stored(res, shard_syms=syms)

    print("ALL ARMS DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
