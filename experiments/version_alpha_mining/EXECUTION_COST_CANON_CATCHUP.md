# Execution-Cost Canon — Catch-Up Compute Log

**Canon shipped 2026-06-02 (code + docs):** asymmetric execution cost. Mid-entry
and limit-TP legs cross NO spread (`SLIP_ENTRY=SLIP_TP=0`); only FORCED exits
(SL stop, day-15 hard sell, dead-hold expiry/open) pay the half-spread
(`SLIP_SL=SLIP_HARD=-0.015`). Dead-hold *popout* = limit fill = free. Replaces
the symmetric `-1.5%/leg` (3% round-trip) the params agent had set, which
over-taxed the ~85% of trades that win on a limit.

Code is LIVE (strategy_config.py + mc/mc15/bc/bc15 + drift-guard 581). **Every
number below was computed under a DIFFERENT (old) cost and is now stale.** Re-run
when compute eases (currently held; production + params-agent MC contend).

## Stale data → re-run list (ordered by value)

| # | What | Computed under | Re-run command | Note |
|---|---|---|---|---|
| 1 | **v70 research pack** (the only honest pack built so far) | OLD symmetric −1.5%/leg | `python tools/build_research_pack.py --version v70 --profiles apex --run-portfolio-windows` | Built 2026-06-02 pre-canon. Its Apex compound (2024 +2471%, 22-now +549%, 2020-now +393%) UNDERSTATES honest result — winners were over-charged 3%. Asymmetric rebuild → higher compound. |
| 2 | **14 other honest packs** (the deferred grind) | not built yet | `bash experiments/version_alpha_mining/rebuild_honest_packs.sh` (uses active config = asymmetric) | Versions 69,68,59,58,57,52,50,46,44,43,40,37,32,27. Serial. Now correctly uses the canon. |
| 3 | **D2 cost diagnostic** (v69 retune "−45% at 6% spread / break-even ~3%") | flat 6% round-trip on ALL trades | re-run `experiments/v69_portfolio_retune/` cost diag with asymmetric model (SLIP_ENTRY=SLIP_TP=0, SLIP_SL=SLIP_HARD=−0.015) | THE decision-relevant one. The "doesn't beat SPY after costs" verdict hinged on the flat 6%; under asymmetric the effective haircut is ~forced-exit-only and likely lands near break-even, not −45%. Re-settles fundability. |
| 4 | **Params-agent MC sweep** (PID 6900, running) | OLD symmetric −1.5%/leg | coordinate: re-validate its winner under asymmetric before adopting | Its param search optimized under the superseded cost. Flag to user — two-agent coordination. |
| 5 | **portfolio_profiles.json** Sentinel/Core/Apex compound/DD metrics | old cost | `python trader.py temporal-refresh --profiles all` + profile compare re-run | Dashboard profile-compare numbers. |
| 6 | **Docs compound numbers** (known-issues CURRENT SHIP STATE; version-history v69/v70 sections) | mixed old | re-derive after #1–#3 land | The v69 "+130% / 35% DD" and v70 "+17,026%" were under symmetric/old cost. |

## Open calibration decision (single knob)

`SLIP_SL = SLIP_HARD = -0.015` is the **half-spread floor**. Forced exits face
spread *widening* (liquidity thins on adverse moves), so the honest value is
likely −0.02 to −0.03. Confirm the magnitude with the user, then it's a
one-line strategy_config change + the re-runs above. The STRUCTURE (asymmetric)
is canon; only the forced-exit magnitude is open.

## Not-yet-modeled drags (future, all conservative-direction)

- Entry mid-limit **fill probability < 100%** — you miss the immediate runners
  (adverse selection); modeled fills are biased toward stalling trades.
- **TP-touch-but-bid-didn't-reach** non-fills — a modeled win that didn't
  actually fill, then rides back down.
- **Illiquid names** — mid-fills/tight exits don't exist on thin 95+ signals;
  this is the Liquidity-Aware Cascade priority (known-issues #10).

These belong in a v1.1 cost model (fill-probability + liquidity filter), not the
v1 spread model shipped here.
