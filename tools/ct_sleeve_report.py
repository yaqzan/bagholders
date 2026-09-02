"""tools/ct_sleeve_report.py -- standing CT-subset vs rest report for the LIVE
Portfolio ledger (.horizon/ledger-ct-instrument/TASK.md, "G2 sharpener").

WHY THIS EXISTS

The live paper ledger (v70-Apex-line tracker, portfolio_engine.py, running
since 2026-06-05) contains the accidental CT_PROMOTE sleeve's trades UNTAGGED
-- makes "is the accident paying on live forward data" a daily/on-demand
number instead of archaeology.

PROVENANCE CHECK DONE FIRST (per this task's own constraint: "if the engine
turns out to log promotion provenance already, USE it -- do not reconstruct
what exists"). Verified against source, not assumed:

  * `PortfolioPosition` (database/models/portfolio.py:106-190) has NO
    ct_flag/ct_tag/is_ct_promoted column. Promotion provenance is NOT
    persisted as a first-class field.
  * `tier` IS set to the CT override string at open time
    (portfolio_engine.py:1189: `tier = bc.CT_CALL_TIER if
    bc.ct_tag(score, trend, 'call') else bc.score_to_tier(score)`,
    `backtest_cascade.py:685`: `CT_CALL_TIER = '95+'`) -- but this is NOT a
    clean signal: a RAW (non-CT) score>=95 call resolves to the identical
    '95+' string via `score_to_tier()`. Same co-reachability V0_DOSSIER.md
    measured for monte_carlo.py's 'ultra' tier (~10% contamination). Cannot
    be used alone.
  * `routed_15dte` (BooleanField) IS a clean, directly-usable signal for the
    DTE-router interaction specifically (set by `portfolio_engine.py:1184-1185`
    via `bc._dte_router_call_eligible`) -- read directly below, no
    reconstruction needed, and cross-checked against V0_DOSSIER's measured
    ~18-21% router-share finding.
  * `entry_score` + `entry_date` + `entry_version_id` ARE persisted
    (sufficient to reconstruct: join back to `Score.trend` by
    (symbol, entry_date, version=entry_version_id), then apply the SAME
    shared predicate ct15-paper-sleeve uses -- tools/ct_predicate.py, no
    drift, no second implementation).

CONCLUSION: reconstruction (not a lookup of an existing field) is required
for the CT tag itself; `routed_15dte` is read directly as a bonus
corroborating column.

USAGE
    python tools/ct_sleeve_report.py
    python tools/ct_sleeve_report.py --run-id 5
    python tools/ct_sleeve_report.py --since 2026-05-08

Read-only on portfolio tables (no writes anywhere in this module).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ct_predicate import ct_tag  # noqa: E402

# ---------------------------------------------------------------------------
# Vehicle-campaign in-sample reference bands (FIXED historical findings --
# cited, never recomputed here). Source:
# experiments/ctsl_vehicle_2026_08/V6_DECEMBER_DRAFT.md section 2, "the
# in-sample band, locked now": population = ct_tag's call branch verbatim,
# v74, 2022-01-01..2026-06-15. These are ASSESSMENT-SURFACE barrier-touch win
# rates (a raw price-barrier outcome), NOT the live ledger's realized
# TP/SL/hard-sell/dead-hold-cascade win rate computed below -- printed side
# by side for situational reference, not as a like-for-like statistical test.
# ---------------------------------------------------------------------------
REFERENCE_BANDS = {
    "30dte_opt@30d": {"ct_wr": 32.61, "ct_ci": (23.89, 42.72), "ct_n": 92,
                       "contrast_wr": 35.85, "contrast_ci": (35.11, 36.59), "contrast_n": 16263},
    "15dte_opt@15d": {"ct_wr": 44.57, "ct_ci": (34.83, 54.74), "ct_n": 92,
                       "contrast_wr": 39.92, "contrast_ci": (39.17, 40.67), "contrast_n": 16263},
}
ROUTER_SHARE_REFERENCE = (18.1, 21.2)   # V0_DOSSIER.md V0.4: 5y / 22-now routed-15 row share, %
G5_POWER_GATE_N = 25   # V6_DECEMBER_DRAFT.md G-5: below this outcome-bearing N -> UNDERPOWERED


def _active_scoring_trend(symbol: str, entry_date, version_id):
    """Score.trend for the (symbol, entry_date, version) the position was
    actually opened under -- version-pinned so a formula change between then
    and now can't silently shift which trend value gets used."""
    from database.models.core import Score
    row = (Score.select(Score.trend)
           .where(Score.symbol == symbol, Score.date == entry_date, Score.version == version_id)
           .tuples().first())
    return row[0] if row else None


def reconstruct_tags(run_id=None, since=None):
    """Returns a list of dicts, one per PortfolioPosition (side='call' only --
    matches ct_predicate's scope and this book's puts-off canon), each
    carrying the reconstructed `ct` tag (True/False/None -- None = Score row
    for that (symbol, entry_date, version) could not be found, reported
    separately rather than silently folded into either bucket) and the
    directly-read `routed_15dte` flag."""
    from database.models.portfolio import PortfolioRun, PortfolioPosition

    if run_id is not None:
        from database.models.portfolio import PortfolioRun as _PR
        run = _PR.get_by_id(run_id)
    else:
        run = PortfolioRun.get_active()
    if run is None:
        raise SystemExit("no active PortfolioRun found")

    q = (PortfolioPosition.select(
            PortfolioPosition.symbol, PortfolioPosition.side, PortfolioPosition.entry_date,
            PortfolioPosition.entry_score, PortfolioPosition.entry_version_id,
            PortfolioPosition.tier, PortfolioPosition.routed_15dte, PortfolioPosition.status,
            PortfolioPosition.pnl_usd, PortfolioPosition.pnl_pct, PortfolioPosition.exit_reason,
            PortfolioPosition.dte)
         .where(PortfolioPosition.run_id == run.id, PortfolioPosition.side == 'call'))
    if since is not None:
        q = q.where(PortfolioPosition.entry_date >= since)
    rows = list(q.order_by(PortfolioPosition.entry_date).tuples())

    out = []
    for (symbol, side, entry_date, entry_score, entry_version_id, tier,
         routed_15dte, status, pnl_usd, pnl_pct, exit_reason, dte) in rows:
        ct = None
        if entry_score is not None and entry_version_id is not None:
            trend = _active_scoring_trend(symbol, entry_date, entry_version_id)
            if trend is not None:
                ct = (ct_tag(int(entry_score), trend, 'call') == 'ct_call')
        out.append({
            "symbol": symbol, "entry_date": entry_date, "entry_score": entry_score,
            "tier": tier, "routed_15dte": bool(routed_15dte), "status": status,
            "pnl_usd": float(pnl_usd) if pnl_usd is not None else None,
            "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
            "exit_reason": exit_reason, "dte": dte, "ct": ct,
        })
    return run, out


def _subset_stats(rows):
    closed = [r for r in rows if r["status"] == "closed" and r["pnl_usd"] is not None]
    open_n = sum(1 for r in rows if r["status"] != "closed")
    n_closed = len(closed)
    wins = sum(1 for r in closed if r["pnl_usd"] > 0)
    win_rate = (wins / n_closed * 100.0) if n_closed else None
    pnl_pcts = [r["pnl_pct"] for r in closed if r["pnl_pct"] is not None]
    mean_pnl_pct = (sum(pnl_pcts) / len(pnl_pcts) * 100.0) if pnl_pcts else None
    median_pnl_pct = (median(pnl_pcts) * 100.0) if pnl_pcts else None
    total_pnl_usd = sum(r["pnl_usd"] for r in closed) if closed else 0.0
    losses = [r["pnl_usd"] for r in closed if r["pnl_usd"] < 0]
    realized_loss_usd = sum(losses) if losses else 0.0
    routed_n = sum(1 for r in rows if r["routed_15dte"])
    return {
        "n_total": len(rows), "n_open": open_n, "n_closed": n_closed,
        "wins": wins, "win_rate": win_rate,
        "mean_pnl_pct": mean_pnl_pct, "median_pnl_pct": median_pnl_pct,
        "total_pnl_usd": total_pnl_usd, "realized_loss_usd": realized_loss_usd,
        "routed_n": routed_n,
    }


def _fmt(v, pct=False, dollar=False, nd=2):
    if v is None:
        return "n/a"
    if pct:
        return f"{v:.{nd}f}%"
    if dollar:
        return f"${v:,.2f}"
    return f"{v:.{nd}f}"


def render_report(run, rows) -> str:
    ct_rows = [r for r in rows if r["ct"] is True]
    rest_rows = [r for r in rows if r["ct"] is False]
    unknown_rows = [r for r in rows if r["ct"] is None]

    ct_stats = _subset_stats(ct_rows)
    rest_stats = _subset_stats(rest_rows)
    all_closed = [r for r in rows if r["status"] == "closed"]
    total_realized_loss = sum(r["pnl_usd"] for r in all_closed if r["pnl_usd"] is not None and r["pnl_usd"] < 0) or 0.0
    ct_dd_share = (ct_stats["realized_loss_usd"] / total_realized_loss * 100.0) if total_realized_loss else None

    lines = []
    A = lines.append
    W = 78
    A("=" * W)
    A(f"CT-SLEEVE LIVE-LEDGER REPORT -- run '{run.name}' (id={run.id}, profile={run.profile}, "
      f"start={run.start_date})")
    A("=" * W)
    A(f"Positions considered: {len(rows)} (call-side only; live book is puts-off)  "
      f"CT-tagged={len(ct_rows)}  rest={len(rest_rows)}  unresolved(no Score row)={len(unknown_rows)}")
    A("")

    if len(ct_rows) < G5_POWER_GATE_N:
        A(f"*** UNDERPOWERED: {len(ct_rows)} CT-tagged position(s) < the G-5 power-gate floor of "
          f"{G5_POWER_GATE_N} (experiments/ctsl_vehicle_2026_08/V6_DECEMBER_DRAFT.md) ***")
        A("*** Every number below is descriptive telemetry, not a statistical verdict. ***")
        A("")

    header = f"{'':22}{'CT-tagged':>16}{'Rest (non-CT)':>18}"
    A(header)
    A("-" * W)
    A(f"{'n total':22}{ct_stats['n_total']:>16}{rest_stats['n_total']:>18}")
    A(f"{'n open':22}{ct_stats['n_open']:>16}{rest_stats['n_open']:>18}")
    A(f"{'n closed':22}{ct_stats['n_closed']:>16}{rest_stats['n_closed']:>18}")
    A(f"{'win rate (closed)':22}{_fmt(ct_stats['win_rate'], pct=True):>16}{_fmt(rest_stats['win_rate'], pct=True):>18}")
    A(f"{'mean pnl% (closed)':22}{_fmt(ct_stats['mean_pnl_pct'], pct=True):>16}{_fmt(rest_stats['mean_pnl_pct'], pct=True):>18}")
    A(f"{'median pnl% (closed)':22}{_fmt(ct_stats['median_pnl_pct'], pct=True):>16}{_fmt(rest_stats['median_pnl_pct'], pct=True):>18}")
    A(f"{'total pnl$ (closed)':22}{_fmt(ct_stats['total_pnl_usd'], dollar=True):>16}{_fmt(rest_stats['total_pnl_usd'], dollar=True):>18}")
    A(f"{'realized loss$':22}{_fmt(ct_stats['realized_loss_usd'], dollar=True):>16}{_fmt(rest_stats['realized_loss_usd'], dollar=True):>18}")
    A(f"{'routed_15dte n':22}{ct_stats['routed_n']:>16}{rest_stats['routed_n']:>18}")
    A("")
    A(f"DD-contribution proxy: CT subset's share of TOTAL realized losses across the whole "
      f"book = {_fmt(ct_dd_share, pct=True)}")
    A("  (proxy = sum(negative closed pnl$, CT) / sum(negative closed pnl$, ALL); NOT a true")
    A("   peak-to-trough equity-curve decomposition -- that needs a counterfactual re-simulation,")
    A("   out of scope for a standing CLI report.)")
    A("")

    router_share_all = (ct_stats["routed_n"] + rest_stats["routed_n"])
    router_share_pct = (router_share_all / len(all_closed) * 100.0) if all_closed else None
    A(f"Router cross-check: {router_share_all}/{len(rows)} positions ({_fmt(router_share_pct, pct=True)}) "
      f"carry routed_15dte=True on the live book, vs V0_DOSSIER.md's measured "
      f"{ROUTER_SHARE_REFERENCE[0]}-{ROUTER_SHARE_REFERENCE[1]}% (5y/22-now, ultra-only vehicle tape).")
    A("")

    A("-" * W)
    A("Reference: vehicle-campaign IN-SAMPLE ASSESSMENT band (barrier-touch WR, NOT the live")
    A("ledger's realized cascade WR above -- different instruments, printed for situational")
    A("reference only). Source: experiments/ctsl_vehicle_2026_08/V6_DECEMBER_DRAFT.md sec.2.")
    for name, b in REFERENCE_BANDS.items():
        A(f"  {name:16} CT-promoted WR={b['ct_wr']:.2f}% [{b['ct_ci'][0]:.2f},{b['ct_ci'][1]:.2f}] "
          f"(N={b['ct_n']})   contrast WR={b['contrast_wr']:.2f}% (N={b['contrast_n']:,})")
    A("=" * W)

    if unknown_rows:
        A("")
        A(f"UNRESOLVED ({len(unknown_rows)}) -- no Score row found at (symbol, entry_date, "
          f"entry_version_id); excluded from both subsets above, listed for audit:")
        for r in unknown_rows[:20]:
            A(f"  {r['symbol']:8} {r['entry_date']}  entry_score={r['entry_score']}")

    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", type=int, default=None, help="PortfolioRun id (default: active run)")
    ap.add_argument("--since", type=str, default=None, help="YYYY-MM-DD floor on entry_date")
    args = ap.parse_args(argv)

    from datetime import date as _date
    since = _date.fromisoformat(args.since) if args.since else None

    run, rows = reconstruct_tags(run_id=args.run_id, since=since)
    print(render_report(run, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
