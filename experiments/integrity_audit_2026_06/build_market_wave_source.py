"""F1 fix (2026-06-09 integrity audit): rebuild the Market Wave source CSV.

The v57 Sector Market Wave score transform has been INERT since its source file
`.cache/market_wave/predictive_market_wave_v57_source.csv` vanished — the loader
silently returns an empty series and every score passes through unadjusted.

This builder reconstructs a candidate source series self-contained from SPDR
sector-ETF prices/indicators already persisted in the DB, via
`market_breadth._load_sector_etf_breadth_rows()` (the same helper the MWDD mine
used to rebuild 1999+ breadth history).

IMPORTANT: this is NOT a reproduction of the lost v57 file (provenance unknown).
The rebuilt series is a NEW candidate input — the wave must re-validate through
an honest Stage-1 A/B before it ships enabled. With the shipped params
(`source='market_wave'`, `mode='direct_market_wave'`), the loader's
`_apply_market_wave_source()` computes `market_wave_score` deterministically
from `pct_above_ema50`/`pct_above_ema200` using DEFAULT_MARKET_WAVE_PARAMS, so
columns `date,pct_above_ema50,pct_above_ema200,avg_rsi` are all it needs.

Usage (from repo root):
    python experiments/integrity_audit_2026_06/build_market_wave_source.py
        [--out .cache/market_wave/predictive_market_wave_v57_source.csv]
"""
import argparse
import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / ".cache" / "market_wave" / "predictive_market_wave_v57_source.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--end", default=None, help="end date YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    from market_breadth import _load_sector_etf_breadth_rows

    end_date = date.fromisoformat(args.end) if args.end else date.today()
    rows = _load_sector_etf_breadth_rows(end_date=end_date)
    if not rows:
        print("ERROR: no sector ETF breadth rows returned - are SPDR "
              "indicators/prices persisted?", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: r["date"])
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "pct_above_ema50", "pct_above_ema200", "avg_rsi"])
        for r in rows:
            w.writerow([
                r["date"].isoformat(),
                f"{r['pct_above_ema50']:.4f}" if r.get("pct_above_ema50") is not None else "",
                f"{r['pct_above_ema200']:.4f}" if r.get("pct_above_ema200") is not None else "",
                f"{r['avg_rsi']:.4f}" if r.get("avg_rsi") is not None else "",
            ])

    print(f"wrote {len(rows)} rows -> {out_path}")
    print(f"  date range: {rows[0]['date']} -> {rows[-1]['date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
