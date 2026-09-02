#!/bin/bash
# Prepare an honest-recalc worktree for version $1 at commit $2.
# Worktree -> pin ALGORITHM_VERSION -> cherry-pick v69 weekly fix (8b59206c3) ->
# version-original simulator.py (recalc path = core.py only) -> resolve core.py
# conflicts (resolve_weekly_conflict.py v2) -> verify R1-R4 -> CONTAMINATION GATE.
# Prints SAFE_GATE only if id==VID. Recalc launched separately (serial).
set -u
VID="$1"; COMMIT="$2"
MAIN=/c/Development/Trader
WT="/c/Development/Trader-honest-v$VID"
FIX=8b59206c3

if [ -d "$WT" ]; then
  echo "[v$VID] worktree exists -> hard reset to $COMMIT"
  git -C "$WT" reset --hard "$COMMIT" >/dev/null 2>&1
else
  git -C "$MAIN" worktree add "$WT" "$COMMIT" >/dev/null 2>&1 && echo "[v$VID] worktree added @ $COMMIT"
fi
printf '%s' "$COMMIT" > "$WT/ALGORITHM_VERSION"
git -C "$WT" cherry-pick -n -x "$FIX" >/dev/null 2>&1; echo "[v$VID] cherry-pick rc=$? (1=conflict expected)"
# version-original simulator (recalc never uses ScoreSimulator; guarantees compile)
git -C "$WT" checkout HEAD -- simulator.py 2>/dev/null
# scoring.py holds the blend formula — it auto-merges on every version tested so
# far; if it ever CONFLICTS the recalc import breaks, so abort loudly.
if grep -q '<<<<<<<' "$WT/database/utils/scoring.py" 2>/dev/null; then
  echo "[v$VID] !!! scoring.py CONFLICTED (blend formula) — resolve manually before recalc"; exit 4
fi
# resolve core.py
python "$MAIN/experiments/version_alpha_mining/resolve_weekly_conflict.py" "$WT" database/models/core.py
# strip v68-era feature imports (kijun/wvf) that 'take THEIRS' bundles into pre-v46
# silos whose scoring.py predates them (dead code; else 'cannot import name' at
# recalc runtime -> 0 rows; py_compile does NOT catch the lazy import).
python "$MAIN/experiments/version_alpha_mining/strip_undefined_scoring_imports.py" "$WT" \
  || { echo "[v$VID] !!! strip left stray refs / broke compile — INVESTIGATE"; exit 5; }
CORE="$WT/database/models/core.py"
R1=$(grep -c '_pit_weekly_map = _build_pit_map' "$CORE")
R3=$(grep -c '_pit_wk = _pit_weekly_map.get' "$CORE")
# R4 = pit args reach compute_overall_score, either inline (resolver inject on conflicted
# silos) OR dict form 'pit_ws_trend': _pit_ws_trend (auto-merged recent silos, v68 template).
R4=$(grep -cE "pit_ws_trend=_pit_ws_trend|'pit_ws_trend': _pit_ws_trend" "$CORE")
echo "[v$VID] recalc-path R1=$R1 R3=$R3 R4=$R4 (need each >=2)"
if [ "$R1" -lt 2 ] || [ "$R3" -lt 2 ] || [ "$R4" -lt 2 ]; then
  echo "[v$VID] !!! RECALC-PATH INCOMPLETE — INVESTIGATE before recalc"; exit 3
fi
# contamination gate (must run from worktree)
cd "$WT" && PYTHONIOENCODING=utf-8 python -c "
from database.models.core import AlgorithmVersion as A
c=A.get_or_create_current()
assert c.id==$VID, 'WRONG id=%s commit=%s — ABORT'%(c.id,c.git_commit)
print('[v$VID] SAFE_GATE id=%s commit=%s'%(c.id,c.git_commit))
" 2>&1 | grep -vE "^INFO|^WARN|Loaded|tensor" | tail -2
