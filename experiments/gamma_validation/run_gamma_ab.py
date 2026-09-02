"""
Gamma engine A/B on the concentration_2x sprint + Apex cells.

Paired (identical window+iter seeds; the ONLY difference is GAMMA_AWARE), so the
delta is pure signal. Runs the drill twice:
  goff : GAMMA_AWARE unset  (shipped constant-delta engine = baseline)
  gon  : GAMMA_AWARE=1      (BS delta+gamma engine)
Cells: flat_n4_a25 (the STAGED fast-2x sprint) + cascade_ref (production Apex).
Then prints the A/B on P(2x)/days/compound/WorstDD/collapse.
"""
import os, sys, json, subprocess, time

REPO = r"C:\Development\Trader"
SWEEP = os.path.join(REPO, "experiments", "concentration_2x", "sweep.py")
RESULTS = os.path.join(REPO, "experiments", "concentration_2x", "results")
CELLS = os.environ.get("GAB_CELLS", "flat_n4_a25,cascade_ref")
N_ITER = os.environ.get("GAB_N_ITER", "150")   # paired A/B: delta is stable at low N
STEP = os.environ.get("GAB_STEP", "3")   # quarterly window starts = faster read, still covers COVID/2022
PY = sys.executable

def run(tag, gamma_on):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if gamma_on:
        env["GAMMA_AWARE"] = "1"
    else:
        env.pop("GAMMA_AWARE", None)
    # Resume ENABLED (distinct goff/gon tags isolate partials) so queue
    # preemptions during market hours cost nothing — each relaunch continues.
    argv = [PY, "-u", SWEEP, "--stage", "drill", "--cells", CELLS,
            "--n-iter", N_ITER, "--step-months", STEP, "--tag", tag,
            "--workers", "6", "--starting-cash", "50000"]
    print(f"\n{'='*72}\n[{tag}] GAMMA_AWARE={'1' if gamma_on else '(off)'}  {' '.join(argv)}\n{'='*72}", flush=True)
    t0 = time.time()
    r = subprocess.run(argv, cwd=REPO, env=env)
    print(f"[{tag}] exit={r.returncode} in {(time.time()-t0)/60:.1f} min", flush=True)
    if r.returncode != 0:
        raise SystemExit(f"[{tag}] sweep failed")
    return os.path.join(RESULTS, f"sweep_drill_{tag}.json")

def load_cells(path):
    d = json.loads(open(path).read())
    return {c["cell"]: c for c in d["cells"]}, d

off_path = run("goff", False)
on_path = run("gon", True)
off, doff = load_cells(off_path)
on, don = load_cells(on_path)

print(f"\n{'#'*72}\nGAMMA A/B  (N_ITER={N_ITER}, windows={doff.get('n_windows')}, span={doff.get('window_span')})\n{'#'*72}")
def fmt(v, pct=False, d=1):
    if v is None: return "  -  "
    return f"{v*100:.{d}f}%" if pct else f"{v:.{d}f}"
for cell in ("flat_n4_a25", "cascade_ref"):
    o = off.get(cell); n = on.get(cell)
    if not o or not n:
        print(f"\n{cell}: MISSING (off={bool(o)} on={bool(n)})"); continue
    label = "SPRINT n4x25%" if cell == "flat_n4_a25" else "APEX cascade_ref"
    print(f"\n=== {cell}  ({label})   paths off={o['n_paths']:,} on={n['n_paths']:,} ===")
    rows = [
        ("P(2x ever)",         'p_2x_ever',        True, 1),
        ("P(2x before 50%DD)", 'p_2x_before_50dd', True, 1),
        ("P(collapse<=20%)",   'p_collapse',       True, 1),
        ("median days->2x",    'median_days_2x',   False, 0),
        ("median compound %",  'median_compound_pct', False, 0),
        ("worst DD %",         'worst_dd_pct',     False, 1),
    ]
    for name, key, pct, d in rows:
        vo, vn = o.get(key), n.get(key)
        delta = ""
        if isinstance(vo,(int,float)) and isinstance(vn,(int,float)):
            dv = (vn-vo)*100 if pct else (vn-vo)
            delta = f"   Δ={dv:+.{d}f}{'pp' if pct else ''}"
        print(f"  {name:22s} off={fmt(vo,pct,d):>8s}   gamma={fmt(vn,pct,d):>8s}{delta}")
print(f"\n{'#'*72}\nGAMMA A/B COMPLETE\n{'#'*72}", flush=True)
