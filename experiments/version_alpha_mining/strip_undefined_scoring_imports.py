"""Neutralize v68-era feature pollution that the v69 cherry-pick bundles into PRE-feature
silos (whose scoring.py predates the feature builders). Two failure shapes, both -> runtime
error -> 0 rows (py_compile does NOT catch either):

  (a) undefined IMPORT: `from database.utils.scoring import build_kijun_pct_map ...` where
      the silo's scoring.py lacks it -> 'cannot import name' at recalc time.
  (b) dangling CALL: `_ret10d_sigma_map = build_ret10d_sigma_map(ph_rows_asc)` with NO
      import (the import line stayed as the silo's older OURS) -> 'name ... is not defined'.

Fix, feature-aware (keeps a builder the silo legitimately defines, e.g. v44 keeps kijun):
  1. Remove `from database.utils.scoring import (...)` names NOT defined in this scoring.py.
  2. Neutralize the 3 known feature-map assignments to `<var> = {}` when their builder is
     undefined in scoring.py — covers BOTH shapes (the map is DEAD: the compute call is OURS
     = version-original and never passes it; even if referenced, `{}.get()` -> None graceful).

Map vars / builders that postdate various versions:
  _ret10d_sigma_map  <- build_ret10d_sigma_map  (v37 PCD)
  _kijun_pct_map     <- build_kijun_pct_map      (v44 ICH)
  _wv_force1_map     <- build_wv_force1_map      (v46 WVD)

Usage: python strip_undefined_scoring_imports.py <worktree>
Exits 0 only if core.py compiles and no removed alias is still referenced.
ALWAYS follow with a 1-stock dry-run (recalc_honest.sh does this) — the anchor of last resort.
"""
import os, re, sys, py_compile

MAP_BUILDERS = {
    '_ret10d_sigma_map': 'build_ret10d_sigma_map',
    '_kijun_pct_map': 'build_kijun_pct_map',
    '_wv_force1_map': 'build_wv_force1_map',
}

def defined_names(scoring_path):
    names = set()
    for ln in open(scoring_path, encoding="utf-8"):
        m = re.match(r'^(?:async\s+)?def\s+(\w+)', ln)
        if m: names.add(m.group(1))
        m = re.match(r'^(\w+)\s*=', ln)
        if m: names.add(m.group(1))
    return names

def main():
    wt = sys.argv[1]
    core = os.path.join(wt, "database/models/core.py")
    scoring = os.path.join(wt, "database/utils/scoring.py")
    defs = defined_names(scoring)
    lines = open(core, encoding="utf-8").readlines()

    # ---- pass 1: remove undefined `from database.utils.scoring import` names ----
    removed_aliases = set()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r'^(\s*)from database\.utils\.scoring import \($', ln)
        if m:
            j = i + 1
            entries = []
            while j < len(lines) and ')' not in lines[j]:
                e = lines[j]
                em = re.search(r'(\w+)(?:\s+as\s+(\w+))?\s*,?\s*$', e.strip())
                if em: entries.append((e, em.group(1), em.group(2) or em.group(1)))
                j += 1
            close = lines[j] if j < len(lines) else ")\n"
            kept = [(e, n, a) for (e, n, a) in entries if n in defs]
            for (_, n, a) in entries:
                if n not in defs: removed_aliases.add(a)
            if kept:
                out.append(ln); out.extend(e for (e, _, _) in kept); out.append(close)
            i = j + 1; continue
        m = re.match(r'^(\s*)from database\.utils\.scoring import (.+?)\s*$', ln)
        if m and '(' not in ln:
            indent, body = m.group(1), m.group(2)
            kept_parts, changed = [], False
            for p in [x.strip() for x in body.split(',') if x.strip()]:
                pm = re.match(r'(\w+)(?:\s+as\s+(\w+))?$', p)
                if pm and pm.group(1) not in defs:
                    removed_aliases.add(pm.group(2) or pm.group(1)); changed = True
                else:
                    kept_parts.append(p)
            if changed:
                if kept_parts:
                    out.append(f"{indent}from database.utils.scoring import {', '.join(kept_parts)}\n")
                i += 1; continue
        out.append(ln); i += 1

    # ---- pass 2: drop dead `_x = _removedalias(...)` lines, and neutralize the 3 known
    #             feature-map assignments to `{}` when their builder is undefined ----
    out2 = []
    for ln in out:
        am = re.match(r'^(\s*)(\w+)\s*=\s*(\w+)\(', ln)
        if am:
            indent, lhs, rhs = am.group(1), am.group(2), am.group(3)
            if rhs in removed_aliases:
                continue  # dead build using a removed import alias
            if lhs in MAP_BUILDERS and MAP_BUILDERS[lhs] not in defs:
                out2.append(f"{indent}{lhs} = {{}}\n")  # neutralize dangling/undefined builder
                continue
        out2.append(ln)

    open(core, "w", encoding="utf-8").writelines(out2)

    txt = open(core, encoding="utf-8").read()
    # stray = removed import aliases still referenced, OR any undefined builder still CALLED
    stray = [a for a in removed_aliases if re.search(r'\b'+re.escape(a)+r'\s*\(', txt)]
    for var, builder in MAP_BUILDERS.items():
        if builder not in defs and re.search(r'\b'+re.escape(builder)+r'\s*\(', txt):
            stray.append(builder + '()')
    try:
        py_compile.compile(core, doraise=True); compiles = True
    except py_compile.PyCompileError as e:
        compiles = False; err = str(e)
    print(f"removed undefined imports: {sorted(removed_aliases) or 'none'}")
    print(f"neutralized undefined feature-maps: " +
          str([v for v, b in MAP_BUILDERS.items() if b not in defs and re.search(re.escape(v)+r'\s*=\s*\{\}', txt)] or 'none'))
    print(f"stray undefined-builder calls after strip: {stray}  compiles: {compiles}")
    if stray or not compiles:
        if not compiles: print("compile error:", err[:200])
        sys.exit(1)

if __name__ == "__main__":
    main()
