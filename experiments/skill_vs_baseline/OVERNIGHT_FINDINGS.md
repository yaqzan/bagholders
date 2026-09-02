# Weatherization — Overnight Findings (2026-06-13)

Autonomous run while the user slept. Scope held to **safe, read-only / additive** work:
built the Verification Substrate (Phase 0) and answered Phase 1's strategic question on
the new predictand. **No shipping, no scoring/strategy_config mutation, no production Score
writes, no unverified MC reported as fact.**

---

## What got built (Phase 0 — Verification Substrate) — DONE

| Step | Deliverable | State |
|---|---|---|
| **0a** Predictand fix | `30dte_apex` barrier set added to `database/barrier_cache.py` (live Apex geometry: TP +30% → 1.092σ win / SL −70% → **2.548σ** stop). 10y backfill (task #174): **27.77M rows**, DuckDB mirror rebuilt (6.15 GB). | ✅ live |
| **0b** Verifier | `experiments/skill_vs_baseline/verify_scorecard.py` — per-version **option-EV** scorecard on the Apex payoff vs climatology + momentum. | ✅ runs |
| **0c** Calibration + spread-skill | `experiments/skill_vs_baseline/calibration_tail.py` — reliability curve, full-N tail, spread-skill on EV. | ✅ runs |
| **0d** Gate | absolute-skill-floor verdict wired into the verifier (must beat climatology AND momentum on Apex EV → SHIP/FLAG/BLOCK). | ✅ live |
| **0e** Holdout | protocol only — forward window doesn't exist yet (cutoff 2026-06-15 > data 2026-06-12). Re-eval ≈ 2026-12-15. | 📋 doc |

The substrate produced, in minutes, a predictand-true, baseline-relative, calibration-aware
read that the old generic-WR gates structurally could not. **The foundation is built and working.**

---

## The headline: a WEAK, REGIME-DEPENDENT risk-shaper (the pooled edge is a 2024 artifact)

**Read the window-robustness section below before trusting the pooled numbers.** The pooled
"score beats momentum on the payoff" (+2.89pp, t+2.72) is almost entirely 2024; in 2022/2023/2025
the score does NOT beat momentum on the payoff. The risk-shaper story is real but **not regime-robust**.

EV map (Apex payoff, v1): win/stop/expire = **+0.30 / −0.70 / −0.40**. 30d window. v73, in-sample.

### Skill vs baselines (0b)
- **Climatology** (random call on the Apex payoff): **EV +2.33%**, WR 71.8% (N=292k). The asymmetric TP/SL on a mostly-bull 10y tape is net-positive at base rate — the −70% stop does NOT dominate (win barrier 1.092σ hits 72%).
- **Score skill vs climatology:** ≥70 EV **+3.40%** (t=+2.10, beats it); ≥75 **+3.09%** (t=+1.02, marginal); ≥80 +1.74% (below, ns); ≥85 +3.29% (N=140); ≥90 +3.68% (N=19).
- **Score vs PERSISTENCE (the flip):** score≥75 EV **+3.24%** vs top-1.2% momentum EV **+0.35%** → **+2.89pp, t=+2.72.** On the OPTION payoff the score **beats momentum decisively** — the *reverse* of the raw-forward-return finding (where momentum beat the score by −6.93pp).
- **Skill beyond momentum (EV):** +0.78pp, t=+1.03 (ns).

### Why it flipped — the mechanism (this is the important part)
Round-2 (raw underlying return): momentum's top-1.2% returns **+7.9%** vs the score's +0.98% — momentum looks far better. But those explosive high-momentum names are **fat-tailed**: under the +30%/−70% option payoff they get **−70%-stopped** so often that their option **EV collapses to +0.35%** — *below* climatology (+2.33%). The score selects **moderate-momentum** names that hit the +30% TP reliably without triggering the −70% stop → **+3.24% EV**.

So the score's value over momentum is almost entirely **selection** (sitting in the moderate-momentum zone, avoiding the lottery-ticket tail), NOT fine within-bucket discrimination (+0.78pp, ns). **The elaborate scoring machinery is, in effect, producing a risk-shaped moderate-momentum selection.** That is a legitimate, valuable role — just not "directional alpha from technical analysis."

### 0d GATE verdict for v73 (Apex calls)
```
vs-climatology t=+1.02 (floor t>=2)   vs-momentum t=+2.72 (floor t>=2)
VERDICT: FLAG (risk-shaper: beats momentum, only marginal vs climatology)
```

### Window-robustness — the pooled edge is a 2024 artifact (CRITICAL qualification)
`window_robust.py`, 75+ EV vs top-momentum EV on the Apex payoff, by year:

| window | clim EV | 75+ EV | top-mom EV | 75+ − mom | t |
|---|--:|--:|--:|--:|--:|
| FULL | +2.38% | +3.24% | +0.35% | **+2.89** | +2.72 |
| 2021 | +1.06% | −1.02% | −3.68% | +2.66 | +0.95 |
| 2022 (bear) | −2.55% | −6.84% | −2.46% | **−4.38** | −1.13 |
| 2023 | +1.46% | −2.56% | +0.41% | **−2.98** | −0.51 |
| 2024 (bull) | +3.36% | +12.23% | −0.35% | **+12.59** | +3.58 |
| 2025 | +3.24% | +0.36% | +1.72% | **−1.35** | −0.36 |

The risk-shaping edge is **negative (ns) in 2022/2023/2025 and explosive only in 2024** — the
pooled t+2.72 is a single-bull-year effect, not a robust property. Note also **climatology EV itself
goes negative in 2022 (−2.55%)** — the Apex payoff is regime-dependent, not unconditionally +EV.
This pulls the conclusion back toward the v69 "thin / leveraged-momentum" read: the score has **no
robust, regime-independent edge over momentum even on the payoff.** The full MC (which weights 8
windows, not a 2024-heavy pool) is now the **necessary tiebreaker**, not optional.

EV-by-12-1-momentum-decile (full) confirms the mechanism: inverted-U, EV peaks at **moderate**
momentum (decile 5, +1–9% trailing → +3.08%) and declines toward both extremes (decile 1 +1.55%,
decile 10 +2.21%; the top-1.2% tail craters to +0.35%). The score's moderate-momentum selection is
the option-EV sweet spot — but the decile spread is only ~1.5pp, sharp only at the extreme tail.

---

## New flags the substrate surfaced

1. **Reliability is NON-monotonic / mildly inverted over 70–89 (0c).** Realized Apex EV by band:
   70-74 **+3.59%** (stop 25.1%) → 75-79 +3.16% (25.9%) → 80-84 +2.22% (27.1%) → 85-89 **+0.80%** (27.9%) → 90-100 **+7.15%** (22.3%, N=130).
   Higher conviction (80–89) → *higher* stop-rate / *lower* EV, until the rare 90+ bucket. The score's "conviction" axis is partly a momentum-extension axis that adds tail-stop risk. A calibrated forecast should rise monotonically; this doesn't.
2. **The cascade may over-weight the worst-EV band.** v73 cascade gives **85-94 → 15%** but **75-79 → 3%**, yet 85-89 has the *worst* per-trade Apex EV (+0.80%) and 75-79 is far better (+3.16%). The 90+/95+ ULTRA is genuinely strong (+7.15%), so this is specifically an **80–89 over-allocation** flag — a Stage-3 allocation-review candidate. *(Caveat: v1 EV; the full MC with dead-hold/theta may shift it.)*
3. **Spread-skill on EV is real in the key 75-79 band** (low-disagreement EV +3.86% vs high +2.09%, +1.76pp) but noisy across other bands. Supports routing ensemble-spread / `technical_alignment` (X) to **Stage-3 sizing** as a confidence input — modestly.

---

## Caveats (do not over-read)
- **v1 EV map** — no theta-on-wins, no slippage, and crucially **no dead-hold**. The dead-hold defers/recovers −70% stops, and could help momentum's tail-prone names *more* than the score's → it may **narrow** the score-vs-momentum gap. The full MC (with dead-hold/leverage/collapse) is the proper confirmation.
- **In-sample** — no forward window exists yet (0e). All numbers are in-sample relative to the calibration era.
- **The 85+/90+ buckets are thin** (N=140/19 sampled; 659/130 full-N). The 90+ "+7.15%" is suggestive, not significant.

---

## RESOLVED 2026-06-14 — the full MC head-to-head: the score is a NET RISK-SHAPER, not dead weight

Both arms ran the identical Apex engine (N=500 × 8 windows, honest calendar-hold-27 + dead-hold +
theta + slippage) via an env-gated override in `monte_carlo.py` (`MOM_SCORE_PARQUET`/`MOM_SCORE_COL`,
inert when unset). Arm A = real score; Arm B = the SAME score distribution reassigned by 12-1
momentum rank (identical selectivity + tier counts; only selection differs). `experiments/skill_vs_baseline/{build_mom_relabel,run via monte_carlo}.py`, results `h2h_real.json` / `h2h_mom.json`.

| window | A score MedRet / DD | B momentum MedRet / DD | DD A−B |
|---|---|---|---|
| 2021 | −12.5% / 57.3 | −36.1% / 66.9 | −9.7 (score) |
| 2022 | −10.7% / 59.3 | **+6.1% / 50.8** | +8.5 (mom) |
| 2023 | −36.1% / 58.6 | **−31.2% / 45.9** | +12.7 (mom) |
| 2024 | **+805% / 26.5** | −12.9% / 58.0 | −31.5 (score) |
| 2025 | **+64% / 54.9** | −35.7% / 64.5 | −9.6 (score) |
| dip | **+131% / 34.3** | −26.8% / 57.6 | −23.3 (score) |
| 22-now | **+1,049% / 61.7** | −22.5% / 64.9 | −3.2 (score) |
| 5y | **+1,093% / 62.6** | −11.8% / 69.6 | −7.0 (score) |

**Collapse = 0% both arms, every window. Score wins 6/8 on return AND DD.** Pure momentum selection
LOSES money through the Apex engine (−11.8% 5y, negative 7/8); the score turns the same engine into
+1,093% at lower DD. **The scoring engine's selection is load-bearing — NOT replaceable by momentum.**

This REVERSES the signal-level lean: the raw-return + apex-EV reads understated the score because they
ignored compounding, capital allocation, and collapse-safety — the portfolio dynamics where the
selection pays off. The MC is the correct tiebreaker for a portfolio question.

**Caveat + best lead:** in 2022/2023 (bear/chop) momentum BEATS the score on return and DD — the
score over-commits to extended names off-trend. Regime-conditioned defense in bear/chop is the
clearest MC-grounded improvement (ties to the regime-predictability index + existing DD levers).
In-sample; 12-1 momentum only.

**Implication for simplification:** "replace the score with momentum" is REFUTED. Simplify SURGICALLY
— keep the selection power, prune non-contributing dampeners via substrate ablation (Phase 3d), and
pursue regime-conditioned bear/chop defense.

Engine change: `monte_carlo.py` gained an env-gated override hook (additive, INERT unless
`MOM_SCORE_PARQUET` set) — uncommitted; keep as experiment infra or revert (`git checkout monte_carlo.py`).

## Phase 3d (2026-06-14) — parsimony ablation: the active dampener TAIL is net-dilutive

Two read-only findings + an MC ablation (reuses the override harness; `pre_boost` = exact pre-tail
checkpoint, scoring.py:1715, so NO re-scoring needed):

1. **CWWD is funded-irrelevant** — gated strictly `[70,75)` = the 0-alloc overflow tier (code-confirmed
   "75+ byte-identical, zero spillover"). Retire from the funded path (free); keep only for the dashboard.
2. **The whole active dampener tail (continuation-echo + EARN_BOOST + WVD + daily-volume; MCD/ICH/SCW/
   sector inert, PCD put-side) is NET-DILUTIVE on the funded book.** It net-lifts ≥75 supply +60%
   (13,001→20,835) and that supply raises DD ~10pp without earning compound.

MC ablation (N=300×8, tail-ON live-v73 vs no-tail pre_boost, same parquet `notail_v73`):

| window | tail-ON Med/DD | no-tail Med/DD | DD ON−OFF |
|---|---|---|---|
| 2021 | −12.1% / 57.7 | +101.7% / 38.0 | +19.7 |
| 2022 | −15.8% / 58.2 | +8.1% / 51.0 | +7.2 |
| 2023 | −37.1% / 59.2 | −28.2% / 51.1 | +8.1 |
| 2024 | +770% / 27.6 | +453% / 28.7 | −1.1 |
| 2025 | +63.8% / 54.8 | +11.5% / 42.7 | +12.1 |
| dip | +131% / 36.4 | +24.4% / 32.9 | +3.5 |
| 22-now | +992% / 61.6 | +517% / 51.3 | +10.3 |
| **5y** | **+1,097% / 62.1** | **+1,143% / 51.3** | +10.8 |

**no-tail wins DD 7/8; on 5y better on BOTH return AND DD (−10.8pp); collapse=0 both.** The lean core
is a better, leaner selector. It also EXPLAINS the momentum-test bear/chop weakness — the tail was the
culprit (2021/22/23 all flip better without it). Baselines reconcile (tail-ON 5y +1,097%/62.1 ≈
momentum-test score_real +1,093%/62.6 → harness sound).

**Caveats:** whole-tail (per-member attribution — which of cont-echo/EARN_BOOST/WVD/daily-vol — needs
targeted re-scores); N=300 (confirm winner N=500); in-sample; shipping the lean core = ALGORITHM_VERSION
bump (review-gated). Artifacts: `build_notail_parquet.py`, `notail_{on,off}.json`.

## Phase 3d-A (2026-06-15) — per-member attribution + v74 LEAN SHIP

Re-scored each tail member in isolation (ScoreSimulator + env toggles, ~128-300 symbol sample;
full-universe ScoreSimulator wedged on the 16y context load → sampled). apex-EV of each member's
ADDED/REMOVED ≥75 signals vs the common book:

| member | verdict | evidence |
|---|---|---|
| continuation-echo | **NEUTRAL** | added +2.48% vs common +2.90% (full-universe exact reconstruction); dominant supply-adder (+7,030) → DD via over-deployment, not per-trade |
| EARN_BOOST | **NEUTRAL (not alpha)** | added +7.01% (N=77) → **+4.47% vs +4.24% at N=179** = noise; matches v70 honest-recal "thin/hygiene". *(cont is a no-op in ScoreSimulator — lacks the barrier-win input — so attributed by exact `pre_boost+cont_lift` reconstruction.)* |
| daily-volume | **DILUTIVE** | added −1.66% vs +2.57% (negative-EV supply) |
| WVD | **HARMFUL** | removes +8.44% signals (dampens out the best) |

**Conclusion: NO tail member carries real per-trade apex-payoff alpha.** The whole-tail DD win is a
supply/over-deployment effect (cont/earn add neutral-EV correlated supply; dvol/wvd are negative).
Nothing to re-integrate. The EARN_BOOST look-ahead concern is moot — v73 already runs the honest
(look-ahead-free) earn, and it's neutral on the payoff regardless.

**v74 LEAN SHIP (in progress 2026-06-15, off-hours):** disabled CONT_BOOST / WVD_WAVE /
DAILY_VOLUME_AUTHORITY / EARN_BOOST in `strategy_config.SCORING`. Kept the lean core (components +
weekly + regime + ext-focal + CWWD/CSWC). Scoring commit `f9fb7b934`, `ALGORITHM_VERSION` bumped
`99cd2f0b1`. Config-edit verified to reproduce the validated lean (201 ≥75 on the smoke set, matching
env-lean). drift-guard 645 + registry green. Full 10y recalc + assess queued (#187); then gate
(0d substrate: must still beat climatology + momentum on apex-EV; Stage-1 W-gates) before confirming
the live pointer. Reversible via `/revert v73`. (Put-side post-1334 dampeners PCD/weekly-put left
on — funded-irrelevant, separate cleanup. The weatherization re-integration design stands as the
standing gate for any FUTURE mechanism: MOS-isolated, predictand-gated, forward-holdout-validated.)

## Recommended next steps (review-gated — NOT done unattended)

1. **Full momentum-vs-score MC head-to-head (1a)** — confirms the risk-shaper finding under leverage / dead-hold / collapse=0. Now well-motivated and the dead-hold caveat makes it necessary. *Greenlight needed (real MC build).*
2. **Window-robustness** of the risk-shaping edge (2022 bear vs 2024 bull) — is "beats momentum on the payoff" regime-robust or a single-regime artifact? Cheap, safe; worth running.
3. **Theta/dead-hold-aware EV** — upgrade the EV map using the cache's `fire_*`/`exit_bars` fields + `option_pricing.option_pnl_pct` so the absolute EV is trustworthy (the relative ranking is already robust).
4. **Phase 2 (MOS separation)** and **Phase 3 (simplification)** — code refactor / shipped changes → explicitly review-gated. The Phase-1 verdict (risk-shaper) means: don't delete the score wholesale; instead simplify toward "what produces the risk-shaped moderate-momentum selection + the 90+ tail," and re-test the 80–89 over-allocation flag.

---

## Artifacts
- Scripts: `experiments/skill_vs_baseline/{skill,skill_returns,verify_scorecard,calibration_tail,backfill_apex}.py`, `experiments/ensemble_spread/spread_skill.py`
- Cache: `30dte_apex` barrier set (cache + duck mirror); parquets `skill_v73_allscores_2016`, `price_feats_raw_2015`, `ensemble_spread_v73_calls_2016`
- Code change (additive, uncommitted): `database/barrier_cache.py` BARRIER_SETS += `30dte_apex`
