"""Phase C — drill the VCBW basin + full W1-W6 picture for the winner.

Reuses phase_b's ledger + evaluate(). Tighter ranges around the Phase B basin,
more samples, ranks by a combined WR/N utility, and dumps the full discrete-tier
breakdown (WR7/WR15/WR30 + per-year) for the chosen winner.
"""
import os, json, random
import numpy as np
import phase_b_sweep as B   # provides evaluate, apply_mod, ov/vol/w15.., baseline machinery

TAG = os.environ.get("TAG", "5y")

# baseline
ident = {"VL_LO": 0, "VL_HI": 0, "KL": 0, "TL": 0, "VD_LO": 1e9, "VD_HI": 1e9 + 1, "KD": 0, "TD": 0}
base = B.evaluate(ident, None)
b75_wr = base["ge75"]["WR15"]; b75_n = base["ge75"]["N"]
b7579 = base["75_79"]["WR15"]; b80 = base["ge80"]["WR15"]
b7074 = base["70_74"]["WR15"]; b8084 = base["80_84"]["WR15"]; b8589 = base["85_89"]["WR15"]
byr = base["ge75_by_year"]

rng = random.Random(99)
NS = int(os.environ.get("NS", "800"))
WN = float(os.environ.get("WN", "0.0015"))  # utility weight per +1 N

cands = []
for _ in range(NS):
    p = {
        "VL_LO": rng.uniform(1.15, 1.55),
        "VL_HI": rng.uniform(2.15, 2.75),
        "KL":    rng.uniform(0.55, 0.95),
        "TL":    rng.uniform(75.6, 78.2),
        "VD_LO": rng.uniform(2.10, 2.55),
        "VD_HI": rng.uniform(3.10, 4.50),
        "KD":    rng.uniform(0.55, 0.95),
        "TD":    rng.uniform(70.2, 72.2),
    }
    if p["VL_HI"] <= p["VL_LO"] or p["VD_HI"] <= p["VD_LO"]:
        continue
    r = B.evaluate(p, base)
    dwr = round(r["ge75"]["WR15"] - b75_wr, 3)
    dn = r["ge75"]["N"] - b75_n
    rec = {"p": p, "ge75_WR15": r["ge75"]["WR15"], "ge75_N": r["ge75"]["N"],
           "d75_WR": dwr, "d75_N": dn, "wr7579": r["75_79"]["WR15"],
           "ge80_WR15": r["ge80"]["WR15"], "yr": r["ge75_by_year"], "full": r,
           "util": round(dwr + WN * dn, 4)}
    cands.append(rec)


def passes(c):
    if c["ge75_N"] < b75_n:
        return False
    if c["ge80_WR15"] is None or abs(c["ge80_WR15"] - b80) > 0.01:
        return False
    if c["wr7579"] < b7579 - 0.01:
        return False
    for y in ["2021", "2022", "2023", "2024", "2025"]:
        if c["yr"][y] is None or byr[y] is None or c["yr"][y] < byr[y] - 0.5:
            return False
    return True


MINN = int(os.environ.get("MINN", "0"))
RANK = os.environ.get("RANK", "util")  # 'util' or 'wr'
good = [c for c in cands if passes(c) and c["d75_N"] >= MINN]
if RANK == "wr":
    good.sort(key=lambda c: (-c["d75_WR"], -c["d75_N"]))
else:
    good.sort(key=lambda c: -c["util"])
print("BASE ge75 WR15=%.2f N=%d 75-79=%.2f 80-84=%.2f 80+=%.2f" % (b75_wr, b75_n, b7579, b8084, b80))
print("DRILL NS=%d PASS=%d" % (len(cands), len(good)))

out = {"baseline": {"ge75_WR15": b75_wr, "ge75_N": b75_n, "wr7579": b7579,
                    "70_74": b7074, "80_84": b8084, "85_89": b8589, "ge80": b80,
                    "ge75_by_year": byr},
       "n_pass": len(good), "top": []}

for c in good[:5]:
    f = c["full"]
    out["top"].append({
        "p": {k: round(v, 3) for k, v in c["p"].items()},
        "util": c["util"], "d75_WR": c["d75_WR"], "d75_N": c["d75_N"],
        "ge75": f["ge75"], "ge80": f["ge80"], "ge70": f["ge70"],
        "70_74": f["70_74"], "75_79": f["75_79"], "80_84": f["80_84"], "85_89": f["85_89"],
        "ge75_by_year": f["ge75_by_year"],
    })

with open(".cache/call_boundary_residual_v60/phase_c_%s.json" % TAG, "w") as fp:
    json.dump(out, fp, indent=2)

if good:
    c = good[0]; f = c["full"]
    print("WINNER util=%.3f d75_WR=%+.2f d75_N=%+d | 75+ WR15=%.2f N=%d | 75-79=%.2f WR7=%.2f WR30=%.2f | 80+=%.2f(N=%d) 80-84=%.2f 85-89=%.2f | 70-74=%.2f" % (
        c["util"], c["d75_WR"], c["d75_N"], f["ge75"]["WR15"], f["ge75"]["N"],
        f["75_79"]["WR15"], f["ge75"]["WR7"], f["ge75"]["WR30"],
        f["ge80"]["WR15"], f["ge80"]["N"], f["80_84"]["WR15"], f["85_89"]["WR15"], f["70_74"]["WR15"]))
    print("WINNER_P=" + json.dumps({k: round(v, 4) for k, v in c["p"].items()}))
    print("WINNER_YR=" + json.dumps(f["ge75_by_year"]))
