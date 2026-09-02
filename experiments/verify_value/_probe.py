"""Readiness probe for the verify_value initiative (Phase 1 Murphy/REV + Phase 2 ensemble).

Read-only. Reports: recent AlgorithmVersion rows, per-version score-row coverage
(can we build a v70..v74 ensemble on common keys?), and the cached 70+ components
cohort size (the Phase-1 band decomposition + Phase-2 components-logistic backbone).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.trader_database import DB
from database.models.core import AlgorithmVersion

print("=== recent AlgorithmVersion rows ===", flush=True)
DB.execute_sql("SET SESSION MAX_EXECUTION_TIME=120000")
rows = DB.execute_sql(
    "SELECT id, git_commit, LEFT(git_message,60) FROM algorithm_versions "
    "WHERE id >= 68 ORDER BY id"
).fetchall()
for r in rows:
    print(f"  v{r[0]:<3} {str(r[1])[:10]:<12} {r[2]}", flush=True)
active = AlgorithmVersion.get_active_scores_version()
print(f"  ACTIVE = v{active.id} ({str(active.git_commit)[:10]})", flush=True)

print("\n=== score-row coverage per version (69..74) ===", flush=True)
cov = DB.execute_sql(
    "SELECT version_id, COUNT(*), MIN(date), MAX(date), "
    "SUM(CASE WHEN overall>=70 THEN 1 ELSE 0 END) "
    "FROM scores WHERE version_id BETWEEN 69 AND 74 GROUP BY version_id ORDER BY version_id"
).fetchall()
print(f"  {'ver':>4} | {'rows':>10} | {'min':>10} | {'max':>10} | {'70+ rows':>9}")
for v, n, mn, mx, n70 in cov:
    print(f"  v{v:>3} | {n:>10,} | {str(mn):>10} | {str(mx):>10} | {int(n70):>9,}")

# common-key overlap for the ensemble (v70..v74, the distinct lineages)
print("\n=== ensemble common-key overlap (70+ scores, where ALL of v70/v71/v73/v74 agree on a key) ===", flush=True)
try:
    ov = DB.execute_sql(
        "SELECT COUNT(*) FROM ("
        "  SELECT symbol, date FROM scores WHERE version_id=74 AND overall>=70 AND date>='2016-01-01'"
        ") t "
        "JOIN scores s70 ON s70.symbol=t.symbol AND s70.date=t.date AND s70.version_id=70 "
        "JOIN scores s71 ON s71.symbol=t.symbol AND s71.date=t.date AND s71.version_id=71 "
        "JOIN scores s73 ON s73.symbol=t.symbol AND s73.date=t.date AND s73.version_id=73"
    ).fetchall()
    print(f"  v74-70+ keys also present in v70 AND v71 AND v73: {int(ov[0][0]):,}", flush=True)
except Exception as e:
    print(f"  (overlap query failed: {e})", flush=True)

print("\n=== cached 70+ components cohort (ensemble_spread_v74_calls_2016) ===", flush=True)
from database.bulk_cache import cache_path
p = cache_path("ensemble_spread_v74_calls_2016")
if p.exists():
    import polars as pl
    df = pl.read_parquet(p)
    print(f"  EXISTS: {df.height:,} rows, cols={df.columns}", flush=True)
else:
    print("  MISSING -> will build via spread_skill.py pull (70+ w/ components)", flush=True)
