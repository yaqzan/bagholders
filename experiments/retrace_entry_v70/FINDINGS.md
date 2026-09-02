# Retracement-conditioned entry on extended/late-in-run calls — PREMISE NULL (2026-06-09)

**Status: NULL at the cheap premise stage. No MC, no ship, no stage-mechanism.** Killed by the
ledger on the same logic the velocity/divergence threads died: the cohort the user perceives as
"wins via pullback-then-resweep" overwhelmingly wins by **direct continuation**, and "wait for the
dip to enter" is **anti-selective** (dipping is a LOSER signal). Seeded by NET/RKLB/IREN (last few
months). Harness: `experiments/retrace_entry_v70/mine.py`. Holdout-locked ledger (≤2026-05-15),
opt15/apex15 barriers.

## Hypothesis (user)
A call signal that fires LATE in / at the top of a strong up-run wins via PULLBACK-then-RESWEEP, so
entering at the extended signal bar mistimes it — a retracement-conditioned (delayed) entry would get
a better price. Plus a sharp categorization point: a multi-day SERIES of consecutive call-days from
local-bottom→top must not be lumped — the EARLY (base) signals are clean continuation wins; the LATE
(extended top) ones are the problem bucket whose "win" is just the resweep tail.

## Method (decisive premise test, no MC)
75+ call signals (N=4699). Built the **run-position feature** (cluster consecutive 75+ days, gap>7cal
= new run per historic_peaks; ordinal 1=base / ≥3=late; cluster-size) + **extension** (σ run-up v5/v10)
+ **path-shape labels** from the barrier-agnostic forward path: `dip_b4_TP` = winner dipped ≥0.5σ
before the opt15 TP touch (= "dip-then-resweep win"); `MISSdelay` = winner that never retraced 0.5σ
within 5d (a "wait for the pullback" rule would NOT enter → missed winner = the cost).

## Result — premise FALSE, three ways

| bucket | N | opt15 | apex15 | WINNERS dip_b4_TP | WINNERS MISSdelay | meanMAE |
|---|---:|---:|---:|---:|---:|---:|
| ALL 75+ | 4699 | 47.5% | 70.0% | **12.5%** | **61.9%** | 1.44σ |
| pos=1 base | 2781 | 47.3% | 70.2% | 12.2% | 61.2% | 1.44σ |
| pos=2 mid | 1110 | 47.4% | 69.3% | 13.5% | 62.5% | 1.48σ |
| pos=3+ late | 808 | 48.5% | 70.5% | 12.5% | 63.3% | 1.36σ |
| EXT v5≥1 | 1872 | 46.8% | 68.5% | 13.5% | 60.5% | 1.40σ |
| **LATE≥3 & EXT v5≥1 (user's bucket)** | 339 | 49.0% | 67.0% | **12.0%** | **61.4%** | 1.52σ |
| LATE≥3 & EXT v5≥1.5 | 194 | 46.4% | 66.5% | 13.3% | 57.8% | 1.78σ |

1. **~88% of winners win by DIRECT CONTINUATION** (only ~12% dip ≥0.5σ before TP) — and the
   late/extended bucket is NOT different (12.0% vs base 12.2%). The "win comes from the resweep" is a
   ~1-in-8 minority, NOT concentrated in the extended/late signals.
2. **"Wait for the dip" is ANTI-SELECTIVE.** Among ALL signals, 69% dip 0.5σ within 5d; among WINNERS
   only 38% do (`MISSdelay` 62%). **Winners dip far LESS than losers** — losers are the ones that dip
   on the way to the SL. So a retracement-entry rule preferentially enters the LOSERS and **misses
   ~60% of the winners** (the runners). It's backwards.
3. **Run-position win rates are FLAT** (base 47.3 / mid 47.4 / late 48.5 opt15; apex 70.2/69.3/70.5).
   The late-in-run signals are NOT mistimed — they win at the same rate as the base signals. The
   categorization is conceptually sound (37.6% of signals are in ≥3-runs; 17.2% are late ord≥3) but
   once separated the late bucket behaves like the base bucket on every metric that matters.

## Named-example grounding (post-cutoff, face validity)
- **RKLB May:** 5/04–5/07 calls (78–84) → 7d **+47% to +68%**, 7d-min only −1% to −8%. Direct
  continuation; waiting MISSES the +50%+ moves. The textbook counter-example.
- **IREN:** 5/04 (+11%) / 5/05 (+7%) early wins; the LATE 5/06 top signal (ord-3, px 60.98) **−14%
  and stayed down** — a crash, not a resweep. Waiting wouldn't help (you'd enter the breakdown).
- The dip-then-resweep cases the user remembers (e.g. RKLB 5/06: −8% then +47%) are real but the ~12%
  minority — recency/salience bias, same as NET in the divergence thread.

## Why it's structurally unfixable
The retracement itself does NOT distinguish a healthy pullback-that-resweeps (~12%) from a runner
(misses if you wait) from the START of a crash (IREN 5/06). A 0.5σ dip looks identical in all three.
So even a perfect "wait for the dip" rule can't select the resweepers — it enters resweepers AND
crashers (worse: buying the breakdown) and forfeits the runners. Compounds the capital-velocity law
(G16: HOLD/ADD-exposure/WAIT tends to null) with adverse selection. **No MC can rescue a premise the
path-shape refutes — the win-set and the dip-set are negatively correlated.**

## Verdict
Do not build a delayed/retracement-conditioned call-entry overlay. The win-path-shape / run-position
angle joins the divergence/velocity/exhaustion threads as closed. The user's salient cases are the
~12% dip-then-resweep minority; the cohort as a whole runs (or crashes), and dipping anti-selects.
