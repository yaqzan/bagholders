# v74 Miss × Regime × Fakeout Ledger — DESIGN (pre-registered)

**Status: APPROVED AS AMENDED 2026-07-10 (FABLE).** Amendments A1-A8 below are part of the pre-registration and were applied to this file BEFORE any mining compute ran.

**Author:** RECON+DESIGN agent (Track 2) · **Date:** 2026-07-10 · **Scope:** new files only under `experiments/miss_regime_fakeout/`; no scoring/engine edits; nothing committed (FABLE commits).

---

## AMENDMENTS (FABLE, 2026-07-10) — binding, applied before execution

**A1 — Phase 0 is HOLDOUT-RESTRICTED.** Phase 0 uses pre-cutoff rows ONLY (date ≤ 2026-06-15) and versions **v69+** (post-weekly-blend era; v60 rows excluded — pre-blend, different whiplash regime). Tally fakeout events by dominant-stage attribution × version; report **retired-dampener-stage attributions separately and EXCLUDE them from the live-mechanism ranking** (they cannot fire on v74). The post-cutoff tally (the 46 v74 events §3e labels UNUSABLE-FOR-SELECTION) is **FORBIDDEN until the Dec-2026 OOS unlock** — the drafted Phase-0 use ("informs whether candidate 1 or 3 is prioritized") was a selection use and is retracted. **Phase 0 does NOT gate Phase 1** — all three licensed candidates get mined regardless; Phase 0 only orders the narrative.

**A2 — Licensing.** LICENSED THIS PASS: **candidates 1-3 only.** Candidates 4 and 5 are NOT licensed — recorded as conditional follow-ups (only if 1-3 mine positive AND consolidation demands), per their own collision flags in §8.

**A3 — Whole-pass kill criterion, pre-registered verbatim:**

> If no candidate among 1-3 clears z>=3 with N>=500 on BOTH replication halves (date-clustered), the residual-fakeout supply-quality axis closes NULL — no same-pass feature extensions, no re-cuts. Context for the close: the v74 residual fakeout event rate is 0.26-0.35% of (symbol,date) groups, far below the 5% actionable floor that closed Priorities #7/#9.

Positives, if any, **stop at the evidence memo** — FABLE arranges independent adversarial verification before anything is called real; gates after that.

**A4 — §6.1 correction.** The draft's claim "no prior instance of date-clustering in this repo" is **WRONG**: `experiments/gex/gex_test.py` implements date-clustered sandwich SEs (`ols_with_clustered_se`, numpy-only, self-tested; also used throughout `experiments/osk_validation/`). The draft's search covered FINDINGS.md files; those experiments use VERDICT.md. §6.1 is corrected in place and this pass **REUSES the `gex_test.py` clustering implementation** (cluster-sandwich OLS on a cohort indicator) rather than reinventing.

**A5 — Dual split, both mandatory.** (a) The interleaved-year halves = **THE replication bar** (regime-balanced, as designed in §9 Phase 4). (b) ADDITIONALLY report a **contiguous era split (2016-2021 vs 2022-2026)** + the per-year sign table as an **ERA-ROBUSTNESS report line**. Rationale: interleaved halves both contain post-2022 rows, so a post-2022-only (era-local) effect passes both halves — the exact OSK failure mode (edge present 2025-26, absent 2022-24). The era split is report-only, not a bar, but an effect that exists only in one era gets flagged **ERA-LOCAL** in the results and FABLE will treat it as OSK-class.

**A6 — Holdout guard enforced in code.** The Phase-1 ledger build MUST call `experiments._holdout.assert_no_holdout_leak` on the built frame (enforced in code, not by convention). Never `HOLDOUT_DISABLE`.

**A7 — Candidate-2 SPREAD_TILT literal-overlap pre-check is MANDATORY before mining c2 cells** (was open item 3): if the 68-77 admission-zone cohort is substantially the same rows SPREAD_TILT already tilts (report the overlap %), c2's definition must carve out the already-covered region or c2 dies as duplication.

**A8 — Queue discipline.** Phase 1 goes through `trader queue submit`. Market hours at execution time → `--priority normal` (below scheduled) `--db heavy --restartable --dedup miss_regime_fakeout_ledger --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1`; do NOT use `high` without `--window off_market`. Phases 2-3 foreground only if they read an existing parquet in <1 min; otherwise queue them too. Phase 0 foreground is fine (light aggregates).

---

## 0. Mission restated

Design (not execute) a **SUPPLY-QUALITY lever hunt**: ranked dampener/guard candidates for fakeout-prone signal cohorts, in the WCF lineage (WCF/CWCF/CSWC/CWWD/SCW/the v72 score-gate ramp) — i.e. **conditions on the signal/symbol/score-path itself**, possibly interacted with regime, **never** book-level exposure scaling off a market indicator alone. Explicitly **NOT**:
- generic WR re-mining (re-deriving a Score.overall gradient shaper — CLOSED, see §3 NULL-CHECK row "Score.overall re-shaping"), or
- a 6th market-context DD-sizing lever (that well is documented DRY after RXDD/SVR/MWDD/TVDD/BDIV — see `.claude/docs/traps.md` §4 "A DD-sizing lever needs low-EV AND high-DD-concentration to COINCIDE on an orthogonal, crash-robust cohort").

Target: whiplash/fakeout **Priorities #7 and #9** in `.claude/docs/known-issues.md`, quoted verbatim below.

---

## 1. Priorities #7 and #9 — verbatim, current status

Both are **CLOSED 2026-06-16** for their *original* target class (the weekly-transition/COHR-VICR whiplash). This is the single most important precondition for this design: **the low-hanging fakeout fruit is already picked.** What remains is a *residual* hunt, and the substrate inventory in §2 shows the residual is real but the evidence window is thin — this design is built around that thinness, not in spite of it.

> ### 7. [CLOSED 2026-06-16 — tamed by the v69 blend; closes with #9] Weekly-Adjustment Volatility Causes Day-Over-Day Score Whiplash
>
> **CLOSE (2026-06-16, `experiments/weekly_flip_probe/`):** The flip-rate probe on v74
> recalc'd scores (which carry the v69 transition blend) confirms the severe whiplash is
> tamed. The gross 75→{<75} flip rate (58%) is boundary jitter (median day-over-day |Δ|=5)
> + genuine price-driven de-qualification, NOT whiplash. The real score-instability metric —
> the COHR/VICR signature of a large score swing with little price move (|Δ|≥20 & <2% price)
> — is **~3% (full) / 2.6% (recent), below the 5% "no value" floor and DECREASING** (every
> cut is lower in the recent window, consistent with the v69 blend + v72 WCF 27/28-cliff ramp
> + v74 lean). The loose |Δ|≥15 & <2% cut is borderline (7.4%/5.4%) but admits genuine
> 1.5–2% moves on volatile 75+ names, and has **no surviving fix** (replace-weekly-with-slow-
> index is falsified: v42 rolling-weekly disaster, v44 substitution fail). Closed NULL with
> #9. Full numbers + verdict: `experiments/weekly_flip_probe/FINDINGS.md`.

> ### 9. [CLOSED 2026-06-16 — tamed by the v69 transition blend; see #7] Score Stability Gate / Weekly Transition Smoothing
>
> **CLOSE (2026-06-16):** the score-stage weekly transition blend shipped in v69 is the
> "transition smoothing" design this priority called for, and the `experiments/weekly_flip_probe/`
> read confirms the severe day-over-day whiplash is now below the actionable floor and decreasing
> (see #7). The cascade-stage stability-gate alternative is not needed. Closed NULL with #7.

**Implication for this design:** the COHR/VICR *weekly* whiplash family is closed and must not be re-mined (§3 NULL-CHECK). This design targets the **residual** fakeout families — the ones assessment-backtest.md's Intraday Score Audit Log already names, plus one newly-confirmed live family found during this recon pass (§4, §6 candidate 1).

---

## 2. Starting taxonomy — Intraday Score Audit Log, verbatim

From `.claude/docs/assessment-backtest.md` "Intraday Score Audit Log":

> **Three fakeout families observed in the first days of data (2026-05-28/29, active v60):** (1) **weekly transition** — PWR 53→81 and ELF 49→20 driven entirely by `w_adj` flipping partial↔completed (33pt / 14pt weekly gap), price flat; (2) **volume-amp dropout** — AMSC 95→72→89 when the ~+18 volume conviction lift vanished for a single run then recovered; (3) **component oscillation** — BBW bouncing 29↔48↔69 as raw RSI/Stoch flip between intraday data refreshes.

**Caveat inherited from the same doc, load-bearing for this design:** these three families were observed **on v60**, a version with WCF/ICH/CWCF/CSWC/SCW/CONT_BOOST/WVD_WAVE/DAILY_VOLUME_AUTHORITY_WAVE/EARN_BOOST all still live. v73 retired the first five (WCF/ICH/CWCF/CSWC/SCW); v74 retired the last four. **Family (1) is the closed Priority #7/#9 class.** Families (2) and (3) need a fresh v74-native re-check, not a re-citation of the v60 examples — done in §4 below, with one new family surfacing that neither the v60 taxonomy nor the shipped `INTRADAY_TYPE_CONF_GATE` fix covers.

---

## 3. Substrate inventory — numbers, not estimates

All queries below are read-only aggregates run directly (light foreground, each 0.04–17s; no query approached the 30s zombie-query threshold; MySQL via peewee, barrier joins via the DuckDB mirror per house rules). Scripts preserved at `C:\Users\Yaqzan\AppData\Local\Temp\claude\C--Development-Trader\589fa349-b55f-4bda-a53a-4dec7d9983a9\scratchpad\substrate_inventory{,2,3,4,5}.py` for reproduction (scratchpad, not part of this experiment's tree).

### 3a. `score_intraday_logs` — overall shape

- Total rows: **316,369**. Date range: **2026-05-27 → 2026-07-10** (today).
- **Version mix is NOT single-version** — this is the load-bearing surprise of this recon pass:

| version_id | rows | min_date | max_date | distinct_dates |
|---|---:|---|---|---:|
| 60 | 22,311 | 2026-05-27 | 2026-05-29 | 3 |
| 69 | 10,725 | 2026-06-01 | 2026-06-01 | 1 |
| 70 | 61,300 | 2026-06-02 | 2026-06-10 | 7 |
| 71 | 20,470 | 2026-06-10 | 2026-06-11 | 2 |
| 72 | 5,219 | 2026-06-11 | 2026-06-12 | 2 |
| 73 | 5,502 | 2026-06-12 | 2026-06-12 | 1 |
| **74** | **190,842** | **2026-06-15** | **2026-07-10** | **18** |

**v74's intraday logs start on its own ship date (2026-06-15), not on 2026-05-27.** The "13 trading days" framing in the task brief describes the *raw* intraday-log window, which is real but **spans seven scoring versions**, each with a different dampener stack (v60 through v73 still carry some subset of WCF/ICH/CWCF/CSWC/SCW; only v74 has the fully-lean stack). A fakeout counted under v60 can be mechanically impossible under v74 (its cause was later retired) — see §4.

- v74-only: 18 distinct dates, ~770 distinct symbols/day, 6,716–12,415 rows/day (except the partial day 07-10).
- Snapshots-per-(symbol,date) distribution (v74): ranges 1–27, modal band 13–16 (≈3,118–3,830 groups each) — a typical trading day logs a symbol ~13-16 times via `trader update` re-runs + `regime_reapply_today`.

### 3b. CLI surface — confirmed live (not just read from source)

`trader intraday-swings 10/25 [--fakeouts] [--min-swing N]` and `trader intraday-drill SYM DATE` were run directly (light, seconds). Output shape matches `intraday_diagnostics.py` exactly: `is_fakeout = spread >= FAKEOUT_MIN_SWING(15) and abs(price_move_pct) < FAKEOUT_MAX_PRICE_MOVE_PCT(3.0)`. The code recognizes **exactly one** named fakeout family, `wcf_boundary` (fires when `wcf_lift` toggles in the dampener stage) — **now structurally dead** on v74 since `WCF_LIFT_K=0.0` (confirmed directly against `strategy_config.py`, see §4). Every other fakeout is attributed generically to one of six pipeline stages (weekly / components / volume / regime / dampener / boost) with no family label.

Two live `intraday-drill` pulls on real v74 `--fakeouts` rows (ADSK 2026-06-18, ARI 2026-06-16) **both** attributed 100% of the swing to the **volume** stage, specifically a same-day flip *into* `ABSORPTION` (magnitude 1.00) from a different volume-signal class (`CONVICTION`→`ABSORPTION`, `REJECTION`→`ABSORPTION`), with dampener/boost/regime stage deltas all exactly `0.0` (confirming those retired mechanisms contribute nothing on v74). This is new, load-bearing evidence — see §6 candidate 1.

### 3c. Regime-state inputs — full coverage, no gap

`MarketRegime` and `MarketBreadth`: **8,032 rows each**, date range **1995-01-03 → 2026-07-10**, essentially fully populated (`vix_close`/`regime_composite`/`regime_multiplier` 8,032/8,032 non-null; `trin` 7,961/8,032, `mcclellan_oscillator`/`pct_above_ema50` ≥8,030/8,032). Field names confirmed against `database/models/core.py`: `MarketRegime.vix_close`, `.regime_composite`, `.regime_multiplier`; `MarketBreadth.trin`, `.mcclellan_oscillator`, `.ad_line`, `.pct_above_ema50/200`. No coverage gap for either layer of this design.

### 3d. Outcome labels — barrier_outcomes (DuckDB mirror)

`barrier_outcomes` (`.cache/barrier_outcomes.duckdb`): **112,000,080 rows** total. The two option-aligned sets at w=15d:

| barrier_set | side | w_days | rows | date range |
|---|---|---:|---:|---|
| `30dte_apex` (live Apex predictand, TP+30%/SL−70%) | low (call) | 15 | 1,748,691 | 2016-04-29 → 2026-07-09 |
| `30dte_apex` | high (put) | 15 | 1,748,691 | 2016-04-29 → 2026-07-09 |
| `30dte_opt` (generic option-aligned) | low/high | 15 | 1,750,438 each | 2016-04-26 → 2026-07-09 |

`result` semantics confirmed: `1`=TP win, `0`=SL stop, `NULL`=expired/insufficient. Raw (unconditional) 30dte_apex label mix: call side TP 1,236,123 / SL 510,635 / EXP 1,933; put side TP 696,833 / SL 1,050,759 / EXP 1,099. Ample coverage for both Layer A (full history) and Layer B (thin intraday window: 21,614 rows pre-cutoff, 24,708 post-cutoff for the 2026-05-27→now range alone — these are *all* (symbol,date) rows, not gated to qualifying scores, so far more than the actual signal population needs).

### 3e. CRITICAL POWER QUESTION — answered with real counts

**CALIBRATION_CUTOFF_DATE = "2026-06-15"** (hard lock, confirmed live against `strategy_config.py` — see `.claude/docs/traps.md` "Holdout lock has flip-flopped"). Rows dated ≤ cutoff only for candidate selection; post-cutoff reserved for the ~Dec-2026 OOS read.

**Fakeout-family × outcome join window, pre-cutoff (2026-05-27→2026-06-15), split by which version was *actually logged* that day** (the (symbol,date,version) group definition — `spread = MAX(overall)-MIN(overall)`, `price_move_pct` from MIN/MAX price, fakeout = `spread≥15 & |price_move_pct|<3.0`, min 2 snapshots/group):

| pre_cutoff | version | n_groups | fakeout(15pt/<3%) | fakeout(20pt/<2%) | swing≥10 | swing≥20 |
|---|---:|---:|---:|---:|---:|---:|
| ✓ | 60 | 1,685 | 61 | 15 | 389 | 73 |
| ✓ | 69 | 770 | 16 | 5 | 115 | 24 |
| ✓ | 70 | 4,619 | 60 | 10 | 485 | 80 |
| ✓ | 71 | 1,542 | 11 | 3 | 123 | 21 |
| ✓ | 72 | 776 | 0 | 0 | 17 | 0 |
| ✓ | 73 | 772 | 1 | 1 | 8 | 1 |
| **✓** | **74** | **772** | **2** | **0** | **55** | **4** |
| ✗ (UNUSABLE-FOR-SELECTION) | 74 | 13,046 | 46 | 5 | 877 | 75 |

**Pre-cutoff totals (all versions pooled): 10,936 groups, 151 fakeout events (15pt/<3% def), 1.38% rate.** The per-version column is the real story: **v72/v73/v74 (post-WCF-ramp-ship era) show 0/1/2 fakeout events vs 61/16/60/11 for v60/v69/v70/v71** — this is an independent, out-of-band confirmation of the Priority #7/#9 CLOSE verdict (§1), computed fresh in this recon pass rather than cited from `weekly_flip_probe`.

**v74-only pre-cutoff: exactly 772 (symbol,date) groups — i.e. exactly ONE trading day (2026-06-15)**, with 2 fakeout events at the loose def and 0 at the tight def. **This is the power wall.** Two events on N=772 is not minable at any respectable bar (z≥3, N≥500 is not just "not met," the *feature space* for finding a discriminating cohort doesn't exist at N=2). v74-only post-cutoff (13,046 groups, 46 fakeout events, 0.35% rate) is the real sample — and it is entirely reserved.

**Qualifying-signal supply in the same window (v74-tagged scores, 2026-05-27→2026-07-10, all 31 calendar days incl. both sides of cutoff):** 209 call-75+ signals, 665 call-70-74, 1,269 put-≤25, averaging ~7/day, ~21/day, ~41/day respectively (full per-day table captured in the scratchpad script output; daily supply is thin and noisy — e.g. 2026-07-06 had 0 call-75+ signals).

**Regime × miss layer power on FULL v74 honest history ≤ cutoff (2016-01-01→2026-06-15, the assess-WINDOWS-cap-matched 10y scope):**

By year (call-75+ / call-70-74 / put-≤25):

| year | call75+ | call70-74 | put≤25 |
|---|---:|---:|---:|
| 2016 | 339 | 910 | 8,903 |
| 2017 | 493 | 1,520 | 4,473 |
| 2018 | 772 | 1,769 | 4,137 |
| 2019 | 430 | 1,165 | 8,586 |
| 2020 | 1,197 | 2,890 | 7,208 |
| 2021 | 1,273 | 3,801 | 2,642 |
| 2022 | 1,177 | 2,224 | 11,503 |
| 2023 | 477 | 1,420 | 14,026 |
| 2024 | 1,190 | 2,835 | 9,890 |
| 2025 | 1,060 | 2,720 | 10,015 |
| 2026 (partial) | 677 | 2,026 | 3,714 |
| **TOTAL** | **9,085** | **23,280** | **85,097** |

By regime cell (VIX band × McClellan sign — the RXDD/MWDD axes, first cut):

| vix_band | mcc_sign | n_days | call75+ | call70-74 | put≤25 |
|---|---|---:|---:|---:|---:|
| calm(<20) | flat([-22,22]) | 1,290 | 3,276 | 9,878 | 37,194 |
| calm(<20) | pos(>22) | 339 | 611 | 2,112 | 19,169 |
| slowbleed(20-26) | flat | 281 | 1,405 | 3,581 | 8,207 |
| slowbleed(20-26) | pos(>22) | 91 | 175 | 545 | 7,245 |
| calm(<20) | neg(<-22) | 212 | 719 | 1,700 | 3,521 |
| panic(≥28) | flat | 99 | 698 | 1,339 | 2,511 |
| slowbleed(20-26) | neg(<-22) | 137 | 632 | 1,370 | 1,968 |
| panic(≥28) | neg(<-22) | 93 | 909 | 1,100 | 917 |
| elevated(26-28) | flat | 50 | 259 | 719 | 1,618 |
| elevated(26-28) | pos(>22) | 19 | 70 | 241 | 1,154 |
| panic(≥28) | pos(>22) | 27 | 53 | 175 | 1,109 |
| elevated(26-28) | neg(<-22) | 37 | 278 | 520 | 484 |

**Verdict: Layer A (regime × miss on full history) is genuinely powered.** 8 of 12 cells clear N≥500 on call-75+ alone; all 12 clear N≥500 on put-≤25 and all but two (`elevated×pos`=241, `panic×pos`=175) clear it on call-70-74. Only the doubly-rare `elevated(26-28) × {pos,neg}` and `panic(≥28) × pos` cells are thin for calls specifically (53–278) — consistent with why every shipped lever (RXDD/MWDD/TVDD) is a **single-axis** band-pass, not a 2-way regime cross: crossing two regime axes thins out fast. **Layer B (fakeout × outcome on the intraday window) is not powered for v74-specific selection — only 1 usable pre-cutoff day exists.** This asymmetry drives the two-layer split below.

### 3f. WCF precedent's fakeout-reduction metric — exact definition to reuse

From `experiments/wcf_score_ramp/FINDINGS.md` (v72 ship, the direct ancestor of this design): the metric was **not** WR-based. It was a `score_intraday_logs` replay (2026-05-27→06-11 window, 18,110 snapshots, 1,542 groups, 200 WCF-affected) reporting four counts per candidate variant vs production:

- `affected groups swing ≥10` (WCF-affected (symbol,date) groups only)
- `affected mean swing`
- `all groups swing ≥15`
- `all groups swing ≥20`

Ship decision was made on **top33 vs top30/top31** purely by which variant most reduced these counts (top33: swing≥10 43→17, −60%), **with per-trade WR checked only for non-regression** (Stage 1-N N1-N4 neutrality track: tradable-bucket invariance + reconstruction bit-exactness + direct fakeout-reduction evidence + W4 on the one diagnostic bucket that did change). **This design reuses that exact metric shape** for any candidate whose value proposition is fakeout reduction rather than WR lift.

---

## 4. Two-layer structure (what the inventory supports)

**Layer A — regime × miss cohort mining on full v74 history ≤ cutoff.** Powered (§3e). Method: extend the `experiments/miss_ledger/` lineage (`build_ledger.py` join pattern: Score components + weight_info features + `30dte_apex`/`30dte_opt` w=15 barrier outcome + breadth, `analyze_ledger.py` cohort lift/z) with **new fakeout-*proneness* proxy features** computable on every historical EOD score row without needing intraday logs — e.g. boundary distance to the 70/75 gates, a component-agreement/disagreement metric (SPREAD_TILT's own `spread = sqrt(pop-variance of [trend,bb,rsi,macd,stoch]/5)` generalized to the admission boundary), and volume-signal-classification proximity to the `ABSORPTION`/`CLIMAX` reversal-blend thresholds (`ELEVATED_RATIO_THRESHOLD`, `CLIMAX_RATIO_THRESHOLD` in `volume_amplifier.py`). This is the **primary evidence layer** — it answers "do fakeout-prone-*shaped* signals also have worse WR15/optTP15 outcomes, and does that hold within specific regime cells," which is testable at real N.

**Layer B — fakeout-family overlay on the thin intraday window.** NOT powered for v74-specific selection (§3e: N=772/2 events pre-cutoff). Treated as **descriptive-only, corroborating evidence, not a selection input.** Its job is narrower: (1) confirm that the Layer-A proxy features (boundary proximity, volume-classification proximity) actually *correlate* with observed intraday swing magnitude on whatever v74 rows exist (772 pre-cutoff + — once Dec-2026 OOS unlocks — 13,046 post-cutoff), and (2) track the WCF-precedent-style fakeout-count metric (§3f) as a **watch metric**, not a gate, growing in power every trading day going forward. A candidate may **advance on Layer A alone**; Layer B is reported for context and re-checked at the Dec-2026 OOS read, never used to select or reject before then.

**Why this split is honest, not a workaround:** the mission's own pre-registered bar (§5) requires "replication on a disjoint date-half" — that is only possible on Layer A (full multi-year history). Layer B literally cannot replicate on a disjoint half of one day. Building the design around Layer A as primary is the only way to actually clear the bar; treating Layer B as equally load-bearing would be exactly the kind of "mid-assess race" / small-N overclaim the traps registry warns against (`.claude/docs/traps.md` §2 "Mid-assess read is a race, not a result", "Cohort z-score is necessary but NOT sufficient").

**Layer B — PARKED follow-up (pre-registered).** Unlock condition: after the pre-registered **Dec-2026 OOS evaluation** completes and the cutoff re-locks forward, the v74 post-cutoff intraday window (46+ fakeout events as of 2026-07-10, growing ~2.5/trading day) rolls in-sample. At that point, re-run Phase 0's dominant-stage attribution AND the §3f WCF-precedent metric (affected-groups swing≥10/15/20 counts + mean swing) **at the same locked bars pre-registered here** — no bar renegotiation at unlock time. Until then, Layer B numbers are **watch-only**: they may be recorded as they accrue but must not inform candidate selection, ranking, or any ship decision.

---

## 5. Pre-registered bars — VERBATIM

> candidate advances only at cohort z >= 3 with N >= 500 AND replication on a disjoint date-half; fakeout-reduction measured on intraday-swing metrics (per-trade WR neutrality is acceptable — WCF precedent shipped on fakeout reduction alone).

Operationalized against §3:
- **z ≥ 3, N ≥ 500** — computed on Layer A (the only layer with N≥500 cells; see §3e regime-cell table). Per-cell N is reported alongside every z (no cell reported without its N).
- **Replication on a disjoint date-half** — see §7 protocol (interleaved-year split, not a contiguous chronological split).
- **Fakeout-reduction on intraday-swing metrics, WR-neutral acceptable** — Layer B's watch metrics (§3f shape: affected-groups-swing≥10/15/20 + mean swing) are the value case for any candidate whose Layer-A cohort z comes from a *stability* framing rather than a WR framing; this mirrors the WCF/Stage-1-N precedent exactly and is how a fakeout-guard candidate is allowed to ship WR-flat.

---

## 6. Methodology commitments

1. **Date-clustered SEs on any panel stat.** *(Corrected per A4.)* The reusable orthogonality-test harness this design extends (`experiments/regime_call_alpha/orthogonality.py`) computes a **plain** `std/sqrt(N)` standard error per cell — adequate for a first look but understates the true SE for any panel where same-day signals share regime state and co-move (the exact mechanism this design is built around: a regime cell's signals are correlated by construction). Every gate-qualifying z-score this design produces must cluster by `date`. **Implementation: REUSE `experiments/gex/gex_test.py::ols_with_clustered_se`** (numpy-only cluster-sandwich OLS with the G/(G−1) small-sample factor; regress the outcome on a cohort indicator, the indicator's `t_clust` is the date-clustered z) — the repo's existing, self-tested implementation (also used throughout `experiments/osk_validation/`). The draft's original claim that no prior date-clustering existed in this repo was wrong (it searched FINDINGS.md; those experiments use VERDICT.md). Plain-SE z-scores from `analyze_ledger.py`'s lift/z method (binomial SE, no clustering) are **screening-only**, never gate-qualifying.
2. **N-label every cell.** No lift/z table row without its N printed alongside (matches `analyze_ledger.py`'s existing table format — keep it).
3. **SKIP < 30.** Any cell with N<30 is dropped from consideration entirely (mission floor; matches `analyze_ledger.py`'s existing pair-level `min_n=30` — extend it to single-feature cells too, tightening from the script's current `min_n=50` default only where the mission's 30-floor and the script's 50-floor disagree, prefer 50 as the practical default and 30 as the absolute floor).
4. **Panic-band exclusion.** Every candidate must explicitly exclude the panic extreme (`VIX≥28` and equivalent) from its fire condition — crash cohorts are mean-reversion WINNERS for this book (`.claude/docs/traps.md` "A regime signal's crash-band cohort is usually a mean-reversion WINNER"). This is a hyperparameter of the candidate's own design, checked before any sweep, not a post-hoc filter.
5. **Orthogonality test vs shipped DD levers' fire states.** For any candidate whose fire condition correlates with a shipped lever's own band (RXDD VIX~20-26 slow-bleed, MWDD McClellan-flat |mcc|<~22, TVDD TRIN~1.0-1.3, SVR semivol_r 0.7-1.25, BDIV SPY-near-60d-high×breadth-rollover), reuse the `orthogonality.py` pattern (§3e cell table is the template: slice the candidate's effect by each lever's own OFF region, then by the ALL-LEVERS-OFF slice) — **date-clustered this time** (commitment 1). A candidate that dissolves to baseline in the all-levers-off slice is DEAD (re-detects a shipped lever, not a new axis).
6. **Sign-stability across regimes required.** Compute the candidate's effect separately per year and per VIX/McClellan cell (the §3e tables are the scaffold); a candidate whose effect flips sign bear-vs-bull or crash-vs-calm is a crash-artifact, same class that killed DQT and the NH/NL participation-quality lever (`.claude/docs/traps.md` §4). Sign flips on cells with N<100 are noise, not evidence, per the same noise-aware convention `stage1_growth_gate.py` W2/W3 already use.

---

## 7. NULL-CHECK table — candidate axis → closed-axis collision → verdict

| Candidate axis | Closed-axis collision | Verdict |
|---|---|---|
| Chop/flat-regime detection as a call-alloc dampener | ≡ shipped **MWDD** (McClellan-flat band); A-MKT SPY-trend-flat variant TESTED-NULL 2026-06-25 (orthogonality gate failed — dissolves inside MWDD's own band) | **DEAD** unless the candidate's fire condition is proven orthogonal to MWDD's band on the all-levers-off slice — do not re-derive a chop detector |
| Omen-day / early-warning DD cuts (Hindenburg, Zweig, NL-spike, summation-divergence) | CLOSED — `experiments/dd_onset_omens/FINDINGS.md`: 0/24 omen days preceded a major DD onset; omen-day entries are mean-reversion WINNERS (+0.079 mpnl). Only survivor was BDIV (shipped) | **DEAD** — any "detect the warning sign, cut exposure" framing is the closed trap; BDIV already captured the one real leading signal |
| %-at-ATH / narrow-breadth "de-rate calls" | NULL, INVERTED — `experiments/breadth_ath_dd/` (2026-06-26): narrow-froth (<2% at ATH) is the **BEST** call cohort (+0.091 mpnl); redundant with MWDD (G44) | **DEAD** |
| Market-Wave / SPY↔breadth-correlation "collapse → cut calls" | CLOSED — already shipped twice (MWDD + BDIV); the literal collapse_flag cohort is a mean-reversion WINNER (+0.274 mpnl) in the all-5-levers-off slice (`experiments/spy_breadth_corr_dd/FINDINGS.md`) | **DEAD** |
| Regime call-edge re-derivation (bear=best / chop=poison) | KNOWN, not a lever — `experiments/regime_call_alpha/FINDINGS.md`: DOWN_hard apexEV +8.98%, FLAT/chop −1.57%; within chop no signal-time discriminator exists (all \|z\|<1.5) | A candidate that just re-labels chop is **DEAD**; only licenses a candidate if it finds a discriminator *within* chop that survives the all-levers-off slice — none has yet |
| Volume-amplifier flattening/cap/removal for "quality" | Funded-WR-NEUTRAL — `experiments/volume_rework/SHIP_CANDIDACY.md`: flattening moves only supply, ±≤1pp funded WR; load-bearing on generic WR30, cannot be deleted | Blanket flattening **DEAD**. The one still-live residual is `INTRADAY_TYPE_CONF_GATE` (shipped, gates `CONVICTION↔THIN_AIR` continuation-class wobble only) — **candidate 1 below targets a DIFFERENT, uncovered transition family (reversal-class `ABSORPTION`/`CLIMAX`) and is explicitly NOT a re-test of the closed axis** |
| Entry timing (open vs close, gap-filtered) | CLOSED — `experiments/entry_timing_v71/FINDINGS.md`: every gap filter converges to ~71.5% WR; selection gain and entry-price drag cancel | **DEAD** — out of scope for a scoring-stage fakeout guard anyway |
| Score.overall re-shaping / re-scaling / z-score / percentile / cross-sectional rank | DEAD, canonical failure mode — `experiments/score_norm/FINDINGS.md`: every scheme is dilutive on the option barrier; A2 audit (2026-06-24) confirms v74 is already at the gate-vs-gradient optimum, no funded pure-gradient-shaper left to cut | **DEAD** — no candidate here may re-grade `overall`'s continuous gradient; every candidate must be **gate-acting or DD-validated**, per the A2 falsifiable rule, not a magnitude re-rank |
| Reviving WCF / ICH / CWCF / CSWC / SCW (v73-retired) | Retired on honest ReSim evidence (`experiments/dampener_ablation_v72/FINDINGS.md`): WCF deleted ~85% of the put band at z=−0.43 (no discrimination); ICH inert on calls, wrong-way on puts; CWCF/CSWC/SCW marginal-retire (\|z\| 1.6-2.0, below shared WR) | **DEAD as literal revivals.** Reviving anything *shaped* like these (a boundary-cliff dampener) requires (a) a genuinely NEW fakeout/intraday evidence axis — not the old wadj-neg/stoch-contradiction cohort definitions — AND (b) proof it does not regress the v74 lean-core's per-member apex-EV evidence (re-run the `experiments/skill_vs_baseline/` per-member attribution style test: does the new dampener's removed cohort carry positive or negative per-member apex-EV on the funded book, the same test that retired CONT_BOOST/WVD/DAILY_VOLUME_AUTHORITY/EARN_BOOST). Candidates 1-3 below are shaped like this family (boundary/transition-cliff guards) but are defined on **currently-live** mechanisms (volume-classification blend, admission-boundary component agreement, regime-multiplier reapplication), not resurrected wadj/stoch cutoffs |
| Reviving CWWD (still active, non-tradable [70,75) gate) | Confirmed funded-irrelevant by the A2 audit (0/569 75+ rows change under an on/off A/B) | Any candidate touching CWWD's gate range is **diagnostic/product-decision scope only**, never a funded-book claim |
| Reviving continuation-echo / daily-volume-authority-wave (v74-retired) | continuation: neutral-EV supply, DD-inducing via over-deployment; daily-volume: net negative-EV adds (−1.66% vs +2.57%) — both per-member apex-EV attribution, `experiments/skill_vs_baseline/OVERNIGHT_FINDINGS.md` | **DEAD as literal revivals**, same (a)+(b) bar as the WCF-family row above if ever revisited in a new shape |
| Continuation-lift exhaustion-entry guard (NEW_LEADS #9) | PREMISE WEAKENED 2026-06-09 — on the live apex15 barrier, continuation-lifted 75+ calls are z+0.9 (better, not harmful); `experiments/missretest_apex15/FINDINGS.md`. Also now moot: `CONT_BOOST_ENABLED=False` in v74 | **DEAD** (both on the evidence and because the mechanism it would guard no longer fires) |
| Score-fidelity "smooth a component cliff" (MACD 4-branch momentum-phase discontinuity) | TESTED-NULL 2026-06-24 — `experiments/score_fidelity/`: smoothing removes ABOVE-average-WR rows from 75+ (the cliff is load-bearing, not a measurement artifact); the dropped-rows-apex-WR test is the mandatory pre-check for any "smooth a cliff" framing | **DEAD as a smoothing/removal candidate.** Candidate 5 below is reframed to the untested corner — *same-day oscillation ACROSS an internal branch threshold* (an intraday-log-measurable event, not a static cliff-smoothing edit) — and is ranked lowest, explicitly flagged as adjacent to this closed axis |
| `wcf_boundary` fakeout family (the one named family in `intraday_diagnostics.py`) | Mechanically dead — `WCF_LIFT_K=0.0` confirmed in `strategy_config.py`; 0-2 fakeout events/version in the v72-v74 era (§3e) | **DEAD**, code-level: the label still exists in `intraday_diagnostics.py` but can never fire again under v74. (Non-blocking cleanup note, not part of this design's scope: the label could be retired or repointed.) |

---

## 8. Ranked candidate cohort axes

Built from the fakeout families in §2/§3f × regime states in §3e × miss outcomes in §3d, filtered through §7. Ranked by (a) strength of direct evidence gathered in this recon pass, (b) orthogonality risk (lower = better), (c) N reachable on Layer A.

### 1. [TOP] Reversal-class (`ABSORPTION`/`CLIMAX`) same-day volume-blend transition guard

**Rationale:** the shipped `INTRADAY_TYPE_CONF_GATE=0.5` fix (`volume_amplifier.py`) gates only the *continuation*-class `CONVICTION↔THIN_AIR` wobble (its own code comment: "the ATI/AMSC ~18pt... live partial-day evolution"). `ABSORPTION`/`CLIMAX` are a structurally different, ungated branch (`_REVERSAL_SIGNALS`) that uses a **blend-toward-extreme-target** model (`final = weighted_sum*(1-blend_w) + vol_target*blend_w`, `vol_target=50±magnitude*50`) rather than a multiplier — so a same-day flip *into* `ABSORPTION` at high magnitude can move the score ~35-40 points in one step, confirmed live: **both** `--fakeouts` drill-downs pulled in this recon (ADSK 2026-06-18: `CONVICTION→ABSORPTION`, Δ+41, 100% volume-stage; ARI 2026-06-16: `REJECTION→ABSORPTION`, Δ−38, 100% volume-stage) landed in this exact family, with dampener/boost/regime stages at flat `0.0` (nothing else is contributing on v74 — this genuinely is the dominant residual mechanism, not an artifact of a 2-sample draw pulled from a biased list). **This is the WCF-lineage shape exactly**: a classification-boundary blend discontinuity, same as WCF's score-gate cliff, addressed the same way (a ramp/gate on the transition, not a market-level exposure change). **Orthogonality risk: LOW** — this is a signal-level (per-symbol volume classification) mechanism, not conditioned on any market index; a regime cross (e.g. does it fire more in a specific VIX band) is a secondary refinement, not the core definition, so the mandatory orthogonality check (§6.5) is cheap and unlikely to kill it outright.

### 2. [HIGH] Admission-boundary (68-77) component-disagreement guard

**Rationale:** generalizes the shipped, DD-validated **SPREAD_TILT** mechanism (component disagreement `spread=sqrt(pop-variance of [trend,bb,rsi,macd,stoch]/5)`, currently scoped to the 75-79 tier only) to the **70/75 double-gate crossing zone** — the boundary where WCF/CWCF/CSWC all historically found their signal, and where the (formally-invalidated-as-a-queue, but directionally-informative) v60 MISS_CANDIDATES table's #1 harmful family ("marginal CALL boundary weakness", `score_bin=75-79`, z+4.38) and the miss-ledger's original z=+10.1 wadj-neg finding both lived. SPREAD_TILT itself is proof this SHAPE of signal survives a modern Stage-3 gate (T1-T7 PASS, 5y WorstDD −4.1pp). This candidate asks whether the same component-disagreement metric, applied at the **admission** boundary (68-77, spanning both the 70-cascade-eligibility gate and the 75-tradable gate) rather than the already-covered 75-79 tier, has residual signal SPREAD_TILT didn't reach. **Orthogonality risk: LOW-MEDIUM** — purely signal-level, but must be checked against SPREAD_TILT's own scope to avoid literal duplication (not a shipped *DD lever* in the RXDD/MWDD sense, so §6.5's specific lever list doesn't directly apply, but a SPREAD_TILT-collision check is the analogous discipline).

### 3. [MEDIUM] Regime-multiplier reapplication boundary-crossing guard

**Rationale:** the direct, previously-flagged-but-unbuilt follow-up named in `experiments/wcf_score_ramp/FINDINGS.md` Recommendation #3: "the gate evaluates the post-regime integer... both sub-point component wobble AND regime-multiplier reapplication (1.02↔1.04) can flip the boundary... The sibling cliff — CWCF call-side gate at overall ≥ 75... is the same instability class with portfolio consequences (it crosses the cascade threshold). Worth its own investigation if intraday logs show that family firing." CWCF is now retired (v73), so the *specific* cited case is moot, but the **general mechanism — `reapply_regime_today()` patching a score across a gate mid-day — is still live** and applies to whichever gates remain (70-admission, 75-cascade). Proxy: signals within ~1-2 raw points of a gate at the pre-regime stage, where the regime multiplier alone (not components, not weekly) determines gate side. **Orthogonality risk: LOW** (signal-level + regime-multiplier-level, not a market-band condition) but **evidence is weaker than candidates 1-2** — 0 of the 2 live drill-downs pulled in this recon showed regime-stage as the dominant driver (both were 100% volume-stage) — this needs its own scan of the v74 `--fakeouts` output before any compute is spent, not just a plausibility argument.

### 4. [NOT LICENSED THIS PASS — conditional follow-up per A2] Volume-magnitude-ceiling × flat/chop-regime interaction

**Rationale:** the miss-ledger lineage's own precedent (Finding #3, `experiments/miss_ledger/FINDINGS.md`): `wadj=neg & vmag=mid/hi` was a compounding miss driver (lift 1.39-1.40) because "the volume amplifier doesn't see weekly context; it amplifies noise in that cell." The generalized hypothesis — high `volume_magnitude` (near its 0.6-1.0 ceiling) combined with a flat/chop regime state amplifies noise rather than signal — is plausible and ties a signal-level feature (the symbol's own volume magnitude) to a regime state, matching the mission's "possibly interacted with regime" framing. **Orthogonality risk: HIGH — this is explicitly why it's ranked below 1-3, not despite it.** A flat/chop-regime condition is exactly MWDD's own territory (§7 row 1); this candidate MUST clear the all-levers-off orthogonality slice (§6.5) on the volume-magnitude axis specifically (not just re-confirm MWDD's existing McClellan-only finding) before a single hour of mining compute is spent on it. If the "flat/chop" component of the interaction dissolves once McClellan is controlled for, only a pure volume-magnitude-ceiling term (no regime cross) survives, which then collapses into candidate 1's territory (both are volume-amplifier-adjacent) — expect consolidation, not two independent ships.

### 5. [NOT LICENSED THIS PASS — conditional follow-up per A2; explicit collision flag] Component-boundary (RSI/Stoch/MACD internal-branch) same-day oscillation guard

**Rationale:** the third named family from §2 (BBW-style component bouncing) is real in the v60-era taxonomy but **collides directly with the CLOSED score-fidelity NULL** (§7): "smoothing a component cliff" was tested and killed because the cliff is load-bearing (dropped rows carry ABOVE-average WR). This candidate is only licensed in the **narrow, untested reframing**: measure same-day oscillation *across* an internal branch threshold (an event visible only in `score_intraday_logs`, not a static-cliff-smoothing edit to `calculate_rsi_score`/MACD branches) as a **transient distrust signal** for that day's admission, not a permanent formula change. Given (a) the direct collision risk, (b) zero supporting evidence from the 2 live drill-downs in this recon (both were volume-stage, not component-stage), and (c) the closed axis's own finding that the underlying cliffs are genuinely load-bearing signal — this is ranked last and should only be pursued if candidates 1-3 are exhausted or a Layer-B tally (once more v74 days accumulate) shows component-stage swings are a materially larger share of fakeouts than the 2-sample draw suggested.

---

## 9. Compute plan

**Phase 0 — Layer B tally (AMENDED per A1: holdout-restricted, narrative-only).** Tally **pre-cutoff (≤2026-06-15), v69+ only** fakeout groups by dominant-stage attribution (`intraday_diagnostics.attribute_swing`'s 6-stage classification) × version. Retired-dampener-stage attributions (keys retired in v73/v74: wcf/cwcf/cswc/scw/ich/wvd/mcd/cont/ern/daily-volume-authority/sector-wave) are reported **separately** and **excluded from the live-mechanism ranking** (they cannot fire on v74). The post-cutoff v74 tally is FORBIDDEN until the Dec-2026 OOS unlock. Phase 0 does **NOT** gate Phase 1 (all of candidates 1-3 are mined regardless) — it only orders the results narrative. **Foreground-safe** (light aggregate + a loop over `attribute_swing()` for ~90 groups, seconds).

**Phase 1 — Ledger build (queue, per A8: market hours → `--priority normal --db heavy --restartable --dedup miss_regime_fakeout_ledger --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1`; the build MUST call `experiments._holdout.assert_no_holdout_leak` on the built frame per A6, never `HOLDOUT_DISABLE`).** Extend `experiments/miss_ledger/build_ledger.py`'s join pattern (Score components + weight_info + `30dte_apex`/`30dte_opt` w=15 barrier + breadth) with the new proxy features for candidates 1-3: (a) boundary distance to 70/75 gates, (b) admission-zone component-disagreement (`spread` metric, SPREAD_TILT formula reused), (c) volume-signal-classification proximity to `ELEVATED_RATIO_THRESHOLD`/`CLIMAX_RATIO_THRESHOLD`, (d) regime-multiplier boundary-crossing flag (requires `pre_regime` + `regime_multiplier` at the EOD row, both already in `Score`). Scope: full v74 history ≤ cutoff (2016-01-01→2026-06-15 per the assess-WINDOWS-matched bound used in §3e, extendable to the full 1995+ backfill if Layer A needs more N in the thin regime cells — not expected to be necessary given §3e's cell table). **Estimated runtime: 30-45 min** (MySQL component pull for the qualifying population, same order of magnitude as the existing `build_ledger.py`'s 5y pull; DuckDB barrier join is sub-second per §3d/5a timings; regime/breadth join is a Python-dict join on 8,032 rows, trivial).

**Phase 2 — Cohort mining with date-clustered SEs (queue or foreground once the parquet exists — foreground-safe if it stays under ~1 min per read, per house rules "no ScoreSimulator/recalculate/assess/MC/research-pack/parquet-cache build" — this phase reads an already-built parquet, not a rebuild, so it qualifies as light).** Extend `analyze_ledger.py`'s lift/z method with: (a) date-block clustering for every reported z (§6.1), (b) the 12-cell regime cross from §3e as an additional grouping axis alongside the existing `(signal_type, score_bin)` cohorts, (c) N-label + SKIP<30 (§6.2-3) baked into the table formatter. **Estimated runtime: 10-15 min** (pure polars aggregation over a cached parquet, same order as the existing script's near-instant cohort tables).

**Phase 3 — Orthogonality + sign-stability (queue, reuses `experiments/regime_call_alpha/orthogonality.py`'s pattern).** For any candidate surviving Phase 2 at z≥3/N≥500: (a) slice by each shipped lever's own OFF region + the all-levers-off slice (§6.5), date-clustered; (b) per-year and per-regime-cell sign check (§6.6). **Estimated runtime: 10-15 min** per surviving candidate.

**Phase 4 — Disjoint date-half replication protocol (AMENDED per A5: dual split, both mandatory).** (a) **THE replication bar:** split the ≤cutoff full-history population (2016-2026) by **interleaved calendar year** (even years {2016,2018,2020,2022,2024,2026-partial} vs odd years {2017,2019,2021,2023,2025}), **not** a contiguous chronological split. Rationale: a contiguous split risks concentrating an entire regime era (2020 COVID crash, the whole 2022 bear year) inside one half, starving the other half of exactly the regime cells this design's candidates are conditioned on. Re-run Phase 2's cohort mining independently on each half; a candidate must clear **z≥3 with N≥500 on BOTH halves (date-clustered)** to count as "replicated" (A3 phrasing governs). (b) **ERA-ROBUSTNESS report line (report-only, not a bar):** additionally report the contiguous era split **2016-2021 vs 2022-2026** plus the per-year sign table. An effect present in only one era is flagged **ERA-LOCAL** (the OSK failure mode — interleaved halves both contain post-2022 rows, so an era-local effect can pass both halves; this line is the tripwire).

**Total estimated compute: ~1-1.5h**, almost entirely Phase 1 (the MySQL pull), queued per `.claude/skills/queue-ops`. Layer B (Phase 0) is foreground-safe today. No phase requires `ScoreSimulator`, `recalculate`, `assess`, Monte Carlo, or a fresh parquet-cache build in the sense the house rules gate — Phase 1 is a bulk MySQL+DuckDB *read* into a new parquet (the same class of operation `build_ledger.py` already does), which is exactly the "queue it, even read-only" case named in CLAUDE.md's Long-Running Compute section, so it still goes through `trader queue submit`, not run raw.

---

## 10. Routing note — verbatim

> anything advancing goes to the normal gates (Stage-1 W1-W6 for scoring dampeners via ship-gates skill; Stage-3 T1-T7 for exposure shaping) — evidence memo first, no auto-ship.

Concretely: candidates 1-3 (score-stage guards, modify `Score.overall` or a pre-cascade dampener) route to **Stage 1 W1-W6** (cohort z ≥3 is W1; this design's §5/§6 bars are the pre-flight, not a substitute for the full W1-W6 pass once a candidate is chosen). If a candidate's value case is fakeout-reduction with WR-neutral evidence (the WCF/Stage-1-N precedent), it uses the **Stage 1-N neutrality track** (N1 tradable-bucket invariance, N2 reconstruction bit-exactness, N3 direct fakeout-reduction evidence via §3f's metric, N4 W4 on any diagnostic bucket that does change) rather than the growth-motivated W1-W6 path — same choice WCF (v72) made. Any candidate reframed as portfolio-stage exposure shaping (should not happen here per the mission's explicit scope, but noted for completeness) would route to **Stage 3 T1-T7** instead. Either way: **write the evidence memo, run the gate, wait for a verdict — no auto-ship**, matching every other Stage-1/Stage-3 ship in this repo's history.

---

## 11. Open items — RESOLVED BY AMENDMENTS (kept for the record)

1. ~~Phase 0 not yet run~~ → **Resolved by A1**: Phase 0 runs holdout-restricted (pre-cutoff, v69+), narrative-only, does not gate Phase 1. The recon's two drill-downs (ADSK, ARI) were post-cutoff observations; the amendment retracts their use for selection — all three candidates are mined regardless.
2. ~~Date-clustered SE has no repo precedent~~ → **Resolved by A4**: WRONG — `experiments/gex/gex_test.py::ols_with_clustered_se` is the existing implementation (VERDICT.md-family experiments); reused here.
3. ~~SPREAD_TILT overlap unchecked for candidate 2~~ → **Resolved by A7**: overlap pre-check is MANDATORY before mining c2 cells; carve-out or c2 dies as duplication.
4. **Layer A's regime-cell table (§3e) uses a first-cut VIX-band/McClellan-sign bucketing** (calm/slowbleed/elevated/panic × pos/flat/neg) chosen to mirror RXDD's and MWDD's own live bands; a finer or coarser cut might change which cells clear N≥500. This is a reasonable default, not a locked decision — but per A3, no same-pass re-cuts if the pass comes up empty.
