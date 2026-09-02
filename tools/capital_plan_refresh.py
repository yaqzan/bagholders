#!/usr/bin/env python
"""tools/capital_plan_refresh.py -- fast, offline-capable refresh for the
owner's capital plan (.claude/docs/capital-plan-2026.md, which this tool
never edits).

On every run:
  1. Recomputes the plan's deterministic debt/net-worth scenario table from
     a params JSON (.claude/docs/capital_plan_params.json).
  2. Harvests the latest shipped-config evidence (canon paired-2x reads +
     calibrated Monte Carlo reads) from known experiment output files.
  3. Reads the live portfolio ledger from the local Flask API, best-effort.
  4. Writes a generated markdown companion doc
     (.claude/docs/capital-plan-2026-scenarios.md).
  5. Compares key verdict facts vs the previous run
     (.cache/capital_plan/last_verdict.json) and prints a VERDICT DELTA
     banner if anything flipped.

Stdlib only. No peewee, no MySQL, no network beyond one best-effort
localhost probe. Target runtime <15s.

Traps this tool deliberately avoids (see CLAUDE.md / traps.md):
  - Connects to the API via 127.0.0.1, NEVER localhost -- on this box
    'localhost' resolves IPv6-first against an IPv4-only Flask bind, which
    burns a double timeout on every dead-service probe.
  - All console output is kept ASCII-only (see to_ascii()) -- the Windows
    cp1252 console raises UnicodeEncodeError on em-dashes/arrows that show
    up verbatim in this repo's experiment docs/CSVs, so any text sourced
    from a file is sanitized before it reaches print().
  - Only reads the exact file paths listed in the params JSON -- no
    glob'ing of experiment trees, to keep runtime bounded and predictable.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PARAMS_PATH = REPO_ROOT / '.claude' / 'docs' / 'capital_plan_params.json'
SCENARIOS_DOC_PATH = REPO_ROOT / '.claude' / 'docs' / 'capital-plan-2026-scenarios.md'
STATE_DIR = REPO_ROOT / '.cache' / 'capital_plan'
STATE_PATH = STATE_DIR / 'last_verdict.json'
PROFILES_JSON_PATH = REPO_ROOT / 'algorithm_versions' / 'portfolio_profiles.json'
STRATEGY_CONFIG_PATH = REPO_ROOT / 'strategy_config.py'

REQUIRED_PARAM_FIELDS = [
    'as_of', 'debt_balance_cad', 'debt_apr', 'monthly_surplus_cad',
    'portfolio_cash_cad', 'seed_target_cad', 'hisa_apr', 'g_tiers_annual',
    'horizon_months', 'gate_dates', 'evidence_sources',
]

# Anchors from the ratified plan's table (.claude/docs/capital-plan-2026.md,
# computed 2026-08-09). "Small start differences are noise" per the owner's
# own note -- hence the +/-2% band rather than an exact match.
ANCHORS_NW_G0 = {'A': 248204.0, 'B': 247287.0, 'C': 245477.0, 'D': 223762.0}
ANCHOR_NW_TOL_PCT = 0.02
ANCHOR_PAYOFF_MONTHS = 11
ANCHOR_PAYOFF_INTEREST = 2636.0
ANCHOR_PAYOFF_INTEREST_TOL = 75.0

EPS = 1e-9

_ASCII_MAP = {
    '—': '--', '–': '-', '→': '->', '←': '<-',
    '‘': "'", '’': "'", '“': '"', '”': '"',
    '×': 'x', '≥': '>=', '≤': '<=', '≈': '~',
    'σ': 'sigma', '±': '+/-',
}


def to_ascii(s):
    """Sanitize file-sourced text before it reaches a console print() --
    this repo's docs/CSVs carry em-dashes, arrows, and (in at least one
    JSON field) mojibake that raise UnicodeEncodeError on a cp1252
    console. Known punctuation is mapped to an ASCII equivalent first;
    anything left over is replaced with '?' rather than crashing."""
    if s is None:
        return ''
    s = str(s)
    for k, v in _ASCII_MAP.items():
        s = s.replace(k, v)
    return s.encode('ascii', 'replace').decode('ascii')


def safe_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_pct(s):
    if s is None:
        return None
    s = str(s).strip().rstrip('%')
    try:
        return float(s)
    except ValueError:
        return None


def parse_num(s):
    if s is None:
        return None
    try:
        return float(str(s).strip())
    except ValueError:
        return None


def fmt_pct(v, digits=0):
    if v is None:
        return 'n/a'
    return f"{v * 100:+.{digits}f}%"


def fmt_cad(v):
    if v is None:
        return 'n/a'
    return f"{v:,.0f}"


def fmt_triplet(w):
    """med / worst-DD / collapse, all as percentages, from a harvested row."""
    if not w:
        return 'n/a'
    med = w.get('med_ret_pct')
    dd = w.get('worst_dd_pct')
    coll = w.get('p_coll_pct')
    if med is None or dd is None:
        return 'n/a'
    return f"{med:+.1f}% / {dd:.1f}% DD / {(coll or 0):.1f}% coll"


def days_in_month(year, month):
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


def add_months(d, n):
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, days_in_month(year, month))
    return date(year, month, day)


# ─── Part 1: deterministic scenario table ────────────────────────────────

def simulate_scenario(scenario, params, g_annual):
    """Month grid 0..horizon_months. Month 0 is the start state (no
    accrual). For months 1..horizon_months: interest/HISA/portfolio growth
    accrues on the PRIOR month-end balance first, then that month's surplus
    is applied per the scenario's waterfall (payment-after-interest -- this
    is the convention the scenario-A anchor (11 payments, ~$2,636 interest)
    validates; see the tool's own anchor self-check output).

    A leftover from one stage's waterfall in the SAME month it clears
    (e.g. scenario A's debt fully paid off with surplus to spare) rolls
    forward into the next stage immediately rather than waiting a month --
    this is what makes scenario B's "immediate redraw" and the mid-month
    stage transitions in A/C behave sensibly.
    """
    debt_apr = float(params['debt_apr'])
    hisa_apr = float(params['hisa_apr'])
    surplus = float(params['monthly_surplus_cad'])
    seed_target = float(params['seed_target_cad'])
    c_threshold = float(params.get('scenario_c_deploy_threshold_cad', 20000))
    horizon = int(params['horizon_months'])

    debt = float(params['debt_balance_cad'])
    cash = 0.0
    portfolio = float(params['portfolio_cash_cad'])

    g_month = (1.0 + g_annual) ** (1.0 / 12.0)
    debt_r = debt_apr / 12.0
    hisa_r = hisa_apr / 12.0

    payoff_month = None      # month debt permanently reaches 0 (None = never, e.g. D)
    deploy_month = None      # month new capital first starts compounding in portfolio
    total_interest = 0.0

    stage = 0                # generic stage counter (meaning is scenario-specific)
    debt_zero = False        # scenario C debt-leg done
    cash_deployed = False    # scenario C cash-leg done

    nw_month36 = None
    nw_horizon = None

    for m in range(1, horizon + 1):
        interest_this_month = debt * debt_r
        debt += interest_this_month
        total_interest += interest_this_month
        cash *= (1.0 + hisa_r)
        portfolio *= g_month

        if scenario == 'A':
            remaining = surplus
            if debt > EPS:
                pay = min(remaining, debt)
                debt -= pay
                remaining -= pay
                if debt <= EPS:
                    debt = 0.0
                    if payoff_month is None:
                        payoff_month = m
            if remaining > EPS and debt <= EPS:
                if deploy_month is None:
                    need = max(0.0, seed_target - cash)
                    add = min(remaining, need)
                    cash += add
                    remaining -= add
                    if cash >= seed_target - EPS:
                        portfolio += cash
                        cash = 0.0
                        deploy_month = m
                if deploy_month is not None and remaining > EPS:
                    portfolio += remaining
                    remaining = 0.0

        elif scenario == 'B':
            remaining = surplus
            if stage < 2 and debt > EPS:
                pay = min(remaining, debt)
                debt -= pay
                remaining -= pay
                if debt <= EPS:
                    debt = 0.0
                    if stage == 0:
                        # Original debt cleared -> redraw seed_target immediately
                        # (NW-neutral wash: new liability == new asset this month).
                        if deploy_month is None:
                            deploy_month = m
                        debt = seed_target
                        portfolio += seed_target
                        stage = 1
                        if remaining > EPS:
                            pay2 = min(remaining, debt)
                            debt -= pay2
                            remaining -= pay2
                            if debt <= EPS:
                                debt = 0.0
                                payoff_month = m
                                stage = 2
                    elif stage == 1:
                        payoff_month = m
                        stage = 2
            if stage == 2 and remaining > EPS:
                portfolio += remaining
                remaining = 0.0

        elif scenario == 'C':
            half = surplus / 2.0
            # Debt-half stream: half surplus -> debt until 0, then that
            # half's stream redirects straight to portfolio.
            if not debt_zero:
                pay = min(half, debt)
                debt -= pay
                leftover_debt = half - pay
                if debt <= EPS:
                    debt = 0.0
                    debt_zero = True
                    if payoff_month is None:
                        payoff_month = m
                if debt_zero and leftover_debt > EPS:
                    portfolio += leftover_debt
            else:
                portfolio += half
            # Cash-half stream: half surplus -> HISA until threshold, then
            # sweep to portfolio and redirect that half's stream too.
            if not cash_deployed:
                need = max(0.0, c_threshold - cash)
                add = min(half, need)
                cash += add
                leftover_cash = half - add
                if cash >= c_threshold - EPS:
                    portfolio += cash
                    cash = 0.0
                    cash_deployed = True
                    if deploy_month is None:
                        deploy_month = m
                    if leftover_cash > EPS:
                        portfolio += leftover_cash
            else:
                portfolio += half

        elif scenario == 'D':
            # Interest-only: debt balance never changes (this month's
            # interest accrual above is reverted and instead paid out of
            # surplus); the rest invests from month 0.
            debt -= interest_this_month
            contribution = surplus - interest_this_month
            portfolio += contribution
            if deploy_month is None:
                deploy_month = m
            payoff_month = None

        if m == 36:
            nw_month36 = portfolio + cash - debt
        if m == horizon:
            nw_horizon = portfolio + cash - debt

    return {
        'nw_month36': nw_month36,
        'nw_horizon': nw_horizon,
        'payoff_month': payoff_month,
        'deploy_month': deploy_month,
        'total_interest': total_interest,
        'end_debt': debt,
        'end_cash': cash,
        'end_portfolio': portfolio,
    }


def build_scenario_table(params):
    g_tiers = params['g_tiers_annual']
    as_of = datetime.strptime(params['as_of'], '%Y-%m-%d').date()
    results_by_g = {}
    for g in g_tiers:
        results_by_g[g] = {}
        for scen in ('A', 'B', 'C', 'D'):
            r = simulate_scenario(scen, params, g)
            r['payoff_date'] = add_months(as_of, r['payoff_month']).strftime('%b %Y') if r['payoff_month'] else None
            r['deploy_date'] = add_months(as_of, r['deploy_month']).strftime('%b %Y') if r['deploy_month'] else None
            results_by_g[g][scen] = r
    ranking = {}
    for g in g_tiers:
        ranking[g] = sorted(('A', 'B', 'C', 'D'), key=lambda s: results_by_g[g][s]['nw_horizon'], reverse=True)
    return {'results_by_g': results_by_g, 'ranking': ranking, 'g_tiers': g_tiers}


def run_anchor_check(results_g0):
    rows = []
    all_pass = True
    for scen in ('A', 'B', 'C', 'D'):
        target = ANCHORS_NW_G0[scen]
        actual = results_g0[scen]['nw_horizon']
        diff_pct = (actual - target) / target
        ok = abs(diff_pct) <= ANCHOR_NW_TOL_PCT
        all_pass = all_pass and ok
        rows.append({'scenario': scen, 'target': target, 'actual': actual, 'diff_pct': diff_pct, 'ok': ok})
    a = results_g0['A']
    payoff_ok = (a['payoff_month'] == ANCHOR_PAYOFF_MONTHS)
    interest_ok = abs(a['total_interest'] - ANCHOR_PAYOFF_INTEREST) <= ANCHOR_PAYOFF_INTEREST_TOL
    all_pass = all_pass and payoff_ok and interest_ok
    return {
        'rows': rows, 'payoff_ok': payoff_ok, 'interest_ok': interest_ok,
        'a_payoff_month': a['payoff_month'], 'a_total_interest': a['total_interest'],
        'all_pass': all_pass,
    }


# ─── Part 2: evidence harvest ─────────────────────────────────────────────

def load_strategy_config_fallback():
    """Non-fatal: only used when a profile doesn't carry its own
    tp_base/sl_base (Core and Sentinel currently don't -- they inherit
    STRATEGY_30DTE.option). strategy_config.py is pure stdlib (dataclasses
    + constants, no DB import), so this is a plain, fast module import."""
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        import strategy_config as _sc
        return {
            'tp_base': _sc.STRATEGY_30DTE.option.TP_BASE,
            'sl_base': _sc.STRATEGY_30DTE.option.SL_BASE,
        }
    except Exception as exc:
        print(f"  warning: could not import strategy_config.py for TP/SL fallback: {to_ascii(exc)}")
        return {'tp_base': None, 'sl_base': None}


def extract_profiles(profiles_json_path):
    """Reads algorithm_versions/portfolio_profiles.json. Trusts the JSON's
    actual field names (per spec) rather than assuming a fixed shape;
    missing fields degrade to None/'?' downstream instead of raising."""
    with open(profiles_json_path, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    fallback = load_strategy_config_fallback()
    out = {}
    for p in data.get('profiles', []):
        key = p.get('key')
        if not key:
            continue
        params_blk = p.get('params', {}) or {}
        tp_in_profile = 'tp_base' in params_blk
        sl_in_profile = 'sl_base' in params_blk
        tp_base = params_blk.get('tp_base', fallback.get('tp_base'))
        sl_base = params_blk.get('sl_base', fallback.get('sl_base'))
        tiers = {t: params_blk.get(f'tier_{t}') for t in ('ultra', 'top', 'mid', 'low')}
        max_positions = params_blk.get('max_positions')
        vals = [v for v in tiers.values() if v is not None]
        flat = vals[0] if (vals and len(set(vals)) == 1 and vals[0]) else None
        sizing_cell_name = f"n{max_positions}_f{flat:.3f}" if (flat is not None and max_positions is not None) else None
        if flat is not None and max_positions is not None:
            sizing_desc = f"flat n{max_positions} x {flat * 100:.0f}%"
        else:
            sizing_desc = (f"cascade ultra/top/mid/low = {tiers['ultra']}/{tiers['top']}/{tiers['mid']}/{tiers['low']}")
        out[key] = {
            'key': key,
            'name': p.get('name', key),
            'version_tag': f"v{p['version']}" if 'version' in p else '?',
            'status': p.get('status'),
            'tp_base': tp_base,
            'sl_base': sl_base,
            'tp_source': 'profile' if tp_in_profile else 'strategy_config.OPT_30DTE fallback',
            'sl_source': 'profile' if sl_in_profile else 'strategy_config.OPT_30DTE fallback',
            'max_positions': max_positions,
            'tiers': tiers,
            'flat_frac': flat,
            'sizing_cell_name': sizing_cell_name,
            'sizing_desc': sizing_desc,
        }
    return out


def read_csv_rows(path):
    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            return list(csv.DictReader(f))
    except (FileNotFoundError, OSError):
        return None


def find_rows(rows, **filters):
    out = []
    for row in rows:
        ok = True
        for k, v in filters.items():
            cell = row.get(k)
            if isinstance(v, float):
                fv = safe_float(cell)
                if fv is None or abs(fv - v) > 1e-6:
                    ok = False
                    break
            else:
                if str(cell) != str(v):
                    ok = False
                    break
        if ok:
            out.append(row)
    return out


def find_rows_profile_lens(rows, profile, lens, tp=None, sl=None):
    """Match rows in a multi-profile/multi-lens CSV (profile/lens/window schema, e.g.
    flipR_r1.csv) by the file's own `profile` + `lens` columns, optionally confirming
    tp/sl against the shipped config. Deliberately does NOT filter by window -- callers
    get back every window row for the (profile, lens) pair, so a 12-window file doesn't
    need a hardcoded window list.

    Profile-first filtering matters: tp/sl alone is NOT a unique key across profiles in
    this file. Core and Sentinel both ship TP10/SL-100 (Sentinel has no tp_base/sl_base
    of its own and falls back to the same STRATEGY_30DTE.option as Core), so the
    existing `tp_sl` match_by would silently return CORE's rows for a SENTINEL lookup
    (whichever profile happens to sort first in the file) if pointed at this CSV."""
    out = []
    for row in rows:
        if str(row.get('profile')) != str(profile):
            continue
        if str(row.get('lens')) != str(lens):
            continue
        if tp is not None:
            rtp = safe_float(row.get('tp'))
            if rtp is None or abs(rtp - tp) > 1e-6:
                continue
        if sl is not None:
            rsl = safe_float(row.get('sl'))
            if rsl is None or abs(rsl - sl) > 1e-6:
                continue
        out.append(row)
    return out


def harvest_calibrated(shipped, sources, profile_key=None):
    results = []
    for src in sources:
        path = REPO_ROOT / src['path']
        rows = read_csv_rows(path)
        entry = {
            'path': src['path'], 'kind': src.get('kind', ''),
            'match_by': src.get('match_by', 'tp_sl'), 'found': False,
            'windows': {}, 'error': None,
        }
        if rows is None:
            entry['error'] = 'file not found or unreadable'
            results.append(entry)
            continue

        if entry['match_by'] == 'profile_lens':
            # profile/lens/window schema (e.g. flipR_r1.csv) -- pulls every window
            # present for (profile, lens) rather than the fixed 5y/22-now pair below.
            src_profile = src.get('profile', profile_key)
            lens = src.get('lens')
            if src_profile and lens:
                matched = find_rows_profile_lens(rows, src_profile, lens,
                                                  tp=shipped.get('tp'), sl=shipped.get('sl'))
                for r in matched:
                    window = r.get('window')
                    if not window or window in entry['windows']:
                        continue  # first match wins; guards a dup-key surprise
                    entry['windows'][window] = {
                        'med_ret_pct': safe_float(r.get('med_ret')),
                        'worst_dd_pct': safe_float(r.get('worst_dd')),
                        'p_coll_pct': safe_float(r.get('p_coll')),
                        'n_iter': r.get('n_iter'),
                    }
                entry['found'] = bool(entry['windows'])
            results.append(entry)
            continue

        for window in ('5y', '22-now'):
            matches = []
            if entry['match_by'] == 'cell_name' and shipped.get('cell_name'):
                matches = find_rows(rows, window=window, cell_name=shipped['cell_name'])
            elif entry['match_by'] == 'tp_sl' and shipped.get('tp') is not None and shipped.get('sl') is not None:
                matches = find_rows(rows, window=window, tp=shipped['tp'], sl=shipped['sl'])
            if matches:
                r = matches[0]
                entry['windows'][window] = {
                    'med_ret_pct': safe_float(r.get('med_ret')),
                    'worst_dd_pct': safe_float(r.get('worst_dd')),
                    'p_coll_pct': safe_float(r.get('p_coll')),
                    'n_iter': r.get('n_iter'),
                }
                entry['found'] = True
        results.append(entry)
    return results


def pick_primary(calib_list):
    """Prefer a fully shipped-matched reading (kind not ending '_only')
    over a barrier-only / sizing-unconfirmed secondary reading."""
    primary = [c for c in calib_list if c.get('found') and not c.get('kind', '').endswith('_only')]
    if primary:
        return primary[0]
    found_any = [c for c in calib_list if c.get('found')]
    return found_any[0] if found_any else None


def harvest_evidence(params, profiles):
    all_sources = params['evidence_sources']['calibrated_csvs']
    ev = {}
    for key, prof in profiles.items():
        shipped = {'tp': prof.get('tp_base'), 'sl': prof.get('sl_base'), 'cell_name': prof.get('sizing_cell_name')}
        profile_sources = [s for s in all_sources if s.get('profile') == key]
        calib_sources = [s for s in profile_sources if 'buf20' not in s.get('kind', '')]
        buf20_sources = [s for s in profile_sources if 'buf20' in s.get('kind', '')]
        calib_harvest = harvest_calibrated(shipped, calib_sources, profile_key=key)
        buf20_harvest = harvest_calibrated(shipped, buf20_sources, profile_key=key)
        ev[key] = {
            'shipped': shipped,
            'calibrated': calib_harvest,
            'buf20': buf20_harvest,
            'no_calibrated_read': not any(r['found'] for r in calib_harvest),
        }
    return ev


def parse_md_pipe_table(text):
    lines = [l for l in text.splitlines() if l.strip().startswith('|')]
    if len(lines) < 3:
        return None, None
    header = [c.strip() for c in lines[0].strip('|').split('|')]
    data_rows = []
    for l in lines[2:]:
        cells = [c.strip() for c in l.strip('|').split('|')]
        if len(cells) == len(header):
            data_rows.append(dict(zip(header, cells)))
    return header, data_rows


def harvest_paired2x(params, apex_profile):
    rel_path = params['evidence_sources'].get('paired2x_summary')
    if not rel_path:
        return {'found': False, 'error': 'no paired2x_summary configured'}
    path = REPO_ROOT / rel_path
    try:
        text = path.read_text(encoding='utf-8-sig', errors='replace')
    except (FileNotFoundError, OSError) as exc:
        return {'found': False, 'error': str(exc), 'path': rel_path}
    header, rows = parse_md_pipe_table(text)
    cell_name = (apex_profile or {}).get('sizing_cell_name')
    if header and rows and cell_name:
        for r in rows:
            if r.get('arm') == cell_name:
                return {
                    'found': True, 'path': rel_path, 'arm': r.get('arm'),
                    'p_2x_ever': parse_pct(r.get('P(2x ever)')),
                    'p_2x_before_dd': parse_pct(r.get('P(2x<50dd)')),
                    'p_collapse': parse_pct(r.get('p_collapse')),
                    'med_days_to_2x': parse_num(r.get('med days-to-2x')),
                    'med_compound': parse_pct(r.get('med compound')),
                    'worst_dd': parse_pct(r.get('worst DD')),
                }
    # Structured parse failed to find the shipped arm -- fall back to a
    # short verbatim excerpt per spec, rather than silently omitting it.
    lines = [l for l in text.splitlines() if l.strip()][:15]
    return {'found': False, 'path': rel_path, 'excerpt': '\n'.join(lines),
            'error': 'shipped sizing arm not found in parsed table'}


def p2x_bucket(p2x):
    if p2x is None:
        return 'unknown'
    if p2x >= 99.0:
        return '>=99%'
    if p2x >= 90.0:
        return '90-99%'
    return '<90%'


def harvest_ctsl_status(params):
    """Small, referenced-evidence pull from the CLOSED ctsl_vehicle_2026_08 campaign.
    NOT a shipped config -- no lever from that campaign survived survivorship and its
    own disposition is 'No ship' (FINDINGS.md). Cited in the scenarios doc only because
    capital-plan-2026.md and known-issues.md name CTSL/CT_PROMOTE as a candidate edge
    carrier -- this keeps the plan's evidence trail pointed at the (later, more honest)
    fill-fidelity and capacity findings, not just the earlier, since-revised
    index-competitive claim. Reads two small source cells, never the full 48/40-cell
    batteries -- this is a citation, not a table."""
    cfg = (params.get('evidence_sources') or {}).get('ctsl_status')
    if not cfg:
        return {'available': False}
    window = cfg.get('window', '22-now')
    result = {
        'available': False, 'window': window,
        'findings_path': cfg.get('findings_path'),
        'missp_path': cfg.get('missp_path'),
        'capacity_path': cfg.get('capacity_path'),
    }
    missp_rows = read_csv_rows(REPO_ROOT / cfg['missp_path']) if cfg.get('missp_path') else None
    cap_rows = read_csv_rows(REPO_ROOT / cfg['capacity_path']) if cfg.get('capacity_path') else None
    if not missp_rows or not cap_rows:
        result['error'] = 'ctsl evidence CSV(s) not found or unreadable'
        return result
    missp_row = next((r for r in missp_rows if r.get('window') == window), None)
    if missp_row is None:
        result['error'] = f"window '{window}' not found in {cfg.get('missp_path')}"
        return result
    cap_by_book = {}
    for r in cap_rows:
        if r.get('window') != window:
            continue
        book = r.get('book')
        if not book:
            continue
        cap_by_book[book] = {
            'pct_clip_capped': safe_float(r.get('pct_clip_capped')),
            'effective_trades_per_year': safe_float(r.get('effective_trades_per_year')),
        }
    result.update({
        'available': True,
        'never_fill': safe_float(missp_row.get('missp_joined_only')),
        'cap_by_book': cap_by_book,
    })
    return result


# ─── Part 3: live ledger (best-effort) ────────────────────────────────────

def discover_api_port(default=5000):
    try:
        import re
        text = (REPO_ROOT / 'api.py').read_text(encoding='utf-8', errors='ignore')
        m = re.search(r"app\.run\([^)]*port\s*=\s*(\d+)", text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return default


def compute_max_dd_from_curve(curve):
    peak = None
    max_dd = 0.0
    for pt in curve:
        eq = pt.get('equity_usd')
        if eq is None:
            continue
        if peak is None or eq > peak:
            peak = eq
        if peak:
            dd = (peak - eq) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def fetch_live_ledger(port, timeout=2.0):
    # NEVER 'localhost': it resolves IPv6-first against this box's
    # IPv4-only Flask bind, which burns a double timeout on a dead service.
    url = f'http://127.0.0.1:{port}/api/portfolio/state'
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError, OSError, ValueError) as exc:
        return {'available': False, 'note': f'live ledger: API unavailable ({type(exc).__name__})', 'url': url}
    try:
        run = data.get('run') or {}
        summary = data.get('summary') or {}
        profile = run.get('profile')
        equity = summary.get('equity_usd')
        if profile is None or equity is None:
            return {'available': False, 'note': 'live ledger: API reachable but profile/equity field missing', 'url': url}
        max_dd = summary.get('max_dd_pct')
        if not max_dd:
            max_dd = compute_max_dd_from_curve(data.get('equity_curve') or [])
        return {
            'available': True, 'profile': profile, 'equity_usd': equity,
            'start_capital_usd': run.get('starting_capital_usd'),
            'return_pct': summary.get('total_return_pct'),
            'max_dd_pct': max_dd, 'url': url,
        }
    except Exception:
        return {'available': False, 'note': 'live ledger: API reachable but response malformed', 'url': url}


# ─── Part 4: generated doc + verdict delta ────────────────────────────────

def build_verdict_facts(scenario_table, evidence, paired2x):
    facts = {}
    for key in ('core', 'apex', 'sentinel'):
        ev = evidence.get(key, {})
        primary = pick_primary(ev.get('calibrated', []))
        w5 = primary['windows'].get('5y') if primary else None
        sign = None
        collapse = None
        if w5 and w5.get('med_ret_pct') is not None:
            sign = 'neg' if w5['med_ret_pct'] < 0 else 'pos'
            collapse = 'nonzero' if (w5.get('p_coll_pct') or 0) > 0 else 'zero'
        facts[key] = {
            'calib_5y_sign': sign,
            'calib_5y_collapse': collapse,
            'no_calibrated_read': ev.get('no_calibrated_read', True),
        }
    g_tiers = scenario_table['g_tiers']
    facts['scenario_ranking_g0'] = scenario_table['ranking'].get(g_tiers[0])
    g30 = next((g for g in g_tiers if abs(g - 0.30) < 1e-9), None)
    facts['scenario_ranking_g30'] = scenario_table['ranking'].get(g30) if g30 is not None else None
    facts['apex_p2x_bucket'] = p2x_bucket(paired2x.get('p_2x_ever')) if paired2x.get('found') else 'unknown'
    return facts


def flatten_facts(facts):
    flat = {}
    for key in ('core', 'apex', 'sentinel'):
        for k, v in (facts.get(key) or {}).items():
            flat[f'{key}.{k}'] = v
    flat['scenario_ranking_g0'] = facts.get('scenario_ranking_g0')
    flat['scenario_ranking_g30'] = facts.get('scenario_ranking_g30')
    flat['apex_p2x_bucket'] = facts.get('apex_p2x_bucket')
    return flat


def diff_facts(old_facts, new_facts):
    old_flat = flatten_facts(old_facts or {})
    new_flat = flatten_facts(new_facts or {})
    diffs = []
    for k in sorted(set(old_flat) | set(new_flat)):
        ov, nv = old_flat.get(k), new_flat.get(k)
        if ov != nv:
            diffs.append((k, ov, nv))
    return diffs


def evidence_source_file_list(params):
    paths = [
        str(PROFILES_JSON_PATH.relative_to(REPO_ROOT)).replace('\\', '/'),
        str(STRATEGY_CONFIG_PATH.relative_to(REPO_ROOT)).replace('\\', '/'),
    ]
    for src in params['evidence_sources']['calibrated_csvs']:
        paths.append(src['path'])
    p2x = params['evidence_sources'].get('paired2x_summary')
    if p2x:
        paths.append(p2x)
    ctsl_cfg = params['evidence_sources'].get('ctsl_status') or {}
    for k in ('findings_path', 'missp_path', 'capacity_path'):
        if ctsl_cfg.get(k):
            paths.append(ctsl_cfg[k])
    out, seen = [], set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        full = REPO_ROOT / p
        try:
            mtime = datetime.fromtimestamp(full.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        except OSError:
            mtime = 'not found'
        out.append({'path': p, 'mtime': mtime})
    return out


SCENARIO_LABELS = {
    'A': 'A -- debt-first -> $25k cash seed -> deploy',
    'B': 'B -- debt-first -> LOC redraw at payoff',
    'C': 'C -- hybrid 2.5/2.5 -> deploy at $20k cash',
    'D': 'D -- interest-only, invest the rest',
}


def render_scenarios_doc(params, scenario_table, anchors, evidence, profiles, paired2x, ledger, port,
                          banner_lines, delta_status, facts, ctsl_status=None):
    now = datetime.now(timezone.utc)
    L = []
    L.append("# Capital Plan 2026 -- Scenario & Evidence Refresh")
    L.append("")
    L.append("**AUTO-GENERATED by `tools/capital_plan_refresh.py` -- do not hand-edit; run the tool to refresh.**")
    L.append("")
    L.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}  |  params as_of: {params.get('as_of')}")
    L.append("")
    L.append("Companion to [capital-plan-2026.md](capital-plan-2026.md) (the owner's hand-maintained "
              "narrative plan). This file is the mechanically-recomputed arithmetic + evidence layer -- "
              "if a VERDICT DELTA fires below, that's the signal to go update the narrative doc.")
    L.append("")

    if banner_lines:
        L.append("```")
        L.extend(banner_lines)
        L.append("```")
        L.append("")

    # --- Section 1: active-config table ---
    L.append("## 1. Active shipped configs")
    L.append("")
    L.append("| Profile | Version | TP | SL | Sizing | MaxPos | Calibrated 5y (med/DD/coll) | "
              "Calibrated 22-now (med/DD/coll) | buf20 5y (med/DD/coll) | Canon (Apex paired-2x) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for key in ('core', 'apex', 'sentinel'):
        prof = profiles.get(key, {})
        ev = evidence.get(key, {})
        if ev.get('no_calibrated_read'):
            calib5 = calib22 = "**NO CALIBRATED READ**"
        else:
            primary = pick_primary(ev.get('calibrated', []))
            calib5 = fmt_triplet(primary['windows'].get('5y')) if primary else 'n/a'
            calib22 = fmt_triplet(primary['windows'].get('22-now')) if primary else 'n/a'
        buf20_primary = pick_primary(ev.get('buf20', []))
        buf20_5 = fmt_triplet(buf20_primary['windows'].get('5y')) if buf20_primary else 'n/a'
        canon = 'n/a'
        if key == 'apex' and paired2x.get('found'):
            canon = (f"P(2x)={paired2x['p_2x_ever']:.1f}% worstDD={paired2x['worst_dd']:.1f}% "
                     f"days={paired2x['med_days_to_2x']:.0f} coll={paired2x['p_collapse']:.1f}%")
        elif key == 'apex':
            canon = 'NOT PARSED -- see excerpt below'
        L.append(f"| {prof.get('name', key)} | {prof.get('version_tag', '?')} | {fmt_pct(prof.get('tp_base'))} | "
                  f"{fmt_pct(prof.get('sl_base'))} | {to_ascii(prof.get('sizing_desc', '?'))} | "
                  f"{prof.get('max_positions', '?')} | {calib5} | {calib22} | {buf20_5} | {canon} |")
    L.append("")
    if any(ev.get('no_calibrated_read') for ev in evidence.values()):
        flagged = [k for k, ev in evidence.items() if ev.get('no_calibrated_read')]
        for k in flagged:
            L.append(f"> **NO CALIBRATED READ FOR ACTIVE CONFIG {k.upper()} -- run one before citing cert "
                      f"numbers for this profile.**")
        L.append("")

    L.append("### Secondary / caveat readings")
    L.append("")
    any_secondary = False
    for key in ('core', 'apex', 'sentinel'):
        ev = evidence.get(key, {})
        secondary = [c for c in ev.get('calibrated', []) if c.get('found') and c.get('kind', '').endswith('_only')]
        secondary += [c for c in ev.get('buf20', []) if c.get('found') and c.get('kind', '').endswith('_only')]
        for c in secondary:
            any_secondary = True
            w5 = c['windows'].get('5y')
            if 'canon' in c.get('kind', ''):
                caption = ("the CANON (zero-friction fill) lens -- optimistic/unrealizable by house "
                           "doctrine; NOT a headline reading, shown for reference only.")
            else:
                caption = ("barrier-matched but sizing not confirmed against the shipped config; treat "
                           "as supplementary, not headline.")
            L.append(f"- **{key}** `{c['path']}` (`{c['kind']}`): 5y {fmt_triplet(w5)} -- {caption}")
    if not any_secondary:
        L.append("(none)")
    L.append("")

    if not paired2x.get('found'):
        L.append(f"> Apex paired-2x summary (`{paired2x.get('path', '?')}`) could not be matched to the shipped "
                  f"sizing arm ({to_ascii(paired2x.get('error', ''))}). Verbatim excerpt:")
        L.append("```")
        L.append(to_ascii(paired2x.get('excerpt', '(no excerpt available -- file unreadable)')))
        L.append("```")
        L.append("")

    # --- CT sleeve status: reference-only citation, NOT a shipped config. The
    # ctsl_vehicle_2026_08 campaign's own disposition is "No ship" -- every lever
    # failed survivorship -- so this is deliberately a pointer + two headline figures,
    # not a table (see that campaign's own FINDINGS.md for the full six-stage case). ---
    ctsl = ctsl_status or {}
    if ctsl.get('available'):
        window = ctsl.get('window')
        nf = ctsl.get('never_fill')
        nf_str = f"{nf * 100:.1f}%" if nf is not None else 'n/a'
        cap_bits = []
        for book, label in (('50000', '$50k'), ('100000', '$100k')):
            c = (ctsl.get('cap_by_book') or {}).get(book)
            pct = c.get('pct_clip_capped') if c else None
            ept = c.get('effective_trades_per_year') if c else None
            if pct is not None and ept is not None:
                cap_bits.append(f"{label} book: {pct:.1f}% of trades clip-capped, ~{ept:.1f} effective trades/yr")
        cap_str = '; '.join(cap_bits) if cap_bits else 'n/a'
        L.append("### CT sleeve status (reference only -- not a shipped config)")
        L.append("")
        L.append(f"CTSL/CT_PROMOTE (the counter-trend sleeve `ctsl_vehicle_2026_08` studied as a candidate "
                  f"edge carrier) is **not shipped and not fundable as measured** -- every sizing/gross lever "
                  f"failed survivorship and the campaign's own disposition is 'No ship'. Cited here only "
                  f"because earlier docs named it a candidate carrier; this keeps the plan pointed at the "
                  f"debunking evidence, not just the superseded index-competitive claim. Honest never-fill "
                  f"({window}): **{nf_str}** -- a HEADWIND vs the 15% engine default, not the tailwind first "
                  f"assumed. Capacity at owner scale ({window}): {cap_str} -- capacity, not fill quality, is "
                  f"the binding constraint.")
        L.append("")
        L.append(f"Full six-stage methodology: `{ctsl.get('findings_path')}`. Source cells: "
                  f"`{ctsl.get('missp_path')}`, `{ctsl.get('capacity_path')}`.")
        L.append("")
    elif ctsl.get('error'):
        L.append("### CT sleeve status (reference only -- not a shipped config)")
        L.append("")
        L.append(f"Could not read CT sleeve evidence: {to_ascii(ctsl.get('error'))} "
                  f"(see `{ctsl.get('findings_path', '?')}`).")
        L.append("")

    # --- Section 2: scenario table ---
    L.append("## 2. Deterministic debt/net-worth scenarios")
    L.append("")
    L.append(f"CAD. Surplus ${params['monthly_surplus_cad']:,.0f}/mo. Debt ${params['debt_balance_cad']:,.0f} @ "
              f"{params['debt_apr'] * 100:.2f}% APR. HISA {params['hisa_apr'] * 100:.1f}% APR. Seed target "
              f"${params['seed_target_cad']:,.0f}. Horizon {params['horizon_months']} months. Month-end "
              f"convention: interest/HISA/portfolio growth accrues first, then that month's surplus applies.")
    L.append("")
    L.append("### Anchor self-check (g=0%, vs the ratified 2026-08-09 plan; +/-2% band)")
    L.append("")
    L.append("| Scenario | Target NW@horizon | Actual NW@horizon | Diff | Result |")
    L.append("|---|---|---|---|---|")
    for row in anchors['rows']:
        L.append(f"| {row['scenario']} | {fmt_cad(row['target'])} | {fmt_cad(row['actual'])} | "
                  f"{row['diff_pct'] * 100:+.2f}% | {'PASS' if row['ok'] else 'FAIL'} |")
    L.append(f"| A payoff schedule | 11 payments, ~$2,636 interest | {anchors['a_payoff_month']} payments, "
              f"${anchors['a_total_interest']:,.0f} interest | -- | "
              f"{'PASS' if (anchors['payoff_ok'] and anchors['interest_ok']) else 'FAIL'} |")
    L.append("")
    L.append(f"**Self-check: {'ALL PASS' if anchors['all_pass'] else 'SOME FAIL -- see rows above; treat the table below with caution until resolved'}**")
    L.append("")

    g_tiers = scenario_table['g_tiers']
    for month_label, month_key, min_horizon in (
        ('Month 36 (3y)', 'nw_month36', 36),
        (f"Month {params['horizon_months']} ({params['horizon_months'] // 12}y)", 'nw_horizon', 0),
    ):
        L.append(f"### Net worth at {month_label}")
        L.append("")
        L.append("| Scenario | Payoff date | Deploy date | " + " | ".join(f"g={g * 100:.0f}%" for g in g_tiers) + " |")
        L.append("|---|---|---|" + "---|" * len(g_tiers))
        for scen in ('A', 'B', 'C', 'D'):
            r0 = scenario_table['results_by_g'][g_tiers[0]][scen]
            payoff_d = r0['payoff_date'] or ('n/a (interest-only)' if scen == 'D' else 'n/a')
            deploy_d = r0['deploy_date'] or 'n/a'
            cells = []
            for g in g_tiers:
                r = scenario_table['results_by_g'][g][scen]
                cells.append(fmt_cad(r.get(month_key)))
            L.append(f"| {SCENARIO_LABELS[scen]} | {payoff_d} | {deploy_d} | " + " | ".join(cells) + " |")
        L.append("")

    L.append("Scenario ranking by terminal NW: g=0% -> " + ' > '.join(scenario_table['ranking'][g_tiers[0]]) +
              "; g=30% -> " + ' > '.join(scenario_table['ranking'].get(
                  next((g for g in g_tiers if abs(g - 0.30) < 1e-9), g_tiers[-1]))) + ".")
    L.append("")
    L.append("This table is the arithmetic substrate only -- see capital-plan-2026.md for the narrative "
              "interpretation, MC adjudication, and gating logic around it.")
    L.append("")

    # --- Section 3: gate snapshot ---
    L.append("## 3. Gate snapshot")
    L.append("")
    today = date.today()
    L.append("| Gate | Date | Days from today |")
    L.append("|---|---|---|")
    for gate_key, gate_date_str in params.get('gate_dates', {}).items():
        try:
            gd = datetime.strptime(gate_date_str, '%Y-%m-%d').date()
            days_str = f"{(gd - today).days:+d}"
        except ValueError:
            days_str = 'n/a'
        L.append(f"| {gate_key} | {gate_date_str} | {days_str} |")
    L.append("")
    if ledger.get('available'):
        eq = ledger.get('equity_usd')
        start = ledger.get('start_capital_usd')
        ret = ledger.get('return_pct')
        dd = ledger.get('max_dd_pct')
        L.append(f"Live ledger: profile=**{ledger.get('profile')}**, equity=${eq:,.2f}"
                  + (f", start=${start:,.2f}" if start is not None else "")
                  + (f", return={ret:+.1f}%" if ret is not None else "")
                  + (f", maxDD={dd:.1f}%" if dd is not None else "")
                  + f" (source: `{ledger.get('url')}`)")
    else:
        L.append(f"Live ledger: {to_ascii(ledger.get('note', 'API unavailable'))} (tried `{ledger.get('url')}`)")
    L.append("")

    # --- Section 4: verdict facts + delta ---
    L.append("## 4. Verdict facts + delta")
    L.append("")
    if delta_status == 'baseline':
        L.append("Baseline established -- no prior run to compare against. This run's facts are now the "
                  "reference for the next run.")
    elif delta_status == 'unchanged':
        L.append("No verdict change vs the previous run.")
    else:
        L.append("```")
        L.extend(banner_lines)
        L.append("```")
    L.append("")
    L.append("Current verdict facts:")
    L.append("")
    L.append("```json")
    L.append(json.dumps(facts, indent=2))
    L.append("```")
    L.append("")

    # --- Section 5: footer (params schema + evidence sources) ---
    L.append("## 5. Params schema + evidence sources")
    L.append("")
    L.append("`.claude/docs/capital_plan_params.json` schema (JSON has no comments, documented here instead):")
    L.append("")
    L.append("- `as_of` (str, YYYY-MM-DD): plan baseline date, month 0 of the scenario grid.")
    L.append("- `debt_balance_cad`, `debt_apr`, `monthly_surplus_cad`, `portfolio_cash_cad`, `seed_target_cad`, "
              "`hisa_apr`: scenario inputs, all CAD / annual rates.")
    L.append("- `scenario_c_deploy_threshold_cad`: scenario C's own cash-deploy trigger (distinct from "
              "`seed_target_cad`, which scenarios A/B use).")
    L.append("- `fx_cad_per_usd`: reference FX rate, informational only (scenario math stays in CAD).")
    L.append("- `g_tiers_annual`: list of annual portfolio growth-rate tiers the NW tables are computed for.")
    L.append("- `horizon_months`: scenario grid length in months.")
    L.append("- `gate_dates`: `{label: YYYY-MM-DD}`, rendered in section 3 with days-from-today.")
    L.append("- `evidence_sources.calibrated_csvs`: list of `{path, profile, kind, match_by}`. `match_by` is "
              "`tp_sl` (match rows on the profile's shipped TP_BASE/SL_BASE), `cell_name` (match on an "
              "alloc-sizing cell name `n<max_positions>_f<flat_frac:.3f>`), or `profile_lens` (match on the "
              "CSV's own `profile` + `lens` columns, tp/sl-confirmed, ALL windows present in the file -- for "
              "multi-profile/multi-lens CSVs like flipR_r1.csv; requires an extra `lens` field, e.g. "
              "`\"calibrated\"` or `\"canon\"`). A `kind` ending in `_only` marks a secondary/caveat reading "
              "-- matched on one dimension but not fully confirmed on the other (or, for a `canon` lens, "
              "matched but deliberately excluded from ever becoming the headline reading).")
    L.append("- `evidence_sources.paired2x_summary`: path to the Apex paired-2x-race markdown summary.")
    L.append("- `evidence_sources.ctsl_status`: optional `{findings_path, missp_path, capacity_path, window}` "
              "-- a small referenced-evidence citation (never-fill rate + capacity) from a CLOSED, "
              "NOT-shipped research campaign; rendered in section 1 as a short note, never a table.")
    L.append("")
    L.append("Evidence source files (path -- last modified):")
    L.append("")
    for p in evidence_source_file_list(params):
        L.append(f"- `{p['path']}` -- {p['mtime']}")
    L.append("")
    return "\n".join(L) + "\n"


# ─── CLI ───────────────────────────────────────────────────────────────────

def main(argv=None):
    t0 = time.time()
    parser = argparse.ArgumentParser(description='Capital plan refresh tool (offline, <15s).')
    parser.add_argument('--check', action='store_true',
                         help='Compute + compare only; do not write the scenarios doc or state file.')
    parser.add_argument('--params', type=str, default=str(DEFAULT_PARAMS_PATH),
                         help='Path to the params JSON (default: .claude/docs/capital_plan_params.json).')
    args = parser.parse_args(argv)

    params_path = Path(args.params)
    try:
        with open(params_path, 'r', encoding='utf-8-sig') as f:
            params = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: params file not found: {params_path}")
        return 1
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: params file unreadable/corrupt: {exc}")
        return 1

    missing = [k for k in REQUIRED_PARAM_FIELDS if k not in params]
    if missing:
        print(f"ERROR: params file missing required fields: {missing}")
        return 1

    print("=" * 72)
    print("Capital Plan Refresh -- tools/capital_plan_refresh.py")
    print(f"params: {params_path}  as_of: {params.get('as_of')}  mode: {'--check' if args.check else 'full run'}")
    print("=" * 72)

    # --- Part 1 ---
    scenario_table = build_scenario_table(params)
    anchors = run_anchor_check(scenario_table['results_by_g'][scenario_table['g_tiers'][0]])
    print()
    print("-- Part 1: deterministic scenario anchors (g=0) --")
    for row in anchors['rows']:
        print(f"  {row['scenario']}: target {row['target']:,.0f}  actual {row['actual']:,.0f}  "
              f"diff {row['diff_pct'] * 100:+.2f}%  [{'PASS' if row['ok'] else 'FAIL'}]")
    print(f"  A payoff months: target {ANCHOR_PAYOFF_MONTHS}  actual {anchors['a_payoff_month']}  "
          f"[{'PASS' if anchors['payoff_ok'] else 'FAIL'}]")
    print(f"  A total interest to payoff: target ~{ANCHOR_PAYOFF_INTEREST:,.0f}  "
          f"actual {anchors['a_total_interest']:,.0f}  [{'PASS' if anchors['interest_ok'] else 'FAIL'}]")
    print(f"  ANCHOR SELF-CHECK: {'ALL PASS' if anchors['all_pass'] else 'SOME FAIL -- see rows above'}")

    # --- Part 2 (profiles.json is a required repo file -- exit 1 if unreadable) ---
    try:
        profiles = extract_profiles(PROFILES_JSON_PATH)
    except FileNotFoundError:
        print(f"ERROR: required repo file not found: {PROFILES_JSON_PATH}")
        return 1
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: required repo file unreadable/corrupt: {PROFILES_JSON_PATH}: {exc}")
        return 1

    print()
    print("-- Part 2: evidence harvest --")
    evidence = harvest_evidence(params, profiles)
    for key in ('core', 'apex', 'sentinel'):
        prof = profiles.get(key, {})
        ev = evidence.get(key, {})
        print(f"  {key} ({prof.get('version_tag', '?')}): tp={prof.get('tp_base')} sl={prof.get('sl_base')} "
              f"sizing={to_ascii(prof.get('sizing_desc'))}")
        if ev.get('no_calibrated_read'):
            print(f"    !! NO CALIBRATED READ FOR ACTIVE CONFIG {key} -- run one before citing cert numbers !!")
        else:
            primary = pick_primary(ev['calibrated'])
            w5 = primary['windows'].get('5y') if primary else None
            w22 = primary['windows'].get('22-now') if primary else None
            if w5:
                print(f"    calibrated 5y [{primary['path']}]: med {w5['med_ret_pct']:+.1f}%  "
                      f"DD {w5['worst_dd_pct']:.1f}%  collapse {(w5['p_coll_pct'] or 0):.1f}%")
            if w22:
                print(f"    calibrated 22-now: med {w22['med_ret_pct']:+.1f}%  DD {w22['worst_dd_pct']:.1f}%  "
                      f"collapse {(w22['p_coll_pct'] or 0):.1f}%")

    apex_profile = profiles.get('apex')
    paired2x = harvest_paired2x(params, apex_profile) if apex_profile else {'found': False, 'error': 'no apex profile'}
    if paired2x.get('found'):
        print(f"  apex paired-2x ({paired2x['arm']}): P(2x)={paired2x['p_2x_ever']}%  "
              f"worstDD={paired2x['worst_dd']}%  medDays={paired2x['med_days_to_2x']}  "
              f"collapse={paired2x['p_collapse']}%")
    else:
        print(f"  apex paired-2x: NOT PARSED -- {to_ascii(paired2x.get('error', 'see excerpt in doc'))}")

    ctsl_status = harvest_ctsl_status(params)
    if ctsl_status.get('available'):
        nf = ctsl_status.get('never_fill')
        nf_str = f"{nf * 100:.1f}%" if nf is not None else 'n/a'
        print(f"  CT sleeve status (NOT shipped -- {to_ascii(ctsl_status.get('findings_path'))}): "
              f"never-fill[{ctsl_status.get('window')}]={nf_str}")
    elif ctsl_status.get('error'):
        print(f"  CT sleeve status: {to_ascii(ctsl_status.get('error'))}")

    # --- Part 3 ---
    print()
    print("-- Part 3: live ledger --")
    port = discover_api_port()
    ledger = fetch_live_ledger(port)
    if ledger.get('available'):
        print(f"  profile={ledger.get('profile')}  equity=${ledger.get('equity_usd'):,.2f}  "
              f"return={fmt_pct((ledger.get('return_pct') or 0) / 100.0, 1)}  "
              f"maxDD={(ledger.get('max_dd_pct') or 0):.1f}%")
    else:
        print(f"  {to_ascii(ledger.get('note', 'live ledger: API unavailable'))}")

    # --- Part 4 ---
    facts = build_verdict_facts(scenario_table, evidence, paired2x)
    old_state = None
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, 'r', encoding='utf-8-sig') as f:
                old_state = json.load(f)
        except (json.JSONDecodeError, OSError):
            old_state = None

    print()
    print("-- Part 4: verdict delta --")
    banner_lines = []
    if old_state is None:
        print("  baseline established (no prior state file -- this is the first run)")
        delta_status = 'baseline'
    else:
        diffs = diff_facts(old_state.get('facts', {}), facts)
        if diffs:
            banner_lines.append("!!! VERDICT DELTA -- REVIEW capital-plan-2026.md !!!")
            for k, ov, nv in diffs:
                banner_lines.append(f"  {k}: {ov} -> {nv}")
            for line in banner_lines:
                print(line)
            delta_status = 'changed'
        else:
            print("  no verdict change")
            delta_status = 'unchanged'

    elapsed = time.time() - t0
    print()
    if not args.check:
        doc_text = render_scenarios_doc(params, scenario_table, anchors, evidence, profiles, paired2x,
                                         ledger, port, banner_lines, delta_status, facts, ctsl_status)
        SCENARIOS_DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SCENARIOS_DOC_PATH, 'w', encoding='utf-8') as f:
            f.write(doc_text)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        new_state = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'params_as_of': params.get('as_of'),
            'facts': facts,
        }
        with open(STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump(new_state, f, indent=2)
        print(f"wrote {SCENARIOS_DOC_PATH}")
        print(f"wrote {STATE_PATH}")
    else:
        print("--check: scenarios doc and state file NOT written")

    print(f"done in {elapsed:.2f}s")
    return 0


if __name__ == '__main__':
    sys.exit(main())
