"""Pre-ship N=300 MC smoke for the v73 dampener-retirement candidate (2026-06-12).

Handoff mandate: Option B shifts binding-tier density >>30% (75+ +77%), so an
N=300x8 MC smoke is mandatory BEFORE ship. Candidate score rows don't exist in
the DB yet, so signals are sourced from the ReSim arm parquets:

  arm_baseline.parquet  -> paired baseline (98.54% exact vs stored v72)
  arm_bundle_b.parquet  -> candidate (WCF+ICH+CWCF+CSWC+SCW retired)

Mechanics: monte_carlo loads signals ONCE in the parent (run_window ->
load_signals), so a parent-side patch propagates to MP workers via the Pool
initializer. The patched loader queries stored v72 rows for the hydration
attributes the retirements don't touch (trend / weight_info / stoch /
volume_signal) and swaps in the arm's `overall`. Both arms share the ReSim
cont-echo gap -> arm-vs-arm deltas internally valid (campaign method).

Windows: parquets start 2021-06-01 -> canonical 2021/2020/COVID windows are NOT
runnable here; both arms run the SAME 7 windows (5y_sim = 2021-06-01..2026-04-15).
Full-window validation on real rows happens at the post-ship Stage-3 retune.

Smoke gates (B2/B3-style): collapse == 0 on every window x both arms;
candidate WorstDD vs paired baseline reported (Apex recoverable-DD budget).

Run:  python experiments/dampener_ablation_v72/mc_smoke.py [--arm both|baseline|bundle_b]
Env:  MC_SMOKE_ITER (default 300)
"""
import json
import os
import sys
from datetime import date

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
# Must be set BEFORE monte_carlo import (module-level reads).
os.environ["N_ITER_OVERRIDE"] = os.environ.get("MC_SMOKE_ITER", "300")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
while _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

import polars as pl

OUT_DIR = os.path.join(_ROOT, ".cache", "dampener_ablation_v72")

SMOKE_WINDOWS = [
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023", date(2023, 1, 1), date(2023, 12, 31)),
    ("2024", date(2024, 1, 1), date(2024, 12, 31)),
    ("2025", date(2025, 1, 1), date(2025, 12, 31)),
    ("dip", date(2025, 11, 1), date(2026, 4, 24)),
    ("22-now", date(2022, 1, 1), date(2026, 4, 24)),
    ("5y_sim", date(2021, 6, 1), date(2026, 4, 15)),
]


def load_override(arm):
    p = os.path.join(OUT_DIR, f"arm_{arm}.parquet")
    df = pl.read_parquet(p).with_columns(pl.col("date").str.to_date())
    return {(s, d): int(o) for s, d, o in
            zip(df["symbol"].to_list(), df["date"].to_list(), df["overall"].to_list())}


def make_loaders(mc, override):
    """Patched loaders sourcing `overall` from the arm parquet.

    Perf notes (the first run ground for 50min on window-1 prep):
      * Score.symbol is a DeferredForeignKey — `s.symbol` fires get_rel_instance
        = ONE SELECT PER ROW. Use `s.symbol_id` (raw key), like monte_carlo does.
      * weight_info/stoch/volume_signal are only read by gated-off filters
        (WEAK_WEEKLY_*_DROP retired; WEEKLY_OVF_FILTER env-off; CTSL reads
        overall+trend) — drop them from the SELECT (peewee returns None for
        unselected plain fields; all consumers guard on falsy).
      * One bulk pull per window shared by both loaders (cache below).
    """
    from database.models.core import Score
    _cache = {}

    def window_rows(version, d_start, d_end):
        key = (d_start, d_end)
        if key not in _cache:
            rows = list(
                Score.select(Score.symbol, Score.date, Score.overall, Score.trend)
                .where(Score.version == version, Score.date >= d_start,
                       Score.date <= d_end, Score.overall.is_null(False)))
            out = []
            for s in rows:
                ov = override.get((s.symbol_id, s.date))
                if ov is None:
                    continue
                s.overall = ov
                out.append(s)
            _cache.clear()        # one window at a time — free prior memory
            _cache[key] = out
        return _cache[key]

    def load_signals_patched(version, d_start, d_end):
        out = [s for s in window_rows(version, d_start, d_end)
               if int(s.overall) >= mc.OVERFLOW_THRESHOLD]
        out.sort(key=lambda s: (s.date, -int(s.overall)))
        return mc._apply_weekly_overflow_filter(
            mc._apply_ctsl_to_signals(out, "call"))

    def load_put_signals_patched(version, d_start, d_end):
        out = [s for s in window_rows(version, d_start, d_end)
               if int(s.overall) <= mc.PUT_THRESHOLD]
        out.sort(key=lambda s: (s.date, int(s.overall)))
        return mc._apply_ctsl_to_signals(out, "put")

    return load_signals_patched, load_put_signals_patched


def run_arm(arm):
    import monte_carlo as mc
    mc.WINDOWS = SMOKE_WINDOWS
    override = load_override(arm)
    ls, lps = make_loaders(mc, override)
    saved = (mc.load_signals, mc.load_put_signals)
    rj = os.path.join(OUT_DIR, f"mc_smoke_{arm}.json")
    os.environ["MC_RESULTS_JSON"] = rj
    print(f"\n######## MC SMOKE ARM: {arm}  (N_ITER={mc.N_ITER}, "
          f"{len(SMOKE_WINDOWS)} windows, signals from arm parquet) ########",
          flush=True)
    try:
        mc.load_signals, mc.load_put_signals = ls, lps
        mc.main()
    finally:
        mc.load_signals, mc.load_put_signals = saved
    return rj


def main():
    arms = ["baseline", "bundle_b"]
    if "--arm" in sys.argv:
        a = sys.argv[sys.argv.index("--arm") + 1]
        if a != "both":
            arms = [a]
    paths = {}
    for a in arms:
        paths[a] = run_arm(a)
    print("\n" + "=" * 90)
    print("SMOKE COMPARE (candidate vs paired ReSim baseline)")
    print("=" * 90)
    res = {a: json.load(open(p)) for a, p in paths.items() if os.path.exists(p)}
    if len(res) == 2:
        b, c = res["baseline"], res["bundle_b"]
        print(f"{'window':<8} {'base MedRet':>16} {'cand MedRet':>16} "
              f"{'base DD':>8} {'cand DD':>8} {'dDD':>6} {'base coll':>9} {'cand coll':>9}")
        for w, _, _ in SMOKE_WINDOWS:
            if w in b and w in c:
                print(f"{w:<8} {b[w]['med_ret']:>+15,.0f}% {c[w]['med_ret']:>+15,.0f}% "
                      f"{b[w]['worst_dd']:>7.1f}% {c[w]['worst_dd']:>7.1f}% "
                      f"{c[w]['worst_dd']-b[w]['worst_dd']:>+5.1f} "
                      f"{b[w]['p_coll']:>8.1f}% {c[w]['p_coll']:>8.1f}%")
        coll = max(max(b[w]["p_coll"], c[w]["p_coll"])
                   for w, _, _ in SMOKE_WINDOWS if w in b and w in c)
        print(f"\nmax collapse across all cells: {coll:.1f}%  "
              f"({'PASS' if coll == 0 else 'FAIL'} — smoke gate requires 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
