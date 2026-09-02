"""Miss-ledger analyzer — pattern-mining over per-signal feature + outcome rows.

For each (signal_type, score_bin) cohort:
  baseline P(miss) = mean(is_miss)
  For each feature -> binned cell:
      cell_miss = mean(is_miss | cell)
      lift      = cell_miss / baseline
      z         = (cell_miss - baseline) / sqrt(baseline*(1-baseline)/N_cell)
  Rank cells by |z| -> top miss-lift (overrepresented in fails) and
                                    miss-protective (underrepresented).

Then explore 2-feature combos for the top-10 single-feature drivers per cohort
to surface conditional patterns.

Outputs:
  experiments/miss_ledger/report.md  — top patterns per cohort + interpretation
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
LOCAL_CACHE = ROOT / '.cache' / 'miss_ledger'
REPORT = Path(__file__).parent / 'report.md'

# ────────────────────────────────────────────────────────────────────
# Feature binning
# ────────────────────────────────────────────────────────────────────

COMP_BINS = [(-1, 35, 'lo'), (35, 65, 'mid'), (65, 101, 'hi')]
EMA_BINS = [(-1e9, -15, 'vneg'), (-15, -5, 'neg'), (-5, 5, 'near'),
            (5, 15, 'pos'), (15, 1e9, 'vpos')]
VMAG_BINS = [(-1, 0.001, 'none'), (0.001, 0.3, 'lo'), (0.3, 0.6, 'mid'),
             (0.6, 1.5, 'hi')]
BRD_BINS = [(-1, 35, 'lo'), (35, 50, 'mid_lo'), (50, 65, 'mid_hi'),
            (65, 80, 'hi'), (80, 101, 'vhi')]
SIGMA_BINS = [(-1, 1.5, 'tight'), (1.5, 3.0, 'mid'), (3.0, 5.0, 'wide'),
              (5.0, 1e9, 'vwide')]
VEL_BINS = [(-100, -10, 'cool'), (-10, 5, 'flat'), (5, 15, 'warm'),
            (15, 100, 'hot')]
WADJ_BINS_PUT = [(-100, -25, 'vneg'), (-25, -13, 'neg'), (-13, 0, 'mild'),
                 (0, 100, 'pos')]
WADJ_BINS_CALL = [(-100, 0, 'neg'), (0, 13, 'mild'), (13, 25, 'mid'),
                  (25, 100, 'strong')]


def regime_label(mult):
    if mult is None:
        return 'unk'
    if mult <= 0.78: return 'STRESS'
    if mult <= 0.88: return 'CAUTION'
    if mult <= 1.00: return 'NEUTRAL'
    if mult <= 1.05: return 'HEALTHY'
    return 'BULL'


def bin_value(v, bins):
    if v is None:
        return 'na'
    for lo, hi, lbl in bins:
        if lo <= v < hi:
            return lbl
    return 'na'


def add_bins(df: pl.DataFrame, signal_type: str) -> pl.DataFrame:
    """Add discrete-bin columns for every analyzed feature."""
    wadj_bins = WADJ_BINS_PUT if signal_type == 'PUT' else WADJ_BINS_CALL

    def bin_expr(col, bins, alias):
        expr = pl.lit('na')
        for lo, hi, lbl in reversed(bins):
            expr = pl.when((pl.col(col) >= lo) & (pl.col(col) < hi)).then(pl.lit(lbl)).otherwise(expr)
        # null handling
        expr = pl.when(pl.col(col).is_null()).then(pl.lit('na')).otherwise(expr)
        return expr.alias(alias)

    df = df.with_columns([
        bin_expr('bb', COMP_BINS, 'b_bb'),
        bin_expr('trend', COMP_BINS, 'b_trend'),
        bin_expr('rsi', COMP_BINS, 'b_rsi'),
        bin_expr('macd', COMP_BINS, 'b_macd'),
        bin_expr('stoch', COMP_BINS, 'b_stoch'),
        bin_expr('ta', COMP_BINS, 'b_ta'),
        bin_expr('ema50_pct', EMA_BINS, 'b_ema50'),
        bin_expr('ema200_pct', EMA_BINS, 'b_ema200'),
        bin_expr('vol_mag', VMAG_BINS, 'b_vmag'),
        bin_expr('breadth_score', BRD_BINS, 'b_brd'),
        bin_expr('sigma_pct', SIGMA_BINS, 'b_sigma'),
        bin_expr('velocity_7d', VEL_BINS, 'b_vel'),
        bin_expr('wadj', wadj_bins, 'b_wadj'),
    ])
    # categorical / flag features — fall through directly
    df = df.with_columns([
        pl.col('vol_sig').fill_null('NEUTRAL').alias('b_vsig'),
        pl.col('reg_mult').map_elements(regime_label, return_dtype=pl.Utf8).alias('b_regime'),
        pl.when(pl.col('mis_stress') > 0.1).then(pl.lit('on')).otherwise(pl.lit('off')).alias('b_mis_stress'),
        pl.when(pl.col('ext_damp') > 0.5).then(pl.lit('on')).otherwise(pl.lit('off')).alias('b_ext_damp'),
        pl.when(pl.col('exh_damp') > 0.5).then(pl.lit('on')).otherwise(pl.lit('off')).alias('b_exh_damp'),
        pl.when(pl.col('cap_dampened') == 1).then(pl.lit('on')).otherwise(pl.lit('off')).alias('b_cap_damp'),
    ])
    return df


# Features to mine (column names after add_bins)
FEATURES = [
    'b_bb', 'b_trend', 'b_rsi', 'b_macd', 'b_stoch', 'b_ta',
    'b_ema50', 'b_ema200', 'b_vmag', 'b_brd', 'b_sigma', 'b_vel',
    'b_wadj', 'b_vsig', 'b_regime',
    'b_mis_stress', 'b_ext_damp', 'b_exh_damp', 'b_cap_damp',
]

# ────────────────────────────────────────────────────────────────────
# Pattern miner
# ────────────────────────────────────────────────────────────────────

def cohort_baseline(df: pl.DataFrame) -> tuple[float, int]:
    n = len(df)
    if n == 0:
        return 0.0, 0
    return float(df['is_miss'].mean()), n


def feature_lifts(df: pl.DataFrame, feature: str, baseline: float, n_total: int,
                   min_n: int = 50) -> list[dict]:
    """For one feature, return list of {value, n, miss_rate, lift, z}."""
    g = (df.group_by(feature)
            .agg(pl.len().alias('n'), pl.col('is_miss').mean().alias('miss_rate'))
            .filter(pl.col('n') >= min_n))
    out = []
    for row in g.iter_rows(named=True):
        n = row['n']; mr = row['miss_rate']
        if baseline <= 0 or baseline >= 1:
            z = 0.0; lift = 1.0
        else:
            se = math.sqrt(baseline * (1 - baseline) / n)
            z = (mr - baseline) / se if se > 0 else 0.0
            lift = mr / baseline
        out.append({
            'feature': feature, 'value': row[feature],
            'n': n, 'miss_rate': mr,
            'lift': lift, 'z': z,
            'pct_of_cohort': n / n_total if n_total > 0 else 0.0,
        })
    return out


def two_feature_lifts(df: pl.DataFrame, f1: str, f2: str,
                       baseline: float, n_total: int, min_n: int = 30) -> list[dict]:
    g = (df.group_by([f1, f2])
            .agg(pl.len().alias('n'), pl.col('is_miss').mean().alias('miss_rate'))
            .filter(pl.col('n') >= min_n))
    out = []
    for row in g.iter_rows(named=True):
        n = row['n']; mr = row['miss_rate']
        if baseline <= 0 or baseline >= 1:
            z = 0.0; lift = 1.0
        else:
            se = math.sqrt(baseline * (1 - baseline) / n)
            z = (mr - baseline) / se if se > 0 else 0.0
            lift = mr / baseline
        out.append({
            'features': f'{f1}={row[f1]} & {f2}={row[f2]}',
            'n': n, 'miss_rate': mr,
            'lift': lift, 'z': z,
            'pct_of_cohort': n / n_total if n_total > 0 else 0.0,
        })
    return out


# ────────────────────────────────────────────────────────────────────
# Cohort definitions
# ────────────────────────────────────────────────────────────────────
# (label, signal_type, score_bin_filter_fn)
COHORTS = [
    ('CALL 95+',   'CALL', lambda o: o >= 95),
    ('CALL 90+',   'CALL', lambda o: o >= 90),
    ('CALL 85+',   'CALL', lambda o: o >= 85),
    ('CALL 80+',   'CALL', lambda o: o >= 80),
    ('CALL 75+',   'CALL', lambda o: o >= 75),
    ('CALL 70+',   'CALL', lambda o: o >= 70),
    ('PUT  <25',   'PUT',  lambda o: o <= 25),
    ('PUT  <20',   'PUT',  lambda o: o <= 20),
    ('PUT  <15',   'PUT',  lambda o: o <= 15),
    ('PUT  <10',   'PUT',  lambda o: o <= 10),
]


def write_report(report_path: Path, sections: list[str]):
    body = '\n\n'.join(sections)
    report_path.write_text(body, encoding='utf-8')
    print(f'\n[analyzer] wrote {report_path}', flush=True)


def fmt_table_single(rows: list[dict], top_n: int = 12) -> str:
    rows = sorted(rows, key=lambda r: abs(r['z']), reverse=True)[:top_n]
    lines = ['| feature | value | N | miss% | lift | z | %cohort |',
             '|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['feature']} | {r['value']} | {r['n']:,} | "
                     f"{r['miss_rate']*100:.1f}% | {r['lift']:.2f} | "
                     f"{r['z']:+.1f} | {r['pct_of_cohort']*100:.1f}% |")
    return '\n'.join(lines)


def fmt_table_pairs(rows: list[dict], top_n: int = 15) -> str:
    rows = sorted(rows, key=lambda r: abs(r['z']), reverse=True)[:top_n]
    lines = ['| pattern | N | miss% | lift | z | %cohort |',
             '|---|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['features']} | {r['n']:,} | "
                     f"{r['miss_rate']*100:.1f}% | {r['lift']:.2f} | "
                     f"{r['z']:+.1f} | {r['pct_of_cohort']*100:.1f}% |")
    return '\n'.join(lines)


def main():
    parquets = sorted(LOCAL_CACHE.glob('ledger_v*_*.parquet'))
    if not parquets:
        raise RuntimeError(f'No ledger parquet found in {LOCAL_CACHE}')
    src = parquets[-1]
    print(f'[analyzer] loading {src}', flush=True)
    df = pl.read_parquet(src)
    print(f'[analyzer] {len(df):,} rows, {len(df.columns)} cols', flush=True)

    sections = [
        f"# Miss-Ledger Pattern Mining — {src.name}",
        f"Source: `{src}`  •  Rows: **{len(df):,}**\n",
        "## Methodology\n",
        "Per-cohort (signal_type × score bin) we measure:",
        "- **Baseline miss rate** = P(NOT TP) over all signals in cohort, where `TP` = barrier_outcome.result == 1 on 30 DTE option-aligned barriers (TP=+0.35, SL=-0.30 calls / -0.20 puts) at w=15d.",
        "- For each (feature, value) bin: **lift** = P(miss|bin) / P(miss|baseline), **z** = standardized deviation under H0=baseline.",
        "- Cells filtered to N≥50 (single feature) / N≥30 (pair). Sorted by |z|.",
        "- High-z + lift>1 = bin **overrepresented in misses** (avoid signals here).",
        "- High-z + lift<1 = bin **underrepresented** (signals here are systematically better than the cohort baseline).",
        "\n---\n",
    ]

    for label, sig_type, filter_fn in COHORTS:
        sub = df.filter(pl.col('signal_type') == sig_type)
        sub = sub.filter(pl.col('overall').map_elements(filter_fn, return_dtype=pl.Boolean))
        if len(sub) < 200:
            sections.append(f"## {label}\n*N={len(sub)} — skipped (insufficient data)*\n")
            continue
        sub = add_bins(sub, sig_type)
        baseline, n_total = cohort_baseline(sub)

        # Single-feature lifts
        all_single = []
        for feat in FEATURES:
            all_single.extend(feature_lifts(sub, feat, baseline, n_total, min_n=50))

        # Top single-feature features by max |z|
        feat_max_z = {}
        for r in all_single:
            cur = feat_max_z.get(r['feature'], 0)
            if abs(r['z']) > abs(cur):
                feat_max_z[r['feature']] = r['z']
        top_features = sorted(feat_max_z.keys(), key=lambda f: abs(feat_max_z[f]), reverse=True)[:6]

        # Pair lifts among top features
        all_pairs = []
        for i, f1 in enumerate(top_features):
            for f2 in top_features[i+1:]:
                all_pairs.extend(two_feature_lifts(sub, f1, f2, baseline, n_total, min_n=30))

        sections.append(
            f"## {label}\n"
            f"**N = {n_total:,}  •  baseline miss rate = {baseline*100:.1f}%**  "
            f"(TP rate {(1-baseline)*100:.1f}%)\n\n"
            f"### Top single-feature patterns (|z| ≥ 2.5, sorted by |z|)\n\n"
            f"{fmt_table_single([r for r in all_single if abs(r['z']) >= 2.5])}\n\n"
            f"### Top 2-feature patterns (top-6 features, N≥30)\n\n"
            f"{fmt_table_pairs([r for r in all_pairs if abs(r['z']) >= 2.5])}\n"
        )
        print(f'[analyzer] {label}: N={n_total} baseline_miss={baseline*100:.1f}% '
              f'-- top_features={top_features[:4]}', flush=True)

    write_report(REPORT, sections)
    print(f'[analyzer] done.', flush=True)


if __name__ == '__main__':
    main()
