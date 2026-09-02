# WCF Score-Gate Ramp — smoothing the 27/28 intraday cliff

**Date:** 2026-06-11 · **Branch:** `algo-exp/wcf-score-ramp` · **Class:** Stage 1
(modifies `Score.overall` → version bump required if shipped) · **Objective:**
intraday score stability (per-trade NEUTRAL by construction — no alpha claim)

## Problem

The v27 WCF put-floor lift (`database/utils/scoring.py`) fires binary on the
post-regime integer: `overall < 28 ∧ w_adj > −17 → lift toward 50` (full lift at
27 ≈ +21.85 pts). A 1-point wobble across 27/28 toggles the entire lift:

- `trader intraday-drill GIS 2026-06-10`: final score toggled 49↔28 three times
  in one session; components nearly flat; `wcf_lift: 21.85->None` is the toggled
  key. Price moved 1.48% — flagged FAKEOUT.
- `trader intraday-swings 2 --min-swing 10` (2026-06-09→11): a cluster of ≥15
  symbols with the identical lo=28 / hi=45-49 signature (CBRE, GEHC, GIS, TEM,
  CRM, GDDY, CALX, STLA, BLDR, DJT, BCC, FUBO, TIGR, FOUR, FRPT…), most FAKEOUT.

**Mechanism detail (confirmed from the GIS drill):** the gate evaluates the
**post-regime** integer — regime is applied (and int-rounded) *before* WCF — so
both sub-point component wobble *and* regime-multiplier reapplication
(1.02↔1.04) can flip the boundary. At 09:03 `preR=27 × 1.03 → 28` (no fire); at
11:16 `preR=27 × 1.02 → 27` (fire → 49).

This is the hard-threshold-gate instability class from process.md "gradient
laws over threshold gates". Note: v27's calibration found linear-clip beat tanh
**on the wadj axis** — the SCORE-gate axis was never smoothed; this candidate
touches only that axis and leaves the wadj axis exactly as shipped.

## Candidate

Score-axis linear ramp on the existing lift (one new constant,
`WCF_LIFT_RAMP_TOP`):

```
span  = RAMP_TOP − (GATE − 1)                  # GATE=28 unchanged
sgrad = clip((RAMP_TOP − overall) / span, 0, 1)
lift  = K · weakness(w_adj) · sgrad · (TARGET − overall)
```

- Full lift unchanged at `overall ≤ 27` (today's firing set is **bit-identical**).
- Lift fades linearly to zero at `RAMP_TOP` instead of cutting off at 28.
- `RAMP_TOP = 28` reproduces the legacy binary gate **bit-exactly** (span=1).
- Hysteresis was considered and rejected by design: scoring is stateless per
  (symbol, date); a state-dependent gate would break bit-reproducibility and
  recalc determinism.

Final-score map (full weakness): legacy `27→49, 28→28` (21-pt cliff);
top31 `27→49, 28→44, 29→39, 30→35, 31→31` (max step ~5); top33
`27→49, 28→45, 29→42, 30→40, 31→37, 32→35, 33→33` (max step ~3.4).

**Per-trade neutral by construction:** every new partial-lift output lands in
[29, 49] — above every put gate (cascade ≤25, ICH ≤27, PESS [16,20], EARN_BOOST
puts ≤25) and below every call gate (≥69/70/75). Every stage downstream of WCF
(CWCF ≥75, CWWD 70-74, CSWC ≥75, boosts, SCW ≥70, continuation ≥50) is a no-op
in this zone. PESS runs *after* the `pre_boost` capture, so its target=28
output never feeds back through WCF.

## Validation (all read-only; v71 active, DB version id 71)

### B2 — reconstruction self-validation: **11,492 / 11,492 bit-exact**

All v71 rows of the last 1y with `wcf_lift` recorded reproduce their stored
final from `(pre-WCF, w_adj)` through the production formula with **zero
mismatches** — the stage-order/reconstruction model used below is exactly
production.

### B — stored-row cohort (5y, overall ∈ [28,30]: 55,235 rows; 50,882 ramp-eligible)

| variant | changed rows | new range | leave `<30` bucket | tradable-bucket violations |
|---|---:|---|---:|---:|
| top30 | 31,534 | [29,42] | 31,236 | **0** |
| top31 | 50,031 | [29,44] | 31,365 | **0** |
| top33 | 50,455¹ | [29,45] | 31,437 | **0** |

¹ top33 also adds tiny lifts on pre-WCF 31-32 (not in this pull; bounded ≤37 by
the formula; covered empirically by Part C zone replay and the sim A/B).

All changed rows are neutral-zone only. Cascade put tiers (≤25), historic peaks
(≤25), and every call tier are **byte-identical**. The only compositional change
is the **`<30` cumulative assessment bucket** (diagnostic, non-tradable): it
turns out ~74% of that bucket today is weak-weekly 28/29 stragglers that escaped
WCF by sitting just above the gate.

### D — migrating cohort quality (generic put barrier, w=15, DuckDB mirror)

| group | N | WR15 |
|---|---:|---:|
| migrators (leave `<30`) | 31,306 | 64.5% |
| remainder (stay) | 11,158 | 65.6% |
| bucket before → after | 42,464 | 64.8% → 65.6% (**+0.8pp**) |

Consistent with the v27 thesis (weak-weekly puts are the worse cohort — mildly
so at the shallow 28/29 end). No bucket regresses; W4 noise-aware
non-regression passes trivially everywhere else (byte-identity).

### C — intraday stability replay (`score_intraday_logs`, 2026-05-27→06-11; 18,110 snapshots, 1,542 groups, 200 WCF-affected)

| metric | production | top30 | top31 | **top33** |
|---|---:|---:|---:|---:|
| affected groups swing ≥10 | 43 | 41 | 33 | **17 (−60%)** |
| affected mean swing | 6.21 | 5.72 | 5.42 | **4.46** |
| all groups swing ≥15 | 44 | 28 | 25 | **20 (−55%)** |
| all groups swing ≥20 | 18 | 8 | 8 | **7 (−61%)** |

Residual affected swing ≈ the underlying pre-WCF component wobble (±2-4 pts)
times the ramp slope — irreducible from the WCF side. Wider ramps flatten the
slope: the dominant intraday wobble is 2-3 pts, so top31 (slope ~5/pt over a
4-pt span) still amplifies a 27↔30 wobble to ~14 pts, while top33 (slope ~3.4)
keeps it at ~9. **top33 recommended** — same tradable-bucket safety, strictly
better on the objective metric.

### E — full-faithful ScoreSimulator A/B (1y, full universe, queue #130)

894 stocks, 194,526 (sym,date) pairs scored through the complete production
path (all push bands / dampeners / boosts / regime — no approximations), three
arms on one loaded context (`RAMP_TOP` 28 / 31 / 33):

| arm | diff rows | diff % | structural violations |
|---|---:|---:|---:|
| top31 vs legacy | 7,794 | 4.01% | **0** |
| top33 vs legacy | 13,305 | 6.84% | **0** |

Every diff has base ∈ [28, RAMP_TOP−1] and new ∈ (base, 45] — the ramp touches
ONLY the predicted neutral-zone cohort. The diff-pair histogram matches the
analytic final-score map exactly (28→45, 29→42, 30→40, 31→37, 32→35 at full
weakness; lower targets = partial wadj weakness). VERDICT: CLEAN.

**⚠ Reproducibility gotcha (cost one wasted run, queue #129):** this box's
global PYTHONPATH includes the MAIN checkout, so a worktree script run as
`python experiments/.../x.py` silently imports MAIN's `simulator`/`scoring`
(no candidate code) — the first A/B returned 0 diffs in both arms (a false
"no effect"). Fixed by pinning `sys.path[0]` to the worktree root + asserting
`module.__file__` origins (now hard-fails instead). Recorded in auto-memory
(`feedback_worktree_pythonpath_trap.md`); applies to EVERY worktree experiment
harness not launched via `python trader.py`.

(Side observation: `ScoreSimulator.compare()` vs DB is broken on main —
`AttributeError: 'Row' object has no attribute 'symbol_id'` — pre-existing,
unrelated; B2's 11,492/11,492 bit-exact reconstruction is the parity proof.)

## Gate verdict (W1-W6 framing)

- **W1 (cohort z):** N/A — no alpha claim; the targeted cohort is defined by
  *instability*, not WR. Stability metric is the objective (precedent: the v69
  weekly-transition ship was gated on dow-flatness + stability + non-regression,
  not the growth gate).
- **W2/W3:** trivially pass — affected tradable cohort is empty.
- **W4 (per-discrete-bucket non-regression):** PASS — every tradable bucket
  byte-identical; the `<30` diagnostic bucket *improves* +0.8pp.
- **W5 (growth verdict):** supply/quality on every binding tier unchanged →
  scoring-neutral tie → formal **FLAG** by convention (the gate cannot see
  stability value). Value case is Part C.
- **W6 (gradient preservation):** PASS — tradable gradients untouched.

## Recommendation

1. **Candidate is ship-safe but per-trade-valueless on its own** — recommend
   carrying `WCF_LIFT_RAMP_TOP=33` as a **rider on the next Stage-1 scoring
   ship** rather than a standalone v72 (a standalone ship costs version bump +
   5y/10y recalc + assess + temporal for zero per-trade delta). If the FAKEOUT
   noise in the intraday diagnostics is operationally annoying enough, a
   standalone ship is clean: all gates above hold.
2. **Display-layer alternative (do regardless, no version bump):** the swing
   attribution in `intraday_diagnostics.py` decomposes stages in the WRONG
   ORDER (assumes pre_boost→boost→pre_regime→regime; actual pipeline is
   pre_regime→regime→dampeners→pre_boost→boosts). The GIS WCF toggle is
   mislabeled "Earnings / continuation boost" (bogus +22/−22 split) even though
   `changed_keys` correctly names `wcf_lift`. Fixing the stage algebra + adding
   a recognized `wcf_boundary` fakeout family makes the dashboard honest about
   this class TODAY. (Spawned as a separate task chip 2026-06-11.) True display
   hysteresis (showing a score ≠ stored) was rejected as dishonest.
3. The sibling cliff — CWCF call-side gate at `overall ≥ 75` (75→56 vs 74→74,
   an 18-pt inversion **at the tradable boundary**) — is out of scope here but
   is the same instability class with *portfolio* consequences (it crosses the
   cascade threshold). Worth its own investigation if intraday logs show that
   family firing.

## Reproduction

```
# in worktree C:\Development\Trader-exp-wcf-ramp
python experiments/wcf_score_ramp/analyze.py    # parts B/B2/C/D (read-only)
python experiments/wcf_score_ramp/sim_diff.py   # part E (read-only, ~queue it)
# legacy bit-exactness: WCF_LIFT_RAMP_TOP=28 reproduces production
```

Artifacts: `analysis_results.json`, `sim_diff_results.json`. Holdout note:
`CALIBRATION_CUTOFF_DATE` is disabled (None) since 2026-06-05; no parameters
here were *fitted* to outcome data (ramp top chosen on the stability metric;
WR was checked for non-regression only).
