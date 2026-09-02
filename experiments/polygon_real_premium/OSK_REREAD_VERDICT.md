# OSK re-read on the CORRECTED panel — **the KILL STANDS**

**Date:** 2026-07-25 · Harness `osk_reread.py` (queue task #84, 4.3s) ·
Panels: OLD `.cache/polygon_iv/iv_ledger_polygon.parquet` (8,643 rows) vs
CORRECTED `.cache/polygon_real_premium/iv_panel_corrected.parquet` (**9,287 rows**, task #81,
59,929 API calls, 416s). Bars = P2.4 / `osk_validation/DESIGN.md`, **printed not re-derived**.

## Why this re-read was run

Two independent defects were found in the panel the 2026-07-07 OSK kill was decided on
(`PANEL_AUDIT.md`, `traps.md`): an adjusted-vs-as-traded spot used for both strike selection and the
BS solve, and a truncate-substituted `pnl15` (the OUTCOME variable). The contamination is worst in
**2022 (44.5% of rows drift >2%)** — the exact era the kill clause fired on — and in **calm/dividend
names**. A pre-run observation sharpened the concern: within the old panel the E1 kill statistic was
concentrated in the drifted rows (era_E1 spearman **−0.148 on drifted** vs **+0.0095 on undrifted**).

That is a legitimate reason to re-measure. It is not evidence of a result, and the expectation was
stated in advance: even if the kill statistic softened, "not killed" would not equal "passes" — the
P2.4 bar needs t>=3.

## Harness validity — reproduced the published numbers bit-exactly

`--selftest` runs the harness against the OLD panel and asserts it reproduces `osk_validation`'s
published values. It imports that experiment's own functions rather than reimplementing them.

| statistic | published | recomputed | delta |
|---|---|---|---|
| spearman backward_oos pooled | −0.00173 | −0.00173 | **0.000000** |
| spearman backward_oos 75+ | −0.07288 | −0.07288 | **0.000000** |
| spearman era_E1 (2022 bear tail) | −0.10716 | −0.10716 | **0.000000** |
| spearman discovery_full | 0.08955 | 0.08955 | **0.000000** |
| t_clust discovery_full\|raw | 3.37544 | 3.37544 | **0.000000** |
| t_clust backward_oos\|raw | 4.59807 | 4.59807 | **0.000000** |

28 statistical + 5 structural checks pass. A harness that could not reproduce the kill it was auditing
would have been worthless; this one can.

## The correctness check — the panels agree exactly where the bug is inert

`ARM=ripe_shared` (rows in BOTH panels with corrected `fwd_bars >= 15` — the only like-for-like set),
`STRATUM=undrifted` (adj_factor <= 1.001), 2,768 rows on each side:

| cell | d_rho | d_t |
|---|---|---|
| era_E1 | **0.0000** | **0.000** |
| backward_oos_pooled | 0.0003 | 0.003 |
| backward_oos_pooled 75+ | −0.0001 | −0.001 |
| discovery_full | 0.0025 | 0.028 |
| full_pooled | 0.0015 | 0.029 |

The corrected builder reproduces the old one wherever the spot bug cannot bite. That is the proof the
rebuild changed only what it was supposed to change.

*(The harness printed a LOUD WARNING about undrifted disagreement. It fired on `arm=native` only —
d_t 0.934 against a 0.50 bar — which is the arm that carries the `pnl15` truncate-substitution
difference, and the warning text itself names `ripe_shared` as the real check. It is a true positive
about the pnl15 defect, not about the rebuild.)*

## The correction was material — on drifted rows the statistics move a lot

`ARM=ripe_shared, STRATUM=drifted` (1,195 rows):

| cell | old rho / t_clust | corrected rho / t_clust | d_t |
|---|---|---|---|
| era_E1 | −0.2057 / −2.429 | −0.1177 / −1.416 | +1.012 |
| backward_oos_pooled | +0.0318 / +1.841 | −0.0300 / **−1.147** | **−2.988** |
| full_pooled | −0.0509 / +1.153 | −0.0242 / −1.021 | −2.173 |

So the bug was not cosmetic: it materially distorted the very cells the decision used.

## The verdict — unchanged

| | OLD panel | CORRECTED panel |
|---|---|---|
| backward-OOS pooled spearman | −0.0017 | **+0.0396** |
| backward-OOS pooled t_clust | +4.598 | **−0.692** |
| era_E1 spearman | −0.1072 | **−0.0726** |
| E1-E3 positive-sign | 2/3 | 2/3 |
| any era <= −0.05 | True | **True** |
| **DESIGN.md bar** | **KILL** | **KILL** |

**OSK stays dead.** The contamination softened the kill statistic (era_E1 −0.107 → −0.0726) but did
not manufacture it: E1 remains below the −0.05 clause and the pooled backward-OOS correlation, while
now positive at +0.0396, is nowhere near the t>=3 the bar requires (its clustered t is −0.692).
Note the N drop on the corrected panel (backward-OOS 3,807 → 2,163) is the honest `pnl15` nulls the
old panel was filling by truncation — less power, but real power rather than fabricated.

**One thread this DOES close.** The memory-recorded "unverified observation" — backward orthogonalized
skew-net-of-momentum positive, β +0.049, **clustered t +4.6** — was flagged as unusual and unverified.
On the corrected panel that same cell is **t_clust −0.692**. It was a contamination artifact. That
loose end is now tied off rather than left dangling.

## What this outcome is worth

The audit did its job in the direction that is easy to under-value: **it confirmed the existing
verdict rather than overturning it.** The 2026-07-07 decision to kill OSK, and the downstream decision
not to spend ~$2,035 on the L3 data buy, were correct — and are now correct on clean data instead of
correct by luck. The bug remains real and still had to be fixed, because the same panel underlies
`gamma_iv_phaseb`, `vega_state`, `era_conditioning` and `osk_era`, and because `pnl15` — a corrupted
outcome column — is common to all of them.

**Still open (not addressed here):** the gamma+IV M1 gate failed on a "panel BS-IV disagrees with real
contract premiums on CALM names" diagnosis, and calm names are the highest-drift names. That re-read
is a separate harness against `experiments/iv_engine_pertrade/`, and it is now the remaining
contaminated verdict worth re-measuring.
