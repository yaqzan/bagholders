"""
Option Pricing — closed-form model for ATM 30/15 DTE option P&L
================================================================

Replaces the static σ-barrier P&L assumption (`SL fires → -20% on premium`)
with a delta + theta + vega model that estimates realized option P&L given:
  - underlying move
  - bars held since entry
  - whether the trade window crosses an earnings event (and the empirical
    post-event IV haircut if so)

Used by `monte_carlo.py` and `backtest_cascade.py` for two purposes:

  1. **Bar-by-bar trigger**: at each hold bar, compute the time-adjusted
     underlying threshold that fires SL/TP. Theta accumulates → less
     underlying movement is required to hit a -20% SL late in the hold.
     This catches the case where the option blows past SL on theta alone
     (e.g., MDLZ post-earnings: small underlying move, IV crush + theta
     drove the option to -73%).

  2. **Realized P&L on fire**: when the trigger fires, the realized option
     P&L is computed at a random fill price uniform across the bar's full
     intraday range [low, high] — not at the trigger barrier. The wider
     range honestly captures intraday path uncertainty. For "both barriers
     breached" cases this also eliminates the need for a separate 50/50
     coin flip (TP vs SL) — sampling within [low, high] naturally produces
     TP-side, SL-side, or in-between outcomes weighted by how much of the
     bar's range sits on each side of the entry.

The closed-form pieces:

    intrinsic_pnl_pct(side, U, U_0, premium_pct, delta=0.5)
        = sign × delta × (U - U_0) / U_0 / premium_pct
        sign = +1 for call (rises good), -1 for put (rises bad)

    theta_pnl_pct(bars_held, total_dte)
        = sqrt((total_dte - bars_held) / total_dte) - 1.0
        ATM option fair value scales as sqrt(remaining_DTE).
        At bar 0:  0.0
        At bar 7:  -0.124   (1 - sqrt(23/30))
        At bar 15: -0.293   (1 - sqrt(0.5))

    vega_pnl_pct(vega_ratio)
        Multiplicative across earnings: option value scales by vega_ratio.
        This is a post-event IV haircut in [0, 1], not a post/pre option-price
        return, because underlying movement is modeled separately via delta.
        Applied only if the trade window straddles an earnings event. For
        non-spanning trades vega_ratio = 1.0 (no-op).

Total option P&L at bar t with underlying U_t, vega state v_t:

    pnl = v_t x (1 + intrinsic + theta) - 1.0,   capped at -1.0

where v_t = 1.0 pre-earnings, sampled/clamped vega_ratio post-earnings.

Calibration / validation: see `experiments/option_pricing_validation.py`.
"""
import math
import os

# Engine A/B flag (default OFF → option_pnl_pct is bit-identical to production).
# When GAMMA_AWARE=1, option_pnl_pct routes through the BS delta+gamma model
# below, so every consumer (monte_carlo, backtest_cascade) prices convexly.
_GAMMA_AWARE = os.environ.get('GAMMA_AWARE', '0') == '1'


# -- Defaults (override per-call if the strategy uses different values) ------

DEFAULT_DELTA       = 0.5
DEFAULT_TOTAL_DTE   = 30   # nominal DTE used for theta scaling


# -- Closed-form components --------------------------------------------------

def theta_pnl_pct(bars_held, total_dte=DEFAULT_TOTAL_DTE):
    """ATM option theta drag as fraction of entry premium.
    Returns negative (or 0 at entry).

    Model: V(t)/V(0) = sqrt((DTE - t) / DTE)  for ATM, no underlying move.
    """
    bars_eff = max(0, min(bars_held, total_dte))
    if total_dte <= 0:
        return 0.0
    factor = math.sqrt((total_dte - bars_eff) / total_dte)
    return factor - 1.0


def intrinsic_pnl_pct(side, current_underlying, entry_underlying, premium_pct,
                      delta=DEFAULT_DELTA):
    """Delta term as fraction of entry premium.
    For calls: positive when U rises. For puts: positive when U falls.
    """
    if entry_underlying <= 0 or premium_pct <= 0:
        return 0.0
    move_frac = (current_underlying - entry_underlying) / entry_underlying
    sign = 1.0 if side == 'call' else -1.0
    return sign * delta * move_frac / premium_pct


def option_pnl_pct(side, current_underlying, entry_underlying, bars_held,
                   premium_pct, total_dte=DEFAULT_TOTAL_DTE,
                   vega_ratio=1.0, delta=DEFAULT_DELTA):
    """Full option P&L (fraction of entry premium), capped at -1.0.

    pnl = vega × (1 + intrinsic + theta) - 1
        intrinsic = delta term from underlying move
        theta     = sqrt((DTE-t)/DTE) - 1
        vega      = post-event IV haircut (1.0 if no earnings span)

    Returns float in [-1.0, +inf).
    """
    if _GAMMA_AWARE:
        # Route through the gamma-aware BS value (delta is derived internally).
        return bs_option_pnl_pct(side, current_underlying, entry_underlying,
                                 bars_held, premium_pct, total_dte=total_dte,
                                 vega_ratio=vega_ratio)
    intrinsic = intrinsic_pnl_pct(side, current_underlying, entry_underlying,
                                  premium_pct, delta=delta)
    theta = theta_pnl_pct(bars_held, total_dte=total_dte)
    pnl_pre_vega = intrinsic + theta
    if vega_ratio == 1.0:
        return max(-1.0, pnl_pre_vega)
    raw = vega_ratio * (1.0 + pnl_pre_vega) - 1.0
    return max(-1.0, raw)


# -- Gamma-aware (Black-Scholes) option value --------------------------------
# The constant-delta model above is LINEAR in the underlying move (delta held
# at 0.5). Real ATM options are convex: delta ramps toward 1 as a winner goes
# ITM (gain accelerates) and toward 0 as it goes OTM (loss on the underlying
# leg decelerates). This BS-anchored value adds that gamma/convexity while
# staying self-consistent with the existing realized-vol premium: the total
# vol over the option's life is implied from `premium_pct` (ATM BS), strike =
# entry, r = 0. It REDUCES to the sqrt((DTE-t)/DTE) theta model at ATM/no-move,
# so it is a strict generalization, not a recalibration.

_SQRT2    = math.sqrt(2.0)
_SQRT2PI  = math.sqrt(2.0 * math.pi)


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _vol_total_from_premium(premium_pct):
    """Total vol over the option's full life (sigma*sqrt(T0)) implied from the
    ATM premium. Exact ATM BS: premium_pct = erf(0.5*w0/sqrt2); the linearized
    inverse w0 ~= sqrt(2*pi)*premium_pct is <1% off for premium_pct <~ 0.1, and
    the entry-normalisation below cancels its level error anyway."""
    return _SQRT2PI * premium_pct


def bs_value_frac(side, current_underlying, entry_underlying, bars_held,
                  premium_pct, total_dte=DEFAULT_TOTAL_DTE):
    """Black-Scholes ATM-anchored option value as a fraction of ENTRY PREMIUM
    (so pnl_pre_vega = bs_value_frac - 1). Entry-normalised → exactly 1.0 at
    bar 0 / no move. Adds delta+gamma+theta coherently."""
    if premium_pct <= 0 or entry_underlying <= 0 or total_dte <= 0:
        return 1.0
    w0 = _vol_total_from_premium(premium_pct)
    if w0 <= 1e-9:
        return 1.0
    val0 = math.erf(0.5 * w0 / _SQRT2)            # ATM entry value (fraction of U0)
    if val0 <= 1e-12:
        return 1.0
    bars_eff = max(0, min(bars_held, total_dte))
    f = (total_dte - bars_eff) / total_dte        # remaining time fraction
    S = current_underlying / entry_underlying     # moneyness ratio (K = 1)
    w = w0 * math.sqrt(f)                          # remaining total vol
    if w <= 1e-9:                                  # at expiry: pure intrinsic
        intr = max(0.0, S - 1.0) if side == 'call' else max(0.0, 1.0 - S)
        return intr / val0
    m = math.log(S) if S > 0 else -50.0
    d1 = (m + 0.5 * w * w) / w
    d2 = d1 - w
    if side == 'call':
        val_t = S * _norm_cdf(d1) - _norm_cdf(d2)
    else:
        val_t = _norm_cdf(-d2) - S * _norm_cdf(-d1)
    return val_t / val0                            # relative to entry premium


def bs_option_pnl_pct(side, current_underlying, entry_underlying, bars_held,
                      premium_pct, total_dte=DEFAULT_TOTAL_DTE, vega_ratio=1.0):
    """Gamma-aware analogue of option_pnl_pct (fraction of premium, capped -1.0)."""
    vf = bs_value_frac(side, current_underlying, entry_underlying, bars_held,
                       premium_pct, total_dte=total_dte)
    return max(-1.0, vega_ratio * vf - 1.0)


def bs_fires_on_bar(side, target_pnl_pct, bar_high, bar_low, entry_underlying,
                    bars_held, premium_pct, total_dte=DEFAULT_TOTAL_DTE,
                    vega_ratio=1.0):
    """Gamma-aware trigger. BS P&L is monotonic in the underlying, so evaluate
    it at the bar's FAVORABLE extreme and test the crossing directly (no
    threshold inversion needed)."""
    if side == 'call':
        u_ext = bar_high if target_pnl_pct > 0 else bar_low
    else:
        u_ext = bar_low if target_pnl_pct > 0 else bar_high
    pnl_ext = bs_option_pnl_pct(side, u_ext, entry_underlying, bars_held,
                                premium_pct, total_dte=total_dte,
                                vega_ratio=vega_ratio)
    return pnl_ext >= target_pnl_pct if target_pnl_pct > 0 else pnl_ext <= target_pnl_pct


def bs_random_fill_pnl(side, bar_high, bar_low, entry_underlying, bars_held,
                       premium_pct, rng, total_dte=DEFAULT_TOTAL_DTE,
                       vega_ratio=1.0):
    """Gamma-aware analogue of random_fill_pnl."""
    if bar_high <= bar_low:
        u_fire = bar_low
    else:
        u_fire = bar_low + rng.random() * (bar_high - bar_low)
    return bs_option_pnl_pct(side, u_fire, entry_underlying, bars_held,
                             premium_pct, total_dte=total_dte, vega_ratio=vega_ratio)


# -- Time-adjusted underlying thresholds for trigger detection ---------------

def adjusted_underlying_threshold(target_pnl_pct, side, bars_held,
                                  premium_pct, total_dte=DEFAULT_TOTAL_DTE,
                                  vega_ratio=1.0, delta=DEFAULT_DELTA):
    """Return the underlying multiplier (relative to entry; e.g., 1.05 = +5%)
    at which option P&L hits target_pnl_pct, accounting for accumulated theta
    and current vega state.

    Solving for U:
        target = vega × (1 + intrinsic + theta) - 1
        intrinsic = (target + 1) / vega - 1 - theta
        intrinsic = sign × delta × move_frac / premium_pct
        move_frac = intrinsic × premium_pct / (sign × delta)

    Calls (target<0 = SL): U_threshold < entry, fires when low ≤ threshold.
    Puts (target<0 = SL): U_threshold > entry, fires when high ≥ threshold.
    Calls (target>0 = TP): U_threshold > entry, fires when high ≥ threshold.
    Puts (target>0 = TP): U_threshold < entry, fires when low ≤ threshold.
    """
    if premium_pct <= 0 or delta <= 0 or vega_ratio <= 0:
        return None
    theta = theta_pnl_pct(bars_held, total_dte=total_dte)
    intrinsic_required = (target_pnl_pct + 1.0) / vega_ratio - 1.0 - theta
    sign = 1.0 if side == 'call' else -1.0
    move_frac = intrinsic_required * premium_pct / (sign * delta)
    return 1.0 + move_frac


# -- Random fill within a triggered bar --------------------------------------

def random_fill_pnl(side, bar_high, bar_low, entry_underlying, bars_held,
                    premium_pct, rng, total_dte=DEFAULT_TOTAL_DTE,
                    vega_ratio=1.0, delta=DEFAULT_DELTA):
    """Sample a uniform underlying fill price across the bar's full intraday
    range [low, high], compute option P&L at that fill.

    Sampling [low, high] (vs the narrower [open, close]) honestly captures
    intraday path uncertainty AND eliminates the need for a separate
    50/50 coin-flip on "both barriers breached" cases — the random sample
    naturally produces TP-side / SL-side / in-between outcomes weighted by
    how much of the bar's range sits on each side of the entry.

    Per-iteration seeded RNG provides MC dispersion (replaces the previous
    3-mode Conservative/Realistic/Optimistic collision system).
    """
    if bar_high <= bar_low:
        u_fire = bar_low
    else:
        u_fire = bar_low + rng.random() * (bar_high - bar_low)
    return option_pnl_pct(side, u_fire, entry_underlying, bars_held,
                          premium_pct, total_dte=total_dte,
                          vega_ratio=vega_ratio, delta=delta)


# -- Vega ratio sampler (delegates to iv_crush_model's empirical pool) -------

_VEGA_CACHE = None

def sample_vega_ratio(side, dte, rng):
    """Sample a post-earnings IV/vega multiplier for an earnings-spanning trade.

    The empirical source stores post/pre option price ratios. Those ratios can
    exceed 1.0 when the underlying move dominates the event, but the pricing
    model already accounts for underlying move via delta. Here the multiplier
    represents post-event IV behavior only, so it can haircut option value or
    leave it unchanged, never boost it.

    Returns 1.0 if no samples available (graceful fallback). Side and DTE band
    route to the appropriate stratified pool.
    """
    global _VEGA_CACHE
    try:
        from iv_crush_model import _load_samples, _dte_band
    except ImportError:
        return 1.0
    if _VEGA_CACHE is None:
        _VEGA_CACHE = _load_samples()
    band = _dte_band(int(dte))
    side_key = side.upper()
    pool = _VEGA_CACHE.get((side_key, band))
    if not pool:
        # Side fallback
        pool = []
        for (s, _), p in _VEGA_CACHE.items():
            if s == side_key:
                pool.extend(p)
    if not pool:
        return 1.0
    ratio = pool[rng.randrange(len(pool))]
    return max(0.0, min(1.0, ratio))


# -- Convenience wrapper for trigger detection on a bar ----------------------

def fires_on_bar(side, target_pnl_pct, bar_high, bar_low, entry_underlying,
                 bars_held, premium_pct, total_dte=DEFAULT_TOTAL_DTE,
                 vega_ratio=1.0, delta=DEFAULT_DELTA):
    """Returns True iff the option would have hit target_pnl_pct intraday on
    this bar, using the time-adjusted underlying threshold.

    Cheap O(1). Use to replace the static σ-barrier check in compute_*_outcome.
    """
    threshold_mult = adjusted_underlying_threshold(
        target_pnl_pct, side, bars_held, premium_pct,
        total_dte=total_dte, vega_ratio=vega_ratio, delta=delta
    )
    if threshold_mult is None:
        return False
    threshold_price = entry_underlying * threshold_mult
    if side == 'call':
        # call SL (target<0): U must drop below threshold (threshold_mult<1)
        # call TP (target>0): U must rise above threshold (threshold_mult>1)
        if target_pnl_pct < 0:
            return bar_low <= threshold_price
        return bar_high >= threshold_price
    # put
    if target_pnl_pct < 0:
        # put SL: U rises above threshold
        return bar_high >= threshold_price
    # put TP: U falls below threshold
    return bar_low <= threshold_price


# -- Self-test / sanity check (only when run directly) -----------------------

def _sanity_check():
    """Quick numerical sanity: verify the model returns expected values at
    known reference points.
    """
    # Reference: ATM 30 DTE put, premium = 1.82 × σ_daily.
    # σ_daily = 2% (typical large-cap), so premium_pct = 0.0364.
    # Entry $100, current $100 (no move), bar 0: pnl = 0.
    pnl = option_pnl_pct('put', 100.0, 100.0, 0, 0.0364)
    assert abs(pnl) < 1e-9, f"Expected 0, got {pnl}"

    # Bar 15 (half DTE), no underlying move: theta_pnl ≈ -0.293
    pnl15 = option_pnl_pct('put', 100.0, 100.0, 15, 0.0364)
    assert abs(pnl15 - (math.sqrt(0.5) - 1.0)) < 1e-9, f"Theta @15: {pnl15}"

    # Underlying rises 0.728σ × 2% = 1.456% (PUT_SL_SIGMA reference move).
    # At bar 0, vega=1: should give exactly -20% on premium.
    sl_move = 0.728 * 0.02
    pnl_sl = option_pnl_pct('put', 100.0 * (1 + sl_move), 100.0, 0, 0.0364)
    assert abs(pnl_sl - (-0.20)) < 1e-3, f"PUT_SL @ bar 0: {pnl_sl} (expected -0.20)"

    # Same move at bar 7 (theta ≈ -0.124): should be deeper than -20%.
    pnl_sl_7 = option_pnl_pct('put', 100.0 * (1 + sl_move), 100.0, 7, 0.0364)
    assert pnl_sl_7 < -0.30, f"PUT_SL @ bar 7: {pnl_sl_7} (expected < -0.30)"

    # Time-adjusted SL threshold: at bar 7, less underlying movement should
    # trigger -20% on premium.
    thresh_t0 = adjusted_underlying_threshold(-0.20, 'put', 0, 0.0364)
    thresh_t7 = adjusted_underlying_threshold(-0.20, 'put', 7, 0.0364)
    assert thresh_t0 > thresh_t7, f"Threshold should tighten with theta: {thresh_t0} vs {thresh_t7}"

    # Vega crush: option held through earnings with 0.80 ratio, no underlying
    # move: pnl = 0.80 × 1.0 - 1 = -0.20.
    pnl_v = option_pnl_pct('put', 100.0, 100.0, 0, 0.0364, vega_ratio=0.80)
    assert abs(pnl_v - (-0.20)) < 1e-9, f"Vega crush: {pnl_v}"

    print("option_pricing.py sanity check: OK")


if __name__ == '__main__':
    _sanity_check()
