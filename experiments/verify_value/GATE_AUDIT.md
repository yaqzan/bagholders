# A2 Gate-vs-Gradient Score-Stage Parsimony Audit — /research 2026-06-24

**Outcome: NO SHIP (honest). The verify_value gate-vs-gradient principle is ALREADY SATISFIED by v74 — every funded (75+) gradient-shaper was retired in the v71/v73/v74 lean campaign; the lone active call-side dampener (CWWD) is empirically confirmed funded-irrelevant.** A2 closes.

Active version v74 (`f9fb7b934`). Read-only (config catalog + one 25-symbol rescore A/B). No recalc, no version change. Budget: ~7h to the 09:30 ET open; this resolved in <1h because **the config IS the evidence** for a subtractive-parsimony hypothesis.

---

## The funded-relevance rule (what makes a score-stage mechanism worth keeping)

Apex trades **75+** (70-74 overflow disabled). So a score-stage mechanism is **funded-load-bearing** only if it can change a **75+** score (i.e., its gate reaches ≥75). A mechanism gated entirely **below 75** (or on the put side ≤25, puts off) is **funded-irrelevant by construction** — it can only reshape untraded bands. The verify_value finding (the gradient above the gate is per-trade-inert) then says: a funded gradient-shaper that survives is dead weight; a sub-gate mechanism is dashboard/hygiene, not funded.

## Catalog of v74 active score-stage mechanisms (from `strategy_config.SCORING`)

| Mechanism | State (v74) | Gate | Funded (75+) relevance |
|---|---|---|---|
| MCD (mcap dampener) | **RETIRED** (v71, `MCD_ENABLED=False`) | 70-84 | — gradient-shaper, already cut |
| ICH (Ichimoku) | **RETIRED** (v73, `ICH_ENABLED=False`) | 69-90 call | — already cut |
| SCW (stoch conviction) | **RETIRED** (v73, `SCW_ENABLED=False`) | 70+ | — already cut |
| CWCF | **RETIRED** (v73, `K=0`) | 75+ | — already cut |
| CSWC | **RETIRED** (v73, `K=0`) | 75+ | — already cut |
| Continuation echo | **RETIRED** (v74, `CONT_BOOST_ENABLED=False`) | 50-84 | — already cut |
| WVD wave | **RETIRED** (v74, `WVD_WAVE_ENABLED=False`) | 70-85 | — already cut |
| Daily-volume authority | **RETIRED** (v74, `False`) | ~71-73 | — already cut |
| EARN_BOOST | **RETIRED** (v74) | 70+/≤25 | — already cut |
| mis_stress, JA4, Sector Wave | **RETIRED** (v71) | — | — already cut |
| WCF lift | **RETIRED** (v73, `K=0`) | <28 put | — already cut |
| **CWWD** | **ACTIVE** (`K=0.95`) | **[70,75)** | **funded-IRRELEVANT** (below 75 gate — see A/B) |
| CAP / EXH / EXT_FOCAL | ACTIVE | ≤25 / oversold | funded-irrelevant (put-side, puts off) |
| PCD / PESS / WEEKLY_PUT | ACTIVE | ≤25 / [16,20] | funded-irrelevant (put-side, puts off) |
| **CORE** (components, weekly adj, regime mult, MACD gate, momentum-confirm, volume amp) | ACTIVE | sets the score | **gate-DETERMINING** — this IS the 75+ selector (keep) |

**Every retired row was a gradient-shaper or look-ahead mechanism the v71/v73/v74 honest-era campaign already removed.** No active mechanism shapes the 75+ gradient.

## Empirical confirmation — CWWD is funded-irrelevant (the only active call-side dampener)

Faithful rescore A/B (25 liquid symbols, 10y, `rescore_dump.py`): v74 baseline (CWWD on) vs `CWWD_DAMPEN_K=0`.

- Arms differ on **186** rows (toggle works); all deltas ≥0 (removing a down-only dampener can only raise) ✓.
- **FUNDED CHECK: 0 rows with a 75+ score change. 75+ membership byte-identical (569 = 569, symdiff 0).** → CWWD cannot touch the traded book, exactly as `gate_hi=75` + lower-only implies.
- CWWD's 186 affected rows are **all in 70-74** (off-dist min 70 max 74): 81.7% gate-crossing (de-qualified below 70), 18.3% mild within-band. Entirely below the 75 gate.

→ CWWD is a **70-74 de-qualification / dashboard-honesty** mechanism (it stops weak 70-74 wadj-neg names from showing as call signals), NOT a funded gradient-shaper. Kept as a free real discriminator + dashboard honesty; retiring it would not change one funded trade.

## Conclusion

The verify_value principle ("magnitude precision above the gate is worthless on the funded payoff") yields **no v75 call-side cut: v74 is already at the gate-vs-gradient optimum.** The honest-era lean campaign (v71 → v73 → v74) *retroactively pre-satisfied the principle* — every pure-gradient call-side shaper is already retired, and the only surviving 75+-relevant machinery is the gate-determining CORE (which the principle says to keep — it sets membership, where all the value is).

## Residual parsimony surface (STAGED, product-decision, NOT shipped)

The only remaining score-stage cut is the **put-side + 70-74-band machinery** (CWWD, CAP, EXH, EXT_FOCAL, PCD, PESS, WEEKLY_PUT — ~7 mechanisms + their config/`weight_info` surface). It is **funded-byte-identical** (the 75+ traded set is unchanged), so it removes overfit surface from the OOS accounting and simplifies audits. BUT it is **not free**: (1) it changes dashboard behavior for the untraded 70-74 band (weak names would re-surface as call signals — CWWD's de-qualification is a *product* feature) and the ≤25 put signals; (2) it requires a full recalc to re-store the changed <75 / put scores; (3) puts could be re-enabled someday, which would make the put machinery funded-relevant again. → A **product decision to stage**, not a funded-relevant ship, and per *Stage > rush* not worth a pre-open recalc for zero funded change.

**Decision: NO SHIP.** Active version stays v74. A2 closed in NEW_LEADS. The next genuine ship path remains the option/IV data-unblock (NEW_LEADS N3), per the mature-frontier read.

Artifacts: `audit_cwwd.py`, `.cache/verify_value/rescore_{base,cwwd_off}.parquet`; the value-verification substrate in `FINDINGS.md` + `phase{1,2,3}_*.py`.
