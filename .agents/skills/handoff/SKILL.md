---
name: handoff
description: Summarize the current conversation into a timestamped handoff file under .Codex/handoffs/ and emit a copy-paste prompt that boots a new chat with full context. Use when the user types /handoff to pass work to a fresh session (e.g. context window filling up, switching machines, ending a long iteration).
---

# /handoff — Save context + generate a new-chat boot prompt

Goal: produce a single self-contained handoff file that a fresh Codex session can read to continue exactly where this one left off, plus a short prompt the user pastes into the new chat.

## When to invoke

The user types `/handoff` (no args needed). Optional arg: `/handoff <slug>` adds a slug to the filename for easier later identification (e.g. `/handoff v46-svd-recalibration`).

## Output location

Write to `.Codex/handoffs/<timestamp>[_<slug>].md` where timestamp is `YYYY-MM-DD-HHMM` in local time. Create the directory if it doesn't exist (`.Codex/handoffs/`).

## What the handoff file MUST contain

Structure the file with these sections in this order. Keep each section terse but complete — a fresh agent should be able to act without re-asking questions.

```markdown
# Handoff — <one-line summary of the session's goal>

Generated: <ISO timestamp>
Branch: <git branch>
HEAD: <short sha> "<commit subject>"

## Goal
<1-3 sentences: what the user is trying to accomplish in this thread>

## Status
<bullet list: what's done, what's in flight, what's blocked>

## Decisions made this session
<bullet list: choices the user explicitly approved or rejected, with brief rationale.
This is the most important section — it prevents the new chat from re-litigating settled points.>

## Files touched
<list of `path:line_range` references with one-line description of the change>

## Files read but not modified (reference context)
<list of paths the new chat should know exist and roughly what's in them>

## Pending work — concrete next steps
<numbered list. Each item has: WHAT to do, WHERE (file:line), and any constraints.
Be specific enough that the new chat doesn't need to ask "what should I do first".>

## Open questions for the user
<bullet list: things THE USER needs to answer before work proceeds. Empty if none.>

## What NOT to do
<bullet list: dead ends already explored, approaches the user rejected, regressions to avoid.
Pull from anything the current session learned the hard way.>

## Commands / state to be aware of
<background processes still running, scheduled tasks, env vars set,
uncommitted changes, branches that need pushing, etc.>

## Reference links
<paths to relevant docs, experiment dirs, or prior handoffs that the new chat
should load when starting>
```

## Steps

1. **Gather repo state** in parallel:
   - `git rev-parse --short HEAD` and `git log -1 --pretty=%s` — current commit
   - `git rev-parse --abbrev-ref HEAD` — branch name
   - `git status --porcelain` — uncommitted changes (summarize, don't paste full diff)
   - `git diff --stat` — files modified this session

2. **Synthesize the conversation** — re-read the actual conversation in your context and extract:
   - The originating goal (the user's first non-trivial request)
   - Decisions: anything where the user said "yes do that" / "no don't" / "let's go with X" — capture both the choice and the why
   - Concrete file edits made (with line refs)
   - Anything the user asked you to remember or that you flagged as risky
   - Pending tasks you were about to do or partway through
   - Approaches that failed and the user explicitly told you not to retry

   Do NOT just dump the conversation chronologically. Synthesize.

3. **Write the file** to `.Codex/handoffs/<timestamp>[_<slug>].md` using the template above. Aim for 100-300 lines — enough to be useful, short enough to actually be read.

4. **Echo the boot prompt** as the final message to the user. Format it as a fenced code block they can copy directly. Template:

   ```
   Continuing work from a prior session. Read the handoff at
   `.Codex/handoffs/<filename>` first — it has the full context (goal,
   decisions made, pending steps, what NOT to do). Then proceed with the
   pending work listed there. Don't re-litigate decisions already made.
   ```

   If a slug was provided, optionally include a one-line description after the path so the user remembers what it was about.

5. **Brief confirmation** to the user: file path written + line count, plus the copy-paste prompt. That's it — don't restate the whole handoff content.

## What NOT to do

- Do not paste raw conversation transcripts into the file. Synthesize.
- Do not include large code blocks. Reference `file:line` instead.
- Do not commit the handoff file automatically — leave it untracked. The user decides whether to commit (handoffs are usually ephemeral).
- Do not write the handoff to project docs (`.Codex/docs/`) — those are for durable knowledge. Handoffs go in `.Codex/handoffs/` and accumulate as a session log.
- Do not skip the "What NOT to do" section even if it feels redundant. It's the single highest-value block for preventing rework.
- Do not include secrets, API keys, or full DB connection strings even if they appeared in the session.

## Tone

The handoff is written for a future agent (possibly you, possibly a different model) starting cold. Be direct, factual, no hedging. If something is uncertain, say so explicitly ("user has not yet confirmed X") rather than implying confidence.
