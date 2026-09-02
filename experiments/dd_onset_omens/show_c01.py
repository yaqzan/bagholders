import json, sys
mode = sys.argv[1] if len(sys.argv) > 1 else 'phaseC'
idx = int(sys.argv[2]) if len(sys.argv) > 2 else 1
r = json.load(open(f'experiments/dd_onset_omens/{mode}_results.json'))
base = r['baseline']
win = [x for x in r['results'] if x['i'] == idx][0]
print('window     base_dd  cand_dd  delta | base_med    cand_med')
for w in r['wins']:
    b, c = base[w], win['per_win'][w]
    print(f"{w:10s} {b['dd']:7.1f} {c['dd']:7.1f} {b['dd']-c['dd']:+6.1f} | {b['med']:10.1f} {c['med']:10.1f}")
