"""IV Premium Model -- wiring sanity check (Amendment 2026-07-12, FABLE ruling).

Exercises the ACTUAL wired IV_MODEL path in monte_carlo.py (IV_PREMIUM=1,
IV_MODEL=1) against 6 anchors, per the executor's STEP 3:
  1. a calm low-RV name             (KO    2023-05-31)
  2. a high-RV name (clamp check)   (LAES  2025-02-11)
  3. a 2020-03 date, VIX>60         (AAPL  2020-03-16, vix_close=82.69)
  4. a 2016 date (pre-panel)        (AAPL  2016-06-15)
  5. a missing-VIX date             (synthetic fallback probe -- no genuine
                                      NULL/gap exists anywhere in market_regime
                                      1995-01-03..2026-07-10, verified by direct
                                      query 2026-07-12, so this uses a date
                                      guaranteed absent from the table: a
                                      synthetic rv is supplied directly so the
                                      probe isolates the VIX-miss path)
  6. one exact panel row            (ELF   2022-08-04, actual panel atm_iv vs
                                      model prediction)

Foreground, read-only, seconds. ASCII-only output.
Usage: python experiments/iv_premium_model/sanity_check_ivmodel.py
"""
from __future__ import annotations

import os
import sys
import math
from datetime import date as _date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)          # pin repo root FIRST -- worktree PYTHONPATH trap guard

# IV_MODEL wiring is read at monte_carlo module-import time -- must be set BEFORE import.
os.environ["IV_PREMIUM"] = "1"
os.environ["IV_MODEL"] = "1"
os.environ["MC_NO_DB_PERSIST"] = "1"

import monte_carlo as mc  # noqa: E402  (env must be set first)
from database.trader_database import DB  # noqa: E402

assert mc.IV_PREMIUM is True and mc.IV_MODEL is True, "wiring env not honored at import time"


def fetch_closes(symbol, up_to):
    cur = DB.execute_sql(
        "SELECT date, close FROM price_history WHERE symbol=%s AND date<=%s ORDER BY date",
        (symbol, up_to))
    rows = cur.fetchall()
    dates = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    return dates, closes


def anchor_from_db(label, symbol, sig_date, dte):
    dates, closes = fetch_closes(symbol, sig_date)
    if not dates or dates[-1] != sig_date:
        raise SystemExit(f"[{label}] {symbol} {sig_date}: no price_history row on that exact date")
    base_idx = len(dates) - 1
    rv = mc.realized_vol(closes, base_idx)
    if rv is None:
        raise SystemExit(f"[{label}] {symbol} {sig_date}: realized_vol returned None (insufficient bars)")
    vix = mc._load_vix_series().get(sig_date)
    fallback_rv_pct = mc.PREMIUM_MULT * rv / 100.0   # engine's dte-invariant 30DTE fallback formula
    model_pct = mc._iv_premium_pct(symbol, sig_date, dte, fallback_rv_pct, rv=rv)
    atm_iv_hat_raw = mc.IV_MODEL_COEF_A + mc.IV_MODEL_COEF_B_RV * rv + mc.IV_MODEL_COEF_C_VIX * (vix or 0.0)
    clamped = "no"
    if vix is not None:
        if atm_iv_hat_raw < mc.IV_MODEL_CLAMP_FLOOR:
            clamped = "floor"
        elif atm_iv_hat_raw > mc.IV_MODEL_CLAMP_CAP:
            clamped = "cap"
    ratio = (model_pct / fallback_rv_pct) if fallback_rv_pct > 0 else None
    return {
        "label": label, "symbol": symbol, "date": str(sig_date), "dte": dte,
        "rv": rv, "vix": vix, "atm_iv_hat_raw": atm_iv_hat_raw, "clamped": clamped,
        "premium_pct_model": model_pct, "premium_pct_rv": fallback_rv_pct, "ratio": ratio,
    }


def main():
    rows = []

    # 1. Calm low-RV name (fit_report.txt hand-verified anchor: RV=0.610 VIX=17.94)
    rows.append(anchor_from_db("1_calm_low_rv", "KO", _date(2023, 5, 31), 23))

    # 2. High-RV name -- clamp CAP check (fit_report.txt: RV=24.940, raw pred clamps to CAP)
    rows.append(anchor_from_db("2_high_rv_clamp", "LAES", _date(2025, 2, 11), 24))

    # 3. 2020-03 COVID peak, VIX=82.69 (MarketRegime-confirmed) -- model IV should rise
    #    materially vs calm tape via the VIX term (c_vix * 82.69 ~= +0.516 to atm_iv_hat).
    rows.append(anchor_from_db("3_covid_vix82", "AAPL", _date(2020, 3, 16), 30))

    # 4. 2016 date -- pre-panel (panel starts 2022-08-01); model must still compute
    #    (the whole point of the model vs the raw-panel plug it extends).
    rows.append(anchor_from_db("4_prepanel_2016", "AAPL", _date(2016, 6, 15), 30))

    # 5. Missing-VIX fallback probe (synthetic). Verified 2026-07-12: 0 NULL vix_close
    #    rows anywhere in market_regime (1995-01-03..2026-07-10) -- no genuine gap exists
    #    to demonstrate against, so this calls _iv_premium_pct directly with a real rv
    #    but a date certain to be absent from the VIX series (market_regime's own max
    #    date is 2026-07-10; 2030-01-01 has no row at all), isolating the VIX-miss path.
    _model_misses_before = mc._MODEL_MISSES
    _synthetic_rv = 2.50
    _synthetic_fallback = mc.PREMIUM_MULT * _synthetic_rv / 100.0
    _probe_date = _date(2030, 1, 1)
    _probe_vix = mc._load_vix_series().get(_probe_date)
    assert _probe_vix is None, "probe date unexpectedly present in VIX series -- pick another"
    _probe_pct = mc._iv_premium_pct("SPY", _probe_date, 30, _synthetic_fallback, rv=_synthetic_rv)
    _miss_counted = (mc._MODEL_MISSES == _model_misses_before + 1)
    rows.append({
        "label": "5_missing_vix_probe(synthetic)", "symbol": "SPY(synthetic)",
        "date": str(_probe_date), "dte": 30, "rv": _synthetic_rv, "vix": None,
        "atm_iv_hat_raw": None, "clamped": "n/a",
        "premium_pct_model": _probe_pct, "premium_pct_rv": _synthetic_fallback,
        "ratio": _probe_pct / _synthetic_fallback,
    })
    print(f"[probe] fallback returned unchanged: {_probe_pct == _synthetic_fallback}  "
          f"model_miss_counted: {_miss_counted}")

    # 6. Exact panel row -- compare model prediction to the REAL observed panel atm_iv
    #    (fit_report.txt hand-verified anchor: ELF 2022-08-04, RV=3.425 VIX=21.44).
    panel_exact, _panel_asof = mc._load_iv_panel()
    elf_date = _date(2022, 8, 4)
    actual_panel_iv = panel_exact.get(("ELF", elf_date))
    r6 = anchor_from_db("6_exact_panel_row", "ELF", elf_date, 43)
    r6["actual_panel_iv"] = actual_panel_iv
    rows.append(r6)

    # ---- print ASCII table --------------------------------------------------
    hdr = (f"{'label':<26} {'sym':<8} {'date':<10} {'dte':>3} {'rv':>7} {'vix':>7} "
           f"{'atm_iv_hat':>10} {'clamp':>6} {'prem_model':>10} {'prem_rv':>9} {'ratio':>7}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        vix_s = f"{r['vix']:.2f}" if r["vix"] is not None else "MISSING"
        aih_s = f"{r['atm_iv_hat_raw']:.4f}" if r["atm_iv_hat_raw"] is not None else "n/a"
        ratio_s = f"{r['ratio']:.3f}" if r["ratio"] is not None else "n/a"
        print(f"{r['label']:<26} {r['symbol']:<8} {r['date']:<10} {r['dte']:>3} "
              f"{r['rv']:>7.3f} {vix_s:>7} {aih_s:>10} {r['clamped']:>6} "
              f"{r['premium_pct_model']:>10.4f} {r['premium_pct_rv']:>9.4f} {ratio_s:>7}")
        if "actual_panel_iv" in r:
            print(f"    -> actual panel atm_iv = {r['actual_panel_iv']:.4f}  "
                  f"(model raw atm_iv_hat = {r['atm_iv_hat_raw']:.4f}, "
                  f"model/actual ratio = {r['atm_iv_hat_raw']/r['actual_panel_iv']:.3f})")

    print()
    print(f"[counters] _MODEL_HITS={mc._MODEL_HITS}  _MODEL_MISSES={mc._MODEL_MISSES}  "
          f"_MODEL_CLAMP_FLOOR_HITS={mc._MODEL_CLAMP_FLOOR_HITS}  "
          f"_MODEL_CLAMP_CAP_HITS={mc._MODEL_CLAMP_CAP_HITS}")
    print(f"[counters] _IV_HITS={mc._IV_HITS}  _IV_MISSES={mc._IV_MISSES}  "
          f"(panel path -- expect 0/0, panel unused while IV_MODEL=1)")


if __name__ == "__main__":
    main()
