"""Reverse engineer factor signatures from realized replay drawdowns.

This experiment detects drawdown episodes from the deterministic replay equity
curve, then ranks factors that were consistently abnormal before and during the
known drawdowns. It is designed for hypothesis generation, not promotion.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from mine_conditional_alpha import BASE_FEATURES, DERIVED_FEATURES, _add_derived, _float


DEFAULT_DAILY = Path("daily_state.csv")
DEFAULT_START_DD = 0.15
DEFAULT_RECOVER_DD = 0.05
DEFAULT_MIN_MAX_DD = 0.30
DEFAULT_PRELUDE_DAYS = 10

PHASES = ["prelude", "onset", "deep"]
TARGETS = [
    "entry_tp_rate",
    "entry_avg_pnl_pct",
    "next_15d_worst_equity_return",
    "next_30d_worst_equity_return",
    "next_15d_dd_increase",
    "next_30d_dd_increase",
]


@dataclass
class Episode:
    episode_id: int
    start_idx: int
    end_idx: int
    trough_idx: int
    start: date
    end: date
    trough: date
    days: int
    max_dd: float
    depth_bucket: str


def _as_date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda row: row["date"])
    for row in rows:
        row["_date"] = _as_date(row["date"])
    _add_derived(rows)
    return rows


def _safe_float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = _float(row.get(key))
    return value if value is not None and math.isfinite(value) else default


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if math.isfinite(v)]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _std(values: list[float]) -> float:
    clean = [v for v in values if math.isfinite(v)]
    if len(clean) < 2:
        return 1.0
    avg = sum(clean) / len(clean)
    var = sum((v - avg) ** 2 for v in clean) / (len(clean) - 1)
    return math.sqrt(var) or 1.0


def _quantile(values: list[float], q: float) -> float:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return 0.0
    idx = max(0, min(len(clean) - 1, round((len(clean) - 1) * q)))
    return clean[idx]


def _detect_episodes(
    rows: list[dict[str, Any]],
    *,
    start_dd: float,
    recover_dd: float,
    min_max_dd: float,
) -> list[Episode]:
    episodes: list[Episode] = []
    in_episode = False
    start_idx = 0
    trough_idx = 0
    max_dd = 0.0
    for idx, row in enumerate(rows):
        dd = _safe_float(row, "dd")
        if not in_episode and dd >= start_dd:
            in_episode = True
            start_idx = idx
            trough_idx = idx
            max_dd = dd
            continue
        if in_episode:
            if dd > max_dd:
                max_dd = dd
                trough_idx = idx
            if dd <= recover_dd:
                end_idx = idx
                if max_dd >= min_max_dd:
                    episodes.append(_make_episode(len(episodes) + 1, rows, start_idx, end_idx, trough_idx, max_dd))
                in_episode = False
    if in_episode and max_dd >= min_max_dd:
        episodes.append(_make_episode(len(episodes) + 1, rows, start_idx, len(rows) - 1, trough_idx, max_dd))
    return episodes


def _make_episode(
    episode_id: int,
    rows: list[dict[str, Any]],
    start_idx: int,
    end_idx: int,
    trough_idx: int,
    max_dd: float,
) -> Episode:
    if max_dd >= 0.60:
        bucket = "severe"
    elif max_dd >= 0.40:
        bucket = "major"
    else:
        bucket = "material"
    return Episode(
        episode_id=episode_id,
        start_idx=start_idx,
        end_idx=end_idx,
        trough_idx=trough_idx,
        start=rows[start_idx]["_date"],
        end=rows[end_idx]["_date"],
        trough=rows[trough_idx]["_date"],
        days=end_idx - start_idx + 1,
        max_dd=max_dd,
        depth_bucket=bucket,
    )


def _phase_indexes(episode: Episode, rows: list[dict[str, Any]], prelude_days: int) -> dict[str, list[int]]:
    pre_start = max(0, episode.start_idx - prelude_days)
    return {
        "prelude": list(range(pre_start, episode.start_idx)),
        "onset": list(range(episode.start_idx, episode.trough_idx + 1)),
        "deep": list(range(episode.start_idx, episode.end_idx + 1)),
    }


def _reference_indexes(rows: list[dict[str, Any]], episode_indexes: set[int]) -> list[int]:
    out = []
    for idx, row in enumerate(rows):
        if idx in episode_indexes:
            continue
        if _safe_float(row, "dd") <= 0.05:
            out.append(idx)
    return out


def _values(rows: list[dict[str, Any]], indexes: list[int], feature: str) -> list[float]:
    out = []
    for idx in indexes:
        value = _float(rows[idx].get(feature))
        if value is not None and math.isfinite(value):
            out.append(value)
    return out


def _target_edge(rows: list[dict[str, Any]], selected: list[int], reference: list[int], target: str) -> float:
    sel = _mean(_values(rows, selected, target))
    ref = _mean(_values(rows, reference, target))
    if sel is None or ref is None:
        return 0.0
    return sel - ref


def _feature_candidates(rows: list[dict[str, Any]]) -> list[str]:
    features = []
    for feature in BASE_FEATURES + DERIVED_FEATURES + ["equity"]:
        vals = _values(rows, list(range(len(rows))), feature)
        if len(vals) >= 100 and len(set(round(v, 10) for v in vals)) >= 6:
            features.append(feature)
    return features


def _episode_rows(episodes: list[Episode]) -> list[dict[str, Any]]:
    return [
        {
            "episode_id": ep.episode_id,
            "start": ep.start.isoformat(),
            "trough": ep.trough.isoformat(),
            "end": ep.end.isoformat(),
            "days": ep.days,
            "max_dd": ep.max_dd,
            "depth_bucket": ep.depth_bucket,
        }
        for ep in episodes
    ]


def _score_feature_phase(
    rows: list[dict[str, Any]],
    episodes: list[Episode],
    reference: list[int],
    feature: str,
    phase: str,
    prelude_days: int,
) -> dict[str, Any] | None:
    ref_vals = _values(rows, reference, feature)
    if len(ref_vals) < 40:
        return None
    ref_mean = _mean(ref_vals)
    if ref_mean is None:
        return None
    ref_std = _std(ref_vals)
    q20 = _quantile(ref_vals, 0.20)
    q80 = _quantile(ref_vals, 0.80)

    episode_means: list[float] = []
    zscores: list[float] = []
    high_hits = 0
    low_hits = 0
    hit_episode_ids: list[int] = []
    all_phase_indexes: list[int] = []
    for ep in episodes:
        idxs = _phase_indexes(ep, rows, prelude_days)[phase]
        vals = _values(rows, idxs, feature)
        if not vals:
            continue
        mean_val = sum(vals) / len(vals)
        episode_means.append(mean_val)
        z = (mean_val - ref_mean) / ref_std
        zscores.append(z)
        all_phase_indexes.extend(idxs)
        if mean_val >= q80:
            high_hits += 1
            hit_episode_ids.append(ep.episode_id)
        if mean_val <= q20:
            low_hits += 1
            hit_episode_ids.append(ep.episode_id)

    if len(episode_means) < 4:
        return None
    avg_z = sum(zscores) / len(zscores)
    direction = "high" if avg_z >= 0 else "low"
    hit_rate = (high_hits if direction == "high" else low_hits) / len(episode_means)
    if hit_rate < 0.45 and abs(avg_z) < 0.50:
        return None

    threshold = q80 if direction == "high" else q20
    rule_indexes = [
        idx
        for idx, row in enumerate(rows)
        if (value := _float(row.get(feature))) is not None
        and ((value >= threshold) if direction == "high" else (value <= threshold))
    ]
    other_indexes = [idx for idx in range(len(rows)) if idx not in set(rule_indexes)]
    dd15_edge = _target_edge(rows, rule_indexes, other_indexes, "next_15d_dd_increase")
    dd30_edge = _target_edge(rows, rule_indexes, other_indexes, "next_30d_dd_increase")
    worst15_edge = _target_edge(rows, rule_indexes, other_indexes, "next_15d_worst_equity_return")
    worst30_edge = _target_edge(rows, rule_indexes, other_indexes, "next_30d_worst_equity_return")
    tp_edge = _target_edge(rows, rule_indexes, other_indexes, "entry_tp_rate")
    pnl_edge = _target_edge(rows, rule_indexes, other_indexes, "entry_avg_pnl_pct")

    forward_risk = dd15_edge + 0.55 * dd30_edge - worst15_edge - 0.45 * worst30_edge
    score = abs(avg_z) * (0.5 + hit_rate) + max(0.0, forward_risk)
    return {
        "feature": feature,
        "phase": phase,
        "direction": direction,
        "threshold": threshold,
        "episode_n": len(episode_means),
        "hit_rate": hit_rate,
        "avg_z": avg_z,
        "median_episode_value": _quantile(episode_means, 0.50),
        "reference_mean": ref_mean,
        "reference_q20": q20,
        "reference_q80": q80,
        "rule_days": len(rule_indexes),
        "rule_support": len(rule_indexes) / len(rows) if rows else 0.0,
        "entry_tp_edge": tp_edge,
        "pnl_edge": pnl_edge,
        "next_15d_worst_edge": worst15_edge,
        "next_30d_worst_edge": worst30_edge,
        "next_15d_dd_edge": dd15_edge,
        "next_30d_dd_edge": dd30_edge,
        "score": score,
    }


def _build_pair_rules(records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    seen_candidate_keys: set[tuple[str, str, float]] = set()
    for record in records:
        if record["phase"] not in {"prelude", "onset"}:
            continue
        if record["score"] <= 0.70 or record["rule_support"] > 0.55:
            continue
        key = (record["feature"], record["direction"], round(float(record["threshold"]), 10))
        if key in seen_candidate_keys:
            continue
        seen_candidate_keys.add(key)
        candidates.append(record)
        if len(candidates) >= 24:
            break
    pairs: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for i, left in enumerate(candidates):
        for right in candidates[i + 1 :]:
            if left["feature"] == right["feature"]:
                continue
            pair_key = tuple(sorted([left["feature"], right["feature"]]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            selected = []
            for idx, row in enumerate(rows):
                lval = _float(row.get(left["feature"]))
                rval = _float(row.get(right["feature"]))
                if lval is None or rval is None:
                    continue
                lhit = lval >= left["threshold"] if left["direction"] == "high" else lval <= left["threshold"]
                rhit = rval >= right["threshold"] if right["direction"] == "high" else rval <= right["threshold"]
                if lhit and rhit:
                    selected.append(idx)
            if len(selected) < 30:
                continue
            other = [idx for idx in range(len(rows)) if idx not in set(selected)]
            tp_edge = _target_edge(rows, selected, other, "entry_tp_rate")
            pnl_edge = _target_edge(rows, selected, other, "entry_avg_pnl_pct")
            worst15_edge = _target_edge(rows, selected, other, "next_15d_worst_equity_return")
            dd15_edge = _target_edge(rows, selected, other, "next_15d_dd_increase")
            badness = -tp_edge - pnl_edge - worst15_edge + dd15_edge
            pairs.append(
                {
                    "condition": (
                        f"{left['feature']} {left['direction']} {left['threshold']:.4g}"
                        f" AND {right['feature']} {right['direction']} {right['threshold']:.4g}"
                    ),
                    "days": len(selected),
                    "support": len(selected) / len(rows),
                    "entry_tp_edge": tp_edge,
                    "pnl_edge": pnl_edge,
                    "next_15d_worst_edge": worst15_edge,
                    "next_15d_dd_edge": dd15_edge,
                    "badness": badness,
                }
            )
    pairs.sort(key=lambda row: row["badness"], reverse=True)
    return pairs[:50]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _fmt_pp(value: float) -> str:
    return f"{value * 100:+.2f}pp"


def _summary(
    args: argparse.Namespace,
    episodes: list[Episode],
    records: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> str:
    state_markers = {"dd", "prev5_dd", "dd_5d_delta", "equity"}
    prelude = [r for r in records if r["phase"] == "prelude"][:15]
    onset = [r for r in records if r["phase"] == "onset"][:15]
    deep = [r for r in records if r["phase"] == "deep"][:10]
    actionable = [
        r
        for r in records
        if r["phase"] in {"prelude", "onset"} and r["feature"] not in state_markers
    ][:15]
    lines = [
        "# Drawdown Signature Reverse Engineering",
        "",
        "Experiment-local drawdown factor mining. This is not a ship result.",
        "",
        "## Inputs",
        "",
        f"- daily_state: `{args.daily}`",
        f"- start_dd: {args.start_dd:.0%}",
        f"- recover_dd: {args.recover_dd:.0%}",
        f"- min_max_dd: {args.min_max_dd:.0%}",
        f"- prelude_days: {args.prelude_days}",
        f"- detected_episodes: {len(episodes)}",
        "",
        "## Episodes",
        "",
        "| id | start | trough | end | days | max DD | bucket |",
        "|---:|---|---|---|---:|---:|---|",
    ]
    for ep in episodes:
        lines.append(
            f"| {ep.episode_id} | {ep.start} | {ep.trough} | {ep.end} | "
            f"{ep.days} | {ep.max_dd:.1%} | {ep.depth_bucket} |"
        )

    def add_table(title: str, rows: list[dict[str, Any]]) -> None:
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| rank | factor | dir | hit | avg z | rule days | TP edge | PnL edge | worst15 edge | DD15 edge |",
                "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for idx, row in enumerate(rows, start=1):
            lines.append(
                f"| {idx} | `{row['feature']}` | {row['direction']} | {row['hit_rate']:.0%} | "
                f"{row['avg_z']:+.2f} | {row['rule_days']} | {_fmt_pp(row['entry_tp_edge'])} | "
                f"{_fmt_pp(row['pnl_edge'])} | {_fmt_pp(row['next_15d_worst_edge'])} | "
                f"{_fmt_pp(row['next_15d_dd_edge'])} |"
            )

    add_table("Prelude Signatures", prelude)
    add_table("Onset Signatures", onset)
    add_table("Deep-Drawdown Signatures", deep)
    add_table("Actionable Non-DD Seeds", actionable)

    lines.extend(
        [
            "",
            "## Pair Rules",
            "",
            "| rank | condition | days | badness | TP edge | PnL edge | worst15 edge | DD15 edge |",
            "|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for idx, row in enumerate(pairs[:12], start=1):
        lines.append(
            f"| {idx} | `{row['condition']}` | {row['days']} | {row['badness']:+.3f} | "
            f"{_fmt_pp(row['entry_tp_edge'])} | {_fmt_pp(row['pnl_edge'])} | "
            f"{_fmt_pp(row['next_15d_worst_edge'])} | {_fmt_pp(row['next_15d_dd_edge'])} |"
        )

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- Prelude signatures are the most useful for a preventive throttle.",
            "- Onset signatures are useful for confirming that a drawdown is no longer just noise.",
            "- Deep-drawdown signatures are mostly regime/re-entry context, not entry prevention.",
            "- `dd`, `prev5_dd`, and `dd_5d_delta` are state markers; use them as guards, not alpha by themselves.",
            "- The pair rules are hypothesis seeds for deterministic replay, not final formulas.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--start-dd", type=float, default=DEFAULT_START_DD)
    parser.add_argument("--recover-dd", type=float, default=DEFAULT_RECOVER_DD)
    parser.add_argument("--min-max-dd", type=float, default=DEFAULT_MIN_MAX_DD)
    parser.add_argument("--prelude-days", type=int, default=DEFAULT_PRELUDE_DAYS)
    parser.add_argument("--max-records", type=int, default=160)
    args = parser.parse_args()

    rows = _read_rows(args.daily)
    episodes = _detect_episodes(
        rows,
        start_dd=args.start_dd,
        recover_dd=args.recover_dd,
        min_max_dd=args.min_max_dd,
    )
    episode_indexes: set[int] = set()
    for ep in episodes:
        for phase_idxs in _phase_indexes(ep, rows, args.prelude_days).values():
            episode_indexes.update(phase_idxs)
    reference = _reference_indexes(rows, episode_indexes)

    records: list[dict[str, Any]] = []
    for feature in _feature_candidates(rows):
        for phase in PHASES:
            record = _score_feature_phase(rows, episodes, reference, feature, phase, args.prelude_days)
            if record is not None:
                records.append(record)
    records.sort(key=lambda row: row["score"], reverse=True)
    records = records[: args.max_records]
    pairs = _build_pair_rules(records, rows)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.out_dir / "drawdown_episodes.csv", _episode_rows(episodes))
    _write_csv(args.out_dir / "drawdown_signature_ranked.csv", records)
    _write_csv(args.out_dir / "drawdown_pair_rules.csv", pairs)
    (args.out_dir / "drawdown_signature_ledger.json").write_text(
        json.dumps(
            {
                "daily_state": str(args.daily),
                "episodes": _episode_rows(episodes),
                "records": records,
                "pairs": pairs,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (args.out_dir / "drawdown_signature_summary.md").write_text(
        _summary(args, episodes, records, pairs),
        encoding="utf-8",
    )
    print(f"wrote drawdown reverse-engineering artifacts to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
