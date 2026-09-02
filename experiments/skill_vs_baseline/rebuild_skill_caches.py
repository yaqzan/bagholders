"""Force-rebuild the skill_vs_baseline OOS-critical parquet caches.

Why: `.cache/experiment_data/skill_v{NN}_allscores_2016.parquet` and
`price_feats_raw_2015.parquet` are build-once caches — `materialize_polars` never
invalidates them on its own. The pre-registered 2026-12-15 OOS evaluation
(experiments/holdout_oos_2026_12/PREREGISTRATION.md, December pre-step 1 /
known-gap note) reads them through verify_scorecard.run_scorecard(oos=True):
H1/H2 need the ACTIVE version's all-scores cache, H5 needs the pinned v73-vs-v74
pair, and H1's momentum baseline (t_mom) needs price_feats_raw_2015 to extend
past the cutoff. Stale caches => OOS n_window=0 => INSUFFICIENT_N forever
(proven by run_oos_eval.py --selftest, 2026-07-13).

This script is the canonical refresher. It does NOT duplicate any SQL: it
imports skill.py's `_build` and skill_returns.py's `_build_prices` and runs them
with the module-level VID monkey-patched per target version (try/finally
restored — the house staging pattern), forcing `materialize_polars(force=True)`.

Default version set = {73, 74} | {active}: 73/74 are pinned by H5's
retirement-regret read; the active version serves H1/H2 (== 74 today, and this
keeps working unchanged if a v75+ ships before December).

Compute class: parquet-cache build (CLAUDE.md Long-Running Compute) — submit via
`trader queue submit`, never run raw:

    trader queue submit --priority low --window off_market --db heavy --cpu 1 \
      --restartable --env PYTHONIOENCODING=utf-8 --dedup skill-oos-cache-refresh \
      --reason "OOS skill-cache refresh (holdout_oos_2026_12 pre-step 1)" \
      -- python experiments/skill_vs_baseline/rebuild_skill_caches.py

Recurring: TraderSkillOOSCacheRefresh (weekly scheduled task) fires
experiments/holdout_oos_2026_12/refresh_skill_caches_task.ps1, which submits
exactly the command above. See install_refresh_task.ps1 in that directory.

Holdout discipline: this script prints BARE row counts / max dates only (the
run_oos_eval.py --selftest disclosure standard) — never a post-cutoff WR/EV/
verdict number.

Exit code: 0 only if every requested cache rebuilt AND (holdout lock active)
every score cache has >0 post-cutoff rows and price_feats extends past the
cutoff — so a silently-useless rebuild shows up as a FAILED queue task.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Worktree-safety pattern (traps.md "Worktree PYTHONPATH trap"): pin sys.path[0]
# to THIS file's repo root, never trust an inherited global PYTHONPATH.
ROOT = Path(__file__).resolve().parents[2]
assert (ROOT / "strategy_config.py").exists(), f"unexpected ROOT resolution: {ROOT}"
sys.path.insert(0, str(ROOT))

import polars as pl  # noqa: E402

from database.bulk_cache import materialize_polars, cache_path  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402
from experiments._holdout import cutoff_iso, DISABLED as HOLDOUT_DISABLED  # noqa: E402

# Importing these executes their module-level VID lookup (one light DB read each).
import experiments.skill_vs_baseline.skill as skill_mod  # noqa: E402
import experiments.skill_vs_baseline.skill_returns as skill_returns_mod  # noqa: E402

PRICE_FEATS_NAME = "price_feats_raw_2015"


def _resolve_versions(spec: str | None) -> list[int]:
    """'73,v74,active' -> sorted unique ids. Default: {73, 74} | {active}."""
    active = AlgorithmVersion.get_active_scores_version().id
    if not spec:
        return sorted({73, 74, active})
    out = set()
    for tok in spec.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        out.add(active if tok == "active" else int(tok.lstrip("v")))
    return sorted(out)


def rebuild_score_cache(vid: int) -> tuple[Path, int, int, str]:
    """Force-rebuild skill_v{vid}_allscores_{START_YEAR} via skill.py's own builder.

    Returns (path, total_rows, post_cutoff_rows, max_date).
    """
    name = f"skill_v{vid}_allscores_{skill_mod.START_YEAR}"
    prev_vid = skill_mod.VID
    skill_mod.VID = vid
    try:
        path = materialize_polars(name, skill_mod._build, force=True)
    finally:
        skill_mod.VID = prev_vid
    df = pl.read_parquet(path)
    n_post = int(df.filter(pl.col("date") > cutoff_iso()).height)
    return path, int(df.height), n_post, str(df["date"].max())


def rebuild_price_feats() -> tuple[Path, int, str]:
    """Force-rebuild the version-agnostic price-feature parquet (H1 momentum source)."""
    path = materialize_polars(PRICE_FEATS_NAME, skill_returns_mod._build_prices, force=True)
    df = pl.read_parquet(path)
    return path, int(df.height), str(df["date"].max())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--versions", default=None,
                    help="comma list of score versions ('73,v74,active'); "
                         "default = {73, 74} | {active}")
    ap.add_argument("--skip-price-feats", action="store_true",
                    help=f"do not rebuild {PRICE_FEATS_NAME} (H1 momentum baseline)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the rebuild plan and exit without touching anything")
    args = ap.parse_args()

    versions = _resolve_versions(args.versions)
    cutoff = cutoff_iso()
    print(f"[rebuild_skill_caches] target versions: {versions}  "
          f"(active={AlgorithmVersion.get_active_scores_version().id})", flush=True)
    print(f"[rebuild_skill_caches] holdout cutoff: {cutoff}"
          f"{'  (HOLDOUT LOCK DISABLED - freshness asserts skipped)' if HOLDOUT_DISABLED else ''}",
          flush=True)

    if args.dry_run:
        for vid in versions:
            p = cache_path(f"skill_v{vid}_allscores_{skill_mod.START_YEAR}")
            state = f"exists ({p.stat().st_size/1e6:.1f} MB)" if p.exists() else "MISSING"
            print(f"  would force-rebuild {p.name}  [{state}]")
        if not args.skip_price_feats:
            p = cache_path(PRICE_FEATS_NAME)
            state = f"exists ({p.stat().st_size/1e6:.1f} MB)" if p.exists() else "MISSING"
            print(f"  would force-rebuild {p.name}  [{state}]")
        print("  dry-run: nothing rebuilt.")
        return 0

    failures: list[str] = []

    for vid in versions:
        path, n_total, n_post, max_d = rebuild_score_cache(vid)
        print(f"[rebuild_skill_caches] {path.name}: {n_total:,} rows, "
              f"max date {max_d}, post-cutoff rows: {n_post:,}", flush=True)
        if not HOLDOUT_DISABLED and n_post == 0:
            failures.append(f"{path.name}: zero post-cutoff rows (OOS reads would stay "
                            f"INSUFFICIENT_N — is v{vid} still in the scoring cadence?)")

    if not args.skip_price_feats:
        path, n_total, max_d = rebuild_price_feats()
        print(f"[rebuild_skill_caches] {path.name}: {n_total:,} rows, max date {max_d}",
              flush=True)
        if not HOLDOUT_DISABLED and max_d <= cutoff:
            failures.append(f"{path.name}: max date {max_d} does not extend past the "
                            f"cutoff {cutoff} (H1 OOS t_mom would have no momentum join)")

    if failures:
        print("[rebuild_skill_caches] FAILED freshness assertions:", flush=True)
        for f in failures:
            print(f"  - {f}", flush=True)
        return 1
    print("[rebuild_skill_caches] OK — all requested caches rebuilt fresh.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
