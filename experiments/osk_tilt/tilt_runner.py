"""OSK Stage-3 allocation tilt -- experiment runner.

See DESIGN.md (pre-registered, read it before touching this file). This
script:

  1. Builds the 4 locked z-score maps (raw_lag0, raw_lag1, resid_lag0,
     resid_lag1) via build_osk_zmaps.build_all() (local parquet only, no
     MySQL).
  2. Forks backtest_cascade.run_backtest via inspect.getsource() + two
     verified (count==1 asserted) textual insertions -- adds one new
     independent multiplicative `osk_scale` term into the existing
     base_premium sizing product. g=0 forces osk_scale==1.0 at every entry,
     so the fork is byte-identical in behavior to the original for g=0
     (verified by the mandatory reproduction check below). exec()'d with
     backtest_cascade's OWN __dict__ as globals, so every existing
     constant/helper (TIER_ALLOC, _svr_call_scale, regime_on_or_before, ...)
     resolves exactly as production does. NEVER edits backtest_cascade.py or
     strategy_config.py on disk.
  3. Runs 1 fully UNPATCHED reference call + 6 patched calls (baseline g=0 +
     5 locked variants) via backtest_cascade.run_cascade_backtest -- this
     hits real MySQL (scores/prices/breadth/regime/earnings), so this
     script must be QUEUED, never run raw:

         trader queue submit --priority normal --db heavy --cpu 2 \\
           --dedup osk_tilt_v1 --reason "OSK Stage-3 allocation tilt (osk_tilt)" \\
           --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \\
           -- python experiments/osk_tilt/tilt_runner.py

  4. Verifies the baseline-reproduction identity (mandatory; aborts before
     any variant table is written if it fails).
  5. Computes era compound / era WorstDD per variant vs baseline, plus the
     per-entry sizing-weighted mean pnl15 uplift (date-clustered bootstrap
     t) and n_entries tilted vs neutral.
  6. Writes tilt_results.json / tilt_results.txt under experiments/osk_tilt/.

Hard stops: no edits to backtest_cascade.py/strategy_config.py; MySQL only
through this queued script; outputs confined to experiments/osk_tilt/ and
.cache/osk_tilt/; HOLDOUT_DISABLE never set; no variant expansion beyond the
5+baseline table in DESIGN.md.
"""
import inspect
import json
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

import numpy as np  # noqa: E402

import backtest_cascade as bc  # noqa: E402
from experiments._holdout import assert_no_holdout_leak  # noqa: E402
from experiments.osk_tilt import build_osk_zmaps  # noqa: E402

VERSION_ID = 74                      # active scores (AlgorithmVersion.get_active_scores_version())
WIN_START = date(2025, 2, 1)
WIN_END = date(2026, 6, 15)          # == strategy_config.CALIBRATION_CUTOFF_DATE
INITIAL_CAPITAL = 50_000.0
OUT_DIR = _HERE

VARIANTS = [
    # (label, zmap_key or None, g)
    ("raw_lag0_g015", "raw_lag0", 0.15),
    ("raw_lag0_g030", "raw_lag0", 0.30),
    ("raw_lag1_g030", "raw_lag1", 0.30),
    ("resid_lag0_g030", "resid_lag0", 0.30),
    ("resid_lag1_g030", "resid_lag1", 0.30),
]

# ---------------------------------------------------------------------------
# Shared mutable state read by the exec'd patched run_backtest.
# ---------------------------------------------------------------------------
_OSK_STATE = {
    "map": None,               # {(symbol, date_iso_str): z} or None
    "g": 0.0,
    "lo": 0.70,
    "hi": 1.30,
    "window_end": None,        # datetime.date -- outcomes_by_date entries after this are dropped
    "renorm_by_date": {},      # populated by the patched fn at call time; read back after each run
}


def _build_patched_run_backtest():
    """Fork backtest_cascade.run_backtest with one added multiplicative sizing
    term. See DESIGN.md 'Engine integration'. Raises AssertionError loudly if
    backtest_cascade.py has drifted since this was written (anchor not found
    exactly once) rather than silently patching the wrong thing."""
    src = inspect.getsource(bc.run_backtest)

    sig_anchor = "def run_backtest(outcomes_by_date: dict,\n"
    assert src.count(sig_anchor) == 1, "run_backtest signature line not found exactly once -- source drifted"
    src = src.replace(sig_anchor, "def run_backtest_osk_tilted(outcomes_by_date: dict,\n", 1)

    top_anchor = "    cfg        = cfg or {}\n"
    assert src.count(top_anchor) == 1, "top-of-function anchor not found exactly once -- source drifted"
    top_inject = (
        "    # --- OSK Stage-3 tilt (experiments/osk_tilt/tilt_runner.py): date-filter\n"
        "    # + per-day renormalization precompute. No-op (byte-identical) whenever\n"
        "    # _OSK_STATE['map'] is None or _OSK_STATE['g'] is 0 -- see DESIGN.md.\n"
        "    _osk_we = _OSK_STATE.get('window_end')\n"
        "    if _osk_we is not None:\n"
        "        outcomes_by_date = {_d: _v for _d, _v in outcomes_by_date.items() if _d <= _osk_we}\n"
        "    _osk_zmap = _OSK_STATE.get('map')\n"
        "    _osk_g = _OSK_STATE.get('g', 0.0) or 0.0\n"
        "    _osk_lo = _OSK_STATE.get('lo', 0.70)\n"
        "    _osk_hi = _OSK_STATE.get('hi', 1.30)\n"
        "    _osk_renorm_by_date = {}\n"
        "    if _osk_zmap is not None and _osk_g:\n"
        "        for _osk_d, _osk_outs in outcomes_by_date.items():\n"
        "            _osk_elig = [_o for _o in _osk_outs\n"
        "                         if getattr(_o, 'side', 'call') == 'call' and _o.tier != '70-74']\n"
        "            if not _osk_elig:\n"
        "                continue\n"
        "            _osk_mraw = []\n"
        "            for _o in _osk_elig:\n"
        "                _z = _osk_zmap.get((_o.symbol, str(_o.signal_date)))\n"
        "                if _z is None:\n"
        "                    _osk_mraw.append(1.0)\n"
        "                else:\n"
        "                    _m = 1.0 + _osk_g * _z\n"
        "                    _m = min(_osk_hi, max(_osk_lo, _m))\n"
        "                    _osk_mraw.append(_m)\n"
        "            _s = sum(_osk_mraw)\n"
        "            _osk_renorm_by_date[_osk_d] = (len(_osk_elig) / _s) if _s > 1e-9 else 1.0\n"
        "    _OSK_STATE['renorm_by_date'] = _osk_renorm_by_date\n"
    )
    src = src.replace(top_anchor, top_inject + top_anchor, 1)

    mid_anchor = (
        "            base_premium = alloc_frac * reg_scale * dd_scale * saw_scale * sat_scale "
        "* rxdd_scale * svr_scale * mwdd_scale * tvdd_scale * bdiv_scale * spread_tilt_scale * allocation_base\n"
    )
    assert src.count(mid_anchor) == 1, "base_premium anchor not found exactly once -- source drifted"
    mid_inject = (
        "            _osk_scale = 1.0\n"
        "            if (not is_put) and outcome.tier != '70-74' and _osk_zmap is not None and _osk_g:\n"
        "                _osk_z_i = _osk_zmap.get((outcome.symbol, str(outcome.signal_date)))\n"
        "                if _osk_z_i is not None:\n"
        "                    _osk_m = 1.0 + _osk_g * _osk_z_i\n"
        "                    _osk_m = min(_osk_hi, max(_osk_lo, _osk_m))\n"
        "                    _osk_scale = _osk_m * _osk_renorm_by_date.get(today, 1.0)\n"
    )
    new_mid_anchor = mid_anchor.rstrip("\n") + " * _osk_scale\n"
    src = src.replace(mid_anchor, mid_inject + new_mid_anchor, 1)

    ns = dict(vars(bc))
    ns["_OSK_STATE"] = _OSK_STATE
    code = compile(src, "<osk_tilt_patched_run_backtest>", "exec")
    exec(code, ns)
    fn = ns["run_backtest_osk_tilted"]
    return fn


_PATCHED_FN = None
_ORIG_RUN_BACKTEST = bc.run_backtest


def install_patch():
    global _PATCHED_FN
    if _PATCHED_FN is None:
        _PATCHED_FN = _build_patched_run_backtest()
    bc.run_backtest = _PATCHED_FN


def restore_patch():
    bc.run_backtest = _ORIG_RUN_BACKTEST


# ---------------------------------------------------------------------------
# Engine invocations
# ---------------------------------------------------------------------------
def _engine_call():
    return bc.run_cascade_backtest(
        VERSION_ID,
        min_score=70.0,
        from_date=WIN_START,
        to_date=None,
        initial=INITIAL_CAPITAL,
        verbose=False,
        calls_only=True,
        cfg=None,
    )


def run_unpatched():
    restore_patch()
    t0 = time.time()
    result = _engine_call()
    print(f"  [unpatched reference] {time.time()-t0:.0f}s, {len(result.get('trade_log', [])):,} trades", flush=True)
    return result


def run_variant(zmap, g, lo=0.70, hi=1.30):
    _OSK_STATE["map"] = zmap
    _OSK_STATE["g"] = g
    _OSK_STATE["lo"] = lo
    _OSK_STATE["hi"] = hi
    _OSK_STATE["window_end"] = WIN_END
    t0 = time.time()
    result = _engine_call()
    renorm_copy = dict(_OSK_STATE.get("renorm_by_date", {}))
    elapsed = time.time() - t0
    n_trades = len(result.get("trade_log", []))
    if n_trades:
        max_entry = max(t["entry_date"] for t in result["trade_log"])
        assert max_entry <= WIN_END, (
            f"[HOLDOUT/WINDOW LEAK] patched trade_log contains entry_date {max_entry} "
            f"> window_end {WIN_END}"
        )
    print(f"    {elapsed:.0f}s, {n_trades:,} trades", flush=True)
    return result, renorm_copy


# ---------------------------------------------------------------------------
# Reproduction check
# ---------------------------------------------------------------------------
def check_reproduction(ref: dict, base: dict, window_end: date) -> tuple[bool, dict]:
    ref_trades = [t for t in ref["trade_log"] if t["entry_date"] <= window_end]
    base_trades = list(base["trade_log"])

    def _key(t):
        return (t["entry_date"], t["symbol"], t["exit_date"], t.get("side", "call"))

    ref_sorted = sorted(ref_trades, key=_key)
    base_sorted = sorted(base_trades, key=_key)

    trades_match = len(ref_sorted) == len(base_sorted)
    n_field_mismatch = 0
    if trades_match:
        for rt, bt in zip(ref_sorted, base_sorted):
            ok = (
                rt["symbol"] == bt["symbol"]
                and rt["entry_date"] == bt["entry_date"]
                and rt["exit_date"] == bt["exit_date"]
                and rt["outcome"] == bt["outcome"]
                and abs(rt["premium"] - bt["premium"]) < 1e-6
                and abs(rt["pnl_pct"] - bt["pnl_pct"]) < 1e-9
                and abs(rt["pnl"] - bt["pnl"]) < 1e-6
            )
            if not ok:
                n_field_mismatch += 1
        trades_match = n_field_mismatch == 0

    ref_eq = {d: eq for d, eq in ref["equity_curve"] if d <= window_end}
    base_eq = {d: eq for d, eq in base["equity_curve"] if d <= window_end}
    same_days = set(ref_eq) == set(base_eq)
    eq_match = same_days and all(abs(ref_eq[d] - base_eq[d]) < 1e-6 for d in ref_eq)

    ok = trades_match and eq_match
    detail = {
        "n_ref_trades_in_window": len(ref_sorted),
        "n_base_trades": len(base_sorted),
        "n_field_mismatch": n_field_mismatch,
        "trades_match": trades_match,
        "eq_days_match": same_days,
        "eq_values_match": eq_match,
        "n_ref_eq_days": len(ref_eq),
        "n_base_eq_days": len(base_eq),
    }
    return ok, detail


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def era_metrics(result: dict) -> dict:
    final_eq = result["final_equity"]
    return {
        "final_equity": round(final_eq, 2),
        "compound_pct": round((final_eq / INITIAL_CAPITAL - 1.0) * 100, 2),
        "worst_dd_pct": round(result["max_dd"] * 100, 2),
        "worst_dd_mtm_pct": round(result["max_dd_mtm"] * 100, 2),
        "n_trades": len(result["trade_log"]),
        "n_open_holdings": len(result.get("open_holdings", [])),
    }


def _pnl15_map() -> dict:
    """Canonical (lag-invariant) realized pnl15 lookup, keyed (symbol, date_iso_str).
    Built from the 65-floor (lag0) ledger's ripe rows -- pnl15/entry_premium never
    depend on lag_days (only the OTM skew legs are lagged), and the 70-floor lag1
    population is a strict subset of the 65-floor lag0 population by construction."""
    import polars as pl
    path = os.path.join(_ROOT, ".cache", "osk_era", "osk_era_ledger_65.parquet")
    df = pl.read_parquet(path)
    assert_no_holdout_leak(df, context="osk_tilt pnl15_map")
    df = df.filter(pl.col("ripe") & pl.col("pnl15").is_not_null())
    return {
        (s, d): float(p)
        for s, d, p in zip(df["symbol"].to_list(), df["date"].to_list(), df["pnl15"].to_list())
    }


def compute_uplift(trade_log: list, zmap: dict | None, g: float, lo: float, hi: float,
                    renorm_by_date: dict, pnl15_map: dict) -> dict | None:
    rows = []
    for t in trade_log:
        if t.get("side", "call") != "call" or t["tier"] == "70-74":
            continue
        key = (t["symbol"], str(t["entry_date"]))
        p15 = pnl15_map.get(key)
        if p15 is None:
            continue
        z = zmap.get(key) if (zmap is not None and g) else None
        if z is None:
            m = 1.0
        else:
            m_raw = min(hi, max(lo, 1.0 + g * z))
            m = m_raw * renorm_by_date.get(t["entry_date"], 1.0)
        rows.append({"date": t["entry_date"], "pnl15": p15, "m": m, "tilted": z is not None})

    if not rows:
        return None

    pnl15_arr = np.array([r["pnl15"] for r in rows], dtype=float)
    m_arr = np.array([r["m"] for r in rows], dtype=float)
    dates_arr = np.array([r["date"] for r in rows])

    baseline_mean = float(pnl15_arr.mean())
    weighted_mean = float((m_arr * pnl15_arr).sum() / m_arr.sum())
    uplift = weighted_mean - baseline_mean

    uniq_dates = np.unique(dates_arr)
    idx_by_date = {d: np.where(dates_arr == d)[0] for d in uniq_dates}
    rng = np.random.default_rng(20260710)
    n_boot = 2000
    boot = np.empty(n_boot)
    for b in range(n_boot):
        samp = rng.choice(uniq_dates, size=len(uniq_dates), replace=True)
        idxs = np.concatenate([idx_by_date[d] for d in samp])
        p = pnl15_arr[idxs]
        m = m_arr[idxs]
        boot[b] = (m * p).sum() / m.sum() - p.mean()
    se = float(boot.std(ddof=1))
    t_clust = (uplift / se) if se > 1e-12 else float("nan")

    n_tilted = sum(1 for r in rows if r["tilted"])
    return {
        "n_entries": len(rows),
        "n_tilted": n_tilted,
        "n_neutral": len(rows) - n_tilted,
        "n_unique_dates": int(len(uniq_dates)),
        "baseline_mean_pnl15": round(baseline_mean, 5),
        "weighted_mean_pnl15": round(weighted_mean, 5),
        "uplift": round(uplift, 5),
        "se_boot": round(se, 5),
        "t_clust": round(t_clust, 3),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"OSK Stage-3 tilt runner -- version={VERSION_ID} window={WIN_START}..{WIN_END}", flush=True)
    print("HARD CEILING: option data spans ~1.3y; canonical N=500x8-window T1-T7 gate is UNREACHABLE. "
          "Outcome is STAGE or PARK, never SHIP.", flush=True)

    print("Building z-score maps (local parquet, no MySQL)...", flush=True)
    zmaps = build_osk_zmaps.build_all()
    for k, v in zmaps.items():
        assert v["n"] > 0, f"z-map {k} is empty"

    pnl15_map = _pnl15_map()
    print(f"  pnl15 map: {len(pnl15_map):,} ripe (symbol,date) rows", flush=True)

    print("Run 0/7: unpatched reference...", flush=True)
    ref = run_unpatched()

    install_patch()
    try:
        print("Run 1/7: baseline (patched, g=0)...", flush=True)
        base_result, base_renorm = run_variant(None, 0.0)

        print("Reproduction check (unpatched vs patched g=0, restricted to <= window_end)...", flush=True)
        repro_ok, repro_detail = check_reproduction(ref, base_result, WIN_END)
        print(f"  reproduction: {'PASS' if repro_ok else 'FAIL'} -- {repro_detail}", flush=True)

        variant_results = {}
        if repro_ok:
            for i, (label, zmap_key, g) in enumerate(VARIANTS, start=2):
                print(f"Run {i}/7: {label} (g={g}, zmap={zmap_key})...", flush=True)
                zmap = zmaps[zmap_key]["map"]
                result, renorm = run_variant(zmap, g)
                variant_results[label] = {
                    "result": result, "renorm": renorm, "zmap_key": zmap_key, "g": g,
                }
        else:
            print("  ABORTING variant runs -- reproduction check failed, engine fork is not trustworthy.",
                  flush=True)
    finally:
        restore_patch()

    if not repro_ok:
        # Write a minimal, honest failure report instead of crashing uncaught -- the
        # reproduction check is mandatory-gating, so no variant table is trustworthy.
        os.makedirs(OUT_DIR, exist_ok=True)
        fail_report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reproduction_check": {"ok": False, "detail": repro_detail},
            "verdict": "PARK",
            "reason": "MANDATORY baseline-reproduction check FAILED -- engine fork does not "
                      "reproduce the unpatched run; no variant is trustworthy on this basis.",
        }
        with open(os.path.join(OUT_DIR, "tilt_results.json"), "w") as f:
            json.dump(fail_report, f, indent=2, default=str)
        with open(os.path.join(OUT_DIR, "tilt_results.txt"), "w", encoding="utf-8") as f:
            f.write("OSK Stage-3 allocation tilt -- results\n" + "=" * 72 + "\n")
            f.write(f"REPRODUCTION CHECK FAILED: {repro_detail}\n")
            f.write("VERDICT: PARK (engine fork invalid -- see DESIGN.md before retrying)\n")
        print("VERDICT: PARK (reproduction check failed)", flush=True)
        return fail_report

    # --- assemble report ---
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version_id": VERSION_ID,
        "window": [str(WIN_START), str(WIN_END)],
        "initial_capital": INITIAL_CAPITAL,
        "hard_ceiling": "option data spans ~1.3y; canonical N=500 x 8-window T1-T7 gate is UNREACHABLE; outcome is STAGE or PARK, never SHIP",
        "reproduction_check": {"ok": repro_ok, "detail": repro_detail},
        "zmap_manifest": {k: {kk: vv for kk, vv in v.items() if kk != "map"} for k, v in zmaps.items()},
        "baseline": {
            "era_metrics": era_metrics(base_result),
            "uplift": compute_uplift(base_result["trade_log"], None, 0.0, 0.70, 1.30, base_renorm, pnl15_map),
        },
        "variants": {},
    }

    baseline_em = report["baseline"]["era_metrics"]
    for label, v in variant_results.items():
        em = era_metrics(v["result"])
        up = compute_uplift(
            v["result"]["trade_log"], zmaps[v["zmap_key"]]["map"], v["g"], 0.70, 1.30,
            v["renorm"], pnl15_map,
        )
        report["variants"][label] = {
            "g": v["g"],
            "zmap_key": v["zmap_key"],
            "era_metrics": em,
            "uplift": up,
            "vs_baseline": {
                "compound_delta_pp": round(em["compound_pct"] - baseline_em["compound_pct"], 2),
                "dd_delta_pp": round(em["worst_dd_pct"] - baseline_em["worst_dd_pct"], 2),
            },
        }

    # --- apply LOCKED BARS ---
    passing = []
    for label, v in report["variants"].items():
        dd_ok = v["vs_baseline"]["dd_delta_pp"] <= 0
        compound_ok = v["vs_baseline"]["compound_delta_pp"] >= 0
        t_ok = (v["uplift"] is not None) and (v["uplift"]["t_clust"] >= 2.0)
        v["bar_pass"] = {"dd_ok": dd_ok, "compound_ok": compound_ok, "t_ok": t_ok,
                          "all_pass": dd_ok and compound_ok and t_ok}
        if v["bar_pass"]["all_pass"]:
            passing.append(label)
    has_lag1 = any("lag1" in p for p in passing)
    verdict = "STAGE" if (len(passing) >= 2 and has_lag1) else "PARK"
    report["verdict"] = verdict
    report["passing_variants"] = passing

    os.makedirs(OUT_DIR, exist_ok=True)
    json_path = os.path.join(OUT_DIR, "tilt_results.json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    txt_path = os.path.join(OUT_DIR, "tilt_results.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("OSK Stage-3 allocation tilt -- results\n")
        f.write("=" * 72 + "\n")
        f.write(f"generated_at: {report['generated_at']}\n")
        f.write(f"version_id={VERSION_ID}  window={WIN_START}..{WIN_END}\n")
        f.write("HARD CEILING: option data spans ~1.3y; N=500x8-window T1-T7 gate UNREACHABLE. "
                "Outcome is STAGE or PARK, never SHIP.\n\n")
        f.write(f"Reproduction check: {'PASS' if repro_ok else 'FAIL'} -- {repro_detail}\n\n")
        f.write(f"Baseline: compound={baseline_em['compound_pct']}%  "
                f"worst_dd={baseline_em['worst_dd_pct']}%  n_trades={baseline_em['n_trades']}\n\n")
        f.write(f"{'variant':<20}{'g':>6}{'compound%':>12}{'d_cmpd_pp':>11}{'dd%':>8}{'d_dd_pp':>9}"
                f"{'n_elig':>8}{'n_tilt':>8}{'uplift':>9}{'t_clust':>9}{'bars':>7}\n")
        for label, v in report["variants"].items():
            em = v["era_metrics"]
            up = v["uplift"] or {}
            bp = v["bar_pass"]
            f.write(
                f"{label:<20}{v['g']:>6.2f}{em['compound_pct']:>12.2f}"
                f"{v['vs_baseline']['compound_delta_pp']:>11.2f}{em['worst_dd_pct']:>8.2f}"
                f"{v['vs_baseline']['dd_delta_pp']:>9.2f}{up.get('n_entries', 0):>8}"
                f"{up.get('n_tilted', 0):>8}{up.get('uplift', float('nan')):>9.4f}"
                f"{up.get('t_clust', float('nan')):>9.2f}{'PASS' if bp['all_pass'] else '-':>7}\n"
            )
        f.write(f"\nPassing variants: {passing}\n")
        f.write(f"VERDICT: {verdict}\n")

    print(f"\nWrote {json_path}\nWrote {txt_path}", flush=True)
    print(f"VERDICT: {verdict}  (passing: {passing})", flush=True)
    return report


if __name__ == "__main__":
    main()
