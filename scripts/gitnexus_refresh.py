"""
Refresh the GitNexus index WITHOUT producing a commit-worthy diff.

THE LOOP THIS KILLS
`gitnexus analyze` rewrites tracked files (the `<!-- gitnexus:start -->` block in CLAUDE.md
and AGENTS.md, plus .claude/skills/gitnexus/*/SKILL.md). An agent then sees a dirty tree,
commits it, and the commit moves HEAD — which makes the index report "stale", which prompts
another analyze. analyze -> push -> analyze -> push, forever.

The previous defence was a written rule ("do not analyze until after a successful push").
Rules are the wrong tool here for two reasons: they rely on every agent reading and obeying
them, and the rule itself lived in the GENERATED block, so `analyze` overwrote it (observed
2026-07-29). This removes the loop's fuel instead:

  1. FINGERPRINT GUARD — skip the run entirely when no indexable source has changed since the
     last analyze. Fingerprint is over tracked source files only, so a docs-only or
     generated-only commit never triggers a rebuild. Recorded in .gitnexus/ (gitignored), NOT
     keyed to a commit SHA, so committing does not invalidate it.
  2. RESTORE GENERATED REGIONS — after analyze, put the generated block and the generated
     SKILL.md files back to their committed content. The index on disk is fresh (that is the
     part that matters); the tracked files are byte-identical to HEAD, so there is nothing to
     commit and the cycle cannot start.

Net effect: analyze becomes idempotent from git's point of view. The committed symbol counts
go stale, which is harmless — CLAUDE.md already says not to hand-maintain them, and the live
numbers are in .gitnexus/meta.json.

  python scripts/gitnexus_refresh.py --check     # is a refresh needed?
  python scripts/gitnexus_refresh.py             # refresh iff needed, then restore
  python scripts/gitnexus_refresh.py --force     # refresh regardless
  python scripts/gitnexus_refresh.py --keep-generated   # leave the regenerated text in place
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

REPO = r"C:\Development\Trader"
STATE = os.path.join(REPO, ".gitnexus", "last_refresh.json")
BLOCK_FILES = ["CLAUDE.md", "AGENTS.md"]
GENERATED_DIRS = [".claude/skills/gitnexus"]
START, END = "<!-- gitnexus:start -->", "<!-- gitnexus:end -->"
# Extensions the analyzer actually indexes; a change outside these cannot alter the graph.
SOURCE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx")


def _git(*args, **kw):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, **kw)


def source_fingerprint():
    """Hash of (path, blob-sha) for every tracked source file. Independent of HEAD, so a
    docs-only commit does not change it — which is precisely what breaks the loop."""
    r = _git("ls-files", "-s")
    h = hashlib.sha256()
    n = 0
    for line in sorted(r.stdout.splitlines()):
        parts = line.split()
        if len(parts) < 4:
            continue
        blob, path = parts[1], parts[3]
        if not path.endswith(SOURCE_EXT):
            continue
        h.update(f"{path}:{blob}\n".encode())
        n += 1
    return h.hexdigest(), n


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def restore_generated():
    """Return tracked generated content to its committed state."""
    restored = []
    for d in GENERATED_DIRS:
        if os.path.isdir(os.path.join(REPO, d)):
            _git("checkout", "--", d)
            restored.append(d)
    for fn in BLOCK_FILES:
        path = os.path.join(REPO, fn)
        if not os.path.exists(path):
            continue
        head = _git("show", f"HEAD:{fn}")
        if head.returncode != 0:
            continue
        raw = open(path, "rb").read()
        # `git show` hands back LF; these files live as CRLF in the worktree. Writing LF
        # back leaves the file byte-different but content-identical, so git flags it as
        # modified with an EMPTY diff -- which looks exactly like the dirt we are trying to
        # eliminate. Remember the original convention and restore it after the splice.
        was_crlf = b"\r\n" in raw
        cur = raw.decode("utf-8")
        old = head.stdout
        if START not in cur or END not in cur or START not in old or END not in old:
            continue
        # Use the LAST occurrence, not the first. CLAUDE.md quotes these delimiters inside
        # its authored growth-policy prose, so index() finds that prose and a splice would
        # silently overwrite AUTHORED text with HEAD's copy — reverting real work whenever
        # the authored section has uncommitted edits. Caught 2026-07-29; it did no damage
        # only because that region happened to match HEAD at the time.
        def _span(text):
            a = text.rindex(START)
            return a, text.index(END, a) + len(END)
        c0, c1 = _span(cur)
        o0, o1 = _span(old)
        # Defence in depth: the generated block is a minority of these files. If the span
        # we are about to replace is most of the file, the delimiters are not where we
        # think they are — refuse rather than guess.
        if (c1 - c0) > 0.6 * len(cur) or (o1 - o0) > 0.6 * len(old):
            print(f"   !! {fn}: generated span looks wrong ({c1 - c0}/{len(cur)} chars) "
                  f"-- refusing to splice")
            continue
        merged = cur[:c0] + old[o0:o1] + cur[c1:]
        # The authored text outside the block must be byte-identical afterwards.
        if merged[:c0] != cur[:c0] or merged[len(merged) - (len(cur) - c1):] != cur[c1:]:
            print(f"   !! {fn}: splice would alter authored text -- refusing")
            continue
        if merged != cur:
            if was_crlf:
                merged = merged.replace("\r\n", "\n").replace("\n", "\r\n")
            open(path, "w", encoding="utf-8", newline="").write(merged)
            restored.append(f"{fn} (generated block)")
    return restored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep-generated", action="store_true",
                    help="leave regenerated text in the working tree (re-enables the loop; "
                         "only for deliberately refreshing the committed counts)")
    a = ap.parse_args()

    fp, nfiles = source_fingerprint()
    st = load_state()
    fresh = st.get("source_fingerprint") == fp
    have_index = os.path.exists(os.path.join(REPO, ".gitnexus", "meta.json"))

    if a.check:
        print(f"indexed source files : {nfiles}")
        print(f"index present        : {have_index}")
        print(f"last analyzed at     : {st.get('analyzed_at', 'never')}")
        print(f"refresh needed       : {not (fresh and have_index)}")
        return 0 if (fresh and have_index) else 1

    if fresh and have_index and not a.force:
        print(f"index already current for {nfiles} source files "
              f"(analyzed {st.get('analyzed_at')}) -- nothing to do")
        return 0

    runner = os.path.join(REPO, ".gitnexus", "run.cjs")
    cmd = (["node", runner, "analyze"] if os.path.exists(runner)
           else ["npx", "--yes", "gitnexus", "analyze"])
    # ALWAYS full re-index (analyze-level --force; distinct from this script's own
    # --force, which merely bypasses the fingerprint guard). gitnexus 1.6.9's
    # INCREMENTAL writeback dies on this repo whenever the writable set contains
    # symbol-bearing (.py) files: native "Failed calling LOWER: Invalid UTF-8"
    # (2026-08-21, 2026-08-22) and once 0xC0000005 (2026-08-18) -- while full
    # rebuilds and md-only incrementals are consistently green. Store hygiene was
    # ruled out: a freshly rebuilt store whose every node/edge string passed a
    # LOWER sweep still failed the next .py incremental. See traps.md
    # "GitNexus native crash poisons state" (2026-08-22 correction). Cost: ~2 min
    # warm / ~6 min cold per refresh, only when source actually changed. Remove
    # when a fixed gitnexus version ships (re-test: drop flag, touch a .py file).
    cmd.append("--force")
    print(f"running: {' '.join(cmd)}", flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=REPO, shell=(os.name == "nt"))
    if r.returncode != 0:
        print(f"analyze FAILED (rc={r.returncode}) -- not recording a fingerprint")
        return r.returncode
    print(f"analyze finished in {time.time() - t0:.0f}s")

    if a.keep_generated:
        print("--keep-generated: regenerated text LEFT in the working tree. Commit it "
              "deliberately, and expect the index to read stale until the next refresh.")
    else:
        restored = restore_generated()
        print("restored to committed state (nothing to commit):")
        for x in restored:
            print(f"   {x}")

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump({"source_fingerprint": fp, "source_files": nfiles,
                   "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, indent=2)

    dirty = _git("status", "--short").stdout.splitlines()
    gen = [l for l in dirty if any(k in l for k in
           ("CLAUDE.md", "AGENTS.md", ".claude/skills/gitnexus"))]
    print(f"\ngenerated files still dirty: {len(gen)} "
          f"({'LOOP RISK' if gen and not a.keep_generated else 'clean -- loop broken'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
