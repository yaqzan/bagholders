"""Integrity-audit honest A/B evaluation (2026-06-09/10) — ReSim pattern.

Builds ScoreSimulator ONCE on the WORKTREE code (F2 lagged spy_wk, F4 PIT mcap),
then re-simulates the full universe per arm by patching module-level constants /
simulator maps, persisting each arm's (symbol, date, overall) for the barrier
analysis (analyze_ab.py joins to the rebuilt barrier_outcomes cache).

Arms:
  legacy   : pre-fix replica — UNLAGGED mis_stress map + STATIC mcap + UNLAGGED
             JA4 put-mult map + wave OFF.  (validation reference vs stored v70)
  fixed    : v71 core — lagged mis_stress + PIT mcap + LAGGED JA4 + wave OFF.
  ms_off   : fixed + MIS_STRESS_CALL_DAMPEN=0          (mis_stress keep/retire)
  ja4_off  : fixed + empty put-mult map                (JA4 keep/retire)
  mcd_off  : fixed + MCD_ENABLED=False                 (MCD keep/retire)
  wave_on  : fixed + SECTOR_BREADTH_WAVE_ENABLED=True on the REBUILT source CSV
             (wave revive/retire; NOT a reproduction of the lost v57 file)

NOTE: the simulator has no continuation-echo (prior_signal) path — all arms
share that lack, so arm-vs-arm deltas are internally valid; the legacy-vs-stored
validation tolerates cont-lift mismatches (counted explicitly).

Run from the WORKTREE root:
    python experiments/integrity_audit_2026_06/ab_eval.py --parallel 7   # full universe
    python experiments/integrity_audit_2026_06/ab_eval.py --shard 0/7    # one shard
    python experiments/integrity_audit_2026_06/ab_eval.py --smoke        # EVAL_SYMBOLS subset
Env: EVAL_START (default 2021-06-01), EVAL_SYMBOLS (csv, smoke only)

Shard mode: each worker bulk-loads 1/N of the sorted universe and runs ALL arms
on its shard (module patches are per-process, so arms stay sequential within a
worker while shards run concurrently). Parent merges shard parquets at the end.
"""
import json
import os
import sys
import time
from datetime import date, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
# FORCE-insert at index 0. The guarded `if _ROOT not in sys.path` idiom is a
# TRAP in worktrees: the global PYTHONPATH puts the MAIN checkout
# (C:\Development\Trader) ahead of any later worktree entry, so a skipped
# insert silently runs this script against the main checkout's code.
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import polars as pl

OUT_DIR = os.path.join(_ROOT, ".cache", "integrity_audit_2026_06")
os.makedirs(OUT_DIR, exist_ok=True)

EVAL_START = date.fromisoformat(os.environ.get("EVAL_START", "2021-06-01"))
REBUILT_WAVE_CSV = os.path.join(_ROOT, ".cache", "market_wave",
                                "predictive_market_wave_v57_source.csv")


# ── market-level map builders (lag toggle) ──────────────────────────────────

def _spy_wk_lookup_factory(lag_days):
    import bisect
    from database.models.core import WeeklyScore
    rows = list(
        WeeklyScore.select(WeeklyScore.date, WeeklyScore.composite)
        .where(WeeklyScore.symbol == 'SPY', WeeklyScore.composite.is_null(False))
        .order_by(WeeklyScore.date.asc()).namedtuples()
    )
    dts = [r.date for r in rows]
    vals = [float(r.composite) for r in rows]

    def lookup(d):
        i = bisect.bisect_right(dts, d - timedelta(days=lag_days)) - 1
        return vals[i] if i >= 0 else None
    return lookup


def build_mis_stress_map(lag_days):
    """Replicates core._load_mis_stress_map with a configurable spy_wk lag.
    lag_days=0 -> the pre-fix (leaky) lookup; 7 -> last-completed-week (F2)."""
    from database.models.core import MarketRegime, MIS_STRESS_FULL
    spy_wk = _spy_wk_lookup_factory(lag_days)
    rows = list(
        MarketRegime.select(
            MarketRegime.date, MarketRegime.regime_composite,
            MarketRegime.vix_close, MarketRegime.spy_close, MarketRegime.spy_ema200,
        ).where(MarketRegime.regime_multiplier.is_null(False))
        .order_by(MarketRegime.date.asc()).namedtuples()
    )
    close_dates = [r.date for r in rows if r.spy_close is not None]
    close_map = {r.date: float(r.spy_close) for r in rows if r.spy_close is not None}
    close_idx = {d: i for i, d in enumerate(close_dates)}
    out = {}
    for r in rows:
        comp = float(r.regime_composite) if r.regime_composite is not None else None
        if comp is None or r.spy_close is None or r.spy_ema200 is None or r.vix_close is None:
            continue
        s_wk = spy_wk(r.date)
        if s_wk is None:
            continue
        i = close_idx.get(r.date)
        if i is None or i < 10:
            continue
        spy_10d = (close_map[r.date] / close_map[close_dates[i - 10]] - 1.0) * 100.0
        bull_obj = (float(r.spy_close) > float(r.spy_ema200)
                    and float(r.vix_close) < 20.0 and spy_10d > 0)
        if not bull_obj:
            out[r.date] = 0.0
            continue
        out[r.date] = min(1.0, max(0.0, s_wk - comp) / MIS_STRESS_FULL)
    return out


def build_put_mult_map(lag_days):
    """JA4 blended put multiplier per regime date (core._compute_put_regime_mult)."""
    from database.models.core import MarketRegime, _JA4_SPY_WK_WEIGHT
    from market_regime import compute_regime_multiplier
    spy_wk = _spy_wk_lookup_factory(lag_days)
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.regime_composite)
        .where(MarketRegime.regime_multiplier.is_null(False))
        .order_by(MarketRegime.date.asc()).namedtuples()
    )
    out = {}
    for r in rows:
        comp = float(r.regime_composite) if r.regime_composite is not None else None
        if comp is None:
            continue
        s_wk = spy_wk(r.date)
        if s_wk is None:
            continue
        blended = max(0.0, min(100.0, (1.0 - _JA4_SPY_WK_WEIGHT) * comp
                               + _JA4_SPY_WK_WEIGHT * s_wk))
        out[r.date] = compute_regime_multiplier(blended)
    return out


# ── arm machinery ────────────────────────────────────────────────────────────

SHARD_TAG = ""   # set in shard mode, e.g. ".shard3"


def persist(name, results, feats=None):
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
    if feats is not None:
        pf = os.path.join(OUT_DIR, f"features_mcap{SHARD_TAG}.parquet")
        pl.DataFrame(feats, schema={"symbol": pl.Utf8, "date": pl.Utf8,
                                    "overall": pl.Int64, "pit_mcap_b": pl.Float64,
                                    "static_mcap_b": pl.Float64}).write_parquet(pf)
        print(f"  [features] {len(feats)} mcap rows -> {pf}", flush=True)


def run_arm(sim, name, *, sc_patch=None, ms_map=None, put_map=None,
            clear_pit_mcap=False, since=EVAL_START, collect_mcap=False):
    import database.utils.scoring as SC
    import database.utils.sector_breadth_wave as sbw

    t0 = time.time()
    saved_sc = {}
    saved_ms = sim._mis_stress_map
    saved_pm = sim._put_mult_map
    saved_pit = None
    try:
        if sc_patch:
            for k, v in sc_patch.items():
                saved_sc[k] = getattr(SC, k)
                if k == "SECTOR_BREADTH_WAVE_PARAMS":
                    setattr(SC, k, v)          # full dict replacement
                else:
                    setattr(SC, k, v)
        if ms_map is not None:
            sim._mis_stress_map = ms_map
        if put_map is not None:
            sim._put_mult_map = put_map
        if clear_pit_mcap:
            saved_pit = {s: ctx.pit_mcap_map for s, ctx in sim._contexts.items()}
            for ctx in sim._contexts.values():
                ctx.pit_mcap_map = {}
        sbw._SERIES_CACHE.clear()
        sbw._GUARD_WARNED.clear()

        res = sim.simulate(since=since)

        feats = None
        if collect_mcap:
            feats = []
            for (s, d), o in res.items():
                if o is None or o < 55:
                    continue
                ctx = sim._contexts.get(s)
                if ctx is None:
                    continue
                feats.append({
                    "symbol": s, "date": d.isoformat(), "overall": int(o),
                    "pit_mcap_b": ctx.pit_mcap_map.get(d, ctx.mcap_b),
                    "static_mcap_b": ctx.mcap_b,
                })
        persist(name, res, feats)
        print(f"  [{name}] simulate took {time.time()-t0:.0f}s", flush=True)
        return res
    finally:
        for k, v in saved_sc.items():
            setattr(SC, k, v)
        sim._mis_stress_map = saved_ms
        sim._put_mult_map = saved_pm
        if saved_pit is not None:
            for s, ctx in sim._contexts.items():
                ctx.pit_mcap_map = saved_pit[s]
        sbw._SERIES_CACHE.clear()


def validate_vs_stored(legacy_res, shard_syms=None):
    """Match-rate of the legacy arm vs stored v70 rows (cont-echo tolerated).
    Persists RAW COUNTS so shard reports merge by summation."""
    from database.models.core import Score, AlgorithmVersion
    ver = AlgorithmVersion.get_active_scores_version()
    q = Score.select(Score.symbol, Score.date, Score.overall).where(
        (Score.version == ver.id) & (Score.date >= EVAL_START)
        & (Score.overall.is_null(False)))
    if shard_syms:
        q = q.where(Score.symbol.in_(list(shard_syms)))
    stored = {(s, d): int(o) for s, d, o in q.tuples()}
    common = [k for k in stored if k in legacy_res and legacy_res[k] is not None]
    if not common:
        print("VALIDATION: no overlap!?", flush=True)
        return
    exact = sum(1 for k in common if int(legacy_res[k]) == stored[k])
    sum_abs = sum(abs(int(legacy_res[k]) - stored[k]) for k in common)
    big = [k for k in common if abs(int(legacy_res[k]) - stored[k]) >= 3]
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
    rep = {"stored_rows": len(stored), "sim_rows": len(legacy_res),
           "common": len(common), "exact": exact, "sum_abs_diff": sum_abs,
           "n_ge3": len(big), "cont_sampled": n_sampled, "cont_hits": n_cont,
           "exact_match_pct": 100.0 * exact / len(common)}
    print("VALIDATION:", json.dumps(rep), flush=True)
    with open(os.path.join(OUT_DIR, f"validation{SHARD_TAG}.json"), "w") as f:
        json.dump(rep, f, indent=2)


def universe_symbols():
    from database.models.core import Stock
    return sorted({s.symbol for s in Stock.select(Stock.symbol)})


def run_parallel(n_workers):
    """Spawn shard subprocesses, wait, merge shard outputs."""
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

    # merge
    arms = ["legacy", "fixed", "ms_off", "ja4_off", "mcd_off", "wave_on", "bundle"]
    for a in arms:
        parts = [p for p in
                 (os.path.join(OUT_DIR, f"arm_{a}.shard{i}.parquet")
                  for i in range(n_workers)) if os.path.exists(p)]
        if not parts:
            continue
        df = pl.concat([pl.read_parquet(p) for p in parts])
        df.write_parquet(os.path.join(OUT_DIR, f"arm_{a}.parquet"))
        print(f"merged arm_{a}: {df.height} rows", flush=True)
    fparts = [p for p in
              (os.path.join(OUT_DIR, f"features_mcap.shard{i}.parquet")
               for i in range(n_workers)) if os.path.exists(p)]
    if fparts:
        fdf = pl.concat([pl.read_parquet(p) for p in fparts])
        fdf.write_parquet(os.path.join(OUT_DIR, "features_mcap.parquet"))
        print(f"merged features_mcap: {fdf.height} rows", flush=True)
    # merge validation counts
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
    smoke = "--smoke" in sys.argv
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
    elif os.environ.get("EVAL_SYMBOLS"):
        syms = [s.strip() for s in os.environ["EVAL_SYMBOLS"].split(",") if s.strip()]

    from simulator import ScoreSimulator
    import database.utils.scoring as SC
    print("simulator module:", sys.modules['simulator'].__file__, flush=True)
    print("scoring module  :", SC.__file__, flush=True)

    lookback = (date.today() - EVAL_START).days + 420   # warmup buffer
    print(f"building ScoreSimulator lookback={lookback}d "
          f"symbols={'ALL' if not syms else len(syms)}", flush=True)
    t0 = time.time()
    sim = ScoreSimulator(symbols=syms, lookback_days=lookback)
    print(f"simulator ready in {time.time()-t0:.0f}s", flush=True)

    print("building market-level maps...", flush=True)
    ms_unlagged = build_mis_stress_map(0)
    ms_lagged = build_mis_stress_map(7)
    pm_unlagged = build_put_mult_map(0)
    pm_lagged = build_put_mult_map(7)
    # sanity: harness lagged map must match the simulator's own (F2-fixed) map
    own = sim._mis_stress_map
    diffs = [d for d in own if abs(own[d] - ms_lagged.get(d, -1)) > 1e-9]
    print(f"  ms map sanity: sim_own={len(own)} harness_lagged={len(ms_lagged)} "
          f"mismatches={len(diffs)}", flush=True)

    base_off = {"SECTOR_BREADTH_WAVE_ENABLED": False}
    only = set(os.environ.get("EVAL_ARMS", "").split(",")) - {""}

    def want(n):
        return not only or n in only

    # 1) legacy (validation reference)
    if want("legacy"):
        legacy = run_arm(sim, "legacy", sc_patch=dict(base_off),
                         ms_map=ms_unlagged, put_map=pm_unlagged, clear_pit_mcap=True)
        validate_vs_stored(legacy, shard_syms=syms)

    # 2) fixed (v71 core)
    if want("fixed"):
        run_arm(sim, "fixed", sc_patch=dict(base_off),
                ms_map=ms_lagged, put_map=pm_lagged, collect_mcap=True)

    # 3) mis_stress OFF
    if want("ms_off"):
        run_arm(sim, "ms_off", sc_patch=dict(base_off, MIS_STRESS_CALL_DAMPEN=0.0),
                ms_map=ms_lagged, put_map=pm_lagged)

    # 4) JA4 OFF
    if want("ja4_off"):
        run_arm(sim, "ja4_off", sc_patch=dict(base_off),
                ms_map=ms_lagged, put_map={})

    # 5) MCD OFF
    if want("mcd_off"):
        run_arm(sim, "mcd_off", sc_patch=dict(base_off, MCD_ENABLED=False),
                ms_map=ms_lagged, put_map=pm_lagged)

    # 6) wave ON (rebuilt source)
    if want("wave_on"):
        wave_params = dict(SC.SECTOR_BREADTH_WAVE_PARAMS)
        wave_params["path"] = REBUILT_WAVE_CSV
        run_arm(sim, "wave_on",
                sc_patch={"SECTOR_BREADTH_WAVE_ENABLED": True,
                          "SECTOR_BREADTH_WAVE_PARAMS": wave_params},
                ms_map=ms_lagged, put_map=pm_lagged)

    # 7) bundle = the exact v71 ship state (all four retirements together):
    #    lagged spy_wk + PIT mcap + MIS_STRESS 0 + JA4 off + MCD off + wave off
    if want("bundle"):
        run_arm(sim, "bundle",
                sc_patch=dict(base_off, MIS_STRESS_CALL_DAMPEN=0.0,
                              MCD_ENABLED=False),
                ms_map=ms_lagged, put_map={})

    print("ALL ARMS DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
