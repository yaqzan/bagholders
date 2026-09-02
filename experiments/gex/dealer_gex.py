"""
dealer_gex.py -- Dealer GEX (gamma exposure) feature builder.

Implements experiments/gex/MATH_SPEC.md EXACTLY. See MATH_SPEC.md for the full
derivation, grid choice, degenerate-case matrix, and locked self-test reference
values. This module makes zero math decisions of its own -- every constant and
rule below is pinned in the spec.

Usage:
    python experiments/gex/dealer_gex.py --selftest
    python experiments/gex/dealer_gex.py --input <chain.parquet> --output <features.parquet> [--workers N]

READ-ONLY offline experiment code. Never touches MySQL or production scoring code.
"""

import argparse
import multiprocessing as mp
import sys
import time

import numpy as np
import polars as pl

# ---------------------------------------------------------------------------
# Fixed constants (MATH_SPEC.md top-of-file block, verbatim)
# ---------------------------------------------------------------------------
R_RATE = 0.04          # risk-free (DESIGN.md D2)
Q_YIELD = 0.0          # dividend yield (DESIGN.md D2)
DAYS_PER_YR = 365.0    # calendar-day convention (DESIGN.md D2: T = dte/365)
MULT = 100.0           # contract multiplier
PCT_MOVE = 0.01        # 1% underlying move (dollar-gamma-per-1% units, DESIGN.md D2)
DTE_MIN = 1            # feature window lower bound (DESIGN.md D3)
DTE_MAX = 90           # feature window upper bound (DESIGN.md D3)
IV_FLOOR = 0.01        # cache filters iv in (0.01, 5.0); clip defensively at boundary
IV_CEIL = 5.0
T_FLOOR = 1.0 / 365.0  # 1 calendar day; guards div-by-zero when dte would be < 1
GRID_LO_MULT = 0.70    # spot grid lower bound = 0.70 * spot
GRID_HI_MULT = 1.30    # spot grid upper bound = 1.30 * spot
GRID_N = 301           # grid point count
MIN_STRIKES_FOR_WALL = 1  # walls need >=1 same-side strike; else NaN
INV_SQRT_2PI = 0.3989422804014327  # 1/sqrt(2*pi), for the hand-rolled pdf

# MATH_SPEC_CLUSTERS.md top-of-file constant block (gamma cluster segmentation)
GAP_MASS_FRAC = 0.01      # a strike is an ACTIVE node iff its per-strike mass >= GAP_MASS_FRAC * total_mass
MIN_CLUSTER_FRAC = 0.05   # a candidate run is a CLUSTER iff its summed mass_share >= MIN_CLUSTER_FRAC
SPACING_GAP = 0.05        # split between consecutive active strikes when ln(K_hi/K_lo) > SPACING_GAP
LOCAL_PCT = 0.02          # +/-2% of spot band for local_density


# ---------------------------------------------------------------------------
# Section 1: Black-Scholes gamma
# ---------------------------------------------------------------------------
def norm_pdf(x):
    """Hand-rolled standard-normal pdf, vectorized. MATH_SPEC.md Section 1.3."""
    return INV_SQRT_2PI * np.exp(-0.5 * x * x)


def gamma_shape_a(S, K, T, sigma):
    """
    Shape A: all contracts of ONE (symbol, date) at a SINGLE spot S (scalar).
    K, T, sigma: 1-D numpy arrays of length C. Returns gamma (C,).
    MATH_SPEC.md Section 1.4, Shape A.
    """
    with np.errstate(under='ignore', invalid='ignore', divide='ignore'):
        sqrtT = np.sqrt(T)
        d1 = (np.log(S / K) + (R_RATE - Q_YIELD + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
        gamma = np.exp(-Q_YIELD * T) * norm_pdf(d1) / (S * sigma * sqrtT)
    gamma = np.nan_to_num(gamma, nan=0.0, posinf=0.0, neginf=0.0)
    return gamma


def gamma_shape_b(grid, K, T, sigma):
    """
    Shape B: all contracts re-evaluated over the SPOT GRID.
    grid: (n_grid,) numpy array. K, T, sigma: (C,) numpy arrays.
    Returns gamma (n_grid, C). MATH_SPEC.md Section 1.4, Shape B.
    """
    Sg = grid[:, None]                    # (n_grid, 1)
    Kb = K[None, :]                       # (1, C)
    Tb = T[None, :]
    sb = sigma[None, :]
    with np.errstate(under='ignore', invalid='ignore', divide='ignore'):
        sqrtTb = np.sqrt(Tb)
        d1 = (np.log(Sg / Kb) + (R_RATE - Q_YIELD + 0.5 * sb * sb) * Tb) / (sb * sqrtTb)
        gamma = np.exp(-Q_YIELD * Tb) * norm_pdf(d1) / (Sg * sb * sqrtTb)
    gamma = np.nan_to_num(gamma, nan=0.0, posinf=0.0, neginf=0.0)
    return gamma


def apply_edge_handling(dte, iv):
    """
    MATH_SPEC.md Section 1.2, steps 1-2 (T floor, iv clip). Applied BEFORE
    computing d1. Returns (T, sigma) arrays.
    """
    dte_floored = np.maximum(dte, 1)
    T = dte_floored.astype(np.float64) / DAYS_PER_YR
    sigma = np.clip(iv, IV_FLOOR, IV_CEIL)
    return T, sigma


# ---------------------------------------------------------------------------
# Section 2/3: net GEX profile, spot grid, levels
# ---------------------------------------------------------------------------
def build_spot_grid(spot):
    """MATH_SPEC.md Section 2.3."""
    return np.linspace(GRID_LO_MULT * spot, GRID_HI_MULT * spot, GRID_N)


def compute_netgex_profile(grid, K, T, sigma, OI, sign_vec):
    """
    MATH_SPEC.md Section 2.2. Returns (netgex_profile (n_grid,), per_contract_gex (n_grid, C)).
    """
    gamma_b = gamma_shape_b(grid, K, T, sigma)                     # (n_grid, C)
    Sg = grid[:, None]                                             # (n_grid, 1)
    per_contract_gex = gamma_b * (OI[None, :] * MULT) * (Sg ** 2) * PCT_MOVE * sign_vec[None, :]
    netgex_profile = per_contract_gex.sum(axis=1)                  # (n_grid,)
    return netgex_profile, per_contract_gex


def find_flip(grid, netgex_profile, spot):
    """
    MATH_SPEC.md Section 3.1. Zero-crossing of netgex_profile over the grid,
    linear interpolation, nearest-to-spot tie-break among multiple crossings.
    Returns flip (float, NaN if no crossing).
    """
    sign_arr = np.sign(netgex_profile)             # (n_grid,); np.sign(0.0) == 0.0
    diffsign = np.diff(sign_arr)
    cross = np.where(diffsign != 0)[0]              # candidate crossing indices

    if cross.size == 0:
        return np.nan

    candidates = []
    for c in cross:
        x0, x1 = grid[c], grid[c + 1]
        y0, y1 = netgex_profile[c], netgex_profile[c + 1]
        if y1 == y0:
            # guard divide-by-zero; skip this candidate (spec Section 3.1)
            continue
        flip_c = x0 - y0 * (x1 - x0) / (y1 - y0)
        candidates.append(flip_c)

    if len(candidates) == 0:
        return np.nan

    candidates = np.array(candidates)
    dists = np.abs(candidates - spot)
    min_dist = dists.min()
    # tie-break: take the lower flip among those at (essentially) min distance
    tied = candidates[dists == min_dist]
    return float(tied.min())


def _side_wall(strike, mask, gex_abs, spot):
    """Helper: argmax per-strike summed gex_abs for one side. NaN if mask empty."""
    if not np.any(mask):
        return np.nan
    s = strike[mask]
    g = gex_abs[mask]
    # group by strike: sum g within identical strike values
    uniq_strikes, inverse = np.unique(s, return_inverse=True)
    sums = np.zeros(uniq_strikes.shape[0], dtype=np.float64)
    np.add.at(sums, inverse, g)
    max_val = sums.max()
    tied_strikes = uniq_strikes[sums == max_val]
    if tied_strikes.size == 1:
        return float(tied_strikes[0])
    # tie-break: nearest to spot, then lower strike
    dists = np.abs(tied_strikes - spot)
    min_dist = dists.min()
    nearest = tied_strikes[dists == min_dist]
    return float(nearest.min())


def compute_walls_at_spot(strike, option_type, spot, K, T, sigma, OI, sign_vec):
    """
    MATH_SPEC.md Section 3.2, Shape A at S=spot. Returns
    (call_wall, put_wall, gamma_spot, gex_spot_abs, gex_spot_signed).
    """
    gamma_spot = gamma_shape_a(spot, K, T, sigma)                   # (C,)
    gex_spot_abs = gamma_spot * (OI * MULT) * (spot ** 2) * PCT_MOVE   # (C,), UNSIGNED
    gex_spot_signed = gex_spot_abs * sign_vec                         # (C,), SIGNED

    call_mask = (option_type == 'call')
    put_mask = (option_type == 'put')

    call_wall = _side_wall(strike, call_mask, gex_spot_abs, spot)
    put_wall = _side_wall(strike, put_mask, gex_spot_abs, spot)

    return call_wall, put_wall, gamma_spot, gex_spot_abs, gex_spot_signed


def compute_cotm(strike, option_type, OI):
    """MATH_SPEC.md Section 3.3. OI-weighted mean strike, per side."""
    call_mask = (option_type == 'call')
    put_mask = (option_type == 'put')

    if np.any(call_mask) and OI[call_mask].sum() > 0:
        cotmc = float((strike[call_mask] * OI[call_mask]).sum() / OI[call_mask].sum())
    else:
        cotmc = np.nan

    if np.any(put_mask) and OI[put_mask].sum() > 0:
        cotmp = float((strike[put_mask] * OI[put_mask]).sum() / OI[put_mask].sum())
    else:
        cotmp = np.nan

    return cotmc, cotmp


def compute_netgex_slope(grid, netgex_profile, spot):
    """
    MATH_SPEC.md Section 4.7. Centered finite difference at the grid node
    nearest spot (with 301 pts, spot is node 150 exactly).
    """
    n_grid = grid.shape[0]
    h = grid[1] - grid[0]
    i0 = int(np.argmin(np.abs(grid - spot)))

    if 0 < i0 < n_grid - 1:
        slope = (netgex_profile[i0 + 1] - netgex_profile[i0 - 1]) / (2 * h)
    elif i0 == 0:
        slope = (netgex_profile[1] - netgex_profile[0]) / h
    else:
        slope = (netgex_profile[-1] - netgex_profile[-2]) / h
    return float(slope)


# ---------------------------------------------------------------------------
# MATH_SPEC_CLUSTERS.md Section 1.2/2/3: gamma cluster segmentation
# ---------------------------------------------------------------------------
def build_per_strike_profile(K, gex_spot_abs, gex_spot_signed, OI):
    """
    MATH_SPEC_CLUSTERS.md Section 1.2. Aggregate gex_spot_abs / gex_spot_signed / OI
    by strike across ALL contracts (calls AND puts pooled). Reuses the
    np.unique(return_inverse=True) + np.add.at pattern from _side_wall, but combines
    both sides into one strike-indexed profile.

    Returns (uniq, mass, smass, oistk):
        uniq  (S,) DISTINCT strikes, STRICTLY ASCENDING
        mass  (S,) per-strike UNSIGNED dollar-gamma mass (segmentation input)
        smass (S,) per-strike SIGNED dollar-gamma (diagnostic only)
        oistk (S,) per-strike OI (diagnostic only)
    """
    uniq, inv = np.unique(K, return_inverse=True)
    mass = np.zeros(uniq.shape[0], dtype=np.float64)
    np.add.at(mass, inv, gex_spot_abs)
    smass = np.zeros(uniq.shape[0], dtype=np.float64)
    np.add.at(smass, inv, gex_spot_signed)
    oistk = np.zeros(uniq.shape[0], dtype=np.float64)
    np.add.at(oistk, inv, OI)
    return uniq, mass, smass, oistk


def segment_clusters(uniq, mass):
    """
    MATH_SPEC_CLUSTERS.md Section 2.1-2.4. Segment the per-strike UNSIGNED mass
    profile into an ordered list of clusters (ascending by lo_idx).

    Returns (clusters, total):
        clusters: list of dicts {lo_idx, hi_idx, lo_strike, hi_strike, center, mass, mass_share},
                  sorted ascending by lo_idx (== lo_strike == hi_strike == center ordering).
        total: float, total mass across the whole profile (0.0 if uniq is empty).
    """
    total = float(mass.sum()) if uniq.shape[0] > 0 else 0.0

    if uniq.shape[0] == 0 or total <= 0.0:
        return [], total

    # --- Section 2.1: active nodes (the mass floor) ---
    low_thresh = GAP_MASS_FRAC * total
    active_idx = np.where(mass >= low_thresh)[0]

    if active_idx.shape[0] == 0:
        return [], total

    # --- Section 2.2: split active nodes into candidate runs (index-gap OR spacing-gap) ---
    runs = []
    run = [int(active_idx[0])]
    for i in range(1, active_idx.shape[0]):
        prev = int(active_idx[i - 1])
        cur = int(active_idx[i])
        contiguous_index = (cur == prev + 1)
        spacing = float(np.log(uniq[cur] / uniq[prev]))
        boundary = (not contiguous_index) or (spacing > SPACING_GAP)
        if boundary:
            runs.append(run)
            run = [cur]
        else:
            run.append(cur)
    runs.append(run)

    # --- Section 2.3: finalize each run (mass, share, center, edges) ---
    finalized_runs = []
    for r in runs:
        lo = r[0]
        hi = r[-1]
        w = mass[lo:hi + 1]
        k = uniq[lo:hi + 1]
        run_mass = float(w.sum())
        mass_share = run_mass / total
        center = float((w * k).sum() / w.sum())
        lo_strike = float(uniq[lo])
        hi_strike = float(uniq[hi])
        finalized_runs.append({
            'lo_idx': lo, 'hi_idx': hi,
            'lo_strike': lo_strike, 'hi_strike': hi_strike,
            'center': center, 'mass': run_mass, 'mass_share': mass_share,
        })

    # --- Section 2.4: the cluster mass-share floor (which runs survive) ---
    clusters = [c for c in finalized_runs if c['mass_share'] >= MIN_CLUSTER_FRAC]

    return clusters, total


def cluster_features(clusters, uniq, mass, total, spot):
    """
    MATH_SPEC_CLUSTERS.md Section 3.1-3.4. Compute the eight new feature columns
    (plus optional dom_center/dom_lo_strike/dom_hi_strike diagnostics) from the
    surviving clusters list, the per-strike profile, and spot.

    Returns a dict with keys:
        esc_above, gap_room_up, local_density, esc_below, gap_below,
        n_clusters, dominant_cluster_mass_share, cluster_span_pct,
        dom_center, dom_lo_strike, dom_hi_strike (optional diagnostics)
    """
    n_clusters = int(len(clusters))

    # --- Section 3.4 row 1: empty window / total_mass <= 0 ---
    if total <= 0.0:
        return {
            'esc_above': np.nan, 'gap_room_up': np.nan, 'local_density': np.nan,
            'esc_below': np.nan, 'gap_below': np.nan,
            'n_clusters': 0,
            'dominant_cluster_mass_share': np.nan, 'cluster_span_pct': np.nan,
            'dom_center': np.nan, 'dom_lo_strike': np.nan, 'dom_hi_strike': np.nan,
        }

    # --- local_density: computed from mass/total directly, independent of clusters ---
    band = (uniq >= spot * (1.0 - LOCAL_PCT)) & (uniq <= spot * (1.0 + LOCAL_PCT))
    local_density = float(mass[band].sum() / total)

    # --- Section 3.4 row 2: no run clears the 5% floor -> n_clusters==0 ---
    if n_clusters == 0:
        return {
            'esc_above': np.nan, 'gap_room_up': np.nan, 'local_density': local_density,
            'esc_below': np.nan, 'gap_below': np.nan,
            'n_clusters': 0,
            'dominant_cluster_mass_share': np.nan, 'cluster_span_pct': np.nan,
            'dom_center': np.nan, 'dom_lo_strike': np.nan, 'dom_hi_strike': np.nan,
        }

    # --- Section 2.5: the DOMINANT cluster (max mass; tie-break smaller center) ---
    max_mass = max(c['mass'] for c in clusters)
    tied = [c for c in clusters if c['mass'] == max_mass]
    if len(tied) == 1:
        dominant = tied[0]
    else:
        dominant = min(tied, key=lambda c: c['center'])

    # --- Section 3.1: esc_above ---
    esc_above = 1.0 if spot > dominant['hi_strike'] else 0.0

    # --- Section 3.2: esc_below ---
    esc_below = 1.0 if spot < dominant['lo_strike'] else 0.0

    # --- Section 3.1: gap_room_up ---
    above = [c for c in clusters if c['lo_strike'] > spot]
    if above:
        nca = min(above, key=lambda c: c['center'])
        gap_room_up = float(np.log(nca['center'] / spot))
    else:
        gap_room_up = np.nan

    # --- Section 3.2: gap_below ---
    below = [c for c in clusters if c['hi_strike'] < spot]
    if below:
        ncb = max(below, key=lambda c: c['center'])
        gap_below = float(np.log(spot / ncb['center']))
    else:
        gap_below = np.nan

    # --- Section 3.3: diagnostics ---
    dominant_cluster_mass_share = float(dominant['mass_share'])
    cluster_span_pct = float((dominant['hi_strike'] - dominant['lo_strike']) / spot)

    return {
        'esc_above': esc_above, 'gap_room_up': gap_room_up, 'local_density': local_density,
        'esc_below': esc_below, 'gap_below': gap_below,
        'n_clusters': n_clusters,
        'dominant_cluster_mass_share': dominant_cluster_mass_share,
        'cluster_span_pct': cluster_span_pct,
        'dom_center': dominant['center'], 'dom_lo_strike': dominant['lo_strike'],
        'dom_hi_strike': dominant['hi_strike'],
    }


# ---------------------------------------------------------------------------
# Section 4: per-group feature computation
# ---------------------------------------------------------------------------
def _nan_row(symbol, date, n_contracts=0, n_calls=0, n_puts=0, total_oi=0,
             n_strikes=0, pct_oi_in_window=np.nan, n_dropped=0, gex_regime=0):
    """Full-NaN degenerate row (empty feature window). MATH_SPEC.md Section 3.4."""
    return {
        'symbol': symbol, 'date': date,
        'gex_regime': gex_regime, 'gex_ratio': np.nan, 'flip_dist': np.nan, 'callwall_dist': np.nan,
        'putwall_dist': np.nan, 'cotmp_dist': np.nan, 'netgex_slope': np.nan, 'log10_total_gex': np.nan,
        'spot': np.nan, 'flip': np.nan, 'call_wall': np.nan, 'put_wall': np.nan,
        'cotmc': np.nan, 'cotmp': np.nan, 'netgex_at_spot': np.nan,
        'n_contracts': n_contracts, 'n_calls': n_calls, 'n_puts': n_puts, 'total_oi': total_oi,
        'n_strikes': n_strikes, 'pct_oi_in_window': pct_oi_in_window, 'n_dropped': n_dropped,
        'cotmc_dist': np.nan,
        # MATH_SPEC_CLUSTERS.md Section 3.4 row 1: empty window -> n_clusters=0, rest NaN.
        'esc_above': np.nan, 'gap_room_up': np.nan, 'local_density': np.nan,
        'esc_below': np.nan, 'gap_below': np.nan, 'n_clusters': 0,
        'dominant_cluster_mass_share': np.nan, 'cluster_span_pct': np.nan,
        'dom_center': np.nan, 'dom_lo_strike': np.nan, 'dom_hi_strike': np.nan,
    }


def features_for_group(symbol, date, strike, option_type, dte, open_interest, iv, spot_arr,
                        full_group_total_oi):
    """
    Compute the Section 4.9 output row for one (symbol, date) group.

    Inputs are the FEATURE-WINDOW-FILTERED (dte in [DTE_MIN, DTE_MAX]) numpy arrays
    for this group, plus full_group_total_oi = the ORIGINAL (pre-filter, full
    dte 1-180) group's total OI, for pct_oi_in_window (Section 4.9 note).

    MATH_SPEC.md Section 4.0 preprocessing (steps 1-4) + Sections 3-4.
    """
    n_raw = strike.shape[0]

    if n_raw == 0:
        # Empty feature-window chain. MATH_SPEC.md Section 3.4 row 1.
        # Numerator (feature-window OI) is undefined-by-emptiness -> NaN regardless
        # of the denominator (locked by MATH_SPEC.md Section 5 test A4, line 451:
        # a chain with dte=120 rows in the ORIGINAL non-empty group still asserts
        # pct_oi_in_window NaN once the 1-90 window is empty).
        return _nan_row(symbol, date, n_contracts=0, n_calls=0, n_puts=0, total_oi=0,
                         n_strikes=0, pct_oi_in_window=np.nan, n_dropped=0, gex_regime=0)

    # --- Section 4.0.2: defensive row hygiene ---
    valid = (
        (open_interest > 0)
        & np.isfinite(iv)
        & (strike > 0)
        & (spot_arr > 0)
        & ((option_type == 'call') | (option_type == 'put'))
    )
    n_dropped = int((~valid).sum())

    strike = strike[valid]
    option_type = option_type[valid]
    dte = dte[valid]
    open_interest = open_interest[valid]
    iv = iv[valid]
    spot_arr = spot_arr[valid]

    if strike.shape[0] == 0:
        # Everything dropped by hygiene -- effectively empty (same NaN rule as above).
        return _nan_row(symbol, date, n_contracts=0, n_calls=0, n_puts=0, total_oi=0,
                         n_strikes=0, pct_oi_in_window=np.nan, n_dropped=n_dropped, gex_regime=0)

    # --- Section 4.0.3: spot (identical across the group by cache contract) ---
    spot = float(spot_arr[0])

    n_contracts = strike.shape[0]
    n_calls = int((option_type == 'call').sum())
    n_puts = int((option_type == 'put').sum())
    total_oi = int(open_interest.sum())
    n_strikes = int(np.unique(strike).shape[0])
    pct_oi_in_window = (
        np.nan if full_group_total_oi == 0 else float(total_oi) / float(full_group_total_oi)
    )

    # --- Section 4.0.4: sign_vec, T floor, iv clip, grid, both gamma shapes ---
    sign_vec = np.where(option_type == 'call', 1.0, -1.0)
    T, sigma = apply_edge_handling(dte, iv)
    K = strike.astype(np.float64)
    OI = open_interest.astype(np.float64)

    grid = build_spot_grid(spot)
    netgex_profile, _ = compute_netgex_profile(grid, K, T, sigma, OI, sign_vec)

    # --- Section 3.1: flip ---
    flip = find_flip(grid, netgex_profile, spot)

    # --- Section 3.2 + 4.1/4.2: walls, at-spot signed/abs GEX ---
    call_wall, put_wall, gamma_spot, gex_spot_abs, gex_spot_signed = compute_walls_at_spot(
        strike, option_type, spot, K, T, sigma, OI, sign_vec
    )

    # --- Section 3.3: COTMC/COTMP ---
    cotmc, cotmp = compute_cotm(strike, option_type, OI)

    # --- Section 4.1: gex_regime, netgex_at_spot (Shape A exact, DECISION) ---
    netgex_at_spot = float(gex_spot_signed.sum())
    if netgex_at_spot > 0:
        gex_regime = 1
    elif netgex_at_spot < 0:
        gex_regime = -1
    else:
        gex_regime = 0

    # --- Section 4.2: gex_ratio ---
    denominator = float(np.abs(gex_spot_signed).sum())
    gex_ratio = (netgex_at_spot / denominator) if denominator > 0 else np.nan

    # --- Section 4.3: flip_dist ---
    flip_dist = np.log(spot / flip) if np.isfinite(flip) else np.nan

    # --- Section 4.4: callwall_dist ---
    callwall_dist = np.log(call_wall / spot) if np.isfinite(call_wall) else np.nan

    # --- Section 4.5: putwall_dist ---
    putwall_dist = np.log(put_wall / spot) if np.isfinite(put_wall) else np.nan

    # --- Section 4.6: cotmp_dist, cotmc_dist (diagnostic) ---
    cotmp_dist = np.log(cotmp / spot) if np.isfinite(cotmp) else np.nan
    cotmc_dist = np.log(cotmc / spot) if np.isfinite(cotmc) else np.nan

    # --- Section 4.7: netgex_slope ---
    netgex_slope = compute_netgex_slope(grid, netgex_profile, spot)

    # --- Section 4.8: log10_total_gex ---
    log10_total_gex = np.log10(denominator) if denominator > 0 else np.nan

    # --- MATH_SPEC_CLUSTERS.md Section 3.0: gamma cluster segmentation ---
    # Reuses gex_spot_abs/gex_spot_signed exactly as compute_walls_at_spot produced them
    # (never recomputes gamma). Called AFTER the walls/COTM/regime block above.
    uniq, cl_mass, cl_smass, cl_oistk = build_per_strike_profile(K, gex_spot_abs, gex_spot_signed, OI)
    clusters, cl_total = segment_clusters(uniq, cl_mass)
    cluster_feat = cluster_features(clusters, uniq, cl_mass, cl_total, spot)

    out = {
        'symbol': symbol, 'date': date,
        'gex_regime': int(gex_regime), 'gex_ratio': gex_ratio, 'flip_dist': flip_dist,
        'callwall_dist': callwall_dist,
        'putwall_dist': putwall_dist, 'cotmp_dist': cotmp_dist, 'netgex_slope': netgex_slope,
        'log10_total_gex': log10_total_gex,
        'spot': spot, 'flip': flip, 'call_wall': call_wall, 'put_wall': put_wall,
        'cotmc': cotmc, 'cotmp': cotmp, 'netgex_at_spot': netgex_at_spot,
        'n_contracts': n_contracts, 'n_calls': n_calls, 'n_puts': n_puts, 'total_oi': total_oi,
        'n_strikes': n_strikes, 'pct_oi_in_window': pct_oi_in_window, 'n_dropped': n_dropped,
        'cotmc_dist': cotmc_dist,
    }
    out.update(cluster_feat)
    return out


def _process_one_group(symbol, date, g_full: pl.DataFrame):
    """
    Take the FULL (pre-DTE-filter) group (dte 1-180 by cache contract) and
    apply Section 4.0 step 1 (DTE window filter) before computing features.
    Returns the Section 4.9 output dict.
    """
    full_group_total_oi = int(g_full['open_interest'].sum())

    g = g_full.filter((pl.col('dte') >= DTE_MIN) & (pl.col('dte') <= DTE_MAX))

    strike = g['strike'].to_numpy()
    option_type = g['option_type'].to_numpy()
    dte = g['dte'].to_numpy()
    open_interest = g['open_interest'].to_numpy()
    iv = g['iv'].to_numpy()
    spot_arr = g['spot'].to_numpy()

    return features_for_group(symbol, date, strike, option_type, dte, open_interest, iv,
                               spot_arr, full_group_total_oi)


# ---------------------------------------------------------------------------
# Multiprocessing driver
# ---------------------------------------------------------------------------
def _worker_process_symbol(args):
    """
    Worker entry point: process ALL (symbol,date) groups for ONE symbol.
    Chunking by symbol keeps memory bounded (one symbol's chain in memory
    at a time per worker) while avoiding per-group IPC overhead.
    """
    symbol, path = args
    # Each worker reads only its symbol's rows via a predicate pushdown scan,
    # so peak memory per worker is one symbol's chain, not the whole input.
    df_sym = pl.scan_parquet(path).filter(pl.col('symbol') == symbol).collect()

    rows = []
    for (sym, dt), g_full in df_sym.group_by(['symbol', 'date'], maintain_order=True):
        rows.append(_process_one_group(sym, dt, g_full))
    return rows


def compute_features(input_path: str, n_workers: int) -> pl.DataFrame:
    """
    Stream (symbol,date) groups through the pipeline, chunked by symbol across
    worker processes, collecting feature rows and concatenating once at the end
    (bounded memory per Requirement 4).
    """
    symbols = (
        pl.scan_parquet(input_path)
        .select('symbol')
        .unique()
        .collect()['symbol']
        .to_list()
    )
    symbols = sorted(symbols)
    print("ASCII: %d symbols to process, %d workers" % (len(symbols), n_workers))

    tasks = [(sym, input_path) for sym in symbols]

    out_rows = []
    n_signals_done = 0
    t_start = time.time()
    progress_every = 100

    if n_workers <= 1:
        for task in tasks:
            rows = _worker_process_symbol(task)
            out_rows.extend(rows)
            n_signals_done += len(rows)
            if n_signals_done // progress_every > (n_signals_done - len(rows)) // progress_every:
                elapsed = time.time() - t_start
                print("progress: %d signals done, %.1fs elapsed" % (n_signals_done, elapsed))
    else:
        with mp.Pool(processes=n_workers) as pool:
            for rows in pool.imap_unordered(_worker_process_symbol, tasks):
                out_rows.extend(rows)
                n_signals_done += len(rows)
                if n_signals_done % progress_every < len(rows):
                    elapsed = time.time() - t_start
                    print("progress: %d signals done, %.1fs elapsed" % (n_signals_done, elapsed))

    elapsed = time.time() - t_start
    print("done: %d signals in %.1fs" % (n_signals_done, elapsed))

    out_df = pl.DataFrame(out_rows)
    # Explicit dtype casts per Section 4.10 reference pseudocode.
    out_df = out_df.with_columns([
        pl.col('gex_regime').cast(pl.Int64),
        pl.col('n_contracts').cast(pl.Int64),
        pl.col('n_calls').cast(pl.Int64),
        pl.col('n_puts').cast(pl.Int64),
        pl.col('total_oi').cast(pl.Int64),
        pl.col('n_strikes').cast(pl.Int64),
        pl.col('n_dropped').cast(pl.Int64),
        pl.col('n_clusters').cast(pl.Int64),
    ])
    return out_df


# ---------------------------------------------------------------------------
# Section 5: self-tests
# ---------------------------------------------------------------------------
def _selftest_a1():
    """A1. BS gamma against a hand-computed known value."""
    passed = True
    # ATM point
    S, K, T, iv = 100.0, 100.0, 30.0 / 365.0, 0.40
    T_arr, sigma_arr = apply_edge_handling(np.array([30]), np.array([iv]))
    gamma = gamma_shape_a(S, np.array([K]), T_arr, sigma_arr)[0]
    expected = 0.03466008190858278
    ok = abs(gamma - expected) < 1e-9
    print("A1a (ATM gamma): computed=%.17g expected=%.17g -> %s" % (gamma, expected, "PASS" if ok else "FAIL"))
    passed &= ok

    # OTM point
    S, K, T, iv = 100.0, 110.0, 45.0 / 365.0, 0.30
    T_arr, sigma_arr = apply_edge_handling(np.array([45]), np.array([iv]))
    gamma2 = gamma_shape_a(S, np.array([K]), T_arr, sigma_arr)[0]
    expected2 = 0.0273840637
    ok2 = abs(gamma2 - expected2) < 1e-9
    print("A1b (OTM gamma): computed=%.17g expected=%.17g -> %s" % (gamma2, expected2, "PASS" if ok2 else "FAIL"))
    passed &= ok2

    return passed


def _selftest_a2():
    """A2. put/call gamma equality + norm_pdf cross-check against scipy."""
    passed = True
    S, K, T, iv = 100.0, 105.0, 20.0 / 365.0, 0.55
    T_arr, sigma_arr = apply_edge_handling(np.array([20]), np.array([iv]))
    gamma_call = gamma_shape_a(S, np.array([K]), T_arr, sigma_arr)[0]
    gamma_put = gamma_shape_a(S, np.array([K]), T_arr, sigma_arr)[0]  # gamma has no call/put branch
    ok = (gamma_call == gamma_put)
    print("A2a (call==put gamma exact): call=%.17g put=%.17g -> %s" % (gamma_call, gamma_put, "PASS" if ok else "FAIL"))
    passed &= ok

    import scipy.stats
    d1 = (np.log(S / K) + (R_RATE - Q_YIELD + 0.5 * sigma_arr[0] ** 2) * T_arr[0]) / (sigma_arr[0] * np.sqrt(T_arr[0]))
    my_pdf = norm_pdf(np.array([d1]))[0]
    scipy_pdf = scipy.stats.norm.pdf(d1)
    ok2 = abs(my_pdf - scipy_pdf) < 1e-12
    print("A2b (norm_pdf vs scipy): mine=%.17g scipy=%.17g diff=%.3e -> %s" % (
        my_pdf, scipy_pdf, abs(my_pdf - scipy_pdf), "PASS" if ok2 else "FAIL"))
    passed &= ok2

    return passed


def _make_synthetic_group(symbol, date, spot, strikes, option_types, dtes, ois, ivs):
    """Build the raw numpy arrays for a synthetic in-memory chain (no parquet)."""
    n = len(strikes)
    strike = np.array(strikes, dtype=np.float64)
    option_type = np.array(option_types, dtype=object)
    dte = np.array(dtes, dtype=np.int64)
    open_interest = np.array(ois, dtype=np.int64)
    iv = np.array(ivs, dtype=np.float64)
    spot_arr = np.full(n, spot, dtype=np.float64)
    return symbol, date, strike, option_type, dte, open_interest, iv, spot_arr


def _selftest_a3():
    """A3. synthetic chain with an analytically-known flip."""
    passed = True
    symbol, date, strike, option_type, dte, open_interest, iv, spot_arr = _make_synthetic_group(
        'SYN', '2026-01-01', 100.0,
        strikes=[110.0, 90.0], option_types=['call', 'put'], dtes=[30, 30],
        ois=[1000, 1000], ivs=[0.40, 0.40],
    )
    full_group_total_oi = int(open_interest.sum())
    row = features_for_group(symbol, date, strike, option_type, dte, open_interest, iv, spot_arr,
                              full_group_total_oi)

    grid_step = 0.60 * 100.0 / (GRID_N - 1)
    tol = grid_step + 1e-6
    expected_flip = 98.522210
    ok_flip = abs(row['flip'] - expected_flip) <= tol
    print("A3a (flip): computed=%.6f expected=%.6f tol=%.6f -> %s" % (
        row['flip'], expected_flip, tol, "PASS" if ok_flip else "FAIL"))
    passed &= ok_flip

    ok_regime = (row['gex_regime'] == 1)
    print("A3b (gex_regime==+1): computed=%s -> %s" % (row['gex_regime'], "PASS" if ok_regime else "FAIL"))
    passed &= ok_regime

    ok_netgex = (row['netgex_at_spot'] > 0) and abs(row['netgex_at_spot'] - 53561.77) < 5.0
    print("A3c (netgex_at_spot~+53561.77): computed=%.2f -> %s" % (
        row['netgex_at_spot'], "PASS" if ok_netgex else "FAIL"))
    passed &= ok_netgex

    expected_flip_dist = np.log(100.0 / 98.52221)
    ok_flip_dist = abs(row['flip_dist'] - expected_flip_dist) < 1e-4
    print("A3d (flip_dist~+0.01488818): computed=%.8f expected=%.8f -> %s" % (
        row['flip_dist'], expected_flip_dist, "PASS" if ok_flip_dist else "FAIL"))
    passed &= ok_flip_dist

    return passed


def _selftest_a4():
    """A4. degenerate chains return the spec'd NaN pattern without raising."""
    passed = True

    # --- Empty window: only rows have dte=120 (outside 1-90) ---
    try:
        symbol, date, strike, option_type, dte, open_interest, iv, spot_arr = _make_synthetic_group(
            'SYN', '2026-01-01', 100.0,
            strikes=[100.0], option_types=['call'], dtes=[120],
            ois=[1000], ivs=[0.40],
        )
        # Simulate the module's DTE-window filter (Section 4.0 step 1) as the
        # driver would do via polars; here directly filter the numpy arrays.
        mask = (dte >= DTE_MIN) & (dte <= DTE_MAX)
        full_group_total_oi = int(open_interest.sum())
        row = features_for_group(symbol, date, strike[mask], option_type[mask], dte[mask],
                                  open_interest[mask], iv[mask], spot_arr[mask], full_group_total_oi)
        checks = [
            ('gex_regime==0', row['gex_regime'] == 0),
            ('flip NaN', not np.isfinite(row['flip'])),
            ('call_wall NaN', not np.isfinite(row['call_wall'])),
            ('put_wall NaN', not np.isfinite(row['put_wall'])),
            ('cotmc NaN', not np.isfinite(row['cotmc'])),
            ('cotmp NaN', not np.isfinite(row['cotmp'])),
            ('gex_ratio NaN', not np.isfinite(row['gex_ratio'])),
            ('flip_dist NaN', not np.isfinite(row['flip_dist'])),
            ('callwall_dist NaN', not np.isfinite(row['callwall_dist'])),
            ('putwall_dist NaN', not np.isfinite(row['putwall_dist'])),
            ('cotmp_dist NaN', not np.isfinite(row['cotmp_dist'])),
            ('netgex_slope NaN', not np.isfinite(row['netgex_slope'])),
            ('log10_total_gex NaN', not np.isfinite(row['log10_total_gex'])),
            ('n_contracts==0', row['n_contracts'] == 0),
            ('pct_oi_in_window NaN', not np.isfinite(row['pct_oi_in_window'])),
        ]
        ok = all(c[1] for c in checks)
        failed_names = [c[0] for c in checks if not c[1]]
        print("A4a (empty window): %s%s" % ("PASS" if ok else "FAIL", (" failed=" + str(failed_names)) if failed_names else ""))
        passed &= ok
    except Exception as e:
        print("A4a (empty window): FAIL (raised %r)" % (e,))
        passed = False

    # --- No puts: two CALLS (K=95, K=105), dte=30 ---
    try:
        symbol, date, strike, option_type, dte, open_interest, iv, spot_arr = _make_synthetic_group(
            'SYN', '2026-01-01', 100.0,
            strikes=[95.0, 105.0], option_types=['call', 'call'], dtes=[30, 30],
            ois=[1000, 1000], ivs=[0.40, 0.40],
        )
        full_group_total_oi = int(open_interest.sum())
        row = features_for_group(symbol, date, strike, option_type, dte, open_interest, iv, spot_arr,
                                  full_group_total_oi)
        checks = [
            ('put_wall NaN', not np.isfinite(row['put_wall'])),
            ('cotmp NaN', not np.isfinite(row['cotmp'])),
            ('putwall_dist NaN', not np.isfinite(row['putwall_dist'])),
            ('cotmp_dist NaN', not np.isfinite(row['cotmp_dist'])),
            ('flip NaN', not np.isfinite(row['flip'])),
            ('flip_dist NaN', not np.isfinite(row['flip_dist'])),
            ('call_wall computed', np.isfinite(row['call_wall'])),
            ('cotmc computed', np.isfinite(row['cotmc'])),
            ('gex_regime==+1', row['gex_regime'] == 1),
            ('gex_ratio==+1.0', abs(row['gex_ratio'] - 1.0) < 1e-12),
        ]
        ok = all(c[1] for c in checks)
        failed_names = [c[0] for c in checks if not c[1]]
        print("A4b (no puts): %s%s" % ("PASS" if ok else "FAIL", (" failed=" + str(failed_names)) if failed_names else ""))
        passed &= ok
    except Exception as e:
        print("A4b (no puts): FAIL (raised %r)" % (e,))
        passed = False

    # --- No calls: mirror (two puts K=95,105) ---
    try:
        symbol, date, strike, option_type, dte, open_interest, iv, spot_arr = _make_synthetic_group(
            'SYN', '2026-01-01', 100.0,
            strikes=[95.0, 105.0], option_types=['put', 'put'], dtes=[30, 30],
            ois=[1000, 1000], ivs=[0.40, 0.40],
        )
        full_group_total_oi = int(open_interest.sum())
        row = features_for_group(symbol, date, strike, option_type, dte, open_interest, iv, spot_arr,
                                  full_group_total_oi)
        checks = [
            ('call_wall NaN', not np.isfinite(row['call_wall'])),
            ('cotmc NaN', not np.isfinite(row['cotmc'])),
            ('callwall_dist NaN', not np.isfinite(row['callwall_dist'])),
            ('gex_regime==-1', row['gex_regime'] == -1),
            ('gex_ratio==-1.0', abs(row['gex_ratio'] - (-1.0)) < 1e-12),
        ]
        ok = all(c[1] for c in checks)
        failed_names = [c[0] for c in checks if not c[1]]
        print("A4c (no calls): %s%s" % ("PASS" if ok else "FAIL", (" failed=" + str(failed_names)) if failed_names else ""))
        passed &= ok
    except Exception as e:
        print("A4c (no calls): FAIL (raised %r)" % (e,))
        passed = False

    # --- <5 strikes: call@100 + put@100, equal OI ---
    try:
        symbol, date, strike, option_type, dte, open_interest, iv, spot_arr = _make_synthetic_group(
            'SYN', '2026-01-01', 100.0,
            strikes=[100.0, 100.0], option_types=['call', 'put'], dtes=[30, 30],
            ois=[1000, 1000], ivs=[0.40, 0.40],
        )
        full_group_total_oi = int(open_interest.sum())
        row = features_for_group(symbol, date, strike, option_type, dte, open_interest, iv, spot_arr,
                                  full_group_total_oi)
        checks = [
            ('call_wall==100', row['call_wall'] == 100.0),
            ('put_wall==100', row['put_wall'] == 100.0),
            ('cotmc==100', row['cotmc'] == 100.0),
            ('cotmp==100', row['cotmp'] == 100.0),
            ('gex_regime==0', row['gex_regime'] == 0),
            ('flip NaN', not np.isfinite(row['flip'])),
            ('gex_ratio==0.0', abs(row['gex_ratio'] - 0.0) < 1e-12),
        ]
        ok = all(c[1] for c in checks)
        failed_names = [c[0] for c in checks if not c[1]]
        print("A4d (<5 strikes, same-strike call+put): %s%s" % (
            "PASS" if ok else "FAIL", (" failed=" + str(failed_names)) if failed_names else ""))
        passed &= ok
    except Exception as e:
        print("A4d (<5 strikes): FAIL (raised %r)" % (e,))
        passed = False

    return passed


def _selftest_a5():
    """A5. monotonicity sanity -- all-call chain."""
    passed = True
    symbol, date, strike, option_type, dte, open_interest, iv, spot_arr = _make_synthetic_group(
        'SYN', '2026-01-01', 100.0,
        strikes=[95.0, 100.0, 105.0], option_types=['call', 'call', 'call'], dtes=[30, 30, 30],
        ois=[1000, 1000, 1000], ivs=[0.40, 0.40, 0.40],
    )
    full_group_total_oi = int(open_interest.sum())

    # Need the profile itself for the all-positive check -- recompute inline.
    T, sigma = apply_edge_handling(dte, iv)
    K = strike.astype(np.float64)
    OI = open_interest.astype(np.float64)
    sign_vec = np.where(option_type == 'call', 1.0, -1.0)
    grid = build_spot_grid(100.0)
    netgex_profile, _ = compute_netgex_profile(grid, K, T, sigma, OI, sign_vec)

    ok_profile = bool(np.all(netgex_profile > 0))
    print("A5a (profile > 0 everywhere): %s" % ("PASS" if ok_profile else "FAIL"))
    passed &= ok_profile

    row = features_for_group(symbol, date, strike, option_type, dte, open_interest, iv, spot_arr,
                              full_group_total_oi)

    ok_flip = not np.isfinite(row['flip'])
    print("A5b (flip NaN): %s" % ("PASS" if ok_flip else "FAIL"))
    passed &= ok_flip

    ok_regime = (row['gex_regime'] == 1)
    print("A5c (gex_regime==+1): %s" % ("PASS" if ok_regime else "FAIL"))
    passed &= ok_regime

    ok_ratio = abs(row['gex_ratio'] - 1.0) < 1e-12
    print("A5d (gex_ratio==+1.0): computed=%.17g -> %s" % (row['gex_ratio'], "PASS" if ok_ratio else "FAIL"))
    passed &= ok_ratio

    ok_wall = (row['call_wall'] == 100.0)
    print("A5e (call_wall==100.0): computed=%s -> %s" % (row['call_wall'], "PASS" if ok_wall else "FAIL"))
    passed &= ok_wall

    return passed


# ---------------------------------------------------------------------------
# MATH_SPEC_CLUSTERS.md Section 4: cluster segmentation self-tests (C1-C4)
# ---------------------------------------------------------------------------
def _three_cluster_chain_profile(spot):
    """
    MATH_SPEC_CLUSTERS.md Section 4.0: the locked 3-cluster synthetic chain,
    RE-EVALUATED at the given spot (gamma depends on S -- never reuse a profile
    across spots). Returns (uniq, mass, total).
    """
    symbol, date, strike, option_type, dte, open_interest, iv, spot_arr = _make_synthetic_group(
        'SYN3C', '2026-01-01', spot,
        strikes=[88, 90, 92, 98, 100, 102, 108, 110, 112],
        option_types=['put', 'put', 'put', 'call', 'call', 'call', 'call', 'call', 'call'],
        dtes=[30] * 9,
        ois=[4000, 6000, 4000, 5000, 9000, 5000, 4000, 6000, 4000],
        ivs=[0.40] * 9,
    )
    sign_vec = np.where(option_type == 'call', 1.0, -1.0)
    T, sigma = apply_edge_handling(dte, iv)
    K = strike.astype(np.float64)
    OI = open_interest.astype(np.float64)
    call_wall, put_wall, gamma_spot, gex_spot_abs, gex_spot_signed = compute_walls_at_spot(
        strike, option_type, spot, K, T, sigma, OI, sign_vec
    )
    uniq, mass, smass, oistk = build_per_strike_profile(K, gex_spot_abs, gex_spot_signed, OI)
    total = float(mass.sum())
    return uniq, mass, total


def _selftest_c1():
    """C1. segmentation of the 3-cluster chain at spot = 100.0."""
    passed = True
    uniq, mass, total = _three_cluster_chain_profile(100.0)

    expected_masses = {
        88.0: 676735.5218514064, 90.0: 1259978.2043156684, 92.0: 999837.0626397815,
        98.0: 1680659.3115517092, 100.0: 3119407.3717724504, 102.0: 1732904.1507432866,
        108.0: 1172614.8119615735, 110.0: 1581348.8110176916, 112.0: 926253.7176200682,
    }
    mass_ok = True
    for k, exp_m in expected_masses.items():
        i = int(np.where(uniq == k)[0][0])
        if abs(mass[i] - exp_m) > 1e-3:
            mass_ok = False
    print("C1pre (per-strike mass matches locked reference): %s" % ("PASS" if mass_ok else "FAIL"))
    passed &= mass_ok

    ok_total = abs(total - 13149738.963473637) < 1e-3
    print("C1pre2 (total mass): computed=%.6f expected=13149738.963473637 -> %s" % (
        total, "PASS" if ok_total else "FAIL"))
    passed &= ok_total

    clusters, cl_total = segment_clusters(uniq, mass)

    ok_n = (len(clusters) == 3)
    print("C1a (n_clusters==3): computed=%d -> %s" % (len(clusters), "PASS" if ok_n else "FAIL"))
    passed &= ok_n

    expected_centers = [90.22005513544661, 100.01599420555166, 109.86611600808261]
    expected_shares = [0.22331628003900217, 0.4968137278020686, 0.27986999215892927]
    expected_edges = [(88.0, 92.0), (98.0, 102.0), (108.0, 112.0)]

    if ok_n:
        centers_ok = all(abs(clusters[i]['center'] - expected_centers[i]) < 1e-9 for i in range(3))
        print("C1b (centers): computed=%s expected=%s -> %s" % (
            [c['center'] for c in clusters], expected_centers, "PASS" if centers_ok else "FAIL"))
        passed &= centers_ok

        shares_ok = all(abs(clusters[i]['mass_share'] - expected_shares[i]) < 1e-9 for i in range(3))
        print("C1c (mass_share): computed=%s expected=%s -> %s" % (
            [c['mass_share'] for c in clusters], expected_shares, "PASS" if shares_ok else "FAIL"))
        passed &= shares_ok

        edges_ok = all(
            (clusters[i]['lo_strike'], clusters[i]['hi_strike']) == expected_edges[i] for i in range(3)
        )
        print("C1d (edges exact): computed=%s expected=%s -> %s" % (
            [(c['lo_strike'], c['hi_strike']) for c in clusters], expected_edges,
            "PASS" if edges_ok else "FAIL"))
        passed &= edges_ok

        feat = cluster_features(clusters, uniq, mass, cl_total, 100.0)
        dominant_ok = abs(feat['dom_center'] - 100.01599420555166) < 1e-9
        print("C1e (dominant is cluster[1], max mass): dom_center=%.11f -> %s" % (
            feat['dom_center'], "PASS" if dominant_ok else "FAIL"))
        passed &= dominant_ok
    else:
        passed = False

    return passed


def _selftest_c2():
    """C2. esc_above/esc_below/gaps for THREE spot placements (a/b/c)."""
    passed = True

    # --- C2a: spot=100.0 (INSIDE the dominant M cluster [98,102]) ---
    uniq, mass, total = _three_cluster_chain_profile(100.0)
    clusters, cl_total = segment_clusters(uniq, mass)
    feat = cluster_features(clusters, uniq, mass, cl_total, 100.0)

    checks_a = [
        ('n_clusters==3', feat['n_clusters'] == 3),
        ('esc_above==0.0 exact', feat['esc_above'] == 0.0),
        ('esc_below==0.0 exact', feat['esc_below'] == 0.0),
        ('gap_room_up~0.0940923113042002', abs(feat['gap_room_up'] - 0.0940923113042002) < 1e-9),
        ('gap_below~0.10291844288580897', abs(feat['gap_below'] - 0.10291844288580897) < 1e-9),
        ('local_density~0.4968137278020686', abs(feat['local_density'] - 0.4968137278020686) < 1e-9),
        ('cluster_span_pct~0.04', abs(feat['cluster_span_pct'] - 0.04) < 1e-12),
    ]
    ok_a = all(c[1] for c in checks_a)
    failed_a = [c[0] for c in checks_a if not c[1]]
    print("C2a (spot=100 inside dominant): %s%s" % (
        "PASS" if ok_a else "FAIL", (" failed=" + str(failed_a)) if failed_a else ""))
    passed &= ok_a

    # --- C2b: spot=105.0 (in the GAP between M and U) ---
    uniq, mass, total = _three_cluster_chain_profile(105.0)
    clusters, cl_total = segment_clusters(uniq, mass)
    feat = cluster_features(clusters, uniq, mass, cl_total, 105.0)

    checks_b = [
        ('n_clusters==3', feat['n_clusters'] == 3),
        ('esc_above==1.0 exact', feat['esc_above'] == 1.0),
        ('esc_below==0.0 exact', feat['esc_below'] == 0.0),
        ('gap_room_up~0.04599851947101873', abs(feat['gap_room_up'] - 0.04599851947101873) < 1e-9),
        ('gap_below~0.04785620621607033', abs(feat['gap_below'] - 0.04785620621607033) < 1e-9),
        ('local_density==0.0 exact', feat['local_density'] == 0.0),
    ]
    ok_b = all(c[1] for c in checks_b)
    failed_b = [c[0] for c in checks_b if not c[1]]
    print("C2b (spot=105 in gap M->U): %s%s" % (
        "PASS" if ok_b else "FAIL", (" failed=" + str(failed_b)) if failed_b else ""))
    passed &= ok_b

    # stash for C3 reuse
    _selftest_c2._c2b_feat = feat

    # --- C2c: spot=115.0 (ESCAPED ABOVE all clusters; L drops below 5% floor) ---
    uniq, mass, total = _three_cluster_chain_profile(115.0)
    clusters, cl_total = segment_clusters(uniq, mass)
    feat = cluster_features(clusters, uniq, mass, cl_total, 115.0)

    checks_c = [
        ('n_clusters==2', feat['n_clusters'] == 2),
        ('esc_above==1.0 exact', feat['esc_above'] == 1.0),
        ('gap_room_up is NaN', not np.isfinite(feat['gap_room_up'])),
        ('gap_below~0.043674371933487874', abs(feat['gap_below'] - 0.043674371933487874) < 1e-9),
        ('dominant_cluster_mass_share~0.5702594619920868',
         abs(feat['dominant_cluster_mass_share'] - 0.5702594619920868) < 1e-9),
    ]
    ok_c = all(c[1] for c in checks_c)
    failed_c = [c[0] for c in checks_c if not c[1]]
    print("C2c (spot=115 escaped above all, L dropped): %s%s" % (
        "PASS" if ok_c else "FAIL", (" failed=" + str(failed_c)) if failed_c else ""))
    passed &= ok_c

    return passed


def _selftest_c3():
    """C3. the 6-decimal gap_room_up lock (reuses C2b's spot=105 scenario)."""
    passed = True
    uniq, mass, total = _three_cluster_chain_profile(105.0)
    clusters, cl_total = segment_clusters(uniq, mass)
    feat = cluster_features(clusters, uniq, mass, cl_total, 105.0)

    gap_room_up = feat['gap_room_up']
    ok_6dp = abs(gap_room_up - 0.045998) < 1e-6
    ok_full = abs(gap_room_up - 0.04599851947101873) < 1e-12
    ok = ok_6dp and ok_full
    print("C3 (gap_room_up 6dp lock): computed=%.15f 6dp_check=%s full_check=%s -> %s" % (
        gap_room_up, ok_6dp, ok_full, "PASS" if ok else "FAIL"))
    passed &= ok

    return passed


def _selftest_c4():
    """C4. every degenerate case (EXACT outputs, no exception)."""
    passed = True

    def run_scenario(strikes, option_types, ois, spot=100.0, dtes=None, ivs=None):
        n = len(strikes)
        dtes = dtes if dtes is not None else [30] * n
        ivs = ivs if ivs is not None else [0.40] * n
        symbol, date, strike, option_type, dte, open_interest, iv, spot_arr = _make_synthetic_group(
            'SYNC4', '2026-01-01', spot, strikes, option_types, dtes, ois, ivs,
        )
        sign_vec = np.where(option_type == 'call', 1.0, -1.0)
        T, sigma = apply_edge_handling(dte, iv)
        K = strike.astype(np.float64)
        OI = open_interest.astype(np.float64)
        call_wall, put_wall, gamma_spot, gex_spot_abs, gex_spot_signed = compute_walls_at_spot(
            strike, option_type, spot, K, T, sigma, OI, sign_vec
        )
        uniq, mass, smass, oistk = build_per_strike_profile(K, gex_spot_abs, gex_spot_signed, OI)
        clusters, cl_total = segment_clusters(uniq, mass)
        feat = cluster_features(clusters, uniq, mass, cl_total, spot)
        return feat

    # --- D1: single cluster only (spot INSIDE it) ---
    try:
        feat = run_scenario([98.0, 100.0, 102.0], ['call', 'call', 'call'], [5000, 9000, 5000])
        checks = [
            ('n_clusters==1', feat['n_clusters'] == 1),
            ('esc_above==0.0', feat['esc_above'] == 0.0),
            ('esc_below==0.0', feat['esc_below'] == 0.0),
            ('gap_room_up NaN', not np.isfinite(feat['gap_room_up'])),
            ('gap_below NaN', not np.isfinite(feat['gap_below'])),
            ('local_density~1.0', abs(feat['local_density'] - 1.0) < 1e-12),
            ('dom_mass_share~1.0', abs(feat['dominant_cluster_mass_share'] - 1.0) < 1e-12),
            ('cluster_span_pct~0.04', abs(feat['cluster_span_pct'] - 0.04) < 1e-12),
            ('dom_center~100.01599420555166', abs(feat['dom_center'] - 100.01599420555166) < 1e-9),
        ]
        ok = all(c[1] for c in checks)
        failed = [c[0] for c in checks if not c[1]]
        print("D1 (single cluster, spot inside): %s%s" % (
            "PASS" if ok else "FAIL", (" failed=" + str(failed)) if failed else ""))
        passed &= ok
        _d1_feat = feat
    except Exception as e:
        print("D1 (single cluster, spot inside): FAIL (raised %r)" % (e,))
        passed = False
        _d1_feat = None

    # --- D2: no cluster ABOVE spot (cluster far below, spot above it) ---
    try:
        feat = run_scenario([78.0, 80.0, 82.0], ['put', 'put', 'put'], [1500, 3000, 1500])
        checks = [
            ('n_clusters==1', feat['n_clusters'] == 1),
            ('esc_above==1.0', feat['esc_above'] == 1.0),
            ('esc_below==0.0', feat['esc_below'] == 0.0),
            ('gap_room_up NaN', not np.isfinite(feat['gap_room_up'])),
            ('gap_below~0.21777786355413326', abs(feat['gap_below'] - 0.21777786355413326) < 1e-9),
            ('local_density==0.0', feat['local_density'] == 0.0),
        ]
        ok = all(c[1] for c in checks)
        failed = [c[0] for c in checks if not c[1]]
        print("D2 (no cluster above spot): %s%s" % (
            "PASS" if ok else "FAIL", (" failed=" + str(failed)) if failed else ""))
        passed &= ok
    except Exception as e:
        print("D2 (no cluster above spot): FAIL (raised %r)" % (e,))
        passed = False

    # --- D3: no cluster BELOW spot (cluster far above, spot below it) ---
    try:
        feat = run_scenario([118.0, 120.0, 122.0], ['call', 'call', 'call'], [1500, 3000, 1500])
        checks = [
            ('n_clusters==1', feat['n_clusters'] == 1),
            ('esc_above==0.0', feat['esc_above'] == 0.0),
            ('esc_below==1.0', feat['esc_below'] == 1.0),
            ('gap_room_up~0.18051486263726815', abs(feat['gap_room_up'] - 0.18051486263726815) < 1e-9),
            ('gap_below NaN', not np.isfinite(feat['gap_below'])),
            ('local_density==0.0', feat['local_density'] == 0.0),
        ]
        ok = all(c[1] for c in checks)
        failed = [c[0] for c in checks if not c[1]]
        print("D3 (no cluster below spot): %s%s" % (
            "PASS" if ok else "FAIL", (" failed=" + str(failed)) if failed else ""))
        passed &= ok
    except Exception as e:
        print("D3 (no cluster below spot): FAIL (raised %r)" % (e,))
        passed = False

    # --- D4: fewer than 3 strikes (2-strike cluster containing spot) ---
    try:
        feat = run_scenario([99.0, 101.0], ['call', 'put'], [2000, 2000])
        checks = [
            ('n_clusters==1', feat['n_clusters'] == 1),
            ('esc_above==0.0', feat['esc_above'] == 0.0),
            ('esc_below==0.0', feat['esc_below'] == 0.0),
            ('gap_room_up NaN', not np.isfinite(feat['gap_room_up'])),
            ('gap_below NaN', not np.isfinite(feat['gap_below'])),
            ('cluster_span_pct~0.02', abs(feat['cluster_span_pct'] - 0.02) < 1e-12),
        ]
        ok = all(c[1] for c in checks)
        failed = [c[0] for c in checks if not c[1]]
        print("D4 (<3 strikes): %s%s" % (
            "PASS" if ok else "FAIL", (" failed=" + str(failed)) if failed else ""))
        passed &= ok
    except Exception as e:
        print("D4 (<3 strikes): FAIL (raised %r)" % (e,))
        passed = False

    # --- D5: all mass at ONE strike ---
    try:
        feat = run_scenario([100.0], ['call'], [5000])
        checks = [
            ('n_clusters==1', feat['n_clusters'] == 1),
            ('cluster_span_pct==0.0 exact', feat['cluster_span_pct'] == 0.0),
            ('local_density~1.0', abs(feat['local_density'] - 1.0) < 1e-12),
            ('dom_center==100.0', feat['dom_center'] == 100.0),
        ]
        ok = all(c[1] for c in checks)
        failed = [c[0] for c in checks if not c[1]]
        print("D5 (all mass at one strike): %s%s" % (
            "PASS" if ok else "FAIL", (" failed=" + str(failed)) if failed else ""))
        passed &= ok
    except Exception as e:
        print("D5 (all mass at one strike): FAIL (raised %r)" % (e,))
        passed = False

    # --- D6: empty profile (0 strikes) ---
    try:
        uniq = np.array([], dtype=np.float64)
        mass = np.array([], dtype=np.float64)
        clusters, cl_total = segment_clusters(uniq, mass)
        feat = cluster_features(clusters, uniq, mass, cl_total, 100.0)
        checks = [
            ('n_clusters==0', feat['n_clusters'] == 0),
            ('esc_above NaN', not np.isfinite(feat['esc_above'])),
            ('esc_below NaN', not np.isfinite(feat['esc_below'])),
            ('gap_room_up NaN', not np.isfinite(feat['gap_room_up'])),
            ('gap_below NaN', not np.isfinite(feat['gap_below'])),
            ('local_density NaN', not np.isfinite(feat['local_density'])),
            ('dom_mass_share NaN', not np.isfinite(feat['dominant_cluster_mass_share'])),
            ('cluster_span_pct NaN', not np.isfinite(feat['cluster_span_pct'])),
        ]
        ok = all(c[1] for c in checks)
        failed = [c[0] for c in checks if not c[1]]
        print("D6 (empty profile): %s%s" % (
            "PASS" if ok else "FAIL", (" failed=" + str(failed)) if failed else ""))
        passed &= ok
    except Exception as e:
        print("D6 (empty profile): FAIL (raised %r)" % (e,))
        passed = False

    # --- D7: all-put profile equals all-call (D1) -- sign-independence of segmentation ---
    try:
        feat7 = run_scenario([98.0, 100.0, 102.0], ['put', 'put', 'put'], [5000, 9000, 5000])
        if _d1_feat is None:
            ok = False
            print("D7 (all-put == D1 all-call): FAIL (D1 baseline unavailable)")
        else:
            keys_to_compare = [
                'esc_above', 'gap_room_up', 'local_density', 'esc_below', 'gap_below',
                'n_clusters', 'dominant_cluster_mass_share', 'cluster_span_pct', 'dom_center',
            ]
            mismatches = []
            for k in keys_to_compare:
                a, b = feat7[k], _d1_feat[k]
                if isinstance(a, float) and np.isnan(a) and isinstance(b, float) and np.isnan(b):
                    continue
                if a != b:
                    mismatches.append((k, a, b))
            ok = (len(mismatches) == 0)
            print("D7 (all-put == D1 all-call, sign-independence): %s%s" % (
                "PASS" if ok else "FAIL", (" mismatches=" + str(mismatches)) if mismatches else ""))
        passed &= ok
    except Exception as e:
        print("D7 (all-put == D1 all-call): FAIL (raised %r)" % (e,))
        passed = False

    return passed


def run_selftest():
    print("=== GEX MATH_SPEC self-test ===")
    results = {}
    results['A1'] = _selftest_a1()
    results['A2'] = _selftest_a2()
    results['A3'] = _selftest_a3()
    results['A4'] = _selftest_a4()
    results['A5'] = _selftest_a5()
    results['C1'] = _selftest_c1()
    results['C2'] = _selftest_c2()
    results['C3'] = _selftest_c3()
    results['C4'] = _selftest_c4()

    print()
    print("=== summary ===")
    all_pass = True
    for name, ok in results.items():
        print("%s: %s" % (name, "PASS" if ok else "FAIL"))
        all_pass &= ok

    if all_pass:
        print("ALL TESTS PASSED")
        return 0
    else:
        print("SOME TESTS FAILED")
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Dealer GEX feature builder (MATH_SPEC.md)")
    parser.add_argument('--input', type=str, default=None, help="input chain parquet path")
    parser.add_argument('--output', type=str, default=None, help="output features parquet path")
    parser.add_argument('--selftest', action='store_true', help="run MATH_SPEC self-test and exit")
    parser.add_argument('--workers', type=int, default=None,
                         help="number of worker processes (default: max(1, cpu_count-2))")
    args = parser.parse_args()

    if args.selftest:
        rc = run_selftest()
        sys.exit(rc)

    if args.input is None or args.output is None:
        print("ERROR: --input and --output are required unless --selftest is passed")
        sys.exit(2)

    n_workers = args.workers if args.workers is not None else max(1, mp.cpu_count() - 2)
    print("input=%s output=%s workers=%d" % (args.input, args.output, n_workers))

    t0 = time.time()
    out_df = compute_features(args.input, n_workers)
    elapsed = time.time() - t0

    out_df.write_parquet(args.output)
    print("wrote %d rows to %s in %.1fs" % (out_df.shape[0], args.output, elapsed))


if __name__ == '__main__':
    main()
