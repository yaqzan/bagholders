"""Overlap analysis — does winning at MULTIPLE windows produce stronger
continuation than winning at just one?

For each prior peak in 5y v32, classify by win_pattern = (W7, W15, W30, W60)
binary tuple. Then for the NEXT signal of the same symbol that fires within
LBMAX days, measure its 30dte_opt w=15d barrier outcome (TP%).

Output: 16 patterns × call/put × TP% × N table.

If "robust winner" patterns (e.g., 1,1,1,1) show meaningfully higher next-signal
TP% than "single-window winner" patterns, the boost formula should reward overlap
explicitly. If patterns are flat, additive sum across windows is sufficient.
"""
from __future__ import annotations
import io, sys, sqlite3, time
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

CACHE_DB        = ROOT / '.cache' / 'barrier_outcomes.db'
COMPONENTS_PQ   = ROOT / '.cache' / 'miss_ledger' / 'components_v32_1825.parquet'
BARRIERS_30OPT  = ROOT / '.cache' / 'runner' / 'barriers_30opt_w15_1825.parquet'

OUT_DIR = Path(__file__).parent
REPORT  = OUT_DIR / 'overlap_analysis.txt'

LOOKBACK_MAX = 60   # next-signal must occur within 60 calendar days of prior
LOOKBACK_MIN = 15   # next-signal must be at least 15 cal days after prior (knowability)
PRIOR_GATE   = 70   # prior must be a peak (calls 70+) or (puts <=29)
PUT_PRIOR_GATE = 29
NEXT_GATE_CALL  = 70   # next signal must qualify (calls 70+ for evaluation)
NEXT_GATE_PUT   = 25


def load_30dte_generic_wins() -> dict:
    """Load all 30dte_generic wins for w=7,15,30,60 from SQLite (cached pickle)."""
    import pickle
    cache_pkl = ROOT / '.cache' / 'continuation_boost' / 'wins_30dgen_w7_15_30_60.pkl'
    if cache_pkl.exists():
        print(f'[load] wins from pickle cache: {cache_pkl.name}', flush=True)
        t0 = time.time()
        with open(cache_pkl, 'rb') as f:
            out = pickle.load(f)
        print(f'[load]   {len(out):,} entries in {time.time()-t0:.1f}s', flush=True)
        return out

    print(f'[load] 30dte_generic w=7,15,30,60 from SQLite ...', flush=True)
    t0 = time.time()
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, date, side, w_days, result, entry_close
        FROM barrier_outcomes
        WHERE barrier_set = '30dte_generic' AND w_days IN (7, 15, 30, 60)
    """)
    rows = cur.fetchall()
    conn.close()
    print(f'[load]   {len(rows):,} rows in {time.time()-t0:.1f}s', flush=True)
    out = {}
    for sym, d_str, side, w, result, entry in rows:
        d = date.fromisoformat(d_str)
        out[(sym, d, side, w)] = (result, entry)
    print(f'[load]   built dict; caching to {cache_pkl.name}', flush=True)
    cache_pkl.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_pkl, 'wb') as f:
        pickle.dump(out, f)
    return out


def load_outcomes_30dte_opt() -> dict:
    """Load 30dte_opt w=15d outcomes (the strategy-aligned barrier).
    Returns dict {(symbol, date, side): result}.
    """
    print(f'[load] 30dte_opt w=15d from {BARRIERS_30OPT.name}', flush=True)
    df = pl.read_parquet(BARRIERS_30OPT).select(['symbol', 'date', 'side', 'result'])
    # Force date conversion in case it's stored as string
    if df['date'].dtype == pl.Utf8:
        df = df.with_columns(pl.col('date').str.to_date())
    print(f'[load]   shape={df.shape}, date_dtype={df["date"].dtype}, side values: {df["side"].unique().to_list()[:5]}', flush=True)
    out = {}
    for r in df.iter_rows(named=True):
        out[(r['symbol'], r['date'], r['side'])] = r['result']
    print(f'[load]   {len(out):,} (sym,date,side) outcome rows', flush=True)
    # Debug: print first 3 keys
    keys_sample = list(out.keys())[:3]
    print(f'[load]   sample keys: {keys_sample}', flush=True)
    return out


def main():
    f_log = open(REPORT, 'w', encoding='utf-8')
    def log(s): print(s, flush=True); f_log.write(s + '\n'); f_log.flush()

    log(f'Overlap analysis  ({date.today().isoformat()})')
    log(f'  LBMIN={LOOKBACK_MIN}  LBMAX={LOOKBACK_MAX}')
    log(f'  prior gates: calls>={PRIOR_GATE}  puts<={PUT_PRIOR_GATE}')
    log(f'  next gates:  calls>={NEXT_GATE_CALL} puts<={NEXT_GATE_PUT}')

    # ── Load components (raw v32 scores)
    log(f'\n[1] Loading v32 scores ...')
    comp = pl.read_parquet(COMPONENTS_PQ).select(['symbol', 'date', 'overall'])
    comp = comp.with_columns(pl.col('date').str.to_date())
    log(f'    {len(comp):,} score rows; range {comp["date"].min()} → {comp["date"].max()}')

    # Build per-symbol date-sorted score list
    by_sym = defaultdict(list)
    for r in comp.iter_rows(named=True):
        by_sym[r['symbol']].append((r['date'], r['overall']))
    for s in by_sym:
        by_sym[s].sort()
    log(f'    {len(by_sym):,} symbols')

    # ── Load barrier wins (30dte_generic for prior win-pattern)
    log(f'\n[2] Loading prior win flags (30dte_generic w=7/15/30/60) ...')
    wins = load_30dte_generic_wins()

    # ── Load next-signal outcomes (30dte_opt w=15d — the strategy barrier)
    log(f'\n[3] Loading next-signal outcomes (30dte_opt w=15d) ...')
    outcomes = load_outcomes_30dte_opt()

    # ── For each (prior_peak, next_signal) pair where the next signal qualifies,
    #     classify prior by win_pattern and record the next signal's outcome.
    log(f'\n[4] Building (prior, next) pairs ...')
    t0 = time.time()

    # First build prior-peak set (calls 70+ OR puts ≤29)
    priors_call = []   # list of (sym, prior_date, prior_score)
    priors_put  = []
    for sym, dates in by_sym.items():
        for (d, ov) in dates:
            if ov >= PRIOR_GATE:
                priors_call.append((sym, d, ov))
            elif ov <= PUT_PRIOR_GATE:
                priors_put.append((sym, d, ov))
    log(f'    priors: {len(priors_call):,} calls, {len(priors_put):,} puts')

    # For each prior, build win pattern and find next qualifying signal in lookahead window.
    # The next signal must be ≥LBMIN AND ≤LBMAX days after prior.
    # Next signal must independently qualify (call ≥70 OR put ≤25) — same side as prior.
    pattern_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'losses': 0, 'expires': 0})
    skipped_no_next = 0
    skipped_no_outcome = 0

    def build_for_side(priors, prior_side_str, next_gate, next_side_check):
        nonlocal skipped_no_next, skipped_no_outcome
        for (sym, prior_d, prior_score) in priors:
            # Get win flags for this prior at all 4 windows
            w7  = wins.get((sym, prior_d, prior_side_str, 7))
            w15 = wins.get((sym, prior_d, prior_side_str, 15))
            w30 = wins.get((sym, prior_d, prior_side_str, 30))
            w60 = wins.get((sym, prior_d, prior_side_str, 60))
            # Need at least W7 knowable (LBMIN=15 ensures this)
            if w7 is None: continue
            # Need W15 knowable too (LBMIN=15 → 15 days have passed → W15 known)
            if w15 is None: continue
            # W30 knowable iff (today - prior_d) >= 30 days (we have data through ~today)
            # For 5y backtest we have data through 2026-04-15 ish, so most priors have W30/W60 knowable
            # We use None-aware classification (treat None as "?")

            r7  = w7[0]  if w7  else None
            r15 = w15[0] if w15 else None
            r30 = w30[0] if w30 else None
            r60 = w60[0] if w60 else None

            # Build win pattern as binary tuple, excluding NULL (insufficient forward data)
            # If a window's outcome is NULL, mark as "?". If 1, "1". If 0, "0".
            def fmt(r):
                if r is None: return '?'
                if r == 1:    return '1'
                return '0'

            pat = (fmt(r7), fmt(r15), fmt(r30), fmt(r60))

            # Find next qualifying signal of SAME side
            sym_dates = by_sym[sym]
            next_sig = None
            for (nd, nov) in sym_dates:
                gap = (nd - prior_d).days
                if gap < LOOKBACK_MIN: continue
                if gap > LOOKBACK_MAX: break
                if not next_side_check(nov): continue
                next_sig = (nd, nov)
                break
            if next_sig is None:
                skipped_no_next += 1
                continue
            nd, nov = next_sig

            # Outcome of next signal at 30dte_opt w=15d barrier
            next_side = 'low' if nov >= 50 else 'high'
            nout = outcomes.get((sym, nd, next_side))
            if nout is None:
                skipped_no_outcome += 1
                continue

            stats = pattern_stats[pat]
            stats['n'] += 1
            if nout == 1:
                stats['wins'] += 1
            elif nout == 0:
                stats['losses'] += 1
            else:
                stats['expires'] += 1

    # Calls — same-side priors and next signals
    log(f'    scanning call-side prior→next pairs ...')
    build_for_side(priors_call, 'low', NEXT_GATE_CALL, lambda x: x >= NEXT_GATE_CALL)
    pattern_stats_call = dict(pattern_stats)
    log(f'    call pairs built; {sum(s["n"] for s in pattern_stats_call.values()):,} '
        f'(skipped no-next={skipped_no_next:,}, no-outcome={skipped_no_outcome:,})')

    pattern_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'losses': 0, 'expires': 0})
    skipped_no_next = 0
    skipped_no_outcome = 0

    log(f'    scanning put-side prior→next pairs ...')
    build_for_side(priors_put, 'high', NEXT_GATE_PUT, lambda x: x <= NEXT_GATE_PUT)
    pattern_stats_put = dict(pattern_stats)
    log(f'    put pairs built; {sum(s["n"] for s in pattern_stats_put.values()):,} '
        f'(skipped no-next={skipped_no_next:,}, no-outcome={skipped_no_outcome:,})')

    log(f'    total pair-build time: {time.time()-t0:.1f}s')

    # ── Report
    log(f'\n{"="*78}')
    log(f'CALL SIDE — fresh-entry TP% by prior win pattern (W7, W15, W30, W60)')
    log(f'  Pattern key: 1=win, 0=loss, ?=NULL (insufficient forward data at prior)')
    log(f'  Next signal: same-side call ≥{NEXT_GATE_CALL} within [{LOOKBACK_MIN}, {LOOKBACK_MAX}] days')
    log(f'  Outcome: 30dte_opt w=15d barrier TP rate')
    log(f'{"="*78}')
    log(f'{"pattern":<13} {"N":>7} {"wins":>6} {"losses":>7} {"expires":>8} {"TP%":>7} {"BE-margin":>10}')
    log('-' * 78)

    BE_CALL = 46.2
    # Sort patterns by N desc
    sorted_patterns = sorted(pattern_stats_call.items(), key=lambda kv: kv[1]['n'], reverse=True)
    total_n = total_wins = total_losses = total_exp = 0
    for pat, s in sorted_patterns:
        n = s['n']
        if n < 30: continue   # skip tiny patterns
        wins = s['wins']; losses = s['losses']; exp = s['expires']
        resolved = wins + losses
        tp_pct = 100 * wins / resolved if resolved else 0
        be_margin = tp_pct - BE_CALL
        log(f'{str(pat):<13} {n:>7,} {wins:>6,} {losses:>7,} {exp:>8,} {tp_pct:>6.1f}% {be_margin:>+9.1f}pp')
        total_n += n; total_wins += wins; total_losses += losses; total_exp += exp
    log('-' * 78)
    if total_n > 0:
        agg_tp = 100 * total_wins / (total_wins + total_losses)
        log(f'{"TOTAL":<13} {total_n:>7,} {total_wins:>6,} {total_losses:>7,} {total_exp:>8,} {agg_tp:>6.1f}%')

    # ── Categorical analysis: count of "1"s in pattern (ignoring "?")
    log(f'\n--- CALL: aggregate by NUMBER of definitive wins ---')
    log(f'{"# wins":<8} {"N":>7} {"TP%":>7} {"BE-margin":>10}  description')
    log('-' * 78)
    by_count_call = defaultdict(lambda: {'n': 0, 'wins': 0, 'losses': 0})
    for pat, s in pattern_stats_call.items():
        n_ones = sum(1 for c in pat if c == '1')
        # Only count patterns where ALL 4 windows are knowable (no '?')
        if '?' in pat: continue
        by_count_call[n_ones]['n'] += s['n']
        by_count_call[n_ones]['wins'] += s['wins']
        by_count_call[n_ones]['losses'] += s['losses']
    for n_wins in sorted(by_count_call):
        s = by_count_call[n_wins]
        if s['n'] < 30: continue
        resolved = s['wins'] + s['losses']
        tp = 100 * s['wins'] / resolved if resolved else 0
        margin = tp - BE_CALL
        desc = ['no-window winner', '1-window winner', '2-window winner',
                '3-window winner', 'robust 4-window winner'][n_wins]
        log(f'{n_wins:<8} {s["n"]:>7,} {tp:>6.1f}% {margin:>+9.1f}pp  {desc}')

    # ── Same for puts
    log(f'\n{"="*78}')
    log(f'PUT SIDE — same analysis')
    log(f'{"="*78}')
    log(f'{"pattern":<13} {"N":>7} {"wins":>6} {"losses":>7} {"expires":>8} {"TP%":>7} {"BE-margin":>10}')
    log('-' * 78)
    BE_PUT = 36.4
    sorted_patterns = sorted(pattern_stats_put.items(), key=lambda kv: kv[1]['n'], reverse=True)
    total_n = total_wins = total_losses = total_exp = 0
    for pat, s in sorted_patterns:
        n = s['n']
        if n < 30: continue
        wins = s['wins']; losses = s['losses']; exp = s['expires']
        resolved = wins + losses
        tp_pct = 100 * wins / resolved if resolved else 0
        be_margin = tp_pct - BE_PUT
        log(f'{str(pat):<13} {n:>7,} {wins:>6,} {losses:>7,} {exp:>8,} {tp_pct:>6.1f}% {be_margin:>+9.1f}pp')
        total_n += n; total_wins += wins; total_losses += losses; total_exp += exp
    log('-' * 78)
    if total_n > 0:
        agg_tp = 100 * total_wins / (total_wins + total_losses)
        log(f'{"TOTAL":<13} {total_n:>7,} {total_wins:>6,} {total_losses:>7,} {total_exp:>8,} {agg_tp:>6.1f}%')

    log(f'\n--- PUT: aggregate by NUMBER of definitive wins ---')
    log(f'{"# wins":<8} {"N":>7} {"TP%":>7} {"BE-margin":>10}  description')
    log('-' * 78)
    by_count_put = defaultdict(lambda: {'n': 0, 'wins': 0, 'losses': 0})
    for pat, s in pattern_stats_put.items():
        n_ones = sum(1 for c in pat if c == '1')
        if '?' in pat: continue
        by_count_put[n_ones]['n'] += s['n']
        by_count_put[n_ones]['wins'] += s['wins']
        by_count_put[n_ones]['losses'] += s['losses']
    for n_wins in sorted(by_count_put):
        s = by_count_put[n_wins]
        if s['n'] < 30: continue
        resolved = s['wins'] + s['losses']
        tp = 100 * s['wins'] / resolved if resolved else 0
        margin = tp - BE_PUT
        desc = ['no-window winner', '1-window winner', '2-window winner',
                '3-window winner', 'robust 4-window winner'][n_wins]
        log(f'{n_wins:<8} {s["n"]:>7,} {tp:>6.1f}% {margin:>+9.1f}pp  {desc}')

    # ── Verdict
    log(f'\n{"="*78}')
    log('VERDICT')
    log(f'{"="*78}')
    if 4 in by_count_call and 1 in by_count_call:
        delta_call = (100 * by_count_call[4]['wins']/(by_count_call[4]['wins']+by_count_call[4]['losses']) -
                      100 * by_count_call[1]['wins']/(by_count_call[1]['wins']+by_count_call[1]['losses']))
        log(f'  CALL: 4-window robust winner vs 1-window winner: {delta_call:+.1f}pp TP%')
        if abs(delta_call) < 1.5:
            log('     → overlap is essentially redundant; ADDITIVE sum across windows is fine')
        elif delta_call > 1.5:
            log('     → overlap matters; MULTIPLICATIVE (or product-of-(1+w)) form preferred')
        else:
            log('     → overlap NEGATIVE; multi-window noise rather than signal')
    if 4 in by_count_put and 1 in by_count_put:
        delta_put = (100 * by_count_put[4]['wins']/(by_count_put[4]['wins']+by_count_put[4]['losses']) -
                     100 * by_count_put[1]['wins']/(by_count_put[1]['wins']+by_count_put[1]['losses']))
        log(f'  PUT:  4-window robust winner vs 1-window winner: {delta_put:+.1f}pp TP%')

    log(f'\n[done] wrote {REPORT}')
    f_log.close()


if __name__ == '__main__':
    main()
