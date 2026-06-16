# Research: Is Sahaidachny needed given GitHub spec-kit?

**Date:** 2026-06-15
**Status:** Complete
**Author:** Research agent (critical / skeptical mode)

## Summary

spec-kit and Sahaidachny overlap heavily on the **planning** layer (spec → plan →
tasks ≈ stories → decisions → contracts → test-specs), and on that axis spec-kit
wins on distribution, agent coverage, and maintenance. Sahaidachny's *defensible*
differentiator is its **closed-loop autonomous execution-and-verification engine**
(test-critique → QA → code-quality gates → DoD decision, with state/resume, token
accounting, and subscription billing) — which spec-kit deliberately does **not**
do. That moat is real today but narrowing, and it is unrelated to "short tasks,"
which neither tool handles well — that gap is the actual opportunity for this task.

## What each tool actually is

### spec-kit (github/spec-kit)
- **Owner/distribution:** GitHub. Integrates with **30+ coding agents** (Copilot,
  Claude Code, Gemini, Cursor, Codex, Qwen, Goose, …). Installed via `uvx specify
  init`. Extensions + presets ecosystem (Jira, code review, CI Guard, Architecture
  Guard).
- **Commands:** `/speckit.constitution`, `/specify`, `/clarify`, `/plan`,
  `/tasks`, `/analyze`, `/implement`, plus `/checklist`, `/taskstoissues`.
- **Artifacts:** installs `.specify/` (constitution, bash scripts, templates) and
  `specs/<feature>/` → `spec.md`, `plan.md`, `tasks.md`, `data-model.md`,
  `research.md`, `quickstart.md`, `contracts/`.
- **Execution model:** `/speckit.implement` parses tasks and drives the agent to
  build them in dependency order — but **the agent implements in one interactive
  session with human review gates**. There is no multi-iteration verifier loop,
  no enforced lint/type/complexity gate, no DoD agent deciding when to stop.
- **Workflows (newer):** YAML, resumable, state-persisting, with if/then/else,
  loops, and **gate** steps. But the built-in pattern chains *planning* commands
  with mandatory human gates (`specify → gate → plan`); it does **not** describe
  autonomous implement→verify→iterate.

### Sahaidachny (this repo)
- **Owner/distribution:** solo project on PyPI; `saha sync` targets 3 runners
  (Claude Code, Codex, Gemini).
- **Planning:** init → research → task → stories → decide → contracts → test-specs
  → plan → verify. More *granular* than spec-kit (separate user stories, DD design
  decisions, e2e/integration/unit test-specs).
- **Execution (the real differentiator):** an agentic loop across multiple context
  windows — implementation (TDD) → **test-critique** → **QA** → **code-quality**
  (ruff/ty/complexity actually run) → manager → **DoD agent that terminates the
  loop**. State persisted to `.sahaidachny/<task>-execution-state.yaml` (resumable),
  per-phase **token usage tracking** + `saha stats`, and **subscription-billed
  in-session execution** (Agent-tool dispatch, no `claude -p`, no API credits).

## Validated assumptions

| Assumption | Status | Evidence |
|---|---|---|
| spec-kit covers the planning/spec workflow Sahaidachny offers | ✅ Confirmed | spec/plan/tasks/contracts/research/data-model templates |
| spec-kit has cross-artifact consistency checking like `/saha:verify` | ✅ Confirmed | `/speckit.analyze` + `/checklist` |
| spec-kit runs the same closed-loop autonomous verify-and-iterate execution | ❌ Incorrect | `/implement` = single-session, agent-driven, human-gated; no QA/DoD verifier loop |
| spec-kit has no resumable orchestration (a Sahaidachny-only trait) | ⚠️ Partial | spec-kit **workflows** are now resumable + state-persisting, but for *planning* with human gates, not autonomous implement→verify |
| spec-kit tracks token/iteration economics | ❌ Not found | no per-phase token accounting / "done after N iterations" equivalent |
| Either tool is well-suited to *short* tasks | ❌ Incorrect | both are heavyweight; full constitution→…→tasks (or init→…→plan) is overkill for a 1–2 file change |

## Critical assessment

1. **The planning layer is largely commoditized.** Competing with GitHub on "more
   granular planning markdown" is a losing axis — they win on distribution (30+
   agents), trust, ecosystem, and maintenance. "We have separate DD files and
   unit/integration/e2e test-specs" is a difference of degree, not kind.

2. **The execution loop is the only defensible moat — and it's genuinely
   differentiated.** spec-kit explicitly stops at "automate planning, human judges
   the rest." Sahaidachny actually runs implement→critique→QA→quality-gate→DoD
   across context windows, enforces ruff/ty/complexity as code (not a self-assessed
   checklist), and tracks the economics. Nothing in spec-kit does this.

3. **But the moat is narrowing.** spec-kit workflows already added resumable,
   gated, state-persisting orchestration. If GitHub extends gates past `/implement`
   with verification loops, the execution advantage shrinks fast. Betting the
   product on "we orchestrate" is risky; betting it on "we *verify* and enforce
   quality autonomously" is more durable.

4. **Short tasks are whitespace for *both* tools.** This is the most useful finding
   for `task-02`. spec-kit's constitution→specify→clarify→plan→tasks and
   Sahaidachny's full hierarchy are both overkill for small changes. Raw prompting
   handles short tasks but with no verification. A lightweight path that **skips the
   full hierarchy but keeps the autonomous verify/DoD/quality loop** would
   differentiate from spec-kit (heavyweight, no exec loop), from raw prompting (no
   verification), and from Sahaidachny's own current heavyweight flow.

## Risks identified

1. **Redundancy risk** (High) — Maintaining a full parallel planning stack against
   GitHub's is unsustainable for a solo project. *Mitigation:* stop competing on
   planning; consider **interop** — consume spec-kit's `spec.md`/`plan.md`/
   `tasks.md` as input to Sahaidachny's execution loop, turning a competitor into an
   upstream.
2. **Moat erosion** (Medium) — spec-kit workflows trend toward automation.
   *Mitigation:* deepen the verification/quality-gate/DoD differentiation, which is
   harder to copy than command-chaining.
3. **Scope risk for this task** (Medium) — "better short tasks" could be built as
   yet another heavyweight variant. *Mitigation:* explicitly design the short-task
   path around the *execution* moat (verify + DoD), not more planning artifacts.

## Recommendations

- **Proceed with `task-02`, but reframe it.** The honest answer to "is our tool
  needed when spec-kit exists?" is: **the planning half is not differentiated; the
  autonomous execution-and-verification half is.** Lead with that.
- **Position the short-task feature as the lightweight on-ramp to the execution
  loop** — minimal/no hierarchy, but keep TDD + QA + DoD + ruff/ty/complexity. That
  is something neither spec-kit nor raw prompting offers for small changes.
- **Strongly consider spec-kit interop** as a design decision: ingest spec-kit
  artifacts → run Sahaidachny's verify loop. Complement, don't duplicate.
- **Decision needed from user (see summary):** is `task-02` (a) a lightweight
  short-task planning+execution path, or (b) also a spec-kit interop layer? This
  changes the user stories materially.

## Decision (2026-06-15)

**Scope chosen: lightweight execution on-ramp.** `task-02` adds a minimal/no-hierarchy
path for small changes that still runs the autonomous verify loop (TDD → QA → DoD →
ruff/ty/complexity), reusing the existing execution engine. It does **not** add a new
planning stack and (for now) does **not** include spec-kit interop. Differentiates
from spec-kit (heavyweight, no exec loop) and raw prompting (no verification).

Deferred (not in this task): spec-kit artifact interop; any new planning artifacts.

## Open questions

- For "short tasks," what's the upper bound (single file? single phase? < N
  acceptance criteria?) that should trigger / gate the lightweight path?
- Does the short-task path skip planning artifacts entirely, or generate a single
  collapsed spec (task + inline acceptance criteria) the execution loop can consume?
- How does the DoD agent define "done" without the usual user-stories/test-specs
  inputs it currently reads from the bundle?

## Sources

- [github/spec-kit](https://github.com/github/spec-kit)
- [Spec Kit documentation](https://github.github.com/spec-kit/)
- [spec-kit/workflows](https://github.com/github/spec-kit/tree/main/workflows)
- [Spec-driven development — GitHub Blog](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/)
- [Spec-driven development — Microsoft for Developers](https://developer.microsoft.com/blog/spec-driven-development-spec-kit)
