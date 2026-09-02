#!/usr/bin/env python
"""PRF out-of-sample MC confirm (F proposes, MC disposes).

The PRF coefficients were fit on v70/v71 (the two same-engine hand-tuned
winners), so replaying those substrates only re-validates known configs. The
informative test is a substrate F was NOT fit on: honest-recalced **v60**
(supply 5.6/day, recycle coverage 0.652 -> a starved book like v70).

There, F derives the v70-style config (low 0.10, overflow 0.035) while the
static current canon carries the v71-fitted c14 (low 0.05, overflow 0). If F's
adaptation thesis is right, the matched config should beat-or-tie the static
one on the starved substrate. If the static config wins, the supply-response
coefficients do not generalize and PRF stays a display instrument.

Arms (N=300, windows 2022 / 22-now / 5y, scores pinned to v60):
  A  static_canon : no overrides -> current strategy_config (c14, low 0.05/ovf 0)
  B  prf_matched  : TIER_*_OV from F(phi_v60) computed live by portfolio_response

CAVEAT: the v60 honest recalc covered ~5y back from 2026-06-01, so the MC '5y'
window may include a small pre-honest seam at its left edge; gate the reading on
2022 + 22-now (fully honest) and treat 5y as secondary.

Logs: .cache/version_scorecard/prf_confirm_v60/<arm>.log + verdict hints at end.
Queue it (db heavy):
  trader queue submit --priority high --db heavy --cpu 4 --restartable \
    --dedup prf-mc-confirm-v60 -- python -u experiments/version_scorecard/prf_mc_confirm.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parents[1]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portfolio_response import load_phi, derive  # noqa: E402

SUBSTRATE = "v60"
WINDOWS = "2022,22-now,5y"
N_ITER = "300"
OUT_DIR = ROOT / ".cache" / "version_scorecard" / "prf_confirm_v60"


def resolve_pin(version_token):
    from algorithm_versions import manager
    v = manager.resolve_algorithm_version(version_token, create_current=False)
    return str(v.git_commit), int(v.id)


def run_arm(name, tier_env, pin):
    env = dict(os.environ)
    env.update({
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
        "ALGORITHM_VERSION_PIN": pin,
        "N_ITER_OVERRIDE": N_ITER,
        "WINDOWS_OVERRIDE": WINDOWS,
    })
    env.update(tier_env)
    log = OUT_DIR / f"{name}.log"
    print(f"[{name}] overrides={tier_env or 'none (current canon)'} -> {log}", flush=True)
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.call([sys.executable, "-u", str(ROOT / "monte_carlo.py")],
                             stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=str(ROOT))
    print(f"[{name}] exit={rc}", flush=True)
    return rc, log


def tail_summary(log, keep=("Med", "DD", "collapse", "Collapse", "WINDOW", "window",
                            "2022", "22-now", "5y", "Real", "Worst")):
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    pat = re.compile("|".join(re.escape(k) for k in keep))
    return [ln for ln in lines[-200:] if pat.search(ln)][-40:]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pin, vid = resolve_pin(SUBSTRATE)
    print(f"substrate {SUBSTRATE}: pin={pin} (db id {vid})", flush=True)

    phi = load_phi(SUBSTRATE)
    if phi is None or phi.get("supply_approx"):
        sys.exit(f"{SUBSTRATE}: phi unavailable/approximated — run signal_supply.py first")
    d = derive(phi)
    t = d["tier_alloc"]
    print(f"F(phi_{SUBSTRATE}) = ladder {t['ultra']}/{t['top']}/{t['mid']}/{t['low']} "
          f"ovf {d['tier_overflow']} thresh {d['min_call_threshold']}+", flush=True)

    arms = [
        ("static_canon", {}),
        ("prf_matched", {
            "TIER_ULTRA_OV": str(t["ultra"]), "TIER_TOP_OV": str(t["top"]),
            "TIER_MID_OV": str(t["mid"]), "TIER_LOW_OV": str(t["low"]),
            "TIER_OVERFLOW_OV": str(d["tier_overflow"] or 0.0),
        }),
    ]
    rc_all = 0
    logs = []
    for name, env in arms:
        rc, log = run_arm(name, env, pin)
        rc_all |= rc
        logs.append((name, log))

    print("\n" + "=" * 80)
    print("PRF OOS CONFIRM — summary hints (full tables in the logs)")
    print("Read: gate on 2022 + 22-now (fully honest); 5y has a left-edge seam.")
    print("=" * 80)
    for name, log in logs:
        print(f"\n--- {name} ({log}) ---")
        for ln in tail_summary(log):
            print(f"  {ln}")
    sys.exit(rc_all)


if __name__ == "__main__":
    main()
