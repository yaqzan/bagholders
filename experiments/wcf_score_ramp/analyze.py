"""
WCF score-gate ramp - stored-data validation (READ-ONLY; no Score writes).

Context: the v27 WCF put-floor lift (scoring.py) fires binary on the post-regime
integer `overall < 28`, producing a 21-point intraday cliff (wcf_lift 21.85 <->
None) when a 1-pt component/regime wobble crosses 27/28. Candidate: score-axis
linear ramp - full lift at <=27 (unchanged), fading to zero at RAMP_TOP.

Parts:
  B. Cohort + analytic diff on stored v71 rows (5y): which rows change under
     RAMP_TOP in {30, 31, 33}, where they land, and bucket migrations.
     Self-validation: reconstruct FIRED rows (wcf_lift key, 1y) and verify the
     production formula reproduces stored finals bit-for-bit (proves the
     stage-order/reconstruction model is faithful).
  C. Stability replay on score_intraday_logs (2026-05-27 ->): recompute every
     snapshot's final under each ramp, compare per-(sym,date) swing
     distributions vs production.
  D. WR15 (generic put barrier w=15, DuckDB mirror) of the cohort that LEAVES
     the <30 assessment bucket vs the bucket remainder.

Run from the worktree; uses the MAIN checkout's duck mirror by absolute path.
"""
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

VERSION_ID = 71
K, TARGET, WADJ_CUT, GATE = 0.95, 50, -17.0, 28
RAMP_TOPS = [30, 31, 33]
CUT5Y = date.today() - timedelta(days=1825)
CUT1Y = date.today() - timedelta(days=365)
LOGS_FROM = date(2026, 5, 27)
DUCK = r'C:\Development\Trader\.cache\barrier_outcomes.duckdb'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis_results.json')

from database.models.core import Score, ScoreIntradayLog  # noqa: E402


def weakness(w_adj):
    return max(0.0, min(1.0, (w_adj - WADJ_CUT) / -WADJ_CUT))


def ramp_final(s0, w_adj, top):
    """New post-WCF integer under a score-axis ramp with the given top."""
    if w_adj is None or s0 >= top:
        return s0
    wk = weakness(float(w_adj))
    span = max(1, top - (GATE - 1))
    sg = max(0.0, min(1.0, (top - s0) / span))
    wk *= sg
    if wk <= 0:
        return s0
    return int(min(100, round(s0 + K * wk * (TARGET - s0))))


def legacy_final(s0, w_adj):
    if w_adj is None or s0 >= GATE:
        return s0
    wk = weakness(float(w_adj))
    if wk <= 0:
        return s0
    return int(min(100, round(s0 + K * wk * (TARGET - s0))))


def parse_wi(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}


results = {}

# ───────────────────────── Part B: stored-row cohort ─────────────────────────
print('Part B: stored v71 rows, overall in [28,30], 5y ...', flush=True)
rows = list(
    Score.select(Score.symbol, Score.date, Score.overall, Score.weight_info)
    .where((Score.version == VERSION_ID)
           & (Score.date >= CUT5Y)
           & (Score.overall.between(28, 30)))
    .tuples()
)
print(f'  pulled {len(rows)} rows', flush=True)

cohort = []          # (sym, d, s0, w_adj) rows where ramp could act
n_no_weekly = n_strong_weekly = n_pess = 0
for sym, d, s0, wi_raw in rows:
    wi = parse_wi(wi_raw)
    if 'pess_lift' in wi:
        n_pess += 1
    w_adj = wi.get('w_adj')
    if w_adj is None:
        n_no_weekly += 1
        continue
    if float(w_adj) <= WADJ_CUT:
        n_strong_weekly += 1
        continue
    cohort.append((sym, d, int(s0), float(w_adj)))

partB = {
    'rows_28_30_5y': len(rows),
    'no_weekly': n_no_weekly,
    'strong_weekly_excluded': n_strong_weekly,
    'pess_rows_seen': n_pess,
    'ramp_eligible': len(cohort),
    'variants': {},
}
for top in RAMP_TOPS:
    changed = []
    migrate_out_lt30 = []   # old <=29 -> new >= 30 (leave <30 assessment bucket)
    by_old = Counter()
    new_min, new_max = 101, -1
    for sym, d, s0, w_adj in cohort:
        if s0 >= top:
            continue
        s1 = ramp_final(s0, w_adj, top)
        if s1 != s0:
            changed.append((sym, d, s0, s1))
            by_old[s0] += 1
            new_min, new_max = min(new_min, s1), max(new_max, s1)
            if s0 <= 29 and s1 >= 30:
                migrate_out_lt30.append((sym, d, s0, s1))
    partB['variants'][f'top{top}'] = {
        'changed_rows': len(changed),
        'changed_by_old_score': dict(by_old),
        'new_score_range': [new_min, new_max] if changed else None,
        'migrate_out_lt30': len(migrate_out_lt30),
        'tradable_bucket_violations': sum(
            1 for _, _, s0, s1 in changed if s1 <= 27 or s1 >= 69),
    }
    partB['variants'][f'top{top}']['_migrate_keys'] = [
        (sym, d.isoformat(), s0, s1) for sym, d, s0, s1 in migrate_out_lt30]
results['partB'] = partB
print(json.dumps({k: v for k, v in partB.items() if k != 'variants'}, indent=2))
for top in RAMP_TOPS:
    v = dict(partB['variants'][f'top{top}'])
    v.pop('_migrate_keys')
    print(f'  top{top}:', json.dumps(v))

# ── Self-validation: fired rows reproduce stored finals bit-for-bit ──
print('\nPart B2: self-validation on FIRED rows (wcf_lift key, 1y) ...', flush=True)
fired = list(
    Score.select(Score.symbol, Score.date, Score.overall, Score.weight_info)
    .where((Score.version == VERSION_ID)
           & (Score.date >= CUT1Y)
           & (Score.overall.between(31, 49))
           & (Score.weight_info.contains('"wcf_lift"')))
    .tuples()
)
n_match = n_mismatch = n_skip = 0
mismatch_examples = []
for sym, d, s1, wi_raw in fired:
    wi = parse_wi(wi_raw)
    lift = wi.get('wcf_lift')
    w_adj = wi.get('w_adj')
    if lift is None or w_adj is None:
        n_skip += 1
        continue
    s0 = int(round(s1 - float(lift)))
    ok = False
    for cand in (s0 - 1, s0, s0 + 1):
        if cand < GATE and legacy_final(cand, w_adj) == s1:
            ok = True
            break
    if ok:
        n_match += 1
    else:
        n_mismatch += 1
        if len(mismatch_examples) < 10:
            mismatch_examples.append((sym, d.isoformat(), int(s1), lift, w_adj))
results['partB2'] = {
    'fired_rows_1y': len(fired), 'match': n_match,
    'mismatch': n_mismatch, 'skipped': n_skip,
    'mismatch_examples': mismatch_examples,
}
print(json.dumps(results['partB2'], indent=2))

# ───────────────────── Part C: intraday stability replay ─────────────────────
print('\nPart C: score_intraday_logs stability replay ...', flush=True)
light = list(
    ScoreIntradayLog.select(
        ScoreIntradayLog.symbol, ScoreIntradayLog.date,
        ScoreIntradayLog.overall, ScoreIntradayLog.pre_boost,
        ScoreIntradayLog.logged_at)
    .where((ScoreIntradayLog.version == VERSION_ID)
           & (ScoreIntradayLog.date >= LOGS_FROM))
    .tuples()
)
zone = list(
    ScoreIntradayLog.select(
        ScoreIntradayLog.symbol, ScoreIntradayLog.date,
        ScoreIntradayLog.logged_at, ScoreIntradayLog.overall,
        ScoreIntradayLog.pre_boost, ScoreIntradayLog.weight_info_json)
    .where((ScoreIntradayLog.version == VERSION_ID)
           & (ScoreIntradayLog.date >= LOGS_FROM)
           & (ScoreIntradayLog.pre_boost.between(26, 52)))
    .tuples()
)
print(f'  {len(light)} snapshots, {len(zone)} in WCF zone', flush=True)

zone_info = {}
for sym, d, ts, ovr, pb, wij in zone:
    wi = parse_wi(wij)
    w_adj = wi.get('w_adj')
    lift = wi.get('wcf_lift')
    s1 = int(pb)
    s0 = int(round(s1 - float(lift))) if lift is not None else s1
    zone_info[(sym, d, ts)] = (s0, w_adj, s1)

groups = defaultdict(list)
for sym, d, ovr, pb, ts in light:
    if ovr is None:
        continue
    groups[(sym, d)].append((ts, int(ovr), pb))

def swing_stats(top):
    """per-group swing under ramp `top` (None = production stored)."""
    out = {}
    for key, snaps in groups.items():
        if len(snaps) < 2:
            continue
        vals = []
        for ts, ovr, pb in snaps:
            zi = zone_info.get((key[0], key[1], ts))
            if top is None or zi is None or zi[1] is None:
                vals.append(ovr)
            else:
                s0, w_adj, s1 = zi
                vals.append(ovr + (ramp_final(s0, w_adj, top) - s1))
        out[key] = max(vals) - min(vals)
    return out

prod = swing_stats(None)
affected = set()
for (sym, d, ts), (s0, w_adj, s1) in zone_info.items():
    if w_adj is not None and float(w_adj) > WADJ_CUT and 26 <= s0 <= 32:
        affected.add((sym, d))
partC = {'groups': len(prod), 'wcf_affected_groups': len(affected), 'variants': {}}

def summarize(sw, label):
    ge10 = sum(1 for v in sw.values() if v >= 10)
    ge15 = sum(1 for v in sw.values() if v >= 15)
    ge20 = sum(1 for v in sw.values() if v >= 20)
    aff = [sw[k] for k in affected if k in sw]
    return {
        'label': label, 'groups_ge10': ge10, 'groups_ge15': ge15,
        'groups_ge20': ge20,
        'affected_mean_swing': round(sum(aff) / len(aff), 2) if aff else None,
        'affected_max_swing': max(aff) if aff else None,
        'affected_ge10': sum(1 for v in aff if v >= 10),
    }

partC['production'] = summarize(prod, 'production (binary gate)')
for top in RAMP_TOPS:
    partC['variants'][f'top{top}'] = summarize(swing_stats(top), f'ramp top={top}')
results['partC'] = partC
print(json.dumps(partC, indent=2))

# ───────────────── Part D: WR15 of the migrating cohort ─────────────────
print('\nPart D: WR15 of <30-bucket migrators vs bucket remainder ...', flush=True)
try:
    import duckdb
    import polars as pl

    bucket_rows = list(
        Score.select(Score.symbol, Score.date, Score.overall)
        .where((Score.version == VERSION_ID)
               & (Score.date >= CUT5Y)
               & (Score.overall <= 29))
        .tuples()
    )
    print(f'  <30 bucket rows (5y): {len(bucket_rows)}', flush=True)
    mig31 = {(sym, d) for sym, d, s0, s1 in
             [(m[0], date.fromisoformat(m[1]), m[2], m[3])
              for m in partB['variants']['top31']['_migrate_keys']]}
    syms, dts, grps = [], [], []
    for sym, d, s in bucket_rows:
        syms.append(sym); dts.append(d)
        grps.append('migrator' if (sym, d) in mig31 else 'remainder')
    df = pl.DataFrame({'symbol': syms, 'date': dts, 'grp': grps})
    con = duckdb.connect(DUCK, read_only=True)
    con.register('cohort', df.to_arrow())
    q = con.execute("""
        SELECT c.grp, COUNT(*) AS n, AVG(b.result) AS wr15
        FROM cohort c
        JOIN barrier_outcomes b
          ON b.symbol = c.symbol AND b.date = c.date
         AND b.side = 'high' AND b.w_days = 15
         AND b.barrier_set = '30dte_generic'
         AND b.result IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    partD = {r[0]: {'n': int(r[1]), 'wr15': round(float(r[2]), 4)} for r in q}
    if 'migrator' in partD and 'remainder' in partD:
        nm, nr = partD['migrator']['n'], partD['remainder']['n']
        wm, wr = partD['migrator']['wr15'], partD['remainder']['wr15']
        before = (wm * nm + wr * nr) / (nm + nr)
        partD['bucket_wr15_before'] = round(before, 4)
        partD['bucket_wr15_after_migration'] = wr
        partD['bucket_delta_pp'] = round((wr - before) * 100, 2)
    results['partD'] = partD
    print(json.dumps(partD, indent=2))
except Exception as e:  # noqa: BLE001
    results['partD'] = {'error': repr(e)}
    print('  Part D failed:', repr(e))

partB['variants_migrate_keys_stripped'] = True
for top in RAMP_TOPS:
    partB['variants'][f'top{top}'].pop('_migrate_keys', None)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2, default=str)
print('\nwrote', OUT)
