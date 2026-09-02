"""
equity_wave.wave — Track A regime/equity aggression-scaling primitives (B2 harness).

PURE FUNCTIONS on plain Python/numpy arrays. NO MySQL, NO MC, NO imports of the
heavy engine. Everything here is unit-testable on synthetic market/equity arrays
(see test_wave.py) and is the logic that a later MC arm would wire at the
monte_carlo.py:2668 call alloc_frac insertion point:

    alloc_frac = TIER_ALLOC[tier] * reg_scale_c * ... * spread_tilt_scale * AW_SCALE

where AW_SCALE is produced by one of:

  (A) regime_strength_index(...)  -> strength in [0,1]  -> aggression(strength)
        a smooth, bounded function of the MARKET-CONTEXT per-date maps from R2
        (VIX / breadth / McClellan / TRIN / breadth-divergence / semivol). This
        is the "does the MARKET say it's a strong regime" signal.

  (B) EquityNativeScaler.scale(...)  -> aggression multiplier
        driven ONLY by the running equity curve (drawdown-from-peak + a
        weekly-downsampled {flat, skyrocketing, drawing-down, recovering} state
        classifier). This is the "does the CURVE ITSELF carry signal beyond the
        market inputs" A/B counterpart. It explicitly de-whipsaws the flat curve
        (no raw daily oscillator).

Both return a bounded multiplier so the engine's collapse/DD floors are never
violated by a runaway aggression spike. The A/B question — whether (B) adds
anything beyond (A) — is answered by analyze.py on per-path MC equity curves.

Design invariants (assert-checked in tests):
  * strength is monotone in each "stronger regime" input direction, holding others fixed
  * strength in [0,1]; aggression in [AGG_MIN, AGG_MAX]; both bounded for ANY input incl NaN
  * a perfectly FLAT equity curve produces a STABLE (non-oscillating) state + scale
  * the drawdown-space transform is in [0,1], 0 at peak, 1 at full wipe, monotone in dd
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence


# ---------------------------------------------------------------------------
# small numeric helpers (no numpy dependency required; numpy optional in tests)
# ---------------------------------------------------------------------------

def _clip(x: float, lo: float, hi: float) -> float:
    if x != x:            # NaN-safe: NaN -> midpoint (neutral), never propagates
        return (lo + hi) / 2.0
    return lo if x < lo else hi if x > hi else x


def _safe(x, default: float = float('nan')) -> float:
    """Coerce None/non-finite to NaN (so _clip neutralizes them)."""
    if x is None:
        return default
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return default
    if xf != xf or xf in (float('inf'), float('-inf')):
        return default
    return xf


def _ramp(x: float, lo: float, hi: float) -> float:
    """Linear 0->1 ramp over [lo, hi], clamped. NaN -> 0.5 (neutral)."""
    if x != x:
        return 0.5
    if hi <= lo:
        return 0.0 if x <= lo else 1.0
    t = (x - lo) / (hi - lo)
    return 0.0 if t < 0.0 else 1.0 if t > 1.0 else t


def _gauss_penalty(x: float, center: float, width: float) -> float:
    """Gaussian bump in [0,1]: 1 at center, ->0 in tails. NaN -> 0 (no penalty)."""
    if x != x or width <= 0:
        return 0.0
    z = (x - center) / width
    return math.exp(-0.5 * z * z)


# ===========================================================================
# (A) MARKET-CONTEXT regime-strength index
# ===========================================================================

@dataclass(frozen=True)
class StrengthParams:
    """Tunable shape of the regime-strength index. Defaults are documented,
    economically-motivated starting points (NOT yet swept). Every sub-score is
    a smooth bounded transform of one R2 market-context input, oriented so that
    HIGHER == STRONGER regime (more aggression warranted).

    The combined strength is a WEIGHTED GEOMETRIC-LEANING blend (see
    regime_strength_index) so that a single very-weak input (e.g. crash-mode VIX)
    pulls the whole index down rather than being averaged away — a strong regime
    requires broad agreement, a weak regime needs only one alarm.
    """
    # --- VIX: low VIX = calm = strong. Ramp DOWN from VIX_CALM(strong) to VIX_FEAR(weak). ---
    vix_calm: float = 14.0      # at/below -> full strength contribution (1.0)
    vix_fear: float = 30.0      # at/above -> zero strength contribution (0.0)
    w_vix: float = 1.0
    # --- breadth_score: high participation = strong. Ramp UP. ---
    brd_weak: float = 35.0      # at/below -> 0
    brd_strong: float = 70.0    # at/above -> 1
    w_brd: float = 1.0
    # --- McClellan oscillator: positive breadth-momentum = strong; the FLAT band
    #     (~0, topping/low-EV per MWDD) is mid; deep-negative (capitulation) is a
    #     mean-reversion setup, NOT "strong regime" for fresh call sizing -> low. ---
    mcc_weak: float = -40.0     # deep-negative -> 0 (capitulation; withhold fresh aggression)
    mcc_strong: float = 40.0    # strong-positive momentum -> 1
    w_mcc: float = 0.7
    # --- TRIN (Arms): the NEUTRAL band (~1.0-1.3, TVDD low-EV trough) is the weak
    #     spot; froth(<0.7) and panic(>1.8) are extremes. For *strength* we treat
    #     the froth/healthy-up-volume side (<~0.9) as strong and neutral/distribution
    #     as weak. Panic(>1.8) is a capitulation extreme -> low strength (withhold). ---
    trin_strong: float = 0.85   # at/below -> strong (healthy up-volume)
    trin_neutral: float = 1.30  # neutral/distribution trough -> weak
    trin_panic: float = 1.90    # at/above -> capitulation -> weak again
    w_trin: float = 0.5
    # --- breadth-divergence at highs (BDIV-style): spy_from60h (<=0, distance below
    #     60d high) + brd_det10 (>0 = internals deteriorating). A pre-top divergence
    #     (near highs AND breadth rolling over) is a LATENT-weakness signal -> reduce
    #     strength. Encoded as a penalty subtracted from 1. ---
    bdiv_prox_cut: float = 0.020   # |from60h| at/above which proximity factor -> 0 (far from highs, no divergence risk)
    bdiv_prox_full: float = 0.0075 # |from60h| at/below which proximity factor -> 1 (right at the high)
    bdiv_gap_c: float = 7.7        # breadth-deterioration center (matches shipped BDIV GAP_C)
    bdiv_gap_w: float = 3.5        # breadth-deterioration width
    bdiv_max_penalty: float = 0.5  # max strength haircut from a full divergence
    w_bdiv: float = 0.6
    # --- semivol_r (skew bridge): the [~0.7, ~1.25] sweet spot is best (SVR);
    #     euphoric-low (<0.7, expensive calls) and crash-mode-high (>1.6) are weak.
    #     A symmetric band-pass -> strength. ---
    svr_lo_full: float = 0.70
    svr_hi_full: float = 1.25
    svr_lo_cut: float = 0.50
    svr_hi_cut: float = 1.65
    w_svr: float = 0.5
    # blend exponent: p->1 is arithmetic mean; p->0 approaches geometric mean
    # (one weak input dominates). 0.4 = "lean toward the weakest input."
    blend_p: float = 0.4


def _vix_strength(vix: float, p: StrengthParams) -> float:
    # low VIX strong: ramp 1->0 as VIX goes calm->fear
    return 1.0 - _ramp(_safe(vix), p.vix_calm, p.vix_fear)


def _brd_strength(brd: float, p: StrengthParams) -> float:
    return _ramp(_safe(brd), p.brd_weak, p.brd_strong)


def _mcc_strength(mcc: float, p: StrengthParams) -> float:
    return _ramp(_safe(mcc), p.mcc_weak, p.mcc_strong)


def _trin_strength(trin: float, p: StrengthParams) -> float:
    t = _safe(trin)
    if t != t:
        return 0.5
    # strong below trin_strong, falling to weak across the neutral trough, and
    # staying weak into panic (capitulation is not "strong regime" for fresh calls)
    if t <= p.trin_strong:
        return 1.0
    if t >= p.trin_neutral:
        return 0.0
    return 1.0 - (t - p.trin_strong) / (p.trin_neutral - p.trin_strong)


def _bdiv_strength(bdiv, p: StrengthParams) -> float:
    """bdiv = (spy_from60h, brd_det10) or None. Returns 1 - penalty in [1-max_pen, 1]."""
    if bdiv is None:
        return 1.0
    try:
        from60h, det10 = bdiv
    except (TypeError, ValueError):
        return 1.0
    from60h = _safe(from60h)
    det10 = _safe(det10)
    if from60h != from60h or det10 != det10:
        return 1.0
    dist = abs(from60h)
    # proximity: 1 right at the high, 0 once far below it
    if p.bdiv_prox_cut <= p.bdiv_prox_full:
        prox = 1.0 if dist <= p.bdiv_prox_full else 0.0
    else:
        prox = (p.bdiv_prox_cut - dist) / (p.bdiv_prox_cut - p.bdiv_prox_full)
        prox = 0.0 if prox < 0.0 else 1.0 if prox > 1.0 else prox
    gap = _gauss_penalty(det10, p.bdiv_gap_c, p.bdiv_gap_w)
    penalty = p.bdiv_max_penalty * prox * gap
    return 1.0 - penalty


def _svr_strength(svr: float, p: StrengthParams) -> float:
    s = _safe(svr)
    if s != s:
        return 0.5
    if p.svr_lo_full <= s <= p.svr_hi_full:
        return 1.0
    if s < p.svr_lo_full:
        if p.svr_lo_full <= p.svr_lo_cut:
            return 0.0 if s <= p.svr_lo_cut else 1.0
        t = (s - p.svr_lo_cut) / (p.svr_lo_full - p.svr_lo_cut)
        return _clip(t, 0.0, 1.0)
    if p.svr_hi_cut <= p.svr_hi_full:
        return 0.0 if s >= p.svr_hi_cut else 1.0
    t = (p.svr_hi_cut - s) / (p.svr_hi_cut - p.svr_hi_full)
    return _clip(t, 0.0, 1.0)


def regime_strength_index(
    vix: Optional[float] = None,
    breadth: Optional[float] = None,
    mcclellan: Optional[float] = None,
    trin: Optional[float] = None,
    bdiv=None,
    semivol_r: Optional[float] = None,
    params: StrengthParams = StrengthParams(),
    return_parts: bool = False,
):
    """Combine the R2 market-context inputs into ONE regime-strength score in [0,1].

    HIGHER = stronger regime = more aggression warranted. Each sub-score is a smooth
    bounded transform (above). The blend is a weighted power-mean with exponent
    params.blend_p in (0,1]:

        strength = ( sum_i w_i * s_i^p / sum_i w_i ) ^ (1/p)

    p=1 is the arithmetic mean; p->0 approaches the (weighted) geometric mean, where
    any single near-zero sub-score crushes the result. We use p=0.4 so a strong
    regime requires BROAD agreement while a single alarm (crash VIX, capitulation
    breadth) can withhold aggression on its own — the asymmetry the strategy wants.

    Any missing/None/NaN input contributes a NEUTRAL 0.5 (or is skipped if weight 0),
    never a crash. Result is ALWAYS in [0,1].
    """
    p = params
    parts = {
        'vix':  (_vix_strength(vix, p),  p.w_vix),
        'brd':  (_brd_strength(breadth, p), p.w_brd),
        'mcc':  (_mcc_strength(mcclellan, p), p.w_mcc),
        'trin': (_trin_strength(trin, p), p.w_trin),
        'bdiv': (_bdiv_strength(bdiv, p), p.w_bdiv),
        'svr':  (_svr_strength(semivol_r, p), p.w_svr),
    }
    pw = max(1e-6, p.blend_p)
    num = 0.0
    den = 0.0
    for name, (s, w) in parts.items():
        if w <= 0:
            continue
        s = _clip(s, 0.0, 1.0)
        num += w * (s ** pw)
        den += w
    if den <= 0:
        strength = 0.5
    else:
        strength = (num / den) ** (1.0 / pw)
    strength = _clip(strength, 0.0, 1.0)
    if return_parts:
        return strength, {k: _clip(v[0], 0.0, 1.0) for k, v in parts.items()}
    return strength


# --- aggression mapping: strength in [0,1] -> bounded alloc multiplier ---------

@dataclass(frozen=True)
class AggressionParams:
    """Map a strength in [0,1] to an aggression multiplier in [agg_min, agg_max].
    NEUTRAL strength (0.5) maps to ~1.0 so a flat/unknown regime ~= baseline.

    Bounded by construction so the engine's premium caps / collapse floor are
    never violated by the wave: a runaway 'strong' reading can lift aggression
    at most to agg_max; a crash can withhold at most to agg_min (NOT zero, so the
    book still participates — withholding != halting)."""
    agg_min: float = 0.50   # max withhold in a weak regime
    agg_max: float = 1.50   # max boost in a strong regime
    gamma: float = 1.0      # >1 = convex (only the very-strong get the boost); <1 concave


def strength_to_aggression(strength: float, params: AggressionParams = AggressionParams()) -> float:
    """Smooth, bounded, monotone map. strength=0.5 -> ~1.0; 0 -> agg_min; 1 -> agg_max."""
    s = _clip(_safe(strength, 0.5), 0.0, 1.0)
    a = params.agg_min
    b = params.agg_max
    if params.gamma != 1.0:
        # center the gamma around 0.5 so neutral stays ~1.0
        if s >= 0.5:
            t = 0.5 + 0.5 * (((s - 0.5) / 0.5) ** params.gamma)
        else:
            t = 0.5 - 0.5 * (((0.5 - s) / 0.5) ** params.gamma)
    else:
        t = s
    return a + (b - a) * t


# ===========================================================================
# (B) EQUITY-CURVE-NATIVE scaler (drawdown-from-peak + weekly state classifier)
# ===========================================================================

# weekly state labels
S_FLAT = 'flat'
S_SKY = 'skyrocketing'
S_DOWN = 'drawing_down'
S_RECOVER = 'recovering'
S_STATES = (S_FLAT, S_SKY, S_DOWN, S_RECOVER)


def drawdown_from_peak(equity: Sequence[float]) -> list[float]:
    """Running drawdown-from-peak series in [0,1]. 0 at/above a running peak,
    ->1 as equity collapses toward zero. Monotone in (peak-equity)/peak."""
    out = []
    peak = -float('inf')
    for v in equity:
        vf = _safe(v, default=0.0)
        if vf > peak:
            peak = vf
        if peak > 0:
            dd = (peak - vf) / peak
        else:
            dd = 0.0
        out.append(_clip(dd, 0.0, 1.0))
    return out


def drawdown_space(dd: float, k: float = 6.0) -> float:
    """Transform a drawdown fraction (0..1) into a smooth aggression-DAMPING factor
    in [0,1] where 1 = full aggression (at peak, dd=0) and ->0 as dd grows. This is
    the equity-native analog of withholding: deep drawdown -> hold back. Uses a
    smooth exp so there is no cliff. k controls steepness (k=6 ~ half-aggression by
    ~12% dd). ALWAYS in [0,1], monotone DECREASING in dd."""
    d = _clip(_safe(dd, 0.0), 0.0, 1.0)
    return math.exp(-k * d)


@dataclass
class WeeklyStateClassifier:
    """Classify a WEEKLY-downsampled equity curve into {flat, skyrocketing,
    drawing_down, recovering}, with explicit flat-curve whipsaw suppression.

    The cautionary case (Jan2025 +51% -> Mar -28%) is WHY this must be weekly and
    hysteretic: a raw daily oscillator flips state on noise and chases the last
    jump straight into the next drawdown. The classifier:

      * operates on weekly bars (downsample first via weekly_downsample)
      * uses a DEAD-BAND (flat_band): |week-over-week log-return| below the band
        => FLAT, regardless of prior state. A long flat stretch CANNOT be relabeled
        skyrocketing/drawing-down by noise.
      * uses HYSTERESIS: entering SKY/DOWN needs to clear `enter`; you stay until
        you fall back inside `exit` (exit < enter). Prevents single-week flip-flop.
      * RECOVERING is "rising again while still below a recent peak" (dd>recover_dd
        AND last week up) — distinct from SKY (rising at/near new highs).

    NOTE: the classifier is descriptive, not predictive. analyze.py tests whether the
    state has ANY forward predictive power; the cautionary case says it likely does
    NOT for the jump direction, which is the whole point of the A/B.
    """
    flat_band: float = 0.015      # |weekly log-ret| below this => FLAT (de-whipsaw)
    enter: float = 0.04           # weekly log-ret to ENTER sky / -enter to enter down
    exit_: float = 0.015          # fall back inside this band to LEAVE sky/down (hysteresis)
    recover_dd: float = 0.05      # must be at least this far below peak to be 'recovering' vs 'sky'
    _state: str = field(default=S_FLAT, init=False)

    def reset(self) -> None:
        self._state = S_FLAT

    def step(self, weekly_log_ret: float, dd: float) -> str:
        """Advance one WEEKLY bar. weekly_log_ret = ln(equity_t / equity_{t-1}) on the
        weekly series; dd = drawdown-from-peak at this weekly bar. Returns the state."""
        r = _safe(weekly_log_ret, 0.0)
        d = _clip(_safe(dd, 0.0), 0.0, 1.0)
        a = abs(r)
        prev = self._state

        # 1) dead-band: small moves are FLAT no matter what the prior state was.
        if a < self.flat_band:
            self._state = S_FLAT
            return self._state

        # 2) hysteresis around the strong-move thresholds
        if r >= self.enter:
            # rising hard. SKY if near highs, RECOVERING if still well below peak.
            self._state = S_RECOVER if d > self.recover_dd else S_SKY
        elif r <= -self.enter:
            self._state = S_DOWN
        else:
            # in the [flat_band, enter) gray zone: keep a strong prior state until
            # it decays inside exit_, else classify by sign of the (non-flat) move.
            if prev in (S_SKY, S_RECOVER) and r > self.exit_:
                self._state = S_RECOVER if d > self.recover_dd else S_SKY
            elif prev == S_DOWN and r < -self.exit_:
                self._state = S_DOWN
            else:
                # mild move, no strong prior to hold: sign decides, dd disambiguates
                if r > 0:
                    self._state = S_RECOVER if d > self.recover_dd else S_SKY
                else:
                    self._state = S_DOWN
        return self._state


@dataclass(frozen=True)
class EquityNativeParams:
    """Aggression multipliers for the equity-native scaler. Bounded like the
    market-context aggression. The state multipliers encode a DEFENSIVE prior
    (the strategy's own lesson: jumps are not predictable from curve momentum, so
    do not chase SKY; protect in DOWN). drawdown_space provides a continuous
    overlay so a deepening DD damps aggression smoothly even within a state."""
    agg_flat: float = 1.00        # neutral when the curve is going nowhere
    agg_sky: float = 1.00         # do NOT chase the last jump (cautionary case)
    agg_down: float = 0.60        # protect during an active drawdown
    agg_recover: float = 1.10     # modest lean-in as it climbs back (post-DD value)
    dd_k: float = 6.0             # drawdown_space steepness overlay
    agg_min: float = 0.40
    agg_max: float = 1.50
    use_dd_overlay: bool = True   # multiply state mult by drawdown_space(dd)


class EquityNativeScaler:
    """Stateful equity-curve-native aggression scaler for A/B vs the market-context
    index. Feed it the (weekly-downsampled) equity curve incrementally; it returns
    a bounded aggression multiplier from {state multiplier} x {drawdown_space overlay}.

    This is deliberately driven by NOTHING but the equity curve, so analyze.py can
    test whether it adds predictive power over regime_strength_index."""

    def __init__(self, params: EquityNativeParams = EquityNativeParams(),
                 classifier: Optional[WeeklyStateClassifier] = None):
        self.p = params
        self.clf = classifier or WeeklyStateClassifier()
        self.clf.reset()
        self._state = S_FLAT

    @property
    def state(self) -> str:
        return self._state

    def scale(self, weekly_log_ret: float, dd: float) -> float:
        self._state = self.clf.step(weekly_log_ret, dd)
        base = {
            S_FLAT: self.p.agg_flat,
            S_SKY: self.p.agg_sky,
            S_DOWN: self.p.agg_down,
            S_RECOVER: self.p.agg_recover,
        }[self._state]
        if self.p.use_dd_overlay:
            base *= drawdown_space(dd, self.p.dd_k)
        return _clip(base, self.p.agg_min, self.p.agg_max)


# ===========================================================================
# weekly downsample (shared by the classifier + analyze.py)
# ===========================================================================

def weekly_downsample(dates: Sequence, equity: Sequence[float], bars_per_week: int = 5):
    """Downsample a daily equity curve to weekly by taking the last bar of each
    `bars_per_week` block (Fri-close convention). Returns (weekly_dates, weekly_equity).
    If `dates` is None, returns (None, weekly_equity)."""
    we = []
    wd = [] if dates is not None else None
    n = len(equity)
    for end in range(bars_per_week - 1, n, bars_per_week):
        we.append(_safe(equity[end], 0.0))
        if wd is not None:
            wd.append(dates[end])
    # always include the final partial week's last point if not already captured
    if n and (n - 1) % bars_per_week != bars_per_week - 1:
        we.append(_safe(equity[-1], 0.0))
        if wd is not None:
            wd.append(dates[-1])
    return wd, we


def weekly_log_returns(weekly_equity: Sequence[float]) -> list[float]:
    """ln(e_t / e_{t-1}) on the weekly series; first element is 0.0. Guards <=0."""
    out = [0.0]
    for i in range(1, len(weekly_equity)):
        a = _safe(weekly_equity[i - 1], 0.0)
        b = _safe(weekly_equity[i], 0.0)
        if a > 0 and b > 0:
            out.append(math.log(b / a))
        else:
            out.append(0.0)
    return out
