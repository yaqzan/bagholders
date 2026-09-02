"""H5 gate — Multi-window sign-consistency check for WVD ship candidates.

Loads the v44 parquet, slices by signal date into 1y / 3y / 5y / 10y windows,
applies the chosen WVD parameters, and reports per-tier ΔWR for each window.
Sign-consistent means same direction (positive lift) across all windows.

Tests two variants per side: top-1 from sweep + a smoother "milder" variant.
"""
from __future__ import annotations
import io, sys, time
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import polars as pl

import os
_VID = os.environ.get('WVD_VID', '45')
CALLS_PARQ = ROOT / '.cache' / 'weekly_volume' / f'calls_v{_VID}_1825.parquet'
PUTS_PARQ  = ROOT / '.cache' / 'weekly_volume' / f'puts_v{_VID}_1825.parquet'

sys.path.insert(0, str(ROOT / 'experiments' / 'weekly_volume'))
from sweep import apply_wvd_calls, apply_wvd_puts, cumulative_wr_n, CALL_TIERS, PUT_TIERS


def evaluate_window(df, params, side, score_col_base='overall', score_col_new='new_overall'):
    """Apply WVD params and return per-tier WR delta."""
    if side == 'call':
        new_df = apply_wvd_calls(df,
            params['gate_lo'], params['gate_hi'], params['k_call'],
            params['k_power'], params['force_sat'], params['target_call'])
        tiers = CALL_TIERS
        sym = '+'
    else:
        new_df = apply_wvd_puts(df,
            params['put_lo'], params['put_hi'], params['k_put'],
            params['k_put_power'], params['cmf_sat'], params['target_put'])
        tiers = PUT_TIERS
        sym = '<'

    out = {}
    for t in tiers:
        base_wr, base_n = cumulative_wr_n(df, side, score_col_base, t)
        new_wr, new_n = cumulative_wr_n(new_df, side, score_col_new, t)
        d_wr = (new_wr - base_wr) if (new_wr is not None and base_wr is not None) else None
        d_n_pct = ((new_n - base_n) / base_n * 100) if base_n else None
        out[t] = {
            'base_wr': base_wr, 'new_wr': new_wr,
            'base_n': base_n,   'new_n': new_n,
            'd_wr': d_wr,        'd_n_pct': d_n_pct,
        }
    return out, sym


def slice_by_window(df: pl.DataFrame, days: int):
    cutoff_date = date.today() - timedelta(days=days)
    cutoff_str = cutoff_date.isoformat()
    return df.filter(pl.col('date') >= cutoff_str)


def report_window(label, results, sym, tiers):
    print(f'  {label:<8s}', end='')
    for t in tiers:
        r = results.get(t, {})
        d_wr = r.get('d_wr')
        d_n = r.get('d_n_pct')
        if d_wr is None:
            print(f'   {"N/A":<14s}', end='')
        else:
            tag = '+' if d_wr >= 0 else '-'
            print(f'   {sym}{t}:{d_wr:>+5.2f}/{d_n:>+5.1f}%', end='')
    print()


def main():
    calls = pl.read_parquet(CALLS_PARQ).filter(pl.col('opt_result_15').is_not_null())
    puts  = pl.read_parquet(PUTS_PARQ).filter(pl.col('opt_result_15').is_not_null())
    print(f'[setup] resolved peaks: calls={len(calls):,}  puts={len(puts):,}\n')

    # Two call candidates
    call_top = {
        'name': 'WVD-Call-Top  (gl70_gh95_k0.20_p1.0_fs0.15_t60)',
        'gate_lo': 70, 'gate_hi': 95, 'k_call': 0.20,
        'k_power': 1.0, 'force_sat': 0.15, 'target_call': 60.0,
    }
    call_mild = {
        'name': 'WVD-Call-Mild (gl75_gh90_k0.15_p1.5_fs0.15_t62)',
        'gate_lo': 75, 'gate_hi': 90, 'k_call': 0.15,
        'k_power': 1.5, 'force_sat': 0.15, 'target_call': 62.0,
    }

    # Two put candidates
    put_top = {
        'name': 'WVD-Put-Top  (pl5_ph25_k0.50_p1.0_cs0.30_t33)',
        'put_lo': 5, 'put_hi': 25, 'k_put': 0.50,
        'k_put_power': 1.0, 'cmf_sat': 0.30, 'target_put': 33.0,
    }
    put_mild = {
        'name': 'WVD-Put-Mild (pl10_ph22_k0.30_p1.5_cs0.30_t30)',
        'put_lo': 10, 'put_hi': 22, 'k_put': 0.30,
        'k_put_power': 1.5, 'cmf_sat': 0.30, 'target_put': 30.0,
    }

    # Multi-window: 1y, 3y, 5y
    windows = [('1y', 365), ('3y', 1095), ('5y', 1825)]

    for cand in [call_top, call_mild]:
        print('=' * 130)
        print(f'  {cand["name"]}')
        print('=' * 130)
        print(f'  {"window":<8s}', end='')
        for t in [75, 80, 85, 90, 95]:
            print(f'   +{t}:ΔWR/ΔN     ', end='')
        print()
        print('  ' + '-' * 90)
        for label, days in windows:
            sub = slice_by_window(calls, days)
            results, sym = evaluate_window(sub, cand, 'call')
            # Filter to tiers we want to report
            filtered = {t: results[t] for t in [75, 80, 85, 90, 95] if t in results}
            report_window(label, filtered, sym, [75, 80, 85, 90, 95])
        # Sign consistency check
        sign_ok = {}
        for t in [75, 80, 85, 90, 95]:
            signs = []
            for label, days in windows:
                sub = slice_by_window(calls, days)
                results, _ = evaluate_window(sub, cand, 'call')
                d_wr = results.get(t, {}).get('d_wr')
                if d_wr is not None:
                    signs.append(1 if d_wr > 0 else (-1 if d_wr < 0 else 0))
            if signs and len(set(signs)) == 1 and signs[0] == 1:
                sign_ok[t] = '✓ all positive'
            elif signs and len(set(signs)) == 1 and signs[0] == -1:
                sign_ok[t] = '✗ all negative'
            elif signs and any(s > 0 for s in signs) and any(s < 0 for s in signs):
                sign_ok[t] = '⚠ mixed'
            else:
                sign_ok[t] = signs
        print('  ' + '-' * 90)
        print(f'  H5 sign-consistency: {sign_ok}')
        print()

    for cand in [put_top, put_mild]:
        print('=' * 130)
        print(f'  {cand["name"]}')
        print('=' * 130)
        print(f'  {"window":<8s}', end='')
        for t in [25, 20, 15, 10]:
            print(f'   <{t}:ΔWR/ΔN     ', end='')
        print()
        print('  ' + '-' * 90)
        for label, days in windows:
            sub = slice_by_window(puts, days)
            results, sym = evaluate_window(sub, cand, 'put')
            filtered = {t: results[t] for t in [25, 20, 15, 10] if t in results}
            report_window(label, filtered, sym, [25, 20, 15, 10])
        sign_ok = {}
        for t in [25, 20, 15, 10]:
            signs = []
            for label, days in windows:
                sub = slice_by_window(puts, days)
                results, _ = evaluate_window(sub, cand, 'put')
                d_wr = results.get(t, {}).get('d_wr')
                if d_wr is not None:
                    signs.append(1 if d_wr > 0 else (-1 if d_wr < 0 else 0))
            if signs and len(set(signs)) == 1 and signs[0] == 1:
                sign_ok[t] = '✓ all positive'
            elif signs and len(set(signs)) == 1 and signs[0] == -1:
                sign_ok[t] = '✗ all negative'
            elif signs and any(s > 0 for s in signs) and any(s < 0 for s in signs):
                sign_ok[t] = '⚠ mixed'
            else:
                sign_ok[t] = signs
        print('  ' + '-' * 90)
        print(f'  H5 sign-consistency: {sign_ok}')
        print()


if __name__ == '__main__':
    main()
