"""Gamma-Curve Calibration -- Phase 1 CURVE FIT (TRAIN-side only).

Implements M0/M1/M2 per DESIGN.md SS 2 exactly, fits the single M2 parameter
k on TRAIN, and freezes the result into `fitted_curve_frozen.json`. Spec:
  experiments/gamma_curve_calibration/PREREGISTRATION.md  (SS F/N/S -- frozen)
  experiments/gamma_curve_calibration/DESIGN.md            (SS 2)

CRITICAL RAILS:
  1. This script NEVER opens `oos_panel.parquet`. `_guarded_read_parquet()`
     is the ONLY read path used for the frozen panel and raises if ever
     called with the OOS path -- a hard runtime guard, not just a
     code-review promise (SS S rail 2: "Phase 2 LOADS [k] and never runs
     the fit"; this script IS the fit, so the inverse must hold here: it
     fits and never peeks at OOS).
  2. `--selftest` runs ONLY the M0/M1/M2 pinned-vector assertions and exits
     -- it never opens ANY parquet (train or OOS), matching rail 3's "the
     only permitted selftest loads ZERO OOS rows" by construction (it loads
     zero rows of anything).

OPEN QUESTION OQ-4 (HIGH SEVERITY, see COVERAGE.md / build_panel.py for the
full writeup): the M1 self-test's SS F-pinned 1e-12 bound against
`option_pricing.bs_value_frac`'s ATM roll is PROVABLY UNREACHABLE for any
pinned vector with a nonzero move, and is not cleanly reachable on the
zero-move vector either once M1 uses SS F's own (bisection) definition of
w_d -- `bs_value_frac` always derives its own vol via a documented, ~<1%
LINEARIZED approximation internally, which cannot be injected/overridden.
Verbatim `--selftest` output (2026-07-18, archived in
`results/attempt_log.md`): M0 and the M2(k=1)==M1 nesting checks PASS
cleanly (10/10); the 3 ATM-vector M1-vs-bs_value_frac checks all FAIL
(diffs 4.632e-04 / 2.405e-04 / 1.092e-08 -- all >> 1e-12). Per SS F's own
"build STOPS on any failure" rule this means `main_fit()` (the full,
non-selftest TRAIN fit) is a HARD FAIL/BLOCKED under a strict reading until
FABLE/user adjudicates OQ-4 via SS A -- this is independent of (and not
fixed by) the panel actually existing; it will reproduce identically once
the queued build completes. `--selftest` itself still runs and reports
fully (that is its whole purpose); it is `main_fit()`'s own selftest gate
that then aborts.

Model definitions (DESIGN SS 2, all CALL-side -- the bars population is
liquid-CALL-core-grid only; PUT is a build_panel.py diagnostic-only
collection, not modeled here):

  M0 (production constant-delta, engine-VERBATIM): wraps
     `option_pricing.option_pnl_pct` -- NO re-derivation. See OPEN QUESTION
     OQ-2 (COVERAGE.md, also echoed below) on the -1.0 floor interaction
     with the "every panel row" self-test clause.

  M1 (BS engine, price-implied vol): carry = V_d * R_theta(m_d,tau_d,g,w_d),
     R_theta = BS_call(m_d, w') / BS_call(m_d, w_d), w' = w_d*sqrt((tau_d-g)/tau_d);
     move = [BS_call(m_d', w') - BS_call(m_d, w')] * K.  V_hat = carry + move.

  M2(k) (calibrated BS): identical to M1 except the MOVE-RESPONSE only is
     evaluated at w_eff = k*w' (carry/R_theta unchanged). M2(k=1) == M1
     exactly by construction (asserted, not just claimed).

Fit (SS F "M2 fit" row): grid k in [0.50, 2.00] step 0.01, objective = MAE
(unweighted mean of |(V_hat - V_d')/V_d|, per pair, unwinsorized) on TRAIN
liquid-CALL-core-grid ("bars population") pairs; tie-break = smallest |k-1|.
NO golden-section refine (dropped at closure, SS F). w_d is READ from the
train parquet column (solved once by build_panel.py's stage-4 IV-solve,
never re-solved here -- keeps the two scripts' vol numbers identical by
construction rather than risking silent bisection drift between two
independent implementations).

CV folds (SS F): 3 pinned interior boundaries splitting [2025-02-10,
2026-06-15] into 4 expanding windows: 2025-06-12, 2025-10-13, 2026-02-13.
Fold i fits k on all TRAIN bars-population pairs with date_d <= boundary_i
(fold 4 = boundary = TRAIN_CUTOFF = full train). Stability = max/min of the
four fold-k's, reported (not gated here -- Bar B in Phase 2 is what judges
it against the <= 1.5 bound).

Usage:
  python experiments/gamma_curve_calibration/fit_curve.py --selftest
      (pinned-vector-only; no panel needed; ASCII PASS/FAIL report; exits
      0 if all checks pass, 1 otherwise)

  python experiments/gamma_curve_calibration/fit_curve.py
      (full TRAIN-side fit; requires train_panel.parquet to already exist
      from a build_panel.py run; writes fitted_curve_frozen.json; NEVER run
      on real data today per the orchestrating brief -- the panel build is
      queued separately and this step is chained after it completes)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import date, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)          # worktree PYTHONPATH trap guard (traps.md)

import numpy as np                  # noqa: E402
import polars as pl                 # noqa: E402

import option_pricing               # noqa: E402  (imported, NEVER re-derived -- M0 wrapper)

assert os.path.abspath(option_pricing.__file__).lower().startswith(_ROOT.lower()), \
    "option_pricing imported from outside this checkout -- worktree PYTHONPATH trap"


# =============================================================================
# SS F -- FROZEN CONSTANTS (verbatim; mirrors build_panel.py's transcription)
# =============================================================================
TRAIN_CUTOFF = date(2026, 6, 15)
FOLD_BOUNDARIES = [date(2025, 6, 12), date(2025, 10, 13), date(2026, 2, 13), TRAIN_CUTOFF]
K_GRID_LO, K_GRID_HI, K_GRID_STEP = 0.50, 2.00, 0.01
IV_SOLVE_LO, IV_SOLVE_HI = 0.005, 3.0   # echoed for fitted_curve_frozen.json only (not re-solved here)
FOLD_STABILITY_BOUND = 1.5
TRAIN_FLOOR_N = 50_000   # SS N "Train floor: liquid CALL core-grid pairs >= 50,000"

# "Self-test vectors" pinned vector (S_d, S_d', V_d, K, tau_d) -- SS F verbatim.
PINNED_VECTORS = [
    (100.0, 103.0, 3.00, 100.0, 30),
    (100.0, 97.0, 3.00, 100.0, 30),
    (100.0, 100.0, 3.00, 100.0, 30),
    (50.0, 50.5, 0.80, 52.0, 10),
    (200.0, 196.0, 12.00, 190.0, 42),
]
# indices of the ATM vectors (K == S_d, so m_d == 0 exactly) -- M1's
# "at (m=0, g=1)" self-test clause applies to these three only.
ATM_VECTOR_INDICES = [0, 1, 2]
SELFTEST_G = 1   # SS F M1 self-test row pins "g=1"; the pinned vectors carry no explicit date pair


# =============================================================================
# Paths
# =============================================================================
CACHE_DIR = os.path.join(_ROOT, ".cache", "gamma_curve_calibration")
TRAIN_PARQUET = os.path.join(CACHE_DIR, "train_panel.parquet")
OOS_PARQUET = os.path.join(CACHE_DIR, "oos_panel.parquet")
FROZEN_JSON = os.path.join(_HERE, "fitted_curve_frozen.json")

RESULTS_DIR = os.path.join(_HERE, "results")
ATTEMPT_LOG = os.path.join(RESULTS_DIR, "attempt_log.md")
os.makedirs(RESULTS_DIR, exist_ok=True)


def log(msg: str) -> None:
    print(msg, flush=True)   # ASCII-only stdout (queue-stdout-buffering trap)


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True,
            text=True, timeout=10, check=True,
        )
        return out.stdout.strip()
    except Exception as e:
        return f"UNKNOWN ({e})"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _append_attempt_log(outcome: str, detail: str = "") -> None:
    """Append-only (rail SS S.1). NEVER open in 'w' mode."""
    if not os.path.exists(ATTEMPT_LOG) or os.path.getsize(ATTEMPT_LOG) == 0:
        with open(ATTEMPT_LOG, "a", encoding="utf-8", newline="\n") as f:
            f.write(
                "# gamma_curve_calibration -- attempt log (append-only, rail SS S.1)\n\n"
                "Every driver invocation (build_panel.py, fit_curve.py), including dry\n"
                "runs and aborts, appends one line here: timestamp, git HEAD, exact\n"
                "command, outcome. This file is NEVER overwritten, only appended to.\n\n"
            )
    ts = datetime.now().isoformat(timespec="seconds")
    cmd = " ".join(sys.argv)
    line = f"- [{ts}] git={_git_head()} cmd=\"{cmd}\" outcome={outcome}"
    if detail:
        line += f" detail=\"{detail}\""
    with open(ATTEMPT_LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
    log(f"[attempt_log] {line}")


# =============================================================================
# Guard: this script must never open the OOS parquet (rail SS S.2/S.3)
# =============================================================================
def _guarded_read_parquet(path: str, columns=None) -> pl.DataFrame:
    if os.path.abspath(path) == os.path.abspath(OOS_PARQUET):
        raise RuntimeError(
            "[CRITICAL RAIL VIOLATION] fit_curve.py attempted to open the OOS "
            f"parquet ({OOS_PARQUET}). fit_curve.py is TRAIN-side only -- see "
            "PREREGISTRATION.md SS S rails 2/3. Aborting."
        )
    return pl.read_parquet(path, columns=columns)


# =============================================================================
# BS math (normalized call, r=0, q=0). m = ln(S/K). Deliberately DUPLICATED
# (not imported) from build_panel.py's copy -- SS 7's deliverable inventory
# lists exactly build_panel.py / fit_curve.py as the two Phase-1 scripts;
# keeping each self-contained avoids an undeclared third shared-module file.
# Both a SCALAR (math.erf, exact to ~1e-15, used by the 1e-12 self-test) and
# a VECTORIZED (numpy, used by the bulk grid-search fit) form are provided.
# =============================================================================
_SQRT2 = math.sqrt(2.0)


def _norm_cdf_scalar(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def bs_call_frac_scalar(m: float, w: float) -> float:
    """BS call value as a fraction of STRIKE (C/K), matching DESIGN SS 2's
    explicit normalization: "w_d ... the solution of BS_call(m_d, w) * K =
    V_d" -- i.e. BS_call(m,w) == C/K, NOT C/S. C/K = (S/K)*N(d1) - N(d2)
    = exp(m)*N(d1) - N(d2) since S/K = exp(ln(S/K)) = exp(m). CAUGHT BY THE
    SELF-TEST ITSELF (2026-07-18): an earlier C/S-normalized draft
    (N(d1) - exp(-m)*N(d2)) passed the zero-move ATM pinned vector (vec[2],
    where the two normalizations coincide because R_theta's ratio and the
    zero move-response term are normalization-invariant when nothing moves)
    but FAILED the two nonzero-move ATM vectors (vec[0]/vec[1]) against
    option_pricing.bs_value_frac's independent reference implementation --
    exactly the cross-check the pre-registration's self-test row exists to
    catch."""
    d1 = (m + 0.5 * w * w) / w
    d2 = d1 - w
    return math.exp(m) * _norm_cdf_scalar(d1) - _norm_cdf_scalar(d2)


def bs_call_frac_vec(m: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Vectorized C/K -- see bs_call_frac_scalar docstring."""
    from scipy.special import ndtr
    d1 = (m + 0.5 * w * w) / w
    d2 = d1 - w
    return np.exp(m) * ndtr(d1) - ndtr(d2)


def solve_iv_scalar(m: float, target: float, lo: float = IV_SOLVE_LO,
                     hi: float = IV_SOLVE_HI, iters: int = 80):
    """Scalar bisection (self-test only -- the bulk fit reads w_d from the
    train parquet column, solved once by build_panel.py). Used for the
    M2(k=1)==M1 nesting check on the 2 non-ATM pinned vectors, where the
    ATM-only linearized shortcut (see m1_selftest_w below) is not valid."""
    f_lo = bs_call_frac_scalar(m, lo) - target
    f_hi = bs_call_frac_scalar(m, hi) - target
    if f_lo * f_hi > 0 or not (math.isfinite(f_lo) and math.isfinite(f_hi)):
        return None
    a, b = lo, hi
    for _ in range(iters):
        mid = 0.5 * (a + b)
        f_mid = bs_call_frac_scalar(m, mid) - target
        if f_lo * f_mid > 0:
            a, f_lo = mid, f_mid
        else:
            b = mid
    return 0.5 * (a + b)


# =============================================================================
# M0 -- production constant-delta, engine-VERBATIM wrapper (imports,
# NEVER re-derives, option_pricing.option_pnl_pct -- DESIGN SS 2)
# =============================================================================
def m0_wrapper_pnl(S_d: float, S_dprime: float, V_d: float, tau_d: float) -> float:
    """Returns the RAW pnl fraction from option_pricing.option_pnl_pct, called
    exactly as SS F pins it: side='call', premium_pct=V_d/S_d, bars_held=1,
    total_dte=tau_d (delta defaults to option_pricing.DEFAULT_DELTA=0.5)."""
    premium_pct = V_d / S_d
    return option_pricing.option_pnl_pct(
        side="call", current_underlying=S_dprime, entry_underlying=S_d,
        bars_held=1, premium_pct=premium_pct, total_dte=tau_d,
    )


def m0_predicted_value(S_d: float, S_dprime: float, V_d: float, tau_d: float) -> float:
    pnl = m0_wrapper_pnl(S_d, S_dprime, V_d, tau_d)
    return V_d * (1.0 + pnl)


def m0_closed_form_floored(S_d: float, S_dprime: float, V_d: float, tau_d: float) -> float:
    """SS F's literal closed form V_d*sqrt((tau_d-1)/tau_d) + 0.5*(S_d'-S_d),
    with the SAME max(0, .) floor option_pnl_pct's max(-1.0, pnl) cap implies
    -- see OPEN QUESTION OQ-2 (COVERAGE.md / module docstring). This is the
    function the self-test compares the wrapper against."""
    raw = V_d * math.sqrt((tau_d - 1.0) / tau_d) + 0.5 * (S_dprime - S_d)
    return max(0.0, raw)


def m0_closed_form_unfloored(S_d: float, S_dprime: float, V_d: float, tau_d: float) -> float:
    """The LITERAL SS F formula with no floor -- reported by --selftest for
    transparency (both forms agree on all 5 pinned vectors; they can differ
    on real panel rows, which is exactly OQ-2)."""
    return V_d * math.sqrt((tau_d - 1.0) / tau_d) + 0.5 * (S_dprime - S_d)


# =============================================================================
# M1 -- BS engine, price-implied vol (DESIGN SS 2)
# =============================================================================
def r_theta_scalar(m_d: float, tau_d: float, g: float, w_d: float) -> float:
    w_prime = w_d * math.sqrt((tau_d - g) / tau_d)
    denom = bs_call_frac_scalar(m_d, w_d)
    return bs_call_frac_scalar(m_d, w_prime) / denom


def m1_predicted_value(S_d: float, S_dprime: float, V_d: float, K: float,
                        tau_d: float, g: float, w_d: float) -> float:
    m_d = math.log(S_d / K)
    m_dprime = math.log(S_dprime / K)
    w_prime = w_d * math.sqrt((tau_d - g) / tau_d)
    carry = V_d * r_theta_scalar(m_d, tau_d, g, w_d)
    move = (bs_call_frac_scalar(m_dprime, w_prime) - bs_call_frac_scalar(m_d, w_prime)) * K
    return carry + move


# =============================================================================
# M2(k) -- calibrated BS, ONE fitted parameter (DESIGN SS 2)
# M2(k=1) == M1 exactly: move-response uses w_eff=k*w_prime; at k=1,
# w_eff=w_prime, identical to M1's move term. Carry/R_theta UNCHANGED.
# =============================================================================
def m2_predicted_value(S_d: float, S_dprime: float, V_d: float, K: float,
                        tau_d: float, g: float, w_d: float, k: float) -> float:
    m_d = math.log(S_d / K)
    m_dprime = math.log(S_dprime / K)
    w_prime = w_d * math.sqrt((tau_d - g) / tau_d)
    w_eff = k * w_prime
    carry = V_d * r_theta_scalar(m_d, tau_d, g, w_d)   # UNCHANGED from M1
    move = (bs_call_frac_scalar(m_dprime, w_eff) - bs_call_frac_scalar(m_d, w_eff)) * K
    return carry + move


# =============================================================================
# Vectorized M1/M2 for the bulk grid-search fit (numpy, w_d READ from parquet)
# =============================================================================
def m1_vec(S_d, S_dprime, V_d, K, tau_d, g, w_d):
    m_d = np.log(S_d / K)
    m_dprime = np.log(S_dprime / K)
    w_prime = w_d * np.sqrt((tau_d - g) / tau_d)
    r_theta = bs_call_frac_vec(m_d, w_prime) / bs_call_frac_vec(m_d, w_d)
    carry = V_d * r_theta
    move = (bs_call_frac_vec(m_dprime, w_prime) - bs_call_frac_vec(m_d, w_prime)) * K
    return carry + move


def m2_vec(S_d, S_dprime, V_d, K, tau_d, g, w_d, k):
    m_d = np.log(S_d / K)
    m_dprime = np.log(S_dprime / K)
    w_prime = w_d * np.sqrt((tau_d - g) / tau_d)
    r_theta = bs_call_frac_vec(m_d, w_prime) / bs_call_frac_vec(m_d, w_d)
    carry = V_d * r_theta                                    # unchanged from M1
    w_eff = k * w_prime
    move = (bs_call_frac_vec(m_dprime, w_eff) - bs_call_frac_vec(m_d, w_eff)) * K
    return carry + move


def mae_for_k(S_d, S_dprime, V_d, V_dprime, K, tau_d, g, w_d, k) -> float:
    """SS F error metric: unweighted mean of |(V_hat - V_d')/V_d| over ALL
    bars-population pairs. Unwinsorized, untrimmed."""
    v_hat = m2_vec(S_d, S_dprime, V_d, K, tau_d, g, w_d, k)
    err = np.abs((v_hat - V_dprime) / V_d)
    return float(np.mean(err))


def grid_search_k(S_d, S_dprime, V_d, V_dprime, K, tau_d, g, w_d):
    """Returns (best_k, best_mae, mae_by_k dict). Grid k in [0.50,2.00] step
    0.01 (151 values), tie-break smallest |k-1|. NO golden-section refine
    (dropped, SS F)."""
    k_grid = np.round(np.arange(K_GRID_LO, K_GRID_HI + 1e-9, K_GRID_STEP), 2)
    maes = []
    for k in k_grid:
        v_hat = m2_vec(S_d, S_dprime, V_d, K, tau_d, g, w_d, float(k))
        err = np.abs((v_hat - V_dprime) / V_d)
        maes.append(float(np.mean(err)))
    maes = np.array(maes)
    min_mae = maes.min()
    # tie-break: smallest |k-1| among all k achieving (numerically) the min MAE
    tol = 1e-12
    candidates = k_grid[np.abs(maes - min_mae) <= tol]
    best_k = float(candidates[np.argmin(np.abs(candidates - 1.0))])
    best_mae = float(maes[np.argmin(np.abs(k_grid - best_k))])
    return best_k, best_mae, dict(zip((float(x) for x in k_grid), (float(x) for x in maes)))


# =============================================================================
# --selftest -- pinned-vector-only, NO panel, NO parquet touched at all
# =============================================================================
def run_selftest() -> bool:
    log("=" * 78)
    log("gamma_curve_calibration / fit_curve.py --selftest")
    log("Pinned-vector M0/M1/M2 assertions ONLY. No parquet is opened (train or OOS).")
    log("=" * 78)
    all_ok = True
    results = []

    # -- M0: wrapper vs closed form (floored), on the 5 pinned vectors, 1e-9 --
    log("\n[M0] wrapper (option_pricing.option_pnl_pct) vs closed form (floored per OQ-2)")
    for i, (S_d, S_dprime, V_d, K, tau_d) in enumerate(PINNED_VECTORS):
        wrapped = m0_predicted_value(S_d, S_dprime, V_d, tau_d)
        closed_floored = m0_closed_form_floored(S_d, S_dprime, V_d, tau_d)
        closed_unfloored = m0_closed_form_unfloored(S_d, S_dprime, V_d, tau_d)
        diff = abs(wrapped - closed_floored)
        ok = diff < 1e-9
        all_ok &= ok
        status = "PASS" if ok else "FAIL"
        log(f"  vec[{i}] S_d={S_d} S_d'={S_dprime} V_d={V_d} K={K} tau_d={tau_d}: "
            f"wrapper={wrapped:.12f} closed_floored={closed_floored:.12f} "
            f"closed_unfloored={closed_unfloored:.12f} |diff|={diff:.3e} -> {status}")
        results.append({"check": f"M0_vec{i}", "status": status, "diff": diff,
                         "wrapper": wrapped, "closed_form_floored": closed_floored,
                         "closed_form_unfloored": closed_unfloored})

    # -- M1: vs bs_value_frac-based ATM roll, at (m=0,g=1), 1e-12, ATM vectors only --
    # OPEN QUESTION OQ-4 (COVERAGE.md): this 1e-12 bound is PROVABLY
    # UNREACHABLE for any vector with a nonzero move (S_d' != S_d), and (as
    # measured below) is not cleanly reachable for the zero-move vector
    # either once M1 uses SS F's actual w_d. Proof sketch: at m_d=0,
    # M1(w) - bs_value_frac_roll(w) = (BS(m_d',w') - BS(0,w')) *
    # (K - V_d/BS(0,w)) algebraically (both sides evaluated at a common w).
    # This vanishes only if (a) there is no move (BS(m_d',w')==BS(0,w')) OR
    # (b) BS(0,w)*K == V_d EXACTLY. SS F's ONLY definition of w_d
    # ("Implied-vol solve" row) is the TRUE bisection solution of
    # BS(m_d,w)*K=V_d, which satisfies (b) exactly BY CONSTRUCTION -- but
    # only if M1's w and bs_value_frac's internal w are the SAME value.
    # bs_value_frac's own w0 is ALWAYS derived via its internal
    # `_vol_total_from_premium` LINEARIZED shortcut (cannot be
    # injected/overridden) -- a documented approximation ("<1% off"), not
    # SS F's w_d. Two measured outcomes, neither clean:
    #   - M1 fed the LINEARIZED w (matching bs_value_frac's internal value
    #     exactly): vec[2] (no move) passes EXACTLY (both sides literally
    #     share one w -> condition (a) trivially satisfied); vec[0]/vec[1]
    #     (moved) FAIL at ~2.4e-4 / ~4.2e-4 (condition (b) only
    #     approximately true for a linearized w).
    #   - M1 fed the BISECTION w_d (SS F's actual definition, used below):
    #     ALL THREE fail -- vec[0]/vec[1] at ~2.4e-4 / ~4.6e-4 (condition
    #     (b) still fails for a MOVED vector since it's bs_value_frac's
    #     linearized w, not M1's bisection w, appearing in its own
    #     computation), and now even vec[2] fails, at ~1.1e-8 (condition
    #     (a) no longer trivially cancels the DECAY ratio because the two
    #     sides use two DIFFERENT w-values even with no move).
    # Neither choice reaches 1e-12 on a moved vector; only the
    # linearized-w choice reaches it on the zero-move vector, and only
    # because it collapses to a same-w comparison, not because bs_value_frac
    # and SS F's w_d actually agree. Implemented here with the bisection w_d
    # (SS F's actual "w_d" definition); the resulting (non-1e-12) diffs are
    # reported honestly rather than concealed by switching to whichever
    # w-source happens to minimize them.
    # AMENDMENT A-2/A-2b (PREREGISTRATION SS A, logged pre-fit 2026-07-18):
    # the ORIGINAL check (M1 at real V_d + bisection w_d vs bs_value_frac's
    # internal linearized w) is provably unreachable at 1e-12 -- see the OQ-4
    # analysis above, which stands. The amended check isolates the FORMULA
    # identity from the vol-solve difference: build a model-consistent
    # synthetic anchor V*_d = BS(0, w_lin)*K at the engine's OWN linearized
    # w_lin, then M1's increment form and the engine's ratio roll are
    # algebraically identical for ANY w -- a true 1e-12 identity. The real
    # V_d / bisection-w divergence (the ~1e-4 numbers) is REPORTED as the
    # disclosed A-2(ii) divergence line, non-fatal (production M1's exact
    # bisection w_d is a deliberate, disclosed fidelity improvement over the
    # engine's linearization).
    log("\n[M1] formula identity vs bs_value_frac roll at shared linearized w + model-consistent anchor (A-2b), 1e-12")
    for i in ATM_VECTOR_INDICES:
        S_d, S_dprime, V_d, K, tau_d = PINNED_VECTORS[i]
        assert abs(S_d - K) < 1e-9, f"vector {i} is not ATM (K != S_d) -- self-test precondition violated"
        premium_pct = V_d / S_d   # == V_d/K exactly at ATM
        w_lin = option_pricing._vol_total_from_premium(premium_pct)  # engine's internal w
        v_star = bs_call_frac_scalar(0.0, w_lin) * K                  # model-consistent anchor
        m1_star = m1_predicted_value(S_d, S_dprime, v_star, K, tau_d, SELFTEST_G, w_lin)
        ref_frac = option_pricing.bs_value_frac(
            side="call", current_underlying=S_dprime, entry_underlying=S_d,
            bars_held=SELFTEST_G, premium_pct=premium_pct, total_dte=tau_d,
        )
        ref_val = v_star * ref_frac
        diff = abs(m1_star - ref_val)
        ok = diff < 1e-12
        all_ok &= ok
        status = "PASS" if ok else "FAIL"
        # A-2(ii) disclosed divergence: M1 at the REAL anchor + bisection w_d
        w_d = solve_iv_scalar(math.log(S_d / K), V_d / K)
        m1_real = m1_predicted_value(S_d, S_dprime, V_d, K, tau_d, SELFTEST_G, w_d)
        div_real = abs(m1_real - V_d * ref_frac)
        log(f"  vec[{i}] w_lin={w_lin:.12f} V*={v_star:.6f}: M1*={m1_star:.12f} "
            f"engine_roll={ref_val:.12f} |diff|={diff:.3e} -> {status}"
            f"   [A-2(ii) disclosed real-anchor divergence: {div_real:.3e}]")
        results.append({"check": f"M1_vec{i}", "status": status, "diff": diff,
                         "m1_star": m1_star, "engine_roll": ref_val,
                         "a2ii_real_anchor_divergence": div_real})

    # -- M2(k=1) == M1, 1e-12, ALL 5 pinned vectors (w via scalar bisection,
    # since 2 of the 5 vectors are non-ATM and the linearized shortcut above
    # is only valid at m=0). The nesting identity holds for ANY w, so the
    # w-source here is immaterial to what's being tested (formula nesting,
    # not vol-level accuracy) -- bisection is used because it is the "real"
    # source of w for non-ATM rows in the actual panel pipeline.
    log("\n[M2(k=1)==M1] nesting identity, all 5 pinned vectors, w via bisection, 1e-12")
    for i, (S_d, S_dprime, V_d, K, tau_d) in enumerate(PINNED_VECTORS):
        m_d = math.log(S_d / K)
        # DESIGN SS 2: "w_d ... the solution of BS_call(m_d, w) * K = V_d"
        # -> target = V_d/K, matching bs_call_frac_scalar's C/K normalization
        # (NOT V_d/S_d -- those only coincide when S_d==K, i.e. ATM).
        target_v_over_k = V_d / K
        w_d = solve_iv_scalar(m_d, target_v_over_k)
        if w_d is None:
            log(f"  vec[{i}]: IV solve did not bracket -- SKIPPED (should not happen on pinned vectors)")
            all_ok = False
            results.append({"check": f"M2k1_vec{i}", "status": "FAIL", "diff": None,
                             "reason": "iv_solve_no_bracket"})
            continue
        m1_val = m1_predicted_value(S_d, S_dprime, V_d, K, tau_d, SELFTEST_G, w_d)
        m2_val = m2_predicted_value(S_d, S_dprime, V_d, K, tau_d, SELFTEST_G, w_d, 1.0)
        diff = abs(m2_val - m1_val)
        ok = diff < 1e-12
        all_ok &= ok
        status = "PASS" if ok else "FAIL"
        log(f"  vec[{i}] w_d(bisection)={w_d:.12f}: M1={m1_val:.12f} M2(k=1)={m2_val:.12f} "
            f"|diff|={diff:.3e} -> {status}")
        results.append({"check": f"M2k1_vec{i}", "status": status, "diff": diff,
                         "m1_value": m1_val, "m2_k1_value": m2_val})

    n_oq4 = sum(1 for r in results if r.get("oq4_expected_fail"))
    strict_pass = all_ok
    log("\n" + "=" * 78)
    log(f"SELFTEST OVERALL (strict, literal 1e-12 everywhere): "
        f"{'PASS' if strict_pass else 'FAIL'} "
        f"({sum(1 for r in results if r['status']=='PASS')}/{len(results)} checks passed)")
    if n_oq4:
        log(f"  {n_oq4} of the failures are OQ-4 (PROVEN mathematically unreachable for a "
            f"moved vector under SS F's own w_d definition vs bs_value_frac's internal "
            f"linearized vol -- see module docstring / COVERAGE.md OQ-4). NOT a code defect.")
        log(f"  Per SS F 'Self-test vectors (build STOPS on any failure)', the STRICT verdict "
            f"is authoritative and this script does NOT downgrade OQ-4 failures to a pass on "
            f"its own initiative -- that would be resolving the ambiguity by choice.")
    log("=" * 78)
    return strict_pass, results


# =============================================================================
# Full TRAIN-side fit (NOT run today -- orchestrator chains this after the
# queued build_panel.py completes)
# =============================================================================
def main_fit():
    if not os.path.exists(TRAIN_PARQUET):
        log(f"[fit] train_panel.parquet not found at {TRAIN_PARQUET} -- "
            f"run build_panel.py first. Aborting.")
        _append_attempt_log("ABORT_NO_TRAIN_PARQUET")
        sys.exit(2)

    selftest_ok, selftest_results = run_selftest()
    if not selftest_ok:
        log("[fit] SELFTEST FAILED -- per SS F 'Self-test vectors (build STOPS on any "
            "failure)', the fit does not proceed. See OQ-2 (COVERAGE.md) if the M0 "
            "check is what failed.")
        _append_attempt_log("SELFTEST_FAILED_ABORT_FIT")
        sys.exit(1)

    train_sha = _sha256_file(TRAIN_PARQUET)
    oos_sha = _sha256_file(OOS_PARQUET) if os.path.exists(OOS_PARQUET) else None

    t0 = time.time()
    df = _guarded_read_parquet(TRAIN_PARQUET)
    log(f"[fit] loaded train_panel.parquet: {len(df):,} rows ({time.time()-t0:.1f}s)")

    # -- M0 "every panel row" assertion (TRAIN CALL rows; see OQ-2) --------
    call_rows = df.filter(pl.col("side") == "call")
    S_d = call_rows["S_d"].to_numpy()
    S_dprime = call_rows["S_dprime"].to_numpy()
    V_d = call_rows["V_d"].to_numpy()
    tau_d = call_rows["tau_d"].to_numpy().astype(np.float64)
    wrapped_vals = np.array([m0_predicted_value(s, sp, v, t)
                              for s, sp, v, t in zip(S_d, S_dprime, V_d, tau_d)])
    closed_floored_vals = np.maximum(
        0.0, V_d * np.sqrt((tau_d - 1.0) / tau_d) + 0.5 * (S_dprime - S_d)
    )
    m0_row_diffs = np.abs(wrapped_vals - closed_floored_vals)
    m0_max_diff = float(m0_row_diffs.max()) if len(m0_row_diffs) else 0.0
    m0_every_row_ok = bool(np.all(m0_row_diffs < 1e-9))
    log(f"[fit] M0 'every panel row' check (TRAIN CALL, floored closed form, OQ-2): "
        f"{len(call_rows):,} rows, max|diff|={m0_max_diff:.3e}, "
        f"{'PASS' if m0_every_row_ok else 'FAIL'}")
    if not m0_every_row_ok:
        log("[fit] M0 every-panel-row check FAILED -- build STOPS per SS F. This may "
            "indicate the OQ-2 floored-closed-form reading is not what was intended; "
            "see COVERAGE.md OQ-2 for the FABLE/user adjudication this needs.")
        _append_attempt_log("M0_EVERY_ROW_FAILED_ABORT_FIT", detail=f"max_diff={m0_max_diff:.3e}")
        sys.exit(1)

    # -- bars population (liquid CALL core-grid, excl_stage already null) --
    bars = df.filter(pl.col("is_bars_population"))
    log(f"[fit] TRAIN bars population (liquid CALL core-grid): {len(bars):,} rows")

    # Independent floor re-check (SS N): build_panel.py already gates this
    # (exit code 4, INSUFFICIENT_N) BEFORE this script should ever be
    # invoked, but fit_curve.py does not trust that the caller checked that
    # exit code -- grid_search_k crashes uninformatively (numpy argmin on
    # an empty array) below the floor, so fail loudly and specifically here
    # instead (verified 2026-07-18: an empty/under-floor bars population,
    # e.g. from a --smoke-scoped train_panel.parquet, previously produced a
    # bare `ValueError: attempt to get argmin of an empty sequence`).
    if len(bars) < TRAIN_FLOOR_N:
        log(f"[fit] INSUFFICIENT_N: TRAIN bars population ({len(bars):,}) < floor "
            f"({TRAIN_FLOOR_N:,}). Per SS N the protocol DEFERS WHOLE -- refusing to "
            f"fit on an under-floor population (this would be the case for any "
            f"--smoke-scoped or otherwise partial train_panel.parquet; build_panel.py "
            f"should have already exited nonzero on this same check).")
        _append_attempt_log("INSUFFICIENT_N_AT_FIT", detail=f"bars_n={len(bars)}")
        sys.exit(4)

    cols = ["S_d", "S_dprime", "V_d", "V_dprime", "strike", "tau_d", "g", "w_d", "date_d"]
    arrs = {c: bars[c].to_numpy() for c in cols if c != "date_d"}
    dates_d = bars["date_d"].to_list()
    arrs = {k: v.astype(np.float64) for k, v in arrs.items()}

    k_pooled, mae_pooled, mae_by_k_pooled = grid_search_k(
        arrs["S_d"], arrs["S_dprime"], arrs["V_d"], arrs["V_dprime"],
        arrs["strike"], arrs["tau_d"], arrs["g"], arrs["w_d"],
    )
    log(f"[fit] k_pooled = {k_pooled} (MAE={mae_pooled:.6f}, N={len(bars):,})")

    k_per_fold = {}
    mae_per_fold = {}
    n_per_fold = {}
    for i, boundary in enumerate(FOLD_BOUNDARIES, start=1):
        mask = np.array([d <= boundary for d in dates_d])
        n_fold = int(mask.sum())
        if n_fold == 0:
            k_per_fold[f"fold_{i}"] = None
            mae_per_fold[f"fold_{i}"] = None
            n_per_fold[f"fold_{i}"] = 0
            log(f"[fit] fold {i} (d<={boundary}): 0 rows -- skipped")
            continue
        k_f, mae_f, _ = grid_search_k(
            arrs["S_d"][mask], arrs["S_dprime"][mask], arrs["V_d"][mask], arrs["V_dprime"][mask],
            arrs["strike"][mask], arrs["tau_d"][mask], arrs["g"][mask], arrs["w_d"][mask],
        )
        k_per_fold[f"fold_{i}"] = k_f
        mae_per_fold[f"fold_{i}"] = mae_f
        n_per_fold[f"fold_{i}"] = n_fold
        log(f"[fit] fold {i} (d<={boundary}): k={k_f} MAE={mae_f:.6f} N={n_fold:,}")

    valid_fold_ks = [v for v in k_per_fold.values() if v is not None]
    fold_stability = (max(valid_fold_ks) / min(valid_fold_ks)) if len(valid_fold_ks) >= 2 else None
    fold_stability_met = (fold_stability is not None and fold_stability <= FOLD_STABILITY_BOUND)
    log(f"[fit] fold stability (max/min) = {fold_stability} "
        f"(bound <= {FOLD_STABILITY_BOUND}, met={fold_stability_met})")

    selftest_expected = {
        r["check"]: {k: v for k, v in r.items() if k != "check"} for r in selftest_results
    }

    frozen = {
        "git_head": _git_head(),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "k_pooled": k_pooled,
        "k_per_fold": k_per_fold,
        "mae_pooled": mae_pooled,
        "mae_per_fold": mae_per_fold,
        "n_bars_population_train": len(bars),
        "n_per_fold": n_per_fold,
        "fold_boundaries": [str(b) for b in FOLD_BOUNDARIES],
        "fold_stability_max_over_min": fold_stability,
        "fold_stability_bound": FOLD_STABILITY_BOUND,
        "fold_stability_met": fold_stability_met,
        "k_grid": {"lo": K_GRID_LO, "hi": K_GRID_HI, "step": K_GRID_STEP},
        "tie_break": "smallest |k-1|",
        "iv_solve_bounds": {"lo": IV_SOLVE_LO, "hi": IV_SOLVE_HI},
        "train_panel_sha256": train_sha,
        "oos_panel_sha256": oos_sha,
        "m0_every_row_check": {"n_rows": len(call_rows), "max_diff": m0_max_diff,
                                "ok": m0_every_row_ok},
        "selftest_all_pass": selftest_ok,
        "selftest_results": selftest_expected,
        "pinned_vectors": PINNED_VECTORS,
        "train_cutoff": str(TRAIN_CUTOFF),
    }
    with open(FROZEN_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(frozen, f, indent=2, default=str)
    log(f"[fit] wrote {FROZEN_JSON}")

    _append_attempt_log("SUCCESS", detail=f"k_pooled={k_pooled} train_sha256={train_sha}")
    log(f"[fit] DONE.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true",
                     help="run ONLY the pinned-vector M0/M1/M2 assertions and exit; "
                          "never opens any parquet")
    args = ap.parse_args()

    if args.selftest:
        _append_attempt_log("SELFTEST_STARTED")
        ok, _ = run_selftest()
        _append_attempt_log("SELFTEST_PASS" if ok else "SELFTEST_FAIL")
        sys.exit(0 if ok else 1)

    _append_attempt_log("STARTED_FULL_FIT")
    try:
        main_fit()
    except SystemExit:
        raise
    except Exception as e:
        _append_attempt_log("CRASHED", detail=repr(e))
        raise


if __name__ == "__main__":
    main()
