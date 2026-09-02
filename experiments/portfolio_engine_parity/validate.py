"""Bit-exact parity validation: portfolio_engine forward ledger vs run_cascade_backtest.

Re-establishes the v70-ship validation (commit a9e171b8c: "Validated bit-exact vs
run_cascade_backtest for a single-version window — same open/closed, 0.00% delta")
after wiring the four missing Stage-3 call-alloc dampeners (SVR / MWDD / TVDD / BDIV)
into portfolio_engine._open_entries_for_day (live-vs-validated divergence since
2026-06-05; experiments/dd_onset_omens/FINDINGS.md WARN 1).

Method: a FRESH stateless replay of the engine over [START_DATE .. END] — a fake run
object whose id matches no DB rows, with _persist monkeypatched to capture instead of
write. READ-ONLY: nothing is persisted, no notifications. The frozen live ledger is
untouched (its realized history predates this fix by design and must NOT be recomputed).

Tolerances: the engine rounds each premium to cents at open (round(premium, 2)) and
its cash then compounds on the rounded values, so premiums match to <=$0.005 and
equity to cents-level accumulation. Everything else — position set, entry/exit dates,
exit reasons, pnl_pct — must be exact.

Run:  PYTHONIOENCODING=utf-8 python experiments/portfolio_engine_parity/validate.py [END_ISO]
"""
import os
import sys
import types
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import portfolio_engine as pe          # noqa: E402
import backtest_cascade as bc          # noqa: E402
import portfolio_profiles              # noqa: E402

PREMIUM_TOL = 0.0051          # engine rounds premium to cents at open
EQUITY_TOL = 1.0              # cents-rounding accumulation across the book
PNL_TOL = 1e-9                # pnl_pct comes from the SAME compute_outcome → exact


def main(end_iso=None):
    from database.models.core import AlgorithmVersion
    ver = AlgorithmVersion.get_active_scores_version()
    end = date.fromisoformat(end_iso) if end_iso else pe.last_completed_session(
        latest_data=pe._latest_price_date())
    start_usd = 36_500.0
    try:
        from database.models.portfolio import PortfolioRun
        live = PortfolioRun.get_active()
        if live is not None and live.starting_capital_usd:
            start_usd = float(live.starting_capital_usd)
    except Exception:
        pass
    print(f"Window {pe.START_DATE} -> {end} | version v{ver.id} | initial ${start_usd:,.2f}")

    # ---- engine side: fresh replay, captured instead of persisted -------------
    captured = {}

    def _capture(run, ledger, closed_out, new_open, snaps, cash, peak, D, version, fp, *args, **kwargs):
        # *args/**kwargs absorb trailing params (send_notifications, pending_requal, …) so this
        # read-only stub stays robust to _persist signature growth. Body uses only the named first-10.
        captured.update(ledger=ledger, closed=closed_out, snaps=snaps, cash=cash, peak=peak)

    orig_persist = pe._persist
    pe._persist = _capture
    try:
        fake = types.SimpleNamespace(
            id=987654321, name='parity', profile=pe.DEFAULT_PROFILE,
            start_date=pe.START_DATE, go_live_date=pe.GO_LIVE_DATE,
            starting_capital_cad=pe.START_CAPITAL_CAD, starting_capital_usd=start_usd,
            cad_per_usd=1.37, version_id=None, strategy_fingerprint=None,
            last_processed_date=None, cash_usd=None, peak_equity_usd=None,
        )
        pe._advance(fake, end, send_notifications=False)
    finally:
        pe._persist = orig_persist
    assert captured, "engine replay produced no capture"

    # ---- backtest side --------------------------------------------------------
    cfg, _profile = portfolio_profiles.apply_profile_to_config({}, pe.DEFAULT_PROFILE)
    calls_only = all(float((cfg.get('tier_alloc') or {}).get(k, 0) or 0) == 0
                     for k in ('p<=15', 'p16-20', 'p21-25'))
    res = bc.run_cascade_backtest(ver.id, min_score=pe.MIN_SCORE,
                                  from_date=pe.START_DATE, to_date=end,
                                  initial=start_usd, verbose=False,
                                  calls_only=calls_only, cfg=cfg)
    assert res, "run_cascade_backtest returned empty result"

    fails = []

    def _chk(cond, msg):
        print(('  OK   ' if cond else '  FAIL ') + msg)
        if not cond:
            fails.append(msg)

    # 1. closed trades ----------------------------------------------------------
    eng_closed = {(p['symbol'], p['entry_date']): p for p in captured['closed']}
    bt_closed = {(t['symbol'], t['entry_date']): t for t in res['trade_log']}
    print(f"\nClosed trades: engine {len(eng_closed)} vs backtest {len(bt_closed)}")
    _chk(set(eng_closed) == set(bt_closed),
         f"closed-trade SET identical (sym, entry_date) "
         f"[eng-only={sorted(set(eng_closed) - set(bt_closed))} "
         f"bt-only={sorted(set(bt_closed) - set(eng_closed))}]")
    for key in sorted(set(eng_closed) & set(bt_closed)):
        e, b = eng_closed[key], bt_closed[key]
        if e['exit_date'] != b['exit_date'] or e['exit_reason'] != b['outcome']:
            _chk(False, f"{key}: exit {e['exit_date']}/{e['exit_reason']} "
                        f"vs {b['exit_date']}/{b['outcome']}")
        if abs(e['premium_usd'] - b['premium']) > PREMIUM_TOL:
            _chk(False, f"{key}: premium {e['premium_usd']:.4f} vs {b['premium']:.4f}")
        if abs(e['pnl_pct'] - b['pnl_pct']) > PNL_TOL:
            _chk(False, f"{key}: pnl_pct {e['pnl_pct']:.10f} vs {b['pnl_pct']:.10f}")
    _chk(True, "per-trade exit date/reason exact, premium <=$0.005, pnl_pct exact "
               "(any mismatch printed above as FAIL)")

    # 2. open positions ---------------------------------------------------------
    eng_open = {(p['symbol'], p['entry_date']): p for p in captured['ledger']}
    bt_open = {(h['symbol'], h['entry_date']): h for h in res['open_holdings']}
    print(f"\nOpen positions: engine {len(eng_open)} vs backtest {len(bt_open)}")
    _chk(set(eng_open) == set(bt_open),
         f"open-position SET identical "
         f"[eng-only={sorted(set(eng_open) - set(bt_open))} "
         f"bt-only={sorted(set(bt_open) - set(eng_open))}]")
    max_prem_d = max((abs(eng_open[k]['premium_usd'] - bt_open[k]['premium'])
                      for k in set(eng_open) & set(bt_open)), default=0.0)
    _chk(max_prem_d <= PREMIUM_TOL, f"open premiums match (max delta ${max_prem_d:.4f})")

    # 3. cash / final equity (cost basis) ----------------------------------------
    eng_final = captured['cash'] + sum(p['premium_usd'] for p in captured['ledger'])
    d_eq = abs(eng_final - res['final_equity'])
    print(f"\nFinal equity (cost): engine ${eng_final:,.2f} vs backtest ${res['final_equity']:,.2f}")
    _chk(d_eq <= EQUITY_TOL, f"final equity delta ${d_eq:.4f} <= ${EQUITY_TOL:.2f} "
                             f"({d_eq / start_usd * 100:.4f}%)")

    # 4. MTM equity curve on common dates ----------------------------------------
    bt_mtm = dict(res['equity_curve_mtm'])
    worst = (0.0, None)
    common = 0
    for d, eq in captured['snaps']:
        if d in bt_mtm:
            common += 1
            dd = abs(eq - bt_mtm[d])
            if dd > worst[0]:
                worst = (dd, d)
    print(f"\nMTM equity curve: {common} common dates")
    _chk(common > 0 and worst[0] <= EQUITY_TOL,
         f"MTM curve max delta ${worst[0]:.4f} on {worst[1]}")

    print('\n' + ('PARITY: PASS' if not fails else f'PARITY: FAIL ({len(fails)} mismatches)'))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
