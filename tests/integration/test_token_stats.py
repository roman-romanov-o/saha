"""Integration tests for per-phase token usage tracking and the saha stats command.

Covers:
- PhaseTokenUsage persistence through StateManager save/load.
- Loop helper _record_phase_usage attaching records to the current iteration.
- Stats command output across summary, per-iteration, per-phase, and --all views.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from saha.cli import app
from saha.commands.stats import _iteration_records, _sum_usage
from saha.models.state import (
    ExecutionState,
    IterationRecord,
    LoopPhase,
    PhaseTokenUsage,
)
from saha.orchestrator.loop import AgenticLoop
from saha.orchestrator.state import StateManager
from saha.runners.base import RunnerResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_state_dir(monkeypatch, tmp_path: Path) -> Path:
    """Point SAHA at an isolated state dir so tests don't pollute the repo."""
    state_dir = tmp_path / ".sahaidachny"
    state_dir.mkdir()
    monkeypatch.setenv("SAHA_STATE_DIR", str(state_dir))
    monkeypatch.chdir(tmp_path)
    return state_dir


def _build_state_with_usage(
    task_id: str = "task-stats",
    phases: list[tuple[LoopPhase, int]] | None = None,
) -> ExecutionState:
    """Create an ExecutionState with two iterations and phase token usage."""
    base = datetime(2026, 5, 7, 10, 0, 0)
    state = ExecutionState(
        task_id=task_id,
        task_path=Path("docs/tasks") / task_id,
        max_iterations=5,
        started_at=base,
        current_iteration=2,
        current_phase=LoopPhase.COMPLETED,
    )
    phase_seq = phases or [
        (LoopPhase.IMPLEMENTATION, 1000),
        (LoopPhase.QA, 500),
        (LoopPhase.MANAGER, 200),
    ]

    for iteration_idx in range(1, 3):
        iteration = IterationRecord(iteration=iteration_idx, started_at=base)
        for offset, (phase, total) in enumerate(phase_seq):
            started = base + timedelta(seconds=offset * 30)
            iteration.token_usage.append(
                PhaseTokenUsage(
                    phase=phase,
                    agent_name=f"execution-{phase.value}",
                    runner_name="claude-cli (sonnet)",
                    started_at=started,
                    completed_at=started + timedelta(seconds=12),
                    duration_seconds=12.0,
                    input_tokens=total * 7 // 10,
                    output_tokens=total * 3 // 10,
                    cache_read_input_tokens=100,
                    cache_write_input_tokens=20,
                    reasoning_tokens=0,
                    total_tokens=total,
                )
            )
        state.iterations.append(iteration)
    return state


# ---------------------------------------------------------------------------
# Model + persistence tests
# ---------------------------------------------------------------------------


class TestPhaseTokenUsagePersistence:
    """Token usage records must roundtrip through state YAML cleanly."""

    def test_iteration_record_accepts_token_usage(self) -> None:
        record = IterationRecord(iteration=1, started_at=datetime.now())
        record.token_usage.append(
            PhaseTokenUsage(
                phase=LoopPhase.IMPLEMENTATION,
                agent_name="execution-implementer",
                started_at=datetime.now(),
                completed_at=datetime.now(),
                total_tokens=42,
            )
        )

        assert len(record.token_usage) == 1
        assert record.token_usage[0].total_tokens == 42

    def test_state_manager_roundtrips_token_usage(self, isolated_state_dir: Path) -> None:
        state = _build_state_with_usage()
        manager = StateManager(isolated_state_dir)
        manager.save(state)

        loaded = manager.load(state.task_id)
        assert loaded is not None
        assert len(loaded.iterations) == 2
        first_iter = loaded.iterations[0]
        assert len(first_iter.token_usage) == 3
        assert first_iter.token_usage[0].phase == LoopPhase.IMPLEMENTATION
        assert first_iter.token_usage[0].total_tokens == 1000
        assert first_iter.token_usage[0].input_tokens == 700
        assert first_iter.token_usage[0].output_tokens == 300


# ---------------------------------------------------------------------------
# Loop recording helper
# ---------------------------------------------------------------------------


class TestRecordPhaseUsage:
    """The loop helper attaches records to the current iteration."""

    def test_record_phase_usage_appends_record(self) -> None:
        state = ExecutionState(
            task_id="task-rec",
            task_path=Path("docs/tasks/task-rec"),
            started_at=datetime.now(),
        )
        state.start_iteration()

        class StubRunner:
            def get_name(self) -> str:
                return "stub-runner"

        loop = AgenticLoop.__new__(AgenticLoop)
        result = RunnerResult.success_result(
            output="ok",
            token_usage={
                "input_tokens": 100,
                "output_tokens": 25,
                "cache_read_input_tokens": 8,
                "total_tokens": 125,
            },
        )

        started = datetime.now() - timedelta(seconds=2)
        loop._record_phase_usage(  # type: ignore[attr-defined]
            state,
            LoopPhase.IMPLEMENTATION,
            "execution-implementer",
            StubRunner(),  # type: ignore[arg-type]
            result,
            started,
        )

        iteration = state.current_iteration_record
        assert iteration is not None
        assert len(iteration.token_usage) == 1
        usage = iteration.token_usage[0]
        assert usage.phase == LoopPhase.IMPLEMENTATION
        assert usage.runner_name == "stub-runner"
        assert usage.input_tokens == 100
        assert usage.output_tokens == 25
        assert usage.total_tokens == 125
        assert usage.duration_seconds >= 2

    def test_record_phase_usage_handles_missing_iteration(self) -> None:
        """Should silently skip when there's no current iteration record."""
        state = ExecutionState(
            task_id="task-empty",
            task_path=Path("docs/tasks/task-empty"),
        )

        class StubRunner:
            def get_name(self) -> str:
                return "stub"

        loop = AgenticLoop.__new__(AgenticLoop)
        result = RunnerResult.success_result(output="x")

        loop._record_phase_usage(  # type: ignore[attr-defined]
            state,
            LoopPhase.QA,
            "execution-qa",
            StubRunner(),  # type: ignore[arg-type]
            result,
            datetime.now(),
        )

        # No iteration was started, no records attached, no exception.
        assert state.iterations == []


# ---------------------------------------------------------------------------
# Helper aggregation
# ---------------------------------------------------------------------------


class TestAggregationHelpers:
    def test_iteration_records_flattens_all(self) -> None:
        state = _build_state_with_usage()
        records = _iteration_records(state)
        assert len(records) == 6  # 2 iterations × 3 phases

    def test_sum_usage_totals_match(self) -> None:
        state = _build_state_with_usage()
        totals = _sum_usage(_iteration_records(state))
        # Expected: each iteration: 1000 + 500 + 200 = 1700; ×2 iterations = 3400
        assert totals["total_tokens"] == 3400
        # input is 70% of total per record => 700+350+140 = 1190 per iter × 2 = 2380
        assert totals["input_tokens"] == 2380


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestStatsCommand:
    """Visualize token usage via the saha stats CLI."""

    def _seed(self, state_dir: Path, *task_ids: str) -> None:
        manager = StateManager(state_dir)
        for tid in task_ids:
            manager.save(_build_state_with_usage(task_id=tid))

    def test_stats_no_state_reports_empty(self, isolated_state_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "No tasks" in result.stdout

    def test_stats_summary_lists_tasks(self, isolated_state_dir: Path) -> None:
        self._seed(isolated_state_dir, "task-alpha", "task-beta")

        runner = CliRunner()
        result = runner.invoke(app, ["stats"])

        assert result.exit_code == 0, result.stdout
        assert "task-alpha" in result.stdout
        assert "task-beta" in result.stdout
        assert "TOTAL" in result.stdout  # multi-task footer
        # Total tokens across phases per task = 1700 × 2 iterations = 3,400
        assert "3,400" in result.stdout

    def test_stats_for_task_shows_phase_breakdown(self, isolated_state_dir: Path) -> None:
        self._seed(isolated_state_dir, "task-alpha")

        runner = CliRunner()
        result = runner.invoke(app, ["stats", "task-alpha"])

        assert result.exit_code == 0, result.stdout
        assert "task-alpha" in result.stdout
        assert "implementation" in result.stdout
        assert "qa" in result.stdout
        assert "manager" in result.stdout
        assert "TASK TOTAL" in result.stdout

    def test_stats_by_phase_aggregates(self, isolated_state_dir: Path) -> None:
        self._seed(isolated_state_dir, "task-alpha")

        runner = CliRunner()
        result = runner.invoke(app, ["stats", "task-alpha", "--by-phase"])

        assert result.exit_code == 0, result.stdout
        # Per phase totals across 2 iterations:
        # implementation = 2000, qa = 1000, manager = 400
        assert "2,000" in result.stdout
        assert "1,000" in result.stdout
        assert "TOTAL" in result.stdout

    def test_stats_all_aggregates_across_tasks(self, isolated_state_dir: Path) -> None:
        self._seed(isolated_state_dir, "task-alpha", "task-beta")

        runner = CliRunner()
        result = runner.invoke(app, ["stats", "--all"])

        assert result.exit_code == 0, result.stdout
        # implementation phase across 2 tasks × 2 iterations × 1000 = 4000
        assert "4,000" in result.stdout
        assert "TOTAL" in result.stdout

    def test_stats_unknown_task_errors(self, isolated_state_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["stats", "nonexistent-task"])

        assert result.exit_code != 0
        assert "No saved state" in result.stdout or "No saved state" in (result.stderr or "")


class TestIterationOutcomeSurfacing:
    """Stats output should make iterations-to-done obvious."""

    def _seed_with_phases(
        self,
        state_dir: Path,
        task_id: str,
        final_phase: LoopPhase,
        iterations: int = 2,
        dod_pattern: tuple[bool, ...] | None = None,
    ) -> None:
        state = _build_state_with_usage(task_id=task_id)
        state.current_phase = final_phase
        state.current_iteration = iterations
        # Trim extra iterations if needed
        state.iterations = state.iterations[:iterations]
        if dod_pattern is None:
            dod_pattern = (False,) * (iterations - 1) + (True,) if iterations else ()
        for it, achieved in zip(state.iterations, dod_pattern, strict=False):
            it.dod_achieved = achieved
            it.quality_passed = achieved
            it.test_critique_passed = True
        StateManager(state_dir).save(state)

    def test_summary_marks_completed_tasks(self, isolated_state_dir: Path) -> None:
        self._seed_with_phases(isolated_state_dir, "task-done", LoopPhase.COMPLETED, iterations=2)
        self._seed_with_phases(isolated_state_dir, "task-fail", LoopPhase.FAILED, iterations=4)

        runner = CliRunner()
        result = runner.invoke(app, ["stats"])

        assert result.exit_code == 0, result.stdout
        # Outcome column shows iterations-to-done
        assert "done @ 2" in result.stdout
        assert "failed @ 4" in result.stdout
        # Footer summary surfaces averages
        assert "1 done" in result.stdout
        assert "1 failed" in result.stdout
        assert "iterations burned" in result.stdout

    def test_iteration_breakdown_shows_completion_line(self, isolated_state_dir: Path) -> None:
        self._seed_with_phases(isolated_state_dir, "task-done", LoopPhase.COMPLETED, iterations=2)

        runner = CliRunner()
        result = runner.invoke(app, ["stats", "task-done"])

        assert result.exit_code == 0, result.stdout
        assert "Task completed after 2 iteration" in result.stdout
        # Per-iteration DoD flag rendered for the final iteration
        assert "DoD" in result.stdout

    def test_iteration_breakdown_shows_failure_line(self, isolated_state_dir: Path) -> None:
        self._seed_with_phases(
            isolated_state_dir,
            "task-fail",
            LoopPhase.FAILED,
            iterations=3,
            dod_pattern=(False, False, False),
        )

        runner = CliRunner()
        result = runner.invoke(app, ["stats", "task-fail"])

        assert result.exit_code == 0, result.stdout
        assert "Task failed after 3 iteration" in result.stdout
