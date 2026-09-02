# Lifecycle MC — Screen 1 (N=100) Results (FABLE, 2026-07-14)

**Tier: SCREEN, not GATE** (per `DESIGN.md` section 8/10 — N=100/300 are screens; only an N=500 read
with 2020-COVID represented licenses the section 8 BARS / any Phase-3 decision). Queue task 623,
N=100 (10,000 pooled paths per arm = 20 pooled starts x 500 replications), $50,000 starting cash,
v74 pinned. Full per-arm output: `results/screen_n100.json`. Pre-registration: `DESIGN.md`.

| arm | median terminal | return | p10 | worst DD | P(collapse) |
|---|---:|---:|---:|---:|---:|
| `core_only` | $207.7k | +315% | $106.8k | 71.9% | 0.0% |
| `sprint_rotate_core` | $228.2k | +356% | $33.0k (3.2x worse than core's p10) | 91.6% | 0.0% |
| `ladder_sprint_core` | $257.5k | — | — | 98.5% | **1.95% (BREACH)** |

`sprint_rotate_core` = staged 30-DTE n4 leg, stop-at-2x, 730-day ride-to-window-end fallback;
median 228 calendar days spent in the sprint state before rotation/fallback.

## Verdict

The rotate policy does **NOT** Pareto-dominate Core-only (pre-registered Phase-3 bar: Pareto on
terminal-wealth-at-bounded-DD, collapse=0) — the sprint's losing starts crater the tail before
rotation can help; the ladder arm is killed on collapse.

- **Phase-3 auto-rotate wiring is NOT licensed** by this result.
- **N=300 escalation is NOT exercised** — the p10/DD gap between `sprint_rotate_core` and
  `core_only` is structural, not seed noise.
- A follow-up arm (n10 sprint leg) is coherent **only if** the user selects Option B in P0.3 —
  pre-registered as a conditional in `DESIGN.md`, unsubmitted.
- **Core-only remains the small-capital answer on current evidence.**
