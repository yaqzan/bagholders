# FINDINGS -- w5dte_ev (EV study for the W5DTE rule family)

Date: 2026-08-18. Prereg: PREREG.md (locked 01:45 ET pre-data; amendment a =
full-precision thresholds). Engine: ev_study.py (5/5 self-tests, incl. exact-count
fidelity vs the parent's discovered rules and an independent pure-python pricing
cross-check). Population: the parent's 1,341,534-row analysis population; FAMILY
hits N=58,057. Tables: RESULTS.md + B:\polygon_derived\weekly_5dte_movers\ev\.

## Verdict: PASS on the pinned gate -- the selection effect is real, and it is a
## TAIL-EXIT, TECH-CONCENTRATED, YEAR-LUMPY lottery edge

**Pinned gate (FAMILY, TP-5x): EV = +2.39% per premium dollar, beats 100/100
exposure-matched control draws (control mean +0.43%, p95 +1.31%), EV > 0. PASS** --
the paper tape is licensed per OWNER_SPEC lock #3.

The honest shape, from the full grid:

1. **The edge lives in the tail.** TP-10x is the best policy (+9.49%, 100/100 vs
   control +2.92%). TP-5x is thin (+2.39%). TP-2x and TP-3x are NEGATIVE and WORSE
   than control (TP2: -11.3% vs control -6.1%, 0/100) -- capping these contracts at
   2-3x forfeits the tail that pays for the ~80% of positions that die. Scalping
   this family low is anti-alpha.
2. **Hold-to-expiry does NOT beat exposure.** FAMILY expiry EV +5.83% but only 40/100
   vs control -- the hold return is mostly market exposure; the SELECTION premium is
   captured by high sell-limits (5x-10x), i.e. the family finds contracts whose paths
   SPIKE, and much of the spike is gone by settlement.
3. **Tech carries everything.** R1 (tech, non-OPEX): TP5 +10.9%, TP10 +26.9%, expiry
   +33.9% -- all 100/100. R2/R3/R4 (tech variants) similar. The non-tech-conditioned
   R5/R6: TP5 only 9/100 vs control, expiry 0/100 (WORSE than control). The
   family-minus-tech residual has no control-beating EV.
4. **Year-lumpy: 2024 was negative at every policy** (TP5 -13.0%, expiry -19.6%),
   2022 TP5 -8.8%; positive years 2023 +6.1% / 2025 +8.0% / 2026H1 +12.7% at TP5.
   3/5 years positive at the primary policy (the prereg made per-year robustness
   reportable, not gating -- reported here plainly: a real strategy on this family
   eats full drawdown years).
5. **Lottery variance:** win rate 21.3% at TP5 (14.3% at TP10), median trade -100%.
   Mean edge per premium dollar is thin; any real sizing would be tiny-Kelly.
6. **Capacity:** FAMILY hits' median entry dollar volume $38.9k/day (p10 $6.0k);
   39.7% below $25k/day; median entry premium $0.48. Feasible for small clips on the
   upper half; binds quickly at scale -- consistent with every capacity read this
   repo has made.

## Amendment 2026-08-18b -- REALISM HAIRCUT (minute-level realizability read)

experiments/w5dte_minute_real/ (prereg-locked) re-priced this study's TP fills against
the minute tape. The touches are real (97.9% pass a 1-lot bar; only 5.5% lone prints;
the family's touches are MORE tradeable than its controls') -- but the EV is a thin
residue and cannot afford to lose fills: **under small-clip gating (R2: >=5 min AND
>=10 contracts at the level) FAMILY TP-5x EV flips to -7.9% and falls BELOW its gated
controls -- the pinned realizability gate FAILED.** The PASS above stands only under
its own daily-bar fill convention. Corrected practical claim: **the edge survives
realistic exits only at 1-lot x TP-10x scale (+7.9% R1-gated, above all gated
controls); it does not survive 5-10-contract clips at the exit print.** Exit-print
capacity, not entry capacity, is the binding constraint. Full anatomy:
experiments/w5dte_minute_real/FINDINGS.md.

## Caveats (carried, not resolved)

- Daily-bar TP fills assume the limit executes when the day's high touches the level
  -- the lone-print risk is untested here (that is exactly the minute-realizability
  follow-up in OWNER_SPEC's continuation list; minute_aggs exist on B: through
  2026-08-05). Mid-entry-free is generous on wide contracts; identical in both arms,
  so the CONTRAST stands even where absolutes flatter.
- In-sample era 2022-08..2026-06-12 only; no crash-regime evidence exists or can
  exist (archive floor). L3-flip humility applies to the absolute EVs.
- Expiry-settle null share 3.07% (settled 0, conservative).

## Disposition

- **Paper tape: BUILD (licensed by PASS).** Live-data reality check: the faithful
  conjuncts (contract H-L range, transactions) are UNOBSERVABLE in the owned live
  source (OptionPrice = daily price/volume/OI snapshot; volume 79%-zeros trap), and
  the OPRA archive died 2026-08-05. The tape is therefore built as a PROXY-FIDELITY
  instrument: violence conjunct proxied by |close-to-close contract move| calibrated
  on the archive against the true hl_range threshold (agreement stats in the tape's
  FIDELITY.md), outcomes proxied by max subsequent daily price snapshot (a LOWER
  bound on the true week high). Every deviation is quantified; the tape is paper-only
  and disposable if the owner rejects the proxy.
- NEW_LEADS "W5DTE" updated: EV verdict merged; next-step ladder = minute
  realizability (touch->capture), then capacity/clip absorption. No production,
  scoring, or portfolio change proposed or made.
