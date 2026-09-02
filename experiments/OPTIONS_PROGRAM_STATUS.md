# Options-Signal Program — multi-agent coordination

Parallel agents are mining option-structure signals. This file is the ownership +
shared-artifact contract so we don't step on each other. Chats are NOT connected —
**coordination is async via the user** (the human relays direction memos between agents).

## Arms & ownership
| Arm | Signal | Owner | Namespace | Status |
|---|---|---|---|---|
| OSK | `opt_skew` (10%OTM put−call IV) | (shipped analysis) | `experiments/iv_skew/`, `year_2024_factor/` | CONFIRMED per-trade residual, Polygon-data-locked |
| Term-structure + OI | IV term-structure slope; put/call OI imbalance | **Claude Code (main)** | `experiments/iv_skew/term_structure_*` | DONE — both NULL after verification (see findings) |
| Dealer-GEX | gamma exposure / positioning levels | **Fable** | `experiments/gex/` | in progress |

## ⚠ CROSS-ARM METHODOLOGY FINDINGS (from the term-structure/OI arm, 2026-07-06) — ALL ARMS MUST HEED
The OI-imbalance feature PASSED the naive orthogonalized bar (t=−3.37, sign-stable across
sub-periods) then DIED in verification. Two lessons that invalidate the naive test template:
1. **`pnl15` is heavy-tailed → raw-OLS t-stats are outlier-garbage.** Sanity proof: opt_skew (the
   CONFIRMED edge, spearman +0.110) shows OLS t=+0.26 on raw pnl15. Use RANK stats (spearman) or
   winsorize/rank-transform the label before any OLS. Trust the rank correlation, not the raw t.
2. **MANDATORY within-symbol (symbol fixed-effect) test.** A cross-sectional signal with ~4
   obs/symbol in ONE regime can be pure symbol selection. oi_imb: cross-sectional spearman −0.06 /
   OLS t=−3.37, but WITHIN-SYMBOL spearman −0.001 (t=−0.80) — it was "which names ran," not timing.
   **GEX features are symbol-persistent (a name's gamma profile is sticky), so this is GEX's #1
   failure mode.** Any GEX feature MUST survive demeaning by symbol, or it's the same artifact.

## Shared READ-ONLY artifacts — never overwrite these
- `.cache/rel_strength/rs_ledger.parquet` — 10y 70+ signal universe + underlying labels (mfe15/mae15).
- `.cache/iv_skew/proxy_ledger.parquet` — labels+controls (opt_skew, semivol_r, stock_r20, overall, pnl15) on the 1.3y overlap.
- `.cache/iv_skew/iv_ledger.parquet` — 1.3y ATM-IV panel (holdout-capped at 2026-05-15).
- `.cache/experiment_data/option_slice_ivledger.parquet` — ±25% near-money chain per signal (Claude Code built).
- `.cache/iv_skew/iv_ledger_ext.parquet` — **fresh panel 2026-05-16 → today** (Claude Code, 2026-07-06), version_id=74. Columns match iv_ledger + {is_oos, pnl15_resolved, version_id}. Use this for any post-5/15 data.

## ⚠ DATA CURRENCY & THE CUTOFF — READ THIS (why "data ends in May" is an illusion)
- option_prices + price_history are CURRENT to **2026-07-06**. The cached ledgers (rs_ledger,
  proxy_ledger, iv_ledger, option_slice) all STOP at 2026-05-15 because `build_iv.py`/`build_proxy.py`
  hardcode a STALE `CUTOFF="2026-05-15"`. Reading only those makes it look like data ends in May — it
  does not. **Bug to fix eventually:** those builders should read `strategy_config.CALIBRATION_CUTOFF_DATE`.
- The LIVE holdout line is **`CALIBRATION_CUTOFF_DATE = 2026-06-15`** (moved forward from 5/15).
- Coincidence that matters: today − 15 trading days ≈ 6/15 too. So:
  - **In-sample GAP 2026-05-16 → 6/15** (~1 month): below the live cutoff, 15d-fwd RESOLVED → usable NOW,
    adds ~540 fresh 70+ signals of test power. In `iv_ledger_ext.parquet` (`is_oos=false`).
  - **True OOS > 6/15** (~303 signals): genuine holdout, but 15d-fwd P&L is NOT ripe (pnl15 nulled;
    `pnl15_resolved=false`). **An OOS win-rate/P&L read is premature until ~mid-July** — re-run
    `build_iv_ext.py` then and the labels fill in. Features (atm_iv/skew) are already present.
- Fresh-month sanity: opt_skew spearman(skew,pnl15)=+0.306 on the gap month (N=540) — OSK persists/strengthens in the newest data.

Each agent writes ONLY in its own namespace + NEW files. Fable builds its own wider
`gex_chain_*` cache; do not repoint the shared ones.

## Shared discipline (all arms)
- Holdout lock: `CALIBRATION_CUTOFF_DATE=2026-05-15`; `assert_no_holdout_leak` on in-sample inputs.
  Data now runs to 2026-07-06 → **2026-05-16..2026-07-06 is genuine OOS** (first forward read).
- READ-ONLY offline experiments; no edits to scoring.py/core.py/simulator.py/api.py/strategy_config.py; no env-gates.
- Stay in the option-pricing/positioning family; directional price re-grading is CLOSED (~47% WR).
- Orthogonality to `opt_skew` is the bar. Per-trade t-stat is the CEILING (single selloff regime, limited N) — no ship claim.

## ⚠ GEX DATA CONSTRAINT (verified 2026-07-07) — READ BEFORE DESIGNING A GEX BACKFILL
Polygon Options Developer (now purchased) was entitlement-verified: **historical open interest
does NOT exist at ANY Polygon tier** (OI is snapshot-only: "quantity held at end of the last
trading day"; no OI endpoint/flat-file). **GEX is computed FROM open interest**, so the GEX arm
**cannot be backfilled from Polygon** — it is limited to our own accumulated `option_prices.open_interest`
(~1.3y, MySQL), or needs a different vendor (ORATS/CBOE). The IV/OSK arm is unaffected (it computes IV
from daily *premiums*, which Polygon does serve 4yr back). Fable: do not architect a multi-year GEX
backfill on Polygon — it can't exist. Scope GEX to the ~1.3y MySQL OI depth, or flag the vendor gap.

## Fable = overall program architect (invited)
Fable is the most capable model here; it is explicitly encouraged to **critique the whole
program and propose adjustments** — the arm taxonomy, label choices, OOS methodology, or the
term-structure design — not just its GEX arm. Put any cross-arm direction in a **DIRECTION MEMO**;
the user relays it back to Claude Code, which will incorporate. Architectural judgment overrides
the plan where Fable sees better.
