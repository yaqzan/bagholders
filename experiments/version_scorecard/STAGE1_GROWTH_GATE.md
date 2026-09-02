# Stage-1 Hydration-Adjusted Growth Gate

Deterministic, pack-only, **no-MC** refinement of the Stage-1 scoring ship gate.
It nets per-trade quality (WR15) against cash-rotation velocity (hydration) into a
single growth-rate proxy, so a "WR up / N down" candidate gets an automatic
**SHIP / FLAG / BLOCK** verdict instead of a human reading the W4/W5/W6
sub-metrics by hand.

Tool: [`stage1_growth_gate.py`](stage1_growth_gate.py) · supply input: [`signal_supply.py`](signal_supply.py)
Status: **WIRED into `assessment-backtest.md` Stage 1 (W5 + W6) as of the 2026-06-11
ship-gate reform.** The gate now also computes **W6** (noise-aware gradient: thin
bands pool upward at N<100, inversions count only at pairwise z ≤ −2 on shrunk
values, and only candidate-INTRODUCED real inversions FLAG — inherited ones
report without escalating). Companion tool: [`tier_drift.py`](tier_drift.py),
the cross-version control chart that owns slow thin-tier rot (the failure mode
per-ship gates structurally cannot see).

> **⚠ §5's historical replay anchors are INVALIDATED (discovered 2026-06-11).**
> The 2026-06 honest-era recalcs overwrote pre-v69 score rows/packs — e.g. the
> v40 pack now carries honest-recalc WR (80-84 call 67.7%, vs the inflated-era
> 80.5% the v40→v42 BLOCK was validated against), so the documented disaster
> delta no longer exists on disk and `--replay` returns FLAG where the table
> below says BLOCK. **Regression-test the verdict logic with `--selftest`**
> (pack-independent, synthesizes identity→FLAG / +13pp→SHIP / v42-signature
> disaster→BLOCK / single-band tail-loss→not-SHIP from any live pack; 4/4 PASS
> at introduction). §5 is kept as the historical validation record.

---

## 1. The problem it automates

Today Stage 1 ends in a cluster of separate pass/fail checks — W4 (per-bucket
non-regression), W5 (recycle coverage, shortfall %, p20 rolling) — plus a
paragraph of "N-awareness judgment" prose telling a human how to weigh
"80+ N up = good, 70-74 N down = fine, 75+ starvation = bad." That prose *is* an
algorithm. This gate is that algorithm, written down.

The mechanism is a per-unit-time log-growth rate:

```
g  =  ebar  ·  lambda_eff
```

- **`ebar` (quality)** — the per-trade **log edge of the *filled* book**. The
  cascade fills only `demand` ≈ 6.19 slots/day (14 slots / ~2.26-bar hold),
  highest conviction first, so over-supply can't inflate `ebar` — it only changes
  *which* tiers fill. Built from `wr_shrunk` / `tp_shrunk` per band via a fixed
  option payoff. This is the same per-trade-quality signal as the scorecard's `Q`.
- **`lambda_eff` (velocity)** — `demand · recycle_coverage`. **Saturates** at the
  full book (extra supply earns zero credit) and is **drought/burstiness-aware**
  (coverage = mean of per-day `min(supply, demand)`, so a 0-signal day next to a
  50-signal day scores worse than two steady days).

The user's exact question — *"WR15 up but N down, is it worth it?"* — becomes a
one-line computation: **does `g_cand` confidently clear `g_base`?**

- N drops but supply was already over-provisioned (the usual case): `lambda_eff`
  unchanged → `g` rises iff `ebar` rises → **SHIP**. ("N-down is fine.")
- N drops enough to push supply below `demand` in some window: `lambda_eff` falls
  → `g` can fall even though `ebar` rose → **FLAG/BLOCK**. ("The WR gain isn't
  worth the lost cash-rotation.")

It also handles the *other* direction — **N up / WR down** (the v42 rolling-weekly
and v58 volume-Q traps): saturation gives the extra volume zero credit, so the
quality term dominates and the candidate is correctly blocked.

---

## 2. Why it is legitimately a Stage-1 device

Stage 1 must be **barrier-independent** and **MC-free** (the doctrine in
`assessment-backtest.md` — otherwise scoring sweeps converge to barrier-overfit
local optima). This gate honors that:

- **Deterministic** — no MC noise. Runs in seconds off existing research packs.
- **Payoff `(f, w, l)` is held CONSTANT** — the scoring sweep cannot tune a
  barrier to win; `g` moves only when WR15 / supply move. (The nominal 30 DTE
  payoff reproduces the documented break-evens exactly: call 45.0%, put 36.4% —
  a good sign the constants are right.)
- **`g` is a relative RANKING number** with arbitrary units. Only `dG` vs
  baseline is ever read. It is **not** a portfolio compound forecast and not a DD
  metric — DD stays Stage-3's primary axis, untouched. This respects the
  "compound is theoretical above ~1e10%" doctrine: `g` is a per-unit-time
  log-edge rate, not a wealth projection.

It also **can't re-introduce the v58 volume bug**: `ebar` is N-neutral and
`lambda_eff` saturates, so signal count enters *only* through `lambda_eff` and
*only below saturation* — never double-counted.

---

## 3. The model in detail

### 3a. Per-trade log edge (fixed payoff)

```
e(p, f, w, l) = p · ln(1 + f·w) + (1-p) · ln(1 - f·l)
```

`p` = shrunk WR15 (`wr_shrunk`) or option TP rate (`tp_shrunk`) per band;
`f` = cascade alloc for that tier; `(w, l)` = nominal TP/SL on premium. Monotone
increasing in `p`.

### 3b. Filled-book quality `ebar` (greedy conviction-priority fill)

Each band has mean daily supply `s_t = N_t / active_days`. Fill `demand` slots/day
highest-conviction-first across both sides (a 76 call competes with a 24 put — both
conviction ~26). `ebar` = supply-weighted mean `e` over the filled slots.
A pruning ship that removes low-WR signals lets the fill back-fill with higher-WR
tiers → `ebar` up. A volume dump of low-WR signals fills slots with low-edge
trades → `ebar` down.

### 3c. Velocity `lambda_eff` (saturating, drought-aware)

```
lambda_eff = demand · recycle_coverage
recycle_coverage = mean_over_days( min(supply_day, demand) ) / demand
```

From `signal_supply.py`. Saturates at `demand`; drops below 1.0 only when some
days fall short (droughts / burstiness).

### 3d. Dual barrier (closes the SVD generic-vs-option gap)

`utility_5y_wr15.json` carries **both** barriers per band:

- `g_option`  : `p = tp_shrunk` — option-aligned 30dte_opt TP rate. **PRIMARY** (tradable).
- `g_generic` : `p = wr_shrunk` — generic K=2σ/M=5σ WR15. Directional sanity.

Rule: **BLOCK if *either* barrier confidently regresses** (guards against an
option metric that is itself a barrier-overfit artifact); **SHIP keys on the
primary (option) barrier** being confidently fine, with the generic only required
*not to confidently regress*. On v42 the generic `dG` was a shrug (−1.0%) but the
option `dG` was −56.9% — the option barrier caught the collapse the generic one
missed. That is the P0/SVD divergence, automated.

### 3e. Binding-window check (bear-tape drought)

The 5y mean is **never** the binding constraint — supply (~12–14/day) runs ~2×
demand (~6.2). Thin regimes (2022 bear, the dip) are. Quality is **era-stable**
(recency ratio ~0.97–1.00 across versions), so the gate holds `ebar` at 5y and
recomputes **`lambda_eff` per window** (2022 / 2023 / 2024 / 2025 / dip / 5y), then
**gates on the worst (min-`g`) window**. A candidate that prunes call supply
specifically in 2022 is caught even when its 5y-mean coverage looks fine.

---

## 4. Verdict logic (statistical, not point-estimate)

A bootstrap (binomial-normal resample of each band's WR by its N) gives a CI on
`dG`, so the noise band is the **real sampling error of the thin top tiers**
(95+ is N~15–30/5y), not an arbitrary constant.

```
BLOCK  if W4-severe (z<=-3, >=1.5pp, N>=100 on a band)
       OR worst-barrier p95 (best case) still < -eps      (confident regression)
SHIP   if option-barrier point dG >= -eps AND option p05 >= -2eps AND no real W4 dip
FLAG   otherwise  (tie / wide CI / real-but-small W4 dip → route to Stage 2/3)
```

All three thresholds gate on the **binding window**. `eps = 1%` (a knob).

**W4 stays a SEPARATE hard guard** — a scalar can mask within-tier asymmetry (the
ICH put-`<10` trap), so per-discrete-bucket non-regression is checked
independently and made **noise-aware** (two-proportion z, not fixed pp): an N=78
tier moving −1.5pp is noise (z −0.24); an N=2500 tier moving −12pp is real (z −5.4).

**Design choice:** scoring-neutral ties → **FLAG, not SHIP.** Stage 1 will not
auto-greenlight a change whose value is downstream; it routes it to confirmation.
FLAG ≠ BLOCK — nothing is stopped out.

---

## 5. Validation — replay against documented ships/reverts

Run: `python experiments/version_scorecard/stage1_growth_gate.py --replay`

| Case | Production reality | Gate verdict | Why it's right |
|---|---|---|---|
| **MCD** v39→v43 | shipped (net+) | **FLAG** — dG tie, CI straddles 0 | scoring-neutral; real win was DD/portfolio → correctly routed to Stage 2/3, the path it took |
| **ICH** v43→v44 | shipped (net+) | **FLAG** — tie; put-tail tagged `noise` (z −0.6..−0.9) | the `<10` "trap" is within sampling noise on 5y → surfaced, not blocked |
| **rolling-weekly** v40→v42 | **reverted** (disaster) | **BLOCK** — option dG −56.9%; W4 z=−3..−8 across ~every band | the one true catastrophe is the only BLOCK |
| **continuation** v57→v58 | **reverted** | **FLAG** — scoring-neutral | reverted for a **Stage-3 DD** reason Stage-1 can't see — the gate defers honestly instead of pretending |
| **reverse-v42** v42→v40 | (undo disaster) | **SHIP** — option dG +131.9%, generic not regressed | proves SHIP fires on a confident win |

The gate **never wrongly blocks a real ship, decisively blocks the one
regression, and correctly defers the case whose fate lives downstream.**

**Per-window binding check (confirmed live):** verdicts are unchanged, but the
gate now reports the binding window. MCD binds on **5y** (its 2022 dG −1.7% is
*better* than 5y → it did NOT starve bear tape). ICH binds on **2022**
(option dG −2.56% vs −2.30% at 5y → its velocity cost concentrates in bear tape —
a real 0.26pp signal the 5y mean masked; still a tie → FLAG, but now visible).
v58 binds on 2024; v42 is −57% in *every* window (regime-agnostic disaster). None
of the historical ships had a *severe* hidden bear cut — good to confirm.

**Teeth test** (`--demo-drought v57`): clone v57 as its own candidate, **WR
identical**, cut only 2022 coverage 0.945→0.614. 5y dG = **0.00%** (a 5y-only gate
says SHIP) → binding **2022 dG −35%, p95 −32.9% → BLOCK.** The bear-tape gate
catches a supply drought the 5y mean is completely blind to — the exact failure
mode this extension exists for.

Two properties proved by the data itself:
- **Saturation is load-bearing.** v42's supply is **27/day** with the *highest*
  coverage (0.976) — a volume metric ranks it #1 (the v58 bug). Saturating
  `lambda_eff` flips it to a hard block.
- **The modern stack is a tie band** (g_opt clusters 55–61; per-trade WR span
  ~1.9pp). The gate manufactures **no false precision** among good siblings; its
  discriminating power is reserved for outliers. (Active-champion v60 sitting
  mid-pack on Stage-1 `g` is correct — its edge is *portfolio DD* from the Tier-3
  MC layer, which Stage-1 quality×velocity deliberately doesn't see.)

---

## 6. Proposed `assessment-backtest.md` change (Stage 1 W4–W6)

**Keep W1–W3 as hard guards** (cohort-z ≥ +3, multi-barrier consistency,
multi-time-window sign). **Replace W4–W6** with:

- **W4 (hardened, noise-aware):** per-discrete-bucket non-regression via
  two-proportion z. Escalate only `z ≤ −2`; BLOCK only `z ≤ −3 ∧ ≥1.5pp ∧ N≥100`.
  Catches the v42 tail; ignores thin-N wiggles. *Separate hard guard — never
  folded into the growth scalar.*
- **W5/W6 → one growth verdict:** `g = ebar · lambda_eff` on both barriers,
  bootstrap CI, binding-window gate, SHIP/FLAG/BLOCK. This is the W5 recycle
  cluster + W6 gradient + the N-awareness prose, fused into one auto-decision.

Workflow: after `recalculate` + `assess --force` + research-pack build for the
candidate, run `signal_supply.py --versions <baseline>,<candidate>` then
`stage1_growth_gate.py --baseline <b> --candidate <c>`. Seconds, no MC.

---

## 7. Inputs, knobs, caveats

**Inputs (already on disk; no recalc, no MC):**
- `.cache/algorithm_versions/<v>/research_pack/utility_5y_wr15.json` — per-band
  `wr`/`wr_shrunk` (generic) and `tp`/`tp_shrunk` (option), with `n`.
- `.cache/algorithm_versions/_scorecard/supply_burstiness.json` — per-version +
  per-window `recycle_coverage` (from `signal_supply.py`).

**Knobs:** payoff `(f, w, l)` = nominal 30 DTE cascade/TP/SL; `eps` = 1%;
W4 z-thresholds; shrink_k inherited from the pack. The payoff reproduces the
documented break-evens, but all are tunable.

**Caveats / not done:**
- Per-tier fill uses **mean** daily supply, so *tier-level* burstiness isn't
  modeled (total-supply burstiness is, via `recycle_coverage`).
- `ebar` is held at 5y per window (era-stability). If a candidate ever shows
  era-divergent per-trade quality, a windowed `assess` would be needed for
  per-window `ebar` (~half a day; skipped for now).
- The gate is a **relative ranking on the generic+option barriers**, not a return
  forecast. The true option-capture tax remains Stage 2's job; the dual-barrier
  `g` mitigates but does not replace it.
- Because supply is over-provisioned, the gate **mostly reduces to per-trade
  quality** today — the hydration term is dormant insurance that only activates
  on a supply-cutting change. That's the correct, safe default.
