"""RQC sweep — Fresh Weekly Momentum Admission Lift (promote-dominant).

Mechanism (point-in-time-safe; uses w_mom = completed-week momentum, NOT a
partial-week aggregate -> no look-ahead). Phase-aware / inverted-U bell on w_mom
(monotonic weekly boosts are a documented null trap):

  PROMOTE 70-74:  P = exp(-((w_mom - center)/width)^2)            (bell, in [0,1])
                  overall += KL * P * (TL - overall)               (TL<=78 -> <80)
  DEMOTE  75-79:  D = clip(-w_mom / DK, 0, 1)  when w_mom < 0
                  overall -= KD * D * (overall - TD)

Each variant is scored by the REAL stage1_growth_gate (via eval.Base.evaluate),
ranked SHIP > FLAG > BLOCK, then by binding option dG. Hard <80 ceiling.
"""
import os, sys, json, itertools
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval as E  # noqa: E402


def retier(b, p):
    ov = b.ov
    wm = b.wmom
    onew = ov.copy()
    valid = ~np.isnan(wm)
    # PROMOTE 70-74 (bell on w_mom)
    mp = (ov >= 70) & (ov <= 74) & valid
    P = np.where(mp, np.exp(-(((wm - p["center"]) / max(1e-6, p["width"])) ** 2)), 0.0)
    onew = np.where(mp, ov + p["KL"] * P * (p["TL"] - ov), onew)
    # DEMOTE 75-79 (w_mom<0)
    if p.get("KD", 0) > 0:
        md = (ov >= 75) & (ov <= 79) & valid & (wm < 0)
        D = np.clip(-wm / max(1e-6, p["DK"]), 0, 1)
        onew = np.where(md, onew - p["KD"] * D * (onew - p["TD"]), onew)
    onew = np.rint(onew)
    touched = (ov >= 70) & (ov <= 79)
    onew = np.where(touched, np.clip(onew, 0, 79), onew)   # <80 ceiling, leave 80-89
    return onew


def rank_key(r):
    order = {"SHIP": 0, "FLAG": 1, "BLOCK": 2}
    # primary: verdict; secondary: binding option dG (higher better); tertiary: generic
    return (order.get(r["res"]["verdict"], 3), -(r["res"]["tp_dgb"] or -9), -(r["res"]["wr_dgb"] or -9))


def main():
    b = E.Base()
    ident = b.evaluate(b.ov.copy())
    print("IDENTITY:", ident["verdict"], "tp_dg5y=%.3f%%" % (ident["tp_dg5y"] * 100),
          "tp_dgb=%.3f%%" % (ident["tp_dgb"] * 100), "n7579=%d" % ident["n7579"])

    # ---- Phase B: coarse grid (promote-only + a few demote) ----
    centers = [5.5, 6.0, 6.5, 7.0]
    widths = [1.5, 2.0, 3.0]
    KLs = [0.6, 0.8, 1.0]
    TLs = [76, 77, 78]
    demotes = [(0.0, 0, 0), (0.8, 73, 8.0)]   # (KD, TD, DK)

    variants = []
    for c, w, kl, tl, (kd, td, dk) in itertools.product(centers, widths, KLs, TLs, demotes):
        variants.append({"center": c, "width": w, "KL": kl, "TL": tl, "KD": kd, "TD": td, "DK": dk})

    results = []
    for i, p in enumerate(variants):
        onew = retier(b, p)
        try:
            res = b.evaluate(onew)
        except AssertionError as e:
            continue
        results.append({"p": p, "res": res})
        if (i + 1) % 30 == 0:
            print("  ...%d/%d" % (i + 1, len(variants)), flush=True)

    results.sort(key=rank_key)
    print("\n=== TOP 15 (verdict, binding option dG, generic dG, N7579, promo/demo, tp7579) ===")
    for r in results[:15]:
        p, x = r["p"], r["res"]
        print("  %-5s tpΔb=%+.2f%%(%s p05=%+.1f) wrΔb=%+.2f%%(%s) | n7579=%d promo=%d demo=%d tp79=%.2f cov=%.4f | "
              "c=%.1f w=%.1f KL=%.1f TL=%d KD=%.1f" % (
              x["verdict"], (x["tp_dgb"] or 0) * 100, x["tp_bind"], (x["tp_p05"] or 0) * 100,
              (x["wr_dgb"] or 0) * 100, x["wr_bind"], x["n7579"], x["n_promo"], x["n_demo"],
              x["tp7579_raw"], x["cov5y"], p["center"], p["width"], p["KL"], p["TL"], p["KD"]))

    ships = [r for r in results if r["res"]["verdict"] == "SHIP"]
    print("\nSHIP variants: %d / %d" % (len(ships), len(results)))
    with open(".cache/rqc_v60/sweep_phaseB.json", "w") as f:
        json.dump([{"p": r["p"], "res": {k: v for k, v in r["res"].items() if k != "w4"},
                    "verdict": r["res"]["verdict"]} for r in results], f, indent=1, default=str)
    print("WROTE .cache/rqc_v60/sweep_phaseB.json")


if __name__ == "__main__":
    main()
