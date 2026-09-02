"""Smoke: confirm the capture_inputs block reproduces simulate() exactly.

For each captured (sym,date), compute_overall_score(*args, **kwargs) MUST equal the overall
that simulate() returned. If so, the fast re-score path is faithful by construction at default
weights (and any divergence at other weights is purely the weight change, as intended).
"""
import os, sys
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from simulator import ScoreSimulator
from database.utils.scoring import compute_overall_score

SYMS = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "NFLX", "CRM"]

sim = ScoreSimulator(symbols=SYMS, lookback_days=700)
sim.capture_inputs = True
sim.capture_min_overall = 60
scores = sim.simulate()
print("scored=%d  captured=%d" % (len(scores), len(sim.inputs_by_key)))

mism = 0
checked = 0
examples = []
for (sym, d), (args, kw) in sim.inputs_by_key.items():
    ov_sim = scores.get((sym, d))
    ov_re, _, _ = compute_overall_score(*args, **kw)
    checked += 1
    if ov_re != ov_sim:
        mism += 1
        if len(examples) < 8:
            examples.append((sym, str(d), ov_sim, ov_re))

print("checked=%d  mismatches=%d" % (checked, mism))
for e in examples:
    print("  MISMATCH", e)
print("RESULT:", "PASS — capture reproduces simulate exactly" if mism == 0
      else "FAIL — capture/kwargs diverge from simulate")
