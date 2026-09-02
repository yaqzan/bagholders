"""Phase A — entry-policy realism comparison on v71 75+ calls (5y).

Question (user, 2026-06-11): assess "only buying at open vs only buying at/near
close, or some mix" — options trade market-hours only, so the practical close
entry is the last ~30 min (Phase 0 measured that proxy as faithful: 83% signal
coverage, 0.36% median price drift, 8% fade risk).

Policies (calls only, qualification = close score >= QUAL under the active version):
  C        enter at signal-day close   (the backtest/MC anchor ~= last-30-min live buy)
  N        enter at next trading day's OPEN, unconditional
  N_px_*   enter at next open only when the overnight gap passes a sigma filter
           (the honest stand-in for "open-score confirmation" — the gap is the
           dominant input to any reconstructed open score; full reconstruction
           is Phase A2 and must be validated vs score_intraday_logs first)

Outcomes per policy, anchored at the policy's OWN entry price:
  apexW15  TP +1.092s / SL -2.548s / 15 trading bars  (live Apex TP30/SL70 at
           PREMIUM_MULT 1.82, delta 0.5; matches the intraday_overnight -1.22pp
           measurement window)
  apexW19  same barriers / 19 bars (~27 calendar days, the live CALENDAR_HOLD)
  holdTP15 TP-only reach within 15 bars (no SL; Apex's wide SL rarely binds)

Methodology:
  - C walks bars D+1..D+W from entry=close(D).
  - N walks the SAME bars D+1..D+W from entry=open(D+1): the open is the first
    print of the entry bar, so that bar's full range is post-entry.
  - Same-bar TP+SL touch = LOSS (conservative), applied identically to both
    policies; tie rates reported. Expire (neither barrier) = LOSS (apex
    semantics). Unresolved (insufficient forward bars) excluded pairwise.
  - For each gap filter we report the entered-subset WR under BOTH anchors
    (isolates entry-price effect from selection effect) and the skipped-subset
    C-anchor WR (does the filter actually drop worse signals?).

Run:  python experiments/entry_timing_v71/run_phase_a.py [--smoke] [--force]
                                                          [--vid N] [--qual 75]
Queue (full): trader queue submit --priority normal --db heavy --cpu 2 \
    --restartable --dedup entry-timing-v71-phaseA -- python experiments/entry_timing_v71/run_phase_a.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT = Path(__file__).resolve().parent / 'out'
OUT.mkdir(exist_ok=True)

SIGMA_WIN = 60          # 60-bar realized daily vol (matches assess convention)
LOOKBACK_YEARS = 5
OHLC_START = '2020-06-01'   # warmup margin ahead of the 5y signal window

BARRIERS = {
    'apexW15':  dict(tp=1.092, sl=2.548, w=15),
    'apexW19':  dict(tp=1.092, sl=2.548, w=19),
    'holdTP15': dict(tp=1.092, sl=None,  w=15),
}

# outcome codes
TP, SL, TIE, EXPIRE, UNRESOLVED = 1, 0, 2, 3, -1

SMOKE_SYMS = ['AAPL', 'NVDA', 'MSFT', 'AMD', 'TSLA', 'SOXX', 'XLK', 'ARKX', 'SMH', 'PLTR']


# ---------------------------------------------------------------- data pulls

def _resolve_vid(cli_vid):
    if cli_vid:
        return int(cli_vid)
    from database.models.core import AlgorithmVersion
    v = AlgorithmVersion.get_active_scores_version()
    print(f"active scores version: id={v.id}")
    return int(v.id)


def load_ohlc(force: bool, smoke: bool) -> pl.DataFrame:
    from database.bulk_cache import materialize_polars, chunked_query_by_year, cache_path

    if smoke:
        from database.trader_database import DB
        syms = ", ".join(f"'{s}'" for s in SMOKE_SYMS)
        cur = DB.execute_sql(
            "SELECT symbol, date, open, high, low, close FROM price_history "
            f"WHERE symbol IN ({syms}) AND date >= '{OHLC_START}'"
        )
        rows = cur.fetchall()
        return _ohlc_df(rows)

    def _build():
        rows = chunked_query_by_year(
            "SELECT symbol, date, open, high, low, close FROM price_history "
            "WHERE date BETWEEN '{y_start}' AND '{y_end}' AND date >= '{min_date}'",
            start_year=2020, end_year=dt.date.today().year + 1, min_date=OHLC_START,
        )
        return _ohlc_df(rows)

    name = 'entry_timing_ohlc_2020'
    # forward walks need bars through ~today: rebuild if the cache is > 2 days old
    p = cache_path(name)
    stale = p.exists() and (time.time() - p.stat().st_mtime) > 2 * 86400
    path = materialize_polars(name, _build, force=force or stale)
    return pl.read_parquet(path)


def _ohlc_df(rows) -> pl.DataFrame:
    return pl.DataFrame(
        {
            'symbol': [r[0] for r in rows],
            'date':   [r[1] for r in rows],
            'open':   [float(r[2]) if r[2] is not None else None for r in rows],
            'high':   [float(r[3]) if r[3] is not None else None for r in rows],
            'low':    [float(r[4]) if r[4] is not None else None for r in rows],
            'close':  [float(r[5]) if r[5] is not None else None for r in rows],
        }
    ).sort(['symbol', 'date'])


def load_signals(vid: int, qual: int, force: bool, smoke: bool) -> pl.DataFrame:
    from database.bulk_cache import materialize_polars, chunked_query_by_year

    min_date = (dt.date.today() - dt.timedelta(days=int(LOOKBACK_YEARS * 365.25))).isoformat()

    if smoke:
        from database.trader_database import DB
        syms = ", ".join(f"'{s}'" for s in SMOKE_SYMS)
        cur = DB.execute_sql(
            "SELECT symbol, date, overall FROM scores "
            f"WHERE version_id={vid} AND overall >= {qual} "
            f"AND date >= '{min_date}' AND symbol IN ({syms})"
        )
        rows = cur.fetchall()
        return _sig_df(rows)

    def _build():
        rows = chunked_query_by_year(
            "SELECT symbol, date, overall FROM scores "
            "WHERE version_id={vid} AND overall >= {qual} "
            "AND date BETWEEN '{y_start}' AND '{y_end}' AND date >= '{min_date}'",
            start_year=int(min_date[:4]), end_year=dt.date.today().year + 1,
            vid=vid, qual=qual, min_date=min_date,
        )
        return _sig_df(rows)

    path = materialize_polars(f'entry_timing_signals_v{vid}_q{qual}_5y', _build, force=force)
    return pl.read_parquet(path)


def _sig_df(rows) -> pl.DataFrame:
    return pl.DataFrame(
        {
            'symbol':  [r[0] for r in rows],
            'date':    [r[1] for r in rows],
            'overall': [int(r[2]) for r in rows],
        }
    ).sort(['symbol', 'date'])


# ---------------------------------------------------------------- walks

def walk(highs, lows, start, end, tp_level, sl_level):
    """Walk bars [start, end] inclusive. Returns outcome code."""
    if end >= len(highs):
        return UNRESOLVED
    for i in range(start, end + 1):
        hit_tp = highs[i] >= tp_level
        hit_sl = sl_level is not None and lows[i] <= sl_level
        if hit_tp and hit_sl:
            return TIE
        if hit_tp:
            return TP
        if hit_sl:
            return SL
    return EXPIRE


def build_signal_table(ohlc: pl.DataFrame, sigs: pl.DataFrame) -> pl.DataFrame:
    out = {k: [] for k in (
        'symbol', 'date', 'overall', 'tier', 'sigma_pct', 'close_d', 'open_d1',
        'gap_raw', 'gap_sig',
    )}
    for b in BARRIERS:
        out[f'c_{b}'] = []
        out[f'n_{b}'] = []

    sig_by_sym = {s: g for (s,), g in sigs.group_by('symbol')}
    n_skipped_nodata = 0

    for (sym,), g in ohlc.group_by('symbol'):
        if sym not in sig_by_sym:
            continue
        g = g.sort('date')
        dates = g['date'].to_list()
        idx_of = {d: i for i, d in enumerate(dates)}
        op = g['open'].to_numpy().astype(np.float64)
        hi = g['high'].to_numpy().astype(np.float64)
        lo = g['low'].to_numpy().astype(np.float64)
        cl = g['close'].to_numpy().astype(np.float64)

        # 60-bar realized daily vol (fraction), trailing inclusive of signal bar
        with np.errstate(divide='ignore', invalid='ignore'):
            rets = np.diff(cl) / cl[:-1]
        sigma = np.full(len(cl), np.nan)
        if len(rets) >= SIGMA_WIN:
            # rolling std over the last SIGMA_WIN returns ending at bar i
            csum = np.cumsum(np.insert(rets, 0, 0.0))
            csum2 = np.cumsum(np.insert(rets ** 2, 0, 0.0))
            for i in range(SIGMA_WIN, len(rets) + 1):
                n = SIGMA_WIN
                s1 = csum[i] - csum[i - n]
                s2 = csum2[i] - csum2[i - n]
                var = max((s2 - s1 * s1 / n) / (n - 1), 0.0)
                sigma[i] = math.sqrt(var)   # sigma at bar index i (uses rets up to bar i)

        for row in sig_by_sym[sym].iter_rows(named=True):
            d = row['date']
            i = idx_of.get(d)
            if i is None or i + 1 >= len(cl):
                n_skipped_nodata += 1
                continue
            sg = sigma[i]
            entry_c = cl[i]
            entry_n = op[i + 1]
            if not (np.isfinite(sg) and sg > 0 and entry_c > 0 and np.isfinite(entry_n) and entry_n > 0):
                n_skipped_nodata += 1
                continue
            gap_raw = entry_n / entry_c - 1.0
            ov = row['overall']
            tier = '85+' if ov >= 85 else ('80-84' if ov >= 80 else '75-79')

            out['symbol'].append(sym)
            out['date'].append(d)
            out['overall'].append(ov)
            out['tier'].append(tier)
            out['sigma_pct'].append(sg * 100.0)
            out['close_d'].append(entry_c)
            out['open_d1'].append(entry_n)
            out['gap_raw'].append(gap_raw)
            out['gap_sig'].append(gap_raw / sg)

            for bname, bp in BARRIERS.items():
                w = bp['w']
                end = i + w
                tp_c = entry_c * (1.0 + bp['tp'] * sg)
                sl_c = entry_c * (1.0 - bp['sl'] * sg) if bp['sl'] else None
                tp_n = entry_n * (1.0 + bp['tp'] * sg)
                sl_n = entry_n * (1.0 - bp['sl'] * sg) if bp['sl'] else None
                out[f'c_{bname}'].append(walk(hi, lo, i + 1, end, tp_c, sl_c))
                out[f'n_{bname}'].append(walk(hi, lo, i + 1, end, tp_n, sl_n))

    print(f"signals skipped (no price/sigma/next-bar): {n_skipped_nodata}")
    return pl.DataFrame(out)


# ---------------------------------------------------------------- analysis

def wr(codes: np.ndarray) -> tuple[float, int]:
    m = codes != UNRESOLVED
    n = int(m.sum())
    if n == 0:
        return float('nan'), 0
    return float((codes[m] == TP).mean() * 100.0), n


def analyze(df: pl.DataFrame, vid: int, qual: int) -> dict:
    res = {'vid': vid, 'qual': qual, 'n_signals': len(df),
           'generated_at': dt.datetime.now().isoformat(timespec='seconds')}
    gap = df['gap_sig'].to_numpy()
    gap_raw = df['gap_raw'].to_numpy()
    n = len(gap_raw)
    t = float(gap_raw.mean() / (gap_raw.std(ddof=1) / math.sqrt(n))) if n > 1 else float('nan')
    res['gap'] = {'mean_bps': float(gap_raw.mean() * 1e4), 't': t,
                  'mean_sig': float(gap.mean()),
                  'pct_gap_down': float((gap_raw < 0).mean() * 100)}
    print(f"\n== Phase A — v{vid} calls >= {qual}, {len(df):,} signals ==")
    print(f"overnight gap: mean {res['gap']['mean_bps']:+.1f} bps (t={t:+.2f}), "
          f"{res['gap']['pct_gap_down']:.1f}% gap down")

    # --- C vs N, paired on jointly-resolved signals, per barrier ---
    res['c_vs_n'] = {}
    for b in BARRIERS:
        c = df[f'c_{b}'].to_numpy()
        nn = df[f'n_{b}'].to_numpy()
        m = (c != UNRESOLVED) & (nn != UNRESOLVED)
        cw, _ = wr(c[m]); nw, npair = wr(nn[m])
        tie_c = float((c[m] == TIE).mean() * 100); tie_n = float((nn[m] == TIE).mean() * 100)
        exp_c = float((c[m] == EXPIRE).mean() * 100)
        res['c_vs_n'][b] = {'n': npair, 'C': cw, 'N': nw, 'delta_pp': nw - cw,
                            'tie_C': tie_c, 'tie_N': tie_n, 'expire_C': exp_c}
        print(f"\n[{b}]  paired n={npair:,}")
        print(f"  C (close anchor)   WR {cw:6.2f}%   tie {tie_c:.2f}%  expire {exp_c:.2f}%")
        print(f"  N (next open)      WR {nw:6.2f}%   tie {tie_n:.2f}%")
        print(f"  delta N-C          {nw - cw:+.2f}pp")

    # --- per-year sign consistency on the primary barrier ---
    prim = 'apexW15'
    c = df[f'c_{prim}'].to_numpy(); nn = df[f'n_{prim}'].to_numpy()
    years = np.array([d.year for d in df['date'].to_list()])
    res['per_year'] = {}
    print(f"\n[{prim}] per-year C vs N:")
    for yr in sorted(set(years.tolist())):
        m = (years == yr) & (c != UNRESOLVED) & (nn != UNRESOLVED)
        if m.sum() < 30:
            continue
        cw, _ = wr(c[m]); nw, ny = wr(nn[m])
        res['per_year'][int(yr)] = {'n': ny, 'C': cw, 'N': nw, 'delta_pp': nw - cw}
        print(f"  {yr}  n={ny:5d}   C {cw:6.2f}%   N {nw:6.2f}%   d {nw-cw:+.2f}pp")

    # --- per-tier ---
    res['per_tier'] = {}
    tiers = df['tier'].to_numpy()
    print(f"\n[{prim}] per-tier C vs N:")
    for tname in ('75-79', '80-84', '85+'):
        m = (tiers == tname) & (c != UNRESOLVED) & (nn != UNRESOLVED)
        if m.sum() < 30:
            continue
        cw, _ = wr(c[m]); nw, nt = wr(nn[m])
        res['per_tier'][tname] = {'n': nt, 'C': cw, 'N': nw, 'delta_pp': nw - cw}
        print(f"  {tname:6s} n={nt:5d}   C {cw:6.2f}%   N {nw:6.2f}%   d {nw-cw:+.2f}pp")

    # --- gap-filtered next-open policies (the "mix") on the primary barrier ---
    filters = {
        'N_all':         np.ones(len(df), dtype=bool),
        'N_nogapdn00':   gap >= 0.0,
        'N_nogapdn025':  gap >= -0.25,
        'N_nogapdn050':  gap >= -0.50,
        'N_nochase05':   gap <= 0.50,
        'N_nochase10':   gap <= 1.00,
        'N_band':        (gap >= -0.25) & (gap <= 1.00),
    }
    resolved = (c != UNRESOLVED) & (nn != UNRESOLVED)
    res['filters'] = {}
    print(f"\n[{prim}] gap-filtered next-open policies "
          f"(entered: N-anchor WR vs C-anchor WR on the same subset; skipped: C-anchor WR):")
    print(f"  {'policy':14s} {'enter%':>7s} {'n':>6s} {'N-WR':>7s} {'C-WR(same)':>10s} "
          f"{'entryFx':>8s} {'C-WR(skip)':>10s}")
    for fname, fm in filters.items():
        ent = fm & resolved
        skp = (~fm) & resolved
        nw, ne = wr(nn[ent])
        cw_same, _ = wr(c[ent])
        cw_skip, ns = wr(c[skp])
        res['filters'][fname] = {
            'entered_pct': float(ent.sum() / max(resolved.sum(), 1) * 100),
            'n_entered': ne, 'N_wr': nw, 'C_wr_same': cw_same,
            'entry_effect_pp': nw - cw_same,
            'C_wr_skipped': cw_skip, 'n_skipped': ns,
        }
        print(f"  {fname:14s} {res['filters'][fname]['entered_pct']:6.1f}% {ne:6d} "
              f"{nw:6.2f}% {cw_same:9.2f}% {nw - cw_same:+7.2f}pp "
              f"{(f'{cw_skip:9.2f}%' if ns else '      n/a')}")

    return res


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--smoke', action='store_true', help='10-symbol foreground smoke')
    ap.add_argument('--force', action='store_true', help='rebuild parquet caches')
    ap.add_argument('--vid', type=int, default=None)
    ap.add_argument('--qual', type=int, default=75)
    args = ap.parse_args()

    t0 = time.perf_counter()
    vid = _resolve_vid(args.vid)

    # holdout convention (lock currently disabled => no-op, asserted defensively)
    try:
        import strategy_config
        cutoff = getattr(strategy_config, 'CALIBRATION_CUTOFF_DATE', None)
        print(f"CALIBRATION_CUTOFF_DATE = {cutoff!r}"
              + ("" if cutoff is None else "  (NOTE: holdout active — results pre-cutoff only)"))
    except Exception as e:  # pragma: no cover
        print(f"holdout check skipped: {e}")

    sigs = load_signals(vid, args.qual, args.force, args.smoke)
    print(f"signals loaded: {len(sigs):,}")
    ohlc = load_ohlc(args.force, args.smoke)
    print(f"ohlc rows: {len(ohlc):,}  symbols: {ohlc['symbol'].n_unique()}")

    df = build_signal_table(ohlc, sigs)
    tag = 'smoke' if args.smoke else 'full'
    df.write_parquet(OUT / f'phase_a_signals_{tag}.parquet')
    print(f"signal table: {len(df):,} rows -> out/phase_a_signals_{tag}.parquet")

    res = analyze(df, vid, args.qual)
    res['runtime_s'] = round(time.perf_counter() - t0, 1)
    (OUT / f'phase_a_results_{tag}.json').write_text(json.dumps(res, indent=2))
    print(f"\nresults -> out/phase_a_results_{tag}.json   ({res['runtime_s']}s)")


if __name__ == '__main__':
    main()
