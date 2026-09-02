"""
Unit tests for experiments/equity_wave/wave.py — SYNTHETIC arrays only.
NO MySQL, NO MC. Run directly:  python experiments/equity_wave/test_wave.py

Covers:
  1. regime_strength_index: range [0,1], monotonicity per input, NaN/None safety,
     blend asymmetry (one weak input pulls strength down), neutral-when-unknown.
  2. strength_to_aggression: range, neutral=0.5->~1.0, monotone, gamma shapes.
  3. drawdown_from_peak: range, 0 at peak, monotone in collapse, recovery handling.
  4. drawdown_space: range, 1 at dd=0, monotone decreasing, bounded for dd>1/NaN.
  5. WeeklyStateClassifier: flat-curve de-whipsaw (THE cautionary case), hysteresis,
     all four states reachable, sky-vs-recover disambiguation by dd.
  6. EquityNativeScaler: bounded, defensive in DOWN, dd overlay damps within state,
     flat curve => stable scale (no oscillation).
  7. weekly_downsample / weekly_log_returns: shape + correctness on a known curve.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wave import (  # noqa: E402
    StrengthParams, AggressionParams, EquityNativeParams,
    regime_strength_index, strength_to_aggression,
    drawdown_from_peak, drawdown_space,
    WeeklyStateClassifier, EquityNativeScaler,
    weekly_downsample, weekly_log_returns,
    S_FLAT, S_SKY, S_DOWN, S_RECOVER, S_STATES,
)

_FAILS = []
_PASSES = 0


def check(cond, msg):
    global _PASSES
    if cond:
        _PASSES += 1
    else:
        _FAILS.append(msg)
        print(f"  FAIL: {msg}")


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# 1. regime_strength_index
# ---------------------------------------------------------------------------
def test_strength_index():
    print("[1] regime_strength_index")
    P = StrengthParams()

    # all-strong inputs -> high; all-weak -> low; range bound
    strong = regime_strength_index(vix=12, breadth=80, mcclellan=60, trin=0.7,
                                   bdiv=(-0.10, 0.0), semivol_r=0.95, params=P)
    weak = regime_strength_index(vix=35, breadth=25, mcclellan=-60, trin=1.3,
                                 bdiv=(-0.10, 0.0), semivol_r=0.40, params=P)
    check(0.0 <= weak <= strong <= 1.0, f"strong({strong:.3f}) >= weak({weak:.3f}) in [0,1]")
    check(strong > 0.8, f"all-strong strength high (got {strong:.3f})")
    check(weak < 0.25, f"all-weak strength low (got {weak:.3f})")

    # monotone in VIX (lower VIX -> stronger), others fixed
    base = dict(breadth=55, mcclellan=10, trin=1.0, bdiv=(-0.10, 0.0), semivol_r=1.0, params=P)
    s_lowvix = regime_strength_index(vix=12, **base)
    s_midvix = regime_strength_index(vix=22, **base)
    s_hivix = regime_strength_index(vix=32, **base)
    check(s_lowvix >= s_midvix >= s_hivix, f"strength monotone-decreasing in VIX ({s_lowvix:.3f},{s_midvix:.3f},{s_hivix:.3f})")

    # monotone in breadth (higher breadth -> stronger)
    base2 = dict(vix=18, mcclellan=10, trin=1.0, bdiv=(-0.10, 0.0), semivol_r=1.0, params=P)
    check(regime_strength_index(breadth=80, **base2) >= regime_strength_index(breadth=40, **base2) >= regime_strength_index(breadth=20, **base2),
          "strength monotone-increasing in breadth")

    # monotone in McClellan (more positive momentum -> stronger)
    base3 = dict(vix=18, breadth=55, trin=1.0, bdiv=(-0.10, 0.0), semivol_r=1.0, params=P)
    check(regime_strength_index(mcclellan=60, **base3) >= regime_strength_index(mcclellan=0, **base3) >= regime_strength_index(mcclellan=-60, **base3),
          "strength monotone-increasing in McClellan")

    # blend asymmetry: ONE crash input (VIX 40) should crush an otherwise-strong reading
    almost_all_strong = regime_strength_index(vix=12, breadth=80, mcclellan=60, trin=0.7,
                                              bdiv=(-0.10, 0.0), semivol_r=0.95, params=P)
    one_alarm = regime_strength_index(vix=40, breadth=80, mcclellan=60, trin=0.7,
                                      bdiv=(-0.10, 0.0), semivol_r=0.95, params=P)
    # with p<1 the single zero (vix) should drag the index well below the arithmetic mean
    arith_drop = almost_all_strong * (1 - 1.0 / 6)  # rough arithmetic expectation of dropping 1 of ~weighted-6
    check(one_alarm < almost_all_strong, f"one VIX alarm lowers strength ({one_alarm:.3f} < {almost_all_strong:.3f})")
    check(one_alarm < 0.75, f"one VIX alarm pulls strength below 0.75 via sub-geometric blend (got {one_alarm:.3f})")

    # NaN / None safety: never raises, always in [0,1]
    for bad in (None, float('nan'), float('inf'), float('-inf')):
        sv = regime_strength_index(vix=bad, breadth=bad, mcclellan=bad, trin=bad,
                                   bdiv=bad, semivol_r=bad, params=P)
        check(0.0 <= sv <= 1.0, f"all-bad inputs -> strength in [0,1] (got {sv})")
    # all-unknown -> ~neutral 0.5
    su = regime_strength_index(params=P)
    check(approx(su, 0.5, 0.06), f"all-None -> ~neutral 0.5 (got {su:.3f})")

    # bdiv penalty: near-high + breadth rolling over (det10 near gap_c) lowers strength
    no_div = regime_strength_index(vix=15, breadth=70, mcclellan=30, trin=0.8,
                                   bdiv=(-0.30, 0.0), semivol_r=1.0, params=P)  # far below high, no det
    pre_top = regime_strength_index(vix=15, breadth=70, mcclellan=30, trin=0.8,
                                    bdiv=(-0.005, 7.7), semivol_r=1.0, params=P)  # at high, breadth rolling
    check(pre_top < no_div, f"pre-top breadth divergence lowers strength ({pre_top:.3f} < {no_div:.3f})")

    # return_parts shape
    s, parts = regime_strength_index(vix=15, breadth=70, params=P, return_parts=True)
    check(set(parts.keys()) == {'vix', 'brd', 'mcc', 'trin', 'bdiv', 'svr'}, "return_parts has all 6 sub-scores")
    check(all(0.0 <= v <= 1.0 for v in parts.values()), "all sub-scores in [0,1]")


# ---------------------------------------------------------------------------
# 2. strength_to_aggression
# ---------------------------------------------------------------------------
def test_aggression():
    print("[2] strength_to_aggression")
    A = AggressionParams(agg_min=0.5, agg_max=1.5, gamma=1.0)
    check(approx(strength_to_aggression(0.0, A), 0.5), "strength 0 -> agg_min")
    check(approx(strength_to_aggression(1.0, A), 1.5), "strength 1 -> agg_max")
    check(approx(strength_to_aggression(0.5, A), 1.0), "strength 0.5 -> neutral ~1.0")
    # monotone
    seq = [strength_to_aggression(x, A) for x in (0.0, 0.25, 0.5, 0.75, 1.0)]
    check(all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1)), "aggression monotone in strength")
    # bounded for any input incl NaN/None
    for bad in (None, float('nan'), 5.0, -3.0):
        a = strength_to_aggression(bad, A)
        check(A.agg_min <= a <= A.agg_max, f"agg bounded for bad input {bad} (got {a})")
    # gamma>1 convex: 0.75 strength gets LESS than linear (only very-strong boosted)
    Aconv = AggressionParams(agg_min=0.5, agg_max=1.5, gamma=2.0)
    check(strength_to_aggression(0.75, Aconv) < strength_to_aggression(0.75, A),
          "gamma>1 is convex (0.75 gets less boost)")
    check(approx(strength_to_aggression(0.5, Aconv), 1.0, 1e-6), "gamma>1 keeps neutral at 1.0")


# ---------------------------------------------------------------------------
# 3. drawdown_from_peak
# ---------------------------------------------------------------------------
def test_drawdown_from_peak():
    print("[3] drawdown_from_peak")
    eq = [100, 110, 120, 90, 60, 80, 130]
    dd = drawdown_from_peak(eq)
    check(len(dd) == len(eq), "dd length matches")
    check(all(0.0 <= d <= 1.0 for d in dd), "dd in [0,1]")
    check(dd[0] == 0.0 and dd[1] == 0.0 and dd[2] == 0.0, "dd 0 while making new highs")
    check(approx(dd[3], (120 - 90) / 120), "dd correct after first drop")
    check(approx(dd[4], (120 - 60) / 120), "dd deepens monotonically into trough")
    check(dd[4] > dd[3], "dd monotone-increasing as collapse deepens")
    check(dd[5] < dd[4], "dd recovers as equity climbs back")
    check(dd[6] == 0.0, "dd resets to 0 at a new peak")
    # collapse near zero -> dd near 1
    check(drawdown_from_peak([100, 1])[-1] > 0.98, "near-wipe -> dd ~1")


# ---------------------------------------------------------------------------
# 4. drawdown_space transform
# ---------------------------------------------------------------------------
def test_drawdown_space():
    print("[4] drawdown_space")
    check(approx(drawdown_space(0.0), 1.0), "dd=0 -> full aggression 1.0")
    vals = [drawdown_space(d) for d in (0.0, 0.1, 0.25, 0.5, 0.8, 1.0)]
    check(all(0.0 <= v <= 1.0 for v in vals), "drawdown_space in [0,1]")
    check(all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)), "monotone decreasing in dd")
    check(drawdown_space(1.0) < 0.01, "full wipe -> ~0 aggression")
    # bounded for dd>1 / NaN / negative
    for bad in (2.0, -0.5, float('nan'), None):
        v = drawdown_space(bad)
        check(0.0 <= v <= 1.0, f"drawdown_space bounded for {bad} (got {v})")
    # k steepness: larger k damps faster at a fixed dd
    check(drawdown_space(0.12, k=10) < drawdown_space(0.12, k=6), "larger k damps faster")


# ---------------------------------------------------------------------------
# 5. WeeklyStateClassifier — the cautionary case lives here
# ---------------------------------------------------------------------------
def test_classifier():
    print("[5] WeeklyStateClassifier")
    clf = WeeklyStateClassifier(flat_band=0.015, enter=0.04, exit_=0.015, recover_dd=0.05)

    # FLAT curve: tiny weekly noise must NOT flip state (de-whipsaw)
    clf.reset()
    states = [clf.step(r, 0.0) for r in (0.005, -0.004, 0.008, -0.006, 0.003)]
    check(all(s == S_FLAT for s in states), f"flat noisy curve stays FLAT (got {states})")

    # SKY: strong rises at/near highs (dd ~0)
    clf.reset()
    s = clf.step(0.08, 0.0)
    check(s == S_SKY, f"strong rise near highs -> SKY (got {s})")
    # hysteresis: a mild positive week after SKY stays SKY (doesn't drop to FLAT immediately)
    s2 = clf.step(0.02, 0.0)
    check(s2 == S_SKY, f"mild-up week holds SKY via hysteresis (got {s2})")
    # but a tiny (<flat_band) week DOES go flat (dead-band wins)
    s3 = clf.step(0.005, 0.0)
    check(s3 == S_FLAT, f"sub-band week -> FLAT even after SKY (got {s3})")

    # DOWN: strong drop
    clf.reset()
    s = clf.step(-0.08, 0.10)
    check(s == S_DOWN, f"strong drop -> DOWN (got {s})")
    # stays DOWN on a mild-negative continuation (hysteresis)
    check(clf.step(-0.02, 0.12) == S_DOWN, "mild-down continuation holds DOWN")

    # RECOVER: rising again while still below peak (dd > recover_dd)
    clf.reset()
    clf.step(-0.10, 0.15)          # DOWN, deep
    s = clf.step(0.06, 0.10)       # rising but still 10% below peak
    check(s == S_RECOVER, f"rising while below peak -> RECOVER (got {s})")
    # once back near highs (dd small) a strong rise is SKY not RECOVER
    s2 = clf.step(0.06, 0.01)
    check(s2 == S_SKY, f"strong rise near new highs -> SKY (got {s2})")

    # all four states reachable
    seen = set()
    clf.reset()
    seq = [(0.08, 0.0), (0.005, 0.0), (-0.09, 0.12), (0.06, 0.08)]
    for r, d in seq:
        seen.add(clf.step(r, d))
    check(seen == set(S_STATES), f"all four states reachable (got {seen})")

    # THE cautionary case: Jan +51% (SKY) then a drawdown. The classifier must
    # NOT remain SKY through the drop (it would chase the jump into the DD).
    clf.reset()
    clf.step(0.41, 0.0)             # the +51%-ish jump week -> SKY
    drop_state = clf.step(-0.10, 0.10)
    check(drop_state == S_DOWN, f"post-jump drop flips SKY->DOWN, not chased (got {drop_state})")


# ---------------------------------------------------------------------------
# 6. EquityNativeScaler
# ---------------------------------------------------------------------------
def test_equity_native_scaler():
    print("[6] EquityNativeScaler")
    P = EquityNativeParams()
    sc = EquityNativeScaler(P)

    # DOWN protects (scale < 1), with dd overlay deepening the cut
    sc = EquityNativeScaler(P)
    sc.scale(-0.10, 0.20)
    down_scale = sc.scale(-0.02, 0.30)
    check(sc.state == S_DOWN, "scaler in DOWN state")
    check(down_scale < 1.0, f"DOWN scale protects (<1.0, got {down_scale:.3f})")
    check(P.agg_min <= down_scale <= P.agg_max, "scale bounded")

    # dd overlay: deeper dd in the same state damps more. Use shallow dd values so
    # neither hits the agg_min floor (agg_down*exp(-6*dd) stays above 0.40 for dd<~0.067),
    # otherwise the floor clamp masks the overlay (a correct safety bound, but not what
    # this assertion tests).
    a = EquityNativeScaler(P); a.scale(-0.10, 0.02); s_shallow = a.scale(-0.02, 0.02)
    b = EquityNativeScaler(P); b.scale(-0.10, 0.02); s_deep = b.scale(-0.02, 0.06)
    check(s_deep < s_shallow, f"deeper dd damps more within DOWN ({s_deep:.3f} < {s_shallow:.3f})")
    # AND the floor clamp itself holds at very deep dd (safety bound)
    c = EquityNativeScaler(P); c.scale(-0.10, 0.10); s_floor = c.scale(-0.02, 0.50)
    check(approx(s_floor, P.agg_min, 1e-9), f"very deep dd clamps to agg_min floor (got {s_floor:.3f})")

    # FLAT curve => STABLE scale (no oscillation across many flat weeks)
    sc = EquityNativeScaler(P)
    flat_scales = [sc.scale(r, 0.0) for r in (0.004, -0.003, 0.005, -0.002, 0.003, -0.004)]
    check(max(flat_scales) - min(flat_scales) < 1e-9, f"flat curve -> constant scale (range {max(flat_scales)-min(flat_scales):.2e})")
    check(approx(flat_scales[0], P.agg_flat * drawdown_space(0.0, P.dd_k)), "flat scale = agg_flat (dd=0 overlay=1)")

    # SKY does NOT over-boost (cautionary): agg_sky default 1.0 -> no chase
    sc = EquityNativeScaler(P)
    sky_scale = sc.scale(0.10, 0.0)
    check(sc.state == S_SKY and approx(sky_scale, P.agg_sky), f"SKY scale = agg_sky (no chase, got {sky_scale:.3f})")

    # bounded for any input incl NaN
    sc = EquityNativeScaler(P)
    for r, d in [(float('nan'), float('nan')), (10.0, 2.0), (-10.0, -1.0), (None, None)]:
        v = sc.scale(r if r is not None else float('nan'), d if d is not None else float('nan'))
        check(P.agg_min <= v <= P.agg_max, f"scale bounded for ({r},{d}) (got {v})")


# ---------------------------------------------------------------------------
# 7. weekly downsample + log returns
# ---------------------------------------------------------------------------
def test_weekly():
    print("[7] weekly_downsample / weekly_log_returns")
    daily = list(range(1, 21))  # 20 daily bars 1..20
    wd, we = weekly_downsample(None, daily, bars_per_week=5)
    # last of each 5-block: index 4,9,14,19 -> values 5,10,15,20
    check(we == [5, 10, 15, 20], f"weekly downsample picks Fri-close (got {we})")
    # partial final week included
    wd2, we2 = weekly_downsample(None, list(range(1, 23)), bars_per_week=5)  # 22 bars
    check(we2[-1] == 22, f"partial final week's last bar included (got {we2[-1]})")

    # log returns on a known geometric series
    we3 = [100, 110, 99, 99, 150]
    lr = weekly_log_returns(we3)
    check(lr[0] == 0.0, "first weekly log-ret is 0")
    check(approx(lr[1], math.log(110 / 100)), "weekly log-ret correct")
    check(approx(lr[3], 0.0), "flat week -> 0 log-ret")
    # guard non-positive
    check(weekly_log_returns([100, 0, 50]) == [0.0, 0.0, 0.0], "non-positive equity -> 0 log-ret (guarded)")


def main():
    print("=" * 64)
    print("equity_wave/test_wave.py  (synthetic, no MySQL/MC)")
    print("=" * 64)
    test_strength_index()
    test_aggression()
    test_drawdown_from_peak()
    test_drawdown_space()
    test_classifier()
    test_equity_native_scaler()
    test_weekly()
    print("-" * 64)
    print(f"PASSED {_PASSES}  FAILED {len(_FAILS)}")
    if _FAILS:
        print("FAILURES:")
        for f in _FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL GREEN")
    sys.exit(0)


if __name__ == '__main__':
    main()
