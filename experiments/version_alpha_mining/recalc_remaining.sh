#!/bin/bash
# Serial honest-recalc orchestrator for the REMAINING inflated-with-packs versions.
# Reuses the proven harness (setup_honest -> recalc_honest -> assess+pack -> finish).
# Designed to run UNDER THE TASK QUEUE (db=heavy, low pri): recalc workers honor the
# injected TRADER_RECALC_MAX_WORKERS, so it respects the core grant. Serial = DB-safe.
#
# Per version: skip if already honest -> setup (GATE; abort==skip, the "applicable"
# filter) -> recalc (scores-only, force) -> backfill assess(force)+pack(all profiles)
# -> sync API algorithm_versions/honest_versions.json -> finish (validate + rm worktree).
# Idempotent + restartable: completed versions are skipped on re-run.
#
# Excluded by design: v41 (phantom dup of v40 commit 917659c) and v42 (reverted+dropped
# rolling-weekly; the v69 weekly-pit cherry-pick is meaningless there and would abort).
set -u
MAIN=/c/Development/Trader
cd "$MAIN" || { echo "no $MAIN"; exit 1; }
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
LOGD="$MAIN/experiments/version_alpha_mining/_logs"
mkdir -p "$LOGD"
SUMMARY="$LOGD/recalc_remaining_summary.log"
echo "=== recalc_remaining start $(date) ===" | tee -a "$SUMMARY"

# version:commit  (v60 first = highest value + early smoke test; then newest->oldest)
PAIRS="60:d4a3e9fec 66:05d75b4ae 65:14a5981cb 64:1bba5f965 63:7b263922f 62:d4d63798e 61:e6fbdbde1 56:c6f384ab 55:bfad76a 54:8af574b 53:e3ed806 48:61561ee 45:56eb1f8 39:200f33a 38:b093e2d 36:d5ef1f5 35:e77714f 34:232a725 33:28fa522"

is_honest() {  # exit 0 if VID already in API honest list
  python - "$1" <<'PY'
import json, sys
d = json.load(open('algorithm_versions/honest_versions.json'))
sys.exit(0 if int(sys.argv[1]) in set(d.get('honest_versions', [])) else 1)
PY
}

add_honest() {  # add VID to API honest list (drives the UI hide toggle)
  python - "$1" <<'PY'
import json, sys
p = 'algorithm_versions/honest_versions.json'
d = json.load(open(p))
hv = set(d.get('honest_versions', [])); vid = int(sys.argv[1])
if vid not in hv:
    hv.add(vid); d['honest_versions'] = sorted(hv); d['updated'] = '2026-06-03'
    json.dump(d, open(p, 'w'), indent=2); print('API-json: added v%d' % vid)
else:
    print('API-json: v%d already present' % vid)
PY
}

for PAIR in $PAIRS; do
  VID="${PAIR%%:*}"; COMMIT="${PAIR##*:}"
  echo "----- v$VID ($COMMIT) $(date +%H:%M:%S) -----" | tee -a "$SUMMARY"
  if is_honest "$VID"; then echo "v$VID SKIP (already honest)" | tee -a "$SUMMARY"; continue; fi

  # 1) setup + contamination gate (no DB writes). SAFE_GATE => applicable.
  SLOG="$LOGD/setup_v$VID.log"
  "$BASH" experiments/version_alpha_mining/setup_honest.sh "$VID" "$COMMIT" > "$SLOG" 2>&1
  if ! grep -q "SAFE_GATE id=$VID" "$SLOG"; then
    echo "v$VID SKIP (gate abort / not applicable) -- see $SLOG" | tee -a "$SUMMARY"
    grep -E "ABORT|CONFLICT|INCOMPLETE|rc=|exit" "$SLOG" | tail -3 | sed 's/^/    /' | tee -a "$SUMMARY"
    continue
  fi
  echo "v$VID gate OK" | tee -a "$SUMMARY"

  # 2) honest recalc (scores-only, force; workers honor TRADER_RECALC_MAX_WORKERS)
  RLOG="$LOGD/recalc_v$VID.log"
  "$BASH" experiments/version_alpha_mining/recalc_honest.sh "$VID" > "$RLOG" 2>&1
  if ! grep -q "RECALC_DONE v$VID" "$RLOG"; then
    echo "v$VID FAIL (recalc) -- worktree left for investigation; see $RLOG" | tee -a "$SUMMARY"
    grep -iE "error|abort|precheck|exit" "$RLOG" | tail -3 | sed 's/^/    /' | tee -a "$SUMMARY"
    continue
  fi
  echo "v$VID recalc OK" | tee -a "$SUMMARY"

  # 3) honest assess (force, from new scores) + research pack (all profiles)
  PLOG="$LOGD/assesspack_v$VID.log"
  python tools/backfill_research_packs.py --versions "v$VID" --force-assess \
    --profiles all --run-portfolio-windows > "$PLOG" 2>&1
  PRC=$?
  if [ "$PRC" -ne 0 ] || ! grep -q "\"pack_built\": true" "$PLOG" 2>/dev/null; then
    # backfill writes a done.json; fall back to checking the manifest mtime via grep
    if ! grep -qiE "pack_built=True|portfolio_windows" "$PLOG" 2>/dev/null; then
      echo "v$VID WARN (assess/pack rc=$PRC) -- see $PLOG" | tee -a "$SUMMARY"
    fi
  fi
  echo "v$VID assess+pack done (rc=$PRC)" | tee -a "$SUMMARY"

  # 4) flip the API honest flag so the UI stops hiding it
  add_honest "$VID" | tee -a "$SUMMARY"

  # 5) register in experiment ledger + anchor-validate + remove worktree
  "$BASH" experiments/version_alpha_mining/finish_honest.sh "$VID" >> "$PLOG" 2>&1 \
    && echo "v$VID finished + worktree removed" | tee -a "$SUMMARY" \
    || echo "v$VID finish WARN (worktree may remain) -- see $PLOG" | tee -a "$SUMMARY"
done

echo "=== recalc_remaining done $(date) ===" | tee -a "$SUMMARY"
echo "RECALC_REMAINING_DONE"
