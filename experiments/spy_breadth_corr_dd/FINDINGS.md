# SPY↔breadth correlation / Market-Wave "breadth collapse → cut calls" — CLOSED AXIS, NO SHIP (2026-06-23)

**/research run.** User ask: *"Can we use the marketwave or refine it and find the correlation with
the SPY to detect market breadth collapses where it would be a bad time to be holding call options?"*

**Verdict: NO SHIP — closed axis.** The idea is already operationalized in TWO live v74 mechanisms,
and its literal core ("breadth collapse / SPY-breadth decoupling = bad time to hold calls → contract
call sizing") is the documented **crash-artifact trap**: on the Apex book those cohorts are
mean-reversion **WINNERS**, not avoidable drawdown. Re-confirmed here on fresh evidence.

---

## The idea is ALREADY SHIPPED — two live mechanisms (both in v74 STRATEGY_30DTE)

1. **MWDD** (McClellan breadth-momentum flat-band CALL-alloc dampener, shipped 2026-06-05 `d79f8a144`).
   Its FINDINGS open with the near-identical user ask: *"the portfolio drew down on a market pullback;
   can we use the market wave function and improve it / add components / shuffle weights?"* It tested
   exactly this and found: **crash-state contraction is FALSIFIED** (Market-Wave stress / negative
   McClellan / high crash_echo cohorts are mean-reversion winners, +0.094 mpnl, positive-EV 11/12
   windows); only the **flat/topping** breadth-momentum band (McClellan ≈ 0) is a genuine low-EV DD
   signal → shipped, VIX-panic-excluded. Also: "shuffle the regime/market-wave weights" is a **no-op
   for DD** (the live composite is dynamic VIX+inverted-breadth, trend=0; `SIGNAL_WEIGHTS` vestigial;
   the regime *amplifies* on weak breadth for WR-reliability, not DD).

2. **BDIV** (pre-top breadth divergence, shipped 2026-06-11 `3505c8770`). The literal "correlation with
   SPY to detect breadth collapse": **SPY within ~1-2% of its 60d high WHILE breadth rolls over** →
   CALL-alloc dampener. Hindenburg/omen family was NULL (0/24 omen days preceded a major onset);
   "drawdowns start at tops, not after warnings." The survivor is the SPY-near-highs × breadth-down
   divergence. Notably the **breadth-gap is an inverted-U** — deep/sharp breadth selloffs at highs are
   mean-reversion winners (the same trap).

The literal **sector-ETF "Market Wave"** the user names was a score-stage transform (v57) that was
**RETIRED in v71** (source CSV vanished → inert in all rows; rebuilt-source A/B showed it removes
ABOVE-baseline call winners, −28% N at 75+ = the breadth-crash-artifact trap). Priority #13
(cross-asset sector-ETF confirmation) is also a documented NULL (2026-05-12).

The DD-sizing well is documented **DRY (G23)** after RXDD + SVR + MWDD + TVDD + BDIV + F3F.

---

## Confirmation probe (read-only, no MC, no recalc) — `probe.py`

Tested the one formulation BDIV/MWDD did not *literally* use — a **rolling 20d Pearson correlation
between SPY daily returns and breadth daily change**, plus the **SPY-up/breadth-down 10d divergence**
and the user's literal **collapse_flag** (SPY up 10d AND breadth −5+/10d) — on the existing
`.cache/dd_ledger` MC tapes (6.37M calls-only trades, 12 windows). Substrate caveat: tapes are
v71-era; the market-context→per-trade-EV relationship under test is **version-robust** (it's about how
calls bought in different market regimes perform, dominated by the underlying market, not the marginal
v73→v74 lean — MWDD/BDIV themselves mined v70/v71 tapes and shipped to the v74 book). And the two
valid forms are already live in v74 regardless.

### (1) Full tape — the "collapse" cohorts are WINNERS, not low-EV

| cohort | n | mpnl | dd_conc | read |
|---|---:|---:|---:|---|
| corr **decoupled** (<0) | 178,783 | **+0.087** | 7.63 | WINNER (high conc = recoverable buy-weakness DD, the trap) |
| corr very-high (≥0.6, calm coupled) | 3.44M | +0.044 | 0.61 | the only low-EV cohort = SPY+breadth moving TOGETHER (the OPPOSITE of a collapse), ≈ baseline |
| collapse_flag = True (literal ask) | 624,051 | +0.017 | 2.62 | low-EV on the full tape **only** — see robustness |

### (2) Robustness (crash-artifact guard) — the one full-tape low-EV cohort is a crash-artifact + per-year sign-flipper

`collapse_flag=True` mpnl by window: 2024 **+0.095 (WINNER)**, 2023 +0.050, 2025 +0.063, 22-now +0.051,
but 2021 **−0.051**, 2022 −0.014, 5y **−0.045**, dip −0.012, 2020_crash −0.053. Low-EV mostly because
of crash windows (already collapse-handled by the dead-hold + VIX-panic exclusions) and it **INVERTS to
a winner in 2024** → fails the G26 sign-stability bar. Same story for corr-decoupled (WINNER in
2021/2023/2024/2025/22-now; low-EV only in the COVID crash).

### (3) DECISIVE — all-5-levers-off orthogonal slice (RXDD off ∧ F3F off ∧ MWDD off ∧ BDIV off ∧ TVDD off, 12.1% of book)

| cohort | n | mpnl | dd_conc | read |
|---|---:|---:|---:|---|
| corr decoupled (<0) | 15,813 | **+0.109** | 0.05 | mean-reversion WINNER |
| corr 0–0.2 | 30,292 | **+0.124** | 0.51 | WINNER |
| div SPY-leads mild/strong | 80,715 / 25,825 | **+0.075 / +0.223** | 0.51 / 0.12 | WINNER |
| **collapse_flag = True** | 6,331 | **+0.274** | **0.01** | **massive WINNER, ~zero DD contribution** |
| div1 breadth-leads-down | 396,976 | +0.037 | 1.37 | the *only* low-EV residual — weak (conc 1.37) + economically = mild generic breadth weakness already in MWDD/F3F |

In the region where every shipped lever is off, **every "breadth collapse" version of the user's signal
is a mean-reversion WINNER.** The one low-EV residual (breadth leading the market down) is weak
(conc 1.37 vs the 2.0+ that MWDD/TVDD shipped at) and collinear with MWDD's breadth-momentum lever.

---

## Why this is structural, not a tuning miss

The Apex edge **IS buying weakness** (75+ momentum calls + wide −70% SL + dead-hold V-recovery). So
calls bought into a SPY-breadth decoupling / breadth collapse **mean-revert and win** — hardest exactly
during the deepest breadth deterioration. The 2020/2020_crash/10y drawdowns (~77-80%) are the
*irreducible price* of that edge, not an avoidable bleed; contracting the crash cohort cuts alpha (G19)
and the DD-gate can't separate the bear-collapse losers from the bull-pullback winners (G23). The only
defensible forms of the intuition are the **flat/topping breadth-momentum band** (MWDD) and the
**pre-top SPY-near-high × breadth-divergence** (BDIV) — both already live.

## New-condition gate for any retry

Do **not** re-mine the breadth / Market-Wave / SPY-breadth-correlation axis as a CALL-DD sizing lever.
A retry is only justified by a genuinely **new mechanism CLASS** (G23), e.g. **option-pricing / IV-skew**
signals (data-blocked on `option_prices` coverage, the top NEW_LEAD) — NOT another breadth/regime sizing
feature. The flat-band (MWDD) and pre-top-divergence (BDIV) forms are exhausted/shipped.

Artifacts: `experiments/spy_breadth_corr_dd/{probe.py, probe_report.json}` (+ this file).
Precedents: `experiments/market_wave_dd_v70/FINDINGS.md` (MWDD), `experiments/dd_onset_omens/FINDINGS.md` (BDIV).
