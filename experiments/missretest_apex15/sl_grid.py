"""Per-trade TP x SL grid on the v70 ledger (W=15, the Apex day-15 HOLD horizon).

User hypothesis: losers blow past the -70% stop (median MAE 3.62sig) and 82% never approach the
+30% TP -> can we set a 'statistically-backed' tighter stop? This shows the per-trade landscape.

HARD CAVEAT printed below: per-trade EV here is a NOMINAL proxy (win=+tp_pnl, SL=-sl_pnl,
expire=-0.40). It IGNORES the two things that make the wide -70% HOLD win at the PORTFOLIO level:
(1) the DEAD-HOLD (deep losers are HELD, ~40% recover to -15% -> real loss << -70%), and
(2) capital-velocity / CONCURRENT-crash collapse under bounded-fill MC. So a tighter-SL 'win' here
is the documented velocity-vs-collapse TRAP, not alpha. Only a bounded-fill MC settles it.
"""
import os, sys
import numpy as np
import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "experiments", "component_reweight"))
from label import UP_GRID, DN_GRID  # noqa: E402

LEDGER = os.path.join(_ROOT, ".cache", "component_reweight", "ledger_v70_5y.parquet")
PM, DELTA = 1.82, 0.5
CONV = DELTA / PM            # sigma -> option pnl fraction (1.092sig -> 0.30)
W = 15
EXPIRE_PNL = -0.40          # nominal day-15 hard-sell (real is theta-priced, ~-0.37 mean)


def idx(grid, t):
    return int(np.clip(int(np.argmin(np.abs(grid - t))), 0, len(grid) - 1))


def main():
    df = pl.read_parquet(LEDGER).filter(pl.col("overall") >= 75)
    n_all = df.height
    tup = df["t_up"].to_list()
    tdn = df["t_dn"].to_list()
    fc = df["fwd_caldays"].to_numpy()
    tup = np.array(tup, dtype=np.int16)   # (N, |UP_GRID|)
    tdn = np.array(tdn, dtype=np.int16)

    TPs = [(0.73, "+20%"), (0.91, "+25%"), (1.092, "+30% (cur)"), (1.274, "+35%")]
    SLs = [(1.09, "-30%"), (1.46, "-40%"), (1.82, "-50%"), (2.18, "-60%"),
           (2.548, "-70% (cur)"), (3.27, "-90%")]

    print("v70 75+ CALL N=%d | W=%dd | per-trade NOMINAL EV (see caveat in header)\n" % (n_all, W))
    print("Row=TP target, Col=SL.  cell = WR%% / EV(nominal) / avg-bars-to-exit\n")
    hdr = "%-12s " % "TP\\SL" + " ".join("%-18s" % s[1] for s in SLs)
    print(hdr)
    for tp_sig, tp_lab in TPs:
        iu = idx(UP_GRID, tp_sig)
        tp_pnl = tp_sig * CONV
        ttp = tup[:, iu]
        cells = []
        for sl_sig, sl_lab in SLs:
            idn = idx(DN_GRID, sl_sig)
            tsl = tdn[:, idn]
            sl_pnl = sl_sig * CONV
            win = (ttp <= W) & (ttp < tsl)
            sl = (tsl <= W) & (tsl <= ttp)
            resolved = win | sl | (fc >= W)
            nr = resolved.sum()
            if nr == 0:
                cells.append("%-18s" % "-")
                continue
            wr = win[resolved].mean()
            exit_bar = np.where(win, ttp, np.where(sl, tsl, W)).astype(float)
            avg_bar = exit_bar[resolved].mean()
            pnl = np.where(win, tp_pnl, np.where(sl, -sl_pnl, EXPIRE_PNL))
            ev = pnl[resolved].mean()
            cells.append("%5.1f/%+.3f/%4.1f" % (wr * 100, ev, avg_bar))
        print("%-12s " % tp_lab + " ".join("%-18s" % c for c in cells))

    # focus: at the current +30% TP, the SL row, with velocity-adjusted EV (EV per bar held)
    print("\n=== SL tuning at fixed TP=+30% (current), velocity-adjusted ===")
    iu = idx(UP_GRID, 1.092); ttp = tup[:, iu]; tp_pnl = 1.092 * CONV
    print("%-12s %7s %8s %8s %9s %12s" % ("SL", "WR%", "EV_nom", "avgBar", "EV/bar", "%SL-hit"))
    for sl_sig, sl_lab in SLs:
        idn = idx(DN_GRID, sl_sig); tsl = tdn[:, idn]; sl_pnl = sl_sig * CONV
        win = (ttp <= W) & (ttp < tsl); sl = (tsl <= W) & (tsl <= ttp)
        resolved = win | sl | (fc >= W); nr = resolved.sum()
        wr = win[resolved].mean()
        exit_bar = np.where(win, ttp, np.where(sl, tsl, W)).astype(float)
        avg_bar = exit_bar[resolved].mean()
        pnl = np.where(win, tp_pnl, np.where(sl, -sl_pnl, EXPIRE_PNL))
        ev = pnl[resolved].mean()
        pct_sl = sl[resolved].mean() * 100
        print("%-12s %6.1f %+8.3f %8.1f %+9.4f %11.1f%%" %
              (sl_lab, wr * 100, ev, avg_bar, ev / avg_bar, pct_sl))
    print("\nNOTE: EV/bar RISING as SL tightens = the capital-velocity signal that ALWAYS appears")
    print("per-trade and ALWAYS reverses under bounded-fill MC (collapse 34-55%%). The dead-hold")
    print("(not modeled here) makes the real -70% loss << nominal. Verdict needs Stage-2/3 MC.")


if __name__ == "__main__":
    main()
