# Task Description: Lightweight on-ramp for short tasks (`/saha:quick`)

**Task ID:** TASK-02
**Status:** Draft
**Last Updated:** 2026-06-15

## Problem Statement

Sahaidachny is only worth using for big tasks. For small changes (a 1-2 file fix,
a tiny feature), the planning ceremony is disproportionate, so users drop to raw
prompting and lose the one thing that makes saha valuable: the autonomous
verify loop.

### Current State

- The existing "lightweight" path, `--mode=minimal`, **does not solve this**:
  - It targets the wrong axis — it's framed for *greenfield / prototype*
    projects (`docs/user-guide.md:286`, `claude_plugin/skills/task-structure/SKILL.md:307`),
    not *small tasks in an existing codebase*.
  - Its *entire* behavioral effect is skipping two folders
    (`design-decisions/`, `code-changes/`) — `init_task.sh:111-114`. No Python
    branches on mode; only `/saha:contracts` and `/saha:decide` gate on it.
  - The friction is the **command chain** (`init → task → research → stories →
    verify → plan → execute`), where each planning command spawns a generator
    **and** a `planning_reviewer` subagent behind a human gate. Minimal mode
    leaves that chain fully intact.
  - "Minimal mode" is defined **four contradictory ways** (`saha.md:53`,
    `user-guide.md:286`, `SKILL.md:307`, `init_task.sh`), and three of them
    reference a "Definition of Done" planning step that **has no command**.
- The execution loop (the moat) is sound but hard-coupled to the full planning
  artifacts: its DoD is "ALL user stories Done, ALL acceptance criteria `[x]`,
  ALL phases complete" (`claude_plugin/commands/execute.md:272-277`,
  `agents/execution-dod.md:116-128`).

### Desired State

A single command — **`/saha:quick "<one-line task>"`** — that replaces the
multi-command planning chain with **one inline planning pass**, producing the
*minimum* artifacts the existing execution loop needs, so the user can go
straight to `/saha:execute`. The autonomous verify loop is kept intact; only the
*planning ceremony* is removed. `--mode=minimal` is deleted.

## Success Criteria

1. [ ] `/saha:quick "<task>"` exists and, in **one pass**, generates a collapsed
   artifact set sufficient for `/saha:execute` to run and terminate cleanly.
2. [ ] The single pass does a **light, targeted codebase scan** (grep/glob) to
   ground the generated artifacts in real files and patterns.
3. [ ] The generated artifacts include at least one checklist of `[ ]` criteria
   (the "Definition of Done") and at least one implementation phase, so the
   existing DoD/QA/manager agents parse and terminate correctly **without agent
   changes** (v1).
4. [ ] `/saha:quick` is **plan-only**: it stops after generating artifacts and
   tells the user to run `/saha:execute`. It does not auto-execute.
5. [ ] No `planning_reviewer` pass in the quick flow; the **execution**
   verify gates (test-critique → QA → code-quality → DoD) are all preserved.
6. [ ] Running `/saha:quick` then `/saha:execute` on a representative small task
   completes the change with passing QA + ruff/ty/complexity.
7. [ ] `--mode=minimal` is removed end-to-end: `init_task.sh`, command docs,
   `SKILL.md`, `user-guide.md`, `saha.md`, `help.sh` — including the phantom
   "Definition of Done" planning step and the four conflicting definitions.
8. [ ] Documentation clearly separates the two axes: *scope* (small vs large)
   and *context* (greenfield vs existing); `/saha:quick` is the small-task path.

## Scope

### In Scope

- New `/saha:quick "<task>"` slash command (Claude Code plugin command), mirrored
  to the other runners (`.codex/`, `.gemini/`, `claude_plugin/`) per existing
  sync conventions.
- One-pass planning generator that performs a light codebase scan and writes a
  **collapsed artifact set**: a short `task-description.md`, a single
  `user-stories/US-001.md` whose acceptance criteria serve as the Definition of
  Done, and a single `implementation-plan/phase-01.md`.
- Reuse of the existing `/saha:execute` loop unchanged.
- Deletion of `--mode=minimal` and cleanup of all its contradictory
  definitions + the phantom DoD step across docs/scripts/skills.
- Docs: a "short tasks" quick-start showing `/saha:quick` → `/saha:execute`.

### Out of Scope

- Auto-executing from `/saha:quick` (decided: plan-only; user runs `/saha:execute`).
- Auto-detecting "small scope" — `/saha:quick` is an explicit opt-in.
- A dedicated `definition-of-done.md` artifact with QA/DoD/manager agent changes
  (deferred; v1 reuses collapsed `US-001` ACs to avoid touching the loop).
- spec-kit artifact interop (deferred per `research/spec-kit-vs-sahaidachny.md`).
- Any new planning hierarchy or a replacement "mode" system.
- Changes to the Python orchestrator path (`saha run`) beyond what deletion of
  minimal mode requires.

## Constraints

| Type | Constraint | Reason |
|------|------------|--------|
| Technical | Reuse the existing execution loop with **no agent changes** in v1 | The loop is the moat; keep risk low and ship on what works |
| Technical | Quick-mode artifacts must use `[ ]` checkboxes + `**Status:**` lines | DoD/QA/manager agents parse these (`execution-dod.md:66-104`) |
| Technical | Must produce ≥1 story-with-ACs + ≥1 phase | Else DoD drops to low-confidence and the loop spins to `max_iter` |
| Product | Keep all execution verify gates; only remove planning ceremony | Verification is the differentiator vs raw prompting |
| Product | Mirror the command across all three runners | Existing `saha sync` convention (claude/codex/gemini) |

## Dependencies

### Prerequisites

- [x] Existing execution loop (`/saha:execute`, `execution-*` agents) — present.
- [x] Strategic framing decided — `research/spec-kit-vs-sahaidachny.md`.

### Blockers

- [ ] None known.

## Technical Context

From `research/current-lightweight-mode-analysis.md`:

- **Minimum viable input to the loop** = `task-description.md` + ≥1 user story
  with checkable ACs + ≥1 implementation phase. `test-specs`,
  `design-decisions`, `code-changes` are optional. This is the floor `/saha:quick`
  must hit — it removes the *authoring ceremony*, not the artifacts the loop reads.
- **Keep gates, simplify their input:** full flow validates correctness against
  `test-specs/` + formal ACs; quick flow validates against a simpler
  natural-language Definition of Done. For v1 the DoD is expressed as the ACs of a
  single collapsed `US-001.md`, so the existing parsers work unchanged.
- **Orchestrator drift to avoid building on:** `/saha:execute` reads artifacts
  from disk (no bundling, `execute.md:314-315`) while the Python path uses
  `ArtifactBundler` (`saha/orchestrator/loop.py:149`). For small artifacts this
  is harmless; `/saha:quick` should target the slash/in-session path.

### Affected Components

- `claude_plugin/commands/` (+ `.codex/`, `.gemini/`, `.claude/commands/`) — new
  `quick` command; remove `--mode` docs from `init`, `saha`, `status`.
- `claude_plugin/scripts/init_task.sh` — remove minimal-mode branches.
- `claude_plugin/scripts/help.sh`, `claude_plugin/commands/saha.md` — remove
  MODES section / fix workflow.
- `claude_plugin/skills/task-structure/SKILL.md` — delete "Minimal Mode" + DoD
  planning step; document the scope-vs-context distinction.
- `docs/user-guide.md` — remove minimal mode; add short-task quick start.

### Integration Points

- `/saha:execute` and the `execution-*` agents consume whatever `/saha:quick`
  writes — the integration contract is the on-disk artifact layout + checkbox /
  `**Status:**` conventions.

## Open Questions

- [ ] Default `--max-iter` for quick tasks (lower than full-mode's 5?).
- [ ] How many acceptance criteria / phases the generator should emit for a
  "typical" small task before it stops collapsing (keep it to one phase?).
- [ ] Whether `/saha:quick` should still drop a one-file `research/` note from its
  light scan, or keep zero research artifacts.

## References

- `research/current-lightweight-mode-analysis.md` — gap analysis + decisions
- `research/spec-kit-vs-sahaidachny.md` — strategic framing (verify loop is the moat)
- `claude_plugin/commands/execute.md`, `agents/execution-dod.md` — loop contract
