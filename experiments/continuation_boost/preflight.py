"""Continuation-boost pre-flight — three checks before designing the sweep.

#1  Lookahead audit: confirm `prior_date + W <= signal_date` filter is required
    (cache holds future-knowable wins; naive lookup leaks). Quantify exposure.

#2  Already-encoded check: corr(prior_win_present, current_score) on 5y v32.
    If high, the boost double-counts what scoring already encodes.

#3  MFE_sigma_p25 coverage per (bucket, period) — confirms the dampener anchor
    can be looked up reliably.

Output: text report under experiments/continuation_boost/preflight_report.txt
"""
from __future__ import annotations
import io, sys, sqlite3, time
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict
from statistics import mean, pstdev

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import polars as pl
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CACHE_DB = ROOT / '.cache' / 'barrier_outcomes.db'
COMPONENTS_PQ = ROOT / '.cache' / 'miss_ledger' / 'components_v32_1825.parquet'
OUT_DIR = Path(__file__).parent
OUT_TXT = OUT_DIR / 'preflight_report.txt'

# Boost-design knobs (match the proposed mechanism)
LOOKBACK_MAX_DAYS = 60   # outer window for prior-peak scan
LOOKBACK_MIN_DAYS = 7    # inner window (= min W for which we can read win_W)
PRIOR_PEAK_GATE   = 70   # only count priors of this absolute conviction (|score-50| >= 20)
CALL_GATE         = 75   # only score current calls at >=75 (sweep can lift 60-69 later)
PUT_GATE          = 25   # mirror

BARRIER_SET = '30dte_generic'  # matches historic_peaks definition

# ---------------------------------------------------------------------------

class Tee:
    def __init__(self, *streams): self.streams = streams
    def write(self, s):
        for x in self.streams: x.write(s)
    def flush(self):
        for x in self.streams: x.flush()


def load_components() -> pl.DataFrame:
    if not COMPONENTS_PQ.exists():
        raise SystemExit(f"missing {COMPONENTS_PQ} — run miss_ledger build first")
    df = pl.read_parquet(COMPONENTS_PQ).select(['symbol', 'date', 'overall'])
    df = df.with_columns(pl.col('date').str.to_date())
    print(f'[load] components: {len(df):,} rows  range {df["date"].min()} -> {df["date"].max()}')
    return df


def load_barriers_bulk(w_days_list: list) -> dict:
    """Bulk load all (symbol, date, side) -> result for given w_days values.

    Loads the entire 30dte_generic slice for chosen w_days into memory once
    (much faster than row-at-a-time IN-clause queries on SQLite).
    """
    out = {w: {} for w in w_days_list}
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    placeholders = ','.join(['?'] * len(w_days_list))
    sql = f"""
    SELECT symbol, date, side, w_days, result
    FROM barrier_outcomes
    WHERE barrier_set = ? AND w_days IN ({placeholders})
    """
    t0 = time.time()
    cur.execute(sql, [BARRIER_SET] + w_days_list)
    for sym, d_str, side, w, result in cur.fetchall():
        d = date.fromisoformat(d_str)
        out[w][(sym, d, side)] = result
    conn.close()
    print(f'[barriers] bulk-loaded {sum(len(d) for d in out.values()):,} rows in {time.time()-t0:.1f}s')
    return out


# ---------------------------------------------------------------------------
# Pre-flight #3 — MFE coverage  (lightweight, do first)
# ---------------------------------------------------------------------------

def preflight_3_mfe_coverage():
    print('\n' + '=' * 78)
    print('PRE-FLIGHT #3 — MFE_sigma_p25 coverage by bucket × period')
    print('=' * 78)
    from database.models.core import (
        AlgorithmVersion, ScoreAssessmentRun, ScoreAssessmentResult
    )
    av = AlgorithmVersion.get_active_scores_version()
    print(f'active version: id={av.id} commit={av.git_commit}')

    # Find 5y run for active version
    runs = (ScoreAssessmentRun
            .select()
            .where((ScoreAssessmentRun.version == av) &
                   (ScoreAssessmentRun.lookback_days == 1825))
            .order_by(ScoreAssessmentRun.id.desc()))
    runs = list(runs)
    if not runs:
        print('  NO 5y run found for active version — run trader assess --force first')
        return False
    # Pick most recent run with dte_strategy=='30' (or first if absent)
    run = None
    for r in runs:
        if getattr(r, 'dte_strategy', '30') == '30':
            run = r
            break
    if run is None:
        run = runs[0]
    print(f'  using run id={run.id} dte={getattr(run,"dte_strategy","30")} '
          f'lookback={run.lookback_days}d')

    rows = list(ScoreAssessmentResult.select().where(ScoreAssessmentResult.run == run))
    if not rows:
        print('  run has no results rows!')
        return False

    print(f'\n  bucket coverage ({len(rows)} rows total):')
    print(f'  {"bucket":<10} {"N":>7} '
          f'{"p25_7d":>8} {"p25_15d":>8} {"p25_30d":>8} {"p25_60d":>8}')
    print(f'  {"-"*10} {"-"*7} {"-"*8} {"-"*8} {"-"*8} {"-"*8}')
    ok = True
    for r in rows:
        if r.sample_count < 50:
            mark = ' ⚠'
        else:
            mark = ''
        gaps = []
        for fld in ('mfe_sigma_p25_7d', 'mfe_sigma_p25_15d',
                    'mfe_sigma_p25_30d', 'mfe_sigma_p25_60d'):
            if getattr(r, fld) is None:
                gaps.append(fld)
        gap_mark = f' MISSING={gaps}' if gaps else ''
        if gaps:
            ok = False
        print(f'  {r.bucket:<10} {r.sample_count:>7} '
              f'{r.mfe_sigma_p25_7d if r.mfe_sigma_p25_7d is not None else "-":>8} '
              f'{r.mfe_sigma_p25_15d if r.mfe_sigma_p25_15d is not None else "-":>8} '
              f'{r.mfe_sigma_p25_30d if r.mfe_sigma_p25_30d is not None else "-":>8} '
              f'{r.mfe_sigma_p25_60d if r.mfe_sigma_p25_60d is not None else "-":>8}'
              f'{mark}{gap_mark}')
    print(f'\n  verdict: {"PASS — all buckets have non-null MFE_sigma_p25 across periods" if ok else "FAIL — gaps found"}')
    return ok


# ---------------------------------------------------------------------------
# Pre-flight #1 + #2 share the same scan — combine
# ---------------------------------------------------------------------------

def preflight_1_and_2(comp: pl.DataFrame):
    print('\n' + '=' * 78)
    print('PRE-FLIGHT #1 + #2 — lookahead audit + already-encoded check')
    print('=' * 78)

    # Sample current peaks: calls >=75, puts <=25, drop earliest LOOKBACK_MAX days
    # of the dataset (they have no priors to look back to).
    earliest = comp['date'].min()
    cutoff_lo = earliest + timedelta(days=LOOKBACK_MAX_DAYS + 5)
    sig = comp.filter(
        ((pl.col('overall') >= CALL_GATE) | (pl.col('overall') <= PUT_GATE)) &
        (pl.col('date') >= cutoff_lo)
    ).sort(['symbol', 'date'])
    print(f'[scan] sampling {len(sig):,} call/put peaks after {cutoff_lo}')

    # For each peak, find prior peaks of same side in [D-LBMAX, D-LBMIN] for same symbol.
    # Also collect non-peak ALL scores (any |score-50|>=PRIOR_PEAK_GATE-50) as candidates.
    # Build per-symbol date->score lookup.
    by_sym = defaultdict(list)  # sym -> list of (date, overall)
    for r in comp.iter_rows(named=True):
        by_sym[r['symbol']].append((r['date'], r['overall']))
    for s in by_sym:
        by_sym[s].sort()
    print(f'[scan] indexed {len(by_sym):,} symbols')

    # Walk peaks and build prior list. Side encoding: 'call' for >=50 current, 'put' for <50.
    # Prior must match side.
    prior_lookups = []  # for barrier batch lookup: (symbol, prior_date, side)
    peak_rows = []  # (idx, symbol, date, overall, side, prior_list)

    for r in sig.iter_rows(named=True):
        sym, d, ov = r['symbol'], r['date'], r['overall']
        side_now = 'call' if ov >= 50 else 'put'
        d_lo = d - timedelta(days=LOOKBACK_MAX_DAYS)
        d_hi = d - timedelta(days=LOOKBACK_MIN_DAYS)
        priors = []
        for pd_, pov in by_sym[sym]:
            if pd_ > d_hi: break
            if pd_ < d_lo: continue
            if (side_now == 'call' and pov < PRIOR_PEAK_GATE): continue
            if (side_now == 'put'  and pov > 100 - PRIOR_PEAK_GATE): continue
            priors.append((pd_, pov))
        peak_rows.append((sym, d, ov, side_now, priors))
        for pd_, _ in priors:
            side_str = 'low' if side_now == 'call' else 'high'
            prior_lookups.append((sym, pd_, side_str))

    n_peaks = len(peak_rows)
    n_with_priors = sum(1 for r in peak_rows if r[4])
    n_total_priors = sum(len(r[4]) for r in peak_rows)
    print(f'[scan] {n_peaks:,} peaks scanned')
    print(f'[scan] {n_with_priors:,} ({100*n_with_priors/n_peaks:.1f}%) have ≥1 prior in [D-{LOOKBACK_MAX_DAYS}, D-{LOOKBACK_MIN_DAYS}]')
    print(f'[scan] {n_total_priors:,} total prior-peak references (avg {n_total_priors/max(1,n_peaks):.2f}/peak)')

    # ---- bulk barrier lookup (w=7 and w=15 in one pass) ----
    print(f'[barriers] need {len(set((s,d) for s,d,_ in prior_lookups)):,} unique (sym,date) lookups')
    barriers = load_barriers_bulk([7, 15])
    win7 = barriers[7]
    win15 = barriers[15]
    print(f'[barriers] w7 dict size={len(win7):,}  w15 dict size={len(win15):,}')

    # ---- PRE-FLIGHT #1: lookahead exposure ----
    print('\n--- PRE-FLIGHT #1 RESULT ---')
    naive_w15_used = 0   # any prior with non-null win_15d (regardless of date gap)
    knowable_w15   = 0   # only when prior_date + 15 <= current_date
    leaky          = 0   # naive minus knowable

    naive_w7_used = 0
    knowable_w7   = 0
    leaky_w7      = 0

    for sym, d, ov, side_now, priors in peak_rows:
        side_str = 'low' if side_now == 'call' else 'high'
        for pd_, pov in priors:
            gap = (d - pd_).days
            r7  = win7.get((sym, pd_, side_str))
            r15 = win15.get((sym, pd_, side_str))
            if r7 is not None:
                naive_w7_used += 1
                if gap >= 7:
                    knowable_w7 += 1
                else:
                    leaky_w7 += 1
            if r15 is not None:
                naive_w15_used += 1
                if gap >= 15:
                    knowable_w15 += 1
                else:
                    leaky_w15 = leaky_w15 + 1 if False else (naive_w15_used - knowable_w15)

    leaky_w15 = naive_w15_used - knowable_w15
    leaky_w7  = naive_w7_used  - knowable_w7
    print(f'  win_7d : naive={naive_w7_used:,}  knowable={knowable_w7:,}  '
          f'leaky={leaky_w7:,} ({100*leaky_w7/max(1,naive_w7_used):.2f}%)')
    print(f'  win_15d: naive={naive_w15_used:,}  knowable={knowable_w15:,}  '
          f'leaky={leaky_w15:,} ({100*leaky_w15/max(1,naive_w15_used):.2f}%)')
    print()
    if leaky_w7 == 0 and leaky_w15 == 0:
        print('  ✓ no lookahead exposure with LBMIN={} (gap >= max_W on every prior).'.format(LOOKBACK_MIN_DAYS))
    else:
        print(f'  ⚠ lookahead would leak {leaky_w7+leaky_w15:,} prior-win observations '
              f'if cache read without per-prior gap filter.')
        print('  → boost code MUST enforce: consume win_W only when (signal_date - prior_date) >= W.')

    # ---- PRE-FLIGHT #2: already-encoded check ----
    print('\n--- PRE-FLIGHT #2 RESULT ---')

    # For each current peak, compute prior_quality = max(prior_score-50)/50 across priors,
    # and prior_winner_present = any prior (knowable) win_15d==1 OR win_7d==1
    # Compare current_score distribution split by prior_winner_present.
    score_with    = []   # current overall (call: distance above 50; put: distance below 50)
    score_without = []
    score_call_with    = []
    score_call_without = []
    score_put_with     = []
    score_put_without  = []

    n_call_total = n_put_total = 0
    n_call_with  = n_put_with  = 0

    for sym, d, ov, side_now, priors in peak_rows:
        side_str = 'low' if side_now == 'call' else 'high'
        present = False
        for pd_, pov in priors:
            gap = (d - pd_).days
            if gap >= 7:
                r7 = win7.get((sym, pd_, side_str))
                if r7 == 1: present = True; break
            if gap >= 15:
                r15 = win15.get((sym, pd_, side_str))
                if r15 == 1: present = True; break

        # Conviction = signed distance from 50 (positive = direction of trade)
        conv = (ov - 50) if side_now == 'call' else (50 - ov)

        if side_now == 'call':
            n_call_total += 1
            if present:
                n_call_with += 1
                score_call_with.append(conv)
                score_with.append(conv)
            else:
                score_call_without.append(conv)
                score_without.append(conv)
        else:
            n_put_total += 1
            if present:
                n_put_with += 1
                score_put_with.append(conv)
                score_with.append(conv)
            else:
                score_put_without.append(conv)
                score_without.append(conv)

    def stat_line(label, xs):
        if not xs: return f'  {label:<28} N={0}'
        return (f'  {label:<28} N={len(xs):>6}  mean={mean(xs):>5.2f}  '
                f'sd={pstdev(xs):>5.2f}  p50={sorted(xs)[len(xs)//2]:>4.1f}')

    print(f'  call peaks (≥{CALL_GATE})  with prior winner: {n_call_with:,} / {n_call_total:,}  ({100*n_call_with/max(1,n_call_total):.1f}%)')
    print(f'  put  peaks (≤{PUT_GATE})   with prior winner: {n_put_with:,} / {n_put_total:,}  ({100*n_put_with/max(1,n_put_total):.1f}%)')
    print(stat_line('CALL conv | prior winner', score_call_with))
    print(stat_line('CALL conv | no prior win', score_call_without))
    print(stat_line('PUT  conv | prior winner', score_put_with))
    print(stat_line('PUT  conv | no prior win', score_put_without))

    # Summary deltas
    if score_call_with and score_call_without:
        d_call = mean(score_call_with) - mean(score_call_without)
        print(f'\n  CALL Δ(mean conviction) = {d_call:+.2f} pts  '
              f'(positive = priors-with cohort scores HIGHER on average → encoded)')
    if score_put_with and score_put_without:
        d_put = mean(score_put_with) - mean(score_put_without)
        print(f'  PUT  Δ(mean conviction) = {d_put:+.2f} pts')

    # Population check
    print(f'\n  population (calls with ≥1 prior winner): {100*n_call_with/max(1,n_call_total):.1f}%')
    print(f'  population (puts  with ≥1 prior winner): {100*n_put_with/max(1,n_put_total):.1f}%')

    # Quality of prior winners — what fraction TPed?
    n_w7_one = sum(1 for v in win7.values() if v == 1)
    n_w7_zero = sum(1 for v in win7.values() if v == 0)
    n_w7_null = sum(1 for v in win7.values() if v is None)
    n_w15_one = sum(1 for v in win15.values() if v == 1)
    n_w15_zero = sum(1 for v in win15.values() if v == 0)
    n_w15_null = sum(1 for v in win15.values() if v is None)
    print(f'\n  prior peak barrier outcomes (BARRIER_SET={BARRIER_SET}):')
    print(f'    win_7d : 1={n_w7_one:,}  0={n_w7_zero:,}  null={n_w7_null:,}  '
          f'(WR={100*n_w7_one/max(1,n_w7_one+n_w7_zero):.1f}%)')
    print(f'    win_15d: 1={n_w15_one:,}  0={n_w15_zero:,}  null={n_w15_null:,}  '
          f'(WR={100*n_w15_one/max(1,n_w15_one+n_w15_zero):.1f}%)')

    return {
        'n_peaks': n_peaks,
        'n_with_priors': n_with_priors,
        'leaky_w7': leaky_w7,
        'leaky_w15': leaky_w15,
        'pop_call_pct': 100 * n_call_with / max(1, n_call_total),
        'pop_put_pct':  100 * n_put_with  / max(1, n_put_total),
        'delta_conv_call': (mean(score_call_with) - mean(score_call_without))
                           if score_call_with and score_call_without else None,
        'delta_conv_put':  (mean(score_put_with)  - mean(score_put_without))
                           if score_put_with and score_put_without else None,
    }


def main():
    OUT_DIR.mkdir(exist_ok=True)
    f = open(OUT_TXT, 'w', encoding='utf-8')
    sys.stdout = Tee(sys.stdout, f)

    print(f'Continuation-boost pre-flight  ({date.today().isoformat()})')
    print(f'  LBMIN={LOOKBACK_MIN_DAYS}  LBMAX={LOOKBACK_MAX_DAYS}  '
          f'PRIOR_GATE={PRIOR_PEAK_GATE}  CALL_GATE={CALL_GATE}  PUT_GATE={PUT_GATE}')
    print(f'  barrier_set={BARRIER_SET}')

    p3_ok = preflight_3_mfe_coverage()
    comp = load_components()
    p12 = preflight_1_and_2(comp)

    print('\n' + '=' * 78)
    print('GO / NO-GO SUMMARY')
    print('=' * 78)
    go = True
    if not p3_ok:
        print('  ❌ #3 FAIL — MFE_sigma_p25 has gaps; mfe-room dampener cannot anchor reliably')
        go = False
    else:
        print('  ✓ #3 PASS — MFE_sigma_p25 coverage clean across buckets/periods')

    if p12['leaky_w7'] > 0 or p12['leaky_w15'] > 0:
        print('  ⚠ #1 NOTE — naive cache lookup leaks; boost MUST enforce gap >= W per prior')
    else:
        print('  ✓ #1 PASS — no leakage at chosen LBMIN')

    pop_pct = (p12['pop_call_pct'] + p12['pop_put_pct']) / 2
    if pop_pct < 30:
        print(f'  ⚠ #2 POP — only {pop_pct:.1f}% of peaks have prior winners — boost touches minority')
    else:
        print(f'  ✓ #2 POP — {pop_pct:.1f}% of peaks have prior winners — rich population')

    dc = p12['delta_conv_call']
    if dc is not None and abs(dc) > 3:
        print(f'  ⚠ #2 ENCODED — call Δconv {dc:+.2f}pp suggests prior-win is partially encoded')
        print('         → mfe-room dampener handles this, but expect smaller per-trade lift')
    else:
        print(f'  ✓ #2 INDEPENDENT — call Δconv {dc:+.2f}pp small; prior-win signal is independent of current score')

    print(f'\n  RECOMMENDATION: {"PROCEED to sweep harness" if go else "FIX coverage gaps before sweep"}')


if __name__ == '__main__':
    main()
