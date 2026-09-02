"""
semivol_r — 60d downside/upside realized-volatility ratio (the live, MC-computable
cousin of option put-skew). High (>1) = downside-heavy = put-skew-like = CHEAP call.

Used by the SVR entry-filter (shipped 2026-06-05, portfolio-stage): downweight call
alloc for the euphoric/EXPENSIVE-call cohort (low semivol_r, the worst per-trade
cohort) and the crash-mode extreme (very-high), favoring the ~0.9-1.1 sweet spot.

Strictly-prior / EOD-known: at index `idx` it uses the 60 daily returns ENDING at
`idx` (i.e. closes[idx-60 .. idx]). Matches the prior-hunt feature build
(experiments/iv_skew/build_proxy.py); a value mismatch vs `.cache/iv_skew/proxy_ledger.parquet`
would invalidate the SVR validation, so keep this formula identical.
"""
import numpy as np

SEMIVOL_WIN = 60


def compute_semivol_r(closes, idx, win: int = SEMIVOL_WIN):
    """Return semivol_r at index `idx`, or None if insufficient/degenerate history.

    closes: sequence of close prices (ascending by date).
    idx:    index of the signal date in `closes`.
    """
    if idx is None or idx < win:
        return None
    seg = np.asarray(closes[idx - win:idx + 1], dtype=float)  # win+1 closes -> win returns
    if seg.shape[0] != win + 1:
        return None
    with np.errstate(divide='ignore', invalid='ignore'):
        ret = seg[1:] / seg[:-1] - 1.0   # the `win` returns ending at idx
    ret = ret[np.isfinite(ret)]          # drop returns from missing/non-finite closes (build_proxy parity)
    if ret.shape[0] < 30:                # build_proxy requires >=30 valid returns
        return None
    up = ret[ret > 0]
    dn = ret[ret < 0]
    if up.shape[0] < 3 or dn.shape[0] < 3:
        return None
    su = float(np.std(up))               # population std (ddof=0), matches build_proxy
    if su <= 1e-9:
        return None
    return float(np.std(dn)) / su
