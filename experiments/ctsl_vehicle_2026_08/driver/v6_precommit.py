#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
v6_precommit.py -- V6: the in-sample band that the 2026-12-15 OOS re-grade
will be scored against. (PREREG V6, lock 3e2adc9f + AMENDMENT-1 1acca0aa)

HOLDOUT DISCIPLINE (the whole point of this stage): every query in this file
is hard-filtered to date <= strategy_config.CALIBRATION_CUTOFF_DATE. The
post-cutoff rows physically exist in the DB today (2026-08-12), so the filter
is asserted, not assumed -- reading them once would burn the only virgin
window this mechanism will ever get.

Predictand = the CT_PROMOTE population itself, NOT the ~138 trades the
portfolio happens to fund. Rationale: the funded subset is MaxPos/gross-
constrained (N ~ 10^2, CI +/- 8pp -- untestable in six months), while the
tagged population is N ~ 10^4 and is exactly what December can re-measure
without knowing anything about portfolio state.

  CT-promoted (call) := active-version score rows with overall >= 70 AND
                        trend <= CT_CALL_TREND_MAX, i.e. ct_tag()'s call
                        branch verbatim (monte_carlo.py:1546).
  Contrast          := overall >= 70 AND trend > CT_CALL_TREND_MAX.
  Outcome           := barrier_outcomes (DuckDB read mirror), side='high'.

Read-only everywhere. Writes only into this campaign's out/ and logs/.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_THIS_DIR)
_REPO_ROOT = os.path.dirname(os.path.dirname(_EXP_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
DUCK = os.path.join(_REPO_ROOT, '.cache', 'barrier_outcomes.duckdb')

# barrier_outcomes stores EIGHT horizons per barrier_set (w_days in
# {1,3,5,7,15,30,60,90}), each a full 862k-row slice. Keying only on
# (symbol, date) silently keeps whichever horizon the cursor emitted last and
# makes every barrier set look identical -- caught on the first run, when
# 30dte_opt and 30dte_apex returned the same 24 wins despite different m_low.
# Each set is therefore pinned to the horizon the vehicle actually holds:
# 30-DTE base book -> w_days=30; the router-15 sleeve -> w_days=15.
BARRIER_SETS_READ = [('30dte_opt', 30), ('30dte_apex', 30), ('15dte_opt', 15)]
PRIMARY_SET = ('30dte_opt', 30)
IN_SAMPLE_START = '2022-01-01'   # the 22-now decision window's own start


def _tee(msg, log_path):
    print(msg, flush=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def wilson(k, n, z=1.96):
    """Wilson score interval -- correct at the tails where normal-approx isn't."""
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def run(log_path, out_csv, cutoff, trend_max, version_id):
    import duckdb
    from database.trader_database import DB

    _tee(f"\n{'=' * 100}", log_path)
    _tee("V6 -- in-sample band for the CT_PROMOTE predictand (December pre-commitment)", log_path)
    _tee(f"[HOLDOUT] hard cutoff = {cutoff} (strategy_config.CALIBRATION_CUTOFF_DATE). "
        f"Nothing after this date is read by this script.", log_path)
    _tee(f"[POPULATION] active version_id={version_id}, calls overall>=70, "
        f"CT := trend <= {trend_max} (== monte_carlo.CT_CALL_TREND_MAX)", log_path)

    DB.connect(reuse_if_open=True)
    cur = DB.execute_sql(
        "SELECT symbol, date, overall, trend FROM scores "
        "WHERE version_id=%s AND overall >= 70 AND trend IS NOT NULL "
        "AND date BETWEEN %s AND %s", (version_id, IN_SAMPLE_START, cutoff))
    rows = cur.fetchall()
    assert rows, "[STOP] no in-sample score rows"
    max_date = max(str(r[1]) for r in rows)
    assert max_date <= cutoff, f"[STOP] holdout breach: max score date {max_date} > {cutoff}"
    _tee(f"[LOAD] {len(rows):,} call signals in [{IN_SAMPLE_START}..{cutoff}] "
        f"(max date seen = {max_date}) -- holdout filter VERIFIED", log_path)

    ct = {(str(r[0]), str(r[1])) for r in rows if int(r[3]) <= trend_max}
    non_ct = {(str(r[0]), str(r[1])) for r in rows} - ct
    _tee(f"[SPLIT] CT-promoted={len(ct):,}  contrast(non-CT >=70)={len(non_ct):,}  "
        f"CT share={100*len(ct)/len(rows):.1f}%", log_path)

    con = duckdb.connect(DUCK, read_only=True)
    out_rows = []
    for bset, wd in BARRIER_SETS_READ:
        df = con.execute(
            "SELECT symbol, CAST(date AS VARCHAR) AS date, result, exit_return "
            "FROM barrier_outcomes WHERE barrier_set=? AND w_days=? AND side='high' "
            "AND date BETWEEN ? AND ? AND result IS NOT NULL",
            [bset, wd, IN_SAMPLE_START, cutoff]).df()
        idx = {(r.symbol, r.date): (int(r.result), float(r.exit_return)) for r in df.itertuples()}
        assert len(idx) == len(df), "[STOP] duplicate (symbol,date) after pinning w_days"
        _tee(f"\n-- barrier_set={bset} w_days={wd} -- outcomes available in-sample: {len(idx):,}", log_path)
        for label, pop in (('CT_promoted', ct), ('contrast_nonCT', non_ct)):
            hits = [idx[k] for k in pop if k in idx]
            n = len(hits)
            if n == 0:
                _tee(f"   {label}: no joinable outcomes", log_path)
                continue
            k = sum(1 for r, _ in hits if r == 1)
            wr = k / n
            lo, hi = wilson(k, n)
            ev = sum(x for _, x in hits) / n
            cov = n / len(pop)
            _tee(f"   {label:<15} N={n:>7,} (cov {100*cov:5.1f}%)  WR={100*wr:6.2f}%  "
                f"95%CI=[{100*lo:.2f}, {100*hi:.2f}]  mean exit_return={ev:+.3f}%", log_path)
            out_rows.append({'barrier_set': bset, 'w_days': wd, 'population': label, 'n': n,
                             'coverage_pct': round(100 * cov, 3), 'wins': k,
                             'wr_pct': round(100 * wr, 4),
                             'wr_ci_lo_pct': round(100 * lo, 4), 'wr_ci_hi_pct': round(100 * hi, 4),
                             'mean_exit_return_pct': round(ev, 5),
                             'in_sample_start': IN_SAMPLE_START, 'cutoff': cutoff,
                             'version_id': version_id, 'trend_max': trend_max})

    if out_rows:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(out_csv, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
        _tee(f"\n[WRITE] {out_csv} ({len(out_rows)} rows)", log_path)

    prim = {r['population']: r for r in out_rows
            if (r['barrier_set'], r['w_days']) == PRIMARY_SET}
    if 'CT_promoted' in prim:
        p = prim['CT_promoted']
        _tee(f"\n[BAND] PRIMARY ({PRIMARY_SET}) CT-promoted in-sample WR = {p['wr_pct']:.2f}% "
            f"[{p['wr_ci_lo_pct']:.2f}, {p['wr_ci_hi_pct']:.2f}] on N={p['n']:,}", log_path)
        if 'contrast_nonCT' in prim:
            c = prim['contrast_nonCT']
            _tee(f"[BAND] contrast non-CT WR = {c['wr_pct']:.2f}% "
                f"[{c['wr_ci_lo_pct']:.2f}, {c['wr_ci_hi_pct']:.2f}] on N={c['n']:,}  "
                f"-> in-sample CT lift = {p['wr_pct']-c['wr_pct']:+.2f}pp", log_path)
    return 0


def selftest() -> int:
    print("=== v6_precommit.py OFFLINE SELF-TESTS ===")
    lo, hi = wilson(50, 100)
    assert 0.399 < lo < 0.41 and 0.59 < hi < 0.601, (lo, hi)
    assert wilson(0, 0) == (None, None)
    lo0, hi0 = wilson(0, 30)
    assert lo0 == 0.0 or lo0 < 1e-9, lo0
    assert 0.0 < hi0 < 0.2, hi0
    print("  [1] Wilson interval: 50/100 -> ~[40.4, 59.6]; degenerate 0/n stays in [0,1] OK")

    import strategy_config as sc
    assert sc.CALIBRATION_CUTOFF_DATE == '2026-06-15', sc.CALIBRATION_CUTOFF_DATE
    print(f"  [2] holdout lock read LIVE from strategy_config = {sc.CALIBRATION_CUTOFF_DATE} OK")

    assert os.path.isfile(DUCK), DUCK
    assert PRIMARY_SET in BARRIER_SETS_READ
    assert all(isinstance(x, tuple) and len(x) == 2 for x in BARRIER_SETS_READ)
    print("  [3] DuckDB barrier mirror present; primary barrier set is in the read list OK")
    print("=== SELFTEST PASS ===")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--selftest', action='store_true')
    a = p.parse_args()
    if a.selftest:
        return selftest()
    import strategy_config as sc
    from database.models.core import AlgorithmVersion
    cutoff = sc.CALIBRATION_CUTOFF_DATE
    assert cutoff, "[STOP] CALIBRATION_CUTOFF_DATE is None -- holdout not locked, refusing to run"
    import monte_carlo as mc
    return run(os.path.join(LOG_DIR, 'v6.log'), os.path.join(OUT_DIR, 'ctsl_v6_band.csv'),
               cutoff, mc.CT_CALL_TREND_MAX,
               AlgorithmVersion.get_active_scores_version().id)


if __name__ == '__main__':
    sys.exit(main())
