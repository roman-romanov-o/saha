---
description: One-pass lightweight planning for a small task, then hand off to /saha:execute
argument-hint: "<one-line task>" [--path=docs/tasks]
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

# Saha Quick

Plan a **small task** (a 1-2 file fix, a tiny feature) in a **single inline pass**
and hand straight off to the execution loop. This collapses the full planning
chain (`init → task → research → stories → verify → plan`) into one generation of
the *minimum* artifacts `/saha:execute` needs:

- a short `task-description.md`,
- one `user-stories/US-001.md` whose acceptance criteria **are** the Definition of Done,
- one `implementation-plan/phase-01.md`.

This command is **plan-only**. It does NOT write product code, does NOT run the
execution loop, and does NOT launch a `planning_reviewer` pass. When it finishes
it tells you to run `/saha:execute`.

> For large or multi-component work, use the full planning flow
> (`/saha:init` → `/saha:task` → `/saha:research` → `/saha:stories` →
> `/saha:verify` → `/saha:plan`) instead.

## Arguments

- **task description** (required, positional): a one-line description of the small
  change, e.g. `/saha:quick "add a --json flag to the status command"`.
- `--path=<path>`: base path for the task folder (default: `docs/tasks`).

## Edge cases

- **Missing description.** If no task description is given (empty `$ARGUMENTS`, or
  only flags), STOP immediately with a clear message asking for a one-line
  description, e.g.:
  `Usage: /saha:quick "<one-line task>". Please describe the small change in one line.`
  Do **not** create any folder or artifact.
- **Scope looks large.** If the description clearly spans many components (e.g.
  "rewrite the auth system", "migrate the whole DB layer", several unrelated
  deliverables), still produce a single collapsed plan, but add a note at the top
  of `task-description.md` and in your final message that the scope looks large
  and the full planning flow (`/saha:init` … `/saha:plan`) may fit better.

## Execution recipe

Do all of the following in **this one pass**. Do not pause for approval between steps.

### Step 1 — Validate input

Parse `$ARGUMENTS`. Separate the positional task description from any `--path=`
flag. If there is no non-flag text, handle the **Missing description** edge case
above and stop.

### Step 2 — Light, targeted codebase scan (grounding)

Run a **brief, targeted** scan — not a full research phase. Pull 3-8 keywords from
the task description and use `Grep`/`Glob` to locate the real files, symbols, and
patterns the change will touch. Examples:

- `Glob` for likely files/dirs by name.
- `Grep` for the function/class/command/flag names mentioned.
- `Read` only the few files that look directly relevant (skim, don't deep-dive).

Goals and limits:

- Ground the artifacts in **actual files and patterns** — reference real paths you
  found, not invented placeholders.
- If the task is in a brand-new area with no existing code, proceed from the
  description and do **not** fabricate file references.
- If the description names something the scan can't locate, note the gap in the
  artifacts rather than assuming it exists.
- Keep it short. This grounds the plan; it is not a research deliverable. Do **not**
  write a `research/` artifact.

**Also detect the project's stack** (saha is not Python-specific). Glob the repo root
for a marker file and note the toolchain so the AC/DoD reference real commands:

| Marker | test | quality |
|--------|------|---------|
| `pyproject.toml` / `setup.py` | `pytest -q` | `ruff check`, `ty`, `complexipy` |
| `Package.swift` / `*.xcodeproj` | `swift test` | `swiftlint` |
| `package.json` | `npm test` | `eslint`, `tsc --noEmit` |
| `Cargo.toml` | `cargo test` | `cargo clippy` |
| `go.mod` | `go test ./...` | `go vet`, `golangci-lint run` |

If the detected stack is **non-Python** and `.sahaidachny/stack.yaml` does not exist,
write one now so `/saha:execute` resolves the right commands. Example for Swift:

```yaml
# .sahaidachny/stack.yaml
build:   { command: "swift build" }
test:    { command: "swift test", file_globs: ["**/*Tests.swift"] }
quality: { commands: ["swiftlint"], changed_files_only: true }
run:     { command: "swift run" }
```

Use `test.command: ""` for a target that genuinely has no headless tests (UI-only).

### Step 3 — Scaffold the task folder

Create the folder structure and set it as the current task by running the init
script. Derive a short slug from the task description for the task name:

```bash
bash .claude/scripts/init_task.sh "<short-slug-from-description>" --path=<path or docs/tasks>
```

This creates `docs/tasks/task-XX-<slug>/` with the standard subfolders and writes
`.sahaidachny/current-task`. Capture the created `task-XX-<slug>` id and its path
from the script output (it prints `Created task folder:` and `Task ID:`).

### Step 4 — Write `task-description.md`

Overwrite `<task_path>/task-description.md` with a **short** description grounded
in the scan. Keep it tight — this is a small task. Include:

```markdown
# Task Description: <Title derived from the one-line task>

**Task ID:** TASK-XX
**Status:** Ready
**Last Updated:** <today's date YYYY-MM-DD>
**Planned via:** /saha:quick (lightweight single-pass)

> [Only if scope looks large] **Note:** this looks larger than a typical quick
> task; the full planning flow (`/saha:init` … `/saha:plan`) may fit better.

## Problem Statement

<1-3 sentences: what the user asked for, restated.>

## Affected Components

- `<real/path/from/scan>` - <how it's affected>
- <... only real paths found in Step 2; if none, say "New area — no existing files">

## Success Criteria

1. [ ] <the change is implemented in the files above>
2. [ ] <tests/quality checks pass>
```

### Step 5 — Write `user-stories/US-001.md` (the Definition of Done)

Overwrite `<task_path>/user-stories/US-001.md`. Its **acceptance criteria are the
Definition of Done** — the execution QA/DoD agents parse the `[ ]` checkboxes and
the `**Status:**` line, so these conventions are mandatory:

```markdown
# US-001: <Short title for the change>

**Priority:** Must Have
**Status:** Ready
**Persona:** Developer

## User Story

As a developer, I want <the change>, so that <the benefit>.

## Acceptance Criteria

- [ ] <Concrete, verifiable outcome 1, referencing the real files from the scan>   <!-- verify: automated -->
- [ ] <Concrete, verifiable outcome 2>   <!-- verify: automated -->
- [ ] <Concrete, verifiable outcome 3>   <!-- verify: build -->
- [ ] The project's tests and quality checks pass on the changed files   <!-- verify: automated -->
```

Rules:

- Emit **2-5** acceptance criteria. Each must be concrete and verifiable, grounded
  in the files found in Step 2. Avoid vague items the loop can't check.
- Always include a final AC for "tests + quality checks pass" so the verify loop
  has a quality target. Phrase it for the **detected stack** (e.g. "pytest + ruff/ty
  clean" for Python, "swift test + swiftlint clean" for Swift) — do not hardcode
  Python tools unless the project is Python.
- **Tag each AC with a verify method** (`<!-- verify: automated|build|manual: ... -->`):
  `automated` (default) for anything a headless test asserts; `build` for "it
  compiles/launches"; `manual: <how a human checks it>` for UI rendering / visual
  things no headless test can confirm. Manual ACs end the loop in
  `completed_pending_manual` for human sign-off instead of churning to max-iter.
- Use `- [ ]` checkboxes (the parsers also accept `- [x]`/`- [~]`) and the literal
  `**Status:**` line. Do not omit them.

### Step 6 — Write `implementation-plan/phase-01.md`

Overwrite `<task_path>/implementation-plan/phase-01.md` with **exactly one** phase
(quick mode always produces a single phase):

```markdown
# Phase 01: <Short phase name> ✓ pending

**Status:** Not Started
**Estimated Effort:** S
**Dependencies:** None

## Objective

<1-2 sentences on what this phase delivers.>

## Scope

### Stories Included

| Story | Priority | Complexity | Status |
|-------|----------|------------|--------|
| US-001 | Must Have | S | [ ] |

## Implementation Steps

### Step 1: <Component>

**Files to Create/Modify:**
- `<real/path/from/scan>` - <what changes>

**Acceptance Criteria:**
- [ ] <maps to a US-001 AC>

## Definition of Done

Phase is complete when ALL of the following are true:

- [ ] All US-001 automated/build acceptance criteria are checked (manual ACs go to
      human sign-off — they don't block completion)
- [ ] The project's tests pass (per the detected stack / `.sahaidachny/stack.yaml`)
- [ ] The project's quality checks are clean on changed files (e.g. ruff/ty for
      Python, swiftlint for Swift, eslint/tsc for Node)
```

### Step 7 — Update the task `README.md` (optional, light)

If `<task_path>/README.md` exists, you may set its Overview to the one-line task.
Don't spend effort here — the loop doesn't depend on it.

### Step 8 — Stop and hand off (do NOT execute)

Print a short summary and stop. Do **not** start `/saha:execute`, do **not** write
product code, do **not** launch any reviewer agent. End with the next step:

```
═══════════════════════════════════════════════
SAHA QUICK — plan ready (single pass)
  Task: <task-XX-slug>  (now the current task)
  Artifacts: task-description.md, user-stories/US-001.md, implementation-plan/phase-01.md
  [if scope looked large] Note: scope looks large — full planning may fit better.

  Next step: /saha:execute
═══════════════════════════════════════════════
```

## Notes

- **One pass, no gate.** Everything above happens in a single invocation with no
  separate review-and-approve step. The verification you keep is the *execution*
  loop (test-critique → QA → code-quality → DoD), which runs later under
  `/saha:execute`.
- **Reuses the loop unchanged.** The artifacts use the same `[ ]` / `**Status:**`
  conventions the execution agents already parse, so `/saha:execute` runs and
  terminates cleanly with no agent changes.
- **Quick = small scope, not greenfield.** This is the path for *small changes in
  an existing codebase*. Greenfield vs. existing codebase is a separate axis; the
  light scan in Step 2 handles existing-codebase grounding either way.

## Example Usage

```
/saha:quick "add a --json flag to saha status output"
/saha:quick "fix the off-by-one in pagination in api/list.py"
/saha:quick "rename Config.timeout to Config.timeout_seconds everywhere"
```
