"""Integration tests for the saha/v2 execution path.

Covers the four dispatch points (bundler, plan updater, verifier, cleanup
guard) plus the v2-only invariants: surgical status-line writes, frozen
model fingerprinting, and the full agentic loop over a v2 fixture.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from saha.config.settings import Settings
from saha.hooks import HookRegistry
from saha.hooks.base import Hook
from saha.models.progress import is_v2_task, load_progress
from saha.orchestrator.artifact_bundler import ArtifactView, LifecycleStatus
from saha.orchestrator.loop import AgenticLoop, LoopConfig
from saha.orchestrator.state import StateManager
from saha.orchestrator.v2_artifact_bundler import V2ArtifactBundler
from saha.orchestrator.v2_progress import V2ProgressUpdater, set_task_status
from saha.runners import IntelligentMockRunner
from saha.tools import create_default_registry
from saha.verification import (
    V2TaskVerifier,
    VerificationStatus,
    cleanup_template_artifacts,
    compute_model_fingerprint,
)

PROGRESS_YAML = """\
format: saha/v2
task: task-42-v2-test
title: v2 execution test task
status: planning  # set by the orchestrator

planning:
  research: { status: done, artifacts: [research/notes.md] }
  test_specs: { status: done, views: [ts-e2e-01], gaps: [] }

stories:
  - id: US-001
    title: Reverse strings
    view: us-001-flow
    priority: must
    status: ready
    story: "As a developer, I want reverse_string so that I can flip text."
    acceptance_criteria:
      - id: AC-1
        text: "reverse_string returns the reversed input"
        verify: automated
        status: pending
        specs: [ts-e2e-01]
        tests: []
      - id: AC-2
        text: "output renders correctly in the widget"
        verify: manual
        manual_instructions: "Open the app and eyeball the widget"
        status: pending
        specs: []
        tests: []
    edge_cases: []
    depends_on: []
    decisions: [DD-001]

phases:
  - id: phase-01
    title: Core implementation
    view: phases
    stories: [US-001]
    status: pending
    steps:
      - { name: implement reverse_string, files: [src/utils.py], status: pending }

iterations: []
"""

STORIES_C4 = """\
views {
  dynamic view us-001-flow {
    title 'US-001 flow'
  }
}
"""

TEST_SPECS_C4 = """\
views {
  dynamic view ts-e2e-01 {
    title 'happy path e2e'
  }
}
"""

PHASES_C4 = """\
views {
  view phases {
    title 'plan overview'
  }
}
"""

TASK_C4 = """\
views {
  view task-context {
    title 'task context'
  }
}
"""

DECISIONS_C4 = "// dd-001 keep it simple\n"
CONTRACTS_C4 = "// contract: reverse_string(s: str) -> str\n"


def create_v2_task(base_dir: Path) -> Path:
    """Build a minimal valid saha/v2 task fixture; returns the ABSOLUTE task path."""
    task_dir = base_dir / "docs/tasks/task-42-v2-test"
    model_dir = task_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "progress.yaml").write_text(PROGRESS_YAML, encoding="utf-8")
    (model_dir / "stories.c4").write_text(STORIES_C4, encoding="utf-8")
    (model_dir / "test-specs.c4").write_text(TEST_SPECS_C4, encoding="utf-8")
    (model_dir / "phases.c4").write_text(PHASES_C4, encoding="utf-8")
    (model_dir / "task.c4").write_text(TASK_C4, encoding="utf-8")
    (model_dir / "decisions.c4").write_text(DECISIONS_C4, encoding="utf-8")
    (model_dir / "contracts.c4").write_text(CONTRACTS_C4, encoding="utf-8")

    research_dir = task_dir / "research"
    research_dir.mkdir(exist_ok=True)
    (research_dir / "notes.md").write_text(
        "Research prose that legitimately shows {{template}} syntax.\n", encoding="utf-8"
    )
    return task_dir


@pytest.fixture
def temp_base():
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def v2_task(temp_base):
    return create_v2_task(temp_base)


@pytest.fixture
def no_likec4(monkeypatch):
    """Keep verifier tests hermetic even when likec4 is installed locally."""
    monkeypatch.setattr("saha.verification.v2_checker.shutil.which", lambda _: None)


# ---------------------------------------------------------------------------
# Detection + cleanup guard
# ---------------------------------------------------------------------------


def test_is_v2_task_detection(v2_task, temp_base):
    assert is_v2_task(v2_task) is True
    legacy = temp_base / "docs/tasks/legacy-task"
    legacy.mkdir(parents=True)
    (legacy / "task-description.md").write_text("# Legacy\n")
    assert is_v2_task(legacy) is False
    assert is_v2_task(temp_base / "does-not-exist") is False


def test_cleanup_is_a_noop_on_v2_tasks(v2_task):
    notes = v2_task / "research" / "notes.md"
    assert "{{template}}" in notes.read_text()

    result = cleanup_template_artifacts(v2_task)

    assert result.total_removed == 0
    assert notes.exists(), "cleanup must never delete v2 research prose"


# ---------------------------------------------------------------------------
# V2TaskVerifier
# ---------------------------------------------------------------------------


def test_verifier_passes_on_valid_fixture(v2_task, no_likec4):
    result = V2TaskVerifier(v2_task).verify("task-42-v2-test")
    failed = [c for c in result.checks if not c.passed and not c.is_warning]
    assert result.status == VerificationStatus.PASSED, failed


def _mutate_progress(task_path: Path, old: str, new: str) -> None:
    path = task_path / "progress.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture drift: {old!r} not found"
    path.write_text(text.replace(old, new), encoding="utf-8")


@pytest.mark.parametrize(
    ("old", "new", "failing_check"),
    [
        ("view: us-001-flow", "view: us-999-missing", "view references"),
        ("verify: automated", "verify: vibes", "AC verify methods"),
        (
            "manual_instructions: \"Open the app and eyeball the widget\"\n        ",
            "",
            "AC verify methods",
        ),
        ("specs: [ts-e2e-01]", "specs: []", "e2e coverage"),
        ("title: v2 execution test task", 'title: "{{TASK_TITLE}}"', "template placeholders"),
        ("stories: [US-001]", "stories: []", "cross references"),
        ("status: planning  # set by the orchestrator", "status: bogus", "task status"),
    ],
)
def test_verifier_fails_on_broken_fixture(v2_task, no_likec4, old, new, failing_check):
    _mutate_progress(v2_task, old, new)
    result = V2TaskVerifier(v2_task).verify("task-42-v2-test")
    assert result.status == VerificationStatus.FAILED
    failed_names = {c.name for c in result.checks if not c.passed and not c.is_warning}
    assert failing_check in failed_names, failed_names


def test_verifier_fails_when_story_is_in_two_phases(v2_task, no_likec4):
    _mutate_progress(
        v2_task,
        "iterations: []",
        "  - id: phase-02\n"
        "    title: Duplicate assignment\n"
        "    stories: [US-001]\n"
        "    status: pending\n"
        "    steps:\n"
        "      - { name: extra, files: [], status: pending }\n"
        "\niterations: []",
    )
    result = V2TaskVerifier(v2_task).verify("task-42-v2-test")
    assert result.status == VerificationStatus.FAILED
    messages = " ".join(c.message for c in result.checks if not c.passed)
    assert "2 phases" in messages


def test_verifier_warns_on_unknown_decision_reference(v2_task, no_likec4):
    _mutate_progress(v2_task, "decisions: [DD-001]", "decisions: [DD-777]")
    result = V2TaskVerifier(v2_task).verify("task-42-v2-test")
    assert result.status == VerificationStatus.WARNINGS
    warning_names = {c.name for c in result.checks if c.is_warning}
    assert "decision references" in warning_names


# ---------------------------------------------------------------------------
# V2ArtifactBundler views
# ---------------------------------------------------------------------------


def test_bundler_implementer_view_shows_active_work(v2_task):
    artifacts = V2ArtifactBundler(v2_task).load(ArtifactView.IMPLEMENTER)

    assert "task-context" in artifacts.task_description or "likec4" in artifacts.task_description
    story_ids = [s.id for s in artifacts.user_stories]
    assert "US-001" in story_ids
    story = next(s for s in artifacts.user_stories if s.id == "US-001")
    assert story.body is not None
    assert "reverse_string returns the reversed input" in story.body
    assert "us-001-flow" in story.body

    # The `pending` phase must be VISIBLE (pending→READY, not the DRAFT trap).
    plan_names = [p.name for p in artifacts.implementation_plan]
    assert any("phase-01" in n for n in plan_names)
    phase = artifacts.implementation_plan[0]
    assert phase.status != LifecycleStatus.DRAFT
    assert phase.body is not None

    assert artifacts.api_contracts, "contracts.c4 should surface as api_contracts"
    spec_names = [t.name for t in artifacts.test_specs]
    assert "ts-e2e-01" in spec_names
    assert artifacts.code_changes == []


def test_bundler_dod_view_is_full(v2_task):
    artifacts = V2ArtifactBundler(v2_task).load(ArtifactView.DOD)
    assert artifacts.user_stories and artifacts.user_stories[0].body is not None
    assert artifacts.implementation_plan and artifacts.implementation_plan[0].body is not None


def test_bundler_code_quality_view_is_minimal(v2_task):
    artifacts = V2ArtifactBundler(v2_task).load(ArtifactView.CODE_QUALITY)
    assert artifacts.user_stories == []
    assert artifacts.implementation_plan == []


def test_bundler_survives_malformed_progress(v2_task):
    (v2_task / "progress.yaml").write_text("format: saha/v2\nstories: [::broken", encoding="utf-8")
    artifacts = V2ArtifactBundler(v2_task).load(ArtifactView.IMPLEMENTER)
    assert artifacts.user_stories == []


# ---------------------------------------------------------------------------
# V2ProgressUpdater + set_task_status write discipline
# ---------------------------------------------------------------------------


def test_updater_selects_first_unfinished_phase(v2_task):
    updater = V2ProgressUpdater(v2_task)

    class FakeState:
        context: dict = {}

    state = FakeState()
    selection = updater.select_active_phase(state)
    assert selection is not None
    assert state.context["current_plan_phase"] == "phase-01"
    assert selection.phase_path == v2_task / "progress.yaml"


def test_updater_write_methods_are_noops(v2_task):
    progress_path = v2_task / "progress.yaml"
    before = progress_path.read_bytes()
    updater = V2ProgressUpdater(v2_task)

    assert updater.mark_all_complete(note="task complete") == 0
    assert progress_path.read_bytes() == before, "Python must never round-trip progress.yaml"


def test_set_task_status_patches_only_the_top_level_line(v2_task):
    progress_path = v2_task / "progress.yaml"
    before = progress_path.read_text(encoding="utf-8")

    assert set_task_status(v2_task, "executing") is True

    after = progress_path.read_text(encoding="utf-8")
    assert "status: executing  # set by the orchestrator" in after
    # Everything except the one patched line is byte-identical (comments,
    # AC-level `status:` lines, indentation all preserved).
    diff = [
        (a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b
    ]
    assert diff == [
        ("status: planning  # set by the orchestrator", "status: executing  # set by the orchestrator")
    ]
    assert load_progress(v2_task).status == "executing"

    assert set_task_status(v2_task, "executing") is False, "idempotent second call"
    with pytest.raises(ValueError):
        set_task_status(v2_task, "not-a-status")


def test_set_task_status_ignores_legacy_tasks(temp_base):
    legacy = temp_base / "docs/tasks/legacy-task"
    legacy.mkdir(parents=True)
    assert set_task_status(legacy, "executing") is False


# ---------------------------------------------------------------------------
# Model fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_is_stable_and_change_sensitive(v2_task, temp_base):
    first = compute_model_fingerprint(v2_task)
    second = compute_model_fingerprint(v2_task)
    assert first is not None
    assert first == second

    (v2_task / "model" / "stories.c4").write_text(STORIES_C4 + "// edited\n", encoding="utf-8")
    assert compute_model_fingerprint(v2_task) != first

    empty = temp_base / "docs/tasks/no-model"
    empty.mkdir(parents=True)
    assert compute_model_fingerprint(empty) is None


# ---------------------------------------------------------------------------
# Full loop over a v2 task
# ---------------------------------------------------------------------------


def _make_orchestrator(base_dir: Path, runner) -> AgenticLoop:
    # state_dir must live under base_dir: the loop resolves the stack profile
    # from state_dir.parent, and a repo-root default would pick up saha's own
    # pyproject.toml and recursively run `pytest -v` on this very suite.
    return AgenticLoop(
        runner=runner,
        tool_registry=create_default_registry(),
        hook_registry=HookRegistry(),
        state_manager=StateManager(base_dir / ".sahaidachny"),
        settings=Settings(runner="mock", state_dir=base_dir / ".sahaidachny"),
    )


def test_full_loop_completes_on_v2_task(temp_base, v2_task):
    runner = IntelligentMockRunner(working_dir=temp_base, make_code_changes=False)
    orchestrator = _make_orchestrator(temp_base, runner)

    config = LoopConfig(
        task_id="task-42-v2-test",
        task_path=v2_task,  # absolute — the v2 bundler reads the real fixture
        max_iterations=3,
        enabled_tools=[],
    )
    state = orchestrator.run(config)

    assert state.current_phase.value == "completed"
    assert state.context.get("model_fingerprint") == compute_model_fingerprint(v2_task)
    assert state.context.get("current_plan_phase") == "phase-01"

    prompts = {c["agent_name"]: c["prompt"] for c in runner.call_history}
    assert "progress.yaml" in prompts["execution-manager"]
    assert "check off" not in prompts["execution-manager"]
    assert "ac_bindings" in prompts["execution-qa"]
    assert "verify: manual" in prompts["execution-dod"] or "manual" in prompts["execution-dod"]
    assert "artifacts.api_contracts" in prompts["execution-implementer"]

    # progress.yaml itself is untouched by the loop (manager is mocked, the
    # orchestrator only ever patches the status line via the command layer).
    assert load_progress(v2_task).format == "saha/v2"


class ModelTamperingHook(Hook):
    """Edits the frozen spec mid-run to trip the DoD integrity gate."""

    def __init__(self, model_file: Path):
        self._model_file = model_file

    @property
    def name(self) -> str:
        return "model-tamperer"

    def execute(self, event: str, **kwargs) -> None:
        event_name = event.value if hasattr(event, "value") else str(event)
        if event_name == "qa_start":
            self._model_file.write_text(STORIES_C4 + "// tampered\n", encoding="utf-8")


def test_dod_refuses_completion_when_model_changes_mid_run(temp_base, v2_task):
    runner = IntelligentMockRunner(working_dir=temp_base, make_code_changes=False)
    hooks = HookRegistry()
    hooks.register(ModelTamperingHook(v2_task / "model" / "stories.c4"))

    orchestrator = AgenticLoop(
        runner=runner,
        tool_registry=create_default_registry(),
        hook_registry=hooks,
        state_manager=StateManager(temp_base / ".sahaidachny"),
        settings=Settings(runner="mock", state_dir=temp_base / ".sahaidachny"),
    )
    config = LoopConfig(
        task_id="task-42-v2-tamper",
        task_path=v2_task,
        max_iterations=1,
        enabled_tools=[],
    )
    state = orchestrator.run(config)

    assert state.current_phase.value != "completed", (
        "editing frozen model/*.c4 mid-run must block completion"
    )
