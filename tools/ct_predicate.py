"""tools/ct_predicate.py -- the ONE shared CT_PROMOTE qualification predicate.

WHY THIS MODULE EXISTS
-----------------------
`experiments/ctsl_vehicle_2026_08/V0_DOSSIER.md` ("THE NAME IS WRONG. The
vehicle's carrier is CT_PROMOTE, not CTSL.") traced the counter-trend cascade
promotion mechanism to `ct_tag()` -- a small predicate that exists as TWO
independent, hand-maintained copies in production:

  * `monte_carlo.py`      (the 30-DTE research/paper-sleeve MC engine)
  * `backtest_cascade.py` (the 30-DTE DETERMINISTIC engine that drives the
                            live Portfolio ledger via portfolio_engine.py)

Both copies are logically identical today (verified below) but are two
separate literals, not one function imported twice -- exactly the kind of
silent-drift risk traps.md's "config drift" trap family warns about. This
module is a byte-for-byte verified port used by BOTH consumers downstream
(`.horizon/ct15-paper-sleeve/` and `.horizon/ledger-ct-instrument/`) so there
is exactly ONE place the predicate can drift from, and a built-in self-check
(`verify_against_production()`) that catches it if the two production copies
ever disagree with each other or with this port.

PROVENANCE (file:line, verified against HEAD 2026-08-16, v74 `f9fb7b934` active)
----------------------------------------------------------------------------
Source A -- monte_carlo.py (research/MC engine; `_cfg = strategy_config.STRATEGY_30DTE`
at monte_carlo.py:66-67):
    monte_carlo.py:1345   OVERFLOW_THRESHOLD = _cfg.OVERFLOW_THRESHOLD          (=70)
    monte_carlo.py:1447   PUT_THRESHOLD = int(os.environ.get(
                             'PUT_THRESHOLD_OVERRIDE', str(_cfg.PUT_THRESHOLD))) (=25)
    monte_carlo.py:1539   CT_PROMOTE = os.environ.get('CT_PROMOTE', '1') == '1'
    monte_carlo.py:1540   CT_PUT_TREND_MIN = int(os.environ.get('CT_PUT_TREND_MIN', '80'))
    monte_carlo.py:1541   CT_CALL_TREND_MAX = int(os.environ.get('CT_CALL_TREND_MAX', '20'))
    monte_carlo.py:1542   CT_CALL_TIER = os.environ.get('CT_CALL_TIER', 'ultra')
    monte_carlo.py:1543   CT_PUT_TIER = os.environ.get('CT_PUT_TIER', 'put_top')
    monte_carlo.py:1546-1554:
        def ct_tag(overall, trend, side):
            if not CT_PROMOTE or trend is None:
                return None
            if side == 'put' and overall <= PUT_THRESHOLD and trend >= CT_PUT_TREND_MIN:
                return 'ct_put'
            if side == 'call' and overall >= OVERFLOW_THRESHOLD and trend <= CT_CALL_TREND_MAX:
                return 'ct_call'
            return None
    monte_carlo.py:2208,2253  sig.overall / sig.trend ARE `Score.overall` / `Score.trend`
                               (database/models/core.py) -- the predicate's two inputs
                               are plain, already-persisted Score columns, nothing derived.
    monte_carlo.py:3453-3454  `if ct == 'ct_call': tier = CT_CALL_TIER` -- confirms ct_tag's
                               output is exactly what the cascade fill logic acts on.

Source B -- backtest_cascade.py (deterministic engine; drives the LIVE ledger via
portfolio_engine.py; `_cfg = strategy_config.STRATEGY_30DTE` at backtest_cascade.py:96-103):
    backtest_cascade.py:674   PUT_THRESHOLD = _cfg.PUT_THRESHOLD                (=25)
    backtest_cascade.py:682   CT_PROMOTE = _cfg.CT_PROMOTE                      (=True)
    backtest_cascade.py:683   CT_PUT_TREND_MIN = _cfg.CT_PUT_TREND_MIN          (=80)
    backtest_cascade.py:684   CT_CALL_TREND_MAX = _cfg.CT_CALL_TREND_MAX        (=20)
    backtest_cascade.py:685   CT_CALL_TIER = '95+'    # DIFFERENT STRING from monte_carlo's 'ultra'
    backtest_cascade.py:686   CT_PUT_TIER  = 'p<=15'  # DIFFERENT STRING from monte_carlo's 'put_top'
    backtest_cascade.py:1119-1130:
        def ct_tag(overall, trend, side):
            if not CT_PROMOTE or trend is None:
                return None
            if side == 'call' and overall >= 70 and trend <= CT_CALL_TREND_MAX:
                return 'ct_call'
            if side == 'put' and overall <= 25 and trend >= CT_PUT_TREND_MIN:
                return 'ct_put'
            return None
    portfolio_engine.py:1056,1189  live ledger call sites: `bc.ct_tag(int(s.overall), trend,
                                    'call')` -- this IS the function that decides a LIVE
                                    position's tier at entry (see NOTE 2 below).

NOTE 1 -- the two copies are logically identical today, sourced differently.
`backtest_cascade.py` reads `CT_PROMOTE`/`CT_PUT_TREND_MIN`/`CT_CALL_TREND_MAX` from
`strategy_config.STRATEGY_30DTE` (the architecturally "correct" single source of
truth per this repo's CLAUDE.md). `monte_carlo.py` instead hardcodes matching
literal defaults behind `os.environ.get(...)`, independently of `_cfg`. The two
happen to agree today (True/80/20 both places) but are NOT the same object in
memory and could silently diverge if either file is edited without the other.
This module sources its own constants from `strategy_config.STRATEGY_30DTE`
directly (matching backtest_cascade.py's, and CLAUDE.md's, "edit here only"
doctrine) while ALSO honoring monte_carlo.py's env-var override names, so a
monte_carlo.py ablation run (e.g. `CT_CALL_TREND_MAX=15`) and this module agree
even under an override. `verify_against_production()` below cross-checks all
three (this module, monte_carlo.ct_tag, backtest_cascade.ct_tag) so the drift
this note describes is caught automatically, not just documented. This is now
also logged as its own entry in .claude/docs/traps.md ("Two independent ct_tag
copies...").

NOTE 2 -- the display TIER strings are NOT interchangeable across engines, and
this is a real landmine for anyone tagging the LIVE ledger by tier string alone:
`PortfolioPosition.tier` (database/models/portfolio.py:114) is populated by
`backtest_cascade.py`'s cascade at portfolio_engine.py:1189
(`tier = bc.CT_CALL_TIER if bc.ct_tag(score, trend, 'call') else bc.score_to_tier(score)`)
-- so a CT-promoted live position's `tier` column holds the literal string
`'95+'`, NEVER `'ultra'`. Searching live `PortfolioPosition` rows for
`tier == 'ultra'` finds NOTHING; `tier == '95+'` is also NOT a clean CT
indicator by itself because raw score>=95 calls resolve to the same '95+'
display tier via `score_to_tier()` even when NOT CT-tagged (V0_DOSSIER.md
measured ~10% raw-95+ contamination of the monte_carlo 'ultra' tier on the same
grounds). CONCLUSION: there is no shortcut through `tier` strings on either
engine's output -- always re-run `ct_tag()` (this module) against
(overall, trend, side), never pattern-match a tier label.

NOTE 3 -- CTSL is proven inert on SET membership (V0_DOSSIER.md V0.1): CTSL's
own eligible set (trend<=15) is a strict subset of CT_PROMOTE's (trend<=20),
and `load_signals()` already floors calls at overall>=70, so CTSL cannot add a
single name to the CT-tagged population. This predicate is therefore
CTSL-invariant by construction -- correct behavior, not an oversight.

WHAT THIS MODULE DOES NOT COVER (V0_DOSSIER.md documents both, out of scope here)
----------------------------------------------------------------------------
  * Whether a CT-tagged signal actually gets FUNDED (cascade capacity,
    MAX_POSITIONS, GROSS_PREMIUM_CAP, day fill-order). This module answers
    "is this signal CT-eligible", not "did a simulated or live portfolio buy it."
  * DTE_ROUTER eligibility (monte_carlo.py:1564 `_dte_router_call_eligible`,
    portfolio_engine.py:1184 `bc._dte_router_call_eligible`) -- a SEPARATE
    predicate (score>=80, trend<50, 1/day cap) that V0 found funds ~1-in-5 CT
    calls as 15-DTE trades at the full ultra/'95+' rate because the tier
    override at monte_carlo.py:3453 / portfolio_engine.py:1189 precedes the
    router's alloc-score cap. `PortfolioPosition.routed_15dte` already
    persists this flag on the live ledger with NO reconstruction needed.

USAGE
-----
    from tools.ct_predicate import ct_tag, is_ct

    ct_tag(overall=82, trend=12, side='call')   # -> 'ct_call'
    ct_tag(overall=60, trend=90, side='put')    # -> None (overall > PUT_THRESHOLD)
    is_ct(82, 12, 'call')                       # -> True

CLI
---
    python tools/ct_predicate.py selfcheck
        Cross-checks this port against live monte_carlo.ct_tag AND
        backtest_cascade.ct_tag over a synthetic edge-case grid (no DB needed).

    python tools/ct_predicate.py reconcile --start 2022-08-01 --end 2026-07-31
        Counts the CT-tagged call population over a date range against the
        active scores version (read-only SELECT, no writes, no simulation).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import strategy_config as _sc

_CFG = _sc.STRATEGY_30DTE  # monte_carlo.py:67 / backtest_cascade.py:103 -- both engines
                           # that implement ct_tag() key off this same DteStrategyConfig.

# ---------------------------------------------------------------------------
# Core predicate constants -- sourced from strategy_config.STRATEGY_30DTE
# (the repo's declared single source of truth), env-overridable with the SAME
# variable names monte_carlo.py exposes so a monte_carlo.py ablation run and
# this module agree even when CT_* is overridden for a sweep.
# ---------------------------------------------------------------------------
CT_PROMOTE: bool = os.environ.get(
    'CT_PROMOTE', '1' if _CFG.CT_PROMOTE else '0'
) == '1'
CT_PUT_TREND_MIN: int = int(os.environ.get('CT_PUT_TREND_MIN', str(_CFG.CT_PUT_TREND_MIN)))
CT_CALL_TREND_MAX: int = int(os.environ.get('CT_CALL_TREND_MAX', str(_CFG.CT_CALL_TREND_MAX)))

# Call-floor / put-floor thresholds gating each side's population (these are
# the SAME 70 / 25 values load_signals()/load_put_signals() pre-filter on --
# a signal never reaches ct_tag() from outside this range in production).
OVERFLOW_THRESHOLD: int = _CFG.OVERFLOW_THRESHOLD  # monte_carlo.py:1345 (=70)
PUT_THRESHOLD: int = int(os.environ.get('PUT_THRESHOLD_OVERRIDE', str(_CFG.PUT_THRESHOLD)))  # (=25)

# Display-tier strings -- NOT used by the predicate; exported only so callers
# don't have to go re-derive NOTE 2 above the hard way.
CT_CALL_TIER_MC = os.environ.get('CT_CALL_TIER', 'ultra')       # monte_carlo.py's name
CT_PUT_TIER_MC = os.environ.get('CT_PUT_TIER', 'put_top')       # monte_carlo.py's name
CT_CALL_TIER_CASCADE = '95+'    # backtest_cascade.py:685 -- what PortfolioPosition.tier holds
CT_PUT_TIER_CASCADE = 'p<=15'   # backtest_cascade.py:686


def ct_tag(overall, trend, side: str) -> Optional[str]:
    """Verbatim port of monte_carlo.py:1546-1554 / backtest_cascade.py:1119-1130.

    Returns 'ct_call', 'ct_put', or None. `overall` and `trend` are plain
    Score.overall / Score.trend values (ints in production; None trend is the
    only guarded null -- matches both production copies exactly, including
    their lack of an `overall is None` guard, since Score.overall is a
    non-nullable column in every real caller).
    """
    if not CT_PROMOTE or trend is None:
        return None
    if side == 'put' and overall <= PUT_THRESHOLD and trend >= CT_PUT_TREND_MIN:
        return 'ct_put'
    if side == 'call' and overall >= OVERFLOW_THRESHOLD and trend <= CT_CALL_TREND_MAX:
        return 'ct_call'
    return None


def is_ct(overall, trend, side: str) -> bool:
    """Convenience boolean wrapper around ct_tag()."""
    return ct_tag(overall, trend, side) is not None


def classify_side(overall) -> Optional[str]:
    """Infer 'call' / 'put' / None from `overall` alone, using the same
    floor/ceiling load_signals()/load_put_signals() pre-filter on. Only useful
    when the caller doesn't already know the side (e.g. mining raw Score rows
    with no ledger context) -- a live ledger trade already carries its own
    `side` column and should pass that explicitly instead."""
    if overall is None:
        return None
    if overall >= OVERFLOW_THRESHOLD:
        return 'call'
    if overall <= PUT_THRESHOLD:
        return 'put'
    return None


def tag_row(row, side: Optional[str] = None) -> Optional[str]:
    """Tag any object exposing `.overall` / `.trend` attributes (a Score row,
    a peewee .tuples() namedtuple via tag_values(), a ledger join result).
    `side` is inferred via classify_side() when not given explicitly."""
    overall = getattr(row, 'overall')
    trend = getattr(row, 'trend')
    resolved_side = side if side is not None else classify_side(overall)
    if resolved_side is None:
        return None
    return ct_tag(overall, trend, resolved_side)


def tag_values(overall, trend, side: Optional[str] = None) -> Optional[str]:
    """Same as tag_row() but for bare (overall, trend) values, e.g. from a
    `.tuples()` query -- avoids the peewee FK-per-row trap (traps.md) by
    never touching a lazy-loaded relation."""
    resolved_side = side if side is not None else classify_side(overall)
    if resolved_side is None:
        return None
    return ct_tag(overall, trend, resolved_side)


# ---------------------------------------------------------------------------
# Self-check: cross-verify this port against BOTH live production copies.
# ---------------------------------------------------------------------------
def _synthetic_grid():
    """Edge-case-dense (overall, trend, side) grid -- boundary values on every
    gate (69/70/71, 19/20/21, 79/80/81, 24/25/26) plus None trend and extremes."""
    overalls_call = [0, 69, 70, 71, 74, 75, 79, 80, 94, 95, 96, 100]
    overalls_put = [0, 1, 15, 20, 24, 25, 26, 30, 50, 100]
    trends = [None, -5, 0, 14, 15, 16, 19, 20, 21, 49, 50, 51, 79, 80, 81, 100, 105]
    grid = []
    for ov in overalls_call:
        for tr in trends:
            grid.append((ov, tr, 'call'))
    for ov in overalls_put:
        for tr in trends:
            grid.append((ov, tr, 'put'))
    return grid


def verify_against_production(sample_db_n: int = 0) -> dict:
    """Cross-check this module's ct_tag() against a LIVE import of both
    monte_carlo.ct_tag and backtest_cascade.ct_tag. Raises AssertionError with
    the first disagreement found; returns a summary dict on full agreement.

    `sample_db_n > 0` additionally pulls that many recent real Score rows
    (both sides) and cross-checks those too -- catches drift a synthetic grid
    could miss (e.g. a stored value type production never guards against).
    Requires a DB connection; the synthetic grid check does not.
    """
    import monte_carlo as _mc
    import backtest_cascade as _bc

    grid = _synthetic_grid()
    mismatches = []
    for overall, trend, side in grid:
        mine = ct_tag(overall, trend, side)
        mc = _mc.ct_tag(overall, trend, side)
        bc = _bc.ct_tag(overall, trend, side)
        if not (mine == mc == bc):
            mismatches.append({
                'overall': overall, 'trend': trend, 'side': side,
                'this_module': mine, 'monte_carlo': mc, 'backtest_cascade': bc,
            })
    checked = len(grid)

    db_checked = 0
    if sample_db_n > 0:
        from database.models.core import Score, AlgorithmVersion
        version = AlgorithmVersion.get_active_scores_version()
        rows = list(
            Score.select(Score.overall, Score.trend)
            .where(Score.version == version, Score.overall >= OVERFLOW_THRESHOLD)
            .order_by(Score.date.desc())
            .limit(sample_db_n)
            .tuples()
        )
        for overall, trend in rows:
            mine = ct_tag(overall, trend, 'call')
            mc = _mc.ct_tag(overall, trend, 'call')
            bc = _bc.ct_tag(overall, trend, 'call')
            if not (mine == mc == bc):
                mismatches.append({
                    'overall': overall, 'trend': trend, 'side': 'call',
                    'this_module': mine, 'monte_carlo': mc, 'backtest_cascade': bc,
                    'source': 'live_db_sample',
                })
        db_checked = len(rows)
        checked += db_checked

    if mismatches:
        raise AssertionError(
            f"ct_predicate DRIFT DETECTED: {len(mismatches)}/{checked} cases disagree "
            f"across this_module/monte_carlo/backtest_cascade. First: {mismatches[0]}"
        )
    return {
        'status': 'AGREE',
        'grid_cases_checked': len(grid),
        'db_rows_checked': db_checked,
        'total_checked': checked,
        'CT_PROMOTE': CT_PROMOTE,
        'CT_PUT_TREND_MIN': CT_PUT_TREND_MIN,
        'CT_CALL_TREND_MAX': CT_CALL_TREND_MAX,
        'OVERFLOW_THRESHOLD': OVERFLOW_THRESHOLD,
        'PUT_THRESHOLD': PUT_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# Reconciliation helper: count the CT-tagged population over a date range,
# directly against the `scores` table -- read-only, no simulation, no MC
# engine import needed. Mirrors V6_DECEMBER_DRAFT.md's population definition:
# "ct_tag's call branch verbatim ... overall >= 70, trend <= CT_CALL_TREND_MAX".
# ---------------------------------------------------------------------------
def reconcile_population(start_date, end_date, side: str = 'call', version=None) -> dict:
    """Count CT-tagged signals of `side` in [start_date, end_date] against the
    active (or given) scores version. Read-only SELECT; no writes.

    Returns a dict with the count, the pre-filter population size, and a few
    sample (symbol, date, overall, trend) rows for spot-checking.
    """
    from database.models.core import Score, AlgorithmVersion

    if version is None:
        version = AlgorithmVersion.get_active_scores_version()

    threshold_filter = (Score.overall >= OVERFLOW_THRESHOLD) if side == 'call' \
        else (Score.overall <= PUT_THRESHOLD)

    rows = list(
        Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
        .where(
            Score.version == version,
            Score.date >= start_date,
            Score.date <= end_date,
            threshold_filter,
        )
        .tuples()
    )
    prefilter_n = len(rows)
    ct_rows = [(sym, d, ov, tr) for (sym, d, ov, tr) in rows
               if ct_tag(ov, tr, side) is not None]

    years = (end_date - start_date).days / 365.25
    return {
        'version_id': version.id,
        'version_commit': getattr(version, 'git_commit', None),
        'start_date': str(start_date),
        'end_date': str(end_date),
        'side': side,
        'years': round(years, 2),
        'prefilter_population': prefilter_n,
        'ct_tagged_count': len(ct_rows),
        'ct_per_year': round(len(ct_rows) / years, 1) if years > 0 else None,
        'sample': [
            {'symbol': str(sym), 'date': str(d), 'overall': ov, 'trend': tr}
            for (sym, d, ov, tr) in ct_rows[:10]
        ],
    }


def _cli():
    import argparse
    from datetime import date as _date

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_check = sub.add_parser('selfcheck', help='cross-check vs monte_carlo.py + backtest_cascade.py')
    p_check.add_argument('--db-sample', type=int, default=500,
                          help='also cross-check this many recent live Score rows (0 = grid only)')

    p_rec = sub.add_parser('reconcile', help='count CT-tagged population over a date range')
    p_rec.add_argument('--start', required=True, help='YYYY-MM-DD')
    p_rec.add_argument('--end', required=True, help='YYYY-MM-DD')
    p_rec.add_argument('--side', choices=['call', 'put'], default='call')
    p_rec.add_argument('--version', type=int, default=None, help='AlgorithmVersion id (default: active)')

    args = parser.parse_args()

    if args.cmd == 'selfcheck':
        result = verify_against_production(sample_db_n=args.db_sample)
        print("ct_predicate selfcheck: AGREE")
        for k, v in result.items():
            print(f"  {k}: {v}")
        return 0

    if args.cmd == 'reconcile':
        start = _date.fromisoformat(args.start)
        end = _date.fromisoformat(args.end)
        version = None
        if args.version is not None:
            from database.models.core import AlgorithmVersion
            version = AlgorithmVersion.get_by_id(args.version)
        result = reconcile_population(start, end, side=args.side, version=version)
        print(f"CT-tagged {args.side} population, {result['start_date']}..{result['end_date']} "
              f"({result['years']}y), version={result['version_id']} ({result['version_commit']}):")
        print(f"  pre-filter population (overall {'>=' if args.side == 'call' else '<='} "
              f"{OVERFLOW_THRESHOLD if args.side == 'call' else PUT_THRESHOLD}): {result['prefilter_population']}")
        print(f"  CT-tagged: {result['ct_tagged_count']}  (~{result['ct_per_year']}/year)")
        print("  sample:")
        for row in result['sample']:
            print(f"    {row}")
        return 0


if __name__ == '__main__':
    raise SystemExit(_cli())
