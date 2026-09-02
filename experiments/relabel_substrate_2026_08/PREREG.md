# PREREG — Honest-label substrate (the mining enabler)

STATUS: LOCKED 2026-08-13 (git commit = lock). Measurement INFRASTRUCTURE, not a ship
gate — preregged so label-construction choices are pinned before any mining consumes
them (silent target drift is the failure mode this lock prevents).

## Why

Every prior formula/discriminator was fit against labels since proven optimistic
(double-touch WR ~+3pp; canon fills ~1.5x TP credit). Mining new formula terms against
old labels re-learns old mistakes. This campaign builds the per-signal HONEST ledger that
all downstream mining (residual-mining, ct-bounce-formula, option-surface) trains and
validates on.

## Object: `honest_ledger` parquet (.cache/relabel_substrate/honest_ledger_v1.parquet)

One row per (symbol, date) signal, version 74, overall >= 70, date in
[2021-01-01 .. today], delisted INCLUDED. Column families:

1. IDENTITY+FEATURES: symbol, date, overall, tier, components (trend/macd/rsi/bb/stoch/
   ta + weekly fields from scores row), regime composite, volume-signal fields,
   liquidity (opt_vol_30d_atm join where FF-3' covers; NULL before 2022-08 — never
   proxied, per the dead rho=0.154 verdict), CT-qualifying flag (predicate extracted
   from code, same source as ct15-paper-sleeve), delisted flag, PIT mcap.
2. L1 LEGACY label: barrier-touch outcome as assessed today (carried for continuity;
   documented caveat: D1 double-touch optimism, unresolved upstream).
3. L2 HONEST-SIM label: per-signal option P&L under the calibrated engine defaults —
   entry at signal close via the engine's premium model, shipped Core barriers
   (TP 0.10 / SL -1.00 dead-hold, 30-DTE, 27cd hold), fill semantics = GAP_AWARE with
   never-fill resolved BOTH ways as two columns: L2_expected (probability-weighted at
   the signal's LIQUIDITY-TIER miss rate from the fidelity study's measured table, NOT
   flat 0.15) and L2_sampled (one seeded draw). Tier-rate table cited from
   `experiments/tp_fill_fidelity_30dte` (t1 20.4% .. t4 7.8%); signals with no tier
   (pre-2022-08) use the flat 0.15 with an `l2_rate_source` flag column.
4. L3 REAL label: realized option P&L from `B:\polygon_derived\ledger_v2` where a kept
   row joins (2022-08+, ~4,403) — the gold validation subset. Join keys + integrity per
   the frontier campaign's verified method.

## Construction rules (LOCKED)

- No look-ahead: every feature readable at signal-date close; entry = signal close.
- The driver is resumable (state.json cursor by date-chunk), atomic appends, queue-run
  (`--priority high --db light` for reads; any heavy phase declared in the submit).
- Sample-first: builder inspects 50 rows of each source before writing parsers.
- ACCEPTANCE (all must pass before the substrate is declared usable):
  (a) population reconciliation: in-window row counts match the loader populations
  (22-now 19,261 / 5y 25,703 at >= 70) within documented eligibility differences;
  (b) L3 join-rate >= the frontier tripwire rates (98%+ on 75+ 2022-08+ windows);
  (c) 20-row spot-check: L2 inputs reproduce engine values (barrier levels, premium
  model) bit-consistent with a fresh engine call; (d) CT-flag count in 2022-08+ era
  reconciles with the vehicle campaign's 133-signals/4.4y figure within +-10%.
- Versioned artifact: any later change to label construction = honest_ledger_v2 + a
  dated CHANGES note; v1 is immutable once acceptance passes.

## Out of scope

No mining, no verdicts, no formula claims from this campaign. FINDINGS.md documents
construction + acceptance only. Downstream campaigns prereg separately.
