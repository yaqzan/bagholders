"""Per-position MTM-mark attribution: portfolio_engine._mark_pnl vs backtest _mtm_pnl.

Diagnoses the engine-snapshot-vs-backtest equity_curve_mtm divergence. UPDATED
2026-07-13 (experiments/mtm_divergence/DIAGNOSIS.md): the original 2026-06-08-era
divergence (engine MTM running below backtest on every weekend) was a *different*,
broader bug — backtest's intermediate MTM curve used trading-bar held/DTE
unconditionally even under CALENDAR_HOLD — fixed as a side effect of commit
3a585c2ed (2026-06-22). The residual divergence THIS tool now reproduces is narrower:
under CALENDAR_HOLD, backtest_cascade._mtm_pnl marks every open position using the
run-level NOMINAL_CAL_DTE constant instead of the position's own per-call `dte`
(unlike its own non-calendar branch, and unlike portfolio_engine._mark_pnl) — visible
only when a non-default-tenor position (e.g. a 15-DTE-router call, DAY_CAP=1) is open.

For a target date T it dumps, per open position:
  held_cal  = (T - entry_date).days             <- both bases' "held" when CALENDAR_HOLD
  held_bars = global trading-day index distance  <- diagnostic only; the non-calendar basis
  eng_mark  = pe._mark_pnl(p, T, ph)             <- the REAL engine function (per-call dte)
  bt_mark   = _bt_mark(p) below                  <- faithful backtest_cascade._mtm_pnl replica,
                                                     DEFECTS INCLUDED (constant DTE under
                                                     CALENDAR_HOLD) -- see its docstring
  $delta    = premium * (eng_mark - bt_mark)

Self-validating: the engine side must sum to the engine snapshot for T, and the
backtest replica must sum to the REAL equity_curve_mtm[T] — both to the cent — so
the per-position attribution is trustworthy, not a hand-rolled guess.

Run:  PYTHONIOENCODING=utf-8 python experiments/portfolio_engine_parity/dump_marks.py [TARGET_ISO=2026-06-08]
"""
import os
import sys
import types
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import portfolio_engine as pe          # noqa: E402
import backtest_cascade as bc          # noqa: E402
import portfolio_profiles              # noqa: E402
from option_pricing import option_pnl_pct  # noqa: E402


def main(target_iso='2026-06-08'):
    target = date.fromisoformat(target_iso)
    from database.models.core import AlgorithmVersion
    ver = AlgorithmVersion.get_active_scores_version()

    start_usd = 36_500.0
    try:
        from database.models.portfolio import PortfolioRun
        live = PortfolioRun.get_active()
        if live is not None and live.starting_capital_usd:
            start_usd = float(live.starting_capital_usd)
    except Exception:
        pass

    print(f"Target {target} | version v{ver.id} | initial ${start_usd:,.2f}")
    print(f"CALENDAR_HOLD={bc.CALENDAR_HOLD}  NOMINAL_CAL_DTE={bc.NOMINAL_CAL_DTE}  "
          f"DEFAULT_TOTAL_DTE={bc.DEFAULT_TOTAL_DTE}\n")

    # ---- engine side: fresh replay to TARGET, captured instead of persisted ----
    captured = {}

    def _capture(run, ledger, closed_out, new_open, snaps, cash, peak, D, version, fp, *a, **k):
        captured.update(ledger=ledger, snaps=snaps, cash=cash)

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
        pe._advance(fake, target, send_notifications=False)
    finally:
        pe._persist = orig_persist
    assert captured, "engine replay produced no capture"

    eng_ledger = captured['ledger']           # positions open at TARGET
    eng_cash = captured['cash']
    eng_snap = dict(captured['snaps']).get(target)

    # price history (same loader the engine/backtest use)
    syms = {p['symbol'] for p in eng_ledger}
    ph = bc.load_price_history(syms, pe.START_DATE) if syms else {}

    # ---- backtest side: real run to TARGET --------------------------------------
    cfg, _profile = portfolio_profiles.apply_profile_to_config({}, pe.DEFAULT_PROFILE)
    calls_only = all(float((cfg.get('tier_alloc') or {}).get(k, 0) or 0) == 0
                     for k in ('p<=15', 'p16-20', 'p21-25'))
    res = bc.run_cascade_backtest(ver.id, min_score=pe.MIN_SCORE,
                                  from_date=pe.START_DATE, to_date=target,
                                  initial=start_usd, verbose=False,
                                  calls_only=calls_only, cfg=cfg)
    bt_mtm_curve = dict(res['equity_curve_mtm'])
    bt_real_eq = bt_mtm_curve.get(target)
    bt_open = {(h['symbol'], h['entry_date']): h for h in res['open_holdings']}

    # global trading-day index — exactly how run_backtest builds it (union of ph dates)
    hold_days = cfg.get('hold_calendar_days', bc.HOLD_CALENDAR_DAYS)
    all_dates = sorted({r.date for rows in ph.values() for r in rows
                        if pe.START_DATE <= r.date <= target})
    td_idx = {d: i for i, d in enumerate(all_dates)}

    def _bt_mark(p):
        """Faithful replica of backtest_cascade._mtm_pnl for position dict p at TARGET.

        Mirrors production bit-for-bit, DEFECTS INCLUDED (experiments/mtm_divergence/
        DIAGNOSIS.md): when CALENDAR_HOLD, backtest_cascade marks every open position
        using the run-level NOMINAL_CAL_DTE constant, not the position's own per-call
        `dte` (unlike its own non-calendar branch, and unlike portfolio_engine._mark_pnl).
        Reproducing that defect here is intentional — this function exists to show where
        production disagrees with the live engine, so it must compute what production
        actually computes, not what it should compute. `bars` is always the trading-day
        distance (diagnostic column only; NOT necessarily what feeds option_pnl_pct).
        """
        rows = ph.get(p['symbol'])
        bars = td_idx.get(target, 0) - td_idx.get(p['entry_date'], 0)
        if not rows:
            return 0.0, bars
        close_t = pe._close_on(rows, target)
        if close_t is None or not p['entry_underlying'] or p['entry_underlying'] <= 0:
            return 0.0, bars
        if bc.CALENDAR_HOLD:
            held, total_dte = (target - p['entry_date']).days, bc.NOMINAL_CAL_DTE
        else:
            held, total_dte = bars, p['dte']
        if held <= 0:
            return 0.0, bars
        sigma = p['sigma_daily']
        if not sigma or sigma <= 0:
            return 0.0, bars
        ppct = p['premium_mult'] * sigma / 100.0
        return option_pnl_pct(p['side'], close_t, p['entry_underlying'], held,
                              premium_pct=ppct, total_dte=total_dte,
                              vega_ratio=1.0, delta=p['delta']), bars

    # ---- per-position attribution -----------------------------------------------
    rows_out = []
    eng_eq = eng_cash
    bt_eq = eng_cash      # replica uses the SAME cash (cost-basis cash is bit-exact)
    for p in eng_ledger:
        eng_m = pe._mark_pnl(p, target, ph)
        bt_m, bars = _bt_mark(p)
        held_cal = (target - p['entry_date']).days
        prem = p['premium_usd']
        eng_val = prem * (1.0 + eng_m)
        bt_val = prem * (1.0 + bt_m)
        eng_eq += eng_val
        bt_eq += bt_val
        rows_out.append({
            'sym': p['symbol'], 'entry': p['entry_date'], 'dte': p['dte'],
            'held_cal': held_cal, 'held_bars': bars,
            'eng_mark': eng_m, 'bt_mark': bt_m,
            'prem': prem, 'usd_delta': eng_val - bt_val,
        })

    rows_out.sort(key=lambda r: r['usd_delta'])
    print(f"{'SYM':<7}{'ENTRY':<12}{'dte':>4}{'h_cal':>6}{'h_bar':>6}"
          f"{'eng_mark':>10}{'bt_mark':>10}{'prem$':>10}{'usd_delta':>11}")
    print('-' * 86)
    for r in rows_out:
        print(f"{r['sym']:<7}{str(r['entry']):<12}{r['dte']:>4}{r['held_cal']:>6}{r['held_bars']:>6}"
              f"{r['eng_mark']:>10.4f}{r['bt_mark']:>10.4f}{r['prem']:>10.2f}{r['usd_delta']:>11.2f}")
    print('-' * 86)
    total_delta = sum(r['usd_delta'] for r in rows_out)
    print(f"{'TOTAL position $delta (engine - backtest):':<63}{total_delta:>11.2f}")

    print(f"\nEngine snapshot[{target}]          = ${eng_snap:,.2f}" if eng_snap is not None else "")
    print(f"Engine replica equity (recomputed)  = ${eng_eq:,.2f}   "
          f"(should == snapshot; delta ${abs((eng_snap or eng_eq) - eng_eq):.4f})")
    print(f"Backtest equity_curve_mtm[{target}] = ${bt_real_eq:,.2f}" if bt_real_eq is not None else "")
    print(f"Backtest replica equity (recomputed)= ${bt_eq:,.2f}   "
          f"(should == real; delta ${abs((bt_real_eq or bt_eq) - bt_eq):.4f})")
    if eng_snap is not None and bt_real_eq is not None:
        print(f"\nObserved engine-vs-backtest gap     = ${eng_snap - bt_real_eq:,.2f}  "
              f"(matches TOTAL position $delta above: ${total_delta:,.2f})")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else '2026-06-08'))
