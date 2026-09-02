# Regime-conditioned call-edge decomposition — DRAWDOWN good, CHOP poison (2026-06-22)

**User thesis:** the market drifts up → calls have a tailwind → the real edge should live in
**choppy / drawdown** windows (integrate with the market-trend / weekly indicator).

**What this probe adds (genuinely unexplored):** strip the market-drift beta out of the call
outcomes and ask *where the selection edge actually lives by market-trend regime*, and whether
the weakness is *uniform* (irreducible) or has a *discriminable winning pocket*. Read-only, no MC,
no MySQL — 75+ call signals (proxy_ledger, N=4,699, 2016–2026) × SPY forward returns (cached OHLC).

## The result (the thesis is HALF right — and the half it's wrong on matters)

Call outcome by **market-trend regime** (SPY trailing 20d return). `exc` = stock_fwd − SPY_fwd
(beta-**stripped** selection); `apexEV` = funded option EV (+0.30 win / −0.70 stop / −0.40 expire,
barrier-walk, no theta/dead-hold):

| market-trend regime | N | exc15% (beta-stripped) | exc30% | apexWR | **apexEV%** |
|---|---:|---:|---:|---:|---:|
| UP_strong (SPY +3%+) | 1146 | +1.26 | +2.35 | 0.737 | +3.97 |
| UP_mild | 1556 | +0.98 | +1.94 | 0.738 | +3.80 |
| **FLAT_chop (SPY ±0.5%)** | 408 | **+0.37** | **+0.62** | **0.681** | **−1.57** ← worst, NEGATIVE |
| DOWN_mild | 634 | +1.41 | +1.34 | 0.733 | +3.63 |
| **DOWN_hard (SPY −3%+)** | 955 | **+1.69** | **+2.10** | **0.790** | **+8.98** ← best |

By **SPY drawdown-from-60d-high** (the cleanest "drawdown window" axis), even cleaner & monotonic:

| SPY drawdown | N | exc15% | apexEV% |
|---|---:|---:|---:|
| near_high (≥ −2%) | 2585 | +0.88 | +2.90 |
| dd_2_6 | 1147 | +1.25 | +4.30 |
| **dd_6p (> −6% off high)** | 967 | **+2.00** | **+8.56** |

**The edge is NOT beta** (the beta-stripped `exc` is strongly regime-dependent, +1.69% in hard
drawdowns vs +0.37% in flat — if it were pure drift the excess would be flat). It is genuine,
regime-concentrated **selection**: deeper the market drawdown → better the calls pick (the
stocks beat SPY by more) AND better the funded EV.

**The thesis is half right, and the conflation matters:** the strategy is a **leveraged-momentum**
engine, so it needs **DIRECTION** (up *or* down). **Drawdowns = directional = the bounce works (best regime);
CHOP/flat = no direction = the strategy's WORST regime (negative EV).** "Choppy or drawdown" are
**opposite** for this strategy. The drawdown edge is partly the documented **buy-weakness / dead-hold
mean-reversion** the live strategy already captures; the **novel, actionable** piece is that
**FLAT/CHOP is a negative-EV regime** that the market-trend axis cleanly isolates.

## You cannot SELECT better in chop — it's a REGIME-level, not a feature-level, effect

Discriminator hunt within chop/down (N=1,997, apexWR 0.750) — does any signal-time feature separate
the chop winners? **No** (all |z| < 1.5):

| feature split | z(hi−lo) |
|---|---:|
| c_rsi ≥ 55 (the "bear-robust RSI" hint) | +0.97 |
| c_trend ≥ 70 | −1.43 (trend-high mildly *worse*, consistent w/ trend-harm) |
| rsi≥55 & trend<60 pocket | +0.72 (thin, n.s.) |
| c_macd / c_bb / c_stoch / rs20 / w_adj | all |z|<1.0 |

So "find the *optimal calls* in chop" by better SELECTION is **not achievable** (no discriminator,
consistent with the documented reweight-null). The only lever the data supports is **regime-level
SIZING** — and the cleanest, most novel one is **contracting the FLAT/CHOP negative-EV regime**.

## The lead (NEW, candidate Stage-3): market-trend (SPY) flat/chop call-alloc contraction

A smooth contraction of CALL allocation when the **market-trend** is flat/chop (SPY trailing ~20d
return ≈ 0, i.e. not directional). This is the user's "integrate with the market-trend indicator,"
operationalized as a **6th orthogonal-candidate DD/EV lever** on the SPY-trend axis. It is a
*contraction* (the documented-safe direction, like RXDD/MWDD/TVDD), targets a genuinely **negative-EV**
regime, and is on an axis (SPY trailing return / drawdown-from-high) distinct from the shipped levers
(RXDD=VIX level, MWDD=McClellan, TVDD=TRIN, F3F=breadth level, BDIV=SPY-near-high+breadth-rollover).

**Before it can ship it needs (NOT done here):**
1. **Orthogonality check** vs the 5 shipped levers — is FLAT_chop already contracted by VIX/McClellan/
   TRIN? (the all-levers-off slice; G21/G23). The SPY-flat condition is a different axis, but must be proven independent.
2. **Stage-3 MC** B→C→D, N=500×8 incl COVID: collapse=0, 5y WorstDD ≤ baseline +1pp, compound flat-or-up.
3. The DOWN_hard "size-UP" direction is **NOT** part of this lead — sizing up adds exposure (G16
   over-deployment) AND that edge is largely the already-captured buy-weakness/dead-hold bounce. The
   lead is the **flat-chop contraction only**.

## Caveats
- Substrate = proxy_ledger (v60-era overall, N=4,699, ≤2026-05-15); apex EV is a clean barrier-walk
  (no theta/dead-hold/spread, generous same-bar=win). The **beta-stripped `exc`** metric is barrier-free
  and corroborates the apex-EV ranking exactly → the regime ordering is robust to barrier mechanics.
  Magnitudes are directional, not ship-grade; the Stage-3 MC is the ship gate.
- FLAT_chop N=408 is the smallest cell (apexWR 0.681 vs 0.743 base ≈ z−2.7; real but modest).

## ORTHOGONALITY GATE → ❌ TESTED-NULL 2026-06-25 (redundant with the shipped MWDD lever)

Ran the lead's stop-rule #1 (`orthogonality.py`): is FLAT_chop's negative-EV independent of the 5
shipped DD levers? Joined VIX/McClellan/TRIN/breadth per entry; `apex` is a ±1 win/loss flag (WR proxy).
FLAT_chop is orthogonal to RXDD (worse where VIX<20, RXDD-off), F3F (worse where breadth≥50), TVDD — BUT
the decisive 2×2 vs MWDD (McClellan-flat band) KILLS it:

| | MWDD-ON (\|mcc\|≤22, flat) | MWDD-OFF (\|mcc\|>22, extreme) |
|---|---:|---:|
| **FLAT_chop** | WR **63.9%** (N=223) | WR **84.2%** (N=79) |
| NOT-flat | 74.0% (N=1930) | 78.5% (N=1223) |

(base WR 75.1%, N=3455.) **FLAT_chop's weakness lives ENTIRELY in the McClellan-flat band where MWDD
already fires; where MWDD is off, FLAT_chop is ABOVE base (84.2%).** So SPY-flat is NOT a 6th orthogonal
axis — it is MWDD's axis (the chop = directionless-breadth = McClellan-flat = MWDD-contracted state). The
all-levers-off slice (which requires \|mcc\|>22) is good/neutral, not bad. The lead as a PARALLEL lever
is redundant; **the live system already does the user's "regime-aware chop contraction" — it's MWDD.**

**Residual (LOW-priority MWDD refinement, NOT a parallel lever):** within MWDD's firing band, SPY-flat
further marks a worse sub-cohort (FLAT∩flat 63.9% vs NOT-flat∩flat 74.0% — MWDD under-contracts the
both-flat cell). A "deepen MWDD contraction when SPY-trend is also flat" tweak COULD add marginal DD — but
it's a 2nd-order MWDD parameter refinement on a thin cell (N=223, residual thinner), G22 regression-risk
(could hurt MWDD), and low expected value (MWDD already gets most of it). Not worth an overnight MC.

## Files
- `probe.py` — the decomposition + discriminator hunt → `regime_call_ledger.parquet`, `regime_call_report.json`
- `orthogonality.py` — the 2026-06-25 orthogonality gate (redundant-with-MWDD → tested-null)
