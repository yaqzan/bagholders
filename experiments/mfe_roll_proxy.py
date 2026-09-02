"""
mfe_roll_proxy.py — σ-normalized MFE extension as a roll-thesis floor check.

Pulls avg/median MFE_sigma at 30d/60d/90d/150d from ScoreAssessmentMeta per
call-side bucket (70+, 75+, 80+, 85+, 90+, 95+) and computes period-to-period
deltas. Each delta approximates the *average post-window additional σ-run* in
the full peak population. Compares against the 2.0σ barrier the rolled trade
has to clear, printing a verdict per bucket.

Notes on interpretation:
  - Deltas are non-negative (MFE is monotone non-decreasing in W by definition)
  - Zero delta = "peak occurred inside the shorter window, nothing new after"
    (includes both "it kept falling" and "it just didn't rally further")
  - Roll candidates are specifically WINNERS, which run harder than average,
    so these figures are a LOWER BOUND on the roll-window MFE

Usage:
    python -m experiments.mfe_roll_proxy
"""

from colorama import Fore, Style
from database.models.core import ScoreAssessmentMeta, AlgorithmVersion


CALL_BUCKETS = ['70+', '75+', '80+', '85+', '90+', '95+']
PERIODS = ['15d', '30d', '60d', '90d', '150d']
ROLL_BARRIER_SIGMA = 2.0  # the rolled 30d trade needs this much NEW σ to win


def _fmt(v, w=7, d=2):
    return f"{v:{w}.{d}f}" if v is not None else " " * w


def run():
    version = AlgorithmVersion.get_active_scores_version()
    print(Fore.CYAN + f"\nActive version: v{version.id} ({version.git_commit})\n" + Style.RESET_ALL)

    rows = {r.bucket: r for r in
            ScoreAssessmentMeta.select().where(ScoreAssessmentMeta.version == version)}

    # --- Table 1: Absolute MFE_sigma across periods ----------------------
    print(Fore.YELLOW + "=" * 100)
    print("TABLE 1 -- sigma-Normalized MFE by Period (call-side, cumulative buckets)")
    print("=" * 100 + Style.RESET_ALL)
    print("  avg_mfe_sigma_{p} = mean best sigma-run reached within window W, from signal date")
    print()
    hdr = f"{'Bucket':<7} {'N':>6} | " + " ".join(f"{'avg_'+p:>8}" for p in PERIODS) + \
          " | " + " ".join(f"{'med_'+p:>8}" for p in PERIODS)
    print(hdr)
    print("-" * len(hdr))
    for b in CALL_BUCKETS:
        r = rows.get(b)
        if not r: continue
        avgs = [getattr(r, f'avg_mfe_sigma_{p}') for p in PERIODS]
        meds = [getattr(r, f'median_mfe_sigma_{p}') for p in PERIODS]
        print(f"{b:<7} {r.sample_count:>6} | " +
              " ".join(_fmt(a, 8) for a in avgs) + " | " +
              " ".join(_fmt(m, 8) for m in meds))

    # --- Table 2: Period-to-period MFE_sigma DELTAS ----------------------
    print()
    print(Fore.YELLOW + "=" * 100)
    print("TABLE 2 -- Post-Window sigma-Extension (MFE_sigma_P - MFE_sigma_{P-1})")
    print("=" * 100 + Style.RESET_ALL)
    print(f"  The rolled 30d trade needs ~{ROLL_BARRIER_SIGMA:.1f} sigma of NEW run from roll entry to win.")
    print("  This is the bucket-wide avg extension -- winners-only will run HARDER.")
    print()
    deltas = [('30-15', '30d', '15d'), ('60-30', '60d', '30d'),
              ('90-60', '90d', '60d'), ('150-90', '150d', '90d')]
    hdr2 = f"{'Bucket':<7} | " + " ".join(f"{'avg_d'+d[0]:>11}" for d in deltas) + \
           " | " + " ".join(f"{'med_d'+d[0]:>11}" for d in deltas)
    print(hdr2)
    print("-" * len(hdr2))
    for b in CALL_BUCKETS:
        r = rows.get(b)
        if not r: continue
        avg_ds, med_ds = [], []
        for _, p_hi, p_lo in deltas:
            a_hi = getattr(r, f'avg_mfe_sigma_{p_hi}')
            a_lo = getattr(r, f'avg_mfe_sigma_{p_lo}')
            m_hi = getattr(r, f'median_mfe_sigma_{p_hi}')
            m_lo = getattr(r, f'median_mfe_sigma_{p_lo}')
            avg_ds.append((a_hi - a_lo) if (a_hi is not None and a_lo is not None) else None)
            med_ds.append((m_hi - m_lo) if (m_hi is not None and m_lo is not None) else None)
        print(f"{b:<7} | " +
              " ".join(_fmt(a, 11) for a in avg_ds) + " | " +
              " ".join(_fmt(m, 11) for m in med_ds))

    # --- Table 3: Verdict per bucket -------------------------------------
    print()
    print(Fore.YELLOW + "=" * 100)
    print("TABLE 3 -- Roll Thesis Verdict (uses 30->60 delta ~ days 15->45 proxy)")
    print("=" * 100 + Style.RESET_ALL)
    print(f"  If avg_d(60-30) >= {ROLL_BARRIER_SIGMA:.1f} sigma -> roll has clear edge even on full population.")
    print(f"  If avg_d(60-30) >= ~1.0 sigma      -> roll may have edge on WINNERS (bucket mean understates).")
    print(f"  If avg_d(60-30) <  ~0.5 sigma      -> roll thesis likely dead. Kill.")
    print()
    hdr3 = f"{'Bucket':<7} {'avg_d60-30':>11} {'med_d60-30':>11} {'mfe_p25_d60-30':>15} {'Verdict':>12}"
    print(hdr3)
    print("-" * len(hdr3))
    for b in CALL_BUCKETS:
        r = rows.get(b)
        if not r: continue
        a_hi = getattr(r, 'avg_mfe_sigma_60d')
        a_lo = getattr(r, 'avg_mfe_sigma_30d')
        m_hi = getattr(r, 'median_mfe_sigma_60d')
        m_lo = getattr(r, 'median_mfe_sigma_30d')
        p25_hi = getattr(r, 'mfe_sigma_p25_60d')
        p25_lo = getattr(r, 'mfe_sigma_p25_30d')
        ad = (a_hi - a_lo) if (a_hi is not None and a_lo is not None) else None
        md = (m_hi - m_lo) if (m_hi is not None and m_lo is not None) else None
        pd_ = (p25_hi - p25_lo) if (p25_hi is not None and p25_lo is not None) else None
        if ad is None:
            verdict = "N/A"
        elif ad >= ROLL_BARRIER_SIGMA:
            verdict = Fore.GREEN + "STRONG" + Style.RESET_ALL
        elif ad >= 1.0:
            verdict = Fore.YELLOW + "POSSIBLE" + Style.RESET_ALL
        elif ad >= 0.5:
            verdict = "MARGINAL"
        else:
            verdict = Fore.RED + "KILL" + Style.RESET_ALL
        print(f"{b:<7} {_fmt(ad, 11)} {_fmt(md, 11)} {_fmt(pd_, 15)} {verdict:>12}")

    print()


if __name__ == '__main__':
    run()
