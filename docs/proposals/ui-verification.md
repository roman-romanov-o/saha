# Autonomous UI Verification — Design Proposal

Status: **approved design, not yet implemented**. Produced from three adversarial
review rounds (28 findings total; all blockers/majors resolved below).

Goal: the agentic loop *experiences* the product — drives real user journeys,
takes screenshots, judges them — so a task is never declared Done on checkbox
state alone. No human verifies each button.

## Design rules

1. The loop must experience the product before declaring it done — scripted
   journeys + screenshots, never checkbox state alone.
2. Environment failures halt loudly. They never become `fix_info`, never
   silently pass.
3. Subjective polish can trigger fix rounds but can never block completion
   indefinitely.
4. Completion guarantees live in orchestrator **code**, not agent prompts.
5. v1 is web-only with one new gate; mobile and design references layer on
   afterward.

## 1. stack.yaml — opt-in `ui:` block

```yaml
ui:
  start: "npm run dev"                            # orchestrator starts before UI phases
  ready_check: "curl -sf http://localhost:5173"   # polled until ready_timeout → env failure
  stop: "kill $UI_PID"                            # explicit; e.g. "pkill -f MyApp", "xcrun simctl terminate com.me.App"
  seed: "npm run seed:test"                       # reset + seed; orchestrator runs before EVERY UI QA/smoke pass
  prepare: "npm run build"                        # heavy one-time setup (build, simulator boot) — orchestrator time
```

No `ui:` block → no lifecycle, no UI gate, zero change for CLI/library
projects. Teardown is always an explicit command (an `open MyApp.app` detaches
via launchd; a process-group kill can't reach it). `/saha:init` proposes a
filled block for web/mobile stacks; the human approves once.

## 2. Journeys artifact (planning time, semantic)

One journey per user story plus a golden path, written as semantic steps —
"add an item named `milk-{run_id}`, expect it in the list" — never selectors
(the UI doesn't exist at planning time). Test data is uniquely suffixed per run
so journeys stay idempotent through the smoke-fail → fix → re-smoke path even
when seeding is imperfect. The QA agent maps steps to elements at runtime from
the accessibility snapshot; structural a11y assertions are primary, screenshots
carry only what structure can't see.

**Triage protocol** for "tap Add finds nothing":

- `journey_failed` — element mapped, assertion failed → a bug → `fix_info`.
- `journey_blocked` — no plausible mapping → routed to the **manager** (owner
  of journey revisions), who either updates a stale journey or converts it to
  `fix_info` if the feature is genuinely missing per the story. Blocked
  journeys never churn the implementer directly.

## 3. UI smoke gate — trigger, budget, enforcement

- **Trigger:** always runs at the first iteration where all stories claim Done
  — no budget precondition (a "reserve N iterations" rule deadlocks: you can't
  control when all-Done occurs).
- **Grace budget:** entering smoke grants up to **+2 grace iterations** beyond
  `max_iterations` — a one-condition change in `_should_continue`
  (`saha/orchestrator/loop.py:318`) — so a smoke failure at iteration 10 still
  gets a fix round.
- **If smoke fails with grace exhausted:** the loop exits in a new distinct
  terminal state, `COMPLETED_SMOKE_FAILED` — never a false green, never a
  silent max-iter stall.
- **Enforcement in code:** at the `task_complete` gate
  (`saha/orchestrator/loop.py:570`), the orchestrator refuses
  `_finalize_completion` without a passed smoke record **when and only when
  the UI gate is active** (`ui:` block present *and* `verify: ui` ACs exist).
  Non-UI tasks are completely unaffected.
- **On smoke failure:** write `fix_info` with evidence, then run a **second
  manager pass in the same iteration** (reuse the
  `_run_manager_on_iteration_stop` pattern) to un-check the affected ACs —
  required because the manager has already marked everything Done by smoke
  time; without the un-check, the next iteration's artifact bundle ships those
  stories as `body: null` stubs and the implementer gets `fix_info` with no
  story context.

## 4. Judging — binary checklist, capped, honest about debt

Fresh judge sessions have no memory, so letter grades oscillate and burn fix
rounds on variance. Each journey's end-state screenshot is judged against a
binary checklist, each item pass/fail with the screenshot as named evidence:

- No clipped, overlapping, or off-screen elements
- Every element the journey referenced is visible
- Text readable — no white-on-white, raw exception text, or placeholder/lorem content
- Empty and error states render styled, not blank
- Layout coherent at 1280px and 390px widths

Failures → `fix_info` with screenshot path + failed checklist line. **Cap: 2
UI-fix rounds per journey**; after the cap, remaining items are recorded as
`ui_debt` in the final summary and the loop proceeds. Flake containment: one
automatic retry per journey after a stability wait (network idle, no layout
shift); pass-on-retry = pass-with-warning; only a twice-failed journey
produces `fix_info`.

## 5. Runner, timeouts, doctor

- `prepare` runs in orchestrator time; the UI agent only drives and judges.
  **Per-phase timeout config** is added — UI phases default 1800s; the
  runner's current 300s default (`saha/runners/claude.py:109`) would kill a
  smoke pass mid-flight and today's fail-open handling would count that as a
  pass.
- UI phases are **pinned to the claude runner** (MCP browser tools + vision).
  If `RunnerRegistry` routes QA elsewhere, the pin wins with a visible warning
  at loop start. Saha ships its own Playwright MCP config, passed via
  `--mcp-config` only for UI phases.
- **Doctor** runs after AC/journey parsing and checks only what the present
  verify methods need — including that `verify: ui` ACs and the `ui:` block
  are present *together* (either alone is a config error, reported as such).
  Failures halt with the exact missing dependency. Non-UI tasks never see a UI
  doctor check.

## 6. Evidence and state

- Screenshots under `.sahaidachny/<task>/evidence/iter-N/` (gitignored, pruned
  to last 3 iterations) — session scratchpads don't survive `saha resume`.
- `fix_info` remains "current," but every entry appends to `fix_history` in
  `state.context`; the implementer prompt includes the last 2, so alternating
  functional/UI failures can't erase each other. The implementer prompt is
  updated to `Read` referenced screenshots — the picture is the fix spec.
- Additive `LoopPhase.UI_SMOKE` + `ArtifactView.UI_QA`; resume-safe (a wiped
  `smoke_passed` on resume just re-runs smoke). Smoke rows appear in the
  plan-progress table.
- New `verify: ui` tag; all QA agent variants updated in one release; unknown
  tags degrade to `manual` with a logged warning.

## Rollout

1. **v1 (web):** `ui:` block, lifecycle, scoped doctor, semantic journeys,
   smoke gate with grace iterations + `COMPLETED_SMOKE_FAILED`,
   binary-checklist judging, evidence dirs, `fix_history`, per-phase timeouts.
2. **v2:** per-iteration UI QA on the active story's journey; `verify: ui` in
   planning prompts.
3. **v3:** mobile via Maestro (Bash-based `qa-mobile` variant, simulator
   lifecycle in `ui:`); `/saha:design` tokens + mockups as implementer
   guidance only (screenshot-vs-mockup diffing stays out — unsound across
   renderers, invites judge oscillation).

## Why this is production-grade

Completion is enforced in orchestrator code against smoke evidence, and every
failure mode has a defined, non-silent exit: env failure halts, smoke failure
gets grace rounds or a distinct terminal state, polish debt is reported
instead of looping forever.
