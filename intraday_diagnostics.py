"""Intraday score-swing diagnostics over the `score_intraday_logs` table.

`Score` is keyed (symbol, date, version) and OVERWRITTEN in place on every
`trader update`, so the DB never retains how today's score evolved across the
day. `ScoreIntradayLog` (database/models/core.py) is the append-only per-run
snapshot that does. This module ranks the biggest intraday score swings and
attributes a single swing down the scoring pipeline so "fakeouts" — score moves
a lot while price / daily components barely move — can be diagnosed.

Pipeline checkpoints logged on every snapshot (stable across scoring versions),
in the ACTUAL apply order of database/utils/scoring.py compute_overall_score:

    weighted_sum(components) + weekly_adj + volume amplification = pre_regime
        -> [regime multiplier, symmetric @ 50]                   (not logged;
                                                                  derived from
                                                                  pre_regime x
                                                                  regime_mult)
        -> [cap/exh/ext-focal/WCF/CWCF/CWWD/CSWC dampeners]      = pre_boost
        -> [PCD/MCD/ICH/WVD/PESS/earnings/SCW/continuation/...]  = overall

Attribution diffs the logged checkpoints (exact) and the additive sub-terms
(components weighted_sum, weekly w_adj) explicitly. The only re-derived value
is the post-regime score (pre_regime with the logged regime_multiplier applied
symmetrically @ 50 — the exact formula in scoring.py), needed to split the
regime stage from the post-regime dampener stage; everything else diffs
*logged values*.

Public API (CLI handlers in trader.py + the dashboard fakeout badge in api.py):
    active_version()
    rank_swings(...)                 -> prints ranked table (CLI)
    drill(symbol, date, version)     -> prints timeline + attribution (CLI)
    today_swing_map(version, date)   -> {symbol: stability dict}  (bulk, for API badge)
    symbol_day_diagnostic(...)       -> full drill as a dict       (for API detail)
"""
from __future__ import annotations

import json
from datetime import date as _date, datetime, timedelta

from peewee import fn

from database.models.core import ScoreIntradayLog, AlgorithmVersion, Score

# component weight keys in weight_info  ->  ScoreIntradayLog score columns
_WEIGHT_TO_COL = {
    'trend': 'trend', 'bb': 'bb', 'rsi': 'rsi',
    'macd': 'macd', 'stoch': 'stoch', 'ta': 'technical_alignment',
}

# keys that move the score between the regime application and the pre_boost
# checkpoint (scoring.py ~1156-1331)
_DAMPENER_KEYS = (
    'cap_dampened', 'exh_damp', 'ext_damp', 'wcf_lift', 'cwcf_dampen',
    'cwwd_dampen', 'cswc_dampen',
)
# keys that move the score AFTER the pre_boost checkpoint (scoring.py ~1338+):
# PCD/MCD/ICH/WVD lifts+dampeners, PESS, earnings boost, SCW, continuation
# echo, sector wave, daily volume authority
_BOOST_KEYS = (
    'pcd_active', 'mcd_dampen', 'ich_call_dampen', 'ich_put_lift', 'wvd_lift',
    'wvd_dampen', 'pess_lift', 'ern_boost', 'scw_dampen', 'cont_lift',
    'sector_breadth_wave', 'daily_volume_authority_wave', 'pci_boost',
)

# fakeout = big score swing on a small price move
FAKEOUT_MIN_SWING = 15
FAKEOUT_MAX_PRICE_MOVE_PCT = 3.0


# --------------------------------------------------------------------------- #
# version resolution
# --------------------------------------------------------------------------- #
def active_version():
    return AlgorithmVersion.get_active_scores_version()


def resolve_version(token):
    """Accept 'v60' / '60' / int / AlgorithmVersion / None(=active)."""
    if token is None:
        return active_version()
    if isinstance(token, AlgorithmVersion):
        return token
    s = str(token).strip().lower().lstrip('v')
    try:
        return AlgorithmVersion.get_by_id(int(s))
    except Exception:
        return active_version()


def _parse_date(token):
    if token is None:
        return None
    if isinstance(token, _date):
        return token
    return datetime.strptime(str(token), '%Y-%m-%d').date()


# --------------------------------------------------------------------------- #
# swing ranking
# --------------------------------------------------------------------------- #
def swing_groups(version, since_date, min_snapshots=2):
    """Per-(symbol, date) aggregate over the intraday log for one version.

    Returns list of dicts sorted by score spread desc.
    """
    S = ScoreIntradayLog
    q = (S.select(
            S.symbol, S.date,
            fn.COUNT(S.id).alias('n'),
            fn.MIN(S.overall).alias('lo'),
            fn.MAX(S.overall).alias('hi'),
            fn.MIN(S.price).alias('plo'),
            fn.MAX(S.price).alias('phi'))
         .where(S.version == version, S.overall.is_null(False), S.date >= since_date)
         .group_by(S.symbol, S.date)
         .having(fn.COUNT(S.id) >= min_snapshots))
    out = []
    for r in q:
        lo, hi = r.lo or 0, r.hi or 0
        spread = hi - lo
        plo, phi = r.plo or 0.0, r.phi or 0.0
        price_move = (100.0 * (phi - plo) / plo) if plo else 0.0
        out.append({
            'symbol': r.symbol_id, 'date': str(r.date), 'n': r.n,
            'lo': lo, 'hi': hi, 'spread': spread,
            'price_move_pct': round(price_move, 2),
            'is_fakeout': spread >= FAKEOUT_MIN_SWING and abs(price_move) < FAKEOUT_MAX_PRICE_MOVE_PCT,
        })
    out.sort(key=lambda d: d['spread'], reverse=True)
    return out


def rank_swings(days=7, version_token=None, min_swing=10, limit=30, fakeout_only=False):
    """CLI: print the biggest intraday score swings over the last `days`."""
    version = resolve_version(version_token)
    since = _date.today() - timedelta(days=days)
    groups = [g for g in swing_groups(version, since) if g['spread'] >= min_swing]
    if fakeout_only:
        groups = [g for g in groups if g['is_fakeout']]
    groups = groups[:limit]

    print(f"Intraday score swings — active v{version.id} — since {since} "
          f"(>= {min_swing} pts{', fakeouts only' if fakeout_only else ''})")
    if not groups:
        print("  (no swings — is score_intraday_logs populated? feature shipped 2026-05-27)")
        return groups
    print(f"  {'SYM':<8}{'DATE':<12}{'runs':>5}{'lo':>5}{'hi':>5}{'swing':>7}{'price%':>9}   flag")
    print("  " + "-" * 60)
    for g in groups:
        flag = "FAKEOUT (score moved, price didn't)" if g['is_fakeout'] else ""
        print(f"  {g['symbol']:<8}{g['date']:<12}{g['n']:>5}{g['lo']:>5}{g['hi']:>5}"
              f"{g['spread']:>7}{g['price_move_pct']:>8.2f}%   {flag}")
    print(f"\n  Drill into any one:  trader intraday-drill SYM [DATE]")
    return groups


# --------------------------------------------------------------------------- #
# snapshot loading + attribution
# --------------------------------------------------------------------------- #
def load_snapshots(symbol, target_date, version):
    """Ordered (by logged_at) list of snapshot dicts for one (symbol, date, version)."""
    S = ScoreIntradayLog
    rows = list(S.select()
                .where(S.symbol == symbol, S.date == target_date, S.version == version,
                       S.overall.is_null(False))
                .order_by(S.logged_at))
    snaps = []
    for r in rows:
        try:
            wi = json.loads(r.weight_info_json) if r.weight_info_json else {}
        except Exception:
            wi = {}
        snaps.append({
            'logged_at': r.logged_at, 'source': r.source,
            'overall': r.overall, 'pre_regime': r.pre_regime, 'pre_boost': r.pre_boost,
            'trend': r.trend, 'bb': r.bb, 'rsi': r.rsi, 'macd': r.macd,
            'stoch': r.stoch, 'technical_alignment': r.technical_alignment,
            'price': r.price, 'regime_multiplier': r.regime_multiplier,
            'volume_signal': r.volume_signal, 'volume_magnitude': r.volume_magnitude,
            'wi': wi,
        })
    return snaps


def _weighted_sum(snap):
    """Reconstruct the component weighted_sum from logged scores x weights."""
    wi = snap['wi']
    total, have = 0.0, False
    for wkey, col in _WEIGHT_TO_COL.items():
        w = wi.get(wkey)
        s = snap.get(col)
        if w is None or s is None:
            continue
        total += float(w) * float(s) / 100.0
        have = True
    return total if have else None


def _num(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _post_regime_value(snap):
    """Score immediately after the regime multiplier (derived — not logged).

    Mirrors the regime application in scoring.compute_overall_score: symmetric
    around 50 (>=50 uses mult, <50 uses 2.0-mult), int-rounded, clipped 0-100.
    None/1.0 multiplier = no-op. Returns None when pre_regime is missing.
    (Historical rows under the retired mis_stress softener / JA4 put blend used
    a perturbed effective multiplier — for those the small discrepancy lands in
    the dampener stage.)
    """
    pr = snap['pre_regime']
    if pr is None:
        return None
    m = snap['regime_multiplier']
    if m is None or m == 1.0:
        return pr
    if pr >= 50:
        adjusted = 50 + (pr - 50) * m
    else:
        adjusted = 50 + (pr - 50) * (2.0 - m)
    return int(max(0, min(100, round(adjusted))))


def attribute_swing(snaps):
    """Diff the two score extremes (chronological earlier -> later) down the pipeline.

    Stage algebra follows the ACTUAL scoring order (see module docstring):
        pre_regime -> [regime] -> post_regime -> [dampeners] -> pre_boost
                   -> [boosts/late transforms] -> overall

    Returns a dict: endpoints, stage deltas, sub-drivers, dominant cause + blurb.
    """
    if len(snaps) < 2:
        return None
    lo_s = min(snaps, key=lambda s: s['overall'])
    hi_s = max(snaps, key=lambda s: s['overall'])
    if lo_s is hi_s or lo_s['overall'] == hi_s['overall']:
        return None
    # present in lived order: earlier snapshot -> later snapshot
    a, b = sorted((lo_s, hi_s), key=lambda s: s['logged_at'])
    d_overall = b['overall'] - a['overall']

    def chk(s, k):
        return s[k] if s[k] is not None else s['overall']

    pr_a, pr_b = chk(a, 'pre_regime'), chk(b, 'pre_regime')
    pb_a, pb_b = chk(a, 'pre_boost'), chk(b, 'pre_boost')
    post_a, post_b = _post_regime_value(a), _post_regime_value(b)
    if post_a is None or post_b is None:
        # can't derive the post-regime score — fold the regime effect into the
        # dampener stage rather than mis-assigning it
        post_a, post_b = pr_a, pr_b

    prebase_stage = pr_b - pr_a                      # components + weekly + volume amp
    regime_stage = (post_b - post_a) - prebase_stage   # regime multiplier
    dampener_stage = (pb_b - pb_a) - (post_b - post_a)  # cap/exh/ext/WCF/CWCF/CWWD/CSWC
    boost_stage = d_overall - (pb_b - pb_a)          # PCD/MCD/ICH/WVD/PESS/ern/SCW/cont/...

    ws_a, ws_b = _weighted_sum(a), _weighted_sum(b)
    d_components = (ws_b - ws_a) if (ws_a is not None and ws_b is not None) else None

    wadj_a = _num(a['wi'].get('w_adj'))
    wadj_b = _num(b['wi'].get('w_adj'))
    d_weekly = (wadj_b - wadj_a) if (wadj_a is not None and wadj_b is not None) else None

    explained = (d_components or 0.0) + (d_weekly or 0.0)
    prebase_residual = prebase_stage - explained  # volume amplification / rounding

    # which dampener/boost keys toggled between the two snapshots
    changed_keys = []
    for k in _DAMPENER_KEYS + _BOOST_KEYS:
        va, vb = a['wi'].get(k), b['wi'].get(k)
        if va != vb:
            changed_keys.append((k, va, vb))
    changed_dampener = [k for k, _, _ in changed_keys if k in _DAMPENER_KEYS]
    changed_boost = [k for k, _, _ in changed_keys if k in _BOOST_KEYS]
    vol_changed = (a['volume_signal'], a['volume_magnitude']) != (b['volume_signal'], b['volume_magnitude'])

    # weekly partial/completed envelope flag
    weekly_flip = None
    for s in (a, b):
        if s['wi'].get('weekly_gap_flag'):
            weekly_flip = {
                'partial': s['wi'].get('wadj_partial'),
                'completed': s['wi'].get('wadj_completed'),
                'gap': s['wi'].get('weekly_adj_gap'),
            }
            break

    # candidate drivers, ranked by |contribution to overall|
    cands = []
    if d_weekly is not None:
        cands.append(('weekly', d_weekly))
    if d_components is not None:
        cands.append(('components', d_components))
    cands.append(('volume', prebase_residual))
    cands.append(('regime', regime_stage))
    cands.append(('dampener', dampener_stage))
    cands.append(('boost', boost_stage))
    cands.sort(key=lambda c: abs(c[1]), reverse=True)
    dominant = cands[0][0]

    labels = {
        'weekly': 'Weekly adjustment',
        'components': 'Daily component shift',
        'volume': 'Volume amplification (pre-regime)',
        'regime': 'Regime multiplier',
        'dampener': 'Post-regime dampener (cap/exh/ext/WCF/CWCF/CWWD/CSWC)',
        'boost': 'Boost-stage transform (PCD/MCD/ICH/WVD/PESS/earnings/SCW/continuation)',
    }
    blurb = labels[dominant]
    if dominant == 'weekly' and weekly_flip:
        blurb += (f" — partial<->completed week flip "
                  f"({weekly_flip['partial']} -> {weekly_flip['completed']}, gap {weekly_flip['gap']})")
    elif dominant == 'volume' and vol_changed:
        blurb += f" — volume signal changed ({a['volume_signal']}/{a['volume_magnitude']} -> {b['volume_signal']}/{b['volume_magnitude']})"
    elif dominant == 'dampener':
        if changed_dampener:
            blurb += " — keys: " + ", ".join(changed_dampener[:6])
        else:
            blurb += " — no traced dampener key changed (possibly a sub-threshold value shift)"
    elif dominant == 'boost':
        if changed_boost:
            blurb += " — keys: " + ", ".join(changed_boost[:6])

    # recognized fakeout families
    fakeout_family = None
    if dominant == 'dampener' and 'wcf_lift' in changed_dampener:
        fakeout_family = 'wcf_boundary'
        blurb += " [wcf_boundary fakeout: WCF put-floor lift toggled at the score gate]"

    return {
        'earlier': a, 'later': b,
        'd_overall': d_overall,
        'stages': {'prebase': prebase_stage, 'regime': regime_stage,
                   'dampener': dampener_stage, 'boost': boost_stage},
        'prebase': {'components': d_components, 'weekly': d_weekly, 'residual': round(prebase_residual, 2)},
        'changed_keys': changed_keys, 'vol_changed': vol_changed, 'weekly_flip': weekly_flip,
        'dominant': dominant, 'blurb': blurb, 'fakeout_family': fakeout_family,
    }


def _biggest_step(snaps):
    """Largest adjacent-run jump (magnitude, time)."""
    best = None
    for i in range(1, len(snaps)):
        d = snaps[i]['overall'] - snaps[i - 1]['overall']
        if best is None or abs(d) > abs(best[0]):
            best = (d, snaps[i - 1], snaps[i])
    return best


# --------------------------------------------------------------------------- #
# CLI drill-down
# --------------------------------------------------------------------------- #
def _pick_drill_date(symbol, version):
    """Most recent date with >= 2 snapshots; fall back to latest snapshot date."""
    S = ScoreIntradayLog
    row = (S.select(S.date)
           .where(S.symbol == symbol, S.version == version, S.overall.is_null(False))
           .group_by(S.date).having(fn.COUNT(S.id) >= 2)
           .order_by(S.date.desc()).limit(1).scalar())
    if row:
        return row
    return (S.select(fn.MAX(S.date))
            .where(S.symbol == symbol, S.version == version).scalar())


def _fmt(v, nd=0):
    if v is None:
        return '-'
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def drill(symbol, date_token=None, version_token=None):
    symbol = symbol.upper()
    version = resolve_version(version_token)
    target = _parse_date(date_token) or _pick_drill_date(symbol, version)
    if target is None:
        print(f"No intraday snapshots for {symbol} (v{version.id}).")
        return None
    snaps = load_snapshots(symbol, target, version)
    if not snaps:
        print(f"No intraday snapshots for {symbol} {target} (v{version.id}).")
        return None

    print(f"\n{symbol}  {target}  v{version.id}  —  {len(snaps)} snapshots")
    print(f"  {'time':<9}{'src':<11}{'ovr':>4}{'preR':>5}{'preB':>5}{'tr':>4}{'bb':>4}{'rsi':>4}"
          f"{'mac':>4}{'st':>4}{'w_adj':>7}{'regM':>6}{'price':>10}{'vol':>13}")
    print("  " + "-" * 96)
    for s in snaps:
        wadj = _num(s['wi'].get('w_adj'))
        vol = f"{s['volume_signal'] or '-'}/{_fmt(s['volume_magnitude'], 2)}"
        print(f"  {s['logged_at'].strftime('%H:%M:%S'):<9}{(s['source'] or '')[:10]:<11}"
              f"{_fmt(s['overall']):>4}{_fmt(s['pre_regime']):>5}{_fmt(s['pre_boost']):>5}"
              f"{_fmt(s['trend']):>4}{_fmt(s['bb']):>4}{_fmt(s['rsi']):>4}{_fmt(s['macd']):>4}"
              f"{_fmt(s['stoch']):>4}{_fmt(wadj, 1):>7}{_fmt(s['regime_multiplier'], 2):>6}"
              f"{_fmt(s['price'], 2):>10}{vol:>13}")

    attr = attribute_swing(snaps)
    if attr is None:
        print("\n  Score was constant across snapshots — no swing to attribute.")
        return None

    a, b = attr['earlier'], attr['later']
    print(f"\n  ── Swing attribution: {a['overall']} @ {a['logged_at'].strftime('%H:%M')} "
          f"-> {b['overall']} @ {b['logged_at'].strftime('%H:%M')}   (Δ {attr['d_overall']:+d})")
    st = attr['stages']
    pb = attr['prebase']
    print(f"     stage  pre-regime (components+weekly+volume)         : {st['prebase']:+.1f}")
    print(f"              ├─ components (weighted_sum)  : {('%+.1f' % pb['components']) if pb['components'] is not None else 'n/a'}")
    print(f"              ├─ weekly  w_adj              : {('%+.1f' % pb['weekly']) if pb['weekly'] is not None else 'n/a'}"
          + (f"   [{a['wi'].get('w_adj')} -> {b['wi'].get('w_adj')}]" if pb['weekly'] else ""))
    print(f"              └─ volume/rounding residual   : {pb['residual']:+.1f}")
    print(f"     stage  regime multiplier                              : {st['regime']:+.1f}")
    print(f"     stage  dampeners (cap/exh/ext/WCF/CWCF/CWWD/CSWC)     : {st['dampener']:+.1f}")
    print(f"     stage  boosts/late (PCD/MCD/ICH/WVD/PESS/ern/SCW/cont): {st['boost']:+.1f}")
    if attr['changed_keys']:
        print("     toggled keys: " + "; ".join(f"{k}: {va}->{vb}" for k, va, vb in attr['changed_keys']))
    if attr['vol_changed']:
        print(f"     volume: {a['volume_signal']}/{a['volume_magnitude']} -> {b['volume_signal']}/{b['volume_magnitude']}")
    print(f"\n  >>> DOMINANT CAUSE: {attr['blurb']}")
    return attr


# --------------------------------------------------------------------------- #
# API-facing helpers (dashboard fakeout badge)
# --------------------------------------------------------------------------- #
def today_swing_map(version=None, target_date=None):
    """Bulk: {symbol: {min, max, spread, n, price_move_pct, is_fakeout, latest}} for one day.

    Powers the dashboard fakeout/stability badge. One GROUP BY query.
    """
    version = resolve_version(version)
    if target_date is None:
        target_date = (ScoreIntradayLog
                       .select(fn.MAX(ScoreIntradayLog.date))
                       .where(ScoreIntradayLog.version == version).scalar())
    if target_date is None:
        return {}, None
    out = {}
    for g in swing_groups(version, target_date, min_snapshots=2):
        if g['date'] != str(target_date):
            continue
        out[g['symbol']] = g
    return out, str(target_date)


def symbol_day_diagnostic(symbol, target_date=None, version=None):
    """Full drill-down as a JSON-friendly dict (for an API detail endpoint)."""
    symbol = symbol.upper()
    version = resolve_version(version)
    target = _parse_date(target_date) or _pick_drill_date(symbol, version)
    if target is None:
        return None
    snaps = load_snapshots(symbol, target, version)
    if not snaps:
        return None
    attr = attribute_swing(snaps)
    timeline = [{
        'time': s['logged_at'].isoformat(), 'source': s['source'],
        'overall': s['overall'], 'pre_regime': s['pre_regime'], 'pre_boost': s['pre_boost'],
        'w_adj': _num(s['wi'].get('w_adj')), 'regime_multiplier': s['regime_multiplier'],
        'price': s['price'], 'volume_signal': s['volume_signal'],
    } for s in snaps]
    return {
        'symbol': symbol, 'date': str(target), 'version': f"v{version.id}",
        'n_snapshots': len(snaps),
        'lo': min(s['overall'] for s in snaps), 'hi': max(s['overall'] for s in snaps),
        'timeline': timeline,
        'dominant': attr['blurb'] if attr else None,
        'fakeout_family': attr.get('fakeout_family') if attr else None,
    }
