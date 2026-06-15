# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] - 2026-05-26

### Added
- **Subscription-billed execution**: new `/saha:execute` and `/saha:resume` slash commands run the full agentic loop **inside the active Claude Code session**, so usage is billed against the Claude subscription instead of API credits. No `claude -p` subprocess, no Agent SDK.
  - The slash commands dispatch each phase (implementation → test-critique → QA → code-quality → manager → DoD) to the existing native `execution-*` subagents via the `Agent` tool, and persist state to `.sahaidachny/<task>-execution-state.yaml` so they remain resumable.
  - Plugin bumped to `0.3.0` to surface the new commands in `/saha:` help.
- **Artifact bundling**: the orchestrator now loads task-folder content once and embeds view-filtered, status-aware snapshots directly into each subagent's prompt, so verifier agents stop re-`Read`ing the task folder every iteration.
  - Per-view policies (implementer/test-critique/QA/code-quality/manager/DoD) ship full bodies for active work, stubs for completed/draft items, and skip irrelevant slices.
  - mtime-keyed cache reused across phases and iterations, plus a soft size budget with progressive stubbing of the largest bodies.
  - Prompt layout reordered (static artifacts → iteration state → instructions) for prompt-cache friendliness; `execution-*` agent specs updated to read from the bundle.
- **Per-phase token usage tracking**: every agent invocation now records input/output/cache/reasoning token counts and timing into the persisted execution state via the new `PhaseTokenUsage` model on `IterationRecord.token_usage`.
- New `saha stats` CLI command with multiple views:
  - default: per-task summary table with **Outcome** column (`✓ done @ N`, `✗ failed @ N`, `▶ running`) and a footer summarizing iterations burned across tasks
  - `saha stats <task-id>`: iteration × phase breakdown with per-iteration DoD/Quality/Critique flags and a "Task completed after N iteration(s)" line
  - `saha stats <task-id> --by-phase`: phase totals across iterations for a single task
  - `saha stats --all`: phase totals aggregated across every saved task

### Changed
- `saha run` and `saha resume` CLI commands now print a yellow notice clarifying they shell out to `claude -p` and are API-billed; users wanting subscription billing are pointed at `/saha:execute` and `/saha:resume`.
- `/saha:` help text reorganized into "in Claude Code" (subscription) vs "in terminal" (API) sections.

## [0.8.2] - 2026-05-05

### Added
- **Planning of Execution**: Support for scheduling and delayed task execution.
- New `--delay` and `--at` flags for the `saha run` command to schedule background agent loops.
- Background execution spawning with detached processes for scheduled tasks.
- `LoopPhase.SCHEDULED` for tracking tasks awaiting execution.
- Enhanced `saha status` to display scheduled execution times.
- Background execution logs in `.saha_logs/saha-scheduled-<task-id>.log`.

## [0.7.4] - 2026-02-20

### Added
- Multi-target artifact sync (`saha sync`) supporting Claude Code, Codex CLI, and Gemini CLI — copies agents, skills, and commands into `.claude/`, `.codex/`, and `.gemini/` directories
- `codex` and `gemini` launcher commands alongside existing `claude` command
- Enhanced test critique schema with per-dimension quality scores (mocking, assertions, structure, coverage, independence)
- Coverage tracking in test critique output: `files_with_coverage` and `files_missing_coverage` fields
- 19 additional test quality patterns for richer issue detection (vague assertions, flaky timing, shared state, missing edge cases, etc.)

### Changed
- Manager agent now runs on every iteration stop rather than only at task end
- `TestCritiqueOutput.critique_passed` now requires grade A or B (previously A/B/C passed)

### Fixed
- Codex runner CI compatibility with mocked subprocess processes
- MyPy type narrowing in `CodexRunner` stream path
- Ruff formatting compliance in CI

## [0.7.1] - 2026-02-16

### Fixed
- Codex runner now properly streams JSON output with `--json` flag
- Added structured event display for Codex CLI output (commands, reasoning, messages)
- Real-time progress visibility when using `saha run` with Codex runner

## [0.7.0] - 2026-02-14

### Added
- Responsive interrupt handling (Ctrl+C) with graceful shutdown
- Runner visibility improvements
- Enhanced progress tracking

## [0.6.2] - 2026-02-11

### Added
- GitHub Actions CI workflow for automated testing
- GitHub Actions publish workflow for PyPI releases
- MIT License file
- This CHANGELOG

### Changed
- Updated installation instructions in README
- Added CI badges to README

### Fixed
- Repository URL in pyproject.toml

## [0.6.1] - 2026-02-09

### Added
- Execution loop enhancements and skills
- Plan progress tracking

## [0.6.0] - 2026-02-07

Initial public release

### Added
- Hierarchical task planning with Claude Code plugin
- Autonomous agentic execution loop
- Support for Claude Code and Codex runners
- Integration with ruff, ty, complexipy, and pytest
- Comprehensive documentation

[Unreleased]: https://github.com/roman-romanov-o/sahaidachny/compare/v0.7.4...HEAD
[0.7.4]: https://github.com/roman-romanov-o/sahaidachny/compare/v0.7.1...v0.7.4
[0.7.1]: https://github.com/roman-romanov-o/sahaidachny/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/roman-romanov-o/sahaidachny/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/roman-romanov-o/sahaidachny/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/roman-romanov-o/sahaidachny/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/roman-romanov-o/sahaidachny/releases/tag/v0.6.0
