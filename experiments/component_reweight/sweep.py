"""Stage B — Bayesian reweight sweep over the dynamic component-weight blend.

Search the 6 base weights (renormalized to sum 100 => genuine REDISTRIBUTION, not score
inflation) + 4 slopes (trend/rsi/macd/ta; bb & stoch flat per v70). Each trial re-simulates
the full v70 pipeline with the candidate weights and re-labels the new 75+ CALL population
against the score-invariant APEX (1.092/2.548 = TP30/SL70) barrier outcomes.

Objective (supply-constrained regime, ~482 sig/yr << book capacity):
  edge   = apexTP15 - (1-apexTP15)*LOSS_RATIO/TP_RATIO     # per-trade edge in TP units
         = (apexTP15*TP - (1-apexTP15)*LOSS) / TP
  obj    = supply_75plus_per_yr * (apexTP15*TP - (1-apexTP15)*LOSS)   # total annual premium edge
HARD GUARDS (reject): unlabeled_frac>0.03; apexTP15_85plus < base-2pp; supply < 0.70*base.

Records every metric; finalists get the real growth gate + APEX MC. Holdout: ledger is
already capped at CUTOFF, and ReSim filters simulated overalls to <= CUTOFF.
"""
import os, sys, json, time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from resim import ReSim, WEIGHT_KEYS
import strategy_config as SCFG

TP, LOSS = 0.30, 0.45          # APEX TP=+30%; mid loss estimate (theta exit ~ -30..-45, SL -70)
N_TRIALS = int(os.environ.get("SWEEP_TRIALS", "70"))
OUT = os.path.join(_HERE, "sweep_results.jsonl")

DEF = {k: float(getattr(SCFG.SCORING, k)) for k in WEIGHT_KEYS}

# Hand-designed seeds encoding the Stage A hypothesis (trim TREND / boost TA + oscillators),
# guaranteed-tested alongside LHS exploration. Base weights renorm to 100 in params_to_weights.
SEEDS = {
    "S1_trim_trend":      dict(W_TREND_BASE=13, W_BB_BASE=18, W_RSI_BASE=27, W_MACD_BASE=27,
                               W_STOCH_BASE=6, W_TA_BASE=11, W_TREND_SLOPE=7, W_RSI_SLOPE=-9,
                               W_MACD_SLOPE=-6, W_TA_SLOPE=8),
    "S2_trim_boost_ta":   dict(W_TREND_BASE=13, W_BB_BASE=16, W_RSI_BASE=26, W_MACD_BASE=26,
                               W_STOCH_BASE=7, W_TA_BASE=14, W_TREND_SLOPE=7, W_RSI_SLOPE=-9,
                               W_MACD_SLOPE=-6, W_TA_SLOPE=10),
    "S3_boost_osc":       dict(W_TREND_BASE=14, W_BB_BASE=14, W_RSI_BASE=28, W_MACD_BASE=27,
                               W_STOCH_BASE=9, W_TA_BASE=13, W_TREND_SLOPE=8, W_RSI_SLOPE=-7,
                               W_MACD_SLOPE=-5, W_TA_SLOPE=9),
    "S4_boost_stoch":     dict(W_TREND_BASE=16, W_BB_BASE=16, W_RSI_BASE=25, W_MACD_BASE=25,
                               W_STOCH_BASE=11, W_TA_BASE=11, W_TREND_SLOPE=9, W_RSI_SLOPE=-9,
                               W_MACD_SLOPE=-6, W_TA_SLOPE=7),
    "S5_mild_trim_trend": dict(W_TREND_BASE=15, W_BB_BASE=18, W_RSI_BASE=26, W_MACD_BASE=26,
                               W_STOCH_BASE=6, W_TA_BASE=10, W_TREND_SLOPE=8, W_RSI_SLOPE=-9,
                               W_MACD_SLOPE=-6, W_TA_SLOPE=7),
    "S6_trim_bb_too":     dict(W_TREND_BASE=14, W_BB_BASE=12, W_RSI_BASE=27, W_MACD_BASE=27,
                               W_STOCH_BASE=8, W_TA_BASE=14, W_TREND_SLOPE=7, W_RSI_SLOPE=-8,
                               W_MACD_SLOPE=-5, W_TA_SLOPE=9),
}
# bounds on the 6 BASE weights (pre-normalization) and 4 SLOPES
BASE_BOUNDS = {
    "W_TREND_BASE": (10.0, 26.0), "W_BB_BASE": (8.0, 26.0), "W_RSI_BASE": (16.0, 32.0),
    "W_MACD_BASE": (16.0, 32.0), "W_STOCH_BASE": (2.0, 12.0), "W_TA_BASE": (4.0, 16.0),
}
SLOPE_BOUNDS = {
    "W_TREND_SLOPE": (4.0, 16.0), "W_RSI_SLOPE": (-14.0, -2.0),
    "W_MACD_SLOPE": (-12.0, 0.0), "W_TA_SLOPE": (0.0, 12.0),
}
BASE_KEYS = list(BASE_BOUNDS)


def params_to_weights(p):
    """Renormalize the 6 base weights to sum 100 (redistribution); slopes pass through."""
    bsum = sum(p[k] for k in BASE_KEYS)
    scale = 100.0 / bsum if bsum > 0 else 1.0
    W = {k: p[k] * scale for k in BASE_KEYS}
    for k in SLOPE_BOUNDS:
        W[k] = p[k]
    return W


def objective_metrics(rs, base, W, label):
    r = rs.fast_evaluate(W, label=label)
    a = r["apexTP15_75plus"]
    sup = r["supply_75plus_per_yr"]
    edge_prem = (a * TP - (1 - a) * LOSS) if a is not None else -1.0
    r["obj"] = sup * edge_prem
    r["edge_prem"] = edge_prem
    # guards
    bad = []
    if r["unlabeled_frac"] > 0.03:
        bad.append("unlabeled %.3f" % r["unlabeled_frac"])
    if (r["apexTP15_85plus"] is not None and base["apexTP15_85plus"] is not None
            and r["apexTP15_85plus"] < base["apexTP15_85plus"] - 0.02):
        bad.append("85+ regress %.3f" % (r["apexTP15_85plus"] - base["apexTP15_85plus"]))
    if sup < 0.70 * base["supply_75plus_per_yr"]:
        bad.append("supply %.0f < .7*base" % sup)
    r["guard_fail"] = bad
    r["weights"] = {k: round(v, 2) for k, v in W.items()}
    return r


def main():
    rs = ReSim()
    rs.precompute()                      # one capturing pass; variants re-score cached inputs

    # inline fidelity gate: fast default-weight 75+ must reproduce DB v70 75+ (else abort).
    from database.trader_database import DB
    from resim import CUTOFF_ISO, _norm_date
    fast0 = rs.fast_overalls({})
    fast75 = {k for k, o in fast0.items() if o >= 75}
    db75 = {(s, _norm_date(d)) for s, d in DB.execute_sql(
        "SELECT symbol, date FROM scores WHERE version_id=70 AND overall>=75 "
        "AND date BETWEEN '2016-05-15' AND '%s'" % CUTOFF_ISO).fetchall()}
    inter = fast75 & db75
    jac = len(inter) / len(fast75 | db75) if (fast75 | db75) else 0.0
    rec = len(inter) / len(db75) if db75 else 0.0
    print("FIDELITY fast75=%d db75=%d jaccard=%.3f recall=%.3f (cont_lift/pit gap expected; "
          "smoke proved fast==simulate, and this gap is UNIFORM across variants so it does NOT "
          "affect RELATIVE reweight ranking — informational)" % (len(fast75), len(db75), jac, rec), flush=True)
    if jac < 0.50:
        print("ABORT: catastrophic fidelity (%.3f) — indicates a real harness bug, not the cont_lift gap" % jac, flush=True)
        return

    t0 = time.time()
    base = objective_metrics(rs, {"apexTP15_85plus": None, "supply_75plus_per_yr": 1e9}, {}, "v70-default")
    # set the real baseline references now that we have them
    base_ref = dict(base)
    dt = time.time() - t0
    print("BASELINE in %.1fs:" % dt, json.dumps({k: base[k] for k in
          ("apexTP15_75plus", "apexTP15_80_84", "apexTP15_85plus", "supply_75plus_per_yr",
           "optTP15_75plus", "obj", "n_75plus")}, default=str), flush=True)

    results = [base]
    with open(OUT, "w") as f:
        f.write(json.dumps(base, default=str) + "\n")

    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        HAVE_OPTUNA = True
    except Exception:
        HAVE_OPTUNA = False
    print("optuna:", HAVE_OPTUNA, " trials:", N_TRIALS, " est %.0f min" %
          (N_TRIALS * dt / 60.0), flush=True)

    n = [0]

    def sample_and_eval(p):
        n[0] += 1
        W = params_to_weights(p)
        r = objective_metrics(rs, base_ref, W, "t%03d" % n[0])
        with open(OUT, "a") as f:
            f.write(json.dumps(r, default=str) + "\n")
        results.append(r)
        gf = ("GUARD:" + ",".join(r["guard_fail"])) if r["guard_fail"] else "ok"
        print("%-18s obj=%.3f apex75=%s sup=%.0f opt75=%s 85+=%s %s" % (
            r.get("label", "t%03d" % n[0]), r["obj"], _p(r["apexTP15_75plus"]),
            r["supply_75plus_per_yr"], _p(r["optTP15_75plus"]), _p(r["apexTP15_85plus"]), gf), flush=True)
        return r

    # hand-designed seeds first (Stage A hypothesis), labeled by name
    for snm, sp in SEEDS.items():
        W = params_to_weights(sp)
        r = objective_metrics(rs, base_ref, W, snm)
        with open(OUT, "a") as f:
            f.write(json.dumps(r, default=str) + "\n")
        results.append(r)
        gf = ("GUARD:" + ",".join(r["guard_fail"])) if r["guard_fail"] else "ok"
        print("%-18s obj=%.3f apex75=%s sup=%.0f opt75=%s 85+=%s %s" % (
            snm, r["obj"], _p(r["apexTP15_75plus"]), r["supply_75plus_per_yr"],
            _p(r["optTP15_75plus"]), _p(r["apexTP15_85plus"]), gf), flush=True)

    if HAVE_OPTUNA:
        def obj(trial):
            p = {}
            for k, (lo, hi) in BASE_BOUNDS.items():
                p[k] = trial.suggest_float(k, lo, hi)
            for k, (lo, hi) in SLOPE_BOUNDS.items():
                p[k] = trial.suggest_float(k, lo, hi)
            r = sample_and_eval(p)
            if r["guard_fail"]:
                return -1e9
            return r["obj"]
        study = optuna.create_study(direction="maximize",
                                    sampler=optuna.samplers.TPESampler(seed=0, n_startup_trials=20))
        study.optimize(obj, n_trials=N_TRIALS)
    else:
        import numpy as np
        rng = np.random.default_rng(0)
        keys = list(BASE_BOUNDS) + list(SLOPE_BOUNDS)
        bounds = {**BASE_BOUNDS, **SLOPE_BOUNDS}
        # Latin hypercube
        N = N_TRIALS
        lh = (np.arange(N)[:, None] + rng.random((N, len(keys)))) / N
        for j in range(len(keys)):
            rng.shuffle(lh[:, j])
        for i in range(N):
            p = {k: bounds[k][0] + lh[i, j] * (bounds[k][1] - bounds[k][0])
                 for j, k in enumerate(keys)}
            sample_and_eval(p)

    valid = [r for r in results if not r["guard_fail"]]
    valid.sort(key=lambda r: -r["obj"])
    print("\n===== TOP 12 (guards passed) by obj =====")
    print("baseline: apex75=%s sup=%.0f obj=%.3f" %
          (_p(base["apexTP15_75plus"]), base["supply_75plus_per_yr"], base["obj"]))
    for r in valid[:12]:
        print("%-8s obj=%.3f (base %.3f)  apex75=%s sup=%.0f opt75=%s 85+=%s | %s" % (
            r["label"], r["obj"], base["obj"], _p(r["apexTP15_75plus"]),
            r["supply_75plus_per_yr"], _p(r["optTP15_75plus"]), _p(r["apexTP15_85plus"]),
            r["weights"]))
    with open(os.path.join(_HERE, "sweep_top.json"), "w") as f:
        json.dump({"baseline": base, "top": valid[:20]}, f, indent=2, default=str)


def _p(x):
    return "n/a" if x is None else "%.1f" % (x * 100)


if __name__ == "__main__":
    main()
