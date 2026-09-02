# Bear/chop trend-confirm DD lever — PRECONDITION-NULL (2026-06-17, /research)

**Hypothesis (Stage-3 portfolio):** a regime-gated CALL-alloc dampener on the
over-extended **trend-confirm 75+ cohort** in the bear/chop band (elevated VIX
22-28 and/or weak breadth, EXCLUDING acute panic where mean-reversion wins) cuts
DD without cutting bull/neutral participation. This is the portfolio-stage form
of the `weather_components` bear/chop-defense lead (TREND is the regime-harmful
driver, dEV −2.42/−1.81 in 2022/2023; RSI is bear-robust) and the staged
NEW_LEADS A0 "trend+macd-confirm × high-VIX call-alloc tilt — an RXDD refinement."

**Test:** read-only per-trade cohort mine on the v74 apex ledger
(`weather_comp_v74_2016`, funded ≥75 book N=12,833) joined to the `30dte_apex`
CALL payoff (+0.30/−0.70/−0.40, 30d) + PIT regime context (VIX/breadth/regime-mult
by date). `cohort_mine.py`. No MC. The decisive test is the **per-window G26
check** (a concentration DD idea must be bad in bear/chop AND not-good in bull —
else it's the reversal-trap).

## Result — precondition NOT met (do not run the MC)

**The pooled band-level signal LOOKS real** but is the wrong kind:
- `elev|weakbrd ex-panic` band: trend≥70 cohort apex-EV **+0.95%** vs band-rest
  **+7.18%** (dEV −6.23, zWin −2.17); trend≥80 +0.64% vs +5.53% (dEV −4.89, z −1.96).
- BUT in **panic≥28 it INVERTS**: trend≥80 +3.56% vs rest +0.48% (z **+2.45**);
  trend≥80&rsi<50 +4.09% vs +0.13% (z **+3.64**). Panic trend-extension is a
  mean-reversion WINNER (G19 crash-artifact) — must not trim, and it's most of
  the high-VIX population.

**The per-window G26 check FAILS (decisive):**

trend≥80 in `elev|weakbrd ex-panic`, cohort−rest dEV by year:
| 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| +2.27 | **−4.46** | **+2.63** | **−4.41** | −2.85 | −12.11(thin) |

trend&macd both≥70, same band:
| 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| +3.43 | **−0.13** | −0.70 | −1.26 | +0.97 | −9.88(thin) |

- Sign-FLIPS across windows: cohort is BETTER in 2021/2023, worse in 2022/2025.
- **Bad in 2024 (a BULL year, −4.41)** → a trim would shed bull-window winners.
- ≈FLAT in the actual bear 2022 for trend&macd (−0.13).
- The pooled "−5pp" is driven by the **elevated-VIX BAND effect (RXDD's
  territory)** + a thin/partial recent-2026 window — NOT a regime-stable,
  RXDD-orthogonal, trend-confirm-SPECIFIC DD-driver.

## Verdict
The cohort can't separate "trend-extended in a real bear (2022, bad)" from
"trend-extended in a chop that recovers (2023, good)" from "trend-extended in a
bull pullback (2024, good)" — the persist-vs-crash unpredictability wall
(G14/G20) wearing a trend-confirmation hat, and the G26 reversal-trap
(concentration is good in the trend, bad in the reversal). It also heavily
overlaps the shipped RXDD (the band effect). Confirms the lead's own pessimism
("likely a refinement, not independent alpha; overlaps RXDD; crash-artifact
risk"). Burning ~4h of MC + throttling the production close to confirm a null
the cheap precondition already shows would violate cheap-first discipline.

**Closed NULL.** Closes the NEW_LEADS A0 "trend+macd-confirm × high-VIX
call-alloc tilt" sub-bullet with quantitative per-window evidence (saves the
next agent the MC). Do-not-retry: a regime/trend-confirm CALL-alloc trim keyed
on VIX/breadth bands — it's RXDD's territory + the persist-crash wall + fails
G26 sign-stability.

Artifacts: `cohort_mine.py` (re-runnable, ~30s, read-only).
