#!/bin/bash
# Launch the honest scores-only recalc for a prepped worktree (serial; background).
# 1-stock PRECHECK first (catches dangling-feature NameError pollution before the
# ~38-min full run), then re-assert the contamination gate, then full recalc via
# recalculate_scores.run directly (no CLI tail) from the worktree.
# Usage: bash recalc_honest.sh <vid>   (redirect to a log; background it)
VID="$1"
WT="/c/Development/Trader-honest-v$VID"
cd "$WT" || { echo "no worktree $WT"; exit 1; }

PRECHK=$(PYTHONIOENCODING=utf-8 python -c "
from database.models.core import AlgorithmVersion as A
assert A.get_or_create_current().id==$VID, 'WRONG id'
from recalculate_scores import run, DAILY_COMPONENTS
run(symbol='AAPL', days=2400, components=DAILY_COMPONENTS.copy(), workers=1, use_batch=True, force=True)
" 2>&1 | grep -oE "[0-9]+ errors" | tail -1)
if [ "$PRECHK" != "0 errors" ]; then
  echo "PRECHECK FAILED for v$VID ($PRECHK) — ABORT full recalc (no rows written)"; exit 9
fi
echo "v$VID precheck OK ($PRECHK)"

PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -u -c "
from database.models.core import AlgorithmVersion as A
c = A.get_or_create_current()
assert c.id == $VID, 'WRONG VERSION id=%s commit=%s — ABORT' % (c.id, c.git_commit)
print('SAFE recalc target: v%s (%s)' % (c.id, c.git_commit), flush=True)
from recalculate_scores import run, DAILY_COMPONENTS
run(symbol=None, days=2400, components=DAILY_COMPONENTS.copy(), workers=None, use_batch=True, force=True)
print('RECALC_DONE v$VID', flush=True)
"
echo "recalc_honest v$VID exit=$?"
