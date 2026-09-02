from peewee import MySQLDatabase, Model
import config

_db_kwargs = dict(config.DB_CONFIGS)
# Prevent indefinite hangs on stalled queries — without these, any MySQL lock
# wait or disk I/O stall blocks the Python process forever.
#
# History:
# - 30s default until 2026-05-07. Triggered cascading 2013 errors during MCD
#   recalc when batch ops (insert_many, full-window SELECTs, historic_peaks
#   365d scan) ran longer than 30s under parallel load.
# - 180s on 2026-05-07 (commit 59a2231) as a band-aid covering all observed
#   batch ops. Worked but masked the underlying issue: queries WITHOUT proper
#   indexes (esp. `SELECT MAX(date) FROM scores WHERE version_id=N`) were
#   running 700ms baseline, exploding to 20+min under contention.
# - 30s restored on 2026-05-08 after `scores_version_date_IDX` index added
#   (CREATE INDEX scores_version_date_IDX ON scores (version_id, date)).
#   That index dropped MAX(date) WHERE version_id=N from 740ms → 1ms,
#   eliminating the dominant slow-query family that was forcing the band-aid.
#   The MCD-era cascading 2013 errors were primarily contention-driven (16
#   concurrent unindexed MAX queries holding locks); without that
#   contention, the original 30s ceiling is sufficient. Any real query
#   exceeding 30s is now a signal to add another index, not raise the
#   timeout ceiling further.
_db_kwargs.setdefault('read_timeout', 30)
_db_kwargs.setdefault('write_timeout', 30)
DB = MySQLDatabase(config.TRADER_DB_NAME, **_db_kwargs)

class UnknownField(object):
    def __init__(self, *_, **__): pass

class BaseModel(Model):
    class Meta:
        database = DB
