"""Verifies the orchestrator wires bundled artifacts into the agent context,
and that the Claude runner places the static artifacts block before the prompt
instructions (cache-friendly ordering).
"""

import textwrap
from pathlib import Path
from unittest.mock import Mock

import pytest

from saha.config.stack import StackProfile
from saha.orchestrator.artifact_bundler import ArtifactView
from saha.orchestrator.loop import AgenticLoop, LoopConfig
from saha.runners.claude import ClaudeRunner


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


@pytest.fixture
def task_with_one_active_story(tmp_path: Path) -> Path:
    task = tmp_path / "task-wired"
    _write(task / "task-description.md", "# Wired task\n\nDescription body.")
    _write(
        task / "user-stories" / "US-001-active.md",
        """\
        # US-001: Active story

        **Status:** In Progress

        ## Acceptance Criteria

        1. **Given** a thing
           **When** I do action
           **Then** result happens
           - [ ] AC-1
        """,
    )
    return task


def _make_loop() -> AgenticLoop:
    loop = AgenticLoop(
        runner=Mock(),
        tool_registry=Mock(),
        hook_registry=Mock(),
        state_manager=Mock(),
        settings=Mock(),
    )
    # Mock settings has no real state_dir; seed the lazy stack cache directly.
    loop._stack = StackProfile()
    return loop


def test_base_context_bundles_artifacts(task_with_one_active_story: Path) -> None:
    loop = _make_loop()
    state = Mock(current_iteration=2)
    config = LoopConfig(task_id="t-1", task_path=task_with_one_active_story, max_iterations=5)

    ctx = loop._base_context(state, config, ArtifactView.IMPLEMENTER)

    assert ctx["task_id"] == "t-1"
    assert ctx["iteration"] == 2
    assert "artifacts" in ctx, "Bundled artifacts must be present in context"

    artifacts = ctx["artifacts"]
    assert artifacts["view"] == "implementer"
    assert artifacts["task_description"].startswith("# Wired task")
    # Active story is shipped with full body
    stories = artifacts["user_stories"]
    assert len(stories) == 1
    assert stories[0]["id"] == "US-001-active"
    assert stories[0]["status"] == "in_progress"


def test_base_context_uses_view_filtering(task_with_one_active_story: Path) -> None:
    """Code-quality view should ship a near-empty bundle."""
    loop = _make_loop()
    state = Mock(current_iteration=1)
    config = LoopConfig(task_id="t-2", task_path=task_with_one_active_story, max_iterations=5)

    ctx = loop._base_context(state, config, ArtifactView.CODE_QUALITY)
    artifacts = ctx["artifacts"]
    assert artifacts["user_stories"] == []
    assert artifacts["task_description"] == ""


def test_bundler_is_reused_across_calls(task_with_one_active_story: Path) -> None:
    """Same task path => same bundler instance => warm mtime cache."""
    loop = _make_loop()
    state = Mock(current_iteration=1)
    config = LoopConfig(task_id="t-3", task_path=task_with_one_active_story, max_iterations=5)

    loop._base_context(state, config, ArtifactView.IMPLEMENTER)
    bundler_first = loop._bundler
    loop._base_context(state, config, ArtifactView.QA)
    bundler_second = loop._bundler

    assert bundler_first is bundler_second


# ---------------------------------------------------------------------------
# Runner prompt structure
# ---------------------------------------------------------------------------


def test_claude_runner_places_artifacts_before_instructions() -> None:
    runner = ClaudeRunner()
    context = {
        "task_id": "t",
        "iteration": 1,
        "artifacts": {"view": "implementer", "user_stories": []},
        "fix_info": "fix the X",
    }
    body = runner._build_agent_prompt("Run the implementation phase.", context)

    static_idx = body.index("## Static artifacts")
    iteration_idx = body.index("## Iteration state")
    instructions_idx = body.index("## Instructions")

    assert static_idx < iteration_idx < instructions_idx, (
        "Layout must be: Static artifacts → Iteration state → Instructions"
    )
    # Iteration state should NOT contain the artifacts (those moved up)
    iteration_block = body[iteration_idx:instructions_idx]
    assert '"user_stories"' not in iteration_block
    assert '"fix_info"' in iteration_block


def test_claude_runner_legacy_layout_when_no_artifacts() -> None:
    runner = ClaudeRunner()
    body = runner._build_agent_prompt("Do the thing.", {"task_id": "t"})

    assert "## Static artifacts" not in body
    assert "## Context" in body
    assert "## Instructions" in body
    assert body.index("## Context") < body.index("## Instructions")
