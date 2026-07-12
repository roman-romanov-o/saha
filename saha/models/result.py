"""Result models for subagents and tools."""

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ResultStatus(str, Enum):
    """Status of a result."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    ERROR = "error"


class ToolResult(BaseModel):
    """Result from running an external tool."""

    tool_name: str
    status: ResultStatus
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    issues: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Check if the tool execution passed."""
        return self.status == ResultStatus.SUCCESS and self.exit_code == 0


class CodeDiff(BaseModel):
    """Represents a code diff from implementation."""

    files_changed: list[Path] = Field(default_factory=list)
    files_added: list[Path] = Field(default_factory=list)
    files_deleted: list[Path] = Field(default_factory=list)
    diff_summary: str = ""
    lines_added: int = 0
    lines_removed: int = 0


class SubagentResult(BaseModel):
    """Result from a subagent execution."""

    agent_name: str
    status: ResultStatus
    output: str = ""
    structured_output: dict[str, Any] = Field(default_factory=dict)
    code_diff: CodeDiff | None = None
    error: str | None = None
    tokens_used: int = 0

    @property
    def succeeded(self) -> bool:
        """Check if the subagent execution succeeded."""
        return self.status == ResultStatus.SUCCESS


class QACheckResult(BaseModel):
    """Single QA check result."""

    check_name: str
    passed: bool
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ManualCheck(BaseModel):
    """An acceptance criterion deferred to human sign-off (verify:manual)."""

    criterion: str
    instructions: str = ""


class QAResult(BaseModel):
    """Result from QA subagent."""

    status: ResultStatus
    dod_achieved: bool = False
    checks: list[QACheckResult] = Field(default_factory=list)
    fix_info: str | None = None
    test_output: str = ""
    script_outputs: dict[str, str] = Field(default_factory=dict)
    manual_checks: list[ManualCheck] = Field(default_factory=list)

    @property
    def all_checks_passed(self) -> bool:
        """Check if all QA checks passed."""
        return all(check.passed for check in self.checks)


class QualityIssue(BaseModel):
    """Single code quality issue identified by the agent."""

    file: str
    line: int | None = None
    tool: str
    code: str = ""
    message: str
    severity: str = "warning"
    is_blocking: bool = False
    reason_if_ignored: str | None = None


class CodeQualityResult(BaseModel):
    """Result from code quality subagent."""

    status: ResultStatus
    passed: bool = False
    fix_info: str | None = None

    # New fields from Code Quality agent
    issues: list[QualityIssue | dict[str, Any]] = Field(default_factory=list)
    files_analyzed: list[str] = Field(default_factory=list)
    blocking_issues_count: int = 0
    ignored_issues_count: int = 0

    # Legacy fields for backward compatibility (optional)
    ruff_result: ToolResult | None = None
    ty_result: ToolResult | None = None
    complexity_result: ToolResult | None = None

    @property
    def all_tools_passed(self) -> bool:
        """Check if all quality tools passed (legacy)."""
        results = [self.ruff_result, self.ty_result, self.complexity_result]
        return all(r.passed for r in results if r is not None)

    @property
    def has_blocking_issues(self) -> bool:
        """Check if there are blocking issues."""
        return self.blocking_issues_count > 0


class TestCritiqueIssue(BaseModel):
    """Single test quality issue found during critique."""

    severity: str  # critical, high, medium
    file: str
    line: int | None = None
    pattern: str  # over_mocking, mocking_sut, placeholder, etc.
    description: str


class TestCritiqueResult(BaseModel):
    """Result from test critique subagent."""

    status: ResultStatus
    passed: bool = False  # True if tests are not hollow
    test_quality_score: str = "C"  # A, B, C, D, F
    tests_analyzed: int = 0
    hollow_tests: int = 0
    issues: list[TestCritiqueIssue | dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    fix_info: str | None = None

    @property
    def is_blocking(self) -> bool:
        """Check if test quality blocks QA (score D or F)."""
        return self.test_quality_score in ("D", "F")
