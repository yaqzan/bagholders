# PREREG — MC-realism default flip (calibrated fills become the shop default)

STATUS: LOCKED 2026-08-11 before any new outcome exists (git commit = lock).
Owner: "Yes proceed" (2026-08-11). Closes the program escalation opened by
`tpsl_refine_2026_08` ("no G-gate should read MC absolute compound/DD at face value
until the MC-realism default flip lands").

## 1. What ships (Phase R4, gated on R2)

`monte_carlo.py` defaults: `TP_FILL_MISS_P` 0.0 → **0.15**, `TP_FILL_GAP_AWARE` off →
**ON**. Canon becomes the labeled optimistic variant (env MISS_P=0, GAP_AWARE=0), used as
a robustness arm. Doctrine: all MC absolutes quoted anywhere default to CALIBRATED unless
labeled canon. Docs re-anchored (monte-carlo.md, assessment-backtest.md Stage-3 notes,
known-issues escalation closure + ship-state entry, CLAUDE.md labeling sentence,
capital-plan refresh run). NO strategy parameter changes in R4; NO Stage-1/2 changes
(WR15/barrier-cache/D1 double-touch are separate measurement layers, out of scope).
Paper-ledger engine prices independently of MC env — unaffected.

## 2. As-seen evidence (declared)

Calibrated 22-now/5y N=500 for: Core shipped (−43.7/−28.7, DD 57.2/57.2), Apex v6
(−59.6/−59.3, DD 70.2/70.1), tpsl/alloc alternates, floor program (A2 killed by
exposure-matched control; random supply cuts improve calibrated DD ~+5pp). Canon 12-window
certs for Core (5y DD 40.8) and Apex v6 paired-2x. NOT seen: any 12-window calibrated
battery; any Sentinel calibrated read; any gross<0.50 cell under calibration.

## 3. Phase R1 — re-baseline battery (compute)

Profiles {Core shipped, Apex v6, Sentinel} × the canonical 12 windows
(`PHASE_D_WINDOWS_12`) × lenses {calibrated, canon} × N=500 paired, one subprocess per
(profile, window, lens). Profile→env mapping cribbed from the closed campaigns
(allocC_apex2x env block for Apex v6; portfolio_profiles.json for Sentinel — builder must
log the engaged config per cell: tier fracs, mp, gross, min-score, TP/SL; any ambiguous
mapping = STOP and report, never guess). Purpose: the official post-flip baseline table +
Sentinel's first calibrated read (clears the capital_plan_refresh NO-CALIBRATED-READ flag).
Identity checks: Core calibrated 22-now/5y rows must reproduce `phaseD_core_calib.csv`
bit-exactly; Apex v6 calibrated 22-now/5y must reproduce `allocC_calibrated_apex.csv`.

## 4. Phase R2 — ranking-stability audit (gate for the flip)

From R1 + existing CSVs, verify the TP10-era portfolio decisions hold under calibrated
12-window reading: (a) Core TP10/SL−100 beats TP30/SL−70 base; (b) beats TP20 alternates;
(c) Apex TP10/SL−60 beats its alternates (collapse ordering); (d) Apex n12×6% beats
n10×10%. PASS = no ordering inversion on DD-primary reading (mean WorstDD + collapse; a
median-compound-only wobble with DD ordering intact is recorded, not blocking).
**Any inversion → R4 does NOT execute**; escalate to owner with the inverted pair — that
is a worldview crisis, not a doc edit.

## 5. Phase R3 — Core exposure re-read under calibrated defaults (compute + lanes)

Motivated by floor_control: blind supply cuts improved calibrated DD ~+5pp — the honest
lever is the explicit exposure knob. Cells: Core gross cap ∈ {0.30, 0.40, 0.50=baseline}
at mp14, plus one interaction probe {gross 0.40, mp10} → 4 configs × 12 windows ×
calibrated × N=500. This is an EXPOSURE claim, not selection — no exposure-matched
control applies (the knob IS exposure).

Ship lanes for a candidate gross (LOCKED; vs gross-0.50 baseline, calibrated):
- WorstDD: better in ≥8/12 windows AND mean improvement ≥ 2.0pp.
- Compound: relative median give-back >10% in ≤3 windows.
- collapse 0 everywhere; 2020_crash WorstDD not worse than baseline.
- Survivor arm on the candidate: DD edge does not evaporate.
- Canon robustness arm on the candidate: canon mean WorstDD not worse by >2.0pp
  (don't ship something canon-catastrophic while the worldview arbiters are pending).
If no candidate passes → gross 0.50 CONFIRMED; R3 closes null. Any pass → ships as a
SEPARATE portfolio-stage change (ship-portfolio flow) AFTER R4 lands, certified under the
new default lens. Apex exposure: out of scope (retuned 2026-08-10; one retune per knob
per evidence cycle).

## AMENDMENT-2 (2026-08-11, pre-R1/R3-outcome, selection-neutral)

The §3 identity gate fired: fresh 22-now/5y cells differ from the 2026-08-10 reference
CSVs by +4/+2 `n_call_signals` and <0.5pp on all metrics. Diagnosis: TODAY'S 16:30 close
update scored 2026-08-11 (+4 new 75+ signals → fixed-start 22-now +4; rolling-start 5y
+4 new − 2 aged-out = +2) — the documented trap "Archived MC artifacts are NOT
bit-reproducible — the price substrate drifts" (traps.md 2026-07-10). The original gate
spec (bit-exact vs prior-day CSVs) violated that trap; the driver mechanism is separately
corroborated bit-exact against a same-day reference (floor_mc A0/dip). Fresh identity-cell
values differ from already-public references by <0.5pp — no decision-relevant novelty was
seen; R1 12-window tables and R3 remain unseen. Amended, tightening the right thing:

1. **Identity gate redefined as SAME-RUN determinism:** the R1 battery includes a
   duplicate Core-calibrated 22-now cell (two independent subprocesses, identical config)
   — they must match each other bit-exactly. Cross-day CSV comparison demoted to a
   DRIFT-PLAUSIBILITY check: fresh values within 1.0pp (med and DD) of the archived
   references AND the signal-count delta must reconcile exactly with close-update
   arithmetic (verified, not assumed: count of loaded signals dated 2026-08-11 must equal
   the fixed-start delta; rolling-window aging must account for the remainder).
2. **No battery may span a close-update boundary:** the runner logs max(score date) at
   battery start and end; a change flags all cells run after it as tainted (rerun them).
3. **R2 cross-substrate note:** ordering comparisons vs Monday-substrate CSVs are valid
   only where gaps exceed 2× the observed drift (<0.5pp) — all R2 gate items have 4-30pp
   gaps; the Core −1.00/−0.90 adjacency (4.6pp) is not an R2 gate item.

## AMENDMENT-3 (2026-08-11, pre-R1/R3-outcome — substrate investigation RESOLVED; proceed)

The +4/+2 identity-gate drift is fully explained at row level (8-round investigation;
evidence in `out/round8_*.csv` + `logs/`): the 2026-07-29 Sharadar repair added MNST with
a same-day score backfill; daily updates never filled its Jul-30→Aug-10 gap; the
2026-08-11 ~10:02 update triggered a FULL re-backfill with REPLACE semantics (delete+
re-insert, resetting every `updated_at` — which is why earlier timestamp forensics
misread "no prior rows"); re-scored on a 13-days-drifted adjusted-price series, 22
borderline MNST dates crossed the ≥70 threshold (10 out, 12 in) → net +4 (22-now) / +2
(5y), ledger closed EXACTLY against the Aug-2 weekly dump, zero non-MNST touches, control
slice clean. Classification: benign-in-intent pipeline behavior; two invariants banked to
traps.md (score history is mutable under re-backfill; replace-semantics resets blind
insert-only-timestamp forensics). Consequences, all tightening:
1. R1/R3 PROCEED on the current (post-re-backfill) substrate — it is the operative one.
2. Substrate-fingerprint guard (per-window loaded ≥70 count at battery start/end) is
   REQUIRED equipment for this and all future multi-cell batteries.
3. The AMENDMENT-2 identity gate stands: twin-cell bit-exactness (same run) + 1.0pp
   drift-plausibility vs the Aug-10 CSVs (current deltas <0.5pp — inside band).
4. Discovered alongside, recorded here for the campaign record: the daily-backup outage
   (Aug 8-11, task lost WorkingDirectory; launcher now anchors cwd — `hidden_run.vbs`)
   and the fact that daily backups exclude `scores` by design (weekly-only coverage).

## 6. Stop rule

Phases and cells as enumerated; no new configs/windows/lanes after any R1/R3 outcome is
seen. Amendments only pre-outcome or tightening-only, dated, committed. Campaign closes
with FINDINGS.md; R4 executes only on R2 PASS.

## 7. Compute

R1: 72 (profile,window,lens) cells; R3: 48 + guards ≤ 24 → ~145 cells × ~16-25s ≈
40-60 min queue time (high, --db light, restartable, chunked; the 16:30 close pipeline is
exclusive — cells interleave around it). Runner: new driver in THIS dir cribbing
floorMC_run/calibP_run patterns read-only.
