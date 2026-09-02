"""Parse weekly_confidence_gate / weekly_tanh_phase* outputs into a utility table.

Reads diff_assess output blocks (one per variant) and computes:
  - WR15 lift vs baseline per put bucket
  - N change vs baseline per bucket
  - Call regression check
  - Composite utility score (favors put WR lift; tolerates aggressive N drop)

Utility formula (encodes the user's calibration preference):
    U = w_wr15  * sum(wr15_delta_put * weight_per_bucket)
      + w_call  * (-max(0, baseline_call_wr15 - new_call_wr15) * 100)   # penalty if call WR drops
      + w_npar  * min(0, n_put / n_call_target - 1.0)                    # bonus for moving toward put:call=1

where weights are:
    w_wr15 = 1.0  (per-trade improvement is primary)
    w_call = 5.0  (call regression penalty heavy)
    w_npar = 0.2  (mild bonus for N parity progress)

Run after each tanh phase. Reads .out file path from argv.
"""
from __future__ import annotations
import io, sys, re
if __name__ == '__main__' and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Bucket weights — favor deep put buckets where the dud cohort lives
PUT_BUCKET_WEIGHTS = {
    '<25': 0.10,
    '<20': 0.10,
    '<15': 0.20,
    '<10': 0.30,
    '<5':  0.30,
}

CALL_BUCKETS = ['95+', '90+', '85+', '80+', '75+', '70+']

PARSE_LINE_RE = re.compile(
    r'^\s*(\S+)\s*\|\s*(\d+)/(\d+)\s*\|\s*'
    r'([\d.]+)%->([\d.]+)%\s+'
    r'([\d.]+)%->([\d.]+)%\s+'
    r'([+\-\d.]+)%->([+\-\d.]+)%\s+'
    r'([+\-\d.]+)%->([+\-\d.]+)%\s+'
    r'([+\-\d.]+)%->([+\-\d.]+)%\s+'
    r'([+\-\d.]+)->([+\-\d.]+)\s*$'
)


# Colorama on Windows emits non-standard ESC sequences (e.g. \x1b\x20).
# Strip any ESC followed by either a CSI sequence or any single byte.
ANSI_RE = re.compile(r'\x1b(?:\[[0-9;]*[a-zA-Z]|[^\n])?')


def parse_variants(text):
    """Return list of (label, dict[bucket -> stats])."""
    # Strip ANSI escape codes (colorama)
    text = ANSI_RE.sub('', text)
    variants = []
    current_label = None
    current = {}
    in_table = False

    for line in text.splitlines():
        # Variant header
        m = re.match(r'\s*VARIANT:\s*(\S+)', line)
        if m:
            if current_label is not None:
                variants.append((current_label, current))
            current_label = m.group(1)
            current = {}
            in_table = False
            continue
        if 'Assessment Diff' in line:
            in_table = True
            continue
        if not in_table:
            continue
        # Try parsing a bucket row
        pm = PARSE_LINE_RE.match(line)
        if pm:
            bucket = pm.group(1)
            n_db = int(pm.group(2))
            n_sim = int(pm.group(3))
            wr15_db = float(pm.group(4))
            wr15_sim = float(pm.group(5))
            wr30_db = float(pm.group(6))
            wr30_sim = float(pm.group(7))
            cap30_db = float(pm.group(14))
            cap30_sim = float(pm.group(15))
            current[bucket] = {
                'n_db': n_db, 'n_sim': n_sim,
                'wr15_db': wr15_db, 'wr15_sim': wr15_sim,
                'wr30_db': wr30_db, 'wr30_sim': wr30_sim,
                'cap30_db': cap30_db, 'cap30_sim': cap30_sim,
            }

    if current_label is not None:
        variants.append((current_label, current))
    return variants


def compute_utility(variant_stats, baseline_stats):
    """Compute utility delta vs baseline."""
    if not baseline_stats:
        return None
    # Sum weighted WR15 delta on put buckets
    put_wr15_delta = 0.0
    n_put_total = 0
    for b, w in PUT_BUCKET_WEIGHTS.items():
        if b in variant_stats and b in baseline_stats:
            d = variant_stats[b]['wr15_sim'] - baseline_stats[b]['wr15_sim']
            put_wr15_delta += d * w
            n_put_total += variant_stats[b].get('n_sim', 0)

    # Call regression check (worst case across call buckets)
    call_regression = 0.0
    for b in CALL_BUCKETS:
        if b in variant_stats and b in baseline_stats:
            d = variant_stats[b]['wr15_sim'] - baseline_stats[b]['wr15_sim']
            if d < call_regression:
                call_regression = d

    # N parity progress: target call N at <=20 conviction equivalent of >=75
    # Crude proxy: total <25 N. Target ratio 1:1 with calls 70+.
    n_put_25 = variant_stats.get('<25', {}).get('n_sim', 0) or 0
    n_call_70 = variant_stats.get('70+', {}).get('n_sim', 0) or 0
    n_ratio = n_put_25 / max(1, n_call_70)
    target_ratio = 1.0
    n_parity_bonus = -max(0, n_ratio - target_ratio) / 5.0  # mild penalty per ratio unit

    utility = put_wr15_delta + 5.0 * min(0, call_regression) + 0.2 * n_parity_bonus
    return {
        'utility': utility,
        'put_wr15_delta': put_wr15_delta,
        'call_regression': call_regression,
        'n_put_25': n_put_25,
        'n_call_70': n_call_70,
        'n_ratio': n_ratio,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python weekly_tanh_analyze.py <output_file.out> [<more.out>...]")
        sys.exit(1)

    all_variants = []
    for fp in sys.argv[1:]:
        text = open(fp, 'r', encoding='utf-8', errors='replace').read()
        all_variants.extend(parse_variants(text))

    # Find baseline
    baseline = None
    for label, stats in all_variants:
        if 'baseline' in label.lower():
            baseline = stats
            break

    if not baseline:
        print("No baseline variant found; cannot compute deltas")
        for label, stats in all_variants:
            print(f"  {label}: parsed {len(stats)} buckets")
        return

    print(f"\n{'Variant':<22}  {'<10 WR':<14}  {'<15 WR':<14}  {'<25 WR':<14}  "
          f"{'70+ WR':<14}  {'Put N <25':<10}  {'Ratio':<7}  {'Util':<8}")
    print("-" * 130)

    for label, stats in all_variants:
        util = compute_utility(stats, baseline)
        if util is None:
            continue

        def fmt_wr(b):
            if b not in stats or b not in baseline:
                return "  --"
            v = stats[b]['wr15_sim']
            d = v - baseline[b]['wr15_sim']
            return f"{v:5.1f}% ({d:+.1f})"

        n_put = util['n_put_25']
        n_call = util['n_call_70']
        n_put_base = baseline.get('<25', {}).get('n_sim', 0)
        n_chg = (n_put - n_put_base) / max(1, n_put_base) * 100
        ratio_str = f"{util['n_ratio']:.2f}"
        util_str = f"{util['utility']:+.2f}"

        print(f"{label:<22}  {fmt_wr('<10'):<14}  {fmt_wr('<15'):<14}  {fmt_wr('<25'):<14}  "
              f"{fmt_wr('70+'):<14}  {n_put} ({n_chg:+.0f}%)  {ratio_str:<7}  {util_str:<8}")


if __name__ == '__main__':
    main()
