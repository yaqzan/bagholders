#!/usr/bin/env bash
# Rebuild research packs for the honest-recalc'd versions with Apex-70%SL portfolio
# windows. SERIAL (disk/MySQL-bound). v70 validated separately; this does the rest,
# newest->oldest so the most relevant versions surface first.
set -u
cd /c/Development/Trader
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
LOGD=experiments/version_alpha_mining/_logs
mkdir -p "$LOGD"
# v70 done separately; remaining honest versions, relevance order:
VERS="${VERS:-69 68 59 58 57 52 50 46 44 43 40 37 32 27}"
for V in $VERS; do
  echo "[$(date +%H:%M:%S)] pack v$V start"
  python tools/build_research_pack.py --version "v$V" --profiles apex --run-portfolio-windows \
    > "$LOGD/pack_v$V.log" 2>&1
  rc=$?
  tailmsg=$(grep -iE "wrote|manifest|portfolio_windows|error|traceback" "$LOGD/pack_v$V.log" 2>/dev/null | tail -1 | tr -d '\r')
  echo "[$(date +%H:%M:%S)] pack v$V exit $rc :: $tailmsg"
done
echo "REBUILD_PACKS_DONE"
