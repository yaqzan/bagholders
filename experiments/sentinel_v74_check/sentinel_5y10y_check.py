"""Read-only: replicate the MC-window '5y'/'10y' date bounds (from
experiments/v69_portfolio_retune/profile_frontier.py WINDOWS) as a single
deterministic backtest_cascade run on the ACTIVE (v74) scoring version, for
the Sentinel profile. This is NOT a Monte Carlo run (no seeded bounded-fill,
N=1 deterministic path) -- it exists only to get a window-definition-matched
comparison point against the v70-era MC numbers baked into
algorithm_versions/portfolio_profiles.json. No writes to any table.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
# Pin repo root explicitly (worktree PYTHONPATH trap precedent) -- this box's
# actual repo root is C:\Development\Trader, not the scratchpad dir.
REPO_ROOT = Path(r"C:\Development\Trader")
sys.path.insert(0, str(REPO_ROOT))

from algorithm_versions import research_pack as rp  # noqa: E402
from database.models.core import AlgorithmVersion  # noqa: E402

rp.ensure_db_open(force_reconnect=True)

version = AlgorithmVersion.get(AlgorithmVersion.id == 74)
print(f"version: id={version.id} label={getattr(version, 'label', None)} commit={getattr(version, 'git_commit', None)}")

windows = [
    rp.PortfolioWindow(
        name="mc5y_match",
        start="2021-01-01",
        end="2026-04-15",
        primary_metric="compound_and_drawdown",
        description="Exact bound-match to profile_frontier.py MC '5y' window",
    ),
    rp.PortfolioWindow(
        name="mc10y_match",
        start="2016-06-01",
        end="2026-04-15",
        primary_metric="compound_and_drawdown",
        description="Exact bound-match to profile_frontier.py MC '10y' window",
    ),
]

results = {}
for w in windows:
    print(f"\n=== running {w.name} ({w.start} -> {w.end}) sentinel profile, v74 ===")
    out = rp.run_portfolio_window(version, w, dte="30", portfolio_profile="sentinel")
    results[w.name] = out
    if not out.get("complete"):
        print("  INCOMPLETE:", out.get("error"))
        continue
    m = out.get("metrics", out)  # summarize_portfolio_result may nest under window/metrics or return flat
    # summarize_portfolio_result returns a flat dict merged with window info; print key fields directly
    for k in ["max_dd_pct", "total_return_pct", "terminal_equity", "initial_capital",
              "n_trades", "call_trades", "put_trades", "tp_rate", "sl_rate", "hard_rate",
              "call_tp_rate", "max_open_positions", "avg_open_positions"]:
        if k in out:
            print(f"  {k}: {out[k]}")
    if "max_dd_event" in out:
        print("  max_dd_event:", out["max_dd_event"])

import json
outpath = Path(__file__).resolve().parent / "sentinel_5y10y_result.json"
with open(outpath, "w") as f:
    json.dump(results, f, default=str, indent=2)
print(f"\nwrote {outpath}")
