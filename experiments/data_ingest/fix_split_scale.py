"""
Rescale a symbol's pre-split price_history OHLC into post-split ADJUSTED convention
when the vendor's own restatement is missing or lagging.

WHY THIS EXISTS (BYND, 2026-08-18): Yahoo had not restated BYND's history for its
1-for-30 reverse split (2026-08-14) four days after the fact — every pull window and
both auto_adjust modes served pre-split bars in OLD-share scale (0.42) against
post-split bars in NEW-share scale (13.47), with 08-12/08-13 dropped entirely. A
wipe + re-pull therefore reproduces the cliff; the fix is arithmetic: for a symbol
with NO dividends, adjusted close before a K-for-1 event is exactly
as-traded x (1/ratio) — for a 1-for-30 reverse split, x30.

ANCHOR SAFETY: the row-level detector uses close_unadj (AS-TRADED, restored verbatim
by the 2026-08-18 split-fix machinery) as ground truth. r = close/close_unadj per
anchored row:
    r ~= 1        -> row is in OLD scale, needs the factor
    r ~= factor   -> row already adjusted, nothing to do (idempotent re-run)
    mixed / else  -> ABORT — never scale a series whose state you cannot prove
Volume is left as-traded on purpose (matches the P2.A Sharadar rebuild convention).
close_unadj is never touched (a print never changes).

DIVIDEND GUARD: the x(1/ratio) shortcut is only exact for a dividend-free symbol
(verified for BYND: zero dividends ever). For a dividend payer the true adjusted
close needs the vendor's closeadj — do not use this tool; rebuild from Sharadar.

  python fix_split_scale.py --symbol BYND --factor 30 --before 2026-08-14            # dry-run
  python fix_split_scale.py --symbol BYND --factor 30 --before 2026-08-14 --apply
"""
import argparse
import sys
from datetime import date

REPO = r"C:\Development\Trader"
sys.path.insert(0, REPO)

OLD_BAND = (0.8, 1.25)      # r ~= 1: old scale
TOL = 0.25                  # done-band half-width as a fraction of factor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--factor", type=float, required=True,
                    help="multiplier for pre-split OHLC (reverse 1-for-K split: K)")
    ap.add_argument("--before", required=True, help="split date (exclusive cutoff)")
    ap.add_argument("--apply", "--commit", action="store_true", dest="apply")
    a = ap.parse_args()
    sym = a.symbol.upper()
    cutoff = date.fromisoformat(a.before)
    f = a.factor

    from database.trader_database import DB

    rows = DB.execute_sql(
        "SELECT `date`,`close`,`close_unadj` FROM price_history "
        "WHERE `symbol`=%s AND `date` < %s ORDER BY `date`", (sym, cutoff)).fetchall()
    if not rows:
        sys.exit(f"{sym}: no rows before {cutoff}")

    old_n = done_n = odd_n = 0
    anchored = 0
    for d, c, cu in rows:
        if cu is None or float(cu) <= 0:
            continue
        anchored += 1
        r = float(c) / float(cu)
        if OLD_BAND[0] <= r <= OLD_BAND[1]:
            old_n += 1
        elif abs(r - f) <= TOL * f:
            done_n += 1
        else:
            odd_n += 1
    print(f"{sym}: {len(rows)} rows before {cutoff}; anchors={anchored} "
          f"(old-scale={old_n}, adjusted={done_n}, odd={odd_n})")

    if odd_n:
        sys.exit("ABORT: anchored rows outside both bands — scale state unprovable")
    if old_n and done_n:
        sys.exit("ABORT: MIXED scale state — refuse to blanket-multiply")
    if not old_n:
        print("nothing to do: series already in adjusted scale")
        return
    if anchored < 0.5 * len(rows):
        sys.exit(f"ABORT: only {anchored}/{len(rows)} rows anchored by close_unadj — "
                 "too little ground truth to certify the whole range")

    first, last = rows[0], rows[-1]
    print(f"will multiply OHLC x{f:g} on {len(rows)} rows "
          f"({first[0]}..{last[0]}); close_unadj and volume untouched")
    if not a.apply:
        print("DRY-RUN -- nothing written.")
        return

    cur = DB.execute_sql(
        "UPDATE price_history SET `open`=`open`*%s, `high`=`high`*%s, "
        "`low`=`low`*%s, `close`=`close`*%s WHERE `symbol`=%s AND `date` < %s",
        (f, f, f, f, sym, cutoff))
    print(f"updated {cur.rowcount} rows")

    chk = DB.execute_sql(
        "SELECT `date`,`close`,`close_unadj` FROM price_history "
        "WHERE `symbol`=%s ORDER BY `date` DESC LIMIT 8", (sym,)).fetchall()
    print("tail after fix:")
    for d, c, cu in reversed(chk):
        cu_s = f"{float(cu):.4f}" if cu is not None else "NULL"
        print(f"  {d}  close={float(c):<12.4f} unadj={cu_s}")


if __name__ == "__main__":
    main()
