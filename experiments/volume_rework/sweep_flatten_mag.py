"""flatten-mag-scaling sweep (Stage-1 simplification screen) — ReSim fast-path, vol_mult OVERRIDE.

The captured compute_overall_score kwargs already contain vol_mult / vol_sig / vol_mag, so each
variant just OVERRIDES vol_mult (flatten the anti-predictive mag tilt / THIN_AIR-off / cap high-mag)
and re-scores — no scoring-code edit needed for the screen. Measures funded (apex15 by tier, opt15
75+/75-79) AND generic (gen15 75+) WR vs baseline + supply. Simplification gate = flat-or-up funded
WR AND generic non-regression.

5y window (lookback 1825) to keep the precompute tractable. Heavy precompute -> RUN VIA trader queue.
"""
import os, sys, json
os.environ.setdefault("PYTHONIOENCODING", "utf-8"); os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for p in (_ROOT, os.path.join(_ROOT, "experiments", "component_reweight")):
    if p not in sys.path:
        sys.path.insert(0, p)
import database.utils.scoring as SC
from resim import ReSim

# vol_mult OVERRIDE transforms: (vol_sig, vol_mag, orig_mult) -> new_mult
VARIANTS = {
    "baseline":        lambda vs, vm, m: m,
    "conv_noamp":      lambda vs, vm, m: 1.0 if vs == "CONVICTION" else m,
    "conv_flat10":     lambda vs, vm, m: 1.10 if vs == "CONVICTION" else m,
    "conv_flat20":     lambda vs, vm, m: 1.20 if vs == "CONVICTION" else m,
    "conv_cap_q3":     lambda vs, vm, m: (min(m, 1.15) if vs == "CONVICTION" else m),  # kill q4 over-amp
    "thinair_off":     lambda vs, vm, m: 1.0 if vs == "THIN_AIR" else m,
    "flat10_thinoff":  lambda vs, vm, m: (1.10 if vs == "CONVICTION" else (1.0 if vs == "THIN_AIR" else m)),
    "capq3_thinoff":   lambda vs, vm, m: (min(m, 1.15) if vs == "CONVICTION" else (1.0 if vs == "THIN_AIR" else m)),
}


def rescore(rs, fn):
    out = {}
    cos = SC.compute_overall_score
    for key, (args, kw) in rs._cap_items:
        kw2 = dict(kw)
        vs = kw2.get("vol_sig", "NEUTRAL"); vm = kw2.get("vol_mag", 0.0) or 0.0; m = kw2.get("vol_mult", 1.0)
        kw2["vol_mult"] = fn(vs, float(vm), float(m))
        ov, _, _ = cos(*args, **kw2)
        if ov is not None:
            out[key] = int(ov)
    return out


def _f(v):
    return ("%6.1f" % v) if isinstance(v, (int, float)) else "   -  "


def _d(m, b, k):
    a, bb = m.get(k), b.get(k)
    return (a - bb) if isinstance(a, (int, float)) and isinstance(bb, (int, float)) else None


def _ds(x):
    return ("%+6.2f" % x) if isinstance(x, (int, float)) else "   -  "


def main():
    rs = ReSim(lookback_days=1825, start="2021-05-15")
    rs.precompute()
    rows = {name: rs._metrics(rescore(rs, fn), name) for name, fn in VARIANTS.items()}
    # save FIRST so any formatting issue can never lose the computed results again
    with open(os.path.join(_HERE, "sweep_flatten_results.json"), "w") as f:
        json.dump(rows, f, indent=2, default=str)
    b = rows["baseline"]
    print("\n===== flatten-mag-scaling sweep (5y ReSim, vol_mult override) =====")
    print("variant           75+N sup/yr  recyc | apexTP 75+/75-79/80-84/85+ |  opt 75+/75-79 |  gen75 | dApex75 dOpt75 dGen75 dSup%")
    for name, m in rows.items():
        dsup = 100.0 * (m.get("n_75plus", 0) - b.get("n_75plus", 0)) / max(1, b.get("n_75plus", 1))
        print("%-16s %5d %6.1f %s | %s %s %s %s | %s %s | %s | %s %s %s %+6.1f" % (
            name, m.get("n_75plus", 0), m.get("supply_75plus_per_yr", 0) or 0.0, _f(m.get("recycle_coverage")),
            _f(m.get("apexTP15_75plus")), _f(m.get("apexTP15_75_79")), _f(m.get("apexTP15_80_84")), _f(m.get("apexTP15_85plus")),
            _f(m.get("optTP15_75plus")), _f(m.get("optTP15_75_79")),
            _f(m.get("genWR15_75plus")),
            _ds(_d(m, b, "apexTP15_75plus")), _ds(_d(m, b, "optTP15_75plus")), _ds(_d(m, b, "genWR15_75plus")), dsup))
    print("\nGATE (simplification): dApex75 & dOpt75 >= 0 (flat-or-up funded) AND dGen75 >= ~-1.0 (generic non-regression).")
    print("wrote sweep_flatten_results.json")


if __name__ == "__main__":
    main()
