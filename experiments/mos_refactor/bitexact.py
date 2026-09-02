"""Bit-exact fixture builder + replay for the compute_overall_score MOS refactor.

compute_overall_score is a PURE function. This harness:
  build  : capture a large, diverse set of REAL (symbol,date) input kwargs via the
           simulator's capture_inputs path (config-independent argument tuples) +
           a directed SYNTHETIC grid that force-hits every gate boundary and every
           currently-OFF mechanism code path. For EACH input row and EACH config
           profile (default-as-shipped + all-mechanisms-forced-ON), compute the
           CURRENT code's (overall, weight_info) and store them.
  replay : re-load the SAME stored inputs, recompute (overall, weight_info) under
           the SAME config profiles with the (refactored) code, and assert byte-
           identical overall AND byte-identical weight_info (key set + values +
           insertion order) for 100% of rows. Reports exact match counts.

Bit-exactness is proven by recomputing fixed inputs under fixed module config and
diffing against the captured baseline. Config profiles are applied via env vars set
IDENTICALLY in build and replay, so OFF-mechanism branches are exercised in both.
"""
import os, sys, json, pickle, importlib

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

INPUTS_PATH = os.path.join(_HERE, "inputs.pkl")          # config-independent arg/kwarg rows
BASELINE_PATH = os.path.join(_HERE, "baseline.pkl")      # {profile: [(overall, weight_info), ...]}

# ── Config profiles ──────────────────────────────────────────────────────────
# Each profile is a dict of env overrides applied (identically) in build + replay.
# "default" = exactly the shipped working-tree config (no overrides).
# "all_on"  = force every post-pre_boost mechanism ON so its apply branch executes,
#             proving the refactor preserves the dormant code paths byte-for-byte.
PROFILES = {
    "default": {},
    "all_on": {
        "PCD_ENABLED": "1",
        "MCD_ENABLED": "1",
        "ICH_ENABLED": "1",
        "WVD_WAVE_ENABLED": "1",
        "EARN_BOOST_ENABLED": "1",
        "SCW_ENABLED": "1",
        "CONT_BOOST_ENABLED": "1",
        "SECTOR_BREADTH_WAVE_ENABLED": "1",
        "DAILY_VOLUME_AUTHORITY_WAVE_ENABLED": "1",
    },
}

_MECH_ENV_KEYS = sorted(
    {k for p in PROFILES.values() for k in p}
    | {"WCF_LIFT_K", "CWCF_DAMPEN_K", "CWWD_DAMPEN_K", "CSWC_DAMPEN_K"}
)


def _apply_env(overrides):
    """Set the profile env (clearing all mechanism keys first), return a restore fn."""
    saved = {k: os.environ.get(k) for k in _MECH_ENV_KEYS}
    for k in _MECH_ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in overrides.items():
        os.environ[k] = v

    def restore():
        for k in _MECH_ENV_KEYS:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]
    return restore


def _fresh_scoring():
    """Re-import scoring so module-level env-driven constants re-read os.environ."""
    import database.utils.scoring as scoring
    importlib.reload(scoring)
    return scoring


# ── Directed synthetic grid: hit every gate boundary + OFF-mechanism path ─────
def _synthetic_rows():
    """Yield (args, kwargs) tuples engineered to exercise specific branches.

    Pure function => synthetic argument tuples are 100% valid for bit-exactness.
    Coverage target: WCF 27/28/33 ramp, call gates 70/75/80/84/85/90/95, put
    gates 16/20/25, capitulation/exhaustion/ext-focal, PCD/MCD/ICH/WVD/PESS/
    EARN_BOOST/SCW/cont-echo/sector/DVAW apply branches, regime + put-regime +
    mis_stress, weekly transition blend.
    """
    rows = []

    def mk(trend, bb, rsi, macd, stoch, ta, **kw):
        rows.append(((trend, bb, rsi, macd, stoch, ta), kw))

    # weekly inputs that drive w_adj into bullish / bearish / weak zones
    weekly_bull = dict(ws_trend=90, ws_rsi=85, ws_macd=80,
                       prev_ws_trend=60, prev_ws_rsi=55, prev_ws_macd=55)
    weekly_bear = dict(ws_trend=15, ws_rsi=18, ws_macd=20,
                       prev_ws_trend=45, prev_ws_rsi=45, prev_ws_macd=45)
    weekly_weak = dict(ws_trend=52, ws_rsi=49, ws_macd=51,
                       prev_ws_trend=50, prev_ws_rsi=50, prev_ws_macd=50)
    weekly_pit = dict(pit_ws_trend=88, pit_ws_rsi=80, pit_ws_macd=78)

    # Full component sweep across the score range (no weekly).
    for tr in range(0, 101, 5):
        for osc in (10, 30, 50, 70, 90):
            mk(tr, osc, osc, osc, osc, osc)

    # Gate-boundary calls with weak/bear weekly (WCF/CWCF/CWWD/CSWC zones).
    for base in (69, 70, 71, 74, 75, 76, 79, 80, 81, 84, 85, 86, 89, 90, 91, 94, 95, 96, 99):
        for stoch in (5, 20, 35, 55, 80):
            mk(base, base, base, base, stoch, base, **weekly_weak,
               raw_rsi=float(base), raw_stoch=float(stoch), macdh_raw=0.3,
               bb_pct=0.7, pct_from_ema50=6.0, pct_from_ema200=20.0)
            mk(base, base, base, base, stoch, base, **weekly_bear,
               raw_rsi=float(base), raw_stoch=float(stoch), macdh_raw=-0.2,
               bb_pct=0.4, pct_from_ema50=-3.0, pct_from_ema200=2.0)

    # WCF score-gate ramp: outputs near 26..34, with bullish-ish weekly (w_adj > cutoff).
    for base in range(14, 36):
        mk(base, base, base, base, base, base, **weekly_bull,
           pct_from_ema50=-1.0, macdh_raw=0.1)
        mk(base, base, base, base, base, base, **weekly_weak)

    # Capitulation: overall==0 + pct_from_ema50 < -10.
    for ext in (-8.0, -11.0, -16.0, -25.0):
        mk(0, 0, 0, 0, 0, 0, pct_from_ema50=ext, macdh_raw=-0.5)
    # Exhaustion: low scores + macdh positive.
    for base in (0, 2, 5, 8, 9, 12):
        for mh in (-0.4, 0.0, 0.3, 1.5):
            mk(base, base, base, base, base, base, macdh_raw=mh)
    # Ext-focal: puts <=25 with price above EMA50.
    for base in (5, 12, 20, 25, 30):
        for ext in (0.5, 3.0, 9.0, 20.0):
            mk(base, base, base, base, base, base, pct_from_ema50=ext)

    # Regime + put-regime + mis_stress.
    for base in (20, 45, 55, 78, 88):
        for rm in (0.70, 0.85, 1.0, 1.05, 1.10):
            mk(base, base, base, base, base, base, regime_multiplier=rm,
               put_regime_multiplier=0.9, mis_stress=0.0)
            mk(base, base, base, base, base, base, regime_multiplier=rm,
               mis_stress=0.6)

    # PCD: puts <=25 with crash sigma.
    for base in (8, 16, 22, 25, 30):
        for r10 in (-0.5, -1.0, -1.5, -2.5):
            mk(base, base, base, base, base, base, ret_10d_sigma=r10)

    # MCD: calls 70..84 across the mcap range.
    for base in (70, 72, 75, 80, 84, 85):
        for mcap in (0.5, 1.0, 5.0, 30.0, 80.0, 500.0):
            mk(base, base, base, base, base, base, mcap_b=mcap, **weekly_bull)

    # ICH: kijun_pct < 0, calls + puts.
    for base in (10, 20, 25, 60, 70, 75, 80, 85, 90, 95):
        for kij in (-1.0, -5.0, -15.0, -30.0):
            mk(base, base, base, base, base, base, kijun_pct=kij,
               raw_rsi=float(base), raw_stoch=float(base),
               bb_pct=0.6, pct_from_ema50=4.0, pct_from_ema200=18.0)

    # WVD: calls with wv_force1 across the inverted-U.
    for base in (70, 75, 80, 85, 90, 95):
        for wvf in (-0.10, 0.0, 0.03, 0.05, 0.12, 0.30):
            mk(base, base, base, base, base, base, wv_force1=wvf)

    # PESS: puts in [16,20] near earnings.
    for base in (14, 16, 18, 20, 22, 25):
        for dte in (1, 3, 5, 7, 8):
            mk(base, base, base, base, base, base, days_to_earnings=dte)

    # EARN_BOOST: calls + puts near earnings, full range.
    for base in (20, 25, 70, 75, 80, 85, 90, 95):
        for dte in (0, 1, 3, 5):
            mk(base, base, base, base, base, base, days_to_earnings=dte,
               **weekly_bull)

    # SCW: calls >= gate with low stoch + weak weekly + raw_* set.
    for base in (70, 72, 75, 80, 85, 90):
        for stoch in (5, 15, 30, 48):
            mk(base, base, base, base, stoch, base, **weekly_weak,
               raw_rsi=float(base), raw_stoch=float(stoch), macdh_raw=0.2,
               bb_pct=0.85, pct_from_ema50=10.0, pct_from_ema200=30.0)

    # Continuation echo: prior_signal set, calls in the cont gate band.
    for base in (55, 60, 68, 72, 74, 76, 80):
        for ps in (0.2, 0.5, 0.8, 1.0):
            mk(base, base, base, base, base, base, prior_signal=ps, **weekly_bull)

    # Sector breadth wave: needs score_date (string ok — adjusted_score parses).
    import datetime as _dt
    for base in (70, 80, 90, 20, 15):
        mk(base, base, base, base, base, base, score_date=_dt.date(2024, 3, 15))

    # DVAW: CONVICTION vol_sig on calls + the _extra inputs.
    for base in (70, 75, 80, 88, 95):
        for mag in (0.1, 0.4, 0.7, 0.95):
            mk(base, base, base, base, base, base, vol_sig='CONVICTION',
               vol_mag=mag, vol_mult=1.4, pct_from_ema50=12.0,
               wv_force1=0.04, price_impulse_sigma=1.2, log_dv_expansion=0.6)

    # Volume blend / amplification paths.
    for base in (30, 50, 70, 90):
        mk(base, base, base, base, base, base, blend_w=0.4, vol_target=20.0,
           vol_mult=1.3, vol_sig='ABSORPTION')
        mk(base, base, base, base, base, base, vol_mult=1.5, vol_sig='CONVICTION',
           pct_from_ema50=25.0)

    # PUT_MACD_GATE + PUT_RSI_ZERO_GATE branches (deeply bearish base).
    for base in (10, 25, 35, 44, 46):
        mk(base, base, 80, base, base, base)  # high RSI but bearish base
        mk(base, base, base, 90, base, base)  # high MACD but bearish base

    # weekly transition blend (pit_* present).
    for base in (40, 60, 80):
        for t in (0.0, 0.2, 0.5, 0.8, 1.0):
            mk(base, base, base, base, base, base, **weekly_bull, **weekly_pit,
               weekly_transition_t=t)

    # None-input early return.
    mk(None, 50, 50, 50, 50, 50)
    mk(50, 50, None, 50, 50, 50)

    return rows


# ── Real input capture via the simulator ─────────────────────────────────────
def _real_rows():
    from simulator import ScoreSimulator
    # Diverse universe: big caps, small caps, high-IV, sector spread, ETFs.
    syms = [
        "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "NFLX",
        "CRM", "JPM", "BAC", "XOM", "CVX", "KO", "PG", "JNJ", "PFE", "WMT",
        "HD", "DIS", "INTC", "MU", "AVGO", "QCOM", "ORCL", "ADBE", "PYPL",
        "SQ", "PLTR", "SOFI", "RIVN", "LCID", "MARA", "RIOT", "COIN", "GME",
        "AMC", "F", "GM", "BA", "CAT", "DE", "GE", "MMM", "T", "VZ", "CMCSA",
        "CSCO", "IBM", "TXN", "NOW", "SNOW", "DDOG", "NET", "CRWD", "ZS",
        "ABNB", "UBER", "LYFT", "SHOP", "ROKU", "DKNG", "UPST", "AFRM",
        "ENPH", "FSLR", "SEDG", "RUN", "PLUG", "BE", "CCJ", "UEC", "SMR",
        "TGT", "COST", "LOW", "NKE", "SBUX", "MCD", "CMG", "DPZ", "GIS",
        "CBRE", "GEHC", "TEM", "STLA", "ELF", "AMSC", "ATI", "NET",
        "LLY", "MRK", "ABBV", "BMY", "GILD", "MRNA", "BNTX", "REGN",
    ]
    sim = ScoreSimulator(symbols=syms, lookback_days=1500)
    sim.capture_inputs = True
    sim.capture_min_overall = 0   # full range incl deep puts + neutral
    sim.simulate()
    return list(sim.inputs_by_key.values())


def build():
    real = _real_rows()
    synth = _synthetic_rows()
    inputs = real + synth
    print("real_rows=%d  synthetic_rows=%d  total=%d" % (len(real), len(synth), len(inputs)))

    with open(INPUTS_PATH, "wb") as f:
        pickle.dump(inputs, f)

    baseline = {}
    for prof, env in PROFILES.items():
        restore = _apply_env(env)
        try:
            scoring = _fresh_scoring()
            out = []
            for args, kw in inputs:
                ov, wi, _vu = scoring.compute_overall_score(*args, **kw)
                # canonicalize weight_info: capture (key, value) pairs in order
                wi_items = list(wi.items()) if wi else []
                out.append((ov, wi_items))
            baseline[prof] = out
        finally:
            restore()
        print("captured profile=%s rows=%d" % (prof, len(baseline[prof])))

    with open(BASELINE_PATH, "wb") as f:
        pickle.dump(baseline, f)
    print("BUILD DONE -> %s , %s" % (INPUTS_PATH, BASELINE_PATH))


def _wi_repr(items):
    """Stable JSON repr of weight_info items preserving order + types."""
    return json.dumps(items, sort_keys=False, default=str)


def replay():
    with open(INPUTS_PATH, "rb") as f:
        inputs = pickle.load(f)
    with open(BASELINE_PATH, "rb") as f:
        baseline = pickle.load(f)

    total_ov_match = total_ov = 0
    total_wi_match = total_wi = 0
    all_pass = True
    for prof, env in PROFILES.items():
        base = baseline[prof]
        restore = _apply_env(env)
        try:
            scoring = _fresh_scoring()
            ov_match = wi_match = 0
            n = len(inputs)
            first_diffs = []
            for i, (args, kw) in enumerate(inputs):
                ov_new, wi_new, _ = scoring.compute_overall_score(*args, **kw)
                wi_new_items = list(wi_new.items()) if wi_new else []
                ov_old, wi_old_items = base[i]
                if ov_new == ov_old:
                    ov_match += 1
                elif len(first_diffs) < 6:
                    first_diffs.append(("OVERALL", i, args, ov_old, ov_new))
                if _wi_repr(wi_new_items) == _wi_repr(wi_old_items):
                    wi_match += 1
                elif len(first_diffs) < 6:
                    first_diffs.append(("WI", i, args, wi_old_items, wi_new_items))
        finally:
            restore()
        print("profile=%s : overall %d/%d identical ; weight_info %d/%d identical"
              % (prof, ov_match, n, wi_match, n))
        for d in first_diffs:
            print("   DIFF", d)
        total_ov_match += ov_match; total_ov += n
        total_wi_match += wi_match; total_wi += n
        if ov_match != n or wi_match != n:
            all_pass = False

    print("=" * 60)
    print("TOTAL overall: %d/%d identical" % (total_ov_match, total_ov))
    print("TOTAL weight_info: %d/%d identical" % (total_wi_match, total_wi))
    print("RESULT:", "PASS — 100% BIT-EXACT" if all_pass else "FAIL — REFACTOR DIVERGED")
    return all_pass


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    if mode == "build":
        build()
    elif mode == "replay":
        ok = replay()
        sys.exit(0 if ok else 1)
    else:
        print("usage: bitexact.py [build|replay]")
        sys.exit(2)
