"""
Probe: do trend maturity + extension acceleration cleanly separate
the canonical examples from the V3-vs-V_GRAD2 investigation?

Originally written to test 8 canonical cases — now extended with a
full-universe scan over every V3 (id=3) HIGH peak in the lookback window.

Modes:
    python -m experiments.probe_maturity_acceleration            # 8-case probe
    python -m experiments.probe_maturity_acceleration --scan     # full V3 scan
    python -m experiments.probe_maturity_acceleration --scan --days 365

Scan output:
  - Overall ret30 distribution + WR for V3 HIGH peaks
  - Marginal: WR + mean/std ret30 by quintile of (sacc, ext, matur, vel5, raw_rsi, raw_stoch)
  - Joint 2D: (ext quintile) x (sacc quintile) — mean ret30, std, N, WR
  - High-variance "do not bet" cells flagged
  - SITM/RGTI bin co-location test: do correctly-faded and continuation cases land in the same cell?
"""

from datetime import date, timedelta
from collections import namedtuple
import math

from database.models.core import Stock, Score, AlgorithmVersion
from database.models.technical import PriceHistory, Indicator


# ── Canonical examples ──────────────────────────────────────────────────────

# (symbol, date, label, expected_regime)
KILLER_CASES = [
    # V3 fired HIGH, was wrong because trend broke (oscillator-dominant MISS)
    ('ASTS', date(2025, 11, 20), 'V3-WRONG-HIGH (trend broke)',  'maturity-collapsing'),
    # V3 fired HIGH, was wrong because trend continued (trend-dominant MISS)
    ('AMAT', date(2025, 12, 19), 'V3-WRONG-HIGH (continuation)', 'late-stage-continuation'),
    # V_GRAD2 fired HIGH (V3 silent), was wrong, late-stage continuation
    ('RGTI', date(2025, 9, 12),  'V_GRAD2-WRONG-HIGH',           'late-stage-continuation'),
    ('OPEN', date(2025, 8, 14),  'V_GRAD2-WRONG-HIGH',           'late-stage-continuation'),
    ('CRML', date(2025, 9, 19),  'V_GRAD2-WRONG-HIGH',           'late-stage-continuation'),
]


# ── Feature computation ─────────────────────────────────────────────────────

Window = namedtuple('Window', 'dates closes ema50s rsis stochs bb_pcts')


def load_window(symbol, target, back_days=80, fwd_days=35):
    lo = target - timedelta(days=back_days)
    hi = target + timedelta(days=fwd_days)

    ph = list(PriceHistory.select()
              .where((PriceHistory.symbol == symbol) &
                     (PriceHistory.date >= lo) & (PriceHistory.date <= hi))
              .order_by(PriceHistory.date))
    ind = {i.date: i for i in Indicator.select()
           .where((Indicator.symbol == symbol) &
                  (Indicator.date >= lo) & (Indicator.date <= hi))}

    dates, closes, ema50s, rsis, stochs, bb_pcts = [], [], [], [], [], []
    for row in ph:
        i = ind.get(row.date)
        if not i or i.ema_50 is None:
            continue
        dates.append(row.date)
        closes.append(float(row.close))
        ema50s.append(float(i.ema_50))
        rsis.append(float(i.rsi) if i.rsi is not None else None)
        stochs.append(float(i.stoch) if i.stoch is not None else None)
        if i.upper_band and i.lower_band:
            u, l = float(i.upper_band), float(i.lower_band)
            br = u - l
            bb_pcts.append((float(row.close) - l) / br if br > 0 else None)
        else:
            bb_pcts.append(None)
    return Window(dates, closes, ema50s, rsis, stochs, bb_pcts)


def extension_series(w):
    """Continuous extension (price - ema50) / ema50, in fractional units."""
    return [(c - e) / e for c, e in zip(w.closes, w.ema50s)]


def ema(values, span):
    """Standard EMA."""
    if not values:
        return []
    alpha = 2.0 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def maturity_series(ext, span=20):
    """
    Signed smooth trend maturity in [-1, 1]-ish range.
    Accumulates direction-aware extension above/below EMA50, normalized by tanh saturation.
    Span 20 = ~one trading month of recent history.

    Positive  = mature uptrend (price persistently above EMA50).
    Negative  = mature downtrend (price persistently below EMA50).
    Near zero = recent crossover / sideways.

    Callers should typically use abs(matur) when comparing across HIGH and LOW peaks
    (i.e. "how committed is this trend in either direction").
    """
    # tanh(k * ext) ∈ [-1, 1] — saturates at extreme extension so mature trends
    # don't get unbounded credit for being far above/below
    k = 8.0  # k=8 means |ext|=12.5% saturates near 1.0
    saturated = [math.tanh(k * x) for x in ext]
    return ema(saturated, span)


def velocity_series(ext, lag=5):
    """5-day change in extension."""
    return [ext[i] - ext[i - lag] if i >= lag else None for i in range(len(ext))]


def acceleration_series(vel, lag=5):
    """5-day change in velocity. Negative = decelerating, positive = accelerating."""
    return [vel[i] - vel[i - lag] if i >= lag and vel[i - lag] is not None else None
            for i in range(len(vel))]


def smooth_acceleration(accel, span=5):
    """Smoothed acceleration to remove single-day noise."""
    out = [None] * len(accel)
    alpha = 2.0 / (span + 1)
    last = None
    for i, a in enumerate(accel):
        if a is None:
            continue
        last = a if last is None else (alpha * a + (1 - alpha) * last)
        out[i] = last
    return out


def fwd_return(w, idx, days=30):
    """Closest-to-30-days-out return."""
    target_d = w.dates[idx] + timedelta(days=days)
    best = None
    for j in range(idx + 1, len(w.dates)):
        if w.dates[j] >= target_d:
            best = j
            break
    if best is None and len(w.dates) > idx + 1:
        best = len(w.dates) - 1
    if best is None:
        return None
    return (w.closes[best] - w.closes[idx]) / w.closes[idx] * 100


# ── Find control cases (V3 HIGHs that correctly mean-reverted) ──────────────

def find_correct_v3_highs(n=2, score_min=85, ret_max=-3.0):
    """V3 (id=3) HIGH peaks where 30d return was actually negative."""
    v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
    if not v3:
        return []
    cands = list(Score.select()
                 .where((Score.version == v3) & (Score.overall >= score_min) &
                        (Score.date >= date(2025, 6, 1)) & (Score.date <= date(2025, 12, 31)))
                 .order_by(Score.overall.desc())
                 .limit(400))
    out = []
    for s in cands:
        if len(out) >= n:
            break
        sym = str(s.symbol_id)  # FK column value, not the resolved Stock object
        ph_anchor = PriceHistory.get_or_none(
            (PriceHistory.symbol == sym) & (PriceHistory.date == s.date))
        if not ph_anchor:
            continue
        target_d = s.date + timedelta(days=30)
        ph_fwd = (PriceHistory.select()
                  .where((PriceHistory.symbol == sym) &
                         (PriceHistory.date >= target_d))
                  .order_by(PriceHistory.date).first())
        if not ph_fwd:
            continue
        ret = (float(ph_fwd.close) - float(ph_anchor.close)) / float(ph_anchor.close) * 100
        if ret <= ret_max:
            out.append((sym, s.date, f'V3-CORRECT-HIGH (s={s.overall})', 'fadeable',
                        round(ret, 1)))
    return out


# ── Probe a single case ─────────────────────────────────────────────────────

def probe_one(symbol, target, label, expected, prefilled_ret=None):
    w = load_window(symbol, target)
    if not w.dates:
        return None
    if target not in w.dates:
        # find closest trading day on or before target
        prior = [d for d in w.dates if d <= target]
        if not prior:
            return None
        target = prior[-1]
    idx = w.dates.index(target)

    ext = extension_series(w)
    mat = maturity_series(ext, span=20)
    vel = velocity_series(ext, lag=5)
    accel = acceleration_series(vel, lag=5)
    saccel = smooth_acceleration(accel, span=5)

    ret30 = prefilled_ret if prefilled_ret is not None else fwd_return(w, idx, 30)

    return {
        'symbol': symbol,
        'date': target,
        'label': label,
        'expected': expected,
        'price': w.closes[idx],
        'ema50': w.ema50s[idx],
        'ext_pct': ext[idx] * 100,
        'maturity': mat[idx],
        'vel_5d_pct': vel[idx] * 100 if vel[idx] is not None else None,
        'accel_5d_pct': accel[idx] * 100 if accel[idx] is not None else None,
        'smooth_accel_pct': saccel[idx] * 100 if saccel[idx] is not None else None,
        'rsi': w.rsis[idx],
        'stoch': w.stochs[idx],
        'bb_pct': w.bb_pcts[idx],
        'ret30': ret30,
    }


# ── Pretty print ────────────────────────────────────────────────────────────

def fmt(v, w=7, prec=2):
    if v is None:
        return ' ' * (w - 2) + '--'
    if isinstance(v, float):
        return f'{v:>{w}.{prec}f}'
    return f'{str(v):>{w}}'


def print_results(rows):
    print()
    print('=' * 142)
    print(f'{"label":<32} {"symbol":<6} {"date":<11} '
          f'{"ext%":>7} {"matur":>7} {"vel5%":>7} {"acc5%":>7} {"sacc%":>7} '
          f'{"RSI":>6} {"Stoch":>7} {"BB%":>6} {"ret30%":>9} {"expected":<25}')
    print('-' * 142)
    for r in rows:
        if r is None:
            continue
        print(f'{r["label"]:<32} {r["symbol"]:<6} {str(r["date"]):<11} '
              f'{fmt(r["ext_pct"])} {fmt(r["maturity"], prec=3)} '
              f'{fmt(r["vel_5d_pct"])} {fmt(r["accel_5d_pct"])} {fmt(r["smooth_accel_pct"])} '
              f'{fmt(r["rsi"], w=6, prec=1)} {fmt(r["stoch"], w=7, prec=1)} '
              f'{fmt(r["bb_pct"], w=6, prec=2) if r["bb_pct"] is not None else "    --"} '
              f'{fmt(r["ret30"], w=9, prec=1)} {r["expected"]:<25}')
    print('=' * 142)
    print()
    print('Legend:')
    print('  ext%       = (close - ema50) / ema50, %  — current extension above EMA50')
    print('  matur      = EMA20 of tanh(8 * ext), clipped to >=0 -- smooth trend maturity in [0,1]')
    print('  vel5%      = ext(t) - ext(t-5), %  — 5d change in extension')
    print('  acc5%      = vel5(t) - vel5(t-5), %  — 5d change in velocity (negative = decelerating)')
    print('  sacc%      = EMA5 of acc5  — smoothed acceleration, less noise')
    print('  ret30%     = realised 30d forward return  — what actually happened')
    print()
    print('Hypothesis test:')
    print('  - late-stage-continuation should cluster: matur >> 0, sacc% > 0  -> DO NOT FADE')
    print('  - maturity-collapsing should cluster:    matur dropping, sacc% < 0 sharply')
    print('  - fadeable (correct HIGH) should cluster: matur moderate, sacc% slightly negative')
    print()


# ── Full V3 scan ────────────────────────────────────────────────────────────

WIN_THRESHOLD_PCT = 1.0  # match assess_scores._WIN_THRESHOLD_PCT
VOL_LOOKBACK = 20        # days of pre-entry returns for realized vol estimate
SWING_K_VALUES = (1.0, 1.5, 2.0, 2.5)
FWD_WINDOW = 30          # trading days forward


def _realized_vol_pct(closes, idx, lookback=VOL_LOOKBACK):
    """Daily realized vol (stdev of simple returns) over the prior `lookback` days,
    expressed as percent. Returns None if not enough history."""
    if idx < lookback + 1:
        return None
    rets = []
    for j in range(idx - lookback + 1, idx + 1):
        prev = closes[j - 1]
        if prev <= 0:
            continue
        rets.append((closes[j] - prev) / prev)
    if len(rets) < max(5, lookback // 2):
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


def _swing_features(closes, idx, vol_pct, side='high', window=63):
    """Track adverse/favorable excursions and simulate path-dependent
    options-tradeable wins.

    side='high' -> SHORT thesis (puts).  Win = drop K*vol before rise M*vol.
    side='low'  -> LONG  thesis (calls). Win = rise K*vol before drop M*vol.

    Returns dict with raw/vol mae/mfe, favorable/adverse excursions in
    trade direction, per-K swing_win flags, and per (K, M, W) path outcomes."""
    if vol_pct is None or vol_pct <= 0:
        return None
    entry = closes[idx]
    end = min(len(closes), idx + 1 + window)
    fwd = closes[idx + 1:end]
    if len(fwd) < 5:
        return None

    running_min = entry
    running_max = entry
    day_min = None
    day_max = None
    for j, c in enumerate(fwd, start=1):
        if c < running_min:
            running_min = c
            day_min = j
        if c > running_max:
            running_max = c
            day_max = j

    mae_pct = (running_min - entry) / entry * 100  # signed: drop is negative
    mfe_pct = (running_max - entry) / entry * 100  # signed: rise is positive
    mae_vol = mae_pct / vol_pct
    mfe_vol = mfe_pct / vol_pct

    # Favorable/adverse in TRADE direction (always positive when good)
    if side == 'high':
        fav_vol = -mae_vol  # we want a drop -> mae (negative) becomes positive favorable
        adv_vol = mfe_vol   # rise hurts puts
        day_to_fav = day_min
        day_to_adv = day_max
    else:  # 'low'
        fav_vol = mfe_vol   # we want a rise
        adv_vol = -mae_vol  # drop hurts calls
        day_to_fav = day_max
        day_to_adv = day_min

    out = {
        'side': side,
        'vol_pct': vol_pct,
        'mae_pct': mae_pct,
        'mfe_pct': mfe_pct,
        'mae_vol': mae_vol,
        'mfe_vol': mfe_vol,
        'fav_vol': fav_vol,
        'adv_vol': adv_vol,
        'day_to_min': day_min,
        'day_to_max': day_max,
        'fav_before_adv': (day_to_fav is not None and day_to_adv is not None
                           and day_to_fav < day_to_adv),
        # Back-compat alias used by _print_mae_before_mfe
        'mae_before_mfe': (day_min is not None and day_max is not None and day_min < day_max),
    }

    # Per-K swing wins: first trading day favorable move of K*vol_pct from entry
    for K in SWING_K_VALUES:
        if side == 'high':
            target_close = entry * (1 - K * vol_pct / 100)
            hit_fn = (lambda c, t=target_close: c <= t)
        else:
            target_close = entry * (1 + K * vol_pct / 100)
            hit_fn = (lambda c, t=target_close: c >= t)
        first_hit = None
        for j, c in enumerate(fwd, start=1):
            if hit_fn(c):
                first_hit = j
                break
        out[f'swing_win_{K}'] = (first_hit is not None)
        out[f'days_to_{K}'] = first_hit

    # Path-dependent options-tradeable wins: drop K*vol BEFORE runup M*vol,
    # within window W. Simulates a long put with stop-loss at +M*vol.
    # We compute for several (K, M, W) triples used by the analysis printers.
    K_M_W_TRIPLES = [
        (1.0, 2.0, 21), (1.0, 2.0, 30), (1.0, 2.0, 42), (1.0, 2.0, 63),
        (1.0, 3.0, 21), (1.0, 3.0, 30), (1.0, 3.0, 42), (1.0, 3.0, 63),
        (1.0, 5.0, 21), (1.0, 5.0, 30), (1.0, 5.0, 42), (1.0, 5.0, 63),
        (1.5, 3.0, 30), (1.5, 3.0, 42), (1.5, 3.0, 63),
        (1.5, 5.0, 30), (1.5, 5.0, 42), (1.5, 5.0, 63),
        (2.0, 5.0, 30), (2.0, 5.0, 42), (2.0, 5.0, 63),
    ]
    for K, M, W in K_M_W_TRIPLES:
        if side == 'high':
            target_win = entry * (1 - K * vol_pct / 100)
            target_stop = entry * (1 + M * vol_pct / 100)
            win_hit = lambda c, t=target_win: c <= t
            stop_hit = lambda c, t=target_stop: c >= t
        else:
            target_win = entry * (1 + K * vol_pct / 100)
            target_stop = entry * (1 - M * vol_pct / 100)
            win_hit = lambda c, t=target_win: c >= t
            stop_hit = lambda c, t=target_stop: c <= t
        result = None  # 'win', 'stopout', 'expire'
        exit_day = None
        exit_close = None
        for j, c in enumerate(fwd[:W], start=1):
            if win_hit(c):
                result, exit_day, exit_close = 'win', j, c
                break
            if stop_hit(c):
                result, exit_day, exit_close = 'stopout', j, c
                break
        if result is None:
            result = 'expire'
            avail = fwd[:W]
            exit_day = len(avail)
            exit_close = avail[-1] if avail else entry
        prefix = f'path_K{K}_M{M}_W{W}'
        out[prefix] = result
        out[prefix + '_day'] = exit_day
        out[prefix + '_exit_ret'] = (exit_close - entry) / entry * 100  # %
    return out


def _bulk_features_for_symbol(symbol, peak_specs):
    """Return {date: feature_dict} for the requested peaks of one symbol.

    peak_specs: list of (date, side, score) tuples. side in {'high','low'}.

    Bulk-loads PriceHistory + Indicator once. Computes ext/maturity/velocity/
    acceleration series across the entire history then plucks values at the
    peak dates. Also returns 30d forward return for each peak.
    """
    if not peak_specs:
        return {}

    peak_dates = [p[0] for p in peak_specs]
    side_by_date = {p[0]: p[1] for p in peak_specs}
    score_by_date = {p[0]: p[2] for p in peak_specs}
    earliest = min(peak_dates) - timedelta(days=120)
    latest = max(peak_dates) + timedelta(days=100)  # ~63 trading days forward

    ph = list(PriceHistory.select()
              .where((PriceHistory.symbol == symbol) &
                     (PriceHistory.date >= earliest) & (PriceHistory.date <= latest))
              .order_by(PriceHistory.date))
    ind = {i.date: i for i in Indicator.select()
           .where((Indicator.symbol == symbol) &
                  (Indicator.date >= earliest) & (Indicator.date <= latest))}

    dates, closes, ema50s, rsis, stochs = [], [], [], [], []
    ema200s, bb_pcts, macd_hists, vols = [], [], [], []
    for row in ph:
        i = ind.get(row.date)
        if not i or i.ema_50 is None:
            continue
        dates.append(row.date)
        closes.append(float(row.close))
        ema50s.append(float(i.ema_50))
        rsis.append(float(i.rsi) if i.rsi is not None else None)
        stochs.append(float(i.stoch) if i.stoch is not None else None)
        ema200s.append(float(i.ema_200) if i.ema_200 is not None else None)
        macd_hists.append(float(i.macd_hist) if i.macd_hist is not None else None)
        if i.upper_band and i.lower_band:
            u, l = float(i.upper_band), float(i.lower_band)
            br = u - l
            bb_pcts.append((float(row.close) - l) / br if br > 0 else None)
        else:
            bb_pcts.append(None)
        vols.append(float(row.volume) if getattr(row, 'volume', None) is not None else None)

    if not dates:
        return {}

    ext = [(c - e) / e for c, e in zip(closes, ema50s)]
    mat = maturity_series(ext, span=20)
    vel = velocity_series(ext, lag=5)
    accel = acceleration_series(vel, lag=5)
    saccel = smooth_acceleration(accel, span=5)

    # date -> idx
    date_idx = {d: i for i, d in enumerate(dates)}

    out = {}
    for pd_ in peak_dates:
        # snap to nearest trading day on/before
        if pd_ in date_idx:
            idx = date_idx[pd_]
        else:
            prior = [d for d in dates if d <= pd_]
            if not prior:
                continue
            idx = date_idx[prior[-1]]

        # forward return: ~30 calendar days out
        target_d = dates[idx] + timedelta(days=30)
        fwd_idx = None
        for j in range(idx + 1, len(dates)):
            if dates[j] >= target_d:
                fwd_idx = j
                break
        if fwd_idx is None:
            continue  # not enough forward data
        ret30 = (closes[fwd_idx] - closes[idx]) / closes[idx] * 100

        # Volatility-normalized swing features
        vol_pct = _realized_vol_pct(closes, idx)
        side = side_by_date[pd_]
        score = score_by_date[pd_]
        sw = _swing_features(closes, idx, vol_pct, side=side)

        # Days since the prior peak of the same side for this symbol — proxy for
        # signal freshness vs cluster of repeated signals
        prior_same_side = [pp for pp in peak_dates
                           if pp < pd_ and side_by_date[pp] == side]
        days_since_same_side = (pd_ - max(prior_same_side)).days if prior_same_side else None

        ema200 = ema200s[idx]
        bb_pct = bb_pcts[idx]
        macdh = macd_hists[idx]
        ema200_dist = ((closes[idx] - ema200) / ema200 * 100) if ema200 else None

        # 5d & 20d trailing returns at the entry bar (entry-time momentum context)
        ret_5d_prior = ((closes[idx] - closes[idx - 5]) / closes[idx - 5] * 100) if idx >= 5 else None
        ret_20d_prior = ((closes[idx] - closes[idx - 20]) / closes[idx - 20] * 100) if idx >= 20 else None

        # Volume-on-entry vs 50d avg (only if we have enough history)
        if idx >= 50 and vols[idx] is not None:
            recent_vols = [v for v in vols[idx - 50:idx] if v is not None]
            avg_vol_50d = sum(recent_vols) / len(recent_vols) if recent_vols else None
            vol_ratio = vols[idx] / avg_vol_50d if avg_vol_50d else None
        else:
            vol_ratio = None

        row = {
            'symbol': symbol,
            'date': pd_,
            'side': side,
            'score': score,
            'band': _score_band(score),
            'ext': ext[idx] * 100,
            'matur': abs(mat[idx]),         # signed -> magnitude (works for both sides)
            'matur_signed': mat[idx],       # keep direction for inspection
            'vel5': vel[idx] * 100 if vel[idx] is not None else None,
            'sacc': saccel[idx] * 100 if saccel[idx] is not None else None,
            'rsi': rsis[idx],
            'stoch': stochs[idx],
            'bb_pct': bb_pct,
            'macdh': macdh,
            'ema200_dist': ema200_dist,
            'ret_5d_prior': ret_5d_prior,
            'ret_20d_prior': ret_20d_prior,
            'vol_ratio': vol_ratio,
            'days_since_same_side': days_since_same_side,
            'ret30': ret30,
            'vol_pct': vol_pct,
        }
        if sw is not None:
            row.update(sw)
        out[pd_] = row
    return out


def _quintile_bounds(values):
    """Return 4 cut points dividing values into 5 quintiles. Robust to dupes."""
    s = sorted(v for v in values if v is not None)
    if len(s) < 5:
        return None
    return [s[len(s) * k // 5] for k in (1, 2, 3, 4)]


def _bin(v, bounds):
    if v is None or bounds is None:
        return None
    for i, b in enumerate(bounds):
        if v < b:
            return i
    return len(bounds)  # last bucket


def _stats(rets, side='high'):
    """Summary stats for a list of forward returns.

    side='high' (default): a 'win' is ret30 < -WIN_THRESHOLD_PCT (price dropped — short worked).
    side='low':           a 'win' is ret30 > +WIN_THRESHOLD_PCT (price rose  — long  worked).
    """
    if not rets:
        return (0, 0.0, 0.0, 0.0)
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n
    std = math.sqrt(var)
    if side == 'low':
        wins = sum(1 for r in rets if r > WIN_THRESHOLD_PCT)
    else:
        wins = sum(1 for r in rets if r < -WIN_THRESHOLD_PCT)
    wr = wins / n * 100
    return (n, mean, std, wr)


def _print_marginal(name, rows, key, n_bins=5, side='high'):
    """Print N / mean ret30 / std / WR by quintile of `key`.
    Pass side='low' so WR is computed for LONG winners (ret30 > +1%)."""
    vals = [r[key] for r in rows if r.get(key) is not None]
    bounds = _quintile_bounds(vals)
    if bounds is None:
        return
    buckets = [[] for _ in range(n_bins)]
    for r in rows:
        b = _bin(r.get(key), bounds)
        if b is not None:
            buckets[b].append(r['ret30'])

    print()
    print(f'  {name} quintiles  (cuts: {", ".join(f"{b:+.2f}" for b in bounds)})')
    print(f'    {"q":<3} {"N":>6} {"mean ret30":>12} {"std ret30":>11} {"WR30":>7}')
    for i, rs in enumerate(buckets):
        n, mean, std, wr = _stats(rs, side=side)
        print(f'    {i+1:<3} {n:>6} {mean:>+11.2f}% {std:>10.2f}% {wr:>6.1f}%')


def _print_joint(rows, key_x, key_y, n=5):
    """2D quintile heatmap of mean ret30 (x) and N / std / WR underneath."""
    xs = [r[key_x] for r in rows if r.get(key_x) is not None and r.get(key_y) is not None]
    ys = [r[key_y] for r in rows if r.get(key_x) is not None and r.get(key_y) is not None]
    bx = _quintile_bounds(xs)
    by = _quintile_bounds(ys)
    if bx is None or by is None:
        return

    grid_rets = [[[] for _ in range(n)] for _ in range(n)]
    for r in rows:
        ix = _bin(r.get(key_x), bx)
        iy = _bin(r.get(key_y), by)
        if ix is None or iy is None:
            continue
        grid_rets[iy][ix].append(r['ret30'])

    print()
    print(f'  Joint heatmap: {key_x} (x) x {key_y} (y)  — each cell shows mean ret30 / N / WR')
    print(f'    {key_x} cuts: {", ".join(f"{b:+.2f}" for b in bx)}')
    print(f'    {key_y} cuts: {", ".join(f"{b:+.2f}" for b in by)}')
    print()
    header = f'    {key_y}\\{key_x}  ' + ''.join(f'{f"q{ix+1}":>16}' for ix in range(n))
    print(header)
    for iy in range(n - 1, -1, -1):  # high y at top
        row_label = f'    q{iy+1}        '
        line_mean = row_label + ''.join(
            f'{(sum(grid_rets[iy][ix])/len(grid_rets[iy][ix])):>+15.1f}%' if grid_rets[iy][ix]
            else f'{"--":>16}' for ix in range(n)
        )
        line_n = ' ' * 14 + ''.join(
            f'{f"N={len(grid_rets[iy][ix])}":>16}' for ix in range(n)
        )
        line_wr = ' ' * 14 + ''.join(
            f'{f"WR={sum(1 for r in grid_rets[iy][ix] if r < -WIN_THRESHOLD_PCT)/len(grid_rets[iy][ix])*100:.0f}%":>16}'
            if grid_rets[iy][ix] else f'{"":>16}' for ix in range(n)
        )
        print(line_mean)
        print(line_n)
        print(line_wr)
        print()


def _print_high_variance_cells(rows, key_x, key_y, min_n=20, n=5):
    """List the cells with highest std(ret30) — these are 'do not bet' regions."""
    xs = [r[key_x] for r in rows if r.get(key_x) is not None and r.get(key_y) is not None]
    ys = [r[key_y] for r in rows if r.get(key_x) is not None and r.get(key_y) is not None]
    bx = _quintile_bounds(xs)
    by = _quintile_bounds(ys)
    grid_rets = [[[] for _ in range(n)] for _ in range(n)]
    for r in rows:
        ix = _bin(r.get(key_x), bx)
        iy = _bin(r.get(key_y), by)
        if ix is None or iy is None:
            continue
        grid_rets[iy][ix].append(r['ret30'])

    cells = []
    for iy in range(n):
        for ix in range(n):
            rs = grid_rets[iy][ix]
            if len(rs) < min_n:
                continue
            ns, mean, std, wr = _stats(rs)
            cells.append((iy, ix, ns, mean, std, wr))
    cells.sort(key=lambda c: -c[4])  # highest std first

    print()
    print(f'  Top high-variance cells (min N={min_n}):')
    print(f'    {"y":>3} {"x":>3} {"N":>6} {"mean":>10} {"std":>10} {"WR":>7}  comment')
    for iy, ix, ns, mean, std, wr in cells[:8]:
        comment = ''
        if std > 25 and abs(mean) < 5:
            comment = 'BIMODAL — model should regress to neutral here'
        elif wr < 50:
            comment = 'NEGATIVE EDGE — V3 is wrong more than right'
        print(f'    q{iy+1}  q{ix+1} {ns:>6} {mean:>+9.1f}% {std:>9.1f}% {wr:>6.1f}%  {comment}')


def _co_location_test(rows):
    """Find cells containing both clearly-correct and clearly-wrong V3 HIGHs."""
    print()
    print('  Co-location test: cells where V3 is fundamentally guessing')
    print('  (a "guessing" cell has both ret30 < -10% AND ret30 > +50% in it)')
    xs = [r['ext'] for r in rows if r.get('ext') is not None and r.get('sacc') is not None]
    ys = [r['sacc'] for r in rows if r.get('ext') is not None and r.get('sacc') is not None]
    bx = _quintile_bounds(xs)
    by = _quintile_bounds(ys)
    grid = [[[] for _ in range(5)] for _ in range(5)]
    for r in rows:
        ix = _bin(r.get('ext'), bx)
        iy = _bin(r.get('sacc'), by)
        if ix is None or iy is None:
            continue
        grid[iy][ix].append(r)

    flagged = 0
    for iy in range(5):
        for ix in range(5):
            cell = grid[iy][ix]
            if len(cell) < 10:
                continue
            has_correct = any(r['ret30'] < -10 for r in cell)
            has_continuation = any(r['ret30'] > 50 for r in cell)
            if has_correct and has_continuation:
                flagged += 1
                n_strong_fade = sum(1 for r in cell if r['ret30'] < -10)
                n_strong_cont = sum(1 for r in cell if r['ret30'] > 50)
                ns, mean, std, wr = _stats([r['ret30'] for r in cell])
                print(f'    cell (ext q{ix+1}, sacc q{iy+1}): N={ns} mean={mean:+.1f}% std={std:.1f}% '
                      f'WR={wr:.0f}% | strong fades={n_strong_fade} continuations={n_strong_cont}')
    if flagged == 0:
        print('    No cells found with both <-10% and >+50% returns. Features may actually separate.')
    else:
        print(f'    {flagged} cells flagged. These are regions where technical features alone cannot')
        print(f'    predict direction — score should regress toward 50 here, not fire HIGH boldly.')


def _print_swing_overview(rows):
    """Headline: WR by K-multiple of vol, plus mean MAE/MFE in vol-units."""
    print()
    print('=' * 80)
    print('Volatility-normalized swing-win headline')
    print('=' * 80)
    valid = [r for r in rows if r.get('vol_pct') is not None and 'mae_vol' in r]
    print(f'  N usable for swing analysis: {len(valid)} / {len(rows)}')
    if not valid:
        return

    mean_vol = sum(r['vol_pct'] for r in valid) / len(valid)
    print(f'  Mean pre-entry daily vol across V3 HIGH peaks: {mean_vol:.2f}%')

    mean_mae_vol = sum(r['mae_vol'] for r in valid) / len(valid)
    mean_mfe_vol = sum(r['mfe_vol'] for r in valid) / len(valid)
    print(f'  Mean MAE (favorable for HIGH): {mean_mae_vol:+.2f} vol-units')
    print(f'  Mean MFE (unfavorable):        {mean_mfe_vol:+.2f} vol-units')
    print(f'  -> On average a HIGH peak swings down ~{abs(mean_mae_vol):.1f}s AND up ~{mean_mfe_vol:.1f}s.')

    n_first_down = sum(1 for r in valid if r.get('mae_before_mfe'))
    print(f'  MAE-before-MFE (downside hit first): {n_first_down}/{len(valid)} '
          f'= {n_first_down/len(valid)*100:.1f}% — proxy for "tradeable on entry"')

    print()
    print('  Swing-WR by drawdown threshold (K * pre-entry daily vol):')
    print(f'    {"K":>5} {"WR":>7} {"mean days-to-target":>22} {"median days":>14}')
    for K in SWING_K_VALUES:
        hits = [r for r in valid if r.get(f'swing_win_{K}')]
        wr = len(hits) / len(valid) * 100
        days_list = [r[f'days_to_{K}'] for r in hits if r[f'days_to_{K}'] is not None]
        mean_days = sum(days_list) / len(days_list) if days_list else 0
        median_days = sorted(days_list)[len(days_list) // 2] if days_list else 0
        print(f'    {K:>5.1f} {wr:>6.1f}% {mean_days:>21.1f}  {median_days:>13}')


def _print_swing_marginal(name, rows, key, K=2.0, n_bins=5):
    """Swing-WR @ K-vol by quintile of `key`. Plus mean MAE/MFE in vol-units."""
    valid = [r for r in rows if r.get('vol_pct') is not None and r.get(key) is not None
             and 'mae_vol' in r]
    if len(valid) < 50:
        return
    vals = [r[key] for r in valid]
    bounds = _quintile_bounds(vals)
    if bounds is None:
        return
    buckets = [[] for _ in range(n_bins)]
    for r in valid:
        b = _bin(r.get(key), bounds)
        if b is not None:
            buckets[b].append(r)

    print()
    print(f'  Swing-WR by {name} quintiles  (cuts: {", ".join(f"{b:+.2f}" for b in bounds)})')
    print(f'    {"q":<3} {"N":>5} {"WR@K=1.0":>10} {"WR@K=1.5":>10} {"WR@K=2.0":>10} '
          f'{"WR@K=2.5":>10} {"mean MAE_vol":>14} {"mean MFE_vol":>14}')
    for i, rs in enumerate(buckets):
        if not rs:
            continue
        n = len(rs)
        wrs = []
        for k in SWING_K_VALUES:
            wins = sum(1 for r in rs if r.get(f'swing_win_{k}'))
            wrs.append(wins / n * 100)
        mean_mae = sum(r['mae_vol'] for r in rs) / n
        mean_mfe = sum(r['mfe_vol'] for r in rs) / n
        print(f'    {i+1:<3} {n:>5} {wrs[0]:>9.1f}% {wrs[1]:>9.1f}% {wrs[2]:>9.1f}% {wrs[3]:>9.1f}% '
              f'{mean_mae:>+13.2f}s {mean_mfe:>+13.2f}s')


def _print_swing_joint(rows, key_x, key_y, K=2.0, n=5):
    """2D heatmap: swing-WR @ K + mean days-to-target."""
    valid = [r for r in rows if r.get('vol_pct') is not None
             and r.get(key_x) is not None and r.get(key_y) is not None
             and f'swing_win_{K}' in r]
    xs = [r[key_x] for r in valid]
    ys = [r[key_y] for r in valid]
    bx = _quintile_bounds(xs)
    by = _quintile_bounds(ys)
    if bx is None or by is None:
        return
    grid = [[[] for _ in range(n)] for _ in range(n)]
    for r in valid:
        ix = _bin(r[key_x], bx)
        iy = _bin(r[key_y], by)
        if ix is None or iy is None:
            continue
        grid[iy][ix].append(r)

    print()
    print(f'  Joint swing-WR @ K={K}: {key_x} (x) x {key_y} (y) — WR / mean days / N')
    print(f'    {key_x} cuts: {", ".join(f"{b:+.2f}" for b in bx)}')
    print(f'    {key_y} cuts: {", ".join(f"{b:+.2f}" for b in by)}')
    print()
    header = f'    {key_y}\\{key_x}  ' + ''.join(f'{f"q{ix+1}":>16}' for ix in range(n))
    print(header)
    for iy in range(n - 1, -1, -1):
        row_label = f'    q{iy+1}        '
        line_wr = ''
        line_dt = ''
        line_n = ''
        for ix in range(n):
            cell = grid[iy][ix]
            if not cell:
                line_wr += f'{"--":>16}'
                line_dt += f'{"":>16}'
                line_n += f'{"":>16}'
                continue
            wins = [r for r in cell if r.get(f'swing_win_{K}')]
            wr = len(wins) / len(cell) * 100
            days = [r[f'days_to_{K}'] for r in wins if r[f'days_to_{K}'] is not None]
            mean_d = (sum(days) / len(days)) if days else 0
            line_wr += f'{f"WR={wr:.0f}%":>16}'
            line_dt += f'{f"d={mean_d:.0f}":>16}'
            line_n += f'{f"N={len(cell)}":>16}'
        print(row_label + line_wr)
        print(' ' * 14 + line_dt)
        print(' ' * 14 + line_n)
        print()


def _print_window_tightness(rows):
    """How does WR change as we tighten/extend the swing window?
    Short windows (5-14d) = short DTE options. Long windows (42-63d) = 60-90 cal DTE."""
    valid = [r for r in rows if r.get('vol_pct') is not None and 'mae_vol' in r]
    if not valid:
        return
    print()
    print('  Window-tightness: WR if swing must hit K*vol within W trading days')
    print('  (W=21~30d: short DTE. W=42d~60 cal DTE. W=63d~90 cal DTE.)')
    print('  NOTE: this counts ANY hit within window — does NOT enforce path order.')
    print(f'    {"K":>5} {"W=5d":>8} {"W=7d":>8} {"W=14d":>8} {"W=21d":>8} {"W=30d":>8} {"W=42d":>8} {"W=63d":>8}')
    for K in SWING_K_VALUES:
        windows = (5, 7, 14, 21, 30, 42, 63)
        wrs = []
        for W in windows:
            hits = sum(1 for r in valid
                       if r.get(f'days_to_{K}') is not None and r[f'days_to_{K}'] <= W)
            wrs.append(hits / len(valid) * 100)
        line = f'    {K:>5.1f}' + ''.join(f' {w:>7.1f}%' for w in wrs)
        print(line)


def _print_path_dependent_options(rows):
    """Path-dependent simulation: WIN if stock drops K*vol BEFORE running M*vol,
    within W trading days. This is the realistic options-trade outcome.

    M is the stop-loss tolerance — the runup the put can survive before
    delta loss makes it impractical to hold. Larger M = more runup tolerated
    (deeper-pocket / more capital risk), smaller M = tighter risk control.
    """
    valid = [r for r in rows if r.get('vol_pct') is not None
             and any(k.startswith('path_') for k in r.keys())]
    if not valid:
        return
    print()
    print('=' * 80)
    print('Path-dependent options simulation: WIN = drop K*vol BEFORE runup M*vol')
    print('=' * 80)
    print('  Win  = take profit hit before stop')
    print('  Stop = stop-loss (M*vol runup) hit before take-profit')
    print('  Exp  = window expired with neither hit')
    print()
    print('  K     M       window     N    win%   stop%    exp%   win/(win+stop)')
    print('  ' + '-' * 72)
    triples = [
        (1.0, 2.0, 21, '21d (~30 cal DTE)'),
        (1.0, 2.0, 30, '30d (~42 cal DTE)'),
        (1.0, 2.0, 42, '42d (~60 cal DTE)'),
        (1.0, 2.0, 63, '63d (~90 cal DTE)'),
        (1.0, 3.0, 21, '21d (~30 cal DTE)'),
        (1.0, 3.0, 30, '30d (~42 cal DTE)'),
        (1.0, 3.0, 42, '42d (~60 cal DTE)'),
        (1.0, 3.0, 63, '63d (~90 cal DTE)'),
        (1.0, 5.0, 21, '21d (~30 cal DTE)'),
        (1.0, 5.0, 30, '30d (~42 cal DTE)'),
        (1.0, 5.0, 42, '42d (~60 cal DTE)'),
        (1.0, 5.0, 63, '63d (~90 cal DTE)'),
        (1.5, 3.0, 30, '30d (~42 cal DTE)'),
        (1.5, 3.0, 42, '42d (~60 cal DTE)'),
        (1.5, 3.0, 63, '63d (~90 cal DTE)'),
        (1.5, 5.0, 30, '30d (~42 cal DTE)'),
        (1.5, 5.0, 42, '42d (~60 cal DTE)'),
        (1.5, 5.0, 63, '63d (~90 cal DTE)'),
        (2.0, 5.0, 30, '30d (~42 cal DTE)'),
        (2.0, 5.0, 42, '42d (~60 cal DTE)'),
        (2.0, 5.0, 63, '63d (~90 cal DTE)'),
    ]
    last_K = None
    for K, M, W, label in triples:
        key = f'path_K{K}_M{M}_W{W}'
        outcomes = [r.get(key) for r in valid if r.get(key) is not None]
        n = len(outcomes)
        if n == 0:
            continue
        wins = sum(1 for o in outcomes if o == 'win')
        stops = sum(1 for o in outcomes if o == 'stopout')
        exps = sum(1 for o in outcomes if o == 'expire')
        win_pct = wins / n * 100
        stop_pct = stops / n * 100
        exp_pct = exps / n * 100
        edge = wins / (wins + stops) * 100 if (wins + stops) > 0 else 0.0
        sep = ''
        if last_K is not None and K != last_K:
            print('  ' + '-' * 72)
        last_K = K
        print(f'  {K:<5.1f} {M:<5.1f} {label:<20} {n:>5} {win_pct:>6.1f}% {stop_pct:>6.1f}% '
              f'{exp_pct:>6.1f}%  {edge:>13.1f}%')


def _score_band(score):
    """Bucket a peak score into a CLAUDE.md-style band.
    LOW side split finer (16-19 / 20-22 / 23-25) per the working-edge investigation."""
    if score >= 90: return '90+'
    if score >= 85: return '85-89'
    if score >= 80: return '80-84'
    if score >= 75: return '75-79'
    if score >= 70: return '70-74'
    if score <= 5:  return '<=5'
    if score <= 15: return '6-15'
    if score <= 19: return '16-19'
    if score <= 22: return '20-22'
    return '23-25'


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_put(S, K, T, sigma, r=0.04):
    """Black-Scholes European put. T in years, sigma annualized."""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bs_call(S, K, T, sigma, r=0.04):
    """Black-Scholes European call. T in years, sigma annualized."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _payout_cell(rows_subset, K, M, W, side):
    """Compute one (K, M, W) EV cell from `rows_subset` (already filtered to one side).
    Returns dict with summary stats, or None if empty."""
    prefix = f'path_K{K}_M{M}_W{W}'
    cell = [r for r in rows_subset if r.get(prefix) is not None]
    if not cell:
        return None
    T_init = W / 252.0
    bs = _bs_put if side == 'high' else _bs_call
    win_pnls, stop_pnls, exp_pnls = [], [], []
    win_favs, stop_advs = [], []
    for r in cell:
        entry = 1.0  # PnL% scale-invariant
        sigma_ann = r['vol_pct'] / 100.0 * math.sqrt(252)
        P0 = bs(S=entry, K=entry, T=T_init, sigma=sigma_ann)
        if P0 <= 0:
            continue
        outcome = r[prefix]
        day = r.get(prefix + '_day') or W
        exit_ret = r.get(prefix + '_exit_ret') or 0.0
        S_exit = entry * (1 + exit_ret / 100.0)
        T_rem = max(0.0, (W - day) / 252.0)
        if outcome == 'expire':
            T_rem = 0.0
        P_exit = bs(S=S_exit, K=entry, T=T_rem, sigma=sigma_ann)
        pnl_pct = (P_exit - P0) / P0 * 100.0
        if outcome == 'win':
            win_pnls.append(pnl_pct)
            win_favs.append(r.get('fav_vol', 0.0))
        elif outcome == 'stopout':
            stop_pnls.append(pnl_pct)
            stop_advs.append(r.get('adv_vol', 0.0))
        else:
            exp_pnls.append(pnl_pct)
    n = len(win_pnls) + len(stop_pnls) + len(exp_pnls)
    if n == 0:
        return None
    p_win = len(win_pnls) / n
    p_stop = len(stop_pnls) / n
    p_exp = len(exp_pnls) / n
    mean_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
    mean_stop = sum(stop_pnls) / len(stop_pnls) if stop_pnls else 0.0
    mean_exp = sum(exp_pnls) / len(exp_pnls) if exp_pnls else 0.0
    ev = p_win * mean_win + p_stop * mean_stop + p_exp * mean_exp
    return {
        'K': K, 'M': M, 'W': W, 'n': n, 'side': side,
        'p_win': p_win, 'p_stop': p_stop, 'p_exp': p_exp,
        'mean_win': mean_win, 'mean_stop': mean_stop, 'mean_exp': mean_exp,
        'ev': ev,
        'heat_w': sum(win_favs) / len(win_favs) if win_favs else 0.0,
        'heat_s': sum(stop_advs) / len(stop_advs) if stop_advs else 0.0,
    }


_PAYOUT_TRIPLES = [
    (1.0, 2.0, 21), (1.0, 2.0, 30), (1.0, 2.0, 42), (1.0, 2.0, 63),
    (1.0, 3.0, 21), (1.0, 3.0, 30), (1.0, 3.0, 42), (1.0, 3.0, 63),
    (1.0, 5.0, 21), (1.0, 5.0, 30), (1.0, 5.0, 42), (1.0, 5.0, 63),
    (1.5, 3.0, 30), (1.5, 3.0, 42), (1.5, 3.0, 63),
    (1.5, 5.0, 30), (1.5, 5.0, 42), (1.5, 5.0, 63),
    (2.0, 5.0, 30), (2.0, 5.0, 42), (2.0, 5.0, 63),
]


def _print_one_side_payout(rows_subset, side, label):
    if not rows_subset:
        print(f'  ({label}: no rows)')
        return None
    print()
    print('=' * 92)
    print(f'{label}: BS option-PnL EV per (K, M, W) cell    [N={len(rows_subset)}]')
    print('=' * 92)
    print('  K     M     W     N    p_win  p_stop  p_exp   '
          'win_pnl  stop_pnl  exp_pnl    EV%    heat(W/S)')
    print('  ' + '-' * 90)
    cells = []
    last_K = None
    for K, M, W in _PAYOUT_TRIPLES:
        c = _payout_cell(rows_subset, K, M, W, side)
        if c is None:
            continue
        cells.append(c)
        if last_K is not None and K != last_K:
            print('  ' + '-' * 90)
        last_K = K
        print(f'  {K:<5.1f} {M:<5.1f} {W:<5} {c["n"]:>5} '
              f'{c["p_win"]*100:>6.1f}% {c["p_stop"]*100:>6.1f}% {c["p_exp"]*100:>6.1f}%  '
              f'{c["mean_win"]:>+7.1f}% {c["mean_stop"]:>+8.1f}% {c["mean_exp"]:>+7.1f}%  '
              f'{c["ev"]:>+6.1f}%   {c["heat_w"]:>+4.2f}/{c["heat_s"]:>+4.2f}')
    if not cells:
        return None
    print()
    print(f'  Top 5 {label} cells by EV:')
    for c in sorted(cells, key=lambda x: -x['ev'])[:5]:
        print(f'    K={c["K"]:.1f}  M={c["M"]:.1f}  W={c["W"]:>3}d  '
              f'EV={c["ev"]:+.1f}%  (p_win={c["p_win"]*100:.0f}%  '
              f'win_pnl={c["mean_win"]:+.0f}%  stop_pnl={c["mean_stop"]:+.0f}%)')
    return max(cells, key=lambda x: x['ev'])


def _print_band_breakdown(rows, side, best_cell, label):
    """Show how EV varies across score bands for the best (K, M, W) cell."""
    if best_cell is None:
        return
    K, M, W = best_cell['K'], best_cell['M'], best_cell['W']
    if side == 'high':
        bands = ['70-74', '75-79', '80-84', '85-89', '90+']
    else:
        bands = ['23-25', '20-22', '16-19', '6-15', '<=5']
    print()
    print(f'  {label} -- score-band breakdown @ best cell K={K} M={M} W={W}d:')
    print(f'    {"band":<8} {"N":>5} {"p_win":>7} {"p_stop":>7} {"win_pnl":>9} '
          f'{"stop_pnl":>10} {"EV%":>8}')
    for b in bands:
        subset = [r for r in rows if r.get('side') == side and r.get('band') == b]
        c = _payout_cell(subset, K, M, W, side)
        if c is None:
            print(f'    {b:<8} {"--":>5}')
            continue
        print(f'    {b:<8} {c["n"]:>5} {c["p_win"]*100:>6.1f}% {c["p_stop"]*100:>6.1f}% '
              f'{c["mean_win"]:>+8.1f}% {c["mean_stop"]:>+9.1f}% {c["ev"]:>+7.1f}%')


def _print_payout_asymmetry(rows):
    """BS option-PnL EV per (K, M, W) cell, split by trade side AND by score band.

    HIGH peaks (>=70) -> SHORT thesis -> ATM puts
    LOW  peaks (<=25) -> LONG  thesis -> ATM calls

    For each peak in a cell:
      - Strike = entry, T_init = W trading days, sigma_ann = vol_pct * sqrt(252)/100
      - P0 = BS(S=entry, K=entry, T=T_init, sigma=sigma_ann)
      - At exit: T_rem=(W-day)/252 (0 if expire), S_exit = entry*(1+exit_ret/100)
      - PnL% = (P_exit - P0) / P0 * 100
    Heat columns: mean favorable excursion (vol) on winners /
                  mean adverse excursion (vol) on stopouts.
    """
    valid = [r for r in rows
             if r.get('vol_pct') is not None
             and any(k.startswith('path_') and k.endswith('_exit_ret') for k in r.keys())]
    if not valid:
        return
    highs = [r for r in valid if r.get('side') == 'high']
    lows  = [r for r in valid if r.get('side') == 'low']
    best_high = _print_one_side_payout(highs, 'high', 'PUTS  (V3 HIGH peaks)')
    best_low  = _print_one_side_payout(lows,  'low',  'CALLS (V3 LOW peaks)')
    if best_high or best_low:
        print()
        print('-' * 92)
        print('Score-band breakdown of best cells')
        print('-' * 92)
        _print_band_breakdown(rows, 'high', best_high, 'PUTS')
        _print_band_breakdown(rows, 'low',  best_low,  'CALLS')


def _print_mae_before_mfe(rows):
    """Conditional WR: among HIGHs where downside hit BEFORE upside, what's the swing-WR?
    These are the only signals that are tradeable for options."""
    valid = [r for r in rows if r.get('vol_pct') is not None and 'mae_before_mfe' in r]
    if not valid:
        return
    tradeable = [r for r in valid if r['mae_before_mfe']]
    untradeable = [r for r in valid if not r['mae_before_mfe']]
    print()
    print('  Conditional on MAE-before-MFE (downside hit FIRST, ie tradeable for options):')
    print(f'    {"group":<35} {"N":>6} {"WR@1.0":>9} {"WR@1.5":>9} {"WR@2.0":>9} '
          f'{"mean MAE":>10} {"mean MFE":>10}')
    for label, grp in (('all V3 HIGHs', valid),
                       ('tradeable (MAE before MFE)', tradeable),
                       ('untradeable (MFE before MAE)', untradeable)):
        if not grp:
            continue
        n = len(grp)
        wrs = []
        for K in (1.0, 1.5, 2.0):
            wins = sum(1 for r in grp if r.get(f'swing_win_{K}'))
            wrs.append(wins / n * 100)
        mae = sum(r['mae_vol'] for r in grp) / n
        mfe = sum(r['mfe_vol'] for r in grp) / n
        print(f'    {label:<35} {n:>6} {wrs[0]:>8.1f}% {wrs[1]:>8.1f}% {wrs[2]:>8.1f}% '
              f'{mae:>+9.2f}s {mfe:>+9.2f}s')

    # Now: does any entry-time feature predict tradeable vs untradeable?
    # (P(tradeable | feature_quintile) — ratio above/below 50% reveals signal)
    print()
    print('  Predictability of tradeable-on-entry (% MAE-before-MFE) by feature quintile:')
    for key in ('sacc', 'ext', 'matur', 'vel5', 'rsi', 'stoch', 'vol_pct'):
        vals = [r[key] for r in valid if r.get(key) is not None]
        bounds = _quintile_bounds(vals)
        if bounds is None:
            continue
        buckets = [[] for _ in range(5)]
        for r in valid:
            b = _bin(r.get(key), bounds)
            if b is not None:
                buckets[b].append(r)
        line = f'    {key:<10}'
        for rs in buckets:
            if not rs:
                line += f'{"--":>10}'
                continue
            pct = sum(1 for r in rs if r['mae_before_mfe']) / len(rs) * 100
            line += f' {pct:>8.1f}%'
        spread = max(sum(1 for r in rs if r['mae_before_mfe']) / max(len(rs), 1) * 100
                     for rs in buckets if rs) - \
                 min(sum(1 for r in rs if r['mae_before_mfe']) / max(len(rs), 1) * 100
                     for rs in buckets if rs)
        line += f'   spread={spread:.1f}pp'
        print(line)


def _print_dte_table(rows, K=2.0):
    """Implied DTE recommendation by ext quintile.
    DTE_rec = 2 * mean(days_to_K_vol_drawdown) for winning peaks in that bucket."""
    valid = [r for r in rows if r.get('vol_pct') is not None and r.get('ext') is not None
             and r.get(f'swing_win_{K}')]
    if len(valid) < 20:
        return
    bounds = _quintile_bounds([r['ext'] for r in valid])
    if bounds is None:
        return
    print()
    print(f'  Implied DTE table @ K={K}: bucket by ext quintile, DTE = 2 * mean(days_to_target)')
    print(f'    (only winning peaks counted; bucket-level expectation, not per-trade)')
    print(f'    {"q":<3} {"ext range":<22} {"N_win":>6} {"WR within":>11} {"mean d":>9} {"DTE rec":>9}')
    buckets = [[] for _ in range(5)]
    for r in valid:
        b = _bin(r['ext'], bounds)
        if b is not None:
            buckets[b].append(r)
    # Need full bucket counts for WR-within
    all_valid = [r for r in rows if r.get('vol_pct') is not None and r.get('ext') is not None
                 and f'swing_win_{K}' in r]
    full_buckets = [[] for _ in range(5)]
    for r in all_valid:
        b = _bin(r['ext'], bounds)
        if b is not None:
            full_buckets[b].append(r)
    for i in range(5):
        winners = buckets[i]
        full = full_buckets[i]
        if not winners or not full:
            continue
        days = [r[f'days_to_{K}'] for r in winners if r[f'days_to_{K}'] is not None]
        mean_d = sum(days) / len(days) if days else 0
        dte_rec = round(2 * mean_d)
        wr_within = len(winners) / len(full) * 100
        lo = bounds[i - 1] if i > 0 else float('-inf')
        hi = bounds[i] if i < 4 else float('+inf')
        ext_range = f'[{lo:+.1f}, {hi:+.1f})' if i < 4 else f'[{lo:+.1f}, +inf)'
        print(f'    {i+1:<3} {ext_range:<22} {len(winners):>6} {wr_within:>10.1f}% '
              f'{mean_d:>8.1f} {dte_rec:>8}d')


def _summarize_feature(rows, key, label_w=22):
    """Return (label_str, n, mean, median, p25, p75) for a feature across rows."""
    vals = sorted(r[key] for r in rows if r.get(key) is not None)
    if not vals:
        return f'  {key:<{label_w}} (no data)'
    n = len(vals)
    mean = sum(vals) / n
    p25 = vals[n // 4]
    median = vals[n // 2]
    p75 = vals[(n * 3) // 4]
    return (f'  {key:<{label_w}} N={n:<5} '
            f'mean={mean:>+9.2f}  p25={p25:>+8.2f}  med={median:>+8.2f}  p75={p75:>+8.2f}')


def _compare_winners_losers(rows, side, band, cell=('K2.0', 'M5.0', 'W30')):
    """For a single (side, band, K, M, W) cell, split into winners vs stopouts
    and print feature distributions side by side. Goal: surface what differentiates
    a profitable LOW signal from one that gets stopped out, so we can find a
    pre-entry filter (rather than touching the score formula).
    """
    K, M, W = cell
    prefix = f'path_{K}_{M}_{W}'
    pop = [r for r in rows
           if r.get('side') == side and r.get('band') == band and r.get(prefix) is not None]
    winners = [r for r in pop if r[prefix] == 'win']
    stopouts = [r for r in pop if r[prefix] == 'stopout']
    expires = [r for r in pop if r[prefix] == 'expire']

    print()
    print('=' * 92)
    print(f'WINNER vs STOPOUT comparison — side={side}  band={band}  cell={K}/{M}/{W}')
    print('=' * 92)
    print(f'  population: N={len(pop)}  '
          f'winners={len(winners)}  stopouts={len(stopouts)}  expires={len(expires)}')

    keys = ['score', 'ext', 'matur', 'vel5', 'sacc', 'rsi', 'stoch', 'bb_pct',
            'macdh', 'ema200_dist', 'ret_5d_prior', 'ret_20d_prior', 'vol_ratio',
            'vol_pct', 'days_since_same_side']
    print()
    print(f'  {"feature":<22} {"WIN":>40}  |  {"STOPOUT":>40}')
    for k in keys:
        w_vals = sorted(r[k] for r in winners if r.get(k) is not None)
        s_vals = sorted(r[k] for r in stopouts if r.get(k) is not None)

        def fmt_block(vals):
            if not vals:
                return f'{"--":>40}'
            n = len(vals)
            mean = sum(vals) / n
            med = vals[n // 2]
            p25 = vals[n // 4]
            p75 = vals[min(n - 1, (n * 3) // 4)]
            return f'N={n:<4} mean={mean:>+8.2f} p25={p25:>+7.2f} med={med:>+7.2f} p75={p75:>+7.2f}'

        # Spread indicator: mean delta / (pooled std proxy from IQR)
        delta = ''
        if w_vals and s_vals:
            mw = sum(w_vals) / len(w_vals)
            ms = sum(s_vals) / len(s_vals)
            iqr_w = (w_vals[min(len(w_vals)-1,(len(w_vals)*3)//4)] - w_vals[len(w_vals)//4]) or 1.0
            iqr_s = (s_vals[min(len(s_vals)-1,(len(s_vals)*3)//4)] - s_vals[len(s_vals)//4]) or 1.0
            pooled = (abs(iqr_w) + abs(iqr_s)) / 2 or 1.0
            z = (mw - ms) / pooled
            flag = ' **' if abs(z) >= 0.4 else ''
            delta = f'  d={(mw-ms):+.2f} (z={z:+.2f}){flag}'
        print(f'  {k:<22} {fmt_block(w_vals)}  |  {fmt_block(s_vals)}{delta}')

    # Sample 10 winners and 10 stopouts (most extreme outcomes), with symbol/date
    print()
    print('  Sample WINNERS (top 10 by exit_ret):')
    print(f'    {"symbol":<8} {"date":<12} {"score":>5} {"ext":>7} {"rsi":>6} '
          f'{"vel5":>7} {"bb_pct":>7} {"vol_r":>6} {"5d_pr":>7} {"20d_pr":>8} {"exit%":>7}')
    sample_w = sorted(winners, key=lambda r: -(r.get(prefix + '_exit_ret') or 0))[:10]
    for r in sample_w:
        print(f'    {str(r["symbol"])[:8]:<8} {str(r["date"]):<12} {r["score"]:>5} '
              f'{r["ext"]:>+7.2f} {(r.get("rsi") or 0):>6.1f} '
              f'{(r.get("vel5") or 0):>+7.2f} {(r.get("bb_pct") or 0):>+7.3f} '
              f'{(r.get("vol_ratio") or 0):>6.2f} {(r.get("ret_5d_prior") or 0):>+7.2f} '
              f'{(r.get("ret_20d_prior") or 0):>+8.2f} {(r.get(prefix + "_exit_ret") or 0):>+7.2f}')
    print()
    print('  Sample STOPOUTS (worst 10 by exit_ret):')
    print(f'    {"symbol":<8} {"date":<12} {"score":>5} {"ext":>7} {"rsi":>6} '
          f'{"vel5":>7} {"bb_pct":>7} {"vol_r":>6} {"5d_pr":>7} {"20d_pr":>8} {"exit%":>7}')
    sample_s = sorted(stopouts, key=lambda r: (r.get(prefix + '_exit_ret') or 0))[:10]
    for r in sample_s:
        print(f'    {str(r["symbol"])[:8]:<8} {str(r["date"]):<12} {r["score"]:>5} '
              f'{r["ext"]:>+7.2f} {(r.get("rsi") or 0):>6.1f} '
              f'{(r.get("vel5") or 0):>+7.2f} {(r.get("bb_pct") or 0):>+7.3f} '
              f'{(r.get("vol_ratio") or 0):>6.2f} {(r.get("ret_5d_prior") or 0):>+7.2f} '
              f'{(r.get("ret_20d_prior") or 0):>+8.2f} {(r.get(prefix + "_exit_ret") or 0):>+7.2f}')


def _final_strategy_test(rows, K, M, W):
    """Test the 4-gate filter (adding ema200_dist > -25%) and the band-union
    strategy (skip 20-22 / 6-15)."""
    prefix = f'path_K{K}_M{M}_W{W}'
    base = [r for r in rows if r.get('side') == 'low' and r.get(prefix) is not None]

    g3 = lambda r: ((r.get('ret_20d_prior') or 0) > 0
                    and (r.get('vol_ratio') or 0) >= 1.0
                    and (r.get('macdh') or 0) > 0)
    g4 = lambda r: g3(r) and (r.get('ema200_dist') or -999) > -25
    g4_tight = lambda r: g3(r) and (r.get('ema200_dist') or -999) > -15
    sweet_bands = {'23-25', '16-19'}
    sweet_band = lambda r: r.get('band') in sweet_bands

    strategies = [
        ('NONE (LOW universe baseline)',                              lambda r: True),
        ('3-gate: 20d>0 + vol>=1 + macdh>0',                          g3),
        ('4-gate: + ema200_dist > -25%',                              g4),
        ('4-gate tight: + ema200_dist > -15%',                        g4_tight),
        ('sweet bands only (23-25 OR 16-19)',                         sweet_band),
        ('3-gate + sweet bands',                                      lambda r: g3(r) and sweet_band(r)),
        ('4-gate + sweet bands',                                      lambda r: g4(r) and sweet_band(r)),
        ('4-gate tight + sweet bands',                                lambda r: g4_tight(r) and sweet_band(r)),
    ]

    print()
    print('=' * 92)
    print(f'FINAL STRATEGY COMPARISON  side=low  cell K={K}/M={M}/W={W}d  base N={len(base)}')
    print('=' * 92)
    print(f'  {"strategy":<42} {"N":>6} {"keep%":>7} {"p_win":>7} '
          f'{"win_pnl":>9} {"stop_pnl":>10} {"EV%":>8}')
    for label, fn in strategies:
        subset = [r for r in base if fn(r)]
        c = _payout_cell(subset, float(K), float(M), int(W), 'low')
        if c is None:
            print(f'  {label:<42} (no data)')
            continue
        keep = c['n'] / len(base) * 100
        print(f'  {label:<42} {c["n"]:>6} {keep:>6.1f}% '
              f'{c["p_win"]*100:>6.1f}% {c["mean_win"]:>+8.1f}% '
              f'{c["mean_stop"]:>+9.1f}% {c["ev"]:>+7.1f}%')

    # Per-year trade rate estimate
    if base:
        days = (max(r['date'] for r in base) - min(r['date'] for r in base)).days
        years = days / 365.25 if days > 0 else 1
        print()
        print(f'  (sample window: {years:.1f} years -> per-year trade rate shown below)')
        for label, fn in strategies:
            subset = [r for r in base if fn(r)]
            c = _payout_cell(subset, float(K), float(M), int(W), 'low')
            if c is None:
                continue
            per_year = c['n'] / years
            annual_ev = c['ev'] * per_year
            print(f'    {label:<42} {per_year:>5.0f}/yr  '
                  f'EV/yr (no compounding) = {annual_ev:>+7.0f}%')


def _filter_test_per_band(rows, K, M, W):
    """Apply the winning combo (20d>0 + vol>=1 + macdh>0) to each LOW sub-band
    independently, to see if the band restriction adds value on top of the filter."""
    bands = ['23-25', '20-22', '16-19', '6-15']
    combo = lambda r: ((r.get('ret_20d_prior') or 0) > 0
                       and (r.get('vol_ratio') or 0) >= 1.0
                       and (r.get('macdh') or 0) > 0)
    print()
    print('=' * 92)
    print(f'FILTER COMBO x BAND  side=low  cell K={K}/M={M}/W={W}d')
    print(f'  combo = ret_20d_prior > 0 AND vol_ratio >= 1.0 AND macdh > 0')
    print('=' * 92)
    print(f'  {"band":<8} {"raw N":>7} {"filt N":>7} {"keep%":>7} '
          f'{"raw EV":>9} {"filt p_win":>11} {"filt EV":>10} {"lift":>8}')
    for band in bands:
        raw = [r for r in rows if r.get('side') == 'low' and r.get('band') == band
               and r.get(f'path_K{K}_M{M}_W{W}') is not None]
        if not raw:
            continue
        c_raw = _payout_cell(raw, float(K), float(M), int(W), 'low')
        if c_raw is None:
            continue
        filt = [r for r in raw if combo(r)]
        c_filt = _payout_cell(filt, float(K), float(M), int(W), 'low')
        if c_filt is None:
            print(f'  {band:<8} {len(raw):>7} {"--":>7}')
            continue
        keep = c_filt['n'] / len(raw) * 100
        lift = c_filt['ev'] - c_raw['ev']
        print(f'  {band:<8} {len(raw):>7} {c_filt["n"]:>7} {keep:>6.1f}% '
              f'{c_raw["ev"]:>+8.1f}% {c_filt["p_win"]*100:>10.1f}% '
              f'{c_filt["ev"]:>+9.1f}% {lift:>+7.1f}pp')


def _drill_residual_stopouts(rows, K, M, W):
    """Show stopouts that PASSED all 3 filters — these are the residual losses
    we want to characterize. Look for a 4th gate."""
    combo = lambda r: ((r.get('ret_20d_prior') or 0) > 0
                       and (r.get('vol_ratio') or 0) >= 1.0
                       and (r.get('macdh') or 0) > 0)
    prefix = f'path_K{K}_M{M}_W{W}'
    pop = [r for r in rows if r.get('side') == 'low' and r.get(prefix) is not None
           and combo(r)]
    stopouts = [r for r in pop if r[prefix] == 'stopout']
    winners = [r for r in pop if r[prefix] == 'win']

    print()
    print('=' * 92)
    print(f'RESIDUAL STOPOUTS after combo filter  side=low  cell K={K}/M={M}/W={W}d')
    print('=' * 92)
    print(f'  Population after filter: N={len(pop)}  '
          f'winners={len(winners)}  stopouts={len(stopouts)}')

    keys = ['score', 'ext', 'matur', 'vel5', 'sacc', 'rsi', 'stoch', 'bb_pct',
            'macdh', 'ema200_dist', 'ret_5d_prior', 'ret_20d_prior', 'vol_ratio',
            'vol_pct', 'days_since_same_side']
    print()
    print(f'  {"feature":<22} {"WIN":>40}  |  {"STOPOUT":>40}')
    for k in keys:
        w_vals = sorted(r[k] for r in winners if r.get(k) is not None)
        s_vals = sorted(r[k] for r in stopouts if r.get(k) is not None)

        def fmt_block(vals):
            if not vals:
                return f'{"--":>40}'
            n = len(vals)
            mean = sum(vals) / n
            med = vals[n // 2]
            p25 = vals[n // 4]
            p75 = vals[min(n - 1, (n * 3) // 4)]
            return f'N={n:<4} mean={mean:>+8.2f} p25={p25:>+7.2f} med={med:>+7.2f} p75={p75:>+7.2f}'

        delta = ''
        if w_vals and s_vals:
            mw = sum(w_vals) / len(w_vals)
            ms = sum(s_vals) / len(s_vals)
            iqr_w = (w_vals[min(len(w_vals)-1,(len(w_vals)*3)//4)] - w_vals[len(w_vals)//4]) or 1.0
            iqr_s = (s_vals[min(len(s_vals)-1,(len(s_vals)*3)//4)] - s_vals[len(s_vals)//4]) or 1.0
            pooled = (abs(iqr_w) + abs(iqr_s)) / 2 or 1.0
            z = (mw - ms) / pooled
            flag = ' **' if abs(z) >= 0.4 else (' *' if abs(z) >= 0.25 else '')
            delta = f'  d={(mw-ms):+.2f} (z={z:+.2f}){flag}'
        print(f'  {k:<22} {fmt_block(w_vals)}  |  {fmt_block(s_vals)}{delta}')

    print()
    print('  Sample residual STOPOUTS (worst 15 by exit_ret) — what slipped through?:')
    print(f'    {"symbol":<8} {"date":<12} {"score":>5} {"ext":>7} {"rsi":>6} '
          f'{"vel5":>7} {"bb_pct":>7} {"vol_r":>6} {"macdh":>7} {"e200d":>8} '
          f'{"5d":>7} {"20d":>7} {"matur":>7} {"exit%":>7}')
    sample_s = sorted(stopouts, key=lambda r: (r.get(prefix + '_exit_ret') or 0))[:15]
    for r in sample_s:
        print(f'    {str(r["symbol"])[:8]:<8} {str(r["date"]):<12} {r["score"]:>5} '
              f'{r["ext"]:>+7.2f} {(r.get("rsi") or 0):>6.1f} '
              f'{(r.get("vel5") or 0):>+7.2f} {(r.get("bb_pct") or 0):>+7.3f} '
              f'{(r.get("vol_ratio") or 0):>6.2f} {(r.get("macdh") or 0):>+7.3f} '
              f'{(r.get("ema200_dist") or 0):>+8.2f} '
              f'{(r.get("ret_5d_prior") or 0):>+7.2f} {(r.get("ret_20d_prior") or 0):>+7.2f} '
              f'{(r.get("matur") or 0):>+7.3f} {(r.get(prefix + "_exit_ret") or 0):>+7.2f}')


def _filter_test(rows, side, K, M, W):
    """Test pre-entry filters by recomputing EV with each filter applied to all
    LOW peaks at the given (K, M, W) cell. Goal: find a filter (or combo) that
    materially boosts EV without shrinking N too much."""
    base = [r for r in rows if r.get('side') == side
            and r.get(f'path_K{K}_M{M}_W{W}') is not None]
    if not base:
        return

    filters = {
        'NONE (baseline)':         lambda r: True,
        'ret_20d_prior > 0':       lambda r: (r.get('ret_20d_prior') or 0) > 0,
        'ret_5d_prior > 0':        lambda r: (r.get('ret_5d_prior') or 0) > 0,
        'vol_ratio >= 1.0':        lambda r: (r.get('vol_ratio') or 0) >= 1.0,
        'ema200_dist > -25%':      lambda r: (r.get('ema200_dist') or -999) > -25,
        'matur < 0.6':             lambda r: (r.get('matur') or 0) < 0.6,
        'macdh > 0':               lambda r: (r.get('macdh') or 0) > 0,
        'days_since_same > 30':   lambda r: (r.get('days_since_same_side') is None
                                              or r.get('days_since_same_side') > 30),
        'COMBO: 20d>0 + ema200>-25 + vol>=1':
            lambda r: ((r.get('ret_20d_prior') or 0) > 0
                       and (r.get('ema200_dist') or -999) > -25
                       and (r.get('vol_ratio') or 0) >= 1.0),
        'COMBO: 20d>0 + matur<0.6':
            lambda r: ((r.get('ret_20d_prior') or 0) > 0
                       and (r.get('matur') or 0) < 0.6),
        'COMBO: 20d>0 + ema200>-20':
            lambda r: ((r.get('ret_20d_prior') or 0) > 0
                       and (r.get('ema200_dist') or -999) > -20),
        'COMBO: 20d>0 + vol>=1 + macdh>0':
            lambda r: ((r.get('ret_20d_prior') or 0) > 0
                       and (r.get('vol_ratio') or 0) >= 1.0
                       and (r.get('macdh') or 0) > 0),
    }

    print()
    print('=' * 92)
    print(f'PRE-ENTRY FILTER TEST  side={side}  cell K={K}/M={M}/W={W}d  base N={len(base)}')
    print('=' * 92)
    print(f'  {"filter":<42} {"N":>6} {"keep%":>7} {"p_win":>7} '
          f'{"p_stop":>8} {"win_pnl":>9} {"stop_pnl":>10} {"EV%":>8}')

    for label, fn in filters.items():
        subset = [r for r in base if fn(r)]
        c = _payout_cell(subset, float(K), float(M), int(W), side)
        if c is None:
            print(f'  {label:<42} (no data)')
            continue
        keep_pct = c['n'] / len(base) * 100
        print(f'  {label:<42} {c["n"]:>6} {keep_pct:>6.1f}% '
              f'{c["p_win"]*100:>6.1f}% {c["p_stop"]*100:>7.1f}% '
              f'{c["mean_win"]:>+8.1f}% {c["mean_stop"]:>+9.1f}% {c["ev"]:>+7.1f}%')


# =====================================================================
# HIGH-side band investigation (session 4)
# Question: why does 90+ collapse and what differs across HIGH bands?
# =====================================================================

HIGH_BANDS = ['70-74', '75-79', '80-84', '85-89', '90+']

# Cells to scan for HIGH side. Best per CLAUDE.md was K=1.0/M=2.0/W=63.
HIGH_CELLS = [
    (1.0, 2.0, 21), (1.0, 2.0, 30), (1.0, 2.0, 42), (1.0, 2.0, 63),
    (1.0, 3.0, 30), (1.0, 3.0, 63),
    (1.0, 5.0, 30), (1.0, 5.0, 63),
    (1.5, 3.0, 42), (1.5, 5.0, 42),
    (2.0, 5.0, 30), (2.0, 5.0, 63),
]


def _high_band_cell_table(rows):
    """For each HIGH band x each cell, print N / EV / p_win.
    Surfaces band-cell intersections that buck the universe-level negative EV."""
    pops = {b: [r for r in rows if r.get('side') == 'high' and r.get('band') == b]
            for b in HIGH_BANDS}
    print()
    print('=' * 110)
    print('HIGH SIDE: EV per (band x cell)  — puts thesis')
    print('=' * 110)
    header = f'  {"band":<7} {"N":>5}  '
    for K, M, W in HIGH_CELLS:
        header += f'K{K}M{M}W{W}'.rjust(13)
    print(header)
    for b in HIGH_BANDS:
        pop = pops[b]
        line = f'  {b:<7} {len(pop):>5}  '
        for K, M, W in HIGH_CELLS:
            c = _payout_cell(pop, K, M, W, 'high')
            if c is None or c['n'] < 5:
                line += '       --    '
            else:
                line += f'{c["ev"]:+5.1f}/{c["p_win"]*100:>2.0f}%/{c["n"]:<3}'.rjust(13)
        print(line)
    print('  (cell entries: EV%/p_win%/N — only shown when N>=5 in band-cell)')


def _high_cross_band_features(rows):
    """For each HIGH band, mean of each entry-time feature.
    Goal: surface what structurally differs between profitable and losing bands."""
    pops = {b: [r for r in rows if r.get('side') == 'high' and r.get('band') == b]
            for b in HIGH_BANDS}
    keys = ['ext', 'matur', 'vel5', 'sacc', 'rsi', 'stoch', 'bb_pct', 'macdh',
            'ema200_dist', 'ret_5d_prior', 'ret_20d_prior', 'vol_ratio',
            'vol_pct', 'days_since_same_side']
    print()
    print('=' * 92)
    print('HIGH SIDE: cross-band feature means (mean entry-time value)')
    print('=' * 92)
    header = f'  {"feature":<22}'
    for b in HIGH_BANDS:
        header += f'{b:>12}'
    print(header)
    line = f'  {"N":<22}'
    for b in HIGH_BANDS:
        line += f'{len(pops[b]):>12}'
    print(line)
    print('  ' + '-' * 88)
    for k in keys:
        line = f'  {k:<22}'
        for b in HIGH_BANDS:
            vals = [r[k] for r in pops[b] if r.get(k) is not None]
            if vals:
                m = sum(vals) / len(vals)
                line += f'{m:>+12.2f}'
            else:
                line += f'{"--":>12}'
        print(line)


def _high_filter_test(rows, K, M, W):
    """HIGH-side pre-entry filters. Gates oriented for SHORT thesis: confirm
    overextension + momentum already cracking + climactic volume."""
    base = [r for r in rows if r.get('side') == 'high'
            and r.get(f'path_K{K}_M{M}_W{W}') is not None]
    if not base:
        return

    filters = {
        'NONE (HIGH baseline)':                          lambda r: True,
        'ret_20d_prior > +10% (rallied hard)':           lambda r: (r.get('ret_20d_prior') or 0) > 10,
        'ret_20d_prior > +20% (parabolic)':              lambda r: (r.get('ret_20d_prior') or 0) > 20,
        'ret_5d_prior > 0 (still rising into entry)':    lambda r: (r.get('ret_5d_prior') or 0) > 0,
        'ret_5d_prior < 0 (already rolling)':            lambda r: (r.get('ret_5d_prior') or 0) < 0,
        'macdh < 0 (momentum cracked)':                  lambda r: (r.get('macdh') or 0) < 0,
        'macdh > 0 (still pushing)':                     lambda r: (r.get('macdh') or 0) > 0,
        'vol_ratio >= 1.0 (active)':                     lambda r: (r.get('vol_ratio') or 0) >= 1.0,
        'vol_ratio >= 1.5 (climactic)':                  lambda r: (r.get('vol_ratio') or 0) >= 1.5,
        'bb_pct >= 0.95 (upper-band pierced)':           lambda r: (r.get('bb_pct') or 0) >= 0.95,
        'rsi >= 70':                                     lambda r: (r.get('rsi') or 0) >= 70,
        'stoch >= 80':                                   lambda r: (r.get('stoch') or 0) >= 80,
        'ext > +15% (far from EMA50)':                   lambda r: (r.get('ext') or 0) > 15,
        'ema200_dist > +20% (extended above LT)':        lambda r: (r.get('ema200_dist') or 0) > 20,
        'COMBO: 20d>10 + macdh<0 + vol>=1':              lambda r: ((r.get('ret_20d_prior') or 0) > 10
                                                                       and (r.get('macdh') or 0) < 0
                                                                       and (r.get('vol_ratio') or 0) >= 1.0),
        'COMBO: 5d<0 + macdh<0 + bb>=0.9':               lambda r: ((r.get('ret_5d_prior') or 0) < 0
                                                                       and (r.get('macdh') or 0) < 0
                                                                       and (r.get('bb_pct') or 0) >= 0.9),
        'COMBO: ext>+15 + macdh<0':                      lambda r: ((r.get('ext') or 0) > 15
                                                                       and (r.get('macdh') or 0) < 0),
    }

    print()
    print('=' * 92)
    print(f'HIGH PRE-ENTRY FILTER TEST  cell K={K}/M={M}/W={W}d  base N={len(base)}')
    print('=' * 92)
    print(f'  {"filter":<46} {"N":>5} {"keep%":>7} {"p_win":>7} '
          f'{"win_pnl":>9} {"stop_pnl":>10} {"EV%":>8}')
    for label, fn in filters.items():
        subset = [r for r in base if fn(r)]
        c = _payout_cell(subset, float(K), float(M), int(W), 'high')
        if c is None:
            print(f'  {label:<46} (no data)')
            continue
        keep = c['n'] / len(base) * 100
        print(f'  {label:<46} {c["n"]:>5} {keep:>6.1f}% '
              f'{c["p_win"]*100:>6.1f}% {c["mean_win"]:>+8.1f}% '
              f'{c["mean_stop"]:>+9.1f}% {c["ev"]:>+7.1f}%')


def _high_per_band_compare(rows, K, M, W):
    """Run _compare_winners_losers for each HIGH band that has enough data
    at the given cell."""
    for band in HIGH_BANDS:
        pop = [r for r in rows if r.get('side') == 'high' and r.get('band') == band
               and r.get(f'path_K{K}_M{M}_W{W}') is not None]
        if len(pop) < 15:
            print(f'  (skipping band={band}: only N={len(pop)} at cell {K}/{M}/{W})')
            continue
        _compare_winners_losers(rows, side='high', band=band,
                                 cell=(f'K{K}', f'M{M}', f'W{W}'))


def _sanitize_split_corruption(rows):
    """Drop rows with implausible feature values that come from unadjusted
    stock splits (e.g. ORLY 15:1 mid-2024 produces ext=-93%, bb_pct=-10).
    Sanity gates: |ext| < 50%, -1 <= bb_pct <= 2."""
    clean = []
    dropped = 0
    drop_syms = {}
    for r in rows:
        ext = r.get('ext')
        bb = r.get('bb_pct')
        if ext is not None and abs(ext) > 50:
            dropped += 1
            drop_syms[r['symbol']] = drop_syms.get(r['symbol'], 0) + 1
            continue
        if bb is not None and (bb < -1 or bb > 2):
            dropped += 1
            drop_syms[r['symbol']] = drop_syms.get(r['symbol'], 0) + 1
            continue
        clean.append(r)
    print(f'  sanitize: dropped {dropped} rows from {len(drop_syms)} symbols')
    if drop_syms:
        top = sorted(drop_syms.items(), key=lambda x: -x[1])[:10]
        print(f'    top dropped symbols: {top}')
    return clean


def _hypothesis_vol_replicates_score(rows, K, M, W):
    """Hypothesis: vol_ratio >= 2.0 on the 70-74 band reproduces 80-84 EV.
    If true, the score is just a volume-conviction tag and 80-84 = 70-74 + vol filter."""
    prefix = f'path_K{K}_M{M}_W{W}'
    bands = ['70-74', '75-79', '80-84', '85-89', '90+']
    print()
    print('=' * 92)
    print(f'HYP TEST: vol_ratio thresholds per band  cell K={K}/M={M}/W={W}')
    print('=' * 92)
    print(f'  {"band":<8} {"vol_thr":>9} {"N":>6} {"keep%":>7} '
          f'{"p_win":>7} {"win_pnl":>9} {"stop_pnl":>10} {"EV%":>8}')
    for band in bands:
        base = [r for r in rows if r.get('side')=='high' and r.get('band')==band
                and r.get(prefix) is not None]
        if not base:
            continue
        for thr in (0.0, 1.0, 1.5, 2.0, 3.0):
            subset = [r for r in base if (r.get('vol_ratio') or 0) >= thr]
            c = _payout_cell(subset, float(K), float(M), int(W), 'high')
            if c is None:
                continue
            keep = c['n'] / len(base) * 100
            print(f'  {band:<8} {thr:>9.1f} {c["n"]:>6} {keep:>6.1f}% '
                  f'{c["p_win"]*100:>6.1f}% {c["mean_win"]:>+8.1f}% '
                  f'{c["mean_stop"]:>+9.1f}% {c["ev"]:>+7.1f}%')


def _hypothesis_universal_fingerprint(rows, K, M, W):
    """Hypothesis: rally+crack+vol fingerprint as universal HIGH filter.
    Apply to full 70+ universe (and per-band)."""
    prefix = f'path_K{K}_M{M}_W{W}'
    base = [r for r in rows if r.get('side')=='high' and r.get(prefix) is not None]
    if not base:
        return

    fp = lambda r: ((r.get('ret_20d_prior') or -999) > 3
                    and (r.get('macdh') or 0) < -0.5
                    and (r.get('vol_ratio') or 0) >= 1.5)
    fp_loose = lambda r: ((r.get('ret_20d_prior') or -999) > 0
                          and (r.get('macdh') or 0) < 0
                          and (r.get('vol_ratio') or 0) >= 1.0)
    fp_tight = lambda r: ((r.get('ret_20d_prior') or -999) > 5
                          and (r.get('macdh') or 0) < -1.0
                          and (r.get('vol_ratio') or 0) >= 2.0)

    strategies = [
        ('NONE (HIGH baseline)',                                   lambda r: True),
        ('LOOSE: 20d>0 + macdh<0 + vol>=1',                        fp_loose),
        ('FINGERPRINT: 20d>3 + macdh<-0.5 + vol>=1.5',             fp),
        ('TIGHT: 20d>5 + macdh<-1 + vol>=2',                       fp_tight),
        ('+ band 80-89',                                           lambda r: fp(r) and r.get('band') in {'80-84','85-89'}),
        ('+ band 70-79',                                           lambda r: fp(r) and r.get('band') in {'70-74','75-79'}),
    ]

    print()
    print('=' * 92)
    print(f'HYP TEST: rally+crack+vol fingerprint  cell K={K}/M={M}/W={W}  base N={len(base)}')
    print('=' * 92)
    print(f'  {"strategy":<46} {"N":>5} {"keep%":>7} {"p_win":>7} '
          f'{"win_pnl":>9} {"stop_pnl":>10} {"EV%":>8}')
    for label, fn in strategies:
        subset = [r for r in base if fn(r)]
        c = _payout_cell(subset, float(K), float(M), int(W), 'high')
        if c is None:
            print(f'  {label:<46} (no data)')
            continue
        keep = c['n'] / len(base) * 100
        print(f'  {label:<46} {c["n"]:>5} {keep:>6.1f}% '
              f'{c["p_win"]*100:>6.1f}% {c["mean_win"]:>+8.1f}% '
              f'{c["mean_stop"]:>+9.1f}% {c["ev"]:>+7.1f}%')

    # Per-band breakdown of the fingerprint
    print()
    print('  fingerprint per band:')
    print(f'    {"band":<8} {"raw N":>7} {"filt N":>7} {"keep%":>7} '
          f'{"raw EV":>9} {"filt p_win":>11} {"filt EV":>10} {"lift":>8}')
    for band in HIGH_BANDS:
        raw = [r for r in base if r.get('band') == band]
        if not raw:
            continue
        c_raw = _payout_cell(raw, float(K), float(M), int(W), 'high')
        if c_raw is None:
            continue
        filt = [r for r in raw if fp(r)]
        c_filt = _payout_cell(filt, float(K), float(M), int(W), 'high')
        if c_filt is None or c_filt['n'] < 5:
            print(f'    {band:<8} {len(raw):>7} {(c_filt["n"] if c_filt else 0):>7} '
                  f'  --  insufficient N')
            continue
        keep = c_filt['n'] / len(raw) * 100
        lift = c_filt['ev'] - c_raw['ev']
        print(f'    {band:<8} {len(raw):>7} {c_filt["n"]:>7} {keep:>6.1f}% '
              f'{c_raw["ev"]:>+8.1f}% {c_filt["p_win"]*100:>10.1f}% '
              f'{c_filt["ev"]:>+9.1f}% {lift:>+7.1f}pp')


def _sign_bug_ghost_test(rows, K, M, W):
    """Test the sign-inversion-bug hypothesis from session 3.

    Hypothesis: a non-trivial fraction of HIGH 'wins' are sign-bug ghosts ---
    score fired HIGH on stocks already in deep declines (ret_20d << 0, ext < 0)
    where the inverted oscillator convention makes oversold raw values produce
    high overall scores. These 'wins' are coincidences (the stock was already
    in a downtrend) and would NOT be signal under a corrected convention.

    If the hypothesis holds:
      - Removing 'ghost' rows from HIGH should LEAVE EV unchanged or worse,
        because we're removing accidental wins.
      - Symmetrically on LOW: removing 'sign-bug ghosts' on the long side
        (ret_20d >> 0 AND ext > +10 -- stocks already pushed up where the
        inverted formula scored them LOW because oscillators overbought)
        should leave LOW EV unchanged or worse.
    If we INSTEAD see EV improve when ghosts are removed, the bug is benign:
    the rows it produces are still tradeable.
    """
    ghost_high = lambda r: ((r.get('ret_20d_prior') or 0) < -10
                            and (r.get('ext') or 0) < 0)
    ghost_low  = lambda r: ((r.get('ret_20d_prior') or 0) > 10
                            and (r.get('ext') or 0) > 10)

    print()
    print('=' * 110)
    print(f'SIGN-BUG GHOST TEST  cell K={K}/M={M}/W={W}')
    print('=' * 110)

    # ── HIGH side ──
    prefix = f'path_K{K}_M{M}_W{W}'
    print()
    print('  HIGH side (ghost fingerprint: ret_20d_prior < -10 AND ext < 0)')
    print(f'  {"band":<8} {"all N":>6} {"all p_win":>10} {"all EV":>9}  '
          f'{"ghost N":>8} {"ghost p_win":>12} {"ghost EV":>10}  '
          f'{"clean N":>8} {"clean p_win":>12} {"clean EV":>10}  {"delta":>8}')
    for band in HIGH_BANDS:
        all_pop = [r for r in rows if r.get('side')=='high' and r.get('band')==band
                   and r.get(prefix) is not None]
        if not all_pop:
            continue
        ghost_pop = [r for r in all_pop if ghost_high(r)]
        clean_pop = [r for r in all_pop if not ghost_high(r)]
        c_all = _payout_cell(all_pop, float(K), float(M), int(W), 'high')
        c_ghost = _payout_cell(ghost_pop, float(K), float(M), int(W), 'high')
        c_clean = _payout_cell(clean_pop, float(K), float(M), int(W), 'high')
        if c_all is None:
            continue
        delta = (c_clean['ev'] - c_all['ev']) if c_clean else 0
        ghost_str = (f'{c_ghost["n"]:>8} {c_ghost["p_win"]*100:>11.1f}% '
                     f'{c_ghost["ev"]:>+9.1f}%') if c_ghost else f'{0:>8} {"--":>12} {"--":>10}'
        clean_str = (f'{c_clean["n"]:>8} {c_clean["p_win"]*100:>11.1f}% '
                     f'{c_clean["ev"]:>+9.1f}%') if c_clean else f'{0:>8} {"--":>12} {"--":>10}'
        print(f'  {band:<8} {c_all["n"]:>6} {c_all["p_win"]*100:>9.1f}% '
              f'{c_all["ev"]:>+8.1f}%  {ghost_str}  {clean_str}  {delta:>+7.1f}pp')

    # ── LOW side ──
    print()
    print('  LOW side (ghost fingerprint: ret_20d_prior > +10 AND ext > +10)')
    print(f'  {"band":<8} {"all N":>6} {"all p_win":>10} {"all EV":>9}  '
          f'{"ghost N":>8} {"ghost p_win":>12} {"ghost EV":>10}  '
          f'{"clean N":>8} {"clean p_win":>12} {"clean EV":>10}  {"delta":>8}')
    LOW_BANDS_ORDER = ['<=5', '6-15', '16-19', '20-22', '23-25']
    for band in LOW_BANDS_ORDER:
        all_pop = [r for r in rows if r.get('side')=='low' and r.get('band')==band
                   and r.get(prefix) is not None]
        if not all_pop:
            continue
        ghost_pop = [r for r in all_pop if ghost_low(r)]
        clean_pop = [r for r in all_pop if not ghost_low(r)]
        c_all = _payout_cell(all_pop, float(K), float(M), int(W), 'low')
        c_ghost = _payout_cell(ghost_pop, float(K), float(M), int(W), 'low')
        c_clean = _payout_cell(clean_pop, float(K), float(M), int(W), 'low')
        if c_all is None:
            continue
        delta = (c_clean['ev'] - c_all['ev']) if c_clean else 0
        ghost_str = (f'{c_ghost["n"]:>8} {c_ghost["p_win"]*100:>11.1f}% '
                     f'{c_ghost["ev"]:>+9.1f}%') if c_ghost else f'{0:>8} {"--":>12} {"--":>10}'
        clean_str = (f'{c_clean["n"]:>8} {c_clean["p_win"]*100:>11.1f}% '
                     f'{c_clean["ev"]:>+9.1f}%') if c_clean else f'{0:>8} {"--":>12} {"--":>10}'
        print(f'  {band:<8} {c_all["n"]:>6} {c_all["p_win"]*100:>9.1f}% '
              f'{c_all["ev"]:>+8.1f}%  {ghost_str}  {clean_str}  {delta:>+7.1f}pp')


def _inspect_90plus_cohort(rows):
    """List every 90+ HIGH peak (sanitized) with symbol, date, key features,
    outcome, and forward return. Goal: see whether the band is dominated by
    a fixed cohort of names (utilities, staples, dividend payers) where mean
    reversion structurally fails."""
    pop = sorted(
        [r for r in rows if r.get('side')=='high' and r.get('band')=='90+'],
        key=lambda r: (r['symbol'], r['date'])
    )
    if not pop:
        print('  (no 90+ rows)')
        return

    print()
    print('=' * 110)
    print(f'90+ HIGH cohort  N={len(pop)}  (after sanitize)')
    print('=' * 110)
    print(f'  {"sym":<8} {"date":<12} {"score":>5} {"ext":>7} {"vel5":>7} '
          f'{"macdh":>7} {"vol_r":>6} {"5d_pr":>7} {"20d_pr":>8} '
          f'{"e200":>7} {"ret30":>8} {"outcome":>9}')
    prefix = 'path_K1.0_M2.0_W63'
    for r in pop:
        outcome = r.get(prefix, '?')[:7]
        print(f'  {str(r["symbol"])[:8]:<8} {str(r["date"]):<12} {r["score"]:>5} '
              f'{(r.get("ext") or 0):>+7.2f} {(r.get("vel5") or 0):>+7.2f} '
              f'{(r.get("macdh") or 0):>+7.3f} {(r.get("vol_ratio") or 0):>6.2f} '
              f'{(r.get("ret_5d_prior") or 0):>+7.2f} {(r.get("ret_20d_prior") or 0):>+8.2f} '
              f'{(r.get("ema200_dist") or 0):>+7.1f} {(r.get("ret30") or 0):>+8.2f} '
              f'{outcome:>9}')

    # Symbol frequency
    print()
    print('  Symbol frequency in 90+ band:')
    from collections import Counter
    freq = Counter(r['symbol'] for r in pop)
    for sym, n in freq.most_common():
        print(f'    {sym:<10} {n}')

    # Sector / type intuition (manually inspect — flag obvious cohorts)
    print()
    print(f'  Total unique symbols: {len(freq)}')
    print(f'  Symbols appearing once: {sum(1 for s, n in freq.items() if n == 1)}')
    print(f'  Symbols appearing 2+ times: {sum(1 for s, n in freq.items() if n >= 2)}')

    # Outcome breakdown
    win = sum(1 for r in pop if r.get(prefix) == 'win')
    stop = sum(1 for r in pop if r.get(prefix) == 'stopout')
    expire = sum(1 for r in pop if r.get(prefix) == 'expire')
    print(f'  Outcomes (K=1/M=2/W=63): win={win}  stop={stop}  expire={expire}')


def _payout_cell_spread(rows_subset, K, M, W, side):
    """Like _payout_cell but prices a vertical spread instead of a naked option.

    HIGH side  -> bear put spread:  long ATM put, short OTM put at S*(1 - K*sig)
    LOW  side  -> bull call spread: long ATM call, short OTM call at S*(1 + K*sig)

    Strike width = the win-target distance, so the spread maxes out exactly
    when the underlying hits the target. Caps both win pnl and loss.
    """
    prefix = f'path_K{K}_M{M}_W{W}'
    cell = [r for r in rows_subset if r.get(prefix) is not None]
    if not cell:
        return None
    T_init = W / 252.0
    win_pnls, stop_pnls, exp_pnls = [], [], []
    for r in cell:
        entry = 1.0
        sigma_ann = r['vol_pct'] / 100.0 * math.sqrt(252)
        if side == 'high':
            K_long = entry
            K_short = entry * (1 - K * r['vol_pct'] / 100.0)
            P_long_0 = _bs_put(entry, K_long, T_init, sigma_ann)
            P_short_0 = _bs_put(entry, K_short, T_init, sigma_ann)
        else:
            K_long = entry
            K_short = entry * (1 + K * r['vol_pct'] / 100.0)
            P_long_0 = _bs_call(entry, K_long, T_init, sigma_ann)
            P_short_0 = _bs_call(entry, K_short, T_init, sigma_ann)
        debit_0 = P_long_0 - P_short_0
        if debit_0 <= 0:
            continue

        outcome = r[prefix]
        day = r.get(prefix + '_day') or W
        exit_ret = r.get(prefix + '_exit_ret') or 0.0
        S_exit = entry * (1 + exit_ret / 100.0)
        T_rem = max(0.0, (W - day) / 252.0)
        if outcome == 'expire':
            T_rem = 0.0

        if side == 'high':
            P_long_x = _bs_put(S_exit, K_long, T_rem, sigma_ann)
            P_short_x = _bs_put(S_exit, K_short, T_rem, sigma_ann)
        else:
            P_long_x = _bs_call(S_exit, K_long, T_rem, sigma_ann)
            P_short_x = _bs_call(S_exit, K_short, T_rem, sigma_ann)
        debit_x = P_long_x - P_short_x

        pnl_pct = (debit_x - debit_0) / debit_0 * 100.0
        if outcome == 'win':
            win_pnls.append(pnl_pct)
        elif outcome == 'stopout':
            stop_pnls.append(pnl_pct)
        else:
            exp_pnls.append(pnl_pct)

    n = len(win_pnls) + len(stop_pnls) + len(exp_pnls)
    if n == 0:
        return None
    p_win = len(win_pnls) / n
    p_stop = len(stop_pnls) / n
    p_exp = len(exp_pnls) / n
    mean_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
    mean_stop = sum(stop_pnls) / len(stop_pnls) if stop_pnls else 0.0
    mean_exp = sum(exp_pnls) / len(exp_pnls) if exp_pnls else 0.0
    ev = p_win * mean_win + p_stop * mean_stop + p_exp * mean_exp
    return {
        'K': K, 'M': M, 'W': W, 'n': n, 'side': side,
        'p_win': p_win, 'p_stop': p_stop, 'p_exp': p_exp,
        'mean_win': mean_win, 'mean_stop': mean_stop, 'mean_exp': mean_exp,
        'ev': ev,
    }


def _spread_test_high_80_84(rows):
    """Compare naked put vs bear put spread on the 80-84 + vol + macdh filter,
    across multiple cells. Goal: see if capping the loss improves EV materially."""
    base = [r for r in rows if r.get('side') == 'high' and r.get('band') == '80-84']
    if not base:
        return

    strategies = [
        ('80-84 ALL', lambda r: True),
        ('80-84 + vol>=1 + macdh<0', lambda r: ((r.get('vol_ratio') or 0) >= 1.0
                                                 and (r.get('macdh') or 0) < 0)),
        ('80-84 + vol>=1 + macdh<-0.5', lambda r: ((r.get('vol_ratio') or 0) >= 1.0
                                                    and (r.get('macdh') or 0) < -0.5)),
    ]
    cells = [(1.0, 2.0, 30), (1.0, 2.0, 42), (1.0, 2.0, 63),
             (1.0, 3.0, 42), (1.0, 3.0, 63),
             (1.5, 3.0, 42), (1.5, 3.0, 63)]

    print()
    print('=' * 110)
    print('BEAR PUT SPREAD vs NAKED PUT  side=high  filter strategies x cells')
    print('=' * 110)
    print(f'  {"strategy":<32} {"K":>4} {"M":>4} {"W":>3} {"N":>5} '
          f'{"naked p_win":>12} {"naked EV":>10} {"spread p_win":>13} {"spread EV":>11} {"delta":>8}')
    print('  ' + '-' * 108)
    for label, fn in strategies:
        subset = [r for r in base if fn(r)]
        for K, M, W in cells:
            naked = _payout_cell(subset, K, M, W, 'high')
            spread = _payout_cell_spread(subset, K, M, W, 'high')
            if naked is None or spread is None:
                continue
            delta = spread['ev'] - naked['ev']
            print(f'  {label:<32} {K:>4.1f} {M:>4.1f} {W:>3} {naked["n"]:>5} '
                  f'{naked["p_win"]*100:>11.1f}% {naked["ev"]:>+9.1f}% '
                  f'{spread["p_win"]*100:>12.1f}% {spread["ev"]:>+10.1f}% '
                  f'{delta:>+7.1f}pp')
        print()


def _train_test_low_strategy(rows, K, M, W, test_days=365):
    """Out-of-sample validation of the session-3 LOW strategy.

    Strategy: score in {16-19} ∪ {23-25}  AND  ret_20d_prior > 0
              AND  vol_ratio >= 1.0  AND  macdh > 0.

    Splits the LOW rows by date: test = newest `test_days`, train = the rest.
    Reports the strategy's performance on each split with the session-3
    decision gates.
    """
    prefix = f'path_K{K}_M{M}_W{W}'
    low_rows = [r for r in rows if r.get('side') == 'low' and r.get(prefix) is not None]
    if not low_rows:
        print('  no LOW rows')
        return

    max_date = max(r['date'] for r in low_rows)
    min_date = min(r['date'] for r in low_rows)
    cutoff = max_date - timedelta(days=test_days)

    train = [r for r in low_rows if r['date'] < cutoff]
    test  = [r for r in low_rows if r['date'] >= cutoff]

    sweet = {'16-19', '23-25'}
    g3 = lambda r: ((r.get('ret_20d_prior') or 0) > 0
                    and (r.get('vol_ratio') or 0) >= 1.0
                    and (r.get('macdh') or 0) > 0)
    strategy = lambda r: g3(r) and r.get('band') in sweet

    print()
    print('=' * 92)
    print('OUT-OF-SAMPLE VALIDATION: LOW 3-gate + sweet-bands strategy')
    print('=' * 92)
    print(f'  Window: {min_date} -> {max_date}  ({(max_date-min_date).days} days)')
    print(f'  Cutoff: {cutoff}  (test = last {test_days} days)')
    print(f'  Train rows: {len(train)}  ({(cutoff-min_date).days}d)')
    print(f'  Test  rows: {len(test)}   ({(max_date-cutoff).days}d)')
    print()
    print(f'  Strategy: score in {{16-19}} U {{23-25}}  AND  ret_20d_prior > 0')
    print(f'            AND  vol_ratio >= 1.0  AND  macdh > 0')
    print(f'  Cell: K={K}s target / M={M}s stop / W={W} trading days')
    print()
    print(f'  {"split":<8} {"window":<26} {"base N":>8} {"strat N":>9} '
          f'{"keep%":>7} {"p_win":>7} {"win_pnl":>9} {"stop_pnl":>10} {"EV%":>8}')
    print('  ' + '-' * 88)

    results = {}
    for label, subset, span_days in [
        ('train', train, (cutoff - min_date).days),
        ('test',  test,  (max_date - cutoff).days),
        ('full',  low_rows, (max_date - min_date).days),
    ]:
        if not subset:
            continue
        filtered = [r for r in subset if strategy(r)]
        c_strat = _payout_cell(filtered, float(K), float(M), int(W), 'low')
        if c_strat is None:
            print(f'  {label:<8} (no strategy hits)')
            continue
        keep = c_strat['n'] / len(subset) * 100
        sub_min = min(r['date'] for r in subset)
        sub_max = max(r['date'] for r in subset)
        win_str = f'{sub_min}->{sub_max}'
        per_year = c_strat['n'] / (span_days / 365.25) if span_days > 0 else 0
        print(f'  {label:<8} {win_str:<26} {len(subset):>8} {c_strat["n"]:>9} '
              f'{keep:>6.1f}% {c_strat["p_win"]*100:>6.1f}% '
              f'{c_strat["mean_win"]:>+8.1f}% {c_strat["mean_stop"]:>+9.1f}% '
              f'{c_strat["ev"]:>+7.1f}%')
        print(f'  {"":<8} (~{per_year:.0f} trades/year)')
        results[label] = c_strat

    # Decision-gate report
    print()
    print('  Decision gates (from CLAUDE.md session-3 pickup recipe):')
    if 'test' in results:
        t = results['test']
        ev = t['ev']
        pwin = t['p_win'] * 100
        n = t['n']
        if ev > 15 and pwin > 63 and n >= 50:
            verdict = '[VALIDATED] ship as entry-filter layer'
        elif ev > 8 and pwin > 60 and n >= 50:
            verdict = '[MARGINAL] real but weaker than in-sample'
        elif n < 50:
            verdict = f'[INSUFFICIENT N] (test N={n}, need >=50)'
        else:
            verdict = '[OVERFIT] does not survive out-of-sample'
        print(f'    test EV={ev:+.1f}%  p_win={pwin:.1f}%  N={n}  ->  {verdict}')

    # Per-band performance on each split
    print()
    print('  Per-band breakdown:')
    print(f'    {"split":<8} {"band":<8} {"raw N":>7} {"filt N":>7} '
          f'{"p_win":>7} {"EV%":>8}')
    bands = ['16-19', '20-22', '23-25', '6-15']
    for label, subset in [('train', train), ('test', test)]:
        for band in bands:
            band_pop = [r for r in subset if r.get('band') == band]
            if not band_pop:
                continue
            filt = [r for r in band_pop if g3(r)]
            c = _payout_cell(filt, float(K), float(M), int(W), 'low')
            if c is None:
                continue
            print(f'    {label:<8} {band:<8} {len(band_pop):>7} {c["n"]:>7} '
                  f'{c["p_win"]*100:>6.1f}% {c["ev"]:>+7.1f}%')

    return results


def _drill_80_84_sub_fingerprint(rows, K, M, W):
    """80-84 is the only positive HIGH band. Drill into it to find the
    maximum-EV sub-fingerprint."""
    prefix = f'path_K{K}_M{M}_W{W}'
    base = [r for r in rows if r.get('side')=='high' and r.get('band')=='80-84'
            and r.get(prefix) is not None]
    if not base:
        return

    filters = {
        'NONE (80-84 baseline)':                          lambda r: True,
        'vol_ratio >= 1.0':                               lambda r: (r.get('vol_ratio') or 0) >= 1.0,
        'vol_ratio >= 1.5':                               lambda r: (r.get('vol_ratio') or 0) >= 1.5,
        'macdh < 0':                                      lambda r: (r.get('macdh') or 0) < 0,
        'macdh < -0.5':                                   lambda r: (r.get('macdh') or 0) < -0.5,
        'macdh < -1.0':                                   lambda r: (r.get('macdh') or 0) < -1.0,
        'vel5 < 1.0 (decelerating)':                      lambda r: (r.get('vel5') or 999) < 1.0,
        'vel5 < 0 (already declining)':                   lambda r: (r.get('vel5') or 999) < 0,
        'vol>=1 + macdh<0':                               lambda r: ((r.get('vol_ratio') or 0) >= 1.0
                                                                       and (r.get('macdh') or 0) < 0),
        'vol>=1 + macdh<-0.5':                            lambda r: ((r.get('vol_ratio') or 0) >= 1.0
                                                                       and (r.get('macdh') or 0) < -0.5),
        'vol>=1 + macdh<0 + vel5<1':                      lambda r: ((r.get('vol_ratio') or 0) >= 1.0
                                                                       and (r.get('macdh') or 0) < 0
                                                                       and (r.get('vel5') or 999) < 1.0),
        'vol>=1.5 + macdh<-0.5 + vel5<1':                 lambda r: ((r.get('vol_ratio') or 0) >= 1.5
                                                                       and (r.get('macdh') or 0) < -0.5
                                                                       and (r.get('vel5') or 999) < 1.0),
    }

    print()
    print('=' * 92)
    print(f'80-84 SUB-FINGERPRINT DRILL  cell K={K}/M={M}/W={W}  base N={len(base)}')
    print('=' * 92)
    print(f'  {"filter":<46} {"N":>5} {"keep%":>7} {"p_win":>7} '
          f'{"win_pnl":>9} {"stop_pnl":>10} {"EV%":>8}')
    for label, fn in filters.items():
        subset = [r for r in base if fn(r)]
        c = _payout_cell(subset, float(K), float(M), int(W), 'high')
        if c is None:
            print(f'  {label:<46} (no data)')
            continue
        keep = c['n'] / len(base) * 100
        print(f'  {label:<46} {c["n"]:>5} {keep:>6.1f}% '
              f'{c["p_win"]*100:>6.1f}% {c["mean_win"]:>+8.1f}% '
              f'{c["mean_stop"]:>+9.1f}% {c["ev"]:>+7.1f}%')


def _validate_exhaustion_paradox(rows, K, M, W):
    """Test the FINDING #6 hypothesis: at 90+ the score fires on mid-rally
    continuation, not exhaustion. Predicted: 90+ peaks with macdh<-0.5
    (which look more like 80-84) should perform better; 90+ peaks with
    macdh>-0.5 (still pushing) should perform worse.

    Also: the 'macdh < -0.5' filter alone, applied across every band,
    should improve EV monotonically (proving macdh-cracking is a universal
    underpriced signal disguised by the score formula's right tail)."""
    prefix = f'path_K{K}_M{M}_W{W}'
    print()
    print('=' * 92)
    print(f'EXHAUSTION-PARADOX TEST  cell K={K}/M={M}/W={W}')
    print('=' * 92)
    print('  Per-band: split by macdh threshold')
    print(f'  {"band":<8} {"split":<18} {"N":>5} {"p_win":>7} '
          f'{"win_pnl":>9} {"stop_pnl":>10} {"EV%":>8}')
    for band in HIGH_BANDS:
        base = [r for r in rows if r.get('side')=='high' and r.get('band')==band
                and r.get(prefix) is not None]
        if not base:
            continue
        for label, fn in [
            ('all',                lambda r: True),
            ('macdh < -0.5',       lambda r: (r.get('macdh') or 0) < -0.5),
            ('macdh in [-0.5, 0]', lambda r: -0.5 <= (r.get('macdh') or 0) < 0),
            ('macdh >= 0',         lambda r: (r.get('macdh') or 0) >= 0),
        ]:
            subset = [r for r in base if fn(r)]
            if len(subset) < 5:
                continue
            c = _payout_cell(subset, float(K), float(M), int(W), 'high')
            if c is None:
                continue
            print(f'  {band:<8} {label:<18} {c["n"]:>5} '
                  f'{c["p_win"]*100:>6.1f}% {c["mean_win"]:>+8.1f}% '
                  f'{c["mean_stop"]:>+9.1f}% {c["ev"]:>+7.1f}%')
        print()


def _train_test_high_strategy(rows, K, M, W, test_days=365):
    """Out-of-sample validation for the HIGH ghost-gated 80-84 strategy.

    Strategy: band == '80-84'  AND  vol_ratio >= 1.0  AND  macdh < 0
              AND  NOT (ret_20d_prior < -10 AND ext < 0)   ← ghost exclusion

    Compares against a baseline (no ghost gate) so we can quantify the lift.
    """
    prefix = f'path_K{K}_M{M}_W{W}'
    high_rows = [r for r in rows if r.get('side') == 'high'
                 and r.get(prefix) is not None]
    if not high_rows:
        print('  no HIGH rows')
        return

    max_date = max(r['date'] for r in high_rows)
    min_date = min(r['date'] for r in high_rows)
    cutoff = max_date - timedelta(days=test_days)

    train = [r for r in high_rows if r['date'] < cutoff]
    test  = [r for r in high_rows if r['date'] >= cutoff]

    is_ghost = lambda r: ((r.get('ret_20d_prior') or 0) < -10
                          and (r.get('ext') or 0) < 0)
    base_filter = lambda r: (r.get('band') == '80-84'
                             and (r.get('vol_ratio') or 0) >= 1.0
                             and (r.get('macdh') or 0) < 0)
    full_filter = lambda r: base_filter(r) and not is_ghost(r)

    print()
    print('=' * 92)
    print('OUT-OF-SAMPLE VALIDATION: HIGH ghost-gated 80-84 strategy')
    print('=' * 92)
    print(f'  Window: {min_date} -> {max_date}  ({(max_date-min_date).days} days)')
    print(f'  Cutoff: {cutoff}  (test = last {test_days} days)')
    print(f'  Train rows: {len(train)}  ({(cutoff-min_date).days}d)')
    print(f'  Test  rows: {len(test)}   ({(max_date-cutoff).days}d)')
    print()
    print(f'  Cell: K={K}s target / M={M}s stop / W={W} trading days')
    print(f'  Strategy: band==80-84  AND  vol_ratio>=1.0  AND  macdh<0')
    print(f'            AND  NOT (ret_20d_prior < -10 AND ext < 0)  [ghost gate]')
    print()
    print(f'  {"split":<8} {"variant":<18} {"base N":>8} {"strat N":>9} '
          f'{"keep%":>7} {"p_win":>7} {"win_pnl":>9} {"stop_pnl":>10} {"EV%":>8}')
    print('  ' + '-' * 92)

    results = {}
    for label, subset, span_days in [
        ('train', train, (cutoff - min_date).days),
        ('test',  test,  (max_date - cutoff).days),
        ('full',  high_rows, (max_date - min_date).days),
    ]:
        if not subset:
            continue
        for variant_name, fn in [('baseline', base_filter), ('+ ghost gate', full_filter)]:
            filtered = [r for r in subset if fn(r)]
            c = _payout_cell(filtered, float(K), float(M), int(W), 'high')
            if c is None:
                print(f'  {label:<8} {variant_name:<18} (no strategy hits)')
                continue
            keep = c['n'] / len(subset) * 100
            print(f'  {label:<8} {variant_name:<18} {len(subset):>8} {c["n"]:>9} '
                  f'{keep:>6.1f}% {c["p_win"]*100:>6.1f}% '
                  f'{c["mean_win"]:>+8.1f}% {c["mean_stop"]:>+9.1f}% '
                  f'{c["ev"]:>+7.1f}%')
            results[(label, variant_name)] = c
        print()

    # Decision-gate report (test split, full strategy)
    print('  Decision gates:')
    if ('test', '+ ghost gate') in results:
        t = results[('test', '+ ghost gate')]
        b = results.get(('test', 'baseline'))
        ev = t['ev']
        pwin = t['p_win'] * 100
        n = t['n']
        lift_str = ''
        if b is not None:
            lift_str = f'  (lift over baseline: {(ev - b["ev"]):+.1f}pp EV)'
        if ev > 8 and pwin > 60 and n >= 30:
            verdict = '[VALIDATED] real edge, ship as HIGH-side filter'
        elif ev > 3 and pwin > 55 and n >= 30:
            verdict = '[MARGINAL] positive but small'
        elif n < 30:
            verdict = f'[INSUFFICIENT N] (test N={n}, need >=30)'
        else:
            verdict = '[OVERFIT] does not survive out-of-sample'
        print(f'    test EV={ev:+.1f}%  p_win={pwin:.1f}%  N={n}  ->  {verdict}{lift_str}')

    return results


def run_high_validation(days=1095, test_days=365, K='1.0', M='2.0', W='42'):
    """Out-of-sample validation runner for the HIGH ghost-gated 80-84 strategy."""
    from assess_scores import extract_peaks
    from collections import defaultdict
    v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
    if not v3:
        print('V3 (id=3) not found in algorithm_versions')
        return

    print(f'Pulling V3 peaks for last {days}d ...')
    peaks = extract_peaks(lookback_days=days, version=v3)
    high_peaks = [p for p in peaks if p.overall >= 70]
    print(f'  {len(high_peaks)} HIGH peaks')

    by_sym = defaultdict(list)
    for p in high_peaks:
        by_sym[str(p.symbol_id)].append((p.date, 'high', p.overall))

    rows = []
    syms = sorted(by_sym.keys())
    for i, sym in enumerate(syms, 1):
        if i % 50 == 0:
            print(f'    [{i}/{len(syms)}]')
        feats = _bulk_features_for_symbol(sym, by_sym[sym])
        for d, f in feats.items():
            rows.append(f)
    rows = _sanitize_split_corruption(rows)
    print(f'  After sanitize: {len(rows)} rows')

    _train_test_high_strategy(rows, K, M, W, test_days=test_days)


def run_low_validation(days=1095, test_days=365):
    """Out-of-sample validation runner for the LOW 3-gate+sweet-bands strategy.
    Pulls only LOW peaks to keep feature compute fast."""
    from assess_scores import extract_peaks
    v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
    if not v3:
        print('V3 (id=3) not found in algorithm_versions')
        return

    print(f'Pulling V3 peaks for last {days}d ...')
    peaks = extract_peaks(lookback_days=days, version=v3)
    low_peaks = [p for p in peaks if p.overall <= 25]
    print(f'  {len(peaks)} total peaks, {len(low_peaks)} LOW (<=25)')

    from collections import defaultdict
    by_sym = defaultdict(list)
    for p in low_peaks:
        by_sym[str(p.symbol_id)].append((p.date, 'low', p.overall))

    print(f'  {len(by_sym)} unique symbols. Computing features...')
    rows = []
    syms = sorted(by_sym.keys())
    n = len(syms)
    for i, sym in enumerate(syms, 1):
        if i % 50 == 0 or i == n:
            print(f'    [{i}/{n}] {sym}')
        feats = _bulk_features_for_symbol(sym, by_sym[sym])
        for d, f in feats.items():
            rows.append(f)

    print(f'  Computed features for {len(rows)} LOW peaks')
    if not rows:
        return

    rows = _sanitize_split_corruption(rows)
    print(f'  After sanitize: {len(rows)} rows')

    # Run the canonical session-3 winning cell K=2/M=5/W=30
    _train_test_low_strategy(rows, '2.0', '5.0', '30', test_days=test_days)
    return rows


def run_high_scan(days=365):
    """Lean HIGH-side-only scan. Pulls only HIGH peaks, computes features,
    runs the band-investigation pipeline."""
    from assess_scores import extract_peaks
    v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
    if not v3:
        print('V3 (id=3) not found in algorithm_versions')
        return

    print(f'Pulling V3 peaks for last {days}d ...')
    peaks = extract_peaks(lookback_days=days, version=v3)
    high_peaks = [p for p in peaks if p.overall >= 70]
    print(f'  {len(peaks)} total peaks, {len(high_peaks)} HIGH (>=70)')

    from collections import defaultdict
    by_sym = defaultdict(list)
    for p in high_peaks:
        by_sym[str(p.symbol_id)].append((p.date, 'high', p.overall))

    print(f'  {len(by_sym)} unique symbols. Computing features...')
    rows = []
    syms = sorted(by_sym.keys())
    n = len(syms)
    for i, sym in enumerate(syms, 1):
        if i % 50 == 0 or i == n:
            print(f'    [{i}/{n}] {sym}')
        feats = _bulk_features_for_symbol(sym, by_sym[sym])
        for d, f in feats.items():
            rows.append(f)

    print(f'  Computed features for {len(rows)} HIGH peaks')
    if not rows:
        return

    # Distribution per band
    print()
    print('=' * 92)
    print('HIGH-side band counts')
    print('=' * 92)
    for b in HIGH_BANDS:
        n_b = sum(1 for r in rows if r.get('band') == b)
        print(f'  {b:<8} N={n_b}')

    # Iter 1: band x cell EV table
    _high_band_cell_table(rows)

    # Iter 1b: cross-band feature means
    _high_cross_band_features(rows)

    # Iter 2: per-band winner vs stopout drill at the canonical best HIGH cell
    print()
    print('=' * 92)
    print('Per-band winner-vs-stopout drill (HIGH best cell K=1.0/M=2.0/W=63)')
    print('=' * 92)
    _high_per_band_compare(rows, '1.0', '2.0', '63')

    # Iter 3: HIGH filter tests at multiple cells
    _high_filter_test(rows, '1.0', '2.0', '63')
    _high_filter_test(rows, '1.0', '2.0', '30')
    _high_filter_test(rows, '2.0', '5.0', '30')

    # ── ITERATION 2 ────────────────────────────────────────────────
    print()
    print('#' * 92)
    print('# ITERATION 2: strip split corruption + test new hypotheses')
    print('#' * 92)
    print()
    print('Sanitizing rows (drop |ext|>50% or bb_pct outside [-1, 2]) ...')
    clean = _sanitize_split_corruption(rows)

    print()
    print('=' * 92)
    print('HIGH-side band counts AFTER sanitize')
    print('=' * 92)
    for b in HIGH_BANDS:
        n_b = sum(1 for r in clean if r.get('band') == b)
        print(f'  {b:<8} N={n_b}')

    _high_band_cell_table(clean)
    _high_cross_band_features(clean)

    # Hypothesis 1: vol_ratio scaling — does threshold replicate higher band EV?
    _hypothesis_vol_replicates_score(clean, '1.0', '2.0', '63')

    # Hypothesis 2: rally + crack + vol fingerprint
    _hypothesis_universal_fingerprint(clean, '1.0', '2.0', '63')
    _hypothesis_universal_fingerprint(clean, '1.0', '2.0', '30')

    # ── ITERATION 3 ────────────────────────────────────────────────
    print()
    print('#' * 92)
    print('# ITERATION 3: 80-84 sub-drill + exhaustion-paradox validation')
    print('#' * 92)

    _drill_80_84_sub_fingerprint(clean, '1.0', '2.0', '63')
    _drill_80_84_sub_fingerprint(clean, '1.0', '2.0', '42')
    _validate_exhaustion_paradox(clean, '1.0', '2.0', '63')

    return clean


def run_scan(days=365):
    from assess_scores import extract_peaks
    v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
    if not v3:
        print('V3 (id=3) not found in algorithm_versions')
        return

    print(f'Pulling V3 peaks for last {days}d ...')
    peaks = extract_peaks(lookback_days=days, version=v3)
    high_peaks = [p for p in peaks if p.overall >= 70]
    low_peaks  = [p for p in peaks if p.overall <= 25]
    print(f'  {len(peaks)} total peaks, {len(high_peaks)} HIGH (>=70), {len(low_peaks)} LOW (<=25)')

    # Group by symbol; spec = (date, side, score)
    from collections import defaultdict
    by_sym = defaultdict(list)
    for p in high_peaks:
        by_sym[str(p.symbol_id)].append((p.date, 'high', p.overall))
    for p in low_peaks:
        by_sym[str(p.symbol_id)].append((p.date, 'low', p.overall))

    print(f'  {len(by_sym)} unique symbols. Computing features...')

    rows = []
    syms = sorted(by_sym.keys())
    n = len(syms)
    for i, sym in enumerate(syms, 1):
        if i % 50 == 0 or i == n:
            print(f'    [{i}/{n}] {sym}')
        feats = _bulk_features_for_symbol(sym, by_sym[sym])
        for d, f in feats.items():
            rows.append(f)

    print(f'  Computed features for {len(rows)} peaks (some skipped: missing fwd data or indicators)')
    if not rows:
        return

    high_rows = [r for r in rows if r.get('side') == 'high']
    low_rows  = [r for r in rows if r.get('side') == 'low']
    nh, mh, sh, wh = _stats([r['ret30'] for r in high_rows], side='high')
    nl, ml, sl, wl = _stats([r['ret30'] for r in low_rows],  side='low')
    print()
    print('=' * 80)
    print('Overall distribution by side')
    print('=' * 80)
    print(f'  HIGH peaks (>=70, short thesis): N={nh}  mean ret30={mh:+.2f}%  '
          f'std={sh:.2f}%  WR30(short)={wh:.1f}%')
    print(f'  LOW  peaks (<=25, long  thesis): N={nl}  mean ret30={ml:+.2f}%  '
          f'std={sl:.2f}%  WR30(long) ={wl:.1f}%')
    print(f'  (CLAUDE.md baseline: HIGH 70+ N=2218, WR30=59.5%)')

    print()
    print('=' * 80)
    print('Marginal distributions — HIGH peaks (short thesis)')
    print('=' * 80)
    for key in ('sacc', 'ext', 'matur', 'vel5', 'rsi', 'stoch'):
        _print_marginal(key, high_rows, key, side='high')

    print()
    print('=' * 80)
    print('Marginal distributions — LOW peaks (long thesis, WR = ret30 > +1%)')
    print('=' * 80)
    for key in ('sacc', 'ext', 'matur', 'vel5', 'rsi', 'stoch'):
        _print_marginal(key, low_rows, key, side='low')

    print()
    print('=' * 80)
    print('Joint distribution: ext x sacc')
    print('=' * 80)
    _print_joint(rows, 'ext', 'sacc')
    _print_high_variance_cells(rows, 'ext', 'sacc')

    print()
    print('=' * 80)
    print('Joint distribution: ext x matur')
    print('=' * 80)
    _print_joint(rows, 'ext', 'matur')

    print()
    print('=' * 80)
    print('Co-location test')
    print('=' * 80)
    _co_location_test(rows)

    # ── Volatility-normalized analysis ─────────────────────────────────
    _print_swing_overview(rows)
    _print_window_tightness(rows)
    _print_path_dependent_options(rows)
    _print_payout_asymmetry(rows)
    _print_mae_before_mfe(rows)

    print()
    print('=' * 80)
    print('Vol-normalized swing-WR marginals (K = 2.0s default)')
    print('=' * 80)
    for key in ('sacc', 'ext', 'matur', 'vel5', 'rsi', 'stoch'):
        _print_swing_marginal(key, rows, key, K=2.0)

    print()
    print('=' * 80)
    print('Vol-normalized swing joint heatmap (ext x sacc, K = 2.0s)')
    print('=' * 80)
    _print_swing_joint(rows, 'ext', 'sacc', K=2.0)

    print()
    print('=' * 80)
    print('Implied DTE recommendation by ext quintile')
    print('=' * 80)
    _print_dte_table(rows, K=2.0)

    # Pattern hunting: what differentiates a 23-25 LOW WINNER from a STOPOUT
    # at the empirically best CALL cell?
    _compare_winners_losers(rows, side='low', band='23-25')
    _compare_winners_losers(rows, side='low', band='20-22')
    _compare_winners_losers(rows, side='low', band='16-19')

    # Test pre-entry filters at the best CALL cell across the full LOW universe
    _filter_test(rows, side='low', K='2.0', M='5.0', W='30')

    # Iteration: filter combo x band intersection
    _filter_test_per_band(rows, K='2.0', M='5.0', W='30')

    # Iteration: drill into stopouts that survived the combo filter
    _drill_residual_stopouts(rows, K='2.0', M='5.0', W='30')

    # Iteration 3: 4-gate filter + band union strategy
    _final_strategy_test(rows, K='2.0', M='5.0', W='30')


# ── Run ─────────────────────────────────────────────────────────────────────

def main():
    import sys
    args = sys.argv[1:]
    if '--scan' in args:
        days = 365
        if '--days' in args:
            days = int(args[args.index('--days') + 1])
        run_scan(days=days)
        return

    if '--high' in args:
        days = 365
        if '--days' in args:
            days = int(args[args.index('--days') + 1])
        run_high_scan(days=days)
        return

    if '--spread-high' in args:
        days = 1095
        if '--days' in args:
            days = int(args[args.index('--days') + 1])
        from assess_scores import extract_peaks
        from collections import defaultdict
        v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
        print(f'Pulling V3 peaks for last {days}d ...')
        peaks = extract_peaks(lookback_days=days, version=v3)
        high_peaks = [p for p in peaks if p.overall >= 70]
        print(f'  {len(high_peaks)} HIGH peaks')
        by_sym = defaultdict(list)
        for p in high_peaks:
            by_sym[str(p.symbol_id)].append((p.date, 'high', p.overall))
        rows = []
        syms = sorted(by_sym.keys())
        for i, sym in enumerate(syms, 1):
            if i % 50 == 0:
                print(f'    [{i}/{len(syms)}]')
            feats = _bulk_features_for_symbol(sym, by_sym[sym])
            for d, f in feats.items():
                rows.append(f)
        rows = _sanitize_split_corruption(rows)
        _spread_test_high_80_84(rows)
        return

    if '--cohort-90' in args:
        days = 1095
        if '--days' in args:
            days = int(args[args.index('--days') + 1])
        from assess_scores import extract_peaks
        from collections import defaultdict
        v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
        print(f'Pulling V3 peaks for last {days}d ...')
        peaks = extract_peaks(lookback_days=days, version=v3)
        high_peaks = [p for p in peaks if p.overall >= 90]
        print(f'  {len(high_peaks)} HIGH 90+ peaks')
        by_sym = defaultdict(list)
        for p in high_peaks:
            by_sym[str(p.symbol_id)].append((p.date, 'high', p.overall))
        rows = []
        for sym in sorted(by_sym.keys()):
            feats = _bulk_features_for_symbol(sym, by_sym[sym])
            for d, f in feats.items():
                rows.append(f)
        rows = _sanitize_split_corruption(rows)
        _inspect_90plus_cohort(rows)
        return

    if '--ghost-test' in args:
        days = 1095
        if '--days' in args:
            days = int(args[args.index('--days') + 1])
        from assess_scores import extract_peaks
        from collections import defaultdict
        v3 = AlgorithmVersion.get_or_none(AlgorithmVersion.id == 3)
        print(f'Pulling V3 peaks for last {days}d ...')
        peaks = extract_peaks(lookback_days=days, version=v3)
        by_sym = defaultdict(list)
        for p in peaks:
            if p.overall >= 70:
                by_sym[str(p.symbol_id)].append((p.date, 'high', p.overall))
            elif p.overall <= 25:
                by_sym[str(p.symbol_id)].append((p.date, 'low', p.overall))
        n_high = sum(1 for s in by_sym.values() for x in s if x[1]=='high')
        n_low  = sum(1 for s in by_sym.values() for x in s if x[1]=='low')
        print(f'  {n_high} HIGH peaks, {n_low} LOW peaks across {len(by_sym)} symbols')
        rows = []
        syms = sorted(by_sym.keys())
        for i, sym in enumerate(syms, 1):
            if i % 50 == 0:
                print(f'    [{i}/{len(syms)}]')
            feats = _bulk_features_for_symbol(sym, by_sym[sym])
            for d, f in feats.items():
                rows.append(f)
        rows = _sanitize_split_corruption(rows)
        print(f'  After sanitize: {len(rows)} rows')
        # HIGH side: best naked-put cell
        _sign_bug_ghost_test(rows, '1.0', '2.0', '63')
        # LOW side: best call cell K=1.0/M=5.0/W=21
        _sign_bug_ghost_test(rows, '1.0', '5.0', '21')
        return

    if '--validate-high' in args:
        days = 1095
        test_days = 365
        if '--days' in args:
            days = int(args[args.index('--days') + 1])
        if '--test-days' in args:
            test_days = int(args[args.index('--test-days') + 1])
        run_high_validation(days=days, test_days=test_days)
        return

    if '--validate-low' in args:
        days = 1095
        test_days = 365
        if '--days' in args:
            days = int(args[args.index('--days') + 1])
        if '--test-days' in args:
            test_days = int(args[args.index('--test-days') + 1])
        run_low_validation(days=days, test_days=test_days)
        return

    rows = []
    print('Probing canonical examples from CLAUDE.md investigation...')
    for sym, d, label, expected in KILLER_CASES:
        r = probe_one(sym, d, label, expected)
        if r is None:
            print(f'  WARN: no data for {sym} {d}')
        rows.append(r)

    print('Searching V3 (id=3) for control cases (correct HIGHs)...')
    controls = find_correct_v3_highs(n=3, score_min=85, ret_max=-3.0)
    for sym, d, label, expected, ret in controls:
        r = probe_one(sym, d, label, expected, prefilled_ret=ret)
        if r:
            rows.append(r)

    print_results(rows)


if __name__ == '__main__':
    main()
