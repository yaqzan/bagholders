import json
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
recs = {json.loads(l)['cand']: json.loads(l)
        for l in open(os.path.join(_ROOT, '.cache/v73_portfolio_retune/phase_D.jsonl'))
        if l.strip()}
base = recs['c_base']['res']
windows = ['2020_crash', '2020', '2021', '2022', '2023', '2024', '2025', 'dip',
           '22-now', '5y']
for cand in ['c08_alloc065', 'c04_low03_mid08']:
    r = recs[cand]['res']
    print('====', cand, 'params=', recs[cand]['params'], '====')
    hdr = ['window', 'base_dd', 'cand_dd', 'ddD(+better)', 'base_med', 'cand_med',
           'medRatio', 'colB', 'colC']
    print('{:12} {:>8} {:>8} {:>13} {:>11} {:>11} {:>9} {:>5} {:>5}'.format(*hdr))
    worst_regress = 0.0
    for w in windows:
        b, c = base[w], r[w]
        ddD = b['worst_dd'] - c['worst_dd']
        worst_regress = min(worst_regress, ddD)
        ratio = c['med_ret'] / b['med_ret'] if b['med_ret'] else float('nan')
        flag = '  <-- DD REGRESS >5pp' if ddD < -5.0 else ''
        print('{:12} {:8.1f} {:8.1f} {:+13.2f} {:11.0f} {:11.0f} {:9.2f} '
              '{:5.1f} {:5.1f}{}'.format(
                  w, b['worst_dd'], c['worst_dd'], ddD, b['med_ret'],
                  c['med_ret'], ratio, b['p_collapse'], c['p_collapse'], flag))
    colmax = max(v['p_collapse'] for v in r.values())
    print('  -> worst per-window DD regress: {:+.2f}pp (T5 needs >= -5.0)  | '
          'max collapse: {:.1f}% (T6 needs 0)'.format(worst_regress, colmax))
    print()
