export const meta = {
  name: 'score-normalization-investigation',
  description: 'Exhaustively evaluate per-stock score normalization (z / percentile / cross-sectional rank) on the OPTION barrier, point-in-time, then adversarially verify and synthesize a go/no-go',
  phases: [
    { title: 'Evaluate', detail: '5 parallel evaluators: per-stock z, percentile, cross-sectional rank, dilution-vs-alpha decomposition, hybrid-bridge + capacity' },
    { title: 'Verify', detail: '4 adversarial skeptics try to refute the surviving claims (PIT integrity, base-comparator validity, concentration, option-vs-generic)' },
    { title: 'Synthesize', detail: 'merge into one cited verdict with a falsifiable Stage-1/Stage-3 next step' },
  ],
}

const SN = 'C:\\Development\\Trader\\experiments\\score_norm'
const OUT = 'C:\\Development\\Trader\\experiments\\score_norm\\out'

const CONTEXT = `
SHARED CONTEXT — read before working.

HYPOTHESIS (the user's "bell curve" idea): the scoring engine outputs an ABSOLUTE 0-100 score and
thresholds calls at overall>=75 universally. Recon proved this is a per-stock-mean+std lottery:
**32% of stocks (247/774) make ZERO 75+ signals in 5y** (a quiet stock like AAL has score mean ~42,
max-ever 68 — it CANNOT cross 75), while a few "loud" names (ANET/FN/SQM) dominate all 2,438 calls.
The hypothesis: NORMALIZE each stock's score to its own distribution so every stock contributes its
genuine extremes — "bridge" the 0-signal stock and the many-signal stock. The make-or-break question:
are the quiet stocks' RELATIVE-best days actually good trades on the OPTION barrier, or just
less-bad-but-still-below-base (which would DILUTE — the documented v42 volume-dump trap)?

LEDGER (already built, holdout-locked <=2026-05-15, point-in-time — DO NOT rebuild/recalc/sweep;
read this parquet only): ${SN.replace(/\\/g, '\\\\')}\\..\\..\\.cache\\score_norm\\norm_ledger_v70.parquet
Per candidate-signal row: symbol, date, overall, ps_z (per-stock expanding z, STRICTLY-prior),
ps_pctile (per-stock expanding percentile, strictly-prior), xs_pctile (cross-sectional same-day rank),
n_prior (warmup count; >=120 = usable), vol_pct (60d daily vol %), and a barrier-agnostic forward path.

USE THE SHARED HELPER (do not re-derive barriers yourself — this avoids the gen15 trap):
  cd to repo root; python with: sys.path.insert(0,'experiments/score_norm'); from eval_norm import load, BASE75, zprop, wr, cohort_vs_base
  df = load()  # adds opt15/apex15/hold15/gen15 win columns. cohort_vs_base(df, mask, label) prints WR-vs-base+z.

THE GATES (non-negotiable, these are the codebase's hard-won lessons):
 - PRIMARY barrier = opt15 (option-aligned WR15) AND apex15 (funded HOLD). gen15/hold15 are diagnostics;
   a signal that only works on gen15 is the SVD/v42 GENERIC-VS-OPTION TRAP — call it out, never ship it.
 - A NEW signal set (fired by normalization, NOT by absolute>=75) is: ALPHA-ADDING if it beats the 75+
   base (opt15 47.5% / apex15 70.0%) at two-proportion z>=+3 on BOTH; NON-DILUTIVE if it ~matches base
   (z within ~-1.5); DILUTIVE if below (z<=-3 = the trap). Resolved-only (drop null labels).
 - POINT-IN-TIME: the ledger is PIT-built (ps_* use only prior data, warmup 120). Respect n_prior>=120.
 - Don't conclude from a few stocks/years — decompose. Two-proportion z; |z|>=3 for a real effect.
 - This is ONE option strategy on a real universe; per-stock normalization is a Stage-1 (score transform,
   version bump) OR Stage-3 (cascade selection) change — note which your finding implies.

Write your section to ${OUT.replace(/\\/g, '\\\\')}\\<your-name>.md (prefix scratch scripts with your name).
Set PYTHONIOENCODING=utf-8 PYTHONUTF8=1. Be quantitative: tables, thresholds, N, z. Be honest about nulls.
`

phase('Evaluate')

const evals = await parallel([
  () => agent(`${CONTEXT}

YOU (agent: eval-zscore). Evaluate the PER-STOCK Z-SCORE scheme (ps_z).
 1. For ps_z thresholds {1.0, 1.5, 2.0, 2.5}: the NEW signals = (ps_z>=th AND overall<75) — the ones the
    absolute threshold MISSES. Run cohort_vs_base on opt15/apex15/hold15/gen15. Are any non-dilutive or alpha?
 2. The FULL normalized call set under each th (ps_z>=th, any overall) vs the absolute-75 set: WR, N, and how
    many DISTINCT stocks signal (vs the absolute set's distinct-stock count). Quantify the supply equalization.
 3. How many of the 247 currently-mute (0 abs-signal) stocks now contribute, and what is THEIR option WR?
    (filter to stocks whose max overall<75 — they're the pure 'bridge' cases.)
 4. Verdict: is there a ps_z threshold where the bridge adds tradeable signal (non-dilutive+), or is it dilution?
Write ${OUT.replace(/\\/g, '\\\\')}\\eval-zscore.md. Return a 12-line summary with the key thresholds, N, opt15/apex15 z, and verdict.`,
    { label: 'eval-zscore', phase: 'Evaluate' }),

  () => agent(`${CONTEXT}

YOU (agent: eval-percentile). Evaluate the PER-STOCK PERCENTILE scheme (ps_pctile) — same structure as the
z-score eval but for ps_pctile thresholds {0.90, 0.95, 0.98, 0.99}. NEW signals = (ps_pctile>=th AND overall<75).
Run cohort_vs_base on each. Compare percentile vs z-score (does rank-based behave differently from moment-based?
percentile is robust to a stock's score skew; z assumes ~symmetry). Report supply equalization + the mute-stock
(max overall<75) option WR. Verdict: best percentile threshold, non-dilutive or trap?
Write ${OUT.replace(/\\/g, '\\\\')}\\eval-percentile.md. Return a 12-line summary.`,
    { label: 'eval-percentile', phase: 'Evaluate' }),

  () => agent(`${CONTEXT}

YOU (agent: eval-xsection). Evaluate the CROSS-SECTIONAL daily-rank scheme (xs_pctile = rank of overall among
ALL stocks that day). This is "trade the best stocks TODAY" rather than a fixed line.
 1. xs_pctile thresholds {0.90, 0.95, 0.98, 0.99}: NEW signals (xs_pctile>=th AND overall<75) cohort_vs_base.
 2. Does daily cross-sectional ranking beat the absolute 75 line? Compare the xs>=0.95 set vs abs>=75 set on
    opt15/apex15 (WR, N). Note: xs_pctile is mechanically correlated with overall (high score -> high rank), so
    isolate the MARGINAL signals (high xs rank but sub-75 absolute) — are they good?
 3. Is cross-sectional better/worse than per-stock-temporal (z/pctile)? One sentence on why.
Write ${OUT.replace(/\\/g, '\\\\')}\\eval-xsection.md. Return a 12-line summary.`,
    { label: 'eval-xsection', phase: 'Evaluate' }),

  () => agent(`${CONTEXT}

YOU (agent: decomp) — THE CRUX. Decompose WHERE the normalized-new signals land (alpha vs dilution).
Take the new-signal set ps_z>=1.5 AND overall<75 (and cross-check ps_pctile>=0.95 AND overall<75). Split its
opt15/apex15 WR by, and report z vs the 75+ base for each split:
 1. the SIGNAL'S stock absolute-supply: stocks with 0 abs-signals (pure bridge) vs 1-5 vs 6+ — are the
    truly-mute stocks' relative-best days good or bad?
 2. the stock's realized vol bucket (vol_pct quintiles) and the SIGNAL's own overall band (50-59 / 60-69 / 70-74).
 3. n_prior bucket (is short-warmup noisier?) and year (2018/2020/2022/2024 — regime stability).
 4. KEY: a normalized signal at overall=55 is a very different animal than at 72. Does the bridge work only for
    the near-threshold (70-74) sub-75 signals (mild lift) and FAIL for the deep ones (50-65)? Find the overall
    floor below which normalized signals are dilutive. This determines if a gradient lift could work.
Write ${OUT.replace(/\\/g, '\\\\')}\\decomp.md. Return a 12-line summary with the alpha-vs-dilution boundary.`,
    { label: 'decomp', phase: 'Evaluate' }),

  () => agent(`${CONTEXT}

YOU (agent: bridge-capacity). Evaluate the literal "BRIDGE" + portfolio/capacity implications.
 1. The hybrid set = (overall>=75) UNION (best normalized-new under a reasonable scheme, e.g. ps_z>=1.5 & overall<75).
    Aggregate opt15/apex15 WR of the UNION vs absolute-only. Does adding the bridge signals raise or lower the
    blended WR? By how much, and how much extra N?
 2. Capacity/diversification: distinct stocks signalling (absolute vs bridged), and does the bridge fix the
    "32% mute stocks" coverage with GOOD signals or just add bodies? Per-day signal supply before/after.
 3. Stage question: is the win (if any) better as a Stage-1 score transform (re-scale overall per-stock -> new
    tradeable >=75) or a Stage-3 cascade selection (pick top per-stock-z each day, scores unchanged)? Argue which.
 4. The capacity caveat: the cascade fills ~6 slots/day; if the bridge 5-10x's supply but the marginal signals
    are base-rate, it changes WHICH 6 fill, not adds alpha. Quantify whether the bridge signals would even get
    cascade priority (they're lower absolute conviction).
Write ${OUT.replace(/\\/g, '\\\\')}\\bridge-capacity.md. Return a 12-line summary.`,
    { label: 'bridge-capacity', phase: 'Evaluate' }),
]).then(rs => rs.filter(Boolean))

phase('Verify')

const claims = evals.map((r, i) => `--- EVALUATOR ${i + 1} ---\n${r}`).join('\n\n')

const verdicts = await parallel([
  () => agent(`${CONTEXT}

YOU (agent: skeptic-PIT) — ADVERSARIAL. The evaluators (summaries below) may have over-claimed. Your job is to
REFUTE, defaulting to "refuted/not-real" if uncertain. LENS: point-in-time / look-ahead integrity.
Re-derive any headline "non-dilutive or alpha" claim and check: (a) all rows used have n_prior>=120 (no warmup
leakage); (b) ps_z/ps_pctile are strictly-prior (spot-check a stock: today's value must NOT be in its own stat —
re-read build_norm_ledger.py to confirm the insort happens AFTER feature write); (c) xs_pctile is same-day only;
(d) does the claim survive if you require n_prior>=250 (longer warmup)? Report which claims survive a strict PIT bar.
${claims}
Write ${OUT.replace(/\\/g, '\\\\')}\\verify-pit.md. Return a 10-line verdict: which claims are PIT-clean vs suspect.`,
    { label: 'skeptic-PIT', phase: 'Verify' }),

  () => agent(`${CONTEXT}

YOU (agent: skeptic-basecomparator) — ADVERSARIAL. LENS: is the comparator legitimate? The evaluators compare
normalized-new signals to the global 75+ base (opt15 47.5%). REFUTE: maybe that's the wrong bar. (a) A
normalized signal at overall=60 should arguably be compared to the 60-69 absolute base rate, not the 75+ base —
recompute the sub-75 absolute base rates per overall band from the ledger and re-judge: are the normalized-new
signals better than ABSOLUTE signals at the SAME overall level? (i.e., does ps_z add info BEYOND overall?) (b)
Within a fixed overall band (e.g. 70-74), split by ps_z high vs low — does ps_z separate winners at z>=3? If ps_z
adds NOTHING beyond the absolute overall, the whole scheme is just "lower the threshold," already known-null.
${claims}
Write ${OUT.replace(/\\/g, '\\\\')}\\verify-basecomparator.md. Return a 10-line verdict: does ps_z add info beyond overall?`,
    { label: 'skeptic-basecomparator', phase: 'Verify' }),

  () => agent(`${CONTEXT}

YOU (agent: skeptic-concentration) — ADVERSARIAL. LENS: is any positive result driven by a few stocks/years/sectors?
REFUTE: take any surviving "alpha" claim and (a) drop the top-5 contributing symbols — does it survive? (b) is it
concentrated in one regime year (2020/2021 bull)? Re-run by year. (c) survivorship: the "mute stocks that work"
might be survivors (stocks that existed all 10y and trended up). Check if the bridge alpha is just beta to a few
big winners. (d) the option-vs-generic gap: re-confirm every claim holds on opt15 AND apex15, not just hold15/gen15.
${claims}
Write ${OUT.replace(/\\/g, '\\\\')}\\verify-concentration.md. Return a 10-line verdict: robust or concentrated/beta.`,
    { label: 'skeptic-concentration', phase: 'Verify' }),

  () => agent(`${CONTEXT}

YOU (agent: skeptic-mechanism) — ADVERSARIAL. LENS: economic mechanism + the "why would a low-mean stock's
relative-best be a good CALL?" question. REFUTE the premise: a stock has a low score-mean usually BECAUSE the
scorer correctly judges it a poor momentum vehicle; forcing its top-percentile day to trade may just buy the best
of a bad lot. Check: do the mute-stock (max overall<75) bridge signals actually capture UP moves, or do they get
chopped? Compare their mfe/mae shape (derive from t_up/t_dn: median t_up at 0.9sigma vs t_dn at 0.77sigma). Also
test the OPPOSITE framing: maybe normalization is better on the PUT side or only in specific sectors. Give the
honest economic read: is there a real reason the bridge SHOULD work, or is the 32%-mute-stocks fact just correct?
${claims}
Write ${OUT.replace(/\\/g, '\\\\')}\\verify-mechanism.md. Return a 10-line verdict: is there a real mechanism?`,
    { label: 'skeptic-mechanism', phase: 'Verify' }),
]).then(rs => rs.filter(Boolean))

phase('Synthesize')

const allClaims = claims + '\n\n=== ADVERSARIAL VERDICTS ===\n' + verdicts.map((r, i) => `--- SKEPTIC ${i + 1} ---\n${r}`).join('\n\n')

const final = await agent(`${CONTEXT}

YOU (agent: synthesis). The 5 evaluators and 4 adversarial skeptics each wrote a section in ${OUT.replace(/\\/g, '\\\\')}\\
(eval-zscore.md, eval-percentile.md, eval-xsection.md, decomp.md, bridge-capacity.md, verify-pit.md,
verify-basecomparator.md, verify-concentration.md, verify-mechanism.md). READ ALL NINE FILES IN FULL. Their
return-summaries are below for orientation:
${allClaims}

Write ONE cohesive report to ${OUT.replace(/\\/g, '\\\\')}\\FINAL_REPORT.md answering the user's question:
"would bell-curving / per-stock-normalizing the scores yield a better result by giving every stock a meaningful
amount of signals?" Structure:
 1. VERDICT up top: does per-stock normalization beat the absolute threshold on the OPTION barrier, point-in-time?
    (ALPHA / NON-DILUTIVE-but-flat / DILUTIVE-NULL). State it in one sentence with the headline numbers.
 2. The supply story (the 32% mute-stocks fact is real — how much does normalization equalize it, and are the new
    signals GOOD).
 3. The crux: do the quiet stocks' relative-best days clear the option barrier, or dilute? (the decomp boundary).
 4. Does ps_z/ps_pctile add info BEYOND the absolute overall at the same level (skeptic-basecomparator), or is it
    just "lower the threshold" in disguise? This is the single most important question — answer it explicitly.
 5. Which scheme is best (z / percentile / cross-sectional / hybrid) and at what threshold, IF any survived.
 6. Stage-1 vs Stage-3 framing for any survivor, and a FALSIFIABLE next step expressed in the gate language.
 7. Honest "what NOT to conclude" box (PIT caveats, the option-vs-generic trap, concentration, single-strategy).
Resolve disagreements between evaluators and skeptics explicitly — if a skeptic refuted a claim, say so. Be
concrete with numbers/dates/thresholds. Return the FINAL_REPORT.md content in full as your response.`,
  { label: 'synthesis', phase: 'Synthesize' })

return { report: final, out: OUT }
