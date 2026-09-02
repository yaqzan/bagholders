"""One-off cleanup: delete EarningsDate rows that are clear ghost duplicates.

Three ghost classes:

1. CLEAR — one row has reported_eps, sibling within ±30 days does not.
   Auto-deletable. Always handled by --apply.

2. AMBIGUOUS — both no-actual, at least one date has already passed.
   When --apply-ambig is set, drop the older row if it is more than
   STALE_PAST_DAYS calendar days old AND its sibling is upcoming.
   (Past-but-recent pairs may just be EPS-not-yet-filled and are kept.)

3. UNCLEAR — both no-actual, both upcoming.
   When --apply-unclear is set, resolve via quarterly cadence: from the
   symbol's last 4 reported earnings, compute the median date gap, project
   the next expected date from the most recent reported one, and keep the
   pair member closest to that anchor. Tie-breaks pick the later date.
   If fewer than 2 prior reported earnings exist, the pair is left alone.

Usage:
    python experiments/earnings_ghost_cleanup.py                          # dry-run, all classes
    python experiments/earnings_ghost_cleanup.py --apply                  # delete clear only
    python experiments/earnings_ghost_cleanup.py --apply --apply-unclear  # + unclear pairs
    python experiments/earnings_ghost_cleanup.py --apply --apply-ambig --apply-unclear
"""
import argparse
import os
import statistics
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models.core import EarningsDate, EarningsJump

WINDOW_DAYS = 30
AMBIG_DAYS = 14
STALE_PAST_DAYS = 7   # ambiguous past row older than this w/o reported_eps -> ghost
CADENCE_LOOKBACK = 4  # number of prior reported earnings used to derive quarterly cadence


def find_pairs():
    """Return (clear_ghosts, ambiguous, unclear).
    clear_ghosts: list of ghost EarningsDate rows safe to delete.
    ambiguous: list of (sym, id_a, id_b, date_a, date_b) — both no-actual, in past.
    unclear: same shape, both upcoming.
    """
    by_sym = defaultdict(list)
    for r in EarningsDate.select().order_by(EarningsDate.symbol, EarningsDate.date):
        by_sym[r.symbol_id].append(r)

    clear_ghost_ids = set()
    clear_ghosts = []
    ambiguous = []
    unclear = []
    today = date.today()

    for sym, rows in by_sym.items():
        for i, r in enumerate(rows):
            for j in range(i + 1, len(rows)):
                r2 = rows[j]
                gap = (r2.date - r.date).days
                if gap > WINDOW_DAYS:
                    break
                r_has = r.reported_eps is not None
                r2_has = r2.reported_eps is not None
                if r_has and not r2_has and gap <= WINDOW_DAYS:
                    if r2.id not in clear_ghost_ids:
                        clear_ghost_ids.add(r2.id)
                        clear_ghosts.append(r2)
                elif r2_has and not r_has and gap <= WINDOW_DAYS:
                    if r.id not in clear_ghost_ids:
                        clear_ghost_ids.add(r.id)
                        clear_ghosts.append(r)
                elif (not r_has) and (not r2_has) and gap <= AMBIG_DAYS:
                    if r.date <= today or r2.date <= today:
                        ambiguous.append((sym, r, r2))
                    else:
                        unclear.append((sym, r, r2))
    return clear_ghosts, ambiguous, unclear


def _expected_next_earnings(sym):
    """Quarterly cadence anchor for symbol: (expected_date, n_reports) or (None, 0)."""
    reported = list(EarningsDate
                    .select()
                    .where((EarningsDate.symbol == sym)
                           & (EarningsDate.reported_eps.is_null(False)))
                    .order_by(EarningsDate.date.desc())
                    .limit(CADENCE_LOOKBACK + 1))
    if len(reported) < 2:
        return None, len(reported)
    reported.sort(key=lambda r: r.date)
    gaps = [(reported[k+1].date - reported[k].date).days for k in range(len(reported) - 1)]
    median_gap = int(statistics.median(gaps))
    expected = reported[-1].date + __import__('datetime').timedelta(days=median_gap)
    return expected, len(reported)


def resolve_ambiguous_pair(r1, r2, today):
    """For an ambiguous (both no-actual, ≥1 past/today) pair, pick the ghost to delete.

    Two paths:
      * Stale-past: older row is more than STALE_PAST_DAYS past AND the newer
        is upcoming -> drop the older as a stale ghost.
      * Today/recent: older is today or 0..STALE_PAST_DAYS days past — treat as
        effectively-upcoming and resolve via quarterly cadence (same logic as
        unclear pairs). EPS will only have populated for one of them once the
        real report lands; until then the cadence anchor is the best signal.
    """
    older, newer = (r1, r2) if r1.date <= r2.date else (r2, r1)
    older_past_days = (today - older.date).days
    newer_is_upcoming = newer.date > today
    if older_past_days > STALE_PAST_DAYS and newer_is_upcoming:
        return older
    # Both effectively-upcoming (older is today or within stale threshold).
    # Defer to cadence resolution.
    return resolve_unclear_pair(r1, r2)


def resolve_unclear_pair(r1, r2):
    """For an unclear (both no-actual, both upcoming) pair, pick the ghost to delete.

    Heuristic: project expected next-earnings from the symbol's last
    CADENCE_LOOKBACK+1 reported quarters; keep the row closest to that anchor.
    Tie -> keep later. If the projected anchor is more than 60 days away from
    BOTH pair members the cadence is too stale to trust (symbol stopped
    reporting on a quarterly schedule, or DB is missing recent quarters);
    fall back to "keep later" deterministically.
    """
    expected, _n = _expected_next_earnings(r1.symbol_id)
    if expected is None:
        # No prior cadence -> deterministic fallback: keep later
        return r1 if r1.date < r2.date else r2
    d1 = abs((r1.date - expected).days)
    d2 = abs((r2.date - expected).days)
    if min(d1, d2) > 60:
        # Anchor too stale to be meaningful -> fallback: keep later
        return r1 if r1.date < r2.date else r2
    if d1 < d2:
        return r2
    if d2 < d1:
        return r1
    # tie -> keep later
    return r1 if r1.date < r2.date else r2


def classify_pairs():
    """Run find_pairs and split ambiguous into stale/today/recent-past buckets.

    Returns dict with keys:
      clear: [EarningsDate]                      — clear ghosts (sibling has EPS)
      ambig_stale: [(ghost, keep)]               — older >STALE_PAST_DAYS past, newer upcoming
      ambig_today: [(ghost, keep)]               — older == today, newer upcoming, cadence-resolved
      ambig_recent: [(sym, r1, r2)]              — older 1..STALE_PAST_DAYS past — SKIPPED (EPS may arrive)
      unclear_resolved: [(ghost, r1, r2)]        — both upcoming, cadence-resolved
      unclear_skipped: [(sym, r1, r2)]           — both upcoming, no cadence available
    """
    clear, ambig, unclear = find_pairs()
    today = date.today()

    ambig_stale = []
    ambig_today = []
    ambig_recent = []
    for sym, r1, r2 in ambig:
        older, newer = (r1, r2) if r1.date <= r2.date else (r2, r1)
        older_past_days = (today - older.date).days
        newer_is_upcoming = newer.date > today
        if older_past_days > STALE_PAST_DAYS and newer_is_upcoming:
            ambig_stale.append((older, newer))
        elif older_past_days == 0 and newer_is_upcoming:
            ghost = resolve_unclear_pair(r1, r2)
            if ghost is None:
                ambig_recent.append((sym, r1, r2))
            else:
                keep = r1 if ghost.id == r2.id else r2
                ambig_today.append((ghost, keep))
        else:
            ambig_recent.append((sym, r1, r2))

    unclear_resolved = []
    unclear_skipped = []
    for sym, r1, r2 in unclear:
        ghost = resolve_unclear_pair(r1, r2)
        if ghost is not None:
            unclear_resolved.append((ghost, r1, r2))
        else:
            unclear_skipped.append((sym, r1, r2))

    return {
        'clear': clear,
        'ambig_stale': ambig_stale,
        'ambig_today': ambig_today,
        'ambig_recent': ambig_recent,
        'unclear_resolved': unclear_resolved,
        'unclear_skipped': unclear_skipped,
    }


def skipped_recent_past_symbols():
    """Symbols whose ambiguous pair has older row 1..STALE_PAST_DAYS days past.

    These are the 'EPS may still arrive' cases. After a yfinance re-pull, EPS
    typically populates on the older row (the real report) — turning the pair
    into a clear ghost (newer = upcoming-quarter ghost) that the next cleanup
    pass will delete.
    """
    parts = classify_pairs()
    syms = set()
    for sym, _r1, _r2 in parts['ambig_recent']:
        syms.add(sym)
    return sorted(syms)


def run_cleanup(apply_clear=True, apply_stale=True, apply_today=True,
                apply_unclear=True, apply_recent=False, verbose=True):
    """Programmatic entry point. Returns dict with counts and the deleted ids.

    apply_clear:   delete clear ghosts (sibling has reported_eps)
    apply_stale:   delete ambiguous-stale (older >STALE_PAST_DAYS past, no EPS, newer upcoming)
    apply_today:   delete ambiguous-today via cadence anchor
    apply_unclear: delete unclear (both upcoming) via cadence anchor
    apply_recent:  delete ambiguous-recent (older 1..STALE_PAST_DAYS past, cadence) — RISKY,
                   may delete real reports waiting for EPS to fill. Off by default.
    """
    parts = classify_pairs()
    if verbose:
        print(f'CLEAR GHOSTS: {len(parts["clear"])}')
        print(f'AMBIG stale-past: {len(parts["ambig_stale"])}')
        print(f'AMBIG today-cadence: {len(parts["ambig_today"])}')
        print(f'AMBIG recent-past (skipped by default): {len(parts["ambig_recent"])}')
        print(f'UNCLEAR resolved: {len(parts["unclear_resolved"])}')
        print(f'UNCLEAR skipped: {len(parts["unclear_skipped"])}')

    to_delete_ids = set()
    if apply_clear:
        to_delete_ids.update(r.id for r in parts['clear'])
    if apply_stale:
        to_delete_ids.update(g.id for g, _k in parts['ambig_stale'])
    if apply_today:
        to_delete_ids.update(g.id for g, _k in parts['ambig_today'])
    if apply_unclear:
        to_delete_ids.update(g.id for g, _a, _b in parts['unclear_resolved'])
    if apply_recent:
        for sym, r1, r2 in parts['ambig_recent']:
            ghost = resolve_unclear_pair(r1, r2)
            if ghost is not None:
                to_delete_ids.add(ghost.id)

    deleted = 0
    deleted_jumps = 0
    if to_delete_ids:
        stale_rows = list(
            EarningsDate.select(EarningsDate.symbol, EarningsDate.effective_date)
            .where(EarningsDate.id.in_(list(to_delete_ids)))
        )
        jump_pairs = [
            (r.symbol_id, r.effective_date)
            for r in stale_rows
            if r.effective_date is not None
        ]
        for sym, eff in jump_pairs:
            deleted_jumps += (
                EarningsJump
                .delete()
                .where((EarningsJump.symbol == sym) & (EarningsJump.earnings_date == eff))
                .execute()
            )
        deleted = (EarningsDate
                   .delete()
                   .where(EarningsDate.id.in_(list(to_delete_ids)))
                   .execute())
    if verbose:
        print(f'Deleted {deleted} earnings rows and {deleted_jumps} jump rows.')
    return {'parts': parts, 'deleted_ids': to_delete_ids, 'deleted': deleted,
            'deleted_jumps': deleted_jumps}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='Actually delete clear ghost rows (default: dry-run)')
    parser.add_argument('--apply-ambig', action='store_true',
                        help='Also delete resolved ambiguous-pair ghosts (stale-past + today-cadence)')
    parser.add_argument('--apply-unclear', action='store_true',
                        help='Also delete resolved unclear-pair ghosts (cadence-anchored)')
    parser.add_argument('--apply-recent', action='store_true',
                        help='Also delete recent-past ambiguous via cadence (RISKY — may delete real reports waiting for EPS)')
    args = parser.parse_args()

    parts = classify_pairs()

    print(f'CLEAR GHOSTS (auto-deletable): {len(parts["clear"])}')
    for r in parts['clear'][:25]:
        print(f'  id={r.id} sym={r.symbol_id} date={r.date} eff={r.effective_date}')
    if len(parts['clear']) > 25:
        print(f'  ...({len(parts["clear"]) - 25} more)')

    print(f'\nAMBIGUOUS pairs (both no-actual, at least one today/past):')
    print(f'  -> stale-past (older >{STALE_PAST_DAYS}d past w/ upcoming sibling): {len(parts["ambig_stale"])}')
    for ghost, keep in parts['ambig_stale'][:10]:
        print(f'    drop id={ghost.id} sym={ghost.symbol_id} date={ghost.date}  (keep {keep.date})')
    if len(parts['ambig_stale']) > 10:
        print(f'    ...({len(parts["ambig_stale"]) - 10} more)')
    print(f'  -> today-cadence (older == today, newer upcoming): {len(parts["ambig_today"])}')
    for ghost, keep in parts['ambig_today'][:10]:
        exp, n = _expected_next_earnings(ghost.symbol_id)
        print(f'    drop id={ghost.id} sym={ghost.symbol_id} date={ghost.date}  '
              f'(keep {keep.date}, expected {exp}, n={n})')
    if len(parts['ambig_today']) > 10:
        print(f'    ...({len(parts["ambig_today"]) - 10} more)')
    print(f'  -> recent-past 1..{STALE_PAST_DAYS}d, EPS may still arrive: {len(parts["ambig_recent"])}')
    if args.apply_recent:
        for sym, r1, r2 in parts['ambig_recent'][:10]:
            ghost = resolve_unclear_pair(r1, r2)
            if ghost is not None:
                keep = r1 if ghost.id == r2.id else r2
                exp, n = _expected_next_earnings(ghost.symbol_id)
                print(f'    drop id={ghost.id} sym={ghost.symbol_id} date={ghost.date}  '
                      f'(keep {keep.date}, expected {exp}, n={n})')

    print(f'\nUNCLEAR pairs total: {len(parts["unclear_resolved"]) + len(parts["unclear_skipped"])}')
    print(f'  -> resolved via quarterly cadence: {len(parts["unclear_resolved"])}')
    for ghost, a, b in parts['unclear_resolved'][:10]:
        keep = a if ghost.id == b.id else b
        exp, n = _expected_next_earnings(ghost.symbol_id)
        print(f'    drop id={ghost.id} sym={ghost.symbol_id} date={ghost.date}  '
              f'(keep {keep.date}, expected {exp}, n={n})')
    if len(parts['unclear_resolved']) > 10:
        print(f'    ...({len(parts["unclear_resolved"]) - 10} more)')
    print(f'  -> skipped (insufficient prior reports): {len(parts["unclear_skipped"])}')

    if not (args.apply or args.apply_ambig or args.apply_unclear or args.apply_recent):
        print('\n[dry-run] Pass --apply (clear), --apply-ambig (ambig-stale+today), '
              '--apply-unclear (unclear), --apply-recent (RISKY).')
        return

    res = run_cleanup(
        apply_clear=args.apply,
        apply_stale=args.apply_ambig,
        apply_today=args.apply_ambig,
        apply_unclear=args.apply_unclear,
        apply_recent=args.apply_recent,
        verbose=False,
    )
    print(f'\nDeleting {len(res["deleted_ids"])} rows ...')
    print(f'Deleted {res["deleted"]} earnings rows and {res["deleted_jumps"]} jump rows.')


if __name__ == '__main__':
    main()
