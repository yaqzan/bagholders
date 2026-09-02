from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polars as pl

CALL_BUCKETS = ("95+", "90-94", "85-89", "80-84", "75-79", "70-74")
PUT_BUCKETS = ("0-5", "6-10", "11-15", "16-20", "21-25", "26-30")
TRADABLE_CALL = ("95+", "90-94", "85-89", "80-84", "75-79")
TRADABLE_PUT = ("0-5", "6-10", "11-15", "16-20", "21-25")
ALLOC_WEIGHTS = {
    "95+": 4.0,
    "90-94": 3.2,
    "85-89": 2.4,
    "80-84": 1.6,
    "75-79": 1.0,
    "0-5": 4.0,
    "6-10": 3.2,
    "11-15": 2.4,
    "16-20": 1.6,
    "21-25": 1.0,
}


@dataclass(frozen=True)
class Variant:
    tau: float
    score_power: float
    norm: float
    alpha: float
    max_lift: float
    target: float
    min_echo: float
    w7: float
    w15: float
    w30: float
    w60: float
    loss_penalty: float
    fizzler_penalty: float
    allow_puts: bool = False


STRICT_CENTER = Variant(
    tau=32.57839933317171,
    score_power=0.8650331075499574,
    norm=1.630249748194968,
    alpha=0.9570882509275846,
    max_lift=5.666403955707368,
    target=80.0,
    min_echo=0.05962736940450597,
    w7=0.2703203500421868,
    w15=0.06078496070870909,
    w30=0.8496621742131409,
    w60=0.9184520076468756,
    loss_penalty=0.3270416480352185,
    fizzler_penalty=0.6470899467981728,
    allow_puts=False,
)


def bucket(score: int) -> str:
    if score >= 95:
        return "95+"
    if score >= 90:
        return "90-94"
    if score >= 85:
        return "85-89"
    if score >= 80:
        return "80-84"
    if score >= 75:
        return "75-79"
    if score >= 70:
        return "70-74"
    if score >= 65:
        return "65-69"
    if score >= 60:
        return "60-64"
    if score >= 55:
        return "55-59"
    if score >= 50:
        return "50-54"
    if score <= 5:
        return "0-5"
    if score <= 10:
        return "6-10"
    if score <= 15:
        return "11-15"
    if score <= 20:
        return "16-20"
    if score <= 25:
        return "21-25"
    if score <= 30:
        return "26-30"
    if score <= 35:
        return "31-35"
    if score <= 40:
        return "36-40"
    if score <= 45:
        return "41-45"
    return "46-49"


def bucket_expr(score: pl.Expr) -> pl.Expr:
    return (
        pl.when(score >= 95).then(pl.lit("95+"))
        .when(score >= 90).then(pl.lit("90-94"))
        .when(score >= 85).then(pl.lit("85-89"))
        .when(score >= 80).then(pl.lit("80-84"))
        .when(score >= 75).then(pl.lit("75-79"))
        .when(score >= 70).then(pl.lit("70-74"))
        .when(score >= 65).then(pl.lit("65-69"))
        .when(score >= 60).then(pl.lit("60-64"))
        .when(score >= 55).then(pl.lit("55-59"))
        .when(score >= 50).then(pl.lit("50-54"))
        .when(score <= 5).then(pl.lit("0-5"))
        .when(score <= 10).then(pl.lit("6-10"))
        .when(score <= 15).then(pl.lit("11-15"))
        .when(score <= 20).then(pl.lit("16-20"))
        .when(score <= 25).then(pl.lit("21-25"))
        .when(score <= 30).then(pl.lit("26-30"))
        .when(score <= 35).then(pl.lit("31-35"))
        .when(score <= 40).then(pl.lit("36-40"))
        .when(score <= 45).then(pl.lit("41-45"))
        .otherwise(pl.lit("46-49"))
    )


def prior_sig_expr(v: Variant) -> pl.Expr:
    w7 = pl.col("prior_win_7d")
    w15 = pl.col("prior_win_15d")
    w30 = pl.col("prior_win_30d")
    w60 = pl.col("prior_win_60d")
    fizzler_60 = (w7 == 1) & (w15 == 1) & (w30 == 0)
    fizzler_30 = (w7 == 1) & (w15 == 1)
    return (
        pl.when(w60.is_not_null())
        .then(
            pl.when(w60 == 1)
            .then(pl.lit(v.w60))
            .when(fizzler_60)
            .then(pl.lit(-v.fizzler_penalty))
            .otherwise(pl.lit(-v.loss_penalty))
        )
        .when(w30.is_not_null())
        .then(
            pl.when(w30 == 1)
            .then(pl.lit(v.w30))
            .when(fizzler_30)
            .then(pl.lit(-0.75 * v.fizzler_penalty))
            .otherwise(pl.lit(-0.6 * v.loss_penalty))
        )
        .when(w15.is_not_null())
        .then(pl.when(w15 == 1).then(pl.lit(v.w15)).otherwise(pl.lit(-0.25 * v.loss_penalty)))
        .when(w7.is_not_null())
        .then(pl.when(w7 == 1).then(pl.lit(v.w7)).otherwise(pl.lit(-0.15 * v.loss_penalty)))
        .otherwise(pl.lit(0.0))
    )


def aggregate_echo(priors: pl.DataFrame, v: Variant) -> pl.DataFrame:
    p = priors if v.allow_puts else priors.filter(pl.col("side") == "low")
    if p.is_empty():
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date, "side": pl.Utf8, "echo": pl.Float64})
    p = p.with_columns(
        [
            prior_sig_expr(v).alias("sig"),
            ((pl.col("prior_distance").cast(pl.Float64) / 50.0).pow(v.score_power)).alias("magnitude"),
            (-pl.col("gap_days").cast(pl.Float64) / v.tau).exp().alias("decay"),
        ]
    ).with_columns((pl.col("sig") * pl.col("magnitude") * pl.col("decay")).alias("contribution"))
    p = p.filter(pl.col("contribution") != 0.0)
    if p.is_empty():
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date, "side": pl.Utf8, "echo": pl.Float64})
    agg = p.group_by(["symbol", "date", "side"]).agg(pl.col("contribution").sum().alias("raw_echo"))
    return agg.with_columns((pl.col("raw_echo") / v.norm).tanh().alias("echo")).select(["symbol", "date", "side", "echo"])


def apply_variant(signals: pl.DataFrame, priors: pl.DataFrame, v: Variant) -> pl.DataFrame:
    echo = aggregate_echo(priors, v)
    df = signals.join(echo, on=["symbol", "date", "side"], how="left").with_columns(pl.col("echo").fill_null(0.0))
    lift_call = pl.min_horizontal(
        pl.lit(v.max_lift),
        pl.lit(v.alpha) * pl.col("echo") * (pl.lit(v.target) - pl.col("overall").cast(pl.Float64)),
    )
    lift_put = pl.min_horizontal(
        pl.lit(v.max_lift),
        pl.lit(v.alpha) * pl.col("echo") * (pl.col("overall").cast(pl.Float64) - pl.lit(100.0 - v.target)),
    )
    new_score = (
        pl.when((pl.col("side") == "low") & (pl.col("echo") >= v.min_echo) & (pl.col("overall") >= 50) & (pl.col("overall") < int(round(v.target))))
        .then(pl.col("overall").cast(pl.Float64) + lift_call)
        .when((pl.col("side") == "high") & pl.lit(v.allow_puts) & (pl.col("echo") >= v.min_echo) & (pl.col("overall") > int(round(100 - v.target))) & (pl.col("overall") < 50))
        .then(pl.col("overall").cast(pl.Float64) - lift_put)
        .otherwise(pl.col("overall").cast(pl.Float64))
        .round()
        .clip(0, 100)
        .cast(pl.Int64)
    )
    return df.with_columns(new_score.alias("new_score")).with_columns(
        [
            (pl.col("new_score") != pl.col("overall")).alias("changed"),
            bucket_expr(pl.col("new_score")).alias("new_bucket"),
        ]
    )


def _bucket_stats(df: pl.DataFrame, bucket_col: str, side: str, outcome_col: str) -> dict:
    wanted = TRADABLE_CALL if side == "low" else TRADABLE_PUT
    out = {}
    for b in wanted:
        sub = df.filter((pl.col("side") == side) & (pl.col(bucket_col) == b) & pl.col(outcome_col).is_not_null())
        n = sub.height
        wins = sub.filter(pl.col(outcome_col) == 1).height
        out[b] = {"n": n, "wins": wins, "wr": (100.0 * wins / n if n else None)}
    return out


def utility(df: pl.DataFrame, max_wr_drop: float | None = None, max_opt_drop: float | None = None) -> dict:
    base_call = _bucket_stats(df, "bucket", "low", "win_7d")
    post_call = _bucket_stats(df, "new_bucket", "low", "win_7d")
    base_opt = _bucket_stats(df, "bucket", "low", "opt_result_15")
    post_opt = _bucket_stats(df, "new_bucket", "low", "opt_result_15")

    util = 0.0
    inc_winners = 0.0
    n_delta = 0
    wr_penalty = 0.0
    opt_penalty = 0.0
    breakdown = {}
    worst_wr_drop = 0.0
    worst_opt_drop = 0.0
    for b in TRADABLE_CALL:
        bw = base_call[b]
        pw = post_call[b]
        bo = base_opt[b]
        po = post_opt[b]
        weight = ALLOC_WEIGHTS[b]
        base_exp = bw["wins"]
        post_exp = pw["wins"]
        dwin = post_exp - base_exp
        dn = pw["n"] - bw["n"]
        dwr = (pw["wr"] - bw["wr"]) if pw["wr"] is not None and bw["wr"] is not None else 0.0
        dopt = (po["wr"] - bo["wr"]) if po["wr"] is not None and bo["wr"] is not None else 0.0
        inc_winners += weight * dwin
        n_delta += dn
        if dwr < -0.75:
            wr_penalty += weight * abs(dwr + 0.75) * max(1, pw["n"]) / 100.0
        if dopt < -1.0:
            opt_penalty += weight * abs(dopt + 1.0) * max(1, po["n"]) / 100.0
        worst_wr_drop = min(worst_wr_drop, dwr)
        worst_opt_drop = min(worst_opt_drop, dopt)
        breakdown[b] = {"d_n": dn, "d_winners": dwin, "d_wr7_pp": dwr, "d_opt15_pp": dopt}

    util = inc_winners + 0.015 * n_delta - wr_penalty - 1.5 * opt_penalty
    screen_pass = True
    if max_wr_drop is not None and worst_wr_drop < -abs(max_wr_drop):
        screen_pass = False
    if max_opt_drop is not None and worst_opt_drop < -abs(max_opt_drop):
        screen_pass = False
    screened_utility = util if screen_pass else util - 100_000.0
    changed = df.filter(pl.col("changed")).height
    promoted_to_trade = df.filter((pl.col("side") == "low") & (pl.col("bucket").is_in(["50-54", "55-59", "60-64", "65-69", "70-74"])) & (pl.col("new_bucket").is_in(list(TRADABLE_CALL)))).height
    return {
        "utility": util,
        "weighted_incremental_winners": inc_winners,
        "n_delta_tradable_calls": n_delta,
        "wr_penalty": wr_penalty,
        "opt_penalty": opt_penalty,
        "screened_utility": screened_utility,
        "screen_pass": screen_pass,
        "worst_wr_drop_pp": worst_wr_drop,
        "worst_opt_drop_pp": worst_opt_drop,
        "changed": changed,
        "promoted_to_trade": promoted_to_trade,
        "breakdown": breakdown,
    }


def random_variants(n: int, seed: int) -> list[Variant]:
    rng = random.Random(seed)
    variants = [
        Variant(40.0, 0.70, 3.0, 1.0, 8.0, 75.0, 0.20, 0.12, 0.20, 0.60, 1.00, 0.30, 0.45, False)
    ]
    for _ in range(max(0, n - 1)):
        variants.append(
            Variant(
                tau=rng.uniform(18.0, 75.0),
                score_power=rng.uniform(0.45, 1.40),
                norm=rng.uniform(1.2, 6.0),
                alpha=rng.uniform(0.35, 1.35),
                max_lift=rng.uniform(3.0, 14.0),
                target=rng.choice([75.0, 78.0, 80.0, 85.0]),
                min_echo=rng.uniform(0.05, 0.45),
                w7=rng.uniform(0.0, 0.35),
                w15=rng.uniform(0.0, 0.50),
                w30=rng.uniform(0.35, 0.95),
                w60=rng.uniform(0.70, 1.35),
                loss_penalty=rng.uniform(0.05, 0.50),
                fizzler_penalty=rng.uniform(0.20, 0.85),
                allow_puts=False,
            )
        )
    return variants


def _jitter(rng: random.Random, x: float, pct: float, lo: float, hi: float) -> float:
    span = abs(x) * pct
    if span == 0:
        span = pct
    return max(lo, min(hi, rng.uniform(x - span, x + span)))


def drill_variants(n: int, seed: int, center: Variant = STRICT_CENTER) -> list[Variant]:
    rng = random.Random(seed)
    variants = [center]
    targets = [78.0, 80.0, 82.0, 85.0]
    for _ in range(max(0, n - 1)):
        variants.append(
            Variant(
                tau=_jitter(rng, center.tau, 0.35, 18.0, 75.0),
                score_power=_jitter(rng, center.score_power, 0.35, 0.45, 1.60),
                norm=_jitter(rng, center.norm, 0.45, 0.8, 4.0),
                alpha=_jitter(rng, center.alpha, 0.35, 0.25, 1.25),
                max_lift=_jitter(rng, center.max_lift, 0.35, 2.0, 8.0),
                target=rng.choice(targets),
                min_echo=_jitter(rng, center.min_echo, 0.75, 0.01, 0.22),
                w7=_jitter(rng, center.w7, 0.60, 0.0, 0.45),
                w15=_jitter(rng, center.w15, 0.80, 0.0, 0.35),
                w30=_jitter(rng, center.w30, 0.35, 0.45, 1.15),
                w60=_jitter(rng, center.w60, 0.35, 0.65, 1.30),
                loss_penalty=_jitter(rng, center.loss_penalty, 0.50, 0.10, 0.55),
                fizzler_penalty=_jitter(rng, center.fizzler_penalty, 0.45, 0.30, 0.95),
                allow_puts=False,
            )
        )
    return variants


def run_sweep(
    signals_path: Path,
    priors_path: Path,
    out_dir: Path,
    variants_n: int,
    seed: int,
    mode: str = "random",
    max_wr_drop: float | None = None,
    max_opt_drop: float | None = None,
) -> dict:
    signals = pl.read_parquet(signals_path)
    priors = pl.read_parquet(priors_path)
    signals = signals.filter(pl.col("win_7d").is_not_null())
    print(
        f"[sweep] signals={len(signals):,} priors={len(priors):,} variants={variants_n} "
        f"mode={mode} max_wr_drop={max_wr_drop} max_opt_drop={max_opt_drop}",
        flush=True,
    )

    results = []
    variant_list = drill_variants(variants_n, seed) if mode == "drill" else random_variants(variants_n, seed)
    for idx, v in enumerate(variant_list, start=1):
        df = apply_variant(signals, priors, v)
        u = utility(df, max_wr_drop=max_wr_drop, max_opt_drop=max_opt_drop)
        rec = {"rank_input": idx, "variant": asdict(v), **u}
        results.append(rec)
        if idx == 1 or idx % 10 == 0:
            print(
                f"[sweep] {idx}/{variants_n} util={u['utility']:.2f} "
                f"screen={u['screen_pass']} changed={u['changed']:,}",
                flush=True,
            )

    sort_key = "screened_utility" if max_wr_drop is not None or max_opt_drop is not None else "utility"
    results.sort(key=lambda r: r[sort_key], reverse=True)
    for i, r in enumerate(results, start=1):
        r["rank"] = i
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "sweep_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"[sweep] wrote {out}", flush=True)
    return {"sweep_results": str(out), "best": results[0] if results else None}


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", required=True)
    parser.add_argument("--priors", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--variants", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260511)
    parser.add_argument("--mode", choices=["random", "drill"], default="random")
    parser.add_argument("--max-wr-drop", type=float, default=None)
    parser.add_argument("--max-opt-drop", type=float, default=None)
    args = parser.parse_args(argv)
    return run_sweep(
        Path(args.signals),
        Path(args.priors),
        Path(args.out_dir),
        args.variants,
        args.seed,
        mode=args.mode,
        max_wr_drop=args.max_wr_drop,
        max_opt_drop=args.max_opt_drop,
    )


if __name__ == "__main__":
    main()
