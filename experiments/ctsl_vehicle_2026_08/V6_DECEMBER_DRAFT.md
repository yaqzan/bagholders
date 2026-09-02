# V6 — December 2026 pre-commitment (DRAFT — for owner ratification, NOT self-approved)

**Status: UNRATIFIED.** Written 2026-08-12, before any post-cutoff data was read. Nothing
here is binding until the owner signs it off. Evidence: `out/ctsl_v6_band.csv`,
driver `driver/v6_precommit.py`.

**Holdout discipline actually enforced, not just claimed.** `CALIBRATION_CUTOFF_DATE` is
`2026-06-15`; today is 2026-08-12, so ~2 months of virgin data already sit in the DB. Every
query in `v6_precommit.py` is hard-filtered to `date <= cutoff` and *asserts* the max date
it actually saw. No post-cutoff row has been read by this campaign.

---

## 1. What the December re-grade is FOR

`frontier_2026_08` closed by saying December "has a sharper job: re-grade CTSL
specifically, not just score bands." This campaign found the object to grade is
**CT_PROMOTE** (see `V0_DOSSIER.md`), and that its portfolio value flows mostly through an
undesigned interaction with `DTE_ROUTER` (see FINDINGS "V1 AMENDMENT-1 diagnostic"). So the
December question is:

> Do counter-trend-tagged calls, on genuinely virgin data, behave the way the in-sample
> band says they do — and specifically, does their **short-dated** edge survive?

## 2. The in-sample band, locked now

Population = `ct_tag`'s call branch verbatim: active version (v74), `overall >= 70`,
`trend <= CT_CALL_TREND_MAX (20)`, 2022-01-01 → 2026-06-15. Contrast = `overall >= 70`,
`trend > 20`. Outcome = `barrier_outcomes`, `side='high'`, horizon pinned by `w_days`.

| barrier set | horizon | population | N | WR | 95% CI (Wilson) | mean exit return |
|---|---|---|---|---|---|---|
| `30dte_opt` | 30d | **CT-promoted** | **92** | **32.61%** | **[23.89, 42.72]** | −0.390% |
| `30dte_opt` | 30d | contrast non-CT | 16,263 | 35.85% | [35.11, 36.59] | −0.028% |
| `15dte_opt` | 15d | **CT-promoted** | **92** | **44.57%** | **[34.83, 54.74]** | +0.137% |
| `15dte_opt` | 15d | contrast non-CT | 16,263 | 39.92% | [39.17, 40.67] | +0.139% |

`30dte_apex` @ 30d is within 0.01pp of `30dte_opt` on both populations — reported, not
separately gated.

**Read this honestly: at the 30-day horizon CT-promoted signals are ~3.2pp WORSE than
ordinary 75+ calls; at the 15-day horizon they are ~4.7pp BETTER.** Both gaps sit inside
overlapping confidence intervals, so neither is established. But the *direction* is a
direct corroboration of the router finding — the counter-trend signal's edge is
short-dated, which is precisely why routing those names onto 15-DTE contracts is where the
portfolio return came from.

## 3. THE BLOCKING PROBLEM — the predictand is too rare to grade in December

Only **133** CT-tagged signals exist in 4.4 years of the whole universe (92 with barrier
outcomes, 69.2% coverage). That is ~30/year.

A Jun→Dec-2026 window is ~6 months, so expect **~15 CT signals, ~10 with outcomes**. The
Wilson interval on 10 trials is roughly **±30pp** — three times wider than the entire
in-sample CT-vs-contrast gap being tested.

**Therefore: the per-signal December re-grade that the PREREG asked for cannot discriminate
anything, and pretending otherwise would manufacture a false verdict in either direction.**
This is exactly the kind of thing worth discovering *before* the data is unsealed rather
than after, which is what this stage was for.

## 4. Proposed pass/fail statements — for ratification

Phrased now, graded 2026-12-15. Each is stated so that it can be *failed*.

### G-1 (PRIMARY, directional, honestly weak)
> On 2026-06-16 → 2026-12-15, CT-promoted calls' `15dte_opt` @ w_days=15 win rate is
> **≥ the contrast population's win rate over the same window**.

Pass = the short-dated CT edge is directionally reproduced. Fail = it inverts.
**Explicitly NOT a significance test** — expected N ≈ 10 makes one impossible. This is a
sign check and must be reported as a sign check.

### G-2 (PRIMARY, falsifier)
> CT-promoted calls' `15dte_opt` win rate does **not** fall below **34.83%** — the lower
> bound of the in-sample 95% CI.

Pass = consistent with the in-sample band. Fail = the mechanism has degraded beyond what
in-sample sampling error can explain. This is the one statement with real teeth, because
it can fail on a small N.

### G-3 (SECONDARY, the horizon claim)
> The CT-promoted WR **gap between the 15-day and 30-day horizons stays positive**
> (15d WR > 30d WR), as it is in-sample (44.57% vs 32.61%).

Pass = the short-dated character of the edge is real. Fail = the horizon story was noise,
and the router interaction has no principled basis.

### G-4 (SECONDARY, live ledger)
> The live Portfolio ledger's CT-promoted trade subset over the same window shows a
> realized win rate within the in-sample CI, **and** the routed-15 share of its funded
> trades is within **±10pp of the 18-21%** measured here.

The second half matters more than the first: it checks whether the *interaction* this
campaign identified is actually running in production the way the simulation says.

### G-5 (POWER GATE — proposed as the gate that governs the others)
> If the virgin window yields **fewer than 25 CT-promoted signals with barrier outcomes**,
> G-1 through G-4 are reported as **UNDERPOWERED / NO VERDICT**, and the re-grade is
> deferred to the next holdout window rather than being resolved either way.

On the in-sample rate (~30 CT signals/year, ~69% with outcomes) the expected December
count is ~10, so **G-5 is expected to fire.** Committing to that now is the point: it
prevents a 10-signal result being read as vindication or as death.

## 5. What the owner is being asked to decide

1. **Ratify or amend G-1..G-5**, in particular the G-5 power gate and the G-2 floor.
2. Decide whether the December read should be **deferred** to a longer window given the
   known sparsity, rather than run on ~10 signals.
3. Decide whether a *portfolio-level* December read (does the live ledger's routed-15
   sleeve behave?) should replace the per-signal read as PRIMARY, since the interaction —
   not the signal — is what this campaign found to be carrying the return.

No ship, no capital implication, and no plan change follows from this document.
