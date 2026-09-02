# Temporal Echo Findings

## Run 20260511_114700

Input:

- Active score version: v46 / `f274eb6`
- Lookback: 1825 calendar days, holdout-filtered to `CALIBRATION_CUTOFF_DATE`
- Signal rows: 925,214
- Prior pairs: 5,349,950
- Sweep variants: 120
- Outputs: `.cache/temporal_echo/runs/20260511_114700/`

### Transition Stats

Pair-level continuation stats do not reproduce the old "W60 dominates" result
cleanly at the broad all-score level. Calls show only a modest spread across
prior patterns:

- prior W7 win -> current WR15 64.5%, opt TP15 51.6%
- prior W7 miss -> current WR15 63.6%, opt TP15 51.3%
- prior W60 win -> current WR15 64.6%, opt TP15 51.5%

Puts remain flat:

- prior W7 win -> current WR15 65.4%, opt TP15 41.9%
- prior W7 miss -> current WR15 65.9%, opt TP15 42.0%
- prior W60 win -> current WR15 65.5%, opt TP15 41.9%

These are pair-level rows, so a current signal with multiple priors appears
multiple times. Treat this as a map of where signal might exist, not a final
independent-cohort estimate.

### Utility Sweep

The best raw utility variant expanded tradable call N strongly, but diluted the
75-79 destination tier too much for a ship candidate:

- tradable call N: +9,003
- promoted-to-trade: +10,599
- weighted incremental WR7 winners: +7,063.2
- 75-79 WR7: -3.76pp
- 75-79 option TP15: -3.88pp

Best candidate under a stricter <=1pp tier-drop screen:

- utility: +644.98
- tradable call N: +772
- promoted-to-trade: +2,634
- 75-79 WR7: +0.60pp
- 75-79 option TP15: -0.94pp
- 80-84 N: -247, but WR7 +8.08pp and option TP15 +10.98pp

Best candidate under a <=0.5pp tier-drop screen:

- utility: +430.16
- tradable call N: +424
- promoted-to-trade: +2,311
- 75-79 WR7: +0.79pp
- 75-79 option TP15: -0.49pp

### Readout

This is not ship-ready yet, but the stricter-screen candidates are worth a
second sweep. The next pass should constrain the objective directly:

- hard cap 75-79 option TP15 dilution at roughly -1pp;
- prefer smaller lift caps and target 80/85 only when destination quality holds;
- add unique-current-signal transition stats after aggregating priors into echo
  strength deciles;
- run a focused drill around the <=1pp candidate basin before any production
  scoring proposal.

## Run 20260511_drill_direct

Input:

- Same v46 / 1825d cached feature set as `20260511_114700`
- Drill variants: 200 around the prior <=1pp candidate basin
- Ranking: screened utility with hard caps `max_wr_drop=1.0pp` and
  `max_opt_drop=1.0pp`
- Outputs: `.cache/temporal_echo/runs/20260511_drill_direct/`

### Unique-Signal Echo Deciles

The new decile table aggregates priors into one echo value per current call
signal. This avoids the pair-duplication problem from the earlier transition
table.

The broad echo score is not globally monotonic:

- best WR15/WR30 region is mid-zero echo: decile 4 has WR15 67.5% / WR30 70.4%
  / opt TP15 54.3%
- top echo decile is weaker: WR15 63.6% / WR30 64.9% / opt TP15 51.4%
- negative echo is not obviously bad: decile 1 WR15 64.3% / opt TP15 51.7%

Interpretation: this is not a clean "more echo is always better" law. The
useful behavior appears to be near-threshold routing/capacity, not a standalone
monotonic echo alpha score.

### Constrained Drill Winner

Best screen-passing candidate:

- utility: +2,187.98
- tradable call N: +2,745
- promoted-to-trade: +4,352
- changed rows: 157,499
- worst WR7 drop: -0.47pp
- worst option TP15 drop: -0.995pp

Bucket breakdown:

| Bucket | dN | d winners | dWR7 | dOptTP15 |
|---|---:|---:|---:|---:|
| 95+ | -7 | -5 | +5.65pp | +15.94pp |
| 90-94 | -1 | +4 | +5.69pp | +3.18pp |
| 85-89 | +31 | +28 | +1.05pp | +3.16pp |
| 80-84 | -21 | +63 | +7.45pp | +9.18pp |
| 75-79 | +2,743 | +1,986 | -0.47pp | -0.995pp |

Formula parameters:

```json
{
  "tau": 33.53068369258105,
  "score_power": 0.6484629680603173,
  "norm": 1.6244582174624052,
  "alpha": 1.1098625228335859,
  "max_lift": 5.324483581447521,
  "target": 82.0,
  "min_echo": 0.03564202937433027,
  "w7": 0.13129125416763923,
  "w15": 0.04059352676568437,
  "w30": 1.1451706248544933,
  "w60": 0.6634847709237296,
  "loss_penalty": 0.22488370555188553,
  "fizzler_penalty": 0.8123506166080852,
  "allow_puts": false
}
```

### Readout

This is a real candidate for further validation, but still not a ship:

- It passes the explicit "utility over strict Stage 1" screen.
- It keeps destination dilution inside the requested "flat/slightly negative"
  envelope.
- It is call-only, consistent with the persistent no-edge put finding.
- It needs a unique-signal implementation check and MC smoke because +2,745
  tradable call N materially changes cascade capacity.

Next validation should be:

1. Re-score this exact variant through `ScoreSimulator` / in-memory assessment
   instead of the parquet routing approximation.
2. Add explicit before/after discrete-bucket WR3/WR5/WR7/WR15/WR30 tables.
3. Run a 22-now smoke MC because the mechanism increases call capacity enough
   that cascade slot competition matters.

### Multi-Window Follow-Up

`candidate_validation.md` checked the top screened utility candidate across
WR3/WR5/WR7/WR15/WR30. It exposed mild short/long-window dilution in the
expanded 75-79 tier:

- 75-79 dN: +2,743
- WR3 -1.29pp
- WR5 -1.12pp
- WR7 -0.47pp
- WR15 -0.50pp
- WR30 -1.25pp
- opt TP15 -0.99pp

This is better than the broad raw-utility winner, but still marginal if we
want "slightly negative" to mean roughly <=1pp across all windows, not just WR7
and option TP15.

`top15_multiwindow.json` then checked the top 15 screened candidates. The
cleanest high-utility candidate is rank 6 from the drill:

- utility: +1,425.46
- tradable call N: +1,791
- promoted-to-trade: +3,434
- worst 75-79 drop across WR3/5/7/15/30/opt15: -0.77pp
- 75-79 details: WR3 -0.76pp, WR5 -0.77pp, WR7 -0.16pp, WR15 -0.14pp,
  WR30 -0.75pp, opt TP15 -0.70pp

Rank 13 is the most conservative top-15 candidate:

- utility: +866.6
- tradable call N: +1,038
- promoted-to-trade: +2,812
- worst 75-79 drop: -0.92pp
- 75-79 has WR3/WR5/WR7/WR15 positive or flat, with only WR30 -0.92pp and
  opt TP15 -0.31pp negative.

Current recommendation: use rank 6 as the next serious candidate, not the rank
1 screened-utility winner. It keeps the multi-window damage inside the intended
"flat to slightly negative" envelope while preserving materially more added N
than rank 13.

Rank 6 parameters:

```json
{
  "tau": 40.167303886177535,
  "score_power": 0.6281942103863293,
  "norm": 1.2591289035385456,
  "alpha": 0.8303841575227674,
  "max_lift": 4.420164793824554,
  "target": 82.0,
  "min_echo": 0.04889786965184006,
  "w7": 0.15934815792492626,
  "w15": 0.08875940908243371,
  "w30": 0.5934974664235093,
  "w60": 0.65,
  "loss_penalty": 0.1885449909679346,
  "fizzler_penalty": 0.7348462309277648,
  "allow_puts": false
}
```

### V46 Gate75 Portfolio Follow-Up

After the algorithm pointer reverted to v46 (`f274eb6`), the v48/v47 artifacts
were treated as invalid for shipping. The v46-only rerun used explicit v46
feature/prior caches and a 75+ destination gate:

- signals: `.cache/temporal_echo/signals_v46_1825_g1_90.parquet`
- priors: `.cache/temporal_echo/priors_v46_1825_g1_90.parquet`
- candidate source: `20260511_drill_direct` rank 6
- gate: only keep echo adjustments that land at `new_score >= 75`

Gate-aware top-30 review kept rank 6 as the best practical candidate:

- utility: +1,425.46
- tradable call N: +1,791
- promoted-to-trade: +3,434
- changed rows after 75+ gate: 4,384
- worst multi-window WR drop: -0.775pp

The same-version `22-now` 60-iteration MC smoke looked strong:

- 75+ calls: 3,044 -> 5,867
- call trades: +1,117
- put trades: -969
- mean DD: -11.91pp
- worst DD: -6.82pp
- mean return: improved
- median return: slightly worse

However, annual validation blocked shipping. The pinned v46 2025 check repeated
the drawdown problem:

- 2025 30-iteration check: mean DD +8.89pp, worst DD +4.18pp
- returns improved, but DD moved the wrong way
- call TP fell by roughly 0.9pp to 6.2pp depending on run length

Partial annual windows were mixed:

- 2022: DD improved strongly, returns fell
- 2023: DD and returns improved
- 2024: mean DD roughly flat, worst DD +1.2pp, returns fell
- 2025: DD worsened materially

Current decision: no ship as-is. Rank 6 gate75 is a real capacity-expansion
mechanism, but it needs an additional regime/year guard before production. The
next experiment should preserve the 75+ gate and test a soft brake for the
2025-like case instead of widening the formula again.

## V50 Test

Run:

- AlgorithmVersion id: 50
- commit: `b0c1954`
- description: `v50 scoring: conservative stoch conviction wave`
- build/run dir: `.cache/temporal_echo/runs/20260511_v50_drill_direct_retry/`
- feature cache: `.cache/temporal_echo/signals_v50_1825_g1_90.parquet`
- prior cache: `.cache/temporal_echo/priors_v50_1825_g1_90.parquet`

The v50 build required changing `build_features.py` score loading from yearly
chunks to month-sized chunks. The yearly v50 query crashed inside PyMySQL while
fetching the larger 2021 result set; month-sized chunks completed cleanly.

Feature build:

- signals: 966,790 rows
- prior pairs: 5,595,463 rows

The v50 drill found a much larger score-layer opportunity than v46:

- best screened utility: +3,856.46
- tradable call N: +4,591
- promoted-to-trade: +5,851
- worst WR7 drop: -0.16pp
- worst option TP15 drop: -0.41pp

After applying the final 75+ destination gate, the cleanest practical candidate
was rank 3 rather than rank 1:

- rank 3 utility: +2,144.34
- tradable call N: +2,449
- promoted-to-trade: +3,886
- changed rows: 5,674
- worst multi-window WR drop: -0.805pp

Rank 3 22-now 30-iteration MC smoke looked strong:

- 75+ calls: 3,030 -> 6,507
- call trades: +1,271
- put trades: -1,195
- mean DD: -9.17pp
- worst DD: -6.33pp
- mean and median return improved

But the 2025 pinned check failed the drawdown gate:

- 75+ calls: 762 -> 1,672
- call trades: +340
- mean return improved
- median return improved
- mean DD: +10.9pp
- worst DD: +5.7pp

Current v50 decision: no ship as-is. v50 strengthens the score-layer and
aggregate 22-now readout, but the same 2025 drawdown failure remains. Next work
should test a regime/date-condition brake around the 2025-like environment,
not the unconstrained echo lift itself.
