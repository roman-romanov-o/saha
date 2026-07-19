"""Pydantic models for the saha/v2 ``progress.yaml`` tracking file.

A task folder is format saha/v2 when its ``progress.yaml`` carries the
``format: saha/v2`` marker. The YAML is the ONLY mutable task file during
execution; the LikeC4 spec in ``model/*.c4`` is frozen.

Parsing is deliberately lenient (``extra="ignore"``): the schema may grow
new keys (``created``, ``mode``, per-step ``gaps``…) and readers must not
break on them. Semantic validation lives in the v2 verifier, not here.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

V2_FORMAT_VALUE = "saha/v2"

_FORMAT_LINE = re.compile(r"^format:\s*(?P<value>\S+)\s*$", re.MULTILINE)


class ProgressFileError(Exception):
    """Raised when a progress.yaml cannot be parsed into the v2 schema."""


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    text: str = ""
    verify: str = ""  # automated | build | manual
    manual_instructions: str | None = None
    status: str = "pending"  # pending | in_progress | done
    specs: list[str] = Field(default_factory=list)  # planned coverage (ts-* view ids)
    tests: list[str] = Field(default_factory=list)  # actual bindings (loop-written)


class EdgeCase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    trigger: str = ""
    expected: str = ""


class StoryProgress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    title: str = ""
    view: str | None = None  # dynamic view id in model/stories.c4
    priority: str = "must"  # must | should | could
    kind: str = "story"  # story | enabler (enabler = developer/system persona)
    status: str = "draft"  # draft | ready | in_progress | done
    story: str = ""
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    edge_cases: list[EdgeCase] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)


class PhaseStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    files: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending | in_progress | done


class PhaseProgress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    title: str = ""
    view: str | None = None
    stories: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending | in_progress | done
    steps: list[PhaseStep] = Field(default_factory=list)


class TestSpecGap(BaseModel):
    model_config = ConfigDict(extra="ignore")

    story: str = ""
    reason: str = ""


class PlanningStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "pending"  # pending | in_progress | done | skipped
    views: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    gaps: list[TestSpecGap] = Field(default_factory=list)
    result: str | None = None  # verify step: passed | passed_with_warnings | failed


class ProgressFile(BaseModel):
    """The whole progress.yaml, read-only view for the Python tooling."""

    model_config = ConfigDict(extra="ignore")

    format: str = ""
    task: str = ""
    title: str = ""
    status: str = "planning"  # planning | executing | completed | completed_pending_manual | failed
    planning: dict[str, PlanningStep] = Field(default_factory=dict)
    stories: list[StoryProgress] = Field(default_factory=list)
    phases: list[PhaseProgress] = Field(default_factory=list)
    iterations: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def is_v2(self) -> bool:
        return self.format == V2_FORMAT_VALUE

    def story_by_id(self, story_id: str) -> StoryProgress | None:
        return next((s for s in self.stories if s.id == story_id), None)

    def test_spec_gaps(self) -> list[TestSpecGap]:
        step = self.planning.get("test_specs")
        return step.gaps if step else []


def progress_yaml_path(task_path: Path) -> Path:
    return task_path / "progress.yaml"


def is_v2_task(task_path: Path) -> bool:
    """Cheap format detection: a ``format: saha/v2`` line in progress.yaml.

    A text scan (not a YAML parse) so a malformed file still routes to the
    v2 path, where the verifier reports a real error instead of the legacy
    checker complaining about missing markdown.
    """
    try:
        text = progress_yaml_path(task_path).read_text(encoding="utf-8")
    except OSError:
        return False
    match = _FORMAT_LINE.search(text)
    return bool(match and match.group("value") == V2_FORMAT_VALUE)


def load_progress(task_path: Path) -> ProgressFile:
    """Parse progress.yaml into the v2 schema. Raises ProgressFileError."""
    path = progress_yaml_path(task_path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProgressFileError(f"Cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProgressFileError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProgressFileError(f"{path} is not a YAML mapping")
    try:
        return ProgressFile.model_validate(raw)
    except ValueError as exc:
        raise ProgressFileError(f"{path} does not match the saha/v2 schema: {exc}") from exc
