# US-002: Codebase-grounded quick plan

**Priority:** Must Have
**Status:** Done (iteration 1)
**Persona:** Developer using saha
**Estimated Complexity:** M

## User Story

As a **developer using saha**,
I want **the quick planning pass to look at the relevant parts of my existing
codebase**,
So that **the generated definition of done and plan reference the real files and
patterns I'll be touching, not generic placeholders**.

## Acceptance Criteria

1. **Given** my task touches existing code
   **When** the quick plan is generated
   **Then** the acceptance criteria and implementation phase reference actual areas
   of my codebase rather than generic or invented placeholders.
   - [x] VERIFIED: light targeted grep/glob scan grounds artifacts in real code

2. **Given** the planning pass inspects the codebase
   **When** it produces the artifacts
   **Then** the inspection is targeted and brief — it grounds the artifacts without
   running a separate, full research phase.
   - [x] VERIFIED: no full research phase; brief targeted scan only

## Edge Cases

1. **Brand-new area with no existing code**
   - Trigger: the task is in a part of the project that doesn't exist yet.
   - Expected behavior: the plan proceeds from what I described and does not
     fabricate references to files that aren't there.

2. **Task description names something not found in the codebase**
   - Trigger: I reference a component the scan can't locate.
   - Expected behavior: the plan notes the gap rather than silently assuming the
     component exists.

## Dependencies

- **Requires:** —
- **Enables:** US-001 (this is the grounding step inside the single pass)

## Questions

- [ ] Should the light scan leave behind a one-file research note, or keep zero
      research artifacts in quick mode?

## Related

- Task: ../task-description.md
- Research: ../research/current-lightweight-mode-analysis.md
