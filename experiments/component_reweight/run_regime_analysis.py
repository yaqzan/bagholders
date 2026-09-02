"""Note 2 inline analysis: what regime/breadth/VIX environments do Apex DRAWDOWNS vs EXPLOSIONS happen
in? Joins the deterministic apex+overflow daily equity/dd with per-date regime/VIX/breadth, identifies
DD episodes + explosion windows, and derives the separator + where the regime knobs are flat.
(Recovers the failed-workflow analysis from its built dataset.)
"""
import json, statistics as st
_DS = "experiments/component_reweight/regime_env_dataset.json"
d = json.load(open(_DS))
eq = d["backtest"]["daily_equity"]                 # [{date, equity, dd}]
env = d["market_environment"]                       # {date: {regime_composite, regime_multiplier, vix_close, breadth_score}}

# join + rolling return
rows = []
for i, e in enumerate(eq):
    m = env.get(e["date"])
    if not m:
        continue
    rows.append({"date": e["date"], "equity": e["equity"], "dd": e["dd"],
                 "vix": m.get("vix_close"), "brd": m.get("breadth_score"),
                 "reg": m.get("regime_composite"), "regmult": m.get("regime_multiplier")})
print("joined days:", len(rows), "of", len(eq), "equity days")

# rolling 20-trading-day forward log-return (explosion proxy) — on the JOINED series index
import math
N = len(rows)
fwd = [None] * N
for i in range(N):
    j = min(i + 20, N - 1)
    if rows[i]["equity"] > 0 and rows[j]["equity"] > 0:
        fwd[i] = math.log(rows[j]["equity"] / rows[i]["equity"])


def dist(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "n/a"
    return "med=%.1f mean=%.1f p10=%.1f p90=%.1f" % (
        st.median(vals), sum(vals) / len(vals),
        sorted(vals)[max(0, int(0.1 * len(vals)))], sorted(vals)[min(len(vals) - 1, int(0.9 * len(vals)))])


# baseline environment
print("\n=== BASELINE environment (all joined days) ===")
print("  VIX    ", dist([r["vix"] for r in rows]))
print("  breadth", dist([r["brd"] for r in rows]))
print("  regime ", dist([r["reg"] for r in rows]))

# --- DRAWDOWN environment: days where the strategy is in a deep drawdown (dd >= 0.40) ---
in_dd = [r for r in rows if r["dd"] is not None and r["dd"] >= 0.40]
deep_dd = [r for r in rows if r["dd"] is not None and r["dd"] >= 0.60]
print("\n=== DRAWDOWN environment ===")
print("  days in dd>=0.40: %d (%.1f%%)  | dd>=0.60: %d (%.1f%%)" % (
    len(in_dd), 100 * len(in_dd) / len(rows), len(deep_dd), 100 * len(deep_dd) / len(rows)))
print("  [dd>=0.40] VIX    ", dist([r["vix"] for r in in_dd]))
print("  [dd>=0.40] breadth", dist([r["brd"] for r in in_dd]))
print("  [dd>=0.40] regime ", dist([r["reg"] for r in in_dd]))
print("  [dd>=0.60] VIX    ", dist([r["vix"] for r in deep_dd]))
print("  [dd>=0.60] breadth", dist([r["brd"] for r in deep_dd]))

# worst DD episodes (contiguous runs of rising dd to a trough)
peak_eq = 0.0
episodes = []
cur = None
for r in rows:
    if r["equity"] >= peak_eq:
        peak_eq = r["equity"]
        if cur and cur["max_dd"] >= 0.45:
            episodes.append(cur)
        cur = None
    else:
        if cur is None:
            cur = {"start": r["date"], "max_dd": 0.0, "vix": [], "brd": [], "reg": [], "trough": r["date"]}
        if r["dd"] > cur["max_dd"]:
            cur["max_dd"] = r["dd"]; cur["trough"] = r["date"]
        cur["vix"].append(r["vix"]); cur["brd"].append(r["brd"]); cur["reg"].append(r["reg"]); cur["end"] = r["date"]
if cur and cur["max_dd"] >= 0.45:
    episodes.append(cur)
episodes.sort(key=lambda e: -e["max_dd"])
print("\n=== WORST DD EPISODES (max_dd>=0.45) ===")
print("  %-12s %-12s %6s  %7s %8s %7s" % ("start", "trough", "maxDD", "avgVIX", "avgBrd", "avgReg"))
for e in episodes[:10]:
    av = lambda L: (sum(x for x in L if x is not None) / max(1, len([x for x in L if x is not None])))
    print("  %-12s %-12s %5.0f%%  %7.1f %8.1f %7.1f" % (e["start"], e["trough"], e["max_dd"] * 100, av(e["vix"]), av(e["brd"]), av(e["reg"])))

# --- EXPLOSION environment: top decile of fwd-20d return ---
valid_fwd = sorted([f for f in fwd if f is not None])
if valid_fwd:
    thr = valid_fwd[int(0.90 * len(valid_fwd))]
    expl = [rows[i] for i in range(N) if fwd[i] is not None and fwd[i] >= thr]
    quiet = [rows[i] for i in range(N) if fwd[i] is not None and fwd[i] < valid_fwd[int(0.10 * len(valid_fwd))]]
    print("\n=== EXPLOSION environment (top-decile fwd-20d return, thr logret=%.2f) ===" % thr)
    print("  [explosion] VIX    ", dist([r["vix"] for r in expl]))
    print("  [explosion] breadth", dist([r["brd"] for r in expl]))
    print("  [explosion] regime ", dist([r["reg"] for r in expl]))
    print("  [bottom-decile fwd] VIX    ", dist([r["vix"] for r in quiet]))
    print("  [bottom-decile fwd] breadth", dist([r["brd"] for r in quiet]))

# --- the SEPARATOR: VIX/breadth bands for dd vs explosion ---
print("\n=== SEPARATOR (mean fwd-20d logret by VIX band) — where does Apex make vs lose? ===")
bands = [(0, 15), (15, 20), (20, 25), (25, 30), (30, 100)]
for lo, hi in bands:
    idxs = [i for i in range(N) if rows[i]["vix"] is not None and lo <= rows[i]["vix"] < hi and fwd[i] is not None]
    if not idxs:
        continue
    fr = [fwd[i] for i in idxs]
    dds = [rows[i]["dd"] for i in idxs if rows[i]["dd"] is not None]
    print("  VIX[%2d,%3d): n=%4d  mean fwd-20d logret=%+.3f  avg dd=%.2f  frac-in-dd>=.4=%.0f%%" % (
        lo, hi, len(idxs), sum(fr) / len(fr), sum(dds) / max(1, len(dds)),
        100 * len([x for x in dds if x >= 0.40]) / max(1, len(dds))))
print("\n=== by BREADTH band ===")
for lo, hi in [(0, 30), (30, 45), (45, 55), (55, 70), (70, 100)]:
    idxs = [i for i in range(N) if rows[i]["brd"] is not None and lo <= rows[i]["brd"] < hi and fwd[i] is not None]
    if not idxs:
        continue
    fr = [fwd[i] for i in idxs]
    print("  brd[%2d,%3d): n=%4d  mean fwd-20d logret=%+.3f" % (lo, hi, len(idxs), sum(fr) / len(fr)))
print("\nDONE.")
