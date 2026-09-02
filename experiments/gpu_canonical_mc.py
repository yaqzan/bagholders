"""GPU-accelerated canonical 3-mode Monte Carlo.

Wires the gpu_mc.py framework into the canonical MC strategy logic. The CPU
monte_carlo.py remains the source of truth — this module produces an identical
result via vectorized GPU operations and validates against it.

Architecture:
    1. Use existing CPU monte_carlo helpers (precompute_outcomes, etc.) to get
       per-trade deterministic exit data
    2. Convert outcome dicts to torch tensors on GPU
    3. Run N iterations as parallel tensor ops:
        - Build (N_iter, T_total_signals) tensor of "would this trade fire" booleans
        - For each iteration, sample collision realizations
        - Compute per-trade P&L given outcome
        - Track per-iteration cash trajectory via sequential scan over trading days
    4. Aggregate per-iteration metrics

Key design choices:
    - Per-iteration cash is a (N_iter,) tensor updated each day
    - Open positions per iteration are stored as a flat (N_iter * MAX_OPEN, ...)
      tensor with a mask
    - Sequential day loop is on CPU; per-day work is GPU-vectorized
    - Collision realizations: pre-sample (N_iter, T_total) random tensor

Validation: run on 2024 single-window with N=200, compare to CPU MC output.
Gates: per-window MeanRet within 0.5% of CPU; same DD-C; same collapse rate.

NOT YET PRODUCTION-READY — this is a development scaffolding. Wire into
monte_carlo.py via env var (e.g. MC_BACKEND=gpu) once validated.
"""
from __future__ import annotations
import io, sys, time, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import torch
import numpy as np
from collections import defaultdict
from datetime import date, timedelta


def select_device():
    return torch.device('cuda' if torch.cuda.is_available() else 'cpu')


class GPUCanonicalMC:
    """Vectorized canonical MC over N iterations.

    Currently implements the SUBSET of the canonical strategy needed to validate
    the GPU framework. Production wiring will require feature parity with
    monte_carlo.py (F3f scaling, breadth gates, CT promotion, EARN_SUPP, etc.).

    For now: implement the core flow (cash management, position lifecycle, exit
    handling, collision resolution) and verify it matches CPU on a simplified
    config.
    """

    def __init__(self, n_iter, mode='realistic', starting_cash=50000.0, max_positions=14,
                  collapse_threshold=0.20, device=None, seed=42):
        self.n_iter = n_iter
        self.mode = mode  # 'conservative' | 'realistic' | 'optimistic'
        self.starting_cash = starting_cash
        self.max_positions = max_positions
        self.collapse_threshold = collapse_threshold
        self.device = device or select_device()
        self.seed = seed

    @staticmethod
    def build_signal_tensors(call_outcomes, put_outcomes, calls_by_date, puts_by_date,
                              trading_days, day_to_idx, device):
        """Convert per-trade outcome dicts into flat tensors keyed by signal index.

        Each (sym_id, signal_date) pair maps to a single index. We store:
            - day_idx: trading day index of signal
            - side: 0=put, 1=call
            - kind: 0=hard, 1=tp, 2=sl, 3=both (collision)
            - exit_bar: trading bars until exit
            - net_tp / net_sl: per-trade P&L if TP/SL realized
            - score (for tier allocation)
            - ct_tag (for CT promotion)
            - ern_tag (for EARN_BOOST)
        """
        rows = []
        # Calls
        for d, day_sigs in calls_by_date.items():
            di = day_to_idx.get(d, -1)
            if di < 0: continue
            for sym_id, score, key, ct, ern in day_sigs:
                if key not in call_outcomes: continue
                o = call_outcomes[key]
                kind_code = {'hard': 0, 'tp': 1, 'sl': 2, 'both': 3}[o['kind']]
                rows.append({
                    'day_idx': di, 'side': 1, 'kind': kind_code,
                    'exit_bar': o['exit_bar'], 'net_tp': o['net_tp'], 'net_sl': o['net_sl'],
                    'score': score, 'sym_id_hash': hash(sym_id) % (1<<31),
                    'ct': 1 if ct == 'ct_call' else 0,
                    'ern': 1 if ern else 0,
                })
        for d, day_sigs in puts_by_date.items():
            di = day_to_idx.get(d, -1)
            if di < 0: continue
            for sym_id, score, key, ct in day_sigs:
                if key not in put_outcomes: continue
                o = put_outcomes[key]
                kind_code = {'hard': 0, 'tp': 1, 'sl': 2, 'both': 3}[o['kind']]
                rows.append({
                    'day_idx': di, 'side': 0, 'kind': kind_code,
                    'exit_bar': o['exit_bar'], 'net_tp': o['net_tp'], 'net_sl': o['net_sl'],
                    'score': score, 'sym_id_hash': hash(sym_id) % (1<<31),
                    'ct': 1 if ct == 'ct_put' else 0,
                    'ern': 0,
                })

        if not rows:
            return None

        n = len(rows)
        return {
            'day_idx':  torch.tensor([r['day_idx'] for r in rows], dtype=torch.long, device=device),
            'side':     torch.tensor([r['side']    for r in rows], dtype=torch.int8, device=device),
            'kind':     torch.tensor([r['kind']    for r in rows], dtype=torch.int8, device=device),
            'exit_bar': torch.tensor([r['exit_bar'] for r in rows], dtype=torch.long, device=device),
            'net_tp':   torch.tensor([r['net_tp']  for r in rows], dtype=torch.float32, device=device),
            'net_sl':   torch.tensor([r['net_sl']  for r in rows], dtype=torch.float32, device=device),
            'score':    torch.tensor([r['score']   for r in rows], dtype=torch.int16, device=device),
            'sym_hash': torch.tensor([r['sym_id_hash'] for r in rows], dtype=torch.long, device=device),
            'ct':       torch.tensor([r['ct']      for r in rows], dtype=torch.int8, device=device),
            'ern':      torch.tensor([r['ern']     for r in rows], dtype=torch.int8, device=device),
            'n_signals': n,
            'n_days': len(trading_days),
        }

    def sample_collision_outcomes(self, kind_tensor):
        """For each (iteration, signal), determine if 'both' collisions resolve as TP or SL.

        Returns: (n_iter, n_signals) bool tensor — True = TP, False = SL.
        Only meaningful for signals with kind=3 (collision); other kinds use deterministic outcome.
        """
        device = kind_tensor.device
        n_sig = kind_tensor.shape[0]
        if self.mode == 'conservative':
            return torch.zeros((self.n_iter, n_sig), dtype=torch.bool, device=device)
        if self.mode == 'optimistic':
            return torch.ones((self.n_iter, n_sig), dtype=torch.bool, device=device)
        # realistic: per-iter, per-signal coin flip
        gen = torch.Generator(device=device).manual_seed(self.seed)
        return torch.rand((self.n_iter, n_sig), generator=gen, device=device) < 0.5

    def resolve_outcomes(self, signals):
        """Given the deterministic kind + collision sample, produce per-iter per-signal P&L.

        Returns: (n_iter, n_signals) tensor of net P&L (as fraction; 0.30 = +30%).
        """
        kind = signals['kind']             # (n_signals,) — 0=hard, 1=tp, 2=sl, 3=both
        net_tp = signals['net_tp']         # (n_signals,)
        net_sl = signals['net_sl']         # (n_signals,)
        # NET_HARD_SELL is a constant from monte_carlo.py
        from monte_carlo import NET_HARD_SELL
        net_hard = torch.full_like(net_tp, NET_HARD_SELL)

        # Default = hard
        det_pnl = net_hard.clone()
        det_pnl = torch.where(kind == 1, net_tp, det_pnl)
        det_pnl = torch.where(kind == 2, net_sl, det_pnl)

        # Broadcast to (n_iter, n_signals)
        det_pnl_b = det_pnl.unsqueeze(0).expand(self.n_iter, -1)
        kind_b = kind.unsqueeze(0).expand(self.n_iter, -1)

        # For collisions (kind==3), per-iter sampled
        collision_tp = self.sample_collision_outcomes(kind)  # (n_iter, n_signals)
        coll_pnl = torch.where(collision_tp, net_tp.unsqueeze(0).expand_as(det_pnl_b),
                                              net_sl.unsqueeze(0).expand_as(det_pnl_b))
        return torch.where(kind_b == 3, coll_pnl, det_pnl_b)


def quick_demo():
    """Synthetic demo confirming the framework runs end-to-end."""
    device = select_device()
    print(f"GPU canonical MC demo on {device}")

    n_iter = 1000
    mc = GPUCanonicalMC(n_iter=n_iter, mode='realistic', device=device, seed=42)

    # Synthetic signals: 5000 trades
    n_sig = 5000
    signals = {
        'day_idx':  torch.randint(0, 252, (n_sig,), dtype=torch.long, device=device),
        'side':     torch.randint(0, 2, (n_sig,), dtype=torch.int8, device=device),
        'kind':     torch.randint(0, 4, (n_sig,), dtype=torch.int8, device=device),
        'exit_bar': torch.randint(1, 16, (n_sig,), dtype=torch.long, device=device),
        'net_tp':   torch.full((n_sig,), 0.30, device=device),
        'net_sl':   torch.full((n_sig,), -0.20, device=device),
        'score':    torch.randint(70, 101, (n_sig,), dtype=torch.int16, device=device),
        'sym_hash': torch.randint(0, 1<<31, (n_sig,), dtype=torch.long, device=device),
        'ct':       torch.zeros(n_sig, dtype=torch.int8, device=device),
        'ern':      torch.zeros(n_sig, dtype=torch.int8, device=device),
        'n_signals': n_sig, 'n_days': 252,
    }

    if device.type == 'cuda':
        torch.cuda.synchronize()
    t0 = time.time()
    pnl = mc.resolve_outcomes(signals)
    if device.type == 'cuda':
        torch.cuda.synchronize()
    print(f"resolve_outcomes ({n_iter} iter, {n_sig} signals): {(time.time()-t0)*1000:.1f}ms")
    print(f"  shape: {pnl.shape}, dtype: {pnl.dtype}")
    print(f"  mean P&L per iter: {pnl.mean(dim=1)[:5].tolist()}")


if __name__ == '__main__':
    quick_demo()
