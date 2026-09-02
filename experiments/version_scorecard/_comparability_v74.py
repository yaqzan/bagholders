"""One-shot v74 comparability driver: supply/hydration row -> PRF matched sizing.

The v74 research pack (#188) covers parts (1) of the deploy.md "post-recalc
comparability unit"; this completes parts (2) + (3): the per-version supply/
hydration row (signal_supply.py, merges into the shared supply_burstiness.json)
and the PRF instrument (portfolio_response.py --materialize -> derived_portfolio.json).
PRF refuses to derive while supply is approximated, so order is enforced here.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(args):
    print(f">>> {' '.join(args)}", flush=True)
    subprocess.run([sys.executable, *args], check=True, cwd=ROOT)


if __name__ == "__main__":
    run(["experiments/version_scorecard/signal_supply.py", "--versions", "v74"])
    run(["experiments/version_scorecard/portfolio_response.py", "--materialize", "v74"])
    print("v74 comparability unit complete: supply_burstiness.json (v74 row) + "
          "derived_portfolio.json (PRF matched sizing).", flush=True)
