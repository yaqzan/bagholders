"""Parse experiments/ct_phase_a.out — emit validation-gate summary."""
import re
from pathlib import Path

OUT = Path(__file__).parent / "ct_phase_a.out"

# baseline 22-now: CT-PUT 79.5% N=541, AL-PUT 70.4% N=18106, CT-CALL 91.2% N=262, AL-CALL 84.8% N=7158
BL = {'CT-PUT': 79.5, 'AL-PUT': 70.4, 'CT-CALL': 91.2, 'AL-CALL': 84.8}

text = OUT.read_text()
# split on "VARIANT:"
chunks = re.split(r'\n=+\n\s*VARIANT: ', text)[1:]

rows = []
for c in chunks:
    label = c.splitlines()[0].strip()
    # grab only 22-now window
    m = re.search(r"-- Window \[22-now\].*?(?=\n-- Window|\n###|\Z)", c, re.S)
    if not m:
        continue
    block = m.group(0)
    rec = {'label': label}
    for bucket in ('CT-PUT', 'AL-PUT', 'CT-CALL', 'AL-CALL'):
        mm = re.search(rf"{bucket}\s+N=\s*(\d+)\s+resolved=\s*(\d+)\s+WR15=\s*([\d.]+)%", block)
        if mm:
            rec[bucket] = {'N': int(mm.group(1)), 'res': int(mm.group(2)), 'wr': float(mm.group(3))}
    rows.append(rec)

# print header
print(f"{'variant':<35} {'CT-PUT N':>10} {'WR15':>6} | {'AL-PUT N':>10} {'WR15':>6} dPP | {'CT-CALL N':>10} {'WR15':>6} | {'AL-CALL N':>10} {'WR15':>6} dPP | Gate")
print("-"*150)
for r in rows:
    ctp = r.get('CT-PUT', {})
    alp = r.get('AL-PUT', {})
    ctc = r.get('CT-CALL', {})
    alc = r.get('AL-CALL', {})
    # gate
    g_ctp = ctp.get('wr', 0) >= 81.0
    d_alp = alp.get('wr', 0) - BL['AL-PUT']
    d_alc = alc.get('wr', 0) - BL['AL-CALL']
    g_alp = abs(d_alp) <= 0.5
    g_alc = abs(d_alc) <= 0.5
    pass_ = g_ctp and g_alp and g_alc
    mark = 'PASS' if pass_ else f"{'CTP' if not g_ctp else ''}{'/ALP' if not g_alp else ''}{'/ALC' if not g_alc else ''}".lstrip('/')
    print(f"{r['label']:<35} {ctp.get('N',0):>10} {ctp.get('wr',0):>6.1f} | {alp.get('N',0):>10} {alp.get('wr',0):>6.1f} {d_alp:>+4.1f} | {ctc.get('N',0):>10} {ctc.get('wr',0):>6.1f} | {alc.get('N',0):>10} {alc.get('wr',0):>6.1f} {d_alc:>+4.1f} | {mark}")

print()
print("Baseline: CT-PUT 79.5% N=541, AL-PUT 70.4% N=18106, CT-CALL 91.2% N=262, AL-CALL 84.8% N=7158")
print("Gate: CT-PUT WR15 >= 81%, AL-PUT within +-0.5pp (69.9-70.9%), AL-CALL within +-0.5pp (84.3-85.3%)")
