"""Wave/Cycle Mine -- prep_mirror.py

Materializes .cache/wave_cycle_mine/mirror_2016_2020.parquet: the W-family
"era-independence" replication slice from PREREGISTRATION.md leg 5 -- v74
75+ CALL rows in 2016-01-01..2020-12-31 (a genuinely different market era
than the 2021-2026 main substrate), joined to the 30dte_apex barrier-cache
win flag.

Run ONCE, foreground (expected seconds-to-a-minute -- a single indexed
MySQL query on (version_id, date) + a DuckDB cache join, both bounded).

Usage:
    PYTHONIOENCODING=utf-8 python -u experiments/wave_cycle_mine/prep_mirror.py

Barrier convention: follows experiments/trend_ma_lattice/mine.py's own
build_replication_surface EXACTLY (same barrier_set='30dte_apex', same "15d"
period read off peaks_to_swing_results) -- that function is precisely what
already produces this study's base-frame `repl_win`/`repl_ev` columns (the
S-family leg-5 replication surface), so this era-mirror is apples-to-apples
with the existing full-universe replication check: both are the "30dte_apex
@ 15d (sqrt(15/30)-scaled)" breadth mirror, just sliced to a different date
range. See that module's own comment for why 15d (scaled) rather than the
funded ledger's own fixed-threshold (1.092/2.548 @ 15 calendar days, no
scaling) convention -- a documented, accepted proxy, not a bug. The
PREREGISTRATION left the exact barrier period unspecified for this new
mirror; this choice was made for internal consistency with the study's
existing S-family replication leg (both now use the identical convention).

W-family features are NOT computed here -- mine.py owns that (loads this
parquet and calls calendar_features.add_w_columns on it with the SAME
WCalendar instance used for the main frame, per that module's docstring on
why it's a real shared import between the two scripts). This script only
does the DB + barrier-cache join.
"""
from __future__ import annotations

import os
import sys
import time
from collections import namedtuple
from datetime import date

import polars as pl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import calendar_features  # noqa: E402  (local, shared with mine.py)

OUT_DIR = os.path.join(_ROOT, ".cache", "wave_cycle_mine")
os.makedirs(OUT_DIR, exist_ok=True)
OUT_PATH = os.path.join(OUT_DIR, "mirror_2016_2020.parquet")

VERSION_ID = 74
EXPECTED_COMMIT = "f9fb7b934"
ERA_START = date(2016, 1, 1)
ERA_END = date(2020, 12, 31)
BARRIER_SET = "30dte_apex"
BARRIER_PERIOD = "15d"     # matches trend_ma_lattice's repl_win convention exactly (see docstring)

# database/barrier_cache.py apex_ev_of_parquet.py convention (also hardcoded
# identically in experiments/trend_ma_lattice/mine.py) -- +TP/-SL/-expire.
EV_WIN, EV_STOP, EV_EXPIRE = 0.30, -0.70, -0.40


def verify_version():
    """PREREGISTRATION.md: 'scores table, version_id = 74, verified'."""
    from database.models.core import AlgorithmVersion
    av = AlgorithmVersion.get_by_id(VERSION_ID)
    print(f"[version] id={av.id} git_commit={av.git_commit} label={av.production_label}", flush=True)
    if av.git_commit != EXPECTED_COMMIT:
        print(f"[version] WARNING: git_commit {av.git_commit!r} != expected v74 commit "
              f"{EXPECTED_COMMIT!r} -- proceeding with version_id={VERSION_ID} regardless "
              f"(the prereg locks the ID, not 'whatever is currently active').", flush=True)
    else:
        print(f"[version] verified: id={VERSION_ID} matches expected v74 commit {EXPECTED_COMMIT}",
              flush=True)
    return av


def fetch_era_scores():
    """(symbol, date, overall) tuples for version_id=74, overall>=75, date in
    [ERA_START, ERA_END]. Peewee .tuples() on a DeferredForeignKey field
    returns the raw stored value (the ticker string, since Stock's PK IS
    `symbol`) -- never touches a row.symbol FK attribute in a loop (the
    documented FK-per-row trap; see prep_lookups.py's identical pattern on
    EarningsDate.symbol)."""
    from database.models.core import Score

    t0 = time.time()
    q = (Score
         .select(Score.symbol, Score.date, Score.overall)
         .where((Score.version == VERSION_ID) & (Score.overall >= 75) &
                (Score.date >= ERA_START) & (Score.date <= ERA_END))
         .tuples())
    rows = list(q)
    print(f"[scores] {len(rows)} rows (version_id={VERSION_ID}, overall>=75, "
          f"{ERA_START.isoformat()}..{ERA_END.isoformat()}) in {time.time()-t0:.1f}s", flush=True)
    return rows


def join_barrier_outcomes(score_rows):
    """Bulk-join to the 30dte_apex DuckDB barrier-cache mirror via
    database.barrier_cache.peaks_to_swing_results (same call
    trend_ma_lattice/mine.py's build_replication_surface makes -- see module
    docstring)."""
    from database.barrier_cache import peaks_to_swing_results

    Peak = namedtuple("Peak", "symbol_id date overall")
    peaks = [Peak(sym, d, int(overall)) for sym, d, overall in score_rows]
    empty_schema = {"symbol": pl.Utf8, "date": pl.Date, "win": pl.Int64, "ev": pl.Float64}
    if not peaks:
        return pl.DataFrame({"symbol": [], "date": [], "win": [], "ev": []}, schema=empty_schema)

    t0 = time.time()
    results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set=BARRIER_SET)
    ev_map = {"win": EV_WIN, "stop": EV_STOP, "expire": EV_EXPIRE}
    rows = []
    for r in results:
        sw = (r.get("swing") or {}).get(BARRIER_PERIOD)
        if not sw:
            continue
        kind = sw.get("result")
        if kind not in ev_map:
            continue
        rows.append({"symbol": r["symbol"], "date": r["date"],
                     "win": 1 if kind == "win" else 0, "ev": ev_map[kind]})
    print(f"[barrier] barrier_set={BARRIER_SET} period={BARRIER_PERIOD} "
          f"matched={len(rows)}/{len(peaks)} skipped={skipped} in {time.time()-t0:.1f}s", flush=True)
    if not rows:
        return pl.DataFrame({"symbol": [], "date": [], "win": [], "ev": []}, schema=empty_schema)
    df = pl.DataFrame(rows, infer_schema_length=None)  # heterogeneous dicts -> None
    if df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date))
    return df


def main():
    t_start = time.time()
    print("[start] wave_cycle_mine prep_mirror.py", flush=True)
    print(f"[paths] OUT_PATH={OUT_PATH}", flush=True)

    verify_version()
    score_rows = fetch_era_scores()
    if not score_rows:
        print("[FATAL] no v74 75+ scores found in 2016-01-01..2020-12-31 -- aborting "
              "(mine.py's fallback path will engage: era_repl_sign='NA', W-family capped at "
              "NEAR_MISS)", flush=True)
        sys.exit(2)

    df = join_barrier_outcomes(score_rows)
    if df.height == 0:
        print("[FATAL] barrier join produced 0 rows -- aborting (same fallback as above)", flush=True)
        sys.exit(2)

    df = df.sort(["symbol", "date"])
    df.write_parquet(OUT_PATH)

    dmin, dmax = df["date"].min(), df["date"].max()
    n_syms = df["symbol"].n_unique()
    win_rate = float(df["win"].mean())
    print(f"[output] wrote {OUT_PATH}", flush=True)
    print(f"[summary] rows={df.height} symbols={n_syms} date_span={dmin}..{dmax} "
          f"win_rate={win_rate:.4f}", flush=True)

    # Light diagnostic use of the SHARED calendar module (both scripts use one
    # implementation -- see calendar_features.py docstring). NOT the analytical
    # era_repl_sign check itself -- mine.py owns that -- just a sanity print.
    cal = calendar_features.WCalendar()
    diag = calendar_features.add_w_columns(df, cal)
    opex_share = diag.filter(pl.col("w2_opex_week")).height / diag.height if diag.height else float("nan")
    tom_share = diag.filter(pl.col("w3_turn_of_month")).height / diag.height if diag.height else float("nan")
    print(f"[diagnostic] era-mirror entry share: opex_week={opex_share:.3f} "
          f"turn_of_month={tom_share:.3f} (report-only sanity check, not analytical)", flush=True)

    print(f"[done] total wall time {time.time()-t_start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
