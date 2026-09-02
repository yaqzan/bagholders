"""DD Mitigation Sweep — test three targeted DD-reduction rules.

Previous gate sweep (dd_gate_sweep.py) blocked entries too bluntly and
crushed compounding. This sweep tests three surgical alternatives:

  BASELINE     : current production
  PANIC_CLOSE  : VIX +25%/3d spike -> bail open calls at SL (close now
                 rather than wait for worse outcome)
  PUT_CAP      : SPY 5d return > +3% -> cap concurrent puts at PUT_CAP_MAX
                 (don't block, just don't ADD more puts beyond cap)
  PUT_TIGHT_TP : breadth rising +10pts/5d -> new puts use TP=20% (tight)
                 to lock gains before bear rally stops them out
  ALL          : all three combined

Panic-close mechanics: when VIX spike fires on day D, for each open call:
  - if its stored exit_bar would already have fired by D, use stored pnl
  - else, mark-to-market close using stock's price move from entry to D:
    option_pnl = DELTA * stock_return / premium_pct, clamped to [NET_SL, NET_TP]
  (honest MTM — captures the fact that some positions were winning, some
  losing, when the vol spike hits)

Metrics: MeanRet, WorstDD, P(collapse) across 6 windows x 3 collision modes.
"""
from __future__ import annotations
import os, sys, random, statistics
from collections import defaultdict
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

import monte_carlo as mc
from database.models.core import AlgorithmVersion, MarketRegime

N_ITER = 200

WINDOWS = [
    ('2021',   date(2021, 1, 1),  date(2021, 12, 31)),
    ('2022',   date(2022, 1, 1),  date(2022, 12, 31)),
    ('2023',   date(2023, 1, 1),  date(2023, 12, 31)),
    ('2024',   date(2024, 1, 1),  date(2024, 12, 31)),
    ('2025',   date(2025, 1, 1),  date(2025, 12, 31)),
    ('22-now', date(2022, 1, 1),  date(2026, 4, 15)),
]

# --- Rule parameters ---------------------------------------------------------
PANIC_VIX_PCT   = 0.25
PANIC_VIX_DAYS  = 3

PUT_CAP_SPY_PCT = 0.03       # SPY 5d ret > +3%
PUT_CAP_SPY_DAYS = 5
PUT_CAP_MAX     = 4          # cap concurrent puts when rally active

TIGHT_TP_BRD_DELTA = 10      # breadth_score up >=10 pts
TIGHT_TP_BRD_DAYS  = 5
TIGHT_PUT_TP    = 0.20       # tighter TP when rule fires
TIGHT_PUT_NET_TP = TIGHT_PUT_TP + mc.SLIP_ENTRY + mc.SLIP_TP


# --- Signal precompute -------------------------------------------------------
def load_spy_vix(d_start, d_end):
    rows = list(
        MarketRegime.select(MarketRegime.date, MarketRegime.spy_close, MarketRegime.vix_close)
        .where(MarketRegime.date >= d_start - timedelta(days=60),
               MarketRegime.date <= d_end + timedelta(days=10))
        .tuples()
    )
    return {d: (float(s) if s else None, float(v) if v else None) for d, s, v in rows}


def compute_day_flags(trading_days, spy_vix, breadth_dates, breadth_map):
    """Return per-day boolean arrays:
      panic[i]       : VIX spike today (panic-close calls)
      put_cap[i]     : SPY rally today (cap puts)
      put_tight[i]   : breadth rising today (use tight put TP)
    """
    n = len(trading_days)
    panic     = [False] * n
    put_cap   = [False] * n
    put_tight = [False] * n

    spy = [spy_vix.get(d, (None, None))[0] for d in trading_days]
    vix = [spy_vix.get(d, (None, None))[1] for d in trading_days]

    for i in range(n):
        if i >= PANIC_VIX_DAYS and vix[i] is not None:
            vp = vix[i - PANIC_VIX_DAYS]
            if vp and vp > 0 and (vix[i] / vp - 1) >= PANIC_VIX_PCT:
                panic[i] = True
        if i >= PUT_CAP_SPY_DAYS and spy[i] is not None:
            sp = spy[i - PUT_CAP_SPY_DAYS]
            if sp and sp > 0 and (spy[i] / sp - 1) >= PUT_CAP_SPY_PCT:
                put_cap[i] = True
        if i >= TIGHT_TP_BRD_DAYS:
            d_now = trading_days[i]
            d_prev = trading_days[i - TIGHT_TP_BRD_DAYS]
            b_now  = mc.breadth_on_or_before(breadth_dates, breadth_map, d_now)
            b_prev = mc.breadth_on_or_before(breadth_dates, breadth_map, d_prev)
            if b_now is not None and b_prev is not None and (b_now - b_prev) >= TIGHT_TP_BRD_DELTA:
                put_tight[i] = True
    return panic, put_cap, put_tight


# --- Put outcome variants (tight TP) -----------------------------------------
def precompute_put_outcomes_tight(signals, ph):
    """Put outcomes with TP=20% (tighter) — win fires faster, smaller gain."""
    tp_sigma = TIGHT_PUT_TP * mc.PREMIUM_MULT / mc.DELTA
    sl_sigma = abs(mc.PUT_SL) * mc.PREMIUM_MULT / mc.DELTA
    net_tp = TIGHT_PUT_NET_TP
    net_sl = mc.PUT_NET_SL

    out = {}
    for sig in signals:
        sym_bars = ph.get(sig.symbol_id)
        if not sym_bars:
            continue
        dates  = [b[0] for b in sym_bars]
        closes = [b[1] for b in sym_bars]
        highs  = [b[2] for b in sym_bars]
        lows   = [b[3] for b in sym_bars]
        try:
            base_idx = dates.index(sig.date)
        except ValueError:
            continue
        entry = closes[base_idx]
        if entry <= 0: continue
        vol = mc.realized_vol(closes, base_idx)
        if vol is None or vol <= 0: continue

        tp_level = entry * (1 - tp_sigma * vol / 100)
        sl_level = entry * (1 + sl_sigma * vol / 100)
        end_idx = min(len(dates), base_idx + 1 + mc.HOLD_DAYS)
        if end_idx <= base_idx + 1: continue

        kind = 'hard'; exit_bar = mc.HOLD_DAYS
        for i in range(base_idx + 1, end_idx):
            tp_hit = lows[i]  <= tp_level
            sl_hit = highs[i] >= sl_level
            if tp_hit and sl_hit:
                kind, exit_bar = 'both', i - base_idx; break
            if tp_hit:
                kind, exit_bar = 'tp', i - base_idx; break
            if sl_hit:
                kind, exit_bar = 'sl', i - base_idx; break
        out[(sig.symbol_id, sig.date)] = dict(
            kind=kind, exit_bar=exit_bar, net_tp=net_tp, net_sl=net_sl,
            vol=vol, entry=entry,
        )
    return out


# --- Position w/ MTM data ----------------------------------------------------
class PosEx:
    __slots__ = ['sym_id','entry_date','exit_bar','premium_cost','option_pnl',
                 'outcome','side','entry_price','premium_pct']
    def __init__(self, sym_id, entry_date, exit_bar, premium_cost, option_pnl,
                 outcome, side, entry_price, premium_pct):
        self.sym_id=sym_id; self.entry_date=entry_date; self.exit_bar=exit_bar
        self.premium_cost=premium_cost; self.option_pnl=option_pnl
        self.outcome=outcome; self.side=side
        self.entry_price=entry_price; self.premium_pct=premium_pct


def mtm_close_pnl(pos, today_close):
    """Mark-to-market close P&L (as option-premium fraction).
    Approx: option_pnl = DELTA * stock_ret / premium_pct, clamped [-0.8, +0.8].
    stock_ret is direction-signed (positive = favorable for the position).
    """
    if pos.entry_price <= 0 or pos.premium_pct <= 0:
        return mc.PUT_NET_SL if pos.side == 'put' else mc.NET_SL_BASE
    raw_ret = (today_close - pos.entry_price) / pos.entry_price
    if pos.side == 'put':
        raw_ret = -raw_ret
    pnl = mc.DELTA * raw_ret / pos.premium_pct + mc.SLIP_ENTRY + mc.SLIP_SL  # panic exit slippage
    if pnl < -0.80: pnl = -0.80
    if pnl >  1.50: pnl =  1.50
    return pnl


# --- Sim loop with mitigations -----------------------------------------------
def run_sim(trading_days, ph_by_sym, calls_by_date, call_outcomes,
            puts_by_date, put_outcomes, put_outcomes_tight, mode, rng,
            regime_dates, regime_map,
            panic, put_cap, put_tight,
            use_panic, use_put_cap, use_put_tight):
    cash = mc.STARTING_CASH
    positions = []
    peak_value = mc.STARTING_CASH
    max_dd = 0.0
    day_to_idx = {d: i for i, d in enumerate(trading_days)}

    tp_c = sl_c = hard_c = 0
    tp_p = sl_p = hard_p = 0
    panic_closes = 0

    for day_idx, today in enumerate(trading_days):
        # 1) panic close of open calls BEFORE natural exits (simulates intraday
        #    stop-out during the panic event)
        if use_panic and panic[day_idx]:
            # need today's close for each open call's symbol
            keep_after_panic = []
            for p in positions:
                if p.side != 'call':
                    keep_after_panic.append(p); continue
                base = day_to_idx.get(p.entry_date, -999)
                # if stored exit would already have fired by today, treat as normal
                if base + p.exit_bar <= day_idx:
                    keep_after_panic.append(p); continue
                # MTM close: find today's close for this sym
                sym_bars = ph_by_sym.get(p.sym_id)
                if not sym_bars:
                    keep_after_panic.append(p); continue
                tc = None
                for b in sym_bars:
                    if b[0] == today:
                        tc = b[1]; break
                if tc is None:
                    keep_after_panic.append(p); continue
                pnl = mtm_close_pnl(p, tc)
                cash += p.premium_cost * (1 + pnl)
                hard_c += 1  # classify panic-close as "hard"
                panic_closes += 1
            positions = keep_after_panic

        # 2) natural exits
        keep = []
        for p in positions:
            base = day_to_idx.get(p.entry_date, -999)
            if base + p.exit_bar <= day_idx:
                cash += p.premium_cost * (1 + p.option_pnl)
                if p.side == 'call':
                    if p.outcome == 'tp': tp_c += 1
                    elif p.outcome == 'sl': sl_c += 1
                    else: hard_c += 1
                else:
                    if p.outcome == 'tp': tp_p += 1
                    elif p.outcome == 'sl': sl_p += 1
                    else: hard_p += 1
            else:
                keep.append(p)
        positions = keep

        pv = cash + sum(p.premium_cost for p in positions)
        if pv > peak_value: peak_value = pv
        dd = (peak_value - pv) / peak_value if peak_value > 0 else 0
        if dd > max_dd: max_dd = dd
        if pv <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD: break

        open_syms = {p.sym_id for p in positions}
        call_open = sum(1 for p in positions if p.side == 'call')
        put_open  = sum(1 for p in positions if p.side == 'put')

        # calls (calls never panic-block new entries; only open ones get closed)
        day_calls = calls_by_date.get(today, [])
        if day_calls:
            eligible = [(sid, sc, k) for sid, sc, k in day_calls
                        if k in call_outcomes and sid not in open_syms]
            primary  = [e for e in eligible if e[1] >= mc.PRIMARY_THRESHOLD]
            overflow = [e for e in eligible if e[1] <  mc.PRIMARY_THRESHOLD]
            primary.sort(key=lambda x: (-x[1], rng.random()))
            overflow.sort(key=lambda x: (-x[1], rng.random()))
            reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
            reg_scale_c = mc.alloc_scale_for(reg_mult, is_put=False)
            for sym_id, score, key in primary + overflow:
                if len(positions) >= mc.MAX_POSITIONS: break
                alloc_frac = mc.TIER_ALLOC[mc.score_to_tier(score)] * reg_scale_c
                premium_cost = pv * alloc_frac
                if premium_cost > cash or premium_cost <= 0: continue
                o = call_outcomes[key]
                outcome, pnl = mc.resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                cash -= premium_cost
                positions.append(PosEx(sym_id, today, o['exit_bar'],
                                       premium_cost, pnl, outcome, 'call',
                                       o['entry'], o['premium_pct']))
                open_syms.add(sym_id); call_open += 1

        # puts (respect put_cap and put_tight if active)
        remaining = mc.MAX_POSITIONS - len(positions)
        if use_put_cap and put_cap[day_idx]:
            remaining = min(remaining, max(0, PUT_CAP_MAX - put_open))
        if remaining > 0:
            day_puts = puts_by_date.get(today, [])
            if day_puts:
                pe = [(sid, sc, k) for sid, sc, k in day_puts
                      if sid not in open_syms]
                pe.sort(key=lambda x: (x[1], rng.random()))
                reg_mult = mc.regime_on_or_before(regime_dates, regime_map, today) if regime_dates else 1.0
                reg_scale_p = mc.alloc_scale_for(reg_mult, is_put=True)
                # Which outcome table? tight or baseline
                use_tight = use_put_tight and put_tight[day_idx]
                table = put_outcomes_tight if use_tight else put_outcomes
                for sym_id, score, key in pe:
                    if len(positions) >= mc.MAX_POSITIONS: break
                    if use_put_cap and put_cap[day_idx] and put_open >= PUT_CAP_MAX: break
                    if key not in table: continue
                    alloc_frac = mc.PUT_TIER_ALLOC[mc.put_score_to_tier(score)] * reg_scale_p
                    premium_cost = pv * alloc_frac
                    if premium_cost > cash or premium_cost <= 0: continue
                    o = table[key]
                    outcome, pnl = mc.resolve(o['kind'], mode, rng, o['net_tp'], o['net_sl'])
                    cash -= premium_cost
                    # premium_pct not in put outcome; compute from vol
                    ppct = mc.PREMIUM_MULT * o['vol'] / 100
                    positions.append(PosEx(sym_id, today, o['exit_bar'],
                                           premium_cost, pnl, outcome, 'put',
                                           o['entry'], ppct))
                    open_syms.add(sym_id); put_open += 1

    for p in positions:
        cash += p.premium_cost * (1 + mc.NET_HARD_SELL)
        if p.side == 'call': hard_c += 1
        else: hard_p += 1

    return dict(final=cash, max_dd=max_dd, panic_closes=panic_closes,
                call_tp=tp_c, call_sl=sl_c, call_hard=hard_c,
                put_tp=tp_p, put_sl=sl_p, put_hard=hard_p)


def run_variant(label, variant_name, flags, data):
    (trading_days, ph_by_sym, calls_by_date, call_outcomes,
     puts_by_date, put_outcomes, put_outcomes_tight,
     regime_dates, regime_map, panic, put_cap, put_tight) = data
    use_panic, use_put_cap, use_put_tight = flags
    modes = ['conservative', 'realistic', 'optimistic']
    results = {}
    for mode in modes:
        finals = []; dds = []; collapses = 0; pcl = []
        for it in range(N_ITER):
            rng = random.Random(1000 * hash(label) + it)
            r = run_sim(trading_days, ph_by_sym, calls_by_date, call_outcomes,
                        puts_by_date, put_outcomes, put_outcomes_tight,
                        mode, rng, regime_dates, regime_map,
                        panic, put_cap, put_tight,
                        use_panic, use_put_cap, use_put_tight)
            finals.append(r['final']); dds.append(r['max_dd'])
            pcl.append(r['panic_closes'])
            if r['final'] <= mc.STARTING_CASH * mc.COLLAPSE_THRESHOLD: collapses += 1
        mean_ret = (statistics.mean(finals) / mc.STARTING_CASH - 1) * 100
        results[mode] = dict(
            mean_ret=mean_ret,
            worst_dd=max(dds) * 100,
            mean_dd=statistics.mean(dds) * 100,
            p_coll=collapses / N_ITER * 100,
            mean_panic=statistics.mean(pcl),
        )
    return results


def prep_window(label, d_start, d_end, version):
    call_sigs = mc.load_signals(version, d_start, d_end)
    put_sigs = mc.load_put_signals(version, d_start, d_end)
    sym_ids = list({s.symbol_id for s in call_sigs} | {s.symbol_id for s in put_sigs})
    ph = mc.load_price_history(sym_ids, d_start, d_end)
    breadth_dates, breadth_map = mc.load_breadth_map(d_start, d_end)
    regime_dates, regime_map = mc.load_regime_map(d_start, d_end)

    ph_dates = set()
    for bars in ph.values():
        for b in bars:
            if d_start <= b[0] <= d_end + timedelta(days=20): ph_dates.add(b[0])
    trading_days = sorted(ph_dates)

    calls_by_date = defaultdict(list)
    for sig in call_sigs:
        calls_by_date[sig.date].append((sig.symbol_id, sig.overall, (sig.symbol_id, sig.date)))
    puts_by_date = defaultdict(list)
    for sig in put_sigs:
        puts_by_date[sig.date].append((sig.symbol_id, sig.overall, (sig.symbol_id, sig.date)))

    call_outcomes = mc.precompute_outcomes(call_sigs, ph, breadth_dates, breadth_map)
    put_outcomes = mc.precompute_put_outcomes(put_sigs, ph, breadth_dates, breadth_map)
    put_outcomes_tight = precompute_put_outcomes_tight(put_sigs, ph)

    spy_vix = load_spy_vix(d_start, d_end)
    panic, put_cap, put_tight = compute_day_flags(trading_days, spy_vix,
                                                  breadth_dates, breadth_map)

    return (trading_days, ph, calls_by_date, call_outcomes,
            puts_by_date, put_outcomes, put_outcomes_tight,
            regime_dates, regime_map, panic, put_cap, put_tight)


def main():
    version = AlgorithmVersion.get_active_scores_version()
    print(f"Algorithm version: {version.git_commit}")
    print(f"Iterations per (variant x window x mode): {N_ITER}")
    print(f"Rules:")
    print(f"  PANIC_CLOSE : VIX +{PANIC_VIX_PCT*100:.0f}%/{PANIC_VIX_DAYS}d -> MTM close open calls")
    print(f"  PUT_CAP     : SPY +{PUT_CAP_SPY_PCT*100:.0f}%/{PUT_CAP_SPY_DAYS}d -> cap puts at {PUT_CAP_MAX}")
    print(f"  PUT_TIGHT_TP: breadth +{TIGHT_TP_BRD_DELTA}/{TIGHT_TP_BRD_DAYS}d -> puts use TP={TIGHT_PUT_TP*100:.0f}%")

    variants = [
        ('BASELINE',     (False, False, False)),
        ('PANIC_CLOSE',  (True,  False, False)),
        ('PUT_CAP',      (False, True,  False)),
        ('PUT_TIGHT_TP', (False, False, True)),
        ('ALL',          (True,  True,  True)),
    ]

    all_results = {}
    for label, d_start, d_end in WINDOWS:
        print(f"\n{'='*100}\nWINDOW: {label}\n{'='*100}")
        data = prep_window(label, d_start, d_end, version)
        (tds, _, _, _, _, _, _, _, _, panic, put_cap, put_tight) = data
        n = len(tds)
        print(f"  days={n}  panic_days={sum(panic)}  put_cap_days={sum(put_cap)}  put_tight_days={sum(put_tight)}")
        all_results[label] = {}
        for vname, flags in variants:
            print(f"  Running {vname}...", flush=True)
            r = run_variant(label, vname, flags, data)
            all_results[label][vname] = r
            for mode in ['realistic', 'conservative']:
                rr = r[mode]
                extra = f"  panic_closes/iter={rr['mean_panic']:.1f}" if vname in ('PANIC_CLOSE','ALL') else ''
                print(f"    {mode:<13}: ret={rr['mean_ret']:>+14,.1f}%  DD={rr['worst_dd']:>5.1f}%{extra}")

    # Summary tables
    def print_table(title, metric, fmt, mark_fn=None):
        print(f"\n{'='*120}\n{title}\n{'='*120}")
        hdr = f"{'Window':<10} " + ' '.join(f"{v[0]:>22}" for v in variants)
        print(hdr)
        for label, _, _ in WINDOWS:
            row = [f"{label:<10}"]
            base = all_results[label]['BASELINE']['realistic'][metric]
            for vname, _ in variants:
                r = all_results[label][vname]['realistic'][metric]
                if vname == 'BASELINE':
                    row.append(fmt(r, ''))
                else:
                    delta_str = ''
                    if mark_fn:
                        delta_str = mark_fn(r, base)
                    row.append(fmt(r, delta_str))
            print(' '.join(row))

    def ret_fmt(v, d):
        return f"{v:>+13,.0f}% {d:>6}"
    def dd_fmt_real(v, d):
        return f"{v:>16.1f}% {d:>3}"
    def dd_delta(r, base):
        return '<' if r < base - 1 else ('>' if r > base + 1 else ' ')
    def ret_delta(r, base):
        if base == 0: return ''
        pct = (r - base) / max(abs(base), 1.0) * 100
        return f"{pct:+.0f}%"

    print_table("REALISTIC MEAN RETURN", 'mean_ret', ret_fmt, ret_delta)
    print_table("REALISTIC WORST DD",    'worst_dd', dd_fmt_real, dd_delta)

    # Conservative DD (floor check)
    print(f"\n{'='*120}\nCONSERVATIVE WORST DD (80% floor)\n{'='*120}")
    hdr = f"{'Window':<10} " + ' '.join(f"{v[0]:>20}" for v in variants)
    print(hdr)
    for label, _, _ in WINDOWS:
        row = [f"{label:<10}"]
        for vname, _ in variants:
            r = all_results[label][vname]['conservative']['worst_dd']
            mark = ' X' if r >= 80.0 else '  '
            row.append(f"{r:>16.1f}%{mark}")
        print(' '.join(row))


if __name__ == '__main__':
    main()
