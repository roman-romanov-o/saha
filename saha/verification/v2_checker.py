"""Pre-execution verification for saha/v2 tasks (progress.yaml + model/*.c4).

Python port of the gates in ``claude_plugin/commands/verify.md``:

- ``likec4 validate <task>/model`` is THE well-formedness gate (skipped
  gracefully when the CLI is absent — node tooling must never block);
  ``likec4 export json`` exits 0 on broken models and is never used.
- Completeness: at least one story with ACs, every AC has a verify
  method, manual ACs carry instructions, at least one phase with steps.
- Cross-references: every view id referenced from the YAML exists in the
  model, each story belongs to exactly one phase, depends_on resolve,
  every story has a ts-e2e-* spec or a declared test_specs gap.
- Unfilled ``{{…}}`` template placeholders are an error (v2 replaces the
  legacy delete-the-file cleanup, which must not touch v2 folders).

Reuses the legacy checker's CheckResult/VerificationResult shapes so the
command layer renders both formats identically.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from saha.models.likec4_source import read_model_sources, view_ids
from saha.models.progress import (
    V2_FORMAT_VALUE,
    ProgressFile,
    ProgressFileError,
    StoryProgress,
    load_progress,
    progress_yaml_path,
)
from saha.verification.checker import (
    TEMPLATE_PLACEHOLDER_PATTERN,
    CheckResult,
    VerificationResult,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

VALID_TASK_STATUSES = {"planning", "executing", "completed", "completed_pending_manual", "failed"}
VALID_VERIFY_METHODS = {"automated", "build", "manual"}
_LIKEC4_TIMEOUT_SECONDS = 120


class V2TaskVerifier:
    """Verifies a saha/v2 task folder is ready for execution."""

    def __init__(self, task_path: Path) -> None:
        self.task_path = task_path
        self.checks: list[CheckResult] = []

    def verify(self, task_id: str) -> VerificationResult:
        self.checks = []
        progress = self._check_progress_parses()
        if progress is not None:
            self._check_format_and_status(progress)
            sources = read_model_sources(self.task_path)
            self._check_model_exists(sources)
            self._check_likec4_validate()
            self._check_completeness(progress)
            self._check_view_references(progress, sources)
            self._check_cross_references(progress, sources)
            self._check_e2e_coverage(progress)
            self._check_template_placeholders(sources)
        return VerificationResult(
            task_id=task_id,
            task_path=self.task_path,
            status=self._determine_status(),
            checks=self.checks,
        )

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_progress_parses(self) -> ProgressFile | None:
        try:
            progress = load_progress(self.task_path)
        except ProgressFileError as exc:
            self._fail("progress.yaml parses", str(exc))
            return None
        self._ok("progress.yaml parses", "progress.yaml loaded")
        return progress

    def _check_format_and_status(self, progress: ProgressFile) -> None:
        if progress.format != V2_FORMAT_VALUE:
            self._fail("format marker", f"format is {progress.format!r}, expected 'saha/v2'")
        else:
            self._ok("format marker", "format: saha/v2")
        if progress.status in VALID_TASK_STATUSES:
            self._ok("task status", f"status: {progress.status}")
        else:
            self._fail("task status", f"invalid top-level status {progress.status!r}")

    def _check_model_exists(self, sources: dict[str, str]) -> None:
        if sources:
            self._ok("model files", f"{len(sources)} model/*.c4 file(s)")
        else:
            self._fail("model files", "model/ has no .c4 files")

    def _check_likec4_validate(self) -> None:
        """Compile gate; a missing CLI is a pass (node tooling never blocks)."""
        likec4 = shutil.which("likec4")
        if not likec4:
            self._ok("likec4 validate", "skipped (likec4 not installed)")
            return
        try:
            proc = subprocess.run(
                [likec4, "validate", str(self.task_path / "model")],
                capture_output=True,
                text=True,
                timeout=_LIKEC4_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._warn("likec4 validate", f"could not run likec4: {exc}")
            return
        if proc.returncode == 0:
            self._ok("likec4 validate", "model compiles")
        else:
            detail = (proc.stderr or proc.stdout).strip()[-500:]
            self._fail("likec4 validate", f"model does not compile: {detail}")

    def _check_completeness(self, progress: ProgressFile) -> None:
        stories_with_acs = [s for s in progress.stories if s.acceptance_criteria]
        if stories_with_acs:
            self._ok("stories", f"{len(stories_with_acs)} story(ies) with acceptance criteria")
        else:
            self._fail("stories", "no story with at least one acceptance criterion")
        self._check_ac_verify_methods(progress)
        phases_with_steps = [p for p in progress.phases if p.steps]
        if phases_with_steps:
            self._ok("phases", f"{len(phases_with_steps)} phase(s) with steps")
        else:
            self._fail("phases", "no phase with at least one step")

    def _check_ac_verify_methods(self, progress: ProgressFile) -> None:
        problems: list[str] = []
        for story in progress.stories:
            for ac in story.acceptance_criteria:
                qualified = f"{story.id}.{ac.id}"
                if ac.verify not in VALID_VERIFY_METHODS:
                    problems.append(f"{qualified} has verify: {ac.verify!r}")
                elif ac.verify == "manual" and not ac.manual_instructions:
                    problems.append(f"{qualified} is manual but has no manual_instructions")
        if problems:
            self._fail("AC verify methods", "; ".join(problems))
        else:
            self._ok("AC verify methods", "every AC has a valid verify method")

    def _check_view_references(self, progress: ProgressFile, sources: dict[str, str]) -> None:
        known = view_ids(sources)
        missing = sorted(
            f"{origin} → {view}"
            for view, origin in _referenced_views(progress)
            if view not in known
        )
        if missing:
            self._fail("view references", "views not found in model/*.c4: " + "; ".join(missing))
        else:
            self._ok("view references", "all referenced views exist in the model")

    def _check_cross_references(self, progress: ProgressFile, sources: dict[str, str]) -> None:
        story_ids = {s.id for s in progress.stories}
        errors = _phase_assignment_errors(progress, story_ids)
        errors += _dependency_errors(progress, story_ids)
        if errors:
            self._fail("cross references", "; ".join(errors))
        else:
            self._ok("cross references", "stories↔phases and depends_on all resolve")
        self._check_decision_references(progress, sources)

    def _check_decision_references(self, progress: ProgressFile, sources: dict[str, str]) -> None:
        combined = "\n".join(sources.values()).lower()
        missing = sorted(
            {
                f"{story.id} → {decision}"
                for story in progress.stories
                for decision in story.decisions
                if not re.search(rf"\b{re.escape(decision.lower())}\b", combined)
            }
        )
        if missing:
            self._warn("decision references", "decisions not in model: " + "; ".join(missing))
        else:
            self._ok("decision references", "all referenced decisions exist")

    def _check_e2e_coverage(self, progress: ProgressFile) -> None:
        gap_stories = {gap.story for gap in progress.test_spec_gaps()}
        uncovered = [
            story.id
            for story in progress.stories
            if not _has_e2e_spec(story) and story.id not in gap_stories
        ]
        if uncovered:
            self._fail(
                "e2e coverage",
                "stories without a ts-e2e-* spec or a declared test_specs gap: "
                + ", ".join(uncovered),
            )
        else:
            self._ok("e2e coverage", "every story has an e2e spec or a declared gap")

    def _check_template_placeholders(self, sources: dict[str, str]) -> None:
        offenders = [
            name
            for name, text in {
                "progress.yaml": _read_progress_text(self.task_path),
                **sources,
            }.items()
            if text and TEMPLATE_PLACEHOLDER_PATTERN.search(text)
        ]
        if offenders:
            self._fail(
                "template placeholders",
                "unfilled {{…}} placeholders in: " + ", ".join(offenders),
            )
        else:
            self._ok("template placeholders", "no unfilled template placeholders")

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def _ok(self, name: str, message: str) -> None:
        self.checks.append(CheckResult(name=name, passed=True, message=message))

    def _fail(self, name: str, message: str) -> None:
        self.checks.append(CheckResult(name=name, passed=False, message=message))

    def _warn(self, name: str, message: str) -> None:
        self.checks.append(CheckResult(name=name, passed=False, message=message, is_warning=True))

    def _determine_status(self) -> VerificationStatus:
        if any(not c.passed and not c.is_warning for c in self.checks):
            return VerificationStatus.FAILED
        if any(not c.passed and c.is_warning for c in self.checks):
            return VerificationStatus.WARNINGS
        return VerificationStatus.PASSED


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _referenced_views(progress: ProgressFile) -> list[tuple[str, str]]:
    """(view_id, origin) pairs for every view the YAML points at."""
    refs: list[tuple[str, str]] = []
    for story in progress.stories:
        if story.view:
            refs.append((story.view, story.id))
        refs.extend(
            (spec, f"{story.id}.{ac.id}") for ac in story.acceptance_criteria for spec in ac.specs
        )
    for phase in progress.phases:
        if phase.view:
            refs.append((phase.view, phase.id))
    for step_name, step in progress.planning.items():
        refs.extend((view, f"planning.{step_name}") for view in step.views)
    return refs


def _phase_assignment_errors(progress: ProgressFile, story_ids: set[str]) -> list[str]:
    errors: list[str] = []
    assignment_count = dict.fromkeys(story_ids, 0)
    for phase in progress.phases:
        for story_id in phase.stories:
            if story_id not in story_ids:
                errors.append(f"{phase.id} references unknown story {story_id}")
            else:
                assignment_count[story_id] += 1
    errors.extend(
        f"{story_id} is in {count} phases (must be exactly 1)"
        for story_id, count in assignment_count.items()
        if count != 1
    )
    return errors


def _dependency_errors(progress: ProgressFile, story_ids: set[str]) -> list[str]:
    return [
        f"{story.id} depends on unknown story {dep}"
        for story in progress.stories
        for dep in story.depends_on
        if dep not in story_ids
    ]


def _has_e2e_spec(story: StoryProgress) -> bool:
    return any(spec.startswith("ts-e2e-") for ac in story.acceptance_criteria for spec in ac.specs)


def _read_progress_text(task_path: Path) -> str:
    try:
        return progress_yaml_path(task_path).read_text(encoding="utf-8")
    except OSError:
        return ""
