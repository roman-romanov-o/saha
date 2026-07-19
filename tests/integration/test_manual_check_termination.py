"""Integration tests for manual-check accumulation and terminal routing.

When a task is code-complete but carries unresolved ``verify:manual`` criteria,
the loop must NOT keep iterating: it lands in ``COMPLETED_PENDING_MANUAL`` (a
success state) and surfaces a human checklist. These tests drive the loop's
accumulation/finalisation helpers against a real ``StateManager`` so the
persisted YAML state machine is exercised, not just in-memory objects.
"""

from pathlib import Path

import pytest

from saha.config.settings import Settings
from saha.hooks import HookRegistry
from saha.models.result import ManualCheck
from saha.models.state import LoopPhase
from saha.orchestrator.loop import AgenticLoop, LoopError
from saha.orchestrator.state import StateManager
from saha.runners import IntelligentMockRunner
from saha.tools import create_default_registry


@pytest.fixture
def state_manager(tmp_path):
    return StateManager(tmp_path / ".sahaidachny")


@pytest.fixture
def loop(tmp_path, state_manager):
    """A loop wired with a mock runner; we exercise its helper methods only."""
    return AgenticLoop(
        runner=IntelligentMockRunner(working_dir=tmp_path),
        tool_registry=create_default_registry(),
        hook_registry=HookRegistry(),
        state_manager=state_manager,
        settings=Settings(runner="mock", state_dir=tmp_path / ".sahaidachny"),
    )


@pytest.fixture
def state(state_manager):
    return state_manager.create(task_id="t1", task_path=Path("docs/tasks/t1"))


class TestExtractManualChecks:
    """Parsing QA structured output into ManualCheck models."""

    def test_extracts_well_formed_checks(self):
        out = {
            "manual_checks": [
                {"criterion": "Window renders", "instructions": "Launch and look"},
                {"criterion": "Audio plays"},
            ]
        }

        checks = AgenticLoop._extract_manual_checks(out)

        assert [c.criterion for c in checks] == ["Window renders", "Audio plays"]
        assert checks[0].instructions == "Launch and look"
        assert checks[1].instructions == ""

    @pytest.mark.parametrize("out", [None, {}, {"manual_checks": []}])
    def test_no_checks_yields_empty(self, out):
        assert AgenticLoop._extract_manual_checks(out) == []

    def test_skips_entries_without_criterion(self):
        out = {"manual_checks": [{"instructions": "orphan"}, {"criterion": "ok"}]}

        checks = AgenticLoop._extract_manual_checks(out)

        assert [c.criterion for c in checks] == ["ok"]


class TestAccumulation:
    """Manual checks accumulate across iterations, deduped by criterion."""

    def test_accumulate_persists_to_state(self, loop, state, state_manager):
        loop._accumulate_manual_checks(state, [ManualCheck(criterion="A", instructions="do A")])

        pending = state.context["pending_manual_checks"]
        assert pending == [{"criterion": "A", "instructions": "do A"}]
        # Persisted: a fresh load sees the same accumulation.
        reloaded = state_manager.load("t1")
        assert reloaded.context["pending_manual_checks"] == pending

    def test_dedup_by_criterion_across_calls(self, loop, state):
        loop._accumulate_manual_checks(state, [ManualCheck(criterion="A")])
        loop._accumulate_manual_checks(
            state, [ManualCheck(criterion="A"), ManualCheck(criterion="B")]
        )

        criteria = [c["criterion"] for c in state.context["pending_manual_checks"]]
        assert criteria == ["A", "B"]

    def test_empty_list_adds_nothing(self, loop, state):
        # The helper in isolation does not seed the key for an empty batch...
        loop._accumulate_manual_checks(state, [])
        assert "pending_manual_checks" not in state.context

        # ...and once seeded (as _init_stack_context does in production), an
        # empty batch leaves the accumulator untouched.
        state.context["pending_manual_checks"] = []
        loop._accumulate_manual_checks(state, [])
        assert state.context["pending_manual_checks"] == []


class TestFinalizationRouting:
    """Completion routes to COMPLETED vs COMPLETED_PENDING_MANUAL correctly."""

    def test_no_pending_marks_completed(self, loop, state, state_manager):
        loop._finalize_completion(state)

        assert state.current_phase == LoopPhase.COMPLETED
        assert state_manager.load("t1").current_phase == LoopPhase.COMPLETED

    def test_pending_routes_to_completed_pending_manual(self, loop, state, state_manager):
        loop._accumulate_manual_checks(state, [ManualCheck(criterion="Window renders")])

        loop._finalize_completion(state)

        assert state.current_phase == LoopPhase.COMPLETED_PENDING_MANUAL
        # Persisted terminal state survives reload.
        reloaded = state_manager.load("t1")
        assert reloaded.current_phase == LoopPhase.COMPLETED_PENDING_MANUAL
        assert reloaded.completed_at is not None

    def test_pending_manual_is_a_terminal_non_running_state(self, state):
        state.current_phase = LoopPhase.COMPLETED_PENDING_MANUAL

        assert state.is_running is False

    def test_resume_on_pending_manual_is_rejected_and_preserves_checks(
        self, loop, state, state_manager
    ):
        # Regression: resume() routes through run(), which deletes state. A
        # pending-manual task must be rejected so accumulated checks survive.
        loop._accumulate_manual_checks(state, [ManualCheck(criterion="X", instructions="look")])
        loop._finalize_completion(state)
        assert state.current_phase == LoopPhase.COMPLETED_PENDING_MANUAL

        with pytest.raises(LoopError, match="completed_pending_manual"):
            loop.resume("t1")

        reloaded = state_manager.load("t1")
        assert reloaded.context["pending_manual_checks"] == [
            {"criterion": "X", "instructions": "look"}
        ]
