"""Build the v70 EARN_BOOST ship table: honest, both-barrier-gated (calls+puts).

Cell included iff generic-lift>0 AND opt-lift>0 AND N>=10 (tradable-positive,
sufficient sample). Magnitude = GENERIC WR15 lift (the canonical metric the
mechanism's strength derivation expects). Format matches v34: side|cohort|bucket
-> [lift_pp, N].  side 'low'=calls (call ledger), 'high'=puts (put ledger).
"""
from __future__ import annotations
import io, sys, os, json
from pathlib import Path
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parents[2]
import polars as pl

OUT = ROOT / 'experiments' / 'earnboost_honest'
CALL_PQ = ROOT / '.cache' / 'earnboost_honest' / 'call_ledger_v69_holdout.parquet'
PUT_PQ = ROOT / '.cache' / 'earnboost_honest' / 'put_ledger_v69_holdout.parquet'
CALL_B = ['95+', '90-94', '85-89', '80-84', '75-79', '70-74']
PUT_B = ['0-5', '6-10', '11-15', '16-20', '21-25']
COH = ['pre1', 'pre3', 'pre7']
MIN_N = 10


def call_bucket(pb):
    if pb >= 95: return '95+'
    if pb >= 90: return '90-94'
    if pb >= 85: return '85-89'
    if pb >= 80: return '80-84'
    if pb >= 75: return '75-79'
    return '70-74'


def put_bucket(pb):
    if pb <= 5: return '0-5'
    if pb <= 10: return '6-10'
    if pb <= 15: return '11-15'
    if pb <= 20: return '16-20'
    return '21-25'


def cells(df, side, buckets, bucket_fn):
    df = df.with_columns(pl.col('pre_boost').map_elements(bucket_fn, return_dtype=pl.Utf8).alias('b'))
    base = df.filter(pl.col('cohort') == 'none')
    bg, bo = {}, {}
    for b in buckets:
        s = base.filter(pl.col('b') == b)
        sg = s.filter(pl.col('w15g').is_not_null()); so = s.filter(pl.col('w15o').is_not_null())
        bg[b] = (100.0 * sg['w15g'].sum() / len(sg)) if len(sg) else None
        bo[b] = (100.0 * so['w15o'].sum() / len(so)) if len(so) else None
    out = {}; survivors = []
    for c in COH:
        for b in buckets:
            s = df.filter((pl.col('cohort') == c) & (pl.col('b') == b))
            sg = s.filter(pl.col('w15g').is_not_null()); so = s.filter(pl.col('w15o').is_not_null())
            n = len(sg)
            if n < MIN_N or bg[b] is None or bo[b] is None or len(so) == 0:
                continue
            gl = 100.0 * sg['w15g'].sum() / n - bg[b]
            ol = 100.0 * so['w15o'].sum() / len(so) - bo[b]
            if gl > 0 and ol > 0:                     # both-barrier gate
                out[f"{side}|{c}|{b}"] = [round(gl, 2), n]
                survivors.append((f"{side}|{c}|{b}", round(gl, 2), round(ol, 2), n))
    return out, survivors


def main():
    cdf = pl.read_parquet(CALL_PQ)
    pdf = pl.read_parquet(PUT_PQ)
    call_t, call_s = cells(cdf, 'low', CALL_B, call_bucket)
    put_t, put_s = cells(pdf, 'high', PUT_B, put_bucket)
    table = {**call_t, **put_t}
    json.dump(table, open(OUT / 'lift_table_v70.json', 'w'), indent=2)

    print("CALL cells that BOOST (both-barrier-gated, generic magnitude / opt lift / N):")
    for k, gl, ol, n in call_s:
        print(f"   {k:<16} | gen +{gl:5.2f} | opt +{ol:5.2f} | n={n}")
    print("\nPUT cells that BOOST:")
    if put_s:
        for k, gl, ol, n in put_s:
            print(f"   {k:<16} | gen +{gl:5.2f} | opt +{ol:5.2f} | n={n}")
    else:
        print("   (none clear both-barrier + N>=10 — put boost effectively disabled honestly)")
    print(f"\n[wrote] {OUT/'lift_table_v70.json'}  ({len(table)} active cells: {len(call_t)} call, {len(put_t)} put)")


if __name__ == '__main__':
    main()
