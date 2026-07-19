"""Plan-progress handling for saha/v2 tasks.

In v2 the tracking file is progress.yaml and its only in-loop writer is
the manager AGENT — the Python side must not decode→re-encode the YAML
(PyYAML drops comments and reorders keys). So:

- phase selection reads the YAML and stores the active phase ID in the
  loop state (the legacy updater stored a markdown file path);
- per-stage progress writes and mark-all-complete are deliberate no-ops;
- the one sanctioned Python write is :func:`set_task_status`, a surgical
  line patch of the single top-level ``status:`` line (same discipline as
  the kanban app's PlanningProgressWriteback).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from saha.models.progress import (
    ProgressFileError,
    is_v2_task,
    load_progress,
    progress_yaml_path,
)
from saha.models.state import ExecutionState, LoopPhase
from saha.orchestrator.plan_progress import PlanPhaseSelection, PlanProgressUpdater

logger = logging.getLogger(__name__)

TASK_STATUSES = {"planning", "executing", "completed", "completed_pending_manual", "failed"}

_TOP_LEVEL_STATUS = re.compile(r"^status:(\s*)(?P<value>\S+)(?P<rest>.*)$")


class V2ProgressUpdater(PlanProgressUpdater):
    """Phase selection from progress.yaml; agent-owned writes stay no-ops."""

    def __init__(self, task_path: Path):
        super().__init__(task_path)
        self._v2_task_path = task_path
        self._progress_path = progress_yaml_path(task_path)

    def select_active_phase(self, state: ExecutionState | None = None) -> PlanPhaseSelection | None:
        """First phase not yet done (else the last); phase ID goes into state."""
        try:
            progress = load_progress(self._v2_task_path)
        except ProgressFileError as exc:
            logger.warning("v2 phase selection failed: %s", exc)
            return None
        if not progress.phases:
            return None
        active = next(
            (p for p in progress.phases if p.status != "done"),
            progress.phases[-1],
        )
        updated = self._store_phase_id(state, active.id)
        return PlanPhaseSelection(phase_path=self._progress_path, updated_context=updated)

    def update_execution_progress(
        self,
        phase_path: Path,
        loop_phase: LoopPhase,
        status_kind: str,
        iteration: int,
        note: str | None = None,
        timestamp: datetime | None = None,
        update_status_line: bool = True,
    ) -> bool:
        """No-op: in-loop progress.yaml writes belong to the manager agent."""
        return False

    def mark_all_complete(self, note: str | None = None) -> int:
        """No-op: the manager agent rolls up statuses; Python never bulk-ticks."""
        return 0

    def _store_phase_id(self, state: ExecutionState | None, phase_id: str) -> bool:
        if state is None or state.context.get("current_plan_phase") == phase_id:
            return False
        state.context["current_plan_phase"] = phase_id
        return True


def set_task_status(task_path: Path, new_status: str) -> bool:
    """Surgically rewrite the top-level ``status:`` line of progress.yaml.

    Patches only that line (comments, AC-level ``status:`` lines, unknown
    keys all survive byte-for-byte). Returns True iff the file changed.
    """
    if new_status not in TASK_STATUSES:
        raise ValueError(f"Unknown v2 task status: {new_status!r}")
    if not is_v2_task(task_path):
        return False
    path = progress_yaml_path(task_path)
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        match = _TOP_LEVEL_STATUS.match(line)
        if not match:
            continue
        if match.group("value") == new_status:
            return False
        lines[i] = f"status:{match.group(1)}{new_status}{match.group('rest')}"
        path.write_text("\n".join(lines), encoding="utf-8")
        return True
    logger.warning("No top-level status line in %s; cannot set %s", path, new_status)
    return False
