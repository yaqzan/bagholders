"""Run mc_wvd.py with the v44-milder constants (K=0.20, gate 70-95, target 60).
Less aggressive — fewer signals dampened — may preserve portfolio compound while
still capturing some per-trade quality gain.
"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Override constants by editing mc_wvd's module-level vars before main()
import experiments.weekly_volume.mc_wvd as mod
mod.WVD_GATE_HI    = 95
mod.WVD_K_CALL     = 0.20
mod.WVD_K_POWER    = 1.0
mod.WVD_FORCE_SAT  = 0.15
mod.WVD_TARGET_CALL = 60.0
mod.WVD_PUT_LO     = 5
mod.WVD_K_PUT      = 0.50
mod.WVD_CMF_SAT    = 0.30


if __name__ == '__main__':
    mod.main()
