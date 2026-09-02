"""Resolve cherry-pick conflicts from applying the v69 weekly-blend fix (8b59206c3)
onto an older scoring silo. Refined rule (v2), derived + verified on v57 and v43:

We WANT the v69 honest behavior (THEIRS) at every weekly site, EXCEPT the
compute_overall_score(...) call — there THEIRS uses a `_score_args`/`_score_kwargs`
refactor that POSTDATES these versions and is malformed standalone, so we instead
keep the version's own inline call (OURS) and INJECT the 4 pit kwargs into it.

Per conflict (ours / theirs):
  1. If THEIRS references the incompatible refactor (`_score_args`/`score_args`/
     `_score_kwargs`/`score_kwargs`):
       - if OURS is a usable compute-call body (has 'prev_ws_macd=pws.macd if pws
         else None,' and not 'pit_ws_trend')  -> take OURS + inject the 4 pit args
         after the prev_ws_macd line   (R4).
       - else (OURS is the empty single-row duplicate re-call)  -> take OURS
         (drops the broken refactor; the version's real call is elsewhere).
  2. Else (THEIRS is a clean v69 addition: R1 pit-map build, R2 W-1/W-2 ws/pws
     shift, R3 pit extraction, weight_info pit keys, simulator pit slot) -> take
     THEIRS. These reference only symbols present in each loop (PriceHistory,
     self.symbol, cutoff_end, ph_rows_asc, _pit_weekly_map, _pit_wk).

R1 (pit-map build), R2 (shift), R3 (extract) thus land whether they auto-merge OR
conflict; R4 (pit args -> compute call) is the inject; scoring.py's blend and
weekly_pit.py auto-merge. The ANCHOR (75+ WR15 ~50-51% after recalc) is the final
validator: missed R1/R3 -> NameError or stale; missed R4 -> look-ahead ~63%.

Usage: python resolve_weekly_conflict.py <worktree> [file1 ...]
       (defaults to database/models/core.py simulator.py)
After running, ALWAYS verify R1/R2/R3/R4 in recalculate_scores_batched (the recalc
hot path) before launching a recalc — see verify_recalc_path() helper at bottom.
"""
import os, sys, py_compile

PIT_INJECT_TMPL = (
    "{ind}pit_ws_trend=_pit_ws_trend, pit_ws_rsi=_pit_ws_rsi,\n"
    "{ind}pit_ws_macd=_pit_ws_macd, weekly_transition_t=_weekly_t,\n"
)
PREV_MACD = "prev_ws_macd=pws.macd if pws else None,"
REFACTOR_TOKENS = ("_score_args", "score_args", "_score_kwargs", "score_kwargs")


def resolve_block(ours, theirs):
    ours_txt = "".join(ours)
    theirs_txt = "".join(theirs)
    theirs_is_refactor = any(t in theirs_txt for t in REFACTOR_TOKENS)
    if theirs_is_refactor:
        # compute-call site (or single-row dup re-call). Keep OURS; inject pit if
        # OURS is a real call body missing pit.
        if PREV_MACD in ours_txt and "pit_ws_trend" not in ours_txt:
            out = []
            for ln in ours:
                out.append(ln)
                if ln.strip() == PREV_MACD:
                    ind = ln[: len(ln) - len(ln.lstrip())]
                    out.append(PIT_INJECT_TMPL.format(ind=ind))
            return out, ("inject" if out != ours else "ours")
        return ours, "ours(drop-refactor)"
    # clean v69 addition (R1/R2/R3/weight_info/sim-slot) -> take THEIRS
    return theirs, "theirs"


def resolve_file(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    out, i = [], 0
    actions = []
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            j = i + 1
            ours = []
            while j < len(lines) and not lines[j].startswith("======="):
                ours.append(lines[j]); j += 1
            theirs = []
            k = j + 1
            while k < len(lines) and not lines[k].startswith(">>>>>>>"):
                theirs.append(lines[k]); k += 1
            resolved, act = resolve_block(ours, theirs)
            actions.append(act)
            out.extend(resolved)
            i = k + 1
        else:
            out.append(lines[i]); i += 1
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)
    return actions


def main():
    if len(sys.argv) < 2:
        print("usage: resolve_weekly_conflict.py <worktree> [files...]"); sys.exit(2)
    wt = sys.argv[1]
    files = sys.argv[2:] or ["database/models/core.py", "simulator.py"]
    ok = True
    for rel in files:
        p = os.path.join(wt, rel)
        if not os.path.exists(p):
            print(f"  [skip] {rel} (not found)"); continue
        with open(p, encoding="utf-8") as f:
            if "<<<<<<<" not in f.read():
                print(f"  [clean] {rel} (no markers)"); continue
        acts = resolve_file(p)
        with open(p, encoding="utf-8") as f:
            txt = f.read()
        markers = txt.count("<<<<<<<") + txt.count(">>>>>>>")
        try:
            py_compile.compile(p, doraise=True); compiles, err = True, ""
        except py_compile.PyCompileError as e:
            compiles, err = False, str(e)
        status = "OK" if (markers == 0 and compiles) else "FAIL"
        if status != "OK":
            ok = False
        from collections import Counter
        print(f"  [{status}] {rel}: {len(acts)} conflict(s) {dict(Counter(acts))}, "
              f"markers_left={markers}, compiles={compiles}")
        if not compiles:
            print(f"        compile error: {err[:200]}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
