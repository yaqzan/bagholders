"""
Mechanical execution of the LOCKED residual-mining PREREG (lock=df5ccf6d, 2026-08-13).
experiments/residual_mining_2026_08/PREREG.md

Pure file analysis over .cache/relabel_substrate/honest_ledger_v1.parquet (READ-ONLY,
LOCKED). No MySQL, no monte_carlo import. Mechanical role: raw results only, every bar
applied exactly as declared. No verdicts computed here.

Operationalization choices made where the prereg's natural-language spec required one
(all disclosed, all fixed BEFORE any outcome was read):

1. "quantile bins" = quintiles (Q1..Q5 by rank), matching the explicit "quintile"
   language used in the sibling ct_bounce_formula PREREG's success bar. Coarse fields
   (e.g. weekly_transition_t, 6 unique values) collapse to fewer realized bins via
   polars .qcut(allow_duplicates=True) -- the ACTUAL realized label set is what gets
   enumerated, never a fictional 5 assumed regardless of the data.
2. Categorical features (volume_signal, liquidity_tier, ct_tag) enumerate one
   hypothesis per observed non-null category vs complement.
3. Boolean (delisted) enumerates ONE hypothesis (True vs complement) -- True-vs-rest
   and False-vs-rest are mirror images of the same effect, testing both would double
   count.
4. Each feature carries its own "universe" = ripe, post-embargo rows where that
   feature is non-null. A hypothesis on feature X (or interaction X*Y) is measured
   cohort-vs-complement WITHIN that universe (liquidity_tier's "(where present)"
   instruction generalized uniformly to every feature with nulls, e.g. mcap_b_pit,
   regime_composite, the 11 weekly fields which share one completeness gate).
5. Interaction pairs = the union (deduplicated) of the three named families:
   {component x regime_composite} (6) + {component x liquidity_tier} (6) +
   {trend x every other single feature} (23) = 33 unique unordered pairs (2 pairs
   are members of two families at once: trend x regime_composite, trend x
   liquidity_tier -- counted ONCE, not twice).
6. Interaction hypothesis = full cross-tab of the two features' own single-feature
   levels (levels_A x levels_B cells), each cell vs complement. This reuses the exact
   same bin edges/category sets as the single-feature analysis (no separate
   interaction-specific discretization -- avoids a second hidden researcher DOF).
7. Bar 6 (floor_control) is applied to EVERY hypothesis, not a hand-picked subset:
   every cohort enumerated here is a minority subset of its universe by construction
   (a quintile is ~20%, a category or interaction cell is typically smaller still),
   so every one of them "cuts supply" in the sense the exposure-matched-control trap
   (.claude/docs/traps.md, 2026-08-11 "supply-cutting mechanism" entry) warns about.
   5 seeded (per hypothesis, per draw) random subsets of matching cohort size, drawn
   from the same universe, "unchanged everything else" (same outcome column, same
   complement construction).
8. Primary significance test = Welch's two-sample t-test (unequal variance) on
   l2_expected; BH-FDR (scipy.stats.false_discovery_control, method='bh') applied
   across the FULL declared hypothesis count (degenerate/NaN p-values count toward
   the multiplicity as p=1.0, never silently dropped).
9. L3 validation (bar 5) requires >=10 rows on EACH side (cohort and complement)
   within the L3-covered subset to be considered "tested"; below that N the bar is
   marked untestable and the candidate FAILS bar 5 (cannot validate = doesn't survive
   -- consistent with "ALL required for candidate status").
"""
import json
import time
import datetime
from pathlib import Path
from itertools import product

import numpy as np
import polars as pl
from scipy import stats as sstats

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DRIVER_DIR = Path(__file__).resolve().parent
CAMPAIGN_DIR = DRIVER_DIR.parent
ROOT = CAMPAIGN_DIR.parent.parent
LEDGER_PATH = ROOT / '.cache' / 'relabel_substrate' / 'honest_ledger_v1.parquet'
OUT_DIR = CAMPAIGN_DIR / 'out'
LOG_DIR = CAMPAIGN_DIR / 'logs'
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------
EMBARGO_DATE = datetime.date(2026, 6, 15)     # rows with date > this: EXCLUDED
MINING_ERA_START = datetime.date(2021, 1, 4)
EFFECT_BAR = 0.03                             # |3.0pp| house d_ev floor, raw fraction units
FDR_Q = 0.05
N_QUANTILES = 5                               # quintiles
FLOOR_CONTROL_DRAWS = 5
MIN_L3_SIDE_N = 10                            # per-side floor for L3 bar to be "tested"
MIN_ERA_SIDE_N = 2                            # per-side floor for an era-third sign to be computable

ERA_THIRDS = [
    ('2021-22',      datetime.date(2021, 1, 1), datetime.date(2022, 12, 31)),
    ('2023-24',      datetime.date(2023, 1, 1), datetime.date(2024, 12, 31)),
    ('2025-H1-26',   datetime.date(2025, 1, 1), datetime.date(2026, 6, 15)),
]

COMPONENTS = ['trend', 'macd', 'rsi', 'bb', 'stoch', 'technical_alignment']
WEEKLY = ['w_comp', 'w_bias', 'w_mom', 'w_adj', 'wadj_completed', 'wadj_partial',
          'pre_regime', 'pre_boost', 'td', 'weekly_transition_t', 'weekly_adj_gap']
REGIME = ['regime_composite']
VOLUME = ['volume_signal', 'volume_magnitude']
LIQUIDITY = ['liquidity_tier']
MCAP = ['mcap_b_pit']
DELISTED = ['delisted']
CT = ['ct_tag']

ALL_SINGLE = COMPONENTS + WEEKLY + REGIME + VOLUME + LIQUIDITY + MCAP + DELISTED + CT
assert len(ALL_SINGLE) == 24, f'expected 24 single features, got {len(ALL_SINGLE)}'

CATEGORICAL_FEATURES = {'volume_signal', 'liquidity_tier', 'ct_tag'}
BOOLEAN_FEATURES = {'delisted'}
CONTINUOUS_FEATURES = set(ALL_SINGLE) - CATEGORICAL_FEATURES - BOOLEAN_FEATURES


def log(msg, fh):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    print(line)
    fh.write(line + '\n')
    fh.flush()


def build_interaction_pairs():
    set_a = {tuple(sorted([c, 'regime_composite'])) for c in COMPONENTS}
    set_b = {tuple(sorted([c, 'liquidity_tier'])) for c in COMPONENTS}
    set_c = {tuple(sorted(['trend', f])) for f in ALL_SINGLE if f != 'trend'}
    union = sorted(set_a | set_b | set_c)
    return union, set_a, set_b, set_c


def build_feature_specs(df):
    """For every single feature: discretize (quintile bin for continuous, raw value
    for categorical, boolean-as-is), and record (level_arr, levels, universe_mask)."""
    specs = {}
    n = df.height
    for feat in ALL_SINGLE:
        col = df[feat]
        if feat in CONTINUOUS_FEATURES:
            non_null_mask = col.is_not_null()
            binned = pl.Series([None] * n, dtype=pl.Utf8)
            if non_null_mask.sum() > 0:
                qc = col.qcut(N_QUANTILES, labels=[f'Q{i+1}' for i in range(N_QUANTILES)],
                               allow_duplicates=True)
                binned = qc.cast(pl.Utf8)
            level_arr = binned.to_numpy()
            levels = sorted(binned.unique().drop_nulls().to_list())
            universe_mask = non_null_mask.to_numpy()
            kind = 'continuous'
        elif feat in CATEGORICAL_FEATURES:
            level_arr = col.to_numpy()
            levels = sorted(col.unique().drop_nulls().to_list())
            universe_mask = col.is_not_null().to_numpy()
            kind = 'categorical'
        elif feat in BOOLEAN_FEATURES:
            level_arr = np.where(col.to_numpy(), 'True', 'False')
            levels = ['True']
            universe_mask = np.ones(n, dtype=bool)  # delisted has 0 nulls; full universe
            kind = 'boolean'
        else:
            raise AssertionError(feat)
        specs[feat] = dict(kind=kind, level_arr=level_arr, levels=levels, universe_mask=universe_mask)
    return specs


def enumerate_hypotheses(specs, interaction_pairs):
    rows = []
    hid = 0
    for feat in ALL_SINGLE:
        for lvl in specs[feat]['levels']:
            hid += 1
            rows.append(dict(hyp_id=hid, family='single', feat_a=feat, level_a=lvl,
                              feat_b=None, level_b=None))
    n_single = hid
    for (fa, fb) in interaction_pairs:
        for la, lb in product(specs[fa]['levels'], specs[fb]['levels']):
            hid += 1
            rows.append(dict(hyp_id=hid, family='interaction', feat_a=fa, level_a=la,
                              feat_b=fb, level_b=lb))
    n_interaction = hid - n_single
    return rows, n_single, n_interaction


def cohort_complement_masks(h, specs):
    if h['family'] == 'single':
        spec = specs[h['feat_a']]
        universe = spec['universe_mask']
        cohort = universe & (spec['level_arr'] == h['level_a'])
    else:
        spec_a, spec_b = specs[h['feat_a']], specs[h['feat_b']]
        universe = spec_a['universe_mask'] & spec_b['universe_mask']
        cohort = universe & (spec_a['level_arr'] == h['level_a']) & (spec_b['level_arr'] == h['level_b'])
    complement = universe & (~cohort)
    return universe, cohort, complement


def two_sample(values_cohort, values_complement):
    n_a, n_b = len(values_cohort), len(values_complement)
    out = dict(n_a=n_a, n_b=n_b, mean_a=np.nan, mean_b=np.nan, lift=np.nan,
               t_stat=np.nan, p_value=np.nan, ci_lo=np.nan, ci_hi=np.nan)
    if n_a < 2 or n_b < 2:
        return out
    a, b = values_cohort, values_complement
    mean_a, mean_b = float(a.mean()), float(b.mean())
    lift = mean_a - mean_b
    t_stat, p_value = sstats.ttest_ind(a, b, equal_var=False)
    var_a, var_b = a.var(ddof=1), b.var(ddof=1)
    se = np.sqrt(var_a / n_a + var_b / n_b)
    ci_lo, ci_hi = lift - 1.96 * se, lift + 1.96 * se
    out.update(mean_a=mean_a, mean_b=mean_b, lift=lift, t_stat=float(t_stat),
               p_value=float(p_value), ci_lo=float(ci_lo), ci_hi=float(ci_hi))
    return out


def main():
    log_path = LOG_DIR / 'mine_residuals.log'
    t0 = time.time()
    with open(log_path, 'w', encoding='utf-8') as fh:
        log(f'residual_mining_2026_08 -- mechanical execution start', fh)
        log(f'ledger path: {LEDGER_PATH}', fh)
        assert LEDGER_PATH.is_file(), f'LOCKED ledger not found at {LEDGER_PATH}'
        df_full = pl.read_parquet(LEDGER_PATH)
        n_total = df_full.height
        log(f'loaded {n_total} rows x {df_full.width} cols', fh)

        # --- HOLDOUT EMBARGO -------------------------------------------------
        n_embargo_dropped = df_full.filter(pl.col('date') > EMBARGO_DATE).height
        df = df_full.filter(pl.col('date') <= EMBARGO_DATE)
        assert df.filter(pl.col('date') > EMBARGO_DATE).height == 0
        log(f'HOLDOUT EMBARGO: dropped {n_embargo_dropped} rows with date > 2026-06-15; '
            f'{df.height} remain', fh)

        # --- RIPE FILTER -------------------------------------------------------
        n_unripe = df.filter(~pl.col('l2_ripe')).height
        df = df.filter(pl.col('l2_ripe'))
        log(f'RIPE FILTER: dropped {n_unripe} unripe rows (l2_ripe=false); {df.height} remain', fh)
        mining_n = df.height
        assert mining_n == df.filter(pl.col('l2_expected').is_not_null()).height, \
            'l2_ripe should exactly gate l2_expected non-null'
        assert df.filter(pl.col('date') < MINING_ERA_START).height == 0, \
            'unexpected rows before mining era start'
        log(f'MINING POPULATION N = {mining_n} (ripe, date in '
            f'[{MINING_ERA_START} .. {EMBARGO_DATE}])', fh)

        # --- feature specs + interaction pairs (BEFORE any outcome) -----------
        specs = build_feature_specs(df)
        for feat in ALL_SINGLE:
            s = specs[feat]
            log(f'  feature spec: {feat:22s} kind={s["kind"]:11s} '
                f'levels={s["levels"]} universe_n={int(s["universe_mask"].sum())}', fh)

        interaction_pairs, set_a, set_b, set_c = build_interaction_pairs()
        log(f'interaction pairs: {len(interaction_pairs)} unique '
            f'(component x regime={len(set_a)}, component x liquidity={len(set_b)}, '
            f'trend x any={len(set_c)}, overlap dedup = '
            f'{len(set_a) + len(set_b) + len(set_c) - len(interaction_pairs)})', fh)

        hyp_rows, n_single, n_interaction = enumerate_hypotheses(specs, interaction_pairs)
        total_hypotheses = len(hyp_rows)
        log(f'ENUMERATED {total_hypotheses} hypotheses TOTAL '
            f'({n_single} single + {n_interaction} interaction) -- '
            f'LOGGED BEFORE COMPUTING ANY OUTCOME', fh)

        # persist the pre-outcome enumeration as proof-of-order
        enum_path = OUT_DIR / 'hypothesis_enumeration.csv'
        pl.DataFrame(hyp_rows, infer_schema_length=None).write_csv(enum_path)
        log(f'pre-outcome enumeration written: {enum_path}', fh)

        # --- arrays for fast repeated stats ------------------------------------
        l2 = df['l2_expected'].to_numpy().astype(float)
        l1 = df['l1_exit_return'].to_numpy().astype(float)  # may contain NaN
        l3 = df['l3_realized_pnl_cd27'].to_numpy().astype(float)  # may contain NaN
        delisted_arr = df['delisted'].to_numpy()

        # era-third assignment (integer 0/1/2), every mining row must land in one
        date_arr = df['date'].to_numpy()
        era_arr = np.full(mining_n, -1, dtype=int)
        for idx, (name, lo, hi) in enumerate(ERA_THIRDS):
            lo_np, hi_np = np.datetime64(str(lo)), np.datetime64(str(hi))
            m = (date_arr >= lo_np) & (date_arr <= hi_np)
            era_arr[m] = idx
        n_unassigned_era = int((era_arr == -1).sum())
        assert n_unassigned_era == 0, f'{n_unassigned_era} rows did not land in any era third'
        log(f'era-third assignment: '
            + ', '.join(f'{ERA_THIRDS[i][0]}={int((era_arr==i).sum())}' for i in range(3)), fh)

        # --- compute all bars for every hypothesis (cheap: numpy masks) -------
        log('computing bars 1/3/4/5/6 for every enumerated hypothesis...', fh)
        results = []
        for h in hyp_rows:
            universe, cohort, complement = cohort_complement_masks(h, specs)
            primary = two_sample(l2[cohort], l2[complement])
            lift = primary['lift']
            overall_sign = np.sign(lift) if not np.isnan(lift) else 0.0

            # bar 1: effect
            bar1 = bool(not np.isnan(lift) and abs(lift) >= EFFECT_BAR)

            # bar 3: era stability (sign match in >=2/3 thirds)
            era_signs = []
            for era_idx in range(3):
                era_mask = (era_arr == era_idx)
                a = l2[cohort & era_mask]
                b = l2[complement & era_mask]
                if len(a) < MIN_ERA_SIDE_N or len(b) < MIN_ERA_SIDE_N:
                    era_signs.append(None)
                else:
                    era_signs.append(float(np.sign(a.mean() - b.mean())))
            n_era_match = sum(1 for s in era_signs if s is not None and overall_sign != 0 and s == overall_sign)
            bar3 = bool(n_era_match >= 2)

            # bar 4: survivor-robust (delisted excluded)
            surv_universe = universe & (~delisted_arr)
            surv_cohort = surv_universe & cohort
            surv_complement = surv_universe & complement
            surv_stats = two_sample(l2[surv_cohort], l2[surv_complement])
            surv_lift = surv_stats['lift']
            bar4 = bool(not np.isnan(surv_lift) and abs(surv_lift) >= EFFECT_BAR
                        and overall_sign != 0 and np.sign(surv_lift) == overall_sign)

            # bar 5: L3 validation (same-sign, on covered subset, both sides >= MIN_L3_SIDE_N)
            l3_covered = ~np.isnan(l3)
            l3_cohort_mask = universe & cohort & l3_covered
            l3_complement_mask = universe & complement & l3_covered
            l3_stats = two_sample(l3[l3_cohort_mask], l3[l3_complement_mask])
            l3_testable = (l3_stats['n_a'] >= MIN_L3_SIDE_N and l3_stats['n_b'] >= MIN_L3_SIDE_N)
            bar5 = bool(l3_testable and overall_sign != 0
                        and not np.isnan(l3_stats['lift'])
                        and np.sign(l3_stats['lift']) == overall_sign)

            # bar 6: floor_control exposure-matched random subsets (5 seeded draws)
            universe_idx = np.flatnonzero(universe)
            n_cohort = primary['n_a']
            random_lifts = []
            if n_cohort >= 2 and len(universe_idx) > n_cohort:
                for draw in range(FLOOR_CONTROL_DRAWS):
                    rng = np.random.default_rng([h['hyp_id'], draw])
                    rand_pick = rng.choice(universe_idx, size=n_cohort, replace=False)
                    rand_mask = np.zeros(mining_n, dtype=bool)
                    rand_mask[rand_pick] = True
                    rand_complement = universe & (~rand_mask)
                    a, b = l2[rand_mask], l2[rand_complement]
                    if len(a) >= 2 and len(b) >= 2:
                        random_lifts.append(float(a.mean() - b.mean()))
            mean_random_lift = float(np.mean(random_lifts)) if random_lifts else np.nan
            attributable_lift = (lift - mean_random_lift) if (not np.isnan(lift) and not np.isnan(mean_random_lift)) else np.nan
            bar6 = bool(not np.isnan(attributable_lift) and abs(attributable_lift) >= EFFECT_BAR
                        and overall_sign != 0 and np.sign(attributable_lift) == overall_sign)

            # l1 contrast (non-gating, reported only)
            l1_cohort_mask = universe & cohort & ~np.isnan(l1)
            l1_complement_mask = universe & complement & ~np.isnan(l1)
            l1_stats = two_sample(l1[l1_cohort_mask], l1[l1_complement_mask])

            candidate = bool(bar1 and bar3 and bar4 and bar5 and bar6)  # bar2 (fdr) applied after

            results.append(dict(
                hyp_id=h['hyp_id'], family=h['family'], feat_a=h['feat_a'], level_a=h['level_a'],
                feat_b=h['feat_b'], level_b=h['level_b'],
                n_cohort=primary['n_a'], n_complement=primary['n_b'],
                mean_cohort=primary['mean_a'], mean_complement=primary['mean_b'],
                lift_pp=lift * 100 if not np.isnan(lift) else np.nan,
                ci_lo_pp=primary['ci_lo'] * 100 if not np.isnan(primary['ci_lo']) else np.nan,
                ci_hi_pp=primary['ci_hi'] * 100 if not np.isnan(primary['ci_hi']) else np.nan,
                t_stat=primary['t_stat'], p_value=primary['p_value'],
                bar1_effect_pass=bar1,
                era_2021_22_sign=era_signs[0], era_2023_24_sign=era_signs[1], era_2025_H1_26_sign=era_signs[2],
                n_era_thirds_matching=n_era_match, bar3_era_pass=bar3,
                survivor_lift_pp=surv_lift * 100 if not np.isnan(surv_lift) else np.nan,
                survivor_n_cohort=surv_stats['n_a'], survivor_n_complement=surv_stats['n_b'],
                bar4_survivor_pass=bar4,
                l3_n_cohort=l3_stats['n_a'], l3_n_complement=l3_stats['n_b'],
                l3_mean_cohort=l3_stats['mean_a'], l3_mean_complement=l3_stats['mean_b'],
                l3_lift_pp=l3_stats['lift'] * 100 if not np.isnan(l3_stats['lift']) else np.nan,
                l3_testable=l3_testable, bar5_l3_pass=bar5,
                floor_control_mean_random_lift_pp=mean_random_lift * 100 if not np.isnan(mean_random_lift) else np.nan,
                floor_control_attributable_lift_pp=attributable_lift * 100 if not np.isnan(attributable_lift) else np.nan,
                bar6_floor_control_pass=bar6,
                candidate_pre_fdr=candidate,
                l1_n_cohort=l1_stats['n_a'], l1_n_complement=l1_stats['n_b'],
                l1_contrast_lift=l1_stats['lift'],
            ))
        log(f'computed bars for all {len(results)} hypotheses', fh)

        # --- bar 2: BH-FDR across the FULL declared count ----------------------
        p_raw = np.array([r['p_value'] for r in results], dtype=float)
        p_for_fdr = np.where(np.isnan(p_raw), 1.0, p_raw)
        assert len(p_for_fdr) == total_hypotheses
        q_values = sstats.false_discovery_control(p_for_fdr, method='bh')
        n_nan_p = int(np.isnan(p_raw).sum())
        log(f'BH-FDR computed across m={total_hypotheses} (declared hypothesis count); '
            f'{n_nan_p} degenerate/NaN p-values set to p=1.0 for the correction', fh)

        for r, q in zip(results, q_values):
            r['q_value'] = float(q)
            r['bar2_fdr_pass'] = bool(q <= FDR_Q)
            r['candidate'] = bool(r['candidate_pre_fdr'] and r['bar2_fdr_pass'])

        res_df = pl.DataFrame(results, infer_schema_length=None)
        res_df = res_df.sort(['candidate', 'bar1_effect_pass'], descending=[True, True])

        out_path = OUT_DIR / 'residual_mining_results.csv'
        res_df.write_csv(out_path)
        log(f'full ranked results table written: {out_path} ({res_df.height} rows x {res_df.width} cols)', fh)

        n_candidates = int(res_df['candidate'].sum())
        n_bar1 = int(res_df['bar1_effect_pass'].sum())
        n_bar2 = int(res_df['bar2_fdr_pass'].sum())
        n_bar1_2 = int((res_df['bar1_effect_pass'] & res_df['bar2_fdr_pass']).sum())
        n_bar3_given_12 = int((res_df['bar1_effect_pass'] & res_df['bar2_fdr_pass'] & res_df['bar3_era_pass']).sum())
        n_bar4_given_123 = int((res_df['bar1_effect_pass'] & res_df['bar2_fdr_pass'] & res_df['bar3_era_pass']
                                 & res_df['bar4_survivor_pass']).sum())
        n_bar5_given_1234 = int((res_df['bar1_effect_pass'] & res_df['bar2_fdr_pass'] & res_df['bar3_era_pass']
                                  & res_df['bar4_survivor_pass'] & res_df['bar5_l3_pass']).sum())

        log(f'SURVIVAL FUNNEL: enumerated={total_hypotheses} -> bar1(effect>=3pp)={n_bar1} '
            f'-> bar2(FDR<=0.05)={n_bar2} -> bar1&2={n_bar1_2} -> +bar3(era)={n_bar3_given_12} '
            f'-> +bar4(survivor)={n_bar4_given_123} -> +bar5(L3)={n_bar5_given_1234} '
            f'-> +bar6(floor_control)=CANDIDATES={n_candidates}', fh)

        wall = time.time() - t0
        meta = dict(
            run_timestamp=time.strftime('%Y-%m-%dT%H:%M:%S'),
            wall_time_sec=round(wall, 2),
            ledger_path=str(LEDGER_PATH),
            n_ledger_rows_total=n_total,
            n_embargo_dropped=n_embargo_dropped,
            n_unripe_dropped=n_unripe,
            mining_population_n=mining_n,
            n_single_features=len(ALL_SINGLE),
            n_interaction_pairs=len(interaction_pairs),
            n_hypotheses_single=n_single,
            n_hypotheses_interaction=n_interaction,
            n_hypotheses_total=total_hypotheses,
            effect_bar_pp=EFFECT_BAR * 100,
            fdr_q=FDR_Q,
            n_quantiles=N_QUANTILES,
            min_l3_side_n=MIN_L3_SIDE_N,
            floor_control_draws=FLOOR_CONTROL_DRAWS,
            n_bar1_effect=n_bar1,
            n_bar2_fdr=n_bar2,
            n_bar1_and_bar2=n_bar1_2,
            n_bar1_2_3=n_bar3_given_12,
            n_bar1_2_3_4=n_bar4_given_123,
            n_bar1_2_3_4_5=n_bar5_given_1234,
            n_candidates_final=n_candidates,
            results_csv=str(out_path),
            enumeration_csv=str(enum_path),
        )
        meta_path = OUT_DIR / 'run_meta.json'
        with open(meta_path, 'w', encoding='utf-8') as mf:
            json.dump(meta, mf, indent=2)
        log(f'run meta written: {meta_path}', fh)
        log(f'DONE in {wall:.1f}s', fh)

    return meta, res_df


if __name__ == '__main__':
    main()
