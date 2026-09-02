"""Realized-slippage report (P3.7, gameplan.md P3.7) — compares REAL,
manually-recorded fills (`trader portfolio record-fill`, `portfolio_fills`
table) against the live-Portfolio engine's own MODELED mark for the same
trade, to validate (or falsify) the asymmetric-cost canon this system's
entire sizing/EV framework leans on:

    entry via mid-fill: SLIP_ENTRY = 0.0   (liquidity provider, no spread)
    TP via resting limit: SLIP_TP  = 0.0   (liquidity provider, no spread)
    forced SL exit:      SLIP_SL   = -0.015  (liquidity taker, ~half-spread)
    forced hard-sell:    SLIP_HARD = -0.015  (liquidity taker, ~half-spread)
    (strategy_config.py STRATEGY_30DTE.option, mirrored in the 15 DTE
    profile -- see tests/test_strategy_config_drift.py for the live values)

This system is alerts + manual execution (no broker feed), so REAL fills
only exist if the user records them. The canon has never seen one. This
tool is the cheapest possible instrument that can falsify it.

REPORT-ONLY. Per the gameplan row this was scoped from: prints N and
REFUSES to draw a conclusion below N>=30 recorded (and resolvable) fills --
below that floor this prints the raw numbers with an explicit "insufficient
data" banner and nothing stronger. It never writes to strategy_config.py,
never adjusts a SLIP_* constant, never touches a PortfolioPosition row --
purely a read + print (+ an optional history JSON under .cache/ for trend-
watching across weekly runs).

Run:      python tools/slippage_report.py [--days N] [--json]
Schedule: weekly, idle-tier queue job (see .claude/docs/incident-runbook.md
          "Real-fill loop" for the exact `trader queue submit` one-liner --
          registration in Task Scheduler is documented there rather than
          added here, to stay off Block A's scheduler-registration surface).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

N_FLOOR = 30   # per gameplan.md P3.7: "report-only until N>=30"

# exit_reason -> slippage category. TP is the only "liquidity provider" exit
# (a resting limit the market comes to); everything else that closes a
# position is some flavor of forced/urgent exit under the canon.
_FORCED_REASONS = {
    'sl', 'hard', 'dh_pop', 'dh_expiry', 'dh_open', 'prem',
    'realloc', 'version_sweep', 'strategy_sweep',
}


def _category(fill, pos):
    if fill.side == 'buy':
        return 'entry'
    reason = getattr(pos, 'exit_reason', None) if pos is not None else None
    if reason == 'tp':
        return 'tp-exit'
    if reason in _FORCED_REASONS:
        return 'forced-exit'
    return 'other-exit'


def _cost_impact(fill, pos):
    """Signed, sign-normalized so POSITIVE always means "worse than the
    model assumed" regardless of buy/sell direction:
      buy:  (real_paid - model_cost)   / model_cost    -- paid more = bad
      sell: (model_proceeds - real_received) / model_proceeds -- got less = bad
    Returns None if there's no resolvable model value to compare against
    (unlinked fill, or a linked-but-still-open sell where current_value_usd
    is a live mark rather than a frozen exit valuation -- flagged, not
    silently included).
    """
    if pos is None:
        return None
    real = float(fill.fill_value_usd)
    if fill.side == 'buy':
        model = float(pos.premium_usd) if pos.premium_usd is not None else None
        if not model or model <= 0:
            return None
        return (real - model) / model
    # sell
    if pos.status != 'closed':
        return None   # live mark, not a frozen exit valuation -- excluded, not guessed at
    model = float(pos.current_value_usd) if pos.current_value_usd is not None else None
    if not model or model <= 0:
        return None
    return (model - real) / model


def _fmt_pct(x):
    return f"{x * 100:+.2f}%"


def _verdict(free_side_mean, forced_mean, noise_band=0.005):
    """Pure sign-logic helper (unit-testable in isolation -- the sign
    convention here is exactly the kind of thing worth locking in with a
    test rather than trusting eyeballed print statements). gap = forced -
    free_side, using the _cost_impact convention where POSITIVE always
    means "worse than modeled" regardless of buy/sell direction.

    gap > +noise_band  -> forced exits really do cost more -> canon HOLDS
    gap < -noise_band  -> free side is worse (or forced isn't) -> canon FAILS
    otherwise          -> within noise, no call
    Returns (gap, verdict_str).
    """
    gap = forced_mean - free_side_mean
    if gap > noise_band:
        verdict = "CONSISTENT with the canon (forced exits cost materially more)"
    elif gap < -noise_band:
        verdict = "CONTRADICTS the canon (forced exits do NOT show the modeled extra cost)"
    else:
        verdict = f"AMBIGUOUS (forced vs free-side gap is within noise, +/-{noise_band * 100:.1f}pp)"
    return gap, verdict


def build_report(days=None):
    from database.models.portfolio import PortfolioFill, PortfolioPosition
    PortfolioFill.ensure_schema()

    q = PortfolioFill.select().order_by(PortfolioFill.filled_at)
    if days is not None:
        cutoff = datetime.now() - timedelta(days=days)
        q = q.where(PortfolioFill.filled_at >= cutoff)
    fills = list(q)

    pos_by_key = {}
    keys = {f.pos_key for f in fills if f.pos_key}
    if keys:
        for p in PortfolioPosition.select().where(PortfolioPosition.pos_key.in_(list(keys))):
            pos_by_key[p.pos_key] = p

    rows = []
    unresolved = 0
    excluded_open_sell = 0
    for f in fills:
        pos = pos_by_key.get(f.pos_key) if f.pos_key else None
        if pos is None:
            unresolved += 1
            rows.append({'fill_id': f.id, 'symbol': f.symbol, 'side': f.side,
                        'filled_at': f.filled_at.isoformat(), 'category': None,
                        'cost_impact': None, 'reason': 'unlinked'})
            continue
        impact = _cost_impact(f, pos)
        cat = _category(f, pos)
        if impact is None and f.side == 'sell' and pos.status != 'closed':
            excluded_open_sell += 1
            rows.append({'fill_id': f.id, 'symbol': f.symbol, 'side': f.side,
                        'filled_at': f.filled_at.isoformat(), 'category': cat,
                        'cost_impact': None, 'reason': 'position still open (live mark, not frozen)'})
            continue
        rows.append({'fill_id': f.id, 'symbol': f.symbol, 'side': f.side,
                    'filled_at': f.filled_at.isoformat(), 'category': cat,
                    'cost_impact': impact,
                    'exit_reason': getattr(pos, 'exit_reason', None)})

    by_cat = defaultdict(list)
    for r in rows:
        if r['cost_impact'] is not None:
            by_cat[r['category']].append(r['cost_impact'])

    summary = {}
    for cat, vals in by_cat.items():
        summary[cat] = {
            'n': len(vals),
            'mean': statistics.mean(vals),
            'median': statistics.median(vals),
            'stdev': statistics.stdev(vals) if len(vals) > 1 else 0.0,
        }

    n_usable = sum(len(v) for v in by_cat.values())
    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'n_fills_total': len(fills),
        'n_unresolved': unresolved,
        'n_excluded_open_sell': excluded_open_sell,
        'n_usable': n_usable,
        'n_floor': N_FLOOR,
        'sufficient': n_usable >= N_FLOOR,
        'by_category': summary,
        'rows': rows,
        'canon': {
            'note': 'strategy_config.py STRATEGY_30DTE.option modeled slippage '
                    '(fraction of the underlying-return threshold, not directly a '
                    'premium %, but the asymmetric SIGN/magnitude split is the claim '
                    'under test)',
            'slip_entry': 0.0, 'slip_tp': 0.0, 'slip_sl': -0.015, 'slip_hard': -0.015,
        },
    }


def print_report(report, verbose_rows=False):
    print(f"[slippage] {report['generated_at']} -- realized-fill vs modeled-mark report")
    print(f"[slippage] fills recorded: {report['n_fills_total']}  "
          f"unresolved (no position link): {report['n_unresolved']}  "
          f"excluded (still-open sell, live mark): {report['n_excluded_open_sell']}  "
          f"usable: {report['n_usable']}")
    print()
    if report['n_unresolved']:
        print(f"[slippage] NOTE: {report['n_unresolved']} fill(s) never resolved to a "
              f"PortfolioPosition -- re-record with --pos-key to link them retroactively "
              f"(edit the portfolio_fills row, or record a fresh one and note the old id).")
        print()

    if not report['by_category']:
        print("[slippage] NO USABLE FILLS YET. No data to report. "
              "Record real fills with `trader portfolio record-fill SYM PRICE QTY "
              "[--side buy|sell]` as you execute them.")
        return

    print(f"{'category':<14}{'N':>6}{'mean':>12}{'median':>12}{'stdev':>10}")
    for cat in ('entry', 'tp-exit', 'forced-exit', 'other-exit'):
        s = report['by_category'].get(cat)
        if not s:
            continue
        print(f"{cat:<14}{s['n']:>6}{_fmt_pct(s['mean']):>12}{_fmt_pct(s['median']):>12}"
              f"{_fmt_pct(s['stdev']):>10}")
    print()
    print(f"[slippage] modeled canon for reference: SLIP_ENTRY={report['canon']['slip_entry']}"
          f"  SLIP_TP={report['canon']['slip_tp']}  SLIP_SL={report['canon']['slip_sl']}"
          f"  SLIP_HARD={report['canon']['slip_hard']}  (entry/TP modeled FREE; "
          f"forced exits modeled at ~half-spread cost)")
    print()

    n = report['n_usable']
    if n < report['n_floor']:
        print(f"[slippage] N={n} < floor={report['n_floor']} -- INSUFFICIENT DATA. "
              f"REPORT-ONLY, NO CONCLUSION DRAWN. The numbers above are directional "
              f"context only; do not act on them, do not cite them as validating or "
              f"falsifying the asymmetric-cost canon until N>=30.")
        return

    entry = report['by_category'].get('entry')
    tp = report['by_category'].get('tp-exit')
    forced = report['by_category'].get('forced-exit')
    print(f"[slippage] N={n} >= floor={report['n_floor']} -- sufficient for a directional read.")
    if forced and (entry or tp):
        free_side = entry or tp
        gap, verdict = _verdict(free_side['mean'], forced['mean'])
        print(f"[slippage] forced-exit mean ({_fmt_pct(forced['mean'])}) vs "
              f"entry/TP mean ({_fmt_pct(free_side['mean'])}): gap {_fmt_pct(gap)} -- {verdict}")
        if gap < -0.005:
            print("[slippage] A materially-worse-than-modeled read on the free side, or a "
                  "materially-smaller-than-modeled forced-exit penalty, is the pre-registered "
                  "trigger (gameplan.md P3.7) to re-open the execution-conditional levers "
                  "(the 70-74 overflow tier and any other mechanism whose ship case leaned on "
                  "this asymmetry) -- do not silently re-tune, escalate for review.")
    else:
        print("[slippage] not enough category coverage yet to compare forced vs free-side "
              "(need at least one of entry/tp-exit AND forced-exit populated).")

    if verbose_rows:
        print()
        print("[slippage] per-fill detail:")
        for r in report['rows']:
            ci = _fmt_pct(r['cost_impact']) if r['cost_impact'] is not None else '--'
            print(f"  #{r['fill_id']:<5} {r['symbol']:<8} {r['side']:<5} "
                  f"{r.get('category') or '-':<12} {ci:>10}  {r['filled_at']}  "
                  f"{r.get('reason') or r.get('exit_reason') or ''}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--days', type=int, default=None,
                    help='only include fills recorded in the last N days (default: all)')
    ap.add_argument('--json', action='store_true',
                    help='also write a timestamped JSON snapshot under .cache/slippage_report/')
    ap.add_argument('--rows', action='store_true', help='print per-fill detail')
    args = ap.parse_args(argv)

    report = build_report(days=args.days)
    print_report(report, verbose_rows=args.rows)

    if args.json:
        import os
        outdir = os.path.join('.cache', 'slippage_report')
        os.makedirs(outdir, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        outpath = os.path.join(outdir, f'{stamp}.json')
        with open(outpath, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"[slippage] wrote {outpath}")

    return 0   # report-only tool -- never a pass/fail gate, always exit 0


if __name__ == '__main__':
    sys.exit(main())
