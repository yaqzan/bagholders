"""
One-shot: rebuild daily scores for every stock under the current HEAD version.
Indicators and WeeklyScores unchanged — only daily Score rows are regenerated.
"""
import time
from colorama import Fore, Style, init
init(autoreset=True, convert=True)

from database.models.core import Stock, AlgorithmVersion


def main():
    v = AlgorithmVersion.get_or_create_current()
    print(Fore.CYAN + f"Rebuilding daily scores for version id={v.id} ({getattr(v, 'version_hash', None) or ''})" + Style.RESET_ALL)

    stocks = list(Stock.select().where(Stock.forward_pe.is_null(False)))
    print(f"stocks: {len(stocks)}")

    t0 = time.time()
    for i, s in enumerate(stocks, 1):
        t1 = time.time()
        try:
            s.calculate_scores_batched(silent=True)
            dt = time.time() - t1
            print(f"  [{i}/{len(stocks)}] {s.symbol:<8} {dt:5.1f}s")
        except Exception as e:
            print(Fore.RED + f"  [{i}/{len(stocks)}] {s.symbol}: FAILED — {e}" + Style.RESET_ALL)

    total = time.time() - t0
    print(Fore.GREEN + f"\nDone in {total/60:.1f} min" + Style.RESET_ALL)


if __name__ == '__main__':
    main()
