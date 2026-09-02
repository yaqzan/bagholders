"""GPU-vectorized Monte Carlo for canonical 3-mode validation.

The canonical MC is dominated by two loops:
  outer: N iterations (typically 200-1000)
  inner: trading days (typically 250 per year)

Within each iteration, the sequential portfolio update is hard to parallelize
(cash-balance dependency). But ACROSS iterations, the only randomness is
same-bar TP+SL collision resolution (50/50 coin flip per collision per iter).

Strategy:
  1. Pre-compute deterministic per-signal exit data on CPU/cache:
     - tp_bar (when target hit, or -1)
     - sl_bar (when stop hit, or -1)
     - hard_sell_bar (typically day 15)
     - is_collision (tp_bar == sl_bar — both trigger same intra-bar)
  2. For N iterations × T trades, sample collision outcomes -> (N, T) tensor
  3. Use deterministic exit data + collision sample to determine actual exit
  4. Compute per-iteration portfolio trajectory via vectorized cumulative scan
  5. Aggregate metrics per iteration (mean/median/min return, max DD)

Requires PyTorch CUDA. Falls back to CPU if unavailable.

This is a *framework* that the existing canonical 3-mode MC can plug into
once Phase 4 of Priority #13 begins. Not yet wired into monte_carlo.py.
"""
from __future__ import annotations
import torch
import time
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def select_device():
    if torch.cuda.is_available():
        return torch.device('cuda')
    return torch.device('cpu')


def deterministic_exit_bars(closes_2d, highs_2d, lows_2d, entry_idx_arr, side_arr,
                             tp_sigma, sl_sigma, sigma_arr, hard_sell_bar):
    """Vectorized barrier-touch exit detection.

    closes_2d / highs_2d / lows_2d: (T, B) tensors — T trades, B forward bars per trade
    entry_idx_arr: (T,) — index into the bar arrays where each trade enters (0 = entry bar)
    side_arr: (T,) — 0 for put (win on drop), 1 for call (win on rise)
    tp_sigma / sl_sigma: scalar barrier multipliers
    sigma_arr: (T,) — realized vol % per trade
    hard_sell_bar: scalar — bar at which to force exit if neither target hit

    Returns:
        tp_bar: (T,) — bar index when target hit (-1 if never)
        sl_bar: (T,) — bar index when stop hit (-1 if never)
        hard_bar: (T,) — hard sell bar (always = hard_sell_bar unless < tp_bar/sl_bar)
        is_collision: (T,) bool — both tp_bar and sl_bar equal and >= 0
    """
    device = closes_2d.device
    T, B = closes_2d.shape
    entry_close = closes_2d.gather(1, entry_idx_arr.unsqueeze(1)).squeeze(1)  # (T,)

    # Build target / stop levels per trade
    side_mask = side_arr.float()  # 0=put, 1=call
    move_pct = sigma_arr / 100.0
    # Put: target = entry * (1 - K*sigma); stop = entry * (1 + M*sigma)
    # Call: target = entry * (1 + K*sigma); stop = entry * (1 - M*sigma)
    sign_target = (2 * side_mask - 1)  # -1 for put, +1 for call
    target = entry_close * (1 + sign_target * tp_sigma * move_pct)
    stop   = entry_close * (1 - sign_target * sl_sigma * move_pct)

    # For each bar, check if target/stop hit
    # Put: target hit when low <= target, stop hit when high >= stop
    # Call: target hit when high >= target, stop hit when low <= stop
    big = torch.full((T, B), 1e9, device=device)
    bar_idx = torch.arange(B, device=device).unsqueeze(0).expand(T, B)

    # Mask: only consider bars AFTER entry_idx
    after_entry = bar_idx > entry_idx_arr.unsqueeze(1)

    # Put-side detection
    put_target_hit = (lows_2d <= target.unsqueeze(1)) & after_entry & (side_mask.unsqueeze(1) == 0)
    put_stop_hit   = (highs_2d >= stop.unsqueeze(1))  & after_entry & (side_mask.unsqueeze(1) == 0)
    # Call-side detection
    call_target_hit = (highs_2d >= target.unsqueeze(1)) & after_entry & (side_mask.unsqueeze(1) == 1)
    call_stop_hit   = (lows_2d  <= stop.unsqueeze(1))  & after_entry & (side_mask.unsqueeze(1) == 1)

    target_hit = put_target_hit | call_target_hit
    stop_hit   = put_stop_hit   | call_stop_hit

    # First-hit bar per trade (-1 if never hit)
    first_target_bar = torch.where(target_hit, bar_idx.float(), big).min(dim=1).values
    first_stop_bar   = torch.where(stop_hit,   bar_idx.float(), big).min(dim=1).values

    tp_bar   = torch.where(first_target_bar < 1e8, first_target_bar.long(), torch.full_like(first_target_bar.long(), -1))
    sl_bar   = torch.where(first_stop_bar   < 1e8, first_stop_bar.long(),   torch.full_like(first_stop_bar.long(),   -1))

    is_collision = (tp_bar == sl_bar) & (tp_bar >= 0)
    return tp_bar, sl_bar, is_collision


def resolve_exit_with_collisions(tp_bar, sl_bar, is_collision, hard_sell_bar, n_iter, mode='realistic', seed=42):
    """Determine actual exit bar per (iter, trade) given collision realization.

    Args:
        tp_bar, sl_bar: (T,) tensors — first-hit bars (-1 if never)
        is_collision: (T,) bool tensor — same-bar TP+SL collision
        hard_sell_bar: scalar
        n_iter: number of MC iterations
        mode: 'conservative' (collisions = SL), 'realistic' (50/50), 'optimistic' (TP)
    Returns:
        exit_outcome: (n_iter, T) int tensor — 1=TP, 0=SL, 2=hard_sell, -1=expire
        exit_bar: (n_iter, T) int tensor
    """
    device = tp_bar.device
    T = tp_bar.shape[0]

    # Determine the FIRST exit bar (without collision resolution)
    # If tp_bar == sl_bar (collision), need MC randomness
    # If tp_bar < sl_bar OR sl_bar == -1: TP wins
    # If sl_bar < tp_bar OR tp_bar == -1: SL wins
    # If both -1: expire / hard sell at min(hard_sell_bar)

    no_tp = tp_bar < 0
    no_sl = sl_bar < 0
    both_none = no_tp & no_sl

    # tp_first: tp_bar < sl_bar (or sl is none and tp is not)
    tp_first = (~no_tp) & (no_sl | (tp_bar < sl_bar) & ~is_collision)
    sl_first = (~no_sl) & (no_tp | (sl_bar < tp_bar) & ~is_collision)

    # Sample collision outcomes per iteration
    if mode == 'conservative':
        # Collisions all go to SL
        collision_tp_realized = torch.zeros((n_iter, T), dtype=torch.bool, device=device)
    elif mode == 'optimistic':
        collision_tp_realized = torch.ones((n_iter, T), dtype=torch.bool, device=device)
    else:  # realistic
        gen = torch.Generator(device=device).manual_seed(seed)
        flips = torch.rand((n_iter, T), generator=gen, device=device) < 0.5
        collision_tp_realized = flips

    # Build per-iteration outcome
    # Default: expire/hard sell
    outcome = torch.full((n_iter, T), 2, dtype=torch.int8, device=device)  # 2=hard_sell
    exit_bar = torch.full((n_iter, T), hard_sell_bar, dtype=torch.long, device=device)

    # tp_first: TP wins for all iterations (no randomness needed)
    tp_first_b = tp_first.unsqueeze(0).expand(n_iter, T)
    outcome = torch.where(tp_first_b, torch.tensor(1, dtype=torch.int8, device=device), outcome)
    exit_bar = torch.where(tp_first_b, tp_bar.unsqueeze(0).expand(n_iter, T), exit_bar)

    # sl_first: SL wins
    sl_first_b = sl_first.unsqueeze(0).expand(n_iter, T)
    outcome = torch.where(sl_first_b, torch.tensor(0, dtype=torch.int8, device=device), outcome)
    exit_bar = torch.where(sl_first_b, sl_bar.unsqueeze(0).expand(n_iter, T), exit_bar)

    # collision: per-iter random
    coll_b = is_collision.unsqueeze(0).expand(n_iter, T)
    coll_tp = coll_b & collision_tp_realized
    coll_sl = coll_b & ~collision_tp_realized
    outcome = torch.where(coll_tp, torch.tensor(1, dtype=torch.int8, device=device), outcome)
    outcome = torch.where(coll_sl, torch.tensor(0, dtype=torch.int8, device=device), outcome)
    exit_bar = torch.where(coll_tp | coll_sl, tp_bar.unsqueeze(0).expand(n_iter, T), exit_bar)

    # both none: leave as hard_sell (already default)
    return outcome, exit_bar


def benchmark():
    """Quick speed test of the GPU MC framework."""
    device = select_device()
    print(f"Device: {device}")

    # Synthetic: 5000 trades, 100 forward bars each, 1000 iterations
    T = 5000
    B = 100
    N_ITER = 1000

    torch.manual_seed(42)
    closes = 100.0 * torch.cumprod(1 + torch.randn(T, B, device=device) * 0.01, dim=1)
    highs = closes * (1.0 + torch.abs(torch.randn(T, B, device=device)) * 0.01)
    lows = closes * (1.0 - torch.abs(torch.randn(T, B, device=device)) * 0.01)
    entry_idx = torch.zeros(T, dtype=torch.long, device=device)
    side = torch.randint(0, 2, (T,), device=device)
    sigmas = torch.empty(T, device=device).uniform_(1.0, 5.0)

    # warmup
    deterministic_exit_bars(closes, highs, lows, entry_idx, side, 2.0, 5.0, sigmas, 15)
    torch.cuda.synchronize() if device.type == 'cuda' else None

    t0 = time.time()
    tp_bar, sl_bar, is_coll = deterministic_exit_bars(closes, highs, lows, entry_idx, side,
                                                       2.0, 5.0, sigmas, 15)
    torch.cuda.synchronize() if device.type == 'cuda' else None
    print(f"Deterministic exit detection ({T} trades, {B} bars): {(time.time()-t0)*1000:.1f}ms")
    print(f"  Collisions: {is_coll.sum().item()} / {T} ({is_coll.sum().item()/T*100:.1f}%)")

    t0 = time.time()
    outcome, exit_bar = resolve_exit_with_collisions(tp_bar, sl_bar, is_coll, 15, N_ITER, 'realistic')
    torch.cuda.synchronize() if device.type == 'cuda' else None
    print(f"Collision resolution ({N_ITER} iterations × {T} trades): {(time.time()-t0)*1000:.1f}ms")
    print(f"  TP rate (mean across iter): {(outcome == 1).float().mean().item()*100:.1f}%")
    print(f"  SL rate: {(outcome == 0).float().mean().item()*100:.1f}%")
    print(f"  Hard sell rate: {(outcome == 2).float().mean().item()*100:.1f}%")


if __name__ == '__main__':
    benchmark()
