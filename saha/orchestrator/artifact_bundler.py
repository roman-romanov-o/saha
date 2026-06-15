"""Status-aware bundler for task artifacts.

Loads task-folder content (user stories, test specs, code changes, plan
phases, design decisions, API contracts) once per iteration and serves
filtered, view-specific snapshots to each phase of the agentic loop.

The goals:
- Eliminate redundant `Read` calls inside verifier subagents that all
  re-discover the same task folder.
- Filter by lifecycle status so completed work isn't re-shipped on every
  iteration (Done stories become stubs; Draft items are skipped for active
  phases).
- Cap the bundled payload via a soft size budget, progressively stubbing
  the largest items if the bundle would exceed the budget.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status normalization
# ---------------------------------------------------------------------------


class LifecycleStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    UNKNOWN = "unknown"


_STATUS_ALIASES: dict[str, LifecycleStatus] = {
    "draft": LifecycleStatus.DRAFT,
    "not started": LifecycleStatus.DRAFT,
    "pending": LifecycleStatus.DRAFT,
    "ready": LifecycleStatus.READY,
    "existing": LifecycleStatus.READY,
    "in progress": LifecycleStatus.IN_PROGRESS,
    "implementing": LifecycleStatus.IN_PROGRESS,
    "qa verification": LifecycleStatus.IN_PROGRESS,
    "validating compliance": LifecycleStatus.IN_PROGRESS,
    "active": LifecycleStatus.IN_PROGRESS,
    "done": LifecycleStatus.DONE,
    "complete": LifecycleStatus.DONE,
    "completed": LifecycleStatus.DONE,
    "implemented": LifecycleStatus.DONE,
}


def normalize_status(raw: str | None) -> LifecycleStatus:
    """Map a free-form status string to a canonical LifecycleStatus."""
    if not raw:
        return LifecycleStatus.UNKNOWN
    head = raw.strip().lower().split("(", 1)[0].strip()
    head = head.rstrip(".:;,")
    return _STATUS_ALIASES.get(head, LifecycleStatus.UNKNOWN)


_STATUS_LINE = re.compile(r"^\*\*Status:\*\*\s*(?P<value>.+)$", re.MULTILINE)


def extract_status(markdown: str) -> LifecycleStatus:
    """Pull `**Status:** ...` from a markdown body and normalize it."""
    match = _STATUS_LINE.search(markdown)
    if not match:
        return LifecycleStatus.UNKNOWN
    return normalize_status(match.group("value"))


_TITLE_LINE = re.compile(r"^#\s+(?P<title>.+)$", re.MULTILINE)


def extract_title(markdown: str, fallback: str) -> str:
    match = _TITLE_LINE.search(markdown)
    if not match:
        return fallback
    return match.group("title").strip()


_AC_LINE = re.compile(r"^\d+\.\s+\*\*Given\*\*", re.MULTILINE)


def count_acceptance_criteria(markdown: str) -> int:
    return len(_AC_LINE.findall(markdown))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ArtifactView(StrEnum):
    IMPLEMENTER = "implementer"
    TEST_CRITIQUE = "test_critique"
    QA = "qa"
    CODE_QUALITY = "code_quality"
    MANAGER = "manager"
    DOD = "dod"


class Story(BaseModel):
    """A user story. `body` is None when the story is shipped as a stub."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: LifecycleStatus
    acceptance_criteria_count: int = 0
    body: str | None = None


class TestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    test_type: Literal["e2e", "integration", "unit"]
    status: LifecycleStatus
    body: str | None = None


class CodeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: LifecycleStatus
    body: str | None = None


class PlanPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: LifecycleStatus
    body: str | None = None


class TaskArtifacts(BaseModel):
    """View-filtered snapshot of a task folder.

    Serialized to JSON and embedded in the prompt sent to a subagent.
    """

    model_config = ConfigDict(extra="forbid")

    view: ArtifactView
    task_description: str = ""
    user_stories: list[Story] = Field(default_factory=list)
    test_specs: list[TestSpec] = Field(default_factory=list)
    code_changes: list[CodeChange] = Field(default_factory=list)
    design_decisions: dict[str, str] = Field(default_factory=dict)
    api_contracts: dict[str, str] = Field(default_factory=dict)
    implementation_plan: list[PlanPhase] = Field(default_factory=list)
    truncated: bool = False
    truncation_notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# View policy
# ---------------------------------------------------------------------------


_StatusMode = Literal["full", "stub", "skip"]


@dataclass(frozen=True)
class ViewPolicy:
    """How a given ArtifactView treats each artifact slice and status.

    Per-status modes choose between full content, stub (id/title/status
    only), or skip (omit entirely).
    """

    user_stories: dict[LifecycleStatus, _StatusMode]
    test_specs: dict[LifecycleStatus, _StatusMode]
    code_changes: dict[LifecycleStatus, _StatusMode]
    implementation_plan: dict[LifecycleStatus, _StatusMode]
    include_task_description: bool = True
    include_design_decisions: bool = True
    include_api_contracts: bool = True


def _active_only_policy() -> dict[LifecycleStatus, _StatusMode]:
    """Active phases (implementer/QA/test-critique): full bodies for active work, skip the rest."""
    return {
        LifecycleStatus.READY: "full",
        LifecycleStatus.IN_PROGRESS: "full",
        LifecycleStatus.UNKNOWN: "full",
        LifecycleStatus.DRAFT: "skip",
        LifecycleStatus.DONE: "skip",
    }


def _manager_policy() -> dict[LifecycleStatus, _StatusMode]:
    """Manager: full for active, stubs for everything else (so it can update status)."""
    return {
        LifecycleStatus.READY: "full",
        LifecycleStatus.IN_PROGRESS: "full",
        LifecycleStatus.UNKNOWN: "full",
        LifecycleStatus.DRAFT: "stub",
        LifecycleStatus.DONE: "stub",
    }


def _full_everywhere() -> dict[LifecycleStatus, _StatusMode]:
    return dict.fromkeys(LifecycleStatus, "full")


def _skip_everywhere() -> dict[LifecycleStatus, _StatusMode]:
    return dict.fromkeys(LifecycleStatus, "skip")


VIEW_POLICIES: dict[ArtifactView, ViewPolicy] = {
    ArtifactView.IMPLEMENTER: ViewPolicy(
        user_stories=_active_only_policy(),
        test_specs=_active_only_policy(),
        code_changes=_active_only_policy(),
        implementation_plan=_active_only_policy(),
    ),
    ArtifactView.TEST_CRITIQUE: ViewPolicy(
        user_stories=_active_only_policy(),
        test_specs=_active_only_policy(),
        code_changes=_active_only_policy(),
        implementation_plan=_skip_everywhere(),
        include_task_description=True,
        include_design_decisions=False,
        include_api_contracts=False,
    ),
    ArtifactView.QA: ViewPolicy(
        user_stories=_active_only_policy(),
        test_specs=_active_only_policy(),
        code_changes=_active_only_policy(),
        implementation_plan=_skip_everywhere(),
        include_design_decisions=False,
    ),
    ArtifactView.CODE_QUALITY: ViewPolicy(
        user_stories=_skip_everywhere(),
        test_specs=_skip_everywhere(),
        code_changes=_skip_everywhere(),
        implementation_plan=_skip_everywhere(),
        include_task_description=False,
        include_design_decisions=False,
        include_api_contracts=False,
    ),
    ArtifactView.MANAGER: ViewPolicy(
        user_stories=_manager_policy(),
        test_specs=_manager_policy(),
        code_changes=_manager_policy(),
        implementation_plan=_manager_policy(),
        include_design_decisions=False,
    ),
    ArtifactView.DOD: ViewPolicy(
        user_stories=_full_everywhere(),
        test_specs=_full_everywhere(),
        code_changes=_full_everywhere(),
        implementation_plan=_full_everywhere(),
    ),
}


# ---------------------------------------------------------------------------
# Raw cache
# ---------------------------------------------------------------------------


@dataclass
class _RawCache:
    """All task-folder content read from disk, before view filtering."""

    task_description: str = ""
    user_stories: list[Story] = field(default_factory=list)
    test_specs: list[TestSpec] = field(default_factory=list)
    code_changes: list[CodeChange] = field(default_factory=list)
    design_decisions: dict[str, str] = field(default_factory=dict)
    api_contracts: dict[str, str] = field(default_factory=dict)
    implementation_plan: list[PlanPhase] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bundler
# ---------------------------------------------------------------------------


_DEFAULT_MAX_SIZE_BYTES = 150 * 1024
_STUB_VALUE = "[stubbed for size budget]"
_MAX_TRUNCATION_PASSES = 200


class ArtifactBundler:
    """Loads, caches, and serves view-filtered task artifacts."""

    def __init__(
        self,
        task_path: Path,
        max_size_bytes: int = _DEFAULT_MAX_SIZE_BYTES,
    ) -> None:
        self.task_path = task_path
        self.max_size_bytes = max_size_bytes
        self._raw_cache: _RawCache | None = None
        self._mtime_signature: tuple[tuple[str, int], ...] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, view: ArtifactView) -> TaskArtifacts:
        """Return a view-filtered, size-bounded snapshot of the task folder."""
        raw = self._load_raw()
        artifacts = self._apply_view(raw, view)
        return self._enforce_size_budget(artifacts)

    # ------------------------------------------------------------------
    # Cache + invalidation
    # ------------------------------------------------------------------

    def _load_raw(self) -> _RawCache:
        sig = self._compute_mtime_signature()
        if self._raw_cache is not None and sig == self._mtime_signature:
            return self._raw_cache
        self._raw_cache = self._read_all()
        self._mtime_signature = sig
        return self._raw_cache

    def _compute_mtime_signature(self) -> tuple[tuple[str, int], ...]:
        entries: list[tuple[str, int]] = []
        for path in sorted(self._all_relevant_files()):
            try:
                entries.append((str(path), path.stat().st_mtime_ns))
            except FileNotFoundError:
                continue
        return tuple(entries)

    def _all_relevant_files(self) -> list[Path]:
        out: list[Path] = []
        for sub in ("task-description.md",):
            p = self.task_path / sub
            if p.exists():
                out.append(p)
        for folder in (
            "user-stories",
            "test-specs/e2e",
            "test-specs/integration",
            "test-specs/unit",
            "code-changes",
            "design-decisions",
            "api-contracts",
            "implementation-plan",
        ):
            d = self.task_path / folder
            if d.is_dir():
                out.extend(d.glob("*.md"))
        return out

    # ------------------------------------------------------------------
    # File reading
    # ------------------------------------------------------------------

    def _read_all(self) -> _RawCache:
        return _RawCache(
            task_description=self._read_task_description(),
            user_stories=self._read_user_stories(),
            test_specs=self._read_test_specs(),
            code_changes=self._read_code_changes(),
            design_decisions=self._read_doc_dict("design-decisions"),
            api_contracts=self._read_doc_dict("api-contracts"),
            implementation_plan=self._read_plan_phases(),
        )

    def _read_task_description(self) -> str:
        path = self.task_path / "task-description.md"
        return _safe_read_text(path)

    def _read_user_stories(self) -> list[Story]:
        folder = self.task_path / "user-stories"
        if not folder.is_dir():
            return []
        stories: list[Story] = []
        for path in sorted(folder.glob("US-*.md")):
            body = _safe_read_text(path)
            if not body:
                continue
            stories.append(
                Story(
                    id=path.stem,
                    title=extract_title(body, fallback=path.stem),
                    status=extract_status(body),
                    acceptance_criteria_count=count_acceptance_criteria(body),
                    body=body,
                )
            )
        return stories

    def _read_test_specs(self) -> list[TestSpec]:
        out: list[TestSpec] = []
        for test_type in ("e2e", "integration", "unit"):
            folder = self.task_path / "test-specs" / test_type
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.md")):
                if path.name.lower() == "readme.md":
                    continue
                body = _safe_read_text(path)
                if not body:
                    continue
                out.append(
                    TestSpec(
                        name=path.stem,
                        test_type=test_type,
                        status=extract_status(body),
                        body=body,
                    )
                )
        return out

    def _read_code_changes(self) -> list[CodeChange]:
        folder = self.task_path / "code-changes"
        if not folder.is_dir():
            return []
        out: list[CodeChange] = []
        for path in sorted(folder.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            body = _safe_read_text(path)
            if not body:
                continue
            out.append(
                CodeChange(
                    name=path.stem,
                    status=extract_status(body),
                    body=body,
                )
            )
        return out

    def _read_plan_phases(self) -> list[PlanPhase]:
        folder = self.task_path / "implementation-plan"
        if not folder.is_dir():
            return []
        out: list[PlanPhase] = []
        for path in sorted(folder.glob("phase-*.md")):
            body = _safe_read_text(path)
            if not body:
                continue
            out.append(
                PlanPhase(
                    name=path.stem,
                    status=extract_status(body),
                    body=body,
                )
            )
        return out

    def _read_doc_dict(self, subdir: str) -> dict[str, str]:
        folder = self.task_path / subdir
        if not folder.is_dir():
            return {}
        out: dict[str, str] = {}
        for path in sorted(folder.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            body = _safe_read_text(path)
            if body:
                out[path.stem] = body
        return out

    # ------------------------------------------------------------------
    # View application
    # ------------------------------------------------------------------

    def _apply_view(self, raw: _RawCache, view: ArtifactView) -> TaskArtifacts:
        policy = VIEW_POLICIES[view]
        stories = _apply_status_policy(raw.user_stories, policy.user_stories)
        # Empty-list safety net: if filtering removed everything but raw data exists,
        # ship stubs so the agent at least knows what was planned. Without this,
        # a task whose stories are all Done/Draft would get zero context for views
        # that filter aggressively (implementer/QA/test-critique).
        if not stories and raw.user_stories and view != ArtifactView.CODE_QUALITY:
            stories = [s.model_copy(update={"body": None}) for s in raw.user_stories]
            logger.debug(
                "View %s had no active stories; falling back to stubs for %d stories",
                view.value,
                len(raw.user_stories),
            )
        return TaskArtifacts(
            view=view,
            task_description=raw.task_description if policy.include_task_description else "",
            user_stories=stories,
            test_specs=_apply_status_policy(raw.test_specs, policy.test_specs),
            code_changes=_apply_status_policy(raw.code_changes, policy.code_changes),
            implementation_plan=_apply_status_policy(
                raw.implementation_plan, policy.implementation_plan
            ),
            design_decisions=raw.design_decisions if policy.include_design_decisions else {},
            api_contracts=raw.api_contracts if policy.include_api_contracts else {},
        )

    # ------------------------------------------------------------------
    # Size budget
    # ------------------------------------------------------------------

    def _enforce_size_budget(self, artifacts: TaskArtifacts) -> TaskArtifacts:
        """Progressively stub the largest item bodies until under budget."""
        if self.max_size_bytes <= 0:
            return artifacts
        size = _estimate_size_bytes(artifacts)
        if size <= self.max_size_bytes:
            return artifacts

        notes: list[str] = list(artifacts.truncation_notes)
        notes.append(
            f"Initial bundle was {size} bytes, over the {self.max_size_bytes}-byte budget."
        )

        # Stub items in priority order: lowest-importance slices first.
        candidate_slices = [
            ("design_decisions", artifacts.design_decisions),
            ("api_contracts", artifacts.api_contracts),
            ("implementation_plan", artifacts.implementation_plan),
            ("test_specs", artifacts.test_specs),
            ("code_changes", artifacts.code_changes),
            ("user_stories", artifacts.user_stories),
        ]

        passes = 0
        for name, items in candidate_slices:
            while _estimate_size_bytes(artifacts) > self.max_size_bytes:
                passes += 1
                if passes > _MAX_TRUNCATION_PASSES:
                    notes.append("Truncation pass cap reached; aborting further stubbing.")
                    logger.error("Artifact bundle truncation exceeded safety cap")
                    artifacts.truncated = True
                    artifacts.truncation_notes = notes
                    return artifacts

                stubbed_label = _stub_largest(items)
                if stubbed_label is None:
                    break
                notes.append(f"Stubbed '{stubbed_label}' from {name}")

            if _estimate_size_bytes(artifacts) <= self.max_size_bytes:
                break

        artifacts.truncated = True
        artifacts.truncation_notes = notes
        logger.warning("Artifact bundle exceeded budget; %d truncations applied", len(notes) - 1)
        return artifacts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return ""


def _apply_status_policy(items, policy):  # type: ignore[no-untyped-def]
    out = []
    for item in items:
        mode = policy.get(item.status, "full")
        if mode == "skip":
            continue
        if mode == "stub":
            out.append(item.model_copy(update={"body": None}))
        else:
            out.append(item)
    return out


def _estimate_size_bytes(artifacts: TaskArtifacts) -> int:
    return len(artifacts.model_dump_json(exclude_none=True).encode("utf-8"))


def _stub_largest(items) -> str | None:  # type: ignore[no-untyped-def]
    """Mutate `items` in place: stub the entry with the largest body. Return its label, or None if nothing stubbable remains."""
    if isinstance(items, dict):
        return _stub_largest_dict(items)
    return _stub_largest_list(items)


def _stub_largest_dict(items: dict[str, str]) -> str | None:
    candidates = [(k, v) for k, v in items.items() if v and v != _STUB_VALUE]
    if not candidates:
        return None
    key, _ = max(candidates, key=lambda kv: len(kv[1]))
    items[key] = _STUB_VALUE
    return key


def _stub_largest_list(items) -> str | None:  # type: ignore[no-untyped-def]
    largest_idx: int | None = None
    largest_size = -1
    for idx, item in enumerate(items):
        body = getattr(item, "body", None)
        if not body:
            continue
        if len(body) > largest_size:
            largest_size = len(body)
            largest_idx = idx
    if largest_idx is None:
        return None
    target = items[largest_idx]
    items[largest_idx] = target.model_copy(update={"body": None})
    return getattr(target, "id", None) or getattr(target, "name", "<unknown>")
