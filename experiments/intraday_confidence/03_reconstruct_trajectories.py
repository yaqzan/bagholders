"""
Phase 1d: At each 30-min snapshot, reconstruct partial-day OHLCV from 1-min
data, run the volume amplifier classification, and record the resulting
signal/magnitude trajectory.

Output: trajectories.parquet with one row per (symbol, day, snapshot_min):
  symbol, date, snap_min, partial_open, partial_high, partial_low,
  partial_close, partial_volume, projected_full_volume, ratio_50,
  signal_type, direction, raw_magnitude, multiplier, intraday_confidence

The 16:00 row is the close-of-day "ground truth" for that (symbol, day).

This pins to v39 prior-day baselines (50d avg volume) loaded from PriceHistory.
No DB writes.
"""
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault('ALGORITHM_VERSION_PIN', '200f33a')

import pandas as pd

# Import scoring helpers — these are pure functions
sys.path.insert(0, '.')
from volume_amplifier import (
    _classify_signal, _raw_magnitude, _to_multiplier, _intraday_confidence,
    project_full_day_volume, AVG_WINDOW_LONG, AVG_WINDOW_SHORT, MIN_HISTORY_DAYS,
    RECENT_TREND_DAYS, ABSORPTION_STRONG_THRESHOLD, ABSORPTION_STRONG_BOOST,
)
from database.models.technical import PriceHistory

CACHE_DIR = Path('.cache/intraday_minute')
OUT_PATH = Path('experiments/intraday_confidence/trajectories.parquet')

SNAPSHOT_OFFSETS = [30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360, 390]


def load_prior_history(symbol: str, on_or_before: date, n: int = AVG_WINDOW_LONG):
    """Load prior PriceHistory rows. Excludes the target date itself."""
    rows = list(PriceHistory.select()
                .where((PriceHistory.symbol == symbol) & (PriceHistory.date < on_or_before))
                .order_by(PriceHistory.date.desc()).limit(n))
    return rows


def reconstruct_partial(day_bars: pd.DataFrame, snap_min: int) -> dict:
    """Reconstruct partial-day OHLCV from 1-min bars up to (but not including) snap_min.

    snap_min=30 → use bars from 09:30 inclusive through 09:59:59 (offset < 30).
    snap_min=390 → full day (16:00 close).
    """
    day_bars = day_bars.copy()
    day_bars['offset'] = day_bars['ts'].apply(lambda t: (t.hour - 9) * 60 + (t.minute - 30))
    eligible = day_bars[day_bars['offset'] < snap_min].sort_values('ts')
    if len(eligible) == 0:
        return None
    return {
        'open': float(eligible.iloc[0]['open']),
        'high': float(eligible['high'].max()),
        'low': float(eligible['low'].min()),
        'close': float(eligible.iloc[-1]['close']),
        'volume': int(eligible['volume'].sum()),
        'last_ts': eligible.iloc[-1]['ts'],
    }


def classify_at_snapshot(symbol: str, day: date, partial: dict, snap_min: int,
                         prior_history) -> dict:
    """Run today-side classification (no decay) at one snapshot."""
    if partial is None:
        return None
    if len(prior_history) < MIN_HISTORY_DAYS:
        return None

    vols = [int(h.volume) for h in prior_history]
    short = vols[:AVG_WINDOW_SHORT] if len(vols) >= AVG_WINDOW_SHORT else vols
    avg_50 = sum(short) / len(short)
    avg_200 = sum(vols) / len(vols)
    if avg_50 == 0 or avg_200 == 0:
        return None

    # Build a fake "pulled_at" — for snapshots, pulled_at is the snap clock time
    pulled_at = datetime.combine(day, datetime.min.time()).replace(
        hour=9 + (30 + snap_min) // 60, minute=(30 + snap_min) % 60)

    # 16:00 / 390 → fully closed; treat as "historical" (confidence 1.0, no projection)
    is_full = (snap_min >= 390)
    if is_full:
        proj_vol = partial['volume']
        confidence = 1.0
    else:
        proj = project_full_day_volume(partial['volume'], pulled_at)
        if proj is None:
            return None
        proj_vol = proj
        confidence = _intraday_confidence(pulled_at)

    r50 = proj_vol / avg_50
    r200 = proj_vol / avg_200
    w_agree = 1.0 - min(1.0, abs(r50 - r200) / max(r50, r200, 0.001))
    if len(short) < AVG_WINDOW_SHORT:
        w_agree *= 0.7
    pct50 = sum(1 for v in short if v < proj_vol) / len(short)
    reg_disc = min(1.0, r200 / r50) if r50 > 1.0 else 1.0

    c_range = partial['high'] - partial['low']
    if c_range == 0:
        sig_type, direction, raw_mag, adj_mag = 'NEUTRAL', 0, 0.0, 0.0
    else:
        body = abs(partial['close'] - partial['open'])
        body_r = body / c_range
        u_wick = partial['high'] - max(partial['open'], partial['close'])
        l_wick = min(partial['open'], partial['close']) - partial['low']
        c_dir = 1 if partial['close'] >= partial['open'] else -1
        recent_closes = [float(h.close) for h in prior_history[:RECENT_TREND_DAYS]]
        prev_close = float(prior_history[0].close) if prior_history else None

        sig_type, direction = _classify_signal(
            r50, body_r, u_wick, l_wick, body, c_dir, recent_closes,
            today_open=partial['open'], prev_close=prev_close)

        if sig_type == 'NEUTRAL':
            raw_mag, adj_mag = 0.0, 0.0
        else:
            raw_mag = _raw_magnitude(r50, pct50, sig_type)
            adj_mag = raw_mag * w_agree * reg_disc * confidence
            if sig_type == 'ABSORPTION' and r50 >= ABSORPTION_STRONG_THRESHOLD:
                adj_mag = min(1.0, adj_mag * ABSORPTION_STRONG_BOOST)

    multiplier = _to_multiplier(sig_type, direction, adj_mag) if sig_type != 'NEUTRAL' else 1.0

    return {
        'symbol': symbol,
        'date': day,
        'snap_min': snap_min,
        'partial_open': partial['open'],
        'partial_high': partial['high'],
        'partial_low': partial['low'],
        'partial_close': partial['close'],
        'partial_volume': partial['volume'],
        'projected_full_volume': proj_vol,
        'ratio_50': r50,
        'ratio_200': r200,
        'pct_50': pct50,
        'window_agreement': w_agree,
        'regime_discount': reg_disc,
        'signal_type': sig_type,
        'direction': direction,
        'raw_magnitude': raw_mag,
        'adj_magnitude': adj_mag,
        'multiplier': multiplier,
        'intraday_confidence': confidence,
    }


def main():
    paths = sorted(CACHE_DIR.glob('*_1m.parquet'))
    rows = []
    for p in paths:
        sym = p.stem.replace('_1m', '')
        df = pd.read_parquet(p)
        # Per-trading-day
        for day, day_df in df.groupby(df['ts'].dt.date):
            prior = load_prior_history(sym, day)
            if len(prior) < MIN_HISTORY_DAYS:
                continue
            for snap in SNAPSHOT_OFFSETS:
                partial = reconstruct_partial(day_df, snap)
                if partial is None:
                    continue
                row = classify_at_snapshot(sym, day, partial, snap, prior)
                if row is not None:
                    rows.append(row)
        print(f'  {sym}: {sum(1 for r in rows if r["symbol"]==sym)} snapshots')

    out = pd.DataFrame(rows)
    out['date'] = pd.to_datetime(out['date'])
    OUT_PATH.write_bytes(b'')  # truncate to clear stale parquet schema
    out.to_parquet(OUT_PATH, index=False)
    print(f'\n[OK] saved {OUT_PATH}: {len(out)} snapshot rows, '
          f'{out.groupby(["symbol","date"]).ngroups} stock-days')

    # Quick stats
    print('\nSignal type distribution at each snapshot:')
    pivot = out.groupby(['snap_min', 'signal_type']).size().unstack(fill_value=0)
    print(pivot.to_string())


if __name__ == '__main__':
    main()
