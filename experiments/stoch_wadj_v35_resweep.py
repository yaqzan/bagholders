"""v35 K-resweep for v34 CSWC dampener.

The v34 sweep landed K=0.30 on the v32 ledger (pre-CSWC). The v35 miss-ledger
shows the CSWC zone (wadj∈[1,12) ∧ stoch<35) at CALL 75+ STILL misses at 43.0%
vs cohort baseline 38.3% — +4.7pp residual. K=0.30 under-dampens.

This sweep re-runs the calibration on v35 data, reversing the live K=0.30 dampening
via weight_info.cswc_dampen + pre_boost, then applying alternate K values and
re-applying ern_boost.

Variants: sn ∈ {30,35,40} × wg ∈ {10,12,14} × K ∈ {0.30..1.20}
Fixed:    score_gate=75, lift_target=55 (semantic anchors from v34)

Gates: H1+H3+H4+H5 (same shape as v34 ship). N>=50 H5 threshold.
"""
from __future__ import annotations
import sys, io, json, math, time
from itertools import product
from pathlib import Path
from datetime import date, timedelta

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOOKBACK_DAYS = 1825
TODAY = date.fromisoformat('2026-05-05')
WINDOWS = {
    '1y': (TODAY - timedelta(365)).isoformat(),
    '3y': (TODAY - timedelta(1095)).isoformat(),
    '5y': (TODAY - timedelta(1825)).isoformat(),
}

CALL_TIERS = [('95+', 95), ('90+', 90), ('85+', 85), ('80+', 80), ('75+', 75)]
PUT_TIERS  = [('<25', 25), ('<20', 20), ('<15', 15)]

H1_PASS_PP   = 0.5
H1_MIN_TIERS = 3
H1_MAX_REG   = -1.0
H3_TOL       = 0.15
H5_N_MIN     = 50

TIER_WEIGHTS = {'95+': 0.20, '90+': 0.15, '85+': 0.12, '80+': 0.10, '75+': 0.10}

# Variant grid — fine grid around the wg=14, K~0.5 winner from coarse sweep.
SNS = [33, 35, 37, 40]
WGS = [12, 13, 14, 15]
KS  = [0.30, 0.40, 0.45, 0.48, 0.50, 0.52, 0.55, 0.60, 0.65]
LIFT_TARGET = 55
SCORE_GATE  = 75


def fetch_signals():
    """Pull v35 signals (calls + puts) joined to 30dte_opt w=15d barriers.

    Returns columns: symbol, date, overall, stoch, wadj, signal_type, side,
                     pre_boost, cswc_dampen, ern_boost, days_to_ern, result
    """
    from database.trader_database import DB
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    print(f'[sweep] fetching v35 scores from MySQL (cutoff={cutoff}) ...', flush=True)
    t0 = time.time()
    cur = DB.execute_sql(f"""
        SELECT s.symbol, s.date, s.overall, s.stoch, s.weight_info
        FROM scores s
        WHERE s.version_id = 35
          AND s.date >= '{cutoff}'
          AND s.overall IS NOT NULL
          AND ((s.overall >= 70) OR (s.overall <= 25))
    """)
    rows = cur.fetchall()
    print(f'[sweep]   {len(rows):,} score rows in {time.time()-t0:.1f}s', flush=True)

    syms, dates, overalls, stochs, wadjs, pre_boosts, cswcs, erns, dtes = [], [], [], [], [], [], [], [], []
    for sym, d, ov, st, winfo in rows:
        if winfo:
            try:
                wi = json.loads(winfo) if isinstance(winfo, str) else winfo
            except Exception:
                wi = {}
        else:
            wi = {}
        syms.append(sym)
        dates.append(d.isoformat() if hasattr(d, 'isoformat') else str(d))
        overalls.append(int(ov))
        stochs.append(int(st) if st is not None else 50)
        wadjs.append(float(wi.get('w_adj', 0.0)) if wi.get('w_adj') is not None else 0.0)
        pre_boosts.append(int(wi.get('pre_boost', ov)) if wi.get('pre_boost') is not None else int(ov))
        cswcs.append(float(wi.get('cswc_dampen', 0.0)) if wi.get('cswc_dampen') is not None else 0.0)
        erns.append(float(wi.get('ern_boost', 0.0)) if wi.get('ern_boost') is not None else 0.0)
        dte_v = wi.get('days_to_ern')
        dtes.append(int(dte_v) if dte_v is not None else -1)
    df = pl.DataFrame({
        'symbol': syms, 'date': dates,
        'overall': overalls, 'stoch': stochs,
        'wadj': wadjs, 'pre_boost': pre_boosts, 'cswc_dampen': cswcs,
        'ern_boost': erns, 'days_to_ern': dtes,
    })
    df = df.with_columns(
        pl.when(pl.col('overall') >= 70).then(pl.lit('CALL'))
          .when(pl.col('overall') <= 25).then(pl.lit('PUT'))
          .otherwise(None).alias('signal_type'),
        pl.when(pl.col('overall') >= 70).then(pl.lit('low'))
          .when(pl.col('overall') <= 25).then(pl.lit('high'))
          .otherwise(None).alias('side'),
    )

    # Join barriers from runner cache
    barriers_pq = ROOT / '.cache' / 'runner' / f'barriers_30opt_w15_{LOOKBACK_DAYS}.parquet'
    barriers = pl.read_parquet(barriers_pq).select(['symbol', 'date', 'side', 'result'])
    df = df.join(barriers, on=['symbol', 'date', 'side'], how='inner')
    df = df.with_columns(pl.col('result').fill_null(0).cast(pl.Int8).alias('result'))
    print(f'[sweep]   joined: {len(df):,} signals with barriers', flush=True)
    return df


def reconstruct_pre_cswc(pre_boost, cswc_dampen):
    """For overall>=75 calls, pre_cswc_overall = pre_boost + cswc_dampen.
    For signals where CSWC didn't fire (cswc_dampen=0), pre_cswc = pre_boost."""
    return pre_boost + cswc_dampen


def apply_new_k(pre_cswc, stoch, wadj, ern, signal_type,
                sn, wg, K, lift=LIFT_TARGET, sg=SCORE_GATE):
    """Apply new CSWC at given K, then re-apply ern_boost.

    Returns: new_overall (post all transforms, what bucketing uses).
    """
    # CSWC fires only on calls with pre_cswc>=sg, stoch<sn, wadj∈[1,wg)
    fire = (
        (signal_type == 'CALL') &
        (pre_cswc >= sg) &
        (stoch < sn) &
        (wadj >= 1.0) & (wadj < wg)
    )
    new_post_cswc = pre_cswc.astype(float).copy()
    if fire.any():
        stoch_w = np.clip((sn - stoch[fire]) / float(sn), 0.0, 1.0)
        wadj_w = np.clip((wg - wadj[fire]) / float(wg - 1), 0.0, 1.0)
        weakness = stoch_w * wadj_w
        new_post_cswc[fire] = pre_cswc[fire] - K * weakness * (pre_cswc[fire] - lift)
        new_post_cswc[fire] = np.maximum(0, np.round(new_post_cswc[fire]))

    # Apply ern_boost: preserve the same strength (boost depends on bucket which may shift,
    # but for first-pass approximation keep the live strength from v35 weight_info).
    new_overall = new_post_cswc + (new_post_cswc - 50) * ern
    new_overall = np.clip(np.round(new_overall), 0, 100)
    return new_overall.astype(int)


def main():
    print('=' * 90, flush=True)
    print('v35 CSWC K-resweep — finding the under-damped optimum', flush=True)
    print('=' * 90, flush=True)

    df = fetch_signals()
    print()

    # Extract arrays
    ov_arr     = df['overall'].to_numpy().astype(int)
    stoch_arr  = df['stoch'].to_numpy().astype(float)
    wadj_arr   = df['wadj'].to_numpy().astype(float)
    pre_boost  = df['pre_boost'].to_numpy().astype(int)
    cswc       = df['cswc_dampen'].to_numpy().astype(float)
    ern_arr    = df['ern_boost'].to_numpy().astype(float)
    sig_arr    = df['signal_type'].to_numpy()
    result_arr = df['result'].to_numpy().astype(int)
    date_arr   = df['date'].to_numpy()

    pre_cswc = reconstruct_pre_cswc(pre_boost, cswc)

    # Sanity: where CSWC fired, ov_arr should be approximately ern_boost(pre_boost)
    print('[sanity] cswc_dampen distribution (where fired):')
    fired = cswc > 0
    print(f'  N fired: {fired.sum():,} of {len(cswc):,} ({fired.mean()*100:.1f}%)')
    print(f'  cswc_dampen mean={cswc[fired].mean():.2f}  median={np.median(cswc[fired]):.2f}  max={cswc[fired].max():.2f}')

    # Baseline: live v35 scores (K=0.30 already applied)
    print('\n[baseline] live v35 TP% (K=0.30 applied):')
    baseline = {}
    for wlbl, cutoff in WINDOWS.items():
        mask_w = date_arr >= cutoff
        for tlbl, lo in CALL_TIERS:
            mask = mask_w & (ov_arr >= lo) & (sig_arr == 'CALL')
            n = int(mask.sum())
            tp = float(result_arr[mask].sum()) / n if n > 0 else float('nan')
            baseline[(tlbl, wlbl)] = (n, tp)
        for tlbl, hi in PUT_TIERS:
            mask = mask_w & (ov_arr <= hi) & (sig_arr == 'PUT')
            n = int(mask.sum())
            tp = float(result_arr[mask].sum()) / n if n > 0 else float('nan')
            baseline[(tlbl, wlbl)] = (n, tp)

    print(f'  {"tier":6s}  {"N (5y)":>7s}  {"TP% (5y)":>9s}  {"TP% (3y)":>9s}  {"TP% (1y)":>9s}')
    for tlbl, _ in CALL_TIERS + PUT_TIERS:
        n5, tp5 = baseline[(tlbl, '5y')]
        n3, tp3 = baseline[(tlbl, '3y')]
        n1, tp1 = baseline[(tlbl, '1y')]
        print(f'  {tlbl:6s}  {n5:>7,}  {tp5*100:>7.2f}%  {tp3*100:>7.2f}%  {tp1*100:>7.2f}%')

    # Sweep
    total = len(SNS) * len(WGS) * len(KS)
    print(f'\n[sweep] {total} variants ...', flush=True)
    t0 = time.time()
    results = []
    for sn, wg, K in product(SNS, WGS, KS):
        new_ov = apply_new_k(pre_cswc, stoch_arr, wadj_arr, ern_arr, sig_arr,
                             sn, wg, K)
        # Per (tier,window) stats
        stats = {}
        for wlbl, cutoff in WINDOWS.items():
            mask_w = date_arr >= cutoff
            for tlbl, lo in CALL_TIERS:
                mask = mask_w & (new_ov >= lo) & (sig_arr == 'CALL')
                n = int(mask.sum())
                tp = float(result_arr[mask].sum()) / n if n > 0 else float('nan')
                stats[(tlbl, wlbl)] = (n, tp)
            for tlbl, hi in PUT_TIERS:
                mask = mask_w & (new_ov <= hi) & (sig_arr == 'PUT')
                n = int(mask.sum())
                tp = float(result_arr[mask].sum()) / n if n > 0 else float('nan')
                stats[(tlbl, wlbl)] = (n, tp)

        # Gates
        gains, regs = [], []
        for tlbl, _ in CALL_TIERS:
            n_b, tp_b = baseline[(tlbl, '5y')]
            n_v, tp_v = stats[(tlbl, '5y')]
            if np.isnan(tp_b) or np.isnan(tp_v):
                continue
            dtp = (tp_v - tp_b) * 100
            if dtp >= H1_PASS_PP:
                gains.append(tlbl)
            if dtp < H1_MAX_REG:
                regs.append((tlbl, dtp))
        h1 = len(gains) >= H1_MIN_TIERS and len(regs) == 0

        h3_fails = []
        for tlbl, _ in CALL_TIERS[2:]:  # 85+,80+,75+
            n_b = baseline[(tlbl, '5y')][0]
            n_v = stats[(tlbl, '5y')][0]
            if n_b > 0 and abs(n_v - n_b) / n_b > H3_TOL:
                h3_fails.append((tlbl, (n_v - n_b) / n_b))
        h3 = len(h3_fails) == 0

        h4_fails = []
        for tlbl, hi in PUT_TIERS:
            n_b, tp_b = baseline[(tlbl, '5y')]
            n_v, tp_v = stats[(tlbl, '5y')]
            if not np.isnan(tp_b) and not np.isnan(tp_v):
                dtp = (tp_v - tp_b) * 100
                if dtp < -0.5:
                    h4_fails.append((tlbl, dtp))
        h4 = len(h4_fails) == 0

        h5_fails = []
        for tlbl, _ in CALL_TIERS[2:]:
            signs = {}
            for wlbl in ('1y', '3y', '5y'):
                n_v, tp_v = stats[(tlbl, wlbl)]
                n_b, tp_b = baseline[(tlbl, wlbl)]
                if n_v >= H5_N_MIN and n_b >= H5_N_MIN:
                    dtp = (tp_v - tp_b) * 100
                    if not np.isnan(dtp):
                        signs[wlbl] = np.sign(dtp)
            vals = list(signs.values())
            if len(vals) >= 2 and len(set(vals)) > 1 and 0.0 not in set(vals):
                h5_fails.append(tlbl)
        h5 = len(h5_fails) == 0

        all_pass = h1 and h3 and h4 and h5

        # Utility = weighted Δ TP% on tiers (heavy on 75+/80+/85+ where N is large)
        utility = 0.0
        for tlbl, _ in CALL_TIERS:
            n_b, tp_b = baseline[(tlbl, '5y')]
            n_v, tp_v = stats[(tlbl, '5y')]
            if not np.isnan(tp_b) and not np.isnan(tp_v):
                utility += TIER_WEIGHTS.get(tlbl, 0) * (tp_v - tp_b) * 100

        n_fired = int(((sig_arr == 'CALL') & (pre_cswc >= SCORE_GATE) &
                       (stoch_arr < sn) & (wadj_arr >= 1.0) & (wadj_arr < wg)).sum())

        results.append({
            'sn': sn, 'wg': wg, 'K': K, 'n_fired': n_fired,
            'dtp_75': (stats[('75+', '5y')][1] - baseline[('75+', '5y')][1]) * 100,
            'dtp_80': (stats[('80+', '5y')][1] - baseline[('80+', '5y')][1]) * 100,
            'dtp_85': (stats[('85+', '5y')][1] - baseline[('85+', '5y')][1]) * 100,
            'dtp_90': (stats[('90+', '5y')][1] - baseline[('90+', '5y')][1]) * 100,
            'dn_75':  (stats[('75+', '5y')][0] - baseline[('75+', '5y')][0]) / max(baseline[('75+', '5y')][0], 1),
            'dn_80':  (stats[('80+', '5y')][0] - baseline[('80+', '5y')][0]) / max(baseline[('80+', '5y')][0], 1),
            'dn_85':  (stats[('85+', '5y')][0] - baseline[('85+', '5y')][0]) / max(baseline[('85+', '5y')][0], 1),
            'utility': utility,
            'h1': h1, 'h3': h3, 'h4': h4, 'h5': h5, 'all_pass': all_pass,
            'h3_fails': h3_fails, 'h5_fails': h5_fails,
            'stats': stats,
        })

    print(f'[sweep] done in {time.time()-t0:.1f}s', flush=True)
    passing = [r for r in results if r['all_pass']]
    print(f'[sweep] {len(passing)}/{total} pass H1+H3+H4+H5 (N>={H5_N_MIN})')

    # Top by utility
    print('\n' + '=' * 90)
    print('TOP-15 BY UTILITY (passing variants first, then near-passes)')
    print('=' * 90)
    print(f'{"rank":>4s} {"sn":>3s} {"wg":>3s} {"K":>5s} {"n_fired":>7s} '
          f'{"75+":>7s} {"80+":>7s} {"85+":>7s} {"90+":>7s} '
          f'{"N75":>7s} {"N80":>7s} {"N85":>7s} '
          f'{"util":>6s}  pass')
    sorted_all = sorted(results, key=lambda r: (r['all_pass'], r['utility']), reverse=True)
    for i, r in enumerate(sorted_all[:15]):
        flags = (('H1' if r['h1'] else '..') + ('H3' if r['h3'] else '..') +
                 ('H4' if r['h4'] else '..') + ('H5' if r['h5'] else '..'))
        ship = 'SHIP' if r['all_pass'] else '----'
        print(f'{i+1:>4d} {r["sn"]:>3d} {r["wg"]:>3d} {r["K"]:>5.2f} {r["n_fired"]:>7,} '
              f'{r["dtp_75"]:>+6.2f}p {r["dtp_80"]:>+6.2f}p {r["dtp_85"]:>+6.2f}p {r["dtp_90"]:>+6.2f}p '
              f'{r["dn_75"]:>+6.1%} {r["dn_80"]:>+6.1%} {r["dn_85"]:>+6.1%} '
              f'{r["utility"]:>+6.3f}  {flags} {ship}')

    # Detail for top passing variant
    if passing:
        r = max(passing, key=lambda x: x['utility'])
    else:
        # Fallback: best H1-H4 (relax H5 if very small N)
        h1_4 = [x for x in results if x['h1'] and x['h3'] and x['h4']]
        r = max(h1_4, key=lambda x: x['utility']) if h1_4 else max(results, key=lambda x: x['utility'])

    print(f'\n{"=" * 90}')
    print(f'WINNER: sn={r["sn"]}, wg={r["wg"]}, K={r["K"]:.2f}  '
          f'(all_pass={r["all_pass"]}, util={r["utility"]:+.3f})')
    print('=' * 90)
    print(f'\nPer-tier per-window TP% delta (vs LIVE v35 K=0.30 baseline):')
    print(f'  {"tier":6s}  {"5y base":>8s}  {"5y var":>7s}  {"5y delta":>8s}  '
          f'{"3y delta":>8s}  {"1y delta":>8s}  {"N5y":>5s}  {"ΔN%":>6s}')
    for tlbl, _ in CALL_TIERS + PUT_TIERS:
        n_b, tp_b = baseline[(tlbl, '5y')]
        n_v, tp_v = r['stats'][(tlbl, '5y')]
        n_b3, tp_b3 = baseline[(tlbl, '3y')]
        n_v3, tp_v3 = r['stats'][(tlbl, '3y')]
        n_b1, tp_b1 = baseline[(tlbl, '1y')]
        n_v1, tp_v1 = r['stats'][(tlbl, '1y')]
        dtp5 = (tp_v - tp_b) * 100 if not (np.isnan(tp_b) or np.isnan(tp_v)) else float('nan')
        dtp3 = (tp_v3 - tp_b3) * 100 if not (np.isnan(tp_b3) or np.isnan(tp_v3)) else float('nan')
        dtp1 = (tp_v1 - tp_b1) * 100 if not (np.isnan(tp_b1) or np.isnan(tp_v1)) else float('nan')
        dn5 = (n_v - n_b) / max(n_b, 1)
        print(f'  {tlbl:6s}  {tp_b * 100:>7.2f}%  {tp_v * 100:>6.2f}%  {dtp5:>+7.2f}pp  '
              f'{dtp3:>+7.2f}pp  {dtp1:>+7.2f}pp  {n_v:>5,}  {dn5:>+6.1%}')


if __name__ == '__main__':
    main()
