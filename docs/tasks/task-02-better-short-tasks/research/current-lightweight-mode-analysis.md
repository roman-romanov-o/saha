# Research: Why the current "lightweight mode" isn't usable, and how to fix it

**Date:** 2026-06-15
**Status:** Complete
**Author:** Research agent (critical / skeptical mode)

## Summary

The current "lightweight mode" (`--mode=minimal`) does **not** solve the user's
problem. It was designed for **greenfield/prototype** projects, not **small tasks
in an existing codebase** — two orthogonal axes the tool conflates. It barely
reduces effort: it only drops 2 of 7 artifact *folders* while keeping the same
~6 sequential slash commands, each spawning a generator **and** a reviewer
subagent behind a human gate. On top of that, "minimal mode" is defined **four
contradictory ways** across the codebase and references a planning step
(`Definition of Done`) that has no command. The fix is not another mode — it's a
**single on-ramp command** that auto-generates the *minimum* artifacts the
execution loop requires and hands straight off to the existing verify loop (the
moat).

## What "lightweight mode" actually is today

`--mode=minimal` is a flag on `init_task.sh`. Its *entire* behavioral effect:

1. Skips creating `design-decisions/` and `code-changes/` folders
   (`claude_plugin/scripts/init_task.sh:111-114`, `247-267`, `281-285`).
2. Drops those two rows from the README progress table
   (`init_task.sh:117-143` vs `144-173`).

That's it. There is **no Python code** that branches on mode — `grep -rniE
"lightweight|mode" saha/**/*.py` finds nothing mode-related in the package. Only
two *planning commands* gate on mode: `/saha:contracts` and `/saha:decide` say
"full mode only" (`claude_plugin/commands/contracts.md:24`,
`decide.md:24`). Every other command (`research`, `task`, `stories`,
`test-specs`, `plan`, `verify`, `execute`) behaves **identically** regardless of
mode.

**Implication:** "minimal" does not make the workflow shorter or cheaper in any
meaningful way. The user still runs the full command chain.

## Finding 1 — "Minimal" targets the wrong axis (greenfield ≠ small)

Every description of minimal mode frames it around **greenfield / prototype**,
not **small scope**:

- `docs/user-guide.md:286` — "**Minimal Mode (Greenfield Projects)** … For new
  projects or throwaway prototypes where codebase context isn't critical."
- `claude_plugin/skills/task-structure/SKILL.md:307` — "**Minimal Mode** — For
  greenfield projects."

The user's need is the opposite: **small changes inside an existing, real
codebase** (where research/context still matters, but the ceremony does not).
The tool has no concept of "small task." It only has "greenfield vs production,"
which is a different dimension entirely. This mismatch is the root cause of "not
usable enough."

| | Small scope | Large scope |
|---|---|---|
| **Greenfield** | ← user wants this cheap | minimal mode aims here |
| **Existing codebase** | ← **user is HERE**, no support | full mode |

Evidence: `count_acceptance_criteria` / the whole `US-XXX` + `DD-XXX` +
`test-specs/{e2e,integration,unit}` + `phase-XX` hierarchy
(`SKILL.md:17-47`) is sized for multi-component features.

## Finding 2 — The friction is the command *chain*, not the folders

The intended workflow is a sequence of manual slash commands, each gated by a
human approval (`docs/user-guide.md:282-345`, `saha.md:40-48`):

```
init → task → research → stories → verify → plan → execute     (full)
init → task → stories → verify → (DoD?) → execute              ("minimal")
```

Each planning command launches **two** subagents — a generator, then a
`planning_reviewer` — e.g. `research.md` → research agent + reviewer
(`commands/research.md` "Review mode: research"); same pattern in `task.md:164`,
`stories.md:181`, `plan.md:229`, etc. So a "minimal" run is still ~4-5 manual
commands × 2 agents × a human gate each. For a 1-2 file change this is wildly
disproportionate. Removing two folders does nothing to fix it.

**This is the real cost the user feels** — not artifact count, but the number of
manual, gated, agent-spawning steps before any code is written.

## Finding 3 — "Minimal mode" is defined four incompatible ways

| Source | Definition of minimal mode |
|---|---|
| `saha.md:53`, `help.sh:43` | "Lightweight (**task + plan only**)" |
| `user-guide.md:286-302` | task → stories → verify → **DoD** → verify (greenfield) |
| `SKILL.md:307-311` | task → stories+verify → **DoD**+verify (greenfield) |
| `init_task.sh` (actual) | creates research, stories, **test-specs, plan** dirs; omits DD + code-changes |

No two agree. Worse, three of them reference a **"Definition of Done" planning
step that has no command** — there is no `/saha:dod`; `execution-dod` is an
*execution-time* agent (`agents/execution-dod.md`), not a planning artifact. A
user following the docs hits a dead end. Any redesign must collapse these into
one coherent definition or delete the concept.

## Finding 4 — The execution loop (the moat) hard-requires stories+ACs+phases

This is the binding constraint on *how* lightweight we can go. The loop's
"done" definition is hard-coded around planning artifacts:

- `/saha:execute` DoD phase (`commands/execute.md:272-277`): "Task is COMPLETE
  only if — **ALL user stories** have status 'Done', **ALL acceptance criteria**
  are checked `[x]`, **ALL implementation phases** are complete."
- `execution-dod.md:116-128` repeats this and, when stories/ACs are missing,
  falls to `confidence: low` + `task_complete: false` + "manual review needed"
  (`execution-dod.md:129-154`, `203-233`). I.e. **without stories+ACs the loop
  cannot cleanly terminate** — it burns iterations until `max_iter`.
- The implementer reads `task_description`, active `user_stories`, active
  `implementation_plan` as its inputs (`execution-implementer.md:37-49`).
- QA verifies against acceptance criteria in `user-stories/*.md`
  (`commands/execute.md:191-197`).

**Minimum viable input to run the loop correctly:**
`task-description.md` + **≥1 user story with checkable ACs** + **≥1 implementation
phase**. `test-specs` are consumed if present but the implementer can derive
tests from ACs; `design-decisions` and `code-changes` are already optional
(full-mode-only). This defines the floor the lightweight path must still produce
— it cannot skip stories/ACs/phases, only the *ceremony of authoring them*.

## Finding 5 — Two execution paths have drifted (reliability risk)

There are two orchestrators and they disagree about artifact delivery:

- **Python** (`saha run`): `saha/orchestrator/loop.py:34,149` uses
  `ArtifactBundler` → status-filtered, size-budgeted, view-specific snapshots
  injected into each agent's context.
- **Slash** (`/saha:execute`): "**No artifact pre-bundling**: each subagent
  reads what it needs from disk" (`commands/execute.md:314-315`), and tells the
  implementer to "Read the task artifacts directly from disk"
  (`commands/execute.md:115`).

But the agent definitions assume bundling: `execution-implementer.md:23-30` and
`execution-dod.md:23-28` both say "artifacts are pre-loaded under
`## Static artifacts → artifacts.*` … do **NOT** use Read/Glob." So under
`/saha:execute` the agents are told two contradictory things. For *small* tasks
this is harmless (tiny artifacts, Read is cheap) — but it's latent debt the
lightweight design should not build new confusion on top of.

## Validated assumptions

| Assumption (from the request) | Status | Evidence |
|---|---|---|
| A "lightweight mode" exists | ✅ Confirmed | `--mode=minimal` in `init_task.sh:20,56` |
| It meaningfully reduces effort for small tasks | ❌ Incorrect | only omits 2 folders; same command chain (`init_task.sh:111-114`) |
| Minimal mode is for small tasks | ❌ Incorrect | it's for *greenfield* (`user-guide.md:286`, `SKILL.md:307`) |
| Minimal mode is coherently specified | ❌ Incorrect | 4 contradictory definitions (Finding 3) |
| The exec loop could run on a bare task description alone | ⚠️ Partial | needs ≥1 story+ACs+phase or DoD never terminates (`execute.md:272`, `execution-dod.md:129-154`) |
| Lightweight = strip verification to go faster | ❌ Wrong direction | verification IS the moat (see `spec-kit-vs-sahaidachny.md`); strip *planning ceremony*, keep QA/quality/DoD |

## Risks identified

1. **Building yet another mode/hierarchy** (High). The instinct will be to add a
   third mode or new artifact set. That repeats the mistake. *Mitigation:* one
   on-ramp **command** that reuses the existing loop; no new artifact types.
2. **Over-collapsing past the loop's floor** (High). If "fast" means skipping
   stories/ACs/phases, DoD degrades to low-confidence and the loop spins to
   `max_iter` (`execution-dod.md:129-154`). *Mitigation:* always auto-generate
   the Finding-4 minimum (1 story + ACs + 1 phase).
3. **Stripping verification to feel lighter** (High). That deletes the only
   differentiator vs raw prompting. *Mitigation:* lighten *planning* ceremony
   only; keep TDD + QA + ruff/ty/complexity + DoD.
4. **Inconsistency debt compounding** (Medium). Four definitions + a phantom DoD
   command already confuse users. *Mitigation:* fix/delete them as part of this
   task; don't ship a 5th definition.
5. **Two-orchestrator drift** (Medium). Slash vs Python disagree on bundling
   (Finding 5). *Mitigation:* base the lightweight path on one path (slash
   `/saha:execute` is the subscription-billed, in-session one users want) and
   accept no-bundling for small artifacts.

## Recommendation

**Don't improve `--mode=minimal`. Replace the concept with a single on-ramp
command** (working name `/saha:quick "<one-line task>"`, or `/saha:go`) that:

1. **Takes the task inline** as an argument — no interactive `/saha:task` session.
2. **One planning pass, no separate reviewer gate**: a single agent reads the
   codebase briefly (existing-codebase context still matters) and writes the
   *minimum* the loop needs — a collapsed `task-description.md`, **one** user
   story with 2-5 acceptance criteria, and **one** `implementation-plan` phase.
   Optionally one inline test-spec; otherwise let the implementer derive tests
   from ACs.
3. **Hands straight off to the existing execution loop** (`/saha:execute`
   internals): implement → test-critique → QA → code-quality → manager → DoD.
   Keep every verification gate — that's the moat.
4. **Tunes only the dials, not the gates**: lower default `--max-iter` (e.g. 2-3
   for small tasks), and optionally let trivial changes skip `test-critique`
   (the heaviest planning-side check) while keeping QA + ruff/ty + DoD.

This differentiates exactly as the strategy research concluded
(`spec-kit-vs-sahaidachny.md:104-126`): heavier than raw prompting (it
*verifies*), lighter than spec-kit and full saha (no planning hierarchy).

**Also in scope (cleanup, low cost, high clarity):**
- Collapse the four definitions of "minimal" into one, or delete the mode in
  favor of the new command.
- Remove the phantom "Definition of Done" planning step from docs/SKILL, or make
  it real.
- Separate the two axes in docs: *scope* (small/large) vs *context*
  (greenfield/existing).

## Decisions (2026-06-15, from user)

1. **Trigger:** a new explicit **`/saha:quick "<task>"`** command. No
   auto-detection of "small scope" — the user opts in explicitly.
2. **Gates:** **keep all execution-verification gates** (test-critique → QA →
   code-quality → DoD). Do **not** strip them. Instead, **simplify the input the
   gates consume.** In the full flow, correctness is validated against formal
   `test-specs/` + acceptance criteria. In the quick flow, the gates validate
   against a **single, simpler natural-language "Definition of Done"** instead of
   the full test-spec/AC hierarchy.
3. **`--mode=minimal`:** **delete it entirely** — including the greenfield
   framing, the phantom "Definition of Done" planning step in the docs, and the
   four contradictory definitions. The on-ramp command replaces it.

### What these decisions imply for design

- **The "Definition of Done" finally becomes real.** Quick mode produces one
  lightweight `definition-of-done.md` (a `[ ]` checklist) instead of
  `user-stories/US-*.md` + `test-specs/{e2e,integration,unit}/*.md`. This is the
  artifact the QA + DoD gates verify against.
- **Constraint to resolve (was Finding 4):** the current DoD/QA/manager agents
  parse `[x]`/`[ ]` checkboxes and `**Status:**` lines out of `user-stories/*.md`
  and count phases (`execution-dod.md:66-104`, `execute.md:191-197,272-277`).
  For the simpler DoD to drive the *same* gates without a rewrite, the quick-mode
  DoD should be a **checklist of `[ ]` criteria** the existing parsers can read —
  either written to a `definition-of-done.md` the agents are pointed at, or
  emitted as a single collapsed `user-stories/US-001.md` whose ACs *are* the DoD.
  Pick one in design; the second reuses the existing loop with zero agent changes.
- **One planning pass, no `planning_reviewer`.** The friction reduction comes
  from collapsing init→task→stories→test-specs→plan into one inline generation of
  {task-description, definition-of-done checklist, single phase} — not from
  removing the execution gates.
- **Dials, not gates:** lower default `--max-iter` for quick tasks; keep
  ruff/ty/complexity + QA + DoD. (Open: exact default cap.)

### Remaining open question

- **DoD artifact shape:** dedicated `definition-of-done.md` (cleaner mental model,
  needs the QA/DoD/manager agents pointed at it) vs. a single collapsed
  `user-stories/US-001.md` whose acceptance criteria serve as the DoD (zero agent
  changes, reuses existing parsing). Recommend the collapsed-US-001 approach for
  v1 to ship on the existing loop unchanged, then consider a dedicated file later.

## Sources (codebase)

- `claude_plugin/scripts/init_task.sh` — mode behavior (folders only)
- `claude_plugin/commands/execute.md` — loop recipe, DoD definition, no-bundling note
- `claude_plugin/agents/execution-dod.md`, `execution-implementer.md` — artifact requirements + degradation
- `saha/orchestrator/loop.py`, `saha/orchestrator/artifact_bundler.py` — Python path + bundler
- `claude_plugin/skills/task-structure/SKILL.md`, `docs/user-guide.md`, `claude_plugin/commands/saha.md`, `scripts/help.sh` — the four conflicting minimal-mode definitions
- `research/spec-kit-vs-sahaidachny.md` — strategic framing (verify loop is the moat)
