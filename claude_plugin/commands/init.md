---
description: Initialize a new Sahaidachny task folder structure
argument-hint: <task-name> [--path=docs/tasks]
allowed-tools: Bash
---

# Initialize Sahaidachny Task

Create a new hierarchical task structure for planning.

> For a **small** change (1-2 files), use `/saha:quick "<one-line task>"` instead —
> it plans in one pass and hands straight off to `/saha:execute`.

## Arguments

- First argument: **Task name** (required) - Short descriptive name
- `--path=<path>`: Base path for tasks (default: docs/tasks)

## Execution

Run the init script:

```bash
bash .claude/scripts/init_task.sh $ARGUMENTS
```

This creates:

```
{base_path}/task-XX-{name}/
├── README.md                    # Task dashboard
├── user-stories/README.md
├── design-decisions/README.md
├── code-changes/README.md
├── implementation-plan/README.md
├── test-specs/
│   ├── README.md
│   ├── e2e/README.md
│   ├── integration/README.md
│   └── unit/README.md
└── research/README.md
```

## Example Usage

```
/saha:init user-authentication
/saha:init api-refactor --path=planning/tasks
```

The init script automatically sets the new task as the current task context
(`.sahaidachny/current-task`), so all subsequent commands will use it without
needing to specify the task path.

After initialization, follow the suggested next step shown in output.

## Toolchain (language-agnostic)

Saha is **not** Python-specific. The execution loop auto-detects the project's
build/test/quality commands from marker files at the repo root (`pyproject.toml`,
`Package.swift`, `package.json`, `Cargo.toml`, `go.mod`). To pin or override them —
or to tell the loop a target has **no** headless tests — create an optional
`.sahaidachny/stack.yaml`:

```yaml
# .sahaidachny/stack.yaml — overrides auto-detection
build:   { command: "swift build" }
test:    { command: "swift test", file_globs: ["**/*Tests.swift"] }
quality: { commands: ["swiftlint"], changed_files_only: true }
run:     { command: "swift run" }
# An empty command (e.g. test.command: "") tells the loop to SKIP that gate —
# use this for a UI-only target whose ACs are all verify:build / verify:manual.
```
