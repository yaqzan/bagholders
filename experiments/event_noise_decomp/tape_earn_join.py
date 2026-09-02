"""Stage-3 pre-screen v2: join REAL earnings events onto the v74 dd_ledger tape.

The tape's own `ern` column is inert under v74 (EARN_BOOST_ENABLED=False, so the
span tagger never populates), so we build the flag ourselves from
`earnings_dates.effective_date` (AMC-shifted), ghost-filtered.

Estimand (deliberately DIFFERENT from the closed peak_fakeout null):
  peak_fakeout tested earnings PROXIMITY as a per-trade fakeout discriminator at
  the score gate (z 1.84, null). Here we ask a portfolio question: do positions
  that SPAN an earnings event during the hold concentrate DRAWDOWN DOLLARS in the
  funded book -- the G21 low-EV-AND-high-dd_conc coincidence a Pareto sizing
  lever requires. Different stage, different estimand.

Read-only. ASCII output only.
"""
import sys
from collections import defaultdict
from datetime import timedelta

import polars as pl

sys.path.insert(0, ".")

from database.models.core import EarningsDate  # noqa: E402

DD_MIN = 0.13
WINDOWS = ["2021", "2022", "2023", "2024", "2025", "2020_crash", "5y"]
BATCH = 150


def load_earnings():
    """{symbol: sorted[effective_date]} -- ghost-filtered, AMC-shifted."""
    rows = list(
        EarningsDate.select(
            EarningsDate.symbol, EarningsDate.effective_date,
            EarningsDate.date, EarningsDate.reported_eps,
        ).tuples()
    )
    by_sym = defaultdict(list)
    for sym, eff, dt, rep in rows:
        if eff is None:
            continue
        by_sym[sym].append((eff, dt, rep))

    out = {}
    for sym, recs in by_sym.items():
        recs.sort(key=lambda r: r[0])
        reported = [r[0] for r in recs if r[2] is not None]
        keep = []
        for eff, _dt, rep in recs:
            if rep is None:
                # ghost: unreported row sitting within +/-30d of a reported sibling
                if any(abs((eff - rd).days) <= 30 for rd in reported):
                    continue
            keep.append(eff)
        out[sym] = sorted(set(keep))
    return out


def spans_earnings(earn_by_sym, sym, d_entry, d_exit):
    lst = earn_by_sym.get(sym)
    if not lst:
        return None                     # no calendar coverage -> unknown, not False
    for e in lst:
        if d_entry < e <= d_exit:
            return True
    return False


def annotate(df, earn_by_sym):
    ents = df["entry_date"].to_list()
    exts = df["exit_date"].to_list()
    syms = df["sym_id"].to_list()
    import datetime as _dt

    def _d(s):
        return _dt.date.fromisoformat(s) if isinstance(s, str) else s

    flags = []
    for s, a, b in zip(syms, ents, exts):
        flags.append(spans_earnings(earn_by_sym, s, _d(a), _d(b)))
    return df.with_columns(pl.Series("ern2", flags, dtype=pl.Boolean))


def cohort(df, name):
    df = df.with_columns(
        (pl.col("option_pnl") * pl.col("premium_cost")).alias("pnl_dollars")
    )
    known = df.filter(pl.col("ern2").is_not_null())
    if known.height == 0:
        print(f"  {name}: no earnings coverage")
        return
    tot = known.height
    loss_pool = known.filter(pl.col("pnl_dollars") < 0)["pnl_dollars"].sum()
    print(f"  {'scope':<14}{'ern':>6}{'N':>9}{'share':>8}"
          f"{'mean_pnl':>11}{'WR':>8}{'dd_conc':>9}")
    for val in [True, False]:
        sub = known.filter(pl.col("ern2") == val)
        if sub.height < 50:
            continue
        share = sub.height / tot
        sl = sub.filter(pl.col("pnl_dollars") < 0)["pnl_dollars"].sum()
        dd_conc = (sl / loss_pool) / share if loss_pool and share else float("nan")
        print(f"  {name:<14}{str(val):>6}{sub.height:>9}{share:>8.3f}"
              f"{sub['option_pnl'].mean():>11.4f}"
              f"{(sub['option_pnl'] > 0).mean():>8.3f}{dd_conc:>9.3f}")
    a = known.filter(pl.col("ern2"))["option_pnl"]
    b = known.filter(~pl.col("ern2"))["option_pnl"]
    if len(a) >= 50 and len(b) >= 50:
        print(f"  {'':14}{'GAP':>6}{'':9}{'':8}{a.mean() - b.mean():>11.4f}")


def main():
    print("Loading earnings calendar ...")
    earn = load_earnings()
    print(f"  symbols with earnings history: {len(earn)}")

    print("\n" + "=" * 78)
    print("EARNINGS-SPANNING POSITIONS -- v74 tape, portfolio DD-dollar view")
    print("=" * 78)

    for label in WINDOWS:
        try:
            df = pl.read_parquet(f".cache/dd_ledger/tape_{label}.parquet")
        except Exception as e:                          # noqa: BLE001
            print(f"[skip] {label}: {e}")
            continue
        df = df.filter(pl.col("side") == "call")
        df = annotate(df, earn)
        cov = df["ern2"].is_not_null().mean()
        print(f"\n[{label}]  calls={df.height}  earnings-coverage={cov:.3f}")
        cohort(df, "whole-tape")
        ddact = df.filter(pl.col("entry_dd") >= DD_MIN)
        if ddact.height > 500:
            cohort(ddact, f"dd>={DD_MIN}")

    print("\n" + "=" * 78)
    print("PER-YEAR SIGN STABILITY of the ern gap (G26 kill-test)")
    print("=" * 78)
    for label in ["2021", "2022", "2023", "2024", "2025"]:
        try:
            df = pl.read_parquet(f".cache/dd_ledger/tape_{label}.parquet")
        except Exception:                               # noqa: BLE001
            continue
        df = annotate(df.filter(pl.col("side") == "call"), earn)
        k = df.filter(pl.col("ern2").is_not_null())
        a = k.filter(pl.col("ern2"))["option_pnl"]
        b = k.filter(~pl.col("ern2"))["option_pnl"]
        if len(a) < 50 or len(b) < 50:
            continue
        print(f"  {label}: N_ern={len(a):>7} N_no={len(b):>7} "
              f"ern={a.mean():+.4f} base={b.mean():+.4f} gap={a.mean() - b.mean():+.4f}")


if __name__ == "__main__":
    sys.exit(main())
