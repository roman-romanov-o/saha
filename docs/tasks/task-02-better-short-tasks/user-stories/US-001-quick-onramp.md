# US-001: One-command lightweight planning for a small task

**Priority:** Must Have
**Status:** Done (iteration 1)
**Persona:** Developer using saha
**Estimated Complexity:** L

## User Story

As a **developer using saha**,
I want to **describe a small task in a single command and get the minimum planning
artifacts generated in one pass**,
So that **I don't have to run the full multi-step planning chain for a 1-2 file
change**.

## Acceptance Criteria

1. **Given** I have a small change in mind
   **When** I run the quick command with a one-line description of the task
   **Then** a collapsed set of planning artifacts is produced in a single pass — a
   short task description, one user story whose acceptance criteria serve as the
   definition of done, and one implementation phase.
   - [x] VERIFIED: quick.md exists; scaffolds folder via init_task.sh; writes task-description, US-001 w/ [ ] ACs, phase-01

2. **Given** the quick planning pass has finished
   **When** I look at what it did
   **Then** it stops and tells me to run the execution command next — it does not
   start writing code or running the execution loop on its own.
   - [x] VERIFIED: quick.md is plan-only; hands off to /saha:execute

3. **Given** I run the quick command
   **When** the artifacts are generated
   **Then** no separate review-and-approve step is required before I can proceed to
   execution.
   - [x] VERIFIED: no planning_reviewer pass; no review required

4. **Given** no task folder exists yet for this change
   **When** I run the quick command
   **Then** the folder structure it needs is created automatically as part of the
   same single pass, and it becomes the current task.
   - [x] VERIFIED: folder structure created by init_task.sh in single pass

## Edge Cases

1. **Missing description**
   - Trigger: I run the quick command without describing the task.
   - Expected behavior: I get a clear prompt or error asking for the one-line
     description; nothing is generated.

2. **Description that is actually a large task**
   - Trigger: I describe something that clearly spans many components.
   - Expected behavior: the command still produces a single collapsed plan, but
     surfaces that the scope looks large and that the full planning flow may fit
     better.

## Dependencies

- **Requires:** US-002 (the single pass includes a light codebase scan)
- **Enables:** US-003 (execution runs on the generated artifacts)

## Questions

- [ ] Should the generator ever emit more than one phase for a borderline task, or
      always exactly one phase in quick mode?

## Related

- Task: ../task-description.md
- Research: ../research/current-lightweight-mode-analysis.md
