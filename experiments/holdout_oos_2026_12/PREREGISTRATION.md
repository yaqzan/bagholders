# December 2026 OOS Evaluation — PRE-REGISTRATION

**Status: COMMITTED + ACTIVE — FABLE rulings OQ-1..OQ-7 applied 2026-07-13 (section 8).
2026-07-17: §11 (Parked-Lead Pre-Registration Pack, PL-1..PL-6) + §12 (December Executor Guide)
appended by FABLE — additive only, zero frozen-number changes; see the Amendment Log provenance
entry.**
**Authored:** 2026-07-13, by the DECEMBER-PREREGISTRATION drafter (Block C) per `gameplan.md` row P1.6 +
section 7 "December protocol". **Eval date: 2026-12-15.** New files only under
`experiments/holdout_oos_2026_12/`; this document, `references.json`, `run_oos_eval.py`,
`run_h3_envelope.py`, and `install_reminder.ps1` are a single package — read together.

> **ADDENDUM 2026-08-16 (additive; zero frozen-number changes): [`SCORECARD.md`](SCORECARD.md)
> in this directory is the OWNER-RATIFIED grading rubric for the December read** — it
> layers ON TOP of this prereg (power tiers, verdict-language rules, decision mapping,
> freeze 2026-11-15) and adds items registered since July: the MC-realism default flip
> context, the locked 24-candidate residual-mining OOS row, the two waiting cluster
> preregs (`experiments/regime_ct_tilt_2026_08/`, `experiments/weak_regime_avoid_2026_08/`
> — their G-DEC gates read from THIS eval), and the CT-mechanism power gate (n<40 →
> belief-update only; powered checkpoint 2027-06-15). **The December executor reads
> SCORECARD.md FIRST, then this document.** Program board: `.horizon/INDEX.md`.

> **Why this exists (verbatim from gameplan.md P1.6):** "The December read is the most
> informative future event for the honest stack; unregistered reads invite post-hoc
> rationalization — the exact failure the holdout lock exists to prevent." Every number in
> section 2 below was pulled **read-only**, before any OOS row was evaluated, precisely so the
> Dec-15 read has nothing to rationalize against.

---

## 0. How to use this package

1. **Do not touch this file's frozen numbers** (section 2, mirrored machine-readably in
   `references.json`) between now and 2026-12-15 except through the Amendment Protocol
   (section 6).
2. **December pre-steps (run BEFORE the evaluation; also listed machine-readably in
   `references.json` → `december_presteps`):**
   1. Rebuild `.cache/experiment_data/skill_v{73,74}_allscores_2016.parquet` through the eval
      date (H1/H2/H5 return `INSUFFICIENT_N` otherwise — proven by this package's own selftest;
      see the known-gap note below; a parquet-cache build goes via `trader queue submit`).
   2. Run the marking-verification pre-step (RULING OQ-3a):
      `python experiments/portfolio_engine_parity/validate.py` — positions/closes/pnl bit-exact,
      the known ~$295 MTM-curve divergence confirmed still display-only — then pass
      `--marking-verified` to the evaluator. H3 refuses to adjudicate any breach without it.
   3. Extend the H3 envelope: re-run `run_h3_envelope.py` with **only** `--win-end` extended
      (same frozen recipe; a pre-registered non-amendment), as its own queue job under normal
      queue discipline (RULING OQ-3b).
   4. If a live-profile change occurred since 2026-07-13: verify the piecewise H3 segments were
      frozen at each switch time (RULING OQ-7) and implement the per-segment evaluation before
      adjudicating H3.
3. On or after 2026-12-15: run `python experiments/holdout_oos_2026_12/run_oos_eval.py
   [--marking-verified]`. It loads `references.json`, computes H1–H6 against live data, and
   prints a per-hypothesis verdict table.
4. Anytime before 2026-12-15: `run_oos_eval.py --selftest` may be run freely — it verifies every
   reference loads and every data source is reachable, **without evaluating any post-cutoff
   outcome** (see section 5).
5. A **BLOCK on H1** is the only hard-escalation trigger defined here (section 4, H1). Everything
   else FLAGs, extends to 2027-06-15, opens a documented review, or (H3 DD breach,
   marking-verified) triggers a mandatory investigation — see each hypothesis's consequence and
   the noise-aware thresholds in section 5.

**Known gap, found by running `--selftest` on 2026-07-13 (action required before Dec-15):**
H1/H2/H5 read `.cache/experiment_data/skill_v{73,74}_allscores_2016.parquet` via
`experiments/skill_vs_baseline/skill.py`'s cache. Those parquets were built once and never
invalidated — `--selftest` confirms both are reachable but report **`OOS n_window=0`** (zero rows
past the 2026-06-15 cutoff) as of authoring. `run_scorecard(oos=True)` will keep returning
`INSUFFICIENT_N` until someone deletes/rebuilds these caches (re-running `skill.py`'s builder —
`chunked_query_by_year` over `START_YEAR..END_YEAR=2016..2027`, so a fresh build naturally picks up
every OOS row scored so far). This is a `.cache/`-only, non-scoring, read-then-write operation —
still a "parquet-cache build" per CLAUDE.md's Long-Running Compute rules, so it must go through
`trader queue submit`, never run raw. **Not attempted by this drafter** (out of this package's
BUILD scope, and a fresh full-history rebuild is exactly the class of compute this session's
one-MC-job budget was not meant to cover) — flagged as a chip (see this drafter's final report) and
should be refreshed periodically between now and 2026-12-15, not left as a Dec-15 surprise.

**Gap RESOLVED (2026-07-13, follow-up session, operational — no frozen number touched):**
`experiments/skill_vs_baseline/rebuild_skill_caches.py` is now the canonical refresher — it reuses
`skill.py`'s / `skill_returns.py`'s own builders (module-VID patched per target, so no duplicate
SQL to drift) with `materialize_polars(force=True)`, targets `{73, 74} ∪ {active}`, and exits
non-zero if a rebuilt score cache still has zero post-cutoff rows. It also rebuilds
`price_feats_raw_2015.parquet` — found equally stale (built once 2026-06-13) and load-bearing for
H1's OOS momentum baseline: without post-cutoff rows there, `verify_scorecard`'s momentum join is
all-null on the OOS window, `t_mom` is undefined, and the H1 verdict can never be adjudicated as
pre-registered. A **recurring refresh is installed**: scheduled task `TraderSkillOOSCacheRefresh`
(weekly, Saturdays 10:00 local; `install_refresh_task.ps1` + `refresh_skill_caches_task.ps1` in
this directory) submits the rebuild through the task queue (`--priority low --window off_market
--dedup skill-oos-cache-refresh`), so the caches stay ≤ ~1 week stale from here to December.
Pre-step 1 (final rebuild through the eval date) still runs before the Dec-15 read — it just
becomes a small delta instead of a 6-month backfill.

**Verification record (2026-07-13, draft):** `run_oos_eval.py --selftest` passes (all 6 sources
reachable). A full `--force-early` rehearsal run was exercised end-to-end for all six hypotheses
and caught two real bugs before they could reach the actual Dec-15 read, both fixed and
re-verified: (1) H3's DD comparison was direction-blind (flagged a *better-than-modeled* DD the
same as a worse one — fixed to only escalate on DD *above* p95); (2) H4's barrier join used the
wrong result-string vocabulary (`"loss"` instead of `"stop"`) and an un-normalized date-typed dict
key, silently producing `n_oos=0` on every band — fixed and re-verified with real, sane N/WR
numbers flowing. This does not change the finding above that H1/H2/H5 still need the cache
refresh to produce anything beyond `INSUFFICIENT_N`.

**Verification record (2026-07-13, post-rulings):** after applying the FABLE rulings (section 8),
`--selftest` re-passes with the expanded checks (H3: + parity harness on disk + 1 pre-registered
segment; H6: + lever scale functions/map builders importable + frozen lever reference present).
A fresh full `--force-early` rehearsal exercised every amended path: H3 correctly held its
indicated breach as `BREACH_PENDING_MARKING_VERIFICATION` without the attestation flag and
adjudicated it (`NOTE_RETURN_LOW`, DD-primary context) with `--marking-verified`; H6 line (a)
computed all five market-state levers on the OOS window (17 OOS trading days → correctly
`INSUFFICIENT_DAYS` against the 60-day floor; SVR correctly `MEASURED_AT_EVAL_TIME`) alongside
line (b)'s tier_drift (`QUIET`); H5 reports the two-stage protocol in its consequence text; the
date gate still refuses an unflagged full run (exit 2). Surviving rehearsal artifact:
`results/eval_20260713T103232Z.json` (`is_early_rehearsal: true`).

---

## 1. Frozen anchors

| Anchor | Value | Source (verified, not assumed) |
|---|---|---|
| Active scoring version | **v74**, `f9fb7b934`, shipped 2026-06-15 | `known-issues.md` CURRENT SHIP STATE; `AlgorithmVersion` row |
| Holdout cutoff | `CALIBRATION_CUTOFF_DATE = "2026-06-15"` | `strategy_config.py:61` (read live 2026-07-13) |
| OOS eval date | **2026-12-15** (~183 days post-cutoff) | this pre-registration |
| Single-window-miss extension date | **2027-06-15** | this pre-registration (section 5) |
| Live portfolio profile | **`apex`** — "v70 Apex Live", the 15-DTE risk-budget elbow | `GET /api/portfolio/state` queried live 2026-07-13 (`run.profile="apex"`, `last_synced_at=2026-07-13T04:23:49`) — **looked up, not assumed**; both `known-issues.md`'s CURRENT SHIP STATE header ("default profile = Core") and `portfolio_profiles.json`'s own `apex.description` prose ("NOT the default") are **stale** and contradicted by this live read |

### 1a. Live Apex profile — full param snapshot (`algorithm_versions/portfolio_profiles.json` key=`apex`, version=3)

| Param | Value | | Param | Value |
|---|---:|---|---|---:|
| `nominal_cal_dte` | 15 | | `max_positions` / `call_max` | 4 / 4 |
| `hold_cal_days` | 13 | | `put_max` | 0 |
| `sl_base` / `sl_stress` | −0.85 / −0.85 | | `gross_cap` / `call_cap` / `put_cap` | 0.9 / 0.9 / 0.0 |
| `tier_ultra/top/mid/low` | 0.25 each | | `dd_lo` / `dd_hi` / `dd_floor` | 0.35 / 0.55 / 0.40 |
| `tier_overflow` | 0.0 | | `sat_floor` / `sat_power` | 0.55 / 0.5 |
| `capital_ceiling` | 0.0 (uncapped) | | `call_ref` / `put_ref` | 16.0 / 4.0 |

**Lever inheritance (load-bearing, easy to get backwards):** `portfolio_profiles.py:324` builds
every profile from `base = sc.STRATEGY_30DTE`, overriding *only* the fields above via
`dataclasses.replace`. The live Apex ledger therefore runs with the 6 Stage-3 DD levers
(RXDD/MWDD/TVDD/BDIV/SVR/SPREAD_TILT) **inherited `ENABLED=True`** from `STRATEGY_30DTE` — it does
**not** use the separate legacy `STRATEGY_15DTE` dict, where all 6 are `False`/"not_wired". This
distinction directly shapes the H3 envelope recipe (section 4).

**Staged, unapplied change:** `experiments/apex_dte_dd/SHIP_HANDOFF.md` (2026-06-30) shows a
30-DTE config **strictly dominates** this live 15-DTE elbow (median compound +4%→+50%/+108%,
worst DD 88%→82%/76%, collapse 1.3%→0%, P(2x) 57%→72%). It is **staged, not applied**, as of
2026-07-13 (real-money change, gated on user green-light per gameplan P0.3). **If it is applied
before 2026-12-15, the pre-registered PIECEWISE protocol executes (RULING OQ-7, section 4 H3 +
section 6)** — the H3 envelope below stays frozen as segment 1 (truncated at the switch date) and
a new segment freezes its own recipe at switch time; the switch never silently re-targets or
invalidates this envelope, and **pre-registering this removes any eval-cleanliness reason to
delay the P0.3 decision.**

**Live ledger snapshot at authoring** (context, not a recommendation — any live-profile action is
P0.3/P3.1 territory outside this drafter's scope): as of 2026-07-13T04:23:49, equity $21,690.50
on $35,968.64 starting capital, **total return −39.70%, max DD 42.34%**, 86 closed / 4 open
positions, ~40 calendar days elapsed. Directionally consistent with this profile's own documented
held-continuously behavior (−37.1% 5y / 79.6% DD, known-issues.md 2026-06-17) — flagged here purely
as input to H3's noise expectations, not adjudicated by this document.

### 1b. `strategy_config.py` knob snapshot

| Knob | `STRATEGY_30DTE` (Apex's base) | `STRATEGY_15DTE` (legacy router — **not** what Apex uses) |
|---|---|---|
| `CALENDAR_HOLD` | True | False |
| `HOLD_CAL_DAYS` / `NOMINAL_CAL_DTE` | 27 / 30 | 10 / 15 |
| `RXDD_ENABLED` / `MWDD_ENABLED` / `TVDD_ENABLED` | True / True / True | False / False / False |
| `BDIV_ENABLED` / `SVR_ENABLED` / `SPREAD_TILT_ENABLED` | True / True / True | False / False / False |

(`STRATEGY_15DTE` is included only to document a near-miss trap: its `NOMINAL_CAL_DTE=15` makes it
*look* like the live Apex config, but it is a different, mostly-frozen legacy path — the "1/day
router" — with all 6 DD levers off. The H3 envelope must **not** be built from this dict.)

---

## 2. In-sample reference numbers (frozen, provenance-tagged)

Full machine-readable form: `references.json`. Headline numbers:

**0d skill-vs-baseline gate (H1/H2 basis)** — `experiments/skill_vs_baseline/verify_scorecard.py`,
`30dte_apex` predictand, v74, in-sample (`.cache/algorithm_versions/v74/research_pack/verify_scorecard.json`,
read 2026-07-13):

| | N | EV | vs floor |
|---|---:|---:|---|
| Climatology (random call) | 292,372 | **+2.323%** | — (the H2 floor) |
| Score ≥75 | 2,196 | +2.659% | +0.336pp vs clim, **t=+0.356** |
| vs momentum (top ~0.76% by 12-1) | — | +3.848pp diff | **t=+2.844** |
| **0d verdict** | | | **FLAG** (beats momentum, not climatology — the accepted "risk-shaper" profile) |

**WR15 generic barrier (H4 basis)** — `.cache/algorithm_versions/v74/research_pack/utility_5y_wr15.json`,
call side, read 2026-07-13:

| Band | N | WR | optTP | Gates H4? |
|---|---:|---:|---:|---|
| 75-79 | 2,514 | 65.69% | 51.33% | yes |
| 80-84 | 814 | 63.97% | 50.66% | yes |
| 85-89 | 210 | 67.53% | 49.97% | yes |
| 90-94 | 56 | 80.34% | 58.90% | no (N<100, report-only) |
| 95-100 | 11 | 54.50% | 45.50% | no (N<100, report-only) |

**v73 in-sample comparator (H5 basis)** — computed live 2026-07-13 via
`run_scorecard(vid=73, oos=False, write_json=False)` (not written to `.cache/`, captured here only):

| | N | EV | vs momentum t | 0d verdict |
|---|---:|---:|---:|---|
| v73, score≥75 | 3,541 | +3.056% | +2.636 | FLAG |
| v74, score≥75 | 2,196 | +2.659% | +2.844 | FLAG |

v73's raw pooled EV is *numerically higher* in-sample — expected, since v73 still admits the
retired dampener tail's extra (mixed-quality) supply. **This is not evidence the retirement was
wrong** — see H5's two-stage protocol (section 4, RULING OQ-4): a pooled-EV gap alone never
concludes; only the stage-2 DD-primary re-ablation can.

**Tier-drift baseline (H6 basis)** — `experiments/version_scorecard/tier_drift.py`, run live
2026-07-13, lineage v27..v74, metric=`tp`, K=3:

> **One flag: `call 75-79: TREND(3x declining)`** — v72 53.8 → v73 52.2 → v74 51.9 (tp_shrunk%),
> not yet CUM-significant (two-proportion z > −2). No other call or put band flagged.

**Live-ledger MC envelope (H3 basis)** — frozen recipe (section 4 H3), N=500, window
2026-06-01→2026-07-10, task #601, DONE in 30s runtime. `envelope_h3.json`:

| | mean | p05 | p50 | p95 |
|---|---:|---:|---:|---:|
| % return (`finals_pct_return`) | −3.12% | −19.44% | −2.64% | +13.09% |
| % drawdown (`dds_pct_drawdown`) | 45.29% | 44.16% | 45.31% | 46.39% |

**Interim observation — RECORDED AS CONTEXT per RULING OQ-3(c), explicitly NON-ADJUDICATED
(window is 40 days old, one realized price tape, thin by design at authoring time):** the live
ledger's actual return at authoring (−39.70%, section 1) sits **decisively outside** this
envelope's return band (below p05 of −19.44%) — the modeled dispersion at this short window comes
only from gap-fill randomness on one realized tape (the recipe caveat), so a live/model gap this
large is more than boundary noise. The live ledger's **max DD (42.34%) sits just below the
modeled p05 (44.16%)** — i.e. slightly *better* than the model's tightest cluster, not concerning
under the DD-primary framing. Not adjudicated — see H3's engine-fidelity framing + marking
pre-step (section 4) and RULING OQ-7 (section 8) before reading anything into it. The Dec-2026
read re-runs this exact recipe with `WIN_END` extended and a ~6-month-deeper, less
noise-dominated window.

---

## 3. Hypotheses — verbatim source

From `gameplan.md` row P1.6 (verbatim): *"hypotheses H1 0d-gate OOS (BLOCK→freeze ships +
escalate; FLAG=accepted, no auto-revert), H2 apex-EV vs climatology floor, H3 live ledger inside
MC envelope, H4 WR15 within CI, H5 v73-vs-v74 retirement-regret on OOS rows, H6 per-lever drift
(the ONLY sanctioned revisit trigger for lever consolidation). Noise-aware thresholds (t-floors;
single-window miss → extend to 2027-06)."*

From `gameplan.md` section 7: *"December protocol: run
`experiments/holdout_oos_2026_12/run_oos_eval.py` as pre-registered. Amendments are documented,
not improvised. A FLAG is not an emergency; a t-significant BLOCK triggers the pre-registered
escalation (freeze ships, user decision on de-risking)."*

**These fragments are the entire spec.** Section 4 below is the operationalization of each —
grounded in existing, already-proven tooling wherever one exists. Every genuinely ambiguous call
was flagged as an open question in the draft rather than silently decided; FABLE ruled on all
seven on 2026-07-13 (section 8), and section 4 reflects the rulings.

---

## 4. Hypotheses — operationalized

### H1 — 0d-gate OOS

**Method:** `verify_scorecard.run_scorecard(vid=<active version>, oos=True, sample=300000)` —
reads *only* post-cutoff rows. Same dual-baseline t≥2.0 floors as the in-sample gate (section 2).

**Verdict:** SHIP (beats both) / FLAG (beats one) / BLOCK (beats neither) / INSUFFICIENT_N (OOS
matched rows < 50 — defer, not a verdict).

**Consequence:**
- **BLOCK** → freeze ships (no new Stage-1/Stage-3 ship proceeds to production) + escalate
  (surfaced prominently, not silently logged) — **unless** the single-window-miss carve-out below
  applies.
- **FLAG** → accepted, no auto-revert. This is the *expected* outcome — v69 through v74 have all
  been FLAG in-sample; FLAG-in-OOS-too is the base rate, not a surprise.
- **SHIP** → exceeds expectations, note only.

**Single-window-miss carve-out (APPROVED per RULING OQ-1 — a pre-registered choice made before
the eval):** a BLOCK escalates immediately only if the 75+ OOS matched-N ≥ 300 **and**
t_mom < 1.0 (a decisive miss). A BLOCK with N < 300, or t_mom in the near-miss band [1.0, 2.0),
is a single-window miss: document it, do **not** escalate, extend observation to **2027-06-15**.

### H2 — apex-EV vs climatology floor

**Operationalization (CONFIRMED per RULING OQ-2 — distinct failure modes are the point; H1 may
FLAG while H2 passes):** an *absolute-magnitude* floor check on OOS rows, distinct from H1's
*skill-vs-momentum* verdict — does the OOS 75+ apex-EV point estimate clear the **frozen**
in-sample climatology value (+2.323%, section 2), independent of whether that OOS reading is
itself significant yet at current N. This is deliberately the softer of the two EV checks: it
catches an environment-level degradation (a generally worse forward tape) that a pure
score-vs-baseline t-test could mask at low N.

**Method:** reuses H1's `run_scorecard(oos=True)` call — no separate pull. Reads
`out['buckets']` thr=75 `ev_pct`.

**Verdict:** PASS if `oos_75plus_ev_pct >= 2.323067872436485`; FLAG otherwise. No BLOCK tier alone
(a BLOCK-worthy reading here would also trip H1). The OOS window's own freshly-computed
climatology is reported alongside as context (regime-shift signal, not gated).

### H3 — live ledger inside the MC envelope

**Method:** (a) a **frozen** env-var recipe (`run_h3_envelope.py`) reproducing the live Apex
15-DTE config on `monte_carlo.py` (the 30-DTE engine, generalized to 15-DTE via
`NOMINAL_CAL_DTE`/`HOLD_CAL_DAYS` — the proven `concentration_2x` pattern; **not**
`monte_carlo_15dte.py`, which has no calendar-hold support), N=500, `WIN_START=2026-06-01` (the
live ledger's own `start_date`). Registered now (section 9); re-run with the **identical** recipe
at the Dec read, only `WIN_END` extended toward the eval date (a pre-registered non-amendment,
section 6), as its own queue job under **normal queue discipline** (RULING OQ-3b). (b) the live
reading — `GET /api/portfolio/state` for the same window.

**Marking-verification pre-step (MANDATORY before adjudicating any breach — RULING OQ-3a):** the
December read must first rule out the known ledger-marking divergence class (the ~$295
MTM/`pending_requal` display-curve item): run
`python experiments/portfolio_engine_parity/validate.py`, confirm positions/closes/pnl_pct/final
cost-equity are bit-exact and the curve divergence remains display-only, then pass
`--marking-verified` to `run_oos_eval.py`. The evaluator enforces this mechanically: an indicated
breach without the flag returns `BREACH_PENDING_MARKING_VERIFICATION`, never a verdict. A PASS
needs no gate (there is no breach to adjudicate).

**Verdict:** PASS if `live_max_dd_pct` is within [p05, p95] of the envelope's `dds` distribution
**and** `live_total_return_pct` is within [p05, p95] of `finals`. DD-primary per project doctrine
— a DD-band miss is decision-relevant; a return-band-only miss is context. Direction-aware: only
DD *above* p95 / return *below* p05 are misses; favorably outside the band is a PASS with a note.

**Consequence of a significant DD breach (post marking-verification) — RULING OQ-6:** a
**MANDATORY INVESTIGATION** of engine/fill fidelity (cross-reference P3.7's real-fill log if it
exists by then), **not a ship freeze** (freeze-ships belongs to H1 alone). Not automatically a
verdict on the score's own edge — that's H1/H2/H4's job.

**Piecewise protocol (RULING OQ-7):** H3 segments per profile-era on any live-profile change
before 2026-12-15. The pre-change segment's envelope is **already frozen** (segment 1,
`references.json` → `hypotheses.H3.segments`; its `WIN_END` truncates at the switch date). Each
new segment **freezes its own recipe at switch time** (new profile params → new
`run_h3_envelope`-style frozen env recipe, `WIN_START` = switch date), recorded via an Amendment
Log entry — the piecewise handling itself is pre-registered; only the segment recording is
logged. At eval: per-segment live metrics from equity-curve slicing (return over segment =
`curve[seg_end]/curve[seg_start]−1`; max DD *within* the segment), each segment against its own
envelope, overall H3 verdict = worst segment. `run_oos_eval.py` refuses to adjudicate a
multi-segment H3 until the per-segment evaluation is implemented (it returns
`MULTI_SEGMENT_NOT_IMPLEMENTED` with this spec as the instruction). Pre-registering this now
removes any eval-cleanliness reason to delay the P0.3 decision.

**Noise caveat:** the envelope's dispersion comes only from gap-fill randomness on **one** realized
price tape (not resampled market histories), so bands may be tighter than "Monte Carlo" implies.
One specific live draw near/outside a tight band at low N is not automatically alarming — read it
with the same single-window-miss patience as H1/H4.

The full frozen env-var table lives in `references.json` → `hypotheses.H3.frozen_env_recipe` and
is reproduced verbatim (with rationale per line) in `run_h3_envelope.py`.

### H4 — WR15 within CI

**Method:** per call band, a 95% normal-approx CI around the frozen in-sample `wr`
(`wr ± 1.96·sqrt(wr·(1−wr)/n)`). At eval time, `oos_wr` for the same band from `Score` (date >
cutoff, active version) joined to `barrier_outcomes` (`barrier_set='30dte_generic'`, `period='15d'`
— the barrier-independent Stage-1 target, distinct from the option-aligned `30dte_apex`/`30dte_opt`
sets H1/H2/H5 use).

**N floor:** bands with in-sample N<100 (90-94 N=56, 95-100 N=11) are report-only, not gating —
mirrors the Stage-1 W2/W3 noise-aware N≥100 convention used throughout this repo's gates. Only
75-79 / 80-84 / 85-89 gate.

**Consequence:** a gating-band miss (OOS `wr` outside its CI) on a single window is a
single-window miss per the task's own instruction — document, extend to 2027-06-15, do not BLOCK
immediately.

### H5 — v73-vs-v74 retirement-regret on OOS rows

**Two-stage protocol (RULING OQ-4, pre-registered):**
- **Stage 1 — the H5 gate:** `run_scorecard(vid=73, oos=True)` vs `run_scorecard(vid=74, oos=True)`
  on the identical OOS window (the cheap apex-EV comparison). Both versions are **actively,
  continuously scored** — `CADENCE_MIN_VERSION_ID=69` auto-enrolls every v69+ version into the
  daily scoring cadence; confirmed live 2026-07-13 that v69–v74 all have score rows through
  today. No reconstruction/backfill needed. A **regret flag** requires v73's OOS 75+ apex-EV to
  exceed v74's at Welch t≥2.0 (on the per-trade EV vectors) with v73's 0d verdict not worse than
  v74's. NO-REGRET (retirement stays validated) if v74 ≥ v73 or the gap doesn't clear t≥2.0.
- **Stage 2 — mandatory escalation before any conclusion:** a t≥2-confirmed stage-1 regret flag
  escalates to the **full DD-primary re-ablation** (queued N=300, the *original* v74 retirement
  methodology — whole-tail-ablation MC per `experiments/skill_vs_baseline/OVERNIGHT_FINDINGS.md`,
  the −10.8pp 5y WorstDD basis) on OOS rows, run **before** any regret conclusion is drawn.
  **Nothing further is built now** — `EXT_H5_DD_ABLATION` in `run_oos_eval.py` is the named,
  deliberately-unbuilt stage-2 hook that raises until the trigger fires.

**Why stage 1 alone can never conclude:** the pooled-EV comparison is not the methodology that
originally justified the retirement. v73's pooled EV is *already* numerically higher in-sample
(section 2) without that contradicting the retirement — v73 admits more low-quality supply that
inflates DD without proportionally inflating pooled EV once cascade-level correlation is accounted
for. That is exactly why the ruling makes stage 2 mandatory on a stage-1 flag.

### H6 — per-lever drift = the only sanctioned lever-consolidation revisit trigger

**Two lines (RULING OQ-5):** H6 carries both a new per-lever drift metric (line a, the direct
lever lens the draft flagged as missing) and `tier_drift.py` (line b, the component/score-tier
diagnostic lens). **Neither line triggers anything except H6's documented lever-consolidation
revisit** — a review, never an automatic action; the review itself is future scoped work.

**Line (a) — per-lever drift metric (NEW, pre-registered; implemented in `run_oos_eval.py`):**
per shipped lever (RXDD / SVR / MWDD / TVDD / BDIV + F3F), per trading day (spine = MarketBreadth
dates in the window), compute the lever's **market-state call-alloc scale** using
`backtest_cascade`'s *own imported* scale functions (`_rxdd_call_scale` / `_mwdd_call_scale` /
`_tvdd_call_scale` / `_bdiv_call_scale` / `_breadth_alloc_scale`) with the shipped constants and
the engine's on-or-before map-lookup semantics (`dte_router.value_on_or_before`). Definitions:
- **active(day)** = scale < 0.995.
- **days-active fraction** = active days / spine days.
- **mean contraction depth** = mean(1 − scale) over active days.
- **apex-EV on active days** = mean `30dte_apex` option-EV (+0.30/−0.70/−0.40, 30d window) of the
  window's 75+ CALL signals dated on active days (inactive-day mean reported alongside).
- **DD-gate disclosure:** RXDD/MWDD/TVDD are DD-gated in the engine; the gate is **bypassed**
  here (dd=1.0) because the market-state component is the only path-independent definition that
  admits an in-sample reference (the in-sample era has no single canonical book-DD path). The
  VIX-panic exclusions (MWDD/TVDD) are market-state and are **kept**. VIX comes from
  `MarketRegime.vix_close` via MySQL directly (not the pre-materialized parquet, which can go
  stale).
- **SVR** is per-signal (needs `semivol_r` per entry from PriceHistory) — **measured at eval
  time** per the ruling's cheap-only rule; its spec is frozen in `references.json` and both its
  reference and OOS sides get computed together at eval with the same code, keeping the
  comparison like-for-like.

**Frozen in-sample reference (computed 2026-07-13 via `--freeze-lever-reference`; window
2016-01-06→2026-06-15, 13,001 75+ signals, 12,847 apex-resolved; full precision + constants
snapshot in `references.json`):**

| Lever | days-active fraction | mean depth | apex-EV active | apex-EV inactive |
|---|---:|---:|---:|---:|
| RXDD | 0.744 | 0.167 | +2.85% | +3.08% |
| MWDD | 0.908 | 0.234 | +3.12% | +1.30% |
| TVDD | 0.848 | 0.234 | +2.70% | +3.89% |
| BDIV | 0.316 | 0.216 | +2.45% | +3.10% |
| F3F | 0.257 | 0.248 | +1.64% | +3.51% |

**Line-(a) review-grade flag (pre-registered):** per lever, with ≥60 OOS trading days:
`ratio = (oos_days_active_fraction + 0.01) / (ref_days_active_fraction + 0.01)`; **DRIFT_FLAG if
ratio > 2.0 or < 0.5**. Depth + apex-EV lines are reported context (OOS active-day signal N will
rarely clear 100 in ~6 months). Asymmetry disclosure: for the high-baseline levers (MWDD ~0.91,
TVDD ~0.85) the >2.0 side is unreachable by construction — there the test effectively detects
only a band going *dead* (<0.5×), which is exactly the redundancy signal the consolidation
revisit wants.

**Line (b) — `tier_drift.py`, unmodified, as the component-lens diagnostic:** compare its flags
against the frozen baseline (section 2): one existing flag, `call 75-79 TREND(3x declining)`
(53.8→52.2→51.9, v72→v73→v74), not yet CUM-significant.

**Consequence (either line):** REVIEW_TRIGGER if line (a) flags any lever on the ratio test, or
line (b)'s existing TREND flag becomes CUM-significant (two-proportion z≤−2, the tool's own
threshold), or any new line-(b) tier/band flag appears — the sanctioned trigger to *open* the
lever-consolidation review per `weatherization.md` Flaw #4/#5.

**Caveat (line b only):** `tier_drift.py` reads per-version research packs built over rolling
lookback windows ending at build time, not a clean pre-/post-cutoff split — the frozen baseline
captured 2026-07-13 already carries ~1 month of post-cutoff accrual mixed into the "current" v74
pack. Disclosed, not corrected. Line (a) does **not** share this caveat (its OOS side queries
date>cutoff rows directly).

---

## 5. Noise-aware thresholds — summary table

| Hypothesis | t/z floor | N floor | Single-window-miss rule |
|---|---|---|---|
| H1 | t≥2.0 (dual baseline) | 75+ matched N≥300 to escalate | Escalate only if N≥300 **and** t_mom<1.0; else extend to 2027-06-15 (RULING OQ-1) |
| H2 | — (magnitude, not significance) | shares H1's N | PASS/FLAG only, no BLOCK tier (RULING OQ-2) |
| H3 | — ([p05,p95] band membership, direction-aware) | envelope N=500 iterations (fixed); live-ledger N grows with elapsed time | Breach adjudication gated on marking-verification (RULING OQ-3a); a verified DD breach → MANDATORY INVESTIGATION, never a freeze (RULING OQ-6); return-band-only miss is context |
| H4 | 95% CI (≈ z 1.96) | band N≥100 to gate; per-band OOS N≥30 to evaluate | Gating-band miss on one window → document, extend to 2027-06-15 |
| H5 | Welch t≥2.0 (stage-1 gate) | shares H1's N per version | Stage-1 flag at t≥2 → MANDATORY stage-2 DD re-ablation before any conclusion (RULING OQ-4); non-REGRET is the default absent t≥2.0 |
| H6 line (a) | days-active ratio >2.0 or <0.5 (epsilon-stabilized) | ≥60 OOS trading days per lever | DRIFT_FLAG opens the lever-consolidation review, nothing else (RULING OQ-5) |
| H6 line (b) | two-proportion z≤−2.0 (tool's own) | tool's own per-band N | New/worsened flag opens the same review, not an automatic BLOCK |

**Global rule (RULING OQ-6):** nothing in H1–H6 triggers an *irreversible* action by itself.
**Only H1 carries freeze-ships** — its decisive BLOCK (N≥300, t_mom<1.0) freezes *new* ships
pending FABLE/user review; it does not revert anything already shipped. H2–H6 are uniformly
softer: FLAG-and-continue, extend-to-2027-06-15, open-a-review, or (H3, marking-verified DD
breach) a mandatory investigation.

---

## 6. Amendment Protocol

- **Who may amend:** FABLE (architect review) or the user. No autonomous agent session may alter
  `references.json` or this document's frozen numbers/thresholds on its own initiative.
- **What counts as an amendment:** any change to a frozen number, threshold, N floor, or
  consequence mapping in section 2/4/5, or to the H3 frozen env recipe's *non-window* fields.
- **What does NOT count as an amendment (pre-registered, self-executing):**
  - Re-running `run_h3_envelope.py` with the **same** recipe and only `WIN_END` extended toward
    the eval date (section 4 H3) — this is completing the protocol, not changing it.
  - Re-running `tier_drift.py` unmodified at eval time (H6 line b), and computing H6 line (a)'s
    OOS side / SVR slot per the frozen spec.
  - The H5 stage-2 escalation itself when its pre-registered trigger (t≥2 stage-1 regret flag)
    fires (RULING OQ-4) — running it then is the protocol, not a change to it.
  - `run_oos_eval.py --selftest` at any time (never evaluates outcomes).
- **How to amend:** append a dated, reasoned entry to the Amendment Log below **before** the
  amended run, edit `references.json` accordingly, and note the amendment in the eval output.
  Silent edits are a holdout-lock violation in spirit even if not in code.
- **Live-profile changes (RULING OQ-7 — piecewise, pre-registered):** if the staged 30-DTE Apex
  switch (or any other live-profile change) is applied before 2026-12-15, the **piecewise
  protocol** (section 4 H3) executes: freeze a new segment recipe + envelope at switch time
  (`WIN_START` = switch date, new profile params), truncate segment 1's `WIN_END` at the switch
  date, retain (never delete) the old envelope, and record the new segment via an Amendment Log
  entry. The piecewise handling itself is pre-registered — only the segment recording is logged.
  Pre-registering this removes any eval-cleanliness reason to delay the P0.3 decision.

### Amendment Log

*(no post-freeze amendments to frozen numbers/thresholds yet)*

- **2026-07-17 — ADDITIVE APPEND (not an amendment):** §11 "Parked-Lead Pre-Registration Pack"
  (PL-1..PL-6) + §12 "December Executor Guide" appended by FABLE (overnight brief
  `overnight1_prereg_masterplan`). Zero changes to the frozen sections 1-6/9; §7 gained a pointer
  note; the stale header Status line was corrected (the package has in fact been committed since
  2026-07-13). Where a §11 lead had NO numeric bar at park time (PL-3), its bar is SET in §11
  pre-peek — a new pre-registration, not an amendment of an existing one.

- **2026-08-02 — SEGMENT RECORDING (pre-registered piecewise, RULING OQ-7; not an amendment):**
  P0.3 applied on user green-light — live Apex profile switched from the 15-DTE n4 flat-25%
  elbow to **30-DTE n10 flat-10%** (`portfolio_profiles.json` apex v4; evidence:
  `experiments/apex_dte_dd/SHIP_HANDOFF.md` Option B, T-gate task #610 DD-better 12/12 windows,
  clean-substrate E-cert apex_n10 collapse 0/12 — `experiments/newbox_rebaseline/ECERT_SUMMARY.md`).
  Per the pre-registered protocol executed in `references.json`: segment 1's `win_end` truncated
  at 2026-08-02 (its frozen envelope retained untouched; the Dec re-run extends segment 1's
  WIN_END only to the switch date), and segment 2 (`win_start` 2026-08-03, recipe
  `frozen_env_recipe_seg2` from the apex-v4 params) frozen at switch time. Segment 2's envelope
  MC runs at the Dec re-read under normal queue discipline (OQ-3b) — the segment had zero live
  trading days at freeze. Zero frozen numbers/thresholds changed.

> Provenance note: the FABLE rulings of 2026-07-13 (section 8) were applied **pre-commit** as
> part of the draft's acceptance — they finalize the pre-registration rather than amend a frozen
> one, so they are recorded in section 8, not here. This log starts counting after FABLE commits
> the package.

---

## 7. Appended — WATCH ITEMS (non-gating December reads)

These do not feed H1–H6's verdicts. They are named December-adjacent watch items folded in per
the task brief so they aren't tracked only in scattered memory/docs.

| Item | Pointer | Action | Gates H1-H6? |
|---|---|---|---|
| **iv_engine_pertrade M3 SL-FNR** | `experiments/iv_engine_pertrade/VERDICT.md` ruling #4 | Re-read M3 SL-FNR when the real-SL event count ~doubles (currently N=33, underpowered). Ledger machinery exists; one queue job + one metrics pass. Pair stays PARKED default-OFF regardless — re-open needs a NEW DATA CLASS (real fills / mid-quotes / P1.4 vega-state), not just more SL events. | No |
| **miss_regime_fakeout Layer-B unlock** | `experiments/miss_regime_fakeout/DESIGN.md` §4 + `VERDICT.md` | Once the cutoff re-locks forward post-eval, the v74 post-cutoff intraday window (46+ events as of 2026-07-10, growing ~2.5/trading day) rolls in-sample. Re-run Phase-0 attribution + the WCF-precedent swing metric **at the same locked bars** (z≥3 clustered, N≥500, dual-split replication) — no bar renegotiation. Named follow-up: the ABSORPTION/CLIMAX same-day volume-blend transition guard (candidate 1), ungated by the shipped `INTRADAY_TYPE_CONF_GATE`. | No |
| **OSK forward_ranks re-read** | `gameplan.md` §4 (OSK row) + `experiments/osk_tilt/DESIGN.md` | Natural cadence ~2026-10 and ~2027-01 (does not align exactly to Dec-15); osk_tilt's own PARKED bar (best t_clust 1.06 vs bar 2.0, N-wall ~69) revisits when eligible N ≈ doubles. Tracked on its own schedule. | No |
| **gex OOS moot-note** | `experiments/gex/VERDICT.md` | **No action.** Per-trade/cross-sectional GEX is CLOSED PERMANENTLY (all NULL). The staged 300-row OOS cohort (resolves ~2026-07-29) matters only if `gex_ratio` is ever re-opened — "do not spend the re-read unless that happens." | No |

> **2026-07-17 update:** the M3, Layer-B, and OSK rows above now have FULL mechanical
> pre-registrations — see §11 **PL-5**, **PL-4**, and **PL-6** respectively (this table's thin
> pointers remain valid but §11 governs execution). §11 also pre-registers three leads parked
> AFTER this table was written: **PL-1** (S1_vr5 path-persistence, 2026-07-16), **PL-2** (TEXTURE,
> 2026-07-15), **PL-3** (cdays_SMA_8_21 16-30, 2026-07-14). The gex row is unchanged (no action).

---

## 8. FABLE rulings (2026-07-13) — replacing the draft's open questions

The draft listed seven open questions (OQ-1..OQ-7) where this drafter had made interpretive calls
beyond the gameplan's one-clause spec. FABLE ruled on all seven on 2026-07-13; each ruling below
**replaces** its open question and has been applied throughout this document, `references.json`,
and `run_oos_eval.py`.

- **RULING (FABLE, 2026-07-13) OQ-1 — APPROVED as drafted.** The single-window-miss carve-out
  (N≥300 floor, t_mom<1.0 noise-qualifier → extend to 2027-06, per the gameplan row) stands.
  Both thresholds are **pre-registered choices made before the eval** (section 4 H1).
- **RULING (FABLE, 2026-07-13) OQ-2 — as drafted.** H2 stays the ABSOLUTE climatology-floor test
  on OOS rows, distinct from H1's skill-vs-momentum verdict. **Distinct failure modes are the
  point** (H1 may FLAG while H2 passes).
- **RULING (FABLE, 2026-07-13) OQ-3 — envelope recipe + WIN_END-extension-only rule APPROVED,
  with three additions:** (a) the December H3 read **must verify ledger-marking integrity FIRST**
  (the known MTM/`pending_requal` divergence class) before adjudicating any breach — enforced in
  `run_oos_eval.py` (a breach without `--marking-verified` returns
  `BREACH_PENDING_MARKING_VERIFICATION`, never a verdict); (b) December re-runs use **normal
  queue discipline** (the one-job budget was this drafting session's scope constraint, not
  protocol); (c) the 2026-07-13 interim read (live −39.70% vs return p05 −19.44%; DD better than
  modeled p05) is recorded as **CONTEXT with the recipe caveat, explicitly non-adjudicated**
  (section 2).
- **RULING (FABLE, 2026-07-13) OQ-4 — two-stage, pre-registered.** The cheap apex-EV comparison
  **IS** the H5 gate (stage 1); a regret flag at t≥2 **escalates to the full DD-primary
  re-ablation** (queued N=300, the original v74 retirement methodology) **before any conclusion**.
  Nothing further is built until that trigger fires (section 4 H5).
- **RULING (FABLE, 2026-07-13) OQ-5 — H6 gets BOTH lines.** (a) a NEW pre-registered per-lever
  drift metric — per shipped lever (RXDD/SVR/MWDD/TVDD/BDIV + F3F): days-active fraction, mean
  contraction depth, apex-EV on active days, OOS vs an in-sample reference (metric spec'd in
  section 4 H6; reference slots + frozen values in `references.json`; measurement **implemented**
  in `run_oos_eval.py` for the five market-state levers — cheap from existing data via the
  engine's own importable scale functions — with SVR marked *measured at eval time*, per the
  ruling's cheap-only rule); (b) `tier_drift.py` as the component-lens diagnostic line. **Neither
  line triggers anything except H6's documented lever-consolidation revisit.**
- **RULING (FABLE, 2026-07-13) OQ-6 — only H1 carries freeze-ships.** H2–H6 are uniformly softer,
  as drafted. **Addition:** a significant H3 breach (post marking-verification) triggers a
  **MANDATORY INVESTIGATION**, not a freeze (section 4 H3, section 5).
- **RULING (FABLE, 2026-07-13) OQ-7 — PIECEWISE envelope on profile change.** If the staged
  30-DTE switch (or any live-profile change) applies before 2026-12-15, H3 segments per
  profile-era: the pre-change segment's envelope is **already frozen** (this one); each new
  segment **freezes its own recipe at switch time**. Pre-registered now — **no eval-cleanliness
  reason to delay P0.3.** (Section 4 H3 "piecewise protocol"; `references.json`
  `hypotheses.H3.segments`.)

---

## 9. H3 envelope task status

**Config:** frozen recipe in `references.json` → `hypotheses.H3.frozen_env_recipe`, implemented in
`run_h3_envelope.py`. Window: `WIN_START=2026-06-01`, `WIN_END=2026-07-10` (the live ledger's own
`last_processed_date` at authoring — the latest date with confirmed-complete price/score data),
N=500, paired-seed canonical MC.

**Queue task:** submitted via
`python trader.py queue submit --priority normal --db heavy --cpu 6 --restartable --dedup oos2026_envelope --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 -- python experiments/holdout_oos_2026_12/run_h3_envelope.py`.

**Status: DONE.** Task **#601**, exit=0, runtime=30s (short window, N=500 completed comfortably
within this session — no multi-hour wait needed). `envelope_h3.json` and the raw MC dump
(`_mc_raw_h3_live_envelope.json`) are on disk in this directory. Results summarized in section 2
above. One correctness fix was made and re-applied post-hoc (via `--skip-mc`, reprocessing the
already-computed raw dump — **no second MC run/queue submission was needed or used**): the
engine's raw per-iteration `finals`/`dds` arrays are in dollar-equity / 0-1-fraction units
respectively, not the `%`-scale their names suggest — `run_h3_envelope.py` now converts both
explicitly and documents the conversion inline.

**At the Dec-2026 read:** re-invoke the identical script with only `--win-end` extended (e.g.
`--win-end 2026-12-12`), submitted as its own queue job under **normal queue discipline** —
the one-job budget was this drafting session's scope constraint, not protocol (RULING OQ-3b,
section 8).

---

## 10. Reminder mechanism

**Installed and verified live (2026-07-13).** A Windows Task Scheduler entry
(`TraderOOSEvalDue2026`, registered via `experiments/holdout_oos_2026_12/install_reminder.ps1`,
which invokes the thin wrapper `oos_due_ping.ps1` — mirrors the house pattern in
`scripts/install_portfolio_notify.ps1`) fires daily at 08:00 local time, 2026-12-15 through
~2026-12-29 (a 14-day repeating window via `-Once -RepetitionInterval 1d -RepetitionDuration 14d`,
not a single fragile one-shot) **plus `-StartWhenAvailable`**, so even a missed occurrence (box off
exactly at trigger time) catches up. Verified: `Get-ScheduledTask -TaskName TraderOOSEvalDue2026 |
Get-ScheduledTaskInfo` → `NextRunTime: 15-Dec-26 8:00:00 AM`.

Each firing runs `python trader.py queue submit --priority normal --db none --dedup
oos2026-due-ping --reason "..." -- echo "OOS-EVAL-DUE-2026-12-15: ..."` — a harmless,
self-explanatory marker task that shows up in `trader queue list --all` / `trader queue status`.
It deliberately does **not** attempt to auto-run `run_oos_eval.py` — interpreting H1-H6 verdicts
and routing an H1 BLOCK to FABLE/user needs a human/agent in the loop, not an unattended cron job.

This is the **redundant hard trigger**; the primary, human-readable signal is Block A's heartbeat
digest, which already carries a days-until-OOS line. Undo: `schtasks /delete /tn
TraderOOSEvalDue2026 /f` (or `Unregister-ScheduledTask -TaskName TraderOOSEvalDue2026 -Confirm:$false`).

---

## 11. Parked-Lead Pre-Registration Pack — the Dec-2026 unlock set (PL-1..PL-6)

**Appended 2026-07-17 by FABLE (overnight brief `overnight1_prereg_masterplan`). ADDITIVE — nothing
in sections 1-10 is amended.** This section is the mechanical execution spec for every parked lead
whose re-read keys off the December-2026 OOS unlock. It exists so the December run is **purely
mechanical**: a Sonnet/Opus-class executor follows it verbatim; every judgment call was made here,
in advance, before any OOS row was peeked. Verification basis: each lead's experiment dir was
re-read on 2026-07-17 (six independent retrieval passes with verbatim quotes + file:line pointers)
before anything below was written.

**Precedence rule:** for each lead, the *source-of-truth lock* is the cited section of the
experiment's own FINDINGS/DESIGN/VERDICT file, frozen at park time. This pack restates those bars
verbatim and adds only EXECUTOR SCAFFOLDING (cache mechanics, N-sufficiency handling, version
pinning, ordering) that the source locks left implicit. On any discrepancy between this pack and a
source lock: **STOP — do not run.** Diff the source file against its park-date git blob; a
post-lock edit to either file is an Amendment-Protocol event (section 6), not something an
executor resolves alone.

**Scaffolding vs bar:** anything marked `[SCAFFOLDING 2026-07-17]` is an operational rule added by
this pack — it may pin *how* to compute a locked quantity or *what to do when a bar cannot yet be
evaluated* (insufficient N, blocked dependency), but it never moves a locked threshold. Where a
lead had NO numeric bar at park time (PL-3), the bar set here — tonight, pre-peek — IS its
pre-registration, marked `[BAR SET 2026-07-17]`.

**Common OOS-data mechanics (PL-1/PL-2/PL-3):**
1. The IS substrate chain is holdout-enforced and its caches stop at/before the cutoff:
   `.cache/trend_ma_lattice/{ledger,features}_v74_full.parquet` (≤2026-06-15) →
   `.cache/peak_fakeout/features_v74_full.parquet` (BASE_FRAME for wave_cycle_mine) — plus
   `.cache/intraday_overnight/ohlc.parquet` (max bar 2026-06-08; must be extended first).
2. **Preferred path:** run PL-1/2/3 AFTER the cutoff re-locks forward (§12 step 4) — the rebuilt
   chain then needs no holdout bypass at all. **Fallback path** (re-lock deferred by the user):
   rebuild with `HOLDOUT_DISABLE=1` — sanctioned HERE, for §11 re-reads only, on/after
   2026-12-15, and ONLY for builds whose output a §11 driver consumes. Either way, every
   adjudication driver must assert the OOS slice explicitly:
   `entry_date > 2026-06-15 AND entry_date <= <eval-date>`. `HOLDOUT_DISABLE` without that
   explicit slice assertion is a protocol violation (it bypasses the leak-check; it is not a
   license to pool silently).
3. Parquet/ledger builds go through `trader queue submit` (CLAUDE.md Long-Running Compute), always
   with `--env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1`.
4. **Frozen fitted objects:** any bucket boundary fitted on IS data (tercile cut-points, the P_run
   rung) is part of the locked DEFINITION. OOS rows are bucketed with the FROZEN IS cut-points —
   never re-fit on OOS or pooled rows. Recover the numeric cut-points from the archived IS
   parquets (`.cache/peak_fakeout/features_v74_full.parquet`,
   `.cache/wave_cycle_mine/features_v74_full.parquet`) or recompute them deterministically from
   the IS ledger alone; record them in the run log BEFORE scoring any OOS row.
5. **Version pinning:** PL-1 (vr5) and PL-2 (TEXTURE) continue the v74 study — their OOS rows are
   v74 rows (`version_id=74`; the ≥v69 cadence keeps v74 scored daily even if v75+ ships). PL-3's
   own lock says "the then-active substrate" — pin THAT as written and record the active version
   id + commit in the run log. If a non-v74 version is active in December, PL-1/PL-2 still read
   v74 rows.
6. **Adjudication order:** H1-H6 verdicts are RECORDED (run_oos_eval.py output on disk) before any
   PL driver runs. No PL result may inform an H-series adjudication or vice versa.

**Audit & attempt-log rules (added 2026-07-17 after the adversarial pass — these are the
enforcement layer for rules 1-6):**
7. **Sealed slice.** At the FIRST §11 driver invocation, compute and record ONCE (in the attempt
   log): the OOS slice upper bound = the latest entry date whose 15-trading-day apex window
   completes on or before that invocation date. That bound is then IDENTICAL for every §11 lead
   and every subsequent attempt — the slice never rolls forward between attempts (kills the
   "wait two weeks for the tail to resolve and re-roll" game).
8. **Append-only attempt logs.** Every driver invocation — including dry runs and aborted runs —
   appends a line to `results/PL_attempt_log.md`: timestamp, lead, command, verdict-or-abort.
   Results files cite ALL attempts. Each PL results file must embed: the sha256 + filename of the
   non-rehearsal `results/eval_*.json` it post-dates (rule 6's audit trail), queue task ids,
   input-cache paths + mtimes/sha256s, the recorded version id(s), the frozen cut-points used,
   and any script-printed window lines. A results file missing these is void. Overwriting a
   results file is void.
9. **Frozen fitted objects are now NUMERIC:** `parked_leads_frozen.json` (this directory,
   committed 2026-07-17) records the vr5 and climax_day/parabolic tercile edges, the P_run rung,
   and the sha256 + snapshot copies of both IS feature parquets
   (`.cache/holdout_oos_2026_12_frozen/`). Common rule 4's "recover or recompute" is superseded:
   USE THESE NUMBERS. (Closure for: the December rebuild overwrites the very parquets the
   cut-points would have been recovered from.)
10. **`HOLDOUT_DISABLE=1` is per-subprocess only** — passed as `--env` on the single queue
    submission (or `env VAR=1 python ...` for one foreground call), never exported into a session
    or parent shell. The preferred path (post-re-lock, no bypass at all) stands.
11. **Cluster-key assertion.** Any driver calling the CR1 machinery must construct clusters from
    the entry-DATE column, assert `n_clusters == n_unique(entry_date)` on its frame, and print G
    (cluster count) in its output. Citing the reused function without the assertion does not
    satisfy this.
12. **Discrepancy threshold for the STOP rule:** the §11-preamble STOP fires on SUBSTANTIVE
    drift — numbers, thresholds, definitions, added/removed conditions. Formatting-only
    differences (whitespace, emphasis) between this pack and a source lock are declared
    non-substantive and do not license a halt.

---

### PL-1 — S1_vr5 path-persistence (parked 2026-07-16)

**Source of truth:** `experiments/wave_cycle_mine/FINDINGS.md` §"PARK — locked Dec-2026 OOS
re-read". Cross-referenced: known-issues.md WHAT-NOT-TO-DO (2026-07-16 wave/cycle entry).

**Hypothesis (one sentence):** trailing-path persistence (Lo-MacKinlay 5-day variance ratio on the
trailing 120 daily log-returns) separates funded 75+ call entries on plunge risk —
choppy/anti-persistent (T1) entries plunge less, trending/persistent (T3) entries plunge more.

**IS park numbers (context, not the bar):** T1 N=1,934 +2.79pp WR / −3.45pp plunge, z_clust(pl)
−3.96, t_ctl −3.93, legs 5/5; T3 N=1,933 the mirror (z_clust +3.53). Park cause: actionability
floor missed — T1 d_ev +0.0298 vs the 0.03 floor ("a 0.0002 miss"); WR leg 2022-flips (only
PLUNGE is sign-stable 5/5).

**Locked bar (verbatim, FINDINGS.md §Park):**
> On post-2026-06-15 OOS rows at the December unlock (`experiments/holdout_oos_2026_12/` window):
> 1. **Confirm:** T1-vs-T3 plunge separation same sign with |z_clust| ≥ 2 on OOS rows alone; AND
> 2. **Actionability:** pooled (IS+OOS) T1 d_ev ≥ 0.03 (the locked per-cell form).
> 3. **Both pass → license an SVR-class per-signal Stage-3 probe** (vr5-keyed sizing tilt, its own
>    B→C→D N=500 incl COVID, collapse=0) as a separately gated step. **OOS sign-flip or
>    |z_clust|<1 → close the axis permanently.** Bars fixed here; do not re-derive.

**Definitions inherited from the IS study (no new definitions were given at park):** vr5 =
Var(overlapping 5-day sums)/(5·Var(1-day)), unbiased (ddof=1), demeaned, trailing 120 daily log
returns ending at the entry date, ≥100 valid; terciles = pooled-IS-substrate cut-points [FROZEN,
common rule 4]; z_clust = CR1 sandwich, clusters = entry date, on the PLUNGE outcome; d_ev = cell
mean apex-EV minus whole-substrate mean apex-EV (the per-cell-vs-rest form — NOT the T1↔T3
spread; the park text records that distinction explicitly).

**[SCAFFOLDING 2026-07-17]:**
- S1. OOS slice = v74 funded 75+ CALL ledger rows, entry date in (2026-06-15, sealed upper bound
  per common rule 7], 15d-apex outcomes resolved. Substrate rebuild per common rules 1-3.
- S1a. **Leg-1 statistic, pinned (adversarial closure):** compute z_clust_T1 and z_clust_T3
  separately on the OOS-only frame, exactly as `compute_cell` computes per-cell-vs-substrate
  effects (the IS convention that produced −3.96/+3.53). Leg 1 passes ONLY if BOTH cells
  independently reach |z_clust| ≥ 2 with each cell's sign matching its IS direction (T1 plunge
  negative, T3 plunge positive). One-cell-clears is NOT a pass; it falls to the S2 zone (or the
  close condition if either cell sign-flips). Tercile bucketing uses the frozen edges in
  `parked_leads_frozen.json` (T1 ≤ 0.843154, T3 > 1.012533) — never re-fit.
- S1b. **Pooled d_ev, pinned (adversarial closure):** "pooled (IS+OOS)" = concatenate the IS
  frame ∪ OOS frame into ONE frame and run the existing base_ev/d_ev logic UNMODIFIED on that
  concatenation (base = the concatenated substrate's mean, cell-inclusive, as `compute_cell`
  does). Never a frozen IS base paired with a refreshed cell mean — that combination is void.
- S2. The middle zone the lock leaves implicit — same-sign with 1 ≤ |z_clust| < 2 —
  is **INCONCLUSIVE**: record it, do NOT close, re-read ONCE at 2027-06-15 (same bars, deeper
  OOS). A second inconclusive at 2027-06-15 → close the axis (an axis that cannot reach |z|≥2 on
  ~12 months of OOS while sitting below its own actionability floor is retired per the house
  bias-to-retire; the SVR-probe license always required z≥2 somewhere).
- S3. **Power disclosure (read before adjudicating; changes nothing):** at ~6 months of OOS
  accrual the expected per-tercile N is ~165; scaling the IS plunge effect (z 3.96 at
  N=1,934/cell) gives expected OOS z ≈ 1.1-1.2 *if the effect is fully real*, so under a true
  effect P(|z_clust| < 1) ≈ 40-50% — the locked close condition is deliberately harsh and can
  close a marginal-but-real axis. Disclosed, not changed: the axis missed its floor in-sample and
  the house bias is to retire. Apply the bar as written.
- S4. Pooled-d_ev arithmetic note: IS d_ev = +0.0298 at N=1,934; a ~165-row OOS cell moves the
  pooled value only fractionally — leg 2 is the actionability floor, leg 1 (OOS-alone z) carries
  the evidential load. By design; do not re-weight.

**Runner:**
```
# (queue) rebuild the substrate chain through eval-date (common rules 1-3), then:
# (queue) PYTHONIOENCODING=utf-8 python -u experiments/wave_cycle_mine/mine.py   # OOS-extended build
# Adjudication driver (~30 lines, December-executor work): T1-vs-T3 plunge z_clust on the OOS
# slice + pooled d_ev, REUSING mine.py/stats.py's CR1 + d_ev functions — never re-implementations.
```

**PASS action:** license the SVR-class Stage-3 probe exactly as locked (own B(100)→C(300)→D(500
incl COVID) ladder, collapse=0, DD-primary) — a SEPARATE gated step, not a ship.
**FAIL action:** OOS sign-flip or |z_clust| < 1 → the wave/cycle S-family axis closes PERMANENTLY
(the calendar family is already closed; the whole wave_cycle axis is then fully closed).
**INCONCLUSIVE:** per S2.

**Traps forwarded:** empirical trading-day index only (the static NYSE_HOLIDAYS table is
short-coverage AND missing the 2025-01-09 Carter closure — pattern in
`wave_cycle_mine/calendar_features.py`, which hard-fails on any other disagreement); ASCII-only
stdout + `PYTHONIOENCODING=utf-8`; stats machinery is COPIED not imported (sibling
`import features` collision); worktree PYTHONPATH pin+assert if run from a worktree;
`prep_mirror.py` is NOT needed for this re-read (the 2016-2020 mirror was the calendar family's
replication leg); queue all cache builds.

---

### PL-2 — TEXTURE: climactic acceleration inside strong runs (parked 2026-07-15)

**Source of truth:** `experiments/peak_fakeout/FINDINGS.md` §3 (the lock);
`experiments/peak_fakeout/PREREGISTRATION.md` (the 6-leg bar definition).

**Hypothesis (one sentence):** within strong 20d runs (P_run ≥ 1.25σ), climactic single-day
acceleration (climax_day top-tercile) or parabolic 5-vs-20d acceleration (parabolic top-tercile)
marks funded 75+ call entries with worse apex WR — losses arriving via expiry-drift, not SL-plunge.

**IS park numbers (context):** P_run∧climax_day-T3: N=474, ΔWR −4.9pp, z −3.40, 5/6 legs (fails
Psign 0.76); P_run∧parabolic-T3: N=194, ΔWR −9.3pp, 4/6 legs. All-window-negative signs; worst
cohort still wins ~65% ≫ BE 45 — no below-BE cohort exists (why it parked rather than shipped).

**Locked bar (verbatim, FINDINGS.md §3):**
> **Locked for the Dec-2026 OOS unlock (one-shot read, candidate only, defined NOW):**
> `TEXTURE := P_run(runup20/σ60 ≥ 1.25) ∧ (climax_day ∈ global-T3 ∨ parabolic ∈ global-T3)` —
> re-evaluate on OOS ≥ 2026-06-15 at the same 6-leg bar (windows = OOS quarters). Expect
> ΔWR ≤ −4pp with stable signs to even discuss a mechanism. The F8 mid-volume U and F6
> high-zone-age ride along as secondary reads only. Do not touch before the unlock.

**The same 6-leg bar (verbatim shape, peak_fakeout/PREREGISTRATION.md):** (1) |z_clustered| ≥ 3
(CR1 sandwich, clusters = entry date); (2) |t_controlled| ≥ 2.5 (clustered logit/robust-OLS;
controls = overall, trend, runup_20σ, pct-from-EMA50, peak-state main effect; finite-masked);
(3) P(sign-consistent ≥ 4/5 windows) > 0.90; (4) interaction delta ≥ 2pp WR (or 0.02 EV) vs the
unconditional effect; (5) N ≥ 150 in-peak; (6) sign-replicates on the full-universe 30dte_apex
mirror.

**Frozen fitted objects (NUMERIC as of 2026-07-17, `parked_leads_frozen.json`):** P_run rung =
1.25σ (locked numerically). Global-T3 lower edges, frozen from the IS parquet (sha256-recorded):
**climax_day T3 > 2.490408; parabolic T3 > 1.715940.** Apply these to OOS rows unchanged;
re-fitting terciles on OOS/pooled rows redefines the cohort and voids the read. Formulas frozen
as coded: climax_day = ret_max10/σ20; parabolic = runup5_σ20/runup20_σ20 gated on
|runup20_sig| > 0.3 (`experiments/peak_fakeout/features.py` — rebuild features with the same
code, never re-derive).

**Leg-4 comparator, pinned (adversarial closure):** the "unconditional effect" for the OR-union
TEXTURE cell = the effect of the SAME union `(climax_day ∈ T3 ∨ parabolic ∈ T3)` measured on the
NON-P_run OOS substrate; the interaction delta = (union effect inside P_run) − (union effect
outside P_run). Substituting a single-feature marginal (climax-only or parabolic-only) as the
comparator is void — the union had no in-sample baseline of its own, so the comparator must be
constructed with the identical OR logic on both sides.

**[SCAFFOLDING 2026-07-17]:**
- S1. **N-sufficiency (the binding constraint):** expected OOS accrual ≈ 500 funded-ledger rows by
  Dec-15; P_run prevalence 15.1% → ~75 in-run rows; the TEXTURE joint cell ≈ 20-40 rows — far
  below leg-5's locked N ≥ 150 — and "windows = OOS quarters" yields only ~2 quarters against a
  ≥4/5-window sign leg. **If TEXTURE joint N < 150 OR complete OOS quarters < 4: record
  INSUFFICIENT_N — report the descriptive ΔWR/Δplunge/N per quarter, adjudicate NOTHING — and
  defer the one-shot read intact to 2027-06-15.** The one-shot is spent only when the bar is
  actually evaluable; a descriptive report does not spend it. (Honest expectation, stated now: at
  2027-06 the joint cell is likely still N ≈ 40-80 — this read may genuinely need late-2027; do
  not force it.) **An INSUFFICIENT_N record is NOT exempt from rigor (adversarial closure):** the
  descriptive report must include the full row-count trail — OOS ledger rows → non-null-feature
  rows → P_run rows → TEXTURE-union rows, with the filters/queries used — so the N claim itself
  is auditable. A bare "N was small" without the trail is void.
- S2. Windows mapping when evaluable: "OOS quarters" = calendar quarters intersected with the OOS
  window; first = 2026-Q3 (starting 2026-06-16), last = the last COMPLETE quarter before the eval
  date. Leg 3 applies verbatim once ≥5 OOS quarters exist; with exactly 4, apply
  P(sign-consistent 4/4) > 0.90 — locked here explicitly as the only faithful K<5 reading
  (all-of-K, same Jeffreys-posterior machinery), so December doesn't choose. (Note: the K=4
  branch is expected to first become live at the 2027-06 extension, not the Dec-15 read — it is
  pre-registered now precisely so it exists before it is needed.)
- S3. Secondary reads (F8 mid-volume U, F6 high-zone-age) are REPORT-ONLY riders — never
  adjudicated, never promoted from this read.

**Runner:**
```
# (queue) rebuild trend_ma_lattice ledger/features through eval-date (common rules 1-3), then:
#   PYTHONIOENCODING=utf-8 python experiments/peak_fakeout/prep_lookups.py
#   (queue) PYTHONIOENCODING=utf-8 python -u experiments/peak_fakeout/mine.py   # OOS-extended
# Adjudication driver: the TEXTURE cell per the frozen definition on the OOS slice, 6 legs via
# mine.py's CR1 machinery. Rank by gated legs, NEVER raw t.
```

**PASS action:** TEXTURE clears all 6 legs on OOS → "discuss a mechanism" per the lock: it earns a
Stage-1 W1-W6 investigation as a CANDIDATE only (no dampener/tilt is pre-licensed; the sizing-lever
well remains documented-DRY, so any mechanism proposal must additionally clear the orthogonality
bar vs shipped levers). **FAIL action:** signs flip, or the bar fails at evaluable N → the
peak/texture axis closes permanently (extends the 2026-07-15 interaction-level close to the last
surviving cell). **INSUFFICIENT_N:** per S1 — defer intact, nothing spent.

**Traps forwarded:** near-constant buckets degenerate the CR1 t (require bucket share ∈ [5%,95%];
rank by gated legs, never raw t); nested peak-states duplicate rows up to 4× (count loci, not
cells); polars NaN≠null choke point + finite-mask regressors before fitting (the silent
false-NULL trap); the earnings lookup must bypass the `EARN_BOOST_ENABLED` gate (False in v74 —
the gate would silently empty the lookup); mcap PIT anchor-close mismatch is acceptable for
TERCILE bucketing only; statistical machinery COPIED not imported (sys.modules collision);
`.cache/intraday_overnight/ohlc.parquet` must be extended past 2026-06-08 before any rebuild.

---

### PL-3 — cdays_SMA_8_21 = 16-30, mid-aged short-kernel cross (near-miss logged 2026-07-14)

**Source of truth:** `experiments/trend_ma_lattice/FINDINGS.md` (near-miss + December language).
**No numeric re-test bar was locked at park time** — the source lock is qualitative: "if
`cdays_SMA_8_21=16-30` (and the fresh-cross negative pattern) hold sign and magnitude OOS,
cross-freshness earns ONE re-read on the then-active substrate — as a candidate feature, not a
presumption."

**Hypothesis (one sentence):** funded 75+ call entries taken 16-30 TRADING BARS after the most
recent SMA-8/SMA-21 cross (either direction) carry higher apex EV/WR than the rest of the funded
ledger (mid-aged continuation sweet spot; fresh crosses ≤5 bars lean negative — the inverted
fresh-cross pattern).

**IS park numbers (context):** N=754, EVΔ +0.047, WRΔ +4.8pp, z_raw +2.91, t_ctl +3.25,
P_pooled 1.00, Psign 0.85 (2021 negative), replicates=True on the full-universe mirror. Failed
the lattice bar on two legs (z<3, Psign<0.90); one story out of 349 cells. Note: the lattice's
z_raw was a plain one-proportion z — NOT date-clustered (the machinery gap peak_fakeout later
closed).

**[BAR SET 2026-07-17 — no numeric bar existed at park; set now, pre-peek, before any post-cutoff
row of this feature has been computed. This IS the December pre-registration:]**
- Definition FROZEN: cdays = trading bars since the last SMA8/SMA21 flip (either direction),
  capped 60, per `experiments/trend_ma_lattice/features.py` unchanged; cell = bucket 16-30;
  substrate = the funded 75+ CALL ledger of the THEN-ACTIVE version. **"Then-active" is pinned to
  a single instant (adversarial closure): the version `trader algorithm active` resolves on
  2026-12-15**, recorded in the run log BEFORE any PL driver runs; later ships are ignored for
  this read (no substrate shopping in the eval→driver gap).
- OOS slice: entry date in (2026-06-15, sealed upper bound per common rule 7], outcomes resolved.
- **Baselines pinned (adversarial closure):** EVΔ = (cell mean apex-EV) − (mean apex-EV of the
  FULL OOS-slice ledger, cell-INCLUSIVE) and WRΔ likewise — i.e. the trend_ma_lattice harness's
  own `ev_cell − base_ev_mean` semantics computed on the OOS slice ONLY. Never a pooled-IS base,
  never a cell-exclusive base, never a hand-rolled variant. The driver prints N_cell, N_substrate,
  both means, and the z inputs so the arithmetic is recomputable by hand.
- **PASS ("holds sign and magnitude") iff ALL of:** (a) OOS EVΔ ≥ +0.02 (≈ half the IS magnitude
  — "magnitude holds"); (b) OOS WRΔ > 0; (c) date-clustered z ≥ 1.0 on the win outcome, computed
  with the CR1 sandwich machinery from `experiments/peak_fakeout/mine.py` (clusters = entry date,
  common rule 11's assertion required — deliberately STRICTER machinery than the lattice's naive
  binomial z); (d) OOS cell N ≥ 50 (expected ≈ 64 at ~6 months' accrual).
- **FAIL (close the cross-freshness axis permanently) iff:** OOS EVΔ < 0 AND OOS WRΔ < 0, with
  the SAME win-outcome CR1 |z| from leg (c) ≥ 1 at N ≥ 50 (a resolved sign-flip on both
  outcomes; there is no separate EV-z — adversarial closure of that ambiguity).
- **Otherwise (N < 50, or same-sign sub-bar, or mixed signs):** INCONCLUSIVE — one extension to
  2027-06-15, same bar; a second inconclusive → close (mirror of PL-1 S2).
- Power note (disclosed): expected OOS z under a fully-real effect ≈ 0.85 at N≈64 — the z≥1 rung
  is ~45% sensitive. Proportionate to the low-stakes consequence (a PASS licenses one Stage-1
  look, not a ship); the 2027-06 extension doubles N.
- Secondary, REPORT-ONLY: the fresh-cross bucket (0-5 bars) sign — the inverted fresh-cross
  pattern — reported alongside, never adjudicated.
- **PASS consequence is exactly the source lock's:** cross-freshness "earns ONE re-read on the
  then-active substrate as a candidate feature" — a fresh Stage-1 W1-W6 investigation of THIS
  cell only; not a ship, not a presumption, and NOT a re-run of the 349-cell lattice (the lattice
  NULL stands regardless of this outcome).

**Runner:**
```
# (queue) PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u experiments/trend_ma_lattice/mine.py
#   with the ledger window extended through eval-date (common rules 1-3);
# Adjudication driver: bucket b_cdays_SMA_8_21 on the OOS slice; CR1 z via the peak_fakeout
# machinery; EVΔ/WRΔ vs the OOS-slice substrate mean.
```

**Traps forwarded:** `infer_schema_length=None` on young listings; polars NaN-is-not-null choke
point; finite-mask regressors (finX) before any controlled fit — the silent false-NULL trap; the
harness auto-resolves the ACTIVE version at runtime (here per-lock, but RECORD it); 16-30 is
TRADING BARS, not calendar days; the CR1 machinery is COPIED not imported across experiment dirs
(sibling `import features` sys.modules collision — same trap as PL-1/PL-2); queue the ledger
build.

---

### PL-4 — ABSORPTION/CLIMAX intraday transition family, Layer-B (parked 2026-07-10)

**Source of truth:** `experiments/miss_regime_fakeout/DESIGN.md` §5 (bars), §6.1 (clustering), A3
(kill criterion), A5 (dual split), §4/§9 (unlock + layers); `VERDICT.md` (Layer-A close + Layer-B
park). Mirrored thinly in §7 of this file; this entry governs execution.

**Hypothesis (one sentence):** intraday score fakeouts (≥15-pt same-day swing at <3% price move)
that attribute to the VOLUME stage with a reversal-class endpoint (ABSORPTION/CLIMAX
classification — whose same-day blend the shipped `INTRADAY_TYPE_CONF_GATE` does NOT cover) form
a mineable family; a candidate guard is judged on fakeout-count reduction (WCF precedent),
per-trade WR neutrality acceptable.

**Locked bar (verbatim, DESIGN.md §5):**
> candidate advances only at cohort z >= 3 with N >= 500 AND replication on a disjoint date-half;
> fakeout-reduction measured on intraday-swing metrics (per-trade WR neutrality is acceptable —
> WCF precedent shipped on fakeout reduction alone).

Clustering: every gate-qualifying z clusters by `date` (cluster-sandwich OLS, cohort-indicator
t_clust). **N ≥ 500 = joined LEDGER rows in the candidate cohort**, not raw fakeout events. Dual
split: THE replication bar = interleaved calendar-year halves (even vs odd years); the contiguous
era split (2016-21 vs 2022-26) is REPORT-ONLY (flags ERA-LOCAL). **"No bar renegotiation at
unlock time"** (DESIGN.md §4). The A3 whole-pass kill criterion and its application remain
FABLE's judgment — the executor scripts report mechanically and explicitly disclaim that
authority (`write_mining_results.py` docstring).

**Unlock condition (DESIGN.md §4):** ONLY after (a) the Dec-2026 OOS evaluation (sections 0-5 of
this file) COMPLETES, and (b) the cutoff RE-LOCKS FORWARD — (b) is a FABLE/user decision
(holdout-lock changes are user-gated). **If the cutoff is not re-locked forward, Layer-B stays
parked** — `phase0_attribution.py` hard-asserts max_d ≤ HOLDOUT_CUTOFF, so the code physically
blocks early runs; do not defeat that assert.

**[SCAFFOLDING 2026-07-17]:**
- S1. Ordering: LAST §11 item to run (with PL-5) — strictly after `run_oos_eval.py` verdicts are
  recorded AND the new cutoff is committed to `strategy_config.py` (with the scoring-lock
  re-capture that a `SCORING`-adjacent constant change entails — see traps.md "Scoring-lock
  cutoff drift").
- S2. Event-count precondition: BEFORE mining, report the unlocked v74 intraday window's
  fakeout-event count (Phase-0 SQL: (symbol,date,version) groups, ≥2 snapshots, spread ≥15,
  |price_move| < 3%). 46 events @ 2026-07-10 growing ~2.5/trading day → expect ~300-350 by
  mid-December. Events power the ATTRIBUTION read; the z≥3 bar runs on cohort LEDGER rows. **If
  volume-stage reversal-class events number < 30, record the attribution distribution and STOP**
  — the family re-parks to the next re-lock horizon (too thin to mine without bar-shopping
  temptation).
- S3. **Scope of the December read, pinned (adversarial closure — the single most important rule
  in this entry):** the licensed re-read is EXACTLY what DESIGN.md §4 names — **Phase-0
  dominant-stage attribution + the §3f WCF-precedent swing metric on the newly in-sample intraday
  window. Nothing else.** The Layer-A EOD-proxy mine (`build_ledger.py` + `mine_candidates.py` +
  `write_mining_results.py`, candidates 1-3 on the 2016-2026 EOD ledger) is **PERMANENTLY CLOSED
  (A3 fired 2026-07-10) and must NOT be re-run** — re-running it with 6 more months appended is
  re-litigating a closed NULL, and its documented near-miss (global max |t| 2.55 across ~30
  tests) makes that the classic noise-crossing route to a fake PASS. The December deliverable is
  a REPORT (attribution distribution + swing-metric counts at the locked definitions).
  **Candidate ADVANCEMENT is not a December-executor decision at all:** any advancement requires
  the full locked bar (z≥3 clustered, N≥500 cohort ledger rows, disjoint-date-half replication) —
  and how the date-half replication applies to an intraday-window cohort is reserved to FABLE
  (the A5 interleaved-year definition was written for the multi-year Layer-A frame and does not
  mechanically transfer to a ~6-month window; an executor may not improvise a substitute split).
- S3a. **The §3f swing metric has NO existing script (adversarial finding):** it must be freshly
  implemented from §3f's definition — four counts per variant vs production: affected groups
  swing ≥10, affected mean swing, all groups swing ≥15, all groups swing ≥20 — and **self-tested
  by reproducing the archived v72 WCF-ship numbers (affected groups 43→17 at swing ≥10;
  all-symbol ≥20-pt swings 18→7) on the pre-cutoff window BEFORE any post-unlock number is
  trusted.** A swing-metric result from an implementation that has not passed that self-test is
  void.
- S3b. **Attribution machinery pinned:** `intraday_diagnostics.attribute_swing` runs AS-IS
  (unmodified production module at the run's HEAD); any local edit to its stage logic or
  tie-breaks voids the read. The Phase-0 run must RECORD n_events, n_vol (volume-dominant), and
  n_rev (reversal-class endpoint) in the results file — the S2 <30 STOP is procedural, and these
  recorded counts are its audit trail.
- S4. v74's intraday logs start at its ship date (2026-06-15), so the unlocked window is pure-v74
  by construction. If v75+ ships before December, v75 events are a SEPARATE population: report
  both counts, analyze only v74's (the locked design's substrate) unless FABLE amends via §6.

**Runner (December scope only — Layer-A mining scripts deliberately absent):**
```
PYTHONUTF8=1 python experiments/miss_regime_fakeout/phase0_attribution.py   # after re-lock only
# + the fresh §3f swing-metric pass per S3a (new script, self-tested vs the archived v72 numbers)
# FORBIDDEN for this read: build_ledger.py / mine_candidates.py / write_mining_results.py
#   (Layer-A EOD-proxy machinery — axis closed permanently 2026-07-10).
```

**PASS-shaped outcome:** the report shows a live, attributable reversal-class family → FABLE
decides whether/how a candidate test at the locked bars (z≥3 / N≥500 / replication per FABLE's
ruling on the split) proceeds, routing any eventual candidate through Stage-1 W1-W6 or the
Stage-1-N neutrality track (the WCF/v72 path): evidence memo, gate run, verdict wait; NO
auto-ship. **FAIL-shaped outcome:** the family is thin/attribution-dead → whether the whole
Layer-B axis closes permanently is reserved to FABLE (A3 whole-pass judgment). **BLOCKED (no
re-lock):** stays parked, nothing spent.

**Traps forwarded:** the family is observable ONLY in live `score_intraday_logs` (bit-identical at
EOD — not reconstructable from historical Score rows); retired-dampener attributions (72/90
pre-cutoff events = the dead WCF stage) stay EXCLUDED from the live-mechanism ranking; the holdout
asserts are load-bearing — never bypassed for this lead; queue the ledger build; the scripts have
NO argparse (hardcoded to the active version) — verify v74 resolution before running.

---

### PL-5 — gamma+IV per-trade gate: M3 SL-FNR re-read (parked 2026-07-13; watch item, non-gating)

**Source of truth:** `experiments/iv_engine_pertrade/VERDICT.md` ruling 4 (the December watch
item) + rulings 1-3 (park + reopen conditions); bars defined in
`experiments/iv_engine_pertrade/DESIGN.md` (M1/M2/M3).

**Hypothesis (one sentence):** the F2+gamma engine's SL-hit false-negative rate (missed real SLs =
hidden risk) is not worse than the production RV+const-delta engine's — currently point-worse
(0.212 vs 0.152, t=+1.39) but UNDERPOWERED at N_real_sl=33.

**Locked terms (verbatim):**
- The M3 bar (DESIGN.md): "F2+gamma TP-hit classification agreement vs the real path >=
  production's, AND its SL-hit false-negative rate is not worse (missed real SLs = hidden risk)"
  — operationalized on the liquid-primary stratum (volume≥5), `ripe15`-gated, date-clustered,
  MIN_CELL_N=30. Current: TP-agreement 0.830 vs 0.811 (noninferior ✓); SL-FNR diff +0.0606,
  se 0.0437, t=+1.39, N=33, G=24.
- The trigger (VERDICT.md ruling 4): "re-read M3 SL-FNR when real-SL event count ~doubles
  (~Dec-2026); ledger machinery exists, one queue job + one metrics pass."
- The park (rulings 1+3, unconditional): "PARKED: GAMMA_AWARE + IV_MODEL stay default-OFF,
  A/B-only"; reopen = "NEW DATA CLASS ONLY, never another form iteration on this panel/ledger:
  (a) real-fill capture from the P3.7 slippage loop (true premiums paid), (b) a mid-price/quote
  source (replacing lastPrice-derived costs), (c) P1.4 vega-state shipping (fixes the earnings
  regime and re-frames d15)." An M3 re-read is NOT a reopen path.

**[SCAFFOLDING 2026-07-17]:**
- S1. **The count cannot double without the cutoff moving** (the extraction's key mechanical
  finding): M1-M3 read only `signal_date <= CUTOFF`, and every in-sample signal is already ripe —
  a December re-run against the frozen 2026-06-15 cutoff reproduces N_real_sl=33 exactly.
  **Therefore PL-5 runs AFTER the post-eval re-lock** (same dependency as PL-4). If the re-lock
  is deferred: PL-5 stays parked — do NOT hand-widen the window or read `is_shadow_oos` rows into
  M1-M3 (the design locks those as "watch-only, never used in M1-M3").
- S2. Run trigger, pinned: proceed when the **liquid-primary (`volume>=5`, ripe15) real-SL count,
  measured from the REBUILT ledger under the new cutoff**, is ≥ 60 (expected ≈ 66 if the cutoff
  moves to ~2026-12-15). Below 60: record the count, defer to the next natural read (2027-06),
  nothing spent. **Stratum gaming closed (adversarial):** the all-inclusive stratum
  (N_real_sl=98 as of July, already ≥60 today) satisfies NEITHER the trigger NOR any verdict —
  the trigger is liquid-primary-only, it is only computable post-rebuild, and the write-up must
  quote BOTH strata's counts side-by-side with an explicit "trigger satisfied by: liquid-primary
  (rebuilt ledger)" line. Citing the July all-inclusive pair (t=+2.04, N=98) as a shortcut
  "second failed bar" without the rebuild is void.
- S3. The ledger REBUILD is mandatory (not just the metrics pass): the parquet persists only
  d5/d10/d15 checkpoints, so M3's day-by-day first-touch walk exists only at build time —
  `build_ledger.py` must re-run in full (queue, db=heavy) so `real_sl_hit` regenerates for old +
  new rows. **The metrics pass is `compute_metrics.py` ONLY — never
  `compute_metrics_refined.py`** (adversarial finding: the refined sibling does not recompute M3;
  it re-opens the July `pertrade_results.json` and carries the stale N=33 numbers forward with no
  freshness check).
- S4. Adjudication is NON-GATING by construction: a PASS resolves M3 from UNDERPOWERED to clean
  (feeds ruling 2's per-trade evidence map); a FAIL adds a second failed bar. Neither changes the
  park (M1 is the blocking finding; the single pre-registered refinement round is SPENT — no
  refits against this validation set in either case).

**Runner (verbatim from DESIGN.md, dedup key included):**
```
trader queue submit --priority high --db heavy --cpu 2 --restartable \
  --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  --dedup iv_engine_pertrade_ledger \
  --reason "M3 SL-FNR December re-read: ledger rebuild under re-locked cutoff" \
  -- python experiments/iv_engine_pertrade/build_ledger.py
# then (foreground, seconds): python experiments/iv_engine_pertrade/compute_metrics.py
```

**PASS action:** record M3=PASS in the evidence map; pair stays PARKED. **FAIL action:** record
M3=FAIL (second failed bar); pair stays PARKED. **Either way:** reopen remains gated exclusively
on the new-data-class conditions (a)/(b)/(c) — by December the most likely live one is (a): the
P3.7 real-fill log crossing N≥30-50 fills.

**Traps forwarded:** ~76-81% of `option_prices` quotes are zero-volume with stale lastPrice —
liquid stratum (volume≥5) is the only honest read; the calm-name panel-IV vs real-contract gap is
the standing M1 blocker (do not "fix" it by refitting — the refinement round is spent); Polygon is
NOT needed (the panel is harvested; forward work runs on our own daily pulls); watch for the
COVERAGE-BLOCKED verdict class (gamma_iv_phaseb precedent) if dose collapses on the extended
window.

---

### PL-6 — OSK re-reads: forward_ranks (~Oct/~Jan) + Stage-3 tilt (~Jan) (parked 2026-07-08/10)

**Source of truth:** `experiments/osk_tilt/DESIGN.md` (the locked STAGE bar) + `RESULTS.md` (park
+ revisit language); `experiments/era_conditioning/DESIGN.md` Phase B (forward_ranks re-read
spec); `experiments/osk_era/STAGE1_VERDICT.md` (modifier BLOCK, permanent);
`experiments/osk_validation/VERDICT.md` (L3-buy kill). **Not a December-15 item** — tracked on
its own cadence; §11 records it so December neither spends nor forgets it.

**Two mechanically distinct re-reads (do not conflate their N's):**

**(a) forward_ranks re-read** — `era_conditioning/DESIGN.md` Phase B (verbatim): "pre-registered
re-reads at N ≥ 60 forward trading days (~2026-10) and N ≥ 120 (~2027-01): do the
2025-26-confirmed cells hold on data that postdates this design?" Artifact:
`.cache/era_conditioning/forward_ranks.parquet` (append-only; `FORWARD_START=2026-06-16`;
own-panel MySQL `option_prices` only — no Polygon dependency). NON-GATING (references.json:
`"gating": false`); descriptive report to FABLE; no numeric bar was stated and none is added here
— it feeds judgment, not a gate. **Scope pinned (adversarial closure): "the 2025-26-confirmed
cells" = exactly TWO — Cell A (OSK skew rank effect) and Cell B (pinning/local-density effect),
the Phase-A RAMP/FLAT_DECAY reads. Cell C (cohort-timing / GEX-adjacent) was
INSUFFICIENT_DATA — never confirmed — and is EXCLUDED: the GEX axis is closed permanently, and
folding Cell C's noisy trajectory into a "holds" narrative is the named gaming vector. The
report gives signed effect size + t_clust per cell — numbers, not prose verdicts.**
- **[SCAFFOLDING 2026-07-17]:** the parquet is STALE (13 days, last row 2026-07-07) and has no
  scheduled catch-up — the "wire an off-market queue invocation" chip was never installed. An
  operator must queue `python experiments/era_conditioning/build_forward_ranks.py` (idempotent
  catch-up, queue-only for the full run) periodically — install a weekly off-market queue
  submission on the new box (mirror the `TraderSkillOOSCacheRefresh` pattern) so N≥60 is actually
  banked by ~Oct. Without this, the Oct re-read silently has no data.

**(b) Stage-3 tilt re-read** — locked bar (verbatim, osk_tilt/DESIGN.md): "STAGE requires: ≥2
variants (at least one of the two lag1 variants) with (i) DD not worse than baseline
(`worst_dd_pct <= baseline`), AND (ii) compound ≥ baseline, AND (iii) clustered t ≥ 2 on the
sizing-weighted uplift. Anything less is PARK." Current best t_clust = 1.06 (resid_lag0_g030) at
eligible-N 69; "the locked bars stay as-is" (RESULTS.md). Outcomes are capped: "STAGE or PARK —
never SHIP" (the ~1.4y option panel structurally cannot reach the N=500×8 T1-T7 gate).
- **[SCAFFOLDING 2026-07-17] — timing resolution:** RESULTS.md names "~2026-10" as the revisit
  alongside the forward_ranks cadence but asserts the eligible-N doubling "by the Jan re-read."
  Mechanically, eligible-N CANNOT grow before the December re-lock: the tilt ledger
  (`osk_era_ledger_65.parquet`) is a fixed-window snapshot ending at the 2026-06-15 cutoff, all
  its entries are already ripe, and gameplan's anti-goal bars touching `CALIBRATION_CUTOFF_DATE`
  before the December read. **Therefore: the tilt re-read is scheduled ~Jan-2027, AFTER the
  Dec-2026 re-lock**, when rebuilding the ledger under the new cutoff extends the window ~6
  months (expected eligible-N ≈ 130-140). An October tilt run would trivially re-PARK at n≈69 —
  do not spend it. October touches only (a).
- **Rebuild + window mechanics, fully pinned (adversarial closures — two CRITICAL constants):**
  1. `build_osk_ledger.py` hard-asserts the worktree `Trader-exp-osk-era`, which was REMOVED at
     closeout — recreate it first (`git worktree add ../Trader-exp-osk-era`), do NOT edit the
     assert away; pin `sys.path` + assert module `__file__` per the worktree PYTHONPATH trap.
  2. **BOTH scripts hardcode the study window and MUST be re-pointed or the January run silently
     reproduces July:** `build_osk_ledger.py` filters on its `WIN_START..WIN_END` constants, and
     `tilt_runner.py` separately hardcodes `WIN_END = date(2026, 6, 15)` (fed into
     `_OSK_STATE["window_end"]`, which discards every outcome after it before sizing — running it
     verbatim in January reproduces n_elig≈69 / t≈1.06 exactly, a guaranteed false PARK). The
     ONLY sanctioned edit: change the two `WIN_END` constants to the NEW post-re-lock
     `CALIBRATION_CUTOFF_DATE`. Nothing else in either file may be touched. The write-up must
     quote the script-printed `window=` lines proving the extended window ran.
  3. **Ledger rebuild invocations, verbatim** (the `_65` suffix = `--score-min 65`, differing
     from the file's default 70; both lag files are needed; then re-fit the z-maps):
```
python experiments/osk_era/build_osk_ledger.py --score-min 65                # lag-0 ledger
python experiments/osk_era/build_osk_ledger.py --score-min 65 --lag-days 1   # lag-1 ledger
python experiments/osk_tilt/build_osk_zmaps.py                               # z-map re-fit (local parquet, no MySQL)
```
     (queue the two ledger builds — MySQL `option_prices` heavy; z-maps is foreground-safe).
  4. Then the queued tilt run — **the five locked variants + baseline ONLY; DESIGN.md's
     hard-stops clause restated verbatim: "No variant expansion... no softened/partial bar, no
     new variants — this file is the ceiling on scope."** No new g values, no min_score changes:
```
trader queue submit --priority normal --db heavy --cpu 2 \
  --dedup osk_tilt_v1 --reason "OSK Stage-3 tilt Jan-2027 re-read (n_elig ~doubled)" \
  --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 \
  -- python experiments/osk_tilt/tilt_runner.py
```

**PASS action (tilt):** verdict = STAGE per the locked bar — staged infrastructure + evidence
memo; NEVER auto-ship (and the score-modifier form stays BLOCKED permanently per
STAGE1_VERDICT.md regardless). **FAIL action:** PARK again; the same locked bars repeat at the
next natural cadence. Neither outcome touches the OSK closures already on record (modifier
BLOCKED; L3 buy OFF; per-trade residual only).

**Traps forwarded:** the two re-reads' N's come from different tables/queries — one never
satisfies the other; `osk_era_ledger_65.parquet` is a static snapshot, not append-only;
forward_ranks staleness (S-item above); Polygon subscription is irrelevant to both (own-panel
only) — its ~Aug-6 cancellation decision does not gate OSK; worktree PYTHONPATH pin+assert
applies to the recreated worktree.

---

### 11.A — Adversarial-pass record (2026-07-17)

Three independent red-team passes (each prompted as a motivated December executor trying to game,
dodge, or shirk) ran against the committed first draft (`6cc4f3b85`). Every hole found, and where
it was closed — kept here per the authoring brief so December knows these doors were checked:

| # | Hole (as found) | Closed at |
|---|---|---|
| A1 | PL-1 leg 1 ambiguous: one-cell-clears could be read as PASS | PL-1 S1a (BOTH cells, IS-direction signs) |
| A2 | Pooled d_ev gameable: frozen IS base + refreshed cell mean | PL-1 S1b (single concatenated frame, unmodified logic) |
| A3 | Tercile cut-points existed nowhere as numbers; December rebuild OVERWRITES the source parquets | `parked_leads_frozen.json` + hashed snapshots + preamble rule 9 |
| A4 | Cluster key not pinned — driver could cluster by (symbol,week) for tighter SEs | Preamble rule 11 (entry-date assertion + G printed) |
| A5 | Rolling OOS tail — re-run two weeks later for a second look, report only the pass | Preamble rule 7 (sealed slice) + rule 8 / §12.6 (append-only attempt logs) |
| A6 | PL-2 leg-4 comparator undefined for the OR-union — smallest single-feature marginal could be substituted | PL-2 "Leg-4 comparator, pinned" |
| A7 | HOLDOUT_DISABLE process-global leak | Preamble rule 10 (per-subprocess only) |
| A8 | INSUFFICIENT_N as a low-scrutiny escape | PL-2 S1 (mandatory row-count trail) |
| A9 | Whitespace/emphasis drift in "verbatim" quotes could weaponize the STOP rule | Quotes made byte-faithful + preamble rule 12 (substance threshold) |
| B1 | **PL-4 runner listed the Layer-A mining scripts — re-litigating a permanently-closed axis (max \|t\| 2.55 one push from z≥3)** | PL-4 S3 rewrite + FORBIDDEN line in the runner |
| B2 | §3f swing metric has no implementing script | PL-4 S3a (fresh implementation + self-test vs archived v72 numbers 43→17 / 18→7) |
| B3 | S2 <30 STOP had no enforcement; attribute_swing tie-breaks editable | PL-4 S3b (counts recorded; module pinned unmodified) |
| B4 | H-before-PL ordering purely textual | Preamble rule 8 (eval-artifact sha embedded in every PL results file) |
| B5 | Quiet re-lock deferral dodges PL-4/PL-5 while looking compliant | §12.3 (recorded ask-and-answer) |
| B6 | PL-3 baseline ambiguous (inclusive/exclusive, OOS/pooled); driver unpinned | PL-3 "Baselines pinned" (OOS-only, cell-inclusive, harness semantics, printed inputs) |
| B7 | PL-3 FAIL-leg z undefined (no EV-z exists) | PL-3 FAIL leg (same win-outcome z + concurrent WRΔ<0) |
| B8 | "Then-active" version timestamp unpinned — substrate shopping in the eval→driver gap | PL-3 (pinned to `trader algorithm active` on 2026-12-15) |
| C1 | **`tilt_runner.py` hardcodes WIN_END=2026-06-15 — a verbatim January run reproduces July exactly (guaranteed false PARK)** | PL-6(b) mechanics item 2 (both WIN_END constants re-pointed; window= lines quoted) |
| C2 | Ledger rebuild flags unpinned (score-min/lag drift; z-maps omitted) | PL-6(b) mechanics item 3 (verbatim invocations, `--score-min 65`, both lags, z-map re-fit) |
| C3 | PL-5 trigger gameable via all-inclusive stratum (N=98 clears 60 today, pre-rebuild) | PL-5 S2 (liquid-primary-only, post-rebuild, both strata quoted) |
| C4 | `compute_metrics_refined.py` silently carries stale July M3 forward | PL-5 S3 (compute_metrics.py ONLY) |
| C5 | PL-6(a) "confirmed cells" unenumerated — Cell C (GEX-adjacent) foldable into a "holds" narrative | PL-6(a) scope pin (Cells A+B only; numbers not prose) |
| C6 | Tilt hard-stops (no variant expansion) not restated locally | PL-6(b) mechanics item 4 (verbatim restatement) |
| C7 | File-drawer: multiple runs, favorable one written up | Preamble rule 8 + §12.6 (append-only, all attempts cited) |

Residual disclosures (found, deliberately NOT changed): PL-1's locked close condition carries
~40-50% false-close probability under a true effect at December power (disclosed in S3; bars
fixed at park); leg 2's pooled floor is nearly IS-determined (S4; by design); PL-3's freshly-set
z≥1 rung is ~45% sensitive (disclosed; proportionate to its low-stakes consequence).

---

## 12. December Executor Guide

**Who runs this:** any competent agent session — Sonnet/Opus-class is sufficient for every
mechanical step in this file (cache rebuilds, runners, metrics passes, descriptive reports, and
applying locked bars to outputs). What is NOT executor work: adjudicating an H1 BLOCK escalation,
the cutoff re-lock decision (user-gated), A3 whole-pass judgments, PASS-consequence licensing
(starting a Stage-1 investigation or Stage-3 probe), and ANY amendment — those route to
FABLE/user.

**Boot order:** `/onboard` → this file top-to-bottom (§0 first, then §11/§12) →
`references.json`. Do not load prior experiment SUMMARY files beyond what a PL entry cites — the
locks are self-contained by design.

**THE RULE (absolute):** **no bar, threshold, N floor, window definition, or consequence mapping
in this file or in a cited source lock may be modified by an executor. A modified bar voids the
OOS read** — the evaluation is then unregistered and worthless (the exact failure this package
exists to prevent). If a bar cannot be evaluated as written, the lead's own deferral clause
(INSUFFICIENT_N / INCONCLUSIVE / BLOCKED) applies; if none fits, record the obstacle verbatim,
STOP that lead, and surface it to FABLE/user. Amendments happen only via §6, logged BEFORE any
amended run.

**Master run order (Dec-15, or first session on/after it):**
1. **Pre-steps** (§0.2): final skill-cache rebuild through the eval date (queue); marking
   verification (`experiments/portfolio_engine_parity/validate.py` → `--marking-verified`); H3
   envelope extension (`run_h3_envelope.py --win-end <eval-date>`, queue); piecewise-segment
   check if the live profile changed since 2026-07-13.
2. **The eval:** `python experiments/holdout_oos_2026_12/run_oos_eval.py --marking-verified` →
   H1-H6 verdicts recorded to `results/`.
3. **FABLE/user checkpoint:** adjudicate H1-H6 per §4/§5; decide the forward re-lock (user-gated
   holdout change; remember the scoring-lock re-capture + same-commit rule when
   `CALIBRATION_CUTOFF_DATE` moves — traps.md "Scoring-lock cutoff drift"). **The re-lock ask is
   RECORDED (adversarial closure):** the executor logs a timestamped, explicit ask-and-answer (or
   ask-and-no-answer) in the write-up. A Path-B claim ("re-lock deferred") without that recorded
   ask is invalid — quiet deferral is not a sanctioned way to skip PL-4/PL-5.
4. **Parked leads, path A (cutoff re-locked forward):** rebuild the substrate chain through the
   eval date (queue; no holdout bypass needed) → run **PL-1**, **PL-2**, **PL-3** adjudication
   drivers on the FIXED OOS slice (2026-06-15, eval-date] → then **PL-4** (Phase-0 + mine at
   locked bars) → then **PL-5** (ledger rebuild + metrics pass).
   **Path B (re-lock deferred):** PL-1/2/3 run via `HOLDOUT_DISABLE=1` + explicit slice
   assertions (§11 common rule 2); **PL-4 and PL-5 stay PARKED** (both are mechanically blocked
   without the re-lock — documented in their entries).
5. **PL-6 is NOT a December item.** December's only PL-6 action: verify
   `forward_ranks.parquet` freshness (the ~Oct N≥60 read should already be banked) and confirm
   the ~Jan-2027 tilt re-read is scheduled. Do not spend the tilt read early (n_elig cannot have
   grown before the re-lock).
6. **Write-up:** one results file per PL lead in this directory
   (`results/PL<N>_<slug>_<date>.md`) — verdict per the locked outcomes, the numbers, the runner
   invocations used, and any BLOCKED/INSUFFICIENT records — **plus the §11 common-rule-8 audit
   block: the eval artifact's filename+sha256, ALL attempts from `results/PL_attempt_log.md`
   (append-only, every invocation incl. dry runs), queue task ids, input-cache paths with
   mtimes/sha256s, recorded version ids, the frozen cut-points used, and any script-printed
   window lines. Results files are append-only per lead — a second attempt appends under a new
   dated heading, never overwrites (file-drawer closure).** FABLE/user consume these for the
   licensing decisions.

**Expected wall-clock (old-box baselines; the 9950X3D should be faster):** pre-steps ~1-2h queued;
the eval itself minutes; substrate-chain rebuild ~1-3h queued; PL-1/2/3 drivers minutes each;
PL-4 ledger build ~1-2h queued; PL-5 `build_ledger.py` a multi-hour queued job (~7,800 MySQL
queries against ~90M-row `option_prices`). One focused day, with the two heavy ledger builds
overnight if needed.

**Standing reminders:** every heavy step through `trader queue submit` with
`--env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1`; never read a result mid-run (mid-assess reads
are races); worktree PYTHONPATH pin+assert wherever a worktree is involved; ASCII-only stdout in
any new driver code.
