"""
Experiment runner: load simulator data once, swap scoring functions, diff-assess each.

Usage
-----
python -m experiments.run_experiments --symbols ASTS MRVL LUNR AMAT --days 365
python -m experiments.run_experiments --days 365         # full universe
python -m experiments.run_experiments --variants V1_raw_gate V2_conf_shrink
"""
import argparse
import sys
from colorama import Fore, Style

from simulator import ScoreSimulator
from experiments.score_variants import VARIANTS


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', nargs='*', default=None)
    p.add_argument('--days', type=int, default=365)
    p.add_argument('--variants', nargs='*', default=None,
                   help='Subset of variant names to run; default = all')
    p.add_argument('--db-version', type=int, default=None,
                   help='AlgorithmVersion id to use as DB-side baseline. '
                        'Without this, mixed-version peaks pollute the comparison.')
    args = p.parse_args()

    variants_to_run = args.variants or list(VARIANTS.keys())
    for v in variants_to_run:
        if v not in VARIANTS:
            print(f"Unknown variant: {v}. Known: {list(VARIANTS.keys())}", file=sys.stderr)
            sys.exit(1)

    print(Fore.CYAN + "=" * 80)
    print(f"  Loading data once for {len(args.symbols) if args.symbols else 'all'} symbols, {args.days}d")
    print("=" * 80 + Style.RESET_ALL)

    sim = ScoreSimulator(symbols=args.symbols, lookback_days=args.days)

    # Single-symbol passed → diff_assess uses that symbol; otherwise full
    diff_symbol = args.symbols[0] if args.symbols and len(args.symbols) == 1 else None

    for v_name in variants_to_run:
        print()
        print(Fore.YELLOW + "=" * 80)
        print(f"  Variant: {v_name}")
        print("=" * 80 + Style.RESET_ALL)
        sim.scoring_fn = VARIANTS[v_name]
        scores = sim.simulate()
        sim.diff_assess(scores, symbol=diff_symbol, db_version=args.db_version)


if __name__ == '__main__':
    main()
