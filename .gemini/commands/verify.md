---
description: Verify planning artifacts are complete and consistent
argument-hint: [task-path] [--mode=manual|playwright|script|test]
allowed-tools: Read, Glob, Grep, Bash, AskUserQuestion, mcp__playwright__browser_navigate, mcp__playwright__browser_snapshot, mcp__playwright__browser_click
---

# Verify Artifacts

Verify planning artifacts are complete, consistent, and ready for implementation.

## Arguments

- **task-path** (optional): Path to task folder
  - If not provided, auto-detects using:
    1. Current task from `.sahaidachny/current-task` (set via `saha use`)
    2. Most recent task folder in `docs/tasks/`
  - If no context found, asks the user
- `--mode=<mode>`: Verification mode
  - `manual` (default): User reviews and approves
  - `playwright`: UI verification via browser automation
  - `script=<path>`: Run custom verification script
  - `test`: Run test suite

## Execution

### 1. Artifact Inventory

Scan task folder and catalog all artifacts:

```
Artifacts Found:
├── task-description.md (1.2KB, modified 2024-01-15)
├── user-stories/
│   ├── US-001-login.md
│   ├── US-002-logout.md
│   └── US-003-password-reset.md
├── design-decisions/
│   └── DD-001-jwt-auth.md
├── code-changes/
│   └── auth-api.md
├── test-specs/
│   ├── e2e/user-auth-flow.md
│   ├── integration/auth-api.md
│   └── unit/token-validator.md
└── implementation-plan/
    ├── phase-01-foundation.md
    └── phase-02-core-auth.md
```

### 2. Completeness Check

Verify required artifacts exist.

**Minimum the execution loop needs:**
- [ ] task-description.md
- [ ] At least 1 user story with checkable `[ ]` acceptance criteria
- [ ] At least 1 implementation phase

**Recommended for larger tasks (full planning flow):**
- [ ] At least 1 test spec
- [ ] At least 1 design decision
- [ ] code changes (if API changes involved)

Report missing artifacts with suggestions.

### 3. Consistency Check

Validate cross-references are valid:

**Stories ↔ Tests:**
- Every user story should have related test specs
- Every test spec should reference valid story IDs

**Stories ↔ Decisions:**
- Design decisions should reference affected stories
- Stories with technical notes should have supporting decisions

**Stories ↔ Phases:**
- Every story should be assigned to a phase
- No story should appear in multiple phases

**Contracts ↔ Tests:**
- code changes should have integration test coverage

Report inconsistencies:
```
Inconsistencies Found:
- US-003 has no test coverage
- DD-001 references non-existent US-005
- Phase 02 includes US-004 which doesn't exist
```

### 4. Quality Check

Evaluate artifact quality:

**Task Description:**
- [ ] Has clear problem statement
- [ ] Success criteria are measurable
- [ ] Scope is defined (in/out)

**User Stories:**
- [ ] Follow "As a... I want... So that..." format
- [ ] Have acceptance criteria
- [ ] Have priority assigned

**Test Specs:**
- [ ] Have clear expected results
- [ ] Include test data
- [ ] Cover error cases

**Implementation Plan:**
- [ ] Phases have clear objectives
- [ ] Steps are actionable
- [ ] Definition of done is specified

### 5. Architecture Model Validation

Only when `docs/architecture/` exists (skip silently otherwise — the model is
opt-in).

**DSL validation:**
- If the `likec4` CLI is on PATH (`command -v likec4`), run it against
  `docs/architecture/`:
  ```bash
  likec4 validate docs/architecture
  ```
  A non-zero exit (DSL error, dangling reference) is a verify **failure**,
  like any other inconsistent artifact. (Do not use `likec4 export json` for
  pass/fail — it exits 0 even when the model has errors.)
- CLI absent → skip with a one-line note ("likec4 CLI not found — DSL
  validation skipped"). Never a failure; never block planning on node tooling.

**View references:**
- Collect view ids referenced from DD docs (`**Architecture Views:**` lines in
  `{task_path}/design-decisions/DD-*.md`).
- Get the actual view list: with the CLI, the keys of `views` in
  `likec4 export json --skip-layout -o /tmp/likec4-verify.json docs/architecture`
  (delete the file afterwards); without it, `view <id>` declarations in
  `docs/architecture/*.c4`.
- Verify every referenced view id exists in that list.
- A DD referencing a non-existent view id is an inconsistency — report it like
  the other cross-reference errors in step 3.

### 6. Mode-Specific Verification

#### Manual Mode (default)

Present summary and ask user to confirm:

```
Verification Summary:
✅ 5/5 required artifacts present
✅ 12/12 cross-references valid
⚠️ 2 quality suggestions

Quality Suggestions:
1. US-002 acceptance criteria could be more specific
2. Phase 02 missing estimated effort

Approve artifacts as ready for implementation?
```

#### Playwright Mode

For UI-related tasks, verify against existing UI:

```javascript
// Navigate to relevant pages
// Take snapshots
// Verify current state matches assumptions in research
```

Report discrepancies between planned changes and current UI.

#### Script Mode

Run custom verification script:

```bash
bash $SCRIPT_PATH $TASK_PATH
```

Script should exit 0 for success, non-zero for failure.
Capture and report stdout/stderr.

#### Test Mode

If tests already exist, run them using the project's resolved test command
(`.sahaidachny/stack.yaml` `test.command`, else auto-detect from marker files —
saha is not Python-specific):

```bash
# Whatever the stack resolves to, e.g.:
pytest -q          # Python (pyproject.toml)
swift test         # Swift (Package.swift)
npm test           # Node (package.json)
cargo test         # Rust (Cargo.toml)
go test ./...      # Go (go.mod)
```

### 7. Generate Verification Report

Create `{task_path}/verification-report.md`:

```markdown
# Verification Report

**Date:** YYYY-MM-DD
**Mode:** Manual | Playwright | Script | Test
**Result:** ✅ Passed | ⚠️ Passed with Warnings | ❌ Failed

## Summary

| Check | Status | Details |
|-------|--------|---------|
| Completeness | ✅ | All required artifacts present |
| Consistency | ✅ | All references valid |
| Quality | ⚠️ | 2 suggestions |
| Architecture model | ✅ | DSL valid, all referenced views exist (omit row if no `docs/architecture/`) |

## Artifacts Verified

| Artifact | Status | Notes |
|----------|--------|-------|
| task-description.md | ✅ | Complete |
| US-001-login.md | ✅ | Complete |
| US-002-logout.md | ⚠️ | Acceptance criteria vague |
| ... | ... | ... |

## Issues Found

### Warnings

1. **US-002 Acceptance Criteria**
   - Current: "User is logged out"
   - Suggestion: Add specific assertions (session cleared, redirect URL)

### Errors

_None_

## Recommendations

- [ ] Address warnings before implementation
- [ ] Consider adding integration tests for auth flow

## Approval

- [ ] Artifacts approved for implementation
- Approved by: [Name]
- Date: YYYY-MM-DD
```

### 8. Update Task README

Update `{task_path}/README.md`:
- Add verification status
- Update overall planning status

## Example Usage

```
/saha:verify docs/tasks/task-01-auth
/saha:verify --mode=manual
/saha:verify --mode=playwright
/saha:verify --mode=script=./scripts/validate-task.sh
/saha:verify --mode=test
```

## Output

- Verification report displayed
- `{task_path}/verification-report.md` created
- `{task_path}/README.md` updated
