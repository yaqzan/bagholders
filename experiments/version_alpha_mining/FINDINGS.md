# Overnight Honest Version-Mining → v71 — FINDINGS

**Session start:** 2026-06-01 21:46 ET. **Hard grind stop:** 06:00 ET. **Market open:** 09:30 ET.
**Active at start:** v70 (`c70d16d22`, honest EARN_BOOST pre7). Honest anchor: 75+ WR15 ≈ 50.5%.

## Time plan (ET)
| Window | Phase |
|---|---|
| 21:46 → 06:00 | Phase 1 — serial honest-recalc grind (~55-61 min/version → ~7-8 versions) |
| (alongside) | Phase 2 — watch for portfolio sl/tp params from the v69_portfolio_retune agent |
| 06:00 → 07:00 | Phase 3 — form v71 hypothesis from honest compare (Workflow candidate screen) |
| 07:00 → 09:00 | Phase 4 — ship v71 (if clean Stage-1) → today's row live; else keep v70 |
| 09:00 → 09:30 | buffer — confirm active version + today row live |

## Chosen version order (MINING-VALUE × v71-potential, de-risked by merge likelihood)
NOT by age or inflated WR. Front-loaded so the most valuable land first under the time box.

| # | ver | commit | mechanism | rationale |
|---|-----|--------|-----------|-----------|
| 1 | v43 | e083032 | MCD mcap dampener (calls 70-84) | structural call; prime v71 candidate; recent→auto-merge; pipeline-establishing |
| 2 | v37 | 6f9afda | PCD post-crash put dampener | structural put; puts are the weakest honest area (most room); clean-edge candidate |
| 3 | v27 | ad02704 | WCF weekly-confirm put floor lift | WEEKLY* top mining; put-side; older→attempt, skip+log if cherry-pick conflicts |
| 4 | v57 | e568b2f4 | direct Market Wave score transform | WEEKLY*; recent; was the v60 baseline; big deflation expected |
| 5 | v32 | 43eecea | CWCF call-side WCF mirror | WEEKLY* call dampener; miss-ledger z=+10.1 |
| 6 | v40 | 917659c | SVD score-velocity dampener | re-eval dropped gem (reverted "precautionary", per-trade evidence intact) |
| 7 | v44 | d8024b9 | ICH Ichimoku Kijun dampener | weekly-derived kijun (residual look-ahead); strong shipped mechanism |
| 8 | v17 | ea8b9fe | weekly scaling / momentum gradient | WEEKLY* foundational (oldest→attempt last) |

**Adaptive (if time):** v59 (volume authority, wv_force1 weekly-derived), v50 (SCW), v34 (CSWC, WEEKLY*),
v55 (market wave seed099), v33 (continuation boost), v39 (PESS put earnings).

**Deprioritized on honest evidence / redundancy:** v60 (== v69 honest, redundant_skip), v67/v68 VCBW
(v68 recalc'd → ~0 honest), v28/v35 EARN_BOOST (already mined → v70). v42 rolling-weekly carries a
STRUCTURAL revert (bypassed breakout-push bands), not just look-ahead — low priority / caution.

## Why this order
The look-ahead was a **weekly, mechanism-dependent** inflation. Two complementary bets:
- **WEEKLY\* mechanisms** (v27/v32/v57/v17/v44-kijun) — honest differs MOST → highest learning + tells us
  which weekly edges SURVIVE honest (a survivor = a real gem, the EARN_BOOST→pre7 pattern).
- **Structural mechanisms** (v43 MCD, v37 PCD) — deflate less → likeliest to retain a clean, ship-able
  honest edge → prime v71 material. Front-loaded #1-#2 to de-risk the pipeline (recent commits auto-merge).

---

## Honest results (filled as each version lands)

### TWO MORE recipe fixes discovered this session (beyond scores-only + conflict resolver)
4. **Pre-v46 feature pollution (`strip_undefined_scoring_imports.py`):** "take THEIRS"
   bundles v68-era feature map-builds (v44 ICH `build_kijun_pct_map`, v46 WVD
   `build_wv_force1_map`) into pre-v46 silos whose scoring.py predates them. These are
   DEAD (the compute call is OURS = version-original, never references them) but the lazy
   import fires at recalc runtime → `cannot import name` → **0 rows written** (py_compile
   does NOT catch it — v43's first recalc failed 778/778 then I caught it). Strip removes
   only undefined-in-scoring.py imports + their dead builds. Feature-aware (v44 keeps kijun,
   drops only wvf). **Now baked into setup_honest.sh.** A 1-stock dry-run before each full
   recalc catches any residual runtime breakage cheaply.
5. **NEVER read assess results until the assess background task COMPLETES.** A mid-flight
   5y run shows partial/transient values — I briefly read v57 75+ as 63.2% (panicked it was
   "still inflated") when the FINAL value was 51.3% (dead-on the anchor). Sequence per
   version: recalc done → launch assess (bg) → **await completion notification** → THEN
   compare/validate. The ~50-51% anchor IS the validator (plus the weight_info blend-keys
   check: `weekly_transition_t` present & dow-appropriate, e.g. 0.2 on Monday).

### Honest WR15 cluster (5y/30dte generic, post-assess — CLEAN)
| bucket | v57 | v68 | v69 | v70 | best |
|---|---|---|---|---|---|
| 95+ | 66.7 | 66.7 | 66.7 | **83.3** | v70 (tiny N=6) |
| 90+ | 62.9 | 60.5 | 60.5 | **66.7** | v70 |
| 85+ | **56.1** | 54.7 | 54.5 | 55.6 | v57 |
| 80+ | **52.2** | 51.9 | 52.0 | 51.7 | v57 |
| 75+ | **51.3** | 50.5 | 51.3 | 50.9 | v57≈v69 |
| 70+ | **49.4** | 49.2 | 49.0 | 48.9 | v57 |
| <25 | 41.7 | 41.7 | 41.7 | 41.7 | all tied |
| <15 | 48.2 | 44.7 | 44.7 | **49.3** | v70 |

The modern honest versions are a **tight ~50-51% cluster on 75+** (spread ~1pp = noise). v57
deflated 69.7→51.3 (−18.4pp), blend-keys verified (Mon t=0.2, wadj_partial≠completed). No
dramatic winner yet — the mining value is whether an OLDER version (v43/v37/v27/v32/v40/v17,
in flight) breaks UP from the cluster. Puts are honestly thin (<25 = 41.7%, clears 36.4% BE
by ~5pp; <5 is noise at tiny N).

### DECISIVE v71 z-test — no honest version beats v70 (incumbent)
Two-proportion z of each honest version's WR15 vs v70, per binding tier (computed on 8
honest versions [27,32,37,43,57,68,69,70]; updated as v44/v17/v59/v50 land):

| tier | best non-v70 z | verdict |
|---|---|---|
| 90+ | all NEGATIVE (max −0.3, v57) | **v70 best** |
| 85+ | +0.1 (v43/v57) | tie / noise |
| 80+ | +0.2 (v57) | tie / noise |
| 75+ | +0.2 (v57/v69) | tie / noise |
| 70+ | +0.7 (v57) | noise (≪ +3) |
| <25 | +0.0 (all tied) | tie |
| <15 | all NEGATIVE (max −0.3, v57) | **v70 best** |

**Max positive z anywhere = +0.7 (v57 on 70+), far below the Stage-1 W1 threshold of +3.**
A version-switch v71 — or "revert the post-v57 dampeners that look marginally better on
mid-tiers" — would FAIL Stage-1 W1 (the v57-vs-v70 mid-tier edge is +0.1 to +0.2 z = pure
noise). And v70 is the significant best on the high-conviction tiers (90+, <15) that drive
cascade allocation. **Honest conclusion forming: KEEP v70** — it is the honest best-available;
no historical mechanism stack beats it on per-trade WR15. Any real v71 would require a NEW
score-stage mechanism with a fresh z≥3 cohort edge (the earnboost-style hunt), which the thin
honest edge (75+ ≈ 51% vs 45% call BE) makes unlikely — consistent with the prior
"do-not-open-endedly-mine" conclusion. The mission blesses a correct keep-v70 over a forced ship.

### HONEST MISS-SWEEP (the v71 hypothesis hunt — user-directed)
Re-ran the miss-ledger methodology (`experiments/miss_ledger/` / `rqc_v60/`) but on **honest
v70** scores instead of inflated, holdout-locked ≤2026-05-15, mined on **option-TP15** (the
tradable barrier the growth gate uses). Harness: `experiments/version_alpha_mining/honest_miss/`
(build_ledger.py → mine.py / deep_mine.py; ledger `.cache/honest_miss/ledger_5y.parquet`,
15,294 honest call signals 70-89).

**Honest call-tier option-TP15 baselines:** 70-74 = 49.15% · 75-79 = 50.83% · 80-84 = 50.30%
· 85-89 = 52.21% (all clear the 45% call BE, thinly).

**CALL miss-sweep result — NO z≥3 cohort.** The strongest honest miss-drivers:
- **Mid-component 75-84 calls underperform** — macd/rsi/trend/ta/td all collinear, MID tercile
  optTP ~46.5% vs 50.83 marginal (75-79 z=−2.06/−4.4pp; 80-84 −5.0pp; combined 75-84 ≈ z−2.4).
  Mechanism: signals lifted to the tier via weekly/boost despite mid-range components. Interactions
  (macd∩rsi∩trend mid) don't concentrate it (collinear). **Below the Stage-1 W1 +3 threshold.**
- **70-74 high-vol_pct misses** (z−1.77) / mid-vol wins (z+2.16) — also sub-z3.
- The inflated ledger's headline driver **wadj_neg** (which seeded the v32 dampener at z=+10.1) is
  now **N=21** in honest 75-79 with negligible z — **it was largely a look-ahead artifact**, and
  v70 already dampens it (CWCF/CWWD).

**Interpretation:** v70's dampener stack (CWCF/CWWD/MCD/ICH/SCW/PCD/PESS) has already absorbed the
genuine miss-signals; on honest data the residual misses are ~noise (max |z|~2.2). This independently
corroborates the z-test conclusion: **no Stage-1-qualifying v71 from version-switching OR from a
fresh honest-miss cohort on the call side.** Put-side miss-sweep in flight (62,334 honest put signals).

### v71 CANDIDATE FOUND — WMPD (Weekly-Momentum Put Dampener)
The honest PUT miss-sweep (62,259 signals) surfaced the **only Stage-1-qualifying cohort** in
the entire sweep: in the **21-25 put tier**, signals with **w_mom ≤ −4** (deeply-negative honest
weekly momentum) have **optTP15 = 32.24%** (N=974) — **below the 36.4% put BE = EV-negative** —
vs the 39.21% tier marginal. Gate-style numbers:
- **W1 z = −4.46** (w_mom ≤ −4; sharpest of the threshold sweep) — clears the +3 pre-flight gate.
- **W2 both barriers:** option −6.2pp AND generic −4.4pp (60.6% vs 65.2%) — agree.
- **W3 1y/3y/5y:** all negative (2025 −2.0pp, 3y aggregate neg, 5y z=−4.1); only 2021 (+7, N=36, oldest, outside 1y/3y) flips.
- **Tier-specific:** z=−4.14 in 21-25 vs −1.0 (16-20) and −0.5 (1-15) — clean concentration on the weakest tradable put tier.
- **Mechanism:** late/post-**weekly**-decline puts that mean-revert (bounce). Complementary to PCD,
  which uses 10-day price-sigma and so misses slow multi-week declines — these survived in honest v70
  *with* PCD active, so w_mom is an independent discriminator.
- **Implementation** (mirrors PCD/PESS): a gradient put-lift in `compute_overall_score` gating on
  `21≤overall≤25 ∧ weekly_detail['w_mom']≤−4` (honest last-completed-week momentum), lifting toward ~28
  (out of the tradable ≤25 gate). Harness: `experiments/version_alpha_mining/honest_miss/`.

**Adversarial Workflow verdict: DOCUMENT (no-ship) — WMPD is real but immaterial on the funded config.**
- **REFUTE (is_real=TRUE):** survives jackknife (drop-top-5 symbols → z still −4.86; broad across ~250
  symbols), survives excl-2022 (−5.65pp, z−2.45, bootstrap P=0.008), NOT a score proxy (within-overall
  Stouffer z−5.43), holds on opt7/opt15/opt30 + generic, monotone dose-response. **Caveat:** the *absolute*
  EV-negativity is ~73% carried by 2022+2024 — excl-2022 the CI crosses BE, excl-2022+2024 it sits AT BE.
  Relative signal robust every year; absolute "below 36.4% BE" is vol-regime-dependent.
- **CALIBRATE:** best predicate **w_mom ≤ −4 AND macd ≤ 0** (z−5.07; macd>0 puts run 41% = EV-positive, must
  be spared). Gradient form: `overall += 1.0·clip((−w_mom−4)/(7−4),0,1)·(28−overall)` for 21≤overall≤25 ∧
  macd≤0. Clean tier-gating (1-15 z−0.80 NS, 16-20 z−0.66 NS — all damage isolated in 21-25). PCD-overlap is
  ~zero **by construction** (PCD already lifts 10-day-crash puts to ≥30, so the 21-25 ledger puts are
  PCD-survivors; w_mom catches the slower weekly decline PCD's 10-day window misses → genuinely complementary).
- **WHY DOCUMENT:** the funded 30 DTE config trades **85+ calls ONLY** — `PUT_TIER_ALLOC = {0.0, 0.0, 0.0}`,
  `TIER_ALLOC` 75-84 = 0.0. WMPD refines a **0%-allocation sleeve** → zero funded portfolio effect. A v71
  `ALGORITHM_VERSION` bump (full recalc + assess) is the heaviest action for zero payoff on a calls-only book.
  **Correct no-ship.** Params documented above — ready to ship in minutes IF/WHEN a put book is ever re-funded
  (carry as a Stage-3 candidate then, with a displaced-trade N=500×8 MC).

### The miss-sweep also checked the FUNDED tier (85+ calls) — clean, nothing to fix
The funded strategy trades 85+ calls (cascade ultra 95+ @0.13, top 85-94 @0.0975). The honest 85+ tier:
N=136, **optTP15=52.21% (7pp above the 45% call BE)**, and **NO miss-cohort** (every discriminator |z|<1.1;
worst −1.06 on tiny N=41). The 75-84 mid-component miss (z~2.4) and the 21-25 WMPD (z~5) are both on
**0%-allocation tiers**. → The tiers the strategy actually trades are already honestly well-calibrated.

### FINAL v71 DECISION: KEEP v70 — triangulated from three independent honest analyses
1. **Version-switch z-test:** no honest version beats v70 at z≥3 (max +0.7); v70 best on 90+/95+/<15.
2. **Honest call miss-sweep:** no z≥3 cohort in the funded 85+ tier (well-calibrated) NOR the unfunded 75-84
   tiers (max z~2.4, sub-threshold).
3. **Honest put miss-sweep:** WMPD real (z~5) but on the 0%-allocated put side → immaterial.
There is **no funded-relevant, Stage-1-shippable v71**. v70 stays active. The honest miss-sweep (the user's
idea) was the right hunt — it produced one real cohort (WMPD), documented + ready, and confirmed the funded
tiers have no fixable honest misses.

### CORRECTION (user): funded = 75+ calls (Apex) + HOLD strat (wide SL ~70-85%, exit day-15), not 85+/fast-recycle
The 85+ assumption + the opt15 (fast-recycle, tight-SL) barrier were both wrong for the intended Apex config
(per `experiments/v69_portfolio_retune/MASTER_FINDINGS.md`: "75+ calls, deploy at EVERY signal, HOLD ≫ CUT").
The hold strat NEVER early-stops, so the fast-recycle "misses" (the ~68% of losers that dip then recover to TP)
are hold-strat WINS. Re-mined the 75+ call tiers on a **hold barrier** (TP-reach 1.274σ within 15d, NO early
stop = `experiments/version_alpha_mining/honest_miss/build_ledger_hold.py` + `mine_hold.py`):

| tier | holdTP15 (held) | fast-recycle opt15 |
|---|---|---|
| 70-74 | 77.7% | ~49% |
| 75-79 | 81.3% | ~51% |
| 80-84 | 82.3% | ~50% |
| 85-99 | 87.2% | ~52% |
| **75+** | **81.9%** | ~51% |

- **75+ DEMOTE: no z≥3** (strongest scw_scalar −2.27, vol_pct −2.05). The 75+ deploy zone is uniformly ~81%
  holdTP and monotonically well-ranked (81→87% from 75-79 to 85+). Scoring already orders calls correctly for HOLD.
- **70-74 PROMOTE: none** — every 70-74 cohort is below the 75-79 baseline (best ~79% vs 81.3%); enabling 70-74
  overflow is a cascade-allocation choice for the Apex agent, not a scoring fix.
- **Corroborates the hold thesis:** the same 75+ signals that look like ~50% coin-flips on the fast-recycle
  barrier hit TP ~81% when held through the dip — the early-cut was the bleed (matches MASTER_FINDINGS "HOLD ≫ CUT").

**LOSS-MAGNITUDE / EV re-mine (user: "count miss reasons again on the hold strat"):** binary TP-reach hides
loss magnitude, so re-mined the deep-loss (wide-SL-hit, MAE_sigma≥3.09) rate per cohort + a hold EV proxy
(`build_ledger_hold_mag.py` + `mine_holdmag.py`, ledger `.cache/honest_miss/ledger_holdmag_5y.parquet`).
- 75+ hold EV uniformly positive + monotone: 75-79 +13.5% / 80-84 +14.6% / 85+ +19.9% (TP 81-87%, SL-hit 8-13%).
- **Worst 75+ cohort = mid macd/rsi/trend** (collinear mid-conviction): EV +11.85% vs +14.10% base, deep-loss
  15.6% vs 12.6%, **SLhit z=+2.41** — the SAME cohort that's z≈−2.4 on the fast-recycle barrier (real, reproducible).
  But still **EV-POSITIVE** (+11.85%) and **z<3** → fails W1; for Apex (DD-tolerant, count-hungry) demoting an
  EV-positive cohort is counter-thesis (it'd be a Sentinel/Core portfolio-stage DD-shaver, not a scoring ship).

**FINAL (triangulated on the CORRECT barrier + tiers, both binary AND magnitude lenses): KEEP v70.** No
funded-relevant z≥3 scoring v71 exists on either barrier or lens; v70's scoring is well-calibrated for the Apex
75+ hold strat. The miss-sweep's payoff was confirming the funded scoring is clean — the look-ahead removal
already extracted the real signal. The mid-conviction cohort (z~2.4 both lenses) is the only recurring near-miss
— a documented DD-lever for a low-DD profile, not Apex alpha.

### Cluster confirmed (10 honest versions: 27,32,37,40,43,44,57,68,69,70)
Version-switching is exhausted: 75+ WR15 cluster 48.3-51.3, v57/v69 best (51.3), v70 50.9; v70 best on
90+/95+/<15. No version beats v70 at z≥3 (max +0.7). (v17 skipped — oldest, awkward kijun/wvf pollution
form; immaterial to the conclusion. v59/v50 adaptive in flight.)

_(per-version honest-vs-inflated detail in compare_honest.py / honest_versions.json)_

### Anchor (already honest)
- v68 = 50.5 (75+ WR15) — clean template, −17.1pp deflation from inflated 67.6.
- v69 = 51.3 (weekly blend, honest by construction).
- v70 = 50.9 (honest EARN_BOOST pre7).

---

## VALIDATED MECHANICAL RECIPE (refined this session — handles cherry-pick conflicts)
The LAUNCH_PROMPT recipe needed two fixes for pre-v68 versions:

1. **`--scores-only` does NOT exist on older `trader.py`** (it parsed as a symbol →
   recalc'd 0 stocks). **Universal scores-only recalc = call the function directly,
   bypassing the CLI tail**, from the worktree (cwd auto on path → worktree's code):
   ```
   python -u -c "from database.models.core import AlgorithmVersion as A; \
     assert A.get_or_create_current().id==NN; \
     from recalculate_scores import run, DAILY_COMPONENTS; \
     run(symbol=None, days=2400, components=DAILY_COMPONENTS.copy(), force=True)"
   ```
   This writes only score rows (no historic/assess tail; tail = cold worktree cache).

2. **The v69 cherry-pick CONFLICTS on every version > 1 step back** (only v68 auto-merged).
   Conflicts are in `core.py` (the 4 weekly sites) and sometimes `simulator.py`.
   - `simulator.py` → take version-original (`git checkout HEAD -- simulator.py`); the
     recalc path is `core.py:recalculate_scores_batched`, which never uses `ScoreSimulator`.
   - `core.py` → `resolve_weekly_conflict.py` (v2): **take THEIRS for clean v69 additions**
     (R1 pit-map build, R2 W-1/W-2 shift, R3 pit extraction, weight_info keys); **take OURS+
     inject the 4 pit kwargs** for the `compute_overall_score(...)` call (THEIRS uses a
     `_score_args`/`_score_kwargs` refactor that postdates these versions); **take OURS(empty)**
     for the single-row duplicate re-call.
   - **MUST verify R1/R3/R4 each appear ≥2× in core.py** (calc_batched + recalc_batched). The
     v1 resolver "take OURS" silently DROPPED R1 (empty-OURS additions) → `_pit_weekly_map`
     unbuilt → runtime NameError that `py_compile` does NOT catch. R1=R3=R4=2 is the gate.

3. **CONTAMINATION LANDMINE (the gate earns its keep):** every version's checked-out commit
   leaves `ALGORITHM_VERSION` pointing at the PREVIOUS version (v57's said `c6f384ab`=v56,
   v43's said `200f33a`=v39). Pin the version's OWN commit + assert `get_or_create_current().id
   == NN` BEFORE recalc. The setup-script conflict-exit originally skipped the pin → the gate
   caught id=56 on the "v57" recalc. **Never trust the file; always pin + assert.**

Tools: `setup_honest.sh <vid> <commit>` (worktree→pin→cherry-pick→resolve→verify R1-R4→gate),
`resolve_weekly_conflict.py` (v2 conflict resolver), `honest_recalc` inline (direct `run()`).
Per-version wall-clock ≈ 42 min recalc (894 stk, ~2.6s/stk) + ~5 min assess(5y).

---

## CORRECTED FINAL (ET 05:47, 2026-06-02) — grind closed at 15 honest versions, KEEP v70

Honest registry (15): 27,32,37,40,43,44,46,50,52,57,58,59,68,69,70 — all look-ahead-free recalc'd (2400d) + 5y/30dte assessed. Every version clusters **48-52% on 75+ WR15** (the honest anchor). v70 best on high-conviction tiers (95+ 83.3, 90+ 66.7, <15 49.3) that drive the Apex 75+ HOLD sleeve.

**Decisive z-test (14 versions × 7 binding tiers vs v70): MAX positive z = +0.69** (v52 70+, 49.4 vs 48.9). Nothing clears +3. **No Stage-1 scoring v71 exists. KEEP v70.**

### v58 false-alarm — the mid-assess artifact (LESSON)
v58's first compare read showed 75+ = **61.6% / N=5119 / z+16.49 "SHIP"** — a uniform +10-17pp lift on EVERY tier, matching v58's *inflated* value. This was a **mid-write transient**: my watcher fired on the WR pass's `RTR Corr` line while the assess was still finalizing the result row. The completion check must wait for the assess **process to EXIT** (count=0), not for `RTR Corr` text. The finalized v58 run = **51.4% / N=1338** — fully deflated, dead-center in the cluster. Same trap I hit reading v57 at 63.2% mid-flight earlier. **RULE: never read an assess result until its process has exited.** (Deflation proof: v57 N=1337/51.3, v58 N=1338/51.4, v59 N=1660/51.5, v70 N=1851/50.9 — v58's blend fired and deflated identically to its siblings; no residual continuation-echo look-ahead after all.)

**Live state at market open:** v70 active, unchanged. Monday 06-01 scores are the actionable pre-open signals. No v71 shipped (correctly — none earned it).
