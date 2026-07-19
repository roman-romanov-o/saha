"""Execution loop CLI commands.

This module contains commands for running and managing the agentic execution loop:
- run: Execute a task through the agentic loop
- resume: Resume a previously interrupted task
- status: Show execution status for tasks
- use: Set/show/clear the current task context
- tools: List available code quality tools
- clean: Remove execution state files
- version: Show version information
"""

import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from saha import __version__
from saha.commands.common import setup_logging
from saha.commands.plugin import sync_claude_artifacts
from saha.config.settings import Settings
from saha.context import clear_current_task, get_current_task, resolve_task_id, set_current_task
from saha.models.progress import is_v2_task
from saha.models.state import ExecutionState, LoopPhase
from saha.orchestrator.factory import create_orchestrator
from saha.orchestrator.loop import LoopConfig
from saha.orchestrator.state import StateManager
from saha.orchestrator.v2_progress import set_task_status
from saha.tools.registry import create_default_registry
from saha.verification import (
    TaskVerifier,
    V2TaskVerifier,
    VerificationResult,
    VerificationStatus,
    cleanup_template_artifacts,
)

# Constants
DEFAULT_MAX_ITERATIONS = 5


def _parse_duration(duration_str: str) -> int:
    """Parse duration string (e.g., 1h, 30m, 10s) to seconds."""
    if not duration_str:
        return 0

    try:
        if duration_str.endswith("h"):
            return int(duration_str[:-1]) * 3600
        if duration_str.endswith("m"):
            return int(duration_str[:-1]) * 60
        if duration_str.endswith("s"):
            return int(duration_str[:-1])
        return int(duration_str)
    except ValueError:
        raise ValueError(
            f"Invalid duration format: {duration_str}. Use e.g. 1h, 30m, 10s."
        ) from None


def _calculate_scheduled_at(delay: str | None, at: str | None) -> datetime:
    """Calculate the scheduled time based on delay or at string."""
    now = datetime.now()

    if delay:
        seconds = _parse_duration(delay)
        return now + timedelta(seconds=seconds)

    if at:
        try:
            # Try parsing HH:MM
            t = time.strptime(at, "%H:%M")
            scheduled = now.replace(hour=t.tm_hour, minute=t.tm_min, second=0, microsecond=0)
            if scheduled < now:
                # If time is in the past, assume tomorrow
                scheduled += timedelta(days=1)
            return scheduled
        except ValueError:
            raise ValueError(f"Invalid time format: {at}. Use HH:MM format (e.g. 14:00).") from None

    return now


# -----------------------------------------------------------------------------
# Autocompletion helpers
# -----------------------------------------------------------------------------


def _complete_task_id(incomplete: str) -> list[str]:
    """Provide autocompletion for task IDs by scanning the tasks directory."""
    try:
        settings = Settings()
        task_base = settings.task_base_path
        if not task_base.exists():
            return []

        # List all directories in the task base path
        task_dirs = [d.name for d in task_base.iterdir() if d.is_dir()]

        # Filter by incomplete input
        if incomplete:
            return [t for t in task_dirs if t.startswith(incomplete)]
        return task_dirs
    except Exception:
        return []


# -----------------------------------------------------------------------------
# Command implementations (extracted for testability and reduced complexity)
# -----------------------------------------------------------------------------


def _schedule_execution(
    task_id: str,
    task_path: Path,
    delay: str | None,
    at: str | None,
    max_iterations: int,
    tools: str | None,
    playwright: bool,
    settings: Settings,
) -> None:
    """Schedule execution for later."""
    scheduled_at = _calculate_scheduled_at(delay, at)

    state_manager = StateManager(settings.state_dir)
    enabled_tools = tools.split(",") if tools else None

    # Create state and mark as scheduled
    state = state_manager.create(
        task_id=task_id,
        task_path=task_path,
        max_iterations=max_iterations,
        enabled_tools=enabled_tools,
    )
    state.current_phase = LoopPhase.SCHEDULED
    state.scheduled_at = scheduled_at
    state_manager.save(state)

    typer.echo(f"Task {task_id} scheduled for {scheduled_at.strftime('%Y-%m-%d %H:%M:%S')}")
    typer.echo(f"Output will be logged to .saha_logs/saha-scheduled-{task_id}.log")

    _spawn_background_execution(task_id, scheduled_at)


def _spawn_background_execution(task_id: str, scheduled_at: datetime) -> None:
    """Spawn a background process to run the task at scheduled time."""
    delay_seconds = (scheduled_at - datetime.now()).total_seconds()
    if delay_seconds < 0:
        delay_seconds = 0

    python_exe = sys.executable
    saha_cmd = ["saha", "resume", task_id]

    # Try to find the full path to 'saha' script in the same venv
    saha_bin = Path(python_exe).parent / "saha"
    if saha_bin.exists():
        saha_cmd[0] = str(saha_bin)

    # Log file for background execution
    log_dir = Path(".saha_logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"saha-scheduled-{task_id}.log"

    # Command that sleeps and then runs saha resume
    cmd_parts = [
        python_exe,
        "-c",
        f"import time, subprocess, os; "
        f"time.sleep({delay_seconds}); "
        f"log = open({str(log_file)!r}, 'a'); "
        f"log.write('\\n--- Starting scheduled execution at ' + time.ctime() + ' ---\\n'); "
        f"log.flush(); "
        f"subprocess.run({saha_cmd!r}, stdout=log, stderr=log, check=False)",
    ]

    # Start detached background process
    try:
        if sys.platform == "win32":
            # Windows detached process
            DETACHED_PROCESS = 0x00000008
            subprocess.Popen(
                cmd_parts,
                creationflags=DETACHED_PROCESS,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Unix detached process
            subprocess.Popen(
                cmd_parts,
                start_new_session=True,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        typer.echo(f"Warning: Failed to spawn background process: {e}", err=True)
        typer.echo("Task remains scheduled but background process may not run.")


def _run_command(
    task_id: str,
    task_path: Path | None,
    max_iterations: int,
    tools: str | None,
    playwright: bool,
    qa_runner: str | None,
    default_runner: str | None,
    dangerously_skip_permissions: bool,
    dry_run: bool,
    verbose: bool,
    skip_verify: bool,
    delay: str | None = None,
    at: str | None = None,
) -> None:
    """Implementation of the run command logic."""
    setup_logging(verbose)

    # Sync Claude artifacts (agents, etc.) before execution
    sync_result = sync_claude_artifacts()
    if sync_result.total_synced > 0:
        typer.echo(
            f"Synced {sync_result.total_synced} agent artifact(s): "
            f"{', '.join(sync_result.agents_synced)}"
        )

    settings = _build_run_settings(
        verbose, dry_run, qa_runner, default_runner, dangerously_skip_permissions
    )
    resolved_path = _resolve_and_validate_task_path(task_id, task_path, settings)

    if delay or at:
        if dry_run:
            scheduled_at = _calculate_scheduled_at(delay, at)
            typer.echo(
                f"[DRY RUN] Would schedule task {task_id} for {scheduled_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return
        _schedule_execution(
            task_id, resolved_path, delay, at, max_iterations, tools, playwright, settings
        )
        return

    enabled_tools = tools.split(",") if tools else None

    # Run verification unless explicitly skipped or dry-run
    if not skip_verify and not dry_run:
        verification_result = _run_verification(task_id, resolved_path)
        if verification_result.errors:
            typer.echo(
                "\nVerification failed. Fix errors and re-run, or use --skip-verify to bypass.",
                err=True,
            )
            raise typer.Exit(1)

        if verification_result.warnings:
            try:
                proceed = typer.confirm(
                    "\nVerification has warnings. Improve planning artifacts and re-run. "
                    "Proceed anyway?",
                    default=False,
                    abort=False,
                )
            except (EOFError, typer.Abort):
                proceed = False

            if not proceed:
                typer.echo("\nAborting due to verification warnings.", err=True)
                raise typer.Exit(1)
            typer.echo("Proceeding despite verification warnings.")

        # Clean up unfilled template artifacts after successful verification
        cleanup_result = cleanup_template_artifacts(resolved_path)
        if cleanup_result.total_removed > 0:
            typer.echo(f"Cleaned up {cleanup_result.total_removed} unfilled template file(s)")

    _display_run_info(task_id, resolved_path, max_iterations)

    if dry_run:
        typer.echo("[DRY RUN] Would execute the loop without making changes.")
        return

    orchestrator = create_orchestrator(settings)
    config = LoopConfig(
        task_id=task_id,
        task_path=resolved_path,
        max_iterations=max_iterations,
        enabled_tools=enabled_tools,
        playwright_enabled=playwright,
    )

    if is_v2_task(resolved_path):
        set_task_status(resolved_path, "executing")

    state = orchestrator.run(config)
    _sync_v2_task_status(resolved_path, state)
    _display_run_result(state)


# Terminal loop phases → the v2 progress.yaml top-level status they map to.
# STOPPED is deliberately absent: a stopped task stays "executing" so it reads
# as resumable rather than finished.
_V2_FINAL_STATUS = {
    LoopPhase.COMPLETED: "completed",
    LoopPhase.COMPLETED_PENDING_MANUAL: "completed_pending_manual",
    LoopPhase.FAILED: "failed",
}


def _sync_v2_task_status(task_path: Path, state: ExecutionState) -> None:
    """Mirror the loop's terminal phase into a v2 task's progress.yaml status line."""
    if not is_v2_task(task_path):
        return
    new_status = _V2_FINAL_STATUS.get(state.current_phase)
    if new_status and set_task_status(task_path, new_status):
        typer.echo(f"progress.yaml status → {new_status}")


def _build_run_settings(
    verbose: bool,
    dry_run: bool,
    qa_runner: str | None,
    default_runner: str | None,
    dangerously_skip_permissions: bool,
) -> Settings:
    """Build settings with CLI overrides."""
    settings = Settings(dry_run=dry_run, verbose=verbose)

    if default_runner:
        agent_updates = {
            "implementer": settings.agents.implementer.model_copy(
                update={"runner": default_runner}
            ),
            "qa": settings.agents.qa.model_copy(update={"runner": default_runner}),
            "code_quality": settings.agents.code_quality.model_copy(
                update={"runner": default_runner}
            ),
            "manager": settings.agents.manager.model_copy(update={"runner": default_runner}),
            "dod": settings.agents.dod.model_copy(update={"runner": default_runner}),
        }
        updated_agents = settings.agents.model_copy(
            update={"default_runner": default_runner, **agent_updates}
        )
        settings = settings.model_copy(update={"agents": updated_agents, "runner": default_runner})

    if qa_runner:
        updated_qa = settings.agents.qa.model_copy(update={"runner": qa_runner})
        updated_agents = settings.agents.model_copy(update={"qa": updated_qa})
        settings = settings.model_copy(update={"agents": updated_agents})

    if dangerously_skip_permissions:
        settings = settings.model_copy(
            update={
                "claude_dangerously_skip_permissions": True,
                "codex_dangerously_bypass_sandbox": True,
            }
        )

    return settings


def _resolve_and_validate_task_path(
    task_id: str, task_path: Path | None, settings: Settings
) -> Path:
    """Resolve and validate the task path."""
    resolved_path = task_path or settings.get_task_path(task_id)
    if not resolved_path.exists():
        typer.echo(f"Error: Task path does not exist: {resolved_path}", err=True)
        raise typer.Exit(1)
    return resolved_path


def _display_run_info(task_id: str, task_path: Path, max_iterations: int) -> None:
    """Display run command startup information."""
    typer.echo(
        typer.style(
            "Note: `saha run` shells out to `claude -p` and is billed against API credits, "
            "not your Claude subscription.",
            fg=typer.colors.YELLOW,
        )
    )
    typer.echo(
        typer.style(
            "  For subscription-billed execution, run `/saha:execute` inside Claude Code instead.",
            fg=typer.colors.YELLOW,
        )
    )
    typer.echo(f"Starting agentic loop for task: {task_id}")
    typer.echo(f"Task path: {task_path}")
    typer.echo(f"Max iterations: {max_iterations}")


def _display_run_result(state: ExecutionState) -> None:
    """Display run command result."""
    typer.echo(f"\nLoop finished. Final phase: {state.current_phase.value}")
    typer.echo(f"Iterations completed: {state.current_iteration}")
    if state.current_phase == LoopPhase.STOPPED and state.error_message:
        typer.echo(f"Reason: {state.error_message}")


def _run_verification(task_id: str, task_path: Path) -> VerificationResult:
    """Run verification checks and display results."""
    typer.echo("Verifying task artifacts...")

    verifier_cls = V2TaskVerifier if is_v2_task(task_path) else TaskVerifier
    verifier = verifier_cls(task_path)
    result = verifier.verify(task_id)

    _display_verification_result(result)
    return result


def _display_verification_result(result: VerificationResult) -> None:
    """Display verification result with colored status indicators."""
    status_symbols = {
        VerificationStatus.PASSED: typer.style("PASSED", fg=typer.colors.GREEN, bold=True),
        VerificationStatus.WARNINGS: typer.style("WARNINGS", fg=typer.colors.YELLOW, bold=True),
        VerificationStatus.FAILED: typer.style("FAILED", fg=typer.colors.RED, bold=True),
    }

    typer.echo(f"\nVerification: {status_symbols[result.status]}")
    typer.echo("")

    for check in result.checks:
        if check.passed:
            symbol = typer.style("[ok]", fg=typer.colors.GREEN)
        elif check.is_warning:
            symbol = typer.style("[warn]", fg=typer.colors.YELLOW)
        else:
            symbol = typer.style("[fail]", fg=typer.colors.RED)
        typer.echo(f"  {symbol} {check.name}: {check.message}")

    typer.echo("")


def _resume_command(task_id: str, verbose: bool) -> None:
    """Implementation of the resume command logic."""
    setup_logging(verbose)

    settings = Settings(verbose=verbose)
    orchestrator = create_orchestrator(settings)

    typer.echo(
        typer.style(
            "Note: `saha resume` uses `claude -p` (API-billed). "
            "For subscription billing, use `/saha:resume` inside Claude Code.",
            fg=typer.colors.YELLOW,
        )
    )
    typer.echo(f"Resuming task: {task_id}")

    try:
        state = orchestrator.resume(task_id)
        _sync_v2_task_status(state.task_path, state)
        typer.echo(f"\nLoop finished. Final phase: {state.current_phase.value}")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


def _status_command(task_id: str | None, verbose: bool) -> None:
    """Implementation of the status command logic."""
    settings = Settings()
    state_manager = StateManager(settings.state_dir)

    if task_id:
        _show_single_task_status(state_manager, task_id, verbose)
    else:
        _list_all_task_statuses(state_manager)


def _show_single_task_status(state_manager: StateManager, task_id: str, verbose: bool) -> None:
    """Display status for a single task."""
    state = state_manager.load(task_id)
    if state is None:
        typer.echo(f"No saved state for task: {task_id}")
        raise typer.Exit(1)

    typer.echo(f"Task: {state.task_id}")
    typer.echo(f"Phase: {state.current_phase.value}")
    typer.echo(f"Iteration: {state.current_iteration}/{state.max_iterations}")
    if state.scheduled_at:
        typer.echo(f"Scheduled: {state.scheduled_at}")
    typer.echo(f"Started: {state.started_at}")
    typer.echo(f"Completed: {state.completed_at or 'N/A'}")
    if state.error_message and state.current_phase in (LoopPhase.FAILED, LoopPhase.STOPPED):
        typer.echo(f"Reason: {state.error_message}")

    if verbose and state.iterations:
        typer.echo("\nIterations:")
        for it in state.iterations:
            typer.echo(f"  {it.iteration}: DoD={it.dod_achieved}, Quality={it.quality_passed}")


def _list_all_task_statuses(state_manager: StateManager) -> None:
    """List status of all tasks with saved state."""
    task_ids = state_manager.list_tasks()
    if not task_ids:
        typer.echo("No tasks with saved execution state.")
        return

    typer.echo("Tasks with saved state:")
    for tid in task_ids:
        state = state_manager.load(tid)
        if state:
            status_line = f"  {tid}: {state.current_phase.value}"
            if state.current_phase == LoopPhase.SCHEDULED and state.scheduled_at:
                status_line += f" at {state.scheduled_at.strftime('%Y-%m-%d %H:%M:%S')}"
            status_line += f" (iter {state.current_iteration})"
            typer.echo(status_line)


def _tools_command() -> None:
    """Implementation of the tools command logic."""
    registry = create_default_registry()

    typer.echo("Registered tools:")
    for name in registry.list_all():
        tool = registry.get(name)
        if tool:
            available = "✓" if tool.is_available() else "✗"
            typer.echo(f"  {available} {name}")


def _clean_command(task_id: str | None, all_tasks: bool) -> None:
    """Implementation of the clean command logic."""
    settings = Settings()
    state_manager = StateManager(settings.state_dir)

    if all_tasks:
        _clean_all_tasks(state_manager)
    elif task_id:
        _clean_single_task(state_manager, task_id)
    else:
        typer.echo("Specify a task ID or use --all to clean all states.")


def _clean_all_tasks(state_manager: StateManager) -> None:
    """Clean all task states."""
    task_ids = state_manager.list_tasks()
    for tid in task_ids:
        state_manager.delete(tid)
        typer.echo(f"Cleaned: {tid}")
    typer.echo(f"Cleaned {len(task_ids)} task(s).")


def _clean_single_task(state_manager: StateManager, task_id: str) -> None:
    """Clean a single task state."""
    if state_manager.delete(task_id):
        typer.echo(f"Cleaned state for: {task_id}")
    else:
        typer.echo(f"No state found for: {task_id}")


def _version_command() -> None:
    """Implementation of the version command logic."""
    typer.echo(f"saha version {__version__}")


def _use_command(task_id: str | None, clear: bool) -> None:
    """Implementation of the use command logic."""
    if clear:
        if clear_current_task():
            typer.echo("Cleared current task context.")
        else:
            typer.echo("No current task was set.")
        return

    if task_id is None:
        current = get_current_task()
        if current:
            typer.echo(f"Current task: {current}")
        else:
            typer.echo("No current task set. Use 'saha use <task-id>' to set one.")
        return

    try:
        set_current_task(task_id)
        typer.echo(f"Current task set to: {task_id}")
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


# -----------------------------------------------------------------------------
# Command registration (minimal - just wires CLI to implementations)
# -----------------------------------------------------------------------------


def register_execution_commands(app: typer.Typer) -> None:
    """Register all execution-related commands with the Typer app."""

    @app.command()
    def run(
        task_id: Annotated[
            str | None,
            typer.Argument(
                help="Task ID to execute (e.g., task-01). Uses current task if omitted.",
                autocompletion=_complete_task_id,
            ),
        ] = None,
        task_path: Annotated[
            Path | None,
            typer.Option(
                "--path", "-p", help="Path to task folder (default: docs/tasks/<task_id>)"
            ),
        ] = None,
        max_iterations: Annotated[
            int,
            typer.Option("--max-iter", "-m", help="Maximum loop iterations"),
        ] = DEFAULT_MAX_ITERATIONS,
        tools: Annotated[
            str | None,
            typer.Option("--tools", "-t", help="Comma-separated list of tools to enable"),
        ] = None,
        playwright: Annotated[
            bool,
            typer.Option(
                "--playwright",
                help="Enable Playwright for UI verification (uses execution-qa-playwright agent)",
            ),
        ] = False,
        qa_runner: Annotated[
            str | None,
            typer.Option("--qa-runner", help="Runner for QA agent: claude, codex, gemini, or mock"),
        ] = None,
        default_runner: Annotated[
            str | None,
            typer.Option(
                "--runner",
                help="Default runner for execution agents: claude, codex, gemini, or mock",
            ),
        ] = None,
        dangerously_skip_permissions: Annotated[
            bool,
            typer.Option(
                "--dangerously-skip-permissions",
                help="Disable execution confirmations for Claude/Codex (unsafe)",
            ),
        ] = False,
        dry_run: Annotated[
            bool,
            typer.Option("--dry-run", help="Simulate execution without actually running"),
        ] = False,
        verbose: Annotated[
            bool,
            typer.Option("--verbose", "-v", help="Enable verbose output"),
        ] = False,
        skip_verify: Annotated[
            bool,
            typer.Option("--skip-verify", help="Skip artifact verification checks"),
        ] = False,
        delay: Annotated[
            str | None,
            typer.Option("--delay", help="Delay execution (e.g., 1h, 30m, 10s)"),
        ] = None,
        at: Annotated[
            str | None,
            typer.Option("--at", help="Schedule execution at specific time (e.g., 14:00)"),
        ] = None,
    ) -> None:
        """Run the agentic loop for a task.

        If no task ID is provided, uses the current task set via 'saha use'.

        Before execution, verifies that task artifacts are complete:
        - task-description.md exists
        - At least 1 user story
        - At least 1 test spec
        - At least 1 implementation phase

        Warnings will block execution by default. You can confirm at the prompt to proceed.
        Use --skip-verify to bypass verification entirely and run anyway.

        The loop executes these phases in order:
        1. Implementation - writes code changes
        2. QA - verifies against Definition of Done (optionally with Playwright)
        3. Code Quality - runs ruff, ty, complexity checks
        4. Manager - updates task artifacts
        5. DoD Check - determines if task is complete

        Different agents can use different LLM backends. Use --runner to switch
        all execution agents (e.g., Codex), or --qa-runner for per-agent overrides.
        """
        try:
            resolved_id = resolve_task_id(task_id)
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        _run_command(
            resolved_id,
            task_path,
            max_iterations,
            tools,
            playwright,
            qa_runner,
            default_runner,
            dangerously_skip_permissions,
            dry_run,
            verbose,
            skip_verify,
            delay,
            at,
        )

    @app.command()
    def resume(
        task_id: Annotated[
            str | None,
            typer.Argument(
                help="Task ID to resume. Uses current task if omitted.",
                autocompletion=_complete_task_id,
            ),
        ] = None,
        verbose: Annotated[
            bool,
            typer.Option("--verbose", "-v", help="Enable verbose output"),
        ] = False,
    ) -> None:
        """Resume a previously started task.

        If no task ID is provided, uses the current task set via 'saha use'.
        """
        try:
            resolved_id = resolve_task_id(task_id)
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        _resume_command(resolved_id, verbose)

    @app.command()
    def status(
        task_id: Annotated[
            str | None,
            typer.Argument(help="Task ID to check", autocompletion=_complete_task_id),
        ] = None,
        verbose: Annotated[
            bool,
            typer.Option("--verbose", "-v", help="Show detailed status"),
        ] = False,
    ) -> None:
        """Show execution status for tasks."""
        _status_command(task_id, verbose)

    @app.command()
    def tools() -> None:
        """List available tools."""
        _tools_command()

    @app.command()
    def clean(
        task_id: Annotated[
            str | None,
            typer.Argument(help="Task ID to clean", autocompletion=_complete_task_id),
        ] = None,
        all_tasks: Annotated[
            bool,
            typer.Option("--all", help="Clean all task states"),
        ] = False,
    ) -> None:
        """Clean execution state files."""
        _clean_command(task_id, all_tasks)

    @app.command()
    def use(
        task_id: Annotated[
            str | None,
            typer.Argument(
                help="Task ID to set as current (e.g., task-01)", autocompletion=_complete_task_id
            ),
        ] = None,
        clear: Annotated[
            bool,
            typer.Option("--clear", help="Clear the current task context"),
        ] = False,
    ) -> None:
        """Set, show, or clear the current task context.

        With no arguments, shows the current task.
        With a task ID, sets it as the active task.
        With --clear, removes the current task context.
        """
        _use_command(task_id, clear)

    @app.command()
    def version() -> None:
        """Show version information."""
        _version_command()
