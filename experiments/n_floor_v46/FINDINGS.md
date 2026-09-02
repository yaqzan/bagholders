# N-Floor Empirical Replay — v46 Provisional Baseline

**Date:** 2026-05-08
**Active version:** v46 (commit `f274eb6`, WVD-Wave score-stage modulator)
**Window:** 2020-01-01 → 2026-05-08 (1,627 trading days, 6.5 years)
**Strategy snapshot:** 30 DTE bounded-fill MC config (commit `cf058ed`)
- `MAX_POSITIONS = 14`
- `TIER_ALLOC = {95+: 0.20, 85-94: 0.15, 80-84: 0.10, 75-79: 0.10, 70-74: 0.00, p<=15: 0.12, p16-20: 0.10, p21-25: 0.08}`
- F3F breadth-driven alloc, H3 DD soft-band, dead-hold, SAW Put U-curve all live

**Status:** Provisional. Recalibration triggered by any portfolio ship OR any
scoring ship that shifts ≥30% offered/year on any binding tier.

---

## Headline result — the cascade is in steady state

| Day classification | Days | % |
|---|---:|---:|
| `filling` (pool not full, ≥1 signal, cash sufficient) | 1,456 | **89.5%** |
| `pool_full` (already at 14 positions) | 139 | 8.5% |
| `signal_bound` (pool open, no signals offered) | 32 | 2.0% |
| `cash_bound` (cash < min tier cost) | 0 | 0.0% |
| `idle` (pool empty AND no signals) | 0 | 0.0% |

**Cash is never the binding constraint.** Signal supply is the binding constraint
on 2.0% of trading days. The cascade is operating well within its design envelope.

| Open-slot count | Days | % |
|---|---:|---:|
| 0 | 1 | 0.1% |
| 1–3 | 40 | 2.5% |
| 4–7 | 389 | 23.9% |
| 8–13 | 1,058 | **65.0%** |
| 14 (full) | 139 | **8.5%** |

The pool sits at 8–14 slots on **73.5% of days** — well-loaded but rarely full.
Tail at 0–7 is regime-driven (deep DD events, breaker tripped, dip windows).

---

## Per-tier offered vs filled (full window)

| Tier | Offered/yr | Fills/yr | Fill rate | offered_p50 | offered_p75 | offered_p90 | fill@offer_mean |
|---|---:|---:|---:|---:|---:|---:|---:|
| **95+** | 40.6 | 24.8 | **61.1%** | 0 | 0 | 0 | 1.10 |
| **85-94** | 96.3 | 67.4 | **69.9%** | 0 | 0 | 1 | 1.12 |
| **80-84** | 187.6 | 111.5 | **59.5%** | 0 | 1 | 2 | 1.24 |
| **75-79** | 498.7 | 247.0 | **49.5%** | 1 | 3 | 5 | 1.52 |
| 70-74 (overflow, alloc=0) | 4,671.2 | 0.0 | 0.0% | 15 | 25 | 37 | 0.00 |
| **p≤15** | 237.0 | 122.8 | **51.8%** | 0 | 1 | 3 | 1.02 |
| **p16-20** | 550.0 | 189.9 | **34.5%** | 1 | 3 | 6 | 1.08 |
| **p21-25** | 1,330.5 | 263.2 | **19.8%** | 4 | 7 | 11 | 1.17 |

**Binding-vs-surplus classification** (driven by fill rate + saturation curve):

| Tier | Class | Why |
|---|---|---|
| 95+ | **binding** | Cascade fills 61% of offered; current offered too sparse to ever saturate (mean 0.16/day vs cascade appetite for 5+/day). Any reduction in offered hurts fills ~1:1. |
| 85-94 | **binding** | Highest fill rate (70%); same logic — signal-limited, not slot-limited. |
| 80-84 | **binding** | 60% fill rate; saturation point ~8/day not reached at current 0.74/day mean. |
| 75-79 | **binding (borderline)** | 50% fill rate; saturation onset ~5/day, current 1.98/day mean. Mild surplus on heavy-supply days only. |
| p≤15 | **binding** | 52% fill rate; saturation onset ~6/day, current 0.94/day mean. |
| p16-20 | **mild surplus** | 35% fill rate; saturation onset ~6/day, current 2.18/day mean — near saturation in tail of distribution. |
| p21-25 | **heavy surplus** | 20% fill rate; cascade routinely sees 6+ offers but pool slots full. ~80% of offered are wasted. |

---

## N-vs-fills saturation curve (key bins)

Reads as: on days where tier T was offered N signals, how many filled?

### Calls
```
75-79:  offered=1: 412 days  74% fill rate
        offered=2: 217 days  73%
        offered=3-4: 229 days  66%
        offered=5-7: 112 days  51%   ← saturation onset
        offered=8-12: 57 days  23%
        offered=13+: 25 days  2%
```

### Puts
```
p21-25: offered=1-2:  410 days  47% fill rate
        offered=3-5:  486 days  36%
        offered=6-10: 363 days  21%   ← cascade saturated; surplus zone
        offered=11-20: 151 days  6%
        offered=21+:  44 days  1.5%
```

The cascade's appetite is tier-cumulative — the highest-conviction bucket fills
first, then the next, so lower-priority tiers see slot-cap saturation earlier
than higher tiers. The 6-slot saturation onset on puts reflects calls having
filled ~8 of 14 slots already on those days.

---

## Year-by-year stability

| Year | Total fills | 95+ | 85-94 | 80-84 | 75-79 | p≤15 | p16-20 | p21-25 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2020 | 870 | 27 | 78 | 100 | 255 | 85 | 142 | 183 |
| 2021 | 897 | 30 | 101 | 156 | 350 | 39 | 47 | 174 |
| 2022 | 1,091 | 38 | 66 | 86 | 195 | 139 | 276 | 291 |
| 2023 | 1,091 | 17 | 27 | 50 | 140 | 191 | 294 | 372 |
| 2024 | 1,131 | 16 | 64 | 143 | 244 | 159 | 207 | 298 |
| 2025 | 1,076 | 26 | 62 | 113 | 235 | 151 | 206 | 283 |
| 2026 (partial) | 472 | 6 | 37 | 72 | 176 | 29 | 54 | 98 |

Total fills stable at ~1,000-1,100/year (the 2020 dip is partly trading-day
calendar variance). Tier composition shifts with regime: 95+/85-94/80-84
calls dominate in bull windows (2020-2021, 2024); puts dominate in 2022-2023.
**The N floor is robust across regimes** — no need for per-regime decomposition.

---

## Provisional N-floor table — H6 gradient gate

Two equally-valid views of the offered/yr counts depending on measurement method:

| Tier | Class | Replay (offered/yr) | check_signals.py (offered/yr) | **Floor (script)** | Buffer |
|---|---|---:|---:|---:|---:|
| 95+ | binding | 41 | 31 | **26** | 15% |
| 85-94 | binding | 96 | 98 | **83** | 15% |
| 80-84 | binding | 188 | 194 | **164** | 15% |
| 75-79 | binding | 499 | 514 | **436** | 15% |
| p≤15 | binding | 237 | 231 | **196** | 15% |
| p16-20 | mild surplus | 550 | 570 | **398** | 30% |
| p21-25 | heavy surplus | 1,330 | 1,353 | **541** | 60% |
| 70-74 | (overflow alloc=0) | 4,671 | — | n/a | n/a |

The **replay** (run.py + reaggregate.py) measures `outcomes_by_date` after
`compute_outcome` filtering — the authoritative count of what the cascade
actually sees on each day. The **check_signals.py** approximation is a
Score-table direct count with CT-promote applied; ~3% lower than replay on
most tiers, ~25% lower on 95+ (where compute_outcome captures extra
CT-promotions). The script-baseline values are the reference floor in the
H6 table — comparing the script's count across versions stays
method-consistent.

**H6 gradient — three-zone classification:**
- **SAFE** (offered/yr ≥ 95% of baseline): no concern.
- **MARGINAL** (in buffer zone): note in ship summary; flag cumulative drift.
- **REVIEW** (offered/yr < floor): compensation analysis required. Show
  `(offered_after × fill_rate × alloc × WR_after × hold_recip)` ≥ baseline
  cash-deployed-days/year. Allocation bumps and WR uplift can offset volume.
  Still a soft gate — REVIEW means "explain the trade-off," not "stop."

`check_signals.py` is the canonical fast check (~5 sec) and runs in both ship
procedures (see deploy.md). `run.py` + `reaggregate.py` is the authoritative
re-derivation if recalibration is needed.

---

## What the metric does NOT capture (caveats)

1. **Within-tier signal *quality* drift.** N-floor counts signals, not
   per-trade WR. A change that drops N by 10% but raises that cohort's WR by
   5pp is net-positive on compounding even if H6 flags it. The gate is a
   soft check, not a veto — the compensation curve is the explicit answer.

2. **Symbol re-entry block effects.** The cascade rejects same-symbol overlap.
   If a scoring change concentrates signals on fewer symbols, raw N can stay
   constant while *effective* signals available to cascade drop. Not measured
   here. Possible follow-up if a scoring change is suspected of this.

3. **Cross-tier substitution.** A change that shifts signals from 75-79 to
   85-94 (e.g. a confidence dampener) reduces 75-79 offered/yr but raises
   85-94 offered/yr. Each tier might fail H6 individually while total
   throughput improves. The compensation curve handles this; H6 enforcement
   should be tier-by-tier *not* sum-of-tiers.

4. **DD-breaker / SAW / F3F path-dependence.** The 8.5% pool-full days
   include DD-breaker activations. Floor was derived from realized fills,
   so the breaker's effect is already baked in. Re-derive after any
   portfolio-mechanism ship that changes path-dependence dynamics.

5. **Provisional baseline.** v46 is current; v47/v48 expected within the day,
   plus a portfolio sweep on the new scoring optimum. The recalibration
   trigger in [deploy.md](../../.claude/docs/deploy.md) should fire once those
   land.

---

## Outputs

- `summary.json` — initial run output (incl. saturation histogram + day classification)
- `summary_v2.json` — corrected n-floor + year-by-year tables (raw_<tier> counts; eff_<tier> bug fixed)
- `per_day.csv` — per-day reconstructed state (1,627 rows)
- `run.log`, `reaggregate.log` — console traces
- `run.py`, `reaggregate.py` — reusable scripts; re-run by re-executing both after any version bump
