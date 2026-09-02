# Cached barrier path vs fresh forward walk — reconciliation

**Date:** 2026-08-10 · **Status:** DIAGNOSED, not fixed (both candidate fixes need an owner decision)
**Follow-up to:** `PREREG.md` §6 (put-side "parity" revision)
**Scope:** measurement layer only. No live-trading, scoring, or portfolio code is involved.

## Question

`PREREG.md` §6 observed that legacy `tp` (cache-served) and `tp26` (fresh-walked) disagree on
put-side WR15 by 1.2–2.4pp at a matched 5y window, *despite byte-identical put barriers*, and
attributed the residual to "the cached-path vs fresh-walk-path implementations disagreeing
slightly." That named the layer but not the mechanism. This closes it.

## Method

A harness feeds the **same price arrays** to both implementations —
`database/barrier_cache.py::_walk_outcome` (what the cache writer stores) and
`assess_scores.py::_swing_walk` (the fallback) — at the `30dte_opt` barriers both paths agree
on nominally, then attributes every disagreement to a cause. Sigma is controlled by re-running
the cache walk with the fresh path's sigma, which separates "different inputs" from
"different logic". Two disjoint symbol samples, 5y, W=15d.

Harness: `walk_diff.py` / `walk_diff2.py` / `walk_diff3.py` (scratchpad — read-only, no DB writes).

## Result — the gap reproduces exactly, and has ONE cause

| sample | side | cache WR15 | fresh WR15 | delta |
|---|---|---:|---:|---:|
| A (12 syms, N≈9.7k/side) | put | 40.37% | 38.50% | **+1.87pp** |
| A | call (`tp` barriers) | 49.68% | 47.76% | **+1.92pp** |
| B (17 syms strided, N≈14.7k/side) | put | 39.09% | 36.81% | **+2.28pp** |

Inside the observed 1.2–2.4pp band, **cache always higher**, on two disjoint samples.

**Attribution: 369 of 369 win-rate mismatches are same-bar double touches. Zero residual.**
Every one survives the sigma control (sigma agrees to <1e-9 wherever both paths define it), so
this is pure logic, not input drift.

### D1 — same-bar double-touch tie-break *(the entire win-rate gap)*

When one daily bar's range spans **both** barriers:

- `_walk_outcome` (`barrier_cache.py:229-250`) tests the target first and returns `win`
  unconditionally. The stop test on that bar is unreachable.
- `_swing_walk` (`assess_scores.py:670-672`) detects the double touch and breaks the tie on
  **close direction** — `is_win = (c < entry) if side == 'high' else (c > entry)`.

The cache is therefore **systematically optimistic**: it resolves every ambiguous bar in the
signal's favour. ≥1.9% of signal-sides flip on this alone.

This is not a `tp26` artifact — it biases **every cached assessment number ever produced**
(`wr` and `tp`, all DTEs, all versions). Magnitude is barrier-dependent: it scales with how
often one bar can span both barriers, so it is largest where the stop sits close to the target
(the put anchors, m=0.728σ) and smallest where the stop is far (tp26's calls, m=3.64σ → +0.55pp).

### D2 — window-reachability precondition *(the N gap — NOT "live-data drift")*

`_swing_walk` gates each period through `_period_reachable()` **before** walking
(`assess_scores.py:538-556`): if forward data doesn't extend past the calendar cutoff, the
period is dropped — *even when a barrier already fired on day 1 and the outcome is
unambiguous*. `_walk_outcome` only demands the full window on the **expire** branch
(`barrier_cache.py:257`), so it keeps decided outcomes near the data edge.

Measured: ~154 extra peaks dropped by the fresh path per 12 symbols, all at the trailing edge,
`reachable=False` with 1–10 forward bars, roughly half of them already resolved (`cache_result=1`).

**This corrects PREREG §6.** The ~0.1% `total_peaks` gap (63,944 vs 63,880) was attributed there
to "ordinary live-data drift between two runs 11 minutes apart." It is deterministic — D2 plus
D3 — and it is structurally concentrated at the **most recent** end of every window.

### D3 — sigma edge window *(small N effect)*

`barrier_cache._realized_vol` accepts as few as 20 returns from a `[i-70 : i+1]` window and its
loop starts at `i=30`, so signals at `base_idx` 30–59 get a sigma off a **short** lookback.
`assess_scores._realized_vol_pct` hard-returns `None` below `base_idx < 60`. 60 presence
mismatches per 12 symbols. Interior values are identical to float noise — sigma is *not* a
source of divergence anywhere both paths define it.

### D4 — expire mark-to-market off by one bar *(returns, not win rate)*

On expire the cache marks out at `last_close`, the last bar **inside** the window
(`barrier_cache.py:262-269`); the fresh walk marks out at `c`, the **first bar past** the cutoff
(`assess_scores.py:641-650`), and its `exit_bars` is likewise one higher. The fresh path is
pricing a hard sell one bar beyond the window it claims to measure — this one looks like a
straight bug, and the cache has it right.

Rare at the `tp` anchors (tight stop → few expires) but **material at tp26's call barriers**,
where the 3.64σ stop makes expire the dominant loss mode: 135/9.7k call signals, and it moves
`avg_return_15d` by ~0.05pp.

### D5 — vestigial stop/expire heuristic *(labels only; free fix)*

`peaks_to_swing_results` can't read stop-vs-expire off the row, so it **reconstructs** it —
"exit_close within 0.5% of the stop barrier ⇒ stop" (`barrier_cache.py:697`, mirrored at `:974`
in the DuckDB path). An expire whose last close happens to land near the stop is mislabeled.
But `fire_type` (0=expire/1=tp/2=sl) **is stored** and is 99.5% populated (139k NULL of 28.8M).
The heuristic is dead weight — the exact answer is already in the row. Win rate is unaffected
(both are non-win); stop/expire *splits* are.

## Which path is right?

**On D4 and D5 the cache is right** and the fixes are safe and local.

**On D1 neither is ground truth.** Daily OHLC cannot tell you which barrier the intraday path
touched first. The cache assumes "always the favourable one" (optimistic); the fresh walk uses
the close as a proxy (at least conditioned on something). What makes this worth flagging: it is
an **empirical** question, and this box now holds the data to settle it — Polygon minute aggs
back to 2022-08 (`B:\polygon_flatfiles\`, `us_options_opra/minute_aggs_v1`; underlying minute
bars would be the cleaner instrument). The FF-4 work already validated daily-bar conventions
against real intraday fills; the same method applies here. Adjudicating the tie-break from data
beats picking a convention.

## Recommendation — three separable decisions, none taken

1. **D4 + D5 (safe, local).** Align the fresh walk's expire mark-out to the last in-window bar,
   and read `fire_type` instead of the 0.5% heuristic. Small blast radius, both strictly
   corrections. *Caveat:* D4 shifts `tp26`'s call-side `avg_return_15d` slightly, so the numbers
   in PREREG §6 would need a re-read.

2. **D1 (the real decision — do NOT do this quietly).** Making the cache tie-break on close
   would move **every historical cached WR figure down ~2pp on the put anchors**. That is exactly
   the hazard the tp26 PREREG was built to avoid: the ~40 closed-axis verdicts in
   `known-issues.md` and memory cite `tp`/`wr` at their current values. If this changes, it is a
   deliberate, announced re-baselining — with a rebuild of all 4 barrier sets — not a bugfix.
   Recommended sequence: adjudicate against minute data first, then decide.

3. **Adding a 5th `BARRIER_SETS` entry for tp26 — recommend NOT yet.** The mechanics are easy:
   the nightly `refresh_recent(days=160)` at `trader.py:1750` passes `barrier_sets=None`, so a
   new key is picked up automatically; only a one-time historical backfill
   (`python -m database.barrier_cache backfill-periods 3650 '' 30dte_opt2026`) would be manual.
   Three reasons to hold:
   - **It would move tp26's published numbers, not just speed them up** — +1.9pp put / +0.55pp
     call (measured above, by injecting the set in memory). The put-side "parity" with legacy
     `tp` that PREREG §2 wanted would finally appear — but only because *both* sides would then
     share D1's optimistic bias. That is parity by adopting a known bias, which is worse than
     the honest mismatch we have now.
   - **Cost is real:** each set is ~28.8M rows / ~7 GB SQLite / ~1.6 GB DuckDB (current: 4 sets,
     115M rows, 29.4 GB / 6.5 GB), plus ~25% on every nightly refresh — to accelerate a metric
     that feeds no ship gate and is recomputed rarely.
   - **Ordering:** if D1 is ever resolved, the whole cache gets rebuilt anyway. Adding a fifth
     set first means paying the backfill twice.

   The cheap interim if tp26 recomputes become frequent: `assess_scores.py:984` already prints a
   "not in BARRIER_SETS, using forward walk" warning — that is working as designed, and 6m31s
   for a full v74 pass (task #414) is not a bottleneck worth 7 GB.

**Also worth knowing:** the cache only spans **2016-04-26 → present**. Any assess window deeper
than ~10y already forward-walks its early portion, so deep-window runs are silently *mixing*
both conventions — the D1 bias is not uniform across a 30y window.
