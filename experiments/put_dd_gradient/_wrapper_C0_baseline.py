
import os, sys, math
sys.path.insert(0, r"C:\Development\Trader")

K = 0.0
SCORE_GATE = 25
TARGET = 0
DEPTH_SHAPE = "linear"

def depth_factor(overall):
    """Returns 0..1 depth multiplier. Larger for deeper (lower) overall."""
    if overall >= SCORE_GATE: return 0.0
    if SCORE_GATE <= 0: return 0.0
    if DEPTH_SHAPE == "linear":
        return max(0.0, min(1.0, (SCORE_GATE - overall) / SCORE_GATE))
    elif DEPTH_SHAPE == "log":
        depth = max(0, SCORE_GATE - overall)
        return max(0.0, min(1.0, math.log(depth + 1.0) / math.log(SCORE_GATE + 1.0)))
    else:
        return 0.0

def apply_lift(overall):
    if K <= 0: return overall
    if overall > 25: return overall  # puts only
    f = depth_factor(overall)
    if f <= 0: return overall
    new_ov = overall + K * f * (TARGET - overall)
    new_ov = int(round(max(0, min(100, new_ov))))
    return new_ov

# Patch load_put_signals BEFORE monte_carlo loads it
import monte_carlo as mc

_orig_load_put = mc.load_put_signals

def _patched_load_put(version, d_start, d_end):
    # Load broader range so lifted-up puts that exit <=25 still re-qualify
    sigs = _orig_load_put(version, d_start, d_end)
    out = []
    for s in sigs:
        new_ov = apply_lift(int(s.overall))
        if new_ov > mc.PUT_THRESHOLD: continue  # lifted out of put cascade
        s.overall = new_ov
        out.append(s)
    out.sort(key=lambda s: (s.date, s.overall))
    return out

mc.load_put_signals = _patched_load_put

# Force baseline by setting K=0 (no patch needed but applied identity)

if __name__ == "__main__":
    mc.main()
