# FINDINGS — Sentinel calibrated-positive guard battery

**STATUS: COMPLETE 2026-08-12. VERDICT (locked mapping applied verbatim): ALL GUARDS PASS →
Sentinel upgrades to "calibrated-positive, guard-verified (single N=500 battery; Dec-15
OOS + forward ledger + fill-canon still arbiters)."** Prereg `9b2c1b47`; queue #491-494;
evidence `out/guardS_{gs1,gs2,gs3}.csv` + per-path tapes `out/tapes/`.

## Guard arithmetic (decision windows)

| guard | 22-now | 5y | bar | result |
|---|---|---|---|---|
| G-S1 survivor median | **+24.45%** | **+17.69%** | > 0 both | PASS |
| G-S1 survivor WorstDD | 42.60 (full 43.50) | 46.98 (full 46.17) | ≤ full +3.0pp | PASS |
| G-S2 MISS_P=0.20 median | **+32.40%** | **+36.23%** | ≥ 0 both | PASS |
| G-S3 median trades/path | 320 | 406-485 | ≥30 (THIN) / ≥10 (ANECDOTE) | OK, wide margin |

Fingerprints flat both batteries (matching R1/R3's exactly); close-boundary flat;
`n_calls_delisted=0` asserted per survivor cell; collapse 0 everywhere in both arms.

## Honest shape of the result (caveats that travel with the upgrade)

1. **The survivor cut REDUCES the edge (+37→+24, +45→+18) without killing it** — a real
   fraction of Sentinel's full-universe read sits in the delisted cohort; the surviving
   half is the bankable-in-principle part. Quote survivor numbers, not full-universe.
2. **Sentinel is economically a near-single-position book**: median concurrency ~1,
   ~70 trades/yr (85+ signals are rare; mp14 never fills). Its +DD profile reflects
   chunky sequential high-conviction trades, not diversification.
3. **Recent-regime positive, long-history negative**: 10y calibrated −37% (survivor);
   crash windows −46/−50 with DD bounded 59-62. The positivity lives in 2023-now.
   Whether that is regime luck or the 85+ band's real selectivity is EXACTLY what the
   Dec-15 OOS re-grade of score bands on virgin data adjudicates — already scheduled.
4. Single N=500 battery, one engine, one substrate era. No portfolio-stage change ships
   from this; no capital moves. It is a *status upgrade of a measurement*, feeding the
   Phase-3 vehicle decision only through the standing gates (G1 Dec OOS, G2 ledger,
   fill-canon P2.B).

## Disposition

- capital-plan bullet updated to the locked phrase; known-issues ship-state updated.
- Natural next reads (not run tonight; each needs its own prereg): Sentinel survivor-arm
  full history at N=500×12 with the D1 double-touch caveat noted; 85+ band WR15 on the
  Dec-15 virgin window (automatic); Sentinel-sized live paper sleeve as a second G2
  instrument (owner decision — zero cost, adds a conservative arm to the forward test).
