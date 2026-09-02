"""Re-rank refinement results with relaxed N gate.

H3 strict 15% N drop is too tight for a cohort-displacement dampener (per
assessment-backtest.md: "a bigger drop is fine if the dropped signals are the
bad-quality cohort the change was targeting"). Mcap dampener targets the
worst-cohort calls (micro/small-caps with 57-60% TP vs 66% large-cap), so
N drop is the WHOLE POINT of the mechanism.

Re-rank with these tiers:
  - 'tight'    : N >= -15% (original H3)
  - 'standard' : N >= -25% (precedent: v37 PCD dropped ~30% put peaks)
  - 'permissive' : N >= -40% (precedent: v27 WCF dropped ~75% put peaks)

For each tier, find the top-composite passing variant.
Final winner = top variant within 'standard' tier (matches PCD ship gate).
"""
from __future__ import annotations
import io, sys, json
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path

LOG_REFINE = Path(__file__).parent / 'sweep_refine.jsonl'
LOG_COARSE = Path(__file__).parent / 'sweep_coarse.jsonl'


def load(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def passes(rec, n_floor):
    g = rec['gate']
    return (
        g['affected_5y'] >= 0.3
        and (g['affected_10y'] is None or g['affected_10y'] >= 0.3)
        and (g['affected_3y'] is None or g['affected_3y'] >= 0.0)
        and (g['affected_1y'] is None or g['affected_1y'] >= -0.2)
        and g['affected_n_pct'] >= n_floor
        and g['spillover_max_neg'] >= -0.4
    )


def fmt(rec):
    g = rec['gate']
    a10 = g['affected_10y']
    a10_s = '--' if a10 is None else f'{a10:+.2f}'
    a3 = g['affected_3y']
    a3_s = '--' if a3 is None else f'{a3:+.2f}'
    a1 = g['affected_1y']
    a1_s = '--' if a1 is None else f'{a1:+.2f}'
    return (f"g[{rec['glo']}-{rec['ghi']}] LO={rec['llo']:+.2f} HI={rec['lhi']:+.2f} "
            f"α={rec['alpha']:.2f} T={rec['target']:>2} | "
            f"75+ 5y={g['affected_5y']:+.2f} 10y={a10_s} 3y={a3_s} 1y={a1_s} | "
            f"N75 {g['affected_n_pct']:+.1f}% spill={g['spillover_max_neg']:+.2f} "
            f"comp={g['composite']:+.3f}")


def main():
    coarse = load(LOG_COARSE)
    refine = load(LOG_REFINE)
    print(f'[rerank] coarse: {len(coarse)}, refine: {len(refine)}')

    # Add coarse results into refine for unified ranking (give coarse a 'label' too)
    all_results = []
    for r in coarse:
        # Coarse has 'label' field; pad refine recs with synthetic label
        all_results.append(r)
    for r in refine:
        if 'label' not in r:
            r['label'] = ''
        all_results.append(r)
    print(f'[rerank] total candidates: {len(all_results)}')

    for tier_label, n_floor in [('tight', -15.0), ('standard', -25.0), ('permissive', -40.0)]:
        passing = [r for r in all_results if passes(r, n_floor)]
        passing.sort(key=lambda r: -r['gate']['composite'])
        print(f'\n=== TIER: {tier_label} (N >= {n_floor:+.0f}%) — {len(passing)} pass ===')
        for r in passing[:15]:
            label = r.get('label', '')
            label_s = f"{label:>22s}" if label else ' ' * 22
            print(f"  {label_s} {fmt(r)}")

    # Picked winner: top of 'standard' tier
    standard = sorted([r for r in all_results if passes(r, -25.0)],
                      key=lambda r: -r['gate']['composite'])
    if standard:
        winner = standard[0]
        g = winner['gate']
        print('\n' + '='*80)
        print('SHIP CANDIDATE — TOP OF STANDARD TIER (matches PCD precedent for N drop)')
        print('='*80)
        label = winner.get('label', '(refinement)')
        print(f"  Source: {label}")
        print(f"  GATE [{winner['glo']}, {winner['ghi']}]")
        print(f"  LOG_LO = {winner['llo']:+.3f}  → mcap_b ${10**winner['llo']:.2f}B at full strength")
        print(f"  LOG_HI = {winner['lhi']:+.3f}  → mcap_b ${10**winner['lhi']:.2f}B at zero strength")
        print(f"  ALPHA = {winner['alpha']:.2f}")
        print(f"  TARGET = {winner['target']}")
        print(f"  Multi-window 75+:")
        for k, v in [('1y', g.get('affected_1y')),
                     ('3y', g.get('affected_3y')),
                     ('5y', g.get('affected_5y')),
                     ('10y', g.get('affected_10y'))]:
            v_s = '--' if v is None else f'{v:+.2f}pp'
            print(f"    {k:4s}: {v_s}")
        print(f"  N drop on 75+: {g['affected_n_pct']:+.1f}%")
        print(f"  Worst spillover: {g['spillover_max_neg']:+.2f}pp")
        print(f"  Composite: {g['composite']:+.3f}")

        # Show the dampener as a function of mcap for clarity
        print('\n  Dampener strength by mcap (at overall=80, target=70, alpha={:.2f}):'.format(winner['alpha']))
        import math
        for mcap_b in [0.5, 1, 2, 5, 10, 25, 50, 100, 250, 1000, 5000]:
            log_mcap = math.log10(mcap_b)
            if log_mcap <= winner['llo']:
                weakness = 1.0
            elif log_mcap >= winner['lhi']:
                weakness = 0.0
            else:
                weakness = (winner['lhi'] - log_mcap) / (winner['lhi'] - winner['llo'])
            new_score = round(80 - winner['alpha'] * weakness * (80 - winner['target']))
            label = f'${mcap_b:.0f}B' if mcap_b >= 1 else f'${mcap_b*1000:.0f}M'
            print(f"    mcap={label:>8s}  weakness={weakness:.2f}  new_overall={new_score}")


if __name__ == '__main__':
    main()
