# MCD/CWWD/WVD Participation Wave Investigation

**Status:** NO SHIP as of 2026-05-12.

## Question

POET had multiple recent CALL scores in the low 70s that behaved like higher-tier
signals. Prior algorithm versions scored POET higher, and archaeology pointed to
MCD/WVD pressure as the main suppressor. The investigation tested whether a
smooth Participation Wave could restore POET-like continuation cases without
using a hard post-score threshold.

## Best Folded Wave

The best production-shaped candidate was the folded/no-velocity Participation
Wave. It removed the circular `score_velocity_7d` dependency and used:

- dollar-volume expansion,
- realized-vol price impulse,
- weekly adjustment strength,
- weekly-volume force shape,
- existing MCD/CWWD/WVD dampener pressure.

This candidate passed the 2020+ broad-domain Stage 1 approximation:

- 2020+ affected cohort: WR7 75.76% vs rest 65.37%, lift +10.39pp, z 3.54.
- POET 2025-10-03: 74 -> 75, WR7 win and 15d option-aligned win.

But the 2016-2020 robustness check failed:

- 2016-2020 affected cohort: WR7 70.00% vs rest 65.79%, lift +4.21pp, z 1.56.
- W2 stayed positive but faded by horizon: WR3 +8.41pp, WR5 +5.66pp, WR7 +4.21pp,
  WR15 +2.07pp, WR30 +0.67pp.
- W6 worsened an existing 85+ vs 80+ gradient inversion.

Decision: not shippable as-is. The signal is real in the recent substrate but
not robust enough over the 2016-2020 slice.

## Sector ETF Breadth Rescue

Sector ETF breadth/confirmation was tested as a smooth multiplier on the folded
wave, using same-day sector ETF `Score.overall` plus cross-sector breadth. It
does not rescue the candidate.

Best sector-breadth confirmation:

- 2016-2020: WR7 76.79% vs rest 65.79%, lift +10.99pp, z 2.45, N 112.
- This improves the weak era directionally, but misses the W1 z >= 3 gate.
- 2020+: WR7 79.31% vs rest 65.38%, lift +13.93pp, z 2.23, N 58.
- The best breadth-confirmation row also suppresses the key POET case:
  POET 2025-10-03 goes from folded-wave 74 -> 75 back to 74 because
  cross-sector ETF breadth was weak that day.

A POET-preserving own-sector ETF bell shape was also spot-checked. It can make
2016-2020 pass in a narrow pocket:

- 2016-2020: z 3.05, N 112, WR7 79.46%.
- But 2020+ collapses to z 1.25, so it is regime-split and not robust.

Decision: sector ETF breadth/confirmation should not be used to save this
Participation Wave. It either fails W1 or kills the POET behavior the mechanism
was intended to preserve.

## Artifacts

- `experiments/mcd_cwwd_wvd_recal/participation_wave.py`
- `experiments/mcd_cwwd_wvd_recal/domain_stress.py`
- `experiments/mcd_cwwd_wvd_recal/sector_breadth_rescue.py`
- `experiments/mcd_cwwd_wvd_recal/runs/domain_stress_min60_2016_2020_folded_20260511_230532/domain_stress_summary.json`
- `experiments/mcd_cwwd_wvd_recal/runs/sector_breadth_rescue_20260511_235631/sector_breadth_fast_summary.json`
- `experiments/mcd_cwwd_wvd_recal/runs/sector_breadth_rescue_20260511_235631/sector_grid_2016_2020.csv`

## Follow-Up

- Do not ship the folded Participation Wave.
- Do not retry broad sector ETF breadth confirmation as the rescue path for this
  POET/MCD/CWWD/WVD problem.
- Market-cap-dependent TP/SL remains a separate Stage 2 question and was not
  tested here.
