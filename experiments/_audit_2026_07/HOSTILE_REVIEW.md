# Hostile Review of the v74 Evidence Chain — 2026-07-17 (Overnight #2, Task B)

**Owner/merger:** FABLE (architect). **Method:** three independent Opus reviewers with distinct
lenses (statistical inference / market-mechanics / code-path), each prompted as a skeptical quant
paid to find the weakest link before more real money scales, each given the documented-limitations
list and told NOT to re-announce it. Findings merged, deduplicated, and **adjudicated at top level:
every load-bearing code citation of the top-ranked attacks was independently re-verified tonight**
(tags below). Read-only audit; one remediation executed same-night (A-0).

**Scope:** the v71→v74 retirement chain, Core/Apex/Sentinel Stage-3 evidence, the three-stage ship
gate, the Verification Substrate. **Not in scope:** re-litigating closed axes (no bar-shopping —
every probe below tests EVIDENCE INTEGRITY of what shipped, not new alpha).

**Adjudication tags:** `VERIFIED` = I re-checked the cite/mechanism in source tonight.
`PLAUSIBLE` = mechanism credible + internally consistent, cite not independently re-checked.
`REMEDIATED` = fixed tonight. `SOUND` = attacked and survived.

---

## 0. Executive synthesis — the compound story

No single attack kills the stack. Three CRITICALs **compound**:

1. **Selection licensing (STAT-2):** the funded DD levers cleared naive-binomial z bars computed
   on date-clustered, window-overlapping outcomes (z=+56.9-class numbers are physically absurd as
   independent-N). The program itself adopted CR1 date-clustered SEs for the July parked leads —
   the stricter bar exists in-repo but was never applied retroactively to what shipped.
2. **Validation substrate (STAT-1):** every Stage-3 verdict is a min-statistic (5y WorstDD) over
   ONE realized price path; paired seeds cancel fill noise and produce tight CIs around the wrong
   quantity (path/regime variance is never sampled).
3. **P&L engine keystones (MECH-1, MECH-2):** the loss floor rests on a zero-spread
   favorable-extreme dead-hold popout fill that absorbs **100% of call stop-losses** (the "−70%
   SL" never books), and the win leverage rests on an RV-based premium documented to underprice
   entry in high-IV windows — both optimistic in exactly the crash windows the DD-primary metric
   weights most.

Together: the collapse=0 / DD certificates are **conditional on 2-3 untested fill/pricing
assumptions plus a selection process whose statistical licensing is weaker than documented.**
The good news is falsifiability: nearly every probe below is wired (env vars exist) or read-only,
and the whole Tier-1+2 program fits in ~2 post-migration nights. **Sequencing consequence for the
masterplan draft §5: run these probes BEFORE the N=2000 E-tier certificates** — certifying
possibly-artifact numbers at 4× precision inverts the value order. (User re-orders Sunday;
decision rights unchanged.)

What this review does NOT say: it does not recommend halting the live ledger (the forward ledger
IS the honest OOS test, and R5/watchdog governance already covers it). It says: run Tier 1-2
before any **new-capital scale-up** decision.

---

## 1. Ranked attacks (merged, adjudicated)

### A-0. [REMEDIATED TONIGHT] The active version had NO scoring lock — the write-integrity guard was a silent no-op (code-path #2)
`scoring_locks.json` held locks for v73/v72/v69/v60 but **not v74** (active since 2026-06-15);
`verify_scoring_matches_version` hits `if lock is None:` → prints a warning and returns
(`scoring_version_guard.py:201-206`). Every v74 row for a month was written unguarded.
**VERIFIED** (read the JSON keys; reproduced the code path) → **REMEDIATED**: lock captured at
the operative state, fingerprint `99cf6f1298726338` verified (commit `fe129ad4a`). Drift
classification performed first: post-ship scoring.py delta = one same-day commit `b1bad05dc`
("bit-exact" MOS core/bias refactor); strategy_config delta = SPREAD_TILT #6 (portfolio-side,
outside SCORING). **Residual finding (open):** the fingerprint covers `strategy_config.SCORING` +
`scoring.py` namespace/bytes only — NOT `volume_amplifier.py` (MAX_AMPLIFICATION/decay tables),
`market_regime.py` (sigmoid/composite params), `market_breadth.py`, `weekly_pit.py` constants —
all of which move `Score.overall`. Extending `SCORING_CODE_FILES` is a one-line-class fix +
re-capture; do it post-migration.

### A-1. [CRITICAL, VERIFIED] The dead-hold popout fill is the collapse=0 keystone — and it absorbs 100% of call stop-losses at a zero-spread favorable-extreme fill (mechanics #1)
- **Claim:** "TP+30/SL−70" exits; "collapse=0 every window incl 2020-COVID" as the hard floor.
- **Crack:** `SL_BASE=−0.70` (`strategy_config.py:789`) ≤ `DEAD_HOLD_TRIGGER_PNL=−0.40` (`:1404`)
  **always**, so the dead-hold override fires on every call SL; the realized loss is set by
  `_compute_dead_hold_call` (`monte_carlo.py:2368+`): scan forward for a bar whose intraday HIGH
  maps to ≥ −15%, fill at `max(−0.15, open_pnl)` with `DH_POP_SLIP=0.0` (`:96`, routed `:2819-22`)
  — a resting limit, zero spread, at the favorable extreme, on deep-OTM near-expiry calls, during
  crash bear-rallies (the worst-liquidity bars in the book). A −0.70 bag pops to −0.15 on a ~+4%
  underlying bounce. The config states the dependency itself: dh_off (clean −70% SL) = 100%
  collapse. So the entire loser-loss distribution AND the collapse=0 floor hang on this one fill
  assumption. All five cites re-verified tonight.
- **Probe (wired, ~1-2h):** Core N=500×10 incl 2020_crash, A/B `DH_POP_SLIP=−0.03` then `−0.05`,
  plus a popout non-fill arm (30-50% chance the pop fails → rides to dh_expiry; small code
  change). SOUND: collapse stays 0, 5y DD moves <2-3pp. HOLLOW: collapse >0 on COVID/2022 or DD
  +5-15pp → the certificate was a free-fill artifact.

### A-2. [CRITICAL, VERIFIED] Funded DD levers were licensed by naive-binomial z on clustered data; the clustered bar exists in-repo but only for later, parked work (statistical #2)
- **Claim:** TVDD/BDIV/SVR/MWDD/RXDD are "genuinely orthogonal" discriminators (e.g. TVDD
  neutral-band z=+56.9 in the levers-off slice).
- **Crack:** `z = (rate − base)/sqrt(base·(1−base)/n)` with n = trade count — **grep-verified
  tonight in 15+ mining scripts** (`dd_residual_v70/mine.py:191`, `market_wave_dd_v70/mine.py:196`,
  `dd_onset_omens`, `breadth_ath_dd`, `apex_speed_v70`, `miss_ledger`, ...). Same-day signals share
  the market factor; 15-30d windows overlap; effective N ≪ trade count. z=+56.9 implies ~58k
  independent Bernoullis — impossible. CR1 date-clustered sandwich SEs exist in
  `peak_fakeout/mine.py:450-532` (July) — adopted AFTER the lever stack shipped. At plausible
  cluster counts the reviewer's deflation arithmetic puts several lever z's near or below the
  z≥3 bar.
- **Probe (machinery exists, minutes-1h, no MC):** re-run each lever's cohort effect through
  `_cluster_sandwich_se` with clusters=entry_date on the on-disk ledger parquets. SOUND:
  clustered t ≥ 3 for all five. HOLLOW: one or more levers' licensing evaporates → their
  DD-deltas become "unlicensed selection, single-path validated" (see A-4) and the Stage-3
  keep/retire questions re-open.

### A-3. [CRITICAL, VERIFIED-mechanism] RV-based premium inflates winner leverage 2-4× in the exact windows the DD levers and collapse certificates were gated on (mechanics #2)
- **Claim:** "pricing formula accuracy does not affect P&L" (options-pnl.md σ-invariance note);
  DD-lever gates and collapse=0 read on 2020-2022 windows.
- **Crack:** `premium_pct` sits in the DENOMINATOR of the intrinsic leverage term
  (`option_pricing.py:88-97`, re-read tonight); barriers and premium are both RV-σ-derived, so
  σ-invariance holds only when IV≈RV — the calibration window (Feb-Apr 2025) had IV premium ≈ 0
  by the doc's own admission, while COVID/2022 ran IV/RV 2-4×. Reaching the TP barrier books +30%
  at IV/RV=1 but ~+10% at IV/RV=3: winners are 2-4× too fat exactly where the five DD dampeners
  were mined. This EXTENDS the documented gamma/IV error-cancellation (which was quantified at
  the compound level and parked) to the **DD/collapse certificates themselves** — a corner the
  park verdict never tested. Lean ranking (most→least exposed): collapse=0-on-2020_crash >
  SL/dead-hold tuning (the +4% pop bounce becomes +12% under real IV → popouts fail) > SVR >
  RXDD/TVDD/MWDD > BDIV > cascade trims.
- **Probe (wired, overnight):** re-run the five lever ship gates as N=500 paired A/Bs with
  `IV_PREMIUM=1 IV_MODEL=1` (plug verified live at `monte_carlo.py:113,152` with F2 coefficients)
  vs off, on 2020_crash + 2022 + 5y. SOUND: every keep-decision holds sign. HOLLOW: DD
  improvements shrink/invert in high-IV windows → lever verdicts were calibrated on inflated
  leverage. (Caveat carried from `iv_premium_model/VERDICT.md`: F2 is a central estimate, MAE
  ~37% of median IV — treat as sensitivity band, not point truth.)

### A-4. [HIGH, VERIFIED-mechanism] Stage-3 verdicts are min-statistics on one path; paired-seed CIs omit the dominant variance (statistical #1)
- **Crack:** MC dispersion = gap-fill randomness on one realized tape (the December prereg
  discloses this verbatim); WorstDD is a minimum over that path; N-convergence across 100/300/500
  proves fill-noise control, not robustness to a different drawdown shape. The v74/RXDD/SVR
  DD-deltas were measured on the same windows they were selected on.
- **Probe (overnight):** window-perturbation + leave-one-year-out on the standing N=500 A/Bs
  (roll WIN_START ±3/±6mo; drop each year) for RXDD, SVR, and the v74 ablation. SOUND: deltas
  keep sign/magnitude in every fold. HOLLOW: deltas swing to 0 or flip under modest shifts →
  overfit to the 2022/2025-Q1 drawdown shapes.

### A-5. [HIGH, VERIFIED] The active v74's core evidence is N=300 with its own "confirm winner N=500" caveat unmet (statistical #4)
- **Crack:** grep-verified tonight — `skill_vs_baseline/OVERNIGHT_FINDINGS.md:161`: "N=300
  (confirm winner N=500); in-sample; shipping the lean core = ALGORITHM_VERSION"; the ship
  proceeded; the only N=500 work on the lean substrate answered a different question (cascade
  sizing). The active version's −10.8pp DD claim sits below the project's own N=500 ship floor.
- **Disposition:** masterplan draft P1.C-3 already schedules the N=1000 in-sample re-ablation —
  this attack **elevates its priority**: run it night 2-3, not night 7, and BEFORE any E-tier
  certificate of v74-derived numbers.

### A-6. [HIGH, VERIFIED-timeline] The lever stack was calibrated inside the holdout-unlocked interim, and the December pack contains no per-lever OOS efficacy test (statistical #5)
- **Crack:** timeline re-verified in git tonight: holdout disabled 2026-06-04 → re-locked
  2026-06-11 (`fb03b0907` "…holdout re-lock"); RXDD (06-04), SVR/MWDD (06-05), TVDD (06-07),
  v71 (06-10), BDIV (06-11) all landed inside/at the edge of that window. Structurally, the
  holdout is a SCORING lock — Stage-3 lever sweeps run on the full tape by design — and the
  December H1-H6 read tests scoring skill + aggregate ledger + lever DRIFT (H6, days-active
  ratio; apex-EV context-only). Nowhere is RXDD −5.6 / SVR −5.8 / MWDD −2.6 / TVDD −3.1 /
  BDIV −3.0pp re-estimated on post-cutoff rows. The ~−20pp funded DD edge has NO scheduled
  forward efficacy read.
- **Disposition:** propose a **December-pack amendment** (leave-one-lever-out N=500 DD
  re-ablation on the post-cutoff ledger) — goes through the pack's §6 Amendment Protocol
  (FABLE/user sign-off, logged BEFORE December; this review does NOT edit the frozen pack).

### A-7. [HIGH, VERIFIED-mechanism] Stage-1 assessment reads a barrier cache whose >160-day-old rows are frozen against a price substrate that provably drifts (code-path #1)
- **Crack:** assess reads cached barrier outcomes (`barrier_cache.py:570-586`); the only
  recurring refresh is `refresh_recent(days=160)` post-close; older rows are written once by
  manual backfill and never recomputed — while `PriceHistory` is retro-adjusted continuously
  (proven material in `gamma_iv_phaseb/VERDICT.md`: 0/38 windows bit-reproducible after 9 days).
  Stage-1 WR15/MAE/MFE/TP-anchors on the 5y/10y windows grade against a months-to-years-old
  snapshot; the MC/ledger walks current prices. Cross-version DELTAS are partly insulated (both
  arms read the same cache); every ABSOLUTE number and the Stage-1↔Stage-3 correspondence are
  exposed.
- **Probe (read-only, ~5 min):** sample ~200 signal dates >1y old on dividend/action-heavy
  names; diff cached `result/exit_return` vs a fresh `_walk_outcome` on current PriceHistory.
  SOUND: ≥99% bit-reproduce. HOLLOW: material flips → the "assess WR → fundable portfolio"
  chain has a broken joint; barrier-cache full-rebuild policy needed (and a substrate-pinning
  decision for December H-reads).

### A-8. [MEDIUM-HIGH, PLAUSIBLE] Multiplicity debt across the search program is unquantified (statistical #3)
~40-58 closed axes, ships swept up to 15,195 variants, zero cross-program FWER/FDR accounting
(repo-wide grep found none); survivors ≈ what a few-hundred-look search yields at the
(de-clustered, per A-2) per-look α. **Probe (paper, ~1 day):** build the alpha-spend ledger from
the FINDINGS corpus; compare survivor count vs expected false discoveries. Honest outcome either
way — it sizes the haircut December's OOS read must overcome.

### A-9. [MEDIUM, PLAUSIBLE] TP fills credit price improvement a resting limit cannot earn (mechanics #3)
`resolve()` samples the TP fill uniform across `[tp_level, bar_high]` with `SLIP_TP=0` — a
passive limit fills AT the limit; the overshoot-to-high credit is taker economics at
maker cost, on ~85% of trades (+1% overshoot ≈ books +44% instead of +30%). (Fill-range cite
not independently re-verified tonight; the [low,high] sampling family is confirmed in
`option_pricing.random_fill_pnl`.) **Probe:** pin TP fills to the barrier
(`u_lo=u_hi=tp_lvl`), one MC pair. If compound/WR15 move materially, the shipped TP=+30 target
itself was selected on overshoot credit.

### A-10. [MEDIUM, PLAUSIBLE] Core (the DEFAULT profile) has zero capacity model and a stale "migrate to a capped profile" mitigation comment (mechanics #4)
`capital_ceiling=0.0` → allocation base = full compounding equity; no OI/ADV/impact/integer
contracts anywhere in the fill path; at the 5y-median terminal book an ultra-tier fill ≈
hundreds-to-thousands of contracts on a single name. `strategy_config.py` still says "you
migrate to Core/Sentinel, which DO cap the base" — stale since the 2026-06-17 restructure made
Core itself the uncapped former-Apex (only Sentinel caps). **Probe:** trade-tape × P3.6
option-volume join → % of fills exceeding 10%/25% of the name's 30-DTE volume by book size;
plus a capped-arm MC. Also: fix the stale comment (one line, with the next portfolio-touching
commit).

### A-11. [MEDIUM] Bundle of PLAUSIBLE execution-realism attacks sharing one probe pattern
(mechanics #5/#6, code-path #6): (a) entry booked free at signal-close on high-momentum days —
adverse selection unmodeled, `ENTRY_FILL_MISS_P` exists (default 0) and has never been ON for a
shipped number; the engine's own doc concedes next-open runs ~1.3pp WR worse and that haircut
is never compounded into headline DD; (b) win-side same-name re-entry recycling survived the
config changes that killed the loss-side artifact (64.8% same-stock repeats; TP frees the slot
and the name re-signals into the same advance). **Probes (all wired/one-liners):** N=500 A/Bs —
`SLIP_ENTRY_OV=−0.015/−0.03` + `ENTRY_FILL_MISS_P=0.2`; next-open entry arm; 15-bar same-symbol
cooldown arm. Each: if the DD/collapse verdicts hold, the canon hardens with evidence; the
real-fill loop (P2.B, accruing) remains the final arbiter.

### A-12. [MEDIUM, VERIFIED-mechanism] Simulator parity (98.4%) is a global average; per-bucket parity in the ≥75 band that supply claims rest on was never measured (code-path #3)
The one recomputed-in-sim input (volume amplifier, with documented conviction cliffs) is exactly
the one concentrated in the tradable band; regime is reused, volume is not. **Probe (~minutes):**
stratify the existing 936k-row exact-match comparison by score bucket. HOLLOW threshold: <95%
match in ≥75 → the "+77% supply" class of claims is measured off a mis-stated base.

### A-13. [MEDIUM, VERIFIED-mechanism] Parquet caches have no read-time version guard; `is_fresh` is existence-only; superseded files are never GC'd (code-path #4)
`bulk_cache.py` writes no version column/sidecar, callers pass no deps, `rebuild-parquets`
writes beside old files. A same-version RECALC silently leaves stale content live. **Probe
(static, ~3 min):** audit `.cache/**` for mixed `_v{N}_` tags + grep consumers for
glob/hardcoded-tag reads. Fix class: version+recalc-timestamp sidecar asserted at read.

### A-14. [MEDIUM-LOW] v73 shipped through a primary-metric regression on an underpowered, unclustered test, carried by the supply secondary (statistical #6; policy-compliant via FLAG-teeth, but the test was weak)
Every tradable call tier's WR15 fell (85+ −6.9pp); "not statistically real" rested on naive z
~−1.3/−1.41. **Probe:** powered, date-clustered WR15 v72-vs-v73 re-read at current N. Also
covered obliquely by December H-reads; cheap to run earlier.

### A-15. [MEDIUM-LOW, VERIFIED-quotes] Regeneration integrity: the gate's replay anchors are invalidated, ship parquets went stale once already; plus the assess HEAD-pointer trap has no ship-day forensics (statistical #8 + code-path #5)
**Probe:** rebuild skill/feature parquets + re-run `trader assess --version v73/v74` explicitly;
diff vs frozen prose (largely piggybacks masterplan P1.D). Static check: grep ship tooling for
bare `assess` (research packs are insulated — explicit `--version` verified by the reviewer).

### A-16. [PROPOSED DECEMBER AMENDMENT] v70-vs-v74 OOS head-to-head (statistical #7)
The retirement chain was validated piecewise in-sample; H5 compares only v73↔v74. Both v70 and
v74 score daily (honest-era cadence), so a v70-vs-v74 OOS apex-EV/DD head-to-head in December is
nearly free and tests the CUMULATIVE retirement as one object. Requires §6 Amendment Protocol
sign-off (Sunday item), not a unilateral edit.

---

## 2. What held up under attack (balance)

- **v69 weekly transition blend: SOUND.** The code-path reviewer went in expecting residual
  look-ahead and found none — PIT-clean in all three consumers (live/batch/simulator), regime
  read as-of date. Residual: `kijun_pct`/`wv_force1` dampener inputs use the stale
  last-completed-week convention — conservative (can't manufacture edge), noted for hygiene.
- **Sim-vs-live entry timing: consistent** (both book signal-day close; the sim is not doing
  something the live engine doesn't — the shared assumption is A-11's subject, but there is no
  sim/live divergence).
- **Loss-side same-stock recycling: genuinely closed** by SL−70/dead-hold/same-symbol-block.
- **Research packs: insulated** from the assess version-resolution trap (explicit `--version`).
- **Flags-off inertness discipline** (bit-proven off-paths for the parked IV/gamma plugs) held
  up as claimed everywhere it was probed.

## 3. Commissioned probes — ordered by information-value per compute-hour

Tier 0 (done tonight): **P0** = A-0 lock capture ✅ (`fe129ad4a`).

**Tier 1 — statics/minutes, run on migration Day 0 alongside P0.B (before ANY certificate):**
- P1 barrier-cache drift sample (A-7) — ~5 min, read-only, widest blast radius per minute.
- P2 per-bucket simulator parity (A-12) — ~minutes, arrays already exist.
- P3 clustered-z re-screen of the five DD levers (A-2) — minutes-1h, machinery in-repo.
- P4 parquet mixed-version audit (A-13) + P5 bare-assess ship forensics (A-15) — static greps.

**Tier 2 — single wired MC pairs (1-3h each, nights 1-2):**
- P6 DH_POP_SLIP −0.03/−0.05 + popout non-fill (A-1) — THE keystone probe.
- P7 TP-fill-pinned-to-barrier (A-9).
- P8 entry-realism bundle: SLIP_ENTRY_OV / ENTRY_FILL_MISS_P / next-open arm (A-11a).
- P9 15-bar same-symbol cooldown (A-11b).

**Tier 3 — overnight batches (nights 2-4):**
- P10 IV_PREMIUM+IV_MODEL lever re-gates on 2020_crash/2022/5y (A-3).
- P11 window-perturbation + leave-one-year-out on RXDD/SVR/v74 deltas (A-4).
- P12 v74 lean-vs-full at N=500-1000 in-sample (A-5; = masterplan P1.C-3 pulled EARLY).
- P13 powered clustered v73 WR15 re-read (A-14). P14 capacity/ADV tape + capped arm (A-10).
- P15 regen-drift rebuild + explicit-version assess (A-15; piggybacks P1.D).

**Tier 4 — paper:** P16 multiplicity/alpha-spend ledger (A-8).

**Tier D — December-pack amendments (REQUIRE §6 protocol, Sunday sign-off, logged before Dec):**
- P17 leave-one-lever-out OOS lever efficacy (A-6). P18 v70-vs-v74 OOS head-to-head (A-16).

**Sequencing recommendation to fold into Sunday's ratification:** Day 0 = P0.B parity gate +
Tier 1; nights 1-2 = Tier 2 (+P12); nights 2-4 = Tier 3; THEN the E-tier N=2000 certificates
from masterplan §5 — certificates certify whatever survives the probes, not before. If P6 or
P10 flips a collapse cell, the certificate program re-baselines on the corrected engine
(that is the system working, not a crisis — mirror of the R6 pre-commitment).

## 4. Provenance

Three Opus reviewer transcripts (session-task outputs, ephemeral) merged 2026-07-17 by FABLE;
all A-0/A-1/A-2/A-3/A-5/A-6 load-bearing cites re-verified against source/git tonight (see tags).
Remediation commit: `fe129ad4a`. This file is the durable record; treat reviewer-quoted numbers
not tagged VERIFIED as pointers to check at probe time, not as established facts.
