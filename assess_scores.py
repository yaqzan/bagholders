"""
Score Assessment — vol-adjusted barrier semantics (2026-04-07 rewrite)
======================================================================

The assessment treats every peak as a directional thesis on the underlying:

    HIGH bucket (score >= 70)  →  LONG  side  (momentum/strength: expect rise → call)
    LOW  bucket (score <= 30)  →  SHORT side  (weakness: expect drop → put)

For each peak we run a single forward walk through the prior-realized-volatility-
scaled price path, looking for whichever of two barriers is hit first within
W trading days:

    win  : favorable move of K * sigma  (hit -> 'win')
    stop : adverse move of M * sigma    (hit -> 'stop')
    expire (neither hit by day W)        (-> 'expire')

K, M are per-side and match the OOS-validated cells from entry_filter.py:

    LOW  side (long  calls):   K = 2.0   M = 5.0
    HIGH side (long  puts) :   K = 1.0   M = 2.0

sigma is the prior 60-trading-day stdev of daily simple returns (in % per day).

The same forward walk produces results for every period in PERIODS (1d, 3d, 5d,
7d, 15d, 30d, 60d, 90d) in O(N) — only W (the maximum days held) varies. This replaces
the old options-backtest pipeline.

Stored fields (per period {p}):

    win_rate_{p}            : p_win  — % of peaks where the K*sigma target
                              was hit before the M*sigma stop within W trading days
    swing_p_stop_{p}        : % stopped out
    swing_p_expire_{p}      : % expired (neither barrier hit)
    avg_return_{p}          : mean SIDE-ADJUSTED exit return % (positive = trade
                              direction worked). For HIGH peaks this is -raw_ret;
                              for LOW peaks it's +raw_ret. ≈ EV per trade.
    swing_avg_win_pnl_{p}   : mean exit return on winners (positive %)
    swing_avg_stop_pnl_{p}  : mean exit return on stop-outs (negative %)
    avg_mae_{p}             : mean adverse excursion in side-adjusted % (negative)
    avg_mfe_{p}             : mean favorable excursion in side-adjusted % (positive)
    avg_peak_{p}            : alias of avg_mfe_{p}
    capture_ratio_{p}       : avg_return / avg_mfe (both side-adjusted)

avg_mae_winner_30d / 60d still mean "average MAE on entries that ended in a
winning swing" — the floor your stop-loss must sit below to avoid cutting
winners.

Shakeout depth/recovery still compare win_rate_7d to win_rate_60d, but now
those win rates are barrier-touch p_win and not sign-of-return rates.
"""
from database.models.core import (
    Score, ScoreAssessmentRun, ScoreAssessmentResult, ScoreAssessmentMeta,
    ScoreAssessmentBandIC, AlgorithmVersion, MarketRegime,
)
from database.models.technical import PriceHistory
from database.models.options import Option, OptionPrice
from database.utils.trading_calendar import trading_days_between
from datetime import datetime, date, timedelta
from colorama import Fore, Style
from collections import defaultdict
from statistics import median
from pathlib import Path
import csv
import math
import sys
import json
import numpy as np

for _stream_name in ('stdout', 'stderr'):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

DEFAULT_LOOKBACK = 365

# Per-side (K, M) reference values at 30 trading days — OOS-validated 2026-04-06.
# All other periods scale by sqrt(W / SWING_REFERENCE_DAYS), anchored here.
SWING_K_HIGH = 1.0   # PUT  side (low score):  target = 1*sigma drop  @ 30d
SWING_M_HIGH = 2.0   # PUT  side (low score):  stop   = 2*sigma rise  @ 30d
SWING_K_LOW  = 2.0   # CALL side (high score): target = 2*sigma rise  @ 30d
SWING_M_LOW  = 5.0   # CALL side (high score): stop   = 5*sigma drop  @ 30d
SWING_REFERENCE_DAYS = 30   # anchor period for K/M; other periods scale by sqrt(W/30)
SWING_VOL_LOOKBACK   = 60   # trading days of history for sigma

# Option P&L context — set by set_dte_strategy() when metric='tp'/'tp26'; 0/0.0 = disabled.
# Used by _compute_option_pnl() to apply theta + bimodal fill to TP% period results.
# OPTION_TOTAL_DTE : calendar DTE of the option at entry (30 or 15)
# OPTION_HOLD_DAYS : max trading bars the strategy holds (HOLD_DAYS from strategy_config)
# OPTION_PREM_MULT : PREMIUM_MULT from strategy_config (1.82 for 30 DTE, 1.29 for 15 DTE)
OPTION_TOTAL_DTE  = 0
OPTION_HOLD_DAYS  = 0
OPTION_PREM_MULT  = 0.0

# DTE strategy presets — used by `--dte 15` mode (Phase 16).
# 30 DTE: legacy generic barrier-touch (K=2.0/5.0 calls, K=1.0/2.0 puts at W=30 cal days).
# 15 DTE: option-aligned barriers from monte_carlo_15dte.py C1 ship.
#   - Premium ~ 1.29 * sigma_daily, delta=0.5
#   - Call TP=+35% premium -> 0.903σ underlying rise (K)
#   - Call SL=-30% premium -> 0.774σ underlying drop (M)
#   - Put  TP=+35% premium -> 0.903σ underlying drop (K)
#   - Put  SL=-20% premium -> 0.516σ underlying rise (M)
#   - Reference window = 15 cal days (option expiry / end hold). At W=15 the WR is
#     unscaled. Other periods (W=7, W=30) scale by sqrt(W/15).
DTE_STRATEGY_PRESETS = {
    '30': {  # Legacy generic barrier-touch (existing behavior)
        'k_high': 1.0,  'm_high': 2.0,
        'k_low':  2.0,  'm_low':  5.0,
        'reference_days': 30,
    },
    '15': {  # 15 DTE option-aligned (from monte_carlo_15dte.py C1)
        'k_high': 0.903, 'm_high': 0.516,   # PUT
        'k_low':  0.903, 'm_low':  0.774,   # CALL
        'reference_days': 15,
    },
}

# Phase 17 (2026-04-29): metric-aware presets. Splits assessment into two
# semantically distinct measurements per DTE:
#
#   metric='wr'  → DIRECTIONAL accuracy (generic barriers, near-irrelevant stops)
#                  same K/M for every DTE — directional accuracy is DTE-agnostic.
#                  Anchored at 30d ref so per-period magnitudes match the legacy
#                  Win Rates tab data.
#   metric='tp'  → OPTION TAKE-PROFIT rate (option-aligned barriers from the
#                  shipped strategy params per DTE — TP fires before SL).
#                  Per-DTE because option premiums and TPs differ.
#
# Both 30 DTE and 15 DTE WR runs use the SAME barriers (generic 30dte ref) so
# the WR tab is internally consistent across the DTE toggle. Only the TP% tab's
# data actually changes when toggling DTE.
#
# metric='tp26' (2026-08-10, dual-anchor reanchor — experiments/assess_reanchor_2026_08/):
#   a SECOND option-TP% anchor, 30-DTE only, at the LIVE 2026-08-10 TP/SL retune
#   canon (calls TP+10%/SL-100%; puts unchanged — not retuned, still off
#   portfolio-wide). 'tp' is deliberately kept byte-identical at its original
#   Phase-H5 anchors because it feeds the W1-W6 ship gates and ~40 closed-axis
#   verdicts cite it at those numbers — 'tp26' is additive, not a replacement.
DTE_METRIC_PRESETS = {
    ('30', 'wr'): {  # 30 DTE directional WR — generic barrier-touch (legacy)
        'k_high': 1.0,  'm_high': 2.0,
        'k_low':  2.0,  'm_low':  5.0,
        'reference_days': 30,
    },
    ('30', 'tp'): {  # 30 DTE option TP% — Phase H5_HOLD15_H40 barriers (BASE regime)
        'k_high': 1.274, 'm_high': 0.728,   # PUT TP=0.35 / SL=-0.20
        'k_low':  1.274, 'm_low':  1.092,   # CALL TP_BASE=0.35 / SL_BASE=-0.30
        'reference_days': 30,
    },
    ('30', 'tp26'): {  # 30 DTE option TP% — 2026-08-10 TP/SL retune canon (Core/Apex)
        # PUT TP=0.35 / SL=-0.20 — UNCHANGED by the retune (puts stay off portfolio-wide,
        # not retuned); identical to ('30','tp')'s put side by construction, not by coincidence.
        'k_high': 1.274, 'm_high': 0.728,
        # CALL TP_BASE=0.10 / SL_BASE=-1.00 ("scalp-and-dead-hold" — SL-100 is a ~3.64sigma
        # disaster stop, not "SL off"; deep fires mostly reroute to dead-hold).
        'k_low':  0.364, 'm_low':  3.64,
        'reference_days': 30,
    },
    ('15', 'wr'): {  # 15 DTE directional WR — uses SAME generic barriers as 30 DTE WR
        'k_high': 1.0,  'm_high': 2.0,
        'k_low':  2.0,  'm_low':  5.0,
        'reference_days': 30,
    },
    ('15', 'tp'): {  # 15 DTE option TP% — Phase 15B C1 barriers
        'k_high': 0.903, 'm_high': 0.516,   # PUT TP=0.35 / SL=-0.20
        'k_low':  0.903, 'm_low':  0.774,   # CALL TP=0.35 / SL=-0.30
        'reference_days': 15,
    },
}


def set_dte_strategy(dte: str = '30', metric: str = 'wr'):
    """Mutate module-level SWING constants to the given (DTE, metric) preset.
    Returns previous values for restore. Used by `--dte` and `--metric` flags.

    metric='wr' selects generic directional barriers (DTE-agnostic);
    metric='tp' selects option-aligned barriers per DTE (Phase 17, 2026-04-29).
    metric='tp26' selects the 2026-08-10 TP/SL retune's option-aligned barriers,
    30 DTE only (dual-anchor reanchor — 'tp' stays byte-identical for comparability).
    When metric is any option-aligned variant ('tp' or 'tp26'), also sets
    OPTION_TOTAL_DTE / OPTION_HOLD_DAYS / OPTION_PREM_MULT so that
    _compute_option_pnl() applies the correct theta model per-DTE.
    """
    global SWING_K_HIGH, SWING_M_HIGH, SWING_K_LOW, SWING_M_LOW
    global SWING_REFERENCE_DAYS, RANDOM_WIN_RATE_CALL, RANDOM_WIN_RATE_PUT
    global OPTION_TOTAL_DTE, OPTION_HOLD_DAYS, OPTION_PREM_MULT
    prev = (SWING_K_HIGH, SWING_M_HIGH, SWING_K_LOW, SWING_M_LOW, SWING_REFERENCE_DAYS)
    key = (str(dte), str(metric))
    p = DTE_METRIC_PRESETS.get(key) or DTE_STRATEGY_PRESETS.get(str(dte)) or DTE_STRATEGY_PRESETS['30']
    SWING_K_HIGH = p['k_high']
    SWING_M_HIGH = p['m_high']
    SWING_K_LOW  = p['k_low']
    SWING_M_LOW  = p['m_low']
    SWING_REFERENCE_DAYS = p['reference_days']
    RANDOM_WIN_RATE_CALL = SWING_M_LOW  / (SWING_K_LOW  + SWING_M_LOW)  * 100
    RANDOM_WIN_RATE_PUT  = SWING_M_HIGH / (SWING_K_HIGH + SWING_M_HIGH) * 100
    # Option P&L context — active for any option-aligned metric ('tp', 'tp26').
    # NOT a bare == 'tp' check: a new option-aligned variant that fails this
    # membership test silently loses avg_option_pnl (no crash, just null fields —
    # see experiments/assess_reanchor_2026_08/PREREG.md "Traps caught in recon").
    if str(metric) in ('tp', 'tp26'):
        try:
            from strategy_config import STRATEGY_30DTE, STRATEGY_15DTE
            cfg = STRATEGY_15DTE if str(dte) == '15' else STRATEGY_30DTE
            OPTION_TOTAL_DTE = int(dte)
            OPTION_HOLD_DAYS = cfg.HOLD_DAYS
            OPTION_PREM_MULT = cfg.PREMIUM_MULT
        except Exception:
            OPTION_TOTAL_DTE = 0
            OPTION_HOLD_DAYS = 0
            OPTION_PREM_MULT = 0.0
    else:
        OPTION_TOTAL_DTE = 0
        OPTION_HOLD_DAYS = 0
        OPTION_PREM_MULT = 0.0
    return prev


def parse_lookback_arg(arg):
    """Parse lookback: bare int = days; suffix d/w/y = calendar-style length. Returns None if not a lookback token."""
    if not arg:
        return None
    s = arg.strip().lower()
    if s.isdigit():
        return int(s)
    if len(s) >= 2 and s[:-1].isdigit():
        n = int(s[:-1])
        suf = s[-1]
        if suf == 'd':
            return n
        if suf == 'w':
            return n * 7
        if suf == 'y':
            return n * 365
    return None
BUY_THRESHOLDS = [95, 90, 85, 80, 75, 70]
SELL_THRESHOLDS = [30, 25, 20, 15, 10, 5]
THRESHOLD_KEYS = [f'{t}+' for t in BUY_THRESHOLDS] + [f'<{t}' for t in SELL_THRESHOLDS]
# PERIODS are in CALENDAR days (matches DTE semantics on options). Each period
# uses the side's K/M cell with W = calendar days forward from the peak date.
# The 1d period is a special case: it always evaluates the next trading bar's
# direction, regardless of calendar gaps (weekends/holidays).
# Barrier scaling keeps sqrt(W/30) so per-label barrier magnitudes are preserved
# vs. the prior trading-bar regime (30d label still has K*sigma magnitude).
PERIODS = [('1d', 1), ('3d', 3), ('5d', 5), ('7d', 7), ('15d', 15), ('30d', 30), ('60d', 60), ('90d', 90)]
PERIOD_LABELS = [p[0] for p in PERIODS]
PERIOD_MAX_W = max(d for _, d in PERIODS)
# Max trading bars to load forward: 90 cal days worst-case ≈ 65 bars; allow headroom.
PERIOD_MAX_BARS = PERIOD_MAX_W  # 90 bars is an inclusive upper bound for 90 cal days
# Random-walk win rate baselines per side — gambler's ruin: P(hit target) = M/(K+M).
# Each side has different barrier ratios so the floor is different.
RANDOM_WIN_RATE_CALL = SWING_M_LOW  / (SWING_K_LOW  + SWING_M_LOW)  * 100  # 5/7 ≈ 71.43%
RANDOM_WIN_RATE_PUT  = SWING_M_HIGH / (SWING_K_HIGH + SWING_M_HIGH) * 100  # 2/3 ≈ 66.67%
# Periods that skip the sigma barrier and use close direction only.
# 1d is too short for a vol-scaled barrier to be meaningful — a 1-day hold
# simply wins if the close moves favorably and loses otherwise.
SIMPLE_DIRECTION_PERIODS = {'1d'}
WINDOWS = [('1y', 365), ('2y', 730), ('3y', 1095), ('5y', 1825), ('10y', 3650),
           # Deep windows (2026-07-05): usable now that v74 spans 1995->present
           # incl dot-com + GFC. Only applied when the assess lookback reaches them
           # (applicable_windows filters), so short/default runs are unaffected.
           ('15y', 5475), ('20y', 7300), ('30y', 10950)]


def applicable_windows(lookback_days):
    """Only windows that fit inside the assessment lookback (e.g. 1y run does not show 25y tables)."""
    return [(label, days) for label, days in WINDOWS if days <= lookback_days]


def get_git_commit():
    from database.project_root import get_trader_project_root, trader_git_output
    try:
        root = get_trader_project_root()
        version_file = root / "ALGORITHM_VERSION"
        if version_file.exists():
            return version_file.read_text().strip()
        if not (root / ".git").exists():
            return None
        return trader_git_output(["rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return None


def get_git_message(commit_hash):
    from database.project_root import trader_git_output
    try:
        return trader_git_output(["log", "-1", "--format=%s", commit_hash]).decode().strip()
    except Exception:
        return None


def get_or_create_version():
    return AlgorithmVersion.get_or_create_current()


def resolve_version_arg(arg):
    """Resolve 'v3', a commit hash, or None to a git_commit string."""
    if arg is None:
        return get_git_commit()
    if arg.lower().startswith('v') and arg[1:].isdigit():
        version = AlgorithmVersion.get_or_none(AlgorithmVersion.id == int(arg[1:]))
        if not version:
            print(f"{Fore.RED}Version {arg} not found.{Style.RESET_ALL}")
            return None
        return version.git_commit
    return arg


def resolve_algorithm_version(arg):
    """Resolve assess `--version` token to an AlgorithmVersion row.
    `v3` / `V3` resolves by production label; bare digits keep legacy DB-id
    behavior; `db:3` forces DB id; else `git_commit` exact or unique prefix.
    Legacy inactive staging rows are not valid assessment/revert targets.
    """
    if arg is None:
        return None
    s = str(arg).strip()
    if not s:
        return None
    if s.lower().startswith('db:') and s[3:].isdigit():
        v = AlgorithmVersion.get_or_none(AlgorithmVersion.id == int(s[3:]))
    elif s.lower().startswith('v') and len(s) > 1 and s[1:].isdigit():
        v = AlgorithmVersion.get_by_production_label(int(s[1:]))
    elif s.isdigit():
        v = AlgorithmVersion.get_or_none(AlgorithmVersion.id == int(s))
    else:
        v = AlgorithmVersion.get_or_none(AlgorithmVersion.git_commit == s)
        if not v:
            matches = list(
                AlgorithmVersion.select()
                .where(AlgorithmVersion.git_commit.startswith(s))
                .order_by(AlgorithmVersion.id.desc())
            )
            if len(matches) == 1:
                v = matches[0]
            elif len(matches) > 1:
                ids = ', '.join(f'{m.production_label} (db:{m.id})' for m in matches[:8])
                print(f"{Fore.RED}Prefix {s!r} matches multiple algorithm versions: {ids}{Style.RESET_ALL}")
                return None
    if not v:
        print(f"{Fore.RED}Algorithm version not found: {s!r}{Style.RESET_ALL}")
        return None
    if AlgorithmVersion.is_legacy_staging_commit(v.git_commit):
        print(
            f"{Fore.RED}Algorithm version {s!r} is a legacy inactive staging row "
            f"(db:{v.id}); use `trader staging migrate-legacy` instead.{Style.RESET_ALL}"
        )
        return None
    return v


def _build_regime_cache(cutoff):
    """Load MarketRegime rows into a {date: multiplier} dict for regime-adjust mode."""
    regimes = list(MarketRegime.select(MarketRegime.date, MarketRegime.regime_multiplier)
                   .where(MarketRegime.date >= cutoff, MarketRegime.regime_multiplier.is_null(False)))
    return {r.date: float(r.regime_multiplier) for r in regimes}


def _regime_adjust_score(overall, multiplier):
    """Apply regime multiplier with sell-signal inversion, matching market_regime.apply_regime_to_score."""
    if multiplier is None or multiplier == 1.0:
        return overall
    if overall >= 50:
        adjusted = 50 + (overall - 50) * multiplier
    else:
        adjusted = 50 + (overall - 50) * (2.0 - multiplier)
    return int(max(0, min(100, round(adjusted))))


def extract_peaks(symbol=None, lookback_days=DEFAULT_LOOKBACK, version=None, regime_adjust=False):
    cutoff = date.today() - timedelta(days=lookback_days)
    regime_cache = _build_regime_cache(cutoff) if regime_adjust else {}

    query = Score.select().where(
        Score.date >= cutoff,
        Score.overall.is_null(False),
    )
    if version:
        query = query.where(Score.version == version)
    if symbol:
        query = query.where(Score.symbol == symbol)
    if not regime_adjust:
        query = query.where((Score.overall >= 70) | (Score.overall <= 30))

    scores = list(query.order_by(Score.symbol, Score.date))

    # Apply regime adjustment on-the-fly (does not mutate DB)
    if regime_adjust:
        for s in scores:
            mult = regime_cache.get(s.date)
            if mult is not None:
                s.overall = _regime_adjust_score(s.overall, mult)

    # Filter to extremes after regime adjustment. In the normal path this is
    # already pushed into SQL to avoid loading neutral scores from long windows.
    scores = [s for s in scores if s.overall >= 70 or s.overall <= 30]

    by_symbol = defaultdict(list)
    for s in scores:
        by_symbol[s.symbol_id].append(s)

    pruned = set()
    all_scores = []
    for sym, sym_scores in by_symbol.items():
        date_map = {s.date: s for s in sym_scores}
        ranked = sorted(sym_scores, key=lambda s: abs(s.overall - 50), reverse=True)
        for s in ranked:
            if (sym, s.date) in pruned:
                continue
            all_scores.append(s)
            for direction in (-1, 1):
                walk = s.date + timedelta(days=direction)
                while walk in date_map and (sym, walk) not in pruned:
                    pruned.add((sym, walk))
                    walk += timedelta(days=direction)

    return all_scores


def _diagnose_assess_zero_peaks(symbol, lookback_days, version):
    """Explain why assess found no peaks (version mismatch vs no extremes vs no rows)."""
    cutoff = date.today() - timedelta(days=lookback_days)

    def _extreme_query(with_version):
        q = Score.select().where(
            Score.date >= cutoff,
            Score.overall.is_null(False),
            (Score.overall >= 70) | (Score.overall <= 30),
        )
        if with_version is not None:
            q = q.where(Score.version == with_version)
        if symbol:
            q = q.where(Score.symbol == symbol)
        return q

    n_extreme_any = _extreme_query(None).count()
    n_extreme_current = _extreme_query(version).count()
    q_rows = Score.select().where(Score.date >= cutoff, Score.version == version)
    if symbol:
        q_rows = q_rows.where(Score.symbol == symbol)
    n_rows_current = q_rows.count()

    if n_extreme_any > 0 and n_extreme_current == 0:
        print(
            f"{Fore.YELLOW}Found {n_extreme_any} extreme score(s) in the window for other algorithm "
            f"versions, but none for current HEAD ({version.git_commit}). "
            f"Assessment only uses scores tied to the current commit. "
            f"Run `trader update` (or your recalculate flow) to refresh scores for this checkout.{Style.RESET_ALL}"
        )
    elif n_rows_current == 0:
        print(
            f"{Fore.YELLOW}No score rows for algorithm version {version.git_commit} in the last "
            f"{lookback_days} days. Run `trader update` so scores exist for the current commit.{Style.RESET_ALL}"
        )
    elif n_extreme_current == 0:
        print(
            f"{Fore.YELLOW}Scores exist for this version, but none in the 70+ or ≤25 bands in the lookback "
            f"(all mid-range).{Style.RESET_ALL}"
        )


def _peak_side(score):
    """HIGH score (>=50) trades LONG (call: expect rise). LOW score trades SHORT (put: expect drop).
    'low' side: win = price rises K*sigma (SWING_K_LOW=2.0), stop = drops M*sigma (SWING_M_LOW=5.0)
    'high' side: win = price drops K*sigma (SWING_K_HIGH=1.0), stop = rises M*sigma (SWING_M_HIGH=2.0)
    """
    return 'low' if score >= 50 else 'high'


def _realized_vol_pct(closes, base_idx, lookback=SWING_VOL_LOOKBACK):
    """Daily realized vol — stdev of simple returns over the prior `lookback`
    closes ending at base_idx — expressed as percent per day. Returns None if
    insufficient history."""
    if base_idx < lookback:
        return None
    rets = []
    for j in range(base_idx - lookback + 1, base_idx + 1):
        prev = closes[j - 1]
        if prev <= 0:
            continue
        rets.append((closes[j] - prev) / prev)
    if len(rets) < max(5, lookback // 2):
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * 100


def _swing_walk(closes, base_idx, side, vol_pct, highs=None, lows=None, dates=None, opens=None):
    """Single forward pass through closes from base_idx+1.

    Periods are now CALENDAR days. The walk iterates over trading rows but each
    period "expires" when the current bar's date exceeds base_date + W cal days.
    The 1d period remains bar-based (next trading bar direction).

    Barrier targets scale with sqrt(W / SWING_REFERENCE_DAYS) — keeping the same
    numerical magnitudes per label as the prior trading-bar regime so pre/post
    conversion assessment rows are directly comparable for calibration.

    When highs/lows are provided, barrier detection uses intraday extremes:
      - HIGH (put) side: win = daily low <= target_win; stop = daily high >= target_stop
      - LOW (call) side: win = daily high >= target_win; stop = daily low <= target_stop
    When both trigger on the same bar, the close breaks the tie.
    MFE/MAE excursions use daily highs/lows when available.

    `dates` (required for non-1d periods): parallel list of date objects.
    """
    if vol_pct is None or vol_pct <= 0:
        return None
    entry = closes[base_idx]
    if entry <= 0:
        return None
    if dates is None:
        return None
    base_date = dates[base_idx]

    use_intraday = highs is not None and lows is not None
    K_base = SWING_K_HIGH if side == 'high' else SWING_K_LOW
    M_base = SWING_M_HIGH if side == 'high' else SWING_M_LOW

    # Load enough trading rows to cover the largest calendar window.
    end = min(len(closes), base_idx + 1 + PERIOD_MAX_BARS)
    fwd_closes = closes[base_idx + 1:end]
    n_fwd = len(fwd_closes)
    if n_fwd < 1:
        return None

    fwd_dates = dates[base_idx + 1:end]
    fwd_highs = highs[base_idx + 1:end] if use_intraday else None
    fwd_lows  = lows[base_idx + 1:end]  if use_intraday else None
    fwd_opens = opens[base_idx + 1:end] if opens is not None else None

    # Per-period availability: does forward history reach the calendar cutoff?
    # If not, period is dropped (same semantic as "insufficient forward bars" before).
    def _period_reachable(W_cal):
        if n_fwd < 1:
            return False
        # Strict >: we need at least one bar past the cutoff so the expire branch
        # will fire. If the last loaded bar lands exactly on cutoff, we can't
        # conclude "expired" — treat as insufficient data (same rule as the
        # prior bar-based `W > n_fwd` guard).
        cutoff = base_date + timedelta(days=W_cal)
        return fwd_dates[-1] > cutoff

    # Build per-period state with scaled targets.
    # Simple-direction periods skip barriers entirely.
    period_state = {}
    for label, W in PERIODS:
        if label == '1d':
            if n_fwd < 1:
                continue
        elif not _period_reachable(W):
            continue
        simple = label in SIMPLE_DIRECTION_PERIODS
        if simple:
            t_win = t_stop = None
            t_win_u = t_stop_u = None
        else:
            scale = math.sqrt(W / SWING_REFERENCE_DAYS)
            k = K_base * scale
            m = M_base * scale
            if side == 'high':
                t_win  = entry * (1 - k * vol_pct / 100)
                t_stop = entry * (1 + m * vol_pct / 100)
                t_win_u  = entry * (1 - K_base * vol_pct / 100)
                t_stop_u = entry * (1 + M_base * vol_pct / 100)
            else:
                t_win  = entry * (1 + k * vol_pct / 100)
                t_stop = entry * (1 - m * vol_pct / 100)
                t_win_u  = entry * (1 + K_base * vol_pct / 100)
                t_stop_u = entry * (1 - M_base * vol_pct / 100)
        period_state[label] = {
            'W':                  W,
            'cal_cutoff':         None if simple else base_date + timedelta(days=W),
            'result':             None,
            'exit_day':           None,
            'exit_close':         None,
            'running_max':        entry,
            'running_min':        entry,
            # Last bar INSIDE the calendar window — the expire mark-out point.
            # (D4 fix 2026-08-10: mirrors barrier_cache._walk_outcome's
            # `last_close`/`bar_count`, which stop at the cutoff. Marking out on
            # the first bar PAST the cutoff priced a hard sell one bar beyond the
            # window being measured.)
            'last_in_close':      None,
            'last_in_j':          None,
            'done':               False,
            'simple':             simple,
            'target_win':         t_win,
            'target_stop':        t_stop,
            'result_unscaled':    None,
            'done_unscaled':      False,
            'target_win_u':       t_win_u,
            'target_stop_u':      t_stop_u,
            # option P&L fire fields
            'fire_type':          None,  # 0=expire, 1=tp, 2=sl
            'fire_open':          None,
            'fire_high':          None,
            'fire_low':           None,
        }
    if not period_state:
        return None

    for j, c in enumerate(fwd_closes, start=1):
        idx = j - 1
        bar_date = fwd_dates[idx]
        bar_max = fwd_highs[idx] if use_intraday else c
        bar_min = fwd_lows[idx]  if use_intraday else c
        if use_intraday:
            bar_high = fwd_highs[idx]
            bar_low  = fwd_lows[idx]
        bar_open = fwd_opens[idx] if fwd_opens is not None else None

        for st in period_state.values():
            if st['done'] and st['done_unscaled']:
                continue

            # In-window (by calendar cutoff) test — simple periods are always in-window
            in_window = st['simple'] or bar_date <= st['cal_cutoff']

            # Running max/min reflect in-window excursion only (MFE/MAE)
            if not st['done'] and in_window:
                if bar_max > st['running_max']:
                    st['running_max'] = bar_max
                if bar_min < st['running_min']:
                    st['running_min'] = bar_min
                # Remember the last in-window bar so an expire marks out THERE
                # rather than one bar past the cutoff (D4).
                st['last_in_close'] = c
                st['last_in_j']     = j

            # Scaled barrier check
            if not st['done']:
                if st['simple']:
                    # Direction-only: win = favorable close, stop = adverse close, expire = flat
                    if j >= st['W']:
                        if side == 'high':
                            result = 'win' if c < entry else ('stop' if c > entry else 'expire')
                        else:
                            result = 'win' if c > entry else ('stop' if c < entry else 'expire')
                        st['result']     = result
                        st['exit_day']   = j
                        st['exit_close'] = c
                        st['done']       = True
                        st['fire_type']  = 1 if result == 'win' else (2 if result == 'stop' else 0)
                        st['fire_open']  = bar_open
                        st['fire_high']  = bar_high if use_intraday else c
                        st['fire_low']   = bar_low  if use_intraday else c
                elif not in_window:
                    # First bar past the calendar cutoff CONFIRMS the expire, but
                    # the hard sell prices at the last bar INSIDE the window —
                    # same semantics as barrier_cache._walk_outcome (:260-269),
                    # which marks out at `last_close` / `bar_count`. (D4)
                    st['done'] = True
                    if st['last_in_close'] is None:
                        # No in-window bar at all (a calendar gap swallowed the
                        # whole window) — nothing to mark out against. Leave
                        # result None; the output loop drops the period, which is
                        # what the cache does too (`last_close is None -> _null`).
                        pass
                    else:
                        st['result']     = 'expire'
                        st['exit_day']   = st['last_in_j']
                        st['exit_close'] = st['last_in_close']
                        st['fire_type']  = 0
                        st['fire_open']  = None  # expire: no intraday fire bar
                        st['fire_high']  = None
                        st['fire_low']   = None
                else:
                    t_win  = st['target_win']
                    t_stop = st['target_stop']
                    # Per-period barrier check (intraday when available)
                    if use_intraday:
                        if side == 'high':
                            is_win  = bar_low  <= t_win
                            is_stop = bar_high >= t_stop
                        else:
                            is_win  = bar_high >= t_win
                            is_stop = bar_low  <= t_stop
                    else:
                        if side == 'high':
                            is_win  = c <= t_win
                            is_stop = c >= t_stop
                        else:
                            is_win  = c >= t_win
                            is_stop = c <= t_stop
                    # Tie-break: both hit intraday → use close direction
                    if is_win and is_stop:
                        is_win  = (c < entry) if side == 'high' else (c > entry)
                        is_stop = not is_win

                    if is_win:
                        st['result']     = 'win'
                        st['exit_day']   = j
                        st['exit_close'] = t_win if use_intraday else c
                        st['done']       = True
                        st['fire_type']  = 1
                        st['fire_open']  = bar_open
                        st['fire_high']  = bar_high if use_intraday else c
                        st['fire_low']   = bar_low  if use_intraday else c
                    elif is_stop:
                        st['result']     = 'stop'
                        st['exit_day']   = j
                        st['exit_close'] = t_stop if use_intraday else c
                        st['done']       = True
                        st['fire_type']  = 2
                        st['fire_open']  = bar_open
                        st['fire_high']  = bar_high if use_intraday else c
                        st['fire_low']   = bar_low  if use_intraday else c

            # Unscaled barrier check — runs independently; same W window, fixed K/M at scale=1.0
            if not st['done_unscaled']:
                if st['simple']:
                    if j >= st['W']:
                        # Direction-only: identical to scaled result
                        if side == 'high':
                            st['result_unscaled'] = 'win' if c < entry else ('stop' if c > entry else 'expire')
                        else:
                            st['result_unscaled'] = 'win' if c > entry else ('stop' if c < entry else 'expire')
                        st['done_unscaled'] = True
                elif not in_window:
                    st['result_unscaled'] = 'expire'
                    st['done_unscaled'] = True
                else:
                    t_win_u  = st['target_win_u']
                    t_stop_u = st['target_stop_u']
                    if use_intraday:
                        if side == 'high':
                            is_win_u  = bar_low  <= t_win_u
                            is_stop_u = bar_high >= t_stop_u
                        else:
                            is_win_u  = bar_high >= t_win_u
                            is_stop_u = bar_low  <= t_stop_u
                    else:
                        if side == 'high':
                            is_win_u  = c <= t_win_u
                            is_stop_u = c >= t_stop_u
                        else:
                            is_win_u  = c >= t_win_u
                            is_stop_u = c <= t_stop_u
                    if is_win_u and is_stop_u:
                        is_win_u  = (c < entry) if side == 'high' else (c > entry)
                        is_stop_u = not is_win_u
                    if is_win_u:
                        st['result_unscaled'] = 'win'
                        st['done_unscaled'] = True
                    elif is_stop_u:
                        st['result_unscaled'] = 'stop'
                        st['done_unscaled'] = True
                    elif j >= st['W']:
                        st['result_unscaled'] = 'expire'
                        st['done_unscaled'] = True

    out = {}
    for label, st in period_state.items():
        if not st['done']:
            continue  # safety; shouldn't happen since j reaches W
        if st['result'] is None:
            continue  # expire with no in-window bar to mark out against (D4)
        raw_ret = (st['exit_close'] - entry) / entry * 100
        if side == 'low':
            exit_ret = raw_ret
            mfe_pct = (st['running_max'] - entry) / entry * 100   # rise = favorable
            mae_pct = (st['running_min'] - entry) / entry * 100   # drop = adverse (negative)
        else:
            exit_ret = -raw_ret
            mfe_pct = -(st['running_min'] - entry) / entry * 100  # drop becomes positive favorable
            mae_pct = -(st['running_max'] - entry) / entry * 100  # rise becomes negative adverse
        # Sigma-normalized MAE (negative; units of σ per-day) — lets us average
        # MAE across stocks with different volatilities for a comparable bucket
        # floor, then de-normalize at display time using the target stock's σ.
        mae_sigma = mae_pct / vol_pct if vol_pct else None
        mfe_sigma = mfe_pct / vol_pct if vol_pct else None
        out[label] = {
            'result':          st['result'],
            'result_unscaled': st.get('result_unscaled'),
            'exit_day':        st['exit_day'],
            'exit_close':      st['exit_close'],
            'exit_ret':        round(exit_ret, 2),
            'mfe':             round(mfe_pct, 2),
            'mae':             round(mae_pct, 2),
            'mae_sigma':       round(mae_sigma, 3) if mae_sigma is not None else None,
            'mfe_sigma':       round(mfe_sigma, 3) if mfe_sigma is not None else None,
            # option P&L fire fields
            'exit_bars':       st['exit_day'],   # j from enumerate = trading bars from base
            'fire_type':       st.get('fire_type'),
            'fire_open':       st.get('fire_open'),
            'fire_high':       st.get('fire_high'),
            'fire_low':        st.get('fire_low'),
        }
    return out


def _peak_to_result(peak, rows):
    """Convert one peak + sorted rows of (date, close, high, low[, open]) into
    the result dict shape, populated from a swing walk. Returns None if the
    peak isn't anchored or has insufficient history/vol."""
    date_idx = {r[0]: i for i, r in enumerate(rows)}
    base_idx = date_idx.get(peak.date)
    if base_idx is None:
        return None
    dates  = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    highs  = [r[2] for r in rows]
    lows   = [r[3] for r in rows]
    opens  = [r[4] for r in rows] if rows and len(rows[0]) >= 5 else None
    side = _peak_side(peak.overall)
    vol_pct = _realized_vol_pct(closes, base_idx)
    swing = _swing_walk(closes, base_idx, side, vol_pct,
                        highs=highs, lows=lows, dates=dates, opens=opens)
    if swing is None:
        return None

    returns, peak_returns, maes, mfes = {}, {}, {}, {}
    for label, _ in PERIODS:
        s = swing.get(label)
        if s is None:
            continue
        returns[label] = s['exit_ret']
        peak_returns[label] = s['mfe']
        mfes[label] = s['mfe']
        maes[label] = s['mae']

    return {
        'symbol':      peak.symbol_id,
        'date':        peak.date,
        'score':       peak.overall,
        'side':        side,
        'vol_pct':     vol_pct,
        'entry_close': float(closes[base_idx]),
        'returns':     returns,
        'peaks':       peak_returns,
        'maes':        maes,
        'mfes':        mfes,
        'swing':       swing,
    }


def _compute_option_pnl(side, fire_type, exit_bars, exit_close, entry_close,
                         fire_open, fire_high, fire_low, sigma_pct):
    """Deterministic option P&L at the exit bar using theta model + bimodal SL fill.

    Called when OPTION_TOTAL_DTE > 0 (option-aligned metric runs only: 'tp'/'tp26').
    Uses module-level OPTION_TOTAL_DTE / OPTION_HOLD_DAYS / OPTION_PREM_MULT
    set by set_dte_strategy().

    side: 'low' (call) or 'high' (put) — assess_scores convention.

    Bimodal SL fill (mirrors monte_carlo.py resolve()):
      - TP fire:  fill at mid of [tp_level, fire_high] (call) / [fire_low, tp_level] (put)
      - SL fire (intraday): fill at sl_level (stock was above/below SL at open)
      - SL fire (gap-through): fill at mid of gap region (open already through SL)
      - Expire (hard sell): fill at last close

    Returns float option P&L as fraction of premium (e.g. 0.30 = +30%) or None.
    """
    if OPTION_TOTAL_DTE <= 0 or exit_bars is None or exit_close is None:
        return None
    if entry_close is None or entry_close <= 0:
        return None
    if sigma_pct is None or sigma_pct <= 0:
        return None
    premium_pct = OPTION_PREM_MULT * sigma_pct / 100.0
    if premium_pct <= 0:
        return None

    bars_held = min(int(exit_bars), OPTION_HOLD_DAYS)
    # Map assess_scores side to option_pnl_pct convention
    pnl_side = 'call' if side == 'low' else 'put'

    if fire_type == 1:  # TP fired — limit-or-better fill
        tp_level = exit_close
        if pnl_side == 'call':  # fill at mid of [tp_level, fire_high]
            fh = fire_high if fire_high is not None else tp_level
            fill = (tp_level + fh) / 2
        else:                   # put TP: fill at mid of [fire_low, tp_level]
            fl = fire_low if fire_low is not None else tp_level
            fill = (fl + tp_level) / 2

    elif fire_type == 2:  # SL fired — bimodal fill
        sl_level = exit_close
        if pnl_side == 'call':  # call SL fires when stock drops through sl_level
            # intraday: open was above SL (stock fell intraday)
            if fire_open is not None and fire_open > sl_level:
                fill = sl_level
            # gap-through: open was already below SL
            elif fire_open is not None and fire_low is not None:
                fill = (fire_low + fire_open) / 2
            else:
                fill = sl_level
        else:                   # put SL fires when stock rises through sl_level
            # intraday: open was below SL
            if fire_open is not None and fire_open < sl_level:
                fill = sl_level
            # gap-through: open was already above SL
            elif fire_open is not None and fire_high is not None:
                fill = (fire_open + fire_high) / 2
            else:
                fill = sl_level

    else:  # expire / hard sell — fill at last close
        fill = exit_close

    try:
        from option_pricing import option_pnl_pct
        return option_pnl_pct(pnl_side, fill, entry_close, bars_held, premium_pct,
                               total_dte=OPTION_TOTAL_DTE)
    except Exception:
        return None


def _enrich_with_option_pnl(results):
    """Add option_pnl to each period in every result dict, in-place.
    No-op when OPTION_TOTAL_DTE == 0 (metric='wr', the only non-option-aligned metric).
    """
    if OPTION_TOTAL_DTE <= 0:
        return
    for r in results:
        swing = r.get('swing')
        if not swing:
            continue
        entry_close = r.get('entry_close') or r.get('entry')
        sigma_pct   = r.get('vol_pct')
        side        = r.get('side')
        for ps in swing.values():
            ps['option_pnl'] = _compute_option_pnl(
                side=side,
                fire_type=ps.get('fire_type'),
                exit_bars=ps.get('exit_bars'),
                exit_close=ps.get('exit_close'),
                entry_close=entry_close,
                fire_open=ps.get('fire_open'),
                fire_high=ps.get('fire_high'),
                fire_low=ps.get('fire_low'),
                sigma_pct=sigma_pct,
            )


def _detect_barrier_set():
    """Detect which barrier_set in BARRIER_SETS the current module-level SWING_K/M
    constants match. Returns the set key, or None if no match (use forward walk).
    Phase 16 (2026-04-28): cache supports both '30dte_generic' and '15dte_opt'.
    """
    try:
        from database.barrier_cache import BARRIER_SETS
    except Exception:
        return None
    for key, bs in BARRIER_SETS.items():
        if (abs(SWING_K_HIGH - bs['k_high']) < 1e-6 and
            abs(SWING_M_HIGH - bs['m_high']) < 1e-6 and
            abs(SWING_K_LOW  - bs['k_low'])  < 1e-6 and
            abs(SWING_M_LOW  - bs['m_low'])  < 1e-6 and
            SWING_REFERENCE_DAYS == bs['reference_days']):
            return key
    return None


def calculate_forward_returns(peaks, use_cache=True):
    """Compute per-peak forward-return result dicts.

    When `use_cache=True` (default), prefers the barrier_outcomes SQLite cache
    (~50× faster than per-symbol forward walks). Cache misses fall back to the
    forward-walk path. The cache is refreshed nightly by `trader update`.

    Phase 16 (2026-04-28): cache supports BOTH '30dte_generic' (legacy K=2.0/5.0)
    AND '15dte_opt' (15 DTE option-aligned K=0.903/0.774/0.516 at ref=15d). The
    set is auto-detected from the current SWING_K/M values via _detect_barrier_set().
    Custom barriers outside BARRIER_SETS still bypass the cache.
    """
    bset = _detect_barrier_set()
    if use_cache and bset is not None:
        try:
            from database.barrier_cache import peaks_to_swing_results, CACHE_DB
            if CACHE_DB.exists():
                results, skipped = peaks_to_swing_results(peaks, verbose=False, barrier_set=bset)
                required_periods = set(PERIOD_LABELS)
                complete_results = []
                incomplete_keys = set()
                for r in results:
                    if required_periods.issubset(set((r.get('swing') or {}).keys())):
                        complete_results.append(r)
                    else:
                        incomplete_keys.add((r['symbol'], r['date']))
                if skipped == 0 and not incomplete_keys:
                    _enrich_with_option_pnl(results)
                    return results
                # Some peaks or periods weren't in the cache — fall back to forward
                # walk for those. This keeps newly added periods correct before a
                # full barrier-cache backfill has populated historical rows.
                cached_keys = {(r['symbol'], r['date']) for r in complete_results}
                missing = [p for p in peaks if (p.symbol_id, p.date) not in cached_keys]
                if incomplete_keys:
                    print(
                        f"{Fore.YELLOW}barrier cache missing current periods for "
                        f"{len(incomplete_keys)} peak(s); forward-walking them{Style.RESET_ALL}"
                    )
                fw_results = _forward_walk_subset(missing)
                combined = complete_results + fw_results
                _enrich_with_option_pnl(combined)
                return combined
        except Exception as _e:
            print(f"{Fore.YELLOW}barrier cache unavailable ({_e}); falling back to forward walk{Style.RESET_ALL}")

    if bset is None:
        print(f"{Fore.YELLOW}Custom DTE barriers (K_call={SWING_K_LOW}/{SWING_M_LOW}, "
              f"K_put={SWING_K_HIGH}/{SWING_M_HIGH}, ref={SWING_REFERENCE_DAYS}d) — "
              f"not in BARRIER_SETS, using forward walk.{Style.RESET_ALL}")
    results = _forward_walk_subset(peaks)
    _enrich_with_option_pnl(results)
    return results


def _forward_walk_subset(peaks):
    """Original DB-querying forward walk path (cache-miss fallback)."""
    results = []
    symbols = set(p.symbol_id for p in peaks)

    price_cache = {}
    for sym in symbols:
        prices = list(
            PriceHistory.select(PriceHistory.date, PriceHistory.close,
                                PriceHistory.high, PriceHistory.low, PriceHistory.open)
            .where(PriceHistory.symbol == sym)
            .order_by(PriceHistory.date)
        )
        price_cache[sym] = [
            (p.date, float(p.close), float(p.high), float(p.low), float(p.open)) for p in prices
        ]

    for peak in peaks:
        rows = price_cache.get(peak.symbol_id, [])
        r = _peak_to_result(peak, rows)
        if r is not None:
            results.append(r)
    return results


def calculate_forward_returns_from_cache(peaks, ph_by_sym):
    """Like calculate_forward_returns but uses pre-loaded price data (no DB queries).

    ph_by_sym: dict[symbol -> list of rows with .date, .close, .high, .low], sorted ascending by date.
    """
    results = []
    for peak in peaks:
        raw = ph_by_sym.get(peak.symbol_id, [])
        rows = [(r.date, float(r.close), float(r.high), float(r.low)) for r in raw]
        r = _peak_to_result(peak, rows)
        if r is not None:
            results.append(r)
    return results


def assess_peaks_in_memory(peaks, ph_by_sym, lookback_days=DEFAULT_LOOKBACK):
    """Full assessment pipeline in memory — no DB writes.

    peaks    : list of objects with .symbol_id, .date, .overall
    ph_by_sym: dict[symbol -> list of rows with .date, .close, .high, .low], sorted ascending

    Returns a dict with:
        bucketed_stats : {bucket_key: stats_dict}   (same shape as compute_bucket_stats)
        band_ic_data   : output of compute_band_ics
        corrs          : output of compute_correlations
        n_peaks        : len(peaks)
        n_results      : peaks that had price data
    """
    results = calculate_forward_returns_from_cache(peaks, ph_by_sym)
    corrs = compute_correlations(results)
    bucketed = bucket_results(results)
    band_ic_data = compute_band_ics(results)

    bucketed_stats = {}
    for key in THRESHOLD_KEYS:
        entries = bucketed[key]
        stats = compute_bucket_stats(entries, is_sell=_is_sell_bucket(key)) or empty_stats()
        stats['bucket'] = key
        compute_shakeout(stats)
        bucketed_stats[key] = stats

    return {
        'bucketed_stats': bucketed_stats,
        'band_ic_data': band_ic_data,
        'corrs': corrs,
        'n_peaks': len(peaks),
        'n_results': len(results),
    }


def run_assessment_on_peaks(peaks, ph_by_sym, lookback_days=DEFAULT_LOOKBACK):
    """Print assessment tables from pre-loaded peaks and price history. No DB writes.
    Used by simulator.py to run in-memory assessment.
    Returns the same dict as assess_peaks_in_memory."""
    data = assess_peaks_in_memory(peaks, ph_by_sym, lookback_days)
    bucket_rows = [data['bucketed_stats'][k] for k in THRESHOLD_KEYS]

    print(f"\n{Fore.CYAN}=== In-Memory Assessment ==={Style.RESET_ALL}")
    print(f"Peaks: {data['n_peaks']} | Results: {data['n_results']}")
    print(f"{Fore.YELLOW}(read-only — results NOT saved to DB){Style.RESET_ALL}")

    _print_return_peak_table(bucket_rows)
    _print_winrate_table(bucket_rows)
    _print_rtr_winrate_table(bucket_rows)
    _print_swing_table(bucket_rows)
    _print_excursion_table(bucket_rows)
    _print_shakeout_table(bucket_rows)
    _print_ic_table(data['band_ic_data'])
    _print_correlations(data['corrs'])
    _print_rtr_correlations(bucket_rows)
    print()

    return data


def print_diff_assessment(old_data, new_data, label_old='DB', label_new='SIM'):
    """Print a side-by-side diff of two assess_peaks_in_memory results.

    Highlights key metrics (WR30, Ret30, MAE30, MFE30, Cap30) old vs new per bucket.
    """
    def _delta_color(d):
        if d is None:
            return Fore.WHITE
        return Fore.GREEN if d > 0 else Fore.RED if d < 0 else Fore.WHITE

    def _fmt(v, fmt='.1f'):
        return f"{v:{fmt}}" if v is not None else '--'

    def _delta_str(old, new):
        if old is None or new is None:
            return ''
        d = new - old
        col = _delta_color(d)
        sign = '+' if d >= 0 else ''
        return f" {col}({sign}{d:.1f}){Style.RESET_ALL}"

    metrics = [
        ('WR15',  'win_rate_15d',      '.1f', '%'),
        ('WR30',  'win_rate_30d',      '.1f', '%'),
        ('Ret30', 'avg_return_30d',    '.2f', '%'),
        ('MAE30', 'avg_mae_30d',       '.2f', '%'),
        ('MFE30', 'avg_mfe_30d',       '.2f', '%'),
        ('Cap30', 'capture_ratio_30d', '.3f', ''),
    ]

    col_w = 14  # width per metric pair column
    metric_header = '  '.join(f"{m[0]:^{col_w}}" for m in metrics)
    print(f"\n{Fore.CYAN}=== Assessment Diff: {label_old} -> {label_new} ==={Style.RESET_ALL}")
    print(f"{'Bucket':>8} | {'N':>4}/{'':<4} | {metric_header}")
    print(f"{'':>8} | {label_old[:4]:>4}/{label_new[:3]:<4} |")
    print("-" * (16 + len(metric_header) + 3))

    active_buckets = [k for k in THRESHOLD_KEYS
                      if (old_data['bucketed_stats'][k].get('sample_count') or 0) > 0
                      or (new_data['bucketed_stats'][k].get('sample_count') or 0) > 0]

    for key in active_buckets:
        os = old_data['bucketed_stats'][key]
        ns = new_data['bucketed_stats'][key]
        n_old = os.get('sample_count') or 0
        n_new = ns.get('sample_count') or 0

        parts = []
        for _, field, fmt, suffix in metrics:
            ov = os.get(field)
            nv = ns.get(field)
            old_str = f"{_fmt(ov, fmt)}{suffix}" if ov is not None else '--'
            new_str = f"{_fmt(nv, fmt)}{suffix}" if nv is not None else '--'
            delta = _delta_str(ov, nv)
            cell = f"{old_str}->{new_str}{delta}"
            parts.append(cell[:col_w].ljust(col_w))

        print(f"{key:>8} | {n_old:>4}/{n_new:<4} | {'  '.join(parts)}")

    print()


def bucket_results(results):
    bucketed = {k: [] for k in THRESHOLD_KEYS}
    for r in results:
        score = r['score']
        for t in BUY_THRESHOLDS:
            if score >= t:
                bucketed[f'{t}+'].append(r)
        for t in SELL_THRESHOLDS:
            if score <= t:
                bucketed[f'<{t}'].append(r)
    return bucketed


def _is_sell_bucket(key):
    """Legacy alias retained for back-compat with display helpers. With the swing
    rewrite, returns/MAE/MFE are already side-adjusted (positive = trade worked),
    so the old sign-flip color convention should not be applied. Always False."""
    return False


def compute_bucket_stats(entries, is_sell=False):
    """Compute swing-barrier stats for one bucket.

    `entries` are result dicts produced by `_peak_to_result`. Each carries a
    side-adjusted exit return (positive = trade direction worked) plus a per-
    period barrier outcome ('win' / 'stop' / 'expire'). The bucket-level
    win_rate_{p} stored here is the swing p_win, not the sign-of-return rate.

    `is_sell` is accepted for legacy call sites but ignored — side handling now
    happens upstream when `_peak_to_result` builds the side-adjusted dicts.
    """
    if not entries:
        return None
    scores = [e['score'] for e in entries]
    stats = {'sample_count': len(entries), 'avg_score': round(np.mean(scores), 1)}

    for label in PERIOD_LABELS:
        # Only entries that produced a swing result for this period count toward
        # the per-period stats. (Periods with W > available forward bars are
        # silently dropped per-peak.)
        per_period = [e['swing'][label] for e in entries
                      if 'swing' in e and label in e.get('swing', {})]

        if not per_period:
            stats[f'avg_return_{label}'] = None
            stats[f'median_return_{label}'] = None
            stats[f'win_rate_{label}'] = None
            stats[f'win_rate_unscaled_{label}'] = None
            stats[f'avg_peak_{label}'] = None
            stats[f'median_peak_{label}'] = None
            stats[f'avg_mae_{label}'] = None
            stats[f'median_mae_{label}'] = None
            stats[f'avg_mfe_{label}'] = None
            stats[f'median_mfe_{label}'] = None
            stats[f'swing_p_stop_{label}'] = None
            stats[f'swing_p_expire_{label}'] = None
            stats[f'swing_avg_win_pnl_{label}'] = None
            stats[f'swing_avg_stop_pnl_{label}'] = None
            continue

        n = len(per_period)
        rets = [s['exit_ret'] for s in per_period]
        mfes_l = [s['mfe'] for s in per_period]
        maes_l = [s['mae'] for s in per_period]

        wins = [s for s in per_period if s['result'] == 'win']
        stops = [s for s in per_period if s['result'] == 'stop']
        expires = [s for s in per_period if s['result'] == 'expire']

        wins_u = [s for s in per_period if s.get('result_unscaled') == 'win']
        stats[f'win_rate_unscaled_{label}'] = round(len(wins_u) / n * 100, 1)

        stats[f'avg_return_{label}'] = round(float(np.mean(rets)), 2)
        stats[f'median_return_{label}'] = round(float(median(rets)), 2)
        stats[f'win_rate_{label}'] = round(len(wins) / n * 100, 1)
        stats[f'swing_p_stop_{label}'] = round(len(stops) / n * 100, 1)
        stats[f'swing_p_expire_{label}'] = round(len(expires) / n * 100, 1)
        stats[f'swing_avg_win_pnl_{label}'] = (
            round(float(np.mean([s['exit_ret'] for s in wins])), 2) if wins else None
        )
        stats[f'swing_avg_stop_pnl_{label}'] = (
            round(float(np.mean([s['exit_ret'] for s in stops])), 2) if stops else None
        )
        # Option P&L (theta + bimodal fill) — only populated for option-aligned metrics ('tp'/'tp26')
        option_pnls = [s['option_pnl'] for s in per_period if s.get('option_pnl') is not None]
        stats[f'avg_option_pnl_{label}'] = (
            round(float(np.mean(option_pnls)) * 100, 2) if option_pnls else None
        )
        stats[f'avg_peak_{label}'] = round(float(np.mean(mfes_l)), 2)
        stats[f'median_peak_{label}'] = round(float(median(mfes_l)), 2)
        stats[f'avg_mae_{label}'] = round(float(np.mean(maes_l)), 2)
        stats[f'median_mae_{label}'] = round(float(median(maes_l)), 2)
        stats[f'avg_mfe_{label}'] = round(float(np.mean(mfes_l)), 2)
        stats[f'median_mfe_{label}'] = round(float(median(mfes_l)), 2)
        stats[f'mfe_p25_{label}'] = round(float(np.percentile(mfes_l, 25)), 2)
        stats[f'mfe_p75_{label}'] = round(float(np.percentile(mfes_l, 75)), 2)
        stats[f'mfe_p90_{label}'] = round(float(np.percentile(mfes_l, 90)), 2)

    # MFE sigma-normalized — TP anchors in units of σ.
    # De-normalize per-stock at display time: mfe_sigma * current_stock_σ = TP %.
    # Consistent with how win barriers are defined (K·σ), so TP targets are
    # directly comparable to the K parameter across different volatility regimes.
    for label in PERIOD_LABELS:
        per_period = [e['swing'][label] for e in entries
                      if 'swing' in e and label in e.get('swing', {})]
        mfes_sig = [s['mfe_sigma'] for s in per_period if s.get('mfe_sigma') is not None]
        if mfes_sig:
            stats[f'avg_mfe_sigma_{label}']    = round(float(np.mean(mfes_sig)), 3)
            stats[f'median_mfe_sigma_{label}'] = round(float(median(mfes_sig)), 3)
            stats[f'mfe_sigma_p25_{label}']    = round(float(np.percentile(mfes_sig, 25)), 3)
            stats[f'mfe_sigma_p75_{label}']    = round(float(np.percentile(mfes_sig, 75)), 3)
        else:
            stats[f'avg_mfe_sigma_{label}']    = None
            stats[f'median_mfe_sigma_{label}'] = None
            stats[f'mfe_sigma_p25_{label}']    = None
            stats[f'mfe_sigma_p75_{label}']    = None

    # Winner/loser MAE — both raw % and sigma-normalized — across all periods.
    # Sigma lets the frontend de-normalize per-stock (mae_sigma * current σ)
    # for a stock-specific stop-loss level that adapts to volatility.
    for wl in PERIOD_LABELS:
        per_period = [e['swing'][wl] for e in entries
                      if 'swing' in e and wl in e.get('swing', {})]
        if per_period:
            winners = [s for s in per_period if s['result'] == 'win']
            losers  = [s for s in per_period if s['result'] != 'win']
            winners_mae = [s['mae'] for s in winners]
            losers_mae  = [s['mae'] for s in losers]
            winners_mae_sig = [s['mae_sigma'] for s in winners if s.get('mae_sigma') is not None]
            losers_mae_sig  = [s['mae_sigma'] for s in losers  if s.get('mae_sigma') is not None]
            stats[f'avg_mae_winner_{wl}'] = (
                round(float(np.mean(winners_mae)), 2) if winners_mae else None
            )
            stats[f'avg_mae_loser_{wl}'] = (
                round(float(np.mean(losers_mae)), 2) if losers_mae else None
            )
            stats[f'avg_mae_winner_sigma_{wl}'] = (
                round(float(np.mean(winners_mae_sig)), 3) if winners_mae_sig else None
            )
            stats[f'avg_mae_loser_sigma_{wl}'] = (
                round(float(np.mean(losers_mae_sig)), 3) if losers_mae_sig else None
            )
        else:
            stats[f'avg_mae_winner_{wl}'] = None
            stats[f'avg_mae_loser_{wl}'] = None
            stats[f'avg_mae_winner_sigma_{wl}'] = None
            stats[f'avg_mae_loser_sigma_{wl}'] = None

    for label in PERIOD_LABELS:
        avg_ret = stats.get(f'avg_return_{label}')
        avg_mfe = stats.get(f'avg_mfe_{label}')
        if avg_ret is not None and avg_mfe and avg_mfe != 0:
            stats[f'capture_ratio_{label}'] = round(avg_ret / avg_mfe, 3)
        else:
            stats[f'capture_ratio_{label}'] = None

    return stats


_ALL_STAT_PREFIXES = [
    'avg_return', 'median_return', 'win_rate', 'avg_peak', 'median_peak',
    'avg_mae', 'median_mae', 'avg_mfe', 'median_mfe', 'mfe_p25', 'mfe_p75', 'mfe_p90',
    'avg_mfe_sigma', 'median_mfe_sigma', 'mfe_sigma_p25', 'mfe_sigma_p75',
    'capture_ratio',
    'swing_p_stop', 'swing_p_expire', 'swing_avg_win_pnl', 'swing_avg_stop_pnl',
]


def empty_stats():
    stats = {'sample_count': 0, 'avg_score': None}
    for label in PERIOD_LABELS:
        for prefix in _ALL_STAT_PREFIXES:
            stats[f'{prefix}_{label}'] = None
    for wl in PERIOD_LABELS:
        stats[f'avg_mae_winner_{wl}'] = None
        stats[f'avg_mae_loser_{wl}'] = None
        stats[f'avg_mae_winner_sigma_{wl}'] = None
        stats[f'avg_mae_loser_sigma_{wl}'] = None
    stats['shakeout_depth'] = None
    stats['shakeout_recovery'] = None
    return stats


def slice_by_window(results):
    today = date.today()
    windowed = {}
    for label, days in WINDOWS:
        cutoff = today - timedelta(days=days)
        subset = [r for r in results if r['date'] >= cutoff]
        if subset:
            windowed[label] = subset
    return windowed


def compute_correlations(results):
    """Pearson correlation between score and binary peak-win, computed separately for each side.

    Returns {label: {'high': corr, 'low': corr}} where:
      high = Pearson(score, win) for HIGH peaks (score>=50, call direction)
             Positive = higher score → more call wins.
      low  = Pearson(50-score, win) for LOW peaks (score<50, put direction)
             Positive = lower score → more put wins.

    Pooling both sides into a single correlation is invalid because HIGH and LOW wins
    point in opposite directions — a score-70 call win and a score-20 put win both map
    to win=1 but pull score correlation in opposite directions.
    """
    corrs = {}
    for label in PERIOD_LABELS:
        high_pairs, low_pairs = [], []
        for r in results:
            sw = r.get('swing', {}).get(label)
            if sw is None or sw.get('result') is None:
                continue
            win = 1 if sw['result'] == 'win' else 0
            if r['score'] >= 50:
                high_pairs.append((r['score'], win))
            else:
                low_pairs.append((50 - r['score'], win))
        high_corr = low_corr = None
        if len(high_pairs) >= 5:
            xs, ys = zip(*high_pairs)
            c = np.corrcoef(xs, ys)[0, 1]
            high_corr = round(c, 3) if not np.isnan(c) else None
        if len(low_pairs) >= 5:
            xs, ys = zip(*low_pairs)
            c = np.corrcoef(xs, ys)[0, 1]
            low_corr = round(c, 3) if not np.isnan(c) else None
        corrs[label] = {'high': high_corr, 'low': low_corr}
    return corrs


def compute_rtr_correlations(bucket_rows):
    """Cross-bucket Pearson(threshold, RTR_win%) for HIGH and LOW sides separately.

    Uses one observation per threshold bucket (6 HIGH, 3 LOW), so noise from
    individual trades is averaged out before computing the correlation. RTR_win%
    uses the side-appropriate random-walk floor derived from gambler's ruin:
      CALL floor = M/(K+M) = 5/7 ≈ 71.43%  (K=2σ target, M=5σ stop)
      PUT  floor = M/(K+M) = 2/3 ≈ 66.67%  (K=1σ target, M=2σ stop)

    HIGH side: x = threshold [70,75,80,85,90,95], y = RTR_win% per bucket
    LOW  side: x = put_strength [25,35,45] (=50−threshold), y = RTR_win% per bucket
    Positive Pearson = higher-scored buckets win more than random.
    """
    rows_by_key = {_get(r, 'bucket'): r for r in bucket_rows}
    corrs = {}
    for label in PERIOD_LABELS:
        high_xy, low_xy = [], []
        for t in BUY_THRESHOLDS:
            row = rows_by_key.get(f'{t}+')
            if row is None:
                continue
            rtr = _rtr(_get(row, f'win_rate_{label}'), RANDOM_WIN_RATE_CALL)
            if rtr is not None:
                high_xy.append((t, rtr))
        for t in SELL_THRESHOLDS:
            row = rows_by_key.get(f'<{t}')
            if row is None:
                continue
            rtr = _rtr(_get(row, f'win_rate_{label}'), RANDOM_WIN_RATE_PUT)
            if rtr is not None:
                low_xy.append((50 - t, rtr))
        high_corr = low_corr = None
        if len(high_xy) >= 3:
            xs, ys = zip(*high_xy)
            c = np.corrcoef(xs, ys)[0, 1]
            high_corr = round(c, 3) if not np.isnan(c) else None
        if len(low_xy) >= 3:
            xs, ys = zip(*low_xy)
            c = np.corrcoef(xs, ys)[0, 1]
            low_corr = round(c, 3) if not np.isnan(c) else None
        corrs[label] = {'high': high_corr, 'low': low_corr}
    return corrs


def _print_rtr_correlations(bucket_rows):
    """Compute and print cross-bucket RTR Pearson for HIGH and LOW sides."""
    rtr_corrs = compute_rtr_correlations(bucket_rows)

    def _fmt(val):
        if val is None:
            return '--   '
        color = Fore.GREEN if val > 0.1 else Fore.RED if val < 0 else Fore.YELLOW
        return f"{color}{val:+.3f}{Style.RESET_ALL}"

    high_parts = ' | '.join(f"{l}={_fmt(rtr_corrs.get(l, {}).get('high'))}" for l in PERIOD_LABELS)
    low_parts  = ' | '.join(f"{l}={_fmt(rtr_corrs.get(l, {}).get('low'))}"  for l in PERIOD_LABELS)
    print(f"RTR Corr  (bucket score vs RTR win,  HIGH): {high_parts}")
    print(f"RTR Corr  (put strength  vs RTR win,  LOW): {low_parts}")


# Non-overlapping bands for intra-band IC computation.
# IC is stored in a dedicated table (ScoreAssessmentBandIC) to avoid semantic
# confusion with cumulative bucket rows where every other field reflects the
# full >= threshold population.
IC_BANDS = [
    (95, 100), (90, 94), (85, 89), (80, 84), (75, 79), (70, 74),
    (0, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30),
]
IC_BAND_LABELS = {(lo, hi): f"{lo}-{hi}" for lo, hi in IC_BANDS}
IC_BAND_TO_BUCKET = {
    '95-100': '95+', '90-94': '90+', '85-89': '85+',
    '80-84': '80+', '75-79': '75+', '70-74': '70+',
    '0-5': '<5', '6-10': '<10', '11-15': '<15',
    '16-20': '<20', '21-25': '<25', '26-30': '<30',
}


def compute_band_ics(results):
    """Compute pearson IC on non-overlapping score bands.
    Returns list of dicts suitable for ScoreAssessmentBandIC creation."""
    band_rows = []
    for lo, hi in IC_BANDS:
        band_entries = [r for r in results if lo <= r['score'] <= hi]
        row = {'band': IC_BAND_LABELS[(lo, hi)], 'sample_count': len(band_entries)}
        for label in PERIOD_LABELS:
            pairs = [(r['score'], r['returns'][label]) for r in band_entries if label in r['returns']]
            if len(pairs) >= 5:
                scores, rets = zip(*pairs)
                corr = np.corrcoef(scores, rets)[0, 1]
                row[f'ic_{label}'] = round(corr, 3) if not np.isnan(corr) else None
            else:
                row[f'ic_{label}'] = None
        band_rows.append(row)
    return band_rows


def compute_shakeout(stats):
    """Compute shakeout depth and recovery from populated win rates.
    Reference: win_rate_60d. Falls back to win_rate_30d if 60d is null."""
    wr7 = stats.get('win_rate_7d')
    wr60 = stats.get('win_rate_60d')
    ref_wr = wr60 if wr60 is not None else stats.get('win_rate_30d')
    if wr7 is not None and ref_wr is not None:
        stats['shakeout_depth'] = round(wr7 - ref_wr, 2)
    else:
        stats['shakeout_depth'] = None
    stats['shakeout_recovery'] = None
    if stats['shakeout_depth'] is not None and stats['shakeout_depth'] < 0 and ref_wr is not None:
        for n_label, n_days in [('7d', 7), ('15d', 15), ('30d', 30)]:
            wr_n = stats.get(f'win_rate_{n_label}')
            if wr_n is not None and wr_n >= ref_wr:
                stats['shakeout_recovery'] = n_days
                break


def option_collection_health(symbol=None):
    """Print per-symbol collection health statistics."""
    from peewee import fn
    query = (
        OptionPrice.select(
            Option.symbol,
            fn.COUNT(fn.DISTINCT(OptionPrice.date)).alias('days_collected'),
            fn.MIN(OptionPrice.date).alias('earliest'),
            fn.MAX(OptionPrice.date).alias('latest'),
        )
        .join(Option)
        .group_by(Option.symbol)
        .order_by(fn.COUNT(fn.DISTINCT(OptionPrice.date)).asc())
    )
    if symbol:
        query = query.where(Option.symbol == symbol.upper())

    rows = list(query.dicts())
    if not rows:
        print(f"{Fore.YELLOW}No option price data found.{Style.RESET_ALL}")
        return

    # Gap info from Option table
    gap_query = (
        Option.select(
            Option.symbol,
            fn.MAX(Option.last_gap_days).alias('worst_gap'),
            fn.MAX(Option.last_gap_detected_at).alias('last_gap_date'),
        )
        .where(Option.last_gap_days.is_null(False))
        .group_by(Option.symbol)
    )
    gap_info = {r['symbol']: r for r in gap_query.dicts()}

    hdr = f"{'Symbol':<8} {'Days':>5} {'Span':>10} {'Coverage':>9} {'Worst Gap':>10} {'Last Gap Date':>14}"
    print(f"\n{Fore.CYAN}--- Options Collection Health ---{Style.RESET_ALL}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        span = trading_days_between(r['earliest'], r['latest'])
        cov = r['days_collected'] / span * 100 if span > 0 else 0
        gi = gap_info.get(r['symbol'], {})
        wg = gi.get('worst_gap', '--')
        lg = str(gi.get('last_gap_date', '--'))[:10]
        color = Fore.RED if cov < 70 else Fore.YELLOW if cov < 85 else Fore.GREEN
        span_str = f"{(r['latest'] - r['earliest']).days}d"
        print(f"{r['symbol']:<8} {r['days_collected']:>5} {span_str:>10} {color}{cov:>8.1f}%{Style.RESET_ALL} {str(wg):>10} {lg:>14}")


def _get(obj, field):
    return obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)


RET_PEAK_PART_W = 8
RET_PEAK_COL_W = RET_PEAK_PART_W + 1 + RET_PEAK_PART_W
WIN_COL_W = 9


def _fmt_win(v):
    if v is None:
        plain = f"{'--':>{WIN_COL_W}}"
        return plain
    plain = f"{v:>6.1f}%".rjust(WIN_COL_W)
    color = Fore.GREEN if v >= 55 else Fore.RED if v < 45 else Fore.YELLOW
    return f"{color}{plain}{Style.RESET_ALL}"


def _bucket_floor(bucket_key):
    """Return the correct random-walk RTR floor for a bucket key.
    PUT buckets (<25, <15, <5) use RANDOM_WIN_RATE_PUT; call buckets use RANDOM_WIN_RATE_CALL."""
    return RANDOM_WIN_RATE_PUT if (bucket_key or '').startswith('<') else RANDOM_WIN_RATE_CALL


def _rtr(win_rate, floor=None):
    """Normalise win_rate relative to the side-appropriate random-walk floor.
    0% = matches random walk; 100% = all wins; negative = below random.
    If floor is not supplied, defaults to RANDOM_WIN_RATE_CALL."""
    if win_rate is None:
        return None
    f = floor if floor is not None else RANDOM_WIN_RATE_CALL
    return round((win_rate - f) / (100.0 - f) * 100.0, 1)


def _fmt_rtr(v):
    if v is None:
        return f"{'--':>{WIN_COL_W}}"
    plain = f"{v:>+6.1f}%".rjust(WIN_COL_W)
    color = Fore.GREEN if v > 0 else Fore.RED if v < 0 else Fore.YELLOW
    return f"{color}{plain}{Style.RESET_ALL}"


def _color_val(v, sell=False):
    """Green for favorable, red for adverse. Sell buckets invert the sign convention."""
    if v is None or v == 0:
        return ''
    if sell:
        return Fore.GREEN if v < 0 else Fore.RED
    return Fore.GREEN if v > 0 else Fore.RED


def _fmt_ret_peak_pair(row, label, sell=False):
    ret = _get(row, f'avg_return_{label}')
    pk = _get(row, f'avg_peak_{label}')

    def part_plain(v):
        if v is None:
            return '--'.rjust(RET_PEAK_PART_W)
        s = f"{v:+7.1f}%"
        s = s[:RET_PEAK_PART_W] if len(s) > RET_PEAK_PART_W else s
        return s.rjust(RET_PEAK_PART_W)

    def part_colored(v):
        p = part_plain(v)
        if v is None:
            return p
        color = _color_val(v, sell)
        return f"{color}{p}{Style.RESET_ALL}" if color else p

    return f"{part_colored(ret)}/{part_colored(pk)}"


def _nonempty_bucket_rows(bucket_rows):
    return [r for r in bucket_rows if (_get(r, 'sample_count') or 0) > 0]


def _hdr_ret_peak(label):
    text = f"{label} ret/pk"
    if len(text) > RET_PEAK_COL_W:
        text = text[:RET_PEAK_COL_W]
    return text.rjust(RET_PEAK_COL_W)


def _print_return_peak_table(bucket_rows, title="Side-adjusted EV (exit return / MFE)"):
    rows = _nonempty_bucket_rows(bucket_rows)
    if not rows:
        return
    col_headers = ' | '.join(_hdr_ret_peak(l) for l in PERIOD_LABELS)
    header = f"{'Bucket':>8} | {'Count':>5} | {col_headers}"
    print(f"\n{Fore.WHITE}{title}{Style.RESET_ALL}")
    print(header)
    print("-" * len(header))
    for row in rows:
        bucket = _get(row, 'bucket')
        sell = _is_sell_bucket(bucket) if bucket else False
        count = _get(row, 'sample_count')
        cols = ' | '.join(_fmt_ret_peak_pair(row, l, sell) for l in PERIOD_LABELS)
        print(f"{bucket:>8} | {count:>5} | {cols}")


def _hdr_win(label):
    text = f"{label} Win"
    if len(text) > WIN_COL_W:
        text = text[:WIN_COL_W]
    return text.rjust(WIN_COL_W)


def _print_winrate_table(bucket_rows):
    rows = _nonempty_bucket_rows(bucket_rows)
    if not rows:
        return
    col_headers = ' | '.join(_hdr_win(l) for l in PERIOD_LABELS)
    header = f"{'Bucket':>8} | {'Count':>5} | {col_headers}"
    print(f"\n{Fore.WHITE}Win Rates{Style.RESET_ALL}")
    print(header)
    print("-" * len(header))
    for row in rows:
        count = _get(row, 'sample_count')
        cols = ' | '.join(_fmt_win(_get(row, f'win_rate_{l}')) for l in PERIOD_LABELS)
        print(f"{_get(row, 'bucket'):>8} | {count:>5} | {cols}")


def _print_rtr_winrate_table(bucket_rows):
    """Print RTR (Relative-to-Random) win rates: (win% − floor) / (100 − floor).
    Floor is side-specific: calls={RANDOM_WIN_RATE_CALL:.1f}% (K=2/M=5), puts={RANDOM_WIN_RATE_PUT:.1f}% (K=1/M=2)."""
    rows = _nonempty_bucket_rows(bucket_rows)
    if not rows:
        return
    col_headers = ' | '.join(f"{'RTR ' + l:>{WIN_COL_W}}" for l in PERIOD_LABELS)
    header = f"{'Bucket':>8} | {'Count':>5} | {col_headers}"
    print(f"\n{Fore.WHITE}RTR Win Rates  "
          f"(calls floor={RANDOM_WIN_RATE_CALL:.1f}%, puts floor={RANDOM_WIN_RATE_PUT:.1f}%){Style.RESET_ALL}")
    print(header)
    print("-" * len(header))
    for row in rows:
        bucket = _get(row, 'bucket')
        floor = _bucket_floor(bucket)
        count = _get(row, 'sample_count')
        cols = ' | '.join(_fmt_rtr(_rtr(_get(row, f'win_rate_{l}'), floor)) for l in PERIOD_LABELS)
        print(f"{_get(row, 'bucket'):>8} | {count:>5} | {cols}")


def _print_correlations(corrs):
    """Print HIGH/LOW split correlations.

    Accepts two formats:
      New (in-memory): {label: {'high': float|None, 'low': float|None}}
      Legacy (DB row/meta dict): has 'correlation_{label}' keys with scalar values.

    Positive correlation = stronger signal predicts more wins in that direction.
    """
    def _fmt(val):
        if val is None:
            return '--   '
        color = Fore.GREEN if val > 0.1 else Fore.RED if val < 0 else Fore.YELLOW
        return f"{color}{val:+.3f}{Style.RESET_ALL}"

    # Detect format: new dict has label keys mapping to sub-dicts
    first = next(iter(corrs.values()), None) if isinstance(corrs, dict) else None
    if isinstance(first, dict):
        high_parts = ' | '.join(f"{l}={_fmt(corrs.get(l, {}).get('high'))}" for l in PERIOD_LABELS)
        low_parts  = ' | '.join(f"{l}={_fmt(corrs.get(l, {}).get('low'))}"  for l in PERIOD_LABELS)
        print(f"\nCorrelation (score vs call win,     HIGH peaks): {high_parts}")
        print(f"Correlation (put strength vs put win, LOW peaks): {low_parts}")
    else:
        # Legacy DB-backed path — single combined value per period
        parts = []
        for label in PERIOD_LABELS:
            val = _get(corrs, f'correlation_{label}')
            parts.append(f"{label}={_fmt(val)}" if val is not None else f"{label}=--")
        print(f"\nCorrelation (legacy, combined): {' | '.join(parts)}")


EXCURSION_COL_W = 9


def _fmt_exc(v, sell=False):
    if v is None:
        return '--'.rjust(EXCURSION_COL_W)
    plain = f"{v:+6.1f}%".rjust(EXCURSION_COL_W)
    color = _color_val(v, sell)
    return f"{color}{plain}{Style.RESET_ALL}" if color else plain


def _fmt_ratio(v):
    if v is None:
        return '--'.rjust(EXCURSION_COL_W)
    plain = f"{v:.2f}".rjust(EXCURSION_COL_W)
    color = Fore.GREEN if v >= 0.5 else Fore.RED if v < 0.3 else Fore.YELLOW
    return f"{color}{plain}{Style.RESET_ALL}"


def _print_excursion_table(bucket_rows):
    rows = _nonempty_bucket_rows(bucket_rows)
    if not rows:
        return
    header = f"{'Bucket':>8} | {'Count':>5} | {'MAE 30d':>{EXCURSION_COL_W}} | {'MAE Win':>{EXCURSION_COL_W}} | {'MFE 30d':>{EXCURSION_COL_W}} | {'Cap 30d':>{EXCURSION_COL_W}}"
    print(f"\n{Fore.WHITE}Excursion Profile (side-adjusted: + favorable / - adverse){Style.RESET_ALL}")
    print(header)
    print("-" * len(header))
    for row in rows:
        bucket = _get(row, 'bucket')
        count = _get(row, 'sample_count')
        mae = _fmt_exc(_get(row, 'avg_mae_30d'))
        mae_w = _fmt_exc(_get(row, 'avg_mae_winner_30d'))
        mfe = _fmt_exc(_get(row, 'avg_mfe_30d'))
        cap = _fmt_ratio(_get(row, 'capture_ratio_30d'))
        print(f"{bucket:>8} | {count:>5} | {mae} | {mae_w} | {mfe} | {cap}")


def _fmt_pct(v, width=7):
    if v is None:
        return '--'.rjust(width)
    return f"{v:>5.1f}%".rjust(width)


def _fmt_signed_pct(v, width=8):
    if v is None:
        return '--'.rjust(width)
    color = Fore.GREEN if v > 0 else Fore.RED if v < 0 else ''
    plain = f"{v:+6.1f}%".rjust(width)
    return f"{color}{plain}{Style.RESET_ALL}" if color else plain


def _print_swing_table(bucket_rows, period='30d'):
    """Print barrier-outcome breakdown matching the experiment output:
    N / p_win / p_stop / p_exp / win_pnl / stop_pnl / EV per bucket at one period.
    EV is the side-adjusted avg_return_{period} (positive = trade direction worked).
    """
    rows = _nonempty_bucket_rows(bucket_rows)
    if not rows:
        return
    cols = (
        f"{'Bucket':>8} | {'N':>5} | {'p_win':>7} {'p_stop':>7} {'p_exp':>7} | "
        f"{'win_pnl':>9} {'stop_pnl':>9} | {'EV':>8}"
    )
    scale = math.sqrt(int(period[:-1]) / SWING_REFERENCE_DAYS) if period != '1d' else None
    k_call = f"{SWING_K_LOW  * scale:.2f}" if scale else 'dir'
    k_put  = f"{SWING_K_HIGH * scale:.2f}" if scale else 'dir'
    print(f"\n{Fore.WHITE}Swing Barrier Outcomes @ {period} "
          f"(HIGH/call K={k_call}, LOW/put K={k_put}, scaled from 30d ref){Style.RESET_ALL}")
    print(cols)
    print('-' * len(cols))
    for row in rows:
        bucket = _get(row, 'bucket')
        n = _get(row, 'sample_count') or 0
        p_win = _get(row, f'win_rate_{period}')
        p_stop = _get(row, f'swing_p_stop_{period}')
        p_exp = _get(row, f'swing_p_expire_{period}')
        win_pnl = _get(row, f'swing_avg_win_pnl_{period}')
        stop_pnl = _get(row, f'swing_avg_stop_pnl_{period}')
        ev = _get(row, f'avg_return_{period}')
        print(
            f"{bucket:>8} | {n:>5} | {_fmt_pct(p_win)} {_fmt_pct(p_stop)} {_fmt_pct(p_exp)} | "
            f"{_fmt_signed_pct(win_pnl, 9)} {_fmt_signed_pct(stop_pnl, 9)} | {_fmt_signed_pct(ev, 8)}"
        )


def _print_shakeout_table(bucket_rows):
    rows = _nonempty_bucket_rows(bucket_rows)
    if not rows:
        return
    has_shakeout = any(_get(r, 'shakeout_depth') is not None for r in rows)
    if not has_shakeout:
        return
    header = f"{'Bucket':>8} | {'7d Win':>{WIN_COL_W}} | {'15d Win':>{WIN_COL_W}} | {'30d Win':>{WIN_COL_W}} | {'60d Win':>{WIN_COL_W}} | {'Depth':>8} | {'Recov':>5}"
    print(f"\n{Fore.WHITE}Shakeout Profile{Style.RESET_ALL}")
    print(header)
    print("-" * len(header))
    for row in rows:
        depth = _get(row, 'shakeout_depth')
        recov = _get(row, 'shakeout_recovery')
        if depth is None:
            continue
        depth_s = f"{depth:+5.1f}%".rjust(8)
        depth_c = Fore.RED if depth < -5 else Fore.YELLOW if depth < 0 else Fore.GREEN
        recov_s = f"{recov}d".rjust(5) if recov is not None else '  --'
        print(f"{_get(row, 'bucket'):>8} | {_fmt_win(_get(row, 'win_rate_7d'))} | "
              f"{_fmt_win(_get(row, 'win_rate_15d'))} | {_fmt_win(_get(row, 'win_rate_30d'))} | "
              f"{_fmt_win(_get(row, 'win_rate_60d'))} | {depth_c}{depth_s}{Style.RESET_ALL} | {recov_s}")


def _fmt_ic(v):
    if v is None:
        return '--'.rjust(EXCURSION_COL_W)
    plain = f"{v:+.3f}".rjust(EXCURSION_COL_W)
    color = Fore.GREEN if v > 0.3 else Fore.RED if v < -0.1 else Fore.YELLOW
    return f"{color}{plain}{Style.RESET_ALL}"


def _print_ic_table(band_ic_rows):
    """Print IC table from band IC data (list of dicts or ScoreAssessmentBandIC instances)."""
    if not band_ic_rows:
        return
    rows = [r for r in band_ic_rows if (_get(r, 'sample_count') or 0) >= 5]
    has_ic = any(_get(r, 'ic_30d') is not None for r in rows)
    if not has_ic:
        return
    ic_labels = ['7d', '15d', '30d', '60d']
    col_headers = ' | '.join(f"{'IC ' + l:>{EXCURSION_COL_W}}" for l in ic_labels)
    header = f"{'Bucket':>8} | {'Band':>8} | {'N':>5} | {col_headers}"
    print(f"\n{Fore.WHITE}Intra-Band IC (non-overlapping score bands){Style.RESET_ALL}")
    print(header)
    print("-" * len(header))
    for row in rows:
        band = _get(row, 'band')
        bucket = IC_BAND_TO_BUCKET.get(band, '')
        n = _get(row, 'sample_count') or 0
        cols = ' | '.join(_fmt_ic(_get(row, f'ic_{l}')) for l in ic_labels)
        print(f"{bucket:>8} | {band:>8} | {n:>5} | {cols}")


def print_results(run, bucket_rows, band_ic_rows=None):
    sym_display = run.symbol or 'ALL'
    version_str = f" | v{run.version_id}" if run.version_id else ""
    commit_str = f" | {run.git_commit}" if run.git_commit else ""
    print(f"\n{Fore.CYAN}=== Score Assessment (run #{run.id}{version_str}{commit_str}, {run.run_at:%Y-%m-%d %H:%M}) ==={Style.RESET_ALL}")
    print(f"Lookback: {run.lookback_days} days | Peaks: {run.total_peaks} | Symbol: {sym_display}")
    print(f"{Fore.YELLOW}Vol-adjusted barrier exits — HIGH(call) K={SWING_K_LOW}/M={SWING_M_LOW} @ 30d, "
          f"LOW(put) K={SWING_K_HIGH}/M={SWING_M_HIGH} @ 30d, scaled sqrt(W/30) per period, "
          f"sigma={SWING_VOL_LOOKBACK}d | 1d=direction only{Style.RESET_ALL}")

    _print_return_peak_table(bucket_rows)
    _print_winrate_table(bucket_rows)
    _print_rtr_winrate_table(bucket_rows)
    _print_swing_table(bucket_rows)
    _print_excursion_table(bucket_rows)
    _print_shakeout_table(bucket_rows)
    _print_ic_table(band_ic_rows)
    _print_correlations(run)
    _print_rtr_correlations(bucket_rows)
    if run.notes:
        print(f"Notes: {run.notes}")
    print()


def _fmt_delta_pair(ov, nv, sell=False):
    if ov is not None and nv is not None:
        d = nv - ov
        color = _color_val(d, sell)
        plain = f"{d:+5.1f}%".rjust(RET_PEAK_PART_W)
        return f"{color}{plain}{Style.RESET_ALL}" if color else plain
    return '--'.rjust(RET_PEAK_PART_W)


def _hdr_delta_pair(label):
    text = f"{label} dR/dPk"
    if len(text) > RET_PEAK_COL_W:
        text = text[:RET_PEAK_COL_W]
    return text.rjust(RET_PEAK_COL_W)


def _print_delta_return_peak_section(old_buckets, new_buckets):
    col_headers = ' | '.join(_hdr_delta_pair(l) for l in PERIOD_LABELS)
    header = f"{'Bucket':>8} | {col_headers}"
    print(f"{Fore.WHITE}Returns delta (close / peak){Style.RESET_ALL}")
    print(header)
    print("-" * len(header))
    for bucket in THRESHOLD_KEYS:
        o, n = old_buckets.get(bucket), new_buckets.get(bucket)
        if not o or not n:
            continue
        sell = _is_sell_bucket(bucket)
        parts = [f"{bucket:>8}"]
        for label in PERIOD_LABELS:
            ovr, nvr = _get(o, f'avg_return_{label}'), _get(n, f'avg_return_{label}')
            ovp, nvp = _get(o, f'avg_peak_{label}'), _get(n, f'avg_peak_{label}')
            parts.append(f"{_fmt_delta_pair(ovr, nvr, sell)}/{_fmt_delta_pair(ovp, nvp, sell)}")
        print(" | ".join(parts))


def _print_delta_section(title, old_buckets, new_buckets, field_prefix):
    col_headers = ' | '.join(f"{'d' + l:>{WIN_COL_W}}" for l in PERIOD_LABELS)
    header = f"{'Bucket':>8} | {col_headers}"
    print(f"{Fore.WHITE}{title}{Style.RESET_ALL}")
    print(header)
    print("-" * len(header))
    for bucket in THRESHOLD_KEYS:
        o, n = old_buckets.get(bucket), new_buckets.get(bucket)
        if not o or not n:
            continue
        parts = [f"{bucket:>8}"]
        for label in PERIOD_LABELS:
            field = f'{field_prefix}_{label}'
            ov, nv = _get(o, field), _get(n, field)
            if ov is not None and nv is not None:
                d = nv - ov
                color = Fore.GREEN if d > 0 else Fore.RED if d < 0 else ''
                plain = f"{d:+6.1f}%".rjust(WIN_COL_W)
                parts.append(f"{color}{plain}{Style.RESET_ALL}")
            else:
                parts.append('--'.rjust(WIN_COL_W))
        print(" | ".join(parts))


def print_delta(label, old_buckets, new_buckets):
    print(f"{Fore.CYAN}=== Delta ({label}) ==={Style.RESET_ALL}")
    _print_delta_return_peak_section(old_buckets, new_buckets)
    _print_delta_section("Win Rates", old_buckets, new_buckets, 'win_rate')
    print()


def compare(arg1=None, arg2=None):
    is_version = arg1 and arg1.lower().startswith('v') and arg1[1:].isdigit()
    is_commit = arg1 and len(arg1) >= 5 and not arg1.isdigit()
    if is_version or is_commit:
        c1 = resolve_version_arg(arg1)
        c2 = resolve_version_arg(arg2) if arg2 else None
        if c1:
            _compare_commits(c1, c2)
    else:
        _compare_runs(int(arg1) if arg1 else None, int(arg2) if arg2 else None)


def _compare_runs(run_id_1=None, run_id_2=None):
    if run_id_1 and run_id_2:
        runs = list(ScoreAssessmentRun.select().where(ScoreAssessmentRun.id.in_([run_id_1, run_id_2])).order_by(ScoreAssessmentRun.id))
    else:
        runs = list(ScoreAssessmentRun.select().order_by(ScoreAssessmentRun.id.desc()).limit(2))
        runs.reverse()

    if len(runs) < 2:
        print(f"{Fore.RED}Need at least 2 assessment runs to compare.{Style.RESET_ALL}")
        return

    for r in runs:
        rows = list(ScoreAssessmentResult.select().where(ScoreAssessmentResult.run == r).order_by(ScoreAssessmentResult.bucket))
        bic = list(ScoreAssessmentBandIC.select().where(ScoreAssessmentBandIC.run == r))
        print_results(r, rows, bic)

    old, new = runs
    old_buckets = {r.bucket: r for r in ScoreAssessmentResult.select().where(ScoreAssessmentResult.run == old)}
    new_buckets = {r.bucket: r for r in ScoreAssessmentResult.select().where(ScoreAssessmentResult.run == new)}
    print_delta(f"run #{new.id} vs #{old.id}", old_buckets, new_buckets)


def _compare_commits(commit1=None, commit2=None):
    if commit1 and commit2:
        commits = [commit1, commit2]
    else:
        current = commit1 or get_git_commit()
        distinct = list(
            ScoreAssessmentRun.select(ScoreAssessmentRun.git_commit)
            .where(ScoreAssessmentRun.git_commit.is_null(False), ScoreAssessmentRun.git_commit != current)
            .distinct().order_by(ScoreAssessmentRun.id.desc()).limit(1)
        )
        if not distinct:
            print(f"{Fore.RED}No other commit versions found to compare against.{Style.RESET_ALL}")
            return
        commits = [distinct[0].git_commit, current]

    agg = []
    for commit in commits:
        a = _aggregate_commit(commit)
        if not a:
            print(f"{Fore.RED}No assessment runs found for commit {commit}.{Style.RESET_ALL}")
            return
        agg.append(a)

    for info, _ in agg:
        _print_meta(info)

    old_info, old_buckets = agg[0]
    new_info, new_buckets = agg[1]
    print_delta(f"{new_info['commit']} vs {old_info['commit']}", old_buckets, new_buckets)


def _aggregate_commit(commit_hash):
    runs = list(ScoreAssessmentRun.select().where(
        ScoreAssessmentRun.git_commit.startswith(commit_hash)
    ))
    if not runs:
        return None

    full_commit = runs[0].git_commit
    all_results = list(ScoreAssessmentResult.select().where(
        ScoreAssessmentResult.run.in_([r.id for r in runs])
    ))

    buckets = {}
    for key in THRESHOLD_KEYS:
        rows = [r for r in all_results if r.bucket == key]
        total_count = sum(r.sample_count for r in rows)
        if total_count == 0:
            buckets[key] = empty_stats()
            buckets[key]['bucket'] = key
            continue

        def wavg(field):
            pairs = [(getattr(r, field), r.sample_count) for r in rows if getattr(r, field) is not None and r.sample_count > 0]
            if not pairs: return None
            return round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs), 2)

        b = {'sample_count': total_count, 'avg_score': wavg('avg_score'), 'bucket': key}
        for label in PERIOD_LABELS:
            for prefix in ['avg_return', 'median_return', 'win_rate', 'win_rate_unscaled',
                           'avg_peak', 'median_peak',
                           'avg_mae', 'median_mae', 'avg_mfe', 'median_mfe',
                           'mfe_p25', 'mfe_p75', 'mfe_p90',
                           'avg_mfe_sigma', 'median_mfe_sigma', 'mfe_sigma_p25', 'mfe_sigma_p75',
                           'capture_ratio',
                           'avg_mae_winner', 'avg_mae_loser',
                           'avg_mae_winner_sigma', 'avg_mae_loser_sigma']:
                b[f'{prefix}_{label}'] = wavg(f'{prefix}_{label}')
        compute_shakeout(b)
        buckets[key] = b

    total_peaks = sum(r.total_peaks for r in runs)
    avg_corrs = {}
    for label in PERIOD_LABELS:
        f = f'correlation_{label}'
        vals = [getattr(r, f) for r in runs if getattr(r, f, None) is not None]
        avg_corrs[f] = round(np.mean(vals), 3) if vals else None

    av = AlgorithmVersion.get_or_none(AlgorithmVersion.git_commit == full_commit)
    info = {
        'commit': full_commit,
        'version_id': av.id if av else None,
        'message': get_git_message(full_commit),
        'notes': av.notes if av else None,
        'run_count': len(runs),
        'total_peaks': total_peaks,
        **avg_corrs,
    }
    return info, buckets


def _aggregate_band_ics(runs):
    """Aggregate band ICs across runs: sample-weighted average per band per period."""
    all_band_ics = list(ScoreAssessmentBandIC.select().where(
        ScoreAssessmentBandIC.run.in_([r.id for r in runs])
    ))
    agg = {}
    for band_label in IC_BAND_LABELS.values():
        rows = [r for r in all_band_ics if r.band == band_label]
        total_n = sum(r.sample_count for r in rows)
        entry = {'band': band_label, 'sample_count': total_n}
        for label in PERIOD_LABELS:
            field = f'ic_{label}'
            pairs = [(getattr(r, field), r.sample_count) for r in rows
                     if getattr(r, field, None) is not None and r.sample_count > 0]
            if pairs:
                entry[field] = round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs), 3)
            else:
                entry[field] = None
        agg[band_label] = entry
    return list(agg.values())


def refresh_meta_cache(version):
    """Re-aggregate all runs for this version and upsert into ScoreAssessmentMeta."""
    from database.trader_database import DB
    DB.create_tables([ScoreAssessmentMeta], safe=True)
    if not version:
        return
    result = _aggregate_commit(version.git_commit)
    if not result:
        return
    info, buckets = result
    ScoreAssessmentMeta.delete().where(ScoreAssessmentMeta.version == version).execute()
    from datetime import datetime as dt
    _meta_prefixes = ['avg_return', 'median_return', 'win_rate', 'win_rate_unscaled',
                      'avg_peak', 'median_peak',
                      'avg_mae', 'median_mae', 'avg_mfe', 'median_mfe',
                      'mfe_p25', 'mfe_p75', 'mfe_p90',
                      'avg_mfe_sigma', 'median_mfe_sigma', 'mfe_sigma_p25', 'mfe_sigma_p75',
                      'capture_ratio',
                      'avg_mae_winner', 'avg_mae_loser',
                      'avg_mae_winner_sigma', 'avg_mae_loser_sigma']
    for key in THRESHOLD_KEYS:
        b = buckets.get(key, {})
        fields = {f: b.get(f) for f in [
            'sample_count', 'avg_score',
            *[f'{p}_{l}' for p in _meta_prefixes for l in PERIOD_LABELS],
            'shakeout_depth',
        ]}
        fields['shakeout_recovery'] = b.get('shakeout_recovery')
        corr_fields = {f'correlation_{l}': info.get(f'correlation_{l}') for l in PERIOD_LABELS}
        ScoreAssessmentMeta.create(
            version=version,
            bucket=key,
            run_count=info['run_count'],
            total_peaks=info['total_peaks'],
            **fields,
            **corr_fields,
            updated_at=dt.now(),
        )


def _print_meta(info):
    msg = f' "{info["message"]}"' if info.get('message') else ''
    v_str = f"v{info['version_id']} | " if info.get('version_id') else ''
    print(f"\n{Fore.CYAN}=== Meta Assessment ({v_str}{info['commit']}, {info['run_count']} runs) ==={Style.RESET_ALL}")
    if msg:
        print(f"{Fore.WHITE}{msg}{Style.RESET_ALL}")


def print_meta_table(info, buckets, band_ic_rows=None):
    _print_meta(info)
    print(f"Total peaks: {info['total_peaks']}")

    bucket_rows = [buckets[k] for k in THRESHOLD_KEYS]
    _print_return_peak_table(bucket_rows)
    _print_winrate_table(bucket_rows)
    _print_rtr_winrate_table(bucket_rows)
    _print_swing_table(bucket_rows)
    _print_excursion_table(bucket_rows)
    _print_shakeout_table(bucket_rows)
    _print_ic_table(band_ic_rows)
    _print_correlations(info)
    _print_rtr_correlations(bucket_rows)
    print()


def print_window_results(window_label, bucketed, corrs, all_results=None):
    print(f"\n{Fore.CYAN}--- {window_label} Window ---{Style.RESET_ALL}")
    band_ic_rows = compute_band_ics(all_results) if all_results else []
    bucket_rows = []
    for key in THRESHOLD_KEYS:
        entries = bucketed[key]
        stats = compute_bucket_stats(entries, is_sell=_is_sell_bucket(key)) or empty_stats()
        stats['bucket'] = key
        compute_shakeout(stats)
        bucket_rows.append(stats)
    total = sum(s['sample_count'] for s in bucket_rows)
    print(f"Peaks: {total}")
    _print_return_peak_table(bucket_rows)
    _print_winrate_table(bucket_rows)
    _print_rtr_winrate_table(bucket_rows)
    _print_swing_table(bucket_rows)
    _print_excursion_table(bucket_rows)
    _print_shakeout_table(bucket_rows)
    _print_ic_table(band_ic_rows)
    _print_correlations(corrs)
    _print_rtr_correlations(bucket_rows)


def meta(version_or_commit=None):
    commit_hash = resolve_version_arg(version_or_commit)
    if not commit_hash:
        print(f"{Fore.RED}Not in a git repository.{Style.RESET_ALL}")
        return

    result = _aggregate_commit(commit_hash)
    if not result:
        print(f"{Fore.RED}No assessment runs found for commit {commit_hash}.{Style.RESET_ALL}")
        return

    info, buckets = result
    runs = list(ScoreAssessmentRun.select().where(
        ScoreAssessmentRun.git_commit.startswith(commit_hash)
    ))
    band_ic_rows = _aggregate_band_ics(runs) if runs else []
    print_meta_table(info, buckets, band_ic_rows)


def run(symbol=None, lookback_days=DEFAULT_LOOKBACK, notes=None, version=None, force=False,
        regime_adjust=False, dte_strategy='30', metric='wr'):
    ScoreAssessmentRun.ensure_schema()
    ScoreAssessmentResult.ensure_schema()
    if version is None:
        version = get_or_create_version()
    sym_key = symbol or ''
    regime_tag = " [regime-adjusted]" if regime_adjust else ""
    dte_tag = f" [DTE={dte_strategy}]" if dte_strategy != '30' else ""
    metric_tag = f" [metric={metric}]" if metric != 'wr' else ""
    print(f"{Fore.CYAN}Using algorithm version {version.production_label} (db:{version.id}, {version.git_commit}){regime_tag}{dte_tag}{metric_tag}{Style.RESET_ALL}")

    # Apply (DTE, metric) preset (mutates SWING_K/M module constants)
    set_dte_strategy(dte_strategy, metric)

    if not force and not regime_adjust:
        existing = ScoreAssessmentRun.get_or_none(
            ScoreAssessmentRun.version == version,
            ScoreAssessmentRun.symbol == sym_key,
            ScoreAssessmentRun.lookback_days == lookback_days,
            ScoreAssessmentRun.dte_strategy == dte_strategy,
            ScoreAssessmentRun.metric == metric,
        )
        if existing:
            _all_rows = list(ScoreAssessmentResult.select().where(
                ScoreAssessmentResult.run == existing
            ))
            _key_order = {k: i for i, k in enumerate(THRESHOLD_KEYS)}
            bucket_rows = sorted(_all_rows, key=lambda r: _key_order.get(r.bucket, 999))
            band_ic_rows = list(ScoreAssessmentBandIC.select().where(
                ScoreAssessmentBandIC.run == existing
            ))
            if bucket_rows:
                print(f"{Fore.YELLOW}Using cached run #{existing.id} ({existing.run_at:%Y-%m-%d %H:%M}). "
                      f"Pass --force to recompute.{Style.RESET_ALL}")
                print_results(existing, bucket_rows, band_ic_rows)
                return existing

    print(f"{Fore.CYAN}Extracting peak scores...{Style.RESET_ALL}")
    peaks = extract_peaks(symbol, lookback_days, version=version, regime_adjust=regime_adjust)
    print(f"Found {len(peaks)} peaks")

    if not peaks:
        print(f"{Fore.YELLOW}No extreme scores found in lookback window.{Style.RESET_ALL}")
        _diagnose_assess_zero_peaks(symbol, lookback_days, version)
        return

    print(f"{Fore.CYAN}Calculating forward returns...{Style.RESET_ALL}")
    results = calculate_forward_returns(peaks)
    print(f"Computed returns for {len(results)} peaks")

    corrs = compute_correlations(results)
    bucketed = bucket_results(results)
    band_ic_data = compute_band_ics(results)

    # Regime-adjust mode: compute and print without DB persistence
    if regime_adjust:
        _print_regime_adjust_results(
            version, sym_key, lookback_days, peaks, results,
            corrs, bucketed, band_ic_data,
        )
        return None

    # Store HIGH-side correlation in DB (most peaks are HIGH-scored; LOW is printed live)
    corr_kwargs = {f'correlation_{label}': (corrs.get(label) or {}).get('high') for label in PERIOD_LABELS}
    try:
        assessment = ScoreAssessmentRun.get(
            ScoreAssessmentRun.version == version,
            ScoreAssessmentRun.symbol == sym_key,
            ScoreAssessmentRun.lookback_days == lookback_days,
            ScoreAssessmentRun.dte_strategy == dte_strategy,
            ScoreAssessmentRun.metric == metric,
        )
    except ScoreAssessmentRun.DoesNotExist:
        assessment = ScoreAssessmentRun.create(
            symbol=sym_key,
            lookback_days=lookback_days,
            total_peaks=len(peaks),
            **corr_kwargs,
            notes=notes,
            git_commit=version.git_commit,
            version=version,
            dte_strategy=dte_strategy,
            metric=metric,
        )
    else:
        ScoreAssessmentResult.delete().where(ScoreAssessmentResult.run == assessment).execute()
        assessment.run_at = datetime.now()
        assessment.total_peaks = len(peaks)
        assessment.notes = notes
        assessment.git_commit = version.git_commit
        for k, v in corr_kwargs.items():
            setattr(assessment, k, v)
        assessment.save()

    bucket_rows = []
    for key in THRESHOLD_KEYS:
        entries = bucketed[key]
        stats = compute_bucket_stats(entries, is_sell=_is_sell_bucket(key)) or empty_stats()
        compute_shakeout(stats)
        row = ScoreAssessmentResult.create(
            run=assessment,
            bucket=key,
            **stats,
        )
        bucket_rows.append(row)

    ScoreAssessmentBandIC.delete().where(ScoreAssessmentBandIC.run == assessment).execute()
    band_ic_rows = []
    for bd in band_ic_data:
        band_ic_rows.append(ScoreAssessmentBandIC.create(run=assessment, **bd))

    print_results(assessment, bucket_rows, band_ic_rows)

    windowed = slice_by_window(results)
    for wlabel, _ in applicable_windows(lookback_days):
        if wlabel in windowed:
            subset = windowed[wlabel]
            print_window_results(wlabel, bucket_results(subset), compute_correlations(subset), subset)

    # Meta cache only stores 30 DTE legacy WR barriers — skip for non-default DTE
    # or non-WR metric. Other (DTE, metric) combos read fresh from ScoreAssessmentRun.
    if dte_strategy == '30' and metric == 'wr':
        refresh_meta_cache(version)
    return assessment


def run_all_windows(symbol=None, lookback_days_list=None, notes=None, version=None, force=True,
                    dte_strategy='30', metric='wr'):
    """Consolidated multi-window assess: extract peaks ONCE for the largest
    window, then derive per-window stats by date-cutoff filter.

    Avoids the 5× redundant peak extraction + forward-return walk that the
    auto-tail loop incurs. Each window still produces its own ScoreAssessmentRun
    + bucket rows in the DB, so the dashboard window-selector keeps working.

    lookback_days_list: list of integer days (e.g. [365, 730, 1095, 1825, 3650]).
                        Defaults to the standard WINDOWS set.
    force: required True (the cache-check-and-skip path is per-window single-run).

    Returns dict {window_label: ScoreAssessmentRun}.
    """
    ScoreAssessmentRun.ensure_schema()
    ScoreAssessmentResult.ensure_schema()
    if version is None:
        version = get_or_create_version()
    if lookback_days_list is None:
        lookback_days_list = [days for _, days in WINDOWS]
    sym_key = symbol or ''

    set_dte_strategy(dte_strategy, metric)
    max_days = max(lookback_days_list)
    dte_tag = f" [DTE={dte_strategy}]" if dte_strategy != '30' else ""
    metric_tag = f" [metric={metric}]" if metric != 'wr' else ""
    print(f"{Fore.CYAN}=== Consolidated assess for {len(lookback_days_list)} windows "
          f"(extracting peaks for max={max_days}d once){dte_tag}{metric_tag} ==={Style.RESET_ALL}")
    print(f"{Fore.CYAN}Using algorithm version {version.production_label} (db:{version.id}, {version.git_commit}){Style.RESET_ALL}")

    print(f"{Fore.CYAN}Extracting peak scores ({max_days}d)...{Style.RESET_ALL}")
    all_peaks = extract_peaks(symbol, max_days, version=version)
    print(f"Found {len(all_peaks)} peaks")
    if not all_peaks:
        print(f"{Fore.YELLOW}No extreme scores found.{Style.RESET_ALL}")
        return {}

    print(f"{Fore.CYAN}Calculating forward returns (cached, single pass)...{Style.RESET_ALL}")
    all_results = calculate_forward_returns(all_peaks)
    print(f"Computed returns for {len(all_results)} peaks")

    today = date.today()
    runs_by_window = {}
    # Process in ascending order so 1y prints first
    for days in sorted(set(lookback_days_list)):
        cutoff = today - timedelta(days=days)
        results_subset = [r for r in all_results if r['date'] >= cutoff]
        wlabel = next((label for label, d in WINDOWS if d == days), f'{days}d')
        # `all_peaks` are Score objects → use `.date` (not `peak_date`, which is HistoricPeak's field).
        n_peaks_subset = sum(1 for p in all_peaks if p.date >= cutoff)
        print(f"\n{Fore.CYAN}── assess {wlabel} ({days}d, {n_peaks_subset} peaks) ──{Style.RESET_ALL}")

        if not results_subset:
            print(f"{Fore.YELLOW}  no peaks in window — skipping{Style.RESET_ALL}")
            continue

        corrs = compute_correlations(results_subset)
        bucketed = bucket_results(results_subset)
        band_ic_data = compute_band_ics(results_subset)

        # Save run + bucket rows (mirrors the per-window persist path in run())
        corr_kwargs = {f'correlation_{label}': (corrs.get(label) or {}).get('high')
                       for label in PERIOD_LABELS}
        try:
            assessment = ScoreAssessmentRun.get(
                ScoreAssessmentRun.version == version,
                ScoreAssessmentRun.symbol == sym_key,
                ScoreAssessmentRun.lookback_days == days,
                ScoreAssessmentRun.dte_strategy == dte_strategy,
                ScoreAssessmentRun.metric == metric,
            )
        except ScoreAssessmentRun.DoesNotExist:
            assessment = ScoreAssessmentRun.create(
                symbol=sym_key,
                lookback_days=days,
                total_peaks=n_peaks_subset,
                **corr_kwargs,
                notes=notes,
                git_commit=version.git_commit,
                version=version,
                dte_strategy=dte_strategy,
                metric=metric,
            )
        else:
            ScoreAssessmentResult.delete().where(ScoreAssessmentResult.run == assessment).execute()
            assessment.run_at = datetime.now()
            assessment.total_peaks = n_peaks_subset
            assessment.notes = notes
            assessment.git_commit = version.git_commit
            for k, v in corr_kwargs.items():
                setattr(assessment, k, v)
            assessment.save()

        bucket_rows = []
        for key in THRESHOLD_KEYS:
            entries = bucketed[key]
            stats = compute_bucket_stats(entries, is_sell=_is_sell_bucket(key)) or empty_stats()
            compute_shakeout(stats)
            bucket_rows.append(ScoreAssessmentResult.create(run=assessment, bucket=key, **stats))

        ScoreAssessmentBandIC.delete().where(ScoreAssessmentBandIC.run == assessment).execute()
        band_ic_rows = []
        for bd in band_ic_data:
            band_ic_rows.append(ScoreAssessmentBandIC.create(run=assessment, **bd))

        # Print only the largest window's full table to keep output manageable
        if days == max_days:
            print_results(assessment, bucket_rows, band_ic_rows)
        else:
            # Compact summary: top + bottom call buckets and a put bucket
            n_total = sum(r.sample_count for r in bucket_rows)
            print(f"  Total peaks: {n_total} | top WR15/30: ", end='')
            for k in ['80+', '75+', '70+', '<25', '<15']:
                br = next((r for r in bucket_rows if r.bucket == k), None)
                if br and br.sample_count:
                    print(f"{k}: {br.win_rate_15d or 0:.1f}/{br.win_rate_30d or 0:.1f} ", end='')
            print()

        runs_by_window[wlabel] = assessment

    if dte_strategy == '30' and metric == 'wr':
        refresh_meta_cache(version)
    return runs_by_window


def _print_regime_adjust_results(version, sym_key, lookback_days, peaks, results,
                                 corrs, bucketed, band_ic_data):
    """Print regime-adjusted assessment results without persisting to DB."""
    sym_display = sym_key or 'ALL'
    print(f"\n{Fore.CYAN}=== Regime-Adjusted Assessment ({version.production_label} db:{version.id} {version.git_commit}) ==={Style.RESET_ALL}")
    print(f"Lookback: {lookback_days} days | Peaks: {len(peaks)} | Symbol: {sym_display}")
    print(f"{Fore.YELLOW}(read-only — results NOT saved to DB){Style.RESET_ALL}")

    bucket_rows = []
    for key in THRESHOLD_KEYS:
        entries = bucketed[key]
        stats = compute_bucket_stats(entries, is_sell=_is_sell_bucket(key)) or empty_stats()
        stats['bucket'] = key
        compute_shakeout(stats)
        bucket_rows.append(stats)

    _print_return_peak_table(bucket_rows)
    _print_winrate_table(bucket_rows)
    _print_rtr_winrate_table(bucket_rows)
    _print_swing_table(bucket_rows)
    _print_excursion_table(bucket_rows)
    _print_shakeout_table(bucket_rows)
    _print_ic_table(band_ic_data)
    _print_correlations(corrs)
    _print_rtr_correlations(bucket_rows)
    print()


def _format_version_date(value):
    """pymysql returns a str (not datetime) for zero/unparseable DATETIMEs."""
    return value.strftime('%Y-%m-%d') if hasattr(value, 'strftime') else str(value)[:10]


def list_versions():
    versions = list(reversed(AlgorithmVersion.production_versions()))
    if not versions:
        print(f"{Fore.YELLOW}No algorithm versions registered yet. Run 'trader assess' to create one.{Style.RESET_ALL}")
        return

    print(f"\n{Fore.CYAN}=== Algorithm Versions ==={Style.RESET_ALL}")
    header = f"{'v':>3} | {'DB':>3} | {'Commit':>7} | {'Date':>10} | {'Runs':>4} | {'80-100 WR30':>11} | {'0-20 WR30':>9} | {'Corr 30d':>8} | Notes"
    print(header)
    print("-" * len(header))

    for v in versions:
        runs = list(ScoreAssessmentRun.select().where(ScoreAssessmentRun.version == v))
        run_count = len(runs)

        wr_buy, wr_sell, corr = '--', '--', '--'
        if runs:
            results = list(ScoreAssessmentResult.select().where(
                ScoreAssessmentResult.run.in_([r.id for r in runs])
            ))
            buy_rows = [r for r in results if r.bucket == '80-100' and r.sample_count > 0]
            sell_rows = [r for r in results if r.bucket == '0-20' and r.sample_count > 0]

            if buy_rows:
                total_w = sum(r.sample_count for r in buy_rows)
                wr = sum(r.win_rate_30d * r.sample_count for r in buy_rows if r.win_rate_30d is not None) / total_w
                color = Fore.GREEN if wr >= 55 else Fore.RED if wr < 45 else Fore.YELLOW
                wr_buy = f"{color}{wr:>6.1f}%{Style.RESET_ALL}"
            if sell_rows:
                total_w = sum(r.sample_count for r in sell_rows)
                wr = sum(r.win_rate_30d * r.sample_count for r in sell_rows if r.win_rate_30d is not None) / total_w
                color = Fore.GREEN if wr < 45 else Fore.RED if wr >= 55 else Fore.YELLOW
                wr_sell = f"{color}{wr:>6.1f}%{Style.RESET_ALL}"

            corr_vals = [r.correlation_30d for r in runs if r.correlation_30d is not None]
            if corr_vals:
                c = np.mean(corr_vals)
                color = Fore.GREEN if c > 0.3 else Fore.RED if c < 0 else Fore.YELLOW
                corr = f"{color}{c:>+.3f}{Style.RESET_ALL}"

        notes = v.notes or v.git_message or '--'
        if len(notes) > 40:
            notes = notes[:37] + '...'
        print(f"{v.production_label:>3} | {v.id:>3} | {v.git_commit:>7} | {_format_version_date(v.created_at):>10} | {run_count:>4} | {wr_buy:>11} | {wr_sell:>9} | {corr:>8} | {notes}")
    print()


def version_notes(version_id, notes_text):
    v = AlgorithmVersion.get_by_production_label(version_id) or AlgorithmVersion.get_or_none(AlgorithmVersion.id == version_id)
    if not v:
        print(f"{Fore.RED}Version {version_id} not found.{Style.RESET_ALL}")
        return
    v.notes = notes_text
    v.save()
    print(f"{Fore.GREEN}Updated {v.production_label} (db:{v.id}) notes: {notes_text}{Style.RESET_ALL}")


def version_revert(version_id):
    v = AlgorithmVersion.get_by_production_label(version_id) or AlgorithmVersion.get_or_none(AlgorithmVersion.id == version_id)
    if not v:
        print(f"{Fore.RED}Version {version_id} not found.{Style.RESET_ALL}")
        return
    if AlgorithmVersion.is_legacy_staging_commit(v.git_commit):
        print(f"{Fore.RED}db:{v.id} is a legacy inactive staging row, not a production version.{Style.RESET_ALL}")
        return
    print(f"\n{Fore.CYAN}To revert to {v.production_label} (db:{v.id}):{Style.RESET_ALL}")
    print(f"  git checkout {v.git_commit}")
    print(f"  trader recalculate")
    print(f"\n{Fore.YELLOW}This will checkout commit {v.git_commit}", end="")
    if v.git_message:
        print(f' ("{v.git_message}")', end="")
    print(f"{Style.RESET_ALL}\n")


# ---------------------------------------------------------------------------
# Signal audit: explain_score_accuracy
# ---------------------------------------------------------------------------

_VERDICT_CORRECT  = 'CORRECT'
_VERDICT_BAD_LUCK = 'BAD_LUCK'
_VERDICT_MISS     = 'MISS'
_VERDICT_PENDING  = 'PENDING'

_CONFIRM_THRESHOLD = 0.45
_WIN_THRESHOLD_PCT = 1.0   # abs% threshold: high wins if window peak > +1% (call touched), low if trough < -1% (put touched)


def _signal_context(ind, score_row, is_high: bool):
    """Return (conf_ratio, raw_detail_str) for a score signal.

    conf_ratio: 0-1, how strongly raw indicators support the score direction.
    raw_detail_str: compact single-line of actual indicator values for agent review.
    """
    if ind is None:
        return 0.0, 'no indicator data'

    points, max_pts = 0, 8
    price  = float(score_row.price)   if score_row.price   is not None else None
    rsi    = float(ind.rsi)           if ind.rsi           is not None else None
    stoch  = float(ind.stoch)         if ind.stoch         is not None else None
    mh     = float(ind.macd_hist)     if ind.macd_hist     is not None else None
    ema50  = float(ind.ema_50)        if ind.ema_50        is not None else None
    upper  = float(ind.upper_band)    if ind.upper_band    is not None else None
    lower  = float(ind.lower_band)    if ind.lower_band    is not None else None
    middle = float(ind.middle_band)   if ind.middle_band   is not None else None

    # BB position: normalised -100 (at lower) to +100 (at upper)
    bb_pos = None
    bb_label = '--'
    if price is not None and upper is not None and lower is not None and middle is not None:
        if upper != middle:
            bb_pos = (price - middle) / (upper - middle) * 100 if price >= middle \
                else (price - middle) / (middle - lower) * 100
        if price >= upper:      bb_label = 'above_upper'
        elif price >= middle:   bb_label = 'above_mid'
        elif price >= lower:    bb_label = 'below_mid'
        else:                   bb_label = 'below_lower'

    ema50_pct = (price - ema50) / ema50 * 100 if price is not None and ema50 else None

    if is_high:
        if rsi is not None:
            points += 2 if rsi > 70 else (1 if rsi > 60 else 0)
        if bb_pos is not None:
            points += 2 if price >= upper else (1 if price >= middle else 0)
        if stoch is not None:
            points += 1 if stoch > 70 else 0
        if mh is not None:
            points += 1 if mh > 0 else 0
        if ema50_pct is not None:
            points += 2 if ema50_pct > 5 else (1 if ema50_pct > 0 else 0)
    else:
        if rsi is not None:
            points += 2 if rsi < 30 else (1 if rsi < 40 else 0)
        if bb_pos is not None:
            points += 2 if price <= lower else (1 if price <= middle else 0)
        if stoch is not None:
            points += 1 if stoch < 30 else 0
        if mh is not None:
            points += 1 if mh < 0 else 0
        if ema50_pct is not None:
            points += 2 if ema50_pct < -5 else (1 if ema50_pct < 0 else 0)

    rsi_s   = f"{rsi:.1f}"      if rsi      is not None else '--'
    stoch_s = f"{stoch:.1f}"    if stoch    is not None else '--'
    mh_s    = f"{mh:+.3f}"      if mh       is not None else '--'
    e50_s   = f"{ema50_pct:+.1f}%" if ema50_pct is not None else '--'
    detail  = f"RSI={rsi_s} Stoch={stoch_s} MACDh={mh_s} BB={bb_label} EMA50={e50_s}"

    return points / max_pts, detail


def _forward_returns_from_db(symbol, signal_date, windows=(7, 15, 30)):
    """Forward price returns and window extremes from PriceHistory.

    Returns {days: pct_change} for close-to-close returns, plus two extra keys:
      'peak_pct'   — max intraday high reached across the full window (% above entry close)
      'trough_pct' — min intraday low reached across the full window (% below entry close, negative)
    These measure the best achievable call/put exit within the window.
    """
    entry_row = PriceHistory.get_or_none(
        PriceHistory.symbol == symbol, PriceHistory.date == signal_date
    )
    if entry_row is None:
        return {}
    entry_close = float(entry_row.close)

    rows = list(
        PriceHistory.select(PriceHistory.date, PriceHistory.close, PriceHistory.high, PriceHistory.low)
        .where(PriceHistory.symbol == symbol, PriceHistory.date > signal_date)
        .order_by(PriceHistory.date)
        .limit(max(windows) + 5)
    )
    result, td = {}, 0
    window_high = entry_close
    window_low  = entry_close
    for row in rows:
        td += 1
        h = float(row.high)
        l = float(row.low)
        if h > window_high:
            window_high = h
        if l < window_low:
            window_low = l
        for w in windows:
            if w not in result and td >= w:
                result[w] = (float(row.close) - entry_close) / entry_close * 100
        if all(w in result for w in windows):
            break
    if entry_close > 0:
        result['peak_pct']   = (window_high - entry_close) / entry_close * 100
        result['trough_pct'] = (window_low  - entry_close) / entry_close * 100
    return result


def _parse_weight_info(score_row):
    raw = getattr(score_row, 'weight_info', None)
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {}


_EXPLAIN_WEIGHT_NESTED_KEYS = {
    'sector_breadth_wave': ('side', 'before', 'after', 'delta', 'crash', 'bull', 'pressure', 'overlay_delta', 'overlay_scale'),
    'daily_volume_authority_wave': ('before', 'after', 'delta', 'lift', 'dampen', 'score_guard', 'authority', 'raw_delta', 'wv_force1'),
}


def _flatten_weight_info(wi, prefix='wi'):
    """Flatten persisted score metadata for CSV/JSONL agent analysis."""
    flat = {}

    def _walk(key_prefix, value):
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                _walk(f"{key_prefix}_{child_key}", child_value)
        elif isinstance(value, (list, tuple)):
            flat[key_prefix] = json.dumps(value, separators=(',', ':'), sort_keys=True)
        else:
            flat[key_prefix] = value

    for key, value in wi.items():
        if isinstance(value, dict):
            expected = _EXPLAIN_WEIGHT_NESTED_KEYS.get(key)
            if expected:
                for child_key in expected:
                    if child_key in value:
                        _walk(f"{prefix}_{key}_{child_key}", value.get(child_key))
                for child_key, child_value in value.items():
                    if child_key not in expected:
                        _walk(f"{prefix}_{key}_{child_key}", child_value)
            else:
                _walk(f"{prefix}_{key}", value)
        else:
            _walk(f"{prefix}_{key}", value)
    return flat


def _safe_json_scalar(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(',', ':'), sort_keys=True)
    return value


def _write_explain_exports(records, output_jsonl=None, output_csv=None):
    if output_jsonl:
        path = Path(output_jsonl)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, default=str, sort_keys=True) + '\n')
    if output_csv:
        path = Path(output_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = []
        seen = set()
        preferred = [
            'symbol', 'date', 'overall', 'signal_type', 'verdict',
            'favorable_extreme_pct', 'ret_7d_pct', 'ret_15d_pct', 'ret_30d_pct',
            'confidence_ratio', 'components', 'volume_signal', 'volume_magnitude',
            'weekly_composite', 'regime_multiplier', 'regime_label', 'breadth_score',
        ]
        for key in preferred:
            if any(key in record for record in records):
                fieldnames.append(key)
                seen.add(key)
        for record in records:
            for key in record:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        with path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for record in records:
                writer.writerow({key: _safe_json_scalar(value) for key, value in record.items()})


def _format_score_modifier_lines(score_row):
    """Return compact score-path/modifier lines from the persisted scoring payload."""
    wi = _parse_weight_info(score_row)
    if not wi:
        return []

    lines = []
    shown_keys = set()
    path_parts = []
    for key, label, fmt in (
        ('pre_regime', 'pre_reg', '{:.0f}'),
        ('pre_boost', 'pre_boost', '{:.0f}'),
        ('w_adj', 'w_adj', '{:+.1f}'),
        ('w_comp', 'w_comp', '{:.0f}'),
        ('w_bias', 'w_bias', '{:+.1f}'),
        ('w_mom', 'w_mom', '{:+.1f}'),
        ('put_regime_mult', 'put_reg', '{:.3f}'),
        ('mis_stress', 'mis_stress', '{:.2f}'),
    ):
        if key in wi and wi.get(key) is not None:
            shown_keys.add(key)
            try:
                path_parts.append(f"{label}=" + fmt.format(float(wi[key])))
            except Exception:
                path_parts.append(f"{label}={wi[key]}")
    if path_parts:
        lines.append("score_path: " + " ".join(path_parts))

    modifier_specs = (
        ('wcf_lift', 'WCF+', '{:+.1f}'),
        ('cwcf_dampen', 'CWCF-', '{:.1f}'),
        ('cwwd_dampen', 'CWWD-', '{:.1f}'),
        ('cswc_dampen', 'CSWC-', '{:.1f}'),
        ('scw_dampen', 'SCW-', '{:.1f}'),
        ('pess_lift', 'PESS+', '{:+.1f}'),
        ('mcd_dampen', 'MCD-', '{:.1f}'),
        ('ich_call_dampen', 'ICHc-', '{:.1f}'),
        ('ich_put_lift', 'ICHp+', '{:+.1f}'),
        ('wvd_lift', 'WVD+', '{:+.1f}'),
        ('wvd_dampen', 'WVD-', '{:.1f}'),
        ('ern_boost', 'ERNx', '{:.3f}'),
        ('cont_lift', 'CONT+', '{:+.1f}'),
        ('exh_damp', 'EXH', '{:.3f}'),
        ('ext_damp', 'EXT', '{:.3f}'),
    )
    mods = []
    for key, label, fmt in modifier_specs:
        if key in wi and wi.get(key) is not None:
            shown_keys.add(key)
            try:
                mods.append(f"{label}=" + fmt.format(float(wi[key])))
            except Exception:
                mods.append(f"{label}={wi[key]}")
    if wi.get('cap_dampened'):
        shown_keys.add('cap_dampened')
        mods.append('CAP')
    if wi.get('pcd_active'):
        shown_keys.add('pcd_active')
        shown_keys.add('pcd_r10sigma')
        pcd = 'PCD'
        if wi.get('pcd_r10sigma') is not None:
            try:
                pcd += f"(r10sig={float(wi['pcd_r10sigma']):+.2f})"
            except Exception:
                pcd += f"(r10sig={wi['pcd_r10sigma']})"
        mods.append(pcd)
    sector = wi.get('sector_breadth_wave')
    if isinstance(sector, dict):
        shown_keys.add('sector_breadth_wave')
        overlay = ""
        if sector.get('overlay_delta') is not None:
            overlay = f" ov={sector.get('overlay_delta')}"
        mods.append(
            "SECTOR("
            f"{sector.get('side', '?')} "
            f"{sector.get('before', '?')}->{sector.get('after', '?')} "
            f"d={sector.get('delta', '?')} "
            f"cr={sector.get('crash', 0)} "
            f"bull={sector.get('bull', 0)}"
            f"{overlay}"
            ")"
        )
    dvaw = wi.get('daily_volume_authority_wave')
    if isinstance(dvaw, dict):
        shown_keys.add('daily_volume_authority_wave')
        mods.append(
            "DVAW("
            f"{dvaw.get('before', '?')}->{dvaw.get('after', '?')} "
            f"d={dvaw.get('delta', '?')} "
            f"lift={dvaw.get('lift', 0)} "
            f"damp={dvaw.get('dampen', 0)} "
            f"auth={dvaw.get('authority', '?')}"
            ")"
        )
    if mods:
        lines.append("modifiers: " + " ".join(mods))

    context = []
    for key, label, fmt in (
        ('kijun_pct', 'kijun', '{:+.1f}%'),
        ('wv_force1', 'wvF1', '{:+.3f}'),
        ('mcd_mcap_b', 'mcapB', '{:.1f}'),
        ('days_to_ern', 'earnD', '{:.0f}'),
        ('cont_sig', 'contSig', '{:.3f}'),
        ('scw_base_dampen', 'scwBase', '{:.2f}'),
        ('scw_scalar', 'scwX', '{:.3f}'),
        ('scw_conf', 'scwConf', '{:.3f}'),
        ('scw_raw_stoch', 'rawStoch', '{:.1f}'),
        ('scw_ext_idx', 'scwExt', '{:.3f}'),
        ('scw_ext_taper', 'scwTaper', '{:.3f}'),
        ('cont_raw_lift', 'contRaw', '{:+.3f}'),
    ):
        if key in wi and wi.get(key) is not None:
            shown_keys.add(key)
            try:
                context.append(f"{label}=" + fmt.format(float(wi[key])))
            except Exception:
                context.append(f"{label}={wi[key]}")
    if context:
        lines.append("modifier_ctx: " + " ".join(context))

    flat_keys = sorted(_flatten_weight_info(wi).keys())
    shown_flat = set()
    for key in shown_keys:
        if isinstance(wi.get(key), dict):
            shown_flat.update(k for k in flat_keys if k.startswith(f"wi_{key}_"))
        else:
            shown_flat.add(f"wi_{key}")
    extra = [key[3:] for key in flat_keys if key not in shown_flat]
    if extra:
        preview = " ".join(extra[:18])
        suffix = f" (+{len(extra) - 18} more)" if len(extra) > 18 else ""
        lines.append("wi_extra_keys: " + preview + suffix)

    return lines


def explain_score_accuracy(symbols=None, days=365, *, high_min=75, low_max=25,
                           output_jsonl=None, output_csv=None, text=True):
    """Per-signal accuracy audit for agent review.

    Finds all score peaks and troughs in the lookback window, then filters to
    the requested signal bands (default CALL >=75 and PUT <=25), then for each
    signal shows:
      • Score components (BB/TR/RSI/MACD/ST/TA) — what drove the overall
      • Volume signal + magnitude
      • Weekly composite at time of signal
      • Raw indicator values (RSI, Stoch, MACDh, BB position, EMA50%)
      • Forward returns 7d/15d/30d
      • Verdict: CORRECT / BAD_LUCK / MISS / PENDING

    Verdict logic (momentum/strength frame — peak/trough based):
      HIGH signal (≥70) wins if window peak high  > +1% above entry (call profit target touched)
      LOW  signal (≤25) wins if window trough low < -1% below entry (put profit target touched)
      BAD_LUCK = wrong outcome but raw indicators confirmed the signal
      MISS     = wrong outcome AND indicators were neutral/contradicting

    Usage:
        trader explain-scores [SYMBOL ...] [days] [--high-min N] [--low-max N]
        trader explain-scores 365 --jsonl .cache/explain.jsonl --csv .cache/explain.csv
    """
    from database.models.technical import Indicator as IndicatorModel
    from database.models.core import WeeklyScore, MarketBreadth

    if symbols:
        sym_list = [s.upper() for s in symbols]
    else:
        sym_list = None

    active_version = AlgorithmVersion.get_active_scores_version()
    peaks = extract_peaks(
        symbol=sym_list[0] if sym_list and len(sym_list) == 1 else None,
        lookback_days=days,
        version=active_version,
    )
    if sym_list and len(sym_list) > 1:
        peaks = [p for p in peaks if p.symbol_id in sym_list]
    peaks = [p for p in peaks if p.overall >= high_min or p.overall <= low_max]

    if not peaks:
        if text:
            print(f"{Fore.YELLOW}No signals found in the last {days} days for >= {high_min} / <= {low_max}.{Style.RESET_ALL}")
        return

    # Batch-load regime + breadth for the full date range (one query each)
    all_dates = [p.date for p in peaks]
    date_min, date_max = min(all_dates), max(all_dates)

    regime_by_date = {
        r.date: r for r in MarketRegime.select(
            MarketRegime.date, MarketRegime.regime_multiplier,
            MarketRegime.regime_composite, MarketRegime.vix_close,
            MarketRegime.vix_score, MarketRegime.market_trend_score,
        ).where(MarketRegime.date >= date_min, MarketRegime.date <= date_max)
    }
    breadth_by_date = {
        b.date: b for b in MarketBreadth.select(
            MarketBreadth.date, MarketBreadth.breadth_score,
            MarketBreadth.mcclellan_oscillator, MarketBreadth.trin,
            MarketBreadth.pct_above_ema50, MarketBreadth.hindenburg_confirmed,
            MarketBreadth.zweig_thrust_active,
        ).where(MarketBreadth.date >= date_min, MarketBreadth.date <= date_max)
    }

    def _regime_label(mult):
        if mult is None:   return '--'
        if mult <= 0.78:   return 'STRESS'
        if mult <= 0.88:   return 'CAUTION'
        if mult <= 1.00:   return 'NEUTRAL'
        if mult <= 1.05:   return 'HEALTHY'
        return 'BULL'

    by_sym = defaultdict(list)
    for p in peaks:
        by_sym[p.symbol_id].append(p)

    total_counts = {v: 0 for v in (_VERDICT_CORRECT, _VERDICT_BAD_LUCK, _VERDICT_MISS, _VERDICT_PENDING)}
    misses = []
    export_records = []

    # Header — line 1 per signal
    hdr = (f"  {'Date':<11} {'Sc':>3}{'T':1}  "
           f"{'[BB TR RS MC ST TA]':<20}  "
           f"{'Vol/Mag':<16}  {'Wk':>3}  "
           f"{'Reg':>7}  {'Brd':>4}  "
           f"{'7d%':>6} {'30d%':>6} {'peak%':>9}  Verdict")
    sep = '-' * len(hdr)

    for sym in sorted(by_sym.keys()):
        sym_peaks = sorted(by_sym[sym], key=lambda p: p.date)
        if text:
            print(f"\n{'='*len(hdr)}")
            print(f"{Fore.CYAN}  {sym}  -  {len(sym_peaks)} signal(s)  |  lookback {days}d  |  >= {high_min} / <= {low_max}{Style.RESET_ALL}")
            print(f"{'='*len(hdr)}")
            print(hdr)
            print(sep)

        sym_counts = {v: 0 for v in (_VERDICT_CORRECT, _VERDICT_BAD_LUCK, _VERDICT_MISS, _VERDICT_PENDING)}

        for score_row in sym_peaks:
            is_high  = score_row.overall >= 70
            sig_type = 'H' if is_high else 'L'
            sig_date = score_row.date

            # Raw indicator confirmation
            ind = IndicatorModel.get_or_none(IndicatorModel.symbol == sym, IndicatorModel.date == sig_date)
            conf_ratio, raw_detail = _signal_context(ind, score_row, is_high)
            confirmed = conf_ratio >= _CONFIRM_THRESHOLD

            # Regime at signal date
            reg = regime_by_date.get(sig_date)
            reg_mult  = float(reg.regime_multiplier)  if reg and reg.regime_multiplier  is not None else None
            reg_comp  = float(reg.regime_composite)   if reg and reg.regime_composite   is not None else None
            reg_vix   = float(reg.vix_close)          if reg and reg.vix_close          is not None else None
            reg_label = _regime_label(reg_mult)
            reg_str   = f"{reg_mult:.2f}/{reg_label}" if reg_mult is not None else '--'

            # Breadth at signal date
            brd = breadth_by_date.get(sig_date)
            brd_score = float(brd.breadth_score)          if brd and brd.breadth_score         is not None else None
            brd_mcosc = float(brd.mcclellan_oscillator)   if brd and brd.mcclellan_oscillator  is not None else None
            brd_trin  = float(brd.trin)                   if brd and brd.trin                  is not None else None
            brd_e50   = float(brd.pct_above_ema50)        if brd and brd.pct_above_ema50       is not None else None
            brd_hind  = brd.hindenburg_confirmed           if brd else False
            brd_zweig = brd.zweig_thrust_active            if brd else False
            brd_s     = f"{brd_score:.0f}" if brd_score is not None else '--'

            # Forward returns + window extremes
            fwd        = _forward_returns_from_db(sym, sig_date, windows=(7, 15, 30))
            ret30      = fwd.get(30)
            peak_pct   = fwd.get('peak_pct')    # max intraday high % above entry
            trough_pct = fwd.get('trough_pct')  # min intraday low  % below entry (negative)

            # Verdict — based on whether the favorable extreme was reached, not the endpoint close
            favorable = peak_pct if is_high else trough_pct
            if favorable is None:
                verdict = _VERDICT_PENDING
            else:
                won = (favorable >= _WIN_THRESHOLD_PCT) if is_high else (favorable <= -_WIN_THRESHOLD_PCT)
                verdict = _VERDICT_CORRECT if won else (_VERDICT_BAD_LUCK if confirmed else _VERDICT_MISS)

            sym_counts[verdict] += 1
            total_counts[verdict] += 1

            # Score components — compact bracket block
            def _c(v): return f"{v:>2}" if v is not None else '--'
            comps = (f"[{_c(score_row.bb)} {_c(score_row.trend)} {_c(score_row.rsi)} "
                     f"{_c(score_row.macd)} {_c(score_row.stoch)} {_c(score_row.technical_alignment)}]")

            # Volume signal + magnitude
            vsig    = score_row.volume_signal or 'NEUTRAL'
            vmag    = score_row.volume_magnitude
            vol_str = f"{vsig}/{vmag:.2f}" if vmag is not None else vsig
            vol_str = vol_str[:16]

            # Weekly composite (most recent Monday ≤ signal date)
            ws = (WeeklyScore.select(WeeklyScore.composite)
                  .where(WeeklyScore.symbol == sym, WeeklyScore.date <= sig_date)
                  .order_by(WeeklyScore.date.desc())
                  .first())
            wkly_s = f"{ws.composite:>3}" if ws and ws.composite is not None else ' --'

            # Return formatter — green if move is favorable for the thesis
            def _fr(v):
                if v is None: return f"{'--':>6}"
                good = (v > 0 and is_high) or (v < 0 and not is_high)
                col  = Fore.GREEN if good else Fore.RED
                return f"{col}{v:>+6.1f}%{Style.RESET_ALL}"

            def _fp(v, favorable_side):
                """Format peak (call) or trough (put) — always shows the key win metric."""
                if v is None: return f"{'--':>6}"
                col = Fore.GREEN if favorable_side else Fore.RED
                return f"{col}{v:>+6.1f}%{Style.RESET_ALL}"

            vcol = {_VERDICT_CORRECT: Fore.GREEN, _VERDICT_BAD_LUCK: Fore.YELLOW,
                    _VERDICT_MISS: Fore.RED, _VERDICT_PENDING: Fore.WHITE}[verdict]

            peak_s = _fp(peak_pct,   peak_pct   is not None and peak_pct   >= _WIN_THRESHOLD_PCT)   if is_high \
                else _fp(trough_pct, trough_pct is not None and trough_pct <= -_WIN_THRESHOLD_PCT)

            # Line 1: score + components + vol + weekly + regime + breadth + 7d/30d close + peak/trough + verdict
            if text:
                print(f"  {sig_date!s:<11} {score_row.overall:>3}{sig_type}  "
                      f"{comps:<20}  {vol_str:<16}  {wkly_s}  "
                      f"{reg_str:>12}  {brd_s:>4}  "
                      f"{_fr(fwd.get(7))} {_fr(ret30)} pk:{peak_s}  "
                      f"{vcol}{verdict}{Style.RESET_ALL}")

            # Line 2: raw indicator values + regime sub-scores + breadth detail + conf
            conf_col  = Fore.GREEN if conf_ratio >= _CONFIRM_THRESHOLD else Fore.RED
            vix_s     = f"VIX={reg_vix:.1f}"   if reg_vix   is not None else ''
            mcosc_s   = f"McOsc={brd_mcosc:+.1f}" if brd_mcosc is not None else ''
            trin_s    = f"TRIN={brd_trin:.2f}"  if brd_trin  is not None else ''
            e50_s     = f"A>EMA50={brd_e50:.0f}%" if brd_e50  is not None else ''
            flags     = ' '.join(f for f in [
                'HINDENBURG' if brd_hind  else '',
                'ZWEIG'      if brd_zweig else '',
            ] if f)
            regime_detail = '  '.join(s for s in [vix_s, mcosc_s, trin_s, e50_s, flags] if s)
            modifier_lines = _format_score_modifier_lines(score_row)
            if text:
                print(f"    {Fore.WHITE}{raw_detail}  conf={conf_col}{conf_ratio:.2f}{Style.RESET_ALL}")
                if regime_detail:
                    print(f"    {Fore.WHITE}{regime_detail}{Style.RESET_ALL}")
                for mod_line in modifier_lines:
                    print(f"    {Fore.WHITE}{mod_line}{Style.RESET_ALL}")

            wi = _parse_weight_info(score_row)
            record = {
                'symbol': sym,
                'date': sig_date.isoformat() if hasattr(sig_date, 'isoformat') else str(sig_date),
                'overall': int(score_row.overall),
                'signal_type': 'CALL' if is_high else 'PUT',
                'verdict': verdict,
                'favorable_extreme_pct': favorable,
                'ret_7d_pct': fwd.get(7),
                'ret_15d_pct': fwd.get(15),
                'ret_30d_pct': ret30,
                'peak_pct': peak_pct,
                'trough_pct': trough_pct,
                'confidence_ratio': round(float(conf_ratio), 4),
                'raw_detail': raw_detail,
                'components': comps,
                'bb': score_row.bb,
                'trend': score_row.trend,
                'rsi': score_row.rsi,
                'macd': score_row.macd,
                'stoch': score_row.stoch,
                'ta': score_row.technical_alignment,
                'volume_signal': score_row.volume_signal or 'NEUTRAL',
                'volume_magnitude': score_row.volume_magnitude,
                'weekly_composite': int(ws.composite) if ws and ws.composite is not None else None,
                'regime_multiplier': reg_mult,
                'regime_label': reg_label,
                'regime_composite': reg_comp,
                'vix_close': reg_vix,
                'breadth_score': brd_score,
                'mcclellan_oscillator': brd_mcosc,
                'trin': brd_trin,
                'pct_above_ema50': brd_e50,
                'modifier_summary': " | ".join(modifier_lines),
            }
            record.update(_flatten_weight_info(wi))
            export_records.append(record)

            if verdict == _VERDICT_MISS:
                misses.append((sym, sig_date, score_row.overall, raw_detail, favorable,
                               comps, vol_str, wkly_s, reg_str, brd_s, regime_detail, is_high,
                               modifier_lines))

        # Per-symbol summary
        n = sum(sym_counts.values())
        if text:
            parts = []
            for v, col in ((_VERDICT_CORRECT, Fore.GREEN), (_VERDICT_BAD_LUCK, Fore.YELLOW),
                           (_VERDICT_MISS, Fore.RED), (_VERDICT_PENDING, Fore.WHITE)):
                cnt = sym_counts[v]
                if cnt:
                    parts.append(f"{col}{v}:{cnt} ({cnt/n*100:.0f}%){Style.RESET_ALL}")
            print(sep)
            print(f"  {Fore.WHITE}Signals:{n}{Style.RESET_ALL}  " + '  '.join(parts))

    # Cross-symbol summary
    total_n = sum(total_counts.values())
    if total_n == 0:
        return
    if text:
        print(f"\n{'='*len(hdr)}")
        print(f"{Fore.CYAN}  OVERALL  -  {total_n} signals  |  {len(by_sym)} symbol(s)  |  lookback {days}d{Style.RESET_ALL}")
        print(f"{'='*len(hdr)}")
        for v, col in ((_VERDICT_CORRECT, Fore.GREEN), (_VERDICT_BAD_LUCK, Fore.YELLOW),
                       (_VERDICT_MISS, Fore.RED), (_VERDICT_PENDING, Fore.WHITE)):
            cnt = total_counts[v]
            pct = cnt / total_n * 100
            bar = '#' * int(pct / 2)
            print(f"  {col}{v:<10}{Style.RESET_ALL}  {cnt:>4} ({pct:>5.1f}%)  {col}{bar}{Style.RESET_ALL}")

        if misses:
            print(f"\n{Fore.RED}  -- MISS log (favorable extreme not reached + indicators did not confirm) --{Style.RESET_ALL}")
            for sym, sig_date, overall, raw_detail, favorable, comps, vol_str, wkly_s, reg_str, brd_s, regime_detail, is_high, modifier_lines in misses:
                fav_s  = f"{favorable:+.1f}%" if favorable is not None else '--'
                side_s = 'peak' if is_high else 'trough'
                print(f"  {sym:<6} {sig_date!s}  score={overall}  {side_s}={fav_s}  "
                      f"wkly={wkly_s.strip()}  reg={reg_str}  brd={brd_s}")
                print(f"    {raw_detail}")
                print(f"    components: {comps}")
                if regime_detail:
                    print(f"    {regime_detail}")
                for mod_line in modifier_lines:
                    print(f"    {mod_line}")
        print()

    _write_explain_exports(export_records, output_jsonl=output_jsonl, output_csv=output_csv)
    if (output_jsonl or output_csv) and text:
        print(f"{Fore.GREEN}Exported {len(export_records)} explain-score record(s).{Style.RESET_ALL}")
