---
description: Show Sahaidachny help and available commands
---

Output the following help information to the user:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                     SAHAIDACHNY COMMANDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SMALL TASKS (one command, in Claude Code):

  /saha:quick "<task>" Plan a small change in one pass, then /saha:execute

PLANNING COMMANDS — large tasks (in Claude Code):

  /saha:init <name>    Initialize new task folder
  /saha:research       Deep codebase exploration
  /saha:task           Define task description
  /saha:stories        Generate user stories
  /saha:decide         Document decision points
  /saha:contracts      Define code changes
  /saha:test-specs     Create test specifications
  /saha:verify <item>  Verify planning artifacts
  /saha:plan           Generate execution plan
  /saha:status         Check planning progress

EXECUTION COMMANDS (in Claude Code — subscription-billed):

  /saha:execute [task-id]     Run agentic loop in this session
  /saha:resume  [task-id]     Resume interrupted task in this session

EXECUTION COMMANDS (in terminal — uses API credits):

  saha use <task-id>          Set current task context
  saha use                    Show current task
  saha use --clear            Clear current task
  saha run [task-id]          Run agentic loop (API-billed, headless)
  saha resume [task-id]       Resume task (API-billed, headless)
  saha status [task-id]       Check execution status
  saha clean [task-id]        Clean execution state

WORKFLOW — small task (1-2 file change):

  1. /saha:quick "<one-line task>"   (one-pass plan, auto-sets current task)
  2. /saha:execute                   (subscription) — or `saha run` for API

WORKFLOW — large task:

  1. /saha:init my-feature       (auto-sets current task)
  2. /saha:task                   (define what we're building)
  3. /saha:research               (explore codebase with context)
  4. /saha:stories
  5. /saha:verify stories
  6. /saha:plan
  7. /saha:execute                (subscription) — or `saha run` for API

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
