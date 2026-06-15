"""Token usage statistics CLI command.

Provides visualizations of token consumption per task, per phase, per iteration
based on persisted execution state YAML files.
"""

import os
from collections import defaultdict
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from saha.config.settings import Settings
from saha.context import resolve_task_id
from saha.logging import SAHA_THEME
from saha.models.state import ExecutionState, IterationRecord, LoopPhase, PhaseTokenUsage
from saha.orchestrator.state import StateManager


def _build_console() -> Console:
    """Create a Console wide enough to render tables without ellipsizing names.

    The shared logging console auto-detects terminal width which collapses to
    80 columns under test capture and over-truncates task names. We render
    tables onto a dedicated console that respects a sane minimum width.
    """
    width_env = os.environ.get("COLUMNS")
    width: int | None = None
    if width_env:
        try:
            width = max(int(width_env), 100)
        except ValueError:
            width = None
    return Console(theme=SAHA_THEME, width=width or 160)


console = _build_console()

# Compact column ordering used in detailed tables
_TOKEN_COLUMNS: list[tuple[str, str]] = [
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_read_input_tokens", "cache rd"),
    ("cache_write_input_tokens", "cache wr"),
    ("reasoning_tokens", "reasoning"),
    ("total_tokens", "total"),
]


def _format_int(value: int) -> str:
    """Format an integer with thousands separators, blank for zero."""
    return f"{value:,}" if value else "—"


def _format_seconds(value: float) -> str:
    """Format a duration in seconds compactly."""
    if value <= 0:
        return "—"
    if value < 60:
        return f"{value:.1f}s"
    minutes, seconds = divmod(int(value), 60)
    return f"{minutes}m{seconds:02d}s"


def _sum_usage(records: list[PhaseTokenUsage]) -> dict[str, int]:
    """Sum token usage across a list of records."""
    totals: dict[str, int] = dict.fromkeys((k for k, _ in _TOKEN_COLUMNS), 0)
    for record in records:
        for key, _ in _TOKEN_COLUMNS:
            totals[key] += int(getattr(record, key, 0) or 0)
    return totals


def _iteration_records(state: ExecutionState) -> list[PhaseTokenUsage]:
    """Flatten all token usage records across iterations."""
    return [usage for iteration in state.iterations for usage in iteration.token_usage]


def _outcome_label(state: ExecutionState) -> str:
    """Build a single-token outcome descriptor for a task."""
    iters = state.current_iteration
    phase = state.current_phase
    if phase == LoopPhase.COMPLETED:
        return f"[success]✓ done @ {iters}[/success]"
    if phase == LoopPhase.FAILED:
        return f"[failure]✗ failed @ {iters}[/failure]"
    if phase == LoopPhase.STOPPED:
        return f"[warning]⏸ stopped @ {iters}[/warning]"
    if phase == LoopPhase.SCHEDULED:
        return "[info]⏰ scheduled[/info]"
    if phase == LoopPhase.IDLE:
        return "[dim]idle[/dim]"
    return f"[info]▶ {phase.value} @ {iters}[/info]"


def _iteration_flags(iteration: IterationRecord) -> str:
    """Compact pass/fail summary for a single iteration's gating phases."""
    parts: list[str] = []
    if iteration.test_critique_passed:
        parts.append("[success]Crit✓[/success]")
    elif iteration.token_usage and any(
        u.phase == LoopPhase.TEST_CRITIQUE for u in iteration.token_usage
    ):
        parts.append("[failure]Crit✗[/failure]")
    if iteration.dod_achieved:
        parts.append("[success]DoD✓[/success]")
    elif iteration.token_usage and any(u.phase == LoopPhase.QA for u in iteration.token_usage):
        parts.append("[failure]DoD✗[/failure]")
    if iteration.quality_passed:
        parts.append("[success]Qual✓[/success]")
    elif iteration.token_usage and any(
        u.phase == LoopPhase.CODE_QUALITY for u in iteration.token_usage
    ):
        parts.append("[failure]Qual✗[/failure]")
    return " ".join(parts) if parts else "—"


def _summarize_outcomes(states: list[ExecutionState]) -> str:
    """Aggregate completion stats across a list of states for the footer."""
    completed = [s for s in states if s.current_phase == LoopPhase.COMPLETED]
    failed = [s for s in states if s.current_phase == LoopPhase.FAILED]
    stopped = [s for s in states if s.current_phase == LoopPhase.STOPPED]
    running = [
        s
        for s in states
        if s.current_phase
        not in (LoopPhase.COMPLETED, LoopPhase.FAILED, LoopPhase.STOPPED, LoopPhase.SCHEDULED)
    ]

    parts: list[str] = []
    if completed:
        avg = sum(s.current_iteration for s in completed) / len(completed)
        parts.append(
            f"[success]{len(completed)} done[/success] (avg {avg:.1f} iters, "
            f"min {min(s.current_iteration for s in completed)}, "
            f"max {max(s.current_iteration for s in completed)})"
        )
    if running:
        parts.append(f"[info]{len(running)} running[/info]")
    if stopped:
        parts.append(f"[warning]{len(stopped)} stopped[/warning]")
    if failed:
        parts.append(f"[failure]{len(failed)} failed[/failure]")
    total_iters = sum(s.current_iteration for s in states)
    parts.append(f"[dim]{total_iters} total iterations burned[/dim]")
    return "  ".join(parts)


def _stats_command(task_id: str | None, by_phase: bool, all_tasks: bool) -> None:
    """Implementation of the stats command logic."""
    settings = Settings()
    state_manager = StateManager(settings.state_dir)

    if all_tasks:
        _show_all_tasks_aggregate(state_manager)
        return

    if task_id is None:
        _show_per_task_summary(state_manager)
        return

    state = state_manager.load(task_id)
    if state is None:
        typer.echo(f"No saved state for task: {task_id}", err=True)
        raise typer.Exit(1)

    if by_phase:
        _show_phase_breakdown(state)
    else:
        _show_iteration_breakdown(state)


def _show_per_task_summary(state_manager: StateManager) -> None:
    """List every task with its total token usage."""
    task_ids = state_manager.list_tasks()
    if not task_ids:
        typer.echo("No tasks with saved execution state.")
        return

    table = Table(title="Token usage per task", show_lines=False)
    table.add_column("Task", style="bold cyan")
    table.add_column("Outcome", style="white")
    table.add_column("Iters", justify="right", style="white")
    for _, label in _TOKEN_COLUMNS:
        table.add_column(label, justify="right", style="white")
    table.add_column("Duration", justify="right", style="dim")

    grand_totals = dict.fromkeys((k for k, _ in _TOKEN_COLUMNS), 0)
    grand_duration = 0.0
    states: list[ExecutionState] = []

    for tid in task_ids:
        state = state_manager.load(tid)
        if state is None:
            continue
        states.append(state)
        records = _iteration_records(state)
        totals = _sum_usage(records)
        duration = sum(r.duration_seconds for r in records)

        row = [
            tid,
            _outcome_label(state),
            f"{state.current_iteration}/{state.max_iterations}",
        ]
        row.extend(_format_int(totals[key]) for key, _ in _TOKEN_COLUMNS)
        row.append(_format_seconds(duration))
        table.add_row(*row)

        for key in grand_totals:
            grand_totals[key] += totals[key]
        grand_duration += duration

    if len(states) > 1:
        footer = ["TOTAL", "—", "—"]
        footer.extend(_format_int(grand_totals[key]) for key, _ in _TOKEN_COLUMNS)
        footer.append(_format_seconds(grand_duration))
        table.add_row(*footer, style="bold")

    console.print(table)
    if states:
        console.print(_summarize_outcomes(states))


def _show_all_tasks_aggregate(state_manager: StateManager) -> None:
    """Aggregate token usage across all tasks, grouped by phase."""
    task_ids = state_manager.list_tasks()
    if not task_ids:
        typer.echo("No tasks with saved execution state.")
        return

    by_phase: dict[LoopPhase, list[PhaseTokenUsage]] = defaultdict(list)
    for tid in task_ids:
        state = state_manager.load(tid)
        if state is None:
            continue
        for record in _iteration_records(state):
            by_phase[record.phase].append(record)

    if not by_phase:
        typer.echo("No token usage recorded yet.")
        return

    table = Table(title=f"Token usage aggregated across {len(task_ids)} task(s)")
    table.add_column("Phase", style="bold cyan")
    table.add_column("Calls", justify="right")
    for _, label in _TOKEN_COLUMNS:
        table.add_column(label, justify="right")
    table.add_column("Duration", justify="right", style="dim")

    grand_totals = dict.fromkeys((k for k, _ in _TOKEN_COLUMNS), 0)
    grand_duration = 0.0
    grand_calls = 0

    for phase in LoopPhase:
        records = by_phase.get(phase)
        if not records:
            continue
        totals = _sum_usage(records)
        duration = sum(r.duration_seconds for r in records)
        row = [phase.value, str(len(records))]
        row.extend(_format_int(totals[key]) for key, _ in _TOKEN_COLUMNS)
        row.append(_format_seconds(duration))
        table.add_row(*row)
        for key in grand_totals:
            grand_totals[key] += totals[key]
        grand_duration += duration
        grand_calls += len(records)

    footer = ["TOTAL", str(grand_calls)]
    footer.extend(_format_int(grand_totals[key]) for key, _ in _TOKEN_COLUMNS)
    footer.append(_format_seconds(grand_duration))
    table.add_row(*footer, style="bold")

    console.print(table)


def _show_iteration_breakdown(state: ExecutionState) -> None:
    """Show phase-by-phase rows for each iteration of a single task."""
    if not state.iterations:
        typer.echo(f"No iterations recorded yet for {state.task_id}.")
        return

    title = f"Token usage — {state.task_id}"
    table = Table(title=title, show_lines=False)
    table.add_column("Iter", justify="right", style="bold yellow")
    table.add_column("Phase", style="cyan")
    table.add_column("Agent", style="dim")
    for _, label in _TOKEN_COLUMNS:
        table.add_column(label, justify="right")
    table.add_column("Duration", justify="right", style="dim")
    table.add_column("Result", style="white")

    grand_totals = dict.fromkeys((k for k, _ in _TOKEN_COLUMNS), 0)
    grand_duration = 0.0

    for iteration in state.iterations:
        flags = _iteration_flags(iteration)

        if not iteration.token_usage:
            table.add_row(
                str(iteration.iteration),
                "(no agent calls)",
                "—",
                *(["—"] * len(_TOKEN_COLUMNS)),
                "—",
                flags,
            )
            continue

        for index, record in enumerate(iteration.token_usage):
            iter_label = str(iteration.iteration) if index == 0 else ""
            row = [iter_label, record.phase.value, record.agent_name]
            row.extend(_format_int(int(getattr(record, key, 0) or 0)) for key, _ in _TOKEN_COLUMNS)
            row.append(_format_seconds(record.duration_seconds))
            row.append("")
            table.add_row(*row)

        iter_totals = _sum_usage(iteration.token_usage)
        iter_duration = sum(r.duration_seconds for r in iteration.token_usage)
        subtotal_row = ["", f"iter {iteration.iteration} subtotal", "—"]
        subtotal_row.extend(_format_int(iter_totals[key]) for key, _ in _TOKEN_COLUMNS)
        subtotal_row.append(_format_seconds(iter_duration))
        subtotal_row.append(flags)
        table.add_row(*subtotal_row, style="bold dim")

        for key in grand_totals:
            grand_totals[key] += iter_totals[key]
        grand_duration += iter_duration

    table.add_section()
    footer = ["", "TASK TOTAL", "—"]
    footer.extend(_format_int(grand_totals[key]) for key, _ in _TOKEN_COLUMNS)
    footer.append(_format_seconds(grand_duration))
    footer.append(_outcome_label(state))
    table.add_row(*footer, style="bold green")

    console.print(table)
    console.print(_format_completion_summary(state))
    _print_task_meta(state)


def _format_completion_summary(state: ExecutionState) -> str:
    """One-line summary highlighting iterations consumed and outcome."""
    iters = state.current_iteration
    if state.current_phase == LoopPhase.COMPLETED:
        return f"[success]✓ Task completed after {iters} iteration(s).[/success]"
    if state.current_phase == LoopPhase.FAILED:
        return (
            f"[failure]✗ Task failed after {iters} iteration(s) "
            f"(burned without reaching DoD).[/failure]"
        )
    if state.current_phase == LoopPhase.STOPPED:
        return f"[warning]⏸ Task stopped after {iters} iteration(s).[/warning]"
    return f"[info]▶ Task in progress: {iters}/{state.max_iterations} iteration(s) used.[/info]"


def _show_phase_breakdown(state: ExecutionState) -> None:
    """Aggregate token usage per phase across iterations for a single task."""
    records = _iteration_records(state)
    if not records:
        typer.echo(f"No token usage recorded yet for {state.task_id}.")
        return

    grouped: dict[LoopPhase, list[PhaseTokenUsage]] = defaultdict(list)
    for record in records:
        grouped[record.phase].append(record)

    table = Table(title=f"Phase totals — {state.task_id}")
    table.add_column("Phase", style="bold cyan")
    table.add_column("Calls", justify="right")
    for _, label in _TOKEN_COLUMNS:
        table.add_column(label, justify="right")
    table.add_column("Duration", justify="right", style="dim")

    grand_totals = dict.fromkeys((k for k, _ in _TOKEN_COLUMNS), 0)
    grand_duration = 0.0
    grand_calls = 0

    for phase in LoopPhase:
        phase_records = grouped.get(phase)
        if not phase_records:
            continue
        totals = _sum_usage(phase_records)
        duration = sum(r.duration_seconds for r in phase_records)
        row = [phase.value, str(len(phase_records))]
        row.extend(_format_int(totals[key]) for key, _ in _TOKEN_COLUMNS)
        row.append(_format_seconds(duration))
        table.add_row(*row)
        for key in grand_totals:
            grand_totals[key] += totals[key]
        grand_duration += duration
        grand_calls += len(phase_records)

    footer = ["TOTAL", str(grand_calls)]
    footer.extend(_format_int(grand_totals[key]) for key, _ in _TOKEN_COLUMNS)
    footer.append(_format_seconds(grand_duration))
    table.add_row(*footer, style="bold green")

    console.print(table)
    _print_task_meta(state)


def _print_task_meta(state: ExecutionState) -> None:
    """Print a small footer with task metadata."""
    parts = [
        f"phase={state.current_phase.value}",
        f"iterations={state.current_iteration}/{state.max_iterations}",
    ]
    if state.started_at:
        parts.append(f"started={state.started_at.isoformat(timespec='seconds')}")
    if state.completed_at:
        parts.append(f"completed={state.completed_at.isoformat(timespec='seconds')}")
    console.print(f"[dim]{'  '.join(parts)}[/dim]")


def _complete_task_id(incomplete: str) -> list[str]:
    """Provide autocompletion for task IDs."""
    try:
        settings = Settings()
        state_manager = StateManager(settings.state_dir)
        ids = state_manager.list_tasks()
    except Exception:
        return []
    if incomplete:
        return [t for t in ids if t.startswith(incomplete)]
    return ids


def register_stats_command(app: typer.Typer) -> None:
    """Register the stats command on the Typer app."""

    @app.command()
    def stats(
        task_id: Annotated[
            str | None,
            typer.Argument(
                help="Task ID to show stats for. Uses current task if omitted; lists all tasks when no current task is set.",
                autocompletion=_complete_task_id,
            ),
        ] = None,
        by_phase: Annotated[
            bool,
            typer.Option(
                "--by-phase",
                help="Aggregate the chosen task's usage per phase instead of per iteration.",
            ),
        ] = False,
        all_tasks: Annotated[
            bool,
            typer.Option(
                "--all",
                help="Aggregate token usage across every task with saved state.",
            ),
        ] = False,
    ) -> None:
        """Show token usage statistics from persisted execution state.

        Without arguments, shows a per-task summary table.
        With a task ID, shows iteration × phase breakdown for that task.
        With --by-phase, aggregates per phase for the chosen task.
        With --all, aggregates per phase across every saved task.
        """
        if all_tasks:
            _stats_command(None, by_phase=False, all_tasks=True)
            return

        if task_id is None:
            try:
                resolved = resolve_task_id(None)
            except ValueError:
                resolved = None
            _stats_command(resolved, by_phase=by_phase, all_tasks=False)
            return

        _stats_command(task_id, by_phase=by_phase, all_tasks=False)


# Re-export for tests
__all__ = [
    "register_stats_command",
    "_stats_command",
    "_iteration_records",
    "_sum_usage",
    "IterationRecord",
]
