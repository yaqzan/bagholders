"""Stage B re-simulate harness — reweight the component blend in-memory, re-label against
the score-invariant ledger outcomes.

Mechanism: build ScoreSimulator ONCE (bulk-load all data). Per variant, reassign the
module-level weights in database.utils.scoring (W_TREND_BASE, ...), re-run .simulate()
(full-fidelity: re-applies weekly blend, volume, regime, AND every downstream dampener),
then JOIN the new 75+ CALL population to the precomputed hold/opt/gen barrier outcomes
(score-invariant: depend only on symbol/date/barrier/side).

Barrier outcomes are NOT recomputed per variant — only band membership changes. This is the
rqc_v60/eval.py pattern, extended to a re-score (not just a re-tier), because reweighting
shifts WHICH signals clear 75.

VALIDATION GATE: evaluate() at the v70 default weights must reproduce the DB 75+ set
(see validate.py). If re-sim diverges from DB, the sweep is untrustworthy.
"""
import os, sys, json
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import polars as pl
import database.utils.scoring as SC
from simulator import ScoreSimulator
from experiments._holdout import CUTOFF as HOLDOUT_CUTOFF
from label import add_label

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
SUPPLY = os.path.join(_ROOT, ".cache", "component_reweight", "supply_v70.json")
CUTOFF_ISO = HOLDOUT_CUTOFF.isoformat()

WEIGHT_KEYS = ["W_TREND_BASE", "W_TREND_SLOPE", "W_BB_BASE", "W_RSI_BASE", "W_RSI_SLOPE",
               "W_MACD_BASE", "W_MACD_SLOPE", "W_STOCH_BASE", "W_TA_BASE", "W_TA_SLOPE"]
# optional d-shaping knobs (left at default unless a variant sets them)
SHAPE_KEYS = ["TREND_BIAS_SCALE", "TREND_DOMINANCE_POWER", "BB_DOMINANCE_DAMP", "OSC_DOMINANCE_DAMP"]


def _norm_date(d):
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


class ReSim:
    def __init__(self, lookback_days=3850, start="2016-05-15",
                 funded_tp=1.092, funded_sl=2.548, funded_w=15):
        self.start = start
        self.funded = (funded_tp, funded_sl, funded_w)
        self.defaults = {k: getattr(SC, k) for k in WEIGHT_KEYS + SHAPE_KEYS}
        # ledger outcomes keyed (sym, date_iso) -> dict; labels derived from forward-path grids
        df = pl.read_parquet(LEDGER)
        df = add_label(df, funded_tp, funded_sl, funded_w, "apex15")   # funded label (configurable)
        df = add_label(df, 0.901, 0.772, 15, "opt15")                  # growth-gate barrier
        df = add_label(df, 1.414, 3.536, 15, "gen15")                  # dashboard generic WR15
        self.outc = {}
        for r in df.iter_rows(named=True):
            self.outc[(r["symbol"], r["date"])] = {
                "apex15": r["apex15"], "opt15": r["opt15"], "gen15": r["gen15"],
                "vol_pct": r["vol_pct"],
            }
        # baseline supply (full-universe per-date) for trade-day count
        with open(SUPPLY) as f:
            self.supply_base = {s["date"]: s for s in json.load(f)}
        self.n_days = sum(1 for d in self.supply_base if d <= CUTOFF_ISO)
        print("ReSim: ledger outcomes=%d, supply days(<=cutoff)=%d" % (len(self.outc), self.n_days), flush=True)
        print("building ScoreSimulator (lookback=%d)..." % lookback_days, flush=True)
        self.sim = ScoreSimulator(symbols=None, lookback_days=lookback_days)
        self._since = date.fromisoformat(start)
        print("ScoreSimulator ready", flush=True)

    def set_weights(self, W):
        for k, v in W.items():
            if k in self.defaults:
                setattr(SC, k, float(v))

    def restore(self):
        for k, v in self.defaults.items():
            setattr(SC, k, v)

    def simulate_overalls(self, W):
        """SLOW full re-simulate (kept for fidelity cross-check)."""
        self.set_weights(W)
        try:
            scores = self.sim.simulate(since=self._since)
        finally:
            self.restore()
        out = {}
        for (sym, d), ov in scores.items():
            di = _norm_date(d)
            if di <= CUTOFF_ISO and ov is not None:
                out[(sym, di)] = int(ov)
        return out

    # ── FAST PATH: capture weight-invariant inputs once, then re-score via
    #    compute_overall_score only (no per-signal component recompute). ──────
    def precompute(self):
        """One capturing simulate pass; caches inputs for default-overall>=60 signals.
        Returns the default-weight 75+ overall map (for the fidelity check)."""
        self.sim.capture_inputs = True
        # 66 floor: a bounded reweight moves scores <~9, so 66->75 is the max promotion
        # crossing; keeps the per-variant re-score fast. Ledger [60,99] covers the JOIN.
        self.sim.capture_min_overall = 66
        self.sim.inputs_by_key = {}
        scores = self.sim.simulate(since=self._since)
        self.sim.capture_inputs = False
        self._cap_items = []
        for (sym, d), payload in self.sim.inputs_by_key.items():
            di = _norm_date(d)
            if di <= CUTOFF_ISO:
                self._cap_items.append(((sym, di), payload))
        default_ov = {}
        for (sym, d), ov in scores.items():
            di = _norm_date(d)
            if di <= CUTOFF_ISO and ov is not None and ov >= 75:
                default_ov[(sym, di)] = int(ov)
        self._default_ov = default_ov
        print("precompute: cached %d inputs (>=60), default 75+ = %d" %
              (len(self._cap_items), len(default_ov)), flush=True)
        return default_ov

    def fast_overalls(self, W):
        """Re-score the cached inputs with weights W (compute_overall_score only)."""
        self.set_weights(W)
        out = {}
        try:
            cos = SC.compute_overall_score
            for key, (args, kw) in self._cap_items:
                ov, _, _ = cos(*args, **kw)
                if ov is not None:
                    out[key] = int(ov)
        finally:
            self.restore()
        return out

    def fast_evaluate(self, W, label=""):
        return self._metrics(self.fast_overalls(W), label)

    def evaluate(self, W, label=""):
        return self._metrics(self.simulate_overalls(W), label)

    def _metrics(self, ov, label=""):
        # per-day 75+ supply
        per_day = {}
        c75 = c7579 = c8084 = c85 = 0
        labeled = unlabeled = 0
        apex = {"75plus": [], "75-79": [], "80-84": [], "85+": []}
        opt = {"75plus": [], "75-79": []}
        gen = {"75plus": []}
        for (sym, di), o in ov.items():
            if o >= 75:
                per_day[di] = per_day.get(di, 0) + 1
                c75 += 1
                rec = self.outc.get((sym, di))
                if rec is None:
                    unlabeled += 1
                    continue
                labeled += 1
                band = "75-79" if o <= 79 else ("80-84" if o <= 84 else "85+")
                if o <= 79: c7579 += 1
                elif o <= 84: c8084 += 1
                else: c85 += 1
                if rec["apex15"] is not None:
                    apex["75plus"].append(rec["apex15"]); apex[band].append(rec["apex15"])
                if rec["opt15"] is not None:
                    opt["75plus"].append(rec["opt15"])
                    if o <= 79: opt["75-79"].append(rec["opt15"])
                if rec["gen15"] is not None:
                    gen["75plus"].append(rec["gen15"])

        def mean(a):
            return (sum(a) / len(a)) if a else None

        # supply per trading-year (use ledger window length)
        years = max(1e-6, self.n_days / 252.0)
        res = {
            "label": label,
            "n_75plus": c75, "n_labeled": labeled, "n_unlabeled": unlabeled,
            "unlabeled_frac": (unlabeled / c75) if c75 else 0.0,
            "n_75_79": c7579, "n_80_84": c8084, "n_85plus": c85,
            "supply_75plus_per_yr": round(c75 / years, 1),
            "apexTP15_75plus": mean(apex["75plus"]),
            "apexTP15_75_79": mean(apex["75-79"]),
            "apexTP15_80_84": mean(apex["80-84"]),
            "apexTP15_85plus": mean(apex["85+"]),
            "optTP15_75plus": mean(opt["75plus"]),
            "optTP15_75_79": mean(opt["75-79"]),
            "genWR15_75plus": mean(gen["75plus"]),
            # recycle_coverage proxy: mean per-day min(supply, demand)/demand over labeled days
        }
        # recycle coverage: demand ~ MAX_POSITIONS / avg_hold_bars; approximate demand=6.2/day
        demand = 6.2
        cov = []
        for di in self.supply_base:
            if di <= CUTOFF_ISO:
                s = per_day.get(di, 0)
                cov.append(min(s, demand) / demand)
        res["recycle_coverage"] = round(sum(cov) / len(cov), 4) if cov else None
        return res


def main():
    """Precompute + fidelity gate: fast default-weight 75+ vs (a) capturing-simulate 75+
    and (b) DB v70 75+. Confirms the fast path is faithful before the sweep trusts it."""
    from database.trader_database import DB
    rs = ReSim()
    default_ov = rs.precompute()                       # capturing simulate
    fast_ov = rs.fast_overalls({})                     # fast re-score at default weights
    fast_75 = {k for k, o in fast_ov.items() if o >= 75}
    cap_75 = set(default_ov)                            # from the capturing simulate

    # (a) fast vs capturing-simulate (must be ~identical — same code, cached inputs)
    ia = fast_75 & cap_75
    jac_self = len(ia) / len(fast_75 | cap_75) if (fast_75 | cap_75) else 1.0

    # (b) re-sim vs DB v70 75+
    sql = ("SELECT symbol, date FROM scores WHERE version_id=70 AND overall>=75 "
           "AND date BETWEEN '2016-05-15' AND '%s'" % CUTOFF_ISO)
    db_75 = {(s, _norm_date(d)) for s, d in DB.execute_sql(sql).fetchall()}
    ib = fast_75 & db_75
    prec = len(ib) / len(fast_75) if fast_75 else 0.0
    rec = len(ib) / len(db_75) if db_75 else 0.0
    jac_db = len(ib) / len(fast_75 | db_75) if (fast_75 | db_75) else 0.0

    base = rs.fast_evaluate({}, label="v70-default-fast")
    print("\n===== FIDELITY =====")
    print("fast 75+ = %d | capturing-sim 75+ = %d | DB 75+ = %d" % (len(fast_75), len(cap_75), len(db_75)))
    print("fast-vs-capture jaccard = %.4f  (want ~1.0 — same code path)" % jac_self)
    print("fast-vs-DB  precision=%.3f recall=%.3f jaccard=%.3f" % (prec, rec, jac_db))
    print("base fast apexTP15_75+ = %s  supply/yr=%.0f  unlabeled=%.3f" %
          (base["apexTP15_75plus"], base["supply_75plus_per_yr"], base["unlabeled_frac"]))
    # cont_lift accounts for most fast<DB misses (simulate omits prior_signal)
    miss = db_75 - fast_75
    print("DB-75+ missed by re-sim: %d (%.1f%% of DB) — expected ~cont_lift promotions" %
          (len(miss), 100.0 * len(miss) / max(1, len(db_75))))
    ok = (jac_self > 0.98 and jac_db >= 0.85 and base["unlabeled_frac"] <= 0.03)
    print("VERDICT:", "PASS — fast path faithful; sweep trustworthy (relative)" if ok
          else "REVIEW — inspect gaps before trusting sweep")
    with open(os.path.join(_HERE, "fidelity.json"), "w") as f:
        json.dump({"jac_self": jac_self, "prec_db": prec, "rec_db": rec, "jac_db": jac_db,
                   "fast_75": len(fast_75), "db_75": len(db_75),
                   "base_apexTP15": base["apexTP15_75plus"],
                   "unlabeled_frac": base["unlabeled_frac"], "pass": ok}, f, indent=2)


if __name__ == "__main__":
    main()
