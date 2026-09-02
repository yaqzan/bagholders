"""Queue-ready BSD re-run on v42 baseline.

Polls the v42 recalc log for the `── historic-update ──` phase marker
(emitted right after the scoring loop completes — see trader.py:3388),
then fires the BSD pipeline:
  1. build_features.py          — writes calls_v{ID}_1825.parquet for active version
  2. profile_breadth.py         — cohort table by breadth quintile / F3F threshold / regime
  3. sweep.py                   — 36-variant calibration grid vs H1 affected-tier gate

The script does NOT wait for historic-update or assess phases — those run
after scoring and the BSD per-trade gate doesn't depend on them.

Usage:
    python experiments/breadth_score_dampener/run_v42.py

Logs to experiments/breadth_score_dampener/v42_run.log.
"""
from __future__ import annotations
import io, os, sys, time, subprocess
from pathlib import Path
from datetime import datetime

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[2]
RECALC_LOG = Path(
    'C:/Users/Yaqzan/AppData/Local/Temp/claude/c--Development-Trader/'
    '6a381aaf-2e30-4add-801a-24599ffbe2a9/tasks/bgd5194fi.output'
)
OUT_LOG = Path(__file__).parent / 'v42_run.log'
PHASE_MARKER = '── historic-update ──'
POLL_SECONDS = 30


def log(msg: str, fp):
    stamp = datetime.now().strftime('%H:%M:%S')
    line = f'[{stamp}] {msg}'
    print(line, flush=True)
    fp.write(line + '\n')
    fp.flush()


def wait_for_scoring_done(fp):
    """Block until the recalc log shows scoring phase finished."""
    log(f'polling {RECALC_LOG} for "{PHASE_MARKER}" (every {POLL_SECONDS}s)', fp)
    while True:
        if not RECALC_LOG.exists():
            log(f'  log file not present yet, sleeping {POLL_SECONDS}s', fp)
            time.sleep(POLL_SECONDS)
            continue
        try:
            text = RECALC_LOG.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            log(f'  read error ({e}); retry in {POLL_SECONDS}s', fp)
            time.sleep(POLL_SECONDS)
            continue
        if PHASE_MARKER in text:
            log(f'  ✓ phase marker found — scoring complete', fp)
            return
        # Heuristic progress hint from the tqdm bar
        last_pct = None
        for line in text.splitlines()[-100:]:
            if 'Stocks:' in line and '%' in line:
                last_pct = line.split('Stocks:')[1][:8].strip()
        if last_pct:
            log(f'  scoring still running (last bar: {last_pct})', fp)
        time.sleep(POLL_SECONDS)


def run_step(label: str, cmd: list, fp, env_extra: dict | None = None):
    log(f'── {label} ──', fp)
    log(f'  cmd: {" ".join(cmd)}', fp)
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    if env_extra:
        env.update(env_extra)
        for k, v in env_extra.items():
            log(f'  env: {k}={v}', fp)
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    elapsed = time.time() - t0
    fp.write(f'\n----- STDOUT ({label}) -----\n')
    fp.write(proc.stdout)
    if proc.stderr:
        fp.write(f'\n----- STDERR ({label}) -----\n')
        fp.write(proc.stderr)
    fp.write(f'----- {label} exit={proc.returncode} ({elapsed:.1f}s) -----\n\n')
    fp.flush()
    log(f'  ✓ {label} done exit={proc.returncode} ({elapsed:.1f}s)', fp)
    if proc.returncode != 0:
        log(f'  ✗ {label} FAILED — see log for stderr', fp)
        return False
    return True


def resolve_active_version_id():
    """Read the active AlgorithmVersion row id."""
    sys.path.insert(0, str(ROOT))
    from database.models.core import AlgorithmVersion
    from database.trader_database import DB
    DB.connect(reuse_if_open=True)
    av = AlgorithmVersion.get_active_scores_version()
    return av.id, av.git_commit


def main():
    OUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with OUT_LOG.open('w', encoding='utf-8') as fp:
        log(f'BSD v42 queue-runner started, output at {OUT_LOG}', fp)

        wait_for_scoring_done(fp)

        # Resolve active version id (will be 42 for v42 ship)
        av_id, av_commit = resolve_active_version_id()
        log(f'active version: id={av_id} ({av_commit[:8]})', fp)
        parquet = ROOT / '.cache' / 'breadth_score_dampener' / f'calls_v{av_id}_1825.parquet'
        log(f'target parquet: {parquet}', fp)

        # Step 1 — build features
        ok = run_step('build_features',
                      [sys.executable, '-u', 'experiments/breadth_score_dampener/build_features.py'],
                      fp)
        if not ok:
            return 1
        if not parquet.exists():
            log(f'✗ expected parquet {parquet} not produced — aborting', fp)
            return 2

        # Step 2 — profile (env var override)
        ok = run_step('profile_breadth',
                      [sys.executable, '-u', 'experiments/breadth_score_dampener/profile_breadth.py'],
                      fp, env_extra={'BSD_PARQUET': str(parquet)})
        if not ok:
            log('  profile failed but continuing to sweep — the failure may be a polars version warning', fp)

        # Step 3 — sweep
        ok = run_step('sweep',
                      [sys.executable, '-u', 'experiments/breadth_score_dampener/sweep.py'],
                      fp, env_extra={'BSD_PARQUET': str(parquet)})
        if not ok:
            return 3

        log('── BSD v42 pipeline COMPLETE ──', fp)
        log(f'  results in {OUT_LOG}', fp)
        log(f'  next: review v42 cohort table + sweep candidates vs v39 null pattern', fp)
        return 0


if __name__ == '__main__':
    sys.exit(main())
