"""
HOSTILE_REVIEW P2 (A-12): per-bucket ScoreSimulator exact-match parity.

The documented "98.43% exact match over 936,580 common rows" (integrity_audit_2026_06
FINDINGS.md) is a GLOBAL average produced by ab_eval.py's validate_vs_stored(): it builds
the 'legacy' arm (ScoreSimulator replica of pre-fix v70), persists it to
.cache/integrity_audit_2026_06/arm_legacy.parquet (symbol, date, overall -- the SIMULATOR
side only), and compares it in-memory to a MySQL Score.overall pull filtered to
version=70 (the STORED side) -- but only the AGGREGATE match-rate is written to disk
(validation.json); the per-row STORED value itself is never persisted anywhere.

So: the ledger parquet exists on disk (arm_legacy.parquet), but reproducing the STORED
side of the comparison -- required to bucket by STORED score, per A-12's brief -- needs a
read-only MySQL SELECT against Score (version=70). That disqualifies this script from
running foreground per its own brief ("foreground OK ONLY if it needs no MySQL"); it is
submitted via the task queue exactly like P1.

Usage:
    PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/_audit_2026_07/p2_parity_by_bucket.py
"""
import os
import sys
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import polars as pl  # noqa: E402

ARM_DIR = os.path.join(_ROOT, ".cache", "integrity_audit_2026_06")
ARM_LEGACY = os.path.join(ARM_DIR, "arm_legacy.parquet")
VALIDATION_JSON = os.path.join(ARM_DIR, "validation.json")
EVAL_START = date(2021, 6, 1)   # matches ab_eval.py EVAL_START default
STORED_VERSION_ID = 70          # AlgorithmVersion.id == 70 == "v70" (DEFAULT_SELECTOR_VERSIONS)

BUCKETS = [
    (None, 25, "<=25"),
    (26, 30, "26-30"),
    (31, 69, "31-69"),
    (70, 74, "70-74"),
    (75, 79, "75-79"),
    (80, 84, "80-84"),
    (85, 89, "85-89"),
    (90, 94, "90-94"),
    (95, None, "95+"),
]


def bucket_of(score):
    for lo, hi, lab in BUCKETS:
        if lo is not None and score < lo:
            continue
        if hi is not None and score > hi:
            continue
        return lab
    return "?"


def main():
    print("=" * 78)
    print("P2 -- per-bucket ScoreSimulator exact-match parity (HOSTILE_REVIEW A-12)")
    print("=" * 78)
    print(f"checked path (simulator side, on-disk): {ARM_LEGACY}")
    print(f"checked path (aggregate summary only)  : {VALIDATION_JSON}")
    print()

    if not os.path.exists(ARM_LEGACY):
        print("MISSING: arm_legacy.parquet not found. Exact path checked:")
        print(f"  {ARM_LEGACY}")
        print("Per brief: do NOT regenerate (a ReSim rebuild is out of scope). Stopping.")
        return 0

    sim = pl.read_parquet(ARM_LEGACY)
    print(f"loaded simulator side: {sim.height} rows, columns={sim.columns}")
    if sim.height == 0:
        print("MISSING: arm_legacy.parquet is empty.")
        return 0

    # ---- STORED side: read-only MySQL SELECT (why this script cannot be foreground) ----
    try:
        from database.models.core import Score, AlgorithmVersion
    except Exception as e:
        print(f"MISSING: could not import Score/AlgorithmVersion models -- {e}")
        return 0

    try:
        ver = AlgorithmVersion.get_by_id(STORED_VERSION_ID)
        print(f"AlgorithmVersion id={STORED_VERSION_ID}: git_commit={ver.git_commit} "
              f"notes={ver.notes!r}")
    except Exception as e:
        print(f"NOTE: AlgorithmVersion.get_by_id({STORED_VERSION_ID}) failed -- {e} "
              f"(continuing; the Score query below is the authoritative check)")

    q = (Score.select(Score.symbol, Score.date, Score.overall)
         .where((Score.version == STORED_VERSION_ID)
                & (Score.date >= EVAL_START)
                & (Score.overall.is_null(False)))
         .tuples())
    stored = {}
    n_rows = 0
    for sym, d, o in q:
        stored[(sym, d)] = int(o)
        n_rows += 1
    print(f"MySQL SELECT: Score rows with version={STORED_VERSION_ID} "
          f"and date>={EVAL_START.isoformat()}: {n_rows}")

    if n_rows == 0:
        print()
        print(f"MISSING: zero stored Score rows at version={STORED_VERSION_ID} "
              f"(v70) in the eval window. Possible causes: version purged, or the "
              f"active-scores pointer no longer resolves version id 70. "
              f"Cannot stratify parity by STORED-score bucket without this data.")
        return 0

    # ---- join sim (symbol,date,overall_sim) to stored (symbol,date,overall_stored) ----
    common = []
    for sym, d_str, o_sim in sim.select(["symbol", "date", "overall"]).iter_rows():
        d = date.fromisoformat(d_str) if isinstance(d_str, str) else d_str
        o_st = stored.get((sym, d))
        if o_st is not None and o_sim is not None:
            common.append((int(o_st), int(o_sim)))

    print(f"common (symbol,date) rows (sim ^ stored): {len(common)}")
    if not common:
        print("MISSING: no overlapping (symbol,date) rows between the simulator arm and "
              "the stored version -- cannot compute per-bucket parity.")
        return 0

    per_bucket = {lab: {"n": 0, "exact": 0, "abs_sum": 0.0} for _, _, lab in BUCKETS}
    total_exact = 0
    total_abs = 0.0
    for o_st, o_sim in common:
        lab = bucket_of(o_st)
        b = per_bucket.setdefault(lab, {"n": 0, "exact": 0, "abs_sum": 0.0})
        b["n"] += 1
        d = abs(o_st - o_sim)
        b["abs_sum"] += d
        if o_st == o_sim:
            b["exact"] += 1
            total_exact += 1
        total_abs += d

    print()
    print("=" * 78)
    print(f"PER-BUCKET PARITY (stratified by STORED score; N={len(common)}, "
          f"overall exact-match={100.0 * total_exact / len(common):.2f}%, "
          f"overall mean|delta|={total_abs / len(common):.4f})")
    print("=" * 78)
    print(f"  {'bucket':8} {'N':>8} {'exact%':>9} {'mean|delta overall|':>22}")
    for _, _, lab in BUCKETS:
        b = per_bucket.get(lab, {"n": 0, "exact": 0, "abs_sum": 0.0})
        n = b["n"]
        if n == 0:
            print(f"  {lab:8} {0:>8} {'n/a':>9} {'n/a':>22}")
            continue
        exact_pct = 100.0 * b["exact"] / n
        mean_abs = b["abs_sum"] / n
        print(f"  {lab:8} {n:>8} {exact_pct:>8.2f}% {mean_abs:>22.4f}")

    print()
    tradable = per_bucket.get("75-79", {"n": 0, "exact": 0})
    n75 = sum(per_bucket.get(lab, {"n": 0})["n"] for _, _, lab in BUCKETS
              if lab in ("75-79", "80-84", "85-89", "90-94", "95+"))
    ex75 = sum(per_bucket.get(lab, {"exact": 0})["exact"] for _, _, lab in BUCKETS
               if lab in ("75-79", "80-84", "85-89", "90-94", "95+"))
    if n75:
        print(f"pooled >=75 (the tradable call band A-12 flags): N={n75} "
              f"exact-match={100.0 * ex75 / n75:.2f}%  "
              f"HOLLOW threshold check (<95%): "
              f"{'HOLLOW' if (100.0 * ex75 / n75) < 95.0 else 'SOUND'}")
    print()
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
