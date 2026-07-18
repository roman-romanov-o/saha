"""ArtifactBundler for saha/v2 tasks (progress.yaml + model/*.c4).

Overrides only the two reading seams of :class:`ArtifactBundler` —
``_all_relevant_files`` (mtime cache signature) and ``_read_all`` — so
view policies, caching, and the size budget are inherited unchanged and
every call site keeps receiving the same :class:`TaskArtifacts` shape.

Mapping notes:
- A story's body is rendered markdown: the story sentence, its ACs with
  verify method / status / spec + test bindings, edge cases, and the
  story's ``us-xxx-flow`` dynamic-view source sliced from stories.c4.
- v2 has no code-changes artifacts; the contract surface lives in
  ``model/contracts.c4`` and ships via ``api_contracts``.
- Phase status ``pending`` maps to READY, NOT through ``normalize_status``
  (which aliases "pending" to DRAFT — and active-phase view policies SKIP
  DRAFT, which would hide the current phase's plan from the implementer).
"""

from __future__ import annotations

import logging
from pathlib import Path

from saha.models.likec4_source import extract_view_block, read_model_sources
from saha.models.progress import (
    AcceptanceCriterion,
    PhaseProgress,
    ProgressFile,
    ProgressFileError,
    StoryProgress,
    load_progress,
    progress_yaml_path,
)
from saha.orchestrator.artifact_bundler import (
    ArtifactBundler,
    LifecycleStatus,
    PlanPhase,
    Story,
    TestSpec,
    _RawCache,
    normalize_status,
)

logger = logging.getLogger(__name__)

_SPEC_TYPE_PREFIXES = {
    "ts-e2e": "e2e",
    "ts-int": "integration",
    "ts-unit": "unit",
}


class V2ArtifactBundler(ArtifactBundler):
    """Bundles a saha/v2 task folder into the legacy TaskArtifacts shape."""

    def _all_relevant_files(self) -> list[Path]:
        out = [progress_yaml_path(self.task_path)]
        model = self.task_path / "model"
        if model.is_dir():
            out.extend(model.glob("*.c4"))
        return [p for p in out if p.exists()]

    def _read_all(self) -> _RawCache:
        try:
            progress = load_progress(self.task_path)
        except ProgressFileError as exc:
            logger.error("v2 bundler cannot parse progress.yaml: %s", exc)
            return _RawCache()
        sources = read_model_sources(self.task_path)
        return _RawCache(
            task_description=_render_task_description(progress, sources),
            user_stories=[_to_story(s, sources) for s in progress.stories],
            test_specs=_collect_test_specs(progress, sources),
            code_changes=[],
            design_decisions=_source_dict(sources, "decisions.c4", "decisions"),
            api_contracts=_source_dict(sources, "contracts.c4", "contracts"),
            implementation_plan=[_to_plan_phase(p) for p in progress.phases],
        )


# ---------------------------------------------------------------------------
# Content mapping
# ---------------------------------------------------------------------------


def _render_task_description(progress: ProgressFile, sources: dict[str, str]) -> str:
    parts = [f"# {progress.title or progress.task}".strip()]
    task_source = sources.get("task.c4")
    if task_source:
        parts.append("## Task model (model/task.c4)\n\n```likec4\n" + task_source + "\n```")
    return "\n\n".join(parts)


def _to_story(story: StoryProgress, sources: dict[str, str]) -> Story:
    return Story(
        id=story.id,
        title=story.title,
        status=normalize_status(story.status),
        acceptance_criteria_count=len(story.acceptance_criteria),
        body=_render_story_body(story, sources),
    )


def _render_story_body(story: StoryProgress, sources: dict[str, str]) -> str:
    lines = [f"## {story.id} — {story.title}"]
    lines.append(f"Status: {story.status} | Priority: {story.priority}")
    if story.story:
        lines.append("")
        lines.append(story.story)
    if story.acceptance_criteria:
        lines.append("")
        lines.append("### Acceptance criteria")
        for ac in story.acceptance_criteria:
            lines.extend(_render_ac(ac))
    if story.edge_cases:
        lines.append("")
        lines.append("### Edge cases")
        lines.extend(
            f"- {ec.name}: {ec.trigger} → {ec.expected}".rstrip(": →")
            for ec in story.edge_cases
        )
    if story.depends_on:
        lines.append(f"\nDepends on: {', '.join(story.depends_on)}")
    if story.decisions:
        lines.append(f"Decisions: {', '.join(story.decisions)}")
    flow = _story_flow_block(story, sources)
    if flow:
        lines.append(f"\n### Flow view ({story.view})\n\n```likec4\n{flow}\n```")
    return "\n".join(lines)


def _render_ac(ac: AcceptanceCriterion) -> list[str]:
    lines = [f"- {ac.id} [{ac.verify}] ({ac.status}) — {ac.text}"]
    if ac.specs:
        lines.append(f"  - specs: {', '.join(ac.specs)}")
    if ac.tests:
        lines.append(f"  - tests: {', '.join(ac.tests)}")
    if ac.manual_instructions:
        lines.append(f"  - manual instructions: {ac.manual_instructions}")
    return lines


def _story_flow_block(story: StoryProgress, sources: dict[str, str]) -> str | None:
    if not story.view:
        return None
    for text in sources.values():
        block = extract_view_block(text, story.view)
        if block:
            return block
    return None


def _collect_test_specs(progress: ProgressFile, sources: dict[str, str]) -> list[TestSpec]:
    """One TestSpec per ts-* view referenced by any AC's planned specs."""
    referencing: dict[str, list[AcceptanceCriterion]] = {}
    for story in progress.stories:
        for ac in story.acceptance_criteria:
            for spec_id in ac.specs:
                referencing.setdefault(spec_id, []).append(ac)
    specs: list[TestSpec] = []
    for spec_id in sorted(referencing):
        block = _find_view_block(sources, spec_id)
        specs.append(
            TestSpec(
                name=spec_id,
                test_type=_spec_type(spec_id),
                status=_spec_status(referencing[spec_id]),
                body=block or f"[spec view {spec_id} not found in model/*.c4]",
            )
        )
    return specs


def _find_view_block(sources: dict[str, str], view_id: str) -> str | None:
    for text in sources.values():
        block = extract_view_block(text, view_id)
        if block:
            return block
    return None


def _spec_type(spec_id: str) -> str:
    for prefix, test_type in _SPEC_TYPE_PREFIXES.items():
        if spec_id.startswith(prefix):
            return test_type
    return "integration"


def _spec_status(acs: list[AcceptanceCriterion]) -> LifecycleStatus:
    if acs and all(ac.status == "done" for ac in acs):
        return LifecycleStatus.DONE
    return LifecycleStatus.READY


def _source_dict(sources: dict[str, str], filename: str, key: str) -> dict[str, str]:
    text = sources.get(filename)
    return {key: text} if text else {}


def _to_plan_phase(phase: PhaseProgress) -> PlanPhase:
    return PlanPhase(
        name=phase.id,
        status=_phase_status(phase.status),
        body=_render_phase_body(phase),
    )


def _phase_status(raw: str) -> LifecycleStatus:
    if raw == "pending":
        return LifecycleStatus.READY
    return normalize_status(raw)


def _render_phase_body(phase: PhaseProgress) -> str:
    lines = [f"## {phase.id} — {phase.title}", f"Status: {phase.status}"]
    if phase.stories:
        lines.append(f"Stories: {', '.join(phase.stories)}")
    if phase.steps:
        lines.append("")
        lines.append("### Steps")
        for step in phase.steps:
            files = f" (files: {', '.join(step.files)})" if step.files else ""
            lines.append(f"- [{step.status}] {step.name}{files}")
    return "\n".join(lines)
