# Peak-Fakeout Discriminator — FINDINGS

**Date:** 2026-07-15 (overnight /research run, ~02:15–03:30 ET, weeknight budget ~7h)
**Verdict:** **COMPREHENSIVE NULL at the pre-registered bar — 0/131 cells clear all 6 legs. NO SHIP.**
The recurring user intuition — "buy signals at the top of rallies that then plunge are fakeouts
some metric (mcap / volatility / EMA / MA) could filter" — is answered at the **interaction**
level (conditional on peak-state), completing the marginal-level closes in `retrace_entry_v70`,
`trend_ma_lattice`, `divergence_dampener`, and `verify_value`.
**Substrate:** funded v74 75+ CALL ledger N=5,810 (697 symbols, 2021-01-04→2026-06-08 after the
44-row OHLC-coverage drop; holdout cutoff 2026-06-15 enforced). Base apex WR 70.07%, plunge
(SL-touch, apex_ev=−0.70) 23.25%.
**Contract:** `PREREGISTRATION.md` (locked 02:30, before any feature was computed). Harness:
`prep_lookups.py`, `features.py`, `mine.py` (5 self-tests incl. look-ahead truncation, all PASS;
built by a Sonnet subagent, audited by the orchestrator).

## The three structural results

### 1. The peak STATE itself is (again) not a dangerous cohort

| state | definition (post-ladder, prevalence-tuned pre-outcome) | N | apex WR | plunge |
|---|---|---:|---:|---:|
| ALL funded | — | 5,810 | 70.07% | 23.25% |
| P_high | close within 1.5% of 126d high | 1,906 | 68.84% | 23.66% |
| P_run | 20d run-up ≥ 1.25σ (σ60) | 875 | 67.54% | 24.34% |
| P_both | both | 558 | 67.74% | 23.48% |

"At the top of a rally" costs at most **−2.5pp WR / +1.1pp plunge** — economically tiny, and the
cohort sits 22pp above call break-even (~45%). This is the third independent confirmation
(after run-position and late&extended in `retrace_entry_v70`) that peak entries win at ~base
rate. The remembered plunges are the visible left tail of a 70%-WR population — salience bias,
not a separable class.

### 2. No feature separates a fakeout cohort inside the peak state

131 cells scored (12 features × buckets × 3 peak-states + unconditional controls). Zero
FINDINGs. Best per family (max |z_clust| over WR/plunge, any state):

| feature | best \|z\| | read |
|---|---:|---|
| F8 vol_z (entry volume) | 3.68 | U-shape (mid-volume worst, low best); **2021 sign-flip** (`+----`) — regime-coupled, Psign 0.58-0.67 |
| F6 high_zone_age | 3.65 | camped >10d at the high: −17.6pp WR — but **N=82** (1.4% of book), one window unresolved, chance-plausible at 131 cells |
| F4 climax_day | 3.40 | strongest honest near-miss, see §3 |
| F1 pit_mcap_b | 3.29 | MID-cap at peaks is *better* (+6.6pp), non-monotonic, **2024 flip** (`+++-+`) |
| F3 parabolic | 2.81 | see §3 |
| F2 vol20 (realized vol — user's "volatility") | 2.73 | Psign 0.53, 0 legs — **null** |
| F12 VIX band | 2.16 | null |
| F10 runup60 (medium-term extension) | 2.14 | null |
| F9 days_to_earnings (call-side, genuinely open pre-run) | 1.84 | **decisively null** — earnings proximity does not mark peak fakeouts |
| F5 up-day streak | 1.60 | null |
| F7 base_days | 1.50 | null (see engineering note) |
| F11 trend_dominant | 1.34 | null |

(User's four spitballs: mcap → non-monotonic window-flipper; volatility → null; EMA/MA → excluded,
closed 2026-07-14 by `trend_ma_lattice`.)

The multiplicity picture: 13 cells show |z_clust|≥3 vs 0.35 expected — but they collapse to ~5
independent loci (peak-states are nested so the same rows appear up to 4×, and WR/plunge are
near-mirror outcomes). A real low-grade texture excess exists; **every locus fails window
stability** (Psign < 0.90), the substrate's standing law.

### 3. The near-miss cluster — "climactic acceleration into a strong run" (parked to Dec-2026 OOS)

Within P_run, the *shape* of the run matters directionally, with all-window-consistent observed
signs but insufficient posterior confidence:

| cell | N | ΔWR pp | Δplunge pp | z_wr | t_ctl | signs | Psign | legs |
|---|---:|---:|---:|---:|---:|---|---:|---|
| P_run ∧ climax_day T3 | 474 | −4.9 | +1.0 | −3.40 | −3.40 | `-----` | 0.76 | 5/6 (fails Psign only) |
| P_run ∧ parabolic T3 | 194 | −9.3 | +3.5 | −2.81 | −2.74 | `-----` | 0.87 | 4/6 |
| P_run ∧ vol_z T2 (mid) | 342 | −6.4 | +6.7 | −3.18 | −3.19 | `+----` | 0.58-0.67 | 5/6 |
| high_zone_age >10 (uncond.) | 82 | −17.6 | +17.0 | −3.49 | −3.58 | `--.--` | 0.44-0.59 | 2/6 |

**Why this is NOT actionable even if real:**
- The worst texture cohort still wins **~65%** — far above BE. There is no below-BE "fakeout
  cohort" to gate out; a dampener would delete profitable supply (the trend_ma_lattice /
  regime_reweight supply-trap, and verify_value's "nothing predicts apex above the gate").
- F4's excess losses arrive via **expiry (−0.40), not SL-touch** (Δplunge only +1.0pp): climactic
  entries drift dead more often; they do not "plunge" more. The user's plunge archetype is not
  concentrated in any found cell.
- As a sizing tilt it would touch ~8% of funded flow at −5pp WR — far weaker than the retired
  DD levers, and the sizing-lever well is documented DRY (G23).

**Locked for the Dec-2026 OOS unlock (one-shot read, candidate only, defined NOW):**
`TEXTURE := P_run(runup20/σ60 ≥ 1.25) ∧ (climax_day ∈ global-T3 ∨ parabolic ∈ global-T3)` —
re-evaluate on OOS ≥ 2026-06-15 at the same 6-leg bar (windows = OOS quarters). Expect ΔWR ≤ −4pp
with stable signs to even discuss a mechanism. The F8 mid-volume U and F6 high-zone-age ride
along as secondary reads only. Do not touch before the unlock.

## Engineering notes (for reuse)

- `mine.py` generalizes the trend_ma_lattice machinery to arbitrary per-signal features and ADDS
  the date-cluster-robust (CR1) z the lattice lacked — naive z_raw overstates significance on
  market-synchronized cohorts; future per-signal feature mines should adapt THIS harness.
- **Near-constant buckets blow up the CR1 t** (F7_base_days T2 within P_run: t=+23 on a +0.37pp
  effect; F11 similar): when a bucket ≈ the whole subset, the sandwich variance degenerates. The
  gate was not fooled (z=n/a → leg 1 fails) but top-20 rankings that fall back to |t| are polluted
  — read `verdict`/legs, not raw t. Fix if reused: require bucket share ∈ [5%, 95%] of subset.
- The raw "13 z≥3 hits vs 0.35 expected" is NOT 13 findings: nested peak-states duplicate rows up
  to 4× and WR/plunge mirror each other. Count independent loci before concluding structure.
- prep_lookups bypasses `_load_earnings_by_symbol`'s `EARN_BOOST_ENABLED` gate (False in v74),
  which would have silently emptied the earnings lookup.

## Artifacts

- `experiments/peak_fakeout/{PREREGISTRATION.md, prep_lookups.py, features.py, mine.py,
  SUMMARY_full.txt, SUMMARY_smoke.txt}`
- `.cache/peak_fakeout/{lookup_mcap,lookup_earnings,lookup_vix,features_v74_full,
  cohort_stats_v74_full}.parquet`
- Queue task #634 (exit 0, ~2 min).
