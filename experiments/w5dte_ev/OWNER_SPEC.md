# OWNER_SPEC -- w5dte_ev (EV study for the W5DTE rule family)

Locked by owner in chat 2026-08-18 ~01:00 ET, before the scheduled 01:45 autonomous run.
The executing session writes its own PREREG.md in this directory honoring these locks,
then proceeds without asking. Parent campaign: experiments/weekly_5dte_movers/
(FINDINGS.md) + alpha_mining/NEW_LEADS.md entry "W5DTE".

## Owner-locked decisions

1. **Exit convention: TP ladder + expiry baseline.** Price every rule-hit under
   sell-limit TPs at {2x, 3x, 5x, 10x} of entry premium -- a TP fills iff a STRICTLY
   LATER daily high in the same week reaches TP x entry_close (TP-only longs: no SL
   race, so daily bars price this without double-touch ambiguity; entry day's own high
   never counts). Plus a hold-to-expiry baseline. Costs per the asymmetric canon:
   entry at close and TP limit fills are free; expiry settlement is a FORCED exit and
   pays the half-spread (source the half-spread estimate from FF-2 measured Roll
   spreads by liquidity tier -- cite FF2_RESULTS.md; prereg pins the number). A hit
   with no later print settles worthless (0), matching the parent study's convention.
2. **Control: exposure-matched random.** Same-count random draws from the same
   analysis population, matched at minimum on (expiry_week x side x entry_dow); >=100
   seeded draws for the control distribution. The rule family's EV must beat the
   control distribution decisively (report percentile), not just be positive.
   (Mandatory per the liquidity-floor lesson + option-surface-features preconditions.)
3. **Forward paper tape: YES, CONDITIONAL.** (Owner corrected an earlier misclick in
   the same chat: the initial "stop at FINDINGS" selection was wrong.) IF the EV read
   survives its exposure-matched control, stand up a ct15-style forward paper tape for
   the W5DTE rule family: a scheduled Mon/Tue pass that logs live rule-hits (entry
   print + rule conjuncts) and later fills in their week outcomes; zero production
   impact; live post-cutoff data is EVALUATION context (sanctioned -- the ct15
   precedent; use HOLDOUT_DISABLE=1 only in that evaluation path, never in
   calibration). Follow the ct15-paper-sleeve pattern for scheduling + state
   (.horizon task + scheduled task + heartbeat-visible dedup key). If the EV read
   FAILS the control, do not build the tape -- verdict + docs only.
4. **Push + GitNexus refresh SANCTIONED**: after the EV work, push main to origin
   (checkpoint 9cac07a6 is already committed; commit the EV artifacts narrowly first),
   then run `python scripts/gitnexus_refresh.py` (the guarded script -- NEVER bare
   `npx gitnexus analyze`).

## Standing constraints (inherit, do not re-derive)

- Data: entries/outcomes <= 2026-06-12 expiries ONLY (holdout intact; `experiments/
  _holdout` asserts). Substrate: the existing analysis parquets at
  `B:\polygon_derived\weekly_5dte_movers\features\analysis_*.parquet` -- no rescan.
- Rule-hit definition: the 6 HOLD rules in weekly_5dte_movers/RESULTS_TABLES.md
  Section E3 (evaluate the family union AND the cp==C variants separately).
- No production changes of any kind (scoring/portfolio/capital-plan untouched).
- Compute >1 min goes through `trader queue submit`; queued python uses the explicit
  `C:\Users\Yaqzan\AppData\Local\Programs\Python\Python311\python.exe` path (the
  py-launcher trap, traps.md). ASCII stdout; polars NaN-vs-null discipline.
- Token economy: orchestrator preregs + audits; a Sonnet builder implements from the
  brief; per-year + per-rule robustness reported with N.
- Closeout: FINDINGS.md here; merge verdict into NEW_LEADS "W5DTE" (re-rank or move to
  null-traps per outcome); update .horizon/INDEX.md row `w5dte-ev`; update auto-memory
  (project_weekly_5dte_movers.md gains the EV verdict); narrow commit + push; delete
  the one-shot cron that launched the run if it persists.

## Post-closeout continuation (owner directive 2026-08-18 00:55 ET -- "proceed autonomously")

The owner expects the EV study to finish with hours to spare and directed: continue
autonomously with the NEXT reasonable compute exploration(s) based on the findings,
through the night. Standing rules: each continuation gets its own `experiments/<name>/`
prereg + .horizon/INDEX.md row BEFORE compute; queue everything heavy; holdout + Dec
embargo untouched; NO production/scoring/portfolio changes; commit+push at each coherent
checkpoint; near morning prefer finishing/checkpointing over starting anything that
cannot cold-boot from disk. The FINAL act is one morning-readable summary covering the
whole night.

Priority guidance (the 01:45 session re-judges with the EV verdict in hand):

1. **Minute-level realizability (default next, EITHER verdict).** We own
   `B:\polygon_flatfiles\us_options_opra\minute_aggs_v1\` (1,007 sessions, unexploited
   here). For a stratified sample of rule-hit weeks (~2-5k, sized to the night):
   time-at-or-above each TP level (minutes / prints / volume traded at-or-above),
   lone-print-spike vs traded-level classification of the daily high, and the implied
   fill haircut vs the daily-bar TP assumption. This attacks the touch != capture gap
   directly with data already on disk. FF-4 validated daily-bar TP/SL conventions at
   minute resolution for 30-DTE cascade slices (FF4_RESULTS.md) -- cite and contrast;
   5-DTE deep-OTM lottery contracts are exactly where that validation might NOT
   transfer.
2. **If EV survived the control:** capacity/clip absorption at the TP prints (contract
   volume at/above the TP level on touch days) -- prices what a real clip could have
   captured; feeds the paper tape's eventual practical read.
3. **If EV failed the control:** decompose WHY touch-lift did not convert (loss-given-
   miss distribution; which grid slices the control catches up in), then the cheap
   E3-widening pass (GBT-derived rule atoms, scoped out of Stage C -- machinery accepts
   them unchanged) to check whether a better rule family exists before shelving.
