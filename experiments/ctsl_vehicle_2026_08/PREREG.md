# PREREG — The CTSL vehicle study

STATUS: LOCKED 2026-08-12 before any outcome exists (this git commit = the lock).
Owner: "go" to execute end-to-end. Successor to `frontier_2026_08` (CTSL re-attribution).

Object: CTSL (Counter-Trend Score Lift, shipped 2026-05-08) as a fundable VEHICLE,
following `frontier_2026_08`'s attribution (CTSL = +57/+61pp; every no-CTSL book
negative under honest fills; ultra-only ≈ 90% CTSL by trade rows). Framing correction
from the full 12-window grid: the vehicle is an ANTI-CORRELATED CHOP HARVESTER —
positive in SPY's bad years (2018 +23 vs −5; 2022 +27 vs −19), hurt in waterfall
crashes (2020: −39, DD 56), idle in melt-ups (2024: +1, 15 trades) — so the decision
frame is a SLEEVE over an index base, judged on the COMPOSITE, not standalone-vs-SPY.

## Stages

**V0 — mechanism audit (recon, no compute).** CTSL's code path, knobs, caps
(CT_PROMOTE ultra-slot semantics, promotion criteria, what was calibrated when, on what
data). Deliverable: the mechanism dossier + how to express "promotions-only" funding
(no raw-95+ picks) if possible via config; else ultra-only stands as the practical
proxy (90% purity measured).

**V1 — vehicle definition battery.** Expressions: {ultra-only anchor, promotions-only
if V0 finds clean expression} × 12 windows × N=500, lenses: calibrated (default),
MISS_P-0.20 buffer, survivor, canon (LABELED reference only). Plus SLEEVE COMPOSITES
computed arithmetically per window: {100% SPY} vs {70% SPY + 30% vehicle} vs
{85/15} — metric: composite return, composite worst-DD proxy (report both components;
no correlation modeling beyond same-window arithmetic — stated limitation).
THIN-window rule: median trades/path < 30 → THIN flag; < 10 → ANECDOTE (2024/dip
expected THIN; decision windows are 22-now/5y as always).

**V2 — vehicle-specific fill honesty (the owner's "close the 15%" — measurement form).**
Composition-weighted MISS_P: reweight the fidelity study's tier-monotone never-fill
rates (t1 20.4% → t4 7.8%) by the VEHICLE's measured liquidity mix (54% high-tier) →
expected effective rate ~0.09-0.12 (vs universe-average 0.15). Rerun V1 winners at the
derived rate as the vehicle's HONEST lens. No market-data collection; pure reweighting
of an existing measurement. (Tactics on the residual — k-day limit-then-exit fallback
measured on real prints — remains the separately-scoped execution study; P2.B real
orders remain the final arbiter. The ~10% model-divergence floor is NOT closable.)

**V3 — levers with survivor checks.** gross {0.30 anchor, 0.45, 0.60} × DTE {30 anchor,
45} on the V1/V2 winner, decision windows, N=500. Lane: median +5.0pp both windows, DD
not worse >2.0pp, collapse 0, survivor-robust — labels stick only after survivor.
(frontier F3's raw-REAL labels fold in here.) Sizing beyond 0.60 banned.

**V4 — capacity at owner scale.** Integer-contract + clip-vs-volume modeling of the
vehicle's ACTUAL historical names (ledger join): at $25k/$50k/$100k book — realized
deployment, per-name clip feasibility (25%-of-day-volume cap), effective trades/yr.
Deliverable: the G3(b) small-account read for THIS vehicle.

**V5 — era-honesty cube (owner Q2, rate-form).** On the V1 winner: signal-drop
{15/30/50%} × MISS_P {vehicle-rate/0.25/0.40} × PIT-mcap existence floor, dot-com +
2008 windows via the deep-backfill rig (custom windows; scores exist to 1995).
Reading rule: an era conclusion counts only if invariant across the cube.

**V6 — December pre-commitment (the anti-rationalization stage).** BEFORE any OOS data
is seen: lock the exact statistics the 2026-12-15 evaluation must show for CTSL
specifically — CT-promoted trades' WR/EV on Jun→Dec-2026 virgin window vs their
in-sample band (CIs stated), plus the live ledger's CTSL-trade subset read. Committed
thresholds; pass/fail phrasing written now, graded then.

## Rules

- No ship of anything from this campaign; any ship is a separate process with its own
  evidence. No capital implication; plan posture (g=0, debt-first) untouched.
- Stop rule: stages as enumerated; amendments pre-outcome or tightening-only.
- Every battery: fingerprint + close-boundary guards, paired seeds, subprocess-per-cell,
  self-logged env. SPY/HISA columns on every table.
- Compute: V1 ~60-100 cells, V2 ~12-24, V3 ~20 + survivor, V4-V5 file/DB analysis +
  ~40 cells. ≲ 3h queue total, off-market preferred.

## AMENDMENT-1 (2026-08-12, written after V0 recon, BEFORE any V1+ cell has run)

V0 is a no-compute source audit; no outcome from this campaign's batteries exists yet.
All four items tighten or correct the object under study. Evidence: `V0_DOSSIER.md`.

1. **Carrier renamed to its true mechanism.** The +57/+61pp `frontier` attributed to
   "CTSL" is **`CT_PROMOTE`** (cascade-stage counter-trend promotion into the funded
   ultra slot); the score-stage `CTSL_ENABLED` lift was ON in BOTH of frontier's arms.
   Proof is structural, not statistical: `ct_tag` fires on `{loaded calls, trend<=20}`,
   `load_signals` already floors at 70, and the CTSL lift is monotone non-decreasing
   with a 74.7 floor — so the promoted SET is invariant to CTSL, and CTSL's eligible
   set (trend<=15) is a strict subset of it. Campaign keeps its filename; every table
   and verdict names **CT_PROMOTE** as the carrier.
2. **V1 gains ONE diagnostic arm (12 cells): `CTSL_ENABLED=0` on the ultra-only anchor**,
   12 windows, N=500, calibrated. Rationale: the campaign is named for a mechanism whose
   marginal contribution has never been separated from CT_PROMOTE's; V0 predicts it is
   near-inert on SELECTION and can act only through intra-ultra queue order and the 75-79
   spread-tilt band. Reading rule (LOCKED now): CTSL is **material** only if the ultra-only
   median moves by >= 5.0pp on BOTH decision windows; otherwise it is recorded INERT
   within this vehicle and the vehicle is CT_PROMOTE-only for V2-V6 purposes.
3. **V1's "promotions-only" expression is DROPPED, per the PREREG's own fallback.** V0
   enumerated every config route (ultra-only; `CT_CALL_TIER`-redirect; ctx removal) and
   found none clean — the ultra tier is unavoidably co-reachable by raw 95+ picks
   (10.3%/10.7% of rows). Ultra-only stands as the anchor at ~90% measured purity. No
   substitute arm is invented.
4. **New mandatory reporting columns, all batteries** (reporting-only, changes no
   mechanism): `n_routed15_rows` / `routed15_row_share` / `routed15_pnl_share`. V0 found
   that `DTE_ROUTER` sends up to 1 signal/day onto a 15-DTE outcome and zeroes its alloc
   score to size it down, but the `ct=='ct_call'` tier override at `monte_carlo.py:3453`
   precedes that cap — so ~21%/18% of the vehicle's rows are 15-DTE trades funded at the
   full ultra rate. The vehicle is therefore ~80/20 30-DTE/15-DTE. This is disclosed and
   carried, NOT fixed (fixing it would change the object `frontier` measured, and no ship
   is in scope). It further means V2's MISS_P reweighting and V3's DTE axis both apply to
   the 30-DTE ~80% only — a stated limitation, not a silent one.
