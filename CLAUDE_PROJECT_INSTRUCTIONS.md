# Claude Project: Trader Dashboard — SUPERSEDED

> **DO NOT RELY ON THIS FILE.** It is retained only because other documents
> reference it. Every parameter, weight, threshold, and scoring *direction*
> described in its historical body has been superseded and is in many cases
> inverted or stale. Use the authoritative sources below for anything current.

## Authoritative sources (use these instead)

- `CLAUDE.md` — current project instructions and conventions
- `.claude/docs/scoring-algorithm.md` — current scoring components, weights, and formula
- `.claude/docs/trading-strategy.md` — current strategy, regime, and exit logic
- `strategy_config.py` — the single source of truth for all live numeric parameters

## Historical context

This file was an early ("Gen-1") project-instructions sketch of the
mean-reversion options-scoring dashboard. It predates the V6 scoring-weight
redistribution, the dynamic VIX/breadth regime composite, the PESS put-earnings
suppression (which replaced the retired EARN_SUPP_PUT), and the relocation of
the scoring formula into `database/utils/scoring.py`. Its specific
numbers — component weights, volume caps, DTE/exit parameters, regime signal
families — and even its high/low score *direction* are no longer accurate. It
is preserved for provenance only; salvage nothing from it.
