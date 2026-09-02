"""Sanity-check the v3 champion across the full (overall × mcap) grid.

Visualizes new_overall as a function of (overall, mcap_b) for the champion
parameters. Confirms:
  - Large caps (>=$80B) untouched
  - Small/mid caps in 70-74 only mildly dampened (preserves natural wave)
  - Small/mid caps in 80-84 strongly dampened (out of qualifying)
  - Smooth interpolation everywhere
"""
from __future__ import annotations
import io, sys, math
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# v3 CHAMPION
GATE_LO, GATE_HI = 70, 84
LOG_LO, LOG_HI = 0.50, 1.90  # $3.16B → $79.43B
ALPHA = 0.80
TARGET = 61
MCAP_POWER = 0.70
SCORE_POWER = 1.50


def dampen(overall, mcap_b):
    if overall < GATE_LO or overall > GATE_HI: return overall
    if mcap_b is None or mcap_b <= 0: return overall
    log_mcap = math.log10(mcap_b)
    if log_mcap >= LOG_HI: return overall
    mcap_raw = max(0.0, min(1.0, (LOG_HI - log_mcap) / (LOG_HI - LOG_LO)))
    mcap_factor = mcap_raw ** MCAP_POWER
    score_raw = max(0.0, min(1.0, (overall - GATE_LO) / (GATE_HI - GATE_LO)))
    score_factor = score_raw ** SCORE_POWER
    weakness = mcap_factor * score_factor
    drop = ALPHA * weakness * (overall - TARGET)
    return round(overall - drop)


def main():
    print(f'V3 CHAMPION: LO={LOG_LO} HI={LOG_HI} α={ALPHA} T={TARGET} mp={MCAP_POWER} sp={SCORE_POWER}')
    print()

    # Grid of (overall, mcap) → new_overall
    overalls = [70, 72, 74, 76, 78, 80, 82, 84, 86, 88]
    mcaps = [0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 10, 20, 50, 100, 250, 1000, 5000]

    print(f"{'Mcap_b':>10s} | " + ' '.join(f'{o:>3d}' for o in overalls))
    print('-' * (12 + 4 * len(overalls)))
    for mc in mcaps:
        label = f'${mc:.0f}M' if mc < 1 else f'${mc:.1f}B' if mc < 1000 else f'${mc/1000:.0f}T'
        row_vals = [dampen(o, mc) for o in overalls]
        # Color via marker: '=' for unchanged, drop value otherwise
        markers = []
        for i, o in enumerate(overalls):
            new = row_vals[i]
            if new == o: markers.append(f'{new:>3d}')
            else:
                if new < 75: markers.append(f'\033[91m{new:>3d}\033[0m')   # red - displaced
                elif new < o - 0.5: markers.append(f'\033[93m{new:>3d}\033[0m') # yellow - dampened
                else: markers.append(f'{new:>3d}')
        print(f"{label:>10s} | " + ' '.join(markers))

    print()
    print('Legend: red = dropped below 75 (out of qualifying), yellow = dampened but still ≥75, plain = unchanged')

    # Specifically check: "natural wave" preservation — at 70-74, micro-caps
    # should only get tiny dampening (preserve the wave the user described)
    print('\nNATURAL WAVE PRESERVATION CHECK (small caps at 70-74):')
    for mc in [0.5, 1.0, 2.0]:
        for o in [70, 71, 72, 73, 74]:
            new = dampen(o, mc)
            print(f'  ${mc}B at overall={o}: dampened to {new} (Δ {new-o:+d})')

    # Major displacement check (mid-cap at 80-84):
    print('\nMAJOR DISPLACEMENT CHECK (mid-cap at 80-84):')
    for mc in [3.0, 5.0, 10, 20]:
        for o in [80, 82, 84]:
            new = dampen(o, mc)
            print(f'  ${mc}B at overall={o}: dampened to {new} (Δ {new-o:+d})')

    # Large-cap preservation
    print('\nLARGE-CAP PRESERVATION CHECK ($50B+):')
    for mc in [50, 100, 500, 2000]:
        for o in [76, 80, 84]:
            new = dampen(o, mc)
            print(f'  ${mc}B at overall={o}: dampened to {new} (Δ {new-o:+d})')


if __name__ == '__main__':
    main()
