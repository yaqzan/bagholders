"""Stage-3 pre-screen v3 -- the DECONFOUNDED earnings-span test.

WHY v3 EXISTS (the trap v2 walked into, recorded so nobody repeats it):
v2 flagged a position as earnings-spanning if an event fell in
(entry_date, REALIZED exit_date]. `exit_date` is an OUTCOME: TP winners exit in
~4 days, dead bags run to the ~15-day expiry. So a long realized window is
MECHANICALLY more likely to contain an earnings date AND mechanically more likely
to be a loser. v2's -0.38 EV gap is therefore confounded by hold duration and is
NOT evidence of anything on its own.

v3 fixes it two ways at once:
  (A) EX-ANTE flag `ern_nom`: an earnings effective_date in
      (entry_date, entry_date + NOMINAL_CAL_DAYS]. Uses ONLY information available
      at entry -- this is the only form a sizing lever could ever key on.
  (B) DURATION CONTROL: report the ex-ante gap stratified by realized hold length,
      so a residual effect can be distinguished from the duration tautology.

If the ex-ante gap collapses toward zero, v2 was pure confound and the axis is
closed. If it survives inside duration strata, it is a real candidate.

Read-only. ASCII output only.
"""
import sys
import datetime as dt
from collections import defaultdict

import polars as pl

sys.path.insert(0, ".")

from database.models.core import EarningsDate  # noqa: E402

NOMINAL_CAL_DAYS = 20      # the apex15 barrier's MAXW_CALDAYS -- the nominal hold
DD_MIN = 0.13
YEARS = ["2021", "2022", "2023", "2024", "2025"]


def load_earnings():
    rows = list(
        EarningsDate.select(
            EarningsDate.symbol, EarningsDate.effective_date,
            EarningsDate.reported_eps,
        ).tuples()
    )
    by_sym = defaultdict(list)
    for sym, eff, rep in rows:
        if eff is not None:
            by_sym[sym].append((eff, rep))
    out = {}
    for sym, recs in by_sym.items():
        recs.sort(key=lambda r: r[0])
        reported = [r[0] for r in recs if r[1] is not None]
        keep = [eff for eff, rep in recs
                if rep is not None
                or not any(abs((eff - rd).days) <= 30 for rd in reported)]
        out[sym] = sorted(set(keep))
    return out


def _d(s):
    return dt.date.fromisoformat(s) if isinstance(s, str) else s


def annotate(df, earn):
    syms = df["sym_id"].to_list()
    ents = [_d(x) for x in df["entry_date"].to_list()]
    exts = [_d(x) for x in df["exit_date"].to_list()]

    nom, real, hold = [], [], []
    for s, a, b in zip(syms, ents, exts):
        lst = earn.get(s)
        hold.append((b - a).days)
        if not lst:
            nom.append(None)
            real.append(None)
            continue
        nom_end = a + dt.timedelta(days=NOMINAL_CAL_DAYS)
        nom.append(any(a < e <= nom_end for e in lst))
        real.append(any(a < e <= b for e in lst))
    return df.with_columns([
        pl.Series("ern_nom", nom, dtype=pl.Boolean),
        pl.Series("ern_real", real, dtype=pl.Boolean),
        pl.Series("hold_days", hold, dtype=pl.Int32),
    ])


def gap(df, flag):
    k = df.filter(pl.col(flag).is_not_null())
    a = k.filter(pl.col(flag))["option_pnl"]
    b = k.filter(~pl.col(flag))["option_pnl"]
    if len(a) < 50 or len(b) < 50:
        return None
    return dict(n_a=len(a), n_b=len(b), share=len(a) / k.height,
                ev_a=a.mean(), ev_b=b.mean(), gap=a.mean() - b.mean(),
                wr_a=(a > 0).mean(), wr_b=(b > 0).mean())


def show(tag, g):
    if g is None:
        print(f"  {tag:<26} (insufficient N)")
        return
    print(f"  {tag:<26} N={g['n_a']:>7} share={g['share']:.3f}  "
          f"ev={g['ev_a']:+.4f} base={g['ev_b']:+.4f} GAP={g['gap']:+.4f}  "
          f"WR={g['wr_a']:.3f}/{g['wr_b']:.3f}")


def main():
    earn = load_earnings()
    print(f"earnings symbols: {len(earn)}   nominal window: {NOMINAL_CAL_DAYS} cal days")

    df = pl.read_parquet(".cache/dd_ledger/tape_5y.parquet").filter(
        pl.col("side") == "call")
    df = annotate(df, earn)

    print("\n" + "=" * 88)
    print("A. EX-ANTE vs EX-POST flag, 5y pooled  (the deconfounding contrast)")
    print("=" * 88)
    show("ex-post (realized window)", gap(df, "ern_real"))
    show("EX-ANTE (nominal window)", gap(df, "ern_nom"))

    print("\n" + "=" * 88)
    print("B. DURATION CONTROL -- ex-ante gap within realized-hold strata")
    print("=" * 88)
    print("   (if the ex-ante gap survives inside every stratum, it is not the")
    print("    hold-duration tautology)")
    for lo, hi in [(0, 3), (3, 6), (6, 10), (10, 14), (14, 99)]:
        sub = df.filter((pl.col("hold_days") >= lo) & (pl.col("hold_days") < hi))
        if sub.height < 2000:
            continue
        g = gap(sub, "ern_nom")
        show(f"hold {lo:>2}-{hi:<2}d  (N={sub.height})", g)

    print("\n" + "=" * 88)
    print("C. HOLD-DURATION composition -- proof the confound exists")
    print("=" * 88)
    k = df.filter(pl.col("ern_real").is_not_null())
    for val, lab in [(True, "ern_real=True"), (False, "ern_real=False")]:
        s = k.filter(pl.col("ern_real") == val)
        print(f"  {lab:<18} mean_hold={s['hold_days'].mean():6.2f}d  "
              f"median={s['hold_days'].median():5.1f}d  N={s.height}")
    k2 = df.filter(pl.col("ern_nom").is_not_null())
    for val, lab in [(True, "ern_nom=True"), (False, "ern_nom=False")]:
        s = k2.filter(pl.col("ern_nom") == val)
        print(f"  {lab:<18} mean_hold={s['hold_days'].mean():6.2f}d  "
              f"median={s['hold_days'].median():5.1f}d  N={s.height}")

    print("\n" + "=" * 88)
    print("D. EX-ANTE gap per year (G26 sign-stability kill-test)")
    print("=" * 88)
    for y in YEARS:
        try:
            d = pl.read_parquet(f".cache/dd_ledger/tape_{y}.parquet").filter(
                pl.col("side") == "call")
        except Exception:                                   # noqa: BLE001
            continue
        d = annotate(d, earn)
        show(y, gap(d, "ern_nom"))

    print("\n" + "=" * 88)
    print("E. EX-ANTE gap in the DD-ACTIVE subset (where a DD-gated lever acts)")
    print("=" * 88)
    dd = df.filter(pl.col("entry_dd") >= DD_MIN)
    show(f"dd>={DD_MIN} (N={dd.height})", gap(dd, "ern_nom"))
    calm = df.filter(pl.col("entry_dd") < DD_MIN)
    show(f"dd<{DD_MIN}  (N={calm.height})", gap(calm, "ern_nom"))

    print("\n" + "=" * 88)
    print("F. dd-dollar concentration of the EX-ANTE cohort")
    print("=" * 88)
    w = df.filter(pl.col("ern_nom").is_not_null()).with_columns(
        (pl.col("option_pnl") * pl.col("premium_cost")).alias("pnl_dollars"))
    pool = w.filter(pl.col("pnl_dollars") < 0)["pnl_dollars"].sum()
    for val in [True, False]:
        s = w.filter(pl.col("ern_nom") == val)
        share = s.height / w.height
        sl = s.filter(pl.col("pnl_dollars") < 0)["pnl_dollars"].sum()
        print(f"  ern_nom={str(val):<6} share={share:.3f}  "
              f"loss_share={sl / pool:.3f}  dd_conc={(sl / pool) / share:.3f}")


if __name__ == "__main__":
    sys.exit(main())
