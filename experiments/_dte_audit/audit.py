"""DTE asymmetry audit — Phase 0 of the standardization plan.

Read-only. Produces AUDIT.md with three inventories:
  1. Mechanism coverage matrix (strategy_config fields x engine files x drift-guard)
  2. Experiment script DTE declarations + engine imports
  3. .cache/ parquet naming (does the filename encode DTE?)

Run: python experiments/_dte_audit/audit.py
"""
from __future__ import annotations

import ast
import re
import sys
import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

OUT_PATH = ROOT / 'experiments' / '_dte_audit' / 'AUDIT.md'

# ─── Helpers ───────────────────────────────────────────────────────────────


def _module_level_names(path: Path) -> set[str]:
    """Return the set of module-level constant Name targets assigned in `path`.

    Uses AST so we catch only top-level assignments (not inside functions).
    Includes all-uppercase names AND mixed-case dict literals (TIER_ALLOC etc.).
    """
    src = path.read_text(encoding='utf-8', errors='replace')
    tree = ast.parse(src, filename=str(path))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _extract_dataclass_fields(path: Path, class_name: str) -> list[str]:
    """Return the ordered field names of a frozen dataclass."""
    src = path.read_text(encoding='utf-8', errors='replace')
    tree = ast.parse(src, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            fields: list[str] = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.append(item.target.id)
            return fields
    return []


def _drift_guard_pair_names(path: Path) -> dict[str, set[str]]:
    """Parse tests/test_strategy_config_drift.py for the (name, observed, expected)
    tuples in pairs_mc / pairs_bc / pairs_15. Returns {array: {first-tuple-element}}.
    """
    src = path.read_text(encoding='utf-8', errors='replace')
    tree = ast.parse(src, filename=str(path))
    pair_arrays = {'pairs_mc', 'pairs_bc', 'pairs_15'}
    out: dict[str, set[str]] = {k: set() for k in pair_arrays}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id in pair_arrays:
                if isinstance(node.value, ast.List):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Tuple) and elt.elts:
                            first = elt.elts[0]
                            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                                out[tgt.id].add(first.value)
    return out


# ─── Inventory 1: mechanism coverage matrix ─────────────────────────────────


def inventory_mechanism_coverage() -> tuple[list[dict], dict]:
    """Compare every config field against engine + drift-guard coverage."""
    cfg_path = ROOT / 'strategy_config.py'
    dte_fields = _extract_dataclass_fields(cfg_path, 'DteStrategyConfig')
    opt_fields = _extract_dataclass_fields(cfg_path, 'OptionStrategyConfig')
    # drop the composed `option` field — it is recursively expanded into opt_fields
    dte_fields = [f for f in dte_fields if f != 'option']

    mc30 = _module_level_names(ROOT / 'monte_carlo.py')
    mc15 = _module_level_names(ROOT / 'monte_carlo_15dte.py')
    bc30 = _module_level_names(ROOT / 'backtest_cascade.py')
    bc15 = _module_level_names(ROOT / 'backtest_cascade_15dte.py')

    pairs = _drift_guard_pair_names(ROOT / 'tests' / 'test_strategy_config_drift.py')
    g_mc = pairs.get('pairs_mc', set())
    g_bc = pairs.get('pairs_bc', set())
    g_15 = pairs.get('pairs_15', set())

    # Some drift-guard pair entries use bc.NAME or mc15.NAME prefixes — strip them.
    def _norm_pairs(s: set[str]) -> set[str]:
        out = set()
        for k in s:
            base = k.split('.', 1)[-1] if '.' in k else k
            out.add(base)
        return out
    g_mc_n = _norm_pairs(g_mc)
    g_bc_n = _norm_pairs(g_bc)
    g_15_n = _norm_pairs(g_15)

    # The drift-guard pair name doesn't always equal the constant name (e.g.
    # WEAK_WEEKLY_CALL_WADJ in mc, WEAK_WEEKLY_CALL_WADJ_LT in cfg). Allow
    # an equivalence map when the bare constant name matches across both ends.
    rows: list[dict] = []

    all_fields = list(dte_fields)
    # OptionStrategyConfig fields are accessed as `cfg.option.X` but module-level
    # constants in MC/BC mirror by bare name (TP_BASE, SL_BASE, ...). Include them.
    all_fields += [f for f in opt_fields if f not in all_fields]

    for fld in all_fields:
        # alias map: a few drift-guard names diverge from cfg names
        # WEAK_WEEKLY_CALL_WADJ_LT in cfg → WEAK_WEEKLY_CALL_WADJ in mc/bc
        alt = None
        if fld == 'WEAK_WEEKLY_CALL_WADJ_LT':
            alt = 'WEAK_WEEKLY_CALL_WADJ'

        def has(pool: set[str]) -> bool:
            return fld in pool or (alt is not None and alt in pool)

        rows.append({
            'field': fld,
            'in_mc30': has(mc30),
            'in_mc15': has(mc15),
            'in_bc30': has(bc30),
            'in_bc15': has(bc15),
            'in_drift_mc': has(g_mc_n),
            'in_drift_bc': has(g_bc_n),
            'in_drift_15': has(g_15_n),
        })

    summary = {
        'dte_fields': len(dte_fields),
        'opt_fields': len(opt_fields),
        'mc30_consts': len(mc30),
        'mc15_consts': len(mc15),
        'bc30_consts': len(bc30),
        'bc15_consts': len(bc15),
        'drift_pairs_mc': len(g_mc),
        'drift_pairs_bc': len(g_bc),
        'drift_pairs_15': len(g_15),
    }
    return rows, summary


# ─── Inventory 2: experiment script DTE declarations ────────────────────────


_IMPORT_MC30 = re.compile(r'^\s*import\s+monte_carlo\s+as\s+(\w+)|^\s*from\s+monte_carlo\s+import', re.M)
_IMPORT_MC15 = re.compile(r'^\s*import\s+monte_carlo_15dte\s+as\s+(\w+)|^\s*from\s+monte_carlo_15dte\s+import', re.M)
_IMPORT_BC30 = re.compile(r'^\s*import\s+backtest_cascade\s+as\s+(\w+)|^\s*from\s+backtest_cascade\s+import', re.M)
_IMPORT_BC15 = re.compile(r'^\s*import\s+backtest_cascade_15dte\s+as\s+(\w+)|^\s*from\s+backtest_cascade_15dte\s+import', re.M)
_DECL_DTE = re.compile(r'^\s*DTE\s*=\s*(\d+)', re.M)
_HOLDOUT = re.compile(r'(assert_no_holdout_leak|pre_cutoff_filter|HOLDOUT_DISABLE|CALIBRATION_CUTOFF_DATE)')


def inventory_experiments() -> list[dict]:
    """For every .py under experiments/ that imports an MC/BC engine, record
    which engine, what alias, whether DTE is declared, holdout usage."""
    out: list[dict] = []
    exp_root = ROOT / 'experiments'
    for path in sorted(exp_root.rglob('*.py')):
        # skip the audit dir itself + dunder
        if '_dte_audit' in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            src = path.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue

        m30 = _IMPORT_MC30.search(src)
        m15 = _IMPORT_MC15.search(src)
        b30 = _IMPORT_BC30.search(src)
        b15 = _IMPORT_BC15.search(src)
        if not (m30 or m15 or b30 or b15):
            continue

        decl = _DECL_DTE.search(src)
        decl_val = int(decl.group(1)) if decl else None

        m30_alias = m30.group(1) if (m30 and m30.group(1)) else (None if not m30 else 'from-import')
        m15_alias = m15.group(1) if (m15 and m15.group(1)) else (None if not m15 else 'from-import')

        # detect aliasing both engines as the same name (copy-paste hazard)
        has_mc_alias_collision = False
        if m30_alias and m15_alias and m30_alias == m15_alias:
            has_mc_alias_collision = True
        # most concerning: 15dte aliased as just `mc` (looks like 30 DTE at a glance)
        suspicious_alias_15 = (m15_alias == 'mc') and not m30

        holdout_seen = bool(_HOLDOUT.search(src))

        out.append({
            'file': rel,
            'imports_mc30': bool(m30),
            'imports_mc15': bool(m15),
            'imports_bc30': bool(b30),
            'imports_bc15': bool(b15),
            'mc30_alias': m30_alias,
            'mc15_alias': m15_alias,
            'has_DTE_decl': decl is not None,
            'DTE_decl_value': decl_val,
            'holdout_seen': holdout_seen,
            'suspicious_alias_15': suspicious_alias_15,
            'alias_collision': has_mc_alias_collision,
        })
    return out


# ─── Inventory 3: cache parquet naming (DuckDB) ─────────────────────────────


_DTE_IN_NAME = re.compile(r'(?<![a-zA-Z])(15dte|30dte|_15_|_30_)(?![a-zA-Z])', re.IGNORECASE)
_VERSION_IN_NAME = re.compile(r'_v(\d+)_', re.IGNORECASE)
_LOOKBACK_IN_NAME = re.compile(r'_(\d{3,4})\.parquet$|_(\d+)y\.parquet$', re.IGNORECASE)


def inventory_cache_parquets() -> tuple[list[dict], dict]:
    """Walk .cache/ for parquets. For each: does the filename encode DTE,
    version, lookback? Use DuckDB to extract row count + column count."""
    cache_root = ROOT / '.cache'
    if not cache_root.exists():
        return [], {'reason': 'no .cache/ dir'}

    # Collect files first; defer DuckDB calls (one connection for all)
    parquets: list[Path] = []
    for p in cache_root.rglob('*.parquet'):
        parquets.append(p)
    parquets.sort()

    # DuckDB metadata extraction
    rows_meta: list[dict] = []
    try:
        import duckdb  # noqa: E402
        con = duckdb.connect(':memory:')
    except Exception as e:
        # fallback: still produce naming inventory without row counts
        for p in parquets:
            rel = p.relative_to(ROOT).as_posix()
            name = p.name
            rows_meta.append({
                'file': rel,
                'name_has_dte': bool(_DTE_IN_NAME.search(name)),
                'name_has_version': bool(_VERSION_IN_NAME.search(name)),
                'name_has_lookback': bool(_LOOKBACK_IN_NAME.search(name)),
                'rows': None,
                'cols': None,
                'size_mb': round(p.stat().st_size / 1e6, 1),
            })
        return rows_meta, {'duckdb_available': False, 'error': str(e), 'count': len(parquets)}

    # use COPY-style metadata extraction; fall back per-file on errors
    n_dte = n_ver = n_lb = 0
    for p in parquets:
        rel = p.relative_to(ROOT).as_posix()
        name = p.name
        has_dte = bool(_DTE_IN_NAME.search(name))
        has_ver = bool(_VERSION_IN_NAME.search(name))
        has_lb = bool(_LOOKBACK_IN_NAME.search(name))
        n_dte += int(has_dte)
        n_ver += int(has_ver)
        n_lb += int(has_lb)
        # row + col count via DuckDB; cheap (reads metadata only)
        try:
            r = con.execute(
                "SELECT COUNT(*) AS rows, "
                "(SELECT COUNT(*) FROM (DESCRIBE SELECT * FROM read_parquet(?))) AS cols "
                "FROM read_parquet(?)",
                [str(p), str(p)]
            ).fetchone()
            rows_count = int(r[0]) if r and r[0] is not None else None
            cols_count = int(r[1]) if r and r[1] is not None else None
        except Exception:
            rows_count = None
            cols_count = None
        rows_meta.append({
            'file': rel,
            'name_has_dte': has_dte,
            'name_has_version': has_ver,
            'name_has_lookback': has_lb,
            'rows': rows_count,
            'cols': cols_count,
            'size_mb': round(p.stat().st_size / 1e6, 1),
        })
    summary = {
        'duckdb_available': True,
        'count': len(parquets),
        'with_dte_in_name': n_dte,
        'with_version_in_name': n_ver,
        'with_lookback_in_name': n_lb,
    }
    return rows_meta, summary


# ─── Reporters ─────────────────────────────────────────────────────────────


def _md_table(headers: list[str], rows: list[list]) -> str:
    out: list[str] = []
    out.append('| ' + ' | '.join(headers) + ' |')
    out.append('|' + '|'.join('---' for _ in headers) + '|')
    for r in rows:
        out.append('| ' + ' | '.join(str(c) for c in r) + ' |')
    return '\n'.join(out)


def render_report(
    cov_rows: list[dict],
    cov_summary: dict,
    exp_rows: list[dict],
    cache_rows: list[dict],
    cache_summary: dict,
) -> str:
    md: list[str] = []
    md.append('# DTE Asymmetry Audit — Phase 0\n')
    md.append('Read-only inventory of 30 DTE / 15 DTE coverage gaps. Drives Phase 1 (drift-guard parity), Phase 2 (mechanism registry), Phase 3 (sweep harness).\n')
    md.append('Generated by `experiments/_dte_audit/audit.py`. Re-run any time.\n')

    # ─── Section 1: mechanism coverage ────────────────────────────────────
    md.append('---\n')
    md.append('## 1. Mechanism Coverage Matrix\n')
    md.append('For every field declared on `DteStrategyConfig` / `OptionStrategyConfig`, '
              'whether the four MC/BC engines define it as a module-level constant, and whether '
              'the drift-guard tracks it.\n')
    md.append('**Summary:**\n')
    md.append(f'- DteStrategyConfig fields: **{cov_summary["dte_fields"]}**')
    md.append(f'- OptionStrategyConfig fields: **{cov_summary["opt_fields"]}**')
    md.append(f'- monte_carlo.py module-level consts: {cov_summary["mc30_consts"]}')
    md.append(f'- monte_carlo_15dte.py module-level consts: {cov_summary["mc15_consts"]}')
    md.append(f'- backtest_cascade.py module-level consts: {cov_summary["bc30_consts"]}')
    md.append(f'- backtest_cascade_15dte.py module-level consts: {cov_summary["bc15_consts"]}')
    md.append(f'- drift-guard pairs_mc entries: {cov_summary["drift_pairs_mc"]}')
    md.append(f'- drift-guard pairs_bc entries: {cov_summary["drift_pairs_bc"]}')
    md.append(f'- drift-guard pairs_15 entries: {cov_summary["drift_pairs_15"]}')
    md.append('')

    # gap counts
    n_total = len(cov_rows)
    gap_mc15_vs_mc30 = sum(1 for r in cov_rows if r['in_mc30'] and not r['in_mc15'])
    gap_bc15_vs_bc30 = sum(1 for r in cov_rows if r['in_bc30'] and not r['in_bc15'])
    gap_drift15_vs_driftmc = sum(1 for r in cov_rows if r['in_drift_mc'] and not r['in_drift_15'])
    md.append('**Asymmetry counts (lower is better — gap fields are silent-error risks):**\n')
    md.append(f'- Fields wired in `monte_carlo.py` but NOT `monte_carlo_15dte.py`: **{gap_mc15_vs_mc30}** of {n_total}')
    md.append(f'- Fields wired in `backtest_cascade.py` but NOT `backtest_cascade_15dte.py`: **{gap_bc15_vs_bc30}** of {n_total}')
    md.append(f'- Fields drift-guarded for 30 DTE but NOT 15 DTE: **{gap_drift15_vs_driftmc}** of {n_total}')
    md.append('')

    md.append('### Full coverage matrix\n')
    md.append('Legend: ✓ = present, ✗ = absent. "drift_*" cols = is the field tracked in the drift-guard pair-array for that engine.\n')
    headers = ['field', 'mc30', 'mc15', 'bc30', 'bc15', 'drift_mc', 'drift_bc', 'drift_15']
    rows = []
    for r in cov_rows:
        m = lambda b: '✓' if b else '✗'
        rows.append([
            r['field'],
            m(r['in_mc30']), m(r['in_mc15']), m(r['in_bc30']), m(r['in_bc15']),
            m(r['in_drift_mc']), m(r['in_drift_bc']), m(r['in_drift_15']),
        ])
    md.append(_md_table(headers, rows))
    md.append('')

    md.append('### Gap rows only (silent-error candidates)\n')
    gap_rows = [r for r in cov_rows if (r['in_mc30'] and not r['in_mc15']) or
                (r['in_bc30'] and not r['in_bc15']) or
                (r['in_drift_mc'] and not r['in_drift_15'])]
    if not gap_rows:
        md.append('_No gaps. All fields covered symmetrically._\n')
    else:
        rows = []
        for r in gap_rows:
            m = lambda b: '✓' if b else '✗'
            rows.append([
                r['field'],
                m(r['in_mc30']), m(r['in_mc15']), m(r['in_bc30']), m(r['in_bc15']),
                m(r['in_drift_mc']), m(r['in_drift_bc']), m(r['in_drift_15']),
            ])
        md.append(_md_table(headers, rows))
    md.append('')

    # ─── Section 2: experiments ───────────────────────────────────────────
    md.append('---\n')
    md.append('## 2. Experiment Script DTE Declarations\n')
    md.append('Every `experiments/**/*.py` that imports `monte_carlo`, `monte_carlo_15dte`, '
              '`backtest_cascade`, or `backtest_cascade_15dte`. Risk indicators: missing `DTE = N` '
              'declaration, alias collision (`mc` used for 15 DTE engine), missing holdout enforcement.\n')
    n_total = len(exp_rows)
    n_no_decl = sum(1 for r in exp_rows if not r['has_DTE_decl'])
    n_15_alias_mc = sum(1 for r in exp_rows if r['suspicious_alias_15'])
    n_collision = sum(1 for r in exp_rows if r['alias_collision'])
    n_no_holdout = sum(1 for r in exp_rows if not r['holdout_seen'])
    md.append(f'- Scripts importing an MC/BC engine: **{n_total}**')
    md.append(f'- Without `DTE = N` declaration: **{n_no_decl}**')
    md.append(f'- 15 DTE engine aliased as `mc` (looks like 30 DTE at a glance): **{n_15_alias_mc}**')
    md.append(f'- Aliasing 30 DTE and 15 DTE engines with the same alias: **{n_collision}**')
    md.append(f'- No holdout helper used (CALIBRATION_CUTOFF_DATE / assert_no_holdout_leak / pre_cutoff_filter): **{n_no_holdout}**')
    md.append('')

    md.append('### Suspicious files (15 DTE aliased as `mc`)\n')
    sus = [r for r in exp_rows if r['suspicious_alias_15']]
    if not sus:
        md.append('_None._\n')
    else:
        rows = [[r['file'], r['mc15_alias'] or '-', r.get('DTE_decl_value', '-') or '-'] for r in sus]
        md.append(_md_table(['file', 'mc15_alias', 'DTE_decl'], rows))
    md.append('')

    md.append('### All MC/BC-importing scripts\n')
    rows = []
    for r in exp_rows:
        rows.append([
            r['file'],
            'mc30' if r['imports_mc30'] else ('mc15' if r['imports_mc15'] else
                ('bc30' if r['imports_bc30'] else 'bc15')),
            r.get('mc30_alias') or r.get('mc15_alias') or '-',
            (str(r['DTE_decl_value']) if r['has_DTE_decl'] else '-'),
            ('Y' if r['holdout_seen'] else 'N'),
        ])
    md.append(_md_table(['file', 'engine', 'alias', 'DTE_decl', 'holdout'], rows))
    md.append('')

    # ─── Section 3: cache parquets ────────────────────────────────────────
    md.append('---\n')
    md.append('## 3. .cache/ Parquet Naming\n')
    md.append('Per `process.md` "On-demand bulk cache pattern", names should encode '
              '`(experiment, version, lookback)`. **DTE is not in the canonical convention** — '
              'this audit shows how many parquets currently encode it.\n')
    if cache_summary.get('duckdb_available') is False:
        md.append(f'_DuckDB unavailable: {cache_summary.get("error", "?")}. Naming inventory only; row counts skipped._\n')
    md.append(f'- Total parquets under `.cache/`: **{cache_summary.get("count", 0)}**')
    md.append(f'- Filename encodes DTE (e.g. `_15dte_`, `_30dte_`): **{cache_summary.get("with_dte_in_name", "?")}**')
    md.append(f'- Filename encodes version (`_vNN_`): **{cache_summary.get("with_version_in_name", "?")}**')
    md.append(f'- Filename encodes lookback (`_NNNN.parquet` or `_Ny.parquet`): **{cache_summary.get("with_lookback_in_name", "?")}**')
    md.append('')

    md.append('### Parquets without DTE in filename (potential cross-DTE collision risk)\n')
    no_dte = [r for r in cache_rows if not r['name_has_dte']]
    if not no_dte:
        md.append('_All parquets encode DTE in name._\n')
    else:
        rows = [[r['file'], r.get('rows', '?') or '?', r.get('cols', '?') or '?',
                 r.get('size_mb', '?'),
                 'Y' if r['name_has_version'] else 'N',
                 'Y' if r['name_has_lookback'] else 'N'] for r in no_dte]
        md.append(_md_table(['file', 'rows', 'cols', 'MB', 'has_version', 'has_lookback'], rows))
    md.append('')

    # ─── Section 4: punch list ────────────────────────────────────────────
    md.append('---\n')
    md.append('## 4. Punch List → drives Phases 1–3\n')
    md.append('Concrete next steps, sized to the gaps surfaced above.\n')
    md.append('### 4.1 Drift-guard parity (Phase 1)\n')
    md.append(f'- Add **{gap_drift15_vs_driftmc}** missing fields to `pairs_15` in `tests/test_strategy_config_drift.py` (mirror `pairs_mc` shape).')
    md.append(f'- Import `backtest_cascade_15dte` and add `pairs_bc15` array — currently 0 fields tracked there.')
    md.append('- Replace the SAW-style self-comparison `(s.X, s.X)` checks with real engine-vs-config asserts when the engine wires the constant.')
    md.append('- Add `check_dte_field_parity()` that walks `STRATEGY_30DTE.__dict__` vs `STRATEGY_15DTE.__dict__` — schema parity check, fails if a field exists on one but not the other.')
    md.append('')
    md.append('### 4.2 Mechanism registry (Phase 2)\n')
    md.append(f'- Create `mechanism_registry.py` listing every mechanism. The {gap_mc15_vs_mc30} fields wired in `monte_carlo.py` but NOT `monte_carlo_15dte.py` are the immediate registry rows: each needs a `dte_15` status declaration with a `not_validated_reason`.')
    md.append('- Wire registry into drift-guard so "shipped to 30 DTE without explicit 15 DTE decision" fails CI.')
    md.append('- Auto-generate the `known-issues.md` CURRENT SHIP STATE table from registry.')
    md.append('')
    md.append('### 4.3 Sweep harness hardening (Phase 3)\n')
    md.append(f'- Of {n_total} MC/BC-importing scripts, **{n_no_decl} lack `DTE = N` declaration**. Linter gate to require it.')
    md.append(f'- **{n_15_alias_mc} scripts alias the 15 DTE engine as `mc`** — copy-paste hazard. Convention: `mc` for 30 DTE only, `mc15` for 15 DTE.')
    md.append(f'- **{n_no_holdout} scripts** don\'t reference any holdout helper. Audit each: if it post-dates 2026-05-15, must gate.')
    no_dte_count = len([r for r in cache_rows if not r["name_has_dte"]])
    md.append(f'- **{no_dte_count} cache parquets** lack DTE in filename. If any are MC-output-derived (vs raw signal data), they need renaming or DTE-tagging.')
    md.append('')

    return '\n'.join(md)


# ─── Main ─────────────────────────────────────────────────────────────────


def main() -> int:
    print('[1/3] Inventory: mechanism coverage matrix...', flush=True)
    cov_rows, cov_summary = inventory_mechanism_coverage()
    print(f'      {len(cov_rows)} fields scanned. mc30 consts={cov_summary["mc30_consts"]}, mc15={cov_summary["mc15_consts"]}, bc30={cov_summary["bc30_consts"]}, bc15={cov_summary["bc15_consts"]}', flush=True)

    print('[2/3] Inventory: experiment script DTE declarations...', flush=True)
    exp_rows = inventory_experiments()
    print(f'      {len(exp_rows)} MC/BC-importing scripts found.', flush=True)

    print('[3/3] Inventory: .cache/ parquet naming (DuckDB)...', flush=True)
    cache_rows, cache_summary = inventory_cache_parquets()
    print(f'      {cache_summary.get("count", 0)} parquets scanned. duckdb={cache_summary.get("duckdb_available", "?")}', flush=True)

    md = render_report(cov_rows, cov_summary, exp_rows, cache_rows, cache_summary)
    OUT_PATH.write_text(md, encoding='utf-8')
    print(f'\nWrote {OUT_PATH}  ({len(md):,} chars)', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
