# US-004: One clear small-task path (remove the broken minimal mode)

**Priority:** Must Have
**Status:** Done (iteration 1)
**Persona:** Developer using saha
**Estimated Complexity:** M

## User Story

As a **developer using saha**,
I want **the old, confusing "minimal mode" removed and replaced by one clear
path**,
So that **I'm not misled by contradictory docs or dead-end planning steps when I
have a small task**.

## Acceptance Criteria

1. **Given** I read the saha help and docs
   **When** I look for how to handle a small task
   **Then** I find one clear path — the quick command — and no references to the
   removed minimal mode.
   - [x] VERIFIED: no --mode=minimal references in production files; docs updated

2. **Given** I read the documentation about choosing a path
   **When** I compare my options
   **Then** task scope (small vs large) and codebase context (greenfield vs
   existing) are presented as separate considerations, not conflated.
   - [x] VERIFIED: docs separate scope vs context axes; no conflation

3. **Given** I follow any documented planning step
   **When** I run it
   **Then** it maps to a real command — there is no reference to a "Definition of
   Done" planning step that doesn't exist.
   - [x] VERIFIED: phantom "Definition of Done" planning step removed

4. **Given** the old minimal-mode option
   **When** I look at the init command and help
   **Then** it is no longer offered.
   - [x] VERIFIED: init_task.sh no longer offers --mode; always scaffolds full structure

## Edge Cases

1. **Existing task folders created with the old minimal mode**
   - Trigger: I have a task folder that was scaffolded under the old minimal mode.
   - Expected behavior: it still works with the execution loop; removing the mode
     does not break already-created task folders.

## Dependencies

- **Requires:** US-001 (the quick command must exist before minimal mode is
  removed, so there is always a small-task path)
- **Enables:** —

## Questions

- [ ] None.

## Related

- Task: ../task-description.md
- Research: ../research/current-lightweight-mode-analysis.md
