# Audit Probe Results — Saturday 2026-07-18 (old box, N=300 screens + statics + replay)

**Adjudicator:** FABLE. Companion to HOSTILE_REVIEW.md (attack ids referenced). All MC arms
N=300 paired seeds, canonical 12 windows, queue tasks #668-#673; statics #672/#674 +
foreground; replay = report-only (its own N-floor gate). Screen-tier caveats apply throughout
(N=300: ±5-8pp DD noise; compound noisier) — but the headline deltas below are 2-4 orders of
magnitude beyond the noise floor.

## 1. TP fill semantics (A-9) — CONFIRMED, the day's structural finding

| Arm | 10y ret | 5y ret | 22-now | 2024 | 10y DD | collapse |
|---|---|---|---|---|---|---|
| #668 production (overshoot credit) | +9,758.8% | +3,066.7% | +922.8% | +458.6% | 69.5% | 0 all |
| #673 honest limit (barrier-on-touch, open-on-gap) | **+7.4%** | **+20.7%** | −3.6% | +161.9% | 75.1% | 0 all |
| #670 barrier-pinned (lower bound) | −50.6% | −48.8% | −50.5% | +56.0% | 81.9% | 0 all |

The honest-fill arm sits near the harsh bound: **the shipped headline compounding is almost
entirely intrabar overshoot credit** — taker-side price improvement a resting maker limit
cannot earn. Only 2024 (momentum continuation) stays strongly positive honestly.
**Live-ledger mechanism VERIFIED:** `portfolio_engine.py:625` marks at daily close via
`option_pnl_pct` — the live book structurally cannot earn the credit, which mechanistically
explains the live-below-envelope gap (P0.E: −39.7% vs envelope p05 −19.4%). H3's
pre-registered engine-fidelity investigation has its candidate mechanism.
**Consequences:** (a) MC ABSOLUTE headlines = upper bounds until re-derived under honest
fills; A/B DELTAS remain usable (arms share semantics); (b) the TP/SL frontier (TP=+30 etc.)
was optimized under overshoot crediting → a TP/SL re-sweep under `TP_FILL_GAP_AWARE=1` is now
the most consequential pending sweep, ahead of ANY E-tier certificate; (c) knobs committed:
`TP_FILL_AT_BARRIER`, `TP_FILL_GAP_AWARE` (both default-OFF bit-inert).

## 2. Dead-hold popout spread (A-1, priced-fill half) — SOUND at −0.03

#669 (`DH_POP_SLIP=−0.03`) vs #668: DD +0.1..+1.5pp across all windows, collapse 0
everywhere, 10y ret −30% relative. Charging a 3% popout spread does not threaten the floor.
−0.05 arm skipped (low marginal info). **The non-fill half of A-1 remains untested** (needs
the dead_hold tuple extension; new-box item) — and the replay's market read (below) says the
model popout RATE is optimistic by ~5pp, so the non-fill arm stays commissioned.

## 3. Statics (P1/P2/P3)

- **P1 barrier-cache drift (A-7): small, real, balanced.** 97.9% exact vs the ≥99% bar;
  4/195 flips split 2/2; mean |Δexit| 0.13pp. Disposition: policy fix (periodic full rebuild;
  pin the substrate before December reads), not an emergency. Scripts + full tables:
  `p1_barrier_cache_drift.py`, task #674 log.
- **P2 simulator parity by bucket (A-12): HOLLOW.** Global 98.44% is carried by the neutral
  mass; tradable band: 75-79 = **67.4%** exact (mean |Δ| 1.17), 80-84 = **60.9%** (2.28);
  pooled ≥75 = 68.3% vs the 95% line. Calibration: v73's sim-derived +77% supply was
  production-confirmed at +54-61% — directions held, precision didn't. Rule going forward:
  ship claims cite PRODUCTION counts (research packs), never sim counts. `p2_parity_by_bucket.py`.
- **P3 clustered-z lever re-screen (A-2): confirmed where testable.** RXDD naive +14.6 →
  **clustered +0.57** (substitute tape; original gone); SVR null both ways; MWDD/TVDD/BDIV
  untestable parquet-only (cohort columns need MySQL context joins). **Second live A-13
  instance found:** `dd_ledger/tape_2020.parquet`+`tape_5y.parquet` silently overwritten with
  v71-scored tape mid-campaign (8 siblings v70). Full-context clustered re-screen = new-box
  P0; the December leave-one-lever-out amendment (Tier D) is elevated. `p3_clustered_z_levers.py`.

## 4. Real-priced replay (A-1/A-3/A-9 on MARKET data) — REPORT-ONLY, and it flips the story

Ledger: 1,661 v74 signals → 785 matched (47%; coverage 9%→91% early-2025→mid-2026),
liquid-primary 195 < the 800 floor → no flags evaluated, report-only. 17-month window,
crash-free — CANNOT speak to collapse/crash claims.

| Arm (close-grain twins, same trades/sizing) | Compound | MaxDD |
|---|---|---|
| MODEL-CG (linear const-delta, RV premium) | **−76.8%** | 80.3% |
| REAL-CG (actual contract prints) | **+76.3%** | 61.6% |
| intraday-grain model reference | −19.3% | 52.6% |

Supporting reads: real/model entry premium median **1.057** (RV premium level median-faithful;
tail-wrong: mean 1.68, elevated-VIX mean 2.9) · model popout rate 14.7% vs real 9.4%
(model optimistic +5.3pp) · per-trade real−model gap: calm +25.5% / elevated +10.3% /
panic +5.1% of premium (means).

**Adjudication:** on real prints the book OUT-earned the linear model — the missing-gamma
convexity deficit (documented in traps.md §4 as one of the two cancelling errors) measured on
market data for the first time, at portfolio level. Combined with §1: the production engine's
absolute numbers are wrong in BOTH directions (overshoot credit inflates; missing gamma
deflates), and the two roughly compensated. The honest 17-month market-data accounting of the
strategy is **+76% / 61.6% MaxDD** (this era, this population, report-only). This
independently corroborates the gamma-curve calibration program's premise (BS convexity > 
const-delta at daily grain) before its own pre-registered read runs. lastPrice staleness, if
anything, UNDERSTATES the real arm. Note: the live forward ledger marks positions with the
LINEAR model at close (`portfolio_engine.py:625`) — the replay's REAL-CG arm is arguably a
truer accounting of the live era than the live ledger's own marks.

## 5. Revised probe priorities (supersedes HOSTILE_REVIEW §3 ordering where they conflict)

1. **TP/SL frontier re-sweep under `TP_FILL_GAP_AWARE=1`** (+ entry-realism arms P8) —
   new-box, BEFORE any certificate (E-tier certificates of overshoot-credited numbers are
   certified artifacts).
2. **Gamma-curve Phase-1 + its December read** — unchanged plan, elevated confidence
   (market-data corroboration in §4).
3. Full-context clustered lever re-screen (MySQL joins) + December leave-one-lever-out
   amendment (Tier D — user sign-off).
4. Dead-hold NON-FILL arm (tuple extension) — the remaining A-1 half.
5. Barrier-cache rebuild policy + pre-December substrate pin (P1 consequence).
6. Production-count discipline for supply claims (P2 consequence, docs/process line).
7. Parquet read-time version guard (two live A-13 instances now on record).

## 6. ADDENDUM (same day, afternoon): honest-fill TP frontier — CLOSED at screen tier

Arms #680/#681/#682 (`TP_FILL_GAP_AWARE=1`, `TP_BASE_OV` ∈ {0.50, 0.75, 1.00}, N=300 paired)
vs #673 (TP=0.30 honest):

| TP (honest fills) | 10y ret | 5y ret | 2024 | 10y collapse |
|---|---|---|---|---|
| +30% (#673) | +7.4% | +20.7% | +161.9% | 0 |
| +50% (#680) | −61.6% | −31.2% | −8.5% | 0 |
| +75% (#681) | −80.6% | −48.4% | −44.0% | **100%** |
| +100% (#682) | −80.7% | −66.4% | −46.9% | **100%** |

**Verdict: the frontier is monotonically falling in TP — no fixed-TP level rescues the
passive-limit book on the LINEAR engine under honest fills; the shipped +30 is already the top
of a losing family.** Combined with §4 (real prints earned +76% at the same TP=30/close grain):
the strategy's real-world harvest runs through gamma convexity the linear model lacks, and the
sim's apparent viability ran through overshoot credit. **Program consequence (supersedes §5 #1's
framing):** the first substantive new-box sweep is NOT a TP re-tune on the linear engine — it is
the COUPLED-TRIO engine re-baseline (GAMMA_AWARE + real-IV premium model + honest fills, flags
together per the traps.md coupling rule, k=0.91 calibrated curve as the candidate correction),
and only then frontier re-tunes on top of physics that match the market. Certificates after
that. The December gamma read (Bars A/B) is the fidelity anchor for the gamma leg.

## 7. ADDENDUM 2 (capstone, #683): coupled trio + honest fills — the premium leg is the gap

`GAMMA_AWARE=1 + IV_PREMIUM=1 + IV_MODEL=1 (F2) + TP_FILL_GAP_AWARE=1`, N=300 paired: 10y
−60.8% / 5y −39.4% / 2024 **+100.8%** / collapse 0 all windows. It does NOT recover the
replay's real-print economics (sim-2025 −26.6% vs real-2025+ +76.3% on the same era). The
gap is attributable to the F2 premium leg: the per-trade round already proved F2's
premium-level is wrong on calm names (M1 FAIL — F2 median markup ~1.17× vs the replay's
measured real markup 1.057×); a ~6-10% median entry overcharge compounds ruinously. The gamma
leg visibly works (2024 is the best honest-fill result of the day).
**Corrected-engine spec, fully identified by today's evidence:** (i) honest fills — built
(`TP_FILL_GAP_AWARE`); (ii) gamma path — k=0.91 frozen, December-validated (Bars A/B); (iii) a
premium leg anchored to REAL prints (2025+ own panel; historical extension = a NEW calibration
target, adjacent to re-open condition (b) — **FABLE/user design decision, not licensed by this
screen; goes to the Sunday list**). Until (iii) exists, no sim absolute number — linear OR
trio — should be treated as a realizable estimate; the replay's real-print accounting is the
only market-grade number on record.

## Provenance

Queue tasks #668/#669/#670/#673 (MC arms), #672/#674 (statics), #675 (replay ledger);
scripts under `experiments/_audit_2026_07/` + `experiments/real_priced_replay/`; raw run
logs under `.codex/runs/`; replay full report `results/replay_results_20260718T103045Z.txt`
+ RESULTS appended to its DESIGN.md (incl. 6 build-time disclosed defaults, all reviewed and
accepted by the adjudicator 2026-07-18).
