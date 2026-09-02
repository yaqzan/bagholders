# Phase 6 — Daily Put Cap: NULL RESULT

## Verdict: NO SHIP — daily put cap does not reliably reduce portfolio DD

---

## Results Table (N=300 × 8 windows, WorstDD%)

| Variant | cap | 2021 | 2022 | 2023 | 2024 | 2025 | dip | 22-now | 5y | MaxDD | vs_base |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P0_baseline | off | 71.7 | 72.9 | 60.5 | 48.3 | 71.2 | 67.6 | 72.8 | 72.6 | 72.9% | 0pp |
| P1_cap10 | 10 | 70.3 | 72.9 | 63.8+ | 59.6+ | 70.7 | 67.2 | **77.8+** | 73.8+ | 77.8% | +4.9pp |
| P2_cap15 | 15 | 69.8 | **70.1** | 66.1+ | 53.5 | 69.3 | 66.2 | 72.6 | 72.1 | 72.6% | **-0.3pp** |
| P3_cap20 | 20 | **77.8+** | 70.3 | 56.1 | 51.1 | 75.3+ | 67.5 | 74.1+ | 73.3 | 77.8% | +4.9pp |
| P4_cap25 | 25 | 67.9 | 68.7 | 65.0+ | 51.2 | 74.9+ | 67.3 | 75.4+ | 73.3 | 75.4% | +2.5pp |
| P5_cap30 | 30 | 68.3 | 72.2 | 66.2+ | 54.4 | 69.9 | 66.9 | 71.5 | **75.9+** | 75.9% | +3.0pp |

`+` = worse by >0.5pp, bolded = notable.

---

## Why the Mechanism Lacks Leverage

### 1. MaxPos=14 is the binding constraint
On a day with 124 puts and 15 calls (like 2022-08-30):
- Calls process first (`calls_first` production default)
- Puts get residual slots (~9-14 depending on open positions)
- Without a cap, the 9-14 DEEPEST puts get filled anyway (sorted by conviction)
- Cap=20: 20 puts compete for 9-14 slots → same 9-14 deepest selected
- Cap=10: only 10 puts compete for 9-14 slots → might leave 4-slot idle if <10 < available_slots

**Only cap < available_put_slots matters.** Since available put slots is typically 5-12 (calls always go first), cap ≥ 15 almost never changes which puts get filled.

### 2. Removed signals leave idle slots, not call replacements
When cap=10 drops 55% of 2022 puts, those freed slots DON'T get replaced by calls. The cascade just has fewer puts AND fewer total positions. Idle capital → reduced compounding, not reduced DD.

### 3. Put signal count per window

| Window | Total puts | P1 dropped | P2 dropped | P3 dropped |
|---|---:|---:|---:|---:|
| 2021 | 782 | 19 (2.4%) | 2 (0.3%) | 0 (0.0%) |
| 2022 | 4,926 | 2,728 (55%) | 2,056 (42%) | 1,617 (33%) |
| 2023 | 3,902 | 1,737 (45%) | 1,074 (28%) | 641 (16%) |
| dip | 1,160 | 204 (18%) | 56 (5%) | 9 (0.8%) |

Cap=20/25/30 shows 2021 and dip as near-zero drop, yet those windows show +6pp/-4pp swings vs baseline — **pure N=300 seed noise**.

### 4. N=300 noise overwhelms 2-3pp real improvements
The Phase v32 noise finding: baseline-to-baseline rng variance at N=300 is ±5-8pp per window on DD. Any mechanism producing 1-3pp DD improvement cannot be distinguished from noise at N=300 across 8 windows.

P2 (cap=15) was the only potentially positive signal:
- 2022: 70.1% vs 72.9% baseline → -2.8pp (within noise)
- 2025: 69.3% vs 71.2% baseline → -1.9pp (within noise)
- 2023: 66.1% vs 60.5% baseline → **+5.6pp WORSE** (contradicts expected mechanism)

The 2023 regression for P2 likely reflects the same seed noise artifact: cap=15 drops 28% of 2023 puts, and the resulting portfolio compound with different seeds runs worse on 2023.

---

## Structural Diagnosis: Why Portfolio-Stage Daily Cap Can't Fix Score-Stage DD

Phase 1B finding: top 20 high-density put days are ALL in 2022/Feb 2023 bear tape.

But the production cascade already filters these days via two mechanisms:
1. **MaxPos=14**: already limits concurrent puts to ~9 per day when calls are also firing
2. **F3f breadth cut for calls**: call allocations scale down in stressed tape, freeing more put slots and slightly cutting correlated call exposure

The daily cap overlaps with MaxPos mechanics rather than complementing them.

---

## What Has Real Structural Leverage (Phase 7)

The F3f breadth knob for puts currently **only cuts in BULL tape** (brd ≥ 75 → scale 0.50 at brd=95). In stressed tape (brd ≤ 25 — exactly the extreme-correlated-DD days), puts scale at full 1.0.

**Stress-side F3f put cut** (Phase 7): Scale put allocation DOWN when breadth is very weak, mirroring the existing call F3f but for the put stress direction.

Shape example (THRESH=30, FLOOR=0.70, LOW=10):
- brd ≥ 30: put scale = 1.0 (unchanged, covers 97.9% of days)
- brd = 20: put scale = 0.85 (15% reduction)
- brd = 10: put scale = 0.70 (30% reduction, floor)
- brd ≤ 10: put scale = 0.70 (floor held)

This fires on the exact same 2.1% of extreme days (≥50 concurrent puts) that Phase 1B identified as the DD-spike drivers, but:
1. Reduces per-TRADE allocation (not count) → no idle slots
2. Complements F3f call mechanism (same breadth signal, both sides)
3. Only active in the stress zone (brd ≤ 30), neutral on all other days

This is implemented in monte_carlo.py via `F3F_PUT_STRESS_THRESH`, `F3F_PUT_STRESS_FLOOR`, `F3F_PUT_STRESS_LOW` env vars (0 = disabled).
