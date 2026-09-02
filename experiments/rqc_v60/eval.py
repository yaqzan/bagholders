"""RQC candidate evaluator — reuses the REAL stage1_growth_gate functions on a
re-tiered ledger, so a sweep verdict cannot drift from the production gate.

Builds the candidate's modified 75-79 CALL band (option-TP + generic WR, shrunk)
and its REAL recycle_coverage (per-window) from re-tiered daily 75+ supply — NO
FALLBACK_COVERAGE (the artifact that produced the false v63 SHIP). 80+ and put
bands are identical to v60 by construction (hard <80 ceiling).
"""
import os, sys, json, math
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "version_scorecard"))
import stage1_growth_gate as G   # noqa: E402

DEMAND = G.DEMAND_PER_DAY
SHRINK_K = 100.0
CALL_TP_REF, CALL_WR_REF = 0.66, 0.75
WINDOWS = {
    "2022": ("2022-01-01", "2022-12-31"),
    "2023": ("2023-01-01", "2023-12-31"),
    "2024": ("2024-01-01", "2024-12-31"),
    "2025": ("2025-01-01", "2025-12-31"),
    "dip":  ("2025-11-01", "2026-03-31"),
}


def shr(n, p, ref):
    return (n * p + SHRINK_K * ref) / (n + SHRINK_K) if (n + SHRINK_K) else ref


class Base:
    def __init__(self, tag="5y"):
        self.bb, _ = G.load_bands("v60")          # band dicts: n, wr(=wr_shrunk), tp(=tp_shrunk)
        self.b7579 = next(b for b in self.bb if b["side"] == "call" and b["band"] == "75-79")
        df = pl.read_parquet(".cache/rqc_v60/ledger_%s.parquet" % tag)
        self.df = df
        self.dates = np.array(df["date"].to_list())
        self.ov = df["overall"].to_numpy().astype(float)
        self.opt15 = df["opt15"].to_numpy().astype(float)
        self.gen15 = df["gen15"].to_numpy().astype(float)
        self.wmom = df["w_mom"].to_numpy().astype(float)   # weekly momentum (point-in-time safe)
        self._optok = ~np.isnan(self.opt15)
        self._genok = ~np.isnan(self.gen15)
        sup = json.load(open(".cache/rqc_v60/supply_v60_%s.json" % tag))
        self.c75 = {r["date"]: int(r["c75"]) for r in sup}
        self.p25 = {r["date"]: int(r["p25"]) for r in sup}
        self.all_dates = sorted(set(self.c75) | set(self.p25))
        self.base_sup = self.supply(delta=None)
        # anchor check: our v60 recompute must match the gate's stored v60 coverage
        gate_cov = G.load_supply("v60")["coverage"]
        self._anchor = (self.base_sup["coverage"], gate_cov)
        # Make the baseline 75-79 band window-consistent with candidates: rebuild it
        # from the ledger identity so identity dG == 0 (otherwise the pack's slightly
        # wider production window leaves a +0.8% offset). 80+/put bands are identical
        # baseline-vs-candidate so they cancel in dG regardless.
        self.bb = self.cand_bands(self.ov.copy())[0]

    def supply(self, delta):
        """delta: dict{date->int added to c75} or None for baseline."""
        def cov(date_lo=None, date_hi=None):
            tot, n = 0.0, 0
            for d in self.all_dates:
                if date_lo is not None and not (date_lo <= d <= date_hi):
                    continue
                t = self.c75[d] + (delta.get(d, 0) if delta else 0) + self.p25.get(d, 0)
                tot += min(t, DEMAND)
                n += 1
            return (tot / (DEMAND * n)) if n else None, n
        c5, n5 = cov()
        windows = {}
        for w, (lo, hi) in WINDOWS.items():
            cw, nw = cov(lo, hi)
            if nw >= 20:
                windows[w] = {"coverage": cw, "active_days": nw}
        return {"coverage": c5, "active_days": n5, "approx": False, "windows": windows}

    def cand_bands(self, onew):
        """Replace the 75-79 call band with the re-tiered cohort's stats; 80+/put
        bands unchanged. Enforces the <80 hard ceiling."""
        assert onew.max() <= 89.001, "ceiling violated: a peak reached %.1f" % onew.max()
        m = (onew >= 75) & (onew <= 79)
        n = int(m.sum())
        mo = m & self._optok
        mg = m & self._genok
        tp_raw = float(self.opt15[mo].mean()) if mo.sum() else self.b7579["tp"]
        wr_raw = float(self.gen15[mg].mean()) if mg.sum() else self.b7579["wr"]
        nb = dict(self.b7579)
        nb["n"] = float(n)
        nb["tp"] = shr(n, tp_raw, CALL_TP_REF)
        nb["wr"] = shr(n, wr_raw, CALL_WR_REF)
        bands = [dict(b) for b in self.bb if not (b["side"] == "call" and b["band"] == "75-79")]
        bands.append(nb)
        return bands, {"n": n, "tp_raw": tp_raw, "wr_raw": wr_raw,
                       "tp_shrunk": nb["tp"], "wr_raw_pct": round(wr_raw * 100, 2)}

    def cand_supply(self, onew):
        promo = (self.ov >= 70) & (self.ov <= 74) & (onew >= 75)
        demo = (self.ov >= 75) & (self.ov <= 79) & (onew < 75)
        delta = {}
        for i in np.where(promo)[0]:
            d = self.dates[i]; delta[d] = delta.get(d, 0) + 1
        for i in np.where(demo)[0]:
            d = self.dates[i]; delta[d] = delta.get(d, 0) - 1
        return self.supply(delta), int(promo.sum()), int(demo.sum())

    def evaluate(self, onew):
        cb, binfo = self.cand_bands(onew)
        cs, n_promo, n_demo = self.cand_supply(onew)
        stats = {}
        gout = {}
        for metric in ("tp", "wr"):
            rw = G.growth_windows(self.bb, cb, self.base_sup, cs, metric)
            valid = {w: r for w, r in rw.items() if r["dg"] is not None}
            binding = min(valid, key=lambda w: valid[w]["dg"]) if valid else "5y"
            b = rw[binding]
            stats[metric] = {"dg": b["dg"], "p05": b["p05"], "p95": b["p95"]}
            gout[metric] = {"dg_5y": rw["5y"]["dg"], "binding": binding,
                            "dg_bind": b["dg"], "p05": b["p05"], "p95": b["p95"]}
        issues = G.w4_check(self.bb, cb)
        v, why = G.verdict(stats, issues)
        return {
            "verdict": v, "why": why,
            "tp_dg5y": gout["tp"]["dg_5y"], "tp_bind": gout["tp"]["binding"],
            "tp_dgb": gout["tp"]["dg_bind"], "tp_p05": gout["tp"]["p05"], "tp_p95": gout["tp"]["p95"],
            "wr_dg5y": gout["wr"]["dg_5y"], "wr_bind": gout["wr"]["binding"],
            "wr_dgb": gout["wr"]["dg_bind"], "wr_p05": gout["wr"]["p05"], "wr_p95": gout["wr"]["p95"],
            "cov5y": cs["coverage"], "n_promo": n_promo, "n_demo": n_demo,
            "n7579": binfo["n"], "tp7579_raw": round(binfo["tp_raw"] * 100, 2),
            "wr7579_raw": binfo["wr_raw_pct"],
            "w4": [i for i in issues if i["level"] != "noise"],
        }


if __name__ == "__main__":
    b = Base()
    print("ANCHOR cov (ours vs gate):", round(b._anchor[0], 5), round(b._anchor[1], 5),
          "MATCH" if abs(b._anchor[0] - b._anchor[1]) < 0.01 else "MISMATCH!!")
    # identity: onew == ov -> v60 vs v60 -> dG ~ 0
    r = b.evaluate(b.ov.copy())
    print("IDENTITY verdict:", r["verdict"], "| tp_dg5y", round(r["tp_dg5y"] * 100, 3),
          "| n7579", r["n7579"], "| tp7579_raw", r["tp7579_raw"])
