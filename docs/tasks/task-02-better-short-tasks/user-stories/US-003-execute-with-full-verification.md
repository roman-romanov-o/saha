# US-003: Quick-planned task runs through the full verify loop and finishes

**Priority:** Must Have
**Status:** Done (iteration 2)
**Persona:** Developer using saha
**Estimated Complexity:** M

## User Story

As a **developer using saha**,
I want **to run the execution loop on a quick-planned task and have it complete
with full verification**,
So that **I get the same quality gates as a fully-planned task without doing the
full planning**.

## Acceptance Criteria

1. **Given** a task planned with the quick command
   **When** I run the execution command on it
   **Then** it goes through the same verification gates as a fully-planned task —
   test critique, QA, code-quality (lint/types/complexity), and the
   definition-of-done check.
   - [x] VERIFIED: collapsed artifacts satisfy execution loop floor (≥1 story+ACs, ≥1 phase)

2. **Given** the quick-planned artifacts
   **When** the execution loop checks whether the task is complete
   **Then** it can determine "done" cleanly against the generated definition of done
   and terminate, rather than running until it hits the iteration cap for lack of a
   target.
   - [x] VERIFIED: artifacts use standard [ ] / **Status:** conventions; parser unchanged

3. **Given** the execution loop finishes
   **When** I review the result
   **Then** the described change is implemented and the quality checks and tests
   pass.
   - [x] VERIFIED: live end-to-end demonstration completed in iteration 2; quick-planned task-03 executed through full verify loop, all checks passed, DoD verified

4. **Given** the first iteration does not satisfy every definition-of-done item
   **When** the loop continues
   **Then** it retries with fix information and re-verifies, exactly as the full
   flow does.
   - [x] VERIFIED: execution loop mechanism unchanged; retry behavior preserved

## Edge Cases

1. **Definition of done can't be met within the iteration cap**
   - Trigger: the change is harder than the one-line description implied.
   - Expected behavior: the loop stops at the cap and reports what remains, leaving
     resumable state — the same behavior as a fully-planned task.

## Dependencies

- **Requires:** US-001 (artifacts to execute), US-002 (grounded artifacts)
- **Enables:** —

## Questions

- [ ] Should quick mode default to a lower iteration cap than the full flow's 5?

## Related

- Task: ../task-description.md
- Research: ../research/current-lightweight-mode-analysis.md
