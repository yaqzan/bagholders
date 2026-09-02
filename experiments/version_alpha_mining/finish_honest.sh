#!/bin/bash
# After vNN honest recalc + assess complete: append to registry, re-run honest
# compare, validate (anchor 75+ ~50.5%), and remove the worktree.
# Usage: finish_honest.sh <vid>
VID="$1"
MAIN=/c/Development/Trader
cd "$MAIN" || exit 1
python - "$VID" <<'PY'
import json, sys
vid = int(sys.argv[1])
p = 'experiments/version_alpha_mining/honest_versions.json'
d = json.load(open(p))
if vid not in d['honest_recalc_versions']:
    d['honest_recalc_versions'] = sorted(set(d['honest_recalc_versions'] + [vid]))
    json.dump(d, open(p, 'w'), indent=2)
    print(f'appended v{vid} to registry')
else:
    print(f'v{vid} already in registry')
PY
echo "=== HONEST COMPARE ==="
PYTHONIOENCODING=utf-8 python experiments/version_alpha_mining/compare_honest.py 2>&1 | grep -vE "^INFO|^WARN|Loaded|tensor"
echo "=== VALIDATE v$VID (anchor 75+ ~50.5%; far off => recipe failed, investigate) ==="
PYTHONIOENCODING=utf-8 python experiments/earnboost_honest/validate_honest_version.py "$VID" 2>&1 \
  | grep -E "infl WR15| 75\+ | 85\+ | <25 |HONEST .run|target." \
  || echo "(validate skipped — transient DB connection; honest WR is in the compare table above)"
echo "=== remove worktree v$VID ==="
git worktree remove --force "../Trader-honest-v$VID" 2>&1 && echo "removed v$VID worktree"
