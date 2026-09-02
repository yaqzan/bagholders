# Era-Conditioning Program — Pre-Registration

**Owner:** FABLE · **Locked 2026-07-08, before any ramp statistic was computed.**
**User hypothesis (2026-07-08):** agentic AI (2025+) put GEX-style setups in many hands; follow-through
crowds the pattern and makes it self-fulfilling via hedging/copycat clustering → test GEX as a
"2025-and-later" signal and evaluate future ranks against it.

## Honest prior (stated up front)
- The GEX campaign's panels were ALREADY 2025-02 → 2026-07 — the per-name money-NULL is an IN-ERA
  result. The crowding hypothesis does not resurrect the dead cells; its live predictions are:
  (P1) effect sizes should RAMP (strengthen monotonically) within the era as adoption grows;
  (P2) the FORWARD window should be the strongest — accumulate honest forward ranks and re-test.
- Crowding amplifies PINNING (movement suppression), which is adverse for long calls. Prediction P3:
  if crowding is real, GEX's usable form for this book is AVOIDANCE (suppress calls into dense
  overhead gamma), not entry. The in-era stats already point this way (local_density → mfe15
  t_clust −4.7).
- The strongest confirmed, replicated, UNSHIPPED in-era edge in hand is OSK (opt_skew): +0.114 (75+)
  in-era, cross-vendor replicated, regime-conditional (absent 2022-24). A "2025+-aware" version sweep
  leads with OSK, with pinning-avoidance secondary. GEX-entry forms stay closed.

## Phase A — Adoption-ramp test (existing parquets only; runs today)
For the three strongest in-era cells, compute effect size in ROLLING 6-month windows stepped monthly
across 2025-02 → 2026-06 (in-sample only, ≤ cutoff 2026-06-15; leak-guarded):
  A. OSK: spearman(skew, pnl15) + clustered OLS beta — iv_ledger_polygon discovery slice.
  B. Pinning: clustered OLS beta of local_density → mfe15 (controls: overall, stock_r20, vol_pct) —
     gex_features × rs_ledger.
  C. Cohort timing: clustered OLS beta of mkt_gex_ratio → cohort_net_path (DD-lever controls) —
     the spy_gex_test A2 panel assembly.
Trend metric (pre-registered): OLS slope of |effect size| vs window index + sign of slope + count of
windows where |t| ≥ 2. **RAMP-SUPPORTED = positive slope AND last-third windows uniformly stronger
than first-third for ≥2 of 3 cells. FLAT/DECAY = anything else.** Windows overlap (6m step 1m) —
report slope nominal, no significance claim on the slope itself (autocorrelated by construction);
this is a direction read, not a gate. It calibrates the PRIOR for Phases B/C; it ships nothing.

## PHASE A RESULT (2026-07-08, ramp_results.json) — pre-registered label: **FLAT/DECAY overall**
Cell A OSK: **RAMP** (12/12 windows; |beta| first-third 0.032 → last-third 0.083; all |t_clust|≥2
windows in the back half; |spearman| 0.062→0.093 agrees). Cell B pinning: FLAT_DECAY (V-shape, no
monotone ramp). Cell C cohort-timing: INSUFFICIENT_DATA (last-third bins unfillable; partial
trajectory rose 0.02→0.21 but 0 windows |t|≥2 — no claim). **Posterior: the user's broad
self-fulfilling-GEX mechanism is unsupported in-era; the strengthening signal is OSK specifically.
Phase C proceeds OSK-led with an upgraded prior; pinning stays secondary with a weakened prior;
Cell C's question transfers to the Phase B forward re-reads.**

## Phase B — Forward rank ledger ("test future ranks against it")
`experiments/era_conditioning/build_forward_ranks.py` — IDEMPOTENT catch-up design (no daily-run
requirement): on invocation, finds trading days since last ledger date with chains in option_prices,
computes per-name dealer_gex features (existing module) + our-recipe skew, writes date-stamped ranks
to `.cache/era_conditioning/forward_ranks.parquet`. Evaluation is BANKED, not continuous:
pre-registered re-reads at N ≥ 60 forward trading days (~2026-10) and N ≥ 120 (~2027-01): do the
2025-26-confirmed cells hold on data that postdates this design? Optional later: wire an off-market
queue invocation into the post-market flow (chip; NOT a production edit now).

## Phase C — v75 candidate sweep (the "new version" directive)
Stage-1 scoring hypothesis ⇒ per CLAUDE.md this runs in a WORKTREE (`algo-exp/osk-era-layer`) through
the real ship gates (W1-W6, real supply via signal_supply.py first, holdout-locked, growth gate),
sequenced AFTER Phase A reads out (its result sets the feature list's prior, not its permission):
- Lead feature: OSK opt_skew as a score modifier on option-covered names (era-aware: active only
  while a ROLLING REGIME GUARD holds — trailing 6m spearman(skew,pnl15) ≥ 0.03 on live data;
  guard breach ⇒ modifier decays to neutral. Guard metric computed from our own chains, defined
  BEFORE the sweep).
- Secondary: pinning-avoidance dampener (suppress call boost when local_density high) — Track B
  closure does NOT bar this: closure covered GEX ENTRY/selection mining; a risk-avoidance dampener
  with the user's explicit re-open directive + new crowding rationale is a distinct claim. It still
  must clear the same gates as any dampener.
- Supply reality: skew/GEX computable only where chains exist (~776 symbols, coverage from 2025-02) —
  W-gate supply numbers must come from REAL coverage, not fallback (the growth-gate supply trap).
- Explicitly out: any GEX entry/level/regime feature as a scoring input (closed, twice).

## Sequencing
1. Phase A agent — today (no MySQL, parquets only). [DONE — FLAT/DECAY, OSK-only ramp]
2. Phase B builder — after A. [DONE — forward_ranks.parquet live from 2026-06-16; re-reads ~Oct/Jan]
3. Phase C worktree kickoff. [RUN AND CLOSED 2026-07-08 — **BLOCK**]

## PHASE C FINAL (2026-07-08): v75 OSK modifier BLOCKED at the faithful path.
Evidence (experiments/osk_era/, moved to MAIN; worktree removed): overlay reconstruction bit-exact;
WR15-primary null (promoted-vs-demoted t −0.27/−0.32); pnl15-secondary REVERSED vs the naive sweep
(+0.52/t2.51 → −0.41/t−2.19) — tier-boundary discrimination is not recipe-robust (our-skew vs
polygon-skew row corr 0.199). B3 fallback: no rescue. Lesson (again): global-rank residual ≠
boundary-local score modifier. OSK remains a confirmed in-regime per-trade residual; the live lead
moves to the PORTFOLIO stage (Stage-3 skew-based allocation tilt within qualified holdings — SVR
precedent), gated T1-T7 N=500 if ever pursued.
