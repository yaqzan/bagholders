"""
equity_wave/analyze.py — the CORE A/B question of Track A.

Given per-path MC daily equity curves, quantify whether ANY equity-curve-native
signal (running drawdown, weekly state, curve momentum) has predictive power for
the NEXT-WINDOW return/DD *beyond* the market-context regime-strength index from
wave.py. The cautionary case (Jan2025 +51% -> Mar -28%) is the null hypothesis we
expect to confirm: curve momentum does NOT predict the next jump.

WHAT IT MEASURES (per path, rolling a forward window of H trading days):
  For each anchor day t with at least H forward bars:
    forward_ret_H(t) = equity[t+H]/equity[t] - 1
    forward_maxdd_H(t) = max running drawdown of equity[t..t+H] (from the t..t+H peak)
  Predictors known AT t (no look-ahead):
    EQUITY-NATIVE:  dd_t (drawdown-from-peak), weekly_state_t (one-hot),
                    mom4w_t (4-week trailing curve log-return)
    MARKET-CONTEXT: strength_t (regime_strength_index of the market maps at t)
                    -- supplied alongside the curve, OR set to None if unavailable
                       (then only the equity-native vs constant comparison runs).

  Then:
    (a) Spearman/Pearson corr of each predictor with forward_ret_H and forward_maxdd_H
        (pooled across paths) + per-state forward-return means with a between-state
        F-like dispersion.
    (b) INCREMENTAL test: does adding the equity-native predictors to a market-context-
        only baseline reduce out-of-sample squared error of forward_ret_H?
        Two nested ridge-free OLS models (closed-form, numpy):
            M0: forward ~ 1 + strength
            M1: forward ~ 1 + strength + dd + mom4w + state_onehot
        Report R^2 of each (in-sample) AND a K-fold-by-PATH out-of-sample R^2 so the
        equity-native increment can't win by overfitting path-specific noise. The
        A/B verdict is: equity-native adds power IFF OOS R^2(M1) - R^2(M0) is
        positive and beyond a small noise band.

This script is INPUT-AGNOSTIC about where the curves come from:
  * primary: a JSON/npz produced by an MC arm with MC_RETURN_PATHS + daily curves
    (the documented ~6-line monte_carlo.py extension -- see README "DAILY-PATH EXPORT").
  * it also accepts a list of (dates, equity, strength_series) tuples directly.
  * `python analyze.py --selftest` runs the whole pipeline on SYNTHETIC curves
    (no MC, no MySQL) and asserts the logic is sound -- runnable NOW.

Pure numpy + stdlib. No MySQL, no engine import.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

from wave import (  # noqa: E402
    drawdown_from_peak, weekly_downsample, weekly_log_returns,
    WeeklyStateClassifier, S_STATES,
)

H_DEFAULT = 21          # forward window (trading days ~ 1 month)
MOM_WEEKS = 4           # trailing curve-momentum lookback (weeks)
BARS_PER_WEEK = 5


# ---------------------------------------------------------------------------
# feature extraction for one path
# ---------------------------------------------------------------------------
def _state_index(equity, bars_per_week=BARS_PER_WEEK):
    """Daily-aligned weekly-state index per daily bar (forward-filled from the
    weekly classification of the most recent completed week). NO look-ahead: the
    state at daily bar i uses only weeks completed at or before i."""
    n = len(equity)
    _, we = weekly_downsample(None, equity, bars_per_week)
    wlr = weekly_log_returns(we)
    wdd = drawdown_from_peak(we)
    clf = WeeklyStateClassifier()
    clf.reset()
    weekly_states = []
    for r, d in zip(wlr, wdd):
        weekly_states.append(clf.step(r, d))
    # map each daily bar to the index of the last completed week at/<= that bar
    daily_state = []
    for i in range(n):
        wk = (i + 1) // bars_per_week - 1   # last fully completed week index
        wk = max(0, min(len(weekly_states) - 1, wk)) if weekly_states else 0
        daily_state.append(weekly_states[wk] if weekly_states else S_STATES[0])
    return daily_state


def _curve_momentum(equity, weeks=MOM_WEEKS, bars_per_week=BARS_PER_WEEK):
    """Trailing log-return over `weeks` weeks ending at each daily bar (no look-ahead)."""
    n = len(equity)
    lag = weeks * bars_per_week
    out = np.zeros(n)
    eq = np.asarray([float(x) if x and x > 0 else np.nan for x in equity], dtype=float)
    for i in range(n):
        j = i - lag
        if j >= 0 and eq[j] > 0 and eq[i] > 0:
            out[i] = math.log(eq[i] / eq[j])
        else:
            out[i] = 0.0
    return out


def extract_path_features(equity, strength_series=None, H=H_DEFAULT):
    """Return a dict of arrays for one path: predictors @ t and forward targets.
    strength_series: per-daily-bar market-context strength in [0,1], or None."""
    eq = np.asarray([float(x) for x in equity], dtype=float)
    n = len(eq)
    dd = np.asarray(drawdown_from_peak(eq), dtype=float)
    states = _state_index(eq)
    mom = _curve_momentum(eq)
    if strength_series is None:
        strength = np.full(n, np.nan)
    else:
        strength = np.asarray([float(x) if x is not None else np.nan for x in strength_series], dtype=float)

    rows = []
    last = n - H
    for t in range(last):
        if eq[t] <= 0:
            continue
        fwd_ret = eq[t + H] / eq[t] - 1.0
        seg = eq[t:t + H + 1]
        pk = -math.inf
        mdd = 0.0
        for v in seg:
            if v > pk:
                pk = v
            if pk > 0:
                mdd = max(mdd, (pk - v) / pk)
        oh = [1.0 if states[t] == s else 0.0 for s in S_STATES]
        rows.append({
            'fwd_ret': fwd_ret, 'fwd_maxdd': mdd,
            'dd': dd[t], 'mom4w': mom[t], 'strength': strength[t],
            'state': states[t], 'state_oh': oh,
        })
    return rows


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def _pearson(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return float('nan'), len(x)
    return float(np.corrcoef(x, y)[0, 1]), len(x)


def _spearman(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 3:
        return float('nan'), len(x)
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    if rx.std() == 0 or ry.std() == 0:
        return float('nan'), len(x)
    return float(np.corrcoef(rx, ry)[0, 1]), len(x)


def _ols_r2(X, y):
    """In-sample R^2 of OLS y ~ X (X already has intercept col). numpy lstsq."""
    m = np.all(np.isfinite(X), axis=1) & np.isfinite(y)
    X, y = X[m], y[m]
    if len(y) < X.shape[1] + 2:
        return float('nan'), None
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot <= 0:
        return float('nan'), beta
    return 1.0 - ss_res / ss_tot, beta


def _kfold_oos_r2_by_path(Xrows, yrows, path_ids, k=5):
    """Out-of-sample R^2 with folds split BY PATH (so a path's noise can't leak
    train->test). Returns mean OOS R^2 across folds, or nan if too few paths."""
    paths = sorted(set(path_ids))
    if len(paths) < k:
        k = max(2, len(paths))
    if len(paths) < 2:
        return float('nan')
    rng = np.random.RandomState(0)
    perm = list(paths)
    rng.shuffle(perm)
    folds = [perm[i::k] for i in range(k)]
    X = np.asarray(Xrows, float); y = np.asarray(yrows, float); pid = np.asarray(path_ids)
    r2s = []
    for f in folds:
        test_mask = np.isin(pid, f)
        train_mask = ~test_mask
        Xtr, ytr = X[train_mask], y[train_mask]
        Xte, yte = X[test_mask], y[test_mask]
        good_tr = np.all(np.isfinite(Xtr), axis=1) & np.isfinite(ytr)
        good_te = np.all(np.isfinite(Xte), axis=1) & np.isfinite(yte)
        Xtr, ytr = Xtr[good_tr], ytr[good_tr]
        Xte, yte = Xte[good_te], yte[good_te]
        if len(ytr) < Xtr.shape[1] + 2 or len(yte) < 3:
            continue
        beta, *_ = np.linalg.lstsq(Xtr, ytr, rcond=None)
        pred = Xte @ beta
        ss_res = float(np.sum((yte - pred) ** 2))
        ss_tot = float(np.sum((yte - ytr.mean()) ** 2))   # baseline = TRAIN mean (honest OOS)
        if ss_tot <= 0:
            continue
        r2s.append(1.0 - ss_res / ss_tot)
    return float(np.mean(r2s)) if r2s else float('nan')


# ---------------------------------------------------------------------------
# the A/B
# ---------------------------------------------------------------------------
def run_ab(paths, H=H_DEFAULT, noise_band=0.005):
    """paths: list of (dates_or_None, equity_list, strength_series_or_None).
    Returns a verdict dict. Pooled across all paths; OOS folds split by path."""
    all_rows = []
    path_ids = []
    has_strength = False
    for pid, (dates, eq, strength) in enumerate(paths):
        rows = extract_path_features(eq, strength, H=H)
        for r in rows:
            all_rows.append(r)
            path_ids.append(pid)
        if strength is not None:
            has_strength = True

    if not all_rows:
        return {'error': 'no rows (curves too short for H)', 'n': 0}

    fwd_ret = [r['fwd_ret'] for r in all_rows]
    fwd_dd = [r['fwd_maxdd'] for r in all_rows]
    dd = [r['dd'] for r in all_rows]
    mom = [r['mom4w'] for r in all_rows]
    strength = [r['strength'] for r in all_rows]

    # (a) univariate correlations
    corr = {}
    for name, x in (('dd', dd), ('mom4w', mom), ('strength', strength)):
        pr, npr = _pearson(x, fwd_ret)
        sr, _ = _spearman(x, fwd_ret)
        prd, _ = _pearson(x, fwd_dd)
        corr[name] = {
            'pearson_fwd_ret': pr, 'spearman_fwd_ret': sr,
            'pearson_fwd_maxdd': prd, 'n': npr,
        }

    # per-state forward-return means (does the curve STATE separate forward outcomes?)
    state_means = {}
    for s in S_STATES:
        vals = [r['fwd_ret'] for r in all_rows if r['state'] == s]
        if vals:
            state_means[s] = {'mean_fwd_ret': float(np.mean(vals)),
                              'median_fwd_ret': float(np.median(vals)),
                              'n': len(vals)}
    # between-state dispersion (how much the state explains, eta^2-ish)
    grand = float(np.mean(fwd_ret))
    ss_between = sum(v['n'] * (v['mean_fwd_ret'] - grand) ** 2 for v in state_means.values())
    ss_total = float(np.sum((np.asarray(fwd_ret) - grand) ** 2))
    eta2_state = ss_between / ss_total if ss_total > 0 else float('nan')

    # (b) nested-model incremental OOS R^2
    n = len(all_rows)
    intercept = np.ones((n, 1))
    col_strength = np.asarray(strength, float).reshape(-1, 1)
    col_dd = np.asarray(dd, float).reshape(-1, 1)
    col_mom = np.asarray(mom, float).reshape(-1, 1)
    state_oh = np.asarray([r['state_oh'][:-1] for r in all_rows], float)  # drop last (collinear w/ intercept)
    y = np.asarray(fwd_ret, float)

    result = {
        'H': H, 'n_anchor_rows': n, 'n_paths': len(paths),
        'has_market_strength': has_strength,
        'univariate_corr': corr,
        'state_forward_means': state_means,
        'eta2_state_on_fwd_ret': eta2_state,
    }

    equity_native = np.hstack([col_dd, col_mom, state_oh])
    if has_strength and np.isfinite(col_strength).any():
        X0 = np.hstack([intercept, col_strength])                 # market-context only
        X1 = np.hstack([intercept, col_strength, equity_native])  # + equity-native
        baseline_name = 'M0: 1 + strength(market)'
    else:
        # no market strength supplied: compare equity-native vs constant-only
        X0 = intercept
        X1 = np.hstack([intercept, equity_native])
        baseline_name = 'M0: 1 (constant)'

    is0, _ = _ols_r2(X0, y)
    is1, _ = _ols_r2(X1, y)
    oos0 = _kfold_oos_r2_by_path(X0, y, path_ids)
    oos1 = _kfold_oos_r2_by_path(X1, y, path_ids)

    oos_gain = (oos1 - oos0) if (oos1 == oos1 and oos0 == oos0) else float('nan')
    result['nested_models'] = {
        'baseline': baseline_name,
        'insample_r2_M0': is0, 'insample_r2_M1': is1,
        'oos_r2_M0': oos0, 'oos_r2_M1': oos1,
        'oos_gain_equity_native': oos_gain,
        'noise_band': noise_band,
    }
    if oos_gain != oos_gain:
        verdict = 'INCONCLUSIVE (insufficient data / paths for OOS folds)'
    elif oos_gain > noise_band:
        verdict = (f'EQUITY-NATIVE ADDS POWER (+{oos_gain:.4f} OOS R^2 beyond market-context, '
                   f'> noise band {noise_band})')
    elif oos_gain < -noise_band:
        verdict = (f'EQUITY-NATIVE HURTS (overfits: {oos_gain:+.4f} OOS R^2) -- '
                   f'curve signal is noise (cautionary case confirmed)')
    else:
        verdict = (f'NULL: equity-native adds nothing beyond market-context '
                   f'({oos_gain:+.4f} within +/-{noise_band} noise band)')
    result['VERDICT'] = verdict
    return result


# ---------------------------------------------------------------------------
# input adapters
# ---------------------------------------------------------------------------
def load_paths_from_json(path):
    """Expected schema (the documented MC daily-path export):
       {"paths": [{"equity": [...], "strength": [...] | null, "dates": [...] | null}, ...]}
    Also tolerates {"daily_paths": [[v,...], ...]} (equity-only) and an MC_RESULTS_JSON
    window dict carrying a per-window 'daily_paths'."""
    with open(path) as f:
        obj = json.load(f)
    out = []
    if isinstance(obj, dict) and 'paths' in obj:
        for p in obj['paths']:
            out.append((p.get('dates'), p['equity'], p.get('strength')))
        return out
    if isinstance(obj, dict) and 'daily_paths' in obj:
        for eq in obj['daily_paths']:
            out.append((None, eq, None))
        return out
    # MC_RESULTS_JSON shape: list of window dicts; pull any with daily_paths
    if isinstance(obj, list):
        for w in obj:
            for eq in (w.get('daily_paths') or []):
                out.append((None, eq, None))
        return out
    raise ValueError(f"unrecognized JSON schema in {path}")


# ---------------------------------------------------------------------------
# synthetic self-test (runnable NOW, no MC/MySQL)
# ---------------------------------------------------------------------------
def _synthetic_paths(n_paths=40, n_days=520, seed=0):
    """Build two flavors of synthetic curves to validate the analyzer detects the
    RIGHT answer in each:
      flavor A ('null'): jumps are RANDOM and INDEPENDENT of curve state/dd, but a
        latent market 'strength' DOES drive forward return -> the analyzer must find
        equity-native adds ~nothing beyond strength (NULL/HURTS), strength predictive.
      flavor B ('curve_predictive'): forward return depends on a hidden mean-reversion
        of dd (deep dd -> higher forward return), with NO market strength supplied ->
        the analyzer must find equity-native (dd) DOES add power vs constant.
    Returns (null_paths, curve_predictive_paths)."""
    rng = np.random.RandomState(seed)

    def build(curve_predictive):
        paths = []
        for _ in range(n_paths):
            eq = [50000.0]
            peak = eq[0]
            strength_series = []
            latent = 0.5
            for i in range(1, n_days):
                # latent market strength: slow AR(1) random walk in [0,1]
                latent = min(1.0, max(0.0, latent + rng.normal(0, 0.03)))
                strength_series.append(latent)
                dd = (peak - eq[-1]) / peak if peak > 0 else 0.0
                if curve_predictive:
                    # mean-reversion: deeper dd -> stronger positive drift (curve-native signal)
                    mu = 0.0008 + 0.02 * dd
                    sig = 0.02
                else:
                    # drift driven by MARKET strength, jumps random wrt curve
                    mu = 0.0010 * (latent - 0.5) * 4.0
                    sig = 0.025
                ret = rng.normal(mu, sig)
                eq.append(max(1.0, eq[-1] * (1.0 + ret)))
                peak = max(peak, eq[-1])
            strength_series = [strength_series[0]] + strength_series  # align length to eq
            paths.append((None, eq, None if curve_predictive else strength_series))
        return paths

    return build(False), build(True)


def _selftest():
    print("=" * 64)
    print("analyze.py --selftest  (synthetic curves, no MC/MySQL)")
    print("=" * 64)
    null_paths, curve_paths = _synthetic_paths()

    print("\n[A] NULL flavor: market-strength drives returns, curve does NOT")
    ra = run_ab(null_paths, H=21)
    print(f"    market-strength supplied: {ra['has_market_strength']}")
    print(f"    strength pearson(fwd_ret) = {ra['univariate_corr']['strength']['pearson_fwd_ret']:.3f}")
    print(f"    dd       pearson(fwd_ret) = {ra['univariate_corr']['dd']['pearson_fwd_ret']:.3f}")
    print(f"    OOS R^2 M0(strength)={ra['nested_models']['oos_r2_M0']:.4f}  "
          f"M1(+native)={ra['nested_models']['oos_r2_M1']:.4f}  "
          f"gain={ra['nested_models']['oos_gain_equity_native']:+.4f}")
    print(f"    VERDICT: {ra['VERDICT']}")

    print("\n[B] CURVE-PREDICTIVE flavor: deep-dd mean-reversion, no market strength")
    rb = run_ab(curve_paths, H=21)
    print(f"    market-strength supplied: {rb['has_market_strength']}")
    print(f"    dd       pearson(fwd_ret) = {rb['univariate_corr']['dd']['pearson_fwd_ret']:.3f}")
    print(f"    OOS R^2 M0(const)={rb['nested_models']['oos_r2_M0']:.4f}  "
          f"M1(+native)={rb['nested_models']['oos_r2_M1']:.4f}  "
          f"gain={rb['nested_models']['oos_gain_equity_native']:+.4f}")
    print(f"    VERDICT: {rb['VERDICT']}")

    # assertions: the analyzer must reach the correct conclusion in each construction
    fails = []
    if not (ra['nested_models']['oos_gain_equity_native'] <= ra['nested_models']['noise_band']):
        fails.append("[A] should find equity-native does NOT beat market-strength")
    if not (ra['univariate_corr']['strength']['pearson_fwd_ret'] > 0.05):
        fails.append("[A] strength should correlate with forward return")
    if not (rb['univariate_corr']['dd']['pearson_fwd_ret'] > 0.05):
        fails.append("[B] dd should correlate with forward return (constructed mean-reversion)")
    if not (rb['nested_models']['oos_gain_equity_native'] > rb['nested_models']['noise_band']):
        fails.append("[B] equity-native (dd) should add OOS power vs constant")

    print("-" * 64)
    if fails:
        for f in fails:
            print("  FAIL:", f)
        print("SELFTEST FAILED")
        return 1
    print("SELFTEST GREEN — analyzer correctly distinguishes curve-predictive from null")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Track A equity-vs-market A/B analyzer")
    ap.add_argument('--paths-json', help="JSON of per-path daily equity curves (see README)")
    ap.add_argument('--H', type=int, default=H_DEFAULT, help="forward window (trading days)")
    ap.add_argument('--out', help="write the verdict dict to this JSON")
    ap.add_argument('--selftest', action='store_true', help="run synthetic self-test (no MC/MySQL)")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    if not args.paths_json:
        ap.error("provide --paths-json or --selftest")
    paths = load_paths_from_json(args.paths_json)
    res = run_ab(paths, H=args.H)
    print(json.dumps(res, indent=2, default=str))
    if args.out:
        with open(args.out, 'w') as f:
            json.dump(res, f, indent=2, default=str)
        print(f"\nwrote {args.out}")


if __name__ == '__main__':
    main()
