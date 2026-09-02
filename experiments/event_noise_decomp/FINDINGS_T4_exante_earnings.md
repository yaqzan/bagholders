# T4 — Ex-ante earnings event-risk as a Stage-3 sizing lever: **NULL** (closed at pre-screen)

**Date:** 2026-07-25 · Substrate: fresh v74 `dd_ledger` tapes (queue task #76, N=300, 3m42s),
shipped default profile. Read-only, no MC sweep spent.
Harness: `tape_prescreen.py` -> `tape_earn_join.py` -> `tape_earn_exante.py`.

## Verdict

The ex-ante earnings-spanning cohort is **not** a DD sizing lever. It fails the G21 coincidence
requirement (low-EV AND high dd-concentration) on both legs, and its sign is *positive*, not
negative. Pre-registered bar §3d not met at the first screen; **B/C/D sweep not licensed.**

## The result

5y pooled, funded calls, N=853,106 (26,308 / 119,586 in the two cohort definitions):

| flag definition | cohort share | cohort EV | base EV | **gap** | WR (cohort/base) | dd_conc |
|---|---|---|---|---|---|---|
| ex-**post** — event in (entry, **realized exit**] | 3.2% | −0.3419 | +0.0375 | **−0.3794** | 0.267 / 0.707 | 2.40 |
| ex-**ante** — event in (entry, entry+20cd] | 14.5% | +0.0355 | +0.0237 | **+0.0118** | 0.669 / 0.697 | 1.19 |

Per-year ex-ante gap (G26 kill-test): 2021 −0.0417 · 2022 +0.0103 · 2023 +0.0025 · 2024 +0.1110 ·
2025 +0.0335 — **4 of 5 positive**. There is no negative-EV earnings cohort to contract.

## Why the ex-post number is a mirage — the trap, stated plainly

`exit_date` is an **outcome**. TP winners exit in ~4 days; dead bags run to the ~15-day expiry.
So "an earnings date fell inside the realized holding window" is mechanically more likely for
long-held positions, and long-held positions are mechanically the losers:

| realized hold | base EV |
|---|---|
| 0-3 d | +0.2412 |
| 3-6 d | +0.1010 |
| 6-10 d | −0.0824 |
| 10-14 d | −0.2453 |
| 14+ d | −0.4892 |

Conditioning on a realized-exit window is therefore **conditioning on the outcome**. The composition
check confirms the mechanism directly: `ern_real=True` has mean hold 6.60d vs 5.56d for the
complement, while the honest `ern_nom=True` cohort actually holds **shorter** (4.44d vs 5.79d) —
an earnings event tends to *resolve* a position early, which is precisely why the ex-post filter
selects against it.

Switching to the ex-ante definition moves the gap from −0.3794 to +0.0118 — a 97% collapse and a
sign flip. Nothing survives.

## Consistency with the existing evidence base

This agrees with, and does not re-litigate, three closed results:
- `peak_fakeout` F9 (2026-07-15): call-side earnings proximity null at the score gate (best z 1.84).
- `earnboost_honest` (2026-06-01): the earnings premium is real but lives in pre7, is thin, and
  `EARN_BOOST` was retired as neutral-EV in the v74 lean.
- `EARN_SUPP_PUT` retired 2026-05-06.

The mildly-positive ex-ante gap found here is the same thin, non-actionable premium those three
describe, now measured at the portfolio/DD-dollar level rather than per-trade.

## Generalizable lesson (promote to the trap registry)

**Any cohort flag defined over a REALIZED holding window is outcome-conditioned and will
manufacture a large fake effect whose sign tracks hold duration.** The tell is a huge gap (here
−0.38, ~10x anything real in this system) that collapses when the window is redefined from the
realized exit to the nominal/scheduled hold. Always define event-window flags from the ENTRY date
plus the NOMINAL hold, and report the hold-duration composition of both cohorts as the standing
proof the confound was addressed. A "this cohort loses 34%" claim in a system whose base EV is
+3.75% should trigger this check before anything else.
