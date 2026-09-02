"""Smoke MC — directional DD/compound check for V4 ship candidate.

Runs monte_carlo.py twice on the same window:
  - BASELINE: production v46 scores
  - TREATMENT: production v46 + V4 SWPM transform applied at signal-load time

The treatment uses runtime monkey-patch: builds a (sym, date) -> new_overall
lookup table, then patches mc.load_signals / mc.load_put_signals to apply the
transform in-place before the cascade runs.

Production code is NOT modified. The patch lives only in this script via
try/finally teardown. Same pattern as `experiments/weekly_avwap/phase_i_*.py`.

Output:
  - smoke_mc_baseline_<window>.json  (DD/compound metrics)
  - smoke_mc_treatment_<window>.json (same)
  - smoke_mc_summary.txt             (side-by-side comparison)
"""
from __future__ import annotations
import sys, os, json, time, pickle

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.sector_etf_alpha.swpm_v2 import SWPMv2Params, apply_swpm_v2

LOCAL = ROOT / '.cache' / 'sector_etf_alpha'
SCREEN = ROOT / '.cache' / 'sector_etf_screen'
OUT = ROOT / 'experiments' / 'sector_etf_alpha'

# V4 ship candidate parameters (from phase_f_ablation winner)
V4_PARAMS = SWPMv2Params(
    SWPM_ENABLED=True, RSU_ENABLED=True,
    GATE_LO_C=70, GATE_HI_C=84, GATE_LO_P=16, GATE_HI_P=25,
    EMA_K=8.1919, RSI_K=22.7129, MACD_TANH_K=0.83,  # MACD_TANH_K unused since MACD_W=0
    EMA_W=0.4028, RSI_W=0.5655, MACD_W=0.0,
    PHASE_POWER=1.7660, SCORE_POWER=1.3609,
    ALPHA_DOWN=0.5606, TARGET_DOWN=57.3281,
    ALPHA_UP=0.8971, TARGET_UP=90.7926,
    ALPHA_PUT_DOWN=0.6088, TARGET_PUT=30.3925,
    ALPHA_PUT_UP=0.1508, TARGET_PUT_UP=12.7735,
    RS_GATE=0.0647, RS_K=0.1252, RS_POWER=1.6429, RS_SCORE_POWER=2.2108,
    RS_ALPHA_C=0.8667, RS_TARGET_C=69.8427,
    RS_ALPHA_P=0.4730, RS_TARGET_P=31.0075,
)


def build_lookup() -> dict:
    """Build {(symbol, date_str): new_overall} from v46 peaks + V4 transform.

    Cached at LOCAL / 'v4_lookup.pkl'.
    """
    cache = LOCAL / 'v4_lookup.pkl'
    if cache.exists():
        with open(cache, 'rb') as f:
            d = pickle.load(f)
        print(f'[lookup] cache hit: {len(d):,} entries', flush=True)
        return d

    print('[lookup] building from v46 peaks + features + V4 transform...', flush=True)

    # Use cohort_v46 (already has all features joined)
    coh = pl.read_parquet(LOCAL / 'cohort_v46_1825.parquet')
    print(f'  cohort_v46 has {len(coh):,} resolved peaks', flush=True)

    # Apply V4 transform
    out = apply_swpm_v2(coh, V4_PARAMS)
    out = out.select(['symbol', 'date', 'overall', 'new_overall'])

    # Now also need to handle UNRESOLVED peaks (recent ones not in cohort).
    # Read peaks_v46 (full), join with ETF features + stock returns.
    peaks = pl.read_parquet(LOCAL / 'peaks_v46_1825.parquet')
    print(f'  peaks_v46 has {len(peaks):,} total peaks (resolved + unresolved)', flush=True)

    # Find unresolved
    coh_keys = set(zip(coh['symbol'].to_list(), coh['date'].to_list()))
    peak_keys = set(zip(peaks['symbol'].to_list(), peaks['date'].to_list()))
    unresolved = peak_keys - coh_keys
    print(f'  unresolved peaks: {len(unresolved):,}', flush=True)

    if unresolved:
        # Build features for unresolved
        unr_df = peaks.filter(
            pl.struct(['symbol', 'date']).map_elements(
                lambda x: (x['symbol'], x['date']) in unresolved, return_dtype=pl.Boolean
            )
        )
        # Join meta + ETF features + stock returns
        meta = pl.read_parquet(SCREEN / 'stock_meta.parquet')
        etf_wide = pl.read_parquet(SCREEN / 'etf_features_wide.parquet')
        stock_ret = pl.read_parquet(LOCAL / f'stock_returns_v46_1825.parquet')

        unr_df = unr_df.join(meta, on='symbol', how='left')
        unr_df = unr_df.with_columns((pl.col('market_cap') / 1e9).alias('mcap_b'))
        unr_df = unr_df.join(stock_ret, on=['symbol', 'date'], how='left')
        unr_df = unr_df.join(etf_wide, on='date', how='left')

        SECTOR_ETF_MAP = {
            'technology': 'XLK', 'healthcare': 'XLV', 'consumer-cyclical': 'XLY',
            'industrials': 'XLI', 'basic-materials': 'XLB', 'real-estate': 'XLRE',
            'financial-services': 'XLF', 'energy': 'XLE', 'consumer-defensive': 'XLP',
            'utilities': 'XLU', 'communication-services': 'XLC',
        }
        sec_map = pl.DataFrame({'sector': list(SECTOR_ETF_MAP.keys()),
                                'sector_etf': list(SECTOR_ETF_MAP.values())})
        unr_df = unr_df.join(sec_map, on='sector', how='left')
        for k in ['pct_ema50', 'pct_ema200', 'rsi', 'macd', 'ret_5d', 'ret_20d']:
            expr = pl.lit(None, dtype=pl.Float64)
            for etf in SECTOR_ETF_MAP.values():
                col = f'{etf}_{k}'
                if col in unr_df.columns:
                    expr = pl.when(pl.col('sector_etf') == etf).then(pl.col(col)).otherwise(expr)
            unr_df = unr_df.with_columns(expr.alias(f'sec_etf_{k}'))

        # Apply transform
        out_unr = apply_swpm_v2(unr_df, V4_PARAMS)
        out_unr = out_unr.select(['symbol', 'date', 'overall', 'new_overall'])
        out = pl.concat([out, out_unr], how='vertical_relaxed')

    print(f'[lookup] total {len(out):,} entries', flush=True)

    # Build dict
    lookup = {}
    for sym, dt, ov, new_ov in out.iter_rows():
        lookup[(sym, dt)] = (int(ov), int(new_ov))

    with open(cache, 'wb') as f:
        pickle.dump(lookup, f)
    print(f'[lookup] saved {cache}', flush=True)
    return lookup


def patch_mc(mc_module, lookup: dict, OVERFLOW=70, PUT_THRESH=25):
    """Monkey-patch mc.load_signals and mc.load_put_signals.

    The patches:
      - call the original loader
      - look up new_overall for each signal
      - if new_overall keeps signal in qualifying tier, mutate s.overall in place
      - if not, drop the signal
    """
    orig_load_signals = mc_module.load_signals
    orig_load_put_signals = mc_module.load_put_signals

    def patched_load_signals(version, d_start, d_end):
        sigs = orig_load_signals(version, d_start, d_end)
        kept = []
        modified = 0
        dropped = 0
        for s in sigs:
            key = (s.symbol_id, s.date.isoformat() if hasattr(s.date, 'isoformat') else str(s.date))
            entry = lookup.get(key)
            if entry is None:
                # Not in lookup (no V4 transform applied — keep at original)
                kept.append(s)
                continue
            orig_ov, new_ov = entry
            if new_ov >= OVERFLOW:
                if new_ov != orig_ov:
                    s.overall = new_ov
                    modified += 1
                kept.append(s)
            else:
                dropped += 1
        # Re-sort by (date, overall desc) since overall changed
        kept.sort(key=lambda s: (s.date, -int(s.overall)))
        print(f'[patch_call] orig N={len(sigs):,} kept={len(kept):,} modified={modified} dropped={dropped}', flush=True)
        return kept

    def patched_load_put_signals(version, d_start, d_end):
        sigs = orig_load_put_signals(version, d_start, d_end)
        kept = []
        modified = 0
        dropped = 0
        for s in sigs:
            key = (s.symbol_id, s.date.isoformat() if hasattr(s.date, 'isoformat') else str(s.date))
            entry = lookup.get(key)
            if entry is None:
                kept.append(s)
                continue
            orig_ov, new_ov = entry
            if new_ov <= PUT_THRESH:
                if new_ov != orig_ov:
                    s.overall = new_ov
                    modified += 1
                kept.append(s)
            else:
                dropped += 1
        kept.sort(key=lambda s: (s.date, int(s.overall)))
        print(f'[patch_put] orig N={len(sigs):,} kept={len(kept):,} modified={modified} dropped={dropped}', flush=True)
        return kept

    mc_module.load_signals = patched_load_signals
    mc_module.load_put_signals = patched_load_put_signals

    return orig_load_signals, orig_load_put_signals


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['baseline', 'treatment'], required=True)
    ap.add_argument('--window', default='22-now')
    ap.add_argument('--n', type=int, default=200)
    args = ap.parse_args()

    lookup = build_lookup() if args.mode == 'treatment' else None

    # Set MC env vars BEFORE importing
    os.environ['N_ITER_OVERRIDE'] = str(args.n)
    os.environ['WINDOWS_OVERRIDE'] = args.window
    os.environ['MC_NO_MP'] = '1'  # Single-process for cleaner patch interaction

    print(f'[smoke] mode={args.mode} window={args.window} N={args.n}', flush=True)

    import monte_carlo as mc
    if args.mode == 'treatment':
        orig_call, orig_put = patch_mc(mc, lookup)
        print(f'[smoke] patches installed', flush=True)
    try:
        t0 = time.time()
        mc.main()
        print(f'[smoke] MC done in {time.time()-t0:.0f}s', flush=True)
    finally:
        if args.mode == 'treatment':
            mc.load_signals = orig_call
            mc.load_put_signals = orig_put
            print(f'[smoke] patches removed', flush=True)


if __name__ == '__main__':
    main()
