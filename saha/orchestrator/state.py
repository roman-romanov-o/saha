"""State manager for persisting execution state to YAML."""

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from saha.models.state import ExecutionState, LoopPhase, StepStatus


class StateManager:
    """Manages execution state persistence to .sahaidachny/ directory."""

    def __init__(self, state_dir: Path):
        self._state_dir = state_dir
        self._current_state: ExecutionState | None = None

    @property
    def state_dir(self) -> Path:
        """Get the state directory."""
        return self._state_dir

    @property
    def current_state(self) -> ExecutionState | None:
        """Get the current loaded state."""
        return self._current_state

    def ensure_state_dir(self) -> None:
        """Ensure the state directory exists."""
        self._state_dir.mkdir(parents=True, exist_ok=True)

    def get_state_file(self, task_id: str) -> Path:
        """Get the state file path for a task."""
        return self._state_dir / f"{task_id}-execution-state.yaml"

    def load(self, task_id: str) -> ExecutionState | None:
        """Load execution state from file."""
        state_file = self.get_state_file(task_id)

        if not state_file.exists():
            return None

        try:
            with state_file.open() as f:
                data = yaml.safe_load(f)

            if not data:
                return None

            self._current_state = ExecutionState.model_validate(data)
            return self._current_state

        except (yaml.YAMLError, ValueError) as e:
            raise StateError(f"Failed to load state: {e}") from e

    def save(self, state: ExecutionState) -> None:
        """Save execution state to file."""
        self.ensure_state_dir()
        state_file = self.get_state_file(state.task_id)

        try:
            # Convert to dict with custom serialization
            data = self._serialize_state(state)

            with state_file.open("w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)

            self._current_state = state

        except (yaml.YAMLError, OSError) as e:
            raise StateError(f"Failed to save state: {e}") from e

    def create(
        self,
        task_id: str,
        task_path: Path,
        max_iterations: int = 10,
        enabled_tools: list[str] | None = None,
    ) -> ExecutionState:
        """Create a new execution state."""
        state = ExecutionState(
            task_id=task_id,
            task_path=task_path,
            max_iterations=max_iterations,
            enabled_tools=enabled_tools or [],
            started_at=datetime.now(),
        )
        self.save(state)
        return state

    def update_phase(self, state: ExecutionState, phase: LoopPhase) -> None:
        """Update the current phase and save."""
        state.current_phase = phase
        state.record_step(phase, StepStatus.IN_PROGRESS)
        self.save(state)

    def complete_phase(
        self,
        state: ExecutionState,
        phase: LoopPhase,
        output_summary: str | None = None,
    ) -> None:
        """Mark phase as completed and save."""
        state.record_step(phase, StepStatus.COMPLETED, output_summary=output_summary)
        self.save(state)

    def fail_phase(
        self,
        state: ExecutionState,
        phase: LoopPhase,
        error: str,
    ) -> None:
        """Mark phase as failed and save.

        This sets the current phase to FAILED, which will stop the agentic loop.
        """
        state.current_phase = LoopPhase.FAILED
        state.error_message = error
        state.record_step(phase, StepStatus.FAILED, error=error)
        self.save(state)

    def mark_completed(self, state: ExecutionState) -> None:
        """Mark the entire execution as completed."""
        state.current_phase = LoopPhase.COMPLETED
        state.completed_at = datetime.now()
        self.save(state)

    def mark_completed_pending_manual(self, state: ExecutionState) -> None:
        """Mark execution code-complete with manual checks awaiting sign-off.

        A success terminal state: all automated/build criteria pass, but one or
        more ``verify:manual`` criteria still need a human. The accumulated
        checks live in ``state.context['pending_manual_checks']``.
        """
        state.current_phase = LoopPhase.COMPLETED_PENDING_MANUAL
        state.completed_at = datetime.now()
        self.save(state)

    def mark_failed(self, state: ExecutionState, error: str) -> None:
        """Mark the entire execution as failed."""
        state.current_phase = LoopPhase.FAILED
        state.completed_at = datetime.now()
        state.error_message = error
        state.record_step(LoopPhase.FAILED, StepStatus.FAILED, error=error)
        self.save(state)

    def mark_stopped(self, state: ExecutionState, reason: str | None = None) -> None:
        """Mark the entire execution as stopped."""
        state.current_phase = LoopPhase.STOPPED
        state.completed_at = datetime.now()
        state.error_message = reason
        state.record_step(LoopPhase.STOPPED, StepStatus.SKIPPED, error=reason)
        self.save(state)

    def list_tasks(self) -> list[str]:
        """List all tasks with saved state."""
        if not self._state_dir.exists():
            return []

        task_ids = []
        for state_file in self._state_dir.glob("*-execution-state.yaml"):
            task_id = state_file.stem.replace("-execution-state", "")
            task_ids.append(task_id)

        return sorted(task_ids)

    def delete(self, task_id: str) -> bool:
        """Delete execution state for a task."""
        state_file = self.get_state_file(task_id)

        if state_file.exists():
            state_file.unlink()
            if self._current_state and self._current_state.task_id == task_id:
                self._current_state = None
            return True

        return False

    def _serialize_state(self, state: ExecutionState) -> dict[str, Any]:
        """Serialize state to a dict suitable for YAML."""
        data = state.model_dump(mode="json")

        # Convert Path objects to strings
        if "task_path" in data:
            data["task_path"] = str(data["task_path"])

        return data


class StateError(Exception):
    """Error during state operations."""

    pass
