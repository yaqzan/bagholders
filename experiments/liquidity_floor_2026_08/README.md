# liquidity_floor_2026_08

**Report-only measurement study. Generated 2026-08-11.** No scoring or portfolio code
changed; no ship claim; no recommendation. Computes the supply-cost / fill-fidelity /
realized-EV cost-benefit curve of a hypothetical ENTRY liquidity floor (premium and/or
volume threshold applied at entry), purely by filtering existing real-contract event data
and re-tabulating. Orchestrator audits the raw numbers in `out/`.

## Question

If entries below a premium and/or volume threshold are refused, how much of the TP
fill-fidelity miss mass (measured in `experiments/tp_fill_fidelity_30dte/`) and how much
signal supply / realized EV does that shed or give up?

## Inputs (all pre-built, read-only; none modified)

- `B:\polygon_derived\ledger_v2\ledger.parquet` -- FF-1 real-contract ledger header rows
  (4,936 total, 4,403 `status=='kept'`). Real entry premiums, real close-only
  tp_touch_date/sl_touch_date/mark_cdNN outcomes, entry_volume, median_path_volume.
- `B:\polygon_derived\ledger_v2\paths\year=*\*.parquet` -- FF-1 real daily contract paths
  (71,278 rows), used only for a last-real-print EV fallback.
- `B:\polygon_derived\liquidity_map\signal_liquidity.parquet` -- FF-3' trailing 30-calendar-day
  underlying ATM-call volume per signal (`opt_vol_30d_atm`), used to assign the same t1..t5
  liquidity tiers as FF-4 / tp_fill_fidelity_30dte (edges [320, 1191, 3486, 14524],
  `B:\polygon_derived\minute_fidelity\bindings.json`).
- `experiments\tp_fill_fidelity_30dte\out\events_arm30.parquet` -- the matched TP-declared
  event table (3,216 rows, ARM-30 = TP+30%/SL-70%, the production 30-DTE cell) with real-print
  fill/no-print/never-fill classification.
- `experiments\tp_fill_fidelity_30dte\out\declarations_arm30.parquet` -- full engine
  declaration census (kind = tp/sl/hard/both) for the same 4,403 kept signals.

Note on the task's "~3,339 real premium paths" framing: that figure belongs to the OLDER
REST-pulled ledger (`.cache\polygon_real_premium\`), predecessor to and narrower than the
ledger used here. This study uses `ledger_v2` (4,403 kept / 71,278 path-days), the current,
larger, full-universe artifact that `tp_fill_fidelity_30dte` and FF-3'/FF-4 already build on.

## What this is NOT

Not a Monte Carlo / portfolio-cascade re-sweep (no `monte_carlo` import, per instructions).
The 2026-07-14 `experiments/liquidity_cascade/VERDICT.md` "bare floor sweep" ban applies to
the equal-dollar-cascade MC engine specifically (concentration artifact: dropping names from a
fixed-dollar book mechanically re-concentrates capital into fewer, survivor names, which can
manufacture a DD "improvement" with no fill-realism payoff). This study never runs the
cascade or reallocates capital to survivors -- it is a flat per-event filter-and-tabulate
exercise (supply cost, fill-fidelity, and a per-trade EV mean/median), so it cannot produce
that artifact. It also cannot see the artifact's fix (Stage B' penalized-engine A/B with a
random-drop control) either -- read the EV numbers here as descriptive per-trade outcomes
under a filter, not as a portfolio-level DD/compound verdict.

## Scripts

- `build_floor_sweep.py` -- loads inputs, computes tiers, computes a real-print realized-outcome
  metric per kept ledger row, runs the premium/volume/joint floor sweep, writes CSVs to `out/`.

## Outputs (`out/`)

- `data_quality_checks.csv`, `cost_audit_crosscheck.csv`
- `premium_axis.csv`, `volume_axis.csv`, `joint_grid.csv`
- `excluded_mass_attribution.csv`

See the orchestrator-facing report (delivered in-conversation) for the compact markdown
tables and methodology caveats -- not duplicated here to avoid drift between the two.
