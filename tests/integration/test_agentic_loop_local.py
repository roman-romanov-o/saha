"""Local integration tests for the agentic loop that don't require Docker.

These tests run the agentic loop locally using IntelligentMockRunner.
They exercise the full workflow without needing testcontainers.

Use these tests for:
- Local development testing
- Quick validation without Docker
- Debugging the agentic loop flow
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from saha.config.settings import Settings
from saha.hooks import HookRegistry
from saha.hooks.base import Hook
from saha.hooks.notification import LoggingHook
from saha.orchestrator.loop import AgenticLoop, LoopConfig
from saha.orchestrator.state import StateManager
from saha.runners import IntelligentMockRunner
from saha.runners.base import Runner, RunnerResult
from saha.tools import create_default_registry


# Sample task files that exercise the full Sahaidachny schema
def create_sample_task(base_dir: Path) -> None:
    """Create sample task files in the given directory."""
    task_dir = base_dir / "docs/tasks/test-task"
    task_dir.mkdir(parents=True, exist_ok=True)

    # Task description
    (task_dir / "task-description.md").write_text("""# Test Task: Add String Utilities

## Overview
Add string utility functions to the project's utility module.

## Goals
- Create a `reverse_string` function in `src/utils.py`
- Create a `capitalize_words` function in `src/utils.py`
- Add comprehensive tests

## Target File
The implementation should be in `src/utils.py`.

## Success Criteria
- All functions have proper type hints
- All functions have docstrings
- All tests pass
""")

    (task_dir / "README.md").write_text("# Test Task\n\nThis is a test task.\n")

    # User stories
    stories_dir = task_dir / "user-stories"
    stories_dir.mkdir(exist_ok=True)

    (stories_dir / "US-001.md").write_text("""# US-001: String Reversal Function

## Description
As a developer, I want a function to reverse strings.

## Status
In Progress

## Acceptance Criteria
- [ ] `reverse_string(s)` returns the reversed string
- [ ] Empty strings are handled correctly
- [ ] Type hints are present
""")

    (stories_dir / "US-002.md").write_text("""# US-002: Capitalize Words Function

## Description
As a developer, I want a function to capitalize words.

## Status
In Progress

## Acceptance Criteria
- [ ] `capitalize_words(s)` capitalizes each word
- [ ] Empty strings return empty strings
- [ ] Type hints are present
""")

    # Implementation plan
    plan_dir = task_dir / "implementation-plan"
    plan_dir.mkdir(exist_ok=True)

    (plan_dir / "phase-01.md").write_text("""# Phase 1: Core Implementation

## Status
Pending

## Objectives
1. Create `src/utils.py` with both utility functions
2. Add proper type hints and docstrings
""")


def create_sample_project(base_dir: Path) -> None:
    """Create a sample Python project structure."""
    src_dir = base_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    (src_dir / "__init__.py").write_text('"""Sample source package."""\n')

    (src_dir / "main.py").write_text('''"""Main module."""


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
''')

    tests_dir = base_dir / "tests"
    tests_dir.mkdir(exist_ok=True)

    (tests_dir / "__init__.py").write_text("")

    (tests_dir / "test_main.py").write_text('''"""Tests for main module."""

from src.main import add


def test_add():
    """Test addition."""
    assert add(2, 3) == 5
''')


class TrackingHook(Hook):
    """Hook that tracks all events for testing."""

    def __init__(self):
        self._triggered_events: list[str] = []

    @property
    def name(self) -> str:
        return "tracking"

    @property
    def triggered_events(self) -> list[str]:
        """Return the list of triggered events."""
        return self._triggered_events

    def execute(self, event: str, **kwargs) -> None:
        event_name = event.value if hasattr(event, "value") else str(event)
        self._triggered_events.append(event_name)


@pytest.fixture
def temp_project():
    """Create a temporary project directory with task and source files."""
    temp_dir = Path(tempfile.mkdtemp())
    create_sample_task(temp_dir)
    create_sample_project(temp_dir)

    yield temp_dir

    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def state_manager(temp_project):
    """Create a state manager for the temp project."""
    state_dir = temp_project / ".sahaidachny"
    return StateManager(state_dir)


class TestAgenticLoopLocal:
    """Local tests for the agentic loop."""

    def test_full_loop_execution_success(self, temp_project, state_manager):
        """Test a complete successful loop execution."""
        settings = Settings(runner="mock")
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=0,
            fail_quality_count=0,
            make_code_changes=True,
        )

        tools = create_default_registry()
        hooks = HookRegistry()
        hooks.register(LoggingHook())

        orchestrator = AgenticLoop(
            runner=runner,
            tool_registry=tools,
            hook_registry=hooks,
            state_manager=state_manager,
            settings=settings,
        )

        config = LoopConfig(
            task_id="test-task",
            task_path=Path("docs/tasks/test-task"),
            max_iterations=5,
            enabled_tools=["ruff"],
        )

        state = orchestrator.run(config)

        assert state.current_phase.value == "completed"
        assert state.current_iteration >= 1
        assert state.task_id == "test-task"

        # Verify all agents were called
        agents_called = {call["agent_name"] for call in runner.call_history}
        assert "execution-implementer" in agents_called
        assert "execution-qa" in agents_called
        assert "execution-dod" in agents_called

    def test_loop_qa_failure_and_recovery(self, temp_project, state_manager):
        """Test that QA failures trigger fix loops."""
        settings = Settings(runner="mock")
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=2,  # Fail QA twice before passing
            fail_quality_count=0,
            make_code_changes=False,
        )

        tools = create_default_registry()
        hooks = HookRegistry()

        orchestrator = AgenticLoop(
            runner=runner,
            tool_registry=tools,
            hook_registry=hooks,
            state_manager=state_manager,
            settings=settings,
        )

        config = LoopConfig(
            task_id="qa-fail-test",
            task_path=Path("docs/tasks/test-task"),
            max_iterations=5,
            enabled_tools=[],
        )

        state = orchestrator.run(config)

        # Should have at least 3 iterations (2 failures + 1 success)
        assert state.current_iteration >= 3

        # Count QA calls - should be at least 3
        qa_calls = sum(1 for call in runner.call_history if "qa" in call["agent_name"])
        assert qa_calls >= 3

        # Manager should run for each iteration, including failed QA iterations
        manager_calls = [c for c in runner.call_history if c["agent_name"] == "execution-manager"]
        assert len(manager_calls) == state.current_iteration

        # At least one manager invocation should indicate an early stop at QA
        assert any(
            (c.get("context", {}).get("iteration_stop", {}).get("stopped_at_phase") == "qa")
            for c in manager_calls
        )

    def test_loop_code_quality_failure_and_recovery(self, temp_project, state_manager):
        """Test that code quality failures trigger fix loops."""
        settings = Settings(runner="mock")
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=0,
            fail_quality_count=1,  # Fail quality once
            make_code_changes=False,
        )

        tools = create_default_registry()
        hooks = HookRegistry()

        orchestrator = AgenticLoop(
            runner=runner,
            tool_registry=tools,
            hook_registry=hooks,
            state_manager=state_manager,
            settings=settings,
        )

        config = LoopConfig(
            task_id="quality-fail-test",
            task_path=Path("docs/tasks/test-task"),
            max_iterations=5,
            enabled_tools=[],
        )

        state = orchestrator.run(config)

        # Should have at least 2 iterations
        assert state.current_iteration >= 2

        # Manager should run for each iteration, including failed quality iterations
        manager_calls = [c for c in runner.call_history if c["agent_name"] == "execution-manager"]
        assert len(manager_calls) == state.current_iteration

    def test_max_iterations_respected(self, temp_project, state_manager):
        """Test that the loop stops at max iterations."""
        settings = Settings(runner="mock")
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=100,  # Always fail QA
            fail_quality_count=0,
            make_code_changes=False,
        )

        tools = create_default_registry()
        hooks = HookRegistry()

        orchestrator = AgenticLoop(
            runner=runner,
            tool_registry=tools,
            hook_registry=hooks,
            state_manager=state_manager,
            settings=settings,
        )

        max_iter = 3
        config = LoopConfig(
            task_id="max-iter-test",
            task_path=Path("docs/tasks/test-task"),
            max_iterations=max_iter,
            enabled_tools=[],
        )

        state = orchestrator.run(config)

        assert state.current_iteration == max_iter
        assert state.current_phase.value != "completed"

    def test_state_persistence_and_loading(self, temp_project, state_manager):
        """Test that state is properly saved and can be loaded."""
        settings = Settings(runner="mock")
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=0,
            fail_quality_count=0,
            make_code_changes=False,
        )

        tools = create_default_registry()
        hooks = HookRegistry()

        orchestrator = AgenticLoop(
            runner=runner,
            tool_registry=tools,
            hook_registry=hooks,
            state_manager=state_manager,
            settings=settings,
        )

        config = LoopConfig(
            task_id="persist-test",
            task_path=Path("docs/tasks/test-task"),
            max_iterations=2,
            enabled_tools=["ruff", "pytest"],
        )

        orchestrator.run(config)

        # Load the state
        loaded_state = state_manager.load("persist-test")

        assert loaded_state is not None
        assert loaded_state.task_id == "persist-test"
        assert loaded_state.enabled_tools == ["ruff", "pytest"]

    def test_hooks_triggered_correctly(self, temp_project, state_manager):
        """Test that hooks are triggered at appropriate points."""
        settings = Settings(runner="mock")
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=0,
            fail_quality_count=0,
            make_code_changes=False,
        )

        tools = create_default_registry()
        hooks = HookRegistry()
        tracker = TrackingHook()
        hooks.register(tracker)

        orchestrator = AgenticLoop(
            runner=runner,
            tool_registry=tools,
            hook_registry=hooks,
            state_manager=state_manager,
            settings=settings,
        )

        config = LoopConfig(
            task_id="hook-test",
            task_path=Path("docs/tasks/test-task"),
            max_iterations=2,
            enabled_tools=[],
        )

        orchestrator.run(config)

        # Verify key events were triggered
        assert "loop_start" in tracker.triggered_events
        assert "iteration_start" in tracker.triggered_events
        assert "implementation_start" in tracker.triggered_events
        assert any("loop" in e for e in tracker.triggered_events)

    def test_context_passed_to_agents(self, temp_project, state_manager):
        """Test that agents receive correct context variables."""
        settings = Settings(runner="mock")
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=0,
            fail_quality_count=0,
            make_code_changes=False,
        )

        tools = create_default_registry()
        hooks = HookRegistry()

        orchestrator = AgenticLoop(
            runner=runner,
            tool_registry=tools,
            hook_registry=hooks,
            state_manager=state_manager,
            settings=settings,
        )

        config = LoopConfig(
            task_id="context-test",
            task_path=Path("docs/tasks/test-task"),
            max_iterations=2,
            enabled_tools=["ruff"],
        )

        orchestrator.run(config)

        # Check that all calls have required context
        for call in runner.call_history:
            context = call.get("context", {})
            assert "task_id" in context, f"{call['agent_name']}: missing task_id"
            assert "task_path" in context, f"{call['agent_name']}: missing task_path"

    def test_manager_receives_iteration_artifacts(self, temp_project, state_manager):
        """Test that manager gets concrete iteration evidence in prompt + context."""
        settings = Settings(runner="mock")
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=0,
            fail_quality_count=0,
            make_code_changes=True,
        )

        tools = create_default_registry()
        hooks = HookRegistry()

        orchestrator = AgenticLoop(
            runner=runner,
            tool_registry=tools,
            hook_registry=hooks,
            state_manager=state_manager,
            settings=settings,
        )

        config = LoopConfig(
            task_id="manager-context-test",
            task_path=Path("docs/tasks/test-task"),
            max_iterations=2,
            enabled_tools=[],
        )

        orchestrator.run(config)

        manager_calls = [c for c in runner.call_history if c["agent_name"] == "execution-manager"]
        assert manager_calls, "manager agent was not called"

        manager_call = manager_calls[0]
        manager_context = manager_call.get("context", {})
        artifacts = manager_context.get("iteration_artifacts", {})
        stop_info = manager_context.get("iteration_stop", {})

        assert "files_changed" in artifacts
        assert "files_added" in artifacts
        assert artifacts.get("qa_passed") is True
        assert artifacts.get("quality_passed") is True
        assert "test_critique_score" in artifacts
        assert "stopped_at_phase" in stop_info

        manager_prompt = manager_call.get("prompt", "")
        assert "{task_path}" not in manager_prompt
        # Prompt now points the manager at the pre-bundled artifacts rather than
        # literal task-folder paths.
        assert "artifacts.user_stories" in manager_prompt
        assert "artifacts.implementation_plan" in manager_prompt

    def test_fix_info_passed_on_failure(self, temp_project, state_manager):
        """Test that fix_info is passed to implementation agent on retry."""
        settings = Settings(runner="mock")
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=1,  # Fail once to trigger retry
            fail_quality_count=0,
            make_code_changes=False,
        )

        tools = create_default_registry()
        hooks = HookRegistry()

        orchestrator = AgenticLoop(
            runner=runner,
            tool_registry=tools,
            hook_registry=hooks,
            state_manager=state_manager,
            settings=settings,
        )

        config = LoopConfig(
            task_id="fix-info-test",
            task_path=Path("docs/tasks/test-task"),
            max_iterations=5,
            enabled_tools=[],
        )

        orchestrator.run(config)

        # Find implementation calls after the first one
        impl_calls = [c for c in runner.call_history if "implementer" in c["agent_name"]]

        # At least one call should have fix_info (the retry)
        assert len(impl_calls) >= 2
        # The second implementation call should have fix_info in context
        second_impl_context = impl_calls[1].get("context", {})
        # Note: fix_info might be None if not set, but the key should exist
        assert "fix_info" in second_impl_context or "iteration" in second_impl_context


class TestIntelligentMockRunner:
    """Tests for the IntelligentMockRunner itself."""

    def test_runner_available(self):
        """Test that runner reports as available."""
        runner = IntelligentMockRunner()
        assert runner.is_available()
        assert runner.get_name() == "intelligent-mock"

    def test_implementation_agent_response(self, temp_project):
        """Test implementation agent produces correct output format."""
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            make_code_changes=False,
        )

        result = runner.run_agent(
            agent_spec_path=Path("execution_implementer.md"),
            prompt="Implement the feature",
            context={"task_id": "test", "task_path": "docs/tasks/test-task"},
        )

        assert result.success
        assert result.structured_output is not None
        assert "status" in result.structured_output

    def test_qa_agent_response(self, temp_project):
        """Test QA agent produces correct output format."""
        runner = IntelligentMockRunner(working_dir=temp_project)

        result = runner.run_agent(
            agent_spec_path=Path("execution_qa.md"),
            prompt="Verify the implementation",
            context={"task_id": "test", "task_path": "docs/tasks/test-task"},
        )

        assert result.success
        assert result.structured_output is not None
        assert "dod_achieved" in result.structured_output

    def test_dod_agent_response(self, temp_project):
        """Test DoD agent produces correct output format."""
        runner = IntelligentMockRunner(working_dir=temp_project)

        result = runner.run_agent(
            agent_spec_path=Path("execution_dod.md"),
            prompt="Check if task is complete",
            context={
                "task_id": "test",
                "task_path": "docs/tasks/test-task",
                "iterations_completed": 1,
            },
        )

        assert result.success
        assert result.structured_output is not None
        assert "task_complete" in result.structured_output

    def test_call_history_recorded(self):
        """Test that call history is recorded correctly."""
        runner = IntelligentMockRunner()

        runner.run_agent(Path("test_agent.md"), "prompt 1", {"key": "value1"})
        runner.run_agent(Path("test_agent.md"), "prompt 2", {"key": "value2"})

        assert len(runner.call_history) == 2
        assert runner.call_history[0]["prompt"] == "prompt 1"
        assert runner.call_history[1]["prompt"] == "prompt 2"

    def test_qa_failure_simulation(self, temp_project):
        """Test that QA failures can be simulated."""
        runner = IntelligentMockRunner(
            working_dir=temp_project,
            fail_qa_count=2,
        )

        # First two calls should fail
        result1 = runner.run_agent(
            Path("execution_qa.md"), "test", {"task_path": "docs/tasks/test-task"}
        )
        result2 = runner.run_agent(
            Path("execution_qa.md"), "test", {"task_path": "docs/tasks/test-task"}
        )

        # Third call should pass
        result3 = runner.run_agent(
            Path("execution_qa.md"), "test", {"task_path": "docs/tasks/test-task"}
        )

        assert result1.structured_output["dod_achieved"] is False
        assert result2.structured_output["dod_achieved"] is False
        assert result3.structured_output["dod_achieved"] is True


def test_token_usage_logged_for_each_stage(monkeypatch, tmp_path: Path) -> None:
    """Ensure token usage logging fires for each agent stage."""
    create_sample_project(tmp_path)
    create_sample_task(tmp_path)

    calls: list[tuple[str, dict[str, int] | None, int | None]] = []

    def fake_log_token_usage(
        phase: str, token_usage: dict[str, int] | None, tokens_used: int | None = None
    ) -> None:
        calls.append((phase, token_usage, tokens_used))

    monkeypatch.setattr("saha.orchestrator.loop.log_token_usage", fake_log_token_usage)

    class TokenMockRunner(Runner):
        def run_agent(
            self, agent_spec_path: Path, prompt: str, context=None, timeout: int = 300
        ) -> RunnerResult:
            agent = agent_spec_path.stem
            usage = {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}

            if agent == "execution-implementer":
                return RunnerResult.success_result(
                    "ok",
                    structured_output={"files_changed": [], "files_added": []},
                    token_usage=usage,
                )
            if agent == "execution-test-critique":
                return RunnerResult.success_result(
                    "ok",
                    structured_output={"critique_passed": True, "test_quality_score": "A"},
                    token_usage=usage,
                )
            if agent == "execution-qa":
                return RunnerResult.success_result(
                    "ok",
                    structured_output={"dod_achieved": True},
                    token_usage=usage,
                )
            if agent == "execution-code-quality":
                return RunnerResult.success_result(
                    "ok",
                    structured_output={"quality_passed": True},
                    token_usage=usage,
                )
            if agent == "execution-manager":
                return RunnerResult.success_result(
                    "ok",
                    structured_output={"status": "success"},
                    token_usage=usage,
                )
            if agent == "execution-dod":
                return RunnerResult.success_result(
                    "ok",
                    structured_output={"task_complete": True},
                    token_usage=usage,
                )
            return RunnerResult.success_result("ok", token_usage=usage)

        def run_prompt(
            self, prompt: str, system_prompt: str | None = None, timeout: int = 300
        ) -> RunnerResult:
            return RunnerResult.success_result(
                "ok", token_usage={"input_tokens": 1, "output_tokens": 1}
            )

        def is_available(self) -> bool:
            return True

        def get_name(self) -> str:
            return "token-mock"

    settings = Settings(runner="mock", agents_path=Path("claude_plugin/agents"))
    runner = TokenMockRunner()
    tools = create_default_registry()
    hooks = HookRegistry()
    state_manager = StateManager(tmp_path / ".sahaidachny")

    orchestrator = AgenticLoop(
        runner=runner,
        tool_registry=tools,
        hook_registry=hooks,
        state_manager=state_manager,
        settings=settings,
    )

    config = LoopConfig(
        task_id="test-task",
        task_path=tmp_path / "docs/tasks/test-task",
        max_iterations=1,
        enabled_tools=[],
    )

    state = orchestrator.run(config)
    assert state.current_phase.value == "completed"

    phases = [call[0] for call in calls]
    assert phases == [
        "Implementation",
        "Test Critique",
        "QA Verification",
        "Code Quality",
        "Manager",
        "DoD Check",
    ]
