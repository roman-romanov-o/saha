"""Base hook interface and event definitions."""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any


class HookEvent(StrEnum):
    """Events that can trigger hooks."""

    # Loop lifecycle
    LOOP_START = "loop_start"
    LOOP_COMPLETE = "loop_complete"
    LOOP_FAILED = "loop_failed"
    LOOP_ERROR = "loop_error"
    LOOP_STOPPED = "loop_stopped"

    # Iteration lifecycle
    ITERATION_START = "iteration_start"
    ITERATION_COMPLETE = "iteration_complete"

    # Phase events
    IMPLEMENTATION_START = "implementation_start"
    TEST_CRITIQUE_START = "test_critique_start"
    TEST_CRITIQUE_FAILED = "test_critique_failed"
    QA_START = "qa_start"
    QA_FAILED = "qa_failed"
    QUALITY_START = "quality_start"
    QUALITY_FAILED = "quality_failed"
    MANAGER_START = "manager_start"
    DOD_CHECK_START = "dod_check_start"


class Hook(ABC):
    """Abstract base class for hooks."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Hook name for identification."""
        ...

    @property
    def events(self) -> list[HookEvent]:
        """Events this hook listens to. Empty means all events."""
        return []

    @abstractmethod
    def execute(self, event: HookEvent, **kwargs: Any) -> None:
        """Execute the hook.

        Args:
            event: The event that triggered the hook.
            **kwargs: Event-specific data (state, error, etc.).
        """
        ...

    def should_trigger(self, event: HookEvent) -> bool:
        """Check if this hook should trigger for the given event."""
        if not self.events:
            return True
        return event in self.events
