"""
VEGA_STATE calibration -- ATM-IV-vs-VIX response, fit on the OWN-panel
(non-Polygon) real-contract IV ledgers: .cache/iv_skew/iv_ledger.parquet +
iv_ledger_ext.parquet. Gameplan P1.4 ("crash-IV vega state").

Distinct from experiments/iv_premium_model/ (which fits atm_iv ~ RV+VIX on
the Polygon panel to reprice ENTRY premium/cost). This calibration fits a
simpler atm_iv ~ VIX response on our OWN real-contract-IV panel (op.iv
straight from option_prices, not BS-inverted from close -- see
experiments/iv_skew/build_iv.py), and is consumed as a RATIO between a held
position's entry-VIX-implied IV and its current-bar-VIX-implied IV -- i.e.
the multiplicative "crash-vega credit" a long call should get when VIX rises
during the hold (or a haircut when it falls). Feeds into
option_pricing.option_pnl_pct's existing `vega_ratio` parameter, applied
ONLY inside monte_carlo.py's dead-hold call walk (_compute_dead_hold_call),
which today hardcodes vega_ratio=1.0 for the entire walk (see that
function's docstring / the comment in resolve() -- "dead-hold walk uses
vega=1.0 ... vega-aware re-walk is deferred").

Read-only: two small local parquets (2018 + 845 rows) + the already-
materialized VIX-by-date parquet (.cache/iv_premium_model/vix_series.parquet,
built by experiments/iv_premium_model/build_vix_series.py from
MarketRegime.vix_close). No MySQL touch -- safe to run directly, not queued.

Holdout: iv_ledger_ext.parquet carries an explicit is_oos flag (verified:
is_oos=False rows run 2026-05-18..2026-06-12 [pre-cutoff]; is_oos=True rows
run 2026-06-16..2026-07-06 [POST CALIBRATION_CUTOFF_DATE=2026-06-15]). Only
is_oos=False rows enter the fit; the base ledger is independently all
pre-cutoff (max date 2026-05-15). Belt-and-suspenders: pre_cutoff_filter +
assert_no_holdout_leak both re-applied on the combined frame.

Output: experiments/vega_state/calibration.json
"""
import os
import sys
import json
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import polars as pl
import numpy as np

from experiments._holdout import pre_cutoff_filter, assert_no_holdout_leak, CUTOFF

BASE_LEDGER = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger.parquet")
EXT_LEDGER = os.path.join(_ROOT, ".cache", "iv_skew", "iv_ledger_ext.parquet")
VIX_SERIES = os.path.join(_ROOT, ".cache", "iv_premium_model", "vix_series.parquet")
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")


def main():
    base = pl.read_parquet(BASE_LEDGER, columns=['symbol', 'date', 'atm_iv'])
    ext = pl.read_parquet(EXT_LEDGER, columns=['symbol', 'date', 'atm_iv', 'is_oos'])

    n_ext_oos = int(ext.filter(pl.col('is_oos') == True).height)
    ext_in = ext.filter(pl.col('is_oos') == False).drop('is_oos')
    combined = pl.concat([base, ext_in], how='vertical_relaxed')

    # Holdout enforcement (belt-and-suspenders on top of the is_oos filter above).
    combined = pre_cutoff_filter(combined)
    assert_no_holdout_leak(combined, context='vega_state calibration')

    # NaN-vs-null trap (traps.md "polars NaN family"): fill_nan(None) BEFORE drop_nulls.
    combined = combined.with_columns(pl.col('atm_iv').fill_nan(None)).drop_nulls(subset=['atm_iv'])
    combined = combined.filter(pl.col('atm_iv') > 0)

    vix = pl.read_parquet(VIX_SERIES, columns=['date', 'vix_close'])
    vix_map = dict(zip(vix['date'].to_list(), vix['vix_close'].to_list()))

    dates = combined['date'].to_list()
    atm_iv = combined['atm_iv'].to_list()
    vix_vals, keep_iv, keep_dates = [], [], []
    for d, iv in zip(dates, atm_iv):
        v = vix_map.get(d)
        if v is not None:
            vix_vals.append(float(v))
            keep_iv.append(float(iv))
            keep_dates.append(d)

    n = len(keep_iv)
    assert n >= 500, f"too few fit-eligible rows after VIX join: {n} (expected ~2500+)"

    X = np.column_stack([np.ones(n), np.array(vix_vals)])
    y = np.array(keep_iv)
    coef, _residuals, _rank, _sv = np.linalg.lstsq(X, y, rcond=None)
    a, b_vix = float(coef[0]), float(coef[1])

    y_hat = X @ coef
    resid = y - y_hat
    mae = float(np.mean(np.abs(resid)))
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mae_null = float(np.mean(np.abs(y - y.mean())))  # mean-only reference

    p1 = float(np.percentile(y, 1))
    p99 = float(np.percentile(y, 99))

    # COVID-scale VIX hard cap: derived from the actual MarketRegime series
    # (not hand-typed) -- 2020-02..2020-05 peak, +2.0 safety margin.
    covid = vix.filter((pl.col('date') >= '2020-02-01') & (pl.col('date') <= '2020-05-31'))
    covid_peak = float(covid['vix_close'].max())
    vix_hard_cap = round(covid_peak + 2.0, 2)

    def predict(vix_in):
        v = min(vix_in, vix_hard_cap)
        raw = a + b_vix * v
        return min(max(raw, p1), p99)

    anchors = {str(v): round(predict(v), 4) for v in (12, 15, 18, 20, 26, 30, 50, 82.69, 120)}

    calibration = {
        "form": "affine_vix: atm_iv_hat = COEF_A + COEF_B_VIX * min(vix, VIX_HARD_CAP), clamped [CLAMP_FLOOR, CLAMP_CAP]",
        "coef_a": round(a, 6),
        "coef_b_vix": round(b_vix, 6),
        "clamp_floor": round(p1, 6),
        "clamp_cap": round(p99, 6),
        "vix_hard_cap": vix_hard_cap,
        "vix_hard_cap_provenance": "COVID-era (2020-02..2020-05) MarketRegime.vix_close peak=%.2f, +2.0 margin" % covid_peak,
        "n_fit_rows": n,
        "r2": round(r2, 4),
        "mae": round(mae, 4),
        "mae_null_mean_only": round(mae_null, 4),
        "date_range": [str(min(keep_dates)), str(max(keep_dates))],
        "sources": [
            ".cache/iv_skew/iv_ledger.parquet (base, %d rows, all pre-cutoff)" % base.height,
            ".cache/iv_skew/iv_ledger_ext.parquet (is_oos=False rows only, %d of %d rows)" % (ext_in.height, ext.height),
        ],
        "excluded_oos_rows": n_ext_oos,
        "calibration_cutoff_date": CUTOFF.isoformat(),
        "extrapolation_anchors_vix_to_atm_iv_hat": anchors,
        "built": date.today().isoformat(),
        "consumer": "monte_carlo.py VEGA_STATE plug (env-gated, default OFF) -- ratio of response(vix_now)/response(vix_entry) fed into option_pricing.option_pnl_pct's vega_ratio, dead-hold CALL walk only",
        "fit_vix_range_observed": [round(float(np.min(vix_vals)), 2), round(float(np.max(vix_vals)), 2)],
        "caveats": [
            "R^2 is ~0 (see r2 field) -- on this narrow own-panel window (2025-02..2026-06, "
            "no 2020/2022-crash coverage) the affine VIX term explains essentially no variance "
            "over a mean-only model (mae vs mae_null_mean_only are within 0.001 of each other).",
            "The fitted VIX coefficient is SLIGHTLY NEGATIVE (b_vix<0): predicted atm_iv falls, "
            "not rises, with VIX across the observed range. This is the OPPOSITE sign from the "
            "'crash-vega-credit' hypothesis motivating this workstream, though not distinguishable "
            "from zero given r2~0 -- read as 'no detectable single-name VIX response in this "
            "sample', not as evidence of a real inverse effect.",
            "Practical consequence: at inference, response(vix_now)/response(vix_entry) will sit "
            "very close to 1.0 (near-inert) for any two VIX values inside the observed fit range "
            "[see fit_vix_range_observed], and will apply a mild HAIRCUT (not credit) if VIX moves "
            "far outside it (e.g. toward vix_hard_cap) -- because the fitted slope is negative. "
            "The 2020_crash-inclusive A/B (gameplan P1.4 queue task) is the correct place to read "
            "whether this measurably moves WorstDD/collapse; do not assume a crash-credit effect "
            "from this calibration alone.",
            "Root cause is almost certainly a DATA gap (own real-contract IV panel only spans "
            "~1.4y, 2025-02 onward, never covering a real broad-market vol shock) rather than a "
            "form problem -- consistent with the data-acquisition.md gap and the coupled "
            "iv_premium_model/gamma_iv_phaseb finding that our own-panel history is too short/calm "
            "to pin a crash-regime response; a wider-history panel (Polygon, or a future L3 buy) "
            "would be the natural re-fit target if this mechanism is judged worth pursuing further.",
        ],
    }

    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(calibration, f, indent=2)

    print("[vega_state] fit n=%d r2=%.4f mae=%.4f (null mae=%.4f)" % (n, r2, mae, mae_null))
    print("[vega_state] a=%.6f b_vix=%.6f clamp=[%.4f,%.4f] vix_hard_cap=%.2f" % (a, b_vix, p1, p99, vix_hard_cap))
    print("[vega_state] excluded %d is_oos rows (holdout)" % n_ext_oos)
    print("[vega_state] wrote %s" % OUT_JSON)
    for v, pv in anchors.items():
        print("    VIX=%-8s -> atm_iv_hat=%s" % (v, pv))


if __name__ == '__main__':
    main()
