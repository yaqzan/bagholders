"""
P2.A step 4 -- build the arm-B survivor-only universe allow-list.

Arm B of the survivorship decomposition (see FINDINGS.md) must run the CLEAN
post-rebuild substrate restricted to the universe arm A actually ran on. That
universe is reconstructed from two frozen sources, both fixed at rebuild time
(2026-07-29):

  1. `.cache/sharadar/backup_price_history_pre_rebuild.parquet` -- the full
     pre-rebuild price_history snapshot. Its 801 distinct symbols are every
     MAPPED symbol that had bars before the rebuild (safety copy also on
     B:\trader_rebuild_safety_2026-07-29, sha256-verified).
  2. The 10 symbols the rebuild never touched (no Sharadar mapping): their
     pre-rebuild bars are still the live bars. Listed inline below -- there is
     no machine-readable frozen artifact for them, so they are pinned here.

801 + 10 = 811 = every symbol with price bars pre-rebuild (the pre-rebuild
table had 811 symbols with bars; scores are a subset of symbols-with-bars, so
this is a superset of arm A's actual signal pool -- symbols in the list that
never had scores simply contribute no signals, which is exactly arm A's
behaviour).

Deliberately DB-free so the list is reproducible from the frozen parquet alone.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKUP_PARQUET = os.path.join(ROOT, '.cache', 'sharadar',
                              'backup_price_history_pre_rebuild.parquet')
OUT = os.path.join(HERE, 'survivor_universe_811.txt')

# The 10 symbols with pre-rebuild bars but no Sharadar mapping -- untouched by
# the rebuild (still on their original yfinance history + conventions).
UNMAPPED_10 = ['COL', 'HAR', 'FOX', 'FOXA', 'CBRS', 'RAM',
               'ENA.V', 'HPS-A.TO', 'PINV.TO', 'VNP.TO']


def main():
    import polars as pl
    df = pl.read_parquet(BACKUP_PARQUET, columns=['symbol'])
    mapped = sorted(set(df['symbol'].to_list()))
    assert len(mapped) == 801, f"expected 801 mapped symbols, got {len(mapped)}"
    overlap = set(mapped) & set(UNMAPPED_10)
    assert not overlap, f"unmapped list overlaps parquet: {overlap}"
    universe = sorted(set(mapped) | set(UNMAPPED_10))
    assert len(universe) == 811, f"expected 811 total, got {len(universe)}"

    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# Arm-B survivor-only universe (P2.A step 4 survivorship decomposition).\n')
        f.write('# 801 distinct symbols from .cache/sharadar/backup_price_history_pre_rebuild.parquet\n')
        f.write('# + 10 unmapped symbols never touched by the 2026-07-29 rebuild.\n')
        f.write('# Built by build_universe.py -- regenerate there, do not hand-edit.\n')
        for s in universe:
            f.write(s + '\n')
    print(f"wrote {OUT}: {len(universe)} symbols "
          f"({len(mapped)} from backup parquet + {len(UNMAPPED_10)} unmapped)")


if __name__ == '__main__':
    main()
