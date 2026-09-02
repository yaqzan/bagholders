"""Dump the honest v69 EARN_BOOST lift table (CALL side) in the production
format (side|cohort|bucket -> [lift_pp, N]), from the holdout-locked ledger.

Writes two call-side tables (generic-WR15 and option-WR15 derived) and a
ship-candidate table that takes honest CALL (low) cells + carries the existing
v34 PUT (high) cells unchanged (puts need their own honest ledger before ship).
"""
from __future__ import annotations
import io, sys, os, json
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import polars as pl

PQ = ROOT / '.cache' / 'earnboost_honest' / 'call_ledger_v69_holdout.parquet'
V34 = ROOT / 'experiments' / 'v34_calibration' / 'lift_table_v34.json'
OUT = ROOT / 'experiments' / 'earnboost_honest'
BUCKETS = ['95+', '90-94', '85-89', '80-84', '75-79', '70-74']
COHORTS = ['pre1', 'pre3', 'pre7']
MIN_N = 10


def bucket(pb):
    if pb >= 95: return '95+'
    if pb >= 90: return '90-94'
    if pb >= 85: return '85-89'
    if pb >= 80: return '80-84'
    if pb >= 75: return '75-79'
    return '70-74'


def build(df, col):
    base = df.filter(pl.col('cohort') == 'none')
    bwr = {}
    for b in BUCKETS:
        s = base.filter((pl.col('pb_bucket') == b) & pl.col(col).is_not_null())
        bwr[b] = (100.0 * s[col].sum() / len(s)) if len(s) else None
    out = {}
    for c in COHORTS:
        for b in BUCKETS:
            s = df.filter((pl.col('cohort') == c) & (pl.col('pb_bucket') == b) & pl.col(col).is_not_null())
            n = len(s)
            if n == 0 or bwr[b] is None:
                continue
            lift = 100.0 * s[col].sum() / n - bwr[b]
            out[f"low|{c}|{b}"] = [round(lift, 2), n]
    return out


def main():
    df = pl.read_parquet(PQ).with_columns(
        pl.col('pre_boost').map_elements(bucket, return_dtype=pl.Utf8).alias('pb_bucket'))
    gen = build(df, 'w15g')
    opt = build(df, 'w15o')
    json.dump(gen, open(OUT / 'lift_table_v69_calls_generic.json', 'w'), indent=2)
    json.dump(opt, open(OUT / 'lift_table_v69_calls_opt.json', 'w'), indent=2)

    # ship-candidate: honest CALL cells + v34 PUT cells unchanged
    v34 = json.load(open(V34)) if V34.exists() else {}
    ship = {k: v for k, v in v34.items() if k.startswith('high|')}   # keep puts
    ship.update(gen)                                                  # honest calls
    json.dump(ship, open(OUT / 'lift_table_v69_ship_candidate.json', 'w'), indent=2)

    print("Honest CALL lift cells with MIN_N>=10 and lift>0 (these are the ones that BOOST):")
    print(f"  {'cell':<18} | {'generic lift':>12} | {'opt lift':>10}")
    for k in sorted(gen, key=lambda x: (x.split('|')[2], x.split('|')[1])):
        gl, gn = gen[k]
        ol, on = opt.get(k, [None, 0])
        flag = '  <- BOOSTS' if (gl > 0 and gn >= MIN_N) else ''
        print(f"  {k:<18} | {gl:+6.1f}pp n={gn:<4} | {(f'{ol:+5.1f}' if ol is not None else '-'):>8}{flag}")
    print(f"\n[wrote] lift_table_v69_calls_generic.json / _opt.json / _ship_candidate.json in {OUT}")
    print("Note: ship-candidate keeps v34 PUT cells unchanged — puts need their own honest ledger before any ship.")


if __name__ == '__main__':
    main()
