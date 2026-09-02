"""DD-Ledger Analyzer — cohort lift/z mining for DD-PnL contribution.

Two parallel mining axes:

1. **DD-contributor binary** — `is_dd_contributor = (in_worst_episode AND
   pnl_dollars < 0)`. Tells us "is this cohort overrepresented in trades
   that LOST during the seed's worst DD episode?" (z stat under H0=baseline).

2. **DD-dollar share** — sum of `dd_contribution_share` per cohort cell,
   normalized by cell's share of total trade count. Tells us "do trades in
   this cohort carry MORE than their proportional weight of episode losses?"
   (Concentrated-loss cohorts are the prime DD reduction targets.)

Per-cohort analysis is split by signal_type (CALL / PUT) and by score tier.
Single-feature axes mine then top-6 features get cross-tabbed for pairs.

Output: experiments/dd_ledger/report.md
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
LOCAL_CACHE = ROOT / '.cache' / 'dd_ledger'
REPORT = Path(__file__).parent / 'report.md'

# ────────────────────────────────────────────────────────────────────
# Bins (re-uses miss-ledger conventions; adds DD-specific axes)
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
# DD-specific axes
CONCURRENT_BINS = [(0, 4, 'lo'), (4, 8, 'mid'), (8, 12, 'hi'), (12, 99, 'vhi')]
ENTRY_DD_BINS   = [(-0.01, 0.05, 'none'), (0.05, 0.20, 'shallow'),
                   (0.20, 0.40, 'mid'), (0.40, 1.0, 'deep')]
BRD_5D_BINS     = [(-100, -10, 'crash'), (-10, -3, 'fall'),
                   (-3, 3, 'flat'), (3, 10, 'rise'), (10, 100, 'surge')]


def bin_expr(col, bins, alias):
    expr = pl.lit('na')
    for lo, hi, lbl in reversed(bins):
        expr = pl.when((pl.col(col) >= lo) & (pl.col(col) < hi)).then(pl.lit(lbl)).otherwise(expr)
    expr = pl.when(pl.col(col).is_null()).then(pl.lit('na')).otherwise(expr)
    return expr.alias(alias)


def add_bins(df: pl.DataFrame, signal_type: str) -> pl.DataFrame:
    wadj_bins = WADJ_BINS_PUT if signal_type == 'PUT' else WADJ_BINS_CALL
    df = df.with_columns([
        bin_expr('bb', COMP_BINS, 'b_bb'),
        bin_expr('trend', COMP_BINS, 'b_trend'),
        bin_expr('rsi', COMP_BINS, 'b_rsi'),
        bin_expr('macd', COMP_BINS, 'b_macd'),
        bin_expr('stoch', COMP_BINS, 'b_stoch'),
        bin_expr('ta', COMP_BINS, 'b_ta'),
        bin_expr('ema50_pct', EMA_BINS, 'b_ema50'),
        bin_expr('vol_mag', VMAG_BINS, 'b_vmag'),
        bin_expr('breadth_score', BRD_BINS, 'b_brd'),
        bin_expr('sigma_pct', SIGMA_BINS, 'b_sigma'),
        bin_expr('velocity_7d', VEL_BINS, 'b_vel'),
        bin_expr('wadj', wadj_bins, 'b_wadj'),
        bin_expr('concurrent_total', CONCURRENT_BINS, 'b_concur'),
        bin_expr('entry_open_calls', CONCURRENT_BINS, 'b_concur_calls'),
        bin_expr('entry_open_puts', CONCURRENT_BINS, 'b_concur_puts'),
        bin_expr('breadth_5d_chg', BRD_5D_BINS, 'b_brd5d'),
    ])
    df = df.with_columns([
        pl.col('vol_sig').fill_null('NEUTRAL').alias('b_vsig'),
        pl.col('regime_label').fill_null('unk').alias('b_regime'),
        pl.col('entry_dd_band').alias('b_entry_dd'),
        pl.col('outcome').alias('b_outcome'),
        pl.col('tier').alias('b_tier'),
        pl.col('window').alias('b_window'),
        pl.when(pl.col('mis_stress') > 0.1).then(pl.lit('on')).otherwise(pl.lit('off')).alias('b_mis_stress'),
        pl.when(pl.col('ext_damp') > 0.5).then(pl.lit('on')).otherwise(pl.lit('off')).alias('b_ext_damp'),
        pl.when(pl.col('ern')).then(pl.lit('on')).otherwise(pl.lit('off')).alias('b_ern'),
    ])
    return df


FEATURES = [
    # Per-signal (from scoring)
    'b_bb', 'b_trend', 'b_rsi', 'b_macd', 'b_stoch', 'b_ta',
    'b_ema50', 'b_vmag', 'b_brd', 'b_sigma', 'b_vel',
    'b_wadj', 'b_vsig', 'b_regime',
    'b_mis_stress', 'b_ext_damp', 'b_ern',
    # Path features (NEW)
    'b_entry_dd', 'b_concur', 'b_concur_calls', 'b_concur_puts', 'b_brd5d',
    # Window (always relevant — DD drivers vary by year)
    'b_window',
]


# ────────────────────────────────────────────────────────────────────
# Mining
# ────────────────────────────────────────────────────────────────────

def cohort_baseline(df: pl.DataFrame) -> tuple[float, float, int]:
    """Returns (baseline_dd_loser_rate, total_dd_dollars, n_trades).

    is_dd_loser: trade is in worst episode AND lost money.
    """
    n = len(df)
    if n == 0:
        return 0.0, 0.0, 0
    # is_dd_loser = in_worst_episode AND pnl_dollars < 0
    loser = df.with_columns(
        ((pl.col('in_worst_episode')) & (pl.col('pnl_dollars') < 0)).alias('is_dd_loser')
    )
    base = float(loser['is_dd_loser'].mean())
    # Total dollars contributed to DD (sum of negative pnl among in-episode trades)
    dd_dollars = float(loser.filter(pl.col('is_dd_loser'))['pnl_dollars'].sum() * -1)
    return base, dd_dollars, n


def feature_lifts(df: pl.DataFrame, feature: str, baseline: float,
                  total_dd_dollars: float, n_total: int, min_n: int = 50) -> list[dict]:
    """For one feature, return list of cells with both DD-loser lift AND DD-dollar concentration."""
    g = (df.with_columns(
            ((pl.col('in_worst_episode')) & (pl.col('pnl_dollars') < 0)).alias('is_dd_loser'),
            pl.when(pl.col('in_worst_episode') & (pl.col('pnl_dollars') < 0))
              .then(-pl.col('pnl_dollars'))
              .otherwise(0.0)
              .alias('dd_loss_dollars')
         )
         .group_by(feature)
         .agg(pl.len().alias('n'),
              pl.col('is_dd_loser').mean().alias('loser_rate'),
              pl.col('dd_loss_dollars').sum().alias('cell_dd_dollars'))
         .filter(pl.col('n') >= min_n))
    out = []
    for row in g.iter_rows(named=True):
        n = row['n']; lr = row['loser_rate']; dd_d = row['cell_dd_dollars']
        if baseline <= 0 or baseline >= 1:
            z = 0.0; lift = 1.0
        else:
            se = math.sqrt(baseline * (1 - baseline) / n)
            z = (lr - baseline) / se if se > 0 else 0.0
            lift = lr / baseline
        # DD-dollar concentration: cell's share of DD dollars vs cell's share of trade count
        cell_n_share = n / n_total if n_total > 0 else 0.0
        cell_dd_share = dd_d / total_dd_dollars if total_dd_dollars > 0 else 0.0
        dd_concentration = cell_dd_share / cell_n_share if cell_n_share > 0 else 0.0
        out.append({
            'feature': feature, 'value': row[feature],
            'n': n, 'loser_rate': lr,
            'lift': lift, 'z': z,
            'pct_of_cohort': cell_n_share,
            'cell_dd_dollars': dd_d,
            'dd_share': cell_dd_share,
            'dd_concentration': dd_concentration,
        })
    return out


def two_feature_lifts(df: pl.DataFrame, f1: str, f2: str,
                      baseline: float, total_dd_dollars: float,
                      n_total: int, min_n: int = 30) -> list[dict]:
    g = (df.with_columns(
            ((pl.col('in_worst_episode')) & (pl.col('pnl_dollars') < 0)).alias('is_dd_loser'),
            pl.when(pl.col('in_worst_episode') & (pl.col('pnl_dollars') < 0))
              .then(-pl.col('pnl_dollars'))
              .otherwise(0.0)
              .alias('dd_loss_dollars')
         )
         .group_by([f1, f2])
         .agg(pl.len().alias('n'),
              pl.col('is_dd_loser').mean().alias('loser_rate'),
              pl.col('dd_loss_dollars').sum().alias('cell_dd_dollars'))
         .filter(pl.col('n') >= min_n))
    out = []
    for row in g.iter_rows(named=True):
        n = row['n']; lr = row['loser_rate']; dd_d = row['cell_dd_dollars']
        if baseline <= 0 or baseline >= 1:
            z = 0.0; lift = 1.0
        else:
            se = math.sqrt(baseline * (1 - baseline) / n)
            z = (lr - baseline) / se if se > 0 else 0.0
            lift = lr / baseline
        cell_n_share = n / n_total if n_total > 0 else 0.0
        cell_dd_share = dd_d / total_dd_dollars if total_dd_dollars > 0 else 0.0
        dd_concentration = cell_dd_share / cell_n_share if cell_n_share > 0 else 0.0
        out.append({
            'features': f'{f1}={row[f1]} & {f2}={row[f2]}',
            'n': n, 'loser_rate': lr,
            'lift': lift, 'z': z,
            'pct_of_cohort': cell_n_share,
            'dd_share': cell_dd_share,
            'dd_concentration': dd_concentration,
        })
    return out


# ────────────────────────────────────────────────────────────────────
# Cohorts
# ────────────────────────────────────────────────────────────────────

COHORTS = [
    ('CALL all (75+)', 'call', lambda s: s >= 75),
    ('CALL 95+',       'call', lambda s: s >= 95),
    ('CALL 85-94',     'call', lambda s: 85 <= s < 95),
    ('CALL 80-84',     'call', lambda s: 80 <= s < 85),
    ('CALL 75-79',     'call', lambda s: 75 <= s < 80),
    ('PUT  all (<=25)','put',  lambda s: s <= 25),
    ('PUT  <=15',      'put',  lambda s: s <= 15),
    ('PUT  16-20',     'put',  lambda s: 16 <= s <= 20),
    ('PUT  21-25',     'put',  lambda s: 21 <= s <= 25),
]


def fmt_table_single(rows: list[dict], top_n: int = 15) -> str:
    rows = sorted(rows, key=lambda r: abs(r['z']), reverse=True)[:top_n]
    lines = ['| feature | value | N | DD-loser% | lift | z | %coh | DD$ | DD-conc |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        dd_d = r['cell_dd_dollars']
        lines.append(f"| {r['feature']} | {r['value']} | {r['n']:,} | "
                     f"{r['loser_rate']*100:.1f}% | {r['lift']:.2f} | "
                     f"{r['z']:+.1f} | {r['pct_of_cohort']*100:.1f}% | "
                     f"${_fmt_dollars(dd_d)} | {r['dd_concentration']:.2f}× |")
    return '\n'.join(lines)


def fmt_table_pairs(rows: list[dict], top_n: int = 20) -> str:
    rows = sorted(rows, key=lambda r: abs(r['z']), reverse=True)[:top_n]
    lines = ['| pattern | N | DD-loser% | lift | z | %coh | DD$share | DD-conc |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['features']} | {r['n']:,} | "
                     f"{r['loser_rate']*100:.1f}% | {r['lift']:.2f} | "
                     f"{r['z']:+.1f} | {r['pct_of_cohort']*100:.1f}% | "
                     f"{r['dd_share']*100:.1f}% | {r['dd_concentration']:.2f}× |")
    return '\n'.join(lines)


def fmt_concentration_table(rows: list[dict], top_n: int = 12) -> str:
    """Top cells by DD-dollar concentration (DD share / count share, sign-aware)."""
    # Filter to cells with positive DD share AND non-trivial size
    rows = [r for r in rows if r['dd_share'] > 0.005 and r['n'] >= 100]
    rows = sorted(rows, key=lambda r: r['dd_concentration'], reverse=True)[:top_n]
    lines = ['| feature | value | N | %coh | DD-loser% | DD$ | DD$share | DD-conc |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['feature']} | {r['value']} | {r['n']:,} | "
                     f"{r['pct_of_cohort']*100:.1f}% | {r['loser_rate']*100:.1f}% | "
                     f"${_fmt_dollars(r['cell_dd_dollars'])} | "
                     f"{r['dd_share']*100:.1f}% | {r['dd_concentration']:.2f}× |")
    return '\n'.join(lines)


def _fmt_dollars(d: float) -> str:
    if abs(d) >= 1e12: return f"{d/1e12:.2f}T"
    if abs(d) >= 1e9:  return f"{d/1e9:.2f}B"
    if abs(d) >= 1e6:  return f"{d/1e6:.2f}M"
    if abs(d) >= 1e3:  return f"{d/1e3:.1f}k"
    return f"{d:.0f}"


def main(version: int):
    src = LOCAL_CACHE / f'dd_ledger_v{version}.parquet'
    if not src.exists():
        raise SystemExit(f'missing {src} — run build_dd_ledger.py first')
    print(f'[analyzer] loading {src}', flush=True)
    df = pl.read_parquet(src)
    print(f'[analyzer] {len(df):,} rows × {len(df.columns)} cols', flush=True)

    sections = [
        f"# DD-Contribution Ledger Pattern Mining — v{version}",
        f"Source: `{src.name}`  •  Trades: **{len(df):,}**\n",
        "## Methodology\n",
        "Per-cohort (signal_type × score tier) we measure:",
        "- **Baseline DD-loser rate** = P(trade is in seed's worst DD episode AND lost money) over all trades in cohort.",
        "- **Lift / z** — same cohort math as miss-ledger: `lift = P(loser|cell)/baseline`, `z = (cell-base)/SE` under H0=baseline.",
        "- **DD-dollar concentration** — `(cell's DD$ share) / (cell's count share)`. >1.0× = cell carries more DD weight than its size; <1.0× = cell is DD-protective.",
        "- Cells filtered N≥50 (single) / N≥30 (pair). Sorted by |z| for primary view, by `dd_concentration` for the concentration-focused table.",
        "- High |z| + lift>1 + DD-conc>1.5× = prime hypothesis target (this cohort consistently loses during DD AND carries disproportionate dollar weight).\n",
        "**Path features (NEW vs miss-ledger):**",
        "- `b_entry_dd` — running portfolio DD when this trade opened (none/shallow/mid/deep)",
        "- `b_concur` — total concurrent positions at entry",
        "- `b_concur_calls`, `b_concur_puts` — by-side concurrent at entry",
        "- `b_brd5d` — 5-trading-day breadth_score change (crash/fall/flat/rise/surge)",
        "- `b_window` — which evaluation window the trade ran in\n",
        "---\n",
    ]

    for label, sig_type, filter_fn in COHORTS:
        sub = df.filter(pl.col('side') == sig_type)
        sub = sub.filter(pl.col('score').map_elements(filter_fn, return_dtype=pl.Boolean))
        if len(sub) < 500:
            sections.append(f"## {label}\n*N={len(sub)} — skipped (insufficient data)*\n")
            continue
        sub = add_bins(sub, sig_type.upper())
        baseline, total_dd, n_total = cohort_baseline(sub)

        all_single = []
        for feat in FEATURES:
            all_single.extend(feature_lifts(sub, feat, baseline, total_dd, n_total, min_n=50))

        # Top features by max |z| (for pair mining)
        feat_max_z = {}
        for r in all_single:
            cur = feat_max_z.get(r['feature'], 0)
            if abs(r['z']) > abs(cur):
                feat_max_z[r['feature']] = r['z']
        top_features = sorted(feat_max_z.keys(),
                              key=lambda f: abs(feat_max_z[f]), reverse=True)[:6]

        all_pairs = []
        for i, f1 in enumerate(top_features):
            for f2 in top_features[i+1:]:
                all_pairs.extend(two_feature_lifts(sub, f1, f2, baseline, total_dd,
                                                    n_total, min_n=30))

        sections.append(
            f"## {label}\n"
            f"**N = {n_total:,}  •  baseline DD-loser rate = {baseline*100:.1f}%  "
            f"•  total DD$ in cohort = ${_fmt_dollars(total_dd)}**\n\n"
            f"### Top single-feature lifts (|z| ≥ 3, by |z|)\n\n"
            f"{fmt_table_single([r for r in all_single if abs(r['z']) >= 3.0])}\n\n"
            f"### Top concentration cells (DD-conc ≥ 1.0× by share, N≥100)\n\n"
            f"{fmt_concentration_table(all_single)}\n\n"
            f"### Top 2-feature patterns (top-6 features × top-6, |z| ≥ 3)\n\n"
            f"{fmt_table_pairs([r for r in all_pairs if abs(r['z']) >= 3.0])}\n"
        )
        print(f'[analyzer] {label}: N={n_total:,} baseline={baseline*100:.1f}% '
              f'DD$={_fmt_dollars(total_dd)} top_features={top_features[:4]}', flush=True)

    REPORT.write_text('\n\n'.join(sections), encoding='utf-8')
    print(f'\n[analyzer] wrote {REPORT}', flush=True)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--version', type=int, default=32)
    args = p.parse_args()
    main(args.version)
