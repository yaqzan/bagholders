"""Final verification for the candidate winner. Print full per-tier 1y/3y/5y
detail with N drift + H1/H3/H4/H5 gate breakdown.
"""
import os, sys, time
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'experiments' / 'v34_calibration'))
from sweep_boost_params import Variant, build_data_cache, load_barriers, evaluate, h1_h5_gate

v27_path = str(ROOT / 'experiments' / 'v27_optimization' / 'phase_tp3b_lift_table.json')
v34_path = str(ROOT / 'experiments' / 'v34_calibration' / 'lift_table_v34_preboost.json')

t0 = time.time()
df = build_data_cache(1825)
barriers = load_barriers()
print(f"[data] in {time.time()-t0:.1f}s\n", flush=True)

candidates = [
    Variant('PROD_v27',     v27_path, max_boost=0.50, norm_call=22.3, norm_put=16.3, window=5),
    Variant('V34_M55_NC14', v34_path, max_boost=0.55, norm_call=14.0, norm_put=16.3, window=5),
    Variant('V34_M55_NC15', v34_path, max_boost=0.55, norm_call=15.0, norm_put=16.3, window=5),
    Variant('V34_M60_NC20', v34_path, max_boost=0.60, norm_call=20.0, norm_put=16.3, window=5),
    Variant('V34_M60_NC15', v34_path, max_boost=0.60, norm_call=15.0, norm_put=16.3, window=5),
    Variant('V34_M55_W7',   v34_path, max_boost=0.55, norm_call=14.0, norm_put=16.3, window=7),
]

windows = [
    ('1y', (date.today() - timedelta(days=365)).isoformat()),
    ('3y', (date.today() - timedelta(days=3*365)).isoformat()),
    ('5y', None),
]

for v in candidates:
    print(f"\n{'='*100}")
    print(f"{v.name}: MAX={v.max_boost} NC={v.norm_call} NP={v.norm_put} W={v.window} put_admit={v.put_admit}")
    print('='*100)
    for wname, since in windows:
        r = evaluate(v, df, barriers, since=since)
        if v.name == 'PROD_v27':
            base = r
        print(f"\n  {wname} (n_boosted={r['70+'][2]:,}):")
        print(f"    {'Tier':<6} {'Cand WR':>9} {'Cand N':>9} | {'Base WR':>9} {'Base N':>9} | {'ΔWR':>9} {'ΔN%':>9}")
        for tier in ['95+', '90+', '85+', '80+', '75+', '<5', '<15', '<25']:
            cn, cwr, _ = r.get(tier, (0, None, 0))
            # Recompute base for this window
            if v.name != 'PROD_v27':
                br = evaluate(candidates[0], df, barriers, since=since)
                bn, bwr, _ = br.get(tier, (0, None, 0))
            else:
                bn, bwr = cn, cwr
            dwr = (cwr - bwr) if (bwr is not None and cwr is not None) else None
            dn = (100*(cn-bn)/bn) if bn else None
            cwr_s = f'{cwr:.2f}%' if cwr else '-'
            bwr_s = f'{bwr:.2f}%' if bwr else '-'
            dwr_s = f'{dwr:+.2f}pp' if dwr is not None else '-'
            dn_s = f'{dn:+.1f}%' if dn is not None else '-'
            print(f"    {tier:<6} {cwr_s:>9} {cn:>9,} | {bwr_s:>9} {bn:>9,} | {dwr_s:>9} {dn_s:>9}")

    if v.name != 'PROD_v27':
        # Strict gate at 5y
        r5 = evaluate(v, df, barriers)
        b5 = evaluate(candidates[0], df, barriers)
        passed, fails = h1_h5_gate(b5, r5)
        print(f"\n  H1-H5 strict gate (5y): {'PASS' if passed else 'FAIL'}")
        for f in fails: print(f"    - {f}")

print(f"\n[total] {time.time()-t0:.1f}s")
