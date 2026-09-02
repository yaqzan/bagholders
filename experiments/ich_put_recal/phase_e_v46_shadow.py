"""Phase E — exact active-stack shadow validation for ICH put asymK.

Runs the current scoring stack with only the ICH put-side block changed in
memory. No production files are modified. Baseline is the active v46 DB score
table; candidate scores are produced through ScoreSimulator's normal default
path so it receives current context kwargs (PCD, MCD, PESS, earnings boost,
put regime multiplier, ret10d sigma, kijun_pct, etc.).

This is the pre-ship check required after the Phase B/C isolation on v43.
"""
from __future__ import annotations

import inspect
import io
import json
import os
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
LOOKBACK_DAYS = 1825
PUT_MAX = 30
PUT_GATE = 25
W_DAYS = 7

# Candidate from Phase B top W4-pass result.
ICH_ASYMK_K = float(os.environ.get("ICH_ASYMK_K", "0.42"))
ICH_ASYMK_POWER = float(os.environ.get("ICH_ASYMK_POWER", "2.12"))
ICH_ASYMK_GATE_LO = int(os.environ.get("ICH_ASYMK_GATE_LO", "18"))
ICH_ASYMK_GATE_HI = int(os.environ.get("ICH_ASYMK_GATE_HI", "25"))
ICH_ASYMK_KIJ_SAT = float(os.environ.get("ICH_ASYMK_KIJ_SAT", "10.7"))
ICH_ASYMK_TARGET = float(os.environ.get("ICH_ASYMK_TARGET", "33.4"))

N_FLOORS_PER_YEAR = {
    "p_le15": 196,
    "p16_20": 398,
    "p21_25": 541,
}


def _s(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)[:10]


def _cutoff_window():
    from experiments._holdout import CUTOFF

    end = min(date.today(), CUTOFF)
    start = end - timedelta(days=LOOKBACK_DAYS)
    return start, end


def _load_baseline_scores(start: date, end: date) -> pl.DataFrame:
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB

    av = AlgorithmVersion.get_active_scores_version()
    cur = DB.execute_sql(
        """
        SELECT symbol, date, overall
        FROM scores
        WHERE version_id = %s
          AND date >= %s
          AND date <= %s
          AND overall <= %s
        """,
        (av.id, start.isoformat(), end.isoformat(), PUT_MAX),
    )
    rows = cur.fetchall()
    df = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [_s(r[1]) for r in rows],
            "baseline_score": [int(r[2]) for r in rows],
        }
    )
    from experiments._holdout import assert_no_holdout_leak

    assert_no_holdout_leak(df, context="phase_e_v46_shadow baseline scores")
    print(f"[baseline] active v{av.id}: {len(df):,} put rows <= {PUT_MAX}")
    return df


def _load_wr7(symbols: list[str]) -> pl.DataFrame:
    placeholders = ",".join("?" for _ in symbols)
    conn = sqlite3.connect(str(ROOT / ".cache" / "barrier_outcomes.db"))
    try:
        cur = conn.execute(
            f"""
            SELECT symbol, date, result
            FROM barrier_outcomes
            WHERE side = 'high'
              AND w_days = {W_DAYS}
              AND barrier_set = '30dte_generic'
              AND symbol IN ({placeholders})
            """,
            symbols,
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    df = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [_s(r[1]) for r in rows],
            "wr7": [int(r[2]) if r[2] is not None else None for r in rows],
        }
    ).filter(pl.col("wr7").is_not_null())
    from experiments._holdout import assert_no_holdout_leak

    assert_no_holdout_leak(df, context="phase_e_v46_shadow wr7 outcomes")
    return df


def _build_variant_compute():
    """Return compute_overall_score with only the ICH put block replaced."""
    import database.utils.scoring as scoring

    src = inspect.getsource(scoring.compute_overall_score)
    old = """        # PUT side: log ramp on both score zone and indicator (Phase C Rank #1)
        if (ICH_GATE_PUT_HI > ICH_GATE_PUT_LO
                and overall <= ICH_GATE_PUT_HI):
            _sg_p = _ich_ramp(ICH_GATE_PUT_HI - overall,
                              ICH_GATE_PUT_HI - ICH_GATE_PUT_LO,
                              ICH_IND_RAMP_PUT)
            _ig_p = _ich_ramp(_ich_ind_dist, ICH_KIJ_SAT_PUT, ICH_IND_RAMP_PUT)
            _w_p = _sg_p * _ig_p
            if _w_p > 0.0:
                _ich_put_l = ICH_K_PUT * _w_p * (ICH_TARGET_PUT - overall)
                if _ich_put_l > 0:
                    overall = int(max(0, min(100, round(overall + _ich_put_l))))
"""
    new = f"""        # PUT side: asymmetric-K boundary dampener (ICH put recal Phase E).
        # Concentrates lift near the put admission boundary and leaves the
        # deep tail untouched; calibrated by experiments/ich_put_recal.
        if (overall <= {ICH_ASYMK_GATE_HI}
                and overall > {ICH_ASYMK_GATE_LO}):
            _score_range_p = max(1, {ICH_ASYMK_GATE_HI} - {ICH_ASYMK_GATE_LO})
            _score_norm_p = max(0.0, min(1.0,
                (overall - {ICH_ASYMK_GATE_LO}) / _score_range_p))
            _k_eff_p = {ICH_ASYMK_K} * (_score_norm_p ** {ICH_ASYMK_POWER})
            _ig_p = _ich_ramp(_ich_ind_dist, {ICH_ASYMK_KIJ_SAT}, ICH_IND_RAMP_PUT)
            if _k_eff_p > 0.0 and _ig_p > 0.0:
                _ich_put_l = _k_eff_p * _ig_p * ({ICH_ASYMK_TARGET} - overall)
                if _ich_put_l > 0:
                    overall = int(max(0, min(100, round(overall + _ich_put_l))))
"""
    count = src.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one ICH put block to replace, found {count}")
    src = src.replace(old, new)

    ns = dict(scoring.__dict__)
    exec(src, ns)
    return ns["compute_overall_score"]


def _simulate_candidate_scores(start: date) -> pl.DataFrame:
    import database.utils.scoring as scoring
    from simulator import ScoreSimulator

    key = (
        f"k{ICH_ASYMK_K:.3f}_p{ICH_ASYMK_POWER:.3f}_"
        f"lo{ICH_ASYMK_GATE_LO}_hi{ICH_ASYMK_GATE_HI}_"
        f"sat{ICH_ASYMK_KIJ_SAT:.3f}_t{ICH_ASYMK_TARGET:.3f}"
    ).replace(".", "p")
    out_path = ROOT / ".cache" / "ich_put_recal" / f"phase_e_v46_candidate_scores_{key}.parquet"
    if out_path.exists():
        df = pl.read_parquet(out_path)
        from experiments._holdout import assert_no_holdout_leak

        assert_no_holdout_leak(df, context="phase_e_v46_shadow cached candidate scores")
        print(f"[candidate] cache hit: {out_path} ({len(df):,} put rows <= {PUT_MAX})")
        return df

    original = scoring.compute_overall_score
    variant = _build_variant_compute()
    t0 = time.time()
    scoring.compute_overall_score = variant
    try:
        sim = ScoreSimulator(symbols=None, lookback_days=LOOKBACK_DAYS)
        scores = sim.simulate(since=start)
    finally:
        scoring.compute_overall_score = original

    rows = [
        (sym, _s(dt), int(overall))
        for (sym, dt), overall in scores.items()
        if overall is not None and int(overall) <= PUT_MAX
    ]
    df = pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "candidate_score": [r[2] for r in rows],
        }
    )
    from experiments._holdout import assert_no_holdout_leak

    assert_no_holdout_leak(df, context="phase_e_v46_shadow candidate scores")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_path)
    print(f"[candidate] wrote checkpoint: {out_path}")
    print(f"[candidate] simulated {len(scores):,} rows; {len(df):,} put rows <= {PUT_MAX} ({time.time() - t0:.1f}s)")
    return df


def _summarize(df: pl.DataFrame, score_col: str, label: str) -> dict:
    sub = df.filter(pl.col(score_col) <= PUT_GATE)
    out = {
        "label": label,
        "n": len(sub),
        "wr7": float(sub["wr7"].mean()) * 100 if len(sub) else None,
    }
    for lo, hi, key in [
        (0, 5, "le5"),
        (0, 10, "le10"),
        (0, 15, "le15"),
        (0, 20, "le20"),
        (0, 25, "le25"),
        (16, 20, "p16_20"),
        (21, 25, "p21_25"),
    ]:
        b = sub.filter(pl.col(score_col).is_between(lo, hi))
        out[f"n_{key}"] = len(b)
        out[f"wr_{key}"] = float(b["wr7"].mean()) * 100 if len(b) else None
    return out


def _print_summary(base: dict, cand: dict):
    print()
    print("=" * 112)
    print("EXACT v46 ACTIVE-STACK SHADOW — baseline DB vs asymK candidate")
    print("=" * 112)
    print(f"{'bucket':>10} | {'base WR/N':>18} | {'candidate WR/N':>18} | {'ΔWR':>8} | {'ΔN':>8}")
    print("-" * 112)
    for key, name in [
        ("le5", "<=5"),
        ("le10", "<=10"),
        ("le15", "<=15"),
        ("le20", "<=20"),
        ("le25", "<=25"),
        ("p16_20", "16-20"),
        ("p21_25", "21-25"),
    ]:
        bw, bn = base[f"wr_{key}"], base[f"n_{key}"]
        cw, cn = cand[f"wr_{key}"], cand[f"n_{key}"]
        dwr = (cw - bw) if bw is not None and cw is not None else None
        print(
            f"{name:>10} | "
            f"{(f'{bw:6.2f}/{bn:5d}' if bw is not None else f'   --/{bn:5d}'):>18} | "
            f"{(f'{cw:6.2f}/{cn:5d}' if cw is not None else f'   --/{cn:5d}'):>18} | "
            f"{(f'{dwr:+7.2f}' if dwr is not None else '     --'):>8} | "
            f"{(cn - bn):+8d}"
        )

    years = LOOKBACK_DAYS / 365.0
    print()
    print("N-FLOW CHECK (offered/year floors):")
    for key, label in [("le15", "p_le15"), ("p16_20", "p16_20"), ("p21_25", "p21_25")]:
        annual = cand[f"n_{key}"] / years
        floor = N_FLOORS_PER_YEAR[label]
        status = "PASS" if annual >= floor else "FAIL"
        print(f"  {label:>7}: {annual:7.1f}/yr  floor={floor:4d}  {status}")


def main():
    start, end = _cutoff_window()
    print(f"[window] {start.isoformat()} .. {end.isoformat()} (holdout enforced)")
    base = _load_baseline_scores(start, end)
    cand = _simulate_candidate_scores(start)

    symbols = sorted(set(base["symbol"].to_list()) | set(cand["symbol"].to_list()))
    wr = _load_wr7(symbols)

    base_wr = base.join(wr, on=["symbol", "date"], how="inner")
    cand_wr = cand.join(wr, on=["symbol", "date"], how="inner")
    print(f"[join] baseline+WR7={len(base_wr):,}; candidate+WR7={len(cand_wr):,}")

    base_s = _summarize(base_wr, "baseline_score", "baseline")
    cand_s = _summarize(cand_wr, "candidate_score", "candidate")
    _print_summary(base_s, cand_s)

    out = {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "candidate": {
            "K": ICH_ASYMK_K,
            "POWER": ICH_ASYMK_POWER,
            "GATE_LO": ICH_ASYMK_GATE_LO,
            "GATE_HI": ICH_ASYMK_GATE_HI,
            "KIJ_SAT": ICH_ASYMK_KIJ_SAT,
            "TARGET": ICH_ASYMK_TARGET,
            "IND_RAMP": "log",
        },
        "baseline": base_s,
        "candidate_summary": cand_s,
    }
    out_path = ROOT / ".cache" / "ich_put_recal" / "phase_e_v46_shadow.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n[cache] wrote {out_path}")


if __name__ == "__main__":
    main()
