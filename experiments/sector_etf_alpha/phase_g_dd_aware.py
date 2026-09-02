"""Phase G — DD-aware Bayesian-style refinement for sector ETF alpha.

The V4 SWPM candidate found real per-trade alpha, but smoke MC exposed a
portfolio failure: low-sector-RSI call lifts promoted several same-day signals
into high allocation tiers, creating correlated drawdown paths.

This phase keeps the same cached v46 cohort and searches constrained variants
that preserve per-trade WR7 lift while penalizing:
  - any 90+ call population expansion
  - large 85+ population expansion
  - same-day clusters of upward promotions into 85+
  - low-sector-RSI promotions into higher allocation tiers

No production code is modified.
"""
from __future__ import annotations

import io
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import polars as pl

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.sector_etf_alpha.swpm_v2 import SWPMv2Params, apply_swpm_v2, evaluate_v2

LOCAL = ROOT / ".cache" / "sector_etf_alpha"
COH_PATH = LOCAL / "cohort_v46_1825.parquet"
OUT = ROOT / "experiments" / "sector_etf_alpha"


BASE = {
    "EMA_K": 8.191921836666943,
    "RSI_K": 22.712930827616663,
    "MACD_TANH_K": 0.83,
    "EMA_W": 0.40276087420995743,
    "RSI_W": 0.5654963306981752,
    "MACD_W": 0.0,
    "PHASE_POWER": 1.7659681877885753,
    "SCORE_POWER": 1.360855841161326,
    "ALPHA_DOWN": 0.5605943781200426,
    "TARGET_DOWN": 57.32811378056606,
    "ALPHA_UP": 0.8971105879403463,
    "TARGET_UP": 90.79257270026329,
    "ALPHA_PUT_DOWN": 0.6088340134421638,
    "TARGET_PUT": 30.392536194890337,
    "ALPHA_PUT_UP": 0.15081698206797753,
    "TARGET_PUT_UP": 12.773456941086042,
    "RS_GATE": 0.06471305138758451,
    "RS_K": 0.12524487985737423,
    "RS_POWER": 1.6429127988567984,
    "RS_SCORE_POWER": 2.2108006865582617,
    "RS_ALPHA_C": 0.866665358993649,
    "RS_TARGET_C": 69.84272634544314,
    "RS_ALPHA_P": 0.4730433409506397,
    "RS_TARGET_P": 31.00749202339499,
}


ARCHES = {
    # Keep the sector phase idea, but cap lift below the 85+ allocation boundary.
    "G1_capped_stack": {
        "swpm": True,
        "rsu": True,
        "space": {
            "EMA_K": (5.0, 11.0),
            "RSI_K": (18.0, 30.0),
            "EMA_W": (0.15, 0.75),
            "RSI_W": (0.25, 0.85),
            "PHASE_POWER": (1.2, 2.8),
            "SCORE_POWER": (1.0, 2.2),
            "ALPHA_DOWN": (0.25, 0.95),
            "TARGET_DOWN": (55.0, 70.0),
            "ALPHA_UP": (0.0, 0.65),
            "TARGET_UP": (78.0, 84.4),
            "ALPHA_PUT_DOWN": (0.20, 0.85),
            "TARGET_PUT": (28.0, 36.0),
            "ALPHA_PUT_UP": (0.0, 0.30),
            "TARGET_PUT_UP": (9.0, 15.0),
            "RS_GATE": (0.045, 0.095),
            "RS_K": (0.06, 0.18),
            "RS_POWER": (1.0, 2.3),
            "RS_SCORE_POWER": (1.0, 2.5),
            "RS_ALPHA_C": (0.35, 0.95),
            "RS_TARGET_C": (62.0, 72.0),
            "RS_ALPHA_P": (0.0, 0.65),
            "RS_TARGET_P": (28.0, 36.0),
        },
    },
    # Pure dampener: keep overheated-sector pruning and RSU; remove sector up-lifts.
    "G2_dampen_only_stack": {
        "swpm": True,
        "rsu": True,
        "fixed": {"ALPHA_UP": 0.0, "TARGET_UP": 84.0},
        "space": {
            "EMA_K": (5.0, 12.0),
            "RSI_K": (18.0, 32.0),
            "EMA_W": (0.15, 0.75),
            "RSI_W": (0.25, 0.85),
            "PHASE_POWER": (1.0, 3.0),
            "SCORE_POWER": (1.0, 2.4),
            "ALPHA_DOWN": (0.25, 0.95),
            "TARGET_DOWN": (55.0, 70.0),
            "ALPHA_PUT_DOWN": (0.20, 0.85),
            "TARGET_PUT": (28.0, 36.0),
            "ALPHA_PUT_UP": (0.0, 0.30),
            "TARGET_PUT_UP": (9.0, 15.0),
            "RS_GATE": (0.045, 0.10),
            "RS_K": (0.06, 0.20),
            "RS_POWER": (1.0, 2.4),
            "RS_SCORE_POWER": (1.0, 2.5),
            "RS_ALPHA_C": (0.35, 0.95),
            "RS_TARGET_C": (60.0, 72.0),
            "RS_ALPHA_P": (0.0, 0.65),
            "RS_TARGET_P": (28.0, 36.0),
        },
    },
    # The strongest single-feature alpha, without sector phase tier promotion.
    "G3_RSU_only": {
        "swpm": False,
        "rsu": True,
        "space": {
            "RS_GATE": (0.035, 0.10),
            "RS_K": (0.05, 0.20),
            "RS_POWER": (1.0, 2.5),
            "RS_SCORE_POWER": (1.0, 2.7),
            "RS_ALPHA_C": (0.25, 0.95),
            "RS_TARGET_C": (55.0, 72.0),
            "RS_ALPHA_P": (0.0, 0.70),
            "RS_TARGET_P": (28.0, 38.0),
        },
    },
    # Sector phase only, but no upward call lift. Tests whether sector alpha is
    # mostly "avoid bad calls" rather than "promote good calls".
    "G4_SEC_down_only": {
        "swpm": True,
        "rsu": False,
        "fixed": {"ALPHA_UP": 0.0, "TARGET_UP": 84.0},
        "space": {
            "EMA_K": (4.0, 12.0),
            "RSI_K": (16.0, 34.0),
            "EMA_W": (0.0, 0.8),
            "RSI_W": (0.2, 1.0),
            "PHASE_POWER": (1.0, 3.0),
            "SCORE_POWER": (1.0, 2.5),
            "ALPHA_DOWN": (0.25, 0.95),
            "TARGET_DOWN": (55.0, 70.0),
            "ALPHA_PUT_DOWN": (0.20, 0.85),
            "TARGET_PUT": (28.0, 36.0),
            "ALPHA_PUT_UP": (0.0, 0.35),
            "TARGET_PUT_UP": (9.0, 15.0),
        },
    },
    # Allow a narrow amount of sector-oversold call lift, but keep the target
    # near the 85 boundary and let the DD-aware objective reject clustered
    # high-tier promotions.
    "G5_limited_lift_stack": {
        "swpm": True,
        "rsu": True,
        "space": {
            "EMA_K": (5.0, 11.0),
            "RSI_K": (18.0, 30.0),
            "EMA_W": (0.15, 0.75),
            "RSI_W": (0.25, 0.85),
            "PHASE_POWER": (1.2, 2.8),
            "SCORE_POWER": (1.0, 2.2),
            "ALPHA_DOWN": (0.25, 0.95),
            "TARGET_DOWN": (55.0, 70.0),
            "ALPHA_UP": (0.05, 0.55),
            "TARGET_UP": (84.0, 87.4),
            "ALPHA_PUT_DOWN": (0.20, 0.85),
            "TARGET_PUT": (28.0, 36.0),
            "ALPHA_PUT_UP": (0.0, 0.30),
            "TARGET_PUT_UP": (9.0, 15.0),
            "RS_GATE": (0.045, 0.095),
            "RS_K": (0.06, 0.18),
            "RS_POWER": (1.0, 2.3),
            "RS_SCORE_POWER": (1.0, 2.5),
            "RS_ALPHA_C": (0.35, 0.95),
            "RS_TARGET_C": (62.0, 72.0),
            "RS_ALPHA_P": (0.0, 0.65),
            "RS_TARGET_P": (28.0, 36.0),
        },
    },
}


def lhs(n: int, dims: list[str], seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    raw = np.zeros((n, len(dims)))
    for j in range(len(dims)):
        cuts = (np.arange(n) + rng.uniform(0, 1, n)) / n
        rng.shuffle(cuts)
        raw[:, j] = cuts
    return [dict(zip(dims, row)) for row in raw]


def materialize(unit: dict, space: dict) -> dict:
    out = dict(BASE)
    for k, u in unit.items():
        lo, hi = space[k]
        out[k] = float(lo + u * (hi - lo))
    out["MACD_W"] = 0.0
    return out


def around(anchor: dict, space: dict, n: int, frac: float, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(n):
        row = dict(BASE)
        for k, (lo_h, hi_h) in space.items():
            v = float(anchor[k])
            span = max((hi_h - lo_h) * frac * 0.5, abs(v) * frac, 0.01)
            row[k] = float(rng.uniform(max(lo_h, v - span), min(hi_h, v + span)))
        row["MACD_W"] = 0.0
        samples.append(row)
    return samples


def params_from_kw(kw: dict, arch: dict) -> SWPMv2Params:
    full = dict(BASE)
    full.update(kw)
    full.update(arch.get("fixed", {}))
    return SWPMv2Params(
        SWPM_ENABLED=arch["swpm"],
        RSU_ENABLED=arch["rsu"],
        GATE_LO_C=70,
        GATE_HI_C=84,
        GATE_LO_P=16,
        GATE_HI_P=25,
        **full,
    )


def _metric(m: dict, side: str, tier: str, field: str) -> float:
    rows = m["tiers_call"] if side == "call" else m["tiers_put"]
    row = next(x for x in rows if x["tier"] == tier)
    return float(row[field] or 0.0)


def risk_proxy(coh: pl.DataFrame, p: SWPMv2Params) -> dict:
    out = apply_swpm_v2(coh, p)
    calls = out.filter(pl.col("signal_type") == "CALL")
    promoted_85 = calls.filter(
        (pl.col("new_overall") > pl.col("overall"))
        & (pl.col("overall") < 85)
        & (pl.col("new_overall") >= 85)
    )
    promoted_90 = calls.filter(
        (pl.col("new_overall") > pl.col("overall"))
        & (pl.col("overall") < 90)
        & (pl.col("new_overall") >= 90)
    )
    low_rsi_promoted = promoted_85.filter(pl.col("sec_etf_rsi") < 45)

    if promoted_85.height:
        by_date = promoted_85.group_by("date").agg(pl.len().alias("n")).sort("n", descending=True)
        max_day = int(by_date["n"].max())
        p95_day = float(by_date["n"].quantile(0.95))
        days_ge3 = int(by_date.filter(pl.col("n") >= 3).height)
    else:
        max_day = 0
        p95_day = 0.0
        days_ge3 = 0

    return {
        "promote_85_n": int(promoted_85.height),
        "promote_90_n": int(promoted_90.height),
        "low_rsi_promote_85_n": int(low_rsi_promoted.height),
        "promote_85_max_day": max_day,
        "promote_85_p95_day": p95_day,
        "promote_85_days_ge3": days_ge3,
    }


def dd_aware_eval(coh: pl.DataFrame, kw: dict, arch_name: str, arch: dict) -> dict:
    p = params_from_kw(kw, arch)
    m = evaluate_v2(coh, p)
    r = risk_proxy(coh, p)

    c75 = _metric(m, "call", "75+", "wr7_delta_pp")
    c80 = _metric(m, "call", "80+", "wr7_delta_pp")
    c85 = _metric(m, "call", "85+", "wr7_delta_pp")
    c90 = _metric(m, "call", "90+", "wr7_delta_pp")
    p20 = _metric(m, "put", "<=20", "wr7_delta_pp")
    c85_dn = _metric(m, "call", "85+", "n_delta_pct")
    c90_dn = _metric(m, "call", "90+", "n_delta_pct")
    c80_wr30 = _metric(m, "call", "80+", "wr30_delta_pp")

    # Utility intentionally values retained alpha but sharply prices the
    # promotion pattern that created the V4 DD failure.
    score = float(m["util"])
    score += 1.25 * max(c75, 0.0) + 1.00 * max(c80, 0.0) + 0.80 * max(p20, 0.0)
    score -= 2.00 * max(c90_dn, 0.0)
    score -= 0.75 * max(c85_dn, 0.0)
    score -= 0.030 * r["promote_85_n"]
    score -= 0.060 * r["low_rsi_promote_85_n"]
    score -= 0.75 * r["promote_85_days_ge3"]
    score -= 1.50 * max(r["promote_85_max_day"] - 2, 0)
    score -= 3.00 * int(c80_wr30 < -0.75)
    score -= 10.0 * int(m["W4_breaches"] > 0)
    score -= 10.0 * int(m["W5_breaches"] > 0)
    score -= 12.0 * int(m["W6_breaches"] > 0)

    row = {
        "arch": arch_name,
        "dd_aware_score": score,
        "stage1_util": float(m["util"]),
        "W4": int(m["W4_breaches"]),
        "W5": int(m["W5_breaches"]),
        "W6": int(m["W6_breaches"]),
        "n_changed": int(m["n_changed"]),
        "affected_call_n": int(m["affected_call_n"]),
        "affected_put_n": int(m["affected_put_n"]),
        **r,
        "call_90+_dWR7": c90,
        "call_90+_dN%": c90_dn,
        "call_85+_dWR7": c85,
        "call_85+_dN%": c85_dn,
        "call_80+_dWR7": c80,
        "call_80+_dWR30": c80_wr30,
        "call_75+_dWR7": c75,
        "put_<=20_dWR7": p20,
    }
    row.update(asdict(p))
    return row


def run_arch(coh: pl.DataFrame, arch_name: str, arch: dict, n_lhs: int, n_drill: int, seed: int) -> pl.DataFrame:
    space = arch["space"]
    dims = list(space)
    print(f"\n[{arch_name}] LHS={n_lhs} drill={n_drill} dims={len(dims)}", flush=True)

    rows = []
    t0 = time.time()
    for i, unit in enumerate(lhs(n_lhs, dims, seed)):
        kw = materialize(unit, space)
        rows.append(dd_aware_eval(coh, kw, arch_name, arch))
        if (i + 1) % 50 == 0:
            print(f"  LHS {i + 1}/{n_lhs} elapsed={time.time() - t0:.0f}s", flush=True)

    lhs_df = pl.DataFrame(rows).sort("dd_aware_score", descending=True)
    top5 = lhs_df.head(5)
    anchor = {k: float(top5[k].mean()) for k in dims}
    drill_rows = []
    t0 = time.time()
    for i, kw in enumerate(around(anchor, space, n_drill, frac=0.35, seed=seed + 991)):
        drill_rows.append(dd_aware_eval(coh, kw, arch_name, arch))
        if (i + 1) % 50 == 0:
            print(f"  drill {i + 1}/{n_drill} elapsed={time.time() - t0:.0f}s", flush=True)

    df = pl.concat([lhs_df, pl.DataFrame(drill_rows)], how="diagonal_relaxed").sort(
        "dd_aware_score", descending=True
    )
    df.write_csv(OUT / f"phase_g_{arch_name}.csv")
    return df


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--lhs", type=int, default=160)
    ap.add_argument("--drill", type=int, default=120)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    coh = pl.read_parquet(COH_PATH)
    print(f"[load] {len(coh):,} rows {coh['date'].min()} -> {coh['date'].max()}", flush=True)

    all_frames = []
    for idx, (name, arch) in enumerate(ARCHES.items()):
        df = run_arch(coh, name, arch, args.lhs, args.drill, seed=7031 + idx * 1000)
        all_frames.append(df)
        cols = [
            "arch",
            "dd_aware_score",
            "stage1_util",
            "call_90+_dWR7",
            "call_90+_dN%",
            "call_85+_dWR7",
            "call_85+_dN%",
            "call_80+_dWR7",
            "call_80+_dWR30",
            "call_75+_dWR7",
            "put_<=20_dWR7",
            "promote_85_n",
            "promote_90_n",
            "promote_85_max_day",
            "promote_85_days_ge3",
        ]
        print(df.select(cols).head(5), flush=True)

    all_df = pl.concat(all_frames, how="diagonal_relaxed").sort("dd_aware_score", descending=True)
    all_df.write_csv(OUT / "phase_g_all.csv")

    top_df = all_df.head(args.top)
    top_df.write_csv(OUT / "phase_g_top.csv")

    winners = {}
    for i, row in enumerate(top_df.to_dicts(), start=1):
        label = f"G{i:02d}_{row['arch']}"
        params = {field.name: row[field.name] for field in SWPMv2Params.__dataclass_fields__.values()}
        winners[label] = {
            "rank": i,
            "arch": row["arch"],
            "dd_aware_score": row["dd_aware_score"],
            "stage1_util": row["stage1_util"],
            "metrics": {
                k: row[k]
                for k in [
                    "call_90+_dWR7",
                    "call_90+_dN%",
                    "call_85+_dWR7",
                    "call_85+_dN%",
                    "call_80+_dWR7",
                    "call_80+_dWR30",
                    "call_75+_dWR7",
                    "put_<=20_dWR7",
                    "promote_85_n",
                    "promote_90_n",
                    "promote_85_max_day",
                    "promote_85_days_ge3",
                    "low_rsi_promote_85_n",
                ]
            },
            "params": params,
        }

    with open(OUT / "phase_g_winners.json", "w", encoding="utf-8") as f:
        json.dump(winners, f, indent=2, default=str)

    print("\n[TOP DD-AWARE CANDIDATES]", flush=True)
    print(
        top_df.select(
            [
                "arch",
                "dd_aware_score",
                "stage1_util",
                "call_90+_dWR7",
                "call_90+_dN%",
                "call_85+_dWR7",
                "call_85+_dN%",
                "call_80+_dWR7",
                "call_80+_dWR30",
                "call_75+_dWR7",
                "put_<=20_dWR7",
                "promote_85_n",
                "promote_90_n",
                "promote_85_max_day",
                "promote_85_days_ge3",
            ]
        ),
        flush=True,
    )
    print("[save] phase_g_all.csv phase_g_top.csv phase_g_winners.json", flush=True)


if __name__ == "__main__":
    main()
