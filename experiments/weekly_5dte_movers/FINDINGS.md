# FINDINGS -- weekly_5dte_movers (owner-directed census + metric ablation)

Date: 2026-08-18. Prereg: PREREG.md (locked 2026-08-17, amendments a/b/c pre-data).
Data: Polygon OPRA day-aggs via FF-0 contract_day_index; 202 expiry weeks, entries
Mon/Tue, expiries 2022-08-05 .. 2026-06-12 (holdout intact -- nothing past 2026-06-15
was read). Outcome = max subsequent daily HIGH through expiry / entry close
("theoretical max", owner's definition). Full tables: RESULTS_TABLES.md +
B:\polygon_derived\weekly_5dte_movers\out\tables\. Pipeline: build_ledger.py (10,310,319
rows) -> build_features.py (171 cols) -> analyze.py (audit-passed; STAGE_C_REPORT.md).

Analysis population: 1,341,534 covered tradeable entries with outcomes (C 735,743 /
P 605,791). Winner = growth_mult >= 5.0 (8.58%; 115,068 events); sensitivity 3x (19.6%)
and 10x (2.51%) ran everywhere. 123 metrics tested (multiple-comparisons denominator).

## Headline verdicts

1. **Explosive weekly movers have a stable, strongly discriminable fingerprint, and it
   is CONTRACT GEOMETRY + ENTRY-DAY TAPE VIOLENCE -- not underlying trend state.**
   The top univariate discriminators by |z| (calls): otm/moneyness z=86.9,
   premium_over_spot 84.9, contract-day H-L range 81.1, entry premium 73.2,
   close-vs-open 65.4, dollar volume 51.7. Everything else is a tier below.

2. **The distilled rule family HOLDS on every prereg robustness slice.** All 6 selected
   rules stamp HOLD: same direction 5/5 years, at 3x AND 10x thresholds, and ex-earnings.
   The family is one pattern with variations:
   **OTM >= ~5.8% (moneyness >= ~+4%) AND entry-day contract H-L range >= ~127% of its
   close AND (technology sector | call side) AND non-OPEX week -> P(>=5x touch) 19.4-20.7%
   vs 8.58% base = lift 2.26-2.42x** (N per rule 25,663-44,342). In words: the cheap,
   out-of-the-money weekly that is ALREADY convulsing intraday on Monday/Tuesday, on a
   tech name, in a non-monthly-expiry week, is the contract that goes on to touch 5x+.
   It is NOT an earnings proxy -- the lift survives with earnings weeks excluded.

3. **The wide SMA/EMA ladder (the owner-mandated 104 columns) collapses to ONE factor
   and is predictively REDUNDANT.** Correlation clustering (|rho|>0.8) merged all 52
   pxrel/slope MA columns + short/medium returns + RSI/Stoch/BB%b + score_stoch/
   score_trend + prior-week return + MA-stack into a single "trend/extension"
   super-factor (rep: ema_5_pxrel) -- 110 metrics reduce to 35 real dimensions, and
   ~60 of them are that one. Ablation: deleting the ENTIRE F4 family moves CV AUC by
   +0.007 (C -- slightly BETTER without it) / -0.001 (P). Univariately significant
   (z 18-29), uniquely informative: no. This extends the 30-DTE trend-MA-lattice null
   (2026-07-14) to the 5-DTE explosive-touch label.

4. **Market-context features are the trap family: strong in-sample, NEGATIVE transfer.**
   F7 (VIX/regime/breadth/SPY) posts z up to 33 in-sample (especially puts), yet
   REMOVING the family IMPROVES year-blocked CV: calls +0.033 AUC and top-decile lift
   1.62 -> 2.05; puts +0.010. Regime conditioning on this label fits the year, not the
   phenomenon. Any future 5-DTE model should treat regime/breadth inputs as
   overfit-suspect by default.

5. **Contract geometry (F1) is the only family with large unique signal.** Leave-F1-out:
   AUC -0.056 (C) / -0.043 (P) -- an order of magnitude beyond any other family's
   unique contribution. F2/F3/F5/F6/F8 each move AUC by <= 0.007 (mutually redundant),
   and no SINGLE metric is irreplaceable (E2 max |dAUC| 0.006: otm/moneyness/premium/
   entry_close substitute for one another).

6. **v74 scores are nearly orthogonal to weekly explosiveness.** overall/pre_regime/
   pre_boost cluster at z ~12-13.5 (best components: score_rsi 23.8, score_stoch 23.7 on
   calls); leave-F6-out moves AUC by -0.0005 (C) / +0.002 (P). Expected and clean: the
   score hunts 30-DTE direction; 5-DTE convexity is a different animal. No evidence here
   licenses using `overall` as a weekly-lottery selector -- and none was expected.

7. **Predictability is real and material.** Year-blocked CV AUC: calls 0.727 logistic /
   0.712 GBT, puts 0.709 / 0.704, vs shuffled-label controls 0.504 / 0.486. Top-decile
   model lift ~1.6-1.7x at baseline features, ~2.0x without the F7 family.

8. **Census structure worth keeping** (tradeable view): P(>=5x) is remarkably stable by
   year (7.7 / 8.5 / 8.6 / 8.5 / 9.4% for 2022H2..2026H1) -- no single-regime artifact.
   Monday entries beat Tuesday (9.05% vs 8.09% -- one extra day of runway), calls beat
   puts (8.90% vs 8.19%). Median theoretical-max is ~1.5x for EVERY year: a 3-4 DTE
   option that trades at all has even odds of printing +50% somewhere before expiry --
   gamma, not signal. The unfloored RAW view runs LOWER (6.99% at 5x): penny/illiquid
   contracts frequently never print again (counted as non-winners by prereg), so floors
   RAISE the measured touch rate -- the artifact cuts the opposite way from the naive
   expectation. Puts' explosions are market-state-driven (F7 z's 27-33); calls' are
   idiosyncratic/geometry-driven.

## What this does NOT license (read before citing)

- **Touch != capture.** growth_mult is the week's highest PRINT over the entry close --
  the owner's deliberate theoretical frame. No exit rule realizes the high; deep-OTM
  weekly spreads are wide; and the very entry condition (H-L range >= 127% of close)
  means the "entry at close" print sits inside a violently moving tape. Expected value
  is NOT established: the ~80% of rule-hits that never touch 5x are deep-OTM weeklies
  that mostly expire worthless (loss-given-miss ~ -100%). A 20% chance of touching 5x
  with -100% otherwise is NOT obviously positive EV even before spreads -- and the
  sim-vs-real flip law (traps.md, ~60% sign flips) says treat any modeled EV with
  suspicion until real premium paths are walked.
- **In-sample mining.** 123 metrics tested; rule atoms = top-6 by |z| + side (255
  candidate rules, 210 cleared N>=1000, 6 selected); atom thresholds are full-window
  P80s; cluster representatives chosen on full-window z. The 5/5-year HOLD grid is a
  robustness check inside the window, not out-of-sample proof. True OOS = post-cutoff
  data, untouched, available for a future read (>= 2026-12-15 per program calendar).
- **No pre-2022-08 evidence exists** (archive floor). Nothing here speaks to crash
  regimes (2020-03, 2018-02).
- Snapshot mcap was joined flagged (`mcap_is_snapshot=1`) and no selected rule uses it;
  sector is the only identity conjunct (12.6% null, own level in tables).

## Disposition

- Lead filed: alpha_mining/NEW_LEADS.md "W5DTE convulsing-OTM weekly family" -- next
  step is an EV study on real premium paths (terminal + path P&L of rule-hits vs
  exposure-matched random controls, per the option-surface-features preconditions), not
  a scoring change. Program-board task option-surface-features (opens post-December)
  inherits this as a prior.
- The F7 anti-generalization result and the MA-ladder redundancy are recorded here as
  reusable negative knowledge for any future short-DTE modeling.
- No scoring, portfolio, or capital-plan change proposed. ALGORITHM_VERSION untouched.
- Artifacts: ledger + features + out/tables on B:; harness re-runs end-to-end via the
  three scripts; .horizon/weekly-5dte-movers/ closed with pointers.
