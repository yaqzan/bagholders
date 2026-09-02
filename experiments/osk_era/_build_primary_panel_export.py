"""ONE-SHOT cross-tree reuse of MAIN's build_discovery_panel, for the OSK
era-layer Stage-1 evidence sweep (orchestrator instruction: "reuse
build_discovery_panel by absolute-path import from MAIN").

DELIBERATE, CONTAINED EXCEPTION to this worktree's usual PYTHONPATH-trap
discipline: this script's ENTIRE job is to import and call MAIN's already
-validated panel-assembly code by absolute path. It runs as an ISOLATED
one-shot process (invoke it as `python experiments/osk_era/_build_primary_
panel_export.py` -- a fresh interpreter, own sys.path) and NEVER imports
this worktree's own `database` / `experiments._holdout` / etc. It writes
its output (a data file, not code) to THIS worktree's .cache/osk_era/ dir.
The main Stage-1 sweep engine (stage1_sweep.py, worktree-native, full
PYTHONPATH-trap guard) then reads that parquet as a plain data file --
no python-level cross-tree import ever touches the main analysis process.

MySQL guard: build_discovery_panel() calls load_or_build_price_cache(),
which only queries MySQL on a cache MISS for some requested symbol. Phase
2's own prior run (osk_validation_phase2.py, already completed -- see
osk_results_phase2.json in MAIN) already warmed
C:\\Development\\Trader\\.cache\\experiment_data\\osk_val_prices.parquet
for this exact symbol set (same discovery window, same panel). This
script asserts price_meta['source'] == 'cache_hit' (or no symbols at all)
and ABORTS with no output written otherwise -- honors the HARD STOP: no
MySQL queries from this program.

Usage:
    python experiments/osk_era/_build_primary_panel_export.py
"""
import json
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

MAIN_ROOT = r"C:\Development\Trader"
MAIN_OSK_DIR = os.path.join(MAIN_ROOT, "experiments", "osk_validation")
for _p in (MAIN_OSK_DIR, MAIN_ROOT):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import polars as pl  # noqa: E402

import osk_validation_phase2 as p2  # noqa: E402
import osk_validation_test as p1  # noqa: E402

assert os.path.abspath(p2.__file__).lower().startswith(MAIN_ROOT.lower()), (
    f"osk_validation_phase2 resolved OUTSIDE MAIN root ({MAIN_ROOT}): {p2.__file__}"
)
assert os.path.abspath(p1.__file__).lower().startswith(MAIN_ROOT.lower()), (
    f"osk_validation_test resolved OUTSIDE MAIN root ({MAIN_ROOT}): {p1.__file__}"
)

OUT_DIR = r"C:\Development\Trader-exp-osk-era\.cache\osk_era"
OUT_PATH = os.path.join(OUT_DIR, "discovery_panel_primary.parquet")
OUT_META = os.path.join(OUT_DIR, "discovery_panel_primary_meta.json")


def main():
    panel = p1.load_panel()
    rs_slim = p1.load_rs_ledger_slim()
    disc_panel, price_meta, join_meta = p2.build_discovery_panel(panel, rs_slim)

    src = price_meta.get("source")
    if src not in ("cache_hit", "skipped_no_symbols"):
        print(
            "ABORT: price cache source=%r (not a clean cache hit) -- calling "
            "load_or_build_price_cache would require a MySQL top-up query. "
            "HARD STOP forbids MySQL from this program. No output written." % src
        )
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    # Serialize date as ISO string for a clean cross-process handoff (avoid
    # polars Date-type / arrow-schema coupling across separate interpreter
    # invocations -- the worktree-side reader treats dates as plain strings,
    # matching osk_era_ledger.parquet's own convention).
    out = disc_panel.with_columns(pl.col("date").cast(pl.Utf8))
    out.write_parquet(OUT_PATH)

    meta = {
        "source_panel": p2.PANEL_PATH,
        "source_rs_ledger": p2.RS_LEDGER,
        "discovery_start": p2.DISCOVERY_START,
        "discovery_end": p2.DISCOVERY_END,
        "n_rows": out.height,
        "n_skew_nonnull": int(out["skew"].drop_nulls().len()),
        "n_pnl15_nonnull": int(out["pnl15"].drop_nulls().len()),
        "price_meta": price_meta,
        "join_meta": join_meta,
        "columns": out.columns,
    }
    with open(OUT_META, "w", encoding="ascii") as f:
        json.dump(meta, f, indent=2, default=str)
    print("DONE", OUT_PATH)
    print(json.dumps(meta, indent=2, default=str))


if __name__ == "__main__":
    main()
