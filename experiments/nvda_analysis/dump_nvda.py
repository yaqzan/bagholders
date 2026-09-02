"""
NVDA cross-version data dump for analysis.

Pulls into experiments/nvda_analysis/data/:
  - versions.json          : all production AlgorithmVersion rows (id->label, git_message)
  - scores_by_version.csv  : NVDA Score rows since 2024-01-01, every version, full components + weight_info
  - price_history.csv      : NVDA OHLCV since 2024-01-01
  - indicators.csv         : NVDA daily indicators since 2024-01-01
  - coverage.json          : per-version NVDA score coverage (min/max date, n rows)

Single-symbol, indexed queries only — light foreground load, no MySQL universe scan.
"""
import os, sys, json, csv
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.models import Score, Stock  # noqa
from database.models.core import AlgorithmVersion  # noqa
from database.models.technical import PriceHistory, Indicator  # noqa

SYM = 'NVDA'
START = date(2024, 1, 1)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(OUT, exist_ok=True)


def dump_versions():
    rows = []
    for v in AlgorithmVersion.select().order_by(AlgorithmVersion.id):
        rows.append({
            'id': v.id,
            'label': v.production_label,
            'git_commit': v.git_commit,
            'git_message': v.git_message or '',
        })
    with open(os.path.join(OUT, 'versions.json'), 'w', encoding='utf-8') as f:
        json.dump(rows, f, indent=2)
    print(f"versions: {len(rows)}")
    return {r['id']: r['label'] for r in rows}


def dump_scores(labels):
    q = (Score.select()
         .where((Score.symbol == SYM) & (Score.date >= START))
         .order_by(Score.version, Score.date))
    cols = ['version_id', 'label', 'date', 'overall', 'bb', 'trend', 'rsi', 'macd', 'stoch',
            'technical_alignment', 'volume', 'price', 'daily_change',
            'volume_signal', 'volume_magnitude', 'pct_from_ema50', 'pct_from_ema200',
            'bb_position', 'regime_composite', 'regime_multiplier', 'next_earnings', 'weight_info']
    n = 0
    cov = {}
    with open(os.path.join(OUT, 'scores_by_version.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(cols)
        for s in q:
            vid = s.version_id if hasattr(s, 'version_id') else s.version.id
            lbl = labels.get(vid, f'db:{vid}')
            wi = s.weight_info or ''
            # keep weight_info compact single-line
            wi = wi.replace('\n', ' ').replace('\r', ' ') if wi else ''
            w.writerow([vid, lbl, s.date.isoformat(), s.overall, s.bb, s.trend, s.rsi, s.macd, s.stoch,
                        s.technical_alignment, s.volume,
                        float(s.price) if s.price is not None else '',
                        float(s.daily_change) if s.daily_change is not None else '',
                        s.volume_signal or '', s.volume_magnitude if s.volume_magnitude is not None else '',
                        s.pct_from_ema50 if s.pct_from_ema50 is not None else '',
                        s.pct_from_ema200 if s.pct_from_ema200 is not None else '',
                        s.bb_position if s.bb_position is not None else '',
                        float(s.regime_composite) if s.regime_composite is not None else '',
                        float(s.regime_multiplier) if s.regime_multiplier is not None else '',
                        s.next_earnings if s.next_earnings is not None else '',
                        wi])
            n += 1
            c = cov.setdefault(lbl, {'version_id': vid, 'n': 0, 'min': None, 'max': None})
            c['n'] += 1
            d = s.date.isoformat()
            if c['min'] is None or d < c['min']:
                c['min'] = d
            if c['max'] is None or d > c['max']:
                c['max'] = d
    with open(os.path.join(OUT, 'coverage.json'), 'w', encoding='utf-8') as f:
        json.dump(cov, f, indent=2)
    print(f"scores rows: {n}  versions-with-NVDA: {len(cov)}")


def dump_price():
    q = (PriceHistory.select()
         .where((PriceHistory.symbol == SYM) & (PriceHistory.date >= START))
         .order_by(PriceHistory.date))
    n = 0
    with open(os.path.join(OUT, 'price_history.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['date', 'open', 'high', 'low', 'close', 'volume'])
        for p in q:
            w.writerow([p.date.isoformat(), float(p.open), float(p.high), float(p.low),
                        float(p.close), int(p.volume) if p.volume is not None else ''])
            n += 1
    print(f"price rows: {n}")


def dump_indicators():
    # Pull whatever columns exist on Indicator
    q = (Indicator.select()
         .where((Indicator.symbol == SYM) & (Indicator.date >= START))
         .order_by(Indicator.date))
    rows = list(q)
    if not rows:
        print("indicators: 0")
        return
    # introspect fields
    field_names = [f for f in rows[0]._meta.sorted_field_names]
    with open(os.path.join(OUT, 'indicators.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(field_names)
        for r in rows:
            out = []
            for fn in field_names:
                val = getattr(r, fn, None)
                if hasattr(val, 'isoformat'):
                    val = val.isoformat()
                elif isinstance(val, Stock):
                    val = val.symbol
                elif val is not None and not isinstance(val, (int, float, str)):
                    try:
                        val = float(val)
                    except (TypeError, ValueError):
                        val = str(val)
                out.append(val if val is not None else '')
            w.writerow(out)
    print(f"indicator rows: {len(rows)}  fields: {field_names}")


if __name__ == '__main__':
    labels = dump_versions()
    dump_scores(labels)
    dump_price()
    dump_indicators()
    print("DONE ->", OUT)
