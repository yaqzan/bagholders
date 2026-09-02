"""
NEW FINDING — investigate PriceHistory.volume vs 1-min sum staleness.

Hypothesis: yfinance's daily-bar volume for recent (T0/T-1) days is stale —
not yet reflecting full-day late-print settlement — while 1-min data IS up
to date. This causes the volume amplifier to read incorrect volume on the
freshest signals (the ones we trade against).

For each (sym, date) where 1m data exists, compare PH.volume vs sum(1m.volume)
and group by `days_ago` to test the staleness hypothesis.
"""
import json
from datetime import date
from pathlib import Path
import pandas as pd
from database.models.technical import PriceHistory

CACHE_DIR = Path('.cache/intraday_minute')
OUT = Path('experiments/intraday_confidence/volume_staleness.json')

today = date.today()


def main():
    rows = []
    for p in sorted(CACHE_DIR.glob('*_1m.parquet')):
        sym = p.stem.replace('_1m', '')
        df = pd.read_parquet(p)
        for day, day_df in df.groupby(df['ts'].dt.date):
            ph = PriceHistory.get_or_none(
                (PriceHistory.symbol == sym) & (PriceHistory.date == day))
            if not ph or ph.volume == 0:
                continue
            sum_1m = int(day_df['volume'].sum())
            if sum_1m == 0:
                continue
            rows.append({
                'symbol': sym,
                'date': day,
                'days_ago': (today - day).days,
                'ph_volume': ph.volume,
                'sum_1m_volume': sum_1m,
                'ratio': sum_1m / ph.volume,
                'pulled_at': ph.pulled_at,
            })
    df = pd.DataFrame(rows)

    print(f'Total stock-days analyzed: {len(df)}')
    print('\nRatio (1m_sum / ph_volume) by days_ago:')
    summary = df.groupby('days_ago')['ratio'].agg(['count', 'mean', 'median', 'std', 'min', 'max']).round(3)
    print(summary.to_string())

    print('\nFraction of stock-days with ratio > 2.0 (significant understatement):')
    by_age = df.groupby('days_ago').apply(lambda x: (x['ratio'] > 2.0).mean())
    print(by_age.round(3).to_string())

    # Per-stock view of today/yesterday
    print('\nMost recent stock-days only (days_ago <= 1):')
    recent = df[df['days_ago'] <= 1].sort_values(['days_ago', 'symbol'])
    print(recent[['symbol', 'date', 'days_ago', 'ph_volume', 'sum_1m_volume', 'ratio']].to_string(index=False))

    out_data = {
        'n_stock_days': len(df),
        'by_days_ago': summary.reset_index().to_dict('records'),
        'recent_stockdays': recent[['symbol', 'date', 'days_ago',
                                     'ph_volume', 'sum_1m_volume', 'ratio']]
            .assign(date=lambda x: x['date'].astype(str)).to_dict('records'),
    }
    OUT.write_text(json.dumps(out_data, indent=2, default=str))
    print(f'\n[OK] saved {OUT}')


if __name__ == '__main__':
    main()
