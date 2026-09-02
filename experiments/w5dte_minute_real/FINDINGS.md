# FINDINGS -- w5dte_minute_real (minute-level realizability of W5DTE TP fills)

Date: 2026-08-18. Prereg: PREREG.md (locked pre-data). Engine: minute_real.py (6/6
smoke checks incl. hand-verified aggregates + exact control-draw reproduction). Full
pass: 766 sessions / 1.67B minute bars / 25,545 fill events in 160s; 0 missing files;
R0 daily-vs-minute consistency 100.0% on FAMILY (controls 99.9%).

## Verdict on the pinned gate: FAIL -- the EV PASS takes a REALISM HAIRCUT

**FAMILY TP-5x EV under R2 gating (small-clip: >=5 minutes AND >=10 contracts at/above
the level): -7.86%, vs R2-gated controls -3.22/-3.60/-3.72%. Both pinned conditions
fail (negative, and BELOW all three controls).** Per prereg: a dated amendment goes on
the w5dte_ev EV PASS, and the paper tape carries an owner-review flag (not killed).

## The anatomy -- the touches are REAL; the EV was too thin to survive losing any

1. **These are not ghost prints.** 97.9% of FAMILY TP-5x fills pass R1 (a 1-lot could
   plausibly exit); 87.1% pass R2; 73.2% pass R3 (20-lot). Lone-print share is only
   5.5%. The rule's touches are MORE realizable than exposure-matched control touches
   (R2: 87.1% vs ~79.5%, a +7pp gap at L5) -- the family selects liquid violence, not
   phantom spikes. Validity is stable across years (85.7-89.5% at R2).
2. **The EV dies anyway because it is a thin residue.** The +2.39% ungated EV is the
   difference between +400% on 19.2% of entries and ~-92% on the rest. Losing the
   12.9% least-tradeable fills (R2) swings ~-10pp of EV -- the book flips to -7.9%.
   The same arithmetic under R1 (losing only 2.1% of fills) leaves +0.79%.
3. **Under strict gating the family falls BELOW its controls** even though its
   validity is HIGHER -- its EV is more tail-dependent, so each disallowed winner
   costs it more. Realism is regressive on lottery books.
4. **The surviving corner (secondary, reported not gated): 1-lot x TP-10x.**
   R1-gated TP-10x: FAMILY +7.94% vs controls +2.97/+2.19/+1.84% -- comfortably
   positive and above all three. R1 x TP-5x: +0.79% vs controls all negative
   (marginal). The edge exists for a SINGLE-CONTRACT lottery player exiting at 10x;
   it does not survive 5-10-contract clips at the exit print.
5. **Exit capacity, not entry capacity, is the binding constraint.** Entry-side
   dollar volume (median $38.9k/day) suggested small clips fit; the exit print says
   otherwise: only 73% of 5x touches could absorb a 20-lot (R3), and the EV math
   punishes every miss. ~45% of first touches happen in the opening 30 minutes
   (both arms) -- half of this phenomenon is the opening auction.

## Consequences applied

- experiments/w5dte_ev/FINDINGS.md: Amendment 2026-08-18b (realism haircut) added --
  the PASS stands as measured under its own daily-bar convention, with the corrected
  claim: "edge survives realistic exits only at 1-lot x TP-10x scale."
- Paper tape: REVIEW flag for the owner (.horizon/INDEX.md). The tape's forward
  evidence must be read at 1-lot x TP-10x semantics; owner decides keep/kill.
- NEW_LEADS "W5DTE": re-ranked with the haircut; the practical ceiling is now
  explicitly a 1-lot lottery sleeve -- pocket-money scale, knowledge value >> P&L
  value. December read unaffected (all of this is in-era descriptive work).
- No production, scoring, or portfolio change. Nothing here re-opens the closed axes.

## Honesty

Tier thresholds are judgment pins (1/5/20-lot framing per the G3(b) precedent);
bar-level volume-at-or-above is an upper bound on truly-at-level volume (symmetric
across arms, so contrasts stand); 3 control draws are a directional screen, not the
100-draw gate (stated in prereg); zoneinfo DST-correct time-of-day. Archive-era only
(2022-08..2026-06-12).
