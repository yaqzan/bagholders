"""Phase G — fast active-stack sweep for ICH put-side recalibration.

Uses the Phase F cache at the ICH entry point. Each variant:
  1. Starts from pre_ich score captured from the current stack.
  2. Applies a candidate ICH put lift.
  3. Replays downstream PESS and EARN_BOOST exactly.
  4. Compares candidate final scores against old_final from the same cache.

This avoids the misleading DB-vs-simulator comparison and avoids the ~17 minute
full simulator run per candidate.
"""
from __future__ import annotations

import io
import math
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = ROOT / ".cache" / "ich_put_recal" / "phase_f_active_ich_entry_v46_1825d.parquet"
RESULTS_PATH = ROOT / ".cache" / "ich_put_recal" / "phase_g_active_sweep_results.parquet"

PUT_GATE = 25
YEARS = 1825 / 365.0
N_FLOORS_PER_YEAR = {"le15": 196, "p16_20": 398, "p21_25": 541}


def _log_ramp(x, sat):
    x = np.maximum(0.0, x)
    if sat <= 0:
        return np.zeros_like(x, dtype=float)
    return np.clip(np.log1p(x) / math.log1p(sat), 0.0, 1.0)


def _linear_ramp(x, sat):
    if sat <= 0:
        return np.zeros_like(x, dtype=float)
    return np.clip(np.maximum(0.0, x) / sat, 0.0, 1.0)


def _build_boost_table():
    import database.utils.scoring as scoring

    table = np.zeros((101, 6), dtype=float)
    for overall in range(101):
        for d in range(6):
            table[overall, d] = scoring.compute_earnings_boost_strength(overall, d)
    return table


def _apply_downstream(scores, days_to_earnings, boost_table):
    """Replay PESS + EARN_BOOST after candidate ICH."""
    import database.utils.scoring as s

    out = scores.astype(np.int16).copy()
    days = days_to_earnings
    has_days = days >= 0

    # PESS
    pess = (
        (out >= s.PESS_GATE_LO) & (out <= s.PESS_GATE_HI) &
        has_days & (days >= s.PESS_DAYS_MIN) & (days <= s.PESS_DAYS_MAX)
    )
    if np.any(pess):
        score_grad = np.zeros_like(out, dtype=float)
        mid_lo = s.PESS_GATE_LO + 1
        lo_mask = pess & (out <= mid_lo)
        hi_mask = pess & (out > mid_lo)
        score_grad[lo_mask] = np.maximum(
            0.0, (out[lo_mask] - (s.PESS_GATE_LO - s.PESS_FADE_WIDTH)) / s.PESS_FADE_WIDTH
        )
        score_grad[hi_mask] = np.maximum(
            0.0, ((s.PESS_GATE_HI + s.PESS_FADE_WIDTH) - out[hi_mask]) / s.PESS_FADE_WIDTH
        )
        score_grad = np.minimum(1.0, score_grad)
        prox = np.zeros_like(out, dtype=float)
        full = pess & (days <= s.PESS_PROXIMITY_FULL_DAY)
        fade = pess & ~full
        prox[full] = 1.0
        prox_range = max(1, s.PESS_PROXIMITY_FADE_END - s.PESS_PROXIMITY_FULL_DAY)
        prox[fade] = np.maximum(0.0, (s.PESS_PROXIMITY_FADE_END - days[fade]) / prox_range)
        lift = s.PESS_K * score_grad * prox * (s.PESS_TARGET - out)
        out = np.rint(np.clip(out + np.maximum(0.0, lift), 0, 100)).astype(np.int16)

    # EARN_BOOST (days are only 0..5 in simulator path).
    boost_mask = has_days & (days <= 5)
    if np.any(boost_mask):
        idx_scores = np.clip(out[boost_mask], 0, 100).astype(np.int16)
        idx_days = days[boost_mask].astype(np.int16)
        strength = boost_table[idx_scores, idx_days]
        if np.any(strength > 0):
            boosted = 50 + (idx_scores - 50) * (1.0 + strength)
            out_boost = out[boost_mask].copy()
            out_boost = np.rint(np.clip(np.where(strength > 0, boosted, out_boost), 0, 100)).astype(np.int16)
            out[boost_mask] = out_boost
    return out


def _apply_hybrid(pre, kij, params):
    """Hybrid shape: preserve deep tail, current-like middle, boundary asymK."""
    (
        tail_floor,
        mid_cut,
        mid_scale,
        boundary_k,
        boundary_power,
        boundary_lo,
        boundary_hi,
        sat,
        target,
    ) = params
    out = pre.astype(np.int16).copy()
    has_kij = kij < 0
    ind = _log_ramp(-kij, sat)

    # Current-like middle lift, but disabled at/below tail_floor.
    mid = has_kij & (pre > tail_floor) & (pre <= mid_cut)
    if np.any(mid):
        sg = _log_ramp(27 - pre[mid], 17)
        lift = 0.278 * mid_scale * sg * ind[mid] * (33.4 - pre[mid])
        out[mid] = np.rint(np.clip(pre[mid] + np.maximum(0.0, lift), 0, 100)).astype(np.int16)

    # Boundary asymK lift. If overlap exists, boundary overwrites middle by
    # design only for rows above mid_cut.
    b = has_kij & (pre > max(boundary_lo, mid_cut)) & (pre <= boundary_hi)
    if np.any(b):
        norm = np.clip((pre[b] - boundary_lo) / max(1, boundary_hi - boundary_lo), 0.0, 1.0)
        k_eff = boundary_k * (norm ** boundary_power)
        lift = k_eff * ind[b] * (target - pre[b])
        out[b] = np.rint(np.clip(pre[b] + np.maximum(0.0, lift), 0, 100)).astype(np.int16)

    return out


def _metrics(final_scores, wr, baseline):
    sub = final_scores <= PUT_GATE
    if not np.any(sub):
        return None
    out = {
        "n_le25": int(sub.sum()),
        "wr_le25": float(np.nanmean(wr[sub]) * 100.0),
    }
    for lo, hi, key in [
        (0, 5, "le5"),
        (0, 10, "le10"),
        (0, 15, "le15"),
        (16, 20, "p16_20"),
        (21, 25, "p21_25"),
    ]:
        m = (final_scores >= lo) & (final_scores <= hi)
        out[f"n_{key}"] = int(m.sum())
        out[f"wr_{key}"] = float(np.nanmean(wr[m]) * 100.0) if np.any(m) else None

    out["lift_le25"] = out["wr_le25"] - baseline["wr_le25"]
    out["w4_pass"] = (
        out["wr_le5"] is not None and out["wr_le5"] >= baseline["wr_le5"] - 0.30 and
        out["wr_le10"] is not None and out["wr_le10"] >= baseline["wr_le10"] - 0.30 and
        out["wr_le15"] is not None and out["wr_le15"] >= baseline["wr_le15"] - 0.30 and
        out["wr_p16_20"] is not None and out["wr_p16_20"] >= baseline["wr_p16_20"] - 0.30 and
        out["wr_p21_25"] is not None and out["wr_p21_25"] >= baseline["wr_p21_25"] - 0.30
    )
    out["w5_pass"] = (
        (out["n_le15"] / YEARS) >= N_FLOORS_PER_YEAR["le15"] and
        (out["n_p16_20"] / YEARS) >= N_FLOORS_PER_YEAR["p16_20"] and
        (out["n_p21_25"] / YEARS) >= N_FLOORS_PER_YEAR["p21_25"]
    )
    return out


def _lhs(n, ranges, seed=7):
    rng = np.random.default_rng(seed)
    cols = []
    for lo, hi in ranges:
        perm = rng.permutation(n)
        u = (perm + rng.random(n)) / n
        cols.append(lo + (hi - lo) * u)
    return np.stack(cols, axis=1)


def main():
    if not CACHE_PATH.exists():
        raise FileNotFoundError(f"missing Phase F cache: {CACHE_PATH}")
    df = pl.read_parquet(CACHE_PATH)
    from experiments._holdout import assert_no_holdout_leak

    assert_no_holdout_leak(df, context="phase_g_active_sweep cache")
    df = df.filter(pl.col("wr7").is_not_null())
    print(f"[cache] loaded {len(df):,} rows")

    pre = df["pre_ich"].to_numpy().astype(np.int16)
    kij = df["kijun_pct"].fill_null(999.0).to_numpy().astype(float)
    days = df["days_to_earnings"].fill_null(-1).to_numpy().astype(np.int16)
    old = df["old_final"].to_numpy().astype(np.int16)
    wr7 = df["wr7"].to_numpy().astype(float)
    boost_table = _build_boost_table()

    baseline = _metrics(old, wr7, {"wr_le25": 0, "wr_le5": 0, "wr_le10": 0, "wr_le15": 0, "wr_p16_20": 0, "wr_p21_25": 0})
    print("\nBaseline (current code old_final):")
    for key in ["le5", "le10", "le15", "p16_20", "p21_25", "le25"]:
        print(f"  {key:>7}: WR7={baseline[f'wr_{key}']:.2f}% N={baseline[f'n_{key}']:,}")

    # Include hand-picked probes plus LHS.
    probes = [
        # tail_floor, mid_cut, mid_scale, bK, bPow, bLo, bHi, sat, target
        (10, 20, 1.00, 0.42, 2.12, 18, 25, 10.7, 33.4),
        (10, 20, 1.00, 0.55, 0.60, 10, 25, 6.0, 33.4),
        (10, 18, 1.00, 0.42, 2.12, 18, 25, 10.7, 33.4),
        (8, 20, 0.90, 0.35, 1.00, 15, 25, 8.8, 33.4),
        (10, 20, 1.20, 0.30, 1.50, 18, 25, 8.8, 33.4),
    ]
    ranges = [
        (5, 12),       # tail_floor
        (15, 22),      # mid_cut
        (0.60, 1.35),  # mid_scale
        (0.05, 0.65),  # boundary_k
        (0.35, 3.50),  # boundary_power
        (12, 22),      # boundary_lo
        (24, 30),      # boundary_hi
        (4.0, 14.0),   # sat
        (28.0, 40.0),  # target
    ]
    raw = _lhs(300, ranges, seed=17)
    variants = []
    for row in raw:
        tail_floor = int(round(row[0]))
        mid_cut = int(round(row[1]))
        boundary_lo = int(round(row[5]))
        boundary_hi = int(round(row[6]))
        if boundary_hi <= boundary_lo + 1 or mid_cut >= boundary_hi:
            continue
        variants.append((
            tail_floor, mid_cut, float(row[2]), float(row[3]), float(row[4]),
            boundary_lo, boundary_hi, float(row[7]), float(row[8])
        ))
    variants = probes + variants

    rows = []
    t0 = time.time()
    for i, params in enumerate(variants, 1):
        ich = _apply_hybrid(pre, kij, params)
        final = _apply_downstream(ich, days, boost_table)
        m = _metrics(final, wr7, baseline)
        if m is None:
            continue
        m.update({
            "tail_floor": params[0],
            "mid_cut": params[1],
            "mid_scale": params[2],
            "boundary_k": params[3],
            "boundary_power": params[4],
            "boundary_lo": params[5],
            "boundary_hi": params[6],
            "sat": params[7],
            "target": params[8],
        })
        rows.append(m)
        if i % 50 == 0:
            print(f"  variant {i}/{len(variants)} ({time.time() - t0:.1f}s)", flush=True)

    res = pl.DataFrame(rows)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    res.write_parquet(RESULTS_PATH)
    print(f"\n[cache] wrote {RESULTS_PATH}: {len(res):,} variants")

    passed = res.filter(pl.col("w4_pass") & pl.col("w5_pass"))
    print(f"W4+W5 pass: {len(passed)} / {len(res)}")
    top = (passed if len(passed) else res).sort("lift_le25", descending=True).head(15)

    print("\nTop candidates:")
    print(
        f"{'lift':>7} {'wr25':>7} {'n25':>6} {'wr15':>7} {'wr16-20':>8} "
        f"{'wr21-25':>8} {'tail':>4} {'mid':>4} {'mS':>5} {'bK':>5} {'bP':>5} {'bLo':>4} {'bHi':>4} {'sat':>5} {'tgt':>5} {'pass':>6}"
    )
    for r in top.iter_rows(named=True):
        print(
            f"{r['lift_le25']:+7.2f} {r['wr_le25']:7.2f} {r['n_le25']:6d} "
            f"{r['wr_le15']:7.2f} {r['wr_p16_20']:8.2f} {r['wr_p21_25']:8.2f} "
            f"{r['tail_floor']:4d} {r['mid_cut']:4d} {r['mid_scale']:5.2f} "
            f"{r['boundary_k']:5.2f} {r['boundary_power']:5.2f} "
            f"{r['boundary_lo']:4d} {r['boundary_hi']:4d} {r['sat']:5.1f} {r['target']:5.1f} "
            f"{'Y' if r['w4_pass'] and r['w5_pass'] else 'N':>6}"
        )


if __name__ == "__main__":
    main()
