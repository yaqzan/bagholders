# v70 Apex / Core / Sentinel Portfolio Profiles — Ship Handoff

**Date:** 2026-06-02 · **Type:** portfolio-only (NO `ALGORITHM_VERSION` bump) · **Status:** SHIPPED + ACTIVE · **Default profile:** Apex
**Scoring substrate:** v70 (`c70d16d22`, honest / look-ahead-free), full 10y (2016-2026, incl. 2020-COVID)

> **Folder-name note (don't trip on this):** this lives under `experiments/v69_portfolio_retune/` for
> historical reasons — the investigation began on v69 (the first honest, look-ahead-free version) and
> continued onto v70 (= v69 + the honest EARN_BOOST recalibration). **The substrate is v70, NOT v69.**
> Every MC run pinned `ALGORITHM_VERSION_PIN=c70d16d22`, which `monte_carlo.py` resolves to **version id 70
> (= the active scores version)**; every run header prints "v70 10y". Verified:
> `ALGORITHM_VERSION_PIN=c70d16d22 ... -> resolved version id = 70`.

This is the single-source handoff for the v70 portfolio-strategy rebuild. Everything below is live in the
canonical machine sources; the narrative docs (`trading-strategy.md`, `version-history.md`, `CLAUDE.md`)
are still being reconciled and may lag — **trust this doc + the machine sources, not those.**

---

## TL;DR

A full options-portfolio rebuild on the **honest v70 scores**, tuned for explosive early-buildout growth.
Three selectable profiles on a **return-vs-drawdown frontier**, all **calls-only**, all **collapse-rate = 0**
on every window including the 2020-COVID crash, all costed at a **realistic ~3% round-trip spread**.

| Profile | Threshold | Exposure (gross/call cap) | Base ceiling | 10y MedRet | 10y DD | 5y DD | 2020 DD | Role |
|---|---|---|---|---:|---:|---:|---:|---|
| **Apex** (default) | 75+ | 50% | **uncapped** | **+16,953%** | 86% | 84% | 82% | explosive (early/small book) |
| **Core** | 75+ | 40% | $2M | +7,422% | 87% | 79% | 79% | balanced |
| **Sentinel** | 85+ only | 30% | $1M | +3,371% | 66% | **37%** | 66% | preservation (mature/large book) |

(MedRet = median terminal return, $50k start, N=250 frontier; Apex cross-checked at N=300 = +17,026%.)
**collapse-rate = 0% on every (profile × window) cell**, including 2020-COVID. DD is *recoverable* drawdown,
not ruin — that distinction is the whole risk model (see "Risk framing").

---

## The shared call engine (all three profiles)

Profiles differ ONLY on **exposure cap + selectivity (threshold) + base ceiling**. They share one validated
**HOLD core** (in `strategy_config.STRATEGY_30DTE` / `OPT_30DTE`):

| Knob | Value | Note |
|---|---|---|
| Instrument | 30 DTE ATM calls | calls only (puts off — see below) |
| TP | **+30%** premium | `TP_BASE = TP_STRESS = 0.30` (base==stress; breadth-adaptive OFF) |
| SL | **−70%** premium | `SL_BASE = SL_STRESS = -0.70` — WIDE (HOLD, not CUT) |
| Hard sell | **day 15** | `HOLD_DAYS=15`; ride to the WR15 barrier, NOT to expiry |
| Spread | **−0.015/leg** | `SLIP_* = -0.015` → ~3% round-trip (realistic mid-fill) |
| Cascade | **20/15/10/10** | 95+/85-94/80-84/75-79 (`overflow`=0); Sentinel zeroes the 80-84 & 75-79 tiers |
| Max positions | **14** (all calls) | `MAX_POSITIONS=14, MAX_POSITIONS_CALL=14, MAX_POSITIONS_PUT=0` |
| Puts | **OFF** | `PUT_TIER_ALLOC` all 0, `PUT_PREMIUM_CAP=0` |

Per-profile overlay (in `algorithm_versions/portfolio_profiles.json`):

| Param | Apex | Core | Sentinel |
|---|---|---|---|
| `gross_cap` / `call_cap` | 0.50 | 0.40 | 0.30 |
| `capital_ceiling` | **0 (uncapped)** | 2,000,000 | 1,000,000 |
| `tier_ultra/top/mid/low` | .20/.15/.10/.10 | .20/.15/.10/.10 | .20/.15/**0/0** |
| effective threshold | 75+ | 75+ | 85+ |
| DD soft-band (lo/hi/floor) | .35/.55/.40 | .35/.55/.40 | .35/.55/.40 |

---

## The five load-bearing findings (why it's built this way)

1. **HOLD ≫ CUT for calls.** Wide SL (−70%), sell at day 15 — do NOT early-stop. ~68% of "losers" recover to
   TP; cutting them bleeds that. "Capital velocity / fast recycling" was a frictionless-era fiction — at real
   ~3% spread, high turnover *is* the bleed (CUT-at-70+ collapsed 100%). "HOLD" still sells at day 15
   (riding to 30-DTE expiry was strictly worse).
2. **Exposure peaks at ~50%, over-deployment HURTS** (capital-velocity law: bigger sizing → deeper drawdowns →
   less capital survives to compound). 50% (+16,953%) > 65% > 100%/off (+6,365%). Apex's explosiveness is the
   **75+ signal density + SL/TP**, NOT cranking allocation.
3. **The capacity ceiling is a pure growth-vs-realism dial — NOT a risk knob.** DD% and collapse are
   scale-invariant (flat ~86% / 0% across $250k → uncapped); only MedRet moves (+3,623% → +16,953%). So the
   backtest always prefers higher; the ceiling is a *liquidity + lifecycle* judgment. **Apex = uncapped**
   (max compounding; the cap never binds while the book is small, and you migrate to Core/Sentinel before it
   would). Core/Sentinel cap for realism at larger books.
4. **Selectivity (85+) is the real DD lever, NOT exposure.** Sentinel's 85+ cut 5y DD 84% → 37%; Core's
   exposure cut only trimmed it to 78.6%. That's why Sentinel = higher threshold, not just lower size.
5. **Puts are dead — rigorously closed.** Tested as a slot-filler / idle-gated tail, CUT *and* HOLD,
   down to the **smallest possible sleeve (<15-only, 5% cap, 1 slot, putTP up to 63.6%)**. Every sliver
   **introduces collapse** (0.5–8%, baseline 0%), **worsens 2022 DD +5 to +8pp**, and **guts 10y return 33–52%**.
   Mechanism: put losses **cluster in the same stress where the calls bleed** and consume the **cash buffer**
   that is the survival mechanism — net-harmful, not anti-correlated. The ~68% generic put WR is real
   directional signal but is NOT convertible into portfolio hedge value at any sleeve size. **Do not re-open
   without a fundamentally different mechanism** (e.g. a reserved-cash put ledger that can't touch the call
   buffer — untested).

---

## Risk framing (read before quoting the numbers)

- **collapse-rate = 0 is the one hard floor for every profile, incl. Apex.** Ruin (account → ~0) is
  unrecoverable at any book size. A high *recoverable* DD (ride 86% down and back) is the accepted Apex
  budget; a collapse is not. Every shipped config is collapse=0 on all windows incl. 2020-COVID.
- **DD is a budget scaled to portfolio maturity, not a universal gate.** Apex (small book + regenerating
  income) accepts high recoverable DD for compounding off a small base; you migrate Apex → Core → Sentinel
  as the book grows and the same % DD becomes catastrophic dollars.
- **This is a leveraged-momentum sleeve, not proven alpha.** Honest v70 removed ~12pp of look-ahead edge;
  the residual per-trade edge is thin (momentum-beta + option convexity). The +16,953% is the *model's*
  compounding of an aggressive 75+/HOLD/50%-exposure leveraged-call book at ~3% spread — explosive but
  high-DD, validated survivable (collapse=0), NOT a claim of beating buy-and-hold on a risk-adjusted basis.
  Treat the headline as "max survivable compounding on this substrate," with the 86% DD as the price.

---

## Canonical sources (what the engines actually read)

| File | Holds |
|---|---|
| `strategy_config.py` → `STRATEGY_30DTE` / `OPT_30DTE` | Apex (default) base config — TP/SL/SLIP/cascade/caps/ceiling/puts |
| `algorithm_versions/portfolio_profiles.json` | the 3 profile overlays (base v70, default=apex) |
| `portfolio_profiles.py` → `DEFAULT_PROFILE_KEY` | `"apex"` |

Every consumer derives from these: MC (`monte_carlo.py`), deterministic backtest (`backtest_cascade.py`),
`/api/backtest/run`, `/api/allocation/live?profile=`, `/api/backtest/temporal?profile=`, and the React
Backtest/Allocator/Assessment pages (profile toggle snaps all advanced params + Calls Only + min call score).

## Verify / run

```bash
python tests/test_strategy_config_drift.py        # 579 constants consistent (engines == config)
python tests/test_mechanism_registry.py           # 138 checks
python trader.py algorithm active                 # active scoring = v70 (c70d16d22)
# Per-profile resolution sanity:
python -c "import portfolio_profiles as pp, strategy_config as sc; \
[print(k, *( (lambda c,_: (c.GROSS_PREMIUM_CAP, c.PRACTICAL_CAPITAL_CEILING, c.TIER_ALLOC['low']))(*pp.profiled_strategy_config(sc.STRATEGY_30DTE,k))) ) for k in ('apex','core','sentinel')]"
```

## Evidence (this experiment folder)

- `MASTER_FINDINGS.md` — full narrative (look-ahead bug → honest v70 → Apex build → put closure)
- `profile_frontier.py` — the Apex/Core/Sentinel frontier run (N=250, v70 10y)
- `n300_confirm.py` — Apex N=300 lock (+17,026% / 0 collapse)
- `ceiling_curve.py` — the ceiling-is-a-growth-dial proof ($250k → uncapped, DD flat)
- `put_tail_tiny.py` — the put-closure run (1-slot sliver still net-harmful)

## Known gap

Narrative docs (`trading-strategy.md` authoritative snapshot, `version-history.md` section, `CLAUDE.md`
version lines, the detailed `known-issues.md` param table) still describe the prior v69-hygiene config and
are pending line-by-line reconciliation. The machine sources + this doc are authoritative meanwhile.
