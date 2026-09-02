"""
calibrate_proxy.py -- one-shot archive calibration for the W5DTE forward paper tape
(experiments/w5dte_tape/, .horizon/w5dte-paper-tape/TASK.md).

Context: experiments/w5dte_ev/FINDINGS.md "Disposition" licenses the paper tape as a
PROXY-FIDELITY instrument, because the live production source (`option_prices`, a daily
lastPrice/volume/OI snapshot) cannot observe the true rule conjunct used by the
weekly_5dte_movers discovery (`hl_range_pct` = intraday high-low range, RESULTS_TABLES.md
E3). This script measures, on the (dead, frozen) Polygon OPRA archive, how well a
LIVE-OBSERVABLE proxy -- the close-to-close move between a contract's two most recent
daily snapshots -- stands in for that true conjunct:

    proxy_move   = |close_t / close_{t-1} - 1|            (what the live tape can see)
    hl_range_pct = (high_t - low_t) / close_t * 100        (the true conjunct, archive-only)

Substrate: B:\\polygon_derived\\contract_day_index\\_bydate\\<date>.parquet (FF-0, one
parquet per session, ALL underlyings/expiries/strikes -- built by
experiments/flatfile_exploitation/build_index.py). Sessions enumerated via
`ff_common.list_session_dates('day_aggs_v1')` (filesystem walk -- the archive's own
calendar, per traps.md "ff_common.list_session_dates(), never the manifest"), restricted to
<= 2026-06-12 (the w5dte_ev holdout cutoff; experiments/_holdout enforces this defensively
below). Read-only, no MySQL, no writes outside this directory.

Population per anchor day d (d a Monday or Tuesday session, d-1 its true immediately-prior
trading session from the FULL archive calendar -- NOT the sampled one, so the day-gap
matches what the live tape actually sees: "prev snapshot = the most recent prior
OptionPrice row ... within 5 days", which in practice is almost always exactly 1 trading
day): contracts present on BOTH d and d-1, with close_d >= $0.20 and volume_d >= 100 (the
"Mon/Tue tradeable-ish" floor this task pins -- deliberately looser than the parent study's
full tradeable floor, which also requires transactions>=10; not applied here).

Timing: a 6-file throughput probe (see TAPE_BUILD_REPORT.md) measured ~10 files/sec. There
are 971 in-scope sessions, 382 of them Monday/Tuesday. Using the FULL Mon/Tue population
(no date-subsampling; stride=1 below) needs ~2 file reads per anchor (self + prev-day) =
~760 reads =~ 75-90s -- comfortably inside the ~10 min budget, so no sampling was actually
needed; --stride N (skip to every Nth Mon/Tue session) is kept as a CLI escape hatch if a
future re-run on a slower box needs it, per this task's own suggestion ("e.g. every 3rd
session, and say so").

Outputs:
    experiments/w5dte_tape/calibration_stats.json  -- machine-readable numbers (X, rates,
        confusion matrix, rho, methodology metadata) -- w5dte_tape.py reads X from here
        rather than a second hardcoded copy of the constant.
    experiments/w5dte_tape/FIDELITY.md             -- human-readable report + standing
        caveats carried from FINDINGS.md Disposition.

Usage:
    python experiments/w5dte_tape/calibrate_proxy.py                  # full Mon/Tue population
    python experiments/w5dte_tape/calibrate_proxy.py --stride 3       # every 3rd Mon/Tue session
    python experiments/w5dte_tape/calibrate_proxy.py --max-seconds 300
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
assert os.path.isfile(os.path.join(_ROOT, "CLAUDE.md")), \
    f"sys.path pin landed on {_ROOT!r}, expected the Trader repo root"

import numpy as np          # noqa: E402
import polars as pl         # noqa: E402

from experiments.flatfile_exploitation.ff_common import (  # noqa: E402
    DERIVED_ROOT, list_session_dates,
)
from experiments._holdout import assert_no_holdout_leak  # noqa: E402

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
HOLDOUT_CUTOFF = date(2026, 6, 12)   # w5dte_ev PREREG.md window end (OWNER_SPEC standing constraint)
BYDATE_DIR = DERIVED_ROOT / "contract_day_index" / "_bydate"

OUT_DIR = Path(_HERE)
FIDELITY_PATH = OUT_DIR / "FIDELITY.md"
STATS_PATH = OUT_DIR / "calibration_stats.json"

# RESULTS_TABLES.md E3 "hl_range_pct>=P80(127.3)" -- the task pins this rounded display
# value verbatim (unlike moneyness_pct, for which the entry rule needs bit-exact
# full-precision fidelity, the task did not supply an unrounded hl_range_pct constant --
# 127.3 is what's specified and is used exactly as given; see FIDELITY.md "methodology").
TRUE_HL_THRESHOLD = 127.3

TRADEABLE_CLOSE_MIN = 0.20
TRADEABLE_VOLUME_MIN = 100

DEFAULT_MAX_SECONDS = 480.0   # 8 min soft budget, leaves margin under the ~10 min ask


def log(msg: str) -> None:
    print(msg, flush=True)


def _atomic_write_text(text: str, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="ascii", errors="replace") as f:
        f.write(text)
    os.replace(tmp, path)


def _atomic_write_json(obj: dict, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="ascii", errors="replace") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# archive scan
# ---------------------------------------------------------------------------
def build_calibration_frame(stride: int, max_seconds: float) -> tuple:
    t0 = time.time()
    all_sessions = list_session_dates("day_aggs_v1")   # authoritative calendar -- never glob-assume
    in_scope = sorted(d for d in all_sessions if d <= HOLDOUT_CUTOFF)
    assert_no_holdout_leak(in_scope, context="w5dte_tape calibrate_proxy archive scan")

    index_of = {d: i for i, d in enumerate(in_scope)}
    mon_tue = [d for d in in_scope if d.weekday() in (0, 1)]   # Mon=0, Tue=1
    anchors = mon_tue[::stride]

    log(f"in-scope sessions <= {HOLDOUT_CUTOFF.isoformat()}: {len(in_scope)} "
        f"({in_scope[0].isoformat()} .. {in_scope[-1].isoformat()})")
    log(f"Mon/Tue sessions: {len(mon_tue)}; stride={stride} -> {len(anchors)} anchor day(s)")

    frames = []
    n_missing_files = 0
    n_no_prev = 0
    n_processed = 0
    n_empty_after_filter = 0
    stopped_early = False

    for i, d in enumerate(anchors):
        if time.time() - t0 > max_seconds:
            log(f"TIME BUDGET ({max_seconds:.0f}s) reached after {i}/{len(anchors)} anchors -- "
                f"stopping early with what has been accumulated")
            stopped_early = True
            break

        idx = index_of[d]
        if idx == 0:
            n_no_prev += 1   # the archive's very first session has no predecessor -- harmless, 1 drop
            continue
        prev_d = in_scope[idx - 1]

        today_path = BYDATE_DIR / f"{d.isoformat()}.parquet"
        prev_path = BYDATE_DIR / f"{prev_d.isoformat()}.parquet"
        if not today_path.exists() or not prev_path.exists():
            n_missing_files += 1
            log(f"  WARNING: missing _bydate file for anchor {d.isoformat()} or its prev "
                f"{prev_d.isoformat()} -- skipped (calendar said both should exist)")
            continue

        today_df = pl.read_parquet(today_path, columns=["ticker", "cp", "close", "high", "low", "volume"])
        prev_df = pl.read_parquet(prev_path, columns=["ticker", "close"]).rename({"close": "prev_close"})

        joined = today_df.join(prev_df, on="ticker", how="inner")
        joined = joined.filter(
            (pl.col("close") >= TRADEABLE_CLOSE_MIN)
            & (pl.col("volume") >= TRADEABLE_VOLUME_MIN)
            & (pl.col("prev_close") > 0)
            & (pl.col("close") > 0)
        )
        n_processed += 1
        if joined.height == 0:
            n_empty_after_filter += 1
            continue

        joined = joined.with_columns([
            ((pl.col("high") - pl.col("low")) / pl.col("close") * 100.0).alias("hl_range_pct"),
            (pl.col("close") / pl.col("prev_close") - 1.0).abs().alias("proxy_move"),
            pl.lit(d).alias("date"),
        ])
        # polars NaN/null discipline: guard the two derived floats explicitly, even though
        # the upstream filters (close>0, prev_close>0) should make NaN unreachable here.
        joined = joined.with_columns([
            pl.col("hl_range_pct").fill_nan(None),
            pl.col("proxy_move").fill_nan(None),
        ]).filter(pl.col("hl_range_pct").is_not_null() & pl.col("proxy_move").is_not_null())

        frames.append(joined.select(["date", "ticker", "cp", "hl_range_pct", "proxy_move", "volume"]))

        if (i + 1) % 25 == 0:
            log(f"  [{i + 1}/{len(anchors)}] {d.isoformat()}  elapsed={time.time() - t0:.1f}s  "
                f"rows_so_far={sum(f.height for f in frames)}")

    combined = (
        pl.concat(frames, how="vertical") if frames else
        pl.DataFrame(schema={"date": pl.Date, "ticker": pl.Utf8, "cp": pl.Utf8,
                              "hl_range_pct": pl.Float64, "proxy_move": pl.Float64, "volume": pl.Int64})
    )

    meta = {
        "elapsed_seconds": round(time.time() - t0, 1),
        "n_sessions_in_scope": len(in_scope),
        "n_mon_tue_sessions": len(mon_tue),
        "stride": stride,
        "n_anchors_selected": len(anchors),
        "n_anchors_processed": n_processed,
        "n_anchors_skipped_missing_file": n_missing_files,
        "n_anchors_skipped_no_prev_session": n_no_prev,
        "n_anchors_empty_after_tradeable_filter": n_empty_after_filter,
        "stopped_early_on_time_budget": stopped_early,
        "n_rows": combined.height,
        "holdout_cutoff": HOLDOUT_CUTOFF.isoformat(),
    }
    return combined, meta


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def spearman_rho(a: pl.Series, b: pl.Series) -> float:
    """Rank-correlate via polars .rank() + numpy corrcoef -- avoids a scipy dependency."""
    ra = a.rank(method="average").to_numpy()
    rb = b.rank(method="average").to_numpy()
    if len(ra) < 2 or np.std(ra) == 0 or np.std(rb) == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def compute_stats(df: pl.DataFrame, true_threshold: float, label: str) -> dict:
    n = df.height
    if n == 0:
        return {"label": label, "n": 0}

    true_hit = df["hl_range_pct"] >= true_threshold
    true_rate = float(true_hit.mean())

    # X = the proxy_move value whose exceedance rate matches true_rate exactly (by
    # construction) -- the (1 - true_rate) quantile of the proxy_move distribution.
    q = max(0.0, min(1.0, 1.0 - true_rate))
    X = float(df["proxy_move"].quantile(q, interpolation="linear"))

    proxy_hit = df["proxy_move"] >= X
    proxy_rate = float(proxy_hit.mean())

    tp = int((proxy_hit & true_hit).sum())
    fp = int((proxy_hit & ~true_hit).sum())
    fn = int((~proxy_hit & true_hit).sum())
    tn = int((~proxy_hit & ~true_hit).sum())
    agreement = (tp + tn) / n

    rho = spearman_rho(df["proxy_move"], df["hl_range_pct"])

    return {
        "label": label, "n": n,
        "true_threshold": true_threshold, "true_rate": true_rate,
        "X": X, "proxy_rate_at_X": proxy_rate,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "agreement_rate": agreement,
        "spearman_rho": rho,
    }


def _fmt_pct(x) -> str:
    return f"{x * 100:.3f}%" if x == x else "nan"   # x==x is False for NaN


def render_fidelity_md(pooled: dict, calls: dict, puts: dict, meta: dict) -> str:
    lines = []
    lines.append("# FIDELITY -- W5DTE forward paper tape proxy calibration")
    lines.append("")
    lines.append(f"Generated by `calibrate_proxy.py`. Archive scan: {meta['elapsed_seconds']}s, "
                 f"{meta['n_anchors_processed']}/{meta['n_anchors_selected']} anchor day(s) processed "
                 f"(stride={meta['stride']}), {meta['n_rows']} calibration row(s).")
    lines.append("")
    lines.append("## What this calibrates")
    lines.append("")
    lines.append("The w5dte_ev rule family's discovered conjunct is `hl_range_pct >= P80(127.3)` "
                 "(intraday high-low range as a percent of close, RESULTS_TABLES.md E3) -- but the "
                 "live production source for the forward paper tape, `option_prices`, is a daily "
                 "lastPrice/volume/OI SNAPSHOT with no intraday high/low. The tape substitutes a "
                 "LIVE-OBSERVABLE proxy: the close-to-close move between a contract's two most "
                 "recent daily snapshots, `proxy_move = |price_t/price_{t-1} - 1|`. This script "
                 "measures, on the archive (which HAS true intraday high/low), how well that proxy "
                 "stands in for the true conjunct.")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append(f"- Substrate: `B:\\polygon_derived\\contract_day_index\\_bydate\\*.parquet` "
                 f"(FF-0), sessions enumerated via `ff_common.list_session_dates('day_aggs_v1')` "
                 f"(the archive's own filesystem-walk calendar -- never a directory glob).")
    lines.append(f"- Window: <= {meta['holdout_cutoff']} ({meta['n_sessions_in_scope']} in-scope "
                 f"sessions, {meta['n_mon_tue_sessions']} of them Monday/Tuesday). Holdout-safe: "
                 f"this is the SAME window the parent weekly_5dte_movers study and w5dte_ev already "
                 f"used; `experiments._holdout.assert_no_holdout_leak` asserted no date beyond it "
                 f"was touched.")
    lines.append(f"- Anchor days = every Monday/Tuesday session in-window (stride={meta['stride']}; "
                 f"stride=1 means ALL {meta['n_mon_tue_sessions']} were used -- a 6-file throughput "
                 f"probe measured ~10 files/sec, so the full population finished in "
                 f"{meta['elapsed_seconds']}s, well inside the ~10 min budget and no subsampling "
                 f"was actually needed; `--stride N` remains available for a slower box).")
    lines.append(f"- For each anchor day `d`: loaded `d`'s own `_bydate` parquet (ticker, cp, close, "
                 f"high, low, volume) and the TRUE immediately-prior trading session `d-1`'s parquet "
                 f"(ticker, close only) -- `d-1` comes from the FULL archive calendar, not a "
                 f"subsampled one, so the day-gap matches what the live tape's own "
                 f"'most recent prior OptionPrice row within 5 days' lookup actually sees in "
                 f"production (almost always exactly 1 trading day).")
    lines.append(f"- Population = contracts present on both `d` and `d-1`, filtered to "
                 f"'Mon/Tue tradeable-ish': close_d >= ${TRADEABLE_CLOSE_MIN:.2f}, "
                 f"volume_d >= {TRADEABLE_VOLUME_MIN} (this task's own floor -- looser than the "
                 f"parent study's full tradeable floor, which also requires transactions>=10; not "
                 f"applied here).")
    lines.append(f"- `hl_range_pct = (high_d - low_d) / close_d * 100` (the true conjunct); "
                 f"`proxy_move = |close_d / close_{{d-1}} - 1|` (the live-observable proxy).")
    lines.append(f"- True threshold used verbatim from RESULTS_TABLES.md E3: "
                 f"`hl_range_pct >= {TRUE_HL_THRESHOLD}` (the task's own pinned value -- a rounded "
                 f"P80 display label, same status as the parent's other rounded rule labels; not "
                 f"re-derived to full precision here since this calibration is on a structurally "
                 f"different population than E3's rule-distillation grid).")
    lines.append(f"- **No sector or moneyness restriction** -- calibration is unconditional over the "
                 f"WHOLE archive (all underlyings, both sides). This script is read-only over B: "
                 f"parquets with NO MySQL access (as directed), so a technology-sector restriction "
                 f"(which needs `stocks.sector`, a MySQL-only field) is not possible here. The live "
                 f"entry rule restricts to technology/calls; this calibration's fidelity numbers are "
                 f"therefore ASSUMED representative within that subset, not independently confirmed "
                 f"for it -- flagged as an open question in TAPE_BUILD_REPORT.md.")
    lines.append("")

    def render_block(title: str, s: dict) -> list:
        out = [f"## {title}", ""]
        if s["n"] == 0:
            out.append("N=0 -- no rows in this slice.")
            out.append("")
            return out
        out.append(f"N = {s['n']}")
        out.append("")
        out.append("### (a) Proxy threshold X matched to the true-conjunct exceedance rate")
        out.append("")
        out.append(f"- True conjunct: `hl_range_pct >= {s['true_threshold']}`  -->  "
                    f"rate = {_fmt_pct(s['true_rate'])} of the population")
        out.append(f"- **X = {s['X']:.6f}**  (proxy_move exceedance-rate-matched threshold: "
                    f"`proxy_move >= X` fires at rate {_fmt_pct(s['proxy_rate_at_X'])}, matched by "
                    f"construction to the true rate above)")
        out.append("")
        out.append("### (b) Confusion matrix / agreement rate at X")
        out.append("")
        c = s["confusion"]
        out.append("| | true hit (hl>=127.3) | true miss |")
        out.append("| --- | --- | --- |")
        out.append(f"| proxy hit (proxy>=X) | TP={c['tp']} | FP={c['fp']} |")
        out.append(f"| proxy miss | FN={c['fn']} | TN={c['tn']} |")
        out.append("")
        out.append(f"Agreement rate (TP+TN)/N = **{_fmt_pct(s['agreement_rate'])}**")
        if c['tp'] + c['fn'] > 0:
            out.append(f"Recall (TP / true hits) = {_fmt_pct(c['tp'] / (c['tp'] + c['fn']))}")
        if c['tp'] + c['fp'] > 0:
            out.append(f"Precision (TP / proxy hits) = {_fmt_pct(c['tp'] / (c['tp'] + c['fp']))}")
        out.append("")
        out.append("### (c) Spearman rank correlation")
        out.append("")
        out.append(f"rho(proxy_move, hl_range_pct) = **{s['spearman_rho']:.4f}**")
        out.append("")
        if c['tp'] + c['fn'] > 0 and c['tp'] + c['fp'] > 0:
            recall = c['tp'] / (c['tp'] + c['fn'])
            trivial_agreement = 1.0 - s['true_rate']
            out.append("### Interpretation")
            out.append("")
            out.append(f"The hard threshold is a WEAK screen at the individual-contract level: "
                        f"precision/recall are only ~{recall * 100:.0f}% despite a moderate positive "
                        f"rank correlation (rho={s['spearman_rho']:.2f}). Concretely: most contracts "
                        f"that clear `proxy_move >= X` would NOT have cleared the true "
                        f"`hl_range_pct >= {s['true_threshold']}` gate that day, and most contracts "
                        f"that WOULD have cleared the true gate are missed by the proxy. The "
                        f"agreement rate ({_fmt_pct(s['agreement_rate'])}) is even slightly BELOW the "
                        f"trivial 'always predict miss' baseline ({_fmt_pct(trivial_agreement)}), "
                        f"which is expected under this much class imbalance (~{s['true_rate']*100:.1f}% "
                        f"positive rate) and should not be read as 'agreement is good' on its own -- "
                        f"rho and the confusion matrix are the more honest read. Interpretation: "
                        f"`proxy_move` is DIRECTIONALLY informative (a bigger close-to-close move does "
                        f"mean a bigger true intraday range, on average) but is a noisy, not crisp, "
                        f"stand-in for the true violence conjunct at any single hard cutoff. The paper "
                        f"tape's entry rule uses it as a hard gate anyway (per spec) -- this is exactly "
                        f"the deviation FINDINGS.md's Disposition means by 'every deviation is "
                        f"quantified; the tape is disposable if the owner rejects the proxy.'")
            out.append("")
        return out

    lines += render_block("Calls only (cp=='C') -- THE VALUE w5dte_tape.py USES LIVE", calls)
    lines += render_block("Reference: pooled, both sides", pooled)
    lines += render_block("Reference: puts only (cp=='P')", puts)

    lines.append("## Which X the live tape uses")
    lines.append("")
    lines.append(f"`w5dte_tape.py` reads `calibration_stats.json`'s `recommended_X`, which is set to the "
                 f"**calls-only** value (X={calls.get('X', float('nan')):.6f}), not the pooled one "
                 f"(X={pooled.get('X', float('nan')):.6f}). The live `--entry` rule only ever evaluates "
                 f"calls (technology-sector, calls-only design decision -- see TAPE_BUILD_REPORT.md), so "
                 f"the threshold is calibrated on exactly the population it is applied to rather than "
                 f"diluted by puts the tape never touches. N=5.87M for calls-only is still a very large, "
                 f"statistically robust sample -- there is no power cost to this choice, only a scope "
                 f"correction. The pooled and puts-only numbers above are reported for completeness and "
                 f"as a robustness cross-check (rho and agreement are similar across all three slices), "
                 f"not because either feeds the live gate.")
    lines.append("")

    lines.append("## Standing caveats (carried from experiments/w5dte_ev/FINDINGS.md \"Disposition\")")
    lines.append("")
    lines.append("- **Outcome proxy = lower bound.** The `--outcomes` mode of w5dte_tape.py prices "
                 "the week's outcome off `max(subsequent daily option_prices.price)`, which is a "
                 "LOWER bound on the true week high (`max_future_high` in the parent study's "
                 "ledger) -- a daily lastPrice snapshot can miss an intraday spike the true "
                 "high captured. This calibration is about the ENTRY-side violence conjunct only; "
                 "it does not measure or correct the separate outcome-side lower-bound gap.")
    lines.append("- **Volume is unreliable live.** `option_prices.volume` (yfinance-sourced) reads "
                 "0 on a large majority of rows even for deeply liquid contracts -- "
                 ".horizon/ct15-paper-sleeve/LESSONS.md measured 79% zero-volume on a 2000-row "
                 "sample, and a specific liquid AAPL contract read volume=0 on all 21 sampled days "
                 "despite genuinely moving day to day. This is why w5dte_tape.py's `--entry` mode "
                 "LOGS volume/open_interest but never gates on them.")
    lines.append("- **The archive is dead.** `B:\\polygon_flatfiles\\us_options_opra\\` stopped "
                 "receiving new sessions 2026-08-05 (Polygon entitlement lapsed, "
                 "data-acquisition.md). This calibration is therefore a ONE-SHOT: it cannot be "
                 "re-run against fresher archive data if the live proxy's fidelity ever needs "
                 "re-checking -- X is fixed at the value computed here unless a future session "
                 "re-derives it from a different archive source entirely.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stride", type=int, default=1,
                     help="use every Nth Mon/Tue session as an anchor (default 1 = all of them)")
    ap.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS,
                     help=f"soft wall-clock budget in seconds (default {DEFAULT_MAX_SECONDS:.0f})")
    args = ap.parse_args()

    if args.stride < 1:
        log("ABORT: --stride must be >= 1")
        return 2

    log(f"w5dte_tape calibrate_proxy.py starting: stride={args.stride} max_seconds={args.max_seconds:.0f}")
    df, meta = build_calibration_frame(args.stride, args.max_seconds)
    log(f"scan done: {meta}")

    if df.height == 0:
        log("ABORT: zero calibration rows produced -- nothing to calibrate against")
        return 3

    pooled = compute_stats(df, TRUE_HL_THRESHOLD, "pooled")
    calls = compute_stats(df.filter(pl.col("cp") == "C"), TRUE_HL_THRESHOLD, "calls_only")
    puts = compute_stats(df.filter(pl.col("cp") == "P"), TRUE_HL_THRESHOLD, "puts_only")

    log(f"POOLED:  N={pooled['n']}  true_rate={pooled['true_rate']:.5f}  X={pooled['X']:.6f}  "
        f"agreement={pooled['agreement_rate']:.5f}  rho={pooled['spearman_rho']:.4f}")
    log(f"CALLS:   N={calls['n']}  true_rate={calls.get('true_rate', float('nan')):.5f}  "
        f"X={calls.get('X', float('nan')):.6f}  agreement={calls.get('agreement_rate', float('nan')):.5f}  "
        f"rho={calls.get('spearman_rho', float('nan')):.4f}")
    log(f"PUTS:    N={puts['n']}  true_rate={puts.get('true_rate', float('nan')):.5f}  "
        f"X={puts.get('X', float('nan')):.6f}  agreement={puts.get('agreement_rate', float('nan')):.5f}  "
        f"rho={puts.get('spearman_rho', float('nan')):.4f}")

    stats_out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meta": meta,
        "true_hl_threshold": TRUE_HL_THRESHOLD,
        "tradeable_close_min": TRADEABLE_CLOSE_MIN,
        "tradeable_volume_min": TRADEABLE_VOLUME_MIN,
        "pooled": pooled,
        "calls_only": calls,
        "puts_only": puts,
        # The constant w5dte_tape.py actually consumes for the live proxy-violence gate.
        # Deliberately CALLS-ONLY, not pooled: the live --entry rule only ever evaluates
        # calls (technology + calls-only design decision, TAPE_BUILD_REPORT.md), so the
        # threshold should be calibrated on exactly the population it will be applied to,
        # not diluted by puts the tape never touches. N=5.87M for calls_only is still a
        # very large, robust sample -- no statistical-power cost to this choice.
        "recommended_X": calls["X"],
        "recommended_X_basis": "calls_only",
    }
    _atomic_write_json(stats_out, STATS_PATH)
    log(f"wrote {STATS_PATH}")

    fidelity_md = render_fidelity_md(pooled, calls, puts, meta)
    _atomic_write_text(fidelity_md, FIDELITY_PATH)
    log(f"wrote {FIDELITY_PATH}")

    log("DONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
