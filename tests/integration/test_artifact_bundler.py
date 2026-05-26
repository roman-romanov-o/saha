"""Integration tests for ArtifactBundler against a synthetic task folder.

These tests build a real task folder on disk (via tmp_path), then exercise
the bundler's status filtering, view-specific output, mtime-based
invalidation, and size-budget guardrails.
"""

import os
import textwrap
import time
from pathlib import Path

import pytest

from saha.orchestrator.artifact_bundler import (
    ArtifactBundler,
    ArtifactView,
    LifecycleStatus,
    extract_status,
    normalize_status,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def _story(status: str, idx: int, body_size: int = 200) -> str:
    filler = "Lorem ipsum dolor sit amet. " * max(1, body_size // 28)
    return f"""\
# US-{idx:03d}: Sample story {idx}

**Priority:** Must Have
**Status:** {status}

## User Story

As a user I want X so that Y.

## Acceptance Criteria

1. **Given** condition A
   **When** action A
   **Then** result A
   - [ ] AC-1

2. **Given** condition B
   **When** action B
   **Then** result B
   - [ ] AC-2

## Body
{filler}
"""


def _phase(status: str, idx: int) -> str:
    return f"""\
# Phase {idx:02d}: Sample phase

**Status:** {status}

Phase body content.
"""


def _spec(status: str, name: str) -> str:
    return f"""\
# Spec {name}

**Status:** {status}

Test spec body.
"""


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    """Build a synthetic task folder with a mix of statuses across artifact types."""
    task = tmp_path / "task-sample"

    _write(task / "task-description.md", "# Task Sample\n\nDescription body.")

    # 5 user stories: 2 Done, 1 In Progress, 1 Ready, 1 Draft
    _write(task / "user-stories" / "US-001-done.md", _story("Done", 1))
    _write(task / "user-stories" / "US-002-done.md", _story("Done", 2))
    _write(task / "user-stories" / "US-003-in-progress.md", _story("In Progress", 3))
    _write(task / "user-stories" / "US-004-ready.md", _story("Ready", 4))
    _write(task / "user-stories" / "US-005-draft.md", _story("Draft", 5))

    # 4 plan phases: 1 In Progress, 2 Not Started, 1 Complete
    _write(task / "implementation-plan" / "phase-01-foo.md", _phase("Complete", 1))
    _write(task / "implementation-plan" / "phase-02-bar.md", _phase("In Progress", 2))
    _write(task / "implementation-plan" / "phase-03-baz.md", _phase("Not Started", 3))
    _write(task / "implementation-plan" / "phase-04-qux.md", _phase("Not Started", 4))

    # 2 test specs (e2e + integration), one Ready one Done
    _write(task / "test-specs" / "e2e" / "flow-1.md", _spec("Ready", "flow-1"))
    _write(task / "test-specs" / "integration" / "api-1.md", _spec("Done", "api-1"))

    # 1 design decision and 1 api contract (no status filtering on these)
    _write(task / "design-decisions" / "auth-storage.md", "# Auth storage decision\n")
    _write(task / "api-contracts" / "runner-iface.md", "# Runner interface\n")

    return task


# ---------------------------------------------------------------------------
# Status normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Draft", LifecycleStatus.DRAFT),
        ("Not Started", LifecycleStatus.DRAFT),
        ("Ready", LifecycleStatus.READY),
        ("In Progress", LifecycleStatus.IN_PROGRESS),
        ("QA Verification", LifecycleStatus.IN_PROGRESS),
        ("Done", LifecycleStatus.DONE),
        ("Done (iteration 2)", LifecycleStatus.DONE),
        ("Complete", LifecycleStatus.DONE),
        ("Implemented", LifecycleStatus.DONE),
        ("", LifecycleStatus.UNKNOWN),
        ("Mystery State", LifecycleStatus.UNKNOWN),
    ],
)
def test_normalize_status(raw: str, expected: LifecycleStatus) -> None:
    assert normalize_status(raw) == expected


def test_extract_status_finds_first_match() -> None:
    body = "# Title\n\n**Status:** In Progress\n\nMore content"
    assert extract_status(body) == LifecycleStatus.IN_PROGRESS


def test_extract_status_missing_returns_unknown() -> None:
    assert extract_status("# Title\n\nNo status here.") == LifecycleStatus.UNKNOWN


# ---------------------------------------------------------------------------
# View-specific filtering
# ---------------------------------------------------------------------------


def test_implementer_view_includes_active_stories_only(task_dir: Path) -> None:
    bundle = ArtifactBundler(task_dir).load(ArtifactView.IMPLEMENTER)
    full_ids = {s.id for s in bundle.user_stories if s.body is not None}
    # In Progress + Ready are active. Done and Draft are skipped (no full bodies).
    assert "US-003-in-progress" in full_ids
    assert "US-004-ready" in full_ids
    assert "US-001-done" not in full_ids
    assert "US-005-draft" not in full_ids


def test_implementer_view_active_phase_only(task_dir: Path) -> None:
    bundle = ArtifactBundler(task_dir).load(ArtifactView.IMPLEMENTER)
    full_phases = [p for p in bundle.implementation_plan if p.body is not None]
    assert len(full_phases) == 1
    assert full_phases[0].name == "phase-02-bar"


def test_dod_view_ships_all_stories_full(task_dir: Path) -> None:
    bundle = ArtifactBundler(task_dir).load(ArtifactView.DOD)
    assert len(bundle.user_stories) == 5
    assert all(s.body is not None for s in bundle.user_stories)
    assert len(bundle.implementation_plan) == 4
    assert all(p.body is not None for p in bundle.implementation_plan)


def test_manager_view_stubs_done_and_draft(task_dir: Path) -> None:
    bundle = ArtifactBundler(task_dir).load(ArtifactView.MANAGER)
    by_id = {s.id: s for s in bundle.user_stories}
    assert by_id["US-001-done"].body is None  # stubbed
    assert by_id["US-005-draft"].body is None  # stubbed
    assert by_id["US-003-in-progress"].body is not None  # full
    assert by_id["US-004-ready"].body is not None  # full
    # Title and status survive on stubs so manager can update them
    assert by_id["US-001-done"].status == LifecycleStatus.DONE
    assert by_id["US-001-done"].title


def test_code_quality_view_is_minimal(task_dir: Path) -> None:
    bundle = ArtifactBundler(task_dir).load(ArtifactView.CODE_QUALITY)
    assert bundle.user_stories == []
    assert bundle.test_specs == []
    assert bundle.implementation_plan == []
    assert bundle.task_description == ""
    assert bundle.design_decisions == {}
    assert bundle.api_contracts == {}


def test_test_critique_view_excludes_design_and_api(task_dir: Path) -> None:
    bundle = ArtifactBundler(task_dir).load(ArtifactView.TEST_CRITIQUE)
    assert bundle.design_decisions == {}
    assert bundle.api_contracts == {}
    # Test specs are filtered by status
    spec_full = [s for s in bundle.test_specs if s.body is not None]
    assert {s.name for s in spec_full} == {"flow-1"}  # the Ready one


# ---------------------------------------------------------------------------
# Empty-list safety net
# ---------------------------------------------------------------------------


def test_active_filter_empty_falls_back_to_stubs(tmp_path: Path) -> None:
    """When all stories are Done/Draft, the implementer view should still ship stubs."""
    task = tmp_path / "task-stalled"
    _write(task / "user-stories" / "US-001.md", _story("Done", 1))
    _write(task / "user-stories" / "US-002.md", _story("Draft", 2))

    bundle = ArtifactBundler(task).load(ArtifactView.IMPLEMENTER)
    assert len(bundle.user_stories) == 2
    assert all(s.body is None for s in bundle.user_stories)


# ---------------------------------------------------------------------------
# Mtime invalidation
# ---------------------------------------------------------------------------


def test_mtime_invalidation_refreshes_cache(task_dir: Path) -> None:
    bundler = ArtifactBundler(task_dir)
    first = bundler.load(ArtifactView.DOD)
    assert any(s.id == "US-005-draft" for s in first.user_stories)

    # Update the draft story to Ready
    story_path = task_dir / "user-stories" / "US-005-draft.md"
    body = story_path.read_text()
    story_path.write_text(body.replace("**Status:** Draft", "**Status:** Ready"))
    # Force a different mtime in case the resolution is coarse
    time.sleep(0.01)
    os.utime(story_path, None)

    second = bundler.load(ArtifactView.IMPLEMENTER)
    full_ids = {s.id for s in second.user_stories if s.body is not None}
    assert "US-005-draft" in full_ids


# ---------------------------------------------------------------------------
# Size budget
# ---------------------------------------------------------------------------


def test_size_budget_truncates_largest_first(tmp_path: Path) -> None:
    """A tight budget forces stubbing of the largest items first."""
    task = tmp_path / "task-bigly"
    # Three stories with very different body sizes
    _write(task / "user-stories" / "US-001.md", _story("In Progress", 1, body_size=500))
    _write(task / "user-stories" / "US-002.md", _story("In Progress", 2, body_size=2000))
    _write(task / "user-stories" / "US-003.md", _story("In Progress", 3, body_size=10000))

    bundler = ArtifactBundler(task, max_size_bytes=4_000)
    bundle = bundler.load(ArtifactView.DOD)

    assert bundle.truncated is True
    assert bundle.truncation_notes
    by_id = {s.id: s for s in bundle.user_stories}
    # US-003 has the biggest body and must be stubbed first.
    assert by_id["US-001"].body  # smallest, kept
    assert by_id["US-003"].body is None  # stubbed


def test_size_budget_terminates_under_pathological_input(tmp_path: Path) -> None:
    """Even with budget impossible to satisfy via stubbing, the bundler must terminate."""
    task = tmp_path / "task-impossible"
    _write(task / "user-stories" / "US-001.md", _story("In Progress", 1, body_size=200))
    bundler = ArtifactBundler(task, max_size_bytes=10)  # smaller than even the JSON wrapper
    bundle = bundler.load(ArtifactView.DOD)
    assert bundle.truncated is True
