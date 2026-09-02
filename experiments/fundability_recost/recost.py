"""Fundability re-evaluation under the REALISTIC 1.5-3% execution-cost band.

Context: the v69 "not fundable" verdict (experiments/v69_portfolio_retune, diag2_cost.py)
hinged on a SYMMETRIC ~6% round-trip spread on the v69-hygiene 85+-only config. The user
clarified the realistic cost is 1.5-3% (not 6%), and the 2026-06-02 asymmetric canon
(EXECUTION_COST_CANON_CATCHUP.md) makes it cheaper still: mid-entry + limit-TP cross NO
spread; only FORCED exits (SL / day-15 hard-sell / dead-hold-expiry) pay the half-spread.

This re-runs the cost question on the CURRENT LIVE v70 Apex (75+ full cascade + overflow +
dead-hold popout + RXDD + SVR + MWDD — everything shipped since the v69 verdict), across
1.5-3% under BOTH cost models, and benchmarks vs buy-and-hold SPY (return + max DD).

  - asymmetric(S): SLIP_ENTRY=SLIP_TP=0, SLIP_SL=SLIP_HARD=-S   (canon; only forced exits pay)
  - symmetric(S):  SLIP_ENTRY=SLIP_TP=SLIP_SL=SLIP_HARD=-S/2     (D2 model; every leg pays, ~S round-trip)

Run (queue, --cpu 6):
  PYTHONIOENCODING=utf-8 N_ITER=200 python -u experiments/fundability_recost/recost.py
"""
from __future__ import annotations
import json, os, sys, time, math

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('ALGORITHM_VERSION_PIN', 'c70d16d22')  # v70 active
from experiments.v69_portfolio_retune.driver import run_candidate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS = ['2022', '5y', '10y', '22-now']
# window date ranges (mirror monte_carlo.WINDOWS) for the SPY benchmark
WIN_DATES = {
    '2022':   ('2022-01-01', '2022-12-31'),
    '5y':     ('2021-01-01', '2026-04-15'),
    '10y':    ('2016-06-01', '2026-04-15'),
    '22-now': ('2022-01-01', '2026-04-24'),
}

# v69 C4_full (85+-only hygiene config diag2 tested) — to re-settle the old verdict.
C4_FULL = dict(TIER_ULTRA=0.20, TIER_TOP=0.15, TIER_MID=0.0, TIER_LOW=0.0,
               PUT_TIER_TOP=0.0012, PUT_TIER_MID=0.001, PUT_TIER_LOW=0.0008,
               MAX_POSITIONS=8, MAX_POSITIONS_CALL=7, MAX_POSITIONS_PUT=2, TP_BASE=0.28)


def asym(S):  # canon: only forced exits pay the half-spread S
    return {'SLIP_ENTRY': 0.0, 'SLIP_TP': 0.0, 'SLIP_SL': round(-S, 4), 'SLIP_HARD': round(-S, 4)}


def sym(S):   # D2 model: every leg pays S/2 -> ~S round-trip
    h = round(-S / 2.0, 4)
    return {'SLIP_ENTRY': h, 'SLIP_TP': h, 'SLIP_SL': h, 'SLIP_HARD': h}


# (label, base_config, cost_overrides). base {} = live v70 Apex (strategy_config defaults).
CANDS = [
    ('apex_asym_0.0',  {}, asym(0.0)),    # frictionless anchor (shows the cost-drag magnitude)
    ('apex_asym_1.5',  {}, asym(0.015)),  # = LIVE shipped canon
    ('apex_asym_2.0',  {}, asym(0.020)),
    ('apex_asym_2.5',  {}, asym(0.025)),
    ('apex_asym_3.0',  {}, asym(0.030)),  # conservative forced-exit magnitude
    ('apex_sym_1.5',   {}, sym(0.015)),   # 1.5% round-trip, every leg
    ('apex_sym_2.25',  {}, sym(0.0225)),
    ('apex_sym_3.0',   {}, sym(0.030)),   # 3% round-trip, every leg (conservative bound)
    ('v69C4_sym_3.0',  C4_FULL, sym(0.030)),   # re-settle v69 verdict at the realistic band end
    ('v69C4_asym_3.0', C4_FULL, asym(0.030)),  # re-settle v69 under the canon
]


def spy_benchmark():
    """Buy-and-hold SPY total return + max drawdown per window (deterministic, from PriceHistory)."""
    from database.models.technical import PriceHistory
    from datetime import date
    rows = list(PriceHistory.select(PriceHistory.date, PriceHistory.close)
                .where(PriceHistory.symbol == 'SPY').order_by(PriceHistory.date).tuples())
    series = [(d, float(c)) for d, c in rows if c is not None]
    out = {}
    for w, (s, e) in WIN_DATES.items():
        sd, ed = date.fromisoformat(s), date.fromisoformat(e)
        seg = [(d, c) for d, c in series if sd <= d <= ed]
        if len(seg) < 2:
            continue
        closes = [c for _, c in seg]
        ret = (closes[-1] / closes[0] - 1.0) * 100.0
        peak = closes[0]; mdd = 0.0
        for c in closes:
            peak = max(peak, c)
            mdd = max(mdd, (peak - c) / peak)
        yrs = (seg[-1][0] - seg[0][0]).days / 365.25
        cagr = ((closes[-1] / closes[0]) ** (1.0 / yrs) - 1.0) * 100.0 if yrs > 0 else float('nan')
        out[w] = {'ret_pct': round(ret, 1), 'cagr_pct': round(cagr, 2), 'max_dd_pct': round(mdd * 100, 1), 'years': round(yrs, 2)}
    return out


def main():
    n_iter = int(os.environ.get('N_ITER', '200'))
    print('== SPY buy-and-hold benchmark ==', flush=True)
    spy = spy_benchmark()
    for w, d in spy.items():
        print(f'  SPY {w:7s} ret={d["ret_pct"]:>8.1f}%  CAGR={d["cagr_pct"]:>6.2f}%  maxDD={d["max_dd_pct"]:>5.1f}%  ({d["years"]}y)', flush=True)

    out_path = os.path.join(HERE, f'recost_n{n_iter}.jsonl')
    rows = []
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'spy_benchmark': spy}) + '\n'); f.flush()
        for name, base, cost in CANDS:
            params = {**base, **cost}
            t0 = time.time()
            try:
                w = run_candidate(params, n_iter, WINDOWS, workers=6, tag=name)
            except Exception as e:
                print(f'  ERROR {name}: {e}', flush=True)
                continue
            rec = {'name': name, 'params': params,
                   'win': {k: {'med': w[k]['med_ret'], 'dd': w[k]['worst_dd'], 'coll': w[k]['p_collapse']} for k in WINDOWS},
                   'dur_s': round(time.time() - t0, 1)}
            f.write(json.dumps(rec) + '\n'); f.flush()
            rows.append(rec)
            ww = rec['win']
            print(f'[{name:16s}] 5y med={ww["5y"]["med"]:>14.1f}% dd={ww["5y"]["dd"]:>5.1f}  '
                  f'10y med={ww["10y"]["med"]:>16.1f}% dd={ww["10y"]["dd"]:>5.1f}  '
                  f'22now med={ww["22-now"]["med"]:>14.1f}% dd={ww["22-now"]["dd"]:>5.1f}  '
                  f'2022 med={ww["2022"]["med"]:>8.1f}% coll={max(ww[k]["coll"] for k in WINDOWS)}', flush=True)
    print(f'\nwrote {out_path}', flush=True)


if __name__ == '__main__':
    main()
