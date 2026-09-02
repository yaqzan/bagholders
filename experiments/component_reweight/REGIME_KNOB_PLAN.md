# Apex regime-knob tuning + hard-sell plan (Notes 1 & 2, 2026-06-03)

Built from `regime_env_dataset.json` (deterministic apex+overflow 10y backtest: $50k→$303M / +607,212% /
max DD 77-79% / 6458 trades, asymmetric live cost) + per-date regime/VIX/breadth + the ledger days-held.
(Workflow `wi5kl24qd` built the dataset; its 4 analysis agents tripped on StructuredOutput so the analysis
was redone inline — `run_regime_analysis.py`.)

## Note 2 — WHERE Apex makes vs loses (the environment separator)

Apex's forward-20d return is cleanly monotonic in the environment:

| VIX band | mean fwd-20d logret | | breadth band | mean fwd-20d logret |
|---|---:|---|---|---:|
| <15 | **+0.134** | | <30 | **−0.086 (LOSES)** |
| 15-20 | +0.106 | | 30-45 | +0.021 |
| 20-25 | +0.028 | | 45-55 | +0.083 |
| **25-30** | **−0.017 (LOSES)** | | 55-70 | +0.081 |
| 30+ | +0.018 (crash-bounce) | | 70+ | +0.075 |

- **Breadth is the cleaner separator** (−0.086 at brd<30 is the strongest loss signal). The single worst
  drawdown trough (−77%, Oct-Nov 2023) was a **weak-breadth (31-45) pullback at moderate VIX (~16-20)** —
  NOT a VIX spike. Apex is a momentum-call book; it bleeds when participation (breadth) thins.
- VIX 25-30 is a secondary loss zone (−0.017); >30 is actually slightly positive (post-crash mean-reversion).
- Caveat: Apex runs at high DD ~continuously (64% of days dd≤−40%) — DD is the Apex *budget*, not a
  crash-only event. The actionable signal is the FORWARD-return separator (contract where fwd-return is −).

**The "no variance" problem (confirmed):** the only environment-conditional sizing knob that bites is
**F3F** (breadth): full 1.0× at breadth≥50, linear down to floor **0.50** at breadth≤30. So Apex still
deploys **half-size into the breadth<30 bleed zone**, and there is **no VIX-conditional sizing at all**
(regime_multiplier feeds REGIME_SLOPE but is composite-driven, not VIX-direct, and shallow). The user is
right: the regime response is nearly flat where it should contract hardest.

**Recommendation (queued for MC validation, #13):** DEEPEN the F3F breadth contraction —
- `F3F_CALL_FLOOR 0.50 → 0.30` (contract harder below breadth 30, the loss zone)
- `F3F_CALL_LOW 30 → 40` (start contracting at breadth 40, covering the weak 30-45 zone)
- aggressive variant: floor 0.25 / low 45.
Goal: lower 5y/10y WorstDD + the 2023 trough WITHOUT gutting 10y return, collapse=0. This ALSO mitigates
the #9 symmetric-cost worst-case-crash-entry fragility (contracting in weak breadth = the crash-protection).
A VIX-conditional contraction (VIX 25-30) is the secondary lever if breadth-deepening isn't enough.

## Note 1 — days-held U-shape + hard-sell timing

Ledger days-held (apex barrier TP 1.092σ / SL 2.548σ): **U-shaped** — mass at day 1 (quick TP) and a pile-up
at the hold-window boundary (e.g. W7: 75k at day1, 134k reach day7). Confirms the user's observation.

**The crux — the day-15 hard-sell cohort (37,569 signals, 9.5% of resolved):** if held beyond 15 days,
**53.6% eventually TP, 38.0% eventually SL, 8.5% never resolve.** So the day-15 pile-up is **majority
late-recoverers, NOT dead losers** → consistent with Apex's validated "HOLD≫CUT, ~68% recover". Cutting
them at day 7 forfeits those recoveries.

**BUT the user's hydration instinct has teeth:** fresh signals win ~68% vs these recoverers' 54% — so
cut-at-day-7 + recycle into a higher-WR fresh signal *could* beat holding **if the pool is hydrated**
(overflow just raised hydration). This is a genuine "hold 54%-recoverer vs recycle into 68%-fresh"
tradeoff the per-trade math can't settle. **Queued for MC (#13): HOLD_DAYS 7 / 10 / 15 on apex+overflow.**
If day-7/10 wins on 10y return at collapse=0, the recycle dominates; if day-15 wins, the recoverers do.
(Note: this tests hard-sell TIMING, distinct from the already-settled SL-cutting question.)

## Overflow cost-model honesty (#9) — feeds the same crash-protection

Overflow (+15× 10y) holds under the LIVE asymmetric canon but evaporates + gets crash-fragile under a
conservative symmetric ~3% round-trip (baseline Apex itself: 3% COVID collapse on worst-case-entry).
The edge is **execution-conditional** (limit-TP genuinely avoids spread). The regime-knob breadth
contraction above is the mitigation for that worst-case-crash fragility under either cost model.

## Next
1. #13 lands → pick the F3F floor/low that lowers DD at collapse=0 (+ the hard-sell verdict) → wire into Apex.
2. Re-run temporal-refresh on the tuned config (monthly/hydration).
3. Router (#11) + puts (#12) verdicts fold in separately.

## Assessment — hydration + monthly return (apex+overflow, deterministic 10y, asymmetric cost)

- **HYDRATION 89%**: avg 12.4/14 slots open, median 14 (full book), 53% of days at full, only 2% of days
  <50% full, 0% idle. Overflow took the book from mostly-idle (75+-only: ~3204 of ~34k eligible fill) to
  89% hydrated — the concrete proof of the overflow lever. **This resolves Note 1's hydration caveat: the
  pool IS full, so a 7-day hard-sell has fresh signals to recycle into → the hard-sell-7 test is warranted
  (queued #13), not dismissible.**
- **MONTHLY RETURN: median +6.2%/mo, mean +10.2%, 67% up-months** (best +98%, worst −55% = the DD budget).
  Realistic profile under the live asymmetric cost; lower under conservative symmetric (#9 caveat).
- Dashboard Calendar tab refreshed for all profiles (temporal-refresh, 2026-06-03).
