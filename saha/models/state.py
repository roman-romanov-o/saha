"""Execution state models for the agentic loop."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class LoopPhase(StrEnum):
    """Current phase in the agentic loop."""

    IDLE = "idle"
    SCHEDULED = "scheduled"
    IMPLEMENTATION = "implementation"
    TEST_CRITIQUE = "test_critique"
    QA = "qa"
    CODE_QUALITY = "code_quality"
    MANAGER = "manager"
    DOD_CHECK = "dod_check"
    STOPPED = "stopped"
    COMPLETED = "completed"
    # Code-complete, but one or more verify:manual acceptance criteria still
    # await human sign-off. A success terminal state — it prevents the loop
    # from churning to max-iter on criteria it can never auto-verify.
    COMPLETED_PENDING_MANUAL = "completed_pending_manual"
    FAILED = "failed"


class StepStatus(StrEnum):
    """Status of a single step in execution."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepRecord(BaseModel):
    """Record of a single step execution."""

    phase: LoopPhase
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt: int = 1
    error: str | None = None
    output_summary: str | None = None


class PhaseTokenUsage(BaseModel):
    """Token usage and timing for a single phase invocation in an iteration."""

    phase: LoopPhase
    agent_name: str
    runner_name: str | None = None
    started_at: datetime
    completed_at: datetime
    duration_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


class IterationRecord(BaseModel):
    """Record of a single loop iteration."""

    iteration: int
    started_at: datetime
    completed_at: datetime | None = None
    steps: list[StepRecord] = Field(default_factory=list)
    test_critique_passed: bool = False
    dod_achieved: bool = False
    quality_passed: bool = False
    fix_info: str | None = None
    # Progress tracking: files modified during this iteration
    files_changed: list[str] = Field(default_factory=list)
    files_added: list[str] = Field(default_factory=list)
    # Per-phase token usage and timing for this iteration
    token_usage: list[PhaseTokenUsage] = Field(default_factory=list)


class ExecutionState(BaseModel):
    """Full execution state for the agentic loop."""

    task_id: str
    task_path: Path
    current_phase: LoopPhase = LoopPhase.IDLE
    current_iteration: int = 0
    max_iterations: int = 10
    started_at: datetime | None = None
    scheduled_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    iterations: list[IterationRecord] = Field(default_factory=list)
    enabled_tools: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    # Agent transcript for potential resume (stores last agent output)
    last_agent_output: str | None = None

    @property
    def is_running(self) -> bool:
        """Check if the loop is currently running."""
        return self.current_phase not in (
            LoopPhase.IDLE,
            LoopPhase.SCHEDULED,
            LoopPhase.STOPPED,
            LoopPhase.COMPLETED,
            LoopPhase.COMPLETED_PENDING_MANUAL,
            LoopPhase.FAILED,
        )

    @property
    def current_iteration_record(self) -> IterationRecord | None:
        """Get the current iteration record."""
        if not self.iterations:
            return None
        return self.iterations[-1]

    def start_iteration(self) -> IterationRecord:
        """Start a new iteration."""
        self.current_iteration += 1
        record = IterationRecord(
            iteration=self.current_iteration,
            started_at=datetime.now(),
        )
        self.iterations.append(record)
        return record

    def record_step(
        self,
        phase: LoopPhase,
        status: StepStatus,
        error: str | None = None,
        output_summary: str | None = None,
    ) -> StepRecord:
        """Record a step in the current iteration."""
        if not self.iterations:
            self.start_iteration()

        step = StepRecord(
            phase=phase,
            status=status,
            started_at=datetime.now() if status == StepStatus.IN_PROGRESS else None,
            completed_at=datetime.now() if status == StepStatus.COMPLETED else None,
            error=error,
            output_summary=output_summary,
        )
        self.iterations[-1].steps.append(step)
        return step
