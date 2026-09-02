#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
v4_capacity.py -- V4: can the owner actually TRADE this vehicle at $25k/$50k/$100k?
(experiments/ctsl_vehicle_2026_08/PREREG.md V4, lock 3e2adc9f)

Integer-contract + clip-vs-volume modelling of the vehicle's ACTUAL historical
names, using REAL Polygon contract economics -- not a premium model:

  entry_premium, entry_volume  <- B:/polygon_derived/ledger_v2/ledger.parquet
                                  (the real-contract ledger; per-share premium
                                  and the entry day's contract volume)
  realized allocation fraction <- this campaign's own V1 tape, premium_cost /
                                  entry_value per trade. Using the REALIZED
                                  fraction rather than the nominal ultra 0.20
                                  matters: every dampener scale (RXDD, SVR,
                                  MWDD, TVDD, BDIV, saturation, DD-soft-band)
                                  multiplies that 0.20 down before the fill.

Two independent ways a trade dies at small size, reported separately because
they have opposite fixes:
  AFFORDABILITY -- floor(alloc$ / (entry_premium * 100)) == 0. One contract
                   costs more than the whole slot. Fix = a bigger book.
  CAPACITY      -- contracts_wanted > CLIP_CAP_FRAC * entry_volume. The market
                   cannot absorb the clip. Fix = nothing; it is a hard ceiling
                   (this is the G3(b) convention from liquidity_floor_2026_08).

Read-only. Writes only into this campaign's out/ and logs/.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_THIS_DIR)

OUT_DIR = os.path.join(_EXP_DIR, 'out')
LOG_DIR = os.path.join(_EXP_DIR, 'logs')
TAPES_DIR = os.path.join(OUT_DIR, 'tapes')

LEDGER = 'B:/polygon_derived/ledger_v2/ledger.parquet'
BOOK_SIZES = [25_000, 50_000, 100_000]
CLIP_CAP_FRAC = 0.25          # G3(b): a clip may not exceed 25% of that contract-day's volume
CONTRACT_MULTIPLIER = 100
DECISION_WINDOWS = ['22-now', '5y']


def _tee(msg, log_path):
    print(msg, flush=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def contracts_for(alloc_dollars, entry_premium):
    """Integer contracts affordable at a real per-share premium. 0 = unaffordable."""
    if entry_premium is None or entry_premium <= 0:
        return None
    return int(math.floor(alloc_dollars / (entry_premium * CONTRACT_MULTIPLIER)))


def clip_feasible(contracts, entry_volume, cap_frac=CLIP_CAP_FRAC):
    """True if the clip fits under cap_frac of that contract-day's volume."""
    if entry_volume is None or entry_volume <= 0:
        return None
    return contracts <= cap_frac * entry_volume


def run(windows, log_path, out_csv, job='v1', arm='vehicle', lens='calibrated'):
    import polars as pl

    led = (pl.read_parquet(LEDGER)
             .with_columns(pl.col('entry_date').cast(pl.Utf8))
             .rename({'symbol': 'sym_id'})
             .select(['sym_id', 'entry_date', 'entry_premium', 'entry_volume',
                      'median_path_volume', 'dte_actual', 'liquid_entry', 'status']))
    _tee(f"\n{'=' * 100}", log_path)
    _tee("V4 -- capacity of the CTSL vehicle at owner scale (real-contract ledger)", log_path)
    _tee(f"[INPUT] ledger rows={len(led):,}  clip cap = {CLIP_CAP_FRAC:.0%} of entry-day contract volume  "
        f"book sizes = {BOOK_SIZES}", log_path)

    rows_out = []
    for w in windows:
        tape = os.path.join(TAPES_DIR, f'{job}_{arm}_{w}_full_{lens}.parquet')
        if not os.path.isfile(tape):
            _tee(f"[SKIP] window={w}: tape not found ({tape})", log_path)
            continue
        t = pl.read_parquet(tape)
        # One row per DISTINCT signal: the 500 paths trade nearly the same names,
        # so path rows would 500x-count every trade. Realized allocation fraction
        # is averaged across the paths that took it.
        per_trade = (t.with_columns((pl.col('premium_cost') / pl.col('entry_value')).alias('frac'))
                      .group_by(['sym_id', 'entry_date'])
                      .agg([pl.col('frac').mean().alias('alloc_frac'),
                            pl.len().alias('n_path_rows')]))
        j = per_trade.join(led, on=['sym_id', 'entry_date'], how='left')
        n_all = len(j)
        n_join = int(j['entry_premium'].is_not_null().sum())
        _tee(f"\n-- window={w} -- distinct vehicle trades={n_all}  joined to real contracts="
            f"{n_join} ({100*n_join/max(n_all,1):.1f}%)", log_path)
        if n_join == 0:
            continue
        js = j.filter(pl.col('entry_premium').is_not_null())
        prem = js['entry_premium'].to_list()
        vol = js['entry_volume'].to_list()
        frac = js['alloc_frac'].to_list()
        _tee(f"   realized alloc fraction: median={sorted(frac)[len(frac)//2]:.4f} "
            f"(nominal ultra tier = 0.2000, i.e. dampeners cut it to "
            f"{100*sorted(frac)[len(frac)//2]/0.20:.0f}% of nominal)", log_path)
        _tee(f"   real per-contract cost: median=${sorted(prem)[len(prem)//2]*CONTRACT_MULTIPLIER:,.0f}  "
            f"min=${min(prem)*CONTRACT_MULTIPLIER:,.0f}  max=${max(prem)*CONTRACT_MULTIPLIER:,.0f}", log_path)

        for book in BOOK_SIZES:
            n_unaff = n_capped = n_ok = 0
            intended = realized = 0.0
            for p, v, f in zip(prem, vol, frac):
                alloc = f * book
                intended += alloc
                c = contracts_for(alloc, p)
                if c is None:
                    continue
                if c == 0:
                    n_unaff += 1
                    continue
                feas = clip_feasible(c, v)
                if feas is False:
                    n_capped += 1
                    c_eff = int(math.floor(CLIP_CAP_FRAC * v))
                    realized += c_eff * p * CONTRACT_MULTIPLIER
                else:
                    n_ok += 1
                    realized += c * p * CONTRACT_MULTIPLIER
            n = len(prem)
            span_years = {'22-now': 4.31, '5y': 5.29}.get(w)
            eff_per_yr = (n_ok / span_years) if span_years else None
            _tee(f"   ${book:>7,}: tradable={n_ok:>3} ({100*n_ok/n:5.1f}%)  "
                f"unaffordable={n_unaff:>3} ({100*n_unaff/n:5.1f}%)  "
                f"clip-capped={n_capped:>3} ({100*n_capped/n:5.1f}%)  "
                f"deployment={100*realized/intended:5.1f}% of intended  "
                f"effective trades/yr={eff_per_yr:.1f}" if eff_per_yr else '', log_path)
            rows_out.append({'window': w, 'book': book, 'n_joined_trades': n,
                             'n_tradable': n_ok, 'pct_tradable': round(100 * n_ok / n, 2),
                             'n_unaffordable': n_unaff, 'pct_unaffordable': round(100 * n_unaff / n, 2),
                             'n_clip_capped': n_capped, 'pct_clip_capped': round(100 * n_capped / n, 2),
                             'intended_dollars': round(intended, 2), 'realized_dollars': round(realized, 2),
                             'deployment_pct': round(100 * realized / intended, 2) if intended else None,
                             'effective_trades_per_year': round(eff_per_yr, 2) if eff_per_yr else None,
                             'clip_cap_frac': CLIP_CAP_FRAC,
                             'n_distinct_trades_all': n_all,
                             'ledger_join_pct': round(100 * n_join / max(n_all, 1), 2)})

    if rows_out:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(out_csv, 'w', newline='', encoding='utf-8') as f:
            wtr = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            wtr.writeheader()
            wtr.writerows(rows_out)
        _tee(f"\n[WRITE] {out_csv} ({len(rows_out)} rows)", log_path)
    return 0


def selftest() -> int:
    print("=== v4_capacity.py OFFLINE SELF-TESTS ===")
    # $2,000 slot, $4.50/share premium -> $450/contract -> 4 contracts, $1,800 used.
    assert contracts_for(2000, 4.50) == 4
    assert contracts_for(400, 4.50) == 0, "one contract costs $450 > $400 slot"
    assert contracts_for(2000, None) is None and contracts_for(2000, 0) is None
    print("  [1] integer-contract affordability incl. the unaffordable (0-contract) case OK")

    assert clip_feasible(4, 100) is True          # 4 <= 25
    assert clip_feasible(30, 100) is False        # 30 > 25
    assert clip_feasible(25, 100) is True         # exactly at the cap
    assert clip_feasible(1, 0) is None and clip_feasible(1, None) is None
    print("  [2] 25%-of-day-volume clip cap, boundary inclusive, unknown-volume -> None OK")

    assert BOOK_SIZES == [25_000, 50_000, 100_000] and CONTRACT_MULTIPLIER == 100
    assert os.path.isfile(LEDGER), LEDGER
    print("  [3] PREREG book sizes; real-contract ledger present OK")
    print("=== SELFTEST PASS ===")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--selftest', action='store_true')
    p.add_argument('--windows', default=','.join(DECISION_WINDOWS))
    a = p.parse_args()
    if a.selftest:
        return selftest()
    return run(a.windows.split(','), os.path.join(LOG_DIR, 'v4.log'),
               os.path.join(OUT_DIR, 'ctsl_v4_capacity.csv'))


if __name__ == '__main__':
    sys.exit(main())
