"""
smoke_test.py -- weekly_5dte_movers smoke scope (BUILD_BRIEF.md "Smoke scope").

Runs Stage A (build_ledger.run_smoke) and Stage B (build_features.run_smoke) on the 4
named weeks / <=10 curated symbols, then hard-asserts every check BUILD_BRIEF specifies.
Prints a PASS/FAIL line per check plus a final summary, and writes the NVDA top-20
growth_mult table (+ tradeable-floor variant) as ASCII into BUILD_REPORT.md.

Exit 0 = every check green. Exit 1 = at least one check failed (see the printed summary
for which, and BUILD_REPORT.md for narrative detail on any known/explained gap).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
assert os.path.isfile(os.path.join(_ROOT, "CLAUDE.md")), \
    f"sys.path pin landed on {_ROOT!r}, expected the Trader repo root"

_FF_DIR = os.path.join(_ROOT, "experiments", "flatfile_exploitation")
if _FF_DIR not in sys.path:
    sys.path.insert(0, _FF_DIR)

import polars as pl  # noqa: E402

import ff_common  # noqa: E402
from experiments.weekly_5dte_movers import build_ledger  # noqa: E402
from experiments.weekly_5dte_movers import build_features  # noqa: E402

BUILD_REPORT_PATH = os.path.join(_HERE, "BUILD_REPORT.md")

RESULTS: list = []


def log(msg) -> None:
    print(msg, flush=True)


def check(name: str, passed: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(passed), detail))
    status = "PASS" if passed else "FAIL"
    log(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(passed)


# ---------------------------------------------------------------- ASCII table helper

def ascii_table(df: pl.DataFrame, cols: list, float_cols: tuple = (), float_fmt: str = "{:.4f}") -> str:
    rows = df.select(cols).to_dicts()

    def fmt(c, v):
        if v is None:
            return ""
        if c in float_cols and isinstance(v, (int, float)):
            return float_fmt.format(float(v))
        return str(v)

    str_rows = [[fmt(c, r[c]) for c in cols] for r in rows]
    widths = [len(c) for c in cols]
    for sr in str_rows:
        for i, v in enumerate(sr):
            widths[i] = max(widths[i], len(v))

    def fmt_row(vals):
        return " | ".join(v.ljust(w) for v, w in zip(vals, widths))

    lines = [fmt_row(cols), "-+-".join("-" * w for w in widths)]
    lines.extend(fmt_row(sr) for sr in str_rows)
    return "\n".join(lines)


# ---------------------------------------------------------------- independent-path spot check

def independent_path_spot_check(ledger: pl.DataFrame, sample_tickers: list, entry_date_by_ticker: dict,
                                 expiry_day: date, week_sessions: list) -> None:
    for ticker in sample_tickers:
        entry_date = entry_date_by_ticker[ticker]
        ledger_row = ledger.filter((pl.col("ticker") == ticker) & (pl.col("entry_date") == entry_date))
        assert ledger_row.height == 1, f"expected exactly 1 ledger row for {ticker}@{entry_date}, got {ledger_row.height}"
        ledger_entry_close = float(ledger_row["entry_close"][0])
        ledger_max_high = ledger_row["max_future_high"][0]
        ledger_max_high = float(ledger_max_high) if ledger_max_high is not None else None

        raw_entry = ff_common.read_flatfile("day_aggs_v1", entry_date)
        raw_entry_row = raw_entry.filter(pl.col("ticker") == ticker)
        assert raw_entry_row.height == 1, f"raw gzip {entry_date} has {raw_entry_row.height} rows for {ticker}, expected 1"
        raw_entry_close = float(raw_entry_row["close"][0])

        forward_days = [d for d in week_sessions if d > entry_date]
        raw_highs = []
        for d in forward_days:
            raw_d = ff_common.read_flatfile("day_aggs_v1", d)
            raw_d_row = raw_d.filter(pl.col("ticker") == ticker)
            if raw_d_row.height:
                raw_highs.append(float(raw_d_row["high"][0]))
        raw_max_high = max(raw_highs) if raw_highs else None

        close_match = abs(raw_entry_close - ledger_entry_close) < 1e-9
        check(f"independent-path entry_close match: {ticker}@{entry_date}", close_match,
              f"ledger={ledger_entry_close} raw={raw_entry_close}")

        if ledger_max_high is None or raw_max_high is None:
            high_match = (ledger_max_high is None and raw_max_high is None)
        else:
            high_match = abs(raw_max_high - ledger_max_high) < 1e-9
        check(f"independent-path max_future_high match: {ticker}@{entry_date}", high_match,
              f"ledger={ledger_max_high} raw={raw_max_high}")


# ---------------------------------------------------------------- main

def main() -> int:
    t0 = time.time()
    log("=" * 78)
    log("weekly_5dte_movers SMOKE TEST")
    log("=" * 78)

    # ---------------- Stage A ----------------
    log("\n--- Stage A: build_ledger.run_smoke() ---")
    ledger_report = build_ledger.run_smoke()
    ledger = pl.read_parquet(build_ledger.SMOKE_LEDGER_PARQUET)

    check("Stage A produced rows", ledger.height > 0, f"{ledger.height} rows")

    check("no expiry > 2026-06-12", ledger.filter(pl.col("expiry") > date(2026, 6, 12)).height == 0)
    check("entry_dow in {Mon,Tue}", ledger.filter(~pl.col("entry_dow").is_in(["Mon", "Tue"])).height == 0)

    mismatch = ledger.filter((pl.col("no_later_print") == 1) != pl.col("growth_mult").is_null())
    check("growth_mult null iff no_later_print", mismatch.height == 0, f"{mismatch.height} mismatches")

    bad_high_date = ledger.filter(
        pl.col("max_high_date").is_not_null() & (pl.col("max_high_date") <= pl.col("entry_date"))
    )
    check("max_high_date > entry_date (all non-null)", bad_high_date.height == 0, f"{bad_high_date.height} violations")

    mon = ledger.filter(pl.col("entry_dow") == "Mon")
    tue = ledger.filter(pl.col("entry_dow") == "Tue")
    mon_bad = mon.filter((pl.col("dte_trading") < 1) | (pl.col("dte_trading") > 4)).height
    tue_bad = tue.filter((pl.col("dte_trading") < 1) | (pl.col("dte_trading") > 3)).height
    check("dte_trading in [1,4] for Mon entries", mon_bad == 0, f"{mon_bad} out-of-range of {mon.height}")
    check("dte_trading in [1,3] for Tue entries", tue_bad == 0, f"{tue_bad} out-of-range of {tue.height}")

    # ---- Good Friday week (expiry_day 2024-03-28) ----
    all_weeks = build_ledger.load_in_scope_weeks()
    gf_wm = build_ledger.week_monday_of(date(2024, 3, 25))
    gf_sessions = all_weeks[gf_wm]
    gf_ledger = ledger.filter(pl.col("week_monday") == gf_wm)
    check("Good Friday week: expiry_day is Thursday 2024-03-28",
          gf_ledger.height > 0 and gf_ledger["expiry_day"].unique().to_list() == [date(2024, 3, 28)])
    check("Good Friday week: nonzero contracts", gf_ledger.height > 0, f"{gf_ledger.height} ledger rows")
    gf_tape = build_ledger.read_week_tape(gf_sessions)
    n_bad_fri = gf_tape.filter(pl.col("expiry") == date(2024, 3, 29)).height
    check("Good Friday week: ~zero rows with expiry==2024-03-29 (nonexistent holiday)",
          n_bad_fri < 10, f"{n_bad_fri} rows (threshold <10, 'approximately zero' per BUILD_BRIEF)")

    # ---- MLK week (expiry_day 2025-01-24) ----
    mlk_wm = build_ledger.week_monday_of(date(2025, 1, 20))
    mlk_ledger = ledger.filter(pl.col("week_monday") == mlk_wm)
    n_mon = mlk_ledger.filter(pl.col("entry_dow") == "Mon").height
    n_tue = mlk_ledger.filter(pl.col("entry_dow") == "Tue").height
    check("MLK week: zero Monday entries (2025-01-20 holiday)", n_mon == 0, f"{n_mon} Mon rows")
    check("MLK week: nonzero Tuesday entries", n_tue > 0, f"{n_tue} Tue rows")

    # ---- NVDA week top-20 tables (raw + tradeable) ----
    nvda_wm = build_ledger.week_monday_of(date(2024, 5, 20))
    nvda_week = ledger.filter((pl.col("week_monday") == nvda_wm) & (pl.col("underlying") == "NVDA"))
    check("NVDA smoke week has rows", nvda_week.height > 0, f"{nvda_week.height} rows")

    table_cols = ["ticker", "underlying", "cp", "strike", "entry_dow", "entry_close",
                  "entry_volume", "max_future_high", "growth_mult"]
    top20_raw = nvda_week.sort("growth_mult", descending=True, nulls_last=True).head(20)
    tradeable = nvda_week.filter(
        (pl.col("entry_close") >= 0.20) & (pl.col("entry_volume") >= 100)
        & (pl.col("entry_transactions") >= 10) & (~pl.col("adjusted"))
    )
    top20_tradeable = tradeable.sort("growth_mult", descending=True, nulls_last=True).head(20)
    RESULTS_EXTRA = {
        "nvda_week_total_rows": nvda_week.height,
        "nvda_week_tradeable_rows": tradeable.height,
    }
    check("NVDA tradeable-floor top-20 is call-dominated (matches earnings-pop narrative)",
          (top20_tradeable["cp"] == "C").sum() >= 15,
          f"{(top20_tradeable['cp'] == 'C').sum()}/{top20_tradeable.height} calls")

    # ---- independent-path spot check: 3 sampled contracts from the NVDA tradeable top-20 ----
    log("\n--- Independent-path spot check (raw gzip vs index-derived ledger) ---")
    sample_rows = top20_tradeable.head(3)
    sample_tickers = sample_rows["ticker"].to_list()
    entry_date_by_ticker = dict(zip(sample_rows["ticker"].to_list(), sample_rows["entry_date"].to_list()))
    nvda_expiry_day = date(2024, 5, 24)
    nvda_sessions = all_weeks[nvda_wm]
    independent_path_spot_check(ledger, sample_tickers, entry_date_by_ticker, nvda_expiry_day, nvda_sessions)

    # ---------------- Stage B ----------------
    log("\n--- Stage B: build_features.run_smoke() ---")
    features_report = build_features.run_smoke()

    recon = features_report["recon_worst_relerr"]
    for p in (21, 50, 200):
        v = recon["sma"][p]
        check(f"MA reconciliation sma{p}: relerr < 1e-3", v is not None and v < 1e-3, f"worst={v}")
    for p in (21, 50, 200):
        v = recon["ema"][p]
        check(f"MA reconciliation ema{p}: relerr < 2%", v is not None and v < 0.02, f"worst={v}")

    check("earnings flag correct for NVDA 2024-05-22 (entry 2024-05-20 week)",
          features_report["nvda_earnings_in_window_20240520"] == 1,
          f"got {features_report['nvda_earnings_in_window_20240520']}")

    n_checked = features_report["moneyness_checked"]
    n_oob = features_report["moneyness_oob_count"]
    oob_rate = (n_oob / n_checked) if n_checked else 0.0
    check("moneyness sanity band (-0.9, 10): OOB rate < 1%", oob_rate < 0.01,
          f"{n_oob}/{n_checked} = {oob_rate:.4%} OOB (deep-ITM legacy strikes, see BUILD_REPORT.md)")
    log(f"  moneyness null_count (reported, not asserted): {features_report['moneyness_null_count']}")

    check("Stage B covered/uncovered split exercised (>=1 uncovered index root)",
          len(features_report["uncovered_symbols"]) >= 1,
          f"uncovered={features_report['uncovered_symbols']}")

    # ---------------- write BUILD_REPORT.md tables ----------------
    raw_table_ascii = ascii_table(top20_raw, table_cols, float_cols=("strike", "entry_close", "max_future_high", "growth_mult"))
    tradeable_table_ascii = ascii_table(top20_tradeable, table_cols, float_cols=("strike", "entry_close", "max_future_high", "growth_mult"))
    _write_smoke_tables_section(raw_table_ascii, tradeable_table_ascii, ledger_report, features_report, RESULTS_EXTRA)

    # ---------------- summary ----------------
    elapsed = time.time() - t0
    n_fail = sum(1 for _, p, _ in RESULTS if not p)
    log("\n" + "=" * 78)
    log(f"SMOKE SUMMARY: {len(RESULTS) - n_fail}/{len(RESULTS)} checks passed in {elapsed:.1f}s")
    for name, passed, detail in RESULTS:
        log(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    log("=" * 78)

    return 0 if n_fail == 0 else 1


def _write_smoke_tables_section(raw_ascii: str, tradeable_ascii: str, ledger_report: dict, features_report: dict,
                                 nvda_counts: dict) -> None:
    """Appends/replaces a machine-written section in BUILD_REPORT.md with the smoke
    tables. The rest of BUILD_REPORT.md is hand-authored; this function only owns the
    block between the two marker lines."""
    begin_marker = "<!-- SMOKE_TABLES_BEGIN (written by smoke_test.py -- do not hand-edit) -->"
    end_marker = "<!-- SMOKE_TABLES_END -->"

    block_lines = [
        begin_marker,
        "",
        "### NVDA smoke week (2024-05-20 Mon / 2024-05-21 Tue entries, expiry 2024-05-24) -- top-20 by growth_mult, RAW view (no floor)",
        "",
        "```",
        raw_ascii,
        "```",
        "",
        "Note: the RAW top-20 is dominated by penny-premium puts (entry_close $0.01-$0.15) whose",
        "huge multiples are the penny-premium artifact PREREG's own Honesty section anticipates --",
        "not a signal that puts outperformed calls that week. See the TRADEABLE view below.",
        "",
        "### NVDA smoke week -- top-20 by growth_mult, TRADEABLE view (entry_close>=$0.20, volume>=100, transactions>=10, standard OCC root)",
        "",
        "```",
        tradeable_ascii,
        "```",
        "",
        f"NVDA smoke week: {nvda_counts['nvda_week_total_rows']} total NVDA ledger rows "
        f"(all entries, both Mon+Tue), {nvda_counts['nvda_week_tradeable_rows']} pass the TRADEABLE floor. "
        f"(The week's grand total across ALL underlyings, {ledger_report['per_week'][0]['entries'] if ledger_report['per_week'] else 'n/a'}, "
        f"is reported in the row-count table below.)",
        "",
        "### Smoke row counts per week",
        "",
        "```",
        ascii_table(
            pl.DataFrame(ledger_report["per_week"]),
            ["week_monday", "expiry_day", "is_monthly_opex", "entries", "contracts",
             "excluded_nonstandard_expiry", "zero_close_drops"],
        ),
        "```",
        "",
        "### Stage B smoke summary",
        "",
        f"- smoke symbols ({len(features_report['smoke_symbols'])}): {features_report['smoke_symbols']}",
        f"- covered ({len(features_report['covered_symbols'])}): {features_report['covered_symbols']}",
        f"- uncovered/covered=0 ({len(features_report['uncovered_symbols'])}): {features_report['uncovered_symbols']}",
        f"- panel rows: {features_report['panel_rows']}, analysis rows: {features_report['analysis_rows']} "
        f"({features_report['analysis_rows_covered']} covered)",
        f"- MA reconciliation worst relerr (entry-event grain, n={features_report['recon_n_sampled']}): "
        f"{features_report['recon_worst_relerr']}",
        f"- moneyness: null_count={features_report['moneyness_null_count']}, "
        f"oob_count={features_report['moneyness_oob_count']} of {features_report['moneyness_checked']} checked",
        "",
        end_marker,
    ]
    block = "\n".join(block_lines)

    existing = ""
    if os.path.exists(BUILD_REPORT_PATH):
        with open(BUILD_REPORT_PATH, "r", encoding="ascii", errors="replace") as f:
            existing = f.read()

    if begin_marker in existing and end_marker in existing:
        pre = existing.split(begin_marker)[0]
        post = existing.split(end_marker)[1]
        new_content = pre + block + post
    elif existing:
        new_content = existing.rstrip() + "\n\n" + block + "\n"
    else:
        new_content = f"# BUILD_REPORT -- weekly_5dte_movers\n\n{block}\n"

    tmp = BUILD_REPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="ascii", errors="replace") as f:
        f.write(new_content)
    os.replace(tmp, BUILD_REPORT_PATH)
    log(f"\nwrote smoke tables into {BUILD_REPORT_PATH}")


if __name__ == "__main__":
    raise SystemExit(main())
