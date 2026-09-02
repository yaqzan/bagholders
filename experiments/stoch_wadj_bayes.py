"""Bayesian fine-tune for call-side stoch+wadj gradient dampener.

v2: Gradient form (no hard stoch gate).
  - stoch_neutral_boundary: score below which stoch starts contributing weakness.
    Semantic anchor = 35 (COMP_BINS neutral threshold). No cliff at 22.
    stoch_w = clip((boundary - stoch) / boundary, 0, 1)
  - wadj_gate_hi: wadj below which weekly starts contributing weakness.
    wadj_w = clip((gate_hi - max(1.0, wadj)) / (gate_hi - 1.0), 0, 1)
  - K: overall dampener strength

After initial hard-gate sweep (v1) found stoch_gate=22, wg=11, K=0.55 as best.
User noted hard thresholds create cliffs; gradient ramp is more principled.
The natural stoch neutral boundary is 35 (= COMP_BINS lo/mid boundary).

Output: ranked table of passing variants + recommended ship candidate.
"""
from __future__ import annotations
import sys, io, time
from itertools import product
from pathlib import Path
from datetime import date, timedelta

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / '.cache' / 'miss_ledger' / 'ledger_v32_1825.parquet'

TODAY   = date.fromisoformat('2026-05-04')
WINDOWS = {
    '1y': (TODAY - timedelta(365)).isoformat(),
    '3y': (TODAY - timedelta(1095)).isoformat(),
    '5y': (TODAY - timedelta(1825)).isoformat(),
}

CALL_TIERS  = [('95+', 95), ('90+', 90), ('85+', 85), ('80+', 80), ('75+', 75)]
PUT_TIERS   = [('<25', 25), ('<20', 20), ('<15', 15)]

H1_PASS_PP   = 0.5
H1_MIN_TIERS = 3
H1_MAX_REG   = -1.0
H3_TOL       = 0.15
H5_N_MIN     = 50   # minimum signals per (tier,window) for sign check — below this = excluded

# Tier allocation weights for utility (proportional to capital deployed)
TIER_WEIGHTS = {'95+': 0.20, '90+': 0.15, '85+': 0.12, '80+': 0.10, '75+': 0.10}

# ── param grid ─────────────────────────────────────────────────────────────
# Gradient form: no hard stoch gate — smooth ramp from STOCH_NEUTRAL down to 0.
# STOCH_NEUTRAL is the "zero contradiction" anchor (stoch at or above this = no dampening).
# Semantic anchor = 35 (COMP_BINS lo/mid boundary). Testing small range around it.

STOCH_NEUTRALS = [30, 35, 40, 45, 50]   # where stoch contradiction begins
WADJ_GATES     = [9, 10, 11, 12, 13]    # wadj upper gate (CWCF handles < 1)
KS             = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55]
LIFT_TARGET    = 55
SCORE_GATE     = 75

# Total: 5 × 5 × 7 = 175 variants


# ── helpers ─────────────────────────────────────────────────────────────────

def tp_rate(sub: pl.DataFrame, score_col: str = 'new_ov', lo: int = None, hi: int = None):
    if lo is not None:
        s = sub.filter(pl.col(score_col) >= lo)
    else:
        s = sub.filter(pl.col(score_col) <= hi)
    n = len(s)
    if n == 0:
        return (0, float('nan'))
    return (n, float((s['result'] == 1).sum()) / n)


def apply_dampener(ov: np.ndarray, stoch: np.ndarray, wadj: np.ndarray,
                   sig_type,
                   stoch_neutral: int, wadj_gate: int, K: float,
                   lift: int = 55, sg: int = 75) -> np.ndarray:
    """Gradient dampener — no hard stoch gate.

    stoch_w: smooth ramp from stoch_neutral (0 contribution) down to stoch=0 (full).
    wadj_w:  smooth ramp from wadj_gate (0) down to wadj=1 (full); CWCF covers < 1.
    """
    stoch_w = np.clip((stoch_neutral - stoch) / float(stoch_neutral), 0.0, 1.0)
    wadj_w  = np.clip((wadj_gate - np.maximum(wadj, 1.0)) / float(wadj_gate - 1), 0.0, 1.0)
    weakness = stoch_w * wadj_w
    fire = (
        (ov >= sg) &
        (stoch < stoch_neutral) &   # only fires where stoch contributes
        (wadj >= 1.0) &             # CWCF handles < 1
        (wadj < wadj_gate) &
        (sig_type == 'CALL')
    )
    new_ov = ov.astype(float).copy()
    new_ov[fire] = np.round(ov[fire] - K * weakness[fire] * (ov[fire] - lift))
    return np.clip(new_ov, 0, 100).astype(int)


# ── main ────────────────────────────────────────────────────────────────────

def main():
    print(f"[bayes] loading ledger ...", flush=True)
    df = pl.read_parquet(LEDGER)
    df = df.with_columns(pl.col('result').fill_null(0).cast(pl.Int8).alias('result'))
    print(f"[bayes] {len(df):,} rows | {df['date'].min()} to {df['date'].max()}", flush=True)

    # Pre-extract arrays for fast numpy ops
    ov_arr    = df['overall'].to_numpy().astype(int)
    stoch_arr = df['stoch'].to_numpy().astype(float)
    wadj_arr  = df['wadj'].to_numpy().astype(float)
    sig_arr   = df['signal_type'].to_numpy()
    result_arr = df['result'].to_numpy().astype(int)
    date_arr  = df['date'].to_numpy()   # strings

    # Baseline bucket stats (per window)
    print("[bayes] computing baseline ...", flush=True)
    baseline = {}
    for wlbl, cutoff in WINDOWS.items():
        mask_w = date_arr >= cutoff
        for tlbl, lo in CALL_TIERS:
            mask = mask_w & (ov_arr >= lo)
            n = int(mask.sum())
            tp = float(result_arr[mask].sum()) / n if n > 0 else float('nan')
            baseline[(tlbl, wlbl)] = (n, tp)
        for tlbl, hi in PUT_TIERS:
            mask = mask_w & (ov_arr <= hi)
            n = int(mask.sum())
            tp = float(result_arr[mask].sum()) / n if n > 0 else float('nan')
            baseline[(tlbl, wlbl)] = (n, tp)

    print("[bayes] baseline TP% (5y):")
    for tlbl, _ in CALL_TIERS + PUT_TIERS:
        n, tp = baseline[(tlbl, '5y')]
        print(f"  {tlbl:6s}  N={n:6d}  TP={tp*100:.2f}%")

    results = []
    t_start = time.perf_counter()
    total = len(STOCH_NEUTRALS) * len(WADJ_GATES) * len(KS)
    i = 0
    for stoch_neutral, wadj_gate, K in product(STOCH_NEUTRALS, WADJ_GATES, KS):
        i += 1
        new_ov = apply_dampener(ov_arr, stoch_arr, wadj_arr, sig_arr,
                                stoch_neutral, wadj_gate, K, LIFT_TARGET, SCORE_GATE)
        n_fired = int(((ov_arr >= SCORE_GATE) & (stoch_arr < stoch_neutral) & (wadj_arr >= 1) & (wadj_arr < wadj_gate) & (sig_arr == 'CALL')).sum())

        # Per (tier, window) stats
        stats = {}
        for wlbl, cutoff in WINDOWS.items():
            mask_w = date_arr >= cutoff
            for tlbl, lo in CALL_TIERS:
                mask = mask_w & (new_ov >= lo) & (ov_arr >= 70)  # call territory
                n = int(mask.sum())
                tp = float(result_arr[mask].sum()) / n if n > 0 else float('nan')
                stats[(tlbl, wlbl)] = (n, tp)
            for tlbl, hi in PUT_TIERS:
                mask = mask_w & (new_ov <= hi) & (ov_arr <= 30)  # put territory
                n = int(mask.sum())
                tp = float(result_arr[mask].sum()) / n if n > 0 else float('nan')
                stats[(tlbl, wlbl)] = (n, tp)

        # Compute gates
        # H1: >= +0.5pp on >= 3 call tiers at 5y; none regress > -1pp
        gains, regressions = [], []
        for tlbl, _ in CALL_TIERS:
            n_b, tp_b = baseline[(tlbl, '5y')]
            n_v, tp_v = stats[(tlbl, '5y')]
            if np.isnan(tp_b) or np.isnan(tp_v): continue
            dtp = (tp_v - tp_b) * 100
            if dtp >= H1_PASS_PP:
                gains.append(tlbl)
            if dtp < H1_MAX_REG:
                regressions.append((tlbl, dtp))
        h1 = len(gains) >= H1_MIN_TIERS and len(regressions) == 0

        # H3: N within +-15% for primary call tiers at 5y
        h3_fails = []
        for tlbl, _ in CALL_TIERS[2:]:  # 85+, 80+, 75+ (skip tiny 95+/90+)
            n_b = baseline[(tlbl, '5y')][0]
            n_v = stats[(tlbl, '5y')][0]
            if n_b > 0 and abs(n_v - n_b) / n_b > H3_TOL:
                h3_fails.append((tlbl, (n_v - n_b) / n_b))
        h3 = len(h3_fails) == 0

        # H4: puts neutral or better at 5y
        h4_fails = []
        for tlbl, hi in PUT_TIERS:
            n_b, tp_b = baseline[(tlbl, '5y')]
            n_v, tp_v = stats[(tlbl, '5y')]
            if not np.isnan(tp_b) and not np.isnan(tp_v):
                dtp = (tp_v - tp_b) * 100
                if dtp < -0.5:
                    h4_fails.append((tlbl, dtp))
        h4 = len(h4_fails) == 0

        # H5: sign-consistent across 1y/3y/5y for tiers with N >= H5_N_MIN in all windows
        h5_fails = []
        for tlbl, _ in CALL_TIERS[2:]:  # 85+, 80+, 75+ (skip tiny tiers)
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

        # Utility: weighted TP% gain at 5y
        utility = 0.0
        for tlbl, _ in CALL_TIERS[2:]:
            n_b, tp_b = baseline[(tlbl, '5y')]
            n_v, tp_v = stats[(tlbl, '5y')]
            if not np.isnan(tp_b) and not np.isnan(tp_v):
                utility += TIER_WEIGHTS.get(tlbl, 0) * (tp_v - tp_b) * 100

        dtp_75 = (stats[('75+', '5y')][1] - baseline[('75+', '5y')][1]) * 100
        dtp_80 = (stats[('80+', '5y')][1] - baseline[('80+', '5y')][1]) * 100
        dtp_85 = (stats[('85+', '5y')][1] - baseline[('85+', '5y')][1]) * 100
        dn_75  = (stats[('75+', '5y')][0] - baseline[('75+', '5y')][0]) / baseline[('75+', '5y')][0]
        dn_80  = (stats[('80+', '5y')][0] - baseline[('80+', '5y')][0]) / baseline[('80+', '5y')][0]
        dn_85  = (stats[('85+', '5y')][0] - baseline[('85+', '5y')][0]) / baseline[('85+', '5y')][0]

        results.append({
            'stoch_neutral': stoch_neutral, 'wadj_gate': wadj_gate, 'K': K,
            'n_fired': n_fired,
            'dtp_75': dtp_75, 'dtp_80': dtp_80, 'dtp_85': dtp_85,
            'dn_75': dn_75, 'dn_80': dn_80, 'dn_85': dn_85,
            'utility': utility,
            'h1': h1, 'h3': h3, 'h4': h4, 'h5': h5, 'all_pass': all_pass,
            'h1_gains': gains, 'h3_fails': h3_fails, 'h5_fails': h5_fails,
            'stats': stats,
        })

    elapsed = time.perf_counter() - t_start
    print(f"\n[bayes] {total} variants in {elapsed:.1f}s", flush=True)

    passing = [r for r in results if r['all_pass']]
    print(f"[bayes] {len(passing)}/{total} variants pass H1+H3+H4+H5(N>={H5_N_MIN})")

    print("\n" + "="*90)
    print(f"TOP PASSING VARIANTS (sorted by utility)")
    print("="*90)
    best = sorted(passing, key=lambda r: r['utility'], reverse=True)

    if not best:
        print("  No passing variants — analyzing best near-pass ...")
        # Show best H1-passing variants sorted by utility
        h1pass = [r for r in results if r['h1'] and r['h3'] and r['h4']]
        h1pass_s = sorted(h1pass, key=lambda r: r['utility'], reverse=True)
        print(f"  (H1+H3+H4 passing, H5 issue: {len(h1pass)})")
        for r in h1pass_s[:5]:
            print(f"  sg={r['stoch_gate']} wg={r['wadj_gate']} K={r['K']:.2f}  "
                  f"75+:{r['dtp_75']:+.2f}pp 80+:{r['dtp_80']:+.2f}pp 85+:{r['dtp_85']:+.2f}pp  "
                  f"N75:{r['dn_75']:+.1%} N80:{r['dn_80']:+.1%} N85:{r['dn_85']:+.1%}  "
                  f"H5fails={r['h5_fails']}  util={r['utility']:.4f}")
    else:
        for r in best[:10]:
            status = "SHIP" if r['all_pass'] else "MISS"
            print(f"  sn={r['stoch_neutral']:2d} wg={r['wadj_gate']:2d} K={r['K']:.2f}  "
                  f"75+:{r['dtp_75']:+.2f}pp 80+:{r['dtp_80']:+.2f}pp 85+:{r['dtp_85']:+.2f}pp  "
                  f"N75:{r['dn_75']:+.1%} N80:{r['dn_80']:+.1%} N85:{r['dn_85']:+.1%}  "
                  f"H1={r['h1']} H3={r['h3']} H4={r['h4']} H5={r['h5']}  "
                  f"util={r['utility']:.4f}  [{status}]")

    # Detailed view of #1 best
    if best:
        r = best[0]
        print(f"\n{'='*90}")
        print(f"RECOMMENDED SHIP CANDIDATE: stoch_neutral={r['stoch_neutral']} wg={r['wadj_gate']} K={r['K']:.2f}")
        print(f"  n_fired (5y)={r['n_fired']}")
        print(f"\n  Per-tier per-window TP% delta:")
        print(f"  {'tier':6s}  {'5y base':>8s}  {'5y var':>7s}  {'5y delta':>8s}  {'3y delta':>8s}  {'1y delta':>8s}  N5y  ΔN%")
        for tlbl, lo in CALL_TIERS + PUT_TIERS:
            n_b, tp_b = baseline[(tlbl, '5y')]
            n_v, tp_v = r['stats'][(tlbl, '5y')]
            n_b3, tp_b3 = baseline[(tlbl, '3y')]
            n_v3, tp_v3 = r['stats'][(tlbl, '3y')]
            n_b1, tp_b1 = baseline[(tlbl, '1y')]
            n_v1, tp_v1 = r['stats'][(tlbl, '1y')]
            dtp5 = (tp_v - tp_b) * 100 if not (np.isnan(tp_b) or np.isnan(tp_v)) else float('nan')
            dtp3 = (tp_v3 - tp_b3) * 100 if not (np.isnan(tp_b3) or np.isnan(tp_v3)) else float('nan')
            dtp1 = (tp_v1 - tp_b1) * 100 if not (np.isnan(tp_b1) or np.isnan(tp_v1)) else float('nan')
            dn5 = (n_v - n_b) / n_b if n_b > 0 else 0.0
            print(f"  {tlbl:6s}  {tp_b*100:>7.2f}%  {tp_v*100:>6.2f}%  {dtp5:>+7.2f}pp  {dtp3:>+7.2f}pp  {dtp1:>+7.2f}pp  {n_v:>4d}  {dn5:>+.1%}")

        print(f"\n  Dampener formula (GRADIENT — no hard stoch gate):")
        sn = r['stoch_neutral']; wg = r['wadj_gate']; K = r['K']
        print(f"    Condition: overall >= {SCORE_GATE} AND stoch < {sn} AND 1 <= wadj < {wg} AND signal==CALL")
        print(f"    stoch_weakness = clip(({sn} - stoch) / {sn}, 0, 1)  # ramp from {sn}->0 to 0->1")
        print(f"    wadj_weakness  = clip(({wg} - max(1,wadj)) / {wg-1}, 0, 1)")
        print(f"    weakness       = stoch_weakness * wadj_weakness")
        print(f"    overall       -= {K} * weakness * (overall - {LIFT_TARGET})")
        print()

        print(f"  Example signal impacts (K={K}, stoch_neutral={sn}):")
        for (stoch_ex, wadj_ex, ov_ex) in [(5, 5, 80), (15, 5, 80), (25, 5, 80), (30, 5, 78), (5, 2, 85), (5, 12, 78)]:
            sw = max(0, min(1, (sn - stoch_ex) / float(sn)))
            ww = max(0, min(1, (wg - max(1.0, wadj_ex)) / float(wg - 1)))
            w = sw * ww
            if stoch_ex < sn and wadj_ex >= 1 and wadj_ex < wg:
                new_ov = round(ov_ex - K * w * (ov_ex - LIFT_TARGET))
                print(f"    stoch={stoch_ex:2d} wadj={wadj_ex:2d} overall={ov_ex} -> {new_ov:2d} (drop={ov_ex-new_ov:2d}, weakness={w:.3f})")
            else:
                print(f"    stoch={stoch_ex:2d} wadj={wadj_ex:2d} overall={ov_ex} -> {ov_ex:2d} (no fire: {'stoch' if stoch_ex>=sn else 'wadj'})")


if __name__ == '__main__':
    main()
