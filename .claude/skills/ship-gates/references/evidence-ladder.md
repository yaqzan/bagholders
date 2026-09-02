# Evidence ladder — what evidence licenses what claim

Overflow reference from [../SKILL.md](../SKILL.md). The most common way a
"clean" ship turns out not to be is a claim resting on evidence one rung
below what it actually needs (the v58 lesson: Stage-1-clean is NOT
portfolio-safe without the downstream DD check). Before stating any of the
left-column claims out loud — to yourself, in a FINDINGS.md, or to the user —
confirm the right-column evidence actually exists.

| Claim you want to make | Minimum evidence that licenses it |
|---|---|
| "This cohort might carry signal" | Cohort z ≥ +3 (W1 pre-flight) — nothing else, don't sweep below this |
| "This Stage 1 candidate is directionally sound" | W1-W4 pass on the affected cohort at 5y, multi-window, on holdout-locked data |
| "This candidate should ship" | W5 SHIP or FLAG-with-teeth (justification + watch metric + downstream confirmation, or N1-N3 for neutrality ships), W6 no candidate-introduced inversion |
| "This barrier retune is safe" | B1-B5 pass, including the smoke-MC DD bound (B2/B3) |
| "This portfolio change reduces real-world risk" | T1-T7 pass at N=500+×8 windows including a 2020_crash screen, DD-primary, collapse=0 on every cell |
| "This is comparable across versions in the dashboard" | `comparability_unit=COMPLETE` (pack + supply + PRF all exist) |
| "This historical WR/FINDINGS number is trustworthy as-is" | Post-v69 (2026-05-31 weekly-blend ship) — pre-v69 numbers are look-ahead-inflated by ~12pp; cite that caveat explicitly if quoting older evidence |
| "The gate tooling itself still behaves correctly" | `--selftest` passes — never `--replay` (main SKILL.md GUARD 3) |
| "This mechanism should stay in the codebase" | It re-earns its seat on honest evidence at each ship, not just historical inertia (the parsimony/bias-to-retire doctrine — see `.claude/docs/weatherization.md`) |

## Why the ladder matters more than any single gate

Each rung is necessary but not sufficient for the rung above it. A candidate
can pass W1-W4 (Stage 1 directionally sound) and still not license "this
candidate should ship" without W5's verdict — and even a clean W5 SHIP does
not automatically license "this reduces real-world risk," because Stage 1 is
explicitly barrier-independent and DD-blind by design (GUARD 8 in the main
SKILL.md: no MC at Stage 1). The residual blind spot named in
`/find-and-ship-alpha` GUARD 8 is exactly this gap: "the gate is MC-free and
cannot see correlated-fill drawdown" — the historical mechanism by which a
Stage-1-clean v58 slipped through and later needed reverting.

Practical rule: when writing up a result (a FINDINGS.md, a chat response, a
ship announcement), state the claim you are ACTUALLY licensed to make by the
evidence in hand, not the claim you'd like to make. "This cohort shows z=+4.2"
is a different (and much weaker) claim than "this should ship," even though
both might be true about the same candidate at different points in its
lifecycle.
