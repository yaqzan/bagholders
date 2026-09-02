"""
minute_real.py -- w5dte_minute_real minute-level realizability study (BUILD_BRIEF_MR.md).

Answers PREREG.md's question: of the daily-bar TP-5x/TP-10x "fills" the w5dte_ev EV
study credited, how many are realizable against the actual minute tape (>=1 real
touching minute bar, with enough minutes/volume to plausibly work a resting sell-limit),
and does the FAMILY rule's TP-5x EV survive once ghost fills are gated out -- compared
against the EV study's own exposure-matched control (draws 0/1/2)?

REUSES experiments/w5dte_ev/ev_study.py verbatim for population load, rule masks,
pricing, and the control draw -- this script adds ONLY the minute-tape join/aggregate
layer on top. No MySQL anywhere.

CLI:
    py -3.11 minute_real.py --smoke   # 2 named weeks (2024-05-24 NVDA-earnings-adjacent,
                                       # 2023-07-28 quiet control), full pipeline, 6 hard
                                       # asserts. Writes to .../minute_real/_smoke/ and
                                       # experiments/w5dte_minute_real/RESULTS_SMOKE.md.
    py -3.11 minute_real.py --full    # full population. Queued only -- never run
                                       # directly by the builder agent. Writes to
                                       # .../minute_real/ and .../RESULTS.md.

Writes ONLY under experiments/w5dte_minute_real/ (this dir) and
B:\\polygon_derived\\weekly_5dte_movers\\minute_real\\. Never MySQL, never anything
outside those two trees.
"""
from __future__ import annotations

import argparse
import bisect
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
assert os.path.isfile(os.path.join(_ROOT, "CLAUDE.md")), \
    f"sys.path pin landed on {_ROOT!r}, expected the Trader repo root"

import polars as pl  # noqa: E402

from experiments._holdout import assert_no_holdout_leak  # noqa: E402
from experiments.w5dte_ev.ev_study import (  # noqa: E402
    load_population, add_rule_masks, add_pricing,
    prepare_control_base, draw_one, SEED_BASE, df_to_md_table,
)
from experiments.flatfile_exploitation.ff_common import (  # noqa: E402
    read_flatfile, list_session_dates,
)

# zoneinfo + tzdata verified working on this box 2026-08-18 (both EDT and EST offsets
# resolve correctly) -- used in preference to the brief's fixed -4h fallback, which
# would mis-convert winter (EST, UTC-5) sessions. See MR_BUILD_REPORT.md.
try:
    from zoneinfo import ZoneInfo
    _ET_ZONE = ZoneInfo("America/New_York")
    _TZ_METHOD = "zoneinfo(America/New_York) -- DST-correct"
except Exception:
    _ET_ZONE = None
    _TZ_METHOD = "fixed -4h offset (zoneinfo unavailable) -- DST-imprecise for winter sessions"

# ================================================================== constants (PREREG pins)

MR_OUT_ROOT = Path("B:/polygon_derived/weekly_5dte_movers/minute_real")
MR_SMOKE_ROOT = MR_OUT_ROOT / "_smoke"
REPO_RESULTS_MD = os.path.join(_HERE, "RESULTS.md")
REPO_RESULTS_SMOKE_MD = os.path.join(_HERE, "RESULTS_SMOKE.md")

LEVELS = (5, 10)
ARM_NAMES = ("FAMILY", "CONTROL0", "CONTROL1", "CONTROL2")
CONTROL_DRAW_IDX = {"CONTROL0": 0, "CONTROL1": 1, "CONTROL2": 2}

# BUILD_BRIEF_MR.md "Smoke" -- 2024-05-24 (NVDA earnings week) and 2023-07-28 (quiet
# control). NOTE (decision log, MR_BUILD_REPORT.md): NVDA itself has exactly one
# _family row in the 2024-05-24 week and it does NOT fill TP5 (0.35 -> 0.44), so the
# hand-check ticker below is a DELL contract from the same week instead (see decision).
SMOKE_EXPIRIES = [date(2024, 5, 24), date(2023, 7, 28)]
SMOKE_HAND_CHECK_TICKER = "O:DELL240524C00152500"  # entry 2024-05-20, entry_close=1.55

# EV RESULTS.md Table A, FAMILY/TP5 row (generated 2026-08-18 09:35:51) -- fidelity
# anchor BUILD_BRIEF_MR.md pins verbatim: 0.192363 * 58057 -> 11,168 +/- 1.
EV_FAMILY_N = 58057
EV_FAMILY_TP5_FILL_RATE = 0.192363

# Reference file for smoke check 5 (control-draw reproduction). Read-only.
EV_CONTROL_DRAWS_PARQUET = Path("B:/polygon_derived/weekly_5dte_movers/ev/tables/C_control_draws.parquet")

MINUTE_NS = 60_000_000_000  # 60 seconds in int64 nanoseconds -- one minute-bar step

EVENT_COLS = [
    "ticker", "underlying", "cp", "entry_date", "expiry_day", "expiry_year",
    "entry_close", "_tp_fill_5", "_tp_fill_10",
    "_r1", "_r2", "_r3", "_r4", "_r5", "_r6", "_row_id",
]

TIER_COLS = [f"tier_{t}_{L}" for L in LEVELS for t in ("R1", "R2", "R3")]


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ================================================================== population + arms (REUSE)

def load_priced_population() -> pl.DataFrame:
    """REUSE ev_study.py verbatim: load, mask, price. Full (unrestricted) population --
    the smoke-week restriction is applied later, by the caller, to the resulting arms."""
    pop = load_population()
    pop = add_rule_masks(pop)
    pop = add_pricing(pop)
    return pop


def check_family_fidelity(pop: pl.DataFrame) -> None:
    """BUILD_BRIEF_MR.md pin: FAMILY TP5-fill count must equal the EV study's own
    fill_rate*n (0.192363*58057 -> 11,168 +/-1). Hard assert -- fails loudly if the
    reused engine's output ever drifts from the parent EV study's fidelity anchor."""
    n = pop.filter(pl.col("_family") & pl.col("_tp_fill_5")).height
    expected = round(EV_FAMILY_TP5_FILL_RATE * EV_FAMILY_N)
    assert abs(n - expected) <= 1, (
        f"FAMILY TP5-fill count {n} does not match EV RESULTS.md fidelity anchor "
        f"{EV_FAMILY_TP5_FILL_RATE}*{EV_FAMILY_N}={expected} (+/-1)"
    )
    log(f"  fidelity check: FAMILY TP5-fill n={n} (expected {expected} +/-1) OK")


def build_arms(pop: pl.DataFrame):
    """Returns (arm_bases, arm_events), both dict[arm_name -> pl.DataFrame], against the
    FULL (unrestricted) population. arm_bases = the whole arm (58,057-ish rows, matches
    ev_study Table A's own denominator) -- used as the gated-EV-re-read base. arm_events
    = arm_bases filtered to _tp_fill_5 -- the population that needs minute data."""
    arm_bases = {}
    arm_events = {}

    family_base = pop.filter(pl.col("_family"))
    arm_bases["FAMILY"] = family_base
    arm_events["FAMILY"] = family_base.filter(pl.col("_tp_fill_5"))

    _, comp_with_k = prepare_control_base(pop, "_family")
    for arm_name, idx in CONTROL_DRAW_IDX.items():
        draw_df = draw_one(comp_with_k, idx, SEED_BASE)
        arm_bases[arm_name] = draw_df
        arm_events[arm_name] = draw_df.filter(pl.col("_tp_fill_5"))

    return arm_bases, arm_events


def restrict_to_smoke(df: pl.DataFrame, mode: str) -> pl.DataFrame:
    if mode == "smoke":
        return df.filter(pl.col("expiry_day").is_in(SMOKE_EXPIRIES))
    return df


# ================================================================== smoke checks 1, 5, 6

def check_control_reproduction(control0_df: pl.DataFrame) -> "tuple[bool, str]":
    """Smoke check 5: draw_one(draw_idx=0) row count must match the EV study's own
    stored control-draw parquet exactly (arm==FAMILY, draw_idx==0, n_selected)."""
    ref = pl.read_parquet(EV_CONTROL_DRAWS_PARQUET).filter(
        (pl.col("arm") == "FAMILY") & (pl.col("draw_idx") == 0)
    )
    if ref.height != 1:
        return False, f"reference row not found/unique in {EV_CONTROL_DRAWS_PARQUET} (rows={ref.height})"
    ref_n = int(ref["n_selected"][0])
    got_n = control0_df.height
    ok = (ref_n == got_n)
    return ok, f"draw_idx=0 n_selected: reference={ref_n} got={got_n}"


def check_event_count_reconciliation(mode_a_count: int) -> "tuple[bool, str]":
    """Smoke check 1: independently reload the raw population, restrict to the smoke
    weeks FIRST, then apply add_rule_masks/add_pricing -- must give the identical
    FAMILY TP5-fill count as restricting AFTER masking/pricing (mode_a_count), since
    both are pure row-wise functions over hardcoded constants. A second load_population()
    call (a few seconds) -- acceptable for a one-shot smoke check."""
    raw = load_population()
    raw = raw.filter(pl.col("expiry_day").is_in(SMOKE_EXPIRIES))
    raw = add_rule_masks(raw)
    raw = add_pricing(raw)
    mode_b_count = raw.filter(pl.col("_family") & pl.col("_tp_fill_5")).height
    ok = (mode_a_count == mode_b_count)
    return ok, f"full-then-filter={mode_a_count} filter-then-mask={mode_b_count}"


def check_holdout(events: pl.DataFrame) -> "tuple[bool, str]":
    """Smoke check 6: explicit holdout assert on the actual event frame this script
    uses downstream (load_population() already asserts on the raw 10.3M-row frame;
    this re-asserts on the smoke-restricted, arm-concatenated frame actually consumed)."""
    try:
        assert_no_holdout_leak(events.select(pl.col("entry_date").alias("date")),
                                context="minute_real.py smoke event frame")
        return True, "no exception raised"
    except AssertionError as e:
        return False, str(e)


# ================================================================== session/ticker index

def sessions_for_event(archive_sessions: list, entry_date_: date, expiry_day_: date) -> list:
    """Sessions strictly after entry_date through expiry_day, i.e. (entry_date, expiry_day]."""
    lo = bisect.bisect_right(archive_sessions, entry_date_)
    hi = bisect.bisect_right(archive_sessions, expiry_day_)
    return archive_sessions[lo:hi]


def build_session_index(events: pl.DataFrame, archive_sessions: list):
    """dict[date -> list[(arm, _row_id, ticker, P5, P10)]], union over all arms/events.
    Pure-python bisect per event (fast: O(log n) each) rather than a cross-join --
    events x full archive would be wastefully large for what is, per event, a
    ~week-long window."""
    session_to_rows: "dict[date, list]" = {}
    n_needed_total = 0
    cols = ["arm", "_row_id", "ticker", "entry_date", "expiry_day", "entry_close"]
    for rec in events.select(cols).iter_rows(named=True):
        needed = sessions_for_event(archive_sessions, rec["entry_date"], rec["expiry_day"])
        n_needed_total += len(needed)
        p5 = 5.0 * rec["entry_close"]
        p10 = 10.0 * rec["entry_close"]
        for d in needed:
            session_to_rows.setdefault(d, []).append((rec["arm"], rec["_row_id"], rec["ticker"], p5, p10))
    return session_to_rows, n_needed_total


# ================================================================== per-session vectorized aggregation

def per_session_level_agg(hit_df: pl.DataFrame) -> pl.DataFrame:
    """hit_df: bars already filtered to the touching condition for ONE level, ONE
    session -- columns [arm, _row_id, volume, window_start]. Returns per-(arm,_row_id)
    mins/vol/first_touch_ns/max_run for THIS session only (runs never span sessions
    because this is called once per session on that session's rows alone)."""
    schema = {"arm": pl.Utf8, "_row_id": pl.Int64, "mins": pl.UInt32,
              "vol": pl.Int64, "first_touch_ns": pl.Int64, "max_run": pl.UInt32}
    if hit_df.height == 0:
        return pl.DataFrame(schema=schema)
    run_frame = (
        hit_df.sort(["arm", "_row_id", "window_start"])
        .with_columns(pl.col("window_start").diff().over(["arm", "_row_id"]).alias("_dw"))
        .with_columns((pl.col("_dw").fill_null(-1) != MINUTE_NS).cast(pl.Int32).alias("_brk"))
        .with_columns(pl.col("_brk").cum_sum().over(["arm", "_row_id"]).alias("_run_id"))
    )
    run_lens = run_frame.group_by(["arm", "_row_id", "_run_id"]).agg(pl.len().alias("_run_len"))
    max_run = (run_lens.group_by(["arm", "_row_id"])
               .agg(pl.col("_run_len").max().cast(pl.UInt32).alias("max_run")))
    base = hit_df.group_by(["arm", "_row_id"]).agg([
        pl.len().cast(pl.UInt32).alias("mins"),
        pl.col("volume").sum().alias("vol"),
        pl.col("window_start").min().alias("first_touch_ns"),
    ])
    return base.join(max_run, on=["arm", "_row_id"], how="left")


def process_minute_tape(events: pl.DataFrame, archive_sessions: list):
    """Main streaming pass. Python loop over SESSIONS (ascending); polars joins/aggregates
    within each session; only small per-session partial-aggregate frames are retained
    across the loop (never a full minute-file join across all sessions at once)."""
    session_to_rows, n_needed_total = build_session_index(events, archive_sessions)
    sessions_sorted = sorted(session_to_rows.keys())
    log(f"  minute-tape scan: {len(sessions_sorted)} distinct sessions needed "
        f"(sum over events = {n_needed_total})")

    today_schema = {"arm": pl.Utf8, "_row_id": pl.Int64, "ticker": pl.Utf8,
                     "P5": pl.Float64, "P10": pl.Float64}
    partials = {5: [], 10: []}
    missing_minute_files = []
    n_bars_read = 0
    t_scan0 = time.time()

    for d in sessions_sorted:
        rows_today = session_to_rows[d]
        today_df = pl.DataFrame(rows_today, schema=today_schema, orient="row")
        try:
            bars = read_flatfile("minute_aggs_v1", d, columns=["ticker", "volume", "high", "window_start"])
        except FileNotFoundError:
            missing_minute_files.append(d)
            continue
        n_bars_read += bars.height

        joined = bars.join(today_df, on="ticker", how="inner")
        if joined.height == 0:
            continue
        joined = joined.with_columns([
            (pl.col("high") >= pl.col("P5")).alias("above_5"),
            (pl.col("high") >= pl.col("P10")).alias("above_10"),
        ])
        # above_10 => above_5 always (10x level is strictly higher), so touch5 already
        # contains every above_10 bar too -- touch10 is a filter of touch5, not of joined.
        touch5 = joined.filter(pl.col("above_5")).select(["arm", "_row_id", "volume", "window_start"])
        if touch5.height:
            partials[5].append(per_session_level_agg(touch5))
        touch10 = joined.filter(pl.col("above_10")).select(["arm", "_row_id", "volume", "window_start"])
        if touch10.height:
            partials[10].append(per_session_level_agg(touch10))

    scan_elapsed = time.time() - t_scan0
    log(f"  scan complete: {len(sessions_sorted)} sessions, {n_bars_read} raw bar-rows read, "
        f"{len(missing_minute_files)} missing files, {scan_elapsed:.2f}s")

    reduced = {}
    for L in LEVELS:
        if partials[L]:
            allL = pl.concat(partials[L], how="vertical_relaxed")
            reduced[L] = (allL.group_by(["arm", "_row_id"])
                          .agg([pl.col("mins").sum().alias(f"mins_at_above_{L}"),
                                pl.col("vol").sum().alias(f"vol_at_above_{L}"),
                                pl.col("first_touch_ns").min().alias(f"first_touch_ns_{L}"),
                                pl.col("max_run").max().alias(f"max_run_{L}"),
                                pl.len().alias(f"n_touch_days_{L}")]))
        else:
            reduced[L] = pl.DataFrame(schema={
                "arm": pl.Utf8, "_row_id": pl.Int64,
                f"mins_at_above_{L}": pl.UInt32, f"vol_at_above_{L}": pl.Int64,
                f"first_touch_ns_{L}": pl.Int64, f"max_run_{L}": pl.UInt32,
                f"n_touch_days_{L}": pl.UInt32,
            })
    return reduced, missing_minute_files, scan_elapsed, n_bars_read, len(sessions_sorted)


def attach_metrics(events: pl.DataFrame, reduced: dict) -> pl.DataFrame:
    out = events
    for L in LEVELS:
        out = out.join(reduced[L], on=["arm", "_row_id"], how="left")
        out = out.with_columns([
            pl.col(f"mins_at_above_{L}").fill_null(0),
            pl.col(f"vol_at_above_{L}").fill_null(0),
            pl.col(f"n_touch_days_{L}").fill_null(0),
        ])
        mins = pl.col(f"mins_at_above_{L}")
        vol = pl.col(f"vol_at_above_{L}")
        out = out.with_columns([
            (mins >= 1).alias(f"r0_ok_{L}"),
            ((mins >= 2) | (vol >= 5)).alias(f"tier_R1_{L}"),
            ((mins >= 5) & (vol >= 10)).alias(f"tier_R2_{L}"),
            ((mins >= 15) & (vol >= 50)).alias(f"tier_R3_{L}"),
        ])
    return out


def add_et_time_strings(df: pl.DataFrame) -> pl.DataFrame:
    """Python-level conversion (small frame -- one row per event, not per bar). window_start
    is an int64 UTC-instant nanosecond epoch; converts via zoneinfo when available."""
    def to_et(ns):
        if ns is None:
            return None
        dt_utc = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
        return dt_utc.astimezone(_ET_ZONE) if _ET_ZONE is not None else dt_utc + timedelta(hours=-4)

    out = df
    for L in LEVELS:
        col = f"first_touch_ns_{L}"
        ets = [to_et(x) for x in df[col].to_list()]
        full_str = [e.strftime("%Y-%m-%d %H:%M:%S") if e is not None else None for e in ets]
        hm_str = [e.strftime("%H:%M") if e is not None else None for e in ets]
        out = out.with_columns([
            pl.Series(f"first_touch_et_{L}", full_str),
            pl.Series(f"first_touch_hm_{L}", hm_str),
        ])
    return out


# ================================================================== smoke checks 2, 3, 4

def check_r0_rate(events: pl.DataFrame) -> "tuple[bool, str]":
    """Smoke check 2: R0 (>=1 touching minute bar) at L=5, pooled across all smoke
    events (FAMILY+CONTROL) -- ALL of them are _tp_fill_5==True by construction, so
    R0 is expected to hold for essentially all of them (daily-vs-minute consistency)."""
    n = events.height
    n_ok = events.filter(pl.col("r0_ok_5")).height
    rate = n_ok / n if n else 0.0
    misses = (events.filter(~pl.col("r0_ok_5"))
              .select(["arm", "ticker", "entry_date", "expiry_day"]).to_dicts())
    ok = rate >= 0.95
    detail = f"L5 r0_ok={n_ok}/{n} ({rate:.4f}); misses={misses[:10]}"
    return ok, detail


def check_hand_verification(archive_sessions: list, events: pl.DataFrame,
                             out_lines: list) -> "tuple[bool, str]":
    """Smoke check 3: independently re-derive mins/vol at P5 for ONE known FAMILY
    fill event straight from the raw minute files (a second, from-scratch computation,
    not a reuse of the pipeline's own accumulator state), and confirm it matches the
    pipeline's own result exactly."""
    rec = events.filter((pl.col("arm") == "FAMILY") & (pl.col("ticker") == SMOKE_HAND_CHECK_TICKER))
    if rec.height != 1:
        # defensive fallback: any FAMILY event in the NVDA week
        fallback = events.filter((pl.col("arm") == "FAMILY")
                                  & (pl.col("expiry_day") == SMOKE_EXPIRIES[0]))
        if fallback.height == 0:
            return False, f"hand-check ticker {SMOKE_HAND_CHECK_TICKER} not found (rec.height={rec.height}); no fallback available"
        out_lines.append(f"WARNING: {SMOKE_HAND_CHECK_TICKER} not uniquely found (rec.height={rec.height}); "
                          f"falling back to {fallback['ticker'][0]}")
        rec = fallback.head(1)
    r = rec.to_dicts()[0]
    ticker = r["ticker"]
    entry_close = r["entry_close"]
    p5 = 5.0 * entry_close
    needed = sessions_for_event(archive_sessions, r["entry_date"], r["expiry_day"])
    manual_mins, manual_vol, printed = 0, 0, 0
    out_lines.append(f"Hand-verification ticker={ticker} entry_date={r['entry_date']} "
                      f"expiry_day={r['expiry_day']} entry_close={entry_close} P5={p5} "
                      f"sessions_scanned={needed}")
    for d in needed:
        bars = read_flatfile("minute_aggs_v1", d, columns=["ticker", "volume", "high", "window_start"])
        sub = bars.filter((pl.col("ticker") == ticker) & (pl.col("high") >= p5)).sort("window_start")
        manual_mins += sub.height
        manual_vol += int(sub["volume"].sum()) if sub.height else 0
        if printed < 5 and sub.height:
            for row in sub.head(5 - printed).to_dicts():
                out_lines.append(f"  {d} {row}")
                printed += 1
    pipeline_mins = r["mins_at_above_5"]
    pipeline_vol = r["vol_at_above_5"]
    ok = (manual_mins == pipeline_mins) and (manual_vol == pipeline_vol)
    detail = (f"manual: mins={manual_mins} vol={manual_vol}; pipeline: mins={pipeline_mins} "
              f"vol={pipeline_vol}")
    out_lines.append(detail)
    return ok, detail


def check_monotonicity(table_a1: pl.DataFrame) -> "tuple[bool, str]":
    """Smoke check 4: validity_R1 >= validity_R2 >= validity_R3 per (arm, L). This is a
    mathematical guarantee given the tier definitions (R3 => R2 => R1 by construction),
    so failure would indicate an implementation bug, not a data surprise."""
    ok = True
    details = []
    for row in table_a1.to_dicts():
        r1, r2, r3 = row["validity_R1"], row["validity_R2"], row["validity_R3"]
        if r1 is None or r2 is None or r3 is None:
            details.append(f"{row['arm']}/L{row['L']}: n_r0_ok=0, skipped")
            continue
        row_ok = (r1 >= r2 >= r3)
        ok = ok and row_ok
        details.append(f"{row['arm']}/L{row['L']}: R1={r1:.4f} R2={r2:.4f} R3={r3:.4f} {'OK' if row_ok else 'FAIL'}")
    return ok, "; ".join(details)


# ================================================================== output tables

def tier_validity_row(sub: pl.DataFrame, L: int) -> dict:
    tot = sub.height
    r0_col = f"r0_ok_{L}"
    denom = sub.filter(pl.col(r0_col)) if tot else sub
    n_r0 = denom.height

    def rate(tier):
        return denom[f"tier_{tier}_{L}"].mean() if n_r0 else None

    return {
        "L": L, "n_total": tot, "n_r0_ok": n_r0,
        "r0_fail_n": tot - n_r0,
        "r0_fail_rate": (tot - n_r0) / tot if tot else None,
        "validity_R1": rate("R1"), "validity_R2": rate("R2"), "validity_R3": rate("R3"),
    }


def _level_population(df: pl.DataFrame, L: int) -> pl.DataFrame:
    """L=5 applies to every event in our population by construction (_tp_fill_5==True
    for all of them); L=10 only has an 'expected touch' for the sub-population whose
    OWN daily _tp_fill_10 flag was true (see MR_BUILD_REPORT.md decision log)."""
    return df if L == 5 else df.filter(pl.col("_tp_fill_10"))


def table_A1_validity_by_arm(events: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for arm in ARM_NAMES:
        sub_arm = events.filter(pl.col("arm") == arm)
        if sub_arm.height == 0:
            continue
        for L in LEVELS:
            rows.append({"arm": arm, **tier_validity_row(_level_population(sub_arm, L), L)})
    return pl.DataFrame(rows)


def table_A2_validity_by_year(events: pl.DataFrame) -> pl.DataFrame:
    fam = events.filter(pl.col("arm") == "FAMILY")
    rows = []
    for year in sorted(fam["expiry_year"].unique().to_list()):
        yr = fam.filter(pl.col("expiry_year") == year)
        for L in LEVELS:
            rows.append({"expiry_year": year, **tier_validity_row(_level_population(yr, L), L)})
    return pl.DataFrame(rows)


def table_A3_validity_by_rule(events: pl.DataFrame) -> pl.DataFrame:
    fam = events.filter(pl.col("arm") == "FAMILY")
    rows = []
    for i in range(1, 7):
        sub = fam.filter(pl.col(f"_r{i}"))
        if sub.height == 0:
            continue
        for L in LEVELS:
            rows.append({"rule": f"R{i}", **tier_validity_row(_level_population(sub, L), L)})
    return pl.DataFrame(rows)


def gated_ev_for_arm(arm_base: pl.DataFrame, arm_events_metrics: pl.DataFrame, arm_name: str) -> list:
    """PREREG 'EV re-read': event kept as a TP fill if it was a daily fill AND its
    realizability tier passes; otherwise its return falls back to _r_EXPIRY (already on
    the population frame from add_pricing). Denominator is the FULL arm (matches the EV
    study's own Table A scope), not just the measured tp_fill_5 subset -- only rows in
    that subset can actually be re-priced; the rest already have _r_TP{L}==_r_EXPIRY."""
    base = arm_base.join(arm_events_metrics.select(["_row_id"] + TIER_COLS), on="_row_id", how="left")
    base = base.with_columns([pl.col(c).fill_null(False) for c in TIER_COLS])
    rows = []
    for L in LEVELS:
        n = base.height
        ungated_ev = base[f"_r_TP{L}"].mean()
        row = {"arm": arm_name, "L": L, "n": n, "ungated_ev": ungated_ev}
        for t in ("R1", "R2", "R3"):
            gated_expr = (pl.when(pl.col(f"_tp_fill_{L}") & pl.col(f"tier_{t}_{L}"))
                          .then(pl.col(f"_r_TP{L}")).otherwise(pl.col("_r_EXPIRY")))
            row[f"{t}_ev"] = base.select(gated_expr.mean().alias("g"))["g"][0]
        rows.append(row)
    return rows


def build_table_B(arm_bases: dict, events_metrics: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for arm_name in ARM_NAMES:
        sub_metrics = events_metrics.filter(pl.col("arm") == arm_name)
        if sub_metrics.height == 0 or arm_bases[arm_name].height == 0:
            continue
        rows.extend(gated_ev_for_arm(arm_bases[arm_name], sub_metrics, arm_name))
    return pl.DataFrame(rows)


def compute_survives_verdict(table_b: pl.DataFrame) -> dict:
    """PREREG 'Adjudication': SURVIVES iff FAMILY TP-5x EV under R2 gating stays > 0 AND
    exceeds all 3 R2-gated control-draw EVs. 3 draws = directional screen, stated as such
    (not the 100-draw gate)."""
    fam = table_b.filter((pl.col("arm") == "FAMILY") & (pl.col("L") == 5))
    if fam.height == 0:
        return {"verdict": "INCONCLUSIVE", "reason": "no FAMILY/L5 row in table B (empty smoke slice?)"}
    fam_r2 = fam["R2_ev"][0]
    controls = table_b.filter(pl.col("arm").is_in(["CONTROL0", "CONTROL1", "CONTROL2"]) & (pl.col("L") == 5))
    control_r2s = controls["R2_ev"].to_list()
    cond_a = fam_r2 is not None and fam_r2 > 0
    cond_b = (len(control_r2s) == 3
              and all(c is not None for c in control_r2s)
              and all(fam_r2 > c for c in control_r2s) if fam_r2 is not None else False)
    verdict = "SURVIVES" if (cond_a and cond_b) else "FAIL"
    return {"verdict": verdict, "family_R2_ev_L5": fam_r2, "control_R2_ev_L5": control_r2s,
            "cond_a_positive": cond_a, "cond_b_beats_all_3_controls": cond_b,
            "note": "3 draws = directional screen per PREREG, not the 100-draw EV gate"}


def bucket_label(hm: str) -> str:
    h, m = int(hm[:2]), int(hm[3:5])
    total = h * 60 + m
    bstart = (total // 30) * 30
    bend = bstart + 30
    return f"{bstart // 60:02d}:{bstart % 60:02d}-{bend // 60:02d}:{bend % 60:02d}"


def table_C1_histogram(events: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for arm in ARM_NAMES:
        sub_arm = events.filter(pl.col("arm") == arm)
        for L in LEVELS:
            pop = _level_population(sub_arm, L).filter(pl.col(f"r0_ok_{L}"))
            counts: "dict[str,int]" = {}
            for hm in pop[f"first_touch_hm_{L}"].drop_nulls().to_list():
                b = bucket_label(hm)
                counts[b] = counts.get(b, 0) + 1
            for b, c in sorted(counts.items()):
                rows.append({"arm": arm, "L": L, "bucket": b, "n": c})
    if not rows:
        return pl.DataFrame(schema={"arm": pl.Utf8, "L": pl.Int64, "bucket": pl.Utf8, "n": pl.Int64})
    return pl.DataFrame(rows)


def table_C2_summary(events: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for arm in ARM_NAMES:
        sub_arm = events.filter(pl.col("arm") == arm)
        for L in LEVELS:
            denom = _level_population(sub_arm, L).filter(pl.col(f"r0_ok_{L}"))
            n = denom.height
            if n == 0:
                rows.append({"arm": arm, "L": L, "n": 0, "open_auction_share": None, "lone_print_share": None})
                continue
            open_auction = denom.filter(pl.col(f"first_touch_hm_{L}") < "10:00").height / n
            lone = denom.filter(pl.col(f"mins_at_above_{L}") == 1).height / n
            rows.append({"arm": arm, "L": L, "n": n, "open_auction_share": open_auction, "lone_print_share": lone})
    return pl.DataFrame(rows)


def table_D_data_quality(events: pl.DataFrame, missing_files: list) -> pl.DataFrame:
    rows = []
    for arm in ARM_NAMES:
        sub_arm = events.filter(pl.col("arm") == arm)
        for L in LEVELS:
            pop = _level_population(sub_arm, L)
            n = pop.height
            r0_fail = pop.filter(~pl.col(f"r0_ok_{L}")).height
            rows.append({"arm": arm, "L": L, "n": n, "r0_fail_n": r0_fail,
                         "r0_fail_rate": (r0_fail / n) if n else None})
    df = pl.DataFrame(rows)
    log(f"  missing_minute_files ({len(missing_files)}): {missing_files}")
    return df


def table_E_family_vs_control(table_a1: pl.DataFrame) -> pl.DataFrame:
    rows = []
    fam = table_a1.filter(pl.col("arm") == "FAMILY")
    for L in LEVELS:
        fam_l = fam.filter(pl.col("L") == L)
        if fam_l.height == 0:
            continue
        fam_r2 = fam_l["validity_R2"][0]
        for arm in ("CONTROL0", "CONTROL1", "CONTROL2"):
            c = table_a1.filter((pl.col("arm") == arm) & (pl.col("L") == L))
            if c.height == 0:
                continue
            c_r2 = c["validity_R2"][0]
            gap = (fam_r2 - c_r2) if (fam_r2 is not None and c_r2 is not None) else None
            rows.append({"L": L, "control_arm": arm, "family_validity_R2": fam_r2,
                         "control_validity_R2": c_r2, "gap": gap})
    return pl.DataFrame(rows)


# ================================================================== write outputs

def write_outputs(mode: str, events_metrics: pl.DataFrame, table_a1, table_a2, table_a3,
                   table_b, verdict, table_c1, table_c2, table_d, table_e,
                   missing_files: list, scan_stats: dict, smoke_checks=None) -> None:
    out_root = MR_SMOKE_ROOT if mode == "smoke" else MR_OUT_ROOT
    out_root.mkdir(parents=True, exist_ok=True)

    def save(name, d):
        d.write_parquet(out_root / f"{name}.parquet")
        with open(out_root / f"{name}.md", "w", encoding="ascii", errors="replace") as f:
            f.write(df_to_md_table(d))
        log(f"  wrote {name}.parquet + .md ({d.height} rows)")

    log(f"writing tables to {out_root}")
    save("events_metrics", events_metrics)
    save("arm_summary", table_a1)
    save("A2_validity_by_year", table_a2)
    save("A3_validity_by_rule", table_a3)
    save("B_gated_ev", table_b)
    save("C1_first_touch_histogram", table_c1)
    save("C2_touch_summary", table_c2)
    save("D_data_quality", table_d)
    save("E_family_vs_control_gap", table_e)

    md = []
    title = "RESULTS -- w5dte_minute_real"
    if mode == "smoke":
        title += " -- SMOKE MODE (2 named weeks only; NOT the full population; NOT a real verdict)"
    md.append(f"# {title}")
    md.append("")
    md.append(f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} (local), seed_base={SEED_BASE}, mode={mode}")
    md.append(f"Timezone method: {_TZ_METHOD}")
    md.append(f"Sessions scanned: {scan_stats['n_sessions']}; raw bar-rows read: {scan_stats['n_bars_read']}; "
              f"scan wall-clock: {scan_stats['scan_elapsed']:.2f}s")
    md.append(f"missing_minute_files: {len(missing_files)} {missing_files}")
    counts_str = ", ".join(f"{a}={events_metrics.filter(pl.col('arm') == a).height}" for a in ARM_NAMES)
    md.append(f"Events processed: {events_metrics.height} ({counts_str})")
    md.append("")
    if smoke_checks is not None:
        md.append("## Smoke checks")
        md.append("")
        for name, passed, detail in smoke_checks:
            md.append(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
        md.append("")
    md.append("## Table A1 -- validity rate by arm x L x tier (denominator = r0_ok events)")
    md.append("")
    md.append(df_to_md_table(table_a1))
    md.append("")
    md.append("## Table A2 -- FAMILY validity by expiry_year x L x tier (secondary)")
    md.append("")
    md.append(df_to_md_table(table_a2))
    md.append("")
    md.append("## Table A3 -- FAMILY validity by rule R1..R6 x L x tier (secondary)")
    md.append("")
    md.append(df_to_md_table(table_a3))
    md.append("")
    md.append("## Table B -- gated EV re-read (arm x L x {ungated, R1, R2, R3})")
    md.append("")
    md.append(df_to_md_table(table_b))
    md.append("")
    md.append("PREREG SURVIVES line (Adjudication, evaluated verbatim):")
    for k, v in verdict.items():
        md.append(f"- {k}: {v}")
    md.append("")
    md.append("## Table C1 -- first-touch time-of-day histogram (30-min ET buckets, r0_ok events)")
    md.append("")
    md.append(df_to_md_table(table_c1))
    md.append("")
    md.append("## Table C2 -- open-auction share (<10:00 ET) + lone-print share (mins_at_above==1)")
    md.append("")
    md.append(df_to_md_table(table_c2))
    md.append("")
    md.append("## Table D -- data quality (r0 failures by arm x L)")
    md.append("")
    md.append(df_to_md_table(table_d))
    md.append("")
    md.append("## Table E -- FAMILY vs CONTROL validity gap (R2, secondary)")
    md.append("")
    md.append(df_to_md_table(table_e))
    md.append("")

    results_path = REPO_RESULTS_SMOKE_MD if mode == "smoke" else REPO_RESULTS_MD
    with open(results_path, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(md) + "\n")
    log(f"wrote {results_path}")


# ================================================================== main

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="w5dte_minute_real minute-level realizability study")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--smoke", action="store_true",
                   help="2 named weeks, full pipeline, 6 hard-asserts; writes to minute_real/_smoke/")
    g.add_argument("--full", action="store_true",
                   help="full population. Queued only -- never run directly by the builder agent.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    mode = "smoke" if args.smoke else "full"
    t0 = time.time()
    log("=" * 78)
    log("w5dte_minute_real -- minute_real.py")
    log(f"mode={mode.upper()}  seed_base={SEED_BASE}  tz_method={_TZ_METHOD}")
    log("=" * 78)

    pop = load_priced_population()
    check_family_fidelity(pop)

    archive_day = list_session_dates("day_aggs_v1")
    archive_min = list_session_dates("minute_aggs_v1")
    log(f"  archive sessions: day_aggs={len(archive_day)} minute_aggs={len(archive_min)} "
        f"equal={archive_day == archive_min}")
    archive_sessions = archive_day

    arm_bases_full, arm_events_full = build_arms(pop)
    log("  arm sizes (full, unrestricted): " +
        ", ".join(f"{a}={arm_bases_full[a].height}(tp5={arm_events_full[a].height})" for a in ARM_NAMES))

    smoke_check_results = []
    if mode == "smoke":
        ok5, d5 = check_control_reproduction(arm_bases_full["CONTROL0"])
        smoke_check_results.append(("5. Control draws reproduce (draw_idx=0 n_selected)", ok5, d5))

    arm_bases = {a: restrict_to_smoke(df, mode) for a, df in arm_bases_full.items()}
    arm_events = {a: restrict_to_smoke(df, mode) for a, df in arm_events_full.items()}

    if mode == "smoke":
        ok1, d1 = check_event_count_reconciliation(arm_events["FAMILY"].height)
        smoke_check_results.append(("1. Event-count reconciliation (full-then-filter vs filter-then-mask)", ok1, d1))

    events = pl.concat(
        [arm_events[a].select(EVENT_COLS).with_columns(pl.lit(a).alias("arm")) for a in ARM_NAMES],
        how="vertical_relaxed",
    )
    log(f"  events to process: {events.height} (" +
        ", ".join(f"{a}={arm_events[a].height}" for a in ARM_NAMES) + ")")

    if mode == "smoke":
        ok6, d6 = check_holdout(events)
        smoke_check_results.append(("6. Holdout assert on event frame", ok6, d6))

    reduced, missing_files, scan_elapsed, n_bars_read, n_sessions = process_minute_tape(events, archive_sessions)
    events = attach_metrics(events, reduced)
    events = add_et_time_strings(events)

    if mode == "smoke":
        ok2, d2 = check_r0_rate(events)
        smoke_check_results.append(("2. R0 sanity >= 95% on smoke events (L5)", ok2, d2))

        hand_lines: list = []
        ok3, d3 = check_hand_verification(archive_sessions, events, hand_lines)
        smoke_check_results.append(("3. Hand-verification (independent manual sum vs pipeline)", ok3, d3))
        for line in hand_lines:
            log("    " + line)

    table_a1 = table_A1_validity_by_arm(events)
    table_a2 = table_A2_validity_by_year(events)
    table_a3 = table_A3_validity_by_rule(events)

    if mode == "smoke":
        ok4, d4 = check_monotonicity(table_a1)
        smoke_check_results.append(("4. Monotonicity validity_R1 >= R2 >= R3 per arm/L", ok4, d4))

    table_b = build_table_B(arm_bases, events)
    verdict = compute_survives_verdict(table_b)
    table_c1 = table_C1_histogram(events)
    table_c2 = table_C2_summary(events)
    table_d = table_D_data_quality(events, missing_files)
    table_e = table_E_family_vs_control(table_a1)

    scan_stats = {"n_sessions": n_sessions, "n_bars_read": n_bars_read, "scan_elapsed": scan_elapsed}

    write_outputs(mode, events, table_a1, table_a2, table_a3, table_b, verdict,
                  table_c1, table_c2, table_d, table_e, missing_files, scan_stats,
                  smoke_checks=smoke_check_results if mode == "smoke" else None)

    if mode == "smoke":
        log("\n" + "=" * 78)
        log("SMOKE CHECK BLOCK")
        log("=" * 78)
        n_fail = 0
        for name, passed, detail in smoke_check_results:
            status = "PASS" if passed else "FAIL"
            if not passed:
                n_fail += 1
            log(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
        log(f"\n{len(smoke_check_results) - n_fail}/{len(smoke_check_results)} smoke checks passed. "
            f"elapsed={time.time() - t0:.1f}s")
        return 0 if n_fail == 0 else 1
    else:
        log(f"\n--full complete. elapsed={time.time() - t0:.1f}s")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
