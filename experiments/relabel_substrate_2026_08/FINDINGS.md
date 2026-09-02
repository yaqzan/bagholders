# FINDINGS — Honest-label substrate (construction + acceptance only)

**STATUS: COMPLETE 2026-08-13. `honest_ledger_v1.parquet` LOCKED (27,887 rows × 83 cols,
`.cache/relabel_substrate/`). All 4 acceptance checks PASS. No mining, no verdicts here
(per PREREG); downstream campaigns prereg separately.** Prereg `7fa41127`; queue #541
(30s wall — 23 quarterly chunks, ~5.6M resolve() draws); driver
`driver/build_ledger.py` (+ `sample_sources.py`); machine-readable acceptance in
`out/acceptance_report.json`.

## Acceptance

| check | result |
|---|---|
| (a) population reconciliation | 22-now 19,262 vs 19,261 cited (+0.01%); 5y 25,728 vs 25,703 (+0.10%) — within score-history-mutability noise (MNST-class churn) |
| (b) L3 join (75+, 2022-08+, N=4,968) | 99.4% any-status (bar 98%); 88.6% kept |
| (c) 20-row engine spot-check | 20/20 bit-exact vs fresh compute_trade_outcome+resolve |
| (d) CT census | ledger CT count on [2022-01-01..2026-06-15] = **133 exactly** (vehicle campaign figure) |

## Construction facts that downstream consumers MUST know

- Population from raw `Score` query, NOT `load_signals()` (which CTSL-mutates `.overall`
  and applies portfolio-stage filters — wrong gate for ledger membership).
- L2 reuses the real engine functions (never reimplemented); `stressed=False` proven
  byte-identical for Core (TP_BASE==TP_STRESS asserted at runtime).
- **Ripeness gate** (new, pnl15-trap family): 60/27,887 rows (0.22%) unripe — recent or
  price-history-short rows carry `unripe` flags, never fake resolved labels.
- **Tier-specific never-fill coverage is only 17.7% of ripe rows** (the liquidity sidecar
  is a 75+/2022-08+ companion, 4,936 rows); 82.3% used the flat 0.15 with
  `l2_rate_source` recording which, per row. Correction to the PREREG's gloss (which
  implied only pre-2022-08 lacked tiers).
- PIT mcap via the F4 formula verbatim; 91.5% coverage (gap ≈ delisted lacking snapshots).
- `ct_flag`/`ct_tag` from shared `tools/ct_predicate.py` (Builder B), self-checked
  (374-grid + 2,000 live rows AGREE) before wiring.
- One real bug caught BY acceptance check (c): early acceptance-side code omitted
  `_attach_earnings_span`, desyncing RNG on earnings-spanning trades (FLEX 2024-01-30);
  fixed pre-full-build. The build pipeline itself was correct throughout.

## Raw observations (not interpreted)

- L1 legacy join 80.0% (cache NULLs + uncached keys).
- l2_kind mix (ripe): tp 90.3% / sl 8.4% / hard 1.2% / both 0.1%.
- ledger_v2/liquidity sidecars are 75+-scoped by construction — small vs the 70+ ledger
  universe but 99.4% of their own target population.

Versioning: v1 immutable; any label-construction change = honest_ledger_v2 + dated note.
