export const meta = {
  name: 'volume-rework-candidacy-v3',
  description: 'Reason over the pre-computed v70 volume-edge surface: three lens-agents propose candidates, one skeptic adversarially refutes+ranks, one writes the ship-candidacy report. Text-only (no schema) for reliability.',
  phases: [
    { title: 'Propose', detail: '3 lens-agents propose candidates (simplify / stability / alpha)' },
    { title: 'Refute', detail: 'one skeptic adversarially refutes + ranks all candidates' },
    { title: 'Report', detail: 'synthesize the ship-candidacy report' },
  ],
}

const DIGEST = [
  'PRE-COMPUTED v70 VOLUME-EDGE SURFACE (10y holdout-locked ledger, CALL signals).',
  'Barriers: opt15 = option/funded PRIMARY (what Apex trades); gen15 = generic dashboard WR15. z = cohort vs comparison (+ = cohort BETTER). Option/funded z is the DECISION barrier; generic alone is insufficient (SVD/VSG FLAG trap).',
  '',
  'A) vol_signal x tier, opt15 WR (vs tier baseline):',
  '   75-79 (base opt 47.9 / gen 65.7, N=3711): CONVICTION 48.7 (z_opt +0.67), THIN_AIR 48.5 (z_opt +0.23), REJECTION 44.4 (z_opt -1.78), ABSORPTION 49.0',
  '   80-84 (base 45.0 / 67.0, N=584): CONVICTION 43.1 (z_opt -0.58), THIN_AIR 66.7 (z_opt +2.62, N=39 TINY), REJECTION 42.4, ABSORPTION 53.1 (gen z +2.05)',
  '   85+   (base 48.0 / 71.0, N=404): CONVICTION 48.6 (z_opt +0.14), REJECTION 47.1',
  '   => CONVICTION amplification is option-FLAT at EVERY tier (z_opt +0.14..+0.67 = no edge). THIN_AIR CALLS WIN at/above baseline => the -30% THIN_AIR suppression on calls is empirically BACKWARDS. REJECTION is the only mildly-negative cohort (75-79 z_opt -1.78).',
  '',
  'B) CONVICTION 75+ by c_stoch (LOW c_stoch = OVERBOUGHT): <=10 opt47.3/gen62.0 (N=1001); 11-20 opt41.4/gen62.1 (N=203); 21-35 opt52.2/gen71.8 (N=291); >35 opt48.7/gen67.3 (N=1508). z overbought(<=15) vs healthy(>35): opt -1.27, gen -2.98, apex -1.05.',
  '   => overbought hole is GENERIC-ONLY (option z -1.27, NOT <= -3). This is why the VSG cap-overbought-CONVICTION patch came out FLAG (WR-neutral, not alpha).',
  '',
  'C) CONVICTION 75+ by vol_mag quartile, opt15: q1(mag<=0.19) 50.4 (N=752); q2(0.19-0.43) 47.2; q3(0.43-0.58) 50.9; q4(mag>0.58) 43.7 (N=750).',
  '   => vol_mag is ANTI-PREDICTIVE on the option barrier: HIGHEST conviction magnitude has the LOWEST opt WR (43.7 vs 50.4). The amplifier scales the score UP most for signals that win LEAST. The magnitude scaling is mis-calibrated for option trading. (q4 43.7 vs tier base ~47.9: modest negative; verify significance before treating as alpha.)',
  '',
  'D) CONVICTION vs NEUTRAL: NEUTRAL N=0 at 75+ -> essentially EVERY tradable call carries a non-NEUTRAL volume signal; the amplifier is pervasive (no clean control group).',
  '',
  'E) existing-mechanism overlap: of CONVICTION 75+, 35% already moved by MCD, 14% by SCW, 5% by continuation-echo. THIN_AIR: 22% MCD, 18% cont. => partial overlap; ~65% of CONVICTION NOT touched by MCD (room exists, but new edge must be net of MCD/SCW).',
  '',
  'STRUCTURE: volume hits the score in 3 places shipped sequentially: (1) daily amplifier (volume_amplifier.py): hard r50 cliffs 0.7/1.5/4.0 + candle-shape buckets -> CONVICTION/THIN_AIR/REJECTION/ABSORPTION/CLIMAX; CONVICTION/THIN_AIR multiply (overall-50) by mag (THIN_AIR -30%, CONVICTION +up to 50%, SIGN FLIPS at the cliff), ABSORPTION/CLIMAX blend toward a target; 3-day decay echo carries a burst forward. (2) WVD-Wave v46: inverted-U on weekly wv_force1. (3) DVAW v59: CONVICTION-calls-only lift/dampen keyed on wv_force1 + daily mag + EMA50-extension. WVD and DVAW BOTH key wv_force1 (double-count); amplifier+DVAW both key mag+extension.',
  'HISTORY/CAVEAT: the weekly-magnitude ablation showed volume is load-bearing on GENERIC WR30 (removing it cost 5-23pp at top tiers) -- so volume-as-a-whole carries generic-WR signal even though the DAILY amplifier (mag scaling, THIN_AIR-on-calls) looks option-miscalibrated. Do NOT propose deleting volume wholesale.',
  'GATE: Stage-1 W1 cohort-z >= +3 on the option/funded barrier to qualify as ALPHA. WR-neutral-by-construction (e.g. intraday-confidence gating the classification TYPE, which does not change EOD/backtest scores) = STABILITY ship (no version bump). Removing mis-calibrated structure with flat-or-up option WR = SIMPLIFICATION ship (version bump, validated WR-non-regression).',
].join('\n')

const FMT = '\n\nReturn MARKDOWN only (no preamble). For EACH candidate give: **id** (short slug), name, flaw_addressed, mechanism (continuous/gradient form), cohort+evidence (quote the A/B/C/D/E numbers), class (alpha|stability|simplification|layer-collapse), honest option-barrier z it relies on + PASS/FLAG/FAIL, and the main risk.'

phase('Propose')
const lenses = [
  ['simplify', 'You are the SIMPLIFICATION lens. The daily amplifier looks option-miscalibrated (CONVICTION flat, vol_mag anti-predictive q4 43.7 vs q1 50.4, THIN_AIR-on-calls backwards). Propose 1-2 candidates that REMOVE/flatten mis-calibrated structure (e.g. flatten or invert the mag-scaling, relax THIN_AIR suppression on calls, collapse the WVD+DVAW wv_force1 double-count) with flat-or-up option WR. Respect the caveat that volume is load-bearing on GENERIC WR30 -- do not delete volume wholesale.'],
  ['stability', 'You are the STABILITY lens. The classifier has hard r50 cliffs (0.7/1.5/4.0) where the signal TYPE flips sign, and intraday confidence scales magnitude but NOT type -> the ATI ~18pt intraday fakeout. Propose 1-2 candidates that fix temporal stability WITHOUT changing EOD/backtest scores (e.g. intraday-confidence gating the classification TYPE; smoothing the cliff into a continuous conviction function). State clearly whether each is WR-neutral-by-construction (STABILITY, no version bump) or actually changes EOD scores.'],
  ['alpha', 'You are the ALPHA-hunt lens, and you are SKEPTICAL. The surface shows CONVICTION is option-flat everywhere and the overbought/mag effects are generic-only or modest. Honestly assess whether ANY continuous volume transform can clear option-barrier z>=+3 net of MCD/SCW overlap. If yes, propose it precisely; if NOT, say so plainly and propose the strongest predictive-value-gated continuous conviction transform you can, labeled with its true (likely FLAG) z. Do NOT re-propose the VSG overbought-CONVICTION cap (already FLAG).'],
]
const proposals = (await parallel(lenses.map(function (pair) {
  return function () {
    return agent(DIGEST + '\n\n' + pair[1] + FMT, { label: 'propose:' + pair[0], phase: 'Propose' })
  }
}))).filter(Boolean)

phase('Refute')
const review = await agent(DIGEST + '\n\nCANDIDATES PROPOSED BY 3 LENSES (markdown):\n\n' + proposals.join('\n\n===== LENS BREAK =====\n\n') +
  '\n\nTASK: You are the adversarial skeptic. For EVERY distinct candidate above, attempt to REFUTE it using ONLY the surface: (1) barrier-divergence (option z>=+3 or generic-only? generic-only => FLAG not alpha); (2) thin-N (e.g. 80-84 THIN_AIR N=39); (3) mechanism overlap (already moved by MCD 35% / SCW 14% / cont 5%?); (4) VSG-relabel (overbought-CONVICTION cap => FLAG); (5) for SIMPLIFY: would it regress GENERIC WR30 (volume load-bearing)?; (6) for STABILITY: confirm it truly cannot change EOD/backtest scores. Then de-duplicate, and RANK all surviving candidates, assigning EACH a verdict: PROCEED_ALPHA / SIMPLIFY / STABILITY / DROP, with one-line justification + confidence. Return markdown.',
  { label: 'adversarial-refute', phase: 'Refute' })

phase('Report')
const report = await agent(DIGEST + '\n\nLENS PROPOSALS:\n' + proposals.join('\n\n---\n\n') + '\n\nADVERSARIAL REVIEW + RANKING:\n' + review +
  '\n\nTASK: write the FINAL volume-rework ship-candidacy report as MARKDOWN ONLY. Sections: (1) Headline verdict. (2) Direction call -- REBUILD vs SIMPLIFY vs HYBRID -- justified from the surface (CONVICTION option-flat, vol_mag anti-predictive, THIN_AIR-on-calls backwards, BUT generic-WR load-bearing). (3) Ranked candidate table (id | class | verdict | option-z | evidence one-liner). (4) Single RECOMMENDED next action: the best candidate to take to a parameter sweep + a queue-governed command template like `trader queue submit --priority high --db light --cpu 4 --dedup volrework-<id> -- python experiments/volume_rework/sweep_<id>.py` (note the sweep is a heavy ScoreSimulator re-score and MUST be queued, never raw). (5) What is NOT yet done: heavy parameter sweep + Stage-1 W2-W6 + growth gate + (if SHIP) recalc/assess are serial/queued follow-ups, not part of this read-only screen.',
  { label: 'report', phase: 'Report' })

return report
