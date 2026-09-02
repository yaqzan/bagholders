"""Shared, DB-free helpers for the experiments/newbox_rebaseline runners.

Pure stdlib only (json/os/re/ast/subprocess/sys/time/pathlib/statistics). NEVER
imports database.*, monte_carlo, strategy_config, or peewee -- this whole
package runs offline in a sandbox with no MySQL/peewee (see each runner's
--selftest). Facts about the live engine (window dates, collapse threshold,
calibration cutoff) are extracted by reading the relevant .py files as PLAIN
TEXT (regex / ast.literal_eval) -- never by importing them. This is the
"source-parsing is the offline verification tool" discipline the locked spec
calls for.

Provides:
  - bootstrap_syspath()          -- mirrors apex_dte_dd/run_p03_evidence.py's
                                     HERE/ROOT sys.path pattern.
  - parse_mc_windows() / parse_mc_deep_windows()
                                  -- regex-parse monte_carlo.py's own WINDOWS /
                                     DEEP_WINDOWS tables. Source of truth --
                                     never hardcode these dates elsewhere.
  - parse_collapse_threshold()   -- regex-parses STRATEGY_30DTE's
                                     COLLAPSE_THRESHOLD out of strategy_config.py
                                     (monte_carlo.py's _cfg is always
                                     STRATEGY_30DTE -- "this module is the 30
                                     DTE engine", monte_carlo.py:72).
  - parse_calibration_cutoff()   -- regex-parses CALIBRATION_CUTOFF_DATE out of
                                     strategy_config.py.
  - extract_dict_literal()       -- ast-based "pull the literal dict/set
                                     assigned to NAME at module level" used by
                                     recipes.py's cross-checks against the
                                     canonical experiment files.
  - compute_collapse_pct() / tier_prefix_stats()
                                  -- replicate monte_carlo.py's aggregate math
                                     (~monte_carlo.py:4425-4442) over a raw
                                     finals/dds per-iteration array.
  - run_mc_subprocess()          -- the resume-check + subprocess.run +
                                     log-write pattern shared by
                                     _mc_pinned_runner.run_one_window and our
                                     own custom-window driver (run_noise_floor.py
                                     needs WIN_START/WIN_END, not
                                     WINDOWS_OVERRIDE, so it cannot call
                                     run_one_window directly).
  - classify_metric_delta()      -- BIT_EQUAL / NEAR_EQUAL / DIVERGENT /
                                     MISSING classifier used by run_parity_gate.py.
  - load_json() / write_json() / write_text() / assert_ascii() /
    ensure_fresh_or_resume() / rule_of_three_bound_pct()
                                  -- small IO / safety helpers.
  - SCREEN_NOT_GATE_BANNER / APEX_DEEP_FAIL_ANNOTATION
                                  -- shared doctrine text for run_ecert.py and
                                     run_deep_screen_n1000.py so the wording
                                     never drifts between the two.
"""
from __future__ import annotations

import ast
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # experiments/newbox_rebaseline
ROOT = HERE.parent.parent                        # repo root

MC_PY = ROOT / 'monte_carlo.py'
STRATEGY_CONFIG_PY = ROOT / 'strategy_config.py'


def bootstrap_syspath() -> Path:
    """Ensure repo ROOT (and this dir) are on sys.path. Mirrors the
    apex_dte_dd/run_p03_evidence.py HERE/ROOT pattern so `import
    experiments....` works, plus adds HERE defensively so bare
    `import _common` / `import recipes` always resolves even if something
    unusual about the launch method changed sys.path[0]."""
    for p in (str(ROOT), str(HERE)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return ROOT


# ---------------------------------------------------------------------------
# Source-of-truth parsers.
# ---------------------------------------------------------------------------

_TUPLE_RE = re.compile(
    r"\(\s*'([^']+)'\s*,\s*date\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*,\s*"
    r"date\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*\)"
)


def _extract_bracket_block(text: str, varname: str) -> str:
    """Return the inside of a `<varname> = [ ... ]` block for a line that
    starts with EXACTLY `<varname> = [` at column 0 (so e.g. 'WINDOWS' never
    matches inside 'DEEP_WINDOWS = ['). Raises ValueError if not found."""
    m = re.search(rf'^{re.escape(varname)} = \[(.*?)\n\]', text, re.MULTILINE | re.DOTALL)
    if not m:
        raise ValueError(f"{varname} = [...] block not found in source")
    return m.group(1)


def _parse_window_tuples(block: str) -> list[tuple[str, str, str]]:
    out = []
    for label, y1, m1, d1, y2, m2, d2 in _TUPLE_RE.findall(block):
        start = f'{int(y1):04d}-{int(m1):02d}-{int(d1):02d}'
        end = f'{int(y2):04d}-{int(m2):02d}-{int(d2):02d}'
        out.append((label, start, end))
    return out


def parse_mc_windows(text: str | None = None) -> list[tuple[str, str, str]]:
    """Regex-parse monte_carlo.py's own WINDOWS table (the canonical 12-row
    default list). Returns [(label, start_iso, end_iso), ...] in source
    order. DB-free -- reads monte_carlo.py as plain text, never imports it."""
    text = text if text is not None else MC_PY.read_text(encoding='utf-8')
    return _parse_window_tuples(_extract_bracket_block(text, 'WINDOWS'))


def parse_mc_deep_windows(text: str | None = None) -> list[tuple[str, str, str]]:
    """Regex-parse monte_carlo.py's DEEP_WINDOWS table (the 4-row
    MC_WINDOW_SET=deep screen tier)."""
    text = text if text is not None else MC_PY.read_text(encoding='utf-8')
    return _parse_window_tuples(_extract_bracket_block(text, 'DEEP_WINDOWS'))


def window_date_map(include_deep: bool = True) -> dict:
    """label -> (start_iso, end_iso) for all standard (+ deep, if requested)
    windows, source-parsed fresh each call."""
    out = {label: (start, end) for label, start, end in parse_mc_windows()}
    if include_deep:
        out.update({label: (start, end) for label, start, end in parse_mc_deep_windows()})
    return out


def parse_collapse_threshold(text: str | None = None) -> float:
    """Regex-parse STRATEGY_30DTE's COLLAPSE_THRESHOLD out of
    strategy_config.py. monte_carlo.py's `_cfg = _sc.STRATEGY_30DTE` binding
    is unconditional ("this module is the 30 DTE engine", monte_carlo.py:72),
    so this is the value COLLAPSE_THRESHOLD resolves to regardless of which
    DTE the recipe is emulating via NOMINAL_CAL_DTE."""
    text = text if text is not None else STRATEGY_CONFIG_PY.read_text(encoding='utf-8')
    m = re.search(r'^STRATEGY_30DTE = DteStrategyConfig\((.*?)\n^STRATEGY_15DTE',
                  text, re.MULTILINE | re.DOTALL)
    if not m:
        raise ValueError("STRATEGY_30DTE = DteStrategyConfig(...) block not found in strategy_config.py")
    m2 = re.search(r'COLLAPSE_THRESHOLD\s*=\s*([\d.]+)', m.group(1))
    if not m2:
        raise ValueError("COLLAPSE_THRESHOLD not found inside the STRATEGY_30DTE block")
    return float(m2.group(1))


def parse_calibration_cutoff(text: str | None = None) -> str:
    """Regex-parse CALIBRATION_CUTOFF_DATE (the OOS holdout line) out of
    strategy_config.py. Returns an ISO date string, e.g. '2026-06-15'."""
    text = text if text is not None else STRATEGY_CONFIG_PY.read_text(encoding='utf-8')
    m = re.search(r'CALIBRATION_CUTOFF_DATE\s*:\s*str \| None\s*=\s*"([\d-]+)"', text)
    if not m:
        raise ValueError("CALIBRATION_CUTOFF_DATE not found in strategy_config.py")
    return m.group(1)


def extract_dict_literal(source_text: str, varname: str):
    """Parse a Python source file's text and return the literal object
    (dict/set/list/str/int/float/bool/None/tuple) assigned to top-level name
    `varname`, e.g. `CORE_ENV = {...}`. Uses ast.literal_eval so only
    literal values are supported -- exactly what these recipe dicts are.
    Raises ValueError if no such top-level assignment exists."""
    tree = ast.parse(source_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == varname:
                    return ast.literal_eval(node.value)
    raise ValueError(f"top-level literal assignment to {varname!r} not found")


# ---------------------------------------------------------------------------
# Aggregate-math replication (monte_carlo.py ~4425-4442 / dump ~4639-4671).
# ---------------------------------------------------------------------------

def compute_collapse_pct(finals: list[float], starting_cash: float, collapse_threshold: float) -> float:
    """Replicate monte_carlo.py's collapse predicate:
    `if r['final'] <= STARTING_CASH * COLLAPSE_THRESHOLD: collapses += 1`
    then `p_coll = collapses / N_ITER * 100`. `finals` here is the LIST used
    as the collapse-counting denominator (i.e. N = len(finals))."""
    n = len(finals)
    if n == 0:
        return 0.0
    threshold_usd = starting_cash * collapse_threshold
    collapses = sum(1 for f in finals if f <= threshold_usd)
    return collapses / n * 100.0


def tier_prefix_stats(finals: list[float], dds: list[float], starting_cash: float,
                       n: int, collapse_threshold: float) -> dict:
    """Replicate monte_carlo.py's _simulate_window aggregate math over the
    first `n` entries of a per-iteration paths array. `finals` are raw dollar
    ending equity; `dds` are raw 0-1 drawdown fractions (both as emitted under
    MC_RETURN_PATHS=1) -- see monte_carlo.py's own comments on 'paths.finals'/
    'paths.dds' units (also cross-confirmed in
    experiments/holdout_oos_2026_12/run_h3_envelope.py's inline notes)."""
    fs = list(finals[:n])
    ds = list(dds[:n])
    if not fs or not ds:
        raise ValueError("tier_prefix_stats: empty slice -- n too large or paths arrays empty")
    mean_ret = (statistics.mean(fs) / starting_cash - 1) * 100
    med_ret = (statistics.median(fs) / starting_cash - 1) * 100
    mean_dd = statistics.mean(ds) * 100
    worst_dd = max(ds) * 100
    p_coll = compute_collapse_pct(fs, starting_cash, collapse_threshold)
    return {'mean_ret': mean_ret, 'med_ret': med_ret, 'mean_dd': mean_dd,
            'worst_dd': worst_dd, 'p_coll': p_coll, 'n': len(fs)}


def rule_of_three_bound_pct(n: int) -> float:
    """Rule-of-three approximate 95% upper confidence bound on a true event
    probability when ZERO events were observed in n trials: bound ~= 3/n.
    Returned as a percentage (0-100 scale, matching p_coll's own units)."""
    if n <= 0:
        raise ValueError("n must be positive")
    return 3.0 / n * 100.0


# ---------------------------------------------------------------------------
# Subprocess driver base (shared core of _mc_pinned_runner.run_one_window and
# our own custom WIN_START/WIN_END driver in run_noise_floor.py).
# ---------------------------------------------------------------------------

def run_mc_subprocess(env: dict, out_json_path, timeout_s: int, resume: bool = True) -> dict | None:
    """Resume-check + subprocess.run(monte_carlo.py) + log-write. Never
    raises -- a subprocess failure/timeout is logged and returns None so
    callers can keep going (a resubmit fills in missing cells later)."""
    out_json_path = Path(out_json_path)
    if resume and out_json_path.exists():
        print(f"  [SKIP] {out_json_path} already exists (resume)", flush=True)
        try:
            with open(out_json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"  [WARN] existing {out_json_path} unreadable ({e}); will NOT "
                  f"re-run automatically -- delete it manually to force a re-run.", flush=True)
            return None

    mc_py = str(MC_PY)
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = out_json_path.with_suffix('.log')

    t0 = time.time()
    print(f"  [RUN] -> {out_json_path}", flush=True)
    try:
        proc = subprocess.run([sys.executable, '-u', mc_py], cwd=str(ROOT), env=env,
                              timeout=timeout_s, capture_output=True, text=True)
    except subprocess.TimeoutExpired as e:
        dur = time.time() - t0
        print(f"  [TIMEOUT] {out_json_path.name} after {dur:.0f}s (cap {timeout_s}s) -- "
              f"skipping this cell for now, a resubmit will retry it.", flush=True)
        try:
            with open(log_path, 'w', encoding='utf-8') as lf:
                lf.write((e.stdout or '') if isinstance(e.stdout, str) else '')
                lf.write((e.stderr or '') if isinstance(e.stderr, str) else '')
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"  [ERROR] {out_json_path.name} subprocess launch failed: {e}", flush=True)
        return None

    dur = time.time() - t0
    try:
        with open(log_path, 'w', encoding='utf-8') as lf:
            lf.write(proc.stdout or '')
            lf.write(proc.stderr or '')
    except Exception:
        pass

    if proc.returncode != 0:
        print(f"  [FAIL] {out_json_path.name} rc={proc.returncode} ({dur:.0f}s) -- see {log_path}", flush=True)
        return None
    if not out_json_path.exists():
        print(f"  [FAIL] {out_json_path.name} exited 0 but JSON was not written "
              f"({dur:.0f}s) -- see {log_path}", flush=True)
        return None
    print(f"  [OK] {out_json_path.name} ({dur:.0f}s)", flush=True)
    with open(out_json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Parity diff classifier (run_parity_gate.py).
# ---------------------------------------------------------------------------

METRIC_KEYS_5 = ('mean_ret', 'med_ret', 'worst_dd', 'mean_dd', 'p_coll')

# NEAR_EQUAL thresholds (locked spec, run_parity_gate.py section): worst_dd
# and mean_dd are already stored on the 0-100 percentage-point scale (see
# monte_carlo.py: worst_dd = max(dds) * 100), so "<=0.05pp" means comparing
# directly against 0.05 on that same 0-100 scale (0.05pp == 0.0005 as a 0-1
# fraction -- the spec's parenthetical is a unit-equivalence note, not an
# instruction to re-convert an already-pp-scaled number into a fraction
# first). Flagged in the final report as the one place a wrong unit
# assumption would silently corrupt every verdict.
DD_NEAR_EQUAL_PP = 0.05
RET_NEAR_EQUAL_REL = 1e-3


def classify_metric_delta(archived: dict | None, rerun: dict | None) -> str:
    """BIT_EQUAL / NEAR_EQUAL / DIVERGENT / MISSING for one window cell."""
    if archived is None or rerun is None:
        return 'MISSING'
    try:
        if all(archived[k] == rerun[k] for k in METRIC_KEYS_5):
            return 'BIT_EQUAL'
    except KeyError:
        return 'DIVERGENT'
    try:
        d_worst_dd = abs(rerun['worst_dd'] - archived['worst_dd'])
        d_mean_dd = abs(rerun['mean_dd'] - archived['mean_dd'])
        p_coll_eq = rerun['p_coll'] == archived['p_coll']

        def _rel(a, b):
            denom = abs(a) if a != 0 else 1e-9
            return abs(b - a) / denom

        d_mean_ret_rel = _rel(archived['mean_ret'], rerun['mean_ret'])
        d_med_ret_rel = _rel(archived['med_ret'], rerun['med_ret'])
    except KeyError:
        return 'DIVERGENT'
    if (d_worst_dd <= DD_NEAR_EQUAL_PP and d_mean_dd <= DD_NEAR_EQUAL_PP and p_coll_eq
            and d_mean_ret_rel <= RET_NEAR_EQUAL_REL and d_med_ret_rel <= RET_NEAR_EQUAL_REL):
        return 'NEAR_EQUAL'
    return 'DIVERGENT'


# ---------------------------------------------------------------------------
# Small IO / safety helpers.
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def write_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def write_text(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def assert_ascii(text: str, where: str = '') -> None:
    """Raise AssertionError if `text` contains any non-ASCII character.
    Windows cp1252 consoles crash on unicode -- every artifact this package
    writes must be pure ASCII."""
    try:
        text.encode('ascii')
    except UnicodeEncodeError as e:
        bad = text[max(0, e.start - 20):e.end + 20]
        loc = f" in {where}" if where else ""
        raise AssertionError(f"non-ASCII content{loc}: ...{bad!r}...") from e


def ensure_fresh_or_resume(results_dir, resume: bool) -> None:
    """Refuse to reuse a non-empty results dir unless --resume. A FRESH
    program run gets a clean dir; the per-cell JSON presence under it IS the
    resume mechanism (locked spec hard rule 6)."""
    results_dir = Path(results_dir)
    if results_dir.exists() and any(results_dir.iterdir()) and not resume:
        raise SystemExit(
            f"{results_dir} already exists and is non-empty. Refusing to overwrite "
            f"a fresh program run -- pass --resume to reuse/fill in this directory, "
            f"or remove it manually first (a fresh N always needs a fresh dir -- the "
            f"per-cell JSON resume guard has no N in its path).")
    results_dir.mkdir(parents=True, exist_ok=True)


def print_plan(lines) -> None:
    """One-line-per-item plan summary printed before launching any compute
    (locked spec hard rule 6)."""
    print("=" * 88, flush=True)
    for line in lines:
        print(line, flush=True)
    print("=" * 88, flush=True)


# ---------------------------------------------------------------------------
# Shared doctrine text (run_ecert.py + run_deep_screen_n1000.py).
# ---------------------------------------------------------------------------

SCREEN_NOT_GATE_BANNER = (
    "SCREEN, not GATE -- see assessment-backtest.md 'Deep-window screens (SCREEN, "
    "not GATE)' + gitnexus 6b155033b. A deep FAIL is a mandatory mechanism "
    "investigation, never an automatic revert. A deep PASS is weak comfort, never "
    "collapse-proof. Deep windows ride the survivor-only 1995 v74 score+regime+"
    "breadth backfill -- every number here reads OPTIMISTICALLY."
)

# Sourced from experiments/deep_crash_screen/RESULTS.md (archived N=300 read,
# 2026-07-13): Core collapse=0 on all 4 deep windows; Apex-held collapse=100%
# on dotcom_crash_2000_2002, 20.7% on gfc_crash_2007_2009, 48.3% on 2007_now
# (0% on ltcm_1998). Doctrine conclusion there: documented mechanism
# (capital-velocity law -- "never make the sprint the held default",
# concentration_2x FINDINGS), no new investigation required, mitigated live
# by the 2x watchdog halt-new-entries latch (0b6b778e0).
APEX_DEEP_FAIL_ANNOTATION = (
    "KNOWN-EXPECTED Apex held-form deep-FAIL: the archived N=300 deep screen "
    "(experiments/deep_crash_screen/RESULTS.md, 2026-07-13) found Apex-held "
    "collapse=100% on dotcom_crash_2000_2002, 20.7% on gfc_crash_2007_2009, 48.3% "
    "on 2007_now (0% on ltcm_1998) -- a documented mechanism (capital-velocity law: "
    "'never make the sprint the held default', concentration_2x FINDINGS), no new "
    "investigation required, mitigated live by the 2x watchdog halt-new-entries "
    "latch (0b6b778e0). A comparable N=1000 result on these same cells is the "
    "expected quantification tightening, NOT a new finding -- flag only if "
    "ltcm_1998 (the one archived PASS cell) newly shows collapse>0, or if Core's "
    "collapse stops being exactly 0 on any deep cell."
)
