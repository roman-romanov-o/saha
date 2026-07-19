# Phase 01: Add `/saha:quick` and remove `--mode=minimal` ✓ (iteration 1)

**Status:** Complete (iteration 1)
**Estimated Effort:** M
**Dependencies:** None

## Objective

Ship the lightweight small-task on-ramp: a new `/saha:quick "<task>"` command
mirrored across all runners, and end-to-end removal of the broken `--mode=minimal`
planning mode. Reuse the existing execution loop with no agent changes.

## Scope

### Stories Included

| Story | Priority | Complexity | Status |
|-------|----------|------------|--------|
| US-001 | Must Have | L | [x] Done (iteration 1) |
| US-002 | Must Have | M | [x] Done (iteration 1) |
| US-003 | Must Have | M | [x] Done (iteration 2) |
| US-004 | Must Have | M | [x] Done (iteration 1) |

## Implementation Steps

### Step 1: New `/saha:quick` command (US-001, US-002)

**Files to Create/Modify:**
- `claude_plugin/commands/quick.md` - source command: one-pass plan-only recipe
  (light codebase scan → scaffold via `init_task.sh` → write collapsed
  `task-description.md` + `user-stories/US-001.md` + `implementation-plan/phase-01.md`
  → stop and tell user to run `/saha:execute`). Handles missing-description and
  large-scope edge cases. No `planning_reviewer` pass.
- `.codex/commands/quick.md`, `.gemini/commands/quick.md`,
  `.claude/commands/saha:quick.md` - mirrored per `saha sync` conventions.

**Acceptance Criteria:**
- [x] `/saha:quick` exists in all four runner dirs with consistent content
- [x] Recipe is plan-only and ends by directing the user to `/saha:execute`
- [x] Recipe includes a light grep/glob scan step and the collapsed artifact set
      using `[ ]` checkboxes + `**Status:**` lines

### Step 2: Remove `--mode=minimal` end-to-end (US-004)

**Files to Create/Modify:**
- `claude_plugin/scripts/init_task.sh` - drop `--mode` arg, validation, and all
  minimal/full branches; always scaffold the full folder set; remove `Mode:` from
  README + final output.
- `claude_plugin/scripts/help.sh`, `claude_plugin/commands/saha.md` - remove MODES
  section; add `/saha:quick` small-task path + split small/large workflow.
- `claude_plugin/commands/init.md`, `status.md`, `contracts.md`, `decide.md`,
  `verify.md` - remove `--mode` / "minimal mode" / "full mode only" references.
- `claude_plugin/skills/task-structure/SKILL.md` - delete "Minimal Mode" section
  and the phantom "Definition of Done" planning step; document scope-vs-context.
- `docs/user-guide.md` - remove minimal mode; add short-task quick start +
  Planning Paths section separating scope (small/large) from context
  (greenfield/existing).
- Re-sync all of the above to `.codex/`, `.gemini/`, `.claude/`.

**Acceptance Criteria:**
- [x] No `--mode=minimal` / planning "minimal mode" references in scripts, command
      docs, SKILL.md, user-guide.md, saha.md, help.sh (the `verify --mode=method`
      flag is unrelated and retained)
- [x] Phantom "Definition of Done" planning step removed
- [x] Docs separate scope (small vs large) from context (greenfield vs existing)
- [x] `init_task.sh` still scaffolds correctly with no `--mode`; existing task
      folders remain valid for the execution loop

### Step 3: Reuse the execution loop unchanged (US-003)

**Files to Create/Modify:**
- None (no agent changes). The collapsed artifacts use the same `[ ]` /
  `**Status:**` conventions the `execution-*` agents already parse.

**Acceptance Criteria:**
- [x] `/saha:quick` artifacts satisfy the loop's floor (≥1 story+ACs, ≥1 phase) so
      DoD/QA/manager parse and terminate without agent changes

## Definition of Done

Phase is complete when ALL of the following are true:

- [x] US-001, US-002, US-004 acceptance criteria are fully met (iteration 1)
- [x] US-003 acceptance criteria fully met (iteration 2); live E2E demonstration verified AC-3
- [x] `bash -n` passes on every edited `.sh` (all runner mirrors)
- [x] Existing tests pass (incl. `tests/unit/commands/test_plugin_sync.py`)
- [x] All runner mirrors are byte-identical to their `claude_plugin/` source

**Phase 1 Status: COMPLETE (iteration 2)**
- 4 of 4 user stories fully done (US-001, US-002, US-003, US-004)
- All implementation mechanics in place and verified
- Live E2E validation completed: quick-planned task executed through full verify loop with all checks passed
