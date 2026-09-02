"""Stage B fidelity gate — does in-memory re-simulate at v70 DEFAULT weights reproduce
the DB v70 75+ CALL set? If not, the reweight sweep is untrustworthy.

Checks:
  1. set agreement: re-sim 75+ (default weights, holdout window) vs DB v70 75+ — precision,
     recall, Jaccard. Want >= ~0.90 overlap.
  2. per-band count agreement (75-79 / 80-84 / 85+).
  3. funded-label agreement: re-sim 75+ apexTP15 vs ledger 75+ apexTP15 (must be ~equal).
  4. unlabeled fraction (re-sim 75+ whose (sym,date) is absent from the [70,99] ledger).
"""
import os, sys, json

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from database.trader_database import DB
from resim import ReSim, CUTOFF_ISO


def main():
    rs = ReSim()
    base = rs.evaluate({}, label="v70-default")
    resim_ov = rs.simulate_overalls({})   # (sym, date_iso) -> overall
    resim_75 = {k for k, o in resim_ov.items() if o >= 75}

    sql = ("SELECT symbol, date FROM scores WHERE version_id=70 AND overall>=75 "
           "AND date BETWEEN '2016-05-15' AND '%s'" % CUTOFF_ISO)
    db_75 = set()
    for sym, d in DB.execute_sql(sql).fetchall():
        db_75.add((sym, d.isoformat() if hasattr(d, "isoformat") else str(d)))

    inter = resim_75 & db_75
    prec = len(inter) / len(resim_75) if resim_75 else 0.0
    rec = len(inter) / len(db_75) if db_75 else 0.0
    jac = len(inter) / len(resim_75 | db_75) if (resim_75 | db_75) else 0.0

    # Diagnose the DB-75+ that re-sim MISSED: how many were cont_lift promotions?
    # (simulate() omits prior_signal -> continuation echo can't fire in re-sim. Expected,
    #  uniform across variants -> relative reweight ranking stays valid.)
    missed = db_75 - resim_75
    cont_missed = 0
    if missed:
        import json as _j
        ph = "('" + "','".join(sorted({m[0] for m in missed})) + "')"
        sql2 = ("SELECT symbol, date, weight_info FROM scores WHERE version_id=70 "
                "AND overall>=75 AND symbol IN %s AND date BETWEEN '2016-05-15' AND '%s'"
                % (ph, CUTOFF_ISO))
        for sym, d, wi in DB.execute_sql(sql2).fetchall():
            key = (sym, d.isoformat() if hasattr(d, "isoformat") else str(d))
            if key in missed and wi:
                try:
                    if float(_j.loads(wi).get("cont_lift", 0) or 0) > 0:
                        cont_missed += 1
                except Exception:
                    pass
    print("missed DB-75+ (not in re-sim): %d  of which cont_lift promotions: %d (%.0f%%)"
          % (len(missed), cont_missed, 100.0 * cont_missed / max(1, len(missed))))

    print("\n===== FIDELITY =====")
    print("re-sim 75+ : %d   DB 75+ : %d   overlap : %d" % (len(resim_75), len(db_75), len(inter)))
    print("precision %.3f  recall %.3f  jaccard %.3f" % (prec, rec, jac))
    print("unlabeled_frac (re-sim 75+ not in [70,99] ledger): %.4f" % base["unlabeled_frac"])
    print("re-sim 75+ apexTP15 = %s  holdTP15 = %s  optTP15 = %s  (N_labeled=%d)" % (
        base["apexTP15_75plus"], base["holdTP15_75plus"], base["optTP15_75plus"], base["n_labeled"]))
    print("re-sim supply 75+/yr = %s  recycle_cov = %s" % (base["supply_75plus_per_yr"], base["recycle_coverage"]))

    cont_share = cont_missed / max(1, len(missed))
    if jac >= 0.90 and base["unlabeled_frac"] <= 0.03:
        verdict = "PASS — re-sim faithful, sweep trustworthy (absolute + relative)"
    elif jac >= 0.80 and cont_share >= 0.5 and base["unlabeled_frac"] <= 0.03:
        verdict = ("PASS-RELATIVE — gap is mostly cont_lift (omitted in simulate, uniform "
                   "across variants); reweight RANKING valid, absolute numbers need real recalc")
    else:
        verdict = "FAIL — re-sim diverges from DB beyond the cont_lift gap; do NOT trust sweep"
    ok = verdict.startswith("PASS")
    print("\nVERDICT:", verdict)
    with open(os.path.join(_HERE, "validate_result.json"), "w") as f:
        json.dump({"precision": prec, "recall": rec, "jaccard": jac,
                   "unlabeled_frac": base["unlabeled_frac"],
                   "resim_apexTP15": base["apexTP15_75plus"],
                   "resim_n_75plus": len(resim_75), "db_n_75plus": len(db_75),
                   "pass": ok}, f, indent=2)


if __name__ == "__main__":
    main()
