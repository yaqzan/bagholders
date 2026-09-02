"""
Phase 1c: Build universal intraday volume completion profile.

For each (symbol, day), compute the cumulative-volume fraction at each
30-min mark (10:00, 10:30, ..., 16:00). Average across all stock-days to
get a universal profile.

Output: volume_profile.json with `{minutes_since_open: completion_fraction}`.

Also computes per-stock profiles for sanity-check (do industrials match
the universal? do high-vol names like NET differ?).
"""
import json
from datetime import time
from pathlib import Path
import pandas as pd

CACHE_DIR = Path('.cache/intraday_minute')
OUT = Path('experiments/intraday_confidence/volume_profile.json')

SNAPSHOT_MINUTES = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360, 390]


def market_open_offset(ts: pd.Timestamp) -> int:
    """Minutes since 9:30 ET. Negative for pre-9:30, capped at 390 (16:00)."""
    return (ts.hour - 9) * 60 + (ts.minute - 30)


def cumulative_at_offsets(day_df: pd.DataFrame) -> dict:
    """For one trading day's 1-min bars, cumulative volume at each snapshot offset."""
    out = {}
    full_vol = day_df['volume'].sum()
    if full_vol <= 0:
        return out
    # offset of each bar
    day_df = day_df.copy()
    day_df['offset'] = day_df['ts'].apply(market_open_offset)
    # cumulative
    day_df = day_df.sort_values('ts')
    day_df['cumvol'] = day_df['volume'].cumsum()
    for snap in SNAPSHOT_MINUTES:
        # Take cumulative volume at the LAST bar with offset < snap
        # (snap=0 means start-of-day = 0; snap=390 = full day = 1.0)
        if snap == 0:
            out[snap] = 0.0
            continue
        eligible = day_df[day_df['offset'] < snap]
        if len(eligible) == 0:
            out[snap] = 0.0
        else:
            out[snap] = eligible.iloc[-1]['cumvol'] / full_vol
    return out


def main():
    paths = sorted(CACHE_DIR.glob('*_1m.parquet'))
    print(f'Loading {len(paths)} symbol files...')

    # Per-stockday results
    rows = []
    for p in paths:
        sym = p.stem.replace('_1m', '')
        df = pd.read_parquet(p)
        for day, day_df in df.groupby(df['ts'].dt.date):
            cum = cumulative_at_offsets(day_df)
            for snap, frac in cum.items():
                rows.append({'symbol': sym, 'date': day, 'offset': snap, 'frac': frac})

    panel = pd.DataFrame(rows)
    print(f'\nStock-days: {panel.groupby(["symbol", "date"]).ngroups}')
    print(f'Snapshots:  {len(panel)}')

    # Universal profile: mean across all stock-days
    universal = panel.groupby('offset')['frac'].mean()
    universal_dict = {int(k): float(v) for k, v in universal.items()}

    print('\nUniversal volume completion profile:')
    print(f'{"offset_min":>10} {"clock_ET":>9} {"frac_done":>10}')
    for off, frac in sorted(universal_dict.items()):
        clock = f'{9 + (30 + off)//60}:{(30 + off) % 60:02d}'
        print(f'{off:>10} {clock:>9}    {frac:.3f}')

    # Per-stock summary (do COHR's industrial cluster differ?)
    per_stock = panel.groupby(['symbol', 'offset'])['frac'].mean().unstack()
    print('\nPer-stock profile (sample at 60/180/300 min):')
    cols_to_show = [60, 180, 300, 360]
    print(per_stock[cols_to_show].round(3).to_string())

    # Save
    out_data = {
        'universal': universal_dict,
        'per_stock': {sym: {int(o): float(f) for o, f in row.items()}
                      for sym, row in per_stock.iterrows()},
        'n_stock_days': panel.groupby(['symbol', 'date']).ngroups,
    }
    OUT.write_text(json.dumps(out_data, indent=2))
    print(f'\n[OK] saved {OUT}')


if __name__ == '__main__':
    main()
