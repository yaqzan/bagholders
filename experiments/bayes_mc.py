"""Bayesian adaptive MC optimizer for put-side parameter search.

Design:
- Configs evaluated at 200 iter Realistic mode on 6 windows (yearly + 22-now).
- Utility: sum of log(1+ret) across windows (22-now weighted 2x) minus DD / collapse
  penalties. See utility_from_results().
- Surrogate: kernel-weighted nearest-neighbor mean + std over normalized params.
- Acquisition: UCB with kappa=2 decaying to 1 as phase matures.
- Batch selection: top-K with diversity penalty (penalize similarity to already picked).
- Stopping: best-utility improvement < 2% for 2 consecutive batches, or budget (default
  15 configs), or max UCB <= current best mu.

All evaluations persisted to JSONL so phases can be resumed / audited remotely.
"""

from __future__ import annotations
import json
import math
import os
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Callable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import monte_carlo as mc  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

# ------- Window set (shared across phases) -----------------------------------

SWEEP_WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
]

WINDOW_WEIGHTS = {
    '2021': 1.0, '2022': 1.0, '2023': 1.0, '2024': 1.0, '2025': 1.0,
    '22-now': 2.0,  # continuous compounding is the ultimate judgment
}

# ------- Utility function ----------------------------------------------------

def utility_from_results(window_results: dict) -> dict:
    """Compute utility from per-window {mean_ret, worst_dd, p_collapse} (Realistic).

    Returns dict with 'utility', 'log_return', 'dd_penalty', 'collapse_penalty',
    'max_dd', 'max_collapse', 'per_window_log' (for inspection).
    """
    per_window_log = {}
    log_sum = 0.0
    w_sum = 0.0
    max_dd = 0.0
    max_coll = 0.0
    for label, r in window_results.items():
        w = WINDOW_WEIGHTS.get(label, 1.0)
        ret_pct = max(-99.0, float(r['mean_ret']))
        lr = math.log1p(ret_pct / 100.0)
        log_sum += w * lr
        w_sum += w
        per_window_log[label] = lr
        max_dd = max(max_dd, float(r['worst_dd']) / 100.0)
        max_coll = max(max_coll, float(r['p_collapse']) / 100.0)

    dd_excess = max(0.0, max_dd - 0.80)
    # quadratic above 0.80: 82%=0.40, 85%=2.50, 88%=6.40, 90%=10.0
    dd_penalty = 100.0 * (dd_excess ** 2) / 0.0025 * 0.25
    # simpler form: 100 * excess^2 / 0.01  (0.82 -> 0.04, 0.85 -> 2.5, 0.90 -> 10)
    dd_penalty = 100.0 * (dd_excess ** 2) / 0.01

    # collapse rate in fractional (0.01 = 1%). 1% collapse = 5 point penalty.
    collapse_penalty = 500.0 * max_coll

    utility = log_sum - dd_penalty - collapse_penalty

    return {
        'utility': utility,
        'log_return': log_sum,
        'log_return_weighted_sum': log_sum,
        'dd_penalty': dd_penalty,
        'collapse_penalty': collapse_penalty,
        'max_dd': max_dd,
        'max_collapse': max_coll,
        'per_window_log': per_window_log,
    }

# ------- Config injection into monte_carlo module ----------------------------

# Everything we can tune. Defaults mirror current monte_carlo.py production values.
DEFAULT_CONFIG = {
    # --- call side (locked during phases 1-3) ---
    'TP_BASE':            0.30,
    'TP_STRESS':          0.35,
    'SL_BASE':           -0.35,
    'SL_STRESS':         -0.40,
    'BREADTH_THRESHOLD':  50,
    'TIER_ALLOC':         {'top': 0.15, 'mid': 0.12, 'low': 0.12, 'overflow': 0.05},
    # --- put side (the variables) ---
    'PUT_TP':             0.30,
    'PUT_SL':            -0.20,
    'PUT_TP_STRESS':      0.30,
    'PUT_SL_STRESS':     -0.20,
    'PUT_BREADTH_MODE':   'none',    # 'none' | 'invert' | 'same'
    'PUT_BREADTH_THRESHOLD': 50,
    'PUT_TIER_ALLOC':     {'put_top': 0.15, 'put_mid': 0.12, 'put_low': 0.12},
    'PUT_THRESHOLD':      25,
    # --- capacity ---
    'MAX_POSITIONS':         14,
    'MAX_POSITIONS_CALL':    None,   # None = share global MAX_POSITIONS
    'MAX_POSITIONS_PUT':     None,
    # --- sweep knobs (do NOT change across a single phase run) ---
    'N_ITER':             200,
    'COLLISION_MODES':    ['realistic'],  # sweeps single-mode; final validation = all 3
}


def apply_config(cfg: dict):
    """Monkey-patch the monte_carlo module globals and recompute derived constants."""
    m = mc

    # Call-side sigma / net
    m.TP_BASE            = cfg['TP_BASE']
    m.TP_STRESS          = cfg['TP_STRESS']
    m.SL_BASE            = cfg['SL_BASE']
    m.SL_STRESS          = cfg['SL_STRESS']
    m.BREADTH_THRESHOLD  = cfg['BREADTH_THRESHOLD']
    m.NET_TP_BASE        = cfg['TP_BASE']   + m.SLIP_ENTRY + m.SLIP_TP
    m.NET_TP_STRESS      = cfg['TP_STRESS'] + m.SLIP_ENTRY + m.SLIP_TP
    m.NET_SL_BASE        = cfg['SL_BASE']   + m.SLIP_ENTRY + m.SLIP_SL
    m.NET_SL_STRESS      = cfg['SL_STRESS'] + m.SLIP_ENTRY + m.SLIP_SL
    m.TP_SIGMA_BASE      = cfg['TP_BASE']   * m.PREMIUM_MULT / m.DELTA
    m.TP_SIGMA_STRESS    = cfg['TP_STRESS'] * m.PREMIUM_MULT / m.DELTA
    m.SL_SIGMA_BASE      = abs(cfg['SL_BASE'])   * m.PREMIUM_MULT / m.DELTA
    m.SL_SIGMA_STRESS    = abs(cfg['SL_STRESS']) * m.PREMIUM_MULT / m.DELTA

    m.TIER_ALLOC = dict(cfg['TIER_ALLOC'])

    # Put side
    m.PUT_TP             = cfg['PUT_TP']
    m.PUT_SL             = cfg['PUT_SL']
    m.PUT_TP_STRESS      = cfg['PUT_TP_STRESS']
    m.PUT_SL_STRESS      = cfg['PUT_SL_STRESS']
    m.PUT_BREADTH_MODE   = cfg['PUT_BREADTH_MODE']
    m.PUT_BREADTH_THRESHOLD = cfg['PUT_BREADTH_THRESHOLD']
    m.PUT_NET_TP         = cfg['PUT_TP']  + m.SLIP_ENTRY + m.SLIP_TP
    m.PUT_NET_SL         = cfg['PUT_SL']  + m.SLIP_ENTRY + m.SLIP_SL
    m.PUT_TP_SIGMA       = cfg['PUT_TP']       * m.PREMIUM_MULT / m.DELTA
    m.PUT_SL_SIGMA       = abs(cfg['PUT_SL'])  * m.PREMIUM_MULT / m.DELTA
    m.PUT_TIER_ALLOC     = dict(cfg['PUT_TIER_ALLOC'])
    m.PUT_THRESHOLD      = cfg['PUT_THRESHOLD']

    # Capacity
    m.MAX_POSITIONS      = cfg['MAX_POSITIONS']
    m.MAX_POSITIONS_CALL = cfg['MAX_POSITIONS_CALL']
    m.MAX_POSITIONS_PUT  = cfg['MAX_POSITIONS_PUT']

    # Sweep knobs
    m.N_ITER             = cfg['N_ITER']
    m.COLLISION_MODES    = list(cfg['COLLISION_MODES'])


def run_config(cfg: dict, windows=None, version=None, silent=False):
    """Run one config across all windows. Returns {window: {mean_ret, worst_dd, p_collapse}}
    taken from the Realistic collision mode."""
    if windows is None:
        windows = SWEEP_WINDOWS
    apply_config(cfg)
    if version is None:
        version = AlgorithmVersion.get_active_scores_version()

    results = {}
    for label, d_start, d_end in windows:
        if silent:
            # Silence run_window's prints by redirecting stdout
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                window_res = mc.run_window(label, d_start, d_end, version)
        else:
            window_res = mc.run_window(label, d_start, d_end, version)

        r = window_res.get('realistic', {})
        results[label] = {
            'mean_ret':  r.get('mean_ret', 0.0),
            'med_ret':   r.get('med_ret', 0.0),
            'worst_dd':  r.get('worst_dd', 0.0),
            'mean_dd':   r.get('mean_dd', 0.0),
            'p_collapse': r.get('p_coll', 0.0),
            'call_tp':   r.get('call_tp', 0.0),
            'put_tp':    r.get('put_tp', 0.0),
            'call_trades': r.get('call_trades', 0.0),
            'put_trades':  r.get('put_trades', 0.0),
        }
    return results


# ------- Bayesian surrogate + acquisition ------------------------------------

def _normalize(cfg_flat: dict, param_space: dict) -> dict:
    """Map each parameter value to [0, 1] using its range in param_space."""
    out = {}
    for k, vals in param_space.items():
        v = cfg_flat[k]
        if isinstance(vals, (list, tuple)) and len(vals) > 1:
            # categorical or discretised
            if all(isinstance(x, (int, float)) for x in vals):
                lo, hi = min(vals), max(vals)
                out[k] = (v - lo) / (hi - lo) if hi > lo else 0.5
            else:
                # categorical — map to index / (n-1)
                idx = vals.index(v)
                out[k] = idx / (len(vals) - 1) if len(vals) > 1 else 0.5
        else:
            out[k] = 0.5
    return out


def _l1_distance(a: dict, b: dict) -> float:
    return sum(abs(a[k] - b[k]) for k in a)


def _surrogate(candidate_norm, evaluated, bandwidth=0.25):
    """Kernel-weighted nearest-neighbor mean + std."""
    if not evaluated:
        return 0.0, 1.0, 0
    weights = []
    utils = []
    for e in evaluated:
        d = _l1_distance(candidate_norm, e['norm'])
        w = math.exp(-d / bandwidth)
        weights.append(w)
        utils.append(e['utility'])
    wsum = sum(weights)
    if wsum <= 0:
        return 0.0, 1.0, 0
    mu = sum(w * u for w, u in zip(weights, utils)) / wsum
    var = sum(w * (u - mu) ** 2 for w, u in zip(weights, utils)) / wsum
    # Add bandwidth-dependent uncertainty floor (less evidence => more std)
    effective_n = wsum  # soft-count of neighbors
    std = math.sqrt(var + 1.0 / (1.0 + effective_n))
    return mu, std, effective_n


def _ucb(candidate_norm, evaluated, kappa=2.0, bandwidth=0.25):
    mu, std, _ = _surrogate(candidate_norm, evaluated, bandwidth)
    return mu + kappa * std, mu, std


# ------- Main optimizer loop -------------------------------------------------

class PhaseOptimizer:
    def __init__(self, phase_name: str, param_space: dict, base_config: dict,
                 seeds: list, budget: int = 15, batch_size: int = 3,
                 patience: int = 2, epsilon: float = 0.02, bandwidth: float = 0.25,
                 kappa_init: float = 2.0, kappa_final: float = 1.0,
                 log_dir: str | None = None, windows=None):
        self.phase_name = phase_name
        self.param_space = param_space          # {param_name: list of candidate values}
        self.base_config = dict(base_config)    # fixed config, overridden by param values
        self.seeds = seeds                      # list of dicts — sub-configs over param_space
        self.budget = budget
        self.batch_size = batch_size
        self.patience = patience
        self.epsilon = epsilon
        self.bandwidth = bandwidth
        self.kappa_init = kappa_init
        self.kappa_final = kappa_final
        self.windows = windows if windows is not None else SWEEP_WINDOWS
        self.version = None
        self.evaluated = []     # list of dicts {params, norm, utility, meta}
        self.best = None

        if log_dir is None:
            log_dir = os.path.join(ROOT, 'experiments', 'bayes_logs')
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, f'phase_{phase_name}.jsonl')

    def _full_config(self, params: dict) -> dict:
        """Merge param-set with base_config to build full monte_carlo config."""
        cfg = dict(self.base_config)
        for k, v in params.items():
            # support nested keys like 'PUT_TIER_ALLOC.put_top'
            if '.' in k:
                outer, inner = k.split('.', 1)
                cfg[outer] = dict(cfg.get(outer, {}))
                cfg[outer][inner] = v
            else:
                cfg[k] = v
        return cfg

    def _log(self, event: dict):
        event['_ts'] = time.time()
        event['_phase'] = self.phase_name
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, default=str) + '\n')

    def _params_key(self, params: dict):
        return tuple(sorted((k, repr(v)) for k, v in params.items()))

    def _already_eval(self, params: dict):
        k = self._params_key(params)
        return any(self._params_key(e['params']) == k for e in self.evaluated)

    def _enumerate_candidates(self):
        """Cartesian product of param_space values. Returns list of param dicts."""
        keys = list(self.param_space.keys())
        value_lists = [self.param_space[k] for k in keys]
        out = []
        def rec(i, acc):
            if i == len(keys):
                out.append(dict(acc))
                return
            for v in value_lists[i]:
                acc[keys[i]] = v
                rec(i + 1, acc)
        rec(0, {})
        return out

    def _pick_batch(self, candidates, kappa):
        """Pick top-K by UCB with diversity penalty."""
        # Score every candidate
        scored = []
        for cand in candidates:
            if self._already_eval(cand):
                continue
            norm = _normalize(cand, self.param_space)
            ucb, mu, std = _ucb(norm, self.evaluated, kappa=kappa, bandwidth=self.bandwidth)
            scored.append({'params': cand, 'norm': norm, 'ucb': ucb, 'mu': mu, 'std': std})

        scored.sort(key=lambda x: x['ucb'], reverse=True)
        batch = []
        for s in scored:
            if len(batch) >= self.batch_size:
                break
            # diversity penalty: if too close to one already picked, skip
            too_close = False
            for b in batch:
                d = _l1_distance(s['norm'], b['norm'])
                if d < 0.10:   # 10% of total param ranges
                    too_close = True
                    break
            if not too_close:
                batch.append(s)
        # If nothing passed diversity, fall back to top picks
        if not batch and scored:
            batch = scored[: self.batch_size]
        return batch

    def _evaluate(self, params: dict):
        full_cfg = self._full_config(params)
        t0 = time.time()
        win_res = run_config(full_cfg, windows=self.windows, version=self.version, silent=True)
        dur = time.time() - t0
        util_info = utility_from_results(win_res)
        norm = _normalize(params, self.param_space)
        rec = {
            'params':   params,
            'norm':     norm,
            'utility':  util_info['utility'],
            'util_info': util_info,
            'windows':  win_res,
            'duration_s': dur,
        }
        self.evaluated.append(rec)
        self._log({
            'event':   'eval',
            'params':  params,
            'utility': util_info['utility'],
            'log_return': util_info['log_return'],
            'dd_penalty': util_info['dd_penalty'],
            'collapse_penalty': util_info['collapse_penalty'],
            'max_dd':   util_info['max_dd'],
            'max_collapse': util_info['max_collapse'],
            'windows':  {k: {kk: vv for kk, vv in v.items() if kk in (
                'mean_ret', 'worst_dd', 'p_collapse', 'call_tp', 'put_tp',
                'call_trades', 'put_trades', 'mean_dd', 'med_ret'
            )} for k, v in win_res.items()},
            'duration_s': dur,
        })
        return rec

    def _report_best(self):
        if not self.evaluated:
            return None
        self.best = max(self.evaluated, key=lambda r: r['utility'])
        return self.best

    def run(self):
        self.version = AlgorithmVersion.get_active_scores_version()
        self._log({
            'event': 'start',
            'param_space': {k: v for k, v in self.param_space.items()},
            'base_config': {k: v for k, v in self.base_config.items() if k != 'COLLISION_MODES'},
            'budget':  self.budget,
            'batch_size': self.batch_size,
            'version': str(self.version.git_commit) if self.version else None,
        })

        # 1) seed evaluation
        for s in self.seeds:
            if self._already_eval(s):
                continue
            self._evaluate(s)
            print(f'[seed]  params={s}  utility={self.evaluated[-1]["utility"]:+.3f}  '
                  f'dur={self.evaluated[-1]["duration_s"]:.1f}s')

        self._report_best()
        print(f'\n[seed-phase complete]  best utility={self.best["utility"]:+.3f}  '
              f'params={self.best["params"]}\n')

        # 2) adaptive loop
        candidates = self._enumerate_candidates()
        no_imp = 0
        it = 0
        while len(self.evaluated) < self.budget:
            it += 1
            # kappa decays linearly with iteration
            progress = min(1.0, len(self.evaluated) / self.budget)
            kappa = self.kappa_init + (self.kappa_final - self.kappa_init) * progress
            batch = self._pick_batch(candidates, kappa)
            if not batch:
                print(f'[iter {it}]  no candidates left to evaluate — stopping.')
                break

            prev_best_u = self.best['utility'] if self.best else float('-inf')
            # stop if max UCB is already below our current best mu
            top_ucb = batch[0]['ucb']
            if top_ucb < prev_best_u - 0.01:
                print(f'[iter {it}]  max UCB ({top_ucb:+.3f}) < current best mu ({prev_best_u:+.3f}) — stopping.')
                break

            self._log({'event': 'batch_start', 'iter': it, 'kappa': kappa,
                       'candidates': [b['params'] for b in batch],
                       'ucbs': [b['ucb'] for b in batch]})
            print(f'[iter {it}  kappa={kappa:.2f}]  picking batch of {len(batch)}:')
            for b in batch:
                print(f'    ucb={b["ucb"]:+.3f}  mu={b["mu"]:+.3f}  std={b["std"]:.3f}  params={b["params"]}')

            for b in batch:
                self._evaluate(b['params'])
                print(f'    [eval] params={b["params"]}  utility={self.evaluated[-1]["utility"]:+.3f}  '
                      f'ret-log={self.evaluated[-1]["util_info"]["log_return"]:+.2f}  '
                      f'maxDD={self.evaluated[-1]["util_info"]["max_dd"]*100:.1f}%  '
                      f'dur={self.evaluated[-1]["duration_s"]:.0f}s')

            new_best = self._report_best()
            gain = new_best['utility'] - prev_best_u
            rel = abs(gain) / (abs(prev_best_u) + 1e-6) if prev_best_u != float('-inf') else 1.0
            print(f'[iter {it} done]  best utility={new_best["utility"]:+.3f}  '
                  f'(gain={gain:+.3f}, rel={rel*100:.2f}%)  params={new_best["params"]}\n')
            self._log({'event': 'batch_done', 'iter': it,
                       'best_utility': new_best['utility'],
                       'best_params':  new_best['params'],
                       'gain':         gain, 'rel_gain': rel})

            if rel < self.epsilon:
                no_imp += 1
                if no_imp >= self.patience:
                    print(f'[converged]  no best-utility improvement > {self.epsilon*100:.1f}% '
                          f'for {self.patience} batches.')
                    self._log({'event': 'converged', 'iter': it})
                    break
            else:
                no_imp = 0

        self._log({'event': 'end', 'best': self.best['params'] if self.best else None,
                   'best_utility': self.best['utility'] if self.best else None,
                   'n_evaluated': len(self.evaluated)})

        return self.best, self.evaluated


# ------- Pretty final report --------------------------------------------------

def print_summary(phase_name, best, evaluated):
    print('\n' + '=' * 96)
    print(f'PHASE {phase_name} SUMMARY — {len(evaluated)} configs evaluated')
    print('=' * 96)
    ranked = sorted(evaluated, key=lambda r: r['utility'], reverse=True)
    print(f'{"rk":>2}  {"utility":>8}  {"logret":>6}  {"maxDD%":>6}  {"col%":>5}  params')
    for i, r in enumerate(ranked[:10]):
        u = r['util_info']
        print(f'{i+1:>2}  {r["utility"]:>+8.3f}  {u["log_return"]:>+6.2f}  '
              f'{u["max_dd"]*100:>6.1f}  {u["max_collapse"]*100:>5.2f}  {r["params"]}')

    print('\nBest config per-window breakdown:')
    print(f'{"window":<10}  {"MeanRet%":>12}  {"WorstDD%":>8}  {"Col%":>5}  {"CallTP":>6}  {"PutTP":>6}  {"CallTr":>7}  {"PutTr":>6}')
    print('-' * 96)
    for label in [w[0] for w in SWEEP_WINDOWS]:
        w = best['windows'].get(label, {})
        print(f'{label:<10}  {w.get("mean_ret",0):>+12.1f}  {w.get("worst_dd",0):>8.1f}  '
              f'{w.get("p_collapse",0):>5.2f}  {w.get("call_tp",0):>6.1f}  {w.get("put_tp",0):>6.1f}  '
              f'{w.get("call_trades",0):>7.1f}  {w.get("put_trades",0):>6.1f}')
