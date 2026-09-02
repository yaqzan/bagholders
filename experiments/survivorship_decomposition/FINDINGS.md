# Survivorship Decomposition — the number the $40 Sharadar purchase was bought for

**P2.A step 4 (final step) — DONE 2026-07-29.** Arms B/C ran as queue #191/#192 (N=300,
32 cells each, ~20 min/arm at 26 MC workers); arm A is the frozen 2026-07-19 baseline.
All 96 (arm × profile × window) cells complete; arm-B filter engagement verified in
32/32 MC logs; arm-C logs contain zero filter lines, as designed.

## The question

The 2026-07-29 `price_history` rebuild changed TWO things at once:
1. **Conventions repaired** — 44.1% of symbols carried mixed adjustment conventions inside
   their own series (84 backfill seams, phantom seam crashes median −28.9%).
2. **Universe expanded** — 895 → 1,626 symbols (S&P-500-ever PIT constituents + 600 delisted).

A naive before/after delta measures both jointly and would be misreported as "the
survivorship discount." This experiment isolates them with a middle arm.

## Design — three arms, two legs

| arm | conventions | universe | source |
|---|---|---|---|
| **A** | contaminated | survivor-only (pre-rebuild) | frozen `experiments/data_ingest/survivor_baseline_pre_sharadar/deep_crash_screen/` (run 2026-07-19 on this box) |
| **B** | **clean** | survivor-only **811** (arm A's universe) | `results/B/` |
| **C** | **clean** | full PIT (1,608 scored symbols) | `results/C/` |

- **A→B = substrate-repair effect** (conventions + everything else the rebuild changed — see caveats)
- **B→C = the TRUE survivorship discount** — same substrate, same scores, same breadth/regime,
  same engine, same per-(window, iter) seeds; the ONLY difference is which symbols' signals
  may enter the portfolio.

**Parameters (locked to arm A):** N=300, v74 pinned `f9fb7b934` (id 74), 16 windows
(deep 4: `ltcm_1998`, `dotcom_crash_2000_2002`, `gfc_crash_2007_2009`, `2007_now` + the
standard 12), profiles Core (bare `STRATEGY_30DTE`) + Apex (live 15-DTE `FROZEN_ENV` from
`run_h3_envelope.py`). Recipes are **imported** from the same modules arm A's driver used
(`experiments/deep_crash_screen/run_screen.py`, `experiments/holdout_oos_2026_12/run_h3_envelope.py`),
so they cannot drift. All three arms ran on this box (MC is deterministic per machine).

## Executive summary — three findings

**1. The substrate repair (A→B), not survivorship, is the dominant level-correction.**
On the survivor universe alone, repairing the conventions cut Core's 10y median compound
from **9,301% → 1,752%** and 5y from 3,050% → 1,787%; moved most standard-window DD reads
by +4 to +11pp; and — most actionably — **unmasked Apex 2022-bear collapse risk that the
contaminated substrate reported as zero: p_coll 0% → 48.7%** (and 22-now 0% → 51.0%),
figures that persist in arm C (47.7% / 65.3%). Every pre-2026-07-29 absolute level was
sitting on this contamination; the standing known-issues invalidation is confirmed and
quantified.

**2. The TRUE survivorship discount (B→C) is real but era- and profile-shaped — it is NOT
a uniform "honest = worse" haircut.** For Core (DD-primary, the held default):
- **DD deepens where the honest universe adds real casualties**: ltcm_1998 **+18.6pp**
  (39.3 → 57.9), the 2020-including windows +5.5/+5.5/+5.6pp (2020, 2020_crash, 10y),
  2007_now +4.9pp.
- **Recent-era compounding is trimmed** (the classic survivorship optimism): 5y median
  −231pp (1,787 → 1,557), 10y −271pp (1,752 → 1,481), 2024 −80pp — roughly a 13-17%
  relative haircut on survivor-only compound claims.
- **But inside the dot-com and GFC crash windows the honest universe IMPROVES Core's
  outcomes**: dotcom median return **−50.6% → +4.3% (+54.9pp)**, GFC −12.1% → +24.6%
  (+36.7pp), with DD ~flat (−0.7 / −1.4pp). The survivor-only universe in those eras is
  not "optimistic" — it is an unrepresentative sliver (39-58% of clean-substrate signal
  supply) tilted toward the era's young, volatile eventual-survivors, and it *understated*
  what the cascade could do with the era's actual diversified opportunity set. **Survivor
  bias biases universe composition, and the sign of its portfolio effect depends on the
  era — the old "survivor-only ⇒ crash DD reads optimistically" doc language is confirmed
  for LTCM/long-compound windows but refuted in direction for dotcom/GFC-era returns.**

**3. Collapse risk: Core p_coll = 0 in all 96 cells of all three arms** — the held
default's collapse=0 property is robust to both the repair and the honest universe.
For Apex (the live sprint profile), survivorship is primarily a **collapse-probability
discount on long horizons**: 2007_now p_coll 0.3% → **69.0%** (+68.7pp) and 10y 0% → 5.7%
on the clean substrate, with DD +8.7 to +12.9pp on 2020/2020_crash/2024/10y. In the
dotcom window the direction inverts (100% → 74.3% collapse — era diversification again).
The clean, honest read of continuously-held Apex is materially riskier than every prior
number: 2022 ~48% collapse, 22-now ~65%, 2007_now ~69%.

## Results — worst_dd (pp), the DD-primary metric

### CORE
| window | A | B | C | A→B (repair) | B→C (survivorship) | p_coll A/B/C |
|---|---|---|---|---|---|---|
| ltcm_1998 | 53.3 | 39.3 | 57.9 | −14.0 | **+18.6** | 0/0/0 |
| dotcom_crash_2000_2002 | 59.5 | 63.5 | 62.9 | +4.1 | −0.7 | 0/0/0 |
| gfc_crash_2007_2009 | 65.5 | 70.1 | 68.7 | +4.5 | −1.4 | 0/0/0 |
| 2007_now | 70.8 | 71.2 | 76.1 | +0.4 | **+4.9** | 0/0/0 |
| 2018 | 40.0 | 50.6 | 48.4 | +10.7 | −2.2 | 0/0/0 |
| 2020 | 69.9 | 70.7 | 76.2 | +0.8 | **+5.5** | 0/0/0 |
| 2020_crash | 69.7 | 70.6 | 76.1 | +0.9 | **+5.5** | 0/0/0 |
| 2021 | 31.9 | 35.1 | 33.6 | +3.2 | −1.5 | 0/0/0 |
| 2022 | 49.5 | 40.9 | 43.0 | −8.6 | +2.1 | 0/0/0 |
| 2023 | 46.2 | 52.8 | 53.5 | +6.6 | +0.7 | 0/0/0 |
| 2024 | 28.5 | 22.7 | 22.0 | −5.8 | −0.6 | 0/0/0 |
| dip | 25.8 | 32.2 | 32.1 | +6.4 | −0.0 | 0/0/0 |
| 22-now | 48.8 | 53.8 | 52.2 | +5.0 | −1.6 | 0/0/0 |
| 2025 | 37.6 | 41.7 | 43.5 | +4.1 | +1.8 | 0/0/0 |
| 5y | 49.6 | 54.2 | 51.3 | +4.6 | −2.9 | 0/0/0 |
| 10y | 69.5 | 70.6 | 76.2 | +1.1 | **+5.6** | 0/0/0 |

### APEX (live sprint profile; p_coll is the primary risk read here)
| window | A | B | C | A→B (repair) | B→C (survivorship) | p_coll A/B/C (%) |
|---|---|---|---|---|---|---|
| ltcm_1998 | 71.4 | 83.0 | 75.9 | +11.6 | −7.1 | 0/0/0 |
| dotcom_crash_2000_2002 | 92.2 | 85.0 | 96.3 | −7.2 | +11.3 | **100/100/74.3** |
| gfc_crash_2007_2009 | 86.5 | 90.7 | 85.3 | +4.2 | −5.3 | 20.7/16.0/3.3 |
| 2007_now | 95.9 | 98.0 | 91.9 | +2.2 | −6.2 | **48.3/0.3/69.0** |
| 2018 | 79.5 | 72.5 | 71.4 | −6.9 | −1.1 | 0/0/0 |
| 2020 | 75.4 | 74.8 | 83.5 | −0.6 | **+8.7** | 0/0/0 |
| 2020_crash | 71.1 | 75.5 | 84.5 | +4.4 | **+8.9** | 0/0/0 |
| 2021 | 63.1 | 63.7 | 65.1 | +0.5 | +1.4 | 0/0/0 |
| 2022 | 84.9 | 92.7 | 90.9 | +7.8 | −1.8 | **0/48.7/47.7** |
| 2023 | 75.6 | 79.8 | 78.6 | +4.2 | −1.2 | 0/0/0 |
| 2024 | 70.3 | 63.4 | 76.3 | −7.0 | **+12.9** | 0/0/0 |
| dip | 67.8 | 62.6 | 55.3 | −5.2 | −7.2 | 0/0/0 |
| 22-now | 86.8 | 92.7 | 90.8 | +5.9 | −1.9 | **0/51.0/65.3** |
| 2025 | 81.5 | 80.5 | 80.2 | −1.0 | −0.3 | 0/0/0 |
| 5y | 86.7 | 82.2 | 83.3 | −4.4 | +1.0 | 0/0/0 |
| 10y | 87.2 | 81.9 | 90.9 | −5.3 | **+9.0** | 0/0/**5.7** |

## Results — med_ret (%), the compound read

### CORE
| window | A | B | C | A→B (repair) | B→C (survivorship) |
|---|---|---|---|---|---|
| ltcm_1998 | −25.8 | −8.0 | −2.0 | +17.8 | +6.0 |
| dotcom_crash_2000_2002 | −43.0 | −50.6 | 4.3 | −7.6 | **+54.9** |
| gfc_crash_2007_2009 | −20.6 | −12.1 | 24.6 | +8.5 | **+36.7** |
| 2007_now | 3706.2 | 1606.6 | 4843.0 | −2099.6 | +3236.4 |
| 2018 | 13.7 | −22.2 | −24.7 | −36.0 | −2.5 |
| 2020 | −39.8 | −45.1 | −46.8 | −5.3 | −1.7 |
| 2020_crash | −44.8 | −48.3 | −55.1 | −3.4 | −6.9 |
| 2021 | 202.7 | 161.9 | 139.6 | −40.7 | −22.3 |
| 2022 | 8.7 | 25.6 | 19.8 | +16.9 | −5.8 |
| 2023 | −12.9 | −36.2 | −28.5 | −23.3 | +7.7 |
| 2024 | 454.2 | 467.3 | 387.2 | +13.1 | −80.1 |
| dip | 47.5 | 43.4 | 37.2 | −4.0 | −6.2 |
| 22-now | 893.0 | 513.9 | 473.4 | −379.0 | −40.6 |
| 2025 | 19.5 | −2.8 | −8.3 | −22.3 | −5.6 |
| 5y | 3050.1 | 1787.3 | 1556.5 | **−1262.8** | **−230.8** |
| 10y | 9301.1 | 1751.5 | 1480.6 | **−7549.6** | **−270.9** |

### APEX
| window | A | B | C | A→B (repair) | B→C (survivorship) |
|---|---|---|---|---|---|
| ltcm_1998 | 8.8 | −42.4 | −58.9 | −51.2 | −16.5 |
| dotcom_crash_2000_2002 | −86.3 | −81.3 | −81.5 | +5.0 | −0.2 |
| gfc_crash_2007_2009 | −74.7 | −71.3 | −66.8 | +3.4 | +4.5 |
| 2007_now | −14.9 | 1008.5 | −81.0 | +1023.4 | −1089.6 |
| 2018 | −57.5 | −41.6 | −0.3 | +15.9 | +41.3 |
| 2020 | −44.4 | −32.3 | −29.8 | +12.2 | +2.5 |
| 2020_crash | 8.7 | −11.5 | −2.3 | −20.1 | +9.2 |
| 2021 | 147.7 | 47.3 | −19.6 | −100.5 | −66.9 |
| 2022 | −57.5 | −54.6 | −70.9 | +2.9 | −16.3 |
| 2023 | −23.9 | −47.3 | −39.8 | −23.5 | +7.6 |
| 2024 | 82.4 | 160.1 | 348.5 | +77.7 | +188.4 |
| dip | −34.5 | 28.3 | 8.3 | +62.8 | −20.0 |
| 22-now | −48.4 | −85.1 | −81.5 | −36.8 | +3.6 |
| 2025 | −45.6 | −38.5 | −42.1 | +7.0 | −3.6 |
| 5y | 22.9 | 13.5 | −36.6 | −9.4 | −50.1 |
| 10y | 326.2 | −20.8 | −22.2 | −347.0 | −1.4 |

Apex medians on collapse-prone windows (dotcom, 2007_now, 22-now) are dominated by the
collapse fraction — read p_coll first, median second. The 2007_now sign flips (A −14.9 /
B +1008.5 / C −81.0) track the collapse rates (48.3% / 0.3% / 69.0%) exactly.

## The arm-B universe — exactly arm A's

Arm B restricts the portfolio to the **811 symbols that had price bars pre-rebuild**:

- **801** distinct symbols from `.cache/sharadar/backup_price_history_pre_rebuild.parquet`
  (the frozen pre-rebuild `price_history` snapshot; safety copy sha256-verified on B:).
- **+10** symbols the rebuild never touched (no Sharadar mapping): COL, HAR, FOX, FOXA,
  CBRS, RAM, ENA.V, HPS-A.TO, PINV.TO, VNP.TO.

Built by `build_universe.py` → `survivor_universe_811.txt` (DB-free, reproducible from the
frozen parquet). Scores are a subset of symbols-with-bars, so 811 is a superset of arm A's
actual signal pool; allow-listed symbols that never had scores contribute no signals — which
is arm A's behaviour for them too. Verified: **all 811 have v74 scores post-rebuild**, of
1,608 scored symbols total.

**Mechanism:** `MC_UNIVERSE_FILE` allow-list filter added to `monte_carlo.py`
`load_signals`/`load_put_signals` (all return paths; inert unless the env var is set;
portfolio-stage only — scores/breadth/regime untouched, per the no-env-gates-in-scoring rule).
Smoke-verified before launch (queue #188: 811 loaded, calls kept 4,047/5,386 on 2024).
Arm B's driver treats a cell whose MC log lacks the `[universe-filter]` engagement line as
FATAL, so a silently-inert filter cannot produce arm B ≡ arm C. Engine-level kept-counts
matched the DB-level preview exactly on all three spot-checked deep windows.

**Signal-supply (v74 scores ≥70, clean substrate — the filter's bite before the portfolio
engine ever runs):**

| window | full universe | survivor-only | kept |
|---|---|---|---|
| ltcm_1998 | 591 | 239 | 40.4% |
| dotcom_crash_2000_2002 | 12,951 | 5,048 | 39.0% |
| gfc_crash_2007_2009 | 4,879 | 2,827 | 57.9% |

## Doctrine

Deep windows are **SCREENS, not GATES** (assessment-backtest.md). This report presents
numbers and renders **no ship verdict**. A deep FAIL is a mechanism investigation, never an
automatic revert; a deep PASS is weak comfort, never collapse-proof. Nothing here retunes
any profile; the Apex collapse figures are inputs to the already-locked P0.3 discussion,
not a new gate.

## What each leg does and does not contain — read before citing

1. **A→B is "the rebuild effect," not purely "the convention repair."** Between A and B the
   substrate also changed in ways that cannot be unbundled without a third recalc chain:
   per-symbol history depth/quality (Sharadar backfill + `DECIMAL(18,6)` precision), the
   de-duplication of 7 double-counted companies (their duplicate bars deleted — arm A could
   hold "two" positions in one business; arm B cannot), and **scores/breadth/regime recomputed
   on the full 1,626-symbol universe** (dead names now inside TRIN/McClellan/A-D → regime
   multiplier → baked into the shared B/C scores). Cite A→B as **"substrate repair,"** whose
   dominant component is the convention repair.
2. **B→C is clean but is specifically the SELECTION-universe survivorship effect.** B and C
   share bit-identical scores, breadth, regime, engine, and per-(window, iter) seeds; only the
   allow-list differs. The survivorship channel that flows through breadth/regime into score
   levels is held fixed (both arms use full-universe breadth) — it lives in the A→B leg, not
   B→C. This is the honest decomposition available with one score substrate; isolating the
   breadth channel would need a survivor-only breadth+regime+recalc chain (a third substrate).
3. **N=300 noise floor** (`feedback_mc_noise_floor`): DD is stabler than compound at this N;
   read ±1-3pp DD deltas as within noise and the large moves (ltcm +18.6, 2020-era +5.5,
   Apex p_coll jumps) as directional. Both new arms are N=300 to pair with the frozen arm A —
   raising N for B/C alone would break the pairing. If tighter CIs are ever needed, re-run
   B and C together at higher N and say so; never mix Ns across arms.
4. **Never compare any of these numbers to pre-migration (old-box) results** — MC is
   deterministic per machine and divergent across machines. All three arms: this box.
5. The 10 unmapped symbols remain on their original (possibly mixed-convention) history in
   BOTH B and C — identical in the B→C leg, a residual ~1% impurity in the "clean" label
   (6/1,562 mapped symbols also remain mixed-convention post-rebuild, per data-acquisition.md).
6. Arm A additionally froze `newbox_ecert` (core/apex_live/apex_n10) and v74 research-pack
   `stress_windows` artifacts. This report scopes to the deep-crash-screen family per the
   locked parameters (Core + Apex); the ecert/apex_n10 and stress-pack comparisons remain
   available in the frozen baseline if ever needed, but re-running them was out of scope.

## Files

| file | role |
|---|---|
| `build_universe.py` → `survivor_universe_811.txt` | frozen arm-B allow-list (801 parquet + 10 unmapped) |
| `run_arm.py` | arm driver (per-cell subprocess via `_mc_pinned_runner`, resume-safe, arm-B filter guard) |
| `smoke_filter.py` | pre-launch one-cell filter engagement proof |
| `build_report.py` | A/B/C table + `results/decomposition.json` builder |
| `results/B/`, `results/C/` | per-(profile, window) MC JSONs + logs, per-arm `summary.json` |
| `results/decomposition.json` | machine-readable A/B/C + d_AB/d_BC for every metric |
