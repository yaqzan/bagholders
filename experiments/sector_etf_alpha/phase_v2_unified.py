"""Unified LHS + random-drill sweep on SWPM v2 (SWPM_SEC + RSU stacked).

Stage 1: LHS 240 variants over 24-dim parameter space (both mechanisms enabled by default).
Stage 2: Random drill 400 in basin around top-5 means.
Stage 3: Random drill 200 tight basin around stage-2 winner.

Also tests three architectural variants:
  A. SWPM_SEC only      — sector phase mechanism alone
  B. RSU only           — relative-strength U-curve alone
  C. Both stacked       — full SWPM v2

For each architecture, finds best params and reports per-tier WR7 deltas.
"""
from __future__ import annotations
import io, sys, json, time
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.sector_etf_alpha.swpm_v2 import SWPMv2Params, evaluate_v2

LOCAL = ROOT / '.cache' / 'sector_etf_alpha'
COH_PATH = LOCAL / 'cohort_v46_1825.parquet'
OUT_DIR = ROOT / 'experiments' / 'sector_etf_alpha'

# Continuous search space
SPACE = {
    # SWPM_SEC mechanism
    'EMA_K':           (3.0, 12.0),
    'RSI_K':           (15.0, 35.0),
    'MACD_TANH_K':     (0.2, 1.5),
    'EMA_W':           (0.0, 1.0),
    'RSI_W':           (0.0, 1.0),
    'MACD_W':          (0.0, 1.0),
    'PHASE_POWER':     (1.0, 3.0),
    'SCORE_POWER':     (1.0, 2.5),
    'ALPHA_DOWN':      (0.20, 0.95),
    'TARGET_DOWN':     (55.0, 70.0),
    'ALPHA_UP':        (0.20, 0.95),
    'TARGET_UP':       (80.0, 92.0),
    'ALPHA_PUT_DOWN':  (0.20, 0.95),
    'TARGET_PUT':      (28.0, 38.0),
    'ALPHA_PUT_UP':    (0.0, 0.5),
    'TARGET_PUT_UP':   (8.0, 16.0),
    # RSU mechanism
    'RS_GATE':         (0.02, 0.08),
    'RS_K':            (0.04, 0.20),
    'RS_POWER':        (1.0, 2.5),
    'RS_SCORE_POWER':  (1.0, 2.5),
    'RS_ALPHA_C':      (0.20, 0.95),
    'RS_TARGET_C':     (55.0, 70.0),
    'RS_ALPHA_P':      (0.0, 0.7),
    'RS_TARGET_P':     (28.0, 38.0),
}


def lhs_sample(n_samples: int, n_dims: int, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros((n_samples, n_dims))
    for j in range(n_dims):
        cuts = (np.arange(n_samples) + rng.uniform(0, 1, n_samples)) / n_samples
        rng.shuffle(cuts)
        out[:, j] = cuts
    return out


def sample_to_kw(s, dim_names):
    out = {}
    for v, name in zip(s, dim_names):
        lo, hi = SPACE[name]
        out[name] = float(lo + v * (hi - lo))
    return out


def random_basin(anchor: dict, frac: float, n: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(n):
        s = {}
        for k, v in anchor.items():
            if k not in SPACE:
                continue
            lo_h, hi_h = SPACE[k]
            span = max((hi_h - lo_h) * frac * 0.5, abs(v) * frac, 0.5)
            lo = max(lo_h, v - span)
            hi = min(hi_h, v + span)
            s[k] = float(rng.uniform(lo, hi))
        samples.append(s)
    return samples


def kw_to_params(kw: dict, swpm_on: bool, rsu_on: bool) -> SWPMv2Params:
    full = {**kw,
            'SWPM_ENABLED': swpm_on, 'RSU_ENABLED': rsu_on,
            'GATE_LO_C': 70, 'GATE_HI_C': 84,
            'GATE_LO_P': 16, 'GATE_HI_P': 25}
    return SWPMv2Params(**full)


def eval_variant(coh, kw, swpm_on, rsu_on):
    params = kw_to_params(kw, swpm_on, rsu_on)
    m = evaluate_v2(coh, params)
    row = {**kw, 'util': m['util'],
           'W4_breaches': m['W4_breaches'], 'W5_breaches': m['W5_breaches'],
           'W6_breaches': m['W6_breaches'], 'n_changed': m['n_changed'],
           'affected_call_n': m['affected_call_n'], 'affected_put_n': m['affected_put_n'],
           'swpm_on': swpm_on, 'rsu_on': rsu_on}
    for t in m['tiers_call']:
        row[f'call_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
        row[f'call_{t["tier"]}_dN%'] = t['n_delta_pct']
        row[f'call_{t["tier"]}_n_new'] = t['n_new']
        row[f'call_{t["tier"]}_dWR1'] = t['wr1_delta_pp']
        row[f'call_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
        row[f'call_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
    for t in m['tiers_put']:
        row[f'put_{t["tier"]}_dWR7'] = t['wr7_delta_pp']
        row[f'put_{t["tier"]}_dN%'] = t['n_delta_pct']
        row[f'put_{t["tier"]}_n_new'] = t['n_new']
        row[f'put_{t["tier"]}_dWR1'] = t['wr1_delta_pp']
        row[f'put_{t["tier"]}_dWR15'] = t['wr15_delta_pp']
        row[f'put_{t["tier"]}_dWR30'] = t['wr30_delta_pp']
    return row


def run_arch(coh, name, swpm_on, rsu_on, n_lhs=240, n_drill=400, n_tight=200):
    """Run staged LHS+drill for a specific architecture."""
    print(f'\n{"="*100}\n[{name}] swpm={swpm_on} rsu={rsu_on}  LHS={n_lhs} drill={n_drill} tight={n_tight}\n{"="*100}', flush=True)

    dim_names = list(SPACE.keys())
    samples = lhs_sample(n_lhs, len(dim_names), seed=hash(name) % 100000)

    rows = []
    t0 = time.time()
    for i, s in enumerate(samples):
        kw = sample_to_kw(s, dim_names)
        rows.append(eval_variant(coh, kw, swpm_on, rsu_on))
        if (i+1) % 40 == 0:
            print(f'  [LHS] {i+1}/{n_lhs} elapsed={time.time()-t0:.0f}s', flush=True)
    print(f'  [LHS] done in {time.time()-t0:.0f}s')
    df_lhs = pl.DataFrame(rows).sort('util', descending=True)

    # Top-5 mean as drill anchor
    top5 = df_lhs.head(5)
    anchor = {k: top5[k].mean() for k in dim_names if k in df_lhs.columns}
    print(f'  [drill] anchor (top-5 mean): util_max={df_lhs["util"].max():.2f}, util_top5_mean={top5["util"].mean():.2f}')
    drill_samples = random_basin(anchor, frac=0.30, n=n_drill, seed=hash(name+'drill') % 100000)
    drill_rows = []
    t0 = time.time()
    for i, kw in enumerate(drill_samples):
        drill_rows.append(eval_variant(coh, kw, swpm_on, rsu_on))
        if (i+1) % 80 == 0:
            print(f'  [drill] {i+1}/{n_drill} elapsed={time.time()-t0:.0f}s', flush=True)
    print(f'  [drill] done in {time.time()-t0:.0f}s')
    df_drill = pl.DataFrame(drill_rows).sort('util', descending=True)

    # Tight drill around drill winner
    drill_top = df_drill.head(1).to_dicts()[0]
    tight_anchor = {k: drill_top[k] for k in dim_names if k in df_drill.columns}
    tight_samples = random_basin(tight_anchor, frac=0.10, n=n_tight, seed=hash(name+'tight') % 100000)
    tight_rows = []
    t0 = time.time()
    for i, kw in enumerate(tight_samples):
        tight_rows.append(eval_variant(coh, kw, swpm_on, rsu_on))
        if (i+1) % 80 == 0:
            print(f'  [tight] {i+1}/{n_tight} elapsed={time.time()-t0:.0f}s', flush=True)
    print(f'  [tight] done in {time.time()-t0:.0f}s')
    df_tight = pl.DataFrame(tight_rows).sort('util', descending=True)

    # Combine all and pick best
    all_df = pl.concat([df_lhs, df_drill, df_tight], how='diagonal_relaxed').sort('util', descending=True)
    all_df.write_csv(OUT_DIR / f'phase_v2_{name}_all.csv')
    df_lhs.write_csv(OUT_DIR / f'phase_v2_{name}_lhs.csv')
    df_drill.write_csv(OUT_DIR / f'phase_v2_{name}_drill.csv')
    df_tight.write_csv(OUT_DIR / f'phase_v2_{name}_tight.csv')

    return all_df


def print_top(name, df):
    cols = ['util', 'W4_breaches', 'W5_breaches', 'W6_breaches',
            'call_70+_dWR7', 'call_75+_dWR7', 'call_80+_dWR7', 'call_85+_dWR7',
            'call_90+_dWR7', 'call_95+_dWR7', 'put_<=15_dWR7', 'put_<=20_dWR7', 'put_<=25_dWR7']
    avail = [c for c in cols if c in df.columns]
    print(f'\n[{name}] TOP 5:')
    print(df.select(avail).head(5))


def print_winner(name, df):
    top = df.head(1).to_dicts()[0]
    print(f'\n=== [{name}] WINNER util={top["util"]:+.3f} ===')
    print(f'  W4 breaches: {top["W4_breaches"]}')
    print(f'  per-tier WR7 deltas:')
    for tier in ['95+', '90+', '85+', '80+', '75+', '70+']:
        wr7 = top.get(f'call_{tier}_dWR7', 0)
        wr1 = top.get(f'call_{tier}_dWR1', 0)
        wr15 = top.get(f'call_{tier}_dWR15', 0)
        wr30 = top.get(f'call_{tier}_dWR30', 0)
        n_new = top.get(f'call_{tier}_n_new', 0)
        n_pct = top.get(f'call_{tier}_dN%', 0)
        print(f'    call {tier:<5} N={n_new:>5,d} ({n_pct:+5.1f}%)  WR1={wr1:+5.2f} WR7={wr7:+5.2f} WR15={wr15:+5.2f} WR30={wr30:+5.2f}')
    for tier in ['<=25', '<=20', '<=15', '<=10', '<=5']:
        wr7 = top.get(f'put_{tier}_dWR7', 0)
        wr15 = top.get(f'put_{tier}_dWR15', 0)
        wr30 = top.get(f'put_{tier}_dWR30', 0)
        n_new = top.get(f'put_{tier}_n_new', 0)
        n_pct = top.get(f'put_{tier}_dN%', 0)
        print(f'    put  {tier:<5} N={n_new:>5,d} ({n_pct:+5.1f}%)  WR7={wr7:+5.2f} WR15={wr15:+5.2f} WR30={wr30:+5.2f}')


def main():
    coh = pl.read_parquet(COH_PATH)
    print(f'[load] {len(coh):,} rows')

    architectures = [
        ('A_SWPM_only', True, False),
        ('B_RSU_only',  False, True),
        ('C_stacked',   True, True),
    ]

    winners = {}
    for name, swpm_on, rsu_on in architectures:
        df = run_arch(coh, name, swpm_on, rsu_on, n_lhs=200, n_drill=300, n_tight=200)
        print_top(name, df)
        print_winner(name, df)
        top = df.head(1).to_dicts()[0]
        winners[name] = top

    # Save final winners
    with open(OUT_DIR / 'phase_v2_winners.json', 'w') as f:
        out = {}
        for name, w in winners.items():
            # Strip non-serializable
            ser = {k: (v if not isinstance(v, (np.floating, np.integer)) else float(v))
                   for k, v in w.items()}
            out[name] = ser
        json.dump(out, f, indent=2, default=str)
    print(f'\n[save] phase_v2_winners.json')

    print('\n\n========== ARCHITECTURE COMPARISON ==========')
    for name, _, _ in architectures:
        w = winners[name]
        print(f'{name}: util={w["util"]:+6.2f}  W4={w["W4_breaches"]} W5={w["W5_breaches"]} W6={w["W6_breaches"]}')


if __name__ == '__main__':
    main()
