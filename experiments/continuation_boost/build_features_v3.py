"""Build features v2 — multi-window (W7/W15/W30/W60) + W60-dominant sig.

Key changes from v1 (build_features.py):
  • Pulls W7/W15/W30/W60 columns (was just W7/W15)
  • Computes per-prior `sig` value using long-window-dominant logic from Q3 findings
  • Calls only (puts have no continuation edge per Q3)
  • Keeps current_overall in 60-89 range (sub-threshold + already-qualifying near boundary)

NO-CASCADE ARCHITECTURE:
  All prior lookups use raw v32 scores (from components_v32_1825.parquet).
  In production this means: prior reads must use weight_info['pre_boost'] field,
  never the (boosted) overall column.

Outputs:
  .cache/continuation_boost/peaks_v2.parquet
      one row per call peak with overall in [60, 89]
  .cache/continuation_boost/priors_v2.parquet
      long-format (peak, prior) pairs with W7/W15/W30/W60 + sig classification
"""
from __future__ import annotations
import io, sys, sqlite3, time, pickle
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

CACHE_DB        = ROOT / '.cache' / 'barrier_outcomes.db'
COMPONENTS_PQ   = ROOT / '.cache' / 'continuation_boost' / 'components_v32_50plus.parquet'  # WIDER UNIVERSE
BARRIERS_30OPT  = ROOT / '.cache' / 'runner' / 'barriers_30opt_w15_1825.parquet'
WINS_PKL        = ROOT / '.cache' / 'continuation_boost' / 'wins_30dgen_w7_15_30_60.pkl'

OUT_DIR = ROOT / '.cache' / 'continuation_boost'
OUT_DIR.mkdir(parents=True, exist_ok=True)
PEAKS_PQ_V2     = OUT_DIR / 'peaks_v3.parquet'   # v3 outputs
PRIORS_PQ_V2    = OUT_DIR / 'priors_v3.parquet'

# Calls only (Q3: puts have no edge)
# v3: WIDER UNIVERSE — admit calls 60-100 (sub-threshold 60-74 + already-qualifying 75+)
PEAK_CALL_LO   = 60
PEAK_CALL_HI   = 100

# Prior selection (calls only)
PRIOR_CALL_GATE = 70   # priors must be 70+ (existing call peak)
PRIOR_LBMIN     = 7    # prior must be at least 7 days old (W7 knowable)
PRIOR_LBMAX     = 60   # prior at most 60 days old

# Bucket assignment (matches assess_scores)
def assign_top_bucket(overall: int) -> str:
    if overall >= 95: return '95+'
    if overall >= 90: return '90+'
    if overall >= 85: return '85+'
    if overall >= 80: return '80+'
    if overall >= 75: return '75+'
    if overall >= 70: return '70+'
    if overall >= 65: return '65-69'   # sub-threshold buckets for promotion source
    if overall >= 60: return '60-64'
    return None


def load_components_calls() -> pl.DataFrame:
    df = pl.read_parquet(COMPONENTS_PQ).select(['symbol', 'date', 'overall'])
    # date might already be Date type in v3 parquet; convert if String
    if df['date'].dtype == pl.Utf8:
        df = df.with_columns(pl.col('date').str.to_date())
    return df


def load_wins_30dgen() -> dict:
    """Load 30dte_generic w=7,15,30,60 wins from pickle cache (built earlier)."""
    if not WINS_PKL.exists():
        raise SystemExit(f'Run overlap_analysis.py first to populate {WINS_PKL.name}')
    print(f'[load] wins from pickle: {WINS_PKL.name}', flush=True)
    t0 = time.time()
    with open(WINS_PKL, 'rb') as f:
        wins = pickle.load(f)
    print(f'[load]   {len(wins):,} entries in {time.time()-t0:.1f}s', flush=True)
    return wins


def load_outcomes() -> dict:
    """Load 30dte_opt w=15d outcomes (strategy-aligned barrier)."""
    df = pl.read_parquet(BARRIERS_30OPT).select(['symbol', 'date', 'side', 'result'])
    if df['date'].dtype == pl.Utf8:
        df = df.with_columns(pl.col('date').str.to_date())
    out = {}
    for r in df.iter_rows(named=True):
        out[(r['symbol'], r['date'], r['side'])] = r['result']
    return out


def classify_prior_sig(w7, w15, w30, w60):
    """Return per-prior contribution sign per Q3 findings.

       W60 known win:    +1.0   (sustained move confirmed)
       W60 known loss:   -0.3   (trend died by 60d → mild penalty)
       W60 unknown, W30 known win:  +0.5   (partial confirmation)
       W60 unknown, W30 known loss: -0.2   (mid-trend death → tiny penalty)
       Both W30+W60 unknown: 0  (too recent — no signal)
       Special: (W7=1, W15=1, W30=0, W60=0) "early-only fizzler" → -0.4
                                                  (worse anti-signal per data)
    """
    # Extract knowability + outcome for W30 and W60
    w60_known = w60 is not None
    w30_known = w30 is not None
    w15_known = w15 is not None
    w7_known  = w7  is not None

    if w60_known:
        if w60 == 1:
            return +1.0
        # W60 known loss — check for early-only fizzler
        if w7_known and w15_known and w30_known and w7 == 1 and w15 == 1 and w30 == 0:
            return -0.4   # early winner that fizzled
        return -0.3
    elif w30_known:
        if w30 == 1:
            return +0.5
        # W30 known loss — check for early-only fizzler (W60 not yet)
        if w7_known and w15_known and w7 == 1 and w15 == 1:
            return -0.3   # early winner stalled by 30d
        return -0.2
    else:
        return 0.0


def build_peaks(comp: pl.DataFrame, outcomes: dict) -> pl.DataFrame:
    """Build per-peak feature table for calls in [60, 89] range."""
    earliest = comp['date'].min()
    cutoff_lo = earliest + timedelta(days=PRIOR_LBMAX + 5)
    peaks = comp.filter(
        (pl.col('overall') >= PEAK_CALL_LO) &
        (pl.col('overall') <= PEAK_CALL_HI) &
        (pl.col('date') >= cutoff_lo)
    ).with_columns([
        pl.lit('call').alias('side'),
        pl.lit('low').alias('barrier_side'),
        (pl.col('overall') - 50).alias('conviction'),
        pl.col('overall').map_elements(assign_top_bucket, return_dtype=pl.Utf8).alias('bucket'),
    ])
    # Join outcomes (NULL means signal too recent for forward walk)
    rows = []
    for r in peaks.iter_rows(named=True):
        out_v = outcomes.get((r['symbol'], r['date'], r['barrier_side']))
        rows.append({**r, 'outcome_tp_15d': out_v})
    out_df = pl.DataFrame(rows)
    # Drop peaks with NULL outcome (no forward-walk yet)
    out_df = out_df.filter(pl.col('outcome_tp_15d').is_not_null())
    print(f'[peaks] {len(out_df):,} call peaks in overall ∈ [{PEAK_CALL_LO}, {PEAK_CALL_HI}]', flush=True)
    print(f'[peaks] outcome split: TP={out_df.filter(pl.col("outcome_tp_15d")==1).height:,}  '
          f'SL={out_df.filter(pl.col("outcome_tp_15d")==0).height:,}', flush=True)
    return out_df


def build_priors(peaks: pl.DataFrame, comp: pl.DataFrame, wins: dict) -> pl.DataFrame:
    """For each peak, find prior call peaks (70+) within [LBMIN, LBMAX]."""
    print(f'[priors] scanning ...', flush=True)
    t0 = time.time()
    # Build per-symbol date→score dict for fast prior lookup
    from collections import defaultdict
    by_sym = defaultdict(list)
    for r in comp.iter_rows(named=True):
        by_sym[r['symbol']].append((r['date'], r['overall']))
    for s in by_sym:
        by_sym[s].sort()

    # For each peak, find prior peaks. sym_dates is sorted ASCENDING by date,
    # so gap (= d - pd_) DECREASES as we iterate. We want priors with
    # PRIOR_LBMIN <= gap <= PRIOR_LBMAX. Walk forward; gap > LBMAX skip,
    # gap in range process, gap < LBMIN break (subsequent dates have even smaller gap).
    rows = []
    for r in peaks.iter_rows(named=True):
        sym, d = r['symbol'], r['date']
        sym_dates = by_sym[sym]
        for (pd_, pov) in sym_dates:
            gap = (d - pd_).days
            if gap > PRIOR_LBMAX: continue   # too old, but later may fit
            if gap < PRIOR_LBMIN: break      # too recent (and all subsequent ones too)
            if pov < PRIOR_CALL_GATE: continue
            # Look up wins for this prior at all 4 windows
            w7  = wins.get((sym, pd_, 'low', 7))
            w15 = wins.get((sym, pd_, 'low', 15))
            w30 = wins.get((sym, pd_, 'low', 30))
            w60 = wins.get((sym, pd_, 'low', 60))
            r7  = w7[0]  if w7  else None
            r15 = w15[0] if w15 else None
            r30 = w30[0] if w30 else None
            r60 = w60[0] if w60 else None
            entry_close = w7[1] if w7 else (w15[1] if w15 else (w30[1] if w30 else (w60[1] if w60 else None)))

            # Knowability gate per window: only count W if gap >= W
            r7_eff  = r7  if gap >= 7  else None
            r15_eff = r15 if gap >= 15 else None
            r30_eff = r30 if gap >= 30 else None
            r60_eff = r60 if gap >= 60 else None

            sig = classify_prior_sig(r7_eff, r15_eff, r30_eff, r60_eff)

            rows.append({
                'symbol': sym, 'date': d, 'prior_date': pd_, 'gap_days': int(gap),
                'prior_score': int(pov), 'prior_conviction': int(abs(pov - 50)),
                'win_7d': int(r7_eff) if r7_eff is not None else None,
                'win_15d': int(r15_eff) if r15_eff is not None else None,
                'win_30d': int(r30_eff) if r30_eff is not None else None,
                'win_60d': int(r60_eff) if r60_eff is not None else None,
                'sig': float(sig),
                'prior_close': float(entry_close) if entry_close is not None else None,
            })
    if not rows:
        df = pl.DataFrame(schema={
            'symbol': pl.Utf8, 'date': pl.Date, 'prior_date': pl.Date,
            'gap_days': pl.Int64, 'prior_score': pl.Int64, 'prior_conviction': pl.Int64,
            'win_7d': pl.Int64, 'win_15d': pl.Int64, 'win_30d': pl.Int64, 'win_60d': pl.Int64,
            'sig': pl.Float64, 'prior_close': pl.Float64,
        })
    else:
        df = pl.DataFrame(rows, schema={
            'symbol': pl.Utf8, 'date': pl.Date, 'prior_date': pl.Date,
            'gap_days': pl.Int64, 'prior_score': pl.Int64, 'prior_conviction': pl.Int64,
            'win_7d': pl.Int64, 'win_15d': pl.Int64, 'win_30d': pl.Int64, 'win_60d': pl.Int64,
            'sig': pl.Float64, 'prior_close': pl.Float64,
        })
    print(f'[priors] {len(df):,} (peak, prior) pairs in {time.time()-t0:.1f}s', flush=True)
    if len(df) > 0:
        n_peaks_with_prior = (df.group_by(['symbol', 'date'])
                              .agg(pl.len().alias('n')).height)
        print(f'[priors] peaks with >=1 prior: {n_peaks_with_prior:,} '
              f'({100*n_peaks_with_prior/len(peaks):.1f}%)', flush=True)
    # sig distribution
    if len(df) > 0:
        print(f'[priors] sig distribution:')
        sig_counts = df.group_by(pl.col('sig').round(1)).agg(pl.len().alias('count')).sort('sig')
        for r in sig_counts.iter_rows(named=True):
            print(f'    sig={r["sig"]:+.1f}: {r["count"]:,}', flush=True)
    return df


def main():
    print(f'build_features_v2  ({date.today().isoformat()})')
    print(f'  call peak range: [{PEAK_CALL_LO}, {PEAK_CALL_HI}]')
    print(f'  prior range: gap_days ∈ [{PRIOR_LBMIN}, {PRIOR_LBMAX}], score ≥ {PRIOR_CALL_GATE}')
    print(f'  sig classification: W60-dominant, anti-signal for early fizzlers')
    print(f'  NO-CASCADE: priors read from raw v32 components')

    print(f'\n[1] components ...')
    comp = load_components_calls()
    print(f'    {len(comp):,} score rows; range {comp["date"].min()} → {comp["date"].max()}')

    print(f'\n[2] outcomes (30dte_opt w=15d) ...')
    outcomes = load_outcomes()
    print(f'    {len(outcomes):,} outcomes')

    print(f'\n[3] peaks (calls in [{PEAK_CALL_LO}, {PEAK_CALL_HI}]) ...')
    peaks = build_peaks(comp, outcomes)

    print(f'\n[4] wins (30dte_generic W7/15/30/60) ...')
    wins = load_wins_30dgen()

    print(f'\n[5] priors ...')
    priors = build_priors(peaks, comp, wins)

    peaks.write_parquet(PEAKS_PQ_V2)
    priors.write_parquet(PRIORS_PQ_V2)
    print(f'\n[done] wrote {PEAKS_PQ_V2.name} ({len(peaks):,} rows)')
    print(f'[done] wrote {PRIORS_PQ_V2.name} ({len(priors):,} rows)')


if __name__ == '__main__':
    main()
