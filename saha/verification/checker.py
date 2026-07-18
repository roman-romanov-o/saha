"""Task verification checker for pre-execution readiness validation.

Performs fast, Python-based checks on task artifacts to ensure
the task is ready for execution. No AI calls needed.

Checks include:
- Existence checks: Do required artifacts exist?
- Content quality checks: Do artifacts follow expected patterns?
"""

import logging
import re
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from saha.models.progress import is_v2_task

logger = logging.getLogger(__name__)


# Patterns for content validation
CODE_BLOCK_PATTERN = re.compile(r"```[\w]*\n[\s\S]*?```", re.MULTILINE)
INLINE_CODE_PATTERN = re.compile(r"`[^`]+`")
PYTHON_TEST_PATTERN = re.compile(r"(def test_|@pytest\.|assert\s+|unittest\.|self\.assert)")
USER_STORY_PATTERN = re.compile(r"as\s+a[n]?\s+.+,?\s*i\s+want\s+.+,?\s*so\s+that", re.IGNORECASE)
ACCEPTANCE_CRITERIA_SECTION = re.compile(
    r"##?\s*(acceptance\s+criteria|given.*when.*then)", re.IGNORECASE
)


class VerificationStatus(str, Enum):
    """Overall verification result status."""

    PASSED = "passed"
    WARNINGS = "warnings"
    FAILED = "failed"


class CheckResult(BaseModel):
    """Result of a single verification check."""

    name: str
    passed: bool
    message: str
    is_warning: bool = False  # True = warning (can proceed), False = error (blocks execution)


class VerificationResult(BaseModel):
    """Complete verification result for a task."""

    task_id: str
    task_path: Path
    status: VerificationStatus
    checks: list[CheckResult]

    @property
    def errors(self) -> list[CheckResult]:
        """Get all failed checks that are errors (not warnings)."""
        return [c for c in self.checks if not c.passed and not c.is_warning]

    @property
    def warnings(self) -> list[CheckResult]:
        """Get all failed checks that are warnings."""
        return [c for c in self.checks if not c.passed and c.is_warning]

    @property
    def passed_checks(self) -> list[CheckResult]:
        """Get all passed checks."""
        return [c for c in self.checks if c.passed]

    def can_proceed(self) -> bool:
        """Check if execution can proceed based on errors and warnings."""
        return not self.errors and not self.warnings


class TaskVerifier:
    """Verifies task artifacts are complete and ready for execution."""

    def __init__(self, task_path: Path) -> None:
        self.task_path = task_path
        self.checks: list[CheckResult] = []

    def verify(self, task_id: str) -> VerificationResult:
        """Run all verification checks and return the result."""
        self.checks = []

        # Existence checks
        self._check_task_description()
        self._check_user_stories()
        self._check_test_specs()
        self._check_code_changes()
        self._check_implementation_plan()
        self._check_design_decisions()
        self._check_story_phase_assignment()

        # Content quality checks
        self._check_task_description_content()
        self._check_user_stories_content()
        self._check_test_specs_content()
        self._check_code_changes_content()
        self._check_implementation_plan_content()

        status = self._determine_status()

        return VerificationResult(
            task_id=task_id,
            task_path=self.task_path,
            status=status,
            checks=self.checks,
        )

    def _determine_status(self) -> VerificationStatus:
        """Determine overall status from individual check results."""
        has_errors = any(not c.passed and not c.is_warning for c in self.checks)
        has_warnings = any(not c.passed and c.is_warning for c in self.checks)

        if has_errors:
            return VerificationStatus.FAILED
        if has_warnings:
            return VerificationStatus.WARNINGS
        return VerificationStatus.PASSED

    def _check_task_description(self) -> None:
        """Check that task-description.md exists and has content."""
        desc_path = self.task_path / "task-description.md"

        if not desc_path.exists():
            self.checks.append(
                CheckResult(
                    name="task-description",
                    passed=False,
                    message="task-description.md not found",
                    is_warning=False,
                )
            )
            return

        content = desc_path.read_text()
        if len(content.strip()) < 50:
            self.checks.append(
                CheckResult(
                    name="task-description",
                    passed=False,
                    message="task-description.md seems too short (< 50 chars)",
                    is_warning=True,
                )
            )
            return

        self.checks.append(
            CheckResult(
                name="task-description",
                passed=True,
                message="task-description.md present",
            )
        )

    def _check_user_stories(self) -> None:
        """Check that at least one user story exists."""
        stories_dir = self.task_path / "user-stories"

        if not stories_dir.exists():
            self.checks.append(
                CheckResult(
                    name="user-stories",
                    passed=False,
                    message="user-stories/ directory not found",
                    is_warning=False,
                )
            )
            return

        stories = list(stories_dir.glob("*.md"))
        if not stories:
            self.checks.append(
                CheckResult(
                    name="user-stories",
                    passed=False,
                    message="No user story files found in user-stories/",
                    is_warning=False,
                )
            )
            return

        self.checks.append(
            CheckResult(
                name="user-stories",
                passed=True,
                message=f"{len(stories)} user story file(s) found",
            )
        )

    def _check_test_specs(self) -> None:
        """Check that at least one test spec exists."""
        specs_dir = self.task_path / "test-specs"

        if not specs_dir.exists():
            self.checks.append(
                CheckResult(
                    name="test-specs",
                    passed=False,
                    message="test-specs/ directory not found",
                    is_warning=False,
                )
            )
            return

        # Check for .md files in test-specs/ and subdirectories
        specs = list(specs_dir.rglob("*.md"))
        if not specs:
            self.checks.append(
                CheckResult(
                    name="test-specs",
                    passed=False,
                    message="No test spec files found in test-specs/",
                    is_warning=False,
                )
            )
            return

        self.checks.append(
            CheckResult(
                name="test-specs",
                passed=True,
                message=f"{len(specs)} test spec file(s) found",
            )
        )

    def _check_code_changes(self) -> None:
        """Check for code changes (required for TDD interface definition).

        code changes define the interfaces (Pydantic models, protocols) that
        the implementation agent creates in the TDD workflow.
        """
        contracts_dir = self.task_path / "code-changes"

        if not contracts_dir.exists():
            self.checks.append(
                CheckResult(
                    name="code-changes",
                    passed=False,
                    message="code-changes/ directory not found (needed for TDD interface definition)",
                    is_warning=True,  # Warning because some tasks may not need full TDD
                )
            )
            return

        # Check for .md files in code-changes/ excluding README
        contracts = [f for f in contracts_dir.glob("*.md") if f.name.lower() != "readme.md"]
        if not contracts:
            self.checks.append(
                CheckResult(
                    name="code-changes",
                    passed=False,
                    message="No code change files found (needed for TDD interface definition)",
                    is_warning=True,
                )
            )
            return

        self.checks.append(
            CheckResult(
                name="code-changes",
                passed=True,
                message=f"{len(contracts)} code change(s) found",
            )
        )

    def _check_implementation_plan(self) -> None:
        """Check that at least one implementation phase exists."""
        plan_dir = self.task_path / "implementation-plan"

        if not plan_dir.exists():
            self.checks.append(
                CheckResult(
                    name="implementation-plan",
                    passed=False,
                    message="implementation-plan/ directory not found",
                    is_warning=False,
                )
            )
            return

        phases = list(plan_dir.glob("phase-*.md"))
        if not phases:
            self.checks.append(
                CheckResult(
                    name="implementation-plan",
                    passed=False,
                    message="No phase-*.md files found in implementation-plan/",
                    is_warning=False,
                )
            )
            return

        self.checks.append(
            CheckResult(
                name="implementation-plan",
                passed=True,
                message=f"{len(phases)} implementation phase(s) found",
            )
        )

    def _check_design_decisions(self) -> None:
        """Check for design decisions (warning only if missing)."""
        decisions_dir = self.task_path / "design-decisions"

        if not decisions_dir.exists():
            self.checks.append(
                CheckResult(
                    name="design-decisions",
                    passed=False,
                    message="design-decisions/ directory not found (optional)",
                    is_warning=True,
                )
            )
            return

        decisions = list(decisions_dir.glob("*.md"))
        if not decisions:
            self.checks.append(
                CheckResult(
                    name="design-decisions",
                    passed=False,
                    message="No design decision files found (optional)",
                    is_warning=True,
                )
            )
            return

        self.checks.append(
            CheckResult(
                name="design-decisions",
                passed=True,
                message=f"{len(decisions)} design decision(s) found",
            )
        )

    def _check_story_phase_assignment(self) -> None:
        """Check that user stories are assigned to phases."""
        stories_dir = self.task_path / "user-stories"
        plan_dir = self.task_path / "implementation-plan"

        if not stories_dir.exists() or not plan_dir.exists():
            return  # Skip if prerequisites missing

        stories = list(stories_dir.glob("*.md"))
        phases = list(plan_dir.glob("phase-*.md"))

        if not stories or not phases:
            return

        # Extract story IDs from filenames (e.g., US-001-login.md -> US-001)
        story_ids = set()
        for story in stories:
            match = re.match(r"(US-\d+)", story.stem)
            if match:
                story_ids.add(match.group(1))

        # Check if stories are referenced in phases
        referenced_stories = set()
        for phase in phases:
            content = phase.read_text()
            for sid in story_ids:
                if sid in content:
                    referenced_stories.add(sid)

        unassigned = story_ids - referenced_stories
        if unassigned:
            self.checks.append(
                CheckResult(
                    name="story-assignment",
                    passed=False,
                    message=f"Stories not assigned to phases: {', '.join(sorted(unassigned))}",
                    is_warning=True,
                )
            )
        else:
            self.checks.append(
                CheckResult(
                    name="story-assignment",
                    passed=True,
                    message="All stories assigned to phases",
                )
            )

    # ========== Content Quality Checks ==========

    def _check_task_description_content(self) -> None:
        """Check task description content quality."""
        desc_path = self.task_path / "task-description.md"
        if not desc_path.exists():
            return  # Existence check already failed

        content = desc_path.read_text()

        # Check for code blocks (implementation details don't belong here)
        code_blocks = CODE_BLOCK_PATTERN.findall(content)
        if code_blocks:
            self.checks.append(
                CheckResult(
                    name="task-description-no-code",
                    passed=False,
                    message=f"Task description has {len(code_blocks)} code block(s) - remove implementation details",
                    is_warning=True,
                )
            )

        # Check for required sections
        required_sections = ["problem statement", "success criteria", "scope"]
        content_lower = content.lower()
        missing_sections = [s for s in required_sections if s not in content_lower]
        if missing_sections:
            self.checks.append(
                CheckResult(
                    name="task-description-sections",
                    passed=False,
                    message=f"Missing sections: {', '.join(missing_sections)}",
                    is_warning=True,
                )
            )

        # Check success criteria are measurable (have checkboxes or numbered list)
        if "success criteria" in content_lower:
            criteria_section = self._extract_section(content, "success criteria")
            if criteria_section:
                has_checkboxes = "[ ]" in criteria_section or "[x]" in criteria_section.lower()
                has_numbered = bool(re.search(r"^\s*\d+\.", criteria_section, re.MULTILINE))
                if not has_checkboxes and not has_numbered:
                    self.checks.append(
                        CheckResult(
                            name="task-description-measurable",
                            passed=False,
                            message="Success criteria should have checkboxes or numbered list for tracking",
                            is_warning=True,
                        )
                    )

    def _check_user_stories_content(self) -> None:
        """Check user stories content quality."""
        stories_dir = self.task_path / "user-stories"
        if not stories_dir.exists():
            return

        stories = [f for f in stories_dir.glob("*.md") if f.name != "README.md"]
        if not stories:
            return

        issues: list[str] = []

        for story_file in stories:
            content = story_file.read_text()
            story_name = story_file.stem

            # Check for user story pattern (As a... I want... So that...)
            if not USER_STORY_PATTERN.search(content):
                issues.append(f"{story_name}: missing 'As a/I want/So that' pattern")

            # Check for acceptance criteria section
            if not ACCEPTANCE_CRITERIA_SECTION.search(content):
                issues.append(f"{story_name}: missing Acceptance Criteria section")

            # Check story is not too long (> 150 lines suggests too much detail)
            lines = content.split("\n")
            if len(lines) > 150:
                issues.append(f"{story_name}: too long ({len(lines)} lines) - should be concise")

            # Check for code blocks (stories should describe what, not how)
            code_blocks = CODE_BLOCK_PATTERN.findall(content)
            if code_blocks:
                issues.append(
                    f"{story_name}: has code blocks - stories shouldn't have implementation"
                )

        if issues:
            # Report first 3 issues to avoid noise
            display_issues = issues[:3]
            more = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
            self.checks.append(
                CheckResult(
                    name="user-stories-content",
                    passed=False,
                    message=f"Story issues: {'; '.join(display_issues)}{more}",
                    is_warning=True,
                )
            )
        else:
            self.checks.append(
                CheckResult(
                    name="user-stories-content",
                    passed=True,
                    message="User stories follow expected format",
                )
            )

    def _check_test_specs_content(self) -> None:
        """Check test specs content quality.

        Test specs for TDD should have:
        - Test case descriptions with inputs and expected outputs
        - Example code snippets as implementation hints (this is OK!)
        - Parameterized test tables

        They should NOT be complete test implementations (many test functions).
        """
        specs_dir = self.task_path / "test-specs"
        if not specs_dir.exists():
            return

        specs = [f for f in specs_dir.rglob("*.md") if f.name != "README.md"]
        if not specs:
            return

        issues: list[str] = []

        for spec_file in specs:
            content = spec_file.read_text()
            spec_name = spec_file.stem

            # Count test function definitions - a few examples are fine,
            # but a complete test module (>10 test functions) is too much
            test_func_count = len(re.findall(r"def test_\w+", content))
            if test_func_count > 10:
                issues.append(
                    f"{spec_name}: has {test_func_count} test functions - "
                    "specs should describe tests, not be complete implementations"
                )

            # Check for test case descriptions (TC-UNIT-XXX, TC-INT-XXX, etc.)
            has_test_cases = bool(re.search(r"TC-\w+-\d+", content))

            # Check for expected/input descriptions
            has_expectations = any(
                term in content.lower()
                for term in ["expected", "input", "output", "should", "returns"]
            )

            if not has_test_cases and not has_expectations:
                issues.append(
                    f"{spec_name}: missing test case descriptions (TC-XXX-NNN) or expectations"
                )

        if issues:
            display_issues = issues[:3]
            more = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
            self.checks.append(
                CheckResult(
                    name="test-specs-content",
                    passed=False,
                    message=f"Test spec issues: {'; '.join(display_issues)}{more}",
                    is_warning=True,
                )
            )
        else:
            self.checks.append(
                CheckResult(
                    name="test-specs-content",
                    passed=True,
                    message="Test specs have proper test case descriptions",
                )
            )

    def _check_code_changes_content(self) -> None:
        """Check code changes content quality for TDD interface definition."""
        contracts_dir = self.task_path / "code-changes"
        if not contracts_dir.exists():
            return

        contracts = [f for f in contracts_dir.glob("*.md") if f.name.lower() != "readme.md"]
        if not contracts:
            return

        issues: list[str] = []

        for contract_file in contracts:
            content = contract_file.read_text()
            contract_name = contract_file.stem
            content_lower = content.lower()

            # Check for data model definitions (needed for Pydantic interfaces)
            has_data_models = any(
                term in content_lower
                for term in ["data model", "schema", "field", "type:", "request", "response"]
            )
            if not has_data_models:
                issues.append(f"{contract_name}: missing data model/schema definitions")

            # Check for JSON/code blocks with schema examples
            code_blocks = CODE_BLOCK_PATTERN.findall(content)
            has_json_schema = any("json" in block.lower() or "{" in block for block in code_blocks)
            if not has_json_schema:
                issues.append(f"{contract_name}: no JSON schema examples found")

            # Check for field type annotations
            has_field_types = bool(
                re.search(
                    r"(string|number|boolean|array|object|datetime|uuid|int|float)", content_lower
                )
            )
            if not has_field_types:
                issues.append(f"{contract_name}: missing field type annotations")

        if issues:
            display_issues = issues[:3]
            more = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
            self.checks.append(
                CheckResult(
                    name="code-changes-content",
                    passed=False,
                    message=f"Contract issues: {'; '.join(display_issues)}{more}",
                    is_warning=True,
                )
            )
        else:
            self.checks.append(
                CheckResult(
                    name="code-changes-content",
                    passed=True,
                    message="code changes have proper schema definitions",
                )
            )

    def _check_implementation_plan_content(self) -> None:
        """Check implementation plan content quality."""
        plan_dir = self.task_path / "implementation-plan"
        if not plan_dir.exists():
            return

        phases = list(plan_dir.glob("phase-*.md"))
        if not phases:
            return

        issues: list[str] = []

        for phase_file in phases:
            content = phase_file.read_text()
            phase_name = phase_file.stem

            # Check for excessive code blocks (plan should be high-level)
            code_blocks = CODE_BLOCK_PATTERN.findall(content)
            if len(code_blocks) > 3:
                issues.append(
                    f"{phase_name}: has {len(code_blocks)} code blocks - plan should be high-level"
                )

            # Check for objective/goal section
            content_lower = content.lower()
            has_objective = any(
                term in content_lower for term in ["objective", "goal", "purpose", "overview"]
            )
            if not has_objective:
                issues.append(f"{phase_name}: missing Objective/Goal section")

            # Check for steps or tasks
            has_steps = "step" in content_lower or "task" in content_lower or "- [ ]" in content
            if not has_steps:
                issues.append(f"{phase_name}: missing actionable steps/tasks")

        if issues:
            display_issues = issues[:3]
            more = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
            self.checks.append(
                CheckResult(
                    name="implementation-plan-content",
                    passed=False,
                    message=f"Plan issues: {'; '.join(display_issues)}{more}",
                    is_warning=True,
                )
            )
        else:
            self.checks.append(
                CheckResult(
                    name="implementation-plan-content",
                    passed=True,
                    message="Implementation plan is well-structured",
                )
            )

    def _extract_section(self, content: str, section_name: str) -> str | None:
        """Extract content of a markdown section by name."""
        lines = content.split("\n")
        in_section = False
        section_lines: list[str] = []

        for line in lines:
            # Check if this is the section header
            if re.match(rf"^#+\s*{re.escape(section_name)}", line, re.IGNORECASE):
                in_section = True
                continue

            # Check if we hit another header (end of section)
            if in_section and re.match(r"^#+\s+", line):
                break

            if in_section:
                section_lines.append(line)

        return "\n".join(section_lines) if section_lines else None


# Placeholder pattern for unfilled template artifacts
TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\{\{[^}]+\}\}")


class CleanupResult(BaseModel):
    """Result of template artifact cleanup."""

    removed_files: list[str]
    total_removed: int


def cleanup_template_artifacts(task_path: Path) -> CleanupResult:
    """Remove files with unfilled template placeholders.

    Scans all markdown files in the task directory and removes any
    that still contain {{...}} placeholder patterns, indicating
    they were created from templates but never properly filled in.

    Args:
        task_path: Path to the task directory.

    Returns:
        CleanupResult with list of removed files.
    """
    removed_files: list[str] = []

    # saha/v2 tasks: markdown is research prose, never template residue —
    # deleting research/*.md that legitimately contains {{…}} would destroy
    # planning artifacts. The v2 verifier reports placeholders instead.
    if is_v2_task(task_path):
        return CleanupResult(removed_files=[], total_removed=0)

    # Scan all markdown files in the task directory
    for md_file in task_path.rglob("*.md"):
        # Skip the task-description.md as it's the main artifact
        if md_file.name == "task-description.md":
            continue

        try:
            content = md_file.read_text()
            if TEMPLATE_PLACEHOLDER_PATTERN.search(content):
                relative_path = str(md_file.relative_to(task_path))
                logger.info(f"Removing unfilled template artifact: {relative_path}")
                md_file.unlink()
                removed_files.append(relative_path)
        except OSError as e:
            logger.warning(f"Failed to process {md_file}: {e}")

    return CleanupResult(
        removed_files=removed_files,
        total_removed=len(removed_files),
    )
